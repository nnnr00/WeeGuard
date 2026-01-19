import os
import logging
import psycopg2
import datetime
import random
import asyncio 
from datetime import timedelta, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ConversationHandler,
)

# ================= 配置区域 =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

# 【需手动配置区 - 请填入提取的 File ID】
VIP_IMAGE_ID = "AgACAgEAAykBA..."    
TUTORIAL_IMAGE_ID = "AgACAgEAAykBA..." 
GROUP_LINK = "https://t.me/your_group_link"

# 积分充值用图
WECHAT_QR_ID = "AgACAgEAAykBA..."        
WECHAT_TUTORIAL_ID = "AgACAgEAAykBA..."  
ALIPAY_QR_ID = "AgACAgEAAykBA..."       
ALIPAY_TUTORIAL_ID = "AgACAgEAAykBA..." 

# ================= 状态机定义 (完整命名) =================
# 管理员 - 提取ID
ADMIN_WAITING_FOR_PHOTO = 1
# 管理员 - 转发库
LIBRARY_INPUT_COMMAND_NAME = 2
LIBRARY_UPLOAD_CONTENT = 3
# 管理员 - 商品管理
PRODUCT_INPUT_NAME = 4
PRODUCT_INPUT_COST = 5
PRODUCT_INPUT_CONTENT = 6
# 用户 - 验证
VERIFY_INPUT_ORDER_NUMBER = 10
# 用户 - 积分充值
POINTS_INPUT_WECHAT_ORDER = 20
POINTS_INPUT_ALIPAY_ORDER = 21

# ================= 日志配置 =================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================= 数据库层 =================
def get_database_connection():
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        logger.error(f"Database Connection Error: {e}")
        return None

def init_database():
    """初始化数据库表结构 (新增用户信息字段)"""
    connection = get_database_connection()
    if connection:
        with connection.cursor() as cursor:
            # 1. VIP 验证表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_verification (
                    user_id BIGINT PRIMARY KEY,
                    failure_count INT DEFAULT 0,
                    cooldown_until TIMESTAMP
                );
            """)
            # 2. 转发库表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS forward_library (
                    id SERIAL PRIMARY KEY,
                    trigger_command TEXT NOT NULL,
                    source_chat_id BIGINT NOT NULL,
                    source_message_id INT NOT NULL,
                    message_type TEXT, 
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            # 3. 积分系统表 (新增 username 和 first_name)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_points (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    points INT DEFAULT 0,
                    last_checkin_date DATE,
                    wechat_done BOOLEAN DEFAULT FALSE,
                    alipay_done BOOLEAN DEFAULT FALSE,
                    wechat_failure_count INT DEFAULT 0,
                    alipay_failure_count INT DEFAULT 0,
                    wechat_cooldown TIMESTAMP,
                    alipay_cooldown TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            # 4. 商品表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    cost INT NOT NULL,
                    content_type TEXT, 
                    content_text TEXT, 
                    file_id TEXT,      
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            # 5. 兑换记录表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS redemptions (
                    user_id BIGINT,
                    product_id INT,
                    redeemed_at TIMESTAMP DEFAULT NOW(),
                    PRIMARY KEY (user_id, product_id)
                );
            """)
            # 6. 积分流水表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS point_history (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    amount INT,
                    reason TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            
            # 初始化测试商品
            cursor.execute("SELECT COUNT(*) FROM products WHERE name = '测试'")
            if cursor.fetchone()[0] == 0:
                cursor.execute("INSERT INTO products (name, cost, content_type, content_text) VALUES (%s, %s, %s, %s)", 
                            ("测试", 0, "text", "哈哈"))

            connection.commit()
        connection.close()

# --- 数据库工具函数 ---

def database_update_user_profile(user_id, username, first_name):
    """更新用户信息"""
    connection = get_database_connection()
    if connection:
        with connection.cursor() as cursor:
            # 如果存在则更新名字，不存在则插入
            cursor.execute("""
                INSERT INTO user_points (user_id, username, first_name) 
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id) 
                DO UPDATE SET username = EXCLUDED.username, first_name = EXCLUDED.first_name
            """, (user_id, username, first_name))
            connection.commit()
        connection.close()

def database_log_history(user_id, amount, reason):
    connection = get_database_connection()
    if connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO point_history (user_id, amount, reason) VALUES (%s, %s, %s)", (user_id, amount, reason))
            connection.commit()
        connection.close()

def database_get_points_info(user_id):
    connection = get_database_connection()
    if not connection: return None
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM user_points WHERE user_id = %s", (user_id,))
            result = cursor.fetchone()
            if not result:
                # 如果没有记录，先创建一个空的
                cursor.execute("INSERT INTO user_points (user_id) VALUES (%s) RETURNING *", (user_id,))
                connection.commit()
                result = cursor.fetchone()
            
            # 字段索引映射需根据 CREATE TABLE 顺序
            # 0:user_id, 1:username, 2:first_name, 3:points, 4:last_checkin, ...
            return {
                'points': result[3],
                'last_checkin_date': result[4],
                'wechat_done': result[5],
                'alipay_done': result[6],
                'wechat_failure_count': result[7],
                'alipay_failure_count': result[8],
                'wechat_cooldown': result[9],
                'alipay_cooldown': result[10]
            }
    finally:
        connection.close()

