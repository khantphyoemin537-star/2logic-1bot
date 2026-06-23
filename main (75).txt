import io
import asyncio
import logging
import random
import os
import threading
import re
import time
import urllib.request
import json
from datetime import datetime, timedelta, date
from flask import Flask, jsonify, render_template_string
from PIL import Image, ImageDraw
from html.parser import HTMLParser
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument
from html import escape as escape_html
from telethon.errors import FloodWaitError
from telethon import TelegramClient, events, types, Button, errors
from collections import defaultdict
from telethon.tl.types import ChannelParticipantsAdmins
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
MAIN_BOT_TOKEN = "8575371720:AAEWWV42CGrwooM_joiJXdo2iEw2_7atyXU"
SPECIFIC_CONTROL_GROUP = -1003940667453 

# ==========================================
# 🗄️ DATABASE CONNECTION MATRIX
# ==========================================
client_mongo = AsyncIOMotorClient(MONGO_URI)
db = client_mongo["telegram_bot"]

characters_base_col = db["characters_base_data"]  
users_catcher_col = db["users_catcher_data"]      
groups_counters_col = db["groups_msg_counters"]    
groups_config_col = db["groups_catcher_config"]   
marketplace_col = db["marketplace_data"]          
boss_col = db["boss_active_data"] 
guilds_col = db["guilds_data"]

bot1 = TelegramClient('bot_main_session', APP_ID, APP_HASH)
active_group_spawns = {} 
active_card_games = {}
STEALTH_MAU_MODE = False
spawn_locks = defaultdict(asyncio.Lock) 
# ==========================================
# 🌌 GLOBAL BOSS POOL MATRIX (၁၀ ကောင်မှ ၁၅ ကောင် စာရင်း)
# ==========================================
BOSS_POOL = [
    {"name": "🔥 𝔎𝔦𝔫𝔤 𝔇𝔢𝔪𝔬𝔫 𝔄𝔰𝔥𝔱𝔞𝔯𝔬𝔱𝔥", "max_hp": 5000},
    {"name": "🐉 𝔄𝔫𝔠𝔦𝔢𝔫𝔱 𝔙𝔬𝔶𝔡 𝔇𝔯𝔞𝔤𝔬𝔫", "max_hp": 7500},
    {"name": "💀 𝔖𝔥𝔞𝔡𝔬𝔴 𝔑𝔢𝔠𝔯𝔬𝔪𝔞𝔫𝔠𝔢𝔯", "max_hp": 4000},
    {"name": "⚡ ℭ𝔥𝔞𝔬𝔰 ℔𝔥𝔲𝔫𝔡𝔢𝔯 𝔊𝔬𝔡", "max_hp": 6500},
    {"name": "❄️ 𝔉𝔯𝔬𝔰𝔱 𝔖𝔭𝔢𝔠𝔱𝔢𝔯 ℜ𝔢𝔵", "max_hp": 4500},
    {"name": "🎭 𝔗𝔥𝔢 𝔐𝔞𝔡 𝔍𝔢𝔰𝔱𝔢𝔯", "max_hp": 3500},
    {"name": "🩸 𝔙𝔞𝔪𝔭𝔦𝔯𝔢 ℔𝔬𝔯𝔡 𝔇𝔯𝔞𝔠𝔲𝔩𝔞", "max_hp": 5500},
    {"name": "⛰️ ℭ𝔲𝔯𝔰𝔢𝔡 𝔖𝔱𝔬𝔫𝔢 𝔊𝔬𝔩𝔢𝔪", "max_hp": 8000},
    {"name": "🦊 𝔑𝔦𝔫𝔢-𝔗𝔞𝔦𝔩𝔢𝔡 𝔇𝔢𝔪𝔬𝔫 𝔉𝔬𝔵", "max_hp": 6000},
    {"name": "⚔️ 𝔓𝔥𝔞𝔫𝔱𝔬𝔪 𝔖𝔥𝔦𝔫𝔬𝔟𝔦 𝔒𝔯𝔬𝔠𝔥𝔦", "max_hp": 4800},
    {"name": "🪐 𝔖𝔭𝔞𝔿𝔢 ℔𝔦𝔱𝔥 ℭ𝔱𝔥𝔲𝔩𝔥𝔲", "max_hp": 9999},
    {"name": "🦾 ℭ𝔶𝔟𝔢𝔯𝔫𝔢𝔱𝔦𝔠 𝔇𝔢𝔰𝔱𝔯𝔬𝔶𝔢𝔯", "max_hp": 7000}
]

# 🎭 RARITY MAPPING MATRIX (NEON GACHA EDITION 🔥)
# ==========================================
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
# 🎨 SMART PIL CIRCULAR PROFILE OVERLAY ENGINE
# ==========================================
from PIL import Image, ImageDraw
import io

def overlay_profile_picture(bg_bytes, pfp_bytes):
    """နောက်ခံပုံပေါ်တွင် User Profile ကို အဝိုင်းပုံစံ ထင်ထင်ရှားရှား အလယ်ဗဟို၌ ထည့်ပေးသည့် Function"""
    try:
        bg_img = Image.open(io.BytesIO(bg_bytes)).convert("RGBA")
        pfp_img = Image.open(io.BytesIO(pfp_bytes)).convert("RGBA")
        
        bg_w, bg_h = bg_img.size
        # ပုံရဲ့ Size ပေါ်မူတည်ပြီး Profile Size ကို အချိုးကျတွက်ချက်ခြင်း
        pfp_size = min(bg_w, bg_h) // 4
        if pfp_size < 120: pfp_size = 120
        if pfp_size > 320: pfp_size = 320
        
        pfp_img = pfp_img.resize((pfp_size, pfp_size), Image.Resampling.LANCZOS)
        
        # Profile ကို အဝိုင်းဖြစ်အောင် Mask ပြုလုပ်ခြင်း
        mask = Image.new("L", (pfp_size, pfp_size), 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0, pfp_size, pfp_size), fill=255)
        
        circular_pfp = Image.new("RGBA", (pfp_size, pfp_size))
        circular_pfp.paste(pfp_img, (0, 0), mask=mask)
        
        # ထင်ထင်ရှားရှားဖြစ်အောင် အဖြူရောင် Border လိုင်းလှလှလေး ထည့်ခြင်း
        draw_border = ImageDraw.Draw(circular_pfp)
        draw_border.ellipse((0, 0, pfp_size - 1, pfp_size - 1), outline="white", width=5)
        
        # အလယ်ဗဟိုတည်နေရာ ရှာဖွေခြင်း
        pos_x = bg_w // 2 - pfp_size // 2
        pos_y = bg_h // 2 - pfp_size // 2
        
        bg_img.paste(circular_pfp, (pos_x, pos_y), circular_pfp)
        
        output = io.BytesIO()
        bg_img.convert("RGB").save(output, format="JPEG", quality=95)
        return output.getvalue()
    except Exception as e:
        print(f"Pillow Overlay Error: {e}")
        return bg_bytes

