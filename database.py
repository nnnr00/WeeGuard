import psycopg2
from psycopg2.extras import RealDictCursor
import os
from datetime import datetime, date, timedelta
import random
import hashlib
import time as time_module
import string
import pytz

DATABASE_URL = os.getenv("DATABASE_URL")

# 北京时区
BEIJING_TZ = pytz.timezone('Asia/Shanghai')

def get_connection():
    """获取数据库连接"""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    """初始化数据库表（不会清除现有数据）"""
    conn = get_connection()
    cur = conn.cursor()
    
    # 创建 file_ids 表（如果不存在）- 保留原有数据
    cur.execute("""
        CREATE TABLE IF NOT EXISTS file_ids (
            id SERIAL PRIMARY KEY,
            file_id TEXT NOT NULL,
            file_type TEXT DEFAULT 'photo',
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 创建用户表（如果不存在）- 保留原有数据
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            user_id BIGINT UNIQUE NOT NULL,
            username TEXT,
            points INTEGER DEFAULT 0,
            is_verified BOOLEAN DEFAULT FALSE,
            first_checkin BOOLEAN DEFAULT TRUE,
            last_checkin_date DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 添加广告观看相关字段（如果不存在）
    try:
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS ad_watch_count INTEGER DEFAULT 0")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS ad_watch_date DATE")
    except:
        pass
    
    # 添加密钥领取相关字段
    try:
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS key_claim_count INTEGER DEFAULT 0")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS key_claim_date TIMESTAMP")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS claimed_key1 BOOLEAN DEFAULT FALSE")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS claimed_key2 BOOLEAN DEFAULT FALSE")
    except:
        pass
    
    # 创建广告验证令牌表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ad_tokens (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            token TEXT UNIQUE NOT NULL,
            is_used BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            used_at TIMESTAMP,
            ip_address TEXT,
            user_agent TEXT
        )
    """)
    
    # 创建广告观看日志表（防作弊）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ad_watch_logs (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            token TEXT NOT NULL,
            ip_address TEXT,
            user_agent TEXT,
            watch_duration INTEGER,
            is_valid BOOLEAN DEFAULT FALSE,
            points_earned INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 创建每日密钥表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_keys (
            id SERIAL PRIMARY KEY,
            key_date DATE NOT NULL,
            key1 TEXT NOT NULL,
            key2 TEXT NOT NULL,
            key1_link TEXT,
            key2_link TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 创建密钥领取记录表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS key_claim_logs (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            key_type TEXT NOT NULL,
            key_value TEXT NOT NULL,
            points_earned INTEGER NOT NULL,
            claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    cur.close()
    conn.close()
    print("✅ 数据库初始化完成（保留原有数据）")

def save_file_id(file_id: str, file_type: str = "photo", description: str = None) -> int:
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
    """获取所有 File ID 记录"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM file_ids ORDER BY created_at DESC")
    records = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return records

def delete_file_id(record_id: int) -> bool:
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

def get_or_create_user(user_id: int, username: str = None):
    """获取或创建用户"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
    user = cur.fetchone()
    
    if not user:
        cur.execute(
            "INSERT INTO users (user_id, username, points, first_checkin, ad_watch_count, ad_watch_date, key_claim_count) VALUES (%s, %s, 0, TRUE, 0, NULL, 0) RETURNING *",
            (user_id, username)
        )
        user = cur.fetchone()
        conn.commit()
    
    cur.close()
    conn.close()
    
    return user

def get_user_points(user_id: int) -> int:
    """获取用户积分"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT points FROM users WHERE user_id = %s", (user_id,))
    result = cur.fetchone()
    
    cur.close()
    conn.close()
    
    if result:
        return result['points']
    return 0

def update_user_points(user_id: int, points: int):
    """更新用户积分"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute(
        "UPDATE users SET points = points + %s, updated_at = CURRENT_TIMESTAMP WHERE user_id = %s",
        (points, user_id)
    )
    
    conn.commit()
    cur.close()
    conn.close()

def check_and_do_checkin(user_id: int, username: str = None):
    """检查并执行签到"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
    user = cur.fetchone()
    
    if not user:
        cur.execute(
            "INSERT INTO users (user_id, username, points, first_checkin, last_checkin_date, ad_watch_count, ad_watch_date, key_claim_count) VALUES (%s, %s, 0, TRUE, NULL, 0, NULL, 0) RETURNING *",
            (user_id, username)
        )
        user = cur.fetchone()
        conn.commit()
    
    today = date.today()
    last_checkin = user['last_checkin_date']
    
    if last_checkin and last_checkin == today:
        cur.close()
        conn.close()
        return False, 0, "您今天已经签到过了，明天再来吧！", False
    
    is_first = user['first_checkin']
    
    if is_first:
        points_earned = 10
        cur.execute(
            """UPDATE users 
               SET points = points + %s, 
                   first_checkin = FALSE, 
                   last_checkin_date = %s,
                   updated_at = CURRENT_TIMESTAMP 
               WHERE user_id = %s""",
            (points_earned, today, user_id)
        )
    else:
        points_earned = random.randint(3, 8)
        cur.execute(
            """UPDATE users 
               SET points = points + %s, 
                   last_checkin_date = %s,
                   updated_at = CURRENT_TIMESTAMP 
               WHERE user_id = %s""",
            (points_earned, today, user_id)
        )
    
    conn.commit()
    cur.close()
    conn.close()
    
    return True, points_earned, "签到成功！", is_first

def get_user_info(user_id: int):
    """获取用户完整信息"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
    user = cur.fetchone()
    
    cur.close()
    conn.close()
    
    return user

def get_beijing_datetime():
    """获取北京时间"""
    return datetime.now(BEIJING_TZ)

def get_beijing_date():
    """获取北京时间日期"""
    return get_beijing_datetime().date()

def get_current_key_period_start():
    """获取当前密钥周期的开始时间（每天北京时间10:00开始）"""
    now = get_beijing_datetime()
    today_10am = now.replace(hour=10, minute=0, second=0, microsecond=0)
    
    if now >= today_10am:
        return today_10am
    else:
        return today_10am - timedelta(days=1)

def get_next_key_reset_time():
    """获取下次密钥重置时间"""
    now = get_beijing_datetime()
    today_10am = now.replace(hour=10, minute=0, second=0, microsecond=0)
    
    if now >= today_10am:
        return today_10am + timedelta(days=1)
    else:
        return today_10am

def is_after_10am_beijing():
    """检查是否在北京时间10点之后"""
    now = get_beijing_datetime()
    return now.hour >= 10

def get_ad_watch_count(user_id: int) -> int:
    """获取用户今日广告观看次数"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT ad_watch_count, ad_watch_date FROM users WHERE user_id = %s", (user_id,))
    result = cur.fetchone()
    
    cur.close()
    conn.close()
    
    if result:
        today = get_beijing_date()
        if result['ad_watch_date'] and result['ad_watch_date'] == today:
            return result['ad_watch_count']
        else:
            return 0
    return 0

def generate_ad_token(user_id: int) -> str:
    """生成广告验证令牌"""
    conn = get_connection()
    cur = conn.cursor()
    
    raw_token = f"{user_id}_{time_module.time()}_{random.randint(100000, 999999)}"
    token = hashlib.sha256(raw_token.encode()).hexdigest()[:32]
    
    cur.execute(
        "INSERT INTO ad_tokens (user_id, token) VALUES (%s, %s)",
        (user_id, token)
    )
    
    conn.commit()
    cur.close()
    conn.close()
    
    return token

def verify_ad_token(token: str, ip_address: str = None, user_agent: str = None):
    """验证广告令牌并发放积分"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM ad_tokens WHERE token = %s", (token,))
    token_record = cur.fetchone()
    
    if not token_record:
        cur.close()
        conn.close()
        return False, 0, "无效的验证令牌"
    
    if token_record['is_used']:
        cur.close()
        conn.close()
        return False, 0, "该令牌已被使用"
    
    token_age = (datetime.now() - token_record['created_at']).total_seconds()
    if token_age > 300:
        cur.close()
        conn.close()
        return False, 0, "验证令牌已过期"
    
    user_id = token_record['user_id']
    today = get_beijing_date()
    
    cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
    user = cur.fetchone()
    
    if not user:
        cur.close()
        conn.close()
        return False, 0, "用户不存在"
    
    current_count = 0
    if user['ad_watch_date'] and user['ad_watch_date'] == today:
        current_count = user['ad_watch_count'] or 0
    
    if current_count >= 3:
        cur.close()
        conn.close()
        return False, 0, "今日观看次数已达上限"
    
    new_count = current_count + 1
    if new_count == 1:
        points = 10
    elif new_count == 2:
        points = 6
    else:
        points = random.randint(3, 10)
    
    cur.execute(
        """UPDATE users 
           SET points = points + %s, 
               ad_watch_count = %s, 
               ad_watch_date = %s,
               updated_at = CURRENT_TIMESTAMP 
           WHERE user_id = %s""",
        (points, new_count, today, user_id)
    )
    
    cur.execute(
        """UPDATE ad_tokens 
           SET is_used = TRUE, 
               used_at = CURRENT_TIMESTAMP,
               ip_address = %s,
               user_agent = %s 
           WHERE token = %s""",
        (ip_address, user_agent, token)
    )
    
    cur.execute(
        """INSERT INTO ad_watch_logs 
           (user_id, token, ip_address, user_agent, is_valid, points_earned) 
           VALUES (%s, %s, %s, %s, TRUE, %s)""",
        (user_id, token, ip_address, user_agent, points)
    )
    
    conn.commit()
    cur.close()
    conn.close()
    
    return True, points, f"观看成功！获得 {points} 积分"

