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

# Web Server & Scheduler Imports
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Telegram Imports
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
# 🛠️ 【配置区域】 请在此处填入您上传图片后获得的 File ID
# ==============================================================================
CONFIG = {
    # 1. 首页 /start -> 开始验证 -> VIP说明配图
    "START_VIP_INFO": "AgACAgEAAxkBAAIC...", 
    
    # 2. 首页 -> 点击"我已付款" -> 查找订单号教程图
    "START_TUTORIAL": "AgACAgEAAxkBAAIC...",
    
    # 3. 积分 /jf -> 点击"微信充值" -> 出现的 微信支付二维码
    "WX_PAY_QR": "AgACAgEAAxkBAAIC...",
    
    # 4. 积分 -> 微信充值 -> 点击"我已支付" -> 出现的 微信账单查找交易单号教程图
    "WX_ORDER_TUTORIAL": "AgACAgEAAxkBAAIC...",
    
    # 5. 积分 /jf -> 点击"支付宝充值" -> 出现的 支付宝支付二维码
    "ALI_PAY_QR": "AgACAgEAAxkBAAIC...",
    
    # 6. 积分 -> 支付宝充值 -> 点击"我已支付" -> 出现的 支付宝账单查找商家订单号教程图
    "ALI_ORDER_TUTORIAL": "AgACAgEAAxkBAAIC...",
}

# ==============================================================================
# 环境变量配置
# ==============================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

# 核心修复：自动清洗 Railway 域名，防止 404
raw_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
RAILWAY_DOMAIN = raw_domain.replace("https://", "").replace("http://", "").strip("/")

# Moontag 直链配置 (用于密钥中转页隐形加载)
DIRECT_LINK_1 = "https://otieu.com/4/10489994"
DIRECT_LINK_2 = "https://otieu.com/4/10489998"

# ==============================================================================
# 日志与全局变量
# ==============================================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

tz_bj = pytz.timezone('Asia/Shanghai')
scheduler = AsyncIOScheduler(timezone=tz_bj)
bot_app = None

# --- 状态机状态定义 ---
# 管理员上传图片
WAITING_FOR_PHOTO = 1
# 管理员修改密钥链接
WAITING_LINK_1 = 2
WAITING_LINK_2 = 3
# 首页入群验证 (输入订单号)
WAITING_START_ORDER = 10
# 充值验证 (输入订单号)
WAITING_RECHARGE_ORDER = 20
# 管理员: 自定义转发命令
WAITING_CMD_NAME = 30
WAITING_CMD_CONTENT = 31
# 管理员: 商品上架
WAITING_PROD_NAME = 40
WAITING_PROD_PRICE = 41
WAITING_PROD_CONTENT = 42

# ==============================================================================
# 数据库初始化与连接
# ==============================================================================

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """初始化数据库 (包含 V3, V4, V5 及最新逻辑)"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. 基础表: 存储 File ID (V3)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS file_ids_v3 (
            id SERIAL PRIMARY KEY,
            file_id TEXT NOT NULL,
            file_unique_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 2. 用户表 (V3扩展版)
    # 包含：积分、签到、入群验证锁、微信锁、支付宝锁
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users_v3 (
            user_id BIGINT PRIMARY KEY,
            points INTEGER DEFAULT 0,
            last_checkin_date DATE,
            checkin_count INTEGER DEFAULT 0,
            verify_fails INTEGER DEFAULT 0,
            verify_lock TIMESTAMP,
            verify_done BOOLEAN DEFAULT FALSE,
            wx_fails INTEGER DEFAULT 0,
            wx_lock TIMESTAMP,
            wx_done BOOLEAN DEFAULT FALSE,
            ali_fails INTEGER DEFAULT 0,
            ali_lock TIMESTAMP,
            ali_done BOOLEAN DEFAULT FALSE,
            username TEXT
        );
    """)
    
    # 补全字段检查
    columns_to_add = [
        "verify_fails INTEGER DEFAULT 0",
        "verify_lock TIMESTAMP",
        "verify_done BOOLEAN DEFAULT FALSE",
        "wx_fails INTEGER DEFAULT 0",
        "wx_lock TIMESTAMP",
        "wx_done BOOLEAN DEFAULT FALSE",
        "ali_fails INTEGER DEFAULT 0",
        "ali_lock TIMESTAMP",
        "ali_done BOOLEAN DEFAULT FALSE",
        "username TEXT"
    ]
    for col_sql in columns_to_add:
        try:
            cur.execute(f"ALTER TABLE users_v3 ADD COLUMN IF NOT EXISTS {col_sql};")
        except Exception:
            conn.rollback()

    # 3. 视频广告统计表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_ads_v3 (
            user_id BIGINT PRIMARY KEY,
            last_watch_date DATE,
            daily_watch_count INTEGER DEFAULT 0
        );
    """)
    
    # 4. 防作弊 Token 表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ad_tokens_v3 (
            token TEXT PRIMARY KEY,
            user_id BIGINT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    # 5. 系统每日密钥表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS system_keys_v3 (
            id INTEGER PRIMARY KEY,
            key_1 TEXT,
            link_1 TEXT,
            key_2 TEXT,
            link_2 TEXT,
            session_date DATE,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("INSERT INTO system_keys_v3 (id, session_date) VALUES (1, %s) ON CONFLICT (id) DO NOTHING", (date(2000,1,1),))
    
    # 6. 用户密钥点击统计
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_key_clicks_v3 (
            user_id BIGINT PRIMARY KEY,
            click_count INTEGER DEFAULT 0,
            session_date DATE
        );
    """)
    
    # 7. 用户密钥领取记录
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_key_claims_v3 (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            key_val TEXT,
            claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, key_val)
        );
    """)

    # 8. 频道转发库 (V4) - 支持批量内容
    cur.execute("""
        CREATE TABLE IF NOT EXISTS custom_commands_v4 (
            id SERIAL PRIMARY KEY,
            command_name TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS command_contents_v4 (
            id SERIAL PRIMARY KEY,
            command_id INTEGER REFERENCES custom_commands_v4(id) ON DELETE CASCADE,
            file_id TEXT,
            file_type TEXT,
            caption TEXT,
            message_text TEXT,
            sort_order SERIAL
        );
    """)

    # 9. 商品表 (V5)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS products_v5 (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            price INTEGER NOT NULL,
            content_text TEXT,
            content_file_id TEXT,
            content_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    # 10. 用户购买记录 (V5)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_purchases_v5 (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            product_id INTEGER REFERENCES products_v5(id) ON DELETE CASCADE,
            purchase_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, product_id)
        );
    """)
    
    # 11. 积分流水日志 (V5)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS point_logs_v5 (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            change_amount INTEGER NOT NULL,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    conn.commit()
    cur.close()
    conn.close()
    # ==============================================================================
