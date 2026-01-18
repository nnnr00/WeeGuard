import os
import logging
import psycopg2
import datetime
import random
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

# 【需手动配置区 - 使用 /admin 提取 ID 填入】
VIP_IMAGE_ID = "AgACAgEAAykBA..."    
TUTORIAL_IMAGE_ID = "AgACAgEAAykBA..." 
GROUP_LINK = "https://t.me/your_group_link"

# 积分充值用图
JF_WX_QR_ID = "AgACAgEAAykBA..."        
JF_WX_TUTORIAL_ID = "AgACAgEAAykBA..."  
JF_ALI_QR_ID = "AgACAgEAAykBA..."       
JF_ALI_TUTORIAL_ID = "AgACAgEAAykBA..." 

# ================= 状态机定义 =================
# Admin - 提取ID
ADMIN_WAIT_PHOTO = 1
# Admin - 转发库
LIB_INPUT_CMD_NAME = 2
LIB_UPLOAD_CONTENT = 3
# Admin - 商品管理 (新)
PROD_INPUT_NAME = 4
PROD_INPUT_COST = 5
PROD_INPUT_CONTENT = 6
# User - 验证
VERIFY_INPUT_ORDER = 10
# User - 积分充值
JF_INPUT_WX_ORDER = 20
JF_INPUT_ALI_ORDER = 21

# ================= 日志 =================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================= 数据库层 =================
def get_db_conn():
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        logger.error(f"DB Error: {e}")
        return None

