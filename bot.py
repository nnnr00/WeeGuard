import os
import logging
from datetime import datetime, timedelta
import random
import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 环境变量
BOT_TOKEN = os.getenv('BOT_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
AD_PAGE_URL = os.getenv('AD_PAGE_URL', 'https://your-github-pages.github.io/ad-page.html')

# 数据库连接
def get_db_connection():
    """获取数据库连接"""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def table_exists(cur, table_name):
    """检查表是否存在"""
    cur.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name = %s
        );
    """, (table_name,))
    return cur.fetchone()['exists']

def column_exists(cur, table_name, column_name):
    """检查列是否存在"""
    cur.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.columns 
            WHERE table_schema = 'public' 
            AND table_name = %s 
            AND column_name = %s
        );
    """, (table_name, column_name))
    return cur.fetchone()['exists']

def init_database():
    """智能初始化数据库（完全保护现有数据）"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        logger.info("🔍 开始检查数据库结构...")
        
        # ==================== USERS 表 ====================
        if not table_exists(cur, 'users'):
            logger.info("📝 创建 users 表...")
            cur.execute('''
                CREATE TABLE users (
                    user_id BIGINT PRIMARY KEY,
                    username VARCHAR(255),
                    first_name VARCHAR(255),
                    points INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            logger.info("✅ users 表创建成功")
        else:
            logger.info("✅ users 表已存在，保持原有数据")
            
            # 检查并添加缺失的列（不影响现有数据）
            if not column_exists(cur, 'users', 'username'):
                cur.execute('ALTER TABLE users ADD COLUMN username VARCHAR(255)')
                conn.commit()
                logger.info("➕ 添加 username 列")
            
            if not column_exists(cur, 'users', 'first_name'):
                cur.execute('ALTER TABLE users ADD COLUMN first_name VARCHAR(255)')
                conn.commit()
                logger.info("➕ 添加 first_name 列")
            
            if not column_exists(cur, 'users', 'points'):
                cur.execute('ALTER TABLE users ADD COLUMN points INTEGER DEFAULT 0')
                conn.commit()
                logger.info("➕ 添加 points 列")
            
            if not column_exists(cur, 'users', 'created_at'):
                cur.execute('ALTER TABLE users ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
                conn.commit()
                logger.info("➕ 添加 created_at 列")
            
            if not column_exists(cur, 'users', 'updated_at'):
                cur.execute('ALTER TABLE users ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
                conn.commit()
                logger.info("➕ 添加 updated_at 列")
        
        # ==================== AD_VIEWS 表 ====================
        if not table_exists(cur, 'ad_views'):
            logger.info("📝 创建 ad_views 表...")
            cur.execute('''
                CREATE TABLE ad_views (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    view_date DATE NOT NULL,
                    view_count INTEGER DEFAULT 0,
                    points_earned INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, view_date)
                )
            ''')
            conn.commit()
            logger.info("✅ ad_views 表创建成功")
        else:
            logger.info("✅ ad_views 表已存在，保持原有数据")
            
            # 检查并添加缺失的列
            if not column_exists(cur, 'ad_views', 'view_count'):
                cur.execute('ALTER TABLE ad_views ADD COLUMN view_count INTEGER DEFAULT 0')
                conn.commit()
                logger.info("➕ 添加 view_count 列")
            
            if not column_exists(cur, 'ad_views', 'points_earned'):
                cur.execute('ALTER TABLE ad_views ADD COLUMN points_earned INTEGER DEFAULT 0')
                conn.commit()
                logger.info("➕ 添加 points_earned 列")
            
            if not column_exists(cur, 'ad_views', 'created_at'):
                cur.execute('ALTER TABLE ad_views ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
                conn.commit()
                logger.info("➕ 添加 created_at 列")
        
        # ==================== VERIFICATIONS 表 ====================
        if not table_exists(cur, 'verifications'):
            logger.info("📝 创建 verifications 表...")
            cur.execute('''
                CREATE TABLE verifications (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    verification_code VARCHAR(100) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_used BOOLEAN DEFAULT FALSE
                )
            ''')
            conn.commit()
            logger.info("✅ verifications 表创建成功")
        else:
            logger.info("✅ verifications 表已存在，保持原有数据")
            
            # 检查并添加缺失的列
            if not column_exists(cur, 'verifications', 'is_used'):
                cur.execute('ALTER TABLE verifications ADD COLUMN is_used BOOLEAN DEFAULT FALSE')
                conn.commit()
                logger.info("➕ 添加 is_used 列")
        
        # ==================== 创建索引（不影响数据）====================
        logger.info("🔧 优化索引...")
        
        indexes = [
            ('idx_ad_views_user_date', 'ad_views', '(user_id, view_date)'),
            ('idx_verifications_user', 'verifications', '(user_id)'),
            ('idx_verifications_code', 'verifications', '(verification_code)'),
        ]
        
        for idx_name, table_name, columns in indexes:
            try:
                cur.execute(f'CREATE INDEX IF NOT EXISTS {idx_name} ON {table_name} {columns}')
                conn.commit()
            except Exception as e:
                logger.warning(f"索引 {idx_name} 跳过: {e}")
                conn.rollback()
        
        # ==================== 统计现有数据 ====================
        cur.execute('SELECT COUNT(*) as count FROM users')
        user_count = cur.fetchone()['count']
        
        cur.execute('SELECT COUNT(*) as count FROM ad_views')
        ad_count = cur.fetchone()['count']
        
        logger.info(f"""