# 业务逻辑函数 (Database Functions)
# ==============================================================================

# --- 辅助函数 ---
def get_session_date():
    """获取当前业务日期 (以北京时间10:00AM为界)"""
    now = datetime.now(tz_bj)
    if now.hour < 10:
        return (now - timedelta(days=1)).date()
    return now.date()

def generate_random_key():
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(10))

def get_file_id(config_key):
    """从配置获取 File ID，如果未配置返回 None"""
    fid = CONFIG.get(config_key)
    return fid if fid and fid.startswith("AgAC") else None

def ensure_user_exists(user_id, username=None):
    """确保用户在数据库中"""
    conn = get_db_connection()
    cur = conn.cursor()
    # 更新用户名，并确保记录存在
    cur.execute("""
        INSERT INTO users_v3 (user_id, username) VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username
    """, (user_id, username))
    # 确保广告表记录存在
    cur.execute("INSERT INTO user_ads_v3 (user_id, daily_watch_count) VALUES (%s, 0) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    conn.commit()
    cur.close()
    conn.close()

# --- 核心风控系统 (锁与验证) ---

def check_lock(user_id, type_prefix):
    """
    检查锁定状态
    type_prefix: 'verify', 'wx', 'ali'
    返回: (fails, lock_until, is_done)
    """
    ensure_user_exists(user_id)
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 动态查询字段
    fields = f"{type_prefix}_fails, {type_prefix}_lock, {type_prefix}_done"
    cur.execute(f"SELECT {fields} FROM users_v3 WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    
    # 返回默认值以防空
    if row:
        return row[0], row[1], row[2]
    return 0, None, False

def update_fail(user_id, type_prefix, current_fails, lock_hours):
    """
    增加失败次数，若达标则锁定
    lock_hours: 锁定小时数
    """
    conn = get_db_connection()
    cur = conn.cursor()
    new_fails = current_fails + 1
    
    if new_fails >= 2:
        # 锁定
        lock_until = datetime.now() + timedelta(hours=lock_hours)
        cur.execute(f"UPDATE users_v3 SET {type_prefix}_fails = %s, {type_prefix}_lock = %s WHERE user_id = %s", (new_fails, lock_until, user_id))
    else:
        # 仅计数
        cur.execute(f"UPDATE users_v3 SET {type_prefix}_fails = %s WHERE user_id = %s", (new_fails, user_id))
        
    conn.commit()
    cur.close()
    conn.close()
    return new_fails

def mark_success(user_id, type_prefix):
    """验证成功：解锁并标记完成"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(f"UPDATE users_v3 SET {type_prefix}_fails = 0, {type_prefix}_lock = NULL, {type_prefix}_done = TRUE WHERE user_id = %s", (user_id,))
    conn.commit()
    cur.close()
    conn.close()

# --- 积分系统 (带日志) ---

def update_points(user_id, amount, reason):
    """统一积分更新接口"""
    conn = get_db_connection()
    cur = conn.cursor()
    # 更新总分
    cur.execute("UPDATE users_v3 SET points = points + %s WHERE user_id = %s RETURNING points", (amount, user_id))
    new_total = cur.fetchone()[0]
    # 记日志
    cur.execute("INSERT INTO point_logs_v5 (user_id, change_amount, reason) VALUES (%s, %s, %s)", (user_id, amount, reason))
    conn.commit()
    cur.close()
    conn.close()
    return new_total

def get_user_data(user_id):
    ensure_user_exists(user_id)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT points, last_checkin_date, checkin_count FROM users_v3 WHERE user_id = %s", (user_id,))
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
    cur.execute("SELECT last_checkin_date, checkin_count FROM users_v3 WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    
    if row[0] == today:
        cur.close(); conn.close()
        return {"status": "already_checked"}
    
    added = 10 if row[1] == 0 else random.randint(3, 8)
    cur.execute("UPDATE users_v3 SET points = points + %s, last_checkin_date = %s, checkin_count = checkin_count + 1 WHERE user_id = %s RETURNING points", (added, today, user_id))
    total = cur.fetchone()[0]
    # 补日志
    cur.execute("INSERT INTO point_logs_v5 (user_id, change_amount, reason) VALUES (%s, %s, '每日签到')", (user_id, added))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "success", "added": added, "total": total}

# --- 商品兑换系统 (V5) ---

def add_product(name, price, text, fid, ftype):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO products_v5 (name, price, content_text, content_file_id, content_type) VALUES (%s, %s, %s, %s, %s)", (name, price, text, fid, ftype))
    conn.commit(); cur.close(); conn.close()

def get_products_list(limit=10, offset=0):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id, name, price FROM products_v5 ORDER BY id DESC LIMIT %s OFFSET %s", (limit, offset))
    rows = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM products_v5")
    total = cur.fetchone()[0]
    cur.close(); conn.close()
    return rows, total

def get_product_details(pid):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id, name, price, content_text, content_file_id, content_type FROM products_v5 WHERE id = %s", (pid,))
    row = cur.fetchone()
    cur.close(); conn.close()
    return row

def delete_product(pid):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM products_v5 WHERE id = %s", (pid,))
    conn.commit(); cur.close(); conn.close()

def check_purchase(user_id, pid):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id FROM user_purchases_v5 WHERE user_id = %s AND product_id = %s", (user_id, pid))
    row = cur.fetchone()
    cur.close(); conn.close()
    return True if row else False

def record_purchase(user_id, pid):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO user_purchases_v5 (user_id, product_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (user_id, pid))
    conn.commit(); cur.close(); conn.close()

# --- 频道转发库 (V4) ---

def add_custom_command(cmd_name):
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("INSERT INTO custom_commands_v4 (command_name) VALUES (%s) RETURNING id", (cmd_name,))
        cid = cur.fetchone()[0]
        conn.commit(); cur.close(); conn.close()
        return cid
    except:
        conn.rollback(); cur.close(); conn.close()
        return None

def add_command_content(cmd_id, file_id, file_type, caption, msg_text):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO command_contents_v4 (command_id, file_id, file_type, caption, message_text) VALUES (%s, %s, %s, %s, %s)", (cmd_id, file_id, file_type, caption, msg_text))
    conn.commit(); cur.close(); conn.close()

def get_command_content(cmd_name):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""
        SELECT c.id, c.file_id, c.file_type, c.caption, c.message_text 
        FROM command_contents_v4 c
        JOIN custom_commands_v4 cmd ON c.command_id = cmd.id
        WHERE cmd.command_name = %s
        ORDER BY c.sort_order ASC
    """, (cmd_name,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows

def get_commands_list(limit, offset):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id, command_name FROM custom_commands_v4 ORDER BY id DESC LIMIT %s OFFSET %s", (limit, offset))
    rows = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM custom_commands_v4")
    total = cur.fetchone()[0]
    cur.close(); conn.close()
    return rows, total

def delete_command_by_id(cmd_id):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM custom_commands_v4 WHERE id = %s", (cmd_id,))
    conn.commit(); cur.close(); conn.close()

# --- 广告 & 密钥 & 其他基础 ---

def get_ad_status(user_id):
    ensure_user_exists(user_id)
    conn = get_db_connection(); cur = conn.cursor()
    today = datetime.now(tz_bj).date()
    cur.execute("SELECT last_watch_date, daily_watch_count FROM user_ads_v3 WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    count = row[1]
    if row[0] != today: count = 0
    cur.close(); conn.close()
    return count

def create_ad_token(user_id):
    t = str(uuid.uuid4()); conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO ad_tokens_v3 (token, user_id) VALUES (%s, %s)", (t, user_id))
    conn.commit(); cur.close(); conn.close()
    return t

def verify_token(t):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM ad_tokens_v3 WHERE token = %s RETURNING user_id", (t,))
    row = cur.fetchone(); conn.commit(); cur.close(); conn.close()
    return row[0] if row else None

def process_ad_reward(user_id):
    ensure_user_exists(user_id)
    count = get_ad_status(user_id)
    if count >= 3: return {"status": "limit_reached"}
    pts = 10 if count == 0 else (6 if count == 1 else random.randint(3, 10))
    # 记录
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE user_ads_v3 SET last_watch_date = %s, daily_watch_count = daily_watch_count + 1 WHERE user_id = %s", (datetime.now(tz_bj).date(), user_id))
    conn.commit(); cur.close(); conn.close()
    # 加分
    update_points(user_id, pts, "观看广告")
    return {"status": "success", "added": pts}

def update_system_keys(k1, k2, d):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE system_keys_v3 SET key_1=%s, key_2=%s, link_1=NULL, link_2=NULL, session_date=%s WHERE id=1", (k1, k2, d))
    conn.commit(); cur.close(); conn.close()

def update_key_links(l1, l2):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE system_keys_v3 SET link_1=%s, link_2=%s WHERE id=1", (l1, l2))
    conn.commit(); cur.close(); conn.close()

def get_system_keys_info():
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT key_1, link_1, key_2, link_2, session_date FROM system_keys_v3 WHERE id = 1")
    row = cur.fetchone(); cur.close(); conn.close()
    return row

def get_user_click_status(user_id):
    s = get_session_date(); conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT click_count, session_date FROM user_key_clicks_v3 WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    if not row or row[1] != s:
        cur.execute("INSERT INTO user_key_clicks_v3 (user_id, click_count, session_date) VALUES (%s, 0, %s) ON CONFLICT (user_id) DO UPDATE SET click_count = 0, session_date = %s", (user_id, s, s))
        conn.commit(); cur.close(); conn.close(); return 0
    cur.close(); conn.close(); return row[0]

def increment_user_click(user_id):
    s = get_session_date(); conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE user_key_clicks_v3 SET click_count = click_count + 1 WHERE user_id = %s AND session_date = %s", (user_id, s))
    conn.commit(); cur.close(); conn.close()

def claim_key_points(user_id, txt):
    ensure_user_exists(user_id); info = get_system_keys_info()
    if not info: return {"status": "error"}
    k1, _, k2, _, _ = info; pts = 0
    if txt.strip() == k1: pts = 8
    elif txt.strip() == k2: pts = 6
    else: return {"status": "invalid"}
    
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id FROM user_key_claims_v3 WHERE user_id = %s AND key_val = %s", (user_id, txt.strip()))
    if cur.fetchone(): cur.close(); conn.close(); return {"status": "already_claimed"}
    
    cur.execute("INSERT INTO user_key_claims_v3 (user_id, key_val) VALUES (%s, %s)", (user_id, txt.strip()))
    conn.commit(); cur.close(); conn.close()
    
    update_points(user_id, pts, "密钥兑换")
    return {"status": "success", "points": pts}

def reset_admin_stats(admin_id):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE user_ads_v3 SET daily_watch_count = 0 WHERE user_id = %s", (admin_id,))
    cur.execute("UPDATE user_key_clicks_v3 SET click_count = 0 WHERE user_id = %s", (admin_id,))
    cur.execute("DELETE FROM user_key_claims_v3 WHERE user_id = %s", (admin_id,))
    cur.execute("DELETE FROM user_purchases_v5 WHERE user_id = %s", (admin_id,))
    cur.execute("""
        UPDATE users_v3 SET 
        verify_fails=0, verify_lock=NULL, verify_done=FALSE,
        wx_fails=0, wx_lock=NULL, wx_done=FALSE,
        ali_fails=0, ali_lock=NULL, ali_done=FALSE
        WHERE user_id = %s
    """, (admin_id,))
    conn.commit(); cur.close(); conn.close()

def get_all_users_info(limit, offset):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT user_id, username, points FROM users_v3 ORDER BY points DESC LIMIT %s OFFSET %s", (limit, offset))
    rows = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM users_v3")
    total = cur.fetchone()[0]
    cur.close(); conn.close()
    return rows, total

def save_file_id(fid, uid):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO file_ids_v3 (file_id, file_unique_id) VALUES (%s, %s)", (fid, uid))
    conn.commit(); cur.close(); conn.close()

def get_all_files():
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id, file_id FROM file_ids_v3 ORDER BY id DESC LIMIT 10")
    rows = cur.fetchall(); cur.close(); conn.close()
    return rows

def delete_file_by_id(did):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM file_ids_v3 WHERE id = %s", (did,))
    conn.commit(); cur.close(); conn.close()
    # --- 定时删除消息任务 (5分钟) ---
async def delete_messages_task(chat_id, message_ids):
    """5分钟后删除消息"""
    try:
        # 等待 5 分钟 (300秒)
        await asyncio.sleep(300)
        
        # 删除所有机器人发出的消息
        for msg_id in message_ids:
            try:
                await bot_app.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception as e:
                logger.warning(f"Delete msg failed: {e}")
        
        # 发送提示并跳转
        text = "⏳ **消息存在时间有限，已自动销毁。**\n\n请到购买处重新获取（已购买不需要二次付费）。"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎁 前往兑换中心", callback_data="go_exchange")],
            [InlineKeyboardButton("🏠 返回首页", callback_data="back_to_home")]
        ])
        await bot_app.bot.send_message(chat_id=chat_id, text=text, reply_markup=kb, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Auto delete task error: {e}")

# --- 普通 Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_exists(user.id, user.username)
    
    # 入群验证锁
    row = check_lock(user.id, 'verify')
    fails, lock_until, is_done = row if row else (0, None, False)
    
    verify_text, verify_cb = "🚀 开始验证", "start_verify_flow"
    
    if is_done:
        verify_text, verify_cb = "✅ 已加入会员群", "noop_verify_done"
    elif lock_until and datetime.now() < lock_until:
        rem = lock_until - datetime.now()
        verify_text = f"🚫 验证锁定 ({int(rem.seconds//3600)}h)", "locked_verify"
        verify_cb = "locked_verify"

    text = "👋 欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~\n\n📢 小卫小卫，守门员小卫！\n一键入群，小卫帮你搞定！"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(verify_text, callback_data=verify_cb)],
        [InlineKeyboardButton("💰 积分 & 兑换", callback_data="my_points")],
        [InlineKeyboardButton("🎉 开业活动", callback_data="open_activity")]
    ])
    
    if update.callback_query:
        if update.callback_query.data == "locked_verify": await update.callback_query.answer("⛔️ 请稍后再试。", show_alert=True); return
        if update.callback_query.data == "noop_verify_done": await update.callback_query.answer("✅ 您已完成验证，无需重复。", show_alert=True); return
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    else: await update.message.reply_text(text, reply_markup=kb)

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
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
    else: await update.message.reply_text(text, reply_markup=kb, parse_mode='Markdown')

