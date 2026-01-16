from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ChatMemberHandler
)
import os
import random
import re
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor

# ==============================================
# 🛠️ 核心配置区（无需修改的固定配置）
# ==============================================
ALLOWED_WELCOME_GROUPS = [-1002520416718, -1002933211039]
VIP_GROUP_LINK = "https://t.me/+495j5rWmApsxYzg9"

# 所有File ID严格使用你提供的原始值
FILE_VIP_INFO = "AgACAgUAAxkBAAIBJ2loboOm15d-Qog2KkzAVSTLG-1eAAKaD2sbQNhBV_UKRl5JPolfAQADAgADeAADOAQ"
FILE_ORDER_GUIDE = "AgACAgUAAxkBAAIBHWlobOW8SVMC9dk6a5KquMiQHPh1AAKVD2sbQNhBV9mV11AQnf1xAQADAgADeQADOAQ"
FILE_WECHAT_PAY = "AgACAgUAAxkBAAIBImlobmPLtn9DWUFZJ53t1mhkVIA7AAKYD2sbQNhBV_A-2IdqoG-dAQADAgADeAADOAQ"
FILE_WECHAT_ORDER = "AgACAgUAAxkBAAIBLWlocIlhveHnlgntE7dGi1ri56i2AAKeD2sbQNhBVyZ8_L3zE7qwAQADAgADeQADOAQ"
FILE_ALIPAY_PAY = "AgACAgUAAxkBAAIBJWlobnt_eXxhfHqg5bpF8WFwDDESAAKZD2sbQNhBVyWCVUCv9Q3iAQADAgADeAADOAQ"
FILE_ALIPAY_ORDER = "AgACAgUAAxkBAAIBMGlocJCdAlLyJie451mVeM6gi7xhAAKfD2sbQNhBV-EDx2qKNqc-AQADAgADeQADOAQ"

# 固定冷却规则
COOL_DOWN_AUTH = 5 * 3600
COOL_DOWN_RECHARGE = 10 * 3600

# 从Railway环境变量读取管理员ID
ADMIN_USER_IDS = []
admin_ids_env = os.getenv("ADMIN_IDS", "")
if admin_ids_env:
    try:
        ADMIN_USER_IDS = [int(id.strip()) for id in admin_ids_env.split(",")]
    except:
        ADMIN_USER_IDS = []

