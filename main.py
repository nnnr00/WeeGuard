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
# 🛠️ 配置区域 (请在此处填入你的 File ID)
# ==============================================================================
CONFIG = {
    # 1. 首页 /start -> 开始验证 -> VIP说明配图
    "START_VIP_INFO": "AgACAgEAAxkBAAIC...", 
    
    # 2. 首页 -> 我已付款 -> 查找订单号教程配图 (20260开头)
    "START_TUTORIAL": "AgACAgEAAxkBAAIC...",
    
    # 3. 积分 /jf -> 微信充值 -> 支付二维码图片
    "WX_PAY_QR": "AgACAgEAAxkBAAIC...",
    
    # 4. 积分 -> 微信充值 -> 查找交易单号教程图
    "WX_ORDER_TUTORIAL": "AgACAgEAAxkBAAIC...",
    
    # 5. 积分 -> 支付宝充值 -> 支付二维码图片
    "ALI_PAY_QR": "AgACAgEAAxkBAAIC...",
    
    # 6. 积分 -> 支付宝充值 -> 查找商家订单号教程图
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

# ==============================================================================

# 日志
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# 全局变量
tz_bj = pytz.timezone('Asia/Shanghai')
scheduler = AsyncIOScheduler(timezone=tz_bj)
bot_app = None

# --- 状态机状态定义 ---
# 1. 管理员
WAITING_FOR_PHOTO = 1
WAITING_LINK_1 = 2
WAITING_LINK_2 = 3
# 2. 首页入群验证
WAITING_START_ORDER = 10
# 3. 充值验证
WAITING_RECHARGE_ORDER = 20

# --- 数据库操作 ---

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """初始化数据库 (V3版 + 充值扩充)"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 基础表
    cur.execute("CREATE TABLE IF NOT EXISTS file_ids_v3 (id SERIAL PRIMARY KEY, file_id TEXT NOT NULL, file_unique_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")
    
    # 用户表 (大幅扩充字段以支持多重锁定)
    # verify_fails: 首页入群失败次数
    # verify_lock: 首页入群锁定时间
    # wx_fails: 微信充值失败次数
    # wx_lock: 微信锁定时间
    # wx_done: 微信是否已充值过 (True/False)
    # ali_fails: 支付宝失败次数
    # ali_lock: 支付宝锁定时间
    # ali_done: 支付宝是否已充值过
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
    # 尝试补全字段 (防止旧表报错)
    cols = [
        "verify_fails INTEGER DEFAULT 0", "verify_lock TIMESTAMP",
        "wx_fails INTEGER DEFAULT 0", "wx_lock TIMESTAMP", "wx_done BOOLEAN DEFAULT FALSE",
        "ali_fails INTEGER DEFAULT 0", "ali_lock TIMESTAMP", "ali_done BOOLEAN DEFAULT FALSE"
    ]
    for c in cols:
        try: cur.execute(f"ALTER TABLE users_v3 ADD COLUMN IF NOT EXISTS {c};")
        except: conn.rollback()

    # 其他业务表
    cur.execute("CREATE TABLE IF NOT EXISTS user_ads_v3 (user_id BIGINT PRIMARY KEY, last_watch_date DATE, daily_watch_count INTEGER DEFAULT 0);")
    cur.execute("CREATE TABLE IF NOT EXISTS ad_tokens_v3 (token TEXT PRIMARY KEY, user_id BIGINT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")
    cur.execute("CREATE TABLE IF NOT EXISTS system_keys_v3 (id INTEGER PRIMARY KEY, key_1 TEXT, link_1 TEXT, key_2 TEXT, link_2 TEXT, session_date DATE, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")
    cur.execute("INSERT INTO system_keys_v3 (id, session_date) VALUES (1, %s) ON CONFLICT (id) DO NOTHING", (date(2000,1,1),))
    cur.execute("CREATE TABLE IF NOT EXISTS user_key_clicks_v3 (user_id BIGINT PRIMARY KEY, click_count INTEGER DEFAULT 0, session_date DATE);")
    cur.execute("CREATE TABLE IF NOT EXISTS user_key_claims_v3 (id SERIAL PRIMARY KEY, user_id BIGINT, key_val TEXT, claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id, key_val));")
    
    conn.commit()
    cur.close()
    conn.close()

# --- 辅助逻辑 ---
def get_session_date():
    now = datetime.now(tz_bj)
    if now.hour < 10: return (now - timedelta(days=1)).date()
    return now.date()

def generate_random_key():
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(10))

def get_file_id(key):
    """从配置获取 File ID，如果没有配置则返回 None (仅发文字)"""
    fid = CONFIG.get(key)
    return fid if fid and fid.startswith("AgAC") else None

# --- 数据库函数：核心业务 ---

def ensure_user_exists(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO users_v3 (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    cur.execute("INSERT INTO user_ads_v3 (user_id, daily_watch_count) VALUES (%s, 0) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    conn.commit(); cur.close(); conn.close()

# 通用锁定检查
def check_lock(user_id, type_prefix):
    """type_prefix: 'verify', 'wx', 'ali'"""
    ensure_user_exists(user_id)
    conn = get_db_connection()
    cur = conn.cursor()
    # 动态查询 fails, lock, done(如果有)
    fields = f"{type_prefix}_fails, {type_prefix}_lock"
    if type_prefix in ['wx', 'ali']: fields += f", {type_prefix}_done"
    
    cur.execute(f"SELECT {fields} FROM users_v3 WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    cur.close(); conn.close()
    return row

def update_fail(user_id, type_prefix, current_fails, lock_hours):
    conn = get_db_connection()
    cur = conn.cursor()
    new_fails = current_fails + 1
    if new_fails >= 2:
        lock_until = datetime.now() + timedelta(hours=lock_hours)
        cur.execute(f"UPDATE users_v3 SET {type_prefix}_fails = %s, {type_prefix}_lock = %s WHERE user_id = %s", (new_fails, lock_until, user_id))
    else:
        cur.execute(f"UPDATE users_v3 SET {type_prefix}_fails = %s WHERE user_id = %s", (new_fails, user_id))
    conn.commit(); cur.close(); conn.close()
    return new_fails

def mark_success(user_id, type_prefix, points=0):
    conn = get_db_connection()
    cur = conn.cursor()
    # 清除锁
    sql = f"UPDATE users_v3 SET {type_prefix}_fails = 0, {type_prefix}_lock = NULL"
    # 如果是充值，标记 done
    if type_prefix in ['wx', 'ali']: sql += f", {type_prefix}_done = TRUE"
    # 加分
    if points > 0: sql += f", points = points + {points}"
    
    sql += " WHERE user_id = %s"
    cur.execute(sql, (user_id,))
    conn.commit(); cur.close(); conn.close()

# 其他函数 (保留原有)
def get_user_data(user_id):
    ensure_user_exists(user_id)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT points, last_checkin_date, checkin_count FROM users_v3 WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    cur.close(); conn.close()
    return row

def process_checkin(user_id):
    ensure_user_exists(user_id)
    conn = get_db_connection()
    cur = conn.cursor()
    today = datetime.now(tz_bj).date()
    cur.execute("SELECT last_checkin_date, checkin_count FROM users_v3 WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    if row[0] == today: cur.close(); conn.close(); return {"status": "already_checked"}
    added = 10 if row[1] == 0 else random.randint(3, 8)
    cur.execute("UPDATE users_v3 SET points = points + %s, last_checkin_date = %s, checkin_count = checkin_count + 1 WHERE user_id = %s RETURNING points", (added, today, user_id))
    total = cur.fetchone()[0]
    conn.commit(); cur.close(); conn.close()
    return {"status": "success", "added": added, "total": total}

def reset_admin_stats(admin_id):
    """重置管理员所有状态 (/cz)"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE user_ads_v3 SET daily_watch_count = 0 WHERE user_id = %s", (admin_id,))
    cur.execute("UPDATE user_key_clicks_v3 SET click_count = 0 WHERE user_id = %s", (admin_id,))
    cur.execute("DELETE FROM user_key_claims_v3 WHERE user_id = %s", (admin_id,))
    # 重置验证锁和充值锁
    cur.execute("""
        UPDATE users_v3 SET 
        verify_fails = 0, verify_lock = NULL,
        wx_fails = 0, wx_lock = NULL, wx_done = FALSE,
        ali_fails = 0, ali_lock = NULL, ali_done = FALSE
        WHERE user_id = %s
    """, (admin_id,))
    conn.commit(); cur.close(); conn.close()