def init_db():
    conn = get_db_conn()
    if conn:
        with conn.cursor() as cur:
            # 1. VIP 验证表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_verification (
                    user_id BIGINT PRIMARY KEY,
                    fail_count INT DEFAULT 0,
                    cooldown_until TIMESTAMP
                );
            """)
            # 2. 转发库表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS forward_library (
                    id SERIAL PRIMARY KEY,
                    trigger_cmd TEXT NOT NULL,
                    source_chat_id BIGINT NOT NULL,
                    source_message_id INT NOT NULL,
                    msg_type TEXT, 
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            # 3. 积分系统表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_points (
                    user_id BIGINT PRIMARY KEY,
                    points INT DEFAULT 0,
                    last_checkin DATE,
                    wx_done BOOLEAN DEFAULT FALSE,
                    ali_done BOOLEAN DEFAULT FALSE,
                    wx_fail INT DEFAULT 0,
                    ali_fail INT DEFAULT 0,
                    wx_cool TIMESTAMP,
                    ali_cool TIMESTAMP
                );
            """)
            # 4. 商品表 (新)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    cost INT NOT NULL,
                    content_type TEXT, -- 'text' or 'media'
                    content_text TEXT, -- if text
                    file_id TEXT,      -- if media
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            # 5. 兑换记录表 (新)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS redemptions (
                    user_id BIGINT,
                    product_id INT,
                    redeemed_at TIMESTAMP DEFAULT NOW(),
                    PRIMARY KEY (user_id, product_id)
                );
            """)
            # 6. 积分流水表 (新)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS point_history (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    amount INT,
                    reason TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            
            # 初始化测试商品
            cur.execute("SELECT COUNT(*) FROM products WHERE name = '测试'")
            if cur.fetchone()[0] == 0:
                cur.execute("INSERT INTO products (name, cost, content_type, content_text) VALUES (%s, %s, %s, %s)", 
                            ("测试", 0, "text", "哈哈"))

            conn.commit()
        conn.close()

# --- 积分与流水相关 ---
def db_log_history(user_id, amount, reason):
    """记录积分流水"""
    conn = get_db_conn()
    if conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO point_history (user_id, amount, reason) VALUES (%s, %s, %s)", (user_id, amount, reason))
            conn.commit()
        conn.close()

def db_get_points_info(user_id):
    conn = get_db_conn()
    if not conn: return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM user_points WHERE user_id = %s", (user_id,))
            res = cur.fetchone()
            if not res:
                cur.execute("INSERT INTO user_points (user_id) VALUES (%s) RETURNING *", (user_id,))
                conn.commit()
                res = cur.fetchone()
            return {
                'points': res[1],
                'last_checkin': res[2],
                'wx_done': res[3],
                'ali_done': res[4],
                'wx_fail': res[5],
                'ali_fail': res[6],
                'wx_cool': res[7],
                'ali_cool': res[8]
            }
    finally:
        conn.close()

def db_checkin(user_id, add_points):
    conn = get_db_conn()
    if conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE user_points SET points = points + %s, last_checkin = %s WHERE user_id = %s", 
                        (add_points, date.today(), user_id))
            conn.commit()
        conn.close()
    db_log_history(user_id, add_points, "每日签到")

def db_add_points(user_id, amount, source="充值"):
    conn = get_db_conn()
    if conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE user_points SET points = points + %s WHERE user_id = %s", (amount, user_id))
            conn.commit()
        conn.close()
    db_log_history(user_id, amount, source)

def db_deduct_points(user_id, amount, reason="兑换"):
    """扣除积分，成功返回True，余额不足返回False"""
    conn = get_db_conn()
    success = False
    if conn:
        with conn.cursor() as cur:
            cur.execute("SELECT points FROM user_points WHERE user_id = %s", (user_id,))
            res = cur.fetchone()
            current = res[0] if res else 0
            
            if current >= amount:
                cur.execute("UPDATE user_points SET points = points - %s WHERE user_id = %s", (amount, user_id))
                conn.commit()
                success = True
        conn.close()
    
    if success:
        db_log_history(user_id, -amount, reason)
    return success

def db_get_history(user_id, limit=10):
    conn = get_db_conn()
    data = []
    if conn:
        with conn.cursor() as cur:
            cur.execute("SELECT amount, reason, created_at FROM point_history WHERE user_id = %s ORDER BY created_at DESC LIMIT %s", (user_id, limit))
            data = cur.fetchall()
        conn.close()
    return data

def db_update_recharge_status(user_id, method, is_success, is_fail_increment=False, lock_hours=0):
    conn = get_db_conn()
    if not conn: return
    try:
        with conn.cursor() as cur:
            if is_success:
                col = f"{method}_done"
                fail_col = f"{method}_fail"
                cur.execute(f"UPDATE user_points SET {col} = TRUE, {fail_col} = 0 WHERE user_id = %s", (user_id,))
            elif is_fail_increment:
                fail_col = f"{method}_fail"
                cool_col = f"{method}_cool"
                if lock_hours > 0:
                    unlock_time = datetime.datetime.now() + timedelta(hours=lock_hours)
                    cur.execute(f"UPDATE user_points SET {fail_col} = 0, {cool_col} = %s WHERE user_id = %s", (unlock_time, user_id))
                else:
                    cur.execute(f"UPDATE user_points SET {fail_col} = {fail_col} + 1 WHERE user_id = %s", (user_id,))
            conn.commit()
    finally:
        conn.close()

# --- 商品与兑换 DB ---
def db_add_product(name, cost, c_type, c_text, c_file_id):
    conn = get_db_conn()
    if conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO products (name, cost, content_type, content_text, file_id) VALUES (%s, %s, %s, %s, %s)", 
                        (name, cost, c_type, c_text, c_file_id))
            conn.commit()
        conn.close()

def db_get_products():
    conn = get_db_conn()
    data = []
    if conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, cost FROM products ORDER BY id ASC")
            data = cur.fetchall()
        conn.close()
    return data

def db_get_product_detail(pid):
    conn = get_db_conn()
    res = None
    if conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM products WHERE id = %s", (pid,))
            res = cur.fetchone() # id, name, cost, type, text, fileid, time
        conn.close()
    return res

def db_delete_product(pid):
    conn = get_db_conn()
    if conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM products WHERE id = %s", (pid,))
            cur.execute("DELETE FROM redemptions WHERE product_id = %s", (pid,))
            conn.commit()
        conn.close()

def db_is_redeemed(user_id, pid):
    conn = get_db_conn()
    redeemed = False
    if conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM redemptions WHERE user_id = %s AND product_id = %s", (user_id, pid))
            if cur.fetchone(): redeemed = True
        conn.close()
    return redeemed

def db_record_redemption(user_id, pid):
    conn = get_db_conn()
    if conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO redemptions (user_id, product_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (user_id, pid))
            conn.commit()
        conn.close()

# --- 原有转发库与验证 DB (完整保留) ---
def check_user_status(user_id):
    conn = get_db_conn()
    if not conn: return (False, 0, 0)
    status = (False, 0, 0)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT fail_count, cooldown_until FROM user_verification WHERE user_id = %s", (user_id,))
            res = cur.fetchone()
            if res:
                fail_count, cooldown_until = res
                if cooldown_until and cooldown_until > datetime.datetime.now():
                    remaining = (cooldown_until - datetime.datetime.now()).total_seconds()
                    status = (True, int(remaining), fail_count)
                else:
                    status = (False, 0, fail_count)
    finally:
        conn.close()
    return status

def update_fail_count(user_id):
    conn = get_db_conn()
    if not conn: return 0
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO user_verification (user_id, fail_count) VALUES (%s, 1)
                ON CONFLICT (user_id) DO UPDATE SET fail_count = user_verification.fail_count + 1
                RETURNING fail_count
            """, (user_id,))
            new_count = cur.fetchone()[0]
            if new_count >= 2:
                cooldown = datetime.datetime.now() + timedelta(hours=5)
                cur.execute("UPDATE user_verification SET cooldown_until = %s, fail_count = 0 WHERE user_id = %s", (cooldown, user_id))
                conn.commit()
                return -1
            conn.commit()
            return new_count
    finally:
        conn.close()