def database_checkin(user_id, add_points):
    connection = get_database_connection()
    if connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE user_points SET points = points + %s, last_checkin_date = %s WHERE user_id = %s", 
                        (add_points, date.today(), user_id))
            connection.commit()
        connection.close()
    database_log_history(user_id, add_points, "每日签到")

def database_add_points(user_id, amount, source="充值"):
    connection = get_database_connection()
    if connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE user_points SET points = points + %s WHERE user_id = %s", (amount, user_id))
            connection.commit()
        connection.close()
    database_log_history(user_id, amount, source)

def database_deduct_points(user_id, amount, reason="兑换"):
    connection = get_database_connection()
    success = False
    if connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT points FROM user_points WHERE user_id = %s", (user_id,))
            result = cursor.fetchone()
            current = result[0] if result else 0
            if current >= amount:
                cursor.execute("UPDATE user_points SET points = points - %s WHERE user_id = %s", (amount, user_id))
                connection.commit()
                success = True
        connection.close()
    if success:
        database_log_history(user_id, -amount, reason)
    return success

def database_get_history(user_id, limit=10):
    connection = get_database_connection()
    data = []
    if connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT amount, reason, created_at FROM point_history WHERE user_id = %s ORDER BY created_at DESC LIMIT %s", (user_id, limit))
            data = cursor.fetchall()
        connection.close()
    return data

def database_update_recharge_status(user_id, method, is_success, is_failure_increment=False, lock_hours=0):
    connection = get_database_connection()
    if not connection: return
    try:
        with connection.cursor() as cursor:
            if is_success:
                column = f"{method}_done"
                failure_column = f"{method}_failure_count"
                cursor.execute(f"UPDATE user_points SET {column} = TRUE, {failure_column} = 0 WHERE user_id = %s", (user_id,))
            elif is_failure_increment:
                failure_column = f"{method}_failure_count"
                cooldown_column = f"{method}_cooldown"
                if lock_hours > 0:
                    unlock_time = datetime.datetime.now() + timedelta(hours=lock_hours)
                    cursor.execute(f"UPDATE user_points SET {failure_column} = 0, {cooldown_column} = %s WHERE user_id = %s", (unlock_time, user_id))
                else:
                    cursor.execute(f"UPDATE user_points SET {failure_column} = {failure_column} + 1 WHERE user_id = %s", (user_id,))
            connection.commit()
    finally:
        connection.close()

def database_add_product(name, cost, content_type, content_text, file_id):
    connection = get_database_connection()
    if connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO products (name, cost, content_type, content_text, file_id) VALUES (%s, %s, %s, %s, %s)", 
                        (name, cost, content_type, content_text, file_id))
            connection.commit()
        connection.close()

def database_get_products():
    connection = get_database_connection()
    data = []
    if connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, name, cost FROM products ORDER BY id ASC")
            data = cursor.fetchall()
        connection.close()
    return data

def database_get_product_detail(product_id):
    connection = get_database_connection()
    result = None
    if connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM products WHERE id = %s", (product_id,))
            result = cursor.fetchone()
        connection.close()
    return result

def database_delete_product(product_id):
    connection = get_database_connection()
    if connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM products WHERE id = %s", (product_id,))
            cursor.execute("DELETE FROM redemptions WHERE product_id = %s", (product_id,))
            connection.commit()
        connection.close()

def database_is_redeemed(user_id, product_id):
    connection = get_database_connection()
    is_redeemed = False
    if connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM redemptions WHERE user_id = %s AND product_id = %s", (user_id, product_id))
            if cursor.fetchone(): is_redeemed = True
        connection.close()
    return is_redeemed

def database_record_redemption(user_id, product_id):
    connection = get_database_connection()
    if connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO redemptions (user_id, product_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (user_id, product_id))
            connection.commit()
        connection.close()

def check_user_verification_status(user_id):
    connection = get_database_connection()
    if not connection: return (False, 0, 0)
    status = (False, 0, 0)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT failure_count, cooldown_until FROM user_verification WHERE user_id = %s", (user_id,))
            result = cursor.fetchone()
            if result:
                failure_count, cooldown_until = result
                if cooldown_until and cooldown_until > datetime.datetime.now():
                    remaining = (cooldown_until - datetime.datetime.now()).total_seconds()
                    status = (True, int(remaining), failure_count)
                else:
                    status = (False, 0, failure_count)
    finally:
        connection.close()
    return status

