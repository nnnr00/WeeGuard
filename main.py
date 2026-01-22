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
# 🛠️ 配置区域 (File ID)
# ==============================================================================
CONFIG = {
    "START_VIP_INFO": "AgACAgEAAxkBAAIC...", 
    "START_TUTORIAL": "AgACAgEAAxkBAAIC...",
    "WX_PAY_QR": "AgACAgEAAxkBAAIC...",
    "WX_ORDER_TUTORIAL": "AgACAgEAAxkBAAIC...",
    "ALI_PAY_QR": "AgACAgEAAxkBAAIC...",
    "ALI_ORDER_TUTORIAL": "AgACAgEAAxkBAAIC...",
}

# 环境变量
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
raw_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
RAILWAY_DOMAIN = raw_domain.replace("https://", "").replace("http://", "").strip("/")

# Moontag 直链
DIRECT_LINK_1 = "https://otieu.com/4/10489994"
DIRECT_LINK_2 = "https://otieu.com/4/10489998"

# 日志
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

tz_bj = pytz.timezone('Asia/Shanghai')
scheduler = AsyncIOScheduler(timezone=tz_bj)
bot_app = None

# 状态机
WAITING_FOR_PHOTO = 1
WAITING_LINK_1 = 2
WAITING_LINK_2 = 3
WAITING_CMD_NAME = 30
WAITING_CMD_CONTENT = 31
WAITING_PROD_NAME = 40
WAITING_PROD_PRICE = 41
WAITING_PROD_CONTENT = 42
WAITING_START_ORDER = 10
WAITING_RECHARGE_ORDER = 20