def get_token_user_id(token: str):
    """根据令牌获取用户ID"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT user_id FROM ad_tokens WHERE token = %s", (token,))
    result = cur.fetchone()
    
    cur.close()
    conn.close()
    
    if result:
        return result['user_id']
    return None

def check_duplicate_ip(user_id: int, ip_address: str) -> bool:
    """检查是否有重复IP作弊"""
    conn = get_connection()
    cur = conn.cursor()
    
    today = get_beijing_date()
    
    cur.execute(
        """SELECT COUNT(DISTINCT user_id) as user_count 
           FROM ad_watch_logs 
           WHERE ip_address = %s 
           AND DATE(created_at) = %s 
           AND is_valid = TRUE""",
        (ip_address, today)
    )
    result = cur.fetchone()
    
    cur.close()
    conn.close()
    
    if result and result['user_count'] >= 3:
        return True
    
    return False

def generate_random_key(length: int = 12) -> str:
    """生成随机密钥（大小写字母和数字）"""
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

def get_today_keys():
    """获取今日密钥（基于10点周期）"""
    conn = get_connection()
    cur = conn.cursor()
    
    period_start = get_current_key_period_start()
    
    cur.execute(
        """SELECT * FROM daily_keys 
           WHERE created_at >= %s AND is_active = TRUE 
           ORDER BY created_at DESC LIMIT 1""",
        (period_start,)
    )
    result = cur.fetchone()
    
    cur.close()
    conn.close()
    
    return result

def create_new_daily_keys():
    """创建新的每日密钥"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("UPDATE daily_keys SET is_active = FALSE WHERE is_active = TRUE")
    
    key1 = generate_random_key(12)
    key2 = generate_random_key(12)
    today = get_beijing_date()
    
    cur.execute(
        """INSERT INTO daily_keys (key_date, key1, key2, is_active) 
           VALUES (%s, %s, %s, TRUE) RETURNING *""",
        (today, key1, key2)
    )
    
    result = cur.fetchone()
    
    cur.execute(
        """UPDATE users 
           SET key_claim_count = 0, 
               claimed_key1 = FALSE, 
               claimed_key2 = FALSE,
               key_claim_date = NULL"""
    )
    
    conn.commit()
    cur.close()
    conn.close()
    
    return result

