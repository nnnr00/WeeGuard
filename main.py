import os
import logging
import random
import secrets
import hashlib
import string
import asyncio
from datetime import datetime, date, timedelta, time
from typing import Optional
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    CallbackQueryHandler, 
    ConversationHandler,
    filters, 
    ContextTypes
)
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import uvicorn
import threading
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# ==================== 配置 ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 环境变量
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://your-username.github.io/your-repo")
API_SECRET = os.getenv("API_SECRET", secrets.token_hex(32))

# 北京时区
BEIJING_TZ = pytz.timezone('Asia/Shanghai')

# 会话状态
WAITING_PHOTO = 1
WAITING_KEY_INPUT = 2
WAITING_KEY1_URL = 3
WAITING_KEY2_URL = 4

# 广告观看积分配置
AD_REWARDS = {
    1: 10,
    2: 6,
    3: (3, 10)
}
MAX_AD_VIEWS_PER_DAY = 3

# 密钥积分配置
KEY_REWARDS = {
    1: 8,  # 密钥1 获得8积分
    2: 6   # 密钥2 获得6积分
}
MAX_KEY_CLICKS_PER_DAY = 2

# Monetag 直链（固定不变）
MONETAG_DIRECT_LINKS = {
    1: "https://otieu.com/4/10489994",  # 第一次点击的直链
    2: "https://otieu.com/4/10489998"   # 第二次点击的直链
}

# 全局变量存储 bot application
bot_application = None

# ==================== 数据库操作 ====================
def get_db_connection():
    """获取数据库连接"""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_database():
    """初始化数据库表（保留原有数据）"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # File ID 表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS file_ids (
                id SERIAL PRIMARY KEY,
                file_id TEXT NOT NULL,
                file_type TEXT DEFAULT 'photo',
                file_unique_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 用户积分表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_points (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                points INTEGER DEFAULT 0,
                total_checkins INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 签到记录表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS checkin_records (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                checkin_date DATE NOT NULL,
                points_earned INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, checkin_date)
            )
        """)
        
        # 广告观看记录表
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
        
        # 广告验证 Token 表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ad_tokens (
                id SERIAL PRIMARY KEY,
                token TEXT UNIQUE NOT NULL,
                user_id BIGINT NOT NULL,
                status TEXT DEFAULT 'pending',
                ip_address TEXT,
                user_agent TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                verified_at TIMESTAMP,
                expires_at TIMESTAMP NOT NULL
            )
        """)
        
        # 每日密钥表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_keys (
                id SERIAL PRIMARY KEY,
                key_date DATE UNIQUE NOT NULL,
                key1 TEXT NOT NULL,
                key2 TEXT NOT NULL,
                key1_url TEXT,
                key2_url TEXT,
                is_url_set BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 用户密钥领取记录表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_key_claims (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                key_date DATE NOT NULL,
                key_type INTEGER NOT NULL,
                points_earned INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, key_date, key_type)
            )
        """)
        
        # 用户密钥点击记录表（按周期重置，10点为分界）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_key_clicks (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                cycle_date DATE NOT NULL,
                click_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, cycle_date)
            )
        """)
        
        conn.commit()
        cur.close()
        conn.close()
        logger.info("✅ 数据库初始化完成（已保留原有数据）")
    except Exception as e:
        logger.error(f"❌ 数据库初始化失败: {e}")

# ==================== File ID 数据库操作 ====================
def save_file_id(file_id: str, file_unique_id: str, file_type: str = 'photo') -> int:
    """保存 File ID 到数据库"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO file_ids (file_id, file_unique_id, file_type) 
           VALUES (%s, %s, %s) RETURNING id""",
        (file_id, file_unique_id, file_type)
    )
    record_id = cur.fetchone()['id']
    conn.commit()
    cur.close()
    conn.close()
    return record_id

def get_all_file_records(limit: int = 20):
    """获取所有文件记录"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM file_ids ORDER BY created_at DESC LIMIT %s", 
        (limit,)
    )
    records = cur.fetchall()
    cur.close()
    conn.close()
    return records

def get_file_record(record_id: int):
    """获取单条记录"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM file_ids WHERE id = %s", (record_id,))
    record = cur.fetchone()
    cur.close()
    conn.close()
    return record

def delete_file_record(record_id: int) -> bool:
    """删除记录"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM file_ids WHERE id = %s", (record_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    cur.close()
    conn.close()
    return deleted

# ==================== 积分数据库操作 ====================
def get_or_create_user(user_id: int, username: str = None, first_name: str = None):
    """获取或创建用户"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM user_points WHERE user_id = %s", (user_id,))
    user = cur.fetchone()
    
    if not user:
        cur.execute(
            """INSERT INTO user_points (user_id, username, first_name, points, total_checkins) 
               VALUES (%s, %s, %s, 0, 0) RETURNING *""",
            (user_id, username, first_name)
        )
        user = cur.fetchone()
        conn.commit()
    else:
        cur.execute(
            """UPDATE user_points SET username = %s, first_name = %s, updated_at = CURRENT_TIMESTAMP 
               WHERE user_id = %s""",
            (username, first_name, user_id)
        )
        conn.commit()
    
    cur.close()
    conn.close()
    return user

def get_user_points(user_id: int) -> int:
    """获取用户积分"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT points FROM user_points WHERE user_id = %s", (user_id,))
    result = cur.fetchone()
    cur.close()
    conn.close()
    return result['points'] if result else 0

def add_user_points(user_id: int, points: int):
    """增加用户积分"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """UPDATE user_points 
           SET points = points + %s, updated_at = CURRENT_TIMESTAMP 
           WHERE user_id = %s""",
        (points, user_id)
    )
    conn.commit()
    cur.close()
    conn.close()

def increment_checkin_count(user_id: int):
    """增加签到次数"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """UPDATE user_points 
           SET total_checkins = total_checkins + 1, updated_at = CURRENT_TIMESTAMP 
           WHERE user_id = %s""",
        (user_id,)
    )
    conn.commit()
    cur.close()
    conn.close()

def get_user_total_checkins(user_id: int) -> int:
    """获取用户总签到次数"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT total_checkins FROM user_points WHERE user_id = %s", (user_id,))
    result = cur.fetchone()
    cur.close()
    conn.close()
    return result['total_checkins'] if result else 0

def check_today_checkin(user_id: int) -> bool:
    """检查今天是否已签到"""
    conn = get_db_connection()
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

