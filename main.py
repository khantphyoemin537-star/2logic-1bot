#############
import io
import csv
import ast
import operator
import asyncio
import logging
import random
import os
import threading
import re
import time
import unicodedata
import urllib.request
import json
import pytz
from PIL import Image, ImageDraw, ImageFont
from collections import Counter, defaultdict
from telethon.tl.functions.channels import GetParticipantsRequest
from datetime import datetime, timedelta
from telethon.tl.functions.messages import ExportChatInviteRequest
from flask import Flask
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument, UpdateOne
from pymongo.errors import DuplicateKeyError
from html import escape as escape_html
from telethon.errors import FloodWaitError
from telethon import TelegramClient, events, functions, types, Button, errors
from telethon.tl.types import ChannelParticipantsAdmins
from telethon.extensions import html
from telethon.tl.types import MessageEntityCustomEmoji
import redis.asyncio as redis

# ==========================================
# 🎨 COLORED INLINE BUTTONS — Bot API 9.4 (Feb 2026) added a `style` field to keyboard buttons,
# letting a button carry a background color instead of the permanent transparent look every
# button had before: bg_success (green), bg_danger (red), bg_primary (blue), or none (default
# transparent — still the recommended look for neutral actions, per Telegram's own docs). The
# style flag lives on every keyboard button type (callback, switch_inline, url, ...), so both
# helpers below share the same _apply_button_style() logic.
#
# Both build the button through the exact same Button.inline()/Button.switch_inline() everything
# else in this file already uses (identical bytes-encoding/validation), then ADD a .style
# attribute — wrapped in try/except so that on any Telethon version that doesn't know about
# types.KeyboardButtonStyle yet, this silently no-ops back to a plain transparent button instead
# of crashing every send/edit call that uses it.
# ==========================================
def _apply_button_style(btn, color):
    if color:
        try:
            btn.style = types.KeyboardButtonStyle(
                bg_success=True if color == "success" else None,
                bg_danger=True if color == "danger" else None,
                bg_primary=True if color == "primary" else None,
            )
        except Exception:
            pass
    return btn

def colored_button(text, data, color=None):
    """color: 'success' (green — the affirmative/beneficial side of a decision, e.g. accepting
    a gift or claiming a reward), 'danger' (red — the destructive/costly side, e.g. /scrap or
    /divorce confirms), 'primary' (blue — a main action), or None (unchanged default
    transparent). Only style the one button in a row that should stand out; leave its
    cancel/decline/skip pair as a plain Button.inline() — that's the neutral "do nothing"
    option, and transparent is exactly what Telegram recommends for that."""
    return _apply_button_style(Button.inline(text, data=data), color)

def colored_switch_inline_button(text, query, same_peer=True, color=None):
    """Same as colored_button(), but for Button.switch_inline() — the "opens an inline query"
    buttons (e.g. /harem's Characters🌎 gallery button) — since that's a different underlying
    button type (KeyboardButtonSwitchInline, not KeyboardButtonCallback) with its own
    query/same_peer args instead of data."""
    return _apply_button_style(Button.switch_inline(text, query=query, same_peer=same_peer), color)

# ==========================================
# 🗃️ BOT STATE — every in-memory runtime cache/counter/game-state dict the bot keeps, in one
# place, instead of ~28 loose module-level globals scattered across the file. Gives us:
#   1. cleanup_expired() — a single periodic sweep that bounds memory on a 512MB Render
#      instance instead of relying on each cache to (maybe) self-prune on its own.
#   2. One obvious place to look when auditing "what does this process hold in RAM".
#
# BACKWARD COMPAT: right after this class, every attribute is ALSO exposed as a bare
# module-level name (e.g. `active_group_spawns = bot_state.active_group_spawns`). Dicts/sets/
# defaultdicts are mutable reference types in Python, so that bare name and the attribute on
# bot_state point at the literal same object — `active_group_spawns[x] = y` anywhere else in
# this file mutates bot_state.active_group_spawns too, and vice versa. This means every one of
# the hundreds of pre-existing references to these globals throughout the file keeps working
# completely unchanged; nothing needed to be renamed at the call sites.
# ==========================================
class BotState:
    def __init__(self):
        # ---- Simple rate-limit / anti-spam / UI-debounce caches: {key: last_action_ts} ----
        self._recent_callback_taps = {}
        self._last_callback_click = {}
        self._recent_event_ids = {}
        self._recent_bot_added_chats = {}
        self.recent_mod_actions = {}          # (chat_id, user_id) -> ts
        self.user_cooldowns = {}              # composite cache_key -> ts
        self.user_mute_until = {}             # user_id -> expiry ts (GLOBAL — applies in every group)
        self.force_sub_prompt_last_sent = {}  # user_id -> ts

        # ---- Caches with an explicit (payload, expiry_ts) or {"expiry": ts} shape ----
        self.force_sub_membership_cache = {}  # user_id -> (is_member, expiry_ts)
        self._welcome_toggle_cache = {}       # chat_id -> (enabled, ts)
        self._spawn_target_cache = {}         # chat_id -> (spawn_target, ts)
        self.admin_cache = {}                 # chat_id -> {"ids": [...], "expiry": ts}
        self.pending_sell_offers = {}         # sell_id -> {"expiry": ts, "seller_id", "char_id", "char_name", "offer"} — /sell Owner-buyback confirm flow
        self.pending_premium_gifts = {}       # gift_id -> {"expiry": ts, "user_id", "star_amount"} — Premium daily Star gift confirm flow
        # ⚡ PERFORMANCE: shared "already checked today" gate for the Premium daily Star gift
        # (see _check_daily_first_message_rewards) — {user_id: date_str}. Set the INSTANT a
        # user's first qualifying message of the day is seen, with no await in between
        # check-and-set (same atomicity trick as spawn claiming above), so a burst of that
        # user's messages across many groups in the same
        # moment only ever triggers ONE combined DB read for the whole day, not one per
        # message. This is purely a performance short-circuit — the actual once-per-day
        # guarantee still comes from premium_gift_date in Mongo (atomic
        # find_one_and_update), so a bot restart mid-day just costs one extra DB read per user
        # on their next message, never a double-grant.
        self.daily_first_msg_gate = {}

        # ---- Per-user rolling-window trackers — already self-pruned inline on every write
        # (see check_sticker_spam/check_char_spam/spam checks), so cleanup_expired() leaves
        # these alone rather than duplicating that per-key pruning logic here. ----
        self.sticker_spam_data = {}
        self.char_spam_data = {}
        self.user_spam_data = {}

        # ---- Group message-interval counters — one small int per active group, reset by the
        # spawn/talk logic itself when it fires, no expiry concept to sweep. ----
        self.reply_msg_counters = {}
        self.random_talk_counters = {}
        # ⚡ PERFORMANCE: the spawn-progress counter for every group used to live purely in
        # Mongo, hit with a find_one_and_update on every single group message — the single
        # biggest source of database load in the whole bot. It now lives here in memory
        # (source of truth for spawn decisions) and is only durably written to Mongo in a
        # cheap periodic batch — see group_counter_flush_loop(). {chat_id: int}
        self.group_spawn_counters = {}
        # 🧠 Trivia auto-spawn — fully independent message counter from group_spawn_counters
        # above (own cadence, own trigger). {chat_id: int} counters, {chat_id: int} targets
        # (a fresh random threshold rolled each time a trivia fires, see get_trivia_target()).
        # No expiry concept to sweep, same as group_spawn_counters.
        self.trivia_spawn_counters = {}
        self.trivia_spawn_targets = {}

        # ---- Small admin/moderation-flow trackers, bounded by concurrent admin activity ----
        self.pending_editchar_prompt_ids = {}
        self.dark_passenger_targets = {}
        # 🎗️ /addle Limited Edition tagging wizard: prompt_msg_id -> {"stage": "name"/"details",
        # "char_ids": [..] or None (None = not yet given, asked for in the "details" reply)}.
        # Same reply-to-bot-message shape as pending_editchar_prompt_ids above.
        self.pending_addle_prompt = {}
        # 🏷️ /renamele reply flow (per owner request — fixing pre-existing LE names that
        # predate the emoji requirement) — sent_prompt_msg_id -> {"batch_number"}.
        self.pending_renamele_prompt = {}
        # 🎰 NEW (per owner request — "banner/caption keeps popping up like spam"): tracks the
        # LAST result message per (user_id, chat_id) for slot/basketball/dice, so a repeat spin
        # EDITS that same message instead of sending a brand new one every time. Only the native
        # dice animation itself is ever a fresh send (Telegram dice messages can't be edited) —
        # the banner+caption result card underneath it now reuses one message per player per
        # chat. {(user_id, chat_id): {"msg_id", "via", "game_key", "ts"}} — "via" is "bot1" or
        # "bot3", since an edit has to come from whichever bot account actually sent the
        # original message.
        self.active_dice_sessions = {}
        # 🚦 NEW (per owner request — anti-spam cooldown): tracks how many slot/basketball/dice
        # plays a user has made in their current "burst". Every CASINO_COOLDOWN_TRIGGER plays,
        # they're locked out of all three games for CASINO_COOLDOWN_SECONDS — see
        # _check_casino_cooldown(). GLOBAL per user_id (applies across every chat, DM included),
        # same reasoning as user_mute_until above. {user_id: {"count": int, "locked_until": ts}}
        self.casino_play_counts = {}
        # 🎮 /casino menu's reply-based bet flow (per owner request) — user_id -> {"chat_id",
        # "prompt_msg_id", "game_key", "expiry"}. Tapping a game button under /casino edits that
        # SAME message into a "reply with your amount" prompt; replying to it with a bare
        # number plays that game — no /slot-style typed command needed at all from this flow.
        self.pending_casino_bet = {}
        # 🎗️ /mergele Limited Edition batch-merge wizard: prompt_msg_id -> {"batch_a": int,
        # "batch_b": int, "le_name_a": str, "le_name_b": str}. Same reply-to-bot-message shape.
        self.pending_mergele_prompt = {}
        # 🚫 .gban reply wizard: prompt_msg_id -> {"stage": "reason"/"duration", "target_id",
        # "reason" (added once stage 2 starts)}. Same shape again.
        self.pending_gban_prompt = {}

        # ---- LIVE game/feature state — deliberately EXCLUDED from cleanup_expired(). Each of
        # these already has its own dedicated timeout task elsewhere that removes an entry at
        # exactly the right moment (spawn timeout, quiz timeout, game round ending, etc.). A
        # generic time-based sweep here could race with that and tear down something mid-play. ----
        self.active_group_spawns = {}
        self.active_haido_events = {}
        self.pending_rarity_quiz = {}
        # 💍 /propose — target_user_id -> {"from_id", "chat_id", "sent_at", "msg_id"}. Keyed by
        # the person BEING proposed to (not the proposer) since only one active proposal can be
        # pending FOR any given target at a time — see marriage_propose_handler. Own dedicated
        # timeout watcher spawned per-proposal (asyncio.create_task in the handler itself), same
        # "one-off timer, not a shared sweep" reasoning as the games above.
        self.pending_marriage_proposals = {}
        # 🧠 /triviaspawn — chat_id -> {"options", "correct_index", "question", "msg_id",
        # "quiz_time", "solved", "attempted_users"}. Own dedicated timeout watcher
        # (trivia_quiz_timeout_watcher), same reasoning as the games above.
        self.pending_trivia_quiz = {}
        # 🏰 Squad-founding flow (/create): user_id -> {"stage": "name"/"photo", "name",
        # "chat_id", "prompt_msg_id", "deadline"}. Has its own dedicated timeout+refund
        # watchdog (_squad_setup_timeout_watchdog), same reasoning as the games above.
        self.pending_squad_setup = {}
        # 💌 Gift with a Note (per owner request) — (sender_id, receiver_id, char_id) -> {"note",
        # "expiry"}. /gift's note (if any) is typed in the SAME message as the char_id, but only
        # actually shown once the gift is confirmed — this just bridges that gap. Short-lived on
        # purpose: nobody sits on a gift confirmation for long, and a stale note is harmless
        # either way (worst case it silently expires and the gift just sends with no note).
        self.pending_gift_notes = {}
        # 🚨 Comedic "AML bust" jail (see try_deduct_bet_bot3 / AML_BUST_THRESHOLD below):
        # user_id -> unix timestamp the jail expires. Satire flavor only, not a real ban —
        # but it doubles as the enforcement point for bot3's actual max-single-bet ceiling.
        self.aml_jail = {}
        # 🃏 /cardgame — NEW (2026-09, per owner request): PVP multiplayer lobby, one active
        # lobby per chat_id at a time -> {"host_id", "host_mention", "bet", "players" (dict,
        # insertion-ordered: user_id -> html mention), "chat_id", "msg_id", "via" ("bot1"/
        # "bot3", whichever actually sent/owns the lobby message), "deadline"}. Each player's
        # bet is deducted the moment they tap Join (escrowed for real, not just displayed), so
        # this needs its own dedicated timeout+refund watchdog exactly like Squad Setup above
        # (_cardgame_lobby_timeout_watchdog) — same "one-off timer, not a shared sweep" reasoning.
        self.active_cardgame_lobbies = {}

        # ---- asyncio.Lock factories — never swept; a lock can be actively held ----
        self.spawn_locks = defaultdict(asyncio.Lock)
        self.bot_added_locks = defaultdict(asyncio.Lock)

        # 🩹 NEW (per owner report — repeated: "first person doesn't get it, last person
        # does", persisted even after the release_spawn timing fix): chat_id -> [(event,
        # user_id), ...], the in-flight candidate list for catch_handler's judging window —
        # see CATCH_RACE_JUDGE_WINDOW's docstring for why picking a winner by asyncio
        # task-scheduling order alone turned out not to be reliable enough, and why this
        # buffers candidates briefly and picks by Telegram's own authoritative message ID
        # instead.
        self.catch_race_buffer = {}

        # 🛡️ NEW (per owner report — tiny groups being spammed purely to farm spawns): cached
        # member counts for the min-group-size spawn/trivia gate — chat_id -> (count, ts). See
        # get_cached_group_member_count_nowait's docstring for why this is read-only in the
        # message hot path (never awaits Telegram's API directly there).
        self.group_member_count_cache = {}
        self.group_member_count_refresh_inflight = set()

        # ---- Misc ----
        self.bot_ids = []
        self.admin_warned_sticker = set()  # (chat_id, admin_id)
        self.admin_warned_char = set()     # (chat_id, admin_id)

    def cleanup_expired(self):
        """Sweeps every cache above that has a known-safe expiry rule. Safe to call from a
        periodic background loop (see start_bot_state_cleanup_loop) or an admin command.
        Returns how many entries were removed, purely for logging."""
        now = time.time()
        removed = 0
        # {key: timestamp} — a generous 24h window. Every real cooldown/debounce/anti-spam
        # window in this bot is seconds-to-minutes long, so anything older than a day is
        # unambiguously stale no matter which of these dicts it's in.
        for d in (self._recent_callback_taps, self._last_callback_click, self._recent_event_ids,
                  self._recent_bot_added_chats, self.recent_mod_actions, self.user_cooldowns,
                  self.force_sub_prompt_last_sent, self.user_mute_until):
            stale_keys = [k for k, ts in list(d.items()) if isinstance(ts, (int, float)) and now - ts > 86400]
            for k in stale_keys:
                del d[k]
                removed += 1
        # {key: (payload, expiry_ts)} — expiry is explicit, so use it exactly
        for d in (self.force_sub_membership_cache, self._welcome_toggle_cache, self._spawn_target_cache):
            stale_keys = [k for k, v in list(d.items()) if isinstance(v, tuple) and len(v) == 2 and now > v[1]]
            for k in stale_keys:
                del d[k]
                removed += 1
        # {key: {"expiry": ts, ...}}
        for d in (self.admin_cache, self.pending_sell_offers, self.pending_premium_gifts, self.pending_gift_notes, self.pending_casino_bet):
            stale_keys = [k for k, v in list(d.items()) if isinstance(v, dict) and now > v.get("expiry", 0)]
            for k in stale_keys:
                del d[k]
                removed += 1
        # {key: {"ts": ts, ...}} — no explicit expiry field, just a last-used timestamp; stale
        # past DICE_SESSION_MAX_AGE means the session is already being treated as dead by
        # _casino_edit_or_send anyway (it'll just send a fresh message instead of trying to
        # edit), so there's nothing lost by actually removing it here too.
        dice_stale_keys = [k for k, v in list(self.active_dice_sessions.items()) if isinstance(v, dict) and now - v.get("ts", 0) > DICE_SESSION_MAX_AGE]
        for k in dice_stale_keys:
            del self.active_dice_sessions[k]
            removed += 1
        # {user_id: {"count", "locked_until"}} — once any lock has fully expired the entry has
        # no more useful state to hold onto (a fresh burst just starts a new count at 0 anyway),
        # so it's safe to drop entirely rather than leave it sitting in memory forever.
        cooldown_stale_keys = [k for k, v in list(self.casino_play_counts.items()) if isinstance(v, dict) and now > v.get("locked_until", 0) and v.get("count", 0) == 0]
        for k in cooldown_stale_keys:
            del self.casino_play_counts[k]
            removed += 1
        # {user_id: date_str} — only TODAY's entries are useful (see the field's docstring
        # above); anything from a previous day is dead weight once the date has rolled over.
        today_str = datetime.now(TZ).strftime("%Y-%m-%d")
        stale_gate_keys = [k for k, v in list(self.daily_first_msg_gate.items()) if v != today_str]
        for k in stale_gate_keys:
            del self.daily_first_msg_gate[k]
            removed += 1
        return removed

bot_state = BotState()

# ==========================================
# ⚡ PREMIUM MATHEMATICAL BOLD SERIF FONT CONVERTER
# ==========================================
def f(text):
    normal = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    modern = "𝗔𝗕𝗖𝗗𝗘𝗙𝗚𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭𝗮𝗯𝗰𝗱𝗲𝗳𝗴𝗵𝗶𝗷𝗸𝗹𝗺𝗻𝗼𝗽𝗾𝗿𝘀𝘁𝘂𝘃𝘄𝘅𝘆𝘇𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵"
    trans = str.maketrans(normal, modern)
    return text.translate(trans)

# 🩹 NEW (2026-08, per owner request — /check redesign): two more fancy-font converters,
# same str.maketrans() approach as f() above. Both built off actual Unicode codepoint math
# (Mathematical Alphanumeric Symbols block for bold-italic-serif/sans-bold-italic, IPA
# Extensions/Phonetic Extensions for small caps) rather than hand-typed strings, so every
# glyph is verifiably the real character, not a visually-similar lookalike.
_SMALL_CAPS_MAP = {
    'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ꜰ', 'g': 'ɢ', 'h': 'ʜ', 'i': 'ɪ',
    'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ',
    's': 's', 't': 'ᴛ', 'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ', 'z': 'ᴢ',
}
def small_caps(text):
    """Lowercased then mapped letter-by-letter to small-caps Unicode (real IPA/Phonetic
    Extensions glyphs — 's' and 'x' have no true small-caps form in Unicode so pass through
    unchanged, same choice every legitimate small-caps font makes). Digits/punctuation/emoji
    pass through untouched."""
    return ''.join(_SMALL_CAPS_MAP.get(ch, ch) for ch in text.lower())

_BOLD_ITALIC_TRANS = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
    ''.join(chr(0x1D468 + i) for i in range(26)) + ''.join(chr(0x1D482 + i) for i in range(26))
)
def bold_italic_serif(text):
    """Mathematical Bold Italic (serif) — used for the event-name banner in /check."""
    return text.translate(_BOLD_ITALIC_TRANS)

_SANS_BOLD_ITALIC_TRANS = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
    ''.join(chr(0x1D63C + i) for i in range(26)) + ''.join(chr(0x1D656 + i) for i in range(26))
)
def sans_bold_italic(text):
    """Mathematical Sans-Serif Bold Italic — used for the 'RARITY' label in /check."""
    return text.translate(_SANS_BOLD_ITALIC_TRANS)

# 🩹 NEW (2026-08, per owner request): LE batch names are expected to start with a leading
# emoji (e.g. "🔱GODLIKE EDITION", set via /addle) — bookend_emoji_name() echoes that same
# emoji at the end too, PURELY at display time ("🔱GODLIKE EDITION" -> "🔱GODLIKE EDITION 🔱"),
# without ever touching the stored le_name itself. Used wherever an LE name is shown: /check,
# /harem, the LE gallery inline query, etc.
_LEADING_EMOJI_RE = re.compile(
    '^([\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF\U0001F1E6-\U0001F1FF]\uFE0F?)'
)
def extract_leading_emoji(text):
    """Returns the leading emoji character (with its variation selector, if any) if `text`
    starts with one, else None. Covers the emoji blocks actually in use across this file
    (RARITY_EMOJI, batch names, etc.) — not a complete emoji spec, just enough to reliably
    catch a person typing an emoji first thing in a name, which is all this needs to do."""
    if not text:
        return None
    m = _LEADING_EMOJI_RE.match(text.strip())
    return m.group(1) if m else None

_TRAILING_EMOJI_RE = re.compile(
    '([\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF\U0001F1E6-\U0001F1FF]\uFE0F?)\\s*$'
)
def extract_trailing_emoji(text):
    """Same idea as extract_leading_emoji, anchored to the END of the string instead."""
    if not text:
        return None
    m = _TRAILING_EMOJI_RE.search(text.strip())
    return m.group(1) if m else None

def bookend_emoji_name(name):
    """'🔱GODLIKE EDITION' -> '🔱GODLIKE EDITION 🔱'. Returns `name` completely unchanged if it
    doesn't start with a recognizable emoji — callers that need to REQUIRE one (e.g. /addle)
    check extract_leading_emoji() directly instead of relying on this silently no-op-ing.
    🩹 FIX (per owner clarification): some LE names were typed with an emoji on BOTH sides
    already, by hand, before this function ever existed (e.g. "🔱 GODLIKE EDITION 🔱" as the
    literal stored le_name) — blindly appending on top of that would double it up into
    "🔱 GODLIKE EDITION 🔱 🔱". Only appends when the name doesn't already end in an emoji."""
    name = (name or "").strip()
    emoji = extract_leading_emoji(name)
    if not emoji or extract_trailing_emoji(name):
        return name
    return f"{name} {emoji}"

# 🩹 FIX (owner report — "rarity emoji is showing up in unrelated places"): /check used to
# bookend the EVENT name with the card's RARITY emoji, since events looked like plain text
# with no emoji data of their own. They're not, though — /addchar already lets an event carry
# its own tag like "[🛸🏛️] Ancient Sci-Fi Edit", same spirit as LE names' leading emoji, just
# bracketed instead of bare. extract_bracketed_emoji() reads THAT tag instead of substituting
# something unrelated.
_LEADING_BRACKET_RE = re.compile(r'^\[([^\]]*)\]\s*')
def extract_bracketed_emoji(text):
    """'[🛸🏛️] Ancient Sci-Fi Edit' -> '🛸🏛️'. None if there's no leading [...] tag, or the
    tag doesn't start with a recognizable emoji (e.g. a plain text tag like '[TBA]')."""
    m = _LEADING_BRACKET_RE.match(text or "")
    if not m:
        return None
    inner = m.group(1).strip()
    return inner if inner and extract_leading_emoji(inner) else None

async def ensure_user_registered(user_id, fullname):
    global_system = await groups_config_col.find_one({"chat_id": "global_system"})
    welcome_bonus = global_system.get("default_welcome_bonus", 0) if global_system else 0
    await users_catcher_col.update_one(
        {"user_id": user_id},
        {
            "$setOnInsert": {
                "star_balance": welcome_bonus,
                "total_caught": 0,
                "harem": [],
                "fullname": fullname,
                "daily_cooldown": 0,
                "hunt_cooldown": 0,
                "last_daily": 0,
                "daily_streak": 0,
                "referral_count": 0,
                "referred_by": None,
                "daily_catches": 0,
                "last_catch_date": None,
                "spam_mute_until": 0,
                "msg_history": [],
                "force_sub_rewarded": False,
                "star_balance": 0.0
            }
        },
        upsert=True
    )

# ==========================================
# 🛡️ FLOOD WAIT PROTECTION ENGINE
# ==========================================
# 🩹 FIX: these used to retry through a FloodWaitError of ANY length, sleeping silently no
# matter how long Telegram asked for. Every caller already wraps these in a try/except — but
# an uncaught-by-them infinite sleep never reaches that except, so under load (e.g. right
# after a burst of activity) a command like /addchar or /exportchars would just hang with
# zero feedback, looking completely dead, sometimes for minutes. Short waits still get
# absorbed transparently (unnoticeable); anything longer is re-raised so the caller's own
# except block can report it instead of the command silently going nowhere.
FLOOD_WAIT_RETRY_CAP = 15  # seconds

async def send_safe_message(client, chat_id, text, **kwargs):
    while True:
        try:
            return await client.send_message(chat_id, text, **kwargs)
        except FloodWaitError as e:
            if e.seconds > FLOOD_WAIT_RETRY_CAP:
                logging.warning(f"⚠️ FloodWait too long ({e.seconds}s) — raising instead of blocking silently.")
                raise
            logging.warning(f"⚠️ FloodWait: sleeping {e.seconds}s...")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logging.error(f"❌ send_safe_message error: {e}")
            raise e

async def send_safe_file(client, chat_id, file, **kwargs):
    while True:
        try:
            return await client.send_file(chat_id, file, **kwargs)
        except FloodWaitError as e:
            if e.seconds > FLOOD_WAIT_RETRY_CAP:
                logging.warning(f"⚠️ FloodWait too long ({e.seconds}s) — raising instead of blocking silently.")
                raise
            logging.warning(f"⚠️ FloodWait (file): sleeping {e.seconds}s...")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logging.error(f"❌ send_safe_file error: {e}")
            raise e

# ==========================================
# 🧵 NON-BLOCKING EXECUTOR BRIDGE
# ==========================================
async def run_blocking(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    if kwargs:
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))
    return await loop.run_in_executor(None, func, *args)

# 🚀 COOLDOWN RATE-LIMITER
async def is_on_cooldown(user_id, command_name, cooldown_seconds=3):
    if user_id == OWNER_ID: return False, 0
    now = time.time()
    cache_key = (user_id, command_name)
    if cache_key in user_cooldowns:
        elapsed = now - user_cooldowns[cache_key]
        if elapsed < cooldown_seconds:
            return True, int(cooldown_seconds - elapsed)
    user_cooldowns[cache_key] = now
    return False, 0

# ==========================================
# 🔒 ATOMIC WALLET DEBIT — prevents double-spend / negative-balance races
# ==========================================
# The old pattern across the casino games used to be:
#   1. find_one() to read the balance
#   2. check `balance < bet` in Python
#   3. update_one({"$inc": {"star_balance": -bet}}) to actually deduct
# Steps 1 and 3 were NOT atomic — two rapid-fire requests from the same user (a fast
# double-tap, a spam script, or just bad luck with network timing) could both read the
# SAME pre-deduction balance, both pass the check, and both deduct — letting a player
# spend far more than they actually had, or drive their balance negative.
#
# This version does the check-and-deduct as a SINGLE atomic MongoDB operation: the
# `star_balance` filter and the `$inc` happen together, so Mongo guarantees only
# requests that still have enough balance AT THE MOMENT OF THE WRITE can succeed.
async def try_deduct_balance(user_id, amount):
    """Attempts to atomically deduct `amount` ⭐ Star from a user's star_balance.
    Returns True if the deduction succeeded (they had enough), False if they didn't
    (or amount <= 0). Safe to call concurrently — never overdraws a balance.
    🩹 2026-08 USD RETIREMENT: this used to deduct USD from wallet_balance — now functionally
    identical to try_deduct_star() below, kept under its original name only because dozens of
    existing casino-game call sites (burn fees, bets, etc.) already call it by this name."""
    if amount <= 0:
        return False
    result = await users_catcher_col.update_one(
        {"user_id": user_id, "star_balance": {"$gte": amount}},
        {"$inc": {"star_balance": -amount}}
    )
    return result.modified_count > 0

async def try_deduct_star(user_id, amount):
    """Same atomic check-and-deduct pattern as try_deduct_balance, for ⭐ Star balance —
    used everywhere a user spends Star."""
    if amount <= 0:
        return False
    result = await users_catcher_col.update_one(
        {"user_id": user_id, "star_balance": {"$gte": amount}},
        {"$inc": {"star_balance": -amount}}
    )
    return result.modified_count > 0

async def try_deduct_vlt(user_id, amount):
    """Same atomic check-and-deduct pattern as try_deduct_star, but for 💠 VLT balance —
    used everywhere a user spends VLT (Owner Shop purchases via /buy, /sellvlt)."""
    if amount <= 0:
        return False
    result = await users_catcher_col.update_one(
        {"user_id": user_id, "vlt_balance": {"$gte": amount}},
        {"$inc": {"vlt_balance": -amount}}
    )
    return result.modified_count > 0

# ==========================================
# 🔒 INLINE-BUTTON DOUBLE-TAP GUARD — prevents duplicate execution of one-shot
# button actions (gifting a card, confirming a trade, joining a bet, resolving a
# hi-lo round, etc.)
# ==========================================
# Telegram can deliver a callback twice (client-side double-tap, network retry, an
# impatient user mashing the button before the message visibly updates). Without a
# guard, a single logical "click" could run the underlying handler code more than
# once — duplicating a card transfer, doubling a payout, or deducting a bet twice.
# Keyed on (user, message, exact button payload) so this only ever blocks a REPEAT of
# the same tap — different users, or the same user on a different button/message,
# are never affected.
_recent_callback_taps = bot_state._recent_callback_taps
CALLBACK_TAP_WINDOW = 2.5  # seconds — long enough to eat a double-tap, short enough to never block a genuine retry

def claim_single_tap(event, extra=""):
    """Returns True the first time this exact (user, message, button data) combo is
    seen within the window, and False for any repeat — the caller should bail out
    (just event.answer() and return) on False instead of re-running the action."""
    key = (event.sender_id, event.message_id, event.data, extra)
    now = time.time()
    last = _recent_callback_taps.get(key)
    if last is not None and (now - last) < CALLBACK_TAP_WINDOW:
        return False
    _recent_callback_taps[key] = now
    if len(_recent_callback_taps) > 5000:  # cheap bound so this dict can never grow unbounded
        cutoff = now - CALLBACK_TAP_WINDOW
        for k, ts in list(_recent_callback_taps.items()):
            if ts < cutoff:
                del _recent_callback_taps[k]
    return True

# ==========================================
# 🔀 DOT/SLASH COMMAND PREFIX HELPERS
# ==========================================
# Every command handler now matches both '/cmd' and '.cmd' (see the pattern= regexes below).
# These two helpers keep the handful of places OUTSIDE those regexes — moderation/mute
# enforcement, spam filters — in sync, so a muted user can't dodge enforcement just by
# switching prefix.
def _has_command_prefix(text):
    return bool(text) and text[0] in ('/', '.')

def _extract_command_word(text):
    """Normalizes a command message to '/wordname' regardless of whether the person used
    '/' or '.', and strips any '@botname' suffix AND any following arguments. Returns None if
    `text` isn't a command. (e.g. '.collect Sailor Moon' -> '/collect', not '/collect Sailor
    Moon' — a message with an argument is still that command.)"""
    if not _has_command_prefix(text):
        return None
    rest = text[1:]
    word = rest.split()[0] if rest.split() else rest
    return '/' + word.split('@')[0]

def today_start():
    return datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

# ==========================================
# 🎯 OWN-USERNAME COMMAND GUARD
# ==========================================
# 🩹 FIX: every command pattern below used to accept ANY '@botname' mention, e.g.
# '^[/.]harem(?:@\w+)?...$' matches '/harem@ThisBot' AND '/harem@SomeOtherBot' equally — the
# regex never actually checked WHICH bot was mentioned. In a group with more than one bot
# sharing a command name, this bot fired even when the person explicitly addressed a
# different bot. own_pattern() wraps a pattern string so that when a message DOES contain an
# explicit @mention right after the command word, it's only treated as a match if that
# mention is this bot's own username — messages with no mention at all still match exactly
# as before. BOT1_USERNAME/BOT2_USERNAME are populated once at startup (see run_bot1_forever/
# run_bot2_forever); until then (a brief window before the very first connect), the check is
# skipped so nothing regresses.
BOT1_USERNAME = None
BOT2_USERNAME = None
BOT3_USERNAME = None
_OWN_MENTION_RE = re.compile(r'^[/.]\S*?@(\w+)')

def own_pattern(regex_str, bot='bot1'):
    """Wraps a command regex so it only matches when either (a) there's no explicit
    '@botname' mention at all, or (b) the mention is this bot's own username. Does NOT alter
    the original regex's capture groups — group(N) on the resulting match works exactly like
    it did before, since the mention check is a completely separate pass over the raw text."""
    compiled = re.compile(regex_str)
    def matcher(text):
        if not text:
            return None
        m = compiled.match(text)
        if not m:
            return None
        mention = _OWN_MENTION_RE.match(text)
        if mention:
            own_username = {'bot1': BOT1_USERNAME, 'bot2': BOT2_USERNAME, 'bot3': BOT3_USERNAME}.get(bot)
            if own_username and mention.group(1).lower() != own_username:
                return None
        return m
    return matcher

# 🚀 REAL-TIME USER METRICS TRACKER
def track_user_metrics(user_id, username, first_name):
    async def _track_bg():
        try:
            await users_col.update_one(
                {"user_id": user_id},
                {"$set": {"username": username, "first_name": first_name, "last_active": datetime.now(TZ)}},
                upsert=True
            )
        except Exception as e:
            print(f"Error in track_user_metrics: {e}")
    try:
        asyncio.create_task(_track_bg())
    except Exception:
        pass

# 🚀 CENTRALIZED ERROR NOTIFICATION
async def report_system_error(location, error_msg):
    try:
        alert_text = (
            f"🚨 <b>CRITICAL SYSTEM ERROR</b>\n"
            f"📍 <b>Location:</b> <code>{escape_html(location)}</code>\n"
            f"❌ <b>Error:</b> <code>{escape_html(str(error_msg))}</code>\n"
            f"⏰ <b>Time:</b> <code>{datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}</code>"
        )
        await bot1.send_message(OWNER_ID, bq(alert_text), parse_mode='html')
    except Exception: pass

async def get_today_stats():
    # 🩹 FIX: daily_report_scheduler fires this at 6:00 AM, intending to summarize the PREVIOUS
    # full day — but the query window here was [midnight TODAY, now), which at 6am is only the
    # last 6 HOURS of the brand-new day, not yesterday's full 24 hours. That's the real reason
    # catches, the rarity breakdown, AND groups-active all looked far smaller than actual daily
    # activity — the report was only ever seeing a 6-hour sliver, every single day. Window is
    # now the full [yesterday's midnight, today's midnight) range.
    now = datetime.now(TZ)
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    period_end = today_midnight.timestamp()
    period_start = (today_midnight - timedelta(days=1)).timestamp()
    pipeline = [
        {"$unwind": "$harem"},
        {"$match": {"harem.caught_date": {"$gte": period_start, "$lt": period_end}}},
        {"$facet": {
            "totals": [{"$group": {
                "_id": None,
                "total": {"$sum": 1},
                "catchers": {"$addToSet": "$user_id"},
                "groups": {"$addToSet": "$harem.chat_id"}
            }}],
            "rarity": [{"$group": {"_id": "$harem.rarity", "count": {"$sum": 1}}}]
        }}
    ]
    result = await users_catcher_col.aggregate(pipeline).to_list(length=1)
    doc = result[0] if result else {"totals": [], "rarity": []}
    totals = doc["totals"][0] if doc["totals"] else {"total": 0, "catchers": [], "groups": []}
    rarity_breakdown = {r["_id"]: r["count"] for r in doc["rarity"]}

    return {
        "total_catches": totals["total"],
        "groups": len(totals["groups"]),
        "rarity": rarity_breakdown,
        "catchers": len(totals["catchers"])
    }

async def send_daily_report():
    stats = await get_today_stats()
    # 🩹 FIX: label with the date actually being summarized — yesterday, since the window above
    # is [yesterday midnight, today midnight) — not today's date, which was misleading.
    report_date = (datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
    text = f"📊 <b>Daily Report – {report_date}</b>\n"
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"🐇 <b>Total Catches:</b> <code>{stats['total_catches']}</code>\n"
    text += f"👥 <b>Unique Catchers:</b> <code>{stats['catchers']}</code>\n"
    text += f"🪐 <b>Groups Active:</b> <code>{stats['groups']}</code>\n\n"
    text += f"🏷️ <b>Rarity Breakdown:</b>\n"
    for rarity, count in sorted(stats['rarity'].items(), key=lambda x: x[1], reverse=True):
        text += f"  • {rarity} — <code>{count}</code>\n"
    if not stats['rarity']:
        text += "  <i>No catches that day.</i>\n"
    text += f"\n<code>/today</code> to see today's top catchers so far."

    # Send to force-sub group
    try:
        await send_safe_message(bot1, FORCE_SUB_CHAT_ID, text, parse_mode='html')
    except Exception as e:
        print(f"Daily report to group failed: {e}")

    # Send to owner's DM
    try:
        await send_safe_message(bot1, OWNER_ID, text, parse_mode='html')
    except Exception as e:
        print(f"Daily report to owner DM failed: {e}")
async def daily_report_scheduler():
    while True:
        now = datetime.now(TZ)
        target = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        # Check if already sent today (to avoid duplicates after restart)
        last_sent = await bot_settings_col.find_one({"_id": "daily_report_last_sent"})
        today_str = datetime.now(TZ).strftime("%Y-%m-%d")
        if last_sent and last_sent.get("date") == today_str:
            continue
        await send_daily_report()
        await bot_settings_col.update_one(
            {"_id": "daily_report_last_sent"},
            {"$set": {"date": today_str}},
            upsert=True
        )

# ==========================================
# 🎗️ LIMITED EDITION 💠 VLT payout — REMOVED (per owner request, 2026-08). LE-tagged
# characters no longer pay their owner any daily VLT; get_le_rate_map(), the pending_le_claims
# offer flow, and the leclaim_take/skip callbacks are all gone with it. The LE tagging system
# itself — /addle, /mergele, batch numbers, the 🎗️ Limited Editions harem gallery — is
# unaffected; le_daily_vlt still gets stored per character via /addle, it's just never read
# or paid out by anything anymore.
# ==========================================

async def get_le_batch_names():
    """[(le_name, char_count), ...] — every DISTINCT Limited Edition batch name currently in
    use, sorted alphabetically, with how many characters are tagged under it. This exact sort
    order is the shared contract between _build_le_batch_menu (which turns this into
    switch_inline buttons carrying a plain integer index, e.g. "le.3") and
    handle_le_inline_query (which resolves that same index back to a name) — both always call
    THIS function fresh rather than caching the order separately, so they can never drift out
    of sync with each other.
    ⚠️ That "le.3" index is a PURELY COSMETIC, alphabetical-position number, recomputed fresh
    on every call — it shifts around whenever batches are renamed/added/merged. It is NOT the
    same thing as the permanent batch_number in le_batches_col (see get_or_create_le_batch_number
    below), which /addle and /mergele use precisely because it never shifts. Don't cross the
    streams between the two."""
    le_chars = [c for c in await get_all_characters_cached() if c.get("is_limited_edition")]
    counts = {}
    for c in le_chars:
        name = c.get("le_name") or "?"
        counts[name] = counts.get(name, 0) + 1
    return sorted(counts.items())

async def get_or_create_le_batch_number(le_name):
    """Returns the permanent batch_number for le_name, assigning the next free one (current
    max + 1, starting at 1) and inserting it into le_batches_col if this name has never been
    seen before. Numbers are NEVER reused, even after /mergele retires one — see le_batches_col's
    own docstring above for why. This is the single place a new number ever gets minted."""
    existing = await le_batches_col.find_one({"le_name": le_name})
    if existing:
        return existing["batch_number"]
    highest = await le_batches_col.find_one(sort=[("batch_number", -1)])
    next_number = (highest["batch_number"] + 1) if highest else 1
    try:
        await le_batches_col.insert_one({"batch_number": next_number, "le_name": le_name})
    except DuplicateKeyError:
        # Lost a race against another concurrent insert (owner-only + very low frequency, but
        # cheap to handle correctly) — whoever won gets to keep their number, we just look up
        # whatever's there now instead of failing outright.
        existing = await le_batches_col.find_one({"le_name": le_name})
        if existing:
            return existing["batch_number"]
        raise
    return next_number

async def get_le_name_by_batch_number(number):
    """le_name for a permanent batch_number, or None if that number doesn't exist (either
    never assigned, or retired by a past /mergele)."""
    doc = await le_batches_col.find_one({"batch_number": number})
    return doc["le_name"] if doc else None

async def ensure_all_le_batches_numbered():
    """One-time-per-batch backfill: any le_name already in use on characters_base_col that
    predates le_batches_col (i.e. every batch that existed before this numbering system did)
    gets a permanent number assigned now, in the same alphabetical order get_le_batch_names()
    already displays them in — so the very first /addle or /mergele referencing a number means
    exactly the batch the owner sees at that position in /harem's Limited Editions menu. Cheap
    (one query, a handful of numbered batches at most) and safe to call on every startup — an
    already-numbered batch is a no-op via get_or_create_le_batch_number's own existing-name
    check, so this never reassigns or shuffles a number that's already been handed out.

    🩹 FIX (2026-08, per owner report — "/mergele does nothing, old LEs aren't #0 #1 yet"):
    this used to be the very last line of create_indexes(), which only ever runs inside
    _create_indexes_background() — a fire-and-forget asyncio.create_task() that deliberately
    does NOT block bot startup (see that function's own docstring). Two problems fell out of
    that: (1) any exception from one of the ~35 unrelated create_index() calls earlier in that
    function aborted it right there, so this line was sometimes never reached at all — silently,
    since _create_indexes_background() only prints the error; (2) even on a clean run, the
    owner could hit /mergele or /addle #N in the first few seconds after a restart, before this
    background task got around to running. Either way, every pre-existing LE batch stayed
    permanently un-numbered, and /mergele #A #B / /addle #N had nothing to look up.
    It's now awaited directly and unconditionally in run_bot1_forever(), the same place as the
    other one-time migrations (run_usd_to_star_migration, run_trivia_hard_tier_wipe, etc.) —
    so it always finishes before bot1 can receive a single /mergele or /addle command, and a
    failure here surfaces the same way theirs do (the connect loop logs it and retries) instead
    of vanishing into create_indexes()'s catch-all."""
    names = await get_le_batch_names()
    numbered = 0
    for le_name, _count in names:
        try:
            await get_or_create_le_batch_number(le_name)
            numbered += 1
        except Exception as e:
            print(f"⚠️ ensure_all_le_batches_numbered: failed to number LE batch '{le_name}': {e}")
    print(f"🎗️ LE batch numbering check: {numbered}/{len(names)} batch name(s) confirmed numbered.")

async def notify_owner_of_unemojied_le_batches():
    """🩹 NEW (2026-08, per owner request): /addle now requires a leading emoji on any NEW LE
    batch name (see extract_leading_emoji), but that check only ever runs at creation time — it
    can't retroactively fix batches that were already sitting in le_batches_col from before this
    requirement existed, so those keep displaying with nothing to bookend (see
    bookend_emoji_name). Runs once per restart, right after ensure_all_le_batches_numbered so
    every batch already has its #number to reference. Purely a notification — never edits
    anything itself; the owner fixes each one with /renamele #N whenever they get to it."""
    try:
        batches = await le_batches_col.find({}, {"batch_number": 1, "le_name": 1, "_id": 0}).to_list(length=None)
    except Exception as e:
        print(f"⚠️ notify_owner_of_unemojied_le_batches: lookup failed: {e}")
        return
    missing = [b for b in batches if not extract_leading_emoji(b.get("le_name", ""))]
    if not missing:
        return
    listing = "\n".join(f"  #{b['batch_number']} — {escape_html(b['le_name'])}" for b in sorted(missing, key=lambda b: b['batch_number']))
    try:
        await bot1.send_message(
            OWNER_ID,
            f"🏷️ <b>{len(missing)} LE batch name(s) still have no emoji:</b>\n{listing}\n\n"
            f"Fix one anytime with <code>/renamele #N</code> — no rush, this is just a reminder "
            f"since /check and /harem have nothing to bookend on these until then.",
            parse_mode='html'
        )
    except Exception as e:
        print(f"⚠️ notify_owner_of_unemojied_le_batches: couldn't DM owner: {e}")



# ==========================================
# 🏅 ACHIEVEMENT SYSTEM (Updated)
# ==========================================
# Rank 1 (rarest) -> Rank 9 (most common). Rank 1 (ULTRA) is reserved exclusively for
# characters whose original /addchar media was a VIDEO — see add_character()'s enforcement.
# Note: UNCOMMON contains COMMON as a substring — classify_rarity() matches longest-name-first
# specifically so this doesn't get misread, so it's safe to keep both.
# 🩹 FIX: UNCOMMON must rank ABOVE (rarer than) COMMON — "common" is meant to be the most
# abundant/lowest tier of the two. They used to be swapped; this is now the correct order.
RARITY_TIERS = ["ULTRA", "LEGEND", "MYTHIC", "EPIC", "INCREDIBLE", "GOLD", "RARE", "UNCOMMON", "COMMON"]
# ⚠️ SINGLE SOURCE OF TRUTH for rarity emoji (Rarity No.1 = ULTRA ... No.9 = UNCOMMON).
# Change emoji here ONLY — RARITY_NUM_MAP below is generated from this, so spawns, /who,
# the /addchar legend, achievements, stats and /changeallrarity all stay in sync automatically.
RARITY_EMOJI = {
    "ULTRA": "🎞", "LEGEND": "☀️", "MYTHIC": "🦚", "EPIC": "⚡", "INCREDIBLE": "🐦‍🔥",
    "GOLD": "🌕", "RARE": "🫐", "COMMON": "📦", "UNCOMMON": "🌱"
}
RARITY_DEFAULT_EMOJI = "❓"  # fallback shown only when a tier truly can't be classified
# 💰 OWNER SHOP 💠 VLT PRICE LIST (per rarity tier) — owner-set fixed prices (Aug 2026),
# replacing the old "rank * 100" formula. This is the SINGLE SOURCE OF TRUTH for what /buy
# [char_id] (Owner Shop) charges and what /sell buyback bases its 0.5x-1.5x offer on — see
# vlt_price_for_char() below. Change a tier's price here ONLY; everywhere else (the /show
# gallery buy button, /buy, /sell, the /market help text) reads through that one function.
# 🩹 2026-08 USD/MARKET RETIREMENT: this list used to be priced in ⭐ Star. Per owner
# directive, cards can now only be bought/sold in the Market using 💠 VLT — Star's role is
# now limited to rewards and exchanging into VLT (see /buyvlt, /sellvlt). The numbers
# themselves are unchanged, just relabeled from ⭐ to 💠.
# 🩹 CHANGED (2026-08, per owner request): ULTRA/LEGEND/MYTHIC — the top 3 rarities — now
# priced at 3x their original value (2500/2000/1800 → 7500/6000/5400). Everything below MYTHIC
# is unchanged. Since scrap_price_for_char() derives its floor straight from this table
# (vlt_price_for_char() × SCRAP_MIN_MULT), the /sell scrap floor for these 3 tiers scales up
# 3x automatically too — no second place to edit.
RARITY_VLT_PRICE = {
    "ULTRA": 7500, "LEGEND": 6000, "MYTHIC": 5400, "EPIC": 1500, "INCREDIBLE": 1000,
    "GOLD": 700, "RARE": 500, "UNCOMMON": 400, "COMMON": 300
}
MMK_PER_USD = 4000  # [LEGACY] 1 old-USD = 4000 old-MMK — kept only so pre-existing
# "<old MMK value> / MMK_PER_USD" audit expressions elsewhere in this file still evaluate to
# the same numbers they always have. USD itself no longer exists as a currency in this bot —
# see the USD → STAR RETIREMENT block further down.
def _build_fancy_font_reverse_map():
    mapping = {}
    for cp in range(0x1D400, 0x1D800):
        try:
            name = unicodedata.name(chr(cp))
        except ValueError:
            continue
        if not name.startswith("MATHEMATICAL "):
            continue
        tokens = name.split()
        last = tokens[-1]
        if "DIGIT" in tokens:
            digit_words = {"ZERO": "0", "ONE": "1", "TWO": "2", "THREE": "3", "FOUR": "4",
                           "FIVE": "5", "SIX": "6", "SEVEN": "7", "EIGHT": "8", "NINE": "9"}
            if last in digit_words:
                mapping[chr(cp)] = digit_words[last]
        elif "DOTLESS" in tokens:
            mapping[chr(cp)] = last.lower()
        elif len(last) == 1 and last.isalpha():
            if "SMALL" in tokens:
                mapping[chr(cp)] = last.lower()
            elif "CAPITAL" in tokens:
                mapping[chr(cp)] = last.upper()
    return mapping

_FANCY_TO_PLAIN = str.maketrans(_build_fancy_font_reverse_map())

def normalize_char_id_input(raw_id):
    """Accepts whatever a player typed for a character ID — with or without the internal
    'BOD' prefix, any case — and returns the canonical stored form (e.g. 'BOD1234').
    Players are no longer shown the 'BOD' prefix, so lookups must accept the bare number."""
    raw_id = (raw_id or "").strip().upper()
    if raw_id.isdigit():
        return f"BOD{raw_id}"
    return raw_id

def display_char_id(char_id):
    """Strips the internal 'BOD' prefix for display — players only ever see the number."""
    if isinstance(char_id, str) and char_id.upper().startswith("BOD"):
        return char_id[3:]
    return char_id

def artist_line(card_doc, prefix="", suffix="\n"):
    """Builds a formatted 'Artist' credit line for any character display, given either a
    full character document (dict with an 'artist' key) or the artist name itself.
    Returns '' when no artist is on file, so callers can safely inline this everywhere
    a character's name/rarity is shown (catch results, /check, /harem, gifts, etc.)."""
    if isinstance(card_doc, dict):
        artist = card_doc.get("artist")
    else:
        artist = card_doc
    if not artist or not str(artist).strip():
        return ""
    return f"{prefix}🎨 <b>Artist:</b> <code>{escape_html(str(artist).strip())}</code>{suffix}"

# ==========================================
# 🔎 PHOTO/VIDEO IDENTIFY — perceptual hash (dHash) so a re-uploaded/re-saved copy of a
# character's media (screenshotted, saved to gallery, re-compressed, etc.) can still be
# matched back to the original character, even though Telegram assigns it a brand-new
# file_id/file_unique_id on re-upload. Pure PIL implementation — no extra dependency.
# ==========================================
PHASH_SIZE = 16  # 16x16 -> 256-bit hash — bumped from 8 (64-bit) to fix false-positive character misidentification (see IDENTIFY_HAMMING_THRESHOLD below and _identify_media_and_reply)

def compute_dhash(media_bytes, hash_size=PHASH_SIZE):
    """Difference hash: robust to re-encoding/resizing, NOT robust to heavy crops/edits."""
    try:
        img = Image.open(io.BytesIO(media_bytes)).convert("L").resize(
            (hash_size + 1, hash_size), Image.LANCZOS
        )
        pixels = list(img.getdata())
        bits = []
        for row in range(hash_size):
            row_pixels = pixels[row * (hash_size + 1): (row + 1) * (hash_size + 1)]
            for col in range(hash_size):
                bits.append("1" if row_pixels[col] > row_pixels[col + 1] else "0")
        return format(int("".join(bits), 2), f'0{hash_size * hash_size // 4}x')
    except Exception as e:
        print(f"compute_dhash error: {e}")
        return None

def hamming_distance(hash_a, hash_b):
    if not hash_a or not hash_b:
        return 999
    try:
        return bin(int(hash_a, 16) ^ int(hash_b, 16)).count("1")
    except Exception:
        return 999

async def compute_phash_for_message(msg):
    """Given a Telethon message with a photo or video, return a dHash string of the
    image (or the video's thumbnail — we never download a full video, just its thumb)."""
    try:
        if msg.photo:
            media_bytes = await msg.download_media(file=bytes)
        elif msg.video or msg.document:
            media_bytes = await msg.download_media(thumb=-1, file=bytes)
        else:
            return None
        if not media_bytes:
            return None
        return compute_dhash(media_bytes)
    except Exception as e:
        print(f"compute_phash_for_message error: {e}")
        return None

def classify_rarity(rarity_str):
    if not rarity_str:
        return "OTHER"
    plain = rarity_str.translate(_FANCY_TO_PLAIN).upper()
    # Longest-first so a tier name that happens to contain a shorter tier name as a
    # substring of another (e.g. a past scheme had "RONIN" containing "ONI") never gets misclassified.
    for tier in sorted(RARITY_TIERS, key=len, reverse=True):
        if tier in plain:
            return tier
    return "OTHER"

_RARITY_NO_SUFFIX_RE = re.compile(r'\s*No\.?\s*\d+\s*$', re.IGNORECASE)

def strip_rarity_number(rarity_str):
    """Strips a trailing ' No.X' from a rarity display string — in ANY font, fancy
    math-unicode digits/letters included — e.g. '☀️ 𝖫𝖤𝖦𝖤𝖭𝖣 𝖭𝗈.𝟤' -> '☀️ 𝖫𝖤𝖦𝖤𝖭𝖣'.
    .translate() maps one character to exactly one character, so a plain-ASCII 'shadow' of
    the same string is guaranteed the same length — find the suffix in the shadow, then slice
    the ORIGINAL string at that same index, which keeps whatever font the rest was in."""
    if not rarity_str:
        return rarity_str
    plain_shadow = rarity_str.translate(_FANCY_TO_PLAIN)
    m = _RARITY_NO_SUFFIX_RE.search(plain_shadow)
    if m:
        return rarity_str[:m.start()].rstrip()
    return rarity_str

def rarity_rank_value(rarity_str):
    """Single source of truth for 'how rare is this, as a sortable number' — rank 1
    (rarest) gets the highest number, rank 9 (most common) gets the lowest. Replaces the
    hardcoded per-tier rank dicts that used to be copy-pasted in several places."""
    tier = classify_rarity(rarity_str)
    try:
        return len(RARITY_TIERS) - RARITY_TIERS.index(tier)
    except ValueError:
        return 0

def vlt_price_for_char(char_doc):
    """💠 OWNER SHOP / MARKET PRICE — fixed VLT price per rarity tier, per RARITY_VLT_PRICE
    (owner-set price list, Aug 2026 — replaces the old "rank * 100" formula). Kept as its own
    named function so the pricing rule reads clearly at every /buy [char id] / /show call site.
    🩹 2026-08: this used to be priced in ⭐ Star — the Market now runs on 💠 VLT only."""
    if not char_doc:
        return 0
    tier = classify_rarity(char_doc.get("rarity_tier") or char_doc.get("rarity", ""))
    return RARITY_VLT_PRICE.get(tier, 0)

def rarity_price_list_text():
    """Compact 'EMOJI TIER price💠 / ...' string built straight from RARITY_VLT_PRICE, in
    rarity order (No.1 rarest first) — used wherever help text needs to show the current
    Owner Shop price list, so it can never drift out of sync with the real prices again."""
    return " / ".join(
        f"{RARITY_EMOJI.get(tier, RARITY_DEFAULT_EMOJI)}{RARITY_DISPLAY_NAME.get(tier, tier)} {RARITY_VLT_PRICE.get(tier, 0)}💠"
        for tier in RARITY_TIERS
    )

def catch_star_reward(rarity_str):
    """⭐ Star bonus paid out when a spawn is actually CAUGHT from the wild — not bought from
    the Owner Shop. A DELIBERATELY SEPARATE, smaller, purely rank-based formula (rank * 25,
    ULTRA flat 500⭐) from the Owner Shop's VLT price list (RARITY_VLT_PRICE) — it does not
    read that table and is unaffected by Owner Shop price changes, so catching a card always
    stays meaningfully cheaper (in VLT-equivalent terms) than buying it outright."""
    if classify_rarity(rarity_str) == "ULTRA":
        return 500
    return rarity_rank_value(rarity_str) * 25

def catch_rarity_star_bonus(rarity_str):
    """⭐ Extra flat Star bonus paid on top of catch_star_reward() when a spawn is CAUGHT,
    keyed by rarity tier. 🩹 2026-08 USD RETIREMENT: this used to be a USD wallet_balance
    payout (_RARITY_VALUE_MAP, itself an old-MMK-worth figure) — since USD no longer exists,
    it's re-expressed directly in Star, keeping the exact same relative spread between
    rarities that _RARITY_VALUE_MAP always had (each tier's old MMK worth / 1,000,000, which
    was 1 Star's own old MMK value before the USD migration — see USD → STAR RETIREMENT
    below). Paid into star_balance alongside catch_star_reward(), never on its own."""
    tier = classify_rarity(rarity_str)
    return _RARITY_STAR_BONUS_MAP.get(tier, 0)
# ==========================================
# ⭐ STAR DISPLAY FORMATTING
# ==========================================
# 🩹 2026-08 USD RETIREMENT: this file used to also have format_usd() / format_usd_compact()
# for USD wallet_balance display. Both are retired along with USD — every call site that used
# to format a USD amount now calls format_star_plain() below instead.
def format_star_plain(star_amount):
    """Clean Star display — whole numbers show with no decimals, fractional Star (e.g. quiz
    rewards of 0.2-1⭐) show up to 2 decimal places."""
    try:
        amount = float(star_amount)
    except Exception:
        return f"{star_amount} ⭐"
    if amount.is_integer():
        return f"{int(amount):,} ⭐"
    return f"{amount:,.2f} ⭐"

def format_vlt_plain(vlt_amount):
    """💠 VLT display, same shape as format_star_plain() — whole numbers with no decimals,
    fractional VLT (e.g. a /sellvlt of 2.5💠) shown with up to 2 decimal places."""
    try:
        amount = float(vlt_amount)
    except Exception:
        return f"{vlt_amount} 💠"
    if amount.is_integer():
        return f"{int(amount):,} 💠"
    return f"{amount:,.2f} 💠"

# ==========================================
# RARITY SPAWN WEIGHT — a single GLOBAL "level" dial (via /spawnweight) that boosts how often
# Rarity 1-4 (RARITY_GATE_TIERS — the same quiz-gated bracket, defined further down) spawns,
# relative to its own defaults below. Rarity 5-9 (the common tiers) are never touched by this —
# only the quiz-gated bracket scales. "Global" means exactly that: one setting, shared by every
# chat, regardless of which chat the owner happens to type the command in.
# 🩹 STEEPENED (2026-08, per owner request — "high-rarity cards shouldn't be easy to get
# anymore"): every tier from ULTRA through GOLD dropped noticeably, RARE stayed about the same,
# and UNCOMMON/COMMON rose to compensate — widening the gap between rare and common well beyond
# the old curve. Old vs new probability (out of the total pool, before /spawnweight's own
# multiplier on Rarity 1-4): ULTRA 3.5%→1.5%, LEGEND 1.5%→0.5%, MYTHIC 3.5%→1.5%, EPIC 5.0%→
# 2.5%, INCREDIBLE 6.0%→4.0%, GOLD 10.0%→6.9%, RARE 15.9%→13.9%, UNCOMMON 24.9%→27.2%,
# COMMON 29.9%→42.1%.
DEFAULT_RARITY_WEIGHTS = dict(zip(RARITY_TIERS, [3, 1, 3, 5, 8, 14, 28, 55, 85]))
# No.1 ULTRA=3 ... No.9 COMMON=85 — a much wider gap between rarest and most common than before.
# ✏️ To add more levels yourself later, just add another "level: multiplier" pair here — e.g.
# {1: 1, 2: 3, 3: 5} adds a level 3 that's 5x default. Nothing else needs to change.
SPAWNWEIGHT_LEVEL_MULTIPLIERS = {1: 1, 2: 3}
_cached_spawnweight_level = 1  # level 1 = 1x = untouched defaults

async def load_rarity_weight_cache():
    global _cached_spawnweight_level
    try:
        doc = await bot_settings_col.find_one({"_id": "rarity_spawn_weight_level"})
        if doc and doc.get("level") in SPAWNWEIGHT_LEVEL_MULTIPLIERS:
            _cached_spawnweight_level = doc["level"]
    except Exception as e:
        print(f"load_rarity_weight_cache error: {e}")

# ==========================================
# 🎯 ULTRA SCARCITY CAP — CORRECTED 2026-08 (previous version of this note was wrong): the cap
# is PER-CHARACTER, not pooled across the tier. Every individual ULTRA (Rarity No.1) character
# gets its own spawn_limit of ULTRA_DEFAULT_CATCH_LIMIT copies, via the exact same generic
# spawn_limit/spawn_count mechanism every other capped character already uses (see /addchar,
# /editchar, /addle) — there is nothing ULTRA-specific about the mechanism itself anymore, only
# the default number applied to it. /addchar defaults new Rarity-1 characters to this limit
# automatically unless the owner explicitly gives a different CatchLimit; see
# run_ultra_catch_limit_migration() near startup for backfilling characters added before this
# fix. Once an individual ULTRA character's own spawn_count reaches its own spawn_limit it:
#   1. stops appearing as a wild spawn for THAT character specifically (others are unaffected)
#      — see trigger_dynamic_spawn's generic spawn_limit filter, and
#   2. stops being purchasable via the Owner Shop /buy or the /show gallery's 🛒 button, for
#      THAT character specifically — see execute_star_shop_purchase's generic spawn_limit
#      check, which (also fixed here) now actually counts Shop purchases toward the same cap
#      instead of letting the Shop mint unlimited fresh copies past it.
# From that point on the only way to get a copy is /gift or /trade from an existing owner —
# see build_character_check_text for the "🔒 LOCKED" status /check shows once this is hit.
# ==========================================
ULTRA_DEFAULT_CATCH_LIMIT = 28

# 🚫 OWNER SHOP "SOLD OUT" TOGGLE — lets the Owner temporarily stop a rarity tier from being
# purchasable via /buy [char_id] and the /show gallery's 🛒 Buy button (catching it from a live
# spawn is completely unaffected). Starts with ULTRA (No.1) disabled by default; toggle any
# tier on/off with /buytoggle no1 .. no9 (persisted in bot_settings_col so it survives restarts).
_cached_disabled_buy_tiers = {"ULTRA"}
SOLD_OUT_CONTACT_LINK = "https://t.me/Comeback_BoD/1300786"  # where buyers can reply to reach
# the owner directly and negotiate/buy manually while a tier is closed in the Owner Shop

async def load_disabled_buy_tiers_cache():
    global _cached_disabled_buy_tiers
    try:
        doc = await bot_settings_col.find_one({"_id": "disabled_buy_tiers"})
        if doc is not None:
            tiers = doc.get("tiers")
            if isinstance(tiers, list):
                _cached_disabled_buy_tiers = {t for t in tiers if t in RARITY_TIERS}
    except Exception as e:
        print(f"load_disabled_buy_tiers_cache error: {e}")

# 🧠 TRIVIA AUTO-SPAWN ON/OFF — global toggle (owner: /triviaspawn on|off), persisted in
# bot_settings_col so it survives restarts. Defaults to ON.
_cached_trivia_enabled = True

async def load_trivia_settings_cache():
    global _cached_trivia_enabled
    try:
        doc = await bot_settings_col.find_one({"_id": "trivia_spawn_enabled"})
        if doc is not None and "enabled" in doc:
            _cached_trivia_enabled = bool(doc["enabled"])
    except Exception as e:
        print(f"load_trivia_settings_cache error: {e}")

def get_effective_rarity_weights():
    """Per-tier weight dict for random.choices() spawn selection: DEFAULT_RARITY_WEIGHTS, with
    Rarity 1-4 (RARITY_GATE_TIERS) multiplied by whatever level /spawnweight is currently set
    to. Rarity 5-9 always stay at their plain defaults."""
    multiplier = SPAWNWEIGHT_LEVEL_MULTIPLIERS.get(_cached_spawnweight_level, 1)
    weights = dict(DEFAULT_RARITY_WEIGHTS)
    for tier in RARITY_GATE_TIERS:
        weights[tier] = DEFAULT_RARITY_WEIGHTS[tier] * multiplier
    return weights

def build_progress_bar(current, total, length=10):
    pct = (current / total) if total > 0 else 0
    pct = max(0.0, min(pct, 1.0))
    filled = int(round(pct * length))
    return "█" * filled + "░" * (length - filled) + f" {pct * 100:.1f}%"

# ==========================================
# 🎮 HUD SMALL-CAPS CONVERTER — turns a normal 'Title Case' label into the small-caps look used
# for section headers on the redesigned /profile "Collector Database" HUD, e.g.
# sc("Collection log") -> "Cᴏʟʟᴇᴄᴛɪᴏɴ ʟᴏɢ". Only lowercase a-z map to a small-caps glyph — 's' and
# 'x' have no distinct small-caps codepoint in Unicode so they pass through as-is, and capitals,
# digits, emoji, and non-Latin script (e.g. Burmese) are all left untouched. Safe to call on any
# label; purely cosmetic, no effect on the underlying text/data.
# ==========================================
_SMALL_CAPS_MAP = str.maketrans(
    "abcdefghijklmnopqrstuvwxyz",
    "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ"
)
def sc(text):
    return text.translate(_SMALL_CAPS_MAP)

def letter_spaced(text):
    """'STARS' -> 'S T A R S' — the spaced-out label style used under HUD headers like
    /balance's wallet card. Purely cosmetic, safe on any short ASCII label."""
    return " ".join(text)

def utf16_len(s):
    """Telegram's message/caption length limits (4096 / 1024) are counted in UTF-16 code
    units, not Python characters. Fancy-font letters (used for rarity names) and many emoji
    live in the Unicode supplementary plane and take 2 UTF-16 units each (a surrogate pair),
    not 1 — plain len() undercounts those and can let a page slip past Telegram's real limit."""
    return len(s.encode('utf-16-le')) // 2

def generate_achievements():
    achievements = []
    # ... (all previous achievements remain, plus new ones)
    catch_targets = [1, 5, 10, 25, 50, 100, 200, 500, 1000, 2000, 5000]
    emojis = ["🎯", "📦", "🎒", "🧳", "🏅", "🏆", "👑", "💎", "🌌", "🌟", "💫"]
    for i, target in enumerate(catch_targets):
        achievements.append({
            "id": f"catch_{target}",
            "emoji": emojis[i % len(emojis)],
            "name": f"Catch {target} Characters",
            "desc": f"Capture {target} characters",
            "check": lambda u, t=target: u.get("total_caught", 0) >= t
        })
    # 🩹 2026-08 USD RETIREMENT: these used to be USD wallet_balance thresholds (old-MMK / MMK_PER_USD).
    # Re-tuned to ⭐ Star balance thresholds, proportional to the rest of the post-migration Star
    # economy (Owner Shop tops out at 2,500⭐-worth of VLT — see RARITY_VLT_PRICE / WEALTH_THRESHOLD)
    # — owner-adjustable, tune to taste.
    balance_targets = [10, 50, 250, 1000, 5000, 25000]
    balance_emojis = ["🪙", "💰", "⭐", "💳", "🏦", "💎"]
    for i, target in enumerate(balance_targets):
        achievements.append({
            "id": f"wealth_{target}",
            "emoji": balance_emojis[i],
            "name": f"Hold {target:,} Star",
            "desc": f"Amass {target:,}⭐ Star in your balance",
            "check": lambda u, t=target: u.get("star_balance", 0) >= t
        })
    streak_targets = [3, 7, 14, 30, 60, 100]
    streak_emojis = ["🔥", "☄️", "⚡", "🌞", "🌙", "⭐"]
    for i, target in enumerate(streak_targets):
        achievements.append({
            "id": f"streak_{target}",
            "emoji": streak_emojis[i],
            "name": f"{target}-Day Streak",
            "desc": f"Maintain a daily streak of {target} days",
            "check": lambda u, t=target: u.get("daily_streak", 0) >= t
        })
    ref_targets = [1, 5, 10, 25, 50]
    ref_emojis = ["🤝", "👥", "📢", "📣", "🌐"]
    for i, target in enumerate(ref_targets):
        achievements.append({
            "id": f"referral_{target}",
            "emoji": ref_emojis[i],
            "name": f"Invite {target} Friends",
            "desc": f"Get {target} friends to join via your referral link",
            "check": lambda u, t=target: u.get("referral_count", 0) >= t
        })
    leg_targets = [1, 5, 10, 50]
    leg_emojis = ["👑", "🌌", "💫", "🌟"]
    top_tier = RARITY_TIERS[0]
    for i, target in enumerate(leg_targets):
        achievements.append({
            "id": f"legendary_{target}",
            "emoji": leg_emojis[i],
            "name": f"Collect {target} {top_tier.title()} Cards",
            "desc": f"Own {target} {top_tier} rarity cards",
            "check": lambda u, t=target, tt=top_tier: sum(1 for item in u.get("harem", []) if isinstance(item, dict) and tt in (item.get("rarity", "").upper())) >= t
        })
    for target in [1, 10, 50]:
        achievements.append({
            "id": f"sell_{target}",
            "emoji": "🛒",
            "name": f"Sell {target} Cards on Market",
            "desc": f"Successfully sell {target} cards on the marketplace",
            "check": lambda u, t=target: u.get("total_sales", 0) >= t
        })
    for target in [1, 10, 50]:
        achievements.append({
            "id": f"trade_{target}",
            "emoji": "🤝",
            "name": f"Complete {target} Trades",
            "desc": f"Successfully trade {target} cards with other players",
            "check": lambda u, t=target: u.get("total_trades", 0) >= t
        })
    achievements.append({
        "id": "slot_jackpot", "emoji": "🎰", "name": "Slot Jackpot Winner",
        "desc": "Win a God‑Tier Jackpot (7 of a kind) on the slot machine",
        "check": lambda u: u.get("slot_jackpots", 0) >= 1
    })
    achievements.append({
        "id": "cardgame_win", "emoji": "🃏", "name": "Card Shark",
        "desc": "Win a multiplayer card game",
        "check": lambda u: u.get("cardgame_wins", 0) >= 1
    })
    achievements.append({
        "id": "gamble_win", "emoji": "💸", "name": "Lucky Gambler",
        "desc": "Win a gamble (double or nothing) 10 times",
        "check": lambda u: u.get("gamble_wins", 0) >= 10
    })
    for target in [1, 5, 10]:
        achievements.append({
            "id": f"guild_level_{target}",
            "emoji": "🏰",
            "name": f"Guild Level {target}",
            "desc": f"Reach Guild Level {target}",
            "check": lambda u, t=target: u.get("guild_level", 0) >= t
        })
    achievements.append({
        "id": "first_blood", "emoji": "🎯", "name": "First Blood",
        "desc": "Every legend starts somewhere. Catch your very first character.",
        "check": lambda u: u.get("total_caught", 0) >= 1
    })
    achievements.append({
        "id": "completionist", "emoji": "🌈", "name": "Completionist",
        "desc": f"Collect at least one of every rarity ({RARITY_TIERS[-1].title()} to {RARITY_TIERS[0].title()})",
        "check": lambda u: all(any(classify_rarity(item.get("rarity", "")) == tier for item in u.get("harem", []) if isinstance(item, dict)) for tier in RARITY_TIERS)
    })
    # New quiz achievement
    achievements.append({
        "id": "quiz_master",
        "emoji": "🧠",
        "name": "Quiz Master",
        "desc": "Correctly answer a Rarity 1 quiz question",
        "check": lambda u: u.get("quiz_correct", 0) >= 1
    })
    quiz_targets = [3, 5, 10, 20, 50]
    quiz_emojis = ["🐟", "🧩", "🎓", "🏅", "👑"]
    for i, target in enumerate(quiz_targets):
        achievements.append({
            "id": f"quiz_correct_{target}",
            "emoji": quiz_emojis[i % len(quiz_emojis)],
            "name": f"Rescue Quiz x{target}",
            "desc": f"Correctly answer {target} Rarity 1 Rescue Quiz questions",
            "check": lambda u, t=target: u.get("quiz_correct", 0) >= t
        })
    extra_milestones = [15, 20, 30, 40, 60, 70, 80, 90, 120, 150, 180, 210, 250, 300, 350, 400, 450, 600, 700, 800, 900, 1200, 1500, 2000, 2500, 3000, 4000, 5000]
    for target in extra_milestones:
        if not any(a["id"] == f"catch_{target}" for a in achievements):
            achievements.append({
                "id": f"catch_{target}",
                "emoji": "🃏",
                "name": f"Catch {target} Characters",
                "desc": f"Capture {target} characters",
                "check": lambda u, t=target: u.get("total_caught", 0) >= t
            })
    return achievements[:300]

ACHIEVEMENTS = generate_achievements()
ACHIEVEMENTS_BY_ID = {a["id"]: a for a in ACHIEVEMENTS}

async def check_and_award_achievements(user_id, notify_chat_id=None):
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    if not user_doc:
        return []
    earned_ids = set(user_doc.get("achievements", []))
    newly_earned = []
    for ach in ACHIEVEMENTS:
        if ach["id"] in earned_ids:
            continue
        try:
            if ach["check"](user_doc):
                newly_earned.append(ach)
        except Exception:
            continue
    if not newly_earned:
        return []
    await users_catcher_col.update_one(
        {"user_id": user_id},
        {"$addToSet": {"achievements": {"$each": [a["id"] for a in newly_earned]}}}
    )
    if notify_chat_id:
        for ach in newly_earned:
            try:
                await send_safe_message(
                    bot1, notify_chat_id,
                    f"🏅 <b>ACHIEVEMENT UNLOCKED!</b>\n{ach['emoji']} <b>{ach['name']}</b>\n<i>{ach['desc']}</i>",
                    parse_mode='html'
                )
            except Exception:
                pass
    return newly_earned

def format_achievement_unlocks(newly_earned):
    if not newly_earned:
        return ""
    lines = "\n".join(f"{a['emoji']} <b>{a['name']}</b>" for a in newly_earned)
    return f"\n\n🏅 <b>ACHIEVEMENT UNLOCKED!</b>\n{lines}"

# ==========================================
# SMART TEXT NORMALIZER
# ==========================================
def normalize_name(text):
    if not text: return ""
    text = unicodedata.normalize('NFKC', text).lower().strip()
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

# ==========================================
# FLASK KEEP-ALIVE
# ==========================================
app = Flask('')
@app.route('/')
def home(): return "Bot Control System is Active!"

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.ERROR)

# ==========================================
# ENVIRONMENT & DATABASE
# ==========================================
def _require_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"❌ Missing required environment variable: {name}.")
    return value

OWNER_ID = int(_require_env("OWNER_ID"))
MONGO_URI = _require_env("MONGO_URI")
APP_ID = int(_require_env("APP_ID"))
APP_HASH = _require_env("APP_HASH")
MAIN_BOT_TOKEN = _require_env("MAIN_BOT_TOKEN")
# 🔐 OWNER BOT — a second, separate bot token used ONLY for owner-only commands
# (/addchar, /ktr, /ktrr, /shadow, /unshadow). Keeping these on a distinct bot
# account means the sensitive control commands never share a token with the
# public-facing game bot.
OWNER_BOT_TOKEN = _require_env("OWNER_BOT_TOKEN")
# 🛡️ GUARD BOT — a third, separate bot dedicated to the force-join group (see bot3 below).
# Optional: leave GUARD_BOT_TOKEN unset and the guard bot simply doesn't start — bot1 + bot2
# keep running exactly as before. Get a token from @BotFather, add the bot as admin (with
# "Delete Messages" permission) in the force-join group, then set this env var to enable it.
GUARD_BOT_TOKEN = os.environ.get("GUARD_BOT_TOKEN")
SPECIFIC_CONTROL_GROUP = int(_require_env("SPECIFIC_CONTROL_GROUP"))
SPECIFIC_GROUP = int(_require_env("SPECIFIC_GROUP"))
# Optional: a channel where every /addchar'd character gets auto-posted with full details.
# Not required — if unset, /addchar simply skips the channel post and says so.
# 🩹 CHANGED (per owner request): new channel is https://t.me/Character_Collocter
# (id 3997871300 → -1003997871300 in Bot API/MTProto form). This is only a FALLBACK default —
# if the CHARACTER_CHANNEL_ID env var is set on Render, that value wins. ⚠️ IMPORTANT: update
# the Render env var to -1003997871300 directly — don't rely on this fallback alone, since an
# old env var pointing at the previous channel would silently override it.
_character_channel_env = os.environ.get("CHARACTER_CHANNEL_ID")
CHARACTER_CHANNEL_ID = int(_character_channel_env) if _character_channel_env else -1003997871300
TZ = pytz.timezone('Asia/Yangon')
STORAGE_CHANNEL = SPECIFIC_CONTROL_GROUP
GLOBAL_SPAWN_CHAT_KEY = "global"  # groups_config_col doc key used for the default/global spawn_target

# MongoDB with connection pool
client_mongo = AsyncIOMotorClient(MONGO_URI, maxPoolSize=10)
db = client_mongo["telegram_bot"]
allow_col = db["allowed_users"]
groups_col = db["active_groups"]
talk_col = db["random_talk"]
ans_col = db["chatbot_answers"]
morgan_col = db["morgan_talk"]
system_col = db["system_col"]
reply_save_col = db["reply_save_col"]
users_col = db["users"]
user_game_profiles_col = db["user_game_profiles"]
characters_col = db["characters"]
muted_registry_col = db["muted_registry"]
characters_base_col = db["characters_base_data"]
users_catcher_col = db["users_catcher_data"]
groups_counters_col = db["groups_msg_counters"]
groups_config_col = db["groups_catcher_config"]
marketplace_col = db["marketplace_data"]
guilds_col = db["guilds_data"]
# 🏰 SQUAD SYSTEM — deliberately its OWN collection, not guilds_col. guilds_col already has an
# older, never-actually-wired-up "Guild XP/Level" mechanic (see the guild_levelup_msg block in
# catch_handler) that assumes every doc there has "xp"/"level" fields. Squad docs don't have
# those, and nothing currently ever inserts into guilds_col — so reusing it here would leave a
# latent KeyError crash sitting in every future catch for anyone in a Squad. Keeping Squad data
# in its own collection sidesteps that entirely without needing to touch the old dormant code.
squads_col = db["squads_data"]
gift_history_col = db["gift_history"]
haido_history_col = db["haido_history"] # records each person-to-person /gift for profile stats
artists_col = db["artists"]  # 🎨 artist_name (lowercased) -> linked Telegram user_id, for Guard Bot collect rewards — see /linkartist
# 🎗️ LE_BATCHES_COL (per owner request — LE fragmentation fix, 2026-08): {"batch_number": int,
# "le_name": str}, one doc per Limited Edition batch. Before this, /addle's le_name was
# free-text retyped from scratch every single time — "Epic Moment 1" one day and "Epic Moment1"
# the next silently created two separate, fragmented batches instead of adding to the same one.
# batch_number is a small, permanent, typo-proof handle the owner can reference instead
# (/addle 3 CharID1 CharID2 ...) — assigned once per le_name and never reused, even if that
# batch later gets merged away (see /mergele). Deliberately its OWN collection rather than a
# field on characters_base_col: a batch's number must survive independently of which — or how
# many — characters currently carry that le_name, including the brief moment mid-/mergele where
# characters are being re-tagged from two old names to one new one.
le_batches_col = db["le_batches"]
gban_col = db["global_bans"]  # 🚫 ban records (reason, duration, confiscated amounts) — see /gban
wealth_compression_log_col = db["wealth_compression_log"] # 🐋 audit trail — one doc per /compresswealth confirm run
force_sub_reclaim_log_col = db["force_sub_reclaim_log"] # 🧾 audit trail — one doc per /reclaimforcesub confirm run
rarity_quiz_bank_col = db["rarity_quiz_bank"] # 🔐 owner-authored Rarity 1-4 gate quiz questions
trivia_bank_col = db["trivia_question_bank"] # 🧠 owner-bulk-added Trivia questions, one doc per
# question: {question, options[2], correct_index, difficulty, created_by, created_at}. Merged
# with the static TRIVIA_QUESTION_BANK[difficulty] fallback list at spawn time — see
# get_trivia_pool() and /addtriviabulk further down.
bot_settings_col = db["bot_settings"] # ⚙️ single-document global settings
# 🩹 2026-08 USD RETIREMENT: star_market_col / star_exchange_log_col below are now LEGACY —
# USD no longer exists in this bot (see USD → STAR RETIREMENT block near MMK_PER_USD), so
# there is nothing left for Star to exchange against here. Kept only so /starlist can still
# render historical pre-retirement rows; /buystar & /sellstar are retired. 💠 vlt_market_col
# is the live exchange doc now — same shape, but tracks the Star <-> VLT rate instead.
star_market_col = db["star_market"] # ⭐ [LEGACY] single global Star <-> USD exchange rate doc
vlt_market_col = db["vlt_market"] # 💠 single global Star <-> VLT exchange rate doc (5-7⭐ per 💠, drifts)
vlt_exchange_log_col = db["vlt_exchange_log"] # 🧾 every /buyvlt & /sellvlt Star<->VLT exchange, for /vltlist
# 🏦 bot3's own "house" ledger — single global doc (key="bot3"), seeded once with a large
# starting bankroll and then updated in lockstep with every bet/refund/fee/payout across every
# bot3-hosted casino game (see bot3_treasury_adjust below). Lets anyone check /bot3balance to
# see exactly how much bot3 has won or lost overall, instead of that being uncountable.
bot3_treasury_col = db["bot3_treasury"]
star_purchase_log_col = db["star_purchase_log"] # 🧾 every /buy [char id] Owner Shop purchase, for /buylist
star_sell_log_col = db["star_sell_log"] # 🧾 every /sell [char id] Owner buyback (Star paid to the player), for /starlist
star_exchange_log_col = db["star_exchange_log"] # 🧾 every /buystar & /sellstar USD<->Star exchange, for /starlist
debts_col = db["debts"]  # 💰 အကြွေးစာရင်း
# Redis caching with connection pool
# NOTE: previously hardcoded to host='localhost' — if Redis runs anywhere other
# than the same machine as the bot (managed Redis, separate container, Redis
# Cloud/Upstash, etc.) that connection can never succeed. Set REDIS_URL in the
# environment (e.g. redis://user:pass@host:port/0 or rediss://... for TLS) to
# point at the real instance. Falls back to localhost for local/dev setups.
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True, max_connections=10)

async def get_cached_user(user_id):
    try:
        cached = await redis_client.get(f"user:{user_id}")
        if cached:
            return json.loads(cached)
    except Exception as e:
        print(f"⚠️ Redis get_cached_user error: {e}")
    user = await users_catcher_col.find_one({"user_id": user_id}, {"_id": 0})  # exclude ObjectId (not JSON serializable)
    if user:
        try:
            await redis_client.setex(f"user:{user_id}", 300, json.dumps(user, default=str))
        except Exception as e:
            print(f"⚠️ Redis get_cached_user write error: {e}")
    return user

# ---- Character-data cache: characters_base_data barely changes (only via /addchar, /editchar,
# /delchar) but is read on almost every group message (spawn trigger) and every /dex lookup.
# TTL is short on purpose: a newly-hit spawn_limit may stay "eligible" for a few seconds longer
# than it should, which is a harmless, rare edge case compared to hitting Mongo on every message.
CHAR_CACHE_TTL = 300  # 5 minutes — roster only changes on /addchar,/editchar,/delchar (which invalidate this explicitly anyway)

async def get_all_characters_cached():
    try:
        cached = await redis_client.get("cache:chars:all")
        if cached:
            return json.loads(cached)
    except Exception as e:
        print(f"⚠️ Redis get_all_characters_cached error: {e}")
    data = await characters_base_col.find({}, {"_id": 0}).to_list(length=None)
    try:
        await redis_client.setex("cache:chars:all", CHAR_CACHE_TTL, json.dumps(data, default=str))
    except Exception as e:
        print(f"char cache write error: {e}")
    return data

async def get_all_categories_cached():
    try:
        cached = await redis_client.get("cache:chars:categories")
        if cached:
            return json.loads(cached)
    except Exception as e:
        print(f"⚠️ Redis get_all_categories_cached error: {e}")
    data = await characters_base_col.distinct("category")
    try:
        await redis_client.setex("cache:chars:categories", CHAR_CACHE_TTL, json.dumps(data, default=str))
    except Exception as e:
        print(f"category cache write error: {e}")
    return data

async def get_category_totals_cached():
    """{category: total_card_count} across the whole roster — used by /harem to show
    'owned/total' per series. Only changes on /addchar, /editchar, /delchar, so it's
    cached the same way as the other character-roster lookups above."""
    try:
        cached = await redis_client.get("cache:chars:category_totals")
        if cached:
            return json.loads(cached)
    except Exception as e:
        print(f"⚠️ Redis get_category_totals_cached error: {e}")
    pipeline = [{"$group": {"_id": "$category", "count": {"$sum": 1}}}]
    data = {(doc["_id"] or "Unknown Series"): doc["count"] for doc in await characters_base_col.aggregate(pipeline).to_list(length=None)}
    try:
        await redis_client.setex("cache:chars:category_totals", CHAR_CACHE_TTL, json.dumps(data))
    except Exception as e:
        print(f"category totals cache write error: {e}")
    return data

# ---- Character display-photo cache ----
# Every place that shows a character's photo (spawns, /check, /who, /harem, gallery, gift
# confirms, etc.) currently does its own fresh client.get_messages(SPECIFIC_CONTROL_GROUP,
# ids=storage_msg_id) call — a live Telegram API round trip, every single time, for media
# that essentially never changes. Under concurrent traffic from many groups at once, this is
# the same bot account repeatedly hitting the same handful of messages in the SAME storage
# chat — exactly the kind of pattern that can trip Telegram's own flood-wait limits for this
# bot account, which would slow things down for every group at once, not just one.
# In-memory (not Redis) on purpose: Telethon's message/media objects aren't JSON-safe, and
# this process is a single Render instance anyway, so a plain dict is the simplest cache that
# actually fits the architecture.
_CHAR_PHOTO_CACHE = {}  # char_id -> (media_object, cached_at)
CHAR_PHOTO_CACHE_TTL = 3600  # 1 hour — cleared early anyway by invalidate_character_caches()

async def get_char_display_media(client, char_id, storage_msg_id):
    """Cached wrapper around client.get_messages(SPECIFIC_CONTROL_GROUP, ids=storage_msg_id)
    for a character's stored photo. Returns the media object, or None if it can't be fetched."""
    cached = _CHAR_PHOTO_CACHE.get(char_id)
    if cached and (time.time() - cached[1]) < CHAR_PHOTO_CACHE_TTL:
        return cached[0]
    media = None
    try:
        storage_msg = await client.get_messages(SPECIFIC_CONTROL_GROUP, ids=storage_msg_id)
        if storage_msg and storage_msg.media:
            media = storage_msg.media
    except Exception as e:
        print(f"⚠️ get_char_display_media fetch error for {char_id}: {e}")
    _CHAR_PHOTO_CACHE[char_id] = (media, time.time())
    return media

INLINE_GALLERY_MEDIA_MAX_AGE = 60  # seconds — see get_char_display_media_batch's own docstring
# for why this is much shorter than CHAR_PHOTO_CACHE_TTL (1hr), not the same value.

async def get_char_display_media_batch(client, cards):
    """Batched sibling of get_char_display_media, for a whole page of cards at once (the
    inline galleries need many cards' media in a single inline-query response, and Telegram's
    ~10s inline-query timeout means these can't afford one round trip per card). Returns
    {char_id: media_or_None}.

    🩹 FIX (owner report — "media won't open in the harem/LE inline gallery anymore"): a
    Telegram file_reference is only valid for a limited window after it was fetched (see
    send_with_char_media's own docstring below) — but THIS function's caller has no way to
    retry on expiry the way send_with_char_media does, because an inline query's media is
    baked into the answer at query time, and the actual failure only happens later, silently,
    whenever the USER selects that result — by which point our code isn't in the loop at all.
    This used to trust the SAME 1-hour cache (CHAR_PHOTO_CACHE_TTL) get_char_display_media
    uses for regular sends, which is fine for THAT path (it sends immediately, and retries if
    the reference has gone stale) but far too generous here, where staleness has no recovery
    path. The owner's "unlimited vault" felt this hardest: browsing the WHOLE roster runs long
    enough that early pages' cached media routinely went stale before anything got selected.
    Only an entry under INLINE_GALLERY_MEDIA_MAX_AGE (60s, not 3600s) is trusted now — anything
    older gets a fresh fetch, same as a full cache miss. Still writes through to the shared
    _CHAR_PHOTO_CACHE afterward, so get_char_display_media's own (longer, safe-for-its-use-case)
    cache stays warm for regular sends too."""
    now = time.time()
    result = {}
    missing_cards = []
    for card in cards:
        cid = card["char_id"]
        cached = _CHAR_PHOTO_CACHE.get(cid)
        if cached and (now - cached[1]) < INLINE_GALLERY_MEDIA_MAX_AGE:
            result[cid] = cached[0]
        else:
            missing_cards.append(card)
    if missing_cards:
        # 🩹 FIX (owner report — "owner's unlimited vault / LE inline gallery shows NOTHING at
        # all"): this used to read card["storage_msg_id"] with bare bracket access — any ONE
        # card with no media ever uploaded for it (the field is just absent from that Mongo
        # doc, not None) threw a bare KeyError building this list, which the caller's
        # try/except caught by discarding the ENTIRE batch's results, not just that one card.
        # Every other card on the page — media and all — got wiped out along with it, so a
        # single incomplete character was enough to make a whole gallery page answer empty.
        # The owner's unlimited vault (the whole roster, not just one person's curated harem)
        # and the LE gallery were both far more likely to contain at least one such card than
        # an ordinary player's harem, which is why those two specifically looked totally
        # broken. .get() + filtering out the None-id cards up front contains the damage to
        # just the affected card/s, same as any other card with genuinely missing media.
        valid_missing = [c for c in missing_cards if c.get("storage_msg_id")]
        skipped_ids = [c["char_id"] for c in missing_cards if not c.get("storage_msg_id")]
        for cid in skipped_ids:
            result[cid] = None
        if valid_missing:
            missing_ids = [c["storage_msg_id"] for c in valid_missing]
            try:
                fetched = await client.get_messages(SPECIFIC_CONTROL_GROUP, ids=missing_ids)
            except Exception as e:
                print(f"⚠️ get_char_display_media_batch fetch error: {e}")
                fetched = [None] * len(valid_missing)
            for card, msg in zip(valid_missing, fetched):
                cid = card["char_id"]
                media = msg.media if (msg and msg.media) else None
                _CHAR_PHOTO_CACHE[cid] = (media, now)
                result[cid] = media
    return result

async def send_with_char_media(char_id, storage_msg_id, send_func):
    """THE FIX for the file_reference bug: runs send_func(media) using this character's
    cached photo. A Telegram file_reference (the part of a media object that actually lets
    you attach it to a BRAND NEW message) is only valid for a limited window after it was
    obtained — reusing the SAME cached media object to send new messages later (which is
    exactly what caching is for) will eventually raise errors.FileReferenceExpiredError once
    enough time has passed since that reference was fetched. This is almost certainly what
    caused spawns/collect/haido to start failing after the caching change shipped: the first
    send after caching works fine (fresh reference), but every later reuse of that same
    cached entry carries a reference that's progressively more likely to have gone stale —
    which is also why it got WORSE over time and eventually spawns stopped entirely, instead
    of failing consistently from the start.
    On that specific error, this invalidates just the one cache entry, fetches a genuinely
    fresh reference, and retries send_func ONCE more. Any other exception (or a second
    failure) propagates normally to the caller's own try/except, unchanged from before."""
    media = await get_char_display_media(bot1, char_id, storage_msg_id)
    if media is None:
        return None
    try:
        return await send_func(media)
    except errors.FileReferenceExpiredError:
        _CHAR_PHOTO_CACHE.pop(char_id, None)
        fresh_media = await get_char_display_media(bot1, char_id, storage_msg_id)
        if fresh_media is None:
            raise
        print(f"♻️ Refreshed stale file_reference for {char_id} and retried send.")
        return await send_func(fresh_media)

async def invalidate_character_caches():
    """Call this right after /addchar, /editchar, or /delchar successfully changes the roster."""
    _CHAR_PHOTO_CACHE.clear()  # cheap in-process clear — cheaper to wipe it all than to track exactly which char_id(s) changed
    try:
        await redis_client.delete("cache:chars:all", "cache:chars:categories", "cache:chars:category_totals")
    except Exception as e:
        print(f"char cache invalidate error: {e}")

# ---- Rarity-gate quiz illustration cache ----
# Same idea as _CHAR_PHOTO_CACHE, separate small dict since this is keyed by
# question_media_msg_id (a quiz's own optional flavor image, set via /addquiz) rather than a
# char_id — this fires every time a Rarity 1-4 spawn gets quiz-gated, across every group, so
# it's a hot path too. No explicit invalidation hook (quiz illustrations are essentially
# never replaced after creation) — the TTL alone is enough here.
_QUIZ_MEDIA_CACHE = {}  # question_media_msg_id -> (media_object, cached_at)

async def get_quiz_question_media(client, question_media_msg_id):
    cached = _QUIZ_MEDIA_CACHE.get(question_media_msg_id)
    if cached and (time.time() - cached[1]) < CHAR_PHOTO_CACHE_TTL:
        return cached[0]
    media = None
    try:
        q_storage_msg = await client.get_messages(SPECIFIC_CONTROL_GROUP, ids=question_media_msg_id)
        if q_storage_msg and q_storage_msg.media:
            media = q_storage_msg.media
    except Exception:
        pass
    _QUIZ_MEDIA_CACHE[question_media_msg_id] = (media, time.time())
    return media


async def get_or_create_star_market():
    """Lazily creates the single global ⭐ Star <-> USD exchange rate doc on first touch, and
    rolls its 'day open' price forward once a new Yangon calendar day begins."""
    market = await star_market_col.find_one({"key": "STAR"})
    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    if not market:
        market = {
            "key": "STAR", "price": STAR_STARTING_PRICE, "day_open_price": STAR_STARTING_PRICE,
            "day_open_date": today_str, "prev_tick_price": STAR_STARTING_PRICE,
            "history": [STAR_STARTING_PRICE], "buy_volume_today": 0.0, "sell_volume_today": 0.0,
            "updated_at": time.time(),
        }
        try:
            await star_market_col.insert_one(dict(market))
        except Exception:
            refetched = await star_market_col.find_one({"key": "STAR"})
            if refetched:
                market = refetched
    if market.get("day_open_date") != today_str:
        await star_market_col.update_one(
            {"key": "STAR"},
            {"$set": {"day_open_price": market["price"], "day_open_date": today_str,
                      "buy_volume_today": 0.0, "sell_volume_today": 0.0}}
        )
        market["day_open_price"] = market["price"]
        market["day_open_date"] = today_str
    return market

async def _write_star_price(market, new_price, extra_fields=None):
    new_price = round(max(new_price, STAR_MIN_PRICE), 2)
    history = (market.get("history", []))[-(STAR_HISTORY_MAX - 1):] + [new_price]
    fields = {"prev_tick_price": market["price"], "price": new_price,
               "history": history, "updated_at": time.time()}
    if extra_fields:
        fields.update(extra_fields)
    await star_market_col.update_one({"key": "STAR"}, {"$set": fields})
    return new_price

# ==========================================
# 💠 VLT MARKET — live Star <-> VLT exchange rate (2026-08, replaces Star's old USD exchange)
# ==========================================
async def get_or_create_vlt_market():
    """Lazily creates the single global 💠 VLT <-> ⭐ Star exchange rate doc on first touch, and
    rolls its 'day open' rate forward once a new Yangon calendar day begins. Same shape as the
    legacy get_or_create_star_market() above, just for VLT — see vlt_drift_loop() for how the
    rate actually moves on its own between VLT_RATE_MIN and VLT_RATE_MAX."""
    market = await vlt_market_col.find_one({"key": "VLT"})
    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    if not market:
        market = {
            "key": "VLT", "rate": VLT_STARTING_RATE, "day_open_rate": VLT_STARTING_RATE,
            "day_open_date": today_str, "prev_tick_rate": VLT_STARTING_RATE,
            "history": [VLT_STARTING_RATE], "buy_volume_today": 0.0, "sell_volume_today": 0.0,
            "updated_at": time.time(),
        }
        try:
            await vlt_market_col.insert_one(dict(market))
        except Exception:
            refetched = await vlt_market_col.find_one({"key": "VLT"})
            if refetched:
                market = refetched
    if market.get("day_open_date") != today_str:
        await vlt_market_col.update_one(
            {"key": "VLT"},
            {"$set": {"day_open_rate": market["rate"], "day_open_date": today_str,
                      "buy_volume_today": 0.0, "sell_volume_today": 0.0}}
        )
        market["day_open_rate"] = market["rate"]
        market["day_open_date"] = today_str
    return market

async def _write_vlt_rate(market, new_rate, extra_fields=None):
    new_rate = round(min(max(new_rate, VLT_RATE_MIN), VLT_RATE_MAX), 2)
    history = (market.get("history", []))[-(VLT_HISTORY_MAX - 1):] + [new_rate]
    fields = {"prev_tick_rate": market["rate"], "rate": new_rate,
               "history": history, "updated_at": time.time()}
    if extra_fields:
        fields.update(extra_fields)
    await vlt_market_col.update_one({"key": "VLT"}, {"$set": fields})
    return new_rate

async def vlt_drift_loop():
    """Background task: every VLT_DRIFT_INTERVAL_SECONDS, nudges the Star<->VLT rate by a
    small random amount (±VLT_DRIFT_STEP_MAX), clamped to [VLT_RATE_MIN, VLT_RATE_MAX] by
    _write_vlt_rate(). This is what gives VLT the "စျေးအတက်အကျရှိ" (price goes up and down)
    behaviour the owner asked for — unlike Star's old fixed USD peg, nobody has to run a
    command for this rate to move. Runs forever once started from run_bot1_forever()."""
    while True:
        try:
            await asyncio.sleep(VLT_DRIFT_INTERVAL_SECONDS)
            market = await get_or_create_vlt_market()
            step = random.uniform(-VLT_DRIFT_STEP_MAX, VLT_DRIFT_STEP_MAX)
            await _write_vlt_rate(market, market["rate"] + step)
        except Exception as e:
            logging.error(f"❌ vlt_drift_loop tick failed: {e}")

async def get_or_create_bot3_treasury():
    """Lazily creates the single global bot3 'house' ledger doc on first touch, seeded with
    BOT3_SEED_STAR_BALANCE. The seed value is also frozen onto the doc (seed_star_balance) so
    /bot3balance can always show original vs current and compute a real profit/loss, no matter
    how much star_balance has since moved.
    🩹 2026-08 USD RETIREMENT: bot3's treasury used to also carry a wallet_balance (USD) half,
    seeded from BOT3_SEED_WALLET_BALANCE. That's retired along with USD — the treasury is
    Star-only now. See bot3_treasury_adjust()."""
    treasury = await bot3_treasury_col.find_one({"key": "bot3"})
    if not treasury:
        treasury = {
            "key": "bot3",
            "star_balance": BOT3_SEED_STAR_BALANCE,
            "seed_star_balance": BOT3_SEED_STAR_BALANCE,
            "created_at": time.time(),
        }
        try:
            await bot3_treasury_col.insert_one(dict(treasury))
        except Exception:
            refetched = await bot3_treasury_col.find_one({"key": "bot3"})
            if refetched:
                treasury = refetched
    return treasury

async def bot3_treasury_adjust(usd=0, star=0):
    """The single choke point every bot3 casino game's money movement passes through, so the
    bot3_treasury_col doc stays a real-time, exact mirror of bot3's own win/loss — separate
    from and in ADDITION to the player's own star_balance change.
    Sign convention: positive = bot3 GAINS (a player's bet, burn fee, or cashout tax being
    taken); negative = bot3 PAYS OUT (a player's win, or a refund reversing an earlier bet).
    Call this immediately alongside — never instead of — the normal users_catcher_col update,
    with the exact opposite of whatever amount just moved into/out of the player's balance.
    🩹 2026-08 USD RETIREMENT: bot3's treasury is Star-only now (star_balance/USD is gone).
    The `usd` kwarg is kept ONLY so the ~65 existing call sites across every casino game don't
    all need editing — it now adds to the SAME star_balance bucket as `star`, not a separate
    USD one. New call sites should just use `star=`.
    🩹 CHANGED (per owner request — Star/VLT inflation fix): until now this doc was tracking-
    only — fee/burn/lost-bet money taken from players was a real sink (destroyed, going
    nowhere spendable), which was correct for inflation but meant bot3's own "profit" was
    fictional, just a number in a ledger nobody could actually spend. Every adjustment here now
    ALSO applies 1:1 to OWNER_ID's own real ⭐ Star balance — the owner IS bot3's bankroll now,
    gaining for real on every burn/lost-bet/cashout-tax, paying out for real on every player
    win, exactly mirroring whatever just happened to this tracking doc. See /claimtreasury for
    the one-time migration of everything bot3 had already accumulated BEFORE this change."""
    total = usd + star
    if not total:
        return
    await get_or_create_bot3_treasury()  # ensure the doc exists before the very first $inc
    try:
        await bot3_treasury_col.update_one(
            {"key": "bot3"},
            {"$inc": {"star_balance": total}},
            upsert=True
        )
    except Exception as e:
        logging.error(f"❌ bot3_treasury_adjust failed (usd={usd}, star={star}): {e}")
    try:
        await users_catcher_col.update_one(
            {"user_id": OWNER_ID},
            {"$inc": {"star_balance": total}},
            upsert=True
        )
    except Exception as e:
        logging.error(f"❌ bot3_treasury_adjust owner-mirror failed (total={total}): {e}")

# ==========================================
# 🚨 AML "BUST" — comedic flavor event AND bot3's real max-single-bet ceiling
# ==========================================
# Root cause of the currency-inflation problem this was built to close off: none of bot3's
# games ever capped how large a single bet could be. With payout multipliers well into double
# digits and NO ceiling, a player only ever needs a handful of lucky high-multiplier wins in a
# row — each one multiplying their ENTIRE current balance — to snowball into an astronomical,
# uncountable number. That's exactly the pattern of the /richest leaderboard: each rank a huge
# multiple of the next, not a gradual accumulation.
# AML_BUST_THRESHOLD closes that off directly: no bet can ever be placed at or above it, full
# stop — dressed up as a satirical "money-laundering investigation" instead of a flat error, per
# request. It is flavor text only; nothing here is a real restriction on the user's account
# outside the game layer.
# 🩹 2026-08 USD RETIREMENT: was 1,000,000 USD, divided down by USD_PER_STAR (100,000) to stay
# proportional to the post-migration ⭐ Star economy — but that division landed on exactly 10⭐,
# which is BELOW every game's own bet presets even at the time (/slot alone went up to 500⭐).
# 🩹 FIX (2026-08, per owner request to raise /slot's max bet to 50,000⭐): now a flat ⭐ value
# set with headroom above SLOT_MAX_BET, so it's back to being the emergency backstop it was
# meant to be, not something that silently blocks every normal max-size bet with a comedic
# "AML bust" instead of a real answer.
USD_PER_STAR = 100_000  # 💵 100,000 USD = 1⭐ Star — the ONE-TIME wallet migration rate (owner-set, Aug 2026)
AML_BUST_THRESHOLD = 60_000    # ⭐ the hard ceiling on any single bot3 bet — headroom above SLOT_MAX_BET (50,000)
AML_JAIL_SECONDS = 600            # 10 min "under investigation" — bot3 games refuse to start

async def check_aml_jail(event, user_id):
    """Returns True (caller must abort, bet NOT taken) if this user is still serving out a
    previous AML 'bust'. Call this before even asking for/accepting a bet amount."""
    until = aml_jail.get(user_id)
    if not until:
        return False
    remaining = int(until - time.time())
    if remaining <= 0:
        aml_jail.pop(user_id, None)
        return False
    mention = await get_html_mention(event, user_id)
    await _out(
        event,
        f"🚨 <b>{mention} — လက်ရှိ ငွေကြေးခဝါချမှု စုံစမ်းစစ်ဆေးမှု အောက်ရောက်နေပါတယ်။</b>\n"
        f"⏳ <code>{remaining}</code> စက္ကန့်အကြာမှ ဂိမ်းများ ပြန်ကစားနိုင်ပါမယ်။\n"
        f"<i>😂 (Simulation ပါ — တကယ့်အရေးယူမှု မဟုတ်ပါ)</i>",
        parse_mode='html'
    )
    return True

async def try_deduct_bet_bot3(event, user_id, bet):
    """Drop-in replacement for try_deduct_balance(user_id, bet), used specifically for the
    INITIAL stake on every bot3 casino game. Same True/False contract, but first runs the AML
    jail + bust checks above — so this is also where AML_BUST_THRESHOLD is actually enforced
    as a real ceiling, not just flavor text."""
    if await check_aml_jail(event, user_id):
        return False
    if bet >= AML_BUST_THRESHOLD:
        aml_jail[user_id] = time.time() + AML_JAIL_SECONDS
        mention = await get_html_mention(event, user_id)
        await _out(
            event,
            f"🚨🚔 <b>ငွေကြေးခဝါချမှု စုံစမ်းရေးအဖွဲ့</b> 🚔🚨\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{mention} — <code>{format_star_plain(bet)}</code> ဆိုတဲ့ လောင်းကြေးက "
            f"သံသယဖြစ်ဖွယ် ငွေလွှဲပြောင်းမှုအဖြစ် တွေ့ရှိရပါတယ်။ 🕵️‍♂️\n\n"
            f"🔒 <b>Account ကို {AML_JAIL_SECONDS // 60} မိနစ် ယာယီ စစ်ဆေးမှု ခံရပါတော့မယ်</b>\n"
            f"<i>😂 (Simulation ပါ — တကယ့်အရေးယူမှု မဟုတ်ပါ)</i>",
            parse_mode='html'
        )
        return False
    return await try_deduct_balance(user_id, bet)


async def _dedupe_groups_config():
    pipeline = [
        {"$group": {"_id": "$chat_id", "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": 1}}}
    ]
    dupes = await groups_config_col.aggregate(pipeline).to_list(length=None)
    for group in dupes:
        docs = await groups_config_col.find({"chat_id": group["_id"]}).sort("_id", 1).to_list(length=None)
        merged = {}
        for doc in docs:
            for k, v in doc.items():
                if k == "_id":
                    continue
                merged[k] = v
        keep_id = docs[-1]["_id"]
        await groups_config_col.update_one({"_id": keep_id}, {"$set": merged})
        remove_ids = [d["_id"] for d in docs if d["_id"] != keep_id]
        if remove_ids:
            await groups_config_col.delete_many({"_id": {"$in": remove_ids}})

async def _migrate_rarity_tiers():
    """Backfill the rarity_tier field for characters saved before this field existed,
    so rarity-based lookups (quiz rewards, hmode filter, dex breakdown) can use an index
    instead of scanning + re-classifying every document every time."""
    untagged = await characters_base_col.find({"rarity_tier": {"$exists": False}}, {"_id": 1, "rarity": 1}).to_list(length=None)
    if not untagged:
        return
    ops = [UpdateOne({"_id": doc["_id"]}, {"$set": {"rarity_tier": classify_rarity(doc.get("rarity", ""))}}) for doc in untagged]
    if ops:
        await characters_base_col.bulk_write(ops)
        print(f"🏷️ Backfilled rarity_tier for {len(ops)} characters.")

async def create_indexes():
    print("⚡ Creating Database Indexes...")
    await users_col.create_index("user_id", unique=True)
    await users_catcher_col.create_index("user_id", unique=True)
    await groups_col.create_index("chat_id", unique=True)
    await reply_save_col.create_index("trigger")
    # 🗣️ /rton — random_talk is looked up filtered by chat_id on every speak, and grows one
    # document per harvested message while active, so this needs an index from day one.
    await talk_col.create_index("chat_id")
    await users_catcher_col.create_index("total_caught")
    await users_catcher_col.create_index("star_balance")
    await users_catcher_col.create_index([("group_catches.$**", 1)])
    await users_catcher_col.create_index([("star_balance", -1)])
    await users_catcher_col.create_index([("total_caught", -1)])
    await users_catcher_col.create_index([("total_gifted", -1)])
    await users_catcher_col.create_index([("total_buys", -1)])
    await users_catcher_col.create_index([("daily_streak", -1)])
    await users_catcher_col.create_index([("last_daily", 1)])
    await users_catcher_col.create_index([("group_catches.chat_id", -1)])
    await users_catcher_col.create_index([("last_catch_date", -1), ("daily_catches", -1)])
    await users_catcher_col.create_index([("quiz_correct", -1)])
    # ⚡ backs /today's "who hit the daily catch limit first" ranking (see
    # render_today_leaderboard) — a sparse index since only users who've actually hit the
    # cap today have this field set at all.
    await users_catcher_col.create_index([("daily_limit_hit_at", 1)], sparse=True)
    # ⚡ harem.char_id (multikey) — backs the rank-leaderboard aggregation used by
    # /harem. Without this, every rank lookup was a full collection scan.
    await users_catcher_col.create_index("harem.char_id")
    await _dedupe_groups_config()
    await groups_config_col.create_index("chat_id", unique=True)
    await muted_registry_col.create_index([("chat_id", 1), ("user_id", 1)])
    await user_game_profiles_col.create_index("user_id", unique=True)
    await characters_col.create_index("name", unique=True)
    await characters_base_col.create_index("char_id", unique=True)
    await characters_base_col.create_index("category")
    await characters_base_col.create_index("rarity_tier")
    await characters_base_col.create_index("spawn_limit")
    # 🎗️ le_batches_col — batch_number unique (it's the whole point: a stable typo-proof
    # handle), le_name unique (a batch name should only ever map to exactly one number).
    await le_batches_col.create_index("batch_number", unique=True)
    await le_batches_col.create_index("le_name", unique=True)
    # 🩹 ensure_all_le_batches_numbered() used to be called right here — moved to
    # run_bot1_forever() (awaited before the bot can receive commands) so it can never be
    # silently skipped by an unrelated index-creation error in this fire-and-forget background
    # task. See that function's own docstring for the full story.
    await gift_history_col.create_index([("sender_id", 1), ("timestamp", -1)])
    await gift_history_col.create_index([("receiver_id", 1), ("timestamp", -1)])
    # haido_history_col indexes
    await haido_history_col.create_index([("chat_id", 1), ("timestamp", -1)])
    await haido_history_col.create_index([("claimed_by", 1), ("timestamp", -1)])
    await haido_history_col.create_index("claimed")
    # 📈 Catcher indexes
    await users_catcher_col.create_index([("daily_catches", -1)])
    await users_catcher_col.create_index("harem.caught_date")
    await users_catcher_col.create_index("harem.chat_id")
    # ⭐ Star exchange + Owner Shop purchase log
    await star_market_col.create_index("key", unique=True)
    await bot3_treasury_col.create_index("key", unique=True)
    await users_catcher_col.create_index([("star_balance", -1)])
    await star_purchase_log_col.create_index([("timestamp", -1)])
    await star_purchase_log_col.create_index([("buyer_id", 1), ("timestamp", -1)])
    await star_sell_log_col.create_index([("timestamp", -1)])
    await star_sell_log_col.create_index([("seller_id", 1), ("timestamp", -1)])
    await star_exchange_log_col.create_index([("timestamp", -1)])
    await star_exchange_log_col.create_index([("user_id", 1), ("timestamp", -1)])
    # ⚡ groups_msg_counters is read+written on almost EVERY group message (the spawn-trigger
    # counter) — it had no index at all, meaning every single message did a full collection
    # scan. This is the single hottest query path in the whole bot.
    await groups_counters_col.create_index("chat_id", unique=True)
    # ⚡ marketplace_data had no indexes at all despite being looked up by listing_id on
    # every purchase attempt, and by char_id/seller_id when browsing or cancelling a listing.
    await marketplace_col.create_index("listing_id", unique=True)
    await marketplace_col.create_index("char_id")
    await marketplace_col.create_index([("seller_id", 1), ("char_id", 1)])
    await marketplace_col.create_index([("timestamp", -1)])
    await artists_col.create_index("artist_name", unique=True)
    # 🚫 gban_col — looked up by user_id on every startup load (active bans) and by an owner
    # checking/lifting a specific user's ban.
    await gban_col.create_index([("user_id", 1), ("active", 1)])
    # 🏰 Squad system — squad_id is looked up on every /squad, /invite, and page-nav callback;
    # members is queried via {"members": user_id} on every catch/command to find "my squad".
    await squads_col.create_index("squad_id", unique=True)
    await squads_col.create_index("members")
    # ❌ REMOVED: gotu_pairs_col, gotu_games_col, gotu_players_col, quiz_questions_col, quiz_msg_counters_col
    await _migrate_rarity_tiers()
    print("Database Indexes synchronized! ✔️")

bot1 = TelegramClient('bot_main_session', APP_ID, APP_HASH, flood_sleep_threshold=10)
# 🔐 bot2 — dedicated owner-control bot. Handles ONLY /addchar, /ktr, /ktrr, /rtclean,
# /shadow, /unshadow. Everything else stays on bot1.
bot2 = TelegramClient('bot_owner_session', APP_ID, APP_HASH, flood_sleep_threshold=10)
# 🛡️ bot3 — Guard Bot. Lives in FORCE_SUB_CHAT_ID only. Pays artists when their /addartist
# cards get collected, hands out Premium users' daily Star gift, and polices/cleans up game
# spam in that group so bot1 doesn't have to. Only starts if GUARD_BOT_TOKEN is configured.
bot3 = TelegramClient('bot_guard_session', APP_ID, APP_HASH, flood_sleep_threshold=10) if GUARD_BOT_TOKEN else None
bot_ids = bot_state.bot_ids
active_group_spawns = bot_state.active_group_spawns
STEALTH_MAU_MODE = False
spawn_locks = bot_state.spawn_locks
catch_race_buffer = bot_state.catch_race_buffer
group_member_count_cache = bot_state.group_member_count_cache
group_member_count_refresh_inflight = bot_state.group_member_count_refresh_inflight
pending_editchar_prompt_ids = bot_state.pending_editchar_prompt_ids
pending_addle_prompt = bot_state.pending_addle_prompt
pending_renamele_prompt = bot_state.pending_renamele_prompt
active_dice_sessions = bot_state.active_dice_sessions
casino_play_counts = bot_state.casino_play_counts
pending_casino_bet = bot_state.pending_casino_bet
pending_mergele_prompt = bot_state.pending_mergele_prompt
pending_gban_prompt = bot_state.pending_gban_prompt
sticker_spam_data = bot_state.sticker_spam_data
char_spam_data = bot_state.char_spam_data
admin_cache = bot_state.admin_cache
pending_sell_offers = bot_state.pending_sell_offers
pending_premium_gifts = bot_state.pending_premium_gifts
daily_first_msg_gate = bot_state.daily_first_msg_gate
dark_passenger_targets = bot_state.dark_passenger_targets
IS_REPLY_ACTIVE = False
REPLY_INTERVAL = 4
reply_msg_counters = bot_state.reply_msg_counters
group_spawn_counters = bot_state.group_spawn_counters
trivia_spawn_counters = bot_state.trivia_spawn_counters
trivia_spawn_targets = bot_state.trivia_spawn_targets
# 🗣️ Random talk (retired): used to quietly save clean lines from real chat and say one back
# every RANDOM_TALK_INTERVAL messages, unprompted. /rton and /rtoff are removed, so this stays
# permanently off — see random_talk_engine below. /rtclean still exists to wipe talk_col.
IS_RANDOM_TALK_ACTIVE = False
RANDOM_TALK_INTERVAL = 50
random_talk_counters = bot_state.random_talk_counters
USER_METRICS_BUFFER = []
BUFFER_LOCK = asyncio.Lock()
user_cooldowns = bot_state.user_cooldowns
# ==========================================
# 🖱️ INLINE BUTTON SPAM-CLICK GUARD
# ==========================================
# 🩹 FIX: no inline button anywhere in the bot was rate-limited — every tap re-fired its
# handler instantly. Most handlers just .edit() a message, which is wasteful but harmless.
# A few (e.g. the "My Profile" button shown on a successful catch — see catchprofile_ in
# system_callback_router) call event.respond(), which posts a BRAND NEW message every time.
# A user mashing that button could flood the chat with duplicate profile cards in seconds,
# and every rapid click also burns a fresh DB read. This handler is registered before every
# other CallbackQuery handler in the file, so (Telethon dispatches handlers in registration
# order) it always runs first. If the same user taps again inside the cooldown window, we
# answer the tap immediately with a small non-blocking toast — so the button doesn't look
# "stuck" or broken — and raise StopPropagation so none of the real handlers below ever see
# that click. First tap and every tap after the cooldown passes through untouched.
CALLBACK_CLICK_COOLDOWN = 1.0  # seconds between accepted clicks, per user
_last_callback_click = bot_state._last_callback_click  # user_id -> timestamp

@bot1.on(events.CallbackQuery())
async def callback_spam_click_guard(event):
    user_id = event.sender_id
    if user_id == OWNER_ID:
        return  # owner needs fast, unthrottled access to admin panels/navigation
    now = time.time()
    last = _last_callback_click.get(user_id, 0)
    if now - last < CALLBACK_CLICK_COOLDOWN:
        try:
            await event.answer("ခနစောင့် အချစ်ကလေး!", alert=False)
        except Exception:
            pass
        raise events.StopPropagation
    _last_callback_click[user_id] = now
    if len(_last_callback_click) > 5000:  # opportunistic cleanup, keeps this from growing forever
        cutoff = now - 300
        for k, ts in list(_last_callback_click.items()):
            if ts < cutoff:
                del _last_callback_click[k]

# Spam & Daily catch tracking
user_spam_data = bot_state.user_spam_data  # (user_id, chat_id) -> list of timestamps
user_mute_until = bot_state.user_mute_until  # user_id -> timestamp (GLOBAL — applies in every group)
active_haido_events = bot_state.active_haido_events  # chat_id -> {"char_id", "char_name", "rarity", "spawn_time", "claimed_by", "claimed"}
HAIDO_TIMEOUT_SECONDS = 900  # 15 minutes

# ---- Rarity 1-4 (top four RARITY_TIERS) spawns are gated behind a quiz: chosen_char is picked
# as usual, but instead of spawning immediately, a question + 4 inline buttons is posted. Only
# the FIRST correct tap actually releases the spawn (normal /who + /collect flow after that).
# chat_id -> {"char", "options", "correct_index", "question", "msg_id", "quiz_time", "solved"}
pending_rarity_quiz = bot_state.pending_rarity_quiz
pending_marriage_proposals = bot_state.pending_marriage_proposals
# 🧠 Trivia auto-spawn — a fully separate, standalone multiple-choice Q&A (TRIVIA_OPTION_COUNT
# options) that fires independently of the rarity-gate quiz above (that one gates a character
# spawn; this one just pays Stars).
# chat_id -> {"options", "correct_index", "question", "msg_id", "quiz_time", "solved", "attempted_users"}
pending_trivia_quiz = bot_state.pending_trivia_quiz
pending_squad_setup = bot_state.pending_squad_setup  # 🏰 /create Squad-founding flow — see BotState
pending_gift_notes = bot_state.pending_gift_notes  # 💌 gift-with-a-note bridge — see BotState
aml_jail = bot_state.aml_jail  # 🚨 comedic AML "bust" — see try_deduct_bet_bot3 below
active_cardgame_lobbies = bot_state.active_cardgame_lobbies  # 🃏 /cardgame PVP lobbies — see BotState
RARITY_GATE_TIERS = {RARITY_TIERS[0], RARITY_TIERS[1], RARITY_TIERS[2], RARITY_TIERS[3]}  # rarity No.1-4, by position
# 🩹 NEW (owner report — "old characters keep spawning, new ones barely show up") — see the
# full reasoning in trigger_dynamic_spawn where this is actually applied. 14 days / 8x is a
# starting point, not a precisely-tuned number — raise the multiplier for a stronger push, or
# the day count for a longer runway, if new characters still aren't catching up fast enough.
NEW_CHARACTER_BOOST_DAYS = 14
NEW_CHARACTER_BOOST_MULTIPLIER = 8
RARITY_GATE_TIMEOUT_SECONDS = 360
RARITY_QUIZ_BANK = [
    {"question": "၅ + ၃ ဘယ်လောက်လဲ?", "options": ["၇", "၈", "၉", "၁၀"], "correct_index": 1},
    {"question": "၁၀ - ၄ ဘယ်လောက်လဲ?", "options": ["၄", "၅", "၆", "၇"], "correct_index": 2},
    {"question": "၃ x ၄ ဘယ်လောက်လဲ?", "options": ["၁၀", "၁၁", "၁၂", "၁၃"], "correct_index": 2},
    {"question": "၂၀ ÷ ၅ ဘယ်လောက်လဲ?", "options": ["၃", "၄", "၅", "၆"], "correct_index": 1},
    {"question": "၁၅ + ၇ ဘယ်လောက်လဲ?", "options": ["၂၀", "၂၁", "၂၂", "၂၃"], "correct_index": 2},
    {"question": "၈ x ၉ ဘယ်လောက်လဲ?", "options": ["၆၂", "၇၂", "၈၂", "၉၂"], "correct_index": 1},
    {"question": "၁၀၀ - ၃၇ ဘယ်လောက်လဲ?", "options": ["၆၁", "၆၂", "၆၃", "၆၄"], "correct_index": 2},
    {"question": "၂၄ ÷ ၃ ဘယ်လောက်လဲ?", "options": ["၆", "၇", "၈", "၉"], "correct_index": 2},
    {"question": "၇ + ၈ ဘယ်လောက်လဲ?", "options": ["၁၃", "၁၄", "၁၅", "၁၆"], "correct_index": 2},
    {"question": "၅ x ၅ ဘယ်လောက်လဲ?", "options": ["၂၀", "၂၄", "၂၅", "၃၀"], "correct_index": 2},
    {"question": "၅၀ - ၂၃ ဘယ်လောက်လဲ?", "options": ["၂၅", "၂၆", "၂၇", "၂၈"], "correct_index": 2},
    {"question": "၃၆ ÷ ၆ ဘယ်လောက်လဲ?", "options": ["၄", "၅", "၆", "၇"], "correct_index": 2},
    {"question": "၉ + ၁၁ ဘယ်လောက်လဲ?", "options": ["၁၈", "၁၉", "၂၀", "၂၁"], "correct_index": 2},
    {"question": "၁၂ x ၃ ဘယ်လောက်လဲ?", "options": ["၃၀", "၃၄", "၃၆", "၃၈"], "correct_index": 2},
    {"question": "၈၀ - ၄၅ ဘယ်လောက်လဲ?", "options": ["၃၃", "၃၄", "၃၅", "၃၆"], "correct_index": 2},
    {"question": "၄၉ ÷ ၇ ဘယ်လောက်လဲ?", "options": ["၅", "၆", "၇", "၈"], "correct_index": 2},
    {"question": "၈ x ၇ ဘယ်လောက်လဲ?", "options": ["၄၆", "၅၆", "၆၆", "၇၆"], "correct_index": 1},
    {"question": "၁၁ + ၁၃ ဘယ်လောက်လဲ?", "options": ["၂၂", "၂၃", "၂၄", "၂၅"], "correct_index": 2},
    {"question": "၃ x ၉ ဘယ်လောက်လဲ?", "options": ["၂၅", "၂၆", "၂၇", "၂၈"], "correct_index": 2},
    {"question": "၉၀ - ၆၈ ဘယ်လောက်လဲ?", "options": ["၂၀", "၂၁", "၂၂", "၂၃"], "correct_index": 2},
    {"question": "၇၂ ÷ ၈ ဘယ်လောက်လဲ?", "options": ["၇", "၈", "၉", "၁၀"], "correct_index": 2},
    {"question": "၁၄ + ၁၅ ဘယ်လောက်လဲ?", "options": ["၂၇", "၂၈", "၂၉", "၃၀"], "correct_index": 2},
    {"question": "၄ x ၁၃ ဘယ်လောက်လဲ?", "options": ["၄၂", "၅၂", "၆၂", "၇၂"], "correct_index": 1},
    {"question": "၅၀ - ၁၂ ဘယ်လောက်လဲ?", "options": ["၃၆", "၃၇", "၃၈", "၃၉"], "correct_index": 2},
    {"question": "၆၀ ÷ ၅ ဘယ်လောက်လဲ?", "options": ["၁၀", "၁၁", "၁၂", "၁၃"], "correct_index": 2},
    {"question": "၁ + ၇ ဘယ်လောက်လဲ?", "options": ["၆", "၇", "၈", "၉"], "correct_index": 2},
    {"question": "၂၀ x ၄ ဘယ်လောက်လဲ?", "options": ["၇၀", "၈၀", "၉၀", "၁၀၀"], "correct_index": 1},
    {"question": "၉၉ - ၂၀ ဘယ်လောက်လဲ?", "options": ["၇၇", "၇၈", "၇၉", "၈၀"], "correct_index": 2},
    {"question": "၅၆ ÷ ၇ ဘယ်လောက်လဲ?", "options": ["၆", "၇", "၈", "၉"], "correct_index": 2},
    {"question": "၁၀၀ + ၂၅ ဘယ်လောက်လဲ?", "options": ["၁၁၅", "၁၂၀", "၁၂၅", "၁၃၀"], "correct_index": 2},
    {"question": "၆ x ၁၁ ဘယ်လောက်လဲ?", "options": ["၅၆", "၆၆", "၇၆", "၈၆"], "correct_index": 1},
    {"question": "၇၃ - ၄၈ ဘယ်လောက်လဲ?", "options": ["၂၃", "၂၄", "၂၅", "၂၆"], "correct_index": 2},
    {"question": "၄၅ ÷ ၉ ဘယ်လောက်လဲ?", "options": ["၄", "၅", "၆", "၇"], "correct_index": 1},
    {"question": "၂ + ၈ ဘယ်လောက်လဲ?", "options": ["၈", "၉", "၁၀", "၁၁"], "correct_index": 2},
    {"question": "၃ x ၁၅ ဘယ်လောက်လဲ?", "options": ["၄၀", "၄၅", "၅၀", "၅၅"], "correct_index": 1},
    {"question": "၈၀ - ၃၃ ဘယ်လောက်လဲ?", "options": ["၄၅", "၄၆", "၄၇", "၄၈"], "correct_index": 2},
    {"question": "၈၄ ÷ ၄ ဘယ်လောက်လဲ?", "options": ["၁၉", "၂၀", "၂၁", "၂၂"], "correct_index": 2},
    {"question": "၆၆ + ၁၂ ဘယ်လောက်လဲ?", "options": ["၇၆", "၇၇", "၇၈", "၇၉"], "correct_index": 2},
    {"question": "၇ x ၈ ဘယ်လောက်လဲ?", "options": ["၄၆", "၅၆", "၆၆", "၇၆"], "correct_index": 1},
    {"question": "၁၀၀ - ၅၆ ဘယ်လောက်လဲ?", "options": ["၄၂", "၄၃", "၄၄", "၄၅"], "correct_index": 2},
    {"question": "၉၀ ÷ ၁၀ ဘယ်လောက်လဲ?", "options": ["၇", "၈", "၉", "၁၀"], "correct_index": 2},
    {"question": "၁၂ + ၂၃ ဘယ်လောက်လဲ?", "options": ["၃၃", "၃၄", "၃၅", "၃၆"], "correct_index": 2},
    {"question": "၆ x ၆ ဘယ်လောက်လဲ?", "options": ["၃၀", "၃၄", "၃၆", "၃၈"], "correct_index": 2},
    {"question": "၇၅ - ၂၆ ဘယ်လောက်လဲ?", "options": ["၄၇", "၄၈", "၄၉", "၅၀"], "correct_index": 2},
    {"question": "၄၈ ÷ ၆ ဘယ်လောက်လဲ?", "options": ["၆", "၇", "၈", "၉"], "correct_index": 2},
    {"question": "၅ + ၁၀ ဘယ်လောက်လဲ?", "options": ["၁၃", "၁၄", "၁၅", "၁၆"], "correct_index": 2},
    {"question": "၉ x ၄ ဘယ်လောက်လဲ?", "options": ["၃၀", "၃၄", "၃၆", "၃၈"], "correct_index": 2},
    {"question": "၅၀ - ၁၉ ဘယ်လောက်လဲ?", "options": ["၂၉", "၃၀", "၃၁", "၃၂"], "correct_index": 2},
    {"question": "၃၆ ÷ ၄ ဘယ်လောက်လဲ?", "options": ["၇", "၈", "၉", "၁၀"], "correct_index": 2},
    {"question": "၈ + ၇ ဘယ်လောက်လဲ?", "options": ["၁၃", "၁၄", "၁၅", "၁၆"], "correct_index": 2},
]

# ---- /ban, /unban, /kick fire a ChatAction (participant update) themselves — e.g. lifting a
# ban via /unban shows up to Telethon as the user "leaving", which used to trigger a spurious
# goodbye message right after the UNBAN OPERATION confirmation. Anything we changed ourselves
# via /ban, /unban, or /kick is recorded here for a few seconds so welcome_goodbye can ignore
# the resulting ChatAction instead of misfiring a welcome/goodbye text for it.
recent_mod_actions = bot_state.recent_mod_actions  # (chat_id, user_id) -> timestamp
MOD_ACTION_SUPPRESS_WINDOW = 10  # seconds

def mark_mod_action(chat_id, user_id):
    recent_mod_actions[(chat_id, user_id)] = time.time()

def was_recent_mod_action(chat_id, user_id):
    ts = recent_mod_actions.pop((chat_id, user_id), None)
    return ts is not None and (time.time() - ts) <= MOD_ACTION_SUPPRESS_WINDOW

# ---- Telegram/Telethon sometimes deliver more than one raw update for a SINGLE "bot was
# added to this group" action (e.g. a service message update AND a separate participant
# update). Without a guard, welcome_goodbye below would fire 2-3x, sending the owner-notify
# and the group intro message that many times. Same lock-then-recheck shape as spawn_locks.
bot_added_locks = bot_state.bot_added_locks
_recent_bot_added_chats = bot_state._recent_bot_added_chats  # chat_id -> timestamp
BOT_ADDED_DEDUP_WINDOW = 30  # seconds

def bq(text): return f"<blockquote><b>{text}</b></blockquote>"
def owner_tag():
    return f"<a href='tg://user?id={OWNER_ID}'><b>Owner</b></a>"

# ==========================================
# 💵 → ⭐ USD RETIREMENT (2026-08)
# ==========================================
# Per owner directive: USD is retired as a currency entirely. Every existing player's
# wallet_balance (USD) is converted ONCE — automatically, the next time the bot starts — into
# ⭐ Star at USD_PER_STAR, then wallet_balance is removed. See run_usd_to_star_migration() and
# its call from run_bot1_forever() near SYSTEM INITIALIZATION. From that point on nothing in
# this bot reads or writes wallet_balance again; every USD-denominated flow (catch bonus,
# referral, daily bonus, casino games, cosmetics shop, achievements, etc.) now runs on
# star_balance instead — Star absorbs USD's old role. 💠 VLT is the new, separate currency
# introduced alongside this for the Market (see VLT EXCHANGE below) — Star itself is no longer
# spent there.


MMK_PER_USD = 4000  # [LEGACY] 1 old-USD = 4000 old-MMK — kept ONLY so every pre-existing
# "<old MMK value> / MMK_PER_USD" audit expression in this file still evaluates to the exact
# number it always has. Nothing new should ever be denominated in MMK or USD again.

# Rarity No.1 (highest) → No.9 (lowest). Emoji come from RARITY_EMOJI so there is only
# ever ONE place to change them. "name" is what gets stored on characters/harem items and
# shown everywhere — it always carries the emoji + fancy font tier name + its number, so
# players can instantly see both the rarity tier and its rank (1–9).
_RARITY_STAR_BONUS_MAP = {
    # ⭐ 2026-08 USD RETIREMENT: this used to be _RARITY_VALUE_MAP, a USD wallet_balance
    # payout (old-MMK-worth / MMK_PER_USD). Re-expressed directly in Star as
    # <old MMK worth> / 1,000,000 (1,000,000 old-MMK was 1 Star's own old fixed rate before
    # the USD migration — see the [LEGACY] STAR_STARTING_PRICE note below), which keeps the
    # exact same relative spread between rarities the old table always had. Paid into
    # star_balance on catch, on top of catch_star_reward() — see catch_rarity_star_bonus().
    "ULTRA": 300, "LEGEND": 75, "MYTHIC": 50, "EPIC": 30, "INCREDIBLE": 15,
    "GOLD": 10, "RARE": 7, "UNCOMMON": 4, "COMMON": 2
}
# ⚠️ Cosmetic-only flavor word tacked onto each tier for display (e.g. ULTRA -> ULTRASTAR).
# classify_rarity() still matches on the bare RARITY_TIERS token (e.g. "MYTHIC"), which is
# always a leading substring of its flavor name here (e.g. "MYTHICALPEACOCK" still contains
# "MYTHIC"), so this is safe to change freely without touching classification/sorting/weights.
RARITY_DISPLAY_NAME = {
    "ULTRA": "ULTRA", "LEGEND": "LEGEND", "MYTHIC": "MYTHICAL",
    "EPIC": "EPIC", "INCREDIBLE": "INCREDIBLE", "GOLD": "GOLDENMOON",
    "RARE": "RARE", "UNCOMMON": "UNCOMMON", "COMMON": "COMMON"
}
RARITY_NUM_MAP = {
    str(i + 1): {
        "name": f"{RARITY_EMOJI[tier]} {f(RARITY_DISPLAY_NAME[tier])} {f(f'No.{i + 1}')}",
        # 💠 "worth" shown on a character (currency_value) is its Market price — see
        # RARITY_VLT_PRICE / vlt_price_for_char() — not the catch bonus.
        "value": RARITY_VLT_PRICE[tier]
    }
    for i, tier in enumerate(RARITY_TIERS)
}
# Reverse lookup: canonical tier name -> its rarity number ("1".."9"). Used by /changeallrarity
# and anywhere we need to re-derive the current official display name for an existing tier.
RARITY_TIER_TO_NUM = {tier: str(i + 1) for i, tier in enumerate(RARITY_TIERS)}

# ==========================================
# ⭐ STAR EXCHANGE — TUNING CONSTANTS [LEGACY — Star no longer trades against USD]
# ==========================================
# 🩹 2026-08 USD RETIREMENT: /buystar, /sellstar and /setstarrate are retired along with USD
# — there is nothing left for Star to exchange against here. STAR_STARTING_PRICE / STAR_MIN_PRICE
# / STAR_HISTORY_MAX are kept only so the legacy star_market_col doc (and /starlist's historical
# rows, from before the retirement) still render correctly. See VLT EXCHANGE below for the
# live exchange mechanism that replaces this one (Star <-> VLT).
STAR_STARTING_PRICE = 1_000_000.0 / MMK_PER_USD   # [LEGACY] USD per 1 ⭐ Star, frozen at retirement
STAR_MIN_PRICE = 100000.0 / MMK_PER_USD  # [LEGACY] safety floor /setstarrate used to enforce
STAR_HISTORY_MAX = 24
# 🏦 bot3's starting bankroll — set once, the first time get_or_create_bot3_treasury() runs.
# Large enough that ordinary payouts across every bot3 game never need bot3 to "go negative";
# from then on the balance only moves via bot3_treasury_adjust() as real bets/payouts happen.
# 🩹 2026-08: BOT3_SEED_WALLET_BALANCE (the old USD half of the treasury) is retired along
# with star_balance — bot3's treasury is Star-only now (see bot3_treasury_adjust()).
BOT3_SEED_STAR_BALANCE = 100_000_000          # ⭐ 100 Million
QUIZ_STAR_REWARD_MIN = 3  # ⭐ reward range for correctly answering a Rarity 1-4 gate quiz
QUIZ_STAR_REWARD_MAX = 7

# ==========================================
# 💠 VLT EXCHANGE — TUNING CONSTANTS (2026-08, replaces Star's old USD exchange role)
# ==========================================
# 💠 VLT is the ONLY currency the Market (Owner Shop /buy + Owner /sell buyback) accepts now —
# see RARITY_VLT_PRICE / vlt_price_for_char(). ⭐ Star's remaining jobs are (1) being paid out
# as a reward (catches, quiz, daily, referral, casino, etc.) and (2) exchanging into VLT via
# /buyvlt & /sellvlt at the live rate below. Unlike Star's old fixed USD peg, this rate is
# allowed to drift on its own within [VLT_RATE_MIN, VLT_RATE_MAX] — see vlt_drift_loop().
VLT_RATE_MIN = 5.0     # ⭐ floor — 1💠 is never worth less than this many Star
VLT_RATE_MAX = 7.0     # ⭐ ceiling — 1💠 is never worth more than this many Star
VLT_STARTING_RATE = 6.0  # ⭐ per 1💠 — the midpoint, used the first time the market doc is created
VLT_HISTORY_MAX = 24
VLT_DRIFT_INTERVAL_SECONDS = 900  # ⏱️ how often the background drift tick nudges the rate (15 min)
VLT_DRIFT_STEP_MAX = 0.15  # ⭐ largest random nudge (up or down) applied per drift tick



# 🧠 TRIVIA AUTO-SPAWN — a standalone, "first correct tap wins Stars" event, 4 answer choices.
# Fully independent of the character-spawn system and the rarity-gate quiz above: its own
# message counter (trivia_spawn_counters/trivia_spawn_targets), its own pending state
# (pending_trivia_quiz), its own timeout watcher. See trigger_trivia_spawn() further down.
# 🩹 CHANGED (per owner request, Aug 2026): was 2 options. With only 2, one attempt-per-PERSON
# doesn't stop the GROUP — someone taps option 1, announces "not 1" in chat, and a second
# account taps option 2 for a guaranteed win. 4 options doesn't eliminate that coordination
# trick outright, but it triples the accounts/coordination needed and makes each wrong tap far
# less informative, so it's a meaningfully harder game rather than a coin flip.
TRIVIA_SPAWN_MIN_MSGS = 45   # min group messages between trivia spawns (per chat)
TRIVIA_SPAWN_MAX_MSGS = 55   # max group messages between trivia spawns (per chat) — averages ~50
# 🩹 CHANGED (per owner request, 2026-08): was 90-110 (avg ~100) — halved to trigger trivia
# noticeably more often.
TRIVIA_TIMEOUT_SECONDS = 120  # how long a trivia question stays open before revealing the answer unsolved
TRIVIA_OPTIONS_REVEAL_DELAY = 5  # 🩹 NEW (per owner report): seconds the question sits alone,
# with NO options text and NO buttons at all, before either appears. Was: options+buttons
# posted instantly alongside the question, which just trained people to reflex-mash without
# reading (and still occasionally win by luck) — see trigger_trivia_spawn's two-stage send.
# TRIVIA_TIMEOUT_SECONDS (the actual answering window) starts counting from the reveal, not
# from this initial post, so nobody's real answering time is shortened by this delay.

# ==========================================
# 🌟 GOLDEN QUESTION — per owner request (goal: more participants AND more hype). A rare,
# visually distinct trivia round that pays out several times the normal Star reward for
# whatever difficulty gets rolled. Deliberately touches ONLY the Star payout — Trivia Points
# (the skill-based rank ladder) stay exactly what that difficulty normally gives, the same
# design line already drawn for Premium's star_reward-only bonus above. Rolled once per spawn,
# independent of difficulty, so a Golden round can land on an Easy question just as easily as
# an Extreme Hard one — keeps it a lucky, exciting moment for everyone, not a reward locked
# behind the hardest tier.
# ==========================================
GOLDEN_QUESTION_CHANCE = 0.10       # 10% of trivia spawns — roughly 1 in every 10 rounds
GOLDEN_QUESTION_MULTIPLIER = 5      # ⭐ reward range ×5 for that one round only
GOLDEN_QUESTION_EMOJI = "🌟"        # the one signature symbol for this event — used consistently
# across the question post, the reveal, and the winner announcement, rather than a different
# "shiny" emoji at each stage.

# ==========================================
# 🛡️ MIN GROUP MEMBERS FOR SPAWNS — 2026-08, per owner report: tiny groups (a handful of
# accounts) were being created purely to spam messages and farm spawn rewards, with none of the
# "shared with a real community" friction a populated group has.
# 🩹 CHANGED (2026-08, per owner request): groups under this member count used to be blocked
# from spawning entirely (a hard `return` in global_message_counter_handler). That's now gone —
# tiny groups still farm spawns, they just need DOUBLE the usual message count to get one (see
# the spawn_target doubling in global_message_counter_handler). This makes it slower to farm
# rather than impossible, without shutting the feature off for small-but-real communities.
# Trivia is untouched by any of this — its target (TRIVIA_SPAWN_MIN_MSGS/MAX_MSGS above) is
# never doubled and never gated by member count, regardless of group size.
# ==========================================
MIN_GROUP_MEMBERS_FOR_SPAWN = 30
GROUP_MEMBER_COUNT_CACHE_TTL = 3600  # 1 hour — member counts don't change fast enough to justify
# refetching more often, and being off by up to an hour here is harmless either direction.

def get_cached_group_member_count_nowait(chat_id):
    """Instant, synchronous cache read — deliberately NEVER calls Telegram's API itself (see
    schedule_group_member_count_refresh for that part). This is called from
    global_message_counter_handler, which runs on every single group message and — per the
    hard lesson from catch_handler's own race-condition bug — must never have an `await` on
    real I/O sitting in front of a /obtain attempt. Returns None if there's no fresh-enough
    cached value yet; callers should treat that as "don't gate" (fail open), never as "assume
    under threshold," so a cold cache can't silently stop spawns in a legitimate group."""
    cached = group_member_count_cache.get(chat_id)
    if cached and (time.time() - cached[1]) < GROUP_MEMBER_COUNT_CACHE_TTL:
        return cached[0]
    return None

def schedule_group_member_count_refresh(chat_id):
    """Fire-and-forget background refresh — never awaited by the caller, so it can never
    introduce a delay into a message-handling hot path. Safe to call on every message with a
    cold/stale cache; the inflight-set guard collapses concurrent calls for the same chat down
    to one real API request."""
    if chat_id in group_member_count_refresh_inflight:
        return
    group_member_count_refresh_inflight.add(chat_id)
    asyncio.create_task(_refresh_group_member_count_bg(chat_id))

async def _refresh_group_member_count_bg(chat_id):
    try:
        participants = await bot1.get_participants(chat_id, limit=0)
        count = getattr(participants, 'total', None)
        if count is None and isinstance(participants, list):
            count = len(participants)
        if count is not None:
            group_member_count_cache[chat_id] = (count, time.time())
    except Exception as e:
        print(f"_refresh_group_member_count_bg error for {chat_id}: {e}")
    finally:
        group_member_count_refresh_inflight.discard(chat_id)

# 🎚️ DIFFICULTY TIERS (owner request, Aug 2026) — 5 levels, each drawing from its own slice of
# TRIVIA_QUESTION_BANK below. "weight" controls how often a tier gets picked when a trivia
# spawns (random.choices, see weighted_random_trivia_difficulty()). 🩹 CHANGED: Easy's weight
# was cut hard (35 → 12) per owner request — it was showing up too often now that there's a
# much bigger question pool across all 5 tiers; Normal/Hard now carry the bulk of spawns
# instead of Easy dominating. "star_min"/"star_max" is that tier's ⭐ reward range for the first
# correct tap — together the 5 tiers span the full 200-1500⭐ range the owner asked for, split
# proportionally by difficulty. "points" is how many Trivia Points (see TRIVIA_RANKS) a correct
# answer at that tier is worth; "wrong_penalty" is how many Trivia Points a WRONG tap costs —
# deliberately smaller than what a correct answer at that tier would have earned, but still
# scaled by difficulty, so a wrong guess on an Extreme Hard question costs more than whiffing
# an Easy one. Points are NOT boosted by Premium — only the ⭐ Star reward is
# (PREMIUM_QUIZ_REWARD_MULTIPLIER) — so the rank ladder stays skill/participation based rather
# than pay-to-win.
# 🩹 NEW "star_penalty" (per owner request — Star/VLT inflation fix): a wrong answer now ALSO
# costs real ⭐ Star, not just Trivia Points — trivia is one of the most-played features, so it
# doubles as a real currency sink now. Set at 20% of that tier's star_min (owner-adjustable —
# tune to taste), same ascending-by-difficulty shape as wrong_penalty. If a player's balance
# can't cover it, they pay what they have and the rest becomes star_debt, auto-settled out of
# their balance the next time they earn Star from a catch, /daily, or a correct trivia answer
# (see settle_star_debt) — never lets a balance go negative. See trivia_answer_callback.
TRIVIA_DIFFICULTY_CONFIG = {
    "EASY":         {"label": "Easy",         "emoji": "🟢", "weight": 12, "star_min": 200,  "star_max": 400,  "points": 5,  "wrong_penalty": 2,  "star_penalty": 40},
    "NORMAL":       {"label": "Normal",       "emoji": "🔵", "weight": 27, "star_min": 450,  "star_max": 650,  "points": 8,  "wrong_penalty": 4,  "star_penalty": 90},
    "HARD":         {"label": "Hard",         "emoji": "🟠", "weight": 28, "star_min": 700,  "star_max": 900,  "points": 12, "wrong_penalty": 6,  "star_penalty": 140},
    "VERY_HARD":    {"label": "Very Hard",    "emoji": "🔴", "weight": 20, "star_min": 950,  "star_max": 1200, "points": 18, "wrong_penalty": 9,  "star_penalty": 190},
    "EXTREME_HARD": {"label": "Extreme Hard", "emoji": "🟣", "weight": 13, "star_min": 1250, "star_max": 1500, "points": 25, "wrong_penalty": 13, "star_penalty": 250},
}
TRIVIA_DIFFICULTY_ORDER = ["EASY", "NORMAL", "HARD", "VERY_HARD", "EXTREME_HARD"]  # display / weighted-pick order
TRIVIA_OPTION_COUNT = 4  # 🩹 was 2 — see the exploit note above. Options are always shuffled
# and re-indexed at spawn time (see trigger_trivia_spawn), so this constant is the only place
# that needs to change if the owner ever wants a different option count again.

def weighted_random_trivia_difficulty():
    """Picks one of the 5 difficulty tiers, weighted by TRIVIA_DIFFICULTY_CONFIG[*]['weight']
    (Easy shows up most often, Extreme Hard rarest — the same 'common tiers are common' shape
    as the character-rarity spawn weighting elsewhere in the bot)."""
    weights = [TRIVIA_DIFFICULTY_CONFIG[d]["weight"] for d in TRIVIA_DIFFICULTY_ORDER]
    return random.choices(TRIVIA_DIFFICULTY_ORDER, weights=weights, k=1)[0]

def normalize_trivia_difficulty_arg(raw):
    """Normalizes a free-typed difficulty argument ('very hard', 'Very-Hard', 'VERYHARD',
    'extreme', 'medium', ...) to one of TRIVIA_DIFFICULTY_CONFIG's canonical keys, matching
    regardless of spaces/dashes/underscores/casing — or returns None if nothing matches.
    Shared by /ftrivia, /addtriviabulk, /listtriviabank, /deltriviabank."""
    squashed = re.sub(r'[\s_\-]+', '', (raw or '')).upper()
    if not squashed:
        return None
    for key in TRIVIA_DIFFICULTY_CONFIG:
        if re.sub(r'[\s_\-]+', '', key).upper() == squashed:
            return key
    aliases = {"MEDIUM": "NORMAL", "EXTREME": "EXTREME_HARD", "INSANE": "EXTREME_HARD"}
    return aliases.get(squashed)

# 🏆 TRIVIA RANK LADDER (owner request) — 18 tiers of a permanent, cumulative "Trivia Points"
# score, stored per-user as users_catcher_col.trivia_points — separate from the spendable ⭐
# Star reward above; points only ever measure standing on the leaderboard, they're never spent.
# Thresholds grow the way the owner specified (100, 300, 500, ... increasing by 200 each step):
# (points_needed, "Rank Name") pairs in ascending order — a user's rank is the highest tier
# whose threshold they've reached. See get_trivia_rank_info() / build_trivia_progress_bar() below.
# 🩹 EXTENDED (per owner request): the ladder used to top out at 🏆 Legend (1100) — anyone past
# that just sat at "(MAX 🏆)" forever with nothing left to climb toward. Added 5 more tiers on
# top, same +200 spacing, so reaching Legend is a milestone on the way up rather than the end
# of the road.
# 🩹 EXTENDED AGAIN (per owner request): same problem resurfaced one ceiling higher — the
# leaderboard's own top scorers had climbed well past 2100 and were all just sitting at
# "♾️ Omniscient" with nothing to distinguish a 2200-pt player from a 2800-pt one. Added 6 more
# tiers on top, same +200 spacing, no new ceiling in sight.
TRIVIA_RANKS = [
    (0,    "🌱 Newbie"),
    (100,  "🥉 Bronze Brain"),
    (300,  "🥈 Silver Scholar"),
    (500,  "🥇 Gold Genius"),
    (700,  "💎 Diamond Mind"),
    (900,  "👑 Quiz Master"),
    (1100, "🏆 Legend"),
    (1300, "⚡ Grandmaster"),
    (1500, "🌟 Celestial"),
    (1700, "🔥 Immortal"),
    (1900, "🌌 Transcendent"),
    (2100, "♾️ Omniscient"),
    (2300, "👁️ All-Seeing"),
    (2500, "🌠 Sovereign"),
    (2700, "🔱 Demiurge"),
    (2900, "🕳️ Singularity"),
    (3100, "☄️ Ascendant"),
    (3300, "🌈 Paragon"),
]

def get_trivia_rank_info(points):
    """Returns (rank_number 1-18, rank_name, this_rank's_floor, next_rank's_floor_or_None)."""
    idx = 0
    for i, (threshold, _name) in enumerate(TRIVIA_RANKS):
        if points >= threshold:
            idx = i
    floor_points, rank_name = TRIVIA_RANKS[idx]
    next_points = TRIVIA_RANKS[idx + 1][0] if idx + 1 < len(TRIVIA_RANKS) else None
    return idx + 1, rank_name, floor_points, next_points

async def get_trivia_position(points):
    """(position, total) — this user's numeric standing among everyone who has ever scored a
    trivia_points > 0, e.g. (243, 1600) meaning "#243 out of 1600 trivia answerers". Distinct
    from get_trivia_rank_info's 1-12 TIER number — this is the actual leaderboard position,
    shared by the /profile HUD, the /triviarank command, and the winner-reveal message in
    trivia_answer_callback so all three always agree on the same number."""
    total = await users_catcher_col.count_documents({"trivia_points": {"$gt": 0}})
    position = await users_catcher_col.count_documents({"trivia_points": {"$gt": points}}) + 1
    return position, total

async def settle_star_debt(user_id):
    """🩹 NEW (per owner request — Star/VLT inflation fix): star_debt is created when a trivia
    wrong-answer's star_penalty (see TRIVIA_DIFFICULTY_CONFIG) is bigger than the player's
    balance can cover right then — they pay what they have, the shortfall becomes debt instead
    of ever pushing star_balance negative. This sweeps as much of that debt as the CURRENT
    balance covers, silently, no message sent (called right after a Star credit, so it isn't
    worth interrupting that reward's own message just to report a debt payment).
    Call this after crediting star_balance, not before — it reads the balance fresh from the
    DB, so it needs the credit to have already landed. Wired into the bot's three highest-
    frequency Star inflows (perform_catch, /daily, a correct trivia answer) rather than every
    single one of the many scattered $inc star_balance call sites across the bot — those three
    cover the vast majority of real Star inflow, so debt gets swept promptly in practice
    without needing a much larger, riskier refactor of every credit path in the file."""
    try:
        user_doc = await users_catcher_col.find_one({"user_id": user_id})
        if not user_doc:
            return
        debt = user_doc.get("star_debt", 0)
        if debt <= 0:
            return
        balance = user_doc.get("star_balance", 0)
        if balance <= 0:
            return
        sweep = round(min(balance, debt), 2)
        if sweep <= 0:
            return
        await users_catcher_col.update_one(
            {"user_id": user_id},
            {"$inc": {"star_balance": -sweep, "star_debt": -sweep}}
        )
    except Exception as e:
        print(f"⚠️ settle_star_debt failed for {user_id}: {e}")

def build_trivia_progress_bar(points, floor_points, next_points, length=10):
    """Text progress bar (█ filled / ░ empty) showing progress from the current rank's floor
    toward the next rank's floor. next_points=None means max rank already reached → full bar.
    🩹 Named build_trivia_progress_bar (not build_progress_bar) — a same-named, differently-
    signatured build_progress_bar(current, total, length=10) already exists above and is used
    by /discover and collection-progress displays; redefining that name here would have broken
    every call site that only passes (current, total)."""
    if next_points is None:
        return "█" * length
    span = next_points - floor_points
    if span <= 0:
        return "█" * length
    progressed = max(0, min(span, points - floor_points))
    filled = max(0, min(length, round(length * progressed / span)))
    return "█" * filled + "░" * (length - filled)

# ==========================================
# 🏆 TOP 10 WEEKLY STAR REWARD (owner request, 2026-08) — every Monday at 00:00 (TZ), the
# current Global Top 10 by cumulative Trivia Points gets a one-time ⭐ Star payout, tiered by
# rank (1st gets the most, 10th the least). This is a bonus ON TOP of the per-question ⭐
# rewards already paid live when someone wins a trivia round — it never touches trivia_points
# itself, so it can't distort the rank ladder or the leaderboard standings that decide next
# week's payout.
# 🩹 Amounts below are a starting point — tune freely, this list is the ONLY place that needs
# to change to adjust the payout scale.
# ==========================================
TRIVIA_TOP10_REWARD_STARS = [5000, 4000, 3000, 2500, 2000, 1750, 1500, 1250, 1000, 750]
# ^ index 0 = rank 1 (top), index 9 = rank 10.

async def get_current_trivia_top10_ids():
    """Set of user_ids currently on the Global Top 10 Trivia Points leaderboard. Used by the
    Top 10 double wrong-penalty in trivia_answer_callback and by the weekly payout below — a
    fresh, cheap indexed query every call rather than a cache, since both callers need exactly
    current standings (a stale snapshot could double-penalize — or let off the hook — someone
    who just fell out of/into the Top 10)."""
    top_users = await users_catcher_col.find(
        {"trivia_points": {"$gt": 0}}, {"user_id": 1}
    ).sort("trivia_points", -1).limit(10).to_list(length=10)
    return {u["user_id"] for u in top_users}

async def send_trivia_top10_rewards():
    """Pays out TRIVIA_TOP10_REWARD_STARS to the current Global Top 10 by Trivia Points, most
    to least by rank. Announces the full list to the force-sub group (same public channel
    daily_report_scheduler posts to) AND DMs each winner individually with their own rank +
    reward. A DM failure (bot blocked, DMs closed, etc.) is caught per-winner so one blocked
    account never stops the rest from being paid or from seeing the public announcement."""
    top_users = await users_catcher_col.find(
        {"trivia_points": {"$gt": 0}}
    ).sort("trivia_points", -1).limit(len(TRIVIA_TOP10_REWARD_STARS)).to_list(length=len(TRIVIA_TOP10_REWARD_STARS))
    if not top_users:
        return
    lines = []
    for idx, u in enumerate(top_users):
        reward = TRIVIA_TOP10_REWARD_STARS[idx]
        try:
            await users_catcher_col.update_one({"user_id": u["user_id"]}, {"$inc": {"star_balance": reward}})
        except Exception as e:
            print(f"Trivia Top10 reward payout error for {u['user_id']}: {e}")
            continue
        uname = escape_html(clean_display_name(u.get("fullname"), fallback=f"Agent {u['user_id']}"))
        mention = f"<a href='tg://user?id={u['user_id']}'>{uname}</a>"
        lines.append(f"<b>{idx + 1}.</b> {mention} — <code>{u.get('trivia_points', 0)} pts</code> → +<code>{reward:,}⭐</code>")
        try:
            await send_safe_message(
                bot1, u["user_id"],
                f"🏆 <b>Weekly Trivia Top 10!</b>\n"
                f"You finished <b>#{idx + 1}</b> this week with <code>{u.get('trivia_points', 0)}</code> Rank Points.\n"
                f"⭐ <b>Reward:</b> +<code>{reward:,}⭐</code> — credited now!",
                parse_mode='html'
            )
        except Exception:
            pass  # blocked the bot / DMs closed — the group announcement still credits them publicly
    announce_text = "🏆 <b>WEEKLY TRIVIA TOP 10 REWARDS</b>\n\n" + "\n".join(lines)
    try:
        await send_safe_message(bot1, FORCE_SUB_CHAT_ID, announce_text, parse_mode='html')
    except Exception as e:
        print(f"Trivia Top10 reward announcement failed: {e}")

async def trivia_top10_reward_scheduler():
    """Weekly cron-style loop — fires once every Monday at 00:00 (TZ). Same
    persisted-last-run-marker shape as daily_report_scheduler above (an ISO year-week string
    instead of a date string), so a restart landing right around the trigger time can never
    double-pay a week's rewards."""
    while True:
        now = datetime.now(TZ)
        days_until_monday = (7 - now.weekday()) % 7  # datetime.weekday(): Monday == 0
        target = (now + timedelta(days=days_until_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=7)
        await asyncio.sleep((target - now).total_seconds())

        week_key = datetime.now(TZ).strftime("%G-W%V")
        last_sent = await bot_settings_col.find_one({"_id": "trivia_top10_reward_last_sent"})
        if last_sent and last_sent.get("week") == week_key:
            continue
        try:
            await send_trivia_top10_rewards()
        except Exception as e:
            print(f"Trivia Top10 reward scheduler error: {e}")
        await bot_settings_col.update_one(
            {"_id": "trivia_top10_reward_last_sent"}, {"$set": {"week": week_key}}, upsert=True
        )

TRIVIA_QUESTION_BANK = {
    # 🩹 CHANGED (per owner request, Aug 2026): the old built-in seed questions were all
    # 2-option and are retired now that trivia uses TRIVIA_OPTION_COUNT (4) options — a 2-option
    # question can't be shown correctly by the new 4-option UI, and converting 215 old entries
    # into good 4-option questions (2 more plausible wrong answers each, fact-checked) wasn't
    # something to do half-heartedly. These lists are now just the static safety-net half of
    # get_trivia_pool() (DB questions + this list) — intentionally empty, filled entirely via
    # /addtriviabulk instead. trigger_trivia_spawn() already falls back across every other
    # difficulty tier (and skips the spawn cleanly) if a chosen tier has nothing yet, so an
    # empty tier here never crashes anything — it just won't spawn until the owner bulk-imports
    # questions into it.
    "EASY": [],
    "NORMAL": [],
    "HARD": [],
    "VERY_HARD": [],
    "EXTREME_HARD": [],
}

# ==========================================
# HELPER FUNCTIONS
# ==========================================
async def get_plain_name(event, user_id=None):
    """Returns just the user's display name as plain text (no HTML) — safe to store in the DB.
    Never store the output of get_html_mention() in a database field: it contains <a>/<b> tags
    which get double-escaped and shown as literal text wherever that field is later displayed."""
    if not user_id: user_id = event.sender_id
    try:
        sender = await event.client.get_entity(user_id)
        first_name = getattr(sender, 'first_name', '') or ''
        last_name = getattr(sender, 'last_name', '') or ''
        fullname = f"{first_name} {last_name}".strip()
        if not fullname: fullname = getattr(sender, 'username', '') or f"Agent {user_id}"
    except:
        fullname = f"Agent {user_id}"
    return fullname

async def get_html_mention(event, user_id=None):
    fullname = await get_plain_name(event, user_id)
    if not user_id: user_id = event.sender_id
    return f"<a href='tg://user?id={user_id}'><b>{escape_html(fullname)}</b></a>"

# 🩹 FIX: these two used to hardcode `bot2` no matter which bot's handler called them.
# Bot1's own welcome_goodbye_enhanced was calling them too, which meant every single join
# and leave in ANY group bot2 isn't a member of (i.e. almost every public group, since bot2
# is the owner-only control bot) paid for a network round-trip that was guaranteed to fail
# before falling back to "Could not fetch" — wasted latency on the hottest possible event
# (member joins), and the reason bot1's welcome/goodbye could look unreliable. Both now take
# the calling client explicitly so each bot uses its own connection.
async def get_user_profile_data(user, chat_id, client=None):
    """User Profile အပြည့်အစုံကို စုစည်းပေးမယ်"""
    client = client or bot1
    first = getattr(user, 'first_name', '') or ''
    last = getattr(user, 'last_name', '') or ''
    fullname = f"{first} {last}".strip() or "Unknown User"
    username = f"@{user.username}" if getattr(user, 'username', None) else "None"
    user_id = user.id
    
    # Premium Status
    is_premium = getattr(user, 'premium', False)
    premium_status = "⭐ Premium" if is_premium else "Standard"
    
    # Bio (အကယ်၍ ရှိရင်)
    bio = getattr(user, 'about', '') or "No bio available."
    
    # Join Date (အဖွဲ့ထဲဝင်ခဲ့တဲ့ရက်စွဲ)
    join_date = "Unknown"
    try:
        participant = await client.get_participants(chat_id, filter=types.ChannelParticipantsSearch(user_id))
        if participant:
            join_date = participant[0].date.strftime("%Y-%m-%d %H:%M:%S") if participant[0].date else "Unknown"
    except Exception:
        join_date = "Could not fetch"
    
    return {
        "fullname": fullname,
        "username": username,
        "user_id": user_id,
        "premium_status": premium_status,
        "is_premium": is_premium,
        "bio": bio,
        "join_date": join_date,
        "user": user
    }

async def handle_gift_fee(sender_id):
    """
    sender ဆီက ⭐ Star ကောက်ခံပြီး မရရင် အကြွေးတင်မယ်။
    bot3 balance ကိုလည်း update လုပ်မယ်။
    🩹 2026-08 USD RETIREMENT: was 1000 USD — kept in sync with the live gift_fee constant in
    gift_callback_handler (this function itself is currently unused/dead code).
    """
    fee = 5.0

    # 1️⃣ အရင်ဆုံး sender မှာ အကြွေးရှိမရှိ စစ်မယ်
    debt_doc = await debts_col.find_one({"user_id": sender_id})
    if debt_doc:
        debt_amount = debt_doc.get("amount", 0)
        sender_doc = await users_catcher_col.find_one({"user_id": sender_id})
        balance = sender_doc.get("star_balance", 0) if sender_doc else 0

        if balance > 0:
            deduct = min(debt_amount, balance)
            await users_catcher_col.update_one(
                {"user_id": sender_id},
                {"$inc": {"star_balance": -deduct}}
            )
            await bot3_treasury_adjust(usd=deduct)  # bot3 မှာ ပေါင်းထည့်

            new_debt = debt_amount - deduct
            if new_debt <= 0:
                await debts_col.delete_one({"user_id": sender_id})
            else:
                await debts_col.update_one(
                    {"user_id": sender_id},
                    {"$set": {"amount": new_debt}}
                )

    # 2️⃣ ဒီ gift အတွက် fee ကောက်မယ်
    if not await try_deduct_balance(sender_id, fee):
        # မရရင် အကြွေးတင်မယ်
        await debts_col.update_one(
            {"user_id": sender_id},
            {"$inc": {"amount": fee}},
            upsert=True
        )
        await bot3_treasury_adjust(usd=fee)
        return False  # fee မရဘူး (အကြွေးတင်ထားတယ်)

    return True  # fee ရတယ်
    
async def get_profile_photo(user, client=None):
    """User Profile Photo ကို ယူပေးမယ် (ရှိရင်)"""
    client = client or bot1
    try:
        photos = await client.get_profile_photos(user.id, limit=1)
        if photos:
            return photos[0]
    except Exception:
        pass
    return None

def format_join_message(profile):
    """Join Message ကို UI လှအောင် ဖော်မတ်လုပ်မယ်"""
    premium_emoji = "⭐" if profile["is_premium"] else ""
    return f"""
🪪 <b>Hey,Thers is a New Member.</b>

🪩 <b>Name:</b> {escape_html(profile['fullname'])}
🦖 <b>User ID:</b> <code>{profile['user_id']}</code>
🍭 <b>Username:</b> {profile['username']}
{premium_emoji} <b>Status:</b> {profile['premium_status']}
🐇 <b>Bio:</b> {escape_html(profile['bio'][:100])}
🎒 <b>Join Date:</b> <code>{profile['join_date']}</code>

🎒 <i>Welcome to the group! Have a great time.</i>
    """

def format_leave_message(profile):
    """Leave Message ကို UI လှအောင် ဖော်မတ်လုပ်မယ်"""
    premium_emoji = "⭐" if profile["is_premium"] else ""
    return f"""
🪪 <b>MEMBER LEFT</b>

🪩 <b>Name:</b> {escape_html(profile['fullname'])}
🦖 <b>User ID:</b> <code>{profile['user_id']}</code>
🍭 <b>Username:</b> {profile['username']}
{premium_emoji} <b>Status:</b> {profile['premium_status']}
🎒 <b>Join Date:</b> <code>{profile['join_date']}</code>

🎒 <i>Goodbye! Hope to see you again.</i>
    """


def clean_display_name(name, max_len=25, fallback="Unknown"):
    """Sanitize a name pulled from the DB before displaying it.
    - Strips any HTML tags (defensive: older bug versions could save an HTML mention
      string straight into the fullname field, which then showed up as literal tag
      text once escaped again at display time).
    - Truncates long/heavily-decorated real Telegram names so tables like the
      leaderboard don't break their alignment.
    Always escape_html() the result before embedding it in an HTML-parsed message."""
    if not name:
        return fallback
    name = re.sub(r'<[^>]+>', '', str(name)).strip()
    if not name:
        return fallback
    if len(name) > max_len:
        name = name[:max_len].rstrip() + "…"
    return name

GAME_FOOTER = "\n\n🪙 <code>/balance</code>, 🙎 တခြားဂိမ်းတွေ ဆော့မယ်ဆို /game လို့ရိုက်"
async def _delete_after_delay(client, chat_id, msg_id, delay=10):
    try:
        await asyncio.sleep(delay)
        await client.delete_messages(chat_id, msg_id)
    except Exception:
        pass

def schedule_game_cleanup(client, chat_id, msg, delay=10):
    # 🛡️ Inside the force-join group specifically, the Guard Bot (bot3) owns cleanup duty —
    # it deletes the game message instead of whichever bot posted it, so that bot doesn't
    # carry the load of every game played in that (typically very active) group. bot3 can
    # delete another bot's message fine as long as it has "Delete Messages" admin rights
    # there. Falls back to the original caller if the Guard Bot isn't configured.
    if chat_id == FORCE_SUB_CHAT_ID and bot3 is not None:
        client = bot3
    msg_id = getattr(msg, 'id', msg)
    asyncio.create_task(_delete_after_delay(client, chat_id, msg_id, delay))

# ==========================================
# ALL GROUPS COMMAND INTERCEPTOR
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^[/.]'))
async def global_slash_cmd_acc_opener(event):
    user_id = event.sender_id
    if not user_id: return
    try:
        user_entity = await event.get_sender()
        first_name = getattr(user_entity, 'first_name', '') or ''
        last_name = getattr(user_entity, 'last_name', '') or ''
        fullname = f"{first_name} {last_name}".strip()
        if not fullname: fullname = getattr(user_entity, 'username', '') or f"User {user_id}"
    except:
        fullname = f"User {user_id}"
    await ensure_user_registered(user_id, fullname)

# ==========================================
# 🔐 FORCE-SUBSCRIBE GATE
# ==========================================
# Players must belong to FORCE_SUB_CHAT_ID before they can use the game. This gates /harem,
# .who/.w/.waifu (and its "Who's this?" button), /collect, and the rest of the player-facing
# commands in FORCE_SUB_GATED_COMMANDS below — admin/owner-only commands (mute, ban, addchar,
# etc.) are never touched.
#
# Registered here, immediately after the ALL-GROUPS COMMAND INTERCEPTOR above and well before
# /who (4403), /collect (4602), /harem (4887) or any casino/economy handler further down —
# Telethon calls handlers in registration order, so this always gets first look at a gated
# command and raises events.StopPropagation to stop everything below it from running for
# that message. bot1/bot_ids/schedule_game_cleanup/_OWN_MENTION_RE/BOT1_USERNAME are all
# already defined above this point, so nothing here is a forward reference.
FORCE_SUB_CHAT_ID = int(os.environ.get("FORCE_SUB_CHAT_ID", "-1003580630981"))
# 🩹 2026-08 USD RETIREMENT: was 500/MMK_PER_USD (0.125 USD) — this legacy audit tool now
# operates on star_balance directly, same small magnitude, just relabeled.
FORCE_SUB_REWARD_STAR = 0.125
FORCE_SUB_MEMBERSHIP_TTL = 120  # seconds a "yes/no member" answer is cached per user
FORCE_SUB_PROMPT_COOLDOWN = 30  # seconds — don't re-nag the same user more than once per 5 min
FORCE_SUB_PROMPT_TTL = 30  # seconds — the join-nudge message deletes itself after this long

# Exactly the commands an ordinary player uses to play — everything listed in get_game_text()/
# get_casino_text()/get_collection_text(), plus catch/who/w/waifu/haido/trade/sell/buy/dex/
# search/scrap/richest/leaderboard/lb/achievements. Deliberately excludes /start, /help,
# /game, /weather, /gift, /fav, and every admin/owner-only command — /gift and /fav no longer
# nudge a non-member to join the force-sub group.
FORCE_SUB_GATED_COMMANDS = {"slot", "game", "harem"}
_FORCE_SUB_CMD_RE = re.compile(r'^[/.](\w+)')

force_sub_membership_cache = bot_state.force_sub_membership_cache  # user_id -> (is_member: bool, expiry_ts: float)
force_sub_prompt_last_sent = bot_state.force_sub_prompt_last_sent  # user_id -> ts of the last join-nudge sent (anti-spam)
_force_sub_invite_link = None     # cached invite link for FORCE_SUB_CHAT_ID (fetched once)

async def get_force_sub_invite_link():
    """Fetches (and caches forever — invite links don't change unless revoked) the invite
    link for FORCE_SUB_CHAT_ID. Requires bot1 to have invite rights there; returns None
    (and the join-prompt just skips the button) if that isn't set up yet."""
    global _force_sub_invite_link
    if _force_sub_invite_link:
        return _force_sub_invite_link
    try:
        invite = await bot1(ExportChatInviteRequest(FORCE_SUB_CHAT_ID))
        _force_sub_invite_link = invite.link
    except Exception as e:
        print(f"⚠️ Force-sub invite link fetch failed: {e}")
    return _force_sub_invite_link

async def is_force_sub_member(user_id):
    """True if `user_id` currently belongs to FORCE_SUB_CHAT_ID. Cached briefly per user
    (FORCE_SUB_MEMBERSHIP_TTL) so a burst of commands from the same person doesn't hammer
    Telegram with a fresh permissions lookup every single time."""
    now = time.time()
    cached = force_sub_membership_cache.get(user_id)
    if cached and now < cached[1]:
        return cached[0]
    try:
        perms = await bot1.get_permissions(FORCE_SUB_CHAT_ID, user_id)
        is_member = perms is not None
    except Exception:
        is_member = False
    force_sub_membership_cache[user_id] = (is_member, now + FORCE_SUB_MEMBERSHIP_TTL)
    return is_member

async def send_force_sub_prompt(event):
    """Sends the bilingual (MM/EN) join-nudge and self-deletes it after FORCE_SUB_PROMPT_TTL
    seconds. Rate-limited per user via FORCE_SUB_PROMPT_COOLDOWN so a not-yet-joined person
    mashing a gated command — or a whole group full of them — never turns into the bot
    repeatedly spamming 'please join' messages, including in groups we don't own."""
    user_id = event.sender_id
    now = time.time()
    last = force_sub_prompt_last_sent.get(user_id, 0)
    if now - last < FORCE_SUB_PROMPT_COOLDOWN:
        return  # nudged this person recently already — stay quiet instead of piling on
    force_sub_prompt_last_sent[user_id] = now
    link = await get_force_sub_invite_link()
    text = (
        "🐉🦋 <b>harem လုပ်ချင်ရင် အောက်က Group ထဲ အရင်ဝင်ဖို့လိုပါတယ် 🦄\n"
        "👇 Group ထဲဝင်ပြီးမှ ဒီ command ကို ပြန်ရိုက်ပေးပါ့။\n\n"
        "🐉🦋 <b>Hold up!</b> You'll need to join our group🦄\n"
        "👇 Tap below to join, then send the command again."
    )
    buttons = [[Button.url("Join Group / Group ဝင်ရန်", link)]] if link else None
    try:
        msg = await event.reply(text, parse_mode='html', buttons=buttons)
        schedule_game_cleanup(event.client, event.chat_id, msg, delay=FORCE_SUB_PROMPT_TTL)
    except Exception as e:
        print(f"⚠️ Force-sub prompt send failed: {e}")

@bot1.on(events.NewMessage(pattern=r'^[/.]'))
async def force_sub_gate(event):
    user_id = event.sender_id
    if not user_id or user_id == OWNER_ID or user_id in bot_ids:
        return
    m = _FORCE_SUB_CMD_RE.match(event.raw_text or "")
    if not m:
        return
    cmd_word = m.group(1).split('@')[0].lower()
    if cmd_word not in FORCE_SUB_GATED_COMMANDS:
        return
    # Respect the same "only if THIS bot is the one being addressed" rule own_pattern() uses
    # on every individual handler — an explicit /cmd@othername mention should never be gated
    # by bot1's own force-sub rule.
    mention = _OWN_MENTION_RE.match(event.raw_text or "")
    if mention and BOT1_USERNAME and mention.group(1).lower() != BOT1_USERNAME:
        return
    if await is_force_sub_member(user_id):
        return
    await send_force_sub_prompt(event)
    raise events.StopPropagation

# ==========================================
# 🛡️ GAME SPAM THROTTLE (every group — not just the force-join room)
# ==========================================
# Originally scoped to just the force-join room, since that was bot1's busiest room for
# casino/economy commands. But the same problem — a group of people rapid-firing a game command
# at once — happens in ANY group bot1 is in, and each send/edit call eats into bot1's own
# per-chat and per-account send budget. This now throttles game commands everywhere, using the
# same per-player cooldown. The notice itself is still posted via bot3 specifically inside the
# force-join room (that's the one place bot3 actually lives and can post it), and via bot1 (the
# event's own client) everywhere else — bot3 is never a member of the many arbitrary other
# groups this bot is added to, so it could never send there anyway.
GUARD_GAME_COOLDOWN_SECONDS = 4  # minimum gap between game commands per player, in any one chat
GUARD_GAME_COMMANDS = {"slot", "basketball", "bball", "dice"}  # 🩹 2026-08 (per owner request): every other casino game was removed, then basketball/dice were added back as native-dice games alongside slot

@bot1.on(events.NewMessage(pattern=r'^[/.]'))
async def guard_bot_game_throttle(event):
    user_id = event.sender_id
    if not user_id or user_id == OWNER_ID or user_id in bot_ids: return
    m = _FORCE_SUB_CMD_RE.match(event.raw_text or "")
    if not m: return
    cmd_word = m.group(1).split('@')[0].lower()
    if cmd_word not in GUARD_GAME_COMMANDS: return
    on_cooldown, remaining = await is_on_cooldown(user_id, "guard_game_spam", GUARD_GAME_COOLDOWN_SECONDS)
    if not on_cooldown: return
    notifier = bot3 if (event.chat_id == FORCE_SUB_CHAT_ID and bot3 is not None) else event.client
    try:
        warn = await notifier.send_message(
            event.chat_id,
            f" <b>ခဏစောင့်ပါ!</b> Game တွေ မကြာခဏ မဆော့ပါနဲ့ — <code>{remaining}s</code> လောက် စောင့်ပြီးမှ ထပ်ကစားပါ။",
            parse_mode='html'
        )
        schedule_game_cleanup(notifier, event.chat_id, warn, delay=5)
    except Exception:
        pass
    try:
        await event.delete()
    except Exception:
        pass
    raise events.StopPropagation

# ---- JOIN REWARD — REMOVED. This used to grant +FORCE_SUB_REWARD_STAR and one random
# character card to anyone joining FORCE_SUB_CHAT_ID (via a ChatAction watcher on new joins,
# plus a startup backfill for existing members). That grant path is gone — nobody gets paid
# for joining anymore. FORCE_SUB_REWARD_STAR, the "force_sub_rewarded" flag, and harem entries
# tagged source="force_sub_reward" are KEPT below because /revokereward (single user) and
# /reclaimforcesub (bulk, see further down) both still need them to reverse what was already
# handed out historically. ----

@bot1.on(events.ChatAction(chats=FORCE_SUB_CHAT_ID))
async def force_sub_join_tracker(event):
    """All that's left of the old join-reward watcher: just keeps the membership cache warm
    on a live join, so is_force_sub_member() doesn't need a fresh API call immediately after
    someone joins. No reward is granted."""
    if not (event.user_joined or event.user_added):
        return
    user_id = event.user_id
    if not user_id or user_id in bot_ids:
        return
    force_sub_membership_cache[user_id] = (True, time.time() + FORCE_SUB_MEMBERSHIP_TTL)

# ==========================================
# 🛡️ GUARD BOT — JOIN / LEAVE WATCHER (force-join group only)
# ==========================================
# Separate concern from force_sub_join_tracker above (which just keeps the membership cache
# warm) — this just posts a profile card so the room can see who's coming and going, and it's
# specifically the Guard Bot's job. Reuses get_user_profile_data / get_profile_photo /
# format_join_message / format_leave_message, which already existed in this file (left over
# from before welcome/goodbye was disabled bot1-wide) but had nothing calling them — this
# wires them back up, scoped to just this one group, on bot3.
# ==========================================
GUARD_MASS_JOIN_LEAVE_THRESHOLD = 5

async def _guard_bot_join_leave_watcher(event):
    if event.chat_id != FORCE_SUB_CHAT_ID:
        return
    try:
        target_ids = event.user_ids or [event.user_id]
        real_ids = [uid for uid in target_ids if uid and uid not in bot_ids]
        if not real_ids:
            return

        if len(real_ids) > GUARD_MASS_JOIN_LEAVE_THRESHOLD:
            if event.user_added or event.user_joined:
                await bot3.send_message(event.chat_id, bq(f"🪪 <b>{len(real_ids)} new members joined at once.</b> Welcome all!"), parse_mode='html')
            elif event.user_kicked or event.user_left:
                await bot3.send_message(event.chat_id, bq(f"👋 <b>{len(real_ids)} members left at once.</b>"), parse_mode='html')
            return

        if event.user_added or event.user_joined:
            for uid in real_ids:
                try:
                    user = await bot3.get_entity(uid)
                except Exception:
                    continue
                if getattr(user, 'bot', False):
                    continue
                profile = await get_user_profile_data(user, event.chat_id, client=bot3)
                msg = format_join_message(profile)
                photo = await get_profile_photo(user, client=bot3)
                try:
                    if photo:
                        await bot3.send_file(event.chat_id, photo, caption=msg, parse_mode='html')
                    else:
                        await bot3.send_message(event.chat_id, msg, parse_mode='html')
                except Exception as e:
                    print(f"⚠️ Guard Bot join card failed for {uid}: {e}")

        elif event.user_kicked or event.user_left:
            for uid in real_ids:
                try:
                    user = await bot3.get_entity(uid)
                except Exception:
                    try:
                        await bot3.send_message(event.chat_id, f"👋 <b>User {uid}</b> has left the chat.", parse_mode='html')
                    except Exception:
                        pass
                    continue
                if getattr(user, 'bot', False):
                    continue
                profile = await get_user_profile_data(user, event.chat_id, client=bot3)
                msg = format_leave_message(profile)
                try:
                    await bot3.send_message(event.chat_id, msg, parse_mode='html')
                except Exception as e:
                    print(f"⚠️ Guard Bot leave card failed for {uid}: {e}")
    except Exception as e:
        print(f"⚠️ Guard Bot join/leave watcher error: {e}")

if bot3 is not None:
    bot3.on(events.ChatAction)(_guard_bot_join_leave_watcher)

# ---- /revokereward [user_id] [confirm] — owner-only cleanup tool for an ALREADY-granted
# force-sub reward that shouldn't have gone out (e.g. a bot account rewarded before the
# is-this-a-bot fix above existed). Shows a preview first; nothing is touched until "confirm"
# is added. Only pulls harem entries tagged source="force_sub_reward" (added going forward) —
# rewards granted before that tag existed can still have their USD reversed here, but any card
# from them needs manual removal since there's no reliable way to tell it apart from a normal
# catch. ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]revokereward(?:@\w+)?\s+(-?\d+)(?:\s+(confirm))?$', 'bot1')))
async def revoke_force_sub_reward(event):
    if event.sender_id != OWNER_ID: return
    target_id = int(event.pattern_match.group(1))
    is_confirm = bool(event.pattern_match.group(2))
    user_doc = await users_catcher_col.find_one({"user_id": target_id})
    if not user_doc or not user_doc.get("force_sub_rewarded"):
        return await event.reply(f"❌ No force-sub reward on record for <code>{target_id}</code> — nothing to revoke.", parse_mode='html')
    harem = user_doc.get("harem", [])
    reward_entries = [item for item in harem if isinstance(item, dict) and item.get("source") == "force_sub_reward"]
    wallet = user_doc.get("star_balance", 0)
    new_wallet = max(0, wallet - FORCE_SUB_REWARD_STAR)
    if not is_confirm:
        preview = (
            f"⚠️ <b>Revoke force-sub reward for</b> <code>{target_id}</code>?\n\n"
            f"⭐ Star: <code>{wallet:,}</code> → <code>{new_wallet:,}</code>\n"
            f"🎴 Tagged reward card(s) found: <code>{len(reward_entries)}</code>"
        )
        if not reward_entries:
            preview += (
                "\n\n⚠️ <i>No tagged reward card found (this account was rewarded before the "
                "tagging fix) — only the ⭐ Star will be reversed here; remove any card "
                "manually if needed.</i>"
            )
        preview += f"\n\nRun <code>/revokereward {target_id} confirm</code> to actually apply this."
        return await event.reply(preview, parse_mode='html')
    new_total_caught = max(0, user_doc.get("total_caught", 0) - len(reward_entries))
    new_daily_catches = max(0, user_doc.get("daily_catches", 0) - len(reward_entries))
    update_ops = {
        "$set": {"star_balance": new_wallet, "total_caught": new_total_caught, "daily_catches": new_daily_catches},
        "$unset": {"force_sub_rewarded": ""}
    }
    if reward_entries:
        update_ops["$pull"] = {"harem": {"source": "force_sub_reward"}}
    await users_catcher_col.update_one({"user_id": target_id}, update_ops)
    await event.reply(
        f"✅ <b>Reward revoked for</b> <code>{target_id}</code>\n"
        f"⭐ Star: <code>{wallet:,}</code> → <code>{new_wallet:,}</code>\n"
        f"🎴 Removed <code>{len(reward_entries)}</code> tagged reward card(s).",
        parse_mode='html'
    )

# ==========================================
# 🧹 /reclaimforcesub [confirm] — owner-only BULK version of /revokereward above. Finds every
# account that was granted the (now-removed) join reward — force_sub_rewarded=True, with at
# least one harem entry tagged source="force_sub_reward" so the exact card(s) granted are
# known precisely — AND that has NEVER caught anything else: total_caught doesn't exceed how
# many reward cards it has. That combination is the signal for "joined only to collect the
# free USD/card and never actually played" as opposed to a genuine player who happened to
# also get the join reward along the way. Genuine players (any catch beyond the reward
# card(s)) are left completely untouched — not even the reward card is pulled from them.
# Same preview-then-confirm shape, and same audit-logging habit, as every other destructive
# owner command in this file (/compresswealth, /revokereward, /changeusd).
# ==========================================
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]reclaimforcesub(?:@\w+)?(?:\s+(confirm))?$', 'bot1')))
async def reclaim_force_sub_rewards_bulk(event):
    if event.sender_id != OWNER_ID: return
    is_confirm = bool(event.pattern_match.group(1))

    candidates = await users_catcher_col.find(
        {"force_sub_rewarded": True, "harem": {"$elemMatch": {"source": "force_sub_reward"}}},
        {"user_id": 1, "fullname": 1, "star_balance": 1, "total_caught": 1, "daily_catches": 1, "harem": 1}
    ).to_list(length=None)

    to_reclaim = []
    for doc in candidates:
        harem = doc.get("harem", []) or []
        reward_entries = [x for x in harem if isinstance(x, dict) and x.get("source") == "force_sub_reward"]
        if not reward_entries:
            continue
        if doc.get("total_caught", 0) > len(reward_entries):
            continue  # genuine player — has catches beyond the reward card(s), leave alone
        to_reclaim.append((doc, reward_entries))

    if not to_reclaim:
        return await event.reply(
            "✅ <b>No matching accounts</b> — nobody currently qualifies "
            "(reward on record, but zero catches beyond it).",
            parse_mode='html'
        )

    total_usd = sum(min(doc.get("star_balance", 0), FORCE_SUB_REWARD_STAR) for doc, _ in to_reclaim)
    total_cards = sum(len(entries) for _, entries in to_reclaim)

    if not is_confirm:
        sample_lines = "\n".join(
            f"• <code>{doc['user_id']}</code> ({escape_html(doc.get('fullname', '?'))}) — "
            f"⭐ <code>{doc.get('star_balance', 0):,.2f}</code>, reward card(s) <code>{len(entries)}</code>"
            for doc, entries in to_reclaim[:15]
        )
        more_line = f"\n…and {len(to_reclaim) - 15} more" if len(to_reclaim) > 15 else ""
        return await event.reply(
            f"⚠️ <b>BULK FORCE-SUB RECLAIM</b>\n\n"
            f"Criteria: <code>force_sub_rewarded=True</code>, has a tagged reward card, "
            f"<b>zero catches beyond it</b> (never actually played).\n\n"
            f"👥 <b>Accounts matching:</b> <code>{len(to_reclaim)}</code>\n"
            f"⭐ <b>Total Star to reverse:</b> <code>{total_usd:,.2f}</code>\n"
            f"🎴 <b>Total reward cards to remove:</b> <code>{total_cards}</code>\n\n"
            f"{sample_lines}{more_line}\n\n"
            f"❗ ဒါက <b>ပြန်ဖျက်လို့မရဘူး</b> — Run <code>/reclaimforcesub confirm</code> to proceed.",
            parse_mode='html'
        )

    reclaimed = 0
    for doc, entries in to_reclaim:
        uid = doc["user_id"]
        wallet = doc.get("star_balance", 0)
        new_wallet = max(0, wallet - FORCE_SUB_REWARD_STAR)
        new_total_caught = max(0, doc.get("total_caught", 0) - len(entries))
        new_daily_catches = max(0, doc.get("daily_catches", 0) - len(entries))
        await users_catcher_col.update_one(
            {"user_id": uid},
            {
                "$set": {"star_balance": new_wallet, "total_caught": new_total_caught, "daily_catches": new_daily_catches},
                "$unset": {"force_sub_rewarded": ""},
                "$pull": {"harem": {"source": "force_sub_reward"}}
            }
        )
        reclaimed += 1

    await force_sub_reclaim_log_col.insert_one({
        "run_at": time.time(), "run_by": OWNER_ID, "accounts_reclaimed": reclaimed,
        "total_usd_reversed": total_usd, "total_cards_removed": total_cards,
        "user_ids": [doc["user_id"] for doc, _ in to_reclaim]
    })

    await event.reply(
        f"✅ <b>BULK RECLAIM COMPLETE</b>\n"
        f"👥 <b>Accounts reclaimed:</b> <code>{reclaimed}</code>\n"
        f"⭐ <b>Total Star reversed:</b> <code>{total_usd:,.2f}</code>\n"
        f"🎴 <b>Total reward cards removed:</b> <code>{total_cards}</code>",
        parse_mode='html'
    )

# 🩹 REMOVED (per owner request): bot3 used to auto-run a perceptual-hash identify lookup on
# EVERY photo/video anyone posted in FORCE_SUB_CHAT_ID, unprompted — needless overhead for
# something that's rarely what the poster actually wanted. Identification in that group is
# still available on request: reply to a photo/video with /who (bot1), or DM the photo/video
# straight to the bot (see dm_auto_identify_handler below).

# ==========================================
# 🚨 ONE-TIME MIGRATION — required after the PHASH_SIZE 8→16 change above. Every character's
# stored photo_phash in the DB was computed with the OLD 8x8/64-bit dHash. New identify
# attempts now compute a 16x16/256-bit hash for the incoming media. hamming_distance() XORs the
# two as raw integers — comparing a 64-bit int against a 256-bit int this way does NOT degrade
# gracefully, it just produces a huge, meaningless distance, so every existing character would
# silently stop being identifiable at all until this runs once. Owner-only, run it once right
# after deploying this change (safe to re-run any time — it always recomputes from the original
# stored media, never from the old hash).
# ==========================================
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]rehashall(?:@\w+)?$', 'bot1')))
async def rehash_all_characters_handler(event):
    if event.sender_id != OWNER_ID:
        return
    if is_duplicate_event(event):
        return
    all_chars = await characters_base_col.find({}, {"char_id": 1, "name": 1, "storage_msg_id": 1, "_id": 0}).to_list(length=None)
    status = await event.reply(f"🔄 Re-hashing {len(all_chars)} characters with the new 256-bit phash... 0/{len(all_chars)}")
    ops, done, failed = [], 0, []
    for i, char in enumerate(all_chars):
        try:
            msg = await bot1.get_messages(SPECIFIC_CONTROL_GROUP, ids=char["storage_msg_id"])
            new_hash = await compute_phash_for_message(msg) if msg else None
            if new_hash:
                ops.append(UpdateOne({"char_id": char["char_id"]}, {"$set": {"photo_phash": new_hash}}))
            else:
                failed.append(char.get("name", char["char_id"]))
        except Exception as e:
            failed.append(char.get("name", char["char_id"]))
            print(f"rehashall error for {char.get('char_id')}: {e}")
        done += 1
        await asyncio.sleep(0.05)  # gentle pacing — avoids FloodWaitError on get_messages
        if done % 50 == 0 or done == len(all_chars):
            try:
                await status.edit(f"🔄 Re-hashing... {done}/{len(all_chars)}")
            except Exception:
                pass
    for i in range(0, len(ops), 500):
        await characters_base_col.bulk_write(ops[i:i + 500])
    await invalidate_character_caches()
    result_text = f"✅ Re-hashed {len(ops)}/{len(all_chars)} characters."
    if failed:
        shown = ", ".join(failed[:15]) + (f" (+{len(failed) - 15} more)" if len(failed) > 15 else "")
        result_text += f"\n⚠️ Couldn't re-fetch media for {len(failed)}: {shown}"
    await status.edit(result_text)

# ==========================================
# 🔎 DM AUTO-IDENTIFY — in the bot's private chat, just sending a photo or video is enough
# to get it identified — no /who, no reply needed. (The old "forward every DM to owner"
# behavior that used to live here has been removed.)
# ==========================================
@bot1.on(events.NewMessage(incoming=True, func=lambda e: e.is_private and (e.photo or e.video) and not e.sticker))
async def dm_auto_identify_handler(event):
    if event.sender_id in bot_ids:
        return
    if event.sender_id == OWNER_ID:
        return  # owner's DM is also where /addquiz collects the quiz's own illustration photo
    text = (event.raw_text or "").strip()
    if _has_command_prefix(text):
        return  # an explicit "/who" (or ".who") caption is handled by who_reveal_handler's Path B instead
    await _identify_media_and_reply(event, event.message)
# ==========================================
# ADMIN CAPABILITY
# ==========================================
async def is_allowed(user_id):
    if user_id == OWNER_ID: return True
    user = await allow_col.find_one({"user_id": user_id})
    return user is not None

async def check_admin(chat_id, user_id):
    if user_id == OWNER_ID: return True
    now = time.time()
    if chat_id in admin_cache and now < admin_cache[chat_id]["expiry"]:
        return user_id in admin_cache[chat_id]["ids"]
    await update_admin_cache(bot1, chat_id)
    if chat_id in admin_cache:
        return user_id in admin_cache[chat_id]["ids"]
    return False

async def update_admin_cache(client, chat_id):
    try:
        admins = await client(GetParticipantsRequest(
            channel=chat_id, filter=ChannelParticipantsAdmins(),
            offset=0, limit=200, hash=0
        ))
        admin_ids = {p.user_id for p in admins.participants}
        admin_cache[chat_id] = {"ids": admin_ids, "expiry": time.time() + 300}
        return admin_ids
    except Exception as e:
        print(f"Error updating admin cache for {chat_id}: {e}")
        return set()


# ==========================================
# EVENT HANDLERS (start, help, etc.)
# ==========================================
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]start(?:@\w+)?(?:\s+(\S+))?$', 'bot1')))
async def system_start_router(event):
    is_cd, rem = await is_on_cooldown(event.sender_id, "start", 2)
    if is_cd: return
    sender = await event.get_sender()
    track_user_metrics(event.sender_id, getattr(sender, 'username', None), getattr(sender, 'first_name', 'User'))
    user_id = event.sender_id
    mention = await get_html_mention(event, user_id)
    payload = event.pattern_match.group(1)
    already_exists = await users_catcher_col.find_one({"user_id": user_id})
    await ensure_user_registered(user_id, await get_plain_name(event, user_id))
    referral_note = ""
    if not already_exists and payload and payload.startswith("ref_"):
        try:
            referrer_id = int(payload[4:])
        except ValueError:
            referrer_id = None
        if referrer_id and referrer_id != user_id:
            referrer_doc = await users_catcher_col.find_one({"user_id": referrer_id})
            if referrer_doc:
                # 🩹 2026-08 USD RETIREMENT: was NEW_USER_BONUS=0.25 USD / REFERRER_BONUS=0.5 USD
                # (already negligible relative to Star even before this migration). Re-tuned to
                # small, meaningful ⭐ Star amounts, same 1:2 ratio — owner-adjustable.
                NEW_USER_BONUS = 2
                REFERRER_BONUS = 4
                await users_catcher_col.update_one(
                    {"user_id": user_id},
                    {"$set": {"referred_by": referrer_id}, "$inc": {"star_balance": NEW_USER_BONUS}}
                )
                await users_catcher_col.update_one(
                    {"user_id": referrer_id},
                    {"$inc": {"star_balance": REFERRER_BONUS, "referral_count": 1}}
                )
                await check_and_award_achievements(referrer_id, notify_chat_id=referrer_id)
                referral_note = (
                    f"\n\n🎉 <b>Referral Bonus!</b> You got <code>+{NEW_USER_BONUS}⭐</code> welcome gift, "
                    f"and your friend got <code>+{REFERRER_BONUS}⭐</code> for inviting you! 🎁"
                )
    bot_me = await bot1.get_me()
    welcome_msg = (
        f"👑 <b>Morgan Bot</b> ကနေ ကြိုဆိုပါတယ်!\n\n"
        f"ကျွန်တော်က <b>Character Collector Bot</b> တစ်ခုပါ — ဒီ Group ထဲမှာ Character အသစ်တွေ "
        f"အခါအားလျော်စွာ ပေါ်လာမှာဖြစ်ပြီး၊ ဖော်ထုတ်ဖမ်းဆီးစုဆောင်းရတဲ့ <b>Collector Game</b> ကို "
        f"အဓိကထားပါတယ်။ အပြင် <b>Casino ဂိမ်းများ</b> (Slot, Roulette, Mines, Plinko, စသည်) နဲ့ "
        f"<b>Group အုပ်ချုပ်ရေး</b> လုပ်ဆောင်ချက်တွေကိုပါ တစ်နေရာတည်း ပေါင်းစပ်ပေးထားပါတယ်။\n\n"
        f"Group ထဲမှာ Character များကို ဖမ်းဆီးရင်း ⭐ Star ရရှိနိုင်ပြီး၊ Casino ဂိမ်းတွေ "
        f"ဆော့ကစားပြီးလည်း ⭐ Star ထပ်ရယူနိုင်ပါတယ်။\n\n"
        f"အောက်က Button များ နှိပ်ပြီး ဘာတွေ ရနိုင်လဲ ကြည့်လိုက်ပါ။ 🙂"
        f"{referral_note}"
    )
    buttons = [
        [Button.inline("⚙️ Commands", data="nav_help_main"), Button.inline("💰 Economy", data="nav_game_main")],
        [Button.inline("🎰 Casino", data="nav_casino_main"), Button.inline("🎒 Collection", data="nav_collection_main")],
        [Button.url("🪐 Add Me To Your Group", f"https://t.me/{bot_me.username}?startgroup=true")],
        [Button.url("👥 Join Our Circle", "https://t.me/Comeback_BoD")]
    ]
    await event.respond(bq(welcome_msg), parse_mode='html', buttons=buttons)

def get_help_text():
    return (
        f"⚙️ <b><u>Command Terminal</u></b>\n\n"
        f" <code>/info</code> [Reply/@username] - User Profile Card ကြည့်ရန်\n"
        f" <code>/weather</code> - မြန်မာ &amp; ထိုင်း ရာသီဥတု တိုက်ရိုက်ကြည့်ရှုရန်\n\n"
        f"📖 <i>Command အားလုံးရဲ့ အပြည့်အစုံစာရင်းအတွက် <code>/introduce</code> ကို ကြည့်ပါ။</i>"
    )

def get_game_text():
    return (
        f"🎮 <b><u>Game Console</u></b>\n\n"
        f"⭐ <code>/balance</code>\n<blockquote>Check your ⭐ Star and 💠 VLT balance.</blockquote>\n\n"
        f"⭐ <code>/daily</code>\n<blockquote>Claim your daily bonus (streak rewards!).</blockquote>\n\n"
        f"⭐ <code>/hunt</code>\n<blockquote>Go on a hunting adventure for extra ⭐ Star.</blockquote>\n\n"
        f"⭐ <code>/gift [char_id] [note]</code> (reply to a user)\n<blockquote>Send a caught character to another player, with an optional short note attached — you'll both see a nicely designed confirmation card right there in chat. Every gift given/received counts toward a Gift Rank — see /giftranks.</blockquote>\n\n"
        f"⭐ <code>/giftstar [amount]</code> (reply to a user)\n<blockquote>Send ⭐ Star straight from your balance to theirs.</blockquote>\n\n"
        f"💠 <code>/giftvlt [amount]</code> (reply to a user)\n<blockquote>Send 💠 VLT straight from your balance to theirs.</blockquote>\n\n"
        f"👑 <code>/giftpremium [months]</code> (reply to a user)\n<blockquote>Buy them Bot Premium with your own ⭐ Star, at the same rate as /buypremium — <code>1, 2, 3, 6, 12</code> months.</blockquote>\n\n"
        f"🎁 <code>/topgift</code>\n<blockquote>Top 10 most generous gifters.</blockquote>\n\n"
        f"🎖️ <code>/giftranks</code>\n<blockquote>See the full Giver and Receiver rank ladders — titles unlock automatically as your gift counts climb.</blockquote>\n\n"
        f"🏪 <code>/market</code>\n<blockquote>Your balance + shortcuts to the Owner Shop and buying 💠 VLT.</blockquote>\n\n"
        f"🛍️ <code>/buy</code>\n<blockquote>Choose between browsing cards (via /show in DM) or buying 💠 VLT.</blockquote>\n\n"
        f"🛍️ <code>/buy [char_id]</code>\n<blockquote>Buy a fresh copy of that character straight from the Owner Shop, priced by rarity: {rarity_price_list_text()}. Capped at {DAILY_BUY_LIMIT}/day, {LIFETIME_BUY_PER_CHAR_LIMIT} copies of the same character ever, and Rarity No.1-3 share {TOP_RARITY_WEEKLY_BUY_LIMIT} combined per week. 🔒 Any character with a catch limit (every 🎯 ULTRA card defaults to {ULTRA_DEFAULT_CATCH_LIMIT}) stops being sellable here — or catchable from a wild spawn — forever, once that many copies of IT specifically have ever been caught or bought; only /gift or /trade can get you one after that.</blockquote>\n\n"
        f"🔥 <code>/sell [char_id]</code>\n<blockquote>Scrap the card for a guaranteed (but small) 💠 VLT floor — {int(SCRAP_MIN_MULT*100)}%-{int(SCRAP_MAX_MULT*100)}% of its Owner Shop price, minted fresh (never from the Owner's own balance). Permanent — the card is destroyed.</blockquote>\n\n"
        f"🤝 <code>/trade [your ID] [their ID]</code> (reply to the user)\n<blockquote>Propose a direct card swap with another player — they confirm or cancel it.</blockquote>\n\n"
        f"💱 <code>/buyvlt [💠]</code> / <code>/sellvlt [💠]</code>\n<blockquote>Exchange ⭐ Star for 💠 VLT (or back) at the live rate — VLT is the only currency the Market (/buy, /sell) accepts.</blockquote>\n\n"
        f"👑 <code>/buypremium</code>\n<blockquote>Buy Bot Premium User status with ⭐ Star — shorter spam cooldown, a higher daily catch limit, bonus cards, a daily Star gift, and more. (Or earn it free — spend/buy {PREMIUM_AUTO_STAR_THRESHOLD}💠 in one day for {PREMIUM_AUTO_GRANT_DAYS} free day.)</blockquote>"
    )

def get_casino_text():
    # 🩹 CHANGED (2026-08, per owner request): every casino game except /slot was removed
    # (cardgame, flip, dice, hilo, gamble, mines, box, roulette, plinko, wheel, rps, blackjack,
    # crash, baccarat, color-tower, lucky-pick, limbo, over/under, tower) and /slot itself was
    # rebuilt to use Telegram's own native dice-message animation instead of hand-edited
    # frames. 🏀 Basketball and 🎲 Dice joined it later, same native-animation engine — see
    # _casino_play.
    # 🩹 CHANGED AGAIN (2026-09, per owner request): /slot alone was reverted off native dice —
    # it drew its own result bot-side and showed it via a single edit.
    # 🩹 CHANGED BACK (2026-09, round 2, per owner request — players missed the real animation):
    # /slot is back on Telegram's native dice, same as basketball/dice — see _casino_play and
    # the paytable's own revert note for the full story and how the jackpot-vs-inflation
    # trade-off was handled instead (payout multiplier, not probability).
    # 🩹 NEW (2026-09, per owner request): /cardgame — PVP, not vs the house like the three
    # above. See its own section comment for the full design (unique 1-10 cards, host-set bet,
    # Join/Start/Info lobby).
    return (
        f"🎰 <b><u>Casino Floor</u></b>\n\n"
        f"🎰 <code>/slot [amount]</code>\n<blockquote>Spin the slot machine — bet ⭐ Star, up to {SLOT_MAX_BET:,} per spin.</blockquote>\n\n"
        f"🏀 <code>/basketball [amount]</code>\n<blockquote>Take a shot — 4 or 5 makes the basket, 5 is the bigger-paying swish.</blockquote>\n\n"
        f"🎲 <code>/dice [amount]</code>\n<blockquote>Roll a 6 to win — simple, single-number odds.</blockquote>\n\n"
        f"🃏 <code>/cardgame [amount]</code>\n<blockquote>PVP, {CARDGAME_MIN_PLAYERS}-{CARDGAME_MAX_PLAYERS} players — highest of a unique 1-10 card wins the pot. Group chats only.</blockquote>\n\n"
        f"<i>All three use Telegram's own native animation, up to {SLOT_MAX_BET:,}⭐ per play.</i>"
    )

def get_collection_text():
    return (
        f"🎒 <b><u>Collection Desk</u></b>\n\n"
        f"🎒 <code>/harem</code>\n<blockquote>View your vault — paginated inventory of everyone you've caught.</blockquote>\n\n"
        f"⭐ <code>/fav [ID]</code>\n<blockquote>Set a favourite card to pin at the top.</blockquote>\n\n"
        f"📊 <code>/profile</code>, <code>/myinfo</code>\n<blockquote>Check your stats, balance, and collection at a glance.</blockquote>\n\n"
        f"🏆 <code>/top</code> / <code>/gtop</code>\n<blockquote>Local and global leaderboards.</blockquote>\n\n"
        f"🔎 <code>/check [ID]</code>\n<blockquote>Detailed character info and its top collectors.</blockquote>\n\n"
        f"🖼 <code>/show</code>\n<blockquote>Pick a rarity (3x3 button grid) and browse every character in it, full quality, with ⬅️ Prev / ➡️ Next and a 🛒 Buy button. DM only.</blockquote>\n\n"
        f"🔗 <code>/referral</code>\n<blockquote>Get your invite link — you get 4⭐, your friend gets 2⭐.</blockquote>\n\n"
        f"💍 <code>/propose</code> (reply to a user) / <code>/divorce</code> / <code>/marriage</code>\n<blockquote>Propose marriage, end one, or check your spouse, Marriage Rank, and progress. See who's been together longest with /topcouples.</blockquote>"
    )

@bot1.on(events.NewMessage(pattern=own_pattern(r'(?i)^[/.]help(?:@\w+)?$', 'bot1')))
async def help_command_handler(event):
    await event.reply(bq(get_help_text()), parse_mode='html', buttons=[[Button.inline("🔙 Back", data="nav_back_home")]])

@bot1.on(events.NewMessage(pattern=own_pattern(r'(?i)^[/.]game(?:@\w+)?$', 'bot1')))
async def game_command_handler(event):
    # 🩹 FIX: this used to call get_game_text(), which — despite the name — is actually the
    # ECONOMY menu (balance/daily/hunt/gifts...). The real games list lives in get_casino_text()
    # — slot, basketball, and dice (2026-08, per owner request: every OTHER casino game was
    # removed, then basketball/dice joined slot as more native-Telegram-animation games).
    await event.reply(bq(get_casino_text()), parse_mode='html', buttons=[[Button.inline("🔙 Back", data="nav_back_home")]])

@bot1.on(events.CallbackQuery())
async def system_callback_router(event):
    data = event.data.decode('utf-8')
    user_id = event.sender_id
    if data == "nav_back_home":
        bot_me = await bot1.get_me()
        welcome_msg = (
            f"<b>🎮 Character Collector Bot</b>\n"
            f"Characters spawn in your groups for you to catch and collect — that's my main game. "
            f"I also run Casino games, an in-bot economy (⭐ Star &amp; 💠 VLT), and basic group "
            f"management on the side."
        )
        buttons = [
            [Button.inline("⚙️ Commands", data="nav_help_main"), Button.inline("💰 Economy", data="nav_game_main")],
            [Button.inline("🎰 Casino", data="nav_casino_main"), Button.inline("🎒 Collection", data="nav_collection_main")],
            [Button.url("Add Me To Your Group", f"https://t.me/{bot_me.username}?startgroup=true")],
            [Button.url("Join Our Circle", "https://t.me/Comeback_BoD")]
        ]
        return await event.edit(bq(welcome_msg), parse_mode='html', buttons=buttons)
    elif data == "nav_help_main":
        return await event.edit(bq(get_help_text()), parse_mode='html', buttons=[[Button.inline("🔙 Back", data="nav_back_home")]])
    elif data == "nav_game_main":
        return await event.edit(bq(get_game_text()), parse_mode='html', buttons=[[Button.inline("🔙 Back", data="nav_back_home")]])
    elif data == "nav_casino_main":
        return await event.edit(bq(get_casino_text()), parse_mode='html', buttons=[[Button.inline("🔙 Back", data="nav_back_home")]])
    elif data == "nav_collection_main":
        return await event.edit(bq(get_collection_text()), parse_mode='html', buttons=[[Button.inline("🔙 Back", data="nav_back_home")]])
    elif data == "nav_hmode":
        await set_rarity_filter_handler(event)
    elif data.startswith("catchprofile_"):
        target_user_id = int(data.split("_", 1)[1])
        if user_id != target_user_id:
            return await event.answer("⚠️ This isn't your profile button!", alert=True)
        mention = await get_html_mention(event, user_id)
        text, buttons = await render_profile_main_page(event, user_id, mention)
        await event.respond(text, parse_mode='html', buttons=buttons)
        await event.answer()
    elif data.startswith("pf_stats_"):
        target_user_id = int(data.split("_", 2)[2])
        if user_id != target_user_id:
            return await event.answer("⚠️ This isn't your profile button!", alert=True)
        user_doc = await users_catcher_col.find_one({"user_id": user_id})
        raw_harem = user_doc.get("harem", []) if user_doc else []
        owned_copies = {tier: 0 for tier in RARITY_TIERS}
        owned_unique = {tier: set() for tier in RARITY_TIERS}
        for item in raw_harem:
            if not isinstance(item, dict):
                continue
            tier = classify_rarity(item.get("rarity", ""))
            if tier in owned_copies:
                owned_copies[tier] += 1
                if item.get("char_id"):
                    owned_unique[tier].add(item["char_id"])
        tier_totals_cursor = characters_base_col.aggregate([
            {"$group": {"_id": "$rarity_tier", "count": {"$sum": 1}}}
        ])
        tier_totals = {doc["_id"]: doc["count"] async for doc in tier_totals_cursor}
        lines = []
        for tier in RARITY_TIERS:
            emoji = RARITY_EMOJI.get(tier, RARITY_DEFAULT_EMOJI)
            copies = owned_copies[tier]
            unique = len(owned_unique[tier])
            total = tier_totals.get(tier, 0)
            lines.append(f"{emoji} <b>{tier}:</b> <code>{copies}</code> owned <i>({unique}/{total} unique)</i>")
        text = (
            f"📊 <b>RARITY BREAKDOWN</b>\n"
            f"<blockquote>" + "\n".join(lines) + "</blockquote>\n"
            f"<i>Ordered from rarest (top) to most common (bottom).</i>"
        )
        buttons = [[Button.inline("🔙 Back to Profile", data=f"pf_back_{user_id}")]]
        await event.edit(text, parse_mode='html', buttons=buttons)
        await event.answer()
    elif data.startswith("pf_back_"):
        target_user_id = int(data.split("_", 2)[2])
        if user_id != target_user_id:
            return await event.answer("⚠️ This isn't your profile button!", alert=True)
        mention = await get_html_mention(event, user_id)
        text, buttons = await render_profile_main_page(event, user_id, mention)
        await event.edit(text, parse_mode='html', buttons=buttons)
        await event.answer()
    elif data.startswith("checkcard_"):
        # 🩹 CHANGED (per owner request): the old "🐇Collections" button on a fresh catch opened
        # the caller's ENTIRE inline harem gallery — heavy for anyone with a big collection.
        # This replaces it with exactly what /check [id] shows for JUST the card that was
        # caught: no gating needed, /check is public info anyone can already look up.
        char_id_raw = data[len("checkcard_"):]
        character, info_text = await build_character_check_text(char_id_raw)
        if not character:
            return await event.answer("✗ Character ID not found!", alert=True)
        async def _send_checkcard(media):
            return await event.respond(info_text, parse_mode='html', file=media)
        sent = await send_with_char_media(character["char_id"], character["storage_msg_id"], _send_checkcard)
        if sent is None:
            await event.respond(info_text, parse_mode='html')
        await event.answer()
    elif data.startswith("quickbal_"):
        # 🩹 CHANGED (per owner request): replaces the old "🦋Profile" button on a fresh catch.
        # A plain alert popup — no new message sent to the chat at all, the lightest possible
        # way to show "what's my balance right now" (Telegram alert text is plain text only,
        # no HTML, hence no tags here).
        target_user_id = int(data[len("quickbal_"):])
        if user_id != target_user_id:
            return await event.answer("⚠️ This isn't your balance button!", alert=True)
        user_doc = await users_catcher_col.find_one({"user_id": user_id})
        star_balance = user_doc.get("star_balance", 0) if user_doc else 0
        vlt_balance = user_doc.get("vlt_balance", 0) if user_doc else 0
        await event.answer(
            f"⭐ Star: {format_star_plain(star_balance)}\n💠 VLT: {format_vlt_plain(vlt_balance)}",
            alert=True
        )
    elif data == "trivialb":
        # 🏆 Global Top 50 by cumulative Trivia Points, paginated 10-per-page — public
        # leaderboard, no gating needed (same reasoning as /check: this is public standing,
        # not personal account data). See render_trivia_leaderboard_page for the pager itself.
        text, buttons = await render_trivia_leaderboard_page(0)
        await event.respond(text, parse_mode='html', buttons=buttons)
        await event.answer()
    elif data == "trivia_global_count_noop":
        # Purely a label — see the button's construction above. Just ack the tap so it never
        # shows a stuck loading spinner; deliberately no alert, no message, no anything else.
        await event.answer()

# ---- CALCULATOR ----
# 🔒 Safe replacement for the old bare eval(text, {"__builtins__": None}, {}) — stripping
# __builtins__ does NOT make eval() a real sandbox in CPython (it's still possible to reach
# dangerous objects through attribute chains on ordinary Python objects). This walks a parsed
# ast.Expression tree instead and only ever evaluates numeric literals combined with
# +-*/%**() — no names, no function calls, no attribute/subscript access are even
# representable in the allowed node types below, so there's no code-execution surface at all.
_SAFE_CALC_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv,
    ast.Pow: operator.pow,
}
_SAFE_CALC_UNARYOPS = {ast.USub: operator.neg, ast.UAdd: operator.pos}
_SAFE_CALC_MAX_EXPONENT = 1000   # 2**99999999 would compute a multi-million-digit number
_SAFE_CALC_MAX_OPERAND = 10 ** 9  # otherwise and hang/eat RAM despite being a short, "safe-looking" string

def _safe_calc_eval(node):
    if isinstance(node, ast.Expression):
        return _safe_calc_eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("only plain numbers are allowed")
        return node.value
    if isinstance(node, ast.BinOp):
        op_func = _SAFE_CALC_BINOPS.get(type(node.op))
        if op_func is None:
            raise ValueError("operator not allowed")
        left, right = _safe_calc_eval(node.left), _safe_calc_eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > _SAFE_CALC_MAX_EXPONENT or abs(left) > _SAFE_CALC_MAX_OPERAND):
            raise ValueError("operands too large")
        return op_func(left, right)
    if isinstance(node, ast.UnaryOp):
        op_func = _SAFE_CALC_UNARYOPS.get(type(node.op))
        if op_func is None:
            raise ValueError("operator not allowed")
        return op_func(_safe_calc_eval(node.operand))
    raise ValueError("expression not allowed")

def safe_calculate(text):
    """Parses+evaluates a plain arithmetic expression safely. Raises on anything invalid
    (syntax error, disallowed node, div-by-zero, oversized operands) — callers should catch
    Exception broadly and just ignore, same as the old eval() call site did."""
    return _safe_calc_eval(ast.parse(text, mode='eval'))

async def _run_auto_calculator(event):
    if event.text.startswith('/'): return
    text = event.text.strip()
    if re.match(r'^[\d\.\s\+\-\*\/\(\)\*\*]+$', text):
        if any(op in text for op in ['+', '-', '*', '/']):
            if len(text) > 50 or (text.count('**') > 1): return
            try:
                result = safe_calculate(text)
                await event.reply(f"🍺<b>Result:</b>\n<code>{text} = {result}</code>", parse_mode='html')
            except Exception:
                pass

TODAY_TOP_LIMIT = 20

async def render_today_leaderboard():
    """Returns the /today text, or None if nobody has caught anything yet today.

    Ranking: users who've already hit today's daily catch cap are listed first, ordered by
    daily_limit_hit_at ascending — i.e. whoever hit the cap earliest ranks #1. That's "who
    reached the limit first". Everyone else who's caught something today but isn't capped yet
    is listed after, ordered by daily_catches descending. Capped at TODAY_TOP_LIMIT total, one
    page — no pagination.

    🩹 FIX: daily_catches/daily_limit_hit_at only reset the next time THAT user runs /collect
    on a new day (see catch_handler), so without a date filter this leaderboard was pulling in
    anyone who'd EVER caught something and hadn't happened to trigger their own rollover yet —
    i.e. stale catches from days or weeks ago, not just today's. last_catch_date >= today's
    midnight (same filter /leaderboard's daily mode already uses) fixes that."""
    today_start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    proj = {"_id": 0, "user_id": 1, "fullname": 1, "daily_catches": 1, "daily_limit_hit_at": 1, "premium_until": 1}

    capped_users = await users_catcher_col.find(
        {"daily_limit_hit_at": {"$exists": True}, "last_catch_date": {"$gte": today_start}}, proj
    ).sort("daily_limit_hit_at", 1).limit(TODAY_TOP_LIMIT).to_list(length=TODAY_TOP_LIMIT)

    remaining = TODAY_TOP_LIMIT - len(capped_users)
    active_users = []
    if remaining > 0:
        active_users = await users_catcher_col.find(
            {"daily_catches": {"$gt": 0}, "daily_limit_hit_at": {"$exists": False}, "last_catch_date": {"$gte": today_start}}, proj
        ).sort("daily_catches", -1).limit(remaining).to_list(length=remaining)

    all_users = capped_users + active_users
    if not all_users:
        return None

    lines = []
    for idx, u in enumerate(all_users, start=1):
        medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else f"#{idx}"
        name = clean_display_name(u.get("fullname"), fallback=f"User {u['user_id']}")
        mention = f"<a href='tg://user?id={u['user_id']}'>{escape_html(name)}</a>"
        premium_tag = " 👑 [Premium User]" if is_premium_active(u) else ""
        hit_at = u.get("daily_limit_hit_at")
        if hit_at:
            time_str = hit_at.astimezone(TZ).strftime("%H:%M") if isinstance(hit_at, datetime) else "?"
            lines.append(f"{medal}  {mention} — 🏁 hit their daily limit at <code>{time_str}</code>{premium_tag}")
        else:
            lines.append(f"{medal}  {mention} — <code>{u['daily_catches']} catches</code>{premium_tag}")

    text = f"📅 <b>Today's Catchers</b> <i>(Top {TODAY_TOP_LIMIT})</i>\n"
    text += f"🏁 Ranked by who reached their daily catch limit first (👑 Premium: {PREMIUM_DAILY_CATCH_LIMIT}, others: {DAILY_CATCH_LIMIT})\n\n"
    text += "\n".join(lines)
    return text

@bot1.on(events.NewMessage)
async def bot1_auto_calculator_handler(event):
    await _run_auto_calculator(event)

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]today(?:@\w+)?$', 'bot1')))
async def today_command(event):
    text = await render_today_leaderboard()
    if text is None:
        return await event.reply("📭 <b>No catches today yet.</b>", parse_mode='html')
    await event.reply(text, parse_mode='html')

# ---- INFO / ID — shared target resolution + a richer profile card for both ----
async def resolve_info_target(event):
    """Figures out who /info or /id should describe: the replied-to user, an @username or
    ID argument, or the sender themself. Returns (target_id, target_user) or (None, None)
    with an error already sent to the chat."""
    if event.is_reply:
        reply_msg = await event.get_reply_message()
        target_id = reply_msg.sender_id
        target_user = await event.client.get_entity(target_id)
        return target_id, target_user
    parts = event.text.split()
    if len(parts) > 1:
        try:
            target_user = await event.client.get_entity(parts[1])
            return target_user.id, target_user
        except Exception:
            await event.reply("⚠️ <b>User not found!</b> Try replying to them instead, or double-check the @username/ID.", parse_mode='html')
            return None, None
    target_id = event.sender_id
    target_user = await event.client.get_entity(target_id)
    return target_id, target_user

async def render_profile_card(event, target_id, target_user):
    """Builds and sends the shared USER PROFILE card used by both /info and /id — with
    account badges (bot/premium/verified/scam/fake/restricted), a clickable name mention,
    and the user's profile photo when one is available."""
    full_name = f"{getattr(target_user, 'first_name', '') or ''} {getattr(target_user, 'last_name', '') or ''}".strip() or "Unknown"
    username = f"@{target_user.username}" if getattr(target_user, 'username', None) else "<i>none</i>"
    mention = f"<a href='tg://user?id={target_id}'><b>{escape_html(full_name)}</b></a>"

    badges = []
    if getattr(target_user, 'bot', False): badges.append("🤖 Bot")
    if getattr(target_user, 'premium', False): badges.append("⭐ Premium")
    if getattr(target_user, 'verified', False): badges.append("✅ Verified")
    if getattr(target_user, 'scam', False): badges.append("🚫 Scam")
    if getattr(target_user, 'fake', False): badges.append("⚠️ Fake")
    if getattr(target_user, 'restricted', False): badges.append("🔒 Restricted")
    badge_line = f"\n🏷️ <b>Badges:</b> {' · '.join(badges)}" if badges else ""

    caption = (
        f"🪪 <b>USER PROFILE</b>\n"
        f"👤 <b>Name:</b> {mention}\n"
        f"🔗 <b>Username:</b> {username}\n"
        f"🆔 <b>User ID:</b> <code>{target_id}</code>\n"
        f"💬 <b>Chat ID:</b> <code>{event.chat_id}</code>"
        f"{badge_line}\n"
        f"<i>💡 Tap any ID above to copy it instantly.</i>"
    )
    # Best-effort profile photo — falls back to plain text if the user has none or it can't be fetched.
    try:
        photos = await bot1.get_profile_photos(target_id, limit=1)
    except Exception:
        photos = None
    if photos:
        try:
            await event.reply(file=photos[0], message=caption, parse_mode='html')
            return
        except Exception:
            pass
    await event.reply(caption, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'(?i)^[/.]info'))
async def info_handler(event):
    try:
        target_id, target_user = await resolve_info_target(event)
        if target_user:
            await render_profile_card(event, target_id, target_user)
    except Exception as e:
        print(f"Error in /info: {e}")

# ---- WELCOME / GOODBYE ----
@bot1.on(events.ChatAction)
async def welcome_goodbye(event):
    try:
        chat = await event.get_chat()
        bot_me = await bot1.get_me()
        group_name = chat.title or "Unknown Group"
        target_ids = event.user_ids or [event.user_id]
        is_broadcast_channel = bool(getattr(chat, 'broadcast', False) and not getattr(chat, 'megagroup', False))
        
        # ---------- BOT ADDED TO GROUP ----------
        if (event.user_added or event.user_joined) and any(uid == bot_me.id for uid in target_ids if uid):
            async with bot_added_locks[chat.id]:
                now_ts = time.time()
                last_ts = _recent_bot_added_chats.get(chat.id)
                if last_ts and (now_ts - last_ts) <= BOT_ADDED_DEDUP_WINDOW:
                    return
                _recent_bot_added_chats[chat.id] = now_ts
                
                # ✅ Member count ကို get_participants နဲ့ မှန်ကန်အောင် ရယူမယ်
                member_count = "Unknown"
                try:
                    # get_participants က total ကို ပြန်ပေးတယ်
                    participants = await bot1.get_participants(chat.id, limit=0)
                    if hasattr(participants, 'total'):
                        member_count = f"{participants.total:,}"
                    elif isinstance(participants, list):
                        member_count = f"{len(participants):,}"
                except Exception as e:
                    print(f"Member count error (get_participants): {e}")
                    # Fallback: get_full_chat ကို စမ်းကြည့်မယ်
                    try:
                        full_chat = await bot1.get_full_chat(chat.id)
                        if hasattr(full_chat, 'participants_count'):
                            member_count = f"{full_chat.participants_count:,}"
                    except Exception as e2:
                        print(f"Member count error (get_full_chat): {e2}")
                        member_count = "N/A"
                
                # ✅ Invite link ရယူမယ်
                group_link = None
                try:
                    invite = await bot1(ExportChatInviteRequest(chat.id))
                    group_link = invite.link if invite else None
                except Exception:
                    group_link = "Cannot fetch (need admin rights)"
                if not group_link:
                    group_link = "Not available (or bot not admin)"
                
                # 👤 Who actually added the bot — per owner request. get_added_by() is the
                # Telethon-recommended way (added_by the plain property can be None if it
                # wasn't already cached; the get_ variant makes an API call if it has to).
                added_by_mention = "someone"
                try:
                    adder = await event.get_added_by()
                    if adder:
                        adder_name = (f"{getattr(adder, 'first_name', '') or ''} "
                                       f"{getattr(adder, 'last_name', '') or ''}").strip() \
                                      or getattr(adder, 'username', '') or "someone"
                        added_by_mention = f"<a href='tg://user?id={adder.id}'>{escape_html(adder_name)}</a>"
                except Exception as e:
                    print(f"Added-by fetch error: {e}")

                # Owner ကို ပို့မယ့် Message
                # 🩹 REDESIGNED (per owner request): dropped the ━━━ divider and the rigid
                # "field: value" report layout for something that reads like a quick heads-up
                # rather than a generated log line — same info (name/ID/members/link/time),
                # now also naming who actually invited the bot.
                kind_word = "channel" if is_broadcast_channel else "group"
                owner_msg = (
                    f"🆕 <b>Just got added to a new {kind_word}!</b>\n\n"
                    f"<b>{escape_html(group_name)}</b> — added by {added_by_mention}\n"
                    f"👥 <code>{member_count}</code> members · 🆔 <code>{chat.id}</code>\n"
                    f"🔗 {escape_html(str(group_link))}\n\n"
                    f"<i>{datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}</i>"
                )
                await bot1.send_message(OWNER_ID, owner_msg, parse_mode='html')
                try:
                    await bot1.send_message(SPECIFIC_GROUP, owner_msg, parse_mode='html')
                except Exception:
                    pass
                # 🩹 ADDED (per owner request): now also posted to CHARACTER_CHANNEL_ID — the
                # same channel /addchar posts new characters to — not just the two private
                # notifications above. See also /syncgroups further down, which backfills this
                # same post for every group the bot was already in before this change shipped.
                if CHARACTER_CHANNEL_ID:
                    try:
                        await bot1.send_message(CHARACTER_CHANNEL_ID, owner_msg, parse_mode='html')
                    except Exception as e:
                        print(f"Bot-added channel post failed: {e}")
                
                # Channel ဆိုရင် ဒီမှာပဲ ရပ်မယ်
                if is_broadcast_channel:
                    return
                
                # Group ဆိုရင် intro message ပို့မယ် (ဖယ်ရှားချင်ရင် ဒီအောက်က ၅ ကြောင်းကို comment လုပ်ပါ)
                intro_msg = (
                    f"<blockquote><b>👾 Character Collector Bot</b> ကနေ ဒီ Group ထဲကို ရောက်ရှိလာပါပြီ!\n\n"
                    f"ကျွန်တော်ရဲ့ အဓိက ဂိမ်းက <b>Character များ ဖမ်းဆီးစုဆောင်းခြင်း (Collector Game)</b> "
                    f"ဖြစ်ပါတယ် — ဒီ Group ထဲမှာ Character အသစ်တွေ အခါအားလျော်စွာ ပေါ်လာမှာဖြစ်ပြီး၊ "
                    f"<code>/w</code>, <code>/waifu</code> or <code>/who</code> နဲ့ ဖော်ထုတ်ပြီး "
                    f"<code>/obtain [name]</code> နဲ့ ဖမ်းယူနိုင်ပါတယ်။ ဖမ်းရမိတဲ့ Character တွေကို "
                    f"<code>/harem</code> မှာ စုဆောင်းထားနိုင်ပါတယ်။\n\n"
                    f"အပြင် Casino ဂိမ်းအမျိုးမျိုးကိုလည်း ဒီ Group ထဲမှာ ဆော့ကစားနိုင်ပါပြီ — "
                    f"ဂိမ်းတွေ ဆော့ပြီး ⭐ Star ကို တကယ် ရယူနိုင်ပါတယ်။\n\n"
                    f"📌 <b>စတင်ကြည့်ရှုရန်:</b>\n"
                    f"   • <code>/introduce</code> - ကျွန်တော် လုပ်ဆောင်ပေးနိုင်တာတွေ အသေးစိတ် ကြည့်ရန်\n"
                    f"   • <code>/help</code> - Command အားလုံး ကြည့်ရန်\n\n"
                    f"🎮 ဂိမ်းဆော့ပြီး ⭐ Star ရယူနိုင်တာမို့၊ သင့် Group ကိုယ်ပိုင်မှာလည်း ဒီအကျိုးခံစားချက်တွေ "
                    f"ရစေချင်ရင် <b>@{bot_me.username}</b> ကို Add လုပ်ထားနိုင်ပါတယ်။\n\n"
                    f"✨ ကောင်းသောအချိန်ဖြစ်ပါစေ။</blockquote>"
                )
                await bot1.send_message(chat.id, intro_msg, parse_mode='html')
                await groups_col.update_one(
                    {"chat_id": chat.id},
                    {"$set": {"title": chat.title, "joined_at": datetime.now(TZ)}},
                    upsert=True
                )
                return
        
        # ---------- WELCOME & GOODBYE ကို လုံးဝ ဖယ်ရှားမယ် ----------
        # အောက်က ကျန်တဲ့ welcome/goodbye code တွေအကုန်ကို ဖယ်ရှားလိုက်မယ်
        # ဘယ်သူမှ join/leave လုပ်ရင် ဘာမှ မပို့တော့ဘူး
        return
        
    except Exception as e:
        await report_system_error("Welcome Goodbye Event", e)

# ---- MAU STATS ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]mau(?:@\w+)?$', 'bot1')))
async def system_analytics_matrix(event):
    if event.sender_id != OWNER_ID: return
    loading = await event.reply("<code>🌌 Pulling every tracked name...</code>", parse_mode='html')
    try:
        total_tracked_users = await users_col.count_documents({})
        total_active_groups = await groups_col.count_documents({})
        day_ago = datetime.now(TZ) - timedelta(days=1)
        active_24h_users = await users_col.count_documents({"last_active": {"$gte": day_ago}})
        metrics_layout = (
            f"<b>MAU Stats</b>\n"
            f"👥 <b>Total Managed Users:</b> <code>{total_tracked_users} 👤</code>\n"
            f"🔥 <b>Active Users (24H):</b> <code>{active_24h_users} 🔥</code>\n"
            f"📍 <b>Active Groups:</b> <code>{total_active_groups} 🪐</code>\n"
            f"⚙️ <b>Engine Status:</b> <code>Everything is under control.</code>"
        )
        await loading.edit(bq(metrics_layout), parse_mode='html')
    except Exception as e:
        await loading.edit(bq(f"❌ Analytics Error: {e}"), parse_mode='html')


# ==========================================
# SPAM FILTERS (Updated with spam mute)
# ==========================================
SPAM_MSG_WINDOW_SECONDS = 60
SPAM_MSG_THRESHOLD = 13       # text, sticker, or other media messages within the window
SPAM_CATCH_MUTE_SECONDS = 480  # 8 minutes — how long /obtain (and /who, /w, /waifu) stay
# blocked, GLOBALLY across every group the user is in (not just the group where they spammed).

@bot1.on(events.NewMessage)
async def spam_detection_and_mute(event):
    if event.is_private:
        return
    if event.sender_id in bot_ids or event.sender_id == OWNER_ID:
        return
    # Counts toward the spam threshold: text OR sticker OR any other media (photo, video, gif,
    # voice, document, etc.) — a burst of stickers to force a spawn is spamming just as much
    # as a burst of text, so both need to count toward the same window.
    if not event.text and not event.media:
        return
    user_id = event.sender_id
    chat_id = event.chat_id
    # 🩹 CHANGED (per owner request): the MUTE is now GLOBAL — user_mute_until is keyed by
    # user_id alone, so spamming in Group A now blocks /obtain and /who in EVERY group, not
    # just Group A. The spam-DETECTION window (what counts toward tripping the mute) stays
    # scoped per-group below — that's about WHERE the burst happened, a separate concern from
    # where the resulting penalty applies.
    spam_key = (user_id, chat_id)
    now = time.time()

    # Already muted from catching — GLOBALLY — block the relevant commands and stop; don't
    # let this message also start counting toward a fresh spam window.
    if user_id in user_mute_until and now < user_mute_until[user_id]:
        if _extract_command_word(event.text) in ['/obtain', '/who', '/w', '/waifu']:
            try:
                await event.delete()
            except:
                pass
        return

    # Track message history for this (user, group) pair — text, sticker, and media all count
    if spam_key not in user_spam_data:
        user_spam_data[spam_key] = []
    # Clean old entries (>60s)
    user_spam_data[spam_key] = [t for t in user_spam_data[spam_key] if now - t < SPAM_MSG_WINDOW_SECONDS]
    user_spam_data[spam_key].append(now)

    # Too many messages in this group's window -> block catching GLOBALLY for
    # SPAM_CATCH_MUTE_SECONDS (PREMIUM_SPAM_MUTE_SECONDS for Premium users)
    if len(user_spam_data[spam_key]) >= SPAM_MSG_THRESHOLD:
        mute_seconds = PREMIUM_SPAM_MUTE_SECONDS if await check_premium(user_id) else SPAM_CATCH_MUTE_SECONDS
        user_mute_until[user_id] = now + mute_seconds
        user_spam_data[spam_key] = []  # start clean once the mute expires, instead of carrying
        # over timestamps from the burst that just tripped it
        mute_minutes = mute_seconds // 60
        try:
            sender = await event.get_sender()
            mention = f"<a href='tg://user?id={user_id}'>{escape_html(sender.first_name)}</a>"
            await event.respond(
                bq(f"<b>Notice:</b> {mention}, that's enough. "
                   f"You've been blocked from /obtain and /who <b>in every group</b> for {mute_minutes} minutes. "
                   f"Sit still for a while."),
                parse_mode='html'
            )
        except Exception:
            pass
        # Delete the message that tripped the threshold
        try:
            await event.delete()
        except:
            pass
        return
            
# ---- ID ----
@bot1.on(events.NewMessage(pattern=r'(?i)^[/.]id'))
async def id_handler(event):
    try:
        target_id, target_user = await resolve_info_target(event)
        if target_user:
            await render_profile_card(event, target_id, target_user)
    except Exception as e:
        print(f"Error in /id: {e}")

# ---- Post a character (full details + image) to CHARACTER_CHANNEL_ID ----
def build_actor_mention(sender):
    """Clickable full-name mention (first + last name) for whoever ran the /addchar,
    /editchar, /addartist, or /change command — used in the channel caption's
    'Added by' / 'Updated by' line. Falls back to 'Owner' if Telegram has no name on file."""
    first = (getattr(sender, "first_name", "") or "").strip()
    last = (getattr(sender, "last_name", "") or "").strip()
    full_name = f"{first} {last}".strip() or "Owner"
    return f"<a href='tg://user?id={sender.id}'>{escape_html(full_name)}</a>"

def _build_character_channel_caption(char_doc, is_new=True, actor_mention=None):
    # 🩹 CHANGED (per owner request): back to a labeled field list — Name/Category/Id/Rarity/
    # Event/Catch limit each on their own line — plus who actually ran the command (Added by /
    # Updated by, full-name mention) and the 💠 VLT price. No
    # ━━━ divider lines anymore, and Rarity drops its trailing "No.X" (emoji + tier name only).
    limit_val = char_doc.get("spawn_limit")
    limit_text = "♾️ Unlimited" if not limit_val else str(limit_val)
    artist = char_doc.get("artist")
    name = escape_html(str(char_doc.get('name', '?')))
    category = escape_html(str(char_doc.get('category', '?')))
    rarity_display = strip_rarity_number(str(char_doc.get('rarity', '?')))
    # 🩹 2026-08 USD/MARKET RETIREMENT: currency_value now stores the same VLT figure as
    # vlt_price_for_char() (see RARITY_VLT_PRICE) — they used to be two independent numbers
    # (a USD "worth" and a separate Star "shop price"), but now that both collapsed onto the
    # one VLT price list, showing both would just repeat the same number twice with different
    # labels. Show it once, correctly labeled 💠 VLT.
    vlt_amount = vlt_price_for_char(char_doc)
    event_name = str(char_doc.get('event') or '').strip()
    char_id = escape_html(str(char_doc.get('char_id', '?')))

    header = f('NEW CARD JUST DROPPED') if is_new else f('CARD UPDATED')
    header_emoji = "🆕" if is_new else "🔄"
    actor_label = f('Added by') if is_new else f('Updated by')

    caption = f"{header_emoji} <b>{header}</b>\n\n"
    if actor_mention:
        caption += f"🤵 <b>{actor_label}:</b> {actor_mention}\n\n"
    caption += (
        f"<b>Name:</b> {name}\n"
        f"<b>Category:</b> {category}\n"
        f"<b>Id:</b> <code>{char_id}</code>\n"
        f"<b>Rarity:</b> {rarity_display}\n"
    )
    if event_name and event_name.lower() != "general":
        caption += f"<b>Event:</b> {escape_html(event_name)}\n"
    if artist:
        caption += f"<b>Artist:</b> {escape_html(str(artist))}\n"
    caption += (
        f"💠 {vlt_amount:,}\n"
        f"<b>Catch limit:</b> {limit_text}"
    )
    return caption

async def post_character_to_channel(char_doc, is_new=True, actor_mention=None):
    """Send one character's full details + image to CHARACTER_CHANNEL_ID.
    Raises on failure so callers (addchar) can catch and report it."""
    if not CHARACTER_CHANNEL_ID:
        raise RuntimeError("CHARACTER_CHANNEL_ID env var not set")
    storage_id = char_doc.get("storage_msg_id")
    if not storage_id:
        raise RuntimeError("character has no stored media (storage_msg_id missing)")
    storage_msg = await bot1.get_messages(SPECIFIC_CONTROL_GROUP, ids=storage_id)
    if not storage_msg or not storage_msg.media:
        raise RuntimeError("stored media not found (deleted from storage group?)")
    caption = _build_character_channel_caption(char_doc, is_new=is_new, actor_mention=actor_mention)
    return await bot1.send_file(CHARACTER_CHANNEL_ID, file=storage_msg.media, caption=caption, parse_mode='html')

# ---- /purgechannelposts — OWNER ONLY: deletes EVERY previously-posted character-announcement
# message from CHARACTER_CHANNEL_ID (using each character's stored channel_msg_id), then clears
# that field on every character so the DB no longer thinks anything's been posted. Run this
# BEFORE switching CHARACTER_CHANNEL_ID to a new channel — it deletes from whatever channel is
# CURRENTLY configured. ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]purgechannelposts(?:@\w+)?(?:\s+(confirm))?$', 'bot1')))
async def purge_channel_posts_handler(event):
    if event.sender_id != OWNER_ID: return
    confirm = bool(event.pattern_match.group(1))
    posted = await characters_base_col.find({"channel_msg_id": {"$exists": True, "$ne": None}}, {"char_id": 1, "channel_msg_id": 1}).to_list(length=None)
    if not posted:
        return await event.reply("📭 <b>ဖျက်ရန် Channel Post မရှိပါ</b> — Character ဘယ်ခုမှ Channel Message ID မှတ်ထားခြင်း မရှိပါ။", parse_mode='html')
    if not confirm:
        return await event.reply(
            f"⚠️ <b>Channel Post <code>{len(posted):,}</code> ခု ဖျက်တော့မလား?</b>\n"
            f"🎯 <b>Target Channel:</b> <code>{CHARACTER_CHANNEL_ID}</code> (လက်ရှိ Configure ထားတာ)\n\n"
            f"<i>ဒါက Channel ထဲက Post များကိုသာ ဖျက်တာပါ — Character Database ကို မထိခိုက်ပါဘူး။ "
            f"Channel ပြောင်းချင်ရင် ဒါကို ပထမဆုံး Run ပြီးမှ CHARACTER_CHANNEL_ID ကို ပြောင်းပါ။</i>\n\n"
            f"အတည်ပြုရန် <code>/purgechannelposts confirm</code> ကို ရိုက်ပါ။",
            parse_mode='html'
        )
    status_msg = await event.reply(f"🗑️ <b>Channel Post {len(posted):,} ခု ဖျက်နေပါသည်...</b>", parse_mode='html')
    msg_ids = [p["channel_msg_id"] for p in posted if p.get("channel_msg_id")]
    deleted, failed = 0, 0
    # delete_messages accepts up to 100 IDs per call — batch it, with pacing to stay flood-safe
    for i in range(0, len(msg_ids), 100):
        batch = msg_ids[i:i + 100]
        try:
            await bot1.delete_messages(CHARACTER_CHANNEL_ID, batch)
            deleted += len(batch)
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 2)
            try:
                await bot1.delete_messages(CHARACTER_CHANNEL_ID, batch)
                deleted += len(batch)
            except Exception:
                failed += len(batch)
        except Exception as ce:
            await report_system_error("purgechannelposts batch delete", ce)
            failed += len(batch)
        await asyncio.sleep(1)
    await characters_base_col.update_many({}, {"$unset": {"channel_msg_id": "", "channel_posted": ""}})
    await status_msg.edit(
        f"✅ <b>Channel Post ရှင်းလင်းပြီးပါပြီ!</b>\n"
        f"🗑️ <b>ဖျက်ပြီး:</b> <code>{deleted:,}</code>  │  ❌ <b>မဖျက်နိုင်:</b> <code>{failed:,}</code>\n\n"
        f"<i>Channel အသစ်ကို သုံးမယ်ဆိုရင် CHARACTER_CHANNEL_ID (Render Env Var) ကို ယခုပြောင်းနိုင်ပါပြီ၊ "
        f"ပြီးရင် /repostallchars ကို Run ပါ။</i>",
        parse_mode='html'
    )

# ---- /repostallchars — OWNER ONLY: posts EVERY existing character in the database to
# CHARACTER_CHANNEL_ID (whatever it's CURRENTLY configured to — run this AFTER switching to the
# new channel). Flood-safe paced, updates channel_msg_id on each character as it goes. ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]repostallchars(?:@\w+)?(?:\s+(confirm))?$', 'bot1')))
async def repost_all_characters_handler(event):
    if event.sender_id != OWNER_ID: return
    if not CHARACTER_CHANNEL_ID:
        return await event.reply("❌ <b>CHARACTER_CHANNEL_ID</b> configure မထားပါ။", parse_mode='html')
    confirm = bool(event.pattern_match.group(1))
    total = await characters_base_col.count_documents({})
    if not total:
        return await event.reply("📭 <b>Database ထဲမှာ Character လုံးဝ မရှိသေးပါ။</b>", parse_mode='html')
    est_minutes = round(total * 3 / 60, 1)
    if not confirm:
        return await event.reply(
            f"⚠️ <b>Character <code>{total:,}</code> ခုလုံးကို Channel <code>{CHARACTER_CHANNEL_ID}</code> ဆီ Repost လုပ်တော့မလား?</b>\n"
            f"⏱ <b>ခန့်မှန်းအချိန်:</b> <code>~{est_minutes}</code> မိနစ် (Flood-safe pacing ကြောင့်)\n\n"
            f"အတည်ပြုရန် <code>/repostallchars confirm</code> ကို ရိုက်ပါ။",
            parse_mode='html'
        )
    status_msg = await event.reply(f"📢 <b>Character {total:,} ခုကို Channel ဆီ Post လုပ်နေပါသည်...</b>\n<code>0/{total}</code>", parse_mode='html')
    posted, failed = 0, 0
    async for char_doc in characters_base_col.find():
        try:
            channel_msg = await post_character_to_channel(char_doc, is_new=False)
            await characters_base_col.update_one(
                {"char_id": char_doc["char_id"]},
                {"$set": {"channel_msg_id": channel_msg.id if channel_msg else None, "channel_posted": True}}
            )
            posted += 1
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 2)
            try:
                channel_msg = await post_character_to_channel(char_doc, is_new=False)
                await characters_base_col.update_one(
                    {"char_id": char_doc["char_id"]},
                    {"$set": {"channel_msg_id": channel_msg.id if channel_msg else None, "channel_posted": True}}
                )
                posted += 1
            except Exception:
                failed += 1
        except Exception as ce:
            await report_system_error(f"repostallchars ({char_doc.get('char_id')})", ce)
            failed += 1
        if (posted + failed) % 20 == 0:
            try:
                await status_msg.edit(f"📢 <b>Character {total:,} ခုကို Channel ဆီ Post လုပ်နေပါသည်...</b>\n<code>{posted + failed}/{total}</code>", parse_mode='html')
            except Exception:
                pass
        await asyncio.sleep(3)  # flood-safe pacing
    await status_msg.edit(
        f"✅ <b>Repost ပြီးပါပြီ!</b>\n"
        f"📢 <b>Post ပြီး:</b> <code>{posted:,}</code>  │  ❌ <b>မအောင်မြင်:</b> <code>{failed:,}</code>",
        parse_mode='html'
    )

# ---- Telegram/Telethon occasionally redeliver the same update more than once — we've hit
# this for ChatAction events (bot-added-to-group) and for the spawn-trigger counter; it can
# just as easily double-fire a plain command handler like /addchar (which is what caused
# characters to get forwarded/posted 2-3x). Any handler with a real side effect (DB write,
# channel post, forwarding media) should call is_duplicate_event(event) first and bail if True.
_recent_event_ids = bot_state._recent_event_ids  # (chat_id, event.id) -> timestamp
EVENT_DEDUP_WINDOW = 15  # seconds

def is_duplicate_event(event):
    key = (event.chat_id, event.id)
    now = time.time()
    last_ts = _recent_event_ids.get(key)
    _recent_event_ids[key] = now
    if len(_recent_event_ids) > 5000:  # opportunistic cleanup, keeps this from growing forever
        cutoff = now - EVENT_DEDUP_WINDOW
        for k, ts in list(_recent_event_ids.items()):
            if ts < cutoff:
                del _recent_event_ids[k]
    return last_ts is not None and (now - last_ts) <= EVENT_DEDUP_WINDOW

# ---- ADD CHARACTER (owner-only bot2) ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]addchar(?:@\w+)?(?:\s+(.+))?', 'bot1')))
async def add_character(event):
    if event.sender_id != OWNER_ID: return
    if is_duplicate_event(event): return
    input_text = event.pattern_match.group(1)
    if not input_text or '|' not in input_text:
        await event.reply(
            f"⚠️ <b>{f('Invalid format!')}</b>\n"
            f"📌 <b>{f('Usage')}:</b>\n"
            f"<code>/addchar Name | Category | Rarity_Number | Event | CatchLimit</code>\n"
            f"<i>(Reply to a media file. Event and CatchLimit are optional.)</i>\n\n"
            f"🔢 <b>{f('Rarity Tiers (1-9)')}:</b>\n"
            + "\n".join(
                f"<code>{num}</code> = {RARITY_NUM_MAP[num]['name']} (<code>{RARITY_NUM_MAP[num]['value']}</code>💠)"
                for num in sorted(RARITY_NUM_MAP.keys())
            ) + "\n\n"
            f"🎪 <b>Event:</b> free text, e.g. <code>Summer Festival</code> (leave blank/'-' for none)\n"
            f"🔁 <b>CatchLimit:</b> how many times total this character may be caught (0 or blank = infinite)",
            parse_mode='html'
        )
        return
    parts = [p.strip() for p in input_text.split('|')]
    if len(parts) < 3:
        return await event.reply(f"❌ <b>{f('Need 3 parts separated by |')}</b>", parse_mode='html')
    char_name, category_name, rarity_num = parts[0], parts[1], parts[2]
    if rarity_num not in RARITY_NUM_MAP:
        return await event.reply(f"❌ <b>{f('Rarity must be 1-9')}</b>", parse_mode='html')
    if not event.is_reply:
        return await event.reply(f"❌ <b>{f('Reply to a media file')}</b>", parse_mode='html')
    event_name = parts[3].strip() if len(parts) > 3 and parts[3].strip() and parts[3].strip() != "-" else "General"
    spawn_limit = 0
    if len(parts) > 4 and parts[4].strip().lstrip('-').isdigit():
        spawn_limit = max(0, int(parts[4].strip()))
    elif rarity_num == "1":
        # 🎯 ULTRA SCARCITY CAP — every ULTRA character gets this catch limit by default
        # unless the owner explicitly gives a different CatchLimit above (0 is still
        # available as an explicit override for a genuinely uncapped ULTRA, if ever wanted —
        # see ULTRA_DEFAULT_CATCH_LIMIT's docstring).
        spawn_limit = ULTRA_DEFAULT_CATCH_LIMIT
    reply_msg = await event.get_reply_message()
    if not reply_msg or not (reply_msg.photo or reply_msg.video or reply_msg.document):
        return await event.reply(f"❌ <b>{f('Valid media not found')}</b>", parse_mode='html')
    is_video_media = bool(
        reply_msg.video or
        (reply_msg.document and (reply_msg.document.mime_type or "").startswith("video/"))
    )
    # 🐉 Rarity No.1 is exclusively for video characters, and videos may ONLY be
    # rarity No.1 — keeps the top tier a clean, dedicated "video" rarity going forward.
    if rarity_num == "1" and not is_video_media:
        return await event.reply(
            f"❌ <b>{f(f'Rarity 1 ({RARITY_TIERS[0]}) is reserved for video characters only. Pick 2-9 for a photo.')}</b>",
            parse_mode='html'
        )
    if is_video_media and rarity_num != "1":
        return await event.reply(
            f"❌ <b>{f(f'This is a video — it must be Rarity 1 ({RARITY_TIERS[0]}).')}</b>",
            parse_mode='html'
        )
    # 🩹 FIX: large videos (the owner's own diagnosis was spot-on) could make /addchar go
    # completely silent. Two compounding causes:
    #  1. Telegram bots have a hard ~2000MB send/forward ceiling — a file over that was
    #     always going to fail, but nothing checked for it up front, so the failure only
    #     surfaced deep inside the network call with a cryptic Telegram error string.
    #  2. Forwarding an older/larger message's media can hit FileReferenceExpiredError
    #     (Telegram invalidates file references after a while), and — separately — a slow
    #     upload of a big file could hang past whatever the underlying connection tolerates.
    #     Both of those are real exceptions and SHOULD have been caught by the `except
    #     Exception` below... except large transfers are exactly the case most likely to be
    #     interrupted by asyncio.CancelledError, which is a BaseException in Python 3.8+ and
    #     slips straight through an `except Exception` clause — so the command would die
    #     with zero message ever reaching the owner. Fixed by: checking size up front,
    #     capping how long we wait, retrying once on an expired reference, and explicitly
    #     catching cancellation/timeout too so every failure path reports back.
    ADDCHAR_MAX_BYTES = 2000 * 1024 * 1024  # 2000MB — Telegram's own bot upload ceiling
    ADDCHAR_UPLOAD_TIMEOUT = 240  # seconds — generous for a big video, but never infinite
    media_size = getattr(getattr(reply_msg, "file", None), "size", None)
    if media_size and media_size > ADDCHAR_MAX_BYTES:
        return await event.reply(
            f"❌ <b>{f('File too large')}</b> "
            f"(<code>{media_size / 1024 / 1024:.1f}MB</code>) — Telegram bots can't send files "
            f"over <code>2000MB</code>. Compress it or trim the clip and try again.",
            parse_mode='html'
        )
    status_msg = None
    try:
        # 🩹 FIX: acknowledge receipt IMMEDIATELY, before the network calls (forwarding media
        # to storage, posting to the channel) that can take a moment or occasionally queue
        # behind a flood wait. Without this, /addchar could look completely silent for the
        # whole time those calls were in flight — now there's always instant confirmation
        # that the command was received and is being processed.
        status_msg = await event.reply("⏳ <b>Adding character...</b>", parse_mode='html')
        try:
            # 🩹 Uses bot2 here, not bot1 — reply_msg.media was fetched through bot2's own
            # connection (bot2 received this event), so its file_reference is only valid
            # for bot2's session. Passing it to a different client instance would fail.
            forwarded_msg = await asyncio.wait_for(
                send_safe_message(bot1, SPECIFIC_CONTROL_GROUP, "", file=reply_msg.media),
                timeout=ADDCHAR_UPLOAD_TIMEOUT
            )
        except errors.FileReferenceExpiredError:
            # Reference went stale (common with older or larger media) — refetch the
            # original message once to get a fresh reference, then retry a single time.
            fresh_reply_msg = await bot1.get_messages(event.chat_id, ids=reply_msg.id)
            if not fresh_reply_msg or not fresh_reply_msg.media:
                raise RuntimeError("media reference expired and the original message is no longer available")
            reply_msg = fresh_reply_msg
            forwarded_msg = await asyncio.wait_for(
                send_safe_message(bot1, SPECIFIC_CONTROL_GROUP, "", file=reply_msg.media),
                timeout=ADDCHAR_UPLOAD_TIMEOUT
            )
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"upload timed out after {ADDCHAR_UPLOAD_TIMEOUT}s — the file may be too large "
                f"or Telegram is slow right now, try a smaller file or retry"
            )
        storage_id = forwarded_msg.id
        r_info = RARITY_NUM_MAP[rarity_num]
        char_id = f"BOD{random.randint(1, 9999)}"
        while await characters_base_col.find_one({"char_id": char_id}):
            char_id = f"BOD{random.randint(1, 9999)}"
        # 🔎 Fingerprint the media so a re-uploaded/re-saved copy of it can later be
        # recognized by identify_from_repost_handler even without a live spawn/reply.
        photo_phash = await compute_phash_for_message(reply_msg)
        character_data = {
            "char_id": char_id,
            "name": char_name,
            "category": category_name,
            "rarity": r_info["name"],
            "rarity_tier": classify_rarity(r_info["name"]),
            "storage_msg_id": storage_id,
            "currency_value": r_info["value"],
            "spawn_count": 0,
            "event": event_name,
            "spawn_limit": spawn_limit,
            "photo_phash": photo_phash,
            "created_at": time.time()  # 🔒 starts the NEW_CARD_PROTECTION_SECONDS window — see execute_star_shop_purchase()
        }
        await characters_base_col.insert_one(character_data)
        await invalidate_character_caches()
        limit_text = "♾️ Infinite" if spawn_limit == 0 else str(spawn_limit)

        channel_note = ""
        if CHARACTER_CHANNEL_ID:
            try:
                actor_mention = build_actor_mention(await event.get_sender())
                channel_msg = await post_character_to_channel(character_data, is_new=True, actor_mention=actor_mention)
                await characters_base_col.update_one(
                    {"char_id": char_id},
                    {"$set": {"channel_posted": True, "channel_msg_id": channel_msg.id if channel_msg else None}}
                )
                channel_note = "\n📢 <b>Channel:</b> Posted ✅"
            except Exception as ce:
                await report_system_error(f"AddChar channel post ({char_id})", ce)
                channel_note = f"\n⚠️ <b>Channel:</b> Post failed — <code>{escape_html(str(ce))}</code>"
        else:
            channel_note = "\n⚠️ <b>Channel:</b> <code>CHARACTER_CHANNEL_ID</code> not set — skipped."

        # 🩹 NEW (per owner question): Telegram itself compresses/downscales any image sent as
        # a "Photo" (not the bot's doing — it happens the instant it's uploaded, before the bot
        # ever sees it) — that compressed version is then what gets stored and re-sent forever
        # after, which is why spawns can look noticeably softer than the original 4K source
        # until you tap to view full-size. Sending as a "File" instead (already supported —
        # reply_msg.document was accepted above) skips Telegram's compression entirely and
        # keeps full original quality permanently. Surface which path was used right here so
        # it's obvious immediately, not discovered later from a blurry spawn.
        quality_note = (
            "\n🗜️ <b>Image sent as compressed Photo</b> — Telegram downscaled it on upload. "
            "For full original quality, reply with the image sent as a <b>File</b> instead."
            if reply_msg.photo else
            "\n✅ <b>Full quality preserved</b> (sent as File/Video)."
        )
        await status_msg.edit(
            f"🔥 <b>{f('DATABASE INJECTED')}</b>\n"
            f"🆔 <b>{f('Character ID')}:</b> <code>{char_id}</code>\n"
            f"👤 <b>{f('Name')}:</b> <code>{char_name}</code>\n"
            f"🏷️ <b>{f('Rarity')}:</b> {r_info['name']}\n"
            f"💎 <b>{f('Worth')}:</b> <code>{r_info['value']}💠</code>\n"
            f"🎡 <b>Event:</b> <code>{escape_html(event_name)}</code>\n"
            f"🔁 <b>Catch Limit:</b> <code>{limit_text}</code>"
            f"{channel_note}"
            f"{quality_note}",
            parse_mode='html'
        )
    except asyncio.CancelledError:
        # CancelledError is a BaseException (not Exception) in Python 3.8+, so it would
        # otherwise skip straight past the `except Exception` below and leave the owner
        # with zero feedback — exactly the "silent" failure reported. Report, then re-raise
        # so real cancellation (e.g. shutdown) still propagates correctly.
        try:
            err_text = "❌ <b>Database Inject Error</b>: <code>upload was cancelled/interrupted before finishing — please retry</code>"
            if status_msg:
                await status_msg.edit(err_text, parse_mode='html')
            else:
                await event.reply(err_text, parse_mode='html')
        except Exception:
            pass
        raise
    except Exception as e:
        err_text = f"❌ <b>Database Inject Error</b>: <code>{escape_html(str(e))}</code>"
        try:
            if status_msg:
                await status_msg.edit(err_text, parse_mode='html')
            else:
                await event.reply(err_text, parse_mode='html')
        except Exception:
            await event.reply(err_text, parse_mode='html')

# ==========================================
# 🚫 GLOBAL BAN — owner-only, two-step reply wizard (reason, then duration) mirroring /addle's
# pattern. Confiscates the target's ENTIRE harem, 💠 VLT, and ⭐ Star (currency credited to the
# Owner's own balance; cards are just wiped — the Owner already has unlimited vault access and
# doesn't need literal harem entries), then blocks them from catching — globally, in every
# group — for the given duration. Reuses user_mute_until (the same in-memory mute catch_handler
# already checks) for IMMEDIATE effect, but the source of truth is gban_col: see
# load_active_gbans_cache() near startup, which re-populates user_mute_until from any still-
# active ban on every restart, so a reboot can never accidentally lift a ban early (this
# matters most for "perm" bans — see GBAN_DURATION_HELP).
# ==========================================
GBAN_DURATION_HELP = (
    "<i>Examples:</i> <code>30m</code> (30 minutes), <code>24h</code> (24 hours), "
    "<code>7d</code> (7 days), <code>2w</code> (2 weeks), <code>perm</code> (permanent)"
)

def parse_gban_duration(raw):
    """Returns (expires_at, display_label) on success — expires_at=None means permanent — or
    (False, None) if `raw` couldn't be parsed at all. Accepts <number><unit> with unit m/h/d/w,
    or the literal word perm/permanent (case-insensitive, whitespace-tolerant)."""
    raw = (raw or "").strip().lower()
    if raw in ("perm", "permanent"):
        return None, "Permanent"
    m = re.match(r'^(\d+)\s*(m|h|d|w)$', raw)
    if not m:
        return False, None
    amount, unit = int(m.group(1)), m.group(2)
    if amount <= 0:
        return False, None
    seconds_per_unit = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    unit_label = {"m": "minute", "h": "hour", "d": "day", "w": "week"}
    expires_at = time.time() + amount * seconds_per_unit[unit]
    label = f"{amount} {unit_label[unit]}{'s' if amount != 1 else ''}"
    return expires_at, label

async def load_active_gbans_cache():
    """Restores user_mute_until for every still-active, not-yet-expired gban on boot — without
    this, a bot restart would silently un-ban everyone (including 'permanent' bans), since
    user_mute_until itself is in-memory only. gban_col (checked here) is the durable source of
    truth; user_mute_until is just the fast, no-DB-read-per-message enforcement layer."""
    try:
        now = time.time()
        active = await gban_col.find({"active": True}).to_list(length=None)
        restored = 0
        for ban in active:
            expires_at = ban.get("expires_at")
            if expires_at is not None and expires_at <= now:
                continue  # expired while the bot was down — leave it inactive-in-effect, /ungban can still formally close it out
            user_mute_until[ban["user_id"]] = expires_at if expires_at is not None else float('inf')
            restored += 1
        if restored:
            print(f"🚫 Restored {restored} active gban(s) into memory.")
    except Exception as e:
        print(f"load_active_gbans_cache error: {e}")

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]gban(?:@\w+)?(?:\s+(\S+))?$', 'bot1')))
async def gban_prompt(event):
    if event.sender_id != OWNER_ID: return
    target_id = None
    if event.is_reply:
        reply_msg = await event.get_reply_message()
        if reply_msg:
            target_id = reply_msg.sender_id
    else:
        arg = event.pattern_match.group(1)
        if arg and arg.strip().lstrip('-').isdigit():
            target_id = int(arg.strip())
    if not target_id:
        return await event.reply(
            "📌 <b>Usage:</b> <code>/gban [user_id]</code>, or reply to their message with <code>/gban</code>.",
            parse_mode='html'
        )
    if target_id == OWNER_ID:
        return await event.reply("⚠️ You can't gban yourself.", parse_mode='html')
    target_name = await get_plain_name(event, target_id)
    sent = await event.reply(
        f"🚫 <b>Global Ban — Step 1/2</b>\n"
        f"🎯 <b>Target:</b> <code>{target_id}</code> ({escape_html(target_name)})\n\n"
        f"↩️ <b>Reply to this message</b> with the reason.",
        parse_mode='html'
    )
    pending_gban_prompt[sent.id] = {"stage": "reason", "target_id": target_id}

@bot1.on(events.NewMessage(incoming=True))
async def gban_apply(event):
    if event.sender_id != OWNER_ID: return
    if not event.is_reply: return
    if event.reply_to_msg_id not in pending_gban_prompt: return
    state = pending_gban_prompt.pop(event.reply_to_msg_id)
    raw = (event.text or "").strip()
    if not raw:
        return await event.reply("⚠️ Reply with actual text.", parse_mode='html')

    if state["stage"] == "reason":
        sent = await event.reply(
            f"🚫 <b>Global Ban — Step 2/2</b>\n"
            f"📝 <b>Reason:</b> {escape_html(raw)}\n\n"
            f"↩️ <b>Reply to this message</b> with the duration.\n{GBAN_DURATION_HELP}",
            parse_mode='html'
        )
        pending_gban_prompt[sent.id] = {"stage": "duration", "target_id": state["target_id"], "reason": raw}
        return

    # stage == "duration"
    target_id = state["target_id"]
    reason = state["reason"]
    expires_at, duration_label = parse_gban_duration(raw)
    if expires_at is False:
        return await event.reply(f"⚠️ Couldn't read that duration.\n{GBAN_DURATION_HELP}", parse_mode='html')

    target_doc = await users_catcher_col.find_one({"user_id": target_id})
    confiscated_cards = len((target_doc or {}).get("harem", []))
    confiscated_vlt = (target_doc or {}).get("vlt_balance", 0)
    confiscated_star = (target_doc or {}).get("star_balance", 0)

    await users_catcher_col.update_one(
        {"user_id": target_id},
        {"$set": {"harem": [], "vlt_balance": 0, "star_balance": 0}},
        upsert=True
    )
    if confiscated_vlt or confiscated_star:
        await users_catcher_col.update_one(
            {"user_id": OWNER_ID},
            {"$inc": {"vlt_balance": confiscated_vlt, "star_balance": confiscated_star}},
            upsert=True
        )

    # Immediate effect (in-memory) + durable record (gban_col, restored on every restart —
    # see load_active_gbans_cache).
    user_mute_until[target_id] = expires_at if expires_at is not None else float('inf')
    await gban_col.insert_one({
        "user_id": target_id,
        "reason": reason,
        "banned_by": OWNER_ID,
        "banned_at": time.time(),
        "expires_at": expires_at,
        "duration_label": duration_label,
        "confiscated_cards": confiscated_cards,
        "confiscated_vlt": confiscated_vlt,
        "confiscated_star": confiscated_star,
        "active": True,
    })

    target_name = await get_plain_name(event, target_id)
    await event.reply(
        f"🚫 <b>GLOBAL BAN APPLIED</b>\n"
        f"🎯 <b>Target:</b> <code>{target_id}</code> ({escape_html(target_name)})\n"
        f"📝 <b>Reason:</b> {escape_html(reason)}\n"
        f"⏳ <b>Duration:</b> {duration_label}\n\n"
        f"💰 <b>Confiscated (now yours):</b> {confiscated_cards} card(s), "
        f"{format_vlt_plain(confiscated_vlt)} VLT, {format_star_plain(confiscated_star)} Star\n"
        f"🔒 Blocked from catching in every group until this lifts.",
        parse_mode='html'
    )

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]ungban(?:@\w+)?\s+(\d+)$', 'bot1')))
async def ungban_handler(event):
    if event.sender_id != OWNER_ID: return
    target_id = int(event.pattern_match.group(1))
    user_mute_until.pop(target_id, None)
    result = await gban_col.update_many(
        {"user_id": target_id, "active": True},
        {"$set": {"active": False, "lifted_at": time.time(), "lifted_by": OWNER_ID}}
    )
    if result.modified_count:
        await event.reply(f"✅ <b>Global ban lifted for</b> <code>{target_id}</code>. They can catch again.", parse_mode='html')
    else:
        await event.reply(f"ℹ️ <code>{target_id}</code> had no active gban on record, but any catch-block has been cleared anyway.", parse_mode='html')

# ---- ADD LIMITED EDITION: owner-only, two-step reply wizard (mirrors /editchar's pattern —
# see pending_editchar_prompt_ids above) that TAGS existing characters (already created via
# /addchar) as Limited Edition. It never creates new characters or touches media — only
# /addchar does that. Tagging a char_id sets is_limited_edition=True, le_name, le_daily_vlt
# on its characters_base_col doc, and (optionally) its spawn_limit — see
# _check_daily_first_message_rewards() near the Premium gift code for the daily opt-in 💠 VLT
# claim button this powers, and build_character_check_text() for where /check shows a tagged
# card's LE info. ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]addle(?:@\w+)?(?:\s+(.+))?$', 'bot1')))
async def add_limited_edition_prompt(event):
    if event.sender_id != OWNER_ID: return
    raw_args = event.pattern_match.group(1)

    # 🩹 NEW (per owner request — LE fragmentation fix): /addle #<batch_number> <CharID(s)...>
    # skips straight to Step 2 (CatchLimit | VLT/day) reusing an EXISTING batch's exact
    # le_name, so adding more cards to a batch never means retyping its name — retyping is
    # exactly what fragmented batches in the first place ("Epic Moment 1" vs "Epic Moment1").
    # The '#' is mandatory and deliberate, not decorative: a bare number ("/addle 1 5920")
    # would be genuinely ambiguous with char_id "1" itself, since this bot's char IDs are
    # plain numbers too (see normalize_char_id_input) — '#' never appears in a char_id, so
    # "#1" can only ever mean "batch number 1," never a card.
    if raw_args:
        tokens = raw_args.strip().split()
        m = re.match(r'^#(\d+)$', tokens[0]) if tokens else None
        if m:
            batch_number = int(m.group(1))
            le_name = await get_le_name_by_batch_number(batch_number)
            if not le_name:
                existing = await le_batches_col.find().sort("batch_number", 1).to_list(length=None)
                listing = ", ".join(f"#{b['batch_number']} {bookend_emoji_name(b['le_name'])}" for b in existing) or "(none yet)"
                return await event.reply(
                    f"❌ <b>Batch #{batch_number} doesn't exist.</b>\n📋 <b>Current batches:</b> {escape_html(listing)}",
                    parse_mode='html'
                )
            char_ids = [normalize_char_id_input(x) for x in tokens[1:] if x.strip()]
            if not char_ids:
                return await event.reply(
                    f"⚠️ <b>Give at least one CharID after the batch number.</b>\n"
                    f"<i>Usage:</i> <code>/addle #{batch_number} CharID1 CharID2 ...</code>",
                    parse_mode='html'
                )
            missing = [display_char_id(c) for c in char_ids if not await characters_base_col.find_one({"char_id": c})]
            if missing:
                return await event.reply(
                    f"❌ <b>Not found:</b> <code>{', '.join(missing)}</code>\n"
                    f"Add {'it' if len(missing) == 1 else 'them'} with /addchar first, then retry.",
                    parse_mode='html'
                )
            text = (
                f"🎗️ <b>Adding to Batch #{batch_number} — {escape_html(bookend_emoji_name(le_name))}</b>\n"
                f"🆔 <b>CharID(s):</b> <code>{', '.join(display_char_id(c) for c in char_ids)}</code>\n\n"
                f"↩️ <b>Reply to this message</b> with:\n<code>CatchLimit | VLT/day</code>\n"
                f"<i>CatchLimit: total copies ever obtainable (0/- = infinite). "
                f"VLT/day: 💠 paid per owned copy per day (- = default 1).</i>"
            )
            sent = await event.reply(text, parse_mode='html')
            pending_addle_prompt[sent.id] = {"stage": "details", "char_ids": char_ids, "le_name": le_name}
            return

    raw_ids = raw_args
    char_ids = None
    if raw_ids:
        char_ids = [normalize_char_id_input(x) for x in re.split(r'[,\s]+', raw_ids.strip()) if x.strip()]
        if not char_ids:
            char_ids = None
    if char_ids:
        missing = [display_char_id(c) for c in char_ids if not await characters_base_col.find_one({"char_id": c})]
        if missing:
            return await event.reply(
                f"❌ <b>Not found:</b> <code>{', '.join(missing)}</code>\n"
                f"Add {'it' if len(missing) == 1 else 'them'} with /addchar first, then retry /addle.",
                parse_mode='html'
            )
    ids_note = (
        f"🆔 <b>CharID(s):</b> <code>{', '.join(display_char_id(c) for c in char_ids)}</code>\n"
        if char_ids else
        "<i>(No CharID given yet — you'll be asked for it/them next.)</i>\n"
    )
    text = (
        f"🎗️ <b>New Limited Edition — Step 1/2</b>\n{ids_note}\n"
        f"↩️ <b>Reply to this message</b> with the Limited Edition's <b>name</b>\n"
        f"<i>e.g. <code>Anniversary LE 2026</code></i>\n\n"
        f"💡 <i>Adding to a batch that already exists? Use "
        f"<code>/addle #&lt;batch number&gt; CharID(s)</code> instead — skips this step "
        f"entirely and reuses that batch's exact name.</i>"
    )
    sent = await event.reply(text, parse_mode='html')
    pending_addle_prompt[sent.id] = {"stage": "name", "char_ids": char_ids}

@bot1.on(events.NewMessage(incoming=True))
async def add_limited_edition_apply(event):
    if event.sender_id != OWNER_ID: return
    if not event.is_reply: return
    if event.reply_to_msg_id not in pending_addle_prompt: return
    state = pending_addle_prompt.pop(event.reply_to_msg_id)
    raw = (event.text or "").strip()
    if not raw:
        return await event.reply("⚠️ Reply with actual text.", parse_mode='html')

    if state["stage"] == "name":
        le_name = raw
        char_ids = state["char_ids"]
        # 🩹 NEW (per owner request): LE batch names are expected to start with a leading
        # emoji (e.g. "🔱GODLIKE EDITION") — it's echoed at the END too everywhere the name is
        # shown (/check, /harem, LE gallery — see bookend_emoji_name()), so a name with no
        # emoji at all would display bare with nothing to bookend. Re-prompt instead of
        # silently accepting one, rather than saving a name that'll always look incomplete.
        if not extract_leading_emoji(le_name):
            text = (
                f"⚠️ <b>Name needs a leading emoji</b> — e.g. <code>🔱GODLIKE EDITION</code>.\n"
                f"It gets echoed at the end automatically everywhere this name is shown, so it "
                f"needs one to bookend in the first place.\n\n"
                f"↩️ <b>Reply to this message</b> with the name again, emoji included."
            )
            sent = await event.reply(text, parse_mode='html')
            pending_addle_prompt[sent.id] = {"stage": "name", "char_ids": char_ids}
            return
        if char_ids:
            fmt_hint = f"{', '.join(display_char_id(c) for c in char_ids)} | CatchLimit | VLT/day"
            ids_extra = ""
        else:
            fmt_hint = "CharID(s) | CatchLimit | VLT/day"
            ids_extra = "CharID(s): comma-separated for multiple, e.g. BOD1234,BOD5678. "
        text = (
            f"🎗️ <b>New Limited Edition — Step 2/2</b>\n"
            f"🏷️ <b>Name:</b> <code>{escape_html(bookend_emoji_name(le_name))}</code>\n\n"
            f"↩️ <b>Reply to this message</b> with:\n"
            f"<code>{fmt_hint}</code>\n"
            f"<i>{ids_extra}"
            f"CatchLimit: total copies ever obtainable (0/- = infinite). "
            f"VLT/day: 💠 paid per owned copy per day (- = default 1).</i>"
        )
        sent = await event.reply(text, parse_mode='html')
        pending_addle_prompt[sent.id] = {"stage": "details", "char_ids": char_ids, "le_name": le_name}
        return

    # stage == "details"
    le_name = state["le_name"]
    char_ids = state["char_ids"]
    parts = [p.strip() for p in raw.split('|')]
    if char_ids:
        if len(parts) < 2:
            return await event.reply("⚠️ Format: <code>CatchLimit | VLT/day</code>", parse_mode='html')
        catch_limit_raw, vlt_raw = parts[0], parts[1]
    else:
        if len(parts) < 3:
            return await event.reply("⚠️ Format: <code>CharID(s) | CatchLimit | VLT/day</code>", parse_mode='html')
        char_ids = [normalize_char_id_input(x) for x in re.split(r'[,\s]+', parts[0].strip()) if x.strip()]
        catch_limit_raw, vlt_raw = parts[1], parts[2]
        if not char_ids:
            return await event.reply("⚠️ Give at least one CharID.", parse_mode='html')

    catch_limit = None
    if catch_limit_raw and catch_limit_raw != "-":
        if not catch_limit_raw.lstrip('-').isdigit():
            return await event.reply("⚠️ CatchLimit must be a whole number (0 or - = infinite).", parse_mode='html')
        catch_limit = max(0, int(catch_limit_raw))

    le_daily_vlt = 1.0
    if vlt_raw and vlt_raw != "-":
        try:
            le_daily_vlt = round(float(vlt_raw), 2)
            if le_daily_vlt <= 0:
                raise ValueError
        except ValueError:
            return await event.reply("⚠️ VLT/day must be a positive number (- = default 1).", parse_mode='html')

    tagged, missing = [], []
    for cid in char_ids:
        char_doc = await characters_base_col.find_one({"char_id": cid})
        if not char_doc:
            missing.append(display_char_id(cid))
            continue
        update_fields = {"is_limited_edition": True, "le_name": le_name, "le_daily_vlt": le_daily_vlt}
        if catch_limit is not None:
            update_fields["spawn_limit"] = catch_limit
        await characters_base_col.update_one({"char_id": cid}, {"$set": update_fields})
        tagged.append(char_doc["name"])
    await invalidate_character_caches()
    # 🩹 NEW (per owner request): register (or just reuse, if this name already had one) this
    # batch's permanent number — get_or_create_le_batch_number is a no-op lookup for an
    # already-numbered name, so this is safe to call unconditionally on every /addle, whether
    # it created a brand new batch or added to one via the #<number> shorthand above.
    batch_number = await get_or_create_le_batch_number(le_name)

    limit_text = "♾️ Infinite" if catch_limit in (None, 0) else str(catch_limit)
    lines = [
        f"🎗️ <b>Limited Edition tagged:</b> <code>{escape_html(bookend_emoji_name(le_name))}</code> (<b>Batch #{batch_number}</b>)",
        f"✅ <b>Cards ({len(tagged)}):</b> {', '.join(escape_html(n) for n in tagged) if tagged else '—'}",
        f"🔁 <b>Catch Limit:</b> <code>{limit_text}</code>" if catch_limit is not None else "🔁 <b>Catch Limit:</b> <i>unchanged</i>",
        f"💠 <b>Daily reward:</b> <code>{format_vlt_plain(le_daily_vlt)}</code>/day per owned copy",
        f"💡 <i>Add more to this batch anytime with</i> <code>/addle #{batch_number} CharID(s)</code>",
    ]
    if missing:
        lines.append(f"❌ <b>Not found (skipped):</b> <code>{', '.join(missing)}</code>")
    await event.reply("\n".join(lines), parse_mode='html')

# =========================================================
# 🎗️ /mergele — owner-only, merges two Limited Edition batches into one (per owner request,
# LE fragmentation fix). Every character currently tagged under EITHER batch's le_name gets
# re-tagged to a single new name the owner supplies via reply — same 2-step reply-wizard shape
# as /addle. Deliberately touches ONLY the le_name field: le_daily_vlt, spawn_limit, and every
# VLT/Star balance are left completely untouched, so a merge can never itself mint, refund, or
# charge anything. The lower of the two batch numbers survives (now pointing at the merged
# name); the higher one is retired for good — see le_batches_col's docstring for why retired
# numbers are never handed out again.
# =========================================================
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]mergele(?:@\w+)?(?:\s+#?(\d+)\s+#?(\d+))?$', 'bot1')))
async def merge_le_prompt(event):
    if event.sender_id != OWNER_ID: return
    m = event.pattern_match
    if not m.group(1) or not m.group(2):
        existing = await le_batches_col.find().sort("batch_number", 1).to_list(length=None)
        listing = "\n".join(f"  #{b['batch_number']} — {escape_html(bookend_emoji_name(b['le_name']))}" for b in existing) or "  (none yet)"
        return await event.reply(
            f"🎗️ <b>Usage:</b> <code>/mergele #1 #2</code>\n\n"
            f"📋 <b>Current batches:</b>\n{listing}",
            parse_mode='html'
        )
    num_a, num_b = sorted([int(m.group(1)), int(m.group(2))])
    if num_a == num_b:
        return await event.reply("❌ <b>Can't merge a batch with itself.</b>", parse_mode='html')
    name_a = await get_le_name_by_batch_number(num_a)
    name_b = await get_le_name_by_batch_number(num_b)
    if not name_a or not name_b:
        missing_num = num_a if not name_a else num_b
        return await event.reply(f"❌ <b>Batch #{missing_num} doesn't exist.</b>", parse_mode='html')
    count_a = await characters_base_col.count_documents({"is_limited_edition": True, "le_name": name_a})
    count_b = await characters_base_col.count_documents({"is_limited_edition": True, "le_name": name_b})
    text = (
        f"🎗️ <b>Merge Batch #{num_a} + #{num_b}</b>\n\n"
        f"#{num_a} <b>{escape_html(name_a)}</b> — {count_a} card(s)\n"
        f"#{num_b} <b>{escape_html(name_b)}</b> — {count_b} card(s)\n\n"
        f"↩️ <b>Reply to this message</b> with the <b>final name</b> to use for the merged batch\n"
        f"<i>All {count_a + count_b} cards from both will carry this name. Batch #{num_a} keeps its "
        f"number; #{num_b} is retired. VLT/day rates and catch limits are untouched — no VLT is "
        f"given or taken by merging.</i>"
    )
    sent = await event.reply(text, parse_mode='html')
    pending_mergele_prompt[sent.id] = {"batch_a": num_a, "batch_b": num_b, "le_name_a": name_a, "le_name_b": name_b}

@bot1.on(events.NewMessage(incoming=True))
async def merge_le_apply(event):
    if event.sender_id != OWNER_ID: return
    if not event.is_reply: return
    if event.reply_to_msg_id not in pending_mergele_prompt: return
    state = pending_mergele_prompt.pop(event.reply_to_msg_id)
    new_name = (event.text or "").strip()
    if not new_name:
        return await event.reply("⚠️ Reply with actual text.", parse_mode='html')

    num_a, num_b = state["batch_a"], state["batch_b"]
    name_a, name_b = state["le_name_a"], state["le_name_b"]

    # 🩹 NEW (per owner request, same reasoning as /addle): the merged name needs a leading
    # emoji too, since it becomes the batch's new canonical display name going forward.
    if not extract_leading_emoji(new_name):
        text = (
            f"⚠️ <b>Name needs a leading emoji</b> — e.g. <code>🔱GODLIKE EDITION</code>.\n\n"
            f"↩️ <b>Reply to this message</b> with the name again, emoji included."
        )
        sent = await event.reply(text, parse_mode='html')
        pending_mergele_prompt[sent.id] = {"batch_a": num_a, "batch_b": num_b, "le_name_a": name_a, "le_name_b": name_b}
        return

    # Re-tag every character on EITHER old name to the new unified name. Only le_name changes —
    # le_daily_vlt, spawn_limit, and every player's VLT/Star balance are left exactly as they
    # were, by design (see the command-level docstring above).
    result = await characters_base_col.update_many(
        {"is_limited_edition": True, "le_name": {"$in": [name_a, name_b]}},
        {"$set": {"le_name": new_name}}
    )
    # Batch #num_a (the lower number) now points at the merged name; #num_b is retired —
    # deleted outright rather than left dangling, so get_le_name_by_batch_number(num_b)
    # correctly reports "doesn't exist" from here on instead of a stale name nobody carries.
    await le_batches_col.update_one({"batch_number": num_a}, {"$set": {"le_name": new_name}})
    await le_batches_col.delete_one({"batch_number": num_b})
    await invalidate_character_caches()

    await event.reply(
        f"🎗️ <b>Merged!</b>\n"
        f"✅ <code>{result.modified_count}</code> card(s) now carry <b>{escape_html(bookend_emoji_name(new_name))}</b> "
        f"under <b>Batch #{num_a}</b>.\n"
        f"🗑️ Batch #{num_b} retired — that number will never be reused.\n"
        f"💠 <i>No VLT was given or taken.</i>",
        parse_mode='html'
    )

# ==========================================
# 🏷️ /renamele — owner-only, fixes ONE existing LE batch's name in place (unlike /mergele,
# which needs two DIFFERENT batches to combine). Built per owner request specifically for
# batches that predate the /addle emoji requirement — see extract_leading_emoji — and so still
# carry a plain-text le_name with nothing to bookend at display time.
# ==========================================
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]renamele(?:@\w+)?\s+#?(\d+)$', 'bot1')))
async def rename_le_prompt(event):
    if event.sender_id != OWNER_ID: return
    batch_number = int(event.pattern_match.group(1))
    batch = await le_batches_col.find_one({"batch_number": batch_number})
    if not batch:
        return await event.reply(f"❌ Batch #{batch_number} doesn't exist.", parse_mode='html')
    sent = await event.reply(
        f"🏷️ <b>Renaming Batch #{batch_number}</b>\n"
        f"Current: <code>{escape_html(batch['le_name'])}</code>\n\n"
        f"↩️ <b>Reply to this message</b> with the new name — needs a leading emoji, e.g. "
        f"<code>🔱GODLIKE EDITION</code>.",
        parse_mode='html'
    )
    pending_renamele_prompt[sent.id] = {"batch_number": batch_number}

@bot1.on(events.NewMessage(incoming=True))
async def rename_le_apply(event):
    if event.sender_id != OWNER_ID: return
    if not event.is_reply: return
    if event.reply_to_msg_id not in pending_renamele_prompt: return
    state = pending_renamele_prompt.pop(event.reply_to_msg_id)
    new_name = (event.text or "").strip()
    if not extract_leading_emoji(new_name):
        sent = await event.reply(
            f"⚠️ <b>Name needs a leading emoji</b> — e.g. <code>🔱GODLIKE EDITION</code>.\n\n"
            f"↩️ <b>Reply to this message</b> with the name again, emoji included.",
            parse_mode='html'
        )
        pending_renamele_prompt[sent.id] = state
        return
    batch_number = state["batch_number"]
    batch = await le_batches_col.find_one({"batch_number": batch_number})
    if not batch:
        return await event.reply(f"❌ Batch #{batch_number} no longer exists.", parse_mode='html')
    old_name = batch["le_name"]
    await le_batches_col.update_one({"batch_number": batch_number}, {"$set": {"le_name": new_name}})
    result = await characters_base_col.update_many(
        {"is_limited_edition": True, "le_name": old_name}, {"$set": {"le_name": new_name}}
    )
    await invalidate_character_caches()
    await event.reply(
        f"✅ <b>Batch #{batch_number} renamed!</b>\n"
        f"<code>{escape_html(old_name)}</code> → <code>{escape_html(bookend_emoji_name(new_name))}</code>\n"
        f"🎗️ <code>{result.modified_count}</code> card(s) updated.",
        parse_mode='html'
    )



# upload to a clean one) WITHOUT touching char_id, name, rarity, or anything else. Every
# existing harem entry only ever stores char_id — never storage_msg_id directly — and media
# is always looked up fresh from characters_base_col at display time (/harem, /check, /show,
# spawns, /who), so this swap is instantly visible everywhere and nobody who already caught
# this character loses it or needs to do anything; they just see the better picture/video.
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]change(?:@\w+)?\s+(\S+)$', 'bot1')))
async def change_character_media(event):
    if event.sender_id != OWNER_ID: return
    char_id = normalize_char_id_input(event.pattern_match.group(1))
    char_doc = await characters_base_col.find_one({"char_id": char_id})
    if not char_doc:
        return await event.reply(f"❌ No character found with ID <code>{escape_html(display_char_id(char_id))}</code>.", parse_mode='html')
    if not event.is_reply:
        return await event.reply("❌ Reply to the new photo/video with <code>/change [CharID]</code>.", parse_mode='html')
    reply_msg = await event.get_reply_message()
    if not reply_msg or not (reply_msg.photo or reply_msg.video or reply_msg.document):
        return await event.reply("❌ Valid media not found in the replied message.", parse_mode='html')
    is_video_media = bool(
        reply_msg.video or
        (reply_msg.document and (reply_msg.document.mime_type or "").startswith("video/"))
    )
    # Same "Rarity 1 = video only, everything else = photo only" invariant /addchar enforces —
    # a media swap must not silently turn a photo character into a video one or vice versa.
    is_currently_video_tier = (classify_rarity(char_doc.get("rarity", "")) == RARITY_TIERS[0])
    if is_currently_video_tier and not is_video_media:
        return await event.reply(
            f"❌ <b>{escape_html(char_doc['name'])}</b> is a {RARITY_TIERS[0]} (video-only) character — the replacement must be a video too.",
            parse_mode='html'
        )
    if is_video_media and not is_currently_video_tier:
        return await event.reply(
            f"❌ <b>{escape_html(char_doc['name'])}</b> isn't rarity {RARITY_TIERS[0]} — replacing it with a video isn't allowed. Use a photo.",
            parse_mode='html'
        )
    CHANGE_MAX_BYTES = 2000 * 1024 * 1024  # Telegram's own bot upload ceiling
    CHANGE_UPLOAD_TIMEOUT = 240
    media_size = getattr(getattr(reply_msg, "file", None), "size", None)
    if media_size and media_size > CHANGE_MAX_BYTES:
        return await event.reply(
            f"❌ <b>File too large</b> (<code>{media_size / 1024 / 1024:.1f}MB</code>) — Telegram bots can't send files over <code>2000MB</code>.",
            parse_mode='html'
        )
    old_storage_id = char_doc.get("storage_msg_id")
    status_msg = await event.reply("⏳ <b>Swapping media...</b>", parse_mode='html')
    try:
        try:
            forwarded_msg = await asyncio.wait_for(
                send_safe_message(bot1, SPECIFIC_CONTROL_GROUP, "", file=reply_msg.media),
                timeout=CHANGE_UPLOAD_TIMEOUT
            )
        except errors.FileReferenceExpiredError:
            fresh_reply_msg = await bot1.get_messages(event.chat_id, ids=reply_msg.id)
            if not fresh_reply_msg or not fresh_reply_msg.media:
                raise RuntimeError("media reference expired and the original message is no longer available")
            reply_msg = fresh_reply_msg
            # 🩹 FIX: this used to forward through `bot2` even though `reply_msg` was just
            # re-fetched via `bot1.get_messages()` above — a file_reference is only valid for
            # the session that fetched it (same rule /addchar's comment documents), so handing
            # bot1-fetched media to bot2 would just fail again with another reference error,
            # silently defeating the whole point of this retry path. Use bot1 here, matching
            # both the fetch right above and the first attempt a few lines up.
            forwarded_msg = await asyncio.wait_for(
                send_safe_message(bot1, SPECIFIC_CONTROL_GROUP, "", file=reply_msg.media),
                timeout=CHANGE_UPLOAD_TIMEOUT
            )
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"upload timed out after {CHANGE_UPLOAD_TIMEOUT}s — the file may be too large or Telegram is slow right now, try again"
            )
        new_storage_id = forwarded_msg.id
        new_phash = await compute_phash_for_message(reply_msg)
        # Update the DB FIRST, delete the old media LAST — a mid-way failure must never leave
        # characters_base_col pointing at media that's already gone.
        await characters_base_col.update_one(
            {"char_id": char_id},
            {"$set": {"storage_msg_id": new_storage_id, "photo_phash": new_phash}}
        )
        await invalidate_character_caches()
        old_deleted_note = ""
        if old_storage_id:
            try:
                await bot1.delete_messages(SPECIFIC_CONTROL_GROUP, [old_storage_id])
                old_deleted_note = "\n🗑 Old media deleted."
            except Exception:
                old_deleted_note = "\n⚠️ New media is live, but the old file couldn't be deleted (already gone?)."
        channel_note = ""
        if CHARACTER_CHANNEL_ID:
            try:
                fresh_doc = await characters_base_col.find_one({"char_id": char_id})
                actor_mention = build_actor_mention(await event.get_sender())
                channel_msg = await post_character_to_channel(fresh_doc, is_new=False, actor_mention=actor_mention)
                await characters_base_col.update_one(
                    {"char_id": char_id},
                    {"$set": {"channel_msg_id": channel_msg.id if channel_msg else None}}
                )
                channel_note = "\n📢 Channel: Posted ✅"
            except Exception as ce:
                await report_system_error(f"Change-media channel post ({char_id})", ce)
                channel_note = f"\n⚠️ Channel: Post failed — <code>{escape_html(str(ce))}</code>"
        await status_msg.edit(
            f"✅ <b>Media updated for</b> <code>{display_char_id(char_id)}</code> — <b>{escape_html(char_doc['name'])}</b>"
            f"{old_deleted_note}\n"
            f"👥 Everyone who already caught this character keeps it — only the picture/video changed."
            f"{channel_note}",
            parse_mode='html'
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("❌ <b>Media swap cancelled/interrupted before finishing</b> — please retry. The old media is untouched.", parse_mode='html')
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            await status_msg.edit(f"❌ <b>Media Swap Error:</b> <code>{escape_html(str(e))}</code>\nThe old media is untouched.", parse_mode='html')
        except Exception:
            pass

# ---- DELETE CHARACTER ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.](delchar|removechar)(?:@\w+)?(?:\s+(\S+))?$', 'bot1')))
async def delete_character_by_owner(event):
    if event.sender_id != OWNER_ID: return
    char_id = event.pattern_match.group(2)
    if not char_id:
        await event.reply(f"❌ Please provide the Character ID to delete.\nExample: `/delchar BOD789`")
        return
    query = {"$or": [{"id": char_id}, {"char_id": char_id}]}
    res1 = await db["characters"].delete_many(query)
    res2 = await db["characters_base_data"].delete_many(query)
    if res1.deleted_count > 0 or res2.deleted_count > 0:
        await invalidate_character_caches()
        await event.reply(f"🔥 {f('DATABASE REMOVED')}\n🆔 {f('Character ID')}: {char_id}\nStatus: Deleted.")
    else:
        await event.reply(f"❌ No character found with ID `{char_id}`.")

# ---- EDIT CHARACTER ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]editchar(?:@\w+)?(?:\s+(\S+))?$', 'bot1')))
async def edit_character_prompt(event):
    if event.sender_id != OWNER_ID: return
    target_id = event.pattern_match.group(1)
    if target_id:
        char_doc = await characters_base_col.find_one({"char_id": target_id.upper()})
        if not char_doc:
            return await event.reply(f"❌ No character found with ID <code>{escape_html(target_id)}</code>.", parse_mode='html')
        cur_event = char_doc.get("event", "General")
        cur_limit = char_doc.get("spawn_limit", 0)
        limit_text = "♾️ Infinite" if not cur_limit else str(cur_limit)
        text = (
            f"✏️ <b>Editing:</b> {escape_html(char_doc['name'])} (<code>{char_doc['char_id']}</code>)\n"
            f"🏷️ Rarity: {char_doc.get('rarity', 'Unknown')}\n"
            f"🎪 Current Event: <code>{escape_html(cur_event)}</code>\n"
            f"🔁 Current Catch Limit: <code>{limit_text}</code> (caught <code>{char_doc.get('spawn_count', 0)}</code> times so far)\n\n"
            f"↩️ <b>Reply to this message</b> with:\n"
            f"<code>Event | CatchLimit</code>\n"
            f"e.g. <code>Summer Festival | 5</code>\n"
            f"<i>Use - to leave a field unchanged. CatchLimit 0 = infinite.</i>"
        )
        sent = await event.reply(text, parse_mode='html')
        pending_editchar_prompt_ids[sent.id] = char_doc["char_id"]
        return
    all_chars = await characters_base_col.find().sort("char_id", 1).to_list(length=None)
    if not all_chars:
        return await event.reply("📭 No characters in the database yet. Use /addchar first.")
    header = "📋 <b>CHARACTER DATABASE</b>\n\n"
    lines = []
    for c in all_chars:
        cur_event = c.get("event", "General")
        cur_limit = c.get("spawn_limit", 0)
        limit_text = "♾️" if not cur_limit else str(cur_limit)
        lines.append(
            f"🆔 <code>{c['char_id']}</code> — <b>{escape_html(c['name'])}</b> (<i>{escape_html(c.get('category',''))}</i>)\n"
            f"     {c.get('rarity','')} | 🎪 {escape_html(cur_event)} | 🔁 {c.get('spawn_count',0)}/{limit_text}"
        )
    footer = (
        "\n\n↩️ <b>Reply to THIS message</b> with:\n"
        "<code>CharID | Event | CatchLimit</code>\n"
        "e.g. <code>BOD1234 | Summer Festival | 5</code>\n"
        "<i>Use - to leave a field unchanged. CatchLimit 0 = infinite.</i>\n"
        "💡 Tip: <code>/editchar CharID</code> jumps straight to one character."
    )
    chunks, current = [], header
    for line in lines:
        if len(current) + len(line) + 1 > 3500:
            chunks.append(current)
            current = ""
        current += line + "\n"
    chunks.append(current + footer)
    last_sent = None
    for chunk in chunks:
        last_sent = await event.reply(chunk, parse_mode='html')
    pending_editchar_prompt_ids[last_sent.id] = None

@bot1.on(events.NewMessage(incoming=True))
async def edit_character_apply(event):
    if event.sender_id != OWNER_ID: return
    if not event.is_reply: return
    if event.reply_to_msg_id not in pending_editchar_prompt_ids: return
    fixed_char_id = pending_editchar_prompt_ids[event.reply_to_msg_id]
    raw = (event.text or "").strip()
    if not raw: return
    parts = [p.strip() for p in raw.split('|')]
    if fixed_char_id:
        char_id = fixed_char_id
        if len(parts) < 2:
            return await event.reply("⚠️ Format: <code>Event | CatchLimit</code>", parse_mode='html')
        new_event, new_limit_raw = parts[0], parts[1]
    else:
        if len(parts) < 3:
            return await event.reply("⚠️ Format: <code>CharID | Event | CatchLimit</code>", parse_mode='html')
        char_id, new_event, new_limit_raw = parts[0].strip().upper(), parts[1], parts[2]
    char_doc = await characters_base_col.find_one({"char_id": char_id})
    if not char_doc:
        return await event.reply(f"❌ No character found with ID <code>{escape_html(char_id)}</code>.", parse_mode='html')
    update_fields = {}
    if new_event and new_event != "-":
        update_fields["event"] = new_event
    if new_limit_raw and new_limit_raw != "-":
        if not new_limit_raw.lstrip('-').isdigit():
            return await event.reply("⚠️ CatchLimit must be a whole number (0 = infinite).")
        update_fields["spawn_limit"] = max(0, int(new_limit_raw))
    if not update_fields:
        return await event.reply("⚠️ Nothing to update — both fields were '-'.")
    await characters_base_col.update_one({"char_id": char_id}, {"$set": update_fields})
    await invalidate_character_caches()
    updated_doc = await characters_base_col.find_one({"char_id": char_id})
    spawned = updated_doc.get("spawn_count", 0)
    limit = updated_doc.get("spawn_limit", 0)
    remaining_text = "♾️ Infinite" if not limit else f"{max(0, limit - spawned)} left ({spawned}/{limit})"
    # 🩹 NEW (per owner request): edits used to update the DB silently with no channel
    # announcement at all. Now every /editchar posts an updated card to CHARACTER_CHANNEL_ID
    # too, same as /addchar does for brand-new cards.
    channel_note = ""
    if CHARACTER_CHANNEL_ID:
        try:
            actor_mention = build_actor_mention(await event.get_sender())
            channel_msg = await post_character_to_channel(updated_doc, is_new=False, actor_mention=actor_mention)
            await characters_base_col.update_one(
                {"char_id": char_id},
                {"$set": {"channel_msg_id": channel_msg.id if channel_msg else None}}
            )
            channel_note = "\n📢 Channel: Posted ✅"
        except Exception as ce:
            await report_system_error(f"EditChar channel post ({char_id})", ce)
            channel_note = f"\n⚠️ Channel: Post failed — <code>{escape_html(str(ce))}</code>"
    await event.reply(
        f"✅ <b>{escape_html(updated_doc['name'])}</b> (<code>{char_id}</code>) updated!\n"
        f"🎡 Event: <code>{escape_html(updated_doc.get('event','General'))}</code>\n"
        f"🔁 Catches remaining: <b>{remaining_text}</b>"
        f"{channel_note}",
        parse_mode='html'
    )

# ---- EXPORT CHARACTERS: owner-only, sends the full character database (name, series,
# rarity, event) as a downloadable CSV document instead of a long chat message. ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]exportchars(?:@\w+)?$', 'bot1')))
async def export_characters_handler(event):
    if event.sender_id != OWNER_ID: return
    status_msg = await event.reply("📄 <b>Building character export...</b>", parse_mode='html')
    try:
        all_chars = await characters_base_col.find().sort("char_id", 1).to_list(length=None)
        if not all_chars:
            return await status_msg.edit("📭 No characters in the database yet. Use /addchar first.", parse_mode='html')

        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerow(["Character ID", "Name", "Series", "Rarity", "Event"])
        for c in all_chars:
            writer.writerow([
                display_char_id(c.get("char_id", "")),
                c.get("name", ""),
                c.get("category", "") or "Unknown Series",
                c.get("rarity", "") or "Unknown",
                c.get("event") or "General",
            ])
        # utf-8-sig adds a BOM so Excel/Sheets render Burmese and other non-ASCII text
        # correctly instead of showing garbled characters when the CSV is opened.
        file_bytes = io.BytesIO(csv_buffer.getvalue().encode("utf-8-sig"))
        file_bytes.name = f"characters_export_{datetime.now(TZ).strftime('%Y-%m-%d')}.csv"

        await send_safe_file(
            bot1, event.chat_id, file_bytes,
            caption=f"📄 <b>Character Database Export</b>\n🧩 <b>Total:</b> <code>{len(all_chars)}</code> characters",
            reply_to=status_msg.id,
            parse_mode='html'
        )
        await status_msg.delete()
    except Exception as e:
        await status_msg.edit(f"❌ <b>Export Error:</b> <code>{escape_html(str(e))}</code>", parse_mode='html')
        await report_system_error("export_characters_handler", e)

# ==========================================
# 🗂️ /mergecategory & /mergecategories — owner-only category cleanup.
# category is stored ONLY on characters_base_col (characters_col is an unused legacy
# collection, and harem/user docs only ever keep a char_id — category is looked up fresh
# from characters_base_col every time), so a single update_many + cache invalidation is a
# complete fix everywhere the category name is shown (/harem, /dex, exports, etc.).
# ==========================================
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]mergecategory(?:@\w+)?\s+(.+)$', 'bot1')))
async def merge_category_handler(event):
    if event.sender_id != OWNER_ID: return
    raw = event.pattern_match.group(1)
    if '|' not in raw:
        return await event.reply(bq("⚠️ Format: <code>/mergecategory Old Name | New Name</code>"), parse_mode='html')
    old_name, new_name = (p.strip() for p in raw.split('|', 1))
    if not old_name or not new_name:
        return await event.reply(bq("⚠️ Need both an old and a new category name."), parse_mode='html')
    if old_name == new_name:
        return await event.reply(bq("⚠️ Old and new names are identical — nothing to do."), parse_mode='html')
    result = await characters_base_col.update_many({"category": old_name}, {"$set": {"category": new_name}})
    await invalidate_character_caches()
    await event.reply(
        bq(f"✅ Merged <b>{escape_html(old_name)}</b> → <b>{escape_html(new_name)}</b>\n<code>{result.modified_count}</code> character(s) moved."),
        parse_mode='html'
    )

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]mergecategories(?:@\w+)?\s+(.+)$', 'bot1')))
async def merge_categories_bulk_handler(event):
    if event.sender_id != OWNER_ID: return
    raw = event.pattern_match.group(1)
    if '|' not in raw:
        return await event.reply(bq("⚠️ Format: <code>/mergecategories Old A, Old B, Old C | New Name</code>"), parse_mode='html')
    old_names_raw, new_name = raw.split('|', 1)
    old_names = [n.strip() for n in old_names_raw.split(',') if n.strip()]
    new_name = new_name.strip()
    if not old_names or not new_name:
        return await event.reply(bq("⚠️ Need at least one old category name and a new name."), parse_mode='html')
    result = await characters_base_col.update_many({"category": {"$in": old_names}}, {"$set": {"category": new_name}})
    await invalidate_character_caches()
    await event.reply(
        bq(f"✅ Merged <code>{len(old_names)}</code> categories → <b>{escape_html(new_name)}</b>\n<code>{result.modified_count}</code> character(s) moved."),
        parse_mode='html'
    )

# ==========================================
# 🖼️ /show — full-quality rarity gallery browser, open to any user but DM-only.
# Walks EVERY character of a given rarity tier (/show no1 .. /show no9) one at a time with
# Prev/Next buttons. Media is sent by re-using the original stored file reference
# (storage_msg.media) instead of downloading+re-uploading, so quality never degrades — the
# exact same technique /check, /harem and spawns already rely on.
# ==========================================
async def _get_show_list(tier_num):
    tier = RARITY_TIERS[int(tier_num) - 1]
    chars = await characters_base_col.find({"rarity_tier": tier}).to_list(length=None)
    chars.sort(key=lambda c: (c.get("name") or "").lower())
    return chars

def _show_rarity_grid_buttons():
    """3x3 grid of rarity buttons (No.1 ... No.9, with each tier's emoji) — shown for a bare
    /show, matching the 9-tier layout used everywhere else in the bot."""
    rows, row = [], []
    for num in sorted(RARITY_NUM_MAP.keys(), key=int):
        tier = RARITY_TIERS[int(num) - 1]
        emoji = RARITY_EMOJI.get(tier, RARITY_DEFAULT_EMOJI)
        row.append(Button.inline(f"{emoji} No.{num}", data=f"shownav_{num}_0"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows

# ---- 🎗️ LIMITED EDITION batch menu — the "🎗️ Limited Editions" button on /harem opens this
# (see "leopen"/"lepg" in unified_callback_handler), 5 batch names per page. Each batch name
# is itself a switch_inline button (query="le.{index}") — tapping ONE is what actually opens
# Telegram's inline-query mode, scoped to just that batch's cards (see handle_le_inline_query).
# ----
LE_BATCHES_PER_PAGE = 5

def _build_le_batch_menu(batches, page):
    total_pages = max(1, (len(batches) + LE_BATCHES_PER_PAGE - 1) // LE_BATCHES_PER_PAGE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * LE_BATCHES_PER_PAGE
    page_batches = list(enumerate(batches))[start:start + LE_BATCHES_PER_PAGE]
    text = (
        f"🎗️ <b>LIMITED EDITIONS</b>\n"
        f"📑 <b>Page:</b> <code>{page}/{total_pages}</code> · <b>Batches:</b> <code>{len(batches)}</code>\n\n"
        f"👇 <i>Tap a batch to browse its cards</i>"
    )
    buttons = [
        [Button.switch_inline(f"🦋 {name} ({count})", query=f"le.{idx}", same_peer=True)]
        for idx, (name, count) in page_batches
    ]
    nav = []
    if page > 1:
        nav.append(Button.inline("⬅️ Prev", data=f"lepg_{page-1}"))
    if page < total_pages:
        nav.append(Button.inline("Next ➡️", data=f"lepg_{page+1}"))
    if nav:
        buttons.append(nav)
    return text, buttons

def _show_nav_buttons(tier_num, idx, total, char_doc=None):
    prev_idx = (idx - 1) % total
    next_idx = (idx + 1) % total
    rows = [[
        Button.inline("⬅️ Prev", data=f"shownav_{tier_num}_{prev_idx}"),
        Button.inline("➡️ Next", data=f"shownav_{tier_num}_{next_idx}")
    ]]
    if char_doc and char_doc.get("char_id"):
        tier = RARITY_TIERS[int(tier_num) - 1]
        limit = char_doc.get("spawn_limit", 0)
        char_locked = bool(limit and limit > 0 and char_doc.get("spawn_count", 0) >= limit)
        if tier in _cached_disabled_buy_tiers:
            # 🩹 CHANGED (per owner request): a dead "sold out" button used to just show an
            # alert and go nowhere. Now it's a URL button straight to the owner's pinned
            # message — buyers can reply there directly to negotiate/buy manually while the
            # tier is closed in the Owner Shop.
            rows.append([Button.url("🚫 အရောင်းကုန်ပါပြီ — ဝယ်ရန်ဆက်သွယ်ပါ", SOLD_OUT_CONTACT_LINK)])
        elif char_locked:
            # 🎯 CATCH LIMIT reached for THIS specific character — Owner Shop no longer sells
            # it (see execute_star_shop_purchase's matching check), point at owner contact
            # instead of a dead/misleading buy button. Corrected 2026-08: this used to only
            # ever fire for ULTRA as a tier-wide pooled flag — it's per-character now, for any
            # capped character, ULTRA included via its own default (see
            # ULTRA_DEFAULT_CATCH_LIMIT).
            rows.append([Button.url("🔒 Locked — ဆက်သွယ်ရန်", SOLD_OUT_CONTACT_LINK)])
        else:
            price = vlt_price_for_char(char_doc)
            rows.append([Button.inline(f"🛒 {price}💠 ဖြင့်ဝယ်မယ်", data=f"shopbuy_{char_doc['char_id']}")])
    rows.append([Button.inline("🔢 Rarity List", data="showgrid_back")])
    return rows

def _build_show_caption(char_doc, tier_num, idx, total):
    rarity_display = char_doc.get("rarity") or RARITY_NUM_MAP.get(tier_num, {}).get("name", "Unknown")
    limit = char_doc.get("spawn_limit", 0)
    spawned = char_doc.get("spawn_count", 0)
    caught_text = "♾️ <b>Infinite</b>" if not limit else f"<code>{spawned}/{limit}</code>"
    price = vlt_price_for_char(char_doc)
    return (
        f"🖼️ <b>Rarity Gallery</b> — <code>{idx + 1}/{total}</code>\n"
        f"<blockquote>"
        f"✨ <b>Name:</b> <code>{escape_html(char_doc.get('name',''))}</code>\n"
        f"🆔 <b>ID:</b> <code>{display_char_id(char_doc.get('char_id',''))}</code>\n"
        f"{rarity_display}\n"
        f"🫧 <b>Category:</b> <code>{escape_html(char_doc.get('category','') or 'Unknown')}</code>\n"
        f"{artist_line(char_doc)}"
        f"🔁 <b>Caught:</b> {caught_text}\n"
        f"💠 <b>Shop Price:</b> <code>{price}💠</code>"
        f"</blockquote>"
    )

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]show(?:@\w+)?\s+(?:no)?([1-9])$', 'bot1')))
async def show_rarity_gallery_handler(event):
    if not event.is_private:
        return await event.reply("📩 <b>DM me</b> and use <code>/show</code> there — it only works in private chat.", parse_mode='html')
    tier_num = event.pattern_match.group(1)
    chars = await _get_show_list(tier_num)
    if not chars:
        r_info = RARITY_NUM_MAP.get(tier_num)
        label = r_info["name"] if r_info else f"Rarity No.{tier_num}"
        return await event.reply(f"📭 <b>No characters found for {label} yet.</b>", parse_mode='html')
    idx = 0
    char_doc = chars[idx]
    caption = _build_show_caption(char_doc, tier_num, idx, len(chars))
    buttons = _show_nav_buttons(tier_num, idx, len(chars), char_doc)

    async def _send_show(media):
        return await event.reply(caption, file=media, buttons=buttons, parse_mode='html')

    sent = await send_with_char_media(char_doc["char_id"], char_doc["storage_msg_id"], _send_show)
    if sent is None:
        await event.reply(
            f"❌ <b>Media missing for</b> <code>{display_char_id(char_doc.get('char_id',''))}</code> "
            f"— its storage message may have been deleted.",
            parse_mode='html'
        )

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]show(?:@\w+)?$', 'bot1')))
async def show_bare_usage_handler(event):
    if not event.is_private:
        return await event.reply("📩 <b>DM me</b> and use <code>/show</code> there — it only works in private chat.", parse_mode='html')
    await event.reply(
        f"🖼️ <b>ဝယ်ချင်တဲ့ ကဒ် Rarity ကိုရွေးပါ</b>\n"
        f"<i>ကြိုက်တဲ့ကဒ်တွေ့ရင် 💠 VLT နဲ့ တန်းဝယ်နိုင်ပါတယ်။</i>",
        parse_mode='html',
        buttons=_show_rarity_grid_buttons()
    )

@bot1.on(events.CallbackQuery(pattern=r'^showgrid_back$'))
async def show_rarity_grid_back_callback(event):
    text = (
        f"🖼️ <b>ဝယ်ချင်တဲ့ ကဒ် Rarity ကိုရွေးပါ</b>\n"
        f"<i>ကြိုက်တဲ့ကဒ်တွေ့ရင် 💠 VLT နဲ့ တန်းဝယ်နိုင်ပါတယ်။</i>"
    )
    buttons = _show_rarity_grid_buttons()
    try:
        await event.edit(text, parse_mode='html', buttons=buttons, file=None)
        await event.answer()
    except errors.MessageNotModifiedError:
        await event.answer()
    except Exception:
        try:
            await event.delete()
        except Exception:
            pass
        await bot1.send_message(event.chat_id, text, parse_mode='html', buttons=buttons)
        await event.answer()

@bot1.on(events.CallbackQuery(pattern=r'^shownav_([1-9])_(\d+)$'))
async def show_rarity_gallery_nav(event):
    tier_num = event.pattern_match.group(1)
    idx_raw = event.pattern_match.group(2)
    if isinstance(tier_num, bytes): tier_num = tier_num.decode('utf-8')
    if isinstance(idx_raw, bytes): idx_raw = idx_raw.decode('utf-8')
    idx = int(idx_raw)
    chars = await _get_show_list(tier_num)
    if not chars:
        return await event.answer("📭 No characters left in this rarity.", alert=True)
    idx = idx % len(chars)
    char_doc = chars[idx]
    caption = _build_show_caption(char_doc, tier_num, idx, len(chars))
    buttons = _show_nav_buttons(tier_num, idx, len(chars), char_doc)

    async def _edit_show(media):
        return await event.edit(caption, file=media, buttons=buttons, parse_mode='html')

    try:
        result = await send_with_char_media(char_doc["char_id"], char_doc["storage_msg_id"], _edit_show)
        if result is None:
            return await event.answer("⚠️ Media missing for this one — skipping.", alert=True)
        await event.answer()
    except errors.MessageNotModifiedError:
        await event.answer()
    except Exception:
        # Fallback for the rare case Telegram rejects an in-place media swap (e.g. a photo
        # entry followed by a video entry) — delete and resend fresh instead of getting stuck.
        try:
            await event.delete()
        except Exception:
            pass

        async def _resend_show(media):
            return await bot1.send_message(event.chat_id, caption, file=media, buttons=buttons, parse_mode='html')

        await send_with_char_media(char_doc["char_id"], char_doc["storage_msg_id"], _resend_show)
        await event.answer()

# ---- ADD ARTIST ----
async def _resolve_character_from_reply(reply_msg, chat_id):
    """Figures out which character a replied-to message refers to. Tries an exact, free,
    instant storage-message match first (only possible when the reply happens inside
    SPECIFIC_CONTROL_GROUP, where every character's canonical media lives at its own
    storage_msg_id) — then falls back to the same perceptual-hash lookup /who uses, so
    replying to ANY repost of that media (a channel post, a spawn, a DM identify result,
    a forward — anywhere) also works."""
    if not reply_msg:
        return None
    if chat_id == SPECIFIC_CONTROL_GROUP:
        char_doc = await characters_base_col.find_one({"storage_msg_id": reply_msg.id})
        if char_doc:
            return char_doc
    if reply_msg.photo or reply_msg.video or reply_msg.document:
        return await find_character_by_media(reply_msg)
    return None

async def _bulk_add_artist(event, char_ids, artist_name):
    """Sets (or clears, if artist_name == '-') the SAME artist credit on every CharID in
    char_ids in one shot — the bulk counterpart to add_artist_handler's single-card mode,
    triggered by replying to a plain message that's just a list of IDs (one per line)."""
    clearing = artist_name == "-"
    updated, not_found = [], []
    updated_docs = []
    for char_id in char_ids:
        char_doc = await characters_base_col.find_one({"char_id": char_id})
        if not char_doc:
            not_found.append(display_char_id(char_id))
            continue
        if clearing:
            await characters_base_col.update_one({"char_id": char_id}, {"$unset": {"artist": ""}})
        else:
            await characters_base_col.update_one({"char_id": char_id}, {"$set": {"artist": artist_name}})
        updated.append(f"{display_char_id(char_id)} — {escape_html(char_doc['name'])}")
        updated_docs.append(char_id)
    if updated:
        await invalidate_character_caches()
    # 🩹 NEW (per owner request): bulk credit-setting used to be silent too — announce each
    # updated card to the channel, not just single-card /addartist. Paced to stay flood-safe.
    # Clears aren't announced (removing info isn't really "news").
    channel_posted, channel_failed = 0, 0
    if not clearing and CHARACTER_CHANNEL_ID:
        actor_mention = build_actor_mention(await event.get_sender())
        for char_id in updated_docs:
            fresh_doc = await characters_base_col.find_one({"char_id": char_id})
            try:
                channel_msg = await post_character_to_channel(fresh_doc, is_new=False, actor_mention=actor_mention)
                await characters_base_col.update_one(
                    {"char_id": char_id},
                    {"$set": {"channel_msg_id": channel_msg.id if channel_msg else None}}
                )
                channel_posted += 1
            except Exception as ce:
                await report_system_error(f"Bulk AddArtist channel post ({char_id})", ce)
                channel_failed += 1
            await asyncio.sleep(3)  # flood-safe pacing, same rate as /stardrop's broadcast loop
    action = "cleared" if clearing else f"set to <code>{escape_html(artist_name)}</code>"
    summary = [f"🎨 <b>Bulk Artist {'Clear' if clearing else 'Set'} Complete!</b>"]
    summary.append(f"✅ <b>Updated</b> <code>{len(updated)}</code>/<code>{len(char_ids)}</code> character(s) — artist {action}")
    if not clearing and CHARACTER_CHANNEL_ID:
        summary.append(f"📢 <b>Channel posts:</b> <code>{channel_posted}</code> sent, <code>{channel_failed}</code> failed")
    if updated:
        shown = "\n".join(f"• <code>{u}</code>" for u in updated[:40])
        if len(updated) > 40:
            shown += f"\n… (+{len(updated) - 40} more)"
        summary.append(f"<blockquote>{shown}</blockquote>")
    if not_found:
        shown = ", ".join(f"<code>{n}</code>" for n in not_found[:20])
        if len(not_found) > 20:
            shown += " …"
        summary.append(f"❌ <b>Not found:</b> <code>{len(not_found)}</code>")
        summary.append(f"<blockquote>{shown}</blockquote>")
    await event.reply("\n".join(summary), parse_mode='html')

_ADDARTIST_RARITY_RE = re.compile(r'^no\.?([1-9])\s+([\s\S]+)$', re.IGNORECASE)

async def _bulk_add_artist_by_rarity(event, rarity_num, artist_name):
    tier = RARITY_TIERS[int(rarity_num) - 1]
    r_info = RARITY_NUM_MAP[rarity_num]
    artist_name = artist_name.strip()
    if artist_name == "-":
        result = await characters_base_col.update_many({"rarity_tier": tier}, {"$unset": {"artist": ""}})
        await invalidate_character_caches()
        return await event.reply(
            f"🎨 <b>Artist credit cleared for every {r_info['name']} character.</b>\n"
            f"🗄️ <b>Characters updated:</b> <code>{result.modified_count}</code>",
            parse_mode='html'
        )
    affected_ids = [c["char_id"] async for c in characters_base_col.find({"rarity_tier": tier}, {"char_id": 1})]
    result = await characters_base_col.update_many({"rarity_tier": tier}, {"$set": {"artist": artist_name}})
    await invalidate_character_caches()
    status_msg = await event.reply(
        f"🎨 <b>Artist set for every {r_info['name']} character!</b>\n"
        f"🎨 <b>Artist:</b> <code>{escape_html(artist_name)}</code>\n"
        f"🗄️ <b>Characters updated:</b> <code>{result.modified_count}</code>\n"
        f"📢 <i>Posting each one to the channel now...</i>",
        parse_mode='html'
    )
    # 🩹 NEW (per owner request): announce every card in this rarity to the channel, paced to
    # stay flood-safe — same rate as /stardrop's broadcast loop.
    channel_posted, channel_failed = 0, 0
    if CHARACTER_CHANNEL_ID:
        actor_mention = build_actor_mention(await event.get_sender())
        for char_id in affected_ids:
            fresh_doc = await characters_base_col.find_one({"char_id": char_id})
            if not fresh_doc:
                continue
            try:
                channel_msg = await post_character_to_channel(fresh_doc, is_new=False, actor_mention=actor_mention)
                await characters_base_col.update_one(
                    {"char_id": char_id},
                    {"$set": {"channel_msg_id": channel_msg.id if channel_msg else None}}
                )
                channel_posted += 1
            except Exception as ce:
                await report_system_error(f"Bulk-by-rarity AddArtist channel post ({char_id})", ce)
                channel_failed += 1
            await asyncio.sleep(3)
    await status_msg.edit(
        f"🎨 <b>Artist set for every {r_info['name']} character!</b>\n"
        f"🎨 <b>Artist:</b> <code>{escape_html(artist_name)}</code>\n"
        f"🗄️ <b>Characters updated:</b> <code>{result.modified_count}</code>\n"
        f"📢 <b>Channel posts:</b> <code>{channel_posted}</code> sent, <code>{channel_failed}</code> failed",
        parse_mode='html'
    )

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]addartist(?:@\w+)?(?:\s+([\s\S]+))?$', 'bot1')))
async def add_artist_handler(event):
    if event.sender_id != OWNER_ID: return
    if is_duplicate_event(event): return
    arg_text = (event.pattern_match.group(1) or "").strip()
    # ✅ BULK-BY-RARITY MODE: "/addartist no.<N> [Artist Name]" — sets that artist on EVERY
    # character currently in rarity tier N at once, no CharID list needed. Checked before the
    # normal CharID/reply parsing below so "no.1 Morgan" is never mistaken for a (nonexistent)
    # CharID called "no.1".
    rarity_bulk_match = _ADDARTIST_RARITY_RE.match(arg_text) if arg_text else None
    if rarity_bulk_match:
        return await _bulk_add_artist_by_rarity(event, rarity_bulk_match.group(1), rarity_bulk_match.group(2))
    char_doc, artist_name = None, None
    # Try classic "/addartist <CharID> <Artist Name>" parsing first — even on a reply, in case
    # the owner replied out of habit but still typed the ID explicitly; an explicit ID always wins.
    if arg_text:
        parts = arg_text.split(None, 1)
        if len(parts) == 2:
            candidate = await characters_base_col.find_one({"char_id": normalize_char_id_input(parts[0])})
            if candidate:
                char_doc, artist_name = candidate, parts[1]
    # Reply mode: no valid CharID found above — the whole argument is just the artist name,
    # and the character comes from whatever message was replied to.
    if char_doc is None and event.is_reply:
        reply_msg = await event.get_reply_message()
        resolved = await _resolve_character_from_reply(reply_msg, event.chat_id)
        if resolved:
            char_doc = resolved
            artist_name = arg_text
        elif arg_text and reply_msg and reply_msg.text:
            # ✅ BULK MODE: replying to a plain message that's just a list of CharIDs, one per
            # line — e.g.
            #   1234
            #   4567
            #   6421
            # then "/addartist <Artist Name>" sets that SAME artist on every ID found in that
            # list in one shot, instead of one /addartist per card.
            id_lines = [ln.strip() for ln in reply_msg.text.splitlines() if ln.strip()]
            candidate_ids = []
            for ln in id_lines:
                token = ln.split()[0] if ln.split() else ln
                norm = normalize_char_id_input(token)
                if norm and norm not in candidate_ids:
                    candidate_ids.append(norm)
            if candidate_ids:
                return await _bulk_add_artist(event, candidate_ids, arg_text.strip())
    if not artist_name or not artist_name.strip():
        return await event.reply(
            "⚠️ <b>Usage:</b>\n"
            "<code>/addartist [CharID] [Artist Name]</code>\n"
            "<i>or reply to that character's card/image/video with</i> <code>/addartist [Artist Name]</code>\n"
            "<i>CharID works with or without the BOD prefix.</i>\n\n"
            "<b>Examples:</b>\n"
            "<code>/addartist 1234 Morgan</code>\n"
            "<i>(replying to the character's media)</i> <code>/addartist Morgan</code>\n\n"
            "<b>Bulk by CharID list:</b> <i>reply to a message containing a list of CharIDs (one per line) with</i> "
            "<code>/addartist [Artist Name]</code> <i>to set it on all of them at once.</i>\n\n"
            "<b>Bulk by rarity:</b> <code>/addartist no.&lt;N&gt; [Artist Name]</code> "
            "<i>sets it on EVERY character in that rarity tier at once — e.g.</i> <code>/addartist no.1 Morgan</code>\n\n"
            "<i>Use</i> <code>-</code> <i>as the artist name to clear an existing credit (works with all three modes above).</i>",
            parse_mode='html'
        )
    if not char_doc:
        return await event.reply(
            "❌ <b>Notice:</b> Couldn't figure out which character that is — "
            "double check the CharID, or reply directly to that character's card/image/video.",
            parse_mode='html'
        )
    char_id = char_doc["char_id"]
    artist_name = artist_name.strip()
    if artist_name == "-":
        await characters_base_col.update_one({"char_id": char_id}, {"$unset": {"artist": ""}})
        await invalidate_character_caches()
        return await event.reply(
            f"🎨 <b>Artist credit cleared.</b>\n"
            f"🆔 <b>Character:</b> <code>{display_char_id(char_id)}</code> — <b>{escape_html(char_doc['name'])}</b>",
            parse_mode='html'
        )
    await characters_base_col.update_one({"char_id": char_id}, {"$set": {"artist": artist_name}})
    await invalidate_character_caches()
    # 🩹 NEW (per owner request): announce this to the channel too.
    channel_note = ""
    updated_doc = await characters_base_col.find_one({"char_id": char_id})
    if CHARACTER_CHANNEL_ID:
        try:
            actor_mention = build_actor_mention(await event.get_sender())
            channel_msg = await post_character_to_channel(updated_doc, is_new=False, actor_mention=actor_mention)
            await characters_base_col.update_one(
                {"char_id": char_id},
                {"$set": {"channel_msg_id": channel_msg.id if channel_msg else None}}
            )
            channel_note = "\n📢 Channel: Posted ✅"
        except Exception as ce:
            await report_system_error(f"AddArtist channel post ({char_id})", ce)
            channel_note = f"\n⚠️ Channel: Post failed — <code>{escape_html(str(ce))}</code>"
    await event.reply(
        f"<b>Noted down.</b>\n"
        f"🆔 <b>Character:</b> <code>{display_char_id(char_id)}</code> — <b>{escape_html(char_doc['name'])}</b>\n"
        f"🎨 <b>Artist:</b> <code>{escape_html(artist_name)}</code>\n\n"
        f"<i>This credit will now show up anywhere this character card appears.</i>"
        f"{channel_note}",
        parse_mode='html'
    )

# ==========================================
# 🎨 /linkartist — owner-only. /addartist only stores an artist's NAME on a character (free
# text, no Telegram account attached), which is all it ever needed to do for display purposes.
# But the Guard Bot's "pay the artist ⭐ Star when their card gets collected" feature needs an
# actual account to pay — this command is that missing link: it maps an artist NAME to a real
# Telegram user_id, once, and every card already/later credited to that name benefits from it.
# ==========================================
GUARD_ARTIST_REWARD_MIN_VLT = 5   # 💠 — was 2-7 ⭐ (worth roughly 0.3-1.2💠 at the going Star<->VLT
GUARD_ARTIST_REWARD_MAX_VLT = 15  # 💠   rate) — per owner request: switched to VLT, and bumped up

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]linkartist(?:@\w+)?(?:\s+([\s\S]+))?$', 'bot1')))
async def link_artist_handler(event):
    if event.sender_id != OWNER_ID: return
    arg_text = (event.pattern_match.group(1) or "").strip()
    target_user_id, artist_name = None, None
    if event.is_reply:
        reply_msg = await event.get_reply_message()
        if reply_msg and reply_msg.sender_id:
            target_user_id = reply_msg.sender_id
            artist_name = arg_text
    if target_user_id is None and arg_text:
        parts = arg_text.split()
        if parts and parts[-1].lstrip('-').isdigit():
            target_user_id = int(parts[-1])
            artist_name = " ".join(parts[:-1])
    if not artist_name or not artist_name.strip() or not target_user_id:
        return await event.reply(
            "⚠️ <b>Usage:</b>\n"
            "<i>Reply to that artist's message with</i> <code>/linkartist [Artist Name]</code>\n"
            "<i>or, without a reply:</i> <code>/linkartist [Artist Name] [user_id]</code>\n\n"
            "<b>Example:</b> <code>/linkartist Morgan 123456789</code>\n\n"
            "🎨 This just links the NAME already used in <code>/addartist</code> to a real Telegram "
            "account, so the Guard Bot has somewhere to send their ⭐ Star reward when a card "
            "credited to that name gets collected. It doesn't change any card's artist credit.",
            parse_mode='html'
        )
    artist_name = artist_name.strip()
    display_name = (await get_plain_name(event, target_user_id)) if event.is_reply else artist_name
    await artists_col.update_one(
        {"artist_name": artist_name.lower()},
        {"$set": {"artist_name": artist_name.lower(), "display_name": display_name,
                   "user_id": target_user_id, "linked_at": time.time(), "linked_by": OWNER_ID}},
        upsert=True
    )
    mention = f"<a href='tg://user?id={target_user_id}'>{escape_html(display_name)}</a>"
    await event.reply(
        f"🎨 <b>Artist linked!</b>\n"
        f"<b>Name:</b> <code>{escape_html(artist_name)}</code> ↔ {mention}\n\n"
        f"Every character already credited to <code>{escape_html(artist_name)}</code> (and any added "
        f"later) will now pay {GUARD_ARTIST_REWARD_MIN_VLT}~{GUARD_ARTIST_REWARD_MAX_VLT}💠 VLT to this account "
        f"whenever it's collected.",
        parse_mode='html'
    )

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]unlinkartist(?:@\w+)?(?:\s+([\s\S]+))?$', 'bot1')))
async def unlink_artist_handler(event):
    if event.sender_id != OWNER_ID: return
    artist_name = (event.pattern_match.group(1) or "").strip()
    if not artist_name:
        return await event.reply("⚠️ <b>Usage:</b> <code>/unlinkartist [Artist Name]</code>", parse_mode='html')
    result = await artists_col.delete_one({"artist_name": artist_name.lower()})
    if result.deleted_count:
        await event.reply(f"🎨 <b>Unlinked</b> <code>{escape_html(artist_name)}</code>. Collect rewards for this name stop until relinked.", parse_mode='html')
    else:
        await event.reply(f"❌ <b>No link found for</b> <code>{escape_html(artist_name)}</code>.", parse_mode='html')

async def _maybe_reward_artist_for_collect(artist_name, catcher_mention, character_name, character_id):
    """Fire-and-forget: if `artist_name` (from a caught card's 'artist' field) is linked to a
    Telegram account via /linkartist, credits that account a random 💠 VLT reward and
    announces it in CHARACTER_CHANNEL_ID (the same channel /addchar posts to). Does nothing —
    silently and cheaply — if there's no artist credit on the card or no link registered for
    it yet.
    🩹 CHANGED (per owner request): this used to announce in FORCE_SUB_CHAT_ID via bot3.
    Moved to CHARACTER_CHANNEL_ID via bot1 instead — bot3 only ever lives in FORCE_SUB_CHAT_ID
    and has no access to the character channel, so bot1 (the account that already posts every
    /addchar there) sends this too.
    🩹 CHANGED (per owner request): the announcement now shows the character's ID alongside its
    name — name alone was ambiguous whenever two cards share a similar or identical name, so
    there was no reliable way to tell which exact card someone actually got."""
    if not artist_name or not str(artist_name).strip():
        return
    try:
        link = await artists_col.find_one({"artist_name": str(artist_name).strip().lower()})
        if not link or not link.get("user_id"):
            return
        artist_user_id = link["user_id"]
        vlt_amount = round(random.uniform(GUARD_ARTIST_REWARD_MIN_VLT, GUARD_ARTIST_REWARD_MAX_VLT), 2)
        await users_catcher_col.update_one({"user_id": artist_user_id}, {"$inc": {"vlt_balance": vlt_amount}}, upsert=True)
        if not CHARACTER_CHANNEL_ID:
            return
        artist_mention = f"<a href='tg://user?id={artist_user_id}'>{escape_html(link.get('display_name') or str(artist_name))}</a>"
        await bot1.send_message(
            CHARACTER_CHANNEL_ID,
            f"🎨 <b>Artist Reward!</b>\n{catcher_mention} collected <b>{escape_html(character_name)}</b> "
            f"(<code>{display_char_id(character_id)}</code>), drawn by "
            f"{artist_mention} — <code>+{format_vlt_plain(vlt_amount)}</code> sent their way!",
            parse_mode='html'
        )
    except Exception as e:
        print(f"⚠️ Artist reward failed for '{artist_name}': {e}")

# ---- SETCHAR ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]setchar(?:@\w+)?(?:\s+([\s\S]+))?$', 'bot1')))
async def bulk_set_char_handler(event):
    if event.sender_id != OWNER_ID: return
    raw_block = event.pattern_match.group(1)
    if not raw_block and event.is_reply:
        reply_msg = await event.get_reply_message()
        if reply_msg and reply_msg.text:
            raw_block = reply_msg.text
    if not raw_block or not raw_block.strip():
        return await event.reply(
            "⚠️ <b>Usage</b>\n"
            "Send (or reply to) a block of lines — one character per line:\n"
            "<code>CharID | Event | CatchLimit</code>\n\n"
            "<b>Example</b> (paste as many lines as you like, e.g. 15 or 30 at once):\n"
            "<code>BOD1234 | Lightning⚡ | 75\nBOD5678 | Fire Festival | 30\nBOD9012 | - | 0</code>\n\n"
            "<i>Use - to leave a field unchanged. CatchLimit 0 = infinite.</i>",
            parse_mode='html'
        )
    lines = [ln.strip() for ln in raw_block.strip().splitlines() if ln.strip()]
    updated, skipped, failed = [], [], []
    for ln in lines:
        parts = [p.strip() for p in ln.split('|')]
        if len(parts) < 3:
            failed.append(f"<code>{escape_html(ln[:40])}</code> — needs 3 parts (CharID | Event | CatchLimit)")
            continue
        char_id, ev_raw, limit_raw = parts[0].upper(), parts[1], parts[2]
        char_doc = await characters_base_col.find_one({"char_id": char_id})
        if not char_doc:
            failed.append(f"<code>{char_id}</code> — not found")
            continue
        update_fields = {}
        if ev_raw and ev_raw != "-":
            update_fields["event"] = ev_raw
        if limit_raw and limit_raw != "-":
            if not limit_raw.lstrip('-').isdigit():
                failed.append(f"<code>{char_id}</code> — CatchLimit must be a whole number")
                continue
            update_fields["spawn_limit"] = max(0, int(limit_raw))
        if not update_fields:
            skipped.append(char_id)
            continue
        await characters_base_col.update_one({"char_id": char_id}, {"$set": update_fields})
        updated.append(char_id)
    if updated:
        await invalidate_character_caches()
    summary = [f"✅ <b>Bulk Set Complete!</b>\n🔁 <b>Updated:</b> <code>{len(updated)}</code> character(s) out of <code>{len(lines)}</code> line(s)"]
    if updated:
        shown = ", ".join(f"<code>{c}</code>" for c in updated[:40])
        if len(updated) > 40:
            shown += " …"
        summary.append(f"<blockquote>{shown}</blockquote>")
    if skipped:
        summary.append(f"➖ <b>Skipped</b> (nothing to change): <code>{len(skipped)}</code>")
    if failed:
        fail_preview = "\n".join(failed[:20])
        if len(failed) > 20:
            fail_preview += "\n…"
        summary.append(f"❌ <b>Failed:</b> <code>{len(failed)}</code>")
        summary.append(f"<blockquote>{fail_preview}</blockquote>")
    await event.reply("\n".join(summary), parse_mode='html')

# ---- CHANGE ALL RARITY (rebrand rarity emoji/name/value everywhere, DB-wide) ----
def _current_official_rarity_fields(old_rarity_str, fallback_tier=None):
    """Given whatever rarity string is currently stored (old emoji, old font, anything),
    work out its canonical tier and return today's official RARITY_NUM_MAP name + value
    for it. Returns (new_name, new_value, tier) or (None, None, "OTHER") if unclassifiable."""
    tier = fallback_tier or classify_rarity(old_rarity_str)
    num = RARITY_TIER_TO_NUM.get(tier)
    if not num:
        return None, None, "OTHER"
    return RARITY_NUM_MAP[num]["name"], RARITY_NUM_MAP[num]["value"], tier

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]changeallrarity(?:@\w+)?(?:\s+(\S+))?$', 'bot1')))
async def change_all_rarity_handler(event):
    """Owner-only. Rewrites EVERY stored rarity name (+ currency_value on the base character
    record) in the database (characters_base_data, characters, and every user's harem) to
    today's official RARITY_NUM_MAP name/emoji/value, so no old rarity name, emoji, or
    stale value is left anywhere — old data included. Run with 'confirm'."""
    if event.sender_id != OWNER_ID: return
    confirm_arg = (event.pattern_match.group(1) or "").lower()

    legend = "\n".join(
        f"<code>{num}</code> = {RARITY_NUM_MAP[num]['name']} (<code>{RARITY_NUM_MAP[num]['value']}</code>💠)"
        for num in sorted(RARITY_NUM_MAP.keys())
    )

    if confirm_arg != "confirm":
        return await event.reply(
            f"⚠️ <b>{f('CHANGE ALL RARITY')}</b>\n"
            f"This rewrites the <b>rarity</b> name/emoji AND <b>currency_value</b> on every "
            f"character in the database (+ the <b>rarity</b> name inside every player's "
            f"harem) to the current official values below. Old rarity names/emoji/values "
            f"will no longer appear anywhere.\n\n"
            f"🔢 <b>{f('Current Rarity Set')}:</b>\n{legend}\n\n"
            f"❗ <b>{f('This cannot be undone.')}</b>\n"
            f"Run <code>/changeallrarity confirm</code> to proceed.",
            parse_mode='html'
        )

    status_msg = await event.reply("⏳ <b>Rebranding rarity across the database...</b>", parse_mode='html')

    chars_updated, chars_skipped = 0, 0
    for col in (characters_base_col, characters_col):
        docs = await col.find({}, {"_id": 1, "rarity": 1, "rarity_tier": 1}).to_list(length=None)
        if not docs:
            continue
        ops = []
        for doc in docs:
            new_name, new_value, tier = _current_official_rarity_fields(doc.get("rarity", ""), doc.get("rarity_tier"))
            if not new_name:
                chars_skipped += 1
                continue
            ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": {"rarity": new_name, "rarity_tier": tier, "currency_value": new_value}}))
        if ops:
            for i in range(0, len(ops), 500):
                await col.bulk_write(ops[i:i + 500])
            chars_updated += len(ops)

    harem_users_updated, harem_items_updated, harem_users_skipped = 0, 0, 0
    cursor = users_catcher_col.find({"harem.0": {"$exists": True}}, {"_id": 1, "harem": 1})
    batch = []
    async for user_doc in cursor:
        harem = user_doc.get("harem") or []
        changed = False
        new_harem = []
        for item in harem:
            if not isinstance(item, dict):
                new_harem.append(item)
                continue
            new_name, _new_value, _tier = _current_official_rarity_fields(item.get("rarity", ""))
            if new_name and new_name != item.get("rarity"):
                item = {**item, "rarity": new_name}
                changed = True
                harem_items_updated += 1
            new_harem.append(item)
        if changed:
            batch.append(UpdateOne({"_id": user_doc["_id"]}, {"$set": {"harem": new_harem}}))
            harem_users_updated += 1
        else:
            harem_users_skipped += 1
        if len(batch) >= 500:
            await users_catcher_col.bulk_write(batch)
            batch = []
    if batch:
        await users_catcher_col.bulk_write(batch)

    await invalidate_character_caches()
    try:
        await redis_client.delete("cache:chars:all", "cache:chars:categories")
    except Exception:
        pass

    await status_msg.edit(
        f"✅ <b>{f('RARITY REBRAND COMPLETE')}</b>\n"
        f"🗄️ <b>{f('Characters updated')}:</b> <code>{chars_updated}</code>"
        + (f" <i>(⚠️ {chars_skipped} unclassifiable, left untouched)</i>" if chars_skipped else "") + "\n"
        f"🎒 <b>{f('Players with harem updated')}:</b> <code>{harem_users_updated}</code>\n"
        f"🃏 <b>{f('Harem cards renamed')}:</b> <code>{harem_items_updated}</code>\n\n"
        f"🔢 <b>{f('New Rarity Set')}:</b>\n{legend}",
        parse_mode='html'
    )




# ==========================================
# 🔁 .cr — owner-only, force-changes ONE specific character's rarity tier by number
# (e.g. ".cr BOD1234 no3" moves BOD1234 to Rarity No.3), rewriting its stored rarity
# name/emoji/value AND every copy already sitting in players' harems, so the change is
# immediate and consistent everywhere (/harem, /check, marketplace, etc. all read rarity
# live off these same fields).
# ==========================================
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]cr(?:@\w+)?$', 'bot1')))
async def change_single_rarity_usage_handler(event):
    if event.sender_id != OWNER_ID: return
    legend = "\n".join(
        f"<code>no{num}</code> = {RARITY_NUM_MAP[num]['name']}" for num in sorted(RARITY_NUM_MAP.keys())
    )
    await event.reply(
        f"📌 <b>Usage:</b> <code>/cr CharID no&lt;N&gt;</code>\n"
        f"<i>Example:</i> <code>/cr BOD1234 no3</code> — force-changes BOD1234 to Rarity No.3.\n\n"
        f"📚 <b>Bulk:</b> reply to a message listing many CharIDs (spaces/commas/newlines) "
        f"with just <code>/cr no&lt;N&gt;</code> to change all of them at once.\n\n"
        f"🔢 <b>Rarity Tiers:</b>\n{legend}",
        parse_mode='html'
    )

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]cr(?:@\w+)?\s+(\S+)\s+(?:no)?([1-9])$', 'bot1')))
async def change_single_rarity_handler(event):
    if event.sender_id != OWNER_ID: return
    char_id = normalize_char_id_input(event.pattern_match.group(1))
    rarity_num = event.pattern_match.group(2)
    char_doc = await characters_base_col.find_one({"char_id": char_id})
    if not char_doc:
        return await event.reply(f"❌ <b>No character found with ID</b> <code>{display_char_id(char_id)}</code>.", parse_mode='html')
    r_info = RARITY_NUM_MAP[rarity_num]
    new_tier = RARITY_TIERS[int(rarity_num) - 1]
    old_rarity_display = char_doc.get("rarity", "Unknown")
    old_tier = char_doc.get("rarity_tier") or classify_rarity(old_rarity_display)
    if old_tier == new_tier:
        return await event.reply(
            f"⚠️ <b>{escape_html(char_doc.get('name', ''))}</b> (<code>{display_char_id(char_id)}</code>) "
            f"is already {r_info['name']}. Nothing to change.",
            parse_mode='html'
        )
    status_msg = await event.reply("⏳ <b>Changing rarity...</b>", parse_mode='html')
    await characters_base_col.update_one(
        {"char_id": char_id},
        {"$set": {"rarity": r_info["name"], "rarity_tier": new_tier, "currency_value": r_info["value"]}}
    )
    # Rewrite every existing harem copy of THIS exact character so already-owned cards
    # immediately reflect the new rarity too — same technique /changeallrarity uses,
    # just scoped to a single char_id via the existing harem.char_id index.
    harem_users_updated, harem_items_updated = 0, 0
    batch = []
    cursor = users_catcher_col.find({"harem.char_id": char_id}, {"_id": 1, "harem": 1})
    async for user_doc in cursor:
        harem = user_doc.get("harem") or []
        changed = False
        new_harem = []
        for item in harem:
            if isinstance(item, dict) and item.get("char_id") == char_id and item.get("rarity") != r_info["name"]:
                item = {**item, "rarity": r_info["name"]}
                changed = True
                harem_items_updated += 1
            new_harem.append(item)
        if changed:
            batch.append(UpdateOne({"_id": user_doc["_id"]}, {"$set": {"harem": new_harem}}))
            harem_users_updated += 1
        if len(batch) >= 500:
            await users_catcher_col.bulk_write(batch)
            batch = []
    if batch:
        await users_catcher_col.bulk_write(batch)
    await invalidate_character_caches()
    updated_doc = await characters_base_col.find_one({"char_id": char_id})
    channel_note = ""
    if CHARACTER_CHANNEL_ID:
        try:
            actor_mention = build_actor_mention(await event.get_sender())
            channel_msg = await post_character_to_channel(updated_doc, is_new=False, actor_mention=actor_mention)
            await characters_base_col.update_one(
                {"char_id": char_id},
                {"$set": {"channel_msg_id": channel_msg.id if channel_msg else None}}
            )
            channel_note = "\n📢 Channel: Posted ✅"
        except Exception as ce:
            await report_system_error(f"ChangeRarity channel post ({char_id})", ce)
            channel_note = f"\n⚠️ Channel: Post failed — <code>{escape_html(str(ce))}</code>"
    await status_msg.edit(
        f"✅ <b>Rarity Changed!</b>\n\n"
        f"✨ <b>Character:</b> <code>{escape_html(char_doc.get('name', ''))}</code> (<code>{display_char_id(char_id)}</code>)\n"
        f"🔁 {old_rarity_display} ➜ {r_info['name']}\n\n"
        f"🎒 <b>Players updated:</b> <code>{harem_users_updated}</code>\n"
        f"🃏 <b>Harem cards renamed:</b> <code>{harem_items_updated}</code>"
        f"{channel_note}",
        parse_mode='html'
    )

# ---- .cr BULK FORM: reply to a message containing many CharIDs (any mix of spaces,
# commas, or newlines, with or without the "BOD" prefix — up to a hundred+ at once) with
# just ".cr no<N>" to force-change ALL of them to that rarity tier in a single shot. The
# single-target ".cr CharID no<N>" form above still works unchanged for one-off edits. ----
CHAR_ID_TOKEN_RE = re.compile(r'^(?:bod)?\d+$', re.IGNORECASE)

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]cr(?:@\w+)?\s+(?:no)?([1-9])$', 'bot1')))
async def change_bulk_rarity_handler(event):
    if event.sender_id != OWNER_ID: return
    if not event.is_reply:
        return await event.reply(
            "📌 <b>Bulk usage:</b> reply to a message that lists the CharIDs "
            "(separated by spaces, commas, or newlines — as many as you like) with "
            "<code>/cr no&lt;N&gt;</code>.\n"
            "<i>For a single card instead, use </i><code>/cr CharID no&lt;N&gt;</code>.",
            parse_mode='html'
        )
    rarity_num = event.pattern_match.group(1)
    reply_msg = await event.get_reply_message()
    raw_block = reply_msg.text if reply_msg else None
    if not raw_block or not raw_block.strip():
        return await event.reply("⚠️ <b>That message has no CharIDs I can read.</b>", parse_mode='html')

    # Pull out only clearly ID-like tokens (digits, optionally "BOD"-prefixed) — anything
    # else in the replied message (notes, punctuation, etc.) is silently ignored rather
    # than showing up as a noisy "not found" entry.
    char_ids, seen = [], set()
    for tok in re.split(r'[\s,]+', raw_block.strip()):
        tok = tok.strip()
        if not tok or not CHAR_ID_TOKEN_RE.match(tok):
            continue
        cid = normalize_char_id_input(tok)
        if cid not in seen:
            seen.add(cid)
            char_ids.append(cid)
    if not char_ids:
        return await event.reply("⚠️ <b>Couldn't find any CharIDs in that message.</b>", parse_mode='html')

    r_info = RARITY_NUM_MAP[rarity_num]
    new_tier = RARITY_TIERS[int(rarity_num) - 1]
    status_msg = await event.reply(f"⏳ <b>Changing rarity for {len(char_ids)} character(s)...</b>", parse_mode='html')

    # One query to fetch everything, one bulk_write to update everything — instead of a
    # find_one/update_one round trip per character, which is what makes this safe to run
    # against a hundred-plus IDs at once instead of just a couple.
    found_docs = await characters_base_col.find({"char_id": {"$in": char_ids}}).to_list(length=None)
    found_map = {d["char_id"]: d for d in found_docs}

    changed, already, not_found = [], [], []
    char_update_ops = []
    for cid in char_ids:
        char_doc = found_map.get(cid)
        if not char_doc:
            not_found.append(cid)
            continue
        old_tier = char_doc.get("rarity_tier") or classify_rarity(char_doc.get("rarity", ""))
        if old_tier == new_tier:
            already.append(cid)
            continue
        char_update_ops.append(UpdateOne(
            {"char_id": cid},
            {"$set": {"rarity": r_info["name"], "rarity_tier": new_tier, "currency_value": r_info["value"]}}
        ))
        changed.append(cid)

    harem_users_updated, harem_items_updated = 0, 0
    if char_update_ops:
        for i in range(0, len(char_update_ops), 500):
            await characters_base_col.bulk_write(char_update_ops[i:i + 500])
        # Rewrite every existing harem copy of every changed char_id in ONE cursor pass.
        changed_set = set(changed)
        batch = []
        cursor = users_catcher_col.find({"harem.char_id": {"$in": changed}}, {"_id": 1, "harem": 1})
        async for user_doc in cursor:
            harem = user_doc.get("harem") or []
            user_changed = False
            new_harem = []
            for item in harem:
                if isinstance(item, dict) and item.get("char_id") in changed_set and item.get("rarity") != r_info["name"]:
                    item = {**item, "rarity": r_info["name"]}
                    user_changed = True
                    harem_items_updated += 1
                new_harem.append(item)
            if user_changed:
                batch.append(UpdateOne({"_id": user_doc["_id"]}, {"$set": {"harem": new_harem}}))
                harem_users_updated += 1
            if len(batch) >= 500:
                await users_catcher_col.bulk_write(batch)
                batch = []
        if batch:
            await users_catcher_col.bulk_write(batch)
        await invalidate_character_caches()

    channel_posted, channel_failed = 0, 0
    if changed and CHARACTER_CHANNEL_ID:
        actor_mention = build_actor_mention(await event.get_sender())
        for cid in changed:
            fresh_doc = await characters_base_col.find_one({"char_id": cid})
            if not fresh_doc:
                continue
            try:
                channel_msg = await post_character_to_channel(fresh_doc, is_new=False, actor_mention=actor_mention)
                await characters_base_col.update_one(
                    {"char_id": cid},
                    {"$set": {"channel_msg_id": channel_msg.id if channel_msg else None}}
                )
                channel_posted += 1
            except Exception as ce:
                await report_system_error(f"Bulk ChangeRarity channel post ({cid})", ce)
                channel_failed += 1
            await asyncio.sleep(3)  # flood-safe pacing, same rate as /stardrop's broadcast loop

    summary = [
        f"✅ <b>Bulk Rarity Change Complete!</b>\n"
        f"🔁 <b>New Rarity:</b> {r_info['name']}\n"
        f"✨ <b>Changed:</b> <code>{len(changed)}</code> / <code>{len(char_ids)}</code> character(s)"
    ]
    if CHARACTER_CHANNEL_ID and changed:
        summary.append(f"📢 <b>Channel posts:</b> <code>{channel_posted}</code> sent, <code>{channel_failed}</code> failed")
    if changed:
        shown = ", ".join(f"<code>{display_char_id(c)}</code>" for c in changed[:40])
        if len(changed) > 40:
            shown += " …"
        summary.append(f"<blockquote>{shown}</blockquote>")
    if already:
        summary.append(f"➖ <b>Already this rarity:</b> <code>{len(already)}</code>")
    if not_found:
        preview = ", ".join(f"<code>{display_char_id(c)}</code>" for c in not_found[:20])
        if len(not_found) > 20:
            preview += " …"
        summary.append(f"❌ <b>Not found:</b> <code>{len(not_found)}</code>\n<blockquote>{preview}</blockquote>")
    summary.append(f"🎒 <b>Players updated:</b> <code>{harem_users_updated}</code> | 🃏 <b>Harem cards renamed:</b> <code>{harem_items_updated}</code>")
    await status_msg.edit("\n".join(summary), parse_mode='html')

# ==========================================
# 🎪 .setevent — owner-only, bulk-sets the Event field (used for LE/collection tags like
# "🍓wifeys🍓") on MANY characters at once. Same reply-to-a-list-of-CharIDs pattern as the
# .cr bulk form above — reply to a message containing CharIDs (spaces/commas/newlines, "BOD"
# prefix optional) with ".setevent <EventName>" and every one of them gets that EXACT Event
# string. Unlike rarity, "event" only ever lives on the character_base doc itself (never
# copied into a player's harem entry — see add_character()'s character_data), so this is a
# single bulk_write against characters_base_col, no harem rewrite pass needed.
# For a ONE-OFF single character instead, /editchar CharID already covers that.
# ==========================================
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]setevent(?:@\w+)?$', 'bot1')))
async def set_event_bulk_usage_handler(event):
    if event.sender_id != OWNER_ID: return
    await event.reply(
        "📌 <b>Bulk usage:</b> reply to a message that lists the CharIDs "
        "(separated by spaces, commas, or newlines — as many as you like) with "
        "<code>/setevent EventName</code> to set ALL of them to that exact Event/LE tag.\n"
        "<i>For a single character instead, use </i><code>/editchar CharID</code>.",
        parse_mode='html'
    )

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]setevent(?:@\w+)?\s+(.+)$', 'bot1')))
async def set_event_bulk_handler(event):
    if event.sender_id != OWNER_ID: return
    if not event.is_reply:
        return await event.reply(
            "📌 <b>Reply to a message listing CharIDs</b> (spaces/commas/newlines) with "
            "<code>/setevent EventName</code>.\n"
            "<i>For a single character instead, use </i><code>/editchar CharID</code>.",
            parse_mode='html'
        )
    new_event = event.pattern_match.group(1).strip()
    if not new_event:
        return await event.reply("⚠️ <b>Event name can't be empty.</b>", parse_mode='html')
    reply_msg = await event.get_reply_message()
    raw_block = reply_msg.text if reply_msg else None
    if not raw_block or not raw_block.strip():
        return await event.reply("⚠️ <b>That message has no CharIDs I can read.</b>", parse_mode='html')

    char_ids, seen = [], set()
    for tok in re.split(r'[\s,]+', raw_block.strip()):
        tok = tok.strip()
        if not tok or not CHAR_ID_TOKEN_RE.match(tok):
            continue
        cid = normalize_char_id_input(tok)
        if cid not in seen:
            seen.add(cid)
            char_ids.append(cid)
    if not char_ids:
        return await event.reply("⚠️ <b>Couldn't find any CharIDs in that message.</b>", parse_mode='html')

    status_msg = await event.reply(f"⏳ <b>Setting Event for {len(char_ids)} character(s)...</b>", parse_mode='html')

    found_docs = await characters_base_col.find(
        {"char_id": {"$in": char_ids}}, {"char_id": 1, "event": 1}
    ).to_list(length=None)
    found_map = {d["char_id"]: d for d in found_docs}

    changed, already, not_found = [], [], []
    char_update_ops = []
    for cid in char_ids:
        char_doc = found_map.get(cid)
        if not char_doc:
            not_found.append(cid)
            continue
        if char_doc.get("event") == new_event:
            already.append(cid)
            continue
        char_update_ops.append(UpdateOne({"char_id": cid}, {"$set": {"event": new_event}}))
        changed.append(cid)

    if char_update_ops:
        for i in range(0, len(char_update_ops), 500):
            await characters_base_col.bulk_write(char_update_ops[i:i + 500])
        await invalidate_character_caches()

    summary = [
        f"✅ <b>Bulk Event Update Complete!</b>\n"
        f"🎪 <b>New Event:</b> <code>{escape_html(new_event)}</code>\n"
        f"✨ <b>Changed:</b> <code>{len(changed)}</code> / <code>{len(char_ids)}</code> character(s)"
    ]
    if changed:
        shown = ", ".join(f"<code>{display_char_id(c)}</code>" for c in changed[:40])
        if len(changed) > 40:
            shown += " …"
        summary.append(f"<blockquote>{shown}</blockquote>")
    if already:
        summary.append(f"➖ <b>Already this Event:</b> <code>{len(already)}</code>")
    if not_found:
        preview = ", ".join(f"<code>{display_char_id(c)}</code>" for c in not_found[:20])
        if len(not_found) > 20:
            preview += " …"
        summary.append(f"❌ <b>Not found:</b> <code>{len(not_found)}</code>\n<blockquote>{preview}</blockquote>")
    await status_msg.edit("\n".join(summary), parse_mode='html')

# ---- /spawnweight: owner sets a GLOBAL level that boosts Rarity 1-4 spawn frequency (the
# same quiz-gated bracket) relative to its own defaults. Applies bot-wide no matter which
# chat the owner types it in — it's one shared setting, not a per-group one. ----
def _spawnweight_status_text():
    multiplier = SPAWNWEIGHT_LEVEL_MULTIPLIERS.get(_cached_spawnweight_level, 1)
    gated_list = "/".join(
        f"No.{RARITY_TIER_TO_NUM[t]} {t}"
        for t in sorted(RARITY_GATE_TIERS, key=lambda t: RARITY_TIER_TO_NUM[t])
    )
    levels_desc = "\n".join(
        f"<code>{lvl}</code> → <code>{mult}x</code>" for lvl, mult in sorted(SPAWNWEIGHT_LEVEL_MULTIPLIERS.items())
    )
    return (
        f"⚖️ <b>Rarity Spawn Weight</b> <i>(Global — same in every chat)</i>\n"
        f"<b>Current level:</b> <code>{_cached_spawnweight_level}</code> → <code>{multiplier}x</code> on {gated_list}\n\n"
        f"<b>Levels:</b>\n{levels_desc}\n\n"
        f"<b>Usage:</b> <code>/spawnweight &lt;level&gt;</code>\n"
        f"<i>Rarity 5-9 (the common tiers) are never affected — only the quiz-gated Rarity 1-4 "
        f"bracket scales, and it applies globally regardless of which chat you run this in.</i>"
    )

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]spawnweight(?:@\w+)?(?:\s+(\S+))?$', 'bot1')))
async def rarity_spawn_weight_handler(event):
    if event.sender_id != OWNER_ID: return
    global _cached_spawnweight_level
    arg = (event.pattern_match.group(1) or "").strip()

    if not arg:
        return await event.reply(_spawnweight_status_text(), parse_mode='html')

    try:
        level = int(arg)
    except ValueError:
        return await event.reply(
            "❌ <b>Level must be a whole number.</b>\n\n" + _spawnweight_status_text(),
            parse_mode='html'
        )
    if level not in SPAWNWEIGHT_LEVEL_MULTIPLIERS:
        valid = ", ".join(str(l) for l in sorted(SPAWNWEIGHT_LEVEL_MULTIPLIERS))
        return await event.reply(
            f"❌ <b>Unknown level.</b> Valid levels: <code>{valid}</code>\n\n" + _spawnweight_status_text(),
            parse_mode='html'
        )
    _cached_spawnweight_level = level
    await bot_settings_col.update_one({"_id": "rarity_spawn_weight_level"}, {"$set": {"level": level}}, upsert=True)
    multiplier = SPAWNWEIGHT_LEVEL_MULTIPLIERS[level]
    await event.reply(
        f"✅ <b>Spawn weight level set to {level}</b> ({multiplier}x on Rarity 1-4) — applies globally, in every chat.\n\n"
        + _spawnweight_status_text(),
        parse_mode='html'
    )

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]send(?:@\w+)?(?:\s+(.*))?$', 'bot1')))
async def broadcast(event):
    if event.sender_id != OWNER_ID: return
    reply_msg = await event.get_reply_message()
    command_text = event.pattern_match.group(1)
    if not reply_msg and not command_text: return
    success, fail = 0, 0
    status_msg = await event.respond(bq("<b>BROADCAST INITIATED</b>"), parse_mode='html')
    groups = await groups_col.find().to_list(length=None)
    for g in groups:
        chat_id = g['chat_id']
        try:
            if reply_msg: await bot1.forward_messages(chat_id, reply_msg)
            else: await bot1.send_message(chat_id, command_text, parse_mode='html')
            success += 1
            await asyncio.sleep(4)
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 2)
            try:
                if reply_msg: await bot1.forward_messages(chat_id, reply_msg)
                else: await bot1.send_message(chat_id, command_text, parse_mode='html')
                success += 104
            except: fail += 1
        except Exception: fail += 1
    await status_msg.edit(bq(f"<b>BROADCAST COMPLETE</b>\n✅ <b>Success:</b> <code>{success}</code>\n❌ <b>Failed:</b> None"), parse_mode='html')

# 🩹 ADDED (per owner request): welcome_goodbye's "bot added to new group" notice now also
# posts to CHARACTER_CHANNEL_ID going forward — but that only covers groups joined AFTER this
# shipped. This is the one-time (or run-whenever) catch-up: walks every group already in
# groups_col and posts the same style of entry for each. Same rate-limit pacing/FloodWaitError
# handling as /send above. "Added by" is left out here — Telegram doesn't retroactively expose
# who originally invited the bot to a group it's already in, only at the moment it happens.
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]syncgroups(?:@\w+)?$', 'bot1')))
async def sync_groups_to_channel(event):
    if event.sender_id != OWNER_ID: return
    if not CHARACTER_CHANNEL_ID:
        return await event.reply("❌ <b>CHARACTER_CHANNEL_ID</b> configure မထားပါ။", parse_mode='html')
    groups = await groups_col.find().to_list(length=None)
    if not groups:
        return await event.reply("📭 <b>No groups on record.</b>", parse_mode='html')
    status_msg = await event.reply(f"🔄 <b>Syncing {len(groups)} group(s) to the channel...</b>", parse_mode='html')
    posted, failed = 0, 0
    for g in groups:
        chat_id = g['chat_id']
        try:
            chat = await bot1.get_entity(chat_id)
            group_name = getattr(chat, 'title', None) or g.get('title') or "Unknown Group"
            is_broadcast_channel = bool(getattr(chat, 'broadcast', False) and not getattr(chat, 'megagroup', False))
            try:
                participants = await bot1.get_participants(chat_id, limit=0)
                member_count = f"{participants.total:,}" if hasattr(participants, 'total') else f"{len(participants):,}"
            except Exception:
                member_count = "Unknown"
            try:
                invite = await bot1(ExportChatInviteRequest(chat_id))
                group_link = invite.link if invite else "Not available (or bot not admin)"
            except Exception:
                group_link = "Cannot fetch (need admin rights)"
            kind_word = "channel" if is_broadcast_channel else "group"
            entry_text = (
                f"📋 <b>Already in this {kind_word}</b>\n\n"
                f"<b>{escape_html(group_name)}</b>\n"
                f"👥 <code>{member_count}</code> members · 🆔 <code>{chat_id}</code>\n"
                f"🔗 {escape_html(str(group_link))}"
            )
            await bot1.send_message(CHARACTER_CHANNEL_ID, entry_text, parse_mode='html')
            posted += 1
            await asyncio.sleep(4)
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 2)
            failed += 1
        except Exception as e:
            print(f"⚠️ /syncgroups failed for {chat_id}: {e}")
            failed += 1
    await status_msg.edit(f"✅ <b>Sync complete!</b> Posted <code>{posted}</code>, failed <code>{failed}</code>.", parse_mode='html')

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.](fspawn|haii)(?:@\w+)?$', 'bot1')))
async def force_spawn_by_owner(event):
    if event.sender_id != OWNER_ID: return
    await trigger_dynamic_spawn(event.chat_id)

# ⚡ PERFORMANCE: spawn_target used to be re-fetched from Mongo (1-2 reads) on literally
# every single group message — the hottest path in the whole bot. It only ever changes
# via an explicit owner command, so cache it and just bust the cache on that one write path.
_spawn_target_cache = bot_state._spawn_target_cache
SPAWN_TARGET_CACHE_TTL = 120  # seconds

async def get_spawn_target(chat_id):
    cached = _spawn_target_cache.get(chat_id)
    now = time.time()
    if cached and (now - cached[1]) < SPAWN_TARGET_CACHE_TTL:
        return cached[0]
    group_config = await groups_config_col.find_one({"chat_id": chat_id})
    if group_config and "spawn_target" in group_config:
        spawn_target = group_config["spawn_target"]
    else:
        global_config = await groups_config_col.find_one({"chat_id": GLOBAL_SPAWN_CHAT_KEY})
        spawn_target = global_config.get("spawn_target", 50) if global_config else 50
    _spawn_target_cache[chat_id] = (spawn_target, now)
    return spawn_target

# ---- AUTOMATIC SPAWN PROCESSOR ----
GROUP_COUNTER_FLUSH_SECONDS = 48
# how often the in-memory spawn counters get batched to Mongo
@bot1.on(events.NewMessage(incoming=True))
async def global_message_counter_handler(event):
    if event.is_private or event.chat_id == SPECIFIC_CONTROL_GROUP: return
    chat_id = event.chat_id

    # 🛡️ Tiny groups (see MIN_GROUP_MEMBERS_FOR_SPAWN) no longer get blocked outright — they
    # just need double the usual message count before a character spawn fires (applied to
    # spawn_target further down). Cache-only read — never awaits Telegram's API here (see
    # get_cached_group_member_count_nowait's docstring). Unknown (None) fails OPEN — treated as
    # "not small" — so a cold cache can never silently double the threshold in a legitimate,
    # populated group; a background task keeps the cache warm for next time.
    member_count = get_cached_group_member_count_nowait(chat_id)
    if member_count is None:
        schedule_group_member_count_refresh(chat_id)
    is_small_group = member_count is not None and member_count < MIN_GROUP_MEMBERS_FOR_SPAWN

    # 🧠 TRIVIA AUTO-SPAWN — deliberately checked FIRST and kept fully independent of the
    # character-spawn counter below: it ticks on every group message regardless of whether a
    # character spawn or rarity-gate quiz is currently active in this chat, and is only ever
    # paused by its own pending_trivia_quiz (so a chat never gets two trivia prompts stacked on
    # top of each other). In-memory only, no Mongo persistence — a restart just costs at most
    # one trivia chat's worth of progress toward its next question, never anything durable.
    if _cached_trivia_enabled and chat_id not in pending_trivia_quiz:
        trivia_target = get_trivia_target(chat_id)
        new_trivia_count = trivia_spawn_counters.get(chat_id, 0) + 1
        if new_trivia_count >= trivia_target:
            trivia_spawn_counters[chat_id] = 0
            trivia_spawn_targets.pop(chat_id, None)
            asyncio.create_task(trigger_trivia_spawn(chat_id))
        else:
            trivia_spawn_counters[chat_id] = new_trivia_count

    if chat_id in active_group_spawns or chat_id in pending_rarity_quiz: return
    spawn_target = await get_spawn_target(chat_id)
    if is_small_group:
        # 🩹 CHANGED (2026-08, per owner request): tiny groups used to be blocked from
        # spawning entirely — now they just need double the normal haitime/spawn_target
        # (e.g. haitime 100 → 200 messages for these groups) instead of being shut off.
        spawn_target *= 2
    # ⚡ PERFORMANCE: this used to be a MongoDB find_one_and_update on EVERY single group
    # message — the single biggest source of database load in the whole bot (one write per
    # message, across every active group, all day long). The counter now lives purely in
    # memory (group_spawn_counters, on bot_state) so a normal message costs zero DB calls.
    # Durability is handled two other ways instead: a cheap periodic bulk write every
    # GROUP_COUNTER_FLUSH_SECONDS (see group_counter_flush_loop), and one immediate write the
    # moment a spawn actually fires (rare — once per spawn_target messages, not per message).
    # Worst case on a crash: up to ~GROUP_COUNTER_FLUSH_SECONDS of message-count progress
    # toward the *next* spawn is lost — never a spawn itself, never any other state.
    new_count = group_spawn_counters.get(chat_id, 0) + 1
    group_spawn_counters[chat_id] = new_count
    if new_count >= spawn_target:
        # 🛡️ RELIABILITY: subtract spawn_target instead of hard-resetting to 0, so any
        # "overshoot" from the threshold check never silently disappears (same reasoning as
        # the old Mongo $inc version — just applied to the in-memory value now).
        group_spawn_counters[chat_id] = new_count - spawn_target
        try:
            await groups_counters_col.update_one(
                {"chat_id": chat_id},
                {"$set": {"counter": group_spawn_counters[chat_id]}},
                upsert=True
            )
        except Exception as e:
            logging.error(f"Group counter persist-on-spawn error: {e}")
        await trigger_dynamic_spawn(chat_id)

async def load_group_spawn_counters_cache():
    """One-time load of persisted spawn counters into memory on boot, so a restart doesn't
    reset every group's progress back to 0 — only whatever hadn't been flushed yet (at most
    ~GROUP_COUNTER_FLUSH_SECONDS worth) is at risk."""
    try:
        docs = await groups_counters_col.find({}, {"chat_id": 1, "counter": 1}).to_list(length=None)
        for d in docs:
            group_spawn_counters[d["chat_id"]] = d.get("counter", 0)
        print(f"📥 Loaded {len(docs)} group spawn counters into memory.")
    except Exception as e:
        print(f"load_group_spawn_counters_cache error: {e}")

async def group_counter_flush_loop():
    """Batches every group's in-memory spawn counter to Mongo in a single bulk_write every
    GROUP_COUNTER_FLUSH_SECONDS — this is the ONLY place (besides the on-spawn write above)
    that persists group_spawn_counters, replacing what used to be a write on every message."""
    while True:
        await asyncio.sleep(GROUP_COUNTER_FLUSH_SECONDS)
        try:
            snapshot = dict(group_spawn_counters)
            if not snapshot:
                continue
            ops = [
                UpdateOne({"chat_id": cid}, {"$set": {"counter": val}}, upsert=True)
                for cid, val in snapshot.items()
            ]
            await groups_counters_col.bulk_write(ops, ordered=False)
        except Exception as e:
            logging.error(f"Group counter flush error: {e}")

def get_trivia_target(chat_id):
    """Next message-count threshold before this chat's next trivia auto-spawn — a fresh random
    value between TRIVIA_SPAWN_MIN_MSGS and TRIVIA_SPAWN_MAX_MSGS, rolled once and cached in
    trivia_spawn_targets until the trivia actually fires (then trigger_trivia_spawn's caller
    pops it so a new random target gets rolled for the next cycle). In-memory only, same
    reasoning as group_spawn_counters — worst case on a restart is losing progress toward the
    next question, never anything durable."""
    target = trivia_spawn_targets.get(chat_id)
    if target is None:
        target = random.randint(TRIVIA_SPAWN_MIN_MSGS, TRIVIA_SPAWN_MAX_MSGS)
        trivia_spawn_targets[chat_id] = target
    return target

# ---- TRIGGER DYNAMIC SPAWN ----
async def trigger_dynamic_spawn(chat_id):
    if chat_id in active_group_spawns or chat_id in pending_rarity_quiz: return
    # 🔒 Two messages can cross the spawn_target threshold within milliseconds of each other —
    # both would pass the check above since neither active_group_spawns nor pending_rarity_quiz
    # has been written yet. Serialize on the same per-chat lock the quiz-solve callback already
    # uses, and re-check once inside it, so only the first caller actually spawns anything.
    async with spawn_locks[chat_id]:
        if chat_id in active_group_spawns or chat_id in pending_rarity_quiz: return
        try:
            characters_list = await get_all_characters_cached()
            if not characters_list: return
            eligible_characters = []
            for char in characters_list:
                limit = char.get("spawn_limit", 0)
                # spawn_count now represents number of catches, so compare against limit —
                # this is what makes an individual ULTRA character (or any other capped
                # character) stop spawning once IT specifically hits its own cap; see the
                # ULTRA SCARCITY CAP note near ULTRA_DEFAULT_CATCH_LIMIT above.
                if limit and limit > 0 and char.get("spawn_count", 0) >= limit:
                    continue
                eligible_characters.append(char)
            if not eligible_characters:
                return
            # ⚖️ Configurable via /spawnweight (groups 1-3 / 4-6 / 7-9); falls back to
            # DEFAULT_RARITY_WEIGHTS for any group left unset.
            RARITY_WEIGHTS = get_effective_rarity_weights()

            # 🩹 NEW (owner report — "old /addchar characters keep spawning almost always,
            # new ones barely show up, even with 5000+ players and only 2570 characters
            # total"): weight here is PER CHARACTER, shared evenly by everyone in the same
            # rarity tier — so as the roster grows over months, every new addition to a tier
            # dilutes everyone ELSE already in it too, while a character that's existed since
            # early on has simply had far more spawn cycles to get lucky. Nothing was actually
            # excluding new characters (checked the insert path, the cache invalidation, and
            # the eligibility filter above — all correctly treat old and new characters
            # identically), but a brand new character starts every dice roll at the exact same
            # bare odds as characters that have had months of rolls to accumulate catches,
            # which is exactly the "gets buried immediately" pattern being reported. This gives
            # any character younger than NEW_CHARACTER_BOOST_DAYS a temporary multiplier on
            # top of its normal tier weight, tapering to nothing once it ages out of the
            # window — old characters (no created_at at all, or past the window) are completely
            # unaffected either way.
            now_ts = time.time()
            def _freshness_multiplier(char):
                created_at = char.get("created_at")
                if not created_at:
                    return 1.0
                age_days = (now_ts - created_at) / 86400
                if age_days >= NEW_CHARACTER_BOOST_DAYS or age_days < 0:
                    return 1.0
                return NEW_CHARACTER_BOOST_MULTIPLIER

            # 🛡️ RELIABILITY: a character whose storage media has gone missing (deleted from
            # the control group, corrupted forward, etc.) used to silently swallow the ENTIRE
            # spawn attempt — trigger_dynamic_spawn would just quietly return and the group
            # would have to rack up a whole new spawn_target's worth of messages before getting
            # another chance. Now we retry with a different character (excluding whichever ones
            # just failed) a few times before giving up, so one bad entry can't stall a group.
            candidates = list(eligible_characters)
            max_attempts = min(5, len(candidates))
            for attempt in range(max_attempts):
                weights = [
                    RARITY_WEIGHTS.get(c.get("rarity_tier") or classify_rarity(c.get("rarity", "")), 20) * _freshness_multiplier(c)
                    for c in candidates
                ]
                chosen_char = random.choices(candidates, weights=weights, k=1)[0]
                tier = chosen_char.get("rarity_tier") or classify_rarity(chosen_char.get("rarity", ""))
                if tier in RARITY_GATE_TIERS:
                    # Rarity No.1-4 — don't spawn directly, gate it behind a quiz first.
                    ok = await start_rarity_gate_quiz(chat_id, chosen_char)
                    if not ok:
                        # Quiz couldn't be posted (e.g. no quiz questions configured) — release
                        # directly rather than silently stalling the whole group.
                        ok = await release_spawn(chat_id, chosen_char)
                else:
                    ok = await release_spawn(chat_id, chosen_char)
                if ok:
                    return
                candidates = [c for c in candidates if c["char_id"] != chosen_char["char_id"]]
                if not candidates:
                    break
            print(f"⚠️ Spawn Reliability: exhausted {max_attempts} attempt(s) in chat {chat_id} without a usable character.")
        except Exception as e:
            print(f"Spawn Error Tracker: {e}")

async def release_spawn(chat_id, chosen_char):
    """Actually post the spawn message + open the /who window for chosen_char. Used both for
    normal spawns and for gated (Rarity 1-4) spawns once their quiz gate has been solved.
    Returns True on success, False if it couldn't post (caller may retry with another char)."""
    if chat_id in active_group_spawns: return False
    try:
        # Do NOT increment spawn_count here; it will be incremented on successful catch.
        rarity_display = chosen_char.get("rarity", "Unknown")
        rarity_tier = classify_rarity(rarity_display)
        rarity_emoji = RARITY_EMOJI.get(rarity_tier, RARITY_DEFAULT_EMOJI)
        event_name = chosen_char.get("event", "General")
        event_display = escape_html(event_name) if event_name and event_name != "General" else "—"
        artist_raw = chosen_char.get("artist")
        artist_display = escape_html(str(artist_raw).strip()) if artist_raw and str(artist_raw).strip() else "-"
        # Spelled out step-by-step on purpose — players were missing that /who has to be a
        # REPLY to this exact message, and that there's a second /collect step after that.
        spawn_text = (
            f"{rarity_emoji} 𝘼 𝘾𝙝𝙖𝙧𝙖𝙘𝙩𝙚𝙧 𝙝𝙖𝙨 𝙖𝙥𝙥𝙚𝙖𝙧𝙚𝙙\n"
            f"𝙞𝙣 𝙩𝙝𝙞𝙨 𝙂𝙧𝙤𝙪𝙥 𝘾𝙝𝙖𝙩 𝙩𝙝𝙤..\n"
            f"Artist:{artist_display}\n"
            f"❖ 𝗥𝗘𝗣𝗟𝗬 /w to this message to /obtain [name].🎒\n"
            f"❖ 𝗢𝗯𝘁𝗮𝗶𝗻 𝗔𝗦𝗔𝗣!\n"
        )

        # 🩹 FIX (per owner report — "first person doesn't get it, last person does"): this
        # dict used to only get built and assigned AFTER `await send_with_char_media(...)`
        # returned. But Telegram delivers the spawn message to the group — and eager racers
        # can already be typing /obtain in reaction to it — as soon as OUR send completes on
        # Telegram's end, which isn't necessarily the exact same instant our own `await` call
        # resolves back to this function. Any /obtain that arrived in that window hit
        # catch_handler's very FIRST check ("chat_id not in active_group_spawns") and bounced
        # with "Nothing to collect here!" WITHOUT ever reaching the fair, order-preserving
        # claim lock — while a slightly LATER message that arrived after this dict existed got
        # to compete properly and won. Setting the entry BEFORE issuing the send call closes
        # the window entirely: by the time the message can physically be visible to anyone,
        # this entry is already there. spawn_msg_id is patched in right after send resolves —
        # nobody can possibly have that ID to reply to (for /who) before the message exists in
        # the first place, and /obtain itself never needed it at all.
        active_group_spawns[chat_id] = {
            "spawn_msg_id": None,
            "char_id": chosen_char["char_id"],
            "name": chosen_char["name"],
            "category": chosen_char["category"],
            "value": chosen_char["currency_value"],
            "rarity": rarity_display,
            "event": event_name,
            "artist": chosen_char.get("artist"),
            "spawn_time": time.time(),
            "revealed": False,
            "claimed": False
        }

        async def _post_spawn(media):
            return await bot1.send_message(chat_id, spawn_text, parse_mode='html', file=media)

        spawn_msg = await send_with_char_media(chosen_char["char_id"], chosen_char["storage_msg_id"], _post_spawn)
        if spawn_msg is None:
            print(f"⚠️ Spawn Reliability: storage media missing for {chosen_char.get('char_id')} — skipping.")
            active_group_spawns.pop(chat_id, None)  # roll back the placeholder so a retry can proceed
            return False

        active_group_spawns[chat_id]["spawn_msg_id"] = spawn_msg.id
        return True
    except Exception as e:
        print(f"Spawn Error Tracker: {e}")
        active_group_spawns.pop(chat_id, None)  # never leave a half-set entry behind on error
        return False


# ---- RARITY GATE QUIZ (No.1 / No.2 only) ----
async def _get_quiz_pool():
    """Merge owner-authored quizzes (rarity_quiz_bank_col) with the static fallback bank.
    Custom quiz dicts carry an extra 'question_media_msg_id' key the static ones don't have."""
    custom = await rarity_quiz_bank_col.find({}, {"_id": 0}).to_list(length=None)
    return (custom or []) + RARITY_QUIZ_BANK

async def start_rarity_gate_quiz(chat_id, chosen_char):
    """Post a 4-option quiz for a Rarity 1-4 pick. The character itself is NEVER shown here —
    only the question (optionally with its own illustrative image, unrelated to the character)
    and the answer buttons. Only the first person to tap the correct answer unlocks
    release_spawn(); everyone else gets exactly one attempt, right or wrong."""
    try:
        pool = await _get_quiz_pool()
        q = random.choice(pool)
        shuffled_options = q["options"][:]
        correct_answer_text = shuffled_options[q["correct_index"]]
        random.shuffle(shuffled_options)
        correct_index = shuffled_options.index(correct_answer_text)

        # 🖼️ This is the QUIZ's own optional illustration (set via /addquiz) — never the
        # character's media. The character stays completely hidden until the gate is solved.
        media = None
        question_media_msg_id = q.get("question_media_msg_id")
        if question_media_msg_id:
            media = await get_quiz_question_media(bot1, question_media_msg_id)

        rarity_display = chosen_char.get("rarity", "Unknown")
        quiz_text = (
            f"<b>Choose The Correct Answer🍭</b>\n"
            f"<b>Rarity:</b> {rarity_display}\n\n"
            f"<b>Nobody gets through unless someone proves themselves.</b>\n"
            f"❓ <b>{escape_html(q['question'])}</b>\n\n"
            f"⏱ <b>{RARITY_GATE_TIMEOUT_SECONDS} seconds</b> — first correct tap unlocks the spawn!\n"
            f"🧨 <i>မှားဖြေမိရင် အခွင့်အရေး ထပ်မရနိုင်ပါ.</i>"
        )
        buttons = [[Button.inline(f"{chr(65 + i)}. {opt}", data=f"rgate_{chat_id}_{i}")] for i, opt in enumerate(shuffled_options)]

        if media:
            sent = await bot1.send_message(chat_id, quiz_text, file=media, buttons=buttons, parse_mode='html')
        else:
            sent = await bot1.send_message(chat_id, quiz_text, buttons=buttons, parse_mode='html')

        pending_rarity_quiz[chat_id] = {
            "char": chosen_char,
            "options": shuffled_options,
            "correct_index": correct_index,
            "question": q["question"],
            "msg_id": sent.id,
            "quiz_time": time.time(),
            "solved": False,
            "attempted_users": set(),
        }
        asyncio.create_task(rarity_gate_quiz_timeout_watcher(chat_id, sent.id))
        return True
    except Exception as e:
        print(f"Rarity Gate Quiz Error: {e}")
        return False

@bot1.on(events.CallbackQuery(pattern=r'^rgate_(-?\d+)_(\d)$'))
async def rarity_gate_answer_callback(event):
    chat_id = int(event.pattern_match.group(1))
    chosen_idx = int(event.pattern_match.group(2))
    quiz = pending_rarity_quiz.get(chat_id)
    if not quiz or quiz.get("solved"):
        return await event.answer("⌛ This gate is already closed.", alert=True)
    if time.time() - quiz["quiz_time"] > RARITY_GATE_TIMEOUT_SECONDS:
        return await event.answer("⌛ Time's up already!", alert=True)
    # 🔒 One attempt per person, no retries — check-and-mark with no await between them so
    # a user double-tapping can't sneak in a second try.
    attempted_users = quiz.setdefault("attempted_users", set())
    if event.sender_id in attempted_users:
        return await event.answer("⚠️ You already used your one attempt on this gate.", alert=True)
    attempted_users.add(event.sender_id)
    if chosen_idx != quiz["correct_index"]:
        return await event.answer("❌ Wrong! No second chances on this gate.", alert=True)
    async with spawn_locks[chat_id]:
        quiz = pending_rarity_quiz.get(chat_id)
        if not quiz or quiz.get("solved"):
            return await event.answer("⌛ Someone already solved it!", alert=True)
        quiz["solved"] = True
        winner_id = event.sender_id
    mention = await get_html_mention(event, winner_id)
    # ⭐ Rarity 1-4 gate quizzes reward a small random Star bonus for the correct answer,
    # separate from and in addition to the spawn itself being released. 👑 Premium winners get
    # PREMIUM_QUIZ_REWARD_MULTIPLIER extra on top of the usual roll.
    star_reward = round(random.uniform(QUIZ_STAR_REWARD_MIN, QUIZ_STAR_REWARD_MAX), 2)
    winner_is_premium = await check_premium(winner_id)
    if winner_is_premium:
        star_reward = round(star_reward * PREMIUM_QUIZ_REWARD_MULTIPLIER, 2)
    try:
        await users_catcher_col.update_one({"user_id": winner_id}, {"$inc": {"star_balance": star_reward}}, upsert=True)
    except Exception as e:
        print(f"Quiz star reward error: {e}")
    try:
        await event.edit(
            f"🐇 <b> အဖြေမှန် နှိပ်ခဲ့သူ - {mention}!</b>\n"
            f"✔ <b>Correct answer:</b> {escape_html(quiz['options'][quiz['correct_index']])}\n"
            f"⭐ <b>Bonus:</b> +<code>{star_reward}⭐</code>{' 👑' if winner_is_premium else ''}\n\n"
            f"🎒 <i>✨ Card Spawned Successfully!.</i>",
            parse_mode='html',
            buttons=None
        )
    except Exception:
        pass
    await event.answer("✅ Correct! Releasing the spawn...", alert=True)
    if pending_rarity_quiz.get(chat_id) is quiz:
        del pending_rarity_quiz[chat_id]
    released_ok = await release_spawn(chat_id, quiz["char"])
    if not released_ok:
        try:
            await event.edit(
                f"⚠️ <b>{mention} answered correctly, but that character's media could no longer be found.</b>\n"
                f"<i>No chance was lost — a fresh spawn will come around again soon.</i>",
                parse_mode='html',
                buttons=None
            ) 
        except Exception:
            pass

async def rarity_gate_quiz_timeout_watcher(chat_id, msg_id):
    try:
        await asyncio.sleep(RARITY_GATE_TIMEOUT_SECONDS)
        quiz = pending_rarity_quiz.get(chat_id)
        if not quiz or quiz.get("solved") or quiz.get("msg_id") != msg_id:
            return
        del pending_rarity_quiz[chat_id]
        try:
            await bot1.edit_message(
                chat_id, msg_id,
                f"⏰ <b>Nobody solved it in time. It's been taken back.</b>\n"
                f"✅ <b>The correct answer was:</b> {escape_html(quiz['options'][quiz['correct_index']])}",
                parse_mode='html',
                buttons=None
            )
        except Exception:
            pass
    except Exception as e:
        # 🛡️ RELIABILITY: no matter what goes wrong above, this chat's gate must never stay
        # stuck — a leftover pending_rarity_quiz entry silently blocks every future auto-spawn
        # in this chat (see the guard at the top of global_message_counter_handler).
        print(f"Rarity Gate Timeout Watcher Error: {e}")
        if pending_rarity_quiz.get(chat_id, {}).get("msg_id") == msg_id:
            del pending_rarity_quiz[chat_id]

# ---- TRIVIA AUTO-SPAWN — standalone 4-option "first correct tap wins ⭐ Stars" event. Fully
# independent of the character-spawn / rarity-gate-quiz system above (no character involved at
# all, purely a Star + Trivia-Points payout). See global_message_counter_handler for cadence. ----
async def trivia_leaderboard_button():
    """Two-row button set shown on every trivia result (win or timeout): jump straight to the
    Global Top 50 Trivia Points leaderboard (see the 'trivialb' branch in
    system_callback_router; paginated 10/page, 5 pages total — see render_trivia_leaderboard_page
    / trivia_leaderboard_page_callback below, always opens on page 0/ranks 1-10), or open the
    Limited Editions batch menu (same 'leopen' callback as the button on every /harem view —
    see _build_le_batch_menu). 🩹 NEW (per owner request): the LE row was added here specifically
    — trivia results are one of the bot's highest-traffic surfaces, and LE cards are the ones
    that matter most to keep visible."""
    return [
        [Button.inline("🏆 Global Top 50", data="trivialb")],
        [Button.inline("🎗️ Limited Editions", data="leopen")]
    ]

TRIVIA_LB_PAGE_SIZE = 10
TRIVIA_LB_TOTAL = 50  # 🩹 CHANGED (per owner request): expanded from a Top 20 (2 pages) to a
# Top 50 (5 pages) by cumulative Trivia Points, still 10 per page with ◀ Previous / Next ▶
# inline buttons — same fetch-once-then-slice shape as render_leaderboard_page() above for the
# catch leaderboard.

async def render_trivia_leaderboard_page(page):
    """Builds (text, buttons) for one page of the Global Top 50 Trivia Points leaderboard.
    page is 0-indexed: 0 = ranks 1-10, 1 = ranks 11-20, ... 4 = ranks 41-50. Re-fetches the top
    50 fresh on every call (cheap: one indexed, limited Mongo query) rather than caching, so a
    page tap never shows stale standings from whenever an earlier page first loaded."""
    top_users = await users_catcher_col.find(
        {"trivia_points": {"$gt": 0}}
    ).sort("trivia_points", -1).limit(TRIVIA_LB_TOTAL).to_list(length=TRIVIA_LB_TOTAL)
    total = len(top_users)
    start = page * TRIVIA_LB_PAGE_SIZE
    page_rows = top_users[start:start + TRIVIA_LB_PAGE_SIZE]

    if not page_rows:
        body = "😶 <i>No Trivia ranks yet — be the first to answer one!</i>" if page == 0 else "<i>No more entries.</i>"
    else:
        lines = []
        for i, u in enumerate(page_rows):
            rank = start + i + 1
            uname = escape_html(clean_display_name(u.get("fullname"), fallback=f"Agent {u['user_id']}"))
            mention = f"<a href='tg://user?id={u['user_id']}'>{uname}</a>"
            pts = u.get("trivia_points", 0)
            _, rank_name, _, _ = get_trivia_rank_info(pts)
            lines.append(f"<b>{rank}.</b> {mention} — {rank_name} (<code>{pts} pts</code>)")
        body = "\n".join(lines)
    text = "🏆 <b>GLOBAL TRIVIA LEADERBOARD</b>\n\n" + body

    # 🩹 Same purely-informational "everyone who's ever answered" row as before — see the
    # original comment on global_participants: created the first time anyone answers ANYTHING,
    # win or lose, so this is a broader count than just the 20 shown above.
    global_participants = await users_catcher_col.count_documents({"trivia_points": {"$exists": True}})

    nav_row = []
    if page > 0:
        nav_row.append(Button.inline("◀ Previous", data=f"trivialb_pg_{page - 1}"))
    if start + TRIVIA_LB_PAGE_SIZE < total:
        nav_row.append(Button.inline("Next ▶", data=f"trivialb_pg_{page + 1}"))

    buttons = []
    if nav_row:
        buttons.append(nav_row)
    buttons.append([Button.inline(f"🌍 Globally: {global_participants:,}", data="trivia_global_count_noop")])
    return text, buttons

@bot1.on(events.CallbackQuery(pattern=r'^trivialb_pg_(\d+)$'))
async def trivia_leaderboard_page_callback(event):
    """◀ Previous / Next ▶ taps on the Global Top 50 Trivia leaderboard — same
    edit-in-place-with-graceful-MessageNotModified shape as leaderboard_callback_handler
    (the catch leaderboard's own pager) above."""
    page = int(event.pattern_match.group(1))
    text, buttons = await render_trivia_leaderboard_page(page)
    try:
        await event.edit(text, parse_mode='html', buttons=buttons)
        await event.answer()
    except errors.MessageNotModifiedError:
        await event.answer()
    except Exception as e:
        await event.answer(f"❌ Error: {e}", alert=True)

async def get_trivia_pool(difficulty):
    """Merge owner-bulk-added questions (trivia_bank_col, filtered to this difficulty) with the
    static built-in TRIVIA_QUESTION_BANK[difficulty] fallback list — same "DB questions plus a
    static safety net" shape as _get_quiz_pool() for the rarity-gate quiz above. Custom docs
    come first so a growing owner-authored bank naturally dominates the built-in seed set over
    time without needing to touch the static list. Can legitimately return an empty list for a
    difficulty nobody has bulk-imported into yet — see the fallback search in
    trigger_trivia_spawn, which handles that instead of crashing."""
    custom = await trivia_bank_col.find({"difficulty": difficulty}, {"_id": 0}).to_list(length=None)
    static = TRIVIA_QUESTION_BANK.get(difficulty) or []
    return (custom or []) + static

async def trigger_trivia_spawn(chat_id, forced_difficulty=None, force_golden=False):
    """Posts one TRIVIA_OPTION_COUNT-option trivia question in chat_id, at a randomly weighted
    difficulty tier (see weighted_random_trivia_difficulty), or at forced_difficulty when the
    owner's /ftrivia specifies one for testing. First correct tap wins that tier's ⭐ Star
    reward range plus Trivia Points toward the rank ladder (TRIVIA_RANKS). Everyone else gets
    exactly one attempt, right or wrong — wrong taps cost a few Trivia Points — same "no second
    chances" rule as the rarity-gate quiz above.

    🌟 GOLDEN QUESTION (per owner request): independent GOLDEN_QUESTION_CHANCE roll every spawn
    — see that constant's own comment for the full reasoning."""
    if chat_id in pending_trivia_quiz:
        return
    try:
        difficulty = forced_difficulty if forced_difficulty in TRIVIA_DIFFICULTY_CONFIG else weighted_random_trivia_difficulty()
        cfg = TRIVIA_DIFFICULTY_CONFIG[difficulty]
        bank = await get_trivia_pool(difficulty)
        if not bank:
            # 🛡️ FALLBACK: the chosen tier has no questions yet (owner hasn't bulk-imported
            # that difficulty via /addtriviabulk). Try every other tier before giving up, so a
            # partially-stocked bank still fires trivia instead of a group going quiet just
            # because one specific tier is still empty.
            for fallback_difficulty in TRIVIA_DIFFICULTY_ORDER:
                if fallback_difficulty == difficulty:
                    continue
                fallback_bank = await get_trivia_pool(fallback_difficulty)
                if fallback_bank:
                    difficulty, cfg, bank = fallback_difficulty, TRIVIA_DIFFICULTY_CONFIG[fallback_difficulty], fallback_bank
                    break
        if not bank:
            # Truly nothing anywhere yet (fresh install, nothing bulk-imported at all) — skip
            # this cycle quietly rather than crashing on random.choice([]); tries again once
            # this chat's counter reaches its next random target.
            return
        q = random.choice(bank)
        options = q["options"][:]
        correct_answer_text = options[q["correct_index"]]
        random.shuffle(options)
        correct_index = options.index(correct_answer_text)

        is_golden = force_golden or random.random() < GOLDEN_QUESTION_CHANCE
        star_min = cfg["star_min"] * GOLDEN_QUESTION_MULTIPLIER if is_golden else cfg["star_min"]
        star_max = cfg["star_max"] * GOLDEN_QUESTION_MULTIPLIER if is_golden else cfg["star_max"]
        # Header line is the one thing that visually separates a Golden round from a normal one
        # — same question/options/timer layout either way, so the difficulty badge still reads
        # clearly and nothing about how to PLAY changes, just the stakes and the banner announcing them.
        header = (
            f"{GOLDEN_QUESTION_EMOJI} <b>GOLDEN QUESTION</b> {GOLDEN_QUESTION_EMOJI}  {cfg['emoji']} <b>{cfg['label']}</b>"
            if is_golden else
            f"🧠 <b>Quick Trivia Time!</b>  {cfg['emoji']} <b>{cfg['label']}</b>"
        )

        # 🩹 CHANGED (per owner request): the option TEXT now lives in the message body as a
        # numbered list, and the inline buttons are bare "1"/"2"/"3"/"4" in a single row — not
        # one full-width button per option with the answer text printed on it.
        options_text = "\n".join(f"<b>{i + 1}.</b> {escape_html(opt)}" for i, opt in enumerate(options))
        # 🩹 STAGE 1 (per owner report — "people mash-tap without even reading the question"):
        # question only, no options, no buttons. Nothing tappable exists yet at all, so no
        # amount of reflex-tapping can land a lucky guess in this window.
        stage1_text = (
            f"{header}\n\n"
            f"❓ <b>{escape_html(q['question'])}</b>\n\n"
            + (f"{GOLDEN_QUESTION_EMOJI} <i>Star reward ×{GOLDEN_QUESTION_MULTIPLIER} this round!</i>\n" if is_golden else "")
            + f"👀 <i>Read it first — options revealing in {TRIVIA_OPTIONS_REVEAL_DELAY}s...</i>"
        )
        stage2_text = (
            f"{header}\n\n"
            f"❓ <b>{escape_html(q['question'])}</b>\n\n"
            f"{options_text}\n\n"
            f"⏱ <b>{TRIVIA_TIMEOUT_SECONDS} seconds</b> — first correct tap wins "
            f"⭐ <code>{star_min}-{star_max}</code> Star + <code>{cfg['points']}</code> Rank Points!\n"
            f"🎯 <i>အဖြေမှန်ကို အရင်ဆုံးနှိပ်သူ Star ဆုရမယ်!</i>"
        )
        buttons = [
            [Button.inline(str(i + 1), data=f"triv_{chat_id}_{i}") for i in range(len(options))],
            [Button.inline("🎮 Casino ဆော့ကြမယ်", data="trivslot")],
        ]
        sent = await bot1.send_message(chat_id, stage1_text, parse_mode='html')

        # Set immediately (not after the reveal) so the "one trivia at a time per chat" guard
        # at the top of this function also covers the reveal-delay window itself — otherwise a
        # second trigger_trivia_spawn call landing in that gap could post an overlapping quiz.
        # "revealed": False is what stops trivia_answer_callback (and stray taps in general)
        # from doing anything until stage 2 actually posts real buttons — belt-and-suspenders,
        # since no buttons exist to tap yet anyway.
        quiz_state = {
            "options": options,
            "correct_index": correct_index,
            "question": q["question"],
            "difficulty": difficulty,
            "msg_id": sent.id,
            "quiz_time": time.time(),
            "solved": False,
            "attempted_users": set(),
            "revealed": False,
            "is_golden": is_golden,
            "star_min": star_min,
            "star_max": star_max,
        }
        pending_trivia_quiz[chat_id] = quiz_state

        await asyncio.sleep(TRIVIA_OPTIONS_REVEAL_DELAY)
        if pending_trivia_quiz.get(chat_id) is not quiz_state:
            return  # got force-cleared/replaced during the delay (e.g. owner ran /ftrivia) — bail out quietly

        try:
            await bot1.edit_message(chat_id, sent.id, stage2_text, parse_mode='html', buttons=buttons)
        except errors.MessageNotModifiedError:
            pass
        # Answering-timeout window starts NOW — at reveal — not at the original stage-1 post,
        # so the mandatory reading pause never eats into anyone's real answering time.
        quiz_state["quiz_time"] = time.time()
        quiz_state["revealed"] = True
        asyncio.create_task(trivia_quiz_timeout_watcher(chat_id, sent.id))
    except Exception as e:
        pending_trivia_quiz.pop(chat_id, None)  # never leave a stuck "pending" guard behind on error
        print(f"Trivia Spawn Error: {e}")

@bot1.on(events.CallbackQuery(pattern=r'^triv_(-?\d+)_(\d)$'))
async def trivia_answer_callback(event):
    chat_id = int(event.pattern_match.group(1))
    chosen_idx = int(event.pattern_match.group(2))
    quiz = pending_trivia_quiz.get(chat_id)
    if not quiz or quiz.get("solved"):
        return await event.answer("⌛ ဒီ Trivia မေးခွန်း ပြီးသွားပါပြီ။", alert=True)
    if time.time() - quiz["quiz_time"] > TRIVIA_TIMEOUT_SECONDS:
        return await event.answer("⌛ အချိန်ကုန်သွားပါပြီ။", alert=True)
    # 🔒 One attempt per person, no retries — check-and-mark with no await between them so a
    # user double-tapping can't sneak in a second try. Same pattern as the rarity-gate quiz.
    attempted_users = quiz.setdefault("attempted_users", set())
    if event.sender_id in attempted_users:
        return await event.answer("⚠️ သင့်မှာ ဒီမေးခွန်းအတွက် တစ်ကြိမ်ပဲ ဖြေခွင့်ရှိပါတယ်။", alert=True)
    attempted_users.add(event.sender_id)
    cfg = TRIVIA_DIFFICULTY_CONFIG[quiz.get("difficulty", "EASY")]

    if chosen_idx != quiz["correct_index"]:
        # 🩹 CHANGED (per owner request): a wrong tap now also costs Trivia Points ("ချိန်ဆလျှော့"
        # — weighed down proportional to difficulty), floored at 0 so a rough patch never sends
        # anyone into negative standing.
        # 🩹 NEW (per owner request, 2026-08): anyone currently ON the Global Top 10 pays DOUBLE
        # this penalty — more at stake once you're already on the leaderboard, same "higher
        # risk once you're near the top" logic as the weekly Top 10 Star reward above rewarding
        # staying there. Checked by user_id against the CURRENT Top 10 (not a points threshold),
        # so ties resolve exactly the same way the leaderboard itself displays them.
        # 🩹 NEW (per owner request — Star/VLT inflation fix): a wrong answer now ALSO costs
        # real ⭐ Star (TRIVIA_DIFFICULTY_CONFIG's star_penalty), not just Trivia Points — with
        # trivia this actively played, it doubles as a real currency sink. Never pushes balance
        # negative: pays what the balance covers now, parks the rest as star_debt, auto-swept
        # the next time this user earns Star (see settle_star_debt).
        base_penalty = cfg["wrong_penalty"]
        penalty = base_penalty
        star_penalty = cfg["star_penalty"]
        is_top10 = False
        new_points = None
        star_penalty_note = ""
        try:
            wrong_user = await users_catcher_col.find_one({"user_id": event.sender_id})
            current_points = (wrong_user or {}).get("trivia_points", 0)
            current_star = (wrong_user or {}).get("star_balance", 0)
            top10_ids = await get_current_trivia_top10_ids()
            is_top10 = event.sender_id in top10_ids
            if is_top10:
                penalty = base_penalty * 2
                star_penalty = star_penalty * 2
            new_points = max(0, current_points - penalty)
            paid_now = round(max(0, min(current_star, star_penalty)), 2)
            added_debt = round(star_penalty - paid_now, 2)
            await users_catcher_col.update_one(
                {"user_id": event.sender_id},
                {"$set": {"trivia_points": new_points}, "$inc": {"star_balance": -paid_now, "star_debt": added_debt}},
                upsert=True
            )
            star_penalty_note = (
                f" | ⭐−{format_star_plain(star_penalty)} ({format_star_plain(added_debt)} အကြွေးမှတ်)"
                if added_debt > 0 else f" | ⭐−{format_star_plain(star_penalty)}"
            )
        except Exception as e:
            print(f"Trivia wrong-answer penalty error: {e}")
        points_note = f"(now {new_points})" if new_points is not None else "(couldn't update)"
        return await event.answer(
            f"❌ မှားသွားပါပြီ။ Rank Points −{penalty}{' (Top 10 x2)' if is_top10 else ''} {points_note}"
            f"{star_penalty_note}\n"
            f"နောက်ထပ် အခွင့်အရေး မရတော့ပါ။",
            alert=True
        )

    async with spawn_locks[chat_id]:
        quiz = pending_trivia_quiz.get(chat_id)
        if not quiz or quiz.get("solved"):
            return await event.answer("⌛ တစ်ယောက်ယောက်က အရင်ဖြေပြီးသွားပါပြီ။", alert=True)
        quiz["solved"] = True
        winner_id = event.sender_id
    mention = await get_html_mention(event, winner_id)

    # ⭐ Star reward random within this round's range — quiz["star_min"/"star_max"] is either
    # cfg's plain range, or that range ×GOLDEN_QUESTION_MULTIPLIER if this was a 🌟 Golden
    # Question (see trigger_trivia_spawn). 👑 Premium winners get PREMIUM_QUIZ_REWARD_MULTIPLIER
    # extra on top of THAT (same bonus rule as the rarity-gate quiz) — Trivia Points are
    # deliberately NOT boosted by either bonus, so the rank ladder stays skill/participation
    # based rather than pay-to-win or luck-to-win.
    is_golden = quiz.get("is_golden", False)
    star_reward = random.randint(quiz.get("star_min", cfg["star_min"]), quiz.get("star_max", cfg["star_max"]))
    points_earned = cfg["points"]
    winner_is_premium = await check_premium(winner_id)
    if winner_is_premium:
        star_reward = round(star_reward * PREMIUM_QUIZ_REWARD_MULTIPLIER)

    points_before = 0
    points_after = points_earned
    try:
        winner_doc_before = await users_catcher_col.find_one({"user_id": winner_id})
        points_before = (winner_doc_before or {}).get("trivia_points", 0)
        points_after = points_before + points_earned
        await users_catcher_col.update_one(
            {"user_id": winner_id},
            {"$inc": {"star_balance": star_reward, "trivia_points": points_earned}},
            upsert=True
        )
        await settle_star_debt(winner_id)
    except Exception as e:
        print(f"Trivia star/points reward error: {e}")

    rank_before_idx, _rb_name, _rb_floor, _rb_next = get_trivia_rank_info(points_before)
    rank_after_idx, rank_after_name, floor_after, next_after = get_trivia_rank_info(points_after)
    bar = build_trivia_progress_bar(points_after, floor_after, next_after)
    progress_line = (
        f"{bar}  <code>{points_after} pts (MAX 🏆)</code>" if next_after is None
        else f"{bar}  <code>{points_after}/{next_after} pts</code>"
    )
    rank_up_line = f"🎉 <b>RANK UP!</b> Now {rank_after_name}!\n" if rank_after_idx > rank_before_idx else ""
    trivia_position, trivia_total = await get_trivia_position(points_after)

    try:
        await event.edit(
            f"<b>အဖြေမှန်ကို အရင်ဆုံးနှိပ်ခဲ့သူ – {mention}!</b>\n"
            f"✔ <b>Correct answer:</b> {escape_html(quiz['options'][quiz['correct_index']])}\n"
            f"⭐ <b>Reward:</b> +<code>{star_reward}⭐</code>{' 👑' if winner_is_premium else ''}\n\n"
            f"{rank_up_line}"
            f"🏅 <b>Rank {f(str(rank_after_idx))}/{f(str(len(TRIVIA_RANKS)))}:</b> {rank_after_name}\n"
            f"{progress_line}\n"
            f"🧠 <b>Position:</b> <code>#{trivia_position} / {trivia_total}</code>",
            parse_mode='html',
            buttons=await trivia_leaderboard_button()
        )
    except Exception:
        pass

    # 🏆 NEW (per owner request — "make the winner announcement more public/exciting"): the
    # edit above is easy to miss on a message people aren't actively looking at, and an EDIT
    # never actually pings the mentioned winner either way — Telegram only notifies on new
    # messages, never edits. This is a separate, deliberately short follow-up so it (a) really
    # does ping the winner and (b) reads as one quick celebratory beat in the chat rather than
    # a second wall of stats — the full rank/progress card stays in the edit above for anyone
    # who wants to look closer. 🌟 Golden Question wins get their own bigger, gold-themed
    # version, same GOLDEN_QUESTION_EMOJI used consistently through the whole round.
    if is_golden:
        announce = (
            f"{GOLDEN_QUESTION_EMOJI} <b>GOLDEN QUESTION WON!</b> {GOLDEN_QUESTION_EMOJI}\n"
            f"🏆 {mention} {small_caps('walked away with')} <code>+{star_reward}⭐</code>!"
        )
    else:
        announce = (
            f"🏆 {mention} {small_caps('swept the trivia')}!\n"
            f"⭐ +{star_reward} · 🧠 +{points_earned} pts"
        )
    if rank_after_idx > rank_before_idx:
        announce += f"\n🎉 {small_caps('rank up')} — now {rank_after_name}!"
    try:
        await bot1.send_message(chat_id, announce, parse_mode='html')
    except Exception:
        pass

    await event.answer(f"✅ မှန်ပါတယ်! +{star_reward}⭐  +{points_earned} pts ရရှိပါပြီ!", alert=True)
    if pending_trivia_quiz.get(chat_id) is quiz:
        del pending_trivia_quiz[chat_id]

# ==========================================
async def _send_casino_menu(event, user_id):
    """Shared by /casino and trivia's quick-access button — builds and sends the game-picker
    menu (welcome bonus check included) as a NEW message. Never edits an existing message
    itself (the trivia question message must stay intact) — /casino's own handler is the one
    that starts the edit-in-place chain from here, via casino_menu_pick_cb."""
    # 🎉 One-time welcome bonus — atomic claim (find_one_and_update), same pattern as the
    # Premium daily Star gift's date-gated claim elsewhere in this file, just with a boolean
    # flag instead of a date since this only ever fires once per account, not once per day.
    claimed = await users_catcher_col.find_one_and_update(
        {"user_id": user_id, "casino_welcome_claimed": {"$ne": True}},
        {"$set": {"casino_welcome_claimed": True}}
    )
    bonus_line = ""
    if claimed:
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"star_balance": CASINO_WELCOME_BONUS}})
        bonus_line = f"🎉 {bold_italic_serif('Welcome to the casino!')} +{format_star_plain(CASINO_WELCOME_BONUS)} {bold_italic_serif('is on the house — good luck!')}\n\n"

    text = (
        f"{bonus_line}"
        f"{bold_italic_serif('Wanna grow your')} ⭐ {bold_italic_serif('Stars by playing games?')}\n"
        f"{bold_italic_serif('Try these games below!')} 🎮"
    )
    banner = await _casino_banner_media()
    send_kwargs = {"message": text, "parse_mode": 'html', "buttons": _casino_menu_buttons(user_id)}
    if banner:
        send_kwargs["file"] = banner
    return await _casino_out(event, **send_kwargs)

# ==========================================
# 🎮 Trivia's quick "Casino ဆော့ကြမယ်" button (per owner request — renamed from "🎰 Slot
# ဆော့မယ်" now that it opens the full game picker, not just slot specifically) — sends the
# same /casino menu as a new message right from under a trivia question, without leaving the
# group. Real money, same _casino_play economy as /casino or typing /slot etc. directly — just
# a faster on-ramp to it, not a separate game or currency.
# ==========================================
@bot1.on(events.CallbackQuery(pattern=r'^trivslot$'))
async def trivia_casino_button_cb(event):
    await event.answer("🎮")
    await _send_casino_menu(event, event.sender_id)

async def trivia_quiz_timeout_watcher(chat_id, msg_id):
    try:
        await asyncio.sleep(TRIVIA_TIMEOUT_SECONDS)
        quiz = pending_trivia_quiz.get(chat_id)
        if not quiz or quiz.get("solved") or quiz.get("msg_id") != msg_id:
            return
        del pending_trivia_quiz[chat_id]
        try:
            await bot1.edit_message(
                chat_id, msg_id,
                f"⏰ <b>ဘယ်သူမှ အချိန်မီ မဖြေနိုင်ခဲ့ပါဘူး။</b>\n"
                f"✅ <b>အဖြေမှန်က:</b> {escape_html(quiz['options'][quiz['correct_index']])}",
                parse_mode='html',
                buttons=await trivia_leaderboard_button()
            )
        except Exception:
            pass
    except Exception as e:
        # 🛡️ RELIABILITY: same reasoning as rarity_gate_quiz_timeout_watcher — a leftover
        # pending_trivia_quiz entry would permanently block this chat's future trivia spawns
        # (see the guard at the top of global_message_counter_handler).
        print(f"Trivia Timeout Watcher Error: {e}")
        if pending_trivia_quiz.get(chat_id, {}).get("msg_id") == msg_id:
            del pending_trivia_quiz[chat_id]

# ---- /triviaspawn: owner-only on/off toggle for the trivia auto-spawn feature. ----
def trivia_difficulty_list_text():
    """Compact 'EMOJI Label min-max⭐ / ...' string built straight from TRIVIA_DIFFICULTY_CONFIG,
    Easy-to-Extreme order — used in /triviaspawn's status text so it can't drift out of sync
    with the real per-tier rewards again (same reasoning as rarity_price_list_text())."""
    return " / ".join(
        f"{TRIVIA_DIFFICULTY_CONFIG[d]['emoji']}{TRIVIA_DIFFICULTY_CONFIG[d]['label']} "
        f"{TRIVIA_DIFFICULTY_CONFIG[d]['star_min']}-{TRIVIA_DIFFICULTY_CONFIG[d]['star_max']}⭐"
        for d in TRIVIA_DIFFICULTY_ORDER
    )

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]triviaspawn(?:@\w+)?(?:\s+(\S+))?$', 'bot1')))
async def trivia_spawn_toggle_handler(event):
    if event.sender_id != OWNER_ID: return
    global _cached_trivia_enabled
    arg = (event.pattern_match.group(1) or "").strip().lower()
    if not arg:
        status = "✅ ON" if _cached_trivia_enabled else "❌ OFF"
        return await event.reply(
            f"🧠 <b>Trivia Auto-Spawn:</b> {status}\n"
            f"<i>Every group gets a random {TRIVIA_OPTION_COUNT}-option question roughly every "
            f"{TRIVIA_SPAWN_MIN_MSGS}-{TRIVIA_SPAWN_MAX_MSGS} messages, at a random difficulty "
            f"(Normal/Hard come up most often, Easy deliberately rare):</i>\n"
            f"<code>{trivia_difficulty_list_text()}</code>\n"
            f"<i>Correct = ⭐ Star + Rank Points toward TRIVIA_RANKS. Wrong = a few Rank Points "
            f"deducted, scaled by difficulty.</i>\n\n"
            f"<b>Usage:</b> <code>/triviaspawn on</code> / <code>/triviaspawn off</code>\n"
            f"<b>Testing:</b> <code>/ftrivia [difficulty] [golden]</code> — e.g. <code>/ftrivia hard golden</code>",
            parse_mode='html'
        )
    if arg not in ("on", "off"):
        return await event.reply("❌ <code>/triviaspawn on</code> ဒါမှမဟုတ် <code>/triviaspawn off</code> လို့ပဲ ရိုက်ပါ။", parse_mode='html')
    _cached_trivia_enabled = (arg == "on")
    try:
        await bot_settings_col.update_one(
            {"_id": "trivia_spawn_enabled"},
            {"$set": {"enabled": _cached_trivia_enabled}},
            upsert=True
        )
    except Exception as e:
        print(f"triviaspawn toggle persist error: {e}")
    await event.reply(f"✅ <b>Trivia Auto-Spawn is now {'ON ✅' if _cached_trivia_enabled else 'OFF ❌'}</b>", parse_mode='html')

# ---- /ftrivia: owner-only, force-post a trivia question in the current chat right now (for
# testing — does not consume or reset that chat's normal auto-spawn counter/target). Optional
# difficulty argument (EASY/NORMAL/HARD/VERY_HARD/EXTREME_HARD, case/space/dash-insensitive)
# forces that exact tier instead of the usual weighted-random pick — handy for QA-ing each
# tier's questions and reward range one at a time. Optional second argument "golden" force-rolls
# a 🌟 Golden Question on top, regardless of GOLDEN_QUESTION_CHANCE — e.g. /ftrivia hard golden. ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]ftrivia(?:@\w+)?(?:\s+(\S+))?(?:\s+(\S+))?$', 'bot1')))
async def force_trivia_by_owner(event):
    if event.sender_id != OWNER_ID: return
    if event.chat_id in pending_trivia_quiz:
        return await event.reply("⚠️ ဒီချက်မှာ Trivia တစ်ခုကို လက်ရှိမေးနေဆဲပါ — အရင်တစ်ခု ပြီးမှ ထပ်ခေါ်ပါ။", parse_mode='html')
    raw_args = [a for a in (event.pattern_match.group(1), event.pattern_match.group(2)) if a]
    force_golden = any(a.strip().lower() == "golden" for a in raw_args)
    difficulty_args = [a for a in raw_args if a.strip().lower() != "golden"]
    raw_arg = difficulty_args[0] if difficulty_args else ""
    forced = normalize_trivia_difficulty_arg(raw_arg) if raw_arg else None
    if raw_arg and not forced:
        return await event.reply(
            f"❌ Unknown difficulty. Usage: <code>/ftrivia [{'|'.join(TRIVIA_DIFFICULTY_ORDER)}] [golden]</code>",
            parse_mode='html'
        )
    await trigger_trivia_spawn(event.chat_id, forced_difficulty=forced, force_golden=force_golden)

# ---- /triviarank: anyone can check their own current Trivia rank + progress bar, any time —
# not just right after winning a question (which already shows it on the reveal message). ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]triviarank(?:@\w+)?$', 'bot1')))
async def trivia_rank_handler(event):
    user_id = event.sender_id
    mention = await get_html_mention(event, user_id)
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    points = (user_doc or {}).get("trivia_points", 0)
    rank_idx, rank_name, floor_points, next_points = get_trivia_rank_info(points)
    bar = build_trivia_progress_bar(points, floor_points, next_points)
    progress_line = (
        f"{bar}  <code>{points} pts (MAX 🏆)</code>" if next_points is None
        else f"{bar}  <code>{points}/{next_points} pts</code>"
    )
    trivia_position, trivia_total = await get_trivia_position(points)
    await event.reply(
        f"🧠 <b>{mention}'s Trivia Rank</b>\n\n"
        f"🏅 <b>Rank {f(str(rank_idx))}/{f(str(len(TRIVIA_RANKS)))}:</b> {rank_name}\n"
        f"{progress_line}\n"
        f"🌍 <b>Position:</b> <code>#{trivia_position} / {trivia_total}</code>",
        parse_mode='html',
        buttons=await trivia_leaderboard_button()
    )

def parse_bulk_trivia_text(raw_text):
    """Parses pipe-delimited bulk trivia text, ONE question per non-empty line:
    'Question text | Option 1 | Option 2 | Option 3 | Option 4 | 1' — the last field says which
    option is correct: 1-4 or A-D (case-insensitive). Field count is driven by
    TRIVIA_OPTION_COUNT (question + that many options + the correct-answer field), so this
    parser automatically matches whatever option count trivia is currently using — no hardcoded
    '4 parts' assumption. Blank lines and lines starting with '#' are skipped (so a pasted batch
    can use blank separators or comment lines). Returns (valid_docs, errors), where errors is a
    list of (line_number, raw_line, reason) so the owner can fix just those lines and resubmit
    the whole file safely (see the dedup logic in add_trivia_bulk_handler — already-successful
    lines won't be re-inserted)."""
    expected_fields = 1 + TRIVIA_OPTION_COUNT + 1  # question + N options + correct marker
    letter_map = {chr(ord('A') + i): i for i in range(TRIVIA_OPTION_COUNT)}
    valid = []
    errors = []
    for line_no, raw_line in enumerate(raw_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != expected_fields:
            errors.append((
                line_no, raw_line,
                f"expected {expected_fields} parts separated by '|' (question + "
                f"{TRIVIA_OPTION_COUNT} options + correct answer), got {len(parts)}"
            ))
            continue
        question = parts[0]
        options = parts[1:1 + TRIVIA_OPTION_COUNT]
        correct_raw = parts[-1]
        if not question or any(not opt for opt in options):
            errors.append((line_no, raw_line, "question/option text can't be empty"))
            continue
        correct_norm = correct_raw.strip().upper()
        correct_index = None
        if correct_norm.isdigit() and 1 <= int(correct_norm) <= TRIVIA_OPTION_COUNT:
            correct_index = int(correct_norm) - 1
        elif correct_norm in letter_map:
            correct_index = letter_map[correct_norm]
        if correct_index is None:
            digits = "/".join(str(i + 1) for i in range(TRIVIA_OPTION_COUNT))
            letters = "/".join(chr(ord('A') + i) for i in range(TRIVIA_OPTION_COUNT))
            errors.append((line_no, raw_line, f"last field must be {digits} or {letters}, got '{correct_raw}'"))
            continue
        valid.append({"question": question, "options": options, "correct_index": correct_index})
    return valid, errors

# ---- /addtriviabulk: owner-only, BULK authoring of Trivia questions for ONE difficulty tier at
# a time — unlike /addquiz's one-at-a-time flow, this accepts HUNDREDS of questions in a single
# paste (or a .txt file upload for batches too big for one Telegram message). DM-only, same
# privacy reasoning as /addquiz — authored where nobody in the group sees it happening. ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]addtriviabulk(?:@\w+)?(?:\s+(\S+))?$', 'bot1')))
async def add_trivia_bulk_handler(event):
    if event.sender_id != OWNER_ID: return
    if not event.is_private:
        return await event.reply(
            "⚠️ <b>Please DM me /addtriviabulk</b> — same reasoning as /addquiz, authored privately.",
            parse_mode='html'
        )
    difficulty = normalize_trivia_difficulty_arg(event.pattern_match.group(1) or "")
    if not difficulty:
        return await event.reply(
            f"📦 <b>Bulk-add Trivia questions</b>\n\n"
            f"<b>Usage:</b> <code>/addtriviabulk [difficulty]</code>\n"
            f"<b>Difficulty:</b> {' / '.join(TRIVIA_DIFFICULTY_ORDER)}\n\n"
            f"Example: <code>/addtriviabulk hard</code>",
            parse_mode='html'
        )
    cfg = TRIVIA_DIFFICULTY_CONFIG[difficulty]
    try:
        async with bot1.conversation(event.chat_id, timeout=600) as conv:
            await conv.send_message(
                f"📦 <b>Bulk Add Trivia — {cfg['emoji']} {cfg['label']}</b>\n\n"
                f"Send me ALL your questions now, <b>one per line</b>, in this exact format:\n"
                f"<code>Question | Option 1 | Option 2 | Option 3 | Option 4 | 1</code>\n\n"
                f"The last field says which option is correct — <code>1</code>-<code>4</code> "
                f"or <code>A</code>-<code>D</code> (whichever's easier to type).\n\n"
                f"<b>Example:</b>\n"
                f"<code>ဂျပန်နိုင်ငံရဲ့ မြို့တော်က ဘယ်မြို့လဲ? | တိုကျို | အိုစာကာ | ကျိုတို | နာဂိုယာ | 1</code>\n\n"
                f"📎 For a LOT of questions, upload a <code>.txt</code> file (one question per "
                f"line, same format) instead of pasting — no length limit that way.\n\n"
                f"Send /cancel anytime to stop.",
                parse_mode='html'
            )
            resp = await conv.get_response()
            if (resp.raw_text or "").strip().lower() == "/cancel":
                return await conv.send_message("❌ Cancelled.")

            if resp.document:
                try:
                    file_bytes = await bot1.download_media(resp, file=bytes)
                    raw_text = file_bytes.decode('utf-8', errors='replace')
                except Exception as e:
                    return await conv.send_message(f"❌ Couldn't read that file: <code>{escape_html(str(e))}</code>", parse_mode='html')
            else:
                raw_text = resp.raw_text or ""

            if not raw_text.strip():
                return await conv.send_message("❌ That was empty. Cancelled — run /addtriviabulk again.")

            valid, parse_errors = parse_bulk_trivia_text(raw_text)

            # 🧹 Dedup — both within this batch AND against what's already stored for this
            # difficulty, so it's always safe to fix a few bad lines and resubmit the WHOLE
            # file without doubling up the lines that already succeeded the first time.
            existing_questions = set()
            try:
                existing_docs = await trivia_bank_col.find(
                    {"difficulty": difficulty}, {"_id": 0, "question": 1}
                ).to_list(length=None)
                existing_questions = {d["question"].strip().lower() for d in existing_docs}
            except Exception as e:
                print(f"addtriviabulk existing-dedup lookup error: {e}")

            to_insert = []
            seen_in_batch = set()
            skipped_dupes = 0
            for doc in valid:
                key = doc["question"].strip().lower()
                if key in existing_questions or key in seen_in_batch:
                    skipped_dupes += 1
                    continue
                seen_in_batch.add(key)
                doc["difficulty"] = difficulty
                doc["created_by"] = event.sender_id
                doc["created_at"] = time.time()
                to_insert.append(doc)

            inserted_count = 0
            if to_insert:
                try:
                    result = await trivia_bank_col.insert_many(to_insert)
                    inserted_count = len(result.inserted_ids)
                except Exception as e:
                    return await conv.send_message(f"❌ Insert failed: <code>{escape_html(str(e))}</code>", parse_mode='html')

            summary = f"✅ <b>Added {inserted_count} question(s) to {cfg['emoji']} {cfg['label']}.</b>\n"
            if skipped_dupes:
                summary += f"⏭️ Skipped {skipped_dupes} duplicate question(s) (already in this tier).\n"
            if parse_errors:
                shown = parse_errors[:15]
                err_lines = "\n".join(f"  Line {ln}: {reason}" for ln, _raw, reason in shown)
                more = f"\n  ...and {len(parse_errors) - 15} more" if len(parse_errors) > 15 else ""
                summary += f"❌ <b>{len(parse_errors)} line(s) had problems:</b>\n<code>{escape_html(err_lines)}{more}</code>\n"
            summary += f"\nUse <code>/listtriviabank {difficulty}</code> to review, or run /addtriviabulk again to add more."
            await conv.send_message(summary, parse_mode='html')
    except asyncio.TimeoutError:
        await event.reply("⌛ <b>Timed out waiting for your bulk paste.</b> Run /addtriviabulk again.", parse_mode='html')
    except Exception as e:
        print(f"add_trivia_bulk_handler error: {e}")
        await event.reply(f"❌ <b>Something went wrong:</b> <code>{escape_html(str(e))}</code>", parse_mode='html')

# ---- /listtriviabank: owner-only. No args = quick counts across all 5 tiers. With a difficulty
# arg = that tier's custom (DB) questions, newest-last, capped to 30 shown so a big bank never
# blows past Telegram's message-length limit. ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]listtriviabank(?:@\w+)?(?:\s+(\S+))?$', 'bot1')))
async def list_trivia_bank_handler(event):
    if event.sender_id != OWNER_ID: return
    raw_arg = event.pattern_match.group(1) or ""
    if not raw_arg:
        lines = []
        for d in TRIVIA_DIFFICULTY_ORDER:
            dcfg = TRIVIA_DIFFICULTY_CONFIG[d]
            custom_count = await trivia_bank_col.count_documents({"difficulty": d})
            static_count = len(TRIVIA_QUESTION_BANK.get(d) or [])
            lines.append(
                f"{dcfg['emoji']} <b>{dcfg['label']}:</b> {custom_count} custom + {static_count} "
                f"built-in = <code>{custom_count + static_count}</code>"
            )
        return await event.reply(
            "🧠 <b>Trivia Question Bank — Overview</b>\n\n" + "\n".join(lines) +
            f"\n\nUse <code>/listtriviabank [difficulty]</code> to see a tier's custom questions.",
            parse_mode='html'
        )
    difficulty = normalize_trivia_difficulty_arg(raw_arg)
    if not difficulty:
        return await event.reply(
            f"❌ Unknown difficulty. Usage: <code>/listtriviabank [{'|'.join(TRIVIA_DIFFICULTY_ORDER)}]</code>",
            parse_mode='html'
        )
    cfg = TRIVIA_DIFFICULTY_CONFIG[difficulty]
    custom = await trivia_bank_col.find(
        {"difficulty": difficulty}, {"question": 1}
    ).sort("created_at", 1).to_list(length=None)
    static_count = len(TRIVIA_QUESTION_BANK.get(difficulty) or [])
    shown = custom[:30]
    lines = [f"<code>{i + 1}.</code> {escape_html(q['question'][:70])}" for i, q in enumerate(shown)]
    more = f"\n<i>...and {len(custom) - 30} more.</i>" if len(custom) > 30 else ""
    await event.reply(
        f"🧠 <b>Custom {cfg['emoji']} {cfg['label']} Trivia ({len(custom)})</b>\n" +
        ("\n".join(lines) if lines else "<i>None yet — use /addtriviabulk.</i>") + more +
        f"\n\n<i>Plus {static_count} built-in fallback questions, always in rotation too.</i>\n"
        f"Use <code>/deltriviabank {difficulty} &lt;number&gt;</code> to remove one.",
        parse_mode='html'
    )

# ---- /deltriviabank [difficulty] [number]: owner-only, removes one custom Trivia question by
# its position in /listtriviabank's numbering (oldest-added = #1). ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]deltriviabank(?:@\w+)?\s+(\S+)\s+(\S+)(?:\s+(\S+))?$', 'bot1')))
async def del_trivia_bank_handler(event):
    if event.sender_id != OWNER_ID: return
    difficulty = normalize_trivia_difficulty_arg(event.pattern_match.group(1))
    if not difficulty:
        return await event.reply(
            f"❌ Unknown difficulty. Usage:\n"
            f"<code>/deltriviabank [{'|'.join(TRIVIA_DIFFICULTY_ORDER)}] &lt;number&gt;</code> — delete one question\n"
            f"<code>/deltriviabank [{'|'.join(TRIVIA_DIFFICULTY_ORDER)}] all confirm</code> — wipe the whole tier",
            parse_mode='html'
        )
    second_arg = event.pattern_match.group(2)
    # 🩹 NEW (per owner request — clearing out an entire tier at once, e.g. "these questions
    # are too hard, delete them all"): bulk-wipe path, gated behind an explicit "confirm" so a
    # typo can't nuke a whole tier by accident.
    if second_arg.lower() == "all":
        third_arg = (event.pattern_match.group(3) or "").lower()
        cfg = TRIVIA_DIFFICULTY_CONFIG[difficulty]
        count = await trivia_bank_col.count_documents({"difficulty": difficulty})
        if third_arg != "confirm":
            return await event.reply(
                f"⚠️ <b>This deletes ALL {count} question(s) in {cfg['emoji']} {cfg['label']}.</b> "
                f"This can't be undone.\n"
                f"Run <code>/deltriviabank {difficulty.lower()} all confirm</code> to go through with it.",
                parse_mode='html'
            )
        result = await trivia_bank_col.delete_many({"difficulty": difficulty})
        return await event.reply(
            f"🗑️ <b>Wiped {cfg['emoji']} {cfg['label']}:</b> <code>{result.deleted_count}</code> question(s) removed.\n"
            f"This tier won't spawn again until you <code>/addtriviabulk {difficulty.lower()}</code> new ones.",
            parse_mode='html'
        )

    if not second_arg.isdigit():
        return await event.reply(
            f"❌ Expected a question number, or the word <code>all</code> to wipe the whole tier.",
            parse_mode='html'
        )
    position = int(second_arg)
    custom = await trivia_bank_col.find(
        {"difficulty": difficulty}, {"question": 1}
    ).sort("created_at", 1).to_list(length=None)
    if position < 1 or position > len(custom):
        return await event.reply(f"❌ No question #{position} in {difficulty} (there are {len(custom)}).", parse_mode='html')
    target = custom[position - 1]
    await trivia_bank_col.delete_one({"_id": target["_id"]})
    await event.reply(f"🗑️ <b>Removed:</b> {escape_html(target['question'][:80])}", parse_mode='html')

# ---- /addquiz: owner-only, step-by-step authoring of a Rarity 1-4 gate question. DM-only so
# nobody sees it being built. Question may be plain text OR a photo/video with the question as
# caption — that image is the QUIZ's own illustration, never the character being gated. ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]addquiz(?:@\w+)?$', 'bot1')))
async def add_quiz_handler(event):
    if event.sender_id != OWNER_ID: return
    if not event.is_private:
        return await event.reply(
            "⚠️ <b>Please DM me /addquiz</b> — quizzes are authored privately so nobody in the group sees it being built.",
            parse_mode='html'
        )
    option_labels = ["A", "B", "C", "D"]
    try:
        async with bot1.conversation(event.chat_id, timeout=300) as conv:
            await conv.send_message(
                "🔐 <b>New Rarity-Gate Quiz</b>\n\n"
                "Send me the <b>question</b> — as plain text, OR as a photo/video with the "
                "question typed as the caption. (That image is just an illustration for the "
                "question itself — it is never the character being gated.)\n\n"
                "Send /cancel anytime to stop.",
                parse_mode='html'
            )
            q_msg = await conv.get_response()
            if (q_msg.raw_text or "").strip().lower() == "/cancel":
                return await conv.send_message("❌ Cancelled.")
            question_text = (q_msg.raw_text or "").strip()
            if not question_text:
                return await conv.send_message("❌ I need question text (typed, or as a caption). Cancelled — run /addquiz again.")
            question_media_msg_id = None
            if q_msg.photo or q_msg.video:
                fwd = await send_safe_message(bot1, SPECIFIC_CONTROL_GROUP, "", file=q_msg.media)
                question_media_msg_id = fwd.id

            options = []
            for label in option_labels:
                await conv.send_message(f"✏️ Send <b>Option {label}</b>:", parse_mode='html')
                opt_msg = await conv.get_response()
                opt_text = (opt_msg.raw_text or "").strip()
                if opt_text.lower() == "/cancel":
                    return await conv.send_message("❌ Cancelled.")
                if not opt_text:
                    return await conv.send_message("❌ Empty option. Cancelled — run /addquiz again.")
                options.append(opt_text)

            await conv.send_message("✅ Which option is <b>correct</b>? Reply with A, B, C, or D.", parse_mode='html')
            correct_msg = await conv.get_response()
            correct_letter = (correct_msg.raw_text or "").strip().upper()
            if correct_letter == "/CANCEL":
                return await conv.send_message("❌ Cancelled.")
            if correct_letter not in option_labels:
                return await conv.send_message("❌ That's not A/B/C/D. Cancelled — run /addquiz again.")
            correct_index = option_labels.index(correct_letter)

            quiz_doc = {
                "question": question_text,
                "options": options,
                "correct_index": correct_index,
                "question_media_msg_id": question_media_msg_id,
                "created_by": event.sender_id,
                "created_at": time.time(),
            }
            await rarity_quiz_bank_col.insert_one(quiz_doc)

            preview = f"❓ <b>{escape_html(question_text)}</b>\n\n" + "\n".join(
                f"{lbl}. {escape_html(opt)}" + (" ✅" if i == correct_index else "")
                for i, (lbl, opt) in enumerate(zip(option_labels, options))
            )
            if question_media_msg_id:
                try:
                    fwd_msg = await bot1.get_messages(SPECIFIC_CONTROL_GROUP, ids=question_media_msg_id)
                    await conv.send_message(preview, file=fwd_msg.media, parse_mode='html')
                except Exception:
                    await conv.send_message(preview, parse_mode='html')
            else:
                await conv.send_message(preview, parse_mode='html')
            await conv.send_message("✅ <b>Saved!</b> This will now show up in the Rarity 1-4 gate rotation.", parse_mode='html')
    except asyncio.TimeoutError:
        await event.reply("⌛ <b>Timed out waiting for your answer.</b> Run /addquiz again.", parse_mode='html')
    except Exception as e:
        print(f"add_quiz_handler error: {e}")
        await event.reply(f"❌ <b>Something went wrong:</b> <code>{escape_html(str(e))}</code>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]listquiz(?:@\w+)?$', 'bot1')))
async def list_quiz_handler(event):
    if event.sender_id != OWNER_ID: return
    custom = await rarity_quiz_bank_col.find({}, {"_id": 0, "question": 1}).to_list(length=None)
    lines = [f"<code>{i + 1}.</code> {escape_html(q['question'][:60])}" for i, q in enumerate(custom)]
    await event.reply(
        f"🔐 <b>Custom Gate Quizzes ({len(custom)})</b>\n" + ("\n".join(lines) if lines else "<i>None yet — use /addquiz.</i>")
        + f"\n\n<i>Plus {len(RARITY_QUIZ_BANK)} built-in fallback questions, always in rotation too.</i>\n"
        f"Use <code>/delquiz &lt;number&gt;</code> to remove one.",
        parse_mode='html'
    )

# ==========================================
# 🔎 IDENTIFY VIA /who ON A SAVED/REPOSTED PHOTO OR VIDEO — if a character's original media
# (added through /addchar) was saved to someone's gallery and later sent back — as a photo/
# video WITH "/who" as the caption, or as a plain photo/video that then gets replied to with
# /who — recognizes it via perceptual hash and shows name/id/rarity + a /collect hint.
# Works in groups AND in the bot's DM. INFO ONLY: never touches active_group_spawns, so it
# never makes anything catchable by itself — /collect still only works against a real live spawn.
# ==========================================
IDENTIFY_HAMMING_THRESHOLD = 40  # lower = stricter match. 0-256 possible now that PHASH_SIZE=16.
IDENTIFY_COOLDOWN_SECONDS = 1  # per-user, so repeated attempts don't spam replies

async def find_character_by_media(media_msg):
    """Core perceptual-hash character lookup — given a Telethon message with a photo/video,
    returns the best-matching character doc (or None). Shared by _identify_media_and_reply
    (the /who-style public lookup) and add_artist_handler's reply-based shortcut."""
    try:
        incoming_hash = await compute_phash_for_message(media_msg)
    except Exception as e:
        print(f"find_character_by_media hash error: {e}")
        return None
    if not incoming_hash:
        return None
    characters_list = await get_all_characters_cached()
    best_match, best_distance = None, IDENTIFY_HAMMING_THRESHOLD + 1
    for char in characters_list:
        char_hash = char.get("photo_phash")
        if not char_hash:
            continue
        dist = hamming_distance(incoming_hash, char_hash)
        if dist < best_distance:
            best_distance, best_match = dist, char
    return best_match

async def _identify_media_and_reply(event, media_msg):
    user_id = event.sender_id
    is_cd, _ = await is_on_cooldown(user_id, "identify_repost", IDENTIFY_COOLDOWN_SECONDS)
    if is_cd:
        return
    best_match = await find_character_by_media(media_msg)
    if not best_match:
        return await event.reply("❓ <b>This one isn't recognized.</b>", parse_mode='html')
    # 🔐 If this exact character is currently sitting behind an unsolved rarity-gate quiz
    # in ANY chat, don't confirm its identity — that would leak Rarity 1-4 characters before
    # the gate is actually cleared and the spawn is released.
    for quiz in pending_rarity_quiz.values():
        if not quiz.get("solved") and quiz.get("char", {}).get("char_id") == best_match["char_id"]:
            return await event.reply(
                "🔐 <b>Can't confirm this one yet — it's still sealed behind a gate somewhere.</b>",
                parse_mode='html'
            )
    try:
        rarity_tier = classify_rarity(best_match.get("rarity", ""))
        rarity_emoji = RARITY_EMOJI.get(rarity_tier, RARITY_DEFAULT_EMOJI)
        event_display = escape_html(best_match.get("event") or "") if best_match.get("event", "General") != "General" else "➖"
        reveal_text = (
            f"🔎 <b>Recognized!</b>\n\n"
            f"<b>Name:</b> <b>{escape_html(best_match['name'])}</b>\n"
            f"🔖 <b>Character ID:</b> <code>{display_char_id(best_match['char_id'])}</code>\n"
            f"{rarity_emoji} <b>Rarity:</b> {best_match.get('rarity', '?')}\n"
            f"🎡 <b>Event:</b> {event_display}\n"
            f"{artist_line(best_match)}\n\n"
            f"<code>/obtain {escape_html(best_match['name'])}</code>\n\n"
            f"<i>Only catchable if this one is actually live right now — this is just a lookup.</i>"
        )
        await event.reply(reveal_text, parse_mode='html')
    except Exception as e:
        print(f"identify reply error: {e}")

# ---- /who (aliases: /w, /waifu) ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.](?:who|w|waifu)(?:@\w+)?$', 'bot1')))
async def who_reveal_handler(event):
    user_id = event.sender_id
    chat_id = event.chat_id
    # Check if user is spam-muted — GLOBALLY, across every group.
    # 🩹 FIX (per owner report — "the biggest bug"): this used to reply "you're muted" EVERY
    # single time the user tried again during their mute window — spam_detection_and_mute
    # already sends the ONE proper notice the instant the mute is first triggered, and Telethon
    # dispatches this handler independently for the same message regardless. Replying again
    # here on every retry was itself spammy. Just stay silent now — the message-deletion in
    # spam_detection_and_mute already handles it.
    if user_id in user_mute_until and time.time() < user_mute_until[user_id]:
        return

    # 🩹 CHANGED (per owner request): only skip when this is a reply to ANOTHER BOT's own
    # message. Several different collector bots run their own /w, /waifu, /who in the same
    # groups — bot1 used to respond even when someone was clearly replying to a DIFFERENT
    # bot's spawn (Path B below matched on ANY photo/video reply, regardless of which bot
    # posted it). Note this only checks the sender's `bot` flag, NOT "is it bot1" — a reply
    # to a regular user's saved/reposted photo or video (the Path B identify-a-saved-character
    # use case) must still fall through below, or /w /waifu /who on those goes completely
    # silent, which was itself a bug (see owner report). A bare /who with no reply (e.g.
    # media-as-caption) is unaffected either way and still works as before.
    if event.is_reply:
        replied_msg = await event.get_reply_message()
        if not replied_msg:
            return
        bot_me = await bot1.get_me()
        if replied_msg.sender_id != bot_me.id:
            replied_sender = await replied_msg.get_sender()
            if getattr(replied_sender, 'bot', False):
                return

    # ---- Path A: replying to the CURRENT live spawn message in this group — original,
    # unchanged flow (expiry/claimed guards, catch button, etc.) ----
    if not event.is_private and chat_id in active_group_spawns:
        spawn_data = active_group_spawns[chat_id]
        if event.is_reply and event.reply_to_msg_id == spawn_data["spawn_msg_id"]:
            if time.time() - spawn_data["spawn_time"] > 300:
                del active_group_spawns[chat_id]
                return await event.reply(
                    "Too slow — this spawn already expired. Wait for the next one.",
                    parse_mode='html'
                )
            if spawn_data.get("claimed"):
                return await _reply_already_caught(event, spawn_data)
            # 🩹 FIX (per owner request — "/w ကို တစ်ကြိမ်ထက်ပို မလုပ်စေချင်"): the "revealed"
            # field already existed on spawn_data (set False at spawn time) but was never
            # actually checked here, so /w worked unlimited times for unlimited people — anyone
            # could keep replying /w and get the name shown again and again. Same atomic
            # check-and-set pattern as the /obtain claim below: whoever's /w reaches this lock
            # first is the ONLY one who gets the name revealed; everyone else after that gets
            # told it's already been revealed instead of seeing the name again.
            async with spawn_locks[chat_id]:
                spawn_data = active_group_spawns.get(chat_id)
                if not spawn_data or spawn_data.get("claimed"):
                    return await _reply_already_caught(event, spawn_data)
                if spawn_data.get("revealed"):
                    return await event.reply(
                        "🔒 <b>Already revealed by someone else!</b> You'll have to go in blind — "
                        "or just use /obtain if you already know the name. ⚡️",
                        parse_mode='html'
                    )
                spawn_data["revealed"] = True
                spawn_data["revealed_by"] = user_id
            try:
                char_name = spawn_data['name']
                rarity_tier = classify_rarity(spawn_data.get('rarity', ''))
                rarity_emoji = RARITY_EMOJI.get(rarity_tier, RARITY_DEFAULT_EMOJI)
                # 🩹 REDESIGNED (per owner request — match the small_caps fancy-font style
                # /check and the catch-success message already moved to, instead of the older
                # single-word f() bold style this still had): decorative brackets around the
                # copyable /obtain line kept as-is, still with the 👇-equivalent ⇩ arrow.
                reveal_text = (
                    f"{rarity_emoji} {small_caps('revealed! copy this and send it fast')} ⇩\n\n"
                    f"『 <code>/obtain {escape_html(char_name)}</code> 』\n\n"
                    f"🫐 <i>ပထမဆုံးပို့တဲ့သူပဲ ရမှာနော်!</i>"
                )
                try:
                    buttons = [
                        [types.KeyboardButtonCopy(
                            text=f"Obtain {char_name} now",
                            copy_text=f"/obtain {char_name}"
                        )]
                    ]
                    await event.reply(reveal_text, parse_mode='html', buttons=buttons)
                except Exception as button_error:
                    await report_system_error("who_button_fallback", f"Button error: {button_error}")
                    await event.reply(reveal_text, parse_mode='html')
            except KeyError as ke:
                error_msg = f"KeyError: {ke}. Spawn data: {spawn_data}"
                print(error_msg)
                await report_system_error("who_reveal_handler", error_msg)
                await event.reply(
                    "❌ <b>Oops! The records are incomplete. Please try again later.</b>",
                    parse_mode='html'
                )
            except Exception as e:
                error_msg = f"General error in /who: {e}\nSpawn data: {spawn_data}"
                print(error_msg)
                await report_system_error("who_reveal_handler", error_msg)
                await event.reply(
                    "❌ <b>Something went wrong while revealing! Fixing it now...</b>",
                    parse_mode='html'
                )
            return

    # ---- Path B: identify a saved/reposted photo or video — works in groups AND DM.
    # Triggers if /who was sent AS the caption on the media, or as a reply to a message
    # that carries a photo/video. (Stickers excluded — animated .webm video-stickers carry a
    # DocumentAttributeVideo alongside DocumentAttributeSticker, so .video alone isn't enough
    # to tell a real video from a video-sticker.) ----
    media_msg = None
    if (event.photo or event.video) and not event.sticker:
        media_msg = event.message
    elif event.is_reply:
        replied = await event.get_reply_message()
        if replied and (replied.photo or replied.video) and not replied.sticker:
            media_msg = replied
    if media_msg is not None:
        return await _identify_media_and_reply(event, media_msg)

    # ---- Path C: nothing to show ----
    if event.is_private:
        return await event.reply(
            "<b>Send a photo/video with /who as the caption, or reply to one with /who, to identify it.</b>",
            parse_mode='html'
        )
    if chat_id not in active_group_spawns:
        return await event.reply(
            "<b>Nothing here yet... No one has come your way.</b>",
            parse_mode='html'
        )
    return await event.reply(
        "📌 <b>Hey! Reply directly to the spawn message to reveal the buddy — "
        "or reply to a saved photo/video with /who to identify it.</b>",
        parse_mode='html'
    )

# ---- CATCH LOGIC (with daily limit and spawn_count increment on success) ----
DAILY_CATCH_LIMIT = 22  # flat cap for everyone — single source of truth, used by both the
# limit check in catch_handler and the /today "who hit the limit first" ranking below.

async def perform_catch(chat_id, user_id, spawn_data, event, reply_to_msg=None, is_callback=False, temp_msg_id=None):
    """Grants the catch reward. IMPORTANT: by the time this is called, catch_handler has
    already atomically marked spawn_data['claimed'] = True under spawn_locks[chat_id] —
    that's the actual "who wins" decision. This function no longer re-checks or re-locks
    that; it assumes the caller already won and just needs the reward + message sent.
    (This split is what fixes the freeze/spam under simultaneous catches — see catch_handler.)"""
    try:
        # 🩹 FIX: mention/plain_name/ensure_user_registered used to run BEFORE this try block,
        # so any failure in them (Telegram API hiccup, flood-wait, transient network error)
        # was completely unhandled — it escaped perform_catch entirely, and if the caller also
        # didn't wrap the call (as catch_handler didn't, before its own fix), the whole catch
        # silently vanished: no success message, no error message, spawn stuck claimed=True,
        # racer's win thrown away. Now every step that can fail here is covered by the same
        # except block below, which always leaves the player with a visible result.
        mention = await get_html_mention(event, user_id)
        plain_name = await get_plain_name(event, user_id)
        await ensure_user_registered(user_id, plain_name)
        # Update user: add card, increment total, balance, group catches, and daily catches.
        # find_one_and_update (not update_one) so we get the POST-increment daily_catches
        # back atomically, with no separate read that could race against another catch.
        updated_user = await users_catcher_col.find_one_and_update(
            {"user_id": user_id},
            {
                "$push": {
                    "harem": {
                        "char_id": spawn_data['char_id'],
                        "caught_date": time.time(),
                        "rarity": spawn_data['rarity'],
                        "status": "vault",
                        "chat_id": chat_id
                    }
                },
                "$inc": {
                    "total_caught": 1,
                    "star_balance": catch_rarity_star_bonus(spawn_data['rarity']) + catch_star_reward(spawn_data['rarity']),
                    f"group_catches.{str(chat_id)}": 1,
                    "daily_catches": 1
                },
                "$set": {"fullname": plain_name, "last_catch_date": datetime.now(TZ)}
            },
            upsert=True,
            return_document=ReturnDocument.AFTER
        )
        await settle_star_debt(user_id)
        # Record exactly when this user's daily_catches first reached today's cap — this is
        # what /today's leaderboard sorts by ("who hit the limit first"). daily_catches only
        # ever moves up by 1 and is reset to 0 (with daily_limit_hit_at unset) at the start of
        # each new day — see the day-rollover check in catch_handler below — so the update
        # that brings it to exactly the user's own cap (PREMIUM_DAILY_CATCH_LIMIT for Premium,
        # DAILY_CATCH_LIMIT otherwise) is the one and only moment that happens today, and the
        # $exists guard makes the write itself idempotent too.
        effective_limit = PREMIUM_DAILY_CATCH_LIMIT if is_premium_active(updated_user) else DAILY_CATCH_LIMIT
        if updated_user and updated_user.get("daily_catches") == effective_limit:
            await users_catcher_col.update_one(
                {"user_id": user_id, "daily_limit_hit_at": {"$exists": False}},
                {"$set": {"daily_limit_hit_at": datetime.now(TZ)}}
            )
        # ✅ Increment spawn_count ONLY on successful catch
        await characters_base_col.update_one(
            {"char_id": spawn_data['char_id']},
            {"$inc": {"spawn_count": 1}}
        )
        guild_levelup_msg = ""
        user_guild = await guilds_col.find_one({"members": user_id})
        if user_guild:
            new_xp = user_guild["xp"] + 10
            current_level = user_guild["level"]
            if new_xp >= (current_level * 500):
                await guilds_col.update_one({"_id": user_guild["_id"]}, {"$set": {"xp": 0}, "$inc": {"level": 1}})
                guild_levelup_msg = f"\n\n🏰 <b>Guild Level Up!</b> 👑\nYour guild <b>[{escape_html(user_guild['name'])}]</b> is now Level <b>{current_level + 1}</b>! 🎉"
            else:
                await guilds_col.update_one({"_id": user_guild["_id"]}, {"$inc": {"xp": 10}})
        character_name = spawn_data['name']
        asyncio.create_task(_maybe_reward_artist_for_collect(spawn_data.get('artist'), mention, character_name, spawn_data['char_id']))
        newly_earned = await check_and_award_achievements(user_id)
        raw_event_name = spawn_data.get('event')
        event_display = escape_html(raw_event_name) if raw_event_name and raw_event_name != "General" else ""
        rarity_tier = classify_rarity(spawn_data['rarity'])
        rarity_emoji = RARITY_EMOJI.get(rarity_tier, RARITY_DEFAULT_EMOJI)
        rarity_name = RARITY_DISPLAY_NAME.get(rarity_tier, rarity_tier)
        # 👑 Premium catchers get a flashier catch card than everyone else — a banner up top,
        # a crown next to their name, and the 🎃🐢 pair — so a Premium catch visibly stands out
        # in the group.
        is_catcher_premium = is_premium_active(updated_user)
        premium_banner = "『♛ <b>PREMIUM COLLECTOR</b> ♛』 \n" if is_catcher_premium else ""
        premium_crown = " 👑" if is_catcher_premium else ""

        # 🩹 NEW (per owner request — reference template provided): "(owned/total)" progress
        # within this character's own series, same spirit as /harem's per-category header.
        # updated_user already has the FULL post-catch harem in hand from the
        # find_one_and_update above, so this is one count_documents call, not a second
        # round-trip to re-fetch the user — cheap enough for a per-catch hot path.
        category = spawn_data.get('category') or 'Unknown Series'
        owned_char_ids = list({
            item.get("char_id") for item in updated_user.get("harem", [])
            if isinstance(item, dict) and item.get("char_id")
        })
        owned_in_category = await characters_base_col.count_documents(
            {"category": category, "char_id": {"$in": owned_char_ids}}
        )
        category_totals = await get_category_totals_cached()
        category_total = category_totals.get(category, owned_in_category)

        # 🎗️ Same lightweight LE emoji marker /harem and /check show next to a name — see
        # extract_leading_emoji()/bookend_emoji_name(). spawn_data doesn't carry
        # is_limited_edition/le_name (spawns are picked from the whole roster, most of which
        # isn't LE), so this is the one spot that still needs its own small lookup — a single
        # projected find_one on an already-indexed char_id, not a big query.
        le_marker = ""
        le_char_doc = await characters_base_col.find_one(
            {"char_id": spawn_data['char_id']}, {"is_limited_edition": 1, "le_name": 1, "_id": 0}
        )
        if le_char_doc and le_char_doc.get("is_limited_edition"):
            le_emoji = extract_leading_emoji(le_char_doc.get("le_name") or "")
            if le_emoji:
                le_marker = f" {le_emoji}"

        # 🩹 REDESIGNED (2026-08, per owner request — reference template provided): fancy-font
        # labels (small_caps / sans_bold_italic — see their definitions near f() at the top of
        # the file), matching the same style /check now uses. One deliberate wording change
        # from the reference template: "you f***ed a new character" -> "you snagged a new
        # character" — this message posts unfiltered into every group the bot is in, to
        # whoever's there, so it stays PG while keeping the same punchy, cheeky tone.
        success_msg = (
            f"{premium_banner}"
            f"🪷 {mention}{premium_crown}, {small_caps('you snagged a new character')}!\n\n"
            f"🫧 N{small_caps('ame')}: <b>{escape_html(character_name)}{le_marker}</b>"
            f"{' (' + event_display + ')' if event_display else ''} | 🐧 <code>{display_char_id(spawn_data['char_id'])}</code>\n"
            f"{rarity_emoji} {sans_bold_italic('RARITY')}: {rarity_name}\n"
            f"🏖️ A{small_caps('nime')}: {escape_html(category)} (<code>{owned_in_category}/{category_total}</code>)\n"
            f"{artist_line(spawn_data.get('artist'))}"
            f"💰 +{format_star_plain(catch_rarity_star_bonus(spawn_data['rarity']) + catch_star_reward(spawn_data['rarity']))}\n\n"
            f"👒 {small_caps('check your')} <code>/harem</code>!"
        )

        if guild_levelup_msg:
            success_msg += guild_levelup_msg
        success_msg += format_achievement_unlocks(newly_earned)
        # 🩹 CHANGED (per owner request): the old buttons here were "🐇Collections" (switch_inline
        # into the catcher's FULL harem gallery — heavy, especially for a big collection) and
        # "🦋Profile". Replaced with something scoped to just this catch: a "Check" button that
        # shows exactly this card's own /check info (see checkcard_ in system_callback_router),
        # and a "Balance" button that pops the catcher's current USD/⭐ balance as a quick alert
        # — no extra message sent to the group at all.
        success_buttons = [
            [
                Button.inline(f"🔍 Check #{display_char_id(spawn_data['char_id'])}", data=f"checkcard_{spawn_data['char_id']}"),
                Button.inline("💰 Balance", data=f"quickbal_{user_id}")
            ],
            # 🎗️ NEW (per owner request): also reachable straight from a fresh catch, not just
            # /harem — same "leopen" callback, opens the paginated LE batch-name menu (see
            # _build_le_batch_menu near /show's nav buttons).
            [Button.inline("🎗️ Limited Editions", data="leopen")]
        ]
        if chat_id in active_group_spawns: del active_group_spawns[chat_id]
        if chat_id in spawn_locks: del spawn_locks[chat_id]
        if is_callback:
            await bot1.send_message(chat_id, success_msg, reply_to=reply_to_msg or event.message_id, parse_mode='html', buttons=success_buttons)
        elif temp_msg_id:
            # 🩹 Edit the 🐛→🦋 catching-animation message into the final result in place,
            # instead of deleting it and sending a brand new message — one message, no flicker.
            try:
                await bot1.edit_message(chat_id, temp_msg_id, success_msg, parse_mode='html', buttons=success_buttons)
            except errors.MessageNotModifiedError:
                pass
            except Exception:
                # Edit failed for some other reason (e.g. message too old) — make sure the
                # player still sees their result rather than silently losing it.
                await event.reply(success_msg, parse_mode='html', buttons=success_buttons)
        else:
            await event.reply(success_msg, parse_mode='html', buttons=success_buttons)
        return True
    except Exception as e:
        # NOTE: we deliberately do NOT reset claimed=False here. This spawn was already
        # atomically handed to this user in catch_handler — reopening it after a failure
        # (e.g. a Telegram flood-wait mid-flow) is exactly how two people could end up
        # winning the same character. Instead we clear the spawn out so the game doesn't
        # get stuck, log the fault, and let the next spawn come normally.
        if chat_id in active_group_spawns: del active_group_spawns[chat_id]
        if chat_id in spawn_locks: del spawn_locks[chat_id]
        error_msg = f"❌ <b>Catch Logic Fault:</b> {e}"
        if is_callback:
            await bot1.send_message(chat_id, error_msg, parse_mode='html')
        else:
            await event.reply(error_msg, parse_mode='html')
        return False

CATCH_USAGE_TEXT = (
    "📌 <b>Usage:</b> <code>/obtain [Character Name]</code>\n"
    "<i>Reply with /who to reveal a spawn, then use the exact name shown, "
    "or just tap the inline button.</i>"
)

# 🎯 CATCH_RACE_JUDGE_WINDOW — 2026-08, per owner report (repeated, and confirmed via
# screenshot even AFTER the release_spawn timing fix): "first person to type it doesn't get
# it, a later person does." The previous fix (no `await` at all between arriving in
# catch_handler and the atomic claim, so — in theory — whichever racer's task got dispatched
# first would reach the lock first) was necessary but evidently NOT sufficient: it assumes
# asyncio task-scheduling order faithfully mirrors real send order, but that's not a
# guarantee Telethon's update dispatch actually makes — under real load, a later-sent message
# CAN still get its handler task scheduled to run before an earlier one's, for reasons outside
# this bot's control.
#
# So the win condition no longer depends on task-scheduling order AT ALL. Every valid /obtain
# attempt for a spawn is now buffered for CATCH_RACE_JUDGE_WINDOW seconds, and the winner is
# picked by comparing `event.id` — the message ID Telegram itself assigns, strictly increasing
# per chat, authoritative and independent of any bot-side processing jitter. This is a genuine
# trade-off: EVERY catch (including an uncontested solo one) now waits out this window before
# the catch even begins, in exchange for a fairness guarantee that no longer depends on
# framework/scheduler internals. See catch_handler for the buffering + judging logic.
CATCH_RACE_JUDGE_WINDOW = 0.6

# 🩹 CHANGED (per owner request): /collect renamed to /obtain — /collect is also a command
# name used by a different, unrelated bot running in the same groups, so bot1 was responding
# (and stepping on that other bot) every single time anyone typed /collect for ANY reason.
# /obtain (and the .obtain dot-form) is now the only trigger; /collect no longer does anything
# here at all. /morgan stays as its own separate fun alias, unrelated to this rename.
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.](?:morgan|obtain)(?:@\w+)?\s+(.*)$', 'bot1')))
async def catch_handler(event):
    if event.is_private: return
    user_id = event.sender_id
    chat_id = event.chat_id
    # Check spam mute — GLOBALLY, across every group.
    # 🩹 FIX (per owner report — "the biggest bug"): same fix as /who above — no repeated
    # "you're muted" reply on every retry, just silence. spam_detection_and_mute already sent
    # the one real notice when the mute first triggered.
    if user_id in user_mute_until and time.time() < user_mute_until[user_id]:
        return
    
    catch_name = event.pattern_match.group(1).strip()
    if not catch_name:
        return await event.reply(CATCH_USAGE_TEXT, parse_mode='html')

    # ==========================================
    # 🛸 NORMAL SPAWN CATCH LOGIC
    # ==========================================
    if chat_id not in active_group_spawns:
        return await event.reply(f"🛸 <b>Nothing to collect here!</b>", parse_mode='html')
    
    spawn_data = active_group_spawns[chat_id]
    if spawn_data["claimed"]:
        return await _reply_already_caught(event, spawn_data)
    
    if time.time() - spawn_data["spawn_time"] > 300:
        if chat_id in active_group_spawns: 
            del active_group_spawns[chat_id]
        return await event.reply(f"⏱️ <b>Too late! They've slipped away.</b>", parse_mode='html')
    
    if normalize_name(catch_name) != normalize_name(spawn_data["name"]):
        return await event.reply(f"❌ <b>Wrong name! Use the exact name shown in /who.</b>", parse_mode='html')

    # 🎯 Buffer this valid attempt and judge the whole race by real Telegram message-ID order
    # once the window closes — see CATCH_RACE_JUDGE_WINDOW's docstring for why. Every racer's
    # task lands here; only the FIRST one to arrive (task-scheduling order doesn't matter for
    # THIS part — it's just "who gets stuck holding the judging job", not who wins) actually
    # runs the code below the `if`. Everyone else just adds themselves to the buffer and
    # returns — the judge replies to them (win or lose) on their behalf once it decides.
    existing_buf = catch_race_buffer.get(chat_id)
    if existing_buf is not None:
        existing_buf.append((event, user_id))
        return
    buf = [(event, user_id)]
    catch_race_buffer[chat_id] = buf
    try:
        await asyncio.sleep(CATCH_RACE_JUDGE_WINDOW)
    finally:
        # try/finally so a stuck buffer entry (which would silently swallow every future
        # /obtain in this chat — see the `if existing_buf is not None` check above) can never
        # survive an unexpected error here.
        candidates = catch_race_buffer.pop(chat_id, buf)

    # Earliest real Telegram message ID = whoever actually sent /obtain first, full stop —
    # independent of network jitter, DB round trips, or asyncio scheduling on the bot's side.
    candidates.sort(key=lambda c: c[0].id)
    winner_event, user_id = candidates[0]
    losers = candidates[1:]

    # 🔒 Claim the spawn atomically — check-and-set with no await in between, under the
    # per-chat lock. The judging above already decided WHO should win; this lock is now only
    # guarding against the spawn having been claimed some other way in the meantime (expiry
    # cleanup, owner command, etc.) — not against another racer, since every racer for this
    # spawn is already accounted for in `candidates` by this point.
    async with spawn_locks[chat_id]:
        spawn_data = active_group_spawns.get(chat_id)
        if not spawn_data or spawn_data.get("claimed"):
            for loser_event, _loser_uid in candidates:
                asyncio.create_task(_reply_already_caught(loser_event, spawn_data))
            return
        spawn_data["claimed"] = True
        spawn_data["claimed_by"] = user_id

    # Tell everyone who lost this specific race exactly who beat them — fired once the winner's
    # own catch flow has actually finished below, not here (see the try block around
    # perform_catch further down), so "Too slow!" never lands in the group before the winner's
    # own success message has gone out.
    event = winner_event  # everything below acts on behalf of the judged winner

    # Daily limit check — now runs only for the winner just claimed above. If they're already
    # capped, give the spawn back (reset claimed/claimed_by) instead of wasting it, so the
    # next racer can still catch it.
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    if user_doc:
        today = datetime.now(TZ).date()
        last_catch_date = user_doc.get("last_catch_date")
        if last_catch_date:
            last_date = last_catch_date.date() if isinstance(last_catch_date, datetime) else last_catch_date
            if last_date != today:
                await users_catcher_col.update_one(
                    {"user_id": user_id},
                    {"$set": {"daily_catches": 0, "last_catch_date": datetime.now(TZ)},
                     "$unset": {"daily_limit_hit_at": ""}}
                )
                user_doc["daily_catches"] = 0
        daily_catches = user_doc.get("daily_catches", 0)
        daily_limit = PREMIUM_DAILY_CATCH_LIMIT if is_premium_active(user_doc) else DAILY_CATCH_LIMIT
        if daily_catches >= daily_limit:
            async with spawn_locks[chat_id]:
                still_current = active_group_spawns.get(chat_id)
                if still_current is spawn_data and still_current.get("claimed_by") == user_id:
                    still_current["claimed"] = False
                    still_current["claimed_by"] = None
            mention = await get_html_mention(event, user_id)
            return await event.reply(
                f"<b>Notice:</b> {mention}, you've reached your daily catch limit of {daily_limit}! "
                f"That's enough for today. Come back tomorrow. 🌟",
                parse_mode='html'
            )

    # 🐛→🦋 catching animation: one message, edited in place frame-by-frame — no extra
    # replies sent, and perform_catch() below edits this same message into the final result
    # (instead of deleting it and sending a new one).
    # 🩹 CHANGED (per owner request): this used to be event.reply("💫") — a REPLY to the
    # racer's own /obtain message, which meant onlookers could see (via the reply preview)
    # who was in the running before the result was ever revealed. Sent as a plain message now,
    # so nobody knows who's even attempting it until the final edit reveals the winner.
    temp_msg = await bot1.send_message(chat_id, "⚡️")
    await asyncio.sleep(1.2)
    try:
        await temp_msg.edit("💥")
    except errors.MessageNotModifiedError:
        pass
    await asyncio.sleep(1.2)
    # 🩹 FIX (per owner report — "sometimes only the 2 emoji show, no success message"): this
    # call used to be bare. perform_catch() already catches its own internal errors, but
    # get_html_mention/get_plain_name/ensure_user_registered used to run BEFORE its try block
    # (see perform_catch below) — any hiccup there (Telegram flood-wait, transient network
    # error, etc.) threw an exception with nothing here to catch it. Result: the ⚡️→💥
    # animation message was left stuck forever, the spawn stayed marked claimed=True with no
    # winner ever announced, and the racer's win was silently thrown away. This try/except is
    # the last-resort net — it guarantees the animation message always gets a final result
    # (success OR a visible error) and that the spawn/lock state never gets stuck.
    try:
        await perform_catch(chat_id, user_id, spawn_data, event, reply_to_msg=event.id, is_callback=False, temp_msg_id=temp_msg.id)
        # 🩹 CHANGED (per owner request): losers now get told "Too slow! X got it first" only
        # AFTER X's own success message has actually landed — fired off concurrently from here
        # so it doesn't block anything further, but never before this point.
        for loser_event, _loser_uid in losers:
            asyncio.create_task(_reply_already_caught(loser_event, spawn_data))
    except Exception as e:
        if chat_id in active_group_spawns:
            del active_group_spawns[chat_id]
        if chat_id in spawn_locks:
            del spawn_locks[chat_id]
        error_msg = f"❌ <b>Catch Logic Fault:</b> {escape_html(str(e))}"
        try:
            await bot1.edit_message(chat_id, temp_msg.id, error_msg, parse_mode='html')
        except Exception:
            await event.reply(error_msg, parse_mode='html')

async def _reply_already_caught(event, spawn_data):
    """Short, English, tells whoever lost the race exactly who beat them — replaces the old
    generic 'Already caught!' which left everyone guessing."""
    winner_id = (spawn_data or {}).get("claimed_by")
    if winner_id:
        mention = await get_html_mention(event, winner_id)
        text = f"Too slow! {mention} got it first. 🐸"
    else:
        text = "❌ <b>Already caught!</b> 🐸"
    return await event.reply(text, parse_mode='html')
# ---- /refillcatch: recompute spawn_count from what's ACTUALLY in players' vaults ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]refillcatch(?:@\w+)?(?:\s+([a-zA-Z0-9_]+))?$', 'bot1')))
async def refillcatch_handler(event):
    if event.sender_id != OWNER_ID:
        return
    char_id_arg = event.pattern_match.group(1)
    status_msg = await event.reply("⏳ <b>Recalculating catch counts from real vault data...</b>", parse_mode='html')
    if char_id_arg:
        char_id_arg = char_id_arg.upper()
        char_doc = await characters_base_col.find_one({"char_id": char_id_arg})
        if not char_doc:
            return await status_msg.edit(f"❌ No character found with ID <code>{escape_html(char_id_arg)}</code>.", parse_mode='html')
        pipeline = [
            {"$unwind": "$harem"},
            {"$match": {"harem.char_id": char_id_arg}},
            {"$count": "total"}
        ]
        result = await users_catcher_col.aggregate(pipeline).to_list(length=1)
        real_count = result[0]["total"] if result else 0
        await characters_base_col.update_one({"char_id": char_id_arg}, {"$set": {"spawn_count": real_count}})
        await invalidate_character_caches()
        await status_msg.edit(
            f"✅ <b>{escape_html(char_doc['name'])}</b> (<code>{char_id_arg}</code>) synced!\n"
            f"📈 <b>Global Catches (real):</b> <code>{real_count}</code>",
            parse_mode='html'
        )
    else:
        pipeline = [
            {"$unwind": "$harem"},
            {"$group": {"_id": "$harem.char_id", "count": {"$sum": 1}}}
        ]
        results = await users_catcher_col.aggregate(pipeline).to_list(length=None)
        counts_map = {r["_id"]: r["count"] for r in results if r.get("_id")}
        all_chars = await characters_base_col.find({}, {"char_id": 1}).to_list(length=None)
        ops = [
            UpdateOne({"char_id": c["char_id"]}, {"$set": {"spawn_count": counts_map.get(c["char_id"], 0)}})
            for c in all_chars
        ]
        if ops:
            await characters_base_col.bulk_write(ops)
        await invalidate_character_caches()
        await status_msg.edit(
            f"✅ <b>Full Resync Complete!</b>\n"
            f"🔁 <code>{len(ops)}</code> characters' Global Catch counts now match players' real vaults.",
            parse_mode='html'
        )

# ==========================================
# 🎯 HMODE – RARITY FILTER
# ==========================================
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]hmode(?:@\w+)?$', 'bot1')))
async def set_rarity_filter_handler(event):
    user_id = event.sender_id
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    current_filter = user_doc.get("rarity_filter") if user_doc else None
    current_sort = (user_doc.get("harem_sort") or "rarity") if user_doc else "rarity"
    # 🩹 FIX: was 9 full-width rows (one per rarity), each showing the whole fancy-font name
    # like "🎞 𝖴𝖫𝖳𝖱𝖠𝖲𝖳𝖠𝖱 𝖭𝗈.𝟣" — ate a huge amount of vertical space in the chat. Now a
    # compact 3x3 grid, emoji-only per button; the current selection still gets a ✅ prefix
    # so it stays recognizable even without the text label.
    buttons = []
    row = []
    for num, data in RARITY_NUM_MAP.items():
        tier = RARITY_TIERS[int(num) - 1]
        emoji = RARITY_EMOJI[tier]
        rarity_name = data["name"]
        label = f"✅{emoji}" if current_filter == rarity_name else emoji
        row.append(Button.inline(label, data=f"hfilter_{num}_{user_id}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    clear_label = "🔓 Clear Filter" if current_filter else "🔒 No Filter"
    buttons.append([Button.inline(clear_label, data=f"hfilter_clear_{user_id}")])
    # 🩹 NEW (per owner request — "Harem Sort/Filter"): sort order is a separate axis from the
    # rarity filter above (filter narrows WHICH cards show, sort decides what ORDER they show
    # in) — its own row so the two don't get confused for one setting. "🏆 Rarity" is the
    # original category-grouped layout this always had; "🕐 Recent" and "🔤 A-Z" are both flat,
    # ungrouped views — see send_paginated_harem's own harem_sort handling.
    sort_options = [("rarity", "🏆 Rarity"), ("recent", "🕐 Recent"), ("az", "🔤 A-Z")]
    buttons.append([
        Button.inline(f"✅{label}" if current_sort == key else label, data=f"hsort_{key}_{user_id}")
        for key, label in sort_options
    ])
    buttons.append([Button.inline("🔙 Back", data="nav_back_home")])
    await event.reply(
        f"🎯 <b>/harem Sort &amp; Filter</b>\n"
        f"Filter: {current_filter if current_filter else 'None (Show All)'}\n"
        f"Sort: {dict(sort_options)[current_sort]}\n\n"
        f"Tap a rarity to filter your vault, or pick a sort order below.\n"
        f"<i>🔐 Only you can use these buttons.</i>",
        buttons=buttons,
        parse_mode='html'
    )

@bot1.on(events.CallbackQuery(pattern=r'^hsort_(rarity|recent|az)_(\d+)$'))
async def harem_sort_callback(event):
    sort_key = event.pattern_match.group(1)
    if isinstance(sort_key, bytes):
        sort_key = sort_key.decode('utf-8')
    owner_user_id = int(event.pattern_match.group(2))
    if event.sender_id != owner_user_id:
        return await event.answer("⚠️ ဒါက သင့်ရဲ့ Sort Setting မဟုတ်ပါ။ /hmode ကို ကိုယ်တိုင်နှိပ်ပါ။", alert=True)
    await users_catcher_col.update_one(
        {"user_id": owner_user_id},
        {"$set": {"harem_sort": sort_key}},
        upsert=True
    )
    label = {"rarity": "🏆 Rarity", "recent": "🕐 Recent", "az": "🔤 A-Z"}[sort_key]
    await event.answer(f"✅ Sort set to {label}")
    await event.edit(f"✅ Sort set to {label}. Use /harem to see it applied.")

@bot1.on(events.CallbackQuery(pattern=r'^hfilter_(\d+|clear)_(\d+)$'))
async def rarity_filter_callback(event):
    action = event.pattern_match.group(1)
    if isinstance(action, bytes):
        action = action.decode('utf-8')
    owner_user_id = int(event.pattern_match.group(2))
    if event.sender_id != owner_user_id:
        return await event.answer("⚠️ ဒါက သင့်ရဲ့ Rarity Filter မဟုတ်ပါ။ /hmode ကို ကိုယ်တိုင်နှိပ်ပါ။", alert=True)
    user_id = owner_user_id
    if action == "clear":
        await users_catcher_col.update_one(
            {"user_id": user_id},
            {"$set": {"rarity_filter": None}},
            upsert=True
        )
        await event.answer("🔓 Filter cleared! All rarities will be shown.")
        await event.edit("✅ Filter cleared. Use /harem to see all cards.")
        return
    rarity_num = action
    rarity_data = RARITY_NUM_MAP.get(rarity_num)
    if not rarity_data:
        await event.answer("❌ Invalid rarity.")
        return
    rarity_name = rarity_data["name"]
    await users_catcher_col.update_one(
        {"user_id": user_id},
        {"$set": {"rarity_filter": rarity_name}},
        upsert=True
    )
    await event.answer(f"✅ Filter set to {rarity_name}")
    await event.edit(f"✅ Filter set to {rarity_name}. Use /harem to see your filtered vault.")

# ==========================================
# 🎒 INVENTORY / HAREM
# ==========================================
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]harem(?:@\w+)?(?:\s+(.*))?$', 'bot1')))
async def show_harem_vault(event):
    # 🩹 CHANGED (per owner request): /harem used to let anyone view someone ELSE's vault by
    # replying to their message or passing @username/user_id — that's removed now. /harem
    # always shows the sender's own vault, full stop. Any argument/reply is simply ignored.
    user_id = event.sender_id
    plain_name = await get_plain_name(event, user_id)
    await ensure_user_registered(user_id, plain_name)
    await send_paginated_harem(bot1, event.chat_id, user_id, page=1, viewer_id=user_id)

# ---- /fav bare usage ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]fav(?:@\w+)?$', 'bot1')))
async def fav_bare_usage_handler(event):
    await event.reply(
        "📌 <b>Usage:</b> <code>/fav [CharID]</code>\n"
        "<i>Example:</i> <code>/fav 1234</code>\n"
        "Sets a card you own as the thumbnail shown in <code>/harem</code>.",
        parse_mode='html'
    )

# ---- /fav with inline confirmation ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]fav(?:@\w+)?\s+([a-zA-Z0-9_]+)$', 'bot1')))
async def set_favorite_card(event):
    user_id = event.sender_id
    raw_input = event.pattern_match.group(1).strip()
    char_id_input = normalize_char_id_input(raw_input)
    
    # ✅ Case-Insensitive ရှာဖို့
    card = await characters_base_col.find_one({
        "char_id": {"$regex": f"^{char_id_input}$", "$options": "i"}
    })
    
    if not card:
        # ✅ အနီးဆုံး ID တွေကို ပြပေးမယ် (raw input သုံးမယ် — BOD ကို prepend လုပ်ထားတဲ့ char_id_input သုံးရင် အကုန် "BOD" နဲ့ စတော့ fuzzy match အလုပ်မလုပ်တော့ဘူး)
        similar = await characters_base_col.find(
            {"char_id": {"$regex": re.escape(raw_input[:3]), "$options": "i"}}
        ).limit(5).to_list(length=5)
        reply = f"❌ <b>Card not found!</b>\n\n"
        if similar:
            reply += "💡 <b>Did you mean:</b>\n"
            for s in similar:
                reply += f"• <code>{display_char_id(s['char_id'])}</code> - {s.get('name', 'Unknown')}\n"
        else:
            reply += "📭 No similar cards found in database."
        return await event.reply(reply, parse_mode='html')
    
    # ✅ Card ကိုတွေ့ရင် ဆက်လုပ်ပါ
    actual_char_id = card["char_id"]  # Database ထဲက အတိအကျ ID
    
    if user_id == OWNER_ID:
        # 👑 Unlimited vault — every character counts as owned, no harem lookup needed.
        owns_card = True
    else:
        user_doc = await users_catcher_col.find_one({"user_id": user_id})
        user_harem = user_doc.get("harem", []) if user_doc else []
        owns_card = any(isinstance(x, dict) and x.get("char_id").upper() == actual_char_id.upper() for x in user_harem)
    
    if not owns_card:
        return await event.reply(f"❌ <b>You don't own this card!</b>", parse_mode='html')
    
    # ✅ Confirmation Buttons
    confirm_text = (
        f"🎠 <b>Favourite Card Confirmation</b>\n\n"
        f"🦢 <b>Character:</b> <code>{card['name']}</code> (<code>{display_char_id(actual_char_id)}</code>)\n"
        f" <b>Rarity:</b> {card.get('rarity', 'Unknown')}\n"
        f"{artist_line(card)}\n"
        f"<blockquote>Do you really want to set this card as your favourite?</blockquote>\n"
        f"✨ Waiting for your decision! 🦋"
    )
    buttons = [
        [
            colored_button("✅ Yep, Why Not✔", f"fav_confirm_{user_id}_{actual_char_id}", color="success"),
            Button.inline("❌ Nope", data=f"fav_cancel_{user_id}_{actual_char_id}")
        ]
    ]
    # 🩹 FIX: this used to be event.reply(confirm_text, ...) with no `file=` at all — the
    # confirmation always showed as plain text, never the card's own photo, even though
    # storage_msg_id was right there on `card`. Same send_with_char_media pattern (handles a
    # stale file_reference with one retry) used by /show, /harem's fav thumbnail, etc.
    async def _send_fav_confirm(media):
        return await event.reply(confirm_text, file=media, buttons=buttons, parse_mode='html')
    sent = await send_with_char_media(actual_char_id, card["storage_msg_id"], _send_fav_confirm)
    if sent is None:
        await event.reply(confirm_text, buttons=buttons, parse_mode='html')

# ---- Helper functions for harem ----
async def remove_one_harem_copy(user_id, char_id, status):
    """Atomically removes exactly ONE harem copy matching (char_id, status) from user_id's
    vault — safe even when they own several copies of the same char_id (duplicates of common
    cards are normal and expected). Uses the positional $ operator (which only ever matches
    the FIRST array element that fits the filter) to null that one slot out, then a cheap
    $pull sweep to actually drop the null.
    🩹 FIX: this replaces the old pattern used by marketplace-buy and /trade-confirm — find_one
    the whole harem array, remove(item) in plain Python, then $set the WHOLE array back. That
    read-modify-write had no atomicity at all: if the same account did anything else to their
    harem (a gift, another sale, another trade) in the gap between the read and the write, that
    other change was silently overwritten by this stale full-array write — a classic lost-update
    race, and a plausible way for cards to duplicate or vanish under concurrent use. There's no
    such gap here: both updates below are single-document atomic operations."""
    await users_catcher_col.update_one(
        {"user_id": user_id, "harem": {"$elemMatch": {"char_id": char_id, "status": status}}},
        {"$unset": {"harem.$": 1}}
    )
    await users_catcher_col.update_one({"user_id": user_id}, {"$pull": {"harem": None}})

async def clear_stale_favorite(user_id, char_id, remaining_harem):
    """Call this right after removing char_id from user_id's harem (gift / sell-and-bought /
    trade / scrap). If that was their LAST copy and it happened to be their /fav card, unset
    fav_card so /harem and /profile stop showing a card they no longer own."""
    still_owns_it = any(isinstance(it, dict) and it.get("char_id") == char_id for it in remaining_harem)
    if still_owns_it:
        return
    await users_catcher_col.update_one(
        {"user_id": user_id, "fav_card": char_id},
        {"$unset": {"fav_card": ""}}
    )

CARD_RANK_CACHE_TTL = 600  # 10 minutes — ranks don't need to be perfectly real-time, a bit of staleness is fine

def _rank_from_owners(owners, user_id):
    for idx, o in enumerate(owners, start=1):
        if o.get("user_id") == user_id:
            return idx
    return None

async def get_user_rank_for_card(user_id, char_id):
    """Single-card lookup — kept for any other call site that only needs one rank.
    /harem itself no longer uses this (see get_user_ranks_for_cards below), since
    calling this once per owned card was the N+1 query pattern that bogged it down."""
    ranks = await get_user_ranks_for_cards(user_id, [char_id])
    return ranks.get(char_id)

async def get_user_ranks_for_cards(user_id, char_ids):
    """Batched, cached replacement for calling get_user_rank_for_card once per card.
    Returns {char_id: rank_or_None} for every char_id in char_ids.

    Two layers make this cheap even under heavy concurrent /harem traffic:
    1. Each card's top-10 ownership leaderboard is cached in Redis (CARD_RANK_CACHE_TTL).
       This cache is *shared across every user* — a popular card's leaderboard is the
       same list no matter whose /harem is asking, so cache hit rate is high.
    2. Whatever isn't cached gets resolved in ONE aggregation call covering every
       missing char_id at once (relies on the harem.char_id index), instead of the
       old approach of one full aggregation per card."""
    if not char_ids:
        return {}
    ranks = {}
    missing = []
    # Batched into a single MGET round-trip instead of one GET per char_id in a loop — the
    # old loop meant len(char_ids) sequential network round-trips (up to 500 for a full
    # /harem before the page-scoped fix above), and if Redis was unreachable each of those
    # had to time out individually instead of failing once.
    try:
        cached_values = await redis_client.mget([f"cardrank:{cid}" for cid in char_ids])
    except Exception:
        cached_values = [None] * len(char_ids)
    for cid, cached in zip(char_ids, cached_values):
        if cached is not None:
            ranks[cid] = _rank_from_owners(json.loads(cached), user_id)
        else:
            missing.append(cid)
    if not missing:
        return ranks
    pipeline = [
        {"$match": {"harem.char_id": {"$in": missing}}},
        {"$project": {
            "user_id": 1,
            "harem": {"$filter": {"input": "$harem", "as": "item", "cond": {"$in": ["$$item.char_id", missing]}}}
        }},
        {"$unwind": "$harem"},
        {"$group": {"_id": {"char_id": "$harem.char_id", "user_id": "$user_id"}, "count": {"$sum": 1}}},
        {"$sort": {"_id.char_id": 1, "count": -1}},
        {"$group": {"_id": "$_id.char_id", "owners": {"$push": {"user_id": "$_id.user_id", "count": "$count"}}}},
        {"$project": {"owners": {"$slice": ["$owners", 10]}}}
    ]
    try:
        results = await users_catcher_col.aggregate(pipeline).to_list(length=None)
    except Exception as e:
        logging.error(f"⚠️ get_user_ranks_for_cards aggregation error: {e}")
        results = []
    found_ids = set()
    for doc in results:
        cid = doc["_id"]
        owners = doc.get("owners", [])
        found_ids.add(cid)
        ranks[cid] = _rank_from_owners(owners, user_id)
        try:
            await redis_client.setex(f"cardrank:{cid}", CARD_RANK_CACHE_TTL, json.dumps(owners))
        except Exception:
            pass
    # Cards nobody owns yet never show up in the aggregation output — cache an empty
    # list for those too, otherwise they'd re-trigger the aggregation on every call.
    for cid in missing:
        if cid not in found_ids:
            ranks[cid] = None
            try:
                await redis_client.setex(f"cardrank:{cid}", CARD_RANK_CACHE_TTL, json.dumps([]))
            except Exception:
                pass
    return ranks

async def send_paginated_harem(client, chat_id, user_id, page=1, edit_msg_id=None, viewer_id=None):
    if viewer_id is None:
        viewer_id = user_id
    is_own_vault = (viewer_id == user_id)
    # 👑 The owner's vault is unlimited: every character ever added via /addchar (and any
    # added in the future) counts as owned, always exactly one copy each — computed live from
    # characters_base_col rather than stored, so it needs no migration and always reflects the
    # current roster automatically. The owner's REAL personal /collect catches stay in their
    # actual harem array untouched and are shown separately (see the "🎯 My Real Catches"
    # button below and the "collected.<id>" inline gallery) rather than mixed into this view.
    is_unlimited_vault = (user_id == OWNER_ID)
    # Projected — this function only ever reads harem/rarity_filter/fav_card off the user doc,
    # no need to pull star_balance, cooldowns, msg_history, etc. too.
    user_doc = await users_catcher_col.find_one({"user_id": user_id}, {"harem": 1, "rarity_filter": 1, "fav_card": 1, "harem_sort": 1, "_id": 0}) or {}
    raw_harem = user_doc.get("harem", [])
    raw_owned_id_set = {item.get("char_id") for item in raw_harem if isinstance(item, dict) and item.get("char_id")}
    rarity_filter = user_doc.get("rarity_filter")

    if is_unlimited_vault:
        all_chars = await get_all_characters_cached()  # already invalidated on /addchar etc.
        if rarity_filter:
            all_chars = [c for c in all_chars if classify_rarity(c.get("rarity")) == classify_rarity(rarity_filter)]
        if not all_chars:
            msg = (f"🚫 <b>No cards found with that rarity filter.</b>\nUse <code>/hmode</code> to change or clear it."
                   if rarity_filter else "🚫 <b>No characters exist yet</b> — add some with /addchar.")
            if edit_msg_id: await client.edit_message(chat_id, edit_msg_id, msg, parse_mode='html')
            else: await client.send_message(chat_id, msg, parse_mode='html')
            return
        harem_counts = {c["char_id"]: {"normal": 1, "market": 0} for c in all_chars}
        owned_ids = list(harem_counts.keys())
        filtered_harem = [{"char_id": cid} for cid in owned_ids]  # stand-in so len() below stays accurate
    else:
        if not raw_harem:
            msg = "📭 <b>Your vault is empty! Go catch some characters!</b>" if is_own_vault else "📭 <b>Their vault is empty!</b>"
            if edit_msg_id: await client.edit_message(chat_id, edit_msg_id, msg, parse_mode='html')
            else: await client.send_message(chat_id, msg, parse_mode='html')
            return
        filtered_harem = []
        if rarity_filter:
            for item in raw_harem:
                if isinstance(item, dict) and classify_rarity(item.get("rarity")) == classify_rarity(rarity_filter):
                    filtered_harem.append(item)
        else:
            filtered_harem = raw_harem
        if not filtered_harem:
            msg = f"🚫 <b>No cards found with that rarity filter.</b>\n"
            msg += f"Use <code>/hmode</code> to change or clear it."
            if edit_msg_id: await client.edit_message(chat_id, edit_msg_id, msg, parse_mode='html')
            else: await client.send_message(chat_id, msg, parse_mode='html')
            return
        harem_counts = {}
        for item in filtered_harem:
            if isinstance(item, dict) and "char_id" in item:
                cid = item["char_id"]
                is_market = item.get("status") == "market"
                if cid not in harem_counts:
                    harem_counts[cid] = {"normal": 0, "market": 0}
                if is_market:
                    harem_counts[cid]["market"] += 1
                else:
                    harem_counts[cid]["normal"] += 1
        owned_ids = list(harem_counts.keys())
        if not owned_ids:
            msg = f"🚫 <b>No valid cards in vault.</b>"
            if edit_msg_id: await client.edit_message(chat_id, edit_msg_id, msg, parse_mode='html')
            else: await client.send_message(chat_id, msg, parse_mode='html')
            return
    db_chars = await characters_base_col.find({"char_id": {"$in": owned_ids}}, {"char_id": 1, "name": 1, "category": 1, "rarity": 1, "is_limited_edition": 1, "le_name": 1, "_id": 0}).to_list(length=None)
    category_totals = await get_category_totals_cached()
    def get_rarity_weight(rarity_str):
        return rarity_rank_value(rarity_str)

    # 🩹 NEW (per owner request — "Harem Sort/Filter"): a per-user harem_sort setting, picked
    # via the /hmode hub alongside the existing rarity filter. "rarity" (the default, and the
    # ONLY mode for the owner's unlimited vault — grouping hundreds/thousands of characters any
    # other way stops being useful) keeps the exact category-grouped layout this always had.
    # "recent" and "az" are both flat, ungrouped views — no category headers — presented as a
    # single unlabeled block so they can reuse every bit of the pagination/rendering logic
    # below unchanged, just with a different card order feeding into it.
    def build_card_line(card):
        cid = card["char_id"]
        counts = harem_counts.get(cid, {"normal": 0, "market": 0})
        normal_qty, market_qty = counts["normal"], counts["market"]
        if normal_qty > 0 and market_qty > 0:
            status_str = f"<b>(x{normal_qty} | {market_qty} 🛒)</b>"
        elif market_qty > 0:
            status_str = f"<b>({market_qty} 🛒)</b>"
        else:
            status_str = f"<b>(x{normal_qty})</b>"
        tier = classify_rarity(card.get("rarity", ""))
        rarity_emoji = RARITY_EMOJI.get(tier, RARITY_DEFAULT_EMOJI)
        # 🩹 NEW (per owner request): LE-tagged cards get their batch's own leading emoji
        # echoed right after the name too (e.g. "... Jane Doe 🔱 (x1)") — same emoji
        # bookend_emoji_name() would put at the end of the full batch name, just without
        # the whole name spelled out here (this list is already tight on width — see
        # PAGE_CHAR_BUDGET above). A batch with no valid leading emoji (shouldn't happen
        # going forward now that /addle requires one) simply adds nothing.
        le_marker = ""
        if card.get("is_limited_edition"):
            le_emoji = extract_leading_emoji(card.get("le_name") or "")
            if le_emoji:
                le_marker = f" {le_emoji}"
        return (cid, f"<code>{display_char_id(cid)}</code> · {rarity_emoji} · {escape_html(card['name'])}{le_marker} {status_str}")

    harem_sort = (user_doc.get("harem_sort") or "rarity") if not is_unlimited_vault else "rarity"

    if harem_sort != "rarity":
        if harem_sort == "recent":
            # Most-recent caught_date per char_id, off the user's OWN harem array (db_chars
            # has no caught_date — that lives per-copy, not on the character's base doc). A
            # duplicate's LATEST catch is what counts for "how recently did I get this one".
            latest_caught = {}
            for item in raw_harem:
                if isinstance(item, dict) and item.get("char_id"):
                    cid = item["char_id"]
                    cd = item.get("caught_date", 0) or 0
                    if cd > latest_caught.get(cid, -1):
                        latest_caught[cid] = cd
            ordered_cards = sorted(db_chars, key=lambda c: -latest_caught.get(c["char_id"], 0))
        else:  # "az"
            ordered_cards = sorted(db_chars, key=lambda c: c.get("name", "").lower())
        sort_header = "🕐 Recently Caught" if harem_sort == "recent" else "🔤 A–Z"
        category_blocks = [([f"{sort_header} <code>{len(ordered_cards)}</code>"], [build_card_line(c) for c in ordered_cards])]
    else:
        from collections import defaultdict
        by_category = defaultdict(list)
        for card in db_chars:
            by_category[card.get("category") or "Unknown Series"].append(card)
        def get_category_weight(cat):
            cards = by_category[cat]
            return max([get_rarity_weight(c.get("rarity", "")) for c in cards], default=0)
        sorted_categories = sorted(by_category.keys(), key=lambda c: (-get_category_weight(c), c.lower()))
        # Build one "block" per category: a header/progress-bar pair plus each card's (char_id,
        # line) — so pagination below can keep a whole category together instead of splitting it
        # awkwardly across two pages. Card lines are built WITHOUT a rank badge yet: the rank
        # lookup is deliberately deferred until after we know which page is actually being
        # rendered, so get_user_ranks_for_cards() only has to look up the ~12 cards on that page
        # instead of every card in the vault (up to hundreds) — that used to be the main reason
        # /harem could take ~10s for big collections (see get_user_ranks_for_cards' docstring).
        category_blocks = []
        for cat in sorted_categories:
            cat_cards = sorted(by_category[cat], key=lambda x: (-get_rarity_weight(x.get("rarity", "")), x.get("name", "").lower()))
            owned_in_cat = len(cat_cards)
            total_in_cat = category_totals.get(cat, owned_in_cat)
            header_lines = [f"✨ <b>{escape_html(cat)}</b> <code>{owned_in_cat}/{total_in_cat}</code>"]
            card_entries = [build_card_line(card) for card in cat_cards]
            category_blocks.append((header_lines, card_entries))
    # 🩹 FIX: pagination used to keep whole categories together no matter what ("never split a
    # category across two pages"), grouping by a flat LINE COUNT (12/page). That broke down for
    # any category bigger than a page — e.g. a category with 80 owned cards would ALWAYS get
    # its own page by itself, however long, since it could never be split — and once its
    # rendered text got long enough it could blow straight past Telegram's real per-message
    # limit, failing the whole /harem view with "Vault Display Error". Pagination now tracks
    # the actual UTF-16 length of every line (utf16_len — fancy-font characters and many emoji
    # are 2 units each, not 1) and cuts to a new page the instant adding the next line would
    # cross PAGE_CHAR_BUDGET, splitting a category across pages when it has to (re-printing a
    # "(cont'd)" header at the top of the continuation page so it's still clear whose cards
    # those are).
    #
    # PAGE_CHAR_BUDGET is deliberately set so that EVERY page — not just page 1 — stays within
    # Telegram's 1024-char photo CAPTION limit even after adding the mention/label line, filter
    # line, page/unique/total line, and footer (worst case ~542 units with a very long display
    # name and an active filter). That's what lets the fav-card photo stay attached for the
    # whole vault, any size: since every single page is guaranteed caption-safe, Next/Previous
    # can keep editing that same photo message's caption forever without ever risking
    # "MediaCaptionTooLongError" — no need to fall back to plain text or a bigger vault at all.
    PAGE_CHAR_BUDGET = 450
    pages = []          # each page: [(char_id_or_None, line_text), ...] — None marks header/spacer lines
    current_page, current_len = [], 0

    def _flush_page():
        nonlocal current_page, current_len
        if current_page:
            pages.append(current_page)
        current_page, current_len = [], 0

    for header_lines, card_entries in category_blocks:
        if not card_entries:
            continue
        idx = 0
        header_printed = False
        continuation = False
        while idx < len(card_entries):
            if not header_printed:
                title = header_lines[0] if not continuation else f"{header_lines[0]} <i>(cont'd)</i>"
                h_lines = [title]
                h_len = sum(utf16_len(l) + 1 for l in h_lines)
                if current_page and current_len + h_len > PAGE_CHAR_BUDGET:
                    _flush_page()
                    continuation = True
                    title = f"{header_lines[0]} <i>(cont'd)</i>"
                    h_lines = [title]
                    h_len = sum(utf16_len(l) + 1 for l in h_lines)
                for l in h_lines:
                    current_page.append((None, l))
                current_len += h_len
                header_printed = True
            cid, line_body = card_entries[idx]
            line_len = utf16_len(line_body) + 1 + 4  # +4 reserves room for a rank badge suffix added after pagination
            # A page holding nothing but the header we just printed must accept at least one
            # card no matter what, or we'd flush forever without ever making progress.
            only_header_so_far = len(current_page) <= 1
            if current_page and not only_header_so_far and current_len + line_len > PAGE_CHAR_BUDGET:
                _flush_page()
                header_printed = False
                continuation = True
                continue
            current_page.append((cid, line_body))
            current_len += line_len
            idx += 1
        current_page.append((None, ""))  # blank spacer after the category
        current_len += 1
    _flush_page()
    total_pages = len(pages) or 1
    if page < 1: page = 1
    if page > total_pages: page = total_pages
    page_items = pages[page - 1] if pages else []
    # Only now do we know exactly which cards are visible on this page — look up ranks for
    # just those instead of the whole vault.
    page_char_ids = [cid for cid, _ in page_items if cid is not None]
    ranks_map = await get_user_ranks_for_cards(viewer_id, page_char_ids)
    page_lines = []
    for cid, line_body in page_items:
        if cid is None:
            page_lines.append(line_body)
            continue
        rank = ranks_map.get(cid)
        rank_str = ""
        if rank == 1:
            rank_str = " 🥇"
        elif rank == 2:
            rank_str = " 🥈"
        elif rank == 3:
            rank_str = " 🥉"
        page_lines.append(f"{line_body}{rank_str}")
    try:
        sender_ent = await client.get_entity(user_id)
        first = getattr(sender_ent, 'first_name', '') or ''
        last = getattr(sender_ent, 'last_name', '') or ''
        fullname = f"{first} {last}".strip() or getattr(sender_ent, 'username', '') or "Hunter"
    except: fullname = "Hunter"
    mention = f"<a href='tg://user?id={user_id}'><b>{escape_html(fullname)}</b></a>"
    vault_icon = "🍁"
    vault_label = "Complete Collection" if is_unlimited_vault else ("Your Vault" if is_own_vault else "Vault")
    vault_owner_doc = await users_catcher_col.find_one({"user_id": user_id}, {"premium_until": 1})
    premium_badge = " 👑" if (not is_unlimited_vault and is_premium_active(vault_owner_doc)) else ""
    output_text = f"{vault_icon} <b>{vault_label}</b> · {mention}{premium_badge}\n"
    if rarity_filter:
        output_text += f"🎚️ <b>Filter:</b> {escape_html(rarity_filter)}\n"
    output_text += f"<i>Page <code>{page}/{total_pages}</code> · Unique <code>{len(owned_ids)}</code> · Total <code>{sum(c['normal'] + c['market'] for c in harem_counts.values())}</code></i>\n\n"
    output_text += "\n".join(page_lines) + "\n"
    footer_lines = []
    if is_own_vault:
        footer_lines.append("💡 <i>/market ဖြင့် ⭐ Star &amp; ကဒ်များ ဝယ်နိုင်ပါတယ်</i>")
    if rarity_filter:
        footer_lines.append("<i>🎚️ Filter active — /hmode ဖြင့် ပြောင်း/ဖျက်နိုင်ပါတယ်</i>")
    if harem_sort != "rarity" and not is_unlimited_vault:
        sort_label = "🕐 Recent" if harem_sort == "recent" else "🔤 A-Z"
        footer_lines.append(f"<i>{sort_label} sort active — /hmode ဖြင့် ပြောင်းနိုင်ပါတယ်</i>")
    if footer_lines:
        output_text += "\n" + "\n".join(footer_lines)
    buttons = []
    # 🩹 FIX: this used to be len(filtered_harem), so setting an /hmode rarity filter (e.g.
    # LEGEND) made the button count drop to just that rarity's count (e.g. 10 instead of the
    # true 100). The button should always reflect the whole vault's total, regardless of
    # whatever rarity filter happens to be active.
    # 🩹 FIX: this used to be len(raw_harem), which counts EVERY raw array element — including
    # any corrupted/invalid entries (non-dict items, or dicts missing "char_id") that a user
    # would never actually see rendered as a card. That silently inflated the count (e.g.
    # showing 19 when only 17 were real, displayable cards), same class of bug as the /stats
    # total-vs-breakdown mismatch. Count only entries that would actually show up as a card.
    total_cards = len(owned_ids) if is_unlimited_vault else sum(
        1 for item in raw_harem if isinstance(item, dict) and item.get("char_id")
    )
    # 🩹 CHANGED (per owner request): trimmed the button stack down to just the inline-query
    # gallery button(s) and Next/Previous pagination — the old "Filterမထားဘူး" / "Rarity
    # Filterချိန်းမယ်" rows are gone from here (still reachable via /hmode directly, which has
    # its own clear-filter button already).
    buttons.append([colored_switch_inline_button(f"Characters🌎 ({total_cards})", query=f"harem.{user_id}", same_peer=True, color="success")])
    # 🎗️ CHANGED (per owner request): this used to switch_inline straight into a flat gallery
    # of every LE card. Now it's a plain callback ("leopen") that opens a paginated menu of
    # DISTINCT LE batch names first (5/page — see _build_le_batch_menu) — each batch name is
    # itself the switch_inline button, scoping the inline gallery to just that batch. Shown on
    # EVERY /harem view regardless of whose vault it is — this isn't scoped to the viewed
    # user's own cards, it's the full GLOBAL roster of everything tagged via /addle. See
    # handle_le_inline_query for what selecting a result actually posts.
    buttons.append([Button.inline("🎗️ Limited Editions", data="leopen")])
    if is_unlimited_vault:
        # Separate gallery for the owner's REAL /collect catches — deliberately not mixed
        # into the unlimited-vault gallery above (see request that prompted this).
        real_catch_count = sum(1 for item in raw_harem if isinstance(item, dict) and item.get("char_id"))
        buttons.append([Button.switch_inline(f"🎯 My Real Catches ({real_catch_count})", query=f"collected.{user_id}", same_peer=True)])
    nav_buttons = []
    if page > 1:
        nav_buttons.append(Button.inline("← Previous", data=f"harem_{page-1}_{user_id}_{viewer_id}"))
    if page < total_pages:
        nav_buttons.append(Button.inline("Next ➡", data=f"harem_{page+1}_{user_id}_{viewer_id}"))
    if nav_buttons:
        buttons.append(nav_buttons)
    if not buttons:
        buttons = None
    fav_card_id = user_doc.get("fav_card")
    if fav_card_id and not is_unlimited_vault and fav_card_id not in raw_owned_id_set:
        # Owner gifted/sold/traded/scrapped away their last copy of this card since favouriting
        # it — stop treating it as the favourite (both for display here and going forward).
        # (Skipped for the unlimited vault — every card is always "owned" there, so a
        # favourite never goes stale.)
        await users_catcher_col.update_one({"user_id": user_id, "fav_card": fav_card_id}, {"$unset": {"fav_card": ""}})
        fav_card_id = None
    fav_media = None
    target_display_id = fav_card_id
    if not target_display_id and owned_ids:
        target_display_id = random.choice(owned_ids)
    if target_display_id:
        fav_card_data = await characters_base_col.find_one({"char_id": target_display_id})
        if fav_card_data:
            fav_media = await get_char_display_media(client, target_display_id, fav_card_data["storage_msg_id"])
    # 🩹 FIX: attach the fav-card photo whenever it fits, for a vault of ANY size — not just
    # when everything happens to fit on a single page. This works now because PAGE_CHAR_BUDGET
    # above guarantees every page (not only page 1) stays within Telegram's 1024-char caption
    # limit, so Next/Previous can keep editing that same photo message's caption on ANY page
    # without ever risking "MediaCaptionTooLongError". The length check below still runs (using
    # page 1's text) as the actual go/no-go signal — if it ever comes out true, every later page
    # is guaranteed true too, by construction.
    CAPTION_SAFE_LIMIT = 1024
    can_attach_photo = bool(fav_media) and utf16_len(output_text) <= CAPTION_SAFE_LIMIT
    try:
        if edit_msg_id:
            try:
                await client.edit_message(chat_id, edit_msg_id, output_text, parse_mode='html', buttons=buttons)
            except errors.MessageNotModifiedError:
                pass
            except Exception:
                # Should never trigger given the PAGE_CHAR_BUDGET guarantee above — kept only
                # as a last-resort safety net (e.g. an unusually long display name we didn't
                # account for) so a genuine edge case degrades to a fresh message instead of an
                # error page.
                try:
                    await client.delete_messages(chat_id, edit_msg_id)
                except Exception:
                    pass
                await client.send_message(chat_id, output_text, parse_mode='html', buttons=buttons)
        else:
            if can_attach_photo:
                async def _send_harem_photo(media):
                    return await client.send_message(chat_id, output_text, file=media, parse_mode='html', buttons=buttons)
                sent = await send_with_char_media(target_display_id, fav_card_data["storage_msg_id"], _send_harem_photo)
                if sent is None:
                    await client.send_message(chat_id, output_text, parse_mode='html', buttons=buttons)
            else:
                await client.send_message(chat_id, output_text, parse_mode='html', buttons=buttons)
    except Exception as main_err:
        try:
            await client.send_message(chat_id, f"❌ <b>Vault Display Error:</b> <code>{escape_html(str(main_err))}</code>", parse_mode='html')
        except: pass

# ---- Inline Query for harem ----
def _rarity_weight_for_sort(rarity_str):
    return rarity_rank_value(rarity_str)

def _inline_media_result(builder, media, cid, title, caption):
    """Builds an inline result for a photo or video. Videos use type='gif' so Telegram
    renders them in the same side-by-side grid as photos, instead of a tall vertical list.
    No title is passed — a visible title/description on a document-type result is what makes
    it render as a text row instead of a clean grid tile on some clients; omitting it (it's
    optional) keeps every tile a plain thumbnail. The name still shows up in the caption once
    a result is actually selected and sent.
    Takes the raw media object (not a full Message) so this works equally with a freshly
    fetched message's .media or a cached one from get_char_display_media[_batch]."""
    if isinstance(media, types.MessageMediaPhoto):
        return builder.photo(file=media, id=cid, text=caption, parse_mode='html')
    return builder.document(
        file=media,
        type='gif',
        id=cid,
        text=caption,
        parse_mode='html'
    )

# ---- harem.<user_id> : shows a user's own vault as an inline gallery.
# collected.<user_id> routes here too (see unified_inline_query_handler) with
# force_real_harem=True, which is what lets the owner's REAL /collect catches be viewed
# separately from their unlimited vault below. ----
async def handle_harem_inline_query(event, query_text, force_real_harem=False):
    builder = event.builder
    # 🩹 ADDED (per owner request): /harem itself has been force-sub-gated all along (see
    # FORCE_SUB_GATED_COMMANDS / force_sub_gate above), but this inline-query entry point —
    # the gallery button on /harem, or anyone typing "@BotUsername harem." by hand — was a
    # complete side door around that gate, since force_sub_gate only listens for
    # events.NewMessage and never sees an InlineQuery at all. Same exemption as the command
    # gate: owner and the bot accounts themselves are never blocked.
    querying_user_id = getattr(event, 'sender_id', None)
    if not querying_user_id:
        try:
            querying_user_id = event.query.user_id
        except Exception:
            querying_user_id = None
    if querying_user_id and querying_user_id != OWNER_ID and querying_user_id not in bot_ids:
        if not await is_force_sub_member(querying_user_id):
            return await event.answer(
                [], cache_time=0,
                switch_pm="🐉🦋 Join our group first to browse harems! Tap below.",
                switch_pm_param="start"
            )
    try:
        target_user_id = int(query_text.split(".", 1)[1])
    except (ValueError, IndexError):
        return await event.answer(
            [], cache_time=0,
            switch_pm="⚠️ Couldn't read that request — try tapping the button again.", switch_pm_param="start"
        )
    user_doc = await users_catcher_col.find_one({"user_id": target_user_id})
    rarity_filter = user_doc.get("rarity_filter") if user_doc else None
    # 🎯 Unlike /harem's own text listing (where the rarity_filter genuinely restricts what's
    # shown), this visual gallery always shows EVERY owned card's photo/video — an active
    # /hmode rarity filter only makes cards of that rarity appear FIRST, as a sort preference
    # rather than an exclusion. Hiding most of someone's actual card art behind a filter here
    # would be a jarring surprise for a feature that's meant purely for browsing.
    filter_tier = classify_rarity(rarity_filter) if rarity_filter else None
    # 👑 Same unlimited-vault rule as /harem itself (see send_paginated_harem) — every
    # character that exists counts as owned, one copy each, computed live so /addchar never
    # needs a sync step. force_real_harem (the "collected." entry point) opts back into the
    # owner's REAL harem array, for viewing their actual personal catches.
    is_unlimited_vault = (target_user_id == OWNER_ID) and not force_real_harem

    if is_unlimited_vault:
        all_chars = await get_all_characters_cached()  # already invalidated on /addchar etc.
        if not all_chars:
            return await event.answer([], cache_time=0, switch_pm="❌ No characters found.", switch_pm_param="start")
        harem_counts = {c["char_id"]: 1 for c in all_chars}
        owned_ids = list(harem_counts.keys())
        total_cards = len(owned_ids)
    else:
        if not user_doc or not user_doc.get("harem"):
            return await event.answer(
                [], cache_time=0,
                switch_pm="📭 This vault is empty!", switch_pm_param="start"
            )
        raw_harem = [item for item in user_doc.get("harem", []) if isinstance(item, dict) and "char_id" in item]
        if not raw_harem:
            return await event.answer([], cache_time=0, switch_pm="📭 No cards found.", switch_pm_param="start")
        harem_counts = {}
        for item in raw_harem:
            cid = item["char_id"]
            harem_counts[cid] = harem_counts.get(cid, 0) + 1
        owned_ids = list(harem_counts.keys())
        total_cards = len(raw_harem)
    db_chars = await characters_base_col.find({"char_id": {"$in": owned_ids}}, {"char_id": 1, "name": 1, "category": 1, "rarity": 1, "storage_msg_id": 1, "artist": 1, "is_limited_edition": 1, "le_name": 1, "_id": 0}).to_list(length=None)
    # Filter-matching cards sort first (priority, not exclusion — see filter_tier note above);
    # within each group, same rarity-weight-then-name order as before.
    db_chars = sorted(db_chars, key=lambda x: (
        0 if (filter_tier and classify_rarity(x.get("rarity", "")) == filter_tier) else 1,
        -_rarity_weight_for_sort(x.get("rarity", "")),
        x.get("name", "").lower()
    ))
    try:
        owner_ent = await bot1.get_entity(target_user_id)
        first = getattr(owner_ent, 'first_name', '') or ''
        last = getattr(owner_ent, 'last_name', '') or ''
        owner_name = f"{first} {last}".strip() or getattr(owner_ent, 'username', '') or f"User {target_user_id}"
    except Exception:
        owner_name = f"User {target_user_id}"
    # ✅ FULL PAGINATION: Telegram hard-caps a single inline answer at 50 results (more raises
    # ResultsTooMuchError) — that's a platform limit, not a choice. To actually show the
    # WHOLE vault instead of freezing at the first 50, we page through db_chars using
    # next_offset: the client automatically re-queries us with the offset once the person
    # scrolls near the end of the current batch, so scrolling reveals every unique card.
    PAGE_SIZE = 50
    try:
        start_idx = int(event.query.offset) if event.query.offset else 0
    except (ValueError, AttributeError):
        start_idx = 0
    page_chars = db_chars[start_idx:start_idx + PAGE_SIZE]
    # Batch-fetch every storage message for this page in ONE call instead of one
    # get_messages() per card. The old per-card loop meant up to 50 sequential API
    # round-trips for a single inline answer — slow enough to risk Telegram's ~10s
    # inline-query timeout and occasional flood-wait, so whichever cards hadn't finished
    # fetching yet (or hit an error) just got silently skipped via `continue`. That's why the
    # set of visible cards looked "random" and capped around ~100 for a 500-card vault: it
    # wasn't a hard limit, it was fetches failing to finish in time, differently each query.
    # get_char_display_media_batch further only hits Telegram for whatever's missing from the
    # in-process cache, so a page anyone's already browsed once is free the next time.
    try:
        media_by_cid = await get_char_display_media_batch(bot1, page_chars)
    except Exception as e:
        print(f"Inline gallery batch fetch error: {e}")
        media_by_cid = {}
    results = []
    for card in page_chars:
        cid = card["char_id"]
        qty = harem_counts.get(cid, 0)
        storage_media = media_by_cid.get(cid)
        if not storage_media:
            continue
        try:
            rarity_no_num = card.get('rarity', '').rsplit(' ', 1)[0]
            owner_line = ("👤 <b>Owner:</b> 🍁 Owner's unlimited vault"
                          if is_unlimited_vault else
                          f"👤 <b>Owner:</b> <a href='tg://user?id={target_user_id}'>{escape_html(owner_name)}</a> (x{qty})")
            le_marker = ""
            if card.get("is_limited_edition"):
                le_emoji = extract_leading_emoji(card.get("le_name") or "")
                if le_emoji:
                    le_marker = f" {le_emoji}"
            caption = (
                f"🍁 <b>{escape_html(card['name'])}{le_marker}</b>\n\n"
                f"🆔 <b>ID:</b> <code>{display_char_id(cid)}</code>\n"
                f"✨ <b>Category:</b> {escape_html(card.get('category', ''))}\n"
                f"🦋 <b>Rarity:</b> {rarity_no_num}\n"
                f"{artist_line(card)}"
                f"{owner_line}"
            )
            results.append(_inline_media_result(builder, storage_media, cid, card['name'], caption))
        except Exception as e:
            print(f"Inline gallery error for {cid}: {e}")
            continue
    next_start = start_idx + PAGE_SIZE
    # 🩹 FIX: next_offset used to be "" (explicit empty string) when there was no further page.
    # That reads fine per the Bot API docs, but Telegram's raw API can reject an
    # explicitly-set-but-empty next_offset outright with NextOffsetInvalidError — the field
    # needs to be OMITTED (None) to signal "no more results", not set to an empty value.
    next_offset = str(next_start) if next_start < len(db_chars) else None
    filter_note = f"🎚️ {classify_rarity(rarity_filter)} shown first" if rarity_filter else "All rarities"
    mode_note = " · 🍁 Unlimited Vault" if is_unlimited_vault else (" · 🎯 Real Collect" if force_real_harem else "")
    try:
        await event.answer(
            results, cache_time=0,
            gallery=True,  # ✅ side-by-side grid, no gaps — instead of a tall one-per-row list
            next_offset=next_offset,
            switch_pm=f"{filter_note} · Total: {total_cards}{mode_note}",
            switch_pm_param="start"
        )
    except errors.NextOffsetInvalidError:
        # Last-resort safety net: whatever Telegram didn't like about next_offset, retry
        # without it (no further-page continuation) so the person still sees these results
        # instead of the whole inline query silently failing.
        await event.answer(
            results, cache_time=0,
            gallery=True,
            switch_pm=f"{filter_note} · Total: {total_cards}{mode_note}",
            switch_pm_param="start"
        )

# ---- dex.<category> : shows every character (caught or not) in a series, image gallery style ----
# ---- le.<batch_index> : gallery of every character in ONE Limited Edition batch (see the
# "🎗️ Limited Editions" button on every /harem view, which opens a paginated menu of batch
# names first — each batch name button is what actually carries this query). batch_index is a
# plain position into get_le_batch_names()'s sorted list, NOT the batch name itself (LE names
# are arbitrary owner-typed text, not safe to embed raw in a query string). Mirrors
# handle_harem_inline_query's shape on purpose (per owner request): gallery=True grid, real
# next_offset pagination instead of a hard 50-card cutoff, switch_pm summary footer. Not
# scoped to any one vault — every result here is the same for whoever searches, except the
# "already own this?" line and, implicitly, who ends up as the mention on the posted message
# (the searcher, via Telegram's normal inline-result-author behaviour). ----
async def handle_le_inline_query(event, query_text):
    builder = event.builder
    try:
        batch_idx = int(query_text.split(".", 1)[1])
    except (ValueError, IndexError):
        return await event.answer(
            [], cache_time=0,
            switch_pm="⚠️ Couldn't read that request — try tapping the button again.", switch_pm_param="start"
        )
    batches = await get_le_batch_names()
    if batch_idx < 0 or batch_idx >= len(batches):
        return await event.answer(
            [], cache_time=0,
            switch_pm="⚠️ That batch no longer exists — try the menu again.", switch_pm_param="start"
        )
    batch_name = batches[batch_idx][0]
    batch_name_display = bookend_emoji_name(batch_name)  # 🩹 display-only — the raw batch_name below is still used for the exact-match filter, never the bookended version
    le_chars = [
        c for c in await get_all_characters_cached()
        if c.get("is_limited_edition") and (c.get("le_name") or "?") == batch_name
    ]
    if not le_chars:
        return await event.answer([], cache_time=0, switch_pm="📭 This batch is empty.", switch_pm_param="start")
    le_chars.sort(key=lambda x: (x.get("name") or "").lower())

    user_id = getattr(event, 'sender_id', None)
    if not user_id:
        try:
            user_id = event.query.user_id
        except Exception:
            user_id = None
    owned_ids = set()
    if user_id:
        user_doc = await users_catcher_col.find_one({"user_id": user_id})
        if user_doc:
            owned_ids = {c.get("char_id") for c in user_doc.get("harem", []) if isinstance(c, dict) and c.get("char_id")}

    # Same offset-based full pagination as handle_harem_inline_query — see that function's
    # comment for why this beats a hard 50-result cutoff.
    PAGE_SIZE = 50
    try:
        start_idx = int(event.query.offset) if event.query.offset else 0
    except (ValueError, AttributeError):
        start_idx = 0
    page_chars = le_chars[start_idx:start_idx + PAGE_SIZE]
    try:
        media_by_cid = await get_char_display_media_batch(bot1, page_chars)
    except Exception as e:
        print(f"LE inline gallery batch fetch error: {e}")
        media_by_cid = {}
    owner_mention = await get_html_mention(event, OWNER_ID)
    results = []
    for card in page_chars:
        cid = card["char_id"]
        storage_media = media_by_cid.get(cid)
        if not storage_media:
            continue
        owned = cid in owned_ids
        # 🩹 CHANGED (per owner report — "reads like AI wrote it, even the emoji"): dropped the
        # exclamation-heavy hype phrasing and celebration emoji, matching /check's own plain,
        # factual label:value style instead of inventing a separate voice for this one gallery.
        caption = (
            f"🎗️ <b>Limited Edition</b>\n\n"
            f"🍬 <b>Name:</b> <code>{escape_html(card['name'])}</code>\n"
            f"🆔 <b>ID:</b> <code>{display_char_id(cid)}</code>\n"
            f"🦋 <b>Rarity:</b> {card.get('rarity', '')}\n"
            f"🎡 <b>Event:</b> <code>{escape_html(card.get('event', 'General'))}</code>\n"
            f"🏷️ <b>Batch:</b> <code>{escape_html(batch_name_display)}</code>\n"
            f"{artist_line(card)}"
            f"👤 <b>Ownership:</b> {'Owned' if owned else 'Not owned'}\n\n"
            f"👑 <b>Buy from:</b> {owner_mention}"
        )
        results.append(_inline_media_result(builder, storage_media, cid, card['name'], caption))
    next_start = start_idx + PAGE_SIZE
    next_offset = str(next_start) if next_start < len(le_chars) else None
    try:
        await event.answer(
            results, cache_time=0,
            gallery=True,
            next_offset=next_offset,
            switch_pm=f"🦋 {batch_name_display} · Total: {len(le_chars)}",
            switch_pm_param="start"
        )
    except errors.NextOffsetInvalidError:
        await event.answer(
            results, cache_time=0,
            gallery=True,
            switch_pm=f"🦋 {batch_name_display} · Total: {len(le_chars)}",
            switch_pm_param="start"
        )

async def handle_dex_inline_query(event, query_text):
    builder = event.builder
    cat_name = query_text.split(".", 1)[1].strip() if "." in query_text else ""
    if not cat_name:
        return await event.answer([], cache_time=0)
    all_categories = await get_all_categories_cached()
    matched_cat = next((c for c in all_categories if c and c.lower() == cat_name.lower()), None)
    if not matched_cat:
        return await event.answer([], cache_time=0)
    user_id = getattr(event, 'sender_id', None)
    if not user_id:
        try:
            user_id = event.query.user_id
        except Exception:
            user_id = None
    owned_ids = set()
    if user_id:
        user_doc = await users_catcher_col.find_one({"user_id": user_id})
        if user_doc:
            owned_ids = {c.get("char_id") for c in user_doc.get("harem", []) if isinstance(c, dict) and c.get("char_id")}
    cat_chars = [c for c in await get_all_characters_cached() if c.get("category") == matched_cat]
    cat_chars.sort(key=lambda x: (-_rarity_weight_for_sort(x.get("rarity", "")), x.get("name", "").lower()))
    results = []
    page_chars = cat_chars[:50]
    # Batch-fetch (see handle_harem_inline_query for why this matters — one call instead of
    # up to 50 sequential ones); further reduced to only-the-misses by the cache inside
    # get_char_display_media_batch.
    try:
        media_by_cid = await get_char_display_media_batch(bot1, page_chars)
    except Exception as e:
        print(f"Dex inline gallery batch fetch error: {e}")
        media_by_cid = {}
    for card in page_chars:
        cid = card["char_id"]
        owned = cid in owned_ids
        storage_media = media_by_cid.get(cid)
        if not storage_media:
            continue
        try:
            caption = (
                f"Character Name🐇<b>{escape_html(card['name'])}</b>\n"
                f"ID <code>{display_char_id(cid)}</code>\n"
                f"🏞️<b>Category:</b> {escape_html(matched_cat)}\n"
                f" <b>Rarity:</b> {card.get('rarity', '')}\n"
                f"{artist_line(card)}"
                f"{'✅ You caught this!' if owned else '❓ Not caught yet'}"
            )
            results.append(_inline_media_result(builder, storage_media, cid, card['name'], caption))
        except Exception as e:
            print(f"Dex inline gallery error for {cid}: {e}")
            continue
    await event.answer(results, cache_time=0)

# ---- /obtain <name> : lets the /who reveal button's switch_inline query actually send a
# message. Accepts legacy "/collect " and "/catch " prefixes too, so any switch_inline buttons
# already sent to chats before the /collect → /obtain rename still work — but always emits the
# new "/obtain <name>" text going forward. ----
async def handle_collect_inline_query(event, query_text):
    for legacy_prefix in ("/obtain ", "/collect ", "/catch "):
        if query_text.startswith(legacy_prefix):
            prefix = legacy_prefix
            break
    else:
        prefix = "/fucck "
    char_name = query_text[len(prefix):].strip()
    if not char_name:
        return await event.answer([], cache_time=0)
    builder = event.builder
    result = builder.article(
        title=f"🦢 Obtain {char_name}!",
        description="Tap here to send this obtain command",
        text=f"/obtain {char_name}",
        id=f"collect_{abs(hash(char_name)) % (10 ** 12)}"
    )
    await event.answer([result], cache_time=0)

@bot1.on(events.InlineQuery)
async def unified_inline_query_handler(event):
    query_text = (event.text or "").strip()
    if query_text.startswith("harem."):
        await handle_harem_inline_query(event, query_text)
    elif query_text.startswith("collected."):
        # Owner's REAL /collect catches, kept separate from their unlimited vault above —
        # see handle_harem_inline_query and send_paginated_harem's "📥 My Real Catches" button.
        await handle_harem_inline_query(event, "harem." + query_text[len("collected."):], force_real_harem=True)
    elif query_text.startswith("le."):
        await handle_le_inline_query(event, query_text)
    elif query_text == "le":
        # Bare "le" (no batch index) shouldn't normally happen anymore — every LE button now
        # carries a specific batch index — but answer gracefully instead of erroring if it
        # ever does (e.g. someone typing it by hand).
        await event.answer([], cache_time=0, switch_pm="🎗️ Use the Limited Editions button on /harem to browse by batch.", switch_pm_param="start")
    elif query_text.startswith("dex."):
        await handle_dex_inline_query(event, query_text)
    elif query_text.startswith(("/obtain ", "/collect ", "/catch ")):
        await handle_collect_inline_query(event, query_text)
    else:
        await event.answer([], cache_time=0)

# ---- Callback for view_all_cards ----
@bot1.on(events.CallbackQuery(pattern=r'^view_all_cards_(\d+)$'))
async def view_all_cards_callback(event):
    user_id = int(event.pattern_match.group(1))
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    if not user_doc or not user_doc.get("harem"):
        return await event.answer("📭 This vault is empty!", alert=True)
    raw_harem = user_doc.get("harem", [])
    rarity_filter = user_doc.get("rarity_filter")
    filtered_harem = []
    if rarity_filter:
        for item in raw_harem:
            if isinstance(item, dict) and classify_rarity(item.get("rarity")) == classify_rarity(rarity_filter):
                filtered_harem.append(item)
    else:
        filtered_harem = raw_harem
    if not filtered_harem:
        return await event.answer("❌ No cards found with current filter!", alert=True)
    unique_cards = {}
    for item in filtered_harem:
        if isinstance(item, dict) and "char_id" in item:
            cid = item["char_id"]
            if cid not in unique_cards:
                unique_cards[cid] = {"count": 0, "item": item}
            unique_cards[cid]["count"] += 1
    if not unique_cards:
        return await event.answer("❌ No valid cards found!", alert=True)
    card_ids = list(unique_cards.keys())
    page = 1
    per_page = 5
    total_pages = (len(card_ids) + per_page - 1) // per_page
    await send_gallery_page(event, user_id, card_ids, unique_cards, page, per_page, total_pages)

async def send_gallery_page(event, user_id, card_ids, unique_cards, page, per_page, total_pages):
    start_idx = (page - 1) * per_page
    end_idx = min(start_idx + per_page, len(card_ids))
    page_card_ids = card_ids[start_idx:end_idx]
    try:
        owner_ent = await bot1.get_entity(user_id)
        first = getattr(owner_ent, 'first_name', '') or ''
        last = getattr(owner_ent, 'last_name', '') or ''
        owner_name = f"{first} {last}".strip() or getattr(owner_ent, 'username', '') or "Hunter"
    except Exception:
        owner_name = "Hunter"
    gallery_text = f"👀 <b>{escape_html(owner_name)}'s Card Gallery</b>\n"
    filter_doc = await users_catcher_col.find_one({"user_id": user_id})
    rarity_filter = filter_doc.get("rarity_filter") if filter_doc else None
    if rarity_filter:
        gallery_text += f"🔍 <b>Filter:</b> {rarity_filter}\n"
    gallery_text += f"📑 <b>Page {page}/{total_pages}</b> · <b>Total Cards:</b> {len(card_ids)}\n\n"
    gallery_text += f"<i>✨ Tap a card below to see its details</i>\n"
    buttons = []
    row = []
    for idx, cid in enumerate(page_card_ids):
        card_data = await characters_base_col.find_one({"char_id": cid})
        if not card_data:
            continue
        card_name = card_data.get("name", "Unknown")
        emoji = card_data.get("rarity", RARITY_DEFAULT_EMOJI)[0] if card_data.get("rarity") else RARITY_DEFAULT_EMOJI
        count = unique_cards[cid]["count"]
        label = f"{emoji} {card_name} x{count}"
        row.append(Button.inline(label, data=f"card_detail_{cid}_{user_id}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    nav_row = []
    if page > 1:
        nav_row.append(Button.inline("⬅️ Prev", data=f"gallery_page_{page-1}_{user_id}"))
    if page < total_pages:
        nav_row.append(Button.inline("Next ➡️", data=f"gallery_page_{page+1}_{user_id}"))
    if nav_row:
        buttons.append(nav_row)
    buttons.append([Button.inline("🔙 Back to Vault", data=f"harem_1_{user_id}")])
    await event.edit(gallery_text, parse_mode='html', buttons=buttons)

@bot1.on(events.CallbackQuery(pattern=r'^gallery_page_(\d+)_(\d+)$'))
async def gallery_page_callback(event):
    page = int(event.pattern_match.group(1))
    user_id = int(event.pattern_match.group(2))
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    if not user_doc or not user_doc.get("harem"):
        return await event.answer("📭 This vault is empty!", alert=True)
    raw_harem = user_doc.get("harem", [])
    rarity_filter = user_doc.get("rarity_filter")
    filtered_harem = []
    if rarity_filter:
        for item in raw_harem:
            if isinstance(item, dict) and classify_rarity(item.get("rarity")) == classify_rarity(rarity_filter):
                filtered_harem.append(item)
    else:
        filtered_harem = raw_harem
    unique_cards = {}
    for item in filtered_harem:
        if isinstance(item, dict) and "char_id" in item:
            cid = item["char_id"]
            if cid not in unique_cards:
                unique_cards[cid] = {"count": 0, "item": item}
            unique_cards[cid]["count"] += 1
    card_ids = list(unique_cards.keys())
    per_page = 5
    total_pages = (len(card_ids) + per_page - 1) // per_page
    await send_gallery_page(event, user_id, card_ids, unique_cards, page, per_page, total_pages)

@bot1.on(events.CallbackQuery(pattern=r'^card_detail_([a-zA-Z0-9_]+)_(\d+)$'))
async def card_detail_callback(event):
    char_id = event.pattern_match.group(1)
    if isinstance(char_id, bytes):
        char_id = char_id.decode('utf-8')
    user_id = int(event.pattern_match.group(2))
    card = await characters_base_col.find_one({"char_id": char_id})
    if not card:
        return await event.answer("❌ Card not found!", alert=True)
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    if not user_doc:
        return await event.answer("❌ User not found!", alert=True)
    harem = user_doc.get("harem", [])
    count = 0
    for item in harem:
        if isinstance(item, dict) and item.get("char_id") == char_id:
            count += 1
    try:
        owner_ent = await bot1.get_entity(user_id)
        first = getattr(owner_ent, 'first_name', '') or ''
        last = getattr(owner_ent, 'last_name', '') or ''
        owner_name = f"{first} {last}".strip() or getattr(owner_ent, 'username', '') or "Hunter"
    except Exception:
        owner_name = "Hunter"
    owner_mention = f"<a href='tg://user?id={user_id}'><b>{escape_html(owner_name)}</b></a>"
    detail_text = (
        f"🎴 <b>{escape_html(card['name'])}</b>\n\n"
        f"👤 <b>Owner:</b> {owner_mention}\n"
        f"🆔 <b>ID:</b> <code>{display_char_id(card['char_id'])}</code>\n"
        f"🫧 <b>Category:</b> <code>{escape_html(card['category'])}</code>\n"
        f"🦋 <b>Rarity:</b> {strip_rarity_number(card['rarity'])}\n"
        f"{artist_line(card)}"
        f"📦 <b>Owned:</b> <code>{count} copies</code>\n"
        f"💠 <b>Value:</b> <code>{format_vlt_plain(card['currency_value'])}</code>\n\n"
        f"<i>✨ Tap the image to close</i>"
    )

    async def _send_detail(media):
        return await bot1.send_file(
            event.chat_id,
            media,
            caption=detail_text,
            parse_mode='html',
            reply_to=event.message_id
        )

    media_preview = await get_char_display_media(bot1, card["char_id"], card["storage_msg_id"])
    if media_preview:
        await event.answer("🃏 Loading card...", alert=False)
        sent = await send_with_char_media(card["char_id"], card["storage_msg_id"], _send_detail)
        if sent:
            await event.answer("✅ Card sent!", alert=True)
        else:
            await event.edit(detail_text, parse_mode='html')
    else:
        await event.edit(detail_text, parse_mode='html')

# ---- UNIFIED CALLBACK HANDLER (extended) ----
@bot1.on(events.CallbackQuery)
async def unified_callback_handler(event):
    if not event.data: return
    try: data_str = event.data.decode('utf-8')
    except: return
    data_parts = data_str.split('_')
    action_type = data_parts[0]

    if action_type == "harem":
        page = int(data_parts[1])
        target_user_id = int(data_parts[2])
        viewer_id = event.sender_id
        await send_paginated_harem(bot1, event.chat_id, target_user_id, page=page, edit_msg_id=event.message_id, viewer_id=viewer_id)
    elif action_type == "ach":
        page = int(data_parts[1])
        target_user_id = int(data_parts[2])
        if event.sender_id != target_user_id:
            return await event.answer("⚠️ These are not your achievements!", alert=True)
        mention = await get_html_mention(event, target_user_id)
        await send_achievements_page(bot1, event.chat_id, target_user_id, mention, page=page, edit_msg_id=event.message_id)
    elif action_type == "tr":
        sub_action = data_parts[1]
        sender_id = int(data_parts[2])
        target_id = int(data_parts[3])
        if sub_action == "canc":
            if event.sender_id in [sender_id, target_id]:
                await event.answer("❌ Transaction cancelled")
                await event.edit(f"❌ <b>Trade contract voided.</b>", parse_mode='html')
            else: await event.answer("⚠️ You are not involved in this trade.", alert=True)
            return
        if sub_action == "conf":
            if event.sender_id != target_id: return await event.answer("⚠️ You are not the recipient.", alert=True)
            if not claim_single_tap(event):
                return await event.answer("⏳ Already processing...", alert=False)
            await event.answer("⚡ Finalising trade...")
            my_char_id, their_char_id = data_parts[4], data_parts[5]
            s_doc = await users_catcher_col.find_one({"user_id": sender_id})
            t_doc = await users_catcher_col.find_one({"user_id": target_id})
            s_harem = s_doc.get("harem", []) if s_doc else []
            t_harem = t_doc.get("harem", []) if t_doc else []
            s_item = next((x for x in s_harem if isinstance(x, dict) and x.get("char_id") == my_char_id and x.get("status") != "market"), None)
            t_item = next((x for x in t_harem if isinstance(x, dict) and x.get("char_id") == their_char_id and x.get("status") != "market"), None)
            if not s_item or not t_item: return await event.edit(f"❌ <b>Trade failed – card unavailable.</b>", parse_mode='html')
            # 🩹 FIX: same atomic swap as marketplace-buy above — $pull exactly one matching
            # copy off each side, then $push the new one on, instead of read-modify-write-back
            # the whole harem array (which raced against any other concurrent change on either
            # account and could silently duplicate or erase cards).
            await remove_one_harem_copy(sender_id, my_char_id, s_item.get("status", "vault"))
            await remove_one_harem_copy(target_id, their_char_id, t_item.get("status", "vault"))
            await users_catcher_col.update_one(
                {"user_id": sender_id},
                {"$push": {"harem": {"char_id": their_char_id, "caught_date": time.time(), "rarity": t_item.get("rarity", "Unknown"), "status": "vault"}}}
            )
            await users_catcher_col.update_one(
                {"user_id": target_id},
                {"$push": {"harem": {"char_id": my_char_id, "caught_date": time.time(), "rarity": s_item.get("rarity", "Unknown"), "status": "vault"}}}
            )
            s_doc_after = await users_catcher_col.find_one({"user_id": sender_id}, {"harem": 1})
            t_doc_after = await users_catcher_col.find_one({"user_id": target_id}, {"harem": 1})
            await clear_stale_favorite(sender_id, my_char_id, (s_doc_after or {}).get("harem", []))
            await clear_stale_favorite(target_id, their_char_id, (t_doc_after or {}).get("harem", []))
            await event.edit(f"🤝 <b>Trade concluded successfully!</b>", parse_mode='html')
    # cardjoin / hilo branches — REMOVED (2026-08, per owner request: all casino games except
    # /slot are gone; cardgame's lobby-join and HI-LO's guess-resolution used to live here).
    # ==========================================
    # 🎁 Gift-history pagination (from /myinfo, /profile)
    # ==========================================
    elif action_type == "gh":
        owner_user_id = int(data_parts[1])
        page = int(data_parts[2])
        if event.sender_id != owner_user_id:
            return await event.answer("⚠️ ဒါက သင့်ရဲ့ history မဟုတ်ပါ။", alert=True)
        text, buttons = await render_gift_history_page(owner_user_id, page)
        try:
            await event.edit(text, parse_mode='html', buttons=buttons)
        except Exception:
            pass
        await event.answer()

    # ==========================================
    # 📊 Profile main/stats page toggle
    # ==========================================
    elif action_type == "pf":
        sub_action = data_parts[1]
        owner_user_id = int(data_parts[2])
        if event.sender_id != owner_user_id:
            return await event.answer("⚠️ ဒါက သင့်ရဲ့ Profile မဟုတ်ပါ။", alert=True)
        mention = await get_html_mention(event, owner_user_id)
        if sub_action == "stats":
            text, buttons = await render_profile_stats_page(owner_user_id, mention)
        else:
            text, buttons = await render_profile_main_page(event, owner_user_id, mention)
        try:
            await event.edit(text, parse_mode='html', buttons=buttons)
        except Exception:
            pass
        await event.answer()

    # ==========================================
    # 🏆 Leaderboard tab/page switching
    # ==========================================
    elif action_type == "lb":
        mode = data_parts[1]
        page = int(data_parts[2])
        text, buttons = await render_leaderboard_page(mode, page)
        try:
            await event.edit(text, parse_mode='html', buttons=buttons)
        except Exception:
            pass
        await event.answer()

    # ==========================================
    # 🎗️ Limited Edition batch-name menu (see /harem's "🎗️ Limited Editions" button)
    # ==========================================
    elif action_type == "leopen":
        # 🩹 ALWAYS a fresh message, never event.edit — this button lives on /harem's own
        # message (often a photo with the fav card), and overwriting THAT would destroy the
        # harem view the person just opened it from.
        batches = await get_le_batch_names()
        await event.answer()
        if not batches:
            return await bot1.send_message(event.chat_id, "🎗️ <b>Limited Edition ကတ် မရှိသေးပါဘူး။</b>", parse_mode='html')
        text, buttons = _build_le_batch_menu(batches, 1)
        await bot1.send_message(event.chat_id, text, parse_mode='html', buttons=buttons)

    elif action_type == "lepg":
        page = int(data_parts[1])
        batches = await get_le_batch_names()
        if not batches:
            return await event.answer("🎗️ Limited Edition ကတ် မရှိသေးပါဘူး။", alert=True)
        text, buttons = _build_le_batch_menu(batches, page)
        try:
            await event.edit(text, parse_mode='html', buttons=buttons)
        except errors.MessageNotModifiedError:
            pass
        except Exception:
            pass
        await event.answer()

    # ==========================================
    # ⭐ /fav confirm / cancel callbacks
    # ==========================================
    elif action_type == "fav":
        if len(data_parts) < 4:
            return await event.answer("❌ Invalid request.", alert=True)
        sub_action = data_parts[1]
        target_user_id = int(data_parts[2])
        char_id = "_".join(data_parts[3:])
        if event.sender_id != target_user_id:
            return await event.answer("⚠️ ဒါက သင့်ရွေးချယ်မှုမဟုတ်ပါ။", alert=True)
        if sub_action == "cancel":
            await event.answer("❌ Cancelled.")
            try:
                await event.edit("❌ <b>Favourite ထည့်ခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။</b>", parse_mode='html', buttons=None)
            except Exception:
                pass
            return
        if sub_action == "confirm":
            card = await characters_base_col.find_one({"char_id": char_id})
            if not card:
                return await event.answer("❌ ဒီကတ်ကို ရှာမတွေ့ပါ။", alert=True)
            user_doc = await users_catcher_col.find_one({"user_id": target_user_id})
            user_harem = user_doc.get("harem", []) if user_doc else []
            owns_card = any(
                isinstance(x, dict) and (x.get("char_id") or "").upper() == char_id.upper()
                for x in user_harem
            )
            if not owns_card:
                return await event.answer("❌ You don't own this card anymore!", alert=True)
            await users_catcher_col.update_one({"user_id": target_user_id}, {"$set": {"fav_card": card["char_id"]}})
            await event.answer("✅ Favourite card updated!")
            try:
                await event.edit(
                    f"🍬 <b>{escape_html(card['name'])}</b> (<code>{display_char_id(card['char_id'])}</code>) ကို "
                    f"favourite ကတ်အဖြစ် သတ်မှတ်လိုက်ပြီ! ✨ /harem မှာ ဒီကတ်ပေါ်လာမယ်။",
                    parse_mode='html', buttons=None
                )
            except Exception:
                pass
            return
# 🛡️ Guard Bot (bot3) also gets this SAME handler — needed because whichever bot actually
# SENT a message owns the callback taps on it. This used to matter for /cardgame and /hilo,
# whose outgoing messages in the force-join room were sent via bot3, so their button taps
# arrived at bot3, not bot1 — both are removed now (2026-08, per owner request), so this
# dual-registration is currently a harmless no-op for bot3, but kept in place in case a future
# feature sends any of the remaining action_type messages (harem, tr, gh, pf, etc.) via bot3.
if bot3 is not None:
    bot3.add_event_handler(unified_callback_handler, events.CallbackQuery)

# ==========================================
# 🏆 LEADERBOARD (Daily / All-Time, paginated)
# ==========================================
LEADERBOARD_PAGE_SIZE = 15
LB_MEDALS = {0: "🥇", 1: "🥈", 2: "🥉"}

async def render_leaderboard_page(mode, page):
    """Generate leaderboard text and buttons for a given mode and page."""
    today_str = datetime.now(TZ).strftime("%Y-%m-%d") if mode == "daily" else None
    data = await get_cached_leaderboard(mode, today_str)
    
    total = len(data)
    start = page * LEADERBOARD_PAGE_SIZE
    page_rows = data[start:start + LEADERBOARD_PAGE_SIZE]
    field_map = {"daily": "daily_catches", "all": "total_caught"}
    title_map = {"daily": "📅 <b>Today's Top Catchers</b>", "all": "🏆 <b>All-Time Top Catchers</b>"}
    field = field_map.get(mode, "total_caught")
    title = title_map.get(mode, title_map["all"])
    
    if not page_rows:
        body = "<i>Nobody's caught anything yet — be the first! 🐟</i>" if page == 0 else "<i>No more entries.</i>"
    else:
        lines = []
        for i, row in enumerate(page_rows):
            rank = start + i
            medal = LB_MEDALS.get(rank, f"<code>#{rank + 1}</code>")
            name = escape_html(clean_display_name(row.get("fullname"), fallback=f"User {row.get('user_id')}"))
            val = row.get(field, 0)
            lines.append(f"{medal}  {name} — <code>{val}</code>")
        body = "\n".join(lines)
    
    text = f"{title}\n<blockquote>{body}</blockquote>"
    
    # Tab buttons
    tab_rows = [
        [
            Button.inline(("🔘 " if mode == "daily" else "") + "📅 Daily", data="lb_daily_0"),
            Button.inline(("🔘 " if mode == "all" else "") + "🏆 All-Time", data="lb_all_0")
        ]
    ]
    
    # Navigation buttons
    nav_row = []
    if page > 0:
        nav_row.append(Button.inline("◀ Prev", data=f"lb_{mode}_{page - 1}"))
    if start + LEADERBOARD_PAGE_SIZE < total:
        nav_row.append(Button.inline("Next ▶", data=f"lb_{mode}_{page + 1}"))
    
    buttons = list(tab_rows)
    if nav_row:
        buttons.append(nav_row)
    
    return text, buttons

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.](leaderboard|lb)(?:@\w+)?$', 'bot1')))
async def leaderboard_handler(event):
    """Handle /leaderboard and /lb commands."""
    text, buttons = await render_leaderboard_page("all", 0)
    await event.reply(text, parse_mode='html', buttons=buttons)

# ---- Callback handler for leaderboard navigation ----
@bot1.on(events.CallbackQuery(pattern=r'^lb_(daily|all)_(\d+)$'))
async def leaderboard_callback_handler(event):
    """Handle leaderboard page/tab switching via inline buttons."""
    mode = event.pattern_match.group(1)  # "daily" or "all"
    if isinstance(mode, bytes):
        mode = mode.decode('utf-8')
    page = int(event.pattern_match.group(2))
    
    text, buttons = await render_leaderboard_page(mode, page)
    
    try:
        await event.edit(text, parse_mode='html', buttons=buttons)
        await event.answer("✅ Updated!")
    except errors.MessageNotModifiedError:
        # Message content hasn't changed, just acknowledge the click
        await event.answer()
    except Exception as e:
        await event.answer(f"❌ Error: {e}", alert=True)

LEADERBOARD_CACHE_TTL = 60  # a full minute of staleness is fine for a top-50 board

async def get_cached_leaderboard(mode, today_str=None):
    """mode: 'daily' or 'all'. Leaderboard staleness of up to a minute is harmless, so this
    caches the whole top-50 snapshot instead of hitting Mongo on every /leaderboard tap."""
    cache_key = f"cache:leaderboard:{mode}:{today_str or 'x'}"
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception as e:
        print(f"⚠️ Redis leaderboard cache error: {e}")
        # Redis မရရင် DB ကို တိုက်ရိုက်ရှာမယ်
    
    # Fetch from database
    if mode == "daily":
        today_start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        cursor = users_catcher_col.find(
            {"last_catch_date": {"$gte": today_start}, "daily_catches": {"$gt": 0}},
            {"_id": 0, "user_id": 1, "fullname": 1, "daily_catches": 1}
        ).sort("daily_catches", -1).limit(50)
    else:
        cursor = users_catcher_col.find(
            {"total_caught": {"$gt": 0}},
            {"_id": 0, "user_id": 1, "fullname": 1, "total_caught": 1}
        ).sort("total_caught", -1).limit(50)
    
    data = await cursor.to_list(length=50)
    
    # Try to cache it
    try:
        await redis_client.setex(cache_key, LEADERBOARD_CACHE_TTL, json.dumps(data, default=str))
    except Exception as e:
        print(f"⚠️ Redis leaderboard cache write error: {e}")
    
    return data

# ==========================================
# 📊 PROFILE
# ==========================================
async def render_profile_main_page(event, user_id, mention):
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    total_caught = user_doc.get("total_caught", 0) if user_doc else 0
    star_balance = user_doc.get("star_balance", 0) if user_doc else 0
    vlt_balance = user_doc.get("vlt_balance", 0) if user_doc else 0
    streak = user_doc.get("daily_streak", 0) if user_doc else 0
    referrals = user_doc.get("referral_count", 0) if user_doc else 0
    badge_count = len(user_doc.get("achievements", [])) if user_doc else 0
    raw_harem = user_doc.get("harem", []) if user_doc else []
    fav_card_id = user_doc.get("fav_card") if user_doc else None
    total_gifted = user_doc.get("total_gifted", 0) if user_doc else 0
    total_gift_received = user_doc.get("total_gift_received", 0) if user_doc else 0
    unique_ids = {item["char_id"] for item in raw_harem if isinstance(item, dict) and "char_id" in item}
    base_total = await characters_base_col.count_documents({})
    unique_owned = len(unique_ids)
    rank = await users_catcher_col.count_documents({"total_caught": {"$gt": total_caught}}) + 1
    chat_rank = None
    if not event.is_private:
        chat_id_str = str(event.chat_id)
        my_chat_catches = (user_doc.get("group_catches", {}) if user_doc else {}).get(chat_id_str, 0)
        if my_chat_catches > 0:
            chat_rank = await users_catcher_col.count_documents({f"group_catches.{chat_id_str}": {"$gt": my_chat_catches}}) + 1
    fav_body = ""
    if fav_card_id and fav_card_id not in unique_ids:
        # Owner no longer has any copy of this card — clear the stale favourite.
        await users_catcher_col.update_one({"user_id": user_id, "fav_card": fav_card_id}, {"$unset": {"fav_card": ""}})
        fav_card_id = None
    if fav_card_id:
        fav_doc = await characters_base_col.find_one({"char_id": fav_card_id})
        if fav_doc:
            fav_body = f"⭐ <code>{escape_html(fav_doc.get('name'))}</code>　<code>{fav_card_id}</code>"
    pct = (unique_owned / base_total * 100) if base_total else 0
    premium_line = ""
    if is_premium_active(user_doc):
        expiry_str = datetime.fromtimestamp(user_doc["premium_until"], TZ).strftime("%Y-%m-%d")
        premium_line = f"👑 <b>{sc('Premium')}</b> — <code>{expiry_str}</code> အထိ\n"
    giver_title = get_giver_title(total_gifted)
    receiver_title = get_receiver_title(total_gift_received)
    giver_rank_str = f" ({giver_title[0]} {giver_title[1]})" if giver_title else ""
    receiver_rank_str = f" ({receiver_title[0]} {receiver_title[1]})" if receiver_title else ""
    cosmetic_prefix = get_equipped_cosmetics_prefix(user_doc)
    cosmetic_line = f"✨ <b>{escape_html(cosmetic_prefix)}</b>\n" if cosmetic_prefix else ""
    frame_border = get_equipped_frame_border(user_doc)

    # 🎮 HUD title bar — frame cosmetic (if equipped) bookends the small-caps title.
    title_text = sc("𖤐 collector database")
    header_top = (
        f"╭─〔 {frame_border} {title_text} {frame_border} 〕─╮"
        if frame_border else f"╭─〔 {title_text} 〕─╮"
    )

    rank_line = f"🏆 <b>{sc('Global')}</b> <code>#{rank}</code>"
    if chat_rank:
        rank_line += f"　🍁 <b>{sc('Chat')}</b> <code>#{chat_rank}</code>"

    # 🧠 Trivia standing — this is the REAL "current position" (not an invented XP level): among
    # everyone who has ever scored a trivia_points > 0, where does this user currently rank?
    # Only shown once the user has actually answered a trivia question — no line at all (rather
    # than "#0/1600") for someone who's never played, same as the Favourite section below only
    # appearing once a favourite is actually set.
    trivia_points = user_doc.get("trivia_points", 0) if user_doc else 0
    trivia_line = ""
    if trivia_points > 0:
        _, trivia_rank_name, _, _ = get_trivia_rank_info(trivia_points)
        trivia_position, trivia_total = await get_trivia_position(trivia_points)
        trivia_line = f"\n🧠 <b>{sc('Trivia')}</b> <code>#{trivia_position}/{trivia_total}</code>　{trivia_rank_name}"

    # ── Section stack: (small-caps header, body). Whichever section ends up LAST (Favourite
    # only shows up if one is set) gets the "╰─...╯" closing corner instead of "├─...╮", so the
    # box always visually seals itself shut regardless of which sections are present. ──
    sections = [
        (sc("Collection log"), (
            f"🎒 <b>{sc('Caught')}</b> <code>{total_caught}</code>\n"
            f"🧬 <b>{sc('Unique')}</b> <code>{unique_owned}/{base_total}</code> (<code>{pct:.1f}%</code>)\n"
            f"<code>{build_progress_bar(unique_owned, base_total)}</code>"
        )),
        (sc("Vault"), (
            f"⭐ <code>{format_star_plain(star_balance)}</code>\n"
            f"💠 <code>{format_vlt_plain(vlt_balance)}</code>"
        )),
        (sc("Records"), (
            f"🏅 <code>{badge_count}/{len(ACHIEVEMENTS)}</code> {sc('Badges')}\n"
            f"🎁 <code>{total_gifted}</code> {sc('Gifted')}{giver_rank_str}　🎀 <code>{total_gift_received}</code> {sc('Received')}{receiver_rank_str}\n"
            f"🔥 <code>{streak}d</code> {sc('Streak')}　🤝 <code>{referrals}</code> {sc('Referrals')}"
        )),
    ]
    if fav_body:
        sections.append((f"★ {sc('Favourite')}", fav_body))

    section_blocks = []
    for i, (label, body) in enumerate(sections):
        is_last = (i == len(sections) - 1)
        left = "╰─" if is_last else "├─"
        right = "──╯" if is_last else "──╮"
        section_blocks.append(f"{left}〔 {label} 〕{right}\n{body}")
    sections_text = "\n".join(section_blocks)

    text = (
        f"{header_top}\n"
        f"<blockquote>"
        f"<b>{mention}</b>\n"
        f"🆔 <code>{user_id}</code>\n"
        f"{cosmetic_line}"
        f"{premium_line}"
        f"{rank_line}{trivia_line}\n"
        f"{sections_text}"
        f"</blockquote>\n"
        f"🏪 <i>/market</i> — ⭐ Star &amp; ကဒ်များ　🎭 <i>/shop</i> — Title/Emblem/Frame\n"
        f"<i>Tap below to open your full 🧬 Rarity Tracker!</i>"
    )
    buttons = [[Button.inline("ရထားတဲ့ကဒ်များ", data=f"pf_stats_{user_id}")]]
    if total_gifted:
        buttons.append([Button.inline("🎁 Gift History", data=f"gh_{user_id}_0")])
    return text, buttons

async def render_profile_stats_page(user_id, mention):
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    raw_harem = user_doc.get("harem", []) if user_doc else []
    gifted_by_tier = (user_doc.get("gifted_by_rarity", {}) if user_doc else {}) or {}
    total_gifted = user_doc.get("total_gifted", 0) if user_doc else 0
    tier_counts = {t: 0 for t in RARITY_TIERS}
    for item in raw_harem:
        if isinstance(item, dict):
            tier = classify_rarity(item.get("rarity", ""))
            if tier in tier_counts:
                tier_counts[tier] += 1
    tier_rows = []
    for tier in RARITY_TIERS:
        cnt = tier_counts[tier]
        gifted = gifted_by_tier.get(tier, 0)
        if cnt == 0 and gifted == 0:
            continue
        ever_total = cnt + gifted
        tier_rows.append(f"{RARITY_EMOJI[tier]} <b>{tier}</b>　<code>{cnt} / {ever_total}</code>")
    body = "\n".join(tier_rows) if tier_rows else "<i>No characters caught yet.</i>"

    title_text = sc("𖤐 rarity tracker")
    footer_note = f"\n🎁 <b>{sc('Gifted away')}</b> <code>{total_gifted}</code>" if total_gifted else ""
    text = (
        f"╭─〔 {title_text} 〕─╮\n"
        f"<blockquote>"
        f"<b>{mention}</b>\n"
        f"<i>current / total ever obtained (incl. gifted-away)</i>\n"
        f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        f"{body}"
        f"{footer_note}\n"
        f"</blockquote>"
        f"╰─────────────────╯"
    )
    buttons = [[Button.inline("🔙 Back to Profile", data=f"pf_main_{user_id}")]]
    return text, buttons

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.](profile|myinfo)(?:@\w+)?$', 'bot1')))
async def profile_handler(event):
    user_id = event.sender_id
    mention = await get_html_mention(event, user_id)
    await ensure_user_registered(user_id, await get_plain_name(event, user_id))
    text, buttons = await render_profile_main_page(event, user_id, mention)
    photo_stream = None
    try:
        photo_bytes = await bot1.download_profile_photo(user_id, file=bytes)
        if photo_bytes:
            photo_stream = io.BytesIO(photo_bytes)
            photo_stream.name = "profile.jpg"
    except Exception:
        photo_stream = None
    if photo_stream:
        try:
            await bot1.send_file(
                event.chat_id, photo_stream, caption=text,
                parse_mode='html', reply_to=event.id, buttons=buttons
            )
            return
        except Exception:
            pass
    await event.reply(text, parse_mode='html', buttons=buttons)

# ==========================================
# 🎭 COSMETICS SHOP — /shop (Title, Emblem, Frame — ⭐ money sink, purely cosmetic)
# ==========================================
# Owned forever once bought (kept in users_catcher_col.owned_cosmetics), but only ONE item per
# category can be equipped at a time (equipped_title / equipped_emblem / equipped_frame).
# Equipped title+emblem show as a line on /profile; equipped frame decorates the card header.
# No gameplay effect whatsoever — this exists purely as another place to spend ⭐.
# 🩹 2026-08 USD RETIREMENT: these prices used to be USD wallet_balance amounts (5-50,000).
# Re-tuned to a comparable ⭐ Star progression for the post-migration economy — same relative
# shape (starter item cheap, prestige item a genuine long-term goal, sitting comfortably under
# the top "Hold 25,000 Star" wealth achievement) rather than the same raw digits relabeled —
# owner-adjustable, tune to taste.
COSMETICS_CATALOG = {
    "title": [
        {"id": "title_newcomer",     "label": "🌱 Newcomer",      "price": 2},
        {"id": "title_adventurer",   "label": "⚔️ Adventurer",    "price": 8},
        {"id": "title_veteran",      "label": "🔥 Veteran",       "price": 30},
        {"id": "title_highroller",   "label": "💎 High Roller",   "price": 100},
        {"id": "title_dragonmaster", "label": "🐉 Dragon Master", "price": 400},
        {"id": "title_legend",       "label": "👑 Legend",        "price": 1500},
        {"id": "title_mythic",       "label": "🌌 Mythic",        "price": 6000},
    ],
    "emblem": [
        {"id": "emblem_lucky",        "label": "🍀 Lucky",         "price": 3},
        {"id": "emblem_swift",        "label": "⚡ Swift",         "price": 15},
        {"id": "emblem_sharpshooter", "label": "🎯 Sharpshooter",  "price": 60},
        {"id": "emblem_elite",        "label": "🦅 Elite",         "price": 250},
        {"id": "emblem_champion",     "label": "🔱 Champion",      "price": 1000},
    ],
    "frame": [
        {"id": "frame_silver",  "label": "🥈 Silver Frame",  "price": 50,   "border": "🥈"},
        {"id": "frame_gold",    "label": "🥇 Gold Frame",    "price": 200,  "border": "🥇"},
        {"id": "frame_diamond", "label": "💠 Diamond Frame", "price": 800,  "border": "💠"},
        {"id": "frame_prism",   "label": "🌈 Prism Frame",   "price": 3000, "border": "🌈"},
    ],
}
COSMETIC_CATEGORY_LABELS = {"title": "🏷️ Title", "emblem": "🎖️ Emblem", "frame": "🖼️ Frame"}
COSMETIC_EQUIP_FIELD = {"title": "equipped_title", "emblem": "equipped_emblem", "frame": "equipped_frame"}

def _find_cosmetic(cosmetic_id):
    for cat, items in COSMETICS_CATALOG.items():
        for item in items:
            if item["id"] == cosmetic_id:
                return cat, item
    return None, None

def get_equipped_cosmetics_prefix(user_doc):
    """Equipped Title + Emblem as one short line for the profile card. '' if neither equipped."""
    if not user_doc:
        return ""
    parts = []
    for field in ("equipped_title", "equipped_emblem"):
        cid = user_doc.get(field)
        if cid:
            _, item = _find_cosmetic(cid)
            if item:
                parts.append(item["label"])
    return " ".join(parts)

def get_equipped_frame_border(user_doc):
    cid = user_doc.get("equipped_frame") if user_doc else None
    if not cid:
        return None
    _, item = _find_cosmetic(cid)
    return item.get("border") if item else None

def _render_shop_category(user_doc, category):
    items = COSMETICS_CATALOG[category]
    owned = set((user_doc or {}).get("owned_cosmetics", []))
    equipped = (user_doc or {}).get(COSMETIC_EQUIP_FIELD[category])
    lines = [f"🎭 <b>COSMETICS SHOP — {COSMETIC_CATEGORY_LABELS[category]}</b>", "<i>ဒီဟာတွေက Cosmetic ပဲဖြစ်ပြီး Gameplay ပေါ် ဘာမှသက်ရောက်မှုမရှိပါ — Profile ကို လှပအောင် လုပ်ဖို့ပဲ ဖြစ်ပါတယ်။</i>", ""]
    rows = []
    for item in items:
        tag = "✅ Equipped" if equipped == item["id"] else ("🔓 Owned" if item["id"] in owned else f"💰 {format_star_plain(item['price'])}")
        lines.append(f"{item['label']} — {tag}")
        if equipped == item["id"]:
            rows.append([Button.inline(f"↩️ Unequip {item['label']}", data=f"shop_uneq_{category}")])
        elif item["id"] in owned:
            rows.append([Button.inline(f"👕 Equip {item['label']}", data=f"shop_equip_{item['id']}")])
        else:
            rows.append([Button.inline(f"💰 Buy {item['label']} — {format_star_plain(item['price'])}", data=f"shop_buy_{item['id']}")])
    cat_row = [Button.inline(("• " if c == category else "") + lbl, data=f"shop_cat_{c}") for c, lbl in COSMETIC_CATEGORY_LABELS.items()]
    rows.append(cat_row)
    return "\n".join(lines), rows

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]shop(?:@\w+)?$', 'bot1')))
async def cosmetics_shop_command(event):
    user_id = event.sender_id
    await ensure_user_registered(user_id, await get_plain_name(event, user_id))
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    text, rows = _render_shop_category(user_doc, "title")
    await _out(event, text, parse_mode='html', buttons=rows)

@bot1.on(events.CallbackQuery(pattern=r'^shop_cat_(title|emblem|frame)$'))
async def shop_category_callback(event):
    category = event.pattern_match.group(1)
    if isinstance(category, bytes):
        category = category.decode('utf-8')
    if event.sender_id is None:
        return
    user_doc = await users_catcher_col.find_one({"user_id": event.sender_id})
    text, rows = _render_shop_category(user_doc, category)
    try:
        await event.edit(text, parse_mode='html', buttons=rows)
    except errors.MessageNotModifiedError:
        pass
    await event.answer()

@bot1.on(events.CallbackQuery(pattern=r'^shop_buy_(\w+)$'))
async def shop_buy_callback(event):
    cosmetic_id = event.pattern_match.group(1)
    if isinstance(cosmetic_id, bytes):
        cosmetic_id = cosmetic_id.decode('utf-8')
    user_id = event.sender_id
    if not claim_single_tap(event):
        return await event.answer()
    category, item = _find_cosmetic(cosmetic_id)
    if not item:
        return await event.answer("⚠️ ဒီ Item မရှိပါ။", alert=True)
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    if item["id"] in (user_doc or {}).get("owned_cosmetics", []):
        return await event.answer("✅ ဒါကို ရှိပြီးသားပါ။", alert=True)
    if not await try_deduct_balance(user_id, item["price"]):
        return await event.answer(f"❌ Balance မလုံလောက်ပါ — {format_star_plain(item['price'])} လိုအပ်ပါတယ်။", alert=True)
    await users_catcher_col.update_one({"user_id": user_id}, {"$addToSet": {"owned_cosmetics": item["id"]}})
    await event.answer(f"🎉 {item['label']} ဝယ်ယူပြီးပါပြီ!")
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    text, rows = _render_shop_category(user_doc, category)
    try:
        await event.edit(text, parse_mode='html', buttons=rows)
    except errors.MessageNotModifiedError:
        pass

@bot1.on(events.CallbackQuery(pattern=r'^shop_equip_(\w+)$'))
async def shop_equip_callback(event):
    cosmetic_id = event.pattern_match.group(1)
    if isinstance(cosmetic_id, bytes):
        cosmetic_id = cosmetic_id.decode('utf-8')
    user_id = event.sender_id
    if not claim_single_tap(event):
        return await event.answer()
    category, item = _find_cosmetic(cosmetic_id)
    if not item:
        return await event.answer("⚠️ ဒီ Item မရှိပါ။", alert=True)
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    if item["id"] not in (user_doc or {}).get("owned_cosmetics", []):
        return await event.answer("❌ ဒါကို မဝယ်ရသေးပါ။", alert=True)
    await users_catcher_col.update_one({"user_id": user_id}, {"$set": {COSMETIC_EQUIP_FIELD[category]: item["id"]}})
    await event.answer(f"👕 {item['label']} ဝတ်ဆင်လိုက်ပါပြီ!")
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    text, rows = _render_shop_category(user_doc, category)
    try:
        await event.edit(text, parse_mode='html', buttons=rows)
    except errors.MessageNotModifiedError:
        pass

@bot1.on(events.CallbackQuery(pattern=r'^shop_uneq_(title|emblem|frame)$'))
async def shop_unequip_callback(event):
    category = event.pattern_match.group(1)
    if isinstance(category, bytes):
        category = category.decode('utf-8')
    user_id = event.sender_id
    if not claim_single_tap(event):
        return await event.answer()
    await users_catcher_col.update_one({"user_id": user_id}, {"$unset": {COSMETIC_EQUIP_FIELD[category]: ""}})
    await event.answer("↩️ ချွတ်လိုက်ပါပြီ။")
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    text, rows = _render_shop_category(user_doc, category)
    try:
        await event.edit(text, parse_mode='html', buttons=rows)
    except errors.MessageNotModifiedError:
        pass

ACH_ENTRIES_PER_PAGE = 7

async def send_achievements_page(client, chat_id, user_id, mention, page=1, edit_msg_id=None):
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    earned_ids = set(user_doc.get("achievements", [])) if user_doc else set()
    entries = []
    for ach in ACHIEVEMENTS:
        tick = "✅" if ach["id"] in earned_ids else "🔒"
        entries.append(f"{tick} {ach['emoji']} <b>{ach['name']}</b>\n<i>{ach['desc']}</i>")
    total_pages = max(1, (len(entries) + ACH_ENTRIES_PER_PAGE - 1) // ACH_ENTRIES_PER_PAGE)
    if page < 1: page = 1
    if page > total_pages: page = total_pages
    start_idx = (page - 1) * ACH_ENTRIES_PER_PAGE
    page_entries = entries[start_idx:start_idx + ACH_ENTRIES_PER_PAGE]
    header = (
        f"🏅 <b>ACHIEVEMENTS</b> — {mention}\n"
        f"📊 <b>Unlocked:</b> <code>{len(earned_ids)}/{len(ACHIEVEMENTS)}</code>\n"
        f"📑 <b>Page:</b> <code>{page}/{total_pages}</code>\n"
    )
    output_text = header + "\n" + "\n".join(page_entries)
    buttons = []
    nav_buttons = []
    if page > 1:
        nav_buttons.append(Button.inline("🔵 ⬅️ Prev", data=f"ach_{page-1}_{user_id}"))
    if page < total_pages:
        nav_buttons.append(Button.inline("🔴 Next ➡️", data=f"ach_{page+1}_{user_id}"))
    if nav_buttons:
        buttons.append(nav_buttons)
    if not buttons:
        buttons = None
    if edit_msg_id:
        try:
            await client.edit_message(chat_id, edit_msg_id, output_text, parse_mode='html', buttons=buttons)
        except errors.MessageNotModifiedError:
            pass
    else:
        await client.send_message(chat_id, output_text, parse_mode='html', buttons=buttons)

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]achievements(?:@\w+)?$', 'bot1')))
async def achievements_handler(event):
    user_id = event.sender_id
    mention = await get_html_mention(event, user_id)
    await ensure_user_registered(user_id, await get_plain_name(event, user_id))
    await check_and_award_achievements(user_id)
    await send_achievements_page(bot1, event.chat_id, user_id, mention, page=1)

# ==========================================
# 🔗 REFERRAL
# ==========================================
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]referral(?:@\w+)?$', 'bot1')))
async def referral_info_handler(event):
    user_id = event.sender_id
    mention = await get_html_mention(event, user_id)
    await ensure_user_registered(user_id, await get_plain_name(event, user_id))
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    ref_count = user_doc.get("referral_count", 0) if user_doc else 0
    bot_me = await bot1.get_me()
    ref_link = f"https://t.me/{bot_me.username}?start=ref_{user_id}"
    msg = f"🔗 <b>YOUR REFERRAL LINK</b>\n⚡ ━━━━━━━━━━━━━━━ ⚡\nShare this link! You get <code>+4⭐</code>, friend gets <code>+2⭐</code>.\n\n🔗 <code>{ref_link}</code>\n\n👥 <b>Total Invited:</b> <code>{ref_count} Friends</code>"
    share_url = f"https://t.me/share/url?url={ref_link}&text=Join%20the%20Bot%20now!"
    await event.reply(msg, parse_mode='html', buttons=[[Button.url("📤 Share Link", share_url)]])

# ==========================================
# 🔍 CHECK CHARACTER (Updated: spawn_count now reflects actual catches)
# ==========================================
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]check(?:@\w+)?$', 'bot1')))
async def check_bare_usage_handler(event):
    await event.reply(
        "📌 <b>Usage:</b> <code>/check [CharID]</code>\n"
        "<i>Example:</i> <code>/check 1234</code>",
        parse_mode='html'
    )

async def build_character_check_text(char_id_input):
    """Core /check lookup + render logic, factored out so both the /check [id] command and the
    'Check #<id>' inline button on a fresh catch (see checkcard_ in system_callback_router) can
    share it. Returns (character_doc, info_text) on success, or (None, error_text) if the given
    char_id doesn't exist."""
    normalized = normalize_char_id_input(char_id_input)

    # ✅ Case-Insensitive ရှာဖို့
    character = await characters_base_col.find_one({
        "char_id": {"$regex": f"^{normalized}$", "$options": "i"}
    })

    if not character:
        return None, "<b>✗ Character ID not found!</b>"

    # ✅ spawn_count က catch အရေအတွက်ကိုပြတယ်
    spawn_count = character.get("spawn_count", 0)
    limit = character.get("spawn_limit", 0)
    is_locked = bool(limit and limit > 0 and spawn_count >= limit)

    # ✅ ပြသပုံ
    if limit == 0:
        spawns_line = f"<code>{spawn_count}</code> (♾️ Infinite)"
    elif is_locked:
        spawns_line = f"<code>{spawn_count}/{limit}</code> (🔒 LOCKED)"
    else:
        remaining = max(0, limit - spawn_count)
        spawns_line = f"<code>{spawn_count}/{limit}</code> (<code>{remaining} left</code>)"
    # 🔒 LOCKED — deliberately in English (per owner request), regardless of the surrounding
    # Burmese UI, so this specific notice reads unambiguously. Applies to any character whose
    # catch limit has been reached, not just ULTRA — see ULTRA_DEFAULT_CATCH_LIMIT's docstring
    # for why ULTRA cards hit this in practice.
    locked_line = (
        "\n🔒 <b>LOCKED — catch limit reached.</b> <i>No longer catchable, spawnable, or "
        "purchasable from the Owner Shop. The only way to get a copy now is /gift or /trade "
        "from an existing owner.</i>\n"
    ) if is_locked else ""

    # ✅ Top 10 Collectors — ranked by catch count first, then by whoever caught it
    # EARLIEST (lowest caught_date) as the tiebreaker, so the first person to ever land
    # this card shows up as #1 instead of wherever Mongo's tie order happens to place them.
    pipeline = [
        {"$match": {"harem.char_id": character['char_id']}},
        {"$project": {
            "fullname": "$fullname",
            "user_id": "$user_id",
            "matches": {"$filter": {
                "input": "$harem",
                "as": "item",
                "cond": {"$eq": ["$$item.char_id", character['char_id']]}
            }}
        }},
        {"$project": {
            "fullname": 1,
            "user_id": 1,
            "count": {"$size": "$matches"},
            "first_caught": {"$min": "$matches.caught_date"}
        }},
        {"$sort": {"count": -1, "first_caught": 1}},
        {"$limit": 10}
    ]
    top_hunters = await users_catcher_col.aggregate(pipeline).to_list(length=10)

    leaderboard_str = ""
    for idx, u in enumerate(top_hunters, start=1):
        uname = escape_html(clean_display_name(u.get("fullname"), fallback=f"Agent {u['user_id']}"))
        # ✅ Mention ပါစေ — tg://user deep link works off the stored user_id alone (no extra
        # get_entity round-trip needed), so tapping a name opens that collector's account.
        mention = f"<a href='tg://user?id={u['user_id']}'>{uname}</a>"
        leaderboard_str += f"➥ {mention} x{u['count']}\n"

    # 💠 Price band — floor is /sell's guaranteed scrap value, ceiling is the Owner Shop's
    # fresh price (see scrap_price_for_char / vlt_price_for_char).
    floor_price = scrap_price_for_char(character)
    ceiling_price = vlt_price_for_char(character)

    # 🎗️ Limited Edition — see /addle. Shown here so it's identical everywhere /check is
    # reached from (the /check [id] command and the fresh-catch "Check #<id>" button both
    # call this same function).
    # 🩹 FIX (2026-08, per owner report): used to also show "LE Daily Reward: X/day" here — that
    # payout was removed, but this display line was missed at the time. le_daily_vlt is still
    # stored per character (see /addle) but nothing pays it out anymore, so showing it here was
    # actively misleading — looked like a live, ongoing reward when it no longer is one.
    le_line = ""
    if character.get("is_limited_edition"):
        le_line = f"\n🎗️ <b>Limited Edition:</b> <code>{escape_html(bookend_emoji_name(character.get('le_name', '?')))}</code>"

    # 💠 format_vlt_plain() appends its own "💠" per call — strip that off both ends of the
    # range so the price line carries a single trailing unit instead of one per number.
    price_floor = format_vlt_plain(floor_price).replace(' 💠', '')
    price_ceiling = format_vlt_plain(ceiling_price).replace(' 💠', '')

    # 🩹 FIX (owner report — "rarity emoji is showing up in unrelated places"): this used to
    # bookend the event name with rarity_emoji, which has nothing to do with the event — see
    # extract_bracketed_emoji's own comment for the full story. Now uses the event's OWN tag
    # when it has one, and skips the whole decorative banner line entirely otherwise (for
    # "General" or an event with no [emoji] tag) rather than forcing an unrelated symbol in.
    rarity_tier = classify_rarity(character.get("rarity", ""))
    rarity_emoji = RARITY_EMOJI.get(rarity_tier, RARITY_DEFAULT_EMOJI)
    rarity_name = RARITY_DISPLAY_NAME.get(rarity_tier, rarity_tier)
    raw_event_name = character.get('event') or 'General'
    event_name = escape_html(raw_event_name)
    if raw_event_name == 'General':
        event_banner = ""
    else:
        event_tag_emoji = extract_bracketed_emoji(event_name)
        event_banner = f"{bold_italic_serif(event_name)}{' ' + event_tag_emoji if event_tag_emoji else ''}\n\n"

    top_catchers_block = leaderboard_str.strip("\n") if leaderboard_str else "➥ <i>No collectors yet.</i>"

    info_text = (
        f"<b>📇 Got a hit — here's the file!</b>\n\n"
        f"{escape_html(character['category'])}\n"
        f"{display_char_id(character['char_id'])}: {escape_html(character['name'])} [{rarity_emoji}]\n"
        f"{artist_line(character)}"
        f"({rarity_emoji} {sans_bold_italic('RARITY')}: {rarity_name})\n\n"
        f"{event_banner}"
        f"💰 V{small_caps('alue')}: {price_floor} ~ {price_ceiling} 💠\n\n"
        f"🌎 {small_caps('caught globally')}: {spawns_line}"
        f"{locked_line}"
        f"{le_line}\n\n"
        f"🎖️ {small_caps('top 10 catchers of this character')}!\n"
        f"{top_catchers_block}"
    )
    return character, info_text

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]check(?:@\w+)?\s+([a-zA-Z0-9_]+)$', 'bot1')))
async def check_character_id_handler(event):
    character, info_text = await build_character_check_text(event.pattern_match.group(1))
    if not character:
        return await event.reply(info_text, parse_mode='html')

    async def _send_check(media):
        return await event.reply(info_text, parse_mode='html', file=media)

    sent = await send_with_char_media(character["char_id"], character["storage_msg_id"], _send_check)
    if sent is None:
        await event.reply(info_text, parse_mode='html')
# ==========================================
# ==========================================
# 📖 DEX (Fixed - No Redis Dependency)
# ==========================================
DEX_CATEGORIES_PER_PAGE = 15
DEX_CARDS_PER_PAGE = 15

def _dex_rarity_weight(rarity_str):
    return rarity_rank_value(rarity_str)

async def _dex_get_all_characters():
    """Get all characters directly from DB (bypass Redis cache for reliability)."""
    return await characters_base_col.find({}, {"_id": 0}).to_list(length=None)

async def _dex_get_all_categories():
    """Get all categories directly from DB."""
    return await characters_base_col.distinct("category")

async def _dex_get_owned_ids(user_id):
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    if not user_doc:
        return set()
    return {c.get("char_id") for c in user_doc.get("harem", []) if isinstance(c, dict) and c.get("char_id")}

# ---- /dex : Show all categories with pagination ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]dex(?:@\w+)?$', 'bot1')))
async def dex_categories_handler(event):
    user_id = event.sender_id
    owned_ids = await _dex_get_owned_ids(user_id)
    all_categories = await _dex_get_all_categories()
    
    if not all_categories:
        return await event.reply("📭 <b>No categories found!</b>", parse_mode='html')
    
    all_categories = sorted(all_categories, key=lambda x: x.lower())
    all_chars = await _dex_get_all_characters()
    
    category_data = []
    for cat in all_categories:
        total = sum(1 for c in all_chars if c.get("category") == cat)
        owned = sum(1 for c in all_chars if c.get("category") == cat and c["char_id"] in owned_ids)
        category_data.append((cat, total, owned))
    
    total_pages = (len(category_data) + DEX_CATEGORIES_PER_PAGE - 1) // DEX_CATEGORIES_PER_PAGE
    await _dex_send_categories_page(event, category_data, 0, total_pages, owned_ids, all_chars)

async def _dex_send_categories_page(event, category_data, page, total_pages, owned_ids, all_chars, edit_msg_id=None):
    start = page * DEX_CATEGORIES_PER_PAGE
    end = min(start + DEX_CATEGORIES_PER_PAGE, len(category_data))
    page_data = category_data[start:end]
    
    total_all = len(all_chars)
    owned_all = len(owned_ids & {c["char_id"] for c in all_chars})
    
    text = f"📖 <b>CHARACTER DEX</b>\n"
    text += f"🧩 <b>Total:</b> <code>{owned_all}/{total_all}</code> caught\n"
    text += f"📑 <b>Page {page + 1}/{total_pages}</b> · <b>Categories:</b> <code>{len(category_data)}</code>\n\n"
    text += f"<i>👇 Tap a category to see all cards</i>\n\n"
    
    for idx, (cat, total, owned) in enumerate(page_data, start=start + 1):
        marker = "🟩" if owned == total else ("🟨" if owned > 0 else "⬜")
        text += f"{marker} <b>{idx}.</b> <code>{escape_html(cat)}</code> — <b>{owned}/{total}</b>\n"
    
    buttons = []
    nav_buttons = []
    if page > 0:
        nav_buttons.append(Button.inline("◀ Prev", data=f"dex_cat_page_{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(Button.inline("Next ▶", data=f"dex_cat_page_{page + 1}"))
    if nav_buttons:
        buttons.append(nav_buttons)
    
    if edit_msg_id:
        try:
            await bot1.edit_message(event.chat_id, edit_msg_id, text, parse_mode='html', buttons=buttons)
        except errors.MessageNotModifiedError:
            pass
    else:
        await event.reply(text, parse_mode='html', buttons=buttons)

# ---- /search [Category] ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]search(?:@\w+)?\s+(.+)$', 'bot1')))
async def dex_search_handler(event):
    user_id = event.sender_id
    category_query = event.pattern_match.group(1).strip()
    owned_ids = await _dex_get_owned_ids(user_id)
    
    all_categories = await _dex_get_all_categories()
    matched_cat = next((c for c in all_categories if c and c.lower() == category_query.lower()), None)
    
    if not matched_cat:
        return await event.reply(
            f"❌ <b>No category named '{escape_html(category_query)}'</b>\n"
            f"💡 Use <code>/dex</code> to see all categories.",
            parse_mode='html'
        )
    
    all_chars = await _dex_get_all_characters()
    cat_chars = [c for c in all_chars if c.get("category") == matched_cat]
    cat_chars.sort(key=lambda x: (-_dex_rarity_weight(x.get("rarity", "")), x.get("name", "").lower()))
    
    total_pages = (len(cat_chars) + DEX_CARDS_PER_PAGE - 1) // DEX_CARDS_PER_PAGE
    await _dex_send_category_detail(event, matched_cat, cat_chars, owned_ids, 0, total_pages)

async def _dex_send_category_detail(event, category, cat_chars, owned_ids, page, total_pages, edit_msg_id=None):
    start = page * DEX_CARDS_PER_PAGE
    end = min(start + DEX_CARDS_PER_PAGE, len(cat_chars))
    page_chars = cat_chars[start:end]
    
    total_n = len(cat_chars)
    owned_n = sum(1 for c in cat_chars if c["char_id"] in owned_ids)
    pct = int((owned_n / total_n) * 100) if total_n else 0
    
    rarity_counter = Counter(classify_rarity(c.get("rarity", "")) for c in cat_chars)
    breakdown_lines = " | ".join(
        f"{RARITY_EMOJI.get(tier, RARITY_DEFAULT_EMOJI)}{tier[:3]} <code>{rarity_counter[tier]}</code>"
        for tier in RARITY_TIERS if rarity_counter.get(tier)
    )
    
    text = f"📖 <b>{escape_html(category)}</b> ({owned_n}/{total_n} · {pct}%)\n"
    text += f"📑 <b>Page {page + 1}/{total_pages}</b>\n"
    text += f"{breakdown_lines}\n\n"
    
    for idx, card in enumerate(page_chars, start=start + 1):
        char_id = card["char_id"]
        owned = "✅" if char_id in owned_ids else "❌"
        rarity_display = card.get("rarity", "➖")
        event_text = card.get("event", "General")
        if event_text == "General":
            event_text = "➖"
        text += f"{owned} <b>{idx}.</b> {rarity_display} <code>{escape_html(card['name'])}</code> [<code>{display_char_id(char_id)}</code>]\n"
        text += f"   🎪 {escape_html(event_text)}\n"
    
    buttons = []
    nav_buttons = []
    if page > 0:
        nav_buttons.append(Button.inline("◀ Prev", data=f"dex_detail_{category}_{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(Button.inline("Next ▶", data=f"dex_detail_{category}_{page + 1}"))
    if nav_buttons:
        buttons.append(nav_buttons)
    buttons.append([Button.inline("🔙 Back to Categories", data="dex_back_categories")])
    buttons.append([Button.switch_inline(f"👀 View {category} Gallery", query=f"dex.{category}", same_peer=True)])
    
    if edit_msg_id:
        try:
            await bot1.edit_message(event.chat_id, edit_msg_id, text, parse_mode='html', buttons=buttons)
        except errors.MessageNotModifiedError:
            pass
    else:
        await event.reply(text, parse_mode='html', buttons=buttons)

# ---- Callback handlers ----
@bot1.on(events.CallbackQuery(pattern=r'^dex_cat_page_(\d+)$'))
async def dex_cat_page_callback(event):
    page = int(event.pattern_match.group(1))
    user_id = event.sender_id
    owned_ids = await _dex_get_owned_ids(user_id)
    all_categories = sorted(await _dex_get_all_categories(), key=lambda x: x.lower())
    all_chars = await _dex_get_all_characters()
    
    category_data = []
    for cat in all_categories:
        total = sum(1 for c in all_chars if c.get("category") == cat)
        owned = sum(1 for c in all_chars if c.get("category") == cat and c["char_id"] in owned_ids)
        category_data.append((cat, total, owned))
    
    total_pages = (len(category_data) + DEX_CATEGORIES_PER_PAGE - 1) // DEX_CATEGORIES_PER_PAGE
    await _dex_send_categories_page(event, category_data, page, total_pages, owned_ids, all_chars, edit_msg_id=event.message_id)
    await event.answer()

@bot1.on(events.CallbackQuery(pattern=r'^dex_detail_([^_]+)_(\d+)$'))
async def dex_detail_page_callback(event):
    category = event.pattern_match.group(1)
    if isinstance(category, bytes):
        category = category.decode('utf-8')
    page = int(event.pattern_match.group(2))
    user_id = event.sender_id
    owned_ids = await _dex_get_owned_ids(user_id)
    all_chars = await _dex_get_all_characters()
    
    cat_chars = [c for c in all_chars if c.get("category") == category]
    cat_chars.sort(key=lambda x: (-_dex_rarity_weight(x.get("rarity", "")), x.get("name", "").lower()))
    total_pages = (len(cat_chars) + DEX_CARDS_PER_PAGE - 1) // DEX_CARDS_PER_PAGE
    
    await _dex_send_category_detail(event, category, cat_chars, owned_ids, page, total_pages, edit_msg_id=event.message_id)
    await event.answer()

@bot1.on(events.CallbackQuery(pattern=r'^dex_back_categories$'))
async def dex_back_categories_callback(event):
    user_id = event.sender_id
    owned_ids = await _dex_get_owned_ids(user_id)
    all_categories = sorted(await _dex_get_all_categories(), key=lambda x: x.lower())
    all_chars = await _dex_get_all_characters()
    
    category_data = []
    for cat in all_categories:
        total = sum(1 for c in all_chars if c.get("category") == cat)
        owned = sum(1 for c in all_chars if c.get("category") == cat and c["char_id"] in owned_ids)
        category_data.append((cat, total, owned))
    
    total_pages = (len(category_data) + DEX_CATEGORIES_PER_PAGE - 1) // DEX_CATEGORIES_PER_PAGE
    await _dex_send_categories_page(event, category_data, 0, total_pages, owned_ids, all_chars, edit_msg_id=event.message_id)
    await event.answer()

# ---- /dex [Category] - shortcut ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]dex(?:@\w+)?\s+(.+)$', 'bot1')))
async def dex_with_category_handler(event):
    # Same as /search
    user_id = event.sender_id
    category_query = event.pattern_match.group(1).strip()
    owned_ids = await _dex_get_owned_ids(user_id)
    
    all_categories = await _dex_get_all_categories()
    matched_cat = next((c for c in all_categories if c and c.lower() == category_query.lower()), None)
    
    if not matched_cat:
        return await event.reply(
            f"❌ <b>No category named '{escape_html(category_query)}'</b>\n"
            f"💡 Use <code>/dex</code> to see all categories.",
            parse_mode='html'
        )
    
    all_chars = await _dex_get_all_characters()
    cat_chars = [c for c in all_chars if c.get("category") == matched_cat]
    cat_chars.sort(key=lambda x: (-_dex_rarity_weight(x.get("rarity", "")), x.get("name", "").lower()))
    
    total_pages = (len(cat_chars) + DEX_CARDS_PER_PAGE - 1) // DEX_CARDS_PER_PAGE
    await _dex_send_category_detail(event, matched_cat, cat_chars, owned_ids, 0, total_pages)
# ==========================================
# 🛍️ /buy [char_id] — DAILY & LIFETIME PURCHASE LIMITS (Owner exempt from both)
# ==========================================
DAILY_BUY_LIMIT = 3  # Owner Shop purchases (/buy [char_id]) per player per day
LIFETIME_BUY_PER_CHAR_LIMIT = 1  # max copies of the SAME character a player can EVER buy via /buy
TOP_RARITY_WEEKLY_TIERS = {"ULTRA", "LEGEND", "MYTHIC"}  # Rarity No.1, No.2, No.3
TOP_RARITY_WEEKLY_BUY_LIMIT = 2  # combined cap across ALL THREE tiers together, per rolling week
TOP_RARITY_WEEK_SECONDS = 7 * 86400
# 🔒 NEW CARD PROTECTION — a freshly /addchar'd character can't be bought outright via
# /buy for this long after creation; it can only be obtained by actually catching it from a
# live spawn. Stops a deep-pocketed player from insta-sniping every new card the moment it's
# added, before anyone else even gets a chance to see it spawn. Keyed off characters_base_col's
# "created_at" (set at /addchar time — see add_character()); characters added before this
# feature shipped have no created_at, default to 0, and are correctly treated as long past
# the window (so nothing already in the roster gets retroactively locked).
NEW_CARD_PROTECTION_SECONDS = 86400  # 24 hours

# ==========================================
# 👑 BOT PREMIUM USER — tuning constants
# ==========================================
PREMIUM_AUTO_STAR_THRESHOLD = 100  # 💠 VLT spent (via /buy Owner Shop) in one day auto-grants Premium
PREMIUM_AUTO_GRANT_DAYS = 1  # length of the auto-grant, from the moment the threshold is crossed
PREMIUM_TIERS = [  # (months, price in ⭐, days credited) — the 5 /buypremium tiers
    (1, 9000, 30),
    (2, 17500, 60),
    (3, 25500, 90),
    (6, 48000, 180),
    (12, 90000, 365),
]
PREMIUM_DAILY_CATCH_LIMIT = 25  # vs DAILY_CATCH_LIMIT (22) for everyone else
PREMIUM_SPAM_MUTE_SECONDS = 180  # 3 minutes, vs SPAM_CATCH_MUTE_SECONDS (8 min / 480s)
PREMIUM_QUIZ_REWARD_MULTIPLIER = 1.5  # Premium winners' rarity-gate quiz Star bonus is scaled by this
PREMIUM_PURCHASE_BONUS_TIER = "RARE"  # Rarity No.6 (🌕 GOLDENMOON) — free cards on every /buypremium purchase
PREMIUM_PURCHASE_BONUS_COUNT = 2

def is_premium_active(user_doc):
    """True if user_doc's premium_until is still in the future. Safe to call with None."""
    return bool(user_doc) and user_doc.get("premium_until", 0) > time.time()

async def check_premium(user_id):
    doc = await users_catcher_col.find_one({"user_id": user_id}, {"premium_until": 1})
    return is_premium_active(doc)

async def grant_premium_days(user_id, days):
    """Extends premium_until by `days`. Stacks on top of any time already remaining —
    buying/earning more while already Premium adds to what's left instead of overwriting it."""
    now = time.time()
    user_doc = await users_catcher_col.find_one({"user_id": user_id}, {"premium_until": 1})
    current_until = (user_doc or {}).get("premium_until", 0)
    base = current_until if current_until > now else now
    new_until = base + days * 86400
    await users_catcher_col.update_one({"user_id": user_id}, {"$set": {"premium_until": new_until}}, upsert=True)
    return new_until

async def _track_star_activity_and_maybe_grant_premium(user_id, kind, amount):
    """kind: 'spent' (💠 VLT spent on /buy Owner Shop purchases) — the only kind anything
    still calls this with. 🩹 2026-08 USD/MARKET RETIREMENT: there used to be a second kind,
    'bought' (⭐ Star bought via /buystar), tracked separately in star_bought_today — /buystar
    is retired now, so that branch is simply never triggered anymore; left in place rather
    than ripped out in case a future Star-purchase path wants it back. Tracks a per-day
    running total and, the moment it FIRST crosses PREMIUM_AUTO_STAR_THRESHOLD in a single
    Yangon calendar day, grants PREMIUM_AUTO_GRANT_DAYS of Premium. Returns the new
    premium_until timestamp if a grant just happened, else None."""
    field = "star_spent_today" if kind == "spent" else "star_bought_today"
    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    user_doc = await users_catcher_col.find_one({"user_id": user_id}, {"star_activity_date": 1, field: 1})
    if not user_doc or user_doc.get("star_activity_date") != today_str:
        await users_catcher_col.update_one(
            {"user_id": user_id},
            {"$set": {"star_activity_date": today_str, "star_spent_today": 0.0, "star_bought_today": 0.0}},
            upsert=True
        )
        before = 0.0
    else:
        before = user_doc.get(field, 0.0)
    updated = await users_catcher_col.find_one_and_update(
        {"user_id": user_id}, {"$inc": {field: amount}}, return_document=ReturnDocument.AFTER
    )
    after = updated.get(field, before + amount) if updated else before + amount
    if before < PREMIUM_AUTO_STAR_THRESHOLD <= after:
        return await grant_premium_days(user_id, PREMIUM_AUTO_GRANT_DAYS)
    return None

# ==========================================
async def execute_star_shop_purchase(buyer_id, char_id):
    """⭐ OWNER SHOP — core purchase logic shared by the /buy [char id] command and the
    "🛒 Buy" button inside the /show gallery. Any character in the database can be bought
    directly (a fresh copy, independent of catching) at its fixed rarity Star price; the
    Star spent goes straight to the Owner's balance, and every purchase is logged for
    /buylist. Capped at DAILY_BUY_LIMIT/day and LIFETIME_BUY_PER_CHAR_LIMIT copies of the
    same character ever, per player — PLUS Rarity No.1-3 (ULTRA/LEGEND/MYTHIC) share a combined
    TOP_RARITY_WEEKLY_BUY_LIMIT across all three tiers together, per rolling
    TOP_RARITY_WEEK_SECONDS window, PLUS a brand new character can't be bought at all for its
    first NEW_CARD_PROTECTION_SECONDS (spawn-only during that window) (Owner is exempt from
    every cap here). Returns (ok: bool, message_html: str)."""
    char_doc = await characters_base_col.find_one({"char_id": char_id})
    if not char_doc:
        return False, "❌ <b>ဒီ Character ID ကို ရှာမတွေ့ပါ။</b>"
    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    now = time.time()
    char_tier = char_doc.get("rarity_tier")
    # 🚫 SOLD OUT — owner has temporarily disabled Owner Shop purchases for this tier via
    # /buytoggle. Catching it from a live spawn is unaffected; only the direct buy is blocked.
    if buyer_id != OWNER_ID and char_tier in _cached_disabled_buy_tiers:
        tier_num = RARITY_TIER_TO_NUM.get(char_tier, "?")
        return False, (
            f"🚫 <b>{RARITY_EMOJI.get(char_tier, '')} {char_tier} (No.{tier_num}) ကို Owner Shop ကနေ "
            f"ခဏပိတ်ထားပါတယ် — အရောင်းကုန်နေပါတယ်။</b>\n"
            f"🎯 <i>Spawn ကနေပဲ ဖမ်းလို့ရပါမယ်။</i>\n"
            f"☎️ <a href='{SOLD_OUT_CONTACT_LINK}'>ဒီမှာနှိပ်ပြီး Owner ကို Reply လုပ်ကာ ဝယ်ယူနိုင်ပါတယ်</a>"
        )
    # 🎯 CATCH LIMIT — permanently sold out from the Owner Shop too, once a character's own
    # spawn_count reaches its own spawn_limit (otherwise the Shop's "unlimited fresh copy"
    # model would make the wild-spawn cap meaningless — this used to only check this for
    # ULTRA specifically and pooled across the whole tier, which was wrong; see the ULTRA
    # SCARCITY CAP note near ULTRA_DEFAULT_CATCH_LIMIT). Owner is exempt, consistent with
    # every other cap in this function — they can still mint past a sold-out character's
    # limit if they genuinely need to (e.g. to fulfil a manual sale negotiated off-bot).
    char_limit = char_doc.get("spawn_limit", 0)
    if buyer_id != OWNER_ID and char_limit and char_limit > 0 and char_doc.get("spawn_count", 0) >= char_limit:
        return False, (
            f"🔒 <b>{escape_html(char_doc.get('name','?'))} ({RARITY_EMOJI.get(char_tier, '')} {char_tier}) ရဲ့ Catch Limit "
            f"({char_limit}) ပြည့်သွားပြီး Locked ဖြစ်သွားပါပြီ။</b>\n"
            f"🚫 <i>Owner Shop ကနေရော, Spawn ကနေရော ထပ်မရနိုင်တော့ပါ — /gift ဒါမှမဟုတ် /trade ဖြင့်သာ တခြား Owner ဆီကနေ ရနိုင်ပါတော့မယ်။</i>\n"
            f"☎️ <a href='{SOLD_OUT_CONTACT_LINK}'>ဒီမှာနှိပ်ပြီး Owner ကို Reply လုပ်ကာ ဆက်သွယ်နိုင်ပါတယ်</a>"
        )
    # 🔒 NEW CARD PROTECTION — block outright Owner Shop purchase for the first
    # NEW_CARD_PROTECTION_SECONDS after /addchar. Owner is exempt (same as every other cap
    # below) so they can still test-buy their own newly added character immediately.
    if buyer_id != OWNER_ID:
        card_age = now - char_doc.get("created_at", 0)
        if card_age < NEW_CARD_PROTECTION_SECONDS:
            remaining = NEW_CARD_PROTECTION_SECONDS - card_age
            hrs, rem_secs = divmod(int(remaining), 3600)
            mins = rem_secs // 60
            return False, (
                f"🔒 <b>ဒီကတ်က အသစ်ထည့်ထားတာဖြစ်လို့ ပထမ 24 နာရီအတွင်း Owner Shop ကနေ "
                f"<code>/buy</code> နဲ့ မဝယ်ရသေးပါဘူး။</b>\n"
                f"🎯 ဒီကတ်ကို Spawn ကနေပဲ ဖမ်းလို့ရပါမယ်။\n"
                f"⏳ <b>ကျန်ချိန်:</b> <code>{hrs}h {mins}m</code>"
            )
    if buyer_id != OWNER_ID:
        limit_doc = await users_catcher_col.find_one(
            {"user_id": buyer_id},
            {"buy_date": 1, "daily_buy_count": 1, "lifetime_char_buys": 1, "top_rarity_week_start": 1, "top_rarity_buys_this_week": 1}
        )
        daily_count = (limit_doc or {}).get("daily_buy_count", 0) if limit_doc and limit_doc.get("buy_date") == today_str else 0
        if daily_count >= DAILY_BUY_LIMIT:
            return False, f"❌ <b>ယနေ့အတွက် Owner Shop ဝယ်ယူခွင့် ပြည့်သွားပါပြီ။</b> <i>(တစ်ရက်လျှင် {DAILY_BUY_LIMIT} ကြိမ်သာ ဝယ်လို့ရပါတယ် — မနက်ဖြန် ပြန်လာပါ)</i>"
        lifetime_count = ((limit_doc or {}).get("lifetime_char_buys") or {}).get(char_id, 0)
        if lifetime_count >= LIFETIME_BUY_PER_CHAR_LIMIT:
            return False, f"❌ <b>ဒီ Character ကို Owner Shop ကနေ တစ်သက်တာအတွက် {LIFETIME_BUY_PER_CHAR_LIMIT} ကြိမ်အထိပဲ ဝယ်လို့ရပါတယ် — ပြည့်သွားပါပြီ။</b>"
        if char_tier in TOP_RARITY_WEEKLY_TIERS:
            week_start = (limit_doc or {}).get("top_rarity_week_start", 0)
            week_count = (limit_doc or {}).get("top_rarity_buys_this_week", 0)
            if now - week_start >= TOP_RARITY_WEEK_SECONDS:
                week_count = 0  # rolling week has elapsed — fresh allowance
            if week_count >= TOP_RARITY_WEEKLY_BUY_LIMIT:
                reset_at = datetime.fromtimestamp(week_start + TOP_RARITY_WEEK_SECONDS, TZ).strftime("%Y-%m-%d %H:%M")
                return False, (
                    f"❌ <b>🎞☀️🦚 Rarity No.1–3 (ULTRASTAR/LEGENDSUN/MYTHICALPEACOCK) ကို တစ်ပတ်လျှင် "
                    f"{TOP_RARITY_WEEKLY_BUY_LIMIT} ကတ် ပေါင်းစပ်၍သာ Owner Shop ကနေ ဝယ်နိုင်ပါတယ် — ပြည့်သွားပါပြီ။</b>\n"
                    f"⏳ <i>{reset_at} မှ ပြန်ဝယ်နိုင်ပါမယ်။</i>"
                )
    price = vlt_price_for_char(char_doc)
    if not await try_deduct_vlt(buyer_id, price):
        buyer_doc = await users_catcher_col.find_one({"user_id": buyer_id})
        have = buyer_doc.get("vlt_balance", 0) if buyer_doc else 0
        return False, (
            f"❌ <b>VLT မလုံလောက်ပါ။</b>\n"
            f"လိုအပ်: <code>{price}💠</code> | လက်ရှိရှိ: <code>{format_vlt_plain(have)}</code>\n"
            f"💡 <code>/buyvlt [VLT amount]</code> ဖြင့် ⭐ Star ကို 💠 VLT အဖြစ် လဲလှယ်နိုင်ပါတယ်။"
        )
    await users_catcher_col.update_one(
        {"user_id": buyer_id},
        {"$inc": {"total_caught": 1, "total_buys": 1},  # total_buys feeds Squad Points
         "$push": {"harem": {"char_id": char_id, "caught_date": time.time(), "rarity": char_doc.get("rarity", "Unknown"), "status": "vault"}}},
        upsert=True
    )
    # 🩹 FIX: a Shop purchase used to never touch characters_base_col.spawn_count at all —
    # meaning a capped character's spawn_limit only ever stopped WILD spawns, and the Shop
    # could keep minting fresh copies of it forever regardless. Counting purchases here too
    # is what makes the char_limit block above (and ULTRA's default cap) actually mean
    # something once a tier is ever re-enabled via /buytoggle. Deliberately NOT invalidating
    # the character cache here — same accepted few-minutes-stale tradeoff as the wild-catch
    # increment already relies on (see CHAR_CACHE_TTL), rather than busting the hot spawn-
    # eligibility cache on every single purchase.
    await characters_base_col.update_one({"char_id": char_id}, {"$inc": {"spawn_count": 1}})
    premium_note = ""
    if buyer_id != OWNER_ID:
        # Roll the daily buy counter over first if it's a new day, then increment both it and
        # the lifetime per-character counter.
        await users_catcher_col.update_one(
            {"user_id": buyer_id, "buy_date": {"$ne": today_str}},
            {"$set": {"buy_date": today_str, "daily_buy_count": 0}}
        )
        await users_catcher_col.update_one(
            {"user_id": buyer_id},
            {"$inc": {"daily_buy_count": 1, f"lifetime_char_buys.{char_id}": 1}}
        )
        if char_tier in TOP_RARITY_WEEKLY_TIERS:
            # Roll the weekly window over first if it has elapsed, then increment.
            await users_catcher_col.update_one(
                {"user_id": buyer_id, "$or": [
                    {"top_rarity_week_start": {"$exists": False}},
                    {"top_rarity_week_start": {"$lte": now - TOP_RARITY_WEEK_SECONDS}}
                ]},
                {"$set": {"top_rarity_week_start": now, "top_rarity_buys_this_week": 0}}
            )
            await users_catcher_col.update_one(
                {"user_id": buyer_id},
                {"$inc": {"top_rarity_buys_this_week": 1}}
            )
        newly_premium_until = await _track_star_activity_and_maybe_grant_premium(buyer_id, "spent", price)
        if newly_premium_until:
            premium_note = f"\n\n👑 <b>💠 သုံးစွဲမှု များပြားလို့ Bot Premium User {PREMIUM_AUTO_GRANT_DAYS}ရက် အပိုရရှိပါပြီ!</b>"
    # Owner owns every VLT that gets paid for a shop purchase.
    await users_catcher_col.update_one({"user_id": OWNER_ID}, {"$inc": {"vlt_balance": price}}, upsert=True)
    await star_purchase_log_col.insert_one({
        "buyer_id": buyer_id, "char_id": char_id, "char_name": char_doc.get("name", "?"),
        "rarity": char_doc.get("rarity", "Unknown"), "vlt_paid": price, "timestamp": time.time()
    })
    return True, (
        f"🎉 <b>ဝယ်ယူမှု အောင်မြင်ပါတယ်!</b>\n"
        f"<blockquote>"
        f"✨ <b>Name:</b> <code>{escape_html(char_doc.get('name',''))}</code>\n"
        f"🆔 <b>ID:</b> <code>{display_char_id(char_id)}</code>\n"
        f"{char_doc.get('rarity','')}\n"
        f"💸 <b>ပေးချေ:</b> <code>{price}💠</code>"
        f"</blockquote>\n"
        f"<code>/harem</code> <i>ဖြင့် ကြည့်နိုင်ပါတယ်။</i>"
        f"{premium_note}"
    )

# ---- /buytoggle: owner switches a rarity tier's Owner Shop purchase on/off ("sold out").
# Blocks BOTH /buy [char_id] and the /show gallery's 🛒 Buy button for that tier — catching it
# from a live spawn is never affected. Bare /buytoggle shows current status of all 9 tiers. ----
def _buytoggle_status_text():
    lines = ["🛍️ <b>Owner Shop — Rarity On/Off Status</b>\n"]
    for tier in RARITY_TIERS:
        num = RARITY_TIER_TO_NUM[tier]
        emoji = RARITY_EMOJI.get(tier, RARITY_DEFAULT_EMOJI)
        state = "🚫 <b>ပိတ်ထား</b> (Sold out)" if tier in _cached_disabled_buy_tiers else "✅ <b>ဖွင့်ထား</b>"
        lines.append(f"No.{num} {emoji} {tier} — {state}")
    lines.append("\n<b>Usage:</b> <code>/buytoggle no1</code> <i>(No.1..No.9 တစ်ခုချင်းစီကို ပိတ်/ဖွင့် Toggle)</i>")
    return "\n".join(lines)

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]buytoggle(?:@\w+)?(?:\s+(?:no)?([1-9]))?$', 'bot1')))
async def buy_tier_toggle_handler(event):
    if event.sender_id != OWNER_ID: return
    global _cached_disabled_buy_tiers
    arg = event.pattern_match.group(1)
    if not arg:
        return await event.reply(_buytoggle_status_text(), parse_mode='html')
    tier = RARITY_TIERS[int(arg) - 1]
    if tier in _cached_disabled_buy_tiers:
        _cached_disabled_buy_tiers.discard(tier)
        action = "✅ <b>ပြန်ဖွင့်လိုက်ပါပြီ</b> — ဝယ်လို့ရပါပြီ"
    else:
        _cached_disabled_buy_tiers.add(tier)
        action = "🚫 <b>ပိတ်လိုက်ပါပြီ</b> — အရောင်းကုန် ဖြစ်သွားပါပြီ"
    await bot_settings_col.update_one(
        {"_id": "disabled_buy_tiers"},
        {"$set": {"tiers": sorted(_cached_disabled_buy_tiers)}},
        upsert=True
    )
    emoji = RARITY_EMOJI.get(tier, RARITY_DEFAULT_EMOJI)
    await event.reply(
        f"{emoji} <b>{tier}</b> (No.{arg}) — {action}\n\n" + _buytoggle_status_text(),
        parse_mode='html'
    )

# ==========================================
# 🔥 /sell [char_id] — SCRAP. A guaranteed, instant, no-negotiation floor: melt the card down
# for scrap 💠 VLT if you just want it gone. (Player-to-player Player Market listings/p2p
# trading were retired 2026-08 — see PLAYER MARKET RETIREMENT note near marketplace_col.)
# 🩹 2026-08 MARKET RETIREMENT: this used to be an "Owner Offer" — the Owner's own vlt_balance
# paid the player, at 5%-35% of Owner Shop price. Per owner directive, scrapping no longer
# touches the Owner's balance at all (the VLT is minted fresh for the scrapper, same as a
# catch/quiz/daily reward would be — nothing is transferred FROM anyone), and the payout is
# far lower (1%-6%), a genuine last resort rather than a competitive way to offload cards.
# ==========================================
SCRAP_MIN_MULT = 0.01
SCRAP_MAX_MULT = 0.06
SCRAP_OFFER_TIMEOUT = 300  # seconds an offer stays open before it silently expires

def scrap_price_for_char(char_doc):
    """The GUARANTEED FLOOR for a card — what /sell scraps it for at the worst possible roll.
    Also used as the low end of the range /check shows — nobody should ever rationally sell
    for less than this, since scrapping is always available instead."""
    return round(vlt_price_for_char(char_doc) * SCRAP_MIN_MULT, 2)

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]sell(?:@\w+)?$', 'bot1')))
async def sell_bare_usage_handler(event):
    await event.reply(
        "📌 <b>Usage:</b> <code>/sell [CharID]</code>\n"
        "<i>Example:</i> <code>/sell 1234</code>\n"
        "🔥 ကဒ်ကို scrap 💠 VLT အဖြစ် ချက်ချင်း ဖျက်ပြောင်းပေးပါမယ်။",
        parse_mode='html'
    )

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]sell(?:@\w+)?\s+([a-zA-Z0-9_]+)\s+(\d+(?:\.\d+)?)$', 'bot1')))
async def sell_legacy_price_handler(event):
    """Catches the OLD '/sell [char_id] [price]' syntax so people typing it out of habit get
    a clear explanation instead of silence — /sell only ever scraps at the fixed floor now."""
    char_id = event.pattern_match.group(1)
    await event.reply(
        f"⚠️ <b>/sell က ကိုယ်တိုင်စျေးသတ်မှတ်လို့ မရပါ — scrap ဖျက်ခြင်းသာ ဖြစ်ပါတယ်။</b>\n"
        f"📌 Scrap ဖျက်ဖို့: <code>/sell {char_id}</code>",
        parse_mode='html'
    )

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]sell(?:@\w+)?\s+([a-zA-Z0-9_]+)$', 'bot1')))
async def sell_offer_handler(event):
    user_id = event.sender_id
    char_id = normalize_char_id_input(event.pattern_match.group(1))
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    user_harem = user_doc.get("harem", []) if user_doc else []
    char_item = next((x for x in user_harem if isinstance(x, dict) and x.get("char_id") == char_id and x.get("status") == "vault"), None)
    if not char_item: return await event.reply(f"❌ <b>You don’t have this card available.</b>", parse_mode='html')
    char_data = await characters_base_col.find_one({"char_id": char_id})
    if not char_data: return await event.reply(f"❌ <b>Character ID not found.</b>", parse_mode='html')
    base_price = vlt_price_for_char(char_data)
    offer = round(random.uniform(SCRAP_MIN_MULT, SCRAP_MAX_MULT) * base_price, 2)
    sell_id = f"S{random.randint(100000, 999999)}"
    pending_sell_offers[sell_id] = {
        "expiry": time.time() + SCRAP_OFFER_TIMEOUT,
        "seller_id": user_id, "char_id": char_id, "char_name": char_data.get("name", "?"),
        "offer": offer,
    }
    buttons = [[
        colored_button("🔥 Scrap လုပ်မယ်", f"sellconf_{sell_id}", color="danger"),
        Button.inline("❌ မလုပ်တော့ဘူး", data=f"sellcanc_{sell_id}")
    ]]
    await event.reply(
        f"🔥 <b>SCRAP</b>\n"
        f"<blockquote>"
        f"👤 <b>Card:</b> <code>{escape_html(char_data.get('name','?'))}</code> [<code>{display_char_id(char_id)}</code>]\n"
        f"💠 <b>Scrap value:</b> <code>{format_vlt_plain(offer)}</code>\n"
        f"💡 <i>Floor price: {format_vlt_plain(scrap_price_for_char(char_data))} ~ {base_price}💠</i>"
        f"</blockquote>\n"
        f"<i>ကဒ်ကို ဖျက်ပြီး scrap ယူမလား? ← ဒါက ပြန်ပြင်လို့ မရတော့ပါ</i>",
        parse_mode='html', buttons=buttons
    )

@bot1.on(events.CallbackQuery(pattern=r'^sellconf_(\S+)$'))
async def sell_offer_confirm_callback(event):
    sell_id = event.pattern_match.group(1)
    if isinstance(sell_id, bytes): sell_id = sell_id.decode('utf-8')
    offer_data = pending_sell_offers.get(sell_id)
    if not offer_data or time.time() > offer_data["expiry"]:
        pending_sell_offers.pop(sell_id, None)
        return await event.answer("⏳ This offer has expired. Run /sell again.", alert=True)
    if event.sender_id != offer_data["seller_id"]:
        return await event.answer("⚠️ This isn't your offer!", alert=True)
    if not claim_single_tap(event):
        return await event.answer("⏳ Already processing...", alert=False)
    seller_id, char_id, offer = offer_data["seller_id"], offer_data["char_id"], offer_data["offer"]
    user_doc = await users_catcher_col.find_one({"user_id": seller_id})
    user_harem = user_doc.get("harem", []) if user_doc else []
    char_item = next((x for x in user_harem if isinstance(x, dict) and x.get("char_id") == char_id and x.get("status") == "vault"), None)
    if not char_item:
        pending_sell_offers.pop(sell_id, None)
        await event.answer("❌ Card no longer available.", alert=True)
        return await event.edit("❌ <b>ဒီကဒ်ကို ရှာမတွေ့တော့ပါ။</b>", parse_mode='html', buttons=None)
    # 🩹 No owner deduction — scrap VLT is minted fresh for the scrapper (see block header
    # note above), same as any other reward. The card is simply destroyed.
    # Same atomic $pull-based removal as marketplace-buy/trade — see remove_one_harem_copy.
    await remove_one_harem_copy(seller_id, char_id, "vault")
    await users_catcher_col.update_one({"user_id": seller_id}, {"$inc": {"vlt_balance": offer}}, upsert=True)
    seller_doc_after = await users_catcher_col.find_one({"user_id": seller_id}, {"harem": 1})
    await clear_stale_favorite(seller_id, char_id, (seller_doc_after or {}).get("harem", []))
    await star_sell_log_col.insert_one({
        "seller_id": seller_id, "char_id": char_id, "char_name": offer_data["char_name"],
        "vlt_paid": offer, "timestamp": time.time()
    })
    pending_sell_offers.pop(sell_id, None)
    await event.answer("🔥 Scrapped!", alert=True)
    await event.edit(
        f"🔥 <b>Scrap ဖြစ်သွားပါပြီ!</b>\n"
        f"<blockquote>"
        f"👤 <b>Card:</b> <code>{escape_html(offer_data['char_name'])}</code>\n"
        f"💠 <b>ရရှိ:</b> <code>{format_vlt_plain(offer)}</code>"
        f"</blockquote>",
        parse_mode='html', buttons=None
    )

@bot1.on(events.CallbackQuery(pattern=r'^sellcanc_(\S+)$'))
async def sell_offer_cancel_callback(event):
    sell_id = event.pattern_match.group(1)
    if isinstance(sell_id, bytes): sell_id = sell_id.decode('utf-8')
    offer_data = pending_sell_offers.get(sell_id)
    if offer_data and event.sender_id != offer_data["seller_id"]:
        return await event.answer("⚠️ This isn't your offer!", alert=True)
    pending_sell_offers.pop(sell_id, None)
    await event.answer("👍 Kept.")
    await event.edit("🙅 <b>Scrap လုပ်ခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ — ကဒ်ကို ဆက်ထားနိုင်ပါတယ်။</b>", parse_mode='html', buttons=None)

# ==========================================
# 🏪 PLAYER MARKET RETIREMENT (2026-08, owner directive) — p2p card trading (/list, /unlist,
# /mylistings, the marketplace_data collection, the /market "Player Market" button, and the
# mktbuy_ purchase callback) has been removed entirely. /sell (scrap) and the Owner Shop
# (/buy, /show gallery) are now the only ways to convert cards <-> 💠 VLT; the /trade command
# (direct card-for-card swap, no VLT involved) is untouched — that was never part of Market.
# See run_player_market_retirement() near startup for the one-time migration that returns any
# still-active listings back to their sellers' vaults.
# ==========================================

async def set_one_harem_copy_status(user_id, char_id, from_status, to_status):
    """Atomically transitions exactly ONE harem copy matching (char_id, from_status) to
    to_status — the $set sibling of remove_one_harem_copy's $unset+$pull pattern (see that
    function's docstring for why atomicity matters here: no find→modify→write-back race).
    Used by run_player_market_retirement() to hand any still-"market"-status copies back to
    their owners' vaults. Returns True if a matching copy was found and updated, False
    otherwise (e.g. already reverted, already sold, never owned)."""
    result = await users_catcher_col.update_one(
        {"user_id": user_id, "harem": {"$elemMatch": {"char_id": char_id, "status": from_status}}},
        {"$set": {"harem.$.status": to_status}}
    )
    return result.modified_count > 0

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]buy(?:@\w+)?$', 'bot1')))
async def buy_bare_usage_handler(event):
    user_id = event.sender_id
    buttons = [
        [Button.inline("🎴 ကဒ်များ", data=f"buyhub_cards_{user_id}")],
        [Button.inline("💠 ကဒ်ဝယ်ဖို့ VLT ဝယ်မယ်", data=f"buyhub_star_{user_id}")],
        [Button.inline("👑 Premium ဝယ်မယ်", data=f"buyhub_premium_{user_id}")],
    ]
    await event.reply(
        "🛍️ <b>ဘာကိုဝယ်ချင်ပါသလဲ?</b>",
        parse_mode='html',
        buttons=buttons
    )

@bot1.on(events.CallbackQuery(pattern=r'^buyhub_(cards|star|premium)_(\d+)$'))
async def buy_hub_callback(event):
    action = event.pattern_match.group(1)
    if isinstance(action, bytes): action = action.decode('utf-8')
    owner_uid = int(event.pattern_match.group(2))
    if event.sender_id != owner_uid:
        return await event.answer("⚠️ This isn't your menu!", alert=True)
    await event.answer()
    if action == "cards":
        await event.edit(
            "🎴 <b>ကဒ်များ ဝယ်ရန်</b>\n"
            "📩 Bot DM မှာ <code>/show</code> ကိုနှိပ်ပြီး Rarity ရွေးပြီး ကဒ်တွေ့ကြည့်နိုင်ပါတယ်။\n"
            "ကြိုက်တဲ့ကဒ်တွေ့ရင် <code>/buy [char id]</code> နဲ့ 💠 VLT ဖြင့်ဝယ်နိုင်ပါတယ်။",
            parse_mode='html', buttons=None
        )
    elif action == "premium":
        text, buttons = render_premium_tiers_text_and_buttons(owner_uid)
        await event.edit(text, parse_mode='html', buttons=buttons)
    else:
        market = await get_or_create_vlt_market()
        await event.edit(
            f"💠 <b>VLT ဝယ်ရန်</b>\n"
            f"💱 <b>လက်ရှိစျေးနှုန်း:</b> <code>1💠 = {market['rate']:,.2f}⭐</code>\n"
            f"📌 <code>/buyvlt [VLT amount]</code> ဟုရိုက်ပြီး ⭐ Star ဖြင့် VLT ဝယ်နိုင်ပါတယ်။\n"
            f"<i>Example:</i> <code>/buyvlt 1</code>",
            parse_mode='html', buttons=None
        )

@bot1.on(events.CallbackQuery(pattern=r'^shopbuy_([a-zA-Z0-9_]+)$'))
async def shop_buy_gallery_callback(event):
    char_id = event.pattern_match.group(1)
    if isinstance(char_id, bytes): char_id = char_id.decode('utf-8')
    if not claim_single_tap(event):
        return await event.answer("⏳ Already processing...", alert=False)
    ok, msg = await execute_star_shop_purchase(event.sender_id, char_id)
    await event.answer("🎉 Purchased!" if ok else "❌ Failed", alert=True)
    await bot1.send_message(event.sender_id if event.is_private else event.chat_id, msg, parse_mode='html')

# ---- /buy [char id] — Owner Shop purchase: buy a fresh copy of any character directly with
# 💠 VLT, priced by rarity (see vlt_price_for_char). ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]buy(?:@\w+)?\s+([a-zA-Z0-9_]+)$', 'bot1')))
async def buy_shop_handler(event):
    char_id = normalize_char_id_input(event.pattern_match.group(1))
    ok, msg = await execute_star_shop_purchase(event.sender_id, char_id)
    await event.reply(msg, parse_mode='html')

# ---- /buy [char id] [seller id] — DISABLED. Peer-to-peer card trading no longer exists
# (see /sell, which is now an Owner-only buyback); this old pattern is kept solely so anyone
# still typing it out of habit gets a clear explanation instead of silence. ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]buy(?:@\w+)?\s+([a-zA-Z0-9_]+)\s+(\d+)$', 'bot1')))
async def buy_market_handler(event):
    await event.reply(
        "⚠️ <b>Player အချင်းချင်း ကဒ်ရောင်းဝယ်ခြင်းကို ရပ်ဆိုင်းလိုက်ပါပြီ။</b>\n"
        "📌 <code>/buy [CharID]</code> ဖြင့် Owner Shop ကနေ တိုက်ရိုက်ဝယ်ပါ။",
        parse_mode='html'
    )

# ==========================================
# 👑 /buypremium — buy Bot Premium User status with ⭐ Star, across 5 duration tiers.
# Premium can ALSO be earned automatically (see _track_star_activity_and_maybe_grant_premium)
# by spending/buying PREMIUM_AUTO_STAR_THRESHOLD⭐ in a single day.
# ==========================================
def _premium_tier_label(months):
    return "1 နှစ်" if months == 12 else f"{months} လ"

def render_premium_tiers_text_and_buttons(user_id):
    lines = [f"• {_premium_tier_label(m)} — <code>{p}⭐</code>" for m, p, _ in PREMIUM_TIERS]
    buttons = [[Button.inline(f"👑 {_premium_tier_label(m)} — {p}⭐", data=f"prembuy_{m}_{user_id}")] for m, p, _ in PREMIUM_TIERS]
    text = (
        f"👑 <b>BOT PREMIUM USER</b>\n"
        f"<blockquote>{chr(10).join(lines)}</blockquote>\n"
        f"✨ <b>အကျိုးခံစားခွင့်များ:</b>\n"
        f"• 🕒 Spam Cooldown <code>8 min → 3 min</code>\n"
        f"• 🎯 Daily Catch <code>22 → 25</code> ကြိမ်\n"
        f"• 🎁 ဝယ်ဝယ်ချင်း 🌕 <b>GOLDENMOON (No.6)</b> ကဒ် <code>{PREMIUM_PURCHASE_BONUS_COUNT}</code> ကဒ် အခမဲ့\n"
        f"• ⭐ <b>နေ့စဉ်</b> Star <code>{PREMIUM_DAILY_GIFT_MIN}~{PREMIUM_DAILY_GIFT_MAX}⭐</code> Random လက်ဆောင်\n"
        f"• 🧠 Quiz Star ဆု ပိုများ\n"
        f"• 👑 Premium Badge — <code>/harem</code>, <code>/profile</code>, <code>/obtain</code> တွေမှာ ပြပေးမယ်\n"
        f"👇 <i>သက်တမ်း ရွေးချယ်ပါ</i>"
    )
    return text, buttons

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]buypremium(?:@\w+)?$', 'bot1')))
async def buypremium_handler(event):
    user_id = event.sender_id
    await ensure_user_registered(user_id, await get_plain_name(event, user_id))
    text, buttons = render_premium_tiers_text_and_buttons(user_id)
    await event.reply(text, parse_mode='html', buttons=buttons)

@bot1.on(events.CallbackQuery(pattern=r'^prembuy_(\d+)_(\d+)$'))
async def premium_purchase_callback(event):
    months = int(event.pattern_match.group(1))
    owner_uid = int(event.pattern_match.group(2))
    if event.sender_id != owner_uid:
        return await event.answer("⚠️ This isn't your menu!", alert=True)
    tier = next((t for t in PREMIUM_TIERS if t[0] == months), None)
    if not tier:
        return await event.answer("❌ Invalid tier.", alert=True)
    if not claim_single_tap(event):
        return await event.answer("⏳ Already processing...", alert=False)
    _, price, days = tier
    if not await try_deduct_star(owner_uid, price):
        buyer_doc = await users_catcher_col.find_one({"user_id": owner_uid})
        have = buyer_doc.get("star_balance", 0) if buyer_doc else 0
        return await event.answer(f"❌ Star မလုံလောက်ပါ။ လိုအပ်: {price}⭐ | ရှိ: {format_star_plain(have)}", alert=True)
    new_until = await grant_premium_days(owner_uid, days)
    # Owner receives the Star paid for Premium too, same as every other Star sink in this economy.
    await users_catcher_col.update_one({"user_id": OWNER_ID}, {"$inc": {"star_balance": price}}, upsert=True)
    
    expiry_str = datetime.fromtimestamp(new_until, TZ).strftime("%Y-%m-%d %H:%M")
    await event.answer("👑 Premium Activated!", alert=True)
    await event.edit(
        f"👑 <b>BOT PREMIUM ACTIVATED!</b>\n"
        f"<blockquote>"
        f"⏳ <b>သက်တမ်း:</b> <code>{expiry_str}</code> အထိ"
        f"</blockquote>\n"
        f"🎉 <i>Premium အကျိုးခံစားခွင့်များ ချက်ချင်း စတင်အသုံးပြုနိုင်ပါပြီ!</i>",
        parse_mode='html', buttons=None
    )

# ==========================================
# 🎁 DAILY FIRST-MESSAGE REWARDS — once per day, the FIRST message ANY user sends anywhere the
# bots can see them triggers a check for one opt-in offer with its own ✅/❌ button:
#   PREMIUM STAR GIFT — Premium users get a small random ⭐ Star gift.
# (🩹 2026-08, per owner request: this used to also offer a LIMITED EDITION VLT CLAIM for
# is_limited_edition card owners — that payout is gone. The LE tagging system itself, /addle,
# /mergele, and the harem gallery are unaffected.)
# Public and visible on purpose. Runs on BOTH bots (2026-08, per owner request): bot3
# (Guard Bot) owns FORCE_SUB_CHAT_ID specifically, bot1 covers every other group it's in — see
# _daily_reward_trigger_bot3 / _bot1 below for the split. If bot3 isn't configured at all,
# bot1's version alone covers every group.
#
# ⚡ PERFORMANCE: the overwhelming majority of messages are from users who aren't Premium, so
# this is designed to cost ZERO database reads for that case after their first message of the
# day. daily_first_msg_gate (in BotState) is an in-memory {user_id: date_str} set the INSTANT a
# user's first message of the day is seen — with no `await` between the check and the set, so a
# burst of that same user's messages across many groups in the same moment can only ever pass
# through once, not once per message — and every later message that same day is a single dict
# lookup, nothing more. This is purely a speed short-circuit: the actual once-per-day guarantee
# still comes from premium_gift_date in Mongo via an atomic find_one_and_update, so a bot
# restart mid-day just costs one extra DB read per user on their next message, never a
# double-grant.
# ==========================================
PREMIUM_DAILY_GIFT_MIN = 100  # ⭐
PREMIUM_DAILY_GIFT_MAX = 200  # ⭐

async def _check_daily_first_message_rewards(event, user_id):
    """The one and only DB-touching half of the flow above — see the section docstring for
    the performance reasoning. Evaluates the Premium Star gift off a single fetched user_doc."""
    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    project = {"premium_until": 1, "premium_gift_date": 1}
    user_doc = await users_catcher_col.find_one({"user_id": user_id}, project)
    if not user_doc:
        return

    # ---- 1. Premium Star gift ----
    if is_premium_active(user_doc) and user_doc.get("premium_gift_date") != today_str:
        # Atomic claim on today's slot — only the update that actually flips premium_gift_date
        # wins, so a burst of qualifying messages in the same moment can't send this twice.
        claimed = await users_catcher_col.find_one_and_update(
            {"user_id": user_id, "premium_gift_date": {"$ne": today_str}},
            {"$set": {"premium_gift_date": today_str}}
        )
        if claimed:
            star_amount = round(random.uniform(PREMIUM_DAILY_GIFT_MIN, PREMIUM_DAILY_GIFT_MAX), 2)
            gift_id = f"G{random.randint(100000, 999999)}"
            pending_premium_gifts[gift_id] = {"expiry": time.time() + 86400, "user_id": user_id, "star_amount": star_amount}
            buttons = [[
                colored_button("✅ ယူမယ်", f"premgift_take_{gift_id}", color="success"),
                Button.inline("❌ မယူဘူး", data=f"premgift_skip_{gift_id}")
            ]]
            try:
                await event.reply(
                    f"👑 <b>ရော့! မင်းက Bot Premium ဝယ်ထားတဲ့ User မို့ Bot Creator က မင်းကို နေ့တိုင်း Star ပေးခိုင်းထားတယ်</b>\n"
                    f"⭐ <b>ဒီနေ့ မင်းရမှာ:</b> <code>{star_amount}⭐</code>",
                    parse_mode='html', buttons=buttons
                )
            except Exception:
                pass

    # ---- 2. Limited Edition VLT claim ---- REMOVED (per owner request, 2026-08): LE cards no
    # longer pay their owner daily VLT at all. See get_le_rate_map's old docstring / this
    # function's own history if this ever needs reviving — the LE tagging system itself
    # (/addle, /mergele, the 🎗️ Limited Editions harem gallery) is untouched and still works;
    # only the VLT payout that used to ride on top of it is gone.

async def _daily_reward_trigger_bot3(event):
    if event.is_private: return
    # Running on the Guard Bot: force-join group only, any message qualifies as the day's
    # "first interaction" — bot3 has no /collect of its own to key off of.
    if event.chat_id != FORCE_SUB_CHAT_ID: return
    user_id = event.sender_id
    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    if daily_first_msg_gate.get(user_id) == today_str: return
    daily_first_msg_gate[user_id] = today_str  # set BEFORE the await — see section docstring
    try:
        await _check_daily_first_message_rewards(event, user_id)
    except Exception as e:
        print(f"Daily reward trigger (bot3) error: {e}")

async def _daily_reward_trigger_bot1(event):
    """🩹 NEW (per owner request): bot3's version above only ever covers FORCE_SUB_CHAT_ID —
    this twin runs on bot1 so both rewards get offered on the SAME first message-of-the-day
    trigger in every OTHER group bot1 sits in too. Skips FORCE_SUB_CHAT_ID itself when bot3
    exists — that group is already bot3's job, no need to check it twice."""
    if event.is_private: return
    if bot3 is not None and event.chat_id == FORCE_SUB_CHAT_ID: return
    user_id = event.sender_id
    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    if daily_first_msg_gate.get(user_id) == today_str: return
    daily_first_msg_gate[user_id] = today_str  # set BEFORE the await — see section docstring
    try:
        await _check_daily_first_message_rewards(event, user_id)
    except Exception as e:
        print(f"Daily reward trigger (bot1) error: {e}")

async def premium_gift_take_callback(event):
    gift_id = event.pattern_match.group(1)
    if isinstance(gift_id, bytes): gift_id = gift_id.decode('utf-8')
    gift = pending_premium_gifts.get(gift_id)
    if not gift or time.time() > gift["expiry"]:
        pending_premium_gifts.pop(gift_id, None)
        return await event.answer("⏳ ဒီ Offer သက်တမ်းကုန်သွားပါပြီ။", alert=True)
    if event.sender_id != gift["user_id"]:
        return await event.answer("⚠️ ဒါ မင်းအတွက် မဟုတ်ဘူး!", alert=True)
    if not claim_single_tap(event):
        return await event.answer("⏳ Processing...", alert=False)
    await users_catcher_col.update_one({"user_id": gift["user_id"]}, {"$inc": {"star_balance": gift["star_amount"]}}, upsert=True)
    pending_premium_gifts.pop(gift_id, None)
    await event.answer("🎉 Star ရပါပြီ!", alert=True)
    await event.edit(f"✅ <b>+{gift['star_amount']}⭐ ရရှိပါပြီ! 👑</b>", parse_mode='html', buttons=None)

async def premium_gift_skip_callback(event):
    gift_id = event.pattern_match.group(1)
    if isinstance(gift_id, bytes): gift_id = gift_id.decode('utf-8')
    gift = pending_premium_gifts.get(gift_id)
    if gift and event.sender_id != gift["user_id"]:
        return await event.answer("⚠️ ဒါ မင်းအတွက် မဟုတ်ဘူး!", alert=True)
    pending_premium_gifts.pop(gift_id, None)
    await event.answer("👍 OK")
    await event.edit("🙅 <b>ငြင်းလိုက်ပါပြီ။</b>", parse_mode='html', buttons=None)

# le_claim_take_callback / le_claim_skip_callback — REMOVED along with the LE VLT claim
# system above (2026-08, per owner request). See _check_daily_first_message_rewards.

# 🩹 NEW (per owner request): this now runs on BOTH bots at once, not one-or-the-other —
# bot3 (if configured) keeps owning FORCE_SUB_CHAT_ID exactly as before, and bot1 now ALSO
# gets it for every other group it's in. If bot3 isn't configured at all, bot1's version
# simply covers every group on its own.
if bot3 is not None:
    bot3.on(events.NewMessage)(_daily_reward_trigger_bot3)
bot1.on(events.NewMessage)(_daily_reward_trigger_bot1)
# 🩹 The ✅/❌ buttons on the Premium Star gift offer must be handled by WHICHEVER bot actually
# SENT that offer message (Telegram routes a callback tap to the bot account that owns the
# message it's attached to) — since bot1 can now send these offers too (not just bot3), both
# bots need this handler registered, not just one.
bot1.on(events.CallbackQuery(pattern=r'^premgift_take_(\S+)$'))(premium_gift_take_callback)
bot1.on(events.CallbackQuery(pattern=r'^premgift_skip_(\S+)$'))(premium_gift_skip_callback)
if bot3 is not None:
    bot3.on(events.CallbackQuery(pattern=r'^premgift_take_(\S+)$'))(premium_gift_take_callback)
    bot3.on(events.CallbackQuery(pattern=r'^premgift_skip_(\S+)$'))(premium_gift_skip_callback)

# ==========================================
# 🧾 /buylist — OWNER ONLY: history of every 💠 Owner Shop purchase (/buy [char id])
# 🩹 2026-08 USD/MARKET RETIREMENT: rows written before this change have "stars_paid" (⭐);
# rows written after have "vlt_paid" (💠) — both are read and labeled correctly below.
# ==========================================
BUYLIST_PAGE_SIZE = 10

async def render_buylist_page(page):
    skip = page * BUYLIST_PAGE_SIZE
    total = await star_purchase_log_col.count_documents({})
    rows = await star_purchase_log_col.find({}).sort("timestamp", -1).skip(skip).limit(BUYLIST_PAGE_SIZE).to_list(length=BUYLIST_PAGE_SIZE)
    total_pages = max(1, (total + BUYLIST_PAGE_SIZE - 1) // BUYLIST_PAGE_SIZE)
    if not rows:
        body = "<i>မှတ်တမ်းမရှိသေးပါ။</i>"
    else:
        lines = []
        for r in rows:
            dt = datetime.fromtimestamp(r["timestamp"], TZ).strftime("%Y-%m-%d %H:%M")
            paid_str = f"{r['vlt_paid']}💠" if "vlt_paid" in r else f"{r.get('stars_paid', 0)}⭐"
            lines.append(
                f"👤 <code>{r['buyer_id']}</code> — <code>{escape_html(r.get('char_name','?'))}</code> "
                f"[<code>{display_char_id(r['char_id'])}</code>] — <code>{paid_str}</code> — <i>{dt}</i>"
            )
        body = "\n".join(lines)
    text = f"🧾 <b>SHOP BUY HISTORY</b> <i>({min(page + 1, total_pages)}/{total_pages})</i>\n<blockquote>{body}</blockquote>\n💰 <b>Total sales:</b> <code>{total}</code>"
    buttons = []
    nav = []
    if page > 0:
        nav.append(Button.inline("⬅️ Prev", data=f"buylistpg_{page - 1}"))
    if skip + BUYLIST_PAGE_SIZE < total:
        nav.append(Button.inline("➡️ Next", data=f"buylistpg_{page + 1}"))
    if nav:
        buttons.append(nav)
    return text, (buttons or None)

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]buylist(?:@\w+)?$', 'bot1')))
async def buylist_handler(event):
    if event.sender_id != OWNER_ID: return
    text, buttons = await render_buylist_page(0)
    await event.reply(text, parse_mode='html', buttons=buttons)

@bot1.on(events.CallbackQuery(pattern=r'^buylistpg_(\d+)$'))
async def buylist_page_callback(event):
    if event.sender_id != OWNER_ID:
        return await event.answer("⚠️ Owner only.", alert=True)
    page = int(event.pattern_match.group(1))
    text, buttons = await render_buylist_page(page)
    try:
        await event.edit(text, parse_mode='html', buttons=buttons)
        await event.answer()
    except errors.MessageNotModifiedError:
        await event.answer()

# ---- /clearbuylist — OWNER ONLY: purge the /buylist history log (star_purchase_log_col)
# whenever it's grown too big. This is just a rolling audit log, viewed briefly and forgotten —
# it doesn't affect wallet/star balances, owned cards, or anything a player can see. Preview
# shows the record count first; add "confirm" to actually delete. ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]clearbuylist(?:@\w+)?(?:\s+(confirm))?$', 'bot1')))
async def clear_buylist_handler(event):
    if event.sender_id != OWNER_ID: return
    confirm = bool(event.pattern_match.group(1))
    total = await star_purchase_log_col.count_documents({})
    if not total:
        return await event.reply("📭 <b>/buylist History က အလွတ်ပါ — ဖျက်စရာ မရှိပါ။</b>", parse_mode='html')
    if not confirm:
        return await event.reply(
            f"⚠️ <b>/buylist History (<code>{total:,}</code> records) ကို ဖျက်တော့မလား?</b>\n"
            f"<i>ဒါက Database ထဲက audit log ကိုပဲ ဖျက်တာပါ — player wallet balance/owned cards ဘာမှ မထိပါဘူး။</i>\n\n"
            f"အတည်ပြုရန် <code>/clearbuylist confirm</code> ကို ရိုက်ပါ။",
            parse_mode='html'
        )
    result = await star_purchase_log_col.delete_many({})
    await event.reply(
        f"🧹 <b>/buylist History ဖျက်ပြီးပါပြီ!</b>\n🗑️ <code>{result.deleted_count:,}</code> records ဖယ်ရှားလိုက်ပါပြီ။",
        parse_mode='html'
    )

# ==========================================
# 🧾 /starlist — OWNER ONLY: unified history of every ⭐/💠 movement —
# Owner buybacks (/sell accepted offers) AND the now-retired /buystar & /sellstar USD<->Star
# exchanges — newest first, with running totals for each.
# 🩹 2026-08 USD/MARKET RETIREMENT: /sell buybacks now pay 💠 VLT (field "vlt_paid") instead of
# ⭐ Star (field "stars_paid") — old rows written before this change still have "stars_paid"
# and are genuinely Star-denominated, so both are read and labeled separately rather than
# merged into one (mis-)total. /buystar & /sellstar rows are pure history now (both commands
# are retired) — their "USD" labels are accurate and intentionally NOT relabeled to Star.
# ==========================================
STARLIST_PAGE_SIZE = 10

async def render_starlist_page(page):
    skip = page * STARLIST_PAGE_SIZE
    total_cardsell = await star_sell_log_col.count_documents({})
    total_exchange = await star_exchange_log_col.count_documents({})
    total = total_cardsell + total_exchange
    total_pages = max(1, (total + STARLIST_PAGE_SIZE - 1) // STARLIST_PAGE_SIZE)

    pipeline = [
        {"$addFields": {"log_type": "card_sell"}},
        {"$unionWith": {"coll": "star_exchange_log", "pipeline": [{"$addFields": {"log_type": "$type"}}]}},
        {"$sort": {"timestamp": -1}},
        {"$skip": skip},
        {"$limit": STARLIST_PAGE_SIZE},
    ]
    rows = await star_sell_log_col.aggregate(pipeline).to_list(length=STARLIST_PAGE_SIZE)

    buyback_star_agg = await star_sell_log_col.aggregate([{"$match": {"stars_paid": {"$exists": True}}}, {"$group": {"_id": None, "s": {"$sum": "$stars_paid"}}}]).to_list(length=1)
    total_buyback_stars = buyback_star_agg[0]["s"] if buyback_star_agg else 0
    buyback_vlt_agg = await star_sell_log_col.aggregate([{"$match": {"vlt_paid": {"$exists": True}}}, {"$group": {"_id": None, "v": {"$sum": "$vlt_paid"}}}]).to_list(length=1)
    total_buyback_vlt = buyback_vlt_agg[0]["v"] if buyback_vlt_agg else 0
    buy_agg = await star_exchange_log_col.aggregate([{"$match": {"type": "buy"}}, {"$group": {"_id": None, "s": {"$sum": "$star_amount"}, "m": {"$sum": "$mmk_amount"}}}]).to_list(length=1)
    total_bought_star, total_bought_mmk = (buy_agg[0]["s"], buy_agg[0]["m"]) if buy_agg else (0, 0)
    sell_agg = await star_exchange_log_col.aggregate([{"$match": {"type": "sell"}}, {"$group": {"_id": None, "s": {"$sum": "$star_amount"}, "m": {"$sum": "$mmk_amount"}}}]).to_list(length=1)
    total_sold_star, total_sold_mmk = (sell_agg[0]["s"], sell_agg[0]["m"]) if sell_agg else (0, 0)

    if not rows:
        body = "<i>မှတ်တမ်းမရှိသေးပါ။</i>"
    else:
        lines = []
        for r in rows:
            dt = datetime.fromtimestamp(r["timestamp"], TZ).strftime("%Y-%m-%d %H:%M")
            if r["log_type"] == "card_sell":
                if "vlt_paid" in r:
                    paid_str = f"{format_vlt_plain(r['vlt_paid'])}"
                else:
                    paid_str = f"{format_star_plain(r.get('stars_paid', 0))} <i>(legacy)</i>"
                lines.append(
                    f"🏷️ <code>{r['seller_id']}</code> — <code>{escape_html(r.get('char_name','?'))}</code> "
                    f"[<code>{display_char_id(r['char_id'])}</code>] — <code>{paid_str}</code> — <i>{dt}</i>"
                )
            elif r["log_type"] == "buy":
                lines.append(
                    f"⬆️ <code>{r['user_id']}</code> — bought <code>{format_star_plain(r['star_amount'])}</code> "
                    f"for <code>{r['mmk_amount']:,} USD</code> — <i>{dt}</i>"
                )
            else:
                lines.append(
                    f"⬇️ <code>{r['user_id']}</code> — sold <code>{format_star_plain(r['star_amount'])}</code> "
                    f"for <code>{r['mmk_amount']:,} USD</code> — <i>{dt}</i>"
                )
        body = "\n".join(lines)
    text = (
        f"🧾 <b>STAR/VLT ACTIVITY HISTORY</b> <i>({min(page + 1, total_pages)}/{total_pages})</i>\n"
        f"<blockquote>{body}</blockquote>\n"
        f"🔢 <b>Total entries:</b> <code>{total}</code>\n"
        f"🏷️ <b>Card buybacks paid:</b> <code>{format_vlt_plain(total_buyback_vlt)}</code>"
        f" + <code>{format_star_plain(total_buyback_stars)}</code> <i>(legacy, pre-VLT)</i>\n"
        f"⬆️ <b>/buystar</b> <i>(retired, history only)</i>: <code>{format_star_plain(total_bought_star)}</code> bought · <code>{total_bought_mmk:,.0f} USD</code> spent\n"
        f"⬇️ <b>/sellstar</b> <i>(retired, history only)</i>: <code>{format_star_plain(total_sold_star)}</code> sold · <code>{total_sold_mmk:,.0f} USD</code> received"
    )
    buttons = []
    nav = []
    if page > 0:
        nav.append(Button.inline("⬅️ Prev", data=f"starlistpg_{page - 1}"))
    if skip + STARLIST_PAGE_SIZE < total:
        nav.append(Button.inline("➡️ Next", data=f"starlistpg_{page + 1}"))
    if nav:
        buttons.append(nav)
    return text, (buttons or None)

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]starlist(?:@\w+)?$', 'bot1')))
async def starlist_handler(event):
    if event.sender_id != OWNER_ID: return
    text, buttons = await render_starlist_page(0)
    await event.reply(text, parse_mode='html', buttons=buttons)

@bot1.on(events.CallbackQuery(pattern=r'^starlistpg_(\d+)$'))
async def starlist_page_callback(event):
    if event.sender_id != OWNER_ID:
        return await event.answer("⚠️ Owner only.", alert=True)
    page = int(event.pattern_match.group(1))
    text, buttons = await render_starlist_page(page)
    try:
        await event.edit(text, parse_mode='html', buttons=buttons)
        await event.answer()
    except errors.MessageNotModifiedError:
        await event.answer()

# ---- /clearstarlist — OWNER ONLY: purge the /starlist history logs (both star_sell_log_col —
# card buybacks — AND star_exchange_log_col — /buystar & /sellstar — since /starlist displays
# them merged together). Same rolling-audit-log reasoning as /clearbuylist: viewed briefly,
# doesn't affect any player's actual star/wallet balance. Preview first; add "confirm" to
# actually delete. ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]clearstarlist(?:@\w+)?(?:\s+(confirm))?$', 'bot1')))
async def clear_starlist_handler(event):
    if event.sender_id != OWNER_ID: return
    confirm = bool(event.pattern_match.group(1))
    total_sell = await star_sell_log_col.count_documents({})
    total_exchange = await star_exchange_log_col.count_documents({})
    total = total_sell + total_exchange
    if not total:
        return await event.reply("📭 <b>/starlist History က အလွတ်ပါ — ဖျက်စရာ မရှိပါ။</b>", parse_mode='html')
    if not confirm:
        return await event.reply(
            f"⚠️ <b>/starlist History ကို ဖျက်တော့မလား?</b>\n"
            f"🏷️ <b>Card buyback records:</b> <code>{total_sell:,}</code>\n"
            f"💱 <b>Star exchange records:</b> <code>{total_exchange:,}</code>\n"
            f"🧾 <b>Total:</b> <code>{total:,}</code>\n\n"
            f"<i>ဒါက Database ထဲက audit log ကိုပဲ ဖျက်တာပါ — player star/wallet balance ဘာမှ မထိပါဘူး။</i>\n\n"
            f"အတည်ပြုရန် <code>/clearstarlist confirm</code> ကို ရိုက်ပါ။",
            parse_mode='html'
        )
    r1 = await star_sell_log_col.delete_many({})
    r2 = await star_exchange_log_col.delete_many({})
    await event.reply(
        f"🧹 <b>/starlist History ဖျက်ပြီးပါပြီ!</b>\n🗑️ <code>{(r1.deleted_count + r2.deleted_count):,}</code> records ဖယ်ရှားလိုက်ပါပြီ။",
        parse_mode='html'
    )

# ==========================================
# 🩹 2026-08 USD RETIREMENT — /buystar, /sellstar & /setstarrate retired along with USD.
# Kept as short redirect stubs (instead of deleting the commands outright) so anyone still
# typing them out of habit gets a clear explanation instead of silence. See /buyvlt & /sellvlt
# below for the live exchange mechanism that replaces all three of these.
# ==========================================
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]buystar(?:@\w+)?(?:\s+(\S+))?$', 'bot1')))
async def buy_star_handler(event):
    await event.reply(
        "💵 <b>USD ကို ဒီ Bot မှာ ရပ်ဆိုင်းလိုက်ပါပြီ — /buystar အလုပ်မလုပ်တော့ပါ။</b>\n"
        "⭐ Star ကို ဆော့ကစားမှု (catch, quiz, daily, casino...) တွေကနေ ရရှိနိုင်ပြီး "
        "<code>/buyvlt [VLT amount]</code> ဖြင့် 💠 VLT အဖြစ် လဲလှယ်နိုင်ပါတယ်။",
        parse_mode='html'
    )

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]sellstar(?:@\w+)?(?:\s+(\S+))?$', 'bot1')))
async def sell_star_handler(event):
    await event.reply(
        "💵 <b>USD ကို ဒီ Bot မှာ ရပ်ဆိုင်းလိုက်ပါပြီ — /sellstar အလုပ်မလုပ်တော့ပါ။</b>\n"
        "💠 VLT ကို ⭐ Star အဖြစ် ပြန်လဲလှယ်ချင်ရင် <code>/sellvlt [VLT amount]</code> ကို သုံးပါ။",
        parse_mode='html'
    )

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]setstarrate(?:@\w+)?(?:\s+(\S+))?$', 'bot1')))
async def set_star_rate_handler(event):
    if event.sender_id != OWNER_ID: return
    await event.reply(
        "💵 <b>USD ကို ဒီ Bot မှာ ရပ်ဆိုင်းလိုက်ပါပြီ — Star မှာ USD နှုန်းထား မရှိတော့ပါ။</b>\n"
        "💠 VLT ⇄ ⭐ Star နှုန်းထားက <code>VLT_RATE_MIN</code>–<code>VLT_RATE_MAX</code> "
        "အကြား အလိုအလျောက် ဝဲလွှင့်နေပါတယ် — ပြောင်းချင်ရင် code ထဲက VLT_RATE_MIN/MAX "
        "(သို့) VLT_STARTING_RATE ကို ချိန်ညှိပါ။",
        parse_mode='html'
    )

# ==========================================
# 💠 /buyvlt & /sellvlt — ⭐ Star <-> 💠 VLT exchange (2026-08, replaces /buystar & /sellstar).
# Unlike Star's old fixed ⭐ peg, this rate drifts on its own between VLT_RATE_MIN and
# VLT_RATE_MAX (see vlt_drift_loop()) — trading itself still doesn't move the rate.
# ==========================================
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]buyvlt(?:@\w+)?(?:\s+(\S+))?$', 'bot1')))
async def buy_vlt_handler(event):
    user_id = event.sender_id
    raw = event.pattern_match.group(1)
    market = await get_or_create_vlt_market()
    if not raw:
        return await event.reply(
            f"💠 <b>VLT ဝယ်ရန်</b>\n"
            f"💱 <b>နှုန်းထား:</b> <code>1💠 = {market['rate']:,.2f}⭐</code>\n"
            f"📌 <b>Usage:</b> <code>/buyvlt [VLT amount]</code>\n"
            f"<i>Example:</i> <code>/buyvlt 1</code>",
            parse_mode='html'
        )
    try:
        vlt_amount = float(raw)
    except ValueError:
        return await event.reply("❌ <b>VLT ပမာဏ မှန်ကန်အောင် ရိုက်ပါ။</b>", parse_mode='html')
    if vlt_amount <= 0:
        return await event.reply("❌ <b>VLT ပမာဏ မှန်ကန်အောင် ရိုက်ပါ။</b>", parse_mode='html')
    star_cost = round(vlt_amount * market["rate"], 2)
    if not await try_deduct_star(user_id, star_cost):
        return await event.reply("❌ <b>⭐ Star Balance မလုံလောက်ပါ။</b>", parse_mode='html')
    await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"vlt_balance": vlt_amount}}, upsert=True)
    await vlt_exchange_log_col.insert_one({
        "user_id": user_id, "type": "buy", "vlt_amount": vlt_amount, "star_amount": star_cost,
        "rate": market["rate"], "timestamp": time.time()
    })
    await event.reply(
        f"✅ <b>VLT ဝယ်ယူမှု အောင်မြင်ပါတယ်!</b>\n"
        f"<blockquote>"
        f"💠 <b>ဝယ်ယူ:</b> <code>{format_vlt_plain(vlt_amount)}</code>\n"
        f"💸 <b>ပေးချေ:</b> <code>{format_star_plain(star_cost)}</code>\n"
        f"💱 <b>နှုန်းထား:</b> <code>1💠 = {market['rate']:,.2f}⭐</code>"
        f"</blockquote>",
        parse_mode='html'
    )

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]sellvlt(?:@\w+)?(?:\s+(\S+))?$', 'bot1')))
async def sell_vlt_handler(event):
    user_id = event.sender_id
    raw = event.pattern_match.group(1)
    market = await get_or_create_vlt_market()
    if not raw:
        return await event.reply(
            f"💠 <b>VLT ရောင်းရန်</b>\n"
            f"💱 <b>နှုန်းထား:</b> <code>1💠 = {market['rate']:,.2f}⭐</code>\n"
            f"📌 <b>Usage:</b> <code>/sellvlt [VLT amount]</code>\n"
            f"<i>Example:</i> <code>/sellvlt 1</code>",
            parse_mode='html'
        )
    try:
        vlt_amount = float(raw)
    except ValueError:
        return await event.reply("❌ <b>VLT ပမာဏ မှန်ကန်အောင် ရိုက်ပါ။</b>", parse_mode='html')
    if vlt_amount <= 0:
        return await event.reply("❌ <b>VLT ပမာဏ မှန်ကန်အောင် ရိုက်ပါ။</b>", parse_mode='html')
    if not await try_deduct_vlt(user_id, vlt_amount):
        return await event.reply("❌ <b>💠 VLT Balance မလုံလောက်ပါ။</b>", parse_mode='html')
    star_gained = round(vlt_amount * market["rate"], 2)
    await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"star_balance": star_gained}}, upsert=True)
    await vlt_exchange_log_col.insert_one({
        "user_id": user_id, "type": "sell", "vlt_amount": vlt_amount, "star_amount": star_gained,
        "rate": market["rate"], "timestamp": time.time()
    })
    await event.reply(
        f"✅ <b>VLT ရောင်းချမှု အောင်မြင်ပါတယ်!</b>\n"
        f"<blockquote>"
        f"💠 <b>ရောင်း:</b> <code>{format_vlt_plain(vlt_amount)}</code>\n"
        f"⭐ <b>ရရှိ:</b> <code>{format_star_plain(star_gained)}</code>\n"
        f"💱 <b>နှုန်းထား:</b> <code>1💠 = {market['rate']:,.2f}⭐</code>"
        f"</blockquote>",
        parse_mode='html'
    )

# ==========================================
# 🏆 LEADERBOARDS
# ==========================================
TOP_MEDALS = {0: "🥇", 1: "🥈", 2: "🥉"}

async def _resolve_one_top_mention(client, user_id, stored_fullname):
    """Best-effort LIVE clickable mention (tg://user?id=...) for a leaderboard row, so /top
    and /gtop always point at the actual current account instead of a possibly-stale stored
    name. Falls back to the safely-sanitized stored fullname if the entity can't be resolved
    (e.g. the bot has genuinely never seen that user) — never breaks the leaderboard."""
    try:
        entity = await client.get_entity(user_id)
        first = getattr(entity, 'first_name', '') or ''
        last = getattr(entity, 'last_name', '') or ''
        live_name = f"{first} {last}".strip() or getattr(entity, 'username', '') or f"User {user_id}"
        display = clean_display_name(live_name, fallback=f"User {user_id}")
    except Exception:
        display = clean_display_name(stored_fullname, fallback=f"User {user_id}")
    return f"<a href='tg://user?id={user_id}'><b>{escape_html(display)}</b></a>"

async def _resolve_top_mentions(client, users):
    # ⚡ PERFORMANCE: resolving 10 rows one-at-a-time meant 10 sequential Telegram round-trips
    # before /top or /gtop could reply — easily a couple of seconds, and the sort of thing
    # that reads as "the bot got heavier". Resolving all of them concurrently turns that into
    # a single parallel batch instead.
    return await asyncio.gather(*(
        _resolve_one_top_mention(client, u["user_id"], u.get("fullname")) for u in users
    ))

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]top(?:@\w+)?$', 'bot1')))
async def local_group_top_handler(event):
    if event.is_private: return
    chat_id = event.chat_id
    cursor = users_catcher_col.find({f"group_catches.{str(chat_id)}": {"$gt": 0}}).sort(f"group_catches.{str(chat_id)}", -1).limit(10)
    top_users = await cursor.to_list(length=10)
    if not top_users:
        return await event.reply(f"🏆 <b>No rank data in this group yet.</b>\n<i>Catch a card to get on the board!</i>", parse_mode='html')
    mentions = await _resolve_top_mentions(event.client, top_users)
    lines = []
    for i, (u, mention) in enumerate(zip(top_users, mentions)):
        count = u["group_catches"][str(chat_id)]
        rank_tag = TOP_MEDALS.get(i, f"<code>#{i + 1}</code>")
        lines.append(f"{rank_tag}  {mention} — <code>{count:,} cards</code>")
    msg = (
        f"🏆 <b>TOP 10 HUNTERS IN THIS GROUP</b>\n"
        f"<blockquote>{chr(10).join(lines)}</blockquote>"
    )
    await event.reply(msg, parse_mode='html')

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]gtop(?:@\w+)?$', 'bot1')))
async def global_top_handler(event):
    cursor = users_catcher_col.find({"total_caught": {"$gt": 0}}).sort("total_caught", -1).limit(10)
    top_users = await cursor.to_list(length=10)
    if not top_users:
        return await event.reply(f"🌐 <b>No global ranking yet.</b>\n<i>Catch a card to get on the board!</i>", parse_mode='html')
    mentions = await _resolve_top_mentions(event.client, top_users)
    lines = []
    for i, (u, mention) in enumerate(zip(top_users, mentions)):
        count = u.get("total_caught", 0)
        rank_tag = TOP_MEDALS.get(i, f"<code>#{i + 1}</code>")
        lines.append(f"{rank_tag}  {mention} — <code>{count:,} cards</code>")
    msg = (
        f"🌐 <b>GLOBAL TOP 10 HUNTERS</b>\n"
        f"<blockquote>{chr(10).join(lines)}</blockquote>"
    )
    await event.reply(msg, parse_mode='html')
# ==========================================
# 💰 BALANCE (Wallet HUD card + optional owner-set banner photo)
# ==========================================
# 🖼️ /addbalance — owner-only, reply to a photo or video to set a GLOBAL banner shown on top of
# EVERY user's /balance card. Same storage pattern as /addchar and squad photos: the media gets
# forwarded into SPECIFIC_CONTROL_GROUP exactly once, and only its storage_msg_id is kept (in
# bot_settings_col this time, since this is a single bot-wide setting, not a per-character or
# per-squad doc) — check_points_balance below fetches it fresh from storage on every /balance
# call, same live-lookup pattern as send_squad_profile_message uses for squad photos.
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]addbalance(?:@\w+)?$', 'bot1')))
async def add_balance_banner(event):
    if event.sender_id != OWNER_ID: return
    if is_duplicate_event(event): return
    if not event.is_reply:
        return await event.reply(
            "❌ <b>Reply to a photo or video with /addbalance</b> — that media becomes the "
            "banner shown on top of everyone's /balance card.",
            parse_mode='html'
        )
    reply_msg = await event.get_reply_message()
    if not reply_msg or not (reply_msg.photo or reply_msg.video or reply_msg.document):
        return await event.reply("❌ <b>Valid media not found</b>", parse_mode='html')
    try:
        forwarded_msg = await send_safe_message(bot1, SPECIFIC_CONTROL_GROUP, "", file=reply_msg.media)
        await bot_settings_col.update_one(
            {"_id": "balance_wallet_banner"},
            {"$set": {"storage_msg_id": forwarded_msg.id, "set_by": event.sender_id, "set_at": time.time()}},
            upsert=True
        )
        await event.reply("✅ <b>Wallet banner updated!</b> Every /balance card will show it from now on.", parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ <b>Failed to save banner:</b> <code>{escape_html(str(e))}</code>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]removebalance(?:@\w+)?$', 'bot1')))
async def remove_balance_banner(event):
    if event.sender_id != OWNER_ID: return
    if is_duplicate_event(event): return
    await bot_settings_col.update_one({"_id": "balance_wallet_banner"}, {"$unset": {"storage_msg_id": ""}}, upsert=True)
    await event.reply("🗑️ <b>Wallet banner removed.</b> /balance will show as plain text again.", parse_mode='html')

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]balance(?:@\w+)?$', 'bot1')))
async def check_points_balance(event):
    user_id = event.sender_id
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    star_balance = user_doc.get("star_balance", 0) if user_doc else 0
    vlt_balance = user_doc.get("vlt_balance", 0) if user_doc else 0
    plain_name = await get_plain_name(event, user_id)

    # sc() runs BEFORE escape_html() on purpose — escaping first would turn a literal '&' into
    # the entity '&amp;', and sc() blindly remapping every lowercase letter would then mangle
    # that entity's own 'a'/'m'/'p' into small-caps glyphs too. Small-caps first, escape second.
    title_text = escape_html(sc(f"{plain_name}'s Wallet"))

    text = (
        f"💰 <b>{title_text}</b>\n\n"
        f"⭐ <b>Stars:</b> <code>{format_star_plain(star_balance)}</code>\n"
        f"💠 <b>VLT:</b> <code>{format_vlt_plain(vlt_balance)}</code>"
    )

    # 🔹 ဒီမှာ ခလုတ်တွေကို ၂ တန်း ၂ လုံးစီ ခွဲထားတယ်
    buttons = [
        [
            Button.inline("🏪 စျေးဝယ်မယ်", data=f"buyhub_cards_{user_id}"),
            Button.inline("💠 VLT ဝယ်မယ်", data=f"buyhub_star_{user_id}")
        ],
        [
            Button.inline("👑 Premium ဝယ်မယ်", data=f"buyhub_premium_{user_id}"),
            Button.inline("🎰 Casino ဂိမ်းများ", data="nav_casino_main")  # ဒီခလုတ်က bot1 မှာရှိတဲ့ Menu ကိုပဲ ခေါ်သွားမယ်
        ]
    ]

    # 🖼️ Optional owner-set wallet banner (see /addbalance above) — falls back to plain text
    # if none has been set yet, or if the stored media couldn't be fetched for any reason.
    banner_doc = await bot_settings_col.find_one({"_id": "balance_wallet_banner"})
    storage_msg_id = banner_doc.get("storage_msg_id") if banner_doc else None
    if storage_msg_id:
        try:
            storage_msg = await bot1.get_messages(SPECIFIC_CONTROL_GROUP, ids=storage_msg_id)
            if storage_msg and storage_msg.media:
                await event.reply(text, parse_mode='html', file=storage_msg.media, buttons=buttons)
                return
        except Exception:
            pass
    await event.reply(text, parse_mode='html', buttons=buttons)

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]topgift(?:@\w+)?$', 'bot1')))
async def top_gifters_handler(event):
    cursor = users_catcher_col.find({"total_gifted": {"$gt": 0}}).sort("total_gifted", -1).limit(10)
    top_users = await cursor.to_list(length=10)
    if not top_users:
        return await event.reply(f"🎁 <b>No gifts sent yet.</b>\n<i>/gift a card to get on the board!</i>", parse_mode='html')
    mentions = await _resolve_top_mentions(event.client, top_users)
    lines = []
    for i, (u, mention) in enumerate(zip(top_users, mentions)):
        count = u.get("total_gifted", 0)
        rank_tag = TOP_MEDALS.get(i, f"<code>#{i + 1}</code>")
        title = get_giver_title(count)
        title_str = f" {title[0]}" if title else ""
        lines.append(f"{rank_tag}  {mention}{title_str} — <code>{count:,} gifts</code>")
    msg = (
        f"🎁 <b>TOP 10 GIFTERS</b>\n"
        f"<blockquote>{chr(10).join(lines)}</blockquote>"
    )
    await event.reply(msg, parse_mode='html')

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]giftranks(?:@\w+)?$', 'bot1')))
async def gift_ranks_handler(event):
    giver_lines = "\n".join(f"{emoji} <b>{title}</b> — <code>{th}+</code> gifts" for th, emoji, title in reversed(GIFT_GIVER_TITLES))
    receiver_lines = "\n".join(f"{emoji} <b>{title}</b> — <code>{th}+</code> gifts" for th, emoji, title in reversed(GIFT_RECEIVER_TITLES))
    text = (
        f"🎖️ <b>GIFT RANKS</b>\n"
        f"<i>/gift ကတ်တွေ ပေး/ရ အရေအတွက်အလိုက် Rank အဆင့်ဆင့် အလိုအလျောက် ရရှိနိုင်ပါတယ်</i>\n\n"
        f"📤 <b>Giver Rank</b> <i>(/gift ပေးသမျှ အရေအတွက်)</i>\n"
        f"<blockquote>{giver_lines}</blockquote>\n"
        f"📥 <b>Receiver Rank</b> <i>(/gift လက်ခံရသမျှ အရေအတွက်)</i>\n"
        f"<blockquote>{receiver_lines}</blockquote>\n"
        f"<code>/profile</code> <i>မှာ ကိုယ့် Rank လက်ရှိကို ကြည့်နိုင်ပါတယ်။</i>"
    )
    await event.reply(text, parse_mode='html')



# ==========================================
# 🎖️ GIFT RANK TITLES — 6 escalating Burmese titles for GIVING (total_gifted) and a separate
# 6 for RECEIVING (total_gift_received). Purely a cosmetic badge computed live off the two
# counters already tracked on every /gift — nothing extra to keep in sync. Sorted descending
# by threshold so the first match in a scan is always the highest tier reached.
# ==========================================
GIFT_TIER_THRESHOLDS = (10, 30, 50, 100, 200, 300)  # 300+ is the open-ended top tier

GIFT_GIVER_TITLES = [  # (threshold, emoji, title) — "the one who gives cards away"
    (300, "🏆", "ကဒ်ဘုရင်"),        # Card King
    (200, "👑", "ကဒ်မင်းလေး"),          # Card Prince
    (100, "⚔️", "ကဒ်သခင်"),         # Card Lord
    (50, "📜", "ကဒ်ဆရာ"),           # Card Master
    (30, "🎗️", "ကဒ်ရက်ရောရှင်"),    # Generous Card-Giver
    (10, "🎁", "ကဒ်အလှူရှင်"),        # Card Donor
]
GIFT_RECEIVER_TITLES = [  # (threshold, emoji, title) — "the one showered with cards"
    (300, "👑", "ကဒ်ဘုန်းရှင်"),     # The Glorious One
    (200, "🌺", "ကဒ်ဂုဏ်ရှင်"),      # The Honored One
    (100, "💎", "ကဒ်မြတ်နိုးရှင်"),   # The Cherished One
    (50, "🌟", "ကဒ်ကျော်စောရှင်"),   # The Renowned One
    (30, "💝", "ကဒ်နှစ်လိုရှင်"),     # The Beloved One
    (10, "🎀", "ကဒ်ကံရှင်"),         # Fortune's Chosen
]

def _gift_title_for_count(count, titles_desc):
    """titles_desc must be sorted descending by threshold. Returns (emoji, title) for the
    highest tier `count` qualifies for, or None if below every threshold."""
    for threshold, emoji, title in titles_desc:
        if count >= threshold:
            return emoji, title
    return None

def get_giver_title(total_gifted):
    return _gift_title_for_count(total_gifted, GIFT_GIVER_TITLES)

def get_receiver_title(total_gift_received):
    return _gift_title_for_count(total_gift_received, GIFT_RECEIVER_TITLES)

# ==========================================
# 🎁 GIFT HISTORY (used by the /profile, /myinfo "Gift History" button)
# ==========================================
GIFT_HISTORY_PAGE_SIZE = 5

async def render_gift_history_page(user_id, page):
    skip = page * GIFT_HISTORY_PAGE_SIZE
    total = await gift_history_col.count_documents({"sender_id": user_id})
    rows = await gift_history_col.find({"sender_id": user_id}).sort("timestamp", -1).skip(skip).limit(GIFT_HISTORY_PAGE_SIZE).to_list(length=GIFT_HISTORY_PAGE_SIZE)
    if not rows:
        body = "<i>No gifts sent yet.</i>" if page == 0 else "<i>No more entries.</i>"
    else:
        lines = []
        for r in rows:
            char_doc = await characters_base_col.find_one({"char_id": r["char_id"]})
            char_name = escape_html(char_doc["name"]) if char_doc else r["char_id"]
            receiver_doc = await users_catcher_col.find_one({"user_id": r["receiver_id"]})
            receiver_name = escape_html(clean_display_name(receiver_doc.get("fullname") if receiver_doc else None, fallback=f"User {r['receiver_id']}"))
            when = datetime.fromtimestamp(r["timestamp"], TZ).strftime("%Y-%m-%d")
            lines.append(f"🎁 <b>{char_name}</b> → {receiver_name} <i>({when})</i>")
        body = "\n".join(lines)
    text = f"🎁 <b>Your Gift History</b> ({total} total)\n<blockquote>{body}</blockquote>"
    nav = []
    if page > 0:
        nav.append(Button.inline("◀ Prev", data=f"gh_{user_id}_{page - 1}"))
    if skip + GIFT_HISTORY_PAGE_SIZE < total:
        nav.append(Button.inline("Next ▶", data=f"gh_{user_id}_{page + 1}"))
    buttons = [nav] if nav else None
    return text, buttons


# ==========================================
# 🎁 GIFT (fixed)
# ==========================================
GIFT_NOTE_MAX_LEN = 150  # characters — keeps the note a quick line, not a wall of text on the gift card

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]gift(?:@\w+)?\s+(\S+)(?:\s+(.+))?$', 'bot1')))
async def gift_asset_handler(event):
    if not event.is_reply:
        return await event.reply("❌ <b>Reply to the user you want to gift.</b>", parse_mode='html')

    char_id = normalize_char_id_input(event.pattern_match.group(1))
    # 💌 NEW (per owner request — "Gift with a Note"): anything typed after the char_id is an
    # optional short note that rides along with the gift — e.g. /gift 3996 happy birthday!.
    # Capped and HTML-escaped before it ever touches a message; see pending_gift_notes below
    # for how it survives from here to the confirm tap.
    gift_note = escape_html((event.pattern_match.group(2) or "").strip()[:GIFT_NOTE_MAX_LEN])
    sender_id = event.sender_id
    reply_msg = await event.get_reply_message()
    receiver_id = reply_msg.sender_id

    if sender_id == receiver_id:
        return await event.reply("❌ <b>Can't gift to yourself!</b>", parse_mode='html')

    # 🩹 FIX (moved to gift_callback_handler below, per owner report): the 1000 ⭐ gift fee
    # used to be charged RIGHT HERE — before checking whether char_id even exists or the sender
    # actually owns it. A typo'd/nonexistent char_id (e.g. /gift 21345), or trying to gift a
    # card you don't have, still cost 1000 ⭐ every single time, with zero refund — even
    # hitting Cancel on the confirmation didn't give it back. It's now only charged once
    # validation has passed AND the gift is confirmed.
    char_data = await characters_base_col.find_one({"char_id": char_id})
    if not char_data:
        return await event.reply("❌ <b>Character data not found.</b>", parse_mode='html')

    if sender_id != OWNER_ID:
        sender_doc = await users_catcher_col.find_one({"user_id": sender_id})
        sender_harem = sender_doc.get("harem", []) if sender_doc else []
        char_item = next((x for x in sender_harem if isinstance(x, dict) and x.get("char_id") == char_id and x.get("status") != "market"), None)
        if not char_item:
            return await event.reply(f"❌ <b>You don’t have this card.</b>", parse_mode='html')

    r_mention = await get_html_mention(event, receiver_id)
    note_preview = f"💌 <b>Note:</b> <i>{gift_note}</i>\n" if gift_note else ""
    confirm_text = (
        f"🎠 <b>Gift Confirmation</b>\n\n"
        f"🐇 <b>Character Card:</b> <code>{char_data['name']}</code> (<code>{display_char_id(char_id)}</code>)\n"
        f"🦋 <b>Rarity:</b> {char_data.get('rarity', 'Unknown')}\n"
        f"{artist_line(char_data)}"
        f"🎯 <b>Receiver:</b> {r_mention}\n"
        f"{note_preview}\n"
        f"<blockquote>Are you sure you want to send this card to {r_mention}?</blockquote>\n"
        f"✨ Confirm below! 🦋"
    )
    if gift_note:
        pending_gift_notes[(sender_id, receiver_id, char_id)] = {"note": gift_note, "expiry": time.time() + 600}

    buttons = [
        [
            colored_button("✅ Yep, Why Not✔", f"gift_confirm_{sender_id}_{receiver_id}_{char_id}", color="success"),
            Button.inline("❌ Nope, Jk✗", data=f"gift_cancel_{sender_id}_{receiver_id}_{char_id}")
        ]
    ]

    async def _send_gift_confirm(media):
        return await event.reply(confirm_text, file=media, buttons=buttons, parse_mode='html')

    sent = await send_with_char_media(char_data["char_id"], char_data["storage_msg_id"], _send_gift_confirm)
    if sent is None:
        await event.reply(confirm_text, buttons=buttons, parse_mode='html')

@bot1.on(events.CallbackQuery(pattern=r'^gift_(confirm|cancel)_(\d+)_(\d+)_([a-zA-Z0-9_]+)$'))
async def gift_callback_handler(event):
    action = event.pattern_match.group(1)
    if isinstance(action, bytes):
        action = action.decode('utf-8')
    sender_id = int(event.pattern_match.group(2))
    receiver_id = int(event.pattern_match.group(3))
    char_id_raw = event.pattern_match.group(4)
    if isinstance(char_id_raw, bytes):
        char_id = char_id_raw.decode('utf-8').upper()
    else:
        char_id = char_id_raw.upper()
    if event.sender_id != sender_id:
        return await event.answer("❌ This action is not for you!", alert=True)
    if not claim_single_tap(event):
        return await event.answer("⏳ Already processing...", alert=False)
    # 💌 Pop (not peek) — a note is meant for exactly the ONE gift it was typed alongside, not
    # reused if this same trio somehow gets confirmed again later.
    note_entry = pending_gift_notes.pop((sender_id, receiver_id, char_id), None)
    gift_note = note_entry["note"] if (note_entry and time.time() <= note_entry["expiry"]) else ""
    if action == "cancel":
        await event.edit(
            f"❌ <b>Gift Cancelled</b>\n\n"
            f"Card <code>{display_char_id(char_id)}</code> was not sent.\n"
            f"🐇 Maybe next time!",
            parse_mode='html',
            buttons=None
        )
        await event.answer("Gift cancelled.", alert=True)
        return
    # confirm
    if sender_id == OWNER_ID:
        # 👑 Unlimited vault: nothing to remove — this card stays available for every future
        # gift too. Rarity comes straight from the character record instead of a harem item
        # (the owner may never have actually caught this one for real). Gift stats are still
        # tracked for visibility, they just don't touch the owner's real harem array.
        char_data = await characters_base_col.find_one({"char_id": char_id})
        if not char_data:
            await event.edit(
                f"❌ <b>Oops!</b>\n\n"
                f"Character <code>{display_char_id(char_id)}</code> no longer exists.",
                parse_mode='html',
                buttons=None
            )
            await event.answer("Card not found.", alert=True)
            return
        char_rarity = char_data.get("rarity", "Unknown")
        gift_tier = classify_rarity(char_rarity)
        await users_catcher_col.update_one(
            {"user_id": sender_id},
            {"$inc": {"total_gifted": 1, f"gifted_by_rarity.{gift_tier}": 1}},
            upsert=True
        )
        # 🩹 FIX (per owner report — "always having to run /refillcatch after gifts"): the
        # receiver below always gets a brand-new real harem entry pushed (this branch mints it
        # from the owner's unlimited vault, nothing existing gets removed from anywhere), but
        # characters_base_col.spawn_count was never incremented to match — same "Global
        # Catches" counter /buy already correctly bumps on every Owner Shop purchase (see
        # execute_star_shop_purchase). Every owner-gift silently under-counted it by 1, which
        # is exactly the drift /refillcatch was being run to paper over afterward.
        await characters_base_col.update_one({"char_id": char_id}, {"$inc": {"spawn_count": 1}})
    else:
        sender_doc = await users_catcher_col.find_one({"user_id": sender_id})
        sender_harem = sender_doc.get("harem", []) if sender_doc else []
        char_item = next((x for x in sender_harem if isinstance(x, dict) and x.get("char_id") == char_id and x.get("status") != "market"), None)
        if not char_item:
            await event.edit(
                f"❌ <b>Oops!</b>\n\n"
                f"It seems you no longer have card <code>{display_char_id(char_id)}</code>.\n"
                f"☄️ Looks like luck played a little trick on you!",
                parse_mode='html',
                buttons=None
            )
            await event.answer("Card not found.", alert=True)
            return
        # 🩹 FIX: atomic $pull-based removal (see remove_one_harem_copy) instead of the old
        # find_one → sender_harem.remove() → $set the whole array back — same lost-update
        # race risk as marketplace/trade/sell fixed earlier.
        char_rarity = char_item.get("rarity", "Unknown")
        gift_tier = char_item.get("rarity_tier") or classify_rarity(char_rarity)
        await remove_one_harem_copy(sender_id, char_id, char_item.get("status", "vault"))
        await users_catcher_col.update_one(
            {"user_id": sender_id},
            {"$inc": {"total_gifted": 1, f"gifted_by_rarity.{gift_tier}": 1}}
        )
        sender_doc_after = await users_catcher_col.find_one({"user_id": sender_id}, {"harem": 1})
        await clear_stale_favorite(sender_id, char_id, (sender_doc_after or {}).get("harem", []))

    # 🩹 FIX: the 1000 ⭐ gift fee is now charged HERE — only once validation above has
    # already succeeded (char exists, sender genuinely owns it) and the gift is definitely
    # going through. Previously this fired the instant /gift was typed, before any of that was
    # checked, so a bad char_id or a card the sender didn't own still cost 1000 ⭐ with no
    # refund, and even hitting Cancel on the confirmation kept the fee too.
    # 🩹 2026-08 USD RETIREMENT: was a flat 1000 USD gift fee — re-tuned to a small, meaningful
    # ⭐ Star amount matching the rest of the post-migration Star economy (referral bonus is
    # 2-4⭐, quiz reward 3-7⭐) rather than the same raw digits relabeled — owner-adjustable.
    gift_fee = 5.0
    gift_fee_paid = await try_deduct_balance(sender_id, gift_fee)
    if gift_fee_paid:
        fee_note = f"💰 <b>Gift Fee Paid:</b> <code>{format_star_plain(gift_fee)}</code>\n"
    else:
        await debts_col.update_one({"user_id": sender_id}, {"$inc": {"amount": gift_fee}}, upsert=True)
        await bot3_treasury_adjust(usd=gift_fee)
        fee_note = f"⚠️ <b>Gift Fee ({format_star_plain(gift_fee)}) added as debt</b> — insufficient balance.\n"

    r_mention = await get_html_mention(event, receiver_id)
    r_plain_name = await get_plain_name(event, receiver_id)
    await users_catcher_col.update_one(
        {"user_id": receiver_id},
        {
            "$push": {"harem": {"char_id": char_id, "caught_date": time.time(), "rarity": char_rarity, "status": "vault"}},
            "$inc": {"total_caught": 1, "total_gift_received": 1},
            "$set": {"fullname": r_plain_name}
        },
        upsert=True
    )
    try:
        await gift_history_col.insert_one({
            "sender_id": sender_id,
            "receiver_id": receiver_id,
            "char_id": char_id,
            "rarity_tier": gift_tier,
            "timestamp": time.time()
        })
    except Exception as e:
        print(f"gift_history log error: {e}")

    # 🎖️ Gift Rank: pull the sender's fresh post-gift count and work out (a) their current
    # title, and (b) whether THIS gift is the exact moment that crossed them into a new tier
    # (count lands precisely on a threshold — safe since each gift only ever moves a counter
    # up by 1) — that's the promotion banner moment, shown once, right when it happens.
    sender_after = await users_catcher_col.find_one({"user_id": sender_id}, {"total_gifted": 1})
    sender_gift_count = (sender_after or {}).get("total_gifted", 0)
    giver_title = get_giver_title(sender_gift_count)
    giver_promoted = sender_gift_count in GIFT_TIER_THRESHOLDS
    giver_rank_line = f"🎖️ <b>Your Rank:</b> {giver_title[0]} <code>{giver_title[1]}</code>\n" if giver_title else ""
    giver_promo_line = f"\n🎉 <b>ဂုဏ်ယူပါတယ်! Rank အသစ် {giver_title[0]} <code>{giver_title[1]}</code> ရရှိသွားပါပြီ!</b> <i>(Gift {sender_gift_count} ကြိမ်ပြည့်ပါပြီ)</i>" if giver_promoted and giver_title else ""

    # 🎨 A designed confirmation where the gift happened.
    char_doc = await characters_base_col.find_one({"char_id": char_id})
    char_name_display = escape_html(char_doc.get("name", "?")) if char_doc else char_id
    rarity_emoji = RARITY_EMOJI.get(gift_tier, RARITY_DEFAULT_EMOJI)
    s_mention = await get_html_mention(event, sender_id)

    await event.edit(
        f"🎁 <b>GIFT SUCCESSFUL!</b>\n"
        f"<blockquote>"
        f"📤 <b>From:</b> {s_mention}\n"
        f"📥 <b>To:</b> {r_mention}\n"
        f"{rarity_emoji} <b>Card:</b> <code>{char_name_display}</code> (<code>{display_char_id(char_id)}</code>)\n"
        f"🦋 <b>Rarity:</b> {char_rarity}\n"
        f"{giver_rank_line}"
        f"{fee_note}"
        f"{'💌 <b>Note:</b> <i>' + gift_note + '</i>' + chr(10) if gift_note else ''}"
        f"</blockquote>\n"
        f"🍔 <i>All done!</i>"
        f"{giver_promo_line}",
        parse_mode='html',
        buttons=None
    )
    await event.answer("Gift sent successfully!", alert=True)
    # 🩹 CHANGED (per owner request): a DM to the receiver used to be sent here too — removed,
    # the in-chat confirmation above is enough now.

# ==========================================
# 🎁 /giftusd, /giftstar, /giftpremium — direct user-to-user gifting of ⭐, ⭐ Star, and Bot
# Premium. All three: reply to the receiver's message, same confirm/cancel pattern as the card
# /gift above. /giftpremium is paid for by the SENDER in ⭐ Star at the exact /buypremium rate
# (see PREMIUM_TIERS) — the days land on the RECEIVER instead of the buyer; the Star still
# flows to the Owner, same Star sink as every other Premium purchase.
# ==========================================
GIFT_USD_MIN = 0.01
GIFT_STAR_MIN = 0.01

async def _resolve_gift_receiver(event):
    """Shared receiver resolution + validation for all three /gift* commands below.
    Returns (receiver_id, error_message_or_None)."""
    if not event.is_reply:
        return None, "❌ <b>Reply to the user you want to gift.</b>"
    reply_msg = await event.get_reply_message()
    receiver_id = reply_msg.sender_id if reply_msg else None
    if not receiver_id:
        return None, "❌ <b>Couldn't identify that user.</b>"
    if receiver_id == event.sender_id:
        return None, "❌ <b>Can't gift to yourself!</b>"
    if receiver_id in bot_ids:
        return None, "❌ <b>Can't gift to a bot.</b>"
    return receiver_id, None

# ---- /giftusd — 🩹 2026-08 USD RETIREMENT: retired along with USD. /giftusd and /giftstar
# both operated on the same wallet — they're identical now that wallet_balance has merged
# into star_balance — so this is a short redirect stub instead of a duplicate implementation. ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]giftusd(?:@\w+)?\s+(\d+(?:\.\d+)?)$', 'bot1')))
async def gift_usd_handler(event):
    amount = event.pattern_match.group(1)
    await event.reply(
        f"💵 <b>USD ကို ဒီ Bot မှာ ရပ်ဆိုင်းလိုက်ပါပြီ — /giftusd အလုပ်မလုပ်တော့ပါ။</b>\n"
        f"⭐ Star ပို့ချင်ရင် <code>/giftstar {amount}</code> ကို reply ပြန်ပြီး သုံးပါ။",
        parse_mode='html'
    )

# ---- /giftstar ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]giftstar(?:@\w+)?\s+(\d+(?:\.\d+)?)$', 'bot1')))
async def gift_star_handler(event):
    amount = round(float(event.pattern_match.group(1)), 2)
    receiver_id, err = await _resolve_gift_receiver(event)
    if err:
        return await event.reply(err, parse_mode='html')
    
    # 💰 0.2% Fee (0.002)
    fee_rate = 0.002
    fee = round(amount * fee_rate, 2)
    net_amount = round(amount - fee, 2)
    
    if fee <= 0:
        return await event.reply("❌ <b>Gift amount too small (fee would be zero).</b>", parse_mode='html')
    
    sender_id = event.sender_id
    r_mention = await get_html_mention(event, receiver_id)
    
    # Check sender Star balance (including fee)
    sender_doc = await users_catcher_col.find_one({"user_id": sender_id})
    star_balance = sender_doc.get("star_balance", 0) if sender_doc else 0
    if star_balance < amount:
        return await event.reply(
            f"❌ <b>Insufficient Star balance!</b> You need {format_star_plain(amount)} (including {format_star_plain(fee)} fee).",
            parse_mode='html'
        )
    
    confirm_text = (
        f"⭐ <b>Gift Confirmation</b>\n\n"
        f"💰 <b>Amount:</b> <code>{format_star_plain(amount)}</code>\n"
        f"🏦 <b>Fee (0.2%):</b> <code>{format_star_plain(fee)}</code>\n"
        f"📥 <b>Receiver gets:</b> <code>{format_star_plain(net_amount)}</code>\n"
        f"🎯 <b>Receiver:</b> {r_mention}\n\n"
        f"<blockquote>Are you sure you want to send this?</blockquote>"
    )
    buttons = [[
        colored_button("✅ Confirm", f"giftstar_confirm_{sender_id}_{receiver_id}_{amount}_{fee}", color="success"),
        Button.inline("❌ Cancel", data=f"giftstar_cancel_{sender_id}_{receiver_id}_{amount}")
    ]]
    await event.reply(confirm_text, buttons=buttons, parse_mode='html')

@bot1.on(events.CallbackQuery(pattern=r'^giftstar_(confirm|cancel)_(\d+)_(\d+)_([\d.]+)(?:_([\d.]+))?$'))
async def gift_star_callback(event):
    action = event.pattern_match.group(1)
    if isinstance(action, bytes):
        action = action.decode('utf-8')
    sender_id = int(event.pattern_match.group(2))
    receiver_id = int(event.pattern_match.group(3))
    amount = round(float(event.pattern_match.group(4)), 2)
    fee = round(float(event.pattern_match.group(5) or 0), 2) if event.pattern_match.group(5) else 0
    
    if event.sender_id != sender_id:
        return await event.answer("❌ This action is not for you!", alert=True)
    if not claim_single_tap(event):
        return await event.answer("⏳ Already processing...", alert=False)
    
    if action == "cancel":
        await event.edit(f"❌ <b>Gift Cancelled.</b> {format_star_plain(amount)} was not sent.", parse_mode='html', buttons=None)
        return await event.answer("Gift cancelled.", alert=True)
    
    # Deduct full amount first (gross Star)
    if not await try_deduct_star(sender_id, amount):
        await event.edit("❌ <b>Not enough ⭐ Star balance anymore.</b>", parse_mode='html', buttons=None)
        return await event.answer("Insufficient Star.", alert=True)
    
    # Send net amount to receiver
    net_amount = round(amount - fee, 2)
    if net_amount > 0:
        r_plain_name = await get_plain_name(event, receiver_id)
        await users_catcher_col.update_one(
            {"user_id": receiver_id},
            {"$inc": {"star_balance": net_amount}, "$set": {"fullname": r_plain_name}},
            upsert=True
        )
    
    # Send fee to bot3 treasury (Star side)
    if fee > 0:
        await bot3_treasury_adjust(star=fee)
    
    r_mention = await get_html_mention(event, receiver_id)
    s_mention = await get_html_mention(event, sender_id)
    await event.edit(
        f"✅ <b>{format_star_plain(net_amount)} sent to {r_mention}!</b>\n"
        f"🏦 <b>Fee (0.2%):</b> {format_star_plain(fee)} (added to bot3 treasury)",
        parse_mode='html',
        buttons=None
    )
    # 🩹 CHANGED (per owner request): the receiver DM notification was removed here — the
    # in-chat confirmation above is enough now.

# ---- /giftvlt (2026-08) — same shape as /giftstar, for 💠 VLT ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]giftvlt(?:@\w+)?\s+(\d+(?:\.\d+)?)$', 'bot1')))
async def gift_vlt_handler(event):
    amount = round(float(event.pattern_match.group(1)), 2)
    receiver_id, err = await _resolve_gift_receiver(event)
    if err:
        return await event.reply(err, parse_mode='html')

    # 💰 0.2% Fee (0.002) — same rate as /giftstar
    fee_rate = 0.002
    fee = round(amount * fee_rate, 2)
    net_amount = round(amount - fee, 2)

    if fee <= 0:
        return await event.reply("❌ <b>Gift amount too small (fee would be zero).</b>", parse_mode='html')

    sender_id = event.sender_id
    r_mention = await get_html_mention(event, receiver_id)

    # Check sender VLT balance (including fee)
    sender_doc = await users_catcher_col.find_one({"user_id": sender_id})
    vlt_balance = sender_doc.get("vlt_balance", 0) if sender_doc else 0
    if vlt_balance < amount:
        return await event.reply(
            f"❌ <b>💠 VLT Balance မလုံလောက်ပါ!</b> လိုအပ်: {format_vlt_plain(amount)} (fee {format_vlt_plain(fee)} ပါ)",
            parse_mode='html'
        )

    confirm_text = (
        f"💠 <b>VLT Gift Confirmation</b>\n\n"
        f"💰 <b>Amount:</b> <code>{format_vlt_plain(amount)}</code>\n"
        f"🏦 <b>Fee (0.2%):</b> <code>{format_vlt_plain(fee)}</code>\n"
        f"📥 <b>Receiver gets:</b> <code>{format_vlt_plain(net_amount)}</code>\n"
        f"🎯 <b>Receiver:</b> {r_mention}\n\n"
        f"<blockquote>Are you sure you want to send this?</blockquote>"
    )
    buttons = [[
        colored_button("✅ Confirm", f"giftvlt_confirm_{sender_id}_{receiver_id}_{amount}_{fee}", color="success"),
        Button.inline("❌ Cancel", data=f"giftvlt_cancel_{sender_id}_{receiver_id}_{amount}")
    ]]
    await event.reply(confirm_text, buttons=buttons, parse_mode='html')

@bot1.on(events.CallbackQuery(pattern=r'^giftvlt_(confirm|cancel)_(\d+)_(\d+)_([\d.]+)(?:_([\d.]+))?$'))
async def gift_vlt_callback(event):
    action = event.pattern_match.group(1)
    if isinstance(action, bytes):
        action = action.decode('utf-8')
    sender_id = int(event.pattern_match.group(2))
    receiver_id = int(event.pattern_match.group(3))
    amount = round(float(event.pattern_match.group(4)), 2)
    fee = round(float(event.pattern_match.group(5) or 0), 2) if event.pattern_match.group(5) else 0

    if event.sender_id != sender_id:
        return await event.answer("❌ This action is not for you!", alert=True)
    if not claim_single_tap(event):
        return await event.answer("⏳ Already processing...", alert=False)

    if action == "cancel":
        await event.edit(f"❌ <b>Gift Cancelled.</b> {format_vlt_plain(amount)} was not sent.", parse_mode='html', buttons=None)
        return await event.answer("Gift cancelled.", alert=True)

    # Deduct full amount first (gross VLT)
    if not await try_deduct_vlt(sender_id, amount):
        await event.edit("❌ <b>💠 VLT Balance မလုံလောက်တော့ပါ။</b>", parse_mode='html', buttons=None)
        return await event.answer("Insufficient VLT.", alert=True)

    # Send net amount to receiver
    net_amount = round(amount - fee, 2)
    if net_amount > 0:
        r_plain_name = await get_plain_name(event, receiver_id)
        await users_catcher_col.update_one(
            {"user_id": receiver_id},
            {"$inc": {"vlt_balance": net_amount}, "$set": {"fullname": r_plain_name}},
            upsert=True
        )

    # 🩹 The VLT fee is BURNED (removed from circulation entirely), not routed to bot3
    # treasury like the Star gift fee — VLT has no treasury of its own (see /sell's scrap
    # design: VLT is never minted from or sunk into a central reserve, it only ever moves
    # peer-to-peer or through /buyvlt & /sellvlt against Star). Burning the fee here keeps
    # that invariant intact instead of quietly creating a VLT pile somewhere.

    r_mention = await get_html_mention(event, receiver_id)
    await event.edit(
        f"✅ <b>{format_vlt_plain(net_amount)} sent to {r_mention}!</b>\n"
        f"🔥 <b>Fee (0.2%):</b> {format_vlt_plain(fee)} (burned)",
        parse_mode='html',
        buttons=None
    )

# ---- /giftpremium ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]giftpremium(?:@\w+)?\s+(\d+)$', 'bot1')))
async def gift_premium_handler(event):
    months = int(event.pattern_match.group(1))
    tier = next((t for t in PREMIUM_TIERS if t[0] == months), None)
    if not tier:
        valid = ", ".join(str(t[0]) for t in PREMIUM_TIERS)
        return await event.reply(f"❌ <b>Invalid duration.</b> Valid tiers (months): <code>{valid}</code>", parse_mode='html')
    receiver_id, err = await _resolve_gift_receiver(event)
    if err:
        return await event.reply(err, parse_mode='html')
    sender_id = event.sender_id
    _, price, days = tier
    r_mention = await get_html_mention(event, receiver_id)
    confirm_text = (
        f"👑 <b>Gift Premium Confirmation</b>\n\n"
        f"⏳ <b>Duration:</b> <code>{_premium_tier_label(months)}</code>\n"
        f"💰 <b>Cost (from your ⭐ Star):</b> <code>{price}⭐</code>\n"
        f"🎯 <b>Receiver:</b> {r_mention}\n\n"
        f"<blockquote>Are you sure you want to gift Premium to {r_mention}?</blockquote>"
    )
    buttons = [[
        colored_button("✅ Confirm", f"giftprem_confirm_{sender_id}_{receiver_id}_{months}", color="success"),
        Button.inline("❌ Cancel", data=f"giftprem_cancel_{sender_id}_{receiver_id}_{months}")
    ]]
    await event.reply(confirm_text, buttons=buttons, parse_mode='html')

@bot1.on(events.CallbackQuery(pattern=r'^giftprem_(confirm|cancel)_(\d+)_(\d+)_(\d+)$'))
async def gift_premium_callback(event):
    action = event.pattern_match.group(1)
    if isinstance(action, bytes): action = action.decode('utf-8')
    sender_id = int(event.pattern_match.group(2))
    receiver_id = int(event.pattern_match.group(3))
    months = int(event.pattern_match.group(4))
    if event.sender_id != sender_id:
        return await event.answer("❌ This action is not for you!", alert=True)
    if not claim_single_tap(event):
        return await event.answer("⏳ Already processing...", alert=False)
    tier = next((t for t in PREMIUM_TIERS if t[0] == months), None)
    if not tier:
        await event.edit("❌ <b>Invalid tier.</b>", parse_mode='html', buttons=None)
        return await event.answer("Invalid tier.", alert=True)
    _, price, days = tier
    if action == "cancel":
        await event.edit("❌ <b>Gift Cancelled.</b> Premium was not sent.", parse_mode='html', buttons=None)
        return await event.answer("Gift cancelled.", alert=True)
    if not await try_deduct_star(sender_id, price):
        buyer_doc = await users_catcher_col.find_one({"user_id": sender_id})
        have = buyer_doc.get("star_balance", 0) if buyer_doc else 0
        await event.edit(f"❌ <b>Not enough ⭐ Star.</b> Need: <code>{price}⭐</code> | Have: <code>{format_star_plain(have)}</code>", parse_mode='html', buttons=None)
        return await event.answer("Insufficient Star.", alert=True)
    new_until = await grant_premium_days(receiver_id, days)
    # Same Star sink as every other Premium purchase — the Owner receives the Star paid.
    await users_catcher_col.update_one({"user_id": OWNER_ID}, {"$inc": {"star_balance": price}}, upsert=True)
    r_mention = await get_html_mention(event, receiver_id)
    s_mention = await get_html_mention(event, sender_id)
    expiry_str = datetime.fromtimestamp(new_until, TZ).strftime("%Y-%m-%d %H:%M")
    await event.edit(
        f"✅ <b>Premium gifted to {r_mention}!</b>\n⏳ <b>Runs until:</b> <code>{expiry_str}</code>",
        parse_mode='html', buttons=None
    )
    # 🩹 CHANGED (per owner request): the receiver DM notification was removed here — the
    # in-chat confirmation above is enough now.

# 🤝 TRADE
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]trade(?:@\w+)?\s+([a-zA-Z0-9_]+)\s+([a-zA-Z0-9_]+)$', 'bot1')))
async def trade_proposal_handler(event):
    if not event.is_reply: return await event.reply(f"❌ <b>Reply to the user you want to trade with.</b>", parse_mode='html')
    my_char_id = normalize_char_id_input(event.pattern_match.group(1))
    their_char_id = normalize_char_id_input(event.pattern_match.group(2))
    sender_id = event.sender_id
    reply_msg = await event.get_reply_message()
    target_user_id = reply_msg.sender_id
    if sender_id == target_user_id: return
    s_doc = await users_catcher_col.find_one({"user_id": sender_id})
    t_doc = await users_catcher_col.find_one({"user_id": target_user_id})
    s_harem = s_doc.get("harem", []) if s_doc else []
    t_harem = t_doc.get("harem", []) if t_doc else []
    s_has = any(isinstance(x, dict) and x.get("char_id") == my_char_id and x.get("status") != "market" for x in s_harem)
    t_has = any(isinstance(x, dict) and x.get("char_id") == their_char_id and x.get("status") != "market" for x in t_harem)
    if not s_has: return await event.reply(f"❌ You don’t have <code>{my_char_id}</code> available.", parse_mode='html')
    if not t_has: return await event.reply(f"❌ They don’t have <code>{their_char_id}</code> available.", parse_mode='html')
    s_char = await characters_base_col.find_one({"char_id": my_char_id})
    t_char = await characters_base_col.find_one({"char_id": their_char_id})
    trade_text = f"🤝 <b>TRADE CONTRACT</b>\n📤 <b>Your Offer:</b> <code>{s_char['name']}</code> ({my_char_id})\n📥 <b>Their Request:</b> <code>{t_char['name']}</code> ({their_char_id})\n⚡ ━━━━ ⚡\n<blockquote>Confirm or cancel – decision is theirs.</blockquote>"
    buttons = [[colored_button("🤝 Confirm", f"tr_conf_{sender_id}_{target_user_id}_{my_char_id}_{their_char_id}", color="success"), Button.inline("❌ Cancel", data=f"tr_canc_{sender_id}_{target_user_id}")]]
    await event.reply(trade_text, parse_mode='html', buttons=buttons)

# ==========================================
# 🎰 CASINO GAMES
# ==========================================
# In the force-join group specifically, every outgoing message these 8 games produce TRIES to
# go via bot3 (Guard Bot) instead of bot1 — bot1 still has to be the one that RECEIVES and
# processes the command (Telegram only delivers a bare "/command" with no @botname suffix to
# ONE bot in a group — whichever one most recently sent a message there — so bot3 can't
# reliably see these commands directly without being made a group admin or having its privacy
# mode disabled), but routing the RESULT/board messages through bot3 takes that volume off
# bot1's own per-chat send budget in what is its busiest room.
# "Tries" is the important word: if bot3.send_message() fails for ANY reason — it lost its
# connection, it's no longer actually in the room, a permissions issue, anything — this falls
# straight back to sending via bot1 instead of leaving the command silently unanswered. A game
# going quiet with no error and no fallback is worse than bot1 carrying a bit more load, so
# correctness (always respond, somehow) comes before the load-shedding optimization.
async def _out(event, text, **kwargs):
    """Drop-in replacement for event.reply(text, **kwargs)."""
    if event.chat_id == FORCE_SUB_CHAT_ID and bot3 is not None:
        try:
            kwargs.setdefault('reply_to', event.message_id)
            return await bot3.send_message(event.chat_id, text, **kwargs)
        except Exception as e:
            print(f"⚠️ Guard Bot send failed in force-join room, falling back to bot1: {e}")
    return await event.reply(text, **kwargs)

# =========================================================
# 🏦 BOT3 TREASURY BALANCE — /bot3balance (anyone can run this, no owner restriction)
# =========================================================
# Read-only: shows the seed bankroll bot3 was given vs its current balance, so how much bot3
# has actually won or lost across every casino game (see bot3_treasury_adjust calls throughout
# this file) is always a single command away instead of being uncountable.
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.](?:bot3balance|housebalance)(?:@\w+)?$', 'bot1')))
async def bot3_balance_command(event):
    treasury = await get_or_create_bot3_treasury()
    seed_star = treasury.get("seed_star_balance", BOT3_SEED_STAR_BALANCE)
    now_star = treasury.get("star_balance", seed_star)
    profit_star = round(now_star - seed_star, 2)
    star_sign = "📈" if profit_star > 0 else ("📉" if profit_star < 0 else "➖")
    star_profit_str = f"+{format_star_plain(profit_star)}" if profit_star >= 0 else f"-{format_star_plain(abs(profit_star))}"
    text = (
        f"🏦 <b>Bot3 Treasury (Casino House Balance)</b>\n"
        f"<blockquote>"
        f"⭐ <b>Star</b>\n"
        f" ┣ Seed (မူလ): <code>{format_star_plain(seed_star)}</code>\n"
        f" ┣ လက်ရှိ: <code>{format_star_plain(now_star)}</code>\n"
        f" ┗ {star_sign} <b>အမြတ်/အရှုံး:</b> <code>{star_profit_str}</code>"
        f"</blockquote>"
    )
    await _out(event, text, parse_mode='html')

# =========================================================
# 🏦 /claimtreasury — owner-only, ONE-TIME migration (per owner request, Star/VLT inflation fix)
# =========================================================
# bot3_treasury_adjust() now mirrors every future fee/burn/win/loss straight to OWNER_ID's real
# ⭐ balance (see its docstring above) — but that only covers changes from here on. Everything
# bot3 had already won off players BEFORE that change is sitting in bot3_treasury_col as a
# number nobody could actually spend. This pays that historical backlog out once, then flags
# the treasury doc so it can never be double-paid — running this again after a successful claim
# is a no-op by design, since ongoing profit/loss is already flowing to the owner automatically.
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]claimtreasury(?:@\w+)?$', 'bot1')))
async def claim_treasury_handler(event):
    if event.sender_id != OWNER_ID: return
    treasury = await get_or_create_bot3_treasury()
    if treasury.get("historical_claimed"):
        return await event.reply(
            "✅ <b>Historical treasury already claimed.</b>\nEvery bot3 fee/burn/win/loss since "
            "then has been flowing to your ⭐ balance automatically — nothing further to migrate.",
            parse_mode='html'
        )
    seed_star = treasury.get("seed_star_balance", BOT3_SEED_STAR_BALANCE)
    now_star = treasury.get("star_balance", seed_star)
    net_profit = round(now_star - seed_star, 2)
    await bot3_treasury_col.update_one({"key": "bot3"}, {"$set": {"historical_claimed": True}})
    if net_profit <= 0:
        return await event.reply(
            f"📊 <b>No historical profit to claim</b> (net: <code>{format_star_plain(net_profit)}⭐</code>).\n"
            f"Marked as migrated — future bot3 activity now flows to your ⭐ balance automatically.",
            parse_mode='html'
        )
    await users_catcher_col.update_one({"user_id": OWNER_ID}, {"$inc": {"star_balance": net_profit}}, upsert=True)
    await event.reply(
        f"🏦 <b>Treasury claimed!</b>\n"
        f"+<code>{format_star_plain(net_profit)}⭐</code> transferred to your balance.\n\n"
        f"<i>From here on bot3_treasury_adjust() mirrors every fee/burn/win/loss straight to "
        f"your ⭐ balance in real time — this command never needs to run again.</i>",
        parse_mode='html'
    )
# =========================================================
# 🎰🏀🎲 CASINO — NATIVE TELEGRAM DICE-MESSAGE GAMES
# =========================================================
# 🩹 REWRITTEN (2026-08, per owner request): /slot used to hand-animate its reel by repeatedly
# EDITING one message with random symbols, then editing it again for the real result — each
# spin was several edit calls, which is exactly the kind of per-chat edit burst that risks
# tripping Telegram's flood limits under load. Now sends Telegram's own native dice message
# (InputMediaDice) instead: Telegram animates it client-side for free, the outcome is decided
# server-side and comes back in the SAME send call (no polling, no edits at all), and it just
# looks like the real thing instead of a text reel. 🏀 Basketball and 🎲 Dice later joined /slot
# on this same engine — see _casino_play below for the full flow, and each game's own paytable
# note further down for how its raw dice value maps to a win.
#
# 🩹 REVERTED FOR /slot ONLY (2026-09, per owner request): the native dice message can never be
# edited — Telegram simply doesn't allow it — so every single spin still had to SEND one brand
# new message no matter what, even after the edit-in-place result card (_casino_edit_or_send)
# stopped the summary text itself from piling up. That leftover fresh-send-per-spin was the last
# real source of group spam, and there's no way to close it while /slot still rides Telegram's
# own dice. So /slot is back to drawing its own outcome bot-side (bot-drawn tiers replacing the
# native value-decode below) — same
# spirit as the pre-2026-08 system this file used to run, MINUS the old multi-edit spin
# animation that caused a different flood problem back then: a spin is now exactly ONE edit,
# landing straight on the static final result, no fake spinning frames. 🏀 Basketball and 🎲 Dice
# were NOT part of this request and still use Telegram's native dice exactly as before (2026-08
# section below, untouched) — every /slot/basketball/dice spin+roll now genuinely never sends a
# fresh message once the shared result-card session exists, EXCEPT basketball/dice's own native
# dice animation message, which is unavoidable for those two specifically.
#
# 🩹 REVERTED BACK TO NATIVE, /slot ONLY (2026-09, round 2, per owner request — players missed
# the real animation enough to bring it back): the bot-drawn detour above lasted about as long
# as it took players to notice. Reintroducing InputMediaDice for /slot necessarily reintroduces
# the per-spin animation-message send this whole rewrite exists to minimize (native dice can't
# be edited, full stop — see above) — that trade was made deliberately, not overlooked, because
# the alternative (fake it with a native-looking animation that doesn't decide the real payout)
# would have broken the "shown result always matches what's paid" honesty rule from the FIRST
# request in this whole saga. See SLOT_JACKPOT_MULT below (right where /slot's paytable lives)
# for how the jackpot rate/multiplier trade-off is handled instead — Telegram's dice value is
# genuinely server-random and can't be reweighted, so probability control was given up in favor
# of multiplier control, which stays fully honest.
#
# Dice messages can't be edited (Telegram limitation) and don't carry buttons of their own, so
# there's no more persistent "session" screen to edit bets on — every /slot [amount] (or tap of
# a preset/"spin again" button) is now one complete, self-contained spin: deduct → send the
# native dice → resolve the result → reply with a summary + a "🔄 Spin Again" button. This also
# drops the old min-3-spins-before-cashout rule and the cashout profit tax entirely — winnings
# already paid out per-spin before, so there was never actually anything held back to "cash
# out"; that screen was pure UI, not a real escrow.
SLOT_MIN_BET = 1
# 🩹 (per owner request) — hard cap, ⭐ per spin. Also see AML_BUST_THRESHOLD's own fix note
# just below (try_deduct_bet_bot3): that ceiling used to sit at a broken 10⭐ post-USD-retirement,
# which would have silently blocked any bet at or above this new max — raised alongside this.
SLOT_MAX_BET = 50_000
SLOT_BET_PRESETS = [10, 50, 100, 1_000, 10_000, 50_000]
# 🩹 NEW (per owner request): a repeat spin now EDITS the player's existing result message in
# this chat instead of sending a brand new banner+caption every time — see
# _casino_edit_or_send(). This is how long a message stays "reusable" before a spin just sends
# a fresh one instead (message got too old to edit, or Telegram simply won't have it anymore).
DICE_SESSION_MAX_AGE = 600  # seconds (10 minutes)

# 🚦 NEW (per owner request — anti-spam): a player gets CASINO_COOLDOWN_TRIGGER plays across
# /slot, /basketball, and /dice (they share this same counter — it's one casino, not three),
# then is locked out of all three for CASINO_COOLDOWN_SECONDS. The counter resets to 0 the
# moment a lock is applied, so the same rule fires again on their next burst. This is on top
# of — not instead of — the edit-in-place fix above: that stops each individual spin from
# spamming a fresh message, this stops a player from spinning nonstop in the first place.
CASINO_COOLDOWN_TRIGGER = 10   # plays
CASINO_COOLDOWN_SECONDS = 60   # seconds locked out once the trigger count is hit

# 🚦 NEW (2026-09, per owner report — screenshots showed slot spam again despite the edit-in-
# place fix above): editing the SAME message forever is exactly what that fix intends, but
# Telegram itself rate-limits how many times one message can be edited in a burst — and at the
# time this was added, /slot had briefly lost its ~2.5s animation-sync sleep (mid-rewrite, since
# reverted — see the paytable's own history above), so nothing was pacing rapid repeat taps and
# that limit became realistic to hit. Once an edit call starts failing for THAT reason,
# _casino_edit_or_send's existing "edit failed -> just send fresh" fallback kicks in — which is
# exactly the spam the fix was supposed to prevent, just moved one layer down. Kept in place even
# after slot's delay came back — it's a cheap, generically-useful safeguard against the same
# rate limit from ANY source (fast double-taps, retries, etc.), not something specific to that
# one now-reverted cause. Fix: retire a message after CASINO_SESSION_MAX_PLAYS reuses no matter
# what (well under Telegram's limit), explicitly DELETING it as the next fresh one takes over —
# so the chat never accumulates orphaned old cards, whether from hitting this cap, from a session
# simply going stale (DICE_SESSION_MAX_AGE), or from a genuine edit failure of any other kind.
# 🩹 Coincidentally shares its default value with CASINO_COOLDOWN_TRIGGER above — intentional in
# spirit (both exist to bound one continuous play burst) but NOT the same counter; they're
# tuned independently on purpose, change either without worrying about the other.
CASINO_SESSION_MAX_PLAYS = 10  # plays reusing one message before it's retired (deleted) and a fresh one starts

def _check_casino_cooldown(user_id):
    """Returns (blocked: bool, remaining_seconds: int). Call once per play attempt, BEFORE any
    bet validation/deduction — a locked-out player shouldn't be able to burn through the check
    by feeding it a bad amount. Owner is exempt, same convention as is_on_cooldown()."""
    if user_id == OWNER_ID:
        return False, 0
    now = time.time()
    state = casino_play_counts.setdefault(user_id, {"count": 0, "locked_until": 0})
    if now < state["locked_until"]:
        return True, int(state["locked_until"] - now)
    state["count"] += 1
    if state["count"] >= CASINO_COOLDOWN_TRIGGER:
        state["count"] = 0
        state["locked_until"] = now + CASINO_COOLDOWN_SECONDS
    return False, 0

# ---- Anti-Whale ----
# 💡 These are game-balance numbers, not technical constants — tune them to taste:
SPIN_BURN_RATE = 0.02            # 2% of the bet is taken as a flat "casino play fee" every spin, win or lose
# 🩹 CHANGED (per owner request): the player-facing label for this was "မီးရှို့ခ" (burn fee) —
# owner found it scared players off, so it's now shown as "ကာစီနို ဆော့ခ" (casino play fee).
# SPIN_BURN_RATE itself is unchanged, just the wording.
# CASHOUT_PROFIT_TAX — REMOVED along with the old session/cashout screen (2026-08): the native
# dice rewrite pays out every spin immediately, so there's nothing left to "cash out" or tax.
# 🩹 2026-08 USD RETIREMENT: WEALTH_THRESHOLD / WHALE_TAX_TIERS used to be denominated in USD
# wallet_balance. Re-tuned to ⭐ Star balance, at a scale proportional to the rest of the
# post-migration Star economy (Owner Shop prices top out at 2,500⭐ — see RARITY_VLT_PRICE's
# ⭐-scale sibling, catch_star_reward) — owner-adjustable, tune to taste.
WEALTH_THRESHOLD = 500          # ⭐ Star balance above which whale tax starts applying at all
# 🩹 CHANGED (per owner request): the old system applied a single flat 50% cut to EVERY whale,
# whether they were $1,001 or $10,000,000 — a hard cliff that felt punishing right at the
# threshold and prompted player complaints. Replaced with a progressive table: the cut now
# scales up smoothly with wealth, like a tax bracket. Sorted ascending; a balance's multiplier
# is whichever tier it satisfies, using the highest (richest) tier that applies.
WHALE_TAX_TIERS = [
    (500,     0.98),   # 500⭐   – 2,000⭐    → 2% cut
    (2_000,   0.955),  # 2,000⭐ – 10,000⭐   → 4.5% cut
    (10_000,  0.925),  # 10,000⭐ – 50,000⭐  → 7.5% cut
    (50_000,  0.885),  # 50,000⭐+            → 11.5% cut
]

def get_whale_multiplier(balance):
    """Returns the raw-winnings multiplier for a given star_balance, per WHALE_TAX_TIERS.
    1.0 (no cut at all) for anyone at or below the lowest tier's threshold."""
    multiplier = 1.0
    for threshold, mult in WHALE_TAX_TIERS:
        if balance > threshold:
            multiplier = mult
    return multiplier

def apply_whale_tax(balance, raw_win):
    """Applies the progressive whale tax to a raw winnings amount. Returns
    (taxed_win, note_html) — note_html is "" when no tax applies (not a whale, or no win)."""
    if raw_win <= 0:
        return raw_win, ""
    multiplier = get_whale_multiplier(balance)
    if multiplier >= 1.0:
        return raw_win, ""
    taxed = round(raw_win * multiplier, 2)
    cut_pct = round((1 - multiplier) * 100)
    note = f"\n🐋 <b>ငွေကြေးရှိသူအခွန် ({cut_pct}% လျှော့):</b> <code>-{format_star_plain(round(raw_win - taxed, 2))}</code>"
    return taxed, note

# HILO_HOUSE_EDGE / _hilo_win_probability / _hilo_fair_multiplier — REMOVED along with HI-LO
# itself (2026-08, per owner request: all casino games except /slot are gone).

# =========================================================
# 🎰🏀🎲 CASINO — NATIVE TELEGRAM DICE-MESSAGE GAMES
# =========================================================
# 🩹 EXPANDED (2026-08, per owner request): /slot was the only game left after every hand-
# animated one was removed. Two more native-dice game types join it now — 🏀 Basketball and 🎲
# Dice — reusing the exact same engine (bet validation, burn fee, whale tax, treasury, AML,
# edit-in-place result card) since Telegram's dice-message mechanic works identically for all
# of them, just with a different emoji and a different raw value range. See _payout_and_status
# for each game's actual odds/payout, and GAMES below for the per-game display config.
#
# 🩹 ALSO (per owner report — "the banner+caption keeps popping up like spam on every spin"):
# repeat spins/rolls in the same chat now EDIT one shared result message instead of sending a
# fresh one — see _casino_edit_or_send(). The native dice ANIMATION message itself still has
# to be a fresh send every time (Telegram dice messages can't be edited, full stop), but that
# one gets auto-deleted after a short delay in groups either way (schedule_game_cleanup), so it
# never piles up. No more "━━━" divider lines in the result card either — matches the plainer,
# fancy-font style /check moved to.
GAMES = {
    # 🩹 "anim_delay" restored for slot (2026-09, round 2) — back on native dice, so the result
    # card needs to wait for Telegram's own client-side animation to visually finish again,
    # exactly like basketball/dice already do.
    "slot":  {"emoticon": "🎰", "name": "SLOT MACHINE", "anim_delay": 2.5, "example": "/slot 100"},
    "bball": {"emoticon": "🏀", "name": "BASKETBALL",   "anim_delay": 2.0, "example": "/basketball 100"},
    "dice":  {"emoticon": "🎲", "name": "DICE ROLL",    "anim_delay": 1.5, "example": "/dice 100"},
}

# ---- 🎰 Slot paytable — decoding Telegram's own native 🎰 dice value (1-64) ----
# 🩹 REVERTED BACK TO NATIVE (2026-09, round 2, per owner request): see the big note above the
# CASINO header for the full why. In short — the real animation and a custom jackpot rate can't
# coexist (Telegram's dice value is genuinely server-random, not bot-chosen), so this goes back
# to decoding Telegram's own value like basketball/dice always did, and the inflation concern is
# addressed via SLOT_JACKPOT_MULT below instead of via probability.
SLOT_SYMBOL_NAMES = ("bar", "grapes", "lemon", "seven")
SLOT_SYMBOL_DISPLAY = {"bar": "BAR", "grapes": "🍇", "lemon": "🍋", "seven": "7️⃣"}

def _slot_decode(value):
    """Decodes a Telegram 🎰 dice value (1-64) into its 3 reel symbols, for DISPLAY only —
    payout itself uses the direct value lookup below, not this. This mapping is Telegram's own
    (undocumented but stable, and relied on by every open-source Telegram casino bot that reads
    dice results): treat (value-1) as a base-4 number; each digit 0-3 selects one reel's symbol
    from SLOT_SYMBOL_NAMES, least-significant digit first."""
    v = value - 1
    reels = []
    for _ in range(3):
        reels.append(SLOT_SYMBOL_NAMES[v % 4])
        v //= 4
    return reels  # [reel1, reel2, reel3]

# Of the 64 equally-likely outcomes: exactly 1 is 777 (value 64), 3 are any-other-triple
# (bar×3=1, grapes×3=22, lemon×3=43), and 9 are "two sevens plus one other symbol, in any of the
# 3 positions" (seven,seven,X / seven,X,seven / X,seven,seven, X≠seven: values 16,32,48 [X in
# reel 3], 52,56,60 [X in reel 2], 61,62,63 [X in reel 1]) — the remaining 51 pay nothing.
# 🩹 BUG FOUND & FIXED (2026-09, round 2, while reverting slot back to native for this exact
# request): SLOT_TWO_SEVENS_VALUES had ONLY ever listed {16, 32, 48} — the 3 cases where the
# non-seven symbol lands in the LAST reel. The other 6 real two-sevens values (52, 56, 60, 61,
# 62, 63), where the non-seven symbol lands in reel 1 or 2 instead, were falling through to
# "lose" — meaning those spins visually showed 7️⃣7️⃣ (a two-sevens win by every visible
# appearance) while actually paying nothing at all, a direct violation of the "shown result
# must always match the payout" rule from the very first request in this whole thread. This
# bug predates every change made in this conversation — it's been in the paytable since the
# original 2026-08 native-dice build, silently under-paying (and effectively lying to) players
# in 6/64 (~9.4%) of ALL slot spins, jackpot or not, the entire time.
# 🔢 KNOCK-ON EFFECT ON RTP — this is likely a bigger driver of the Star inflation being fought
# in this thread than the jackpot rate alone: two-sevens actually lands 9/64 (≈14.06%) of the
# time, not the 3/64 (≈4.69%) the old odds comment (and every RTP estimate given earlier in
# this thread) assumed. At the ORIGINAL multipliers (30x/7x/2x) this means the slot machine's
# real historical RTP was (30×1 + 7×3 + 2×9)/64 = 69/64 ≈ 107.8% — mathematically guaranteed to
# pay out MORE than it collected, on average, forever. That alone could fully explain ongoing
# Star inflation independent of anything about jackpot frequency.
SLOT_JACKPOT_VALUE = 64
SLOT_TRIPLE_VALUES = {1, 22, 43}
SLOT_TWO_SEVENS_VALUES = {16, 32, 48, 52, 56, 60, 61, 62, 63}
# 🩹 RETUNED (2026-09, round 2, per owner request — Star inflation, now against the CORRECTED
# odds above): native probabilities can't be changed (Telegram, server-side — see the paytable's
# revert note above), so all three multipliers are retuned together instead, to land back on
# roughly the same ~48% overall RTP this thread had already settled on for the (since-reverted)
# 1/500 bot-drawn system — the number to beat, not a new target invented here. Solving
# (J×1 + T×3 + W×9)/64 ≈ 0.48 while keeping each tier's relative size similar to the original
# 30:7:2 shape (jackpot biggest, triple solid, two-sevens a small "close call" consolation)
# gives J=13, T=3, W=1 → (13 + 9 + 9)/64 = 31/64 ≈ 48.4%. All three are independent constants —
# tune any of them directly for a different balance; this is the reasoning that produced these
# specific numbers, not a rule that they have to stay exactly here.
SLOT_JACKPOT_MULT = 13
SLOT_TRIPLE_MULT = 3
SLOT_TWO_SEVENS_MULT = 1

# ---- 🏀 Basketball — Telegram value 1-5. 4 or 5 = a made basket (undocumented but stable
# Telegram behavior); 1-3 = a miss. 🩹 CHANGED (per owner request): flat 2x on ANY make, no
# swish/bank-in distinction anymore. 2/5 chance of a make → 2×0.4 = 80% RTP. ----
BBALL_IN_VALUES = {4, 5}
BBALL_MAKE_MULT = 2

# ---- 🎲 Dice — Telegram value 1-6, uniform. 🩹 CHANGED (per owner request — owner's original
# spec was 4→1.5x, 5→3x, 6→5x, which averages to (1.5+3+5)/6 ≈ 158% RTP: a guaranteed-profit
# game that would eventually drain the whole casino bankroll, since a player betting forever
# would be MORE likely to profit than not on every single roll). Same shape kept — 6 pays the
# most, 5 pays a middle amount, 4 pays the least, 1-3 lose — just scaled down to
# (0.5+1.5+3)/6 ≈ 83.3% RTP, matching this game's original single-tier design's RTP exactly. ----
DICE_PAYOUTS = {4: 0.5, 5: 1.5, 6: 3}

def _payout_and_status(game_key, value):
    """Returns (multiplier, tier) — tier is "jackpot"/"big"/"small"/"lose", used for both the
    reward math and picking which status line/wording to show. All three games decide
    everything from Telegram's own raw native-dice value (🩹 /slot is back to this too, 2026-09
    round 2 — see the paytable's revert note above)."""
    if game_key == "slot":
        if value == SLOT_JACKPOT_VALUE:
            return SLOT_JACKPOT_MULT, "jackpot"
        if value in SLOT_TRIPLE_VALUES:
            return SLOT_TRIPLE_MULT, "big"
        if value in SLOT_TWO_SEVENS_VALUES:
            return SLOT_TWO_SEVENS_MULT, "small"
        return 0, "lose"
    if game_key == "bball":
        if value in BBALL_IN_VALUES:
            return BBALL_MAKE_MULT, "big"
        return 0, "lose"
    if game_key == "dice":
        if value == 6:
            return DICE_PAYOUTS[6], "jackpot"
        if value == 5:
            return DICE_PAYOUTS[5], "big"
        if value == 4:
            return DICE_PAYOUTS[4], "small"
        return 0, "lose"
    return 0, "lose"

def _result_line(game_key, value):
    """The one line showing what actually happened — reel symbols for slot, a shot description
    for basketball, the rolled pip-face for dice."""
    if game_key == "slot":
        reels = _slot_decode(value)
        return " │ ".join(SLOT_SYMBOL_DISPLAY[r] for r in reels)
    if game_key == "bball":
        return {4: "🏀 In the hoop!", 5: "🏀 SWISH! Nothing but net"}.get(value, "🏀 Missed the hoop")
    if game_key == "dice":
        pip = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}.get(value, str(value))
        return f"{pip} Rolled a {value}"
    return str(value)

def _status_headline(tier, win, mult):
    if tier == "jackpot":
        return f"🔥 <b>JACKPOT!</b> +{format_star_plain(win)} <code>({mult}x)</code>"
    if tier == "big":
        return f"✨ <b>Big win! +{format_star_plain(win)}</b> <code>({mult}x)</code>"
    if tier == "small":
        return f"🎉 <b>+{format_star_plain(win)}</b> <code>({mult}x)</code>"
    return "💔 <b>No win this time</b>"

def _game_result_buttons(user_id, bet, game_key):
    row1 = [Button.inline(f"🔄 ထပ်ကစား ({format_star_plain(bet)})", data=f"cg_go_{game_key}_{user_id}_{bet}")]
    row1 += [Button.inline(g["emoticon"], data=f"cg_go_{k}_{user_id}_{bet}") for k, g in GAMES.items() if k != game_key]
    preset_row = [Button.inline(f"{amt:,}⭐", data=f"cg_go_{game_key}_{user_id}_{amt}") for amt in SLOT_BET_PRESETS[:3]]
    return [row1, preset_row]

async def _casino_out(event, **kwargs):
    """Send a casino-flow message via bot3 if we're in the force-join room (same load-shedding
    reasoning as _out(), just above the CASINO GAMES section) — falls back to the event's own
    client (bot1, everywhere these commands are actually received) on any bot3 failure, or
    immediately if we're not in that room at all. Unlike _out(), this accepts file= too, since
    the native dice send needs it."""
    chat_id = event.chat_id
    if chat_id == FORCE_SUB_CHAT_ID and bot3 is not None:
        try:
            return await bot3.send_message(chat_id, **kwargs)
        except Exception as e:
            print(f"⚠️ Guard Bot send failed in force-join room (casino), falling back to bot1: {e}")
    return await event.client.send_message(chat_id, **kwargs)

async def _casino_banner_media():
    """The owner-set image/video shown above every game's menu and result card — shared across
    slot/basketball/dice (see /addslotbanner). Same reply-to-media storage trick as /addbalance's
    wallet banner: the media is forwarded once into SPECIFIC_CONTROL_GROUP and only its
    message_id is kept. Returns None if none is set or it couldn't be fetched, in which case
    callers just send text-only."""
    banner_doc = await bot_settings_col.find_one({"_id": "slot_banner"})
    storage_msg_id = banner_doc.get("storage_msg_id") if banner_doc else None
    if not storage_msg_id:
        return None
    try:
        storage_msg = await bot1.get_messages(SPECIFIC_CONTROL_GROUP, ids=storage_msg_id)
        return storage_msg.media if (storage_msg and storage_msg.media) else None
    except Exception:
        return None

async def _casino_edit_or_send(event, user_id, text, buttons, banner):
    """🩹 NEW (per owner report — "banner/caption keeps popping up like spam"): tries to EDIT
    the player's existing result message in THIS chat first — only sends a brand new one (with
    the banner re-attached) if there's no usable session yet, it's gone stale
    (DICE_SESSION_MAX_AGE), it's already been reused CASINO_SESSION_MAX_PLAYS times, or the
    edit itself fails for any reason (message deleted, too old for Telegram to edit, rate-
    limited, etc.). This is what stops repeat spins from flooding the chat with a fresh
    banner+caption every single time — only the native dice ANIMATION is ever a fresh send
    (Telegram dice messages can't be edited), this result card underneath it now reuses one
    message per player per chat instead.

    🩹 NEW (2026-09, per owner report): whichever of those reasons triggers a fresh send, the
    OLD message (if any) is explicitly DELETED first — not just abandoned — so a player's chat
    never accumulates a trail of retired result cards either. Returns the msg_id actually used
    either way, so callers that need to track it further (e.g. registering a reply-based bet
    prompt against it) don't have to duplicate this same edit-or-send logic themselves."""
    chat_id = event.chat_id
    session_key = (user_id, chat_id)
    session = active_dice_sessions.get(session_key)
    if session and (time.time() - session["ts"]) < DICE_SESSION_MAX_AGE and session.get("plays", 0) < CASINO_SESSION_MAX_PLAYS:
        client = bot3 if (session["via"] == "bot3" and bot3 is not None) else bot1
        try:
            await client.edit_message(chat_id, session["msg_id"], text, parse_mode='html', buttons=buttons)
            session["ts"] = time.time()
            session["plays"] = session.get("plays", 0) + 1
            return session["msg_id"]
        except Exception:
            pass  # stale/deleted/too-old-to-edit/rate-limited — fall through to a fresh send below

    if session:
        # 🗑️ Retiring this message (cap hit, stale, or the edit above just failed) — delete it
        # outright instead of leaving it behind. Best-effort: it may already be gone (a player
        # deleted it themselves, or it's too old for the bot to touch), which isn't an error
        # worth logging, just a no-op here.
        old_client = bot3 if (session["via"] == "bot3" and bot3 is not None) else bot1
        try:
            await old_client.delete_messages(chat_id, [session["msg_id"]])
        except Exception:
            pass

    send_kwargs = {"message": text, "parse_mode": 'html', "buttons": buttons}
    if banner:
        send_kwargs["file"] = banner
    result_msg = await _casino_out(event, **send_kwargs)
    via = "bot3" if (chat_id == FORCE_SUB_CHAT_ID and bot3 is not None) else "bot1"
    active_dice_sessions[session_key] = {"msg_id": result_msg.id, "via": via, "ts": time.time(), "plays": 1}
    return result_msg.id

async def _casino_play(event, user_id, bet, mention, game_key):
    """One complete, self-contained spin/roll/shot: validate → deduct bet → send Telegram's own
    native dice message (the actual animation the player sees) → resolve the server-decided
    result → pay out → show a summary + buttons, editing the player's existing result message
    in this chat when possible. Shared by every game's command and every button (presets,
    "play again", game-switch) — event can be either a NewMessage or a CallbackQuery event,
    both work with everything called below.

    🩹 CHANGED (per owner request): no more casino play fee (SPIN_BURN_RATE) or whale tax on
    winnings — every game's own paytable is the only house edge now (see _payout_and_status;
    all three stay comfortably under 100% RTP on their own, so this is still never a
    guaranteed-profit setup, just a more generous one than before).

    🩹 /slot briefly split off this shared native-dice flow (2026-09) and went bot-drawn, then
    was REVERTED BACK (2026-09, round 2, per owner request — players missed the real animation)
    — see the CASINO section's changelog comments and slot's own paytable note for the full
    story. All three games are back to sharing this exact same flow below."""
    game = GAMES[game_key]
    chat_id = event.chat_id
    back_btn = [[Button.inline("🔙 Back", data="nav_back_home")]]

    # 🚦 Anti-spam cooldown check — BEFORE any validation, so a locked-out player mashing the
    # button or retyping /slot with junk amounts still can't slip a fresh message through.
    blocked, remaining = _check_casino_cooldown(user_id)
    if blocked:
        text = (
            f"⏳ {mention} — {CASINO_COOLDOWN_TRIGGER} ကြိမ် ဆက်တိုက်ကစားလို့ ခဏနားပေးထားပါတယ်။\n"
            f"<code>{remaining}</code> စက္ကန့်အကြာ ထပ်ကစားနိုင်ပါမယ်။"
        )
        return await _casino_edit_or_send(event, user_id, text, back_btn, None)

    if bet < SLOT_MIN_BET:
        text = f"❌ အနည်းဆုံး {SLOT_MIN_BET}⭐ လောင်းပါ။"
        return await _casino_edit_or_send(event, user_id, text, back_btn, None)
    if bet > SLOT_MAX_BET:
        text = f"❌ တစ်ကြိမ်လျှင် အများဆုံး {SLOT_MAX_BET:,}⭐ ပဲ လောင်းလို့ရပါတယ်။"
        return await _casino_edit_or_send(event, user_id, text, back_btn, None)

    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    balance = user_doc.get("star_balance", 0) if user_doc else 0
    if balance < bet:
        text = f"❌ မင်းမှာ {format_star_plain(balance)} ပဲရှိတယ်။"
        return await _casino_edit_or_send(event, user_id, text, back_btn, None)

    if not await try_deduct_bet_bot3(event, user_id, bet):
        return  # try_deduct_bet_bot3 already sent the AML-jail/bust or balance message itself
    await bot3_treasury_adjust(usd=bet)

    # 🎲 THE actual native Telegram animation — this is what the player sees play out.
    try:
        dice_msg = await _casino_out(event, file=types.InputMediaDice(emoticon=game["emoticon"]))
        value = dice_msg.media.value
    except Exception as e:
        # Refund everything taken so far — never eat a bet if we couldn't even show the roll.
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"star_balance": bet}})
        await bot3_treasury_adjust(usd=-bet)
        print(f"❌ /{game_key} dice send failed, refunded {user_id}: {e}")
        text = "⚠️ ဂိမ်းစက် ခေတ္တပျက်နေပါတယ် — ငွေပြန်အမ်းပြီးပါပြီ၊ ထပ်ကြိုးစားကြည့်ပါ။"
        return await _casino_edit_or_send(event, user_id, text, back_btn, None)

    # 🩹 The result is already decided server-side and sitting in `value` right now — this
    # sleep is PURELY cosmetic, so the result card lands right as Telegram's own client-side
    # animation visually finishes instead of appearing while it's still playing.
    await asyncio.sleep(game["anim_delay"])

    mult, tier = _payout_and_status(game_key, value)
    win = round(bet * mult, 2) if mult else 0
    if win > 0:
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"star_balance": win}})
        await bot3_treasury_adjust(usd=-win)
    net = win - bet

    result_line = _result_line(game_key, value)
    status_line = _status_headline(tier, win, mult)
    net_emoji = "📈" if net > 0 else ("📉" if net < 0 else "➖")
    # 🩹 CHANGED (per owner report — "the ━━━ dividers aren't pretty"): no more divider lines —
    # a plain, compact card, matching the style /check moved to.
    text = (
        f"{game['emoticon']} <b>{game['name']}</b>\n"
        f"👤 {mention} · 💵 <code>{format_star_plain(bet)}</code>\n\n"
        f"<b>{result_line}</b>\n\n"
        f"{status_line}\n"
        f"{net_emoji} <b>အသားတင်:</b> <code>{'+' if net >= 0 else ''}{format_star_plain(net)}</code>"
    )
    banner = await _casino_banner_media()
    await _casino_edit_or_send(event, user_id, text, _game_result_buttons(user_id, bet, game_key), banner)

    # 🩹 The public group announcement below is deliberately reserved for genuinely RARE wins
    # only — slot's true 777 jackpot is native Telegram odds, 1/64 (≈1.6%; 2026-09 round 2 —
    # see the paytable's revert note for why this went back to native, with the payout
    # multiplier retuned instead of the rate). "jackpot" is also used as dice's win-tier LABEL
    # for styling (the status line/color), but dice's win is 1/6 (≈16.7%) — announcing THAT
    # loudly to the whole group every time would itself become the exact spam this rewrite was
    # meant to cut down on. Same reasoning keeps basketball's swish (1/5) off this list too.
    if game_key == "slot" and tier == "jackpot":
        await _casino_out(event, message=f"🔥 {mention} {game['name']} Jackpot <b>{format_star_plain(win)}</b> ပေါက်သွားပြီ!", parse_mode='html')

    # 🩹 Only the native dice ANIMATION message gets auto-deleted — the result card is now
    # reused via edit (see _casino_edit_or_send), so it's never piling up the way repeat fresh
    # sends used to.
    if not event.is_private:
        schedule_game_cleanup(event.client, chat_id, dice_msg.id, delay=20)

async def _game_cmd_handler(event, game_key):
    if is_duplicate_event(event): return
    user_id = event.sender_id
    bet_str = event.pattern_match.group(1)
    mention = await get_html_mention(event, user_id)
    game = GAMES[game_key]

    if not bet_str:
        # 🩹 FIX (per owner report — "if someone spams the /slot command it becomes spam"):
        # this used to unconditionally send a brand NEW message (banner and all) every single
        # time the bare command was typed, completely bypassing the edit-in-place session —
        # spamming /slot with no amount was actually WORSE than spamming it with one, since
        # /slot [amount] at least reuses the result card via _casino_edit_or_send. Now matches
        # /casino's own per-game prompt exactly (same reply-based flow, same session-reuse),
        # so typing the bare command repeatedly edits the same message instead of flooding new
        # ones — there's only ONE way this menu ever gets shown now, not two different UIs for
        # what's functionally the same "pick an amount" step.
        text = (
            f"{game['emoticon']} <b>{game['name']}</b>\n\n"
            f"{bold_italic_serif('Reply with your')} ⭐ {bold_italic_serif('Star Amount, bro / sis!')}\n"
            f"<i>အနည်းဆုံး {SLOT_MIN_BET}⭐, အများဆုံး {SLOT_MAX_BET:,}⭐</i>"
        )
        banner = await _casino_banner_media()
        msg_id = await _casino_edit_or_send(event, user_id, text, [[Button.inline("🔙 Back", data="nav_back_home")]], banner)
        pending_casino_bet[user_id] = {
            "chat_id": event.chat_id, "prompt_msg_id": msg_id, "game_key": game_key,
            "expiry": time.time() + CASINO_BET_PROMPT_TTL,
        }
        return

    try:
        bet = round(float(bet_str), 2)
    except ValueError:
        text = f"❌ ဂဏန်းအမှန်ထည့်ပါ — ဥပမာ <code>{game['example']}</code>"
        return await _casino_edit_or_send(event, user_id, text, [[Button.inline("🔙 Back", data="nav_back_home")]], None)

    await _casino_play(event, user_id, bet, mention, game_key)

# ==========================================
# 🎮 /casino — menu-driven entry point (per owner request), separate from typing /slot,
# /basketball, or /dice directly (both paths stay fully supported side by side). Shows a
# picker of all 3 games; tapping one turns this SAME message into a "reply with your bet"
# prompt, and replying to it plays that game, editing this SAME message again with the result
# — the whole interaction stays inside one message rather than spawning a new one per step.
# ==========================================
CASINO_WELCOME_BONUS = 50_000  # ⭐ one-time, first /casino visit ever — see the atomic claim below
CASINO_BET_PROMPT_TTL = 300     # seconds the "reply with your bet" prompt stays valid

def _casino_menu_buttons(user_id):
    game_row = [Button.inline(g["emoticon"], data=f"cgmenu_{k}_{user_id}") for k, g in GAMES.items()]
    # 🩹 NEW (2026-09, per owner request): 🃏 Card Game alongside the 3 vs-the-house games — not
    # part of GAMES/_casino_play (it's PVP, not vs-house — see its own section comment), but
    # tapping it still reuses this exact same "pick an amount" flow (casino_menu_pick_cb /
    # casino_bet_reply_handler), just routed to _start_cardgame_lobby instead of _casino_play.
    game_row.append(Button.inline("🃏", data=f"cgmenu_cardgame_{user_id}"))
    return [game_row, [Button.inline("🔙 Back", data="nav_back_home")]]

@bot1.on(events.NewMessage(pattern=own_pattern(r'(?i)^[/.]casino(?:@\w+)?$', 'bot1')))
async def casino_menu_cmd(event):
    if is_duplicate_event(event): return
    user_id = event.sender_id
    mention = await get_html_mention(event, user_id)

    # 🎉 One-time welcome bonus — atomic claim (find_one_and_update), same pattern as the
    # Premium daily Star gift's date-gated claim elsewhere in this file, just with a boolean
    # flag instead of a date since this only ever fires once per account, not once per day.
    claimed = await users_catcher_col.find_one_and_update(
        {"user_id": user_id, "casino_welcome_claimed": {"$ne": True}},
        {"$set": {"casino_welcome_claimed": True}}
    )
    bonus_line = ""
    if claimed:
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"star_balance": CASINO_WELCOME_BONUS}})
        bonus_line = f"🎉 {bold_italic_serif('Welcome to the casino!')} +{format_star_plain(CASINO_WELCOME_BONUS)} {bold_italic_serif('is on the house — good luck!')}\n\n"

    text = (
        f"{bonus_line}"
        f"{bold_italic_serif('Wanna grow your')} ⭐ {bold_italic_serif('Stars by playing games?')}\n"
        f"{bold_italic_serif('Try these games below!')} 🎮"
    )
    banner = await _casino_banner_media()
    send_kwargs = {"message": text, "parse_mode": 'html', "buttons": _casino_menu_buttons(user_id)}
    if banner:
        send_kwargs["file"] = banner
    await _casino_out(event, **send_kwargs)

@bot1.on(events.CallbackQuery(pattern=r'^cgmenu_(slot|bball|dice|cardgame)_(\d+)$'))
async def casino_menu_pick_cb(event):
    game_key = event.pattern_match.group(1)
    if isinstance(game_key, bytes):
        game_key = game_key.decode('utf-8')
    owner_user_id = int(event.pattern_match.group(2))
    # 🩹 (per owner request) — only the person who opened /casino can use these buttons.
    if event.sender_id != owner_user_id:
        return await event.answer("⚠️ ဒါ မင်းရဲ့ Casino Menu မဟုတ်ဘူး — /casino ကို ကိုယ်တိုင်ရိုက်ပါ။", alert=True)
    if not claim_single_tap(event):
        return await event.answer("⏳ ခဏစောင့်", alert=False)

    # 🃏 NEW (2026-09, per owner request) — PVP, not vs-house, so it's not in GAMES and doesn't
    # get an edit-in-place RESULT session the way slot/bball/dice do below: its own lobby
    # message is tracked separately in active_cardgame_lobbies once _start_cardgame_lobby opens
    # it, not in active_dice_sessions.
    if game_key == "cardgame":
        if event.is_private:
            return await event.answer(
                f"🃏 Card Game ကို Group ထဲမှာပဲ ကစားလို့ရပါတယ် — အနည်းဆုံး {CARDGAME_MIN_PLAYERS} ယောက် လိုအပ်လို့ပါ။",
                alert=True
            )
        text = (
            f"🃏 <b>CARD GAME</b>\n\n"
            f"{bold_italic_serif('Reply with your')} ⭐ {bold_italic_serif('Star Amount, bro / sis!')}\n"
            f"<i>ကတ် 1-10 ထဲက အကြီးဆုံးရသူ အနိုင် — ကစားသူ {CARDGAME_MIN_PLAYERS}-{CARDGAME_MAX_PLAYERS} ဦး</i>\n"
            f"<i>အနည်းဆုံး {CARDGAME_MIN_BET}⭐, အများဆုံး {CARDGAME_MAX_BET:,}⭐</i>"
        )
        await event.edit(text, parse_mode='html', buttons=[[Button.inline("🔙 Back", data="nav_back_home")]])
        await event.answer()
        msg = await event.get_message()
        pending_casino_bet[owner_user_id] = {
            "chat_id": event.chat_id, "prompt_msg_id": msg.id, "game_key": "cardgame",
            "expiry": time.time() + CASINO_BET_PROMPT_TTL,
        }
        return

    game = GAMES[game_key]
    text = (
        f"{game['emoticon']} <b>{game['name']}</b>\n\n"
        f"{bold_italic_serif('Reply with your')} ⭐ {bold_italic_serif('Star Amount, bro / sis!')}\n"
        f"<i>အနည်းဆုံး {SLOT_MIN_BET}⭐, အများဆုံး {SLOT_MAX_BET:,}⭐</i>"
    )
    await event.edit(text, parse_mode='html', buttons=[[Button.inline("🔙 Back", data="nav_back_home")]])
    await event.answer()

    # 🩹 Pre-seeds the edit-in-place session to point at THIS message, so when the reply below
    # comes in and _casino_play eventually calls _casino_edit_or_send, it finds this exact
    # message and edits it with the result — same message the whole way through, menu → prompt
    # → result, instead of a new one at each step.
    msg = await event.get_message()
    chat_id = event.chat_id
    via = "bot3" if (chat_id == FORCE_SUB_CHAT_ID and bot3 is not None) else "bot1"
    # 🩹 "plays": 0 — this pre-seed itself doesn't count as a play; the counter starts moving
    # once _casino_edit_or_send below is actually asked to show a real result (see
    # CASINO_SESSION_MAX_PLAYS).
    active_dice_sessions[(owner_user_id, chat_id)] = {"msg_id": msg.id, "via": via, "ts": time.time(), "plays": 0}
    pending_casino_bet[owner_user_id] = {
        "chat_id": chat_id, "prompt_msg_id": msg.id, "game_key": game_key,
        "expiry": time.time() + CASINO_BET_PROMPT_TTL,
    }

@bot1.on(events.NewMessage(incoming=True))
async def casino_bet_reply_handler(event):
    user_id = event.sender_id
    state = pending_casino_bet.get(user_id)
    if not state:
        return
    if not event.is_reply or event.chat_id != state["chat_id"] or event.reply_to_msg_id != state["prompt_msg_id"]:
        return
    if is_duplicate_event(event): return
    if time.time() > state["expiry"]:
        pending_casino_bet.pop(user_id, None)
        return await event.reply("⏳ အချိန်ကုန်သွားပါပြီ — /casino ကို ထပ်ရိုက်ပါ။", parse_mode='html')

    raw = (event.raw_text or "").strip()
    try:
        bet = round(float(raw), 2)
    except ValueError:
        return await event.reply("❌ ဂဏန်းအမှန်ဖြင့် ဒီစာကို Reply ပြန်ပါ — ဥပမာ <code>500</code>", parse_mode='html')

    game_key = state["game_key"]
    pending_casino_bet.pop(user_id, None)
    if game_key == "cardgame":
        return await _start_cardgame_lobby(event, bet)
    mention = await get_html_mention(event, user_id)
    await _casino_play(event, user_id, bet, mention, game_key)

# 🩹 FIX (2026-08, per owner question): this used to be registered on bot3 ("@bot3.on(...)",
# same as every other bot3 casino game before it) — but bot3 "Lives in FORCE_SUB_CHAT_ID only"
# (see bot3's own definition comment above). A command registered directly on bot3 can only
# ever be received in that ONE group bot3 is actually a member of — everywhere else, bot3 never
# sees the message at all, so these commands would have silently done nothing. Registered on
# bot1 now, which is added everywhere — matches the architecture the CASINO GAMES section
# comment already documented ("bot1 still has to be the one that RECEIVES and processes the
# command") but that the actual game handlers never followed. _casino_out() above still routes
# the OUTGOING messages through bot3 specifically inside the force-join room, for the same
# load-shedding reason _out() does — just the command reception itself is fixed.
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]slot(?:@\w+)?(?:\s+(\S+))?$', 'bot1')))
async def slot_cmd_bot1(event):
    await _game_cmd_handler(event, "slot")

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.](?:basketball|bball)(?:@\w+)?(?:\s+(\S+))?$', 'bot1')))
async def basketball_cmd_bot1(event):
    await _game_cmd_handler(event, "bball")

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]dice(?:@\w+)?(?:\s+(\S+))?$', 'bot1')))
async def dice_cmd_bot1(event):
    await _game_cmd_handler(event, "dice")

# 🩹 Dual-registered on bot1 AND bot3 (same reasoning as unified_callback_handler above): the
# message these buttons sit on may have been sent via EITHER bot, depending on whether
# _casino_out() routed through bot3 (force-join room) or fell back to bot1 — and Telegram only
# ever delivers a callback tap to whichever bot account actually owns that message.
async def casino_go_cb(event):
    game_key = event.pattern_match.group(1)
    if isinstance(game_key, bytes):
        game_key = game_key.decode('utf-8')
    user_id = int(event.pattern_match.group(2))
    bet = round(float(event.pattern_match.group(3)), 2)
    if event.sender_id != user_id:
        return await event.answer("⚠️ ဒါ မင်းရဲ့ ဂိမ်း မဟုတ်ဘူး!", alert=True)
    if not claim_single_tap(event):
        return await event.answer("⏳ ခဏစောင့်", alert=False)
    await event.answer(f"{GAMES[game_key]['emoticon']} ကစားနေပါပြီ...")
    mention = await get_html_mention(event, user_id)
    await _casino_play(event, user_id, bet, mention, game_key)

bot1.on(events.CallbackQuery(pattern=r'^cg_go_(slot|bball|dice)_(\d+)_([\d.]+)$'))(casino_go_cb)
if bot3 is not None:
    bot3.on(events.CallbackQuery(pattern=r'^cg_go_(slot|bball|dice)_(\d+)_([\d.]+)$'))(casino_go_cb)

# ==========================================
# 🃏 /cardgame — NEW (2026-09, per owner request): PVP, group-only, 2+ players. One host sets
# the bet, anyone in the group can Join for that same amount, the host Starts once enough
# people are in, and the highest of a UNIQUE set of 1-10 cards wins the whole pot minus the
# owner's cut. Deliberately its OWN standalone flow rather than a 4th entry in GAMES/
# _casino_play above — those are all "one player vs the house, resolved instantly"; this is
# "N players vs each other, resolved once the host says so", which needs an actual multi-step
# lobby (join roster, a Start gate, real per-player escrow) that the single-player casino
# engine was never built to hold.
#
# 🎴 CARD DEALING — why 1-10, always UNIQUE, no ties possible: _cardgame_deal() draws N
# DISTINCT values out of 1-10 (random.sample, no replacement) instead of N independent
# 1-10 rolls. With independent rolls, two players landing on the same number would need an
# explicit tie-break rule that was never specified — drawing without replacement removes the
# question entirely: with only 10 distinct values in existence, no two players can ever hold
# the same one, so "the highest card wins" always has exactly one answer. This is also the
# reason CARDGAME_MAX_PLAYERS is capped at 10 — an 11th distinct value doesn't exist.
#
# 💰 PAYOUT MATH — owner's example was 5 players × 1,000⭐: winner gets 4,000⭐, owner gets
# 1,000⭐. Generalized: pot = bet × N; owner's cut is a flat ONE bet's worth (which, since
# every player stakes the same amount, is exactly pot ÷ N); the winner gets everything else
# (pot − bet = bet × (N−1)). Heads-up on the N=2 edge case this generalization implies: with
# only the minimum 2 players, the winner's payout (1 × bet) exactly equals their OWN stake
# back — they don't net any actual profit, only the loser's bet moves (straight to the
# owner). That's a direct, unavoidable consequence of "owner's cut is always worth one full
# bet" holding at every N — flag it here in case a minimum-2 game feeling like a non-event for
# the winner isn't the intended feel; raising CARDGAME_MIN_PLAYERS to 3 would avoid it
# entirely (3-player winner nets +1 bet instead of breaking even).
#
# 🔒 ESCROW — a player's bet is taken (try_deduct_bet_bot3, same AML-jail/bust path every
# other casino game uses) the MOMENT they tap Join, not deferred to Start. That money is real
# and gone from their balance while the lobby sits open, which is exactly why this lobby needs
# its own timeout+refund watchdog (_cardgame_lobby_timeout_watchdog) — unlike a single /slot
# spin that resolves in one function call, a lobby can sit open for minutes with real Star
# already collected from real players, and if the host simply never starts it, that money
# needs a way back.
# ==========================================
CARDGAME_MIN_BET = 1            # mirrors SLOT_MIN_BET's floor — independent constant, tune freely
CARDGAME_MAX_BET = 50_000       # mirrors SLOT_MAX_BET's ceiling (also keeps every single bet safely under AML_BUST_THRESHOLD)
CARDGAME_MIN_PLAYERS = 2        # owner spec: "2 or more" — see the N=2 payout note above
CARDGAME_MAX_PLAYERS = 10       # hard ceiling — cards are UNIQUE values 1-10, see _cardgame_deal
CARDGAME_LOBBY_TTL = 300        # 5 min to fill before auto-cancel + full refund (safety net for the real escrowed Star — see ESCROW note above)

CARDGAME_INFO_TEXT = (
    "🃏 Host က ဘောင်းကြေးသတ်မှတ်သည်။ Join နှိပ်ပြီး ဝင်ပါ (2-10ဦး)။ "
    "လူစုံရင် Host က Start နှိပ်ပါ။ ကတ် 1-10 ထဲက အကြီးဆုံးရသူ အနိုင်။ "
    "Pot ထဲက Owner ဝေစု (1 ယောက်စာ) နုတ်ပြီး ကျန်တာ အားလုံး အနိုင်ရသူ ရမည်။"
)  # ⚠️ Telegram caps callback-alert text at 200 chars — this is 197; re-count before editing.

def _cardgame_deal(n):
    """n UNIQUE card values out of 1-10, no replacement — see the big comment above for why
    this (not n independent rolls) is what makes ties structurally impossible."""
    return random.sample(range(1, 11), n)

def _cardgame_lobby_text(lobby):
    """Renders the current lobby card — host, bet, live roster, and a running pot/payout
    preview — so everyone can see exactly what's on the table before the host starts. Called
    fresh on every join and re-edited into the SAME message (see cardgame_join_cb), never sent
    as a new one."""
    n = len(lobby["players"])
    bet = lobby["bet"]
    pot = bet * n
    text = (
        f"🃏 {bold_italic_serif('CARD GAME')} — {bold_italic_serif('Highest Card Wins!')}\n\n"
        f"👑 <b>Host:</b> {lobby['host_mention']}\n"
        f"💵 <b>လောင်းကြေး (တစ်ယောက်ချင်း):</b> <code>{format_star_plain(bet)}</code>\n\n"
    )
    if n == 0:
        text += "<i>🙋 Join ကိုနှိပ်ပြီး ဒီစားပွဲထဲ ဝင်ထိုင်လိုက်ပါ...</i>"
    else:
        roster = "\n".join(f"  {i + 1}. {m}" for i, m in enumerate(lobby["players"].values()))
        text += (
            f"👥 <b>ကစားသူများ ({n}/{CARDGAME_MAX_PLAYERS}):</b>\n{roster}\n\n"
            f"💰 <b>Pot စုစုပေါင်း:</b> <code>{format_star_plain(pot)}</code>\n"
            f"🏆 <b>အနိုင်ရရင် ရမည့်ငွေ:</b> <code>{format_star_plain(max(pot - bet, 0))}</code>"
        )
    text += (
        f"\n\n⏳ <i>အနည်းဆုံး {CARDGAME_MIN_PLAYERS} ယောက်ဝင်မှ Host Start နှိပ်နိုင်ပါမယ်။</i>"
        if n < CARDGAME_MIN_PLAYERS else
        f"\n\n✅ <i>Host အသင့်ရင် Start နှိပ်လိုက်ပါ — ဒါမှမဟုတ် နောက်ထပ်ကစားသူများ စောင့်ပါ။</i>"
    )
    return text

def _cardgame_lobby_buttons():
    return [
        [colored_button("🙋 Join ဝင်ကစားမယ်", data="cardgame_join", color="success"),
         colored_button("▶️ လူစုံရင် Start", data="cardgame_start", color="primary")],
        [Button.inline("ℹ️ Cardgame အကြောင်း", data="cardgame_info")],
    ]

async def _cardgame_lobby_timeout_watchdog(chat_id, deadline):
    """If a lobby doesn't get Started within CARDGAME_LOBBY_TTL, refunds every already-joined
    player's escrowed bet and tears the lobby down — otherwise real Star a player already paid
    in would sit stuck forever if the host just never comes back. Same "one-off timer keyed to
    THIS instance, not a shared periodic sweep" pattern as _squad_setup_timeout_watchdog."""
    wait_for = deadline - time.time()
    if wait_for > 0:
        await asyncio.sleep(wait_for)
    lobby = active_cardgame_lobbies.get(chat_id)
    if not lobby or lobby.get("deadline") != deadline:
        return  # already started, or superseded by a newer lobby in this chat
    active_cardgame_lobbies.pop(chat_id, None)
    for user_id in lobby["players"]:
        try:
            await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"star_balance": lobby["bet"]}})
            await bot3_treasury_adjust(star=-lobby["bet"])
        except Exception as e:
            logging.error(f"❌ Cardgame lobby refund failed for {user_id} in chat {chat_id}: {e}")
    notice = (
        f"⏳ <b>Card Game သက်တမ်းကုန်သွားပါပြီ</b> — {CARDGAME_LOBBY_TTL // 60} မိနစ်အတွင်း Host "
        f"Start မပေးလို့ ပယ်ဖျက်ပြီး ဝင်ထားသူအားလုံးကို ငွေအပြည့်ပြန်အမ်းပေးလိုက်ပါပြီ။ "
        f"<code>/cardgame [ပမာဏ]</code> နဲ့ ထပ်စလို့ရပါတယ်။"
    )
    try:
        client = bot3 if (lobby["via"] == "bot3" and bot3 is not None) else bot1
        await client.edit_message(chat_id, lobby["msg_id"], notice, parse_mode='html', buttons=None)
    except Exception as e:
        logging.error(f"❌ Cardgame lobby expiry edit failed in chat {chat_id}: {e}")

async def _start_cardgame_lobby(event, bet):
    """Shared lobby-creation logic — validates and opens a lobby with `event.sender_id` as
    host. Used by BOTH entry points: the direct `/cardgame [amount]` command below, AND the
    `/casino` menu's 🃏 button, which reuses the same "reply with your bet" flow as slot/
    basketball/dice (see casino_menu_pick_cb / casino_bet_reply_handler) rather than having its
    own separate UI for what's functionally the same "pick an amount" step. Returns nothing —
    on success the lobby message IS the response; on failure it replies with why."""
    if event.is_private:
        return await event.reply(
            f"🃏 Card Game ကို Group ထဲမှာပဲ ကစားလို့ရပါတယ် — အနည်းဆုံး {CARDGAME_MIN_PLAYERS} ယောက် လိုအပ်လို့ပါ။",
            parse_mode='html'
        )
    chat_id = event.chat_id
    if chat_id in active_cardgame_lobbies:
        return await event.reply(
            "⚠️ ဒီ Group မှာ Card Game လောင်းကြေး တစ်ခု ဖွင့်ထားပြီးသားပါ — အဲဒါပြီးမှ (သို့) သက်တမ်းကုန်မှ ထပ်ဖွင့်လို့ရပါမယ်။",
            parse_mode='html'
        )
    if bet < CARDGAME_MIN_BET or bet > CARDGAME_MAX_BET:
        return await event.reply(
            f"❌ လောင်းကြေးက {CARDGAME_MIN_BET}⭐ နဲ့ {CARDGAME_MAX_BET:,}⭐ ကြားထဲမှာ ဖြစ်ရပါမယ်။", parse_mode='html'
        )

    host_id = event.sender_id
    host_mention = await get_html_mention(event, host_id)
    lobby = {
        "host_id": host_id, "host_mention": host_mention, "bet": bet,
        "players": {}, "chat_id": chat_id, "msg_id": None,
        "via": "bot3" if (chat_id == FORCE_SUB_CHAT_ID and bot3 is not None) else "bot1",
        "deadline": time.time() + CARDGAME_LOBBY_TTL,
    }
    active_cardgame_lobbies[chat_id] = lobby
    sent = await _casino_out(event, message=_cardgame_lobby_text(lobby), parse_mode='html', buttons=_cardgame_lobby_buttons())
    lobby["msg_id"] = sent.id
    asyncio.create_task(_cardgame_lobby_timeout_watchdog(chat_id, lobby["deadline"]))

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]cardgame(?:@\w+)?(?:\s+(\S+))?$', 'bot1')))
async def cardgame_cmd_bot1(event):
    if is_duplicate_event(event): return
    bet_str = event.pattern_match.group(1)
    if not bet_str:
        return await event.reply(
            f"🃏 <b>အသုံးပြုပုံ:</b> <code>/cardgame [ပမာဏ]</code>\n"
            f"<i>ဥပမာ — /cardgame 1000</i>\n"
            f"<i>အနည်းဆုံး {CARDGAME_MIN_BET}⭐, အများဆုံး {CARDGAME_MAX_BET:,}⭐ · ကစားသူ {CARDGAME_MIN_PLAYERS}-{CARDGAME_MAX_PLAYERS} ဦး</i>",
            parse_mode='html'
        )
    try:
        bet = round(float(bet_str), 2)
    except ValueError:
        return await event.reply(f"❌ ဂဏန်းအမှန်ထည့်ပါ — ဥပမာ <code>/cardgame 1000</code>", parse_mode='html')
    await _start_cardgame_lobby(event, bet)

async def cardgame_join_cb(event):
    chat_id = event.chat_id
    lobby = active_cardgame_lobbies.get(chat_id)
    if not lobby:
        return await event.answer("⏳ ဒီ Card Game ပြီးသွားပြီ (သို့) သက်တမ်းကုန်သွားပါပြီ။", alert=True)
    if not claim_single_tap(event):
        return await event.answer("⏳ ခဏစောင့်", alert=False)

    user_id = event.sender_id
    if user_id in lobby["players"]:
        return await event.answer("✅ မင်း ဒီဂိမ်းထဲ ဝင်ပြီးသားပါ။", alert=False)
    if len(lobby["players"]) >= CARDGAME_MAX_PLAYERS:
        return await event.answer(f"🚫 ကစားသူ အများဆုံး {CARDGAME_MAX_PLAYERS} ယောက် ပြည့်သွားပါပြီ။", alert=True)

    # 🩹 check_aml_jail is checked here FIRST, on purpose, even though try_deduct_bet_bot3
    # (below) already runs it internally — if this user IS jailed, check_aml_jail sends its own
    # explanatory "under investigation" message straight into the chat and returns True; without
    # this early check we'd fall through to a generic "Balance မလုံလောက်ပါ" alert instead, which
    # would be flatly wrong (and confusing) as the reason. try_deduct_bet_bot3 still re-runs the
    # same check a moment later — harmless, it's just a dict lookup — but only ITS non-jail
    # failure path (genuinely insufficient balance) reaches the generic alert below now.
    if await check_aml_jail(event, user_id):
        return await event.answer()
    if not await try_deduct_bet_bot3(event, user_id, lobby["bet"]):
        return await event.answer(f"❌ Balance မလုံလောက်ပါ — {format_star_plain(lobby['bet'])} လိုအပ်ပါတယ်။", alert=True)
    await bot3_treasury_adjust(star=lobby["bet"])

    # 🛡️ Re-check capacity AFTER the await above — a burst of near-simultaneous taps could in
    # theory have filled the last slot while this particular join was mid-flight (the deduction
    # call yields control back to the event loop). _cardgame_deal() hard-requires <=10 players,
    # so this is defended against directly instead of assumed away as "too unlikely to matter" —
    # refund immediately rather than silently overfill the roster.
    if len(lobby["players"]) >= CARDGAME_MAX_PLAYERS:
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"star_balance": lobby["bet"]}})
        await bot3_treasury_adjust(star=-lobby["bet"])
        return await event.answer(f"🚫 ကစားသူ အများဆုံး {CARDGAME_MAX_PLAYERS} ယောက် ပြည့်သွားလို့ ငွေပြန်အမ်းလိုက်ပါပြီ။", alert=True)

    lobby["players"][user_id] = await get_html_mention(event, user_id)
    await event.edit(_cardgame_lobby_text(lobby), parse_mode='html', buttons=_cardgame_lobby_buttons())
    await event.answer("✅ ဝင်ရောက်ပြီးပါပြီ — Good luck! 🍀")

async def cardgame_start_cb(event):
    chat_id = event.chat_id
    lobby = active_cardgame_lobbies.get(chat_id)
    if not lobby:
        return await event.answer("⏳ ဒီ Card Game ပြီးသွားပြီ (သို့) သက်တမ်းကုန်သွားပါပြီ။", alert=True)
    if event.sender_id != lobby["host_id"]:
        return await event.answer("⚠️ Host ကပဲ ဒီဂိမ်းကို Start ပေးလို့ရပါတယ်။", alert=True)
    if not claim_single_tap(event):
        return await event.answer("⏳ ခဏစောင့်", alert=False)

    n = len(lobby["players"])
    if n < CARDGAME_MIN_PLAYERS:
        return await event.answer(f"🚫 အနည်းဆုံး ကစားသူ {CARDGAME_MIN_PLAYERS} ယောက် လိုအပ်ပါတယ် (လက်ရှိ {n} ယောက်)။", alert=True)

    # 🔒 Pop FIRST, before any awaiting payout work below — the lobby is now committed to
    # resolving, so nothing else (a late Join, a repeat Start tap slipping past claim_single_tap
    # on a retry, the TTL watchdog waking up) can touch active_cardgame_lobbies[chat_id] again.
    active_cardgame_lobbies.pop(chat_id, None)
    await event.answer("🃏 ဖွင့်နေပါပြီ...")

    player_ids = list(lobby["players"].keys())
    cards = _cardgame_deal(n)
    bet = lobby["bet"]
    pot = bet * n
    win_amount = pot - bet  # 🔒 = bet × (n-1) — see the big payout-math comment above the constants
    ranked = sorted(zip(player_ids, cards), key=lambda pc: -pc[1])
    winner_id = ranked[0][0]

    await users_catcher_col.update_one(
        {"user_id": winner_id},
        {"$inc": {"star_balance": win_amount, "cardgame_wins": 1}}
    )
    # 🎯 bot3_treasury_adjust's own owner-mirror (see its docstring) is what actually sends the
    # owner's cut — every player's bet already flowed IN as a treasury gain the moment they
    # joined (see cardgame_join_cb), so paying the winner OUT is the only adjustment left; the
    # owner's flat "1 bet's worth" cut is just whatever of that pot never gets paid back out.
    await bot3_treasury_adjust(star=-win_amount)
    newly_earned = await check_and_award_achievements(winner_id)

    board = "\n".join(
        f"  {'🏆' if uid == winner_id else '▫️'} {lobby['players'][uid]} — <code>{card}</code>"
        for uid, card in ranked
    )
    winner_mention = lobby['players'][winner_id]
    text = (
        f"🃏 {bold_italic_serif('CARD GAME')} — {bold_italic_serif('Result!')}\n\n"
        f"👑 <b>Host:</b> {lobby['host_mention']}\n"
        f"💵 <b>လောင်းကြေး:</b> <code>{format_star_plain(bet)}</code> (တစ်ယောက်ချင်း)\n\n"
        f"🎴 <b>ကတ်များ:</b>\n{board}\n\n"
        f"🏆 <b>အနိုင်ရသူ:</b> {winner_mention}\n"
        f"💰 <b>ရငွေ:</b> <code>{format_star_plain(win_amount)}</code> "
        f"<i>(Pot {format_star_plain(pot)} ထဲက Owner ဝေစု {format_star_plain(bet)} နုတ်ပြီး)</i>"
        + format_achievement_unlocks(newly_earned)
    )
    try:
        await event.edit(text, parse_mode='html', buttons=None)
    except Exception as e:
        logging.error(f"❌ Cardgame result edit failed in chat {chat_id}: {e}")
        await _casino_out(event, message=text, parse_mode='html')

async def cardgame_info_cb(event):
    await event.answer(CARDGAME_INFO_TEXT, alert=True)

bot1.on(events.CallbackQuery(pattern=r'^cardgame_join$'))(cardgame_join_cb)
bot1.on(events.CallbackQuery(pattern=r'^cardgame_start$'))(cardgame_start_cb)
bot1.on(events.CallbackQuery(pattern=r'^cardgame_info$'))(cardgame_info_cb)
if bot3 is not None:
    bot3.on(events.CallbackQuery(pattern=r'^cardgame_join$'))(cardgame_join_cb)
    bot3.on(events.CallbackQuery(pattern=r'^cardgame_start$'))(cardgame_start_cb)
    bot3.on(events.CallbackQuery(pattern=r'^cardgame_info$'))(cardgame_info_cb)

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]addslotbanner(?:@\w+)?$', 'bot1')))
async def add_slot_banner(event):
    """Owner-only, mirrors /addbalance exactly (see add_balance_banner): reply to a photo/video
    with this, and that media becomes the banner shown on the slot/basketball/dice menus and
    every result card."""
    if event.sender_id != OWNER_ID: return
    if is_duplicate_event(event): return
    if not event.is_reply:
        return await event.reply(
            "❌ <b>Reply to a photo or video with /addslotbanner</b> — that media becomes the "
            "banner shown on the slot/basketball/dice menus and every result card.",
            parse_mode='html'
        )
    reply_msg = await event.get_reply_message()
    if not reply_msg or not (reply_msg.photo or reply_msg.video or reply_msg.document):
        return await event.reply("❌ <b>Valid media not found</b>", parse_mode='html')
    try:
        forwarded_msg = await send_safe_message(bot1, SPECIFIC_CONTROL_GROUP, "", file=reply_msg.media)
        await bot_settings_col.update_one(
            {"_id": "slot_banner"},
            {"$set": {"storage_msg_id": forwarded_msg.id, "set_by": event.sender_id, "set_at": time.time()}},
            upsert=True
        )
        await event.reply("✅ <b>Casino banner updated!</b> Slot, basketball, and dice will all show it from now on.", parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ <b>Failed to save banner:</b> <code>{escape_html(str(e))}</code>", parse_mode='html')


@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]removeslotbanner(?:@\w+)?$', 'bot1')))
async def remove_slot_banner(event):
    if event.sender_id != OWNER_ID: return
    if is_duplicate_event(event): return
    await bot_settings_col.update_one({"_id": "slot_banner"}, {"$unset": {"storage_msg_id": ""}}, upsert=True)
    await event.reply("🗑️ <b>Casino banner removed.</b> Slot, basketball, and dice will show as plain text again.", parse_mode='html')

# =========================================================
# 🎰 ROULETTE / CARDGAME / FLIP / DICE / HI-LO — REMOVED (2026-08, per owner request: every
# casino game except /slot is gone. /slot below is the only one left, rebuilt to use
# Telegram's own native 🎰 dice-message animation instead of hand-edited frames.)
# =========================================================

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]daily(?:@\w+)?$', 'bot1')))
async def daily_bounty_handler(event):
    user_id = event.sender_id
    mention = await get_html_mention(event, user_id)
    plain_name = await get_plain_name(event, user_id)
    await ensure_user_registered(user_id, plain_name)
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    now = time.time()
    last_daily = user_doc.get("last_daily", 0)
    streak = user_doc.get("daily_streak", 0)
    if now - last_daily < 86400:
        rem_time = int(86400 - (now - last_daily))
        return await event.reply(f"⏳ {mention} <b>You already claimed today!</b> Next claim in {str(timedelta(seconds=rem_time))}.", parse_mode='html')
    if now - last_daily > 172800: streak = 0
    new_streak = streak + 1
    # 🩹 2026-08 USD RETIREMENT: this used to pay out in USD (old-MMK-worth / MMK_PER_USD).
    # Re-expressed directly in Star as <old MMK worth> / 1,000,000 — same conversion used for
    # _RARITY_STAR_BONUS_MAP above (1,000,000 old-MMK was 1 Star's own old fixed rate before
    # the USD migration), which keeps this bonus proportionally exactly as generous as it
    # always was, just paid in Star instead of USD.
    base_bonus = round(random.randint(20000000, 500000000) / 1_000_000, 2)
    streak_bonus = round(min(new_streak * 20000000, 300000000) / 1_000_000, 2)
    bonus = base_bonus + streak_bonus
    await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"star_balance": bonus}, "$set": {"last_daily": now, "daily_streak": new_streak, "fullname": plain_name}})
    await settle_star_debt(user_id)
    newly_earned = await check_and_award_achievements(user_id)
    reply_text = f"🎁 {mention} <b>Daily bonus claimed! <code>+{bonus} ⭐</code></b>\n🔥 <b>{new_streak} day streak</b> — Streak Bonus: <code>+{streak_bonus} ⭐</code>"

    # 💍 MARRIAGE PERK (owner request, 2026-08): claiming /daily also sends a small, free ⭐
    # bonus straight to your spouse's balance — no action needed from them. spouse_id is read
    # from user_doc (fetched before this claim); /daily itself never changes it, so it's still
    # accurate. See MARRIAGE_DAILY_BONUS near the Marriage System section for the amount.
    spouse_id = user_doc.get("spouse_id")
    if spouse_id:
        await users_catcher_col.update_one({"user_id": spouse_id}, {"$inc": {"star_balance": MARRIAGE_DAILY_BONUS}})
        spouse_mention = await get_html_mention(event, spouse_id)
        reply_text += f"\n💌 {spouse_mention} also got <code>+{MARRIAGE_DAILY_BONUS} ⭐</code> from your love!"

    reply_text += format_achievement_unlocks(newly_earned)
    await event.reply(reply_text, parse_mode='html')

# ==========================================
# ==========================================
# 💰 RICHEST LEADERBOARD (TOP 10 ONLY — NO PAGINATION)
# ==========================================
# 🩹 CHANGED (per owner request): this used to paginate through EVERY single player with a
# positive star_balance (5 per page) — with ~1,586 active players that's well over 300
# pages for what's supposed to be a leaderboard. Now it just shows the top 10, full stop,
# same as /top and /gtop.
RICHEST_TOP_N = 10

async def render_richest_page():
    """Render the top RICHEST_TOP_N richest players. No pagination."""
    pipeline = [
        {"$match": {"star_balance": {"$gt": 0}}},
        {"$project": {
            "user_id": 1,
            "fullname": 1,
            "star_balance": 1,
            "vlt_balance": 1,
            # 🩹 FIX: plain {"$size": "$harem"} throws and aborts the WHOLE aggregation the
            # moment it hits any user doc that's missing "harem" entirely (legacy accounts
            # created before that field existed, or via a path that skipped ensure_user_
            # registered's $setOnInsert default) — which silently broke /richest for
            # everyone, not just that one user. $ifNull falls back to an empty array first.
            "cards": {"$size": {"$ifNull": ["$harem", []]}}
        }},
        {"$sort": {"star_balance": -1}},
        {"$limit": RICHEST_TOP_N}
    ]
    users = await users_catcher_col.aggregate(pipeline).to_list(length=RICHEST_TOP_N)

    if not users:
        return "🏆 <b>No wealthy players yet!</b>"

    # Header
    text = f"💰 <b>TOP {len(users)} RICHEST PLAYERS</b>\n\n"

    # Build each user row
    for idx, u in enumerate(users, start=1):
        # Safe conversion for balances (fix string issues)
        try:
            star = float(u.get("star_balance", 0))
        except (ValueError, TypeError):
            star = 0.0

        try:
            vlt = float(u.get("vlt_balance", 0))
        except (ValueError, TypeError):
            vlt = 0.0

        cards = u.get("cards", 0)

        # User mention
        name = clean_display_name(u.get('fullname'), fallback=f"User {u['user_id']}")
        mention = f"<a href='tg://user?id={u['user_id']}'>{escape_html(name)}</a>"

        # Format: '1' Name
        # ⭐ 500 | 💠 20 | 🎴 50
        # ----------
        text += f"<b>'{idx}'</b> {mention}\n"
        text += f"⭐ {format_star_plain(star)} | 💠 {format_vlt_plain(vlt)} | 🎴 {cards:,}\n"
        text += f"----------\n"

    return text


@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]richest(?:@\w+)?$', 'bot1')))
async def richest_paginated_handler(event):
    """Handle /richest command — shows the top 10 richest players, no pagination."""
    try:
        text = await render_richest_page()
    except Exception as e:
        print(f"❌ /richest error: {e}")
        return await event.reply("⚠️ <b>Richest leaderboard ကို ခေတ္တ ဖော်ပြလို့မရသေးပါ — ထပ်ကြိုးစားကြည့်ပါ။</b>", parse_mode='html')
    await event.reply(text, parse_mode='html')

# =========================================================
# 🎰 GAMBLE / MINES / PLINKO / BLACKJACK / CRASH / RPS / WHEEL / BACCARAT / COLOR-TOWER /
# LUCKY-PICK — REMOVED (2026-08, per owner request: every casino game except /slot is gone).
# =========================================================

# ---- STAR / VLT စာရင်းအင်း (.stars) ----
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]stars(?:@\w+)?$', 'bot1')))
async def stars_info_handler(event):
    user_id = event.sender_id
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    star_balance = user_doc.get("star_balance", 0) if user_doc else 0
    vlt_balance = user_doc.get("vlt_balance", 0) if user_doc else 0

    # ယနေ့ သုံးစွဲမှုအချက်အလက် (Owner Shop မှာ VLT ဘယ်လောက်သုံးခဲ့လဲ)
    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    star_spent_today = 0
    if user_doc and user_doc.get("star_activity_date") == today_str:
        star_spent_today = user_doc.get("star_spent_today", 0)

    # လက်ရှိ ⭐⇄💠 ငွေလဲနှုန်း
    market = await get_or_create_vlt_market()
    rate = market.get("rate", VLT_STARTING_RATE)

    mention = await get_html_mention(event, user_id)
    text = f"⭐ Star / 💠 VLT အခြေအနေ\nကစားသမား - {mention}\n\nStar လက်ကျန် - {format_star_plain(star_balance)}\nVLT လက်ကျန် - {format_vlt_plain(vlt_balance)}\n"
    text += f"ယနေ့ Shop သုံးစွဲပြီးသား - {format_vlt_plain(star_spent_today)}\n"
    text += f"လက်ရှိ ငွေလဲနှုန်း - 1💠 = {rate:,.2f}⭐\n\n"
    text += f"ကဒ်ဝယ်ရန်၊ Premium ဝယ်ရန်၊ ငွေလဲရန် အောက်က ခလုတ်များကို နှိပ်ပါ။"

    buttons = [
        [
            Button.inline("💠 VLT ဝယ်မယ်", data=f"buyhub_star_{user_id}"),
            Button.inline("🛍️ ကဒ်များဝယ်မယ်", data=f"buyhub_cards_{user_id}")
        ],
        [
            Button.inline("👑 Premium ဝယ်မယ်", data=f"buyhub_premium_{user_id}"),
            Button.inline("💰 လက်ကျန်ငွေကြည့်မယ်", data=f"buyhub_balance_{user_id}")
        ]
    ]
    
    await event.reply(text, parse_mode='html', buttons=buttons)

# ---- လက်ကျန်ငွေပြခလုတ် (balance from .stars) ----
@bot1.on(events.CallbackQuery(pattern=r'^buyhub_balance_(\d+)$'))
async def buyhub_balance_callback(event):
    user_id = int(event.pattern_match.group(1))
    if event.sender_id != user_id:
        return await event.answer("ဒါက သင့်ရဲ့ menu မဟုတ်ပါ။", alert=True)
    
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    star_balance = user_doc.get("star_balance", 0) if user_doc else 0
    vlt_balance = user_doc.get("vlt_balance", 0) if user_doc else 0
    mention = await get_html_mention(event, user_id)
    
    text = f"လက်ကျန်ငွေစာရင်း\nကစားသမား - {mention}\n\n⭐ Star - {format_star_plain(star_balance)}\n💠 VLT - {format_vlt_plain(vlt_balance)}"
    buttons = [[Button.inline("🔙 နောက်သို့", data=f"stars_back_{user_id}")]]
    await event.edit(text, parse_mode='html', buttons=buttons)
    await event.answer()

# ---- .stars မှ နောက်သို့ပြန်ခလုတ် ----
@bot1.on(events.CallbackQuery(pattern=r'^stars_back_(\d+)$'))
async def stars_back_callback(event):
    user_id = int(event.pattern_match.group(1))
    if event.sender_id != user_id:
        return await event.answer("ဒါက သင့်ရဲ့ menu မဟုတ်ပါ။", alert=True)
    
    # အဓိက .stars စာမျက်နှာကို ပြန်ခေါ်မယ်
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    star_balance = user_doc.get("star_balance", 0) if user_doc else 0
    vlt_balance = user_doc.get("vlt_balance", 0) if user_doc else 0
    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    star_spent_today = 0
    if user_doc and user_doc.get("star_activity_date") == today_str:
        star_spent_today = user_doc.get("star_spent_today", 0)
    market = await get_or_create_vlt_market()
    rate = market.get("rate", VLT_STARTING_RATE)
    mention = await get_html_mention(event, user_id)
    
    text = f"⭐ Star / 💠 VLT အခြေအနေ\nကစားသမား - {mention}\n\nStar လက်ကျန် - {format_star_plain(star_balance)}\nVLT လက်ကျန် - {format_vlt_plain(vlt_balance)}\nယနေ့ Shop သုံးစွဲပြီးသား - {format_vlt_plain(star_spent_today)}\nလက်ရှိ ငွေလဲနှုန်း - 1💠 = {rate:,.2f}⭐\n\nကဒ်ဝယ်ရန်၊ Premium ဝယ်ရန်၊ ငွေလဲရန် အောက်က ခလုတ်များကို နှိပ်ပါ။"
    buttons = [
        [
            Button.inline("💠 VLT ဝယ်မယ်", data=f"buyhub_star_{user_id}"),
            Button.inline("🛍️ ကဒ်များဝယ်မယ်", data=f"buyhub_cards_{user_id}")
        ],
        [
            Button.inline("👑 Premium ဝယ်မယ်", data=f"buyhub_premium_{user_id}"),
            Button.inline("💰 လက်ကျန်ငွေကြည့်မယ်", data=f"buyhub_balance_{user_id}")
        ]
    ]
    await event.edit(text, parse_mode='html', buttons=buttons)
    await event.answer()
# =========================================================
# 🎰 LIMBO / OVER-UNDER / TOWER / BOX — REMOVED (2026-08, per owner request: every casino
# game except /slot is gone).
# =========================================================

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]hunt(?:@\w+)?$', 'bot1')))
async def text_adventure_hunt_handler(event):
    user_id = event.sender_id
    mention = await get_html_mention(event, user_id)
    plain_name = await get_plain_name(event, user_id)
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    current_time = time.time()
    last_hunt = user_doc.get("hunt_cooldown", 0) if user_doc else 0
    if current_time - last_hunt < 180:
        return await event.reply(f"⏳ {mention} <b>You must wait {int(180 - (current_time - last_hunt))}s.</b>", parse_mode='html')
    # 🩹 2026-08 USD RETIREMENT: was USD (old-MMK-worth / MMK_PER_USD). Re-expressed directly
    # in Star as <old MMK worth> / 1,000,000 — same conversion used for _RARITY_STAR_BONUS_MAP
    # and the daily bonus above, keeping this proportionally exactly as generous as it always was.
    earned = round(random.randint(200000, 8000000) / 1_000_000, 2)
    events_pool = [f"🌲 {mention} <b>found treasure! (+<code>{earned}⭐</code>)</b>", f"⚔️ {mention} <b>defeated a rival! (+<code>{earned}⭐</code>)</b>", f"🌌 {mention} <b>collected quantum points! (+<code>{earned}⭐</code>)</b>"]
    await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"star_balance": earned}, "$set": {"hunt_cooldown": current_time, "fullname": plain_name}}, upsert=True)
    await event.reply(random.choice(events_pool), parse_mode='html')

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]market(?:@\w+)?$', 'bot1')))
async def global_market_catalog_viewer(event):
    user_id = event.sender_id
    await ensure_user_registered(user_id, await get_plain_name(event, user_id))
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    star_balance = user_doc.get("star_balance", 0) if user_doc else 0
    vlt_balance = user_doc.get("vlt_balance", 0) if user_doc else 0
    premium_line = ""
    if is_premium_active(user_doc):
        expiry_str = datetime.fromtimestamp(user_doc["premium_until"], TZ).strftime("%Y-%m-%d")
        premium_line = f"\n👑 <b>Premium:</b> <code>{expiry_str}</code> အထိ"
    buttons = [
        [Button.inline("🌠 Owner Shop (ကဒ်အသစ်)", data=f"buyhub_cards_{user_id}")],
        [Button.inline("💠 ကဒ်ဝယ်ဖို့ VLT ဝယ်မယ်", data=f"buyhub_star_{user_id}")],
        [Button.inline("👑 Premium ဝယ်မယ်", data=f"buyhub_premium_{user_id}")],
    ]
    await event.reply(
        f"🏪 <b>MARKET</b>\n"
        f"<blockquote>"
        f"⭐ <b>Star:</b> <code>{format_star_plain(star_balance)}</code>\n"
        f"💠 <b>VLT:</b> <code>{format_vlt_plain(vlt_balance)}</code>"
        f"{premium_line}"
        f"</blockquote>\n"
        f"👇 <i>ဘာကိုဝယ်ချင်ပါသလဲ?</i>",
        parse_mode='html',
        buttons=buttons
    )

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]stats(?:@\w+)?$', 'bot1')))
async def system_inflation_stats(event):
    status_msg = await event.reply("⏳ <b>Compiling statistics...</b>", parse_mode='html')
    try:
        pipeline_cash = [
            {"$group": {
                "_id": None,
                "total_cash": {"$sum": "$star_balance"},
                "players_count": {"$sum": 1}
            }}
        ]
        econ_data = await users_catcher_col.aggregate(pipeline_cash).to_list(length=1)
        metrics = econ_data[0] if econ_data else {"total_cash": 0, "players_count": 0}
        all_base_chars = await characters_base_col.find({}, {"rarity": 1}).to_list(length=None)
        base_total = len(all_base_chars)
        base_tier_counts = {t: 0 for t in RARITY_TIERS}
        for c in all_base_chars:
            tier = classify_rarity(c.get("rarity", ""))
            if tier in base_tier_counts:
                base_tier_counts[tier] += 1
        all_users = await users_catcher_col.find({}, {"harem": 1}).to_list(length=None)
        catch_tier_counts = {t: 0 for t in RARITY_TIERS}
        other_catch_count = 0  # 🩹 FIX: harem items whose rarity string doesn't match any
        # known tier (e.g. legacy "Unknown" rarity from old trades/gifts) used to be silently
        # dropped from the per-tier breakdown while still counted in total_catches, so the 9
        # tier counts never summed to the total shown. Tracked separately here so the numbers
        # always reconcile, and logged so the offending raw rarity strings can be found.
        other_rarity_samples = set()
        total_catches = 0
        caught_unique_ids = set()
        for u in all_users:
            harem = u.get("harem", [])
            total_catches += len(harem)
            for item in harem:
                if isinstance(item, dict) and "char_id" in item:
                    caught_unique_ids.add(item["char_id"])
                    raw_rarity = item.get("rarity", "")
                    tier = classify_rarity(raw_rarity)
                    if tier in catch_tier_counts:
                        catch_tier_counts[tier] += 1
                    else:
                        other_catch_count += 1
                        if len(other_rarity_samples) < 20:
                            other_rarity_samples.add(repr(raw_rarity))
        if other_catch_count:
            print(f"⚠️ /stats: {other_catch_count} harem items had unclassifiable rarity strings: {sorted(other_rarity_samples)}")
        discovery_count = len(caught_unique_ids)
        msg = (
            f"📊 <b>GLOBAL ECONOMY & COLLECTION STATS</b>\n"
            f"⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n\n"
            f"👥 <b>Active Agents:</b> <code>{metrics['players_count']} Players</code>\n"
            f"🪙 <b>Total Money in Circulation:</b> <code>{metrics['total_cash']:,}⭐</code>\n\n"
            f"🗄️ <b>CHARACTER DATABASE</b> (<code>/addchar</code> total: <code>{base_total}</code>)\n"
        )
        for tier in RARITY_TIERS:
            cnt = base_tier_counts[tier]
            msg += f"{RARITY_EMOJI[tier]} <b>{tier}</b> — <code>{cnt}</code>\n"
            msg += f"<code>{build_progress_bar(cnt, base_total)}</code>\n"
        msg += f"\n🃏 <b>TOTAL CATCHES</b> (all players combined: <code>{total_catches}</code>)\n"
        for tier in RARITY_TIERS:
            cnt = catch_tier_counts[tier]
            msg += f"{RARITY_EMOJI[tier]} <b>{tier}</b> — <code>{cnt}</code>\n"
            msg += f"<code>{build_progress_bar(cnt, total_catches)}</code>\n"
        if other_catch_count:
            msg += f"❓ <b>OTHER/UNKNOWN</b> — <code>{other_catch_count}</code>\n"
            msg += f"<code>{build_progress_bar(other_catch_count, total_catches)}</code>\n"
        msg += f"\n🔎 <b>DISCOVERY RATE</b> (unique characters caught at least once)\n"
        msg += f"<code>{build_progress_bar(discovery_count, base_total)}</code>\n"
        msg += f"<code>{discovery_count}/{base_total}</code> characters discovered by players"
        await status_msg.edit(msg, parse_mode='html')
    except Exception as e:
        error_text = f"❌ <b>Stats Error:</b>\n<code>{escape_html(str(e))}</code>"
        await status_msg.edit(error_text, parse_mode='html')
        await report_system_error("system_inflation_stats", e)

# 🩹 FIX: /introduce used to build its whole reply as a single <pre style='...'> block.
# Two separate bugs made it fail completely (silently — no reply, no error visible to the
# user, looked "disappeared"):
#   1. Telegram's HTML parse mode does NOT support a `style` attribute on <pre> (or any
#      tag) — only a bare <pre> or <pre><code class="language-x">. Sending it raised a
#      "can't parse entities" error from Telegram, which the reply call never caught.
#   2. Even with that fixed, the text itself is ~4280 characters — over Telegram's hard
#      4096-char message limit — so it would still have failed to send on length alone.
# This splits the body across as many plain <pre> messages as needed, breaking only on
# line boundaries, so it always sends regardless of how long the list of commands grows.
def _chunk_text_by_lines(text, max_chunk=3900):
    lines = text.split('\n')
    chunks, current, current_len = [], [], 0
    for line in lines:
        added_len = len(line) + 1
        if current and current_len + added_len > max_chunk:
            chunks.append('\n'.join(current))
            current, current_len = [], 0
        current.append(line)
        current_len += added_len
    if current:
        chunks.append('\n'.join(current))
    return chunks

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]introduce(?:@\w+)?$', 'bot1')))
async def introduce_bot_handler(event):
    intro_body = (
        "ဟိုက်  –  YOUR ALL-IN-ONE TELEGRAM BOT\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "    I am a multi‑functional Telegram bot built to keep this group running smoothly.\n"
        "    I combine fun, economy, and powerful group management — all in one place.\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📌  WHAT I CAN DO FOR YOU (complete list)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "🛡️  MODERATION\n"
        "    • /info [Reply/@username]  –  user profile card (ID, badges, etc.)\n"
        "    • Auto‑spam detection for stickers & short messages (auto‑mutes repeat offenders)\n"
        "\n"
        "🎮  CATCHING GAME (CHARACTER COLLECTION)\n"
        "    • /who (/w, /waifu) –  reveal the spawned character\n"
        "    • /obtain [name]   –  capture the character and earn ⭐ Star\n"
        "    • /harem          –  view your vault (paginated inventory)\n"
        "    • /fav [ID]       –  set a favourite card\n"
        "    • /profile, /myinfo –  check your stats and balance\n"
        "    • /shop           –  buy Titles, Emblems & Frames for your profile\n"
        "    • /top /gtop      –  local and global leaderboards\n"
        "    • /check [ID]     –  detailed character info & top collectors\n"
        "\n"
        "🎰  CASINO & GAMBLING\n"
        "    • /slot [amount]       –  spin the slot machine (native Telegram animation, 777 jackpot pays 4x)\n"
        "    • /basketball [amount] –  shoot a hoop, 4 or 5 makes it\n"
        "    • /dice [amount]       –  roll a 6 to win\n"
        "    • /cardgame [amount]   –  PVP lobby, 2-10 players, highest unique 1-10 card wins\n"
        "\n"
        "💰  ECONOMY & TRADING\n"
        "    • /balance        –  view your ⭐ Star & 💠 VLT balance\n"
        "    • /daily           –  claim daily bonus (streak rewards)\n"
        "    • /hunt            –  go hunting for extra cash (3min cooldown)\n"
        "    • /gift [cardID] [note]   –  gift a card to someone, with an optional note (reply)\n"
        "    • /trade [myID] [theirID]  –  propose a card swap (reply)\n"
        "    • /sell [ID]       –  Owner offers to buy your card back with ⭐ Star (0.5x-1.5x its price)\n"
        "    • /buy [ID]        –  buy a fresh copy from the Owner Shop with ⭐ Star\n"
        "    • /buypremium      –  buy Bot Premium User status with ⭐ Star\n"
        "    • /market          –  Owner Shop, VLT & Premium purchase menu\n"
        "    • /richest         –  top 10 wealthiest players\n"
        "    • /stats           –  global economy overview\n"
        "\n"
        "🔗  SOCIAL & REFERRAL\n"
        "    • /referral        –  get your unique invite link\n"
        "                         (you get 4⭐, friend gets 2⭐)\n"
        "    • /propose         –  reply to someone to propose marriage 💍\n"
        "    • /divorce         –  end your current marriage (confirm required)\n"
        "    • /marriage        –  see your spouse, marriage rank & progress\n"
        "    • /topcouples      –  longest-married couples leaderboard\n"
        "\n"
        "🌦️  WEATHER\n"
        "    • /weather         –  live weather for Myanmar & Thailand\n"
        "                         (choose country, then city)\n"
        "\n"
        "📚  HELP & NAVIGATION\n"
        "    • /help            –  detailed command reference\n"
        "    • /game            –  casino games overview\n"
        "    • /introduce       –  you are reading this!\n"
        "\n"
        "⚙️  OWNER‑ONLY (hidden from normal users)\n"
        "    • /addchar, /delchar, /editchar, /setchar, /addartist,\n"
        "    • /addle CharID(s)  –  tag existing character(s) as Limited Edition (2-step reply\n"
        "                         wizard: name, then CatchLimit + daily VLT/copy reward)\n"
        "      /addle #N CharID(s)  –  add straight to existing Batch #N, skips the name step\n"
        "      /mergele #A #B      –  merge two LE batches into one (asks for the final name;\n"
        "                         never touches VLT/day rates, catch limits, or any balance)\n"
        "      /change CharID (reply to new photo/video)  –  swap a character's media\n"
        "                         (e.g. upgrade a blurry upload) without affecting anyone's catches,\n"
        "      /revokereward UserID [confirm]  –  claw back ONE account's force-sub join\n"
        "                         reward (e.g. it was granted by mistake to a bot account),\n"
        "      /gban UserID (or reply) –  confiscate their ENTIRE harem/VLT/Star (to you), then\n"
        "                         block them from catching everywhere (2-step: reason, duration —\n"
        "                         e.g. 7d/24h/2w/perm), /ungban UserID lifts it early,\n"
        "      /reclaimforcesub [confirm]  –  BULK claw back the join reward from every\n"
        "                         account that never caught anything beyond it (i.e. only\n"
        "                         joined to farm the free ⭐/card, never actually played),\n"
        "      /exportchars      –  send the full character database as a CSV file,\n"
        "      /changeallrarity confirm  –  rebrand all rarity names/emoji DB-wide,\n"
        "      /linkartist, /unlinkartist  –  link an /addartist name to a real Telegram\n"
        "                         account so the Guard Bot can pay them for collected cards,\n"
        "      /cr CharID no&lt;N&gt;  –  force-change ONE character's rarity tier,\n"
        "      /cr no&lt;N&gt; (as a reply)  –  bulk-change MANY CharIDs at once,\n"
        "      /fspawn, /haitime, /resetstats, /mau, /stealth, etc.\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡  Everything is designed to be fun, fair, and easy to follow.\n"
        "    For any help, type /help or ask me directly.\n"
        "\n"
        "📢  Join our community: https://t.me/Comeback_BoD\n"
        "Glad to have you here.\n"
    )
    chunks = _chunk_text_by_lines(intro_body)
    for i, chunk in enumerate(chunks):
        is_last = (i == len(chunks) - 1)
        html_chunk = f"<pre>{chunk}</pre>"
        if is_last:
            await event.reply(html_chunk, parse_mode='html', buttons=[[Button.inline("🏠 Open Menu", data="nav_back_home")]])
        else:
            await event.reply(html_chunk, parse_mode='html')

# ==========================================
# ⭐ STAR ကြဲပွဲ (STAR GIVEAWAY) — owner broadcasts an easy Burmese trivia question with 4
# inline-button choices to EVERY group at once. Answers are recorded SILENTLY for the full
# STAR_GIVEAWAY_DURATION window (no right/wrong reveal on click) — this is intentional: button
# taps count toward Telegram's Monthly Active Users metric, so the goal is to keep people
# tapping for the whole window rather than settling the instant someone answers. Only once the
# timer runs out does each group's message get edited to reveal the correct answer and (if
# anyone in that group got it right) the winner, who receives STAR_GIVEAWAY_REWARD Stars.
# ==========================================
STAR_GIVEAWAY_DURATION = 120  # seconds (2 minutes)
STAR_GIVEAWAY_REWARD = 1000   # ⭐ Stars for the first correct answer in each group
active_star_giveaways = {}    # giveaway_id -> {question, options, correct_idx, expiry, reward,
                              #  messages: {chat_id: msg_id}, attempts: {chat_id: [(user_id, option_idx, ts)]},
                              #  answered: {(chat_id, user_id)}, owner_chat}

# 50 easy Burmese trivia questions: (question, [4 options], correct_option_index)
STAR_GIVEAWAY_QUESTIONS = [
    ("မြန်မာနိုင်ငံရဲ့ မြို့တော်က ဘယ်မြို့လဲ?", ["ရန်ကုန်", "မန္တလေး", "နေပြည်တော်", "ပုဂံ"], 2),
    ("၂ + ၂ = ဘယ်နှစ်လဲ?", ["၃", "၄", "၅", "၆"], 1),
    ("မြန်မာနိုင်ငံရဲ့ အရှည်ဆုံးမြစ်က ဘာလဲ?", ["သံလွင်မြစ်", "ဧရာဝတီမြစ်", "ချင်းတွင်းမြစ်", "စစ်တောင်းမြစ်"], 1),
    ("တစ်နှစ်မှာ ဘယ်နှစ်လ ရှိလဲ?", ["၁၀လ", "၁၁လ", "၁၂လ", "၁၃လ"], 2),
    ("ရွှေတိဂုံစေတီတော် ဘယ်မြို့မှာ ရှိလဲ?", ["မန္တလေး", "ရန်ကုန်", "ပုဂံ", "တောင်ကြီး"], 1),
    ("အင်းလေးကန် ဘယ်ပြည်နယ်မှာ ရှိလဲ?", ["ကချင်ပြည်နယ်", "ရှမ်းပြည်နယ်", "ချင်းပြည်နယ်", "ကရင်ပြည်နယ်"], 1),
    ("မြန်မာနိုင်ငံ လွတ်လပ်ရေးရသည့်ခုနှစ်က ဘယ်နှစ်လဲ?", ["၁၉၄၅", "၁၉၄၇", "၁၉၄၈", "၁၉၅၀"], 2),
    ("သင်္ကြန်ပွဲတော်ကို များသောအားဖြင့် ဘယ်လမှာ ကျင်းပလဲ?", ["မတ်လ", "ဧပြီလ", "မေလ", "ဇွန်လ"], 1),
    ("ကမ္ဘာပေါ်မှာ အကြီးဆုံးတိုက်က ဘာလဲ?", ["အာဖရိကတိုက်", "အာရှတိုက်", "ဥရောပတိုက်", "အမေရိကတိုက်"], 1),
    ("ဂျပန်နိုင်ငံရဲ့ မြို့တော်က ဘာလဲ?", ["အိုဆာကာ", "တိုကျို", "ကျိုတို", "နာဂိုယာ"], 1),
    ("ထိုင်းနိုင်ငံရဲ့ မြို့တော်က ဘာလဲ?", ["ချင်းမိုင်", "ဘန်ကောက်", "ဖူးခက်", "ပတ္တရား"], 1),
    ("ကမ္ဘာပေါ်မှာ အမြင့်ဆုံးတောင်က ဘာလဲ?", ["K2", "ဧဗရက်စ်တောင်", "ကီလီမန်ဂျာရိုတောင်", "ဖူဂျီတောင်"], 1),
    ("ဧဖယ်လ်မျှော်စင် (Eiffel Tower) ဘယ်မြို့မှာ ရှိလဲ?", ["လန်ဒန်", "ပါရီ", "ရောမ", "ဗာလင်စီယာ"], 1),
    ("ကမ္ဘာပေါ်မှာ အကြီးဆုံး သမုဒ္ဒရာက ဘာလဲ?", ["အတ္တလန်တစ် သမုဒ္ဒရာ", "အိန္ဒိယ သမုဒ္ဒရာ", "ပစိဖိတ် သမုဒ္ဒရာ", "အာတိတ် သမုဒ္ဒရာ"], 2),
    ("၁၀ - ၄ = ဘယ်နှစ်လဲ?", ["၄", "၅", "၆", "၇"], 2),
    ("၅ x ၅ = ဘယ်နှစ်လဲ?", ["၂၀", "၂၅", "၃၀", "၃၅"], 1),
    ("၁၀၀ ÷ ၂၀ = ဘယ်နှစ်လဲ?", ["၄", "၅", "၆", "၇"], 1),
    ("၃ x ၃ = ဘယ်နှစ်လဲ?", ["၆", "၇", "၈", "၉"], 3),
    ("၉ + ၆ = ဘယ်နှစ်လဲ?", ["၁၃", "၁၄", "၁၅", "၁၆"], 2),
    ("၂၀ - ၇ = ဘယ်နှစ်လဲ?", ["၁၁", "၁၂", "၁၃", "၁၄"], 2),
    ("တောရဲ့ဘုရင် လို့ခေါ်ကြတဲ့ တိရစ္ဆာန်က ဘာလဲ?", ["ကျား", "ဆင်", "ခြင်္သေ့", "ဝံ"], 2),
    ("ကုန်းနေတိရစ္ဆာန်တွေထဲမှာ အကြီးဆုံးက ဘာလဲ?", ["ကျား", "ဆင်", "ဒရယ်", "မြင်း"], 1),
    ("ကုန်းနေတိရစ္ဆာန်တွေထဲမှာ အမြန်ဆုံးပြေးနိုင်တာ ဘာလဲ?", ["ခြင်္သေ့", "ကျား", "ချီတား", "မြင်း"], 2),
    ("ကုလားအုတ်ရဲ့ ကျောကုန်းအနုံထဲမှာ ဘာသိုလှောင်ထားလဲ?", ["ရေ", "အဆီ", "အသား", "အရိုး"], 1),
    ("ငှက်တွေက ဘယ်လိုမျိုးပွားလဲ?", ["ကလေးမွေးတယ်", "ဥကနေထွက်တယ်", "ခွဲထွက်တယ်", "အခွံဖောက်ထွက်တယ်"], 1),
    ("ရေရဲ့ ဓာတုနာမည်က ဘာလဲ?", ["CO2", "H2O", "O2", "NaCl"], 1),
    ("သက်တံရဲ့ အရောင်က ဘယ်နှစ်ရောင်ပါလဲ?", ["၅ရောင်", "၆ရောင်", "၇ရောင်", "၈ရောင်"], 2),
    ("လူ့ခန္ဓာကိုယ်ထဲမှာ သွေးကို ပန့်ပို့ပေးတဲ့ အင်္ဂါက ဘာလဲ?", ["အဆုတ်", "နှလုံး", "အသည်း", "ကျောက်ကပ်"], 1),
    ("ကမ္ဘာမြေက နေကို တစ်ပတ်လှည့်ဖို့ ဘယ်လောက်ကြာလဲ?", ["၁ ရက်", "၁ လ", "၁ နှစ်", "၁၀ နှစ်"], 2),
    ("တစ်ရက်မှာ ဘယ်နှစ်နာရီရှိလဲ?", ["၁၂", "၂၀", "၂၄", "၃၀"], 2),
    ("တနင်္ဂနွေနေ့ရဲ့ နောက်တစ်ရက်က ဘာနေ့လဲ?", ["စနေနေ့", "တနင်္လာနေ့", "အင်္ဂါနေ့", "ဗုဒ္ဓဟူးနေ့"], 1),
    ("သစ်ရွက်တွေရဲ့ ပုံမှန်အရောင်က ဘာလဲ?", ["အနီ", "အဝါ", "အစိမ်း", "အပြာ"], 2),
    ("နှင်းရဲ့ အရောင်က ဘာလဲ?", ["ဖြူ", "အနက်", "အဝါ", "အနီ"], 0),
    ("နေက ဘယ်ဘက်ကနေ ထွက်လဲ?", ["အနောက်", "တောင်", "အရှေ့", "မြောက်"], 2),
    ("တစ်ပတ်မှာ ဘယ်နှစ်ရက် ရှိလဲ?", ["၅ရက်", "၆ရက်", "၇ရက်", "၈ရက်"], 2),
    ("နွားနို့ရဲ့ ပုံမှန်အရောင်က ဘာလဲ?", ["ဖြူ", "အဝါ", "အညို", "ပြာ"], 0),
    ("ငါးတွေ ရေထဲမှာ အသက်ရှုဖို့ ဘယ်အင်္ဂါကို သုံးလဲ?", ["အဆုတ်", "ယင်ကို", "အသည်း", "နှလုံး"], 1),
    ("ကမ္ဘာပေါ်မှာ အကြီးဆုံး သဲကန္တာရက ဘာလဲ?", ["ဂိုဘီ", "ဆာဟာရ", "အာရေဗျ", "သာရ်"], 1),
    ("ပါရီမြို့က ဘယ်နိုင်ငံမှာ ရှိလဲ?", ["အီတလီ", "ပြင်သစ်", "ဂျာမနီ", "စပိန်"], 1),
    ("မြန်မာ့အမျိုးသားအားကစား (ရိုးရာ) က ဘာလဲ?", ["ဘောလုံး", "ချင်လုံ", "တင်းနစ်", "ရေကူး"], 1),
    ("ပုဂံမြို့က ဘယ်တိုင်းဒေသကြီးထဲ ရှိလဲ?", ["စစ်ကိုင်းတိုင်း", "မန္တလေးတိုင်း", "မကွေးတိုင်း", "ပဲခူးတိုင်း"], 1),
    ("မြန်မာနိုင်ငံရဲ့ တရားဝင်ဘာသာစကားက ဘာလဲ?", ["အင်္ဂလိပ်", "မြန်မာ", "ထိုင်း", "တရုတ်"], 1),
    ("ပျားရည်ကို ဘယ်ပိုးက ထုတ်လုပ်လဲ?", ["ပုရွက်ဆိတ်", "ပျား", "ပိုးကောင်", "ယင်"], 1),
    ("ကမ္ဘာပေါ်မှာ အနှေးဆုံး ရွေ့လျားတဲ့ တိရစ္ဆာန်တစ်မျိုးက ဘာလဲ?", ["လိပ်", "ချစ်ချက်", "ခွာသုံးလိပ်", "ပျား"], 0),
    ("တစ်လအတွင်း ရက်အနည်းဆုံးရှိတဲ့လက ဘယ်လလဲ?", ["ဇန်နဝါရီ", "ဖေဖော်ဝါရီ", "မတ်", "ဧပြီ"], 1),
    ("၈ + ၇ = ဘယ်နှစ်လဲ?", ["၁၃", "၁၄", "၁၅", "၁၆"], 2),
    ("၆ x ၄ = ဘယ်နှစ်လဲ?", ["၂၀", "၂၂", "၂၄", "၂၆"], 2),
    ("ရေခဲရဲ့ အခြေအနေက ဘာလဲ (အစိုင်အခဲ/အရည်/အငွေ့)?", ["အရည်", "အစိုင်အခဲ", "အငွေ့", "ဓာတ်ငွေ့"], 1),
    ("မီးရထားလမ်းပေါ်မှာ အသုံးများဆုံး သတ္တုက ဘာလဲ?", ["ရွှေ", "သံမဏိ", "ကြေး", "ခဲ"], 1),
    ("လကို ဘယ်အချိန်မှာ အကောင်းဆုံး မြင်ရလဲ?", ["နေ့ခင်း", "ညဘက်", "နံနက်", "ညနေ"], 1),
]

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]stardrop(?:@\w+)?$', 'bot1')))
async def star_giveaway_start(event):
    if event.sender_id != OWNER_ID: return
    q_text, options, correct_idx = random.choice(STAR_GIVEAWAY_QUESTIONS)
    giveaway_id = f"SG{random.randint(100000, 999999)}"
    buttons = [
        [Button.inline(f"1️⃣ {options[0]}", data=f"sgans_{giveaway_id}_0"),
         Button.inline(f"2️⃣ {options[1]}", data=f"sgans_{giveaway_id}_1")],
        [Button.inline(f"3️⃣ {options[2]}", data=f"sgans_{giveaway_id}_2"),
         Button.inline(f"4️⃣ {options[3]}", data=f"sgans_{giveaway_id}_3")],
    ]
    text = (
        f"⭐🎉 <b>STAR ကြဲပွဲ!</b> 🎉⭐\n\n"
        f"❓ <b>{escape_html(q_text)}</b>\n\n"
        f"⏱ <i>2 မိနစ်အတွင်း အဖြေမှန်ကို ရွေးပါ — ဒီ Group ထဲက အမြန်ဆုံး အဖြေမှန်ရွေးသူ "
        f"⭐ {STAR_GIVEAWAY_REWARD} ရမည်!</i>\n"
        f"🤫 <i>ရလဒ်ကို 2 မိနစ်ပြည့်မှ တစ်ပြိုင်နက် ကြေညာပါမည်။</i>"
    )
    giveaway = {
        "question": q_text, "options": options, "correct_idx": correct_idx,
        "expiry": time.time() + STAR_GIVEAWAY_DURATION, "reward": STAR_GIVEAWAY_REWARD,
        "messages": {}, "attempts": {}, "answered": set(), "owner_chat": event.chat_id,
    }
    active_star_giveaways[giveaway_id] = giveaway

    status_msg = await event.reply("⏳ <b>Star ကြဲပွဲကို Group အားလုံးဆီ ပို့နေပါသည်...</b>", parse_mode='html')
    groups = await groups_col.find().to_list(length=None)
    sent, failed = 0, 0
    for g in groups:
        chat_id = g["chat_id"]
        try:
            msg = await bot1.send_message(chat_id, text, parse_mode='html', buttons=buttons)
            giveaway["messages"][chat_id] = msg.id
            giveaway["attempts"][chat_id] = []
            sent += 1
            await asyncio.sleep(4)  # gentle pacing — same rate as /send, avoids FloodWaitError across many groups
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 2)
            try:
                msg = await bot1.send_message(chat_id, text, parse_mode='html', buttons=buttons)
                giveaway["messages"][chat_id] = msg.id
                giveaway["attempts"][chat_id] = []
                sent += 1
            except Exception:
                failed += 1
        except Exception:
            failed += 1

    await status_msg.edit(
        f"✅ <b>Star ကြဲပွဲ ပို့ပြီးပါပြီ!</b>\n"
        f"📨 <b>Group အောင်မြင်:</b> <code>{sent}</code>  │  ❌ <b>မအောင်မြင်:</b> <code>{failed}</code>\n"
        f"⏱ <i>2 မိနစ်အကြာမှာ ရလဒ် အနှစ်ချုပ်ကို ဒီမှာ ပြန်ကြေညာပါမယ်။</i>",
        parse_mode='html'
    )
    asyncio.create_task(_finalize_star_giveaway(giveaway_id))


@bot1.on(events.CallbackQuery(pattern=r'^sgans_(\S+)_(\d)$'))
async def star_giveaway_answer_callback(event):
    giveaway_id = event.pattern_match.group(1)
    if isinstance(giveaway_id, bytes): giveaway_id = giveaway_id.decode('utf-8')
    option_idx = int(event.pattern_match.group(2))
    giveaway = active_star_giveaways.get(giveaway_id)
    if not giveaway or time.time() > giveaway["expiry"]:
        return await event.answer("⏳ ဒီ Star ကြဲပွဲ ပြီးဆုံးသွားပါပြီ။", alert=True)
    chat_id = event.chat_id
    user_id = event.sender_id
    key = (chat_id, user_id)
    if key in giveaway["answered"]:
        return await event.answer("✅ သင် ဖြေပြီးသားပါ — ရလဒ်ကို စောင့်ပါ။", alert=False)
    giveaway["answered"].add(key)
    giveaway["attempts"].setdefault(chat_id, []).append((user_id, option_idx, time.time()))
    # 🩹 INTENTIONAL: never reveal correct/incorrect here — see module note above. Keeping
    # everyone guessing (and tapping) for the full window is the whole point.
    await event.answer("🗳️ သင့်အဖြေကို မှတ်တမ်းတင်ပြီးပါပြီ! ရလဒ်ကို 2 မိနစ်ပြည့်မှ ကြေညာပါမယ်။", alert=True)


async def _finalize_star_giveaway(giveaway_id):
    giveaway = active_star_giveaways.get(giveaway_id)
    if not giveaway:
        return
    await asyncio.sleep(max(0, giveaway["expiry"] - time.time()))
    reward = giveaway["reward"]
    correct_idx = giveaway["correct_idx"]
    correct_text = giveaway["options"][correct_idx]
    groups_with_winner = 0
    winners_total = 0
    for chat_id, msg_id in giveaway["messages"].items():
        attempts = giveaway["attempts"].get(chat_id, [])
        correct_attempts = sorted((a for a in attempts if a[1] == correct_idx), key=lambda a: a[2])
        if correct_attempts:
            winner_id = correct_attempts[0][0]
            await users_catcher_col.update_one({"user_id": winner_id}, {"$inc": {"star_balance": reward}}, upsert=True)
            groups_with_winner += 1
            winners_total += 1
            mention = f"<a href='tg://user?id={winner_id}'>🏆 Winner</a>"
            result_text = (
                f"⭐ <b>STAR ကြဲပွဲ ပြီးဆုံးပါပြီ!</b>\n\n"
                f"❓ {escape_html(giveaway['question'])}\n"
                f"✅ <b>အဖြေမှန်:</b> {escape_html(correct_text)}\n\n"
                f"🏆 {mention} က ဒီ Group ထဲမှာ အမြန်ဆုံး အဖြေမှန်ရွေးနိုင်ခဲ့လို့ ⭐ {reward} ရရှိသွားပါတယ်!"
            )
        else:
            result_text = (
                f"⭐ <b>STAR ကြဲပွဲ ပြီးဆုံးပါပြီ!</b>\n\n"
                f"❓ {escape_html(giveaway['question'])}\n"
                f"✅ <b>အဖြေမှန်:</b> {escape_html(correct_text)}\n\n"
                f"😢 ဒီ Group ထဲမှာ ဘယ်သူမှ အဖြေမှန် မရွေးနိုင်ခဲ့ပါဘူး။"
            )
        try:
            await bot1.edit_message(chat_id, msg_id, result_text, parse_mode='html', buttons=None)
        except Exception:
            pass
        await asyncio.sleep(0.3)  # gentle pacing on the reveal edits too

    total_groups = len(giveaway["messages"])
    try:
        await bot1.send_message(
            giveaway["owner_chat"],
            f"📊 <b>Star ကြဲပွဲ ရလဒ် အနှစ်ချုပ်</b>\n"
            f"🪐 <b>Group စုစုပေါင်း:</b> <code>{total_groups}</code>\n"
            f"🏆 <b>အနိုင်ရ Group အရေအတွက်:</b> <code>{groups_with_winner}</code>\n"
            f"⭐ <b>Star ရရှိသူ စုစုပေါင်း:</b> <code>{winners_total}</code> ယောက် (တစ်ယောက်လျှင် ⭐{reward} စီ)",
            parse_mode='html'
        )
    except Exception:
        pass
    active_star_giveaways.pop(giveaway_id, None)

# ==========================================
# 🗑️ HAITIME / RESETSTATS / GIFTALL / STEALTH
# ==========================================
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]giftall(?:@\w+)?\s+(\d+(?:\.\d+)?)$', 'bot1')))
async def owner_giftall_broadcast(event):
    if event.sender_id != OWNER_ID: return
    amount = round(float(event.pattern_match.group(1)), 2)
    try:
        result = await users_catcher_col.update_many({}, {"$inc": {"star_balance": amount}})
        await event.reply(f"🎉 <b>GIVEAWAY</b>\n<code>{amount:,}⭐</code> was given to <b>{result.modified_count}</b> players.", parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ Error: <code>{e}</code>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]resetstats(?:@\w+)?$', 'bot1')))
async def reset_group_counters_by_owner(event):
    if event.sender_id != OWNER_ID: return
    try:
        # group_spawn_counters (in-memory) is the source of truth for spawn decisions now —
        # clearing only the Mongo copy would do nothing (the next periodic flush would just
        # overwrite it right back with whatever's still live in memory).
        group_spawn_counters.clear()
        await groups_counters_col.update_many({}, {"$set": {"counter": 0}})
        await event.reply(f"⚙️ <b>All counters reset to 0.</b>", parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ Error: <code>{e}</code>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]haitime(?:@\w+)?$', 'bot1')))
async def show_spawn_target_status(event):
    if event.sender_id != OWNER_ID: return
    global_config = await groups_config_col.find_one({"chat_id": GLOBAL_SPAWN_CHAT_KEY})
    global_target = global_config.get("spawn_target", 50) if global_config else 50
    overrides = await groups_config_col.find(
        {"chat_id": {"$ne": GLOBAL_SPAWN_CHAT_KEY}, "spawn_target": {"$exists": True}}
    ).to_list(length=None)
    text = (
        f"<b>SPAWN THRESHOLD STATUS</b>\n"
        f"🌐 <b>Global (default for all groups):</b> <code>{global_target}</code> messages\n"
    )
    if overrides:
        text += f"\n📌 <b>{len(overrides)} group(s) currently have their own override:</b>\n"
        for doc in overrides[:20]:
            text += f"  • <code>{doc['chat_id']}</code> → <code>{doc.get('spawn_target')}</code>\n"
        if len(overrides) > 20:
            text += f"  … and {len(overrides) - 20} more\n"
        text += "<i>Setting a new global value with /haitime [count] clears all of these automatically.</i>"
    else:
        text += "\n📌 No group has its own override — every group follows the global value above."
    text += (
        f"\n\n📖 <b>Usage:</b>\n"
        f"<code>/haitime [count]</code> — set the global default\n"
        f"<code>/haitime [chat_id] [count]</code> — override one specific group"
    )
    await event.reply(text, parse_mode='html')

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]haitime(?:@\w+)?\s+(-?\d+)(?:\s+(-?\d+))?$', 'bot1')))
async def change_spawn_target_handler(event):
    if event.sender_id != OWNER_ID: return
    args = event.pattern_match.groups()
    try:
        val1 = int(args[0])
        val2 = int(args[1]) if args[1] else None
    except (ValueError, TypeError):
        return await event.reply("⚠️ <b>Invalid format.</b>\nUsage: <code>/haitime <count></code> or <code>/haitime <chat_id> <count></code>", parse_mode='html')
    if val2 is not None:
        target_chat_id = val1
        new_target = val2
        scope_text = f"Group ID: <code>{target_chat_id}</code>"
    else:
        target_chat_id = GLOBAL_SPAWN_CHAT_KEY
        new_target = val1
        scope_text = "Global (default for every group without its own override)"
    if new_target <= 0: return await event.reply("❌ <b>Count must be > 0.</b>", parse_mode='html')
    await groups_config_col.update_one({"chat_id": target_chat_id}, {"$set": {"spawn_target": new_target}}, upsert=True)
    cleared_count = 0
    if target_chat_id == GLOBAL_SPAWN_CHAT_KEY:
        # 🩹 FIX: setting the global value used to leave every per-group override untouched,
        # so those groups silently kept ignoring it — the owner had to hunt down and clear each
        # one by hand. /haitime [count] (no chat_id) now means "this is the value, everywhere,
        # no exceptions": every other group's override is cleared in the same call.
        clear_result = await groups_config_col.update_many(
            {"chat_id": {"$ne": GLOBAL_SPAWN_CHAT_KEY}, "spawn_target": {"$exists": True}},
            {"$unset": {"spawn_target": ""}}
        )
        cleared_count = clear_result.modified_count
    # A global-default change affects every chat that falls back to it, and we can't tell
    # from here which cached entries that includes — clearing the whole cache is cheap
    # (it's just a dict) and guarantees no group keeps running on a stale value.
    _spawn_target_cache.clear()
    # Verify the write actually persisted before confirming to the owner
    confirm_doc = await groups_config_col.find_one({"chat_id": target_chat_id})
    confirmed_value = confirm_doc.get("spawn_target") if confirm_doc else None
    if confirmed_value != new_target:
        return await event.reply(
            f"❌ <b>Notice:</b> The update didn't save correctly. Please try again.\n"
            f"<code>Expected {new_target}, found {confirmed_value}</code>",
            parse_mode='html'
        )
    extra_note = f"\n✅ <i>{cleared_count} group override(s) cleared — every group now follows this value.</i>" if cleared_count else ""
    await event.reply(
        f"⚙️ <b>SPAWN THRESHOLD UPDATED</b>\n"
        f"<blockquote>New spawn count for {scope_text} set to <code>{new_target}</code> messages.</blockquote>"
        f"{extra_note}",
        parse_mode='html'
    )

async def ghost_spawn_cleaner():
    while True:
        try:
            current_time = time.time()
            expired_chats = []
            for chat_id, data in active_group_spawns.items():
                if current_time - data.get("spawn_time", 0) > 1800:
                    expired_chats.append(chat_id)
            for chat_id in expired_chats:
                if chat_id in active_group_spawns: del active_group_spawns[chat_id]
                if chat_id in spawn_locks: del spawn_locks[chat_id]
            # 🛡️ RELIABILITY: pending_rarity_quiz entries are normally cleaned up by
            # rarity_gate_quiz_timeout_watcher a few minutes after posting. This is a
            # belt-and-suspenders safety net — if that watcher task ever died without running
            # its cleanup, a leftover entry here would silently block ALL future auto-spawns
            # in that chat forever (see the guard at the top of global_message_counter_handler).
            expired_quizzes = [
                cid for cid, quiz in pending_rarity_quiz.items()
                if current_time - quiz.get("quiz_time", 0) > RARITY_GATE_TIMEOUT_SECONDS + 60
            ]
            for cid in expired_quizzes:
                if cid in pending_rarity_quiz: del pending_rarity_quiz[cid]
            # Same belt-and-suspenders reasoning, for pending_trivia_quiz /
            # trivia_quiz_timeout_watcher — a stuck entry here only blocks that one chat's
            # future trivia spawns (see the guard at the top of global_message_counter_handler),
            # never the character-spawn system, but it's still cleaned up here just in case.
            expired_trivia = [
                cid for cid, quiz in pending_trivia_quiz.items()
                if current_time - quiz.get("quiz_time", 0) > TRIVIA_TIMEOUT_SECONDS + 60
            ]
            for cid in expired_trivia:
                if cid in pending_trivia_quiz: del pending_trivia_quiz[cid]
        except Exception as e:
            logging.error(f"Cleaner Error: {e}")
        await asyncio.sleep(300)

# ==========================================
# 🚨 STEALTH CONTROLLER
# ==========================================
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]stealth(?:@\w+)?(?:\s+(on|off))?$', 'bot1')))
async def toggle_stealth(event):
    global STEALTH_MAU_MODE
    if event.sender_id != OWNER_ID: return
    args = event.pattern_match.group(1)
    if not args:
        status = "🟢 ACTIVE" if STEALTH_MAU_MODE else "🔴 INACTIVE"
        return await event.reply(f"<b>Stealth MAU Status:</b> {status}\n📌 <code>/stealth on</code> or <code>/stealth off</code>", parse_mode='html')
    if args.lower() == "on":
        STEALTH_MAU_MODE = True
        await event.reply("🟢 <b>Stealth MAU Engine: ACTIVATED!</b>", parse_mode='html')
    elif args.lower() == "off":
        STEALTH_MAU_MODE = False
        await event.reply("🔴 <b>Stealth MAU Engine: DEACTIVATED!</b>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'(?i)^(play|/|\.|p|harem|h|vault|v|waifu|w|daily|claim)$'))
async def stealth_mau_handler(event):
    global STEALTH_MAU_MODE
    if not STEALTH_MAU_MODE or event.is_private: return
    user_id = event.sender_id
    try:
        await ensure_user_registered(user_id, await get_plain_name(event, user_id))
    except Exception:
        pass

# ==========================================
# 📡 WEATHER ENGINE (Fixed)
# ==========================================
def fetch_live_weather(city_id="Yangon"):
    try:
        search_query = city_id.replace("_", " ")
        url = f"https://wttr.in/{search_query}?format=j1"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=7) as response:
            data = json.loads(response.read().decode())
            current = data['current_condition'][0]
            temp_c = current['temp_C']
            weather_desc = current['weatherDesc'][0]['value'].strip()
            humidity = current['humidity']
            wind_speed = current['windspeedKmph']
            translations = {
                "Sunny": "☀️ Sunny", "Clear": "🌌 Clear", "Partly cloudy": "⛅ Partly cloudy",
                "Cloudy": "☁️ Cloudy", "Overcast": "☁️ Overcast", "Mist": "🌫️ Mist",
                "Fog": "🌫️ Fog", "Patchy rain nearby": "🌦️ Patchy rain",
                "Light rain": "🌧️ Light rain", "Moderate rain": "🌧️ Moderate rain",
                "Heavy rain": "⛈️ Heavy rain", "Thunderstorm": "⛈️ Thunderstorm",
                "Torrential rain shower": "⛈️ Torrential rain"
            }
            translated = translations.get(weather_desc, weather_desc)
            return {"success": True, "temp": temp_c, "desc": translated, "humidity": humidity, "wind": wind_speed, "city": search_query.upper()}
    except Exception as e:
        print(f"Weather Fetch Error: {e}")
        return {"success": False}

@bot1.on(events.NewMessage(pattern=r"(?i)^[/.]weather$"))
async def weather_cmd_handler(event):
    buttons = [[Button.inline("🇲🇲 Myanmar", data="w_country_mm"), Button.inline("🇹🇭 Thailand", data="w_country_th")]]
    await event.reply("🌍 <b>Select Country</b>\nChoose a country.", parse_mode='html', buttons=buttons)

@bot1.on(events.CallbackQuery(pattern=r"^w_(.+)$"))
async def weather_callback_engine(event):
    # ✅ Handle both string and bytes data
    raw_data = event.data.decode('utf-8') if isinstance(event.data, bytes) else event.data
    action = raw_data.replace("w_", "")
    
    if action == "main_menu":
        buttons = [[Button.inline("🇲🇲 Myanmar", data="w_country_mm"), Button.inline("🇹🇭 Thailand", data="w_country_th")]]
        await event.edit("🌍 <b>Select Country</b>", parse_mode='html', buttons=buttons)
        await event.answer()
        return
    elif action == "country_mm":
        buttons = [
            [Button.inline("Yangon", data="w_city_Yangon"), Button.inline("Mandalay", data="w_city_Mandalay")],
            [Button.inline("Naypyidaw", data="w_city_Naypyidaw"), Button.inline("Taunggyi", data="w_city_Taunggyi")],
            [Button.inline("Bago", data="w_city_Bago"), Button.inline("Mawlamyine", data="w_city_Mawlamyine")],
            [Button.inline("⬅️ Back", data="w_main_menu")]
        ]
        await event.edit("🇲🇲 <b>Myanmar Regions</b>", parse_mode='html', buttons=buttons)
        await event.answer()
        return
    elif action == "country_th":
        buttons = [
            [Button.inline("Bangkok", data="w_city_Bangkok"), Button.inline("Chiang Mai", data="w_city_Chiang_Mai")],
            [Button.inline("Phuket", data="w_city_Phuket"), Button.inline("Pattaya", data="w_city_Pattaya")],
            [Button.inline("Hat Yai", data="w_city_Hat_Yai"), Button.inline("Khon Kaen", data="w_city_Khon_Kaen")],
            [Button.inline("⬅️ Back", data="w_main_menu")]
        ]
        await event.edit("🇹🇭 <b>Thai Provinces</b>", parse_mode='html', buttons=buttons)
        await event.answer()
        return
    elif action.startswith("city_"):
        city_name = action.replace("city_", "")
        await event.edit(f"📡 <i>Fetching weather for {city_name}...</i>", parse_mode='html')
        loop = asyncio.get_event_loop()
        w_data = await loop.run_in_executor(None, fetch_live_weather, city_name)
        mm_cities = ["Yangon", "Mandalay", "Naypyidaw", "Taunggyi", "Bago", "Mawlamyine"]
        back_target = "w_country_mm" if city_name in mm_cities else "w_country_th"
        control_buttons = [[Button.inline("🔄 Refresh", data=f"w_city_{city_name}")], [Button.inline("⬅️ Back", data=back_target)]]
        
        if w_data["success"]:
            response_text = (
                f"🌍 <b>LIVE WEATHER</b>\n"
                f"📍 <b>Location:</b> <code>{w_data['city']}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🌡️ <b>Temp:</b> <code>{w_data['temp']}°C</code>\n"
                f"💧 <b>Humidity:</b> <code>{w_data['humidity']}%</code>\n"
                f"💨 <b>Wind:</b> <code>{w_data['wind']} Km/h</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🌤️ <b>Condition:</b> <code>{w_data['desc']}</code>"
            )
        else:
            response_text = f"❌ <b>ERROR:</b> Could not retrieve data for {city_name}."
        
        await event.edit(response_text, parse_mode='html', buttons=control_buttons)
        await event.answer()
# ==========================================
# 🏰 SQUAD SYSTEM (အသင်း) - မြန်မာလို
# ==========================================
# Founding a Squad costs SQUAD_CREATION_COST Star and reserves up to SQUAD_MAX_MEMBERS seats
# (leader included). Squad "Points" = the sum of every member's total_caught + total_gifted +
# total_buys, which is what both the cross-squad Global Rank (/squads) and each member's own
# in-squad rank (shown on /squad) are sorted by — so every member contributing (catching,
# gifting, or buying) moves the whole squad up, not just the leader.
SQUAD_CREATION_COST = 100000      # ⭐ Star cost to found a Squad
SQUAD_MAX_MEMBERS = 8             # seats, leader included
SQUAD_SETUP_TIMEOUT = 1200        # 20 min to submit Name + Photo before the flow auto-cancels
SQUAD_INVITE_FEE = 10000          # ⭐ Star an invited member pays to actually join a Squad
SQUAD_INVITE_LEADER_CUT = 1000    # ⭐ of that fee the Squad leader keeps; the rest → bot3 treasury


async def get_user_squad(user_id):
    """The Squad doc the user currently belongs to (as leader or member), or None."""
    return await squads_col.find_one({"members": user_id})


async def _compute_squad_points(members):
    """One batched query for every member's contribution — returns
    (total_points, total_wealth, member_list). member_list carries each member's raw
    catch/gift/buy counts plus their combined 'points', already keyed by user_id so callers
    can look a specific member up without a second query."""
    if not members:
        return 0, 0, []
    user_docs = await users_catcher_col.find(
        {"user_id": {"$in": members}},
        {"user_id": 1, "fullname": 1, "star_balance": 1, "total_caught": 1, "total_gifted": 1, "total_buys": 1}
    ).to_list(length=None)
    total_points = 0
    total_wealth = 0
    member_list = []
    seen = set()
    for doc in user_docs:
        uid = doc["user_id"]
        seen.add(uid)
        pts = doc.get("total_caught", 0) + doc.get("total_gifted", 0) + doc.get("total_buys", 0)
        total_points += pts
        total_wealth += doc.get("star_balance", 0)
        member_list.append({
            "user_id": uid,
            "fullname": doc.get("fullname") or f"User {uid}",
            "total_caught": doc.get("total_caught", 0),
            "total_gifted": doc.get("total_gifted", 0),
            "total_buys": doc.get("total_buys", 0),
            "points": pts,
        })
    # A member listed on the Squad doc but with no users_catcher_col record yet (shouldn't
    # normally happen) still gets a zeroed row so member counts/UI stay consistent.
    for uid in members:
        if uid not in seen:
            member_list.append({"user_id": uid, "fullname": f"User {uid}", "total_caught": 0, "total_gifted": 0, "total_buys": 0, "points": 0})
    return total_points, total_wealth, member_list


async def calculate_squad_rank(squad_id):
    """Global rank (1 = highest Squad Points) among every Squad that currently exists."""
    all_squads = await squads_col.find({}, {"squad_id": 1, "members": 1}).to_list(length=None)
    if not all_squads:
        return 1
    squad_points = {}
    for sq in all_squads:
        sid = sq.get("squad_id")
        if not sid:
            continue
        pts, _, _ = await _compute_squad_points(sq.get("members", []))
        squad_points[sid] = pts
    sorted_squads = sorted(squad_points.items(), key=lambda x: x[1], reverse=True)
    for idx, (sid, _) in enumerate(sorted_squads, start=1):
        if sid == squad_id:
            return idx
    return len(sorted_squads) + 1


async def render_squad_profile(squad_doc, viewer_id, page=1):
    """Squad Profile — Page 1 is the squad's own stats PLUS the viewing member's personal
    standing inside it (their in-squad rank, points, and % contribution); Page 2 is the
    full member list ranked by contribution, with the viewer's own row marked."""
    squad_id = squad_doc["squad_id"]
    name = squad_doc.get("name", "Unnamed")
    leader_id = squad_doc.get("leader_id")
    members = squad_doc.get("members", [])

    total_points, total_wealth, member_list = await _compute_squad_points(members)
    rank = await calculate_squad_rank(squad_id)

    # Names come straight from each member's own cached `fullname` (populated on every catch)
    # rather than a live get_entity() lookup — up to 8 API round-trips per single profile view
    # would otherwise risk flood-waits on a busy bot.
    leader_entry = next((m for m in member_list if m["user_id"] == leader_id), None)
    if leader_entry:
        leader_mention = f"<a href='tg://user?id={leader_id}'><b>{escape_html(clean_display_name(leader_entry['fullname']))}</b></a>"
    else:
        leader_mention = f"<code>{leader_id}</code>"

    sorted_members = sorted(member_list, key=lambda x: x["points"], reverse=True)
    viewer_rank, viewer_entry = None, None
    for idx, m in enumerate(sorted_members, start=1):
        if m["user_id"] == viewer_id:
            viewer_rank, viewer_entry = idx, m
            break
    viewer_points = viewer_entry["points"] if viewer_entry else 0
    contribution_pct = (viewer_points / total_points * 100) if total_points > 0 else 0.0
    role_label = "👑 Squad Leader" if viewer_id == leader_id else "🧑‍🤝‍🧑 Member"

    if page == 1:
        text = (
            f"🏰 <b>SQUAD PROFILE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📛 <b>Name:</b> <code>{escape_html(name)}</code>\n"
            f"👑 <b>Leader:</b> {leader_mention}\n"
            f"🏅 <b>Global Rank:</b> <code>#{rank}</code>\n"
            f"👥 <b>Members:</b> <code>{len(members)} / {SQUAD_MAX_MEMBERS}</code>\n"
            f"⭐ <b>Squad Points:</b> <code>{total_points:,}</code>\n"
            f"💰 <b>Total Wealth:</b> <code>{format_star_plain(total_wealth)}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>YOUR STANDING</b>\n"
            f"🪪 <b>Role:</b> {role_label}\n"
            f"🎖 <b>Your Rank in Squad:</b> <code>#{viewer_rank if viewer_rank else '-'} / {len(members)}</code>\n"
            f"⭐ <b>Your Points:</b> <code>{viewer_points:,}</code> <i>({contribution_pct:.1f}% of squad)</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>👇 Member စာရင်းကြည့်ရန် အောက်ခလုတ်နှိပ်ပါ။</i>"
        )
        buttons = [[Button.inline("👥 Member List (Page 2)", data=f"squad_page_{squad_id}_2")]]
        return text, buttons

    # Page 2: full member list, ranked by contribution, viewer's own row marked
    member_lines = []
    for idx, m in enumerate(sorted_members, start=1):
        mention = f"<a href='tg://user?id={m['user_id']}'><b>{escape_html(clean_display_name(m['fullname']))}</b></a>"
        crown = " 👑" if m["user_id"] == leader_id else ""
        you_tag = " <i>(You)</i>" if m["user_id"] == viewer_id else ""
        member_lines.append(
            f"{idx}. {mention}{crown}{you_tag}\n"
            f"   └─ 🎯 <code>{m['total_caught']}</code> | 🎁 <code>{m['total_gifted']}</code> | 🛒 <code>{m['total_buys']}</code> | ⭐ <code>{m['points']:,}</code>"
        )

    text = (
        f"🏰 <b>SQUAD MEMBERS</b> <i>(Active Ranking)</i>\n"
        f"📛 <b>Squad:</b> <code>{escape_html(name)}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        + "\n".join(member_lines[:SQUAD_MAX_MEMBERS]) +
        f"\n━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>👑 Leader | ⭐ = 🎯Catch + 🎁Gift + 🛒Buy</i>"
    )
    buttons = [[Button.inline("⬅️ Back to Profile", data=f"squad_page_{squad_id}_1")]]
    return text, buttons


async def send_squad_profile_message(event, squad_doc, page=1):
    text, buttons = await render_squad_profile(squad_doc, event.sender_id, page=page)
    storage_msg_id = squad_doc.get("storage_msg_id")
    if storage_msg_id:
        try:
            storage_msg = await bot1.get_messages(SPECIFIC_CONTROL_GROUP, ids=storage_msg_id)
            if storage_msg and storage_msg.media:
                await _out(event, text, parse_mode='html', file=storage_msg.media, buttons=buttons)
                return
        except Exception:
            pass
    await _out(event, text, parse_mode='html', buttons=buttons)


# ==========================================
# 📝 1. Squad တည်ထောင်ခြင်း (/create)
# ==========================================
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]create(?:@\w+)?$', 'bot1')))
async def squad_create_command(event):
    user_id = event.sender_id

    if await get_user_squad(user_id):
        return await _out(event, "❌ သင်သည် Squad တစ်ခုထဲမှာ ရှိပြီးသားပါ။", parse_mode='html')

    if user_id in pending_squad_setup:
        return await _out(
            event,
            "⏳ သင့်ရဲ့ Squad တည်ထောင်ခြင်း လုပ်ငန်းစဉ် တစ်ခု ဆက်လက်လုပ်ဆောင်နေဆဲပါ။\n"
            "ပယ်ဖျက်ချင်ရင် <code>/cancelsquad</code> ရိုက်ပါ (Star ပြန်အမ်းပေးပါမယ်)။",
            parse_mode='html'
        )

    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    star_bal = user_doc.get("star_balance", 0) if user_doc else 0
    if star_bal < SQUAD_CREATION_COST:
        return await _out(
            event,
            f"❌ Squad တည်ထောင်ဖို့ <code>{SQUAD_CREATION_COST:,}⭐</code> လိုပါတယ်။ "
            f"မင်းမှာ <code>{format_star_plain(star_bal)}</code> ပဲရှိတယ်။",
            parse_mode='html'
        )

    text = (
        f"🏰 <b>Squad တည်ထောင်ရန်</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>တည်ထောင်စရိတ်:</b> <code>{SQUAD_CREATION_COST:,}⭐</code>\n"
        f"👥 <b>အများဆုံးအသင်းသား:</b> <code>{SQUAD_MAX_MEMBERS} ယောက်</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Squad တည်ထောင်ပြီးပါက အောက်ပါအဆင့်များ ဆက်လုပ်ရမယ်။</i>\n"
        f"1️⃣ Squad Name ပေးရမယ်\n"
        f"2️⃣ Squad Profile Photo/Video ပေးရမယ်\n\n"
        f"တည်ထောင်မလား?"
    )
    buttons = [
        [colored_button("✅ တည်ထောင်မယ်", f"squad_create_confirm_{user_id}", color="success"),
         Button.inline("❌ မတည်ထောင်ဘူး", data=f"squad_create_cancel_{user_id}")]
    ]
    await _out(event, text, parse_mode='html', buttons=buttons)


async def _squad_setup_timeout_watchdog(user_id, origin_chat_id, deadline):
    """If Name+Photo aren't both submitted within SQUAD_SETUP_TIMEOUT, refunds the Star cost
    and clears the pending state — otherwise a user who abandons the flow partway through
    would have paid SQUAD_CREATION_COST for a Squad that never gets created, with no way back
    short of an admin manually fixing their balance."""
    wait_for = deadline - time.time()
    if wait_for > 0:
        await asyncio.sleep(wait_for)
    state = pending_squad_setup.get(user_id)
    if not state or state.get("deadline") != deadline:
        return  # already completed, cancelled, or superseded by a newer attempt
    pending_squad_setup.pop(user_id, None)
    try:
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"star_balance": SQUAD_CREATION_COST}})
    except Exception as e:
        logging.error(f"❌ Squad setup refund failed for {user_id}: {e}")
        return
    notice = (
        f"⏳ <b>Squad Setup Timeout</b>\n"
        f"Squad Name/Photo ကို {SQUAD_SETUP_TIMEOUT // 60} မိနစ်အတွင်း လက်ခံရရှိခြင်း မရှိလို့ "
        f"Squad တည်ထောင်ခြင်းကို အလိုအလျောက် ပယ်ဖျက်လိုက်ပါပြီ။\n"
        f"⭐ <code>{SQUAD_CREATION_COST:,}</code> ကို ပြန်အမ်းပေးလိုက်ပါပြီ။ <code>/create</code> နဲ့ ပြန်စနိုင်ပါတယ်။"
    )
    try:
        await bot1.send_message(user_id, notice, parse_mode='html')
        return
    except Exception:
        pass
    try:
        await send_safe_message(bot1, origin_chat_id, notice, parse_mode='html')
    except Exception:
        pass


# ==========================================
# 🔘 Squad Creation Callbacks
# ==========================================
@bot1.on(events.CallbackQuery(pattern=r'^squad_create_confirm_(\d+)$'))
async def squad_create_confirm_callback(event):
    user_id = int(event.pattern_match.group(1))
    if event.sender_id != user_id:
        return await event.answer("⚠️ ဒါ မင်းရဲ့ လုပ်ဆောင်ချက် မဟုတ်ဘူး။", alert=True)

    if not claim_single_tap(event):
        return await event.answer("⏳ ခဏစောင့်ပါ...", alert=False)

    if await get_user_squad(user_id):
        await event.edit("❌ သင်သည် Squad တစ်ခုထဲမှာ ရှိပြီးသားပါ။", parse_mode='html', buttons=None)
        return await event.answer("Already in squad.", alert=True)

    if user_id in pending_squad_setup:
        await event.edit("⏳ Squad တည်ထောင်ခြင်း လုပ်ငန်းစဉ် တစ်ခု ရှိနှင့်ပြီးသားပါ။", parse_mode='html', buttons=None)
        return await event.answer("Already pending.", alert=True)

    if not await try_deduct_star(user_id, SQUAD_CREATION_COST):
        await event.edit(f"❌ Star မလုံလောက်ပါ။ <code>{SQUAD_CREATION_COST:,}⭐</code> လိုပါတယ်။", parse_mode='html', buttons=None)
        return await event.answer("Insufficient Star.", alert=True)

    prompt_msg = await event.edit(
        "✅ <b>Star နှုတ်ယူပြီးပါပြီ!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "ပထမဆုံးအနေနဲ့ <b>Squad Name</b> ကို ဒီမက်ဆေ့ခ်ျကို <u>Reply</u> ကာ ရိုက်ထည့်ပါ။\n"
        "<i>(ဘယ် Font နဲ့ရေးရေး လက်ခံပါတယ်)</i>\n\n"
        f"⏳ <i>{SQUAD_SETUP_TIMEOUT // 60} မိနစ်အတွင်း မပြီးစီးရင် Star ကို အလိုအလျောက် ပြန်အမ်းပေးပါမယ်။</i>",
        parse_mode='html', buttons=None
    )
    deadline = time.time() + SQUAD_SETUP_TIMEOUT
    pending_squad_setup[user_id] = {
        "stage": "name",
        "name": None,
        "chat_id": event.chat_id,
        "prompt_msg_id": prompt_msg.id,
        "deadline": deadline,
    }
    asyncio.create_task(_squad_setup_timeout_watchdog(user_id, event.chat_id, deadline))
    await event.answer("✅ Star နှုတ်ပြီးပါပြီ။ Name ထည့်ပါ။", alert=True)


@bot1.on(events.CallbackQuery(pattern=r'^squad_create_cancel_(\d+)$'))
async def squad_create_cancel_callback(event):
    user_id = int(event.pattern_match.group(1))
    if event.sender_id != user_id:
        return await event.answer("⚠️ ဒါ မင်းရဲ့ လုပ်ဆောင်ချက် မဟုတ်ဘူး။", alert=True)
    await event.edit("❌ Squad တည်ထောင်ခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။", parse_mode='html', buttons=None)
    await event.answer("Cancelled.", alert=True)


@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]cancelsquad(?:@\w+)?$', 'bot1')))
async def squad_cancel_setup_command(event):
    """Lets a user back out of an in-progress /create flow early (Star refunded immediately)
    instead of having to wait out the full SQUAD_SETUP_TIMEOUT for the watchdog to do it."""
    user_id = event.sender_id
    state = pending_squad_setup.pop(user_id, None)
    if not state:
        return await _out(event, "❌ ပယ်ဖျက်ရန် Squad တည်ထောင်ခြင်း လုပ်ငန်းစဉ် မရှိပါ။", parse_mode='html')
    await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"star_balance": SQUAD_CREATION_COST}})
    await _out(
        event,
        f"✅ Squad တည်ထောင်ခြင်းကို ပယ်ဖျက်ပြီး Star <code>{SQUAD_CREATION_COST:,}⭐</code> ကို ပြန်အမ်းပေးလိုက်ပါပြီ။",
        parse_mode='html'
    )


# ==========================================
# 📝 2. Squad Name နဲ့ Photo လက်ခံခြင်း (Reply Handler)
# ==========================================
# Only ever consumes an explicit reply to the EXACT prompt message this flow itself just sent
# (matched on both chat_id and message_id) — any other message from that user, anywhere else,
# passes straight through untouched. Without this check a broad incoming=True handler like
# this one would risk swallowing a completely unrelated message the user happens to send in
# some other group while a /create flow is still pending.
@bot1.on(events.NewMessage(incoming=True))
async def squad_setup_reply_handler(event):
    user_id = event.sender_id
    state = pending_squad_setup.get(user_id)
    if not state:
        return
    if not event.is_reply or event.chat_id != state["chat_id"] or event.reply_to_msg_id != state["prompt_msg_id"]:
        return

    if state["stage"] == "name":
        if not event.raw_text or not event.raw_text.strip():
            await event.reply("❌ Squad Name ကို စာသားအနေနဲ့ ရိုက်ထည့်ပြီး ဒီစာကို Reply ပြန်ပါ။")
            return
        squad_name = clean_display_name(event.raw_text.strip(), max_len=40, fallback="Unnamed Squad")
        state["name"] = squad_name
        state["stage"] = "photo"
        prompt_msg = await event.reply(
            f"✅ Squad Name <code>{escape_html(squad_name)}</code> ကို သိမ်းဆည်းလိုက်ပါပြီ။\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "အခု <b>Squad Profile Photo</b> (Photo/Video) ကို ဒီစာကို <u>Reply</u> ကာ ပို့ပေးပါ။",
            parse_mode='html'
        )
        state["prompt_msg_id"] = prompt_msg.id
        return

    if state["stage"] == "photo":
        if not (event.photo or event.video or event.document):
            await event.reply("❌ Photo သို့မဟုတ် Video တစ်ခု ပို့ပြီး ဒီစာကို Reply ပြန်ပါ။")
            return
        try:
            fwd_msg = await send_safe_message(bot1, SPECIFIC_CONTROL_GROUP, "", file=event.media)
            storage_msg_id = fwd_msg.id
        except Exception as e:
            await event.reply(f"❌ Media သိမ်းဆည်းရာမှာ အမှားရှိသွားတယ်: {escape_html(str(e))}")
            return

        squad_name = state.get("name") or "Unnamed Squad"
        squad_id = f"SQD_{random.randint(10000, 99999)}"
        while await squads_col.find_one({"squad_id": squad_id}):
            squad_id = f"SQD_{random.randint(10000, 99999)}"

        await squads_col.insert_one({
            "squad_id": squad_id,
            "name": squad_name,
            "leader_id": user_id,
            "members": [user_id],
            "storage_msg_id": storage_msg_id,
            "created_at": time.time(),
        })
        pending_squad_setup.pop(user_id, None)

        await event.reply(
            f"✅ <b>Squad တည်ထောင်ခြင်း အောင်မြင်ပါပြီ!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📛 <b>Squad Name:</b> <code>{escape_html(squad_name)}</code>\n"
            f"🆔 <b>Squad ID:</b> <code>{squad_id}</code>",
            parse_mode='html'
        )

        squad_doc = await squads_col.find_one({"squad_id": squad_id})
        if squad_doc:
            await send_squad_profile_message(event, squad_doc)
        return


# ==========================================
# 📊 3. Squad Profile ပြသခြင်း (/squad သို့မဟုတ် /squadprofile)
# ==========================================
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]squad(?:profile)?(?:@\w+)?$', 'bot1')))
async def squad_profile_command(event):
    user_id = event.sender_id
    squad_doc = await get_user_squad(user_id)
    if not squad_doc:
        return await _out(event, "❌ သင်သည် Squad တစ်ခုခုထဲမှာ မပါဝင်သေးပါ။ <code>/create</code> နဲ့ တည်ထောင်ပါ။", parse_mode='html')
    await send_squad_profile_message(event, squad_doc, page=1)


# ==========================================
# 🔘 Squad Page Navigation Callback
# ==========================================
@bot1.on(events.CallbackQuery(pattern=r'^squad_page_([a-zA-Z0-9_]+)_(\d+)$'))
async def squad_page_callback(event):
    squad_id = event.pattern_match.group(1)
    if isinstance(squad_id, bytes):
        squad_id = squad_id.decode('utf-8')
    page = int(event.pattern_match.group(2))

    user_id = event.sender_id
    squad_doc = await squads_col.find_one({"squad_id": squad_id})
    if not squad_doc:
        return await event.answer("❌ Squad မရှိတော့ဘူး။", alert=True)
    if user_id not in squad_doc.get("members", []):
        return await event.answer("⚠️ ဒီ Squad ထဲမှာ မင်းမပါဘူး။", alert=True)

    text, buttons = await render_squad_profile(squad_doc, user_id, page=page)
    try:
        await event.edit(text, parse_mode='html', buttons=buttons)
    except errors.MessageNotModifiedError:
        pass
    await event.answer()


# ==========================================
# 👥 4. Squad ထဲသို့ ဖိတ်ကြားခြင်း (/invite) — ဝင်ကြေး SQUAD_INVITE_FEE ⭐ ပါ
# ==========================================
# Flow: leader /invite (reply) sends a request only — nothing is charged or added yet.
# The INVITED user then taps Accept, which is the only point money actually moves:
#   target pays SQUAD_INVITE_FEE ⭐ total → SQUAD_INVITE_LEADER_CUT ⭐ of it goes straight to
#   the leader's star_balance, the remainder goes to the bot3 treasury (star side) exactly
#   like every other bot3 economy flow, via bot3_treasury_adjust. Only after the fee clears
#   does the target actually get added to squads_col.members.
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]invite(?:@\w+)?$', 'bot1')))
async def squad_invite_command(event):
    user_id = event.sender_id
    squad_doc = await get_user_squad(user_id)
    if not squad_doc:
        return await _out(event, "❌ သင်သည် Squad တစ်ခုခုထဲမှာ မပါဝင်သေးပါ။", parse_mode='html')

    if squad_doc.get("leader_id") != user_id:
        return await _out(event, "❌ Squad Leader မှသာ ဖိတ်ကြားနိုင်ပါတယ်။", parse_mode='html')

    members = squad_doc.get("members", [])
    if len(members) >= SQUAD_MAX_MEMBERS:
        return await _out(event, f"❌ Squad ပြည့်နေပါပြီ။ ({SQUAD_MAX_MEMBERS} ယောက်)", parse_mode='html')

    if not event.is_reply:
        return await _out(event, "📌 ဖိတ်ချင်တဲ့ User ကို Reply လုပ်ပြီး <code>/invite</code> ရိုက်ပါ။", parse_mode='html')

    reply_msg = await event.get_reply_message()
    target_id = reply_msg.sender_id if reply_msg else None
    if not target_id:
        return await _out(event, "❌ ဒီ Message ကို ပို့ခဲ့သူကို မတွေ့ပါ။", parse_mode='html')
    if target_id == user_id:
        return await _out(event, "❌ ကိုယ့်ကိုယ်ကို ဖိတ်လို့မရဘူး။", parse_mode='html')
    if target_id in bot_ids:
        return await _out(event, "❌ Bot ကို ဖိတ်လို့မရဘူး။", parse_mode='html')

    target_squad = await get_user_squad(target_id)
    if target_squad:
        return await _out(event, "❌ ဒီ User က Squad တစ်ခုထဲမှာ ရှိပြီးသားပါ။", parse_mode='html')

    target_mention = await get_html_mention(event, target_id)
    leader_mention = await get_html_mention(event, user_id)
    squad_name = escape_html(squad_doc.get('name', ''))
    await _out(
        event,
        f"🏰 {leader_mention} က {target_mention} ကို Squad <code>{squad_name}</code> ထဲ ဖိတ်ကြားလိုက်ပါပြီ။\n"
        f"⏳ <b>{target_mention} ရဲ့ အတည်ပြုချက်ကို စောင့်နေပါတယ်။</b>",
        parse_mode='html'
    )

    buttons = [[
        colored_button(f"✅ ဝင်မယ် ({SQUAD_INVITE_FEE:,}⭐ ပေးမယ်)", f"sqinv_ok_{squad_doc['squad_id']}_{user_id}_{target_id}", color="success"),
        Button.inline("❌ ငြင်းမယ်", data=f"sqinv_no_{squad_doc['squad_id']}_{user_id}_{target_id}")
    ]]
    try:
        await bot1.send_message(
            target_id,
            f"🏰 {leader_mention} က မင်းကို Squad <code>{squad_name}</code> ထဲ ဖိတ်ကြားလိုက်ပါပြီ။\n"
            f"💵 <b>ဝင်ကြေး −</b> <code>{SQUAD_INVITE_FEE:,}⭐</code>\n"
            f"<i>(Leader ကို {SQUAD_INVITE_LEADER_CUT:,}⭐ ရောက်ပါမယ်)</i>\n\n"
            f"လက်ခံပြီး Squad ထဲဝင်မလား?",
            parse_mode='html', buttons=buttons
        )
    except Exception:
        await _out(event, "⚠️ ဖိတ်စာ ပို့လို့မရပါ — User က Bot ကို Block ထားနိုင်ပါတယ်။", parse_mode='html')

@bot1.on(events.CallbackQuery(pattern=r'^sqinv_(ok|no)_([a-zA-Z0-9_]+)_(\d+)_(\d+)$'))
async def squad_invite_response_callback(event):
    action = event.pattern_match.group(1)
    if isinstance(action, bytes):
        action = action.decode('utf-8')
    squad_id = event.pattern_match.group(2)
    if isinstance(squad_id, bytes):
        squad_id = squad_id.decode('utf-8')
    leader_id = int(event.pattern_match.group(3))
    target_id = int(event.pattern_match.group(4))

    if event.sender_id != target_id:
        return await event.answer("⚠️ ဒီဖိတ်စာက မင်းအတွက်မဟုတ်ဘူး။", alert=True)

    if not claim_single_tap(event):
        return await event.answer("⏳ ခဏစောင့်ပါ...", alert=False)

    if action == "no":
        try:
            await event.edit("❌ <b>ဖိတ်စာကို ငြင်းလိုက်ပါပြီ။</b>", parse_mode='html', buttons=None)
        except errors.MessageNotModifiedError:
            pass
        return await event.answer()

    squad_doc = await squads_col.find_one({"squad_id": squad_id})
    if not squad_doc or squad_doc.get("leader_id") != leader_id:
        return await event.edit("❌ <b>Squad ဒါမှမဟုတ် Leader ပြောင်းလဲသွားပါပြီ — ဖိတ်စာ သက်တမ်းကုန်သွားပါပြီ။</b>", parse_mode='html', buttons=None)

    existing_squad = await get_user_squad(target_id)
    if existing_squad:
        return await event.edit("❌ <b>မင်း Squad တစ်ခုခုထဲမှာ ရှိနေပြီးသားပါ။</b>", parse_mode='html', buttons=None)

    if len(squad_doc.get("members", [])) >= SQUAD_MAX_MEMBERS:
        return await event.edit(f"❌ <b>Squad ပြည့်နေပါပြီ။</b> ({SQUAD_MAX_MEMBERS} ယောက်)", parse_mode='html', buttons=None)

    if not await try_deduct_star(target_id, SQUAD_INVITE_FEE):
        return await event.answer(f"❌ Star Balance မလုံလောက်ပါ။ {SQUAD_INVITE_FEE:,}⭐ လိုအပ်ပါတယ်။", alert=True)

    # Same atomic slot-guard as before — now happens at accept-time instead of invite-time.
    result = await squads_col.update_one(
        {"squad_id": squad_id, f"members.{SQUAD_MAX_MEMBERS - 1}": {"$exists": False}},
        {"$addToSet": {"members": target_id}}
    )
    if result.modified_count == 0:
        # Squad filled up in the gap between the checks above and this write — refund in full.
        await users_catcher_col.update_one({"user_id": target_id}, {"$inc": {"star_balance": SQUAD_INVITE_FEE}})
        return await event.edit(f"❌ <b>Squad ပြည့်သွားပါပြီ — ဝင်ကြေး ပြန်အမ်းပေးလိုက်ပါပြီ။</b>", parse_mode='html', buttons=None)

    remainder = SQUAD_INVITE_FEE - SQUAD_INVITE_LEADER_CUT
    await users_catcher_col.update_one({"user_id": leader_id}, {"$inc": {"star_balance": SQUAD_INVITE_LEADER_CUT}}, upsert=True)
    await bot3_treasury_adjust(star=remainder)

    target_mention = await get_html_mention(event, target_id)
    squad_name = escape_html(squad_doc.get('name', ''))
    await event.edit(
        f"✅ <b>{target_mention} Squad <code>{squad_name}</code> ထဲ ဝင်ရောက်လိုက်ပါပြီ!</b>\n"
        f"💸 ပေးဆောင် − <code>{SQUAD_INVITE_FEE:,}⭐</code>",
        parse_mode='html', buttons=None
    )
    await event.answer("🎉 Squad ထဲဝင်ရောက်ပါပြီ!")
    try:
        leader_mention = await get_html_mention(event, leader_id)
        await bot1.send_message(
            leader_id,
            f"🎉 {target_mention} က Squad <code>{squad_name}</code> ထဲ ဝင်ရောက်လိုက်ပါပြီ!\n"
            f"⭐ <b>ရရှိ −</b> <code>+{SQUAD_INVITE_LEADER_CUT:,}⭐</code>",
            parse_mode='html'
        )
    except Exception:
        pass


# ==========================================
# 📋 5. Squad အားလုံးစာရင်း (/squads သို့မဟုတ် /topsquads)
# ==========================================
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.](squads|topsquads)(?:@\w+)?$', 'bot1')))
async def squad_list_command(event):
    # Squad creation is expensive (SQUAD_CREATION_COST Star each), so this list is never going
    # to be huge in practice — but this cap keeps a busy server from ever having to fully
    # recompute points for an unbounded number of squads on a single /squads call.
    all_squads = await squads_col.find({}).sort("created_at", -1).limit(200).to_list(length=None)
    if not all_squads:
        return await _out(event, "📭 Squad မရှိသေးပါ။ <code>/create</code> နဲ့ ပထမဆုံး Squad တည်ထောင်လိုက်ပါ။", parse_mode='html')

    viewer_squad = await get_user_squad(event.sender_id)
    viewer_squad_id = viewer_squad.get("squad_id") if viewer_squad else None

    squad_summaries = []
    for sq in all_squads:
        members = sq.get("members", [])
        pts, _, member_list = await _compute_squad_points(members)
        leader_entry = next((m for m in member_list if m["user_id"] == sq.get("leader_id")), None)
        leader_name = clean_display_name(leader_entry["fullname"]) if leader_entry else f"User {sq.get('leader_id')}"
        squad_summaries.append({
            "name": sq.get("name", "Unnamed"),
            "squad_id": sq.get("squad_id"),
            "members": len(members),
            "points": pts,
            "leader_name": leader_name,
        })

    squad_summaries.sort(key=lambda x: x["points"], reverse=True)
    top10 = squad_summaries[:10]

    lines = []
    viewer_in_top10 = False
    for idx, sq in enumerate(top10, start=1):
        medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else f"#{idx}"
        you_tag = ""
        if sq["squad_id"] == viewer_squad_id:
            you_tag = " 👈 <i>(Your Squad)</i>"
            viewer_in_top10 = True
        lines.append(
            f"{medal} <b>{escape_html(sq['name'])}</b>{you_tag}\n"
            f"   └─ 👑 {escape_html(sq['leader_name'])} | 👥 <code>{sq['members']}/{SQUAD_MAX_MEMBERS}</code> | ⭐ <code>{sq['points']:,}</code>"
        )

    footer_note = ""
    if viewer_squad_id and not viewer_in_top10:
        for idx, sq in enumerate(squad_summaries, start=1):
            if sq["squad_id"] == viewer_squad_id:
                footer_note = f"\n\n📍 <i>Your Squad (</i><b>{escape_html(sq['name'])}</b><i>) is ranked</i> <code>#{idx}</code>"
                break

    text = (
        f"🏆 <b>TOP 10 SQUADS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        + "\n".join(lines) +
        f"\n━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Points = Catches + Gifts + Buys (စုစုပေါင်း)</i>"
        + footer_note
    )
    await _out(event, text, parse_mode='html')


# ==========================================
# 💍 MARRIAGE SYSTEM (အိမ်ထောင်ရေး) — owner request, 2026-08. Two users can /propose to each
# other; once Accepted, they're "married" — a public bond shown on /marriage, a small free ⭐
# perk shared through /daily, a duration-based Rank ladder (MARRIAGE_RANKS, same shape as
# TRIVIA_RANKS), and a /topcouples leaderboard for social visibility. Same "loop + leaderboard +
# reward" shape that made Trivia land well, applied to a relationship layer instead of a quiz.
# ==========================================
MARRIAGE_PROPOSAL_COST = 5000       # 🩹 placeholder — tune freely. Charged to the PROPOSER only
                                     # once the target actually taps Accept — a Decline or a
                                     # silent timeout never costs the proposer anything.
MARRIAGE_DAILY_BONUS = 50           # 🩹 placeholder. Free ⭐ your SPOUSE receives (no action
                                     # needed from them) every time YOU successfully claim /daily.
MARRIAGE_PROPOSAL_TIMEOUT = 300     # 5 minutes to Accept/Decline before a proposal auto-expires.
MARRIAGE_REMARRY_COOLDOWN = 172800  # 48h after a /divorce before either ex-spouse can send OR
                                     # accept another proposal — closes the "instantly remarry
                                     # someone else right after splitting up" loophole.

MARRIAGE_RANKS = [
    (0,   "🌱 Newlyweds"),
    (7,   "💐 Sweethearts"),
    (30,  "💞 Soulmates"),
    (90,  "💍 Devoted Partners"),
    (180, "👑 Eternal Bond"),
]

def get_marriage_rank_info(days_married):
    """Same shape as get_trivia_rank_info() — (rank_number 1-based, rank_name, this_rank's
    floor_days, next_rank's_floor_days_or_None)."""
    idx = 0
    for i, (threshold, _name) in enumerate(MARRIAGE_RANKS):
        if days_married >= threshold:
            idx = i
    floor_days, rank_name = MARRIAGE_RANKS[idx]
    next_days = MARRIAGE_RANKS[idx + 1][0] if idx + 1 < len(MARRIAGE_RANKS) else None
    return idx + 1, rank_name, floor_days, next_days

async def get_spouse_doc(user_id):
    """The OTHER user's full doc if user_id is CURRENTLY, MUTUALLY married, else None. Always
    re-reads both sides fresh and requires them to agree with each other — no Mongo
    transactions are used when marrying/divorcing (two separate update_one calls), so a crash
    landing between those two writes could leave a one-sided spouse_id behind. Requiring
    mutual agreement here means a broken half-marriage displays as "not married" instead of
    a fake bond, on the theory that under-claiming is safer than over-claiming a relationship."""
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    spouse_id = (user_doc or {}).get("spouse_id")
    if not spouse_id:
        return None
    spouse_doc = await users_catcher_col.find_one({"user_id": spouse_id})
    if not spouse_doc or spouse_doc.get("spouse_id") != user_id:
        return None
    return spouse_doc

async def get_top_couples(limit=20):
    """Every mutually-married pair, deduplicated by keeping only the (lower_id, higher_id)
    ordering once per couple (every married pair has exactly one side where user_id < spouse_id
    — arbitrary but stable tie-break), sorted oldest-married first (longest together = 'top')."""
    married_docs = await users_catcher_col.find(
        {"spouse_id": {"$ne": None, "$exists": True}},
        {"user_id": 1, "spouse_id": 1, "married_at": 1, "fullname": 1}
    ).to_list(length=None)
    by_id = {d["user_id"]: d for d in married_docs}
    couples = []
    for d in married_docs:
        uid, sid = d["user_id"], d.get("spouse_id")
        if not sid or sid not in by_id or by_id[sid].get("spouse_id") != uid:
            continue  # one-sided/broken record — see get_spouse_doc's same defensive logic
        if uid < sid:  # count each couple exactly once
            couples.append((d, by_id[sid]))
    couples.sort(key=lambda pair: pair[0].get("married_at", time.time()))
    return couples[:limit]

COUPLES_LB_PAGE_SIZE = 10
COUPLES_LB_TOTAL = 20  # same 2-page-of-10 shape as the Trivia Top 20 leaderboard above

async def render_couples_leaderboard_page(page):
    all_couples = await get_top_couples(COUPLES_LB_TOTAL)
    total = len(all_couples)
    start = page * COUPLES_LB_PAGE_SIZE
    page_rows = all_couples[start:start + COUPLES_LB_PAGE_SIZE]
    if not page_rows:
        body = ("😶 <i>အိမ်ထောင်ရှင် အတွဲ မရှိသေးပါ — </i><code>/propose</code><i> နဲ့ ပထမဆုံး အတွဲ ဖြစ်ကြည့်ပါ!</i>"
                if page == 0 else "<i>No more entries.</i>")
    else:
        lines = []
        now = time.time()
        for i, (a, b) in enumerate(page_rows):
            rank = start + i + 1
            a_name = escape_html(clean_display_name(a.get("fullname"), fallback=f"Agent {a['user_id']}"))
            b_name = escape_html(clean_display_name(b.get("fullname"), fallback=f"Agent {b['user_id']}"))
            a_mention = f"<a href='tg://user?id={a['user_id']}'>{a_name}</a>"
            b_mention = f"<a href='tg://user?id={b['user_id']}'>{b_name}</a>"
            days = int((now - a.get("married_at", now)) // 86400)
            _, rank_name, _, _ = get_marriage_rank_info(days)
            lines.append(f"<b>{rank}.</b> {a_mention} 💍 {b_mention} — {rank_name} (<code>{days}d</code>)")
        body = "\n".join(lines)
    text = "💞 <b>TOP COUPLES</b>\n\n" + body

    nav_row = []
    if page > 0:
        nav_row.append(Button.inline("◀ Previous", data=f"couples_lb_pg_{page - 1}"))
    if start + COUPLES_LB_PAGE_SIZE < total:
        nav_row.append(Button.inline("Next ▶", data=f"couples_lb_pg_{page + 1}"))
    buttons = [nav_row] if nav_row else []
    return text, buttons

@bot1.on(events.CallbackQuery(pattern=r'^couples_lb_pg_(\d+)$'))
async def couples_leaderboard_page_callback(event):
    page = int(event.pattern_match.group(1))
    text, buttons = await render_couples_leaderboard_page(page)
    try:
        await event.edit(text, parse_mode='html', buttons=buttons)
        await event.answer()
    except errors.MessageNotModifiedError:
        await event.answer()
    except Exception as e:
        await event.answer(f"❌ Error: {e}", alert=True)

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]topcouples(?:@\w+)?$', 'bot1')))
async def topcouples_command(event):
    text, buttons = await render_couples_leaderboard_page(0)
    await _out(event, text, parse_mode='html', buttons=buttons)

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]propose(?:@\w+)?$', 'bot1')))
async def marriage_propose_handler(event):
    proposer_id = event.sender_id
    if not event.is_reply:
        return await _out(event, "💍 <b>လက်ထပ်ဖို့ တင်ပြချင်တဲ့သူကို Reply လုပ်ပြီး</b> <code>/propose</code> <b>ကို ရိုက်ပါ။</b>", parse_mode='html')
    replied = await event.get_reply_message()
    target_id = replied.sender_id if replied else None
    if target_id is None:
        return await _out(event, "❌ ဒီစာကို ဘယ်သူပို့လိုက်တာလဲဆိုတာ မသိနိုင်ပါ။", parse_mode='html')
    if target_id == proposer_id:
        return await _out(event, "❌ ကိုယ့်ကိုယ်ကို လက်ထပ်လို့ မရပါ 😅", parse_mode='html')
    target_sender = await replied.get_sender()
    if getattr(target_sender, "bot", False):
        return await _out(event, "❌ Bot တစ်ခုကို လက်ထပ်ခွင့် မပြုပါ။", parse_mode='html')

    plain_name = await get_plain_name(event, proposer_id)
    await ensure_user_registered(proposer_id, plain_name)
    proposer_doc = await users_catcher_col.find_one({"user_id": proposer_id})
    if (proposer_doc or {}).get("spouse_id"):
        return await _out(event, "❌ <b>မင်းက အိမ်ထောင်ရှိပြီးသားပါ။</b> အရင် <code>/divorce</code> လုပ်ပါ။", parse_mode='html')

    now_ts = time.time()
    last_divorced = (proposer_doc or {}).get("last_divorced_at", 0)
    if now_ts - last_divorced < MARRIAGE_REMARRY_COOLDOWN:
        remaining = int(MARRIAGE_REMARRY_COOLDOWN - (now_ts - last_divorced))
        return await _out(event, f"⏳ <b>Divorce လုပ်ပြီးစချင်းမို့ {str(timedelta(seconds=remaining))} လောက် စောင့်ပါဦး</b> — ပြန်လက်ထပ်ခွင့် မရသေးပါ။", parse_mode='html')

    target_doc = await users_catcher_col.find_one({"user_id": target_id})
    if (target_doc or {}).get("spouse_id"):
        return await _out(event, "❌ <b>အဲ့သူက အိမ်ထောင်ရှိပြီးသားပါ။</b>", parse_mode='html')
    target_last_divorced = (target_doc or {}).get("last_divorced_at", 0)
    if now_ts - target_last_divorced < MARRIAGE_REMARRY_COOLDOWN:
        return await _out(event, "⏳ <b>အဲ့သူက Divorce လုပ်ပြီးစချင်းမို့ ခဏစောင့်ပေးပါ။</b>", parse_mode='html')

    if target_id in pending_marriage_proposals:
        return await _out(event, "⏳ <b>အဲ့သူဆီ proposal တစ်ခု pending ရှိနေပြီးသားပါ။</b> ခဏစောင့်ပါ။", parse_mode='html')

    proposer_balance = (proposer_doc or {}).get("star_balance", 0)
    if proposer_balance < MARRIAGE_PROPOSAL_COST:
        return await _out(event, f"❌ <b>Proposal Accept ခံရင် {format_star_plain(MARRIAGE_PROPOSAL_COST)} နှုတ်ပါမယ်</b> — လက်ရှိလက်ကျန် {format_star_plain(proposer_balance)} ပဲ ရှိပါတယ်။", parse_mode='html')

    proposer_mention = await get_html_mention(event, proposer_id)
    target_mention = await get_html_mention(event, target_id)

    proposal_entry = {"from_id": proposer_id, "chat_id": event.chat_id, "sent_at": now_ts}
    pending_marriage_proposals[target_id] = proposal_entry

    msg = await event.reply(
        f"💍 <b>Marriage Proposal!</b>\n\n"
        f"{proposer_mention} က {target_mention} ကို လက်ထပ်ဖို့ တင်ပြနေပါတယ်! 💕\n\n"
        f"<i>{target_mention} ပဲ Accept/Decline နှိပ်နိုင်ပါတယ် — {MARRIAGE_PROPOSAL_TIMEOUT // 60} မိနစ်အတွင်း မဖြေရင် proposal ပျက်သွားပါမယ်။</i>",
        parse_mode='html',
        buttons=[[
            colored_button("💍 Accept", f"marry_accept_{proposer_id}", color="success"),
            Button.inline("💔 Decline", data=f"marry_decline_{proposer_id}"),
        ]]
    )
    proposal_entry["msg_id"] = msg.id

    async def _expire_marriage_proposal():
        await asyncio.sleep(MARRIAGE_PROPOSAL_TIMEOUT)
        if pending_marriage_proposals.get(target_id) is proposal_entry:
            del pending_marriage_proposals[target_id]
            try:
                await bot1.edit_message(proposal_entry["chat_id"], proposal_entry["msg_id"],
                                         "💔 <i>Proposal timed out — ဖြေဆိုမှု မရှိတော့ပါ။</i>", parse_mode='html', buttons=None)
            except Exception:
                pass
    asyncio.create_task(_expire_marriage_proposal())

@bot1.on(events.CallbackQuery(pattern=r'^marry_(accept|decline)_(-?\d+)$'))
async def marriage_response_callback(event):
    action = event.pattern_match.group(1)
    proposer_id = int(event.pattern_match.group(2))
    target_id = event.sender_id

    entry = pending_marriage_proposals.get(target_id)
    if not entry or entry["from_id"] != proposer_id:
        return await event.answer("❌ ဒီ Proposal ရဲ့ သက်တမ်း ကုန်သွားပါပြီ (or invalid)။", alert=True)

    if action == "decline":
        del pending_marriage_proposals[target_id]
        try:
            await event.edit("💔 <i>Proposal ကို ငြင်းပယ်လိုက်ပါပြီ။</i>", parse_mode='html', buttons=None)
        except Exception:
            pass
        try:
            await send_safe_message(bot1, proposer_id, "💔 သင့် Marriage Proposal ကို ငြင်းပယ်ခံခဲ့ရပါတယ်။")
        except Exception:
            pass
        return await event.answer()

    # action == "accept" — re-validate everything fresh. A lot could've changed since the
    # proposal was sent: either side could've married someone else, gone broke, or started a
    # remarry cooldown in the meantime.
    plain_name = await get_plain_name(event, target_id)
    await ensure_user_registered(target_id, plain_name)
    proposer_doc = await users_catcher_col.find_one({"user_id": proposer_id})
    target_doc = await users_catcher_col.find_one({"user_id": target_id})
    if not proposer_doc or proposer_doc.get("spouse_id"):
        del pending_marriage_proposals[target_id]
        try:
            await event.edit("❌ <i>Proposer က အခြားသူနဲ့ အိမ်ထောင်ပြုသွားပြီ ဖြစ်နိုင်ပါတယ် — proposal ပျက်သွားပါပြီ။</i>", parse_mode='html', buttons=None)
        except Exception:
            pass
        return await event.answer()
    if target_doc and target_doc.get("spouse_id"):
        del pending_marriage_proposals[target_id]
        return await event.answer("❌ မင်း အိမ်ထောင်ရှိနေပြီ ဖြစ်နိုင်ပါတယ်။", alert=True)

    if proposer_doc.get("star_balance", 0) < MARRIAGE_PROPOSAL_COST:
        del pending_marriage_proposals[target_id]
        try:
            await event.edit(f"❌ <i>Proposer မှာ {format_star_plain(MARRIAGE_PROPOSAL_COST)} မလုံလောက်တော့ပါ — proposal ပျက်သွားပါပြီ။</i>", parse_mode='html', buttons=None)
        except Exception:
            pass
        return await event.answer()

    if not await try_deduct_balance(proposer_id, MARRIAGE_PROPOSAL_COST):
        del pending_marriage_proposals[target_id]
        return await event.answer("❌ ငွေနှုတ်ယူ၍ မရပါ — ခဏနောက်မှ ပြန်ကြိုးစားပါ။", alert=True)

    now_ts = time.time()
    await users_catcher_col.update_one({"user_id": proposer_id}, {"$set": {"spouse_id": target_id, "married_at": now_ts}})
    await users_catcher_col.update_one({"user_id": target_id}, {"$set": {"spouse_id": proposer_id, "married_at": now_ts, "fullname": plain_name}}, upsert=True)
    del pending_marriage_proposals[target_id]

    proposer_mention = await get_html_mention(event, proposer_id)
    target_mention = await get_html_mention(event, target_id)
    announce = (
        f"💒 <b>CONGRATULATIONS!</b> 💒\n\n"
        f"{proposer_mention} 💍 {target_mention}\n\n"
        f"<i>တစ်ဦးကိုတစ်ဦး ကတိသစ္စာနဲ့ လက်ထပ်လိုက်ကြပါပြီ! 🎉</i>"
    )
    try:
        await event.edit(announce, parse_mode='html', buttons=None)
    except Exception:
        await event.respond(announce, parse_mode='html')
    try:
        await send_safe_message(bot1, proposer_id, f"💒 {target_mention} က သင့် Proposal ကို လက်ခံလိုက်ပါပြီ! Congratulations! 🎉", parse_mode='html')
    except Exception:
        pass
    await event.answer("💍 Married! 🎉")

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]divorce(?:@\w+)?$', 'bot1')))
async def marriage_divorce_handler(event):
    user_id = event.sender_id
    spouse_doc = await get_spouse_doc(user_id)
    if not spouse_doc:
        return await _out(event, "❌ <b>မင်း အိမ်ထောင် မရှိသေးပါ။</b>", parse_mode='html')
    spouse_id = spouse_doc["user_id"]
    spouse_mention = await get_html_mention(event, spouse_id)
    cooldown_hours = int(MARRIAGE_REMARRY_COOLDOWN // 3600)
    await event.reply(
        f"💔 <b>{spouse_mention} နဲ့ တကယ် Divorce လုပ်မှာ သေချာပါသလား?</b>\n"
        f"<i>ပြန်လက်ထပ်ချင်ရင် {cooldown_hours} နာရီ စောင့်ရပါမယ်။</i>",
        parse_mode='html',
        buttons=[[
            colored_button("💔 Yes, Divorce", f"divorce_confirm_{spouse_id}", color="danger"),
            Button.inline("❌ Cancel", data="divorce_cancel"),
        ]]
    )

@bot1.on(events.CallbackQuery(pattern=r'^divorce_confirm_(-?\d+)$'))
async def marriage_divorce_confirm_callback(event):
    user_id = event.sender_id
    spouse_id = int(event.pattern_match.group(1))
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    if not user_doc or user_doc.get("spouse_id") != spouse_id:
        return await event.answer("❌ မင်း ဒီလူနဲ့ အိမ်ထောင် မရှိတော့ပါ။", alert=True)
    now_ts = time.time()
    await users_catcher_col.update_one({"user_id": user_id}, {"$set": {"spouse_id": None, "last_divorced_at": now_ts}})
    await users_catcher_col.update_one({"user_id": spouse_id}, {"$set": {"spouse_id": None, "last_divorced_at": now_ts}})
    mention = await get_html_mention(event, user_id)
    spouse_mention = await get_html_mention(event, spouse_id)
    try:
        await event.edit(f"💔 <b>{mention} နဲ့ {spouse_mention} ကွာရှင်းလိုက်ကြပါပြီ။</b>", parse_mode='html', buttons=None)
    except Exception:
        pass
    try:
        await send_safe_message(bot1, spouse_id, f"💔 {mention} က သင်နဲ့ Divorce လုပ်လိုက်ပါပြီ။", parse_mode='html')
    except Exception:
        pass
    await event.answer()

@bot1.on(events.CallbackQuery(pattern=r'^divorce_cancel$'))
async def marriage_divorce_cancel_callback(event):
    try:
        await event.edit("✅ <i>Divorce ကို ပယ်ဖျက်လိုက်ပါပြီ။</i>", parse_mode='html', buttons=None)
    except Exception:
        pass
    await event.answer()

@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.](marriage|spouse)(?:@\w+)?$', 'bot1')))
async def marriage_status_handler(event):
    user_id = event.sender_id
    mention = await get_html_mention(event, user_id)
    spouse_doc = await get_spouse_doc(user_id)
    if not spouse_doc:
        return await event.reply(
            f"💍 {mention} <b>အိမ်ထောင် မရှိသေးပါ။</b>\n\n"
            f"<i>လက်ထပ်ချင်တဲ့သူကို Reply လုပ်ပြီး</i> <code>/propose</code> <i>ကို စမ်းကြည့်ပါ!</i>",
            parse_mode='html',
            buttons=[[Button.inline("💞 Top Couples", data="couples_lb_pg_0")]]
        )
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    married_at = (user_doc or {}).get("married_at", time.time())
    days_married = int((time.time() - married_at) // 86400)
    rank_idx, rank_name, floor_days, next_days = get_marriage_rank_info(days_married)
    bar = build_trivia_progress_bar(days_married, floor_days, next_days)
    progress_line = (
        f"{bar}  <code>{days_married} days (MAX 👑)</code>" if next_days is None
        else f"{bar}  <code>{days_married}/{next_days} days</code>"
    )
    spouse_id = spouse_doc["user_id"]
    spouse_name = escape_html(clean_display_name(spouse_doc.get("fullname"), fallback=f"Agent {spouse_id}"))
    spouse_mention = f"<a href='tg://user?id={spouse_id}'>{spouse_name}</a>"
    await event.reply(
        f"💍 <b>{mention}'s Marriage</b>\n\n"
        f"💞 <b>Spouse:</b> {spouse_mention}\n"
        f"🏅 <b>Rank {rank_idx}/{len(MARRIAGE_RANKS)}:</b> {rank_name}\n"
        f"{progress_line}",
        parse_mode='html',
        buttons=[[Button.inline("💞 Top Couples", data="couples_lb_pg_0")]]
    )

# ==========================================
# 💰 /fixbalance — Owner: လူတိုင်းရဲ့ လက်ကျန်ငွေကို Star 500 နဲ့ VLT 50 ပြန်သတ်မှတ်မယ် (Owner မပါ)
# ==========================================
# 🩹 2026-08 USD RETIREMENT: this used to reset wallet_balance (USD) + star_balance. USD is
# gone, so it now resets star_balance + the new vlt_balance instead.
@bot1.on(events.NewMessage(pattern=own_pattern(r'^[/.]fixbalance(?:@\w+)?$', 'bot1')))
async def owner_fix_all_balances(event):
    if event.sender_id != OWNER_ID:
        return

    status_msg = await event.reply("⏳ <b>လူတိုင်းရဲ့ လက်ကျန်ငွေကို Star 500 နဲ့ VLT 50 ပြန်သတ်မှတ်နေပါပြီ (Owner မပါ)...</b>", parse_mode='html')
    
    try:
        # Owner ကိုချန်လှပ်ပြီး ကျန်သူအားလုံးကို update လုပ်မယ်
        result = await users_catcher_col.update_many(
            {"user_id": {"$ne": OWNER_ID}},  # Owner မဟုတ်သူများ
            {"$set": {"star_balance": 500.00, "vlt_balance": 50.00}}
        )
        
        await status_msg.edit(
            f"✅ <b>Balance Reset Complete!</b>\n"
            f"👥 <b>Users updated:</b> <code>{result.modified_count}</code>\n"
            f"⭐ <b>New Star:</b> <code>500 ⭐</code>\n"
            f"💠 <b>New VLT:</b> <code>50 💠</code>\n\n"
            f"<i>(Owner အကောင့်ကို ထိခိုက်မှုမရှိစေရ ချန်လှပ်ထားပါသည်။)</i>",
            parse_mode='html'
        )
    except Exception as e:
        await status_msg.edit(f"❌ Error: <code>{escape_html(str(e))}</code>", parse_mode='html')
# ==========================================
# 🚨 ONE-TIME MIGRATION — USD → ⭐ Star retirement (2026-08). Runs automatically, once, the
# very next time bot1 starts (see the call inside run_bot1_forever() just below) — no owner
# command needed, matching the "bot restarts once and USD disappears" request. Safe to leave
# in permanently: the bot_settings_col flag doc means it only ever actually does work once,
# no matter how many times the bot restarts after that.
# ==========================================
async def run_usd_to_star_migration():
    """Converts every user's leftover USD wallet_balance into ⭐ Star at USD_PER_STAR
    (100,000 USD = 1⭐), then removes wallet_balance entirely. After this runs once, USD no
    longer exists anywhere in this bot — every economy flow from this point on runs on
    star_balance (see USD → STAR RETIREMENT near MMK_PER_USD). Idempotent — checks a flag doc
    in bot_settings_col first and does nothing if it already ran."""
    flag = await bot_settings_col.find_one({"_id": "usd_to_star_migration"})
    if flag and flag.get("done"):
        return  # already ran — no-op on every subsequent restart
    print("💵→⭐ Running one-time USD → Star migration...")
    converted_users = 0
    total_usd_converted = 0.0
    ops = []
    cursor = users_catcher_col.find({"wallet_balance": {"$gt": 0}}, {"_id": 1, "wallet_balance": 1})
    async for doc in cursor:
        usd_amount = doc.get("wallet_balance", 0) or 0
        if usd_amount <= 0:
            continue
        star_amount = round(usd_amount / USD_PER_STAR, 2)
        ops.append(UpdateOne(
            {"_id": doc["_id"]},
            {"$inc": {"star_balance": star_amount}, "$unset": {"wallet_balance": ""}}
        ))
        converted_users += 1
        total_usd_converted += usd_amount
        if len(ops) >= 500:
            await users_catcher_col.bulk_write(ops)
            ops = []
    if ops:
        await users_catcher_col.bulk_write(ops)
    # Anyone with wallet_balance present but not > 0 (missing/zero/negative) never touched the
    # loop above — clear the field for them too, so nothing stale is left in the DB.
    await users_catcher_col.update_many({"wallet_balance": {"$exists": True}}, {"$unset": {"wallet_balance": ""}})
    # bot3's own treasury doc used to carry a wallet_balance/seed_wallet_balance half too (see
    # get_or_create_bot3_treasury(), already Star-only in code) — clean up any leftover fields.
    await bot3_treasury_col.update_many(
        {"$or": [{"wallet_balance": {"$exists": True}}, {"seed_wallet_balance": {"$exists": True}}]},
        {"$unset": {"wallet_balance": "", "seed_wallet_balance": ""}}
    )
    await bot_settings_col.update_one(
        {"_id": "usd_to_star_migration"},
        {"$set": {"done": True, "completed_at": time.time(), "converted_users": converted_users,
                  "total_usd_converted": total_usd_converted, "rate_used": USD_PER_STAR}},
        upsert=True
    )
    print(f"✅ USD → Star migration complete: {converted_users} users, "
          f"{total_usd_converted:,.2f} USD converted at {USD_PER_STAR:,}:1 into Star.")
    try:
        await bot1.send_message(OWNER_ID, (
            f"💵→⭐ <b>USD → Star migration complete.</b>\n"
            f"👥 <b>Users converted:</b> <code>{converted_users}</code>\n"
            f"💰 <b>Total USD converted:</b> <code>{total_usd_converted:,.2f}</code>\n"
            f"📐 <b>Rate used:</b> <code>{USD_PER_STAR:,} USD = 1⭐</code>\n\n"
            f"USD no longer exists in this bot. /buystar, /sellstar and /setstarrate are "
            f"retired — 💠 VLT (via /buyvlt, /sellvlt) is the new exchange partner for ⭐ Star, "
            f"and the Market (/buy, /sell) now runs on VLT only."
        ), parse_mode='html')
    except Exception:
        pass

# ==========================================
# 🚨 ONE-TIME MIGRATION — Player Market retirement (2026-08). p2p card trading (/list, /unlist,
# /mylistings, the /market "Player Market" button, and the mktbuy_ purchase callback) has been
# removed. Runs automatically once, the next time bot1 starts — no owner command needed. Any
# card still sitting in marketplace_data (i.e. still actively listed) gets handed straight back
# to its seller's vault so nobody's card is silently stranded outside their harem forever with
# no /unlist left to reach it. Idempotent — checks a flag doc first, safe to leave in permanently.
# ==========================================
async def run_player_market_retirement():
    """Drains marketplace_data: every remaining listing's card is reverted from "market" back
    to "vault" status on the seller's harem (see set_one_harem_copy_status), then the listing
    document itself is deleted. After this runs once, marketplace_data is always empty and
    Player Market/p2p trading no longer exists anywhere in this bot."""
    flag = await bot_settings_col.find_one({"_id": "player_market_retirement"})
    if flag and flag.get("done"):
        return  # already ran — no-op on every subsequent restart
    print("🏪→🎒 Running one-time Player Market retirement migration...")
    reverted, orphaned = 0, 0
    async for listing in marketplace_col.find():
        ok = await set_one_harem_copy_status(listing["seller_id"], listing["char_id"], "market", "vault")
        if ok:
            reverted += 1
        else:
            # Copy wasn't marked "market" anymore for some reason — don't lose track of it
            # silently; still remove the now-meaningless listing doc below.
            orphaned += 1
        await marketplace_col.delete_one({"_id": listing["_id"]})
    await bot_settings_col.update_one(
        {"_id": "player_market_retirement"},
        {"$set": {"done": True, "completed_at": time.time(), "reverted": reverted, "orphaned": orphaned}},
        upsert=True
    )
    print(f"✅ Player Market retirement complete: {reverted} listed card(s) returned to vaults"
          + (f", {orphaned} stale listing doc(s) cleared." if orphaned else "."))
    if reverted or orphaned:
        try:
            await bot1.send_message(OWNER_ID, (
                f"🏪→🎒 <b>Player Market retirement complete.</b>\n"
                f"↩️ <b>Cards returned to vault:</b> <code>{reverted}</code>\n"
                f"🧹 <b>Stale listings cleared:</b> <code>{orphaned}</code>\n\n"
                f"/list, /unlist, /mylistings and the /market 'Player Market' button are gone — "
                f"/sell (scrap) and the Owner Shop (/buy, /show) are the only ways to trade "
                f"cards for 💠 VLT now. /trade (direct card-for-card swap) is unaffected."
            ), parse_mode='html')
        except Exception:
            pass

# ==========================================
# 🚨 ONE-TIME MIGRATION — ULTRA catch limit correction (2026-08). The original ULTRA scarcity
# cap was implemented as a single pool shared across the whole tier — that was a
# misunderstanding of what was wanted; see ULTRA_DEFAULT_CATCH_LIMIT's docstring. This backfills
# every EXISTING ULTRA-tier character that doesn't already have a real spawn_limit of its own
# (i.e. still at the old default of 0/infinite, since the pooled system used to do the capping
# instead) up to ULTRA_DEFAULT_CATCH_LIMIT individually. Any ULTRA character an owner had
# already given an explicit non-zero spawn_limit is left alone — this only fills the gap, it
# never overwrites a deliberate choice. Runs automatically once, the next time bot1 starts.
# ==========================================
async def run_ultra_catch_limit_migration():
    flag = await bot_settings_col.find_one({"_id": "ultra_catch_limit_migration"})
    if flag and flag.get("done"):
        return  # already ran — no-op on every subsequent restart
    print("🎯 Running one-time ULTRA catch limit migration...")
    result = await characters_base_col.update_many(
        {"rarity_tier": "ULTRA", "$or": [{"spawn_limit": {"$exists": False}}, {"spawn_limit": 0}]},
        {"$set": {"spawn_limit": ULTRA_DEFAULT_CATCH_LIMIT}}
    )
    await invalidate_character_caches()
    await bot_settings_col.update_one(
        {"_id": "ultra_catch_limit_migration"},
        {"$set": {"done": True, "completed_at": time.time(), "updated": result.modified_count}},
        upsert=True
    )
    print(f"✅ ULTRA catch limit migration complete: {result.modified_count} character(s) set to {ULTRA_DEFAULT_CATCH_LIMIT}.")
    if result.modified_count:
        try:
            await bot1.send_message(OWNER_ID, (
                f"🎯 <b>ULTRA catch limit migration complete.</b>\n"
                f"🔁 <b>Characters set to {ULTRA_DEFAULT_CATCH_LIMIT} each:</b> <code>{result.modified_count}</code>\n\n"
                f"Each ULTRA card now has its OWN {ULTRA_DEFAULT_CATCH_LIMIT}-copy catch limit "
                f"(spawn + Owner Shop combined) instead of one pool shared by the whole tier. "
                f"Any ULTRA card you'd already given a custom catch limit was left untouched."
            ), parse_mode='html')
        except Exception:
            pass

# ==========================================
# 🚨 ONE-TIME MIGRATION — trivia hard-tier wipe (2026-08, per owner report: HARD/VERY_HARD/
# EXTREME_HARD questions were too obscure — "even actual teachers wouldn't reliably know
# them"). Wipes every trivia_bank_col question in those 3 tiers so the owner can bulk-import
# a fresh, better-calibrated set via /addtriviabulk. EASY and NORMAL are untouched. Runs
# automatically once, the next time bot1 starts.
# ==========================================
TRIVIA_WIPE_TIERS = ["HARD", "VERY_HARD", "EXTREME_HARD"]

async def run_trivia_hard_tier_wipe():
    flag = await bot_settings_col.find_one({"_id": "trivia_hard_tier_wipe"})
    if flag and flag.get("done"):
        return  # already ran — no-op on every subsequent restart
    print("🧠 Running one-time trivia hard-tier wipe...")
    result = await trivia_bank_col.delete_many({"difficulty": {"$in": TRIVIA_WIPE_TIERS}})
    await bot_settings_col.update_one(
        {"_id": "trivia_hard_tier_wipe"},
        {"$set": {"done": True, "completed_at": time.time(), "deleted": result.deleted_count}},
        upsert=True
    )
    print(f"✅ Trivia hard-tier wipe complete: {result.deleted_count} question(s) removed.")
    try:
        await bot1.send_message(OWNER_ID, (
            f"🧠 <b>Trivia hard-tier wipe complete.</b>\n"
            f"🗑️ <b>Questions removed:</b> <code>{result.deleted_count}</code> "
            f"(🟠 Hard, 🔴 Very Hard, 🟣 Extreme Hard — all cleared)\n\n"
            f"Those 3 tiers won't spawn again until you bulk-import new questions into them — "
            f"see <code>/addtriviabulk [difficulty]</code>. 🟢 Easy and 🔵 Normal were untouched."
        ), parse_mode='html')
    except Exception:
        pass

# Up to three bots run in the same process, side by side:
#   bot1 — the public-facing game bot (everything except the owner-only / guard commands)
#   bot2 — owner-only control bot: /addchar, /ktr, /ktrr, /shadow, /unshadow
#   bot3 — Guard Bot, force-join-group only (artist rewards, premium daily Star, game
#          moderation); only starts if GUARD_BOT_TOKEN is set
# Each has its own independent reconnect loop so a disconnect/crash on one never takes
# the others down with it; asyncio.gather runs all loops concurrently forever.
async def run_bot1_forever():
    global BOT1_USERNAME
    while True:
        try:
            print("🚀 Connecting Main Bot (bot1)...")
            await bot1.start(bot_token=MAIN_BOT_TOKEN)
            me_main = await bot1.get_me()
            print(f"✅ Main Bot connected as @{me_main.username}")
            if me_main.username: BOT1_USERNAME = me_main.username.lower()
            if me_main.id not in bot_ids: bot_ids.append(me_main.id)
            await load_rarity_weight_cache()
            await load_disabled_buy_tiers_cache()
            await load_group_spawn_counters_cache()
            await load_trivia_settings_cache()
            await load_active_gbans_cache()
            await run_usd_to_star_migration()  # 💵→⭐ one-time, no-ops after the first successful run
            await run_player_market_retirement()  # 🏪→🎒 one-time, no-ops after the first successful run
            await run_ultra_catch_limit_migration()  # 🎯 one-time, no-ops after the first successful run
            await run_trivia_hard_tier_wipe()  # 🧠 one-time, no-ops after the first successful run
            await ensure_all_le_batches_numbered()  # 🎗️ backfills #numbers for pre-existing LE batches — see its own docstring for why this moved here from create_indexes()
            await notify_owner_of_unemojied_le_batches()  # 🏷️ one-time-per-restart nudge for any LE batch name still missing its emoji — see its own docstring
            asyncio.create_task(ghost_spawn_cleaner())
            # ⭐ Star's old USD rate is now a frozen fixed peg (see [LEGACY] STAR_STARTING_PRICE)
            # — no background drift task for it anymore. 💠 VLT is the one that drifts now.
            asyncio.create_task(vlt_drift_loop())
            asyncio.create_task(group_counter_flush_loop())
            print("📅 Background tasks started.")
            await bot1.run_until_disconnected()
        except Exception as system_fault:
            print(f"⚠️ Main Bot disconnected: {system_fault}")
            print("⏳ Restarting Main Bot in 30 seconds...")
            await asyncio.sleep(30)

async def run_bot2_forever():
    global BOT2_USERNAME
    while True:
        try:
            print("🚀 Connecting Owner Bot (bot2)...")
            await bot2.start(bot_token=OWNER_BOT_TOKEN)
            me_owner = await bot2.get_me()
            print(f"✅ Owner Bot connected as @{me_owner.username}")
            if me_owner.username: BOT2_USERNAME = me_owner.username.lower()
            if me_owner.id not in bot_ids: bot_ids.append(me_owner.id)
            await bot2.run_until_disconnected()
        except Exception as system_fault:
            print(f"⚠️ Owner Bot disconnected: {system_fault}")
            print("⏳ Restarting Owner Bot in 30 seconds...")
            await asyncio.sleep(30)

async def run_bot3_forever():
    """Guard Bot — only runs at all if GUARD_BOT_TOKEN was configured (bot3 is None otherwise).
    Its handlers are registered elsewhere in this file, all scoped to FORCE_SUB_CHAT_ID."""
    global BOT3_USERNAME
    if bot3 is None:
        print("🛡️ Guard Bot: GUARD_BOT_TOKEN not set — skipping, bot1 + bot2 only.")
        return
    while True:
        try:
            print("🚀 Connecting Guard Bot (bot3)...")
            await bot3.start(bot_token=GUARD_BOT_TOKEN)
            me_guard = await bot3.get_me()
            print(f"✅ Guard Bot connected as @{me_guard.username}")
            if me_guard.username: BOT3_USERNAME = me_guard.username.lower()
            if me_guard.id not in bot_ids: bot_ids.append(me_guard.id)
            await bot3.run_until_disconnected()
        except Exception as system_fault:
            print(f"⚠️ Guard Bot disconnected: {system_fault}")
            print("⏳ Restarting Guard Bot in 30 seconds...")
            await asyncio.sleep(30)

async def _create_indexes_background():
    """Runs create_indexes() without blocking bot startup — index creation is a MongoDB round
    trip per index (~35 of them), which used to make bot1/bot2 sit disconnected from Telegram
    for however long that took. Indexes are a pure performance optimization (queries still
    work without one, just slower), so there's no correctness reason to wait on them."""
    try:
        await create_indexes()
    except Exception as e:
        print(f"Index creation error: {e}")

async def start_bot_state_cleanup_loop():
    """Sweeps BotState's expired cache entries every 10 minutes for the life of the process."""
    while True:
        await asyncio.sleep(600)
        try:
            removed = bot_state.cleanup_expired()
            if removed:
                print(f"🧹 BotState cleanup: removed {removed} stale entries")
        except Exception as e:
            print(f"BotState cleanup error: {e}")

async def start_system():
    threading.Thread(target=run_flask, daemon=True).start()
    print("Bot System Starting...")
    asyncio.create_task(_create_indexes_background())
    asyncio.create_task(start_bot_state_cleanup_loop())
    asyncio.create_task(daily_report_scheduler())
    asyncio.create_task(trivia_top10_reward_scheduler())
    await asyncio.gather(run_bot1_forever(), run_bot2_forever(), run_bot3_forever())

if __name__ == "__main__":
    try:
        asyncio.run(start_system())
    except (KeyboardInterrupt, SystemExit):
        print("Bot System Shutting Down.")
        