def update_verification_fail_count(user_id):
    connection = get_database_connection()
    if not connection: return 0
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO user_verification (user_id, failure_count) VALUES (%s, 1)
                ON CONFLICT (user_id) DO UPDATE SET failure_count = user_verification.failure_count + 1
                RETURNING failure_count
            """, (user_id,))
            new_count = cursor.fetchone()[0]
            if new_count >= 2:
                cooldown_time = datetime.datetime.now() + timedelta(hours=5)
                cursor.execute("UPDATE user_verification SET cooldown_until = %s, failure_count = 0 WHERE user_id = %s", (cooldown_time, user_id))
                connection.commit()
                return -1
            connection.commit()
            return new_count
    finally:
        connection.close()

def reset_verification_success(user_id):
    connection = get_database_connection()
    if connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM user_verification WHERE user_id = %s", (user_id,))
            connection.commit()
        connection.close()

def database_add_library_content(command, chat_id, message_id, message_type):
    connection = get_database_connection()
    if connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO forward_library (trigger_command, source_chat_id, source_message_id, message_type) VALUES (%s, %s, %s, %s)", 
                        (command, chat_id, message_id, message_type))
            connection.commit()
        connection.close()

def database_get_library_commands():
    connection = get_database_connection()
    commands = []
    if connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DISTINCT trigger_command FROM forward_library ORDER BY trigger_command")
            commands = [row[0] for row in cursor.fetchall()]
        connection.close()
    return commands

def database_get_content_by_command(command):
    connection = get_database_connection()
    data = []
    if connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT source_chat_id, source_message_id FROM forward_library WHERE trigger_command = %s ORDER BY id ASC", (command,))
            data = cursor.fetchall()
        connection.close()
    return data

def database_delete_command(command):
    connection = get_database_connection()
    if connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM forward_library WHERE trigger_command = %s", (command,))
            connection.commit()
        connection.close()

# --- 新增：用户管理与记录查询 DB 函数 ---
def database_get_all_users(limit=20):
    """获取最近的用户列表"""
    connection = get_database_connection()
    users = []
    if connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT user_id, first_name, username, points FROM user_points ORDER BY created_at DESC LIMIT %s", (limit,))
            users = cursor.fetchall()
        connection.close()
    return users

def database_get_user_redemption_history(user_id):
    """获取指定用户的兑换记录"""
    connection = get_database_connection()
    history = []
    if connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT p.name, r.redeemed_at 
                FROM redemptions r
                JOIN products p ON r.product_id = p.id
                WHERE r.user_id = %s
                ORDER BY r.redeemed_at DESC
            """, (user_id,))
            history = cursor.fetchall()
        connection.close()
    return history