def record_checkin(user_id: int, points: int):
    """记录签到"""
    conn = get_db_connection()
    cur = conn.cursor()
    today = get_beijing_date()
    cur.execute(
        """INSERT INTO checkin_records (user_id, checkin_date, points_earned) 
           VALUES (%s, %s, %s)""",
        (user_id, today, points)
    )
    conn.commit()
    cur.close()
    conn.close()

# ==================== 广告观看数据库操作 ====================
def get_beijing_date() -> date:
    """获取北京时间的日期"""
    return datetime.now(BEIJING_TZ).date()

def get_beijing_datetime() -> datetime:
    """获取北京时间"""
    return datetime.now(BEIJING_TZ)

def get_current_key_cycle_date() -> date:
    """获取当前密钥周期日期（以北京时间10点为分界）"""
    now = get_beijing_datetime()
    # 如果当前时间在10点之前，使用前一天的日期
    if now.hour < 10:
        return (now - timedelta(days=1)).date()
    return now.date()

def get_user_ad_watch_count(user_id: int) -> int:
    """获取用户今日广告观看次数"""
    conn = get_db_connection()
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

def increment_ad_watch_count(user_id: int, points: int):
    """增加广告观看次数和积分"""
    conn = get_db_connection()
    cur = conn.cursor()
    today = get_beijing_date()
    
    cur.execute(
        """INSERT INTO ad_watch_records (user_id, watch_date, watch_count, points_earned)
           VALUES (%s, %s, 1, %s)
           ON CONFLICT (user_id, watch_date) 
           DO UPDATE SET watch_count = ad_watch_records.watch_count + 1,
                         points_earned = ad_watch_records.points_earned + %s,
                         updated_at = CURRENT_TIMESTAMP""",
        (user_id, today, points, points)
    )
    
    conn.commit()
    cur.close()
    conn.close()

def calculate_ad_reward(watch_count: int) -> int:
    """计算广告观看奖励"""
    next_watch = watch_count + 1
    if next_watch == 1:
        return AD_REWARDS[1]
    elif next_watch == 2:
        return AD_REWARDS[2]
    elif next_watch == 3:
        min_points, max_points = AD_REWARDS[3]
        return random.randint(min_points, max_points)
    return 0

# ==================== Token 管理 ====================
def generate_ad_token(user_id: int) -> str:
    """生成广告验证 Token"""
    raw_token = secrets.token_urlsafe(32)
    token = hashlib.sha256(f"{raw_token}{API_SECRET}{user_id}".encode()).hexdigest()[:48]
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute(
        "DELETE FROM ad_tokens WHERE user_id = %s AND (status != 'pending' OR expires_at < CURRENT_TIMESTAMP)",
        (user_id,)
    )
    
    expires_at = datetime.utcnow() + timedelta(minutes=10)
    
    cur.execute(
        """INSERT INTO ad_tokens (token, user_id, status, expires_at) 
           VALUES (%s, %s, 'pending', %s)""",
        (token, user_id, expires_at)
    )
    
    conn.commit()
    cur.close()
    conn.close()
    
    return token

def verify_ad_token(token: str, ip_address: str = None, user_agent: str = None) -> Optional[dict]:
    """验证广告 Token"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute(
        """SELECT * FROM ad_tokens 
           WHERE token = %s AND status = 'pending' AND expires_at > CURRENT_TIMESTAMP""",
        (token,)
    )
    token_record = cur.fetchone()
    
    if not token_record:
        cur.close()
        conn.close()
        return None
    
    user_id = token_record['user_id']
    
    today = get_beijing_date()
    cur.execute(
        "SELECT watch_count FROM ad_watch_records WHERE user_id = %s AND watch_date = %s",
        (user_id, today)
    )
    watch_record = cur.fetchone()
    current_count = watch_record['watch_count'] if watch_record else 0
    
    if current_count >= MAX_AD_VIEWS_PER_DAY:
        cur.close()
        conn.close()
        return {'error': 'max_reached', 'message': '今日观看次数已达上限'}
    
    cur.execute(
        """UPDATE ad_tokens 
           SET status = 'verified', verified_at = CURRENT_TIMESTAMP, 
               ip_address = %s, user_agent = %s
           WHERE token = %s""",
        (ip_address, user_agent, token)
    )
    
    conn.commit()
    cur.close()
    conn.close()
    
    return {'user_id': user_id, 'watch_count': current_count}

def claim_ad_reward(token: str) -> Optional[dict]:
    """领取广告奖励"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute(
        """SELECT * FROM ad_tokens 
           WHERE token = %s AND status = 'verified'""",
        (token,)
    )
    token_record = cur.fetchone()
    
    if not token_record:
        cur.close()
        conn.close()
        return None
    
    user_id = token_record['user_id']
    
    today = get_beijing_date()
    cur.execute(
        "SELECT watch_count FROM ad_watch_records WHERE user_id = %s AND watch_date = %s",
        (user_id, today)
    )
    watch_record = cur.fetchone()
    current_count = watch_record['watch_count'] if watch_record else 0
    
    if current_count >= MAX_AD_VIEWS_PER_DAY:
        cur.close()
        conn.close()
        return {'error': 'max_reached', 'message': '今日观看次数已达上限'}
    
    reward = calculate_ad_reward(current_count)
    
    cur.execute(
        "UPDATE ad_tokens SET status = 'claimed' WHERE token = %s",
        (token,)
    )
    
    conn.commit()
    cur.close()
    conn.close()
    
    increment_ad_watch_count(user_id, reward)
    add_user_points(user_id, reward)
    
    new_count = current_count + 1
    
    return {
        'user_id': user_id,
        'reward': reward,
        'watch_count': new_count,
        'remaining': MAX_AD_VIEWS_PER_DAY - new_count
    }

# ==================== 密钥系统数据库操作 ====================
def generate_random_key(length: int = 10) -> str:
    """生成随机密钥（大小写字母+数字）"""
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

def get_or_create_daily_keys(key_date: date = None) -> dict:
    """获取或创建每日密钥"""
    if key_date is None:
        key_date = get_current_key_cycle_date()
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM daily_keys WHERE key_date = %s", (key_date,))
    record = cur.fetchone()
    
    if not record:
        key1 = generate_random_key(10)
        key2 = generate_random_key(10)
        
        cur.execute(
            """INSERT INTO daily_keys (key_date, key1, key2, is_url_set) 
               VALUES (%s, %s, %s, FALSE) RETURNING *""",
            (key_date, key1, key2)
        )
        record = cur.fetchone()
        conn.commit()
        logger.info(f"✅ 生成新密钥 - 日期: {key_date}, 密钥1: {key1}, 密钥2: {key2}")
    
    cur.close()
    conn.close()
    return dict(record)