def reset_success(user_id):
    conn = get_db_conn()
    if conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_verification WHERE user_id = %s", (user_id,))
            conn.commit()
        conn.close()

def db_add_library_content(cmd, chat_id, msg_id, msg_type):
    conn = get_db_conn()
    if conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO forward_library (trigger_cmd, source_chat_id, source_message_id, msg_type) VALUES (%s, %s, %s, %s)", 
                        (cmd, chat_id, msg_id, msg_type))
            conn.commit()
        conn.close()

def db_get_library_commands():
    conn = get_db_conn()
    cmds = []
    if conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT trigger_cmd FROM forward_library ORDER BY trigger_cmd")
            cmds = [row[0] for row in cur.fetchall()]
        conn.close()
    return cmds

def db_get_content_by_cmd(cmd):
    conn = get_db_conn()
    data = []
    if conn:
        with conn.cursor() as cur:
            cur.execute("SELECT source_chat_id, source_message_id FROM forward_library WHERE trigger_cmd = %s ORDER BY id ASC", (cmd,))
            data = cur.fetchall()
        conn.close()
    return data

def db_delete_command(cmd):
    conn = get_db_conn()
    if conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM forward_library WHERE trigger_cmd = %s", (cmd,))
            conn.commit()
        conn.close()

# ================= 业务逻辑：首页 =================
async def send_home_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 <b>欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~</b>\n\n"
        "📢 小卫小卫，守门员小卫！\n"
        "一键入群，小卫帮你搞定！\n"
        "新人来报到，小卫查身份！"
    )
    keyboard = [
        [InlineKeyboardButton("🚀 开始验证", callback_data="start_verify")],
        [InlineKeyboardButton("💰 我的积分", callback_data="jf_home")]
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
        except:
            pass

async def global_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_home_screen(update, context)

# ================= 业务逻辑：积分系统 =================

async def jf_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    info = db_get_points_info(user_id)
    query = update.callback_query
    if query: await query.answer()

    text = f"💰 <b>我的积分中心</b>\n\n当前积分：<b>{info['points']}</b>"
    
    keyboard = [
        [InlineKeyboardButton("📅 每日签到", callback_data="jf_checkin")],
        [InlineKeyboardButton("💎 积分充值", callback_data="jf_recharge")],
        [InlineKeyboardButton("🎁 积分兑换", callback_data="dh_home")],
        [InlineKeyboardButton("📜 余额记录", callback_data="jf_history")],
        [InlineKeyboardButton("🔙 返回首页", callback_data="back_home")]
    ]
    
    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def jf_checkin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    info = db_get_points_info(user_id)
    if info['last_checkin'] == date.today():
        await query.answer("⚠️ 今天已经签到过了", show_alert=True)
    else:
        add = random.randint(3, 8)
        db_checkin(user_id, add)
        await query.answer(f"✅ 签到成功！获得 {add} 积分。", show_alert=True)
        await jf_menu_handler(update, context)

# --- 余额记录 ---
async def jf_history_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    info = db_get_points_info(user_id)
    history = db_get_history(user_id, limit=10)
    
    text = f"📜 <b>积分余额记录</b>\n\n当前余额：<b>{info['points']}</b>\n\n<b>最近记录：</b>\n"
    if not history:
        text += "暂无记录"
    else:
        for amount, reason, date_time in history:
            sign = "+" if amount > 0 else ""
            t_str = date_time.strftime("%m-%d %H:%M")
            text += f"• <code>{t_str}</code>: {reason} <b>{sign}{amount}</b>\n"
            
    keyboard = [[InlineKeyboardButton("🔙 返回积分中心", callback_data="jf_home")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

# --- 充值部分 ---
async def jf_recharge_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    info = db_get_points_info(user_id)
    
    keyboard = []
    
    # 微信
    wx_text = "💚 微信充值 (5元)"
    if info['wx_done']:
        keyboard.append([InlineKeyboardButton("💚 微信充值 (已完成)", callback_data="jf_disabled_done")])
    elif info['wx_cool'] and info['wx_cool'] > datetime.datetime.now():
        keyboard.append([InlineKeyboardButton("💚 微信充值 (5h冷却)", callback_data="jf_disabled_cool")])
    else:
        keyboard.append([InlineKeyboardButton(wx_text, callback_data="jf_pay_wx")])
        
    # 支付宝
    ali_text = "💙 支付宝充值 (5元)"
    if info['ali_done']:
        keyboard.append([InlineKeyboardButton("💙 支付宝充值 (已完成)", callback_data="jf_disabled_done")])
    elif info['ali_cool'] and info['ali_cool'] > datetime.datetime.now():
        keyboard.append([InlineKeyboardButton("💙 支付宝充值 (5h冷却)", callback_data="jf_disabled_cool")])
    else:
        keyboard.append([InlineKeyboardButton(ali_text, callback_data="jf_pay_ali")])
        
    keyboard.append([InlineKeyboardButton("🔙 返回积分中心", callback_data="jf_home")])
    
    text = (
        "💎 <b>积分充值中心</b>\n\n"
        "✨ <b>5元 = 100积分</b>\n\n"
        "⚠️ <b>温馨提示：</b>\n"
        "1. 微信和支付宝每个用户<b>仅限使用一次</b>。\n"
        "2. 连续失败2次将锁定通道5小时。"
    )
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def jf_disabled_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if "done" in data: await query.answer("⛔️ 每人仅限一次。", show_alert=True)
    else: await query.answer("⛔️ 通道锁定中。", show_alert=True)

async def jf_wx_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "💚 <b>微信充值</b>\n\n请扫码支付 <b>5元</b>。\n支付后点击下方按钮。"
    keyboard = [[InlineKeyboardButton("✅ 我已付款，开始验证", callback_data="jf_wx_paid")]]
    try: await query.message.reply_photo(photo=JF_WX_QR_ID, caption=text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
    except: await query.message.reply_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def jf_wx_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "📝 <b>请输入微信支付凭证号</b>\n\n请复制 <b>交易单号</b> 回复："
    try: await query.message.reply_photo(photo=JF_WX_TUTORIAL_ID, caption=text, parse_mode='HTML')
    except: await query.message.reply_text(text, parse_mode='HTML')
    return JF_INPUT_WX_ORDER

async def jf_wx_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    user_id = update.effective_user.id
    if user_input.startswith("4200"):
        db_update_recharge_status(user_id, 'wx', is_success=True)
        db_add_points(user_id, 100, "微信充值")
        await update.message.reply_text("✅ <b>充值成功！</b>\n已到账 100 积分。", parse_mode='HTML')
        await jf_menu_handler(update, context)
        return ConversationHandler.END
    else:
        info = db_get_points_info(user_id)
        if info['wx_fail'] + 1 >= 2:
            db_update_recharge_status(user_id, 'wx', is_success=False, is_fail_increment=True, lock_hours=5)
            await update.message.reply_text("❌ <b>识别失败</b>\n通道已锁定 5小时。", parse_mode='HTML')
            await jf_menu_handler(update, context)
            return ConversationHandler.END
        else:
            db_update_recharge_status(user_id, 'wx', is_success=False, is_fail_increment=True)
            await update.message.reply_text("⚠️ <b>识别失败</b>\n请重试，剩余 1次 机会。", parse_mode='HTML')
            return JF_INPUT_WX_ORDER

async def jf_ali_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "💙 <b>支付宝充值</b>\n\n请扫码支付 <b>5元</b>。\n支付后点击下方按钮。"
    keyboard = [[InlineKeyboardButton("✅ 我已付款，开始验证", callback_data="jf_ali_paid")]]
    try: await query.message.reply_photo(photo=JF_ALI_QR_ID, caption=text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
    except: await query.message.reply_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def jf_ali_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "📝 <b>请输入支付宝订单号</b>\n\n请复制 <b>商家订单号</b> 回复："
    try: await query.message.reply_photo(photo=JF_ALI_TUTORIAL_ID, caption=text, parse_mode='HTML')
    except: await query.message.reply_text(text, parse_mode='HTML')
    return JF_INPUT_ALI_ORDER

async def jf_ali_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    user_id = update.effective_user.id
    if user_input.startswith("4768"):
        db_update_recharge_status(user_id, 'ali', is_success=True)
        db_add_points(user_id, 100, "支付宝充值")
        await update.message.reply_text("✅ <b>充值成功！</b>\n已到账 100 积分。", parse_mode='HTML')
        await jf_menu_handler(update, context)
        return ConversationHandler.END
    else:
        info = db_get_points_info(user_id)
        if info['ali_fail'] + 1 >= 2:
            db_update_recharge_status(user_id, 'ali', is_success=False, is_fail_increment=True, lock_hours=5)
            await update.message.reply_text("❌ <b>识别失败</b>\n通道已锁定 5小时。", parse_mode='HTML')
            await jf_menu_handler(update, context)
            return ConversationHandler.END
        else:
            db_update_recharge_status(user_id, 'ali', is_success=False, is_fail_increment=True)
            await update.message.reply_text("⚠️ <b>识别失败</b>\n请重试，剩余 1次 机会。", parse_mode='HTML')
            return JF_INPUT_ALI_ORDER

# ================= 业务逻辑：兑换系统 (/dh) =================

async def dh_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """兑换列表页面"""
    query = update.callback_query
    if query: await query.answer()
    
    user_id = update.effective_user.id
    products = db_get_products()
    
    text = "🎁 <b>积分兑换商城</b>\n\n点击下方商品进行兑换。"
    keyboard = []
    
    for pid, name, cost in products:
        # 检查是否已购买
        if db_is_redeemed(user_id, pid):
            btn_text = f"📦 {name} (已兑换)"
            callback = f"dh_view_{pid}"
        else:
            btn_text = f"🛍️ {name} ({cost} 积分)"
            callback = f"dh_buy_ask_{pid}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=callback)])
        
    keyboard.append([InlineKeyboardButton("🔙 返回积分中心", callback_data="jf_home")])
    
    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def dh_confirm_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """询问确认购买"""
    query = update.callback_query
    pid = int(query.data.split('_')[-1])
    await query.answer()
    
    product = db_get_product_detail(pid)
    if not product:
        await query.answer("❌ 商品不存在", show_alert=True)
        return
    
    name, cost = product[1], product[2]
    
    text = f"🛍️ <b>确认兑换？</b>\n\n商品：<b>{name}</b>\n价格：<b>{cost} 积分</b>"
    keyboard = [
        [InlineKeyboardButton("✅ 确认兑换", callback_data=f"dh_do_buy_{pid}")],
        [InlineKeyboardButton("❌ 取消", callback_data="dh_home")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def dh_execute_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """执行扣分和发货"""
    query = update.callback_query
    pid = int(query.data.split('_')[-1])
    user_id = query.from_user.id
    
    product = db_get_product_detail(pid)
    if not product: return
    name, cost = product[1], product[2]
    
    # 尝试扣分
    if db_deduct_points(user_id, cost, reason=f"兑换-{name}"):
        db_record_redemption(user_id, pid)
        await query.answer("✅ 兑换成功！", show_alert=True)
        # 发送商品内容
        await send_product_content(user_id, product, context)
        # 返回列表
        await dh_menu_handler(update, context)
    else:
        await query.answer("❌ 余额不足，请充值或签到。", show_alert=True)
        await dh_menu_handler(update, context)

async def dh_view_owned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看已拥有的商品"""
    query = update.callback_query
    pid = int(query.data.split('_')[-1])
    await query.answer()
    
    product = db_get_product_detail(pid)
    if product:
        await send_product_content(query.from_user.id, product, context)
    else:
        await query.answer("商品已下架", show_alert=True)

async def send_product_content(user_id, product, context):
    """发送商品内容逻辑"""
    # product: (id, name, cost, type, text, fileid, ...)
    p_type = product[3]
    p_text = product[4]
    p_file = product[5]
    
    caption = f"📦 <b>商品内容：{product[1]}</b>"
    
    try:
        if p_type == 'text':
            await context.bot.send_message(user_id, f"{caption}\n\n{p_text}", parse_mode='HTML')
        elif p_type == 'photo':
            await context.bot.send_photo(user_id, p_file, caption=caption, parse_mode='HTML')
        elif p_type == 'video':
            await context.bot.send_video(user_id, p_file, caption=caption, parse_mode='HTML')
        elif p_type == 'document':
            await context.bot.send_document(user_id, p_file, caption=caption, parse_mode='HTML')
        else:
            # 兼容其他媒体
            await context.bot.send_message(user_id, f"{caption}\n\n[未知格式]", parse_mode='HTML')
    except Exception as e:
        logger.error(f"Send product failed: {e}")
        await context.bot.send_message(user_id, "❌ 发送内容失败，请联系管理员。", parse_mode='HTML')

# ================= 业务逻辑：VIP验证流程 =================
async def verify_click_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    is_cd, rem, _ = check_user_status(user_id)
    if is_cd:
        m, s = divmod(rem, 60)
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
    return VERIFY_INPUT_ORDER

async def process_order_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    user_id = update.effective_user.id
    
    if user_input.startswith("20260"):
        reset_success(user_id)
        keyboard = [[InlineKeyboardButton("🔗 点击加入 VIP 群", url=GROUP_LINK)]]
        await update.message.reply_text("✅ <b>验证通过！</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        await send_home_screen(update, context)
        return ConversationHandler.END
    else:
        status = update_fail_count(user_id)
        if status == -1:
            await update.message.reply_text("❌ <b>失败次数过多，锁定5小时。</b>", parse_mode='HTML')
            await send_home_screen(update, context)
            return ConversationHandler.END
        else:
            await update.message.reply_text("⚠️ <b>未查询到订单，请重试。</b>", parse_mode='HTML')
            return VERIFY_INPUT_ORDER

# ================= 业务逻辑：自定义命令转发 =================
async def cleanup_messages(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    data = job.data 
    chat_id = job.chat_id
    for msg_id in data.get('msg_ids', []):
        try: await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except: pass
    try: await context.bot.send_message(chat_id=chat_id, text="⌛️ <b>消息已销毁</b>", parse_mode='HTML')
    except: pass
    await send_home_screen(None, context)

async def check_custom_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    text = update.message.text.strip()
    content_list = db_get_content_by_cmd(text)
    
    if content_list:
        try: await update.message.delete()
        except: pass
        sent_ids = []
        user_id = update.effective_chat.id
        for src_chat, src_msg in content_list:
            try:
                msg = await context.bot.copy_message(chat_id=user_id, from_chat_id=src_chat, message_id=src_msg)
                sent_ids.append(msg.message_id)
            except Exception as e: logger.error(f"Copy Failed: {e}")
        
        info = await context.bot.send_message(chat_id=user_id, text="✅ <b>资源已发送，20分钟后销毁</b>", parse_mode='HTML')
        sent_ids.append(info.message_id)
        context.job_queue.run_once(cleanup_messages, 1200, chat_id=user_id, data={'msg_ids': sent_ids})
        return
    else:
        await global_start_handler(update, context)

# ================= 管理员后台 =================
def is_admin(update: Update) -> bool:
    return str(update.effective_user.id) == ADMIN_ID

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, is_edit=False):
    keyboard = [
        [InlineKeyboardButton("🖼️ 提取图片 File ID", callback_data='get_file_id')],
        [InlineKeyboardButton("📚 频道转发库", callback_data='manage_lib')],
        [InlineKeyboardButton("🛍️ 兑换商品管理", callback_data='manage_prod')],
    ]
    text = "👑 <b>管理员后台</b>"
    if is_edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def admin_start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update): await admin_panel(update, context)

async def admin_ask_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query: await update.callback_query.answer()
    await update.effective_message.reply_text("📤 请发送图片/文件", parse_mode='HTML')
    return ADMIN_WAIT_PHOTO

async def admin_get_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_id = "未知"
    if update.message.photo: file_id = update.message.photo[-1].file_id
    elif update.message.document: file_id = update.message.document.file_id
    await update.message.reply_text(f"✅ ID:\n<code>{file_id}</code>", parse_mode='HTML')
    await admin_panel(update, context)
    return ConversationHandler.END

# --- 商品管理 (新) ---
async def prod_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    products = db_get_products()
    keyboard = [[InlineKeyboardButton("➕ 上架新商品", callback_data="prod_add_new")]]
    
    for pid, name, cost in products:
        keyboard.append([InlineKeyboardButton(f"🗑️ 下架: {name}", callback_data=f"prod_del_{pid}")])
    
    keyboard.append([InlineKeyboardButton("🔙 返回后台", callback_data="back_admin")])
    await query.edit_message_text("🛍️ <b>兑换商品管理</b>\n点击商品进行下架。", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def prod_start_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("⌨️ <b>请输入商品名称</b>", parse_mode='HTML')
    return PROD_INPUT_NAME

async def prod_save_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    context.user_data['p_name'] = name
    await update.message.reply_text(f"💰 商品：<b>{name}</b>\n\n请输入兑换所需积分 (数字):", parse_mode='HTML')
    return PROD_INPUT_COST

async def prod_save_cost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        cost = int(update.message.text.strip())
        context.user_data['p_cost'] = cost
        await update.message.reply_text("📤 <b>请发送商品内容</b>\n支持文本、图片、视频、文件。", parse_mode='HTML')
        return PROD_INPUT_CONTENT
    except ValueError:
        await update.message.reply_text("❌ 请输入有效的数字。", parse_mode='HTML')
        return PROD_INPUT_COST

async def prod_save_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = context.user_data['p_name']
    cost = context.user_data['p_cost']
    
    # 识别类型
    c_type = "text"
    c_text = None
    c_file = None
    
    if update.message.text:
        c_type = "text"
        c_text = update.message.text
    elif update.message.photo:
        c_type = "photo"
        c_file = update.message.photo[-1].file_id
    elif update.message.video:
        c_type = "video"
        c_file = update.message.video.file_id
    elif update.message.document:
        c_type = "document"
        c_file = update.message.document.file_id
    
    db_add_product(name, cost, c_type, c_text, c_file)
    
    await update.message.reply_text(f"✅ <b>商品已上架</b>\n名称：{name}\n价格：{cost}", parse_mode='HTML')
    await admin_panel(update, context)
    return ConversationHandler.END

async def prod_confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pid = int(query.data.split('_')[-1])
    
    # 简单处理：点击即确认删除
    db_delete_product(pid)
    await query.answer("✅ 商品已下架", show_alert=True)
    update.callback_query.data = "manage_prod"
    await prod_menu(update, context)

# --- 转发库 (原有) ---
async def lib_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cmds = db_get_library_commands()
    keyboard = [[InlineKeyboardButton("➕ 添加", callback_data="lib_add_new")]]
    for cmd in cmds: keyboard.append([InlineKeyboardButton(f"📂 {cmd}", callback_data=f"lib_view_{cmd}")])
    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="back_admin")])
    await query.edit_message_text("📚 <b>转发库</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def lib_start_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("⌨️ 输入命令名", parse_mode='HTML')
    return LIB_INPUT_CMD_NAME

async def lib_save_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd_name = update.message.text.strip()
    context.user_data['temp_cmd'] = cmd_name
    context.user_data['temp_count'] = 0
    await update.message.reply_text(f"📤 请发送内容到 <b>{cmd_name}</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 完成", callback_data="lib_upload_done")]]), parse_mode='HTML')
    return LIB_UPLOAD_CONTENT

async def lib_handle_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd_name = context.user_data.get('temp_cmd')
    msg_type = "文本" if update.message.text else "媒体"
    db_add_library_content(cmd_name, update.message.chat_id, update.message.message_id, msg_type)
    context.user_data['temp_count'] += 1
    await update.message.reply_text(f"✅ 已接收 {context.user_data['temp_count']} 条", quote=True)
    return LIB_UPLOAD_CONTENT

async def lib_finish_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    update.callback_query = query
    await lib_menu(update, context)
    return ConversationHandler.END

async def lib_view_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    cmd = query.data.replace("lib_view_", "")
    await query.answer()
    content = db_get_content_by_cmd(cmd)
    keyboard = [[InlineKeyboardButton("🗑️ 删除", callback_data=f"lib_del_{cmd}")], [InlineKeyboardButton("🔙 返回", callback_data="manage_lib")]]
    await query.edit_message_text(f"📂 <b>{cmd}</b>: {len(content)} 条", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def lib_confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    cmd = query.data.replace("lib_del_", "")
    db_delete_command(cmd)
    update.callback_query.data = "manage_lib"
    await lib_menu(update, context)

async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("已取消")
    await admin_panel(update, context)
    return ConversationHandler.END

async def back_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await admin_panel(update, context, is_edit=True)

# ================= Main =================
if __name__ == '__main__':
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Admin Conversations
    admin_id_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_ask_photo, pattern='^get_file_id$')],
        states={ADMIN_WAIT_PHOTO: [MessageHandler(filters.ALL, admin_get_photo)]},
        fallbacks=[CommandHandler('cancel', admin_cancel)],
    )
    
    admin_lib_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(lib_start_add, pattern='^lib_add_new$')],
        states={
            LIB_INPUT_CMD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, lib_save_name)],
            LIB_UPLOAD_CONTENT: [MessageHandler(filters.ALL & ~filters.COMMAND & ~filters.StatusUpdate.ALL, lib_handle_upload), CallbackQueryHandler(lib_finish_upload, pattern='^lib_upload_done$')]
        },
        fallbacks=[CommandHandler('cancel', admin_cancel)],
    )

    admin_prod_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(prod_start_add, pattern='^prod_add_new$')],
        states={
            PROD_INPUT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, prod_save_name)],
            PROD_INPUT_COST: [MessageHandler(filters.TEXT & ~filters.COMMAND, prod_save_cost)],
            PROD_INPUT_CONTENT: [MessageHandler(filters.ALL & ~filters.COMMAND, prod_save_content)]
        },
        fallbacks=[CommandHandler('cancel', admin_cancel)],
    )

    # User Conversations
    verify_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(ask_order_id_handler, pattern='^i_paid$')],
        states={VERIFY_INPUT_ORDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_order_input)]},
        fallbacks=[CommandHandler('start', global_start_handler)],
    )

    jf_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(jf_wx_ask, pattern='^jf_wx_paid$'),
            CallbackQueryHandler(jf_ali_ask, pattern='^jf_ali_paid$')
        ],
        states={
            JF_INPUT_WX_ORDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, jf_wx_process)],
            JF_INPUT_ALI_ORDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, jf_ali_process)]
        },
        fallbacks=[
            CommandHandler('start', global_start_handler),
            CommandHandler('jf', jf_menu_handler),
            CallbackQueryHandler(jf_menu_handler, pattern='^back_jf$')
        ]
    )

    # Handlers Registration
    app.add_handler(CommandHandler("admin", admin_start_cmd))
    app.add_handler(CommandHandler("id", admin_ask_photo))
    
    app.add_handler(admin_id_conv)
    app.add_handler(admin_lib_conv)
    app.add_handler(admin_prod_conv)

    # Admin Callbacks
    app.add_handler(CallbackQueryHandler(lib_menu, pattern='^manage_lib$'))
    app.add_handler(CallbackQueryHandler(lib_view_cmd, pattern='^lib_view_'))
    app.add_handler(CallbackQueryHandler(lib_confirm_delete, pattern='^lib_del_'))
    app.add_handler(CallbackQueryHandler(prod_menu, pattern='^manage_prod$'))
    app.add_handler(CallbackQueryHandler(prod_confirm_delete, pattern='^prod_del_'))
    app.add_handler(CallbackQueryHandler(back_to_admin, pattern='^back_admin$'))

    # User Callbacks
    app.add_handler(CommandHandler('jf', jf_menu_handler))
    app.add_handler(CommandHandler('dh', dh_menu_handler))
    app.add_handler(CallbackQueryHandler(verify_click_handler, pattern='^start_verify$'))
    app.add_handler(verify_conv)
    
    app.add_handler(CallbackQueryHandler(jf_menu_handler, pattern='^(jf_home|back_jf)$'))
    app.add_handler(CallbackQueryHandler(global_start_handler, pattern='^back_home$'))
    app.add_handler(CallbackQueryHandler(jf_checkin_handler, pattern='^jf_checkin$'))
    app.add_handler(CallbackQueryHandler(jf_history_handler, pattern='^jf_history$'))
    app.add_handler(CallbackQueryHandler(jf_recharge_menu, pattern='^jf_recharge$'))
    app.add_handler(CallbackQueryHandler(jf_disabled_handler, pattern='^jf_disabled_'))
    app.add_handler(CallbackQueryHandler(jf_wx_start, pattern='^jf_pay_wx$'))
    app.add_handler(CallbackQueryHandler(jf_ali_start, pattern='^jf_pay_ali$'))
    app.add_handler(jf_conv)

    # Redemption Callbacks
    app.add_handler(CallbackQueryHandler(dh_menu_handler, pattern='^dh_home$'))
    app.add_handler(CallbackQueryHandler(dh_confirm_buy, pattern='^dh_buy_ask_'))
    app.add_handler(CallbackQueryHandler(dh_execute_buy, pattern='^dh_do_buy_'))
    app.add_handler(CallbackQueryHandler(dh_view_owned, pattern='^dh_view_'))

    # Core
    app.add_handler(CommandHandler('start', global_start_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_custom_command))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, global_start_handler))

    print("Bot running with Full Features...")
    app.run_polling()
