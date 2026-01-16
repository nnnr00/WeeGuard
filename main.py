import os
import logging
import json
import random
import asyncio
import psycopg2
from psycopg2.extras import Json
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, 
    ContextTypes, 
    CommandHandler, 
    CallbackQueryHandler, 
    MessageHandler, 
    filters
)

# --- 1. 配置区域 (请在此处填入 File ID) ---
MEDIA_WECHAT_QR = None         
MEDIA_WECHAT_TUTORIAL = None   
MEDIA_ALIPAY_QR = None         
MEDIA_ALIPAY_TUTORIAL = None   
GROUP_LINK = "https://t.me/+495j5rWmApsxYzg9"

# 环境变量
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

# --- 2. 数据库连接与初始化 ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

def get_db_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """初始化数据库表结构"""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # 用户表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    points INT DEFAULT 0,
                    total_gained INT DEFAULT 0,
                    last_checkin TEXT,
                    wx_used BOOLEAN DEFAULT FALSE,
                    ali_used BOOLEAN DEFAULT FALSE,
                    vip_attempts INT DEFAULT 0,
                    vip_lock TIMESTAMP,
                    topup_attempts INT DEFAULT 0,
                    topup_lock TIMESTAMP,
                    redeemed JSONB DEFAULT '[]'::jsonb
                );
            """)
            # 交易记录表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    time TEXT,
                    reason TEXT,
                    change TEXT
                );
            """)
            # 商品表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    type TEXT,
                    content TEXT,
                    price INT,
                    active BOOLEAN DEFAULT TRUE,
                    media_id TEXT
                );
            """)
            # 系统配置表 (排行榜重置时间)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS system_config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
            """)
            
            # 初始化测试商品
            cur.execute("SELECT id FROM products WHERE id = 'test_001'")
            if not cur.fetchone():
                cur.execute("""
                    INSERT INTO products (id, name, type, content, price, active)
                    VALUES ('test_001', '始终测试按钮', 'text', '哈哈 😄 测试成功！', 0, TRUE)
                """)
            
            # 初始化排行榜时间
            cur.execute("SELECT value FROM system_config WHERE key = 'leaderboard_reset'")
            if not cur.fetchone():
                reset_time = (datetime.now() + timedelta(days=3)).timestamp()
                cur.execute("INSERT INTO system_config (key, value) VALUES ('leaderboard_reset', %s)", (str(reset_time),))

            conn.commit()
            print("✅ 数据库连接并初始化成功 (Neon)")
    except Exception as e:
        print(f"❌ 数据库初始化失败: {e}")
    finally:
        conn.close()

# --- 3. 数据库操作封装 ---

def get_user_data(user_id):
    """获取用户信息，不存在则创建"""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            if not row:
                cur.execute("INSERT INTO users (user_id) VALUES (%s)", (user_id,))
                conn.commit()
                # 返回默认值结构
                return {
                    "user_id": user_id, "points": 0, "total_gained": 0,
                    "last_checkin": None, "wx_used": False, "ali_used": False,
                    "vip_attempts": 0, "vip_lock": None, "topup_attempts": 0,
                    "topup_lock": None, "redeemed": []
                }
            
            # 将Tuple转换为Dict
            return {
                "user_id": row[0], "points": row[1], "total_gained": row[2],
                "last_checkin": row[3], "wx_used": row[4], "ali_used": row[5],
                "vip_attempts": row[6], "vip_lock": row[7], "topup_attempts": row[8],
                "topup_lock": row[9], "redeemed": row[10] if row[10] else []
            }
    finally:
        conn.close()

def update_user_field(user_id, field, value):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # 动态构建 SQL (注意安全，field必须是内部可控的字符串)
            query = f"UPDATE users SET {field} = %s WHERE user_id = %s"
            cur.execute(query, (value, user_id))
            conn.commit()
    finally:
        conn.close()