async def view_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); uid = update.effective_user.id
    data = get_user_data(uid); logs = get_point_logs(uid, 10)
    log_text = "\n".join([f"• {l[2].strftime('%m-%d %H:%M')} | {l[1]:+d} | {l[0]}" for l in logs]) if logs else "暂无记录"
    text = f"💳 **账户余额**\n\n💎 总积分：`{data[0]}`\n\n📝 **最近记录：**\n{log_text}"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="my_points")]]), parse_mode='Markdown')

async def recharge_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    uid = update.effective_user.id
    _, wx_l, wx_d = check_lock(uid, 'wx')
    _, ali_l, ali_d = check_lock(uid, 'ali')
    
    # 充值锁改为 3 小时
    wx_t, wx_c = ("✅ 微信已充", "noop_done") if wx_d else (("🚫 3小时冷却", "noop_lock") if wx_l and datetime.now()<wx_l else ("💚 微信充值", "pay_wx"))
    ali_t, ali_c = ("✅ 支付宝已充", "noop_done") if ali_d else (("🚫 3小时冷却", "noop_lock") if ali_l and datetime.now()<ali_l else ("💙 支付宝充值", "pay_ali"))
    
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(wx_t, callback_data=wx_c), InlineKeyboardButton(ali_t, callback_data=ali_c)], [InlineKeyboardButton("🔙 返回", callback_data="my_points")]])
    await query.edit_message_text("💎 **充值中心**\n每种方式限充 1 次。", reply_markup=kb, parse_mode='Markdown')

