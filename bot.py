import os
import logging
import asyncio
import re
import random
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)
from telegram.error import TelegramError
import psycopg2
from psycopg2.extras import Json, RealDictCursor
import json

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 从环境变量获取配置
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))
DATABASE_URL = os.getenv('DATABASE_URL')
GROUP_INVITE_LINK = os.getenv('GROUP_INVITE_LINK', 'https://t.me/your_group')

# 内存缓存
user_locks = {}
forward_library = {}
temp_commands = {}
delete_tasks = {}
file_id_storage = {
    'PAYMENT_IMAGE': '',
    'TUTORIAL_IMAGE': '',
    'WECHAT_PAY_IMAGE': '',
    'WECHAT_TUTORIAL_IMAGE': '',
    'ALIPAY_PAY_IMAGE': '',
    'ALIPAY_TUTORIAL_IMAGE': ''
}
user_points = {}
signin_records = {}
recharge_records = {}
recharge_locks = {}
waiting_recharge_order = {}
products = {
    'test_product': {
        'name': '测试商品',
        'points': 0,
        'content': {
            'type': 'text',
            'data': '哈哈'
        }
    }
}
user_exchanges = {}
temp_products = {}
points_history = {}

# ============== 数据库连接函数 ==============

def get_db_connection():
    """获取数据库连接"""
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        return conn
    except Exception as e:
        logger.error(f"数据库连接失败: {e}")
        return None