# ================= 业务逻辑：首页 =================
async def send_home_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 更新用户信息
    user = update.effective_user
    if user:
        database_update_user_profile(user.id, user.username, user.first_name)

    text = (
        "👋 <b>欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~</b>\n\n"
        "📢 小卫小卫，守门员小卫！\n"
        "一键入群，小卫帮你搞定！\n"
        "新人来报到，小卫查身份！"
    )
    keyboard = [
        [InlineKeyboardButton("🚀 开始验证", callback_data="start_verify")],
        [InlineKeyboardButton("💰 我的积分", callback_data="points_home")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    chat_id = None
    if update:
        chat_id = update.effective_chat.id
    elif context.job:
        chat_id = context.job.chat_id
    
    if chat_id:
        try:
            await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode='HTML')
        except Exception:
            pass

async def global_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_home_screen(update, context)

# ================= 业务逻辑：积分系统 =================

async def points_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # 只要进入积分中心也更新一下信息
    if user:
        database_update_user_profile(user.id, user.username, user.first_name)
        
    user_id = user.id
    info = database_get_points_info(user_id)
    query = update.callback_query
    if query: await query.answer()

    text = f"💰 <b>我的积分中心</b>\n\n当前积分：<b>{info['points']}</b>"
    
    keyboard = [
        [InlineKeyboardButton("📅 每日签到", callback_data="points_checkin")],
        [InlineKeyboardButton("💎 积分充值", callback_data="points_recharge")],
        [InlineKeyboardButton("🎁 积分兑换", callback_data="exchange_home")],
        [InlineKeyboardButton("📜 余额记录", callback_data="points_history")],
        [InlineKeyboardButton("🔙 返回首页", callback_data="back_home")]
    ]
    
    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def points_checkin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    info = database_get_points_info(user_id)
    if info['last_checkin_date'] == date.today():
        await query.answer("⚠️ 今天已经签到过了", show_alert=True)
    else:
        points_to_add = random.randint(3, 8)
        database_checkin(user_id, points_to_add)
        await query.answer(f"✅ 签到成功！获得 {points_to_add} 积分。", show_alert=True)
        await points_menu_handler(update, context)

async def points_history_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    info = database_get_points_info(user_id)
    history = database_get_history(user_id, limit=10)
    
    text = f"📜 <b>积分余额记录</b>\n\n当前余额：<b>{info['points']}</b>\n\n<b>最近记录：</b>\n"
    if not history:
        text += "暂无记录"
    else:
        for amount, reason, date_time in history:
            sign = "+" if amount > 0 else ""
            time_string = date_time.strftime("%m-%d %H:%M")
            text += f"• <code>{time_string}</code>: {reason} <b>{sign}{amount}</b>\n"
            
    keyboard = [[InlineKeyboardButton("🔙 返回积分中心", callback_data="points_home")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def points_recharge_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    info = database_get_points_info(user_id)
    
    keyboard = []
    
    wechat_text = "💚 微信充值 (5元)"
    if info['wechat_done']:
        keyboard.append([InlineKeyboardButton("💚 微信充值 (已完成)", callback_data="points_disabled_done")])
    elif info['wechat_cooldown'] and info['wechat_cooldown'] > datetime.datetime.now():
        keyboard.append([InlineKeyboardButton("💚 微信充值 (5h冷却)", callback_data="points_disabled_cool")])
    else:
        keyboard.append([InlineKeyboardButton(wechat_text, callback_data="points_pay_wechat")])
        
    alipay_text = "💙 支付宝充值 (5元)"
    if info['alipay_done']:
        keyboard.append([InlineKeyboardButton("💙 支付宝充值 (已完成)", callback_data="points_disabled_done")])
    elif info['alipay_cooldown'] and info['alipay_cooldown'] > datetime.datetime.now():
        keyboard.append([InlineKeyboardButton("💙 支付宝充值 (5h冷却)", callback_data="points_disabled_cool")])
    else:
        keyboard.append([InlineKeyboardButton(alipay_text, callback_data="points_pay_alipay")])
        
    keyboard.append([InlineKeyboardButton("🔙 返回积分中心", callback_data="points_home")])
    
    text = (
        "💎 <b>积分充值中心</b>\n\n"
        "✨ <b>5元 = 100积分</b>\n\n"
        "⚠️ <b>温馨提示：</b>\n"
        "1. 微信和支付宝每个用户<b>仅限使用一次</b>。\n"
        "2. 连续失败2次将锁定通道5小时。"
    )
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def points_disabled_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if "done" in data: await query.answer("⛔️ 每人仅限一次。", show_alert=True)
    else: await query.answer("⛔️ 通道锁定中。", show_alert=True)

async def points_wechat_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "💚 <b>微信充值</b>\n\n请扫码支付 <b>5元</b>。\n支付后点击下方按钮。"
    keyboard = [[InlineKeyboardButton("✅ 我已付款，开始验证", callback_data="points_wechat_paid")]]
    try: await query.message.reply_photo(photo=WECHAT_QR_ID, caption=text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
    except: await query.message.reply_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def points_wechat_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "📝 <b>请输入微信支付凭证号</b>\n\n请复制 <b>交易单号</b> 回复："
    try: await query.message.reply_photo(photo=WECHAT_TUTORIAL_ID, caption=text, parse_mode='HTML')
    except: await query.message.reply_text(text, parse_mode='HTML')
    return POINTS_INPUT_WECHAT_ORDER

async def points_wechat_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    user_id = update.effective_user.id
    if user_input.startswith("4200"):
        database_update_recharge_status(user_id, 'wechat', is_success=True)
        database_add_points(user_id, 100, "微信充值")
        await update.message.reply_text("✅ <b>充值成功！</b>\n已到账 100 积分。", parse_mode='HTML')
        await points_menu_handler(update, context)
        return ConversationHandler.END
    else:
        info = database_get_points_info(user_id)
        if info['wechat_failure_count'] + 1 >= 2:
            database_update_recharge_status(user_id, 'wechat', is_success=False, is_failure_increment=True, lock_hours=5)
            await update.message.reply_text("❌ <b>识别失败</b>\n通道已锁定 5小时。", parse_mode='HTML')
            await points_menu_handler(update, context)
            return ConversationHandler.END
        else:
            database_update_recharge_status(user_id, 'wechat', is_success=False, is_failure_increment=True)
            await update.message.reply_text("⚠️ <b>识别失败</b>\n请重试，剩余 1次 机会。", parse_mode='HTML')
            return POINTS_INPUT_WECHAT_ORDER

async def points_alipay_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "💙 <b>支付宝充值</b>\n\n请扫码支付 <b>5元</b>。\n支付后点击下方按钮。"
    keyboard = [[InlineKeyboardButton("✅ 我已付款，开始验证", callback_data="points_alipay_paid")]]
    try: await query.message.reply_photo(photo=ALIPAY_QR_ID, caption=text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
    except: await query.message.reply_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def points_alipay_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "📝 <b>请输入支付宝订单号</b>\n\n请复制 <b>商家订单号</b> 回复："
    try: await query.message.reply_photo(photo=ALIPAY_TUTORIAL_ID, caption=text, parse_mode='HTML')
    except: await query.message.reply_text(text, parse_mode='HTML')
    return POINTS_INPUT_ALIPAY_ORDER

async def points_alipay_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    user_id = update.effective_user.id
    if user_input.startswith("4768"):
        database_update_recharge_status(user_id, 'alipay', is_success=True)
        database_add_points(user_id, 100, "支付宝充值")
        await update.message.reply_text("✅ <b>充值成功！</b>\n已到账 100 积分。", parse_mode='HTML')
        await points_menu_handler(update, context)
        return ConversationHandler.END
    else:
        info = database_get_points_info(user_id)
        if info['alipay_failure_count'] + 1 >= 2:
            database_update_recharge_status(user_id, 'alipay', is_success=False, is_failure_increment=True, lock_hours=5)
            await update.message.reply_text("❌ <b>识别失败</b>\n通道已锁定 5小时。", parse_mode='HTML')
            await points_menu_handler(update, context)
            return ConversationHandler.END
        else:
            database_update_recharge_status(user_id, 'alipay', is_success=False, is_failure_increment=True)
            await update.message.reply_text("⚠️ <b>识别失败</b>\n请重试，剩余 1次 机会。", parse_mode='HTML')
            return POINTS_INPUT_ALIPAY_ORDER

# ================= 业务逻辑：兑换系统 (/dh) =================

async def exchange_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()
    user_id = update.effective_user.id
    products = database_get_products()
    text = "🎁 <b>积分兑换商城</b>\n\n点击下方商品进行兑换。"
    keyboard = []
    for pid, name, cost in products:
        if database_is_redeemed(user_id, pid):
            button_text = f"📦 {name} (已兑换)"
            callback = f"exchange_view_{pid}"
        else:
            button_text = f"🛍️ {name} ({cost} 积分)"
            callback = f"exchange_buy_ask_{pid}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback)])
    keyboard.append([InlineKeyboardButton("🔙 返回积分中心", callback_data="points_home")])
    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def exchange_confirm_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    product_id = int(query.data.split('_')[-1])
    await query.answer()
    product = database_get_product_detail(product_id)
    if not product:
        await query.answer("❌ 商品不存在", show_alert=True)
        return
    name, cost = product[1], product[2]
    text = f"🛍️ <b>确认兑换？</b>\n\n商品：<b>{name}</b>\n价格：<b>{cost} 积分</b>"
    keyboard = [
        [InlineKeyboardButton("✅ 确认兑换", callback_data=f"exchange_do_buy_{product_id}")],
        [InlineKeyboardButton("❌ 取消", callback_data="exchange_home")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def exchange_execute_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    product_id = int(query.data.split('_')[-1])
    user_id = query.from_user.id
    product = database_get_product_detail(product_id)
    if not product: return
    name, cost = product[1], product[2]
    if database_deduct_points(user_id, cost, reason=f"兑换-{name}"):
        database_record_redemption(user_id, product_id)
        await query.answer("✅ 兑换成功！", show_alert=True)
        await send_product_content(user_id, product, context)
        await exchange_menu_handler(update, context)
    else:
        await query.answer("❌ 余额不足，请充值或签到。", show_alert=True)
        await exchange_menu_handler(update, context)

async def exchange_view_owned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    product_id = int(query.data.split('_')[-1])
    await query.answer()
    product = database_get_product_detail(product_id)
    if product:
        await send_product_content(query.from_user.id, product, context)
    else:
        await query.answer("商品已下架", show_alert=True)

async def send_product_content(user_id, product, context):
    content_type = product[3]
    content_text = product[4]
    file_id = product[5]
    caption = f"📦 <b>商品内容：{product[1]}</b>"
    try:
        if content_type == 'text':
            await context.bot.send_message(user_id, f"{caption}\n\n{content_text}", parse_mode='HTML')
        elif content_type == 'photo':
            await context.bot.send_photo(user_id, file_id, caption=caption, parse_mode='HTML')
        elif content_type == 'video':
            await context.bot.send_video(user_id, file_id, caption=caption, parse_mode='HTML')
        elif content_type == 'document':
            await context.bot.send_document(user_id, file_id, caption=caption, parse_mode='HTML')
        else:
            await context.bot.send_message(user_id, f"{caption}\n\n[未知格式]", parse_mode='HTML')
    except Exception as e:
        logger.error(f"Send product failed: {e}")
        await context.bot.send_message(user_id, "❌ 发送内容失败，请联系管理员。", parse_mode='HTML')

# ================= 业务逻辑：VIP验证流程 =================
async def verify_click_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    is_cooldown, remaining, _ = check_user_verification_status(user_id)
    if is_cooldown:
        m, s = divmod(remaining, 60)
        h, m = divmod(m, 60)
        await query.answer(f"⛔️ 锁定中 {int(h)}h{int(m)}m", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    text = (
        "💎 <b>VIP会员特权说明：</b>\n"
        "✅ 专属中转通道\n"
        "✅ 优先审核入群\n"
        "✅ 7x24小时客服支持\n"
        "✅ 定期福利活动"
    )
    keyboard = [[InlineKeyboardButton("✅ 我已付款，开始验证", callback_data="i_paid")]]
    try: await query.message.reply_photo(photo=VIP_IMAGE_ID, caption=text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
    except: await query.message.reply_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END

async def ask_order_id_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "📝 <b>请回复您的订单号：</b>"
    try: await query.message.reply_photo(photo=TUTORIAL_IMAGE_ID, caption=text, parse_mode='HTML')
    except: await query.message.reply_text(text, parse_mode='HTML')
    return VERIFY_INPUT_ORDER_NUMBER

async def process_order_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    user_id = update.effective_user.id
    if user_input.startswith("20260"):
        reset_verification_success(user_id)
        keyboard = [[InlineKeyboardButton("🔗 点击加入 VIP 群", url=GROUP_LINK)]]
        await update.message.reply_text("✅ <b>验证通过！</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        await send_home_screen(update, context)
        return ConversationHandler.END
    else:
        status = update_verification_fail_count(user_id)
        if status == -1:
            await update.message.reply_text("❌ <b>失败次数过多，锁定5小时。</b>", parse_mode='HTML')
            await send_home_screen(update, context)
            return ConversationHandler.END
        else:
            await update.message.reply_text("⚠️ <b>未查询到订单，请重试。</b>", parse_mode='HTML')
            return VERIFY_INPUT_ORDER_NUMBER

# ================= 业务逻辑：自定义命令转发与自动删除 =================

async def cleanup_messages_task(context: ContextTypes.DEFAULT_TYPE):
    """
    定时任务：删除消息，并提示跳转 (带日志调试版)
    """
    job = context.job
    data = job.data # 包含 'message_ids' 列表
    chat_id = job.chat_id
    
    logger.info(f"开始执行销毁任务，目标 Chat ID: {chat_id}, 待删除消息数: {len(data.get('message_ids', []))}")

    # 尝试删除所有记录的消息ID
    for message_id in data.get('message_ids', []):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass
            
    # 发送提示 + 跳转按钮
    text = (
        "💥 <b>消息已自动销毁</b>\n\n"
        "请重新获取命令。\n"
        "💡 <b>已购买者无需二次付费</b>，请前往兑换中心查看。"
    )
    keyboard = [[InlineKeyboardButton("🎁 前往兑换中心", callback_data="exchange_home")]]
    
    try:
        await context.bot.send_message(
            chat_id=chat_id, 
            text=text, 
            reply_markup=InlineKeyboardMarkup(keyboard), 
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"发送销毁提示失败: {e}")

async def check_custom_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    text = update.message.text.strip()
    content_list = database_get_content_by_command(text)
    
    if content_list:
        messages_to_delete = []
        user_id = update.effective_chat.id
        
        # 1. 尝试立即删除用户发送的触发命令 (在群组有效，私聊无效但必须尝试)
        try:
            await update.message.delete()
        except Exception:
            pass 

        # 2. 分批发送资源内容 (10条一批)
        batch_size = 10
        for i in range(0, len(content_list), batch_size):
            batch = content_list[i : i + batch_size]
            
            for source_chat, source_message in batch:
                try:
                    message = await context.bot.copy_message(chat_id=user_id, from_chat_id=source_chat, message_id=source_message)
                    messages_to_delete.append(message.message_id)
                except Exception as e:
                    logger.error(f"Copy Message Failed: {e}")
            
            # 如果还有下一批，暂停1秒，防止触发刷屏限制
            if i + batch_size < len(content_list):
                await asyncio.sleep(1)

        # 3. 发送倒计时提示 (8分钟)
        info_message = await context.bot.send_message(
            chat_id=user_id, 
            text="⏳ <b>资源已发送</b>\n\n为保护内容，本消息将在 <b>8分钟</b> 后自动销毁。", 
            parse_mode='HTML'
        )
        messages_to_delete.append(info_message.message_id)
        
        # 4. 设置8分钟 (480秒) 后执行删除任务
        context.job_queue.run_once(
            cleanup_messages_task, 
            480, 
            chat_id=user_id, 
            data={'message_ids': messages_to_delete}
        )
        return
    else:
        # 这里顺便更新一下用户信息，因为用户发消息了
        user = update.effective_user
        if user:
            database_update_user_profile(user.id, user.username, user.first_name)
        await global_start_handler(update, context)

# ================= 管理员后台 =================
def is_admin(update: Update) -> bool:
    return str(update.effective_user.id) == ADMIN_ID

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, is_edit=False):
    keyboard = [
        [InlineKeyboardButton("🖼️ 提取图片 File ID", callback_data='get_file_id')],
        [InlineKeyboardButton("📚 频道转发库", callback_data='manage_library')],
        [InlineKeyboardButton("🛍️ 兑换商品管理", callback_data='manage_products')],
        [InlineKeyboardButton("👥 用户管理 & 记录", callback_data='manage_users')],
    ]
    text = "👑 <b>管理员后台</b>\n输入 /c 可取消当前操作。"
    if is_edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def admin_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update): await admin_panel(update, context)

async def admin_ask_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query: await update.callback_query.answer()
    await update.effective_message.reply_text("📤 请发送图片/文件", parse_mode='HTML')
    return ADMIN_WAITING_FOR_PHOTO

async def admin_get_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_id = "未知"
    if update.message.photo: file_id = update.message.photo[-1].file_id
    elif update.message.document: file_id = update.message.document.file_id
    await update.message.reply_text(f"✅ ID:\n<code>{file_id}</code>", parse_mode='HTML')
    await admin_panel(update, context)
    return ConversationHandler.END

# --- 用户管理相关 (新增) ---
async def manage_users_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    users = database_get_all_users(limit=20)
    
    text = "👥 <b>用户管理 (最近20位)</b>\n点击查看兑换记录。"
    keyboard = []
    
    if not users:
        text += "\n\n暂无用户数据。"
    else:
        for u_id, u_first, u_user, u_points in users:
            display_name = u_first if u_first else "用户"
            if u_user: display_name += f" (@{u_user})"
            # 按钮文本: [ID] 名字
            btn_text = f"[{u_id}] {display_name}"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"view_user_{u_id}")])
            
    keyboard.append([InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def view_user_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    target_user_id = int(query.data.split('_')[-1])
    await query.answer()
    
    # 获取用户基础信息
    user_info = database_get_points_info(target_user_id)
    # 获取兑换历史
    history = database_get_user_redemption_history(target_user_id)
    
    text = f"👤 <b>用户详情</b>\n\nID: <code>{target_user_id}</code>\n"
    text += f"当前积分: <b>{user_info['points']}</b>\n"
    
    text += "\n🎁 <b>兑换记录:</b>\n"
    if not history:
        text += "暂无兑换记录。"
    else:
        for product_name, time in history:
            time_str = time.strftime("%Y-%m-%d %H:%M")
            text += f"• {time_str} - {product_name}\n"
            
    keyboard = [[InlineKeyboardButton("🔙 返回用户列表", callback_data="manage_users")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

# --- 商品管理 ---
async def products_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    products = database_get_products()
    keyboard = [[InlineKeyboardButton("➕ 上架新商品", callback_data="product_add_new")]]
    for pid, name, cost in products:
        keyboard.append([InlineKeyboardButton(f"🗑️ 下架: {name}", callback_data=f"product_delete_{pid}")])
    keyboard.append([InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")])
    await query.edit_message_text("🛍️ <b>兑换商品管理</b>\n点击商品进行下架。", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def product_start_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("⌨️ <b>请输入商品名称</b>", parse_mode='HTML')
    return PRODUCT_INPUT_NAME

async def product_save_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    context.user_data['product_name'] = name
    await update.message.reply_text(f"💰 商品：<b>{name}</b>\n\n请输入兑换所需积分 (数字):", parse_mode='HTML')
    return PRODUCT_INPUT_COST

async def product_save_cost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        cost = int(update.message.text.strip())
        context.user_data['product_cost'] = cost
        await update.message.reply_text("📤 <b>请发送商品内容</b>\n支持文本、图片、视频、文件。", parse_mode='HTML')
        return PRODUCT_INPUT_CONTENT
    except ValueError:
        await update.message.reply_text("❌ 请输入有效的数字。", parse_mode='HTML')
        return PRODUCT_INPUT_COST

async def product_save_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = context.user_data['product_name']
    cost = context.user_data['product_cost']
    content_type = "text"
    content_text = None
    file_id = None
    if update.message.text:
        content_type = "text"
        content_text = update.message.text
    elif update.message.photo:
        content_type = "photo"
        file_id = update.message.photo[-1].file_id
    elif update.message.video:
        content_type = "video"
        file_id = update.message.video.file_id
    elif update.message.document:
        content_type = "document"
        file_id = update.message.document.file_id
    database_add_product(name, cost, content_type, content_text, file_id)
    await update.message.reply_text(f"✅ <b>商品已上架</b>\n名称：{name}\n价格：{cost}", parse_mode='HTML')
    await admin_panel(update, context)
    return ConversationHandler.END

async def product_confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    product_id = int(query.data.split('_')[-1])
    database_delete_product(product_id)
    await query.answer("✅ 商品已下架", show_alert=True)
    update.callback_query.data = "manage_products"
    await products_menu(update, context)

# --- 转发库管理 ---
async def library_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    commands = database_get_library_commands()
    keyboard = [[InlineKeyboardButton("➕ 添加", callback_data="library_add_new")]]
    for cmd in commands: keyboard.append([InlineKeyboardButton(f"📂 {cmd}", callback_data=f"library_view_{cmd}")])
    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="back_to_admin")])
    await query.edit_message_text("📚 <b>转发库</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def library_start_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("⌨️ 输入命令名", parse_mode='HTML')
    return LIBRARY_INPUT_COMMAND_NAME

async def library_save_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    command_name = update.message.text.strip()
    context.user_data['temp_command'] = command_name
    context.user_data['temp_count'] = 0
    await update.message.reply_text(f"📤 请发送内容到 <b>{command_name}</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 完成", callback_data="library_upload_done")]]), parse_mode='HTML')
    return LIBRARY_UPLOAD_CONTENT

async def library_handle_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    command_name = context.user_data.get('temp_command')
    message_type = "文本" if update.message.text else "媒体"
    database_add_library_content(command_name, update.message.chat_id, update.message.message_id, message_type)
    context.user_data['temp_count'] += 1
    await update.message.reply_text(f"✅ 已接收 {context.user_data['temp_count']} 条", quote=True)
    return LIBRARY_UPLOAD_CONTENT

async def library_finish_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    update.callback_query = query
    await library_menu(update, context)
    return ConversationHandler.END

async def library_view_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    command = query.data.replace("library_view_", "")
    await query.answer()
    content = database_get_content_by_command(command)
    keyboard = [[InlineKeyboardButton("🗑️ 删除", callback_data=f"library_delete_{command}")], [InlineKeyboardButton("🔙 返回", callback_data="manage_library")]]
    await query.edit_message_text(f"📂 <b>{command}</b>: {len(content)} 条", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def library_confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    command = query.data.replace("library_delete_", "")
    database_delete_command(command)
    update.callback_query.data = "manage_library"
    await library_menu(update, context)

async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("已取消")
    await admin_panel(update, context)
    return ConversationHandler.END

async def back_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await admin_panel(update, context, is_edit=True)

# ================= 主程序入口 =================
if __name__ == '__main__':
    init_database()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    admin_id_conversation = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_ask_photo, pattern='^get_file_id$')],
        states={ADMIN_WAITING_FOR_PHOTO: [MessageHandler(filters.ALL, admin_get_photo)]},
        fallbacks=[CommandHandler('cancel', admin_cancel), CommandHandler('c', admin_cancel)],
    )
    
    admin_library_conversation = ConversationHandler(
        entry_points=[CallbackQueryHandler(library_start_add, pattern='^library_add_new$')],
        states={
            LIBRARY_INPUT_COMMAND_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, library_save_name)],
            LIBRARY_UPLOAD_CONTENT: [
                # 修复核心：优先监听按钮回调，防止被 MessageHandler 拦截
                CallbackQueryHandler(library_finish_upload, pattern='^library_upload_done$'), 
                MessageHandler(filters.ALL & ~filters.COMMAND & ~filters.StatusUpdate.ALL, library_handle_upload)
            ]
        },
        fallbacks=[CommandHandler('cancel', admin_cancel), CommandHandler('c', admin_cancel)],
    )

    admin_product_conversation = ConversationHandler(
        entry_points=[CallbackQueryHandler(product_start_add, pattern='^product_add_new$')],
        states={
            PRODUCT_INPUT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, product_save_name)],
            PRODUCT_INPUT_COST: [MessageHandler(filters.TEXT & ~filters.COMMAND, product_save_cost)],
            PRODUCT_INPUT_CONTENT: [MessageHandler(filters.ALL & ~filters.COMMAND, product_save_content)]
        },
        fallbacks=[CommandHandler('cancel', admin_cancel), CommandHandler('c', admin_cancel)],
    )

    verify_conversation = ConversationHandler(
        entry_points=[CallbackQueryHandler(ask_order_id_handler, pattern='^i_paid$')],
        states={VERIFY_INPUT_ORDER_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_order_input)]},
        fallbacks=[CommandHandler('start', global_start_handler)],
    )

    points_conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(points_wechat_ask, pattern='^points_wechat_paid$'),
            CallbackQueryHandler(points_alipay_ask, pattern='^points_alipay_paid$')
        ],
        states={
            POINTS_INPUT_WECHAT_ORDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, points_wechat_process)],
            POINTS_INPUT_ALIPAY_ORDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, points_alipay_process)]
        },
        fallbacks=[
            CommandHandler('start', global_start_handler),
            CommandHandler('jf', points_menu_handler),
            CallbackQueryHandler(points_menu_handler, pattern='^back_jf$')
        ]
    )

    # 注册命令处理器
    app.add_handler(CommandHandler("admin", admin_start_command))
    app.add_handler(CommandHandler("id", admin_ask_photo))
    app.add_handler(admin_id_conversation)
    app.add_handler(admin_library_conversation)
    app.add_handler(admin_product_conversation)

    # 管理员按钮回调
    app.add_handler(CallbackQueryHandler(library_menu, pattern='^manage_library$'))
    app.add_handler(CallbackQueryHandler(library_view_command, pattern='^library_view_'))
    app.add_handler(CallbackQueryHandler(library_confirm_delete, pattern='^library_delete_'))
    app.add_handler(CallbackQueryHandler(products_menu, pattern='^manage_products$'))
    app.add_handler(CallbackQueryHandler(product_confirm_delete, pattern='^product_delete_'))
    # 新增用户管理回调
    app.add_handler(CallbackQueryHandler(manage_users_menu, pattern='^manage_users$'))
    app.add_handler(CallbackQueryHandler(view_user_details, pattern='^view_user_'))
    
    app.add_handler(CallbackQueryHandler(back_to_admin, pattern='^back_to_admin$'))

    # 用户命令处理器
    app.add_handler(CommandHandler('jf', points_menu_handler))
    app.add_handler(CommandHandler('dh', exchange_menu_handler))
    app.add_handler(CallbackQueryHandler(verify_click_handler, pattern='^start_verify$'))
    app.add_handler(verify_conversation)
    
    # 用户积分系统回调
    app.add_handler(CallbackQueryHandler(points_menu_handler, pattern='^(points_home|back_jf)$'))
    app.add_handler(CallbackQueryHandler(global_start_handler, pattern='^back_home$'))
    app.add_handler(CallbackQueryHandler(points_checkin_handler, pattern='^points_checkin$'))
    app.add_handler(CallbackQueryHandler(points_history_handler, pattern='^points_history$'))
    app.add_handler(CallbackQueryHandler(points_recharge_menu, pattern='^points_recharge$'))
    app.add_handler(CallbackQueryHandler(points_disabled_handler, pattern='^points_disabled_'))
    app.add_handler(CallbackQueryHandler(points_wechat_start, pattern='^points_pay_wechat$'))
    app.add_handler(CallbackQueryHandler(points_alipay_start, pattern='^points_pay_alipay$'))
    app.add_handler(points_conversation)

    # 用户兑换系统回调
    app.add_handler(CallbackQueryHandler(exchange_menu_handler, pattern='^exchange_home$'))
    app.add_handler(CallbackQueryHandler(exchange_confirm_buy, pattern='^exchange_buy_ask_'))
    app.add_handler(CallbackQueryHandler(exchange_execute_buy, pattern='^exchange_do_buy_'))
    app.add_handler(CallbackQueryHandler(exchange_view_owned, pattern='^exchange_view_'))

    # 核心消息监听
    app.add_handler(CommandHandler('start', global_start_handler))
    # 优先监听是否为自定义命令
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_custom_command))
    # 兜底
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, global_start_handler))

    print("Bot is running with User Management & Redemption Logs...")
    app.run_polling()