# ==========================================
# ⚙️ GLOBAL WELCOME TEMPLATE SETTER COMMANDS
# ==========================================

@bot1.on(events.NewMessage(pattern=r'^/setwc$'))
async def set_welcome_caption(event):
    """Owner မှ Welcome စာသားအား Premium Emoji ပါဝင်မှုမပျက် သိမ်းဆည်းရန်"""
    if event.sender_id != OWNER_ID: return
        
    if not event.is_reply:
        return await event.reply("⚠️ <b>Welcome စာသားကို Reply ပြန်ပြီး <code>/setwc</code> ဟု ရိုက်ပေးပါ Boss!</b>", parse_mode='html')
        
    reply_msg = await event.get_reply_message()
    if not reply_msg or (not reply_msg.text and not reply_msg.message):
        return await event.reply("❌ <b>Reply ပြန်ထားတဲ့ မက်ဆေ့ခ်ျထဲမှာ စာသားမတွေ့ပါဘူး Bro!</b>", parse_mode='html')
        
    # Unicode / Premium Emoji Formatting အပြည့်အစုံကို HTML သို့ ပြောင်းလဲခြင်း
    html_template = message_to_html(reply_msg.message, reply_msg.entities)
    
    await groups_config_col.update_one(
        {"chat_id": "global_welcome_settings"},
        {"$set": {"welcome_html_template": html_template}},
        upsert=True
    )
    
    success_text = (
        f"✅ <b>{f('GLOBAL WELCOME TEXT CONFIG MATRIX LOCKED')}</b>\n"
        f"{html_template}\n\n"
        f"<🔥</blockquote>"
    )
    
    # 🌟 FIX: parse_mode='html' ကို မသုံးဘဲ Custom Parser ဖြင့် Text နှင့် Entities ခွဲထုတ်ပြီး ပို့ဆောင်ခြင်း
    clean_text, entities = parse_html_to_telethon(success_text)
    await event.reply(clean_text, entities=entities)


