import asyncpg
import os
from datetime import datetime, date
import logging

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

class Database:
    def __init__(self):
        self.pool = None
    
    async def connect(self):
        """连接数据库"""
        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
        await self.create_tables()
        logger.info("数据库连接成功")
    
    async def close(self):
        """关闭连接"""
        if self.pool:
            await self.pool.close()
    
    async def create_tables(self):
        """创建表"""
        async with self.pool.acquire() as conn:
            # 用户表
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username VARCHAR(255),
                    points INTEGER DEFAULT 0,
                    total_earned INTEGER DEFAULT 0,
                    last_checkin DATE,
                    vip_verified BOOLEAN DEFAULT FALSE,
                    vip_attempts INTEGER DEFAULT 0,
                    vip_cooldown TIMESTAMP,
                    wechat_used BOOLEAN DEFAULT FALSE,
                    wechat_attempts INTEGER DEFAULT 0,
                    wechat_cooldown TIMESTAMP,
                    alipay_used BOOLEAN DEFAULT FALSE,
                    alipay_attempts INTEGER DEFAULT 0,
                    alipay_cooldown TIMESTAMP,
                    first_join BOOLEAN DEFAULT FALSE,
                    in_group BOOLEAN DEFAULT FALSE,
                    join_time TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # 兑换记录表
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS redeemed (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    product_id VARCHAR(50),
                    redeemed_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # 积分历史表
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS point_history (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    action_type VARCHAR(20),
                    amount INTEGER,
                    description VARCHAR(255),
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # 商品表
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS products (
                    product_id VARCHAR(50) PRIMARY KEY,
                    name VARCHAR(255),
                    price INTEGER DEFAULT 0,
                    content_type VARCHAR(20),
                    content TEXT,
                    file_id VARCHAR(255),
                    status VARCHAR(10) DEFAULT 'on',
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # 转发命令表
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS commands (
                    id SERIAL PRIMARY KEY,
                    command_name VARCHAR(100) UNIQUE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # 转发链接表
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS command_links (
                    id SERIAL PRIMARY KEY,
                    command_name VARCHAR(100),
                    chat_id BIGINT,
                    message_id INTEGER
                )
            ''')
            
            # 插入默认测试商品
            await conn.execute('''
                INSERT INTO products (product_id, name, price, content_type, content, status)
                VALUES ('TEST001', '🎁 新手测试礼包', 0, 'text', '哈哈，恭喜你成��兑换了测试商品！🎉', 'on')
                ON CONFLICT (product_id) DO NOTHING
            ''')
    
    # ============ 用户操作 ============
    
    async def get_user(self, user_id: int, username: str = None):
        """获取或创建用户"""
        async with self.pool.acquire() as conn:
            user = await conn.fetchrow('SELECT * FROM users WHERE user_id = $1', user_id)
            if not user:
                await conn.execute('''
                    INSERT INTO users (user_id, username) VALUES ($1, $2)
                ''', user_id, username or "用户")
                user = await conn.fetchrow('SELECT * FROM users WHERE user_id = $1', user_id)
            elif username:
                await conn.execute('UPDATE users SET username = $1 WHERE user_id = $2', username, user_id)
            return dict(user)
    
    async def update_user(self, user_id: int, **kwargs):
        """更新用户信息"""
        if not kwargs:
            return
        set_clause = ', '.join([f"{k} = ${i+2}" for i, k in enumerate(kwargs.keys())])
        values = [user_id] + list(kwargs.values())
        async with self.pool.acquire() as conn:
            await conn.execute(f'UPDATE users SET {set_clause} WHERE user_id = $1', *values)
    
    async def add_points(self, user_id: int, amount: int, desc: str):
        """增加积分"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE users SET points = points + $1, total_earned = total_earned + $1 WHERE user_id = $2
            ''', amount, user_id)
            await conn.execute('''
                INSERT INTO point_history (user_id, action_type, amount, description)
                VALUES ($1, 'earn', $2, $3)
            ''', user_id, amount, desc)
    
    async def spend_points(self, user_id: int, amount: int, desc: str):
        """消费积分"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE users SET points = points - $1 WHERE user_id = $2
            ''', amount, user_id)
            await conn.execute('''
                INSERT INTO point_history (user_id, action_type, amount, description)
                VALUES ($1, 'spend', $2, $3)
            ''', user_id, amount, desc)
    
    async def get_history(self, user_id: int, limit: int = 10):
        """获取积分历史"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT * FROM point_history WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2
            ''', user_id, limit)
            return [dict(r) for r in rows]
    
    async def checkin(self, user_id: int):
        """签到"""
        async with self.pool.acquire() as conn:
            user = await conn.fetchrow('SELECT last_checkin FROM users WHERE user_id = $1', user_id)
            today = date.today()
            if user['last_checkin'] == today:
                return None
            import random
            points = random.randint(3, 8)
            await conn.execute('UPDATE users SET last_checkin = $1 WHERE user_id = $2', today, user_id)
            await self.add_points(user_id, points, "每日签到")
            return points
    
    async def get_leaderboard(self, limit: int = 20):
        """获取排行榜"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT user_id, username, total_earned FROM users 
                ORDER BY total_earned DESC LIMIT $1
            ''', limit)
            return [dict(r) for r in rows]
    
    async def get_user_rank(self, user_id: int):
        """获取用户排名"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT COUNT(*) + 1 as rank FROM users 
                WHERE total_earned > (SELECT total_earned FROM users WHERE user_id = $1)
            ''', user_id)
            return row['rank'] if row else 0
    
    async def get_user_count(self):
        """获取用户数量"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT COUNT(*) FROM users')
            return row[0]
    
    async def get_total_points(self):
        """获取积分总额"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT SUM(points), SUM(total_earned) FROM users')
            return row[0] or 0, row[1] or 0
    
    async def get_vip_count(self):
        """获取VIP数量"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT COUNT(*) FROM users WHERE vip_verified = TRUE')
            return row[0]
    
    # ============ 进群/退群 ============
    
    async def user_join_group(self, user_id: int, username: str):
        """用户进群"""
        user = await self.get_user(user_id, username)
        if not user['first_join']:
            await self.add_points(user_id, 20, "首次进群奖励")
            await self.update_user(user_id, first_join=True, in_group=True)
            return True, 20
        else:
            await self.update_user(user_id, in_group=True)
            return False, 0
    
    async def user_leave_group(self, user_id: int):
        """用户退群"""
        async with self.pool.acquire() as conn:
            user = await conn.fetchrow('SELECT points, first_join FROM users WHERE user_id = $1', user_id)
            if user and user['first_join']:
                # 收回20积分
                new_points = max(0, user['points'] - 20)
                await conn.execute('''
                    UPDATE users SET points = $1, in_group = FALSE WHERE user_id = $2
                ''', new_points, user_id)
                await conn.execute('''
                    INSERT INTO point_history (user_id, action_type, amount, description)
                    VALUES ($1, 'spend', 20, '退群收回积分')
                ''', user_id)
                return True
        return False
    
    # ============ 兑换操作 ============
    
    async def is_redeemed(self, user_id: int, product_id: str):
        """检查是否已兑换"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT id FROM redeemed WHERE user_id = $1 AND product_id = $2
            ''', user_id, product_id)
            return row is not None
    
    async def add_redeem(self, user_id: int, product_id: str):
        """添加兑换记录"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO redeemed (user_id, product_id) VALUES ($1, $2)
            ''', user_id, product_id)
    
    async def get_user_redeemed(self, user_id: int):
        """获取用户兑换记录"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('SELECT product_id FROM redeemed WHERE user_id = $1', user_id)
            return [r['product_id'] for r in rows]
    
    # ============ 商品操作 ============
    
    async def get_products(self, status: str = None):
        """获取商品列表"""
        async with self.pool.acquire() as conn:
            if status:
                rows = await conn.fetch('SELECT * FROM products WHERE status = $1', status)
            else:
                rows = await conn.fetch('SELECT * FROM products')
            return {r['product_id']: dict(r) for r in rows}
    
    async def get_product(self, product_id: str):
        """获取单个商品"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT * FROM products WHERE product_id = $1', product_id)
            return dict(row) if row else None
    
    async def add_product(self, product_id: str, name: str, price: int, content_type: str, content: str, file_id: str = None):
        """添加商品"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO products (product_id, name, price, content_type, content, file_id)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (product_id) DO UPDATE SET
                name = $2, price = $3, content_type = $4, content = $5, file_id = $6
            ''', product_id, name, price, content_type, content, file_id)
    
    async def toggle_product(self, product_id: str):
        """切换商品状态"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE products SET status = CASE WHEN status = 'on' THEN 'off' ELSE 'on' END
                WHERE product_id = $1
            ''', product_id)
    
    async def delete_product(self, product_id: str):
        """删除商品"""
        async with self.pool.acquire() as conn:
            await conn.execute('DELETE FROM products WHERE product_id = $1', product_id)
    
    async def get_product_count(self):
        """获取商品数量"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT COUNT(*) FROM products')
            return row[0]
    
    # ============ 命令操作 ============
    
    async def add_command(self, command_name: str, links: list):
        """添加命令"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO commands (command_name) VALUES ($1)
                ON CONFLICT (command_name) DO NOTHING
            ''', command_name)
            await conn.execute('DELETE FROM command_links WHERE command_name = $1', command_name)
            for link in links:
                await conn.execute('''
                    INSERT INTO command_links (command_name, chat_id, message_id)
                    VALUES ($1, $2, $3)
                ''', command_name, link['chat_id'], link['message_id'])
    
    async def get_command(self, command_name: str):
        """获取命令链接"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT chat_id, message_id FROM command_links WHERE command_name = $1
            ''', command_name)
            return [{'chat_id': r['chat_id'], 'message_id': r['message_id']} for r in rows]
    
    async def get_all_commands(self):
        """获取所有命令"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT c.command_name, COUNT(l.id) as link_count
                FROM commands c
                LEFT JOIN command_links l ON c.command_name = l.command_name
                GROUP BY c.command_name
            ''')
            return {r['command_name']: r['link_count'] for r in rows}
    
    async def delete_command(self, command_name: str):
        """删除命令"""
        async with self.pool.acquire() as conn:
            await conn.execute('DELETE FROM command_links WHERE command_name = $1', command_name)
            await conn.execute('DELETE FROM commands WHERE command_name = $1', command_name)
    
    async def command_exists(self, command_name: str):
        """检查命令是否存在"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT command_name FROM commands WHERE command_name = $1', command_name)
            return row is not None


db = Database()