async def noop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("✅ 已完成" if "done" in q.data else "⛔️ 暂时锁定", show_alert=True)

# --- Admin Handlers (基础) ---
async def admin_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 获取 File ID", callback_data="start_upload")],
        [InlineKeyboardButton("📂 管理图片", callback_data="view_files")],
        [InlineKeyboardButton("📚 频道转发库 (添加/管理)", callback_data="manage_cmds_entry")]
    ])
    await update.message.reply_text("⚙️ **管理员后台**", reply_markup=kb, parse_mode='Markdown')
    return ConversationHandler.END

# --- Admin Custom Commands (V4) ---

async def manage_cmds_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ 添加新命令", callback_data="add_new_cmd")],
        [InlineKeyboardButton("📂 管理/删除命令", callback_data="list_cmds_0")],
        [InlineKeyboardButton("🛍 商品管理 (上架/下架)", callback_data="manage_products_entry")],
        [InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")]
    ])
    await query.edit_message_text("📚 **内容管理**", reply_markup=kb, parse_mode='Markdown')

# 添加命令流程
async def add_cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    await query.edit_message_text("📝 **请输入自定义命令**\n(例如：`资源1`)", parse_mode='Markdown')
    return WAITING_CMD_NAME

async def receive_cmd_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd_name = update.message.text.strip()
    cmd_id = add_custom_command(cmd_name)
    
    if not cmd_id:
        await update.message.reply_text("❌ 命令已存在。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="manage_cmds_entry")]]))
        return ConversationHandler.END
        
    context.user_data['ccd'] = cmd_id
    context.user_data['ccn'] = cmd_name
    
    await update.message.reply_text(
        f"✅ 命令 `{cmd_name}` 已创建。\n\n"
        "👇 **请发送要绑定的内容 (支持批量)**\n"
        "支持：文本、图片、视频、文件。\n"
        "发送完毕后，请点击【我已完成绑定】。",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 我已完成绑定", callback_data="finish_cmd_bind")]]),
        parse_mode='Markdown'
    )
    return WAITING_CMD_CONTENT

async def receive_cmd_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd_id = context.user_data.get('ccd')
    msg = update.message
    
    fid, ftype, txt = None, 'text', msg.text or msg.caption
    if msg.photo: fid=msg.photo[-1].file_id; ftype='photo'
    elif msg.video: fid=msg.video.file_id; ftype='video'
    elif msg.document: fid=msg.document.file_id; ftype='document'
    
    add_command_content(cmd_id, fid, ftype, msg.caption, txt)
    return WAITING_CMD_CONTENT

async def finish_cmd_bind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    name = context.user_data.get('ccn', '')
    await query.edit_message_text(f"🎉 **{name} 绑定完成！**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回管理菜单", callback_data="manage_cmds_entry")]]))
    return ConversationHandler.END