@bot1.on(events.NewMessage(pattern=r'^/setbg$'))
async def set_welcome_background(event):
    """Owner မှ Welcome နောက်ခံ ပုံ (သို့) ဗီဒီယိုအား Group အားလုံးအတွက် သတ်မှတ်ရန်"""
    if event.sender_id != OWNER_ID: return
        
    if not event.is_reply:
        return await event.reply("⚠️ <b>Welcome နောက်ခံဖြစ်စေချင်တဲ့ ပုံ (သို့) ဗီဒီယိုကို Reply ပြန်ပြီး <code>/setbg</code> ဟု ရိုက်ပေးပါ Boss!</b>", parse_mode='html')
        
    reply_msg = await event.get_reply_message()
    if not reply_msg or not (reply_msg.photo or reply_msg.video or reply_msg.document):
        return await event.reply("❌ <b>ထည့်သွင်းရန် သင့်လျော်သော ပုံ (သို့) ဗီဒီယို ရှာမတွေ့ပါ Bro!</b>", parse_mode='html')
        
    media_type = "photo" if reply_msg.photo else "video"
    if reply_msg.document and reply_msg.document.mime_type.startswith("video"):
        media_type = "video"
        
    try:
        # Control Group ထဲသို့ သိမ်းဆည်းရန်အတွက် လုံခြုံစွာ Forward လုပ်ခြင်း
        forwarded_msg = await send_safe_message(bot1, SPECIFIC_CONTROL_GROUP, "", file=reply_msg.media)
        storage_msg_id = forwarded_msg.id
        
        await groups_config_col.update_one(
            {"chat_id": "global_welcome_settings"},
            {"$set": {
                "bg_storage_msg_id": storage_msg_id,
                "bg_media_type": media_type
            }},
            upsert=True
        )
        
        await event.reply(f"✅ <b>Global Welcome Background Injected!</b>\n\n📸 Type: <code>{media_type.upper()}</code>\n📦 Storage Msg ID: <code>{storage_msg_id}</code>\n<blockquote>နောက်ခံ Media အား စနစ်ထဲ ထည့်သွင်းပြီးပါပြီ။ ဂရုအားလုံးတွင် သုံးပါမည် Boss! ⚡</blockquote>", parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ <b>Background Save Fault:</b> <code>{escape_html(str(e))}</code>", parse_mode='html')


# ==========================================
# 🌌 GLOBAL USER JOIN ACTION DISPATCHER
# ==========================================
@bot1.on(events.ChatAction)
async def global_welcome_handler(event):
    """လူသစ်ဝင်လာပါက Auto Account ဖွင့်ပေးပြီး Premium Welcome Media ပေးပို့သည့် စနစ်"""
    if not (event.user_joined or event.user_added):
        return
        
    try:
        chat = await event.get_chat()
        group_name = getattr(chat, 'title', 'This Active Realm')
        group_id = event.chat_id
    except:
        return
        
    user_id = event.user_id
    try:
        user_entity = await event.get_user()
        first_name = getattr(user_entity, 'first_name', '') or ''
        last_name = getattr(user_entity, 'last_name', '') or ''
        fullname = f"{first_name} {last_name}".strip()
        if not fullname:
            fullname = getattr(user_entity, 'username', '') or f"Agent {user_id}"
    except:
        fullname = f"Agent {user_id}"
        
    # Joined Date အား လှပသပ်ရပ်စွာ ထုတ်ယူခြင်း
    joined_date = datetime.now().strftime("%Y-%m-%d %I:%M %p")
    
    # လူသစ်အား Catcher Game စနစ်ထဲ အလိုအလျောက် အကောင့်ဖွင့်ပေးခြင်း
    mention_html = f"<a href='tg://user?id={user_id}'><b>{escape_html(fullname)}</b></a>"
    await ensure_user_registered(user_id, mention_html)
    
    # ဒေတာဘေ့စ်မှ Welcome Settings ကို ထုတ်ယူခြင်း
    settings = await groups_config_col.find_one({"chat_id": "global_welcome_settings"})
    if not settings:
        return # သတ်မှတ်မထားပါက ဘာမှဝင်မလုပ်ပါ
        
    welcome_html_template = settings.get("welcome_html_template")
    bg_storage_msg_id = settings.get("bg_storage_msg_id")
    bg_media_type = settings.get("bg_media_type", "photo")
    
    if not welcome_html_template:
        return # စာသားမရှိလျှင် မပို့ပါ
        
    # Placeholder များကို ပုံစံမပျက် ဘေးကင်းစွာ အစားထိုးခြင်း
    caption_html = welcome_html_template.replace("{fullname}", mention_html).replace("{groupname}", escape_html(group_name)).replace("{joined_date}", escape_html(joined_date))
    
    # 🌟 FIX: HTML Template အား Telethon သုံးနိုင်ရန် Text နှင့် Entities အဖြစ် ကြိုတင်ခွဲထုတ်ခြင်း
    clean_caption, entities = parse_html_to_telethon(caption_html)
    
    if bg_storage_msg_id:
        try:
            # Control Group ထဲက မူရင်း မီဒီယာဖိုင်ကို ဆွဲထုတ်ခြင်း
            storage_msg = await bot1.get_messages(SPECIFIC_CONTROL_GROUP, ids=bg_storage_msg_id)
            if storage_msg and storage_msg.media:
                if bg_media_type == "photo":
                    # ပုံဖြစ်ပါက ဒေါင်းလုဒ်ဆွဲပြီး Profile Overlay Engine သို့ ပို့မည်
                    bg_stream = io.BytesIO()
                    await bot1.download_media(storage_msg.media, file=bg_stream)
                    
                    pfp_stream = io.BytesIO()
                    has_pfp = await bot1.download_profile_photo(user_id, file=pfp_stream)
                    
                    if has_pfp:
                        processed_image = overlay_profile_picture(bg_stream.getvalue(), pfp_stream.getvalue())
                    else:
                        processed_image = bg_stream.getvalue()
                        
                    # 🌟 FIX: parse_mode='html' ကိုဖြုတ်ပြီး ပြောင်းလဲထားသော entities ကို တိုက်ရိုက်ထည့်သွင်းပေးပို့ခြင်း
                    await send_safe_file(bot1, group_id, file=io.BytesIO(processed_image), caption=clean_caption, entities=entities)
                    return
                else:
                    # ဗီဒီယိုဖြစ်ပါက မူရင်း Quality အတိုင်း တိုက်ရိုက် Spawn ပို့ပေးမည်
                    # 🌟 FIX: parse_mode='html' ကိုဖြုတ်ပြီး ပြောင်းလဲထားသော entities ကို တိုက်ရိုက်ထည့်သွင်းပေးပို့ခြင်း
                    await send_safe_file(bot1, group_id, file=storage_msg.media, caption=clean_caption, entities=entities)
                    return
        except Exception as e:
            print(f"Welcome Media Forwarding Error: {e}")
            
    # အကယ်၍ မီဒီယာပို့ရာတွင် Error တက်ပါက Safe စာသားသက်သက်ဖြင့်သာ လှမ်းအော်ပေးမည်
    # 🌟 FIX: parse_mode='html' ကိုဖြုတ်ပြီး ပြောင်းလဲထားသော entities ကို တိုက်ရိုက်ထည့်သွင်းပေးပို့ခြင်း
    await send_safe_message(bot1, group_id, clean_caption, entities=entities)

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
            f"⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n"
            f"🆔 <b>{f('Character ID')}:</b> <code>{char_id}</code>\n"
            f"👤 <b>{f('Name')}:</b> <code>{char_name}</code>\n"
            f"🌐 <b>{f('Category')}:</b> <code>{category_name}</code>\n"
            f"🌟 <b>{f('Rarity')}:</b> {r_info['name']}\n"
            f"💎 <b>{f('Worth')}:</b> <code>{r_info['value']} MMK</code>\n"
            f"⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n"
            f"<blockquote><b>{f('Status')}:</b> Storage ID {storage_id} နဲ့ အိုင်တမ်အသစ်ကို Database ထဲ အောင်မြင်စွာ ချိတ်ဆက်ပြီးပါပြီ Boss! 😎🎧</blockquote>"
        )
        await event.reply(success_msg, parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ <b>Database Inject Error</b>: <code>{escape_html(str(e))}</code>", parse_mode='html')


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
            f"⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n"
            f"<blockquote>Database အတွင်းရှိ Player စုစုပေါင်း <code>{result.modified_count}</code> ယောက်ထံမှ တစ်ဦးလျှင် <code>{amount:,} MMK</code> စီ {f('နှုတ်ယူသိမ်းဆည်းလိုက်ပါပြီ Boss!')} ⚠️</blockquote>"
        )
        await event.reply(text, parse_mode='html')
    except Exception as e:
        await event.reply(f"❌ <b>Takeall System Fault:</b> <code>{e}</code>", parse_mode='html')
# 🚨 OWNER သာ သုံးခွင့်ရှိသော BOSS SPAWNER
@bot1.on(events.NewMessage(pattern=r'^/spawnboss$'))
async def spawn_random_boss(event):
    if event.sender_id != OWNER_ID: return
    
    # ယခင် ရှိနေခဲ့ဖူးသော Active Boss အဟောင်းများကို ရှင်းထုတ်ခြင်း
    await boss_col.delete_many({"status": "active"})
    
    # BOSS_POOL ထဲမှ ကျပန်း တစ်ကောင်ရွေးခြင်း
    chosen_boss = random.choice(BOSS_POOL)
    boss_id = f"B{random.randint(1000, 9999)}"
    
    # Database ထဲ Data Inject လုပ်ခြင်း
    active_boss_data = {
        "boss_id": boss_id,
        "name": chosen_boss["name"],
        "hp": chosen_boss["max_hp"],
        "max_hp": chosen_boss["max_hp"],
        "status": "active",
        "contributors": {}
    }
    
    await boss_col.insert_one(active_boss_data)
    
    # Group ထဲကို Boss ထွက်လာကြောင်း ကြေညာခြင်း
    announcement = (
        f"🚨 <b>GLOBAL BOSS RAID WARNING</b> 🚨\n"
        f"⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n\n"
        f"ငရဲတံခါး ပွင့်သွားပြီး ကမ္ဘာမြေပေါ်သို့ ရန်သူဆိုးကြီး ကျရောက်လာပါပြီ။\n\n"
        f"👹 Boss Name: <b>{chosen_boss['name']}</b>\n"
        f"🩸 Total HP: <code>{chosen_boss['max_hp']} / {chosen_boss['max_hp']}</code>\n\n"
        f"⚡ ━━━━━━━━━━━━━━━━━━━━ ⚡\n"
        f"⚔️ ကစားသမားများအားလုံး စုပေါင်းပြီး <code>/attack [Card_ID]</code> ဖြင့် အမြန်ဆုံး သုတ်သင်ကြပါဗျာ။"
    )
    
    await send_safe_message(bot1, event.chat_id, announcement, parse_mode='html')
