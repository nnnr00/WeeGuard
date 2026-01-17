import os
import re
import logging
import asyncio
import psycopg2
import random
from datetime import datetime, timedelta, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ConversationHandler
)

# ==========================================
# ⚠️ 配置区域 (请填入你的 File ID)
# ==========================================
FILE_ID_VIP = "AgACAgUAAxkBAAIBamlrJM3dh9v-D0JT_Ou6p8RB7MygAAI1DWsbsJNZVzYLFeKKSIIoAQADAgADeAADOAQ"      # VIP特权说明图
FILE_ID_TUTORIAL = "AgACAgUAAxkBAAIBbmlrJORDj5FFL_6I1DCNChw9j_hXAAJqDWsbtShZV3RK8xCohcbUAQADAgADeQADOAQ"    # 验证订单教程图
FILE_ID_WX_QR = "AgACAgUAAxkBAAIBdmlrJPwfK_08snHlwtdI-isXhZdJAAIzDWsbsJNZV48inn-X9Td_AQADAgADeAADOAQ"       # 微信收款码
FILE_ID_WX_HELP = "AgACAgUAAxkBAAIBfmlrJQlRNQgmGXXLwiBlSFj2nNAlAAI3DWsbsJNZV-QR8b3h8hBxAQADAgADeQADOAQ"     # 微信教程
FILE_ID_ALI_QR = "AgACAgUAAxkBAAIBcmlrJPSCSgGDCWOS9P2eLOQNSggdAAI0DWsbsJNZV7e6iz3VImm2AQADAgADeAADOAQ"      # 支付宝收款码
FILE_ID_ALI_HELP = "AgACAgUAAxkBAAIBemlrJQTC0w-4MrMrx92OYlDXBu8FAAI2DWsbsJNZV_QG5bUozN_YAQADAgADeQADOAQ"    # 支付宝教程

GROUP_LINK = "https://t.me/joinchat/YOUR_LINK_HERE" # 验证成功后的群链接

# ==========================================
# ⚙️ 系统设置
# ==========================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

# --- 状态定义 ---
# Admin Basic
ADMIN_WAITING_FOR_FILE = 10

# Admin Channel Binding
ADMIN_BIND_WAIT_CMD = 50
ADMIN_BIND_WAIT_LINK = 51

# Admin Product
ADMIN_PROD_WAIT_NAME = 40
ADMIN_PROD_WAIT_COST = 41
ADMIN_PROD_WAIT_CONTENT = 42

# User Verify
USER_WAITING_FOR_ORDER = 20
# User Recharge
WAITING_FOR_WX_ORDER = 30
WAITING_FOR_ALI_ORDER = 31