# 管理/删除流程
async def list_cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    offset = int(query.data.split('_')[-1])
    rows, total = get_commands_list(limit=10, offset=offset)
    
    if not rows:
        await query.edit_message_text("📭 暂无自定义命令。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="manage_cmds_entry")]]))
        return
        
    kb = []
    for r in rows: kb.append([InlineKeyboardButton(f"🗑 删除 {r[1]}", callback_data=f"ask_del_cmd_{r[0]}")])
    
    nav = []
    if offset > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"list_cmds_{offset-10}"))
    if offset + 10 < total: nav.append(InlineKeyboardButton("➡️", callback_data=f"list_cmds_{offset+10}"))
    if nav: kb.append(nav)
    kb.append([InlineKeyboardButton("🔙 返回", callback_data="manage_cmds_entry")])
    
    await query.edit_message_text(f"📂 **命令列表 ({offset//10 + 1})**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def ask_del_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); cmd_id = int(query.data.split('_')[-1])
    await query.edit_message_text(f"⚠️ **确定删除吗？**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 确认", callback_data=f"confirm_del_cmd_{cmd_id}"), InlineKeyboardButton("❌ 取消", callback_data="manage_cmds_entry")]]), parse_mode='Markdown')

async def confirm_del_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); cmd_id = int(query.data.split('_')[-1])
    delete_command_by_id(cmd_id)
    await query.edit_message_text("🗑 **已删除。**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回列表", callback_data="list_cmds_0")]]))

