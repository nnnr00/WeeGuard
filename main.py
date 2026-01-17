import os
import logging
import psycopg2
import asyncio
import random
from datetime import datetime, timedelta, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, 
    CallbackQueryHandler, filters, ConversationHandler
)

# ================= 配置区域 =================
BOT_TOKEN = os.getenv("BOT_TOKEN") 
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

# --- 图片配置 (请填入 File ID) ---
VIP_IMAGE_FILE_ID = ""  
TUTORIAL_IMAGE_FILE_ID = "" 
WECHAT_PAY_IMAGE = ""       
WECHAT_TUTORIAL_IMAGE = ""  
ALIPAY_PAY_IMAGE = ""       
ALIPAY_TUTORIAL_IMAGE = ""  

GROUP_LINK = "https://t.me/YourGroupLink" 

# ================= 日志设置 =================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================= 状态定义 =================
# VIP验证
WAITING_FOR_ORDER = 1
# 充值
WAITING_RECHARGE_ORDER = 20

# 管理员后台状态
ADMIN_SELECT = 10
ADMIN_GET_FILE = 11
ADMIN_LIB_MENU = 12
ADMIN_ADD_CMD_NAME = 13
ADMIN_ADD_CONTENT = 14
# 新增：商品管理状态
ADMIN_PROD_MENU = 15
ADMIN_ADD_PROD_NAME = 16
ADMIN_ADD_PROD_COST = 17
ADMIN_ADD_PROD_CONTENT = 18

# ================= 数据库操作 =================
def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. VIP用户状态表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_status (
            user_id BIGINT PRIMARY KEY,
            attempts INT DEFAULT 0,
            locked_until TIMESTAMP
        )
    """)
    
    # 2. 转发命令表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS forward_commands (
            id SERIAL PRIMARY KEY,
            trigger_text TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 3. 转发内容表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS forward_contents (
            id SERIAL PRIMARY KEY,
            cmd_id INT REFERENCES forward_commands(id) ON DELETE CASCADE,
            source_chat_id BIGINT,
            source_message_id INT,
            message_type VARCHAR(20)
        )
    """)
    
    # 4. 积分与充值表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_points (
            user_id BIGINT PRIMARY KEY,
            points INT DEFAULT 0,
            last_checkin DATE,
            wechat_used BOOLEAN DEFAULT FALSE,
            alipay_used BOOLEAN DEFAULT FALSE,
            recharge_attempts INT DEFAULT 0,
            recharge_locked_until TIMESTAMP
        )
    """)

    # 5. 商品表 (新增)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS exchange_products (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            cost INT NOT NULL,
            content_type VARCHAR(20),
            content_value TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 6. 用户兑换记录表 (新增)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_redemptions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            product_id INT,
            redeemed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, product_id)
        )
    """)

    # 7. 积分变动历史表 (新增)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS point_history (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            change_amount INT,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    cur.close()
    conn.close()

# --- 积分历史记录操作 ---
def add_point_history(user_id, amount, reason):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO point_history (user_id, change_amount, reason) VALUES (%s, %s, %s)",
                (user_id, amount, reason))
    conn.commit()
    cur.close()
    conn.close()

def get_user_history(user_id, limit=10):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT change_amount, reason, created_at 
        FROM point_history WHERE user_id = %s 
        ORDER BY id DESC LIMIT %s
    """, (user_id, limit))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

# --- 积分与充值操作 ---
def get_points_data(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT points, last_checkin, wechat_used, alipay_used, recharge_attempts, recharge_locked_until 
        FROM user_points WHERE user_id = %s
    """, (user_id,))
    res = cur.fetchone()
    cur.close()
    conn.close()
    if not res: return (0, None, False, False, 0, None)
    return res