# ==========================================
# 🗄️ 数据库操作
# ==========================================
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    if not DATABASE_URL: return
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # 1. 消息转发绑定表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS msg_bindings (
                command_trigger TEXT PRIMARY KEY,
                source_chat_id BIGINT,
                start_msg_id INTEGER,
                msg_count INTEGER
            );
        """)

        # 2. 用户验证状态表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_verification (
                user_id BIGINT PRIMARY KEY,
                attempt_count INTEGER DEFAULT 0,
                lockout_until TIMESTAMP
            );
        """)

        # 3. 用户积分表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_points (
                user_id BIGINT PRIMARY KEY,
                points INTEGER DEFAULT 0,
                last_signin_date DATE,
                wx_used BOOLEAN DEFAULT FALSE,
                ali_used BOOLEAN DEFAULT FALSE,
                recharge_attempts INTEGER DEFAULT 0,
                recharge_lockout TIMESTAMP
            );
        """)

        # 4. 商品表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                cost INTEGER NOT NULL,
                content_type TEXT, 
                content_val TEXT
            );
        """)

        # 5. 兑换记录表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS redemptions (
                user_id BIGINT,
                product_id INTEGER,
                PRIMARY KEY (user_id, product_id)
            );
        """)

        # 6. 积分变动日志表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS point_logs (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                change_amount INTEGER,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        conn.commit()
        cur.close()
        conn.close()
        logger.info("数据库表结构初始化成功")
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")

# --- 辅助函数 ---
def parse_telegram_link(link):
    """解析链接，返回 (ID/Username, msg_id)"""
    # 1. 私有频道 (t.me/c/ID/MSG_ID)
    match_private = re.search(r't\.me/c/(\d+)/(\d+)', link)
    if match_private:
        return int(f"-100{match_private.group(1)}"), int(match_private.group(2))
    
    # 2. 公开频道 (t.me/username/MSG_ID)
    match_public = re.search(r't\.me/([^/]+)/(\d+)', link)
    if match_public:
        username = match_public.group(1)
        if username != 'c':
            return username, int(match_public.group(2))
            
    return None, None

def is_admin(user_id):
    return str(user_id) == str(ADMIN_ID)

def log_point_change(user_id, amount, reason):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO point_logs (user_id, change_amount, reason) VALUES (%s, %s, %s)", (user_id, amount, reason))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"日志记录失败: {e}")

async def send_media_msg(update, context, file_id, caption, reply_markup=None):
    chat_id = update.effective_chat.id
    try:
        if file_id:
            try: await context.bot.send_photo(chat_id, file_id, caption=caption, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
            except: 
                try: await context.bot.send_video(chat_id, file_id, caption=caption, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
                except: await context.bot.send_message(chat_id, caption, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        else:
            await context.bot.send_message(chat_id, caption, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"发送消息失败: {e}")

# ==========================================
# 🏠 首页逻辑 /start
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~\n\n"
        "📢 **小卫小卫，守门员小卫！**\n"
        "一键入群，小卫帮你搞定！\n"
        "新人来报到，小卫查身份！"
    )
    keyboard = [
        [InlineKeyboardButton("🚀 开始验证", callback_data='btn_start_verify')],
        [InlineKeyboardButton("💰 我的积分", callback_data='btn_my_points')]
    ]
    if update.callback_query:
        await update.callback_query.answer()
        try: await update.callback_query.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
        except: pass
    else:
        await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

# ==========================================
# 💰 积分系统 /jf
# ==========================================
async def get_user_point_data(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT points, last_signin_date, wx_used, ali_used, recharge_attempts, recharge_lockout FROM user_points WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO user_points (user_id, points) VALUES (%s, 0)", (user_id,))
        conn.commit()
        row = (0, None, False, False, 0, None)
    conn.close()
    return row

async def jf_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if update.callback_query: await update.callback_query.answer()
    
    data = await get_user_point_data(user_id)
    points = data[0]
    
    text = f"💰 **积分中心**\n\n当前积分：**{points}** 分\n\n👇 请选择操作："
    
    keyboard = [
        [InlineKeyboardButton("📅 每日签到", callback_data='btn_signin')],
        [InlineKeyboardButton("💎 积分充值", callback_data='btn_recharge_menu')],
        [InlineKeyboardButton("🎁 兑换中心", callback_data='btn_dh_menu')],
        [InlineKeyboardButton("📜 余额/明细", callback_data='btn_balance_log')],
        [InlineKeyboardButton("🏠 返回首页", callback_data='go_home')]
    ]
    
    try: await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    except: await update.callback_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def balance_log_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT points FROM user_points WHERE user_id = %s", (user_id,))
    res = cur.fetchone()
    points = res[0] if res else 0

    cur.execute("SELECT change_amount, reason, created_at FROM point_logs WHERE user_id = %s ORDER BY created_at DESC LIMIT 10", (user_id,))
    logs = cur.fetchall()
    conn.close()

    log_text = ""
    if not logs: log_text = "暂无记录"
    else:
        for amount, reason, date_time in logs:
            dt_str = date_time.strftime("%Y-%m-%d %H:%M")
            sign = "+" if amount > 0 else ""
            log_text += f"`{dt_str}` | {reason} | **{sign}{amount}**\n"

    text = f"📜 **余额与明细**\n\n当前余额：**{points}** 积分\n\n📝 **最近记录：**\n{log_text}\n"
    keyboard = [[InlineKeyboardButton("🔙 返回积分中心", callback_data='btn_my_points')]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def signin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT points, last_signin_date FROM user_points WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    today = date.today()
    if row and row[1] == today:
        await query.message.reply_text("📅 今天已经签到过了，明天再来吧！")
    else:
        add_points = random.randint(3, 8)
        new_points = (row[0] if row else 0) + add_points
        cur.execute("INSERT INTO user_points (user_id, points, last_signin_date) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET points=%s, last_signin_date=%s", (user_id, new_points, today, new_points, today))
        conn.commit()
        log_point_change(user_id, add_points, "每日签到")
        await query.message.reply_text(f"✅ 签到成功！\n获得积分：**+{add_points}**\n当前总分：**{new_points}**", parse_mode=ParseMode.MARKDOWN)
    conn.close()
    await asyncio.sleep(1.5)
    await jf_menu_handler(update, context)

# ==========================================
# 🎁 兑换系统 /dh
# ==========================================
async def dh_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    query = update.callback_query
    if query: await query.answer()
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name, cost FROM products ORDER BY id ASC")
    products = cur.fetchall()
    cur.execute("SELECT product_id FROM redemptions WHERE user_id = %s", (user_id,))
    redeemed_ids = [r[0] for r in cur.fetchall()]
    conn.close()
    
    text = "🎁 **积分兑换商城**\n\n👇 点击下方按钮进行兑换："
    keyboard = []
    
    btn_text = "🤣 哈哈 (✅ 已兑换)" if 0 in redeemed_ids else "🤣 测试按钮 (0积分)"
    keyboard.append([InlineKeyboardButton(btn_text, callback_data="prod_click_0")])
    
    for pid, name, cost in products:
        if pid in redeemed_ids: display_text = f"📦 {name} (✅ 已拥有)"
        else: display_text = f"📦 {name} ({cost} 积分)"
        keyboard.append([InlineKeyboardButton(display_text, callback_data=f"prod_click_{pid}")])
    
    keyboard.append([InlineKeyboardButton("🔙 返回积分中心", callback_data='btn_my_points')])
    
    target_reply = query.edit_message_text if query else update.message.reply_text
    try: await target_reply(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    except: await update.effective_chat.send_message(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def handle_product_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    try: prod_id = int(query.data.split('_')[-1])
    except: return

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM redemptions WHERE user_id = %s AND product_id = %s", (user_id, prod_id))
    is_redeemed = cur.fetchone()

    prod_name, prod_cost, prod_type, prod_val = "测试商品", 0, "text", "哈哈"
    if prod_id != 0:
        cur.execute("SELECT name, cost, content_type, content_val FROM products WHERE id = %s", (prod_id,))
        prod = cur.fetchone()
        if not prod:
            conn.close()
            await query.message.reply_text("⚠️ 商品已下架。")
            await dh_menu_handler(update, context)
            return
        prod_name, prod_cost, prod_type, prod_val = prod
    conn.close()

    if is_redeemed:
        await deliver_product(update, context, prod_type, prod_val)
        return

    text = f"🛒 **确认兑换**\n\n商品：**{prod_name}**\n价格：**{prod_cost} 积分**\n\n是否确认兑换？"
    keyboard = [[InlineKeyboardButton("✅ 确认兑换", callback_data=f"redeem_confirm_{prod_id}")], [InlineKeyboardButton("❌ 取消", callback_data="btn_dh_menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def handle_redeem_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    try: prod_id = int(query.data.split('_')[-1])
    except: return

    conn = get_db_connection()
    cur = conn.cursor()

    prod_name, prod_cost, prod_type, prod_val = "测试商品", 0, "text", "哈哈"
    if prod_id != 0:
        cur.execute("SELECT name, cost, content_type, content_val FROM products WHERE id = %s", (prod_id,))
        prod = cur.fetchone()
        if not prod:
            conn.close(); await query.message.reply_text("⚠️ 商品已下架。"); await dh_menu_handler(update, context); return
        prod_name, prod_cost, prod_type, prod_val = prod

    cur.execute("SELECT points FROM user_points WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    user_points = row[0] if row else 0

    if user_points < prod_cost:
        conn.close(); await query.message.reply_text("⚠️ **余额不足，兑换失败！**"); await asyncio.sleep(2); await dh_menu_handler(update, context); return

    try:
        new_points = user_points - prod_cost
        cur.execute("UPDATE user_points SET points = %s WHERE user_id = %s", (new_points, user_id))
        cur.execute("INSERT INTO redemptions (user_id, product_id) VALUES (%s, %s)", (user_id, prod_id))
        conn.commit()
        conn.close()
        
        log_point_change(user_id, -prod_cost, f"兑换:{prod_name}")
        
        await query.message.reply_text(f"🎉 **兑换成功！**\n消耗 {prod_cost} 积分。", parse_mode=ParseMode.MARKDOWN)
        await deliver_product(update, context, prod_type, prod_val)
        await asyncio.sleep(2)
        await dh_menu_handler(update, context)
    except Exception as e:
        logger.error(f"Redemption error: {e}"); conn.rollback(); conn.close(); await query.message.reply_text("⚠️ 系统错误")

async def deliver_product(update, context, p_type, p_val):
    chat_id = update.effective_chat.id
    try:
        if p_type == 'text': await context.bot.send_message(chat_id, p_val)
        elif p_type == 'photo': await context.bot.send_photo(chat_id, p_val)
        elif p_type == 'video': await context.bot.send_video(chat_id, p_val)
        elif p_type == 'document': await context.bot.send_document(chat_id, p_val)
    except Exception as e: await context.bot.send_message(chat_id, f"⚠️ 发货出错: {e}")

# ==========================================
# 🛠 管理员系统
# ==========================================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    keyboard = [
        [InlineKeyboardButton("📂 获取文件ID", callback_data='btn_get_file_id')],
        [InlineKeyboardButton("📚 频道转发库 (绑定命令)", callback_data='btn_bind_channel')],
        [InlineKeyboardButton("🛍 商品管理 (上架/下架)", callback_data='btn_manage_products')]
    ]
    await update.message.reply_text("🔧 **管理员后台**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def admin_prod_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id, name, cost FROM products ORDER BY id ASC")
    products = cur.fetchall(); conn.close()
    text = "🛍 **商品管理面板**\n\n点击商品名称可 **下架删除**。\n点击【➕】添加新商品。"
    keyboard = []
    for pid, name, cost in products: keyboard.append([InlineKeyboardButton(f"🗑 {name} ({cost})", callback_data=f"admin_del_prod_{pid}")])
    keyboard.append([InlineKeyboardButton("➕ 添加新商品", callback_data='btn_add_product')])
    keyboard.append([InlineKeyboardButton("🔙 返回后台", callback_data='back_to_admin')])
    func = query.edit_message_text if query else update.message.reply_text
    await func(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

# --- 文件ID获取逻辑 (修正：跳转回admin) ---
async def handle_file_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    fid = msg.document.file_id if msg.document else (msg.video.file_id if msg.video else (msg.photo[-1].file_id if msg.photo else None))
    
    if fid:
        await msg.reply_text(f"✅ **获取成功**\nFile ID:\n`{fid}`", parse_mode=ParseMode.MARKDOWN)
    else:
        await msg.reply_text("❌ 未知文件类型")
    
    # 跳转回 Admin 面板
    await asyncio.sleep(1)
    await admin_panel(update, context)
    return ConversationHandler.END

# --- 商品上架逻辑 (修正：跳转回admin) ---
async def admin_add_prod_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    await query.edit_message_text("📝 **步骤 1/3：请输入商品名称**", parse_mode=ParseMode.MARKDOWN)
    return ADMIN_PROD_WAIT_NAME

async def admin_add_prod_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_prod_name'] = update.message.text.strip()
    await update.message.reply_text("💰 **步骤 2/3：请输入所需积分**", parse_mode=ParseMode.MARKDOWN)
    return ADMIN_PROD_WAIT_COST

async def admin_add_prod_cost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['new_prod_cost'] = int(update.message.text.strip())
        await update.message.reply_text("📦 **步骤 3/3：请发送商品内容** (文本/图片/视频)", parse_mode=ParseMode.MARKDOWN)
        return ADMIN_PROD_WAIT_CONTENT
    except: await update.message.reply_text("❌ 请输入数字"); return ADMIN_PROD_WAIT_COST

async def admin_add_prod_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message; p_type = 'text'; p_val = ''
    if msg.text: p_type = 'text'; p_val = msg.text
    elif msg.photo: p_type = 'photo'; p_val = msg.photo[-1].file_id
    elif msg.video: p_type = 'video'; p_val = msg.video.file_id
    elif msg.document: p_type = 'document'; p_val = msg.document.file_id
    
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO products (name, cost, content_type, content_val) VALUES (%s, %s, %s, %s)", (context.user_data['new_prod_name'], context.user_data['new_prod_cost'], p_type, p_val))
    conn.commit(); conn.close()
    
    await update.message.reply_text("✅ 商品上架成功！")
    
    # 跳转回 Admin 面板
    await asyncio.sleep(1)
    await admin_panel(update, context)
    return ConversationHandler.END

# --- 商品删除逻辑 ---
async def admin_del_prod_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    prod_id = query.data.split('_')[-1]
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT name FROM products WHERE id = %s", (prod_id,)); res = cur.fetchone(); conn.close()
    if not res: await admin_prod_menu(update, context); return
    text = f"⚠️ **确认下架：{res[0]}**？"
    keyboard = [[InlineKeyboardButton("✅ 确认删除", callback_data=f"admin_del_exec_{prod_id}")], [InlineKeyboardButton("❌ 取消", callback_data="btn_manage_products")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def admin_del_exec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    prod_id = query.data.split('_')[-1]
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM products WHERE id = %s", (prod_id,))
    cur.execute("DELETE FROM redemptions WHERE product_id = %s", (prod_id,))
    conn.commit(); conn.close()
    await query.message.reply_text("✅ 已删除。"); await asyncio.sleep(1); await admin_prod_menu(update, context)

# --- 频道绑定逻辑 (修正：跳转回admin) ---
async def admin_bind_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    await query.edit_message_text("⌨️ **请输入自定义命令**\n(例如：`VIP1`，支持中文/大写)", parse_mode=ParseMode.MARKDOWN)
    return ADMIN_BIND_WAIT_CMD

async def admin_bind_get_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.strip().upper()
    context.user_data['bind_cmd'] = cmd
    await update.message.reply_text(f"✅ 命令：`{cmd}`\n🔗 **请输入消息链接** (支持 t.me/...)", parse_mode=ParseMode.MARKDOWN)
    return ADMIN_BIND_WAIT_LINK

async def admin_bind_get_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text.strip()
    chat_identifier, msg_id = parse_telegram_link(link)
    
    if not chat_identifier:
        await update.message.reply_text("❌ 链接格式无效，请重试。")
        return ADMIN_BIND_WAIT_LINK
    
    # 尝试解析公开频道 Username 为 ID
    final_chat_id = chat_identifier
    if isinstance(chat_identifier, str):
        try:
            chat = await context.bot.get_chat(chat_id=f"@{chat_identifier}")
            final_chat_id = chat.id
        except Exception as e:
            await update.message.reply_text("❌ 无法获取该公开频道ID，请确保链接正确或将机器人拉入频道。")
            return ADMIN_BIND_WAIT_LINK

    cmd = context.user_data['bind_cmd']
    count = 100 # 固定数量

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO msg_bindings (command_trigger, source_chat_id, start_msg_id, msg_count)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (command_trigger) 
        DO UPDATE SET source_chat_id = EXCLUDED.source_chat_id, start_msg_id = EXCLUDED.start_msg_id, msg_count = EXCLUDED.msg_count;
    """, (cmd, final_chat_id, msg_id, count))
    conn.commit()
    conn.close()
    
    await update.message.reply_text(f"✅ **绑定成功**\n命令: `{cmd}`\n自动转发: 100条", parse_mode=ParseMode.MARKDOWN)
    
    # 跳转回 Admin 面板
    await asyncio.sleep(1)
    await admin_panel(update, context)
    return ConversationHandler.END