# ၁။ လက်ရှိ Boss အခြေအနေကို ကြည့်ခြင်း
@bot1.on(events.NewMessage(pattern=r'^/boss$'))
async def view_boss(event):
    active_boss = await boss_col.find_one({"status": "active"})
    if not active_boss:
        return await send_safe_message(bot1, event.chat_id, "💤 လက်ရှိအချိန်မှာ Global Boss မရှိသေးပါဘူးဗျာ။")
    
    # HP Bar ဖန်တီးခြင်း
    hp_percent = int((active_boss["hp"] / active_boss["max_hp"]) * 10)
    hp_bar = "🟥" * hp_percent + "⬛" * (10 - hp_percent)
    
    msg = (
        f"👹 <b>GLOBAL BOSS RAID ACTIVE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"NAME: <b>{active_boss['name']}</b>\n"
        f"HP: <code>{active_boss['hp']}/{active_boss['max_hp']}</code>\n"
        f"[{hp_bar}] ({int((active_boss['hp']/active_boss['max_hp'])*100)}%)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚔️ <b>တိုက်ခိုက်ရန်:</b> <code>/attack [BOD_ID]</code>\n"
        f"<i>(ဥပမာ- /attack BOD1234)</i>"
    )
    await send_safe_message(bot1, event.chat_id, msg, parse_mode='html')

# ၂။ မိမိကတ်ဖြင့် Boss ကို တိုက်ခိုက်ခြင်း
@bot1.on(events.NewMessage(pattern=r'^/attack(?:\s+(.+))?'))
async def attack_boss(event):
    user_id = event.sender_id
    char_id = event.pattern_match.group(1)
    
    if not char_id:
        return await event.reply("⚠️ <b>တိုက်ခိုက်မည့် Card ID ထည့်ပေးပါ!</b>\nUsage: <code>/attack CH12345</code>", parse_mode='html')
    
    active_boss = await boss_col.find_one({"status": "active"})
    if not active_boss:
        return await event.reply("❌ လက်ရှိတိုက်ခိုက်စရာ Boss မရှိပါဘူး။")
        
    # ကစားသမားဆီမှာ အဲဒီကတ် တကယ်ရှိမရှိ စစ်ဆေးခြင်း
    user_card = await users_catcher_col.find_one({"user_id": user_id, "char_id": char_id.strip().upper()})
    if not user_card:
        return await event.reply("❌ သင့် Inventory ထဲမှာ ဒီ Card ID မရှိပါဘူးဗျာ။")
    
    # ကတ်ရဲ့ တန်ဖိုး (Worth) အပေါ်မူတည်ပြီး Damage တွက်ချက်ခြင်း
    base_dmg = user_card.get("currency_value", 50)
    damage = random.randint(int(base_dmg * 0.8), int(base_dmg * 1.2)) # Variable damage +-20%
    
    new_hp = max(0, active_boss["hp"] - damage)
    
    # Contributors ထဲ ဒေတာပေါင်းထည့်ခြင်း
    contributors = active_boss.get("contributors", {})
    str_user_id = str(user_id)
    contributors[str_user_id] = contributors.get(str_user_id, 0) + damage
    
    if new_hp <= 0:
        # Boss သေဆုံးသွားပါက Reward ပေးမည့် Logic
        await boss_col.update_one({"_id": active_boss["_id"]}, {"$set": {"hp": 0, "status": "defeated", "contributors": contributors}})
        
        # Damage အများဆုံး ပေးနိုင်သူ Top 3 ကို ရှာခြင်း
        sorted_contributors = sorted(contributors.items(), key=lambda x: x[1], reverse=True)[:3]
        
        leaderboard_text = ""
        for index, (u_id, dmg) in enumerate(sorted_contributors):
            mention = await get_html_mention(event, int(u_id))
            leaderboard_text += f"{index+1}️⃣ {mention} - {dmg} DMG 🏆\n"
            # ဤနေရာတွင် Top 3 ကို ပိုက်ဆံ (သို့) Special Item များ Code ဖြင့် ပေးနိုင်ပါသည်
            
        reward_msg = (
            f"🎉 <b>BOSS DEFEATED!</b> 🎉\n"
            f"<b>{active_boss['name']}</b> ကို အောင်မြင်စွာ နှိမ်နင်းနိုင်ခဲ့ပါပြီ။\n\n"
            f"🏆 <b>TOP DAMAGE DEALERS (REWARDS SENT):</b>\n{leaderboard_text}"
        )
        await send_safe_message(bot1, event.chat_id, reward_msg, parse_mode='html')
    else:
        # Boss သွေးလျော့သွားကြောင်း Update လုပ်ခြင်း
        await boss_col.update_one({"_id": active_boss["_id"]}, {"$set": {"hp": new_hp, "contributors": contributors}})
        await event.reply(f"⚔️ သင်သည် <b>{user_card['name']}</b> ကိုသုံးပြီး Boss ကို 💥 <code>{damage}</code> Damage ပေးလိုက်နိုင်ပါပြီ!")
# Frame ဆိုင်နှင့် လက်ရှိရှိသော Frame များစာရင်း
# ==========================================
# 🎨 6. CARD FRAME COSMETIC SHOP & ENGINE
# ==========================================
AVAILABLE_FRAMES = {
    "neon": {"name": "⚡ [ NEON GLOW ]", "cost": 500, "style": "⚡ <b><tg-spoiler>{name}</tg-spoiler></b> ⚡"},
    "hellfire": {"name": "🔥 [ HELLFIRE ]", "cost": 1000, "style": "🔥 <i><u>{name}</u></i> 🔥"},
    "sakura": {"name": "🌸 [ SAKURA VALE ]", "cost": 300, "style": "🌸 <b>{name}</b> 🌸"}
}

