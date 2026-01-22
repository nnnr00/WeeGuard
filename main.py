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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)

# ==============================================================================
# 🛠️ 【配置区域】 请在此处填入您上传图片后获得的 File ID
# ==============================================================================
CONFIG = {
    # 1. 首页 /start -> 点击"开始验证" -> 出现的 VIP特权说明图
    "START_VIP_INFO": "AgACAgEAAxkBAAIC...", 
    
    # 2. 首页 -> 点击"我已付款" -> 出现的 查找订单号教程图 (入群验证用)
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

# Moontag 直链配置 (用于中转页隐形加载)
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
# 管理员修改链接
WAITING_LINK_1 = 2
WAITING_LINK_2 = 3
# 首页入群验证 (输入订单号)
WAITING_START_ORDER = 10
# 充值验证 (输入订单号)
WAITING_RECHARGE_ORDER = 20

# ==============================================================================
# 数据库操作逻辑
# ==============================================================================

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """初始化数据库 (V3版 + 充值字段扩充)"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. 基础表: 存储 File ID
    cur.execute("""
        CREATE TABLE IF NOT EXISTS file_ids_v3 (
            id SERIAL PRIMARY KEY,
            file_id TEXT NOT NULL,
            file_unique_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 2. 用户表: 包含积分、签到、以及各种验证锁
    # verify_fails / verify_lock: 首页入群验证的失败次数和锁定时间
    # wx_fails / wx_lock / wx_done: 微信充值相关
    # ali_fails / ali_lock / ali_done: 支付宝充值相关
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users_v3 (
            user_id BIGINT PRIMARY KEY,
            points INTEGER DEFAULT 0,
            last_checkin_date DATE,
            checkin_count INTEGER DEFAULT 0,
            verify_fails INTEGER DEFAULT 0,
            verify_lock TIMESTAMP,
            wx_fails INTEGER DEFAULT 0,
            wx_lock TIMESTAMP,
            wx_done BOOLEAN DEFAULT FALSE,
            ali_fails INTEGER DEFAULT 0,
            ali_lock TIMESTAMP,
            ali_done BOOLEAN DEFAULT FALSE
        );
    """)
    
    # 尝试补全可能缺失的字段 (防止旧表报错)
    columns_to_add = [
        "verify_fails INTEGER DEFAULT 0",
        "verify_lock TIMESTAMP",
        "wx_fails INTEGER DEFAULT 0",
        "wx_lock TIMESTAMP",
        "wx_done BOOLEAN DEFAULT FALSE",
        "ali_fails INTEGER DEFAULT 0",
        "ali_lock TIMESTAMP",
        "ali_done BOOLEAN DEFAULT FALSE"
    ]
    for col_sql in columns_to_add:
        try:
            cur.execute(f"ALTER TABLE users_v3 ADD COLUMN IF NOT EXISTS {col_sql};")
        except Exception:
            conn.rollback()

    # 3. 广告统计表
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
    # 插入默认行
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
    
    conn.commit()
    cur.close()
    conn.close()

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
    """从配置字典中安全获取 File ID"""
    fid = CONFIG.get(config_key)
    # 简单校验一下是不是 Telegram 的 File ID 格式 (通常以 AgAC 开头)
    if fid and fid.startswith("AgAC"):
        return fid
    return None

# --- 数据库业务函数 ---

def ensure_user_exists(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO users_v3 (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    cur.execute("INSERT INTO user_ads_v3 (user_id, daily_watch_count) VALUES (%s, 0) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    conn.commit()
    cur.close()
    conn.close()

def check_lock_status(user_id, type_prefix):
    """
    检查某种操作的锁定状态
    type_prefix: 'verify' (首页验证), 'wx' (微信充值), 'ali' (支付宝充值)
    返回: (fails, lock_until, is_done)
    """
    ensure_user_exists(user_id)
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 动态构建 SQL，根据前缀查询对应的字段
    fields = f"{type_prefix}_fails, {type_prefix}_lock"
    # 如果是充值类型，还需要查询是否已完成
    if type_prefix in ['wx', 'ali']:
        fields += f", {type_prefix}_done"
    
    cur.execute(f"SELECT {fields} FROM users_v3 WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    
    fails = row[0] if row else 0
    lock_until = row[1] if row else None
    is_done = row[2] if row and len(row) > 2 else False
    
    return fails, lock_until, is_done

def update_fail_count(user_id, type_prefix, current_fails, lock_hours):
    """
    更新失败次数，如果达到2次则锁定
    lock_hours: 锁定几小时 (入群5小时，充值10小时)
    """
    conn = get_db_connection()
    cur = conn.cursor()
    new_fails = current_fails + 1
    
    if new_fails >= 2:
        # 达到限制，写入锁定时间
        lock_until = datetime.now() + timedelta(hours=lock_hours)
        cur.execute(f"UPDATE users_v3 SET {type_prefix}_fails = %s, {type_prefix}_lock = %s WHERE user_id = %s", (new_fails, lock_until, user_id))
    else:
        # 还没到限制，只更新次数
        cur.execute(f"UPDATE users_v3 SET {type_prefix}_fails = %s WHERE user_id = %s", (new_fails, user_id))
        
    conn.commit()
    cur.close()
    conn.close()
    return new_fails

def mark_success_and_unlock(user_id, type_prefix, points_to_add=0):
    """
    验证成功：清除锁、清除失败次数、标记已完成、加分
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 构建 SQL
    sql_parts = [f"{type_prefix}_fails = 0", f"{type_prefix}_lock = NULL"]
    
    if type_prefix in ['wx', 'ali']:
        sql_parts.append(f"{type_prefix}_done = TRUE")
    
    if points_to_add > 0:
        sql_parts.append(f"points = points + {points_to_add}")
        
    sql = f"UPDATE users_v3 SET {', '.join(sql_parts)} WHERE user_id = %s"
    
    cur.execute(sql, (user_id,))
    conn.commit()
    cur.close()
    conn.close()

def get_user_data(user_id):
    ensure_user_exists(user_id)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT points, last_checkin_date, checkin_count FROM users_v3 WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row

def process_checkin(user_id):
    ensure_user_exists(user_id)
    conn = get_db_connection()
    cur = conn.cursor()
    today = datetime.now(tz_bj).date()
    cur.execute("SELECT last_checkin_date, checkin_count FROM users_v3 WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    if row[0] == today:
        cur.close(); conn.close(); return {"status": "already_checked"}
    
    added = 10 if row[1] == 0 else random.randint(3, 8)
    cur.execute("UPDATE users_v3 SET points = points + %s, last_checkin_date = %s, checkin_count = checkin_count + 1 WHERE user_id = %s RETURNING points", (added, today, user_id))
    total = cur.fetchone()[0]
    conn.commit(); cur.close(); conn.close()
    return {"status": "success", "added": added, "total": total}

def reset_admin_stats(admin_id):
    """管理员重置测试状态 (/cz)"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 重置广告
    cur.execute("UPDATE user_ads_v3 SET daily_watch_count = 0 WHERE user_id = %s", (admin_id,))
    # 重置密钥点击
    cur.execute("UPDATE user_key_clicks_v3 SET click_count = 0 WHERE user_id = %s", (admin_id,))
    # 重置密钥领取
    cur.execute("DELETE FROM user_key_claims_v3 WHERE user_id = %s", (admin_id,))
    # 重置入群验证锁、微信锁、支付宝锁
    cur.execute("""
        UPDATE users_v3 SET 
        verify_fails = 0, verify_lock = NULL,
        wx_fails = 0, wx_lock = NULL, wx_done = FALSE,
        ali_fails = 0, ali_lock = NULL, ali_done = FALSE
        WHERE user_id = %s
    """, (admin_id,))
    
    conn.commit()
    cur.close()
    conn.close()

# --- 广告 & 密钥相关 ---

def get_ad_status(user_id):
    ensure_user_exists(user_id)
    conn = get_db_connection()
    cur = conn.cursor()
    today = datetime.now(tz_bj).date()
    cur.execute("SELECT last_watch_date, daily_watch_count FROM user_ads_v3 WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    count = row[1]
    if row[0] != today: count = 0
    cur.close(); conn.close()
    return count

def create_ad_token(user_id):
    token = str(uuid.uuid4())
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO ad_tokens_v3 (token, user_id) VALUES (%s, %s)", (token, user_id))
    conn.commit(); cur.close(); conn.close()
    return token

def verify_token(token):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM ad_tokens_v3 WHERE token = %s RETURNING user_id", (token,))
    row = cur.fetchone()
    conn.commit(); cur.close(); conn.close()
    return row[0] if row else None

def process_ad_reward(user_id):
    ensure_user_exists(user_id)
    count = get_ad_status(user_id)
    if count >= 3: return {"status": "limit_reached"}
    
    points = 10 if count == 0 else (6 if count == 1 else random.randint(3, 10))
    conn = get_db_connection()
    cur = conn.cursor()
    today = datetime.now(tz_bj).date()
    cur.execute("UPDATE users_v3 SET points = points + %s WHERE user_id = %s", (points, user_id))
    cur.execute("UPDATE user_ads_v3 SET last_watch_date = %s, daily_watch_count = %s + 1 WHERE user_id = %s", (today, count, user_id))
    conn.commit(); cur.close(); conn.close()
    return {"status": "success", "added": points}

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
    row = cur.fetchone(); cur.close(); conn.close(); return row

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
    cur.execute("UPDATE users_v3 SET points = points + %s WHERE user_id = %s RETURNING points", (pts, user_id))
    tot = cur.fetchone()[0]; conn.commit(); cur.close(); conn.close()
    return {"status": "success", "points": pts, "total": tot}

def save_uploaded_photo(file_id, file_unique_id):
    """管理员上传图片记录"""
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO file_ids_v3 (file_id, file_unique_id) VALUES (%s, %s)", (file_id, file_unique_id))
    conn.commit(); cur.close(); conn.close()
    # --- Telegram Bot Handlers ---

# 1. Start 首页 (带入群验证锁检查)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_exists(user.id)
    
    # 检查【入群验证】的锁定状态
    fails, lock_until, _ = check_lock_status(user.id, 'verify')
    
    verify_btn_text = "🚀 开始验证"
    verify_callback = "start_verify_flow"
    
    # 如果已锁定
    if lock_until and datetime.now() < lock_until:
        remaining = lock_until - datetime.now()
        h, m = int(remaining.seconds // 3600), int((remaining.seconds % 3600) // 60)
        verify_btn_text = f"🚫 验证冷却中 ({h}h{m}m)"
        verify_callback = "locked_verify"

    text = (
        "👋 欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~\n\n"
        "📢 小卫小卫，守门员小卫！\n"
        "一键入群，小卫帮你搞定！\n"
        "新人来报到，小卫查身份！"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(verify_btn_text, callback_data=verify_callback)],
        [InlineKeyboardButton("💰 我的积分", callback_data="my_points")],
        [InlineKeyboardButton("🎉 开业活动", callback_data="open_activity")]
    ])
    
    if update.callback_query:
        # 处理点击已锁定按钮
        if update.callback_query.data == "locked_verify":
            await update.callback_query.answer(f"⛔️ 验证失败次数过多，请 {h}小时{m}分 后再试。", show_alert=True)
            return
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    else:
        await update.message.reply_text(text, reply_markup=kb)

# 2. 积分中心 (jf)
async def jf_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = get_user_data(user.id)
    
    text = (
        f"💰 **积分中心**\n"
        f"💎 当前积分：`{data[0]}`\n\n"
        "👇 请选择操作："
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 每日签到", callback_data="do_checkin")],
        [InlineKeyboardButton("💎 积分充值", callback_data="go_recharge")],
        [InlineKeyboardButton("🔙 返回首页", callback_data="back_to_home")]
    ])
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode='Markdown')

# 3. 充值菜单 (检查微信/支付宝的锁和完成状态)
async def recharge_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    # 检查微信状态
    _, wx_lock, wx_done = check_lock_status(user_id, 'wx')
    
    # 检查支付宝状态
    _, ali_lock, ali_done = check_lock_status(user_id, 'ali')
    
    # 构造微信按钮
    if wx_done:
        wx_btn = InlineKeyboardButton("✅ 微信已充值 (限1次)", callback_data="noop_wx_done")
    elif wx_lock and datetime.now() < wx_lock:
        wx_btn = InlineKeyboardButton("🚫 微信冷却中 (5小时)", callback_data="noop_wx_lock")
    else:
        wx_btn = InlineKeyboardButton("💚 微信充值", callback_data="pay_wx")
    
    # 构造支付宝按钮
    if ali_done:
        ali_btn = InlineKeyboardButton("✅ 支付宝已充值 (限1次)", callback_data="noop_ali_done")
    elif ali_lock and datetime.now() < ali_lock:
        ali_btn = InlineKeyboardButton("🚫 支付宝冷却中 (5小时)", callback_data="noop_ali_lock")
    else:
        ali_btn = InlineKeyboardButton("💙 支付宝充值", callback_data="pay_ali")
    
    kb = InlineKeyboardMarkup([
        [wx_btn, ali_btn],
        [InlineKeyboardButton("🔙 返回积分中心", callback_data="my_points")]
    ])
    
    await query.edit_message_text(
        "💎 **积分充值中心 (5元 = 100积分)**\n\n"
        "⚠️ **温馨提示：**\n"
        "微信和支付宝每位用户**各只能充值 1 次**。\n"
        "请勿重复尝试。",
        reply_markup=kb,
        parse_mode='Markdown'
    )

# 处理不可点击的充值按钮提示
async def noop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if "done" in data:
        await query.answer("✅ 您已经充值过该渠道，无法重复充值。", show_alert=True)
    elif "lock" in data:
        await query.answer("⛔️ 该渠道因多次验证失败已锁定，请等待解锁。", show_alert=True)

# ------------------------------------------------------------------------------
# 🟢 流程 A：首页入群验证 (Conversation)
# ------------------------------------------------------------------------------

async def verify_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示 VIP 说明 + 图片"""
    query = update.callback_query
    await query.answer()
    
    text = (
        "💎 **VIP会员特权说明：**\n"
        "✅ 专属中转通道\n"
        "✅ 优先审核入群\n"
        "✅ 7x24小时客服支持\n"
        "✅ 定期福利活动"
    )
    fid = get_file_id("START_VIP_INFO")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ 我已付款，开始验证", callback_data="paid_start")]])
    
    # 如果配置了图片就发图，否则只发字
    if fid:
        try:
            await query.message.reply_photo(photo=fid, caption=text, reply_markup=kb, parse_mode='Markdown')
            await query.delete_message()
        except Exception:
            await query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
    else:
        await query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
        
    return WAITING_START_ORDER

async def ask_start_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """提示输入 20260 开头的订单号"""
    query = update.callback_query
    await query.answer()
    
    text = (
        "📝 **查找订单号教程：**\n\n"
        "1. 打开支付软件 -> 我的 -> 账单\n"
        "2. 找到付款记录 -> 点击进入账单详情\n"
        "3. 点击【更多】或直接复制【订单号】\n\n"
        "👇 **请在下方直接回复您的订单号：**"
    )
    fid = get_file_id("START_TUTORIAL")
    
    if fid:
        try: await query.message.reply_photo(photo=fid, caption=text, parse_mode='Markdown')
        except: await query.message.reply_text(text, parse_mode='Markdown')
    else:
        await query.message.reply_text(text, parse_mode='Markdown')
        
    return WAITING_START_ORDER

async def check_start_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text_input = update.message.text.strip()
    
    # 规则：20260开头
    if text_input.startswith("20260"):
        # 成功 -> 清除锁
        mark_success_and_unlock(user_id, 'verify', points_to_add=0)
        
        await update.message.reply_text(
            "✅ **验证成功！**\n\n欢迎加入VIP大家庭。",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👉 点击加入会员群", url="https://t.me/example")]]), # 请替换真实链接
            parse_mode='Markdown'
        )
        # 自动跳转回首页
        await asyncio.sleep(2)
        await start(update, context)
        return ConversationHandler.END
    else:
        # 失败 -> 记录次数
        fails, _, _ = check_lock_status(user_id, 'verify')
        # 2次失败锁5小时
        new_fails = update_fail_count(user_id, 'verify', fails, 5)
        
        if new_fails >= 2:
            await update.message.reply_text(
                "❌ **未查询到订单信息 (2/2)**\n\n"
                "⚠️ **由于连续失败两次，验证功能已锁定 5 小时。**\n"
                "请稍后重试。",
                parse_mode='Markdown'
            )
            await start(update, context)
            return ConversationHandler.END
        else:
            await update.message.reply_text(
                f"❌ **未查询到订单信息，请重试。**\n"
                f"您还有 **{2 - new_fails}** 次机会。\n"
                "请仔细核对订单号 (以 20260 开头)，再次发送：",
                parse_mode='Markdown'
            )
            return WAITING_START_ORDER

# ------------------------------------------------------------------------------
# 🟢 流程 B：充值验证 (Conversation)
# ------------------------------------------------------------------------------

async def recharge_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """点击微信/支付宝 -> 显示二维码"""
    query = update.callback_query
    await query.answer()
    
    # 记录当前选择的支付方式
    pay_type = 'wx' if query.data == 'pay_wx' else 'ali'
    context.user_data['pay_type'] = pay_type
    
    # 根据类型选择图片和文案
    if pay_type == 'wx':
        title = "微信充值"
        fid = get_file_id("WX_PAY_QR")
    else:
        title = "支付宝充值"
        fid = get_file_id("ALI_PAY_QR")
        
    text = (
        f"💎 **{title}**\n"
        "💰 价格：5元 = 100积分\n\n"
        "⚠️ **请扫码支付 5 元**\n"
        "支付完成后，请点击下方按钮验证。"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ 我已支付，开始验证", callback_data="paid_recharge")]])
    
    if fid:
        try:
            await query.message.reply_photo(photo=fid, caption=text, reply_markup=kb, parse_mode='Markdown')
            await query.delete_message()
        except: await query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
    else:
        await query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
        
    return WAITING_RECHARGE_ORDER

async def ask_recharge_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """提示输入充值单号"""
    query = update.callback_query
    await query.answer()
    
    pay_type = context.user_data.get('pay_type', 'wx')
    
    if pay_type == 'wx':
        text = "📝 **微信验证步骤：**\n请在微信账单找到【交易单号】。\n👇 请输入订单编号："
        fid = get_file_id("WX_ORDER_TUTORIAL")
    else:
        text = "📝 **支付宝验证步骤：**\n请在账单详情更多中找到【商家订单号】。\n👇 请输入订单号："
        fid = get_file_id("ALI_ORDER_TUTORIAL")
        
    if fid:
        try: await query.message.reply_photo(photo=fid, caption=text, parse_mode='Markdown')
        except: await query.message.reply_text(text, parse_mode='Markdown')
    else:
        await query.message.reply_text(text, parse_mode='Markdown')
        
    return WAITING_RECHARGE_ORDER

async def check_recharge_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text_input = update.message.text.strip()
    pay_type = context.user_data.get('pay_type', 'wx')
    
    # 验证规则
    is_valid = False
    if pay_type == 'wx' and text_input.startswith("4200"): is_valid = True
    elif pay_type == 'ali' and text_input.startswith("4768"): is_valid = True
    
    if is_valid:
        # 成功 -> 解锁、标记Done、加100分
        mark_success_and_unlock(user_id, pay_type, 100)
        
        await update.message.reply_text(
            "✅ **充值成功！**\n"
            "已为您添加 100 积分。",
            parse_mode='Markdown'
        )
        await asyncio.sleep(1)
        await jf_command_handler(update, context) # 跳转回积分页
        return ConversationHandler.END
    else:
        # 失败 -> 记录次数
        fails, _, _ = check_lock_status(user_id, pay_type)
        # 充值失败2次锁10小时
        new_fails = update_fail_count(user_id, pay_type, fails, 10)
        
        if new_fails >= 2:
            await update.message.reply_text(
                "❌ **订单识别失败 (2/2)**\n\n"
                "⚠️ **此充值渠道已锁定 10 小时，请稍后重试。**",
                parse_mode='Markdown'
            )
            await jf_command_handler(update, context) # 回积分页
            return ConversationHandler.END
        else:
            await update.message.reply_text(
                f"❌ **订单识别失败，请重试。**\n"
                f"剩余机会：{2 - new_fails} 次\n"
                "请仔细核对单号后再次发送：",
                parse_mode='Markdown'
            )
            return WAITING_RECHARGE_ORDER

# ------------------------------------------------------------------------------
# 其他 Handler (活动, 签到, 管理员)
# ------------------------------------------------------------------------------

async def checkin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    result = process_checkin(update.effective_user.id)
    if result["status"] == "already_checked":
        await query.answer("⚠️ 今日已签到，请明天再来！", show_alert=True)
    else:
        msg = f"🎉 **签到成功！**\n获得奖励：`{result['added']}` 积分\n当前总分：`{result['total']}`"
        await query.answer("签到成功！")
        await query.edit_message_text(text=msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回首页", callback_data="back_to_home")]]), parse_mode='Markdown')

async def activity_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_exists(user.id)
    
    # 获取各种状态用于显示 (0/3) (0/2)
    ad_count = get_ad_status(user.id)
    key_clicks = get_user_click_status(user.id)
    
    token = create_ad_token(user.id)
    watch_url = f"https://{RAILWAY_DOMAIN}/watch_ad/{token}"
    test_url = f"https://{RAILWAY_DOMAIN}/test_page"
    
    text = (
        "🎉 **开业活动中心**\n\n"
        f"1️⃣ **观看视频得积分** ({ad_count}/3)\n"
        "奖励：10分 -> 6分 -> 随机3-10分。\n\n"
        f"2️⃣ **夸克网盘取密钥** ({key_clicks}/2)\n"
        "点击按钮跳转获取今日密钥。\n\n"
        "🛠 **功能测试**\n"
        "体验广告流程 (不加分)。"
    )
    
    kb_list = []
    # 广告按钮
    if ad_count < 3:
        kb_list.append([InlineKeyboardButton("📺 看视频 (积分)", url=watch_url)])
    else:
        kb_list.append([InlineKeyboardButton("✅ 视频任务已完成 (3/3)", callback_data="none")])
    
    # 密钥按钮
    if key_clicks < 2:
        kb_list.append([InlineKeyboardButton("🔑 获取今日密钥", callback_data="get_quark_key")])
    else:
        kb_list.append([InlineKeyboardButton("✅ 密钥任务已完成 (2/2)", callback_data="none")])
        
    kb_list.append([InlineKeyboardButton("🛠 测试按钮", url=test_url)])
    kb_list.append([InlineKeyboardButton("🔙 返回首页", callback_data="back_to_home")])
    
    if update.callback_query:
        if update.callback_query.data == "none":
            await update.callback_query.answer("今日次数已用完，明天再来吧！", show_alert=True)
            return
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb_list), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb_list), parse_mode='Markdown')

async def quark_key_btn_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    await query.answer()
    
    info = get_system_keys_info()
    if not info or not info[1]:
        await query.message.reply_text("⏳ **密钥正在初始化...**")
        return

    clicks = get_user_click_status(user.id)
    if clicks >= 2:
        await query.message.reply_text("⚠️ **今日次数已用完。**")
        return
    
    target = 1 if clicks == 0 else 2
    increment_user_click(user.id)
    
    jump_url = f"https://{RAILWAY_DOMAIN}/jump?type={target}"
    
    msg = (
        f"🚀 **获取密钥** ({clicks+1}/2)\n"
        f"链接：{jump_url}\n"
        "点击跳转 -> 存网盘 -> 复制文件名 -> 发给机器人。"
    )
    await context.bot.send_message(chat_id=user.id, text=msg)

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """全局文本处理 (仅用于密钥验证)"""
    # 注意：Conversations 会拦截它们自己的状态，这里只处理普通状态下的文本
    # 用于验证密钥
    user_id = update.effective_user.id
    text = update.message.text
    if text.startswith('/'): return
    
    result = claim_key_points(user_id, text)
    if result["status"] == "success":
        await update.message.reply_text(f"✅ **成功！**\n获得 +{result['points']} 积分", parse_mode='Markdown')
    elif result["status"] == "already_claimed":
        await update.message.reply_text("⚠️ 此密钥已使用过。")
    else:
        # 如果既不是密钥，也不是验证状态，则弹回首页
        await start(update, context)

# --- 管理员功能 ---

async def admin_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 获取新 File ID", callback_data="start_upload")],
        [InlineKeyboardButton("📂 查看已存图片 & 管理", callback_data="view_files")]
    ])
    await update.message.reply_text("⚙️ **管理员后台**", reply_markup=kb, parse_mode='Markdown')
    return ConversationHandler.END

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    context.user_data.clear()
    await update.message.reply_text("🧹 **状态已清理。**")
    await admin_entry(update, context)
    return ConversationHandler.END