# --- 管理员通用回调 ---
async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    if query.data == 'btn_get_file_id': await query.edit_message_text("📤 发送文件获取ID"); return ADMIN_WAITING_FOR_FILE
    elif query.data == 'btn_bind_channel': return await admin_bind_start(update, context) 
    elif query.data == 'back_to_admin': await admin_panel(update, context); return ConversationHandler.END

# ==========================================
# 充值与验证 (核心逻辑)
# ==========================================
async def recharge_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; user_id = query.from_user.id; await query.answer()
    data = await get_user_point_data(user_id)
    wx_used, ali_used, attempts, lockout = data[2], data[3], data[4], data[5]
    if lockout and datetime.now() < lockout:
        wait = int((lockout - datetime.now()).total_seconds() / 3600) + 1
        await query.message.reply_text(f"⛔️ 充值锁定中，请 {wait} 小时后再试。"); return
    text = "💎 **积分充值中心**\n✨ 5元 = 100积分\n⚠️ 微信支付宝各限购一次！"
    keyboard = []
    if not wx_used: keyboard.append([InlineKeyboardButton("💚 微信充值 (5元)", callback_data='btn_pay_wx')])
    if not ali_used: keyboard.append([InlineKeyboardButton("💙 支付宝充值 (5元)", callback_data='btn_pay_ali')])
    keyboard.append([InlineKeyboardButton("🔙 返回积分中心", callback_data='btn_my_points')])
    if wx_used and ali_used: text += "\n🚫 优惠次数已用完。"
    try: await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    except: await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def pay_wx_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await send_media_msg(update, context, FILE_ID_WX_QR, "💚 **微信支付 5元**\n支付后点击下方验证。", InlineKeyboardMarkup([[InlineKeyboardButton("✅ 我已支付，开始验证", callback_data='btn_verify_wx')]]))

