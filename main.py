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
    """Converts regular English text to premium Bold Sans-Serif Unicode Font"""
    normal = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    bold = "𝙰𝙱𝙲𝙳𝙴𝙵𝙶𝙷𝙸𝙹𝙺𝙻𝙼𝙽𝙾𝙿𝚀𝚁𝚂𝚃𝚄𝚅𝚆𝚇𝚈𝚉𝚊𝚋𝚌𝚍𝚎𝚏𝚐𝚑𝚒𝚓𝚔𝚕𝚖𝚗𝚘𝚙𝚚𝚛𝚜𝚝𝚞𝚟𝚠𝚡𝚢𝚣0123456789"
    trans = str.maketrans(normal, bold)
    return text.translate(trans)

async def ensure_user_registered(user_id, fullname):
    """User က စနစ်ထဲမှာ ရှိမရှိစစ်ပြီး မရှိသေးရင် Welcome Bonus နဲ့အတူ အလိုအလျောက် အကောင့်ဖွင့်ပေးမည့် Function"""
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
                "hunt_cooldown": 0
            }
        },
        upsert=True
    )

# ==========================================
# 🛡️ FLOOD WAIT PROTECTION ENGINE
# ==========================================
async def send_safe_message(client, chat_id, text, **kwargs):
    """FloodWait မိရင် အလိုအလျောက် စောင့်ပြီးမှ ပို့ပေးမည့် Safe Message Function"""
    while True:
        try:
            return await client.send_message(chat_id, text, **kwargs)
        except FloodWaitError as e:
            logging.warning(f"⚠️ Telegram FloodWait Hit! Sleeping for {e.seconds} seconds...")
            await asyncio.sleep(e.seconds)  # Telegram တောင်းဆိုတဲ့ စက္ကန့်အတိုင်း အလိုအလျောက် Sleep သွားမည်
        except Exception as e:
            logging.error(f"❌ Unexpected Error in send_safe_message: {e}")
            raise e

async def send_safe_file(client, chat_id, file, **kwargs):
    """Spawn တဲ့အခါ ပုံ/ဗီဒီယိုတွေ ပို့ရင် FloodWait မမိအောင် ကာကွယ်ပေးမည့် Function"""
    while True:
        try:
            return await client.send_file(chat_id, file, **kwargs)
        except FloodWaitError as e:
            logging.warning(f"⚠️ Telegram FloodWait Hit during File Upload! Sleeping for {e.seconds} seconds...")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logging.error(f"❌ Unexpected Error in send_safe_file: {e}")
            raise e
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
            f"🚨 <b>CRITICAL SYSTEM ERROR REPORT</b>\n"
            f"🛸 ───────────────────\n"
            f"📍 <b>Location:</b> <code>{escape_html(location)}</code>\n"
            f"❌ <b>Error:</b> <code>{escape_html(str(error_msg))}</code>\n"
            f"⏰ <b>Time:</b> <code>{datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}</code>"
        )
        await bot1.send_message(OWNER_ID, bq(alert_text), parse_mode='html')
    except Exception: pass

# 🚀 Mute သက်တမ်း မှတ်သားရန် Function အသစ်
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
def home(): return "BoD Sovereign System Plus Engine is Active!"

def run_flask(): 
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.ERROR)

# ==========================================
# ⚙️ SYSTEM CONFIGURATIONS & CREDENTIALS
# ==========================================
OWNER_ID = 6015356597
MONGO_URI = "mongodb+srv://khantphyoemin537_db_user:9VRKiaeZkz7rJdpz@cluster0.w6tgi8j.mongodb.net/?appName=Cluster0&tlsAllowInvalidCertificates=true"
APP_ID = 35766004
APP_HASH = 'd15b4226b81724722279bae6af69e22d'
MAIN_BOT_TOKEN = "8575371720:AAH22-_s80jy96bO0Xek6-8xLUa91FN1GMc"
SPECIFIC_CONTROL_GROUP = -1003940667453
SPECIFIC_GROUP = -1003580630981 
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
    print("⚡ Creating Database Indexes for High-Speed Performance...")
    await users_col.create_index("user_id", unique=True)
    await users_catcher_col.create_index("user_id", unique=True)
    await groups_col.create_index("chat_id", unique=True)
    # reply_save_col ထဲက trigger field ကို Index (မာတိကာအညွှန်း) ဆောက်ပေးခြင်း
    await reply_save_col.create_index("trigger")
    
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
# ==========================================
# 🐉 PERMANENT DRAGON ASCII GIF GENERATOR
# ==========================================
def generate_permanent_dragon_gif(target_name):
    frames_data = []
    
    # ရာသက်ပန် ပိတ်သိပ်ခြင်းအတွက် ၄-Frame နဂါး ကကွက်
    frame_1 = (
        f"[!] SYSTEM: PERMANENT MUTE INITIATED\n"
        f"👤 Target: {target_name}\n\n"
        f"     <>_/\_/\_/\_<>   _\n"
        f"     <  o     o  >_//_X  *Waking Up*\n"
        f"     <>~~\_/\_/\_<>\n\n"
        f"[ COLD ENGINE STARTING... ]"
    )
    
    frame_2 = (
        f"[⚡] ERASING VOICE FREQUENCY...\n"
        f"⏳ Duration: PERMANENT (ရာသက်ပန်)\n\n"
        f"      <>_/\_/\_/\_<>   _\n"
        f"    ==<  @     @  >_//_X  *Target Locked*\n"
        f"      <>~~\_/\_/\_<>\n\n"
        f"[ SPELL POWER: MAXIMIZING ]"
    )
    
    frame_3 = (
        f"[🔥] INFLICTING INFINITE SILENCE SPELL!!!\n"
        f"💥 Status: Banishing Chat Privileges...\n\n"
        f"      <>_/\_/\_/\_<>   _\n"
        f"  >>>><  #     #  >_//_X =====>> 🔥🔥🔥\n"
        f"      <>~~\_/\_/\_<>\n\n"
        f"[ CLOSING COMMUNICATION CHANNELS ]"
    )
    
    frame_4 = (
        f"[💀] OPERATION COMPLETE: FOREVER MUTED!\n"
        f"🛡️ Status: Permanently Restricted\n\n"
        f"     <>_/\_/\_/\_<>   _\n"
        f"     <  X     X  >_//_X  [GAME OVER]\n"
        f"     <>~~\_/\_/\_<>\n\n"
        f"[ STATUS: TARGET IS SILENCED FOREVER ]"
    )

    all_frames = [frame_1, frame_2, frame_3, frame_4]
    
    try:
        font = ImageFont.truetype("Courier-Bold.ttf", 18)
    except:
        font = ImageFont.load_default()

    for text in all_frames:
        img = Image.new('RGB', (600, 260), color=(15, 15, 15))
        d = ImageDraw.Draw(img)
        d.text((25, 20), text, fill=(0, 255, 65), font=font)
        frames_data.append(np.array(img))
        
    gif_buffer = io.BytesIO()
    imageio.mimsave(gif_buffer, frames_data, format='GIF', fps=2)
    gif_buffer.seek(0)
    return gif_buffer

def generate_mod_dragon_gif(target_name, action_type):
    frames_data = []
    all_frames = []
    
    # 🔊 ၁။ UNMUTE အီဖက်ခ်
    if action_type == 'unmute':
        all_frames = [
            f"[!] SYSTEM: VOICE RESTORATION PROTOCOL\n👤 Target: {target_name}\n\n     <>_/\_/\_/\_<>   _\n     <  -     -  >_//_X  *Checking Registry*\n     <>~~\_/\_/\_<>\n\n[ DECRYPTING SPEECH CHANNELS... ]",
            f"[⚡] UNLOCKING AUDIO FILTERS...\n👤 Target: {target_name}\n\n      <>_/\_/\_/\_<>   _\n    ==<  o     o  >_//_X  *Syncing*\n      <>~~\_/\_/\_<>\n\n[ REMOVING MUTED REGISTRY KEY ]",
            f"[🔥] FLOODING RESTRICTIONS REMOVED!!!\n💥 Status: Restoring Audio Privileges...\n\n      <>_/\_/\_/\_<>   _\n  >>>><  O     O  >_//_X =====>> 🔊🔊🔊\n      <>~~\_/\_/\_<>\n\n[ INJECTING VOICE CODECS: 100% ]",
            f"[🔊] OPERATION COMPLETE: VOICE RESTORED!\n⚡ Status: Restriction Cleared\n\n     <>_/\_/\_/\_<>   _\n     <  ^     ^  >_//_X  [CLEARED]\n     <>~~\_/\_/\_<>\n\n[ TARGET CAN NOW CHAT FREELY ]"
        ]
    # 🚫 ၂။ BAN အီဖက်ခ်
    elif action_type == 'ban':
        all_frames = [
            f"[!] SYSTEM: EXILE PROTOCOL ACTIVE\n🚫 Target: {target_name}\n\n     <>_/\_/\_/\_<>   _\n     <  o     o  >_//_X  *Locating User*\n     <>~~\_/\_/\_<>\n\n[ PARSING USER SYSTEM DATA... ]",
            f"[⚡] ERASING IDENTITY FROM REALM...\n🚫 Target: {target_name}\n\n      <>_/\_/\_/\_<>   _\n    ==<  @     @  >_//_X  *Target Locked*\n      <>~~\_/\_/\_<>\n\n[ BANISHMENT LEVEL: MAXIMUM ]",
            f"[🔥] BANISHING OUT OF THE SERVER!!!\n💥 Status: Purging Chat Access...\n\n      <>_/\_/\_/\_<>   _\n  >>>><  #     #  >_//_X =====>> 💀💀💀\n      <>~~\_/\_/\_<>\n\n[ DESTROYING SESSION TOKENS ]",
            f"[💀] OPERATION COMPLETE: BANISHED!\n🚫 Status: Permanently Exiled\n\n     <>_/\_/\_/\_<>   _\n     <  X     X  >_//_X  [GAME OVER]\n     <>~~\_/\_/\_<>\n\n[ STATUS: USER IS BLOCKED FOREVER ]"
        ]
    # ✅ ၃။ UNBAN အီဖက်ခ်
    elif action_type == 'unban':
        all_frames = [
            f"[!] SYSTEM: PARDON ENGINE RUNNING\n👤 Target: {target_name}\n\n     <>_/\_/\_/\_<>   _\n     <  -     -  >_//_X  *Checking Files*\n     <>~~\_/\_/\_<>\n\n[ RE-EVALUATING ACCESS RIGHTS ]",
            f"[⚡] GRANTING REALM ENTRY PERMIT...\n✅ Target: {target_name}\n\n      <>_/\_/\_/\_<>   _\n    ==<  o     o  >_//_X  *Lifting Block*\n      <>~~\_/\_/\_<>\n\n[ RE-ACTIVATING ACCOUNT ROUTE ]",
            f"[🔥] SHATTERING THE BAN CHAINS!!!\n💥 Status: Clearing Restriction Logs...\n\n      <>_/\_/\_/\_<>   _\n  >>>><  .     .  >_//_X =====>> ✨✨✨\n      <>~~\_/\_/\_<>\n\n[ INJECTING RE-ENTRY LICENSES ]",
            f"[✅] OPERATION COMPLETE: UNBANNED!\n🎉 Status: Ban Lifted Successfully\n\n     <>_/\_/\_/\_<>   _\n     <  ^     ^  >_//_X  [WELCOME BACK]\n     <>~~\_/\_/\_<>\n\n[ TARGET IS ALLOWED TO JOIN AGAIN ]"
        ]
    # 💨 ၄။ KICK အီဖက်ခ်
    elif action_type == 'kick':
        all_frames = [
            f"[!] SYSTEM: EJECTION SEQUENCE READY\n👤 Target: {target_name}\n\n     <>_/\_/\_/\_<>   _\n     <  o     o  >_//_X  *Targeting*\n     <>~~\_/\_/\_<>\n\n[ INITIATING SOCKET FLUSH... ]",
            f"[⚡] TERMINATING MEMBER SESSION...\n💨 Target: {target_name}\n\n      <>_/\_/\_/\_<>   _\n    ==<  @     @  >_//_X  *Disconnecting*\n      <>~~\_/\_/\_<>\n\n[ DISCONNECT FORCE: 100% ]",
            f"[🔥] JETTISONING OUT OF CHAT ROOM!!!\n💥 Status: Force Removing User...\n\n      <>_/\_/\_/\_<>   _\n  >>>><  >     <  >_//_X =====>> 💨💨💨\n      <>~~\_/\_/\_<>\n\n[ FLUSHING PARTICIPANT DATA ]",
            f"[💨] OPERATION COMPLETE: KICKED!\n⚡ Status: Removed From Group\n\n     <>_/\_/\_/\_<>   _\n     <  _     _  >_//_X  [EJECTED]\n     <>~~\_/\_/\_<>\n\n[ TARGET WAS EXPELLED FROM CHAT ]"
        ]
      # 🔍 ၅။ ID SCANNER အီဖက်ခ် (အသစ်ဖြည့်စွက်ချက်)
    elif action_type == 'id':
        all_frames = [
            f"[!] SYSTEM: SCANNER PROTOCOL INITIALIZED\n🔍 Target: {target_name}\n\n     <>_/\_/\_/\_<>   _\n     <  o     o  >_//_X  *Target Found*\n     <>~~\_/\_/\_<>\n\n[ BIO-METRIC READING: 15% ]",
            f"[⚡] ACCESSING TELEGRAM CORE DATA...\n🛰️ Realm: Chat Grid Network\n\n      <>_/\_/\_/\_<>   _\n    ==<  -     -  >_//_X  *Extracting ID*\n      <>~~\_/\_/\_<>\n\n[ PROTOCOL DECRYPTION: 55% ]",
            f"[🔥] COMPILING DATABASE FIELDS!!!\n⚙️ Matrix: Mapping Object Entities...\n\n      <>_/\_/\_/\_<>   _\n  >>>><  O     O  >_//_X =====>> 📡📡📡\n      <>~~\_/\_/\_<>\n\n[ MATRIX EXTRACTION: 90% ]",
            f"[✨] ANALYSIS COMPLETE: TARGET IDENTIFIED!\n🛡️ Status: Security Clearance Granted\n\n     <>_/\_/\_/\_<>   _\n     <  ^     ^  >_//_X  [DATA RETRIEVED]\n     <>~~\_/\_/\_<>\n\n[ GRID MATRIX IS FULLY LOADED ]"
        ]
    elif action_type == 'info':
        all_frames = [
            f"[!] SYSTEM: RETRIEVING USER DOSSIER\n📂 Profile: {target_name}\n\n     <>_/\_/\_/\_<>   _\n     <  o     o  >_//_X  *Syncing Cloud*\n     <>~~\_/\_/\_<>\n\n[ PINGING DATABASE SERVERS... 10% ]",
            f"[⚡] FETCHING FINANCIAL LEDGERS...\n💰 Target: Accessing Economy Data\n\n      <>_/\_/\_/\_<>   _\n    ==<  -     -  >_//_X  *Reading Balance*\n      <>~~\_/\_/\_<>\n\n[ EXTRACTING BANK CACHE: 45% ]",
            f"[🔥] PARSING TELEGRAM PREMIUM STATUS!!!\n🌟 Matrix: Checking Verification Badge...\n\n      <>_/\_/\_/\_<>   _\n  >>>><  O     O  >_//_X =====>> 📊📊📊\n      <>~~\_/\_/\_<>\n\n[ ASSEMBLING PROFILE BLOCKS: 85% ]",
            f"[✨] RETRIEVAL SUCCESSFUL: PROFILE COMPILED\n🛡️ Status: Dossier Unlocked & Rendered\n\n     <>_/\_/\_/\_<>   _\n     <  ^     ^  >_//_X  [DATA MATRIX READY]\n     <>~~\_/\_/\_<>\n\n[ DISPLAYING USER DOSSIER MATRIX ]"
        ]
    elif action_type == 'calc':
        all_frames = [
            f"[!] SYSTEM: MATH ENGINE ENGAGED\n🧮 Input: {target_name}\n\n     <>_/\_/\_/\_<>   _\n     <  o     o  >_//_X  *Parsing Data*\n     <>~~\_/\_/\_<>\n\n[ LOADING QUANTUM CORES... 20% ]",
            f"[⚡] RUNNING ARITHMETIC LOGIC...\n🧮 Matrix: Resolving Operators\n\n      <>_/\_/\_/\_<>   _\n    ==<  -     -  >_//_X  *Calculating*\n      <>~~\_/\_/\_<>\n\n[ PROCESSING EQUATION: 55% ]",
            f"[🔥] ASSEMBLING BINARY DATA!!!\n📊 Status: Compiling Output Cache...\n\n      <>_/\_/\_/\_<>   _\n  >>>><  O     O  >_//_X =====>> 🧮🧮🧮\n      <>~~\_/\_/\_<>\n\n[ RENDERING RESULT: 90% ]",
            f"[✨] CALCULATION COMPLETE: SOLVED!\n🛡️ Status: Memory Flushed & Ready\n\n     <>_/\_/\_/\_<>   _\n     <  ^     ^  >_//_X  [COMPUTED]\n     <>~~\_/\_/\_<>\n\n[ MATHEMATICAL MATRIX SUCCESSFUL ]"
        ]
    elif action_type == 'catch':
        all_frames = [
            f"[!] SYSTEM: CAPTURE BEAM ACTIVATED\n🃏 Target: {target_name}\n\n     <>_/\_/\_/\_<>   _\n     <  o     o  >_//_X  *Locking Matrix*\n     <>~~\_/\_/\_<>\n\n[ SCANNING MOLECULAR STRUCTURE: 25% ]",
            f"[⚡] INJECTING QUANTUM CONTAINMENT...\n📦 Status: Drawing Entity Into Grid\n\n      <>_/\_/\_/\_<>   _\n    ==<  -     -  >_//_X  *Capturing*\n      <>~~\_/\_/\_<>\n\n[ DOWNLOADING CHARACTER ASSETS: 60% ]",
            f"[🔥] WRITING TO PLAYER INVENTORY VAULT!!!\n📊 Database: Syncing Harem Registry...\n\n      <>_/\_/\_/\_<>   _\n  >>>><  O     O  >_//_X =====>> 🃏🃏🃏\n      <>~~\_/\_/\_<>\n\n[ SECURING BLOCKCHAIN REWARDS: 90% ]",
            f"[✨] CAPTURE SUCCESSFUL: MISSION SECURED!\n🛡️ Status: Character Safely Vaulted\n\n     <>_/\_/\_/\_<>   _\n     <  ^     ^  >_//_X  [ASSET LOCKED]\n     <>~~\_/\_/\_<>\n\n[ HUNTER HAS SUCCESSFULLY CLAIMED ]"
        ]
    elif action_type == 'who':
        all_frames = [
            f"[!] SYSTEM: ENCRYPTED ENTITY DETECTED\n🔍 Query: Who is this character?\n\n     <>_/\_/\_/\_<>   _\n     <  -     -  >_//_X  *Analyzing Pixels*\n     <>~~\_/\_/\_<>\n\n[ DECRYPTING IMAGE METADATA: 15% ]",
            f"[⚡] BYPASSING SECURITY SHIELDS...\n🛡️ Status: Cracking Character Code\n\n      <>_/\_/\_/\_<>   _\n    ==<  o     o  >_//_X  *Matching Data*\n      <>~~\_/\_/\_<>\n\n[ EXTRACTING IDENTITY CORES: 50% ]",
            f"[🔥] REVEALING DESTINY MATRIX!!!\n✨ Target: Removing Cloaking Field...\n\n      <>_/\_/\_/\_<>   _\n  >>>><  O     O  >_//_X =====>> 👁️👁️👁️\n      <>~~\_/\_/\_<>\n\n[ PARSING COGNITIVE NAME: 85% ]",
            f"[👁️] ANALYSIS COMPLETE: TARGET UNVEILED!\n🎉 Status: True Identity Exposed\n\n     <>_/\_/\_/\_<>   _\n     <  ^     ^  >_//_X  [DATA REVEALED]\n     <>~~\_/\_/\_<>\n\n[ RUN /catch COMMAND IMMEDIATELY! ]"
        ]
        # 🎰 ၁၀။ SLOT MACHINE ENGINE
    elif action_type == 'slot':
        all_frames = [
            f"[!] SYSTEM: CASINO REELS SPINNING\n🎰 Player: {target_name}\n\n     <>_/\_/\_/\_<>   _\n     <  o     o  >_//_X  *Spinning Matrix*\n     <>~~\_/\_/\_<>\n\n[ REROLLING MOLECULAR SYMBOLS... ]",
            f"[✨] LOCKING IN COMBINATIONS...\n📊 Status: Matching Symbol Arrays\n\n     <>_/\_/\_/\_<>   _\n     <  ^     ^  >_//_X  [RESOLVED]\n     <>~~\_/\_/\_<>\n\n[ RENDERING SLOT PAYOUT BLOCKS ]"
        ]
    # 🃏 ၁၁။ CARD SHOWDOWN ENGINE
    elif action_type == 'cardgame':
        all_frames = [
            f"[!] SYSTEM: HIGH CARD SHOWDOWN\n🃏 Status: Dealing Quantum Decks\n\n     <>_/\_/\_/\_<>   _\n     <  -     -  >_//_X  *Shuffling*\n     <>~~\_/\_/\_<>\n\n[ COMPILING MULTIPLAYER SCORES... ]"
        ]
    # 🪙 ၁၂။ QUANTUM FLIP ENGINE
    elif action_type == 'flip':
        all_frames = [
            f"[!] SYSTEM: COIN ROTATION ENGAGED\n🪙 Bet Side: {target_name}\n\n     <>_/\_/\_/\_<>   _\n     <  o     o  >_//_X  *Flipping*\n     <>~~\_/\_/\_<>\n\n[ CALCULATING 50/50 GRAVITY CORE... ]"
        ]
    # 🎁 ၁၃။ DAILY BOUNTY ENGINE
    elif action_type == 'daily':
        all_frames = [
            f"[!] SYSTEM: COOLDOWN MATRIX REFRESH\n🎁 Target: {target_name}\n\n     <>_/\_/\_/\_<>   _\n     <  -     -  >_//_X  *Syncing Vault*\n     <>~~\_/\_/\_<>\n\n[ EXTRACTING DAILY BONUS BLOCK... ]"
        ]
    # 🏆 ၁၄။ RICHEST LEADERBOARD ENGINE
    elif action_type == 'richest':
        all_frames = [
            f"[!] SYSTEM: LOADING CASINO LEADERBOARD\n📊 Scope: Top 10 Wealthiest Entities\n\n     <>_/\_/\_/\_<>   _\n     <  O     O  >_//_X  *Fetching*\n     <>~~\_/\_/\_<>\n\n[ RETRIEVING BANK LEDGER RECORDS... ]"
        ]
    # ♻️ ၁၅။ ASSET MELT ENGINE (SCRAP)
    elif action_type == 'scrap':
        all_frames = [
            f"[!] SYSTEM: CARD DISSOLUTION CORE\n♻️ Asset ID: {target_name}\n\n     <>_/\_/\_/\_<>   _\n     <  o     o  >_//_X  *Melting*\n     <>~~\_/\_/\_<>\n\n[ CONVERTING CARD TO CASH CORES: 50% ]"
        ]
    # 🎲 ၁၆။ RISK VENTURE ENGINE (GAMBLE)
    elif action_type == 'gamble':
        all_frames = [
            f"[!] SYSTEM: HIGH-RISK VENTURE ENGAGED\n🎲 Mode: Double Or Nothing\n\n     <>_/\_/\_/\_<>   _\n     <  -     -  >_//_X  *Risking Balance*\n     <>~~\_/\_/\_<>\n\n[ PARSING MATRIX PARADOX OUTCOME... ]",
            f"[✨] TRANSACTION POSTED FLUENTLY\n🛡️ Status: Account Balance Flushed\n\n     <>_/\_/\_/\_<>   _\n     <  O     O  >_//_X  [COMPLETED]\n     <>~~\_/\_/\_<>\n\n[ RISK VENTURE SYSTEM CLOSED ]"
        ]   
    # ၁။ Font ဖိုင်ကို အင်တာနက်ကနေ အလိုအလျောက် ဒေါင်းလုဒ်ဆွဲခိုင်းခြင်း
    font_path = "matrix_font.ttf"
    if not os.path.exists(font_path):
        try:
            # Google Fonts ရဲ့ Monospace (Courier Prime) Font ကို လှမ်းယူတာပါ
            font_url = "https://github.com/google/fonts/raw/main/ofl/courierprime/CourierPrime-Bold.ttf"
            urllib.request.urlretrieve(font_url, font_path)
        except:
            font_path = None # ဒေါင်းမရရင် Default သုံးဖို့ သတ်မှတ်မယ်

    # ၂။ ဒေါင်းလုဒ်ရလာတဲ့ Font ကို တန်ဖိုး 30 နဲ့ စနစ်ထဲ ထည့်သွင်းခြင်း
    try:
        if font_path:
            font = ImageFont.truetype(font_path, 30)
        else:
            font = ImageFont.load_default(size=30)
    except:
        font = ImageFont.load_default()

    # Canvas Size ကို 600x320 သတ်မှတ်ထားပါတယ်
    img_w, img_h = 500, 320

    for text in all_frames:
        img = Image.new('RGB', (img_w, img_h), color=(15, 15, 15))
        d = ImageDraw.Draw(img)
        
        # စာသားရဲ့ အကျယ်အဝန်းကို တွက်ချက်ခြင်း
        left, top, right, bottom = d.textbbox((0, 0), text, font=font)
        text_w = right - left
        text_h = bottom - top
        
        # အလယ်ဗဟို နေရာရှာခြင်း
        x = (img_w - text_w) / 2
        y = (img_h - text_h) / 2
        
        # စာသားကို အလယ်တည့်တည့်မှာ ထင်ထင်ရှားရှား ရေးဆွဲခြင်း
        d.text((x, y), text, fill=(0, 255, 65), font=font, align="center", spacing=6)
        frames_data.append(np.array(img))
        
    gif_buffer = io.BytesIO()
    imageio.mimsave(gif_buffer, frames_data, format='GIF', fps=2)
    gif_buffer.seek(0)
    return gif_buffer

