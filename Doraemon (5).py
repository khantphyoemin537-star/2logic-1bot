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
from collections import Counter
from telethon.tl.functions.channels import GetParticipantsRequest
from datetime import datetime, timedelta
from telethon.tl.functions.messages import ExportChatInviteRequest
from flask import Flask
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument
from html import escape as escape_html
from telethon.errors import FloodWaitError
from telethon import TelegramClient, events, functions, types, Button, errors
from collections import defaultdict
from telethon.tl.types import ChannelParticipantsAdmins
from telethon.extensions import html
from telethon.tl.types import MessageEntityCustomEmoji

# ==========================================
# ⚡ PREMIUM MATHEMATICAL BOLD SERIF FONT CONVERTER
# ==========================================
def f(text):
    """Converts regular English text to elegant sans-serif Unicode Font (smaller and cleaner)"""
    normal = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    elegant = "𝖠𝖡𝖢𝖣𝖤𝖥𝖦𝖧𝖨𝖩𝖪𝖫𝖬𝖭𝖮𝖯𝖰𝖱𝖲𝖳𝖴𝖵𝖶𝖷𝖸𝖹𝖺𝖻𝖼𝖽𝖾𝖿𝗀𝗁𝗂𝗃𝗄𝗅𝗆𝗇𝗈𝗉𝗊𝗋𝗌𝗍𝗎𝗏𝗐𝗑𝗒𝗓𝟢𝟣𝟤𝟥𝟦𝟧𝟨𝟩𝟪𝟫"
    trans = str.maketrans(normal, elegant)
    return text.translate(trans)

async def ensure_user_registered(user_id, fullname):
    """Auto‑register user with welcome bonus (Doraemon’s gift)"""
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
                "referred_by": None
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

async def search_emoji_id(emoticon_text):
    try:
        result = await bot1(functions.messages.SearchCustomEmojiRequest(
            emoticon=emoticon_text,
            hash=0
        ))
        if result.document_id:
            # Return the first one (most common)
            return result.document_id[0] if isinstance(result.document_id, list) else result.document_id
    except Exception as e:
        print(f"Error searching emoji: {e}")
    return None
# ==========================================
# 🧵 NON-BLOCKING EXECUTOR BRIDGE (for weather API calls)
# ==========================================
async def run_blocking(func, *args, **kwargs):
    """Run blocking functions (like weather API) in thread pool."""
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
                {
                    "$set": {
                        "username": username,
                        "first_name": first_name,
                        "last_active": datetime.now(TZ)
                    }
                },
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

# 🚀 Mute expiry registry
async def register_temporary_mute(chat_id, user_id, duration_minutes=60):
    expiry_time = datetime.now(TZ) + timedelta(minutes=duration_minutes)
    await muted_registry_col.update_one(
        {"chat_id": chat_id, "user_id": user_id},
        {"$set": {"expiry": expiry_time}},
        upsert=True
    )

# ==========================================
# 🏅 ACHIEVEMENT / BADGE SYSTEM (300 achievements)
# ==========================================
def generate_achievements():
    achievements = []

    # 1. Catch count achievements
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

    # 2. Balance achievements
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

    # 3. Streak achievements
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

    # 4. Referral achievements
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

    # 5. Legendary catches
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

    # 6. Marketplace sales
    for target in [1, 10, 50]:
        achievements.append({
            "id": f"sell_{target}",
            "emoji": "🛒",
            "name": f"Sell {target} Cards on Market",
            "desc": f"Successfully sell {target} cards on the marketplace",
            "check": lambda u, t=target: u.get("total_sales", 0) >= t
        })

    # 7. Trades
    for target in [1, 10, 50]:
        achievements.append({
            "id": f"trade_{target}",
            "emoji": "🤝",
            "name": f"Complete {target} Trades",
            "desc": f"Successfully trade {target} cards with other players",
            "check": lambda u, t=target: u.get("total_trades", 0) >= t
        })

    # 8. Casino wins
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

    # 9. Guild level achievements
    for target in [1, 5, 10]:
        achievements.append({
            "id": f"guild_level_{target}",
            "emoji": "🏰",
            "name": f"Guild Level {target}",
            "desc": f"Reach Guild Level {target}",
            "check": lambda u, t=target: u.get("guild_level", 0) >= t
        })

    # 10. Additional unique ones
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

    # Fill up to 300 with extra catch milestones
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
    """Checks a user's stats against all achievements, silently awards any newly
    unlocked ones, and (optionally) posts an unlock notice. Returns the list of
    newly earned achievement dicts so callers can also inline the result."""
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
    """Small inline text block to append to an existing reply (e.g. catch/daily success)."""
    if not newly_earned:
        return ""
    lines = "\n".join(f"{a['emoji']} <b>{a['name']}</b>" for a in newly_earned)
    return f"\n\n🏅 <b>ACHIEVEMENT UNLOCKED!</b>\n{lines}"

# ==========================================
# 📈 PROGRESS BAR / RARITY CLASSIFIER
# ==========================================
RARITY_TIERS = ["LEGENDARY", "LIMITED", "MYTHIC", "EPIC", "RARE", "COMMON"]
RARITY_EMOJI = {"LEGENDARY": "👑", "LIMITED": "💎", "MYTHIC": "🌟", "EPIC": "⚡", "RARE": "🍀", "COMMON": "🃏"}

def _build_fancy_font_reverse_map():
    """Build a reverse-map from EVERY Unicode 'Mathematical Alphanumeric Symbols'
    fancy-font style (bold, italic, script, fraktur, double-struck, sans-serif,
    monospace, etc. — the whole U+1D400–U+1D7FF block) back to plain ASCII.
    This is what makes rarity matching bulletproof: different characters in the
    database turned out to have their rarity saved in DIFFERENT fancy fonts
    (some in MONOSPACE, current /hmode generates SANS-SERIF) — a narrow
    one-style table silently failed to match the other. Covering the whole
    block means it doesn't matter which style any given record used."""
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

def classify_rarity(rarity_str):
    """Detect a rarity tier from ANY format the field might be stored in
    (plain 'LEGENDARY', ANY fancy-unicode font, with/without emoji or stray
    spacing). We always translate fancy glyphs back to plain ASCII first
    (covering every fancy-font style, not just one), then do a substring
    check. This is the fix for /hmode filters not matching in /hai."""
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

# ==========================================
# 🔍 SMART TEXT NORMALIZER
# ==========================================
def normalize_name(text):
    if not text: return ""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

# ==========================================
# 🌐 FLASK KEEP-ALIVE SYSTEM
# ==========================================
app = Flask('')
@app.route('/')
def home(): return "Doraemon Bot System is Active!"

def run_flask(): 
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.ERROR)

# ==========================================
# ⚙️ SYSTEM CONFIGURATIONS & CREDENTIALS
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
TZ = pytz.timezone('Asia/Yangon')
STORAGE_CHANNEL = SPECIFIC_CONTROL_GROUP

# ==========================================
# 🗄️ DATABASE CONNECTION MATRIX
# ==========================================
client_mongo = AsyncIOMotorClient(MONGO_URI)
db = client_mongo["telegram_bot"]

custom_filters_col = db["custom_filters"] 
filters_col = db["filters"]               
allow_col = db["allowed_users"]
groups_col = db["active_groups"]
talk_col = db["random_talk"]
talker_col = db["talker"]
ans_col = db["chatbot_answers"]
morgan_col = db["morgan_talk"] 
rank_col = db["rank_col"]
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
gotu_pairs_col = db["gotu_pairs"]
gotu_games_col = db["gotu_games"]
gotu_players_col = db["gotu_players"]

async def _dedupe_groups_config():
    """One-time cleanup: merge duplicate groups_catcher_config docs that share the
    same chat_id. The chat_id index used to be non-unique, so a race between two
    concurrent upserts (e.g. /talkon and /haitime firing close together) could
    create two separate documents for the same group. find_one()/update_one()
    would then non-deterministically hit either doc, which is exactly why
    /haitime <count> looked like it "reset" back to the default of 50."""
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
                merged[k] = v  # later docs (more recent) win on conflicting fields
        keep_id = docs[-1]["_id"]
        await groups_config_col.update_one({"_id": keep_id}, {"$set": merged})
        remove_ids = [d["_id"] for d in docs if d["_id"] != keep_id]
        if remove_ids:
            await groups_config_col.delete_many({"_id": {"$in": remove_ids}})

async def create_indexes():
    print("⚡ Creating Database Indexes...")
    await users_col.create_index("user_id", unique=True)
    await users_catcher_col.create_index("user_id", unique=True)
    await groups_col.create_index("chat_id", unique=True)
    await reply_save_col.create_index("trigger")
    await users_catcher_col.create_index("total_caught")
    await users_catcher_col.create_index("wallet_balance")
    await users_catcher_col.create_index([("group_catches.$**", 1)])
    await _dedupe_groups_config()
    await groups_config_col.create_index("chat_id", unique=True)
    await muted_registry_col.create_index([("chat_id", 1), ("user_id", 1)])
    await user_game_profiles_col.create_index("user_id", unique=True)
    await characters_col.create_index("name", unique=True)
    await gotu_pairs_col.create_index("pair")
    await gotu_games_col.create_index([("chat_id", 1), ("status", 1)])
    await gotu_players_col.create_index("game_id")
    print("Database Indexes synchronized! ✔️")

bot1 = TelegramClient('bot_main_session', APP_ID, APP_HASH)
bot_ids = []
bot_names = []
active_group_spawns = {} 
active_card_games = {}
STEALTH_MAU_MODE = False
spawn_locks = defaultdict(asyncio.Lock) 

# Tracks bot messages (by message id) that are awaiting a specific reply from the
# owner, e.g. the /editchar character list. Maps message_id -> char_id (a specific
# character to edit) or None (free-form "CharID | Event | SpawnLimit" reply expected).
# Used so the generic reply_to_user_from_owner DM-relay handler doesn't hijack these replies.
pending_editchar_prompt_ids = {}

sticker_spam_data = {}
char_spam_data = {}
admin_cache = {}          
active_rank_messages = {} 
bot_bully_tasks = {}      
shoot_tasks = {}          
tracking_targets = {} 
dark_passenger_targets = {}
learning_status = {}      
talking_status = {}       
message_counts = {}       
GLOBAL_TALK = False 
TRAINING_ENABLED_MORGAN = False      
group_msg_counters = {}
GLOBAL_PREMODE_THRESHOLD = None  
chat_counters = {}               
IS_REPLY_ACTIVE = False              
REPLY_INTERVAL = 4                   
GLOBAL_TALK_INTERVAL = 6            
reply_msg_counters = {}   

USER_METRICS_BUFFER = []
BUFFER_LOCK = asyncio.Lock()
user_cooldowns = {}

def bq(text): return f"<blockquote><b>{text}</b></blockquote>"

def owner_tag():
    return f"<a href='tg://user?id={OWNER_ID}'><b>Owner</b></a>"

# 🎭 RARITY MAPPING MATRIX
RARITY_NUM_MAP = {
    "1": {"name": f"👑 {f('LEGENDARY')}", "value": 1000},  
    "2": {"name": f"💎 {f('LIMITED')}", "value": 750},   
    "3": {"name": f"🌟 {f('MYTHIC')}", "value": 500},    
    "4": {"name": f"⚡ {f('EPIC')}", "value": 300},      
    "5": {"name": f"🍀 {f('RARE')}", "value": 150},      
    "6": {"name": f"🃏 {f('COMMON')}", "value": 50}       
}

# ==========================================
# 🧠 HELPER FUNCTIONS
# ==========================================
async def get_html_mention(event, user_id=None):
    if not user_id: user_id = event.sender_id
    try:
        sender = await event.client.get_entity(user_id)
        first_name = getattr(sender, 'first_name', '') or ''
        last_name = getattr(sender, 'last_name', '') or ''
        fullname = f"{first_name} {last_name}".strip()
        if not fullname: fullname = getattr(sender, 'username', '') or f"Agent {user_id}"
    except:
        fullname = f"Agent {user_id}"
    return f"<a href='tg://user?id={user_id}'><b>{escape_html(fullname)}</b></a>"

# 🎮 Every game result ends with this footer, and the message auto-deletes
# itself 10s later so group chats don't stay cluttered with old game spam.
GAME_FOOTER = "\n\n🪙 <code>/balance</code>"

async def _delete_after_delay(client, chat_id, msg_id, delay=10):
    try:
        await asyncio.sleep(delay)
        await client.delete_messages(chat_id, msg_id)
    except Exception:
        pass

def schedule_game_cleanup(client, chat_id, msg, delay=10):
    """Fire-and-forget: delete a game result message `delay` seconds after
    it's sent, so a group chat full of people playing games doesn't get
    buried in old results. Accepts a Message object or a raw message id."""
    msg_id = getattr(msg, 'id', msg)
    asyncio.create_task(_delete_after_delay(client, chat_id, msg_id, delay))
  
# ==========================================
# ⚔️ ALL GROUPS COMMAND INTERCEPTOR (AUTO ACC OPENER)
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
# 📩 AUTO-FORWARD ALL DM (EXCEPT COMMANDS) TO OWNER
# ==========================================
@bot1.on(events.NewMessage(incoming=True))
async def forward_dm_to_owner(event):
    # Private Chat (DM) မှသာ အလုပ်လုပ်မယ်
    if not event.is_private:
        return
    
    # Owner ကိုယ်တိုင်ပို့တာ နဲ့ Bot ကိုယ်တိုင်ပို့တာကို ကျော်မယ်
    if event.sender_id == OWNER_ID:
        return
    bot_me = await bot1.get_me()
    if event.sender_id == bot_me.id:
        return
    
    # Command ( / နဲ့စတဲ့စာ ) တွေကို မပို့တော့ဘူး (Owner DM မှာ ရှုပ်သွားမှာစိုးလို့)
    if event.text and event.text.startswith('/'):
        return
    
    try:
        # ✅ မူရင်း Message ကို Owner ဆီကို Forward လုပ်မယ် (ဘယ်သူပို့တယ်ဆိုတာ ပါမယ်)
        await bot1.forward_messages(OWNER_ID, event.message)
        
        # User ကို ပြန်ပြောမယ် (သူ့စာရောက်ပြီဆိုတာ သိစေဖို့)
        await event.reply("Bot နဲ့လည်း လာစကားပြောတာပဲလား")
    except Exception as e:
        print(f"Forward error: {e}")
        await event.reply("bot နဲ့လည်း စကားပြောတာပဲလားဟ")


# ==========================================
# 💬 OWNER REPLY TO FORWARDED MESSAGE → SEND BACK TO USER
# ==========================================
@bot1.on(events.NewMessage(incoming=True))
async def reply_to_user_from_owner(event):
    # Private Chat (DM) မှသာ အလုပ်လုပ်မယ်
    if not event.is_private:
        return
    
    # Owner ကနေ Reply လုပ်မှသာ အလုပ်လုပ်မယ်
    if event.sender_id != OWNER_ID:
        return
    
    # Reply လုပ်ထားမှသာ အလုပ်လုပ်မယ်
    if not event.is_reply:
        return
    
    # /editchar list ကို reply လုပ်နေတာဆိုရင် ဒီ handler ကနေ ကျော်သွားမယ် (dedicated handler ရှိလို့)
    if event.reply_to_msg_id in pending_editchar_prompt_ids:
        return
    
    # Owner က Reply လုပ်ထားတဲ့ မူရင်းစာကို ဆွဲယူမယ်
    reply_msg = await event.get_reply_message()
    if not reply_msg:
        return
    
    # အဲဒီစာက Forward လုပ်ထားတဲ့စာမှန်ရင် မူရင်းလူရဲ့ ID ကို ထုတ်မယ်
    if not reply_msg.fwd_from:
        # တခါတရံ forward မဟုတ်တဲ့စာကို reply လုပ်မိနိုင်တယ်၊ ဒါဆိုရင် ဘာမှမလုပ်ပါနဲ့
        await event.reply("⚠️ ဒီစာက Forward လုပ်ထားတာမဟုတ်ဘူး။ ကျေးဇူးပြုပြီး Forward လုပ်ထားတဲ့စာကို Reply လုပ်ပါ။")
        return
    
    # Forward လုပ်ထားတဲ့ မူရင်းလူရဲ့ ID ကို ထုတ်မယ်
    try:
        original_user_id = reply_msg.fwd_from.from_id.user_id
    except Exception:
        await event.reply("❌ မူရင်းလူရဲ့ ID ကို ရှာမတွေ့ဘူး။")
        return
    
    if not original_user_id:
        await event.reply("❌ မူရင်းလူရဲ့ ID မရှိဘူး။")
        return
    
    try:
        # ✅ Owner ရဲ့ Reply စာကို မူရင်းလူဆီ ပြန်ပို့မယ် (Media ပါရင်လည်း အတူတူပို့မယ်)
        if event.media:
            # ပုံ၊ Video၊ Sticker စတာတွေပါရင် Media နဲ့တကွ ပို့မယ်
            await bot1.send_file(original_user_id, event.media, caption=event.text)
        else:
            # စာသားပဲပါရင် စာသားပဲပို့မယ်
            await bot1.send_message(original_user_id, event.text)
        
        # Owner ကို ပြန်ပြောမယ် (ပို့ပြီးပြီဆိုတာ သိစေဖို့)
        await event.reply(f"✅ သင့်အဖြေကို မူရင်းလူ (ID: {original_user_id}) ဆီ ပြန်ပို့လိုက်ပါပြီ။")
    except Exception as e:
        await event.reply(f"❌ ပြန်ပို့ရာမှာ အမှားရှိသွားပါတယ်: {e}")