async def pay_wx_verify_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await send_media_msg(update, context, FILE_ID_WX_HELP, "📝 **请输入微信交易单号**：")
    return WAITING_FOR_WX_ORDER

async def pay_ali_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await send_media_msg(update, context, FILE_ID_ALI_QR, "💙 **支付宝支付 5元**\n支付后点击下方验证。", InlineKeyboardMarkup([[InlineKeyboardButton("✅ 我已支付，开始验证", callback_data='btn_verify_ali')]]))

async def pay_ali_verify_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await send_media_msg(update, context, FILE_ID_ALI_HELP, "📝 **请输入支付宝商家订单号**：")
    return WAITING_FOR_ALI_ORDER

async def check_recharge_order(update: Update, context: ContextTypes.DEFAULT_TYPE, method):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    conn = get_db_connection(); cur = conn.cursor()
    valid = (method=='wx' and text.startswith('4200')) or (method=='ali' and text.startswith('4768'))
    
    if valid:
        # === 成功 ===
        cur.execute("UPDATE user_points SET points=points+100, recharge_attempts=0 WHERE user_id=%s", (user_id,))
        if method=='wx': cur.execute("UPDATE user_points SET wx_used=TRUE WHERE user_id=%s", (user_id,))
        else: cur.execute("UPDATE user_points SET ali_used=TRUE WHERE user_id=%s", (user_id,))
        conn.commit(); conn.close()
        
        log_point_change(user_id, 100, f"充值:{'微信' if method=='wx' else '支付宝'}")
        
        # 成功 -> 跳转到首页
        await update.message.reply_text("🎉 **充值成功！**\n获得 100 积分。")
        await asyncio.sleep(2)
        await start(update, context) # 跳转到首页 /start
        return ConversationHandler.END
    else:
        # === 失败 ===
        cur.execute("SELECT recharge_attempts FROM user_points WHERE user_id=%s", (user_id,))
        att = (cur.fetchone()[0] or 0) + 1
        if att >= 2:
            lock = datetime.now() + timedelta(hours=5)
            cur.execute("UPDATE user_points SET recharge_attempts=%s, recharge_lockout=%s WHERE user_id=%s", (att, lock, user_id))
            conn.commit(); conn.close()
            await update.message.reply_text("❌ 失败2次，锁定5小时。")
        else:
            cur.execute("UPDATE user_points SET recharge_attempts=%s WHERE user_id=%s", (att, user_id))
            conn.commit(); conn.close()
            await update.message.reply_text("❌ 失败，请重试 (剩1次)。")
        
        # 失败 -> 跳转回积分页
        await asyncio.sleep(2)
        await jf_menu_handler(update, context) # 跳转回积分页 /jf
        return ConversationHandler.END

