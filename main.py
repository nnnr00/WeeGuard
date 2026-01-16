import os
import random
import time
from datetime import datetime, timedelta
import asyncpg
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ChatMemberHandler,
)

# ============ 【需替换】全局配置 ============
BOT_TOKEN = os.environ.get("BOT_TOKEN")  # 你的Bot Token（@BotFather获取）
ADMIN_ID = int(os.environ.get("ADMIN_ID"))  # 你的Telegram ID（@userinfobot查询）
DATABASE_URL = os.environ.get("DATABASE_URL")  # Neon数据库连接字符串
WELCOME_GROUP_LINK = "https://t.me/WeeBearbot"  # 欢迎语主群链接
VIP_GROUP_LINK = "https://t.me/+495j5rWmApsxYzg9"  # 会员福利群链接
ALLOWED_WELCOME_GROUPS = [-1002520416718, -1002933211039]  # 允许欢迎的群ID（@RawDataBot查询）

# 初始商品（仅保留测试商品）
INITIAL_PRODUCTS = [
    {"name": "测试兑换", "price": 0, "type": "text", "content": "哈哈", "is_active": True},
]
# ==========================================

# 全局数据库连接池
db_pool = None

# ================= 1. 数据库初始化 =================
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    await db_pool.execute("""
        CREATE TABLE IF NOT EXISTS user_points (
            user_id BIGINT PRIMARY KEY,
            points INT DEFAULT 0,
            last_sign_in TIMESTAMP DEFAULT NULL,
            first_join_group BOOLEAN DEFAULT FALSE
        );
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100),
            price INT,
            type VARCHAR(20),
            content TEXT,
            is_active BOOLEAN DEFAULT TRUE
        );
        CREATE TABLE IF NOT EXISTS forward_library (
            id SERIAL PRIMARY KEY,
            command VARCHAR(50) UNIQUE,
            channel_msg_url TEXT,
            is_active BOOLEAN DEFAULT TRUE
        );
        CREATE TABLE IF NOT EXISTS user_purchases (
            user_id BIGINT,
            command VARCHAR(50),
            purchase_time TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (user_id, command)
        );
    """)
    for p in INITIAL_PRODUCTS:
        await db_pool.execute("""
            INSERT INTO products (name, price, type, content, is_active) 
            VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING
        """, p["name"], p["price"], p["type"], p["content"], p["is_active"])

# ================= 2. 群组交互 =================
async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.chat_member.new_chat_members:
        if member.is_bot:
            continue
        chat_id = update.chat_member.chat.id
        user_id = member.id
        user = await get_user(user_id)

        if chat_id not in ALLOWED_WELCOME_GROUPS:
            return

        if not user["first_join_group"]:
            new_points = user["points"] + 20
            await update_user(user_id, points=new_points, first_join_group=True)
            welcome_text = f"""
🎉 <b>欢迎 @{member.username or member.first_name} 加入本群！</b>

👮 我是管理员 <a href="tg://user?id={ADMIN_ID}">小卫</a>，点击我可私聊
🎁 获得 <b>20积分</b>（首次进群奖励，退群收回）

📌 积分用途：
  • 兑换商品（发送 /start 到私聊）
  • 解锁专属福利

🔗 会员群：<a href="{WELCOME_GROUP_LINK}">点此加入</a>
            """
        else:
            welcome_text = f"""
👋 <b>欢迎回来 @{member.username or member.first_name}！</b>

💡 发送 /start 到私聊，使用积分兑换商品
            """
        await context.bot.send_message(chat_id=chat_id, text=welcome_text.strip(), parse_mode="HTML")

async def handle_left_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    old_member = update.chat_member.old_chat_member
    if old_member.status in [ChatMember.LEFT, ChatMember.BANNED]:
        user_id = old_member.user.id
        user = await get_user(user_id)
        chat_id = update.chat_member.chat.id

        if chat_id not in ALLOWED_WELCOME_GROUPS:
            return

        if user["first_join_group"]:
            new_points = max(0, user["points"] - 20)
            await update_user(user_id, points=new_points, first_join_group=False)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"""
👋 @{old_member.user.username or old_member.user.first_name} 离开了本群

💸 已收回首次进群的 <b>20积分</b>
💡 欢迎随时回家！
                """.strip(),
                parse_mode="HTML"
            )