@bot1.on(events.NewMessage(pattern=r'^/frame(?:\s+(.+))?'))
async def card_frame_system(event):
    user_id = event.sender_id
    args = event.pattern_match.group(1)
    
    # 1️⃣ Frame Shop စာရင်းကို ပြသခြင်း
    if not args:
        shop_text = "🎨 <b>CARD FRAME COSMETIC SHOP</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        for fid, f_info in AVAILABLE_FRAMES.items():
            shop_text += f"🔹 Code: <code>{fid}</code> | {f_info['name']} - Cost: <code>{f_info['cost']} MMK</code>\n"
        shop_text += "━━━━━━━━━━━━━━━━━━━━\n📌 <b>ဝယ်ယူရန်:</b> <code>/frame buy [Frame_Code]</code>\n📌 <b>ကတ်တွင်တပ်ရန်:</b> <code>/frame apply [Card_ID] [Frame_Code]</code>"
        return await send_safe_message(bot1, event.chat_id, shop_text, parse_mode='html')
        
    parts = args.split()
    sub_command = parts[0].lower()
    
    # 2️⃣ Frame ဝယ်ယူသည့်အပိုင်း (/frame buy hellfire)
    if sub_command == "buy" and len(parts) >= 2:
        frame_code = parts[1].lower()
        if frame_code not in AVAILABLE_FRAMES:
            return await event.reply("❌ ထို Frame Code မရှိပါဘူးဗျာ။")
            
        frame_info = AVAILABLE_FRAMES[frame_code]
        
        # User အကောင့် ရှိမရှိနှင့် ပိုက်ဆံ စစ်ဆေးခြင်း
        user_data = await users_catcher_col.find_one({"user_id": user_id})
        if not user_data:
            return await event.reply("❌ သင့်အကောင့်ကို မတွေ့ပါဘူး။ အရင်ဆုံး /catch တစ်ခါလုပ်ပြီး အကောင့်ဖွင့်ပါ။")
            
        balance = user_data.get("wallet_balance", 0)
        if balance < frame_info["cost"]:
            return await event.reply(f"❌ <b>ဝယ်ယူရန် ပိုက်ဆံမလောက်ပါဘူး Boss!</b>\n💵 လိုအပ်ချက်: <code>{frame_info['cost']} MMK</code>\n🪙 သင့်လက်ကျန်: <code>{balance} MMK</code>", parse_mode='html')
            
        # ဝယ်ပြီးသား ဖြစ်မဖြစ် စစ်ဆေးခြင်း
        owned_frames = user_data.get("owned_frames", [])
        if frame_code in owned_frames:
            return await event.reply(f"❌ သင်သည် {frame_info['name']} ကို ဝယ်ယူထားပြီးသား ဖြစ်ပါသည်။")
            
        # ပိုက်ဆံနှုတ်ပြီး ဝယ်ထားသော စာရင်း (owned_frames) ထဲသို့ ဒေတာ ထည့်ခြင်း
        await users_catcher_col.update_one(
            {"user_id": user_id},
            {
                "$inc": {"wallet_balance": -frame_info["cost"]},
                "$push": {"owned_frames": frame_code}
            }
        )
        return await event.reply(f"🎉 <b>ဝယ်ယူမှု အောင်မြင်ပါပြီ!</b>\nคุณ {frame_info['name']} ကို <code>{frame_info['cost']} MMK</code> ဖြင့် ဝယ်ယူလိုက်ပါပြီ။\n📌 သင့်ကတ်မှာ သွားတပ်ရန်: <code>/frame apply [Card_ID] {frame_code}</code>", parse_mode='html')

    # 3️⃣ ဝယ်ပြီးသား Frame အား မိမိကတ်တွင် တပ်ဆင်သည့်အပိုင်း (/frame apply CH1234 neon)
    elif sub_command == "apply" and len(parts) >= 3:
        char_id = parts[1].upper()
        frame_code = parts[2].lower()
        
        if frame_code not in AVAILABLE_FRAMES:
            return await event.reply("❌ ထို Frame Code မရှိပါဘူး။")
            
        frame_info = AVAILABLE_FRAMES[frame_code]
        user_data = await users_catcher_col.find_one({"user_id": user_id})
        if not user_data: return await event.reply("❌ သင့်အကောင့်ကို မတွေ့ပါဘူး။")
        
        # ဤ Frame ကို တကယ် ဝယ်ထားခြင်း ရှိမရှိ စစ်ဆေးခြင်း
        owned_frames = user_data.get("owned_frames", [])
        if frame_code not in owned_frames:
            return await event.reply(f"❌ သင်သည် ဤ Frame ကို ဝယ်ယူထားခြင်း မရှိသေးပါ။ အရင်ဆုံး <code>/frame buy {frame_code}</code> ဖြင့် ဝယ်ယူပါဦး။", parse_mode='html')
        
        # ကစားသမား၏ harem ထဲတွင် ထို ကတ် ID ရှိမရှိ စစ်ဆေးခြင်း
        harem = user_data.get("harem", [])
        card_exists = any(card["char_id"] == char_id for card in harem)
        if not card_exists:
            return await event.reply("❌ သင့်ရဲ့ စုဆောင်းမှု Vault (Harem) ထဲမှာ ဒီ Card ID မရှိပါဘူးဗျာ။")
            
        # MongoDB Array Dynamic Operator ($) ကိုသုံးပြီး harem ထဲက သက်ဆိုင်ရာ ကတ်ကောင်လေးမှာ frame သွားထည့်ခြင်း
        await users_catcher_col.update_one(
            {"user_id": user_id, "harem.char_id": char_id},
            {"$set": {"harem.$.frame": frame_code}}
        )
        return await event.reply(f"🎨 <b>COSMETIC EQUIPPED!</b>\nသင်၏ Card ID: <code>{char_id}</code> တွင် {frame_info['name']} ကို အောင်မြင်စွာ တပ်ဆင်လိုက်ပါပြီ။", parse_mode='html')
        
    else:
        return await event.reply("⚠️ <b>ကွန်မန်း ပုံစံမှားနေပါတယ် Boss!</b>\n🛒 ဝယ်ယူရန်: <code>/frame buy [code]</code>\n✨ ကတ်တွင်တပ်ရန်: <code>/frame apply [Card_ID] [code]</code>", parse_mode='html')

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
            f"⚡ <b>{f('MYSTERY HAI DETECTED / ဘယ်သူလေး ပေါ်လာတာလဲ...')} 🫣</b>\n"
            f"<blockquote><b>{f('Hey Hunters')}!</b> သူက ဘယ်သူဖြစ်မလဲ? သဲလွန်စတွေ ကြည့်ဖို့ ဒီပိုစ့်ကို <code>/who</code> နဲ့ အမြန်ဆုံး Reply ပြန်ပြီး စစ်ဆေးလိုက်ပါ! 👀🔥</blockquote>\n"
            f"If u still don't know how to use, Reply it to /who",
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