def perform_checkin(user_id):
    data = get_points_data(user_id)
    current_points = data[0]
    last_date = data[1]
    today = date.today()
    
    if last_date == today:
        return False, current_points, 0 
    
    add_pts = random.randint(3, 8)
    new_points = current_points + add_pts
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO user_points (user_id, points, last_checkin) VALUES (%s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET points = %s, last_checkin = %s
    """, (user_id, new_points, today, new_points, today))
    conn.commit()
    cur.close()
    conn.close()
    
    # 记录历史
    add_point_history(user_id, add_pts, "📅 每日签到")
    return True, new_points, add_pts

def success_recharge(user_id, method):
    conn = get_db_connection()
    cur = conn.cursor()
    field = "wechat_used" if method == 'wechat' else "alipay_used"
    amount = 100
    cur.execute(f"""
        UPDATE user_points 
        SET points = points + %s, {field} = TRUE, recharge_attempts = 0, recharge_locked_until = NULL 
        WHERE user_id = %s
    """, (amount, user_id))
    conn.commit()
    cur.close()
    conn.close()
    
    # 记录历史
    reason = "💚 微信充值" if method == 'wechat' else "💙 支付宝充值"
    add_point_history(user_id, amount, reason)

# --- 商品与兑换操作 (新增) ---
def add_product(name, cost, c_type, c_val):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO exchange_products (name, cost, content_type, content_value) 
        VALUES (%s, %s, %s, %s)
    """, (name, cost, c_type, c_val))
    conn.commit()
    cur.close()
    conn.close()

