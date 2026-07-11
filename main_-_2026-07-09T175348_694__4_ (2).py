#############
import io
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
from html import escape as escape_html
from telethon.errors import FloodWaitError
from telethon import TelegramClient, events, functions, types, Button, errors
from telethon.tl.types import ChannelParticipantsAdmins
from telethon.extensions import html
from telethon.tl.types import MessageEntityCustomEmoji
import redis.asyncio as redis

# ==========================================
# ⚡ PREMIUM MATHEMATICAL BOLD SERIF FONT CONVERTER
# ==========================================
def f(text):
    normal = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    elegant = "𝖠𝖡𝖢𝖣𝖤𝖥𝖦𝖧𝖨𝖩𝖪𝖫𝖬𝖭𝖮𝖯𝖰𝖱𝖲𝖳𝖴𝖵𝖶𝖷𝖸𝖹𝖺𝖻𝖼𝖽𝖾𝖿𝗀𝗁𝗂𝗃𝗄𝗅𝗆𝗇𝗈𝗉𝗊𝗋𝗌𝗍𝗎𝗏𝗐𝗑𝗒𝗓𝟢𝟣𝟤𝟥𝟦𝟧𝟨𝟩𝟪𝟫"
    trans = str.maketrans(normal, elegant)
    return text.translate(trans)

async def ensure_user_registered(user_id, fullname):
    global_system = await groups_config_col.find_one({"chat_id": "global_system"})
    welcome_bonus = global_system.get("default_welcome_bonus", 0) if global_system else 0
    await users_catcher_col.update_one(
        {"user_id": user_id},
        {
            "$setOnInsert": {
                "wallet_balance": welcome_bonus,
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
                "stock_portfolio": [],
                "stock_portfolio_value": 0,
                "btc_balance": 0.0,
                "btc_value_mmk": 0
            }
        },
        upsert=True
    )

# ==========================================
# 🛡️ FLOOD WAIT PROTECTION ENGINE
# ==========================================
async def send_safe_message(client, chat_id, text, **kwargs):
    while True:
        try:
            return await client.send_message(chat_id, text, **kwargs)
        except FloodWaitError as e:
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

# ==========================================
# 🏅 ACHIEVEMENT SYSTEM (Updated)
# ==========================================
RARITY_TIERS = ["LEGENDARY", "LIMITED", "MYTHIC", "EPIC", "RARE", "COMMON"]
# ⚠️ SINGLE SOURCE OF TRUTH for rarity emoji (Rarity No.1 = LEGENDARY ... No.6 = COMMON).
# Change emoji here ONLY — RARITY_NUM_MAP below is generated from this, so spawns, /who,
# the /addchar legend, achievements, stats and /changeallrarity all stay in sync automatically.
RARITY_EMOJI = {"LEGENDARY": "🪎", "LIMITED": "💎", "MYTHIC": "⚱", "EPIC": "🏺", "RARE": "🧺", "COMMON": "🪆"}
RARITY_DEFAULT_EMOJI = "❓"  # fallback shown only when a tier truly can't be classified

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
PHASH_SIZE = 8  # 8x8 -> 64-bit hash

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
    for tier in RARITY_TIERS:
        if tier in plain:
            return tier
    return "OTHER"