# --- 用户触发逻辑 (V4 核心) ---

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    if not text or text.startswith('/'): return

    # 1. 检查是否为自定义命令
    contents = get_command_content(text.strip())
    
    if contents:
        sent_msg_ids = []
        chat_id = update.effective_chat.id
        try: await update.message.delete()
        except: pass
        
        # 分组发送 (每10条)
        chunk_size = 10
        for i in range(0, len(contents), chunk_size):
            chunk = contents[i:i + chunk_size]
            media_group = []
            for item in chunk:
                # item: id, file_id, file_type, caption, text
                if item[2] == 'photo': media_group.append(InputMediaPhoto(media=item[1], caption=item[3]))
                elif item[2] == 'video': media_group.append(InputMediaVideo(media=item[1], caption=item[3]))
            
            if len(media_group) == len(chunk) and len(media_group) > 1:
                try:
                    msgs = await context.bot.send_media_group(chat_id=chat_id, media=media_group)
                    sent_msg_ids.extend([m.message_id for m in msgs])
                except: pass # 降级逻辑略
            else:
                for item in chunk:
                    try:
                        if item[2] == 'text': m = await context.bot.send_message(chat_id, item[4])
                        elif item[2] == 'photo': m = await context.bot.send_photo(chat_id, item[1], caption=item[3])
                        elif item[2] == 'video': m = await context.bot.send_video(chat_id, item[1], caption=item[3])
                        elif item[2] == 'document': m = await context.bot.send_document(chat_id, item[1], caption=item[3])
                        sent_msg_ids.append(m.message_id)
                    except: pass

        # 发送完成提示 & 启动删除任务
        success_msg = await context.bot.send_message(chat_id, "✅ **信息已发送。**\n正在为您跳转...", parse_mode='Markdown')
        sent_msg_ids.append(success_msg.message_id)
        
        asyncio.create_task(delete_messages_task(chat_id, sent_msg_ids))
        
        await asyncio.sleep(2)
        await dh_command(update, context) # 跳转到兑换页
        return

    # 2. 检查密钥
    result = claim_key_points(user.id, text)
    if result["status"] == "success":
        await update.message.reply_text(f"✅ **成功！** +{result['points']}分", parse_mode='Markdown')
    elif result["status"] == "already_claimed":
        await update.message.reply_text("⚠️ 密钥已使用。")
    else:
        await start(update, context)
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
        btn_text = f"✅ {r[1]} (已兑换)" if is_bought else f"🎁 {r[1]} ({r[2]}积分)"
        callback = f"view_bought_{r[0]}" if is_bought else f"confirm_buy_{r[0]}"
        kb.append([InlineKeyboardButton(btn_text, callback_data=callback)])
        
    # 翻页
    nav = []
    if offset > 0: nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"list_prod_{offset-10}"))
    if offset + 10 < total: nav.append(InlineKeyboardButton("➡️ 下一页", callback_data=f"list_prod_{offset+10}"))
    if nav: kb.append(nav)
    
    kb.append([InlineKeyboardButton("💰 查看余额 & 记录", callback_data="view_balance")])
    kb.append([InlineKeyboardButton("🔙 返回首页", callback_data="back_to_home")])
    
    text = "🎁 **积分兑换中心**\n请选择您要兑换的商品："
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def exchange_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理购买确认与发货"""
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = update.effective_user.id
    
    # 1. 测试商品
    if data == "confirm_buy_test":
        await query.edit_message_text("❓ **确认兑换**\n商品：测试商品\n价格：0 积分", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 确认", callback_data="do_buy_test"), InlineKeyboardButton("❌ 取消", callback_data="list_prod_0")]]), parse_mode='Markdown')
        return
    elif data == "do_buy_test":
        await query.edit_message_text("🎉 **兑换成功！**\n内容：哈哈", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回兑换列表", callback_data="list_prod_0")]]), parse_mode='Markdown')
        return

    # 2. 真实商品
    pid = int(data.split("_")[-1])
    
    # 查看已购
    if "view_bought_" in data:
        prod = get_product_details(pid)
        if not prod: await query.answer("商品已下架", show_alert=True); return
        content = prod[3] or "无文本"
        fid = prod[4]; ftype = prod[5]
        
        await query.message.reply_text(f"📦 **已购内容：**\n{content}", parse_mode='Markdown')
        if fid:
            try:
                if ftype=='photo': await context.bot.send_photo(uid, fid)
                elif ftype=='video': await context.bot.send_video(uid, fid)
            except: pass
        return

    # 确认购买
    if "confirm_buy_" in data:
        prod = get_product_details(pid)
        if not prod: await query.answer("商品已下架", show_alert=True); return
        price = prod[2]
        await query.edit_message_text(f"❓ **确认兑换**\n商品：{prod[1]}\n价格：{price} 积分", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 确认支付", callback_data=f"do_buy_{pid}"), InlineKeyboardButton("❌ 取消", callback_data="list_prod_0")]]), parse_mode='Markdown')
        return

    # 执行购买
    if "do_buy_" in data:
        prod = get_product_details(pid)
        if not prod: await query.answer("商品已下架", show_alert=True); return
        price = prod[2]
        
        user_pts = get_user_data(uid)[0]
        if user_pts < price:
            await query.edit_message_text("❌ **余额不足！**\n请充值或赚取更多积分。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="list_prod_0")]]), parse_mode='Markdown')
            return
            
        # 扣分 & 记录
        update_points(uid, -price, f"兑换-{prod[1]}")
        record_purchase(uid, pid)
        
        # 发货
        await query.message.reply_text(f"🎉 **兑换成功！**\n消耗 {price} 积分。\n\n📦 **内容：**\n{prod[3] or ''}", parse_mode='Markdown')
        if prod[4]:
            try:
                if prod[5]=='photo': await context.bot.send_photo(uid, prod[4])
                elif prod[5]=='video': await context.bot.send_video(uid, prod[4])
            except: pass
            
        await asyncio.sleep(1)
        await dh_command(update, context) # 刷新列表

# --- Admin Products (V5) ---

async def manage_products_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ 上架新商品", callback_data="add_product_start")],
        [InlineKeyboardButton("📂 管理/下架商品", callback_data="list_admin_prods_0")],
        [InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")]
    ])
    await query.edit_message_text("🛍 **商品管理**", reply_markup=kb, parse_mode='Markdown')

# 添加商品流程
async def add_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    await query.edit_message_text("📝 请输入 **商品名称**：", parse_mode='Markdown'); return WAITING_PROD_NAME

async def receive_prod_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['p_name'] = update.message.text
    await update.message.reply_text("💰 请输入 **兑换价格** (数字)："); return WAITING_PROD_PRICE

async def receive_prod_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: context.user_data['p_price'] = int(update.message.text)
    except: await update.message.reply_text("❌ 必须是数字，请重试："); return WAITING_PROD_PRICE
    await update.message.reply_text("📦 请发送 **商品内容** (文本/图片/视频)："); return WAITING_PROD_CONTENT

async def receive_prod_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    fid, ftype, txt = None, 'text', msg.text or msg.caption
    if msg.photo: fid=msg.photo[-1].file_id; ftype='photo'
    elif msg.video: fid=msg.video.file_id; ftype='video'
    
    add_product(context.user_data['p_name'], context.user_data['p_price'], txt, fid, ftype)
    await update.message.reply_text("✅ **商品上架成功！**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="manage_products_entry")]]), parse_mode='Markdown')
    return ConversationHandler.END

# 删除商品
async def list_admin_prods(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    offset = int(query.data.split("_")[-1])
    rows, total = get_products_list(10, offset)
    kb = []
    for r in rows: kb.append([InlineKeyboardButton(f"🗑 下架 {r[1]}", callback_data=f"ask_del_prod_{r[0]}")])
    nav = []
    if offset > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"list_admin_prods_{offset-10}"))
    if offset+10 < total: nav.append(InlineKeyboardButton("➡️", callback_data=f"list_admin_prods_{offset+10}"))
    if nav: kb.append(nav)
    kb.append([InlineKeyboardButton("🔙 返回", callback_data="manage_products_entry")])
    await query.edit_message_text(f"🛍 **商品列表 ({offset//10+1})**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def ask_del_prod(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); pid = int(query.data.split("_")[-1])
    await query.edit_message_text(f"⚠️ 确认下架商品 ID {pid}?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 确认", callback_data=f"confirm_del_prod_{pid}"), InlineKeyboardButton("❌ 取消", callback_data="list_admin_prods_0")]]))

async def confirm_del_prod(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); pid = int(query.data.split("_")[-1])
    delete_product(pid)
    await query.edit_message_text("🗑 已下架。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="manage_products_entry")]]))

# Admin User List
async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    rows, _ = get_all_users_info(20, 0)
    msg = "👥 **用户列表 (Top 20)**\n\n"
    for r in rows: msg += f"ID: `{r[0]}` | 名: {r[1] or '无'} | 分: {r[2]}\n"
    await update.message.reply_text(msg, parse_mode='Markdown')

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
    
    # 1. Conversations (Priority High)
    
    verify_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(verify_entry, pattern="^start_verify_flow$")],
        states={WAITING_START_ORDER: [CallbackQueryHandler(ask_start_order, pattern="^paid_start$"), MessageHandler(filters.TEXT & ~filters.COMMAND, check_start_order)]},
        fallbacks=[CommandHandler("start", start)], per_message=False
    )
    
    recharge_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(recharge_menu, pattern="^go_recharge$"), CallbackQueryHandler(recharge_entry, pattern="^pay_wx|pay_ali$")],
        states={WAITING_RECHARGE_ORDER: [CallbackQueryHandler(ask_recharge_order, pattern="^paid_recharge$"), MessageHandler(filters.TEXT & ~filters.COMMAND, check_recharge_order)]},
        fallbacks=[CommandHandler("jf", jf_command_handler)], per_message=False
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
    
    cmd_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_cmd_start, pattern="^add_new_cmd$")],
        states={
            WAITING_CMD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_cmd_name)],
            WAITING_CMD_CONTENT: [MessageHandler(filters.ALL & ~filters.COMMAND, receive_cmd_content), CallbackQueryHandler(finish_cmd_bind, pattern="^finish_cmd_bind$")]
        },
        fallbacks=[CallbackQueryHandler(manage_cmds_entry, pattern="^manage_cmds_entry$")], per_message=False
    )
    
    key_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_edit_links, pattern="^edit_links$")],
        states={
            WAITING_LINK_1: [MessageHandler(filters.TEXT, receive_link_1)],
            WAITING_LINK_2: [MessageHandler(filters.TEXT, receive_link_2)]
        },
        fallbacks=[CommandHandler("cancel", cancel_admin)]
    )
    
    admin_up_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_upload_flow, pattern="^start_upload$")],
        states={WAITING_FOR_PHOTO: [MessageHandler(filters.PHOTO, handle_photo_upload), CallbackQueryHandler(admin_entry, pattern="^back_to_admin$")]},
        fallbacks=[CommandHandler("admin", admin_entry)]
    )

    bot_app.add_handler(verify_conv)
    bot_app.add_handler(recharge_conv)
    bot_app.add_handler(prod_conv)
    bot_app.add_handler(cmd_conv)
    bot_app.add_handler(key_conv)
    bot_app.add_handler(admin_up_conv)
    
    # 2. Handlers
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
    
    # Admin
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
    
    # Text Matcher (Last)
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    
    yield
    if bot_app: await bot_app.stop(); await bot_app.shutdown()
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def health(): return {"status": "ok"}

@app.get("/watch_ad/{token}", response_class=HTMLResponse)
async def wad(token: str):
    html = f"""
    <!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>视频任务</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <script src='https://libtl.com/sdk.js' data-zone='10489957' data-sdk='show_10489957'></script>
    <style>body{{font-family:sans-serif;text-align:center;padding:20px;background:#f4f4f9;display:flex;flex-direction:column;justify-content:center;height:90vh}}.container{{max-width:500px;margin:0 auto;background:white;padding:30px;border-radius:12px;box-shadow:0 4px 15px rgba(0,0,0,0.1)}}.btn{{padding:15px 30px;background:#0088cc;color:white;border:none;border-radius:8px;font-size:18px;cursor:pointer;width:100%}}.btn:disabled{{background:#ccc}}#status{{margin-top:20px;font-size:16px;color:#555}}.progress{{width:100%;background-color:#ddd;border-radius:5px;margin-top:15px;height:10px;display:none}}.bar{{width:0%;height:100%;background-color:#4CAF50;border-radius:5px;transition:width 1s linear}}</style></head>
    <body><div class="container"><h2>📺 观看广告获取积分</h2><p style="color:#666;margin-bottom:25px">请点击下方按钮，保持页面开启 15 秒。</p><button id="adBtn" class="btn" onclick="startProcess()">▶️ 开始观看</button><div class="progress" id="progress"><div class="bar" id="bar"></div></div><div id="status"></div></div>
    <script>const token="{token}",s=document.getElementById('status'),btn=document.getElementById('adBtn'),bar=document.getElementById('bar'),p=document.getElementById('progress');if(window.Telegram&&window.Telegram.WebApp)window.Telegram.WebApp.ready();function startProcess(){{btn.disabled=!0;s.innerText="⏳ 正在加载...";if(typeof show_10489957==='function')show_10489957().catch(e=>console.log(e));s.innerText="📺 广告观看中...";p.style.display='block';let t=15;const timer=setInterval(()=>{{t--;bar.style.width=((15-t)/15)*100+"%";if(t<=0){{clearInterval(timer);v();}}else{{s.innerText="📺 剩余: "+t+"秒";}}}},1000)}}function v(){{s.innerText="✅ 正在验证...";fetch('/api/verify_ad',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{token:token}})}}).then(r=>r.json()).then(d=>{{if(d.success)window.location.href="/ad_success?points="+d.points;else{{s.innerText="❌ "+d.message;btn.disabled=!1}}}}).catch(e=>{{s.innerText="❌ 网络错误";btn.disabled=!1}})}}</script></body></html>
    """
    return HTMLResponse(content=html)

@app.get("/ad_success", response_class=HTMLResponse)
async def success_page(points: int = 0):
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>成功</title><script src="https://telegram.org/js/telegram-web-app.js"></script><style>body{{font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;background-color:#e8f5e9;text-align:center;margin:0}}.card{{background:white;padding:40px;border-radius:15px;box-shadow:0 4px 20px rgba(0,0,0,0.1)}}h1{{color:#2e7d32}}p{{font-size:18px;color:#555}}.score{{font-size:40px;font-weight:bold;color:#f57c00;display:block;margin:20px 0}}</style></head><body><div class="card"><h1>🎉 观看成功！</h1><p>获得奖励</p><span class="score">+{points} 积分</span><p style="font-size:14px;color:#999">页面将自动关闭...</p></div><script>setTimeout(()=>{{if(window.Telegram&&window.Telegram.WebApp)window.Telegram.WebApp.close();else window.close()}},2500)</script></body></html>"""
    return HTMLResponse(content=html)

