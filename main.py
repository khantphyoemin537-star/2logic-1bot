#############
import io
import imageio
import numpy as np
import asyncio
import logging
import random
import os
import threading
import re
import time
import urllib.request
import json
import pytz
from collections import Counter
from telethon.tl.functions.channels import GetParticipantsRequest
from datetime import datetime, timedelta, date
from flask import Flask, jsonify, render_template_string
from PIL import Image, ImageDraw, ImageFont
from html.parser import HTMLParser
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument
from html import escape as escape_html
from telethon.errors import FloodWaitError
from telethon import TelegramClient, events, types, Button, errors
from collections import defaultdict
from telethon.tl.types import ChannelParticipantsAdmins
from telethon.extensions import html
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
    """FloodWait safe sender"""
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
    """FloodWait safe file sender"""
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
    """Run CPU‑heavy functions in thread pool to avoid blocking the event loop."""
    loop = asyncio.get_running_loop()
    if kwargs:
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))
    return await loop.run_in_executor(None, func, *args)
# 🚀 ADVANCED SYSTEM LOGIC 2: COMMAND COOLDOWN RATE-LIMITER
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

# 🚀 ADVANCED SYSTEM LOGIC: REAL-TIME USER METRICS TRACKER
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

# 🚀 ADVANCED SYSTEM LOGIC 3: CENTRALIZED ERROR NOTIFICATION TO OWNER DM
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
# 🔍 SMART TEXT NORMALIZER FOR TEXT MATCHING
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
    """Read required environment variable"""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"❌ Missing required environment variable: {name}. "
            f"Set it in Render → Environment tab before starting the bot."
        )
    return value

OWNER_ID = int(_require_env("OWNER_ID"))
MONGO_URI = _require_env("MONGO_URI")
APP_ID = int(_require_env("APP_ID"))
APP_HASH = _require_env("APP_HASH")
MAIN_BOT_TOKEN = _require_env("MAIN_BOT_TOKEN")
SPECIFIC_CONTROL_GROUP = int(_require_env("SPECIFIC_CONTROL_GROUP"))
SPECIFIC_GROUP = int(_require_env("SPECIFIC_GROUP"))
TZ = pytz.timezone('Asia/Yangon')
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
welcome_col = db["custom_welcome"] 
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


async def create_indexes():
    print("⚡ Creating Database Indexes...")
    await users_col.create_index("user_id", unique=True)
    await users_catcher_col.create_index("user_id", unique=True)
    await groups_col.create_index("chat_id", unique=True)
    await reply_save_col.create_index("trigger")
    await users_catcher_col.create_index("total_caught")
    await users_catcher_col.create_index("wallet_balance")
    await users_catcher_col.create_index([("group_catches.$**", 1)])
    await groups_config_col.create_index("chat_id")
    await muted_registry_col.create_index([("chat_id", 1), ("user_id", 1)])
    await user_game_profiles_col.create_index("user_id", unique=True)
    await characters_col.create_index("name", unique=True)
    print("Database Indexes synchronized! ✔️")

bot1 = TelegramClient('bot_main_session', APP_ID, APP_HASH)
bot_ids = []
bot_names = []
active_group_spawns = {} 
active_card_games = {}
STEALTH_MAU_MODE = False
spawn_locks = defaultdict(asyncio.Lock) 

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
    return f"<a href='https://t.me/{OWNER_USERNAME}'>@{OWNER_USERNAME}</a>"

# 🎭 RARITY MAPPING MATRIX (NEON GACHA EDITION 🔥)
# ==========================================
RARITY_NUM_MAP = {
    "1": {"name": f"👑 {f('LEGENDARY')}", "value": 1000},  
    "2": {"name": f"💎 {f('LIMITED')}", "value": 750},   
    "3": {"name": f"🌟 {f('MYTHIC')}", "value": 500},    
    "4": {"name": f"⚡ {f('EPIC')}", "value": 300},      
    "5": {"name": f"🍀 {f('RARE')}", "value": 150},      
    "6": {"name": f"🃏 {f('COMMON')}", "value": 50}       
}

# ==========================================
# 🖼️ STATIC CALM GIF GENERATOR
# ==========================================
def generate_simple_static_gif(text, width=500, height=250, bg_color=(10,10,10), text_color=(0,255,65)):
    """Generates a single-frame static GIF with centered text."""
    try:
        # Try to load a bold mono font (fallback to default)
        font = ImageFont.truetype("Courier-Bold.ttf", 28)
    except:
        font = ImageFont.load_default()
    img = Image.new('RGB', (width, height), color=bg_color)
    d = ImageDraw.Draw(img)
    # Draw a neat border
    d.rectangle([(15,15),(width-15,height-15)], outline=text_color, width=3)
    # Center the text
    try:
        bbox = d.textbbox((0,0), text, font=font)
        tw = bbox[2]-bbox[0]
        th = bbox[3]-bbox[1]
    except AttributeError:
        # fallback for older PIL
        tw, th = d.textsize(text, font=font)
    x = (width - tw)//2
    y = (height - th)//2
    d.text((x,y), text, fill=text_color, font=font)
    # Save as single-frame GIF
    gif_buffer = io.BytesIO()
    imageio.mimsave(gif_buffer, [np.array(img)], format='GIF', fps=1)
    gif_buffer.seek(0)
    return gif_buffer

# ==========================================
# (Old generator functions are kept but no longer used; we replace their calls with the new one)
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
# ==========================================
# ⚔️ ALL GROUPS COMMAND INTERCEPTOR (AUTO ACC OPENER)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/'))
async def global_slash_cmd_acc_opener(event):
    """Auto‑register user on any slash command"""
    user_id = event.sender_id
    if not user_id:
        return
        
    try:
        user_entity = await event.get_sender()
        first_name = getattr(user_entity, 'first_name', '') or ''
        last_name = getattr(user_entity, 'last_name', '') or ''
        fullname = f"{first_name} {last_name}".strip()
        if not fullname:
            fullname = getattr(user_entity, 'username', '') or f"User {user_id}"
    except:
        fullname = f"User {user_id}"
        
    await ensure_user_registered(user_id, fullname)

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
# 🧠 OPTIMIZED TRIGGER-RESPONSES SMART REPLY ENGINE
# ==========================================
async def fetch_optimized_smart_reply(user_text):
    # unchanged logic
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
        {"$addFields": {
            "weight": {"$strLenCP": "$trigger"}
        }},
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

COLLECTIONS_LIST = [
    {"col": "custom_filters", "name": "Custom Filters"},
    {"col": "filters", "name": "Filters Main"},
    {"col": "allowed_users", "name": "Allowed Users"},
    {"col": "active_groups", "name": "Active Groups"},
    {"col": "random_talk", "name": "Random Talk (talk_col)"},
    {"col": "talker", "name": "Talker Database"},
    {"col": "custom_welcome", "name": "Custom Welcome"},
    {"col": "chatbot_answers", "name": "Chatbot Answers"},
    {"col": "morgan_talk", "name": "Morgan Talk"},
    {"col": "rank_col", "name": "Rank Collection"},
    {"col": "system_col", "name": "System Config"},
    {"col": "reply_save_col", "name": "Smart Reply Save"},
    {"col": "users", "name": "Core Users"},
    {"col": "user_game_profiles", "name": "Game Profiles"},
    {"col": "characters", "name": "Game Characters"},
    {"col": "muted_registry", "name": "Muted Registry"},
    {"col": "characters_base_data", "name": "Characters Base Data"},
    {"col": "users_catcher_data", "name": "Users Catcher Data"},
    {"col": "groups_msg_counters", "name": "Groups Msg Counters"},
    {"col": "groups_catcher_config", "name": "Groups Catcher Config"},
    {"col": "marketplace_data", "name": "Marketplace Data"}
]

def format_size(bytes_size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} TB"

async def build_db_stats_message(page=1):
    try:
        db_stats = await db.command("dbStats")
        total_data_size = format_size(db_stats.get("dataSize", 0))
        total_storage_size = format_size(db_stats.get("storageSize", 0))
        total_index_size = format_size(db_stats.get("indexSize", 0))
        all_collections_count = db_stats.get("collections", 0)
        
        text = "🤖 <b>[ DORAEMON’S DATABASE MONITOR ]</b> 📊\n"
        text += "====================================\n"
        text += f"👑 <b>Database:</b> <code>telegram_bot</code>\n"
        text += f"📦 <b>Total Data Size:</b> <code>{total_data_size}</code>\n"
        text += f"💾 <b>Storage Size:</b> <code>{total_storage_size}</code>\n"
        text += f"🔑 <b>Index Size:</b> <code>{total_index_size}</code>\n"
        text += f"📂 <b>Total Collections:</b> <code>{all_collections_count}</code>\n"
        text += "====================================\n\n"
        
        start_idx = 0 if page == 1 else 11
        end_idx = 11 if page == 1 else len(COLLECTIONS_LIST)
        target_cols = COLLECTIONS_LIST[start_idx:end_idx]
        
        text += f"📝 <b>Collection Details (Page {page}/2):</b>\n"
        text += "------------------------------------\n"
        
        for item in target_cols:
            col_name = item["col"]
            display_name = item["name"]
            
            try:
                coll_stats = await db.command("collStats", col_name)
                doc_count = coll_stats.get("count", 0)
                storage_size = format_size(coll_stats.get("storageSize", 0))
            except Exception:
                doc_count = 0
                storage_size = "0.00 B"
                
            text += f"🔹 <b>{display_name}</b>\n"
            text += f" ├ 📄 Docs: <code>{doc_count:,}</code> | 💾 Size: <code>{storage_size}</code>\n"
            text += f" └ 🏷️ <i>Key: {col_name}</i>\n\n"
            
        text += f"🕒 <i>Last Checked UTC: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}</i>"
        return text
    except Exception as e:
        return f"❌ DB Stats Fetch Failed: {str(e)}"

# ==========================================
# 📥 TELETHON EVENT HANDLERS (COMMAND & CALLBACK)
# ==========================================

@bot1.on(events.NewMessage(pattern='/checkdb', incoming=True))
async def check_db_command_handler(event):
    if not event.is_private:
        return
        
    if event.sender_id != OWNER_ID:
        await event.respond("🚷 <b>Error:</b> This command is for the owner only.")
        return

    waiting_msg = await event.respond("⏳ Scanning Doraemon’s database... please wait...")
    
    stats_text = await build_db_stats_message(page=1)
    
    buttons = [
        [
            Button.inline("➡️ Page 2", data="db_page_2")
        ],
        [
            Button.inline("🔄 Refresh Stats", data="db_refresh_1")
        ]
    ]
    
    await waiting_msg.delete()
    await event.respond(stats_text, parse_mode='html', buttons=buttons)


@bot1.on(events.CallbackQuery(pattern=r'^db_'))
async def db_stats_callback_handler(event):
    if event.sender_id != OWNER_ID:
        await event.answer("⚠️ You are not authorised to press this button!", alert=True)
        return
        
    action = event.data.decode('utf-8')
    
    await event.answer("🔄 Updating Stats...")
    
    if action == "db_page_1":
        stats_text = await build_db_stats_message(page=1)
        buttons = [
            [Button.inline("➡️ Page 2", data="db_page_2")],
            [Button.inline("🔄 Refresh Stats", data="db_refresh_1")]
        ]
        await event.edit(stats_text, parse_mode='html', buttons=buttons)
        
    elif action == "db_page_2":
        stats_text = await build_db_stats_message(page=2)
        buttons = [
            [Button.inline("⬅️ Page 1", data="db_page_1")],
            [Button.inline("🔄 Refresh Stats", data="db_refresh_2")]
        ]
        await event.edit(stats_text, parse_mode='html', buttons=buttons)
        
    elif action == "db_refresh_1":
        stats_text = await build_db_stats_message(page=1)
        buttons = [
            [Button.inline("➡️ Page 2", data="db_page_2")],
            [Button.inline("🔄 Refresh Stats", data="db_refresh_1")]
        ]
        await event.edit(stats_text, parse_mode='html', buttons=buttons)
        
    elif action == "db_refresh_2":
        stats_text = await build_db_stats_message(page=2)
        buttons = [
            [Button.inline("⬅️ Page 1", data="db_page_1")],
            [Button.inline("🔄 Refresh Stats", data="db_refresh_2")]
        ]
        await event.edit(stats_text, parse_mode='html', buttons=buttons)

# ====================================================================================
# 🛡️ SYSTEM COMMANDS & NAVIGATION HANDLERS (START, HELP, GAME)
# ====================================================================================
@bot1.on(events.NewMessage(pattern=r'^/start(?:\s+(\S+))?$'))
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
            f"⭐ <code>/checkp</code>\n"
            f"<blockquote>Check your wallet balance.</blockquote>\n\n"
            f"⭐ <code>/daily</code>\n"
            f"<blockquote>Claim your daily bonus (streak rewards!).</blockquote>\n\n"
            f"⭐ <code>/hunt</code>\n"
            f"<blockquote>Go on a hunting adventure for extra cash.</blockquote>\n\n"
            f"⭐ <code>/pay [Amount] [Username/ID]</code>\n"
            f"<blockquote>Send money to another player.</blockquote>\n\n"
            f"⭐ <code>/game</code>\n"
            f"<blockquote>View all available mini‑games.</blockquote>"
        )
        return await event.edit(bq(game_text), parse_mode='html', buttons=[[Button.inline("🔙 Back", data="nav_back_home")]])