def build_progress_bar(current, total, length=10):
    pct = (current / total) if total > 0 else 0
    pct = max(0.0, min(pct, 1.0))
    filled = int(round(pct * length))
    return "█" * filled + "░" * (length - filled) + f" {pct * 100:.1f}%"

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
    balance_targets = [1000, 10000, 50000, 100000, 500000, 1000000]
    balance_emojis = ["🪙", "💰", "💵", "💳", "🏦", "💎"]
    for i, target in enumerate(balance_targets):
        achievements.append({
            "id": f"wealth_{target}",
            "emoji": balance_emojis[i],
            "name": f"Hold {target:,} MMK",
            "desc": f"Amass {target:,} MMK in your wallet",
            "check": lambda u, t=target: u.get("wallet_balance", 0) >= t
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
    for i, target in enumerate(leg_targets):
        achievements.append({
            "id": f"legendary_{target}",
            "emoji": leg_emojis[i],
            "name": f"Collect {target} Legendary Cards",
            "desc": f"Own {target} LEGENDARY rarity cards",
            "check": lambda u, t=target: sum(1 for item in u.get("harem", []) if isinstance(item, dict) and "LEGENDARY" in (item.get("rarity", "").upper())) >= t
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
        "desc": "Collect at least one of every rarity (Common to Legendary)",
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
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

# ==========================================
# FLASK KEEP-ALIVE
# ==========================================
app = Flask('')
@app.route('/')
def home(): return "Makima's Control System is Active!"

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
SPECIFIC_CONTROL_GROUP = int(_require_env("SPECIFIC_CONTROL_GROUP"))
SPECIFIC_GROUP = int(_require_env("SPECIFIC_GROUP"))
# Optional: a channel where every /addchar'd character gets auto-posted with full details.
# Not required — if unset, /addchar and /postallchars simply skip the channel post and say so.
_character_channel_env = os.environ.get("CHARACTER_CHANNEL_ID")
CHARACTER_CHANNEL_ID = int(_character_channel_env) if _character_channel_env else None
TZ = pytz.timezone('Asia/Yangon')
STORAGE_CHANNEL = SPECIFIC_CONTROL_GROUP
GLOBAL_SPAWN_CHAT_KEY = "global"  # groups_config_col doc key used for the default/global spawn_target

# MongoDB with connection pool
client_mongo = AsyncIOMotorClient(MONGO_URI, maxPoolSize=50)
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
gift_history_col = db["gift_history"]
haido_history_col = db["haido_history"] # records each person-to-person /gift for profile stats
character_stocks_col = db["character_stocks"] # 📈 per-character live stock market data
btc_market_col = db["btc_market"] # ₿ single global Bitcoin-style exchange rate doc

# Redis caching with connection pool
# NOTE: previously hardcoded to host='localhost' — if Redis runs anywhere other
# than the same machine as the bot (managed Redis, separate container, Redis
# Cloud/Upstash, etc.) that connection can never succeed. Set REDIS_URL in the
# environment (e.g. redis://user:pass@host:port/0 or rediss://... for TLS) to
# point at the real instance. Falls back to localhost for local/dev setups.
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True, max_connections=20)

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
CHAR_CACHE_TTL = 45

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

async def invalidate_character_caches():
    """Call this right after /addchar, /editchar, or /delchar successfully changes the roster."""
    try:
        await redis_client.delete("cache:chars:all", "cache:chars:categories")
    except Exception as e:
        print(f"char cache invalidate error: {e}")

# ==========================================
# 📈 CHARACTER STOCK MARKET — CORE ENGINE
# ==========================================
def _stock_tier_for_char(char_doc):
    if not char_doc:
        return "OTHER"
    return char_doc.get("rarity_tier") or classify_rarity(char_doc.get("rarity", ""))

async def get_or_create_stock(char_id, char_doc=None):
    """Fetches a character's stock doc, lazily creating it (seeded from the character's
    base currency_value) on first-ever market touch. Also lazily rolls the 'day open'
    price forward whenever a new Yangon calendar day has begun, so % daily change stays
    accurate without needing a separate midnight cron job."""
    if char_doc is None:
        char_doc = await characters_base_col.find_one({"char_id": char_id})
    stock = await character_stocks_col.find_one({"char_id": char_id})
    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    if not stock:
        base_price = float(char_doc.get("currency_value", 100)) if char_doc else 100.0
        base_price = max(base_price, 10.0)
        stock = {
            "char_id": char_id, "price": base_price, "day_open_price": base_price,
            "day_open_date": today_str, "prev_tick_price": base_price,
            "history": [base_price], "total_shares": 0.0,
            "buy_volume_today": 0.0, "sell_volume_today": 0.0, "updated_at": time.time(),
        }
        try:
            await character_stocks_col.insert_one(dict(stock))
        except Exception:
            refetched = await character_stocks_col.find_one({"char_id": char_id})
            if refetched:
                stock = refetched
    if stock.get("day_open_date") != today_str:
        await character_stocks_col.update_one(
            {"char_id": char_id},
            {"$set": {"day_open_price": stock["price"], "day_open_date": today_str,
                      "buy_volume_today": 0.0, "sell_volume_today": 0.0}}
        )
        stock["day_open_price"] = stock["price"]
        stock["day_open_date"] = today_str
    return stock

def _stock_price_floor(char_doc, stock):
    base = float(char_doc.get("currency_value", 100)) if char_doc else stock.get("day_open_price", 100)
    return max(1.0, base * STOCK_MIN_PRICE_FACTOR)

async def _write_stock_price(char_id, stock, new_price, extra_fields=None):
    new_price = round(max(new_price, 1.0), 2)
    history = (stock.get("history", []))[-(STOCK_HISTORY_MAX - 1):] + [new_price]
    fields = {"prev_tick_price": stock["price"], "price": new_price,
               "history": history, "updated_at": time.time()}
    if extra_fields:
        fields.update(extra_fields)
    await character_stocks_col.update_one({"char_id": char_id}, {"$set": fields})
    return new_price

async def apply_stock_trade_impact(char_id, mmk_amount, direction, char_doc=None):
    """Buying/selling nudges the character's own price — small trades barely move it,
    big trades (relative to the character's rarity-based liquidity) swing it hard.
    direction: +1 for a buy (price up), -1 for a sell (price down)."""
    if char_doc is None:
        char_doc = await characters_base_col.find_one({"char_id": char_id})
    stock = await get_or_create_stock(char_id, char_doc)
    tier = _stock_tier_for_char(char_doc)
    liquidity = STOCK_TIER_LIQUIDITY.get(tier, 18000)
    impact = min(mmk_amount / liquidity, STOCK_MAX_SINGLE_TRADE_IMPACT)
    new_price = stock["price"] * (1 + direction * impact)
    new_price = max(new_price, _stock_price_floor(char_doc, stock))
    vol_field = "buy_volume_today" if direction > 0 else "sell_volume_today"
    new_price = await _write_stock_price(char_id, stock, new_price, {
        vol_field: stock.get(vol_field, 0.0) + mmk_amount
    })
    return new_price

async def nudge_stock_on_catch(char_id, rarity_str):
    """Called from perform_catch — every real catch is treated as a tiny demand signal
    for that character's stock. Never allowed to break the catch flow if it fails."""
    try:
        tier = classify_rarity(rarity_str)
        bump = STOCK_CATCH_BUMP.get(tier, 0.005)
        char_doc = await characters_base_col.find_one({"char_id": char_id})
        stock = await get_or_create_stock(char_id, char_doc)
        new_price = stock["price"] * (1 + bump)
        new_price = max(new_price, _stock_price_floor(char_doc, stock))
        await _write_stock_price(char_id, stock, new_price)
        return bump
    except Exception as e:
        logging.error(f"Stock catch-nudge error for {char_id}: {e}")
        return 0.0

def stock_sparkline(history):
    if not history or len(history) < 2:
        return "▪️" * 8
    blocks = "▁▂▃▄▅▆▇█"
    lo, hi = min(history), max(history)
    span = (hi - lo) or 1.0
    return "".join(blocks[min(7, int((p - lo) / span * 7))] for p in history[-16:])

def stock_change_pct(current, base):
    if not base:
        return 0.0
    return ((current - base) / base) * 100.0

async def recompute_user_portfolio_value(user_id, portfolio=None):
    """Recomputes and caches a user's total live stock portfolio value onto their user
    doc (stock_portfolio_value) so the investor leaderboard can sort on an indexed field
    instead of walking every user's holdings on every /leaderboard tap."""
    if portfolio is None:
        user_doc = await users_catcher_col.find_one({"user_id": user_id})
        portfolio = user_doc.get("stock_portfolio", []) if user_doc else []
    total = 0.0
    for holding in portfolio:
        stock = await character_stocks_col.find_one({"char_id": holding["char_id"]})
        price = stock["price"] if stock else holding.get("avg_cost", 0)
        total += holding.get("shares", 0) * price
    total = round(total, 2)
    await users_catcher_col.update_one({"user_id": user_id}, {"$set": {"stock_portfolio_value": total}})
    return total

# ==========================================
# ₿ BITCOIN EXCHANGE — CORE ENGINE
# ==========================================
async def get_or_create_btc_market():
    """Lazily creates the single global BTC market doc on first-ever touch, and rolls its
    'day open' price forward whenever a new Yangon calendar day has begun."""
    market = await btc_market_col.find_one({"key": "BTC"})
    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    if not market:
        market = {
            "key": "BTC", "price": BTC_STARTING_PRICE, "day_open_price": BTC_STARTING_PRICE,
            "day_open_date": today_str, "prev_tick_price": BTC_STARTING_PRICE,
            "history": [BTC_STARTING_PRICE], "buy_volume_today": 0.0, "sell_volume_today": 0.0,
            "updated_at": time.time(),
        }
        try:
            await btc_market_col.insert_one(dict(market))
        except Exception:
            refetched = await btc_market_col.find_one({"key": "BTC"})
            if refetched:
                market = refetched
    if market.get("day_open_date") != today_str:
        await btc_market_col.update_one(
            {"key": "BTC"},
            {"$set": {"day_open_price": market["price"], "day_open_date": today_str,
                      "buy_volume_today": 0.0, "sell_volume_today": 0.0}}
        )
        market["day_open_price"] = market["price"]
        market["day_open_date"] = today_str
    return market

async def _write_btc_price(market, new_price, extra_fields=None):
    new_price = round(max(new_price, BTC_MIN_PRICE), 2)
    history = (market.get("history", []))[-(BTC_HISTORY_MAX - 1):] + [new_price]
    fields = {"prev_tick_price": market["price"], "price": new_price,
               "history": history, "updated_at": time.time()}
    if extra_fields:
        fields.update(extra_fields)
    await btc_market_col.update_one({"key": "BTC"}, {"$set": fields})
    return new_price

async def apply_btc_trade_impact(mmk_amount, direction):
    """Same pump/dump-on-volume model as character stocks, but on the single global rate."""
    market = await get_or_create_btc_market()
    impact = min(mmk_amount / BTC_LIQUIDITY, BTC_MAX_SINGLE_TRADE_IMPACT)
    new_price = market["price"] * (1 + direction * impact)
    vol_field = "buy_volume_today" if direction > 0 else "sell_volume_today"
    new_price = await _write_btc_price(market, new_price, {vol_field: market.get(vol_field, 0.0) + mmk_amount})
    return new_price

async def _compute_stock_market_sentiment():
    """Average tick-over-tick % change across every traded character stock — this is the
    signal that lets BTC drift with the broader character stock market instead of moving
    in total isolation, tying the two economies together."""
    try:
        stocks = await character_stocks_col.find({}, {"price": 1, "prev_tick_price": 1}).to_list(length=None)
        changes = []
        for s in stocks:
            prev = s.get("prev_tick_price") or s.get("price")
            if prev:
                changes.append((s["price"] - prev) / prev)
        return sum(changes) / len(changes) if changes else 0.0
    except Exception:
        return 0.0

async def recompute_user_btc_value(user_id, btc_balance=None):
    if btc_balance is None:
        user_doc = await users_catcher_col.find_one({"user_id": user_id})
        btc_balance = user_doc.get("btc_balance", 0.0) if user_doc else 0.0
    market = await get_or_create_btc_market()
    value = round(btc_balance * market["price"], 2)
    await users_catcher_col.update_one({"user_id": user_id}, {"$set": {"btc_value_mmk": value}})
    return value

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
    await users_catcher_col.create_index("total_caught")
    await users_catcher_col.create_index("wallet_balance")
    await users_catcher_col.create_index([("group_catches.$**", 1)])
    await users_catcher_col.create_index([("wallet_balance", -1)])
    await users_catcher_col.create_index([("total_caught", -1)])
    await users_catcher_col.create_index([("daily_streak", -1)])
    await users_catcher_col.create_index([("last_daily", 1)])
    await users_catcher_col.create_index([("group_catches.chat_id", -1)])
    await users_catcher_col.create_index([("last_catch_date", -1), ("daily_catches", -1)])
    await users_catcher_col.create_index([("quiz_correct", -1)])
    await _dedupe_groups_config()
    await groups_config_col.create_index("chat_id", unique=True)
    await muted_registry_col.create_index([("chat_id", 1), ("user_id", 1)])
    await user_game_profiles_col.create_index("user_id", unique=True)
    await characters_col.create_index("name", unique=True)
    await characters_base_col.create_index("char_id", unique=True)
    await characters_base_col.create_index("category")
    await characters_base_col.create_index("rarity_tier")
    await characters_base_col.create_index("spawn_limit")
    await gift_history_col.create_index([("sender_id", 1), ("timestamp", -1)])
    await gift_history_col.create_index([("receiver_id", 1), ("timestamp", -1)])
    # haido_history_col indexes
    await haido_history_col.create_index([("chat_id", 1), ("timestamp", -1)])
    await haido_history_col.create_index([("claimed_by", 1), ("timestamp", -1)])
    await haido_history_col.create_index("claimed")
    # 📈 Stock market indexes
    await character_stocks_col.create_index("char_id", unique=True)
    await users_catcher_col.create_index([("stock_portfolio_value", -1)])
    await btc_market_col.create_index("key", unique=True)
    await users_catcher_col.create_index([("btc_value_mmk", -1)])
    # ❌ REMOVED: gotu_pairs_col, gotu_games_col, gotu_players_col, quiz_questions_col, quiz_msg_counters_col
    await _migrate_rarity_tiers()
    print("Database Indexes synchronized! ✔️")

bot1 = TelegramClient('bot_main_session', APP_ID, APP_HASH)
bot_ids = []
active_group_spawns = {}
active_card_games = {}
STEALTH_MAU_MODE = False
spawn_locks = defaultdict(asyncio.Lock)
pending_editchar_prompt_ids = {}
sticker_spam_data = {}
char_spam_data = {}
admin_cache = {}
dark_passenger_targets = {}
IS_REPLY_ACTIVE = False
REPLY_INTERVAL = 4
reply_msg_counters = {}
USER_METRICS_BUFFER = []
BUFFER_LOCK = asyncio.Lock()
user_cooldowns = {}

# Spam & Daily catch tracking
user_spam_data = {}  # user_id -> list of timestamps
user_mute_until = {}  # user_id -> timestamp
active_haido_events = {}  # chat_id -> {"char_id", "char_name", "rarity", "spawn_time", "claimed_by", "claimed"}
HAIDO_TIMEOUT_SECONDS = 900  # 15 minutes

# ---- Rarity 1 & 2 (LEGENDARY / LIMITED) spawns are gated behind a quiz: chosen_char is picked
# as usual, but instead of spawning immediately, a question + 4 inline buttons is posted. Only
# the FIRST correct tap actually releases the spawn (normal /who + /catch flow after that).
# chat_id -> {"char", "options", "correct_index", "question", "msg_id", "quiz_time", "solved"}
pending_rarity_quiz = {}
RARITY_GATE_TIERS = {"LEGENDARY", "LIMITED"}  # rarity No.1 and No.2
RARITY_GATE_TIMEOUT_SECONDS = 120
RARITY_QUIZ_BANK = [
    {"question": "What is the capital of Japan?", "options": ["Tokyo", "Osaka", "Kyoto", "Nagoya"], "correct_index": 0},
    {"question": "How many continents are there on Earth?", "options": ["5", "6", "7", "8"], "correct_index": 2},
    {"question": "What is the chemical symbol for Gold?", "options": ["Ag", "Au", "Gd", "Go"], "correct_index": 1},
    {"question": "Which planet is known as the Red Planet?", "options": ["Venus", "Mars", "Jupiter", "Saturn"], "correct_index": 1},
    {"question": "What is 12 × 12?", "options": ["124", "144", "132", "156"], "correct_index": 1},
    {"question": "Which ocean is the largest on Earth?", "options": ["Atlantic", "Indian", "Arctic", "Pacific"], "correct_index": 3},
    {"question": "How many days are there in a leap year?", "options": ["364", "365", "366", "367"], "correct_index": 2},
    {"question": "What is the largest organ in the human body?", "options": ["Heart", "Liver", "Skin", "Lungs"], "correct_index": 2},
    {"question": "Which country gifted the Statue of Liberty to the USA?", "options": ["Spain", "France", "United Kingdom", "Italy"], "correct_index": 1},
    {"question": "What is the smallest prime number?", "options": ["0", "1", "2", "3"], "correct_index": 2},
    {"question": "Which language has the most native speakers worldwide?", "options": ["English", "Hindi", "Mandarin Chinese", "Spanish"], "correct_index": 2},
    {"question": "How many hearts does an octopus have?", "options": ["1", "2", "3", "4"], "correct_index": 2},
    {"question": "What is the freezing point of water in Celsius?", "options": ["0°C", "32°C", "-1°C", "100°C"], "correct_index": 0},
    {"question": "Which gas do plants absorb from the atmosphere to make food?", "options": ["Oxygen", "Nitrogen", "Carbon Dioxide", "Hydrogen"], "correct_index": 2},
    {"question": "What is the tallest mountain in the world?", "options": ["K2", "Kangchenjunga", "Mount Everest", "Lhotse"], "correct_index": 2},
]

# ---- /ban, /unban, /kick fire a ChatAction (participant update) themselves — e.g. lifting a
# ban via /unban shows up to Telethon as the user "leaving", which used to trigger a spurious
# goodbye message right after the UNBAN OPERATION confirmation. Anything we changed ourselves
# via /ban, /unban, or /kick is recorded here for a few seconds so welcome_goodbye can ignore
# the resulting ChatAction instead of misfiring a welcome/goodbye text for it.
recent_mod_actions = {}  # (chat_id, user_id) -> timestamp
MOD_ACTION_SUPPRESS_WINDOW = 10  # seconds

def mark_mod_action(chat_id, user_id):
    recent_mod_actions[(chat_id, user_id)] = time.time()

def was_recent_mod_action(chat_id, user_id):
    ts = recent_mod_actions.pop((chat_id, user_id), None)
    return ts is not None and (time.time() - ts) <= MOD_ACTION_SUPPRESS_WINDOW

def bq(text): return f"<blockquote><b>{text}</b></blockquote>"
def owner_tag():
    return f"<a href='tg://user?id={OWNER_ID}'><b>Owner</b></a>"

# Rarity No.1 (highest) → No.6 (lowest). Emoji come from RARITY_EMOJI so there is only
# ever ONE place to change them. "name" is what gets stored on characters/harem items and
# shown everywhere — it always carries the emoji + fancy font tier name + its number, so
# players can instantly see both the rarity tier and its rank (1–6).
_RARITY_VALUE_MAP = {"LEGENDARY": 10000, "LIMITED": 750, "MYTHIC": 500, "EPIC": 300, "RARE": 150, "COMMON": 50}
RARITY_NUM_MAP = {
    str(i + 1): {
        "name": f"{RARITY_EMOJI[tier]} {f(tier)} {f(f'No.{i + 1}')}",
        "value": _RARITY_VALUE_MAP[tier]
    }
    for i, tier in enumerate(RARITY_TIERS)
}
# Reverse lookup: canonical tier name -> its rarity number ("1".."6"). Used by /changeallrarity
# and anywhere we need to re-derive the current official display name for an existing tier.
RARITY_TIER_TO_NUM = {tier: str(i + 1) for i, tier in enumerate(RARITY_TIERS)}

# ==========================================
# 📈 CHARACTER STOCK MARKET — TUNING CONSTANTS
# ==========================================
# Rarer characters swing harder on the background "market drift" tick — think of it as
# thinner order books for rare cards vs. deep, stable liquidity for commons.
STOCK_TIER_VOLATILITY = {
    "LEGENDARY": 0.08, "LIMITED": 0.065, "MYTHIC": 0.05,
    "EPIC": 0.035, "RARE": 0.02, "COMMON": 0.01, "OTHER": 0.02,
}
# How much MMK of net buy/sell pressure it takes to move a character's price by ~100%.
# Rarer = lower liquidity = a given MMK amount pushes the price further (pump/dump easier).
STOCK_TIER_LIQUIDITY = {
    "LEGENDARY": 60000, "LIMITED": 48000, "MYTHIC": 36000,
    "EPIC": 24000, "RARE": 14000, "COMMON": 9000, "OTHER": 18000,
}
# Small automatic demand bump applied to a character's stock every time it's actually
# caught in the wild — this is the link between the catch game and the market.
STOCK_CATCH_BUMP = {
    "LEGENDARY": 0.02, "LIMITED": 0.016, "MYTHIC": 0.013,
    "EPIC": 0.01, "RARE": 0.006, "COMMON": 0.003, "OTHER": 0.005,
}
STOCK_PRICE_TICK_SECONDS = 600      # background market drift runs every 10 minutes
STOCK_MIN_PRICE_FACTOR = 0.15       # price can never fall below 15% of the character's base worth
STOCK_MAX_SINGLE_TRADE_IMPACT = 0.35  # a single buy/sell can't move price more than ±35%
STOCK_HISTORY_MAX = 24              # ticks kept for the mini sparkline
STOCK_MIN_TRADE_MMK = 50            # smallest allowed /invest amount
STOCK_PAGE_SIZE = 8

# ==========================================
# ₿ BITCOIN EXCHANGE — TUNING CONSTANTS
# ==========================================
# A single global game-currency, separate from per-character stocks. Its price is its own
# random walk PLUS a small pull from the character stock market's average mood — so BTC
# behaves like a broad "index" of how hot the whole catch/invest economy is right now.
BTC_STARTING_PRICE = 1_000_000.0    # MMK per 1 BTC at genesis
BTC_TICK_SECONDS = 300              # background drift runs every 5 minutes
BTC_VOLATILITY = 0.03               # ±3% random walk per tick
BTC_SENTIMENT_WEIGHT = 0.25         # how much character-stock-market sentiment bleeds into BTC
BTC_LIQUIDITY = 300000              # MMK of net trade pressure to move price by ~100%
BTC_MAX_SINGLE_TRADE_IMPACT = 0.20  # a single buy/sell capped at ±20% impact
BTC_MIN_PRICE = 50000.0             # hard floor — can never crash to near-zero
BTC_TRADE_FEE_PCT = 0.015           # 1.5% exchange fee both ways (MMK sink, keeps the economy healthy)
BTC_MIN_TRADE_MMK = 100             # smallest allowed /buybtc amount
BTC_HISTORY_MAX = 24

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

GAME_FOOTER = "\n\n🪙 <code>/balance</code>"

async def _delete_after_delay(client, chat_id, msg_id, delay=10):
    try:
        await asyncio.sleep(delay)
        await client.delete_messages(chat_id, msg_id)
    except Exception:
        pass

def schedule_game_cleanup(client, chat_id, msg, delay=10):
    msg_id = getattr(msg, 'id', msg)
    asyncio.create_task(_delete_after_delay(client, chat_id, msg_id, delay))

# ==========================================
# ALL GROUPS COMMAND INTERCEPTOR
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/'))
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
# 🔎 DM AUTO-IDENTIFY — in the bot's private chat, just sending a photo or video is enough
# to get it identified — no /who, no reply needed. (The old "forward every DM to owner"
# behavior that used to live here has been removed.)
# ==========================================
@bot1.on(events.NewMessage(incoming=True, func=lambda e: e.is_private and (e.photo or e.video)))
async def dm_auto_identify_handler(event):
    if event.sender_id in bot_ids:
        return
    text = (event.raw_text or "").strip()
    if text.startswith('/'):
        return  # an explicit "/who" caption is handled by who_reveal_handler's Path B instead
    await _identify_media_and_reply(event, event.message)

# ==========================================
# OWNER REPLY TO FORWARDED MESSAGE
# ==========================================
@bot1.on(events.NewMessage(incoming=True))
async def reply_to_user_from_owner(event):
    if not event.is_private:
        return
    if event.sender_id != OWNER_ID:
        return
    if not event.is_reply:
        return
    if event.reply_to_msg_id in pending_editchar_prompt_ids:
        return
    reply_msg = await event.get_reply_message()
    if not reply_msg:
        return
    if not reply_msg.fwd_from:
        await event.reply("⚠️ ဒီစာက Forward လုပ်ထားတာမဟုတ်ဘူး။ ကျေးဇူးပြုပြီး Forward လုပ်ထားတဲ့စာကို Reply လုပ်ပါ။")
        return
    try:
        original_user_id = reply_msg.fwd_from.from_id.user_id
    except Exception:
        await event.reply("❌ မူရင်းလူရဲ့ ID ကို ရှာမတွေ့ဘူး။")
        return
    if not original_user_id:
        await event.reply("❌ မူရင်းလူရဲ့ ID မရှိဘူး။")
        return
    try:
        if event.media:
            await bot1.send_file(original_user_id, event.media, caption=event.text)
        else:
            await bot1.send_message(original_user_id, event.text)
        await event.reply(f"✅ သင့်အဖြေကို မူရင်းလူ (ID: {original_user_id}) ဆီ ပြန်ပို့လိုက်ပါပြီ။")
    except Exception as e:
        await event.reply(f"❌ ပြန်ပို့ရာမှာ အမှားရှိသွားပါတယ်: {e}")

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

async def check_ban_rights(chat_id, user_id):
    if user_id == OWNER_ID: return True
    try:
        permissions = await bot1.get_permissions(chat_id, user_id)
        return permissions.is_admin and permissions.ban_users
    except: return False

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
# SMART REPLY ENGINE
# ==========================================
async def fetch_optimized_smart_reply(user_text):
    reply_text = None
    user_text_clean = user_text.strip().lower()
    try:
        exact_doc = await reply_save_col.find_one({"trigger": user_text_clean})
        if exact_doc and exact_doc.get("responses") and isinstance(exact_doc["responses"], list):
            valid_choices = [c for c in exact_doc["responses"] if not ('@' in str(c) or 't.me/' in str(c))]
            if valid_choices:
                return random.choice(valid_choices)
    except Exception as e:
        print(f"Exact Match DB Error: {e}")
    match_pipeline = [
        {"$match": {
            "$and": [
                {"$ifNull": ["$trigger", False]},
                {"$gte": [{"$strLenCP": "$trigger"}, 2]},
                {"$gte": [{"$indexOfCP": [user_text_clean, {"$toLower": "$trigger"}]}, 0]}
            ]
        }},
        {"$addFields": {"weight": {"$strLenCP": "$trigger"}}},
        {"$sort": {"weight": -1}},
        {"$limit": 5}
    ]
    try:
        cursor = reply_save_col.aggregate(match_pipeline)
        matched_docs = await cursor.to_list(length=5)
        if matched_docs:
            valid_choices = []
            max_weight = matched_docs[0]["weight"]
            best_docs = [d for d in matched_docs if d["weight"] == max_weight]
            for doc in best_docs:
                if doc.get("responses") and isinstance(doc["responses"], list):
                    choices = [c for c in doc["responses"] if not ('@' in str(c) or 't.me/' in str(c))]
                    valid_choices.extend(choices)
            if valid_choices:
                reply_text = random.choice(valid_choices)
    except Exception as e:
        print(f"Smart Reply DB Error: {e}")
    return reply_text

# ==========================================
# EVENT HANDLERS (start, help, etc.)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/start(?:@\w+)?(?:\s+(\S+))?$'))
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
                NEW_USER_BONUS = 1000
                REFERRER_BONUS = 2000
                await users_catcher_col.update_one(
                    {"user_id": user_id},
                    {"$set": {"referred_by": referrer_id}, "$inc": {"wallet_balance": NEW_USER_BONUS}}
                )
                await users_catcher_col.update_one(
                    {"user_id": referrer_id},
                    {"$inc": {"wallet_balance": REFERRER_BONUS, "referral_count": 1}}
                )
                await check_and_award_achievements(referrer_id, notify_chat_id=referrer_id)
                referral_note = (
                    f"\n\n🎉 <b>Referral Bonus!</b> You got <code>+{NEW_USER_BONUS} MMK</code> welcome gift, "
                    f"and your friend got <code>+{REFERRER_BONUS} MMK</code> for inviting you! 🎁"
                )
    bot_me = await bot1.get_me()
    welcome_msg = (
        f"⛓️ <b>MAKIMA’S CONTROL SYSTEM</b> ⛓️\n"
        f"Hello. I am Makima. You may think of me as the one who watches over this place. ⛓️\n"
        f"I manage groups, summon characters for you to collect, run a few games to keep you entertained, "
        f"and answer your questions — quietly, and completely. 🩸\n\n"
        f"Use the buttons below to see what I allow. "
        f"Behave, and I'll be pleased with you. 🙂"
        f"{referral_note}"
    )
    buttons = [
        [Button.inline("⚙️ Commands", data="nav_help_main"), Button.inline("🎮 Game Rules", data="nav_game_main")],
        [Button.url("🪐 Add Me To Your Group", f"https://t.me/{bot_me.username}?startgroup=true")],
        [Button.url("👥 Join Makima's Circle", "https://t.me/Comeback_BoD")]
    ]
    await event.respond(bq(welcome_msg), parse_mode='html', buttons=buttons)

def get_help_text():
    return (
        f"⚙️ <b><u>Makima’s Command Terminal</u></b>\n\n"
        f" <code>/id</code> - Get user or chat ID\n"
        f" <code>/mute [Reply]</code> - Mute a user permanently\n"
        f" <code>/unmute [Reply]</code> - Unmute a user\n"
        f" <code>/ban [Reply]</code> - Ban a user from the group\n"
        f" <code>/kick [Reply]</code> - Kick a user from the chat\n"
        f" <code>/adm [Text]</code> - Tag all admins in the group\n"
        f" <code>/calc [Expression]</code> - Calculate math expressions\n"
        f" <code>/weather</code> - Live weather report (Myanmar & Thailand)"
    )

def get_game_text():
    return (
        f"🎮 <b><u>Makima’s Game Console</u></b>\n\n"
        f"⭐ <code>/balance</code>\n<blockquote>Check your wallet balance.</blockquote>\n\n"
        f"⭐ <code>/daily</code>\n<blockquote>Claim your daily bonus (streak rewards!).</blockquote>\n\n"
        f"⭐ <code>/hunt</code>\n<blockquote>Go on a hunting adventure for extra cash.</blockquote>\n\n"
        f"⭐ <code>/gift [char_id]</code> (reply to a user)\n<blockquote>Send a caught character to another player.</blockquote>\n\n"
        f"📈 <code>/stocks</code>\n<blockquote>Browse the character stock market.</blockquote>\n\n"
        f"📈 <code>/stock [char_id]</code>\n<blockquote>View one character's live price, trend, and your holding.</blockquote>\n\n"
        f"📈 <code>/invest [char_id] [MMK]</code>\n<blockquote>Buy shares in a character — price moves with catches and trading.</blockquote>\n\n"
        f"📈 <code>/divest [char_id] [shares|all]</code>\n<blockquote>Sell shares back for MMK.</blockquote>\n\n"
        f"📈 <code>/portfolio</code>\n<blockquote>See everything you've invested in and your live P/L.</blockquote>\n\n"
        f"₿ <code>/btc</code>\n<blockquote>Check the live Bitcoin exchange rate (moves with the stock market's mood!).</blockquote>\n\n"
        f"₿ <code>/buybtc [MMK]</code>\n<blockquote>Convert MMK into BTC.</blockquote>\n\n"
        f"₿ <code>/sellbtc [BTC|all]</code>\n<blockquote>Convert BTC back into MMK.</blockquote>"
    )

@bot1.on(events.NewMessage(pattern=r'(?i)^/help(?:@\w+)?$'))
async def help_command_handler(event):
    await event.reply(bq(get_help_text()), parse_mode='html', buttons=[[Button.inline("🔙 Back", data="nav_back_home")]])

@bot1.on(events.NewMessage(pattern=r'(?i)^/game(?:@\w+)?$'))
async def game_command_handler(event):
    await event.reply(bq(get_game_text()), parse_mode='html', buttons=[[Button.inline("🔙 Back", data="nav_back_home")]])

@bot1.on(events.CallbackQuery())
async def system_callback_router(event):
    data = event.data.decode('utf-8')
    user_id = event.sender_id
    if data == "nav_back_home":
        bot_me = await bot1.get_me()
        welcome_msg = (
            f"⛓️ <b>MAKIMA’S CONTROL SYSTEM</b> ⛓️\n"
            f"Hello. I am Makima. You may think of me as the one who watches over this place. ⛓️\n"
            f"I manage groups, summon characters for you to collect, run a few games to keep you entertained, "
            f"and answer your questions — quietly, and completely. 🩸"
        )
        buttons = [
            [Button.inline("⚙️ Commands", data="nav_help_main"), Button.inline("🎮 Game Rules", data="nav_game_main")],
            [Button.url("Add Me To Your Group", f"https://t.me/{bot_me.username}?startgroup=true")],
            [Button.url("Join Makima's Circle", "https://t.me/Comeback_BoD")]
        ]
        return await event.edit(bq(welcome_msg), parse_mode='html', buttons=buttons)
    elif data == "nav_help_main":
        return await event.edit(bq(get_help_text()), parse_mode='html', buttons=[[Button.inline("🔙 Back", data="nav_back_home")]])
    elif data == "nav_game_main":
        return await event.edit(bq(get_game_text()), parse_mode='html', buttons=[[Button.inline("🔙 Back", data="nav_back_home")]])
    elif data == "nav_hmode":
        await set_rarity_filter_handler(event)

# ---- CALCULATOR ----
@bot1.on(events.NewMessage)
async def auto_calculator_handler(event):
    if event.text.startswith('/'): return
    text = event.text.strip()
    if re.match(r'^[\d\.\s\+\-\*\/\(\)\*\*]+$', text):
        if any(op in text for op in ['+', '-', '*', '/']):
            if len(text) > 50 or (text.count('**') > 1): return
            try:
                result = eval(text, {"__builtins__": None}, {})
                await event.reply(f"🍺<b>Result:</b>\n<code>{text} = {result}</code>", parse_mode='html')
            except Exception:
                pass

# ---- INFO ----
@bot1.on(events.NewMessage(pattern=r'(?i)^/info'))
async def info_handler(event):
    try:
        target_id = None
        target_user = None
        if event.is_reply:
            reply_msg = await event.get_reply_message()
            target_id = reply_msg.sender_id
            target_user = await event.client.get_entity(target_id)
        elif len(event.text.split()) > 1:
            arg = event.text.split()[1]
            try:
                target_user = await event.client.get_entity(arg)
                target_id = target_user.id
            except Exception:
                return await event.reply("⚠️ User not found!")
        else:
            target_id = event.sender_id
            target_user = await event.client.get_entity(target_id)
        if target_user:
            full_name = f"{target_user.first_name or ''} {target_user.last_name or ''}".strip() or "User"
            username = f"@{target_user.username}" if target_user.username else "None"
            info_caption = (
                f"✨ <b>USER INFO</b> ✨\n───────────\n"
                f"🦖 <b>Full Name:</b> {full_name}\n"
                f"🍄 <b>User ID:</b> <code>{target_id}</code> (tap to copy)\n"
                f"🦦 <b>Username:</b> {username}\n"
                f"🌏 <b>Chat ID:</b> <code>{event.chat_id}</code> (tap to copy)\n"
                f"───────────\n<i>Click to copy</i>"
            )
            await event.reply(info_caption, parse_mode='html')
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
        # Broadcast channels (not groups/supergroups) never get welcome/goodbye/intro text —
        # only a private heads-up to the owner when the bot itself is added, further down.
        is_broadcast_channel = bool(getattr(chat, 'broadcast', False) and not getattr(chat, 'megagroup', False))
        if (event.user_added or event.user_joined) and any(uid == bot_me.id for uid in target_ids if uid):
            try:
                full_chat = await event.client.get_full_chat(chat.id)
                members_cnt = getattr(full_chat, 'participants_count', None)
                member_count = f"{members_cnt:,}" if members_cnt else "Unknown"
            except Exception:
                member_count = "N/A"
            group_link = None
            try:
                invite = await event.client(ExportChatInviteRequest(chat.id))
                group_link = invite.link if invite else None
            except Exception:
                group_link = "Cannot fetch (need admin rights)"
            if not group_link:
                group_link = "Not available (or bot not admin)"
            owner_msg = (
                f"✅ <b>Bot Added to New {'Channel' if is_broadcast_channel else 'Group'}!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📛 <b>Name:</b> <code>{escape_html(group_name)}</code>\n"
                f"🆔 <b>ID:</b> <code>{chat.id}</code>\n"
                f"👥 <b>Members:</b> <code>{member_count}</code>\n"
                f"🔗 <b>Invite Link:</b> <code>{escape_html(str(group_link))}</code>\n"
                f"⏰ <b>Time:</b> <code>{datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}</code>"
            )
            await bot1.send_message(OWNER_ID, owner_msg, parse_mode='html')
            try:
                await bot1.send_message(SPECIFIC_GROUP, owner_msg, parse_mode='html')
            except Exception:
                pass
            if is_broadcast_channel:
                # No public intro/welcome message in channels — owner has already been notified above.
                return
            intro_msg = (
                f"<blockquote>⛓️ <b>Makima is watching over this group now.</b>\n\n"
                f"I'll manage things here, run a few games, and keep everyone in line.\n"
                f"📌 <b>Try these commands:</b>\n"
                f"   • <code>/introduce</code> - Learn what I can do\n"
                f"   • <code>/help</code> - See all commands\n\n"
                f"✨ Behave, and you'll enjoy your stay.</blockquote>"
            )
            await bot1.send_message(chat.id, intro_msg, parse_mode='html')
            await groups_col.update_one(
                {"chat_id": chat.id},
                {"$set": {"title": chat.title, "joined_at": datetime.now(TZ)}},
                upsert=True
            )
            return
        if is_broadcast_channel:
            # Individual member join/leave events in a channel never get a welcome/goodbye text.
            return
        elif (event.user_added or event.user_joined):
            for uid in target_ids:
                if uid and uid != bot_me.id:
                    # A join immediately following our own /unban on this user is not a real
                    # join — it's Telegram reflecting the permission change. Skip it silently.
                    if was_recent_mod_action(chat.id, uid):
                        continue
                    try:
                        u = await bot1.get_entity(uid)
                    except:
                        continue
                    track_user_metrics(u.id, u.username, u.first_name)
                    user_first_name = u.first_name or "User"
                    mention = f"<a href='tg://user?id={uid}'><b>{escape_html(user_first_name)}</b></a>"
                    welcome_text = (
                        f"<blockquote>ဟိုက် {mention}... {escape_html(group_name)} ကနေ ကြိုဆိုလိုက်ပါတယ်\n"
                        f"⛓️ Makima has her eyes on you now.</blockquote>"
                    )
                    await bot1.send_message(
                        event.chat_id,
                        welcome_text,
                        buttons=[
                            [Button.url("Bot OWNER DM", "t.me/Hello_Im_DexterMorgan")],
                            [Button.url("Join Makima's Group", "https://t.me/Comeback_BoD")]
                        ],
                        parse_mode='html'
                    )
        elif event.user_kicked or event.user_left:
            if event.user_id == bot_me.id:
                await groups_col.delete_one({"chat_id": chat.id})
            else:
                # A leave immediately following our own /ban, /unban, or /kick on this user is
                # an admin action we already confirmed in-chat, not a real departure — skip it.
                if was_recent_mod_action(chat.id, event.user_id):
                    return
                try:
                    u = await bot1.get_entity(event.user_id)
                    user_first_name = u.first_name or "User"
                    mention = f"<a href='tg://user?id={event.user_id}'><b>{escape_html(user_first_name)}</b></a>"
                    goodbye_text = f"<blockquote> တာ့တာ {mention}!\nWe'll miss you.</blockquote>"
                    await bot1.send_message(event.chat_id, goodbye_text, parse_mode='html')
                except:
                    pass
    except Exception as e:
        await report_system_error("Welcome Goodbye Event", e)

# ---- AUTO RESPONDER ----
@bot1.on(events.NewMessage(incoming=True))
async def chatbot_auto_responder(event):
    if event.sender_id:
        sender = await event.get_sender()
        if sender and not sender.bot:
            track_user_metrics(event.sender_id, getattr(sender, 'username', None), getattr(sender, 'first_name', None))
    if event.is_private or not event.text or event.text.startswith(('/', '.', 'Hey there','@')): return
    user_text = event.text.lower()
    cursor = ans_col.find()
    async for doc in cursor:
        keyphrase = doc.get("keyphrase")
        if keyphrase and keyphrase in user_text:
            responses = doc.get("responses", [])
            if responses:
                await event.reply(bq(random.choice(responses)), parse_mode='html')
                break

# ---- MAU STATS ----
@bot1.on(events.NewMessage(pattern=r'^/mau(?:@\w+)?$'))
async def system_analytics_matrix(event):
    if event.sender_id != OWNER_ID: return
    loading = await event.reply("<code>🌌 Pulling every name Makima keeps track of...</code>", parse_mode='html')
    try:
        total_tracked_users = await users_col.count_documents({})
        total_active_groups = await groups_col.count_documents({})
        day_ago = datetime.now(TZ) - timedelta(days=1)
        active_24h_users = await users_col.count_documents({"last_active": {"$gte": day_ago}})
        metrics_layout = (
            f"⛓️ <b>Makima’s MAU Stats</b>\n"
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
@bot1.on(events.NewMessage)
async def spam_detection_and_mute(event):
    if event.is_private or not event.text:
        return
    if event.sender_id in bot_ids or event.sender_id == OWNER_ID:
        return
    user_id = event.sender_id
    chat_id = event.chat_id
    now = time.time()

    # Check if user is already muted
    if user_id in user_mute_until and now < user_mute_until[user_id]:
        # Still muted, delete message if it's a command that should be blocked
        if event.text.startswith('/') and event.text.split('@')[0] in ['/catch', '/who', '/w', '/waifu']:
            try:
                await event.delete()
            except:
                pass
        return

    # Track message history
    if user_id not in user_spam_data:
        user_spam_data[user_id] = []
    # Clean old entries (>60s)
    user_spam_data[user_id] = [t for t in user_spam_data[user_id] if now - t < 60]
    user_spam_data[user_id].append(now)

    # If more than 20 messages in 60 seconds -> mute for 5 minutes
    if len(user_spam_data[user_id]) >= 20:
        user_mute_until[user_id] = now + 300  # 5 minutes
        try:
            sender = await event.get_sender()
            mention = f"<a href='tg://user?id={user_id}'>{escape_html(sender.first_name)}</a>"
            await event.respond(
                bq(f"⛓️ <b>Makima says:</b> {mention}, that's enough. "
                   f"You've been muted from using /catch and /who for 5 minutes. "
                   f"Sit still for a while. ⛓️"),
                parse_mode='html'
            )
        except Exception:
            pass
        # Optionally delete spam messages
        try:
            await event.delete()
        except:
            pass
        return

    # If user is a spammer, block /catch and /who
    if event.text.startswith('/'):
        if event.text.split('@')[0] in ['/catch', '/who', '/w', '/waifu']:
            if user_id in user_mute_until and now < user_mute_until[user_id]:
                try:
                    await event.delete()
                    await event.reply("⛓️ You're currently muted from catching. Wait quietly for a few minutes.")
                except:
                    pass

# ==========================================
# 🛡️ SPAM FILTERS (Admin Warn Once)
# ==========================================

# Track which admins have already been warned (so we don't spam them)
admin_warned_sticker = set()   # (chat_id, admin_id) -> True
admin_warned_char = set()      # (chat_id, admin_id) -> True

# ---- STICKER SPAM ----
@bot1.on(events.NewMessage)
async def sticker_spam_(event):
    if not event.sticker or event.is_private: return
    if event.sender_id in bot_ids: return
    
    sender_id = event.sender_id
    chat_id = event.chat_id
    now = datetime.now()
    
    if sender_id not in sticker_spam_data:
        sticker_spam_data[sender_id] = {"times": [], "ids": []}
    
    sticker_spam_data[sender_id]["times"].append(now)
    sticker_spam_data[sender_id]["ids"].append(event.id)
    
    one_minute_ago = now - timedelta(seconds=60)
    valid_data = [(t, i) for t, i in zip(sticker_spam_data[sender_id]["times"], sticker_spam_data[sender_id]["ids"]) if t > one_minute_ago]
    sticker_spam_data[sender_id]["times"] = [x[0] for x in valid_data]
    sticker_spam_data[sender_id]["ids"] = [x[1] for x in valid_data]
    
    recent_times = sticker_spam_data[sender_id]["times"]
    recent_ids = sticker_spam_data[sender_id]["ids"]
    
    # Check if admin
    is_admin = await check_admin(chat_id, sender_id)
    admin_key = (chat_id, sender_id)
    
    if len(recent_times) >= 6:
        try:
            await bot1.delete_messages(chat_id, recent_ids)
            sender = await event.get_sender()
            mention = f"<a href='tg://user?id={sender_id}'>{escape_html(sender.first_name)}</a>"
            
            if is_admin:
                # Admin: warn only ONCE per chat
                if admin_key not in admin_warned_sticker:
                    action_msg = f"⚠️ <b>Admin {mention}</b>, please refrain from spamming stickers. (⚠️ This is your only warning!)"
                    await event.respond(bq(action_msg), parse_mode='html')
                    admin_warned_sticker.add(admin_key)
                # After first warning, do nothing further for this admin in this chat
            else:
                # Normal user: restrict
                await bot1.edit_permissions(chat_id, sender_id, send_stickers=False)
                action_msg = f"{mention} has been restricted from sending stickers due to spam."
                await event.respond(bq(action_msg), parse_mode='html')
            
            del sticker_spam_data[sender_id]
            return
        except Exception as e:
            print(f"Sticker Spam Error: {e}")
    
    if len(recent_times) >= 3:
        time_diff = (recent_times[-1] - recent_times[-3]).total_seconds()
        if time_diff <= 1.0:
            await event.delete()
            # Admin: warn only ONCE for this too
            if is_admin and admin_key not in admin_warned_sticker:
                sender = await event.get_sender()
                mention = f"<a href='tg://user?id={sender_id}'>{escape_html(sender.first_name)}</a>"
                action_msg = f"⚠️ <b>Admin {mention}</b>, please don't spam stickers. (Only warning!)"
                await event.respond(bq(action_msg), parse_mode='html')
                admin_warned_sticker.add(admin_key)


# ---- CHARACTER SPAM ----
@bot1.on(events.NewMessage)
async def character_spam_filter(event):
    if event.is_private or not event.text: return
    if event.sender_id in bot_ids: return
    
    text = event.text.strip()
    if len(text) > 3: return
    
    sender_id = event.sender_id
    chat_id = event.chat_id
    now = datetime.now()
    
    if sender_id not in char_spam_data:
        char_spam_data[sender_id] = {"times": [], "ids": []}
    
    char_spam_data[sender_id]["times"].append(now)
    char_spam_data[sender_id]["ids"].append(event.id)
    
    one_minute_ago = now - timedelta(seconds=60)
    valid_data = [(t, i) for t, i in zip(char_spam_data[sender_id]["times"], char_spam_data[sender_id]["ids"]) if t > one_minute_ago]
    char_spam_data[sender_id]["times"] = [x[0] for x in valid_data]
    char_spam_data[sender_id]["ids"] = [x[1] for x in valid_data]
    
    recent_times = char_spam_data[sender_id]["times"]
    recent_ids = char_spam_data[sender_id]["ids"]
    
    # Check if admin
    is_admin = await check_admin(chat_id, sender_id)
    admin_key = (chat_id, sender_id)
    
    if len(recent_times) >= 6:
        try:
            await bot1.delete_messages(chat_id, recent_ids)
            sender = await event.get_sender()
            mention = f"<a href='tg://user?id={sender_id}'>{escape_html(sender.first_name)}</a>"
            
            if is_admin:
                # Admin: warn only ONCE per chat
                if admin_key not in admin_warned_char:
                    action_msg = f"⚠️ <b>Admin {mention}</b>, please stop spamming short messages. (⚠️ This is your only warning!)"
                    await event.respond(bq(action_msg), parse_mode='html')
                    admin_warned_char.add(admin_key)
                # After first warning, do nothing further for this admin in this chat
            else:
                # Normal user: mute for 5 minutes
                await bot1.edit_permissions(chat_id, sender_id, until_date=datetime.now() + timedelta(minutes=5), send_messages=False)
                action_msg = f"🚫 {mention} has been muted for 5 minutes due to spam."
                await event.respond(bq(action_msg), parse_mode='html')
            
            del char_spam_data[sender_id]
            return
        except Exception as e:
            print(f"Char Spam Error: {e}")
    
    if len(recent_times) == 5:
        try:
            await bot1.delete_messages(chat_id, recent_ids)
            sender = await event.get_sender()
            mention = f"<a href='tg://user?id={sender_id}'>{escape_html(sender.first_name)}</a>"
            
            if is_admin:
                # Admin: warn only ONCE for this too
                if admin_key not in admin_warned_char:
                    warn_msg = f"⚠️ <b>Admin {mention}</b>, stop spam or you'll be warned! (Only warning!)"
                    await event.respond(bq(warn_msg), parse_mode='html')
                    admin_warned_char.add(admin_key)
            else:
                warn_msg = f"⚠️ {mention}, stop spam or you’ll be muted for 5 minutes!"
                await event.respond(bq(warn_msg), parse_mode='html')
        except Exception as e:
            print(f"Char Spam Warn Error: {e}")
            
# ---- ID ----
@bot1.on(events.NewMessage(pattern=r'(?i)^/id'))
async def id_handler(event):
    try:
        target_id = None
        target_user = None
        if event.is_reply:
            reply_msg = await event.get_reply_message()
            target_id = reply_msg.sender_id
            target_user = await event.client.get_entity(target_id)
        elif len(event.text.split()) > 1:
            arg = event.text.split()[1]
            try:
                target_user = await event.client.get_entity(arg)
                target_id = target_user.id
            except Exception:
                return await event.reply("⚠️ User not found!")
        else:
            target_id = event.sender_id
            target_user = await event.client.get_entity(target_id)
        if target_user:
            first_name = target_user.first_name or "User"
            username = f"@{target_user.username}" if target_user.username else "None"
            id_caption = (
                f"👤 <b>Name:</b> {first_name}\n"
                f"🆔 <b>User ID:</b> <code>{target_id}</code> (tap to copy)\n"
                f"🦦 <b>Username:</b> {username}\n"
                f"🌏 <b>Chat ID:</b> <code>{event.chat_id}</code> (tap to copy)"
            )
            await event.reply(id_caption, parse_mode='html')
    except Exception as e:
        print(f"Error in /id: {e}")

# ---- MUTE / UNMUTE / BAN / UNBAN / KICK ----
@bot1.on(events.NewMessage(pattern=r'^/mute(?:@\w+)?(?:\s+(.+))?'))
async def permanent_ascii_mute_handler(event):
    if not await check_admin(event.chat_id, event.sender_id): return
    target_id = None
    args_text = event.pattern_match.group(1)
    if event.is_reply:
        reply_msg = await event.get_reply_message()
        target_id = reply_msg.sender_id
    else:
        if args_text:
            target_str = args_text.strip().split()[0]
            if target_str.isdigit():
                target_id = int(target_str)
            else:
                try:
                    user_entity = await event.client.get_entity(target_str)
                    target_id = user_entity.id
                except Exception:
                    await event.reply("⚠️ User not found.")
                    return
        else:
            await event.reply("⚠️ Usage: <code>/mute</code> (reply) or <code>/mute [@username]</code>")
            return
    if not target_id: return
    bot_me = await event.client.get_me()
    if target_id == bot_me.id or target_id == OWNER_ID:
        await event.reply("❌ Cannot mute the bot or the owner.")
        return
    try:
        await event.client.edit_permissions(
            event.chat_id, target_id,
            send_messages=False, send_media=False, send_stickers=False, send_gifs=False
        )
        user_entity = await event.client.get_entity(target_id)
        target_name = f"{user_entity.first_name} {user_entity.last_name or ''}".strip()
        await event.reply(f"<b>MUTE OPERATION SUCCESS!</b>\n<b>{target_name}</b> has been silenced <b>Permanently</b>.", parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ Error: {str(e)}")

@bot1.on(events.NewMessage(pattern=r'^/unmute(?:@\w+)?(?:\s+(.*))?'))
async def unmute_user(event):
    if not await check_ban_rights(event.chat_id, event.sender_id): return
    target_user = await get_target_user(event, event.pattern_match.group(1))
    if not target_user: return
    try:
        await bot1.edit_permissions(event.chat_id, target_user.id, send_messages=True)
        await muted_registry_col.delete_one({"chat_id": event.chat_id, "user_id": target_user.id})
        target_name = f"{getattr(target_user, 'first_name', 'User')} {getattr(target_user, 'last_name', '') or ''}".strip()
        mention = f"<a href='tg://user?id={target_user.id}'>{escape_html(target_name)}</a>"
        await event.reply(f"🌌 <b>UNMUTE OPERATION</b>\n🔊 <b>Target:</b> {mention}\n⚡ <b>Status:</b> <code>Voice Restored</code>", parse_mode='html')
    except Exception as e: print(f"Unmute Error: {e}")

@bot1.on(events.NewMessage(pattern=r'^/ban(?:@\w+)?(?:\s+(.*))?'))
async def ban_user(event):
    if not await check_ban_rights(event.chat_id, event.sender_id): return
    target_user = await get_target_user(event, event.pattern_match.group(1))
    if not target_user: return
    try:
        await bot1.edit_permissions(event.chat_id, target_user.id, view_messages=False)
        mark_mod_action(event.chat_id, target_user.id)
        target_name = f"{getattr(target_user, 'first_name', 'User')} {getattr(target_user, 'last_name', '') or ''}".strip()
        mention = f"<a href='tg://user?id={target_user.id}'>{escape_html(target_name)}</a>"
        await event.reply(f"🌌 <b>BAN OPERATION</b>\n🚫 <b>Target:</b> {mention}\n⚡ <b>Status:</b> <code>Exiled / Perm-Banned</code>", parse_mode='html')
    except Exception as e: print(f"Ban Error: {e}")

@bot1.on(events.NewMessage(pattern=r'^/unban(?:@\w+)?(?:\s+(.*))?'))
async def unban_user(event):
    if not await check_ban_rights(event.chat_id, event.sender_id): return
    target_user = await get_target_user(event, event.pattern_match.group(1))
    if not target_user: return
    try:
        await bot1.edit_permissions(event.chat_id, target_user.id, view_messages=True)
        mark_mod_action(event.chat_id, target_user.id)
        target_name = f"{getattr(target_user, 'first_name', 'User')} {getattr(target_user, 'last_name', '') or ''}".strip()
        mention = f"<a href='tg://user?id={target_user.id}'>{escape_html(target_name)}</a>"
        await event.reply(f"🌌 <b>UNBAN OPERATION</b>\n✅ <b>Target:</b> {mention}\n⚡ <b>Status:</b> <code>Ban Lifted</code>", parse_mode='html')
    except Exception as e: print(f"Unban Error: {e}")

@bot1.on(events.NewMessage(pattern=r'^/kick(?:@\w+)?(?:\s+(.*))?'))
async def kick_user(event):
    if not await check_ban_rights(event.chat_id, event.sender_id): return
    target_user = await get_target_user(event, event.pattern_match.group(1))
    if not target_user: return
    try:
        await bot1.edit_permissions(event.chat_id, target_user.id, view_messages=False)
        await bot1.edit_permissions(event.chat_id, target_user.id, view_messages=True)
        mark_mod_action(event.chat_id, target_user.id)
        target_name = f"{getattr(target_user, 'first_name', 'User')} {getattr(target_user, 'last_name', '') or ''}".strip()
        mention = f"<a href='tg://user?id={target_user.id}'>{escape_html(target_name)}</a>"
        await event.reply(f"🌌 <b>KICK OPERATION</b>\n💨 <b>Target:</b> {mention}\n⚡ <b>Status:</b> <code>Removed / Kicked</code>", parse_mode='html')
    except Exception as e: print(f"Kick Error: {e}")

async def get_target_user(event, arg):
    if event.is_reply:
        reply_msg = await event.get_reply_message()
        return await event.client.get_entity(reply_msg.sender_id)
    if arg:
        try:
            return await event.client.get_entity(arg)
        except:
            await event.reply("⚠️ User not found.")
            return None
    await event.reply("⚠️ Reply to a user or provide username/ID.")
    return None

# ---- Post a character (full details + image) to CHARACTER_CHANNEL_ID ----
def _build_character_channel_caption(char_doc, is_new=True):
    limit_val = char_doc.get("spawn_limit")
    limit_text = "♾️ Infinite" if not limit_val else str(limit_val)
    artist = char_doc.get("artist")
    header = f('NEW CHARACTER ADDED') if is_new else f('CHARACTER')
    caption = (
        f"🆕 <b>{header}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>{f('ID')}:</b> <code>{escape_html(str(char_doc.get('char_id', '?')))}</code>\n"
        f"👤 <b>{f('Name')}:</b> <code>{escape_html(str(char_doc.get('name', '?')))}</code>\n"
        f"📂 <b>{f('Category')}:</b> <code>{escape_html(str(char_doc.get('category', '?')))}</code>\n"
        f"🏷️ <b>{f('Rarity')}:</b> {char_doc.get('rarity', '?')}\n"
        f"💎 <b>{f('Worth')}:</b> <code>{char_doc.get('currency_value', 0):,} MMK</code>\n"
        f"🎪 <b>{f('Event')}:</b> <code>{escape_html(str(char_doc.get('event') or 'General'))}</code>\n"
        f"🔁 <b>{f('Catch Limit')}:</b> <code>{limit_text}</code>\n"
    )
    if artist:
        caption += f"🎨 <b>{f('Artist')}:</b> <code>{escape_html(str(artist))}</code>\n"
    return caption

async def post_character_to_channel(char_doc, is_new=True):
    """Send one character's full details + image to CHARACTER_CHANNEL_ID.
    Raises on failure so callers (addchar / postallchars) can catch and report it."""
    if not CHARACTER_CHANNEL_ID:
        raise RuntimeError("CHARACTER_CHANNEL_ID env var not set")
    storage_id = char_doc.get("storage_msg_id")
    if not storage_id:
        raise RuntimeError("character has no stored media (storage_msg_id missing)")
    storage_msg = await bot1.get_messages(SPECIFIC_CONTROL_GROUP, ids=storage_id)
    if not storage_msg or not storage_msg.media:
        raise RuntimeError("stored media not found (deleted from storage group?)")
    caption = _build_character_channel_caption(char_doc, is_new=is_new)
    await bot1.send_file(CHARACTER_CHANNEL_ID, file=storage_msg.media, caption=caption, parse_mode='html')

# ---- ADD CHARACTER ----
@bot1.on(events.NewMessage(pattern=r'^/addchar(?:@\w+)?(?:\s+(.+))?'))
async def add_character(event):
    if event.sender_id != OWNER_ID: return
    input_text = event.pattern_match.group(1)
    if not input_text or '|' not in input_text:
        await event.reply(
            f"⚠️ <b>{f('Invalid format!')}</b>\n"
            f"📌 <b>{f('Usage')}:</b>\n"
            f"<code>/addchar Name | Category | Rarity_Number | Event | CatchLimit</code>\n"
            f"<i>(Reply to a media file. Event and CatchLimit are optional.)</i>\n\n"
            f"🔢 <b>{f('Rarity Tiers (1-6)')}:</b>\n"
            + "\n".join(
                f"<code>{num}</code> = {RARITY_NUM_MAP[num]['name']} (<code>{RARITY_NUM_MAP[num]['value']}</code> MMK)"
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
        return await event.reply(f"❌ <b>{f('Rarity must be 1-6')}</b>", parse_mode='html')
    if not event.is_reply:
        return await event.reply(f"❌ <b>{f('Reply to a media file')}</b>", parse_mode='html')
    event_name = parts[3].strip() if len(parts) > 3 and parts[3].strip() and parts[3].strip() != "-" else "General"
    spawn_limit = 0
    if len(parts) > 4 and parts[4].strip().lstrip('-').isdigit():
        spawn_limit = max(0, int(parts[4].strip()))
    reply_msg = await event.get_reply_message()
    if not reply_msg or not (reply_msg.photo or reply_msg.video or reply_msg.document):
        return await event.reply(f"❌ <b>{f('Valid media not found')}</b>", parse_mode='html')
    try:
        forwarded_msg = await send_safe_message(bot1, SPECIFIC_CONTROL_GROUP, "", file=reply_msg.media)
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
            "photo_phash": photo_phash
        }
        await characters_base_col.insert_one(character_data)
        await invalidate_character_caches()
        limit_text = "♾️ Infinite" if spawn_limit == 0 else str(spawn_limit)

        channel_note = ""
        if CHARACTER_CHANNEL_ID:
            try:
                await post_character_to_channel(character_data, is_new=True)
                await characters_base_col.update_one({"char_id": char_id}, {"$set": {"channel_posted": True}})
                channel_note = "\n📢 <b>Channel:</b> Posted ✅"
            except Exception as ce:
                await report_system_error(f"AddChar channel post ({char_id})", ce)
                channel_note = f"\n⚠️ <b>Channel:</b> Post failed — <code>{escape_html(str(ce))}</code>"
        else:
            channel_note = "\n⚠️ <b>Channel:</b> <code>CHARACTER_CHANNEL_ID</code> not set — skipped."

        await event.reply(
            f"🔥 <b>{f('DATABASE INJECTED')}</b>\n"
            f"🆔 <b>{f('Character ID')}:</b> <code>{char_id}</code>\n"
            f"👤 <b>{f('Name')}:</b> <code>{char_name}</code>\n"
            f"🏷️ <b>{f('Rarity')}:</b> {r_info['name']}\n"
            f"💎 <b>{f('Worth')}:</b> <code>{r_info['value']} MMK</code>\n"
            f"🎡 <b>Event:</b> <code>{escape_html(event_name)}</code>\n"
            f"🔁 <b>Catch Limit:</b> <code>{limit_text}</code>"
            f"{channel_note}",
            parse_mode='html'
        )
    except Exception as e:
        await event.reply(f"❌ <b>Database Inject Error</b>: <code>{escape_html(str(e))}</code>", parse_mode='html')

# ---- POST ALL CHARACTERS TO CHANNEL (backfill for characters added before this feature existed) ----
@bot1.on(events.NewMessage(pattern=r'^/postallchars(?:@\w+)?(?:\s+(\S+))?$'))
async def post_all_characters_handler(event):
    """Owner-only. Posts every character currently in the database to CHARACTER_CHANNEL_ID with
    full details + image — used once to backfill everything added via /addchar before the
    auto-post-on-add feature existed. 'confirm' posts only characters not yet posted;
    'all' re-posts literally everything, including ones already posted before."""
    if event.sender_id != OWNER_ID: return
    if not CHARACTER_CHANNEL_ID:
        return await event.reply(
            "⚠️ <code>CHARACTER_CHANNEL_ID</code> env var မသတ်မှတ်ရသေးပါ။ "
            "Channel ID ကို env variable အနေနဲ့ ထည့်ပြီး bot ကို channel မှာ admin ခန့်ပေးပြီးမှ ပြန်စမ်းပါ။"
        )
    arg = (event.pattern_match.group(1) or "").lower()
    total_docs = await characters_base_col.count_documents({})
    not_yet_posted = await characters_base_col.count_documents({"channel_posted": {"$ne": True}})

    if arg not in ("confirm", "all"):
        return await event.reply(
            f"⚠️ <b>{f('POST ALL CHARACTERS TO CHANNEL')}</b>\n"
            f"🗄️ <b>{f('Total characters in DB')}:</b> <code>{total_docs}</code>\n"
            f"📭 <b>{f('Not yet posted')}:</b> <code>{not_yet_posted}</code>\n"
            f"📢 <b>{f('Target channel')}:</b> <code>{CHARACTER_CHANNEL_ID}</code>\n\n"
            f"This posts each character's full details + image to the channel, one by one "
            f"(this can take a while for hundreds of characters).\n\n"
            f"▶️ <code>/postallchars confirm</code> — post only the ones not yet posted\n"
            f"▶️ <code>/postallchars all</code> — re-post literally everything again",
            parse_mode='html'
        )

    force_all = arg == "all"
    query = {} if force_all else {"channel_posted": {"$ne": True}}
    docs = await characters_base_col.find(query).to_list(length=None)
    if not docs:
        return await event.reply("✅ Nothing to post — every character is already posted to the channel.")

    status_msg = await event.reply(
        f"⏳ <b>{f('Posting')} {len(docs)} {f('characters to the channel')}...</b>", parse_mode='html'
    )
    posted, failed = 0, 0
    for i, doc in enumerate(docs, 1):
        try:
            await post_character_to_channel(doc, is_new=False)
            await characters_base_col.update_one({"_id": doc["_id"]}, {"$set": {"channel_posted": True}})
            posted += 1
        except Exception as ce:
            failed += 1
            await report_system_error(f"PostAllChars ({doc.get('char_id', '?')})", ce)
        if i % 20 == 0 or i == len(docs):
            try:
                await status_msg.edit(
                    f"⏳ <b>{f('Progress')}:</b> <code>{i}/{len(docs)}</code>  "
                    f"(✅ <code>{posted}</code> · ❌ <code>{failed}</code>)",
                    parse_mode='html'
                )
            except Exception:
                pass
        await asyncio.sleep(1.2)  # pace sends to stay well under Telegram flood limits

    await status_msg.edit(
        f"✅ <b>{f('CHANNEL BACKFILL COMPLETE')}</b>\n"
        f"📤 <b>{f('Posted')}:</b> <code>{posted}/{len(docs)}</code>"
        + (f"\n❌ <b>{f('Failed')}:</b> <code>{failed}</code> <i>(check owner error log)</i>" if failed else ""),
        parse_mode='html'
    )

# ---- DELETE CHARACTER ----
@bot1.on(events.NewMessage(pattern=r'^/(delchar|removechar)(?:@\w+)?(?:\s+(\S+))?$'))
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
@bot1.on(events.NewMessage(pattern=r'^/editchar(?:@\w+)?(?:\s+(\S+))?$'))
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
    await event.reply(
        f"✅ <b>{escape_html(updated_doc['name'])}</b> (<code>{char_id}</code>) updated!\n"
        f"🎡 Event: <code>{escape_html(updated_doc.get('event','General'))}</code>\n"
        f"🔁 Catches remaining: <b>{remaining_text}</b>",
        parse_mode='html'
    )

# ---- ADD ARTIST ----
@bot1.on(events.NewMessage(pattern=r'^/addartist(?:@\w+)?(?:\s+(\S+)(?:\s+([\s\S]+))?)?$'))
async def add_artist_handler(event):
    if event.sender_id != OWNER_ID: return
    raw_id, artist_name = event.pattern_match.group(1), event.pattern_match.group(2)
    if not raw_id or not artist_name or not artist_name.strip():
        return await event.reply(
            "⚠️ <b>Usage:</b> <code>/addartist [CharID] [Artist Name]</code>\n"
            "<i>CharID works with or without the BOD prefix.</i>\n\n"
            "<b>Example:</b> <code>/addartist 1234 Morgan</code> "
            "<i>or</i> <code>/addartist BOD1234 Morgan</code>\n"
            "<i>Use</i> <code>-</code> <i>as the artist name to clear an existing credit.</i>",
            parse_mode='html'
        )
    char_id = normalize_char_id_input(raw_id)
    char_doc = await characters_base_col.find_one({"char_id": char_id})
    if not char_doc:
        return await event.reply(
            f"❌ <b>Makima:</b> No character found with ID <code>{escape_html(display_char_id(char_id))}</code>.",
            parse_mode='html'
        )
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
    await event.reply(
        f"⛓️ <b>Makima has noted this down.</b>\n"
        f"🆔 <b>Character:</b> <code>{display_char_id(char_id)}</code> — <b>{escape_html(char_doc['name'])}</b>\n"
        f"🎨 <b>Artist:</b> <code>{escape_html(artist_name)}</code>\n\n"
        f"<i>This credit will now show up anywhere this character card appears.</i>",
        parse_mode='html'
    )

# ---- SETCHAR ----
@bot1.on(events.NewMessage(pattern=r'^/setchar(?:@\w+)?(?:\s+([\s\S]+))?$'))
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

# ---- CHANGE ALL RARITY (rebrand rarity emoji/name everywhere, DB-wide) ----
def _current_official_rarity_name(old_rarity_str, fallback_tier=None):
    """Given whatever rarity string is currently stored (old emoji, old font, anything),
    work out its canonical tier and return today's official RARITY_NUM_MAP display name
    for it. Returns (new_name, tier) or (None, "OTHER") if it can't be classified at all."""
    tier = fallback_tier or classify_rarity(old_rarity_str)
    num = RARITY_TIER_TO_NUM.get(tier)
    if not num:
        return None, "OTHER"
    return RARITY_NUM_MAP[num]["name"], tier

@bot1.on(events.NewMessage(pattern=r'^/changeallrarity(?:@\w+)?(?:\s+(\S+))?$'))
async def change_all_rarity_handler(event):
    """Owner-only. Rewrites EVERY stored rarity name in the database (characters_base_data,
    characters, and every user's harem) to today's official RARITY_NUM_MAP name/emoji, so
    no old rarity name or emoji is left anywhere — old data included. Rarity_tier / value /
    who-owns-what is untouched; only the display string is rebranded. Run with 'confirm'."""
    if event.sender_id != OWNER_ID: return
    confirm_arg = (event.pattern_match.group(1) or "").lower()

    legend = "\n".join(
        f"<code>{num}</code> = {RARITY_NUM_MAP[num]['name']}" for num in sorted(RARITY_NUM_MAP.keys())
    )

    if confirm_arg != "confirm":
        return await event.reply(
            f"⚠️ <b>{f('CHANGE ALL RARITY')}</b>\n"
            f"This rewrites the <b>rarity</b> field on every character in the database "
            f"AND inside every player's harem to the current official name/emoji below. "
            f"Old rarity names/emoji will no longer appear anywhere.\n\n"
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
            new_name, tier = _current_official_rarity_name(doc.get("rarity", ""), doc.get("rarity_tier"))
            if not new_name:
                chars_skipped += 1
                continue
            ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": {"rarity": new_name, "rarity_tier": tier}}))
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
            new_name, _tier = _current_official_rarity_name(item.get("rarity", ""))
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

# ---- ADM ----
@bot1.on(events.NewMessage(pattern=r'^/adm(?:@\w+)?(?:\s+(.*))?'))
async def tag_admins(event):
    if event.is_private: return
    if not await check_admin(event.chat_id, event.sender_id): return
    text = event.pattern_match.group(1) or "Admins, Makima needs you! ⛓️"
    try:
        admins = await bot1.get_participants(event.chat_id, filter=ChannelParticipantsAdmins)
        admin_mentions = []
        for a in admins:
            if a.bot or getattr(a, 'deleted', False): continue
            full_name = f"{a.first_name or ''} {a.last_name or ''}".strip() or "Admin"
            mention = f"<a href='tg://user?id={a.id}'><b>{escape_html(full_name)}</b></a>"
            admin_mentions.append(mention)
        compact_admin_list = "  •  ".join(admin_mentions)
        await event.respond(bq(f"<b>{escape_html(text)}</b>\n\n⛓️ <b>Admins, please pay attention!</b>\n{compact_admin_list}"), parse_mode='html')
    except Exception as e:
        print(f"Admin Tag System Error: {e}")

# ---- BROADCAST ----
@bot1.on(events.NewMessage(pattern=r'^/send(?:@\w+)?(?:\s+(.*))?'))
async def broadcast(event):
    if event.sender_id != OWNER_ID: return
    reply_msg = await event.get_reply_message()
    command_text = event.pattern_match.group(1)
    if not reply_msg and not command_text: return
    success, fail = 0, 0
    status_msg = await event.respond(bq("⛓️ <b>BROADCAST INITIATED</b>"), parse_mode='html')
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
    await status_msg.edit(bq(f"⛓️ <b>BROADCAST COMPLETE</b>\n✅ <b>Success:</b> <code>{success}</code>\n❌ <b>Failed:</b> None"), parse_mode='html')
# ---- PROTECTION LOGIC (No Harem/Waifu deletion) ----
@bot1.on(events.NewMessage())
async def protection_logic(event):
    if event.is_private: return
    if not event.text or event.sender_id == OWNER_ID or event.sender_id in bot_ids: return
    
    text, chat_id, sender_id = event.text.strip(), event.chat_id, event.sender_id
    sender = await event.get_sender()
    
    # ❌ REMOVED: The /harrrem and /waaaifu detection block is GONE.
    # Those commands will now be processed normally (if they are valid commands) or ignored.
    
    # ---- BIO Keywords Filter (still active) ----
    if text:
        text_lower = text.lower()
        BIO_KEYWORDS = ["bio", "b i o", "biolink", "bio-link", "b-i-o", "tg bio", 
                        "ဘိုင်အို", "ဘိုင်-အို", "ဘိုင်-o", "ဘီအိုင်အို", "b.i.o", 
                        "ဘိုင်အိုလင့်", "ဘိုင်အိုလင့်ခ်"]
        if any(key in text_lower for key in BIO_KEYWORDS) and not await check_admin(chat_id, sender_id):
            try:
                await event.delete()
                first_name = sender.first_name if sender else "User"
                mention = f"<a href='tg://user?id={sender_id}'>{escape_html(first_name)}</a>"
                await event.respond(bq(f"⚠️ <b>BIO ALERT</b>\n{mention}, bio links are not allowed.\n⚡ <b>Status:</b> <blockquote expandable>Message deleted 🗑️</blockquote>"), parse_mode='html')
                return
            except Exception as e:
                print(f"Bio Filter Error: {e}")
    
    # ---- Links Filter ----
    urls = re.findall(r'(https?://\S+|www\.\S+)', text)
    if urls and not (event.audio or event.voice):
        if await check_admin(chat_id, sender_id):
            try:
                f_msg = await bot1.forward_messages(event.chat_id, event.message)
                mention = f"<a href='tg://user?id={sender_id}'>{escape_html(sender.first_name)}</a>"
                await f_msg.reply(f"<b>Link posted by Admin {mention}</b>", parse_mode='html')
                await event.delete()
            except:
                pass
        else:
            try:
                await event.delete()
                mention = f"<a href='tg://user?id={sender_id}'>{escape_html(sender.first_name)}</a>"
                await event.respond(bq(f"🔗 {mention}, no links allowed! Deleted."), parse_mode='html')
            except:
                pass
                
# ---- SHADOW BAN ----
@bot1.on(events.NewMessage(pattern=r'^/shadow(?:@\w+)?$'))
async def set_shadow_ban(event):
    if not await is_allowed(event.sender_id): return
    reply = await event.get_reply_message()
    if not reply or reply.sender_id == OWNER_ID: return
    dark_passenger_targets[event.chat_id] = reply.sender_id
    await event.respond(bq("Shadow ban activated."))

@bot1.on(events.NewMessage(pattern=r'^/unshadow(?:@\w+)?$'))
async def remove_shadow_ban(event):
    if not await is_allowed(event.sender_id): return
    if event.chat_id in dark_passenger_targets:
        del dark_passenger_targets[event.chat_id]
        await event.respond(bq("Shadow ban removed."))

@bot1.on(events.NewMessage())
async def dark_passenger_watcher(event):
    chat_id = event.chat_id
    sender_id = event.sender_id
    if chat_id in dark_passenger_targets and sender_id == dark_passenger_targets[chat_id]:
        if event.text and not event.text.startswith(('L', 'S', 'မှတ်')):
            try:
                await event.delete()
                sender = await event.get_sender()
                mention = f"<a href='tg://user?id={sender_id}'>{escape_html(sender.first_name if sender else 'Subject')}</a>"
                insult_msg = await event.respond(bq(f"🤫 <b>SHADOW BAN</b>\n{mention}, your message has been erased."), parse_mode='html')
                await asyncio.sleep(4.0)
                await insult_msg.delete()
            except: pass

# ---- LANGUAGE & FORWARD FILTER ----
FORBIDDEN_SCRIPTS = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\u3040-\u309f\u30a0-\u30ff\u0e00-\u0e7f\u0600-\u06ff\uac00-\ud7af]')

@bot1.on(events.NewMessage(incoming=True))
async def global_traffic_processing_matrix(event):
    if event.is_private or not event.text: return
    if event.sender_id == OWNER_ID or event.sender_id in bot_ids: return
    chat_id = event.chat_id
    sender_id = event.sender_id
    if chat_id == SPECIFIC_GROUP and not await check_admin(chat_id, sender_id):
        if FORBIDDEN_SCRIPTS.search(event.text):
            try:
                await event.delete()
                sender = await event.get_sender()
                mention = f"<a href='tg://user?id={sender_id}'>{escape_html(sender.first_name if sender else 'User')}</a>"
                await event.respond(bq(f"⚠️ <b>LANGUAGE SECURITY</b>\n{mention}, only <b>Burmese, English</b> and <b>Numbers</b> are allowed. Message deleted."), parse_mode='html')
            except Exception: pass
    if event.fwd_from and isinstance(event.fwd_from.from_id, types.PeerChannel):
        if not await check_admin(chat_id, sender_id):
            try:
                await event.delete()
                sender = await event.get_sender()
                mention = f"<a href='tg://user?id={sender_id}'>{escape_html(sender.first_name if sender else 'User')}</a>"
                await event.respond(bq(f"🚫 <b>CHANNEL FORWARD NOT ALLOWED!</b>\n{mention} <b>[Deleted]</b>"), parse_mode='html')
            except Exception: pass

# ---- SMART REPLY TOGGLES ----
@bot1.on(events.NewMessage(pattern=r'^/ktr(?:@\w+)?(?:\s+(\d+))?$'))
async def turn_on_reply_save_match(event):
    global IS_REPLY_ACTIVE, REPLY_INTERVAL
    if event.sender_id != OWNER_ID: return
    IS_REPLY_ACTIVE = True
    interval_arg = event.pattern_match.group(1)
    if interval_arg: REPLY_INTERVAL = int(interval_arg)
    await event.reply(bq(f"⛓️ <b>REPLY ENGINE ACTIVATED</b>\nEvery <code>{REPLY_INTERVAL}</code> messages."), parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/ktrr(?:@\w+)?$'))
async def turn_off_reply_save_match(event):
    global IS_REPLY_ACTIVE
    if event.sender_id != OWNER_ID: return
    IS_REPLY_ACTIVE = False
    await event.reply(bq("⛔ Reply engine offline."), parse_mode='html')

async def processed_background_reply(event, user_text, as_reply=True):
    chat_entity = await event.get_input_chat()
    smart_reply = await fetch_optimized_smart_reply(user_text)
    if smart_reply:
        try:
            async with bot1.action(chat_entity, 'typing'):
                await asyncio.sleep(random.uniform(0.5, 1.5))
            if as_reply: await event.reply(smart_reply, parse_mode='html')
            else: await event.respond(smart_reply, parse_mode='html')
        except FloodWaitError as e: await asyncio.sleep(e.seconds + 1)
        except Exception: pass

@bot1.on(events.NewMessage(incoming=True))
async def reply_save_counter_handler(event):
    global reply_msg_counters, IS_REPLY_ACTIVE, REPLY_INTERVAL
    if not IS_REPLY_ACTIVE: return
    if event.is_private or not event.text or event.text.startswith(('/', '.', 'မှတ်', '@')): return
    sender = await event.get_sender()
    if sender and sender.bot: return
    chat_id = event.chat_id
    reply_msg_counters[chat_id] = reply_msg_counters.get(chat_id, 0) + 1
    if reply_msg_counters[chat_id] >= REPLY_INTERVAL:
        reply_msg_counters[chat_id] = 0
        asyncio.create_task(processed_background_reply(event, event.text.strip(), as_reply=True))

@bot1.on(events.NewMessage(pattern=r'^/giveall(?:@\w+)?\s+(\d+)'))
async def give_all_players_mmk(event):
    if event.sender_id != OWNER_ID: return
    amount = int(event.pattern_match.group(1))
    try:
        result = await users_catcher_col.update_many({}, {"$inc": {"wallet_balance": amount}})
        await event.reply(f"🎁 <b>GLOBAL BOUNTY</b>\nMakima gave <code>{amount:,} MMK</code> to every player. Total: <code>{result.modified_count}</code>", parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ Error: <code>{e}</code>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/give(?:@\w+)?\s+(\d+)'))
async def give_money_to_user(event):
    if event.sender_id != OWNER_ID: return
    if not event.is_reply:
        return await event.reply("⚠️ Reply to the user you want to give money to.", parse_mode='html')
    amount = int(event.pattern_match.group(1))
    reply_msg = await event.get_reply_message()
    target_user_id = reply_msg.sender_id
    target_mention = await get_html_mention(event, target_user_id)
    target_plain_name = await get_plain_name(event, target_user_id)
    await ensure_user_registered(target_user_id, target_plain_name)
    try:
        await users_catcher_col.update_one({"user_id": target_user_id}, {"$inc": {"wallet_balance": amount}, "$set": {"fullname": target_plain_name}})
        await event.reply(f"💸 {target_mention} received <code>{amount:,} MMK</code> as instructed by Makima.", parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ Error: <code>{e}</code>", parse_mode='html')

# ---- FORCE SPAWN ----
@bot1.on(events.NewMessage(pattern=r'^/(fspawn|haii)(?:@\w+)?$'))
async def force_spawn_by_owner(event):
    if event.sender_id != OWNER_ID: return
    await trigger_dynamic_spawn(event.chat_id)

# ---- AUTOMATIC SPAWN PROCESSOR ----
@bot1.on(events.NewMessage(incoming=True))
async def global_message_counter_handler(event):
    if event.is_private or event.chat_id == SPECIFIC_CONTROL_GROUP: return
    chat_id = event.chat_id
    if chat_id in active_group_spawns or chat_id in pending_rarity_quiz: return
    group_config = await groups_config_col.find_one({"chat_id": chat_id})
    if group_config and "spawn_target" in group_config:
        spawn_target = group_config["spawn_target"]
    else:
        global_config = await groups_config_col.find_one({"chat_id": GLOBAL_SPAWN_CHAT_KEY})
        spawn_target = global_config.get("spawn_target", 50) if global_config else 50
    counter_doc = await groups_counters_col.find_one_and_update(
        {"chat_id": chat_id},
        {"$inc": {"counter": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    if counter_doc and counter_doc.get("counter", 0) >= spawn_target:
        await groups_counters_col.update_one({"chat_id": chat_id}, {"$set": {"counter": 0}})
        await trigger_dynamic_spawn(chat_id)

# ---- TRIGGER DYNAMIC SPAWN ----
async def trigger_dynamic_spawn(chat_id):
    if chat_id in active_group_spawns or chat_id in pending_rarity_quiz: return
    try:
        characters_list = await get_all_characters_cached()
        if not characters_list: return
        eligible_characters = []
        for char in characters_list:
            limit = char.get("spawn_limit", 0)
            # spawn_count now represents number of catches, so compare against limit
            if limit and limit > 0 and char.get("spawn_count", 0) >= limit:
                continue
            eligible_characters.append(char)
        if not eligible_characters:
            return
        RARITY_WEIGHTS = {"LEGENDARY": 3, "LIMITED": 5, "MYTHIC": 8, "EPIC": 15, "RARE": 25, "COMMON": 40}
        weights = []
        for char in eligible_characters:
            tier = char.get("rarity_tier") or classify_rarity(char.get("rarity", ""))
            weights.append(RARITY_WEIGHTS.get(tier, 20))
        chosen_char = random.choices(eligible_characters, weights=weights, k=1)[0]
        tier = chosen_char.get("rarity_tier") or classify_rarity(chosen_char.get("rarity", ""))
        if tier in RARITY_GATE_TIERS:
            # Rarity No.1/No.2 — don't spawn directly, gate it behind a quiz first.
            await start_rarity_gate_quiz(chat_id, chosen_char)
        else:
            await release_spawn(chat_id, chosen_char)
    except Exception as e:
        print(f"Spawn Error Tracker: {e}")

async def release_spawn(chat_id, chosen_char):
    """Actually post the spawn message + open the /who window for chosen_char. Used both for
    normal spawns and for LEGENDARY/LIMITED spawns once their quiz gate has been solved."""
    if chat_id in active_group_spawns: return
    try:
        # Do NOT increment spawn_count here; it will be incremented on successful catch.
        storage_msg = await bot1.get_messages(SPECIFIC_CONTROL_GROUP, ids=chosen_char["storage_msg_id"])
        if not storage_msg or not storage_msg.media: return
        rarity_display = chosen_char.get("rarity", "Unknown")
        event_name = chosen_char.get("event", "General")
        event_display = escape_html(event_name) if event_name and event_name != "General" else "➖"
        spawn_msg = await bot1.send_message(
            chat_id,
            f"🫧 <b>A new chain has surfaced. Makima has brought someone here.</b>\n\n"
            f"🏷️ <b>Rarity:</b> {rarity_display}\n"
            f"🎡 <b>Event:</b> {event_display}\n\n"
            f"➠➠ Reply with <code>/who</code> to reveal!",
            parse_mode='html',
            file=storage_msg.media
        )

        active_group_spawns[chat_id] = {
            "spawn_msg_id": spawn_msg.id,
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
    except Exception as e:
        print(f"Spawn Error Tracker: {e}")

# ---- RARITY GATE QUIZ (No.1 LEGENDARY / No.2 LIMITED only) ----
async def start_rarity_gate_quiz(chat_id, chosen_char):
    """Post a 4-option quiz for a LEGENDARY/LIMITED pick. The character itself is NOT
    catchable yet — only the first person to tap the correct answer unlocks release_spawn()."""
    try:
        q = random.choice(RARITY_QUIZ_BANK)
        shuffled_options = q["options"][:]
        correct_answer_text = shuffled_options[q["correct_index"]]
        random.shuffle(shuffled_options)
        correct_index = shuffled_options.index(correct_answer_text)

        media = None
        try:
            storage_msg = await bot1.get_messages(SPECIFIC_CONTROL_GROUP, ids=chosen_char["storage_msg_id"])
            if storage_msg and storage_msg.media:
                media = storage_msg.media
        except Exception:
            pass

        rarity_display = chosen_char.get("rarity", "Unknown")
        quiz_text = (
            f"🔐 <b>HIGH-RARITY GATE ENGAGED</b>\n"
            f"🏷️ <b>Rarity:</b> {rarity_display}\n\n"
            f"⛓️ <b>Makima won't let this one through unless someone proves themselves.</b>\n"
            f"❓ <b>{escape_html(q['question'])}</b>\n\n"
            f"⏳ <b>{RARITY_GATE_TIMEOUT_SECONDS} seconds</b> — first correct tap unlocks the spawn!"
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
        }
        asyncio.create_task(rarity_gate_quiz_timeout_watcher(chat_id, sent.id))
    except Exception as e:
        print(f"Rarity Gate Quiz Error: {e}")

@bot1.on(events.CallbackQuery(pattern=r'^rgate_(-?\d+)_(\d)$'))
async def rarity_gate_answer_callback(event):
    chat_id = int(event.pattern_match.group(1))
    chosen_idx = int(event.pattern_match.group(2))
    quiz = pending_rarity_quiz.get(chat_id)
    if not quiz or quiz.get("solved"):
        return await event.answer("⌛ This gate is already closed.", alert=True)
    if time.time() - quiz["quiz_time"] > RARITY_GATE_TIMEOUT_SECONDS:
        return await event.answer("⌛ Time's up already!", alert=True)
    if chosen_idx != quiz["correct_index"]:
        return await event.answer("❌ Wrong answer! Try again.", alert=True)
    async with spawn_locks[chat_id]:
        quiz = pending_rarity_quiz.get(chat_id)
        if not quiz or quiz.get("solved"):
            return await event.answer("⌛ Someone already solved it!", alert=True)
        quiz["solved"] = True
        winner_id = event.sender_id
    mention = await get_html_mention(event, winner_id)
    try:
        await event.edit(
            f"🔓 <b>GATE UNLOCKED by {mention}!</b>\n"
            f"✅ <b>Correct answer:</b> {escape_html(quiz['options'][quiz['correct_index']])}\n\n"
            f"🫧 <i>Makima is bringing them through now...</i>",
            parse_mode='html',
            buttons=None
        )
    except Exception:
        pass
    await event.answer("✅ Correct! Releasing the spawn...", alert=True)
    if pending_rarity_quiz.get(chat_id) is quiz:
        del pending_rarity_quiz[chat_id]
    await release_spawn(chat_id, quiz["char"])

async def rarity_gate_quiz_timeout_watcher(chat_id, msg_id):
    await asyncio.sleep(RARITY_GATE_TIMEOUT_SECONDS)
    quiz = pending_rarity_quiz.get(chat_id)
    if not quiz or quiz.get("solved") or quiz.get("msg_id") != msg_id:
        return
    del pending_rarity_quiz[chat_id]
    try:
        await bot1.edit_message(
            chat_id, msg_id,
            f"⏰ <b>Nobody solved it in time. Makima took them back.</b>\n"
            f"✅ <b>The correct answer was:</b> {escape_html(quiz['options'][quiz['correct_index']])}",
            parse_mode='html',
            buttons=None
        )
    except Exception:
        pass

# ==========================================
# 🔎 IDENTIFY VIA /who ON A SAVED/REPOSTED PHOTO OR VIDEO — if a character's original media
# (added through /addchar) was saved to someone's gallery and later sent back — as a photo/
# video WITH "/who" as the caption, or as a plain photo/video that then gets replied to with
# /who — Makima recognizes it via perceptual hash and shows name/id/rarity + a /catch hint.
# Works in groups AND in the bot's DM. INFO ONLY: never touches active_group_spawns, so it
# never makes anything catchable by itself — /catch still only works against a real live spawn.
# ==========================================
IDENTIFY_HAMMING_THRESHOLD = 10   # lower = stricter match. 0-64 possible for a 64-bit dHash.
IDENTIFY_COOLDOWN_SECONDS = 5     # per-user, so repeated attempts don't spam replies

async def _identify_media_and_reply(event, media_msg):
    user_id = event.sender_id
    is_cd, _ = await is_on_cooldown(user_id, "identify_repost", IDENTIFY_COOLDOWN_SECONDS)
    if is_cd:
        return
    try:
        incoming_hash = await compute_phash_for_message(media_msg)
    except Exception as e:
        print(f"identify hash error: {e}")
        return
    if not incoming_hash:
        return await event.reply("❓ <b>Couldn't read that file, try sending it again.</b>", parse_mode='html')
    characters_list = await get_all_characters_cached()
    best_match, best_distance = None, IDENTIFY_HAMMING_THRESHOLD + 1
    for char in characters_list:
        char_hash = char.get("photo_phash")
        if not char_hash:
            continue
        dist = hamming_distance(incoming_hash, char_hash)
        if dist < best_distance:
            best_distance, best_match = dist, char
    if not best_match:
        return await event.reply("❓ <b>Makima doesn't recognize this one.</b>", parse_mode='html')
    try:
        rarity_tier = classify_rarity(best_match.get("rarity", ""))
        rarity_emoji = RARITY_EMOJI.get(rarity_tier, RARITY_DEFAULT_EMOJI)
        event_display = escape_html(best_match.get("event") or "") if best_match.get("event", "General") != "General" else "➖"
        reveal_text = (
            f"🔎 <b>Makima recognizes this one.</b>\n\n"
            f"⛓️ <b>Name:</b> <b>{escape_html(best_match['name'])}</b>\n"
            f"🔖 <b>Chain ID:</b> <code>{display_char_id(best_match['char_id'])}</code>\n"
            f"{rarity_emoji} <b>Rarity:</b> {best_match.get('rarity', '?')}\n"
            f"🎡 <b>Event:</b> {event_display}\n"
            f"{artist_line(best_match)}\n"
            f"<code>/catch {escape_html(best_match['name'])}</code>\n"
            f"<i>Only catchable if this one is actually live right now — this is just a lookup.</i>"
        )
        await event.reply(reveal_text, parse_mode='html')
    except Exception as e:
        print(f"identify reply error: {e}")

# ---- /who (aliases: /w, /waifu) ----
@bot1.on(events.NewMessage(pattern=r'^/(?:who|w|waifu)(?:@\w+)?$'))
async def who_reveal_handler(event):
    user_id = event.sender_id
    # Check if user is spam-muted
    if user_id in user_mute_until and time.time() < user_mute_until[user_id]:
        return await event.reply("⛓️ You're currently muted from using /who. Please wait a few minutes.")

    chat_id = event.chat_id

    # ---- Path A: replying to the CURRENT live spawn message in this group — original,
    # unchanged flow (expiry/claimed guards, catch button, etc.) ----
    if not event.is_private and chat_id in active_group_spawns:
        spawn_data = active_group_spawns[chat_id]
        if event.is_reply and event.reply_to_msg_id == spawn_data["spawn_msg_id"]:
            if time.time() - spawn_data["spawn_time"] > 300:
                del active_group_spawns[chat_id]
                return await event.reply(
                    "⏳ <b>Too slow. Makima called them back. Try again later.</b>",
                    parse_mode='html'
                )
            try:
                char_name = spawn_data['name']
                reveal_text = (
                    f"🫧 <b>Makima says: I've brought someone new for you.</b>\n\n"
                    f"<code>/catch {escape_html(char_name)}</code>\n\n"
                    f"✨ <b>Ready to catch?</b>\n"
                )
                try:
                    # ✅ Button ကိုနှိပ်လိုက်ရင် Input Box ထဲ /catch {char_name} ထည့်ပေးမယ်
                    buttons = [
                        [Button.switch_inline(
                            text=f"🐛 Catch {char_name} Now!",
                            query=f"/catch {char_name}",
                            same_peer=True
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
                    "❌ <b>Oops! Makima's records are incomplete. Please try again later.</b>",
                    parse_mode='html'
                )
            except Exception as e:
                error_msg = f"General error in /who: {e}\nSpawn data: {spawn_data}"
                print(error_msg)
                await report_system_error("who_reveal_handler", error_msg)
                await event.reply(
                    "❌ <b>Something went wrong while revealing! Makima is correcting it...</b>",
                    parse_mode='html'
                )
            return

    # ---- Path B: identify a saved/reposted photo or video — works in groups AND DM.
    # Triggers if /who was sent AS the caption on the media, or as a reply to a message
    # that carries a photo/video. ----
    media_msg = None
    if event.photo or event.video:
        media_msg = event.message
    elif event.is_reply:
        replied = await event.get_reply_message()
        if replied and (replied.photo or replied.video):
            media_msg = replied
    if media_msg is not None:
        return await _identify_media_and_reply(event, media_msg)

    # ---- Path C: nothing to show ----
    if event.is_private:
        return await event.reply(
            "⛓️ <b>Send a photo/video with /who as the caption, or reply to one with /who, to identify it.</b>",
            parse_mode='html'
        )
    if chat_id not in active_group_spawns:
        return await event.reply(
            "⛓️ <b>Nothing here yet... Makima hasn't sent anyone your way.</b>",
            parse_mode='html'
        )
    return await event.reply(
        "📌 <b>Hey! Reply directly to the spawn message to reveal the buddy — "
        "or reply to a saved photo/video with /who to identify it.</b>",
        parse_mode='html'
    )

# ---- CATCH LOGIC (with daily limit and spawn_count increment on success) ----
async def perform_catch(chat_id, user_id, spawn_data, event, reply_to_msg=None, is_callback=False, temp_msg_id=None):
    if chat_id not in active_group_spawns:
        if is_callback:
            await event.answer("🛸 Nothing to catch here!", alert=True)
        else:
            await event.reply("🛸 <b>Nothing to catch here!</b>", parse_mode='html')
        return False
    if spawn_data["claimed"]:
        if is_callback:
            await event.answer("❌ Already caught!", alert=True)
        else:
            await event.reply("❌ <b>This one has already been caught!</b>", parse_mode='html')
        return False
    if time.time() - spawn_data["spawn_time"] > 600:
        if chat_id in active_group_spawns: del active_group_spawns[chat_id]
        if is_callback:
            await event.answer("⏱️ Too late!", alert=True)
        else:
            await event.reply("⏱️ <b>Too late! They've slipped away.</b>", parse_mode='html')
        return False

    # Check daily limit
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    if user_doc:
        today = datetime.now(TZ).date()
        last_catch_date = user_doc.get("last_catch_date")
        if last_catch_date:
            last_date = last_catch_date.date() if isinstance(last_catch_date, datetime) else last_catch_date
            if last_date != today:
                # reset daily catches
                await users_catcher_col.update_one(
                    {"user_id": user_id},
                    {"$set": {"daily_catches": 0, "last_catch_date": datetime.now(TZ)}}
                )
                user_doc["daily_catches"] = 0
        daily_catches = user_doc.get("daily_catches", 0)
        if daily_catches >= 22:
            mention = await get_html_mention(event, user_id)
            await event.reply(
                f"⛓️ <b>Makima says:</b> {mention}, you've reached your daily catch limit of 22! "
                f"That's enough for today. Come back tomorrow. 🌟",
                parse_mode='html'
            )
            return False

    async with spawn_locks[chat_id]:
        if active_group_spawns.get(chat_id, {}).get("claimed", True):
            if is_callback:
                await event.answer("❌ Already caught by someone else!", alert=True)
            else:
                await event.reply("❌ <b>Already caught by someone else!</b>", parse_mode='html')
            return False
        active_group_spawns[chat_id]["claimed"] = True
        mention = await get_html_mention(event, user_id)
        plain_name = await get_plain_name(event, user_id)
        await ensure_user_registered(user_id, plain_name)
        try:
            # Update user: add card, increment total, balance, group catches, and daily catches
            await users_catcher_col.update_one(
                {"user_id": user_id},
                {
                    "$push": {
                        "harem": {
                            "char_id": spawn_data['char_id'],
                            "caught_date": time.time(),
                            "rarity": spawn_data['rarity'],
                            "status": "vault"
                        }
                    },
                    "$inc": {
                        "total_caught": 1,
                        "wallet_balance": spawn_data["value"],
                        f"group_catches.{str(chat_id)}": 1,
                        "daily_catches": 1
                    },
                    "$set": {"fullname": plain_name, "last_catch_date": datetime.now(TZ)}
                },
                upsert=True
            )
            # ✅ Increment spawn_count ONLY on successful catch
            await characters_base_col.update_one(
                {"char_id": spawn_data['char_id']},
                {"$inc": {"spawn_count": 1}}
            )
            # 📈 Catching a character is a demand signal — nudge its stock price up a little.
            stock_bump_pct = await nudge_stock_on_catch(spawn_data['char_id'], spawn_data['rarity'])
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
            newly_earned = await check_and_award_achievements(user_id)
            raw_event_name = spawn_data.get('event')
            event_display = escape_html(raw_event_name) if raw_event_name and raw_event_name != "General" else "➖"
            # ... (catch logic code အတွင်းမှာ)
            
# ✅ Rarity အတွက် သက်ဆိုင်ရာ Emoji ကိုယူပါ
            rarity_tier = classify_rarity(spawn_data['rarity'])
            rarity_emoji = RARITY_EMOJI.get(rarity_tier, RARITY_DEFAULT_EMOJI)

# ✅ Event ကို Name နောက်မှာ ထည့်ပါ
            event_display = escape_html(raw_event_name) if raw_event_name and raw_event_name != "General" else ""

            success_msg = (
                f"🦄 <b>Good. Another one now belongs to you.</b>\n\n"
                f"🐬 <b>Trainer:</b> {mention}\n"
                f"⛓️ <b>New Buddy:</b> <b>{escape_html(character_name)}</b>{' (' + event_display + ')' if event_display else ''}\n"
                f"🔖 <b>Chain ID:</b> <code>{display_char_id(spawn_data['char_id'])}</code>\n"
                f"{rarity_emoji} <b>Rarity:</b> {spawn_data['rarity']}\n"
                f"{artist_line(spawn_data.get('artist'))}"
                f"💰 <b>Coins Earned:</b> <b>+{spawn_data['value']} MMK</b>\n"
                f"📈 <b>Stock Demand:</b> <code>+{stock_bump_pct * 100:.1f}%</code> <i>(check </i><code>/stock {display_char_id(spawn_data['char_id'])}</code><i>)</i>\n\n"
                f"🎒 <b>Check your collection anytime with</b> <code>/harem</code>! ⛓️"
)

   
            if guild_levelup_msg:
                success_msg += guild_levelup_msg
            success_msg += format_achievement_unlocks(newly_earned)
            if temp_msg_id:
                try:
                    await bot1.delete_messages(chat_id, temp_msg_id)
                except:
                    pass
            del active_group_spawns[chat_id]
            if chat_id in spawn_locks: del spawn_locks[chat_id]
            if is_callback:
                await bot1.send_message(chat_id, success_msg, reply_to=reply_to_msg or event.message_id, parse_mode='html')
            else:
                await event.reply(success_msg, parse_mode='html')
            return True
        except Exception as e:
            if chat_id in active_group_spawns:
                active_group_spawns[chat_id]["claimed"] = False
            error_msg = f"❌ <b>Catch Logic Fault:</b> {e}"
            if is_callback:
                await bot1.send_message(chat_id, error_msg, parse_mode='html')
            else:
                await event.reply(error_msg, parse_mode='html')
            return False

CATCH_USAGE_TEXT = (
    "📌 <b>Usage:</b> <code>/catch [Character Name]</code>\n"
    "<i>Reply with /who to reveal a spawn, then use the exact name shown, "
    "or just tap the inline button.</i>"
)

@bot1.on(events.NewMessage(pattern=r'^/catch(?:@\w+)?\s+(.*)$'))
async def catch_handler(event):
    if event.is_private: return
    user_id = event.sender_id
    # Check spam mute
    if user_id in user_mute_until and time.time() < user_mute_until[user_id]:
        return await event.reply("⛓️ You're currently muted from catching. Please wait a few minutes.")
    
    chat_id = event.chat_id
    catch_name = event.pattern_match.group(1).strip()
    if not catch_name:
        return await event.reply(CATCH_USAGE_TEXT, parse_mode='html')

    # ==========================================
    # 🎁 CHECK HAIDO EVENT FIRST
    # ==========================================
    if chat_id in active_haido_events:
        haido_data = active_haido_events[chat_id]
        
        # Check if already claimed
        if haido_data["claimed"]:
            return await event.reply("❌ <b>Makima:</b> ဒီ Haido ကတ်ကို ရသွားပြီး!", parse_mode='html')
        
        # Check if name matches
        if normalize_name(catch_name) == normalize_name(haido_data["char_name"]):
            # Check if time is still valid
            if time.time() - haido_data["spawn_time"] > HAIDO_TIMEOUT_SECONDS:
                # Expired - clean up
                if chat_id in active_haido_events:
                    del active_haido_events[chat_id]
                return await event.reply("⏰ <b>Makima:</b> အချိန်ကုန်သွားပါပြီ!", parse_mode='html')
            
            # ✅ Claim the Haido card!
            haido_data["claimed"] = True
            haido_data["claimed_by"] = user_id
            
            mention = await get_html_mention(event, user_id)
            plain_name = await get_plain_name(event, user_id)
            await ensure_user_registered(user_id, plain_name)
            
            # Add to user's harem
            await users_catcher_col.update_one(
                {"user_id": user_id},
                {
                    "$push": {
                        "harem": {
                            "char_id": haido_data["char_id"],
                            "caught_date": time.time(),
                            "rarity": haido_data["rarity"],
                            "status": "vault"
                        }
                    },
                    "$inc": {
                        "total_caught": 1,
                        "wallet_balance": 1000,  # Bonus for Haido
                        f"group_catches.{str(chat_id)}": 1
                    },
                    "$set": {"fullname": plain_name, "last_catch_date": datetime.now(TZ)}
                },
                upsert=True
            )
            
            # Increment spawn count
            await characters_base_col.update_one(
                {"char_id": haido_data["char_id"]},
                {"$inc": {"spawn_count": 1}}
            )
            
            # Record in history
            try:
                await haido_history_col.insert_one({
                    "chat_id": chat_id,
                    "char_id": haido_data["char_id"],
                    "char_name": haido_data["char_name"],
                    "rarity": haido_data["rarity"],
                    "rarity_tier": haido_data["rarity_tier"],
                    "claimed_by": user_id,
                    "claimed": True,
                    "claimed_name": mention,
                    "timestamp": time.time()
                })
            except Exception as e:
                print(f"Haido history insert error: {e}")
            
            # Send success message
            rarity_emoji = RARITY_EMOJI.get(haido_data["rarity_tier"], RARITY_DEFAULT_EMOJI)
            success_msg = f"""
🎉 <b>MAKIMA'S HAIDO GIVEAWAY WINNER!</b> 🎉

{rarity_emoji} <b>Rarity:</b> {haido_data['rarity']}
🧑 <b>Winner:</b> {mention}
⛓️ <b>Buddy:</b> <b>{escape_html(haido_data['char_name'])}</b>
🔖 <b>Chain ID:</b> <code>{display_char_id(haido_data['char_id'])}</code>
{artist_line(haido_data.get('artist'))}💰 <b>Bonus:</b> <code>+1000 MMK</code>

<blockquote>🎊 Makima says: ဂုဏ်ပြုပါတယ်! မင်းက Rarity အမြင့်ဆုံးကတ်ကို ရသွားပြီ!</blockquote>

⛓️ <i>Check your collection with /harem!</i>
"""
            
            await event.reply(success_msg, parse_mode='html')
            
            # Clean up
            del active_haido_events[chat_id]
            return
        
        else:
            # Name doesn't match Haido card
            return await event.reply(
                f"❌ <b>Makima:</b> ဒီနာမည်က မှားနေတယ်!\n"
                f"💡 <code>/catch {haido_data['char_name']}</code> ကို ရိုက်ပါ!",
                parse_mode='html'
            )

    # ==========================================
    # 🛸 NORMAL SPAWN CATCH LOGIC
    # ==========================================
    if chat_id not in active_group_spawns:
        return await event.reply(f"🛸 <b>Nothing to catch here!</b>", parse_mode='html')
    
    spawn_data = active_group_spawns[chat_id]
    if spawn_data["claimed"]:
        return await event.reply(f"❌ <b>Already caught!</b>", parse_mode='html')
    
    if time.time() - spawn_data["spawn_time"] > 300:
        if chat_id in active_group_spawns: 
            del active_group_spawns[chat_id]
        return await event.reply(f"⏱️ <b>Too late! They've slipped away.</b>", parse_mode='html')
    
    if normalize_name(catch_name) != normalize_name(spawn_data["name"]):
        return await event.reply(f"❌ <b>Wrong name! Use the exact name shown in /who.</b>", parse_mode='html')
    
    temp_msg = await event.reply("👾")
    await asyncio.sleep(2.5)
    await perform_catch(chat_id, user_id, spawn_data, event, reply_to_msg=event.id, is_callback=False, temp_msg_id=temp_msg.id)
# ---- /refillcatch: recompute spawn_count from what's ACTUALLY in players' vaults ----
@bot1.on(events.NewMessage(pattern=r'^/refillcatch(?:@\w+)?(?:\s+([a-zA-Z0-9_]+))?$'))
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

# ---- /backfillphash: fingerprint characters added BEFORE the identify feature existed ----
@bot1.on(events.NewMessage(pattern=r'^/backfillphash(?:@\w+)?$'))
async def backfill_phash_handler(event):
    if event.sender_id != OWNER_ID:
        return
    status_msg = await event.reply("⏳ <b>Fingerprinting characters that are missing a photo_phash...</b>", parse_mode='html')
    missing = await characters_base_col.find(
        {"$or": [{"photo_phash": {"$exists": False}}, {"photo_phash": None}]},
        {"char_id": 1, "name": 1, "storage_msg_id": 1}
    ).to_list(length=None)
    if not missing:
        return await status_msg.edit("✅ <b>Every character already has a fingerprint.</b>", parse_mode='html')
    done, failed = 0, 0
    for char in missing:
        try:
            storage_msg = await bot1.get_messages(SPECIFIC_CONTROL_GROUP, ids=char["storage_msg_id"])
            if not storage_msg or not storage_msg.media:
                failed += 1
                continue
            phash = await compute_phash_for_message(storage_msg)
            if phash:
                await characters_base_col.update_one({"char_id": char["char_id"]}, {"$set": {"photo_phash": phash}})
                done += 1
            else:
                failed += 1
        except Exception as e:
            print(f"backfillphash error for {char.get('char_id')}: {e}")
            failed += 1
        await asyncio.sleep(0.3)  # be gentle on Telegram + Mongo
    await invalidate_character_caches()
    await status_msg.edit(
        f"✅ <b>Backfill complete!</b>\n"
        f"🔎 <b>Fingerprinted:</b> <code>{done}</code>\n"
        f"❌ <b>Failed:</b> <code>{failed}</code>",
        parse_mode='html'
    )

# ==========================================
# 🎯 HMODE – RARITY FILTER
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/hmode(?:@\w+)?$'))
async def set_rarity_filter_handler(event):
    user_id = event.sender_id
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    current_filter = user_doc.get("rarity_filter") if user_doc else None
    buttons = []
    for num, data in RARITY_NUM_MAP.items():
        rarity_name = data["name"]
        label = f"✅ {rarity_name}" if current_filter == rarity_name else rarity_name
        buttons.append([Button.inline(label, data=f"hfilter_{num}_{user_id}")])
    clear_label = "🔓 Clear Filter" if current_filter else "🔒 No Filter"
    buttons.append([Button.inline(clear_label, data=f"hfilter_clear_{user_id}")])
    buttons.append([Button.inline("🔙 Back", data="nav_back_home")])
    await event.reply(
        f"🎯 <b>Select Rarity Filter for /harem</b>\n"
        f"Current: {current_filter if current_filter else 'None (Show All)'}\n\n"
        f"Tap a rarity to filter your vault, or clear to show all.\n"
        f"<i>🔐 Only you can use these buttons.</i>",
        buttons=buttons,
        parse_mode='html'
    )

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
@bot1.on(events.NewMessage(pattern=r'^/harem(?:@\w+)?(?:\s+(.*))?$'))
async def show_harem_vault(event):
    user_id = event.sender_id
    target_user_id = None
    if event.is_reply:
        reply_msg = await event.get_reply_message()
        if reply_msg:
            target_user_id = reply_msg.sender_id
    else:
        args = event.pattern_match.group(1)
        if args:
            args = args.strip()
            if args.startswith('@'):
                try:
                    entity = await bot1.get_entity(args)
                    target_user_id = entity.id
                except Exception:
                    return await event.reply("❌ User not found. Use @username or reply to their message.")
            elif args.isdigit():
                target_user_id = int(args)
            else:
                return await event.reply("❌ Invalid format. Use <code>/harem @username</code> or reply to a message.")
    if target_user_id is None:
        target_user_id = user_id
    try:
        target_entity = await bot1.get_entity(target_user_id)
        target_name = target_entity.first_name or "User"
    except Exception:
        target_name = "User"
    await ensure_user_registered(target_user_id, target_name)
    await send_paginated_harem(bot1, event.chat_id, target_user_id, page=1, viewer_id=user_id)

# ---- /fav bare usage ----
@bot1.on(events.NewMessage(pattern=r'^/fav(?:@\w+)?$'))
async def fav_bare_usage_handler(event):
    await event.reply(
        "📌 <b>Usage:</b> <code>/fav [CharID]</code>\n"
        "<i>Example:</i> <code>/fav 1234</code>\n"
        "Sets a card you own as the thumbnail shown in <code>/harem</code>.",
        parse_mode='html'
    )

# ---- /fav with inline confirmation ----
@bot1.on(events.NewMessage(pattern=r'^/fav(?:@\w+)?\s+([a-zA-Z0-9_]+)$'))
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
    
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    user_harem = user_doc.get("harem", []) if user_doc else []
    owns_card = any(isinstance(x, dict) and x.get("char_id").upper() == actual_char_id.upper() for x in user_harem)
    
    if not owns_card:
        return await event.reply(f"❌ <b>You don't own this card!</b>", parse_mode='html')
    
    # ✅ Confirmation Buttons
    confirm_text = (
        f"⛓️ <b>Makima's Favourite Card Confirmation</b>\n\n"
        f"📦 <b>Card:</b> <code>{card['name']}</code> (<code>{display_char_id(actual_char_id)}</code>)\n"
        f"🏷️ <b>Rarity:</b> {card.get('rarity', 'Unknown')}\n"
        f"{artist_line(card)}\n"
        f"<blockquote>Do you really want to set this card as your favourite?</blockquote>\n"
        f"✨ Makima is waiting for your decision! ⛓️"
    )
    buttons = [
        [
            Button.inline("✅ Yep, Why Not✔", data=f"fav_confirm_{user_id}_{actual_char_id}"),
            Button.inline("❌ Nope", data=f"fav_cancel_{user_id}_{actual_char_id}")
        ]
    ]
    await event.reply(confirm_text, buttons=buttons, parse_mode='html') 

# ---- Helper functions for harem ----
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

async def get_user_rank_for_card(user_id, char_id):
    pipeline = [
        {"$match": {"harem.char_id": char_id}},
        {"$project": {"user_id": 1, "count": {"$size": {"$filter": {"input": "$harem", "as": "item", "cond": {"$eq": ["$$item.char_id", char_id]}}}}}},
        {"$sort": {"count": -1}},
        {"$limit": 10}
    ]
    top_owners = await users_catcher_col.aggregate(pipeline).to_list(length=10)
    for idx, doc in enumerate(top_owners, start=1):
        if doc["user_id"] == user_id:
            return idx
    return None

async def send_paginated_harem(client, chat_id, user_id, page=1, edit_msg_id=None, viewer_id=None):
    if viewer_id is None:
        viewer_id = user_id
    is_own_vault = (viewer_id == user_id)
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    if not user_doc or not user_doc.get("harem"):
        msg = "<b>Your vault is empty! Go catch some charaters!</b>" if is_own_vault else "<b>Their vault is empty!</b>"
        if edit_msg_id: await client.edit_message(chat_id, edit_msg_id, msg, parse_mode='html')
        else: await client.send_message(chat_id, msg, parse_mode='html')
        return
    raw_harem = user_doc.get("harem", [])
    raw_owned_id_set = {item.get("char_id") for item in raw_harem if isinstance(item, dict) and item.get("char_id")}
    rarity_filter = user_doc.get("rarity_filter")
    filtered_harem = []
    if rarity_filter:
        for item in raw_harem:
            if isinstance(item, dict) and classify_rarity(item.get("rarity")) == classify_rarity(rarity_filter):
                filtered_harem.append(item)
    else:
        filtered_harem = raw_harem
    if not filtered_harem:
        msg = f"⚔️ <b>No cards found with rarity filter.</b>\n"
        msg += f"Use <code>/hmode</code> to change or clear filter."
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
        msg = f"⚔️ <b>No valid cards in vault.</b>"
        if edit_msg_id: await client.edit_message(chat_id, edit_msg_id, msg, parse_mode='html')
        else: await client.send_message(chat_id, msg, parse_mode='html')
        return
    db_chars = await characters_base_col.find({"char_id": {"$in": owned_ids}}).to_list(length=None)
    category_totals_pipeline = [{"$group": {"_id": "$category", "count": {"$sum": 1}}}]
    category_totals = {(doc["_id"] or "Unknown Series"): doc["count"] for doc in await characters_base_col.aggregate(category_totals_pipeline).to_list(length=None)}
    def get_rarity_weight(rarity_str):
        tier = classify_rarity(rarity_str)
        return {"LEGENDARY": 6, "LIMITED": 5, "MYTHIC": 4, "EPIC": 3, "RARE": 2, "COMMON": 1}.get(tier, 0)
    from collections import defaultdict
    by_category = defaultdict(list)
    for card in db_chars:
        by_category[card.get("category") or "Unknown Series"].append(card)
    def get_category_weight(cat):
        cards = by_category[cat]
        return max([get_rarity_weight(c.get("rarity", "")) for c in cards], default=0)
    sorted_categories = sorted(by_category.keys(), key=lambda c: (-get_category_weight(c), c.lower()))
    # Build one "block" of lines per category (header + progress bar + each card),
    # so pagination below can keep a whole category together instead of splitting
    # it awkwardly across two pages.
    category_blocks = []
    for cat in sorted_categories:
        cat_cards = sorted(by_category[cat], key=lambda x: (-get_rarity_weight(x.get("rarity", "")), x.get("name", "").lower()))
        owned_in_cat = len(cat_cards)
        total_in_cat = category_totals.get(cat, owned_in_cat)
        block = [f"⚜️ <b>{escape_html(cat)}</b> (<code>{owned_in_cat}/{total_in_cat}</code>)",
                 f"<code>{build_progress_bar(owned_in_cat, total_in_cat, length=15)}</code>"]
        for card in cat_cards:
            cid = card["char_id"]
            counts = harem_counts.get(cid, {"normal": 0, "market": 0})
            normal_qty = counts["normal"]
            market_qty = counts["market"]
            if normal_qty > 0 and market_qty > 0:
                status_str = f"<b>(x{normal_qty} | {market_qty} 🛒)</b>"
            elif market_qty > 0:
                status_str = f"<b>({market_qty} 🛒)</b>"
            else:
                status_str = f"<b>(x{normal_qty})</b>"
            rank = await get_user_rank_for_card(viewer_id, cid)
            rank_str = ""
            if rank == 1:
                rank_str = "🥇 "
            elif rank == 2:
                rank_str = "🥈 "
            elif rank == 3:
                rank_str = "🥉 "
            tier = classify_rarity(card.get("rarity", ""))
            rarity_emoji = RARITY_EMOJI.get(tier, RARITY_DEFAULT_EMOJI)
            block.append(f"{rank_str}🔖 <code>{display_char_id(cid)}</code> | {rarity_emoji} | {escape_html(card['name'])} {status_str}")
        category_blocks.append(block)
    # Paginate by whole category blocks (never split a category across two pages)
    LINES_PER_PAGE = 12
    pages = []
    current_page, current_len = [], 0
    for block in category_blocks:
        block_len = len(block) + 1  # +1 for the blank-line spacer after each category
        if current_page and current_len + block_len > LINES_PER_PAGE:
            pages.append(current_page)
            current_page, current_len = [], 0
        current_page.append(block)
        current_len += block_len
    if current_page:
        pages.append(current_page)
    total_pages = len(pages) or 1
    if page < 1: page = 1
    if page > total_pages: page = total_pages
    page_blocks = pages[page - 1] if pages else []
    page_lines = []
    for i, block in enumerate(page_blocks):
        page_lines.extend(block)
        if i < len(page_blocks) - 1:
            page_lines.append("")
    try:
        sender_ent = await client.get_entity(user_id)
        first = getattr(sender_ent, 'first_name', '') or ''
        last = getattr(sender_ent, 'last_name', '') or ''
        fullname = f"{first} {last}".strip() or getattr(sender_ent, 'username', '') or "Hunter"
    except: fullname = "Hunter"
    mention = f"<a href='tg://user?id={user_id}'><b>{escape_html(fullname)}</b></a>"
    vault_label = "🎒 YOUR VAULT" if is_own_vault else "🎒 VAULT COLLECTION"
    output_text = f"{mention}'s <b>{vault_label}</b>\n"
    if rarity_filter:
        output_text += f"🔍 <b>Filter:</b> {rarity_filter}\n"
    output_text += f"📑 <b>Page</b> <code>{page}/{total_pages}</code> · <b>Unique</b> <code>{len(owned_ids)}</code> · <b>Total</b> <code>{len(filtered_harem)}</code>\n\n"
    for l in page_lines:
        output_text += l + "\n"
    output_text += "\n"
    if is_own_vault:
        output_text += f"<blockquote>💡 <b>Rank Badges:</b> 🥇 Top 1, 🥈 Top 2, 🥉 Top 3 collector for that card.</blockquote>"
        output_text += f"💡 Use <code>/fav [ID]</code> to set a favourite card."
    if rarity_filter:
        output_text += f"\n<i>🔍 Filter active — use /hmode to change or clear.</i>"
    buttons = []
    total_cards = len(filtered_harem)
    buttons.append([Button.switch_inline(f"👀 Take A Look ({total_cards})", query=f"harem.{user_id}", same_peer=True)])
    nav_buttons = []
    if page > 1:
        nav_buttons.append(Button.inline("⬅️ Prev", data=f"harem_{page-1}_{user_id}_{viewer_id}"))
    if page < total_pages:
        nav_buttons.append(Button.inline("Next ➡️", data=f"harem_{page+1}_{user_id}_{viewer_id}"))
    if nav_buttons:
        buttons.append(nav_buttons)
    if is_own_vault:
        if rarity_filter:
            buttons.append([Button.inline("🔓 Clear Filter", data=f"hfilter_clear_{user_id}")])
        buttons.append([Button.inline("🎯 Change Rarity Filter", data="nav_hmode")])
    if not buttons:
        buttons = None
    fav_card_id = user_doc.get("fav_card")
    if fav_card_id and fav_card_id not in raw_owned_id_set:
        # Owner gifted/sold/traded/scrapped away their last copy of this card since favouriting
        # it — stop treating it as the favourite (both for display here and going forward).
        await users_catcher_col.update_one({"user_id": user_id, "fav_card": fav_card_id}, {"$unset": {"fav_card": ""}})
        fav_card_id = None
    fav_media = None
    target_display_id = fav_card_id
    if not target_display_id and owned_ids:
        target_display_id = random.choice(owned_ids)
    if target_display_id:
        fav_card_data = await characters_base_col.find_one({"char_id": target_display_id})
        if fav_card_data:
            try:
                storage_msg = await client.get_messages(SPECIFIC_CONTROL_GROUP, ids=fav_card_data["storage_msg_id"])
                if storage_msg and storage_msg.media:
                    fav_media = storage_msg.media
            except: pass
    try:
        if edit_msg_id:
            try:
                await client.edit_message(chat_id, edit_msg_id, output_text, parse_mode='html', buttons=buttons)
            except errors.MessageNotModifiedError:
                pass
        else:
            if fav_media:
                try:
                    await client.send_message(chat_id, output_text, file=fav_media, parse_mode='html', buttons=buttons)
                except Exception:
                    await client.send_message(chat_id, output_text, parse_mode='html', buttons=buttons)
            else:
                await client.send_message(chat_id, output_text, parse_mode='html', buttons=buttons)
    except Exception as main_err:
        try:
            await client.send_message(chat_id, f"❌ <b>Vault Display Error:</b> <code>{escape_html(str(main_err))}</code>", parse_mode='html')
        except: pass

# ---- Inline Query for harem ----
def _rarity_weight_for_sort(rarity_str):
    tier = classify_rarity(rarity_str)
    return {"LEGENDARY": 6, "LIMITED": 5, "MYTHIC": 4, "EPIC": 3, "RARE": 2, "COMMON": 1}.get(tier, 0)

def _inline_media_result(builder, storage_msg, cid, title, caption):
    """Builds an inline result for a photo or video. Videos use type='gif' so Telegram
    renders them in the same side-by-side grid as photos, instead of a tall vertical list."""
    if storage_msg.photo:
        return builder.photo(file=storage_msg.media, id=cid, text=caption, parse_mode='html')
    return builder.document(
        file=storage_msg.media,
        title=title,
        type='gif',
        id=cid,
        text=caption,
        parse_mode='html'
    )

# ---- harem.<user_id> : shows a user's own vault as an inline gallery ----
async def handle_harem_inline_query(event, query_text):
    builder = event.builder
    try:
        target_user_id = int(query_text.split(".", 1)[1])
    except (ValueError, IndexError):
        return await event.answer([], cache_time=0)
    user_doc = await users_catcher_col.find_one({"user_id": target_user_id})
    if not user_doc or not user_doc.get("harem"):
        return await event.answer(
            [], cache_time=0,
            switch_pm="📭 This vault is empty!", switch_pm_param="start"
        )
    raw_harem = user_doc.get("harem", [])
    rarity_filter = user_doc.get("rarity_filter")
    filtered_harem = [
        item for item in raw_harem
        if isinstance(item, dict) and "char_id" in item
        and (not rarity_filter or classify_rarity(item.get("rarity")) == classify_rarity(rarity_filter))
    ]
    if not filtered_harem:
        return await event.answer([], cache_time=0)
    harem_counts = {}
    for item in filtered_harem:
        cid = item["char_id"]
        harem_counts[cid] = harem_counts.get(cid, 0) + 1
    owned_ids = list(harem_counts.keys())
    db_chars = await characters_base_col.find({"char_id": {"$in": owned_ids}}).to_list(length=None)
    db_chars = sorted(db_chars, key=lambda x: (-_rarity_weight_for_sort(x.get("rarity", "")), x.get("name", "").lower()))
    try:
        owner_ent = await bot1.get_entity(target_user_id)
        first = getattr(owner_ent, 'first_name', '') or ''
        last = getattr(owner_ent, 'last_name', '') or ''
        owner_name = f"{first} {last}".strip() or getattr(owner_ent, 'username', '') or f"User {target_user_id}"
    except Exception:
        owner_name = f"User {target_user_id}"
    results = []
    for card in db_chars[:50]:
        cid = card["char_id"]
        qty = harem_counts.get(cid, 0)
        try:
            storage_msg = await bot1.get_messages(SPECIFIC_CONTROL_GROUP, ids=card["storage_msg_id"])
            if not storage_msg or not storage_msg.media:
                continue
            caption = (
                f"🌟 <b>{escape_html(card['name'])}</b>\n"
                f"🆔 <code>{display_char_id(cid)}</code>\n"
                f"🌐 <b>Category:</b> {escape_html(card.get('category', ''))}\n"
                f"🏷️ <b>Rarity:</b> {card.get('rarity', '')}\n"
                f"{artist_line(card)}"
                f"⛓️ <b>Card Owner:</b> <a href='tg://user?id={target_user_id}'>{escape_html(owner_name)}</a> (x{qty})"
            )
            results.append(_inline_media_result(builder, storage_msg, cid, card['name'], caption))
        except Exception as e:
            print(f"Inline gallery error for {cid}: {e}")
            continue
    await event.answer(results, cache_time=0)

# ---- dex.<category> : shows every character (caught or not) in a series, image gallery style ----
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
    for card in cat_chars[:50]:
        cid = card["char_id"]
        owned = cid in owned_ids
        try:
            storage_msg = await bot1.get_messages(SPECIFIC_CONTROL_GROUP, ids=card["storage_msg_id"])
            if not storage_msg or not storage_msg.media:
                continue
            caption = (
                f"🌟 <b>{escape_html(card['name'])}</b>\n"
                f"🆔 <code>{display_char_id(cid)}</code>\n"
                f"🌐 <b>Series:</b> {escape_html(matched_cat)}\n"
                f"🏷️ <b>Rarity:</b> {card.get('rarity', '')}\n"
                f"{artist_line(card)}"
                f"{'✅ You caught this!' if owned else '❓ Not caught yet'}"
            )
            results.append(_inline_media_result(builder, storage_msg, cid, card['name'], caption))
        except Exception as e:
            print(f"Dex inline gallery error for {cid}: {e}")
            continue
    await event.answer(results, cache_time=0)

# ---- /catch <name> : lets the /who reveal button's switch_inline query actually send a message ----
async def handle_catch_inline_query(event, query_text):
    char_name = query_text[len("/catch "):].strip()
    if not char_name:
        return await event.answer([], cache_time=0)
    builder = event.builder
    result = builder.article(
        title=f"🎯 Catch {char_name}!",
        description="Tap here to send this catch command",
        text=f"/catch {char_name}",
        id=f"catch_{abs(hash(char_name)) % (10 ** 12)}"
    )
    await event.answer([result], cache_time=0)

@bot1.on(events.InlineQuery)
async def unified_inline_query_handler(event):
    query_text = (event.text or "").strip()
    if query_text.startswith("harem."):
        await handle_harem_inline_query(event, query_text)
    elif query_text.startswith("dex."):
        await handle_dex_inline_query(event, query_text)
    elif query_text.startswith("/catch "):
        await handle_catch_inline_query(event, query_text)
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
    media_file = None
    try:
        storage_msg = await bot1.get_messages(SPECIFIC_CONTROL_GROUP, ids=card["storage_msg_id"])
        if storage_msg and storage_msg.media:
            media_file = storage_msg.media
    except Exception as e:
        print(f"Error getting media: {e}")
    detail_text = (
        f" <b>Card Details</b>\n"
        f" <b>This Charater Card Owner:</b> {owner_mention}\n\n"
        f" <b>Name:</b> <code>{escape_html(card['name'])}</code>\n"
        f" <b>ID:</b> <code>{display_char_id(card['char_id'])}</code>\n"
        f" <b>Category:</b> <code>{escape_html(card['category'])}</code>\n"
        f" <b>Rarity:</b> {card['rarity']}\n"
        f"{artist_line(card)}"
        f"📦 <b>Owned:</b> <code>{count} copies</code>\n"
        f" <b>Value:</b> <code>{card['currency_value']} MMK</code>\n\n"
        f"<i>✨ Tap the image to close</i>"
    )
    if media_file:
        await event.answer("🃏 Loading card...", alert=False)
        await bot1.send_file(
            event.chat_id,
            media_file,
            caption=detail_text,
            parse_mode='html',
            reply_to=event.message_id
        )
        await event.answer("✅ Card sent!", alert=True)
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
    elif action_type == "mktpg":
        page = int(data_parts[1])
        await send_market_page(bot1, event.chat_id, page=page, edit_msg_id=event.message_id)
    elif action_type == "mktbuy":
        listing_id = data_parts[1]
        buyer_id = event.sender_id
        listing = await marketplace_col.find_one({"listing_id": listing_id})
        if not listing: return await event.answer("❌ This listing no longer exists.", alert=True)
        price = listing["price"]
        seller_id = listing["seller_id"]
        char_id = listing["char_id"]
        if buyer_id == seller_id: return await event.answer("⚠️ You cannot buy your own item!", alert=True)
        buyer_doc = await users_catcher_col.find_one({"user_id": buyer_id})
        if not buyer_doc or buyer_doc.get("wallet_balance", 0) < price:
            return await event.answer(f"❌ Insufficient balance! Need {price} MMK", alert=True)
        char_data = await characters_base_col.find_one({"char_id": char_id})
        char_rarity = char_data.get("rarity", "Unknown") if char_data else "Unknown"
        seller_doc = await users_catcher_col.find_one({"user_id": seller_id})
        seller_harem = seller_doc.get("harem", []) if seller_doc else []
        item_to_remove = next((x for x in seller_harem if isinstance(x, dict) and x.get("char_id") == char_id and x.get("status") == "market"), None)
        if item_to_remove: seller_harem.remove(item_to_remove)
        res = await users_catcher_col.update_one(
            {"user_id": buyer_id, "wallet_balance": {"$gte": price}},
            {
                "$inc": {"wallet_balance": -price, "total_caught": 1},
                "$push": {"harem": {"char_id": char_id, "caught_date": time.time(), "rarity": char_rarity, "status": "vault"}}
            }
        )
        if res.modified_count == 0: return await event.answer("❌ Transaction failed!", alert=True)
        await users_catcher_col.update_one({"user_id": seller_id}, {"$set": {"harem": seller_harem}, "$inc": {"wallet_balance": price}})
        await clear_stale_favorite(seller_id, char_id, seller_harem)
        await marketplace_col.delete_one({"listing_id": listing_id})
        await event.answer("🎉 Purchase successful!", alert=True)
        await event.edit(f"🤝 <b>DEAL SECURED</b>\n\nYou successfully bought the card.", parse_mode='html')
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
            await event.answer("⚡ Finalising trade...")
            my_char_id, their_char_id = data_parts[4], data_parts[5]
            s_doc = await users_catcher_col.find_one({"user_id": sender_id})
            t_doc = await users_catcher_col.find_one({"user_id": target_id})
            s_harem = s_doc.get("harem", []) if s_doc else []
            t_harem = t_doc.get("harem", []) if t_doc else []
            s_item = next((x for x in s_harem if isinstance(x, dict) and x.get("char_id") == my_char_id and x.get("status") != "market"), None)
            t_item = next((x for x in t_harem if isinstance(x, dict) and x.get("char_id") == their_char_id and x.get("status") != "market"), None)
            if not s_item or not t_item: return await event.edit(f"❌ <b>Trade failed – card unavailable.</b>", parse_mode='html')
            s_harem.remove(s_item); t_harem.remove(t_item)
            s_harem.append({"char_id": their_char_id, "caught_date": time.time(), "rarity": t_item.get("rarity", "Unknown"), "status": "vault"})
            t_harem.append({"char_id": my_char_id, "caught_date": time.time(), "rarity": s_item.get("rarity", "Unknown"), "status": "vault"})
            await users_catcher_col.update_one({"user_id": sender_id}, {"$set": {"harem": s_harem}})
            await users_catcher_col.update_one({"user_id": target_id}, {"$set": {"harem": t_harem}})
            await clear_stale_favorite(sender_id, my_char_id, s_harem)
            await clear_stale_favorite(target_id, their_char_id, t_harem)
            await event.edit(f"🤝 <b>Trade concluded successfully!</b>", parse_mode='html')
    elif action_type == "cardjoin":
        g_chat_id = int(data_parts[1])
        p_id = event.sender_id
        if g_chat_id not in active_card_games: return await event.answer("❌ This game lobby no longer exists.", alert=True)
        game = active_card_games[g_chat_id]
        if game["status"] != "lobby": return await event.answer("❌ Game started!", alert=True)
        if p_id in game["players"]: return await event.answer("⚠️ Already in game.", alert=True)
        user_doc = await users_catcher_col.find_one({"user_id": p_id})
        balance = user_doc.get("wallet_balance", 0) if user_doc else 0
        if balance < game["bet"]: return await event.answer(f"❌ Insufficient balance! Need {game['bet']} MMK", alert=True)
        await users_catcher_col.update_one({"user_id": p_id}, {"$inc": {"wallet_balance": -game["bet"]}})
        try:
            p_ent = await event.client.get_entity(p_id)
            f_n = getattr(p_ent, 'first_name', '') or ''
            l_n = getattr(p_ent, 'last_name', '') or ''
            fullname = f"{f_n} {l_n}".strip() or getattr(p_ent, 'username', '') or f"Agent {p_id}"
        except: fullname = f"Agent {p_id}"
        game["players"][p_id] = fullname
        host_mention = f"<a href='tg://user?id={game['host_id']}'><b>{escape_html(game['players'][game['host_id']])}</b></a>"
        lobby_text = f"🃏 <b>HIGH CARD DRAW</b>\n👑 <b>Host:</b> {host_mention}\n💵 <b>Bet:</b> <code>{game['bet']} MMK</code>\n\n👥 <b>Players ({len(game['players'])}):</b>\n"
        for idx, (pid, name) in enumerate(game["players"].items(), start=1):
            p_mention = f"<a href='tg://user?id={pid}'><b>{escape_html(name)}</b></a>"
            lobby_text += f" {idx}. {p_mention}\n"
        lobby_text += f"\n📌 <i>Host: <code>/startgame</code></i>"
        buttons = [[Button.inline("🃏 Join", data=f"cardjoin_{g_chat_id}")]]
        await event.edit(lobby_text, parse_mode='html', buttons=buttons)
        await event.answer("🎉 You joined the game!")
    elif action_type == "hilo":
        choice = data_parts[1]
        base_card = int(data_parts[2])
        bet_amount = int(data_parts[3])
        target_user_id = int(data_parts[4])
        if event.sender_id != target_user_id: return await event.answer("⚠️ This is not your game.", alert=True)
        user_doc = await users_catcher_col.find_one({"user_id": target_user_id})
        balance = user_doc.get("wallet_balance", 0) if user_doc else 0
        if balance < bet_amount: return await event.answer("❌ Insufficient balance.", alert=True)
        await event.answer("🎲 Shuffling...")
        new_card = random.randint(1, 13)
        while new_card == base_card: new_card = random.randint(1, 13)
        card_map = {1: "A", 11: "J", 12: "Q", 13: "K"}
        base_display = card_map.get(base_card, str(base_card))
        new_display = card_map.get(new_card, str(new_card))
        is_win = (choice == "HIGH" and new_card > base_card) or (choice == "LOW" and new_card < base_card)
        mention = await get_html_mention(event, target_user_id)
        if is_win:
            await users_catcher_col.update_one({"user_id": target_user_id}, {"$inc": {"wallet_balance": bet_amount}})
            status_text = f"🎉 <b>YOU WIN! (+{bet_amount:,} MMK)</b>"
        else:
            await users_catcher_col.update_one({"user_id": target_user_id}, {"$inc": {"wallet_balance": -bet_amount}})
            status_text = f"💸 <b>YOU LOSE... (-{bet_amount:,} MMK)</b>"
        result_text = f"🃏 <b>HI-LO RESULT</b>\n👤 <b>Player:</b> {mention}\n💵 <b>Bet:</b> <code>{bet_amount:,} MMK</code>\n\n🎴 Previous: <b>[{base_display}]</b>\n🎲 New: <b>[{new_display}]</b>\n🎯 You chose: <code>{choice}</code>\n\n{status_text}{GAME_FOOTER}"
        await event.edit(result_text, parse_mode='html')
        if not event.is_private:
            schedule_game_cleanup(bot1, event.chat_id, event.message_id)
    # ---- Gift-history pagination (from /myinfo, /profile) ----
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

    # ---- Profile main/stats page toggle ----
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

    # ---- Leaderboard tab/page switching ----
    elif action_type == "lb":
        mode = data_parts[1]
        page = int(data_parts[2])
        text, buttons = await render_leaderboard_page(mode, page)
        try:
            await event.edit(text, parse_mode='html', buttons=buttons)
        except Exception:
            pass
        await event.answer()

    # ---- /fav confirm / cancel callbacks ----
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
                    f"⛓️ <b>Makima:</b> <b>{escape_html(card['name'])}</b> (<code>{display_char_id(card['char_id'])}</code>) ကို "
                    f"favourite ကတ်အဖြစ် သတ်မှတ်လိုက်ပြီ! ✨ /harem မှာ ဒီကတ်ပေါ်လာမယ်။",
                    parse_mode='html', buttons=None
                )
            except Exception:
                pass
            return
# ==========================================
# 🏆 LEADERBOARD (Daily / All-Time, paginated)
# ==========================================
LEADERBOARD_PAGE_SIZE = 10
LB_MEDALS = {0: "🥇", 1: "🥈", 2: "🥉"}

async def render_leaderboard_page(mode, page):
    """Generate leaderboard text and buttons for a given mode and page."""
    today_str = datetime.now(TZ).strftime("%Y-%m-%d") if mode == "daily" else None
    data = await get_cached_leaderboard(mode, today_str)
    
    total = len(data)
    start = page * LEADERBOARD_PAGE_SIZE
    page_rows = data[start:start + LEADERBOARD_PAGE_SIZE]
    field_map = {"daily": "daily_catches", "all": "total_caught", "invest": "stock_portfolio_value", "btc": "btc_value_mmk"}
    title_map = {"daily": "📅 <b>Today's Top Catchers</b>", "all": "🏆 <b>All-Time Top Catchers</b>", "invest": "📈 <b>Top Investors</b>", "btc": "₿ <b>Top BTC Holders</b>"}
    field = field_map.get(mode, "total_caught")
    title = title_map.get(mode, title_map["all"])
    
    if not page_rows:
        empty_texts = {
            "invest": "<i>No investors yet — try </i><code>/stocks</code><i>!</i>",
            "btc": "<i>No BTC holders yet — try </i><code>/btc</code><i>!</i>",
        }
        empty_text = empty_texts.get(mode, "<i>Nobody's caught anything yet — be the first! 🐟</i>")
        body = empty_text if page == 0 else "<i>No more entries.</i>"
    else:
        lines = []
        for i, row in enumerate(page_rows):
            rank = start + i
            medal = LB_MEDALS.get(rank, f"<code>#{rank + 1}</code>")
            name = escape_html(clean_display_name(row.get("fullname"), fallback=f"User {row.get('user_id')}"))
            val = row.get(field, 0)
            val_str = f"{val:,.0f} MMK" if mode in ("invest", "btc") else f"{val}"
            lines.append(f"{medal}  {name} — <code>{val_str}</code>")
        body = "\n".join(lines)
    
    text = f"{title}\n<blockquote>{body}</blockquote>"
    
    # Tab buttons (two rows so it stays readable on mobile)
    tab_rows = [
        [
            Button.inline(("🔘 " if mode == "daily" else "") + "📅 Daily", data="lb_daily_0"),
            Button.inline(("🔘 " if mode == "all" else "") + "🏆 All-Time", data="lb_all_0")
        ],
        [
            Button.inline(("🔘 " if mode == "invest" else "") + "📈 Investors", data="lb_invest_0"),
            Button.inline(("🔘 " if mode == "btc" else "") + "₿ BTC", data="lb_btc_0")
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

@bot1.on(events.NewMessage(pattern=r'^/(leaderboard|lb)(?:@\w+)?$'))
async def leaderboard_handler(event):
    """Handle /leaderboard and /lb commands."""
    text, buttons = await render_leaderboard_page("all", 0)
    await event.reply(text, parse_mode='html', buttons=buttons)

# ---- Callback handler for leaderboard navigation ----
@bot1.on(events.CallbackQuery(pattern=r'^lb_(daily|all|invest|btc)_(\d+)$'))
async def leaderboard_callback_handler(event):
    """Handle leaderboard page/tab switching via inline buttons."""
    mode = event.pattern_match.group(1)  # "daily", "all", "invest", or "btc"
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

async def get_cached_leaderboard(mode, today_str=None):
    """mode: 'daily' or 'all'. Leaderboard staleness of a few seconds is harmless, so this
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
    elif mode == "invest":
        cursor = users_catcher_col.find(
            {"stock_portfolio_value": {"$gt": 0}},
            {"_id": 0, "user_id": 1, "fullname": 1, "stock_portfolio_value": 1}
        ).sort("stock_portfolio_value", -1).limit(50)
    elif mode == "btc":
        cursor = users_catcher_col.find(
            {"btc_value_mmk": {"$gt": 0}},
            {"_id": 0, "user_id": 1, "fullname": 1, "btc_value_mmk": 1}
        ).sort("btc_value_mmk", -1).limit(50)
    else:
        cursor = users_catcher_col.find(
            {"total_caught": {"$gt": 0}},
            {"_id": 0, "user_id": 1, "fullname": 1, "total_caught": 1}
        ).sort("total_caught", -1).limit(50)
    
    data = await cursor.to_list(length=50)
    
    # Try to cache it
    try:
        await redis_client.setex(cache_key, 20, json.dumps(data, default=str))
    except Exception as e:
        print(f"⚠️ Redis leaderboard cache write error: {e}")
    
    return data
# ==========================================
# 🎁 HAIDO - Rarity 1-4 Spawn Event (Owner Only)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/haido(?:@\w+)?$'))
async def haido_spawn_handler(event):
    if event.sender_id != OWNER_ID:
        return await event.reply("❌ ဒီ Command ကို Owner ပဲသုံးလို့ရပါတယ်။")
    
    chat_id = event.chat_id
    
    # Check if there's already an active haido event in this chat
    if chat_id in active_haido_events:
        remaining = int(active_haido_events[chat_id]["spawn_time"] + HAIDO_TIMEOUT_SECONDS - time.time())
        if remaining > 0:
            minutes = remaining // 60
            seconds = remaining % 60
            return await event.reply(
                f"⚠️ <b>Makima:</b> ဒီ Chat မှာ Haido Event ရှိပြီးသားပါ! ⛓️\n"
                f"⏳ ကျန်ချိန်: <code>{minutes} မိနစ် {seconds} စက္ကန့်</code>\n"
                f"💡 <code>/catch [Name]</code> နဲ့ ကောက်လို့ရပါသေးတယ်!",
                parse_mode='html'
            )
    
    # Get all characters with Rarity 1-4 (LEGENDARY, LIMITED, MYTHIC, EPIC)
    all_chars = await get_all_characters_cached()
    if not all_chars:
        return await event.reply("❌ <b>Makima:</b> ကတ်တွေ မရှိသေးပါဘူး! ကြိုထည့်ပါဦး ⛓️", parse_mode='html')
    
    # Filter: Rarity 1-4 (LEGENDARY, LIMITED, MYTHIC, EPIC)
    eligible_rarities = ["LEGENDARY", "LIMITED", "MYTHIC", "EPIC"]
    eligible_chars = []
    for char in all_chars:
        tier = char.get("rarity_tier") or classify_rarity(char.get("rarity", ""))
        if tier not in eligible_rarities:
            continue
        # Check spawn limit
        limit = char.get("spawn_limit", 0)
        if limit and limit > 0 and char.get("spawn_count", 0) >= limit:
            continue
        eligible_chars.append(char)
    
    if not eligible_chars:
        return await event.reply(
            f"😅 <b>Makima:</b> ကံဆိုးချင်တယ်! Rarity 1-4 ကတ်တွေ အကုန်ကုန်သွားပြီ!\n"
            f"💡 နောက်တစ်ခါ ကြိုးစားပါဦး ⛓️",
            parse_mode='html'
        )
    
    # Randomly select one character
    chosen_char = random.choice(eligible_chars)
    char_id = chosen_char["char_id"]
    char_name = chosen_char["name"]
    char_rarity = chosen_char.get("rarity", "Unknown")
    tier = classify_rarity(char_rarity)
    
    # Store the event
    active_haido_events[chat_id] = {
        "char_id": char_id,
        "char_name": char_name,
        "rarity": char_rarity,
        "rarity_tier": tier,
        "artist": chosen_char.get("artist"),
        "spawn_time": time.time(),
        "claimed_by": None,
        "claimed": False,
        "msg_id": None
    }
    
    # Get media
    media_file = None
    try:
        storage_msg = await bot1.get_messages(SPECIFIC_CONTROL_GROUP, ids=chosen_char["storage_msg_id"])
        if storage_msg and storage_msg.media:
            media_file = storage_msg.media
    except:
        pass
    
    # Send spawn message
    rarity_emoji = RARITY_EMOJI.get(tier, RARITY_DEFAULT_EMOJI)
    spawn_msg = f"""
🎁 <b>MAKIMA'S HAIDO GIVEAWAY!</b> 🎁

{rarity_emoji} <b>Rarity:</b> {char_rarity}
📦 <b>Buddy:</b> <code>{char_name}</code>
🆔 <b>ID:</b> <code>{display_char_id(char_id)}</code>
{artist_line(chosen_char.get("artist"))}
⏳ <b>Time Limit:</b> 15 မိနစ်

<blockquote>➠ Reply with <code>/catch {char_name}</code> to claim!</blockquote>

⛓️ <i>Makima says: ဘယ်သူအရင်ရမလဲ? ပြိုင်ကြည့်ကြရအောင်!</i>
"""
    
    try:
        if media_file:
            sent = await bot1.send_message(chat_id, spawn_msg, file=media_file, parse_mode='html')
        else:
            sent = await bot1.send_message(chat_id, spawn_msg, parse_mode='html')
        active_haido_events[chat_id]["msg_id"] = sent.id
    except Exception as e:
        await event.reply(f"❌ <b>Makima:</b> Spawn မအောင်မြင်ဘူး! {e}", parse_mode='html')
        del active_haido_events[chat_id]
        return
    
    # Start timeout watcher
    asyncio.create_task(haido_timeout_watcher(chat_id))
    
    # Log to owner
    await event.reply(
        f"✅ <b>Haido Event Started!</b>\n"
        f"📦 <b>Card:</b> <code>{char_name}</code> ({display_char_id(char_id)})\n"
        f"🏷️ <b>Rarity:</b> {char_rarity}\n"
        f"⏳ <b>Time Limit:</b> 15 မိနစ်\n"
        f"📍 <b>Chat:</b> <code>{chat_id}</code>",
        parse_mode='html'
    )


# ---- Haido Timeout Watcher ----
async def haido_timeout_watcher(chat_id):
    """Check if haido event expires after 15 minutes."""
    await asyncio.sleep(HAIDO_TIMEOUT_SECONDS)
    
    if chat_id not in active_haido_events:
        return
    
    event_data = active_haido_events[chat_id]
    if event_data["claimed"]:
        return  # Already claimed
    
    # Expired - send timeout message
    char_name = event_data["char_name"]
    char_rarity = event_data["rarity"]
    rarity_emoji = RARITY_EMOJI.get(event_data["rarity_tier"], RARITY_DEFAULT_EMOJI)
    
    timeout_msg = f"""
⏰ <b>MAKIMA SAYS: TIME'S UP!</b> ⏰

{rarity_emoji} <b>{char_rarity}</b> — <code>{char_name}</code>

<blockquote>😅 မင်းတို့တော့ Rarity အမြင့်ဆုံးကတ်ကို မရလိုက်ဘူး!</blockquote>

⛓️ <i>နောက်တစ်ခါ ပိုမြန်မြန်လုပ်ကြပါဦး! Makima က စောင့်ပေးမယ် 😄</i>
"""
    
    try:
        await bot1.send_message(chat_id, timeout_msg, parse_mode='html')
    except Exception:
        pass
    
    # Record as unclaimed
    try:
        await haido_history_col.insert_one({
            "chat_id": chat_id,
            "char_id": event_data["char_id"],
            "char_name": event_data["char_name"],
            "rarity": event_data["rarity"],
            "rarity_tier": event_data["rarity_tier"],
            "claimed_by": None,
            "claimed": False,
            "timestamp": time.time()
        })
    except Exception as e:
        print(f"Haido history insert error: {e}")
    
    # Clean up
    del active_haido_events[chat_id]

# ==========================================
# 📊 PROFILE
# ==========================================
async def render_profile_main_page(event, user_id, mention):
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    total_caught = user_doc.get("total_caught", 0) if user_doc else 0
    balance = user_doc.get("wallet_balance", 0) if user_doc else 0
    streak = user_doc.get("daily_streak", 0) if user_doc else 0
    referrals = user_doc.get("referral_count", 0) if user_doc else 0
    badge_count = len(user_doc.get("achievements", [])) if user_doc else 0
    raw_harem = user_doc.get("harem", []) if user_doc else []
    fav_card_id = user_doc.get("fav_card") if user_doc else None
    total_gifted = user_doc.get("total_gifted", 0) if user_doc else 0
    unique_ids = {item["char_id"] for item in raw_harem if isinstance(item, dict) and "char_id" in item}
    base_total = await characters_base_col.count_documents({})
    unique_owned = len(unique_ids)
    rank = await users_catcher_col.count_documents({"total_caught": {"$gt": total_caught}}) + 1
    chat_rank_line = ""
    if not event.is_private:
        chat_id_str = str(event.chat_id)
        my_chat_catches = (user_doc.get("group_catches", {}) if user_doc else {}).get(chat_id_str, 0)
        if my_chat_catches > 0:
            chat_rank = await users_catcher_col.count_documents({f"group_catches.{chat_id_str}": {"$gt": my_chat_catches}}) + 1
            chat_rank_line = f"➤ 🍁 <b>Chat Rank:</b> <code>#{chat_rank}</code>\n"
    fav_line = ""
    if fav_card_id and fav_card_id not in unique_ids:
        # Owner no longer has any copy of this card — clear the stale favourite.
        await users_catcher_col.update_one({"user_id": user_id, "fav_card": fav_card_id}, {"$unset": {"fav_card": ""}})
        fav_card_id = None
    if fav_card_id:
        fav_doc = await characters_base_col.find_one({"char_id": fav_card_id})
        if fav_doc:
            fav_line = f"➤ ⭐ <b>Favourite:</b> <code>{escape_html(fav_doc.get('name'))}</code> (<code>{fav_card_id}</code>)\n"
    pct = (unique_owned / base_total * 100) if base_total else 0
    text = (
        f"⛓️ <b>MAKIMA'S CATCHER ID</b> ⛓️\n"
        f"<blockquote>"
        f"➤ 👤 <b>Trainer:</b> {mention}\n"
        f"➤ 🔖 <b>ID:</b> <code>{user_id}</code>\n"
        f"➤ 🏆 <b>Global Rank:</b> <code>#{rank}</code>\n"
        f"{chat_rank_line}"
        f"➤ 🎒 <b>Total Caught:</b> <code>{total_caught}</code>\n"
        f"➤ 🧬 <b>Unique Owned:</b> <code>{unique_owned}/{base_total}</code> (<code>{pct:.1f}%</code>)\n"
        f"<code>{build_progress_bar(unique_owned, base_total)}</code>\n"
        f"➤ 🪙 <b>Balance:</b> <code>{balance:,} MMK</code>\n"
        f"➤ 🔥 <b>Streak:</b> <code>{streak}d</code>   🤝 <b>Referrals:</b> <code>{referrals}</code>\n"
        f"➤ 🏅 <b>Badges:</b> <code>{badge_count}/{len(ACHIEVEMENTS)}</code>\n"
        f"{fav_line}"
        f"</blockquote>\n"
        f"⛓️ <i>Tap below to open your Rarity Vault breakdown!</i>"
    )
    buttons = [[Button.inline("📊 Rarity Stats", data=f"pf_stats_{user_id}")]]
    if total_gifted:
        buttons.append([Button.inline("🎁 Gift History", data=f"gh_{user_id}_0")])
    return text, buttons

async def render_profile_stats_page(user_id, mention):
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    total_caught = user_doc.get("total_caught", 0) if user_doc else 0
    raw_harem = user_doc.get("harem", []) if user_doc else []
    gifted_by_tier = (user_doc.get("gifted_by_rarity", {}) if user_doc else {}) or {}
    total_gifted = user_doc.get("total_gifted", 0) if user_doc else 0
    tier_counts = {t: 0 for t in RARITY_TIERS}
    for item in raw_harem:
        if isinstance(item, dict):
            tier = classify_rarity(item.get("rarity", ""))
            if tier in tier_counts:
                tier_counts[tier] += 1
    tier_block = []
    for tier in RARITY_TIERS:
        cnt = tier_counts[tier]
        gifted = gifted_by_tier.get(tier, 0)
        if cnt == 0 and gifted == 0:
            continue
        ever_total = cnt + gifted
        tier_block.append(
            f"{RARITY_EMOJI[tier]} <b>{tier}</b> — <code>{cnt}</code> (<code>{ever_total}</code>)\n"
            f"<code>{build_progress_bar(cnt, total_caught)}</code>"
        )
    body = "\n".join(tier_block) if tier_block else "<i>No characters caught yet.</i>"
    text = (
        f"📊 <b>RARITY VAULT</b> — {mention}\n"
        f"<i>current (total ever obtained, including gifted-away cards)</i>\n"
        f"<blockquote>{body}</blockquote>"
    )
    if total_gifted:
        text += f"\n🎁 <b>Total Gifted Away:</b> <code>{total_gifted}</code>"
    buttons = [[Button.inline("🔙 Back to Profile", data=f"pf_main_{user_id}")]]
    return text, buttons

@bot1.on(events.NewMessage(pattern=r'^/(profile|myinfo)(?:@\w+)?$'))
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

@bot1.on(events.NewMessage(pattern=r'^/achievements(?:@\w+)?$'))
async def achievements_handler(event):
    user_id = event.sender_id
    mention = await get_html_mention(event, user_id)
    await ensure_user_registered(user_id, await get_plain_name(event, user_id))
    await check_and_award_achievements(user_id)
    await send_achievements_page(bot1, event.chat_id, user_id, mention, page=1)

# ==========================================
# 🔗 REFERRAL
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/referral(?:@\w+)?$'))
async def referral_info_handler(event):
    user_id = event.sender_id
    mention = await get_html_mention(event, user_id)
    await ensure_user_registered(user_id, await get_plain_name(event, user_id))
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    ref_count = user_doc.get("referral_count", 0) if user_doc else 0
    bot_me = await bot1.get_me()
    ref_link = f"https://t.me/{bot_me.username}?start=ref_{user_id}"
    msg = f"🔗 <b>YOUR REFERRAL LINK</b>\n⚡ ━━━━━━━━━━━━━━━ ⚡\nShare this link! You get <code>+2000 MMK</code>, friend gets <code>+1000 MMK</code>.\n\n🔗 <code>{ref_link}</code>\n\n👥 <b>Total Invited:</b> <code>{ref_count} Friends</code>"
    share_url = f"https://t.me/share/url?url={ref_link}&text=Join%20Makima%27s%20Bot%20now!"
    await event.reply(msg, parse_mode='html', buttons=[[Button.url("📤 Share Link", share_url)]])

# ==========================================
# 🔍 CHECK CHARACTER (Updated: spawn_count now reflects actual catches)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/check(?:@\w+)?$'))
async def check_bare_usage_handler(event):
    await event.reply(
        "📌 <b>Usage:</b> <code>/check [CharID]</code>\n"
        "<i>Example:</i> <code>/check 1234</code>",
        parse_mode='html'
    )

@bot1.on(events.NewMessage(pattern=r'^/check(?:@\w+)?\s+([a-zA-Z0-9_]+)$'))
async def check_character_id_handler(event):
    char_id_input = normalize_char_id_input(event.pattern_match.group(1))
    
    # ✅ Case-Insensitive ရှာဖို့
    character = await characters_base_col.find_one({
        "char_id": {"$regex": f"^{char_id_input}$", "$options": "i"}
    })
    
    if not character:
        return await event.reply(f"<b>✗ Character ID not found!</b>", parse_mode='html')
    
    # ✅ spawn_count က catch အရေအတွက်ကိုပြတယ်
    spawn_count = character.get("spawn_count", 0)
    limit = character.get("spawn_limit", 0)
    
    # ✅ ပြသပုံ
    if limit == 0:
        spawns_line = f"<code>{spawn_count}</code> (♾️ Infinite)"
    else:
        remaining = max(0, limit - spawn_count)
        spawns_line = f"<code>{spawn_count}/{limit}</code> (<code>{remaining} left</code>)"
    
    # ✅ Top 10 Collectors
    pipeline = [
        {"$match": {"harem.char_id": character['char_id']}},
        {"$project": {
            "fullname": "$fullname", 
            "user_id": "$user_id", 
            "count": {"$size": {"$filter": {
                "input": "$harem", 
                "as": "item", 
                "cond": {"$eq": ["$$item.char_id", character['char_id']]}
            }}}
        }},
        {"$sort": {"count": -1}}, 
        {"$limit": 10}
    ]
    top_hunters = await users_catcher_col.aggregate(pipeline).to_list(length=10)
    
    leaderboard_str = ""
    for idx, u in enumerate(top_hunters, start=1):
        uname = escape_html(clean_display_name(u.get("fullname"), fallback=f"Agent {u['user_id']}"))
        leaderboard_str += f" <b>{idx}.</b> {uname} — <code>x{u['count']}</code>\n"
    
    # ✅ Media
    media_file = None
    try:
        storage_msg = await bot1.get_messages(SPECIFIC_CONTROL_GROUP, ids=character["storage_msg_id"])
        if storage_msg and storage_msg.media:
            media_file = storage_msg.media
    except: 
        media_file = None
    
    info_text = (
        f"⛓️ <b>Here, the record Makima kept on this one</b>\n\n"
        f"🍬 <b>Asset ID:</b> <code>{display_char_id(character['char_id'])}</code>\n"
        f"☃️ <b>Name:</b> <code>{character['name']}</code>\n"
        f"🫧 <b>Category:</b> <code>{character['category']}</code>\n"
        f"🚩 <b>Rarity:</b> {character['rarity']}\n"
        f"{artist_line(character)}"
        f"🎡 <b>Event:</b> <code>{escape_html(character.get('event', 'General'))}</code>\n"
        f"📈 <b>Global Catches:</b> {spawns_line}\n\n"
        f"🏆 <b>TOP 10 COLLECTORS</b>\n"
        f"<blockquote>{leaderboard_str if leaderboard_str else 'No collectors yet.'}</blockquote>"
    )
    await event.reply(info_text, parse_mode='html', file=media_file)
# ==========================================
# ==========================================
# 📖 DEX (Fixed - No Redis Dependency)
# ==========================================
DEX_CATEGORIES_PER_PAGE = 15
DEX_CARDS_PER_PAGE = 15

def _dex_rarity_weight(rarity_str):
    tier = classify_rarity(rarity_str)
    return {"LEGENDARY": 6, "LIMITED": 5, "MYTHIC": 4, "EPIC": 3, "RARE": 2, "COMMON": 1}.get(tier, 0)

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
@bot1.on(events.NewMessage(pattern=r'^/dex(?:@\w+)?$'))
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
    
    text = f"📖 <b>MAKIMA'S CHARACTER DEX</b>\n"
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
@bot1.on(events.NewMessage(pattern=r'^/search(?:@\w+)?\s+(.+)$'))
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
@bot1.on(events.NewMessage(pattern=r'^/dex(?:@\w+)?\s+(.+)$'))
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
@bot1.on(events.NewMessage(pattern=r'^/sell(?:@\w+)?$'))
async def sell_bare_usage_handler(event):
    await event.reply(
        "📌 <b>Usage:</b> <code>/sell [CharID] [Price]</code>\n"
        "<i>Example:</i> <code>/sell 1234 500</code>",
        parse_mode='html'
    )

@bot1.on(events.NewMessage(pattern=r'^/sell(?:@\w+)?\s+([a-zA-Z0-9_]+)\s+(\d+)$'))
async def sell_market_handler(event):
    user_id = event.sender_id
    char_id = normalize_char_id_input(event.pattern_match.group(1))
    price = int(event.pattern_match.group(2))
    if price <= 0: return await event.reply(f"❌ <b>Price must be at least 1 MMK.</b>", parse_mode='html')
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    user_harem = user_doc.get("harem", []) if user_doc else []
    char_item = next((x for x in user_harem if isinstance(x, dict) and x.get("char_id") == char_id and x.get("status") != "market"), None)
    if not char_item: return await event.reply(f"❌ <b>You don’t have this card available.</b>", parse_mode='html')
    char_data = await characters_base_col.find_one({"char_id": char_id})
    if not char_data: return await event.reply(f"❌ <b>Character ID not found.</b>", parse_mode='html')
    char_item["status"] = "market"
    await users_catcher_col.update_one({"user_id": user_id}, {"$set": {"harem": user_harem}})
    listing_id = f"L{random.randint(1000, 9999)}"
    mention = await get_html_mention(event, user_id)
    await marketplace_col.insert_one({"listing_id": listing_id, "seller_id": user_id, "seller_name": mention, "char_id": char_id, "char_name": char_data["name"], "price": price, "timestamp": time.time()})
    await event.reply(f"🏪 <b>LISTING CREATED</b>\n🎫 <b>ID:</b> <code>{listing_id}</code>\n👤 <b>Card:</b> <code>{char_data['name']}</code>\n💰 <b>Price:</b> <code>{price} MMK</code>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/buy(?:@\w+)?$'))
async def buy_bare_usage_handler(event):
    await event.reply(
        "📌 <b>Usage:</b> <code>/buy [CharID] (SellerID)</code>\n"
        "<i>Example:</i> <code>/buy 1234</code>\n"
        "<i>Browse listings with</i> <code>/market</code>.",
        parse_mode='html'
    )

@bot1.on(events.NewMessage(pattern=r'^/buy(?:@\w+)?\s+([a-zA-Z0-9_]+)(?:\s+(\d+))?$'))
async def buy_market_handler(event):
    buyer_id = event.sender_id
    char_id = normalize_char_id_input(event.pattern_match.group(1))
    target_seller_id = event.pattern_match.group(2)
    if target_seller_id:
        target_seller_id = int(target_seller_id)
        if buyer_id == target_seller_id: return await event.reply(f"❌ <b>You cannot buy your own card.</b>", parse_mode='html')
        listing = await marketplace_col.find_one({"char_id": char_id, "seller_id": target_seller_id})
        if not listing: return await event.reply(f"❌ <b>No listing found.</b>", parse_mode='html')
        price = listing["price"]
        buyer_doc = await users_catcher_col.find_one({"user_id": buyer_id})
        if not buyer_doc or buyer_doc.get("wallet_balance", 0) < price: return await event.reply(f"❌ <b>Insufficient balance!</b>", parse_mode='html')
        char_data = await characters_base_col.find_one({"char_id": char_id})
        char_rarity = char_data.get("rarity", "Unknown") if char_data else "Unknown"
        seller_doc = await users_catcher_col.find_one({"user_id": target_seller_id})
        seller_harem = seller_doc.get("harem", []) if seller_doc else []
        item_to_remove = next((x for x in seller_harem if isinstance(x, dict) and x.get("char_id") == char_id and x.get("status") == "market"), None)
        if item_to_remove: seller_harem.remove(item_to_remove)
        res = await users_catcher_col.update_one(
            {"user_id": buyer_id, "wallet_balance": {"$gte": price}},
            {"$inc": {"wallet_balance": -price, "total_caught": 1}, "$push": {"harem": {"char_id": char_id, "caught_date": time.time(), "rarity": char_rarity, "status": "vault"}}}
        )
        if res.modified_count == 0: return await event.reply("❌ Transaction failed!")
        await users_catcher_col.update_one({"user_id": target_seller_id}, {"$set": {"harem": seller_harem}, "$inc": {"wallet_balance": price}})
        await clear_stale_favorite(target_seller_id, char_id, seller_harem)
        await marketplace_col.delete_one({"listing_id": listing["listing_id"]})
        return await event.reply(f"🎉 <b>Purchase successful!</b>", parse_mode='html')
    listings = await marketplace_col.find({"char_id": char_id}).sort("price", 1).to_list(length=10)
    if not listings: return await event.reply(f"❌ <b>No listings for this card.</b>", parse_mode='html')
    listing_char_data = await characters_base_col.find_one({"char_id": char_id})
    market_text = f"🛒 <b>LISTINGS FOR {char_id}</b>\n⚡ ━━━━ ⚡\n{artist_line(listing_char_data)}\n"
    buttons = []
    for item in listings:
        market_text += f"👤 <b>Seller:</b> {item['seller_name']} [<code>{item['seller_id']}</code>]\n💰 <b>Price:</b> <code>{item['price']} MMK</code>\n\n"
        buttons.append([Button.inline(f"Buy for {item['price']} MMK", data=f"mktbuy_{item['listing_id']}")])
    await event.reply(market_text, parse_mode='html', buttons=buttons)

# ==========================================
# 📈 CHARACTER STOCK MARKET — COMMANDS
# ==========================================
# This is a separate system from /buy /sell (which trade the actual character CARDS).
# Here players invest MMK straight into a character's popularity — buying "shares" that
# rise and fall with catch demand, trading pressure, and background market drift —
# without ever owning the card itself.

async def get_stock_market_snapshot():
    """Merged character+stock price list, cached briefly since /stocks can be tapped a lot.
    Characters that have never been traded show their base worth with 0% change until
    someone actually touches them (via /stock, /invest, or a catch)."""
    try:
        cached = await redis_client.get("cache:stock:snapshot")
        if cached:
            return json.loads(cached)
    except Exception as e:
        print(f"⚠️ Redis stock snapshot error: {e}")
    all_chars = await get_all_characters_cached()
    stocks = await character_stocks_col.find({}).to_list(length=None)
    stock_map = {s["char_id"]: s for s in stocks}
    snapshot = []
    for c in all_chars:
        cid = c.get("char_id")
        if not cid:
            continue
        s = stock_map.get(cid)
        if s:
            price, day_open = s["price"], s.get("day_open_price", s["price"])
        else:
            price = day_open = max(float(c.get("currency_value", 100)), 10.0)
        snapshot.append({"char_id": cid, "name": c.get("name", "?"), "rarity": c.get("rarity", ""),
                          "price": price, "day_open_price": day_open})
    snapshot.sort(key=lambda x: (-_rarity_weight_for_sort(x["rarity"]), x["name"].lower()))
    try:
        await redis_client.setex("cache:stock:snapshot", 20, json.dumps(snapshot, default=str))
    except Exception as e:
        print(f"⚠️ Redis stock snapshot write error: {e}")
    return snapshot

async def render_stock_list_page(page):
    snapshot = await get_stock_market_snapshot()
    total = len(snapshot)
    total_pages = max(1, (total + STOCK_PAGE_SIZE - 1) // STOCK_PAGE_SIZE)
    start = page * STOCK_PAGE_SIZE
    page_rows = snapshot[start:start + STOCK_PAGE_SIZE]
    title = f"📈 <b>Character Stock Market</b> <i>({min(page + 1, total_pages)}/{total_pages})</i>"
    buttons = []
    if not page_rows:
        body = "<i>No characters listed yet.</i>"
    else:
        lines = []
        for row in page_rows:
            chg = stock_change_pct(row["price"], row["day_open_price"])
            arrow = "🟢▲" if chg > 0 else ("🔴▼" if chg < 0 else "⚪️")
            lines.append(f"{arrow} <b>{escape_html(row['name'])}</b> [<code>{display_char_id(row['char_id'])}</code>] — <code>{row['price']:,.0f} MMK</code> <i>({chg:+.1f}%)</i>")
            buttons.append([Button.inline(f"📊 {row['name'][:24]}", data=f"stkview_{row['char_id']}")])
        body = "\n".join(lines)
    text = f"{title}\n<blockquote>{body}</blockquote>\n💡 <i>Tap a name, or use</i> <code>/stock [id]</code> <i>for full details.</i>"
    nav = []
    if page > 0:
        nav.append(Button.inline("◀ Prev", data=f"stklist_{page - 1}"))
    if start + STOCK_PAGE_SIZE < total:
        nav.append(Button.inline("Next ▶", data=f"stklist_{page + 1}"))
    if nav:
        buttons.append(nav)
    return text, buttons

@bot1.on(events.NewMessage(pattern=r'^/stocks(?:@\w+)?$'))
async def stock_list_handler(event):
    text, buttons = await render_stock_list_page(0)
    await event.reply(text, parse_mode='html', buttons=buttons or None)

@bot1.on(events.CallbackQuery(pattern=r'^stklist_(\d+)$'))
async def stock_list_callback(event):
    page = int(event.pattern_match.group(1))
    text, buttons = await render_stock_list_page(page)
    try:
        await event.edit(text, parse_mode='html', buttons=buttons or None)
        await event.answer()
    except errors.MessageNotModifiedError:
        await event.answer()
    except Exception as e:
        await event.answer(f"❌ Error: {e}", alert=True)

async def render_stock_detail(raw_char_id, user_id=None):
    char_id = normalize_char_id_input(raw_char_id)
    char_doc = await characters_base_col.find_one({"char_id": char_id})
    if not char_doc:
        return None, None
    stock = await get_or_create_stock(char_id, char_doc)
    chg = stock_change_pct(stock["price"], stock.get("day_open_price", stock["price"]))
    arrow = "🟢 ▲" if chg > 0 else ("🔴 ▼" if chg < 0 else "⚪️ –")
    spark = stock_sparkline(stock.get("history", []))
    tier = _stock_tier_for_char(char_doc)
    text = (
        f"📈 <b>{escape_html(char_doc.get('name', '?'))}</b> [<code>{display_char_id(char_id)}</code>]\n"
        f"{RARITY_EMOJI.get(tier, RARITY_DEFAULT_EMOJI)} <b>Rarity:</b> {char_doc.get('rarity', '?')}\n"
        f"{artist_line(char_doc)}"
        f"<blockquote>"
        f"💰 <b>Price:</b> <code>{stock['price']:,.2f} MMK</code>\n"
        f"{arrow} <b>Today:</b> <code>{chg:+.2f}%</code> <i>(opened {stock.get('day_open_price', 0):,.0f})</i>\n"
        f"📊 <b>Trend:</b> <code>{spark}</code>\n"
        f"🔄 <b>Volume today:</b> <code>+{stock.get('buy_volume_today', 0):,.0f}</code> / <code>-{stock.get('sell_volume_today', 0):,.0f} MMK</code>"
        f"</blockquote>"
    )
    if user_id:
        user_doc = await users_catcher_col.find_one({"user_id": user_id})
        portfolio = user_doc.get("stock_portfolio", []) if user_doc else []
        holding = next((h for h in portfolio if h.get("char_id") == char_id), None)
        if holding and holding.get("shares", 0) > 0:
            cur_val = holding["shares"] * stock["price"]
            pl = cur_val - (holding["shares"] * holding.get("avg_cost", 0))
            pl_pct = stock_change_pct(stock["price"], holding.get("avg_cost", 0))
            pl_emoji = "🟢" if pl >= 0 else "🔴"
            text += (
                f"\n👤 <b>Your Holding</b>\n<blockquote>"
                f"📦 <code>{holding['shares']:,.4f} shares</code> @ avg <code>{holding.get('avg_cost', 0):,.2f} MMK</code>\n"
                f"💵 <b>Value:</b> <code>{cur_val:,.2f} MMK</code>\n"
                f"{pl_emoji} <b>P/L:</b> <code>{pl:+,.2f} MMK ({pl_pct:+.1f}%)</code>"
                f"</blockquote>"
            )
    text += f"\n💡 <i>Custom amount:</i> <code>/invest {display_char_id(char_id)} [MMK]</code> <i>·</i> <code>/divest {display_char_id(char_id)} [shares|all]</code>"
    buttons = [
        [Button.inline("💰 +500", data=f"stkbuy_{char_id}_500"), Button.inline("💰 +1000", data=f"stkbuy_{char_id}_1000"), Button.inline("💰 +5000", data=f"stkbuy_{char_id}_5000")],
        [Button.inline("📉 -25%", data=f"stksell_{char_id}_25"), Button.inline("📉 -50%", data=f"stksell_{char_id}_50"), Button.inline("📉 -100%", data=f"stksell_{char_id}_100")],
        [Button.inline("🔄 Refresh", data=f"stkview_{char_id}"), Button.inline("🔙 Market", data="stklist_0")]
    ]
    return text, buttons

@bot1.on(events.NewMessage(pattern=r'^/stock(?:@\w+)?\s+(\S+)$'))
async def stock_detail_handler(event):
    text, buttons = await render_stock_detail(event.pattern_match.group(1), event.sender_id)
    if not text:
        return await event.reply("❌ <b>Character ID not found.</b>", parse_mode='html')
    await event.reply(text, parse_mode='html', buttons=buttons)

@bot1.on(events.NewMessage(pattern=r'^/stock(?:@\w+)?$'))
async def stock_detail_bare_usage(event):
    await event.reply(
        "📌 <b>Usage:</b> <code>/stock [CharID]</code>\n<i>Example:</i> <code>/stock 1234</code>\n"
        "<i>Browse everything with</i> <code>/stocks</code>.",
        parse_mode='html'
    )

@bot1.on(events.CallbackQuery(pattern=r'^stkview_(\S+)$'))
async def stock_view_callback(event):
    char_id = event.pattern_match.group(1)
    if isinstance(char_id, bytes):
        char_id = char_id.decode('utf-8')
    text, buttons = await render_stock_detail(char_id, event.sender_id)
    if not text:
        return await event.answer("❌ Character not found.", alert=True)
    try:
        await event.edit(text, parse_mode='html', buttons=buttons)
        await event.answer()
    except errors.MessageNotModifiedError:
        await event.answer()
    except Exception as e:
        await event.answer(f"❌ Error: {e}", alert=True)

async def execute_stock_buy(user_id, raw_char_id, mmk_amount):
    """Returns (ok, html_message)."""
    char_id = normalize_char_id_input(raw_char_id)
    if mmk_amount < STOCK_MIN_TRADE_MMK:
        return False, f"❌ <b>Minimum invest amount is {STOCK_MIN_TRADE_MMK} MMK.</b>"
    char_doc = await characters_base_col.find_one({"char_id": char_id})
    if not char_doc:
        return False, "❌ <b>Character ID not found.</b>"
    res = await users_catcher_col.update_one(
        {"user_id": user_id, "wallet_balance": {"$gte": mmk_amount}},
        {"$inc": {"wallet_balance": -mmk_amount}}
    )
    if res.modified_count == 0:
        return False, "❌ <b>Insufficient balance!</b>"
    stock = await get_or_create_stock(char_id, char_doc)
    exec_price = stock["price"]
    shares_bought = round(mmk_amount / exec_price, 4)
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    portfolio = user_doc.get("stock_portfolio", []) if user_doc else []
    holding = next((h for h in portfolio if h.get("char_id") == char_id), None)
    if holding:
        total_cost = holding["shares"] * holding.get("avg_cost", 0) + mmk_amount
        holding["shares"] = round(holding["shares"] + shares_bought, 4)
        holding["avg_cost"] = round(total_cost / holding["shares"], 4) if holding["shares"] > 0 else exec_price
    else:
        portfolio.append({"char_id": char_id, "shares": shares_bought, "avg_cost": exec_price})
    await users_catcher_col.update_one({"user_id": user_id}, {"$set": {"stock_portfolio": portfolio}})
    new_price = await apply_stock_trade_impact(char_id, mmk_amount, 1, char_doc)
    await recompute_user_portfolio_value(user_id, portfolio)
    msg = (
        f"📈 <b>INVESTED</b>\n"
        f"👤 <b>{escape_html(char_doc.get('name', '?'))}</b> [<code>{display_char_id(char_id)}</code>]\n"
        f"💵 <b>Spent:</b> <code>{mmk_amount:,} MMK</code>\n"
        f"📦 <b>Got:</b> <code>{shares_bought:,.4f} shares</code> @ <code>{exec_price:,.2f} MMK</code>\n"
        f"🔺 <b>New Price:</b> <code>{new_price:,.2f} MMK</code>"
    )
    return True, msg

async def execute_stock_sell(user_id, raw_char_id, shares_or_pct, is_pct=False):
    """Returns (ok, html_message)."""
    char_id = normalize_char_id_input(raw_char_id)
    char_doc = await characters_base_col.find_one({"char_id": char_id})
    if not char_doc:
        return False, "❌ <b>Character ID not found.</b>"
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    portfolio = user_doc.get("stock_portfolio", []) if user_doc else []
    holding = next((h for h in portfolio if h.get("char_id") == char_id), None)
    if not holding or holding.get("shares", 0) <= 0:
        return False, "❌ <b>You don't own any shares of this character.</b>"
    max_shares = holding["shares"]
    sell_shares = round(max_shares * (shares_or_pct / 100.0), 4) if is_pct else round(min(shares_or_pct, max_shares), 4)
    if sell_shares <= 0:
        return False, "❌ <b>Nothing to sell.</b>"
    stock = await get_or_create_stock(char_id, char_doc)
    exec_price = stock["price"]
    proceeds = round(sell_shares * exec_price, 2)
    avg_cost = holding.get("avg_cost", 0)
    realized_pl = round(proceeds - (sell_shares * avg_cost), 2)
    holding["shares"] = round(max_shares - sell_shares, 4)
    if holding["shares"] <= 0.0001:
        portfolio = [h for h in portfolio if h.get("char_id") != char_id]
    await users_catcher_col.update_one(
        {"user_id": user_id},
        {"$set": {"stock_portfolio": portfolio}, "$inc": {"wallet_balance": proceeds}}
    )
    new_price = await apply_stock_trade_impact(char_id, proceeds, -1, char_doc)
    await recompute_user_portfolio_value(user_id, portfolio)
    pl_emoji = "🟢" if realized_pl >= 0 else "🔴"
    msg = (
        f"📉 <b>DIVESTED</b>\n"
        f"👤 <b>{escape_html(char_doc.get('name', '?'))}</b> [<code>{display_char_id(char_id)}</code>]\n"
        f"📦 <b>Sold:</b> <code>{sell_shares:,.4f} shares</code> @ <code>{exec_price:,.2f} MMK</code>\n"
        f"💵 <b>Proceeds:</b> <code>{proceeds:,.2f} MMK</code>\n"
        f"{pl_emoji} <b>Realized P/L:</b> <code>{realized_pl:+,.2f} MMK</code>\n"
        f"🔻 <b>New Price:</b> <code>{new_price:,.2f} MMK</code>"
    )
    return True, msg

@bot1.on(events.NewMessage(pattern=r'^/invest(?:@\w+)?\s+(\S+)\s+(\d+)$'))
async def stock_invest_handler(event):
    ok, msg = await execute_stock_buy(event.sender_id, event.pattern_match.group(1), int(event.pattern_match.group(2)))
    await event.reply(msg, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/invest(?:@\w+)?$'))
async def stock_invest_bare_usage(event):
    await event.reply(
        f"📌 <b>Usage:</b> <code>/invest [CharID] [MMK]</code>\n<i>Example:</i> <code>/invest 1234 500</code>\n"
        f"<i>Minimum:</i> <code>{STOCK_MIN_TRADE_MMK} MMK</code>",
        parse_mode='html'
    )

@bot1.on(events.NewMessage(pattern=r'^/divest(?:@\w+)?\s+(\S+)\s+(all|\d+(?:\.\d+)?)$'))
async def stock_divest_handler(event):
    amt_str = event.pattern_match.group(2)
    if amt_str == "all":
        ok, msg = await execute_stock_sell(event.sender_id, event.pattern_match.group(1), 100, is_pct=True)
    else:
        ok, msg = await execute_stock_sell(event.sender_id, event.pattern_match.group(1), float(amt_str), is_pct=False)
    await event.reply(msg, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/divest(?:@\w+)?$'))
async def stock_divest_bare_usage(event):
    await event.reply(
        "📌 <b>Usage:</b> <code>/divest [CharID] [Shares|all]</code>\n<i>Example:</i> <code>/divest 1234 all</code>",
        parse_mode='html'
    )

@bot1.on(events.CallbackQuery(pattern=r'^stkbuy_(\S+)_(\d+)$'))
async def stock_buy_callback(event):
    char_id, amount = event.pattern_match.group(1), int(event.pattern_match.group(2))
    if isinstance(char_id, bytes):
        char_id = char_id.decode('utf-8')
    ok, msg = await execute_stock_buy(event.sender_id, char_id, amount)
    if not ok:
        return await event.answer(re.sub(r'<[^>]+>', '', msg)[:200], alert=True)
    await event.answer("✅ Invested!")
    text, buttons = await render_stock_detail(char_id, event.sender_id)
    try:
        await event.edit(text, parse_mode='html', buttons=buttons)
    except errors.MessageNotModifiedError:
        pass
    except Exception:
        pass

@bot1.on(events.CallbackQuery(pattern=r'^stksell_(\S+)_(\d+)$'))
async def stock_sell_callback(event):
    char_id, pct = event.pattern_match.group(1), int(event.pattern_match.group(2))
    if isinstance(char_id, bytes):
        char_id = char_id.decode('utf-8')
    ok, msg = await execute_stock_sell(event.sender_id, char_id, pct, is_pct=True)
    if not ok:
        return await event.answer(re.sub(r'<[^>]+>', '', msg)[:200], alert=True)
    await event.answer("✅ Divested!")
    text, buttons = await render_stock_detail(char_id, event.sender_id)
    try:
        await event.edit(text, parse_mode='html', buttons=buttons)
    except errors.MessageNotModifiedError:
        pass
    except Exception:
        pass

async def render_portfolio_page(user_id, page):
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    portfolio = user_doc.get("stock_portfolio", []) if user_doc else []
    if not portfolio:
        text = "💼 <b>Your Portfolio</b>\n<blockquote><i>You don't hold any shares yet — try </i><code>/stocks</code><i> to browse the market!</i></blockquote>"
        return text, []
    grand_total = await recompute_user_portfolio_value(user_id, portfolio)
    invested_total = sum(h.get("shares", 0) * h.get("avg_cost", 0) for h in portfolio)
    overall_pl = grand_total - invested_total
    pl_emoji = "🟢" if overall_pl >= 0 else "🔴"
    total = len(portfolio)
    total_pages = max(1, (total + STOCK_PAGE_SIZE - 1) // STOCK_PAGE_SIZE)
    start = page * STOCK_PAGE_SIZE
    page_rows = portfolio[start:start + STOCK_PAGE_SIZE]
    lines = []
    for h in page_rows:
        stock = await get_or_create_stock(h["char_id"])
        char_doc = await characters_base_col.find_one({"char_id": h["char_id"]})
        name = char_doc.get("name", h["char_id"]) if char_doc else h["char_id"]
        cur_val = h.get("shares", 0) * stock["price"]
        pl_pct = stock_change_pct(stock["price"], h.get("avg_cost", 0))
        arrow = "🟢" if pl_pct >= 0 else "🔴"
        lines.append(
            f"{arrow} <b>{escape_html(name)}</b> [<code>{display_char_id(h['char_id'])}</code>]\n"
            f"<code>{h.get('shares', 0):,.4f} sh</code> @ <code>{h.get('avg_cost', 0):,.2f}</code> → <code>{cur_val:,.2f} MMK</code> <i>({pl_pct:+.1f}%)</i>"
        )
    title = (
        f"💼 <b>Your Portfolio</b> <i>({min(page + 1, total_pages)}/{total_pages})</i>\n"
        f"💵 <b>Total Value:</b> <code>{grand_total:,.2f} MMK</code>\n"
        f"{pl_emoji} <b>Unrealized P/L:</b> <code>{overall_pl:+,.2f} MMK</code>"
    )
    text = f"{title}\n<blockquote>{chr(10).join(lines)}</blockquote>"
    nav = []
    if page > 0:
        nav.append(Button.inline("◀ Prev", data=f"stkpf_{page - 1}"))
    if start + STOCK_PAGE_SIZE < total:
        nav.append(Button.inline("Next ▶", data=f"stkpf_{page + 1}"))
    buttons = [nav] if nav else []
    return text, buttons

@bot1.on(events.NewMessage(pattern=r'^/portfolio(?:@\w+)?$'))
async def portfolio_handler(event):
    text, buttons = await render_portfolio_page(event.sender_id, 0)
    await event.reply(text, parse_mode='html', buttons=buttons or None)

@bot1.on(events.CallbackQuery(pattern=r'^stkpf_(\d+)$'))
async def portfolio_callback(event):
    page = int(event.pattern_match.group(1))
    text, buttons = await render_portfolio_page(event.sender_id, page)
    try:
        await event.edit(text, parse_mode='html', buttons=buttons or None)
        await event.answer()
    except errors.MessageNotModifiedError:
        await event.answer()
    except Exception as e:
        await event.answer(f"❌ Error: {e}", alert=True)

# ==========================================
# ₿ BITCOIN EXCHANGE — COMMANDS
# ==========================================
# One global rate for the whole game, unlike the per-character stock market above.
# Its background drift is partly pulled by the character stock market's mood (see
# _compute_stock_market_sentiment), so the two systems visibly move together.

async def render_btc_page(user_id=None):
    market = await get_or_create_btc_market()
    chg = stock_change_pct(market["price"], market.get("day_open_price", market["price"]))
    arrow = "🟢 ▲" if chg > 0 else ("🔴 ▼" if chg < 0 else "⚪️ –")
    spark = stock_sparkline(market.get("history", []))
    text = (
        f"₿ <b>Bitcoin Exchange</b>\n"
        f"<blockquote>"
        f"💰 <b>Rate:</b> <code>1 BTC = {market['price']:,.0f} MMK</code>\n"
        f"{arrow} <b>Today:</b> <code>{chg:+.2f}%</code> <i>(opened {market.get('day_open_price', 0):,.0f})</i>\n"
        f"📊 <b>Trend:</b> <code>{spark}</code>\n"
        f"🔄 <b>Volume today:</b> <code>+{market.get('buy_volume_today', 0):,.0f}</code> / <code>-{market.get('sell_volume_today', 0):,.0f} MMK</code>\n"
        f"💸 <b>Exchange Fee:</b> <code>{BTC_TRADE_FEE_PCT * 100:.1f}%</code>"
        f"</blockquote>"
    )
    if user_id:
        user_doc = await users_catcher_col.find_one({"user_id": user_id})
        btc_balance = user_doc.get("btc_balance", 0.0) if user_doc else 0.0
        mmk_balance = user_doc.get("wallet_balance", 0) if user_doc else 0
        btc_value = round(btc_balance * market["price"], 2)
        text += (
            f"\n👤 <b>Your Wallet</b>\n<blockquote>"
            f"🪙 <code>{mmk_balance:,} MMK</code>\n"
            f"₿ <code>{btc_balance:,.8f} BTC</code> <i>(≈ {btc_value:,.2f} MMK)</i>"
            f"</blockquote>"
        )
    text += "\n💡 <i>Custom amount:</i> <code>/buybtc [MMK]</code> <i>·</i> <code>/sellbtc [BTC|all]</code>"
    buttons = [
        [Button.inline("💰 +10K", data="btcbuy_10000"), Button.inline("💰 +50K", data="btcbuy_50000"), Button.inline("💰 +200K", data="btcbuy_200000")],
        [Button.inline("📉 -25%", data="btcsell_25"), Button.inline("📉 -50%", data="btcsell_50"), Button.inline("📉 -100%", data="btcsell_100")],
        [Button.inline("🔄 Refresh", data="btcview")]
    ]
    return text, buttons

@bot1.on(events.NewMessage(pattern=r'^/(btc|exchange)(?:@\w+)?$'))
async def btc_page_handler(event):
    text, buttons = await render_btc_page(event.sender_id)
    await event.reply(text, parse_mode='html', buttons=buttons)

@bot1.on(events.CallbackQuery(pattern=r'^btcview$'))
async def btc_view_callback(event):
    text, buttons = await render_btc_page(event.sender_id)
    try:
        await event.edit(text, parse_mode='html', buttons=buttons)
        await event.answer()
    except errors.MessageNotModifiedError:
        await event.answer()
    except Exception as e:
        await event.answer(f"❌ Error: {e}", alert=True)

async def execute_btc_buy(user_id, mmk_amount):
    """Returns (ok, html_message)."""
    if mmk_amount < BTC_MIN_TRADE_MMK:
        return False, f"❌ <b>Minimum buy amount is {BTC_MIN_TRADE_MMK} MMK.</b>"
    fee = round(mmk_amount * BTC_TRADE_FEE_PCT, 2)
    net_amount = mmk_amount - fee
    res = await users_catcher_col.update_one(
        {"user_id": user_id, "wallet_balance": {"$gte": mmk_amount}},
        {"$inc": {"wallet_balance": -mmk_amount}}
    )
    if res.modified_count == 0:
        return False, "❌ <b>Insufficient balance!</b>"
    market = await get_or_create_btc_market()
    exec_price = market["price"]
    btc_bought = round(net_amount / exec_price, 8)
    await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"btc_balance": btc_bought}})
    new_price = await apply_btc_trade_impact(mmk_amount, 1)
    await recompute_user_btc_value(user_id)
    msg = (
        f"₿ <b>BOUGHT BITCOIN</b>\n"
        f"💵 <b>Paid:</b> <code>{mmk_amount:,} MMK</code> <i>(fee {fee:,.0f} MMK)</i>\n"
        f"₿ <b>Got:</b> <code>{btc_bought:,.8f} BTC</code> @ <code>{exec_price:,.0f} MMK</code>\n"
        f"🔺 <b>New Rate:</b> <code>1 BTC = {new_price:,.0f} MMK</code>"
    )
    return True, msg

async def execute_btc_sell(user_id, btc_or_pct, is_pct=False):
    """Returns (ok, html_message)."""
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    btc_balance = user_doc.get("btc_balance", 0.0) if user_doc else 0.0
    if btc_balance <= 0:
        return False, "❌ <b>You don't own any BTC.</b>"
    sell_amount = round(btc_balance * (btc_or_pct / 100.0), 8) if is_pct else round(min(btc_or_pct, btc_balance), 8)
    if sell_amount <= 0:
        return False, "❌ <b>Nothing to sell.</b>"
    market = await get_or_create_btc_market()
    exec_price = market["price"]
    gross_proceeds = sell_amount * exec_price
    fee = round(gross_proceeds * BTC_TRADE_FEE_PCT, 2)
    net_proceeds = round(gross_proceeds - fee, 2)
    res = await users_catcher_col.update_one(
        {"user_id": user_id, "btc_balance": {"$gte": sell_amount}},
        {"$inc": {"btc_balance": -sell_amount, "wallet_balance": net_proceeds}}
    )
    if res.modified_count == 0:
        return False, "❌ <b>Insufficient BTC balance!</b>"
    new_price = await apply_btc_trade_impact(gross_proceeds, -1)
    await recompute_user_btc_value(user_id)
    msg = (
        f"₿ <b>SOLD BITCOIN</b>\n"
        f"₿ <b>Sold:</b> <code>{sell_amount:,.8f} BTC</code> @ <code>{exec_price:,.0f} MMK</code>\n"
        f"💵 <b>Received:</b> <code>{net_proceeds:,.2f} MMK</code> <i>(fee {fee:,.0f} MMK)</i>\n"
        f"🔻 <b>New Rate:</b> <code>1 BTC = {new_price:,.0f} MMK</code>"
    )
    return True, msg

@bot1.on(events.NewMessage(pattern=r'^/buybtc(?:@\w+)?\s+(\d+)$'))
async def btc_buy_handler(event):
    ok, msg = await execute_btc_buy(event.sender_id, int(event.pattern_match.group(1)))
    await event.reply(msg, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/buybtc(?:@\w+)?$'))
async def btc_buy_bare_usage(event):
    await event.reply(
        f"📌 <b>Usage:</b> <code>/buybtc [MMK]</code>\n<i>Example:</i> <code>/buybtc 5000</code>\n"
        f"<i>Minimum:</i> <code>{BTC_MIN_TRADE_MMK} MMK</code> <i>· Fee:</i> <code>{BTC_TRADE_FEE_PCT * 100:.1f}%</code>",
        parse_mode='html'
    )

@bot1.on(events.NewMessage(pattern=r'^/sellbtc(?:@\w+)?\s+(all|\d+(?:\.\d+)?)$'))
async def btc_sell_handler(event):
    amt_str = event.pattern_match.group(1)
    if amt_str == "all":
        ok, msg = await execute_btc_sell(event.sender_id, 100, is_pct=True)
    else:
        ok, msg = await execute_btc_sell(event.sender_id, float(amt_str), is_pct=False)
    await event.reply(msg, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/sellbtc(?:@\w+)?$'))
async def btc_sell_bare_usage(event):
    await event.reply(
        "📌 <b>Usage:</b> <code>/sellbtc [BTC|all]</code>\n<i>Example:</i> <code>/sellbtc 0.001</code> or <code>/sellbtc all</code>",
        parse_mode='html'
    )

@bot1.on(events.CallbackQuery(pattern=r'^btcbuy_(\d+)$'))
async def btc_buy_callback(event):
    amount = int(event.pattern_match.group(1))
    ok, msg = await execute_btc_buy(event.sender_id, amount)
    if not ok:
        return await event.answer(re.sub(r'<[^>]+>', '', msg)[:200], alert=True)
    await event.answer("✅ Bought!")
    text, buttons = await render_btc_page(event.sender_id)
    try:
        await event.edit(text, parse_mode='html', buttons=buttons)
    except errors.MessageNotModifiedError:
        pass
    except Exception:
        pass

@bot1.on(events.CallbackQuery(pattern=r'^btcsell_(\d+)$'))
async def btc_sell_callback(event):
    pct = int(event.pattern_match.group(1))
    ok, msg = await execute_btc_sell(event.sender_id, pct, is_pct=True)
    if not ok:
        return await event.answer(re.sub(r'<[^>]+>', '', msg)[:200], alert=True)
    await event.answer("✅ Sold!")
    text, buttons = await render_btc_page(event.sender_id)
    try:
        await event.edit(text, parse_mode='html', buttons=buttons)
    except errors.MessageNotModifiedError:
        pass
    except Exception:
        pass

# ==========================================
# 🏆 LEADERBOARDS
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/top(?:@\w+)?$'))
async def local_group_top_handler(event):
    if event.is_private: return
    chat_id = event.chat_id
    cursor = users_catcher_col.find({f"group_catches.{str(chat_id)}": {"$gt": 0}}).sort(f"group_catches.{str(chat_id)}", -1).limit(10)
    top_users = await cursor.to_list(length=10)
    if not top_users: return await event.reply(f"🏆 <b>No rank data in this group yet.</b>", parse_mode='html')
    msg = f"🏆 <b>TOP 10 HUNTERS IN THIS GROUP</b>\n⚡ ━━━━⚡\n"
    for i, u in enumerate(top_users):
        count = u["group_catches"][str(chat_id)]
        user_display = escape_html(clean_display_name(u.get('fullname'), fallback=f"User {u['user_id']}"))
        msg += f"<b>{i+1}.</b> {user_display} — <code>{count} cards</code>\n"
    await event.reply(msg, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/gtop(?:@\w+)?$'))
async def global_top_handler(event):
    cursor = users_catcher_col.find({"total_caught": {"$gt": 0}}).sort("total_caught", -1).limit(10)
    top_users = await cursor.to_list(length=10)
    if not top_users: return await event.reply(f"🏆 <b>No global ranking yet.</b>", parse_mode='html')
    msg = f"🌐 <b>GLOBAL TOP 10 HUNTERS</b>\n"
    for i, u in enumerate(top_users):
        count = u.get("total_caught", 0)
        user_display = escape_html(clean_display_name(u.get('fullname'), fallback=f"User {u['user_id']}"))
        msg += f"<b>{i+1}.</b> {user_display} — <code>{count} cards</code>\n"
    await event.reply(msg, parse_mode='html')

# ==========================================
# 💰 BALANCE
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/balance(?:@\w+)?$'))
async def check_points_balance(event):
    user_doc = await users_catcher_col.find_one({"user_id": event.sender_id})
    balance = user_doc.get("wallet_balance", 0) if user_doc else 0
    await event.reply(f"💰 <b>Your Balance:</b>\n<blockquote><code>{balance} MMK</code> 🪙</blockquote>", parse_mode='html')

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
@bot1.on(events.NewMessage(pattern=r'^/gift(?:@\w+)?\s+(.+)$'))
async def gift_asset_handler(event):
    if not event.is_reply:
        return await event.reply("❌ <b>Reply to the user you want to gift.</b>", parse_mode='html')
    char_id = normalize_char_id_input(event.pattern_match.group(1))
    sender_id = event.sender_id
    reply_msg = await event.get_reply_message()
    receiver_id = reply_msg.sender_id
    if sender_id == receiver_id:
        return await event.reply("❌ <b>Can't gift to yourself!</b>", parse_mode='html')
    sender_doc = await users_catcher_col.find_one({"user_id": sender_id})
    sender_harem = sender_doc.get("harem", []) if sender_doc else []
    char_item = next((x for x in sender_harem if isinstance(x, dict) and x.get("char_id") == char_id and x.get("status") != "market"), None)
    if not char_item:
        return await event.reply(f"❌ <b>You don’t have this card.</b>", parse_mode='html')
    char_data = await characters_base_col.find_one({"char_id": char_id})
    if not char_data:
        return await event.reply("❌ <b>Character data not found.</b>", parse_mode='html')
    media_file = None
    try:
        storage_msg = await bot1.get_messages(SPECIFIC_CONTROL_GROUP, ids=char_data["storage_msg_id"])
        if storage_msg and storage_msg.media:
            media_file = storage_msg.media
    except Exception:
        pass
    r_mention = await get_html_mention(event, receiver_id)
    confirm_text = (
        f"⛓️ <b>Makima's Gift Confirmation</b>\n\n"
        f"📦 <b>Card:</b> <code>{char_data['name']}</code> (<code>{display_char_id(char_id)}</code>)\n"
        f"🏷️ <b>Rarity:</b> {char_data.get('rarity', 'Unknown')}\n"
        f"{artist_line(char_data)}"
        f"🎯 <b>Receiver:</b> {r_mention}\n\n"
        f"<blockquote>Are you sure you want to send this card to {r_mention}?</blockquote>\n"
        f"✨ Makima is watching! ⛓️"
    )
    buttons = [
        [
            Button.inline("✅ Yep, Why Not✔", data=f"gift_confirm_{sender_id}_{receiver_id}_{char_id}"),
            Button.inline("❌ Nope, Jk✗", data=f"gift_cancel_{sender_id}_{receiver_id}_{char_id}")
        ]
    ]
    if media_file:
        await event.reply(confirm_text, file=media_file, buttons=buttons, parse_mode='html')
    else:
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
    if action == "cancel":
        await event.edit(
            f"❌ <b>Gift Cancelled</b>\n\n"
            f"Card <code>{display_char_id(char_id)}</code> was not sent.\n"
            f"⛓️ Makima says: 'Maybe next time!'",
            parse_mode='html',
            buttons=None
        )
        await event.answer("Gift cancelled.", alert=True)
        return
    # confirm
    sender_doc = await users_catcher_col.find_one({"user_id": sender_id})
    sender_harem = sender_doc.get("harem", []) if sender_doc else []
    char_item = next((x for x in sender_harem if isinstance(x, dict) and x.get("char_id") == char_id and x.get("status") != "market"), None)
    if not char_item:
        await event.edit(
            f"❌ <b>Oops!</b>\n\n"
            f"It seems you no longer have card <code>{display_char_id(char_id)}</code>.\n"
            f"⛓️ Maybe Makima played a little trick on you!",
            parse_mode='html',
            buttons=None
        )
        await event.answer("Card not found.", alert=True)
        return
    sender_harem.remove(char_item)
    gift_tier = char_item.get("rarity_tier") or classify_rarity(char_item.get("rarity", ""))
    await users_catcher_col.update_one(
        {"user_id": sender_id},
        {
            "$set": {"harem": sender_harem},
            "$inc": {"total_gifted": 1, f"gifted_by_rarity.{gift_tier}": 1}
        }
    )
    await clear_stale_favorite(sender_id, char_id, sender_harem)
    r_mention = await get_html_mention(event, receiver_id)
    r_plain_name = await get_plain_name(event, receiver_id)
    await users_catcher_col.update_one(
        {"user_id": receiver_id},
        {
            "$push": {"harem": {"char_id": char_id, "caught_date": time.time(), "rarity": char_item.get("rarity", "Unknown"), "status": "vault"}},
            "$inc": {"total_caught": 1},
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
    await event.edit(
        f"🎁 <b>GIFT SUCCESSFUL!</b>\n\n"
        f"Card <code>{display_char_id(char_id)}</code> has been sent to {r_mention}!\n"
        f"🍔Makima is happy!",
        parse_mode='html',
        buttons=None
    )
    await event.answer("Gift sent successfully!", alert=True)

# 🤝 TRADE
@bot1.on(events.NewMessage(pattern=r'^/trade(?:@\w+)?\s+([a-zA-Z0-9_]+)\s+([a-zA-Z0-9_]+)$'))
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
    buttons = [[Button.inline("🤝 Confirm", data=f"tr_conf_{sender_id}_{target_user_id}_{my_char_id}_{their_char_id}"), Button.inline("❌ Cancel", data=f"tr_canc_{sender_id}_{target_user_id}")]]
    await event.reply(trade_text, parse_mode='html', buttons=buttons)

# ==========================================
# 🎰 CASINO GAMES
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/slot(?:@\w+)?(?:\s+(\d+))?$'))
async def slot_game_handler(event):
    user_id = event.sender_id
    bet_str = event.pattern_match.group(1)
    if not bet_str:
        return await event.reply(
            f"🎰 ငွေပမာဏထည့်ပါ။\nဥပမာ: <code>/slot 100</code>"
        )
    bet = int(bet_str)
    if bet <= 0:
        return await event.reply(" <b>အနည်းဆုံး 1 MMK လောင်းပါ။</b>", parse_mode='html')
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    balance = user_doc.get("wallet_balance", 0) if user_doc else 0
    if balance < bet:
        return await event.reply(f"<b>မင်းမှာ {balance} MMK ပဲရှိတယ်နော်! ⛓️</b>", parse_mode='html')
    await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": -bet}})
    mention = await get_html_mention(event, user_id)
    symbols = ["🧊", "🦠", "🏈", "👾", "🛩", "🛺", "🧸"]
    loading_msg = await event.reply("🎰 <code>🔄 🔄 🔄 🔄 🔄 🔄 🔄</code>", parse_mode='html')
    for spin in range(5):
        spin_result = random.choices(symbols, k=7)
        display = " | ".join(spin_result)
        await bot1.edit_message(
            event.chat_id,
            loading_msg,
            f"🎰 <code>{display}</code>",
            parse_mode='html'
        )
        await asyncio.sleep(max(0.4, 1.0 - spin * 0.4))
    res_syms = random.choices(symbols, k=7)
    res_str = " | ".join(res_syms)
    run_emoji = None
    run_count = 0
    current_emoji = res_syms[0]
    current_count = 1
    for i in range(1, len(res_syms)):
        if res_syms[i] == current_emoji:
            current_count += 1
        else:
            if current_count >= 2:
                run_emoji = current_emoji
                run_count = current_count
                break
            current_emoji = res_syms[i]
            current_count = 1
    if current_count >= 2 and run_emoji is None:
        run_emoji = current_emoji
        run_count = current_count
    if run_emoji is not None:
        partial_list = []
        matched_so_far = 0
        for sym in res_syms:
            if sym == run_emoji and matched_so_far < run_count:
                partial_list.append(sym)
                matched_so_far += 1
            else:
                partial_list.append("❓")
        partial_display = " | ".join(partial_list)
        await bot1.edit_message(
            event.chat_id,
            loading_msg,
            f"🎰 <code>{partial_display}</code>",
            parse_mode='html'
        )
        await asyncio.sleep(1)
    counts = Counter(res_syms)
    most_common_sym, max_count = counts.most_common(1)[0]
    win_amount = 0
    status_text = ""
    celebration = ""
    if max_count >= 7:
        win_amount = bet * 77 if most_common_sym == "🧊" else bet * 50
        status_text = f"🎉🎊 <b>ဂျက်ပေါက် ထိသွားပြီ!!!</b> 🎊🎉\n{most_common_sym} x{max_count} = {win_amount:,} MMK! 😱"
        celebration = "🎉✨🎊"
    elif max_count == 6:
        win_amount = bet * 25
        status_text = f"🔥🔥 <b>တစ်မျိုးတည်း ၆ လုံး ဆက်မိ!!!</b> 🔥🔥\n{most_common_sym} x{max_count} = {win_amount:,} MMK! 😍"
        celebration = "🔥✨🎉"
    elif max_count == 5:
        win_amount = bet * 12
        status_text = f"⚡⚡ <b>တစ်မျိုးတည်း ၅ လုံး ဆက်မိ!!</b> ⚡⚡\n{most_common_sym} x{max_count} = {win_amount:,} MMK! 🔥"
        celebration = "⚡✨🎉"
    elif max_count == 4:
        win_amount = bet * 5
        status_text = f"😲😲 <b>တစ်မျိုးတည်း ၄ လုံး ဆက်မိ!</b> 😲😲\n{most_common_sym} x{max_count} = {win_amount:,} MMK! 👍"
        celebration = "😲✨🎉"
    elif max_count == 3:
        win_amount = int(bet * 2.5)
        status_text = f"🤩 <b>တစ်မျိုးတည်း ၃ လုံး ဆက်မိ!</b> 🤩\n{most_common_sym} x{max_count} = {win_amount:,} MMK! 💪"
        celebration = "🤩✨"
    elif max_count == 2:
        win_amount = int(bet * 0.5)
        status_text = f"😊 <b>တစ်မျိုးတည်း ၂ လုံး ဆက်မိ!</b> 😊\n{most_common_sym} x{max_count} = {win_amount:,} MMK! 🎈"
        celebration = "😊✨"
    else:
        win_amount = 0
        status_text = f"😅 <b>ဒီတစ်ခါ မအောင်မြင်ဘူးနော်။</b>\nနောက်တစ်ခါ ကံကောင်းမယ်! ⛓️"
        celebration = "💔"
    if win_amount > 0:
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": win_amount}})
    net_gain = win_amount - bet
    profit_loss_symbol = "+" if net_gain > 0 else "" if net_gain == 0 else "-"
    display_net = f"{profit_loss_symbol}{abs(net_gain):,}" if net_gain != 0 else "0"
    final_text = (
        f"🎰 <b>ဒိုရေမွန် လောင်းကစား</b> 🎰\n"
        f"{celebration}\n"
        f"👤 <b>ကစားသမား:</b> {mention}\n"
        f"💵 <b>လောင်းငွေ:</b> <code>{bet:,} MMK</code>\n\n"
        f"🎯 <b>ထွက်လာတဲ့ ပုံစံ</b>\n"
        f"<code>{res_str}</code>\n\n"
        f"{status_text}\n"
        f"{celebration}\n"
        f"🔄 <b>ပြန်ရငွေ:</b> <code>{win_amount:,} MMK</code>\n"
        f"📊 <b>အမြတ်/အရှုံး:</b> <code>{display_net} MMK</code>\n"
        f"⛓️ <i>ဒိုရေမွန်က ကံပေးပါစေ။</i>"
        f"{GAME_FOOTER}"
    )
    await bot1.edit_message(event.chat_id, loading_msg, final_text, parse_mode='html')
    if not event.is_private:
        schedule_game_cleanup(bot1, event.chat_id, loading_msg)

@bot1.on(events.NewMessage(pattern=r'^/cardgame(?:@\w+)?\s+(\d+)$'))
async def create_cardgame_lobby(event):
    if event.is_private: return
    chat_id = event.chat_id
    bet = int(event.pattern_match.group(1))
    if bet < 100: return await event.reply("❌ <b>Minimum bet is 100 MMK.</b>", parse_mode='html')
    if chat_id in active_card_games: return await event.reply("⚠️ <b>A game is already running.</b>", parse_mode='html')
    host_id = event.sender_id
    user_doc = await users_catcher_col.find_one({"user_id": host_id})
    balance = user_doc.get("wallet_balance", 0) if user_doc else 0
    if balance < bet: return await event.reply(f"❌ <b>Insufficient balance! You have {balance} MMK</b>", parse_mode='html')
    await users_catcher_col.update_one({"user_id": host_id}, {"$inc": {"wallet_balance": -bet}})
    try:
        sender = await event.get_sender()
        fullname = f"{getattr(sender, 'first_name', '')} {getattr(sender, 'last_name', '')}".strip() or getattr(sender, 'username', '') or "Host"
    except: fullname = "Host"
    host_mention = f"<a href='tg://user?id={host_id}'><b>{escape_html(fullname)}</b></a>"
    active_card_games[chat_id] = {"host_id": host_id, "bet": bet, "players": {host_id: fullname}, "status": "lobby", "msg_id": None}
    lobby_text = f"🃏 <b>MULTIPLAYER CARD GAME</b>\n👑 <b>Host:</b> {host_mention}\n💵 <b>Bet:</b> <code>{bet} MMK</code>\n\n👥 <b>Players (1):</b>\n 1. {host_mention} (Host)\n\n📌 <i>At least 2 players. Host: <code>/startgame</code></i>"
    buttons = [[Button.inline("🃏 Join Game", data=f"cardjoin_{chat_id}")]]
    msg = await event.reply(lobby_text, parse_mode='html', buttons=buttons)
    active_card_games[chat_id]["msg_id"] = msg.id

@bot1.on(events.NewMessage(pattern=r'^/startgame(?:@\w+)?$'))
async def start_cardgame_handler(event):
    if event.is_private: return
    chat_id = event.chat_id
    if chat_id not in active_card_games: return await event.reply("❌ <b>No game lobby to start.</b>", parse_mode='html')
    game = active_card_games[chat_id]
    if game["status"] != "lobby": return
    if event.sender_id != game["host_id"]: return await event.reply("❌ <b>Only the host can start.</b>", parse_mode='html')
    if len(game["players"]) < 2: return await event.reply("👥 <b>Need at least 2 players.</b>", parse_mode='html')
    game["status"] = "playing"
    pool = game["bet"] * len(game["players"])
    results = {}
    max_score = -1
    for pid, name in game["players"].items():
        card_score = random.randint(1, 10)
        results[pid] = {"name": name, "score": card_score}
        if card_score > max_score: max_score = card_score
    winners = [pid for pid, data in results.items() if data["score"] == max_score]
    split_prize = pool // len(winners)
    for w_id in winners: await users_catcher_col.update_one({"user_id": w_id}, {"$inc": {"wallet_balance": split_prize}})
    reveal_msg = await event.reply(f"🃏 <b>Dealing cards...</b>\n💰 <b>Prize Pool:</b> <code>{pool} MMK</code>", parse_mode='html')
    dealt_text = f"🃏 <b>GAME RESULTS</b>\n💰 <b>Prize Pool:</b> <code>{pool} MMK</code>\n\n"
    for pid, data in results.items():
        p_mention = f"<a href='tg://user?id={pid}'><b>{escape_html(data['name'])}</b></a>"
        dealt_text += f"🎴 {p_mention} drew: <b>[??]</b>\n"
        await asyncio.sleep(0.5)
        try:
            await bot1.edit_message(event.chat_id, reveal_msg, dealt_text, parse_mode='html')
        except Exception:
            pass
    result_text = f"🃏 <b>GAME RESULTS</b>\n💰 <b>Prize Pool:</b> <code>{pool} MMK</code>\n\n"
    for pid, data in results.items():
        p_mention = f"<a href='tg://user?id={pid}'><b>{escape_html(data['name'])}</b></a>"
        win_tag = " 🏆 (WINNER)" if pid in winners else ""
        result_text += f"🃏 {p_mention} drew card: <b>[{data['score']}/10]</b>{win_tag}\n"
    if len(winners) > 1: result_text += f"🤝 <b>Tie! Each winner gets <code>{split_prize} MMK</code></b>"
    else:
        winner_mention = f"<a href='tg://user?id={winners[0]}'><b>{escape_html(results[winners[0]]['name'])}</b></a>"
        result_text += f"🎉 <b>{winner_mention} wins <code>{pool} MMK</code>!</b>"
    result_text += GAME_FOOTER
    del active_card_games[chat_id]
    await bot1.edit_message(event.chat_id, reveal_msg, result_text, parse_mode='html')
    if not event.is_private:
        schedule_game_cleanup(bot1, event.chat_id, reveal_msg)

@bot1.on(events.NewMessage(pattern=r'^/cancelgame(?:@\w+)?$'))
async def cancel_cardgame_handler(event):
    if event.is_private: return
    chat_id = event.chat_id
    if chat_id not in active_card_games: return await event.reply("❌ <b>No game to cancel.</b>", parse_mode='html')
    game = active_card_games[chat_id]
    if event.sender_id != game["host_id"] and event.sender_id != OWNER_ID: return await event.reply("❌ <b>Only the host can cancel.</b>", parse_mode='html')
    for pid in game["players"].keys(): await users_catcher_col.update_one({"user_id": pid}, {"$inc": {"wallet_balance": game["bet"]}})
    bet_amount = game["bet"]
    del active_card_games[chat_id]
    await event.reply(f"🎯 <b>GAME CANCELLED</b>\n<blockquote>Refunded <code>{bet_amount} MMK</code> to all players.</blockquote>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/flip(?:@\w+)?\s+(ခေါင်း|ပန်း)\s+(\d+)$'))
async def coin_flip_handler(event):
    user_id = event.sender_id
    choice = event.pattern_match.group(1)
    bet_amount = int(event.pattern_match.group(2))
    user_data = await users_catcher_col.find_one({"user_id": user_id})
    if not user_data or user_data.get("wallet_balance", 0) < bet_amount: return await event.reply("❌ <b>Insufficient balance!</b>", parse_mode='html')
    result = random.choice(["ခေါင်း", "ပန်း"])
    mention = await get_html_mention(event, user_id)
    spin_msg = await event.reply("🪙 <b>Flipping...</b>", parse_mode='html')
    for frame in ["🪙 ↻", "🪙 ↺", "🪙 ↻"]:
        await asyncio.sleep(0.4)
        try:
            await bot1.edit_message(event.chat_id, spin_msg, f"<b>{frame}</b>", parse_mode='html')
        except Exception:
            pass
    if choice == result:
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": bet_amount}})
        caption_text = f"🪙 {mention} <b>YOU WON!</b>\n✨ Result: [ <b>{result}</b> ]\n🎉 You guessed right! <code>+{bet_amount} MMK</code>"
    else:
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": -bet_amount}})
        caption_text = f"🪙 {mention} <b>YOU LOST.</b>\n💨 Result: [ <b>{result}</b> ]\n💸 You guessed wrong. <code>-{bet_amount} MMK</code>"
    caption_text += GAME_FOOTER
    await bot1.edit_message(event.chat_id, spin_msg, caption_text, parse_mode='html')
    if not event.is_private:
        schedule_game_cleanup(bot1, event.chat_id, spin_msg)

@bot1.on(events.NewMessage(pattern=r'^/dice(?:@\w+)?\s+(\d+)$'))
async def dice_game_handler(event):
    user_id = event.sender_id
    bet_amount = int(event.pattern_match.group(1))
    chat_id = event.chat_id
    user_data = await users_catcher_col.find_one({"user_id": user_id})
    if not user_data or user_data.get("wallet_balance", 0) < bet_amount: return await event.reply("❌ <b>Insufficient balance!</b>", parse_mode='html')
    dice_msg = await bot1.send_message(chat_id, file=types.InputMediaDice(emoticon="🎲"))
    await asyncio.sleep(2)
    dice_value = dice_msg.media.value
    mention = await get_html_mention(event, user_id)
    if dice_value >= 4:
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": bet_amount}})
        result_msg = await event.reply(f"🎉 {mention} <b>YOU WIN!</b> Dice: {dice_value}\n🪙 Added: <code>+{bet_amount} MMK</code>{GAME_FOOTER}", parse_mode='html')
    else:
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": -bet_amount}})
        result_msg = await event.reply(f"💸 {mention} <b>YOU LOSE!</b> Dice: {dice_value}\n🪙 Deducted: <code>-{bet_amount} MMK</code>{GAME_FOOTER}", parse_mode='html')
    if not event.is_private:
        schedule_game_cleanup(bot1, event.chat_id, result_msg)

@bot1.on(events.NewMessage(pattern=r'^/hilo(?:@\w+)?\s+(\d+)$'))
async def hilo_game_handler(event):
    user_id = event.sender_id
    bet_amount = int(event.pattern_match.group(1))
    if bet_amount <= 0: return await event.reply("❌ <b>Bet must be at least 1 MMK.</b>", parse_mode='html')
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    balance = user_doc.get("wallet_balance", 0) if user_doc else 0
    if balance < bet_amount: return await event.reply(f"❌ <b>Insufficient balance! You have {balance:,} MMK</b>", parse_mode='html')
    base_card = random.randint(2, 12)
    card_map = {11: "J", 12: "Q", 13: "K"}
    base_display = card_map.get(base_card, str(base_card))
    buttons = [[Button.inline("🔼 Higher", data=f"hilo_HIGH_{base_card}_{bet_amount}_{user_id}"), Button.inline("🔽 Lower", data=f"hilo_LOW_{base_card}_{bet_amount}_{user_id}")]]
    await event.reply(f"🃏 <b>HI-LO GAME</b>\n⛓️ MAKIMA’S CASINO\n💵 Bet: <code>{bet_amount:,} MMK</code>\n🎴 Current card: ✨ <b>[ {base_display} ]</b> ✨\n\n<blockquote>Higher or lower? Choose!</blockquote>", buttons=buttons, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/daily(?:@\w+)?$'))
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
    base_bonus = random.randint(2000, 5000)
    streak_bonus = min(new_streak * 200, 3000)
    bonus = base_bonus + streak_bonus
    await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": bonus}, "$set": {"last_daily": now, "daily_streak": new_streak, "fullname": plain_name}})
    newly_earned = await check_and_award_achievements(user_id)
    reply_text = f"🎁 {mention} <b>Daily bonus claimed! <code>+{bonus} MMK</code></b>\n🔥 <b>{new_streak} day streak</b> — Streak Bonus: <code>+{streak_bonus} MMK</code>"
    reply_text += format_achievement_unlocks(newly_earned)
    await event.reply(reply_text, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/richest(?:@\w+)?$'))
async def system_richest_leaderboard(event):
    cursor = users_catcher_col.find({"wallet_balance": {"$gt": 0}}).sort("wallet_balance", -1).limit(10)
    richest_users = await cursor.to_list(length=10)
    if not richest_users: return await event.reply(f"🏆 <b>No wealthy players yet!</b>", parse_mode='html')
    msg = f"💰 <b>TOP 10 RICHEST PLAYERS</b>\n⛓️ MAKIMA’S ECONOMY\n"
    for i, u in enumerate(richest_users):
        bal = u.get("wallet_balance", 0)
        user_display = escape_html(clean_display_name(u.get('fullname'), fallback=f"User {u['user_id']}"))
        msg += f"<b>{i+1}.</b> {user_display} — <code>{bal:,} MMK</code> 🪙\n"
    await event.reply(msg, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/scrap(?:@\w+)?\s+([a-zA-Z0-9_]+)$'))
async def scrap_card_handler(event):
    user_id = event.sender_id
    char_id = normalize_char_id_input(event.pattern_match.group(1))
    mention = await get_html_mention(event, user_id)
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    user_harem = user_doc.get("harem", []) if user_doc else []
    char_item = next((x for x in user_harem if isinstance(x, dict) and x.get("char_id") == char_id and x.get("status") != "market"), None)
    if not char_item: return await event.reply("❌ <b>You don’t have this card.</b>", parse_mode='html')
    char_data = await characters_base_col.find_one({"char_id": char_id})
    if not char_data: return await event.reply("❌ <b>Character not found.</b>", parse_mode='html')
    melt_value = int(char_data.get("currency_value", 100) * 0.5)
    user_harem.remove(char_item)
    await users_catcher_col.update_one({"user_id": user_id}, {"$set": {"harem": user_harem}, "$inc": {"wallet_balance": melt_value}})
    await clear_stale_favorite(user_id, char_id, user_harem)
    await event.reply(f"♻️ {mention} melted <code>{char_data['name']}</code> and got <code>+{melt_value} MMK</code>.", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/gamble(?:@\w+)?\s+(\d+)$'))
async def high_risk_gamble_handler(event):
    user_id = event.sender_id
    bet = int(event.pattern_match.group(1))
    mention = await get_html_mention(event, user_id)
    if bet < 50: return await event.reply("❌ <b>Minimum bet is 50 MMK.</b>", parse_mode='html')
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    balance = user_doc.get("wallet_balance", 0) if user_doc else 0
    if balance < bet: return await event.reply(f"❌ <b>Insufficient balance! You have {balance} MMK</b>", parse_mode='html')
    suspense_msg = await event.reply("🎲 <b>Rolling the dice of fate...</b>", parse_mode='html')
    for frame in ["🎲 ⚀", "🎲 ⚄", "🎲 ⚁"]:
        await asyncio.sleep(0.4)
        try:
            await bot1.edit_message(event.chat_id, suspense_msg, f"<b>{frame}</b>", parse_mode='html')
        except Exception:
            pass
    if random.choice([True, False]):
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": bet}})
        response = f"🔥 {mention} <b>RISK WON!</b> You doubled your bet! (+<code>{bet} MMK</code>)"
    else:
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": -bet}})
        response = f"💸 {mention} <b>RISK LOST.</b> You lost your bet. (-<code>{bet} MMK</code>)"
    response += GAME_FOOTER
    await bot1.edit_message(event.chat_id, suspense_msg, response, parse_mode='html')
    if not event.is_private:
        schedule_game_cleanup(bot1, event.chat_id, suspense_msg)


@bot1.on(events.NewMessage(pattern=r'^/hunt(?:@\w+)?$'))
async def text_adventure_hunt_handler(event):
    user_id = event.sender_id
    mention = await get_html_mention(event, user_id)
    plain_name = await get_plain_name(event, user_id)
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    current_time = time.time()
    last_hunt = user_doc.get("hunt_cooldown", 0) if user_doc else 0
    if current_time - last_hunt < 180:
        return await event.reply(f"⏳ {mention} <b>You must wait {int(180 - (current_time - last_hunt))}s.</b>", parse_mode='html')
    earned = random.randint(200, 8000)
    events_pool = [f"🌲 {mention} <b>found treasure! (+<code>{earned} MMK</code>)</b>", f"⚔️ {mention} <b>defeated a rival! (+<code>{earned} MMK</code>)</b>", f"🌌 {mention} <b>collected quantum points! (+<code>{earned} MMK</code>)</b>"]
    await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": earned}, "$set": {"hunt_cooldown": current_time, "fullname": plain_name}}, upsert=True)
    await event.reply(random.choice(events_pool), parse_mode='html')

MARKET_LISTINGS_PER_PAGE = 5

async def send_market_page(client, chat_id, page=1, edit_msg_id=None):
    all_listings = await marketplace_col.find().sort("timestamp", -1).to_list(length=None)
    if not all_listings:
        msg = "🏪 <b>No items on the market.</b>"
        if edit_msg_id:
            try:
                await client.edit_message(chat_id, edit_msg_id, msg, parse_mode='html')
            except errors.MessageNotModifiedError:
                pass
        else:
            await client.send_message(chat_id, msg, parse_mode='html')
        return
    total_pages = max(1, (len(all_listings) + MARKET_LISTINGS_PER_PAGE - 1) // MARKET_LISTINGS_PER_PAGE)
    if page < 1: page = 1
    if page > total_pages: page = total_pages
    start_idx = (page - 1) * MARKET_LISTINGS_PER_PAGE
    page_listings = all_listings[start_idx:start_idx + MARKET_LISTINGS_PER_PAGE]
    msg = (
        f"🏪 <b>MARKETPLACE CATALOG</b>\n⛓️ MAKIMA’S AUCTION\n"
        f"📑 <b>Page:</b> <code>{page}/{total_pages}</code> | <b>Total Listings:</b> <code>{len(all_listings)}</code>\n\n"
    )
    for item in page_listings:
        msg += f"📦 <b>Card:</b> <code>{item['char_name']}</code> [<code>{display_char_id(item['char_id'])}</code>]\n ├─ 💰 <b>Price:</b> <code>{item['price']} MMK</code>\n └─ 👤 <b>Seller:</b> {item['seller_name']}\n\n"
    buttons = []
    nav_buttons = []
    if page > 1:
        nav_buttons.append(Button.inline("⬅️ Prev", data=f"mktpg_{page-1}"))
    if page < total_pages:
        nav_buttons.append(Button.inline("Next ➡️", data=f"mktpg_{page+1}"))
    if nav_buttons:
        buttons.append(nav_buttons)
    if not buttons:
        buttons = None
    if edit_msg_id:
        try:
            await client.edit_message(chat_id, edit_msg_id, msg, parse_mode='html', buttons=buttons)
        except errors.MessageNotModifiedError:
            pass
    else:
        await client.send_message(chat_id, msg, parse_mode='html', buttons=buttons)

@bot1.on(events.NewMessage(pattern=r'^/market(?:@\w+)?$'))
async def global_market_catalog_viewer(event):
    await send_market_page(bot1, event.chat_id, page=1)

@bot1.on(events.NewMessage(pattern=r'^/stats(?:@\w+)?$'))
async def system_inflation_stats(event):
    status_msg = await event.reply("⏳ <b>Compiling statistics...</b>", parse_mode='html')
    try:
        pipeline_cash = [
            {"$group": {
                "_id": None,
                "total_cash": {"$sum": "$wallet_balance"},
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
        total_catches = 0
        caught_unique_ids = set()
        for u in all_users:
            harem = u.get("harem", [])
            total_catches += len(harem)
            for item in harem:
                if isinstance(item, dict) and "char_id" in item:
                    caught_unique_ids.add(item["char_id"])
                    tier = classify_rarity(item.get("rarity", ""))
                    if tier in catch_tier_counts:
                        catch_tier_counts[tier] += 1
        discovery_count = len(caught_unique_ids)
        msg = (
            f"📊 <b>GLOBAL ECONOMY & COLLECTION STATS</b>\n"
            f"⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n\n"
            f"👥 <b>Active Agents:</b> <code>{metrics['players_count']} Players</code>\n"
            f"🪙 <b>Total Money in Circulation:</b> <code>{metrics['total_cash']:,} MMK</code>\n\n"
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
        msg += f"\n🔎 <b>DISCOVERY RATE</b> (unique characters caught at least once)\n"
        msg += f"<code>{build_progress_bar(discovery_count, base_total)}</code>\n"
        msg += f"<code>{discovery_count}/{base_total}</code> characters discovered by players"
        await status_msg.edit(msg, parse_mode='html')
    except Exception as e:
        error_text = f"❌ <b>Stats Error:</b>\n<code>{escape_html(str(e))}</code>"
        await status_msg.edit(error_text, parse_mode='html')
        await report_system_error("system_inflation_stats", e)

@bot1.on(events.NewMessage(pattern=r'^/introduce(?:@\w+)?$'))
async def introduce_bot_handler(event):
    intro_text = (
        "<pre style='background-color:#2d2d2d; color:#d4d4d4; padding:15px; border-radius:8px; font-family: monospace;'>"
        "ဟိုက် MAKIMA BOT  –  THE ONE WHO CONTROLS EVERYTHING HERE\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "    I am a multi‑functional Telegram bot carrying Makima's quiet authority.\n"
        "    I combine fun, economy, and powerful group management — all under my watch.\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📌  WHAT I CAN DO FOR YOU (complete list)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "🛡️  ADMIN & MODERATION TOOLS\n"
        "    • /mute, /unmute  –  silence or restore voice\n"
        "    • /ban, /unban    –  exile or reinstate a member\n"
        "    • /kick           –  remove a user from the chat\n"
        "    • /adm            –  tag all admins with a custom message\n"
        "    • /id, /info      –  get user/chat IDs and detailed info\n"
        "    • Auto‑spam detection for stickers & short messages\n"
        "\n"
        "🎮  CATCHING GAME (CHARACTER COLLECTION)\n"
        "    • /who (/w, /waifu) –  reveal the spawned character\n"
        "    • /catch [name]   –  capture the character and earn MMK\n"
        "    • /harem          –  view your vault (paginated inventory)\n"
        "    • /fav [ID]       –  set a favourite card\n"
        "    • /profile, /myinfo –  check your stats and balance\n"
        "    • /top /gtop      –  local and global leaderboards\n"
        "    • /check [ID]     –  detailed character info & top collectors\n"
        "\n"
        "🎰  CASINO & GAMBLING\n"
        "    • /slot [amount]       –  spin the 7‑symbol slot machine\n"
        "    • /cardgame [amount]   –  create a multiplayer card game lobby\n"
        "    • /startgame /cancelgame  –  start or cancel the lobby\n"
        "    • /flip [heads/tails] [amount]  –  coin flip (50/50)\n"
        "    • /dice [amount]       –  roll a dice, win on 4‑6\n"
        "    • /hilo [amount]       –  higher/lower card game\n"
        "    • /gamble [amount]     –  double or nothing (50% chance)\n"
        "\n"
        "💰  ECONOMY & TRADING\n"
        "    • /balance        –  view your wallet balance\n"
        "    • /daily           –  claim daily bonus (streak rewards)\n"
        "    • /hunt            –  go hunting for extra cash (3min cooldown)\n"
        "    • /pay [amount]    –  send money to another user (reply)\n"
        "    • /gift [cardID]   –  gift a card to someone (reply)\n"
        "    • /trade [myID] [theirID]  –  propose a card swap (reply)\n"
        "    • /sell [ID] [price]  –  list your card on the marketplace\n"
        "    • /buy [ID]        –  view and buy the cheapest listing\n"
        "    • /market          –  browse all active listings\n"
        "    • /scrap [ID]      –  sell a card back to the system (50% value)\n"
        "    • /richest         –  top 10 wealthiest players\n"
        "    • /stats           –  global economy overview\n"
        "\n"
        "🔗  SOCIAL & REFERRAL\n"
        "    • /referral        –  get your unique invite link\n"
        "                         (you get 2000 MMK, friend gets 1000 MMK)\n"
        "\n"
        "🌦️  WEATHER\n"
        "    • /weather         –  live weather for Myanmar & Thailand\n"
        "                         (choose country, then city)\n"
        "\n"
        "🧠  SMART AUTO‑REPLY\n"
        "    • I can remember custom answers (admins can add filters)\n"
        "    • I reply intelligently based on keywords you teach me\n"
        "    • /ktr /ktrr       –  toggle smart reply engine (owner only)\n"
        "\n"
        "🎵  MUSIC PLAYER\n"
        "    • /play [title]    –  build a personal playlist (up to 30 songs)\n"
        "                         (just for fun – it stores your requests)\n"
        "\n"
        "📚  HELP & NAVIGATION\n"
        "    • /help            –  detailed command reference\n"
        "    • /game            –  casino games overview\n"
        "    • /allcmd          –  quick list of all commands\n"
        "    • /introduce       –  you are reading this!\n"
        "\n"
        "⚙️  OWNER‑ONLY (hidden from normal users)\n"
        "    • /addchar, /delchar, /editchar, /setchar, /addartist,\n"
        "      /postallchars confirm  –  backfill-post all characters to the channel,\n"
        "      /changeallrarity confirm  –  rebrand all rarity names/emoji DB-wide,\n"
        "      /haiclean, /take, /give, /giveall,\n"
        "      /takeall, /fspawn, /haitime, /resetstats, /mau, /stealth, etc.\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡  Everything is designed to be fun, fair, and easy to follow.\n"
        "    I speak calmly, but I expect to be listened to. ⛓️\n"
        "    For any help, type /help or ask me directly.\n"
        "\n"
        "📢  Join our community: https://t.me/Comeback_BoD\n"
        "⛓️  You're exactly where I want you to be.\n"
        "</pre>"
    )
    await event.reply(intro_text, parse_mode='html')

# ==========================================
# 🗑️ HAITIME / RESETSTATS / GIFTALL / STEALTH
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/giftall(?:@\w+)?\s+(\d+)$'))
async def owner_giftall_broadcast(event):
    if event.sender_id != OWNER_ID: return
    amount = int(event.pattern_match.group(1))
    try:
        result = await users_catcher_col.update_many({}, {"$inc": {"wallet_balance": amount}})
        await event.reply(f"🎉 <b>GIVEAWAY</b>\n⛓️ Makima gave <code>{amount:,} MMK</code> to <b>{result.modified_count}</b> players.", parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ Error: <code>{e}</code>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/resetstats(?:@\w+)?$'))
async def reset_group_counters_by_owner(event):
    if event.sender_id != OWNER_ID: return
    try:
        await groups_counters_col.update_many({}, {"$set": {"counter": 0}})
        await event.reply(f"⚙️ <b>All counters reset to 0.</b>", parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ Error: <code>{e}</code>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/haitime(?:@\w+)?$'))
async def show_spawn_target_status(event):
    if event.sender_id != OWNER_ID: return
    global_config = await groups_config_col.find_one({"chat_id": GLOBAL_SPAWN_CHAT_KEY})
    global_target = global_config.get("spawn_target", 50) if global_config else 50
    overrides = await groups_config_col.find(
        {"chat_id": {"$ne": GLOBAL_SPAWN_CHAT_KEY}, "spawn_target": {"$exists": True}}
    ).to_list(length=None)
    text = (
        f"⛓️ <b>SPAWN THRESHOLD STATUS</b>\n"
        f"🌐 <b>Global (default for all groups):</b> <code>{global_target}</code> messages\n"
    )
    if overrides:
        text += f"\n📌 <b>{len(overrides)} group(s) have their own override</b> (global changes won't affect these):\n"
        for doc in overrides[:20]:
            text += f"  • <code>{doc['chat_id']}</code> → <code>{doc.get('spawn_target')}</code>\n"
        if len(overrides) > 20:
            text += f"  … and {len(overrides) - 20} more\n"
    else:
        text += "\n📌 No group has its own override — every group follows the global value above."
    text += (
        f"\n\n📖 <b>Usage:</b>\n"
        f"<code>/haitime [count]</code> — set the global default\n"
        f"<code>/haitime [chat_id] [count]</code> — override one specific group"
    )
    await event.reply(text, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/haitime(?:@\w+)?\s+(-?\d+)(?:\s+(-?\d+))?$'))
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
    # Verify the write actually persisted before confirming to the owner
    confirm_doc = await groups_config_col.find_one({"chat_id": target_chat_id})
    confirmed_value = confirm_doc.get("spawn_target") if confirm_doc else None
    if confirmed_value != new_target:
        return await event.reply(
            f"❌ <b>Makima:</b> The update didn't save correctly. Please try again.\n"
            f"<code>Expected {new_target}, found {confirmed_value}</code>",
            parse_mode='html'
        )
    extra_note = ""
    if target_chat_id == GLOBAL_SPAWN_CHAT_KEY:
        override_count = await groups_config_col.count_documents(
            {"chat_id": {"$ne": GLOBAL_SPAWN_CHAT_KEY}, "spawn_target": {"$exists": True}}
        )
        if override_count:
            extra_note = f"\n⚠️ <i>{override_count} group(s) have their own override and won't follow this global value until cleared.</i>"
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
        except Exception as e:
            logging.error(f"Cleaner Error: {e}")
        await asyncio.sleep(300)

async def stock_market_tick_engine():
    """Every STOCK_PRICE_TICK_SECONDS, applies a small rarity-weighted random walk to
    every character that currently has a stock listing, so prices keep drifting even
    when nobody is actively trading — a live, breathing market."""
    while True:
        await asyncio.sleep(STOCK_PRICE_TICK_SECONDS)
        try:
            today_str = datetime.now(TZ).strftime("%Y-%m-%d")
            async for stock in character_stocks_col.find({}):
                try:
                    char_doc = await characters_base_col.find_one({"char_id": stock["char_id"]})
                    tier = _stock_tier_for_char(char_doc)
                    vol = STOCK_TIER_VOLATILITY.get(tier, 0.02)
                    drift = random.uniform(-vol, vol)
                    new_price = stock["price"] * (1 + drift)
                    new_price = max(new_price, _stock_price_floor(char_doc, stock))
                    is_new_day = stock.get("day_open_date") != today_str
                    extra = {"day_open_price": stock["price"], "day_open_date": today_str,
                              "buy_volume_today": 0.0, "sell_volume_today": 0.0} if is_new_day else None
                    await _write_stock_price(stock["char_id"], stock, new_price, extra)
                except Exception as inner_e:
                    logging.error(f"Stock tick error for {stock.get('char_id')}: {inner_e}")
        except Exception as e:
            logging.error(f"Stock market tick engine error: {e}")

async def btc_market_tick_engine():
    """Every BTC_TICK_SECONDS: a random walk plus a small pull from the character stock
    market's current mood, so the two economies visibly move together over time."""
    while True:
        await asyncio.sleep(BTC_TICK_SECONDS)
        try:
            market = await get_or_create_btc_market()
            sentiment = await _compute_stock_market_sentiment()
            drift = random.uniform(-BTC_VOLATILITY, BTC_VOLATILITY) + sentiment * BTC_SENTIMENT_WEIGHT
            new_price = market["price"] * (1 + drift)
            today_str = datetime.now(TZ).strftime("%Y-%m-%d")
            is_new_day = market.get("day_open_date") != today_str
            extra = {"day_open_price": market["price"], "day_open_date": today_str,
                      "buy_volume_today": 0.0, "sell_volume_today": 0.0} if is_new_day else None
            await _write_btc_price(market, new_price, extra)
        except Exception as e:
            logging.error(f"BTC market tick engine error: {e}")

# ==========================================
# 👥 /users – LIST ALL USERS (LIVE NAMES)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/users(?:@\w+)?$'))
async def list_users_command(event):
    if event.sender_id != OWNER_ID:
        return await event.reply("❌ Owner only.")
    if not event.is_private:
        return await event.reply("❌ Please use in DM.")
    users = await users_catcher_col.find({}, {"user_id": 1}).to_list(length=None)
    if not users:
        return await event.reply("📭 No users found.")
    user_ids = sorted([u['user_id'] for u in users])
    per_page = 30
    total_pages = (len(user_ids) + per_page - 1) // per_page
    await send_users_page(event, user_ids, 1, per_page, total_pages)

async def send_users_page(event, user_ids, page, per_page, total_pages, edit_msg_id=None):
    start = (page - 1) * per_page
    end = min(start + per_page, len(user_ids))
    page_ids = user_ids[start:end]
    entities = []
    for uid in page_ids:
        try:
            ent = await bot1.get_entity(uid)
            entities.append(ent)
        except Exception:
            entities.append(None)
        await asyncio.sleep(0.1)
    msg = f"👥 <b>ALL USERS (Total: {len(user_ids)})</b>\n"
    msg += f"📑 Page {page}/{total_pages}\n\n"
    for idx, (uid, ent) in enumerate(zip(page_ids, entities), start=start+1):
        if ent:
            first = getattr(ent, 'first_name', '') or ''
            last = getattr(ent, 'last_name', '') or ''
            fullname = f"{first} {last}".strip()
            if not fullname:
                fullname = getattr(ent, 'username', '') or f"User {uid}"
        else:
            fullname = f"User {uid}"
        if len(fullname) > 25:
            fullname = fullname[:22] + "..."
        mention = f"<a href='tg://user?id={uid}'><b>{escape_html(fullname)}</b></a>"
        msg += f"• {mention} — <code>{uid}</code>\n"
    buttons = []
    nav = []
    if page > 1:
        nav.append(Button.inline("⬅️ Prev", data=f"users_{page-1}"))
    if page < total_pages:
        nav.append(Button.inline("Next ➡️", data=f"users_{page+1}"))
    if nav:
        buttons.append(nav)
    if edit_msg_id:
        try:
            await bot1.edit_message(event.chat_id, edit_msg_id, msg, parse_mode='html', buttons=buttons)
        except:
            pass
    else:
        await event.reply(msg, parse_mode='html', buttons=buttons)

@bot1.on(events.CallbackQuery(pattern=r'^users_(\d+)$'))
async def users_page_callback(event):
    if event.sender_id != OWNER_ID:
        return await event.answer("❌ Owner only.", alert=True)
    page = int(event.pattern_match.group(1))
    users = await users_catcher_col.find({}, {"user_id": 1}).to_list(length=None)
    if not users:
        return await event.edit("📭 No users found.")
    user_ids = sorted([u['user_id'] for u in users])
    per_page = 30
    total_pages = (len(user_ids) + per_page - 1) // per_page
    if page < 1: page = 1
    if page > total_pages: page = total_pages
    await send_users_page(event, user_ids, page, per_page, total_pages, edit_msg_id=event.message_id)

# 📢 /hey – BROADCAST TO ALL DM USERS (LIVE NAMES)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/hey(?:@\w+)?$'))
async def owner_broadcast_hey(event):
    if event.sender_id != OWNER_ID:
        return await event.reply("❌ Owner အတွက်သာ သုံးလို့ရပါတယ်။")
    if not event.is_private:
        return await event.reply("❌ ဒီ Command ကို DM မှာပဲ သုံးပါ။")
    if not event.is_reply:
        return await event.reply("❌ ပို့ချင်တဲ့ Message ကို Reply လုပ်ပြီး <code>/hey</code> ရိုက်ပါ။", parse_mode='html')
    reply_msg = await event.get_reply_message()
    broadcast_text = reply_msg.text
    if not broadcast_text:
        return await event.reply("❌ Reply လုပ်ထားတဲ့ Message မှာ စာသားမပါဘူး။")
    status_msg = await event.reply("⏳ <b>Broadcast စတင်နေပါပြီ...</b>", parse_mode='html')
    users = await users_catcher_col.find({}, {"user_id": 1}).to_list(length=None)
    if not users:
        return await status_msg.edit("📭 Database ထဲမှာ သုံးစွဲသူမရှိသေးပါဘူး။")
    success_ids = []
    fail_ids = []
    success_count = 0
    fail_count = 0
    for u in users:
        uid = u['user_id']
        try:
            await send_safe_message(bot1, uid, broadcast_text)
            success_count += 1
            success_ids.append(uid)
            await asyncio.sleep(0.05)
        except Exception:
            fail_count += 1
            fail_ids.append(uid)
    summary = f"📢 <b>BROADCAST အပြီးသတ်ပါပြီ</b>\n"
    summary += f"━━━━━━━━━━━━━━━━━━━━\n"
    summary += f"✅ <b>အောင်မြင်သူ:</b> <code>{success_count}</code> ယောက်\n"
    summary += f"❌ <b>မအောင်မြင်သူ:</b> <code>{fail_count}</code> ယောက်\n"
    if success_ids:
        summary += f"\n👤 <b>အောင်မြင်စွာ ပို့ပြီးသူများ (ပထမ ၃၀ ယောက်):</b>\n"
        display_ids = success_ids[:30]
        for uid in display_ids:
            try:
                ent = await bot1.get_entity(uid)
                first = getattr(ent, 'first_name', '') or ''
                last = getattr(ent, 'last_name', '') or ''
                fullname = f"{first} {last}".strip()
                if not fullname:
                    fullname = getattr(ent, 'username', '') or f"User {uid}"
            except Exception:
                fullname = f"User {uid}"
            summary += f"• <a href='tg://user?id={uid}'><b>{escape_html(fullname)}</b></a> — <code>{uid}</code>\n"
        if len(success_ids) > 30:
            summary += f"\n... နှင့် ကျန် <code>{len(success_ids)-30}</code> ယောက် ကျန်သေးပါတယ်။"
    if success_ids:
        import io
        file_content = "✅ SUCCESSFUL USERS (Broadcast)\n"
        file_content += "="*40 + "\n\n"
        for uid in success_ids:
            try:
                ent = await bot1.get_entity(uid)
                first = getattr(ent, 'first_name', '') or ''
                last = getattr(ent, 'last_name', '') or ''
                fullname = f"{first} {last}".strip()
                if not fullname:
                    fullname = getattr(ent, 'username', '') or f"User {uid}"
            except Exception:
                fullname = f"User {uid}"
            file_content += f"{fullname} (ID: {uid})\n"
            await asyncio.sleep(0.2)
        file_bytes = io.BytesIO(file_content.encode('utf-8'))
        file_bytes.name = "broadcast_success.txt"
        await bot1.send_file(
            event.chat_id,
            file_bytes,
            caption="📄 အပြည့်အစုံ အောင်မြင်သူစာရင်း (Live Names)",
            reply_to=status_msg.id
        )
    await status_msg.edit(summary, parse_mode='html')

# ==========================================
# 🚨 STEALTH CONTROLLER
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/stealth(?:@\w+)?(?:\s+(on|off))?$'))
async def toggle_stealth(event):
    global STEALTH_MAU_MODE
    if event.sender_id != OWNER_ID: return
    args = event.pattern_match.group(1)
    if not args:
        status = "🟢 ACTIVE" if STEALTH_MAU_MODE else "🔴 INACTIVE"
        return await event.reply(f"⛓️ <b>Stealth MAU Status:</b> {status}\n📌 <code>/stealth on</code> or <code>/stealth off</code>", parse_mode='html')
    if args.lower() == "on":
        STEALTH_MAU_MODE = True
        await event.reply("🟢 <b>Stealth MAU Engine: ACTIVATED!</b>", parse_mode='html')
    elif args.lower() == "off":
        STEALTH_MAU_MODE = False
        await event.reply("🔴 <b>Stealth MAU Engine: DEACTIVATED!</b>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'(?i)^(play|/|.|p|harem|h|vault|v|waifu|w|daily|claim)$'))
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

@bot1.on(events.NewMessage(pattern=r"(?i)^/weather$"))
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
# 🚀 SYSTEM INITIALIZATION
# ==========================================
async def start_system():
    threading.Thread(target=run_flask, daemon=True).start()
    print("⛓️ Makima Bot System Starting...")
    try:
        await create_indexes()
    except Exception as e:
        print(f"Index creation error: {e}")
    while True:
        try:
            print("🚀 Connecting Telethon...")
            await bot1.start(bot_token=MAIN_BOT_TOKEN)
            me_main = await bot1.get_me()
            print(f"✅ Bot connected as @{me_main.username}")
            if me_main.id not in bot_ids: bot_ids.append(me_main.id)
            asyncio.create_task(ghost_spawn_cleaner())
            asyncio.create_task(stock_market_tick_engine())
            asyncio.create_task(btc_market_tick_engine())
            print("📅 Background tasks started.")
            await bot1.run_until_disconnected()
        except Exception as system_fault:
            print(f"⚠️ Bot disconnected: {system_fault}")
            print("⏳ Restarting in 30 seconds...")
            await asyncio.sleep(30)

if __name__ == "__main__":
    try:
        asyncio.run(start_system())
    except (KeyboardInterrupt, SystemExit):
        print("Makima Bot System Shutting Down.")
        
