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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

# --- 核心修复：404错误根源 ---
# 获取域名，默认为空
raw_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")

# 自动清洗域名：去除 https://, http:// 和末尾的 /
# 确保最终格式只是 "xxx.up.railway.app"
RAILWAY_DOMAIN = raw_domain.replace("https://", "").replace("http://", "").strip("/")

# Moontag 直链配置 (用于隐形加载)
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

# 状态机状态 (管理员后台用)
WAITING_FOR_PHOTO = 1
WAITING_LINK_1 = 2
WAITING_LINK_2 = 3

# --- 数据库操作 ---

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """初始化数据库 (V3版)"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. 基础表 (v3)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS file_ids_v3 (
            id SERIAL PRIMARY KEY,
            file_id TEXT NOT NULL,
            file_unique_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users_v3 (
            user_id BIGINT PRIMARY KEY,
            points INTEGER DEFAULT 0,
            last_checkin_date DATE,
            checkin_count INTEGER DEFAULT 0
        );
    """)
    
    # 2. 视频广告表 (v3)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_ads_v3 (
            user_id BIGINT PRIMARY KEY,
            last_watch_date DATE,
            daily_watch_count INTEGER DEFAULT 0
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ad_tokens_v3 (
            token TEXT PRIMARY KEY,
            user_id BIGINT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    # 3. 密钥系统中转表 (v3)
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
    
    # user_key_clicks (v3)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_key_clicks_v3 (
            user_id BIGINT PRIMARY KEY,
            click_count INTEGER DEFAULT 0,
            session_date DATE
        );
    """)
    
    # user_key_claims (v3)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_key_claims_v3 (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            key_val TEXT,
            claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, key_val)
        );
    """)
    
    # 初始化 system_keys 行
    cur.execute("INSERT INTO system_keys_v3 (id, session_date) VALUES (1, %s) ON CONFLICT (id) DO NOTHING", (date(2000,1,1),))
    
    conn.commit()
    cur.close()
    conn.close()

# --- 辅助逻辑 ---
def get_session_date():
    """获取当前业务日期 (以北京时间10:00AM为界)"""
    now = datetime.now(tz_bj)
    if now.hour < 10:
        return (now - timedelta(days=1)).date()
    return now.date()

def generate_random_key():
    """生成10位随机密钥"""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(10))

# --- 数据库函数集合 (V3) ---

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
    cur.execute("""
        UPDATE system_keys_v3 
        SET key_1 = %s, key_2 = %s, link_1 = NULL, link_2 = NULL, session_date = %s
        WHERE id = 1
    """, (key1, key2, session_date))
    conn.commit()
    cur.close()
    conn.close()

def update_key_links(link1, link2):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE system_keys_v3 SET link_1 = %s, link_2 = %s WHERE id = 1", (link1, link2))
    conn.commit()
    cur.close()
    conn.close()

def get_system_keys_info():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT key_1, link_1, key_2, link_2, session_date FROM system_keys_v3 WHERE id = 1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row

def get_user_click_status(user_id):
    session_date = get_session_date()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT click_count, session_date FROM user_key_clicks_v3 WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    
    if not row or row[1] != session_date:
        cur.execute("""
            INSERT INTO user_key_clicks_v3 (user_id, click_count, session_date) 
            VALUES (%s, 0, %s) 
            ON CONFLICT (user_id) DO UPDATE SET click_count = 0, session_date = %s
        """, (user_id, session_date, session_date))
        conn.commit()
        return 0
    
    cur.close()
    conn.close()
    return row[0]