def update_key_url(key_type: int, url: str) -> bool:
    """更新密钥链接"""
    key_date = get_current_key_cycle_date()
    conn = get_db_connection()
    cur = conn.cursor()
    
    if key_type == 1:
        cur.execute(
            """UPDATE daily_keys 
               SET key1_url = %s, is_url_set = (key2_url IS NOT NULL), updated_at = CURRENT_TIMESTAMP 
               WHERE key_date = %s""",
            (url, key_date)
        )
    else:
        cur.execute(
            """UPDATE daily_keys 
               SET key2_url = %s, is_url_set = (key1_url IS NOT NULL), updated_at = CURRENT_TIMESTAMP 
               WHERE key_date = %s""",
            (url, key_date)
        )
    
    # 检查是否两个链接都已设置
    cur.execute(
        """UPDATE daily_keys 
           SET is_url_set = (key1_url IS NOT NULL AND key2_url IS NOT NULL)
           WHERE key_date = %s""",
        (key_date,)
    )
    
    updated = cur.rowcount > 0
    conn.commit()
    cur.close()
    conn.close()
    return updated

def get_daily_keys() -> Optional[dict]:
    """获取当前周期的密钥"""
    key_date = get_current_key_cycle_date()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM daily_keys WHERE key_date = %s", (key_date,))
    record = cur.fetchone()
    cur.close()
    conn.close()
    return dict(record) if record else None

def get_user_key_click_count(user_id: int) -> int:
    """获取用户当前周期密钥点击次数"""
    cycle_date = get_current_key_cycle_date()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT click_count FROM user_key_clicks WHERE user_id = %s AND cycle_date = %s",
        (user_id, cycle_date)
    )
    result = cur.fetchone()
    cur.close()
    conn.close()
    return result['click_count'] if result else 0

def increment_key_click_count(user_id: int):
    """增加用户密钥点击次数"""
    cycle_date = get_current_key_cycle_date()
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute(
        """INSERT INTO user_key_clicks (user_id, cycle_date, click_count)
           VALUES (%s, %s, 1)
           ON CONFLICT (user_id, cycle_date) 
           DO UPDATE SET click_count = user_key_clicks.click_count + 1,
                         updated_at = CURRENT_TIMESTAMP""",
        (user_id, cycle_date)
    )
    
    conn.commit()
    cur.close()
    conn.close()

def check_user_key_claimed(user_id: int, key_type: int) -> bool:
    """检查用户是否已领取过该密钥"""
    key_date = get_current_key_cycle_date()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM user_key_claims WHERE user_id = %s AND key_date = %s AND key_type = %s",
        (user_id, key_date, key_type)
    )
    result = cur.fetchone()
    cur.close()
    conn.close()
    return result is not None

def claim_key_reward(user_id: int, key_type: int) -> int:
    """领取密钥奖励"""
    key_date = get_current_key_cycle_date()
    points = KEY_REWARDS.get(key_type, 0)
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute(
        """INSERT INTO user_key_claims (user_id, key_date, key_type, points_earned)
           VALUES (%s, %s, %s, %s)""",
        (user_id, key_date, key_type, points)
    )
    
    conn.commit()
    cur.close()
    conn.close()
    
    add_user_points(user_id, points)
    return points

def verify_key_input(key_input: str) -> Optional[dict]:
    """验证用户输入的密钥"""
    daily_keys = get_daily_keys()
    if not daily_keys:
        return None
    
    if key_input == daily_keys['key1']:
        return {'key_type': 1, 'points': KEY_REWARDS[1]}
    elif key_input == daily_keys['key2']:
        return {'key_type': 2, 'points': KEY_REWARDS[2]}
    
    return None

# ==================== 权限检查 ====================
def is_admin(user_id: int) -> bool:
    """检查是否是管理员"""
    return user_id == ADMIN_ID