# ==============================================================================
# 数据库初始化
# ==============================================================================

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. 基础表 V3
    cur.execute("CREATE TABLE IF NOT EXISTS file_ids_v3 (id SERIAL PRIMARY KEY, file_id TEXT, file_unique_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")
    
    # 2. 用户表 V3
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users_v3 (
            user_id BIGINT PRIMARY KEY,
            points INTEGER DEFAULT 0,
            last_checkin_date DATE,
            checkin_count INTEGER DEFAULT 0,
            verify_fails INTEGER DEFAULT 0, verify_lock TIMESTAMP, verify_done BOOLEAN DEFAULT FALSE,
            wx_fails INTEGER DEFAULT 0, wx_lock TIMESTAMP, wx_done BOOLEAN DEFAULT FALSE,
            ali_fails INTEGER DEFAULT 0, ali_lock TIMESTAMP, ali_done BOOLEAN DEFAULT FALSE,
            username TEXT
        );
    """)
    cols = ["verify_fails INT DEFAULT 0", "verify_lock TIMESTAMP", "verify_done BOOLEAN DEFAULT FALSE",
            "wx_fails INT DEFAULT 0", "wx_lock TIMESTAMP", "wx_done BOOLEAN DEFAULT FALSE",
            "ali_fails INT DEFAULT 0", "ali_lock TIMESTAMP", "ali_done BOOLEAN DEFAULT FALSE",
            "username TEXT"]
    for c in cols:
        try: cur.execute(f"ALTER TABLE users_v3 ADD COLUMN IF NOT EXISTS {c};")
        except: conn.rollback()

    # 3. 广告/密钥 V3
    cur.execute("CREATE TABLE IF NOT EXISTS user_ads_v3 (user_id BIGINT PRIMARY KEY, last_watch_date DATE, daily_watch_count INT DEFAULT 0);")
    cur.execute("CREATE TABLE IF NOT EXISTS ad_tokens_v3 (token TEXT PRIMARY KEY, user_id BIGINT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")
    cur.execute("CREATE TABLE IF NOT EXISTS system_keys_v3 (id INTEGER PRIMARY KEY, key_1 TEXT, link_1 TEXT, key_2 TEXT, link_2 TEXT, session_date DATE, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")
    cur.execute("INSERT INTO system_keys_v3 (id, session_date) VALUES (1, %s) ON CONFLICT (id) DO NOTHING", (date(2000,1,1),))
    cur.execute("CREATE TABLE IF NOT EXISTS user_key_clicks_v3 (user_id BIGINT PRIMARY KEY, click_count INT DEFAULT 0, session_date DATE);")
    cur.execute("CREATE TABLE IF NOT EXISTS user_key_claims_v3 (id SERIAL PRIMARY KEY, user_id BIGINT, key_val TEXT, claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id, key_val));")

    # 4. 转发库 V4
    cur.execute("CREATE TABLE IF NOT EXISTS custom_commands_v4 (id SERIAL PRIMARY KEY, command_name TEXT UNIQUE NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")
    cur.execute("CREATE TABLE IF NOT EXISTS command_contents_v4 (id SERIAL PRIMARY KEY, command_id INT REFERENCES custom_commands_v4(id) ON DELETE CASCADE, file_id TEXT, file_type TEXT, caption TEXT, message_text TEXT, sort_order SERIAL);")

    # 5. 商品 V5
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
    if now.hour < 10: return (now - timedelta(days=1)).date()
    return now.date()

def generate_random_key():
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(10))

def get_file_id(key):
    fid = CONFIG.get(key)
    return fid if fid and fid.startswith("AgAC") else None

def ensure_user_exists(user_id, username=None):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO users_v3 (user_id, username) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username", (user_id, username))
    cur.execute("INSERT INTO user_ads_v3 (user_id, daily_watch_count) VALUES (%s, 0) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    conn.commit(); cur.close(); conn.close()

# --- 积分系统 ---

def update_points(user_id, amount, reason):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE users_v3 SET points = points + %s WHERE user_id = %s RETURNING points", (amount, user_id))
    new_total = cur.fetchone()[0]
    cur.execute("INSERT INTO point_logs_v5 (user_id, change_amount, reason) VALUES (%s, %s, %s)", (user_id, amount, reason))
    conn.commit(); cur.close(); conn.close()
    return new_total

def get_user_data(user_id):
    ensure_user_exists(user_id)
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT points, last_checkin_date, checkin_count FROM users_v3 WHERE user_id=%s", (user_id,))
    row = cur.fetchone(); cur.close(); conn.close()
    return row

def get_point_logs(user_id, limit=5):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT change_amount, reason, created_at FROM point_logs_v5 WHERE user_id = %s ORDER BY id DESC LIMIT %s", (user_id, limit))
    rows = cur.fetchall(); cur.close(); conn.close()
    return rows

def process_checkin(user_id):
    ensure_user_exists(user_id)
    conn = get_db_connection(); cur = conn.cursor()
    today = datetime.now(tz_bj).date()
    cur.execute("SELECT last_checkin_date, checkin_count FROM users_v3 WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    if row[0] == today: cur.close(); conn.close(); return {"status": "already_checked"}
    pts = 10 if row[1] == 0 else random.randint(3, 8)
    cur.execute("UPDATE users_v3 SET points=points+%s, last_checkin_date=%s, checkin_count=checkin_count+1 WHERE user_id=%s RETURNING points", (pts, today, user_id))
    total = cur.fetchone()[0]
    cur.execute("INSERT INTO point_logs_v5 (user_id, change_amount, reason) VALUES (%s, %s, '每日签到')", (user_id, pts))
    conn.commit(); cur.close(); conn.close()
    return {"status": "success", "added": pts, "total": total}

# --- 验证/锁 ---

def check_lock(user_id, type_prefix):
    ensure_user_exists(user_id)
    conn = get_db_connection(); cur = conn.cursor()
    fields = f"{type_prefix}_fails, {type_prefix}_lock, {type_prefix}_done"
    cur.execute(f"SELECT {fields} FROM users_v3 WHERE user_id = %s", (user_id,))
    row = cur.fetchone(); cur.close(); conn.close()
    return row if row else (0, None, False)

def update_fail(user_id, type_prefix, current_fails, lock_hours):
    conn = get_db_connection(); cur = conn.cursor()
    new_fails = current_fails + 1
    if new_fails >= 2:
        lock_until = datetime.now() + timedelta(hours=lock_hours)
        cur.execute(f"UPDATE users_v3 SET {type_prefix}_fails = %s, {type_prefix}_lock = %s WHERE user_id = %s", (new_fails, lock_until, user_id))
    else:
        cur.execute(f"UPDATE users_v3 SET {type_prefix}_fails = %s WHERE user_id = %s", (new_fails, user_id))
    conn.commit(); cur.close(); conn.close()
    return new_fails

def mark_success(user_id, type_prefix):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute(f"UPDATE users_v3 SET {type_prefix}_fails=0, {type_prefix}_lock=NULL, {type_prefix}_done=TRUE WHERE user_id=%s", (user_id,))
    conn.commit(); cur.close(); conn.close()

# --- 广告 & 密钥 ---

def get_ad_status(user_id):
    ensure_user_exists(user_id)
    conn = get_db_connection(); cur = conn.cursor()
    today = datetime.now(tz_bj).date()
    cur.execute("SELECT daily_watch_count FROM user_ads_v3 WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    cnt = row[0] if row else 0
    if row and row[0] != today: cnt = 0 
    cur.close(); conn.close()
    return cnt

def create_ad_token(user_id):
    t = str(uuid.uuid4()); conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO ad_tokens_v3 (token, user_id) VALUES (%s,%s)", (t, user_id))
    conn.commit(); cur.close(); conn.close()
    return t

def verify_token(t):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM ad_tokens_v3 WHERE token=%s RETURNING user_id", (t,))
    row = cur.fetchone(); conn.commit(); cur.close(); conn.close()
    return row[0] if row else None

def process_ad_reward(user_id):
    ensure_user_exists(user_id)
    cnt = get_ad_status(user_id)
    if cnt >= 3: return {"status": "limit_reached"}
    pts = 10 if cnt == 0 else (6 if cnt == 1 else random.randint(3, 10))
    update_points(user_id, pts, "观看广告")
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE user_ads_v3 SET last_watch_date=%s, daily_watch_count=daily_watch_count+1 WHERE user_id=%s", (datetime.now(tz_bj).date(), user_id))
    conn.commit(); cur.close(); conn.close()
    return {"status": "success", "added": pts}

def update_system_keys(k1, k2, d):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE system_keys_v3 SET key_1=%s, key_2=%s, session_date=%s WHERE id=1", (k1, k2, d))
    conn.commit(); cur.close(); conn.close()

def update_key_links(l1, l2):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE system_keys_v3 SET link_1=%s, link_2=%s WHERE id=1", (l1, l2))
    conn.commit(); cur.close(); conn.close()

def get_system_keys_info():
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT key_1, link_1, key_2, link_2, session_date FROM system_keys_v3 WHERE id=1")
    row = cur.fetchone(); cur.close(); conn.close()
    return row

def get_user_click_status(user_id):
    s = get_session_date(); conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT click_count, session_date FROM user_key_clicks_v3 WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    if not row or row[1] != s:
        cur.execute("INSERT INTO user_key_clicks_v3 (user_id,click_count,session_date) VALUES (%s,0,%s) ON CONFLICT(user_id) DO UPDATE SET click_count=0,session_date=%s", (user_id, s, s))
        conn.commit(); cur.close(); conn.close(); return 0
    cur.close(); conn.close(); return row[0]

def increment_user_click(user_id):
    s = get_session_date(); conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE user_key_clicks_v3 SET click_count=click_count+1 WHERE user_id=%s AND session_date=%s", (user_id, s))
    conn.commit(); cur.close(); conn.close()

def claim_key_points(user_id, txt):
    ensure_user_exists(user_id); info = get_system_keys_info()
    if not info: return {"status": "error"}
    k1, _, k2, _, _ = info; pts = 0
    if txt.strip() == k1: pts = 8
    elif txt.strip() == k2: pts = 6
    else: return {"status": "invalid"}
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id FROM user_key_claims_v3 WHERE user_id=%s AND key_val=%s", (user_id, txt.strip()))
    if cur.fetchone(): cur.close(); conn.close(); return {"status": "already_claimed"}
    cur.execute("INSERT INTO user_key_claims_v3 (user_id, key_val) VALUES (%s, %s)", (user_id, txt.strip()))
    conn.commit(); cur.close(); conn.close()
    update_points(user_id, pts, "密钥兑换")
    return {"status": "success", "points": pts}

# --- 商品 & 命令 ---

def add_product(name, price, text, fid, ftype):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO products_v5 (name, price, content_text, content_file_id, content_type) VALUES (%s, %s, %s, %s, %s)", (name, price, text, fid, ftype))
    conn.commit(); cur.close(); conn.close()

def get_products_list(limit, offset):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id, name, price FROM products_v5 ORDER BY id DESC LIMIT %s OFFSET %s", (limit, offset))
    rows = cur.fetchall(); cur.execute("SELECT COUNT(*) FROM products_v5"); t = cur.fetchone()[0]
    cur.close(); conn.close(); return rows, t

def get_product_details(pid):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id, name, price, content_text, content_file_id, content_type FROM products_v5 WHERE id=%s", (pid,))
    row = cur.fetchone(); cur.close(); conn.close(); return row

def delete_product(pid):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM products_v5 WHERE id=%s", (pid,))
    conn.commit(); cur.close(); conn.close()

def check_purchase(uid, pid):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id FROM user_purchases_v5 WHERE user_id=%s AND product_id=%s", (uid, pid))
    row = cur.fetchone(); cur.close(); conn.close(); return True if row else False

def record_purchase(uid, pid):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO user_purchases_v5 (user_id, product_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (uid, pid))
    conn.commit(); cur.close(); conn.close()

def add_custom_command(cmd):
    conn = get_db_connection(); cur = conn.cursor()
    try: cur.execute("INSERT INTO custom_commands_v4 (command_name) VALUES (%s) RETURNING id", (cmd,)); cid = cur.fetchone()[0]; conn.commit(); cur.close(); conn.close(); return cid
    except: conn.rollback(); cur.close(); conn.close(); return None

def add_command_content(cid, fid, ftype, cap, txt):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO command_contents_v4 (command_id,file_id,file_type,caption,message_text) VALUES (%s,%s,%s,%s,%s)", (cid, fid, ftype, cap, txt))
    conn.commit(); cur.close(); conn.close()

def get_commands_list(limit, offset):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id, command_name FROM custom_commands_v4 ORDER BY id DESC LIMIT %s OFFSET %s", (limit, offset))
    rs = cur.fetchall(); cur.execute("SELECT COUNT(*) FROM custom_commands_v4"); t = cur.fetchone()[0]; cur.close(); conn.close(); return rs, t

def delete_command_by_id(cid):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM custom_commands_v4 WHERE id=%s", (cid,))
    conn.commit(); cur.close(); conn.close()

def get_command_content(cmd):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT c.id, c.file_id, c.file_type, c.caption, c.message_text FROM command_contents_v4 c JOIN custom_commands_v4 cmd ON c.command_id=cmd.id WHERE cmd.command_name=%s ORDER BY c.sort_order", (cmd,))
    rs = cur.fetchall(); cur.close(); conn.close(); return rs

def get_all_users_info(limit, offset):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT user_id, username, points FROM users_v3 ORDER BY points DESC LIMIT %s OFFSET %s", (limit, offset))
    rs = cur.fetchall(); cur.execute("SELECT COUNT(*) FROM users_v3"); t = cur.fetchone()[0]; cur.close(); conn.close(); return rs, t

def reset_admin_stats(aid):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE user_ads_v3 SET daily_watch_count=0 WHERE user_id=%s", (aid,))
    cur.execute("UPDATE user_key_clicks_v3 SET click_count=0 WHERE user_id=%s", (aid,))
    cur.execute("DELETE FROM user_key_claims_v3 WHERE user_id=%s", (aid,))
    cur.execute("DELETE FROM user_purchases_v5 WHERE user_id=%s", (aid,))
    cur.execute("UPDATE users_v3 SET verify_fails=0,verify_lock=NULL,verify_done=FALSE,wx_fails=0,wx_lock=NULL,wx_done=FALSE,ali_fails=0,ali_lock=NULL,ali_done=FALSE WHERE user_id=%s", (aid,))
    conn.commit(); cur.close(); conn.close()

def save_file_id(fid, fuid):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO file_ids_v3 (file_id, file_unique_id) VALUES (%s, %s)", (fid, fuid))
    conn.commit(); cur.close(); conn.close()

def get_all_files():
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id, file_id FROM file_ids_v3 ORDER BY id DESC LIMIT 10")
    rs = cur.fetchall(); cur.close(); conn.close(); return rs

def delete_file_by_id(did):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM file_ids_v3 WHERE id=%s", (did,))
    conn.commit(); cur.close(); conn.close()
    # ==============================================================================
# 定时任务 (必须在 Handlers 之前定义)
# ==============================================================================

async def daily_reset_task():
    """每日密钥重置任务"""
    k1 = generate_random_key()
    k2 = generate_random_key()
    update_system_keys(k1, k2, date.today())
    if bot_app and ADMIN_ID:
        try:
            await bot_app.bot.send_message(ADMIN_ID, f"🔔 每日密钥更新\nK1: `{k1}`\nK2: `{k2}`", parse_mode='Markdown')
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
        [InlineKeyboardButton("💰 积分 & 兑换", callback_data="my_points")],
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

async def jf_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = get_user_data(user.id)
    text = f"💰 **积分中心**\n💎 积分：`{data[0]}`"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 签到", callback_data="do_checkin"), InlineKeyboardButton("🎁 兑换", callback_data="go_exchange")],
        [InlineKeyboardButton("💎 充值 (微信/支付宝)", callback_data="go_recharge")],
        [InlineKeyboardButton("📜 余额 & 记录", callback_data="view_balance")],
        [InlineKeyboardButton("🔙 返回首页", callback_data="back_to_home")]
    ])
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode='Markdown')

async def view_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看余额与流水"""
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    data = get_user_data(uid)
    logs = get_point_logs(uid, 10)
    
    log_text = ""
    if logs:
        for l in logs:
            # l: (change_amount, reason, created_at)
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
    msg = "✅ 已完成" if "done" in query.data else "⛔️ 暂时锁定"
    await query.answer(msg, show_alert=True)

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
    kc = get_user_click_status(user.id)
    t = create_ad_token(user.id)
    
    w_url = f"https://{RAILWAY_DOMAIN}/watch_ad/{t}"
    test_url = f"https://{RAILWAY_DOMAIN}/test_page"
    
    text = f"🎉 **活动中心**\n1️⃣ 视频积分 ({count}/3)\n2️⃣ 夸克密钥 ({kc}/2)\n🛠 功能测试"
    
    kb = []
    if count < 3:
        kb.append([InlineKeyboardButton("📺 看视频", url=w_url)])
    else:
        kb.append([InlineKeyboardButton("✅ 视频已完成", callback_data="noop_done")])
        
    if kc < 2:
        kb.append([InlineKeyboardButton("🔑 获取密钥", callback_data="get_quark_key")])
    else:
        kb.append([InlineKeyboardButton("✅ 密钥已完成", callback_data="noop_done")])
        
    kb.append([InlineKeyboardButton("🛠 测试", url=test_url)])
    kb.append([InlineKeyboardButton("🔙 返回", callback_data="back_to_home")])
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def quark_key_btn_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    info = get_system_keys_info()
    
    if not info or not info[1]:
        await query.message.reply_text("⏳ 初始化中...")
        return
        
    kc = get_user_click_status(uid)
    if kc >= 2:
        await query.message.reply_text("⚠️ 次数已用完")
        return
        
    increment_user_click(uid)
    t = 1 if kc == 0 else 2
    url = f"https://{RAILWAY_DOMAIN}/jump?type={t}"
    
    await context.bot.send_message(uid, f"🚀 **获取密钥**\n链接：{url}\n点击跳转->保存->复制文件名->发送给机器人")