# ==========================================
# 杂项 & 转发 & 验证完整版
# ==========================================
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('已取消。')
    await start(update, context); return ConversationHandler.END

async def go_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: await update.callback_query.answer(); await update.callback_query.delete_message()
    except: pass
    await start(update, context)

async def delete_msg_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    for mid in data['mids']: 
        try: await context.bot.delete_message(data['cid'], mid) 
        except: pass
    await context.bot.send_message(data['cid'], "⏳ 消息已过期，请购买后获取。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 首页", callback_data="go_home")]]))

async def handle_command_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    cmd = update.message.text.strip().upper()
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT source_chat_id, start_msg_id, msg_count FROM msg_bindings WHERE command_trigger = %s", (cmd,))
    res = cur.fetchone(); conn.close()
    if not res: await start(update, context); return
    try: await update.message.delete()
    except: pass
    mids = []
    count = res[2]
    for i in range(count):
        try: 
            m = await context.bot.copy_message(update.effective_chat.id, res[0], res[1]+i)
            mids.append(m.message_id); await asyncio.sleep(0.05)
        except: continue
    if mids:
        end_msg = await context.bot.send_message(update.effective_chat.id, "✅ 发送完毕", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💰 我的积分", callback_data="btn_my_points"), InlineKeyboardButton("🏠 首页", callback_data="go_home")]]))
        mids.append(end_msg.message_id)
        context.job_queue.run_once(delete_msg_job, 1200, data={'cid': update.effective_chat.id, 'mids': mids})
    else: await context.bot.send_message(update.effective_chat.id, "❌ 获取内容失败")

# --- 验证流程步骤 (完整) ---
async def verify_step_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT lockout_until FROM user_verification WHERE user_id = %s", (query.from_user.id,)); res = cur.fetchone(); conn.close()
    if res and res[0] and datetime.now() < res[0]:
        h = int((res[0] - datetime.now()).total_seconds()/3600) + 1
        await query.answer(f"验证已锁定，请等待 {h} 小时", show_alert=True); return
    await send_media_msg(update, context, FILE_ID_VIP, "💎 VIP说明...", InlineKeyboardMarkup([[InlineKeyboardButton("✅ 我已付款，开始验证", callback_data='btn_paid_confirm')]]))

async def verify_step_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await send_media_msg(update, context, FILE_ID_TUTORIAL, "📝 请输入商户订单号：")
    return USER_WAITING_FOR_ORDER

async def verify_step_3(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """验证订单号完整逻辑"""
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    conn = get_db_connection()
    cur = conn.cursor()

    if text.startswith("20260"):
        cur.execute("DELETE FROM user_verification WHERE user_id = %s", (user_id,))
        conn.commit()
        conn.close()

        success_text = "🎉 **验证成功！**"
        keyboard = [[InlineKeyboardButton("🔗 点击加入群组", url=GROUP_LINK)]]
        
        await update.message.reply_text(success_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
        await asyncio.sleep(2)
        await start(update, context)
        return ConversationHandler.END
    else:
        cur.execute("SELECT attempt_count FROM user_verification WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        current_attempts = row[0] if row else 0
        new_attempts = current_attempts + 1

        if new_attempts >= 2:
            lockout_time = datetime.now() + timedelta(hours=5)
            cur.execute("""
                INSERT INTO user_verification (user_id, attempt_count, lockout_until)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET attempt_count = %s, lockout_until = %s
            """, (user_id, new_attempts, lockout_time, new_attempts, lockout_time))
            conn.commit()
            conn.close()

            await update.message.reply_text("❌ 未查询到订单信息。\n🚫 连续失败 2 次，系统已暂停验证。\n请 5 小时后再试。", parse_mode=ParseMode.MARKDOWN)
            await asyncio.sleep(2)
            await start(update, context)
            return ConversationHandler.END
        else:
            cur.execute("""
                INSERT INTO user_verification (user_id, attempt_count)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE SET attempt_count = %s
            """, (user_id, new_attempts, new_attempts))
            conn.commit()
            conn.close()

            await update.message.reply_text("❌ 未查询到订单信息，请重试。\n(您还有 1 次尝试机会)", parse_mode=ParseMode.MARKDOWN)
            return USER_WAITING_FOR_ORDER

# ==========================================
# 🚀 主程序
# ==========================================
if __name__ == '__main__':
    init_db()
    if not BOT_TOKEN: exit("BOT_TOKEN missing")
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Admin Handler
    app.add_handler(ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_callback, pattern='^(btn_get_file_id|btn_bind_channel)$'),
            CallbackQueryHandler(admin_prod_menu, pattern='^btn_manage_products$'),
            CallbackQueryHandler(admin_add_prod_start, pattern='^btn_add_product$')
        ],
        states={
            ADMIN_WAITING_FOR_FILE: [MessageHandler(filters.ATTACHMENT|filters.PHOTO, handle_file_id)],
            
            # 频道绑定状态
            ADMIN_BIND_WAIT_CMD: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_bind_get_cmd)],
            ADMIN_BIND_WAIT_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_bind_get_link)],

            # 商品管理状态
            ADMIN_PROD_WAIT_NAME: [MessageHandler(filters.TEXT, admin_add_prod_name)],
            ADMIN_PROD_WAIT_COST: [MessageHandler(filters.TEXT, admin_add_prod_cost)],
            ADMIN_PROD_WAIT_CONTENT: [MessageHandler(filters.ALL, admin_add_prod_content)],
        },
        fallbacks=[CommandHandler('cancel', cancel), CallbackQueryHandler(admin_panel, pattern='^back_to_admin$')]
    ))

    # Verify
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(verify_step_2, pattern='^btn_paid_confirm$')],
        states={USER_WAITING_FOR_ORDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, verify_step_3)]},
        fallbacks=[CommandHandler('cancel', cancel)]
    ))
    
    # Recharge
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(pay_wx_verify_step, pattern='^btn_verify_wx$')],
        states={WAITING_FOR_WX_ORDER: [MessageHandler(filters.TEXT, lambda u,c: check_recharge_order(u,c,'wx'))]},
        fallbacks=[CommandHandler('cancel', cancel)]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(pay_ali_verify_step, pattern='^btn_verify_ali$')],
        states={WAITING_FOR_ALI_ORDER: [MessageHandler(filters.TEXT, lambda u,c: check_recharge_order(u,c,'ali'))]},
        fallbacks=[CommandHandler('cancel', cancel)]
    ))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("jf", jf_menu_handler))
    app.add_handler(CommandHandler("dh", dh_menu_handler))
    
    app.add_handler(CallbackQueryHandler(jf_menu_handler, pattern='^btn_my_points$'))
    app.add_handler(CallbackQueryHandler(balance_log_handler, pattern='^btn_balance_log$'))
    app.add_handler(CallbackQueryHandler(dh_menu_handler, pattern='^btn_dh_menu$'))
    app.add_handler(CallbackQueryHandler(signin_handler, pattern='^btn_signin$'))
    app.add_handler(CallbackQueryHandler(recharge_menu_handler, pattern='^btn_recharge_menu$'))
    app.add_handler(CallbackQueryHandler(pay_wx_start, pattern='^btn_pay_wx$'))
    app.add_handler(CallbackQueryHandler(pay_ali_start, pattern='^btn_pay_ali$'))
    app.add_handler(CallbackQueryHandler(handle_product_click, pattern='^prod_click_'))
    app.add_handler(CallbackQueryHandler(handle_redeem_confirm, pattern='^redeem_confirm_'))
    app.add_handler(CallbackQueryHandler(admin_del_prod_confirm, pattern='^admin_del_prod_'))
    app.add_handler(CallbackQueryHandler(admin_del_exec, pattern='^admin_del_exec_'))
    app.add_handler(CallbackQueryHandler(verify_step_1, pattern='^btn_start_verify$'))
    app.add_handler(CallbackQueryHandler(go_home, pattern='^go_home$'))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_command_forward))

    print("Bot is running with Final Optimized Flows...")
    app.run_polling()