# ==========================================
# 🛠️ ADMIN CAPABILITY CORE ENGINE
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
# 🧠 OPTIMIZED SMART REPLY ENGINE
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
# ==========================================
# 📥 TELETHON EVENT HANDLERS
# ==========================================

# ---- SYSTEM START / HELP / GAME ----
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
    await ensure_user_registered(user_id, mention)
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
        f"🐱 <b>DORAEMON’S BOT SYSTEM</b> 🎋\n"
        f"Hello! I am Doraemon, your friendly robot cat from the future! 🐱\n"
        f"I can manage groups, spawn magical gadgets (characters), run fun casino games, "
        f"and answer your questions with my 22nd‑century pocket! 🎒\n\n"
        f"Use the buttons below to explore my commands and game rules. "
        f"Nobita would be proud of you! 😄"
        f"{referral_note}"
    )
    buttons = [
        [Button.inline("⚙️ Commands", data="nav_help_main"), Button.inline("🎮 Game Rules", data="nav_game_main")],
        [Button.url("🪐 Add Me To Your Group", f"https://t.me/{bot_me.username}?startgroup=true")],
        [Button.url("👥 Join the Doraemon Fan Club", "https://t.me/Comeback_BoD")]
    ]
    await event.respond(bq(welcome_msg), parse_mode='html', buttons=buttons)

@bot1.on(events.CallbackQuery())
async def system_callback_router(event):
    data = event.data.decode('utf-8')
    user_id = event.sender_id
    
    if data == "nav_back_home":
        bot_me = await bot1.get_me()
        welcome_msg = (
            f"🐱 <b>DORAEMON’S BOT SYSTEM</b> 🎋\n"
            f"Hello! I am Doraemon, your friendly robot cat from the future! 🐱\n"
            f"I can manage groups, spawn magical gadgets (characters), run fun casino games, "
            f"and answer your questions with my 22nd‑century pocket! 🎒"
        )
        buttons = [
            [Button.inline("⚙️ Commands", data="nav_help_main"), Button.inline("🎮 Game Rules", data="nav_game_main")],
            [Button.url("Add Me To Your Group", f"https://t.me/{bot_me.username}?startgroup=true")],
            [Button.url("Join the Doraemon Fan Club", "https://t.me/Comeback_BoD")]
        ]
        return await event.edit(bq(welcome_msg), parse_mode='html', buttons=buttons)

    elif data == "nav_help_main":
        help_text = (
            f"⚙️ <b><u>Doraemon’s Command Terminal</u></b>\n\n"
            f" <code>/id</code> - Get user or chat ID\n"
            f" <code>/mute [Reply]</code> - Mute a user permanently\n"
            f" <code>/unmute [Reply]</code> - Unmute a user\n"
            f" <code>/ban [Reply]</code> - Ban a user from the group\n"
            f" <code>/kick [Reply]</code> - Kick a user from the chat\n"
            f" <code>/adm [Text]</code> - Tag all admins in the group\n"
            f" <code>/calc [Expression]</code> - Calculate math expressions\n"
            f" <code>/weather</code> - Live weather report (Myanmar & Thailand)"
        )
        return await event.edit(bq(help_text), parse_mode='html', buttons=[[Button.inline("🔙 Back", data="nav_back_home")]])

    elif data == "nav_game_main":
        game_text = (
            f"🎮 <b><u>Doraemon’s Game Console</u></b>\n\n"
            f"⭐ <code>/checkp</code>\n<blockquote>Check your wallet balance.</blockquote>\n\n"
            f"⭐ <code>/daily</code>\n<blockquote>Claim your daily bonus (streak rewards!).</blockquote>\n\n"
            f"⭐ <code>/hunt</code>\n<blockquote>Go on a hunting adventure for extra cash.</blockquote>\n\n"
            f"⭐ <code>/pay [Amount] [Username/ID]</code>\n<blockquote>Send money to another player.</blockquote>\n\n"
            f"⭐ <code>/game</code>\n<blockquote>View all available mini‑games.</blockquote>"
        )
        return await event.edit(bq(game_text), parse_mode='html', buttons=[[Button.inline("🔙 Back", data="nav_back_home")]])
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

