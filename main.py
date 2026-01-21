import os
import logging
import asyncio
import hashlib
import time
import secrets
import string
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager
import threading

import psycopg2
from psycopg2.extras import RealDictCursor

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    CallbackQueryHandler, 
    MessageHandler, 
    filters, 
    ContextTypes
)

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# ==================== 配置 ====================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
API_BASE_URL = os.getenv("API_BASE_URL", "https://your-railway-app.up.railway.app")
GITHUB_PAGES_URL = os.getenv("GITHUB_PAGES_URL", "https://your-username.github.io/your-repo")

BEIJING_TZ = ZoneInfo("Asia/Shanghai")

# 用户状态常量
WAITING_FOR_PHOTO = "waiting_for_photo"
WAITING_FOR_SECRET_KEY = "waiting_for_secret_key"
WAITING_FOR_KEY1_LINK = "waiting_for_key1_link"
WAITING_FOR_KEY2_LINK = "waiting_for_key2_link"

# Monetag 直链（固定不变）
MONETAG_LINK_1 = "https://otieu.com/4/10489994"
MONETAG_LINK_2 = "https://otieu.com/4/10489998"

# Telegram 应用实例（全局）
telegram_app = None
scheduler = None
# ==================== 数据库操作 ====================

def get_connection():
    """获取数据库连接"""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_database():
    """初始化数据库表（如果不存在则创建，保留原有数据）"""
    conn = get_connection()
    cur = conn.cursor()
    
    # 创建 file_ids 表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS file_ids (
            id SERIAL PRIMARY KEY,
            file_id TEXT NOT NULL,
            file_type TEXT DEFAULT 'photo',
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 创建用户积分表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_points (
            user_id BIGINT PRIMARY KEY,
            points INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 创建签到记录表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS checkin_records (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            checkin_date DATE NOT NULL,
            points_earned INTEGER NOT NULL,
            is_first_checkin BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, checkin_date)
        )
    """)
    
    # 创建广告观看记录表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ad_watch_records (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            watch_date DATE NOT NULL,
            watch_count INTEGER DEFAULT 0,
            points_earned INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, watch_date)
        )
    """)
    
    # 创建广告验证令牌表（防作弊）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ad_tokens (
            id SERIAL PRIMARY KEY,
            token TEXT UNIQUE NOT NULL,
            user_id BIGINT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            used BOOLEAN DEFAULT FALSE,
            ip_address TEXT,
            user_agent TEXT
        )
    """)
    
    # 创建每日密钥表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_secret_keys (
            id SERIAL PRIMARY KEY,
            key_date DATE NOT NULL,
            key1 TEXT NOT NULL,
            key2 TEXT NOT NULL,
            key1_link TEXT,
            key2_link TEXT,
            link_updated BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(key_date)
        )
    """)
    
    # 创建密钥领取记录表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS secret_key_claims (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            claim_date DATE NOT NULL,
            key_type INTEGER NOT NULL,
            points_earned INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, claim_date, key_type)
        )
    """)
    
    # 创建密钥点击次数记录表（每天北京时间10点重置）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS secret_key_clicks (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            click_date DATE NOT NULL,
            click_count INTEGER DEFAULT 0,
            reset_hour INTEGER DEFAULT 10,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, click_date)
        )
    """)
    
    conn.commit()
    cur.close()
    conn.close()
    logger.info("✅ 数据库初始化完成（保留原有数据）")
    # -------------------- File ID 操作 --------------------

def save_file_id(file_id: str, file_type: str = "photo", description: str = None):
    """保存 File ID 到数据库"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute(
        "INSERT INTO file_ids (file_id, file_type, description) VALUES (%s, %s, %s) RETURNING id",
        (file_id, file_type, description)
    )
    
    record_id = cur.fetchone()['id']
    conn.commit()
    cur.close()
    conn.close()
    return record_id

def get_all_file_ids():
    """获取所有保存的 File ID"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT id, file_id, file_type, description, created_at FROM file_ids ORDER BY created_at DESC")
    records = cur.fetchall()
    
    cur.close()
    conn.close()
    return records

def delete_file_id(record_id: int):
    """删除指定的 File ID 记录"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("DELETE FROM file_ids WHERE id = %s", (record_id,))
    deleted = cur.rowcount > 0
    
    conn.commit()
    cur.close()
    conn.close()
    return deleted

def get_file_by_id(record_id: int):
    """根据 ID 获取单条记录"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM file_ids WHERE id = %s", (record_id,))
    record = cur.fetchone()
    
    cur.close()
    conn.close()
    return record
    # -------------------- 积分操作 --------------------

def get_user_points(user_id: int) -> int:
    """获取用户积分"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT points FROM user_points WHERE user_id = %s", (user_id,))
    result = cur.fetchone()
    
    cur.close()
    conn.close()
    
    return result['points'] if result else 0

def add_user_points(user_id: int, points: int) -> int:
    """增加用户积分，返回新的积分总数"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        INSERT INTO user_points (user_id, points, updated_at) 
        VALUES (%s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (user_id) 
        DO UPDATE SET points = user_points.points + %s, updated_at = CURRENT_TIMESTAMP
        RETURNING points
    """, (user_id, points, points))
    
    new_points = cur.fetchone()['points']
    conn.commit()
    cur.close()
    conn.close()
    
    return new_points

def init_user_points(user_id: int):
    """初始化用户积分记录"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        INSERT INTO user_points (user_id, points) 
        VALUES (%s, 0)
        ON CONFLICT (user_id) DO NOTHING
    """, (user_id,))
    
    conn.commit()
    cur.close()
    conn.close()

# -------------------- 签到操作 --------------------

def get_beijing_now():
    """获取北京时间当前时间"""
    return datetime.now(BEIJING_TZ)

def get_beijing_date():
    """获取北京时间日期"""
    return datetime.now(BEIJING_TZ).date()

def get_secret_key_date():
    """获取密钥日期（北京时间10点后为当天，10点前为前一天）"""
    now = get_beijing_now()
    if now.hour < 10:
        return (now - timedelta(days=1)).date()
    return now.date()

def check_user_checkin_today(user_id: int) -> bool:
    """检查用户今天是否已签到"""
    conn = get_connection()
    cur = conn.cursor()
    
    today = get_beijing_date()
    cur.execute(
        "SELECT id FROM checkin_records WHERE user_id = %s AND checkin_date = %s",
        (user_id, today)
    )
    result = cur.fetchone()
    
    cur.close()
    conn.close()
    
    return result is not None

def is_first_checkin(user_id: int) -> bool:
    """检查是否是用户的第一次签到"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT id FROM checkin_records WHERE user_id = %s LIMIT 1", (user_id,))
    result = cur.fetchone()
    
    cur.close()
    conn.close()
    
    return result is None

