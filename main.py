import os
import logging
import psycopg2
import random
import asyncio
import uuid
import string
import uvicorn
from datetime import datetime, date, timedelta
from contextlib import asynccontextmanager
import pytz

# Web Server
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Telegram
from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup, 
    WebAppInfo, 
    InputMediaPhoto, 
    InputMediaVideo
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)
from telegram.error import BadRequest

# ==============================================================================
# 配置区域
# ==============================================================================
CONFIG = {
    "GROUP_LINK": "https://t.me/+495j5rWmApsxYzg9",
    "START_VIP_INFO": "AgACAgEAAxkBAAIC...", 
    "START_TUTORIAL": "AgACAgEAAxkBAAIC...",
    "WX_PAY_QR": "AgACAgEAAxkBAAIC...",
    "WX_ORDER_TUTORIAL": "AgACAgEAAxkBAAIC...",
    "ALI_PAY_QR": "AgACAgEAAxkBAAIC...",
    "ALI_ORDER_TUTORIAL": "AgACAgEAAxkBAAIC...",
}

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
raw_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
RAILWAY_DOMAIN = raw_domain.replace("https://", "").replace("http://", "").strip("/")
DIRECT_LINK_1 = "https://otieu.com/4/10489994"
DIRECT_LINK_2 = "https://otieu.com/4/10489998"

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

tz_bj = pytz.timezone('Asia/Shanghai')
scheduler = AsyncIOScheduler(timezone=tz_bj)
bot_app = None

# States
WAITING_FOR_PHOTO = 1
WAITING_LINK_1 = 2; WAITING_LINK_2 = 3; WAITING_LINK_3 = 4; WAITING_LINK_4 = 5; WAITING_LINK_5 = 6; WAITING_LINK_6 = 7; WAITING_LINK_7 = 8
WAITING_CMD_NAME = 30; WAITING_CMD_CONTENT = 31
WAITING_PROD_NAME = 40; WAITING_PROD_PRICE = 41; WAITING_PROD_CONTENT = 42
WAITING_START_ORDER = 10; WAITING_VIP_ORDER = 20; WAITING_RECHARGE_ORDER = 25