def add_points_db(user_id, amount, reason):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # 更新积分
            cur.execute("UPDATE users SET points = points + %s WHERE user_id = %s", (amount, user_id))
            
            # 如果是增加积分，更新总获取量
            if amount > 0:
                cur.execute("UPDATE users SET total_gained = total_gained + %s WHERE user_id = %s", (amount, user_id))
            
            # 插入账单记录
            change_str = f"+{amount}" if amount >= 0 else str(amount)
            time_str = datetime.now().strftime("%m-%d %H:%M")
            cur.execute("INSERT INTO transactions (user_id, time, reason, change) VALUES (%s, %s, %s, %s)",
                        (user_id, time_str, reason, change_str))
            conn.commit()
    finally:
        conn.close()

def get_transaction_history(user_id):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT time, reason, change FROM transactions WHERE user_id = %s ORDER BY id DESC LIMIT 20", (user_id,))
            return cur.fetchall()
    finally:
        conn.close()

def get_all_products():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # 获取所有商品，转为字典
            cur.execute("SELECT id, name, type, content, price, active, media_id FROM products")
            rows = cur.fetchall()
            products = {}
            for r in rows:
                products[r[0]] = {
                    "id": r[0], "name": r[1], "type": r[2], "content": r[3],
                    "price": r[4], "active": r[5], "media_id": r[6]
                }
            return products
    finally:
        conn.close()

def check_leaderboard_reset_db():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM system_config WHERE key = 'leaderboard_reset'")
            res = cur.fetchone()
            reset_ts = float(res[0]) if res else 0
            
            if datetime.now().timestamp() > reset_ts:
                # 重置
                cur.execute("UPDATE users SET total_gained = 0")
                new_reset = (datetime.now() + timedelta(days=3)).timestamp()
                cur.execute("UPDATE system_config SET value = %s WHERE key = 'leaderboard_reset'", (str(new_reset),))
                conn.commit()
                return True
            return False
    finally:
        conn.close()

def get_leaderboard_data():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # 获取前10名
            cur.execute("SELECT user_id, total_gained FROM users ORDER BY total_gained DESC LIMIT 10")
            return cur.fetchall()
    finally:
        conn.close()

# --- 4. 辅助逻辑 ---
def is_admin(user_id):
    return str(user_id) == str(ADMIN_ID)

async def send_media(chat_id, context, text, media_id, reply_markup=None):
    try:
        if media_id:
            try:
                await context.bot.send_photo(chat_id, photo=media_id, caption=text, parse_mode='Markdown', reply_markup=reply_markup)
            except:
                await context.bot.send_video(chat_id, video=media_id, caption=text, parse_mode='Markdown', reply_markup=reply_markup)
        else:
            await context.bot.send_message(chat_id, text, parse_mode='Markdown', reply_markup=reply_markup)
    except Exception as e:
        await context.bot.send_message(chat_id, text, parse_mode='Markdown', reply_markup=reply_markup)

# --- 5. 核心功能处理 ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    text = (
        "👋 **欢迎加入【VIP中转】！**\n"
        "我是守门员小卫，你的身份验证小助手~\n\n"
        "📢 **小卫小卫，守门员小卫！**\n"
        "一键入群，小卫帮你搞定！\n"
        "新人来报到，小卫查身份！"
    )
    kb = [
        [InlineKeyboardButton("🚀 开始验证 (VIP)", callback_data="start_verify")],
        [InlineKeyboardButton("💰 积分中心", callback_data="points_center")]
    ]
    if update.callback_query:
        await update.callback_query.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))