# 广告/密钥相关 (保留不展示细节以节省篇幅，逻辑同前)
def get_ad_status(user_id):
    ensure_user_exists(user_id)
    conn = get_db_connection(); cur = conn.cursor(); today = datetime.now(tz_bj).date()
    cur.execute("SELECT last_watch_date, daily_watch_count FROM user_ads_v3 WHERE user_id = %s", (user_id,))
    row = cur.fetchone(); cur.close(); conn.close()
    return row[1] if row and row[0] == today else 0

def create_ad_token(user_id):
    token = str(uuid.uuid4()); conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO ad_tokens_v3 (token, user_id) VALUES (%s, %s)", (token, user_id)); conn.commit(); cur.close(); conn.close(); return token

def verify_token(token):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM ad_tokens_v3 WHERE token = %s RETURNING user_id", (token,))
    row = cur.fetchone(); conn.commit(); cur.close(); conn.close(); return row[0] if row else None

def process_ad_reward(user_id):
    ensure_user_exists(user_id); count = get_ad_status(user_id)
    if count >= 3: return {"status": "limit_reached"}
    points = 10 if count == 0 else (6 if count == 1 else random.randint(3, 10))
    conn = get_db_connection(); cur = conn.cursor(); today = datetime.now(tz_bj).date()
    cur.execute("UPDATE users_v3 SET points = points + %s WHERE user_id = %s", (points, user_id))
    cur.execute("UPDATE user_ads_v3 SET last_watch_date = %s, daily_watch_count = %s + 1 WHERE user_id = %s", (today, count, user_id))
    conn.commit(); cur.close(); conn.close(); return {"status": "success", "added": points}

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

