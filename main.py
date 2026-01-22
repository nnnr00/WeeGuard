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

# --- 配置 ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

# --- 自动清洗域名 ---
raw_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
RAILWAY_DOMAIN = raw_domain.replace("https://", "").replace("http://", "").strip("/")

# Moontag 直链
DIRECT_LINK_1 = "https://otieu.com/4/10489994"
DIRECT_LINK_2 = "https://otieu.com/4/10489998"

# --- 日志 ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- 全局变量 ---
tz_bj = pytz.timezone('Asia/Shanghai')
scheduler = AsyncIOScheduler(timezone=tz_bj)
bot_app = None

# --- 状态定义 ---
# 1. 管理员上传状态
WAITING_FOR_PHOTO = 1
# 2. 用户验证状态
WAITING_ORDER_ID = 10

# --- 数据库操作 ---

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """初始化数据库 (V3版 + 验证字段升级)"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. File ID 表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS file_ids_v3 (
            id SERIAL PRIMARY KEY,
            file_id TEXT NOT NULL,
            file_unique_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 2. 用户表 (升级)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users_v3 (
            user_id BIGINT PRIMARY KEY,
            points INTEGER DEFAULT 0,
            last_checkin_date DATE,
            checkin_count INTEGER DEFAULT 0,
            verify_fails INTEGER DEFAULT 0,
            verify_lock_until TIMESTAMP
        );
    """)
    
    # 3. 尝试添加新字段 (防止旧表缺少字段导致报错)
    try:
        cur.execute("ALTER TABLE users_v3 ADD COLUMN IF NOT EXISTS verify_fails INTEGER DEFAULT 0;")
        cur.execute("ALTER TABLE users_v3 ADD COLUMN IF NOT EXISTS verify_lock_until TIMESTAMP;")
    except Exception as e:
        print(f"Update column notice: {e}")
        conn.rollback()

    # 4. 视频广告表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_ads_v3 (
            user_id BIGINT PRIMARY KEY,
            last_watch_date DATE,
            daily_watch_count INTEGER DEFAULT 0
        );
    """)
    
    # 5. Token 表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ad_tokens_v3 (
            token TEXT PRIMARY KEY,
            user_id BIGINT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    # 6. 系统密钥表
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
    
    # 7. 密钥点击统计
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_key_clicks_v3 (
            user_id BIGINT PRIMARY KEY,
            click_count INTEGER DEFAULT 0,
            session_date DATE
        );
    """)
    
    # 8. 密钥领取记录
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

# --- 辅助逻辑 ---
def get_session_date():
    now = datetime.now(tz_bj)
    if now.hour < 10:
        return (now - timedelta(days=1)).date()
    return now.date()

def generate_random_key():
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(10))

# --- 数据库函数：验证逻辑 (新增) ---

def check_verify_status(user_id):
    """检查用户验证状态：返回 (fails, lock_until)"""
    ensure_user_exists(user_id)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT verify_fails, verify_lock_until FROM users_v3 WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row

def update_verify_fail(user_id, current_fails):
    """更新失败次数，如果达到2次则锁定5小时"""
    conn = get_db_connection()
    cur = conn.cursor()
    new_fails = current_fails + 1
    
    if new_fails >= 2:
        # 锁定 5 小时
        lock_time = datetime.now() + timedelta(hours=5)
        cur.execute("UPDATE users_v3 SET verify_fails = %s, verify_lock_until = %s WHERE user_id = %s", (new_fails, lock_time, user_id))
    else:
        cur.execute("UPDATE users_v3 SET verify_fails = %s WHERE user_id = %s", (new_fails, user_id))
        
    conn.commit()
    cur.close()
    conn.close()
    return new_fails

def clear_verify_lock(user_id):
    """验证成功或管理员重置"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users_v3 SET verify_fails = 0, verify_lock_until = NULL WHERE user_id = %s", (user_id,))
    conn.commit()
    cur.close()
    conn.close()

# --- 数据库函数：通用 ---

def ensure_user_exists(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO users_v3 (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    cur.execute("INSERT INTO user_ads_v3 (user_id, daily_watch_count) VALUES (%s, 0) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    conn.commit()
    cur.close()
    conn.close()

def save_file_id(file_id, file_unique_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO file_ids_v3 (file_id, file_unique_id) VALUES (%s, %s)", (file_id, file_unique_id))
    conn.commit()
    cur.close()
    conn.close()

def get_latest_file_id():
    """获取最新上传的一张图片用于展示"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT file_id FROM file_ids_v3 ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None