def init_database():
    """初始化数据库表"""
    conn = get_db_connection()
    if not conn:
        return
    
    try:
        cur = conn.cursor()
        
        # 创建用户积分表
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_points (
                user_id BIGINT PRIMARY KEY,
                points INTEGER DEFAULT 0,
                last_signin DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 创建充值记录表
        cur.execute('''
            CREATE TABLE IF NOT EXISTS recharge_records (
                user_id BIGINT PRIMARY KEY,
                wechat_used BOOLEAN DEFAULT FALSE,
                alipay_used BOOLEAN DEFAULT FALSE,
                wechat_locked_until TIMESTAMP,
                alipay_locked_until TIMESTAMP,
                wechat_fail_count INTEGER DEFAULT 0,
                alipay_fail_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 创建商品表
        cur.execute('''
            CREATE TABLE IF NOT EXISTS products (
                product_id VARCHAR(100) PRIMARY KEY,
                name VARCHAR(200),
                points INTEGER,
                content_type VARCHAR(50),
                content_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 创建用户兑换记录表
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_exchanges (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                product_id VARCHAR(100),
                exchanged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, product_id)
            )
        ''')
        
        # 创建积分历史表
        cur.execute('''
            CREATE TABLE IF NOT EXISTS points_history (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                points_type VARCHAR(20),
                points INTEGER,
                description VARCHAR(200),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 创建转发库表
        cur.execute('''
            CREATE TABLE IF NOT EXISTS forward_library (
                command VARCHAR(100) PRIMARY KEY,
                chat_id VARCHAR(100),
                message_ids TEXT,
                created_by BIGINT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 创建用户锁定表
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_locks (
                user_id BIGINT PRIMARY KEY,
                locked_until TIMESTAMP,
                fail_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        logger.info("✅ 数据库表初始化成功")
        
    except Exception as e:
        logger.error(f"数据库表创建失败: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

def load_data_from_db():
    """从数据库加载所有数据到内存"""
    global user_points, signin_records, recharge_records, recharge_locks
    global products, user_exchanges, points_history, forward_library, user_locks
    
    conn = get_db_connection()
    if not conn:
        return
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # 加载用户积分
        cur.execute('SELECT * FROM user_points')
        for row in cur.fetchall():
            user_points[row['user_id']] = row['points']
            if row['last_signin']:
                signin_records[row['user_id']] = row['last_signin']
        
        # 加载充值记录
        cur.execute('SELECT * FROM recharge_records')
        for row in cur.fetchall():
            recharge_records[row['user_id']] = {
                'wechat': row['wechat_used'],
                'alipay': row['alipay_used']
            }
            
            # 加载锁定信息
            recharge_locks[row['user_id']] = {}
            if row['wechat_locked_until']:
                recharge_locks[row['user_id']]['wechat'] = {
                    'count': row['wechat_fail_count'],
                    'locked_until': row['wechat_locked_until']
                }
            if row['alipay_locked_until']:
                recharge_locks[row['user_id']]['alipay'] = {
                    'count': row['alipay_fail_count'],
                    'locked_until': row['alipay_locked_until']
                }
        
        # 加载商品
        cur.execute('SELECT * FROM products')
        for row in cur.fetchall():
            products[row['product_id']] = {
                'name': row['name'],
                'points': row['points'],
                'content': {
                    'type': row['content_type'],
                    'data': row['content_data']
                }
            }
        
        # 加载用户兑换记录
        cur.execute('SELECT user_id, product_id FROM user_exchanges')
        for row in cur.fetchall():
            if row['user_id'] not in user_exchanges:
                user_exchanges[row['user_id']] = []
            user_exchanges[row['user_id']].append(row['product_id'])
        
        # 加载积分历史
        cur.execute('SELECT * FROM points_history ORDER BY created_at DESC')
        for row in cur.fetchall():
            if row['user_id'] not in points_history:
                points_history[row['user_id']] = []
            points_history[row['user_id']].append({
                'time': row['created_at'],
                'type': row['points_type'],
                'points': row['points'],
                'desc': row['description']
            })
        
        # 加载转发库
        cur.execute('SELECT * FROM forward_library')
        for row in cur.fetchall():
            forward_library[row['command']] = {
                'chat_id': row['chat_id'],
                'message_ids': json.loads(row['message_ids']) if row['message_ids'] else [],
                'created_by': row['created_by']
            }
        
        # 加载用户锁定
        cur.execute('SELECT * FROM user_locks WHERE locked_until > NOW()')
        for row in cur.fetchall():
            user_locks[row['user_id']] = {
                'count': row['fail_count'],
                'locked_until': row['locked_until']
            }
        
        logger.info("✅ 数据加载成功")
        
    except Exception as e:
        logger.error(f"数据加载失败: {e}")
    finally:
        cur.close()
        conn.close()

def save_user_points_to_db(user_id: int, points: int):
    """保存用户积分到数据库"""
    conn = get_db_connection()
    if not conn:
        return
    
    try:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO user_points (user_id, points, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (user_id)
            DO UPDATE SET points = %s, updated_at = NOW()
        ''', (user_id, points, points))
        conn.commit()
    except Exception as e:
        logger.error(f"保存积分失败: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

def save_signin_to_db(user_id: int, signin_date):
    """保存签到记录到数据库"""
    conn = get_db_connection()
    if not conn:
        return
    
    try:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO user_points (user_id, last_signin, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (user_id)
            DO UPDATE SET last_signin = %s, updated_at = NOW()
        ''', (user_id, signin_date, signin_date))
        conn.commit()
    except Exception as e:
        logger.error(f"保存签到失败: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

def save_product_to_db(product_id: str, product_data: dict):
    """保存商品到数据库"""
    conn = get_db_connection()
    if not conn:
        return
    
    try:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO products (product_id, name, points, content_type, content_data)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (product_id)
            DO UPDATE SET name = %s, points = %s, content_type = %s, content_data = %s
        ''', (
            product_id,
            product_data['name'],
            product_data['points'],
            product_data['content']['type'],
            product_data['content']['data'],
            product_data['name'],
            product_data['points'],
            product_data['content']['type'],
            product_data['content']['data']
        ))
        conn.commit()
    except Exception as e:
        logger.error(f"保存商品失败: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

def save_forward_library_to_db(command: str, data: dict):
    """保存转发库到数据库"""
    conn = get_db_connection()
    if not conn:
        return
    
    try:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO forward_library (command, chat_id, message_ids, created_by)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (command)
            DO UPDATE SET chat_id = %s, message_ids = %s
        ''', (
            command,
            data['chat_id'],
            json.dumps(data['message_ids']),
            data['created_by'],
            data['chat_id'],
            json.dumps(data['message_ids'])
        ))
        conn.commit()
    except Exception as e:
        logger.error(f"保存转发库失败: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

def delete_forward_library_from_db(command: str):
    """从数据库删除转发库"""
    conn = get_db_connection()
    if not conn:
        return
    
    try:
        cur = conn.cursor()
        cur.execute('DELETE FROM forward_library WHERE command = %s', (command,))
        conn.commit()
    except Exception as e:
        logger.error(f"删除转发库失败: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

def save_exchange_to_db(user_id: int, product_id: str):
    """保存兑换记录到数据库"""
    conn = get_db_connection()
    if not conn:
        return
    
    try:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO user_exchanges (user_id, product_id)
            VALUES (%s, %s)
            ON CONFLICT (user_id, product_id) DO NOTHING
        ''', (user_id, product_id))
        conn.commit()
    except Exception as e:
        logger.error(f"保存兑换记录失败: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

def save_points_history_to_db(user_id: int, points_type: str, points: int, description: str):
    """保存积分历史到数据库"""
    conn = get_db_connection()
    if not conn:
        return
    
    try:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO points_history (user_id, points_type, points, description)
            VALUES (%s, %s, %s, %s)
        ''', (user_id, points_type, points, description))
        conn.commit()
    except Exception as e:
        logger.error(f"保存积分历史失败: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

def save_recharge_record_to_db(user_id: int, pay_type: str):
    """保存充值记录到数据库"""
    conn = get_db_connection()
    if not conn:
        return
    
    try:
        cur = conn.cursor()
        field = f"{pay_type}_used"
        cur.execute(f'''
            INSERT INTO recharge_records (user_id, {field}, updated_at)
            VALUES (%s, TRUE, NOW())
            ON CONFLICT (user_id)
            DO UPDATE SET {field} = TRUE, updated_at = NOW()
        ''', (user_id,))
        conn.commit()
    except Exception as e:
        logger.error(f"保存充值记录失败: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

# ============== 积分历史记录函数 ==============

def add_points_history(user_id: int, points_type: str, points: int, description: str):
    """添加积分历史记录"""
    if user_id not in points_history:
        points_history[user_id] = []
    
    points_history[user_id].append({
        'time': datetime.now(),
        'type': points_type,
        'points': points,
        'desc': description
    })
    
    # 保存到数据库
    save_points_history_to_db(user_id, points_type, points, description)
    
    # 只保留最近50条记录
    if len(points_history[user_id]) > 50:
        points_history[user_id] = points_history[user_id][-50:]

def get_points_history(user_id: int) -> list:
    """获取积分历史记录"""
    return points_history.get(user_id, [])

# ============== 兑换系统函数 ==============

def has_exchanged(user_id: int, product_id: str) -> bool:
    """检查用户是否已兑换该商品"""
    if user_id not in user_exchanges:
        return False
    return product_id in user_exchanges[user_id]

def mark_exchanged(user_id: int, product_id: str):
    """标记用户已兑换"""
    if user_id not in user_exchanges:
        user_exchanges[user_id] = []
    if product_id not in user_exchanges[user_id]:
        user_exchanges[user_id].append(product_id)
        save_exchange_to_db(user_id, product_id)

def can_exchange(user_id: int, product_id: str) -> bool:
    """检查用户是否可以兑换"""
    if product_id not in products:
        return False
    
    product = products[product_id]
    user_pts = get_user_points(user_id)
    
    return user_pts >= product['points']

def exchange_product(user_id: int, product_id: str) -> bool:
    """兑换商品，返回是否成功"""
    if not can_exchange(user_id, product_id):
        return False
    
    product = products[product_id]
    
    if user_id not in user_points:
        user_points[user_id] = 0
    
    user_points[user_id] -= product['points']
    save_user_points_to_db(user_id, user_points[user_id])
    
    mark_exchanged(user_id, product_id)
    add_points_history(user_id, 'spend', product['points'], f"兑换商品：{product['name']}")
    
    return True

# ============== 积分系统函数 ==============

def get_user_points(user_id: int) -> int:
    """获取用户积分"""
    return user_points.get(user_id, 0)

def add_points(user_id: int, points: int):
    """增加积分"""
    if user_id not in user_points:
        user_points[user_id] = 0
    user_points[user_id] += points
    save_user_points_to_db(user_id, user_points[user_id])

def can_signin(user_id: int) -> bool:
    """检查是否可以签到"""
    if user_id not in signin_records:
        return True
    
    last_signin = signin_records[user_id]
    today = datetime.now().date()
    
    return last_signin < today

def do_signin(user_id: int) -> int:
    """执行签到，返回获得的积分"""
    points = random.randint(3, 8)
    add_points(user_id, points)
    signin_records[user_id] = datetime.now().date()
    save_signin_to_db(user_id, signin_records[user_id])
    add_points_history(user_id, 'earn', points, '每日签到')
    
    return points

def has_recharged(user_id: int, pay_type: str) -> bool:
    """检查是否已充值过"""
    if user_id not in recharge_records:
        recharge_records[user_id] = {'wechat': False, 'alipay': False}
    return recharge_records[user_id].get(pay_type, False)

def mark_recharged(user_id: int, pay_type: str):
    """标记已充值"""
    if user_id not in recharge_records:
        recharge_records[user_id] = {'wechat': False, 'alipay': False}
    recharge_records[user_id][pay_type] = True
    save_recharge_record_to_db(user_id, pay_type)

def is_recharge_locked(user_id: int, pay_type: str) -> tuple[bool, datetime]:
    """检查充值是否被锁定"""
    if user_id not in recharge_locks:
        return False, None
    
    if pay_type not in recharge_locks[user_id]:
        return False, None
    
    lock_info = recharge_locks[user_id][pay_type]
    if lock_info.get('locked_until') and lock_info['locked_until'] > datetime.now():
        return True, lock_info['locked_until']
    else:
        if 'locked_until' in lock_info and lock_info['locked_until'] <= datetime.now():
            recharge_locks[user_id][pay_type] = {'count': 0, 'locked_until': None}
        return False, None

def record_recharge_failed(user_id: int, pay_type: str):
    """记录充值失败"""
    if user_id not in recharge_locks:
        recharge_locks[user_id] = {}
    
    if pay_type not in recharge_locks[user_id]:
        recharge_locks[user_id][pay_type] = {'count': 0, 'locked_until': None}
    
    recharge_locks[user_id][pay_type]['count'] += 1
    
    if recharge_locks[user_id][pay_type]['count'] >= 2:
        recharge_locks[user_id][pay_type]['locked_until'] = datetime.now() + timedelta(hours=10)
        recharge_locks[user_id][pay_type]['count'] = 0
        return True
    return False

def get_recharge_attempts(user_id: int, pay_type: str) -> int:
    """获取充值失败次数"""
    if user_id not in recharge_locks:
        return 0
    if pay_type not in recharge_locks[user_id]:
        return 0
    return recharge_locks[user_id][pay_type].get('count', 0)

def verify_wechat_order(order_number: str) -> bool:
    """验证微信订单号"""
    return order_number.startswith('4200')

def verify_alipay_order(order_number: str) -> bool:
    """验证支付宝订单号"""
    return order_number.startswith('4768')

# ============== 余额页面 ==============

async def show_balance_page(query, context: ContextTypes.DEFAULT_TYPE):
    """显示余额页面"""
    user_id = query.from_user.id
    points = get_user_points(user_id)
    history = get_points_history(user_id)
    
    text = f"💰 *我的余额*\n\n📊 当前积分：`{points}` 分\n\n"
    
    if history:
        text += "📝 *积分记录*\n\n"
        
        recent_history = sorted(history, key=lambda x: x['time'], reverse=True)[:10]
        
        for record in recent_history:
            time_str = record['time'].strftime('%m-%d %H:%M')
            points_str = f"+{record['points']}" if record['type'] == 'earn' else f"-{record['points']}"
            
            if record['type'] == 'earn':
                emoji = "📈"
            else:
                emoji = "📉"
            
            text += f"{emoji} {time_str} | {points_str} 分 | {record['desc']}\n"
    else:
        text += "📝 *积分记录*\n\n暂无记录"
    
    keyboard = [
        [InlineKeyboardButton("🔙 返回积分页", callback_data="show_points")]
    ]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

# ============== 兑换页面 - 修复部分 ==============

async def show_exchange_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示兑换页面"""
    user_id = update.effective_user.id
    points = get_user_points(user_id)
    
    text = (
        f"🎁 *积分兑换*\n\n"
        f"💰 当前积分：`{points}` 分\n\n"
        f"📦 请选择要兑换的商品："
    )
    
    keyboard = []
    
    for product_id, product in products.items():
        if has_exchanged(user_id, product_id):
            button_text = f"✅ {product['name']} (已兑换)"
        else:
            button_text = f"🎁 {product['name']} ({product['points']}积分)"
        
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"exchange_{product_id}")])
    
    keyboard.append([InlineKeyboardButton("🔙 返回积分页", callback_data="show_points")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def handle_exchange(query, context: ContextTypes.DEFAULT_TYPE, product_id: str):
    """处理兑换请求"""
    user_id = query.from_user.id
    
    if product_id not in products:
        await query.answer("❌ 商品不存在", show_alert=True)
        return
    
    product = products[product_id]
    
    # 如果已兑换，直接发送内容（修复：显示在单独页面）
    if has_exchanged(user_id, product_id):
        await show_exchanged_product_content(query, context, product_id)
        return
    
    # 未兑换，显示确认页面
    points = get_user_points(user_id)
    
    text = (
        f"🎁 *确认兑换*\n\n"
        f"商品名称：{product['name']}\n"
        f"所需积分：{product['points']} 分\n"
        f"当前积分：{points} 分\n\n"
        f"确定要兑换吗？"
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ 确认兑换", callback_data=f"confirm_exchange_{product_id}")],
        [InlineKeyboardButton("❌ 取消", callback_data="show_exchange")]
    ]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def confirm_exchange(query, context: ContextTypes.DEFAULT_TYPE, product_id: str):
    """确认兑换"""
    user_id = query.from_user.id
    
    if product_id not in products:
        await query.answer("❌ 商品不存在", show_alert=True)
        return
    
    if exchange_product(user_id, product_id):
        await query.answer("✅ 兑换成功！", show_alert=True)
        await show_exchanged_product_content(query, context, product_id)
    else:
        await query.answer("❌ 积分余额不足，请重试", show_alert=True)
        
        class TempUpdate:
            def __init__(self, query_obj):
                self.callback_query = query_obj
                self.effective_user = query_obj.from_user
                self.message = None
        
        temp_update = TempUpdate(query)
        await show_exchange_page(temp_update, context)

async def show_exchanged_product_content(query, context: ContextTypes.DEFAULT_TYPE, product_id: str):
    """显示已兑换商品内容（单独页面）- 修复部分"""
    user_id = query.from_user.id
    product = products[product_id]
    content = product['content']
    
    # 先显示内容页面
    content_text = (
        f"📦 *{product['name']}*\n\n"
        f"✅ 兑换成功\n\n"
    )
    
    keyboard = [
        [InlineKeyboardButton("🔙 返回兑换页", callback_data="show_exchange")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        if content['type'] == 'text':
            # 文本内容直接显示
            full_text = content_text + f"📄 内容：\n\n{content['data']}"
            await query.edit_message_text(
                full_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        
        elif content['type'] == 'photo':
            # 图片内容
            await query.edit_message_text(content_text, parse_mode='Markdown')
            await context.bot.send_photo(
                chat_id=user_id,
                photo=content['data'],
                caption=f"📦 {product['name']} - 商品内容",
                reply_markup=reply_markup
            )
        
        elif content['type'] == 'video':
            # 视频内容
            await query.edit_message_text(content_text, parse_mode='Markdown')
            await context.bot.send_video(
                chat_id=user_id,
                video=content['data'],
                caption=f"📦 {product['name']} - 商品内容",
                reply_markup=reply_markup
            )
        
        elif content['type'] == 'document':
            # 文档内容
            await query.edit_message_text(content_text, parse_mode='Markdown')
            await context.bot.send_document(
                chat_id=user_id,
                document=content['data'],
                caption=f"📦 {product['name']} - 商品内容",
                reply_markup=reply_markup
            )
        
    except Exception as e:
        logger.error(f"发送商品内容失败: {e}")
        await query.answer("❌ 发送失败，请联系管理员", show_alert=True)

# ============== 管理员商品管理 ==============

async def show_product_management(query, context: ContextTypes.DEFAULT_TYPE):
    """显示商品管理页面"""
    text = "📦 *商品管理*\n\n已上架商品："
    
    keyboard = []
    
    for product_id, product in products.items():
        if product_id == 'test_product':
            continue
        keyboard.append([InlineKeyboardButton(
            f"📦 {product['name']} ({product['points']}积分)",
            callback_data=f"manage_product_{product_id}"
        )])
    
    keyboard.append([InlineKeyboardButton("➕ 添加新商品", callback_data="add_product")])
    keyboard.append([InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def handle_add_product(query, context: ContextTypes.DEFAULT_TYPE):
    """处理添加商品"""
    user_id = query.from_user.id
    
    await query.edit_message_text(
        "📝 *添加新商品*\n\n"
        "请输入商品名称：\n\n"
        "💡 支持中文、英文\n\n"
        "发送 /cancel 取消",
        parse_mode='Markdown'
    )
    
    context.user_data['waiting_product_name'] = True
    context.user_data['in_admin_process'] = True

async def handle_product_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理商品名称输入"""
    if not context.user_data.get('waiting_product_name'):
        return False
    
    user_id = update.effective_user.id
    product_name = update.message.text.strip()
    
    temp_products[user_id] = {
        'name': product_name,
        'points': 0,
        'content': {}
    }
    
    context.user_data['waiting_product_name'] = False
    context.user_data['waiting_product_points'] = True
    
    await update.message.reply_text(
        f"✅ 商品名称：{product_name}\n\n"
        f"💰 请输入所需积分：\n\n"
        f"💡 输入纯数字即可"
    )
    
    return True

async def handle_product_points_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理商品积分输入"""
    if not context.user_data.get('waiting_product_points'):
        return False
    
    user_id = update.effective_user.id
    
    try:
        points = int(update.message.text.strip())
        
        if points < 0:
            await update.message.reply_text("❌ 积分必须大于等于0，请重新输入：")
            return True
        
        temp_products[user_id]['points'] = points
        
        context.user_data['waiting_product_points'] = False
        context.user_data['waiting_product_content'] = True
        
        await update.message.reply_text(
            f"✅ 所需积分：{points} 分\n\n"
            f"📤 *请发送商品内容*\n\n"
            f"支持的类型：\n"
            f"• 📝 文本消息\n"
            f"• 🖼 图片\n"
            f"• 🎬 视频\n"
            f"• 📄 文档\n\n"
            f"💡 发送内容后自动上架",
            parse_mode='Markdown'
        )
        
        return True
        
    except ValueError:
        await update.message.reply_text("❌ 请输入有效的数字：")
        return True

async def handle_product_content_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理商品内容输入"""
    if not context.user_data.get('waiting_product_content'):
        return False
    
    user_id = update.effective_user.id
    message = update.message
    
    if user_id not in temp_products:
        await message.reply_text("❌ 会话已过期，请重新开始")
        context.user_data.clear()
        return True
    
    content = {}
    
    if message.text:
        content = {
            'type': 'text',
            'data': message.text
        }
    elif message.photo:
        content = {
            'type': 'photo',
            'data': message.photo[-1].file_id
        }
    elif message.video:
        content = {
            'type': 'video',
            'data': message.video.file_id
        }
    elif message.document:
        content = {
            'type': 'document',
            'data': message.document.file_id
        }
    else:
        await message.reply_text("❌ 不支持的内容类型，请重新发送")
        return True
    
    temp_products[user_id]['content'] = content
    product_id = f"product_{len(products)}_{int(datetime.now().timestamp())}"
    
    products[product_id] = temp_products[user_id]
    save_product_to_db(product_id, products[product_id])
    
    del temp_products[user_id]
    context.user_data.clear()
    
    await message.reply_text(
        f"✅ *商品上架成功！*\n\n"
        f"商品名称：{products[product_id]['name']}\n"
        f"所需积分：{products[product_id]['points']} 分\n\n"
        f"正在返回商品管理页面...",
        parse_mode='Markdown'
    )
    
    await asyncio.sleep(1)
    
    keyboard = [
        [InlineKeyboardButton("📦 商品管理", callback_data="product_management")],
        [InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")]
    ]
    
    await message.reply_text(
        "✅ 上架完成",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    return True

async def manage_product(query, context: ContextTypes.DEFAULT_TYPE, product_id: str):
    """管理单个商品"""
    if product_id not in products:
        await query.answer("❌ 商品不存在", show_alert=True)
        return
    
    product = products[product_id]
    
    text = (
        f"📦 *商品详情*\n\n"
        f"商品名称：{product['name']}\n"
        f"所需积分：{product['points']} 分\n"
        f"内容类型：{product['content']['type']}\n\n"
        f"确定要下架此商品吗？"
    )
    
    keyboard = [
        [InlineKeyboardButton("🗑 确认下架", callback_data=f"remove_product_{product_id}")],
        [InlineKeyboardButton("🔙 返回列表", callback_data="product_management")]
    ]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def remove_product(query, context: ContextTypes.DEFAULT_TYPE, product_id: str):
    """删除商品"""
    if product_id in products:
        del products[product_id]
        await query.answer("✅ 商品已下架", show_alert=True)
    else:
        await query.answer("❌ 商品不存在", show_alert=True)
    
    await show_product_management(query, context)

# ============== 积分页面 ==============

async def show_points_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示积分页面"""
    user_id = update.effective_user.id
    points = get_user_points(user_id)
    can_sign = can_signin(user_id)
    
    text = (
        f"💰 *积分中心*\n\n"
        f"👤 当前积分：`{points}` 分\n\n"
        f"📌 签到状态：{'✅ 可签到' if can_sign else '❌ 今日已签到'}\n"
        f"💡 每日签到可随机获得 3-8 积分"
    )
    
    keyboard = [
        [InlineKeyboardButton("✍️ 每日签到", callback_data="daily_signin")],
        [InlineKeyboardButton("💳 积分充值", callback_data="recharge_menu")],
        [InlineKeyboardButton("🎁 积分兑换", callback_data="show_exchange")],
        [InlineKeyboardButton("💼 我的余额", callback_data="show_balance")],
        [InlineKeyboardButton("🏠 返回首页", callback_data="back_home")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def handle_signin(query, context: ContextTypes.DEFAULT_TYPE):
    """处理签到"""
    user_id = query.from_user.id
    
    if not can_signin(user_id):
        await query.answer("❌ 今日已签到，明天再来吧！", show_alert=True)
        return
    
    points = do_signin(user_id)
    total_points = get_user_points(user_id)
    
    await query.answer(f"✅ 签到成功！获得 {points} 积分", show_alert=True)
    
    text = (
        f"🎉 *签到成功！*\n\n"
        f"🎁 本次获得：`{points}` 积分\n"
        f"💰 当前积分：`{total_points}` 分\n\n"
        f"📅 明天继续签到可获得更多积分哦~"
    )
    
    keyboard = [
        [InlineKeyboardButton("💳 积分充值", callback_data="recharge_menu")],
        [InlineKeyboardButton("🔙 返回积分页", callback_data="show_points")]
    ]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

# 由于字符限制，我将在下一条消息继续发送代码的其余部分...