def generate_clean_calc_gif(calc_text):
    # Font အလိုအလျောက် ဒေါင်းလုဒ်ဆွဲခြင်း (စာလုံးထင်ရှားစေရန်)
    font_path = "matrix_font.ttf"
    if not os.path.exists(font_path):
        try:
            font_url = "https://github.com/google/fonts/raw/main/ofl/courierprime/CourierPrime-Bold.ttf"
            urllib.request.urlretrieve(font_url, font_path)
        except:
            font_path = None

    try:
        if font_path:
            font = ImageFont.truetype(font_path, 32) # စာလုံးကြီးကြီး 32 ထားထားပါတယ်
        else:
            font = ImageFont.load_default(size=32)
    except:
        font = ImageFont.load_default()

    # GIF Canvas Size (အကျယ် ၆၀၀၊ အမြင့် ၂၆၀)
    img_w, img_h = 600, 260
    img = Image.new('RGB', (img_w, img_h), color=(15, 15, 15))
    d = ImageDraw.Draw(img)

    # စာသား အလယ်ဗဟို ကျအောင် တွက်ချက်ခြင်း
    left, top, right, bottom = d.textbbox((0, 0), calc_text, font=font)
    text_w = right - left
    text_h = bottom - top

    x = (img_w - text_w) / 2
    y = (img_h - text_h) / 2

    # Matrix အစိမ်းရောင်ဖြင့် စာသားရေးဆွဲခြင်း
    d.text((x, y), calc_text, fill=(0, 255, 65), font=font, align="center", spacing=10)
    
    # မလှုပ်ရှားစေရန် Frame တစ်ခုတည်းသာ ထည့်သွင်းခြင်း
    frames_data = [np.array(img)]
    
    gif_buffer = io.BytesIO()
    imageio.mimsave(gif_buffer, frames_data, format='GIF', fps=1)
    gif_buffer.seek(0)
    return gif_buffer
def generate_clean_info_gif(info_text):
    font_path = "matrix_font.ttf"
    if not os.path.exists(font_path):
        try:
            font_url = "https://github.com/google/fonts/raw/main/ofl/courierprime/CourierPrime-Bold.ttf"
            urllib.request.urlretrieve(font_url, font_path)
        except:
            font_path = None

    try:
        if font_path:
            # စာကြောင်းရေ များတဲ့အတွက် အချိုးကျအောင် Size ကို 22 ထားပါတယ်
            font = ImageFont.truetype(font_path, 22) 
        else:
            font = ImageFont.load_default(size=22)
    except:
        font = ImageFont.load_default()

    # စာသားတွေ အပေါ်အောက် မပြတ်စေရန် အမြင့်ကို 360 အထိ တိုးမြှင့်ထားပါတယ်
    img_w, img_h = 600, 360
    img = Image.new('RGB', (img_w, img_h), color=(15, 15, 15))
    d = ImageDraw.Draw(img)

    # စာသားတစ်ခုလုံးရဲ့ အကျယ်အဝန်းကို တွက်ချက်ခြင်း
    left, top, right, bottom = d.textbbox((0, 0), info_text, font=font)
    text_w = right - left
    text_h = bottom - top

    # အလယ်ဗဟိုနေရာ ရှာဖွေခြင်း
    x = (img_w - text_w) / 2
    y = (img_h - text_h) / 2

    # align="left" က စာသားတွေကို Terminal ပုံစံ ဘယ်ဘက်ညီညာစေပြီး (x,y) က တစ်တုံးလုံးကို အလယ်ပို့ပေးပါတယ်
    d.text((x, y), info_text, fill=(0, 255, 65), font=font, align="left", spacing=8)
    
    frames_data = [np.array(img)]
    gif_buffer = io.BytesIO()
    imageio.mimsave(gif_buffer, frames_data, format='GIF', fps=1)
    gif_buffer.seek(0)
    return gif_buffer

def generate_clean_event_gif(event_text, img_h=280):
    font_path = "matrix_font.ttf"
    if not os.path.exists(font_path):
        try:
            font_url = "https://github.com/google/fonts/raw/main/ofl/courierprime/CourierPrime-Bold.ttf"
            urllib.request.urlretrieve(font_url, font_path)
        except:
            font_path = None

    try:
        if font_path:
            font = ImageFont.truetype(font_path, 22)
        else:
            font = ImageFont.load_default(size=22)
    except:
        font = ImageFont.load_default()

    img_w = 600
    img = Image.new('RGB', (img_w, img_h), color=(15, 15, 15))
    d = ImageDraw.Draw(img)

    # စာသား အလယ်ဗဟိုကျအောင် တွက်ချက်ခြင်း
    left, top, right, bottom = d.textbbox((0, 0), event_text, font=font)
    text_w = right - left
    text_h = bottom - top

    x = (img_w - text_w) / 2
    y = (img_h - text_h) / 2

    d.text((x, y), event_text, fill=(0, 255, 65), font=font, align="left", spacing=8)
    
    frames_data = [np.array(img)]
    gif_buffer = io.BytesIO()
    imageio.mimsave(gif_buffer, frames_data, format='GIF', fps=1)
    gif_buffer.seek(0)
    return gif_buffer

def generate_clean_id_gif(id_text):
    font_path = "matrix_font.ttf"
    if not os.path.exists(font_path):
        try:
            font_url = "https://github.com/google/fonts/raw/main/ofl/courierprime/CourierPrime-Bold.ttf"
            urllib.request.urlretrieve(font_url, font_path)
        except:
            font_path = None

    try:
        if font_path:
            font = ImageFont.truetype(font_path, 22) 
        else:
            font = ImageFont.load_default(size=22)
    except:
        font = ImageFont.load_default()

    # ID အချက်အလက် စာကြောင်းရေနှင့် ကွက်တိဖြစ်အောင် အမြင့်ကို ၃၈၀ ထားပေးထားပါတယ်
    img_w, img_h = 600, 380
    img = Image.new('RGB', (img_w, img_h), color=(15, 15, 15))
    d = ImageDraw.Draw(img)

    # စာသားတစ်ခုလုံး၏ အကျယ်အဝန်းကို တွက်ချက်ခြင်း
    left, top, right, bottom = d.textbbox((0, 0), id_text, font=font)
    text_w = right - left
    text_h = bottom - top

    # အလယ်ဗဟိုနေရာ ရှာဖွေခြင်း
    x = (img_w - text_w) / 2
    y = (img_h - text_h) / 2

    # ဘရို သတ်မှတ်ထားသည့် နီယွန်စာလုံးစိမ်းအရောင်ဖြင့် ဆွဲခြင်း
    d.text((x, y), id_text, fill=(0, 255, 65), font=font, align="left", spacing=8)
    
    frames_data = [np.array(img)]
    gif_buffer = io.BytesIO()
    imageio.mimsave(gif_buffer, frames_data, format='GIF', fps=1)
    gif_buffer.seek(0)
    return gif_buffer

def generate_clean_who_gif(who_text):
    font_path = "matrix_font.ttf"
    if not os.path.exists(font_path):
        try:
            font_url = "https://github.com/google/fonts/raw/main/ofl/courierprime/CourierPrime-Bold.ttf"
            urllib.request.urlretrieve(font_url, font_path)
        except:
            font_path = None

    try:
        if font_path:
            font = ImageFont.truetype(font_path, 22) 
        else:
            font = ImageFont.load_default(size=22)
    except:
        font = ImageFont.load_default()

    img_w, img_h = 600, 360
    img = Image.new('RGB', (img_w, img_h), color=(15, 15, 15))
    d = ImageDraw.Draw(img)

    left, top, right, bottom = d.textbbox((0, 0), who_text, font=font)
    text_w = right - left
    text_h = bottom - top

    x = (img_w - text_w) / 2
    y = (img_h - text_h) / 2

    # နီယွန်စာလုံးစိမ်းဖြင့် ဆွဲခြင်း
    d.text((x, y), who_text, fill=(0, 255, 65), font=font, align="left", spacing=8)
    
    frames_data = [np.array(img)]
    gif_buffer = io.BytesIO()
    imageio.mimsave(gif_buffer, frames_data, format='GIF', fps=1)
    gif_buffer.seek(0)
    return gif_buffer
def generate_clean_catch_gif(catch_text):
    font_path = "matrix_font.ttf"
    if not os.path.exists(font_path):
        try:
            font_url = "https://github.com/google/fonts/raw/main/ofl/courierprime/CourierPrime-Bold.ttf"
            urllib.request.urlretrieve(font_url, font_path)
        except:
            font_path = None

    try:
        if font_path:
            font = ImageFont.truetype(font_path, 22) 
        else:
            font = ImageFont.load_default(size=22)
    except:
        font = ImageFont.load_default()

    img_w, img_h = 600, 380
    img = Image.new('RGB', (img_w, img_h), color=(15, 15, 15))
    d = ImageDraw.Draw(img)

    left, top, right, bottom = d.textbbox((0, 0), catch_text, font=font)
    text_w = right - left
    text_h = bottom - top

    x = (img_w - text_w) / 2
    y = (img_h - text_h) / 2

    # နီယွန်စာလုံးစိမ်းဖြင့် ပုံဖော်ခြင်း
    d.text((x, y), catch_text, fill=(0, 255, 65), font=font, align="left", spacing=8)
    
    frames_data = [np.array(img)]
    gif_buffer = io.BytesIO()
    imageio.mimsave(gif_buffer, frames_data, format='GIF', fps=1)
    gif_buffer.seek(0)
    return gif_buffer