# ================= 3. 用户功能 =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🚀 开始验证", callback_data="show_vip")],
        [InlineKeyboardButton("🎯 我的积分", callback_data="my_points")],
        [InlineKeyboardButton("🔗 会员群（福利）", url=VIP_GROUP_LINK)],
    ]
    text = f"""
👋 你好！我是管理员小卫，你的积分助手~

🎁 本群福利：
• 进群即送20积分
• 兑换商品/会员
• 参与排行榜

📌 验证/兑换成功后，可加入：
<a href="{VIP_GROUP_LINK}">会员福利群</a>
""".strip()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    user = await get_user(user_id)
    text = f"🎯 我的积分\n\n当前积分：{user['points']} 分\n\n请选择功能："
    keyboard = [
        [InlineKeyboardButton("📅 每日签到", callback_data="sign_in")],
        [InlineKeyboardButton("💰 充值积分", callback_data="recharge")],
        [InlineKeyboardButton("🎁 兑换中心", callback_data="exchange")],
        [InlineKeyboardButton("💰 我的余额", callback_data="balance")],
        [InlineKeyboardButton("🏆 积分排行榜", callback_data="leaderboard")],
        [InlineKeyboardButton("🔙 返回", callback_data="back_to_home")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# ================= 4. 积分功能 =================
async def sign_in(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    now = datetime.now()
    user = await get_user(user_id)

    if user["last_sign_in"] and (now - user["last_sign_in"] < timedelta(days=1)):
        await query.answer("📅 您今天已签到！", show_alert=True)
        return

    new_points = user["points"] + random.randint(3, 8)
    await update_user(user_id, points=new_points, last_sign_in=now)
    await query.answer(f"✅ 签到成功！+{new_points - user['points']}积分", show_alert=True)

async def show_exchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    products = await db_pool.fetch("SELECT * FROM products WHERE is_active = TRUE")
    text = "🎁 兑换中心\n\n"
    keyboard = []
    for p in products:
        status = "✅ 已兑换" if await has_exchanged(user_id, p["id"]) else f"{p['price']}积分"
        keyboard.append([InlineKeyboardButton(f"{p['name']} | {status}", callback_data=f"exchange_{p['id']}")])
    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="my_points")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def has_exchanged(user_id: int, item_id: int) -> bool:
    async with db_pool.acquire() as conn:
        record = await conn.fetchrow("SELECT * FROM exchange_records WHERE user_id = $1 AND item_id = $2", user_id, item_id)
        return record is not None

async def handle_exchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    item_id = int(query.data.split("_")[1])
    item = await db_pool.fetchrow("SELECT * FROM products WHERE id = $1", item_id)
    user = await get_user(user_id)

    if not item or not item["is_active"]:
        await query.edit_message_text(
            "❌ 商品不存在或已下架。",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回兑换列表", callback_data="exchange")]])
        )
        return

    if await has_exchanged(user_id, item_id):
        await show_exchange_content(update, context, item)
        return

    if user["points"] < item["price"]:
        await query.edit_message_text(
            "❌ 余额不足！",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="exchange")]])
        )
        return

    keyboard = [
        [InlineKeyboardButton("✅ 确认", callback_data=f"confirm_exchange_{item_id}")],
        [InlineKeyboardButton("❌ 取消", callback_data="exchange")]
    ]
    await query.edit_message_text(
        f"📌 确认兑换：{item['name']}（{item['price']}积分）？",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def confirm_exchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    item_id = int(query.data.split("_")[2])
    item = await db_pool.fetchrow("SELECT * FROM products WHERE id = $1", item_id)
    user = await get_user(user_id)

    if user["points"] >= item["price"]:
        new_points = user["points"] - item["price"]
        await update_user(user_id, points=new_points)
        await db_pool.execute(
            "INSERT INTO exchange_records (user_id, item_id) VALUES ($1, $2)",
            user_id, item_id
        )
        await show_exchange_content(update, context, item)
    else:
        await query.edit_message_text("❌ 兑换失败，积分不足！")

async def show_exchange_content(update: Update, context: ContextTypes.DEFAULT_TYPE, item):
    query = update.callback_query
    if item["type"] == "text":
        text = f"🎁 兑换内容：{item['content']}"
    elif item["type"] == "image":
        await context.bot.send_photo(chat_id=query.message.chat_id, photo=item["content"])
        text = "✅ 兑换成功！"
    elif item["type"] == "video":
        await context.bot.send_video(chat_id=query.message.chat_id, video=item["content"])
        text = "✅ 兑换成功！"
    keyboard = [[InlineKeyboardButton("🔙 返回兑换中心", callback_data="exchange")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# ================= 5. 管理员系统 =================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ 无权访问！")
        return
    text = "👮 管理员系统\n\n小卫在此为您服务！"
    keyboard = [
        [InlineKeyboardButton("📦 商品管理", callback_data="manage_products")],
        [InlineKeyboardButton("📡 频道转发库", callback_data="manage_forwards")],
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def manage_forwards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    commands = await db_pool.fetch("SELECT * FROM forward_library WHERE is_active = TRUE")
    text = "📡 频道转发库（点击管理）\n\n"
    keyboard = []
    for cmd in commands:
        keyboard.append([InlineKeyboardButton(f"🗑️ {cmd['command']}", callback_data=f"del_forward_{cmd['id']}")])
    keyboard.append([InlineKeyboardButton("➕ 添加命令", callback_data="add_forward")])
    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="admin_home")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# ================= 6. 频道转发库 =================
async def handle_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    is_purchased = await db_pool.fetchval("SELECT 1 FROM user_purchases WHERE user_id = $1 AND command = $2", user_id, text)
    if not is_purchased:
        await db_pool.execute("INSERT INTO user_purchases (user_id, command) VALUES ($1, $2)", user_id, text)
    msg_urls = await db_pool.fetch("SELECT channel_msg_url FROM forward_library WHERE command = $1 AND is_active = TRUE", text)
    sent_msgs = []
    for url in msg_urls[:50]:
        try:
            parts = url.split("/")
            channel_id = f"@{parts[-2]}"
            msg_id = int(parts[-1])
            sent_msg = await context.bot.copy_message(
                chat_id=user_id,
                from_chat_id=channel_id,
                message_id=msg_id,
                protect_content=True,
            )
            sent_msgs.append(sent_msg.message_id)
        except Exception as e:
            print(f"转发失败: {e}")
    if sent_msgs:
        context.job_queue.run_once(
            callback=delete_forwarded_messages,
            when=1200,
            data={"user_id": user_id, "msg_ids": sent_msgs},
        )
    await update.message.delete()
    await update.effective_message.reply_text(
        "✅ 资源已发送！20分钟后自动删除\n（已购买，无需二次付费）",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎁 兑换中心", callback_data="exchange")]])
    )

# ================= 7. 辅助函数 =================
async def get_user(user_id: int) -> dict:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM user_points WHERE user_id = $1", user_id)
        if not row:
            await conn.execute("INSERT INTO user_points (user_id) VALUES ($1)", user_id)
            return {"points": 0, "first_join_group": False, "last_sign_in": None}
        return dict(row)

async def update_user(user_id: int, **kwargs):
    async with db_pool.acquire() as conn:
        fields = ", ".join([f"{k} = ${i+1}" for i, k in enumerate(kwargs.keys())])
        await conn.execute(f"UPDATE user_points SET {fields} WHERE user_id = ${len(kwargs)+1}", *kwargs.values(), user_id)

async def delete_forwarded_messages(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    for msg_id in job_data["msg_ids"]:
        try:
            await context.bot.delete_message(chat_id=job_data["user_id"], message_id=msg_id)
        except Exception as e:
            print(f"清理失败: {e}")
    await context.bot.send_message(
        chat_id=job_data["user_id"],
        text="⏰ 消息已过期，如需再次查看请重新发送命令",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 返回首页", callback_data="back_to_home")]])
    )

# ================= 8. 注册处理器 =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # 群组事件
    app.add_handler(ChatMemberHandler(welcome_new_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(handle_left_member, ChatMemberHandler.CHAT_MEMBER))

    # 用户命令
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_command))

    # 积分功能
    app.add_handler(CallbackQueryHandler(start, pattern="^back_to_home$"))
    app.add_handler(CallbackQueryHandler(show_points, pattern="^my_points$"))
    app.add_handler(CallbackQueryHandler(sign_in, pattern="^sign_in$"))
    app.add_handler(CallbackQueryHandler(show_exchange, pattern="^exchange$"))
    app.add_handler(CallbackQueryHandler(handle_exchange, pattern="^exchange_"))
    app.add_handler(CallbackQueryHandler(confirm_exchange, pattern="^confirm_exchange_"))

    # 管理员功能
    app.add_handler(CallbackQueryHandler(manage_forwards, pattern="^manage_forwards$"))

    print("Bot 已启动...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    import asyncio
    asyncio.run(init_db())
    main()