async def cz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID):
        return
    reset_admin_stats(update.effective_user.id)
    await update.message.reply_text("✅ 测试数据重置")
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
        await update.message.reply_text("✅ **验证成功！**\n您已成功加入会员群，无需重复验证。", parse_mode='Markdown')
        await asyncio.sleep(2)
        await start(update, context)
        return ConversationHandler.END
    else:
        fails, _, _ = check_lock(user_id, 'verify')
        new_fails = update_fail(user_id, 'verify', fails, 5)
        
        if new_fails >= 2:
            await update.message.reply_text("❌ **验证失败 (2/2)**\n⚠️ 已锁定 5 小时。", parse_mode='Markdown')
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
        new_fails = update_fail(user_id, pt, fails, 3) # 3小时锁
        
        if new_fails >= 2:
            await update.message.reply_text("❌ **失败 (2/2)**\n⚠️ 此渠道锁定 3 小时。", parse_mode='Markdown')
            await jf_command_handler(update, context)
            return ConversationHandler.END
        else:
            await update.message.reply_text(f"❌ **识别失败。**\n剩余机会：{2 - new_fails}次", parse_mode='Markdown')
            return WAITING_RECHARGE_ORDER
            # --- 兑换系统 (V5) /dh ---

async def dh_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/dh 兑换列表"""
    offset = 0
    if update.callback_query:
        await update.callback_query.answer()
        if "list_prod_" in update.callback_query.data:
            offset = int(update.callback_query.data.split("_")[-1])
    
    rows, total = get_products_list(limit=10, offset=offset)
    
    kb = []
    # 始终存在的测试按钮
    kb.append([InlineKeyboardButton("🎁 测试商品 (0积分)", callback_data="confirm_buy_test")])
    
    # 数据库商品
    for r in rows:
        # r: id, name, price
        is_bought = check_purchase(update.effective_user.id, r[0])
        if is_bought:
            btn_text = f"✅ {r[1]} (已兑换)"
            callback = f"view_bought_{r[0]}"
        else:
            btn_text = f"🎁 {r[1]} ({r[2]}积分)"
            callback = f"confirm_buy_{r[0]}"
        kb.append([InlineKeyboardButton(btn_text, callback_data=callback)])
        
    # 翻页
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"list_prod_{offset-10}"))
    if offset + 10 < total:
        nav.append(InlineKeyboardButton("➡️ 下一页", callback_data=f"list_prod_{offset+10}"))
    if nav:
        kb.append(nav)
    
    # 移除了余额按钮
    kb.append([InlineKeyboardButton("🔙 返回首页", callback_data="back_to_home")])
    
    text = "🎁 **积分兑换中心**\n请选择您要兑换的商品："
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
    
    # 1. 测试商品
    if data == "confirm_buy_test":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 确认", callback_data="do_buy_test"), InlineKeyboardButton("❌ 取消", callback_data="list_prod_0")]
        ])
        await query.edit_message_text("❓ **确认兑换**\n商品：测试商品\n价格：0 积分", reply_markup=kb, parse_mode='Markdown')
        return
    elif data == "do_buy_test":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回兑换列表", callback_data="list_prod_0")]])
        await query.edit_message_text("🎉 **兑换成功！**\n内容：哈哈", reply_markup=kb, parse_mode='Markdown')
        return

    # 2. 真实商品
    pid = int(data.split("_")[-1])
    
    # 查看已购
    if "view_bought_" in data:
        prod = get_product_details(pid)
        if not prod:
            await query.answer("商品已下架", show_alert=True)
            return
        
        content = prod[3] or "无文本"
        fid = prod[4]
        ftype = prod[5]
        
        await query.message.reply_text(f"📦 **已购内容：**\n{content}", parse_mode='Markdown')
        if fid:
            try:
                if ftype == 'photo':
                    await context.bot.send_photo(uid, fid)
                elif ftype == 'video':
                    await context.bot.send_video(uid, fid)
            except:
                pass
        return

    # 确认购买
    if "confirm_buy_" in data:
        prod = get_product_details(pid)
        if not prod:
            await query.answer("商品已下架", show_alert=True)
            return
        price = prod[2]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 确认支付", callback_data=f"do_buy_{pid}"), InlineKeyboardButton("❌ 取消", callback_data="list_prod_0")]
        ])
        await query.edit_message_text(f"❓ **确认兑换**\n商品：{prod[1]}\n价格：{price} 积分", reply_markup=kb, parse_mode='Markdown')
        return

    # 执行购买
    if "do_buy_" in data:
        prod = get_product_details(pid)
        if not prod:
            await query.answer("商品已下架", show_alert=True)
            return
        price = prod[2]
        
        user_pts = get_user_data(uid)[0]
        if user_pts < price:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="list_prod_0")]])
            await query.edit_message_text("❌ **余额不足！**\n请充值或赚取更多积分。", reply_markup=kb, parse_mode='Markdown')
            return
            
        update_points(uid, -price, f"兑换-{prod[1]}")
        record_purchase(uid, pid)
        
        await query.message.reply_text(f"🎉 **兑换成功！**\n消耗 {price} 积分。\n\n📦 **内容：**\n{prod[3] or ''}", parse_mode='Markdown')
        if prod[4]:
            try:
                if prod[5] == 'photo':
                    await context.bot.send_photo(uid, prod[4])
                elif prod[5] == 'video':
                    await context.bot.send_video(uid, prod[4])
            except:
                pass
            
        await asyncio.sleep(1)
        await dh_command(update, context) # 刷新列表

# --- Admin Handlers ---

async def admin_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID):
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 File ID 管理", callback_data="start_upload")],
        [InlineKeyboardButton("📚 频道转发库", callback_data="manage_cmds_entry")],
        [InlineKeyboardButton("🛍 商品管理", callback_data="manage_products_entry")],
        [InlineKeyboardButton("👥 用户与记录", callback_data="list_users")]
    ])
    # 修复：区分按钮和命令
    if update.callback_query:
        await update.callback_query.edit_message_text("⚙️ **管理员后台**", reply_markup=kb, parse_mode='Markdown')
    else:
        await update.message.reply_text("⚙️ **管理员后台**", reply_markup=kb, parse_mode='Markdown')
    return ConversationHandler.END

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
    await update.message.reply_text("📦 请发送 **商品内容** (文本/图片/视频)：")
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

# Admin User List
async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID):
        return
    rows, _ = get_all_users_info(20, 0)
    msg = "👥 **用户列表 (Top 20)**\n\n"
    for r in rows:
        msg += f"ID: `{r[0]}` | 名: {r[1] or '无'} | 分: {r[2]}\n"
    await update.message.reply_text(msg, parse_mode='Markdown')

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
    info = get_system_keys_info()
    if not info:
        return
    k1, l1, k2, l2, d = info
    msg = f"👮‍♂️ **密钥管理** ({d})\nK1: `{k1}`\nL1: {l1}\nK2: `{k2}`\nL2: {l2}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✏️ 修改", callback_data="edit_links")]])
    await update.message.reply_text(msg, reply_markup=kb, parse_mode='Markdown')

async def start_edit_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("👇 发送密钥1链接：")
    return WAITING_LINK_1

async def receive_link_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['nl1'] = update.message.text
    await update.message.reply_text("👇 发送密钥2链接：")
    return WAITING_LINK_2

async def receive_link_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_key_links(context.user_data['nl1'], update.message.text)
    await update.message.reply_text("✅ 更新完成")
    await start(update, context)
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
    
    # 1. 检查是否为自定义命令
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
                            m = await context.bot.send_photo(chat_id, item[1])
                        elif item[2] == 'video':
                            m = await context.bot.send_video(chat_id, item[1])
                        elif item[2] == 'document':
                            m = await context.bot.send_document(chat_id, item[1])
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
    
    # 2. 检查密钥
    result = claim_key_points(user.id, text)
    if result["status"] == "success":
        await update.message.reply_text(f"✅ **成功！** +{result['points']}分", parse_mode='Markdown')
    elif result["status"] == "already_claimed":
        await update.message.reply_text("⚠️ 密钥已使用。")
    else:
        # 全局回退
        await start(update, context)

# --- Main App & Web Server ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"--- DOMAIN: {RAILWAY_DOMAIN} ---")
    init_db()
    print("DB OK.")
    
    info = get_system_keys_info()
    if not info or info[4] == date(2000, 1, 1):
        update_system_keys(generate_random_key(), generate_random_key(), date.today())
    
    scheduler.add_job(daily_reset_task, 'cron', hour=10, minute=0, timezone=tz_bj)
    scheduler.start()
    
    global bot_app
    bot_app = Application.builder().token(BOT_TOKEN).build()
    
    # Handlers
    verify_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(verify_entry, pattern="^start_verify_flow$")],
        states={WAITING_START_ORDER: [CallbackQueryHandler(ask_start_order, pattern="^paid_start$"), MessageHandler(filters.TEXT & ~filters.COMMAND, check_start_order)]},
        fallbacks=[CommandHandler("start", start)], per_message=False
    )
    
    recharge_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(recharge_menu, pattern="^go_recharge$"), CallbackQueryHandler(recharge_entry, pattern="^pay_wx|pay_ali$")],
        states={WAITING_RECHARGE_ORDER: [CallbackQueryHandler(ask_recharge_order, pattern="^paid_recharge$"), MessageHandler(filters.TEXT & ~filters.COMMAND, check_recharge_order)]},
        fallbacks=[CommandHandler("jf", jf_command_handler), CallbackQueryHandler(jf_command_handler, pattern="^my_points$")], per_message=False
    )
    
    cmd_add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_cmd_start, pattern="^add_new_cmd$")],
        states={
            WAITING_CMD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_cmd_name)],
            WAITING_CMD_CONTENT: [MessageHandler(filters.ALL & ~filters.COMMAND, receive_cmd_content), CallbackQueryHandler(finish_cmd_bind, pattern="^finish_cmd_bind$")]
        },
        fallbacks=[CallbackQueryHandler(manage_cmds_entry, pattern="^manage_cmds_entry$")], per_message=False
    )
    
    key_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_edit_links, pattern="^edit_links$")],
        states={WAITING_LINK_1:[MessageHandler(filters.TEXT, receive_link_1)], WAITING_LINK_2:[MessageHandler(filters.TEXT, receive_link_2)]},
        fallbacks=[CommandHandler("cancel", cancel_admin)]
    )
    
    admin_up_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_upload_flow, pattern="^start_upload$")],
        states={WAITING_FOR_PHOTO:[MessageHandler(filters.PHOTO, handle_photo_upload), CallbackQueryHandler(admin_entry, pattern="^back_to_admin$")]},
        fallbacks=[CommandHandler("admin", admin_entry)]
    )
    
    prod_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_product_start, pattern="^add_product_start$")],
        states={
            WAITING_PROD_NAME: [MessageHandler(filters.TEXT, receive_prod_name)],
            WAITING_PROD_PRICE: [MessageHandler(filters.TEXT, receive_prod_price)],
            WAITING_PROD_CONTENT: [MessageHandler(filters.ALL, receive_prod_content)]
        },
        fallbacks=[CallbackQueryHandler(manage_products_entry, pattern="^manage_products_entry$")], per_message=False
    )

    bot_app.add_handler(verify_conv)
    bot_app.add_handler(recharge_conv)
    bot_app.add_handler(cmd_add_conv)
    bot_app.add_handler(key_conv)
    bot_app.add_handler(admin_up_conv)
    bot_app.add_handler(prod_conv)
    
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CallbackQueryHandler(start, pattern="^back_to_home$"))
    
    bot_app.add_handler(CommandHandler("jf", jf_command_handler))
    bot_app.add_handler(CallbackQueryHandler(jf_command_handler, pattern="^my_points$"))
    bot_app.add_handler(CallbackQueryHandler(noop_handler, pattern="^noop_"))
    bot_app.add_handler(CallbackQueryHandler(view_balance, pattern="^view_balance$"))
    
    bot_app.add_handler(CommandHandler("hd", activity_handler))
    bot_app.add_handler(CallbackQueryHandler(activity_handler, pattern="^open_activity$"))
    bot_app.add_handler(CallbackQueryHandler(checkin_handler, pattern="^do_checkin$"))
    bot_app.add_handler(CallbackQueryHandler(quark_key_btn_handler, pattern="^get_quark_key$"))
    
    bot_app.add_handler(CommandHandler("dh", dh_command))
    bot_app.add_handler(CallbackQueryHandler(dh_command, pattern="^go_exchange$"))
    bot_app.add_handler(CallbackQueryHandler(dh_command, pattern="^list_prod_"))
    bot_app.add_handler(CallbackQueryHandler(exchange_handler, pattern="^confirm_buy_|do_buy_|view_bought_"))
    
    bot_app.add_handler(CommandHandler("admin", admin_entry))
    bot_app.add_handler(CallbackQueryHandler(admin_entry, pattern="^back_to_admin$"))
    bot_app.add_handler(CallbackQueryHandler(manage_cmds_entry, pattern="^manage_cmds_entry$"))
    bot_app.add_handler(CallbackQueryHandler(list_cmds, pattern="^list_cmds_"))
    bot_app.add_handler(CallbackQueryHandler(ask_del_cmd, pattern="^ask_del_cmd_"))
    bot_app.add_handler(CallbackQueryHandler(confirm_del_cmd, pattern="^confirm_del_cmd_"))
    
    bot_app.add_handler(CallbackQueryHandler(manage_products_entry, pattern="^manage_products_entry$"))
    bot_app.add_handler(CallbackQueryHandler(list_admin_prods, pattern="^list_admin_prods_"))
    bot_app.add_handler(CallbackQueryHandler(ask_del_prod, pattern="^ask_del_prod_"))
    bot_app.add_handler(CallbackQueryHandler(confirm_del_prod, pattern="^confirm_del_prod_"))
    
    bot_app.add_handler(CommandHandler("my", my_command))
    bot_app.add_handler(CommandHandler("cz", cz_command))
    bot_app.add_handler(CommandHandler("users", list_users))
    
    bot_app.add_handler(CallbackQueryHandler(list_users, pattern="^list_users$"))
    
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    
    yield
    if bot_app:
        await bot_app.stop()
        await bot_app.shutdown()
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def health():
    return {"status": "ok"}

@app.get("/watch_ad/{token}")
async def wad(token: str):
    # 修复：使用字符串替换而非 f-string 避免语法冲突
    html = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Task</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<script src='https://libtl.com/sdk.js' data-zone='10489957' data-sdk='show_10489957'></script>
<style>body{font-family:sans-serif;text-align:center;padding:20px;background:#f4f4f9}.btn{padding:15px;background:#0088cc;color:white;border:none;border-radius:8px;width:100%}</style>
</head>
<body>
<h2>📺 观看广告</h2>
<button id="btn" class="btn" onclick="start()">▶️ 开始</button>
<div id="s" style="margin-top:20px"></div>
<script>
const token = "TOKEN_VAL";
const s = document.getElementById('s');
const btn = document.getElementById('btn');
if(window.Telegram && window.Telegram.WebApp) window.Telegram.WebApp.ready();

function start() {
    btn.disabled = true;
    s.innerText = "⏳ 加载中...";
    if (typeof show_10489957 === 'function') {
        show_10489957().then(() => {
            s.innerText = "✅ 验证中...";
            fetch('/api/verify_ad', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({token: token})
            }).then(r => r.json()).then(d => {
                if(d.success) {
                    s.innerHTML = "🎉 成功! +"+d.points+"分";
                    setTimeout(() => {
                        if(window.Telegram && window.Telegram.WebApp) window.Telegram.WebApp.close();
                        else window.close();
                    }, 2000);
                } else {
                    s.innerText = "❌ " + d.message;
                    btn.disabled = false;
                }
            }).catch(e => { s.innerText = "❌ 网络错误"; btn.disabled = false; });
        }).catch(e => { 
            console.log(e);
            s.innerText = "❌ 广告失败: " + e; 
            btn.disabled = false; 
        });
    } else {
        s.innerText = "❌ SDK Error";
        btn.disabled = false;
    }
}
</script>
</body>
</html>
"""
    return HTMLResponse(content=html.replace("TOKEN_VAL", token))