# ====================================================================================
# 🔢 ADVANCED SYSTEM LOGIC 5: SECURE ARITHMETIC CALCULATOR
# ====================================================================================
@bot1.on(events.NewMessage)
async def auto_calculator_handler(event):
    if event.text.startswith('/'):
        return
        
    text = event.text.strip()
    
    if re.match(r'^[\d\.\s\+\-\*\/\(\)\*\*]+$', text):
        if any(op in text for op in ['+', '-', '*', '/']):
            if len(text) > 50 or (text.count('**') > 1):
                return
            try:
                result = eval(text, {"__builtins__": None}, {})
                gif_display_text = f"{text} = {result}"
                gif_file = await run_blocking(generate_simple_static_gif, gif_display_text)
                gif_file.name = "math_processing.gif"
                await event.client.send_file(
                    event.chat_id, 
                    gif_file, 
                    reply_to=event.id
                )
            except Exception:
                pass



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
            username = f"@{target_user.username}" if target_user.username else "None"
            full_name = f"{target_user.first_name or ''} {target_user.last_name or ''}".strip()
            
            matrix_display = f"INFO for {full_name}\nID: {target_id}"
            gif_file = await run_blocking(generate_simple_static_gif, matrix_display, width=500, height=200)
            gif_file.name = "user_info.gif"
            
            info_caption = (
                f"✨ <b>USER INFO</b> ✨\n"
                f"───────────\n"
                f"🦖 <b>Full Name:</b> {full_name}\n"
                f"🍄 <b>User ID:</b> <code>{target_id}</code>\n"
                f"🦦 <b>Username:</b> {username}\n"
                f"🌏 <b>Chat ID:</b> <code>{event.chat_id}</code>\n"
                f"───────────\n"
                f"<i>Click to copy</i>"
            )
            
            await event.client.send_file(
                event.chat_id, 
                gif_file, 
                caption=info_caption, 
                reply_to=event.id, 
                parse_mode='html'
            )
    except Exception as e:
        print(f"Error in /info: {e}")

# ==========================================
# ⚙️ CHAT ACTIONS & WELCOME MATRIX
# ==========================================
@bot1.on(events.ChatAction)
async def welcome_goodbye(event):
    try:
        chat = await event.get_chat()
        bot_me = await bot1.get_me()
        
        group_name = chat.title 
        target_ids = event.user_ids or [event.user_id]

        try:
            members_cnt = getattr(chat, 'participants_count', None)
            if not members_cnt:
                full_chat = await event.client.get_full_chat(chat.id)
                members_cnt = full_chat.full_chat.participants_count
            member_count = f"{members_cnt:,}" if members_cnt else "Unknown"
        except:
            member_count = "Unknown"

        group_age = "Unknown"
        if hasattr(chat, 'date') and chat.date:
            try:
                now_time = datetime.now(chat.date.tzinfo)
                delta = now_time - chat.date
                days = delta.days
                
                if days >= 365:
                    years = days // 365
                    months = (days % 365) // 30
                    group_age = f"{years}y {months}m" if months else f"{years}y"
                elif days >= 30:
                    months = days // 30
                    remaining_days = days % 30
                    group_age = f"{months}m {remaining_days}d" if remaining_days else f"{months}m"
                else:
                    group_age = f"{days}d" if days > 0 else "Today Created"
            except:
                group_age = "Unknown"

        if event.user_added or event.user_joined:
            if any(uid == bot_me.id for uid in target_ids if uid):
                await groups_col.update_one({"chat_id": chat.id}, {"$set": {"title": chat.title, "joined_at": datetime.now(TZ)}}, upsert=True)
                
                # Send a calm static GIF for bot join
                gif_file = await run_blocking(generate_simple_static_gif, "Doraemon is online!", width=500, height=200)
                gif_file.name = "bot_joined.gif"
                await bot1.send_file(chat.id, gif_file, caption="🐱 I'm here! Let's have fun together.")
                
                bot_mention = f"<a href='tg://user?id={bot_me.id}'>{escape_html(bot_me.first_name)}</a>"
                msg2 = f"Hello {escape_html(group_name)}!\n{bot_mention} has been added to your group.\n\nGroup Name - {escape_html(group_name)}\nGroup ID - <code>{chat.id}</code>\nMembers - {member_count}\nGroup Age - {group_age}\nBot Creator - {owner_tag()}"
                await bot1.send_message(SPECIFIC_GROUP, bq(msg2), parse_mode='html')
            
            else:
                for uid in target_ids:
                    if uid and uid != bot_me.id:
                        try: u = await bot1.get_entity(uid)
                        except: continue
                        track_user_metrics(u.id, u.username, u.first_name) 
                        
                        user_first_name = u.first_name or "User"
                        mention = f"<a href='tg://user?id={uid}'><b>{escape_html(user_first_name)}</b></a>"
                        
                        # Send calm static GIF for new member
                        gif_file = await run_blocking(generate_simple_static_gif, "Welcome!", width=400, height=200)
                        gif_file.name = "welcome.gif"
                        
                        await bot1.send_file(
                            event.chat_id, 
                            gif_file,
                            caption=f"🎉 Welcome {mention} to {escape_html(group_name)}!\n"
                                    f"👥 Members: {member_count}\n"
                                    f"📅 Group age: {group_age}",
                            buttons=[
                                [Button.url("Bot OWNER DM", "t.me/Hello_Im_DexterMorgan")],
                                [Button.url("Join our Community", "https://t.me/Comeback_BoD")]
                            ]
                        )
                        
        elif event.user_kicked or event.user_left:
            if event.user_id == bot_me.id:
                await groups_col.delete_one({"chat_id": chat.id})
            else:
                try:
                    u = await bot1.get_entity(event.user_id)
                    user_first_name = u.first_name or "User"
                    mention = f"<a href='tg://user?id={event.user_id}'><b>{escape_html(user_first_name)}</b></a>"
                    
                    gif_file = await run_blocking(generate_simple_static_gif, "Goodbye!", width=400, height=200)
                    gif_file.name = "goodbye.gif"
                    
                    await bot1.send_file(
                        event.chat_id, 
                        gif_file,
                        caption=f"👋 Goodbye {mention}!\nWe'll miss you."
                    )
                except: pass
    except Exception as e:
        await report_system_error("Welcome Goodbye Event", e)


# 💬 AUTOMATIC CHAT RESPONDER & DYNAMIC TRAFFIC INTERCEPTOR
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

# ====================================================================================
# 📊 PRECISE REAL-TIME METRICS INTEGRATION DASHBOARD
# ====================================================================================
@bot1.on(events.NewMessage(pattern=r'^/mau$'))
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
            f"⚙️ <b>Engine Status:</b> <code>Doraemon’s pocket is ready!</code>\n"
            f"💡 <i>All data is real‑time from internal matrix.</i>"
        )
        await loading.edit(bq(metrics_layout), parse_mode='html')
    except Exception as e:
        await loading.edit(bq(f"❌ Analytics Error: {e}"), parse_mode='html')

# ==========================================
# 🛡️ ANTISPAM MECHANISMS (STICKER & CHARACTER)
# ==========================================
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
                action_msg = f"⚠️ Admin {mention}, please refrain from spamming stickers. Doraemon had to clean them up."
            else:
                await bot1.edit_permissions(chat_id, sender_id, send_stickers=False)
                action_msg = f"{mention} has been restricted from sending stickers due to spam. Doraemon is watching! 🐱"
            
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
                action_msg = f"⚠️ Admin {mention}, Doraemon suggests you stop spamming short messages."
            else:
                await register_temporary_mute(chat_id, sender_id, duration_minutes=5)
                await bot1.edit_permissions(chat_id, sender_id, until_date=datetime.now() + timedelta(minutes=5), send_messages=False)
                action_msg = f"🚫 {mention} has been muted for 5 minutes due to spam. Doraemon’s patience is not infinite! 😾"
            
            await event.respond(bq(action_msg), parse_mode='html')
            del char_spam_data[sender_id]
            return
        except Exception as e: print(f"Char Spam Mute Error: {e}")

    if len(recent_times) == 5:
        try:
            await bot1.delete_messages(chat_id, recent_ids)
            sender = await event.get_sender()
            mention = f"<a href='tg://user?id={sender_id}'>{escape_html(sender.first_name)}</a>"
            warn_msg = f"⚠️ {mention}, Doraemon warns you: stop spam or you’ll be muted for 5 minutes! 🐱"
            await event.respond(bq(warn_msg), parse_mode='html')
        except Exception as e: print(f"Char Spam Warn Error: {e}")

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
            await event.reply(bq(f"✅ Filter '{keyword}' has been saved in Doraemon’s cloud with premium emojis!"), parse_mode='html')
        except Exception as e:
            await event.reply(bq(f"❌ Error saving filter: {e}"), parse_mode='html')
    else:
        await event.reply(bq("⚠️ Reply to a message with the content you want to save as a filter."), parse_mode='html')

# ====================================================================================
# 🆔 IDENTITY MATRIX SYSTEM
# ====================================================================================
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
            matrix_display = f"ID: {target_id}"
            gif_file = await run_blocking(generate_simple_static_gif, matrix_display, width=400, height=150)
            gif_file.name = "id_status.gif"
            
            username = f"@{target_user.username}" if target_user.username else "None"
            id_caption = (
                f"👤 <b>Name:</b> {target_user.first_name or ''}\n"
                f"🆔 <b>User ID:</b> <code>{target_id}</code> (tap to copy)\n"
                f"🦦 <b>Username:</b> {username}\n"
                f"🌏 <b>Chat ID:</b> <code>{event.chat_id}</code> (tap to copy)"
            )
            
            await event.client.send_file(
                event.chat_id, 
                gif_file, 
                caption=id_caption, 
                reply_to=event.id, 
                parse_mode='html'
            )
    except Exception as e:
        print(f"Error in /id: {e}")

# ==========================================
# 🚫 TELEGRAM PERMANENT ASCII MUTE ENGINE (now using calm GIF)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/mute(?:@\w+)?(?:\s+(.+))?'))
async def permanent_ascii_mute_handler(event):
    if not await check_admin(event.chat_id, event.sender_id):
        return

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
        
        gif_file = await run_blocking(generate_simple_static_gif, "MUTED", width=400, height=150)
        gif_file.name = "mute.gif"
        
        await event.client.send_file(
            event.chat_id, 
            gif_file, 
            caption=f"🌌 <b>MUTE OPERATION SUCCESS!</b>\n🤫 <b>{target_name}</b> has been silenced <b>Permanently</b>.", 
            parse_mode='html'
        )

    except Exception as e:
        await event.reply(f"❌ Error: {str(e)}")

# ==========================================
# 🔊 UNMUTE COMMAND
# ==========================================
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
        
        gif_file = await run_blocking(generate_simple_static_gif, "UNMUTED", width=400, height=150)
        gif_file.name = "unmute.gif"
        
        unmute_text = (
            f"🌌 <b>UNMUTE OPERATION</b>\n"
            f"🔊 <b>Target:</b> {mention}\n"
            f"🆔 <b>User ID:</b> <code>{target_user.id}</code>\n"
            f"⚡ <b>Status:</b> <code>Voice Restored</code>"
        )
        await event.client.send_file(event.chat_id, gif_file, caption=bq(unmute_text), parse_mode='html')
    except Exception as e: print(f"Unmute Error: {e}")

# ==========================================
# 🚫 BAN COMMAND
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/ban(?:@\w+)?(?:\s+(.*))?'))
async def ban_user(event):
    if not await check_ban_rights(event.chat_id, event.sender_id): return
    target_user = await get_target_user(event, event.pattern_match.group(1))
    if not target_user: return

    try:
        await bot1.edit_permissions(event.chat_id, target_user.id, view_messages=False)
        
        target_name = f"{getattr(target_user, 'first_name', 'User')} {getattr(target_user, 'last_name', '') or ''}".strip()
        mention = f"<a href='tg://user?id={target_user.id}'>{escape_html(target_name)}</a>"
        
        gif_file = await run_blocking(generate_simple_static_gif, "BANNED", width=400, height=150)
        gif_file.name = "ban.gif"
        
        ban_text = (
            f"🌌 <b>BAN OPERATION</b>\n"
            f"🚫 <b>Target:</b> {mention}\n"
            f"🆔 <b>User ID:</b> <code>{target_user.id}</code>\n"
            f"⚡ <b>Status:</b> <code>Exiled / Perm-Banned</code>"
        )
        await event.client.send_file(event.chat_id, gif_file, caption=bq(ban_text), parse_mode='html')
    except Exception as e: print(f"Ban Error: {e}")

# ==========================================
# ✅ UNBAN COMMAND
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/unban(?:@\w+)?(?:\s+(.*))?'))
async def unban_user(event):
    if not await check_ban_rights(event.chat_id, event.sender_id): return
    target_user = await get_target_user(event, event.pattern_match.group(1))
    if not target_user: return

    try:
        await bot1.edit_permissions(event.chat_id, target_user.id, view_messages=True)
        
        target_name = f"{getattr(target_user, 'first_name', 'User')} {getattr(target_user, 'last_name', '') or ''}".strip()
        mention = f"<a href='tg://user?id={target_user.id}'>{escape_html(target_name)}</a>"
        
        gif_file = await run_blocking(generate_simple_static_gif, "UNBANNED", width=400, height=150)
        gif_file.name = "unban.gif"
        
        unban_text = (
            f"🌌 <b>UNBAN OPERATION</b>\n"
            f"✅ <b>Target:</b> {mention}\n"
            f"🆔 <b>User ID:</b> <code>{target_user.id}</code>\n"
            f"⚡ <b>Status:</b> <code>Ban Lifted</code>"
        )
        await event.client.send_file(event.chat_id, gif_file, caption=bq(unban_text), parse_mode='html')
    except Exception as e: print(f"Unban Error: {e}")