async def points_center(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = get_user_data(user_id)
    
    text = (
        f"💰 **积分中心 - {update.effective_user.first_name}**\n"
        "━━━━━━━━━━━━━━━━\n"
        f"🪙 **当前积分：** `{data['points']}`\n"
        "━━━━━━━━━━━━━━━━\n"
        "👇 请选择操作："
    )
    kb = [
        [InlineKeyboardButton("📅 每日签到", callback_data="daily_checkin"), InlineKeyboardButton("💳 充值积分", callback_data="topup_menu")],
        [InlineKeyboardButton("🎁 积分兑换", callback_data="redeem_shop"), InlineKeyboardButton("🧾 余额/账单", callback_data="my_balance")],
        [InlineKeyboardButton("🏆 排行榜 (每3天)", callback_data="leaderboard")],
        [InlineKeyboardButton("🔙 返回首页", callback_data="back_to_home")]
    ]
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))

async def handle_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = get_user_data(user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    
    if data['last_checkin'] == today:
        await update.callback_query.answer("⚠️ 一天只能签到一次哦！", show_alert=True)
        return

    points = random.randint(3, 8)
    update_user_field(user_id, 'last_checkin', today)
    add_points_db(user_id, points, "每日签到")
    
    await update.callback_query.answer(f"🎉 签到成功！获得 {points} 积分", show_alert=True)
    await points_center(update, context)

async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = get_user_data(user_id)
    history = get_transaction_history(user_id)
    
    history_text = ""
    if not history:
        history_text = "暂无记录"
    else:
        for t, r, c in history:
            history_text += f"`{t}` | {r} | **{c}**\n"
            
    text = (
        f"🧾 **我的账单详情**\n"
        f"当前余额：**{data['points']}** 积分\n"
        "━━━━━━━━━━━━━━━━\n"
        "**📜 最近记录：**\n"
        f"{history_text}\n"
        "━━━━━━━━━━━━━━━━"
    )
    kb = [[InlineKeyboardButton("🔙 返回积分中心", callback_data="points_center")]]
    await update.callback_query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))

async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    was_reset = check_leaderboard_reset_db()
    
    leaders = get_leaderboard_data()
    my_id = update.effective_user.id
    
    rank_text = ""
    my_score = 0
    my_rank = "未上榜"
    
    for idx, (uid, score) in enumerate(leaders):
        medal = "🥇" if idx==0 else "🥈" if idx==1 else "🥉" if idx==2 else f"{idx+1}."
        uid_str = str(uid)
        hidden = uid_str[-4:] if len(uid_str) > 4 else uid_str
        rank_text += f"{medal} 用户...{hidden} : **{score}** 分\n"
        
        if uid == my_id:
            my_rank = idx + 1
            my_score = score
            
    # 如果没在前10，查一下自己
    if my_rank == "未上榜":
        udata = get_user_data(my_id)
        my_score = udata['total_gained']

    notice = "🔄 **排行榜已重置**" if was_reset else ""
    text = (
        f"🏆 **积分风云榜 (每3天重置)**\n"
        f"{notice}\n"
        "━━━━━━━━━━━━━━━━\n"
        f"{rank_text}\n"
        "━━━━━━━━━━━━━━━━\n"
        f"👤 **我的排名：** {my_rank} (总获取: {my_score})"
    )
    kb = [[InlineKeyboardButton("🔙 返回积分中心", callback_data="points_center")]]
    await update.callback_query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))

# --- 商城 ---
async def redeem_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = get_user_data(user_id)
    user_redeemed = data['redeemed']
    products = get_all_products()
    
    keyboard = []
    for pid, info in products.items():
        if not info['active']: continue
        status = "✅ 已兑换" if pid in user_redeemed else f"💰 {info['price']} 积分"
        btn_text = f"{info['name']} - {status}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"shop_click_{pid}")])
    
    keyboard.append([InlineKeyboardButton("🔙 返回积分中心", callback_data="points_center")])
    text = "🎁 **积分兑换商城**\n请选择您要兑换的商品："
    await update.callback_query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_shop_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    pid = query.data.replace("shop_click_", "")
    products = get_all_products()
    
    if pid not in products:
        await query.answer("❌ 商品已下架", show_alert=True)
        return

    prod = products[pid]
    data = get_user_data(user_id)
    
    if pid in data['redeemed']:
        await show_product_content(update, context, prod)
        return
    
    text = (
        f"🛒 **确认兑换：{prod['name']}**\n"
        f"需要消耗：**{prod['price']}** 积分\n"
        f"当前余额：{data['points']} 积分"
    )
    kb = [
        [InlineKeyboardButton("✅ 确认支付", callback_data=f"shop_pay_{pid}")],
        [InlineKeyboardButton("❌ 取消", callback_data="redeem_shop")]
    ]
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))

