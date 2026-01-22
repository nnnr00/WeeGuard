import psycopg2
from psycopg2.extras import RealDictCursor
import logging
from datetime import datetime, timedelta
import pytz
import random
import hashlib
import secrets

logger = logging.getLogger(__name__)

# 北京时区
BEIJING_TZ = pytz.timezone('Asia/Shanghai')


class Database:
    def __init__(self, database_url):
        self.database_url = database_url
    
    def get_connection(self):
        """获取数据库连接"""
        return psycopg2.connect(self.database_url, cursor_factory=RealDictCursor)
    
    def init_tables(self):
        """
        初始化数据库表
        使用 IF NOT EXISTS，不会删除现有数据
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # 创建 file_ids 表（如果不存在）- 原有表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS file_ids (
                    id SERIAL PRIMARY KEY,
                    file_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 创建 users 表 - 存储用户积分
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    points INTEGER DEFAULT 0,
                    first_sign_in BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 创建 sign_ins 表 - 签到记录
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sign_ins (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    sign_date DATE NOT NULL,
                    points_earned INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, sign_date)
                )
            ''')
            
            # 创建 ad_watches 表 - 广告观看记录
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ad_watches (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    watch_date DATE NOT NULL,
                    watch_count INTEGER DEFAULT 0,
                    points_earned INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, watch_date)
                )
            ''')
            
            # 创建 ad_tokens 表 - 广告验证令牌（防作弊）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ad_tokens (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    token TEXT NOT NULL UNIQUE,
                    used BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL
                )
            ''')
            
            conn.commit()
            cursor.close()
            conn.close()
            logger.info("Database tables initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing tables: {e}")
    
    # ==================== File ID 相关方法 ====================
    
    def save_image(self, file_id: str) -> int:
        """
        保存图片 file_id
        返回: 保存的记录ID
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                'INSERT INTO file_ids (file_id) VALUES (%s) RETURNING id',
                (file_id,)
            )
            
            result = cursor.fetchone()
            saved_id = result['id']
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Image saved with ID: {saved_id}")
            return saved_id
        except Exception as e:
            logger.error(f"Error saving image: {e}")
            return 0
    
    def get_all_images(self) -> list:
        """获取所有保存的图片"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                'SELECT id, file_id, created_at FROM file_ids ORDER BY id DESC'
            )
            
            images = cursor.fetchall()
            
            cursor.close()
            conn.close()
            
            return images
        except Exception as e:
            logger.error(f"Error getting images: {e}")
            return []
    
    def get_image_by_id(self, image_id: int) -> dict:
        """根据ID获取图片信息"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                'SELECT id, file_id, created_at FROM file_ids WHERE id = %s',
                (image_id,)
            )
            
            image = cursor.fetchone()
            
            cursor.close()
            conn.close()
            
            return image
        except Exception as e:
            logger.error(f"Error getting image by ID: {e}")
            return None
    
    def delete_image(self, image_id: int) -> bool:
        """删除指定ID的图片"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                'DELETE FROM file_ids WHERE id = %s',
                (image_id,)
            )
            
            deleted = cursor.rowcount > 0
            
            conn.commit()
            cursor.close()
            conn.close()
            
            if deleted:
                logger.info(f"Image {image_id} deleted")
            
            return deleted
        except Exception as e:
            logger.error(f"Error deleting image: {e}")
            return False
    
    # ==================== 用户相关方法 ====================
    
    def get_or_create_user(self, user_id: int, username: str = None) -> dict:
        """获取或创建用户"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # 尝试获取用户
            cursor.execute(
                'SELECT * FROM users WHERE user_id = %s',
                (user_id,)
            )
            user = cursor.fetchone()
            
            if not user:
                # 创建新用户
                cursor.execute(
                    'INSERT INTO users (user_id, username, points) VALUES (%s, %s, 0) RETURNING *',
                    (user_id, username)
                )
                user = cursor.fetchone()
                conn.commit()
            
            cursor.close()
            conn.close()
            
            return dict(user) if user else None
        except Exception as e:
            logger.error(f"Error get_or_create_user: {e}")
            return None
    
    def get_user_points(self, user_id: int) -> int:
        """获取用户积分"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                'SELECT points FROM users WHERE user_id = %s',
                (user_id,)
            )
            result = cursor.fetchone()
            
            cursor.close()
            conn.close()
            
            return result['points'] if result else 0
        except Exception as e:
            logger.error(f"Error getting user points: {e}")
            return 0
    
    def add_user_points(self, user_id: int, points: int) -> bool:
        """增加用户积分"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                '''UPDATE users SET points = points + %s, updated_at = CURRENT_TIMESTAMP 
                   WHERE user_id = %s''',
                (points, user_id)
            )
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return True
        except Exception as e:
            logger.error(f"Error adding user points: {e}")
            return False
    
    # ==================== 签到相关方法 ====================
    
    def get_beijing_today(self):
        """获取北京时间今天的日期"""
        return datetime.now(BEIJING_TZ).date()
    
    def check_signed_today(self, user_id: int) -> bool:
        """检查用户今天是否已签到"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            today = self.get_beijing_today()
            
            cursor.execute(
                'SELECT id FROM sign_ins WHERE user_id = %s AND sign_date = %s',
                (user_id, today)
            )
            result = cursor.fetchone()
            
            cursor.close()
            conn.close()
            
            return result is not None
        except Exception as e:
            logger.error(f"Error checking sign in: {e}")
            return True
    
    def is_first_sign_in(self, user_id: int) -> bool:
        """检查是否是第一次签到"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                'SELECT first_sign_in FROM users WHERE user_id = %s',
                (user_id,)
            )
            result = cursor.fetchone()
            
            cursor.close()
            conn.close()
            
            return not result['first_sign_in'] if result else True
        except Exception as e:
            logger.error(f"Error checking first sign in: {e}")
            return False
    
    def do_sign_in(self, user_id: int) -> tuple:
        """
        执行签到
        返回: (是否成功, 获得积分, 是否第一次签到)
        """
        try:
            if self.check_signed_today(user_id):
                return (False, 0, False)
            
            conn = self.get_connection()
            cursor = conn.cursor()
            
            today = self.get_beijing_today()
            is_first = self.is_first_sign_in(user_id)
            
            # 计算积分：第一次10分，之后随机3-8分
            if is_first:
                points = 10
            else:
                points = random.randint(3, 8)
            
            # 记录签到
            cursor.execute(
                'INSERT INTO sign_ins (user_id, sign_date, points_earned) VALUES (%s, %s, %s)',
                (user_id, today, points)
            )
            
            # 更新用户积分和首次签到状态
            cursor.execute(
                '''UPDATE users SET points = points + %s, first_sign_in = TRUE, 
                   updated_at = CURRENT_TIMESTAMP WHERE user_id = %s''',
                (points, user_id)
            )
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return (True, points, is_first)
        except Exception as e:
            logger.error(f"Error doing sign in: {e}")
            return (False, 0, False)
    
    # ==================== 广告观看相关方法 ====================
    
    def get_ad_watch_count_today(self, user_id: int) -> int:
        """获取用户今天观看广告次数"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            today = self.get_beijing_today()
            
            cursor.execute(
                'SELECT watch_count FROM ad_watches WHERE user_id = %s AND watch_date = %s',
                (user_id, today)
            )
            result = cursor.fetchone()
            
            cursor.close()
            conn.close()
            
            return result['watch_count'] if result else 0
        except Exception as e:
            logger.error(f"Error getting ad watch count: {e}")
            return 0
    
    def can_watch_ad(self, user_id: int) -> bool:
        """检查用户是否还能观看广告"""
        return self.get_ad_watch_count_today(user_id) < 3
    
    def record_ad_watch(self, user_id: int) -> tuple:
        """
        记录广告观看并发放积分
        返回: (是否成功, 获得积分, 当前观看次数)
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            today = self.get_beijing_today()
            
            # 获取当前观看次数
            cursor.execute(
                'SELECT watch_count FROM ad_watches WHERE user_id = %s AND watch_date = %s',
                (user_id, today)
            )
            result = cursor.fetchone()
            
            current_count = result['watch_count'] if result else 0
            
            if current_count >= 3:
                cursor.close()
                conn.close()
                return (False, 0, current_count)
            
            # 计算积分：第一次10分，第二次6分，第三次3-10随机
            if current_count == 0:
                points = 10
            elif current_count == 1:
                points = 6
            else:
                points = random.randint(3, 10)
            
            new_count = current_count + 1
            
            # 更新或插入观看记录
            if result:
                cursor.execute(
                    '''UPDATE ad_watches SET watch_count = %s, points_earned = points_earned + %s,
                       updated_at = CURRENT_TIMESTAMP WHERE user_id = %s AND watch_date = %s''',
                    (new_count, points, user_id, today)
                )
            else:
                cursor.execute(
                    '''INSERT INTO ad_watches (user_id, watch_date, watch_count, points_earned) 
                       VALUES (%s, %s, %s, %s)''',
                    (user_id, today, new_count, points)
                )
            
            # 更新用户积分
            cursor.execute(
                '''UPDATE users SET points = points + %s, updated_at = CURRENT_TIMESTAMP 
                   WHERE user_id = %s''',
                (points, user_id)
            )
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return (True, points, new_count)
        except Exception as e:
            logger.error(f"Error recording ad watch: {e}")
            return (False, 0, 0)
    
    # ==================== 广告令牌相关方法（防作弊）====================
    
    def generate_ad_token(self, user_id: int) -> str:
        """生成广告观看令牌"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # 生成唯一令牌
            token_data = f"{user_id}_{datetime.now().timestamp()}_{secrets.token_hex(16)}"
            token = hashlib.sha256(token_data.encode()).hexdigest()
            
            # 设置5分钟过期
            expires_at = datetime.now() + timedelta(minutes=5)
            
            # 保存令牌
            cursor.execute(
                '''INSERT INTO ad_tokens (user_id, token, expires_at) 
                   VALUES (%s, %s, %s) RETURNING token''',
                (user_id, token, expires_at)
            )
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return token
        except Exception as e:
            logger.error(f"Error generating ad token: {e}")
            return None
    
    def verify_and_use_token(self, token: str) -> tuple:
        """
        验证并使用令牌
        返回: (是否有效, user_id)
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # 查找令牌
            cursor.execute(
                '''SELECT user_id, used, expires_at FROM ad_tokens WHERE token = %s''',
                (token,)
            )
            result = cursor.fetchone()
            
            if not result:
                cursor.close()
                conn.close()
                return (False, None)
            
            # 检查是否已使用
            if result['used']:
                cursor.close()
                conn.close()
                return (False, None)
            
            # 检查是否过期
            if datetime.now() > result['expires_at'].replace(tzinfo=None):
                cursor.close()
                conn.close()
                return (False, None)
            
            # 标记为已使用
            cursor.execute(
                'UPDATE ad_tokens SET used = TRUE WHERE token = %s',
                (token,)
            )
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return (True, result['user_id'])
        except Exception as e:
            logger.error(f"Error verifying token: {e}")
            return (False, None)
    
    def cleanup_expired_tokens(self):
        """清理过期令牌"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                'DELETE FROM ad_tokens WHERE expires_at < CURRENT_TIMESTAMP OR used = TRUE'
            )
            
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            logger.error(f"Error cleaning up tokens: {e}")