# ==========================================
# 💡 4. IDENTITY REVEAL ENGINE (/who)
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/who$'))
async def who_reveal_handler(event):
    if event.is_private: return
    chat_id = event.chat_id
    
    if chat_id not in active_group_spawns:
        return await event.reply(f"❌ <b>{f('လက်ရှိ Spawnတဲ့ Hai မရှိသေးပါဘူး သူငယ်ချင်း')}</b>", parse_mode='html')
        
    spawn_data = active_group_spawns[chat_id]
    if time.time() - spawn_data["spawn_time"] > 60:
        if chat_id in active_group_spawns: del active_group_spawns[chat_id]
        return await event.reply(f"⏱️ <b>{f('TARGET GHOSTED / နောက်မှထပ်ကောက်ပါတော့')}😅</b>", parse_mode='html')
        
    if not event.is_reply or event.reply_to_msg_id != spawn_data["spawn_msg_id"]:
        return await event.reply(f"⚠️ <b>{f('ပစ်မှတ်လွဲနေတယ်! ပေါ်လာတဲ့ ပိုစ့်ကို တိုက်ရိုက် Reply ပြန်ပေးပါ')}</b>", parse_mode='html')
        
    reveal_text = f"🌟 {spawn_data['rarity']}\n🌐 Domain: <code>{spawn_data['category']}</code>\n\n<code>/catch {spawn_data['name']}</code>"
    await event.reply(reveal_text, parse_mode='html')

# ==========================================
# 🎯 5. CLAIM ENGINE CORE (/catch) - WITH ATOMIC LOCKS & GUILD EXP
# ==========================================
@bot1.on(events.NewMessage(pattern=r'^/catch\s+(.*)$'))
async def catch_handler(event):
    if event.is_private: return
    chat_id = event.chat_id
    user_id = event.sender_id
    catch_name = event.pattern_match.group(1).strip()
    
    if chat_id not in active_group_spawns:
        return await event.reply(f"🛸 <b>{f('ဒီ Dimension မှာ ဖမ်းစရာ ဘယ်သူမှ မရှိတော့ဘူး')}</b>", parse_mode='html')
        
    spawn_data = active_group_spawns[chat_id]
    if spawn_data["claimed"]: return

    if time.time() - spawn_data["spawn_time"] > 180:
        if chat_id in active_group_spawns: del active_group_spawns[chat_id]
        return await event.reply(f"⏱️ <b>{f('TARGET GHOSTED / အချိန်ကုန်သွားလို့ ထွက်ပြေးသွားပြီ!')}😅</b>", parse_mode='html')

    if normalize_name(catch_name) != normalize_name(spawn_data["name"]): 
        return await event.reply(f"❌ <b>{f('နာမည်မှားနေတယ် Boss! သေချာပြန်စစ်ပြီး /catch')}</b>", parse_mode='html')

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
            
            # 3️⃣ Active ဖြစ်နေသော Spawn ဒေတာများအား Memory ထဲမှ ဖယ်ရှားခြင်း
            del active_group_spawns[chat_id]
            if chat_id in spawn_locks: del spawn_locks[chat_id]
            
            # Success Message (မူရင်းကုဒ်မှ Variable Name အမှားအား ဆရာကျကျ ပြင်ဆင်ပေးထားပါသည် 😎)
            success_msg = (
                f"🎯 <b>{f('CAPTURED SUCCESS / ဖမ်းယူမှု အောင်မြင်ခြင်း')} ✨</b>\n"
                f"👤 <b>{f('Hunter')}:</b> {mention}\n"
                f"🃏 <b>{f('Character')}:</b> <code>{escape_html(spawn_data['name'])}</code>\n"
                f"🆔 <b>{f('Asset ID')}:</b> <code>{spawn_data['char_id']}</code>\n"
                f"🌟 <b>{f('Rarity Class')}:</b> {spawn_data['rarity']}\n"
                f"🪙 <b>{f('Bounty Added')}:</b> <code>+{spawn_data['value']} MMK</code>\n"
                f"<blockquote><b>{f('Mission Secured')}!</b> ဒီ Character ကို သင့်ရဲ့ စုဆောင်းမှုထဲထည့်လိုက်ပြီ စစ်ဆေးရန် /hai လို့ရိုက်</blockquote>"
            )
            
            # အကယ်၍ Guild Level တက်ခဲ့ပါက ကတ်မိတဲ့စာသားအောက်မှာ တစ်ခါတည်း Message ချိတ်ပြပေးမည့်စနစ်
            if guild_levelup_msg:
                success_msg += guild_levelup_msg
                
            await send_safe_message(bot1, event.chat_id, success_msg, parse_mode='html', reply_to=event.id)
            
        except Exception as e:
            # Error တစ်ခုခုတက်လျှင် တခြားလူ ပြန်ဖမ်းလို့ရအောင် Claimed ကို False ပြန်ပြင်ပေးခြင်း
            active_group_spawns[chat_id]["claimed"] = False
            await event.reply(f"❌ <b>Catch Logic Fault:</b> <code>{e}</code>", parse_mode='html')

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
        f"🌌 <b>{f('BOD AGENT DOSSIER')}</b>\n"
        f"👤 <b>{f('Agent Identity')}:</b> {mention}\n"
        f"🦦 <b>{f('User Core ID')}:</b> <code>{user_id}</code>\n"
        f"🪙 <b>{f('Asset Liquid Capital')}:</b> <code>{balance} MMK</code>\n"
        f"🎒 <b>{f('Gross Captured Units')}:</b> <code>{total_caught} Units</code>\n"
        f"🛒 <b>{f('On Marketplace')}:</b> <code>{market_count} Cards</code>\n"
        f"⚡ ━━━━━━⚡\n"
    )
    if counts:
        profile_text += f"📊 <b>{f('VAULT QUANTUM SEGMENTS')}:</b>\n"
        for tier, qty in sorted(counts.items(), key=lambda x: x[1], reverse=True):
            profile_text += f" ├─➩ {tier}: <b>{qty} Cards</b>\n"
        profile_text += f"⚡ ━━━━━━ ⚡\n"
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
    
    anim_msg = await event.reply(
        f"🎰 {mention} <b>{f('SPINNING THE QUANTUM REELS')}... ⚡</b>\n\n<b>[ 🎰 | 🎰 | 🎰 | 🎰 | 🎰 | 🎰 | 🎰 ]</b>\n\n<i>Rerolling Matrix Clusters... ⏳</i>", parse_mode='html'
    )
    symbols = ["​🐯", "​🦁", "​🐃", "​🦓", "🦀", "🐄", "🦅"]
    await asyncio.sleep(0.6)
    mid_syms = random.choices(symbols, k=7)
    mid_str = " | ".join(mid_syms)
    await bot1.edit_message(
        event.chat_id, anim_msg.id, 
        f"🎰 {mention} <b>{f('REELS ARE LOCKING IN')}... 🔥</b>\n\n<b>[ {mid_str} ]</b>\n\n<i>Decelerating Matrix Engine... ⚡</i>", parse_mode='html'
    )
    await asyncio.sleep(0.9)
    res_syms = random.choices(symbols, k=7)
    res_str = " | ".join(res_syms)
    
    from collections import Counter
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
    await bot1.edit_message(event.chat_id, anim_msg.id, final_text, parse_mode='html')

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
        f"📌 <i>အနည်းဆုံး ၂ ယောက်ပြည့်လျှင် Host က <code>/startgame</code> (ထိလိုက်ရုံ​နဲ့COPYယူနိုင်) ဟုရိုက်၍ စတင်နိုင်ပါသည်။</i>"
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
    await event.respond(result_text, parse_mode='html')

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