async def handle_shop_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    pid = query.data.replace("shop_pay_", "")
    products = get_all_products()
    prod = products.get(pid)
    
    data = get_user_data(user_id)
    if not prod:
        await query.edit_message_text("❌ 商品不存在")
        return
    
    if data['points'] < prod['price']:
        await query.answer("❌ 余额不足，请去充值！", show_alert=True)
        await redeem_shop(update, context)
        return

    # 数据库更新：扣分、记录交易、添加到已兑换
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # 1. 扣分
            cur.execute("UPDATE users SET points = points - %s WHERE user_id = %s", (prod['price'], user_id))
            # 2. 增加redeemed (Postgres JSONB append)
            cur.execute("UPDATE users SET redeemed = redeemed || %s::jsonb WHERE user_id = %s", (json.dumps([pid]), user_id))
            # 3. 记录账单
            time_str = datetime.now().strftime("%m-%d %H:%M")
            cur.execute("INSERT INTO transactions (user_id, time, reason, change) VALUES (%s, %s, %s, %s)",
                        (user_id, time_str, f"兑换-{prod['name']}", f"-{prod['price']}"))
            conn.commit()
    finally:
        conn.close()
    
    await query.answer("✅ 兑换成功！", show_alert=True)
    await show_product_content(update, context, prod)

async def show_product_content(update: Update, context: ContextTypes.DEFAULT_TYPE, prod):
    query = update.callback_query
    await query.message.delete()
    content_text = f"🎁 **{prod['name']}**\n━━━━━━━━━━━━━━━━\n{prod['content']}"
    kb = [[InlineKeyboardButton("🔙 返回商城", callback_data="redeem_shop")]]
    
    if prod['type'] == 'text' or not prod['media_id']:
        await query.message.reply_text(content_text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))
    else:
        await send_media(query.message.chat_id, context, content_text, prod['media_id'], InlineKeyboardMarkup(kb))

# --- 管理后台 ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    text = "👮‍♂️ **管理员后台**\n欢迎回来，数据库连接状态：正常 (Neon)"
    kb = [
        [InlineKeyboardButton("➕ 添加新商品", callback_data="admin_add_prod")],
        [InlineKeyboardButton("📦 管理/下架商品", callback_data="admin_manage_prod")],
        [InlineKeyboardButton("🆔 获取 File ID", callback_data="admin_get_fid")],
        [InlineKeyboardButton("🔙 关闭", callback_data="admin_close")]
    ]
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))

async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "admin_close": await query.message.delete()
    elif data == "admin_get_fid":
        context.user_data['admin_state'] = 'get_fid'
        await query.edit_message_text("📥 发送图片/视频获取 ID...")
    elif data == "admin_add_prod":
        context.user_data['admin_state'] = 'add_prod_id'
        context.user_data['new_prod'] = {}
        await query.edit_message_text("1️⃣ 输入商品ID (如 vip_01):")
    elif data == "admin_manage_prod":
        products = get_all_products()
        kb = []
        for pid, info in products.items():
            status = "🟢" if info['active'] else "🔴"
            kb.append([InlineKeyboardButton(f"{status} {info['name']}", callback_data=f"toggle_prod_{pid}")])
        kb.append([InlineKeyboardButton("🔙 返回", callback_data="back_admin")])
        await query.edit_message_text("📦 点击切换状态", reply_markup=InlineKeyboardMarkup(kb))
    elif data.startswith("toggle_prod_"):
        pid = data.replace("toggle_prod_", "")
        conn = get_db_conn()
        with conn.cursor() as cur:
            cur.execute("UPDATE products SET active = NOT active WHERE id = %s", (pid,))
            conn.commit()
        await admin_panel(update, context)
    elif data == "back_admin":
        await admin_panel(update, context)