def do_checkin(user_id: int) -> tuple:
    """执行签到，返回 (是否成功, 获得积分, 是否首次签到)"""
    if check_user_checkin_today(user_id):
        return False, 0, False
    
    first_checkin = is_first_checkin(user_id)
    
    if first_checkin:
        points = 10
    else:
        points = random.randint(3, 8)
    
    conn = get_connection()
    cur = conn.cursor()
    
    today = get_beijing_date()
    cur.execute("""
        INSERT INTO checkin_records (user_id, checkin_date, points_earned, is_first_checkin)
        VALUES (%s, %s, %s, %s)
    """, (user_id, today, points, first_checkin))
    
    conn.commit()
    cur.close()
    conn.close()
    
    add_user_points(user_id, points)
    
    return True, points, first_checkin

def get_checkin_stats(user_id: int) -> dict:
    """获取用户签到统计"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT COUNT(*) as total_days, COALESCE(SUM(points_earned), 0) as total_points
        FROM checkin_records WHERE user_id = %s
    """, (user_id,))
    result = cur.fetchone()
    
    cur.close()
    conn.close()
    
    return {
        'total_days': result['total_days'],
        'total_points': result['total_points']
    }
    # -------------------- 广告观看操作 --------------------

def get_ad_watch_count_today(user_id: int) -> int:
    """获取用户今天观看广告次数"""
    conn = get_connection()
    cur = conn.cursor()
    
    today = get_beijing_date()
    cur.execute(
        "SELECT watch_count FROM ad_watch_records WHERE user_id = %s AND watch_date = %s",
        (user_id, today)
    )
    result = cur.fetchone()
    
    cur.close()
    conn.close()
    
    return result['watch_count'] if result else 0

def record_ad_watch(user_id: int) -> tuple:
    """记录广告观看，返回 (是否成功, 获得积分, 今日观看次数)"""
    current_count = get_ad_watch_count_today(user_id)
    
    if current_count >= 3:
        return False, 0, current_count
    
    if current_count == 0:
        points = 10
    elif current_count == 1:
        points = 6
    else:
        points = random.randint(3, 10)
    
    conn = get_connection()
    cur = conn.cursor()
    
    today = get_beijing_date()
    
    cur.execute("""
        INSERT INTO ad_watch_records (user_id, watch_date, watch_count, points_earned, updated_at)
        VALUES (%s, %s, 1, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (user_id, watch_date)
        DO UPDATE SET 
            watch_count = ad_watch_records.watch_count + 1,
            points_earned = ad_watch_records.points_earned + %s,
            updated_at = CURRENT_TIMESTAMP
        RETURNING watch_count
    """, (user_id, today, points, points))
    
    result = cur.fetchone()
    new_count = result['watch_count']
    
    conn.commit()
    cur.close()
    conn.close()
    
    add_user_points(user_id, points)
    
    return True, points, new_count

# -------------------- 防作弊令牌操作 --------------------

def generate_ad_token(user_id: int, ip_address: str = None, user_agent: str = None) -> str:
    """生成广告观看验证令牌"""
    current_count = get_ad_watch_count_today(user_id)
    if current_count >= 3:
        return None
    
    token_data = f"{user_id}:{time.time()}:{secrets.token_hex(16)}"
    token = hashlib.sha256(token_data.encode()).hexdigest()
    
    conn = get_connection()
    cur = conn.cursor()
    
    expires_at = datetime.now() + timedelta(minutes=5)
    
    cur.execute("""
        INSERT INTO ad_tokens (token, user_id, expires_at, ip_address, user_agent)
        VALUES (%s, %s, %s, %s, %s)
    """, (token, user_id, expires_at, ip_address, user_agent))
    
    conn.commit()
    cur.close()
    conn.close()
    
    return token

def validate_and_use_token(token: str, ip_address: str = None) -> tuple:
    """验证并使用令牌，返回 (是否有效, user_id, 错误信息)"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT * FROM ad_tokens 
        WHERE token = %s AND used = FALSE AND expires_at > CURRENT_TIMESTAMP
    """, (token,))
    
    result = cur.fetchone()
    
    if not result:
        cur.close()
        conn.close()
        return False, None, "令牌无效或已过期"
    
    user_id = result['user_id']
    
    cur.execute("UPDATE ad_tokens SET used = TRUE WHERE token = %s", (token,))
    
    conn.commit()
    cur.close()
    conn.close()
    
    return True, user_id, None

def cleanup_expired_tokens():
    """清理过期令牌"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("DELETE FROM ad_tokens WHERE expires_at < CURRENT_TIMESTAMP OR used = TRUE")
    deleted = cur.rowcount
    
    conn.commit()
    cur.close()
    conn.close()
    
    return deleted
    # -------------------- 密钥系统操作 --------------------

def generate_random_key(length: int = 12) -> str:
    """生成随机密钥（大小写字母和数字）"""
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

def get_or_create_daily_keys(key_date: datetime.date = None) -> dict:
    """获取或创建当天的密钥"""
    if key_date is None:
        key_date = get_secret_key_date()
    
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM daily_secret_keys WHERE key_date = %s", (key_date,))
    result = cur.fetchone()
    
    if result:
        cur.close()
        conn.close()
        return dict(result)
    
    key1 = generate_random_key(12)
    key2 = generate_random_key(12)
    
    cur.execute("""
        INSERT INTO daily_secret_keys (key_date, key1, key2, link_updated)
        VALUES (%s, %s, %s, FALSE)
        RETURNING *
    """, (key_date, key1, key2))
    
    result = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    
    return dict(result)

def update_key_links(key_date: datetime.date, key1_link: str = None, key2_link: str = None) -> bool:
    """更新密钥链接"""
    conn = get_connection()
    cur = conn.cursor()
    
    if key1_link and key2_link:
        cur.execute("""
            UPDATE daily_secret_keys 
            SET key1_link = %s, key2_link = %s, link_updated = TRUE
            WHERE key_date = %s
        """, (key1_link, key2_link, key_date))
    elif key1_link:
        cur.execute("""
            UPDATE daily_secret_keys 
            SET key1_link = %s
            WHERE key_date = %s
        """, (key1_link, key_date))
    elif key2_link:
        cur.execute("""
            UPDATE daily_secret_keys 
            SET key2_link = %s, link_updated = TRUE
            WHERE key_date = %s
        """, (key2_link, key_date))
    
    updated = cur.rowcount > 0
    conn.commit()
    cur.close()
    conn.close()
    
    return updated

def check_key_links_updated(key_date: datetime.date = None) -> bool:
    """检查密钥链接是否已更新"""
    if key_date is None:
        key_date = get_secret_key_date()
    
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT link_updated, key1_link, key2_link 
        FROM daily_secret_keys 
        WHERE key_date = %s
    """, (key_date,))
    result = cur.fetchone()
    
    cur.close()
    conn.close()
    
    if not result:
        return False
    
    return result['link_updated'] and result['key1_link'] and result['key2_link']

def validate_secret_key(key: str) -> tuple:
    """验证密钥，返回 (是否有效, 密钥类型1或2, 积分)"""
    key_date = get_secret_key_date()
    keys = get_or_create_daily_keys(key_date)
    
    if key == keys['key1']:
        return True, 1, 8
    elif key == keys['key2']:
        return True, 2, 6
    
    return False, 0, 0