# ==============================================================================
# 数据库初始化
# ==============================================================================

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # V3/V4/V5/V7 完整表结构
    cur.execute("CREATE TABLE IF NOT EXISTS file_ids_v3 (id SERIAL PRIMARY KEY, file_id TEXT, file_unique_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users_v3 (
            user_id BIGINT PRIMARY KEY,
            points INTEGER DEFAULT 0,
            last_checkin_date DATE,
            checkin_count INTEGER DEFAULT 0,
            verify_fails INTEGER DEFAULT 0, verify_lock TIMESTAMP, verify_done BOOLEAN DEFAULT FALSE,
            wx_fails INTEGER DEFAULT 0, wx_lock TIMESTAMP, wx_done BOOLEAN DEFAULT FALSE,
            ali_fails INTEGER DEFAULT 0, ali_lock TIMESTAMP, ali_done BOOLEAN DEFAULT FALSE,
            username TEXT,
            vip_expire TIMESTAMP, daily_free_count INTEGER DEFAULT 0, last_free_date DATE,
            vip_buy_fails INTEGER DEFAULT 0, vip_buy_lock TIMESTAMP, verify_unlock_date DATE
        );
    """)
    # 补全字段
    cols = [
        "verify_fails INT DEFAULT 0", "verify_lock TIMESTAMP", "verify_done BOOLEAN DEFAULT FALSE",
        "wx_fails INT DEFAULT 0", "wx_lock TIMESTAMP", "wx_done BOOLEAN DEFAULT FALSE",
        "ali_fails INT DEFAULT 0", "ali_lock TIMESTAMP", "ali_done BOOLEAN DEFAULT FALSE",
        "vip_expire TIMESTAMP", "daily_free_count INT DEFAULT 0", "last_free_date DATE",
        "vip_buy_fails INT DEFAULT 0", "vip_buy_lock TIMESTAMP", "verify_unlock_date DATE",
        "username TEXT"
    ]
    for c in cols:
        try: cur.execute(f"ALTER TABLE users_v3 ADD COLUMN IF NOT EXISTS {c};")
        except: conn.rollback()

    cur.execute("CREATE TABLE IF NOT EXISTS user_ads_v3 (user_id BIGINT PRIMARY KEY, last_watch_date DATE, daily_watch_count INT DEFAULT 0);")
    cur.execute("CREATE TABLE IF NOT EXISTS ad_tokens_v3 (token TEXT PRIMARY KEY, user_id BIGINT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS system_keys_v7 (
            id INTEGER PRIMARY KEY,
            key_1 TEXT, link_1 TEXT, key_2 TEXT, link_2 TEXT,
            key_3 TEXT, link_3 TEXT, key_4 TEXT, link_4 TEXT,
            key_5 TEXT, link_5 TEXT, key_6 TEXT, link_6 TEXT,
            key_7 TEXT, link_7 TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("INSERT INTO system_keys_v7 (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
    cur.execute("CREATE TABLE IF NOT EXISTS user_used_keys_v7 (id SERIAL PRIMARY KEY, user_id BIGINT NOT NULL, key_index INTEGER NOT NULL, used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id, key_index));")
    
    cur.execute("CREATE TABLE IF NOT EXISTS user_key_clicks_v3 (user_id BIGINT PRIMARY KEY, click_count INT DEFAULT 0, session_date DATE);")
    cur.execute("CREATE TABLE IF NOT EXISTS user_key_claims_v3 (id SERIAL PRIMARY KEY, user_id BIGINT, key_val TEXT, claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id, key_val));")

    cur.execute("CREATE TABLE IF NOT EXISTS custom_commands_v4 (id SERIAL PRIMARY KEY, command_name TEXT UNIQUE NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")
    cur.execute("CREATE TABLE IF NOT EXISTS command_contents_v4 (id SERIAL PRIMARY KEY, command_id INT REFERENCES custom_commands_v4(id) ON DELETE CASCADE, file_id TEXT, file_type TEXT, caption TEXT, message_text TEXT, sort_order SERIAL);")

    cur.execute("CREATE TABLE IF NOT EXISTS products_v5 (id SERIAL PRIMARY KEY, name TEXT NOT NULL, price INTEGER NOT NULL, content_text TEXT, content_file_id TEXT, content_type TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")
    cur.execute("CREATE TABLE IF NOT EXISTS user_purchases_v5 (id SERIAL PRIMARY KEY, user_id BIGINT NOT NULL, product_id INTEGER REFERENCES products_v5(id) ON DELETE CASCADE, purchase_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id, product_id));")
    cur.execute("CREATE TABLE IF NOT EXISTS point_logs_v5 (id SERIAL PRIMARY KEY, user_id BIGINT NOT NULL, change_amount INTEGER NOT NULL, reason TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")

    conn.commit()
    cur.close()
    conn.close()
    # ==============================================================================
# 业务逻辑函数 (Database Functions)
# ==============================================================================

def get_session_date():
    now = datetime.now(tz_bj)
    if now.hour < 10:
        return (now - timedelta(days=1)).date()
    return now.date()

def generate_random_key():
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(8))

def get_file_id(key):
    fid = CONFIG.get(key)
    return fid if fid and fid.startswith("AgAC") else None

def get_group_link():
    return CONFIG.get("GROUP_LINK", "https://t.me/+495j5rWmApsxYzg9")

def ensure_user_exists(user_id, username=None):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO users_v3 (user_id, username) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username", (user_id, username))
    cur.execute("INSERT INTO user_ads_v3 (user_id, daily_watch_count) VALUES (%s, 0) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    conn.commit()
    cur.close()
    conn.close()

# --- 积分 ---
def update_points(user_id, amount, reason):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users_v3 SET points = points + %s WHERE user_id = %s RETURNING points", (amount, user_id))
    new_total = cur.fetchone()[0]
    cur.execute("INSERT INTO point_logs_v5 (user_id, change_amount, reason) VALUES (%s, %s, %s)", (user_id, amount, reason))
    conn.commit()
    cur.close()
    conn.close()
    return new_total

def get_user_data(user_id):
    ensure_user_exists(user_id)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT points, last_checkin_date, checkin_count, vip_expire, daily_free_count, last_free_date, verify_done, verify_unlock_date FROM users_v3 WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row

def get_point_logs(user_id, limit=5):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT change_amount, reason, created_at FROM point_logs_v5 WHERE user_id = %s ORDER BY id DESC LIMIT %s", (user_id, limit))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def process_checkin(user_id):
    ensure_user_exists(user_id)
    conn = get_db_connection()
    cur = conn.cursor()
    today = datetime.now(tz_bj).date()
    cur.execute("SELECT last_checkin_date, checkin_count FROM users_v3 WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    if row[0] == today:
        cur.close(); conn.close()
        return {"status": "already_checked"}
    pts = 10 if row[1] == 0 else random.randint(3, 8)
    cur.execute("UPDATE users_v3 SET points=points+%s, last_checkin_date=%s, checkin_count=checkin_count+1 WHERE user_id=%s RETURNING points", (pts, today, user_id))
    total = cur.fetchone()[0]
    cur.execute("INSERT INTO point_logs_v5 (user_id, change_amount, reason) VALUES (%s, %s, '每日签到')", (user_id, pts))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "success", "added": pts, "total": total}

# --- 验证/锁 ---
def check_lock(user_id, type_prefix):
    ensure_user_exists(user_id)
    conn = get_db_connection()
    cur = conn.cursor()
    fields = f"{type_prefix}_fails, {type_prefix}_lock"
    if type_prefix == 'verify': fields += ", verify_done"
    cur.execute(f"SELECT {fields} FROM users_v3 WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        done = row[2] if len(row) > 2 else False
        return row[0], row[1], done
    return 0, None, False

def update_fail(user_id, type_prefix, current_fails, lock_minutes):
    conn = get_db_connection()
    cur = conn.cursor()
    new_fails = current_fails + 1
    if new_fails >= 2:
        lock_until = datetime.now() + timedelta(minutes=lock_minutes)
        cur.execute(f"UPDATE users_v3 SET {type_prefix}_fails = %s, {type_prefix}_lock = %s WHERE user_id = %s", (new_fails, lock_until, user_id))
    else:
        cur.execute(f"UPDATE users_v3 SET {type_prefix}_fails = %s WHERE user_id = %s", (new_fails, user_id))
    conn.commit()
    cur.close()
    conn.close()
    return new_fails

def mark_success(user_id, type_prefix):
    conn = get_db_connection()
    cur = conn.cursor()
    sql = f"UPDATE users_v3 SET {type_prefix}_fails=0, {type_prefix}_lock=NULL"
    if type_prefix == 'verify': sql += ", verify_done=TRUE"
    cur.execute(sql + " WHERE user_id=%s", (user_id,))
    conn.commit()
    cur.close()
    conn.close()

# --- VIP ---
def activate_vip(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    expire = datetime(2099, 1, 1)
    cur.execute("UPDATE users_v3 SET vip_expire=%s, vip_buy_fails=0, vip_buy_lock=NULL WHERE user_id=%s", (expire, user_id))
    conn.commit()
    cur.close()
    conn.close()

def is_vip(user_id):
    ensure_user_exists(user_id)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT vip_expire FROM users_v3 WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row and row[0] and row[0] > datetime.now(): return True, row[0]
    return False, None

# --- 七星密钥 V7 ---
def refresh_system_keys_v7():
    keys = [generate_random_key() for _ in range(7)]
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE system_keys_v7 SET key_1=%s, link_1=NULL, key_2=%s, link_2=NULL, key_3=%s, link_3=NULL, key_4=%s, link_4=NULL, key_5=%s, link_5=NULL, key_6=%s, link_6=NULL, key_7=%s, link_7=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=1", tuple(keys))
    cur.execute("TRUNCATE TABLE user_used_keys_v7")
    conn.commit()
    cur.close()
    conn.close()
    return keys

def get_system_keys_v7():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM system_keys_v7 WHERE id=1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row

def update_key_link_v7(index, link):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(f"UPDATE system_keys_v7 SET link_{index}=%s WHERE id=1", (link,))
    conn.commit()
    cur.close()
    conn.close()

def check_key_valid(user_id, input_key):
    row = get_system_keys_v7()
    if not row: return False, None
    found_idx = -1
    for i in range(1, 8):
        if row[(i-1)*2 + 1] == input_key.strip():
            found_idx = i
            break
    if found_idx == -1: return False, "invalid"
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM user_used_keys_v7 WHERE user_id=%s AND key_index=%s", (user_id, found_idx))
    if cur.fetchone(): cur.close(); conn.close(); return False, "used"
    cur.execute("INSERT INTO user_used_keys_v7 (user_id, key_index) VALUES (%s, %s)", (user_id, found_idx))
    cur.execute("UPDATE users_v3 SET verify_unlock_date=%s WHERE user_id=%s", (datetime.now(tz_bj).date(), user_id))
    conn.commit()
    cur.close()
    conn.close()
    return True, "success"

def is_exchange_unlocked(user_id):
    is_v, _ = is_vip(user_id)
    if is_v: return True
    ensure_user_exists(user_id)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT verify_unlock_date FROM users_v3 WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row and row[0] == datetime.now(tz_bj).date()

# --- 商品 & 转发 ---
def get_products_list(limit, offset):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name, price FROM products_v5 ORDER BY id DESC LIMIT %s OFFSET %s", (limit, offset))
    rs = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM products_v5")
    t = cur.fetchone()[0]
    cur.close()
    conn.close()
    return rs, t

def get_product_details(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name, price, content_text, content_file_id, content_type FROM products_v5 WHERE id=%s", (pid,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row

def check_purchase(uid, pid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM user_purchases_v5 WHERE user_id=%s AND product_id=%s", (uid, pid))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return True if row else False

def record_purchase(uid, pid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO user_purchases_v5 (user_id, product_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (uid, pid))
    conn.commit()
    cur.close()
    conn.close()

def add_product(name, price, text, fid, ftype):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO products_v5 (name, price, content_text, content_file_id, content_type) VALUES (%s, %s, %s, %s, %s)", (name, price, text, fid, ftype))
    conn.commit()
    cur.close()
    conn.close()

def delete_product(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM products_v5 WHERE id=%s", (pid,))
    conn.commit()
    cur.close()
    conn.close()

def check_daily_free(user_id):
    ensure_user_exists(user_id)
    conn = get_db_connection()
    cur = conn.cursor()
    today = datetime.now(tz_bj).date()
    cur.execute("SELECT daily_free_count, last_free_date FROM users_v3 WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    count = row[0]
    if row[1] != today: count = 0
    cur.close()
    conn.close()
    return count, count < 5

def use_free_chance(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    today = datetime.now(tz_bj).date()
    cur.execute("SELECT daily_free_count, last_free_date FROM users_v3 WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    count = row[0]
    if row[1] != today: count = 0
    cur.execute("UPDATE users_v3 SET daily_free_count=%s, last_free_date=%s WHERE user_id=%s", (count+1, today, user_id))
    conn.commit()
    cur.close()
    conn.close()

def get_all_users_info(l, o):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, points, vip_expire FROM users_v3 ORDER BY points DESC LIMIT %s OFFSET %s", (l, o))
    rs = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM users_v3")
    t = cur.fetchone()[0]
    cur.close()
    conn.close()
    return rs, t

def save_file_id(fid, fuid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO file_ids_v3 (file_id, file_unique_id) VALUES (%s, %s)", (fid, fuid))
    conn.commit()
    cur.close()
    conn.close()

def get_all_files():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, file_id FROM file_ids_v3 ORDER BY id DESC LIMIT 10")
    rs = cur.fetchall()
    cur.close()
    conn.close()
    return rs

def delete_file_by_id(did):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM file_ids_v3 WHERE id=%s", (did,))
    conn.commit()
    cur.close()
    conn.close()

def add_custom_command(cmd):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO custom_commands_v4 (command_name) VALUES (%s) RETURNING id", (cmd,))
        cid = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return cid
    except:
        conn.rollback()
        cur.close()
        conn.close()
        return None

def add_command_content(cid, fid, ftype, cap, txt):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO command_contents_v4 (command_id,file_id,file_type,caption,message_text) VALUES (%s,%s,%s,%s,%s)", (cid, fid, ftype, cap, txt))
    conn.commit()
    cur.close()
    conn.close()

def get_commands_list(limit, offset):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, command_name FROM custom_commands_v4 ORDER BY id DESC LIMIT %s OFFSET %s", (limit, offset))
    rs = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM custom_commands_v4")
    t = cur.fetchone()[0]
    cur.close()
    conn.close()
    return rs, t

def delete_command_by_id(cid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM custom_commands_v4 WHERE id=%s", (cid,))
    conn.commit()
    cur.close()
    conn.close()

def get_command_content(cmd):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT c.id, c.file_id, c.file_type, c.caption, c.message_text FROM command_contents_v4 c JOIN custom_commands_v4 cmd ON c.command_id=cmd.id WHERE cmd.command_name=%s ORDER BY c.sort_order", (cmd,))
    rs = cur.fetchall()
    cur.close()
    conn.close()
    return rs

def reset_admin_stats(aid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE user_ads_v3 SET daily_watch_count=0 WHERE user_id=%s", (aid,))
    cur.execute("DELETE FROM user_key_claims_v3 WHERE user_id=%s", (aid,))
    cur.execute("DELETE FROM user_purchases_v5 WHERE user_id=%s", (aid,))
    cur.execute("DELETE FROM user_used_keys_v7 WHERE user_id=%s", (aid,))
    cur.execute("UPDATE users_v3 SET verify_fails=0,verify_lock=NULL,verify_done=FALSE,wx_fails=0,wx_lock=NULL,wx_done=FALSE,ali_fails=0,ali_lock=NULL,ali_done=FALSE,vip_expire=NULL,daily_free_count=0,vip_buy_fails=0,vip_buy_lock=NULL,verify_unlock_date=NULL WHERE user_id=%s", (aid,))
    conn.commit()
    cur.close()
    conn.close()

def get_ad_status(uid):
    ensure_user_exists(uid)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT daily_watch_count FROM user_ads_v3 WHERE user_id=%s", (uid,))
    row = cur.fetchone()
    cnt = row[0] if row else 0
    cur.close()
    conn.close()
    return cnt

def get_user_click_status(uid):
    s = get_session_date()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT click_count, session_date FROM user_key_clicks_v3 WHERE user_id=%s", (uid,))
    row = cur.fetchone()
    if not row or row[1] != s:
        cur.execute("INSERT INTO user_key_clicks_v3 (user_id,click_count,session_date) VALUES (%s,0,%s) ON CONFLICT(user_id) DO UPDATE SET click_count=0,session_date=%s", (uid, s, s))
        conn.commit()
        cur.close()
        conn.close()
        return 0
    cur.close()
    conn.close()
    return row[0]

def increment_user_click(uid):
    s = get_session_date()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE user_key_clicks_v3 SET click_count=click_count+1 WHERE user_id=%s AND session_date=%s", (uid, s))
    conn.commit()
    cur.close()
    conn.close()
    # ==============================================================================
# 定时任务 (必须在 Handlers 之前定义)
# ==============================================================================

async def daily_reset_task():
    """每日0点重置任务 (保留接口)"""
    pass

async def weekly_reset_task():
    """每周一重置7个密钥"""
    keys = refresh_system_keys_v7()
    msg = "🔔 **每周密钥重置提醒**\n\n已生成新密钥并清空链接。\n请使用 `/my` 重新绑定。"
    if bot_app and ADMIN_ID:
        try:
            await bot_app.bot.send_message(ADMIN_ID, msg, parse_mode='Markdown')
        except:
            pass

async def delete_messages_task(chat_id, message_ids):
    """5分钟后自动删除消息"""
    try:
        await asyncio.sleep(300) # 5分钟
        for msg_id in message_ids:
            try:
                await bot_app.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except:
                pass
        
        text = "⏳ **消息存在时间有限，已自动销毁。**\n\n请到购买处重新获取（已购买不需要二次付费）。"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎁 前往兑换中心", callback_data="go_exchange")],
            [InlineKeyboardButton("🏠 返回首页", callback_data="back_to_home")]
        ])
        await bot_app.bot.send_message(chat_id=chat_id, text=text, reply_markup=kb, parse_mode='Markdown')
    except:
        pass

# ==============================================================================
# Telegram Handlers (核心交互)
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_exists(user.id, user.username)
    
    fails, lock_until, is_done = check_lock(user.id, 'verify')
    
    verify_text = "🚀 开始验证"
    verify_cb = "start_verify_flow"
    
    if is_done:
        verify_text = "✅ 已加入会员群"
        verify_cb = "noop_verify_done"
    elif lock_until and datetime.now() < lock_until:
        rem = lock_until - datetime.now()
        h, m = int(rem.seconds // 3600), int((rem.seconds % 3600) // 60)
        verify_text = f"🚫 验证锁定 ({h}h{m}m)"
        verify_cb = "locked_verify"

    text = "👋 欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~\n\n📢 小卫小卫，守门员小卫！\n一键入群，小卫帮你搞定！\n新人来报到，小卫查身份！"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(verify_text, callback_data=verify_cb)],
        [InlineKeyboardButton("💰 积分中心", callback_data="my_points")],
        [InlineKeyboardButton("🎉 开业活动", callback_data="open_activity")]
    ])
    
    if update.callback_query:
        if update.callback_query.data == "locked_verify":
            await update.callback_query.answer("⛔️ 请稍后再试。", show_alert=True)
            return
        if update.callback_query.data == "noop_verify_done":
            await update.callback_query.answer("✅ 您已完成验证，无需重复。", show_alert=True)
            return
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    else:
        await update.message.reply_text(text, reply_markup=kb)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """通用取消命令 /c"""
    context.user_data.clear()
    await update.message.reply_text("✅ 当前操作已取消，返回首页。")
    await start(update, context)
    return ConversationHandler.END

async def jf_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = get_user_data(user.id)
    # data: points, checkin, count, vip_expire ...
    
    is_v, expire_time = is_vip(user.id)
    vip_status = f"👑 会员状态：**已开通** (至 {expire_time.strftime('%Y-%m-%d')})" if is_v else "💀 会员状态：未开通"
    
    # 购买月卡按钮状态
    _, v_lock, _ = check_lock(user.id, 'vip_buy')
    if is_v:
        vip_btn_text = "✅ 你已购买"
        vip_btn_cb = "noop_vip_bought"
    elif v_lock and datetime.now() < v_lock:
        vip_btn_text = "🚫 购买冷却中"
        vip_btn_cb = "noop_vip_lock"
    else:
        vip_btn_text = "💎 购买月卡 (终身)"
        vip_btn_cb = "buy_vip_card"

    text = f"💰 **积分中心**\n\n👤 用户：{user.first_name} (`{user.id}`)\n{vip_status}\n💰 积分余额：`{data[0]}`"
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 每日签到", callback_data="do_checkin")],
        [InlineKeyboardButton("🎁 兑换中心", callback_data="go_exchange")],
        [InlineKeyboardButton("🔑 获取密钥 (7密钥)", callback_data="get_quark_key_v7")],
        [InlineKeyboardButton(vip_btn_text, callback_data=vip_btn_cb)],
        [InlineKeyboardButton("📜 余额记录", callback_data="view_balance")]
    ])
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode='Markdown')

async def view_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    data = get_user_data(uid)
    logs = get_point_logs(uid, 10)
    
    log_text = ""
    if logs:
        for l in logs:
            log_text += f"• {l[2].strftime('%m-%d %H:%M')} | {int(l[0]):+d} | {l[1]}\n"
    else:
        log_text = "暂无记录"
        
    text = f"💳 **账户余额**\n\n💎 总积分：`{data[0]}`\n\n📝 **最近记录：**\n{log_text}"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="my_points")]]), parse_mode='Markdown')

async def recharge_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    
    _, wx_l, wx_d = check_lock(uid, 'wx')
    _, ali_l, ali_d = check_lock(uid, 'ali')
    
    if wx_d:
        wx_t, wx_c = "✅ 微信已充", "noop_done"
    elif wx_l and datetime.now() < wx_l:
        wx_t, wx_c = "🚫 3小时冷却", "noop_lock"
    else:
        wx_t, wx_c = "💚 微信充值", "pay_wx"
        
    if ali_d:
        ali_t, ali_c = "✅ 支付宝已充", "noop_done"
    elif ali_l and datetime.now() < ali_l:
        ali_t, ali_c = "🚫 3小时冷却", "noop_lock"
    else:
        ali_t, ali_c = "💙 支付宝充值", "pay_ali"
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(wx_t, callback_data=wx_c), InlineKeyboardButton(ali_t, callback_data=ali_c)],
        [InlineKeyboardButton("🔙 返回", callback_data="my_points")]
    ])
    await query.edit_message_text("💎 **充值中心**\n每种方式限充 1 次。", reply_markup=kb, parse_mode='Markdown')

async def noop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if "vip_bought" in query.data:
        await query.answer("✅ 您已是尊贵的终身会员，无需重复购买！", show_alert=True)
    elif "vip_lock" in query.data:
        await query.answer("⛔️ 购买尝试次数过多，请 10 分钟后再试。", show_alert=True)
    elif "done" in query.data:
        await query.answer("✅ 已完成", show_alert=True)
    elif "empty" in query.data:
        await query.answer("⚠️ 此位置暂无链接，请尝试其他按钮。", show_alert=True)
    else:
        await query.answer("⛔️ 暂时锁定", show_alert=True)

async def checkin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    res = process_checkin(update.effective_user.id)
    if res["status"] == "already_checked":
        await query.answer("⚠️ 今日已签到", show_alert=True)
    else:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_to_home")]])
        await query.edit_message_text(f"🎉 **签到成功！** +{res['added']}分", reply_markup=kb, parse_mode='Markdown')

async def activity_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_exists(user.id)
    count = get_ad_status(user.id)
    t = create_ad_token(user.id)
    
    w_url = f"https://{RAILWAY_DOMAIN}/watch_ad/{t}"
    test_url = f"https://{RAILWAY_DOMAIN}/test_page"
    
    text = (
        "🎉 **开业活动中心**\n\n"
        "📺 **视频任务**：观看 15 秒广告，每日 3 次，积分随机。\n"
        "🔑 **密钥任务**：已移至积分中心，支持 7 组密钥轮换！"
    )
    
    kb = []
    if count < 3:
        kb.append([InlineKeyboardButton(f"📺 去看视频 ({count}/3)", url=w_url)])
    else:
        kb.append([InlineKeyboardButton("✅ 视频已完成 (3/3)", callback_data="noop_done")])
        
    kb.append([InlineKeyboardButton("🛠 测试按钮", url=test_url)])
    kb.append([InlineKeyboardButton("🔙 返回", callback_data="back_to_home")])
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def quark_key_btn_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    info = get_system_keys_v7()
    
    if not info:
        await query.message.reply_text("⏳ 初始化中...")
        return
        
    # V7逻辑：这里不再发放单个密钥，而是跳转到7密钥选择界面
    # 此函数仅为了兼容旧按钮
    await get_quark_key_entry(update, context)

async def cz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID):
        return
    reset_admin_stats(update.effective_user.id)
    await update.message.reply_text("✅ 测试数据已重置 (含VIP状态)")
    await start(update, context)

# --- 验证流程 Handlers ---

async def verify_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    fid = get_file_id("START_VIP_INFO")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ 我已付款，开始验证", callback_data="paid_start")]])
    text = "💎 **VIP会员特权说明：**\n✅ 专属中转通道\n✅ 优先审核入群\n✅ 7x24小时客服支持\n✅ 定期福利活动"
    
    if fid:
        try:
            await query.message.reply_photo(fid, caption=text, reply_markup=kb, parse_mode='Markdown')
            await query.delete_message()
        except:
            await query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
    else:
        await query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
    return WAITING_START_ORDER

async def ask_start_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    fid = get_file_id("START_TUTORIAL")
    text = "📝 **查找订单号教程：**\n请在支付账单中找到【订单号】。\n👇 **请在下方直接回复您的订单号：**"
    
    if fid:
        try:
            await query.message.reply_photo(fid, caption=text, parse_mode='Markdown')
        except:
            await query.message.reply_text(text, parse_mode='Markdown')
    else:
        await query.message.reply_text(text, parse_mode='Markdown')
    return WAITING_START_ORDER

async def check_start_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    txt = update.message.text.strip()
    
    if txt.startswith("20260"):
        mark_success(user_id, 'verify')
        gl = get_group_link()
        await update.message.reply_text("✅ **验证成功！**\n您已成功加入会员群，无需重复验证。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👉 点击加入会员群", url=gl)]]), parse_mode='Markdown')
        await asyncio.sleep(2)
        await start(update, context)
        return ConversationHandler.END
    else:
        fails, _, _ = check_lock(user_id, 'verify')
        new_fails = update_fail(user_id, 'verify', fails, 3 * 60)
        
        if new_fails >= 2:
            await update.message.reply_text("❌ **验证失败 (2/2)**\n⚠️ 已锁定 3 小时。", parse_mode='Markdown')
            await start(update, context)
            return ConversationHandler.END
        else:
            await update.message.reply_text(f"❌ **未查询到订单信息。**\n剩余机会：{2 - new_fails}次", parse_mode='Markdown')
            return WAITING_START_ORDER

# --- 充值流程 Handlers ---

async def recharge_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pt = 'wx' if query.data == 'pay_wx' else 'ali'
    context.user_data['pay_type'] = pt
    fid = get_file_id("WX_PAY_QR" if pt == 'wx' else "ALI_PAY_QR")
    text = f"💎 **{'微信' if pt == 'wx' else '支付宝'}充值**\n💰 5元 = 100积分\n⚠️ **限充 1 次，请勿重复。**"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ 我已支付，开始验证", callback_data="paid_recharge")]])
    
    if fid:
        try:
            await query.message.reply_photo(fid, caption=text, reply_markup=kb, parse_mode='Markdown')
            await query.delete_message()
        except:
            await query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
    else:
        await query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
    return WAITING_RECHARGE_ORDER

async def ask_recharge_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pt = context.user_data.get('pay_type', 'wx')
    fid = get_file_id("WX_ORDER_TUTORIAL" if pt == 'wx' else "ALI_ORDER_TUTORIAL")
    text = f"📝 **验证步骤：**\n请查找{'交易单号' if pt == 'wx' else '商家订单号'}。\n👇 请输入订单号："
    
    if fid:
        try:
            await query.message.reply_photo(fid, caption=text, parse_mode='Markdown')
        except:
            await query.message.reply_text(text, parse_mode='Markdown')
    else:
        await query.message.reply_text(text, parse_mode='Markdown')
    return WAITING_RECHARGE_ORDER

async def check_recharge_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    txt = update.message.text.strip()
    pt = context.user_data.get('pay_type', 'wx')
    valid = (pt == 'wx' and txt.startswith("4200")) or (pt == 'ali' and txt.startswith("4768"))
    
    if valid:
        update_points(user_id, 100, "充值")
        mark_success(user_id, pt)
        await update.message.reply_text("✅ **已充值 100 积分**", parse_mode='Markdown')
        await asyncio.sleep(1)
        await jf_command_handler(update, context)
        return ConversationHandler.END
    else:
        fails, _, _ = check_lock(user_id, pt)
        new_fails = update_fail(user_id, pt, fails, 3 * 60)
        
        if new_fails >= 2:
            await update.message.reply_text("❌ **失败 (2/2)**\n⚠️ 此渠道锁定 3 小时。", parse_mode='Markdown')
            await jf_command_handler(update, context)
            return ConversationHandler.END
        else:
            await update.message.reply_text(f"❌ **识别失败。**\n剩余机会：{2 - new_fails}次", parse_mode='Markdown')
            return WAITING_RECHARGE_ORDER

# --- VIP 购买 Handlers ---

async def buy_vip_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    is_v, _ = is_vip(update.effective_user.id)
    if is_v:
        await query.message.reply_text("✅ 您已是终身会员，无需重复购买！")
        return ConversationHandler.END
        
    fid = get_file_id("ALI_PAY_QR")
    text = (
        "🏆 **开通终身月卡会员**\n\n"
        "💰 价格：**5元** (终身有效)\n"
        "🔥 特权：每日兑换中心 **前 5 次免费** (无需积分)！\n\n"
        "👇 请使用 **支付宝** 扫码支付："
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ 我已付款，开始验证", callback_data="paid_vip")]])
    
    if fid:
        try:
            await query.message.reply_photo(fid, caption=text, reply_markup=kb, parse_mode='Markdown')
            await query.delete_message()
        except:
            await query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
    else:
        await query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
        
    return WAITING_VIP_ORDER

async def ask_vip_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    fid = get_file_id("ALI_ORDER_TUTORIAL")
    text = "📝 **验证步骤：**\n请复制 **商家订单号**\n\n👇 **请在下方输入订单号：**"
    if fid:
        try:
            await query.message.reply_photo(fid, caption=text, parse_mode='Markdown')
        except:
            await query.message.reply_text(text, parse_mode='Markdown')
    else:
        await query.message.reply_text(text, parse_mode='Markdown')
    return WAITING_VIP_ORDER

async def check_vip_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    txt = update.message.text.strip()
    
    if txt.startswith("4768"):
        activate_vip(user.id)
        await update.message.reply_text("🎉 **恭喜成为尊贵的终身会员！**", parse_mode='Markdown')
        if ADMIN_ID:
            try:
                await context.bot.send_message(chat_id=ADMIN_ID, text=f"💰 **新会员入账！**\n用户：{user.first_name} (`{user.id}`)", parse_mode='Markdown')
            except:
                pass
        await asyncio.sleep(2)
        await jf_command_handler(update, context)
        return ConversationHandler.END
    else:
        fails, _, _ = check_lock(user.id, 'vip_buy')
        new_fails = update_fail(user.id, 'vip_buy', fails, 10) # 10分钟
        
        if new_fails >= 2:
            await update.message.reply_text("❌ **验证失败 (2/2)**\n⚠️ 锁定 10 分钟。", parse_mode='Markdown')
            await jf_command_handler(update, context)
            return ConversationHandler.END
        else:
            await update.message.reply_text(f"❌ **订单号错误，请重试。**\n剩余机会：{2 - new_fails}次", parse_mode='Markdown')
            return WAITING_VIP_ORDER
            # ==============================================================================
# 兑换系统 (V5) /dh
# ==============================================================================

async def dh_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/dh 兑换列表"""
    user_id = update.effective_user.id
    
    # 门槛检查
    is_unlocked = is_exchange_unlocked(user_id)
    if not is_unlocked:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 去获取密钥解锁", callback_data="get_quark_key_v7")]])
        if update.callback_query:
            await update.callback_query.answer("🔒 请先解锁！", show_alert=True)
            return
        else:
            await update.message.reply_text("🔒 **兑换中心已锁定**\n请先在积分中心获取密钥解锁！", reply_markup=kb, parse_mode='Markdown')
            return

    offset = 0
    if update.callback_query and "list_prod_" in update.callback_query.data:
        offset = int(update.callback_query.data.split("_")[-1])
        
    rows, total = get_products_list(limit=10, offset=offset)
    is_v, _ = is_vip(user_id)
    daily_used, has_free = check_daily_free(user_id)
    
    kb = []
    kb.append([InlineKeyboardButton("🎁 测试商品 (0积分)", callback_data="confirm_buy_test")])
    
    for r in rows:
        is_bought = check_purchase(user_id, r[0])
        if is_bought:
            btn_text = f"✅ {r[1]} (已兑换)"
            callback = f"view_bought_{r[0]}"
        else:
            price_text = f"{r[2]}积分"
            if is_v and has_free:
                price_text = "免费(会员)"
            btn_text = f"🎁 {r[1]} ({price_text})"
            callback = f"confirm_buy_{r[0]}"
        kb.append([InlineKeyboardButton(btn_text, callback_data=callback)])
        
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"list_prod_{offset-10}"))
    if offset + 10 < total:
        nav.append(InlineKeyboardButton("➡️ 下一页", callback_data=f"list_prod_{offset+10}"))
    if nav:
        kb.append(nav)
    
    kb.append([InlineKeyboardButton("🔙 返回首页", callback_data="back_to_home")])
    
    text = "🎁 **积分兑换中心**\n请选择您要兑换的商品："
    if is_v:
        text += f"\n👑 会员特权：今日已免 {daily_used}/5 单"
        
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def exchange_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理购买确认与发货"""
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = update.effective_user.id
    
    if data == "confirm_buy_test":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ 确认", callback_data="do_buy_test"), InlineKeyboardButton("❌ 取消", callback_data="list_prod_0")]])
        await query.edit_message_text("❓ 确认兑换测试商品？", reply_markup=kb, parse_mode='Markdown')
        return
    elif data == "do_buy_test":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回兑换列表", callback_data="list_prod_0")]])
        await query.edit_message_text("🎉 兑换成功！内容：哈哈", reply_markup=kb, parse_mode='Markdown')
        return

    pid = int(data.split("_")[-1])
    
    if "view_bought_" in data:
        prod = get_product_details(pid)
        if not prod:
            await query.answer("商品不存在", show_alert=True)
            return
        content = prod[3] or "无文本"
        fid = prod[4]
        ftype = prod[5]
        await query.message.reply_text(f"📦 **已购内容：**\n`{content}`", parse_mode='Markdown')
        if fid:
            try:
                if ftype == 'photo': await context.bot.send_photo(uid, fid)
                elif ftype == 'video': await context.bot.send_video(uid, fid)
            except: pass
        return

    if "confirm_buy_" in data:
        prod = get_product_details(pid)
        if not prod:
            await query.answer("商品已下架", show_alert=True)
            return
        is_v, _ = is_vip(uid)
        _, has_free = check_daily_free(uid)
        cost_text = f"{prod[2]} 积分"
        if is_v and has_free: cost_text = "0 积分 (会员特权)"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ 确认兑换", callback_data=f"do_buy_{pid}"), InlineKeyboardButton("❌ 取消", callback_data="list_prod_0")]])
        await query.edit_message_text(f"❓ **确认兑换**\n商品：{prod[1]}\n价格：{cost_text}", reply_markup=kb, parse_mode='Markdown')
        return

    if "do_buy_" in data:
        prod = get_product_details(pid)
        if not prod:
            await query.answer("商品已下架", show_alert=True)
            return
        is_v, _ = is_vip(uid)
        _, has_free = check_daily_free(uid)
        price = prod[2]
        if is_v and has_free:
            use_free_chance(uid)
        else:
            if get_user_data(uid)[0] < price:
                await query.edit_message_text("❌ **余额不足！**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="list_prod_0")]]))
                return
            update_points(uid, -price, f"兑换-{prod[1]}")
        record_purchase(uid, pid)
        await query.message.reply_text(f"🎉 **兑换成功！**\n消耗 {price if not (is_v and has_free) else 0} 积分。\n\n📦 **内容：**\n`{prod[3] or ''}`", parse_mode='Markdown')
        if prod[4]:
            try:
                if prod[5]=='photo': await context.bot.send_photo(uid, prod[4])
                elif prod[5]=='video': await context.bot.send_video(uid, prod[4])
            except: pass
        await asyncio.sleep(1)
        await dh_command(update, context)

# ==============================================================================
# Admin Handlers
# ==============================================================================

async def admin_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID):
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 File ID 管理", callback_data="start_upload")],
        [InlineKeyboardButton("📚 频道转发库", callback_data="manage_cmds_entry")],
        [InlineKeyboardButton("🛍 商品管理", callback_data="manage_products_entry")],
        [InlineKeyboardButton("👥 用户与记录", callback_data="list_users")]
    ])
    if update.callback_query:
        await update.callback_query.edit_message_text("⚙️ **管理员后台**", reply_markup=kb, parse_mode='Markdown')
    else:
        await update.message.reply_text("⚙️ **管理员后台**", reply_markup=kb, parse_mode='Markdown')
    return ConversationHandler.END

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID):
        return
    rows, _ = get_all_users_info(20, 0)
    msg = "👥 **用户列表 (Top 20)**\n\n"
    for r in rows:
        mark = "👑" if r[3] and r[3] > datetime.now() else ""
        msg += f"ID: `{r[0]}` {mark} | 分: {r[2]}\n"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")]])
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, reply_markup=kb, parse_mode='Markdown')
    else:
        await update.message.reply_text(msg, reply_markup=kb, parse_mode='Markdown')

async def manage_products_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ 上架新商品", callback_data="add_product_start")],
        [InlineKeyboardButton("📂 管理/下架商品", callback_data="list_admin_prods_0")],
        [InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")]
    ])
    await query.edit_message_text("🛍 **商品管理**", reply_markup=kb, parse_mode='Markdown')

async def add_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📝 请输入 **商品名称**：", parse_mode='Markdown')
    return WAITING_PROD_NAME

async def receive_prod_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['p_name'] = update.message.text
    await update.message.reply_text("💰 请输入 **兑换价格** (数字)：")
    return WAITING_PROD_PRICE

async def receive_prod_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['p_price'] = int(update.message.text)
    except:
        await update.message.reply_text("❌ 必须是数字，请重试：")
        return WAITING_PROD_PRICE
    await update.message.reply_text("📦 请发送 **商品内容** (文本/图片/视频)：\n提示：使用反引号 `内容` 可让用户点击复制。")
    return WAITING_PROD_CONTENT

async def receive_prod_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    fid = None
    ftype = 'text'
    txt = msg.text or msg.caption
    
    if msg.photo:
        fid = msg.photo[-1].file_id
        ftype = 'photo'
    elif msg.video:
        fid = msg.video.file_id
        ftype = 'video'
    
    add_product(context.user_data['p_name'], context.user_data['p_price'], txt, fid, ftype)
    
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="manage_products_entry")]])
    await update.message.reply_text("✅ **商品上架成功！**", reply_markup=kb, parse_mode='Markdown')
    return ConversationHandler.END

async def list_admin_prods(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    offset = int(query.data.split("_")[-1])
    rows, total = get_products_list(limit=10, offset=offset)
    
    kb = []
    for r in rows:
        kb.append([InlineKeyboardButton(f"🗑 下架 {r[1]}", callback_data=f"ask_del_prod_{r[0]}")])
        
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"list_admin_prods_{offset-10}"))
    if offset + 10 < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"list_admin_prods_{offset+10}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("🔙 返回", callback_data="manage_products_entry")])
    
    await query.edit_message_text(f"🛍 **商品列表 ({offset//10 + 1})**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def ask_del_prod(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pid = int(query.data.split("_")[-1])
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 确认", callback_data=f"confirm_del_prod_{pid}"), InlineKeyboardButton("❌ 取消", callback_data="list_admin_prods_0")]
    ])
    await query.edit_message_text(f"⚠️ 确认下架商品 ID {pid}?", reply_markup=kb)

async def confirm_del_prod(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pid = int(query.data.split("_")[-1])
    delete_product(pid)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="manage_products_entry")]])
    await query.edit_message_text("🗑 已下架。", reply_markup=kb)

# --- Admin Handlers Continued ---

async def manage_cmds_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ 添加新命令", callback_data="add_new_cmd")],
        [InlineKeyboardButton("📂 管理/删除命令", callback_data="list_cmds_0")],
        [InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")]
    ])
    await query.edit_message_text("📚 **内容管理**", reply_markup=kb, parse_mode='Markdown')

async def list_cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    offset = int(query.data.split('_')[-1])
    rows, total = get_commands_list(limit=10, offset=offset)
    
    if not rows:
        await query.edit_message_text("📭 暂无自定义命令。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="manage_cmds_entry")]]))
        return
        
    kb = []
    for r in rows:
        kb.append([InlineKeyboardButton(f"🗑 删除 {r[1]}", callback_data=f"ask_del_cmd_{r[0]}")])
        
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"list_cmds_{offset-10}"))
    if offset + 10 < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"list_cmds_{offset+10}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("🔙 返回", callback_data="manage_cmds_entry")])
    
    await query.edit_message_text(f"📂 **命令列表 ({offset//10 + 1})**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def ask_del_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cmd_id = int(query.data.split('_')[-1])
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 确认", callback_data=f"confirm_del_cmd_{cmd_id}"), InlineKeyboardButton("❌ 取消", callback_data="manage_cmds_entry")]
    ])
    await query.edit_message_text(f"⚠️ **确定删除吗？**", reply_markup=kb, parse_mode='Markdown')

async def confirm_del_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cmd_id = int(query.data.split('_')[-1])
    delete_command_by_id(cmd_id)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="list_cmds_0")]])
    await query.edit_message_text("🗑 **已删除。**", reply_markup=kb, parse_mode='Markdown')

async def add_cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📝 输入新命令名称：", parse_mode='Markdown')
    return WAITING_CMD_NAME

async def receive_cmd_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    cid = add_custom_command(name)
    if not cid:
        await update.message.reply_text("❌ 已存在")
        return ConversationHandler.END
    context.user_data['ccd'] = cid
    context.user_data['ccn'] = name
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ 完成", callback_data="finish_cmd_bind")]])
    await update.message.reply_text(f"✅ `{name}` 创建。\n👇 发送内容 (多条)，完成后点按钮。", reply_markup=kb, parse_mode='Markdown')
    return WAITING_CMD_CONTENT

async def receive_cmd_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    cid = context.user_data.get('ccd')
    fid = None
    ftype = 'text'
    txt = msg.text or msg.caption
    if msg.photo:
        fid = msg.photo[-1].file_id
        ftype = 'photo'
    elif msg.video:
        fid = msg.video.file_id
        ftype = 'video'
    elif msg.document:
        fid = msg.document.file_id
        ftype = 'document'
    add_command_content(cid, fid, ftype, msg.caption, txt)
    return WAITING_CMD_CONTENT

async def finish_cmd_bind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="manage_cmds_entry")]])
    await query.edit_message_text("🎉 绑定完成！", reply_markup=kb)
    return ConversationHandler.END

async def my_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID):
        return
    info = get_system_keys_v7()
    if not info:
        refresh_system_keys_v7()
        info = get_system_keys_v7()
    
    msg = f"👮‍♂️ **密钥管理** ({info[-1]})\n\n"
    for i in range(1, 8):
        k_idx = (i-1)*2 + 1
        l_idx = (i-1)*2 + 2
        msg += f"🔑 Key{i}: `{info[k_idx]}`\n🔗 Link{i}: {info[l_idx] or '❌'}\n\n"
        
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✏️ 修改链接 (1-7)", callback_data="edit_links")]])
    await update.message.reply_text(msg, reply_markup=kb, parse_mode='Markdown')

async def start_edit_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("👇 请发送 **第 1 个** (百度) 链接：")
    return WAITING_LINK_1

async def receive_link_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_key_link_v7(1, update.message.text)
    await update.message.reply_text("👇 请发送 **第 2 个** (百度) 链接：")
    return WAITING_LINK_2

async def receive_link_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_key_link_v7(2, update.message.text)
    await update.message.reply_text("👇 请发送 **第 3 个** (夸克) 链接：")
    return WAITING_LINK_3

async def receive_link_3(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_key_link_v7(3, update.message.text)
    await update.message.reply_text("👇 请发送 **第 4 个** (夸克) 链接：")
    return WAITING_LINK_4

async def receive_link_4(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_key_link_v7(4, update.message.text)
    await update.message.reply_text("👇 请发送 **第 5 个** (夸克) 链接：")
    return WAITING_LINK_5

async def receive_link_5(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_key_link_v7(5, update.message.text)
    await update.message.reply_text("👇 请发送 **第 6 个** (夸克) 链接：")
    return WAITING_LINK_6

async def receive_link_6(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_key_link_v7(6, update.message.text)
    await update.message.reply_text("👇 请发送 **第 7 个** (夸克) 链接：")
    return WAITING_LINK_7

async def receive_link_7(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_key_link_v7(7, update.message.text)
    await update.message.reply_text("✅ **7个链接全部更新完成！**")
    return ConversationHandler.END

async def start_upload_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_to_admin")]])
    await update.callback_query.edit_message_text("📤 发送图片:", reply_markup=kb)
    return WAITING_FOR_PHOTO

async def handle_photo_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID):
        return ConversationHandler.END
    p = update.message.photo[-1]
    save_file_id(p.file_id, p.file_unique_id)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_to_admin")]])
    await update.message.reply_text(f"✅ ID:\n`{p.file_id}`", parse_mode='Markdown', reply_markup=kb)
    return WAITING_FOR_PHOTO

async def view_files_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    fs = get_all_files()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_to_admin")]])
    if not fs:
        await q.edit_message_text("📭 无记录", reply_markup=kb)
        return ConversationHandler.END
    await q.message.reply_text("📂 **列表:**", parse_mode='Markdown')
    for dbid, fid in fs:
        del_kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"🗑 删除 {dbid}", callback_data=f"pre_del_{dbid}")]])
        await context.bot.send_photo(q.message.chat_id, fid, caption=f"ID: `{dbid}`", reply_markup=del_kb)
    await context.bot.send_message(q.message.chat_id, "--- END ---", reply_markup=kb)
    return ConversationHandler.END

async def pre_delete_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    did = q.data.split('_')[-1]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 确认", callback_data=f"confirm_del_{did}"), InlineKeyboardButton("❌ 取消", callback_data="cancel_del")]
    ])
    await q.edit_message_caption(f"⚠️ 确认删除 ID {did}?", reply_markup=kb)

async def execute_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    did = q.data.split('_')[-1]
    delete_file_by_id(did)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_to_admin")]])
    await q.delete_message()
    await context.bot.send_message(q.message.chat_id, "已删除", reply_markup=kb)

async def cancel_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("取消")
    await update.callback_query.edit_message_caption("已取消", reply_markup=None)

async def cancel_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 取消")
    return ConversationHandler.END

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    if not text or text.startswith('/'):
        return
    
    # 1. 检查是否为自定义命令 (纯净转发)
    contents = get_command_content(text.strip())
    if contents:
        sent_msg_ids = []
        chat_id = update.effective_chat.id
        try:
            await update.message.delete()
        except:
            pass
        chunk_size = 10
        for i in range(0, len(contents), chunk_size):
            chunk = contents[i:i + chunk_size]
            media_group = []
            for item in chunk:
                # 修复：移除 caption，实现纯净发送
                if item[2] == 'photo':
                    media_group.append(InputMediaPhoto(media=item[1]))
                elif item[2] == 'video':
                    media_group.append(InputMediaVideo(media=item[1]))
            if len(media_group) == len(chunk) and len(media_group) > 1:
                try:
                    msgs = await context.bot.send_media_group(chat_id=chat_id, media=media_group)
                    sent_msg_ids.extend([m.message_id for m in msgs])
                except:
                    pass
            else:
                for item in chunk:
                    try:
                        m = None
                        if item[2] == 'text':
                            m = await context.bot.send_message(chat_id, item[4])
                        elif item[2] == 'photo':
                            m = await context.bot.send_photo(chat_id, item[1]) # 无 caption
                        elif item[2] == 'video':
                            m = await context.bot.send_video(chat_id, item[1]) # 无 caption
                        elif item[2] == 'document':
                            m = await context.bot.send_document(chat_id, item[1]) # 无 caption
                        if m:
                            sent_msg_ids.append(m.message_id)
                    except:
                        pass
        
        success_msg = await context.bot.send_message(chat_id, "✅ **发送完毕**", parse_mode='Markdown')
        sent_msg_ids.append(success_msg.message_id)
        asyncio.create_task(delete_messages_task(chat_id, sent_msg_ids))
        await asyncio.sleep(2)
        await dh_command(update, context)
        return
    
    # 2. 密钥验证
    success, msg = check_key_valid(user.id, text)
    if success:
        await update.message.reply_text("✅ **密钥验证成功！**\n兑换中心已为您解锁。", parse_mode='Markdown')
        await jf_command_handler(update, context)
    elif msg == "used":
        await update.message.reply_text("⚠️ 此密钥您已使用过，请获取新的密钥。")
    else:
        await start(update, context)

@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"--- DOMAIN: {RAILWAY_DOMAIN} ---"); init_db(); print("DB OK.")
    if not get_system_keys_v7(): refresh_system_keys_v7()
    scheduler.add_job(weekly_reset_task, 'cron', day_of_week='mon', hour=0, timezone=tz_bj)
    scheduler.add_job(daily_reset_task, 'cron', hour=0, minute=0, timezone=tz_bj)
    scheduler.start()
    
    global bot_app
    bot_app = Application.builder().token(BOT_TOKEN).build()
    
    # Handlers
    verify_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(verify_entry, pattern="^start_verify_flow$")],
        states={WAITING_START_ORDER: [CallbackQueryHandler(ask_start_order, pattern="^paid_start$"), MessageHandler(filters.TEXT & ~filters.COMMAND, check_start_order)]},
        fallbacks=[CommandHandler("start", start), CommandHandler("c", cancel_command)], per_message=False
    )
    vip_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(buy_vip_card, pattern="^buy_vip_card$")],
        states={WAITING_VIP_ORDER: [CallbackQueryHandler(ask_vip_order, pattern="^paid_vip$"), MessageHandler(filters.TEXT & ~filters.COMMAND, check_vip_order)]},
        fallbacks=[CommandHandler("jf", jf_command_handler), CommandHandler("c", cancel_command)], per_message=False
    )
    recharge_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(recharge_menu, pattern="^go_recharge$"), CallbackQueryHandler(recharge_entry, pattern="^pay_wx|pay_ali$")],
        states={WAITING_RECHARGE_ORDER: [CallbackQueryHandler(ask_recharge_order, pattern="^paid_recharge$"), MessageHandler(filters.TEXT & ~filters.COMMAND, check_recharge_order)]},
        fallbacks=[CommandHandler("jf", jf_command_handler), CallbackQueryHandler(jf_command_handler, pattern="^my_points$"), CommandHandler("c", cancel_command)], per_message=False
    )
    cmd_add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_cmd_start, pattern="^add_new_cmd$")],
        states={WAITING_CMD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_cmd_name)], WAITING_CMD_CONTENT: [MessageHandler(filters.ALL & ~filters.COMMAND, receive_cmd_content), CallbackQueryHandler(finish_cmd_bind, pattern="^finish_cmd_bind$")]},
        fallbacks=[CallbackQueryHandler(manage_cmds_entry, pattern="^manage_cmds_entry$"), CommandHandler("c", cancel_command)], per_message=False
    )
    key_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_edit_links, pattern="^edit_links$")],
        states={WAITING_LINK_1: [MessageHandler(filters.TEXT, receive_link_1)], WAITING_LINK_2: [MessageHandler(filters.TEXT, receive_link_2)], WAITING_LINK_3: [MessageHandler(filters.TEXT, receive_link_3)], WAITING_LINK_4: [MessageHandler(filters.TEXT, receive_link_4)], WAITING_LINK_5: [MessageHandler(filters.TEXT, receive_link_5)], WAITING_LINK_6: [MessageHandler(filters.TEXT, receive_link_6)], WAITING_LINK_7: [MessageHandler(filters.TEXT, receive_link_7)]},
        fallbacks=[CommandHandler("cancel", cancel_admin), CommandHandler("c", cancel_command)]
    )
    admin_up_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_upload_flow, pattern="^start_upload$")],
        states={WAITING_FOR_PHOTO:[MessageHandler(filters.PHOTO, handle_photo_upload), CallbackQueryHandler(admin_entry, pattern="^back_to_admin$")]},
        fallbacks=[CommandHandler("admin", admin_entry), CommandHandler("c", cancel_command)]
    )
    prod_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_product_start, pattern="^add_product_start$")],
        states={WAITING_PROD_NAME: [MessageHandler(filters.TEXT, receive_prod_name)], WAITING_PROD_PRICE: [MessageHandler(filters.TEXT, receive_prod_price)], WAITING_PROD_CONTENT: [MessageHandler(filters.ALL, receive_prod_content)]},
        fallbacks=[CallbackQueryHandler(manage_products_entry, pattern="^manage_products_entry$"), CommandHandler("c", cancel_command)], per_message=False
    )

    bot_app.add_handler(verify_conv); bot_app.add_handler(vip_conv); bot_app.add_handler(recharge_conv)
    bot_app.add_handler(cmd_add_conv); bot_app.add_handler(key_conv); bot_app.add_handler(admin_up_conv); bot_app.add_handler(prod_conv)
    
    bot_app.add_handler(CommandHandler("start", start)); bot_app.add_handler(CallbackQueryHandler(start, pattern="^back_to_home$"))
    bot_app.add_handler(CommandHandler("jf", jf_command_handler)); bot_app.add_handler(CallbackQueryHandler(jf_command_handler, pattern="^my_points$")); bot_app.add_handler(CallbackQueryHandler(noop_handler, pattern="^noop_")); bot_app.add_handler(CallbackQueryHandler(view_balance, pattern="^view_balance$"))
    bot_app.add_handler(CommandHandler("hd", activity_handler)); bot_app.add_handler(CallbackQueryHandler(activity_handler, pattern="^open_activity$")); bot_app.add_handler(CallbackQueryHandler(checkin_handler, pattern="^do_checkin$")); bot_app.add_handler(CallbackQueryHandler(quark_key_btn_handler, pattern="^get_quark_key$")); bot_app.add_handler(CallbackQueryHandler(get_quark_key_entry, pattern="^get_quark_key_v7$"))
    bot_app.add_handler(CommandHandler("dh", dh_command)); bot_app.add_handler(CallbackQueryHandler(dh_command, pattern="^go_exchange$")); bot_app.add_handler(CallbackQueryHandler(dh_command, pattern="^list_prod_")); bot_app.add_handler(CallbackQueryHandler(exchange_handler, pattern="^confirm_buy_|do_buy_|view_bought_"))
    
    bot_app.add_handler(CommandHandler("admin", admin_entry)); bot_app.add_handler(CallbackQueryHandler(admin_entry, pattern="^back_to_admin$"))
    bot_app.add_handler(CallbackQueryHandler(manage_cmds_entry, pattern="^manage_cmds_entry$")); bot_app.add_handler(CallbackQueryHandler(list_cmds, pattern="^list_cmds_")); bot_app.add_handler(CallbackQueryHandler(ask_del_cmd, pattern="^ask_del_cmd_")); bot_app.add_handler(CallbackQueryHandler(confirm_del_cmd, pattern="^confirm_del_cmd_"))
    bot_app.add_handler(CallbackQueryHandler(manage_products_entry, pattern="^manage_products_entry$")); bot_app.add_handler(CallbackQueryHandler(list_admin_prods, pattern="^list_admin_prods_")); bot_app.add_handler(CallbackQueryHandler(ask_del_prod, pattern="^ask_del_prod_")); bot_app.add_handler(CallbackQueryHandler(confirm_del_prod, pattern="^confirm_del_prod_"))
    bot_app.add_handler(CommandHandler("my", my_command)); bot_app.add_handler(CommandHandler("cz", cz_command)); bot_app.add_handler(CommandHandler("users", list_users))
    bot_app.add_handler(CallbackQueryHandler(list_users, pattern="^list_users$"))
    bot_app.add_handler(CommandHandler("c", cancel_command))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    await bot_app.initialize(); await bot_app.start(); await bot_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    
    yield
    if bot_app: await bot_app.stop(); await bot_app.shutdown()
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def health(): return {"status": "ok"}

@app.get("/watch_ad/{token}")
async def wad(token: str):
    html = """<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>视频任务</title><script src="https://telegram.org/js/telegram-web-app.js"></script><script src='https://libtl.com/sdk.js' data-zone='10489957' data-sdk='show_10489957'></script><style>body{font-family:sans-serif;text-align:center;padding:20px;background:#f4f4f9}.btn{padding:15px;background:#0088cc;color:white;border:none;border-radius:8px;width:100%}</style></head><body><h2>📺 观看广告</h2><button id="btn" class="btn" onclick="start()">▶️ 开始</button><div id="s" style="margin-top:20px"></div><script>const token="TOKEN_VAL";const s=document.getElementById('s'),btn=document.getElementById('btn');if(window.Telegram&&window.Telegram.WebApp)window.Telegram.WebApp.ready();function start(){btn.disabled=!0;s.innerText="⏳ 加载中...";if(typeof show_10489957==='function'){show_10489957().then(()=>{s.innerText="✅ 验证中...";fetch('/api/verify_ad',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:token})}).then(r=>r.json()).then(d=>{if(d.success){s.innerHTML="🎉 成功! +"+d.points+"分";setTimeout(()=>{if(window.Telegram&&window.Telegram.WebApp)window.Telegram.WebApp.close();else window.close()},2000)}else{s.innerText="❌ "+d.message;btn.disabled=!1}}).catch(e=>{s.innerText="❌ 网络错误";btn.disabled=!1})}).catch(e=>{console.log(e);s.innerText="❌ 广告失败:"+e;btn.disabled=!1})}else{s.innerText="❌ SDK Error";btn.disabled=!1}}</script></body></html>"""
    return HTMLResponse(content=html.replace("TOKEN_VAL", token))

@app.post("/api/verify_ad")
async def vad(p: dict):
    uid = verify_token(p.get("token"))
    if not uid: return JSONResponse({"success": False, "message": "Expired"})
    res = process_ad_reward(uid)
    if res["status"] == "success":
        try: await bot_app.bot.send_message(chat_id=uid, text=f"🎉 **恭喜！** 观看完成，获得 {res['added']} 积分！", parse_mode='Markdown')
        except: pass
    return JSONResponse({"success": True, "points": res.get("added", 0), "message": res.get("status")})

@app.get("/jump")
async def jump(key_index: int = 1):
    row = get_system_keys_v7()
    if not row: return HTMLResponse("<h1>System Error</h1>")
    link_idx = key_index * 2; raw_target = row[link_idx]
    if not raw_target: return HTMLResponse("<h1>Link Not Set</h1>")
    target = raw_target if raw_target.startswith("http") else "https://" + raw_target
    html = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>跳转中</title></head><body><h2 style="text-align:center">🚀 跳转中...</h2><iframe src="https://otieu.com/4/10489994" style="width:1px;height:1px;opacity:0;border:none"></iframe><script>setTimeout(()=>window.location.href="TARGET_URL",3000)</script></body></html>"""
    return HTMLResponse(content=html.replace("TARGET_URL", target))

@app.get("/ad_success")
async def success_page(points: int = 0):
    return HTMLResponse(content=f"<html><body><h1>🎉 成功! +{points}分</h1></body></html>")

@app.get("/test_page")
async def test_page():
    return HTMLResponse(content="<html><body><h1>Test Page</h1></body></html>")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