@app.get("/test_page", response_class=HTMLResponse)
async def test_page():
    return HTMLResponse(content="""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>测试</title><script src="https://telegram.org/js/telegram-web-app.js"></script><style>body{font-family:sans-serif;text-align:center;padding:20px;background:#fff3e0;display:flex;flex-direction:column;justify-content:center;height:90vh}.container{background:white;padding:30px;border-radius:12px;box-shadow:0 4px 10px rgba(0,0,0,0.1)}.btn{padding:15px 30px;background:#ff9800;color:white;border:none;border-radius:8px;font-size:18px;cursor:pointer;width:100%}.btn:disabled{background:#ccc}#status{margin-top:20px;font-weight:bold;color:#555}</style></head><body><div class="container"><h2>🛠 测试模式</h2><p>简陋测试页。</p><button id="btn" class="btn" onclick="startTest()">🖱 点击测试</button><div id="status"></div></div><script>function startTest(){const btn=document.getElementById('btn'),s=document.getElementById('status');btn.disabled=!0;let c=3;const t=setInterval(()=>{c--;if(c<=0){clearInterval(t);s.innerText="✅ 模拟成功! 跳转中...";setTimeout(()=>{window.location.href="/ad_success?points=0"},1000)}else{s.innerText="⏳ "+c}},1000)}</script></body></html>""")