def update_key_link(key_type: str, link: str):
    """更新密钥链接"""
    conn = get_connection()
    cur = conn.cursor()
    
    if key_type == "key1":
        cur.execute(
            """UPDATE daily_keys 
               SET key1_link = %s, updated_at = CURRENT_TIMESTAMP 
               WHERE is_active = TRUE""",
            (link,)
        )
    elif key_type == "key2":
        cur.execute(
            """UPDATE daily_keys 
               SET key2_link = %s, updated_at = CURRENT_TIMESTAMP 
               WHERE is_active = TRUE""",
            (link,)
        )
    
    conn.commit()
    cur.close()
    conn.close()

def get_key_links():
    """获取当前密钥链接"""
    keys = get_today_keys()
    if keys:
        return keys.get('key1_link'), keys.get('key2_link')
    return None, None

def get_user_key_claim_count(user_id: int) -> int:
    """获取用户今日密钥领取次数（基于10点周期）"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute(
        "SELECT key_claim_count, key_claim_date, claimed_key1, claimed_key2 FROM users WHERE user_id = %s",
        (user_id,)
    )
    result = cur.fetchone()
    
    cur.close()
    conn.close()
    
    if result:
        period_start = get_current_key_period_start()
        claim_date = result['key_claim_date']
        
        if claim_date and claim_date >= period_start.replace(tzinfo=None):
            return result['key_claim_count'] or 0
        else:
            return 0
    return 0

def check_user_claimed_key(user_id: int, key_type: str) -> bool:
    """检查用户是否已领取某个密钥"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute(
        "SELECT claimed_key1, claimed_key2, key_claim_date FROM users WHERE user_id = %s",
        (user_id,)
    )
    result = cur.fetchone()
    
    cur.close()
    conn.close()
    
    if result:
        period_start = get_current_key_period_start()
        claim_date = result['key_claim_date']
        
        if claim_date and claim_date >= period_start.replace(tzinfo=None):
            if key_type == "key1":
                return result['claimed_key1'] or False
            elif key_type == "key2":
                return result['claimed_key2'] or False
        else:
            return False
    return False