# ---- WELCOME / GOODBYE (UPGRADED) ----
@bot1.on(events.ChatAction)
async def welcome_goodbye(event):
    try:
        chat = await event.get_chat()
        bot_me = await bot1.get_me()
        group_name = chat.title or "Unknown Group"
        target_ids = event.user_ids or [event.user_id]

        # Bot ကိုထည့်လိုက်တဲ့အခါ (Group သစ်ရောက်တဲ့အခါ)
        if (event.user_added or event.user_joined) and any(uid == bot_me.id for uid in target_ids if uid):
            # 1️⃣ Group အကြောင်းအရာ အပြည့်အစုံရယူမယ် (Member တွက်ဖို့)
            try:
                full_chat = await event.client.get_full_chat(chat.id)
                members_cnt = getattr(full_chat, 'participants_count', None)
                member_count = f"{members_cnt:,}" if members_cnt else "Unknown"
            except Exception:
                member_count = "N/A"

            # 2️⃣ Group Link ရယူဖို့ကြိုးစားမယ် (Admin ဖြစ်မှရမယ်)
            group_link = None
            try:
                invite = await event.client(ExportChatInviteRequest(chat.id))
                group_link = invite.link if invite else None
            except Exception:
                group_link = "Cannot fetch (need admin rights)"
            if not group_link:
                group_link = "Not available (or bot not admin)"

            # 3️⃣ Owner DM ကိုသတိပေးမယ်
            owner_msg = (
                f"✅ <b>Bot Added to New Group!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📛 <b>Group Name:</b> <code>{escape_html(group_name)}</code>\n"
                f"🆔 <b>Group ID:</b> <code>{chat.id}</code>\n"
                f"👥 <b>Members:</b> <code>{member_count}</code>\n"
                f"🔗 <b>Invite Link:</b> <code>{escape_html(str(group_link))}</code>\n"
                f"⏰ <b>Time:</b> <code>{datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}</code>"
            )
            await bot1.send_message(OWNER_ID, owner_msg, parse_mode='html')
            
            # 4️⃣ SPECIFIC_GROUP (သတ်မှတ်ထားတဲ့အုပ်စု) ကိုလည်းသတိပေးမယ်
            try:
                await bot1.send_message(SPECIFIC_GROUP, owner_msg, parse_mode='html')
            except Exception:
                pass  # Specific group မှာ မပို့ရရင် ဂရုမစိုက်ပါနဲ့

            # 5️⃣ အုပ်စုသစ်ထဲမှာ ကြိုဆိုစာ (blockquote နဲ့လှလှ) + ညွှန်ကြားချက်
            intro_msg = (
                f"<blockquote>🐱 <b>Doraemon Bot is now active here!</b>\n\n"
                f"I can help manage this group, run games, and more!\n"
                f"📌 <b>Try these commands:</b>\n"
                f"   • <code>/introduce</code> - Learn what I can do\n"
                f"   • <code>/help</code> - See all commands\n\n"
                f"✨ Enjoy your stay!</blockquote>"
            )
            await bot1.send_message(chat.id, intro_msg, parse_mode='html')
            
            # Group ကို DB ထဲသိမ်းမယ်
            await groups_col.update_one(
                {"chat_id": chat.id}, 
                {"$set": {"title": chat.title, "joined_at": datetime.now(TZ)}}, 
                upsert=True
            )
            return  # ✅ ဒီနေရာမှာ ရပ်လိုက်မယ်၊ ဒါမှ user welcome နဲ့ မထပ်တွေ့တော့ဘူး

        # 👤 User တစ်ယောက် Join လာရင် (Bot မဟုတ်ဘူး)
        elif (event.user_added or event.user_joined):
            for uid in target_ids:
                if uid and uid != bot_me.id:
                    try:
                        u = await bot1.get_entity(uid)
                    except:
                        continue
                    track_user_metrics(u.id, u.username, u.first_name)
                    user_first_name = u.first_name or "User"
                    mention = f"<a href='tg://user?id={uid}'><b>{escape_html(user_first_name)}</b></a>"
                    
                    welcome_text = (
                        f"<blockquote>ဟိုက် {mention}... {escape_html(group_name)} ကနေ ကြိုဆိုလိုက်ပါတယ်\n"
                        f"🐱 Doraemon is happy to see you!</blockquote>"
                    )
                    await bot1.send_message(
                        event.chat_id,
                        welcome_text,
                        buttons=[
                            [Button.url("Bot OWNER DM", "t.me/Hello_Im_DexterMorgan")],
                            [Button.url("Join Doraemon Group", "https://t.me/Comeback_BoD")]
                        ],
                        parse_mode='html'
                    )

        # 🚪 User ထွက်သွား / ဖြုတ်ခံရရင်
        elif event.user_kicked or event.user_left:
            if event.user_id == bot_me.id:
                await groups_col.delete_one({"chat_id": chat.id})
            else:
                try:
                    u = await bot1.get_entity(event.user_id)
                    user_first_name = u.first_name or "User"
                    mention = f"<a href='tg://user?id={event.user_id}'><b>{escape_html(user_first_name)}</b></a>"
                    goodbye_text = f"<blockquote> တာ့တာ {mention}!\nWe'll miss you.</blockquote>"
                    await bot1.send_message(event.chat_id, goodbye_text, parse_mode='html')
                except:
                    pass

    except Exception as e:
        # Error က Owner DM ကို ရောက်မယ်၊ ဒါပေမယ့် ဒီအတိုင်းထားလိုက်ပါ
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
    
    loading = await event.reply("<code>🌌 Fetching core metrics from Doraemon’s database...</code>", parse_mode='html')
    try:
        total_tracked_users = await users_col.count_documents({})
        total_active_groups = await groups_col.count_documents({})
        day_ago = datetime.now(TZ) - timedelta(days=1)
        active_24h_users = await users_col.count_documents({"last_active": {"$gte": day_ago}})
        
        metrics_layout = (
            f"🐱 <b>Doraemon’s MAU Stats</b>\n"
            f"👥 <b>Total Managed Users:</b> <code>{total_tracked_users} 👤</code>\n"
            f"🔥 <b>Active Users (24H):</b> <code>{active_24h_users} 🔥</code>\n"
            f"📍 <b>Active Groups:</b> <code>{total_active_groups} 🪐</code>\n"
            f"⚙️ <b>Engine Status:</b> <code>Doraemon’s pocket is ready!</code>"
        )
        await loading.edit(bq(metrics_layout), parse_mode='html')
    except Exception as e:
        await loading.edit(bq(f"❌ Analytics Error: {e}"), parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/talkon(?:@\w+)?(?:\s+(\d+))?$'))
async def talk_on_handler(event):
    if event.is_private:
        return await event.reply("❌ This command only works in groups.")
    
    # Check if user is admin (optional, but recommended)
    if not await check_admin(event.chat_id, event.sender_id):
        return await event.reply("❌ Only admins can enable random talk.")
    
    ratio_str = event.pattern_match.group(1)
    ratio = int(ratio_str) if ratio_str else 5
    if ratio < 1:
        return await event.reply("❌ Ratio must be at least 1.")
    
    chat_id = event.chat_id
    await groups_config_col.update_one(
        {"chat_id": chat_id},
        {"$set": {"talk_enabled": True, "talk_ratio": ratio, "talk_counter": 0}},
        upsert=True
    )
    await event.reply(f"✅ Random talk enabled! I will reply with a random message every <code>{ratio}</code> user messages.", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/talkoff(?:@\w+)?$'))
async def talk_off_handler(event):
    if event.is_private:
        return await event.reply("❌ This command only works in groups.")
    
    if not await check_admin(event.chat_id, event.sender_id):
        return await event.reply("❌ Only admins can disable random talk.")
    
    chat_id = event.chat_id
    await groups_config_col.update_one(
        {"chat_id": chat_id},
        {"$set": {"talk_enabled": False}},
        upsert=True
    )
    await event.reply("✅ Random talk disabled.")

@bot1.on(events.NewMessage(pattern=r'^/talklist(?:@\w+)?$'))
async def list_talk_messages(event):
    if event.sender_id != OWNER_ID:
        return
    cursor = talk_col.find().limit(20)
    docs = await cursor.to_list(length=20)
    if not docs:
        return await event.reply("No messages found.")
    lines = []
    for i, doc in enumerate(docs, 1):
        msg = extract_message_from_doc(doc) or "No text"
        lines.append(f"{i}. {escape_html(msg[:50])}{'...' if len(msg) > 50 else ''}")
    await event.reply("📋 <b>Random Talk Messages (max 20):</b>\n\n" + "\n".join(lines), parse_mode='html')

# ---- RARITY MIGRATION ----
@bot1.on(events.NewMessage(pattern=r'^/migrate_rarity(?:@\w+)?$'))
async def migrate_rarity_handler(event):
    if event.sender_id != OWNER_ID:
        return await event.reply("❌ Owner only command.")
    
    status_msg = await event.reply("⏳ Migrating rarity emojis... Please wait.")

    def detect_tier(rarity_str):
        tier = classify_rarity(rarity_str)
        return tier if tier != "OTHER" else None

    tier_to_new_name = {}
    for num, data in RARITY_NUM_MAP.items():
        for tier in RARITY_TIERS:
            if f(tier) in data["name"]:
                tier_to_new_name[tier] = data["name"]
                break

    updated_base = 0
    updated_users = 0

    all_base_chars = await characters_base_col.find({}, {"_id": 1, "rarity": 1}).to_list(length=None)
    for char in all_base_chars:
        old_rarity = char.get("rarity")
        tier = detect_tier(old_rarity)
        if not tier:
            continue
        new_rarity = tier_to_new_name.get(tier)
        if new_rarity and new_rarity != old_rarity:
            result = await characters_base_col.update_one(
                {"_id": char["_id"]},
                {"$set": {"rarity": new_rarity}}
            )
            updated_base += result.modified_count

    users = await users_catcher_col.find({}).to_list(length=None)
    for user in users:
        harem = user.get("harem", [])
        modified = False
        new_harem = []
        for item in harem:
            if isinstance(item, dict):
                old_rarity = item.get("rarity")
                tier = detect_tier(old_rarity)
                new_rarity = tier_to_new_name.get(tier) if tier else None
                if new_rarity and new_rarity != old_rarity:
                    item["rarity"] = new_rarity
                    modified = True
            new_harem.append(item)
        if modified:
            await users_catcher_col.update_one(
                {"_id": user["_id"]},
                {"$set": {"harem": new_harem}}
            )
            updated_users += 1

    await status_msg.edit(
        f"✅ <b>Rarity Migration Complete!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>Base Characters Updated:</b> <code>{updated_base}</code>\n"
        f"👥 <b>User Inventories Updated:</b> <code>{updated_users}</code>"
    )

# ---- SPAM FILTERS ----
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

    if len(recent_times) >= 6:
        try:
            is_admin = await check_admin(chat_id, sender_id)
            await bot1.delete_messages(chat_id, recent_ids)
            sender = await event.get_sender()
            mention = f"<a href='tg://user?id={sender_id}'>{escape_html(sender.first_name)}</a>"

            if is_admin:
                action_msg = f"⚠️ Admin {mention}, please refrain from spamming stickers."
            else:
                await bot1.edit_permissions(chat_id, sender_id, send_stickers=False)
                action_msg = f"{mention} has been restricted from sending stickers due to spam."
            
            await event.respond(bq(action_msg), parse_mode='html')
            del sticker_spam_data[sender_id]
            return
        except Exception as e: print(f"Spam Filter Error: {e}")

    if len(recent_times) >= 3:
        time_diff = (recent_times[-1] - recent_times[-3]).total_seconds()
        if time_diff <= 1.0: 
            await event.delete()

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

    if len(recent_times) >= 6:
        try:
            is_admin = await check_admin(chat_id, sender_id)
            await bot1.delete_messages(chat_id, recent_ids)
            sender = await event.get_sender()
            mention = f"<a href='tg://user?id={sender_id}'>{escape_html(sender.first_name)}</a>"

            if is_admin:
                action_msg = f"⚠️ Admin {mention}, please stop spamming short messages."
            else:
                await register_temporary_mute(chat_id, sender_id, duration_minutes=5)
                await bot1.edit_permissions(chat_id, sender_id, until_date=datetime.now() + timedelta(minutes=5), send_messages=False)
                action_msg = f"🚫 {mention} has been muted for 5 minutes due to spam."
            
            await event.respond(bq(action_msg), parse_mode='html')
            del char_spam_data[sender_id]
            return
        except Exception as e: print(f"Char Spam Mute Error: {e}")

    if len(recent_times) == 5:
        try:
            await bot1.delete_messages(chat_id, recent_ids)
            sender = await event.get_sender()
            mention = f"<a href='tg://user?id={sender_id}'>{escape_html(sender.first_name)}</a>"
            warn_msg = f"⚠️ {mention}, stop spam or you’ll be muted for 5 minutes!"
            await event.respond(bq(warn_msg), parse_mode='html')
        except Exception as e: print(f"Char Spam Warn Error: {e}")

# ---- FILTERS ----
@bot1.on(events.NewMessage(pattern=r'^[/.]f\s+(.*)'))
async def add_filter(event):
    if not await check_admin(event.chat_id, event.sender_id): return
    input_str = event.pattern_match.group(1).split(None, 1)
    keyword = input_str[0].lower().strip()
    reply = await event.get_reply_message()
    if reply:
        try:
            forwarded = await bot1.forward_messages(STORAGE_CHANNEL, reply)
            await custom_filters_col.update_one(
                {"keyword": keyword},
                {"$set": {"type": "storage_msg", "msg_id": forwarded.id, "chat_id": "global"}},
                upsert=True
            )
            await event.reply(bq(f"✅ Filter '{keyword}' has been saved!"), parse_mode='html')
        except Exception as e:
            await event.reply(bq(f"❌ Error saving filter: {e}"), parse_mode='html')
    else:
        await event.reply(bq("⚠️ Reply to a message with the content you want to save as a filter."), parse_mode='html')

# ---- GOTU SYSTEM ----
DEFAULT_GOTU_PRIZE = 3000  # MMK — used when a pair was added without an explicit prize

@bot1.on(events.NewMessage(pattern=r'^/(?:setgotu|guess)(?:@\w+)?(?:\s+([\s\S]+))?$'))
async def set_gotu_pair_handler(event):
    if event.sender_id != OWNER_ID:
        return await event.reply("❌ This command is for the owner only.")
    
    if not event.is_private:
        return await event.reply("❌ Please use this command in DM.")
    
    text = event.pattern_match.group(1)
    if not text:
        return await event.reply(
            "📝 <b>Add word pairs (bulk supported!)</b>\n"
            "Send <code>/guess</code> followed by pairs, one per line — as many as you like:\n"
            "<code>Majority | Minority | Prize</code>\n\n"
            "<b>Example:</b>\n"
            "<code>/guess\n"
            "ပန်းသီး | ခရမ်းသီး | 3000\n"
            "ခြင် | ဝက် | 5000\n"
            "ဆင် | ကျား</code>\n\n"
            f"<i>Prize is optional — defaults to {DEFAULT_GOTU_PRIZE:,} MMK if left out.</i>"
        )
    
    lines = text.strip().split('\n')
    added = 0
    skipped = 0
    errors = []
    
    for line in lines:
        line = line.strip()
        if not line or '|' not in line:
            continue
        
        parts = [p.strip() for p in line.split('|')]
        if len(parts) not in (2, 3):
            errors.append(f"❌ Invalid: {escape_html(line[:40])}")
            continue
        
        pair = parts[:2]
        prize = DEFAULT_GOTU_PRIZE
        if len(parts) == 3 and parts[2]:
            if parts[2].isdigit():
                prize = int(parts[2])
            else:
                errors.append(f"❌ Prize must be a number: {escape_html(line[:40])}")
                continue

        existing = await gotu_pairs_col.find_one({"pair": pair})
        if existing:
            skipped += 1
            continue
        
        await gotu_pairs_col.insert_one({"pair": pair, "prize": prize})
        added += 1
    
    reply_msg = f"✅ <b>Bulk pairs added!</b>\n"
    reply_msg += f"📦 <b>Added:</b> {added}\n"
    reply_msg += f"⏭️ <b>Skipped (already exist):</b> {skipped}\n"
    if errors:
        reply_msg += f"❌ <b>Errors:</b>\n" + "\n".join(errors[:10])
    
    await event.reply(reply_msg, parse_mode='html')

async def resolve_gotu_guess(event):
    chat_id = event.chat_id
    user_id = event.sender_id
    args = (event.pattern_match.group(1) or "").strip()

    game = await gotu_games_col.find_one({"chat_id": chat_id, "status": "active"})
    if not game:
        return await event.reply("❌ No active game in this chat. Start one with <code>/gotu start</code>")

    target_user_id = None
    if event.is_reply:
        reply_msg = await event.get_reply_message()
        target_user_id = reply_msg.sender_id
    elif args.startswith('@'):
        try:
            entity = await bot1.get_entity(args)
            target_user_id = entity.id
        except Exception:
            return await event.reply("❌ User not found. Use @username or reply to their message.")
    else:
        return await event.reply("❌ Reply to the user you suspect, or use <code>/catching @username</code>.")

    target_player = await gotu_players_col.find_one({"game_id": game["_id"], "user_id": target_user_id, "alive": True})
    if not target_player:
        return await event.reply("❌ That user is not a player in this game or already dead.")

    guesser = await gotu_players_col.find_one({"game_id": game["_id"], "user_id": user_id, "alive": True})
    if not guesser:
        return await event.reply("❌ You are not a player in this game.")

    if guesser.get("has_guessed", False):
        return await event.reply("❌ You have already used your guess! 😅")

    unique_player = await gotu_players_col.find_one({"game_id": game["_id"], "is_unique": True})

    if target_player["_id"] == unique_player["_id"]:
        # ✅ Caught! The majority wins — prize goes to whoever made the catch.
        await gotu_games_col.update_one({"_id": game["_id"]}, {"$set": {"status": "ended", "winner": user_id}})
        await gotu_players_col.update_one({"_id": guesser["_id"]}, {"$set": {"has_guessed": True}})

        prize = game.get("prize", DEFAULT_GOTU_PRIZE)
        mention = await get_html_mention(event, user_id)
        await ensure_user_registered(user_id, mention)
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": prize}})

        reveal_msg = await event.reply("🔍 <b>Unmasking...</b>", parse_mode='html')
        for frame in ["🔍 ❓❓❓❓", "🔍 🍎❓🍎❓", "🔍 🍎🍅🍎🍎"]:
            await asyncio.sleep(0.5)
            try:
                await bot1.edit_message(chat_id, reveal_msg, f"<b>{frame}</b>", parse_mode='html')
            except Exception:
                pass

        all_players = await gotu_players_col.find({"game_id": game["_id"]}).to_list(length=None)
        roles_lines = "\n".join(
            f"• {p['username']}: <code>{p['role_item']}</code>{' 🎭 (ODD ONE)' if p['is_unique'] else ''}"
            for p in all_players
        )
        final_text = (
            f"🎉 <b>CAUGHT!</b> 🎉\n\n"
            f"👤 <b>{guesser['username']}</b> correctly caught the odd one out!\n"
            f"🎭 It was <b>{unique_player['username']}</b> — hiding <code>{unique_player['role_item']}</code>!\n\n"
            f"📋 <b>Final Roles</b>\n<blockquote>{roles_lines}</blockquote>\n\n"
            f"🪙 <b>Prize:</b> <code>+{prize:,} MMK</code> to {guesser['username']}!"
            f"{GAME_FOOTER}"
        )
        await bot1.edit_message(chat_id, reveal_msg, final_text, parse_mode='html')
        if not event.is_private:
            schedule_game_cleanup(bot1, chat_id, reveal_msg)
    else:
        await gotu_players_col.update_one({"_id": guesser["_id"]}, {"$set": {"has_guessed": True}})
        async with bot1.action(chat_id, 'typing'):
            await asyncio.sleep(1)
        await event.reply(
            f"❌ <b>Wrong!</b> {target_player['username']} is not the odd one out.\n"
            f"😅 You have used your guess! Better luck next time, {guesser['username']}!"
        )

@bot1.on(events.NewMessage(pattern=r'^/catching(?:@\w+)?(?:\s+(.*))?$'))
async def catching_alias_handler(event):
    await resolve_gotu_guess(event)

@bot1.on(events.NewMessage(pattern=r'^/gotu(?:@\w+)?(?:\s+(.*))?'))
async def gotu_handler(event):
    chat_id = event.chat_id
    user_id = event.sender_id
    args = event.pattern_match.group(1) or ""
    args = args.strip().lower()
    
    if args.startswith('@') or args:
        return await resolve_gotu_guess(event)
    
    if args == "start":
        existing = await gotu_games_col.find_one({
            "chat_id": chat_id,
            "status": {"$in": ["lobby", "active"]}
        })
        if existing:
            return await event.reply("❌ A game is already in progress or lobby.")
        
        pair_doc = await gotu_pairs_col.aggregate([{"$sample": {"size": 1}}]).to_list(length=1)
        if not pair_doc:
            return await event.reply("❌ No word pairs available. Owner needs to add some with <code>/guess</code>")
        
        pair = pair_doc[0]["pair"]
        prize = pair_doc[0].get("prize", DEFAULT_GOTU_PRIZE)
        game = {
            "chat_id": chat_id,
            "host_id": user_id,
            "status": "lobby",
            "pair": pair,
            "prize": prize,
            "created_at": datetime.now(TZ),
            "started_at": None,
            "unique_item": None,
            "winner": None
        }
        result = await gotu_games_col.insert_one(game)
        game_id = result.inserted_id
        
        sender = await event.get_sender()
        await gotu_players_col.insert_one({
            "game_id": game_id,
            "user_id": user_id,
            "username": sender.first_name or "Player",
            "role_item": None,
            "is_unique": False,
            "alive": True,
            "has_guessed": False
        })
        
        await event.reply(
            f"🎮 <b>ODD ONE OUT — Lobby Created!</b>\n\n"
            f"👑 <b>Host:</b> {sender.first_name}\n"
            f"🪙 <b>Prize:</b> <code>{prize:,} MMK</code>\n\n"
            f"💕 Use <code>/gotu join</code> to join!\n"
            f"🐱 Need at least 3 players to start.\n"
            f"Host: <code>/gotu startgame</code> when ready!"
        )
        return
    
    elif args == "join":
        game = await gotu_games_col.find_one({
            "chat_id": chat_id,
            "status": "lobby"
        })
        if not game:
            return await event.reply("❌ No lobby open. Host: <code>/gotu start</code>")
        
        existing_player = await gotu_players_col.find_one({
            "game_id": game["_id"],
            "user_id": user_id
        })
        if existing_player:
            return await event.reply("❌ You are already in the lobby!")
        
        user_entity = await event.get_sender()
        bot_me = await bot1.get_me()
        
        user_doc = await users_catcher_col.find_one({"user_id": user_id})
        if not user_doc:
            return await event.reply(
                f"💕 {user_entity.first_name}, you need to meet Doraemon first! 🐱\n\n"
                f"📌 Please send <code>/start</code> to @{bot_me.username} in private chat to register.\n"
                f"💡 After that, use <code>/gotu join</code> again!\n"
                f"🐱 Doraemon is waiting for you! 🌸"
            )
        
        mention = await get_html_mention(event, user_id)
        await ensure_user_registered(user_id, mention)
        
        sender = await event.get_sender()
        await gotu_players_col.insert_one({
            "game_id": game["_id"],
            "user_id": user_id,
            "username": sender.first_name or "Player",
            "role_item": None,
            "is_unique": False,
            "alive": True,
            "has_guessed": False
        })
        
        players = await gotu_players_col.find({"game_id": game["_id"]}).to_list(length=None)
        player_list = "\n".join([f"• {p['username']}" for p in players])
        await event.reply(
            f"🎮 <b>ODD ONE OUT — Lobby</b>\n\n"
            f"👥 <b>Players ({len(players)}):</b>\n{player_list}\n\n"
            f"🪙 <b>Prize:</b> <code>{game['prize']:,} MMK</code>\n"
            f"🐱 Need at least 3 players. Host: <code>/gotu startgame</code>"
        )
        return
    
    elif args == "leave":
        game = await gotu_games_col.find_one({
            "chat_id": chat_id,
            "status": "lobby"
        })
        if not game:
            return await event.reply("❌ No lobby to leave.")
        result = await gotu_players_col.delete_one({
            "game_id": game["_id"],
            "user_id": user_id
        })
        if result.deleted_count:
            await event.reply("✅ You have left the lobby.")
        else:
            await event.reply("❌ You are not in the lobby.")
        return
    
    elif args == "startgame":
        game = await gotu_games_col.find_one({
            "chat_id": chat_id,
            "status": "lobby"
        })
        if not game:
            return await event.reply("❌ No lobby to start.")
        if game["host_id"] != user_id:
            return await event.reply("❌ Only the host can start the game.")
        
        players = await gotu_players_col.find({"game_id": game["_id"]}).to_list(length=None)
        if len(players) < 3:
            return await event.reply(f"❌ Need at least 3 players. Currently {len(players)}.")
        
        random.shuffle(players)
        unique_player = players[0]
        common_item = game["pair"][0]
        unique_item = game["pair"][1]
        prize = game.get("prize", DEFAULT_GOTU_PRIZE)
        
        for p in players:
            is_unique = (p["_id"] == unique_player["_id"])
            role = unique_item if is_unique else common_item
            await gotu_players_col.update_one(
                {"_id": p["_id"]},
                {"$set": {"role_item": role, "is_unique": is_unique, "alive": True}}
            )
        
        await gotu_games_col.update_one(
            {"_id": game["_id"]},
            {"$set": {"status": "active", "started_at": datetime.now(TZ), "unique_item": unique_item}}
        )
        
        dm_failed = []
        for p in players:
            try:
                if p["is_unique"]:
                    role_msg = (
                        f"🎭 <b>Your Role / သင်၏ အခန်းကဏ္ဍ</b>\n\n"
                        f"🔍 <b>English:</b> You are the <b>ODD ONE OUT</b>!\n"
                        f"   Your item: <b>{unique_item}</b>\n"
                        f"   Others have a <b>different</b> item. You don't know what it is!\n"
                        f"   Blend in and don't get caught!\n\n"
                        f"🇲🇲 <b>မြန်မာ:</b> သင်သည် <b>ထူးခြားသူ</b> ဖြစ်သည်!\n"
                        f"   သင့်အရာ: <b>{unique_item}</b>\n"
                        f"   အခြားသူများက <b>မတူညီတဲ့အရာ</b> ရထားသည်။ သင်မသိပါ။\n"
                        f"   သာမန်လိုဟန်ဆောင်ပြီး မမိအောင်နေပါ!"
                    )
                else:
                    role_msg = (
                        f"🎭 <b>Your Role / သင်၏ အခန်းကဏ္ဍ</b>\n\n"
                        f"👤 <b>English:</b> You are a <b>COMMON</b> player.\n"
                        f"   Your item: <b>{common_item}</b>\n"
                        f"   The odd one out has something different — find them!\n\n"
                        f"🇲🇲 <b>မြန်မာ:</b> သင်သည် <b>သာမန်</b> ကစားသမားဖြစ်သည်။\n"
                        f"   သင့်အရာ: <b>{common_item}</b>\n"
                        f"   ထူးခြားသူကို ရှာပါ!"
                    )
                await bot1.send_message(p["user_id"], role_msg, parse_mode='html')
            except Exception as e:
                dm_failed.append(p["username"])
                print(f"Failed to send DM to {p['user_id']}: {e}")
        
        dm_warning = ""
        if dm_failed:
            dm_warning = f"\n⚠️ <i>Couldn't DM: {', '.join(dm_failed)} — they need to /start the bot privately first.</i>"
        
        await event.reply(
            f"🎮 <b>ODD ONE OUT — GAME STARTED!</b>\n\n"
            f"👥 <b>Players:</b> {len(players)}\n"
            f"🪙 <b>Prize:</b> <code>{prize:,} MMK</code>\n"
            f"🔍 <b>Check your DM for your secret item!</b>\n"
            f"⏱️ <b>Time limit:</b> 5 minutes\n"
            f"💕 Reply to who you suspect with <code>/catching</code> (or <code>/gotu @username</code>)!\n"
            f"🐱 Good luck!{dm_warning}"
        )
        
        async def game_timer():
            await asyncio.sleep(300)
            g = await gotu_games_col.find_one({"_id": game["_id"]})
            if g and g["status"] == "active":
                await gotu_games_col.update_one({"_id": game["_id"]}, {"$set": {"status": "ended"}})
                all_players = await gotu_players_col.find({"game_id": game["_id"]}).to_list(length=None)
                unique_p = next((p for p in all_players if p["is_unique"]), None)

                # ⏰ Nobody caught the odd one in time — they win the prize.
                if unique_p:
                    await ensure_user_registered(unique_p["user_id"], unique_p["username"])
                    await users_catcher_col.update_one({"user_id": unique_p["user_id"]}, {"$inc": {"wallet_balance": prize}})

                reveal_msg = await bot1.send_message(chat_id, "⏰ <b>Time's up! Unmasking...</b>", parse_mode='html')
                for frame in ["⏰ ❓❓❓❓❓", "⏰ 🍎🍎❓🍎🍎", "⏰ 🍎🍎🍅🍎🍎"]:
                    await asyncio.sleep(0.5)
                    try:
                        await bot1.edit_message(chat_id, reveal_msg, f"<b>{frame}</b>", parse_mode='html')
                    except Exception:
                        pass

                roles_lines = "\n".join(
                    f"• {p['username']}: <code>{p['role_item']}</code>{' 🎭 (ODD ONE)' if p['is_unique'] else ''}"
                    for p in all_players
                )
                prize_line = f"🪙 <b>Nobody caught them in time — {unique_p['username']} wins <code>{prize:,} MMK</code>!</b>" if unique_p else ""
                roles_msg = (
                    f"⏰ <b>Time's Up!</b>\n\n"
                    f"📋 <b>Final Roles</b>\n<blockquote>{roles_lines}</blockquote>\n\n"
                    f"{prize_line}"
                    f"{GAME_FOOTER}"
                )
                await bot1.edit_message(chat_id, reveal_msg, roles_msg, parse_mode='html')
                schedule_game_cleanup(bot1, chat_id, reveal_msg)
        
        asyncio.create_task(game_timer())
        return
    
    else:
        await event.reply(
            f"🎮 <b>ODD ONE OUT — Game Commands</b>\n\n"
            f"<code>/gotu start</code> - Create a game lobby\n"
            f"<code>/gotu join</code> - Join the lobby\n"
            f"<code>/gotu leave</code> - Leave the lobby\n"
            f"<code>/gotu startgame</code> - Start the game (host only)\n"
            f"<code>/catching @username</code> (or reply) - Accuse the odd one out\n\n"
            f"🐱 Find the odd one out before time runs out!"
        )

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

# ---- ADD CHARACTER ----
@bot1.on(events.NewMessage(pattern=r'^/addchar(?:@\w+)?(?:\s+(.+))?'))
async def add_character(event):
    if event.sender_id != OWNER_ID: return

    input_text = event.pattern_match.group(1)
    if not input_text or '|' not in input_text:
        await event.reply(
            f"⚠️ <b>{f('Invalid format!')}</b>\n"
            f"📌 <b>{f('Usage')}:</b>\n"
            f"<code>/addchar Name | Category | Rarity_Number | Event | SpawnLimit</code>\n"
            f"<i>(Reply to a media file. Event and SpawnLimit are optional.)</i>\n\n"
            f"🔢 <b>{f('Rarity Tiers (1-6)')}:</b>\n"
            f"<code>1</code> = 👑 LEGENDARY (1000 MMK)\n"
            f"<code>2</code> = 💎 LIMITED-EDITION (750 MMK)\n"
            f"<code>3</code> = 🌟 MYTHIC (500 MMK)\n"
            f"<code>4</code> = ⚡ EPIC (300 MMK)\n"
            f"<code>5</code> = 🍀 RARE (150 MMK)\n"
            f"<code>6</code> = 🃏 COMMON (50 MMK)\n\n"
            f"🎪 <b>Event:</b> free text, e.g. <code>Summer Festival</code> (leave blank/'-' for none)\n"
            f"🔁 <b>SpawnLimit:</b> how many times total this character may spawn (0 or blank = infinite)",
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

        character_data = {
            "char_id": char_id,
            "name": char_name,
            "category": category_name,
            "rarity": r_info["name"],
            "storage_msg_id": storage_id,
            "currency_value": r_info["value"],
            "spawn_count": 0,
            "event": event_name,
            "spawn_limit": spawn_limit
        }
        await characters_base_col.insert_one(character_data)
        limit_text = "♾️ Infinite" if spawn_limit == 0 else str(spawn_limit)
        await event.reply(
            f"🔥 <b>{f('DATABASE INJECTED')}</b>\n"
            f"🆔 <b>{f('Character ID')}:</b> <code>{char_id}</code>\n"
            f"👤 <b>{f('Name')}:</b> <code>{char_name}</code>\n"
            f"🌟 <b>{f('Rarity')}:</b> {r_info['name']}\n"
            f"💎 <b>{f('Worth')}:</b> <code>{r_info['value']} MMK</code>\n"
            f"🎪 <b>Event:</b> <code>{escape_html(event_name)}</code>\n"
            f"🔁 <b>Spawn Limit:</b> <code>{limit_text}</code>",
            parse_mode='html'
        )
    except Exception as e:
        await event.reply(f"❌ <b>Database Inject Error</b>: <code>{escape_html(str(e))}</code>", parse_mode='html')

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
        await event.reply(f"🔥 {f('DATABASE REMOVED')}\n🆔 {f('Character ID')}: {char_id}\nStatus: Deleted.")
    else:
        await event.reply(f"❌ No character found with ID `{char_id}`.")

# ---- EDIT CHARACTER (Event name + Spawn Limit) ----
# /editchar            -> lists every character in the DB, then waits for a reply
#                          formatted as "CharID | Event | SpawnLimit"
# /editchar <CharID>   -> shortcut straight to one character, reply is just
#                          "Event | SpawnLimit" (no need to retype the ID)
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
            f"🌟 Rarity: {char_doc.get('rarity', 'Unknown')}\n"
            f"🎪 Current Event: <code>{escape_html(cur_event)}</code>\n"
            f"🔁 Current Spawn Limit: <code>{limit_text}</code> (spawned <code>{char_doc.get('spawn_count', 0)}</code> times so far)\n\n"
            f"↩️ <b>Reply to this message</b> with:\n"
            f"<code>Event | SpawnLimit</code>\n"
            f"e.g. <code>Summer Festival | 5</code>\n"
            f"<i>Use - to leave a field unchanged. SpawnLimit 0 = infinite.</i>"
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
        "<code>CharID | Event | SpawnLimit</code>\n"
        "e.g. <code>BOD1234 | Summer Festival | 5</code>\n"
        "<i>Use - to leave a field unchanged. SpawnLimit 0 = infinite.</i>\n"
        "💡 Tip: <code>/editchar CharID</code> jumps straight to one character."
    )

    # Split into <=3500 char chunks so long lists don't hit Telegram's 4096 limit.
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
    # Only the final chunk (which carries the reply instructions) accepts a reply.
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
            return await event.reply("⚠️ Format: <code>Event | SpawnLimit</code>", parse_mode='html')
        new_event, new_limit_raw = parts[0], parts[1]
    else:
        if len(parts) < 3:
            return await event.reply("⚠️ Format: <code>CharID | Event | SpawnLimit</code>", parse_mode='html')
        char_id, new_event, new_limit_raw = parts[0].strip().upper(), parts[1], parts[2]

    char_doc = await characters_base_col.find_one({"char_id": char_id})
    if not char_doc:
        return await event.reply(f"❌ No character found with ID <code>{escape_html(char_id)}</code>.", parse_mode='html')

    update_fields = {}
    if new_event and new_event != "-":
        update_fields["event"] = new_event
    if new_limit_raw and new_limit_raw != "-":
        if not new_limit_raw.lstrip('-').isdigit():
            return await event.reply("⚠️ SpawnLimit must be a whole number (0 = infinite).")
        update_fields["spawn_limit"] = max(0, int(new_limit_raw))

    if not update_fields:
        return await event.reply("⚠️ Nothing to update — both fields were '-'.")

    await characters_base_col.update_one({"char_id": char_id}, {"$set": update_fields})
    updated_doc = await characters_base_col.find_one({"char_id": char_id})
    spawned = updated_doc.get("spawn_count", 0)
    limit = updated_doc.get("spawn_limit", 0)
    remaining_text = "♾️ Infinite" if not limit else f"{max(0, limit - spawned)} left ({spawned}/{limit})"

    await event.reply(
        f"✅ <b>{escape_html(updated_doc['name'])}</b> (<code>{char_id}</code>) updated!\n"
        f"🎪 Event: <code>{escape_html(updated_doc.get('event','General'))}</code>\n"
        f"🔁 Spawns remaining: <b>{remaining_text}</b>",
        parse_mode='html'
    )

# ---- SETCHAR (BULK EVENT + SPAWN LIMIT) ----
# Bulk-set Event + SpawnLimit for characters that are already in the database,
# 15, 30, or however many lines you like — all in a single message.
#   /setchar
#   BOD1234 | Lightning⚡ | 75
#   BOD5678 | Fire Festival | 30
#   BOD9012 | - | 0
# Format per line: CharID | Event | SpawnLimit
# Use "-" to leave a field unchanged. SpawnLimit 0 = infinite.
# Can also be used by replying /setchar to a message that already contains the lines.
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
            "<code>CharID | Event | SpawnLimit</code>\n\n"
            "<b>Example</b> (paste as many lines as you like, e.g. 15 or 30 at once):\n"
            "<code>BOD1234 | Lightning⚡ | 75\nBOD5678 | Fire Festival | 30\nBOD9012 | - | 0</code>\n\n"
            "<i>Use - to leave a field unchanged. SpawnLimit 0 = infinite.</i>",
            parse_mode='html'
        )

    lines = [ln.strip() for ln in raw_block.strip().splitlines() if ln.strip()]
    updated, skipped, failed = [], [], []

    for ln in lines:
        parts = [p.strip() for p in ln.split('|')]
        if len(parts) < 3:
            failed.append(f"<code>{escape_html(ln[:40])}</code> — needs 3 parts (CharID | Event | SpawnLimit)")
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
                failed.append(f"<code>{char_id}</code> — SpawnLimit must be a whole number")
                continue
            update_fields["spawn_limit"] = max(0, int(limit_raw))

        if not update_fields:
            skipped.append(char_id)
            continue

        await characters_base_col.update_one({"char_id": char_id}, {"$set": update_fields})
        updated.append(char_id)

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

# ---- HAICLEAN ----
@bot1.on(events.NewMessage(pattern=r'^/haiclean(?:@\w+)?$'))
async def haiclean_handler(event):
    if event.sender_id != OWNER_ID: return await event.reply("❌ This command is for the owner only.")
    progress_msg = await event.reply("⏳ Cleaning database...")
    try:
        await db["characters_base_data"].delete_many({})
        await db["users_catcher_data"].delete_many({})
        await db["marketplace_data"].delete_many({})
        await progress_msg.edit("🧹 All three databases have been completely wiped clean!")
    except Exception as e:
        await progress_msg.edit(f"❌ Error: {e}")

# ---- ADM ----
@bot1.on(events.NewMessage(pattern=r'^/adm(?:@\w+)?(?:\s+(.*))?'))
async def tag_admins(event):
    if event.is_private: return
    if not await check_admin(event.chat_id, event.sender_id): return
    text = event.pattern_match.group(1) or "Admins, Doraemon needs you! 🐱"
    
    try:
        admins = await bot1.get_participants(event.chat_id, filter=ChannelParticipantsAdmins)
        admin_mentions = []
        for a in admins:
            if a.bot or getattr(a, 'deleted', False): continue
            full_name = f"{a.first_name or ''} {a.last_name or ''}".strip() or "Admin"
            mention = f"<a href='tg://user?id={a.id}'><b>{escape_html(full_name)}</b></a>"
            admin_mentions.append(mention)
        compact_admin_list = "  •  ".join(admin_mentions)
        await event.respond(bq(f"<b>{escape_html(text)}</b>\n\n🐱 <b>Admins, please pay attention!</b>\n{compact_admin_list}"), parse_mode='html')
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
    status_msg = await event.respond(bq("🐱 <b>BROADCAST INITIATED</b>"), parse_mode='html')
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
            
    await status_msg.edit(bq(f"🐱 <b>BROADCAST COMPLETE</b>\n✅ <b>Success:</b> <code>{success}</code>\n❌ <b>Failed:</b> None"), parse_mode='html')

# ---- PROTECTION LOGIC (BIO/LINK) ----
@bot1.on(events.NewMessage())
async def protection_logic(event):
    if event.is_private: return
    if not event.text or event.sender_id == OWNER_ID or event.sender_id in bot_ids: return

    text, chat_id, sender_id = event.text.strip(), event.chat_id, event.sender_id
    sender = await event.get_sender()

    if re.search(r'/harrrem(@\w+)?', text) or re.search(r'/waaaifu(@\w+)?', text):
        try:
            mention = f"<a href='tg://user?id={sender_id}'>{escape_html(sender.first_name)}</a>"
            cmd_type = "harem" if "harem" in text.lower() else "waifu"
            async def delay_delete():
                await asyncio.sleep(2)
                try: await event.delete()
                except: pass
            asyncio.create_task(delay_delete())

            warn_msg = await event.respond(bq(f"<b>{cmd_type} command detected</b> {mention}\nDeleted to keep chat clean."), parse_mode='html')
            for i in range(2, 0, -1):
                await asyncio.sleep(1)
                await warn_msg.edit(bq(f"<b>{cmd_type} command detected</b> {mention}\nDeleting in {i} seconds..."), parse_mode='html')
            await warn_msg.delete()
            return 
        except: pass

    if text:
        text_lower = text.lower()
        BIO_KEYWORDS = ["bio", "b i o", "biolink", "bio-link", "b-i-o", "tg bio", "ဘိုင်အို", "ဘိုင်-အို", "ဘိုင်-o", "ဘီအိုင်အို", "b.i.o", "ဘိုင်အိုလင့်", "ဘိုင်အိုလင့်ခ်"]

        if any(key in text_lower for key in BIO_KEYWORDS) and not await check_admin(chat_id, sender_id):
            try:
                await event.delete()
                first_name = sender.first_name if sender else "User"
                mention = f"<a href='tg://user?id={sender_id}'>{escape_html(first_name)}</a>"
                await event.respond(bq(f"⚠️ <b>BIO ALERT</b>\n{mention}, bio links are not allowed.\n⚡ <b>Status:</b> <blockquote expandable>Message deleted 🗑️</blockquote>"), parse_mode='html')
                return 
            except Exception as e: print(f"Bio Filter Error: {e}")

    urls = re.findall(r'(https?://\S+|www\.\S+)', text)
    if urls and not (event.audio or event.voice):
        if await check_admin(chat_id, sender_id):
            try:
                f_msg = await bot1.forward_messages(event.chat_id, event.message)
                mention = f"<a href='tg://user?id={sender_id}'>{escape_html(sender.first_name)}</a>"
                await f_msg.reply(f"<b>Link posted by Admin {mention}</b>", parse_mode='html')
                await event.delete()
            except: pass
        else:
            try:
                await event.delete() 
                mention = f"<a href='tg://user?id={sender_id}'>{escape_html(sender.first_name)}</a>"
                await event.respond(bq(f"🔗 {mention}, no links allowed! Deleted."), parse_mode='html')
            except: pass

# ---- PLAY / MUSIC ----
play_blocks = {}

@bot1.on(events.NewMessage(pattern=r'^/play(?:@\w+)?(?:\s+(.*))?'))
async def play_response(event):
    if event.is_private: return
    song_title = event.pattern_match.group(1)
    if not song_title:
        return await event.reply(bq("🐱 Please provide a song title!\nExample: <code>/play Doraemon Theme</code>"), parse_mode='html')

    chat_id = event.chat_id
    user_id = event.sender_id
    sender = await event.get_sender()
    
    if sender and getattr(sender, 'username', None):
        mention = f"@{sender.username}"
    else:
        mention = f"<a href='tg://user?id={user_id}'>{escape_html(getattr(sender, 'first_name', 'User'))}</a>"

    session_key = (chat_id, user_id)
    if session_key not in play_blocks:
        play_blocks[session_key] = {"msg_id": None, "songs": []}

    current_song_entry = {"title": song_title.strip(), "by": mention}
    play_blocks[session_key]["songs"].append(current_song_entry)

    playlist_text = ""
    for index, s in enumerate(play_blocks[session_key]["songs"][-30:], start=1):
        playlist_text += f"{index} 💿 <code>/play {escape_html(s['title'])}</code> — {s['by']}\n"

    try:
        owner_entity = await bot1.get_entity(OWNER_ID)
        owner_fullname = f"{owner_entity.first_name or ''} {owner_entity.last_name or ''}".strip() or "Khant Thu Rain"
    except Exception:
        owner_fullname = "Khant Thu Rain"

    msg_template = (
        f"🐱 <b>Doraemon's Music System</b>\n"
        f"💿 <b>Now Playing:</b> <code>{escape_html(song_title.strip())}</code>\n"
        f"✨ <b>Requested By:</b> {mention}\n\n"
        f"💎 <b>Your Playlist (max 30):</b>\n{playlist_text}"
        f"💡 <i>Doraemon keeps your last 30 songs.</i>"
    )

    inline_buttons = [
        [Button.url("Join the Fan Club", "https://t.me/Comeback_BoD")],
        [Button.inline("💾 Save Playlist", data=f"save_pl_{user_id}")]
    ]

    try:
        last_msg_id = play_blocks[session_key]["msg_id"]
        if last_msg_id:
            try:
                await bot1.edit_message(entity=chat_id, message=last_msg_id, text=bq(msg_template), parse_mode='html', buttons=inline_buttons)
                try: await event.delete()
                except: pass
                return
            except Exception:
                last_msg_id = None

        if not last_msg_id:
            new_msg = await event.respond(bq(msg_template), parse_mode='html', buttons=inline_buttons)
            play_blocks[session_key]["msg_id"] = new_msg.id
            try: await event.delete()
            except: pass
    except Exception as e:
        print(f"Play System Error: {e}")

@bot1.on(events.CallbackQuery(pattern=r'^save_pl_(\d+)$'))
async def close_playlist_callback(event):
    chat_id = event.chat_id
    clicker_id = event.sender_id
    target_user_id = int(event.pattern_match.group(1))
    
    if clicker_id != target_user_id:
        return await event.answer("❌ This is not your playlist!", alert=True)
    
    session_key = (chat_id, target_user_id)
    try:
        msg = await event.get_message()
        current_text = msg.text or ""
        locked_buttons = [[Button.url("Join Our Group", "https://t.me/Comeback_BoD")], [Button.inline("🔒 Playlist Saved ✓", data="playlist_frozen_done")]]
        await event.edit(current_text, parse_mode='html', buttons=locked_buttons)
        if session_key in play_blocks: del play_blocks[session_key]
        await event.answer("💾 Playlist saved successfully!", alert=True)
    except Exception as e:
        print(f"Callback Error: {e}")
        await event.answer("❌ Error saving playlist!", alert=True)

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

# ---- TALK ENGINE TOGGLES ----
@bot1.on(events.NewMessage(pattern=r'^စကားဝင်ပြော$'))
async def turn_on_talking(event):
    if event.sender_id != OWNER_ID or event.chat_id != SPECIFIC_CONTROL_GROUP: return
    global GLOBAL_TALK
    GLOBAL_TALK = True
    await event.reply(bq("🐱 <b>TALK ENGINE: ACTIVATED</b>"), parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^မပြောနဲ့တော့$'))
async def turn_off_talking(event):
    if event.sender_id != OWNER_ID or event.chat_id != SPECIFIC_CONTROL_GROUP: return
    global GLOBAL_TALK
    GLOBAL_TALK = False
    await event.reply(bq("🔇 <b>TALK ENGINE: DEACTIVATED</b>"), parse_mode='html')

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
    await event.reply(bq(f"🐱 <b>REPLY ENGINE ACTIVATED</b>\nEvery <code>{REPLY_INTERVAL}</code> messages."), parse_mode='html')

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

@bot1.on(events.NewMessage(incoming=True))
async def global_talk_counter_handler(event):
    # ---- Old GLOBAL_TALK logic (keep for backward compatibility) ----
    global group_msg_counters, GLOBAL_TALK, GLOBAL_TALK_INTERVAL
    if not GLOBAL_TALK: return
    if event.is_private or not event.text or event.text.startswith(('/', '.', 'မှတ်', '@')): return
    sender = await event.get_sender()
    if sender and sender.bot: return

    chat_id = event.chat_id
    group_msg_counters[chat_id] = group_msg_counters.get(chat_id, 0) + 1
    if group_msg_counters[chat_id] >= GLOBAL_TALK_INTERVAL:
        group_msg_counters[chat_id] = 0
        asyncio.create_task(processed_background_reply(event, event.text.strip(), as_reply=False))
    
    # ---- NEW Talk logic (per-group /talkon system) ----
    # This runs independently of GLOBAL_TALK
    # Skip if message is from bot or command
    if event.sender_id in bot_ids or event.text.startswith('/'):
        return
    
    # Check if talk is enabled for this group
    talk_config = await groups_config_col.find_one({"chat_id": chat_id})
    if not talk_config or not talk_config.get("talk_enabled", False):
        return
    
    ratio = talk_config.get("talk_ratio", 5)
    # Get current counter
    current_counter = talk_config.get("talk_counter", 0) + 1
    await groups_config_col.update_one(
        {"chat_id": chat_id},
        {"$set": {"talk_counter": current_counter}}
    )
    
    if current_counter >= ratio:
        # Reset counter
        await groups_config_col.update_one(
            {"chat_id": chat_id},
            {"$set": {"talk_counter": 0}}
        )
        # Pick a random message from talk_col
        pipeline = [{"$sample": {"size": 1}}]
        cursor = talk_col.aggregate(pipeline)
        docs = await cursor.to_list(length=1)
        if docs:
            msg = docs[0].get("text") or docs[0].get("message") or docs[0].get("content")
            if msg:
                await bot1.send_message(chat_id, msg, parse_mode='html')
# ==========================================
# 💰 OWNER WALLET CONTROL
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/take(?:@\w+)?\s+(\d+)'))
async def take_mmk_telethon(event):
    if event.sender_id != OWNER_ID or not event.is_private: return
    amount = int(event.pattern_match.group(1))
    mention = await get_html_mention(event, OWNER_ID)
    try:
        await users_catcher_col.update_one(
            {"user_id": OWNER_ID},
            {"$inc": {"wallet_balance": amount}, "$setOnInsert": {"total_caught": 0, "harem": [], "fullname": mention}},
            upsert=True
        )
        await event.reply(f"💰 Added <code>{amount:,} MMK</code> to owner wallet.", parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ Error: <code>{e}</code>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/giveall(?:@\w+)?\s+(\d+)'))
async def give_all_players_mmk(event):
    if event.sender_id != OWNER_ID: return
    amount = int(event.pattern_match.group(1))
    try:
        result = await users_catcher_col.update_many({}, {"$inc": {"wallet_balance": amount}})
        await event.reply(f"🎁 <b>GLOBAL BOUNTY</b>\nDoraemon gave <code>{amount:,} MMK</code> to every player. Total: <code>{result.modified_count}</code>", parse_mode='html')
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
    await ensure_user_registered(target_user_id, target_mention)
    try:
        await users_catcher_col.update_one({"user_id": target_user_id}, {"$inc": {"wallet_balance": amount}, "$set": {"fullname": target_mention}})
        await event.reply(f"💸 {target_mention} received <code>{amount:,} MMK</code> from Doraemon’s pocket.", parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ Error: <code>{e}</code>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/takeall(?:@\w+)?\s+(\d+)'))
async def take_money_from_everyone(event):
    if event.sender_id != OWNER_ID: return
    amount = int(event.pattern_match.group(1))
    try:
        result = await users_catcher_col.update_many(
            {},
            [{"$set": {"wallet_balance": {"$max": [0, {"$subtract": ["$wallet_balance", amount]}]}}}]
        )
        await event.reply(f"🚨 <b>SYSTEM DEDUCTION</b>\nDoraemon deducted <code>{amount:,} MMK</code> from every player. Affected: <code>{result.modified_count}</code>", parse_mode='html')   
    except Exception as e:
        await event.reply(f"❌ Error: <code>{e}</code>", parse_mode='html')

# ==========================================
# 🛰️ FORCE SPAWN
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/(fspawn|haii)(?:@\w+)?$'))
async def force_spawn_by_owner(event):
    if event.sender_id != OWNER_ID: return
    await trigger_dynamic_spawn(event.chat_id)

# ==========================================
# 📢 AUTOMATIC SPAWN PROCESSOR
# ==========================================
@bot1.on(events.NewMessage(incoming=True))
async def global_message_counter_handler(event):
    if event.is_private or event.chat_id == SPECIFIC_CONTROL_GROUP: return
    chat_id = event.chat_id
    if chat_id in active_group_spawns: return

    group_config = await groups_config_col.find_one({"chat_id": chat_id})
    if group_config and "spawn_target" in group_config:
        spawn_target = group_config["spawn_target"]
    else:
        global_config = await groups_config_col.find_one({"chat_id": "global"})
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

async def trigger_dynamic_spawn(chat_id):
    if chat_id in active_group_spawns: return  
    try:
        characters_list = await characters_base_col.find().to_list(length=None)
        if not characters_list: return

        # Respect each character's spawn_limit (0/absent = unlimited)
        eligible_characters = []
        for char in characters_list:
            limit = char.get("spawn_limit", 0)
            if limit and limit > 0 and char.get("spawn_count", 0) >= limit:
                continue
            eligible_characters.append(char)
        if not eligible_characters:
            return

        # ⚠️ BUGFIX: rarity is stored as fancy unicode text (e.g. "👑 𝖫𝖤𝖦𝖤𝖭𝖣𝖠𝖱𝖸"),
        # so a plain "LEGENDARY" in char_rarity.upper() check NEVER matched and every
        # character silently fell through to the same default weight (20). That
        # flattened the intended rarity curve and made LEGENDARY/MYTHIC (meant to be
        # rare) spawn far more often than COMMON/RARE. classify_rarity() already knows
        # how to read the fancy text correctly, so we use that instead.
        RARITY_WEIGHTS = {"LEGENDARY": 1, "LIMITED": 3, "MYTHIC": 6, "EPIC": 15, "RARE": 30, "COMMON": 50}
        weights = []
        for char in eligible_characters:
            tier = classify_rarity(char.get("rarity", ""))
            weights.append(RARITY_WEIGHTS.get(tier, 20))

        chosen_char = random.choices(eligible_characters, weights=weights, k=1)[0]
        storage_msg = await bot1.get_messages(SPECIFIC_CONTROL_GROUP, ids=chosen_char["storage_msg_id"])
        if not storage_msg or not storage_msg.media: return
        
        rarity_display = chosen_char.get("rarity", "Unknown")
        event_name = chosen_char.get("event", "General")
        event_display = escape_html(event_name) if event_name and event_name != "General" else "➖"
        spawn_msg = await bot1.send_message(
            chat_id, 
            f"🔔 <b>Ring ring! A Character popped out of the Anywhere Door!</b>\n\n"
            f"🌟 <b>Rarity:</b> {rarity_display}\n"
            f"🎪 <b>Event:</b> {event_display}\n\n"
            f"<blockquote>🔎 Who is this? Reply with <code>/who</code> to reveal!</blockquote>",
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
            "spawn_time": time.time(),
            "revealed": False,
            "claimed": False
        }
    except Exception as e:
        print(f"Spawn Error Tracker: {e}")

# ==========================================
# 🕵️ /who
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/who(?:@\w+)?$'))
async def who_reveal_handler(event):
    if event.is_private: return
    chat_id = event.chat_id
    
    if chat_id not in active_group_spawns:
        return await event.reply(f"❌ <b>No gadget has spawned yet!</b>", parse_mode='html')
        
    spawn_data = active_group_spawns[chat_id]
    if time.time() - spawn_data["spawn_time"] > 60:
        if chat_id in active_group_spawns: del active_group_spawns[chat_id]
        return await event.reply(f"⏱️ <b>The gadget has vanished! Try again later.</b>", parse_mode='html')
        
    if not event.is_reply or event.reply_to_msg_id != spawn_data["spawn_msg_id"]:
        return await event.reply(f"⚠️ <b>Reply directly to the spawn message to reveal!</b>", parse_mode='html')
        
    try:
        event_display = spawn_data.get('event') or "➖"
        reveal_text = (
            f"🌟 <b>Name:</b> {spawn_data['name']}\n"
            f"🌟 <b>Rarity:</b> {spawn_data['rarity']}\n"
            f"🌐 <b>Category:</b> {spawn_data['category']}\n"
            f"🎪 <b>Event:</b> {event_display}\n\n"
            f"├─➩ <code>/catch {spawn_data['name']}</code>"
        )
        await event.reply(reveal_text, parse_mode='html')
    except Exception as e:
        print(f"Error in /who command: {e}")

# ==========================================
# ⚡ CATCH LOGIC
# ==========================================
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
            await event.reply("❌ <b>This gadget has already been caught!</b>", parse_mode='html')
        return False

    if time.time() - spawn_data["spawn_time"] > 300:
        if chat_id in active_group_spawns: del active_group_spawns[chat_id]
        if is_callback:
            await event.answer("⏱️ Too late!", alert=True)
        else:
            await event.reply("⏱️ <b>Too late! The gadget ran away.</b>", parse_mode='html')
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
        await ensure_user_registered(user_id, mention)
        
        try:
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
                        f"group_catches.{str(chat_id)}": 1
                    },
                    "$set": {"fullname": mention}
                },
                upsert=True
            )
            
            # 📈 spawn_count now tracks actual CATCHES (not mere appearances) so
            # /check's "Global Spawns" and each character's SpawnLimit both reflect
            # how many times it has really been caught.
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

            newly_earned = await check_and_award_achievements(user_id)

            raw_event_name = spawn_data.get('event')
            event_display = escape_html(raw_event_name) if raw_event_name and raw_event_name != "General" else "➖"
            success_msg = (
                f"🎉 <b>Mission Secured!</b>\n\n"
                f"👤 <b>Hunter:</b> {mention}\n"
                f"🃏 <b>Character:</b> <b>{escape_html(character_name)}</b>\n"
                f"🆔 <b>Asset ID:</b> <code>{spawn_data['char_id']}</code>\n"
                f"🌟 <b>Rarity Class:</b> {spawn_data['rarity']}\n"
                f"🎪 <b>Event:</b> {event_display}\n"
                f"🪙 <b>Bounty Added:</b> <b>+{spawn_data['value']} MMK</b>\n\n"
                f"<blockquote>📦 Check your collection anytime with <code>/hai</code></blockquote>"
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
            
@bot1.on(events.NewMessage(pattern=r'^/catch(?:@\w+)?\s+(.*)$'))
async def catch_handler(event):
    if event.is_private: return
    chat_id = event.chat_id
    user_id = event.sender_id
    catch_name = event.pattern_match.group(1).strip()
    
    if chat_id not in active_group_spawns:
        return await event.reply(f"🛸 <b>Nothing to catch here!</b>", parse_mode='html')
        
    spawn_data = active_group_spawns[chat_id]
    if spawn_data["claimed"]:
        return await event.reply(f"❌ <b>Already caught!</b>", parse_mode='html')
        
    if time.time() - spawn_data["spawn_time"] > 300:
        if chat_id in active_group_spawns: del active_group_spawns[chat_id]
        return await event.reply(f"⏱️ <b>Too late! The gadget ran away.</b>", parse_mode='html')

    if normalize_name(catch_name) != normalize_name(spawn_data["name"]):
        return await event.reply(f"❌ <b>Wrong name! Use the exact name shown in /who.</b>", parse_mode='html')
    
    temp_msg = await event.reply("👾")
    await asyncio.sleep(2.5)
    await perform_catch(chat_id, user_id, spawn_data, event, reply_to_msg=event.id, is_callback=False, temp_msg_id=temp_msg.id)

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
        buttons.append([Button.inline(label, data=f"hfilter_{num}")])
    
    clear_label = "🔓 Clear Filter" if current_filter else "🔒 No Filter"
    buttons.append([Button.inline(clear_label, data="hfilter_clear")])
    buttons.append([Button.inline("🔙 Back", data="nav_back_home")])
    
    await event.reply(
        f"🎯 <b>Select Rarity Filter for /hai</b>\n"
        f"Current: {current_filter if current_filter else 'None (Show All)'}\n\n"
        f"Tap a rarity to filter your vault, or clear to show all.",
        buttons=buttons,
        parse_mode='html'
    )

@bot1.on(events.CallbackQuery(pattern=r'^hfilter_(\d+|clear)$'))
async def rarity_filter_callback(event):
    user_id = event.sender_id
    data = event.data.decode('utf-8')
    action = data.split('_')[1]
    
    if action == "clear":
        await users_catcher_col.update_one(
            {"user_id": user_id},
            {"$set": {"rarity_filter": None}},
            upsert=True
        )
        await event.answer("🔓 Filter cleared! All rarities will be shown.")
        await event.edit("✅ Filter cleared. Use /hai to see all cards.")
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
    await event.edit(f"✅ Filter set to {rarity_name}. Use /hai to see your filtered vault.")

# ==========================================
# 🎒 INVENTORY / HAI – WITH VIEW OTHERS VAULT
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/hai(?:@\w+)?(?:\s+(.*))?$'))
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
                return await event.reply("❌ Invalid format. Use <code>/hai @username</code> or reply to a message.")

    if target_user_id is None:
        target_user_id = user_id

    try:
        target_entity = await bot1.get_entity(target_user_id)
        target_name = target_entity.first_name or "User"
    except Exception:
        target_name = "User"
    await ensure_user_registered(target_user_id, target_name)

    await send_paginated_harem(bot1, event.chat_id, target_user_id, page=1, viewer_id=user_id)

@bot1.on(events.NewMessage(pattern=r'^/fav(?:@\w+)?\s+([a-zA-Z0-9_]+)$'))
async def set_favorite_card(event):
    user_id = event.sender_id
    char_id = event.pattern_match.group(1).upper()
    card = await characters_base_col.find_one({"char_id": char_id})
    if not card: return await event.reply(f"❌ <b>Card not found!</b>", parse_mode='html')
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    user_harem = user_doc.get("harem", []) if user_doc else []
    owns_card = any(isinstance(x, dict) and x.get("char_id") == char_id for x in user_harem)
    if not owns_card: return await event.reply(f"❌ <b>You don’t own this card!</b>", parse_mode='html')
    await users_catcher_col.update_one({"user_id": user_id}, {"$set": {"fav_card": char_id}})
    await event.reply(f"⭐️ <b>{escape_html(card['name'])}</b> ({char_id}) is now your favourite card!", parse_mode='html')

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
    
    def get_rarity_weight(rarity_str):
        tier = classify_rarity(rarity_str)
        return {"LEGENDARY": 6, "LIMITED": 5, "MYTHIC": 4, "EPIC": 3, "RARE": 2, "COMMON": 1}.get(tier, 0)

    db_chars = sorted(db_chars, key=lambda x: (-get_rarity_weight(x.get("rarity", "")), x.get("name", "").lower()))
    
    lines = []
    for card in db_chars:
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
        
        rarity_text = card.get("rarity", "")
        emoji = rarity_text.split()[0] if rarity_text else "•"
        series_name = card.get("category", "Unknown Series")
        
        escaped_name = escape_html(card['name'])
        escaped_series = escape_html(series_name)
        lines.append(f" {emoji} <b>{escaped_name}</b> (<i>{escaped_series}</i>) — [<code>{cid}</code>] {status_str}")

    per_page = 7
    total_pages = (len(lines) + per_page - 1) // per_page
    if page < 1: page = 1
    if page > total_pages: page = total_pages
    
    start_idx = (page - 1) * per_page
    page_lines = lines[start_idx:start_idx + per_page]
    
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
    output_text += f"📑 <b>Page</b> <code>{page}/{total_pages}</code> · <b>Unique</b> <code>{len(lines)}</code> · <b>Total</b> <code>{len(filtered_harem)}</code>\n\n"
    
    for l in page_lines:
        output_text += l + "\n"
    
    output_text += "\n"
    if is_own_vault:
        output_text += f"<blockquote>💡 Use <code>/fav [BODxxxx]</code> to set a favourite card.</blockquote>"
    if rarity_filter:
        output_text += f"\n<i>🔍 Filter active — use /hmode to change or clear.</i>"
    
    buttons = []

    total_cards = len(filtered_harem)
    buttons.append([Button.switch_inline(f"👀 Take A Look ({total_cards})", query=f"harem.{user_id}", same_peer=True)])

    nav_buttons = []
    if page > 1:
        nav_buttons.append(Button.inline("⬅️ Prev", data=f"hai_{page-1}_{user_id}_{viewer_id}"))
    if page < total_pages:
        nav_buttons.append(Button.inline("Next ➡️", data=f"hai_{page+1}_{user_id}_{viewer_id}"))
    if nav_buttons:
        buttons.append(nav_buttons)

    if is_own_vault:
        if rarity_filter:
            buttons.append([Button.inline("🔓 Clear Filter", data=f"hfilter_clear")])
        buttons.append([Button.inline("🎯 Change Rarity Filter", data="nav_hmode")])

    if not buttons:
        buttons = None
    
    fav_card_id = user_doc.get("fav_card")
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
                              
@bot1.on(events.InlineQuery)
async def harem_inline_gallery(event):
    query_text = (event.text or "").strip()
    builder = event.builder

    if not query_text.startswith("harem."):
        return await event.answer([], cache_time=0)

    try:
        target_user_id = int(query_text.split(".", 1)[1])
    except (ValueError, IndexError):
        return await event.answer([], cache_time=0)

    # 👀 Take A Look is read-only browsing — anyone can inline-query anyone's
    # vault this way, same as paging through /hai. No ownership check here.
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

    def get_rarity_weight(rarity_str):
        tier = classify_rarity(rarity_str)
        return {"LEGENDARY": 6, "LIMITED": 5, "MYTHIC": 4, "EPIC": 3, "RARE": 2, "COMMON": 1}.get(tier, 0)

    db_chars = sorted(
        db_chars,
        key=lambda x: (-get_rarity_weight(x.get("rarity", "")), x.get("name", "").lower())
    )

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
                f"🆔 <code>{cid}</code>\n"
                f"🌐 <b>Category:</b> {escape_html(card.get('category', ''))}\n"
                f"🌟 <b>Rarity:</b> {card.get('rarity', '')}\n"
                f"📦 <b>You Own:</b> x{qty}"
            )
            if storage_msg.photo:
                results.append(builder.photo(
                    file=storage_msg.media,
                    id=cid,
                    text=caption,
                    parse_mode='html'
                ))
            else:
                results.append(builder.document(
                    file=storage_msg.media,
                    title=card['name'],
                    type='video' if storage_msg.video else 'document',
                    id=cid,
                    text=caption,
                    parse_mode='html'
                ))
        except Exception as e:
            print(f"Inline gallery error for {cid}: {e}")
            continue

    await event.answer(results, cache_time=0)

@bot1.on(events.CallbackQuery(pattern=r'^view_all_cards_(\d+)$'))
async def view_all_cards_callback(event):
    # 👀 Take A Look is a read-only gallery — anyone in the chat can tap it to
    # browse this vault's cards, same as paging through /hai. Ownership only
    # matters for the vault's own edit actions (fav/filter), not for looking.
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
        emoji = card_data.get("rarity", "🃏")[0] if card_data.get("rarity") else "🃏"
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
    
    buttons.append([Button.inline("🔙 Back to Vault", data=f"hai_1_{user_id}")])
    
    await event.edit(gallery_text, parse_mode='html', buttons=buttons)

@bot1.on(events.CallbackQuery(pattern=r'^gallery_page_(\d+)_(\d+)$'))
async def gallery_page_callback(event):
    # Read-only gallery paging — open to any viewer, same as view_all_cards_callback.
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
    # Read-only card detail — open to any viewer. Always shows whose vault this
    # card belongs to, since the viewer tapping it may not be the vault owner.
    char_id = event.pattern_match.group(1)
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
        f"🃏 <b>Card Details</b>\n"
        f"👤 <b>Vault Owner:</b> {owner_mention}\n\n"
        f"🌟 <b>Name:</b> <code>{escape_html(card['name'])}</code>\n"
        f"🆔 <b>ID:</b> <code>{card['char_id']}</code>\n"
        f"🌐 <b>Category:</b> <code>{escape_html(card['category'])}</code>\n"
        f"🌟 <b>Rarity:</b> {card['rarity']}\n"
        f"📦 <b>Owned:</b> <code>{count} copies</code>\n"
        f"💎 <b>Value:</b> <code>{card['currency_value']} MMK</code>\n\n"
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

# ==========================================
# 🔗 UNIFIED CALLBACK HANDLER
# ==========================================
@bot1.on(events.CallbackQuery)
async def unified_callback_handler(event):
    if not event.data: return
    try: data_str = event.data.decode('utf-8')
    except: return

    data_parts = data_str.split('_')
    action_type = data_parts[0]

    if action_type == "hai":
        page = int(data_parts[1])
        target_user_id = int(data_parts[2])
        # Anyone can browse pages of a vault (view-only). is_own_vault inside
        # send_paginated_harem still gates filter/edit buttons to the true owner.
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

# ==========================================
# 📊 PROFILE
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/(profile|myinfo)(?:@\w+)?$'))
async def profile_handler(event):
    user_id = event.sender_id
    mention = await get_html_mention(event, user_id)
    await ensure_user_registered(user_id, mention)
    user_doc = await users_catcher_col.find_one({"user_id": user_id})

    total_caught = user_doc.get("total_caught", 0) if user_doc else 0
    balance = user_doc.get("wallet_balance", 0) if user_doc else 0
    streak = user_doc.get("daily_streak", 0) if user_doc else 0
    referrals = user_doc.get("referral_count", 0) if user_doc else 0
    badge_count = len(user_doc.get("achievements", [])) if user_doc else 0
    raw_harem = user_doc.get("harem", []) if user_doc else []
    fav_card_id = user_doc.get("fav_card") if user_doc else None

    tier_counts = {t: 0 for t in RARITY_TIERS}
    market_count = 0
    unique_ids = set()
    for item in raw_harem:
        if isinstance(item, dict):
            if item.get("status") == "market":
                market_count += 1
            if "char_id" in item:
                unique_ids.add(item["char_id"])
            tier = classify_rarity(item.get("rarity", ""))
            if tier in tier_counts:
                tier_counts[tier] += 1

    base_total = await characters_base_col.count_documents({})
    unique_owned = len(unique_ids)
    rank = await users_catcher_col.count_documents({"total_caught": {"$gt": total_caught}}) + 1

    fav_name = None
    if fav_card_id:
        fav_doc = await characters_base_col.find_one({"char_id": fav_card_id})
        if fav_doc:
            fav_name = fav_doc.get("name")

    # ---- 🪪 Headline card — short, travels together with the profile photo ----
    headline_lines = [
        "🪪 <b>HUNTER ID CARD</b>",
        f"{mention}",
        f"🆔 <code>{user_id}</code>",
        "",
        f"🏆 <b>Rank</b> <code>#{rank}</code>   🪙 <code>{balance:,} MMK</code>",
        f"🔥 <b>Streak</b> <code>{streak}d</code>   🤝 <code>{referrals}</code> referrals",
    ]
    if fav_name:
        headline_lines.append(f"⭐ <b>Favourite</b> <code>{escape_html(fav_name)}</code> (<code>{fav_card_id}</code>)")
    headline_text = "\n".join(headline_lines)

    # ---- 📊 Full breakdown — sent as its own message, grouped with blockquotes
    # instead of the old dash-line dividers ----
    detail_lines = [
        f"🎒 <b>Total Caught:</b> <code>{total_caught}</code>   🛒 <b>On Market:</b> <code>{market_count}</code>",
        "",
        "🧬 <b>Collection Progress</b> <i>(unique characters owned)</i>",
        f"<code>{build_progress_bar(unique_owned, base_total)}</code>",
        f"<code>{unique_owned}/{base_total}</code> unique out of the full database",
        "",
        "📊 <b>VAULT BREAKDOWN</b>",
    ]
    tier_block = []
    for tier in RARITY_TIERS:
        cnt = tier_counts[tier]
        if cnt == 0 and total_caught == 0:
            continue
        tier_block.append(f"{RARITY_EMOJI[tier]} <b>{tier}</b> — <code>{cnt}</code>\n<code>{build_progress_bar(cnt, total_caught)}</code>")
    if tier_block:
        detail_lines.append(f"<blockquote>{chr(10).join(tier_block)}</blockquote>")
    detail_lines += [
        "",
        f"🏅 <b>Badges:</b> <code>{badge_count}/{len(ACHIEVEMENTS)}</code>",
        f"<code>{build_progress_bar(badge_count, len(ACHIEVEMENTS))}</code>",
        "<i>✨ Use /achievements to see full details</i>",
    ]
    detail_text = "\n".join(detail_lines)

    # ---- 📸 Pull the user's real Telegram profile photo, if they have one ----
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
            sent = await bot1.send_file(
                event.chat_id, photo_stream, caption=headline_text,
                parse_mode='html', reply_to=event.id
            )
            await bot1.send_message(event.chat_id, detail_text, parse_mode='html', reply_to=sent.id)
            return
        except Exception:
            pass  # fall through to a text-only card if the photo send fails

    await event.reply(headline_text + "\n\n" + detail_text, parse_mode='html')

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
    await ensure_user_registered(user_id, mention)

    await check_and_award_achievements(user_id)

    await send_achievements_page(bot1, event.chat_id, user_id, mention, page=1)

# ==========================================
# 🔗 REFERRAL
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/referral(?:@\w+)?$'))
async def referral_info_handler(event):
    user_id = event.sender_id
    mention = await get_html_mention(event, user_id)
    await ensure_user_registered(user_id, mention)
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    ref_count = user_doc.get("referral_count", 0) if user_doc else 0
    bot_me = await bot1.get_me()
    ref_link = f"https://t.me/{bot_me.username}?start=ref_{user_id}"
    msg = f"🔗 <b>YOUR REFERRAL LINK</b>\n⚡ ━━━━━━━━━━━━━━━ ⚡\nShare this link! You get <code>+2000 MMK</code>, friend gets <code>+1000 MMK</code>.\n\n🔗 <code>{ref_link}</code>\n\n👥 <b>Total Invited:</b> <code>{ref_count} Friends</code>"
    share_url = f"https://t.me/share/url?url={ref_link}&text=Join%20Doraemon%27s%20Bot%20now!"
    await event.reply(msg, parse_mode='html', buttons=[[Button.url("📤 Share Link", share_url)]])

# ==========================================
# 🔍 CHECK CHARACTER
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/check(?:@\w+)?\s+([a-zA-Z0-9_]+)$'))
async def check_character_id_handler(event):
    char_id = event.pattern_match.group(1).upper()
    character = await characters_base_col.find_one({"char_id": char_id})
    if not character: return await event.reply(f"❌ <b>Character ID not found!</b>", parse_mode='html')
    spawn_count = character.get("spawn_count", 0)
    pipeline = [
        {"$match": {"harem.char_id": char_id}},
        {"$project": {"fullname": "$fullname", "user_id": "$user_id", "count": {"$size": {"$filter": {"input": "$harem", "as": "item", "cond": {"$eq": ["$$item.char_id", char_id]}}}}}},
        {"$sort": {"count": -1}}, {"$limit": 10}
    ]
    top_hunters = await users_catcher_col.aggregate(pipeline).to_list(length=10)
    leaderboard_str = ""
    for idx, u in enumerate(top_hunters, start=1):
        uname = u.get("fullname") or f"Agent {u['user_id']}"
        leaderboard_str += f" <b>{idx}.</b> {uname} — <code>x{u['count']}</code>\n"
    try:
        storage_msg = await bot1.get_messages(SPECIFIC_CONTROL_GROUP, ids=character["storage_msg_id"])
        media_file = storage_msg.media if storage_msg else None
    except: media_file = None
    limit = character.get("spawn_limit", 0)
    spawns_line = f"<code>{spawn_count}</code>" if not limit else f"<code>{spawn_count}/{limit}</code> (<code>{max(0, limit - spawn_count)} left</code>)"
    info_text = (
        f"🎴 <b>CHARACTER ANALYTICS</b>\n\n"
        f"🆔 <b>Asset ID:</b> <code>{character['char_id']}</code>\n"
        f"👤 <b>Name:</b> <code>{character['name']}</code>\n"
        f"🌐 <b>Category:</b> <code>{character['category']}</code>\n"
        f"🌟 <b>Rarity:</b> {character['rarity']}\n"
        f"🎪 <b>Event:</b> <code>{escape_html(character.get('event', 'General'))}</code>\n"
        f"📈 <b>Global Spawns:</b> {spawns_line}\n\n"
        f"🏆 <b>TOP 10 COLLECTORS</b>\n"
        f"<blockquote>{leaderboard_str if leaderboard_str else 'No collectors yet.'}</blockquote>"
    )
    await event.reply(info_text, parse_mode='html', file=media_file)

# ==========================================
# 🛒 MARKETPLACE
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/sell(?:@\w+)?\s+([a-zA-Z0-9_]+)\s+(\d+)$'))
async def sell_market_handler(event):
    user_id = event.sender_id
    char_id = event.pattern_match.group(1).upper()
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

@bot1.on(events.NewMessage(pattern=r'^/buy(?:@\w+)?\s+([a-zA-Z0-9_]+)(?:\s+(\d+))?$'))
async def buy_market_handler(event):
    buyer_id = event.sender_id
    char_id = event.pattern_match.group(1).upper()
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
        await marketplace_col.delete_one({"listing_id": listing["listing_id"]})
        return await event.reply(f"🎉 <b>Purchase successful!</b>", parse_mode='html')

    listings = await marketplace_col.find({"char_id": char_id}).sort("price", 1).to_list(length=10)
    if not listings: return await event.reply(f"❌ <b>No listings for this card.</b>", parse_mode='html')
    market_text = f"🛒 <b>LISTINGS FOR {char_id}</b>\n⚡ ━━━━ ⚡\n\n"
    buttons = []
    for item in listings:
        market_text += f"👤 <b>Seller:</b> {item['seller_name']} [<code>{item['seller_id']}</code>]\n💰 <b>Price:</b> <code>{item['price']} MMK</code>\n\n"
        buttons.append([Button.inline(f"Buy for {item['price']} MMK", data=f"mktbuy_{item['listing_id']}")])
    await event.reply(market_text, parse_mode='html', buttons=buttons)

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
        user_display = u.get('fullname') or f"User {u['user_id']}"
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
        user_display = u.get('fullname') or f"User {u['user_id']}"
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
# 🎁 GIFT
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/gift(?:@\w+)?\s+(.+)$'))
async def gift_asset_handler(event):
    if not event.is_reply: return await event.reply(f"❌ <b>Reply to the user you want to gift.</b>", parse_mode='html')
    char_id = event.pattern_match.group(1).strip().upper()
    sender_id = event.sender_id
    reply_msg = await event.get_reply_message()
    receiver_id = reply_msg.sender_id
    if sender_id == receiver_id: return await event.reply(f"❌ <b>Can't gift to yourself!</b>", parse_mode='html')
    sender_doc = await users_catcher_col.find_one({"user_id": sender_id})
    sender_harem = sender_doc.get("harem", []) if sender_doc else []
    char_item = next((x for x in sender_harem if isinstance(x, dict) and x.get("char_id") == char_id and x.get("status") != "market"), None)
    if not char_item: return await event.reply(f"❌ <b>You don’t have this card.</b>", parse_mode='html')
    sender_harem.remove(char_item)
    await users_catcher_col.update_one({"user_id": sender_id}, {"$set": {"harem": sender_harem}})
    r_mention = await get_html_mention(event, receiver_id)
    await users_catcher_col.update_one(
        {"user_id": receiver_id},
        {"$push": {"harem": {"char_id": char_id, "caught_date": time.time(), "rarity": char_item.get("rarity", "Unknown"), "status": "vault"}}, "$inc": {"total_caught": 1}, "$set": {"fullname": r_mention}},
        upsert=True
    )
    await event.reply(f"🎁 <b>GIFT SUCCESSFUL</b>\n<blockquote>Card <code>{char_id}</code> sent to {r_mention}!</blockquote>", parse_mode='html')

# ==========================================
# 🤝 TRADE
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/trade(?:@\w+)?\s+([a-zA-Z0-9_]+)\s+([a-zA-Z0-9_]+)$'))
async def trade_proposal_handler(event):
    if not event.is_reply: return await event.reply(f"❌ <b>Reply to the user you want to trade with.</b>", parse_mode='html')
    my_char_id = event.pattern_match.group(1).upper()
    their_char_id = event.pattern_match.group(2).upper()
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
            f"🎰 Please specify your bet amount!\nExample: <code>/slot 100</code>"
        )
    
    bet = int(bet_str)
    if bet <= 0:
        return await event.reply("💕 <b>Bet must be at least 1 MMK!</b>", parse_mode='html')
    
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    balance = user_doc.get("wallet_balance", 0) if user_doc else 0
    if balance < bet:
        return await event.reply(f"💕 <b>Oh no! You only have {balance} MMK! 🐱</b>", parse_mode='html')
    
    await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": -bet}})
    mention = await get_html_mention(event, user_id)
    
    symbols = ["🐯", "🦁", "🐃", "🦓", "🦀", "🐄", "🦅"]
    
    loading_msg = await event.reply("🎰 🔄 🔄 🔄 🔄 🔄 🔄")
    
    for spin in range(3):
        await asyncio.sleep(0.8)
        shuffled = random.sample(symbols, 7)
        display = " | ".join(shuffled)
        await bot1.edit_message(
            event.chat_id,
            loading_msg,
            f"🎰 {display}"
        )
    
    res_syms = random.choices(symbols, k=7)
    res_str = " | ".join(res_syms)
    
    counts = Counter(res_syms)
    most_common_sym, max_count = counts.most_common(1)[0]
    win_amount = 0
    
    if max_count >= 7:
        win_amount = bet * 77 if most_common_sym == "🐃" else bet * 50
        status_text = f"🎉🎊 <b>GOD-TIER JACKPOT!!!</b> 🎊🎉\n{most_common_sym} x{max_count} = {win_amount:,} MMK! 😱"
        celebration = "🎉✨🌟💎🎊🎉✨🌟💎🎊"
    elif max_count == 6:
        win_amount = bet * 25
        status_text = f"🔥🔥 <b>MEGA MULTI-MATCH!!!</b> 🔥🔥\n{most_common_sym} x{max_count} = {win_amount:,} MMK! 😍"
        celebration = "🎉✨🌟💎🎊"
    elif max_count == 5:
        win_amount = bet * 12
        status_text = f"⚡⚡ <b>SUPER MULTI-MATCH!!</b> ⚡⚡\n{most_common_sym} x{max_count} = {win_amount:,} MMK! 🔥"
        celebration = "🎉🌟✨💎"
    elif max_count == 4:
        win_amount = bet * 5
        status_text = f"😲😲 <b>QUADRA SYNC MATCH!</b> 😲😲\n{most_common_sym} x{max_count} = {win_amount:,} MMK! 👍"
        celebration = "🎉✨🌟"
    elif max_count == 3:
        win_amount = int(bet * 2.5)
        status_text = f"🤩 <b>TRIPLE COMBINATION!</b> 🤩\n{most_common_sym} x{max_count} = {win_amount:,} MMK! 💪"
        celebration = "🎉✨"
    elif max_count == 2:
        win_amount = int(bet * 0.5)
        status_text = f"😊 <b>PAIR MATCH!</b> 😊\n{most_common_sym} x{max_count} = {win_amount:,} MMK! 🎈"
        celebration = "✨"
    else:
        status_text = f"😅 <b>No win this time!</b> 😅\nBetter luck next time! 🐱"
        celebration = "💔"
    
    if win_amount > 0:
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": win_amount}})
    
    final_text = (
        f"🎰 <b>DORAEMON'S SLOT MACHINE</b> 🎰\n"
        f"{celebration}\n"
        f"👤 <b>Player:</b> {mention}\n"
        f"💵 <b>Bet:</b> <code>{bet:,} MMK</code>\n\n"
        f"🎯 <b>RESULT:</b>\n"
        f"<code>{res_str}</code>\n\n"
        f"{status_text}\n"
        f"{celebration}\n"
        f"🐱 <i>Doraemon sends you luck!</i>"
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

    # ✨ Deal the cards one by one for a bit of suspense before the reveal
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
    await event.reply(f"🃏 <b>HI-LO GAME</b>\n🐱 DORAEMON’S CASINO\n💵 Bet: <code>{bet_amount:,} MMK</code>\n🎴 Current card: ✨ <b>[ {base_display} ]</b> ✨\n\n<blockquote>Higher or lower? Choose!</blockquote>", buttons=buttons, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/daily(?:@\w+)?$'))
async def daily_bounty_handler(event):
    user_id = event.sender_id
    mention = await get_html_mention(event, user_id)
    await ensure_user_registered(user_id, mention)
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
    await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": bonus}, "$set": {"last_daily": now, "daily_streak": new_streak, "fullname": mention}})
    newly_earned = await check_and_award_achievements(user_id)
    reply_text = f"🎁 {mention} <b>Daily bonus claimed! <code>+{bonus} MMK</code></b>\n🔥 <b>{new_streak} day streak</b> — Streak Bonus: <code>+{streak_bonus} MMK</code>"
    reply_text += format_achievement_unlocks(newly_earned)
    await event.reply(reply_text, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/richest(?:@\w+)?$'))
async def system_richest_leaderboard(event):
    cursor = users_catcher_col.find({"wallet_balance": {"$gt": 0}}).sort("wallet_balance", -1).limit(10)
    richest_users = await cursor.to_list(length=10)
    if not richest_users: return await event.reply(f"🏆 <b>No wealthy players yet!</b>", parse_mode='html')
    msg = f"💰 <b>TOP 10 RICHEST PLAYERS</b>\n🐱 DORAEMON’S ECONOMY\n"
    for i, u in enumerate(richest_users):
        bal = u.get("wallet_balance", 0)
        user_display = u.get('fullname') or f"User {u['user_id']}"
        msg += f"<b>{i+1}.</b> {user_display} — <code>{bal:,} MMK</code> 🪙\n"
    await event.reply(msg, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/scrap(?:@\w+)?\s+([a-zA-Z0-9_]+)$'))
async def scrap_card_handler(event):
    user_id = event.sender_id
    char_id = event.pattern_match.group(1).upper()
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

@bot1.on(events.NewMessage(pattern=r'^/pay(?:@\w+)?\s+(\d+)$'))
async def peer_to_peer_payment(event):
    if not event.is_reply: return await event.reply("⚠️ <b>Reply to the user you want to pay.</b>", parse_mode='html')
    amount = int(event.pattern_match.group(1))
    sender_id = event.sender_id
    reply_msg = await event.get_reply_message()
    receiver_id = reply_msg.sender_id
    if sender_id == receiver_id: return await event.reply("❌ <b>You can’t pay yourself.</b>", parse_mode='html')
    s_doc = await users_catcher_col.find_one({"user_id": sender_id})
    if not s_doc or s_doc.get("wallet_balance", 0) < amount: return await event.reply("❌ <b>Insufficient balance.</b>", parse_mode='html')
    r_mention = await get_html_mention(event, receiver_id)
    s_mention = await get_html_mention(event, sender_id)
    await ensure_user_registered(receiver_id, r_mention)
    await users_catcher_col.update_one({"user_id": sender_id}, {"$inc": {"wallet_balance": -amount}})
    await users_catcher_col.update_one({"user_id": receiver_id}, {"$inc": {"wallet_balance": amount}})
    await event.reply(f"💸 {s_mention} sent <code>{amount:,} MMK</code> to {r_mention}.", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/hunt(?:@\w+)?$'))
async def text_adventure_hunt_handler(event):
    user_id = event.sender_id
    mention = await get_html_mention(event, user_id)
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    current_time = time.time()
    last_hunt = user_doc.get("hunt_cooldown", 0) if user_doc else 0
    if current_time - last_hunt < 180:
        return await event.reply(f"⏳ {mention} <b>You must wait {int(180 - (current_time - last_hunt))}s.</b>", parse_mode='html')
    earned = random.randint(200, 80000)
    events_pool = [f"🌲 {mention} <b>found treasure! (+<code>{earned} MMK</code>)</b>", f"⚔️ {mention} <b>defeated a rival! (+<code>{earned} MMK</code>)</b>", f"🌌 {mention} <b>collected quantum points! (+<code>{earned} MMK</code>)</b>"]
    await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": earned}, "$set": {"hunt_cooldown": current_time, "fullname": mention}}, upsert=True)
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
        f"🏪 <b>MARKETPLACE CATALOG</b>\n🐱 DORAEMON’S AUCTION\n"
        f"📑 <b>Page:</b> <code>{page}/{total_pages}</code> | <b>Total Listings:</b> <code>{len(all_listings)}</code>\n\n"
    )
    for item in page_listings:
        msg += f"📦 <b>Card:</b> <code>{item['char_name']}</code> [<code>{item['char_id']}</code>]\n ├─ 💰 <b>Price:</b> <code>{item['price']} MMK</code>\n └─ 👤 <b>Seller:</b> {item['seller_name']}\n\n"

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

    pipeline = [{"$group": {"_id": None, "total_cash": {"$sum": "$wallet_balance"}, "total_cards": {"$sum": {"$size": "$harem"}}, "players_count": {"$sum": 1}}}]
    econ_data = await users_catcher_col.aggregate(pipeline).to_list(length=1)
    metrics = econ_data[0] if econ_data else {"total_cash": 0, "total_cards": 0, "players_count": 0}

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
        for item in u.get("harem", []):
            if isinstance(item, dict) and "char_id" in item:
                total_catches += 1
                caught_unique_ids.add(item["char_id"])
                tier = classify_rarity(item.get("rarity", ""))
                if tier in catch_tier_counts:
                    catch_tier_counts[tier] += 1

    discovery_count = len(caught_unique_ids)

    msg = "📊 <b>GLOBAL ECONOMY & COLLECTION STATS</b>\n⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n\n"
    msg += f"👥 <b>Active Agents:</b> <code>{metrics['players_count']} Players</code>\n"
    msg += f"🪙 <b>Total Money in Circulation:</b> <code>{metrics['total_cash']:,} MMK</code>\n"

    msg += f"\n🗄️ <b>CHARACTER DATABASE</b> (<code>/addchar</code> total: <code>{base_total}</code>)\n"
    for tier in RARITY_TIERS:
        cnt = base_tier_counts[tier]
        msg += f"{RARITY_EMOJI[tier]} <b>{tier}</b> — <code>{cnt}</code>\n<code>{build_progress_bar(cnt, base_total)}</code>\n"

    msg += f"\n🃏 <b>TOTAL CATCHES</b> (all players combined: <code>{total_catches}</code>)\n"
    for tier in RARITY_TIERS:
        cnt = catch_tier_counts[tier]
        msg += f"{RARITY_EMOJI[tier]} <b>{tier}</b> — <code>{cnt}</code>\n<code>{build_progress_bar(cnt, total_catches)}</code>\n"

    msg += f"\n🔎 <b>DISCOVERY RATE</b> (unique characters caught at least once)\n"
    msg += f"<code>{build_progress_bar(discovery_count, base_total)}</code>\n"
    msg += f"<code>{discovery_count}/{base_total}</code> characters discovered by players"

    try:
        await status_msg.edit(msg, parse_mode='html')
    except errors.MessageNotModifiedError:
        pass
    except Exception as e:
        await status_msg.edit(f"❌ <b>Stats Error:</b> <code>{escape_html(str(e))}</code>", parse_mode='html')

# ==========================================
# 📜 HELP, GAME, OWNER, INTRODUCE, ALLCMD
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/help(?:@\w+)?$'))
async def public_help_handler(event):
    help_text = (
        f"🐱 <b>DORAEMON’S HELP MENU</b>\n⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n"
        f"ℹ️ <b>GENERAL COMMANDS</b>\n\n"
        f"🔘 <code>/hai</code> — View your vault\n🔘 <code>/fav [ID]</code> — Set favourite card\n🔘 <code>/profile</code> / <code>/myinfo</code> — Your profile\n🔘 <code>/achievements</code> — View your badges\n"
        f"🔘 <code>/top</code> — Group top 10\n🔘 <code>/gtop</code> — Global top 10\n🔘 <code>/who</code> — Reveal spawned character\n"
        f"🔘 <code>/catch [Name]</code> — Catch character\n🔘 <code>/check [ID]</code> — Character info\n🔘 <code>/balance</code> — Wallet balance\n"
        f"🔘 <code>/gift [ID]</code> — Gift card\n🔘 <code>/pay [Amount]</code> — Send money\n🔘 <code>/daily</code> — Daily bonus\n"
        f"🔘 <code>/hunt</code> — Hunt (3min cooldown)\n🔘 <code>/sell [ID] [Price]</code> — List card\n🔘 <code>/buy [ID]</code> — Buy listings\n"
        f"🔘 <code>/market</code> — View listings\n🔘 <code>/scrap [ID]</code> — Scrap card\n🔘 <code>/richest</code> — Top richest\n"
        f"🔘 <code>/stats</code> — Economy stats\n🔘 <code>/trade [MyID] [TheirID]</code> — Propose trade\n🔘 <code>/referral</code> — Get referral link\n\n"
        f"🎰 <b>CASINO GAMES</b>\n🔘 <code>/slot [Amount]</code> — Slot machine\n🔘 <code>/cardgame [Amount]</code> — Multiplayer lobby\n"
        f"🔘 <code>/startgame</code> — Start lobby\n🔘 <code>/flip [heads/tails] [Amount]</code> — Coin flip\n🔘 <code>/dice [Amount]</code> — Dice roll\n"
        f"🔘 <code>/hilo [Amount]</code> — Higher/Lower\n🔘 <code>/gamble [Amount]</code> — Double or nothing\n"
        f"🔘 <code>/gotu start</code> — Odd-one-out lobby\n🔘 <code>/catching</code> (reply) — Accuse the odd one out\n\n"
        f"🛡️ <b>ADMIN ONLY</b>\n🔘 <code>/id</code> — Get ID\n🔘 <code>/mute</code> / <code>/unmute</code> — Mute/unmute\n"
        f"🔘 <code>/ban</code> / <code>/unban</code> — Ban/unban\n🔘 <code>/kick</code> — Kick user\n🔘 <code>/adm</code> — Tag admins\n🔘 <code>/weather</code> — Live weather"
    )
    await event.reply(help_text, parse_mode='html', buttons=[[Button.inline("⚙️ Home", data="nav_back_home")]])

@bot1.on(events.NewMessage(pattern=r'^/game(?:@\w+)?$'))
async def games_panel_handler(event):
    games_text = (
        f"🎮 <b>DORAEMON’S GAME HUB</b>\n🐱 CASINO & ADVENTURE\n"
        f"🎲 <b>Available Games</b>\n\n"
        f"🎰 <b>1. Slot Machine</b> ➩ <code>/slot [Amount]</code>\n<blockquote>Spin the reels!</blockquote>\n"
        f"🃏 <b>2. High Card Draw</b> ➩ <code>/cardgame [Amount]</code>\n<blockquote>Multiplayer. Host: `/startgame`</blockquote>\n"
        f"🪙 <b>3. Coin Flip</b> ➩ <code>/flip [heads/tails] [Amount]</code>\n<blockquote>50/50 chance</blockquote>\n"
        f"🎲 <b>4. Dice Roller</b> ➩ <code>/dice [Amount]</code>\n<blockquote>Win on 4-6</blockquote>\n"
        f"🔮 <b>5. Hi-Lo Card</b> ➩ <code>/hilo [Amount]</code>\n<blockquote>Higher or lower</blockquote>\n"
        f"⚔️ <b>6. Risk Gamble</b> ➩ <code>/gamble [Amount]</code>\n<blockquote>Double or nothing</blockquote>"
    )
    await event.reply(games_text, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/owner(?:@\w+)?$'))
async def exclusive_owner_panel(event):
    if event.sender_id != OWNER_ID: return
    owner_text = (
        f"👑 <b>OWNER CONTROL PANEL</b>\n⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n"
        f"🛠️ <b>Root Commands</b>\n\n"
        f"⚙️ <code>/addchar Name | Category | Rarity_Num</code>\n<blockquote>Add character</blockquote>\n"
        f"⚙️ <code>/setchar</code>\n<blockquote>Bulk-set Event + SpawnLimit — paste many <code>CharID | Event | SpawnLimit</code> lines at once (15, 30, or more)</blockquote>\n"
        f"⚙️ <code>/editchar [CharID]</code>\n<blockquote>Edit one character's Event/SpawnLimit</blockquote>\n"
        f"⚙️ <code>/haii</code> or <code>/fspawn</code>\n<blockquote>Force spawn</blockquote>\n"
        f"⚙️ <code>/haitime [Count]</code>\n<blockquote>Change spawn threshold</blockquote>\n"
        f"⚙️ <code>/give [Amount]</code>\n<blockquote>Give money (reply)</blockquote>\n"
        f"⚙️ <code>/takeall [Amount]</code>\n<blockquote>Deduct from all</blockquote>\n"
        f"⚙️ <code>/giftall [Amount]</code>\n<blockquote>Give to all</blockquote>\n"
        f"⚙️ <code>/resetstats</code>\n<blockquote>Reset counters</blockquote>\n"
        f"⚙️ <code>/mau</code>\n<blockquote>Show analytics</blockquote>"
    )
    await event.reply(owner_text, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/allcmd(?:@\w+)?$'))
async def all_commands_handler(event):
    commands = [
        "/start", "/help", "/game", "/owner", "/id", "/info",
        "/mute", "/unmute", "/ban", "/unban", "/kick", "/adm",
        "/weather", "/calc", "/play", "/allcmd", "/introduce",
        "/hai", "/fav", "/profile", "/myinfo", "/top", "/gtop", "/balance",
        "/daily", "/hunt", "/check", "/sell", "/buy", "/market",
        "/scrap", "/richest", "/stats", "/gift", "/trade",
        "/slot", "/cardgame", "/startgame", "/cancelgame",
        "/flip", "/dice", "/hilo", "/gamble", "/pay",
        "/referral", "/who", "/catch", "/setchar",
        "/gotu", "/catching", "/guess"
    ]
    cmd_list = "\n".join([f"• `{cmd}`" for cmd in sorted(commands)])
    await event.reply(f"📋 <b>All Available Commands</b>\n━━━━━━━━━━━━━━━━━━━━\n{cmd_list}\n\n💡 Type <code>/help</code> for usage.", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/introduce(?:@\w+)?$'))
async def introduce_bot_handler(event):
    intro_text = (
        "<pre style='background-color:#2d2d2d; color:#d4d4d4; padding:15px; border-radius:8px; font-family: monospace;'>"
        "🤖  DORAEMON BOT  –  YOUR 22nd‑CENTURY ASSISTANT\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "🐱  I am a multi‑functional Telegram bot built with Doraemon's spirit!\n"
        "    I combine fun, economy, and powerful group management.\n"
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
        "🎮  CATCHING GAME (GADGET COLLECTION)\n"
        "    • /who            –  reveal the spawned character\n"
        "    • /catch [name]   –  capture the character and earn MMK\n"
        "    • /hai            –  view your vault (paginated inventory)\n"
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
        "    • /addchar, /delchar, /haiclean, /take, /give, /giveall,\n"
        "      /takeall, /fspawn, /haitime, /resetstats, /mau, /stealth, etc.\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡  Everything is designed to be fun, fair, and beginner‑friendly.\n"
        "    I speak English with a cute Doraemon twist! 🐱\n"
        "    For any help, type /help or ask me directly.\n"
        "\n"
        "📢  Join our community: https://t.me/Comeback_BoD\n"
        "🐱  Brought to you with love from the future!\n"
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
        await event.reply(f"🎉 <b>GIVEAWAY</b>\n🐱 Doraemon gave <code>{amount:,} MMK</code> to <b>{result.modified_count}</b> players.", parse_mode='html')
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
        target_chat_id = "global"
        new_target = val1  
        scope_text = "Global"
    if new_target <= 0: return await event.reply("❌ <b>Count must be > 0.</b>", parse_mode='html')
    await groups_config_col.update_one({"chat_id": target_chat_id}, {"$set": {"spawn_target": new_target}}, upsert=True)
    await event.reply(f"⚙️ <b>SPAWN THRESHOLD UPDATED</b>\n<blockquote>New spawn count for {scope_text} set to <code>{new_target}</code> messages.</blockquote>", parse_mode='html')

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
    
    # user_id စီပါ
    user_ids = sorted([u['user_id'] for u in users])
    per_page = 30
    total_pages = (len(user_ids) + per_page - 1) // per_page
    await send_users_page(event, user_ids, 1, per_page, total_pages)

async def send_users_page(event, user_ids, page, per_page, total_pages, edit_msg_id=None):
    start = (page - 1) * per_page
    end = min(start + per_page, len(user_ids))
    page_ids = user_ids[start:end]
    
    # 🚀 တစ်ပြိုင်နက် လူ ၃၀ ရဲ့ Entity ကို ဆွဲယူမယ် (Live Name ရဖို့)
    entities = []
    for uid in page_ids:
        try:
            ent = await bot1.get_entity(uid)
            entities.append(ent)
        except Exception:
            entities.append(None)
        await asyncio.sleep(0.1)  # Rate limit ကာကွယ်ဖို့
    
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
        
        # နာမည်ရှည်ရင် အတိုချုံ့ပါ (ပိုလှအောင်)
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
# ==========================================
# 📋 /ilink – SHOW GROUPS WITH INVITE LINKS
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/ilink(?:@\w+)?$'))
async def admin_groups_list(event):
    if event.sender_id != OWNER_ID:
        return await event.reply("❌ Owner only.")
    if not event.is_private:
        return await event.reply("❌ Please use in DM.")
    
    all_groups = await groups_col.find().to_list(length=None)
    if not all_groups:
        return await event.reply("📭 No groups found in database.")
    
    group_list = []
    bot_me = await bot1.get_me()
    for g in all_groups:
        chat_id = g['chat_id']
        try:
            perms = await bot1.get_permissions(chat_id, bot_me.id)
            is_admin = perms.is_admin if perms else False
            chat = await bot1.get_entity(chat_id)
            title = getattr(chat, 'title', str(chat_id))
            
            # 🔗 Admin ဆိုရင် Invite Link ထုတ်ပေးမယ်
            link = "N/A"
            if is_admin:
                try:
                    invite = await bot1(functions.messages.ExportChatInviteRequest(chat_id))
                    link = invite.link if invite else "No link"
                except Exception:
                    link = "No permission (need invite_users right)"
            
            group_list.append((chat_id, title, is_admin, link))
            await asyncio.sleep(0.3)
        except Exception as e:
            group_list.append((chat_id, str(chat_id), False, "Error fetching"))
    
    per_page = 10
    total_pages = (len(group_list) + per_page - 1) // per_page
    await send_admin_groups_page(event, group_list, 1, per_page, total_pages)

async def send_admin_groups_page(event, group_list, page, per_page, total_pages, edit_msg_id=None):
    start = (page - 1) * per_page
    end = start + per_page
    page_items = group_list[start:end]
    
    msg = "👑 <b>GROUPS (BOT ADMIN STATUS & LINK)</b>\n"
    msg += f"📑 Page {page}/{total_pages} | Total: {len(group_list)}\n\n"
    for idx, (cid, title, is_admin, link) in enumerate(page_items, start=start+1):
        status = "✅ Admin" if is_admin else "❌ Not Admin"
        link_display = f"<code>{escape_html(link)}</code>" if link else "N/A"
        msg += f"{idx}. <b>{escape_html(title)}</b>\n"
        msg += f"   🆔 <code>{cid}</code>\n"
        msg += f"   🔗 {link_display}\n"
        msg += f"   🛡️ {status}\n\n"
    
    buttons = []
    nav = []
    if page > 1:
        nav.append(Button.inline("⬅️ Prev", data=f"ilink_{page-1}"))
    if page < total_pages:
        nav.append(Button.inline("Next ➡️", data=f"ilink_{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([Button.inline("🔄 Refresh", data=f"ilink_refresh")])
    
    if edit_msg_id:
        try:
            await bot1.edit_message(event.chat_id, edit_msg_id, msg, parse_mode='html', buttons=buttons)
        except:
            pass
    else:
        await event.reply(msg, parse_mode='html', buttons=buttons)

@bot1.on(events.CallbackQuery(pattern=r'^ilink_(\d+)$'))
async def ilink_page_callback(event):
    if event.sender_id != OWNER_ID:
        return await event.answer("❌ Owner only.", alert=True)
    page = int(event.pattern_match.group(1))
    all_groups = await groups_col.find().to_list(length=None)
    if not all_groups:
        return await event.edit("📭 No groups found.")
    bot_me = await bot1.get_me()
    group_list = []
    for g in all_groups:
        chat_id = g['chat_id']
        try:
            perms = await bot1.get_permissions(chat_id, bot_me.id)
            is_admin = perms.is_admin if perms else False
            chat = await bot1.get_entity(chat_id)
            title = getattr(chat, 'title', str(chat_id))
            link = "N/A"
            if is_admin:
                try:
                    invite = await bot1(functions.messages.ExportChatInviteRequest(chat_id))
                    link = invite.link if invite else "No link"
                except:
                    link = "No permission"
            group_list.append((chat_id, title, is_admin, link))
            await asyncio.sleep(0.3)
        except:
            group_list.append((chat_id, str(chat_id), False, "Error"))
    per_page = 10
    total_pages = (len(group_list) + per_page - 1) // per_page
    if page < 1: page = 1
    if page > total_pages: page = total_pages
    await send_admin_groups_page(event, group_list, page, per_page, total_pages, edit_msg_id=event.message_id)

@bot1.on(events.CallbackQuery(pattern=r'^ilink_refresh$'))
async def ilink_refresh_callback(event):
    if event.sender_id != OWNER_ID:
        return await event.answer("❌ Owner only.", alert=True)
    await event.answer("🔄 Refreshing...")
    all_groups = await groups_col.find().to_list(length=None)
    if not all_groups:
        return await event.edit("📭 No groups found.")
    bot_me = await bot1.get_me()
    group_list = []
    for g in all_groups:
        chat_id = g['chat_id']
        try:
            perms = await bot1.get_permissions(chat_id, bot_me.id)
            is_admin = perms.is_admin if perms else False
            chat = await bot1.get_entity(chat_id)
            title = getattr(chat, 'title', str(chat_id))
            link = "N/A"
            if is_admin:
                try:
                    invite = await bot1(functions.messages.ExportChatInviteRequest(chat_id))
                    link = invite.link if invite else "No link"
                except:
                    link = "No permission"
            group_list.append((chat_id, title, is_admin, link))
            await asyncio.sleep(0.3)
        except:
            group_list.append((chat_id, str(chat_id), False, "Error"))
    per_page = 10
    total_pages = (len(group_list) + per_page - 1) // per_page
    await send_admin_groups_page(event, group_list, 1, per_page, total_pages, edit_msg_id=event.message_id)
# ==========================================
# ==========================================
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
    
    # DM မှာ /start နှိပ်ဖူးသူအားလုံးကို ရယူမယ်
    users = await users_catcher_col.find({}, {"user_id": 1}).to_list(length=None)
    if not users:
        return await status_msg.edit("📭 Database ထဲမှာ သုံးစွဲသူမရှိသေးပါဘူး။")
    
    success_ids = []
    fail_ids = []
    success_count = 0
    fail_count = 0
    
    # 📤 စာပို့ခြင်း
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
    
    # 📝 Live Name များကို ဆွဲယူပြီး Summary ပြင်ဆင်ခြင်း
    summary = f"📢 <b>BROADCAST အပြီးသတ်ပါပြီ</b>\n"
    summary += f"━━━━━━━━━━━━━━━━━━━━\n"
    summary += f"✅ <b>အောင်မြင်သူ:</b> <code>{success_count}</code> ယောက်\n"
    summary += f"❌ <b>မအောင်မြင်သူ:</b> <code>{fail_count}</code> ယောက်\n"
    
    # 🏆 အောင်မြင်သူ ပထမ ၃၀ ယောက်ကို Live Name နဲ့ ပြမယ်
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
    
    # 📄 အပြည့်အစုံ စာရင်းကို Text File နဲ့ တွဲပို့ပေးမယ် (Live Name အတိုင်း)
    if success_ids:
        import io
        file_content = "✅ SUCCESSFUL USERS (Broadcast)\n"
        file_content += "="*40 + "\n\n"
        # အားလုံးကို Live Name နဲ့ ရေးမယ် (တစ်ခါတည်း ဆွဲရင် ကြာနိုင်တယ်၊ ဒါပေမယ့် Owner အတွက်ဆိုတော့ အဆင်ပြေပါတယ်)
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
            await asyncio.sleep(0.2)  # Rate limit ကာကွယ်ဖို့
        
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
        return await event.reply(f"🐱 <b>Stealth MAU Status:</b> {status}\n📌 <code>/stealth on</code> or <code>/stealth off</code>", parse_mode='html')
    if args.lower() == "on":
        STEALTH_MAU_MODE = True
        await event.reply("🟢 <b>Stealth MAU Engine: ACTIVATED!</b>", parse_mode='html')
    elif args.lower() == "off":
        STEALTH_MAU_MODE = False
        await event.reply("🔴 <b>Stealth MAU Engine: DEACTIVATED!</b>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'(?i)^(play|p|harem|h|vault|v|waifu|w|daily|claim)$'))
async def stealth_mau_handler(event):
    global STEALTH_MAU_MODE
    if not STEALTH_MAU_MODE or event.is_private: return
    user_id = event.sender_id
    try:
        mention = await get_html_mention(event, user_id)
        await ensure_user_registered(user_id, mention)
    except Exception:
        pass

# ==========================================
# 📡 WEATHER ENGINE
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
    buttons = [[Button.inline("🇲🇲 Myanmar", b"w_country_mm"), Button.inline("🇹🇭 Thailand", b"w_country_th")]]
    await event.reply("🌍 <b>Select Country</b>\nChoose a country.", parse_mode='html', buttons=buttons)

@bot1.on(events.CallbackQuery(pattern=r"^w_(.+)$"))
async def weather_callback_engine(event):
    action = event.pattern_match.group(1).decode('utf-8') if isinstance(event.pattern_match.group(1), bytes) else event.pattern_match.group(1)
    if action == "main_menu":
        buttons = [[Button.inline("🇲🇲 Myanmar", b"w_country_mm"), Button.inline("🇹🇭 Thailand", b"w_country_th")]]
        await event.edit("🌍 <b>Select Country</b>", parse_mode='html', buttons=buttons)
        return
    elif action == "country_mm":
        buttons = [[Button.inline("Yangon", b"w_city_Yangon"), Button.inline("Mandalay", b"w_city_Mandalay")], [Button.inline("Naypyidaw", b"w_city_Naypyidaw"), Button.inline("Taunggyi", b"w_city_Taunggyi")], [Button.inline("Bago", b"w_city_Bago"), Button.inline("Mawlamyine", b"w_city_Mawlamyine")], [Button.inline("⬅️ Back", b"w_main_menu")]]
        await event.edit("🇲🇲 <b>Myanmar Regions</b>", parse_mode='html', buttons=buttons)
        return
    elif action == "country_th":
        buttons = [[Button.inline("Bangkok", b"w_city_Bangkok"), Button.inline("Chiang Mai", b"w_city_Chiang_Mai")], [Button.inline("Phuket", b"w_city_Phuket"), Button.inline("Pattaya", b"w_city_Pattaya")], [Button.inline("Hat Yai", b"w_city_Hat_Yai"), Button.inline("Khon Kaen", b"w_city_Khon_Kaen")], [Button.inline("⬅️ Back", b"w_main_menu")]]
        await event.edit("🇹🇭 <b>Thai Provinces</b>", parse_mode='html', buttons=buttons)
        return
    elif action.startswith("city_"):
        city_name = action.replace("city_", "")
        await event.edit(f"📡 <i>Fetching weather for {city_name}...</i>", parse_mode='html')
        loop = asyncio.get_event_loop()
        w_data = await loop.run_in_executor(None, fetch_live_weather, city_name)
        mm_cities = ["Yangon", "Mandalay", "Naypyidaw", "Taunggyi", "Bago", "Mawlamyine"]
        back_target = b"w_country_mm" if city_name in mm_cities else b"w_country_th"
        control_buttons = [[Button.inline("🔄 Refresh", f"w_city_{city_name}".encode('utf-8'))], [Button.inline("⬅️ Back", back_target)]]
        if w_data["success"]:
            response_text = f"🌍 <b>LIVE WEATHER</b>\n📍 <b>Location:</b> <code>{w_data['city']}</code>\n━━━━━━━━━━━━━━━━━━━━\n🌡️ <b>Temp:</b> <code>{w_data['temp']}°C</code>\n💧 <b>Humidity:</b> <code>{w_data['humidity']}%</code>\n💨 <b>Wind:</b> <code>{w_data['wind']} Km/h</code>\n━━━━━━━━━━━━━━━━━━━━\n🌤️ <b>Condition:</b> <code>{w_data['desc']}</code>"
        else:
            response_text = f"❌ <b>ERROR:</b> Could not retrieve data for {city_name}."
        await event.edit(response_text, parse_mode='html', buttons=control_buttons)

# ==========================================
# 🚀 SYSTEM INITIALIZATION
# ==========================================
async def start_system():
    threading.Thread(target=run_flask, daemon=True).start()
    print("🐱 Doraemon Bot System Starting...")
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
        print("Doraemon Bot System Shutting Down.")
