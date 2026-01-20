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

# 广告页面URL（需要替换为你的实际URL）
AD_PAGE_URL = os.getenv('AD_PAGE_URL', 'https://your-github-pages.github.io/ad-page.html')

# 数据库连接
def get_db_connection():
    """获取数据库连接"""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

# 初始化数据库表（不会删除现有数据）
def init_database():
    """初始化数据库表（如果不存在）"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 创建用户表（如果不存在）
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username VARCHAR(255),
            first_name VARCHAR(255),
            points INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 创建广告观看记录表（如果不存在）
    cur.execute('''
        CREATE TABLE IF NOT EXISTS ad_views (
            id SERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(user_id),
            view_date DATE,
            view_count INTEGER DEFAULT 0,
            points_earned INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, view_date)
        )
    ''')
    
    # 创建验证码表（如果不存在）
    cur.execute('''
        CREATE TABLE IF NOT EXISTS verifications (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            verification_code VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_used BOOLEAN DEFAULT FALSE
        )
    ''')
    
    conn.commit()
    cur.close()
    conn.close()
    logger.info("数据库表初始化完成（保留现有数据）")

# 用户管理函数
def get_or_create_user(user_id: int, username: str = None, first_name: str = None):
    """获取或创建用户"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute('SELECT * FROM users WHERE user_id = %s', (user_id,))
    user = cur.fetchone()
    
    if not user:
        cur.execute(
            'INSERT INTO users (user_id, username, first_name, points) VALUES (%s, %s, %s, 0) RETURNING *',
            (user_id, username, first_name)
        )
        user = cur.fetchone()
        conn.commit()
    
    cur.close()
    conn.close()
    return user

def get_user_points(user_id: int) -> int:
    """获取用户积分"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute('SELECT points FROM users WHERE user_id = %s', (user_id,))
    result = cur.fetchone()
    
    cur.close()
    conn.close()
    return result['points'] if result else 0

def add_points(user_id: int, points: int):
    """添加积分"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute(
        'UPDATE users SET points = points + %s, updated_at = CURRENT_TIMESTAMP WHERE user_id = %s',
        (points, user_id)
    )
    conn.commit()
    
    cur.close()
    conn.close()

def get_today_ad_views(user_id: int) -> int:
    """获取今日观看次数"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    today = datetime.now().date()
    cur.execute(
        'SELECT view_count FROM ad_views WHERE user_id = %s AND view_date = %s',
        (user_id, today)
    )
    result = cur.fetchone()
    
    cur.close()
    conn.close()
    return result['view_count'] if result else 0

def record_ad_view(user_id: int) -> dict:
    """记录广告观看"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    today = datetime.now().date()
    view_count = get_today_ad_views(user_id)
    
    if view_count >= 3:
        cur.close()
        conn.close()
        return {'success': False, 'message': '今日观看次数已达上限'}
    
    # 计算积分
    if view_count == 0:
        points_earned = 10
    elif view_count == 1:
        points_earned = 6
    else:
        points_earned = random.randint(3, 10)
    
    # 插入或更新记录
    cur.execute('''
        INSERT INTO ad_views (user_id, view_date, view_count, points_earned)
        VALUES (%s, %s, 1, %s)
        ON CONFLICT (user_id, view_date)
        DO UPDATE SET 
            view_count = ad_views.view_count + 1,
            points_earned = ad_views.points_earned + %s
    ''', (user_id, today, points_earned, points_earned))
    
    # 添加积分
    add_points(user_id, points_earned)
    
    conn.commit()
    cur.close()
    conn.close()
    
    return {
        'success': True,
        'points_earned': points_earned,
        'view_count': view_count + 1,
        'remaining_views': 2 - view_count
    }

def create_verification_code(user_id: int) -> str:
    """创建验证码"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 生成唯一验证码
    code = f"{user_id}_{random.randint(100000, 999999)}_{int(datetime.now().timestamp())}"
    
    cur.execute(
        'INSERT INTO verifications (user_id, verification_code) VALUES (%s, %s)',
        (user_id, code)
    )
    conn.commit()
    
    cur.close()
    conn.close()
    return code

def verify_code(user_id: int, code: str) -> bool:
    """验证码验证"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 查找未使用且在5分钟内创建的验证码
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
        cur.close()
        conn.close()
        return True
    
    cur.close()
    conn.close()
    return False