def get_all_products():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name, cost FROM exchange_products ORDER BY id ASC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def delete_product(prod_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM exchange_products WHERE id = %s", (prod_id,))
    cur.execute("DELETE FROM user_redemptions WHERE product_id = %s", (prod_id,))
    conn.commit()
    cur.close()
    conn.close()

def get_product_detail(prod_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT name, cost, content_type, content_value FROM exchange_products WHERE id = %s", (prod_id,))
    res = cur.fetchone()
    cur.close()
    conn.close()
    return res

def check_is_redeemed(user_id, prod_id):
    """检查是否已兑换"""
    if str(prod_id) == 'test': return False # 测试商品不记录
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM user_redemptions WHERE user_id = %s AND product_id = %s", (user_id, prod_id))
    res = cur.fetchone()
    cur.close()
    conn.close()
    return bool(res)

def execute_redemption(user_id, prod_id, cost, name):
    """执行兑换：扣分、记录"""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 扣分
        cur.execute("UPDATE user_points SET points = points - %s WHERE user_id = %s", (cost, user_id))
        # 记录兑换
        cur.execute("INSERT INTO user_redemptions (user_id, product_id) VALUES (%s, %s)", (user_id, prod_id))
        conn.commit()
        success = True
    except Exception as e:
        conn.rollback()
        success = False
    finally:
        cur.close()
        conn.close()
    
    if success:
        add_point_history(user_id, -cost, f"🎁 兑换: {name}")
    return success

# --- 原有VIP和转发库DB函数保持不变 ---
def get_user_state(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT attempts, locked_until FROM user_status WHERE user_id = %s", (user_id,))
    result = cur.fetchone()
    cur.close()
    conn.close()
    return (result[0], result[1]) if result else (0, None)

def update_fail_attempt(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO user_status (user_id, attempts) VALUES (%s, 1)
        ON CONFLICT (user_id) DO UPDATE SET attempts = user_status.attempts + 1
    """, (user_id,))
    conn.commit()
    cur.close()
    conn.close()

def lock_user(user_id, hours=5):
    unlock_time = datetime.now() + timedelta(hours=hours)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO user_status (user_id, attempts, locked_until) VALUES (%s, 2, %s)
        ON CONFLICT (user_id) DO UPDATE SET locked_until = %s
    """, (user_id, unlock_time, unlock_time))
    conn.commit()
    cur.close()
    conn.close()

def reset_user(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM user_status WHERE user_id = %s", (user_id,))
    conn.commit()
    cur.close()
    conn.close()

def lock_recharge(user_id):
    unlock_time = datetime.now() + timedelta(hours=5)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE user_points SET recharge_attempts = 2, recharge_locked_until = %s WHERE user_id = %s
    """, (unlock_time, user_id))
    conn.commit()
    cur.close()
    conn.close()

def fail_recharge_attempt(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO user_points (user_id, recharge_attempts) VALUES (%s, 1)
        ON CONFLICT (user_id) DO UPDATE SET recharge_attempts = user_points.recharge_attempts + 1
    """, (user_id,))
    conn.commit()
    cur.close()
    conn.close()

# 转发库DB (Admin)
def add_command(trigger):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO forward_commands (trigger_text) VALUES (%s) RETURNING id", (trigger,))
        cmd_id = cur.fetchone()[0]
        conn.commit()
        return cmd_id
    except:
        conn.rollback()
        return None
    finally:
        cur.close()
        conn.close()

def add_content(cmd_id, chat_id, message_id, msg_type):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO forward_contents (cmd_id, source_chat_id, source_message_id, message_type) VALUES (%s, %s, %s, %s)",
        (cmd_id, chat_id, message_id, msg_type))
    conn.commit()
    cur.close()
    conn.close()

def get_all_commands():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, trigger_text FROM forward_commands ORDER BY id DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def delete_command(cmd_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM forward_commands WHERE id = %s", (cmd_id,))
    conn.commit()
    cur.close()
    conn.close()

def get_command_content(trigger_text):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT c.source_chat_id, c.source_message_id 
        FROM forward_contents c
        JOIN forward_commands cmd ON c.cmd_id = cmd.id
        WHERE cmd.trigger_text = %s
        ORDER BY c.id ASC
    """, (trigger_text,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

# ================= 权限与通用功能 =================
def is_admin(user_id):
    return user_id in ADMIN_IDS

async def delete_messages_job(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    chat_id = job_data['chat_id']
    message_ids = job_data['message_ids']
    for msg_id in message_ids:
        try: await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except: pass
    await context.bot.send_message(chat_id=chat_id, text="⏳ <b>消息存在时间有限，请到购买处重新获取（已购买不需要二次付费就可看见消息）。</b>", parse_mode='HTML')
    await asyncio.sleep(2)
    await send_home_logic(context.bot, chat_id)

async def send_home_logic(bot, chat_id, user_id=None):
    if user_id:
        _, locked_until = get_user_state(user_id)
        if locked_until and locked_until > datetime.now():
            remaining = locked_until - datetime.now()
            hours_left = int(remaining.total_seconds() / 3600) + 1
            await bot.send_message(chat_id, f"🚫 系统风控中\n\n您已连续验证失败，请在 {hours_left} 小时后重试。")
            return

    text = (
        "👋 <b>欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~</b>\n\n"
        "📢 <b>小卫小卫，守门员小卫！</b>\n"
        "一键入群，小卫帮你搞定！\n"
        "新人来报到，小卫查身份！"
    )
    keyboard = [
        [InlineKeyboardButton("🚀 开始验证", callback_data='start_verify')],
        [InlineKeyboardButton("💰 我的积分", callback_data='points_home')]
    ]
    await bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

# ================= 积分中心逻辑 =================

async def points_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    if query: await query.answer()

    data = get_points_data(user_id)
    points = data[0]

    text = (
        f"💰 <b>积分中心</b>\n\n"
        f"👤 用户ID：<code>{user_id}</code>\n"
        f"💎 当前积分：<b>{points}</b>\n\n"
        "👇 请选择操作："
    )
    keyboard = [
        [InlineKeyboardButton("📅 每日签到", callback_data='daily_sign')],
        [InlineKeyboardButton("💳 积分充值", callback_data='recharge_menu')],
        [InlineKeyboardButton("🎁 积分兑换", callback_data='exchange_menu')], # 第三按钮
        [InlineKeyboardButton("📜 余额记录", callback_data='point_history')], # 第四按钮
        [InlineKeyboardButton("🏠 返回首页", callback_data='go_home')]
    ]
    
    if query: await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    return ConversationHandler.END

async def daily_sign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    success, total, added = perform_checkin(user_id)
    if success:
        await query.message.reply_text(f"🎉 <b>签到成功！</b>\n获得：{added} 积分\n当前：{total} 积分", parse_mode='HTML')
    else:
        await query.message.reply_text(f"📅 <b>今日已签到</b>\n\n明天再来吧！\n当前积分：{total}", parse_mode='HTML')
    return ConversationHandler.END

async def point_history_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """余额记录"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    data = get_points_data(user_id)
    current_points = data[0]
    
    history = get_user_history(user_id, limit=10)
    
    text = f"📜 <b>余额与使用记录</b>\n\n💎 当前余额：<b>{current_points}</b>\n\n<b>--- 最近 10 条记录 ---</b>\n"
    
    if not history:
        text += "暂无记录"
    else:
        for amount, reason, created_at in history:
            symbol = "+" if amount > 0 else ""
            time_str = created_at.strftime('%m-%d %H:%M')
            text += f"▪️ <code>{time_str}</code> | <b>{symbol}{amount}</b> | {reason}\n"
            
    keyboard = [[InlineKeyboardButton("🔙 返回积分中心", callback_data='points_home')]]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

# ================= 商品兑换逻辑 =================

async def exchange_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/dh 兑换页面"""
    query = update.callback_query
    user_id = update.effective_user.id
    if query: await query.answer()

    data = get_points_data(user_id)
    points = data[0]

    text = (
        f"🎁 <b>积分兑换商城</b>\n\n"
        f"💎 您的积分：<b>{points}</b>\n\n"
        "👇 点击下方商品进行兑换："
    )
    
    keyboard = []
    # 1. 固定测试按钮
    keyboard.append([InlineKeyboardButton("🤡 测试商品 (0积分)", callback_data='redeem_test')])
    
    # 2. 动态加载后台商品
    products = get_all_products()
    for pid, name, cost in products:
        # 检查是否已兑换
        is_owned = check_is_redeemed(user_id, pid)
        status = "✅已拥有" if is_owned else f"💎{cost}"
        btn_text = f"{name} ({status})"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f'redeem_prod_{pid}')])
        
    keyboard.append([InlineKeyboardButton("🔙 返回积分中心", callback_data='points_home')])
    
    if query: await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    return ConversationHandler.END

async def confirm_redemption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理点击商品"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    
    # A. 测试商品逻辑
    if data == 'redeem_test':
        await query.message.reply_text("🤡 <b>测试兑换内容：</b>\n\n哈哈", parse_mode='HTML')
        # 刷新页面
        return await exchange_menu(update, context)

    # B. 真实商品逻辑
    prod_id = int(data.split('_')[-1])
    
    # 检查是否已拥有
    if check_is_redeemed(user_id, prod_id):
        # 直接发送内容
        prod = get_product_detail(prod_id) # name, cost, type, value
        await send_product_content(query, prod[2], prod[3])
        return await exchange_menu(update, context)
    
    # 未拥有 -> 确认购买
    prod = get_product_detail(prod_id)
    if not prod:
        await query.message.reply_text("❌ 商品已下架")
        return await exchange_menu(update, context)
        
    name, cost = prod[0], prod[1]
    
    text = (
        f"🛒 <b>确认兑换？</b>\n\n"
        f"📦 商品：<b>{name}</b>\n"
        f"💰 价格：<b>{cost} 积分</b>\n"
    )
    keyboard = [
        [InlineKeyboardButton("✅ 确认兑换", callback_data=f'do_buy_{prod_id}')],
        [InlineKeyboardButton("❌ 取消", callback_data='exchange_menu')]
    ]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def execute_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """执行扣款和发货"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    prod_id = int(query.data.split('_')[-1])
    
    prod = get_product_detail(prod_id)
    if not prod:
        await query.message.reply_text("❌ 商品已下架")
        return await exchange_menu(update, context)

    name, cost, c_type, c_value = prod
    
    # 检查余额
    points_data = get_points_data(user_id)
    if points_data[0] < cost:
        await query.answer("❌ 余额不足", show_alert=True)
        await query.message.reply_text("⚠️ <b>余额不足，请充值或签到。</b>", parse_mode='HTML')
        return await exchange_menu(update, context)
        
    # 执行交易
    success = execute_redemption(user_id, prod_id, cost, name)
    
    if success:
        await query.message.reply_text(f"🎉 <b>兑换成功！</b>\n已扣除 {cost} 积分。", parse_mode='HTML')
        await send_product_content(query, c_type, c_value)
    else:
        await query.message.reply_text("❌ 系统繁忙，请重试。")
        
    return await exchange_menu(update, context)

async def send_product_content(query, c_type, c_value):
    """辅助函数：发送内容"""
    try:
        if c_type == 'text':
            await query.message.reply_text(f"📦 <b>商品内容：</b>\n{c_value}", parse_mode='HTML')
        elif c_type == 'photo':
            await query.message.reply_photo(photo=c_value, caption="📦 <b>商品内容</b>", parse_mode='HTML')
        elif c_type == 'video':
            await query.message.reply_video(video=c_value, caption="📦 <b>商品内容</b>", parse_mode='HTML')
    except Exception as e:
        await query.message.reply_text(f"⚠️ 内容发送失败，请联系管理员。\nError: {e}")

# ================= 充值流程 =================
async def recharge_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    data = get_points_data(user_id)
    locked_until = data[5]
    if locked_until and locked_until > datetime.now():
        hours = int((locked_until - datetime.now()).total_seconds() / 3600) + 1
        await query.message.edit_text(f"🚫 <b>充值通道锁定中</b>\n\n请在 {hours} 小时后重试。", parse_mode='HTML')
        return ConversationHandler.END

    text = (
        "💳 <b>积分充值中心</b>\n\n"
        "🔥 <b>限时特惠：5元 = 100积分</b>\n\n"
        "⚠️ <b>温馨提示：</b>\n"
        "微信和支付宝每位用户<b>仅限充值一次</b>，请勿重复操作！"
    )
    keyboard = [
        [InlineKeyboardButton("💚 微信充值", callback_data='pay_wechat')],
        [InlineKeyboardButton("💙 支付宝充值", callback_data='pay_alipay')],
        [InlineKeyboardButton("🔙 返回积分中心", callback_data='points_home')]
    ]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    return ConversationHandler.END

async def start_recharge_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    mode = query.data
    
    data = get_points_data(user_id)
    if data[5] and data[5] > datetime.now():
        await query.message.reply_text("🚫 充值锁定中")
        return ConversationHandler.END
    
    if mode == 'pay_wechat':
        if data[2]: 
            await query.message.reply_text("⚠️ 微信充值机会已使用。")
            return ConversationHandler.END
        img_id = WECHAT_PAY_IMAGE
        context.user_data['recharge_type'] = 'wechat'
    else:
        if data[3]:
            await query.message.reply_text("⚠️ 支付宝充值机会已使用。")
            return ConversationHandler.END
        img_id = ALIPAY_PAY_IMAGE
        context.user_data['recharge_type'] = 'alipay'
        
    text = "🔥 <b>充值确认：5元 = 100积分</b>\n⚠️ 仅限一次！"
    keyboard = [[InlineKeyboardButton("✅ 我已支付，开始验证", callback_data='paid_confirm_recharge')]]
    if img_id: await query.message.reply_photo(img_id, caption=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    else: await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    return ConversationHandler.END

async def ask_recharge_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rtype = context.user_data.get('recharge_type')
    if rtype == 'wechat':
        text = "📝 请在微信支付账单找到<b>交易单号</b>并发送。"
        img = WECHAT_TUTORIAL_IMAGE
    else:
        text = "📝 请在支付宝账单详情找到<b>商家订单号</b>并发送。"
        img = ALIPAY_TUTORIAL_IMAGE
    
    if img: await query.message.reply_photo(img, caption=text, parse_mode='HTML')
    else: await query.message.reply_text(text, parse_mode='HTML')
    return WAITING_RECHARGE_ORDER

async def verify_recharge_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    order_text = update.message.text.strip()
    rtype = context.user_data.get('recharge_type')
    
    valid = False
    if rtype == 'wechat' and order_text.startswith("4200") and order_text.isdigit(): valid = True
    elif rtype == 'alipay' and order_text.startswith("4768") and order_text.isdigit(): valid = True
        
    if valid:
        success_recharge(user_id, rtype)
        await update.message.reply_text("🎉 <b>充值成功！</b>\n已到账 100 积分。", parse_mode='HTML')
        context.user_data.pop('recharge_type', None)
        return await points_home(update, context)
    else:
        fail_recharge_attempt(user_id)
        attempts = get_points_data(user_id)[4]
        if attempts >= 2:
            lock_recharge(user_id)
            await update.message.reply_text("❌ 失败2次，锁定5小时。", parse_mode='HTML')
            context.user_data.pop('recharge_type', None)
            return await points_home(update, context)
        else:
            await update.message.reply_text("❌ 识别失败，请核对。还剩 1 次机会。", parse_mode='HTML')
            return WAITING_RECHARGE_ORDER

# ================= 管理员后台 (含商品管理) =================

async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id): return None
    
    keyboard = [
        [InlineKeyboardButton("📂 获取 File ID", callback_data='admin_file_id')],
        [InlineKeyboardButton("📚 频道转发库", callback_data='admin_lib_menu')],
        [InlineKeyboardButton("🛍 商品管理 (兑换)", callback_data='admin_prod_menu')], # 新增
        [InlineKeyboardButton("❌ 退出后台", callback_data='admin_exit')]
    ]
    text = "🔧 <b>管理员控制台</b>\n\n请选择操作："
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    return ADMIN_SELECT

# --- 商品管理流程 ---
async def admin_prod_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    prods = get_all_products()
    keyboard = [[InlineKeyboardButton("➕ 添加新商品", callback_data='add_new_prod')]]
    
    for pid, name, cost in prods:
        keyboard.append([InlineKeyboardButton(f"{name} ({cost}分)", callback_data=f'manage_prod_{pid}')])
        
    keyboard.append([InlineKeyboardButton("🔙 返回主菜单", callback_data='back_to_admin')])
    await query.message.edit_text("🛍 <b>商品管理</b>\n\n点击商品进行删除，或点击添加。", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    return ADMIN_PROD_MENU

async def admin_add_prod_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("✏️ <b>请输入商品名称：</b>\n例如：高级教程、VIP视频")
    return ADMIN_ADD_PROD_NAME

async def admin_save_prod_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_prod_name'] = update.message.text
    await update.message.reply_text("💰 <b>请输入兑换所需积分：</b>\n(请输入数字)")
    return ADMIN_ADD_PROD_COST

async def admin_save_prod_cost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        cost = int(update.message.text)
    except:
        await update.message.reply_text("❌ 必须是数字，请重新输入：")
        return ADMIN_ADD_PROD_COST
    context.user_data['new_prod_cost'] = cost
    await update.message.reply_text("📥 <b>请发送商品内容：</b>\n支持文本、图片、视频。")
    return ADMIN_ADD_PROD_CONTENT

async def admin_save_prod_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    c_type = 'text'
    c_val = msg.text
    
    if msg.photo:
        c_type = 'photo'
        c_val = msg.photo[-1].file_id
    elif msg.video:
        c_type = 'video'
        c_val = msg.video.file_id
    
    name = context.user_data['new_prod_name']
    cost = context.user_data['new_prod_cost']
    
    add_product(name, cost, c_type, c_val)
    
    await update.message.reply_text(f"✅ <b>商品【{name}】已上架！</b>\n价格：{cost}积分", parse_mode='HTML')
    
    # 返回菜单需要模拟一下
    keyboard = [[InlineKeyboardButton("🔙 返回商品管理", callback_data='admin_prod_menu')]]
    await update.message.reply_text("点击返回", reply_markup=InlineKeyboardMarkup(keyboard))
    return ADMIN_PROD_MENU

async def admin_manage_prod(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pid = int(query.data.split('_')[-1])
    prod = get_product_detail(pid)
    
    if not prod:
        await query.message.edit_text("❌ 商品不存在")
        return await admin_prod_menu(update, context)
        
    text = (
        f"📦 <b>商品详情</b>\n\n"
        f"名称：{prod[0]}\n"
        f"价格：{prod[1]} 积分\n\n"
        "❓ <b>确认下架（删除）此商品？</b>"
    )
    keyboard = [
        [InlineKeyboardButton("🗑 是，确认删除", callback_data=f'confirm_del_prod_{pid}')],
        [InlineKeyboardButton("🔙 取消", callback_data='admin_prod_menu')]
    ]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    return ADMIN_PROD_MENU

async def admin_delete_prod_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pid = int(query.data.split('_')[-1])
    delete_product(pid)
    await query.answer("🗑 已删除", show_alert=True)
    return await admin_prod_menu(update, context)

# --- 占位补全 Admin 其他功能 (保持代码完整性) ---
async def admin_file_id_entry(u, c):
    q=u.callback_query
    await q.answer()
    await q.message.edit_text("🖼 请发送文件获取ID", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data='back_to_admin')]]))
    return ADMIN_GET_FILE
async def admin_process_file(u, c):
    fid = "Unknown"
    if u.message.photo: fid=u.message.photo[-1].file_id
    elif u.message.video: fid=u.message.video.file_id
    elif u.message.document: fid=u.message.document.file_id
    await u.message.reply_text(f"ID: <code>{fid}</code>", parse_mode='HTML')
    return ADMIN_GET_FILE
async def admin_lib_menu(u, c):
    q=u.callback_query
    await q.answer()
    cmds=get_all_commands()
    kb=[[InlineKeyboardButton("➕",callback_data='add_new_cmd')]]
    for i,t in cmds: kb.append([InlineKeyboardButton(f"{t}",callback_data=f'del_cmd_{i}')]) # Simplified view
    kb.append([InlineKeyboardButton("🔙", callback_data='back_to_admin')])
    await q.message.edit_text("📚 转发库管理", reply_markup=InlineKeyboardMarkup(kb))
    return ADMIN_LIB_MENU
async def admin_add_cmd_start(u,c):
    q=u.callback_query; await q.answer()
    await q.message.edit_text("✏️ 输入触发词:")
    return ADMIN_ADD_CMD_NAME
async def admin_save_cmd_name(u,c):
    t=u.message.text
    cid=add_command(t)
    if not cid: 
        await u.message.reply_text("❌ 已存在")
        return ADMIN_ADD_CMD_NAME
    c.user_data.update({'cur_cmd_id':cid, 'cnt':0})
    await u.message.reply_text("📥 发送内容 (Max 100)，完成后点按钮。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 完成",callback_data='finish_binding')]]))
    return ADMIN_ADD_CONTENT