async def cz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    reset_admin_stats(update.effective_user.id)
    await update.message.reply_text("✅ **测试数据已重置。**\n(包括入群验证锁、充值锁、广告次数等)")

async def my_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    info = get_system_keys_info()
    if not info: return 
    k1, l1, k2, l2, d = info
    msg = f"👮‍♂️ **密钥管理** ({d})\nK1: `{k1}`\nL1: {l1 or '❌'}\nK2: `{k2}`\nL2: {l2 or '❌'}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✏️ 点此修改链接", callback_data="edit_links")]])
    await update.message.reply_text(msg, reply_markup=kb, parse_mode='Markdown')

async def start_edit_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    await query.message.reply_text("👇 请发送【密钥 1】的新链接：")
    return WAITING_LINK_1

async def receive_link_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_link_1'] = update.message.text
    await update.message.reply_text("✅ 已记录 L1。👇 发送【密钥 2】新链接：")
    return WAITING_LINK_2

async def receive_link_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_key_links(context.user_data['new_link_1'], update.message.text)
    await update.message.reply_text("✅ **更新完毕！**")
    return ConversationHandler.END

async def start_upload_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    await query.edit_message_text("📤 请发送图片", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")]]))
    return WAITING_FOR_PHOTO

async def handle_photo_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return ConversationHandler.END
    photo = update.message.photo[-1]
    save_uploaded_photo(photo.file_id, photo.file_unique_id)
    await update.message.reply_text(f"✅ ID Saved:\n`{photo.file_id}`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")]]), parse_mode='Markdown')
    return WAITING_FOR_PHOTO