# ==============================================
# 🗄️ 数据库自动初始化
# ==============================================
def init_db():
    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        with conn.cursor() as cur:
            # 1. 先创建表（如果不存在）
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                balance INTEGER DEFAULT 0,
                total_earned INTEGER DEFAULT 0,
                last_sign_at TIMESTAMP DEFAULT '1970-01-01 00:00:00',
                has_received_join_points BOOLEAN DEFAULT FALSE,
                wechat_used BOOLEAN DEFAULT FALSE,
                alipay_used BOOLEAN DEFAULT FALSE,
                auth_retry INTEGER DEFAULT 0,
                auth_cooldown TIMESTAMP DEFAULT '1970-01-01 00:00:00',
                recharge_retry INTEGER DEFAULT 0,
                recharge_cooldown TIMESTAMP DEFAULT '1970-01-01 00:00:00',
                current_state VARCHAR(50) DEFAULT 'welcome'
            )
            """)

            # 2. 自动补全缺失的字段（针对已存在的旧表）
            cur.execute("ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS auth_cooldown TIMESTAMP DEFAULT '1970-01-01 00:00:00'")
            cur.execute("ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS recharge_cooldown TIMESTAMP DEFAULT '1970-01-01 00:00:00'")
            cur.execute("ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS current_state VARCHAR(50) DEFAULT 'welcome'")

            # 其他表初始化保持不变
            cur.execute("""
            CREATE TABLE IF NOT EXISTS point_records (
                id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(user_id),
                type VARCHAR(10) NOT NULL CHECK (type IN ('earn', 'spend')),
                amount INTEGER NOT NULL,
                remark TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

            # 兑换商品表和其他表初始化保持不变...

        conn.commit()
        conn.close()
        print("数据库初始化成功，所有字段已补全")
    except Exception as e:
        print(f"数据库初始化失败: {str(e)}")
# ==============================================
# 🧩 核心工具函数
# ==============================================
def get_user_data(user_id):
    """安全获取用户数据，绝对不会出现KeyError"""
    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
            user = cur.fetchone()
            if not user:
                cur.execute("INSERT INTO users (user_id) VALUES (%s) RETURNING *", (user_id,))
                user = cur.fetchone()
        conn.commit()
        conn.close()
        return user
    except Exception as e:
        print(f"获取用户数据失败: {str(e)}")
        # 强制返回完整的默认值字典，彻底避免KeyError
        return {
            "user_id": user_id,
            "balance": 0,
            "total_earned": 0,
            "last_sign_at": datetime.fromtimestamp(0),
            "has_received_join_points": False,
            "wechat_used": False,
            "alipay_used": False,
            "auth_retry": 0,
            "auth_cooldown": datetime.fromtimestamp(0),
            "recharge_retry": 0,
            "recharge_cooldown": datetime.fromtimestamp(0),
            "current_state": "welcome"
        }

# ==============================================
# 🎬 群聊核心逻辑
# ==============================================
async def group_welcome_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_member = update.chat_member
    chat_id = chat_member.chat.id
    new_member = chat_member.new_chat_member
    if chat_id not in ALLOWED_WELCOME_GROUPS or new_member.is_bot or new_member.is_chat:
        return
    user_id = new_member.user.id
    user_name = new_member.user.first_name
    user = get_user_data(user_id)
    if not user:
        return

    welcome_base = f"""👋 <b>欢迎 {user_name} 加入【VIP中转】大家庭！</b>
我是守门员小卫😎，你的专属服务助手

💎 在这里你可以享受：
✅ 每日签到、充值获取积分，兑换海量专属商品
✅ 验证身份后加入VIP专属会员群，享受绿色通道
✅ 一键获取专属中转资源，全程无忧
"""
    if not user['has_received_join_points']:
        update_user_data(user_id, has_received_join_points=True, balance=user['balance'] + 20)
        add_point_record(user_id, "earn", 20, "首次进群专属福利")
        welcome_msg = welcome_base + "\n🎉 <b>首次进群专属福利：已为你发放20积分</b>，可前往积分中心查看~"
    else:
        welcome_msg = welcome_base + "\n😉 很高兴再次见到你，如需查看积分或兑换商品可点击下方按钮"

    keyboard = [
        [InlineKeyboardButton("🏆 我的积分中心", callback_data="points_center")],
        [InlineKeyboardButton("🚪 申请VIP会员群", url=VIP_GROUP_LINK)],
        [InlineKeyboardButton("🎁 积分兑换商城", callback_data="exchange_mall")]
    ]
    try:
        await context.bot.send_message(chat_id=chat_id, text=welcome_msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    except:
        pass

async def group_leave_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_member = update.chat_member
    chat_id = chat_member.chat.id
    left_member = chat_member.old_chat_member
    if chat_id not in ALLOWED_WELCOME_GROUPS or left_member.user.is_bot:
        return
    user_id = left_member.user.id
    user_name = left_member.user.first_name
    user = get_user_data(user_id)
    if not user:
        return

    if user['has_received_join_points']:
        deduct_amount = min(20, user['balance'])
        if deduct_amount > 0:
            update_user_data(user_id, balance=user['balance'] - deduct_amount)
            add_point_record(user_id, "spend", deduct_amount, "退群收回进群专属积分")

    try:
        await context.bot.send_message(chat_id=user_id, text=f"""👋 再见啦 {user_name}！
小卫会乖乖在这里等你回来哒😔
已为你处理进群专属积分（若积分已使用则按剩余可扣减部分收回）
如果之后想回来随时都可以哦~
""")
    except:
        await context.bot.send_message(chat_id=chat_id, text=f"👋 再见啦 {user_name}，期待你下次再来~")

# ==============================================
# 🎬 私聊欢迎语逻辑
# ==============================================
async def welcome_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user_data(user_id)
    if not user:
        await update.message.reply_text("❌ 系统暂时无法服务，请稍后再试")
        return

    if user['auth_cooldown'] > datetime.now():
        remain = int((user['auth_cooldown'] - datetime.now()).total_seconds() // 3600) or 1
        await update.message.reply_text(f"⏳ 请于{remain}小时后再尝试验证")
        return

    update_user_data(user_id, current_state="welcome")
    welcome_msg = """👋 欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~

📢 小卫小卫，守门员小卫！
一键入群，小卫帮你搞定！
新人来报到，小卫查身份！
"""
    keyboard = [
        [InlineKeyboardButton("🚪 开始身份验证", callback_data="auth_start")],
        [InlineKeyboardButton("🏆 我的积分中心", callback_data="points_center")]
    ]
    await update.message.reply_text(welcome_msg, reply_markup=InlineKeyboardMarkup(keyboard))

# ==============================================
# 🎯 核心按钮处理器（修复之前缺失的函数）
# ==============================================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user = get_user_data(user_id)
    if not user:
        await query.edit_message_text("❌ 系统暂时无法服务，请稍后再试")
        return

    # -------------------------- 身份验证相关 --------------------------
    if query.data == "auth_start":
        vip_msg = """💎 VIP会员特权说明：
✅ 专属中转通道
✅ 优先审核入群
✅ 7x24小时客服支持
✅ 定期福利活动
"""
        await query.edit_message_text(vip_msg)
        await context.bot.send_document(chat_id=query.message.chat_id, document=FILE_VIP_INFO)
        keyboard = [[InlineKeyboardButton("✅ 我已完成付款，验证订单", callback_data="auth_verify")]]
        await query.message.reply_text("请确认已完成付款后点击下方按钮", reply_markup=InlineKeyboardMarkup(keyboard))
        update_user_data(user_id, current_state="wait_auth_order", auth_retry=0)

    elif query.data == "auth_verify":
        guide_msg = """📋 快速查找订单号：
1.  打开你完成付款的平台，进入【我的】页面
2.  找到【我的订单/全部账单】入口
3.  定位到对应VIP服务的付款记录，点击【账单详情】
4.  在详情页中复制你的专属订单号即可
"""
        await query.edit_message_text(guide_msg)
        await context.bot.send_document(chat_id=query.message.chat_id, document=FILE_ORDER_GUIDE)
        await query.message.reply_text("请发送你的订单号，我将为你验证身份")

    elif query.data == "join_group":
        await query.edit_message_text(
            "🎉 恭喜你验证成功！点击下方链接加入VIP专属群聊",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎉 立即加入VIP群", url=VIP_GROUP_LINK)]])
        )
        update_user_data(user_id, current_state="welcome")

    # -------------------------- 积分中心相关 --------------------------
    elif query.data == "points_center":
        points_msg = f"""🏆 我的积分中心
当前可用积分：<b>{user['balance']}</b> 分
累计获得积分：<b>{user['total_earned']}</b> 分
"""
        keyboard = [
            [InlineKeyboardButton("🎁 每日签到领积分", callback_data="sign_in")],
            [InlineKeyboardButton("💸 充值获取积分", callback_data="recharge_page")],
            [InlineKeyboardButton("🎁 积分兑换商城", callback_data="exchange_mall")],
            [InlineKeyboardButton("📜 我的积分明细", callback_data="point_records")],
            [InlineKeyboardButton("🏆 积分排行榜", callback_data="rank_list")],
            [InlineKeyboardButton("⬅️ 返回首页", callback_data="back_welcome")]
        ]
        await query.edit_message_text(points_msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "sign_in":
        today = datetime.now().date()
        last_sign_date = user['last_sign_at'].date() if user['last_sign_at'] else None
        if last_sign_date == today:
            await query.edit_message_text("✅ 你今日已签到过啦，明天再来吧～", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回积分中心", callback_data="points_center")]]))
        else:
            add_points = random.randint(3, 8)
            update_user_data(user_id, balance=user['balance'] + add_points, last_sign_at=datetime.now())
            add_point_record(user_id, "earn", add_points, "每日签到福利")
            await query.edit_message_text(f"🎉 签到成功！获得 {add_points} 积分，当前总积分：{user['balance'] + add_points} 分",
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回积分中心", callback_data="points_center")]]))

    elif query.data == "recharge_page":
        if user['recharge_cooldown'] > datetime.now():
            remain = int((user['recharge_cooldown'] - datetime.now()).total_seconds() // 3600) or 1
            await query.edit_message_text(f"⏳ 请于{remain}小时后再尝试充值", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回积分中心", callback_data="points_center")]]))
            return

        warn_msg = """⚠️ 【重要提示】
微信、支付宝充值渠道**各仅可使用1次**，请勿重复发起充值请求！

💎 充值档位：
💰 `5元 = 100积分`
"""
        keyboard = [
            [InlineKeyboardButton("💳 微信快捷充值", callback_data="recharge_wechat")],
            [InlineKeyboardButton("🛒 支付宝快捷充值", callback_data="recharge_alipay")],
            [InlineKeyboardButton("⬅️ 返回积分中心", callback_data="points_center")]
        ]
        await query.edit_message_text(warn_msg, reply_markup=InlineKeyboardMarkup(keyboard))

    # -------------------------- 充值相关 --------------------------
    elif query.data == "recharge_wechat":
        if user['wechat_used']:
            await query.edit_message_text("❌ 微信充值渠道已使用过，无法再次发起", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回充值页面", callback_data="recharge_page")]]))
            return
        update_user_data(user_id, wechat_used=True, recharge_retry=0, current_state="wait_wechat_order")
        await query.edit_message_text("📱 请使用微信扫描下方二维码完成充值")
        await context.bot.send_document(chat_id=query.message.chat_id, document=FILE_WECHAT_PAY)
        await query.message.reply_text("💰 5元即可获得100积分", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 我已完成微信支付，验证订单", callback_data="wechat_verify")]]))

    elif query.data == "wechat_verify":
        guide_msg = """📋 快速查找微信交易单号：
1.  打开微信 → 我 → 服务 → 钱包 → 账单
2.  找到对应5元的充值交易，点击进入账单详情
3.  复制页面底部的【交易单号】并发送给我
"""
        await query.edit_message_text(guide_msg)
        await context.bot.send_document(chat_id=query.message.chat_id, document=FILE_WECHAT_ORDER)

    elif query.data == "recharge_alipay":
        if user['alipay_used']:
            await query.edit_message_text("❌ 支付宝充值渠道已使用过，无法再次发起", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回充值页面", callback_data="recharge_page")]]))
            return
        update_user_data(user_id, alipay_used=True, recharge_retry=0, current_state="wait_alipay_order")
        await query.edit_message_text("📱 请使用支付宝扫描下方二维码完成充值")
        await context.bot.send_document(chat_id=query.message.chat_id, document=FILE_ALIPAY_PAY)
        await query.message.reply_text("💰 5元即可获得100积分", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 我已完成支付宝支付，验证订单", callback_data="alipay_verify")]]))

    elif query.data == "alipay_verify":
        guide_msg = """📋 快速查找支付宝商家订单号：
1.  打开支付宝 → 我 → 账单
2.  找到对应5元的充值交易，点击进入账单详情
3.  点击右上角【···】→ 复制【商家订单号】并发送给我
"""
        await query.edit_message_text(guide_msg)
        await context.bot.send_document(chat_id=query.message.chat_id, document=FILE_ALIPAY_ORDER)

    # -------------------------- 兑换相关 --------------------------
    elif query.data == "exchange_mall":
        conn = psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM exchange_goods WHERE is_on_shelf = TRUE ORDER BY required_points ASC")
            goods_list = cur.fetchall()
            cur.execute("SELECT goods_id FROM user_exchange WHERE user_id = %s", (user_id,))
            exchanged_goods = [x['goods_id'] for x in cur.fetchall()]
        keyboard = []
        for goods in goods_list:
            if goods['goods_id'] in exchanged_goods:
                btn_text = f"🎁 {goods['name']} | 已兑换"
            else:
                btn_text = f"🎁 {goods['name']} | 需{goods['required_points']}积分"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"exchange_{goods['goods_id']}")])
        keyboard.append([InlineKeyboardButton("⬅️ 返回积分中心", callback_data="points_center")])
        await query.edit_message_text("🎁 积分兑换商城", reply_markup=InlineKeyboardMarkup(keyboard))
        conn.close()

    elif query.data.startswith("exchange_"):
        goods_id = query.data.replace("exchange_", "")
        conn = psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM exchange_goods WHERE goods_id = %s", (goods_id,))
            goods = cur.fetchone()
            cur.execute("SELECT * FROM user_exchange WHERE user_id = %s AND goods_id = %s", (user_id, goods_id))
            has_exchanged = cur.fetchone() is not None

        if has_exchanged:
            await query.edit_message_text(goods['content'])
            await query.message.reply_text("⬅️ 返回兑换商城", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("返回兑换商城", callback_data="exchange_mall")]]))
            conn.close()
            return

        confirm_keyboard = [
            [InlineKeyboardButton("✅ 确认兑换", callback_data=f"confirm_exchange_{goods_id}")],
            [InlineKeyboardButton("❌ 取消兑换", callback_data="exchange_mall")]
        ]
        await query.edit_message_text(f"🎁 确认使用{goods['required_points']}积分兑换【{goods['name']}】吗？", reply_markup=InlineKeyboardMarkup(confirm_keyboard))
        conn.close()

    elif query.data.startswith("confirm_exchange_"):
        goods_id = query.data.replace("confirm_exchange_", "")
        conn = psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM exchange_goods WHERE goods_id = %s", (goods_id,))
            goods = cur.fetchone()

        if user['balance'] < goods['required_points']:
            await query.edit_message_text("💸 积分余额不足，无法兑换该商品，请先获取更多积分~", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回兑换商城", callback_data="exchange_mall")]]))
            conn.close()
            return

        update_user_data(user_id, balance=user['balance'] - goods['required_points'])
        add_point_record(user_id, "spend", goods['required_points'], f"兑换商品：{goods['name']}")
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        with conn.cursor() as cur:
            cur.execute("INSERT INTO user_exchange (user_id, goods_id) VALUES (%s, %s)", (user_id, goods_id))
        conn.commit()
        conn.close()

        await query.edit_message_text(f"🎉 兑换成功！{goods['content']}")
        await query.message.reply_text("⬅️ 返回兑换商城", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("返回兑换商城", callback_data="exchange_mall")]]))

    # -------------------------- 管理员相关 --------------------------
    elif query.data == "show_files":
        file_msg = f"""📋 当前配置的File ID：
1. VIP特权说明：{FILE_VIP_INFO}
2. 身份验证订单教程：{FILE_ORDER_GUIDE}
3. 微信充值二维码：{FILE_WECHAT_PAY}
4. 微信订单教程：{FILE_WECHAT_ORDER}
5. 支付宝充值二维码：{FILE_ALIPAY_PAY}
6. 支付宝订单教程：{FILE_ALIPAY_ORDER}
"""
        await query.edit_message_text(file_msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回管理员面板", callback_data="admin_panel")]]))

    elif query.data == "goods_manage":
        keyboard = [
            [InlineKeyboardButton("➕ 上架全新商品", callback_data="admin_add_goods")],
            [InlineKeyboardButton("🗑️ 删除指定商品", callback_data="admin_del_goods")],
            [InlineKeyboardButton("📋 查看全部商品", callback_data="admin_list_goods")],
            [InlineKeyboardButton("⬅️ 返回管理员面板", callback_data="admin_panel")]
        ]
        await query.edit_message_text("📦 商品管理中心", reply_markup=InlineKeyboardMarkup(keyboard))

    # -------------------------- 返回按钮 --------------------------
    elif query.data == "back_welcome":
        await welcome_flow(update, context)

# ==============================================
# 🎯 核心消息处理器（修复之前缺失的函数）
# ==============================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user_data(user_id)
    if not user:
        await update.message.reply_text("❌ 系统暂时无法服务，请稍后再试")
        return
    input_text = update.message.text.strip()

    # 身份验证订单处理
    if user['current_state'] == "wait_auth_order":
        if input_text.startswith("20260"):
            await update.message.reply_text("✅ 订单验证成功！恭喜你成为VIP会员")
            keyboard = [[InlineKeyboardButton("🎉 立即加入VIP会员群", callback_data="join_group")]]
            await update.message.reply_text("点击下方按钮进入专属群聊", reply_markup=InlineKeyboardMarkup(keyboard))
            update_user_data(user_id, current_state="welcome")
        else:
            new_retry = user['auth_retry'] + 1
            if new_retry < 2:
                update_user_data(user_id, auth_retry=new_retry)
                await update.message.reply_text("❌ 未查询到订单信息，请重试")
            else:
                update_user_data(user_id, auth_cooldown=datetime.now() + timedelta(seconds=COOL_DOWN_AUTH), current_state="welcome")
                await update.message.reply_text("❌ 已重试2次，请于5小时后再尝试验证")

    # 微信充值订单处理
    elif user['current_state'] == "wait_wechat_order":
        if input_text.startswith("4200"):
            update_user_data(user_id, balance=user['balance'] + 100, current_state="welcome")
            add_point_record(user_id, "earn", 100, "微信充值福利")
            await update.message.reply_text(f"✅ 充值验证成功！已为你添加100积分，当前总积分：{user['balance'] + 100} 分",
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回积分中心", callback_data="points_center")]]))
        else:
            new_retry = user['recharge_retry'] + 1
            if new_retry < 2:
                update_user_data(user_id, recharge_retry=new_retry)
                await update.message.reply_text("❌ 订单识别失败，请重试")
            else:
                update_user_data(user_id, recharge_cooldown=datetime.now() + timedelta(seconds=COOL_DOWN_RECHARGE), current_state="welcome")
                await update.message.reply_text("❌ 已重试2次，请于10小时后再尝试充值",
                                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回积分中心", callback_data="points_center")]]))

    # 支付宝充值订单处理
    elif user['current_state'] == "wait_alipay_order":
        if input_text.startswith("4768"):
            update_user_data(user_id, balance=user['balance'] + 100, current_state="welcome")
            add_point_record(user_id, "earn", 100, "支付宝充值福利")
            await update.message.reply_text(f"✅ 充值验证成功！已为你添加100积分，当前总积分：{user['balance'] + 100} 分",
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回积分中心", callback_data="points_center")]]))
        else:
            new_retry = user['recharge_retry'] + 1
            if new_retry < 2:
                update_user_data(user_id, recharge_retry=new_retry)
                await update.message.reply_text("❌ 订单识别失败，请重试")
            else:
                update_user_data(user_id, recharge_cooldown=datetime.now() + timedelta(seconds=COOL_DOWN_RECHARGE), current_state="welcome")
                await update.message.reply_text("❌ 已重试2次，请于10小时后再尝试充值",
                                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回积分中心", callback_data="points_center")]]))

    # 非指定状态自动返回首页
    else:
        await welcome_flow(update, context)

# ==============================================
# 🎬 管理员后台逻辑
# ==============================================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ 无管理员权限")
        return

    admin_msg = """🤵 欢迎管理员大大！我是小卫，为你提供专属后台服务

📦 可进行商品上下架、查看File ID等操作
"""
    keyboard = [
        [InlineKeyboardButton("📄 查看当前File ID", callback_data="show_files")],
        [InlineKeyboardButton("📦 商品管理中心", callback_data="goods_manage")],
        [InlineKeyboardButton("⬅️ 返回首页", callback_data="back_welcome")]
    ]
    await update.message.reply_text(admin_msg, reply_markup=InlineKeyboardMarkup(keyboard))

# ==============================================
# 🚀 主函数
# ==============================================
def main():
    # 彻底忽略所有python-telegram-bot的废弃警告
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="telegram")
    warnings.filterwarnings("ignore", category=PTBDeprecationWarning, module="telegram")

    init_db()
    bot_token = os.getenv("BOT_TOKEN")
    db_url = os.getenv("DATABASE_URL")
    
    if not bot_token or not db_url:
        print("❌ 请先在Railway配置BOT_TOKEN和DATABASE_URL环境变量")
        return

    app = ApplicationBuilder().token(bot_token).build()

    # 全局错误处理器
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        print(f"⚠️ 捕获到错误: {str(context.error)}")

    app.add_error_handler(error_handler)

    # 注册所有处理器...

    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
        timeout=30,
        read_timeout=30
    )
if __name__ == "__main__":
    main()