@app.post("/api/verify_ad")
async def vad(p: dict):
    uid = verify_token(p.get("token"))
    if not uid: return JSONResponse({"success": False, "message": "Expired"})
    
    # 修复：加分后主动推送消息
    res = process_ad_reward(uid)
    if res["status"] == "success":
        try:
            await bot_app.bot.send_message(chat_id=uid, text=f"🎉 **恭喜！** 观看完成，获得 {res['added']} 积分！", parse_mode='Markdown')
        except:
            pass
    return JSONResponse({"success": True, "points": res.get("added", 0), "message": res.get("status")})

@app.get("/jump")
async def jump(type: int = 1):
    i = get_system_keys_info()
    u = DIRECT_LINK_1 if type == 1 else DIRECT_LINK_2
    
    # 修复：获取完整目标链接，防止拼接
    raw_target = i[1] if type == 1 else i[3]
    target = raw_target if raw_target.startswith("http") else "https://" + raw_target
    
    html = """
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>跳转中</title></head>
<body>
<h2 style="text-align:center">🚀 跳转中...</h2>
<iframe src="AD_URL" style="width:1px;height:1px;opacity:0;border:none"></iframe>
<script>
setTimeout(() => window.location.href = "TARGET_URL", 3000);
</script>
</body>
</html>
"""
    return HTMLResponse(content=html.replace("AD_URL", u).replace("TARGET_URL", target))

@app.get("/ad_success")
async def success_page(points: int = 0):
    return HTMLResponse(content=f"<html><body><h1>🎉 成功! +{points}分</h1></body></html>")

@app.get("/test_page")
async def test_page():
    return HTMLResponse(content="<html><body><h1>Test Page</h1></body></html>")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