def claim_key(user_id: int, key_value: str, username: str = None):
    """领取密钥积分"""
    conn = get_connection()
    cur = conn.cursor()
    
    keys = get_today_keys()
    
    if not keys:
        cur.close()
        conn.close()
        return False, 0, "今日密钥尚未生成，请稍后再试", None
    
    key_type = None
    points = 0
    
    if key_value == keys['key1']:
        key_type = "key1"
        points = 8
    elif key_value == keys['key2']:
        key_type = "key2"
        points = 6
    else:
        cur.close()
        conn.close()
        return False, 0, "❌ 密钥无效，请检查是否输入正确", None
    
    cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
    user = cur.fetchone()
    
    if not user:
        cur.execute(
            "INSERT INTO users (user_id, username, points, first_checkin, key_claim_count) VALUES (%s, %s, 0, TRUE, 0) RETURNING *",
            (user_id, username)
        )
        user = cur.fetchone()
        conn.commit()
    
    period_start = get_current_key_period_start()
    claim_date = user.get('key_claim_date')
    
    in_current_period = claim_date and claim_date >= period_start.replace(tzinfo=None)
    
    if in_current_period:
        if key_type == "key1" and user.get('claimed_key1'):
            cur.close()
            conn.close()
            return False, 0, "⚠️ 您已领取过密钥一的积分，请勿重复领取", key_type
        elif key_type == "key2" and user.get('claimed_key2'):
            cur.close()
            conn.close()
            return False, 0, "⚠️ 您已领取过密钥二的积分，请勿重复领取", key_type
    
    now = datetime.now()
    
    if key_type == "key1":
        cur.execute(
            """UPDATE users 
               SET points = points + %s, 
                   key_claim_count = CASE WHEN key_claim_date >= %s THEN key_claim_count + 1 ELSE 1 END,
                   claimed_key1 = TRUE,
                   key_claim_date = %s,
                   updated_at = CURRENT_TIMESTAMP 
               WHERE user_id = %s""",
            (points, period_start.replace(tzinfo=None), now, user_id)
        )
    else:
        cur.execute(
            """UPDATE users 
               SET points = points + %s, 
                   key_claim_count = CASE WHEN key_claim_date >= %s THEN key_claim_count + 1 ELSE 1 END,
                   claimed_key2 = TRUE,
                   key_claim_date = %s,
                   updated_at = CURRENT_TIMESTAMP 
               WHERE user_id = %s""",
            (points, period_start.replace(tzinfo=None), now, user_id)
        )
    
    cur.execute(
        """INSERT INTO key_claim_logs (user_id, key_type, key_value, points_earned) 
           VALUES (%s, %s, %s, %s)""",
        (user_id, key_type, key_value, points)
    )
    
    conn.commit()
    cur.close()
    conn.close()
    
    key_name = "密钥一" if key_type == "key1" else "密钥二"
    return True, points, f"🎉 恭喜！{key_name}验证成功，获得 +{points} 积分！", key_type

def check_keys_ready():
    """检查密钥链接是否已设置"""
    keys = get_today_keys()
    if not keys:
        return False, "密钥未生成"
    
    if not keys.get('key1_link') or not keys.get('key2_link'):
        return False, "密钥链接未设置"
    
    return True, "密钥已就绪"