# 键盘按钮
def get_main_keyboard():
    """主页键盘"""
    keyboard = [
        [InlineKeyboardButton("🎉 开业活动", callback_data='activity_center')],
        [InlineKeyboardButton("💰 我的积分", callback_data='my_points')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_activity_keyboard():
    """活动中心键盘"""
    keyboard = [
        [InlineKeyboardButton("📺 观看广告获得积分", callback_data='watch_ad')],
        [InlineKeyboardButton("📊 今日观看记录", callback_data='today_stats')],
        [InlineKeyboardButton("🔙 返回首页", callback_data='back_home')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_ad_keyboard(user_id: int, code: str):
    """广告页面键盘"""
    ad_url = f"{AD_PAGE_URL}?user={user_id}&code={code}"
    keyboard = [
        [InlineKeyboardButton("🎬 点击观看广告", url=ad_url)],
        [InlineKeyboardButton("✅ 我已观看完广告", callback_data=f'verify_ad:{code}')],
        [InlineKeyboardButton("🔙 返回活动中心", callback_data='activity_center')]
    ]
    return InlineKeyboardMarkup(keyboard)

# 消息模板
def get_welcome_message(name: str) -> str:
    """欢迎消息"""
    return f"""👋 欢迎回来，{name}！

🎁 这是一个积分奖励机器人
💡 通过观看广告即可获得积分

📌 每日可观看3次广告：
   • 第1次：10积分
   • 第2次：6积分
   • 第3次：3-10积分（随机）

请选择下方功能："""

def get_activity_message() -> str:
    """活动中心消息"""
    return """🎉 活动中心

欢迎参加我们的开业活动！
观看广告即可轻松赚取积分！

请选择您要进行的操作："""

# 命令处理器
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令"""
    user = update.effective_user
    get_or_create_user(user.id, user.username, user.first_name)
    
    name = user.first_name or user.username or "用户"
    await update.message.reply_text(
        get_welcome_message(name),
        reply_markup=get_main_keyboard()
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理按钮回调"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    user_id = user.id
    name = user.first_name or user.username or "用户"
    
    get_or_create_user(user_id, user.username, user.first_name)
    
    data = query.data
    
    # 活动中心
    if data == 'activity_center':
        await query.edit_message_text(
            get_activity_message(),
            reply_markup=get_activity_keyboard()
        )
    
    # 观看广告
    elif data == 'watch_ad':
        today_views = get_today_ad_views(user_id)
        
        if today_views >= 3:
            await query.answer("❌ 今日观看次数已达上限（3次），请明天再来！", show_alert=True)
            return
        
        # 创建验证码
        code = create_verification_code(user_id)
        
        # 计算本次可获得积分
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
    
    # 验证广告观看
    elif data.startswith('verify_ad:'):
        code = data.split(':')[1]
        
        if not verify_code(user_id, code):
            await query.answer("❌ 验证失败！请先观看广告或验证码已过期", show_alert=True)
            return
        
        # 记录观看并发放奖励
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
    
    # 今日统计
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
    
    # 我的积分
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
    
    # 返回首页
    elif data == 'back_home':
        await query.edit_message_text(
            get_welcome_message(name),
            reply_markup=get_main_keyboard()
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理所有其他消息，返回首页"""
    user = update.effective_user
    get_or_create_user(user.id, user.username, user.first_name)
    
    name = user.first_name or user.username or "用户"
    await update.message.reply_text(
        get_welcome_message(name),
        reply_markup=get_main_keyboard()
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """错误处理"""
    logger.error(f"Update {update} caused error {context.error}")

def main():
    """主函数"""
    # 初始化数据库
    init_database()
    
    # 创建应用
    application = Application.builder().token(BOT_TOKEN).build()
    
    # 添加处理器
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # 错误处理
    application.add_error_handler(error_handler)
    
    # 启动机器人
    logger.info("机器人启动成功！")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