async def view_files_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    files = get_all_files()
    if not files: await query.edit_message_text("📭 无记录。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")]])); return ConversationHandler.END
    await query.message.reply_text("📂 **图片列表:**", parse_mode='Markdown')
    for db_id, f_id in files:
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=f_id, caption=f"ID: `{db_id}`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"🗑 删除 {db_id}", callback_data=f"pre_del_{db_id}")]]), parse_mode='Markdown')
    await context.bot.send_message(chat_id=update.effective_chat.id, text="--- End ---", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")]]))
    return ConversationHandler.END

async def pre_delete_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    db_id = query.data.split("_")[-1]
    await query.edit_message_caption(caption=f"⚠️ 确认删除 ID {db_id}?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 确认", callback_data=f"confirm_del_{db_id}"), InlineKeyboardButton("❌ 取消", callback_data="cancel_del")]]), parse_mode='Markdown')

async def execute_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    db_id = query.data.split("_")[-1]
    delete_file_by_id(db_id)
    await query.delete_message()
    await context.bot.send_message(chat_id=update.effective_chat.id, text="已删除", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")]]))

async def cancel_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("已取消")
    await update.callback_query.edit_message_caption("操作已取消", reply_markup=None)

async def cancel_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 操作已取消。")
    return ConversationHandler.END

async def daily_reset_task():
    k1, k2 = generate_random_key(), generate_random_key()
    update_system_keys(k1, k2, date.today())
    if bot_app and ADMIN_ID:
        await bot_app.bot.send_message(chat_id=ADMIN_ID, text=f"🔔 每日密钥更新\nK1: `{k1}`\nK2: `{k2}`", parse_mode='Markdown')
        # ==============================================================================
# Web Server & Main Logic
# ==============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"-------- RAILWAY DOMAIN: {RAILWAY_DOMAIN} --------")
    init_db()
    print("Database Initialized.")
    
    # 检查并初始化今日密钥
    info = get_system_keys_info()
    if not info or info[4] == date(2000, 1, 1):
        update_system_keys(generate_random_key(), generate_random_key(), date.today())
    
    scheduler.add_job(daily_reset_task, 'cron', hour=10, minute=0, timezone=tz_bj)
    scheduler.start()
    
    global bot_app
    bot_app = Application.builder().token(BOT_TOKEN).build()
    
    # --- 注册 Handlers ---
    
    # 1. 验证流程 (Priority High)
    verify_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(verify_entry, pattern="^start_verify_flow$")],
        states={
            WAITING_START_ORDER: [
                CallbackQueryHandler(ask_start_order, pattern="^paid_start$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, check_start_order)
            ]
        },
        fallbacks=[CommandHandler("start", start), CommandHandler("cancel", cancel_admin)],
        per_message=False
    )
    
    # 2. 充值流程 (Priority High)
    recharge_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(recharge_menu, pattern="^go_recharge$"),
            CallbackQueryHandler(recharge_entry, pattern="^pay_wx|pay_ali$")
        ],
        states={
            WAITING_RECHARGE_ORDER: [
                CallbackQueryHandler(ask_recharge_order, pattern="^paid_recharge$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, check_recharge_order)
            ]
        },
        fallbacks=[CommandHandler("jf", jf_command_handler), CallbackQueryHandler(jf_command_handler, pattern="^my_points$")],
        per_message=False
    )
    
    # 3. 管理员密钥修改
    key_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_edit_links, pattern="^edit_links$")],
        states={
            WAITING_LINK_1: [MessageHandler(filters.TEXT, receive_link_1)],
            WAITING_LINK_2: [MessageHandler(filters.TEXT, receive_link_2)]
        },
        fallbacks=[CommandHandler("cancel", cancel_admin)]
    )
    
    # 4. 管理员图片上传
    admin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_upload_flow, pattern="^start_upload$"), CommandHandler("id", lambda u, c: start_upload_flow(u, c))],
        states={WAITING_FOR_PHOTO: [MessageHandler(filters.PHOTO, handle_photo_upload), CallbackQueryHandler(admin_entry, pattern="^back_to_admin$")]},
        fallbacks=[CommandHandler("admin", admin_entry)], per_message=False
    )

    bot_app.add_handler(verify_conv)
    bot_app.add_handler(recharge_conv)
    bot_app.add_handler(key_conv)
    bot_app.add_handler(admin_conv)
    
    # 普通命令与回调
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CallbackQueryHandler(start, pattern="^back_to_home$"))
    
    bot_app.add_handler(CommandHandler("jf", jf_command_handler))
    bot_app.add_handler(CallbackQueryHandler(jf_command_handler, pattern="^my_points$"))
    # 处理充值菜单中被锁定的按钮
    bot_app.add_handler(CallbackQueryHandler(noop_handler, pattern="^noop_"))
    
    bot_app.add_handler(CallbackQueryHandler(checkin_handler, pattern="^do_checkin$"))
    
    bot_app.add_handler(CommandHandler("hd", activity_handler))
    bot_app.add_handler(CallbackQueryHandler(activity_handler, pattern="^open_activity$"))
    bot_app.add_handler(CallbackQueryHandler(quark_key_btn_handler, pattern="^get_quark_key$"))
    
    bot_app.add_handler(CommandHandler("admin", admin_entry))
    bot_app.add_handler(CallbackQueryHandler(admin_entry, pattern="^back_to_admin$"))
    bot_app.add_handler(CallbackQueryHandler(view_files_flow, pattern="^view_files$"))
    bot_app.add_handler(CallbackQueryHandler(pre_delete_check, pattern="^pre_del_"))
    bot_app.add_handler(CallbackQueryHandler(execute_delete, pattern="^confirm_del_"))
    bot_app.add_handler(CallbackQueryHandler(cancel_delete, pattern="^cancel_del$"))
    
    bot_app.add_handler(CommandHandler("c", clear_command))
    bot_app.add_handler(CommandHandler("cz", cz_command))
    bot_app.add_handler(CommandHandler("my", my_command))
    
    # 兜底消息 (处理普通文本/密钥)
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    print("Bot Polling Started.")
    
    yield
    
    if bot_app:
        await bot_app.stop()
        await bot_app.shutdown()
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def health_check():
    return {"status": "running"}