╔══════════════════════════════════════╗
║   🎉 数据库初始化完成                ║
╠══════════════════════════════════════╣
║   📊 现有用户数: {user_count:<20} ║
║   📺 广告观看记录: {ad_count:<18} ║
║   ✅ 所有数据完整保留                ║
╚══════════════════════════════════════╝
        """)
        
    except Exception as e:
        logger.error(f"❌ 数据库初始化错误: {e}")
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

# ==================== 数据库操作函数 ====================

def get_or_create_user(user_id: int, username: str = None, first_name: str = None):
    """获取或创建用户"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute('SELECT * FROM users WHERE user_id = %s', (user_id,))
        user = cur.fetchone()
        
        if not user:
            cur.execute(
                'INSERT INTO users (user_id, username, first_name, points) VALUES (%s, %s, %s, 0) RETURNING *',
                (user_id, username, first_name)
            )
            user = cur.fetchone()
            conn.commit()
            logger.info(f"新用户注册: {user_id} ({first_name})")
        
        return user
    except Exception as e:
        logger.error(f"获取用户失败: {e}")
        conn.rollback()
        return None
    finally:
        cur.close()
        conn.close()

def get_user_points(user_id: int) -> int:
    """获取用户积分"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute('SELECT points FROM users WHERE user_id = %s', (user_id,))
        result = cur.fetchone()
        return result['points'] if result else 0
    except Exception as e:
        logger.error(f"获取积分失败: {e}")
        return 0
    finally:
        cur.close()
        conn.close()

def add_points(user_id: int, points: int):
    """添加积分"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute(
            'UPDATE users SET points = points + %s, updated_at = CURRENT_TIMESTAMP WHERE user_id = %s',
            (points, user_id)
        )
        conn.commit()
        logger.info(f"用户 {user_id} 获得 {points} 积分")
    except Exception as e:
        logger.error(f"添加积分失败: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

def get_today_ad_views(user_id: int) -> int:
    """获取今日观看次数"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        today = datetime.now().date()
        cur.execute(
            'SELECT view_count FROM ad_views WHERE user_id = %s AND view_date = %s',
            (user_id, today)
        )
        result = cur.fetchone()
        return result['view_count'] if result else 0
    except Exception as e:
        logger.error(f"获取观看次数失败: {e}")
        return 0
    finally:
        cur.close()
        conn.close()

def record_ad_view(user_id: int) -> dict:
    """记录广告观看"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        today = datetime.now().date()
        view_count = get_today_ad_views(user_id)
        
        if view_count >= 3:
            return {'success': False, 'message': '今日观看次数已达上限'}
        
        # 计算积分
        if view_count == 0:
            points_earned = 10
        elif view_count == 1:
            points_earned = 6
        else:
            points_earned = random.randint(3, 10)
        
        # 使用 PostgreSQL 的 UPSERT
        cur.execute('''
            INSERT INTO ad_views (user_id, view_date, view_count, points_earned)
            VALUES (%s, %s, 1, %s)
            ON CONFLICT (user_id, view_date)
            DO UPDATE SET 
                view_count = ad_views.view_count + 1,
                points_earned = ad_views.points_earned + %s
        ''', (user_id, today, points_earned, points_earned))
        
        conn.commit()
        
        # 添加积分到用户账户
        add_points(user_id, points_earned)
        
        logger.info(f"✅ 用户 {user_id} 第 {view_count + 1} 次观看，获得 {points_earned} 积分")
        
        return {
            'success': True,
            'points_earned': points_earned,
            'view_count': view_count + 1,
            'remaining_views': 2 - view_count
        }
    except Exception as e:
        logger.error(f"记录观看失败: {e}")
        conn.rollback()
        return {'success': False, 'message': '系统错误，请稍后重试'}
    finally:
        cur.close()
        conn.close()

def create_verification_code(user_id: int) -> str:
    """创建验证码"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        code = f"{user_id}_{random.randint(100000, 999999)}_{int(datetime.now().timestamp())}"
        
        cur.execute(
            'INSERT INTO verifications (user_id, verification_code, is_used) VALUES (%s, %s, FALSE)',
            (user_id, code)
        )
        conn.commit()
        
        logger.info(f"为用户 {user_id} 创建验证码")
        return code
    except Exception as e:
        logger.error(f"创建验证码失败: {e}")
        conn.rollback()
        return None
    finally:
        cur.close()
        conn.close()

def verify_code(user_id: int, code: str) -> bool:
    """验证码验证"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # 查找有效的验证码
        cur.execute('''
            SELECT * FROM verifications 
            WHERE user_id = %s 
            AND verification_code = %s 
            AND is_used = FALSE 
            AND created_at > NOW() - INTERVAL '5 minutes'
            ORDER BY created_at DESC
            LIMIT 1
        ''', (user_id, code))
        
        result = cur.fetchone()
        
        if result:
            # 标记为已使用
            cur.execute(
                'UPDATE verifications SET is_used = TRUE WHERE id = %s',
                (result['id'],)
            )
            conn.commit()
            logger.info(f"✅ 用户 {user_id} 验证成功")
            return True
        
        logger.warning(f"❌ 用户 {user_id} 验证失败（无效或过期）")
        return False
    except Exception as e:
        logger.error(f"验证码验证失败: {e}")
        conn.rollback()
        return False
    finally:
        cur.close()
        conn.close()

# ==================== Telegram 机器人逻辑 ====================

def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("🎉 开业活动", callback_data='activity_center')],
        [InlineKeyboardButton("💰 我的积分", callback_data='my_points')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_activity_keyboard():
    keyboard = [
        [InlineKeyboardButton("📺 观看广告获得积分", callback_data='watch_ad')],
        [InlineKeyboardButton("📊 今日观看记录", callback_data='today_stats')],
        [InlineKeyboardButton("🔙 返回首页", callback_data='back_home')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_ad_keyboard(user_id: int, code: str):
    ad_url = f"{AD_PAGE_URL}?user={user_id}&code={code}"
    keyboard = [
        [InlineKeyboardButton("🎬 点击观看广告", url=ad_url)],
        [InlineKeyboardButton("✅ 我已观看完广告", callback_data=f'verify_ad:{code}')],
        [InlineKeyboardButton("🔙 返回活动中心", callback_data='activity_center')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_welcome_message(name: str) -> str:
    return f"""👋 欢迎回来，{name}！

🎁 这是一个积分奖励机器人
💡 通过观看广告即可获得积分

📌 每日可观看3次广告：
   • 第1次：10积分
   • 第2次：6积分
   • 第3次：3-10积分（随机）

请选择下方功能："""

def get_activity_message() -> str:
    return """🎉 活动中心

欢迎参加我们的开业活动！
观看广告即可轻松赚取积分！

请选择您要进行的操作："""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_or_create_user(user.id, user.username, user.first_name)
    
    name = user.first_name or user.username or "用户"
    await update.message.reply_text(
        get_welcome_message(name),
        reply_markup=get_main_keyboard()
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    user_id = user.id
    name = user.first_name or user.username or "用户"
    
    get_or_create_user(user_id, user.username, user.first_name)
    
    data = query.data
    
    if data == 'activity_center':
        await query.edit_message_text(
            get_activity_message(),
            reply_markup=get_activity_keyboard()
        )
    
    elif data == 'watch_ad':
        today_views = get_today_ad_views(user_id)
        
        if today_views >= 3:
            await query.answer("❌ 今日观看次数已达上限（3次），请明天再来！", show_alert=True)
            return
        
        code = create_verification_code(user_id)
        
        if not code:
            await query.answer("❌ 系统错误，请稍后重试", show_alert=True)
            return
        
        if today_views == 0:
            next_points = "10"
        elif today_views == 1:
            next_points = "6"
        else:
            next_points = "3-10（随机）"
        
        message = f"""📺 观看广告

今日已观看：{today_views}/3 次
本次可获得：{next_points} 积分

🔗 请点击下方按钮打开广告页面
⚠️ 观看完整广告后，返回点击"我已观看完广告"按钮验证"""
        
        await query.edit_message_text(
            message,
            reply_markup=get_ad_keyboard(user_id, code)
        )
    
    elif data.startswith('verify_ad:'):
        code = data.split(':', 1)[1]
        
        if not verify_code(user_id, code):
            await query.answer("❌ 验证失败！请先观看广告或验证码已过期", show_alert=True)
            return
        
        result = record_ad_view(user_id)
        
        if result['success']:
            current_points = get_user_points(user_id)
            message = f"""🎉 恭喜！观看成功

✅ 获得积分：+{result['points_earned']}
💰 当前积分：{current_points}
📊 今日已观看：{result['view_count']}/3 次
🔄 剩余次数：{result['remaining_views']} 次"""
            
            await query.edit_message_text(
                message,
                reply_markup=get_activity_keyboard()
            )
            await query.answer(f"🎉 获得 {result['points_earned']} 积分！")
        else:
            await query.answer(result['message'], show_alert=True)
    
    elif data == 'today_stats':
        views = get_today_ad_views(user_id)
        points = get_user_points(user_id)
        
        message = f"""📊 今日数据

👀 今日观看：{views}/3 次
💰 当前积分：{points}
🔄 剩余次数：{3 - views} 次

{'💡 继续观看广告赚取更多积分！' if views < 3 else '✅ 今日次数已用完，明天再来吧！'}"""
        
        await query.edit_message_text(
            message,
            reply_markup=get_activity_keyboard()
        )
    
    elif data == 'my_points':
        points = get_user_points(user_id)
        views = get_today_ad_views(user_id)
        
        message = f"""💰 我的积分

当前积分：{points}
今日观看：{views}/3 次

💡 积分可用于兑换奖励（功能开发中）"""
        
        await query.edit_message_text(
            message,
            reply_markup=get_main_keyboard()
        )
    
    elif data == 'back_home':
        await query.edit_message_text(
            get_welcome_message(name),
            reply_markup=get_main_keyboard()
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_or_create_user(user.id, user.username, user.first_name)
    
    name = user.first_name or user.username or "用户"
    await update.message.reply_text(
        get_welcome_message(name),
        reply_markup=get_main_keyboard()
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")

def main():
    try:
        logger.info("=" * 50)
        logger.info("🚀 Telegram 广告积分机器人启动中...")
        logger.info("=" * 50)
        
        # 初始化数据库
        init_database()
        
        # 创建应用
        logger.info("📱 创建 Telegram 应用...")
        application = Application.builder().token(BOT_TOKEN).build()
        
        # 添加处理器
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CallbackQueryHandler(button_callback))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        application.add_error_handler(error_handler)
        
        # 启动机器人
        logger.info("✅ 机器人启动成功！等待用户消息...")
        logger.info("=" * 50)
        application.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        logger.error(f"❌ 启动失败: {e}")
        import traceback
        traceback.print_exc()
        raise

if __name__ == '__main__':
    main()
