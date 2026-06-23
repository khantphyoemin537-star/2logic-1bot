import io
import asyncio
import logging
import random
import os
import threading
import re
import time
from datetime import datetime, timedelta
import pytz
from flask import Flask
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne, ReturnDocument
from html import escape as escape_html
from telethon import TelegramClient, events, types, Button, errors

# --- Telethon Spec Imports ---
from telethon.tl.functions.channels import GetAdminLogRequest, GetParticipantsRequest
from telethon.tl.types import (
    ChannelParticipantAdmin, 
    ChannelParticipantCreator, 
    ChannelParticipantsAdmins,
    ChatAdminRights,
    ChannelAdminLogEventsFilter
)

# ==========================================
# 🌐 DNS FIX FOR MONGODB
# ==========================================
try:
    import dns.resolver
    dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
    dns.resolver.default_resolver.nameservers = ['8.8.8.8', '1.1.1.1']
except: pass

# ==========================================
# 🌐 FLASK KEEP-ALIVE
# ==========================================
app = Flask('')
@app.route('/')
def home(): return "BoD Sovereign System Plus Engine is Active!"

def run_flask(): 
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.ERROR)

# ==========================================
# ⚙️ CONFIGURATION & TOKENS
# ==========================================
OWNER_ID = 6015356597
OWNER_USERNAME = "Hello_Im_DexterMorgan"
MONGO_URI = "mongodb+srv://khantphyoemin537_db_user:9VRKiaeZkz7rJdpz@cluster0.w6tgi8j.mongodb.net/?appName=Cluster0&tlsAllowInvalidCertificates=true"
APP_ID = 35766004
APP_HASH = 'd15b4226b81724722279bae6af69e22d'
MAIN_BOT_TOKEN = "8575371720:AAEWWV42CGrwooM_joiJXdo2iEw2_7atyXU"

SPECIFIC_GROUP = -1003580630981 
SPECIFIC_CONTROL_GROUP = -1003580630981
TZ = pytz.timezone('Asia/Yangon')

# ==========================================
# 🗄️ DATABASE SETUP (MOTOR ASYNC)
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

# ==========================================
# 🤖 CLIENT SETUP (SINGLE BOT)
# ==========================================
bot1 = TelegramClient('bot_main_session', APP_ID, APP_HASH)
bot_ids = []
bot_names = []

# ==========================================
# 📊 STATE VARIABLES & SPEED CACHE
# ==========================================
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
# 💎 PREMIUM MATHEMATICAL BOLD SERIF FONT CONVERTER
def f(text):
    normal = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    bold = "𝙰𝙱𝙲𝙳𝙴𝙵𝙶𝙷𝙸𝙹𝙺𝙻𝙼𝙽𝙾𝙿𝚀𝚁𝚂𝚃𝚄𝚅𝚆𝚇𝚈𝚉𝚊𝚋𝚌𝚍𝚎𝚏𝚐𝚑𝚒𝚓𝚔𝚕𝚖𝚗𝚘𝚙𝚚𝚛𝚜𝚝𝚞𝚟𝚠𝚡𝚢𝚣0123456789"
    trans = str.maketrans(normal, bold)
    return str(text).translate(trans)

# ==========================================
# 🛠️ ADVANCED PRODUCTION LOGICS & BUFFERS
# ==========================================
USER_METRICS_BUFFER = []
BUFFER_LOCK = asyncio.Lock()
user_cooldowns = {}

def bq(text): return f"<blockquote><b>{text}</b></blockquote>"