# ==================== 键盘布局 ====================
def get_start_keyboard():
    """首页键盘"""
    keyboard = [
        [InlineKeyboardButton("✅ 开始验证", callback_data="user:verify")],
        [InlineKeyboardButton("💰 积分中心", callback_data="user:points")],
        [InlineKeyboardButton("🎉 开业活动", callback_data="user:activity")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_points_keyboard():
    """积分中心键盘"""
    keyboard = [
        [InlineKeyboardButton("📅 每日签到", callback_data="points:checkin")],
        [InlineKeyboardButton("🔙 返回首页", callback_data="user:back_home")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_to_points_keyboard():
    """返回积分中心键盘"""
    keyboard = [
        [InlineKeyboardButton("🔙 返回积分中心", callback_data="user:points")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_activity_keyboard(user_id: int):
    """活动中心键盘"""
    watch_count = get_user_ad_watch_count(user_id)
    key_click_count = get_user_key_click_count(user_id)
    
    keyboard = [
        [InlineKeyboardButton(f"🎬 看视频得积分 ({watch_count}/{MAX_AD_VIEWS_PER_DAY})", callback_data="activity:watch_ad")],
        [InlineKeyboardButton(f"🔑 夸克网盘密钥 ({key_click_count}/{MAX_KEY_CLICKS_PER_DAY})", callback_data="activity:get_key")],
        [InlineKeyboardButton("🔙 返回首页", callback_data="user:back_home")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_watch_ad_keyboard(user_id: int, token: str):
    """观看广告键盘"""
    webapp_url = f"{WEBAPP_URL}/index.html?token={token}"
    
    keyboard = [
        [InlineKeyboardButton("▶️ 开始观看", web_app=WebAppInfo(url=webapp_url))],
        [InlineKeyboardButton("🔙 返回活动中心", callback_data="user:activity")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_key_keyboard(user_id: int):
    """获取密钥键盘"""
    click_count = get_user_key_click_count(user_id)
    daily_keys = get_daily_keys()
    
    # 检查是否还有点击次数
    if click_count >= MAX_KEY_CLICKS_PER_DAY:
        keyboard = [
            [InlineKeyboardButton("🔙 返回活动中心", callback_data="user:activity")],
        ]
    elif not daily_keys or not daily_keys.get('is_url_set'):
        # 管理员还没设置链接
        keyboard = [
            [InlineKeyboardButton("🔙 返回活动中心", callback_data="user:activity")],
        ]
    else:
        # 根据点击次数决定跳转哪个链接
        next_click = click_count + 1
        if next_click == 1:
            redirect_url = f"{WEBAPP_URL}/redirect.html?type=1"
        else:
            redirect_url = f"{WEBAPP_URL}/redirect.html?type=2"
        
        keyboard = [
            [InlineKeyboardButton("🚀 开始获取密钥", web_app=WebAppInfo(url=redirect_url))],
            [InlineKeyboardButton("🔙 返回活动中心", callback_data="user:activity")],
        ]
    
    return InlineKeyboardMarkup(keyboard)

def get_back_to_activity_keyboard():
    """返回活动中心键盘"""
    keyboard = [
        [InlineKeyboardButton("🔙 返回活动中心", callback_data="user:activity")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard():
    """管理员后台主键盘"""
    keyboard = [
        [InlineKeyboardButton("📷 获取 File ID", callback_data="action:get_file_id")],
        [InlineKeyboardButton("📋 查看历史记录", callback_data="action:view_history")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_cancel_keyboard():
    """取消操作键盘"""
    keyboard = [[InlineKeyboardButton("❌ 取消并返回", callback_data="action:cancel")]]
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard():
    """返回后台键盘"""
    keyboard = [[InlineKeyboardButton("🔙 返回后台", callback_data="action:back")]]
    return InlineKeyboardMarkup(keyboard)

# ==================== 用户命令处理器 ====================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令"""
    user = update.effective_user
    
    get_or_create_user(user.id, user.username, user.first_name)
    
    await update.message.reply_text(
        f"👋 *欢迎使用本机器人！*\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"您好，{user.first_name}！\n\n"
        f"请选择下方功能：",
        reply_markup=get_start_keyboard(),
        parse_mode="Markdown"
    )

async def cmd_jf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /jf 命令 - 积分中心"""
    user = update.effective_user
    
    get_or_create_user(user.id, user.username, user.first_name)
    
    points = get_user_points(user.id)
    total_checkins = get_user_total_checkins(user.id)
    already_checkin = check_today_checkin(user.id)
    
    checkin_status = "✅ 今日已签到" if already_checkin else "⏳ 今日未签到"
    
    await update.message.reply_text(
        f"💰 *积分中心*\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 用户：{user.first_name}\n"
        f"💎 当前积分：*{points}*\n"
        f"📊 累计签到：*{total_checkins}* 次\n"
        f"📅 签到状态：{checkin_status}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"选择下方功能：",
        reply_markup=get_points_keyboard(),
        parse_mode="Markdown"
    )

async def cmd_hd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /hd 命令 - 活动中心"""
    user = update.effective_user
    
    get_or_create_user(user.id, user.username, user.first_name)
    
    watch_count = get_user_ad_watch_count(user.id)
    key_click_count = get_user_key_click_count(user.id)
    
    await update.message.reply_text(
        f"🎉 *活动中心*\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🎊 开业活动火热进行中！\n\n"
        f"📺 视频观看：*{watch_count}/{MAX_AD_VIEWS_PER_DAY}* 次\n"
        f"🔑 密钥获取：*{key_click_count}/{MAX_KEY_CLICKS_PER_DAY}* 次\n"
        f"⏰ 每日北京时间 10:00 重置\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"选择下方活动参与：",
        reply_markup=get_activity_keyboard(user.id),
        parse_mode="Markdown"
    )

# ==================== 管理员命令处理器 ====================
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /admin 命令"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("⛔ 抱歉，您没有权限访问管理后台")
        return
    
    await update.message.reply_text(
        "🔐 *管理员后台*\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "请选择需要的功能：",
        reply_markup=get_admin_keyboard(),
        parse_mode="Markdown"
    )

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /id 命令 - 快捷获取 File ID"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("⛔ 抱歉，您没有权限使用此功能")
        return ConversationHandler.END
    
    context.user_data['waiting_photo'] = True
    
    await update.message.reply_text(
        "📷 *获取 File ID*\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "请发送一张图片，我将获取其 File ID\n"
        "并保存到数据库中",
        reply_markup=get_cancel_keyboard(),
        parse_mode="Markdown"
    )
    return WAITING_PHOTO

async def cmd_my(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /my 命令 - 管理员查看/设置密钥"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("⛔ 抱歉，您没有权限使用此功能")
        return ConversationHandler.END
    
    # 检查当前时间是否在10点之后
    now = get_beijing_datetime()
    
    # 获取或创建今日密钥
    daily_keys = get_or_create_daily_keys()
    
    key1_url_status = "✅ 已设置" if daily_keys.get('key1_url') else "❌ 未设置"
    key2_url_status = "✅ 已设置" if daily_keys.get('key2_url') else "❌ 未设置"
    
    await update.message.reply_text(
        f"🔐 *今日密钥管理*\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📅 周期日期：*{daily_keys['key_date']}*\n"
        f"⏰ 当前时间：{now.strftime('%H:%M:%S')}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🔑 *密钥1*（8积分）：\n`{daily_keys['key1']}`\n"
        f"📎 链接状态：{key1_url_status}\n\n"
        f"🔑 *密钥2*（6积分）：\n`{daily_keys['key2']}`\n"
        f"📎 链接状态：{key2_url_status}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"💡 请发送密钥1的网盘链接：",
        parse_mode="Markdown"
    )
    
    context.user_data['setting_key_url'] = 1
    return WAITING_KEY1_URL

# ==================== 用户回调处理器 ====================
async def handle_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理用户相关按钮回调"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data
    
    # ===== 返回首页 =====
    if data == "user:back_home":
        await query.edit_message_text(
            f"👋 *欢迎使用本机器人！*\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"您好，{user.first_name}！\n\n"
            f"请选择下方功能：",
            reply_markup=get_start_keyboard(),
            parse_mode="Markdown"
        )
    
    # ===== 开始验证 =====
    elif data == "user:verify":
        await query.edit_message_text(
            "✅ *开始验证*\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "验证功能开发中...\n\n"
            "请稍后再试",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 返回首页", callback_data="user:back_home")]
            ]),
            parse_mode="Markdown"
        )
    
    # ===== 积分中心 =====
    elif data == "user:points":
        get_or_create_user(user.id, user.username, user.first_name)
        
        points = get_user_points(user.id)
        total_checkins = get_user_total_checkins(user.id)
        already_checkin = check_today_checkin(user.id)
        
        checkin_status = "✅ 今日已签到" if already_checkin else "⏳ 今日未签到"
        
        await query.edit_message_text(
            f"💰 *积分中心*\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 用户：{user.first_name}\n"
            f"💎 当前积分：*{points}*\n"
            f"📊 累计签到：*{total_checkins}* 次\n"
            f"📅 签到状态：{checkin_status}\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"选择下方功能：",
            reply_markup=get_points_keyboard(),
            parse_mode="Markdown"
        )
    
    # ===== 每日签到 =====
    elif data == "points:checkin":
        get_or_create_user(user.id, user.username, user.first_name)
        
        if check_today_checkin(user.id):
            points = get_user_points(user.id)
            await query.edit_message_text(
                f"⚠️ *签到提示*\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"您今天已经签到过了！\n\n"
                f"💎 当前积分：*{points}*\n\n"
                f"明天再来吧~ 🌟",
                reply_markup=get_back_to_points_keyboard(),
                parse_mode="Markdown"
            )
            return
        
        total_checkins = get_user_total_checkins(user.id)
        
        if total_checkins == 0:
            earned_points = 10
        else:
            earned_points = random.randint(3, 8)
        
        record_checkin(user.id, earned_points)
        add_user_points(user.id, earned_points)
        increment_checkin_count(user.id)
        
        new_points = get_user_points(user.id)
        new_total_checkins = get_user_total_checkins(user.id)
        
        if total_checkins == 0:
            bonus_text = "🎉 首次签到奖励！"
        else:
            bonus_text = "🎲 随机奖励"
        
        await query.edit_message_text(
            f"🎉 *签到成功！*\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{bonus_text}\n\n"
            f"✨ 获得积分：*+{earned_points}*\n"
            f"💎 当前积分：*{new_points}*\n"
            f"📊 累计签到：*{new_total_checkins}* 次\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"明天记得再来签到哦~ 🌟",
            reply_markup=get_back_to_points_keyboard(),
            parse_mode="Markdown"
        )
    
    # ===== 活动中心 =====
    elif data == "user:activity":
        get_or_create_user(user.id, user.username, user.first_name)
        
        watch_count = get_user_ad_watch_count(user.id)
        key_click_count = get_user_key_click_count(user.id)
        
        await query.edit_message_text(
            f"🎉 *活动中心*\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎊 开业活动火热进行中！\n\n"
            f"📺 视频观看：*{watch_count}/{MAX_AD_VIEWS_PER_DAY}* 次\n"
            f"🔑 密钥获取：*{key_click_count}/{MAX_KEY_CLICKS_PER_DAY}* 次\n"
            f"⏰ 每日北京时间 10:00 重置\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"选择下方活动参与：",
            reply_markup=get_activity_keyboard(user.id),
            parse_mode="Markdown"
        )

# ==================== 活动回调处理器 ====================
async def handle_activity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理活动相关按钮回调"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data
    
    # ===== 看视频得积分 =====
    if data == "activity:watch_ad":
        get_or_create_user(user.id, user.username, user.first_name)
        
        watch_count = get_user_ad_watch_count(user.id)
        
        if watch_count >= MAX_AD_VIEWS_PER_DAY:
            await query.edit_message_text(
                f"⚠️ *观看提示*\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"您今日观看次数已达上限！\n\n"
                f"📺 今日观看：*{watch_count}/{MAX_AD_VIEWS_PER_DAY}*\n"
                f"⏰ 北京时间 10:00 重置\n\n"
                f"明天再来吧~ 🌟",
                reply_markup=get_back_to_activity_keyboard(),
                parse_mode="Markdown"
            )
            return
        
        token = generate_ad_token(user.id)
        
        next_watch = watch_count + 1
        if next_watch == 1:
            reward_text = f"第 1 次观看可获得 *10* 积分"
        elif next_watch == 2:
            reward_text = f"第 2 次观看可获得 *6* 积分"
        else:
            reward_text = f"第 3 次观看可获得 *3-10* 随机积分"
        
        await query.edit_message_text(
            f"🎬 *看视频得积分*\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📺 观看视频广告即可获得积分奖励！\n\n"
            f"📊 当前进度：*{watch_count}/{MAX_AD_VIEWS_PER_DAY}*\n"
            f"🎁 {reward_text}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ *注意事项：*\n"
            f"• 请完整观看视频\n"
            f"• 中途退出无法获得积分\n"
            f"• 每日最多观看 3 次\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"点击下方按钮开始观看：",
            reply_markup=get_watch_ad_keyboard(user.id, token),
            parse_mode="Markdown"
        )
    
    # ===== 夸克网盘密钥 =====
    elif data == "activity:get_key":
        get_or_create_user(user.id, user.username, user.first_name)
        
        click_count = get_user_key_click_count(user.id)
        daily_keys = get_daily_keys()
        
        # 检查是否已达上限
        if click_count >= MAX_KEY_CLICKS_PER_DAY:
            now = get_beijing_datetime()
            if now.hour >= 10:
                next_reset = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0)
            else:
                next_reset = now.replace(hour=10, minute=0, second=0)
            
            await query.edit_message_text(
                f"⏰ *次数已用完*\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"您今日获取密钥次数已达上限！\n\n"
                f"🔑 今日获取：*{click_count}/{MAX_KEY_CLICKS_PER_DAY}*\n"
                f"⏰ 下次重置：明日 10:00\n\n"
                f"请明天北京时间 10:00 后再来~ 🌟",
                reply_markup=get_back_to_activity_keyboard(),
                parse_mode="Markdown"
            )
            return
        
        # 检查管理员是否设置了链接
        if not daily_keys or not daily_keys.get('is_url_set'):
            await query.edit_message_text(
                f"⏳ *请稍候*\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ 管理员还未更换今日密钥链接\n\n"
                f"请等待管理员设置后再来获取~\n\n"
                f"💡 提示：每日北京时间 10:00 更新",
                reply_markup=get_back_to_activity_keyboard(),
                parse_mode="Markdown"
            )
            return
        
        # 计算下一次点击的奖励
        next_click = click_count + 1
        if next_click == 1:
            reward_text = "第 1 次获取可获得 *8* 积分"
        else:
            reward_text = "第 2 次获取可获得 *6* 积分"
        
        # 设置用户状态为等待输入密钥
        context.user_data['waiting_key'] = True
        context.user_data['key_click_num'] = next_click
        
        await query.edit_message_text(
            f"🔑 *夸克网盘密钥*\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📋 *获取步骤：*\n\n"
            f"1️⃣ 点击下方「开始获取密钥」按钮\n"
            f"2️⃣ 等待 3 秒自动跳转到网盘页面\n"
            f"3️⃣ 保存文件到您的网盘\n"
            f"4️⃣ 打开文件，复制里面的密钥\n"
            f"5️⃣ 返回机器人，发送密钥领取积分\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 当前进度：*{click_count}/{MAX_KEY_CLICKS_PER_DAY}*\n"
            f"🎁 {reward_text}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ *注意事项：*\n"
            f"• 每个密钥仅可使用一次\n"
            f"• 请勿重复领取同一密钥\n"
            f"• 每日北京时间 10:00 更新密钥\n"
            f"• 中途请勿关闭页面\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"点击下方按钮开始获取：",
            reply_markup=get_key_keyboard(user.id),
            parse_mode="Markdown"
        )

# ==================== 管理员回调处理器 ====================
async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理管理员相关按钮回调"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if not is_admin(user_id):
        await query.edit_message_text("⛔ 权限不足")
        return ConversationHandler.END
    
    data = query.data
    
    # ===== 主菜单操作 =====
    if data == "action:get_file_id":
        context.user_data['waiting_photo'] = True
        await query.edit_message_text(
            "📷 *获取 File ID*\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "请发送一张图片，我将获取其 File ID\n"
            "并保存到数据库中",
            reply_markup=get_cancel_keyboard(),
            parse_mode="Markdown"
        )
        return WAITING_PHOTO
    
    elif data == "action:view_history":
        records = get_all_file_records()
        
        if not records:
            await query.edit_message_text(
                "📋 *历史记录*\n\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "📭 暂无任何记录",
                reply_markup=get_back_keyboard(),
                parse_mode="Markdown"
            )
            return
        
        keyboard = []
        for record in records:
            created_time = record['created_at'].strftime('%m-%d %H:%M')
            btn_text = f"🖼 #{record['id']} | {created_time}"
            keyboard.append([
                InlineKeyboardButton(btn_text, callback_data=f"view:{record['id']}")
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 返回后台", callback_data="action:back")])
        
        await query.edit_message_text(
            "📋 *历史记录*\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"共 {len(records)} 条记录\n"
            "点击查看详情或删除：",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    
    elif data == "action:cancel" or data == "action:back":
        context.user_data['waiting_photo'] = False
        await query.edit_message_text(
            "🔐 *管理员后台*\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "请选择需要的功能：",
            reply_markup=get_admin_keyboard(),
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    
    # ===== 查看单条记录 =====
    elif data.startswith("view:"):
        record_id = int(data.split(":")[1])
        record = get_file_record(record_id)
        
        if not record:
            await query.edit_message_text(
                "❌ 记录不存在或已被删除",
                reply_markup=get_back_keyboard()
            )
            return
        
        try:
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=record['file_id'],
                caption=(
                    f"🖼 *图片预览 #{record['id']}*\n\n"
                    f"📎 File ID:\n`{record['file_id']}`"
                ),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"发送图片失败: {e}")
        
        created_time = record['created_at'].strftime('%Y-%m-%d %H:%M:%S')
        
        keyboard = [
            [InlineKeyboardButton("🗑 删除此记录", callback_data=f"delete:{record_id}")],
            [InlineKeyboardButton("🔙 返回列表", callback_data="action:view_history")]
        ]
        
        await query.edit_message_text(
            f"📄 *记录详情 #{record['id']}*\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📎 *File ID:*\n`{record['file_id']}`\n\n"
            f"📅 *创建时间:*\n{created_time}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    
    # ===== 删除确认 =====
    elif data.startswith("delete:"):
        record_id = int(data.split(":")[1])
        
        keyboard = [
            [
                InlineKeyboardButton("✅ 确认删除", callback_data=f"confirm_del:{record_id}"),
                InlineKeyboardButton("❌ 取消", callback_data=f"view:{record_id}")
            ]
        ]
        
        await query.edit_message_text(
            f"⚠️ *确认删除*\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"确定要删除记录 *#{record_id}* 吗？\n\n"
            f"此操作不可恢复！",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    
    # ===== 确认删除 =====
    elif data.startswith("confirm_del:"):
        record_id = int(data.split(":")[1])
        
        if delete_file_record(record_id):
            await query.edit_message_text(
                f"✅ *删除成功*\n\n"
                f"记录 #{record_id} 已被删除",
                reply_markup=get_admin_keyboard(),
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text(
                "❌ 删除失败，记录可能不存在",
                reply_markup=get_back_keyboard()
            )

# ==================== 消息处理器 ====================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理收到的图片"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        return ConversationHandler.END
    
    if not context.user_data.get('waiting_photo'):
        return ConversationHandler.END
    
    photo = update.message.photo[-1]
    file_id = photo.file_id
    file_unique_id = photo.file_unique_id
    
    record_id = save_file_id(file_id, file_unique_id, 'photo')
    
    context.user_data['waiting_photo'] = False
    
    await update.message.reply_text(
        f"✅ *获取成功！*\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📝 *记录 ID:* `{record_id}`\n\n"
        f"📎 *File ID:*\n`{file_id}`\n\n"
        f"💾 已保存到数据库",
        reply_markup=get_admin_keyboard(),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def handle_non_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理非图片消息（在等待图片时）"""
    if context.user_data.get('waiting_photo'):
        await update.message.reply_text(
            "⚠️ 请发送图片，或点击下方按钮取消",
            reply_markup=get_cancel_keyboard()
        )
        return WAITING_PHOTO

async def handle_key_url_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理管理员输入密钥链接"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        return ConversationHandler.END
    
    setting_key = context.user_data.get('setting_key_url')
    
    if setting_key == 1:
        url = update.message.text.strip()
        update_key_url(1, url)
        
        context.user_data['setting_key_url'] = 2
        
        await update.message.reply_text(
            f"✅ *密钥1链接设置成功！*\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📎 链接：{url}\n\n"
            f"💡 请继续发送密钥2的网盘链接：",
            parse_mode="Markdown"
        )
        return WAITING_KEY2_URL
    
    elif setting_key == 2:
        url = update.message.text.strip()
        update_key_url(2, url)
        
        context.user_data['setting_key_url'] = None
        
        daily_keys = get_daily_keys()
        
        await update.message.reply_text(
            f"✅ *密钥链接设置完成！*\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🔑 密钥1链接：\n{daily_keys['key1_url']}\n\n"
            f"🔑 密钥2链接：\n{daily_keys['key2_url']}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"用户现在可以正常获取密钥了！",
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    
    return ConversationHandler.END

async def handle_user_key_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理用户输入密钥"""
    user = update.effective_user
    user_id = user.id
    text = update.message.text.strip()
    
    # 检查是否是管理员输入链接
    if is_admin(user_id) and context.user_data.get('setting_key_url'):
        return await handle_key_url_input(update, context)
    
    # 检查是否是普通用户输入密钥
    if not context.user_data.get('waiting_key'):
        return
    
    get_or_create_user(user_id, user.username, user.first_name)
    
    # 验证密钥
    key_result = verify_key_input(text)
    
    if not key_result:
        await update.message.reply_text(
            f"❌ *密钥无效*\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"您输入的密钥不正确或已过期。\n\n"
            f"请检查后重新输入，或返回活动中心。",
            reply_markup=get_back_to_activity_keyboard(),
            parse_mode="Markdown"
        )
        return
    
    key_type = key_result['key_type']
    points = key_result['points']
    
    # 检查是否已领取过
    if check_user_key_claimed(user_id, key_type):
        await update.message.reply_text(
            f"⚠️ *重复领取*\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"您已经领取过该密钥的奖励了！\n\n"
            f"每个密钥仅可领取一次。",
            reply_markup=get_back_to_activity_keyboard(),
            parse_mode="Markdown"
        )
        return
    
    # 领取奖励
    earned_points = claim_key_reward(user_id, key_type)
    
    # 增加点击次数
    increment_key_click_count(user_id)
    
    # 重置状态
    context.user_data['waiting_key'] = False
    
    # 获取更新后的信息
    new_points = get_user_points(user_id)
    click_count = get_user_key_click_count(user_id)
    
    await update.message.reply_text(
        f"🎉 *领取成功！*\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"✨ 密钥{key_type} 验证通过！\n\n"
        f"🎁 获得积分：*+{earned_points}*\n"
        f"💎 当前积分：*{new_points}*\n"
        f"🔑 今日获取：*{click_count}/{MAX_KEY_CLICKS_PER_DAY}*\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"感谢参与活动！🌟",
        reply_markup=get_back_to_activity_keyboard(),
        parse_mode="Markdown"
    )

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理文本消息"""
    user = update.effective_user
    user_id = user.id
    text = update.message.text.strip()
    
    # 检查是否是管理员设置链接
    if is_admin(user_id) and context.user_data.get('setting_key_url'):
        return await handle_key_url_input(update, context)
    
    # 检查是否在等待输入密钥
    if context.user_data.get('waiting_key'):
        return await handle_user_key_input(update, context)
    
    # 其他情况：尝试验证密钥
    get_or_create_user(user_id, user.username, user.first_name)
    
    key_result = verify_key_input(text)
    
    if key_result:
        key_type = key_result['key_type']
        points = key_result['points']
        
        # 检查是否已领取过
        if check_user_key_claimed(user_id, key_type):
            await update.message.reply_text(
                f"⚠️ *重复领取*\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"您已经领取过该密钥的奖励了！\n\n"
                f"每个密钥仅可领取一次。",
                reply_markup=get_back_to_activity_keyboard(),
                parse_mode="Markdown"
            )
            return
        
        # 领取奖励
        earned_points = claim_key_reward(user_id, key_type)
        
        # 增加点击次数
        increment_key_click_count(user_id)
        
        # 获取更新后的信息
        new_points = get_user_points(user_id)
        click_count = get_user_key_click_count(user_id)
        
        await update.message.reply_text(
            f"🎉 *领取成功！*\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"✨ 密钥{key_type} 验证通过！\n\n"
            f"🎁 获得积分：*+{earned_points}*\n"
            f"💎 当前积分：*{new_points}*\n"
            f"🔑 今日获取：*{click_count}/{MAX_KEY_CLICKS_PER_DAY}*\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"感谢参与活动！🌟",
            reply_markup=get_back_to_activity_keyboard(),
            parse_mode="Markdown"
        )

# ==================== 定时任务 ====================
async def generate_daily_keys_job():
    """每日生成新密钥的定时任务"""
    global bot_application
    
    logger.info("⏰ 执行每日密钥生成任务...")
    
    # 生成新密钥
    new_keys = get_or_create_daily_keys()
    
    # 发送给管理员
    if bot_application and ADMIN_ID:
        try:
            message = (
                f"🔔 *每日密钥更新通知*\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📅 日期：{new_keys['key_date']}\n"
                f"⏰ 时间：{get_beijing_datetime().strftime('%H:%M:%S')}\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"🔑 *密钥1*（8积分）：\n`{new_keys['key1']}`\n\n"
                f"🔑 *密钥2*（6积分）：\n`{new_keys['key2']}`\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ 请使用 /my 命令设置密钥链接"
            )
            await bot_application.bot.send_message(
                chat_id=ADMIN_ID,
                text=message,
                parse_mode="Markdown"
            )
            logger.info(f"✅ 已向管理员发送新密钥通知")
        except Exception as e:
            logger.error(f"❌ 发送密钥通知失败: {e}")

# ==================== FastAPI 应用 ====================
app = FastAPI(title="Telegram Bot API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    """根路径"""
    return {"status": "ok", "message": "Telegram Bot API is running"}

@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

@app.post("/api/ad/verify")
async def verify_ad(request: Request, token: str = Query(...)):
    """验证广告观看开始"""
    try:
        ip_address = request.client.host
        user_agent = request.headers.get("user-agent", "")
        
        result = verify_ad_token(token, ip_address, user_agent)
        
        if not result:
            raise HTTPException(status_code=400, detail="无效或过期的 Token")
        
        if 'error' in result:
            raise HTTPException(status_code=400, detail=result['message'])
        
        return JSONResponse({
            "success": True,
            "message": "验证成功，请观看广告",
            "watch_count": result['watch_count']
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"验证广告失败: {e}")
        raise HTTPException(status_code=500, detail="服务器错误")

@app.post("/api/ad/claim")
async def claim_reward(request: Request, token: str = Query(...)):
    """领取广告奖励"""
    try:
        result = claim_ad_reward(token)
        
        if not result:
            raise HTTPException(status_code=400, detail="无效的 Token 或奖励已领取")
        
        if 'error' in result:
            raise HTTPException(status_code=400, detail=result['message'])
        
        return JSONResponse({
            "success": True,
            "message": f"恭喜获得 {result['reward']} 积分！",
            "reward": result['reward'],
            "watch_count": result['watch_count'],
            "remaining": result['remaining']
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"领取奖励失败: {e}")
        raise HTTPException(status_code=500, detail="服务器错误")

@app.get("/api/ad/status")
async def get_ad_status(token: str = Query(...)):
    """获取广告观看状态"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute(
            "SELECT * FROM ad_tokens WHERE token = %s",
            (token,)
        )
        token_record = cur.fetchone()
        
        if not token_record:
            raise HTTPException(status_code=404, detail="Token 不存在")
        
        user_id = token_record['user_id']
        today = get_beijing_date()
        
        cur.execute(
            "SELECT watch_count FROM ad_watch_records WHERE user_id = %s AND watch_date = %s",
            (user_id, today)
        )
        watch_record = cur.fetchone()
        
        cur.close()
        conn.close()
        
        return JSONResponse({
            "success": True,
            "status": token_record['status'],
            "watch_count": watch_record['watch_count'] if watch_record else 0,
            "max_watches": MAX_AD_VIEWS_PER_DAY
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取状态失败: {e}")
        raise HTTPException(status_code=500, detail="服务器错误")

@app.get("/api/key/urls")
async def get_key_urls(type: int = Query(...)):
    """获取密钥跳转链接"""
    try:
        daily_keys = get_daily_keys()
        
        if not daily_keys:
            raise HTTPException(status_code=404, detail="今日密钥未生成")
        
        if not daily_keys.get('is_url_set'):
            raise HTTPException(status_code=400, detail="密钥链接未设置")
        
        # 获取 Monetag 直链和密钥链接
        monetag_url = MONETAG_DIRECT_LINKS.get(type)
        
        if type == 1:
            key_url = daily_keys.get('key1_url')
        else:
            key_url = daily_keys.get('key2_url')
        
        if not key_url:
            raise HTTPException(status_code=400, detail="密钥链接未设置")
        
        return JSONResponse({
            "success": True,
            "monetag_url": monetag_url,
            "key_url": key_url
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取密钥链接失败: {e}")
        raise HTTPException(status_code=500, detail="服务器错误")

# ==================== 主函数 ====================
def run_api():
    """运行 FastAPI"""
    port = int(os.getenv("PORT", 8080))
    logger.info(f"🌐 FastAPI 启动中... 端口: {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)

async def run_bot_async():
    """异步运行 Telegram Bot"""
    global bot_application
    
    # 初始化数据库
    init_database()
    
    # 创建应用
    bot_application = Application.builder().token(BOT_TOKEN).build()
    
    # 管理员密钥设置会话处理器
    key_url_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("my", cmd_my)],
        states={
            WAITING_KEY1_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_key_url_input)
            ],
            WAITING_KEY2_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_key_url_input)
            ]
        },
        fallbacks=[
            CommandHandler("admin", cmd_admin),
            CommandHandler("start", cmd_start)
        ],
        allow_reentry=True
    )
    
    # File ID 会话处理器
    photo_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("id", cmd_id),
            CallbackQueryHandler(handle_admin_callback, pattern="^action:get_file_id$")
        ],
        states={
            WAITING_PHOTO: [
                MessageHandler(filters.PHOTO, handle_photo),
                MessageHandler(~filters.PHOTO & ~filters.COMMAND, handle_non_photo),
                CallbackQueryHandler(handle_admin_callback)
            ]
        },
        fallbacks=[
            CallbackQueryHandler(handle_admin_callback, pattern="^action:"),
            CommandHandler("admin", cmd_admin)
        ],
        allow_reentry=True
    )
    
    # 添加处理器
    bot_application.add_handler(CommandHandler("start", cmd_start))
    bot_application.add_handler(CommandHandler("admin", cmd_admin))
    bot_application.add_handler(CommandHandler("jf", cmd_jf))
    bot_application.add_handler(CommandHandler("hd", cmd_hd))
    bot_application.add_handler(key_url_conv_handler)
    bot_application.add_handler(photo_conv_handler)
    bot_application.add_handler(CallbackQueryHandler(handle_user_callback, pattern="^user:"))
    bot_application.add_handler(CallbackQueryHandler(handle_user_callback, pattern="^points:"))
    bot_application.add_handler(CallbackQueryHandler(handle_activity_callback, pattern="^activity:"))
    bot_application.add_handler(CallbackQueryHandler(handle_admin_callback, pattern="^action:"))
    bot_application.add_handler(CallbackQueryHandler(handle_admin_callback, pattern="^view:"))
    bot_application.add_handler(CallbackQueryHandler(handle_admin_callback, pattern="^delete:"))
    bot_application.add_handler(CallbackQueryHandler(handle_admin_callback, pattern="^confirm_del:"))
    bot_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    
    # 设置定时任务
    scheduler = AsyncIOScheduler(timezone=BEIJING_TZ)
    scheduler.add_job(
        generate_daily_keys_job,
        CronTrigger(hour=10, minute=0, second=0, timezone=BEIJING_TZ),
        id='daily_keys_job',
        replace_existing=True
    )
    scheduler.start()
    logger.info("⏰ 定时任务已启动 - 每日北京时间 10:00 生成新密钥")
    
    # 启动时检查是否需要生成今日密钥
    get_or_create_daily_keys()
    
    # 启动轮询
    logger.info("🚀 Telegram Bot 启动中...")
    await bot_application.initialize()
    await bot_application.start()
    await bot_application.updater.start_polling(drop_pending_updates=True)
    
    # 保持运行
    while True:
        await asyncio.sleep(3600)

def main():
    """主入口"""
    # 在单独线程中运行 FastAPI
    api_thread = threading.Thread(target=run_api, daemon=True)
    api_thread.start()
    
    # 在主线程中运行 Bot
    asyncio.run(run_bot_async())

if __name__ == "__main__":
    main()