RARITY_NUM_MAP = {
    "1": {"name": f"🌌 {f('LEGENDARY')}", "value": 1000},  
    "2": {"name": f"🎴 {f('LIMITED')}", "value": 750},   
    "3": {"name": f"🌀 {f('MYTHIC')}", "value": 500},    
    "4": {"name": f"🔥 {f('EPIC')}", "value": 300},      
    "5": {"name": f"💠 {f('RARE')}", "value": 150},      
    "6": {"name": f"🃏 {f('COMMON')}", "value": 50}       
}

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
    """ဘယ် group မှာမဆို / (slash) နဲ့စတဲ့ ဘာစာပဲရိုက်ရိုက်
    အကောင့်မရှိသေးတဲ့ လူဟောင်း/လူသစ်အားလုံးကို Auto Acc ဖွင့်ပေးမည့်စနစ်"""
    user_id = event.sender_id
    if not user_id:
        return
        
    try:
        # Command ရိုက်လိုက်တဲ့သူရဲ့ Telegram Profile နာမည်ကို လှမ်းယူခြင်း
        user_entity = await event.get_sender()
        first_name = getattr(user_entity, 'first_name', '') or ''
        last_name = getattr(user_entity, 'last_name', '') or ''
        fullname = f"{first_name} {last_name}".strip()
        if not fullname:
            fullname = getattr(user_entity, 'username', '') or f"User {user_id}"
    except:
        fullname = f"User {user_id}"
        
    # Database ထဲမှာ အကောင့်ရှိမရှိ စစ်ပြီး မရှိသေးရင် Auto Acc ဖွင့်ပေးသွားမည်
    # (ensure_user_registered function ထဲမှာ အကောင့်ရှိပြီးသားဆို ကျော်သွားမယ့် logic ပါပြီးသားမို့ ထပ်ခါထပ်ခါ Acc ပွင့်မနေပါဘူး)
    await ensure_user_registered(user_id, fullname)
    
    # 💡 ဒီနေရာမှာ event.stop_propagation() မသုံးထားတဲ့အတွက် 
    # အကောင့်ဖွင့်ပေးပြီးတာနဲ့ သူရိုက်လိုက်တဲ့ /cmd က သက်ဆိုင်ရာ Handler ဆီ ဆက်သွားပြီး ပုံမှန်အတိုင်း အလုပ်လုပ်ပါလိမ့်မယ်။

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
    reply_text = None
    user_text_clean = user_text.strip().lower()

    # ✨ အဆင့် (၁) - EXACT MATCH (စာသားချင်း အတိအကျတူတာကို ဦးစားပေး ရှာဖွေခြင်း)
    # ဒေတာဘေ့စ်ထဲက "trigger" နဲ့ လူရိုက်လိုက်တဲ့စာသား အတိအကျတူရင် "responses" ထဲကနေ တန်းရွေးမယ်
    try:
        exact_doc = await reply_save_col.find_one({"trigger": user_text_clean})
        if exact_doc and exact_doc.get("responses") and isinstance(exact_doc["responses"], list):
            # ကြော်ငြာ Link များ ဖယ်ထုတ်ခြင်း
            valid_choices = [c for c in exact_doc["responses"] if not ('@' in str(c) or 't.me/' in str(c))]
            if valid_choices:
                return random.choice(valid_choices) # အတိအကျတူတာတွေ့ရင် ဒီမှာတင် တန်းပြန်ဖြေပြီး ပွဲသိမ်းပြီ။
    except Exception as e:
        print(f"Exact Match DB Error: {e}")

    # ✨ အဆင့် (၂) - SUBSTRING MATCH PIPELINE (စာသားထဲမှာ Trigger စာလုံး ပါဝင်နေရင် ရှာဖွေပေးမည့်စနစ်)
    # ဥပမာ - လူက "ဟေး မောနီး" လို့ ရိုက်ရင် ဒေတာဘေ့စ်ထဲက "မောနီး" ဆိုတဲ့ Trigger ကို လိုက်ရှာပေးမယ်
    match_pipeline = [
        {"$match": {
            "$and": [
                {"$ifNull": ["$trigger", False]}, 
                {"$gte": [{"$strLenCP": "$trigger"}, 2]}, # စာလုံးအရှည် ၂ လုံးနှင့်အထက်ရှိမှ စစ်မယ် (စာလုံးအိုကေစေရန်)
                {"$gte": [{"$indexOfCP": [user_text_clean, {"$toLower": "$trigger"}]}, 0]} # လူရိုက်တဲ့စာထဲမှာ trigger ပါနေရမယ်
            ]
        }},
        {"$addFields": {
            "weight": {"$strLenCP": "$trigger"} # ကိုက်ညီတဲ့အထဲကမှ စာလုံးအရှည်ဆုံး Trigger ကို ဦးစားပေးရန် weight သတ်မှတ်ခြင်း
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
            
            # ကိုက်ညီမှုအရှိဆုံး Document များထဲမှ responses (List) များကို စုစည်းခြင်း
            for doc in best_docs:
                if doc.get("responses") and isinstance(doc["responses"], list):
                    choices = [c for c in doc["responses"] if not ('@' in str(c) or 't.me/' in str(c))]
                    valid_choices.extend(choices)
            
            if valid_choices: 
                reply_text = random.choice(valid_choices)
    except Exception as e:
        print(f"Smart Reply DB Error: {e}")

    # ⚠️ ပြတ်သားသော အချက် - talk_col ထဲက ကြုံရာကျပန်း Fallback စနစ်ကို လုံးဝ ဖြုတ်ပစ်ထားတဲ့အတွက်
    # ဒေတာဘေ့စ်ထဲမှာ မသင်ထားတဲ့စာဆိုရင် Bot က အဆက်အစပ်မရှိတာတွေ လျှောက်မပြောတော့ဘဲ ငြိမ်နေပါလိမ့်မယ်ဗျာ။

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

# 🧮 Bytes မှ KB, MB, GB သို့ ပြောင်းပေးမည့် Utility Helper
def format_size(bytes_size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} TB"

# 🔍 Database တစ်ခုလုံးနှင့် သက်ဆိုင်ရာ Page လိုက် Collection stats ဖတ်ပေးမည့် Core Function
async def build_db_stats_message(page=1):
    try:
        # MongoDB Database Stats တစ်ခုလုံးကို လှမ်းတောင်းခြင်း
        db_stats = await db.command("dbStats")
        total_data_size = format_size(db_stats.get("dataSize", 0))
        total_storage_size = format_size(db_stats.get("storageSize", 0))
        total_index_size = format_size(db_stats.get("indexSize", 0))
        all_collections_count = db_stats.get("collections", 0)
        
        text = "🤖 <b>[ MONGODB CORE SYSTEM MONITOR ]</b> 📊\n"
        text += "====================================\n"
        text += f"👑 <b>Database:</b> <code>telegram_bot</code>\n"
        text += f"📦 <b>Total Data Size:</b> <code>{total_data_size}</code>\n"
        text += f"💾 <b>Storage Size:</b> <code>{total_storage_size}</code>\n"
        text += f"🔑 <b>Index Size:</b> <code>{total_index_size}</code>\n"
        text += f"📂 <b>Total Collections:</b> <code>{all_collections_count} ခု</code>\n"
        text += "====================================\n\n"
        
        # Pagination စနစ်အတွက် စာရင်းကို ခွဲထုတ်ခြင်း (Page 1 = ပထမ ၁၁ ခု, Page 2 = ကျန်တဲ့ ၁၀ ခု)
        start_idx = 0 if page == 1 else 11
        end_idx = 11 if page == 1 else len(COLLECTIONS_LIST)
        target_cols = COLLECTIONS_LIST[start_idx:end_idx]
        
        text += f"📝 <b>Collection Details (Page {page}/2):</b>\n"
        text += "------------------------------------\n"
        
        for item in target_cols:
            col_name = item["col"]
            display_name = item["name"]
            
            try:
                # Collection တစ်ခုချင်းစီ၏ Detail Stats ဖတ်ခြင်း
                coll_stats = await db.command("collStats", col_name)
                doc_count = coll_stats.get("count", 0)
                storage_size = format_size(coll_stats.get("storageSize", 0))
            except Exception:
                # ဒေတာ လုံးဝမရှိသေး၍ error တက်ပါက Safe Fallback သုံးရန်
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

# ၁။ /checkdb Command အား လက်ခံသည့်အပိုင်း (Private DM + Owner Only ဖြစ်ရမည်)
@bot1.on(events.NewMessage(pattern='/checkdb', incoming=True))
async def check_db_command_handler(event):
    if not event.is_private:
        return # Bot ကို DM လာပြောမှသာ အလုပ်လုပ်မည်
        
    if event.sender_id != OWNER_ID:
        await event.respond("🚷 <b>Error:</b> ဤ Command သည် Bot Owner သီးသန့်ဖြစ်သဖြင့် အသုံးပြုခွင့်မရှိပါ။")
        return

    waiting_msg = await event.respond("⏳ Database တစ်ခုလုံးကို စစ်ဆေးနေပါသည်... ခေတ္တစောင့်ဆိုင်းပေးပါ...")
    
    # Page 1 အတွက် စာသားတည်ဆောက်ပြီး Inline Button နှင့်တကွ ပို့ပေးခြင်း
    stats_text = await build_db_stats_message(page=1)
    
    buttons = [
        [
            Button.inline("➡️ Page 2 ကြည့်ရန်", data="db_page_2")
        ],
        [
            Button.inline("🔄 Refresh Stats", data="db_refresh_1")
        ]
    ]
    
    await waiting_msg.delete()
    await event.respond(stats_text, parse_mode='html', buttons=buttons)


# ၂။ Inline Buttons များကို နှိပ်ပါက စာမျက်နှာ လှန်ပေးမည့် အပိုင်း
@bot1.on(events.CallbackQuery(pattern=r'^db_'))
async def db_stats_callback_handler(event):
    if event.sender_id != OWNER_ID:
        await event.answer("⚠️ သင်သည် ဤခလုတ်ကို နှိပ်ပိုင်ခွင့် မရှိပါ!", alert=True)
        return
        
    action = event.data.decode('utf-8')
    
    await event.answer("🔄 Updating Stats...")
    
    if action == "db_page_1":
        stats_text = await build_db_stats_message(page=1)
        buttons = [
            [Button.inline("➡️ Page 2 ကြည့်ရန်", data="db_page_2")],
            [Button.inline("🔄 Refresh Stats", data="db_refresh_1")]
        ]
        await event.edit(stats_text, parse_mode='html', buttons=buttons)
        
    elif action == "db_page_2":
        stats_text = await build_db_stats_message(page=2)
        buttons = [
            [Button.inline("⬅️ Page 1 သို့ပြန်သွားရန်", data="db_page_1")],
            [Button.inline("🔄 Refresh Stats", data="db_refresh_2")]
        ]
        await event.edit(stats_text, parse_mode='html', buttons=buttons)
        
    elif action == "db_refresh_1":
        stats_text = await build_db_stats_message(page=1)
        buttons = [
            [Button.inline("➡️ Page 2 ကြည့်ရန်", data="db_page_2")],
            [Button.inline("🔄 Refresh Stats", data="db_refresh_1")]
        ]
        await event.edit(stats_text, parse_mode='html', buttons=buttons)
        
    elif action == "db_refresh_2":
        stats_text = await build_db_stats_message(page=2)
        buttons = [
            [Button.inline("⬅️ Page 1 သို့ပြန်သွားရန်", data="db_page_1")],
            [Button.inline("🔄 Refresh Stats", data="db_refresh_2")]
        ]
        await event.edit(stats_text, parse_mode='html', buttons=buttons)

# ====================================================================================
# 🛡️ SYSTEM COMMANDS & NAVIGATION HANDLERS (START, HELP, GAME)
# ====================================================================================
@bot1.on(events.NewMessage(pattern=r'^/start$'))
async def system_start_router(event):
    is_cd, rem = await is_on_cooldown(event.sender_id, "start", 2)
    if is_cd: return
    
    sender = await event.get_sender()
    track_user_metrics(event.sender_id, getattr(sender, 'username', None), getattr(sender, 'first_name', 'User'))
    
    bot_me = await bot1.get_me()
    welcome_msg = (
        f"🌌 <b>𝑩𝑶𝑫 𝑺𝒚𝒔𝒕𝒆𝒎 𝑷𝒍𝒖𝒔 𝑬𝒏𝒈𝒊𝒏𝒆</b>\n"
        f"ကျွန်ုပ်သည် Telegram Groups များကို အလိုအလျောက် အဆင့်မြင့်ဆုံး ထိန်းချုပ်ပေးနိုင်သော စနစ်ဖြစ်ပြီး "
        f"ပျော်ရွှင်စရာ Charater Card Spawning နှင့် /Game နှင့် စကားပြော စာထောက် စနစ်များပါ ပါဝင်ဖွဲ့စည်းထားသော Production-level Bot ဖြစ်ပါသည်။\n\n"
        f"🦖 အောက်ပါ Inline Buttons များကို အသုံးပြု၍ လုပ်ငန်းဆောင်တာများနှင့် ကစားနည်းလမ်းညွှန်များကို အလွယ်တကူ လေ့လာနိုင်ပါသည် Chief။"
    )
    buttons = [
        [Button.inline("⚙️ Operational Commands", data="nav_help_main"), Button.inline("🎮 Catching Game Rules", data="nav_game_main")],
        [Button.url("🪐 Add Me To Your Group Chat 🪐", f"https://t.me/{bot_me.username}?startgroup=true")],
        [Button.url("👥 Support Brotherhood Community", "https://t.me/Comeback_BoD")]
    ]
    await event.respond(bq(welcome_msg), parse_mode='html', buttons=buttons)

@bot1.on(events.CallbackQuery())
async def system_callback_router(event):
    data = event.data.decode('utf-8')
    user_id = event.sender_id
    
    if data == "nav_back_home":
        bot_me = await bot1.get_me()
        welcome_msg = (
            f"🌌 <b>𝑩𝑶𝑫 𝑺𝒚𝒔𝒕𝒆𝒎 𝑷𝒍𝒖𝒔 𝑬𝒏𝒈𝒊𝒏𝒆</b>\n"
            f"🤖 ကျွန်ုပ်သည် Telegram Groups များကို အလိုအလျောက် အဆင့်မြင့်ဆုံး ထိန်းချုပ်ပေးနိုင်သော စနစ်ဖြစ်ပြီး "
            f"ပျော်ရွှင်စရာ Charater Card Spawning နှင့် /Game နှင့် စကားပြော စာထောက် စနစ်များ ပါဝင်ဖွဲ့စည်းထားသော Production-level Bot ဖြစ်ပါသည်။"
        )
        buttons = [
            [Button.inline("Operational Commands", data="nav_help_main"), Button.inline("🎮 Catching Game Rules", data="nav_game_main")],
            [Button.url("Add Me To Your Group Chat", f"https://t.me/{bot_me.username}?startgroup=true")],
            [Button.url("Support Brotherhood Group", "https://t.me/Comeback_BoD")]
        ]
        return await event.edit(bq(welcome_msg), parse_mode='html', buttons=buttons)

    elif data == "nav_help_main":
        help_text = (
            f"⚙️ <b><u>Operational Moderation Terminal</u></b>\n\n"
            f" <code>/id</code> - မိမိ သို့မဟုတ် ရည်ညွှန်းသူ၏ Profile ID Matrix အား ထုတ်ယူခြင်း။\n"
            f" <code>/mute [Reply]</code> - စည်းကမ်းဖောက်ဖျက်သူအား စကားပြောခွင့် ကန့်သတ်ခြင်း။\n"
            f" <code>/unmute [Reply]</code> - ကန့်သတ်ချက်များအား ပြန်လည်ရုပ်သိမ်းပေးခြင်း။\n"
            f" <code>/ban [Reply]</code> - အဖွဲ့ဝင်အဖြစ်မှ ထာဝရထုတ်ပယ် (Exile) ပြုလုပ်ခြင်း။\n"
            f" <code>/kick [Reply]</code> - လက်ရှိ Chat Sector မှ ချက်ချင်း ဖယ်ရှားခြင်း။\n"
            f" <code>/adm [Text]</code> - လက်ရှိ Group အတွင်းရှိ (Admins) အားလုံးကို Tag ခေါ်ယူခြင်း။\n"
            f" <code>/calc [Expression]</code> - မြန်ဆန်မှန်ကန်သော သင်္ချာတွက်ချက်မှု Engine။\n"
            f" <code>/weather</code> - မြန်မာတစ်နိုင်ငံလုံးဆိုင်ရာ ရာသီဥတု တိုင်းတာရေး Dashboard အား ခေါ်ယူခြင်း။"
        )
        return await event.edit(bq(help_text), parse_mode='html', buttons=[[Button.inline("🔙 Back Menu", data="nav_back_home")]])

    elif data == "nav_game_main":
        game_text = (
            f"🎮 <b><u>BoD Game Console</u></b>\n\n"
            f"⭐ <code>/checkp</code>\n"
            f"<blockquote><b>မိမိပိုင်ဆိုင်သော ငွေလက်ကျန်ပမာဏကို စစ်ဆေးကြည့်ရှုခြင်း။</b></blockquote>\n\n"
            f"⭐ <code>/daily</code>\n"
            f"<blockquote><b>နေ့စဥ်ထုတ်ယူနိုင်သော ငွေကြေးအား ရယူခြင်း။</b></blockquote>\n\n"
            f"⭐ <code>/hunt</code>\n"
            f"<blockquote><b>နေ့စဥ် Hunt ပြုလုပ်ပြီး ငွေကြေးများ ရှာဖွေစုဆောင်းခြင်း။</b></blockquote>\n\n"
            f"⭐ <code>/pay [Amount] [Username/ID]</code>\n"
            f"<blockquote><b>အခြားသူများထံသို့ ငွေကြေးများ လွှဲပြောင်းပေးပို့ခြင်း။</b></blockquote>\n\n"
            f"⭐ <code>/game</code>\n"
            f"<blockquote><b>လက်ရှိဆော့ကစားနိုင်သော ဂိမ်း Menu များကို ကြည့်ရှုခြင်း။</b></blockquote>"
        )
        return await event.edit(bq(game_text), parse_mode='html', buttons=[[Button.inline("🔙 Back Menu", data="nav_back_home")]])

@bot1.on(events.NewMessage(pattern=r'^/help$'))
async def direct_help_handler(event):
    is_cd, rem = await is_on_cooldown(event.sender_id, "helpp", 2)
    if is_cd: return
    help_text = (
        f" <b><u>Operational Moderation Terminal</u></b>\n\n"
        f" <code>/id</code> - Profile ID Matrix ထုတ်ယူခြင်း။\n"
        f" <code>/mute</code> - စကားပြောခွင့် ကန့်သတ်ခြင်း။\n"
        f" <code>/unmute</code> - ကန့်သတ်ချက် ရုပ်သိမ်းခြင်း။\n"
        f" <code>/ban</code> - အဖွဲ့ဝင်အဖြစ်မှ ထာဝရထုတ်ပယ်ခြင်း။\n"
        f" <code>/kick</code> - Chat Sector မှ ဖယ်ရှားခြင်း။\n"
        f" <code>/adm</code> - Group Admins အားလုံးကို Tag ခေါ်ယူခြင်း။\n"
        f" <code>/calc</code> - သင်္ချာတွက်ချက်မှု စနစ်။\n"
        f" <code>/weather</code> - မိုးလေဝသ တိုင်းဒေသကြီးများ Dashboard။"
    )
    await event.respond(bq(help_text), parse_mode='html', buttons=[[Button.inline("⚙️ Navigation Home", data="nav_back_home")]])


# ====================================================================================
# 🔢 ADVANCED SYSTEM LOGIC 5: SECURE ARITHMETIC CALCULATOR
# ====================================================================================
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
                # Safe evaluation context
                result = eval(text, {"__builtins__": None}, {})
                
                # GIF ထဲမှာ ပြသမယ့် စာသားပုံစံ
                gif_display_text = f"{text} = {result}\n\n[ PRODUCED BY MORGAN ]"
                
                # Static GIF ထုတ်ယူခြင်း
                gif_file = generate_clean_calc_gif(gif_display_text)
                gif_file.name = "math_processing.gif"
                
                # Caption မပါဘဲ GIF သီးသန့်သာ မေးခွန်းရှင်ထံ Reply ပြန်ခြင်း 
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
                await event.reply("⚠️ User not found! Cannot fetch information.")
                return
        else:
            target_id = event.sender_id
            target_user = await event.client.get_entity(target_id)

        if target_user:
            # Database ထဲမှ အကောင့်ဒေတာကို ရှာဖွေခြင်း
            db_user = await db.users_catcher_data.find_one({"user_id": target_id})
            balance = db_user.get("balance", 0) if db_user else "Not Registered"
            
            username = f"@{target_user.username}" if target_user.username else "None"
            premium_status = "Yes" if getattr(target_user, 'premium', False) else "No"
            full_name = f"{target_user.first_name or ''} {target_user.last_name or ''}".strip()
            
            # GIF ထဲမှာ Font ကြောင့် Icon တွေမစောင်းသွားအောင် Text Character များဖြင့် လှပစွာ ပုံဖော်ထားပါတယ်
            gif_display_text = (
                f"       USER PROFILE INFO\n"
                f"==============================\n"
                f" NAME    : {full_name}\n"
                f" ID      : {target_id}\n"
                f" USERNAME: {username}\n"
                f" PREMIUM : {premium_status}\n"
                f" BALANCE : {balance}\n"
                f"==============================\n"
                f"    [ PRODUCED BY MORGAN ]"
            )
            
            # Clean Static GIF ထုတ်လုပ်ခြင်း
            gif_file = generate_clean_info_gif(gif_display_text)
            gif_file.name = "info_scan.gif"
            
            # Caption စာသားများလုံးဝမပါဘဲ GIF သီးသန့်သာ Reply ပြန်ခြင်း 🔥
            await event.client.send_file(
                event.chat_id, 
                gif_file, 
                reply_to=event.id
            )
            
    except Exception as e:
        print(f"Error in /info command: {e}")

# ==========================================
# ⚙️ CHAT ACTIONS & WELCOME MATRIX
# ==========================================
# ==========================================
@bot1.on(events.ChatAction)
async def welcome_goodbye(event):
    try:
        chat = await event.get_chat()
        bot_me = await bot1.get_me()
        
        group_name = chat.title 
        target_ids = event.user_ids or [event.user_id]

        # ၁။ Member အရေအတွက်ကို အသေအချာဆွဲထုတ်ခြင်း
        try:
            members_cnt = getattr(chat, 'participants_count', None)
            if not members_cnt:
                full_chat = await event.client.get_full_chat(chat.id)
                members_cnt = full_chat.full_chat.participants_count
            member_count = f"{members_cnt:,}" if members_cnt else "Unknown"
        except:
            member_count = "Unknown"

        # ၂။ Group သက်တမ်း (Group Age) ကို တွက်ချက်ခြင်း
        group_age = "Unknown"
        if hasattr(chat, 'date') and chat.date:
            try:
                # လက်ရှိအချိန်နှင့် Group စတင်တည်ထောင်ချိန် ခြားနားချက်ကို ရှာသည်
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
            # Bot ကိုယ်တိုင် Group ထဲ ရောက်သွားချိန်
            if any(uid == bot_me.id for uid in target_ids if uid):
                await groups_col.update_one({"chat_id": chat.id}, {"$set": {"title": chat.title, "joined_at": datetime.now(TZ)}}, upsert=True)
                
                bot_join_text = (
                    f"       SYSTEM: BOT ACTIVATED\n"
                    f"==================================\n"
                    f" THANK YOU FOR ADDING ME!\n"
                    f" GROUP  : {group_name}\n"
                    f" MEMBERS: {member_count}\n"
                    f" AGE    : {group_age}\n"
                    f"==================================\n"
                    f"      [ PRODUCED BY MORGAN ]"
                )
                # စာကြောင်းများလာသဖြင့် img_h ကို 340 သို့ တိုးမြှင့်ထားပါသည်
                gif_file = generate_clean_event_gif(bot_join_text, img_h=340)
                gif_file.name = "bot_joined.gif"
                await bot1.send_file(chat.id, gif_file)
                
                # Admin Log Group သို့ စာသားလှမ်းပို့ခြင်း
                bot_mention = f"<a href='tg://user?id={bot_me.id}'>{escape_html(bot_me.first_name)}</a>"
                msg2 = f"မင်္ဂလာပါ {escape_html(group_name)}က \n{bot_mention}ထည့်သုံးထားပါသေးတယ်\n\nGroup Name - {escape_html(group_name)}\nGroup ID - <code>{chat.id}</code>\nMembers အရေအတွက် - {member_count}\nGroup သက်တမ်း - {group_age}\nBot Creator - {owner_tag()}"
                await bot1.send_message(SPECIFIC_GROUP, bq(msg2), parse_mode='html')
            
            # Member အသစ်များ ဝင်လာချိန်
            else:
                for uid in target_ids:
                    if uid and uid != bot_me.id:
                        try: u = await bot1.get_entity(uid)
                        except: continue
                        track_user_metrics(u.id, u.username, u.first_name) 
                        
                        user_first_name = u.first_name or "User"
                        
                        # Welcome GIF (Member Count နှင့် Group Age ပါဝင်သည်)
                        welcome_text = (
                            f"        WELCOME TO GROUP\n"
                            f"==================================\n"
                            f" MEMBER : {user_first_name}\n"
                            f" GROUP  : {group_name}\n"
                            f" MEMBERS: {member_count}\n"
                            f" AGE    : {group_age}\n"
                            f"==================================\n"
                            f"      [ PRODUCED BY MORGAN ]"
                        )
                        
                        gif_file = generate_clean_event_gif(welcome_text, img_h=340)
                        gif_file.name = "welcome.gif"
                        
                        await bot1.send_file(
                            event.chat_id, 
                            gif_file,
                            buttons=[
                                [Button.url("Bot OWNER DM", "t.me/Hello_Im_DexterMorgan")],
                                [Button.url("ကျွန်ုပ်တို့Groupသို့ဝင်ရန်", "https://t.me/Comeback_BoD")]
                            ]
                        )
                        
        # Member များ ထွက်သွားချိန် / Kicked ဖြစ်ချိန်
        elif event.user_kicked or event.user_left:
            if event.user_id == bot_me.id:
                await groups_col.delete_one({"chat_id": chat.id})
            else:
                try:
                    u = await bot1.get_entity(event.user_id)
                    user_first_name = u.first_name or "User"
                    
                    # Goodbye GIF
                    goodbye_text = (
                        f"         GOODBYE MEMBER\n"
                        f"==================================\n"
                        f" MEMBER : {user_first_name}\n"
                        f" STATUS : LEFT THE GROUP\n"
                        f" MEMBERS: {member_count}\n"
                        f"==================================\n"
                        f"      [ PRODUCED BY MORGAN ]"
                    )
                    
                    gif_file = generate_clean_event_gif(goodbye_text, img_h=310)
                    gif_file.name = "goodbye.gif"
                    
                    await bot1.send_file(event.chat_id, gif_file)
                except: pass
    except Exception as e:
        await report_system_error("Welcome Goodbye Event Tracker", e)


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
@bot1.on(events.NewMessage(pattern=r'^/stats$'))
async def system_analytics_matrix(event):
    if event.sender_id != OWNER_ID: return
    
    loading = await event.reply("<code>🌌 Core Processor Engine မှ အချက်အလက်များကို ထုတ်ယူနေပါသည်...</code>", parse_mode='html')
    try:
        total_tracked_users = await users_col.count_documents({})
        total_active_groups = await groups_col.count_documents({})
        
        day_ago = datetime.now(TZ) - timedelta(days=1)
        active_24h_users = await users_col.count_documents({"last_active": {"$gte": day_ago}})
        
        metrics_layout = (
            f"🌌 <b>MAU Stats</b>\n"
            f"👥 <b>Total Managed Users:</b> <code>{total_tracked_users} 👤</code>\n"
            f"🔥 <b>Active Users (24H):</b> <code>{active_24h_users} 🔥</code>\n"
            f"📍 <b>ထည့်သွင်းအသုံးပြုနေသော (Groups):</b> <code>{total_active_groups} 🪐</code>\n"
            f"⚙️ <b>Engine Status:</b> <code>High-Speed Optimized (No Delay)</code>\n"
            f"💡 <i>Telegram API ကို အသုံးမပြုဘဲ Internal Matrix မှ တိုက်ရိုက်တွက်ချက်ထားသဖြင့် 100% တိကျပါသည်။</i>"
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
                action_msg = f"⚠️ Admin {mention} က Sticker တွေ Spam နေလို့ ပို့ထားသမျှ အကုန်ဖျက်လိုက်ပါတယ်။"
            else:
                await bot1.edit_permissions(chat_id, sender_id, send_stickers=False)
                action_msg = f"{mention} ကို Sticker ပို့ခွင့် ပိတ်လိုက်ပါပြီ။\nအကြောင်းရင်း- Spam တဲ့အတွက် ဖြစ်ပါတယ်။"
            
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
                action_msg = f"⚠️ Admin {mention}... သတိပေးထားတဲ့ကြားက စာတိုတွေ ထပ်စပမ်းနေလို့ ဆင်ခြင်ပေးဖို့ မေတ္တာရပ်ခံပါတယ်။"
            else:
                await register_temporary_mute(chat_id, sender_id, duration_minutes=5)
                await bot1.edit_permissions(chat_id, sender_id, until_date=datetime.now() + timedelta(minutes=5), send_messages=False)
                action_msg = f"🚫 {mention} ကို သတိပေးချက်ကို မလိုက်နာဘဲ စာတိုတွေ ဆက်တိုက် Spam တဲ့အတွက် ၅ မိနစ်ခန့် Mute လိုက်ပါပြီ (Auto-Expiry Ensured)။"
            
            await event.respond(bq(action_msg), parse_mode='html')
            del char_spam_data[sender_id]
            return
        except Exception as e: print(f"Char Spam Mute Error: {e}")

    if len(recent_times) == 5:
        try:
            await bot1.delete_messages(chat_id, recent_ids)
            sender = await event.get_sender()
            mention = f"<a href='tg://user?id={sender_id}'>{escape_html(sender.first_name)}</a>"
            warn_msg = f"⚠️ {mention} စာ Spam နေလို့ အကုန်ဖျက်လိုက်ပါပြီ။ နောက်တစ်ခါ ထပ်ပို့ရင် 5 မိနစ် Mute ခံရပါလိမ့်မယ်။"
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
            await event.reply(bq(f"အိုကေ! '{keyword}' filter ကို Premium Emoji တွေအပါအဝင် Cloud စနစ်နဲ့ မှတ်လိုက်ပြီ ✅"), parse_mode='html')
        except Exception as e:
            await event.reply(bq(f"❌ Storage ထဲ လှမ်းသိမ်းရမှာ Error တက်သွားတယ်: {e}"), parse_mode='html')
    else:
        await event.reply(bq("⚠️ Filter မှတ်ဖို့အတွက် ပို့ချင်တဲ့စာ သို့မဟုတ် မီဒီယာကို Reply ပြန်ပြီးမှ သုံးပေးပါ ဘရို။"), parse_mode='html')

# ====================================================================================
# 🆔 IDENTITY MATRIX SYSTEM
# ====================================================================================
@bot1.on(events.NewMessage(pattern=r'(?i)^/id'))
async def id_handler(event):
    try:
        target_id = None
        target_user = None
        
        # ၁။ Reply ပြန်ထားလျှင်
        if event.is_reply:
            reply_msg = await event.get_reply_message()
            target_id = reply_msg.sender_id
            target_user = await event.client.get_entity(target_id)
            
        # ၂။ Mention သို့မဟုတ် User ID ရိုက်ထည့်ထားလျှင် (ဥပမာ - /id @username)
        elif len(event.text.split()) > 1:
            arg = event.text.split()[1]
            try:
                target_user = await event.client.get_entity(arg)
                target_id = target_user.id
            except Exception:
                await event.reply("⚠️ User not found! Please check the username or ID.")
                return
                
        # ၃။ ဘာမှမပါဘဲ /id ဟုပဲ ရိုက်ထားလျှင် မိမိ ID ကိုပြမည်
        else:
            target_id = event.sender_id
            target_user = await event.client.get_entity(target_id)

        if target_user:
            username = f"@{target_user.username}" if target_user.username else "None"
            full_name = f"{target_user.first_name or ''} {target_user.last_name or ''}".strip()
            
            # 🖼️ GIF ပုံထဲတွင် ပေါ်မည့် စာသားပုံစံကို တည်ဆောက်ခြင်း
            id_details = (
                f"✨ USER IDENTIFICATION MATRIX ✨\n"
                f"🦖 Name: {full_name}\n"
                f"🍄 User ID: {target_id}\n"
                f"🦦 Username: {username}\n"
                f"🌏 Chat ID: {event.chat_id}\n"
                f"   [ RECON DATA RESOLVED ]"
            )
            
            # အပေါ်က Generator ထံ စာသားလှမ်းပို့ပြီး စာလုံးစိမ်း GIF ထုတ်ယူခြင်း
            gif_file = generate_clean_id_gif(id_details)
            gif_file.name = "id_matrix_scan.gif"
            
            # Telegram ပေါ်သို့ စာလုံးစိမ်းကတ်ပြားပုံစံဖြင့် လှမ်းပို့ပေးခြင်း 🔥
            await event.client.send_file(event.chat_id, gif_file)
            
    except Exception as e:
        print(f"Error in /id command: {e}")

# ==========================================
# 🚫 TELEGRAM PERMANENT ASCII MUTE ENGINE
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/mute(?:@\w+)?(?:\s+(.+))?'))
async def permanent_ascii_mute_handler(event):
    if not await check_admin(event.chat_id, event.sender_id):
        return

    target_id = None
    args_text = event.pattern_match.group(1)

    # ၁။ Reply ဖြင့် Mute ခြင်း
    if event.is_reply:
        reply_msg = await event.get_reply_message()
        target_id = reply_msg.sender_id
    # ၂။ Username અથવા ID ဖြင့် Mute ခြင်း
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
                    await event.reply("⚠️ ၎င်း User အား ရှာမတွေ့ပါ။")
                    return
        else:
            await event.reply("⚠️ Usage: <code>/mute</code> (Reply ဖြင့်) သို့မဟုတ် <code>/mute [@username]</code>")
            return

    if not target_id: return
    bot_me = await event.client.get_me()
    if target_id == bot_me.id or target_id == OWNER_ID:
        await event.reply("❌ Bot သို့မဟုတ် Owner ကို Mute လို့မရပါ။")
        return

    try:
        # until_date ကို ကုဒ်ထဲမှာ လုံးဝမထည့်ပေးဘဲ ချန်ထားခဲ့တဲ့အတွက် ရာသက်ပန် ပိတ်သွားမှာ ဖြစ်ပါတယ်
        await event.client.edit_permissions(
            event.chat_id, target_id, 
            send_messages=False, send_media=False, send_stickers=False, send_gifs=False
        )
        
        # User အမည်ကို Plain Text အနေနဲ့ ဆွဲထုတ်ခြင်း
        user_entity = await event.client.get_entity(target_id)
        target_name = f"{user_entity.first_name} {user_entity.last_name or ''}".strip()
        
        # ရာသက်ပန် နဂါး GIF ထုတ်လုပ်ခြင်း
        gif_file = generate_permanent_dragon_gif(target_name)
        gif_file.name = "permanent_mute.gif"
        
        # ဂရုထဲသို့ ပြတ်သားတဲ့ စာသားနဲ့အတူ ပို့လိုက်ခြင်း 🔥
        await event.client.send_file(
            event.chat_id, 
            gif_file, 
            caption=f"🌌 <b>𝙈𝙐𝙏𝙀 𝙊𝙋𝙀𝙍𝘼𝙏𝙄𝙊𝙉 𝙎𝙐𝘾𝘾𝙀𝙎𝙎!</b>\n🤫 <b>{target_name}</b> has been silenced <b>Permanently</b>.", 
            parse_mode='html'
        )

    except Exception as e:
        await event.reply(f"❌ Error: {str(e)}")
# ==========================================
# 🔊 UNMUTE COMMAND WITH DRAGON EFFECT
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/unmute(?:@\w+)?(?:\s+(.*))?'))
async def unmute_user(event):
    if not await check_ban_rights(event.chat_id, event.sender_id): return
    target_user = await get_target_user(event, event.pattern_match.group(1))
    if not target_user: return

    try:
        # စွမ်းဆောင်ချက် မြှင့်တင်ခြင်း
        await bot1.edit_permissions(event.chat_id, target_user.id, send_messages=True)
        await muted_registry_col.delete_one({"chat_id": event.chat_id, "user_id": target_user.id})
        
        target_name = f"{getattr(target_user, 'first_name', 'User')} {getattr(target_user, 'last_name', '') or ''}".strip()
        mention = f"<a href='tg://user?id={target_user.id}'>{escape_html(target_name)}</a>"
        
        # နဂါး GIF ထုတ်ယူခြင်း
        gif_file = generate_mod_dragon_gif(target_name, 'unmute')
        gif_file.name = "unmute_operation.gif"
        
        unmute_text = (
            f"🌌 <b>𝙐做𝘼𝙉 𝙐𝙉𝙈𝙐𝙏𝙀 𝙊𝙋𝙀𝙍𝘼𝙏𝙄𝙊做</b>\n"
            f"🔊 <b>Target:</b> {mention}\n"
            f"🆔 <b>User ID:</b> <code>{target_user.id}</code>\n"
            f"⚡ <b>Status:</b> <code>Voice Restored / Cleared</code>"
        )
        await event.client.send_file(event.chat_id, gif_file, caption=bq(unmute_text), parse_mode='html')
    except Exception as e: print(f"Unmute Error: {e}")

# ==========================================
# 🚫 BAN COMMAND WITH DRAGON EFFECT
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
        
        gif_file = generate_mod_dragon_gif(target_name, 'ban')
        gif_file.name = "ban_operation.gif"
        
        ban_text = (
            f"🌌 <b>𝘽𝘼𝙉 𝙊𝙋𝙀𝙍𝘼𝙏𝙄𝙊𝙉</b>\n"
            f"🚫 <b>Target:</b> {mention}\n"
            f"🆔 <b>User ID:</b> <code>{target_user.id}</code>\n"
            f"⚡ <b>Status:</b> <code>Exiled / Perm-Banned (ဘန်းပစ်လိုက်ရှုပ်တယ်)</code>"
        )
        await event.client.send_file(event.chat_id, gif_file, caption=bq(ban_text), parse_mode='html')
    except Exception as e: print(f"Ban Error: {e}")

# ==========================================
# ✅ UNBAN COMMAND WITH DRAGON EFFECT
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
        
        gif_file = generate_mod_dragon_gif(target_name, 'unban')
        gif_file.name = "unban_operation.gif"
        
        unban_text = (
            f"🌌 <b>𝙐𝙉𝘽Α𝙉 𝙊𝙋𝙀𝙍𝘼𝙏𝙄𝙊𝙉</b>\n"
            f"✅ <b>Target:</b> {mention}\n"
            f"🆔 <b>User ID:</b> <code>{target_user.id}</code>\n"
            f"⚡ <b>Status:</b> <code>Ban Lifted / Allowed to Join</code>"
        )
        await event.client.send_file(event.chat_id, gif_file, caption=bq(unban_text), parse_mode='html')
    except Exception as e: print(f"Unban Error: {e}")

# ==========================================
# 💨 KICK COMMAND WITH DRAGON EFFECT
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
        
        gif_file = generate_mod_dragon_gif(target_name, 'kick')
        gif_file.name = "kick_operation.gif"
        
        kick_text = (
            f"🌌 <b>𝙆𝙄𝘾𝙆 𝙊𝙋𝙀𝙍𝘼𝙏𝙄𝙊𝙉</b>\n"
            f"💨 <b>Target:</b> {mention}\n"
            f"🆔 <b>User ID:</b> <code>{target_user.id}</code>\n"
            f"⚡ <b>Status:</b> <code>Removed / Kicked from Chat</code>"
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
            f"⚠️ <b>{f('Format Wrong နေတယ် Bro!')}</b>\n"
            f"⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n"
            f"📌 <b>{f('Usage / အသုံးပြုနည်း')}:</b>\n"
            f"<code>/addchar Name | Category | Rarity_Number</code>\n"
            f"<i>(Media File တစ်ခုခုကို Reply ပြန်ပြီး သုံးပေးပါ)</i>\n\n"
            f"🔢 <b>{f('Rarity Tiers (1-6)')}:</b>\n"
            f"<code>1</code> = 🌌 LEGENDARY (1000 MMK)\n"
            f"<code>2</code> = 🎴 LIMITED-EDITION (750 MMK)\n"
            f"<code>3</code> = 🌀 MYTHIC (500 MMK)\n"
            f"<code>4</code> = 🔥 EPIC (300 MMK)\n"
            f"<code>5</code> = 💠 RARE (150 MMK)\n"
            f"<code>6</code> = 🃏 COMMON (50 MMK)\n"
            f"⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n"
            f"💡 <b>{f('Example')}:</b> <code>/addchar Dexter Morgan | Series | 1</code>",
            parse_mode='html'
        )
        return

    parts = [p.strip() for p in input_text.split('|')]
    if len(parts) < 3: 
        return await event.reply(f"❌ <b>{f('Segment တွေလိုနေသေးတယ် Bro! 3 ပိုင်း ခွဲပေးပါ။')}</b>", parse_mode='html')

    char_name, category_name, rarity_num = parts[0], parts[1], parts[2]
    
    if rarity_num not in RARITY_NUM_MAP:
        return await event.reply(f"❌ <b>{f('Rarity Number မှားနေတယ်! 1 ကနေ 6 ထိပဲ ရွေးပါ။')}</b>", parse_mode='html')

    if not event.is_reply: 
        return await event.reply(f"❌ <b>{f('Media တစ်ခုခုကို Reply ပြန်ပေးဦးလေ Boss!')}</b>", parse_mode='html')

    reply_msg = await event.get_reply_message()
    if not reply_msg or not (reply_msg.photo or reply_msg.video or reply_msg.document):
        return await event.reply(f"❌ <b>{f('ထည့်သွင်းဖို့ Valid ဖြစ်တဲ့ Media ရှာမတွေ့ဘူး Bro!')}</b>", parse_mode='html')

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
            f"🔥 <b>{f('DATABASE INJECTED / စနစ်ထဲ ထည့်ပြီးပြီ')}</b>\n"
            f"🆔 <b>{f('Character ID')}:</b> <code>{char_id}</code>\n"
            f"👤 <b>{f('Name')}:</b> <code>{char_name}</code>\n"
            f"🌐 <b>{f('Category')}:</b> <code>{category_name}</code>\n"
            f"🌟 <b>{f('Rarity')}:</b> {r_info['name']}\n"
            f"💎 <b>{f('Worth')}:</b> <code>{r_info['value']} MMK</code>\n"
            f"<blockquote><b>{f('Status')}:</b> Storage ID {storage_id} နဲ့ အိုင်တမ်အသစ်ကို Database ထဲ အောင်မြင်စွာ ချိတ်ဆက်ပြီးပါပြီ Boss! 😎🎧</blockquote>"
        )
        await event.reply(success_msg, parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ <b>Database Inject Error</b>: <code>{escape_html(str(e))}</code>", parse_mode='html')
# ==========================================
# 🗑️ OWNER-ONLY CHARACTER DELETE COMMAND
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/(delchar|removechar)(?:\s+(\S+))?(?:@\w+)?$'))
async def delete_character_by_owner(event):
    # Owner စစ်ထုတ်ခြင်း (မောင်မင်းကြီးသားများ သုံးခွင့်မရှိစေရန်)
    if event.sender_id != OWNER_ID: 
        return
        
    char_id = event.pattern_match.group(2)
    if not char_id:
        await event.reply(f"❌ ကျေးဇူးပြု၍ ဖျက်လိုသော Character ID ကို ရိုက်ထည့်ပေးပါ Chief!\nExample: `/delchar BOD789`")
        return
        
    # ID က ကာရက်တာစနစ်မှာ 'id' ရော 'char_id' ရော ဖြစ်နိုင်လို့ နှစ်မျိုးလုံးကို Query စစ်ပြီးဖျက်ခြင်း
    query = {"$or": [{"id": char_id}, {"char_id": char_id}]}
    
    # Collection နှစ်ခုလုံးထဲကနေ အပြီးတိုင် ဖျက်ထုတ်ခြင်း
    res1 = await db["characters"].delete_many(query)
    res2 = await db["characters_base_data"].delete_many(query)
    
    if res1.deleted_count > 0 or res2.deleted_count > 0:
        # သင့် Bot ရဲ့ Font Style အတိုင်း f() သုံးပြီး Reply ပြန်ခြင်း
        reply_msg = (
            f"🔥 {f('DATABASE REMOVED')} / စနစ်ထဲမှ ဖြုတ်ချပြီး\n"
            f"🆔 {f('Character ID')}: {char_id}\n\n"
            f"𝚂𝚝𝚊𝚝𝚞𝚜: အဆိုပါ Character အား ဒေတာဘေ့စ်ထဲကနေ အပြီးတိုင် အောင်မြင်စွာ ဖျက်ဆီး/ဖြုတ်ချ ပြီးပါပြီ Boss! 🧹😎"
        )
        await event.reply(reply_msg)
    else:
        await event.reply(f"❌ Database ထဲမှာ ID `{char_id}` နဲ့ Character လုံးဝ ရှာမတွေ့ပါ Chief!")

@bot1.on(events.NewMessage(pattern=r'^/haiclean$'))
async def haiclean_handler(event):
    # Owner ID ဟုတ်မဟုတ် သေချာအောင် စစ်ဆေးခြင်း (Owner သာ သုံးခွင့်ရှိမည်)
    if event.sender_id != OWNER_ID:
        return await event.reply("❌ ဒီကွန်မန်းကို Bot Owner တစ်ဦးတည်းသာ အသုံးပြုခွင့်ရှိပါတယ်။")
    
    # ရှင်းလင်းမည့်အကြောင်း အကြောင်းကြားစာ အရင်ပို့ခြင်း
    progress_msg = await event.reply("⏳ Database များကို ရှင်းလင်းနေပါသည်...")
    
    try:
        # Collection ၃ ခုလုံးက ဒေတာများကို အကုန်ဖျက်ပစ်ခြင်း
        await db["characters_base_data"].delete_many({})
        await db["users_catcher_data"].delete_many({})
        await db["marketplace_data"].delete_many({})
        
        # အောင်မြင်ကြောင်း စာပြန်ခြင်း
        await progress_msg.edit("🧹 Database ၃ ခုလုံးကို လုံးဝ (လုံးဝ) ပြောင်စင်အောင် ရှင်းလင်းပြီးပါပြီဗျာ။ အသစ်ပြန်ထည့်နိုင်ပါပြီ။")
        
    except Exception as e:
        await progress_msg.edit(f"❌ Error တစ်ခု တက်သွားပါတယ်- {e}")
@bot1.on(events.NewMessage(pattern=r'^/adm(?:\s+(.*))?'))
async def tag_admins(event):
    if event.is_private: return
    if not await check_admin(event.chat_id, event.sender_id): return
    text = event.pattern_match.group(1) or "Adminတို့ရေ ခေါ်နေပါတယ်🤍"
    
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
        
        compact_admin_list = "  BOD  ".join(admin_mentions)
        adm_signal_text = (
            f"</b> <code>{escape_html(text)}</code>\n\n"
            f"🌌 <b>အက်ဒမင်များ ခဏလာကြတဲ့</b>\n"
            f"🦦:</b>\n"
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
    init_text = f"🌌 <b>𝘽𝙍𝙊𝘼𝘿𝘾𝘼𝙎𝙏 𝙎... 𝙄𝙉𝙄𝙏𝙄𝘼𝙏𝙀𝘿</b>\n⚡ <b>Transmission:</b> <code>Deploying signals...</code>"
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
            
    report_text = f"🌌 <b>𝘽𝙍𝙊𝘼𝘿𝘾𝘼𝙎𝙏 𝙏𝙍𝘼𝙉𝙎𝙈𝙄𝙎𝙎𝙄𝙊𝙉 𝘾𝙊𝙈𝙋𝙇𝙀𝙏𝙀𝘿</b>\n✅ <b>Success Sectors:</b> <code>{success}</code>\n❌ <b>Failed Sectors:</b>  <code>{fail}</code>"
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

            msg_template = "<b>{type} လုပ်သူ-</b> {mention}\nChat သန့်ရှင်းအောင် ဖျက်လိုက်ပါပြီ。"
            warn_msg = await event.respond(bq(msg_template.format(type=cmd_type, mention=mention)), parse_mode='html')
            for i in range(2, -1, -1):
                await asyncio.sleep(1)
                if i == 0: await warn_msg.delete(); break
                await warn_msg.edit(bq(msg_template.format(type=cmd_type, mention=mention) + f"\n\n{i} စက္ကန့်အတွင်း ပျောက်ကွယ်သွားပါမယ်।"), parse_mode='html')
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
                
                warn_text = f"⚠️ <b>❌𝘽𝙄𝙊 𝘼𝙇𝙀𝙍𝙏</b>\n{mention}, ဟိုက်<b>Bio</b> မှာ ဘာဖစ်တာလဲ တိချင်ဝူး.\n\n⚡ <b>Status:</b> <blockquote expandable>Message Disintegrated 🗑️</blockquote>"
                await event.respond(bq(warn_text), parse_mode='html')
                return 
            except Exception as e: print(f"Bio Filter Error: {e}")

    urls = re.findall(r'(https?://\S+|www\.\S+)', text)
    if urls and not (event.audio or event.voice):
        if await check_admin(chat_id, sender_id):
            try:
                f_msg = await bot1.forward_messages(event.chat_id, event.message)
                mention = f"<a href='tg://user?id={sender_id}'>{escape_html(sender.first_name)}</a>"
                await f_msg.reply(f"<b>Link Sender- {mention}</b>", parse_mode='html')
                await event.delete()
            except: pass
        else:
            try:
                await event.delete() 
                mention = f"<a href='tg://user?id={sender_id}'>{escape_html(sender.first_name)}</a>"
                await event.respond(bq(f"ဟိတ်🐬 {mention} link မပို့နဲ့ bro/sis မင်း link ကို ဖျက်လိုက်ပြီ"), parse_mode='html')
            except: pass

play_blocks = {}  

@bot1.on(events.NewMessage(pattern=r'^/play(?:\s+(.*))?'))
async def play_response(event):
    if event.is_private: return
    song_title = event.pattern_match.group(1)
    if not song_title: return await event.reply(bq("🛸 Please provide a song title!\nExample: <code>/play Faded</code>"), parse_mode='html')

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
        f"🌌 <b>{escape_html(owner_fullname)} Multimedia System</b>\n"
        f"💿 <b>Now Spinning:</b> <code>{escape_html(song_title.strip())}</code>\n"
        f"✨ <b>Requested By:</b> {mention}\n\n"
        f"💎 <b>Your Recent Playlist (Max 30):</b>\n"
        f"{playlist_text}"
        f"💡 <i>ဒါက မင်း ဖွင့်တဲ့ song 30 ပုဒ်အထိ သိမ်းထားပေးပြီး /play xxxကို ထိတာနဲ့ copyဖြစ်ပြီ🍬</i>"
    )

    inline_buttons = [
        [Button.url("ကျမတို့ဂလုကိုjoinပေးပါလား ဟိ🤍", "https://t.me/Comeback_BoD")],
        [Button.inline("songsစုထားမယ် နှိပ် 📥", data=f"save_pl_{user_id}")]
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
        return await event.answer("❌ ဒါက မင်းရဲ့ Playlist မဟုတ်ပါဘူး! မင်းကိုယ်ပိုင် /play စာရင်းကိုပဲ ပိတ်လို့ရပါမယ်။", alert=True)
        
    session_key = (chat_id, target_user_id)
    try:
        msg = await event.get_message()
        current_html = msg.html if msg else ""
        frozen_buttons = [[Button.url("Join Our Group", "https://t.me/Comeback_BoD")], [Button.inline("🔒 Playlist Locked & Ready to Save", data="playlist_frozen_done")]]
        if current_html: await event.edit(text=current_html, parse_mode='html', buttons=frozen_buttons)
        if session_key in play_blocks: del play_blocks[session_key]
        await event.answer("🔒 Playlist Terminated!", alert=True)
    except Exception as e: print(f"Callback Error: {e}")

@bot1.on(events.NewMessage(pattern=r'^/shadow(?:@\w+)?$'))
async def set_shadow_ban(event):
    if not await is_allowed(event.sender_id): return
    reply = await event.get_reply_message()
    if not reply or reply.sender_id == OWNER_ID: return
    dark_passenger_targets[event.chat_id] = reply.sender_id
    await event.respond(bq("Dark Passenger တပ်ဆင်ပြီးပါပြီ။"))

@bot1.on(events.NewMessage(pattern=r'^/unshadow(?:@\w+)?$'))
async def remove_shadow_ban(event):
    if not await is_allowed(event.sender_id): return
    if event.chat_id in dark_passenger_targets:
        del dark_passenger_targets[event.chat_id]
        await event.respond(bq("Dark Passenger ပြန်လည်ထွက်ခွာသွားပါပြီ။"))

@bot1.on(events.NewMessage(pattern=r'^စကားဝင်ပြော$'))
async def turn_on_talking(event):
    if event.sender_id != OWNER_ID or event.chat_id != SPECIFIC_CONTROL_GROUP: return
    global GLOBAL_TALK
    GLOBAL_TALK = True
    on_text = f"🌌 <b>𝙑𝙊𝘾𝘼𝙇 𝙋𝙍𝙊𝙏𝙊𝘾𝙊𝙇: 𝘼𝘾𝙏𝙄𝙑𝘼𝙏𝙀𝘿</b>\n🔊 Smart Reply Engine ကို Sector အားလုံးမှာ စကားဝင်ပြောဖို့ အသက်သွင်းပြီး"
    await event.reply(bq(on_text), parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^မပြောနဲ့တော့$'))
async def turn_off_talking(event):
    if event.sender_id != OWNER_ID or event.chat_id != SPECIFIC_CONTROL_GROUP: return
    global GLOBAL_TALK
    GLOBAL_TALK = False
    off_text = f"🔇 Smart Reply Engine ကို Absolute <b>Stealth Mode</b> ပြောင်းပြီး စကားပိတ်လိုက်ပါပြီ!"
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
                    f"🤫 <b>THE ILLUSION OF VOICE</b>\n\nMorgan lets your pathetic fingers click those keys, {mention}, but he owns the void.",
                    f"💀 <b>MORGAN'S ISOLATION CHAMBER</b>\n\nGo ahead and type another essay, {mention}. Morgan allows the keystroke but incinerates the output."
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

    # FORBIDDEN LANGUAGE FILTER SYSTEM
    if chat_id == SPECIFIC_GROUP and not await check_admin(chat_id, sender_id):
        if FORBIDDEN_SCRIPTS.search(event.text):
            try:
                await event.delete()
                sender = await event.get_sender()
                mention = f"<a href='tg://user?id={sender_id}'>{escape_html(sender.first_name if sender else 'User')}</a>"
                warn_text = f"⚠️ <b>SYSTEM SECURITY ALERT</b>\nHey {mention}, only <b>Burmese, English</b> and <b>Numbers</b> are allowed.\n\n⚡ <b>လုပ်ဆောင်ချက်:</b> တခြားဘာသာစကား messageကိုဖျက်ချလိုက်ခြင်း"
                return await event.respond(bq(warn_text), parse_mode='html')
            except Exception: pass

    # CHANNEL FORWARD FILTER SYSTEM
    if event.fwd_from and isinstance(event.fwd_from.from_id, types.PeerChannel):
        if not await check_admin(chat_id, sender_id):
            try:
                await event.delete()
                sender = await event.get_sender()
                mention = f"<a href='tg://user?id={sender_id}'>{escape_html(sender.first_name if sender else 'User')}</a>"
                warn_msg = f"🚫 <b>CHANNEL FORWARD NOT ALLOWED!</b>\n\n{mention} <b>[Erased]</b>"
                return await event.respond(bq(warn_msg), parse_mode='html')
            except Exception: pass


@bot1.on(events.NewMessage(pattern=r'^/ktr(?:\s+(\d+))?$'))
async def turn_on_reply_save_match(event):
    global IS_REPLY_ACTIVE, REPLY_INTERVAL
    if event.sender_id != OWNER_ID: return
    IS_REPLY_ACTIVE = True
    interval_arg = event.pattern_match.group(1)
    if interval_arg: REPLY_INTERVAL = int(interval_arg)
        
    ktr_on_text = f"🌌 <b>စာလိုက်ထောက် စကားဝင်ပြောမယ့် mood:𝘼𝘾𝙏𝙄𝙑𝙰𝚃𝙴𝘿</b>\n🎯 Target Tracking Matrix engaged.\n⚡ Core system will intervene every <code>{REPLY_INTERVAL}</code> messages, Chief!"
    await event.reply(bq(ktr_on_text), parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/ktrr$'))
async def turn_off_reply_save_match(event):
    global IS_REPLY_ACTIVE
    if event.sender_id != OWNER_ID: return
    IS_REPLY_ACTIVE = False
    ktr_off_text = f"⛔ System disengaged. Auto-response matrix entering offline sleep mode."
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
        await event.reply(f"💰 Boss ရဲ့ ဒေတာဘေ့စ်အကောင့်ထဲကို <code>{amount:,} MMK</code> အောင်မြင်စွာ ထည့်သွင်းပေးလိုက်ပါပြီ။", parse_mode='html')
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
            f"<blockquote>စနစ်ထဲက ကစားသမား စုစုပေါင်း <code>{result.modified_count}</code> ယောက်စလုံးကို တစ်ယောက်လျှင် <code>{amount:,} MMK</code> စီ အောင်မြင်စွာ ခွဲဝေထည့်သွင်းပေးလိုက်ပါပြီ Boss! 🔥</blockquote>"
        )
        await event.reply(success_text, parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ <b>Global Distribution Error:</b> <code>{e}</code>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/give\s+(\d+)'))
async def give_money_to_user(event):
    """[CMD 1] Owner မှ User စာကို Reply ပြန်၍ စိတ်ကြိုက်ငွေပေးအပ်နိုင်သော စနစ်"""
    if event.sender_id != OWNER_ID: return
    if not event.is_reply:
        return await event.reply("⚠️ <b>{f('ယခု command အား ငွေပေးလိုသော player ၏ စာကို Reply ပြန်၍ သုံးစွဲပါ Boss!')}</b>", parse_mode='html')
    
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
        await event.reply(f"💸 {target_mention} <b>{f('ထံသို့ စနစ်မှ')} <code>{amount:,} MMK</code> {f('လွှဲပြောင်းပေးအပ်ပြီးပါပြီ Boss!')}</b>", parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ <b>Give System Fault:</b> <code>{e}</code>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/takeall\s+(\d+)'))
async def take_money_from_everyone(event):
    """[CMD 2] Owner မှ စနစ်အတွင်းရှိ ကစားသမားအားလုံးထံမှ သတ်မှတ်ငွေပမာဏကို သိမ်းဆည်း/နှုတ်ယူသော စနစ်"""
    if event.sender_id != OWNER_ID: return
    amount = int(event.pattern_match.group(1))
    try:
        # နှုတ်ယူရာတွင် 0 အောက် ရောက်မသွားစေရန် Balance Threshold Check ပြုလုပ်ခြင်း
        result = await users_catcher_col.update_many(
            {},
            [{"$set": {"wallet_balance": {"$max": [0, {"$subtract": ["$wallet_balance", amount]}]}}}]
        )
        text = (
            f"🚨 <b>{f('SYSTEM DEDUCTION MATRIX ACTIVATED')}</b>\n"
            f"<blockquote>Database အတွင်းရှိ Player စုစုပေါင်း <code>{result.modified_count}</code> ယောက်ထံမှ တစ်ဦးလျှင် <code>{amount:,} MMK</code> စီ {f('နှုတ်ယူသိမ်းဆည်းလိုက်ပါပြီ Boss!')} ⚠️</blockquote>"
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
        
        spawn_msg = await bot1.send_message(
            chat_id, 
            f"🐸 <b>{f('Hey,Charater appeared!')}</b>\n"
            f"<blockquote><b>{f('Hey Collocter')}!</b> သူက ဘယ်သူဖြစ်မလဲ?ဒီပိုစ့်ကို <code>/who</code> နဲ့ အမြန်ဆုံး Reply ပြန်ပြီး စစ်ဆေးလိုက်ပါ!</blockquote>\n"
            f"If u still don't know how to use, Reply it to /help",
            parse_mode='html',
            file=storage_msg.media
        )
        
        active_group_spawns[chat_id] = {
            "spawn_msg_id": spawn_msg.id,
            "char_id": chosen_char["char_id"],
            "name": chosen_char["name"],
            "category": chosen_char["category"],
            "value": chosen_char["currency_value"],
            "rarity": chosen_char["rarity"],
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
        return await event.reply(f"❌ <b>လက်ရှိ Spawnတဲ့ Hai မရှိသေးပါဘူး သူငယ်ချင်း</b>", parse_mode='html')
        
    spawn_data = active_group_spawns[chat_id]
    if time.time() - spawn_data["spawn_time"] > 60:
        if chat_id in active_group_spawns: del active_group_spawns[chat_id]
        return await event.reply(f"⏱️ <b>TARGET GHOSTED / နောက်မှထပ်ကောက်ပါတော့😅</b>", parse_mode='html')
        
    if not event.is_reply or event.reply_to_msg_id != spawn_data["spawn_msg_id"]:
        return await event.reply(f"⚠️ <b>ပစ်မှတ်လွဲနေတယ်! ပေါ်လာတဲ့ ပိုစ့်ကို တိုက်ရိုက် Reply ပြန်ပေးပါ</b>", parse_mode='html')
        
    try:
        # 🖼️ GIF ပုံရိပ်ထဲတွင် ပြသမည့် စာလုံးစိမ်း Matrix စာသားများ
        matrix_display = (
            f"⚡ CHARACTER: IDENTITY REVEAL ⚡\n"
            f"🌟 Rarity: {spawn_data['rarity']}\n"
            f"🌐 Domain: {spawn_data['category']}\n"
            f"─────────────────────────────\n"
            f"   [ STATUS: TARGET LOCKED ]"
        )
        
        # စာသားအငြိမ် GIF ထုတ်လုပ်ခြင်း
        gif_file = generate_clean_who_gif(matrix_display)
        gif_file.name = "identity_reveal.gif"
        
        # 🔗 မီးခိုးရောင်ဘောင် လုံးဝမပါဘဲ တစ်ချက်နှိပ်ရုံဖြင့် ကော်ပီကူးနိုင်ရန် URL အဖြစ် ပြောင်းလဲခြင်း
        catch_command = f"/catch {spawn_data['name']}"
        encoded_command = urllib.parse.quote(catch_command)
        
        # ကတ်ပရှင်စာသားစနစ် (ဘောင်ကွက်များ လုံးဝမပါဝင်ပါ)
        reveal_text = (
            f"🌟 <b>Rarity:</b> {spawn_data['rarity']}\n"
            f"🌐 <b>Domain:</b> {spawn_data['category']}\n\n"
            f"🎩 <a href='https://t.me/share/url?url={encoded_command}'><b>{catch_command} (ကလစ်နှိပ်ပြီး Copy ယူပါ)</b></a>"
        )
        
        # ဖိုင်အား ကတ်ပရှင်နှင့်တကွ တွဲဖက်ပို့ပေးခြင်း
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
        return await event.reply(f"🛸 <b>ဒီ Dimension မှာ ဖမ်းစရာ ဘယ်သူမှ မရှိတော့ဘူး</b>", parse_mode='html')
        
    spawn_data = active_group_spawns[chat_id]
    if spawn_data["claimed"]: return

    if time.time() - spawn_data["spawn_time"] > 180:
        if chat_id in active_group_spawns: del active_group_spawns[chat_id]
        return await event.reply(f"⏱️ <b>TARGET GHOSTED / အချိန်ကုန်သွားလို့ ထွက်ပြေးသွားပြီ!😅</b>", parse_mode='html')

    if normalize_name(catch_name) != normalize_name(spawn_data["name"]): 
        return await event.reply(f"❌ <b>နာမည်မှားနေတယ် Boss! သေချာပြန်စစ်ပြီး /catch</b>", parse_mode='html')

    # [AI Optimization] Distributed Async Lock to absolutely block double-claim race conditions     
    async with spawn_locks[chat_id]:
        if active_group_spawns.get(chat_id, {}).get("claimed", True): return
        active_group_spawns[chat_id]["claimed"] = True

        mention = await get_html_mention(event, user_id)
        await ensure_user_registered(user_id, mention)
        
        try:
            # 1️⃣ ကစားသမား၏ Inventory Database အား အရင်ဆုံး Update ပြုလုပ်ခြင်း
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
            
            # 🏰 2️⃣ GUILD EXP SYSTEM INTEGRATION (ကတ်မိလို့ Clan Level တက်မတက် စစ်ဆေးခြင်း)
            guild_levelup_msg = ""
            user_guild = await guilds_col.find_one({"members": user_id})
            if user_guild:
                new_xp = user_guild["xp"] + 10
                current_level = user_guild["level"]
                
                # Level Up သတ်မှတ်ချက် စစ်ဆေးခြင်း (Level အလိုက် XP 500 လိုအပ်သည်)
                if new_xp >= (current_level * 500):
                    await guilds_col.update_one({"_id": user_guild["_id"]}, {"$set": {"xp": 0}, "$inc": {"level": 1}})
                    guild_levelup_msg = f"\n\n🏰 <b>Guild Level Up!</b> 👑\nသင်တို့၏ Guild <b>[{escape_html(user_guild['name'])}]</b> သည် Level <b>{current_level + 1}</b> သို့ တက်လှမ်းသွားပါပြီ။ 🎉"
                else:
                    await guilds_col.update_one({"_id": user_guild["_id"]}, {"$inc": {"xp": 10}})
            
            # 🐉 2.5️⃣ CYBER MATRIX CATCH GIF GENERATION (စာသားအငြိမ် ပြောင်းလဲခြင်း)
            character_name = spawn_data['name']
            
            matrix_display = (
                f"⚡ MISSION: TARGET SECURED ⚡\n"
                f"─────────\n"
                f"🃏 Character : {character_name}\n"
                f"🌟 Rarity    : {spawn_data['rarity']}\n"
                f"🪙 Bounty    : +{spawn_data['value']} MMK\n"
                f" [ ADDED TO HUNTER INVENTORY ]"
            )
            
            gif_file = generate_clean_catch_gif(matrix_display)
            gif_file.name = "catch_success.gif"
            
            # 3️⃣ Active ဖြစ်နေသော Spawn ဒေတာများအား Memory ထဲမှ ဖယ်ရှားခြင်း
            del active_group_spawns[chat_id]
            if chat_id in spawn_locks: del spawn_locks[chat_id]
            
            # Success Message (မီးခိုးရောင် <code> နှင့် <blockquote> များ လုံးဝဖယ်ရှားထားသည်)
            success_msg = (
                f"👤 <b>Hunter:</b> {mention}, U r nice Catcher 🦦\n"
                f"🃏 <b>Character:</b> <b>{escape_html(character_name)}</b>\n"
                f"🆔 <b>Asset ID:</b> <b>{spawn_data['char_id']}</b>\n"
                f"🌟 <b>Rarity Class:</b> {spawn_data['rarity']}\n"
                f"🪙 <b>Bounty Added:</b> <b>+{spawn_data['value']} MMK</b>\n\n"
                f"🎉 <b>Mission Secured!</b> ဒီ Character ကို သင့်ရဲ့ စုဆောင်းမှုထဲထည့်လိုက်ပြီ စစ်ဆေးရန် /hai လို့ရိုက်"
            )
            
            # အကယ်၍ Guild Level တက်ခဲ့ပါက ကတ်မိတဲ့စာသားအောက်မှာ တစ်ခါတည်း Message ချိတ်ပြပေးမည့်စနစ်
            if guild_levelup_msg:
                success_msg += guild_levelup_msg
                
            # စာသားသန့်သန့် ကတ်ပရှင်နှင့်အတူ စာလုံးစိမ်း GIF အား တွဲဖက်ပို့ပေးခြင်း 🔥
            await bot1.send_file(
                event.chat_id, 
                gif_file, 
                caption=success_msg, 
                reply_to=event.id, 
                parse_mode='html'
            )
            
        except Exception as e:
            # Error တစ်ခုခုတက်လျှင် တခြားလူ ပြန်ဖမ်းလို့ရအောင် Claimed ကို False ပြန်ပြင်ပေးခြင်း
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
    if not card: return await event.reply(f"❌ <b>{f('ကတ်ရှာမတွေ့ပါဘူး Bro!')}</b>", parse_mode='html')
        
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    user_harem = user_doc.get("harem", []) if user_doc else []
    
    owns_card = any(isinstance(x, dict) and x.get("char_id") == char_id for x in user_harem)
    if not owns_card: return await event.reply(f"❌ <b>{f('ဒီကတ်က သင့်ဆီမှာ မရှိသေးပါဘူး Bro!')}</b>", parse_mode='html')
        
    await users_catcher_col.update_one({"user_id": user_id}, {"$set": {"fav_card": char_id}})
    await event.reply(f"⭐️ <b>{escape_html(card['name'])}</b> ({char_id}) <code>{f('ကို Favorite ကတ်အဖြစ် သတ်မှတ်လိုက်ပါပြီ။')}</code>", parse_mode='html')

async def send_paginated_harem(client, chat_id, user_id, page=1, edit_msg_id=None):
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    if not user_doc or not user_doc.get("harem"):
        msg = f"🎒 <b>{f('သင့် Vault ထဲမှာ ဘာကတ်မှ မရှိသေးပါဘူး Bro!')}</b>"
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
    
    # Weight System (Legendary ကို ထိပ်ဆုံးပို့ပြီး Common ကို အောက်ဆုံးထားရန်)
    def get_rarity_weight(rarity_str):
        rarity_str = rarity_str.upper()
        if "LEGENDARY" in rarity_str: return 6
        if "LIMITED" in rarity_str: return 5
        if "MYTHIC" in rarity_str: return 4
        if "EPIC" in rarity_str: return 3
        if "RARE" in rarity_str: return 2
        if "COMMON" in rarity_str: return 1
        return 0

    # Rarity အမြင့်မှအနည်းစီပြီး တူပါက နာမည်အလိုက် A-Z ပြန်စီခြင်း
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
    output_text = f"🎒 {mention} <b>{f('s VAULT COLLECTION')}</b>\n"
    output_text += f"📑 <b>Page:</b> <code>{page}/{total_pages}</code> | <b>Total Unique:</b> <code>{len(lines)}</code>\n\n"
    for l in page_lines: output_text += l + "\n"
    output_text += f"\n⚡ ━━━━━⚡\n"
    
    # 📌 [NEW FEATURE] အောက်ခြေမှာ /fav ထားဖို့ လမ်းညွှန်စာသားကို Blockquote နဲ့ ထင်းခနဲပေါ်အောင် ထည့်သွင်းခြင်း
    output_text += f"<blockquote>💡 <code>/fav [BODxxxx]</code> ဆိုပြီး favorite ထားနိုင်ပါတယ် ညီ!</blockquote>"
    
    buttons = []
    nav_buttons = []
    if page > 1: nav_buttons.append(Button.inline("⬅️ Prev", data=f"hai_{page-1}_{user_id}"))
    if page < total_pages: nav_buttons.append(Button.inline("Next ➡️", data=f"hai_{page+1}_{user_id}"))
    if nav_buttons: buttons.append(nav_buttons)
    
    if not buttons: buttons = None
    
    fav_card_id = user_doc.get("fav_card")
    fav_media = None
    
    # 🛠️ [NEW LOGIC] Favorite ကတ် မသတ်မှတ်ထားရင် Vault ထဲက ကတ်တစ်ခုခုကို Random ရွေးပြီး ပုံထုတ်ပြမည့်စနစ်
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
            return await event.answer("⚠️ ဒါက သင့်ရဲ့ Vault မဟုတ်ပါဘူး Bro! ကြည့်ချင်ရင် /hai လို့ သီးသန့်ရိုက်ပါ!", alert=True)
        await send_paginated_harem(bot1, event.chat_id, target_user_id, page=page, edit_msg_id=event.message_id)

    elif action_type == "mktbuy":
        listing_id = data_parts[1]
        buyer_id = event.sender_id
        
        listing = await marketplace_col.find_one({"listing_id": listing_id})
        if not listing: return await event.answer("❌ ဒီအရောင်းစာရင်းက မရှိတော့ပါဘူး သို့မဟုတ် ရောင်းထွက်သွားပါပြီ။", alert=True)
            
        price = listing["price"]
        seller_id = listing["seller_id"]
        char_id = listing["char_id"]
        if buyer_id == seller_id: return await event.answer("⚠️ မိမိပစ္စည်းကို မိမိပြန်ဝယ်လို့ မရပါဘူး Bro!", alert=True)
            
        buyer_doc = await users_catcher_col.find_one({"user_id": buyer_id})
        if not buyer_doc or buyer_doc.get("wallet_balance", 0) < price:
            return await event.answer(f"❌ လက်ကျန်ငွေ မလုံလောက်ပါဘူး! လိုအပ်ချက်: {price} MMK", alert=True)
            
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
        if res.modified_count == 0: return await event.answer("❌ Transaction System Error!", alert=True)
            
        await users_catcher_col.update_one({"user_id": seller_id}, {"$set": {"harem": seller_harem}, "$inc": {"wallet_balance": price}})
        await marketplace_col.delete_one({"listing_id": listing_id})
        await event.answer("🎉 ဝယ်ယူမှု အောင်မြင်ပါပြီ!", alert=True)
        await event.edit(f"🤝 <b>{f('DEAL SECURED')}</b>\n\nဒီကတ်ပြားကို စျေးကွက်ထဲမှ အောင်မြင်စွာ သိမ်းပိုက်ပြီးပါပြီ။", parse_mode='html')

    elif action_type == "tr":
        sub_action = data_parts[1]
        sender_id = int(data_parts[2])
        target_id = int(data_parts[3])
        
        if sub_action == "canc":
            if event.sender_id in [sender_id, target_id]:
                await event.answer("❌ Transaction Terminated")
                await event.edit(f"❌ <b>{f('Exchange Contract Voided / ကတ်ဖလှယ်မှုကို ပယ်ဖျက်လိုက်ပါပြီ။')}</b>", parse_mode='html')
            else: await event.answer("⚠️ သင်က ဒီစာချုပ်ထဲမှာ ပါဝင်သူ မဟုတ်ပါဘူး Bro!", alert=True)
            return
            
        if sub_action == "conf":
            if event.sender_id != target_id: return await event.answer("⚠️ သင်က ဒီကုန်သွယ်မှုစာချုပ်ရဲ့ ကမ်းလှမ်းခံရသူ မဟုတ်ပါဘူး Bro!", alert=True)
            await event.answer("⚡ Finalizing Contract Matrix...")
            my_char_id, their_char_id = data_parts[4], data_parts[5]
            
            s_doc = await users_catcher_col.find_one({"user_id": sender_id})
            t_doc = await users_catcher_col.find_one({"user_id": target_id})
            s_harem = s_doc.get("harem", []) if s_doc else []
            t_harem = t_doc.get("harem", []) if t_doc else []
            
            s_item = next((x for x in s_harem if isinstance(x, dict) and x.get("char_id") == my_char_id and x.get("status") != "market"), None)
            t_item = next((x for x in t_harem if isinstance(x, dict) and x.get("char_id") == their_char_id and x.get("status") != "market"), None)
            
            if not s_item or not t_item: return await event.edit(f"❌ <b>{f('CONTRACT FAILED / ပိုင်ဆိုင်မှု အခြေအနေ ပြောင်းလဲသွားလို့ မအောင်မြင်တော့ပါ!')}</b>", parse_mode='html')
                
            s_harem.remove(s_item)
            t_harem.remove(t_item)
            s_harem.append({"char_id": their_char_id, "caught_date": time.time(), "rarity": t_item.get("rarity", "Unknown"), "status": "vault"})
            t_harem.append({"char_id": my_char_id, "caught_date": time.time(), "rarity": s_item.get("rarity", "Unknown"), "status": "vault"})
            
            await users_catcher_col.update_one({"user_id": sender_id}, {"$set": {"harem": s_harem}})
            await users_catcher_col.update_one({"user_id": target_id}, {"$set": {"harem": t_harem}})
            await event.edit(f"🤝 <b>{f('TRADE CONCLUDED / ကတ်လဲလှယ်ခြင်း လုပ်ငန်းစဉ် အောင်မြင်စွာ ပြီးဆုံးပါပြီ။ 🔥')}</b>", parse_mode='html')

    elif action_type == "cardjoin":
        g_chat_id = int(data_parts[1])
        p_id = event.sender_id
        if g_chat_id not in active_card_games: return await event.answer("❌ ဒီပွဲစဉ်က မရှိတော့ပါဘူး သို့မဟုတ် ပွဲစတင်သွားပါပြီ။", alert=True)
            
        game = active_card_games[g_chat_id]
        if game["status"] != "lobby": return await event.answer("❌ ကစားပွဲက စတင်သွားပြီမို့ ဝင်လို့မရတော့ပါဘူး!", alert=True)
        if p_id in game["players"]: return await event.answer("⚠️ သင်က ဒီပွဲစဉ်ထဲမှာ ပါဝင်ပြီးသားပါ Bro!", alert=True)
            
        user_doc = await users_catcher_col.find_one({"user_id": p_id})
        balance = user_doc.get("wallet_balance", 0) if user_doc else 0
        if balance < game["bet"]: return await event.answer(f"❌ လက်ကျန်ငွေ မလုံလောက်ပါဘူး! လိုအပ်ချက်: {game['bet']} MMK", alert=True)
            
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
            f"🃏 <b>HIGH CARD DRAW - MULTIPLAYER CASINO</b> 🃏\n"
            f"👑 <b>Host:</b> {host_mention}\n"
            f"💵 <b>Bet Stake:</b> <code>{game['bet']} MMK</code>\n\n"
            f"👥 <b>Joined Players ({len(game['players'])}):</b>\n"
        )
        for idx, (pid, name) in enumerate(game["players"].items(), start=1):
            p_mention = f"<a href='tg://user?id={pid}'><b>{escape_html(name)}</b></a>"
            lobby_text += f" {idx}. {p_mention}\n"
            
        lobby_text += f"\n📌 <i>Host က <code>/startgame</code> ဟုရိုက်၍ စတင်နိုင်ပါသည်။</i>"
        buttons = [[Button.inline("🃏 Join Match", data=f"cardjoin_{g_chat_id}")]]
        await event.edit(lobby_text, parse_mode='html', buttons=buttons)
        await event.answer("🎉 ပွဲစဉ်ထဲသို့ အောင်မြင်စွာ ပါဝင်လိုက်ပါပြီ!")

    elif action_type == "hilo":
        choice = data_parts[1]
        base_card = int(data_parts[2])
        bet_amount = int(data_parts[3])
        target_user_id = int(data_parts[4])
        if event.sender_id != target_user_id: return await event.answer("⚠️ ဒါက မင်းဆော့နေတဲ့ပွဲ မဟုတ်ဘူး Bro!", alert=True)
            
        user_doc = await users_catcher_col.find_one({"user_id": target_user_id})
        balance = user_doc.get("wallet_balance", 0) if user_doc else 0
        if balance < bet_amount: return await event.answer("❌ သင့် Wallet ထဲမှာ ပိုက်ဆံ မလုံလောက်တော့ပါဘူး။", alert=True)
            
        await event.answer("🎲 Shuffling Matrix Deck...")
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
            status_text = f"🎉 <b>{f('CHALLENGE WON')}!!! (+{bet_amount:,} MMK)</b>"
        else:
            await users_catcher_col.update_one({"user_id": target_user_id}, {"$inc": {"wallet_balance": -bet_amount}})
            status_text = f"💸 <b>{f('CHALLENGE LOST')}... (-{bet_amount:,} MMK)</b>"
            
        result_text = (
            f"🃏 <b>{f('HI-LO CASINO MATRIX RESULTS')}</b>\n"
            f"👤 <b>Player:</b> {mention}\n"
            f"💵 <b>Bet Stake:</b> <code>{bet_amount:,} MMK</code>\n\n"
            f"🎴 ယခင်ပြထားတဲ့ကတ်: <b>[ {base_display} ]</b>\n"
            f"🎲 နောက်ထွက်လာတဲ့ကတ်: ✨ <b>[ {new_display} ]</b> ✨\n"
            f"🎯 မင်းရွေးချယ်ခဲ့တာ: <code>{choice}</code>\n\n"
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
        f"🌌 <b>{f('Hey,This is your PROFILE🐬')}</b>\n"
        f"👤 <b>{f('Agent Identity')}:</b> {mention}\n"
        f"🦦 <b>{f('User Core ID')}:</b> <code>{user_id}</code>\n"
        f"🪙 <b>{f('Asset Liquid Capital')}:</b> <code>{balance} MMK</code>\n"
        f"🎒 <b>{f('Gross Captured Units')}:</b> <code>{total_caught} Units</code>\n"
        f"🛒 <b>{f('On Marketplace')}:</b> <code>{market_count} Cards</code>\n"
    )
    if counts:
        profile_text += f"📊 <b>{f('VAULT QUANTUM SEGMENTS')}:</b>\n"
        for tier, qty in sorted(counts.items(), key=lambda x: x[1], reverse=True):
            profile_text += f" ├─➩ {tier}: <b>{qty} Cards</b>\n"
        profile_text += f"⚡HELLO⚡\n"
    await event.reply(profile_text, parse_mode='html')

# ==========================================
# 🔍 9. SPECIFIC CHARACTER METRICS ARCHIVE (/check)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/check\s+([a-zA-Z0-9_]+)$'))
async def check_character_id_handler(event):
    char_id = event.pattern_match.group(1).upper()
    character = await characters_base_col.find_one({"char_id": char_id})
    if not character: return await event.reply(f"❌ <b>{f('DATABASE MISMATCH / Character ID အမှန်ရိုက်ပါဦး Bro!')}</b>", parse_mode='html')
        
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
        f"⚜️ <b>{f('DATA MATRIX ANALYTICS')}</b>\n"
        f"🆔 <b>{f('Asset Index')}:</b> <code>{character['char_id']}</code>\n"
        f"👤 <b>{f('Identity Label')}:</b> <code>{character['name']}</code>\n"
        f"🌐 <b>{f('Domain Realm')}:</b> <code>{character['category']}</code>\n"
        f"🌟 <b>{f('Rarity Tier')}:</b> {character['rarity']}\n"
        f"📈 <b>{f('Global Spawn Metrics')}:</b> <code>{spawn_count} Times spawned</code>\n"
        f"🏆 <b>{f('TOP 10 ELITE COLLECTORS')}:</b>\n\n"
        f"{leaderboard_str if leaderboard_str else 'Catcherမရှိသေးပါ။'}"
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
    
    if price <= 0: return await event.reply(f"❌ <b>{f('ဈေးနှုန်းက အနည်းဆုံး 1 MMK တော့ ရှိရမယ်လေ Bro!')}</b>", parse_mode='html')
    
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    user_harem = user_doc.get("harem", []) if user_doc else []
    
    char_item = next((x for x in user_harem if isinstance(x, dict) and x.get("char_id") == char_id and x.get("status") != "market"), None)
    if not char_item: return await event.reply(f"❌ <b>{f('သင့်ဆီမှာ ရောင်းစရာ အားလပ်နေတဲ့ ဒီလိုကတ် မရှိပါဘူး Bro!')}</b>", parse_mode='html')
        
    char_data = await characters_base_col.find_one({"char_id": char_id})
    if not char_data: return await event.reply(f"❌ <b>{f('Character ID မှားနေတယ် Bro!')}</b>", parse_mode='html')

    char_item["status"] = "market"
    await users_catcher_col.update_one({"user_id": user_id}, {"$set": {"harem": user_harem}})
    
    listing_id = f"L{random.randint(1000, 9999)}"
    mention = await get_html_mention(event, user_id)
    await marketplace_col.insert_one({
        "listing_id": listing_id, "seller_id": user_id, "seller_name": mention,
        "char_id": char_id, "char_name": char_data["name"], "price": price, "timestamp": time.time()
    })
    await event.reply(
        f"🏪 <b>{f('MARKET ITEM LOCKED IN ESCROW')} 🛒</b>\n"
        f"🎫 <b>{f('Listing Reference')}:</b> <code>{listing_id}</code>\n"
        f"👤 <b>{f('Asset Identity')}:</b> <code>{char_data['name']}</code>\n"
        f"💰 <b>{f('Bounty Evaluation')}:</b> <code>{price} MMK</code>\n"
        f"📦 ━━━━━📦", parse_mode='html'
    )

@bot1.on(events.NewMessage(pattern=r'^/buy\s+([a-zA-Z0-9_]+)(?:\s+(\d+))?$'))
async def buy_market_handler(event):
    buyer_id = event.sender_id
    char_id = event.pattern_match.group(1).upper()
    target_seller_id = event.pattern_match.group(2)
    
    if target_seller_id:
        target_seller_id = int(target_seller_id)
        if buyer_id == target_seller_id: return await event.reply(f"❌ <b>{f('မိမိပစ္စည်းကို မိမိပြန်ဝယ်၍ မရပါ!')}</b>", parse_mode='html')
            
        listing = await marketplace_col.find_one({"char_id": char_id, "seller_id": target_seller_id})
        if not listing: return await event.reply(f"❌ <b>{f('ဒီရောင်းသူထံမှ သတ်မှတ်ထားသော ကတ်အရောင်းစာရင်း ရှာမတွေ့ပါ!')}</b>", parse_mode='html')
            
        price = listing["price"]
        buyer_doc = await users_catcher_col.find_one({"user_id": buyer_id})
        if not buyer_doc or buyer_doc.get("wallet_balance", 0) < price: return await event.reply(f"❌ <b>{f('ငွေပမာဏ မလုံလောက်ပါဘူး Bro!')}</b>", parse_mode='html')
            
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
        if res.modified_count == 0: return await event.reply("❌ Transaction Error!")
        
        await users_catcher_col.update_one({"user_id": target_seller_id}, {"$set": {"harem": seller_harem}, "$inc": {"wallet_balance": price}})
        await marketplace_col.delete_one({"listing_id": listing["listing_id"]})
        return await event.reply(f"🎉 <b>{f('Direct Purchase Success!')}</b>", parse_mode='html')

    listings = await marketplace_col.find({"char_id": char_id}).sort("price", 1).to_list(length=10)
    if not listings: return await event.reply(f"❌ <b>{f('ဒီကတ်အတွက် လက်ရှိရောင်းချသူ မရှိသေးပါဘူး Bro!')}</b>", parse_mode='html')
        
    market_text = f"🛒 <b>{f('MARKETPLACE LISTINGS FOR')} : {char_id}</b>\n⚡ ━━━━ ⚡\n\n"
    buttons = []
    for item in listings:
        market_text += f"👤 <b>Merchant:</b> {item['seller_name']} [<code>{item['seller_id']}</code>]\n💰 <b>Price Value:</b> <code>{item['price']} MMK</code>\n\n"
        buttons.append([Button.inline(f"Buy via {item['price']} MMK", data=f"mktbuy_{item['listing_id']}")])
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
    
    if not top_users: return await event.reply(f"🏆 <b>{f('ဒီဂရုထဲမှာ Rank စာရင်း မရှိသေးပါဘူး Bro!')}</b>", parse_mode='html')
        
    msg = f"🏆 <b>{f('TOP 10 HUNTERS IN THIS GROUP')}</b>\n⚡ ━━━━⚡\n"
    for i, u in enumerate(top_users):
        count = u["group_catches"][str(chat_id)]
        user_display = u.get('fullname') or f"User {u['user_id']}"
        msg += f"<b>{i+1}.</b> {user_display} — <code>{count} ကတ်</code>\n"
    msg += f"\n⚡PARADOX Family:BOD⚡"
    await event.reply(msg, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/gtop(?:@\w+)?$'))
async def global_top_handler(event):
    cursor = users_catcher_col.find({"total_caught": {"$gt": 0}}).sort("total_caught", -1).limit(10)
    top_users = await cursor.to_list(length=10)
    
    if not top_users: return await event.reply(f"🏆 <b>{f('Global Leaderboard မှာ စာရင်းမရှိသေးပါဘူး Bro!')}</b>", parse_mode='html')
        
    msg = f"🌐 <b>{f('GLOBAL TOP 10 HUNTERS')}</b>\n"
    for i, u in enumerate(top_users):
        count = u.get("total_caught", 0)
        user_display = u.get('fullname') or f"User {u['user_id']}"
        msg += f"<b>{i+1}.</b> {user_display} — <code>{count} ကတ်</code>\n"
    msg += f"\n⚡PARADOX Family:BOD⚡"
    await event.reply(msg, parse_mode='html') 

# ==========================================
# 💰 12. WALLET BALANCE CHECKER (/checkp)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/checkp(?:@\w+)?$'))
async def check_points_balance(event):
    user_doc = await users_catcher_col.find_one({"user_id": event.sender_id})
    balance = user_doc.get("wallet_balance", 0) if user_doc else 0
    await event.reply(
        f"💰 <b>{f('Your Current Balance / လက်ရှိလက်ကျန်ငွေတန်ဖိုး')}:</b>\n"
        f"<blockquote><code>{balance} Myanmar Kyats</code> 🪙</blockquote>\n"
        f"ဂိမ်းများကစားရန်➡ /game", parse_mode='html'
    )

# ==========================================
# 🎁 13. PEER-TO-PEER ASSET TRANSFER (/gift)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/gift\s+(.+)$'))
async def gift_asset_handler(event):
    if not event.is_reply: return await event.reply(f"❌ <b>{f('ဘယ်သူ့ကို ပေးမှာလဲ Bro? အဲ့ဒီလူ့စာကို Reply ပြန်ပြီး ကုဒ်ရိုက်ပေးပါဦး!')}</b>", parse_mode='html')
        
    char_id = event.pattern_match.group(1).strip().upper()
    sender_id = event.sender_id
    reply_msg = await event.get_reply_message()
    receiver_id = reply_msg.sender_id
    
    if sender_id == receiver_id: return await event.reply(f"❌ <b>{f('Loop Error! မိမိကိုယ်ကို ပြန်လည် Gift ပေးလို့ မရပါဘူး Bro!')} 💀</b>", parse_mode='html')

    sender_doc = await users_catcher_col.find_one({"user_id": sender_id})
    sender_harem = sender_doc.get("harem", []) if sender_doc else []
    
    char_item = next((x for x in sender_harem if isinstance(x, dict) and x.get("char_id") == char_id and x.get("status") != "market"), None)
    if not char_item: return await event.reply(f"❌ <b>{f('Transaction Refused! သင့် Vault ထဲမှာ အားလပ်နေတဲ့ ဒီကတ် မရှိဘူးနော်!')}</b>", parse_mode='html')

    sender_harem.remove(char_item)
    await users_catcher_col.update_one({"user_id": sender_id}, {"$set": {"harem": sender_harem}})
    
    r_mention = await get_html_mention(event, receiver_id)
    await users_catcher_col.update_one(
        {"user_id": receiver_id},
        {"$push": {"harem": {"char_id": char_id, "caught_date": time.time(), "rarity": char_item.get("rarity", "Unknown"), "status": "vault"}}, "$inc": {"total_caught": 1}, "$set": {"fullname": r_mention}},
        upsert=True
    )
    await event.reply(
        f"🎁 <b>{f('ASSET TRANSFER SECURED')}</b>\n"
        f"⚡PARADOX Family:BOD⚡\n"
        f"<blockquote><b>Successful Sent</b>! <code>{char_id}</code> ပိုင်ဆိုင်မှုကတ်ကို {r_mention} ထံသို့ လွှဲပြောင်းပေးအပ်လိုက်ပါပြီ Bestie! ⚡🤝</blockquote>", parse_mode='html'
    )

# ==========================================
# 🤝 14. PEER TRADE MATRIX (/trade)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/trade\s+([a-zA-Z0-9_]+)\s+([a-zA-Z0-9_]+)$'))
async def trade_proposal_handler(event):
    if not event.is_reply: return await event.reply(f"❌ <b>{f('Trade လုပ်မယ့်သူရဲ့ စာကို Reply ပြန်ပြီး ခေါ်ယူပေးပါ Bro!')}</b>", parse_mode='html')
    
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
    if not s_has: return await event.reply(f"❌ သင့်ထံတွင် ID: <code>{my_char_id}</code> အားလပ်လျက် မရှိပါ Bro!", parse_mode='html')
    if not t_has: return await event.reply(f"❌ ၎င်းထံတွင် ID: <code>{their_char_id}</code> အားလပ်လျက် မရှိပါ Bro!", parse_mode='html')
        
    s_char = await characters_base_col.find_one({"char_id": my_char_id})
    t_char = await characters_base_col.find_one({"char_id": their_char_id})
    
    trade_text = (
        f"🤝 <b>{f('EXCHANGE CONTRACT / ဖလှယ်မှု စာချုပ်')}</b>\n"
        f"⚡PARADOX Family:BOD⚡\n"
        f"📤 <b>{f('Your Offer')}:</b> <code>{s_char['name']}</code> ({my_char_id})\n"
        f"📥 <b>{f('Their Request')}:</b> <code>{t_char['name']}</code> ({their_char_id})\n"
        f"⚡ ━━━━ ⚡\n"
        f"<blockquote><b>{f('Notice')}:</b> ဒီစာချုပ်ကို အတည်ပြုဖို့ ဆုံးဖြတ်ပိုင်ခွင့်က ကမ်းလှမ်းခံရသူထံမှာပဲ ရှိပါတယ်နော် Bro! ⚡👀</blockquote>"
    )
    buttons = [[
        Button.inline("🤝 Confirm Contract", data=f"tr_conf_{sender_id}_{target_user_id}_{my_char_id}_{their_char_id}"),
        Button.inline("❌ Void Contract", data=f"tr_canc_{sender_id}_{target_user_id}")
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
# ၁။ SLOT GAME HANDLER (RATE LIMIT FREE DETECTED)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/slot\s+(\d+)$'))
async def slot_game_handler(event):
    user_id = event.sender_id
    bet = int(event.pattern_match.group(1))
    if bet <= 0: return await event.reply("❌ <b>လောင်းကြေးက အနည်းဆုံး 1 MMK ရှိရပါမယ် Bro!</b>", parse_mode='html')
        
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    balance = user_doc.get("wallet_balance", 0) if user_doc else 0
    if balance < bet: return await event.reply(f"❌ <b>သင့်ထံတွင် Ngwe မလုံလောက်ပါဘူး! လက်ကျန်: {balance} MMK</b>", parse_mode='html')
        
    await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": -bet}})
    mention = await get_html_mention(event, user_id)
    
    # Cyber Dragon Generator
    gif_file = generate_mod_dragon_gif(getattr(event.sender, 'first_name', 'Player'), 'slot')
    gif_file.name = "slot_spinning.gif"
    
    symbols = ["​🐯", "​🦁", "​🐃", "​🦓", "🦀", "🐄", "🦅"]
    res_syms = random.choices(symbols, k=7)
    res_str = " | ".join(res_syms)
    
    counts = Counter(res_syms)
    most_common_sym, max_count = counts.most_common(1)[0]
    win_amount = 0
    
    if max_count == 7:
        win_amount = bet * 77 if most_common_sym == "​🐃" else bet * 50
        status_text = f"🥶 <b>{f('GOD-TIER JACKPOT')}!!! (+{win_amount:,} MMK)</b>"
    elif max_count == 6:
        win_amount = bet * 25
        status_text = f"💎 <b>{f('MEGA MULTI-MATCH')}!!! (+{win_amount:,} MMK)</b>"
    elif max_count == 5:
        win_amount = bet * 12
        status_text = f"⚡ <b>{f('SUPER MULTI-MATCH')}!! (+{win_amount:,} MMK)</b>"
    elif max_count == 4:
        win_amount = bet * 5
        status_text = f"😲 <b>{f('QUADRA SYNC MATCH')}! (+{win_amount:,} MMK)</b>"
    elif max_count == 3:
        win_amount = int(bet * 2.5)
        status_text = f"🤤 <b>{f('TRIPLE COMBINATION')}. (+{win_amount:,} MMK)</b>"
    elif max_count == 2:
        win_amount = int(bet * .5)
        status_text = f"🤣 <b>{f('SINGLE PAIR MATCH')}. လောင်းကြေးတစ်ဝက်ပြန်ရမယ်။ (+{win_amount:,} MMK)</b>"
    else: status_text = f"🤪 <b>ကံမကောင်းသေးပါဘူး Bro! လောင်းကြေး ရှုံးနိမ့်သွားပါပြီ။ (-{bet:,} MMK)</b>"
        
    if win_amount > 0: await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": win_amount}})
        
    final_text = (
        f"🎰 <b>{f('BOD အပေးကြမ်း 7ခုတန်း စလော့')}</b>\n⚡ PARADOX Family: BOD ⚡\n"
        f"👤 <b>Player:</b> {mention}\n💵 <b>Bet Amount:</b> <code>{bet:,} MMK</code>\n\n🎰 <b>[ {res_str} ]</b>\n\n{status_text}"
    )
    # Edit အစား GIF ဖြင့် တိုက်ရိုက်ပို့ခြင်းကြောင့် မြန်ဆန်ပြီး အန္တရာယ်ကင်းပါသည် 🔥
    await event.client.send_file(event.chat_id, gif_file, caption=final_text, reply_to=event.id, parse_mode='html')

# ==========================================
# ၂။ MULTIPLAYER CARD GAME SUITE
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/cardgame\s+(\d+)$'))
async def create_cardgame_lobby(event):
    if event.is_private: return
    chat_id = event.chat_id
    bet = int(event.pattern_match.group(1))
    if bet < 100: return await event.reply("❌ <b>အနည်းဆုံးလောင်းကြေး 100 MMK ရှိရပါမယ် Boss!</b>", parse_mode='html')
    if chat_id in active_card_games: return await event.reply("⚠️ <b>ဒီဂရုထဲမှာ အခြားပွဲစဉ်တစ်ခု လက်ရှိလည်ပတ်နေဆဲ ဖြစ်ပါတယ်။</b>", parse_mode='html')
        
    host_id = event.sender_id
    user_doc = await users_catcher_col.find_one({"user_id": host_id})
    balance = user_doc.get("wallet_balance", 0) if user_doc else 0
    if balance < bet: return await event.reply(f"❌ <b>သင့်ထံတွင် Ngwe မလုံလောက်ပါဘူး! လက်ကျန်: {balance} MMK</b>", parse_mode='html')
        
    await users_catcher_col.update_one({"user_id": host_id}, {"$inc": {"wallet_balance": -bet}})
    try:
        sender = await event.get_sender()
        fullname = f"{getattr(sender, 'first_name', '')} {getattr(sender, 'last_name', '')}".strip() or getattr(sender, 'username', '') or "Host"
    except: fullname = "Host"
        
    host_mention = f"<a href='tg://user?id={host_id}'><b>{escape_html(fullname)}</b></a>"
    active_card_games[chat_id] = {"host_id": host_id, "bet": bet, "players": {host_id: fullname}, "status": "lobby", "msg_id": None}
    
    lobby_text = (
        f"🃏 <b>CARD နံပါတ်ကြီးသူနိုင်မယ့် - MULTIPLAYER CASINO</b> 🃏\n⚡PARADOX Family:BOD⚡\n"
        f"👑 <b>Host:</b> {host_mention}\n💵 <b>Bet Stake:</b> <code>{bet} MMK</code>\n\n👥 <b>Joined Players (1):</b>\n 1. {host_mention} (Host)\n\n"
        f"📌 <i>အနည်းဆုံး ၂ ယောက်ပြည့်လျှင် Host က <code>/startgame</code> ဟုရိုက်၍ စတင်နိုင်ပါသည်။</i>"
    )
    buttons = [[Button.inline("🃏ပါမယ့်သူဆိုနှိပ်လိုက်", data=f"cardjoin_{chat_id}")]]
    msg = await event.reply(lobby_text, parse_mode='html', buttons=buttons)
    active_card_games[chat_id]["msg_id"] = msg.id

@bot1.on(events.NewMessage(pattern=r'^/startgame(?:@\w+)?$'))
async def start_cardgame_handler(event):
    if event.is_private: return
    chat_id = event.chat_id
    if chat_id not in active_card_games: return await event.reply("❌ <b>ဒီဂရုမှာ စတင်စရာ ကစားပွဲ Lobby မရှိသေးပါဘူး!</b>", parse_mode='html')
        
    game = active_card_games[chat_id]
    if game["status"] != "lobby": return
    if event.sender_id != game["host_id"]: return await event.reply("❌ <b>ပွဲစဉ်ကို Host လုပ်သူသာလျှင် စတင်ခွင့် ရှိပါတယ်။</b>", parse_mode='html')
    if len(game["players"]) < 2: return await event.reply("👥 <b>ကစားပွဲစတင်ရန် အနည်းဆုံး ကစားသမား ၂ ဦး လိုအပ်ပါတယ် Bro!</b>", parse_mode='html')
        
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
        
    gif_file = generate_mod_dragon_gif("Casino Matrix", 'cardgame')
    gif_file.name = "card_showdown.gif"

    result_text = f"🃏 <b>HIGH CARD CASINO RESULTS / ဖဲချပ်ဆွဲပွဲ ရလဒ်</b> 🃏\n💰 <b>Total Prize Pool:</b> <code>{pool} MMK</code>\n⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n\n"
    for pid, data in results.items():
        p_mention = f"<a href='tg://user?id={pid}'><b>{escape_html(data['name'])}</b></a>"
        win_tag = " 🏆 (WINNER)" if pid in winners else ""
        result_text += f"🃏 {p_mention} drew card: <b>[{data['score']}/10]</b>{win_tag}\n"
    result_text += f"😎💰\n"
    if len(winners) > 1: result_text += f"🤝 <b>သရေကျသဖြင့် ပါဝင်သူများ တစ်ဦးလျှင် <code>{split_prize} Myanmar Kyats</code> စီ ရရှိသွားပါပြီ!</b>"
    else:
        winner_mention = f"<a href='tg://user?id={winners[0]}'><b>{escape_html(results[winners[0]]['name'])}</b></a>"
        result_text += f"🎉 <b>{winner_mention} ကံထူးပြီး စုစုပေါင်းဆုကြေး <code>{pool} MMK</code> အားလုံးကို သိမ်းပိုက်သွားပါပြီ!</b>"
        
    del active_card_games[chat_id]
    await event.client.send_file(event.chat_id, gif_file, caption=result_text, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/cancelgame(?:@\w+)?$'))
async def cancel_cardgame_handler(event):
    if event.is_private: return
    chat_id = event.chat_id
    if chat_id not in active_card_games: return await event.reply("❌ <b>ဖျက်သိမ်းစရာ ကစားပွဲ Lobby မရှိသေးပါဘူး Bro!</b>", parse_mode='html')
        
    game = active_card_games[chat_id]
    if event.sender_id != game["host_id"] and event.sender_id != OWNER_ID: return await event.reply("❌ <b>ဒီကစားပွဲကို တည်ဆောက်ခဲ့တဲ့ Host ကိုယ်တိုင်သာ ဖျက်သိမ်းခွင့်ရှိပါတယ် Boss!</b>", parse_mode='html')
        
    for pid in game["players"].keys(): await users_catcher_col.update_one({"user_id": pid}, {"$inc": {"wallet_balance": game["bet"]}})
    bet_amount = game["bet"]
    del active_card_games[chat_id] 
    await event.reply(
        f"🎯 <b>{f('GAME LOBBY TERMINATED')}</b>\n⚡PARADOX Family:BOD⚡\n"
        f"<blockquote><b>Refund Successful</b>! ကစားသမားများထံ လောင်းကြေးငွေ <code>{bet_amount} MMK</code> စီကို ပြန်လည် ထည့်သွင်းပေးပြီးပါပြီ။ ✨🤝</blockquote>", parse_mode='html'
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
    if not user_data or user_data.get("wallet_balance", 0) < bet_amount: return await event.reply("❌ <b>လောင်းကြေးမလောက်ပါဘူး Boss!</b>", parse_mode='html')
    
    gif_file = generate_mod_dragon_gif(choice, 'flip')
    gif_file.name = "coin_flip.gif"
    
    result = random.choice(["ခေါင်း", "ပန်း"])
    mention = await get_html_mention(event, user_id)
    
    if choice == result:
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": bet_amount}})
        caption_text = f"🪙 {mention} <b>{f('COIN FLIP SUCCESS')}!!</b>\n✨ ရလဒ်က [ <b>{result}</b> ] ဖြစ်ပါတယ်!\n🎉 Boss မှန်သွားလို့ ပွဲနိုင်ပါတယ်။ <code>+{bet_amount} MMK</code>"
    else:
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": -bet_amount}})
        caption_text = f"🪙 {mention} <b>{f('COIN FLIP FAILED')}..</b>\n💨 ရလဒ်က [ <b>{result}</b> ] ဖြစ်ပါတယ်!\n💸 မှားသွားတဲ့အတွက် လောင်းကြေးရှုံးသွားပါပြီ။ <code>-{bet_amount} MMK</code>"
        
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
    if not user_data or user_data.get("wallet_balance", 0) < bet_amount: return await event.reply("❌ <b>လောင်းကြေးမလောက်ပါဘူး Boss!</b>", parse_mode='html')

    # Telegram Animation အစစ်ကို ထိန်းသိမ်းထားပါသည်
    dice_msg = await bot1.send_message(chat_id, file=types.InputMediaDice(emoticon="🎲"))
    await asyncio.sleep(2)
    dice_value = dice_msg.media.value
    
    if dice_value >= 4:  
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": bet_amount}})
        await event.reply(f"🎉 <b>{f('MATRIX WINNER')}! အမှတ် {dice_value} ကျလို့ နိုင်သွားပါပြီ!</b>\n🪙 Bounty Added: <code>+{bet_amount} MMK</code>", parse_mode='html')
    else: 
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": -bet_amount}})
        await event.reply(f"💸 <b>{f('MATRIX LOSE')}! အမှတ် {dice_value} ပဲကျလို့ ရှုံးသွားပါပြီဗျာ!</b>\n🪙 Balance Deducted: <code>-{bet_amount} MMK</code>", parse_mode='html')

# ==========================================
# ၅။ HI-LO INTERACTIVE HANDLER
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/hilo\s+(\d+)$'))
async def hilo_game_handler(event):
    user_id = event.sender_id
    bet_amount = int(event.pattern_match.group(1))
    if bet_amount <= 0: return await event.reply("❌ <b>လောင်းကြေးက အနည်းဆုံး 1 MMK ရှိရပါမယ် Bro!</b>", parse_mode='html')
        
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    balance = user_doc.get("wallet_balance", 0) if user_doc else 0
    if balance < bet_amount: return await event.reply(f"❌ <b>သင့်ထံတွင် Ngwe မလုံလောက်ပါဘူး! လက်ကျန်: {balance:,} MMK</b>", parse_mode='html')
        
    base_card = random.randint(2, 12) 
    card_map = {11: "J", 12: "Q", 13: "K"}
    base_display = card_map.get(base_card, str(base_card))
    buttons = [[
        Button.inline("🔼 နောက်Cardက (ပိုကြီးမယ်)", data=f"hilo_HIGH_{base_card}_{bet_amount}_{user_id}"),
        Button.inline("🔽 နောက်Cardက (ပိုငယ်မယ်)", data=f"hilo_LOW_{base_card}_{bet_amount}_{user_id}")
    ]]
    await event.reply(
        f"🃏 <b>{f('HI-LO CASINO MATRIX')}</b>\n⚡ PARADOX Family: BOD ⚡\n⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n\n"
        f"💵 လောင်းကြေး: <code>{bet_amount:,} MMK</code>\n🎴 လက်ရှိကတ်ပြား: ✨ <b>[ {base_display} ]</b> ✨\n\n"
        f"<blockquote>နောက်ထွက်မည့် ကတ်ပြားသည် ပိုကြီးမလား (HIGHER) 🔼 ဒါမှမဟုတ် ပိုငယ်မလား (LOWER) 🔽 ကို ရွေးချယ်ပါ!</blockquote>",
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
    current_time = time.time()
    last_daily = user_doc.get("daily_cooldown", 0)
    
    if current_time - last_daily < 86400:
        rem_time = int(86400 - (current_time - last_daily))
        return await event.reply(f"⏳ {mention} <b>{f(' cooldown မပြည့်သေးပါ!')} ရယူရန် {str(timedelta(seconds=rem_time))} နာရီ လိုအပ်ပါသေးတယ်။</b>", parse_mode='html')
        
    bonus = random.randint(3000, 10000)
    await users_catcher_col.update_one(
        {"user_id": user_id},
        {"$inc": {"wallet_balance": bonus}, "$set": {"daily_cooldown": current_time, "fullname": mention}}
    )
    
    gif_file = generate_mod_dragon_gif(getattr(event.sender, 'first_name', 'Hunter'), 'daily')
    gif_file.name = "daily_bounty.gif"
    
    success_text = f"🎁 {mention} <b>{f('နေ့စဉ်ထောက်ပံ့ကြေး')} <code>+{bonus} MMK</code> {f('ကို သင့် Vault ထဲသို့ ထည့်သွင်းပေးလိုက်ပါပြီ Boss!')}</b>"
    await event.client.send_file(event.chat_id, gif_file, caption=success_text, reply_to=event.id, parse_mode='html')

# ==========================================
# ၇။ SYSTEM RICHEST LEADERBOARD
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/richest(?:@\w+)?$'))
async def system_richest_leaderboard(event):
    cursor = users_catcher_col.find({"wallet_balance": {"$gt": 0}}).sort("wallet_balance", -1).limit(10)
    richest_users = await cursor.to_list(length=10)
    
    if not richest_users: return await event.reply(f"🏆 <b>{f('Leaderboard matrix data clear ဖြစ်နေပါတယ် Bro!')}</b>", parse_mode='html')
        
    gif_file = generate_mod_dragon_gif("Global Grid", 'richest')
    gif_file.name = "leaderboard.gif"

    msg = f"💰 <b>{f('BODကာစီနိုနဲ့Cardဖမ်းလောကရဲ့ - TOP 10 RICHEST players')}</b>\n⚡ ━━━━━⚡\n"
    for i, u in enumerate(richest_users):
        bal = u.get("wallet_balance", 0)
        user_display = u.get('fullname') or f"User {u['user_id']}"
        msg += f"<b>{i+1}.</b> {user_display} — <code>{bal:,} MMK</code> 🪙\n"
    msg += f"\nFamily Cluster: <b>Brotherhood of Dexter</b>"
    
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
    if not char_item: return await event.reply("❌ <b>{f('သင့် Vault ထဲမှာ အားလပ်နေတဲ့ ဒီကတ် မရှိပါဘူး Bro!')}</b>", parse_mode='html')
    
    char_data = await characters_base_col.find_one({"char_id": char_id})
    if not char_data: return await event.reply("❌ <b>{f('Character ID database ထဲမှာ ရှာမတွေ့ပါ!')}</b>", parse_mode='html')
    
    melt_value = int(char_data.get("currency_value", 100) * 0.5)
    
    user_harem.remove(char_item)
    await users_catcher_col.update_one(
        {"user_id": user_id},
        {"$set": {"harem": user_harem}, "$inc": {"wallet_balance": melt_value}}
    )
    
    gif_file = generate_mod_dragon_gif(char_id, 'scrap')
    gif_file.name = "melt_engine.gif"
    
    response = f"♻️ {mention} <b>မှ ကတ်ပြား</b> <code>{char_data['name']}</code> <b>{f('အား ဖျက်စီးပြီး စနစ်ထံ ရောင်းချခဲ့သည်။')} (+<code>{melt_value} MMK</code>)</b>"
    await event.client.send_file(event.chat_id, gif_file, caption=response, reply_to=event.id, parse_mode='html')

# ==========================================
# ၉။ HIGH RISK GAMBLE ENGINE (DOUBLE OR NOTHING)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/gamble\s+(\d+)$'))
async def high_risk_gamble_handler(event):
    user_id = event.sender_id
    bet = int(event.pattern_match.group(1))
    mention = await get_html_mention(event, user_id)
    
    if bet < 50: return await event.reply("❌ <b>အနည်းဆုံး လောင်းကြေး 50 MMK ရှိရပါမည် Bro!</b>", parse_mode='html')
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    balance = user_doc.get("wallet_balance", 0) if user_doc else 0
    if balance < bet: return await event.reply(f"❌ <b>သင့်ထံတွင် ငွေမလုံလောက်ပါ! လက်ကျန်: {balance} MMK</b>", parse_mode='html')
    
    gif_file = generate_mod_dragon_gif(getattr(event.sender, 'first_name', 'Gambler'), 'gamble')
    gif_file.name = "gamble_risk.gif"
    
    if random.choice([True, False]):
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": bet}})
        response = f"🔥 {mention} <b>{f('RISK VENTURE SUCCESS')}! အနိုင်ရရှိသဖြင့် လောင်းကြေးနှစ်ဆ ရရှိသည်။ (+<code>{bet} MMK</code>)</b>"
    else:
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": -bet}})
        response = f"💸 {mention} <b>{f('RISK VENTURE FAILED')}! ကံမကောင်းစွာဖြင့် လောင်းကြေးအားလုံး ဆုံးရှုံးသွားသည်။ (-<code>{bet} MMK</code>)</b>"
        
    await event.client.send_file(event.chat_id, gif_file, caption=response, reply_to=event.id, parse_mode='html')
        
@bot1.on(events.NewMessage(pattern=r'^/pay\s+(\d+)$'))
async def peer_to_peer_payment(event):
    """[CMD 7] Player အချင်းချင်း Reply ပြန်ရုံဖြင့် လုံခြုံစွာ ငွေလွှဲနိုင်သော Banking စနစ်"""
    if not event.is_reply: return await event.reply("⚠️ <b>ငွေလွှဲလိုသော Player ၏ စာကို Reply ပြန်၍ သုံးစွဲပါ Bro!</b>", parse_mode='html')
    amount = int(event.pattern_match.group(1))
    sender_id = event.sender_id
    
    reply_msg = await event.get_reply_message()
    receiver_id = reply_msg.sender_id
    if sender_id == receiver_id: return await event.reply("❌ <b>မိမိကိုယ်ကို ငွေပြန်လွှဲ၍ မရနိုင်ပါ Bro!</b>", parse_mode='html')
    
    s_doc = await users_catcher_col.find_one({"user_id": sender_id})
    if not s_doc or s_doc.get("wallet_balance", 0) < amount: return await event.reply("❌ <b>သင့်ထံတွင် လွှဲပြောင်းရန် ငွေအလုံအလောက် မရှိပါ Bro!</b>", parse_mode='html')
    
    r_mention = await get_html_mention(event, receiver_id)
    s_mention = await get_html_mention(event, sender_id)
    await ensure_user_registered(receiver_id, r_mention)
    
    await users_catcher_col.update_one({"user_id": sender_id}, {"$inc": {"wallet_balance": -amount}})
    await users_catcher_col.update_one({"user_id": receiver_id}, {"$inc": {"wallet_balance": amount}})
    
    await event.reply(f"💸 {s_mention} <b>{f('မှ')} {r_mention} {f('ထံသို့')} <code>{amount:,} MMK</code> {f('အောင်မြင်စွာ လွှဲပြောင်းပေးလိုက်ပါပြီ။')} 🤝</b>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/hunt(?:@\w+)?$'))
async def text_adventure_hunt_handler(event):
    """[CMD 8] Cooldown အခြေခံ၍ တောလိုက်ကာ ရမှတ်/ငွေသား ရှာဖွေနိုင်သော စနစ် (Mini-Excursion)"""
    user_id = event.sender_id
    mention = await get_html_mention(event, user_id)
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    
    current_time = time.time()
    last_hunt = user_doc.get("hunt_cooldown", 0) if user_doc else 0
    if current_time - last_hunt < 180:
        return await event.reply(f"⏳ {mention} <b>{f('အမဲလိုက်ထွက်ခြင်း နားပါဦး Bro!')} Cooldown စက္ကန့် {int(180 - (current_time - last_hunt))}s လိုပါသေးသည်။</b>", parse_mode='html')
        
    earned = random.randint(200, 80000)
    events_pool = [
        f"🌲 {mention} <b>{f('တောနက်ထဲ စွန့်စားခန်းထွက်ရင်း ရတနာသေတ္တာအဟောင်းကို တွေ့ရှိခဲ့တယ်!')} (+<code>{earned} MMK</code>)</b>",
        f"⚔️ {mention} <b>{f('BOD ရန်သူတော် အဖွဲ့အစည်းကို နှိမ်နင်းပြီး ဘောနပ်စ် ဆုကြေးရရှိခဲ့တယ်!')} (+<code>{earned} MMK</code>)</b>",
        f"🌌 {mention} <b>{f('Dimension Matrix ထဲမှာ Quantum Points တွေ ကောက်ရခဲ့တယ်!')} (+<code>{earned} MMK</code>)</b>"
    ]
    await users_catcher_col.update_one(
        {"user_id": user_id},
        {"$inc": {"wallet_balance": earned}, "$set": {"hunt_cooldown": current_time, "fullname": mention}},
        upsert=True
    )
    await event.reply(random.choice(events_pool), parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/market(?:@\w+)?$'))
async def global_market_catalog_viewer(event):
    """[CMD 9] စျေးကွက်အတွင်း လက်ရှိရောင်းချရန် တင်ထားသော ကတ်များ အားလုံးအား သေသပ်စွာ ကြည့်ရှုခြင်း စနစ်"""
    listings = await marketplace_col.find().sort("timestamp", -1).limit(15).to_list(length=15)
    if not listings: return await event.reply(f"🏪 <b>{f('လက်ရှိ စျေးကွက်ထဲမှာ မည်သည့်ပစ္စည်းမှ တင်မထားသေးပါ Bro!')}</b>", parse_mode='html')
    
    msg = f"🏪 <b>{f('BOD HUB AUCTION MARKETPLACEL DATA')}</b>\n⚡ ━━━━━━━ ⚡\n"
    for item in listings:
        msg += f"📦 <b>Card:</b> <code>{item['char_name']}</code> [<code>{item['char_id']}</code>]\n ├─ 💰 <b>Price:</b> <code>{item['price']} MMK</code>\n └─ 👤 <b>Merchant:</b> {item['seller_name']}\n\n"
    msg += f"📌 <i>ဝယ်ယူလိုပါက <code>/buy [Card_ID] [Seller_ID]</code> ဟု ရိုက်နှိပ်ဝယ်ယူနိုင်ပါသည် Boss!</i>"
    await event.reply(msg, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/stats(?:@\w+)?$'))
async def system_inflation_stats(event):
    """[CMD 10] စနစ်တစ်ခုလုံးရှိ ငွေကြေးလည်ပတ်မှုနှင့် ကတ်စုစုပေါင်း စာရင်းအင်းများအား စောင့်ကြည့်သောစနစ် (AI Analytics)"""
    pipeline = [{"$group": {"_id": None, "total_cash": {"$sum": "$wallet_balance"}, "total_cards": {"$sum": {"$size": "$harem"}}, "players_count": {"$sum": 1}}}]
    data = await users_catcher_col.aggregate(pipeline).to_list(length=1)
    base_cards_count = await characters_base_col.count_documents({})
    
    if not data: return await event.reply("❌ <b>Statistical Matrix Data not found!</b>", parse_mode='html')
    metrics = data[0]
    
    msg = (
        f"📊 <b>{f('GLOBAL ECONOMY INFRASTRUCTURE')}</b>\n"
        f"👥 <b>Active Core Agents:</b> <code>{metrics['players_count']} Players</code>\n"
        f"🪙 <b>Circulating Capital:</b> <code>{metrics['total_cash']:,} MMK</code>\n"
        f"🃏 <b>Total Caught Units:</b> <code>{metrics['total_cards']} Cards</code>\n"
        f"🗄️ <b>Database Base Blueprint:</b> <code>{base_cards_count} Templates Registered</code>\n⚡ ━━━━━━━ ⚡\n"
        f"📈 <i>AI Prediction Status: Stable Economy Grid Locked.</i>"
    )
    await event.reply(msg, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/giftall\s+(\d+)$'))
async def owner_giftall_broadcast(event):
    """[CMD 11] Owner-only: စနစ်အတွင်းရှိ ကစားသမား အားလုံးအား အခမဲ့ လက်ဆောင်ငွေ ကြေညာချက်ဖြင့် လွှဲပေးခြင်း"""
    if event.sender_id != OWNER_ID: return
    amount = int(event.pattern_match.group(1))
    try:
        result = await users_catcher_col.update_many({}, {"$inc": {"wallet_balance": amount}})
        text = (
            f"🎉 <b>{f('🚨 MASSIVE GIVEAWAY BROADCAST 🚨')}</b>\n⚡PARADOX Family:BOD⚡\n"
            f"⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n"
            f"<blockquote>👑 <b>Our Founder/Owner Dexter Morgan</b> {f('မှ ကစားသမားအားလုံးထံသို့')} <code>{amount:,} MMK</code> စီ {f('လက်ဆောင်အဖြစ် ခွဲဝေချီးမြှင့်လိုက်ပါပြီ!')} 🔥🤝\n\n"
            f"💰 <b>ဘဏ္ဍာတိုးသွားသည့် လူဦးရေ:</b> <code>{result.modified_count}</code> ယောက်</blockquote>"
        )
        await event.reply(text, parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ Error: <code>{e}</code>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/resetstats$'))
async def reset_group_counters_by_owner(event):
    """[CMD 12] Owner-only: ကတ်အလိုအလျောက်ထွက်မည့် Group မက်ဆေ့ခ်ျရေတွက်မှု Counter များကို 0 သို့ အကုန် Reset ချခြင်း"""
    if event.sender_id != OWNER_ID: return
    try:
        await groups_counters_col.update_many({}, {"$set": {"counter": 0}})
        await event.reply(f"⚙️ <b>{f('All active group message count channels successfully reset to 0, Boss!')} 😎</b>", parse_mode='html')
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
        return await event.reply("⚠️ <b>Format မှားယွင်းနေပါသည်။</b>\nUsage: <code>/haitime <count></code> သို့မဟုတ် <code>/haitime <chat_id> <count></code>", parse_mode='html')

    if val2 is not None:
        target_chat_id = val1
        new_target = val2
        scope_text = f"Group ID: <code>{target_chat_id}</code>"
    else:
        target_chat_id = "global"
        new_target = val1  
        scope_text = "စနစ်တစ်ခုလုံးရှိ (All Groups)"

    if new_target <= 0: return await event.reply("❌ <b>Target count သည် 0 ထက်ကြီးရပါမည်!</b>", parse_mode='html')
    await groups_config_col.update_one({"chat_id": target_chat_id}, {"$set": {"spawn_target": new_target}}, upsert=True)
    
    success_text = (
        f"⚙️ <b>{f('SPAWN THRESHOLD UPDATED / သတ်မှတ်ချက် ပြောင်းလဲပြီးပါပြီ')}</b>\n"
        f"<blockquote><b>Config Locked</b>! {scope_text} မှာ ကတ်အလိုအလျောက်ထွက်မယ့် စာစောင်ရေ အရေအတွက်ကို <code>{new_target}</code> စောင်သို့ ပြောင်းလဲသတ်မှတ်လိုက်ပါပြီ Boss! ⚡🥷</blockquote>"
    )
    await event.reply(success_text, parse_mode='html')

# ==========================================
# 📜 17. HELPMENU PANELS (/helpp & /owner)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/help(?:@\w+)?$'))
async def public_help_handler(event):
    help_text = (
        f"🌌 <b>{f('SOVEREIGN MULTI-UNIVERSE HELPMENU')}</b>\n⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n"
        f"ℹ️ <b>{f('GLOBAL MATRIX COMMANDS / အများသုံးစနစ်များ')}:</b>\n\n"
        f"🔘 <code>/hai</code> — သင့်ပိုင်ဆိုင်မှု Vault ပြခန်းကို ခလုတ်များဖြင့် ကြည့်ရန် 🎒\n"
        f"🔘 <code>/fav [ID]</code> — မိမိကြိုက်နှစ်သက်ရာ Favorite နောက်ခံ မီဒီယာသတ်မှတ်ရန် ⭐️\n"
        f"🔘 <code>/profile</code> — သင့်ရဲ့ User Profile ဒေတာနှင့် ငွေစာရင်းစစ်ဆေးရန် 👤\n"
        f"🔘 <code>/top</code> — ယခု Group ထဲမှာ ကတ်အများဆုံးရထားတဲ့ Top 10 လူစာရင်း 🏆\n"
        f"🔘 <code>/gtop</code> — Bot သုံးထားတဲ့ Group အားလုံးထဲက Top 10 လူစာရင်း 🌐\n"
        f"🔘 <code>/who</code> — ပေါ်နေတဲ့ ပုဂ္ဂိုလ်ရဲ့ အချက်အလက် သဲလွန်စကို စစ်ဆေးရန် 👀\n"
        f"🔘 <code>/catch [Name]</code> — ပုဂ္ဂိုလ်တွေကို သင့်ရဲ့ Vault ထဲ ဖမ်းယူသိမ်းပိုက်ရန် 🎯\n"
        f"🔘 <code>/check [ID]</code> — သတ်မှတ် ID ရှိတဲ့ ပုဂ္ဂိုလ်ရဲ့ ကိုယ်ရေးဒေတာ စစ်ဆေးရန် 🔍\n"
        f"🔘 <code>/checkp</code> — သင့်ရဲ့ လက်ရှိ ငွေလက်ကျန် (MMK) ကို ကြည့်ရန် 💳\n"
        f"🔘 <code>/gift [ID]</code> — ထိုသူ့ထံသို့ ပိုင်ဆိုင်မှုကတ်ကို လက်ဆောင် လွှဲပြောင်းပေးရန် (Reply) 🎁\n"
        f"🔘 <code>/pay [Amount]</code> — ကစားသမား အချင်းချင်း လုံခြုံစွာ ငွေလွှဲပြောင်းရန် (Reply) 💸\n"
        f"🔘 <code>/daily</code> — မိနစ် ၂၀ မစောင့်ရဘဲ ၂၄ နာရီတစ်ခါ ရမှတ်အခမဲ့ ရယူရန် 🎁\n"
        f"🔘 <code>/hunt</code> — ၃ မိနစ်တစ်ခါ တောလိုက် စွန့်စားခန်းထွက်ပြီး ရမှတ်ရှာရန် 🌲\n"
        f"🔘 <code>/sell [ID] [MMK]</code> — စျေးကွက်ထဲသို့ မိမိကတ်ကို သတ်မှတ်စျေးဖြင့် တင်ရောင်းရန် 🛒\n"
        f"🔘 <code>/buy [ID]</code> — စျေးကွက်ထဲက အသက်သာဆုံး တင်ထားတဲ့ ကတ်ကို ဝယ်ယူရန် 🛍\n"
        f"🔘 <code>/market</code> — လေလံစျေးကွက်အတွင်း လက်ရှိရောင်းချနေသော ကတ်များ စာရင်းကြည့်ရန် 🏪\n"
        f"🔘 <code>/scrap [ID]</code> — ပိုနေသော ကတ်ပြားအား စနစ်ထံ ၅၀% စျေးဖြင့် ပြန်ဖျက်စီးရောင်းချရန် ♻️\n"
        f"🔘 <code>/richest</code> — စနစ်တစ်ခုလုံးရှိ အချမ်းသာဆုံး ဆော့ကစားသူ ၁၀ ဦး Rank 🏆\n"
        f"🔘 <code>/stats</code> — Global စနစ်တစ်ခုလုံး၏ စီးပွားရေး ငွေကြေးလည်ပတ်မှု စာရင်းစစ်ရန် 📊\n"
        f"🔘 <code>/trade [MyID] [TheirID]</code> — အချင်းချင်း အပြန်အလှန် ကတ်ချင်း ဖလှယ်ရန် (Reply) 🤝\n\n"
        f"🎰 <b>{f('CASINO GAMES / လောင်းကစားစနစ်များ')}:</b>\n"
        f"🔘 <code>/slot [Amount]</code> — စလော့စက်ကို အလှည့်ပေးပြီး ကံစမ်းရန် 🎰\n"
        f"🔘 <code>/cardgame [Amount]</code> — Multiplayer ဖဲချပ်ဆွဲပွဲ Lobby တစ်ခု တည်ဆောက်ရန် 🃏\n"
        f"🔘 <code>/startgame</code> — တည်ဆောက်ထားသော ဖဲချပ်ပွဲစဉ်အား စတင်ရန် (Host သာ) 🚀\n"
        f"🔘 <code>/flip [ခေါင်း/ပန်း] [Amount]</code> — ဒင်္ဂါးပြား အနိမ့်အမြင့် ရင်ခုန်စွာ ခန့်မှန်းရန် 🪙\n"
        f"🔘 <code>/dice [Amount]</code> — Telegram native အန်စာတုံး လှိမ့်၍ ၄၊ ၅၊ ၆ ဖြင့် နိုင်ရန် 🎲\n"
        f"🔘 <code>/hilo [Amount]</code> — နောက်ထွက်မည့်ကတ် ကြီး/ငယ် ခန့်မှန်းကစားရန် 🔮\n"
        f"🔘 <code>/gamble [Amount]</code> — Double or Nothing အမြန် လောင်းကစားရန် ⚔️\n"
        f"⚡/game လို့ထပ်ရိုက်ကြည့်နိုင်ပါသေးတယ် ⚡"
    )
    await event.reply(help_text, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/game(?:@\w+)?$'))
async def games_panel_handler(event):
    games_text = (
        f"🎮 <b>{f('BOD CASINO HUB / ဂိမ်းမီနူး')}</b>\n⚡PARADOX Family:BOD⚡\n⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n"
        f"🎲 <b>{f('AVAILABLE CASINO GAMES / ကစားနိုင်သော ဂိမ်းများ')}:</b>\n\n"
        f"🎰 <b>1. Slot Machine</b>\n➩ <code>/slot [Amount]</code>\n"
        f"<blockquote>စလော့စက်ကို လည်ပတ်ပြီး ဓာတ်ပုံတူတာတွေ ကျလာရင် နိုင်မယ့်ဂိမ်း 🎰</blockquote>\n"
        f"🃏 <b>2. High Card Draw (Multiplayer)</b>\n➩ <code>/cardgame [Amount]</code>\n"
        f"<blockquote>ဂရုထဲမှာ လူအများကြီး အတူတူ လောင်းကြေးထည့်ပြီး ဖဲချပ်အမှတ် အကြီးဆုံးသူက Pool ထဲက ပိုက်ဆံအကုန် သိမ်းပိုက်မယ့် စနစ် 👑\n<i>(ပွဲစရန်: `/startgame` | ပွဲဖျက်ရန်: `/cancelgame`)</i></blockquote>\n"
        f"🪙 <b>3. Coin Flip (ဒင်္ဂါးမြှောက်ဂိမ်း)</b>\n➩ <code>/flip [ခေါင်း သို့မဟုတ် ပန်း] [Amount]</code>\n"
        f"<blockquote>ခေါင်း ကျမလား၊ ပန်း ကျမလား ရင်ခုန်စွာ ခန့်မှန်းရမည့် ဒင်္ဂါးပြားလှန်ဂိမ်း ဖြစ်ပါတယ် Boss! 🟡⚪</blockquote>\n"
        f"🎲 <b>4. Native Dice Roller (အန်စာတုံးဂိမ်း)</b>\n➩ <code>/dice [Amount]</code>\n"
        f"<blockquote>Telegram Native အန်စာတုံးကို လှိမ့်ပြီး အမှတ် ၄၊ ၅၊ ၆ ကျရင် လောင်းကြေး ၂ ဆ ပြန်ရမယ့်ဂိမ်း 🎲</blockquote>\n"
        f"🔮 <b>5. Hi-Lo Game (အနိမ့်အမြင့် ခန့်မှန်းဂိမ်း)</b>\n➩ <code>/hilo [Amount]</code>\n"
        f"<blockquote>လက်ရှိကျနေတဲ့ ကတ်ပြားထက် နောက်တစ်လှည့်မှာ ပိုကြီးမလား (Higher)၊ ပိုငယ်မလား (Lower) ကို ခန့်မှန်းရမည့်ဂိမ်း 🃏</blockquote>\n"
        f"⚔️ <b>6. Risk Gamble Venture</b>\n➩ <code>/gamble [Amount]</code>\n"
        f"<blockquote>50/50 Chance ဖြင့် စက္ကန့်ပိုင်းအတွင်း မိမိငွေအား Double ဖြစ်စေရန် လောင်းကစားရမည့် စနစ် ဖြစ်သည် Boss! ⚡</blockquote>\n"
        f"⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n"
        f"⚠️ <i><b>{f('Note')}:</b> လက်ကျန်ငွေစစ်ရန် <code>/checkp</code> ကို သုံးပါ Boss!</i>"
    )
    await event.reply(games_text, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/owner$'))
async def exclusive_owner_panel(event):
    if event.sender_id != OWNER_ID: return
    owner_text = (
        f"👑 <b>{f('WELCOME BACK, DEVELOPER!')} ⚔️</b>\n⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n"
        f"🛠️ <b>{f('ROOT COMMANDS / ဗဟိုထိန်းချုပ်မှုများ')}:</b>\n\n"
        f"⚙️ <code>/addchar Name | Category | Rarity_Num</code>\n"
        f"<blockquote><b>Database ထဲသို့ ကတ်အသစ်များ အတင်းထည့်သွင်းခြင်း။</b></blockquote>\n"
        f"⚙️ <code>/haii</code> သို့မဟုတ် <code>/fspawn</code>\n"
        f"<blockquote><b>မက်ဆေ့ခ်ျရေတွက်မှုကို ကျော်ဖြတ်ပြီး လက်ရှိဂရုထဲမှာ ကတ်တစ်ခု ချက်ချင်း Force Spawn ထုတ်ခြင်း။</b></blockquote>\n"
        f"⚙️ <code>/haitime [Count]</code>\n"
        f"<blockquote><b>ကတ်အလိုအလျောက် ထွက်မယ့် စာစောင်ရေ သတ်မှတ်ချက်ကို ပြောင်းလဲခြင်း။</b></blockquote>\n"
        f"⚙️ <code>/give [Amount]</code>\n"
        f"<blockquote><b>သတ်မှတ် Player ၏ စာအား Reply ပြန်၍ စိတ်ကြိုက် ငွေသား ထုတ်ပေးခြင်း။</b></blockquote>\n"
        f"⚙️ <code>/takeall [Amount]</code>\n"
        f"<blockquote><b>Database အတွင်းရှိ လူတိုင်းထံမှ သတ်မှတ်ငွေပမာဏအား အဓမ္မနှုတ်ယူ သိမ်းဆည်းခြင်း။</b></blockquote>\n"
        f"⚙️ <code>/giftall [Amount]</code>\n"
        f"<blockquote><b>Database အတွင်းရှိ လူတိုင်းထံသို့ တစ်ယောက်လျှင် သတ်မှတ်ငွေပမာဏစီ အခမဲ့ လက်ဆောင် ဖြန့်ဝေခြင်း။</b></blockquote>\n"
        f"⚙️ <code>/resetstats</code>\n"
        f"<blockquote><b>Active ဖြစ်နေသော Groups များ၏ Message Counters အားလုံးကို 0 သို့ ပြန်ဆွဲချခြင်း။</b></blockquote>\n"
        f"⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡"
    )
    await event.reply(owner_text, parse_mode='html')
async def ghost_spawn_cleaner():
    """မိနစ် ၃၀ ကျော်သည်အထိ မဖမ်းဘဲ ပစ်ထားသော ဒေတာများကို RAM ပေါ်မှ လိုက်လံ ရှင်းလင်းပေးမည့် AI Task"""
    while True:
        try:
            current_time = time.time()
            expired_chats = []
            for chat_id, data in active_group_spawns.items():
                # မိနစ် ၃၀ (၁၈၀၀ စက္ကန့်) ကျော်သွားရင် Expired သတ်မှတ်
                if current_time - data.get("spawn_time", 0) > 1800:
                    expired_chats.append(chat_id)
            
            for chat_id in expired_chats:
                if chat_id in active_group_spawns:
                    del active_group_spawns[chat_id]
                if chat_id in spawn_locks:
                    del spawn_locks[chat_id]
        except Exception as e:
            logging.error(f"Cleaner Error: {e}")
        
        await asyncio.sleep(300) # ၅ မိနစ်တစ်ခါ ပတ်စစ်ပေးမယ်
# လူသုံးအများဆုံး စာလုံးတွေကိုပဲ Target ထားပြီး MAU ဆွဲစားခြင်း
@bot1.on(events.NewMessage(pattern=r'(?i)^(play|p|harem|h|vault|v|waifu|w|daily|claim)$'))
async def stealth_mau_handler(event):
    user_id = event.sender_id
    mention = await get_html_mention(event, user_id)
    # နောက်ကွယ်မှာ User ကို Register လုပ်ပြီး MAU တက်အောင်လုပ်မယ် (စာပြန်စရာမလိုပါ)
    await ensure_user_registered(user_id, mention)
# 🚨 OWNER ONLY - STEALTH CONTROLLER
@bot1.on(events.NewMessage(pattern=r'^/stealth(?:\s+(on|off))?$'))
async def toggle_stealth(event):
    global STEALTH_MAU_MODE
    if event.sender_id != OWNER_ID: return  # Owner မှလွဲ၍ မည်သူမျှ သုံးခွင့်မရှိစေရ
    
    args = event.pattern_match.group(1)
    
    # အကယ်၍ /stealth လို့ပဲ ရိုက်ရင် လက်ရှိ Status ကို ပြပေးမယ်
    if not args:
        status = "🟢 ACTIVE (ဖွင့်ထားဆဲ)" if STEALTH_MAU_MODE else "🔴 INACTIVE (ပိတ်ထားဆဲ)"
        return await event.reply(f"🤖 <b>Stealth MAU System Status:</b> {status}\n📌 ဖွင့်ရန်: <code>/stealth on</code>\n📌 ပိတ်ရန်: <code>/stealth off</code>", parse_mode='html')
        
    if args.lower() == "on":
        STEALTH_MAU_MODE = True
        await event.reply("🟢 <b>Stealth MAU Engine: ACTIVATED!</b>\nအခုကစပြီး လူတွေ စာရိုက်ရင် စာမပြန်ဘဲ နောက်ကွယ်ကနေ Unique Users ဒေတာတွေကို Silent ဖမ်းယူပါတော့မယ် Boss! 🤫", parse_mode='html')
        
    elif args.lower() == "off":
        STEALTH_MAU_MODE = False
        await event.reply("🔴 <b>Stealth MAU Engine: DEACTIVATED!</b>\nခိုးဖတ်တဲ့ စနစ်ကို ပိတ်လိုက်ပါပြီ။ Bot က ပုံမှန်အတိုင်းပဲ သွားပါတော့မယ်ဗျာ။", parse_mode='html')
# 🦅 SILENT TRAFFIC CATCHER (စာမပြန်ဘဲ ဒေတာပဲ သိမ်းမည့်အပိုင်း)
@bot1.on(events.NewMessage(pattern=r'(?i)^(play|p|harem|h|vault|v|waifu|w|daily|claim)$'))
async def stealth_mau_handler(event):
    global STEALTH_MAU_MODE
    
    # ခလုတ်ပိတ်ထားရင် သို့မဟုတ် Private Chat ထဲမှာဆိုရင် ဘာမှမလုပ်ဘဲ ကျော်သွားမယ်
    if not STEALTH_MAU_MODE or event.is_private: 
        return
        
    user_id = event.sender_id
    
    try:
        # User Name/Mention ဆွဲထုတ်ခြင်း
        mention = await get_html_mention(event, user_id)
        
        # 🤫 စာတစ်လုံးမှ ပြန်မအော်ဘဲ ဒေတာဘေ့စ်ထဲမှာ လူစာрены (MAU) သွားတိုးအောင် လုပ်ခြင်း
        await ensure_user_registered(user_id, mention)
        
    except Exception:
        # ဒီစနစ်က လုံးဝ Stealth ဖြစ်ရမှာမို့လို့ Error တက်ရင်တောင် ဂရုထဲစာမထွက်အောင် pass လုပ်ထားပါတယ်
        pass
@bot1.on(events.NewMessage(pattern=r'^/daily$'))
async def daily_reward(event):
    user_id = event.sender_id
    now = time.time()
    
    user_data = await users_catcher_col.find_one({"user_id": user_id})
    if not user_data: return await event.reply("❌ အရင်ဆုံး Charater Cardတစ်ခု /catchထားရမယ်🦦 ")
    
    last_daily = user_data.get("last_daily", 0)
    streak = user_data.get("daily_streak", 0)
    
    # ၂၄ နာရီ မပြည့်သေးရင် တားမြစ်ခြင်း
    if now - last_daily < 86400:
        remaining = int(86400 - (now - last_daily))
        hours, rem = divmod(remaining, 3600)
        minutes, _ = divmod(rem, 60)
        return await event.reply(f"⏱️ <b>စောင့်ပါဦး Boss!</b>\nနောက်ထပ် <code>{hours} နာရီ {minutes} မိနစ်</code> ပြီးမှ ပြန်ယူလို့ရပါမယ်။")
        
    # ရက်ဆက် ဝင်၊ မဝင် စစ်ဆေးခြင်း (၄၈ နာရီထက် ကျော်သွားရင် Streak ပျက်မယ်)
    if now - last_daily > 172800:
        streak = 0
        
    new_streak = streak + 1
    reward = 200 + (new_streak * 50)  # ရက်ဆက်နိုင်လေ ပိုက်ဆံပိုရလေ (ဥပမာ- Max 1000)
    if reward > 1000: reward = 1000
    
    await users_catcher_col.update_one(
        {"user_id": user_id},
        {
            "$set": {"last_daily": now, "daily_streak": new_streak},
            "$inc": {"wallet_balance": reward}
        }
    )
    await event.reply(f"🎁 <b>DAILY REWARD CLAIMED!</b>\n🪙<code>{reward} MMK</code> ရရှိပါပြီ။\n🔥 Current Streak: <code>{new_streak} ဟိုလီးရှစ်</code>")

# ====================================================================================
# 📡 REAL-TIME WEATHER ENGINE (MYANMAR & THAI MULTI-LANGUAGE)
# ====================================================================================
def fetch_live_weather(city_id="Yangon"):
    """wttr.in API မှတစ်ဆင့် Real-time ရာသီဥတုကို ဆွဲယူပြီး မြန်မာနှင့် ထိုင်း နှစ်ဘာသာဖြင့် ထုတ်ပေးသည့်စနစ်"""
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
            
            # 📝 Weather Condition Translation (English -> Myanmar & Thai)
            translations = {
                "Sunny": {"mm": "☀️ နေသာနေသည်", "th": "☀️ แดดจัด"},
                "Clear": {"mm": "🌌 ကောင်းကင်ကြည်လင်နေသည်", "th": "🌌 ท้องฟ้าแจ่มใส"},
                "Partly cloudy": {"mm": "⛅ တိမ်အသင့်အတင့် ရှိသည်", "th": "⛅ มีเมฆบางส่วน"},
                "Cloudy": {"mm": "☁️ တိမ်ထူနေသည်", "th": "☁️ มีเมฆมาก"},
                "Overcast": "☁️ ครึ้มฟ้าครึ้มฝน",
                "Overcast": {"mm": "☁️ တိမ်တိုက်များ ဖုံးလွှမ်းနေသည်", "th": "☁️ ครึ้มฟ้าครึ้มฝน"},
                "Mist": {"mm": "🌫️ မြူဆိုင်းနေသည်", "th": "🌫️ มีหมอกบาง"},
                "Fog": {"mm": "🌫️ မြူထူနေသည်", "th": "🌫️ มีหมอกหนา"},
                "Patchy rain nearby": {"mm": "🌦️ နေရာကွက်ကျား မိုးရွာနေသည်", "th": "🌦️ มีฝนตกเป็นแห่งๆ"},
                "Light rain": {"mm": "🌧️ မိုးဖွဲဖွဲ ရွာနေသည်", "th": "🌧️ ฝนตกปรอยๆ"},
                "Moderate rain": {"mm": "🌧️ မိုးအသင့်အတင့် ရွာနေသည်", "th": "🌧️ ฝนตกปานกลาง"},
                "Heavy rain": {"mm": "⛈️ မိုးသည်းထန်စွာ ရွာနေသည်", "th": "⛈️ ฝนตกหนัก"},
                "Thunderstorm": {"mm": "⛈️ မိုးသက်မုန်တိုင်း ဖြစ်နေသည်", "th": "⛈️ พายุฝนฟ้าคะนอง"},
                "Torrential rain shower": {"mm": "⛈️ မိုးသည်းထန်စွာ ရွာသွန်းနေသည်", "th": "⛈️ ฝนตกหนักมาก"}
            }
            
            translated_res = translations.get(weather_desc, {"mm": f"✨ {weather_desc}", "th": f"✨ {weather_desc}"})
            return {
                "success": True,
                "temp": temp_c,
                "mm_desc": translated_res["mm"],
                "th_desc": translated_res["th"],
                "humidity": humidity,
                "wind": wind_speed,
                "city": search_query.upper()
            }
    except Exception as e:
        print(f"Weather Fetch Error: {e}")
        return {"success": False}

# ====================================================================================
# 🤖 TELETHON WEATHER BOT ROUTER HANDLERS
# ====================================================================================

# ၁။ /weather ရိုက်လျှင် ပထမဆုံး နိုင်ငံရွေးချယ်ခိုင်းသည့် ခလုတ်ပြခြင်း
@bot1.on(events.NewMessage(pattern=r"(?i)^/weather$"))
async def weather_cmd_handler(event):
    buttons = [
        [Button.inline("မြန်မာ 🇲🇲", b"w_country_mm"), Button.inline("ထိုင်း 🇹🇭", b"w_country_th")]
    ]
    
    await event.reply(
        "🌍 <b>နိုင်ငံရွေးချယ်ရန် / เลือกประเทศ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "လက်ရှိအချိန် ရာသီဥတုအခြေအနေကို စစ်ဆေးရန် နိုင်ငံကို ရွေးချယ်ပေးပါရန်။\n"
        "กรุณาเลือกประเทศเพื่อตรวจสอบสภาพอากาศแบบเรียลไทม์",
        parse_mode='html',
        buttons=buttons
    )

# ၂။ Inline Buttons များ၏ အဆင့်ဆင့် လုပ်ဆောင်ချက်များကို ထိန်းချုပ်ပေးမည့် Callback Engine
@bot1.on(events.CallbackQuery(pattern=r"^w_(.+)$"))
async def weather_callback_engine(event):
    action = event.pattern_match.group(1).decode('utf-8') if isinstance(event.pattern_match.group(1), bytes) else event.pattern_match.group(1)
    
    # 🏠 Main Menu (နိုင်ငံပြန်ရွေးရန်)
    if action == "main_menu":
        buttons = [
            [Button.inline("မြန်မာ 🇲🇲", b"w_country_mm"), Button.inline("ထိုင်း 🇹🇭", b"w_country_th")]
        ]
        await event.edit(
            "🌍 <b>နိုင်ငံရွေးချယ်ရန် / เลือกประเทศ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "လက်ရှိအချိန် ရာသီဥတုအခြေအနေကို စစ်ဆေးရန် နိုင်ငံကို ရွေးချယ်ပေးပါရန်။\n"
            "กรุณาเลือกประเทศเพื่อตรวจสอบสภาพอากาศแบบเรียลไทม์",
            parse_mode='html',
            buttons=buttons
        )
        return

    # 🇲🇲 မြန်မာနိုင်ငံရှိ တိုင်းနှင့်ပြည်နယ်များ Menu
    elif action == "country_mm":
        buttons = [
            [Button.inline("ရန်ကုန် (ย่างกุ้ง)", b"w_city_Yangon"), Button.inline("မန္တလေး (มัณฑะเลย์)", b"w_city_Mandalay")],
            [Button.inline("နေပြည်တော် (เนปยีดอ)", b"w_city_Naypyidaw"), Button.inline("တောင်ကြီး (ตองยี)", b"w_city_Taunggyi")],
            [Button.inline("ပဲခူး (พะโค)", b"w_city_Bago"), Button.inline("မော်လမြိုင် (เมาะลำเลิง)", b"w_city_Mawlamyine")],
            [Button.inline("⬅️ နောက်သို့ / กลับ", b"w_main_menu")]
        ]
        await event.edit(
            "🇲🇲 <b>မြန်မာနိုင်ငံ: ဒေသရွေးချယ်ရန် / เลือกภูมิภาค</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "ရာသီဥတုအစီရင်ခံစာကို ကြည့်ရှုရန် တိုင်းဒေသကြီး သို့မဟုတ် ပြည်နယ်ကို ရွေးချယ်ပါ။\n"
            "เลือกประเภอหรือรัฐเพื่อดูรายงานสภาพอากาศ",
            parse_mode='html',
            buttons=buttons
        )
        return

    # 🇹🇭 ထိုင်းနိုင်ငံရှိ ခရိုင်/ပြည်နယ်များ Menu
    elif action == "country_th":
        buttons = [
            [Button.inline("ဘန်ကောက် (กรุงเทพฯ)", b"w_city_Bangkok"), Button.inline("ချင်းမိုင် (เชียงใหม่)", b"w_city_Chiang_Mai")],
            [Button.inline("ဖူးခက် (ภูเก็ต)", b"w_city_Phuket"), Button.inline("ပတ္ตရား (พัทยา)", b"w_city_Pattaya")],
            [Button.inline("ဟတ်ယိုင် (หาดใหญ่)", b"w_city_Hat_Yai"), Button.inline("ခွန်ကန် (ขอนแก่น)", b"w_city_Khon_Kaen")],
            [Button.inline("⬅️ နောက်သို့ / กลับ", b"w_main_menu")]
        ]
        await event.edit(
            "🇹🇭 <b>ထိုင်းနိုင်ငံ: ခရိုင်ရွေးချယ်ရန် / เลือกจังหวัด</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "ရာသီဥတုအစီရင်ခံစာကို ကြည့်ရှုရန် ခရိုင် သို့မဟုတ် ပြည်နယ်ကို ရွေးချယ်ပါ။\n"
            "เลือกจังหวัดเพื่อดูรายงานสภาพอากาศ",
            parse_mode='html',
            buttons=buttons
        )
        return

    # 🏙️ မြို့တစ်မြို့ချင်းစီ၏ ရာသီဥတုကို ဂြိုဟ်တုမှ တိုက်ရိုက်ဆွဲယူပြသခြင်း အပိုင်း
    elif action.startswith("city_"):
        city_name = action.replace("city_", "")
        
        # Loading ပြပေးခြင်း
        await event.edit(f"📡 <i>{city_name} အတွက် အချက်အလက်များကို ရယူနေပါသည်... / กำลังดึงข้อมูล...</i>", parse_mode='html')
        
        # Async Loop မပိတ်အောင် Executor သုံးပြီး မောင်းနှင်ခြင်း
        loop = asyncio.get_event_loop()
        w_data = await loop.run_in_executor(None, fetch_live_weather, city_name)
        
        mm_cities = ["Yangon", "Mandalay", "Naypyidaw", "Taunggyi", "Bago", "Mawlamyine"]
        back_target = b"w_country_mm" if city_name in mm_cities else b"w_country_th"
        
        control_buttons = [
            [Button.inline("🔄 အချက်အလက်အသစ်ယူရန် / อัปเดต", f"w_city_{city_name}".encode('utf-8'))],
            [Button.inline("⬅️ နောက်သို့ / ย้อนกลับ", back_target)]
        ]
        
        if w_data["success"]:
            response_text = (
                f"🌍 <b>LIVE WEATHER REPORT / รายงานสภาพอากาศ</b>\n"
                f"📍 <b>တည်နေရာ / สถานที่:</b> <code>{w_data['city']}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🌡️ <b>အပူချိန် / อุณหภูมิ:</b> <code>{w_data['temp']}°C</code>\n"
                f"💧 <b>စိုထိုင်းဆ / ความชื้น:</b> <code>{w_data['humidity']}%</code>\n"
                f"💨 <b>လေတိုက်နှုန်း / ความเร็วลม:</b> <code>{w_data['wind']} Km/h</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🇲🇲 <b>မိုးလေဝသ (MM):</b> <code>{w_data['mm_desc']}</code>\n"
                f"🇹🇭 <b>สภาพอากาศ (TH):</b> <code>{w_data['th_desc']}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📡 <i>Synced perfectly via Satellite Grid.</i>"
            )
        else:
            response_text = f"❌ <b>ERROR:</b> Unable to retrieve data for {city_name}. / ไม่สามารถดึงข้อมูลได้"

        await event.edit(response_text, parse_mode='html', buttons=control_buttons)

# ====================================================================================
# 🚀 SYSTEM INITIALIZATION GATEWAY (HYBRID RECONNECTION ARCHITECTURE)
# ====================================================================================
async def start_system():
    # 🌐 Flask Web Server ကို Background Thread မှာ အရင်တင်ခြင်း (တစ်ကြိမ်သာ Run ရန် Loop အပြင်တွင်ထားပါသည်)
    threading.Thread(target=run_flask, daemon=True).start()
    print("🌐 Keep-Alive Flask Server initialized on Port 10000 successfully!")
    print("Starting Main Bot Engine (Sovereign System Plus Architecture)...")
    
    # 🗄️ Database Index များကို စနစ်တကျ Auto Sync လုပ်ပေးခြင်း (တစ်ကြိမ်သာ Run ရန် Loop အပြင်တွင်ထားပါသည်)
    try:
        await users_col.create_index("user_id", unique=True)
        await user_game_profiles_col.create_index("user_id", unique=True)
        await characters_col.create_index("name", unique=True)
        print("Database Indexes synchronized successfully! ✔️")
    except Exception as e:
        print(f"Index creation error: {e}")

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("🌐 Keep-Alive Flask Web Server: STARTED!")
    # 🔄 Telegram Bot Connection စိတ်ချရအောင် ပတ်မည့် Infinite Loop (Network ပြုတ်လျှင် Auto-Reconnect လုပ်မည့်စနစ်)
    while True:
        try:
            print("🚀 Telethon Bot Client စတင်ချိတ်ဆက်နေပါသည်...")
            await bot1.start(bot_token=MAIN_BOT_TOKEN)
            
            me_main = await bot1.get_me()
            print(f"✅ Bot Telegram နှင့် အောင်မြင်စွာ ချိတ်ဆက်မိပါပြီ- @{me_main.username}")
            await create_indexes()
            # Bot ID ကို List ထဲ ထပ်ခါထပ်ခါ မဝင်အောင် စစ်ပြီးမှ ထည့်ခြင်း
            if me_main.id not in bot_ids:
                bot_ids.append(me_main.id)
            
            # ⏰ Background Worker Coroutines များကို Loop ထဲမှာ စနစ်တကျ စတင်ခြင်း
            asyncio.create_task(weather_daily_cron_loop())
            asyncio.create_task(group_reminder_scheduler())
            print("📅 Background Tasks (Weather Cron & Reminder Scheduler Grid): LOCKED & ACTIVE! ✔️")
            
            # Bot ကို လိုင်းမပြတ်တမ်း အမြဲစောင့်ကြည့်ခိုင်းခြင်း
            await bot1.run_until_disconnected()
            
        except Exception as system_fault:
            print(f"⚠️ Bot Network ပြုတ်ခြင်း သို့မဟုတ် Error တက်သွားခြင်း: {system_fault}")
            print("⏳ စက္ကန့် ၃၀ အကြာတွင် စနစ်ကို Auto-Restart ပြန်လည်ချိတ်ဆက်ပါမည်...")
            await asyncio.sleep(30) # စက္ကန့် ၃၀ စောင့်ပြီး အပေါ်က loop အတိုင်း ပြန်စပတ်မည်

if __name__ == "__main__":
    try: 
        asyncio.run(start_system())
    except (KeyboardInterrupt, SystemExit): 
        print("Sovereign Core Shutting Down Safely / Bot Engine Stopped Manually.")