def owner_tag():
    return f"<a href='https://t.me/{OWNER_USERNAME}'>@{OWNER_USERNAME}</a>"
    
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
# 🌤️ MYANMAR WEATHER DASHBOARD DATA ENGINE
# ==========================================
WEATHER_REGIONS_DATA = {
    "mdy": {"name": "မန္တလေး တိုင်းဒေသကြီး", "temp": "၃၈°C", "condition": "☀️ နေသာပြီး အလွန်ပူပြင်းသည်", "humidity": "၄2%", "wind": "၁၂ km/h"},
    "ygn": {"name": "ရန်ကုန် တိုင်းဒေသကြီး", "temp": "၃၂°C", "condition": "🌧️ နေရာကွက်ကျား မိုးရွာနိုင်သည်", "humidity": "၇၈%", "wind": "၁၅ km/h"},
    "npw": {"name": "နေပြည်တော် ကောင်စီနယ်မြေ", "temp": "၃၆°C", "condition": "⛅ တိမ်အသင့်အတင့် ဖြစ်ထွန်းသည်", "humidity": "၅၀%", "wind": "၁၀ km/h"},
    "sgn": {"name": "စစ်ကိုင်း တိုင်းဒေသကြီး", "temp": "၃၇°C", "condition": "☀️ နေသာပြီး ရာသီဥတု ပူပြင်းသည်", "humidity": "၄၅%", "wind": "၉ km/h"},
    "bgo": {"name": "ပဲခူး တိုင်းဒေသကြီး", "temp": "၃၃°C", "condition": "⛅ တိမ်အနည်းငယ် ဖြစ်ထွန်းသည်", "humidity": "၆၅%", "wind": "၁၁ km/h"},
    "mgy": {"name": "မကွေး တိုင်းဒေသကြီး", "temp": "၃၉°C", "condition": "☀️ နေသာပြီး အပူရှိန် အလွန်ပြင်းထန်သည်", "humidity": "၃၈%", "wind": "၁၄ km/h"},
    "ayd": {"name": "ဧရာဝတီ တိုင်းဒေသကြီး", "temp": "၃၁°C", "condition": "🌧️ မိုးသက်လေပြင်း ကျရောက်နိုင်သည်", "humidity": "၈၂%", "wind": "၁၈ km/h"},
    "tny": {"name": "တနင်္သာရီ တိုင်းဒေသကြီး", "temp": "၃၀°C", "condition": "🌧️ မိုးအသင့်အတင့် ရွာသွန်းနိုင်သည်", "humidity": "၈၅%", "wind": "၂၀ km/h"},
    "kcn": {"name": "ကချင် ပြည်နယ်", "temp": "၂၆°C", "condition": "🌫️ မြူဆိုင်းပြီး ရာသီဥတု အေးမြသည်", "humidity": "၇၀%", "wind": "၆ km/h"},
    "kyh": {"name": "ကယား ပြည်နယ်", "temp": "၂၉°C", "condition": "⛅ တိမ်အသင့်အတင့် ဖြစ်ထွန်းသည်", "humidity": "၆၀%", "wind": "၈ km/h"},
    "kyn": {"name": "ကရင် ပြည်နယ်", "temp": "၃၁°C", "condition": "🌧️ နေရာကွက်ကျား မိုးထူထပ်နိုင်သည်", "humidity": "၇၅%", "wind": "၁၃ km/h"},
    "chn": {"name": "ချင်း ပြည်နယ်", "temp": "၂၂°C", "condition": "❄️ ရာသီဥတု အလွန်အေးမြပြီး တိမ်ထူထပ်သည်", "humidity": "၅၅%", "wind": "၇ km/h"},
    "mon": {"name": "မွန် ပြည်နယ်", "temp": "၃၁°C", "condition": "🌧️ မိုးရွာသွန်းရန် ရာခိုင်နှုန်း များသည်", "humidity": "၈0%", "wind": "၁၆ km/h"},
    "shn": {"name": "ရှမ်း ပြည်နယ်", "temp": "၂၅°C", "condition": "🌫️ တောင်ပေါ်ဒေသများ မြူပိတ်ပြီး အေးမြသည်", "humidity": "၆၂%", "wind": "၈ km/h"}
}

def generate_weather_text(region_id):
    data = WEATHER_REGIONS_DATA.get(region_id)
    if not data: return "အချက်အလက် မရှိပါ။"
    return (
        f"🌤️ <b><u>မိုးလေဝသနှင့် ရာသီဥတု</u></b>\n"
        f"📍 <b>တိုင်းဒေသကြီး/ပြည်နယ်:</b> <code>{data['name']}</code>\n"
        f"🌡️ <b>လက်ရှိအပူချိန်:</b> <code>{data['temp']}</code>\n"
        f"☁️ <b>မိုးလေဝသ အခြေအနေ:</b> <code>{data['condition']}</code>\n"
        f"💧 <b>လေထုစိုထိုင်းဆ:</b> <code>{data['humidity']}</code>\n"
        f"💨 <b>လေတိုက်နှုန်း:</b> <code>{data['wind']}</code>\n"
        f"⏰ <b>နောက်ဆုံး Update:</b> <code>ယနေ့ ည ၀၈:၀၀ နာရီ</code>"
    )