def save_file_id(fid, uid):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO file_ids_v3 (file_id, file_unique_id) VALUES (%s, %s)", (fid, uid))
    conn.commit(); cur.close(); conn.close()

# --- Handlers ---

# 1. Start 首页 (逻辑：5小时锁检查)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_exists(user.id)
    
    # 检查入群验证锁
    row = check_lock(user.id, 'verify') # (fails, lock)
    fails, lock_until = row[0] if row else 0, row[1] if row else None
    
    verify_btn_text = "🚀 开始验证"
    verify_callback = "start_verify_flow"
    
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
        if update.callback_query.data == "locked_verify":
            await update.callback_query.answer("⛔️ 操作过于频繁，请稍后再试。", show_alert=True)
            return
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    else:
        await update.message.reply_text(text, reply_markup=kb)

# 2. 积分 & 充值首页
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
    
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
    else: await update.message.reply_text(text, reply_markup=kb, parse_mode='Markdown')

# 3. 充值渠道选择 (检查锁状态)
async def recharge_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    # 检查微信状态
    row_wx = check_lock(user_id, 'wx') # fails, lock, done
    wx_fail, wx_lock, wx_done = row_wx[0], row_wx[1], row_wx[2]
    
    # 检查支付宝状态
    row_ali = check_lock(user_id, 'ali')
    ali_fail, ali_lock, ali_done = row_ali[0], row_ali[1], row_ali[2]
    
    # 构造微信按钮
    if wx_done: wx_btn = InlineKeyboardButton("✅ 微信已充值", callback_data="noop")
    elif wx_lock and datetime.now() < wx_lock: wx_btn = InlineKeyboardButton("🚫 微信冷却中", callback_data="noop")
    else: wx_btn = InlineKeyboardButton("💚 微信充值", callback_data="pay_wx")
    
    # 构造支付宝按钮
    if ali_done: ali_btn = InlineKeyboardButton("✅ 支付宝已充值", callback_data="noop")
    elif ali_lock and datetime.now() < ali_lock: ali_btn = InlineKeyboardButton("🚫 支付宝冷却中", callback_data="noop")
    else: ali_btn = InlineKeyboardButton("💙 支付宝充值", callback_data="pay_ali")
    
    kb = InlineKeyboardMarkup([
        [wx_btn, ali_btn],
        [InlineKeyboardButton("🔙 返回积分中心", callback_data="my_points")]
    ])
    
    await query.edit_message_text("💎 **选择充值方式 (5元=100积分)**\n⚠️ 每种方式仅限充值 1 次，请勿重复。", reply_markup=kb, parse_mode='Markdown')