def increment_user_click(user_id):
    session_date = get_session_date()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE user_key_clicks_v3 SET click_count = click_count + 1 
        WHERE user_id = %s AND session_date = %s
    """, (user_id, session_date))
    conn.commit()
    cur.close()
    conn.close()

def claim_key_points(user_id, text_input):
    ensure_user_exists(user_id)
    info = get_system_keys_info()
    if not info: return {"status": "error"}
    
    k1, _, k2, _, _ = info
    
    matched_points = 0
    
    if text_input.strip() == k1:
        matched_points = 8
    elif text_input.strip() == k2:
        matched_points = 6
    else:
        return {"status": "invalid"}

    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT id FROM user_key_claims_v3 WHERE user_id = %s AND key_val = %s", (user_id, text_input.strip()))
    if cur.fetchone():
        cur.close(); conn.close()
        return {"status": "already_claimed"}
    
    cur.execute("INSERT INTO user_key_claims_v3 (user_id, key_val) VALUES (%s, %s)", (user_id, text_input.strip()))
    cur.execute("UPDATE users_v3 SET points = points + %s WHERE user_id = %s RETURNING points", (matched_points, user_id))
    new_total = cur.fetchone()[0]
    
    conn.commit()
    cur.close()
    conn.close()
    
    return {"status": "success", "points": matched_points, "total": new_total}

# --- Telegram Bot Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_exists(user.id)
    text = f"👋 你好，{user.first_name}！\n欢迎使用功能："
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 开始验证", callback_data="start_verify")],
        [InlineKeyboardButton("💰 我的积分", callback_data="my_points")],
        [InlineKeyboardButton("🎉 开业活动", callback_data="open_activity")]
    ])
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=kb)
    else: await update.message.reply_text(text, reply_markup=kb)

async def verify_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("开发中...")
    await query.message.reply_text("✅ 验证请求已收到。")

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
    
    # 强制使用 https 并使用清洗后的域名
    # 如果 RAILWAY_DOMAIN 为空，这里会生成 https:///... 方便在日志里发现错误
    watch_url = f"https://{RAILWAY_DOMAIN}/watch_ad/{token}"
    
    text = (
        "🎉 **开业活动中心**\n\n"
        f"1️⃣ **观看视频得积分** ({count}/3)\n"
        "2️⃣ **夸克网盘取密钥** (🔥推荐)\n"
        "点击按钮 -> 跳转中转站(3秒) -> 存网盘 -> 复制文件名(密钥) -> 发给机器人。\n"
        "⚠️ **注意：** 每天北京时间 10:00 重置。"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📺 看视频 (积分)", url=watch_url)],
        [InlineKeyboardButton("🔑 获取今日密钥", callback_data="get_quark_key")],
        [InlineKeyboardButton("🔙 返回首页", callback_data="back_to_home")]
    ])
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
    else: await update.message.reply_text(text, reply_markup=kb, parse_mode='Markdown')

async def quark_key_btn_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    await query.answer()
    
    info = get_system_keys_info()
    if not info or not info[1]:
        await query.message.reply_text("⏳ **密钥正在初始化...**\n请稍后或联系管理员。")
        return

    clicks = get_user_click_status(user.id)
    if clicks >= 2:
        await query.message.reply_text("⚠️ **今日次数已用完 (2/2)。**")
        return
    
    target_type = 1 if clicks == 0 else 2
    increment_user_click(user.id)
    
    jump_url = f"https://{RAILWAY_DOMAIN}/jump?type={target_type}"
    
    name_ref = "密钥1" if target_type == 1 else "密钥2"
    msg = (
        f"🚀 **获取 {name_ref}**\n\n"
        f"链接 (点击 {clicks+1}/2)：\n{jump_url}\n\n"
        "点击链接 -> 等待跳转 -> 保存文件 -> 复制文件名 -> 发给机器人。"
    )
    await context.bot.send_message(chat_id=user.id, text=msg)

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    if text.startswith('/'): return
    result = claim_key_points(user_id, text)
    if result["status"] == "success":
        await update.message.reply_text(f"✅ **成功！**\n积分：+{result['points']}\n总分：`{result['total']}`", parse_mode='Markdown')
    elif result["status"] == "already_claimed":
        await update.message.reply_text("⚠️ 密钥已使用。")

# --- Admin ---
async def admin_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 获取新 File ID", callback_data="start_upload")],
        [InlineKeyboardButton("📂 查看已存图片 & 管理", callback_data="view_files")]
    ])
    await update.message.reply_text("⚙️ **管理员后台**", reply_markup=kb, parse_mode='Markdown')
    return ConversationHandler.END

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
    if not files:
        await query.edit_message_text("📭 无记录。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")]]))
        return ConversationHandler.END
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
    if not info or not info[1]:
        update_system_keys(generate_random_key(), generate_random_key(), date.today())
        info = get_system_keys_info()

    k1, l1, k2, l2, date_s = info
    msg = (
        f"👮‍♂️ **密钥管理** ({date_s})\n"
        f"K1: `{k1}`\nL1: {l1 or '❌'}\n\n"
        f"K2: `{k2}`\nL2: {l2 or '❌'}\n\n"
        "👇 发送【密钥 1】新链接:"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')
    return WAITING_LINK_1

async def receive_link_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_link_1'] = update.message.text
    await update.message.reply_text("✅ 已记录 L1。👇 发送【密钥 2】新链接：")
    return WAITING_LINK_2

async def receive_link_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link2 = update.message.text
    link1 = context.user_data['new_link_1']
    update_key_links(link1, link2)
    await update.message.reply_text("✅ **更新完毕！**")
    return ConversationHandler.END

async def cancel_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 取消。")
    return ConversationHandler.END

async def daily_reset_task():
    key1 = generate_random_key()
    key2 = generate_random_key()
    update_system_keys(key1, key2, date.today())
    logger.info(f"Daily keys reset: {key1}, {key2}")
    if bot_app and ADMIN_ID:
        try:
            msg = f"🔔 **每日密钥更新**\n\n🔑 K1: `{key1}`\n🔑 K2: `{key2}`\n⚠️ 原链接失效，请用 /my 绑定新链接。"
            await bot_app.bot.send_message(chat_id=ADMIN_ID, text=msg, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Failed to send admin msg: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 打印清洗后的域名，方便调试 404
    print(f"-------- RAILWAY DOMAIN: {RAILWAY_DOMAIN} --------")
    
    init_db()
    print("Database Initialized (v3 tables).")
    
    info = get_system_keys_info()
    if not info or info[4] == date(2000, 1, 1):
        print("Generating Initial Keys...")
        update_system_keys(generate_random_key(), generate_random_key(), date.today())
    
    scheduler.add_job(daily_reset_task, 'cron', hour=10, minute=0, timezone=tz_bj)
    scheduler.start()
    
    global bot_app
    bot_app = Application.builder().token(BOT_TOKEN).build()
    
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CallbackQueryHandler(start, pattern="^back_to_home$"))
    bot_app.add_handler(CommandHandler("hd", activity_handler))
    bot_app.add_handler(CallbackQueryHandler(activity_handler, pattern="^open_activity$"))
    bot_app.add_handler(CallbackQueryHandler(quark_key_btn_handler, pattern="^get_quark_key$"))
    bot_app.add_handler(CommandHandler("jf", jf_command_handler))
    bot_app.add_handler(CallbackQueryHandler(jf_command_handler, pattern="^my_points$"))
    bot_app.add_handler(CallbackQueryHandler(checkin_handler, pattern="^do_checkin$"))
    bot_app.add_handler(CallbackQueryHandler(verify_handler, pattern="^start_verify$"))

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

@app.get("/watch_ad/{token}", response_class=HTMLResponse)
async def watch_ad_page(token: str):
    # 使用你要求的具体 SDK 代码逻辑
    html_content = f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>视频任务</title>
        <script src='//libtl.com/sdk.js' data-zone='10489957' data-sdk='show_10489957'></script>
        <style>
            body {{ font-family: sans-serif; text-align: center; padding: 20px; background: #f4f4f9; }}
            .container {{ max-width: 500px; margin: 0 auto; background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            .btn {{ padding: 12px 24px; background: #0088cc; color: white; border: none; border-radius: 5px; font-size: 16px; cursor: pointer; }}
            #s {{ margin-top: 15px; font-weight: bold; color: #555; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>📺 观看广告获取积分</h2>
            <p>点击按钮，看完广告后点击确认。</p>
            <button id="adBtn" class="btn" onclick="startAd()">开始观看</button>
            <div id="s"></div>
        </div>

        <script>
        const token = "{token}";
        const s = document.getElementById('s');
        const btn = document.getElementById('adBtn');
        
        function startAd() {{
            btn.disabled = true;
            s.innerText = "⏳ 正在请求广告...";
            
            if (typeof show_10489957 === 'function') {{
                // 你要求的特定代码格式
                show_10489957().then(() => {{
                    alert('You have seen an ad!');
                    // 验证并获得积分
                    verify();
                }}).catch(e => {{
                    console.log(e);
                    s.innerText = "❌ 广告加载失败或被关闭";
                    btn.disabled = false;
                }});
            }} else {{
                s.innerText = "❌ SDK 未加载，请关闭广告拦截插件";
                btn.disabled = false;
            }}
        }}

        function verify() {{
            s.innerText = "✅ 正在验证奖励...";
            fetch('/api/verify_ad', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ token: token }})
            }})
            .then(r => r.json())
            .then(d => {{
                if(d.success) {{
                    s.innerHTML = "🎉 成功! +"+d.points+"分<br>现在可以关闭页面返回 Telegram";
                    btn.style.display = 'none';
                }} else {{
                    s.innerText = "❌ " + d.message;
                }}
            }});
        }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.post("/api/verify_ad")
async def verify_ad_api(payload: dict):
    user_id = verify_token(payload.get("token"))
    if not user_id: return JSONResponse({"success": False, "message": "Expired"})
    res = process_ad_reward(user_id)
    return JSONResponse({"success": res["status"]=="success", "points": res.get("added"), "message": res.get("status")})

@app.get("/jump", response_class=HTMLResponse)
async def jump_page(request: Request, type: int = 1):
    info = get_system_keys_info()
    if not info: return HTMLResponse("<h1>🚫 系统维护中</h1>")
    target_link = info[1] if type == 1 else info[3]
    if not target_link: return HTMLResponse("<h1>⏳ 等待管理员更新...</h1>")
    
    moontag_ad = DIRECT_LINK_1 if type == 1 else DIRECT_LINK_2
    
    html = f"""
    <!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>跳转中...</title>
    <style>body{{font-family:Arial,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;background:#f0f2f5;margin:0}} .card{{background:white;padding:30px;border-radius:12px;text-align:center;box-shadow:0 4px 12px rgba(0,0,0,0.1)}} .loader{{border:4px solid #f3f3f3;border-top:4px solid #3498db;border-radius:50%;width:30px;height:30px;animation:spin 1s linear infinite;margin:20px auto}} @keyframes spin{{0%{{transform:rotate(0deg)}}100%{{transform:rotate(360deg)}}}}</style>
    </head><body>
        <div class="card"><h2>🚀 正在为您获取密钥...</h2><div class="loader"></div><p id="msg">3 秒后跳转...</p></div>
        <iframe src="{moontag_ad}" style="width:1px;height:1px;opacity:0;position:absolute;border:none;"></iframe>
        <script>
            let count = 3; const msg = document.getElementById('msg'); const target = "{target_link}";
            setInterval(() => {{ count--; if(count > 0) msg.innerText = count + " 秒后跳转..."; else {{ msg.innerText = "正在跳转..."; window.location.href = target; }} }}, 1000);
        </script>
    </body></html>
    """
    return HTMLResponse(content=html)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