# 观看广告页 (HTML)
@app.get("/watch_ad/{token}", response_class=HTMLResponse)
async def watch_ad_page(token: str):
    html = f"""
    <!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>视频任务</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <script src='https://libtl.com/sdk.js' data-zone='10489957' data-sdk='show_10489957'></script>
    <style>body{{font-family:sans-serif;text-align:center;padding:20px;background:#f4f4f9;display:flex;flex-direction:column;justify-content:center;height:90vh}}.container{{max-width:500px;margin:0 auto;background:white;padding:30px;border-radius:12px;box-shadow:0 4px 15px rgba(0,0,0,0.1)}}.btn{{padding:15px 30px;background:#0088cc;color:white;border:none;border-radius:8px;font-size:18px;cursor:pointer;width:100%}}.btn:disabled{{background:#ccc}}#status{{margin-top:20px;font-size:16px;color:#555}}.progress{{width:100%;background-color:#ddd;border-radius:5px;margin-top:15px;height:10px;display:none}}.bar{{width:0%;height:100%;background-color:#4CAF50;border-radius:5px;transition:width 1s linear}}</style></head>
    <body><div class="container"><h2>📺 观看广告获取积分</h2><p style="color:#666;margin-bottom:25px">请点击下方按钮，保持页面开启 15 秒。</p><button id="adBtn" class="btn" onclick="startProcess()">▶️ 开始观看</button><div class="progress" id="progress"><div class="bar" id="bar"></div></div><div id="status"></div></div>
    <script>const token="{token}",s=document.getElementById('status'),btn=document.getElementById('adBtn'),bar=document.getElementById('bar'),p=document.getElementById('progress');if(window.Telegram&&window.Telegram.WebApp)window.Telegram.WebApp.ready();function startProcess(){{btn.disabled=!0;s.innerText="⏳ 正在加载...";if(typeof show_10489957==='function')show_10489957().catch(e=>console.log(e));s.innerText="📺 广告观看中...";p.style.display='block';let t=15;const timer=setInterval(()=>{{t--;bar.style.width=((15-t)/15)*100+"%";if(t<=0){{clearInterval(timer);v();}}else{{s.innerText="📺 剩余: "+t+"秒";}}}},1000)}}function v(){{s.innerText="✅ 正在验证...";fetch('/api/verify_ad',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{token:token}})}}).then(r=>r.json()).then(d=>{{if(d.success)window.location.href="/ad_success?points="+d.points;else{{s.innerText="❌ "+d.message;btn.disabled=!1}}}}).catch(e=>{{s.innerText="❌ 网络错误";btn.disabled=!1}})}}</script></body></html>
    """
    return HTMLResponse(content=html)