# --- 验证流程 1: 首页入群验证 (Start) ---

async def verify_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示 VIP 说明"""
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
    
    if fid:
        try:
            await query.message.reply_photo(fid, caption=text, reply_markup=kb, parse_mode='Markdown')
            await query.delete_message()
        except: await query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
    else: await query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
    return WAITING_START_ORDER

async def ask_start_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """提示输入 20260 订单号"""
    query = update.callback_query
    await query.answer()
    
    text = (
        "📝 **查找订单号教程：**\n\n"
        "1. 打开支付软件 -> 账单\n"
        "2. 点击账单详情 -> 更多\n"
        "3. 复制【订单号】\n\n"
        "👇 **请直接发送您的订单号：**"
    )
    fid = get_file_id("START_TUTORIAL")
    if fid:
        try: await query.message.reply_photo(fid, caption=text, parse_mode='Markdown')
        except: await query.message.reply_text(text, parse_mode='Markdown')
    else: await query.message.reply_text(text, parse_mode='Markdown')
    return WAITING_START_ORDER

async def check_start_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    txt = update.message.text.strip()
    
    # 验证逻辑: 20260 开头
    if txt.startswith("20260"):
        # 成功 -> 清锁 -> 加群 -> 回首页
        mark_success(user_id, 'verify') # 只清锁，不加分
        await update.message.reply_text("✅ **验证成功！**\n欢迎加入VIP大家庭。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👉 点击入群", url="https://t.me/example")]]), parse_mode='Markdown')
        await asyncio.sleep(2)
        await start(update, context)
        return ConversationHandler.END
    else:
        # 失败 -> 记次
        row = check_lock(user_id, 'verify')
        fails = row[0] if row else 0
        new_fails = update_fail(user_id, 'verify', fails, 5) # 5小时锁
        
        if new_fails >= 2:
            await update.message.reply_text("❌ **验证失败 (2/2)**\n\n⚠️ 功能已锁定 5 小时，请稍后重试。")
            await start(update, context)
            return ConversationHandler.END
        else:
            await update.message.reply_text(f"❌ **未查询到订单信息，请重试。**\n剩余机会：{2-new_fails}次", parse_mode='Markdown')
            return WAITING_START_ORDER

# --- 验证流程 2: 充值验证 (Recharge) ---

async def recharge_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """点击微信/支付宝 -> 显示二维码"""
    query = update.callback_query
    await query.answer()
    data = query.data # pay_wx or pay_ali
    context.user_data['pay_type'] = 'wx' if data == 'pay_wx' else 'ali'
    
    is_wx = (data == 'pay_wx')
    fid = get_file_id("WX_PAY_QR" if is_wx else "ALI_PAY_QR")
    
    text = (
        f"💎 **{'微信' if is_wx else '支付宝'}充值中心**\n"
        "💰 价格：5元 = 100积分\n\n"
        "⚠️ **温馨提示：**\n"
        "本渠道每人仅限使用 1 次，请勿重复支付！"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ 我已支付，开始验证", callback_data="paid_recharge")]])
    
    if fid:
        try:
            await query.message.reply_photo(fid, caption=text, reply_markup=kb, parse_mode='Markdown')
            await query.delete_message()
        except: await query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
    else: await query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
    return WAITING_RECHARGE_ORDER

async def ask_recharge_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """提示输入单号 (微信/支付宝不同文案)"""
    query = update.callback_query
    await query.answer()
    ptype = context.user_data.get('pay_type', 'wx')
    
    if ptype == 'wx':
        text = "📝 **微信验证步骤：**\n请在微信账单找到【交易单号】。\n👇 请输入订单编号："
        fid = get_file_id("WX_ORDER_TUTORIAL")
    else:
        text = "📝 **支付宝验证步骤：**\n请在账单详情更多中找到【商家订单号】。\n👇 请输入订单号："
        fid = get_file_id("ALI_ORDER_TUTORIAL")
        
    if fid:
        try: await query.message.reply_photo(fid, caption=text, parse_mode='Markdown')
        except: await query.message.reply_text(text, parse_mode='Markdown')
    else: await query.message.reply_text(text, parse_mode='Markdown')
    return WAITING_RECHARGE_ORDER

async def check_recharge_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    txt = update.message.text.strip()
    ptype = context.user_data.get('pay_type', 'wx')
    
    # 规则：微信4200，支付宝4768
    valid = False
    if ptype == 'wx' and txt.startswith("4200"): valid = True
    elif ptype == 'ali' and txt.startswith("4768"): valid = True
    
    if valid:
        # 成功 -> 标记done -> 加100分 -> 回积分页
        mark_success(user_id, ptype, 100)
        await update.message.reply_text("✅ **充值成功！**\n已到账 100 积分。", parse_mode='Markdown')
        await asyncio.sleep(1)
        await jf_command_handler(update, context) # 回积分页
        return ConversationHandler.END
    else:
        # 失败 -> 10小时锁 (按要求)
        row = check_lock(user_id, ptype)
        fails = row[0]
        new_fails = update_fail(user_id, ptype, fails, 10) # 10小时
        
        if new_fails >= 2:
            await update.message.reply_text("❌ **订单识别失败 (2/2)**\n⚠️ 此渠道已锁定 10 小时，请稍后重试。")
            await jf_command_handler(update, context)
            return ConversationHandler.END
        else:
            await update.message.reply_text(f"❌ **订单识别失败，请重试。**\n剩余机会：{2-new_fails}次", parse_mode='Markdown')
            return WAITING_RECHARGE_ORDER

# --- Admin Key (Conversation) ---
async def my_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    # /my 不重置，只显示和修改
    info = get_system_keys_info()
    if not info: update_system_keys(generate_random_key(), generate_random_key(), date.today()); info = get_system_keys_info()
    k1, l1, k2, l2, d = info
    msg = f"👮‍♂️ **密钥管理** ({d})\nK1: `{k1}`\nL1: {l1 or '❌'}\nK2: `{k2}`\nL2: {l2 or '❌'}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✏️ 点此修改链接", callback_data="edit_links")]])
    await update.message.reply_text(msg, reply_markup=kb, parse_mode='Markdown')

async def start_edit_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    await query.message.reply_text("👇 请发送【密钥 1】的新链接：")
    return WAITING_LINK_1

# --- Admin Clear ---
async def cz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    reset_admin_stats(update.effective_user.id)
    await update.message.reply_text("✅ **测试数据已重置。**\n(验证锁、充值锁、点击次数均已清零)")

# --- 全局回退 ---
async def global_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text and update.message.text.startswith('/'): return
    await start(update, context)

# --- Main Setup ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"-------- RAILWAY DOMAIN: {RAILWAY_DOMAIN} --------")
    init_db()
    print("Database Initialized.")
    info = get_system_keys_info()
    if not info or info[4] == date(2000, 1, 1): update_system_keys(generate_random_key(), generate_random_key(), date.today())
    scheduler.add_job(daily_reset_task, 'cron', hour=10, minute=0, timezone=tz_bj)
    scheduler.start()
    
    global bot_app
    bot_app = Application.builder().token(BOT_TOKEN).build()
    
    # 1. 优先级最高：验证流程 (Conversation)
    
    # 首页入群验证 (Start Flow)
    start_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(verify_entry, pattern="^start_verify_flow$")],
        states={
            WAITING_START_ORDER: [
                CallbackQueryHandler(ask_start_order, pattern="^paid_start$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, check_start_order)
            ]
        },
        fallbacks=[CommandHandler("start", start)],
        per_message=False
    )
    
    # 充值验证 (Recharge Flow)
    recharge_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(recharge_menu, pattern="^go_recharge$"), CallbackQueryHandler(recharge_entry, pattern="^pay_wx|pay_ali$")],
        states={
            WAITING_RECHARGE_ORDER: [
                CallbackQueryHandler(ask_recharge_order, pattern="^paid_recharge$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, check_recharge_order)
            ]
        },
        fallbacks=[CommandHandler("jf", jf_command_handler), CallbackQueryHandler(jf_command_handler, pattern="^my_points$")],
        per_message=False
    )
    
    # 管理员修改链接
    key_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_edit_links, pattern="^edit_links$")],
        states={
            WAITING_LINK_1: [MessageHandler(filters.TEXT, receive_link_1)],
            WAITING_LINK_2: [MessageHandler(filters.TEXT, receive_link_2)]
        },
        fallbacks=[CommandHandler("cancel", cancel_admin)]
    )

    # 注册
    bot_app.add_handler(start_conv)
    bot_app.add_handler(recharge_conv)
    bot_app.add_handler(key_conv)
    
    # 2. 普通命令
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CallbackQueryHandler(start, pattern="^back_to_home$"))
    
    bot_app.add_handler(CommandHandler("jf", jf_command_handler))
    bot_app.add_handler(CallbackQueryHandler(jf_command_handler, pattern="^my_points$"))
    bot_app.add_handler(CallbackQueryHandler(recharge_menu, pattern="^go_recharge$"))
    bot_app.add_handler(CallbackQueryHandler(checkin_handler, pattern="^do_checkin$"))
    
    bot_app.add_handler(CommandHandler("hd", activity_handler))
    bot_app.add_handler(CallbackQueryHandler(activity_handler, pattern="^open_activity$"))
    bot_app.add_handler(CallbackQueryHandler(quark_key_btn_handler, pattern="^get_quark_key$"))
    
    bot_app.add_handler(CommandHandler("cz", cz_command))
    bot_app.add_handler(CommandHandler("my", my_command)) # 只显示菜单
    
    # 密钥验证 (Global Text)
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    
    # 3. 兜底回首页 (防止误触)
    # bot_app.add_handler(MessageHandler(filters.ALL, global_fallback)) 
    # (注：暂且注释掉兜底，防止干扰其他文本输入，如需完全封闭可解开)

    await bot_app.initialize(); await bot_app.start(); await bot_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    yield
    if bot_app: await bot_app.stop(); await bot_app.shutdown()
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan)

# --- Web Routes (保留原样) ---
@app.get("/")
async def health_check(): return {"status": "running"}

@app.get("/watch_ad/{token}", response_class=HTMLResponse)
async def watch_ad_page(token: str):
    # (HTML代码同上个版本，篇幅限制省略，逻辑未变)
    return HTMLResponse(content=f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>视频任务</title><script src="https://telegram.org/js/telegram-web-app.js"></script><script src='https://libtl.com/sdk.js' data-zone='10489957' data-sdk='show_10489957'></script><style>body{{font-family:sans-serif;text-align:center;padding:20px;background:#f4f4f9;display:flex;flex-direction:column;justify-content:center;height:90vh}}.container{{max-width:500px;margin:0 auto;background:white;padding:30px;border-radius:12px;box-shadow:0 4px 15px rgba(0,0,0,0.1)}}.btn{{padding:15px 30px;background:#0088cc;color:white;border:none;border-radius:8px;font-size:18px;cursor:pointer;width:100%}}.btn:disabled{{background:#ccc}}#status{{margin-top:20px;font-size:16px;color:#555}}.progress{{width:100%;background-color:#ddd;border-radius:5px;margin-top:15px;height:10px;display:none}}.bar{{width:0%;height:100%;background-color:#4CAF50;border-radius:5px;transition:width 1s linear}}</style></head><body><div class="container"><h2>📺 观看广告获取积分</h2><p style="color:#666;margin-bottom:25px">请点击下方按钮，保持页面开启 15 秒。</p><button id="adBtn" class="btn" onclick="startProcess()">▶️ 开始观看</button><div class="progress" id="progress"><div class="bar" id="bar"></div></div><div id="status"></div></div><script>const token="{token}",s=document.getElementById('status'),btn=document.getElementById('adBtn'),bar=document.getElementById('bar'),p=document.getElementById('progress');if(window.Telegram&&window.Telegram.WebApp)window.Telegram.WebApp.ready();function startProcess(){{btn.disabled=!0;s.innerText="⏳ 正在加载...";if(typeof show_10489957==='function')show_10489957().catch(e=>console.log(e));s.innerText="📺 广告观看中...";p.style.display='block';let t=15;const timer=setInterval(()=>{{t--;bar.style.width=((15-t)/15)*100+"%";if(t<=0){{clearInterval(timer);v();}}else{{s.innerText="📺 剩余: "+t+"秒";}}}},1000)}}function v(){{s.innerText="✅ 正在验证...";fetch('/api/verify_ad',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{token:token}})}}).then(r=>r.json()).then(d=>{{if(d.success)window.location.href="/ad_success?points="+d.points;else{{s.innerText="❌ "+d.message;btn.disabled=!1}}}}).catch(e=>{{s.innerText="❌ 网络错误";btn.disabled=!1}})}}</script></body></html>""")