def get_all_files():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, file_id FROM file_ids_v3 ORDER BY id DESC LIMIT 10")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def delete_file_by_id(db_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM file_ids_v3 WHERE id = %s", (db_id,))
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

def get_ad_status(user_id):
    ensure_user_exists(user_id)
    conn = get_db_connection()
    cur = conn.cursor()
    today = datetime.now(tz_bj).date()
    cur.execute("SELECT last_watch_date, daily_watch_count FROM user_ads_v3 WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    last_date, count = row[0], row[1]
    if last_date != today: count = 0
    cur.close(); conn.close()
    return count

def process_ad_reward(user_id):
    ensure_user_exists(user_id)
    conn = get_db_connection()
    cur = conn.cursor()
    today = datetime.now(tz_bj).date()
    cur.execute("SELECT last_watch_date, daily_watch_count FROM user_ads_v3 WHERE user_id = %s FOR UPDATE", (user_id,))
    row = cur.fetchone()
    last_date, count = row[0], row[1]
    if last_date != today: count = 0
    if count >= 3:
        conn.rollback(); cur.close(); conn.close(); return {"status": "limit_reached"}
    points = 10 if count == 0 else (6 if count == 1 else random.randint(3, 10))
    cur.execute("UPDATE users_v3 SET points = points + %s WHERE user_id = %s", (points, user_id))
    cur.execute("UPDATE user_ads_v3 SET last_watch_date = %s, daily_watch_count = %s + 1 WHERE user_id = %s", (today, count, user_id))
    conn.commit(); cur.close(); conn.close()
    return {"status": "success", "added": points}

def update_system_keys(key1, key2, session_date):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE system_keys_v3 SET key_1 = %s, key_2 = %s, link_1 = NULL, link_2 = NULL, session_date = %s WHERE id = 1", (key1, key2, session_date))
    conn.commit(); cur.close(); conn.close()

def update_key_links(link1, link2):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE system_keys_v3 SET link_1 = %s, link_2 = %s WHERE id = 1", (link1, link2))
    conn.commit(); cur.close(); conn.close()

def get_system_keys_info():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT key_1, link_1, key_2, link_2, session_date FROM system_keys_v3 WHERE id = 1")
    row = cur.fetchone()
    cur.close(); conn.close()
    return row

def get_user_click_status(user_id):
    session_date = get_session_date()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT click_count, session_date FROM user_key_clicks_v3 WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    if not row or row[1] != session_date:
        cur.execute("INSERT INTO user_key_clicks_v3 (user_id, click_count, session_date) VALUES (%s, 0, %s) ON CONFLICT (user_id) DO UPDATE SET click_count = 0, session_date = %s", (user_id, session_date, session_date))
        conn.commit(); cur.close(); conn.close()
        return 0
    cur.close(); conn.close()
    return row[0]

def increment_user_click(user_id):
    session_date = get_session_date()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE user_key_clicks_v3 SET click_count = click_count + 1 WHERE user_id = %s AND session_date = %s", (user_id, session_date))
    conn.commit(); cur.close(); conn.close()

def claim_key_points(user_id, text_input):
    ensure_user_exists(user_id)
    info = get_system_keys_info()
    if not info: return {"status": "error"}
    k1, _, k2, _, _ = info
    matched_points = 0
    if text_input.strip() == k1: matched_points = 8
    elif text_input.strip() == k2: matched_points = 6
    else: return {"status": "invalid"}
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM user_key_claims_v3 WHERE user_id = %s AND key_val = %s", (user_id, text_input.strip()))
    if cur.fetchone(): cur.close(); conn.close(); return {"status": "already_claimed"}
    cur.execute("INSERT INTO user_key_claims_v3 (user_id, key_val) VALUES (%s, %s)", (user_id, text_input.strip()))
    cur.execute("UPDATE users_v3 SET points = points + %s WHERE user_id = %s RETURNING points", (matched_points, user_id))
    new_total = cur.fetchone()[0]
    conn.commit(); cur.close(); conn.close()
    return {"status": "success", "points": matched_points, "total": new_total}

def reset_admin_stats(admin_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE user_ads_v3 SET daily_watch_count = 0 WHERE user_id = %s", (admin_id,))
    cur.execute("UPDATE user_key_clicks_v3 SET click_count = 0 WHERE user_id = %s", (admin_id,))
    cur.execute("DELETE FROM user_key_claims_v3 WHERE user_id = %s", (admin_id,))
    cur.execute("UPDATE users_v3 SET verify_fails = 0, verify_lock_until = NULL WHERE user_id = %s", (admin_id,))
    conn.commit()
    cur.close()
    conn.close()

# --- Telegram Handlers ---

# 1. Start (首页)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_exists(user.id)
    
    # 获取验证锁定状态
    fails, lock_until = check_verify_status(user.id) or (0, None)
    is_locked = False
    lock_msg = ""
    
    if lock_until and datetime.now() < lock_until:
        is_locked = True
        remaining = lock_until - datetime.now()
        hours = int(remaining.total_seconds() // 3600)
        mins = int((remaining.total_seconds() % 3600) // 60)
        lock_msg = f"\n⚠️ 验证功能锁定中 (剩余 {hours}小时{mins}分)"

    text = (
        "👋 欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~\n\n"
        "📢 小卫小卫，守门员小卫！\n"
        "一键入群，小卫帮你搞定！\n"
        "新人来报到，小卫查身份！"
    )
    
    kb_list = []
    
    # 验证按钮状态控制
    if is_locked:
        kb_list.append([InlineKeyboardButton(f"🚫 验证锁定中 ({hours}h{mins}m)", callback_data="verify_locked")])
    else:
        kb_list.append([InlineKeyboardButton("🚀 开始验证", callback_data="start_verify_flow")])
        
    kb_list.append([InlineKeyboardButton("💰 我的积分", callback_data="my_points")])
    kb_list.append([InlineKeyboardButton("🎉 开业活动", callback_data="open_activity")])
    
    reply_markup = InlineKeyboardMarkup(kb_list)
    
    if update.callback_query:
        # 处理“验证锁定”点击
        if update.callback_query.data == "verify_locked":
            await update.callback_query.answer(f"⛔️ 验证失败次数过多，请 {hours}小时{mins}分 后再试。", show_alert=True)
            return
        
        # 正常刷新
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

# 2. 验证流程 (ConversationHandler)
async def verify_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """点击开始验证 -> 显示VIP说明"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    fails, lock_until = check_verify_status(user_id) or (0, None)
    
    if lock_until and datetime.now() < lock_until:
        await start(update, context) # 刷新回首页显示锁定
        return ConversationHandler.END

    text = (
        "💎 **VIP会员特权说明：**\n"
        "✅ 专属中转通道\n"
        "✅ 优先审核入群\n"
        "✅ 7x24小时客服支持\n"
        "✅ 定期福利活动"
    )
    
    # 尝试发送图片 (这里使用动态获取的 File ID，如果没有就只发文字)
    file_id = get_latest_file_id() 
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ 我已付款，开始验证", callback_data="i_paid")]])
    
    if file_id:
        try:
            await query.message.reply_photo(photo=file_id, caption=text, reply_markup=kb, parse_mode='Markdown')
            await query.delete_message() # 删除上一条纯文字菜单保持整洁
        except:
            await query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
    else:
        await query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
        
    return WAITING_ORDER_ID

async def ask_order_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """点击我已付款 -> 提示输入单号"""
    query = update.callback_query
    await query.answer()
    
    # 再次使用 File ID (或者你可以上传第二张不同的图作为教程)
    file_id = get_latest_file_id()
    
    text = (
        "📝 **查找订单号教程：**\n\n"
        "1. 打开支付软件 (微信/支付宝)\n"
        "2. 点击【我的】->【账单】\n"
        "3. 找到对应付款记录，点击进入【账单详情】\n"
        "4. 点击【更多】或直接复制【订单号】\n\n"
        "👇 **请在下方直接回复您的订单号：**"
    )
    
    if file_id:
        try:
            await query.message.reply_photo(photo=file_id, caption=text, parse_mode='Markdown')
            # 不删除上一条，保留VIP说明给用户参考，或者你可以选择删除
        except:
            await query.message.reply_text(text, parse_mode='Markdown')
    else:
        await query.message.reply_text(text, parse_mode='Markdown')
        
    return WAITING_ORDER_ID

async def check_order_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """验证用户输入的单号"""
    user_id = update.effective_user.id
    order_id = update.message.text.strip()
    
    # 验证逻辑：20260 开头
    if order_id.startswith("20260"):
        # 成功
        clear_verify_lock(user_id) # 清除可能的旧失败记录
        
        success_text = "✅ **验证成功！**\n\n欢迎加入VIP大家庭，请点击下方按钮入群。"
        # 这里的入群链接仅作示例，你可以换成你的
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("👉 点击加入会员群", url="https://t.me/+ExampleLink")]])
        
        await update.message.reply_text(success_text, reply_markup=kb, parse_mode='Markdown')
        
        # 延迟一点后发首页
        await asyncio.sleep(2)
        await start(update, context)
        return ConversationHandler.END
        
    else:
        # 失败
        fails, _ = check_verify_status(user_id) or (0, None)
        new_fails = update_verify_fail(user_id, fails)
        
        if new_fails >= 2:
            # 失败次数耗尽
            fail_text = (
                "❌ **验证失败 (2/2)**\n\n"
                "您输入的订单号格式错误或未查询到信息。\n"
                "⚠️ **由于连续失败两次，验证功能已锁定 5 小时。**\n"
                "请稍后重试，或联系客服。"
            )
            await update.message.reply_text(fail_text, parse_mode='Markdown')
            await start(update, context) # 回首页
            return ConversationHandler.END
        else:
            # 还有一次机会
            retry_text = (
                "❌ **未查询到订单信息，请重试。**\n\n"
                f"您还有 **{2 - new_fails}** 次机会。\n"
                "请仔细核对订单号 (通常以 20260 开头)，再次发送："
            )
            await update.message.reply_text(retry_text, parse_mode='Markdown')
            return WAITING_ORDER_ID # 保持在输入状态

async def cancel_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """超时或取消"""
    await update.message.reply_text("验证已取消。")
    await start(update, context)
    return ConversationHandler.END

# 3. 全局监听 (回首页)
async def global_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """任何非状态内的消息，都显示首页"""
    # 排除命令
    if update.message.text and update.message.text.startswith('/'):
        return 
    await start(update, context)

# 4. 其他原有功能 (积分, 活动, Admin)
async def verify_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("功能维护中...")

async def jf_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = get_user_data(user.id)
    today = datetime.now(tz_bj).date()
    status_text = "已签到 ✅" if data[1] == today else "未签到 ❌"
    text = f"💰 **积分中心**\n💎 积分：`{data[0]}`\n📅 状态：{status_text}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📅 立即签到", callback_data="do_checkin")], [InlineKeyboardButton("🔙 返回首页", callback_data="back_to_home")]])
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
    else: await update.message.reply_text(text, reply_markup=kb, parse_mode='Markdown')

async def checkin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    result = process_checkin(update.effective_user.id)
    if result["status"] == "already_checked":
        await query.answer("已签到！", show_alert=True)
    else:
        msg = f"🎉 **签到成功！** +{result['added']} 积分"
        await query.answer("成功！")
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回首页", callback_data="back_to_home")]]), parse_mode='Markdown')

async def activity_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_exists(user.id)
    count = get_ad_status(user.id)
    token = create_ad_token(user.id)
    watch_url = f"https://{RAILWAY_DOMAIN}/watch_ad/{token}"
    test_url = f"https://{RAILWAY_DOMAIN}/test_page"
    
    text = (
        "🎉 **开业活动中心**\n\n"
        f"1️⃣ **观看视频得积分** ({count}/3)\n"
        "2️⃣ **夸克网盘取密钥** (🔥推荐)\n\n"
        "🛠 **功能测试**\n"
        "点击测试按钮体验流程。"
    )
    kb_list = []
    if count < 3: kb_list.append([InlineKeyboardButton("📺 看视频 (积分)", url=watch_url)])
    else: kb_list.append([InlineKeyboardButton("✅ 今日已完成 (3/3)", callback_data="none")])
    kb_list.append([InlineKeyboardButton("🔑 获取今日密钥", callback_data="get_quark_key")])
    kb_list.append([InlineKeyboardButton("🛠 测试按钮", url=test_url)])
    kb_list.append([InlineKeyboardButton("🔙 返回首页", callback_data="back_to_home")])
    
    if update.callback_query: 
        if update.callback_query.data == "none": await update.callback_query.answer("明天再来！", show_alert=True); return
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb_list), parse_mode='Markdown')
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb_list), parse_mode='Markdown')

async def quark_key_btn_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    await query.answer()
    info = get_system_keys_info()
    if not info or not info[1]: await query.message.reply_text("⏳ 密钥初始化中..."); return
    clicks = get_user_click_status(user.id)
    if clicks >= 2: await query.message.reply_text("⚠️ 今日次数已用完。"); return
    target = 1 if clicks == 0 else 2
    increment_user_click(user.id)
    url = f"https://{RAILWAY_DOMAIN}/jump?type={target}"
    msg = f"🚀 **获取密钥**\n链接：\n{url}\n点击跳转 -> 存网盘 -> 复制文件名 -> 发给机器人。"
    await context.bot.send_message(chat_id=user.id, text=msg)

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 此函数只处理密钥验证，订单号验证由 ConversationHandler 接管
    user_id = update.effective_user.id
    text = update.message.text
    if text.startswith('/'): return
    result = claim_key_points(user_id, text)
    if result["status"] == "success":
        await update.message.reply_text(f"✅ **成功！** +{result['points']}分", parse_mode='Markdown')
    elif result["status"] == "already_claimed":
        await update.message.reply_text("⚠️ 密钥已使用。")
    else:
        # 如果不是密钥，也不是验证流程中，则回首页
        await start(update, context)

# --- Admin ---
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
    await update.message.reply_text("✅ **测试数据已重置。**")

async def start_upload_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📤 请发送图片", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")]]))
    return WAITING_FOR_PHOTO

async def handle_photo_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return ConversationHandler.END
    photo = update.message.photo[-1]
    save_file_id(photo.file_id, photo.file_unique_id)
    await update.message.reply_text(f"✅ ID Saved: `{photo.file_id}`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")]]), parse_mode='Markdown')
    return WAITING_FOR_PHOTO

async def view_files_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    files = get_all_files()
    if not files: await query.edit_message_text("📭 无记录。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")]])); return ConversationHandler.END
    await query.message.reply_text("📂 **图片:**", parse_mode='Markdown')
    for db_id, f_id in files:
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=f_id, caption=f"ID: `{db_id}`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"🗑 删除 {db_id}", callback_data=f"pre_del_{db_id}")]]), parse_mode='Markdown')
    await context.bot.send_message(chat_id=update.effective_chat.id, text="--- End ---", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")]]))
    return ConversationHandler.END

async def pre_delete_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db_id = query.data.split("_")[-1]
    await query.edit_message_caption(caption=f"⚠️ 确认删除 ID {db_id}?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 确认", callback_data=f"confirm_del_{db_id}"), InlineKeyboardButton("❌ 取消", callback_data="cancel_del")]]), parse_mode='Markdown')

async def execute_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db_id = query.data.split("_")[-1]
    delete_file_by_id(db_id)
    await query.delete_message()
    await context.bot.send_message(chat_id=update.effective_chat.id, text="已删除", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")]]))

async def cancel_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("已取消")
    await update.callback_query.edit_message_caption("操作已取消", reply_markup=None)

async def my_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    info = get_system_keys_info()
    if not info or not info[1]: update_system_keys(generate_random_key(), generate_random_key(), date.today()); info = get_system_keys_info()
    k1, l1, k2, l2, date_s = info
    msg = f"👮‍♂️ **密钥管理** ({date_s})\nK1: `{k1}`\nL1: {l1 or '❌'}\nK2: `{k2}`\nL2: {l2 or '❌'}\n👇 发送【密钥 1】新链接:"
    await update.message.reply_text(msg, parse_mode='Markdown')
    return WAITING_LINK_1

async def receive_link_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_link_1'] = update.message.text
    await update.message.reply_text("✅ 已记录 L1。👇 发送【密钥 2】新链接：")
    return WAITING_LINK_2

async def receive_link_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_key_links(context.user_data['new_link_1'], update.message.text)
    await update.message.reply_text("✅ **更新完毕！**")
    return ConversationHandler.END

async def cancel_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 取消。")
    return ConversationHandler.END

async def daily_reset_task():
    k1, k2 = generate_random_key(), generate_random_key()
    update_system_keys(k1, k2, date.today())
    if bot_app and ADMIN_ID: await bot_app.bot.send_message(chat_id=ADMIN_ID, text=f"🔔 每日密钥更新\nK1: `{k1}`\nK2: `{k2}`", parse_mode='Markdown')

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
    
    # Handlers Registration
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CallbackQueryHandler(start, pattern="^back_to_home$"))
    
    # 验证流程 (Priority High)
    verify_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(verify_entry, pattern="^start_verify_flow$")],
        states={
            WAITING_ORDER_ID: [
                CallbackQueryHandler(ask_order_id, pattern="^i_paid$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, check_order_id)
            ]
        },
        fallbacks=[CommandHandler("start", start), CommandHandler("cancel", cancel_verify)],
        per_message=False
    )
    bot_app.add_handler(verify_conv)
    
    # Other features
    bot_app.add_handler(CommandHandler("hd", activity_handler))
    bot_app.add_handler(CallbackQueryHandler(activity_handler, pattern="^open_activity$"))
    bot_app.add_handler(CallbackQueryHandler(quark_key_btn_handler, pattern="^get_quark_key$"))
    bot_app.add_handler(CommandHandler("jf", jf_command_handler))
    bot_app.add_handler(CallbackQueryHandler(jf_command_handler, pattern="^my_points$"))
    bot_app.add_handler(CallbackQueryHandler(checkin_handler, pattern="^do_checkin$"))
    bot_app.add_handler(CommandHandler("c", clear_command))
    bot_app.add_handler(CommandHandler("cz", cz_command))

    # Admin Conv
    admin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_upload_flow, pattern="^start_upload$"), CommandHandler("id", lambda u, c: start_upload_flow(u, c))],
        states={WAITING_FOR_PHOTO: [MessageHandler(filters.PHOTO, handle_photo_upload), CallbackQueryHandler(admin_entry, pattern="^back_to_admin$")]},
        fallbacks=[CommandHandler("admin", admin_entry)], per_message=False
    )
    bot_app.add_handler(CommandHandler("admin", admin_entry))
    bot_app.add_handler(CallbackQueryHandler(admin_entry, pattern="^back_to_admin$"))
    bot_app.add_handler(CallbackQueryHandler(view_files_flow, pattern="^view_files$"))
    bot_app.add_handler(CallbackQueryHandler(pre_delete_check, pattern="^pre_del_"))
    bot_app.add_handler(CallbackQueryHandler(execute_delete, pattern="^confirm_del_"))
    bot_app.add_handler(CallbackQueryHandler(cancel_delete, pattern="^cancel_del$"))
    bot_app.add_handler(admin_conv)

    key_conv = ConversationHandler(
        entry_points=[CommandHandler("my", my_command)],
        states={
            WAITING_LINK_1: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_link_1)],
            WAITING_LINK_2: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_link_2)],
        },
        fallbacks=[CommandHandler("cancel", cancel_admin)]
    )
    bot_app.add_handler(key_conv)
    
    # 密钥验证 (非会话)
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    
    yield
    if bot_app: await bot_app.stop(); await bot_app.shutdown()
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def health_check(): return {"status": "running"}

@app.get("/watch_ad/{token}", response_class=HTMLResponse)
async def watch_ad_page(token: str):
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>视频任务</title><script src="https://telegram.org/js/telegram-web-app.js"></script><script src='https://libtl.com/sdk.js' data-zone='10489957' data-sdk='show_10489957'></script><style>body{{font-family:sans-serif;text-align:center;padding:20px;background:#f4f4f9;display:flex;flex-direction:column;justify-content:center;height:90vh}}.container{{max-width:500px;margin:0 auto;background:white;padding:30px;border-radius:12px;box-shadow:0 4px 15px rgba(0,0,0,0.1)}}.btn{{padding:15px 30px;background:#0088cc;color:white;border:none;border-radius:8px;font-size:18px;cursor:pointer;width:100%}}.btn:disabled{{background:#ccc}}#status{{margin-top:20px;font-size:16px;color:#555}}.progress{{width:100%;background-color:#ddd;border-radius:5px;margin-top:15px;height:10px;display:none}}.bar{{width:0%;height:100%;background-color:#4CAF50;border-radius:5px;transition:width 1s linear}}</style></head><body><div class="container"><h2>📺 观看广告获取积分</h2><p style="color:#666;margin-bottom:25px">请点击下方按钮，保持页面开启 15 秒。</p><button id="adBtn" class="btn" onclick="startProcess()">▶️ 开始观看</button><div class="progress" id="progress"><div class="bar" id="bar"></div></div><div id="status"></div></div><script>const token="{token}",s=document.getElementById('status'),btn=document.getElementById('adBtn'),bar=document.getElementById('bar'),p=document.getElementById('progress');if(window.Telegram&&window.Telegram.WebApp)window.Telegram.WebApp.ready();function startProcess(){{btn.disabled=!0;s.innerText="⏳ 正在加载...";if(typeof show_10489957==='function')show_10489957().catch(e=>console.log(e));s.innerText="📺 广告观看中...";p.style.display='block';let t=15;const timer=setInterval(()=>{{t--;bar.style.width=((15-t)/15)*100+"%";if(t<=0){{clearInterval(timer);v();}}else{{s.innerText="📺 剩余: "+t+"秒";}}}},1000)}}function v(){{s.innerText="✅ 正在验证...";fetch('/api/verify_ad',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{token:token}})}}).then(r=>r.json()).then(d=>{{if(d.success)window.location.href="/ad_success?points="+d.points;else{{s.innerText="❌ "+d.message;btn.disabled=!1}}}}).catch(e=>{{s.innerText="❌ 网络错误";btn.disabled=!1}})}}</script></body></html>"""
    return HTMLResponse(content=html)

@app.get("/ad_success", response_class=HTMLResponse)
async def success_page(points: int = 0):
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>成功</title><script src="https://telegram.org/js/telegram-web-app.js"></script><style>body{{font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;background-color:#e8f5e9;text-align:center;margin:0}}.card{{background:white;padding:40px;border-radius:15px;box-shadow:0 4px 20px rgba(0,0,0,0.1)}}h1{{color:#2e7d32}}p{{font-size:18px;color:#555}}.score{{font-size:40px;font-weight:bold;color:#f57c00;display:block;margin:20px 0}}</style></head><body><div class="card"><h1>🎉 观看成功！</h1><p>获得奖励</p><span class="score">+{points} 积分</span><p style="font-size:14px;color:#999">页面将自动关闭...</p></div><script>setTimeout(()=>{{if(window.Telegram&&window.Telegram.WebApp)window.Telegram.WebApp.close();else window.close()}},2500)</script></body></html>"""
    return HTMLResponse(content=html)

@app.get("/test_page", response_class=HTMLResponse)
async def test_page():
    html = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>测试模式</title><script src="https://telegram.org/js/telegram-web-app.js"></script><style>body{font-family:sans-serif;text-align:center;padding:20px;background:#fff3e0;display:flex;flex-direction:column;justify-content:center;height:90vh}.container{background:white;padding:30px;border-radius:12px;box-shadow:0 4px 10px rgba(0,0,0,0.1)}.btn{padding:15px 30px;background:#ff9800;color:white;border:none;border-radius:8px;font-size:18px;cursor:pointer;width:100%}.btn:disabled{background:#ccc}#status{margin-top:20px;font-weight:bold;color:#555}</style></head><body><div class="container"><h2>🛠 测试模式</h2><p>简陋测试页。</p><button id="btn" class="btn" onclick="startTest()">🖱 点击测试</button><div id="status"></div></div><script>function startTest(){const btn=document.getElementById('btn'),s=document.getElementById('status');btn.disabled=!0;let c=3;const t=setInterval(()=>{c--;if(c<=0){clearInterval(t);s.innerText="✅ 模拟成功! 跳转中...";setTimeout(()=>{window.location.href="/ad_success?points=0"},1000)}else{s.innerText="⏳ "+c}},1000)}</script></body></html>"""
    return HTMLResponse(content=html)

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