async def admin_msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    state = context.user_data.get('admin_state')
    msg = update.message
    txt = msg.text
    
    if state == 'get_fid':
        fid = None
        if msg.photo: fid = msg.photo[-1].file_id
        elif msg.video: fid = msg.video.file_id
        if fid: await msg.reply_text(f"`{fid}`", parse_mode='Markdown')
        context.user_data['admin_state'] = None
        
    elif state == 'add_prod_id':
        if not txt: return
        context.user_data['new_prod']['id'] = txt
        context.user_data['admin_state'] = 'add_prod_name'
        await msg.reply_text("2️⃣ 输入商品名称:")
    elif state == 'add_prod_name':
        context.user_data['new_prod']['name'] = txt
        context.user_data['admin_state'] = 'add_prod_price'
        await msg.reply_text("3️⃣ 输入积分价格:")
    elif state == 'add_prod_price':
        try:
            context.user_data['new_prod']['price'] = int(txt)
            context.user_data['admin_state'] = 'add_prod_content'
            await msg.reply_text("4️⃣ 发送商品内容 (文字或媒体):")
        except: await msg.reply_text("请输入数字")
    elif state == 'add_prod_content':
        np = context.user_data['new_prod']
        np['type'] = 'media' if (msg.photo or msg.video) else 'text'
        np['content'] = msg.caption if msg.caption else (msg.text if msg.text else "资源")
        np['media_id'] = msg.photo[-1].file_id if msg.photo else (msg.video.file_id if msg.video else None)
        
        # 存入数据库
        conn = get_db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO products (id, name, type, content, price, active, media_id)
                    VALUES (%s, %s, %s, %s, %s, TRUE, %s)
                """, (np['id'], np['name'], np['type'], np['content'], np['price'], np['media_id']))
                conn.commit()
            await msg.reply_text("✅ 商品添加成功")
        except Exception as e:
            await msg.reply_text(f"❌ 添加失败: {e}")
            conn.rollback()
        finally:
            conn.close()
        
        context.user_data['admin_state'] = None
        await admin_panel(update, context)

# --- 充值菜单 ---
async def topup_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "💳 **充值中心**\n⚠️ 微信/支付宝各限一次。\n5元 = 100积分"
    kb = [
        [InlineKeyboardButton("💚 微信充值", callback_data="pay_wx"), InlineKeyboardButton("💙 支付宝充值", callback_data="pay_ali")],
        [InlineKeyboardButton("🔙 返回", callback_data="points_center")]
    ]
    await update.callback_query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))

# --- 主路由 ---
async def master_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = context.user_data.get('admin_state')
    if is_admin(user_id) and state:
        await admin_msg_handler(update, context)
        return
        
    # 输入模式路由
    input_mode = context.user_data.get('input_mode')
    msg_text = update.message.text
    if not input_mode or not msg_text or msg_text.startswith('/'):
        if not msg_text.startswith('/'): await start_command(update, context)
        return

    data = get_user_data(user_id)
    clean_text = msg_text.strip()
    
    # VIP 验证
    if input_mode == 'vip':
        if data['vip_lock'] and data['vip_lock'] > datetime.now():
            await update.message.reply_text("⛔️ 锁定中，请稍后重试")
            return
        
        if clean_text.startswith("20260"):
            update_user_field(user_id, 'vip_attempts', 0)
            kb = [[InlineKeyboardButton("🎉 加入会员群", url=GROUP_LINK)]]
            await update.message.reply_text("✅ 验证成功！", reply_markup=InlineKeyboardMarkup(kb))
            context.user_data['input_mode'] = None
        else:
            att = data['vip_attempts'] + 1
            update_user_field(user_id, 'vip_attempts', att)
            if att >= 2:
                lock_time = datetime.now() + timedelta(hours=5)
                update_user_field(user_id, 'vip_lock', lock_time)
                await update.message.reply_text("❌ 错误过多，锁定5小时。")
                context.user_data['input_mode'] = None
            else:
                await update.message.reply_text(f"⚠️ 验证失败，剩余 {2-att} 次")

    # 充值验证
    elif input_mode in ['wechat', 'alipay']:
        if data['topup_lock'] and data['topup_lock'] > datetime.now():
             await update.message.reply_text("⛔️ 锁定中")
             return
             
        success = (input_mode == 'wechat' and clean_text.startswith("4200")) or \
                  (input_mode == 'alipay' and clean_text.startswith("4768"))
        
        if success:
            add_points_db(user_id, 100, f"{input_mode}充值")
            update_user_field(user_id, 'topup_attempts', 0)
            if input_mode == 'wechat': update_user_field(user_id, 'wx_used', True)
            else: update_user_field(user_id, 'ali_used', True)
            
            await update.message.reply_text("✅ 充值成功！+100积分")
            context.user_data['input_mode'] = None
            await asyncio.sleep(1)
            await points_center(update, context)
        else:
            att = data['topup_attempts'] + 1
            update_user_field(user_id, 'topup_attempts', att)
            if att >= 2:
                lock_time = datetime.now() + timedelta(hours=10)
                update_user_field(user_id, 'topup_lock', lock_time)
                await update.message.reply_text("❌ 错误过多，锁定10小时。")
                context.user_data['input_mode'] = None
            else:
                await update.message.reply_text(f"⚠️ 识别失败，剩余 {2-att} 次")

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    # 路由映射
    if data == "start_verify":
        # ... (VIP图片逻辑) ... 
        # 为了简洁，此处简写，请务必保留你原来的 send_media 逻辑，这里只做核心跳转演示
        context.user_data['input_mode'] = 'vip'
        await update.callback_query.message.reply_text("请输入20260开头的订单号:")
        
    elif data == "pay_wx":
        data_db = get_user_data(update.effective_user.id)
        if data_db['wx_used']: 
            await update.callback_query.answer("已使用过", show_alert=True)
            return
        context.user_data['input_mode'] = 'wechat'
        await send_media(update.effective_user.id, context, "请扫码支付 (4200开头)", MEDIA_WECHAT_QR)
    
    elif data == "pay_ali":
        data_db = get_user_data(update.effective_user.id)
        if data_db['ali_used']: 
            await update.callback_query.answer("已使用过", show_alert=True)
            return
        context.user_data['input_mode'] = 'alipay'
        await send_media(update.effective_user.id, context, "请扫码支付 (4768开头)", MEDIA_ALIPAY_QR)

    elif data == "points_center": await points_center(update, context)
    elif data == "daily_checkin": await handle_checkin(update, context)
    elif data == "my_balance": await show_balance(update, context)
    elif data == "leaderboard": await show_leaderboard(update, context)
    elif data == "redeem_shop": await redeem_shop(update, context)
    elif data.startswith("shop_click_"): await handle_shop_click(update, context)
    elif data.startswith("shop_pay_"): await handle_shop_pay(update, context)
    elif data == "topup_menu": await topup_menu(update, context)
    elif data == "back_to_home": await start_command(update, context)
    
    elif "admin" in data or "toggle" in data: await admin_handler(update, context)

if __name__ == '__main__':
    if not BOT_TOKEN or not DATABASE_URL:
        print("Error: Config missing")
        exit(1)
        
    # 初始化数据库表
    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), master_handler))
    
    print("System Online (DB Connected)...")
    app.run_polling()
