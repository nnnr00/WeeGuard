import os
import logging
import psycopg2
import random
import re
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler, 
    CallbackQueryHandler, MessageHandler, filters
)

# --- 配置日志 ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- 环境变量 ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = os.getenv("ADMIN_ID")

# --- 素材配置 (部署后通过环境变量填入 File ID) ---
VIP_INTRO_FILE_ID = os.getenv("VIP_FILE_ID", "")
TUTORIAL_FILE_ID = os.getenv("TUTORIAL_FILE_ID", "")
WECHAT_QR_FILE_ID = os.getenv("WECHAT_QR_ID", "")
ALIPAY_QR_FILE_ID = os.getenv("ALIPAY_QR_ID", "")
WECHAT_STEP_FILE_ID = os.getenv("WECHAT_STEP_ID", "")
ALIPAY_STEP_FILE_ID = os.getenv("ALIPAY_STEP_ID", "")

# --- 数据库连接 ---
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """初始化所有数据库表"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # 1. 基础积分与锁定状态表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_points (
                user_id TEXT PRIMARY KEY,
                points INTEGER DEFAULT 0,
                last_checkin DATE,
                wechat_used BOOLEAN DEFAULT FALSE,
                alipay_used BOOLEAN DEFAULT FALSE,
                recharge_fail_count INTEGER DEFAULT 0,
                recharge_locked_until TIMESTAMP,
                vip_fail_count INTEGER DEFAULT 0,
                vip_locked_until TIMESTAMP,
                is_vip BOOLEAN DEFAULT FALSE
            );
        """)

        # 2. 积分流水表 (用于明细和排行)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS point_history (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                change_amount INTEGER NOT NULL,
                reason TEXT,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 3. 商品表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS shop_products (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                cost INTEGER NOT NULL,
                content_type TEXT,
                content_data TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 4. 购买记录表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_purchases (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                product_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, product_id)
            );
        """)

        # 5. 转发命令绑定表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS command_bindings (
                id SERIAL PRIMARY KEY,
                command TEXT NOT NULL,
                from_chat_id TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        conn.commit()
        cur.close()
        conn.close()
        print("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Database init error: {e}")

# --- 数据库逻辑封装 ---

def get_user_data(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    # 确保用户存在
    cur.execute("INSERT INTO user_points (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (str(user_id),))
    conn.commit()
    
    cur.execute("SELECT * FROM user_points WHERE user_id = %s", (str(user_id),))
    # 获取列名以便构建字典
    colnames = [desc[0] for desc in cur.description]
    row = cur.fetchone()
    conn.close()
    return dict(zip(colnames, row)) if row else {}

def update_user_status(user_id, updates: dict):
    conn = get_db_connection()
    cur = conn.cursor()
    set_clauses = []
    values = []
    for k, v in updates.items():
        set_clauses.append(f"{k} = %s")
        values.append(v)
    values.append(str(user_id))
    sql = f"UPDATE user_points SET {', '.join(set_clauses)} WHERE user_id = %s"
    cur.execute(sql, tuple(values))
    conn.commit()
    conn.close()

def add_points(user_id, amount, reason, desc):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE user_points SET points = points + %s WHERE user_id = %s", (amount, str(user_id)))
    cur.execute("INSERT INTO point_history (user_id, change_amount, reason, description) VALUES (%s, %s, %s, %s)",
                (str(user_id), amount, reason, desc))
    conn.commit()
    conn.close()

def save_binding(command, chat_id, msg_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO command_bindings (command, from_chat_id, message_id) VALUES (%s, %s, %s)",
                (command.upper(), str(chat_id), int(msg_id)))
    conn.commit()
    conn.close()

# --- 辅助逻辑 ---

def parse_telegram_link(link):
    """解析链接获取 chat_id 和 message_id"""
    private = re.search(r't\.me/c/(\d+)/(\d+)', link)
    if private: return f"-100{private.group(1)}", int(private.group(2))
    public = re.search(r't\.me/([a-zA-Z0-9_]+)/(\d+)', link)
    if public: return f"@{public.group(1)}", int(public.group(2))
    return None, None

async def delayed_delete(context: ContextTypes.DEFAULT_TYPE):
    """定时任务：删除资源并提示"""
    job = context.job.data
    chat_id = job['chat_id']
    msg_ids = job['msg_ids']
    
    # 删除消息
    for mid in msg_ids:
        try: await context.bot.delete_message(chat_id=chat_id, message_id=mid)
        except: pass
        
    # 发送提示
    bot_uname = context.bot.username
    kb = [[InlineKeyboardButton("🔄 重新获取 (已购买可点此)", url=f"https://t.me/{bot_uname}")]]
    await context.bot.send_message(
        chat_id=chat_id,
        text="⏳ **消息已过期**\n\n消息存在时间有限，请到购买处重新获取。\n（已购买 不需要二次付费就可看见消息）",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode='Markdown'
    )

# --- 核心处理器 ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear() # 清除状态
    text = (
        "👋 欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~\n\n"
        "📢 小卫小卫，守门员小卫！\n"
        "一键入群，小卫帮你搞定！\n"
        "新人来报到，小卫查身份！"
    )
    kb = [
        [InlineKeyboardButton("🚀 开始验证", callback_data='start_verify')],
        [InlineKeyboardButton("💰 积分中心", callback_data='menu_points')]
    ]
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

# === VIP 验证模块 ===
async def vip_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, step):
    user_id = str(update.effective_user.id)
    query = update.callback_query
    
    if step == 'intro':
        # 检查锁定
        udata = get_user_data(user_id)
        if udata.get('vip_locked_until') and datetime.now() < udata['vip_locked_until']:
             await query.answer("⛔️ 验证尝试过多，暂时锁定中。", show_alert=True)
             return

        text = (
            "💎 **VIP会员特权说明：**\n\n"
            "✅ 专属中转通道\n✅ 优先审核入群\n✅ 7x24小时客服支持\n✅ 定期福利活动"
        )
        kb = [[InlineKeyboardButton("✅ 我已付款，开始验证", callback_data='vip_input')]]
        if VIP_INTRO_FILE_ID:
            await query.message.reply_document(VIP_INTRO_FILE_ID, caption=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        else:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
            
    elif step == 'input':
        context.user_data['state'] = 'waiting_vip_order'
        text = (
            "📝 **请输入您的订单号**\n\n"
            "请在【我的】-【账单】-【账单详情】-【更多】中查找订单号。\n"
            "👇 请直接发送订单号："
        )
        if TUTORIAL_FILE_ID:
            await query.message.reply_document(TUTORIAL_FILE_ID, caption=text, parse_mode='Markdown')
        else:
            await query.message.reply_text(text, parse_mode='Markdown')

# === 积分商城模块 ===
async def points_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    udata = get_user_data(user_id)
    text = f"💰 **我的积分中心**\n\n当前积分：**{udata['points']}** 分"
    kb = [
        [InlineKeyboardButton("📅 每日签到", callback_data='do_checkin'), InlineKeyboardButton("💎 充值积分", callback_data='menu_recharge')],
        [InlineKeyboardButton("🎁 积分兑换", callback_data='menu_shop')],
        [InlineKeyboardButton("📜 余额明细", callback_data='view_history'), InlineKeyboardButton("🏆 积分排行", callback_data='view_rank')],
        [InlineKeyboardButton("🔙 返回首页", callback_data='back_home')]
    ]
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def handle_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    udata = get_user_data(user_id)
    if str(udata['last_checkin']) == str(datetime.now().date()):
        await update.callback_query.answer("⚠️ 今天已经签到过了！", show_alert=True)
        return
    pts = random.randint(3, 8)
    # 更新积分和签到时间
    add_points(user_id, pts, 'checkin', '每日签到')
    update_user_status(user_id, {'last_checkin': datetime.now().date()})
    await update.callback_query.answer(f"🎉 签到成功！+{pts} 积分", show_alert=True)
    await points_menu(update, context)

# === 充值模块 ===
async def recharge_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    udata = get_user_data(user_id)
    # 检查锁定
    if udata.get('recharge_locked_until') and datetime.now() < udata['recharge_locked_until']:
        hours = int((udata['recharge_locked_until'] - datetime.now()).total_seconds() // 3600) + 1
        await update.callback_query.answer(f"⛔️ 充值功能已锁定，请 {hours} 小时后重试。", show_alert=True)
        return

    text = "💎 **请选择充值方式**\n\n⚠️ 温馨提示：微信和支付宝只能各使用一次，请勿重复充值！"
    kb = [
        [InlineKeyboardButton("💚 微信充值 (100积分)", callback_data='pay_wechat')],
        [InlineKeyboardButton("💙 支付宝充值 (100积分)", callback_data='pay_alipay')],
        [InlineKeyboardButton("🔙 返回", callback_data='menu_points')]
    ]
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def show_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, method):
    user_id = str(update.effective_user.id)
    udata = get_user_data(user_id)
    
    if (method == 'wechat' and udata['wechat_used']) or (method == 'alipay' and udata['alipay_used']):
        await update.callback_query.answer("⚠️ 该方式已使用过，无法重复充值。", show_alert=True)
        return

    fid = WECHAT_QR_FILE_ID if method == 'wechat' else ALIPAY_QR_FILE_ID
    name = "微信" if method == 'wechat' else "支付宝"
    text = f"{'💚' if method=='wechat' else '💙'} **{name}充值**\n\n5元 = 100积分\n\n👇 支付完成后点击下方按钮："
    kb = [[InlineKeyboardButton("✅ 我已支付，开始验证", callback_data=f'verify_pay_{method}')]]
    
    await update.callback_query.message.delete() # 删掉旧菜单
    if fid:
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=fid, caption=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def verify_pay_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, method):
    context.user_data['state'] = f'waiting_{method}_order'
    fid = WECHAT_STEP_FILE_ID if method == 'wechat' else ALIPAY_STEP_FILE_ID
    name = "微信" if method == 'wechat' else "支付宝"
    target = "交易单号" if method == 'wechat' else "商家订单号"
    
    text = f"📝 **请输入{name}订单号**\n\n请找到账单详情中的 **【{target}】**。\n👇 直接粘贴发送给我："
    if fid:
        await update.callback_query.message.reply_photo(photo=fid, caption=text, parse_mode='Markdown')
    else:
        await update.callback_query.message.reply_text(text, parse_mode='Markdown')

# === 兑换/商品模块 ===
async def shop_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    conn = get_db_connection()
    cur = conn.cursor()
    # 获取商品
    cur.execute("SELECT id, name, cost FROM shop_products WHERE is_active = TRUE ORDER BY id ASC")
    prods = cur.fetchall()
    # 获取已购
    cur.execute("SELECT product_id FROM user_purchases WHERE user_id = %s", (user_id,))
    bought = [r[0] for r in cur.fetchall()]
    conn.close()
    
    kb = []
    # 固定测试商品
    kb.append([InlineKeyboardButton("😂 测试商品: 哈哈 (0积分)", callback_data='shop_confirm_test')])
    
    for pid, name, cost in prods:
        if pid in bought:
            kb.append([InlineKeyboardButton(f"✅ 已兑换: {name}", callback_data=f'shop_show_{pid}')])
        else:
            kb.append([InlineKeyboardButton(f"🎁 {name} ({cost}积分)", callback_data=f'shop_confirm_{pid}')])
            
    kb.append([InlineKeyboardButton("🔙 返回积分中心", callback_data='menu_points')])
    text = "🛍️ **积分兑换商城**\n\n请选择兑换商品："
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def process_shop(update: Update, context: ContextTypes.DEFAULT_TYPE, action, pid_str):
    query = update.callback_query
    user_id = str(update.effective_user.id)
    
    # --- 测试商品 ---
    if pid_str == 'test':
        if action == 'confirm':
            kb = [[InlineKeyboardButton("✅ 确认 (0积分)", callback_data='shop_buy_test'), InlineKeyboardButton("❌ 取消", callback_data='menu_shop')]]
            await query.edit_message_text("🤔 **确认兑换**\n\n商品：测试商品\n消耗：0 积分", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        elif action == 'buy':
            await query.answer("兑换成功！")
            kb = [[InlineKeyboardButton("🔙 返回", callback_data='menu_shop')]]
            await query.edit_message_text("😂 **内容展示**\n\n哈哈", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        return

    # --- 真实商品 ---
    pid = int(pid_str)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT name, cost, content_type, content_data FROM shop_products WHERE id = %s", (pid,))
    prod = cur.fetchone()
    
    if not prod:
        conn.close()
        await query.answer("商品不存在")
        await shop_list(update, context)
        return
        
    name, cost, ctype, cdata = prod
    
    if action == 'confirm':
        kb = [[InlineKeyboardButton(f"✅ 确认消耗 {cost} 积分", callback_data=f'shop_buy_{pid}'), InlineKeyboardButton("❌ 取消", callback_data='menu_shop')]]
        await query.edit_message_text(f"🤔 **确认兑换**\n\n商品：{name}\n消耗：**{cost}** 积分", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        conn.close()
        
    elif action == 'buy':
        # 检查余额
        udata = get_user_data(user_id)
        if udata['points'] < cost:
            await query.answer("❌ 余额不足！", show_alert=True)
            conn.close()
            await shop_list(update, context)
            return
            
        # 扣款发货
        add_points(user_id, -cost, 'redeem', f'兑换: {name}')
        cur.execute("INSERT INTO user_purchases (user_id, product_id) VALUES (%s, %s)", (user_id, pid))
        conn.commit()
        conn.close()
        await query.answer("🎉 兑换成功！")
        await deliver_content(update, ctype, cdata, name)
        
    elif action == 'show':
        conn.close()
        await deliver_content(update, ctype, cdata, name)

async def deliver_content(update, ctype, cdata, name):
    kb = [[InlineKeyboardButton("🔙 返回商城", callback_data='menu_shop')]]
    caption = f"📦 **{name}**"
    try:
        await update.callback_query.message.delete() # 删掉确认框
        if ctype == 'text':
            await update.callback_query.message.reply_text(f"{caption}\n\n{cdata}", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        elif ctype == 'photo':
            await update.callback_query.message.reply_photo(cdata, caption=caption, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        elif ctype == 'video':
            await update.callback_query.message.reply_video(cdata, caption=caption, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        elif ctype == 'document':
            await update.callback_query.message.reply_document(cdata, caption=caption, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Delivery failed: {e}")

# === 排行榜与历史 ===
async def view_stats(update: Update, context: ContextTypes.DEFAULT_TYPE, mode):
    user_id = str(update.effective_user.id)
    conn = get_db_connection()
    cur = conn.cursor()
    kb = [[InlineKeyboardButton("🔙 返回", callback_data='menu_points')]]
    
    if mode == 'history':
        cur.execute("SELECT change_amount, description, created_at FROM point_history WHERE user_id = %s ORDER BY created_at DESC LIMIT 10", (user_id,))
        rows = cur.fetchall()
        text = "📜 **最近积分明细**\n\n" + "\n".join([f"`{r[2].strftime('%m-%d')}` | {'+' if r[0]>0 else ''}{r[0]} | {r[1]}" for r in rows])
        if not rows: text += "暂无记录"
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        
    elif mode == 'rank':
        three_days = datetime.now() - timedelta(days=3)
        cur.execute("SELECT user_id, SUM(change_amount) as total FROM point_history WHERE change_amount > 0 AND created_at >= %s GROUP BY user_id ORDER BY total DESC LIMIT 10", (three_days,))
        rows = cur.fetchall()
        text = "🏆 **三天积分获取榜** (仅统计获取)\n\n"
        my_rank = "未上榜"
        for idx, (uid, total) in enumerate(rows):
            if uid == user_id: my_rank = f"第 {idx+1} 名"
            text += f"{idx+1}. 用户..{uid[-4:]} —— {total}分\n"
        text += f"\n👤 **您的排名**：{my_rank}"
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    conn.close()

# === 管理后台 ===
async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    kb = [
        [InlineKeyboardButton("➕ 添加商品", callback_data='adm_add'), InlineKeyboardButton("➖ 删除商品", callback_data='adm_del')],
        [InlineKeyboardButton("🔗 绑定频道链接", callback_data='adm_bind')],
        [InlineKeyboardButton("❌ 取消操作", callback_data='adm_cancel')]
    ]
    await update.message.reply_text("🔧 **管理员后台**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data == 'adm_cancel':
        context.user_data.clear()
        await query.edit_message_text("操作已取消。")
        
    elif data == 'adm_add':
        context.user_data['adm_state'] = 'add_name'
        await query.edit_message_text("请输入新商品名称：")
        
    elif data == 'adm_bind':
        context.user_data['adm_state'] = 'bind_links'
        await query.edit_message_text("请发送绑定格式：\n关键词\n链接1\n链接2...")
        
    elif data == 'adm_del':
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, name, is_active FROM shop_products ORDER BY id DESC")
        rows = cur.fetchall()
        conn.close()
        if not rows:
            await query.edit_message_text("没有商品。")
            return
        kb = []
        for pid, name, active in rows:
            status = "🟢" if active else "🔴"
            kb.append([InlineKeyboardButton(f"{status} {name} (ID:{pid})", callback_data=f'adm_toggle_{pid}')])
        kb.append([InlineKeyboardButton("🗑️ 彻底删除 (输入ID)", callback_data='adm_ask_del')])
        await query.edit_message_text("点击切换上下架，或选择彻底删除：", reply_markup=InlineKeyboardMarkup(kb))
        
    elif data.startswith('adm_toggle_'):
        pid = int(data.split('_')[-1])
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE shop_products SET is_active = NOT is_active WHERE id = %s", (pid,))
        conn.commit()
        conn.close()
        await query.answer("状态已更新")
        
    elif data == 'adm_ask_del':
        context.user_data['adm_state'] = 'del_id'
        await query.edit_message_text("请输入要彻底删除的商品 ID (数字)：")

# === 全局消息处理 (路由中心) ===

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text
    state = context.user_data.get('state')
    adm_state = context.user_data.get('adm_state')
    
    # 1. 管理员逻辑
    if user_id == str(ADMIN_ID) and adm_state:
        if adm_state == 'add_name':
            context.user_data['new_name'] = text
            context.user_data['adm_state'] = 'add_cost'
            await update.message.reply_text("请输入所需积分 (数字)：")
            
        elif adm_state == 'add_cost':
            if text.isdigit():
                context.user_data['new_cost'] = int(text)
                context.user_data['adm_state'] = 'add_content'
                await update.message.reply_text("请发送商品内容 (文字/图片/视频/文件)：")
            else:
                await update.message.reply_text("必须是数字，请重试。")
                
        elif adm_state == 'add_content': # 仅处理文字内容
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("INSERT INTO shop_products (name, cost, content_type, content_data) VALUES (%s,%s,%s,%s)",
                        (context.user_data['new_name'], context.user_data['new_cost'], 'text', text))
            conn.commit()
            conn.close()
            await update.message.reply_text("✅ 商品上架成功！")
            context.user_data.clear()
            
        elif adm_state == 'del_id':
            if text.isdigit():
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("DELETE FROM shop_products WHERE id = %s", (int(text),))
                conn.commit()
                conn.close()
                await update.message.reply_text("✅ 删除成功。")
            context.user_data.clear()
            
        elif adm_state == 'bind_links':
            lines = text.strip().split('\n')
            if len(lines) >= 2:
                cmd = lines[0].strip()
                cnt = 0
                for link in lines[1:]:
                    cid, mid = parse_telegram_link(link.strip())
                    if cid and mid:
                        save_binding(cmd, cid, mid)
                        cnt += 1
                await update.message.reply_text(f"✅ 已绑定 {cnt} 条消息到命令 `{cmd}`", parse_mode='Markdown')
            else:
                await update.message.reply_text("❌ 格式错误。")
            context.user_data.clear()
        return

    # 2. 用户订单输入逻辑
    if state and state.startswith('waiting_'):
        clean_text = text.strip()
        udata = get_user_data(user_id)
        
        # VIP 订单 (20260)
        if state == 'waiting_vip_order':
            if udata.get('vip_locked_until') and datetime.now() < udata['vip_locked_until']:
                context.user_data.clear()
                await start(update, context)
                return

            if clean_text.startswith("20260"):
                # 成功
                update_user_status(user_id, {'is_vip': True, 'vip_fail_count': 0, 'vip_locked_until': None})
                kb = [[InlineKeyboardButton("👉 点击加入会员群", url="https://t.me/+495j5rWmApsxYzg9")]]
                await update.message.reply_text("🎉 **验证成功！**\n\n欢迎加入尊贵的VIP会员群！", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
                context.user_data.clear()
            else:
                fails = udata.get('vip_fail_count', 0) + 1
                if fails >= 2:
                    lock_time = datetime.now() + timedelta(hours=5)
                    update_user_status(user_id, {'vip_fail_count': fails, 'vip_locked_until': lock_time})
                    await update.message.reply_text("❌ 连续失败2次，已锁定 5 小时。")
                    context.user_data.clear()
                    await start(update, context)
                else:
                    update_user_status(user_id, {'vip_fail_count': fails})
                    await update.message.reply_text(f"❌ 未查询到订单，请重试 ({fails}/2)。")
            return

        # 充值订单 (4200/4768)
        if state in ['waiting_wechat_order', 'waiting_alipay_order']:
            if udata.get('recharge_locked_until') and datetime.now() < udata['recharge_locked_until']:
                 await update.message.reply_text("⛔️ 充值已锁定。")
                 context.user_data.clear()
                 await points_menu(update, context)
                 return
                 
            is_wechat = 'wechat' in state
            valid = (is_wechat and clean_text.startswith("4200")) or (not is_wechat and clean_text.startswith("4768"))
            
            if valid:
                add_points(user_id, 100, 'recharge', '微信充值' if is_wechat else '支付宝充值')
                update_user_status(user_id, {'wechat_used' if is_wechat else 'alipay_used': True, 'recharge_fail_count': 0, 'recharge_locked_until': None})
                await update.message.reply_text("🎉 **充值成功！** +100 积分", parse_mode='Markdown')
                context.user_data.clear()
                await points_menu(update, context)
            else:
                fails = udata.get('recharge_fail_count', 0) + 1
                if fails >= 2:
                    lock_time = datetime.now() + timedelta(hours=10)
                    update_user_status(user_id, {'recharge_fail_count': fails, 'recharge_locked_until': lock_time})
                    await update.message.reply_text("❌ 订单识别失败，充值功能锁定 10 小时。")
                    context.user_data.clear()
                    await points_menu(update, context)
                else:
                    update_user_status(user_id, {'recharge_fail_count': fails})
                    await update.message.reply_text("❌ 识别失败，请检查单号重试。")
            return

    # 3. 隐秘转发逻辑 (关键词触发)
    if text and not text.startswith('/'):
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT from_chat_id, message_id FROM command_bindings WHERE command = %s", (text.strip().upper(),))
        binds = cur.fetchall()
        conn.close()
        
        if binds:
            try: await update.message.delete() # 删指令
            except: pass
            
            sent_ids = []
            for cid, mid in binds:
                try:
                    msg = await context.bot.copy_message(chat_id=update.effective_chat.id, from_chat_id=cid, message_id=mid)
                    sent_ids.append(msg.message_id)
                except Exception as e:
                    logger.error(f"Copy fail: {e}")
            
            if sent_ids:
                context.job_queue.run_once(delayed_delete, 1200, data={'chat_id': update.effective_chat.id, 'msg_ids': sent_ids})
            return

        # 没匹配到命令 -> 回首页
        await start(update, context)

async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    # 管理员添加商品素材
    if user_id == str(ADMIN_ID) and context.user_data.get('adm_state') == 'add_content':
        ctype, cdata = 'unknown', ''
        if update.message.photo: ctype, cdata = 'photo', update.message.photo[-1].file_id
        elif update.message.video: ctype, cdata = 'video', update.message.video.file_id
        elif update.message.document: ctype, cdata = 'document', update.message.document.file_id
        
        if ctype != 'unknown':
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("INSERT INTO shop_products (name, cost, content_type, content_data) VALUES (%s,%s,%s,%s)",
                        (context.user_data['new_name'], context.user_data['new_cost'], ctype, cdata))
            conn.commit()
            conn.close()
            await update.message.reply_text("✅ 商品(媒体)上架成功！")
            context.user_data.clear()
        return

    # 管理员获取 ID (非状态下)
    if user_id == str(ADMIN_ID):
        fid = ''
        if update.message.photo: fid = update.message.photo[-1].file_id
        elif update.message.video: fid = update.message.video.file_id
        elif update.message.document: fid = update.message.document.file_id
        if fid: await update.message.reply_text(f"📄 File ID: `{fid}`", parse_mode='Markdown')

# === 按钮路由 ===
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data == 'back_home': await start(update, context)
    elif data == 'start_verify': await vip_flow(update, context, 'intro')
    elif data == 'vip_input': await vip_flow(update, context, 'input')
    
    elif data == 'menu_points': await points_menu(update, context)
    elif data == 'do_checkin': await handle_checkin(update, context)
    elif data == 'menu_recharge': await recharge_menu(update, context)
    elif data.startswith('pay_'): await show_payment(update, context, data.split('_')[1])
    elif data.startswith('verify_pay_'): await verify_pay_prompt(update, context, data.split('_')[2])
    
    elif data == 'menu_shop': await shop_list(update, context)
    elif data.startswith('shop_confirm_'): await process_shop(update, context, 'confirm', data.split('_')[2])
    elif data.startswith('shop_buy_'): await process_shop(update, context, 'buy', data.split('_')[2])
    elif data.startswith('shop_show_'): await process_shop(update, context, 'show', data.split('_')[2])
    
    elif data == 'view_history': await view_stats(update, context, 'history')
    elif data == 'view_rank': await view_stats(update, context, 'rank')
    
    elif data.startswith('adm_'): await admin_callback(update, context)

if __name__ == '__main__':
    if DATABASE_URL: init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_menu))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO, file_handler))
    
    print("Bot is running...")
    app.run_polling()