@app.post("/api/verify_ad")
async def verify_ad_api(payload: dict):
    user_id = verify_token(payload.get("token"))
    if not user_id: return JSONResponse({"success": False, "message": "Expired"})
    res = process_ad_reward(user_id)
    return JSONResponse({"success": res["status"]=="success", "points": res.get("added"), "message": res.get("status")})

@app.get("/jump", response_class=HTMLResponse)
async def jump_page(request: Request, type: int = 1):
    info = get_system_keys_info()
    if not info: return HTMLResponse("<h1>System Error</h1>")
    target = info[1] if type == 1 else info[3]
    if not target: return HTMLResponse("<h1>Wait Admin...</h1>")
    ad_url = DIRECT_LINK_1 if type == 1 else DIRECT_LINK_2
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>跳转中...</title><style>body{{font-family:Arial,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;background:#f0f2f5;margin:0}}.card{{background:white;padding:30px;border-radius:12px;text-align:center;box-shadow:0 4px 12px rgba(0,0,0,0.1)}}.loader{{border:4px solid #f3f3f3;border-top:4px solid #3498db;border-radius:50%;width:30px;height:30px;animation:spin 1s linear infinite;margin:20px auto}}@keyframes spin{{0%{{transform:rotate(0deg)}}100%{{transform:rotate(360deg)}}}}</style></head><body><div class="card"><h2>🚀 获取密钥中...</h2><div class="loader"></div><p id="msg">3 秒后跳转...</p></div><iframe src="{ad_url}" style="width:1px;height:1px;opacity:0;position:absolute;border:none"></iframe><script>let c=3;const m=document.getElementById('msg'),t="{target}";setInterval(()=>{{c--;if(c>0)m.innerText=c+" 秒后跳转...";else{{m.innerText="正在跳转...";window.location.href=t}}}},1000)</script></body></html>"""
    return HTMLResponse(content=html)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