def check_user_claimed_key(user_id: int, key_type: int, claim_date: datetime.date = None) -> bool:
    """检查用户是否已领取过该密钥"""
    if claim_date is None:
        claim_date = get_secret_key_date()
    
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT id FROM secret_key_claims 
        WHERE user_id = %s AND claim_date = %s AND key_type = %s
    """, (user_id, claim_date, key_type))
    result = cur.fetchone()
    
    cur.close()
    conn.close()
    
    return result is not None

def claim_secret_key(user_id: int, key_type: int, points: int) -> bool:
    """领取密钥奖励"""
    claim_date = get_secret_key_date()
    
    if check_user_claimed_key(user_id, key_type, claim_date):
        return False
    
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            INSERT INTO secret_key_claims (user_id, claim_date, key_type, points_earned)
            VALUES (%s, %s, %s, %s)
        """, (user_id, claim_date, key_type, points))
        
        conn.commit()
        cur.close()
        conn.close()
        
        add_user_points(user_id, points)
        return True
    except Exception:
        conn.rollback()
        cur.close()
        conn.close()
        return False

def get_user_key_click_count(user_id: int) -> int:
    """获取用户今天的密钥点击次数（北京时间10点重置）"""
    key_date = get_secret_key_date()
    
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT click_count FROM secret_key_clicks 
        WHERE user_id = %s AND click_date = %s
    """, (user_id, key_date))
    result = cur.fetchone()
    
    cur.close()
    conn.close()
    
    return result['click_count'] if result else 0

def increment_key_click_count(user_id: int) -> int:
    """增加密钥点击次数，返回新的次数"""
    key_date = get_secret_key_date()
    
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        INSERT INTO secret_key_clicks (user_id, click_date, click_count, updated_at)
        VALUES (%s, %s, 1, CURRENT_TIMESTAMP)
        ON CONFLICT (user_id, click_date)
        DO UPDATE SET 
            click_count = secret_key_clicks.click_count + 1,
            updated_at = CURRENT_TIMESTAMP
        RETURNING click_count
    """, (user_id, key_date))
    
    result = cur.fetchone()
    new_count = result['click_count']
    
    conn.commit()
    cur.close()
    conn.close()
    
    return new_count