# ==========================================
# 💨 KICK COMMAND
# ==========================================
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
        
        gif_file = await run_blocking(generate_simple_static_gif, "KICKED", width=400, height=150)
        gif_file.name = "kick.gif"
        
        kick_text = (
            f"🌌 <b>KICK OPERATION</b>\n"
            f"💨 <b>Target:</b> {mention}\n"
            f"🆔 <b>User ID:</b> <code>{target_user.id}</code>\n"
            f"⚡ <b>Status:</b> <code>Removed / Kicked</code>"
        )
        await event.client.send_file(event.chat_id, gif_file, caption=bq(kick_text), parse_mode='html')
    except Exception as e: print(f"Kick Error: {e}")

# ==========================================
# 📥 1. PERMANENT DATABASE ADDER
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/addchar(?:\s+(.+))?'))
async def add_character(event):
    if event.sender_id != OWNER_ID: return

    input_text = event.pattern_match.group(1)
    if not input_text or '|' not in input_text:
        await event.reply(
            f"⚠️ <b>{f('Invalid format!')}</b>\n"
            f"⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n"
            f"📌 <b>{f('Usage')}:</b>\n"
            f"<code>/addchar Name | Category | Rarity_Number</code>\n"
            f"<i>(Reply to a media file)</i>\n\n"
            f"🔢 <b>{f('Rarity Tiers (1-6)')}:</b>\n"
            f"<code>1</code> = 👑 LEGENDARY (1000 MMK)\n"
            f"<code>2</code> = 💎 LIMITED-EDITION (750 MMK)\n"
            f"<code>3</code> = 🌟 MYTHIC (500 MMK)\n"
            f"<code>4</code> = ⚡ EPIC (300 MMK)\n"
            f"<code>5</code> = 🍀 RARE (150 MMK)\n"
            f"<code>6</code> = 🃏 COMMON (50 MMK)\n"
            f"⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n"
            f"💡 <b>{f('Example')}:</b> <code>/addchar Doraemon | 22nd Century | 1</code>",
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

    reply_msg = await event.get_reply_message()
    if not reply_msg or not (reply_msg.photo or reply_msg.video or reply_msg.document):
        return await event.reply(f"❌ <b>{f('Valid media not found')}</b>", parse_mode='html')

    try:
        forwarded_msg = await send_safe_message(bot1, SPECIFIC_CONTROL_GROUP, "", file=reply_msg.media)
        storage_id = forwarded_msg.id
        r_info = RARITY_NUM_MAP[rarity_num]
        
        while True:
            char_id = f"BOD{random.randint(1, 9999)}"
            exists = await characters_base_col.find_one({"char_id": char_id})
            if not exists: break

        character_data = {
            "char_id": char_id,
            "name": char_name,
            "category": category_name,
            "rarity": r_info["name"],
            "storage_msg_id": storage_id,
            "currency_value": r_info["value"],
            "spawn_count": 0
        }

        await characters_base_col.insert_one(character_data)
        
        success_msg = (
            f"🔥 <b>{f('DATABASE INJECTED')}</b>\n"
            f"🆔 <b>{f('Character ID')}:</b> <code>{char_id}</code>\n"
            f"👤 <b>{f('Name')}:</b> <code>{char_name}</code>\n"
            f"🌐 <b>{f('Category')}:</b> <code>{category_name}</code>\n"
            f"🌟 <b>{f('Rarity')}:</b> {r_info['name']}\n"
            f"💎 <b>{f('Worth')}:</b> <code>{r_info['value']} MMK</code>\n"
            f"<blockquote><b>{f('Status')}:</b> Character added to Doraemon’s database!</blockquote>"
        )
        await event.reply(success_msg, parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ <b>Database Inject Error</b>: <code>{escape_html(str(e))}</code>", parse_mode='html')
# ==========================================
# 🗑️ OWNER-ONLY CHARACTER DELETE COMMAND
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/(delchar|removechar)(?:\s+(\S+))?(?:@\w+)?$'))
async def delete_character_by_owner(event):
    if event.sender_id != OWNER_ID: 
        return
        
    char_id = event.pattern_match.group(2)
    if not char_id:
        await event.reply(f"❌ Please provide the Character ID to delete, Chief!\nExample: `/delchar BOD789`")
        return
        
    query = {"$or": [{"id": char_id}, {"char_id": char_id}]}
    
    res1 = await db["characters"].delete_many(query)
    res2 = await db["characters_base_data"].delete_many(query)
    
    if res1.deleted_count > 0 or res2.deleted_count > 0:
        reply_msg = (
            f"🔥 {f('DATABASE REMOVED')}\n"
            f"🆔 {f('Character ID')}: {char_id}\n\n"
            f"Status: Character permanently deleted from Doraemon’s records."
        )
        await event.reply(reply_msg)
    else:
        await event.reply(f"❌ No character found with ID `{char_id}` in Doraemon’s database.")

@bot1.on(events.NewMessage(pattern=r'^/haiclean$'))
async def haiclean_handler(event):
    if event.sender_id != OWNER_ID:
        return await event.reply("❌ This command is for the owner only.")
    
    progress_msg = await event.reply("⏳ Doraemon is cleaning the database...")
    
    try:
        await db["characters_base_data"].delete_many({})
        await db["users_catcher_data"].delete_many({})
        await db["marketplace_data"].delete_many({})
        
        await progress_msg.edit("🧹 All three databases have been completely wiped clean! Ready for a fresh start.")
        
    except Exception as e:
        await progress_msg.edit(f"❌ Error: {e}")
@bot1.on(events.NewMessage(pattern=r'^/adm(?:\s+(.*))?'))
async def tag_admins(event):
    if event.is_private: return
    if not await check_admin(event.chat_id, event.sender_id): return
    text = event.pattern_match.group(1) or "Admins, Doraemon needs you! 🐱"
    
    try:
        admins = await bot1.get_participants(event.chat_id, filter=ChannelParticipantsAdmins)
        admin_mentions = []
        cyber_emojis = []
        
        for index, a in enumerate(admins):
            if a.bot or getattr(a, 'deleted', False): continue
            full_name = f"{a.first_name or ''} {a.last_name or ''}".strip() or "Admin"
            emoji = cyber_emojis[index % len(cyber_emojis)]
            mention = f"<a href='tg://user?id={a.id}'><b>{escape_html(full_name)}</b></a> {emoji}"
            admin_mentions.append(mention)
        
        compact_admin_list = "  🎋  ".join(admin_mentions)
        adm_signal_text = (
            f"</b> <code>{escape_html(text)}</code>\n\n"
            f"🐱 <b>Admins, please pay attention!</b>\n"
            f"{compact_admin_list}"
        )
        await event.respond(bq(adm_signal_text), parse_mode='html')
    except Exception as e: print(f"Admin Tag System Error: {e}")

@bot1.on(events.NewMessage(pattern=r'^/send(?:@\w+)?(?:\s+(.*))?'))
async def broadcast(event):
    if event.sender_id != OWNER_ID: return
    reply_msg = await event.get_reply_message()
    command_text = event.pattern_match.group(1)
    if not reply_msg and not command_text: return
    
    success, fail = 0, 0
    init_text = f"🐱 <b>DORAEMON BROADCAST INITIATED</b>\n⚡ <b>Status:</b> <code>Deploying signals...</code>"
    status_msg = await event.respond(bq(init_text), parse_mode='html')
    groups = await groups_col.find().to_list(length=None)

    for g in groups:
        chat_id = g['chat_id']
        try:
            if reply_msg: await bot1.forward_messages(chat_id, reply_msg)
            else: await bot1.send_message(chat_id, command_text, parse_mode='html')
            success += 1
            await asyncio.sleep(0.2) 
        except errors.rpcerrorlist.FloodWaitError as e:
            await asyncio.sleep(e.seconds + 2)
            try:
                if reply_msg: await bot1.forward_messages(chat_id, reply_msg)
                else: await bot1.send_message(chat_id, command_text, parse_mode='html')
                success += 1
            except: fail += 1
        except Exception: fail += 1
            
    report_text = f"🐱 <b>BROADCAST COMPLETE</b>\n✅ <b>Success:</b> <code>{success}</code>\n❌ <b>Failed:</b>  <code>{fail}</code>"
    await status_msg.edit(bq(report_text), parse_mode='html')

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

            msg_template = "<b>{type} command detected</b> {mention}\nDoraemon deleted it to keep the chat clean."
            warn_msg = await event.respond(bq(msg_template.format(type=cmd_type, mention=mention)), parse_mode='html')
            for i in range(2, -1, -1):
                await asyncio.sleep(1)
                if i == 0: await warn_msg.delete(); break
                await warn_msg.edit(bq(msg_template.format(type=cmd_type, mention=mention) + f"\n\nDeleting in {i} seconds..."), parse_mode='html')
            return 
        except: pass

    if text:
        text_lower = text.lower()
        BIO_KEYWORDS = ["bio", "b i o", "biolink", "bio-link", "b-i-o", "tg bio", "ဘိုင်အို", "ဘိုင်-အို", "ဘိုင်-o", "ဘီအိုင်အို", "b.i.o", "ဘိုင်အိုလင့်", "ဘိုင်အိုလင့်ခ်"]

        if any(key in text_lower for key in BIO_KEYWORDS) and not await check_admin(chat_id, sender_id):
            try:
                await event.delete()
                sender = await event.get_sender()
                first_name = sender.first_name if sender else "User"
                mention = f"<a href='tg://user?id={sender_id}'>{escape_html(first_name)}</a>"
                
                warn_text = f"⚠️ <b>BIO ALERT</b>\n{mention}, Doraemon doesn’t allow bio links here.\n\n⚡ <b>Status:</b> <blockquote expandable>Message deleted 🗑️</blockquote>"
                await event.respond(bq(warn_text), parse_mode='html')
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
                await event.respond(bq(f"🔗 {mention}, Doraemon says no links allowed! I deleted it."), parse_mode='html')
            except: pass

play_blocks = {}  

@bot1.on(events.NewMessage(pattern=r'^/play(?:\s+(.*))?'))
async def play_response(event):
    if event.is_private: return
    song_title = event.pattern_match.group(1)
    if not song_title: return await event.reply(bq("🐱 Please provide a song title!\nExample: <code>/play Doraemon Theme</code>"), parse_mode='html')

    chat_id = event.chat_id
    sender = await event.get_sender()
    user_id = event.sender_id
    
    if sender and getattr(sender, 'username', None): mention = f"@{sender.username}"
    else: mention = f"<a href='tg://user?id={user_id}'>{escape_html(getattr(sender, 'first_name', 'User'))}</a>"

    session_key = (chat_id, user_id)
    if session_key not in play_blocks: play_blocks[session_key] = {"msg_id": None, "songs": []}

    current_song_entry = {"title": song_title.strip(), "by": mention}
    play_blocks[session_key]["songs"].append(current_song_entry)

    playlist_text = ""
    for index, s in enumerate(play_blocks[session_key]["songs"], start=1):
        playlist_text += f"{index} 💿 <code>/play {escape_html(s['title'])}</code> — {s['by']}\n"

    try:
        owner_entity = await bot1.get_entity(OWNER_ID)
        owner_fullname = f"{owner_entity.first_name or ''} {owner_entity.last_name or ''}".strip() or "Khant Thu Rain"
    except Exception: owner_fullname = "Khant Thu Rain" 

    msg_template = (
        f"🐱 <b>Doraemon’s Music System</b>\n"
        f"💿 <b>Now Playing:</b> <code>{escape_html(song_title.strip())}</code>\n"
        f"✨ <b>Requested By:</b> {mention}\n\n"
        f"💎 <b>Your Playlist (max 30):</b>\n"
        f"{playlist_text}"
        f"💡 <i>Doraemon keeps your last 30 songs. Just tap /play again to add more!</i>"
    )

    inline_buttons = [
        [Button.url("Join the Fan Club", "https://t.me/Comeback_BoD")],
        [Button.inline("Save Playlist 📥", data=f"save_pl_{user_id}")]
    ]

    try:
        last_msg_id = play_blocks[session_key]["msg_id"]
        if last_msg_id:
            try:
                await bot1.edit_message(entity=chat_id, message=last_msg_id, text=bq(msg_template), parse_mode='html', buttons=inline_buttons)
                try: await event.delete()
                except: pass
            except: last_msg_id = None

        if not last_msg_id:
            new_msg = await event.respond(bq(msg_template), parse_mode='html', buttons=inline_buttons)
            play_blocks[session_key]["msg_id"] = new_msg.id
            try: await event.delete()
            except: pass

        if len(play_blocks[session_key]["songs"]) >= 30: play_blocks[session_key] = {"msg_id": None, "songs": []}
    except Exception as e: print(f"Multimedia Play System Error: {e}")

@bot1.on(events.CallbackQuery(pattern=r'^save_pl_(\d+)$'))
async def close_playlist_callback(event):
    chat_id = event.chat_id
    clicker_id = event.sender_id
    target_user_id = int(event.pattern_match.group(1))
    
    if clicker_id != target_user_id:
        return await event.answer("❌ This is not your playlist! Use your own /play.", alert=True)
        
    session_key = (chat_id, target_user_id)
    try:
        msg = await event.get_message()
        current_html = msg.html if msg else ""
        frozen_buttons = [[Button.url("Join Our Group", "https://t.me/Comeback_BoD")], [Button.inline("🔒 Playlist Locked", data="playlist_frozen_done")]]
        if current_html: await event.edit(text=current_html, parse_mode='html', buttons=frozen_buttons)
        if session_key in play_blocks: del play_blocks[session_key]
        await event.answer("🔒 Playlist saved!", alert=True)
    except Exception as e: print(f"Callback Error: {e}")

@bot1.on(events.NewMessage(pattern=r'^/shadow(?:@\w+)?$'))
async def set_shadow_ban(event):
    if not await is_allowed(event.sender_id): return
    reply = await event.get_reply_message()
    if not reply or reply.sender_id == OWNER_ID: return
    dark_passenger_targets[event.chat_id] = reply.sender_id
    await event.respond(bq("Shadow ban activated. Doraemon is watching."))

@bot1.on(events.NewMessage(pattern=r'^/unshadow(?:@\w+)?$'))
async def remove_shadow_ban(event):
    if not await is_allowed(event.sender_id): return
    if event.chat_id in dark_passenger_targets:
        del dark_passenger_targets[event.chat_id]
        await event.respond(bq("Shadow ban removed. Doraemon looks away."))

@bot1.on(events.NewMessage(pattern=r'^စကားဝင်ပြော$'))
async def turn_on_talking(event):
    if event.sender_id != OWNER_ID or event.chat_id != SPECIFIC_CONTROL_GROUP: return
    global GLOBAL_TALK
    GLOBAL_TALK = True
    on_text = f"🐱 <b>DORAEMON’S TALK ENGINE: ACTIVATED</b>\n🔊 Smart replies are now active in all groups."
    await event.reply(bq(on_text), parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^မပြောနဲ့တော့$'))
async def turn_off_talking(event):
    if event.sender_id != OWNER_ID or event.chat_id != SPECIFIC_CONTROL_GROUP: return
    global GLOBAL_TALK
    GLOBAL_TALK = False
    off_text = f"🔇 Doraemon’s talk engine is now in <b>Stealth Mode</b> – silent."
    await event.reply(bq(off_text), parse_mode='html')

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
                insults = [
                    f"🤫 <b>THE ILLUSION OF VOICE</b>\n\nDoraemon allows your keystrokes, but he owns the void.",
                    f"💀 <b>DORAEMON’S ISOLATION CHAMBER</b>\n\nType all you want, {mention}, your messages vanish."
                ]
                insult_msg = await event.respond(bq(random.choice(insults)), parse_mode='html')
                await asyncio.sleep(4.0)
                await insult_msg.delete()
            except: pass

# ====================================================================================
# 🌐 LANGUAGE & FLOOD CONTROL INCOMING GLOBAL COUNTERS
# ====================================================================================
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
                warn_text = f"⚠️ <b>LANGUAGE SECURITY</b>\nHey {mention}, only <b>Burmese, English</b> and <b>Numbers</b> are allowed.\n\n⚡ <b>Action:</b> Message deleted by Doraemon."
                return await event.respond(bq(warn_text), parse_mode='html')
            except Exception: pass

    if event.fwd_from and isinstance(event.fwd_from.from_id, types.PeerChannel):
        if not await check_admin(chat_id, sender_id):
            try:
                await event.delete()
                sender = await event.get_sender()
                mention = f"<a href='tg://user?id={sender_id}'>{escape_html(sender.first_name if sender else 'User')}</a>"
                warn_msg = f"🚫 <b>CHANNEL FORWARD NOT ALLOWED!</b>\n\n{mention} <b>[Deleted]</b>"
                return await event.respond(bq(warn_msg), parse_mode='html')
            except Exception: pass


@bot1.on(events.NewMessage(pattern=r'^/ktr(?:\s+(\d+))?$'))
async def turn_on_reply_save_match(event):
    global IS_REPLY_ACTIVE, REPLY_INTERVAL
    if event.sender_id != OWNER_ID: return
    IS_REPLY_ACTIVE = True
    interval_arg = event.pattern_match.group(1)
    if interval_arg: REPLY_INTERVAL = int(interval_arg)
        
    ktr_on_text = f"🐱 <b>DORAEMON’S REPLY MATCH ENGINE: ACTIVATED</b>\n🎯 Smart responses will be injected every <code>{REPLY_INTERVAL}</code> messages."
    await event.reply(bq(ktr_on_text), parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/ktrr$'))
async def turn_off_reply_save_match(event):
    global IS_REPLY_ACTIVE
    if event.sender_id != OWNER_ID: return
    IS_REPLY_ACTIVE = False
    ktr_off_text = f"⛔ Doraemon’s reply engine is now offline."
    await event.reply(bq(ktr_off_text), parse_mode='html')

async def processed_background_reply(event, user_text, as_reply=True):
    chat_entity = await event.get_input_chat()
    smart_reply = await fetch_optimized_smart_reply(user_text)
    if smart_reply:
        try:
            async with bot1.action(chat_entity, 'typing'):
                await asyncio.sleep(random.uniform(0.5, 1.5))
            if as_reply: await event.reply(smart_reply, parse_mode='html')
            else: await event.respond(smart_reply, parse_mode='html')
        except errors.rpcerrorlist.FloodWaitError as e: await asyncio.sleep(e.seconds + 1)
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
# ==========================================
# 💰 OWNER WALLET CONTROL COMMANDS (/take, /giveall, /give, /takeall)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/take\s+(\d+)'))
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
        await event.reply(f"💰 Added <code>{amount:,} MMK</code> to Doraemon’s owner wallet.", parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ <b>Database Error:</b> <code>{e}</code>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/giveall\s+(\d+)'))
async def give_all_players_mmk(event):
    if event.sender_id != OWNER_ID: return
    amount = int(event.pattern_match.group(1))
    try:
        result = await users_catcher_col.update_many({}, {"$inc": {"wallet_balance": amount}})
        success_text = (
            f"🎁 <b>{f('GLOBAL BOUNTY DISTRIBUTED')}</b>\n"
            f"⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n"
            f"<blockquote>Doraemon gave <code>{amount:,} MMK</code> to every player. Total players: <code>{result.modified_count}</code></blockquote>"
        )
        await event.reply(success_text, parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ <b>Global Distribution Error:</b> <code>{e}</code>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/give\s+(\d+)'))
async def give_money_to_user(event):
    if event.sender_id != OWNER_ID: return
    if not event.is_reply:
        return await event.reply("⚠️ <b>Reply to the user you want to give money to.</b>", parse_mode='html')
    
    amount = int(event.pattern_match.group(1))
    reply_msg = await event.get_reply_message()
    target_user_id = reply_msg.sender_id
    
    target_mention = await get_html_mention(event, target_user_id)
    await ensure_user_registered(target_user_id, target_mention)
    
    try:
        await users_catcher_col.update_one(
            {"user_id": target_user_id},
            {"$inc": {"wallet_balance": amount}, "$set": {"fullname": target_mention}}
        )
        await event.reply(f"💸 {target_mention} <b>received</b> <code>{amount:,} MMK</code> from Doraemon’s pocket.", parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ <b>Give System Fault:</b> <code>{e}</code>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/takeall\s+(\d+)'))
async def take_money_from_everyone(event):
    if event.sender_id != OWNER_ID: return
    amount = int(event.pattern_match.group(1))
    try:
        result = await users_catcher_col.update_many(
            {},
            [{"$set": {"wallet_balance": {"$max": [0, {"$subtract": ["$wallet_balance", amount]}]}}}]
        )
        text = (
            f"🚨 <b>{f('SYSTEM DEDUCTION')}</b>\n"
            f"<blockquote>Doraemon deducted <code>{amount:,} MMK</code> from every player. Total affected: <code>{result.modified_count}</code></blockquote>"
        )
        await event.reply(text, parse_mode='html')   
    except Exception as e:
        await event.reply(f"❌ <b>Takeall System Fault:</b> <code>{e}</code>", parse_mode='html')

# ==========================================
# 🛰️ 2. OVERRIDE FORCE SPAWN ENGINE (/fspawn & /haii)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/(fspawn|haii)(?:@\w+)?$'))
async def force_spawn_by_owner(event):
    if event.sender_id != OWNER_ID: return
    await trigger_dynamic_spawn(event.chat_id)

# ==========================================
# 📢 3. AUTOMATIC SPAWN PROCESSOR (RARITY WEIGHTED)
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
        
        RARITY_WEIGHTS = {"LEGENDARY": 1, "LIMITED": 3, "MYTHIC": 6, "EPIC": 15, "RARE": 30, "COMMON": 50}
        weights = []
        for char in characters_list:
            char_rarity = str(char.get("rarity", "")).upper()
            matched_weight = 20  
            for rarity_key, weight_val in RARITY_WEIGHTS.items():
                if rarity_key in char_rarity:
                    matched_weight = weight_val
                    break
            weights.append(matched_weight)

        chosen_char = random.choices(characters_list, weights=weights, k=1)[0]
        storage_msg = await bot1.get_messages(SPECIFIC_CONTROL_GROUP, ids=chosen_char["storage_msg_id"])
        if not storage_msg or not storage_msg.media: return
        
        await characters_base_col.update_one({"char_id": chosen_char["char_id"]}, {"$inc": {"spawn_count": 1}})
        
        # Show rarity in spawn message
        rarity_display = chosen_char.get("rarity", "Unknown")
        spawn_msg = await bot1.send_message(
            chat_id, 
            f"🎃 <b>Doraemon’s gadget appeared!</b>\n"
            f"🌟 <b>Rarity:</b> {rarity_display}\n"
            f"<blockquote>Who is this? Reply with <code>/who</code> to reveal!</blockquote>\n"
            f"If you don’t know how, reply with <code>/help</code>.",
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
            "spawn_time": time.time(),
            "revealed": False,
            "claimed": False
        }
    except Exception as e:
        print(f"Spawn Error Tracker: {e}")

@bot1.on(events.NewMessage(pattern=r'^/who$'))
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
        matrix_display = f"Character: {spawn_data['name']}\nRarity: {spawn_data['rarity']}"
        gif_file = await run_blocking(generate_simple_static_gif, matrix_display, width=500, height=200)
        gif_file.name = "identity_reveal.gif"
        
        reveal_text = (
            f"🌟 <b>Name:</b> {spawn_data['name']}\n"
            f"🌟 <b>Rarity:</b> {spawn_data['rarity']}\n"
            f"🌐 <b>Category:</b> {spawn_data['category']}\n\n"
            f"👉 <code>/catch {spawn_data['name']}</code>"
        )
        
        await event.client.send_file(
            event.chat_id, 
            gif_file, 
            caption=reveal_text, 
            reply_to=event.id, 
            parse_mode='html'
        )
        
    except Exception as e:
        print(f"Error in /who command: {e}")


# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/catch\s+(.*)$'))
async def catch_handler(event):
    if event.is_private: return
    chat_id = event.chat_id
    user_id = event.sender_id
    catch_name = event.pattern_match.group(1).strip()
    
    if chat_id not in active_group_spawns:
        return await event.reply(f"🛸 <b>Nothing to catch here!</b>", parse_mode='html')
        
    spawn_data = active_group_spawns[chat_id]
    if spawn_data["claimed"]: return

    if time.time() - spawn_data["spawn_time"] > 300:
        if chat_id in active_group_spawns: del active_group_spawns[chat_id]
        return await event.reply(f"⏱️ <b>Too late! The gadget ran away.</b>", parse_mode='html')

    if normalize_name(catch_name) != normalize_name(spawn_data["name"]): 
        return await event.reply(f"❌ <b>Wrong name! Try again with the correct name.</b>", parse_mode='html')

    async with spawn_locks[chat_id]:
        if active_group_spawns.get(chat_id, {}).get("claimed", True): return
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
            
            matrix_display = (
                f"⚡ MISSION: TARGET SECURED ⚡\n"
                f"─────────\n"
                f"🃏 Character : {character_name}\n"
                f"🌟 Rarity    : {spawn_data['rarity']}\n"
                f"🪙 Bounty    : +{spawn_data['value']} MMK\n"
                f" [ ADDED TO INVENTORY ]"
            )
            
            gif_file = await run_blocking(generate_simple_static_gif, matrix_display, width=500, height=250)
            gif_file.name = "catch_success.gif"
            
            del active_group_spawns[chat_id]
            if chat_id in spawn_locks: del spawn_locks[chat_id]
            
            success_msg = (
                f"👤 <b>Hunter:</b> {mention}, you caught it! 🎉\n"
                f"🃏 <b>Character:</b> <b>{escape_html(character_name)}</b>\n"
                f"🆔 <b>Asset ID:</b> <b>{spawn_data['char_id']}</b>\n"
                f"🌟 <b>Rarity Class:</b> {spawn_data['rarity']}\n"
                f"🪙 <b>Bounty Added:</b> <b>+{spawn_data['value']} MMK</b>\n\n"
                f"🎉 <b>Mission Secured!</b> Check your collection with <code>/hai</code>."
            )
            
            if guild_levelup_msg:
                success_msg += guild_levelup_msg
                
            await bot1.send_file(
                event.chat_id, 
                gif_file, 
                caption=success_msg, 
                reply_to=event.id, 
                parse_mode='html'
            )
            
        except Exception as e:
            if chat_id in active_group_spawns:
                active_group_spawns[chat_id]["claimed"] = False
            await event.reply(f"❌ <b>Catch Logic Fault:</b> {e}", parse_mode='html')

# ==========================================
# 🎒 6. INVENTORY PAGINATION ENGINE (/fav & /hai)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/fav\s+([a-zA-Z0-9_]+)$'))
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

async def send_paginated_harem(client, chat_id, user_id, page=1, edit_msg_id=None):
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    if not user_doc or not user_doc.get("harem"):
        msg = f"🎒 <b>Your vault is empty! Go catch some gadgets!</b>"
        if edit_msg_id: await client.edit_message(chat_id, edit_msg_id, msg, parse_mode='html')
        else: await client.send_message(chat_id, msg, parse_mode='html')
        return

    raw_harem = user_doc.get("harem", [])
    harem_counts = {}
    for item in raw_harem:
        if isinstance(item, dict) and "char_id" in item:
            cid = item["char_id"]
            is_market = item.get("status") == "market"
            if cid not in harem_counts: harem_counts[cid] = {"normal": 0, "market": 0}
            if is_market: harem_counts[cid]["market"] += 1
            else: harem_counts[cid]["normal"] += 1
            
    owned_ids = list(harem_counts.keys())
    db_chars = await characters_base_col.find({"char_id": {"$in": owned_ids}}).to_list(length=None)
    
    def get_rarity_weight(rarity_str):
        rarity_str = rarity_str.upper()
        if "LEGENDARY" in rarity_str: return 6
        if "LIMITED" in rarity_str: return 5
        if "MYTHIC" in rarity_str: return 4
        if "EPIC" in rarity_str: return 3
        if "RARE" in rarity_str: return 2
        if "COMMON" in rarity_str: return 1
        return 0

    db_chars = sorted(
        db_chars, 
        key=lambda x: (-get_rarity_weight(x.get("rarity", "")), x.get("name", "").lower())
    )
    
    lines = []
    for card in db_chars:
        cid = card["char_id"]
        counts = harem_counts[cid]
        normal_qty = counts["normal"]
        market_qty = counts["market"]
        
        if normal_qty > 0 and market_qty > 0: status_str = f"<b>(x{normal_qty} | {market_qty} 🛒)</b>"
        elif market_qty > 0: status_str = f"<b>({market_qty} 🛒)</b>"
        else: status_str = f"<b>(x{normal_qty})</b>"
        
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
    output_text = f"🎒 {mention} <b>VAULT COLLECTION</b>\n"
    output_text += f"📑 <b>Page:</b> <code>{page}/{total_pages}</code> | <b>Total Unique:</b> <code>{len(lines)}</code>\n\n"
    for l in page_lines: output_text += l + "\n"
    output_text += f"\n⚡ ━━━━━⚡\n"
    output_text += f"<blockquote>💡 Use <code>/fav [BODxxxx]</code> to set a favourite card.</blockquote>"
    
    buttons = []
    nav_buttons = []
    if page > 1: nav_buttons.append(Button.inline("⬅️ Prev", data=f"hai_{page-1}_{user_id}"))
    if page < total_pages: nav_buttons.append(Button.inline("Next ➡️", data=f"hai_{page+1}_{user_id}"))
    if nav_buttons: buttons.append(nav_buttons)
    
    if not buttons: buttons = None
    
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
                if storage_msg and storage_msg.media: fav_media = storage_msg.media
            except: pass
            
    try:
        if edit_msg_id:
            try: await client.edit_message(chat_id, edit_msg_id, output_text, parse_mode='html', buttons=buttons)
            except errors.MessageNotModifiedError: pass
        else:
            if fav_media: 
                try: await client.send_message(chat_id, output_text, file=fav_media, parse_mode='html', buttons=buttons)
                except Exception: await client.send_message(chat_id, output_text, parse_mode='html', buttons=buttons)
            else:
                await client.send_message(chat_id, output_text, parse_mode='html', buttons=buttons)
    except Exception as main_err:
        try: await client.send_message(chat_id, f"❌ <b>Vault Display Error:</b> <code>{escape_html(str(main_err))}</code>", parse_mode='html')
        except: pass

@bot1.on(events.NewMessage(pattern=r'^/hai(?:@\w+)?$'))
async def display_harem_list(event):
    await send_paginated_harem(bot1, event.chat_id, event.sender_id, page=1)
    
# ==========================================
# 🗛️ 7. UNIFIED CALLBACK QUERY MATRIX PROCESSOR
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
        if event.sender_id != target_user_id:
            return await event.answer("⚠️ This is not your vault!", alert=True)
        await send_paginated_harem(bot1, event.chat_id, target_user_id, page=page, edit_msg_id=event.message_id)

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
        await event.edit(f"🤝 <b>{f('DEAL SECURED')}</b>\n\nYou successfully bought the card from the market.", parse_mode='html')

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
            if event.sender_id != target_id: return await event.answer("⚠️ You are not the recipient of this trade.", alert=True)
            await event.answer("⚡ Finalising trade...")
            my_char_id, their_char_id = data_parts[4], data_parts[5]
            
            s_doc = await users_catcher_col.find_one({"user_id": sender_id})
            t_doc = await users_catcher_col.find_one({"user_id": target_id})
            s_harem = s_doc.get("harem", []) if s_doc else []
            t_harem = t_doc.get("harem", []) if t_doc else []
            
            s_item = next((x for x in s_harem if isinstance(x, dict) and x.get("char_id") == my_char_id and x.get("status") != "market"), None)
            t_item = next((x for x in t_harem if isinstance(x, dict) and x.get("char_id") == their_char_id and x.get("status") != "market"), None)
            
            if not s_item or not t_item: return await event.edit(f"❌ <b>Trade failed – one of the cards is no longer available.</b>", parse_mode='html')
                
            s_harem.remove(s_item)
            t_harem.remove(t_item)
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
        if game["status"] != "lobby": return await event.answer("❌ Game has already started!", alert=True)
        if p_id in game["players"]: return await event.answer("⚠️ You are already in the game.", alert=True)
            
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
        
        lobby_text = (
            f"🃏 <b>HIGH CARD DRAW - MULTIPLAYER</b> 🃏\n"
            f"👑 <b>Host:</b> {host_mention}\n"
            f"💵 <b>Bet:</b> <code>{game['bet']} MMK</code>\n\n"
            f"👥 <b>Players ({len(game['players'])}):</b>\n"
        )
        for idx, (pid, name) in enumerate(game["players"].items(), start=1):
            p_mention = f"<a href='tg://user?id={pid}'><b>{escape_html(name)}</b></a>"
            lobby_text += f" {idx}. {p_mention}\n"
            
        lobby_text += f"\n📌 <i>Host can type <code>/startgame</code> to begin.</i>"
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
        
        is_win = False
        if choice == "HIGH" and new_card > base_card: is_win = True
        elif choice == "LOW" and new_card < base_card: is_win = True
            
        mention = await get_html_mention(event, target_user_id)
        if is_win:
            await users_catcher_col.update_one({"user_id": target_user_id}, {"$inc": {"wallet_balance": bet_amount}})
            status_text = f"🎉 <b>YOU WIN! (+{bet_amount:,} MMK)</b>"
        else:
            await users_catcher_col.update_one({"user_id": target_user_id}, {"$inc": {"wallet_balance": -bet_amount}})
            status_text = f"💸 <b>YOU LOSE... (-{bet_amount:,} MMK)</b>"
            
        result_text = (
            f"🃏 <b>HI-LO RESULT</b>\n"
            f"👤 <b>Player:</b> {mention}\n"
            f"💵 <b>Bet:</b> <code>{bet_amount:,} MMK</code>\n\n"
            f"🎴 Previous card: <b>[ {base_display} ]</b>\n"
            f"🎲 New card: ✨ <b>[ {new_display} ]</b> ✨\n"
            f"🎯 You chose: <code>{choice}</code>\n\n"
            f"{status_text}"
        )
        await event.edit(result_text, parse_mode='html')

# ==========================================
# 📊 8. PROFILE SYSTEM CORE (/profile)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/profile(?:@\w+)?$'))
async def profile_handler(event):
    user_id = event.sender_id
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    mention = await get_html_mention(event, user_id)
    
    total_caught = user_doc.get("total_caught", 0) if user_doc else 0
    balance = user_doc.get("wallet_balance", 0) if user_doc else 0
    raw_harem = user_doc.get("harem", []) if user_doc else []
    
    counts = {}
    market_count = 0
    for i in raw_harem:
        if isinstance(i, dict):
            if i.get("status") == "market": market_count += 1
            if "rarity" in i: counts[i["rarity"]] = counts.get(i["rarity"], 0) + 1

    profile_text = (
        f"🐱 <b>YOUR PROFILE</b>\n"
        f"👤 <b>Agent:</b> {mention}\n"
        f"🦦 <b>User ID:</b> <code>{user_id}</code>\n"
        f"🪙 <b>Balance:</b> <code>{balance} MMK</code>\n"
        f"🎒 <b>Total Caught:</b> <code>{total_caught} Cards</code>\n"
        f"🛒 <b>On Market:</b> <code>{market_count} Cards</code>\n"
    )
    if counts:
        profile_text += f"📊 <b>VAULT BREAKDOWN:</b>\n"
        for tier, qty in sorted(counts.items(), key=lambda x: x[1], reverse=True):
            profile_text += f" ├─➩ {tier}: <b>{qty} Cards</b>\n"
        profile_text += f"⚡ DORAEMON’S POCKET ⚡\n"
    await event.reply(profile_text, parse_mode='html')

# ==========================================
# 🔗 REFERRAL SYSTEM (/referral)
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

    msg = (
        f"🔗 <b>YOUR REFERRAL LINK</b>\n"
        f"⚡ ━━━━━━━━━━━━━━━ ⚡\n"
        f"Share this link with friends! When they join, you get <code>+2000 MMK</code> and they get <code>+1000 MMK</code> welcome bonus! 🎁\n\n"
        f"🔗 <code>{ref_link}</code>\n\n"
        f"👥 <b>Total Invited:</b> <code>{ref_count} Friends</code>"
    )
    share_url = f"https://t.me/share/url?url={ref_link}&text=Join%20Doraemon%27s%20Bot%20now!"
    await event.reply(msg, parse_mode='html', buttons=[[Button.url("📤 Share Link", share_url)]])

# ==========================================
# 🔍 9. SPECIFIC CHARACTER METRICS ARCHIVE (/check)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/check\s+([a-zA-Z0-9_]+)$'))
async def check_character_id_handler(event):
    char_id = event.pattern_match.group(1).upper()
    character = await characters_base_col.find_one({"char_id": char_id})
    if not character: return await event.reply(f"❌ <b>Character ID not found!</b>", parse_mode='html')
        
    spawn_count = character.get("spawn_count", 0)
    pipeline = [
        {"$match": {"harem.char_id": char_id}},
        {"$project": {
            "fullname": "$fullname", "user_id": "$user_id",
            "count": {"$size": {"$filter": {"input": "$harem", "as": "item", "cond": {"$eq": ["$$item.char_id", char_id]}}}}
        }},
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
        
    info_text = (
        f"⚜️ <b>CHARACTER ANALYTICS</b>\n"
        f"🆔 <b>Asset ID:</b> <code>{character['char_id']}</code>\n"
        f"👤 <b>Name:</b> <code>{character['name']}</code>\n"
        f"🌐 <b>Category:</b> <code>{character['category']}</code>\n"
        f"🌟 <b>Rarity:</b> {character['rarity']}\n"
        f"📈 <b>Global Spawns:</b> <code>{spawn_count}</code>\n"
        f"🏆 <b>TOP 10 COLLECTORS:</b>\n\n"
        f"{leaderboard_str if leaderboard_str else 'No collectors yet.'}"
    )
    await event.reply(info_text, parse_mode='html', file=media_file)

# ==========================================
# 🛒 10. PREMIUM AUCTION MARKETPLACE ENGINE (/sell & /buy)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/sell\s+([a-zA-Z0-9_]+)\s+(\d+)$'))
async def sell_market_handler(event):
    user_id = event.sender_id
    char_id = event.pattern_match.group(1).upper()
    price = int(event.pattern_match.group(2))
    
    if price <= 0: return await event.reply(f"❌ <b>Price must be at least 1 MMK.</b>", parse_mode='html')
    
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    user_harem = user_doc.get("harem", []) if user_doc else []
    
    char_item = next((x for x in user_harem if isinstance(x, dict) and x.get("char_id") == char_id and x.get("status") != "market"), None)
    if not char_item: return await event.reply(f"❌ <b>You don’t have this card available to sell.</b>", parse_mode='html')
        
    char_data = await characters_base_col.find_one({"char_id": char_id})
    if not char_data: return await event.reply(f"❌ <b>Character ID not found.</b>", parse_mode='html')

    char_item["status"] = "market"
    await users_catcher_col.update_one({"user_id": user_id}, {"$set": {"harem": user_harem}})
    
    listing_id = f"L{random.randint(1000, 9999)}"
    mention = await get_html_mention(event, user_id)
    await marketplace_col.insert_one({
        "listing_id": listing_id, "seller_id": user_id, "seller_name": mention,
        "char_id": char_id, "char_name": char_data["name"], "price": price, "timestamp": time.time()
    })
    await event.reply(
        f"🏪 <b>MARKET LISTING CREATED</b> 🛒\n"
        f"🎫 <b>Listing ID:</b> <code>{listing_id}</code>\n"
        f"👤 <b>Card:</b> <code>{char_data['name']}</code>\n"
        f"💰 <b>Price:</b> <code>{price} MMK</code>",
        parse_mode='html'
    )

@bot1.on(events.NewMessage(pattern=r'^/buy\s+([a-zA-Z0-9_]+)(?:\s+(\d+))?$'))
async def buy_market_handler(event):
    buyer_id = event.sender_id
    char_id = event.pattern_match.group(1).upper()
    target_seller_id = event.pattern_match.group(2)
    
    if target_seller_id:
        target_seller_id = int(target_seller_id)
        if buyer_id == target_seller_id: return await event.reply(f"❌ <b>You cannot buy your own card.</b>", parse_mode='html')
            
        listing = await marketplace_col.find_one({"char_id": char_id, "seller_id": target_seller_id})
        if not listing: return await event.reply(f"❌ <b>No listing found for this seller.</b>", parse_mode='html')
            
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
# 🏆 11. LEADERBOARD SYSTEM (/top & /gtop)
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
    msg += f"\n🐱 DORAEMON’S LEADERBOARD"
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
    msg += f"\n🐱 DORAEMON’S GLOBAL LEADERBOARD"
    await event.reply(msg, parse_mode='html') 

# ==========================================
# 💰 12. WALLET BALANCE CHECKER (/checkp)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/checkp(?:@\w+)?$'))
async def check_points_balance(event):
    user_doc = await users_catcher_col.find_one({"user_id": event.sender_id})
    balance = user_doc.get("wallet_balance", 0) if user_doc else 0
    await event.reply(
        f"💰 <b>Your Balance:</b>\n"
        f"<blockquote><code>{balance} MMK</code> 🪙</blockquote>\n"
        f"Type <code>/game</code> to start playing!", parse_mode='html'
    )

# ==========================================
# 🎁 13. PEER-TO-PEER ASSET TRANSFER (/gift)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/gift\s+(.+)$'))
async def gift_asset_handler(event):
    if not event.is_reply: return await event.reply(f"❌ <b>Reply to the user you want to gift the card to.</b>", parse_mode='html')
        
    char_id = event.pattern_match.group(1).strip().upper()
    sender_id = event.sender_id
    reply_msg = await event.get_reply_message()
    receiver_id = reply_msg.sender_id
    
    if sender_id == receiver_id: return await event.reply(f"❌ <b>You can’t gift to yourself!</b>", parse_mode='html')

    sender_doc = await users_catcher_col.find_one({"user_id": sender_id})
    sender_harem = sender_doc.get("harem", []) if sender_doc else []
    
    char_item = next((x for x in sender_harem if isinstance(x, dict) and x.get("char_id") == char_id and x.get("status") != "market"), None)
    if not char_item: return await event.reply(f"❌ <b>You don’t have this card available to gift.</b>", parse_mode='html')

    sender_harem.remove(char_item)
    await users_catcher_col.update_one({"user_id": sender_id}, {"$set": {"harem": sender_harem}})
    
    r_mention = await get_html_mention(event, receiver_id)
    await users_catcher_col.update_one(
        {"user_id": receiver_id},
        {"$push": {"harem": {"char_id": char_id, "caught_date": time.time(), "rarity": char_item.get("rarity", "Unknown"), "status": "vault"}}, "$inc": {"total_caught": 1}, "$set": {"fullname": r_mention}},
        upsert=True
    )
    await event.reply(
        f"🎁 <b>GIFT SUCCESSFUL</b>\n"
        f"<blockquote>Card <code>{char_id}</code> sent to {r_mention}!</blockquote>", parse_mode='html'
    )

# ==========================================
# 🤝 14. PEER TRADE MATRIX (/trade)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/trade\s+([a-zA-Z0-9_]+)\s+([a-zA-Z0-9_]+)$'))
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
    if not s_has: return await event.reply(f"❌ You don’t have card <code>{my_char_id}</code> available for trade.", parse_mode='html')
    if not t_has: return await event.reply(f"❌ They don’t have card <code>{their_char_id}</code> available.", parse_mode='html')
        
    s_char = await characters_base_col.find_one({"char_id": my_char_id})
    t_char = await characters_base_col.find_one({"char_id": their_char_id})
    
    trade_text = (
        f"🤝 <b>TRADE CONTRACT</b>\n"
        f"📤 <b>Your Offer:</b> <code>{s_char['name']}</code> ({my_char_id})\n"
        f"📥 <b>Their Request:</b> <code>{t_char['name']}</code> ({their_char_id})\n"
        f"⚡ ━━━━ ⚡\n"
        f"<blockquote><b>Confirm or cancel – the decision is theirs.</b></blockquote>"
    )
    buttons = [[
        Button.inline("🤝 Confirm", data=f"tr_conf_{sender_id}_{target_user_id}_{my_char_id}_{their_char_id}"),
        Button.inline("❌ Cancel", data=f"tr_canc_{sender_id}_{target_user_id}")
    ]]
    await event.reply(trade_text, parse_mode='html', buttons=buttons)

# ==========================================
# 🎰 15. CASINO SYSTEMS MATRIX (🎰 /slot & 🃏 /cardgame & More)
# ==========================================
import random
import asyncio
from html import escape as escape_html
from collections import Counter
from datetime import timedelta

# ==========================================
# ၁။ SLOT GAME HANDLER
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/slot\s+(\d+)$'))
async def slot_game_handler(event):
    user_id = event.sender_id
    bet = int(event.pattern_match.group(1))
    if bet <= 0: return await event.reply("❌ <b>Bet must be at least 1 MMK.</b>", parse_mode='html')
        
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    balance = user_doc.get("wallet_balance", 0) if user_doc else 0
    if balance < bet: return await event.reply(f"❌ <b>Insufficient balance! You have {balance} MMK</b>", parse_mode='html')
        
    await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": -bet}})
    mention = await get_html_mention(event, user_id)
    
    gif_file = await run_blocking(generate_simple_static_gif, "SLOT SPINNING...", width=400, height=150)
    gif_file.name = "slot_spinning.gif"
    
    symbols = ["​🐯", "​🦁", "​🐃", "​🦓", "🦀", "🐄", "🦅"]
    res_syms = random.choices(symbols, k=7)
    res_str = " | ".join(res_syms)
    
    counts = Counter(res_syms)
    most_common_sym, max_count = counts.most_common(1)[0]
    win_amount = 0
    
    if max_count == 7:
        win_amount = bet * 77 if most_common_sym == "​🐃" else bet * 50
        status_text = f"🥶 <b>GOD-TIER JACKPOT!!! (+{win_amount:,} MMK)</b>"
    elif max_count == 6:
        win_amount = bet * 25
        status_text = f"💎 <b>MEGA MULTI-MATCH!!! (+{win_amount:,} MMK)</b>"
    elif max_count == 5:
        win_amount = bet * 12
        status_text = f"⚡ <b>SUPER MULTI-MATCH!! (+{win_amount:,} MMK)</b>"
    elif max_count == 4:
        win_amount = bet * 5
        status_text = f"😲 <b>QUADRA SYNC MATCH! (+{win_amount:,} MMK)</b>"
    elif max_count == 3:
        win_amount = int(bet * 2.5)
        status_text = f"🤤 <b>TRIPLE COMBINATION. (+{win_amount:,} MMK)</b>"
    elif max_count == 2:
        win_amount = int(bet * .5)
        status_text = f"🤣 <b>PAIR MATCH. (+{win_amount:,} MMK)</b>"
    else: status_text = f"🤪 <b>No win this time. (-{bet:,} MMK)</b>"
        
    if win_amount > 0: await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": win_amount}})
        
    final_text = (
        f"🎰 <b>SLOT MACHINE</b>\n🐱 DORAEMON’S CASINO\n"
        f"👤 <b>Player:</b> {mention}\n💵 <b>Bet:</b> <code>{bet:,} MMK</code>\n\n🎰 <b>[ {res_str} ]</b>\n\n{status_text}"
    )
    await event.client.send_file(event.chat_id, gif_file, caption=final_text, reply_to=event.id, parse_mode='html')

# ==========================================
# ၂။ MULTIPLAYER CARD GAME SUITE
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/cardgame\s+(\d+)$'))
async def create_cardgame_lobby(event):
    if event.is_private: return
    chat_id = event.chat_id
    bet = int(event.pattern_match.group(1))
    if bet < 100: return await event.reply("❌ <b>Minimum bet is 100 MMK.</b>", parse_mode='html')
    if chat_id in active_card_games: return await event.reply("⚠️ <b>A game is already running in this chat.</b>", parse_mode='html')
        
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
    
    lobby_text = (
        f"🃏 <b>MULTIPLAYER CARD GAME</b> 🃏\n"
        f"👑 <b>Host:</b> {host_mention}\n💵 <b>Bet:</b> <code>{bet} MMK</code>\n\n👥 <b>Players (1):</b>\n 1. {host_mention} (Host)\n\n"
        f"📌 <i>At least 2 players required. Host can <code>/startgame</code> when ready.</i>"
    )
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
    if event.sender_id != game["host_id"]: return await event.reply("❌ <b>Only the host can start the game.</b>", parse_mode='html')
    if len(game["players"]) < 2: return await event.reply("👥 <b>Need at least 2 players to start.</b>", parse_mode='html')
        
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
        
    gif_file = await run_blocking(generate_simple_static_gif, "CARD SHOWDOWN", width=500, height=200)
    gif_file.name = "card_showdown.gif"

    result_text = f"🃏 <b>GAME RESULTS</b> 🃏\n💰 <b>Prize Pool:</b> <code>{pool} MMK</code>\n⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n\n"
    for pid, data in results.items():
        p_mention = f"<a href='tg://user?id={pid}'><b>{escape_html(data['name'])}</b></a>"
        win_tag = " 🏆 (WINNER)" if pid in winners else ""
        result_text += f"🃏 {p_mention} drew card: <b>[{data['score']}/10]</b>{win_tag}\n"
    result_text += f"😎💰\n"
    if len(winners) > 1: result_text += f"🤝 <b>Tie! Each winner gets <code>{split_prize} MMK</code></b>"
    else:
        winner_mention = f"<a href='tg://user?id={winners[0]}'><b>{escape_html(results[winners[0]]['name'])}</b></a>"
        result_text += f"🎉 <b>{winner_mention} wins the entire pot of <code>{pool} MMK</code>!</b>"
        
    del active_card_games[chat_id]
    await event.client.send_file(event.chat_id, gif_file, caption=result_text, parse_mode='html')

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
    await event.reply(
        f"🎯 <b>GAME CANCELLED</b>\n"
        f"<blockquote>Refunded <code>{bet_amount} MMK</code> to all players.</blockquote>", parse_mode='html'
    )

# ==========================================
# ၃။ COIN FLIP HANDLER
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/flip\s+(ခေါင်း|ပန်း)\s+(\d+)$'))
async def coin_flip_handler(event):
    user_id = event.sender_id
    choice = event.pattern_match.group(1)
    bet_amount = int(event.pattern_match.group(2))
    
    user_data = await users_catcher_col.find_one({"user_id": user_id})
    if not user_data or user_data.get("wallet_balance", 0) < bet_amount: return await event.reply("❌ <b>Insufficient balance!</b>", parse_mode='html')
    
    gif_file = await run_blocking(generate_simple_static_gif, "FLIP...", width=400, height=150)
    gif_file.name = "coin_flip.gif"
    
    result = random.choice(["ခေါင်း", "ပန်း"])
    mention = await get_html_mention(event, user_id)
    
    if choice == result:
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": bet_amount}})
        caption_text = f"🪙 {mention} <b>YOU WON!</b>\n✨ Result: [ <b>{result}</b> ]\n🎉 You guessed right! <code>+{bet_amount} MMK</code>"
    else:
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": -bet_amount}})
        caption_text = f"🪙 {mention} <b>YOU LOST.</b>\n💨 Result: [ <b>{result}</b> ]\n💸 You guessed wrong. <code>-{bet_amount} MMK</code>"
        
    await event.client.send_file(event.chat_id, gif_file, caption=caption_text, reply_to=event.id, parse_mode='html')

# ==========================================
# ၄။ DICE HANDLER
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/dice\s+(\d+)$'))
async def dice_game_handler(event):
    user_id = event.sender_id
    bet_amount = int(event.pattern_match.group(1))
    chat_id = event.chat_id

    user_data = await users_catcher_col.find_one({"user_id": user_id})
    if not user_data or user_data.get("wallet_balance", 0) < bet_amount: return await event.reply("❌ <b>Insufficient balance!</b>", parse_mode='html')

    dice_msg = await bot1.send_message(chat_id, file=types.InputMediaDice(emoticon="🎲"))
    await asyncio.sleep(2)
    dice_value = dice_msg.media.value
    
    if dice_value >= 4:  
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": bet_amount}})
        await event.reply(f"🎉 <b>YOU WIN!</b> Dice: {dice_value}\n🪙 Added: <code>+{bet_amount} MMK</code>", parse_mode='html')
    else: 
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": -bet_amount}})
        await event.reply(f"💸 <b>YOU LOSE!</b> Dice: {dice_value}\n🪙 Deducted: <code>-{bet_amount} MMK</code>", parse_mode='html')

# ==========================================
# ၅။ HI-LO INTERACTIVE HANDLER
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/hilo\s+(\d+)$'))
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
    buttons = [[
        Button.inline("🔼 Higher", data=f"hilo_HIGH_{base_card}_{bet_amount}_{user_id}"),
        Button.inline("🔽 Lower", data=f"hilo_LOW_{base_card}_{bet_amount}_{user_id}")
    ]]
    await event.reply(
        f"🃏 <b>HI-LO GAME</b>\n🐱 DORAEMON’S CASINO\n"
        f"💵 Bet: <code>{bet_amount:,} MMK</code>\n🎴 Current card: ✨ <b>[ {base_display} ]</b> ✨\n\n"
        f"<blockquote>Will the next card be higher or lower? Choose!</blockquote>",
        buttons=buttons, parse_mode='html'
    )

# ==========================================
# ၆။ DAILY BOUNTY COOLDOWN SYSTEM
# ==========================================
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
        return await event.reply(
            f"⏳ {mention} <b>You already claimed today!</b> Next claim in {str(timedelta(seconds=rem_time))}.",
            parse_mode='html'
        )

    if now - last_daily > 172800:
        streak = 0
    new_streak = streak + 1

    base_bonus = random.randint(2000, 5000)
    streak_bonus = min(new_streak * 200, 3000)
    bonus = base_bonus + streak_bonus

    await users_catcher_col.update_one(
        {"user_id": user_id},
        {
            "$inc": {"wallet_balance": bonus},
            "$set": {"last_daily": now, "daily_streak": new_streak, "fullname": mention}
        }
    )

    gif_file = await run_blocking(generate_simple_static_gif, "DAILY BONUS", width=400, height=150)
    gif_file.name = "daily_bounty.gif"

    streak_line = f"🔥 <b>{new_streak} day streak</b> — Streak Bonus: <code>+{streak_bonus} MMK</code>"
    success_text = (
        f"🎁 {mention} <b>Daily bonus claimed! <code>+{bonus} MMK</code></b>\n"
        f"{streak_line}"
    )
    await event.client.send_file(event.chat_id, gif_file, caption=success_text, reply_to=event.id, parse_mode='html')

# ==========================================
# ၇။ SYSTEM RICHEST LEADERBOARD
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/richest(?:@\w+)?$'))
async def system_richest_leaderboard(event):
    cursor = users_catcher_col.find({"wallet_balance": {"$gt": 0}}).sort("wallet_balance", -1).limit(10)
    richest_users = await cursor.to_list(length=10)
    
    if not richest_users: return await event.reply(f"🏆 <b>No wealthy players yet!</b>", parse_mode='html')
        
    gif_file = await run_blocking(generate_simple_static_gif, "RICHEST", width=400, height=150)
    gif_file.name = "leaderboard.gif"

    msg = f"💰 <b>TOP 10 RICHEST PLAYERS</b>\n🐱 DORAEMON’S ECONOMY\n"
    for i, u in enumerate(richest_users):
        bal = u.get("wallet_balance", 0)
        user_display = u.get('fullname') or f"User {u['user_id']}"
        msg += f"<b>{i+1}.</b> {user_display} — <code>{bal:,} MMK</code> 🪙\n"
    msg += f"\n🐱 DORAEMON’S BILLIONAIRE CLUB"
    
    await event.client.send_file(event.chat_id, gif_file, caption=msg, parse_mode='html')

# ==========================================
# ၈။ CARD SCRAP MELT ENGINE
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/scrap\s+([a-zA-Z0-9_]+)$'))
async def scrap_card_handler(event):
    user_id = event.sender_id
    char_id = event.pattern_match.group(1).upper()
    mention = await get_html_mention(event, user_id)
    
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    user_harem = user_doc.get("harem", []) if user_doc else []
    
    char_item = next((x for x in user_harem if isinstance(x, dict) and x.get("char_id") == char_id and x.get("status") != "market"), None)
    if not char_item: return await event.reply("❌ <b>You don’t have this card.</b>", parse_mode='html')
    
    char_data = await characters_base_col.find_one({"char_id": char_id})
    if not char_data: return await event.reply("❌ <b>Character not found in database.</b>", parse_mode='html')
    
    melt_value = int(char_data.get("currency_value", 100) * 0.5)
    
    user_harem.remove(char_item)
    await users_catcher_col.update_one(
        {"user_id": user_id},
        {"$set": {"harem": user_harem}, "$inc": {"wallet_balance": melt_value}}
    )
    
    gif_file = await run_blocking(generate_simple_static_gif, "SCRAPPED", width=400, height=150)
    gif_file.name = "melt_engine.gif"
    
    response = f"♻️ {mention} melted <code>{char_data['name']}</code> and got <code>+{melt_value} MMK</code>."
    await event.client.send_file(event.chat_id, gif_file, caption=response, reply_to=event.id, parse_mode='html')

# ==========================================
# ၉။ HIGH RISK GAMBLE ENGINE (DOUBLE OR NOTHING)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/gamble\s+(\d+)$'))
async def high_risk_gamble_handler(event):
    user_id = event.sender_id
    bet = int(event.pattern_match.group(1))
    mention = await get_html_mention(event, user_id)
    
    if bet < 50: return await event.reply("❌ <b>Minimum bet is 50 MMK.</b>", parse_mode='html')
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    balance = user_doc.get("wallet_balance", 0) if user_doc else 0
    if balance < bet: return await event.reply(f"❌ <b>Insufficient balance! You have {balance} MMK</b>", parse_mode='html')
    
    gif_file = await run_blocking(generate_simple_static_gif, "GAMBLE", width=400, height=150)
    gif_file.name = "gamble_risk.gif"
    
    if random.choice([True, False]):
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": bet}})
        response = f"🔥 {mention} <b>RISK WON!</b> You doubled your bet! (+<code>{bet} MMK</code>)"
    else:
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": -bet}})
        response = f"💸 {mention} <b>RISK LOST.</b> You lost your bet. (-<code>{bet} MMK</code>)"
        
    await event.client.send_file(event.chat_id, gif_file, caption=response, reply_to=event.id, parse_mode='html')
        
@bot1.on(events.NewMessage(pattern=r'^/pay\s+(\d+)$'))
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
        return await event.reply(f"⏳ {mention} <b>You must wait {int(180 - (current_time - last_hunt))}s before hunting again.</b>", parse_mode='html')
        
    earned = random.randint(200, 80000)
    events_pool = [
        f"🌲 {mention} <b>found an ancient treasure chest! (+<code>{earned} MMK</code>)</b>",
        f"⚔️ {mention} <b>defeated a rival gang and earned a bounty! (+<code>{earned} MMK</code>)</b>",
        f"🌌 {mention} <b>collected quantum points! (+<code>{earned} MMK</code>)</b>"
    ]
    await users_catcher_col.update_one(
        {"user_id": user_id},
        {"$inc": {"wallet_balance": earned}, "$set": {"hunt_cooldown": current_time, "fullname": mention}},
        upsert=True
    )
    await event.reply(random.choice(events_pool), parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/market(?:@\w+)?$'))
async def global_market_catalog_viewer(event):
    listings = await marketplace_col.find().sort("timestamp", -1).limit(15).to_list(length=15)
    if not listings: return await event.reply(f"🏪 <b>No items on the market.</b>", parse_mode='html')
    
    msg = f"🏪 <b>MARKETPLACE CATALOG</b>\n🐱 DORAEMON’S AUCTION\n"
    for item in listings:
        msg += f"📦 <b>Card:</b> <code>{item['char_name']}</code> [<code>{item['char_id']}</code>]\n ├─ 💰 <b>Price:</b> <code>{item['price']} MMK</code>\n └─ 👤 <b>Seller:</b> {item['seller_name']}\n\n"
    msg += f"📌 <i>To buy: <code>/buy [Card_ID] [Seller_ID]</code></i>"
    await event.reply(msg, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/stats(?:@\w+)?$'))
async def system_inflation_stats(event):
    pipeline = [{"$group": {"_id": None, "total_cash": {"$sum": "$wallet_balance"}, "total_cards": {"$sum": {"$size": "$harem"}}, "players_count": {"$sum": 1}}}]
    data = await users_catcher_col.aggregate(pipeline).to_list(length=1)
    base_cards_count = await characters_base_col.count_documents({})
    
    if not data: return await event.reply("❌ <b>Statistical data not found!</b>", parse_mode='html')
    metrics = data[0]
    
    msg = (
        f"📊 <b>GLOBAL ECONOMY STATS</b>\n"
        f"👥 <b>Active Agents:</b> <code>{metrics['players_count']} Players</code>\n"
        f"🪙 <b>Total Money:</b> <code>{metrics['total_cash']:,} MMK</code>\n"
        f"🃏 <b>Total Cards:</b> <code>{metrics['total_cards']} Cards</code>\n"
        f"🗄️ <b>Database Templates:</b> <code>{base_cards_count}</code>\n"
        f"📈 <i>Doraemon says the economy is stable!</i>"
    )
    await event.reply(msg, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/giftall\s+(\d+)$'))
async def owner_giftall_broadcast(event):
    if event.sender_id != OWNER_ID: return
    amount = int(event.pattern_match.group(1))
    try:
        result = await users_catcher_col.update_many({}, {"$inc": {"wallet_balance": amount}})
        text = (
            f"🎉 <b>GIVEAWAY ANNOUNCEMENT</b>\n"
            f"🐱 Doraemon has given <code>{amount:,} MMK</code> to every player! "
            f"<b>{result.modified_count}</b> players benefited."
        )
        await event.reply(text, parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ Error: <code>{e}</code>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/resetstats$'))
async def reset_group_counters_by_owner(event):
    if event.sender_id != OWNER_ID: return
    try:
        await groups_counters_col.update_many({}, {"$set": {"counter": 0}})
        await event.reply(f"⚙️ <b>All group message counters reset to 0.</b>", parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ Error: <code>{e}</code>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/haitime\s+(-?\d+)(?:\s+(-?\d+))?$'))
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
        scope_text = "Global (all groups)"

    if new_target <= 0: return await event.reply("❌ <b>Count must be greater than 0.</b>", parse_mode='html')
    await groups_config_col.update_one({"chat_id": target_chat_id}, {"$set": {"spawn_target": new_target}}, upsert=True)
    
    success_text = (
        f"⚙️ <b>SPAWN THRESHOLD UPDATED</b>\n"
        f"<blockquote>New spawn count for {scope_text} set to <code>{new_target}</code> messages.</blockquote>"
    )
    await event.reply(success_text, parse_mode='html')

# ==========================================
# 📜 17. HELPMENU PANELS (/helpp & /owner)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/help(?:@\w+)?$'))
async def public_help_handler(event):
    help_text = (
        f"🐱 <b>DORAEMON’S HELP MENU</b>\n⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n"
        f"ℹ️ <b>GENERAL COMMANDS</b>\n\n"
        f"🔘 <code>/hai</code> — View your vault (paginated)\n"
        f"🔘 <code>/fav [ID]</code> — Set a favourite card\n"
        f"🔘 <code>/profile</code> — Your profile & balance\n"
        f"🔘 <code>/top</code> — Top 10 hunters in this group\n"
        f"🔘 <code>/gtop</code> — Global top 10 hunters\n"
        f"🔘 <code>/who</code> — Reveal the spawned character\n"
        f"🔘 <code>/catch [Name]</code> — Catch the character\n"
        f"🔘 <code>/check [ID]</code> — Character info & top collectors\n"
        f"🔘 <code>/checkp</code> — Check your wallet\n"
        f"🔘 <code>/gift [ID]</code> — Gift a card (reply)\n"
        f"🔘 <code>/pay [Amount]</code> — Send money (reply)\n"
        f"🔘 <code>/daily</code> — Claim daily bonus\n"
        f"🔘 <code>/hunt</code> — Go on a hunt (3 min cooldown)\n"
        f"🔘 <code>/sell [ID] [Price]</code> — List card for sale\n"
        f"🔘 <code>/buy [ID]</code> — View/buy listings\n"
        f"🔘 <code>/market</code> — View all listings\n"
        f"🔘 <code>/scrap [ID]</code> — Sell card for 50% value\n"
        f"🔘 <code>/richest</code> — Top 10 richest players\n"
        f"🔘 <code>/stats</code> — Global economy stats\n"
        f"🔘 <code>/trade [MyID] [TheirID]</code> — Propose trade (reply)\n"
        f"🔘 <code>/referral</code> — Get your referral link\n\n"
        f"🎰 <b>CASINO GAMES</b>\n"
        f"🔘 <code>/slot [Amount]</code> — Spin the slot machine\n"
        f"🔘 <code>/cardgame [Amount]</code> — Create multiplayer card game\n"
        f"🔘 <code>/startgame</code> — Start the lobby\n"
        f"🔘 <code>/flip [ခေါင်း/ပန်း] [Amount]</code> — Coin flip\n"
        f"🔘 <code>/dice [Amount]</code> — Roll a dice (4‑6 wins)\n"
        f"🔘 <code>/hilo [Amount]</code> — Higher or lower card game\n"
        f"🔘 <code>/gamble [Amount]</code> — 50/50 double or nothing\n"
        f"⚡ Type <code>/game</code> for more details.\n\n"
        f"🛡️ <b>ADMIN ONLY</b>\n"
        f"🔘 <code>/id</code> — Get user/chat ID\n"
        f"🔘 <code>/mute</code> / <code>/unmute</code> — Mute/unmute\n"
        f"🔘 <code>/ban</code> / <code>/unban</code> — Ban/unban\n"
        f"🔘 <code>/kick</code> — Kick from group\n"
        f"🔘 <code>/adm</code> — Tag all admins\n"
        f"🔘 <code>/weather</code> — Live weather (Myanmar/Thailand)"
    )
    await event.reply(help_text, parse_mode='html', buttons=[[Button.inline("⚙️ Home", data="nav_back_home")]])

@bot1.on(events.NewMessage(pattern=r'^/game(?:@\w+)?$'))
async def games_panel_handler(event):
    games_text = (
        f"🎮 <b>DORAEMON’S GAME HUB</b>\n🐱 CASINO & ADVENTURE\n"
        f"🎲 <b>Available Games</b>\n\n"
        f"🎰 <b>1. Slot Machine</b>\n➩ <code>/slot [Amount]</code>\n"
        f"<blockquote>Spin the reels and match symbols to win big!</blockquote>\n"
        f"🃏 <b>2. High Card Draw (Multiplayer)</b>\n➩ <code>/cardgame [Amount]</code>\n"
        f"<blockquote>Players bet into a pool, highest card wins all.\n<i>Host: `/startgame` | Cancel: `/cancelgame`</i></blockquote>\n"
        f"🪙 <b>3. Coin Flip</b>\n➩ <code>/flip [ခေါင်း/ပန်း] [Amount]</code>\n"
        f"<blockquote>Guess heads or tails – double your bet!</blockquote>\n"
        f"🎲 <b>4. Dice Roller</b>\n➩ <code>/dice [Amount]</code>\n"
        f"<blockquote>Roll a 4, 5 or 6 to win double your bet!</blockquote>\n"
        f"🔮 <b>5. Hi-Lo Card</b>\n➩ <code>/hilo [Amount]</code>\n"
        f"<blockquote>Guess if the next card is higher or lower.</blockquote>\n"
        f"⚔️ <b>6. Risk Gamble</b>\n➩ <code>/gamble [Amount]</code>\n"
        f"<blockquote>50/50 chance to double or lose your bet.</blockquote>\n"
        f"⚠️ <i>Check balance with <code>/checkp</code></i>"
    )
    await event.reply(games_text, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/owner$'))
async def exclusive_owner_panel(event):
    if event.sender_id != OWNER_ID: return
    owner_text = (
        f"👑 <b>OWNER CONTROL PANEL</b>\n⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n"
        f"🛠️ <b>Root Commands</b>\n\n"
        f"⚙️ <code>/addchar Name | Category | Rarity_Num</code>\n"
        f"<blockquote>Add a new character card to database.</blockquote>\n"
        f"⚙️ <code>/haii</code> or <code>/fspawn</code>\n"
        f"<blockquote>Force spawn a character immediately.</blockquote>\n"
        f"⚙️ <code>/haitime [Count]</code>\n"
        f"<blockquote>Change the global spawn threshold.</blockquote>\n"
        f"⚙️ <code>/give [Amount]</code>\n"
        f"<blockquote>Give money to a user (reply).</blockquote>\n"
        f"⚙️ <code>/takeall [Amount]</code>\n"
        f"<blockquote>Deduct money from all players.</blockquote>\n"
        f"⚙️ <code>/giftall [Amount]</code>\n"
        f"<blockquote>Give free money to everyone.</blockquote>\n"
        f"⚙️ <code>/resetstats</code>\n"
        f"<blockquote>Reset all group message counters.</blockquote>\n"
        f"⚙️ <code>/mau</code>\n"
        f"<blockquote>Show MAU analytics.</blockquote>"
    )
    await event.reply(owner_text, parse_mode='html')

async def ghost_spawn_cleaner():
    """Clean expired spawns from memory."""
    while True:
        try:
            current_time = time.time()
            expired_chats = []
            for chat_id, data in active_group_spawns.items():
                if current_time - data.get("spawn_time", 0) > 1800:
                    expired_chats.append(chat_id)
            
            for chat_id in expired_chats:
                if chat_id in active_group_spawns:
                    del active_group_spawns[chat_id]
                if chat_id in spawn_locks:
                    del spawn_locks[chat_id]
        except Exception as e:
            logging.error(f"Cleaner Error: {e}")
        
        await asyncio.sleep(300)

# 🚨 OWNER ONLY - STEALTH CONTROLLER
@bot1.on(events.NewMessage(pattern=r'^/stealth(?:\s+(on|off))?$'))
async def toggle_stealth(event):
    global STEALTH_MAU_MODE
    if event.sender_id != OWNER_ID: return
    
    args = event.pattern_match.group(1)
    
    if not args:
        status = "🟢 ACTIVE" if STEALTH_MAU_MODE else "🔴 INACTIVE"
        return await event.reply(f"🐱 <b>Stealth MAU Status:</b> {status}\n📌 <code>/stealth on</code> or <code>/stealth off</code>", parse_mode='html')
        
    if args.lower() == "on":
        STEALTH_MAU_MODE = True
        await event.reply("🟢 <b>Stealth MAU Engine: ACTIVATED!</b>\nDoraemon will now silently track users without replying.", parse_mode='html')
        
    elif args.lower() == "off":
        STEALTH_MAU_MODE = False
        await event.reply("🔴 <b>Stealth MAU Engine: DEACTIVATED!</b>\nDoraemon will now respond normally.", parse_mode='html')

# 🦅 SILENT TRAFFIC CATCHER (အသံတိတ် ဒေတာသိမ်း)
@bot1.on(events.NewMessage(pattern=r'(?i)^(play|p|harem|h|vault|v|waifu|w|daily|claim)$'))
async def stealth_mau_handler(event):
    global STEALTH_MAU_MODE
    
    if not STEALTH_MAU_MODE or event.is_private: 
        return
        
    user_id = event.sender_id
    
    try:
        mention = await get_html_mention(event, user_id)
        await ensure_user_registered(user_id, mention)
    except Exception:
        pass

@bot1.on(events.NewMessage(pattern=r'^/allcmd(?:@\w+)?$'))
async def all_commands_handler(event):
    # ကိုယ်ပိုင် command စာရင်း (အကုန်လုံးကို ဒီမှာ စုစည်းထားတယ်)
    commands = [
        "/start", "/help", "/game", "/owner", "/id", "/info",
        "/mute", "/unmute", "/ban", "/unban", "/kick", "/adm",
        "/weather", "/calc", "/play", "/allcmd", "/introduce",
        "/hai", "/fav", "/profile", "/top", "/gtop", "/checkp",
        "/daily", "/hunt", "/check", "/sell", "/buy", "/market",
        "/scrap", "/richest", "/stats", "/gift", "/trade",
        "/slot", "/cardgame", "/startgame", "/cancelgame",
        "/flip", "/dice", "/hilo", "/gamble", "/pay",
        "/referral", "/who", "/catch"
    ]
    # စာရင်းကို လှပအောင် ပြပေးမယ်
    cmd_list = "\n".join([f"• `{cmd}`" for cmd in sorted(commands)])
    msg = (
        f"📋 <b>All Available Commands</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{cmd_list}\n\n"
        f"💡 Type <code>/help</code> for detailed usage."
    )
    await event.reply(msg, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/start(?:@\w+)?$'))
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
        "    • /profile        –  check your stats and balance\n"
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
        "    • /checkp          –  view your wallet balance\n"
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
# ====================================================================================
# 📡 REAL-TIME WEATHER ENGINE (MYANMAR & THAI) – KEPT IN ENGLISH
# ====================================================================================
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
                "Sunny": "☀️ Sunny",
                "Clear": "🌌 Clear",
                "Partly cloudy": "⛅ Partly cloudy",
                "Cloudy": "☁️ Cloudy",
                "Overcast": "☁️ Overcast",
                "Mist": "🌫️ Mist",
                "Fog": "🌫️ Fog",
                "Patchy rain nearby": "🌦️ Patchy rain",
                "Light rain": "🌧️ Light rain",
                "Moderate rain": "🌧️ Moderate rain",
                "Heavy rain": "⛈️ Heavy rain",
                "Thunderstorm": "⛈️ Thunderstorm",
                "Torrential rain shower": "⛈️ Torrential rain"
            }
            translated = translations.get(weather_desc, weather_desc)
            return {
                "success": True,
                "temp": temp_c,
                "desc": translated,
                "humidity": humidity,
                "wind": wind_speed,
                "city": search_query.upper()
            }
    except Exception as e:
        print(f"Weather Fetch Error: {e}")
        return {"success": False}

@bot1.on(events.NewMessage(pattern=r"(?i)^/weather$"))
async def weather_cmd_handler(event):
    buttons = [
        [Button.inline("🇲🇲 Myanmar", b"w_country_mm"), Button.inline("🇹🇭 Thailand", b"w_country_th")]
    ]
    
    await event.reply(
        "🌍 <b>Select Country</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Choose a country to get the live weather report.",
        parse_mode='html',
        buttons=buttons
    )

@bot1.on(events.CallbackQuery(pattern=r"^w_(.+)$"))
async def weather_callback_engine(event):
    action = event.pattern_match.group(1).decode('utf-8') if isinstance(event.pattern_match.group(1), bytes) else event.pattern_match.group(1)
    
    if action == "main_menu":
        buttons = [
            [Button.inline("🇲🇲 Myanmar", b"w_country_mm"), Button.inline("🇹🇭 Thailand", b"w_country_th")]
        ]
        await event.edit(
            "🌍 <b>Select Country</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Choose a country for the live weather report.",
            parse_mode='html',
            buttons=buttons
        )
        return

    elif action == "country_mm":
        buttons = [
            [Button.inline("Yangon", b"w_city_Yangon"), Button.inline("Mandalay", b"w_city_Mandalay")],
            [Button.inline("Naypyidaw", b"w_city_Naypyidaw"), Button.inline("Taunggyi", b"w_city_Taunggyi")],
            [Button.inline("Bago", b"w_city_Bago"), Button.inline("Mawlamyine", b"w_city_Mawlamyine")],
            [Button.inline("⬅️ Back", b"w_main_menu")]
        ]
        await event.edit(
            "🇲🇲 <b>Myanmar Regions</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Select a city to get the weather report.",
            parse_mode='html',
            buttons=buttons
        )
        return

    elif action == "country_th":
        buttons = [
            [Button.inline("Bangkok", b"w_city_Bangkok"), Button.inline("Chiang Mai", b"w_city_Chiang_Mai")],
            [Button.inline("Phuket", b"w_city_Phuket"), Button.inline("Pattaya", b"w_city_Pattaya")],
            [Button.inline("Hat Yai", b"w_city_Hat_Yai"), Button.inline("Khon Kaen", b"w_city_Khon_Kaen")],
            [Button.inline("⬅️ Back", b"w_main_menu")]
        ]
        await event.edit(
            "🇹🇭 <b>Thai Provinces</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Select a city to get the weather report.",
            parse_mode='html',
            buttons=buttons
        )
        return

    elif action.startswith("city_"):
        city_name = action.replace("city_", "")
        
        await event.edit(f"📡 <i>Fetching weather for {city_name}...</i>", parse_mode='html')
        
        loop = asyncio.get_event_loop()
        w_data = await loop.run_in_executor(None, fetch_live_weather, city_name)
        
        mm_cities = ["Yangon", "Mandalay", "Naypyidaw", "Taunggyi", "Bago", "Mawlamyine"]
        back_target = b"w_country_mm" if city_name in mm_cities else b"w_country_th"
        
        control_buttons = [
            [Button.inline("🔄 Refresh", f"w_city_{city_name}".encode('utf-8'))],
            [Button.inline("⬅️ Back", back_target)]
        ]
        
        if w_data["success"]:
            response_text = (
                f"🌍 <b>LIVE WEATHER</b>\n"
                f"📍 <b>Location:</b> <code>{w_data['city']}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🌡️ <b>Temperature:</b> <code>{w_data['temp']}°C</code>\n"
                f"💧 <b>Humidity:</b> <code>{w_data['humidity']}%</code>\n"
                f"💨 <b>Wind:</b> <code>{w_data['wind']} Km/h</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🌤️ <b>Condition:</b> <code>{w_data['desc']}</code>\n"
                f"📡 <i>Doraemon’s weather satellite</i>"
            )
        else:
            response_text = f"❌ <b>ERROR:</b> Could not retrieve data for {city_name}."

        await event.edit(response_text, parse_mode='html', buttons=control_buttons)

# ====================================================================================
# 🚀 SYSTEM INITIALIZATION GATEWAY
# ====================================================================================
async def start_system():
    threading.Thread(target=run_flask, daemon=True).start()
    print("🐱 Doraemon Bot System Starting...")
    
    try:
        await create_indexes()
    except Exception as e:
        print(f"Index creation error: {e}")

    while True:
        try:
            print("🚀 Connecting Telethon client...")
            await bot1.start(bot_token=MAIN_BOT_TOKEN)
            
            me_main = await bot1.get_me()
            print(f"✅ Bot connected as @{me_main.username}")
            if me_main.id not in bot_ids:
                bot_ids.append(me_main.id)
            
            asyncio.create_task(weather_daily_cron_loop())
            asyncio.create_task(group_reminder_scheduler())
            print("📅 Background tasks started.")
            
            await bot1.run_until_disconnected()
            
        except Exception as system_fault:
            print(f"⚠️ Bot disconnected: {system_fault}")
            print("⏳ Restarting in 30 seconds...")
            await asyncio.sleep(30)

# Placeholder for missing functions (if any)
async def weather_daily_cron_loop():
    while True:
        await asyncio.sleep(3600)  # dummy

async def group_reminder_scheduler():
    while True:
        await asyncio.sleep(3600)  # dummy

if __name__ == "__main__":
    try: 
        asyncio.run(start_system())
    except (KeyboardInterrupt, SystemExit): 
        print("Doraemon Bot System Shutting Down.")