async def weather_daily_cron_loop():
    while True:
        try:
            now = datetime.now(TZ)
            if now.hour == 20 and now.minute == 0:
                summary_text = (
                    f"☀️ <b>မြန်မာနိုင်ငံလုံးဆိုင်ရာ နေ့စဉ် မိုးလေဝသသတင်းစလွှာ</b>\n"
                    f"📅 <b>နေ့စွဲ:</b> <code>{now.strftime('%Y-%m-%d')}</code>\n\n"
                    f"🇲🇲 မိမိတို့ တိုင်းဒေသကြီးနှင့် ပြည်နယ်အလိုက် အသေးစိတ် ရာသီဥတုကို သိရှိရန် <code>/weather</code> ဟု ရိုက်နှိပ်၍ Dashboard တွင် စစ်ဆေးနိုင်ပါသည် Chief။"
                )
                active_groups = await groups_col.find().to_list(length=None)
                for g in active_groups:
                    try: 
                        await bot1.send_message(g["chat_id"], bq(summary_text), parse_mode='html')
                        await asyncio.sleep(0.2)
                    except Exception: pass
                
                tracked_users = await users_col.find().to_list(length=None)
                for u in tracked_users:
                    try:
                        await bot1.send_message(u["user_id"], bq(summary_text), parse_mode='html')
                        await asyncio.sleep(0.2)
                    except Exception: pass
                    
                await asyncio.sleep(60) 
        except Exception as e:
            await report_system_error("Weather Cron Broadcaster Loop", e)
        await asyncio.sleep(30)

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
# 🧠 SCHEMA-AGNOSTIC SMART REPLY SEARCH ENGINE
# ==========================================
async def fetch_optimized_smart_reply(user_text):
    reply_text = None
    user_text_clean = user_text.strip().lower()

    match_pipeline = [
        {"$match": {
            "$or": [
                {"$and": [{"$ifNull": ["$trigger", False]}, {"$gte": [{"$strLenCP": "$trigger"}, 2]}, {"$gte": [{"$indexOfCP": [user_text_clean, {"$toLower": "$trigger"}]}, 0]}]},
                {"$and": [{"$ifNull": ["$key", False]}, {"$gte": [{"$strLenCP": "$key"}, 2]}, {"$gte": [{"$indexOfCP": [user_text_clean, {"$toLower": "$key"}]}, 0]}]},
                {"$and": [{"$ifNull": ["$keyword", False]}, {"$gte": [{"$strLenCP": "$keyword"}, 2]}, {"$gte": [{"$indexOfCP": [user_text_clean, {"$toLower": "$keyword"}]}, 0]}]}
            ]
        }},
        {"$addFields": {
            "weight": {
                "$max": [
                    {"$cond": [{"$and": [{"$ifNull": ["$trigger", False]}, {"$gte": [{"$indexOfCP": [user_text_clean, {"$toLower": "$trigger"}]}, 0]}]}, {"$strLenCP": "$trigger"}, 0]},
                    {"$cond": [{"$and": [{"$ifNull": ["$key", False]}, {"$gte": [{"$indexOfCP": [user_text_clean, {"$toLower": "$key"}]}, 0]}]}, {"$strLenCP": "$key"}, 0]},
                    {"$cond": [{"$and": [{"$ifNull": ["$keyword", False]}, {"$gte": [{"$indexOfCP": [user_text_clean, {"$toLower": "$keyword"}]}, 0]}]}, {"$strLenCP": "$keyword"}, 0]}
                ]
            }
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
                chosen = None
                if doc.get("responses") and isinstance(doc["responses"], list): chosen = random.choice(doc["responses"])
                elif doc.get("values") and isinstance(doc["values"], list): chosen = random.choice(doc["values"])
                elif doc.get("response"): chosen = doc["response"]
                elif doc.get("value"): chosen = doc["value"]
                elif doc.get("text"): chosen = doc["text"]

                if chosen and not ('@' in str(chosen) or 't.me/' in str(chosen)): valid_choices.append(str(chosen))
            
            if valid_choices: reply_text = random.choice(valid_choices)
    except Exception as e:
        print(f"Smart Reply DB Error: {e}")

    if not reply_text:
        try:
            fallback_cursor = talk_col.aggregate([{"$sample": {"size": 1}}])
            fallback_docs = await fallback_cursor.to_list(length=1)
            if fallback_docs and fallback_docs[0].get("text"):
                chosen_fb = fallback_docs[0]["text"]
                if not ('@' in chosen_fb or 't.me/' in chosen_fb): reply_text = chosen_fb
        except Exception: pass

    return reply_text

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
    
    # 🌦️ WEATHER SELECTION SYSTEM INTERCEPTOR
    if data.startswith("w_"):
        region_code = data.split("_")[1]
        weather_report_txt = generate_weather_text(region_code)
        back_btn = [[Button.inline("🔙 Back to Weather Dashboard", data="nav_weather_dash")]]
        return await event.edit(bq(weather_report_txt), parse_mode='html', buttons=back_btn)
        
    if data == "nav_weather_dash":
        weather_msg = "🇲🇲 <b>မြန်မာနိုင်ငံ တိုင်းဒေသကြီးများနှင့် ပြည်နယ်များ၏ ရာသီဥတုကို စစ်ဆေးရန် ခလုတ်များကို နှိပ်ပါ-</b>"
        buttons = [
            [Button.inline("မန္တလေး", data="w_mdy"), Button.inline("ရန်ကုန်", data="w_ygn")],
            [Button.inline("နေပြည်တော်", data="w_npw"), Button.inline("စစ်ကိုင်း", data="w_sgn")],
            [Button.inline("ပဲခူး", data="w_bgo"), Button.inline("မကွေး", data="w_mgy")],
            [Button.inline("ဧရာဝတီ", data="w_ayd"), Button.inline("တနင်္သာရီ", data="w_tny")],
            [Button.inline("ကချင်", data="w_kcn"), Button.inline("ကယား", data="w_kyh")],
            [Button.inline("ကရင်", data="w_kyn"), Button.inline("ချင်း", data="w_chn")],
            [Button.inline("မွန်", data="w_mon"), Button.inline("ရှမ်း", data="w_shn")],
            [Button.inline("🔙 Return to Main", data="nav_back_home")]
        ]
        return await event.edit(bq(weather_msg), parse_mode='html', buttons=buttons)

    # UI INLINE INTERACTION MANAGEMENT
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

# COMMANDS INTERCEPTORS
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
# 🌤️ ADVANCED SYSTEM LOGIC 6: INTERACTIVE WEATHER DASHBOARD & COMMAND
# ====================================================================================
@bot1.on(events.NewMessage(pattern=r'^/weather$'))
async def weather_dashboard_command(event):
    is_cd, rem = await is_on_cooldown(event.sender_id, "weather", 3)
    if is_cd: return await event.reply(bq(f"⚠️ Flood Protection: {rem} စက္ကန့် စောင့်ဆိုင်းပေးပါ။"), parse_mode='html')
    
    weather_msg = "🇲🇲 <b>မြန်မာနိုင်ငံ တိုင်းဒေသကြီးများနှင့် ပြည်နယ်များ၏ ရာသီဥတုကို စစ်ဆေးရန် ခလုတ်များကို နှိပ်ပါ Chief-</b>"
    buttons = [
        [Button.inline("မန္တလေး", data="w_mdy"), Button.inline("ရန်ကုန်", data="w_ygn")],
        [Button.inline("နေပြည်တော်", data="w_npw"), Button.inline("စစ်ကိုင်း", data="w_sgn")],
        [Button.inline("ပဲခူး", data="w_bgo"), Button.inline("မကွေး", data="w_mgy")],
        [Button.inline("ဧရာဝတီ", data="w_ayd"), Button.inline("တနင်္သာရီ", data="w_tny")],
        [Button.inline("ကချင်", data="w_kcn"), Button.inline("ကယား", data="w_kyh")],
        [Button.inline("ကရင်", data="w_kyn"), Button.inline("ချင်း", data="w_chn")],
        [Button.inline("မွန်", data="w_mon"), Button.inline("ရှမ်း", data="w_shn")]
    ]
    await event.respond(bq(weather_msg), parse_mode='html', buttons=buttons)

# ====================================================================================
# 🔢 ADVANCED SYSTEM LOGIC 5: SECURE ARITHMETIC CALCULATOR
# ====================================================================================
@bot1.on(events.NewMessage(pattern=r'^/calc\s+(.*)'))
async def secure_calculator_handler(event):
    is_cd, rem = await is_on_cooldown(event.sender_id, "calc", 2)
    if is_cd: return await event.reply(bq(f"⚠️ Cooldown: {rem} စက္ကန့် စောင့်ပါ။"), parse_mode='html')
    
    raw_expression = event.pattern_match.group(1).strip()
    # Security Sandbox: Match only safe mathematical character arrays
    if not re.match(r'^[0-9\+\-\*\/\.\s\(\)]+$', raw_expression):
        return await event.reply(bq("❌ <b>လုံခြုံရေး သတိပေးချက်:</b> ခွင့်မပြုသော ကာရက်တာများ ပါဝင်နေပါသည်။ ဂဏန်းနှင့် သင်္ချာအော်ပရေတာများသာ အသုံးပြုပါ။"), parse_mode='html')
    
    try:
        calculated_result = eval(raw_expression, {"__builtins__": None}, {})
        response_msg = (
            f"🔢 <b><u>Mathematical Calculation Engine</u></b>\n\n"
            f"🧮 <b>တွက်ချက်မှုစနစ်:</b> <code>{raw_expression}</code>\n"
            f"✨ <b>ရရှိလာသော ရလဒ်:</b> <code>{calculated_result}</code>"
        )
        await event.reply(bq(response_msg), parse_mode='html')
    except Exception as e:
        await event.reply(bq(f"❌ သင်္ချာတွက်ချက်မှု ပုံစံ မှားယွင်းနေပါသည်- {escape_html(str(e))}"), parse_mode='html')

# ==========================================
# ⚙️ CHAT ACTIONS & WELCOME MATRIX
# ==========================================
@bot1.on(events.ChatAction)
async def welcome_goodbye(event):
    try:
        chat = await event.get_chat()
        bot_me = await bot1.get_me()
        bot_mention = f"<a href='tg://user?id={bot_me.id}'>{escape_html(bot_me.first_name)}</a>"
        safe_title = escape_html(chat.title)
        target_ids = event.user_ids or [event.user_id]

        if event.user_added or event.user_joined:
            if any(uid == bot_me.id for uid in target_ids if uid):
                await groups_col.update_one({"chat_id": chat.id}, {"$set": {"title": chat.title, "joined_at": datetime.now(TZ)}}, upsert=True)
                msg1 = f"ဟိတ် OWNER နဲ့ Admins တို့ မင်းတို့ရဲ့ ဒီ {safe_title} ထဲကို ထည့်သုံးပေးလို့ ကျေးဇူးတင်ပါတယ်! အသုံးလိုကောင်းလိုနိုင်တဲ့ command များယူထားမယ်ဆိုရင် {bot_mention} ရဲ့ Creator {owner_tag()} ရဲ့ Dm (သို့မဟုတ်) @Comeback_BoD ကိုလာရောက်ပါ!!!"
                await bot1.send_message(chat.id, bq(msg1), parse_mode='html')
                
                try: members = getattr(chat, 'participants_count', 'Unknown')
                except: members = "Unknown"
                msg2 = f"မင်္ဂလာပါ {safe_title}က \n{bot_mention}ထည့်သုံးထားပါသေးတယ်\n\nGroup Name - {safe_title}\nGroup ID - <code>{chat.id}</code>\nMembers အရေအတွက် - {members}\nBot Creator - {owner_tag()}"
                await bot1.send_message(SPECIFIC_GROUP, bq(msg2), parse_mode='html')
            else:
                custom_welcome = await welcome_col.find_one({"chat_id": chat.id})
                for uid in target_ids:
                    if uid and uid != bot_me.id:
                        try: u = await bot1.get_entity(uid)
                        except: continue
                        track_user_metrics(u.id, u.username, u.first_name) 
                        u_mention = f"<a href='tg://user?id={u.id}'>{escape_html(u.first_name)}</a>"
                        
                        if custom_welcome and custom_welcome.get("text"):
                            welcome_text = custom_welcome["text"].replace("{mention}", u_mention).replace("{group}", safe_title)
                        else:
                            welcome_text = (
                                f"ဟိုက် {u_mention}\n"
                                "╔═━━━❖━━━═╗\n"
                                f"{safe_title} ကနေ ကြိုဆိုလိုက်ပါတယ်\n"
                                "╚═━━━❖━━━═╝\n"
                                f"This bot is proudly developed and maintained by 𝑷𝑨𝑹𝑨𝑫𝑶𝑿 𝑭𝒂𝒎𝒊𝒍𝒚⌬𝑩𝑶𝑫.\n"
                            )
                        
                        media_file = custom_welcome.get("media") if custom_welcome else None
                        await bot1.send_message(
                            event.chat_id, bq(welcome_text), parse_mode='html', file=media_file,
                            buttons=[
                                [Button.url("Bot ဖြင့် Broadcasting အပ်ရန်", f"t.me/Hello_Im_DexterMorgan")],
                                [Button.url("ကျွန်ုပ်တို့Groupသို့ဝင်ရန်", "https://t.me/Comeback_BoD")]
                            ]
                        )
                        
        elif event.user_kicked or event.user_left:
            if event.user_id == bot_me.id:
                await groups_col.delete_one({"chat_id": chat.id})
            else:
                try:
                    u = await bot1.get_entity(event.user_id)
                    u_mention = f"<a href='tg://user?id={u.id}'>{escape_html(u.first_name)}</a>"
                    await bot1.send_message(event.chat_id, bq(f"ထွက်သွားလိုက်ပါသေးတယ်.. {u_mention} !!"), parse_mode='html')
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
            f"🛸 ───────────────────\n"
            f"👥 <b>Total Managed Users:</b> <code>{total_tracked_users} 👤</code>\n"
            f"🔥 <b>Active Users (24H):</b> <code>{active_24h_users} 🔥</code>\n"
            f"📍 <b>ထည့်သွင်းအသုံးပြုနေသော (Groups):</b> <code>{total_active_groups} 🪐</code>\n"
            f"⚙️ <b>Engine Status:</b> <code>High-Speed Optimized (No Delay)</code>\n"
            f"🛸 ───────────────────\n"
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
@bot1.on(events.NewMessage(pattern=r'^/id$'))
async def get_identity_matrix_handler(event):
    is_cd, rem = await is_on_cooldown(event.sender_id, "id", 2)
    if is_cd: return
    
    chat_id = event.chat_id
    is_private = event.is_private
    reply = await event.get_reply_message()
    
    if reply:
        target_id = reply.sender_id
        target_user = await reply.get_sender()
        user_type = "Target Profile"
        
        if target_user and getattr(target_user, 'username', None):
            mention = f"@{target_user.username}"
        else:
            first_name = getattr(target_user, 'first_name', None) or "User"
            mention = f"<a href='tg://user?id={target_id}'>{escape_html(first_name)}</a>"
    else:
        target_id = event.sender_id
        sender = await event.get_sender()
        user_type = "Your Profile"
        
        if sender and getattr(sender, 'username', None):
            mention = f"@{sender.username}"
        else:
            first_name = getattr(sender, 'first_name', None) or "User"
            mention = f"<a href='tg://user?id={target_id}'>{escape_html(first_name)}</a>"

    if not is_private:
        try:
            chat = await event.get_chat()
            group_title = escape_html(chat.title)
        except Exception: group_title = "Unknown Sector"
        group_section = f"📍 <b>Group:</b> {group_title}\n🌐 <b>Chat ID:</b> <code>{chat_id}</code>\n"
    else:
        group_section = f"📍 <b>Sector:</b> <code>Private Chat (No Group ID)</code>\n"

    matrix_text = (
        f"🌌 <b>𝙄𝘿 🤓</b>\n"
        f"👤 <b>{user_type}:</b> {mention}\n"
        f"🆔 <b>User ID:</b> <code>{target_id}</code>\n\n"
        f"{group_section}"
    )
    try: await event.respond(bq(matrix_text), parse_mode='html')
    except Exception as e: print(f"Identity Matrix Error: {e}")

# ====================================================================================
# 🛡️ ADVANCED MODERATION CONSOLE
# ====================================================================================
async def get_target_user(event, args):
    reply = await event.get_reply_message()
    if reply: 
        return await reply.get_sender() # 👈 ပြင်ဆင်ချက်: .sender အစား await reply.get_sender() ကိုသုံးရပါမယ်
    if args:
        try:
            target = args.strip()
            return await bot1.get_entity(int(target) if target.lstrip('-').isdigit() else target)
        except Exception: return None
    return None
    
@bot1.on(events.NewMessage(pattern=r'^/mute(?:@\w+)?(?:\s+(.*))?'))
async def mute_user(event):
    if not await check_ban_rights(event.chat_id, event.sender_id): return
    target_user = await get_target_user(event, event.pattern_match.group(1))
    if not target_user: return

    try:
        await bot1.edit_permissions(event.chat_id, target_user.id, send_messages=False)
        await register_temporary_mute(event.chat_id, target_user.id, duration_minutes=60)
        mention = f"<a href='tg://user?id={target_user.id}'>{escape_html(getattr(target_user, 'first_name', 'User'))}</a>"
        mute_text = (
            f"🌌 <b>𝙈𝙐𝙏𝙀 𝙊𝙋𝙀𝙍𝘼𝙏𝙄𝙊𝙉</b>\n"
            f"🔇 <b>Target:</b> {mention}\n"
            f"🆔 <b>User ID:</b> <code>{target_user.id}</code>\n"
            f"⚡ <b>Status:</b> <code>Muted (မြုပြီး)</code>"
        )
        inline_buttons = [[Button.inline("Unmute? 🔊", data=f"inline_unmute_{target_user.id}")]]
        await event.respond(bq(mute_text), parse_mode='html', buttons=inline_buttons)
    except Exception as e: print(f"Mute Error: {e}")

@bot1.on(events.NewMessage(pattern=r'^/unmute(?:@\w+)?(?:\s+(.*))?'))
async def unmute_user(event):
    if not await check_ban_rights(event.chat_id, event.sender_id): return
    target_user = await get_target_user(event, event.pattern_match.group(1))
    if not target_user: return

    try:
        await bot1.edit_permissions(event.chat_id, target_user.id, send_messages=True)
        await muted_registry_col.delete_one({"chat_id": event.chat_id, "user_id": target_user.id})
        mention = f"<a href='tg://user?id={target_user.id}'>{escape_html(getattr(target_user, 'first_name', 'User'))}</a>"
        unmute_text = (
            f"🌌 <b>𝙐𝙉𝘽𝘼𝙉 𝙐𝙉𝙈𝙐𝙏𝙀 𝙊𝙋𝙀𝙍𝘼𝙏𝙄𝙊𝙉</b>\n"
            f"🔊 <b>Target:</b> {mention}\n"
            f"🆔 <b>User ID:</b> <code>{target_user.id}</code>\n"
            f"⚡ <b>Status:</b> <code>Voice Restored / Cleared</code>"
        )
        await event.respond(bq(unmute_text), parse_mode='html')
    except Exception as e: print(f"Unmute Error: {e}")

@bot1.on(events.NewMessage(pattern=r'^/ban(?:@\w+)?(?:\s+(.*))?'))
async def ban_user(event):
    if not await check_ban_rights(event.chat_id, event.sender_id): return
    target_user = await get_target_user(event, event.pattern_match.group(1))
    if not target_user: return

    try:
        await bot1.edit_permissions(event.chat_id, target_user.id, view_messages=False)
        mention = f"<a href='tg://user?id={target_user.id}'>{escape_html(getattr(target_user, 'first_name', 'User'))}</a>"
        ban_text = (
            f"🌌 <b>𝘽𝘼𝙉 𝙊𝙋𝙀𝙍𝘼𝙏𝙄𝙊𝙉</b>\n"
            f"🚫 <b>Target:</b> {mention}\n"
            f"🆔 <b>User ID:</b> <code>{target_user.id}</code>\n"
            f"⚡ <b>Status:</b> <code>Exiled / Perm-Banned (ဘန်းပစ်လိုက်ရှုပ်တယ်)</code>"
        )
        await event.respond(bq(ban_text), parse_mode='html')
    except Exception as e: print(f"Ban Error: {e}")

@bot1.on(events.NewMessage(pattern=r'^/unban(?:@\w+)?(?:\s+(.*))?'))
async def unban_user(event):
    if not await check_ban_rights(event.chat_id, event.sender_id): return
    target_user = await get_target_user(event, event.pattern_match.group(1))
    if not target_user: return

    try:
        await bot1.edit_permissions(event.chat_id, target_user.id, view_messages=True)
        mention = f"<a href='tg://user?id={target_user.id}'>{escape_html(getattr(target_user, 'first_name', 'User'))}</a>"
        unban_text = (
            f"🌌 <b>𝙐𝙉𝘽Α𝙉 𝙊𝙋𝙀𝙍𝘼𝙏𝙄𝙊𝙉</b>\n"
            f"✅ <b>Target:</b> {mention}\n"
            f"🆔 <b>User ID:</b> <code>{target_user.id}</code>\n"
            f"⚡ <b>Status:</b> <code>Ban Lifted / Allowed to Join</code>"
        )
        await event.respond(bq(unban_text), parse_mode='html')
    except Exception as e: print(f"Unban Error: {e}")

@bot1.on(events.NewMessage(pattern=r'^/kick(?:@\w+)?(?:\s+(.*))?'))
async def kick_user(event):
    if not await check_ban_rights(event.chat_id, event.sender_id): return
    target_user = await get_target_user(event, event.pattern_match.group(1))
    if not target_user: return

    try:
        await bot1.edit_permissions(event.chat_id, target_user.id, view_messages=False)
        await bot1.edit_permissions(event.chat_id, target_user.id, view_messages=True)
        mention = f"<a href='tg://user?id={target_user.id}'>{escape_html(getattr(target_user, 'first_name', 'User'))}</a>"
        kick_text = (
            f"🌌 <b>𝙆𝙄𝘾𝙆 𝙊𝙋𝙀𝙍𝘼𝙏𝙄𝙊𝙉</b>\n"
            f" <b>Target:</b> {mention}\n"
            f"🆔 <b>User ID:</b> <code>{target_user.id}</code>\n"
            f"⚡ <b>Status:</b> <code>Removed / Kicked from Chat</code>"
        )
        await event.respond(bq(kick_text), parse_mode='html')
    except Exception as e: print(f"Kick Error: {e}")

@bot1.on(events.CallbackQuery(pattern=r'^inline_unmute_(\d+)'))
async def inline_unmute_callback(event):
    if not await check_ban_rights(event.chat_id, event.sender_id):
        return await event.answer("⚠️ You are not authorized to use this console!", alert=True)

    target_id = int(event.pattern_match.group(1))
    try:
        await bot1.edit_permissions(event.chat_id, target_id, send_messages=True)
        await muted_registry_col.delete_one({"chat_id": event.chat_id, "user_id": target_id})
        try:
            target_user = await bot1.get_entity(target_id)
            name = escape_html(getattr(target_user, 'first_name', 'User'))
        except: name = "User"
            
        mention = f"<a href='tg://user?id={target_id}'>{name}</a>"
        updated_text = (
            f"🌌 <b>𝙈𝙐𝙏𝙀 𝙎𝙔𝙎𝙏𝙀𝙈 (UPDATED)</b>\n"
            f"🔊 {mention} has been <b>Unmuted</b> via administrative console.\n"
            f"⚡ <b>Status:</b> <code>Restrictions Disintegrated</code>"
        )
        await event.edit(bq(updated_text), parse_mode='html', buttons=None) 
    except Exception as e: await event.answer(f"Action Failed: {e}", alert=True)

# ====================================================================================
# 📣 COMPACT ADMIN TAG SYSTEM
# ====================================================================================
@bot1.on(events.NewMessage(pattern=r'^/adm(?:\s+(.*))?'))
async def tag_admins(event):
    if event.is_private: return
    if not await check_admin(event.chat_id, event.sender_id): return
    text = event.pattern_match.group(1) or "Adminတို့ရေ ခေါ်နေပါတယ်🤍"
    
    try:
        admins = await bot1.get_participants(event.chat_id, filter=ChannelParticipantsAdmins)
        admin_mentions = []
        cyber_emojis = ["⚡", "🔮", "💎", "✨", "🔥", "🛸", "🛡️", "🪐", "🌌", "🦾"]
        
        for index, a in enumerate(admins):
            if a.bot or getattr(a, 'deleted', False): continue
            full_name = f"{a.first_name or ''} {a.last_name or ''}".strip() or "Admin"
            emoji = cyber_emojis[index % len(cyber_emojis)]
            mention = f"<a href='tg://user?id={a.id}'><b>{escape_html(full_name)}</b></a> {emoji}"
            admin_mentions.append(mention)
        
        compact_admin_list = "  •  ".join(admin_mentions)
        adm_signal_text = (
            f"</b> <code>{escape_html(text)}</code>\n\n"
            f"🌌 <b>အက်ဒမင်များ ခဏလာကြတဲ့</b>\n"
            f"🦦:</b>\n"
            f"{compact_admin_list}"
        )
        await event.respond(bq(adm_signal_text), parse_mode='html')
    except Exception as e: print(f"Admin Tag System Error: {e}")

@bot1.on(events.NewMessage(pattern=r'^/addallow(?:@\w+)?$'))
async def add_allow(event):
    if event.sender_id != OWNER_ID: return
    reply = await event.get_reply_message()
    if reply:
        await allow_col.update_one({"user_id": reply.sender_id}, {"$set": {"name": reply.sender.first_name}}, upsert=True)
        await event.reply(bq(f"{escape_html(reply.sender.first_name)} ကို Allow List ထည့်ပြီးပါပြီ! ✅"), parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/removeallow(?:@\w+)?\s+(\d+)'))
async def remove_allow(event):
    if event.sender_id != OWNER_ID: return
    target_id = int(event.pattern_match.group(1))
    delete_result = await allow_col.delete_one({"user_id": target_id})
    if delete_result.deleted_count > 0: await event.respond(bq(f"User ID {target_id} ကို ပါမစ်မပေးတော့ဘူး"), parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/allowlist(?:@\w+)?$'))
async def allow_list(event):
    if event.sender_id != OWNER_ID: return
    users = await allow_col.find().to_list(length=None)
    msg = "<b>✅ Allowed Users List:</b>\n\n" + "".join([f"• <a href='tg://user?id={u.get('user_id')}'>{escape_html(u.get('name', 'Unknown'))}</a> (<code>{u.get('user_id')}</code>)\n" for u in users]) if users else "Allow List မှာ မရှိသေးပါ။"
    await event.reply(bq(msg), parse_mode='html')

# ====================================================================================
# 📢 GLOBAL BROADCAST SYSTEM
# ====================================================================================
@bot1.on(events.NewMessage(pattern=r'^/ပို့(?:@\w+)?(?:\s+(.*))?'))
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

# ====================================================================================
# 🛡️ CHANNEL FORWARD, BIO & HARREM FILTERS
# ====================================================================================
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

# ====================================================================================
# 🎵 ADVANCED MULTIMEDIA PLAY & TRACKING SYSTEM
# ====================================================================================
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

# ==========================================
# 🧹 OWNER SYSTEM PURGE COMMAND (/cleanhai)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/cleanhai(?:@\w+)?$'))
async def clean_all_harem_catches(event):
    """Owner မှ စနစ်အတွင်းရှိ ကစားသမားအားလုံး၏ ဖမ်းမိထားသော ကတ်များ (Vault) နှင့် Catch Records အားလုံးကို ရှင်းလင်းဖျက်သိမ်းသည့် စနစ်"""
    if event.sender_id != OWNER_ID: return
    try:
        # ဒေတာဘေ့စ်အတွင်းရှိ ကစားသမားအားလုံး၏ harem, total_caught နှင့် group_catches များကို Reset ချပြီး favorite ကတ်ကိုပါ ဖျက်ခြင်း
        result = await users_catcher_col.update_many(
            {},
            {
                "$set": {
                    "harem": [],
                    "total_caught": 0,
                    "group_catches": {}
                },
                "$unset": {
                    "fav_card": ""
                }
            }
        )
        
        text = (
            f"🧹 <b>{f('SYSTEM PURGE MATRIX ACTIVATED')}</b>\n"
            f"⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n"
            f"<blockquote>Database အတွင်းရှိ ကစားသမားအားလုံးရဲ့ Vault Collection (<code>/hai</code>)၊ Total Catches (<code>/profile</code>) နဲ့ ဖမ်းယူထားသမျှ Item Records အားလုံးကို အောင်မြင်စွာ ရှင်းလင်း (Reset) ပြုလုပ်လိုက်ပါပြီ Boss! ⚠️</blockquote>\n"
            f"📊 <b>{f('Affected Accounts')}:</b> <code>{result.modified_count}</code> ယောက်"
        )
        await event.reply(text, parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ <b>Cleanhai System Fault:</b> <code>{e}</code>", parse_mode='html')

# ====================================================================================
# 💀 BULlY & SHADOW BAN SYSTEM
# ====================================================================================
@bot1.on(events.NewMessage(pattern=r'^/b(?:@\w+)?$'))
async def bot_bully(event):
    if not await is_allowed(event.sender_id): return
    reply = await event.get_reply_message()
    if not reply: return
    try: await event.delete()
    except: pass
    t = await reply.get_sender()
    if not t or t.id == OWNER_ID: return

    bot_bully_tasks[event.chat_id] = True
    mention = f"<a href='tg://user?id={t.id}'>{escape_html(t.first_name)}</a>"
    db_filters = await filters_col.find().to_list(length=None)
    words = [w.get("text") for w in db_filters if w.get("text")] or ["စောက်ချိုးမပြေဘူး"]

    while bot_bully_tasks.get(event.chat_id):
        try: 
            await bot1.send_message(event.chat_id, bq(f"{mention} {random.choice(words)}"), reply_to=reply.id, parse_mode='html')
            await asyncio.sleep(2.0) 
        except: bot_bully_tasks[event.chat_id] = False; break

@bot1.on(events.NewMessage(pattern=r'^/sb(?:@\w+)?$'))
async def stop_bot_bully(event):
    if not await is_allowed(event.sender_id): return
    bot_bully_tasks[event.chat_id] = False
    await event.respond(bq("နားလိုက်ပြီ။"))
                
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

@bot1.on(events.NewMessage(pattern=r'^လိုက်မှတ်$'))
async def turn_on_learning(event):
    if event.sender_id != OWNER_ID or event.chat_id != SPECIFIC_CONTROL_GROUP: return
    learning_status[SPECIFIC_CONTROL_GROUP] = True
    await event.reply(bq("စကားမှတ်သားခြင်း စနစ် ဖွင့်လိုက်ပြီ။"))

@bot1.on(events.NewMessage(pattern=r'^မမှတ်နဲ့တော့$'))
async def turn_off_learning(event):
    if event.sender_id != OWNER_ID or event.chat_id != SPECIFIC_CONTROL_GROUP: return
    learning_status[SPECIFIC_CONTROL_GROUP] = False
    await event.reply(bq("စကားမှတ်သားခြင်း စနစ် ပိတ်လိုက်ပြီ။"))

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

@bot1.on(events.NewMessage(pattern=r'^(ရပ်|အပစ်ရပ်)$'))
async def stop_actions(event):
    if not await is_allowed(event.sender_id): return
    shoot_tasks[event.chat_id] = False
    if event.chat_id in tracking_targets: del tracking_targets[event.chat_id]
    await event.reply(bq("အပစ်ရပ်စဲလိုက်ပြီ။"))

@bot1.on(events.NewMessage())
async def dark_passenger_watcher(event):
    chat_id = event.chat_id
    sender_id = event.sender_id
    if chat_id in dark_passenger_targets and sender_id == dark_passenger_targets[chat_id]:
        if event.text and not event.text.startswith(('/', '.', 'မှတ်')):
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
                warn_msg = f"🚫 <b>CHANNEL FORWARD NOT ALLOWED!</b>\n\nHey {mention} Channel ကနေ Forward လာတဲ့ Post တွေ ပို့ခွင့်မရှိပါဘူးရှင့်🌬️<b>[Erased]</b>"
                return await event.respond(bq(warn_msg), parse_mode='html')
            except Exception: pass


# ====================================================================================
# 👈 REPLY SAVE AUTO RESPONDER SYSTEM (NON-BLOCKING ASYNC POOL)
# ====================================================================================
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
# 🚀 SYSTEM INITIALIZATION GATEWAY (အမှန်ကန်ဆုံး ပေါင်းစပ်ပြီးသားဗားရှင်း)
# ==========================================
async def start_system():
    threading.Thread(target=run_flask, daemon=True).start()
    print("Starting Main Bot Engine (Sovereign System Plus Architecture)...")
    
    # 🗄️ Active Loop ထဲရောက်မှ Index တွေကို စနစ်တကျ Auto Sync လုပ်ပေးခြင်း (ဒါကြောင့် Render error မတက်တော့ပါ)
    try:
        await users_col.create_index("user_id", unique=True)
        await user_game_profiles_col.create_index("user_id", unique=True)
        await characters_col.create_index("name", unique=True)
        print("Database Indexes synchronized successfully! ✔️")
    except Exception as e:
        print(f"Index creation error: {e}")

    # Bot Engine ကို စတင်ပတ်ခြင်း
    await bot1.start(bot_token=MAIN_BOT_TOKEN)
    me_main = await bot1.get_me()
    bot_ids.append(me_main.id)
    print(f"Sovereign Engine synchronized as @{me_main.username}")
    
    # Background Worker Coroutines
    asyncio.create_task(weather_daily_cron_loop())
    
    # Bot ကို အမြဲတမ်း Live ဖြစ်နေအောင် ထိန်းထားပေးမည့်အဆင့်
    await bot1.run_until_disconnected()

if __name__ == '__main__':
    try: 
        asyncio.run(start_system())
    except (KeyboardInterrupt, SystemExit): 
        print("Sovereign Core Shutting Down Safely.")