# 广告成功页
@app.get("/ad_success", response_class=HTMLResponse)
async def success_page(points: int = 0):
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>成功</title><script src="https://telegram.org/js/telegram-web-app.js"></script><style>body{{font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;background-color:#e8f5e9;text-align:center;margin:0}}.card{{background:white;padding:40px;border-radius:15px;box-shadow:0 4px 20px rgba(0,0,0,0.1)}}h1{{color:#2e7d32}}p{{font-size:18px;color:#555}}.score{{font-size:40px;font-weight:bold;color:#f57c00;display:block;margin:20px 0}}</style></head><body><div class="card"><h1>🎉 观看成功！</h1><p>获得奖励</p><span class="score">+{points} 积分</span><p style="font-size:14px;color:#999">页面将自动关闭...</p></div><script>setTimeout(()=>{{if(window.Telegram&&window.Telegram.WebApp)window.Telegram.WebApp.close();else window.close()}},2500)</script></body></html>"""
    return HTMLResponse(content=html)

# 测试页
@app.get("/test_page", response_class=HTMLResponse)
async def test_page():
    html = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>测试</title><script src="https://telegram.org/js/telegram-web-app.js"></script><style>body{font-family:sans-serif;text-align:center;padding:20px;background:#fff3e0;display:flex;flex-direction:column;justify-content:center;height:90vh}.container{background:white;padding:30px;border-radius:12px;box-shadow:0 4px 10px rgba(0,0,0,0.1)}.btn{padding:15px 30px;background:#ff9800;color:white;border:none;border-radius:8px;font-size:18px;cursor:pointer;width:100%}.btn:disabled{background:#ccc}#status{margin-top:20px;font-weight:bold;color:#555}</style></head><body><div class="container"><h2>🛠 测试模式</h2><p>简陋测试页。</p><button id="btn" class="btn" onclick="startTest()">🖱 点击测试</button><div id="status"></div></div><script>function startTest(){const btn=document.getElementById('btn'),s=document.getElementById('status');btn.disabled=!0;let c=3;const t=setInterval(()=>{c--;if(c<=0){clearInterval(t);s.innerText="✅ 模拟成功! 跳转中...";setTimeout(()=>{window.location.href="/ad_success?points=0"},1000)}else{s.innerText="⏳ "+c}},1000)}</script></body></html>"""
    return HTMLResponse(content=html)

# 广告验证 API
@app.post("/api/verify_ad")
async def verify_ad_api(payload: dict):
    user_id = verify_token(payload.get("token"))
    if not user_id: return JSONResponse({"success": False, "message": "Expired"})
    res = process_ad_reward(user_id)
    return JSONResponse({"success": res["status"]=="success", "points": res.get("added"), "message": res.get("status")})

# 中转页
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