@bot1.on(events.NewMessage(pattern=r'^/flip\s+(ခေါင်း|ပန်း)\s+(\d+)$'))
async def coin_flip_handler(event):
    user_id = event.sender_id
    choice = event.pattern_match.group(1)
    bet_amount = int(event.pattern_match.group(2))
    
    user_data = await users_catcher_col.find_one({"user_id": user_id})
    if not user_data or user_data.get("wallet_balance", 0) < bet_amount: return await event.reply("❌ <b>လောင်းကြေးမလောက်ပါဘူး Boss!</b>", parse_mode='html')
    
    animation_msg = await event.reply("🪙 <b>ဒင်္ဂါးပြားကို လေထဲမြှောက်လိုက်ပါပြီ...</b>\n[ 🔄 လည်နေသည် ]", parse_mode='html')
    await asyncio.sleep(3)
    
    result = random.choice(["ခေါင်း", "ပန်း"])
    mention = await get_html_mention(event, user_id)
    if choice == result:
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": bet_amount}})
        await bot1.edit_message(event.chat_id, animation_msg.id, f"🪙 {mention} <b>{f('COIN FLIP SUCCESS')}!!</b>\n✨ ရလဒ်က [ <b>{result}</b> ] ဖြစ်ပါတယ်!\n🎉 Boss မှန်သွားလို့ ပွဲနိုင်ပါတယ်။ <code>+{bet_amount} MMK</code>", parse_mode='html')
    else:
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": -bet_amount}})
        await bot1.edit_message(event.chat_id, animation_msg.id, f"🪙 {mention} <b>{f('COIN FLIP FAILED')}..</b>\n💨 ရလဒ်က [ <b>{result}</b> ] ဖြစ်ပါတယ်!\n💸 မှားသွားတဲ့အတွက် လောင်းကြေးရှုံးသွားပါပြီ။ <code>-{bet_amount} MMK</code>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/dice\s+(\d+)$'))
async def dice_game_handler(event):
    user_id = event.sender_id
    bet_amount = int(event.pattern_match.group(1))
    chat_id = event.chat_id

    user_data = await users_catcher_col.find_one({"user_id": user_id})
    if not user_data or user_data.get("wallet_balance", 0) < bet_amount: return await event.reply("❌ <b>လောင်းကြေးမလောက်ပါဘူး Boss!</b>", parse_mode='html')

    dice_msg = await bot1.send_message(chat_id, file=types.InputMediaDice(emoticon="🎲"))
    await asyncio.sleep(2)
    dice_value = dice_msg.media.value
    
    if dice_value >= 4:  
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": bet_amount}})
        await event.reply(f"🎉 <b>အမှတ် {dice_value} ကျလို့ နိုင်သွားပါပြီ!</b>\n🪙 Bounty Added: <code>+{bet_amount} MMK</code>", parse_mode='html')
    else: 
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": -bet_amount}})
        await event.reply(f"💸 <b>အမှတ် {dice_value} ပဲကျလို့ ရှုံးသွားပါပြီဗျာ!</b>\n🪙 Balance Deducted: <code>-{bet_amount} MMK</code>", parse_mode='html')

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
# 🔥 AI FULL POWER OPTIMIZATION: 12+ EXTRA AI FEATURES & COMMANDS MATRIX
# ==========================================

@bot1.on(events.NewMessage(pattern=r'^/daily(?:@\w+)?$'))
async def daily_bounty_handler(event):
    """[CMD 3] Cooldown Engine ပါဝင်သော နေ့စဉ် Rewards စနစ် (Dynamic Streak Multiplier)"""
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
    await event.reply(f"🎁 {mention} <b>{f('နေ့စဉ်ထောက်ပံ့ကြေး')} <code>+{bonus} MMK</code> {f('ကို သင့် Vault ထဲသို့ ထည့်သွင်းပေးလိုက်ပါပြီ Boss!')}</b>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/richest(?:@\w+)?$'))
async def system_richest_leaderboard(event):
    """[CMD 4] BODကာစီနိုလောကအတွင်း ငွေအများဆုံး ပိုင်ဆိုင်ထားသော သူဌေး 10 ဦး စာရင်း"""
    cursor = users_catcher_col.find({"wallet_balance": {"$gt": 0}}).sort("wallet_balance", -1).limit(10)
    richest_users = await cursor.to_list(length=10)
    
    if not richest_users: return await event.reply(f"🏆 <b>{f('Leaderboard matrix data clear ဖြစ်နေပါတယ် Bro!')}</b>", parse_mode='html')
        
    msg = f"💰 <b>{f('BODကာစီနိုနဲ့Cardဖမ်းလောကရဲ့ - TOP 10 RICHEST players')}</b>\n⚡ ━━━━━⚡\n"
    for i, u in enumerate(richest_users):
        bal = u.get("wallet_balance", 0)
        user_display = u.get('fullname') or f"User {u['user_id']}"
        msg += f"<b>{i+1}.</b> {user_display} — <code>{bal:,} MMK</code> 🪙\n"
    msg += f"\nFamily Cluster: <b>Brotherhood of Dexter</b>"
    await event.reply(msg, parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/scrap\s+([a-zA-Z0-9_]+)$'))
async def scrap_card_handler(event):
    """[CMD 5] မလိုချင်သော ပိုနေသည့်ကတ်များအား ဖျက်စီးပြီး ငွေသားအဖြစ် ပြန်လည်လဲလှယ်ခြင်း (Melt Engine)"""
    user_id = event.sender_id
    char_id = event.pattern_match.group(1).upper()
    mention = await get_html_mention(event, user_id)
    
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    user_harem = user_doc.get("harem", []) if user_doc else []
    
    char_item = next((x for x in user_harem if isinstance(x, dict) and x.get("char_id") == char_id and x.get("status") != "market"), None)
    if not char_item: return await event.reply("❌ <b>{f('သင့် Vault ထဲမှာ အားလပ်နေတဲ့ ဒီကတ် မရှိပါဘူး Bro!')}</b>", parse_mode='html')
    
    char_data = await characters_base_col.find_one({"char_id": char_id})
    if not char_data: return await event.reply("❌ <b>{f('Character ID database ထဲမှာ ရှာမတွေ့ပါ!')}</b>", parse_mode='html')
    
    # ရောင်းတန်ဖိုး၏ 50% အား သတ်မှတ်ချက်အတိုင်း ပြန်အမ်းပေးခြင်း
    melt_value = int(char_data.get("currency_value", 100) * 0.5)
    
    user_harem.remove(char_item)
    await users_catcher_col.update_one(
        {"user_id": user_id},
        {"$set": {"harem": user_harem}, "$inc": {"wallet_balance": melt_value}}
    )
    await event.reply(f"♻️ {mention} <b>{f('မှ ကတ်ပြား')} <code>{char_data['name']}</code> {f('အား ဖျက်စီးပြီး စနစ်ထံ ရောင်းချခဲ့သည်။')} (+<code>{melt_value} MMK</code>)</b>", parse_mode='html')

@bot1.on(events.NewMessage(pattern=r'^/gamble\s+(\d+)$'))
async def high_risk_gamble_handler(event):
    """[CMD 6] Double or Nothing စနစ်သုံး ရင်ခုန်စရာ အမြန်လောင်းကစားနည်း"""
    user_id = event.sender_id
    bet = int(event.pattern_match.group(1))
    mention = await get_html_mention(event, user_id)
    
    if bet < 50: return await event.reply("❌ <b>အနည်းဆုံး လောင်းကြေး 50 MMK ရှိရပါမည် Bro!</b>", parse_mode='html')
    user_doc = await users_catcher_col.find_one({"user_id": user_id})
    balance = user_doc.get("wallet_balance", 0) if user_doc else 0
    if balance < bet: return await event.reply(f"❌ <b>သင့်ထံတွင် ငွေမလုံလောက်ပါ! လက်ကျန်: {balance} MMK</b>", parse_mode='html')
    
    if random.choice([True, False]):
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": bet}})
        await event.reply(f"🔥 {mention} <b>{f('RISK VENTURE SUCCESS')}! အနိုင်ရရှိသဖြင့် လောင်းကြေးနှစ်ဆ ရရှိသည်။ (+<code>{bet} MMK</code>)</b>", parse_mode='html')
    else:
        await users_catcher_col.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": -bet}})
        await event.reply(f"💸 {mention} <b>{f('RISK VENTURE FAILED')}! ကံမကောင်းစွာဖြင့် လောင်းကြေးအားလုံး ဆုံးရှုံးသွားသည်။ (-<code>{bet} MMK</code>)</b>", parse_mode='html')

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
        f"📊 <b>{f('SOVEREIGN GLOBAL ECONOMY INFRASTRUCTURE')}</b>\n⚡ ━━━━━━━ ⚡\n"
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
# 🚀 BULLETPROOF AUTO-RECONNECT ENGINE & SYSTEM LAUNCHER
# ====================================================================================
async def start_sovereign_system():
    # 🌐 Flask Web Server ကို Background Thread မှာ အရင်တင်ခြင်း
    threading.Thread(target=run_flask, daemon=True).start()
    print("🌐 Keep-Alive Flask Server initialized on Port 10000 successfully!")

    # 🔄 Telegram Bot Connection စိတ်ချရအောင် ပတ်မည့် Infinite Loop
    while True:
        try:
            print("🚀 Telethon Bot Client စတင်ချိတ်ဆက်နေပါသည်...")
            await bot1.start(bot_token=MAIN_BOT_TOKEN)
            
            me = await bot1.get_me()
            print(f"✅ Bot Telegram နှင့် အောင်မြင်စွာ ချိတ်ဆက်မိပါပြီ- @{me.username}")
            
            # ⏰ Background Cronjobs များကို Loop ထဲမှာ စနစ်တကျ စတင်ခြင်း
            asyncio.create_task(group_reminder_scheduler())
            print("📅 Background Reminder Scheduler Grid: LOCKED & ACTIVE! ✔️")
            
            # Bot ကို လိုင်းမပြတ်တမ်း အမြဲစောင့်ကြည့်ခိုင်းခြင်း
            await bot1.run_until_disconnected()
            
        except Exception as system_fault:
            print(f"⚠️ Bot Network ပြုတ်ခြင်း သို့မဟုတ် Error တက်သွားခြင်း: {system_fault}")
            print("⏳ စက္ကန့် ၃၀ အကြာတွင် စနစ်ကို Auto-Restart ပြန်လည်ချိတ်ဆက်ပါမည်...")
            await asyncio.sleep(30) # စက္ကန့် ၃၀ စောင့်ပြီး အပေါ်က loop အတိုင်း ပြန်စပတ်မယ်

if __name__ == "__main__":
    try:
        asyncio.run(start_sovereign_system())
    except KeyboardInterrupt:
        print("Bot Engine Stopped Manually.")