async def admin_save_content(u,c):
    cnt=c.user_data.get('cnt',0)
    if cnt>=100: return
    mt='text'
    if u.message.photo: mt='photo'
    elif u.message.video: mt='video'
    add_content(c.user_data['cur_cmd_id'], u.message.chat_id, u.message.message_id, mt)
    c.user_data['cnt']=cnt+1
    return ADMIN_ADD_CONTENT
async def admin_finish_binding(u,c):
    q=u.callback_query; await q.answer()
    await q.message.reply_text("✅ 绑定完成")
    return await admin_lib_menu(u,c)
async def admin_delete_cmd(u,c):
    q=u.callback_query; cid=q.data.split('_')[-1]
    delete_command(cid); await q.answer("已删除")
    return await admin_lib_menu(u,c)
async def admin_exit(u,c):
    q=u.callback_query; await q.answer()
    await send_home_logic(c.bot, q.message.chat_id)
    return ConversationHandler.END
async def back_to_admin(u,c):
    return await admin_start(u,c)

# --- VIP验证占位 (保持原有) ---
async def handle_start_verify_click(u, c):
    q=u.callback_query; await q.answer()
    uid=q.from_user.id
    _, l=get_user_state(uid)
    if l and l>datetime.now(): 
        await q.message.reply_text("🚫 锁定中")
        return ConversationHandler.END
    kb=[[InlineKeyboardButton("✅ 我已付款，开始验证", callback_data='paid_confirm')]]
    txt="💎 VIP权益..."
    if VIP_IMAGE_FILE_ID: await q.message.reply_photo(VIP_IMAGE_FILE_ID, caption=txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    else: await q.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    return ConversationHandler.END
async def handle_paid_click(u,c):
    q=u.callback_query; await q.answer()
    txt="🔍 请输入订单号..."
    if TUTORIAL_IMAGE_FILE_ID: await q.message.reply_photo(TUTORIAL_IMAGE_FILE_ID, caption=txt, parse_mode='HTML')
    else: await q.message.reply_text(txt, parse_mode='HTML')
    return WAITING_FOR_ORDER
async def check_order(u,c):
    uid=u.effective_user.id; txt=u.message.text.strip()
    if txt.startswith("20260"):
        reset_user(uid)
        await u.message.reply_text("🎉 验证成功!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("加入群组", url=GROUP_LINK)]]))
        await send_home(u,c)
        return ConversationHandler.END
    update_fail_attempt(uid)
    att,_=get_user_state(uid)
    if att>=2:
        lock_user(uid)
        await u.message.reply_text("❌ 锁定5小时")
        await send_home(u,c)
        return ConversationHandler.END
    await u.message.reply_text("❌ 错误，请重试")
    return WAITING_FOR_ORDER

# ================= 消息入口 =================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await send_home(update, context)

async def send_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    if update.callback_query: 
        await update.callback_query.answer()
        cid = update.callback_query.message.chat_id
    await send_home_logic(context.bot, cid, update.effective_user.id)
    return ConversationHandler.END

async def catch_all_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    uid = update.effective_user.id
    if txt == '/admin' and is_admin(uid): return await admin_start(update, context)
    if txt == '/jf': return await points_home(update, context)
    if txt == '/dh': return await exchange_menu(update, context) # 新增快捷指令
    
    if txt:
        conts = get_command_content(txt)
        if conts:
            sids = [update.message.message_id]
            for sc, sm in conts:
                try: m=await context.bot.copy_message(update.effective_chat.id, sc, sm); sids.append(m.message_id)
                except: pass
            m=await update.message.reply_text("✅ 发送完毕")
            sids.append(m.message_id)
            context.job_queue.run_once(delete_messages_job, 1200, data={'chat_id':update.effective_chat.id,'message_ids':sids})
            await asyncio.sleep(2)
            await send_home(update, context)
            return
    await send_home(update, context)

# ================= 主程序 =================
if __name__ == '__main__':
    init_db()
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # 1. 积分充值流程
    recharge_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_recharge_flow, pattern='^pay_(wechat|alipay)$')],
        states={WAITING_RECHARGE_ORDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, verify_recharge_order)]},
        fallbacks=[CommandHandler('start', start_command), CallbackQueryHandler(points_home, pattern='^points_home$')]
    )

    # 2. VIP验证流程
    vip_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_paid_click, pattern='^paid_confirm$')],
        states={WAITING_FOR_ORDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_order)]},
        fallbacks=[CommandHandler('start', start_command)],
    )

    # 3. Admin流程
    admin_conv = ConversationHandler(
        entry_points=[CommandHandler('admin', admin_start)],
        states={
            ADMIN_SELECT: [
                CallbackQueryHandler(admin_file_id_entry, pattern='^admin_file_id$'),
                CallbackQueryHandler(admin_lib_menu, pattern='^admin_lib_menu$'),
                CallbackQueryHandler(admin_prod_menu, pattern='^admin_prod_menu$'), # 新入口
                CallbackQueryHandler(admin_exit, pattern='^admin_exit$')
            ],
            ADMIN_GET_FILE: [MessageHandler(filters.ALL & ~filters.COMMAND, admin_process_file), CallbackQueryHandler(back_to_admin, pattern='^back_to_admin$')],
            # 转发库部分
            ADMIN_LIB_MENU: [
                CallbackQueryHandler(admin_add_cmd_start, pattern='^add_new_cmd$'),
                CallbackQueryHandler(admin_delete_cmd, pattern='^del_cmd_'),
                CallbackQueryHandler(back_to_admin, pattern='^back_to_admin$')
            ],
            ADMIN_ADD_CMD_NAME: [MessageHandler(filters.TEXT, admin_save_cmd_name)],
            ADMIN_ADD_CONTENT: [MessageHandler(filters.ALL, admin_save_content), CallbackQueryHandler(admin_finish_binding, pattern='^finish_binding$')],
            # 商品管理部分 (新增)
            ADMIN_PROD_MENU: [
                CallbackQueryHandler(admin_add_prod_start, pattern='^add_new_prod$'),
                CallbackQueryHandler(admin_manage_prod, pattern='^manage_prod_'),
                CallbackQueryHandler(admin_delete_prod_confirm, pattern='^confirm_del_prod_'),
                CallbackQueryHandler(back_to_admin, pattern='^back_to_admin$')
            ],
            ADMIN_ADD_PROD_NAME: [MessageHandler(filters.TEXT, admin_save_prod_name)],
            ADMIN_ADD_PROD_COST: [MessageHandler(filters.TEXT, admin_save_prod_cost)],
            ADMIN_ADD_PROD_CONTENT: [MessageHandler(filters.ALL, admin_save_prod_content)],
        },
        fallbacks=[CommandHandler('start', start_command)]
    )

    application.add_handler(admin_conv)
    application.add_handler(recharge_conv)
    application.add_handler(vip_conv)

    # 按钮与指令
    application.add_handler(CommandHandler('jf', points_home))
    application.add_handler(CommandHandler('dh', exchange_menu)) # 新增
    application.add_handler(CallbackQueryHandler(points_home, pattern='^points_home$'))
    application.add_handler(CallbackQueryHandler(daily_sign, pattern='^daily_sign$'))
    application.add_handler(CallbackQueryHandler(recharge_menu, pattern='^recharge_menu$'))
    application.add_handler(CallbackQueryHandler(exchange_menu, pattern='^exchange_menu$')) # 新增
    application.add_handler(CallbackQueryHandler(point_history_view, pattern='^point_history$')) # 新增
    
    # 商品兑换动作
    application.add_handler(CallbackQueryHandler(confirm_redemption, pattern='^redeem_'))
    application.add_handler(CallbackQueryHandler(execute_buy, pattern='^do_buy_'))

    application.add_handler(CallbackQueryHandler(send_home, pattern='^go_home$'))
    application.add_handler(CallbackQueryHandler(handle_start_verify_click, pattern='^start_verify$'))
    
    application.add_handler(CommandHandler('start', start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, catch_all_message))

    print("Bot is running with Exchange System...")
    application.run_polling()