@app.get("/ad_success", response_class=HTMLResponse)
async def success_page(points: int = 0):
    return HTMLResponse(content=f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>成功</title><script src="https://telegram.org/js/telegram-web-app.js"></script><style>body{{font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;background-color:#e8f5e9;text-align:center;margin:0}}.card{{background:white;padding:40px;border-radius:15px;box-shadow:0 4px 20px rgba(0,0,0,0.1)}}h1{{color:#2e7d32}}p{{font-size:18px;color:#555}}.score{{font-size:40px;font-weight:bold;color:#f57c00;display:block;margin:20px 0}}</style></head><body><div class="card"><h1>🎉 观看成功！</h1><p>获得奖励</p><span class="score">+{points} 积分</span><p style="font-size:14px;color:#999">页面将自动关闭...</p></div><script>setTimeout(()=>{{if(window.Telegram&&window.Telegram.WebApp)window.Telegram.WebApp.close();else window.close()}},2500)</script></body></html>""")

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
    info = get_system_keys_info(); target = info[1] if type == 1 else info[3]
    if not target: return HTMLResponse("<h1>Wait Admin...</h1>")
    ad_url = DIRECT_LINK_1 if type == 1 else DIRECT_LINK_2
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>跳转中...</title><style>body{{font-family:Arial,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;background:#f0f2f5;margin:0}}.card{{background:white;padding:30px;border-radius:12px;text-align:center;box-shadow:0 4px 12px rgba(0,0,0,0.1)}}.loader{{border:4px solid #f3f3f3;border-top:4px solid #3498db;border-radius:50%;width:30px;height:30px;animation:spin 1s linear infinite;margin:20px auto}}@keyframes spin{{0%{{transform:rotate(0deg)}}100%{{transform:rotate(360deg)}}}}</style></head><body><div class="card"><h2>🚀 获取密钥中...</h2><div class="loader"></div><p id="msg">3 秒后跳转...</p></div><iframe src="{ad_url}" style="width:1px;height:1px;opacity:0;position:absolute;border:none"></iframe><script>let c=3;const m=document.getElementById('msg'),t="{target}";setInterval(()=>{{c--;if(c>0)m.innerText=c+" 秒后跳转...";else{{m.innerText="正在跳转...";window.location.href=t}}}},1000)</script></body></html>"""
    return HTMLResponse(content=html)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