def get_user_claimed_keys_today(user_id: int) -> list:
    """获取用户今天已领取的密钥类型列表"""
    claim_date = get_secret_key_date()
    
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT key_type FROM secret_key_claims 
        WHERE user_id = %s AND claim_date = %s
    """, (user_id, claim_date))
    results = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return [r['key_type'] for r in results]
    # ==================== 辅助函数 ====================

def is_admin(user_id: int) -> bool:
    """检查是否为管理员"""
    return user_id == ADMIN_ID

def get_start_keyboard():
    """首页键盘"""
    keyboard = [
        [InlineKeyboardButton("✅ 开始验证", callback_data="start_verify")],
        [InlineKeyboardButton("💰 我的积分", callback_data="my_points")],
        [InlineKeyboardButton("🎉 开业活动", callback_data="activity_center")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard():
    """管理员后台主键盘"""
    keyboard = [
        [InlineKeyboardButton("📷 获取图片 File ID", callback_data="get_file_id")],
        [InlineKeyboardButton("📂 查看已保存的图片", callback_data="view_saved_files")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_points_keyboard():
    """积分页面键盘"""
    keyboard = [
        [InlineKeyboardButton("📅 每日签到", callback_data="daily_checkin")],
        [InlineKeyboardButton("🔙 返回首页", callback_data="back_to_start")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_activity_keyboard(user_id: int):
    """活动中心键盘"""
    watch_count = get_ad_watch_count_today(user_id)
    click_count = get_user_key_click_count(user_id)
    
    keyboard = [
        [InlineKeyboardButton(f"🎬 看视频得积分 ({watch_count}/3)", callback_data="watch_ad_info")],
        [InlineKeyboardButton(f"🔑 每日寻宝密钥 ({click_count}/2)", callback_data="secret_key_info")],
        [InlineKeyboardButton("🔙 返回首页", callback_data="back_to_start")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_next_reset_time_str() -> str:
    """获取下次重置时间字符串"""
    now = get_beijing_now()
    if now.hour >= 10:
        next_reset = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    else:
        next_reset = now.replace(hour=10, minute=0, second=0, microsecond=0)
    
    diff = next_reset - now
    hours, remainder = divmod(int(diff.total_seconds()), 3600)
    minutes, _ = divmod(remainder, 60)
    
    return f"{hours}小时{minutes}分钟"
    # ==================== 命令处理器 ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令"""
    user = update.effective_user
    init_user_points(user.id)
    
    await update.message.reply_text(
        f"👋 欢迎 {user.first_name}！\n\n"
        "🤖 这是一个多功能机器人\n\n"
        "请选择功能：",
        reply_markup=get_start_keyboard(),
        parse_mode="Markdown"
    )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /admin 命令"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("⛔ 无权限访问管理后台")
        return
    
    await update.message.reply_text(
        "🔐 **管理员后台**\n\n请选择功能：",
        reply_markup=get_admin_keyboard(),
        parse_mode="Markdown"
    )

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /id 命令 - 快捷获取 File ID"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("⛔ 无权限使用此功能")
        return
    
    context.user_data['state'] = WAITING_FOR_PHOTO
    
    keyboard = [[InlineKeyboardButton("❌ 取消", callback_data="cancel_upload")]]
    
    await update.message.reply_text(
        "📷 **获取 File ID**\n\n请发送一张图片，我会返回它的 File ID",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def jf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /jf 命令 - 积分页面"""
    user_id = update.effective_user.id
    points = get_user_points(user_id)
    stats = get_checkin_stats(user_id)
    checked_today = check_user_checkin_today(user_id)
    
    status = "✅ 今日已签到" if checked_today else "❌ 今日未签到"
    
    await update.message.reply_text(
        f"💰 **我的积分**\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💎 当前积分：**{points}**\n"
        f"📅 累计签到：**{stats['total_days']}** 天\n"
        f"🎁 签到获得：**{stats['total_points']}** 积分\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"📌 签到状态：{status}",
        reply_markup=get_points_keyboard(),
        parse_mode="Markdown"
    )

async def hd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /hd 命令 - 活动中心"""
    user_id = update.effective_user.id
    
    await update.message.reply_text(
        "🎉 **活动中心**\n\n"
        "━━━━━━━━━━━━━━━\n"
        "🎊 开业大酬宾！\n"
        "参与活动赢取丰厚积分奖励！\n"
        "━━━━━━━━━━━━━━━\n\n"
        "请选择活动：",
        reply_markup=get_activity_keyboard(user_id),
        parse_mode="Markdown"
    )

async def my_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /my 命令 - 管理员查看/更换密钥"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("⛔ 无权限使用此功能")
        return
    
    now = get_beijing_now()
    key_date = get_secret_key_date()
    keys = get_or_create_daily_keys(key_date)
    
    links_updated = check_key_links_updated(key_date)
    
    link_status = "✅ 已更新" if links_updated else "❌ 未更新"
    
    message = (
        f"🔑 **今日密钥管理**\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📅 密钥日期：{key_date}\n"
        f"⏰ 当前时间：{now.strftime('%H:%M:%S')}\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"🔐 **密钥1** (8积分)：\n`{keys['key1']}`\n\n"
        f"🔐 **密钥2** (6积分)：\n`{keys['key2']}`\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🔗 链接状态：{link_status}\n"
    )
    
    if keys['key1_link']:
        message += f"📎 密钥1链接：{keys['key1_link'][:30]}...\n"
    if keys['key2_link']:
        message += f"📎 密钥2链接：{keys['key2_link'][:30]}...\n"
    
    keyboard = [
        [InlineKeyboardButton("🔄 更换密钥链接", callback_data="update_key_links")],
    ]
    
    await update.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    # ==================== 回调处理器 ====================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理按钮回调"""
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    
    await query.answer()
    
    # ==================== 首页相关 ====================
    
    if data == "back_to_start":
        user = query.from_user
        context.user_data.pop('state', None)
        try:
            if query.message.photo:
                await query.message.delete()
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"👋 欢迎 {user.first_name}！\n\n"
                         "🤖 这是一个多功能机器人\n\n"
                         "请选择功能：",
                    reply_markup=get_start_keyboard(),
                    parse_mode="Markdown"
                )
            else:
                await query.edit_message_text(
                    f"👋 欢迎 {user.first_name}！\n\n"
                    "🤖 这是一个多功能机器人\n\n"
                    "请选择功能：",
                    reply_markup=get_start_keyboard(),
                    parse_mode="Markdown"
                )
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"👋 欢迎 {user.first_name}！\n\n"
                     "🤖 这是一个多功能机器人\n\n"
                     "请选择功能：",
                reply_markup=get_start_keyboard(),
                parse_mode="Markdown"
            )
    
    elif data == "start_verify":
        await query.edit_message_text(
            "✅ **验证功能**\n\n"
            "🔄 验证进行中...\n\n"
            "请按照提示完成验证。",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 返回首页", callback_data="back_to_start")]
            ]),
            parse_mode="Markdown"
        )
    
    # ==================== 积分相关 ====================
    
    elif data == "my_points":
        points = get_user_points(user_id)
        stats = get_checkin_stats(user_id)
        checked_today = check_user_checkin_today(user_id)
        
        status = "✅ 今日已签到" if checked_today else "❌ 今日未签到"
        
        await query.edit_message_text(
            f"💰 **我的积分**\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💎 当前积分：**{points}**\n"
            f"📅 累计签到：**{stats['total_days']}** 天\n"
            f"🎁 签到获得：**{stats['total_points']}** 积分\n"
            f"━━━━━━━━━━━━━━━\n\n"
            f"📌 签到状态：{status}",
            reply_markup=get_points_keyboard(),
            parse_mode="Markdown"
        )
    
    elif data == "daily_checkin":
        success, points_earned, is_first = do_checkin(user_id)
        
        if success:
            total_points = get_user_points(user_id)
            if is_first:
                message = (
                    f"🎉 **首次签到成功！**\n\n"
                    f"🎁 获得首签奖励：**+{points_earned}** 积分\n"
                    f"💎 当前积分：**{total_points}**\n\n"
                    f"✨ 欢迎加入，每天记得来签到哦！"
                )
            else:
                message = (
                    f"✅ **签到成功！**\n\n"
                    f"🎁 获得积分：**+{points_earned}**\n"
                    f"💎 当前积分：**{total_points}**\n\n"
                    f"📅 明天继续签到可获得 3-8 积分"
                )
        else:
            total_points = get_user_points(user_id)
            message = (
                f"⚠️ **今日已签到**\n\n"
                f"💎 当前积分：**{total_points}**\n\n"
                f"⏰ 明天再来签到吧！\n"
                f"🕐 每日 00:00 (北京时间) 重置"
            )
        
        keyboard = [
            [InlineKeyboardButton("💰 返回积分", callback_data="my_points")],
            [InlineKeyboardButton("🔙 返回首页", callback_data="back_to_start")],
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
            # ==================== 活动中心相关 ====================
    
    elif data == "activity_center":
        await query.edit_message_text(
            "🎉 **活动中心**\n\n"
            "━━━━━━━━━━━━━━━\n"
            "🎊 开业大酬宾！\n"
            "参与活动赢取丰厚积分奖励！\n"
            "━━━━━━━━━━━━━━━\n\n"
            "请选择活动：",
            reply_markup=get_activity_keyboard(user_id),
            parse_mode="Markdown"
        )
    
    # ==================== 看视频得积分 ====================
    
    elif data == "watch_ad_info":
        watch_count = get_ad_watch_count_today(user_id)
        remaining = 3 - watch_count
        
        if remaining <= 0:
            await query.edit_message_text(
                "🎬 **看视频得积分**\n\n"
                "━━━━━━━━━━━━━━━\n"
                "❌ 今日观看次数已用完\n\n"
                "⏰ 每日 00:00 (北京时间) 重置\n"
                "━━━━━━━━━━━━━━━",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 返回活动中心", callback_data="activity_center")]
                ]),
                parse_mode="Markdown"
            )
            return
        
        if watch_count == 0:
            next_points = "10"
        elif watch_count == 1:
            next_points = "6"
        else:
            next_points = "3-10 (随机)"
        
        await query.edit_message_text(
            "🎬 **看视频得积分**\n\n"
            "━━━━━━━━━━━━━━━\n"
            "📺 观看完整视频广告即可获得积分奖励！\n\n"
            "🎁 **积分规则：**\n"
            "• 第1次观看：+10 积分\n"
            "• 第2次观看：+6 积分\n"
            "• 第3次观看：+3~10 积分（随机）\n\n"
            f"📊 今日已观看：{watch_count}/3 次\n"
            f"🎯 下次可得：{next_points} 积分\n"
            "━━━━━━━━━━━━━━━\n\n"
            "⚠️ 请完整观看视频，中途退出无法获得积分",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("▶️ 开始观看", callback_data="start_watch_ad")],
                [InlineKeyboardButton("🔙 返回活动中心", callback_data="activity_center")]
            ]),
            parse_mode="Markdown"
        )
    
    elif data == "start_watch_ad":
        watch_count = get_ad_watch_count_today(user_id)
        
        if watch_count >= 3:
            await query.answer("今日观看次数已用完", show_alert=True)
            return
        
        token = generate_ad_token(user_id)
        
        if not token:
            await query.answer("生成验证失败，请稍后重试", show_alert=True)
            return
        
        ad_url = f"{GITHUB_PAGES_URL}/ad.html?token={token}&user_id={user_id}"
        
        await query.edit_message_text(
            "🎬 **准备观看广告**\n\n"
            "━━━━━━━━━━━━━━━\n"
            "📱 点击下方按钮打开广告页面\n"
            "✅ 完整观看后自动领取积分\n"
            "━━━━━━━━━━━━━━━\n\n"
            "⚠️ **注意事项：**\n"
            "• 请完整观看视频\n"
            "• 中途退出无法获得积分\n"
            "• 链接5分钟内有效",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📺 打开广告页面", url=ad_url)],
                [InlineKeyboardButton("✅ 我已观看完成", callback_data="check_ad_reward")],
                [InlineKeyboardButton("🔙 返回", callback_data="watch_ad_info")]
            ]),
            parse_mode="Markdown"
        )
        
        context.user_data['pending_ad_token'] = token
    
    elif data == "check_ad_reward":
        pending_token = context.user_data.get('pending_ad_token')
        
        if not pending_token:
            await query.answer("请先点击观看广告", show_alert=True)
            return
        
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT used FROM ad_tokens WHERE token = %s", (pending_token,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        
        if not result:
            await query.answer("验证令牌已过期，请重新观看", show_alert=True)
            context.user_data.pop('pending_ad_token', None)
            return
        
        if not result['used']:
            await query.answer("广告未观看完成，请完整观看后再领取", show_alert=True)
            return
        
        context.user_data.pop('pending_ad_token', None)
        
        points = get_user_points(user_id)
        watch_count = get_ad_watch_count_today(user_id)
        
        await query.edit_message_text(
            "🎉 **领取成功！**\n\n"
            f"💎 当前积分：**{points}**\n"
            f"📊 今日已观看：{watch_count}/3 次",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎬 继续观看", callback_data="watch_ad_info")],
                [InlineKeyboardButton("🔙 返回活动中心", callback_data="activity_center")]
            ]),
            parse_mode="Markdown"
        )
            # ==================== 每日寻宝密钥 ====================
    
    elif data == "secret_key_info":
        click_count = get_user_key_click_count(user_id)
        claimed_keys = get_user_claimed_keys_today(user_id)
        links_updated = check_key_links_updated()
        next_reset = get_next_reset_time_str()
        
        if click_count >= 2:
            await query.edit_message_text(
                "🔑 **每日寻宝密钥**\n\n"
                "━━━━━━━━━━━━━━━\n"
                "❌ 今日获取次数已用完\n\n"
                f"⏰ 下次重置：{next_reset}后\n"
                "🕐 每日 10:00 (北京时间) 重置\n"
                "━━━━━━━━━━━━━━━",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 返回活动中心", callback_data="activity_center")]
                ]),
                parse_mode="Markdown"
            )
            return
        
        key1_status = "✅ 已领取" if 1 in claimed_keys else "❌ 未领取"
        key2_status = "✅ 已领取" if 2 in claimed_keys else "❌ 未领取"
        
        if click_count == 0:
            next_points = "8"
        else:
            next_points = "6"
        
        await query.edit_message_text(
            "🔑 **每日寻宝密钥**\n\n"
            "━━━━━━━━━━━━━━━\n"
            "🎯 **活动说明：**\n\n"
            "📱 通过夸克网盘获取神秘密钥\n"
            "🔄 点击按钮后需等待 3 秒跳转\n"
            "📝 看到文件名后，保存到网盘\n"
            "✏️ 重命名查看文本内容\n"
            "📤 复制密钥发送给机器人\n\n"
            "━━━━━━━━━━━━━━━\n"
            "🎁 **积分规则：**\n"
            "• 第1次密钥：+8 积分\n"
            "• 第2次密钥：+6 积分\n\n"
            f"📊 今日已获取：{click_count}/2 次\n"
            f"🎯 下次可得：{next_points} 积分\n\n"
            f"🔐 密钥1：{key1_status}\n"
            f"🔐 密钥2：{key2_status}\n"
            "━━━━━━━━━━━━━━━\n\n"
            f"⏰ 下次重置：{next_reset}后",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 开始获取密钥", callback_data="get_secret_key")],
                [InlineKeyboardButton("📝 输入密钥", callback_data="input_secret_key")],
                [InlineKeyboardButton("🔙 返回活动中心", callback_data="activity_center")]
            ]),
            parse_mode="Markdown"
        )
    
    elif data == "get_secret_key":
        click_count = get_user_key_click_count(user_id)
        
        if click_count >= 2:
            next_reset = get_next_reset_time_str()
            await query.answer(f"今日次数已用完，{next_reset}后重置", show_alert=True)
            return
        
        links_updated = check_key_links_updated()
        
        if not links_updated:
            await query.edit_message_text(
                "⏳ **请稍候**\n\n"
                "━━━━━━━━━━━━━━━\n"
                "🔄 管理员正在更换新密钥链接\n"
                "请稍后再试...\n"
                "━━━━━━━━━━━━━━━",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 刷新", callback_data="get_secret_key")],
                    [InlineKeyboardButton("🔙 返回", callback_data="secret_key_info")]
                ]),
                parse_mode="Markdown"
            )
            return
        
        new_count = increment_key_click_count(user_id)
        
        key_date = get_secret_key_date()
        keys = get_or_create_daily_keys(key_date)
        
        if new_count == 1:
            monetag_link = MONETAG_LINK_1
            key_link = keys['key1_link']
            key_num = "1"
            points_hint = "8"
        else:
            monetag_link = MONETAG_LINK_2
            key_link = keys['key2_link']
            key_num = "2"
            points_hint = "6"
        
        redirect_url = f"{GITHUB_PAGES_URL}/redirect.html?monetag={monetag_link}&target={key_link}&user_id={user_id}"
        
        await query.edit_message_text(
            f"🔑 **获取密钥 {key_num}**\n\n"
            "━━━━━━━━━━━━━━━\n"
            f"🎁 本次可获得：**{points_hint}** 积分\n\n"
            "📋 **操作步骤：**\n"
            "1️⃣ 点击下方按钮\n"
            "2️⃣ 等待 3 秒自动跳转\n"
            "3️⃣ 保存网盘文件\n"
            "4️⃣ 重命名查看密钥\n"
            "5️⃣ 返回输入密钥领取积分\n"
            "━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 前往获取", url=redirect_url)],
                [InlineKeyboardButton("📝 输入密钥", callback_data="input_secret_key")],
                [InlineKeyboardButton("🔙 返回", callback_data="secret_key_info")]
            ]),
            parse_mode="Markdown"
        )
    
    elif data == "input_secret_key":
        context.user_data['state'] = WAITING_FOR_SECRET_KEY
        
        await query.edit_message_text(
            "📝 **输入密钥**\n\n"
            "━━━━━━━━━━━━━━━\n"
            "请发送您获取到的密钥\n\n"
            "💡 密钥格式：12位字母数字组合\n"
            "━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ 取消", callback_data="secret_key_info")]
            ]),
            parse_mode="Markdown"
        )
    
        # ==================== 管理员密钥链接更新 ====================
    
    elif data == "update_key_links":
        if not is_admin(user_id):
            await query.answer("⛔ 无权限", show_alert=True)
            return
        
        now = get_beijing_now()
        
        if now.hour < 10:
            await query.answer(f"请在 10:00 后更换密钥链接（当前 {now.strftime('%H:%M')}）", show_alert=True)
            return
        
        context.user_data['state'] = WAITING_FOR_KEY1_LINK
        
        await query.edit_message_text(
            "🔗 **更换密钥链接**\n\n"
            "━━━━━━━━━━━━━━━\n"
            "📎 请发送 **密钥1** 的网盘链接\n\n"
            "💡 这是用户第1次点击后跳转的链接\n"
            "━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ 取消", callback_data="cancel_key_update")]
            ]),
            parse_mode="Markdown"
        )
    
    elif data == "cancel_key_update":
        context.user_data.pop('state', None)
        await query.edit_message_text(
            "❌ 已取消更换密钥链接",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 返回", callback_data="back_to_start")]
            ]),
            parse_mode="Markdown"
        )
    
    # ==================== 管理员后台 - File ID ====================
    
    elif data == "get_file_id":
        if not is_admin(user_id):
            await query.answer("⛔ 无权限", show_alert=True)
            return
        
        context.user_data['state'] = WAITING_FOR_PHOTO
        keyboard = [[InlineKeyboardButton("❌ 取消", callback_data="cancel_upload")]]
        
        await query.edit_message_text(
            "📷 **获取 File ID**\n\n请发送一张图片，我会返回它的 File ID 并保存到数据库",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    
    elif data == "cancel_upload":
        context.user_data.pop('state', None)
        await query.edit_message_text(
            "🔐 **管理员后台**\n\n请选择功能：",
            reply_markup=get_admin_keyboard(),
            parse_mode="Markdown"
        )
    
    elif data == "view_saved_files":
        if not is_admin(user_id):
            await query.answer("⛔ 无权限", show_alert=True)
            return
        
        files = get_all_file_ids()
        
        if not files:
            keyboard = [[InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")]]
            await query.edit_message_text(
                "📂 **已保存的图片**\n\n暂无保存的图片",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            return
        
        keyboard = []
        for f in files[:10]:
            label = f"🖼 #{f['id']} - {f['created_at'].strftime('%m/%d %H:%M')}"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"view_file_{f['id']}")])
        
        keyboard.append([InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")])
        
        await query.edit_message_text(
            "📂 **已保存的图片**\n\n点击查看详情：",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    
    elif data.startswith("view_file_"):
        if not is_admin(user_id):
            await query.answer("⛔ 无权限", show_alert=True)
            return
        
        record_id = int(data.replace("view_file_", ""))
        file_record = get_file_by_id(record_id)
        
        if not file_record:
            await query.answer("❌ 记录不存在", show_alert=True)
            return
        
        keyboard = [
            [InlineKeyboardButton("🗑 删除此记录", callback_data=f"confirm_delete_{record_id}")],
            [InlineKeyboardButton("🔙 返回列表", callback_data="view_saved_files")],
        ]
        
        text = (
            f"🖼 **图片详情 #{record_id}**\n\n"
            f"📋 **File ID:**\n`{file_record['file_id']}`\n\n"
            f"📅 **保存时间:** {file_record['created_at'].strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        try:
            await query.message.delete()
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=file_record['file_id'],
                caption=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        except Exception:
            await query.edit_message_text(
                text + "\n\n⚠️ 图片预览失败",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
    
    elif data.startswith("confirm_delete_"):
        if not is_admin(user_id):
            await query.answer("⛔ 无权限", show_alert=True)
            return
        
        record_id = int(data.replace("confirm_delete_", ""))
        
        keyboard = [
            [
                InlineKeyboardButton("✅ 确认删除", callback_data=f"delete_{record_id}"),
                InlineKeyboardButton("❌ 取消", callback_data=f"view_file_{record_id}")
            ],
        ]
        
        try:
            await query.message.edit_caption(
                caption=f"⚠️ **确认删除 #{record_id}?**\n\n此操作不可撤销！",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        except Exception:
            await query.edit_message_text(
                f"⚠️ **确认删除 #{record_id}?**\n\n此操作不可撤销！",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
    
    elif data.startswith("delete_") and not data.startswith("delete_confirm"):
        if not is_admin(user_id):
            await query.answer("⛔ 无权限", show_alert=True)
            return
        
        record_id = int(data.replace("delete_", ""))
        
        if delete_file_id(record_id):
            await query.message.delete()
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="✅ 删除成功！\n\n🔐 **管理员后台**\n\n请选择功能：",
                reply_markup=get_admin_keyboard(),
                parse_mode="Markdown"
            )
        else:
            await query.answer("❌ 删除失败", show_alert=True)
    
    elif data == "back_to_admin":
        if not is_admin(user_id):
            await query.answer("⛔ 无权限", show_alert=True)
            return
        
        try:
            if query.message.photo:
                await query.message.delete()
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="🔐 **管理员后台**\n\n请选择功能：",
                    reply_markup=get_admin_keyboard(),
                    parse_mode="Markdown"
                )
            else:
                await query.edit_message_text(
                    "🔐 **管理员后台**\n\n请选择功能：",
                    reply_markup=get_admin_keyboard(),
                    parse_mode="Markdown"
                )
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="🔐 **管理员后台**\n\n请选择功能：",
                reply_markup=get_admin_keyboard(),
                parse_mode="Markdown"
            )
            # ==================== 消息处理器 ====================

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理文本消息"""
    user_id = update.effective_user.id
    text = update.message.text.strip()
    state = context.user_data.get('state')
    
    # 处理密钥输入
    if state == WAITING_FOR_SECRET_KEY:
        context.user_data.pop('state', None)
        
        is_valid, key_type, points = validate_secret_key(text)
        
        if not is_valid:
            await update.message.reply_text(
                "❌ **密钥无效**\n\n"
                "请检查密钥是否正确，或密钥可能已过期\n\n"
                "💡 密钥每日 10:00 (北京时间) 更新",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔑 返回密钥页面", callback_data="secret_key_info")],
                    [InlineKeyboardButton("🔙 返回活动中心", callback_data="activity_center")]
                ]),
                parse_mode="Markdown"
            )
            return
        
        if check_user_claimed_key(user_id, key_type):
            await update.message.reply_text(
                f"⚠️ **重复领取**\n\n"
                f"您已经领取过密钥{key_type}的奖励了\n\n"
                "💡 每个密钥每天只能领取一次",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔑 返回密钥页面", callback_data="secret_key_info")],
                    [InlineKeyboardButton("🔙 返回活动中心", callback_data="activity_center")]
                ]),
                parse_mode="Markdown"
            )
            return
        
        success = claim_secret_key(user_id, key_type, points)
        
        if success:
            total_points = get_user_points(user_id)
            claimed_keys = get_user_claimed_keys_today(user_id)
            
            await update.message.reply_text(
                f"🎉 **恭喜领取成功！**\n\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🔐 密钥类型：密钥{key_type}\n"
                f"🎁 获得积分：**+{points}**\n"
                f"💎 当前积分：**{total_points}**\n"
                f"━━━━━━━━━━━━━━━\n\n"
                f"✅ 已领取：{len(claimed_keys)}/2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔑 继续获取密钥", callback_data="secret_key_info")],
                    [InlineKeyboardButton("🔙 返回活动中心", callback_data="activity_center")]
                ]),
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "❌ **领取失败**\n\n请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 返回活动中心", callback_data="activity_center")]
                ]),
                parse_mode="Markdown"
            )
        return
    
    # 管理员更新密钥1链接
    if state == WAITING_FOR_KEY1_LINK and is_admin(user_id):
        context.user_data['key1_link'] = text
        context.user_data['state'] = WAITING_FOR_KEY2_LINK
        
        await update.message.reply_text(
            "✅ **密钥1链接已保存**\n\n"
            f"🔗 链接：{text[:50]}...\n\n"
            "━━━━━━━━━━━━━━━\n"
            "📎 请发送 **密钥2** 的网盘链接\n\n"
            "💡 这是用户第2次点击后跳转的链接\n"
            "━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ 取消", callback_data="cancel_key_update")]
            ]),
            parse_mode="Markdown"
        )
        return
    
    # 管理员更新密钥2链接
    if state == WAITING_FOR_KEY2_LINK and is_admin(user_id):
        key1_link = context.user_data.get('key1_link')
        key2_link = text
        
        context.user_data.pop('state', None)
        context.user_data.pop('key1_link', None)
        
        key_date = get_secret_key_date()
        get_or_create_daily_keys(key_date)
        update_key_links(key_date, key1_link, key2_link)
        
        keys = get_or_create_daily_keys(key_date)
        
        await update.message.reply_text(
            "✅ **密钥链接更新完成！**\n\n"
            "━━━━━━━━━━━━━━━\n"
            f"📅 生效日期：{key_date}\n\n"
            f"🔐 密钥1：`{keys['key1']}`\n"
            f"🔗 链接1：{key1_link[:40]}...\n\n"
            f"🔐 密钥2：`{keys['key2']}`\n"
            f"🔗 链接2：{key2_link[:40]}...\n"
            "━━━━━━━━━━━━━━━\n\n"
            "✨ 用户现在可以正常获取密钥了",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 返回首页", callback_data="back_to_start")]
            ]),
            parse_mode="Markdown"
        )
        return
    
    # 检查是否是密钥（任何时候发送密钥都可以尝试领取）
    if len(text) == 12 and text.isalnum():
        is_valid, key_type, points = validate_secret_key(text)
        
        if is_valid:
            if check_user_claimed_key(user_id, key_type):
                await update.message.reply_text(
                    f"⚠️ 您已经领取过密钥{key_type}的奖励了",
                    parse_mode="Markdown"
                )
                return
            
            success = claim_secret_key(user_id, key_type, points)
            
            if success:
                total_points = get_user_points(user_id)
                await update.message.reply_text(
                    f"🎉 **恭喜！密钥{key_type}验证成功！**\n\n"
                    f"🎁 获得积分：**+{points}**\n"
                    f"💎 当前积分：**{total_points}**",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 返回活动中心", callback_data="activity_center")]
                    ]),
                    parse_mode="Markdown"
                )
            return

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理图片消息"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        return
    
    if context.user_data.get('state') != WAITING_FOR_PHOTO:
        return
    
    context.user_data.pop('state', None)
    
    photo = update.message.photo[-1]
    file_id = photo.file_id
    
    record_id = save_file_id(file_id, "photo")
    
    keyboard = [[InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")]]
    
    await update.message.reply_text(
        f"✅ **已保存！**\n\n"
        f"📋 **记录 ID:** #{record_id}\n\n"
        f"🖼 **File ID:**\n`{file_id}`",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
   # ==================== 定时任务 ====================

async def daily_key_rotation():
    """每日密钥轮换任务 - 北京时间10:00执行"""
    global telegram_app
    
    logger.info("🔄 开始每日密钥轮换...")
    
    key_date = get_secret_key_date()
    keys = get_or_create_daily_keys(key_date)
    
    logger.info(f"📅 新密钥日期：{key_date}")
    logger.info(f"🔐 密钥1：{keys['key1']}")
    logger.info(f"🔐 密钥2：{keys['key2']}")
    
    # 发送给管理员
    if telegram_app and ADMIN_ID:
        try:
            message = (
                "🔔 **每日密钥已更新**\n\n"
                "━━━━━━━━━━━━━━━\n"
                f"📅 日期：{key_date}\n"
                f"⏰ 时间：{get_beijing_now().strftime('%H:%M:%S')}\n"
                "━━━━━━━━━━━━━━━\n\n"
                f"🔐 **密钥1** (8积分)：\n`{keys['key1']}`\n\n"
                f"🔐 **密钥2** (6积分)：\n`{keys['key2']}`\n\n"
                "━━━━━━━━━━━━━━━\n"
                "⚠️ 请及时使用 /my 命令更新密钥链接\n"
                "用户需要等待您更新链接后才能获取密钥"
            )
            
            await telegram_app.bot.send_message(
                chat_id=ADMIN_ID,
                text=message,
                parse_mode="Markdown"
            )
            logger.info(f"✅ 已发送新密钥给管理员 {ADMIN_ID}")
        except Exception as e:
            logger.error(f"❌ 发送密钥给管理员失败: {e}")

async def cleanup_old_data():
    """清理过期数据"""
    try:
        deleted_tokens = cleanup_expired_tokens()
        logger.info(f"🧹 清理了 {deleted_tokens} 个过期令牌")
    except Exception as e:
        logger.error(f"❌ 清理过期数据失败: {e}")

def setup_scheduler():
    """设置定时任务调度器"""
    global scheduler
    
    scheduler = AsyncIOScheduler(timezone=BEIJING_TZ)
    
    # 每天北京时间 10:00 执行密钥轮换
    scheduler.add_job(
        daily_key_rotation,
        CronTrigger(hour=10, minute=0, second=0, timezone=BEIJING_TZ),
        id='daily_key_rotation',
        name='每日密钥轮换',
        replace_existing=True
    )
    
    # 每小时清理过期数据
    scheduler.add_job(
        cleanup_old_data,
        CronTrigger(minute=30, timezone=BEIJING_TZ),
        id='cleanup_old_data',
        name='清理过期数据',
        replace_existing=True
    )
    
    scheduler.start()
    logger.info("⏰ 定时任务调度器已启动")
    logger.info("   - 每日密钥轮换：北京时间 10:00")
    logger.info("   - 清理过期数据：每小时30分")
    # ==================== FastAPI 后端 ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命周期管理"""
    # 启动时初始化数据库
    init_database()
    yield
    # 关闭时的清理工作（如需要）
    pass

api = FastAPI(title="Telegram Bot API", lifespan=lifespan)

# CORS 配置 - 允许跨域请求
api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@api.get("/")
async def root():
    """健康检查接口"""
    return {
        "status": "ok",
        "message": "Telegram Bot API is running",
        "time": get_beijing_now().isoformat()
    }

@api.get("/health")
async def health_check():
    """健康检查接口"""
    return {
        "status": "healthy",
        "database": "connected",
        "time": get_beijing_now().isoformat()
    }

@api.post("/api/ad/verify")
async def verify_ad_watch(request: Request):
    """验证广告观看并发放积分"""
    try:
        data = await request.json()
        token = data.get("token")
        user_id = data.get("user_id")
        
        if not token or not user_id:
            raise HTTPException(status_code=400, detail="缺少必要参数")
        
        # 获取客户端IP
        client_ip = request.client.host
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
        
        # 验证令牌
        is_valid, token_user_id, error = validate_and_use_token(token, client_ip)
        
        if not is_valid:
            raise HTTPException(status_code=400, detail=error)
        
        if int(user_id) != token_user_id:
            raise HTTPException(status_code=400, detail="用户ID不匹配")
        
        # 记录广告观看并发放积分
        success, points, watch_count = record_ad_watch(token_user_id)
        
        if not success:
            raise HTTPException(status_code=400, detail="今日观看次数已达上限")
        
        return {
            "success": True,
            "points_earned": points,
            "total_points": get_user_points(token_user_id),
            "watch_count": watch_count,
            "remaining": 3 - watch_count
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"验证广告观看失败: {e}")
        raise HTTPException(status_code=500, detail="服务器内部错误")

@api.get("/api/ad/status/{user_id}")
async def get_ad_status(user_id: int):
    """获取用户广告观看状态"""
    try:
        watch_count = get_ad_watch_count_today(user_id)
        points = get_user_points(user_id)
        
        return {
            "success": True,
            "user_id": user_id,
            "watch_count": watch_count,
            "remaining": 3 - watch_count,
            "total_points": points
        }
    except Exception as e:
        logger.error(f"获取广告状态失败: {e}")
        raise HTTPException(status_code=500, detail="服务器内部错误")

@api.post("/api/token/generate")
async def generate_token_api(request: Request):
    """生成广告验证令牌"""
    try:
        data = await request.json()
        user_id = data.get("user_id")
        
        if not user_id:
            raise HTTPException(status_code=400, detail="缺少用户ID")
        
        client_ip = request.client.host
        user_agent = request.headers.get("User-Agent", "")
        
        token = generate_ad_token(int(user_id), client_ip, user_agent)
        
        if not token:
            raise HTTPException(status_code=400, detail="今日观看次数已达上限")
        
        return {
            "success": True,
            "token": token,
            "expires_in": 300
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"生成令牌失败: {e}")
        raise HTTPException(status_code=500, detail="服务器内部错误")

@api.get("/api/key/status/{user_id}")
async def get_key_status(user_id: int):
    """获取用户密钥状态"""
    try:
        click_count = get_user_key_click_count(user_id)
        claimed_keys = get_user_claimed_keys_today(user_id)
        links_updated = check_key_links_updated()
        
        return {
            "success": True,
            "user_id": user_id,
            "click_count": click_count,
            "remaining": 2 - click_count,
            "claimed_keys": claimed_keys,
            "links_updated": links_updated
        }
    except Exception as e:
        logger.error(f"获取密钥状态失败: {e}")
        raise HTTPException(status_code=500, detail="服务器内部错误")

@api.post("/api/key/verify")
async def verify_secret_key(request: Request):
    """验证密钥并发放积分"""
    try:
        data = await request.json()
        user_id = data.get("user_id")
        key = data.get("key")
        
        if not user_id or not key:
            raise HTTPException(status_code=400, detail="缺少必要参数")
        
        user_id = int(user_id)
        
        # 验证密钥
        is_valid, key_type, points = validate_secret_key(key)
        
        if not is_valid:
            raise HTTPException(status_code=400, detail="密钥无效")
        
        # 检查是否已领取
        if check_user_claimed_key(user_id, key_type):
            raise HTTPException(status_code=400, detail="已领取过该密钥奖励")
        
        # 领取奖励
        success = claim_secret_key(user_id, key_type, points)
        
        if not success:
            raise HTTPException(status_code=400, detail="领取失败")
        
        return {
            "success": True,
            "key_type": key_type,
            "points_earned": points,
            "total_points": get_user_points(user_id)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"验证密钥失败: {e}")
        raise HTTPException(status_code=500, detail="服务器内部错误")

@api.get("/api/key/info")
async def get_key_info():
    """获取当前密钥信息（管理员调试用）"""
    try:
        key_date = get_secret_key_date()
        keys = get_or_create_daily_keys(key_date)
        
        return {
            "success": True,
            "key_date": str(key_date),
            "links_updated": keys['link_updated'],
            "has_key1_link": keys['key1_link'] is not None,
            "has_key2_link": keys['key2_link'] is not None
        }
    except Exception as e:
        logger.error(f"获取密钥信息失败: {e}")
        raise HTTPException(status_code=500, detail="服务器内部错误")
        # ==================== 主程序 ====================

def run_fastapi():
    """在单独线程中运行 FastAPI"""
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(api, host="0.0.0.0", port=port, log_level="info")

async def post_init(application: Application):
    """机器人初始化后的回调"""
    logger.info("🤖 Telegram 机器人初始化完成")
    
    # 检查今天是否需要生成密钥
    key_date = get_secret_key_date()
    keys = get_or_create_daily_keys(key_date)
    logger.info(f"📅 当前密钥日期：{key_date}")
    logger.info(f"🔐 密钥1：{keys['key1']}")
    logger.info(f"🔐 密钥2：{keys['key2']}")
    logger.info(f"🔗 链接状态：{'已更新' if keys['link_updated'] else '未更新'}")

def main():
    """启动机器人和 API 服务"""
    global telegram_app
    
    # 检查环境变量
    if not BOT_TOKEN:
        logger.error("❌ 请设置 BOT_TOKEN 环境变量")
        return
    
    if not ADMIN_ID:
        logger.error("❌ 请设置 ADMIN_ID 环境变量")
        return
    
    if not DATABASE_URL:
        logger.error("❌ 请设置 DATABASE_URL 环境变量")
        return
    
    logger.info("🚀 正在启动服务...")
    logger.info(f"👤 管理员ID：{ADMIN_ID}")
    logger.info(f"🌐 API地址：{API_BASE_URL}")
    logger.info(f"📄 GitHub Pages：{GITHUB_PAGES_URL}")
    
    # 初始化数据库
    init_database()
    
    # 设置定时任务
    setup_scheduler()
    
    # 创建 Telegram 应用
    telegram_app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    # 添加命令处理器
    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("admin", admin_command))
    telegram_app.add_handler(CommandHandler("id", id_command))
    telegram_app.add_handler(CommandHandler("jf", jf_command))
    telegram_app.add_handler(CommandHandler("hd", hd_command))
    telegram_app.add_handler(CommandHandler("my", my_command))
    
    # 添加回调处理器
    telegram_app.add_handler(CallbackQueryHandler(handle_callback))
    
    # 添加消息处理器
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    # 在单独线程中启动 FastAPI
    api_thread = threading.Thread(target=run_fastapi, daemon=True)
    api_thread.start()
    logger.info("🌐 FastAPI 服务已启动")
    
    # 启动 Telegram 机器人
    logger.info("🤖 Telegram 机器人启动中...")
    telegram_app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
