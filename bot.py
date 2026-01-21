import os
import logging
import asyncio
import random
import string
from datetime import datetime, date, timedelta

from telegram import (
    Update,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# --------------------------------------------------------------
# 1️⃣ 环境变量（Railway 自动注入）
# --------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")                     # Telegram Bot Token
DATABASE_URL = os.getenv("DATABASE_URL")               # PostgreSQL async URL
ADMIN_IDS = os.getenv("ADMIN_IDS", "")                 # 逗号分隔的管理员 IDs
REPLY_WEBHOOK_URL = os.getenv("REPLY_WEBHOOK_URL", "")  # Railway 根域名

if not BOT_TOKEN or not DATABASE_URL:
    raise RuntimeError(
        "⚠️ 请在 Railway → Settings → Variables 中配置 BOT_TOKEN 与 DATABASE_URL"
    )
if not ADMIN_IDS:
    ADMIN_IDS = ""
if not REPLY_WEBHOOK_URL:
    REPLY_WEBHOOK_URL = ""

# --------------------------------------------------------------
# 2️⃣ SQLAlchemy（异步）模型声明
# --------------------------------------------------------------
from sqlalchemy import (
    MetaData,
    Table,
    Column,
    Integer,
    String,
    Date,          # <-- DATE 类型（仅日期）
    DateTime,
    Boolean,
    Text,
    text,
)
from sqlalchemy.ext.asyncio import create_async_engine

metadata = MetaData()

# ------------------- users 表（余额、积分、签到等） -------------------
users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("telegram_id", Integer, unique=True, index=True),
    Column("username", String),
    Column("balance", Integer, default=0),                 # 业务余额
    Column("points_balance", Integer, default=0),          # 积分余额
    Column("last_sign_in", DateTime, nullable=True),       # 最近签到时间
)

# ------------------- file_ids 表（管理员保存的 file_id） -------------------
file_ids = Table(
    "file_ids",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("admin_id", Integer, index=True),
    Column("file_id", String, nullable=False),
    Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
)

# ------------------- admin_links 表（存储「获取密钥」按钮使用的 Quark 链接） -------------------
admin_links = Table(
    "admin_links",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("url_one", String),      # 第一个链接
    Column("url_two", String),      # 第二个链接
    Column("updated_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
)

# ------------------- daily_tokens 表（每日密钥、积分、使用状态） -------------------
daily_tokens = Table(
    "daily_tokens",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("token_one", String),        # 今日第一个密钥（10 位随机字符）
    Column("token_two", String),        # 今日第二个密钥（10 位随机字符）
    Column("points_one", Integer),      # 对应积分（8）
    Column("points_two", Integer),      # 对应积分（6）
    Column("generated_date", Date),      # 对应的日期（仅 DATE，不含时间）
    Column("used_one", Boolean, default=False),
    Column("used_two", Boolean, default=False),
)

# ------------------- admin_usage 表（记录 /my 命令使用次数，限制 24h 内最多 3 次） -------------------
admin_usage = Table(
    "admin_usage",
    metadata,
    Column("admin_id", Integer, primary_key=True),
    Column("count", Integer, default=0),
)

# ------------------- 引擎 -------------------
# ⚠️ 必须使用 “+asyncpg” 的 URL，否则会报 “psycopg2 is not async”
engine = create_async_engine(
    DATABASE_URL,          # ← 这里必须是 `postgresql+asyncpg://…` 形式且 **不要带查询参数**
    echo=False,
    future=True,
    echo_pool=False,
)
# --------------------------------------------------------------
# 3️⃣ 数据库初始化（首次启动时创建表，之后永不删除）
# --------------------------------------------------------------
async def init_database() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

# --------------------------------------------------------------
# 4️⃣ 基础辅助函数
# --------------------------------------------------------------
def is_admin(user_id: int) -> bool:
    """判断是否为机器人创建者的管理员"""
    if not ADMIN_IDS:
        return False
    return str(user_id) in ADMIN_IDS.split(",")

# --------------------------------------------------------------
# 5️⃣ 每日密钥生成（北京时间 10:00 自动执行）
# --------------------------------------------------------------
def build_nonce_alphanumeric(length: int = 10) -> str:
    """返回指定长度的大小写字母+数字混合字符串"""
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))

async def ensure_daily_tokens_up_to_date() -> None:
    """
    检查 daily_tokens 表是否已有当天记录；
    若没有或日期已过期，随机生成两段 10 位密钥并写入。
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            daily_tokens.select().with_only_columns(daily_tokens.c.generated_date)
        )
        row = result.first()
        today = date.today()
        if not row or row.generated_date != today:
            token_one = build_nonce_alphanumeric(10)
            token_two = build_nonce_alphanumeric(10)
            await conn.execute(
                daily_tokens.update()
                .where(daily_tokens.c.id == 1)
                .values(
                    token_one=token_one,
                    token_two=token_two,
                    points_one=8,
                    points_two=6,
                    generated_date=today,
                    used_one=False,
                    used_two=False,
                )
            )
            if not row:
                await conn.execute(
                    daily_tokens.insert()
                    .values(
                        token_one=token_one,
                        token_two=token_two,
                        points_one=8,
                        points_two=6,
                        generated_date=today,
                        used_one=False,
                        used_two=False,
                    )
                )

# --------------------------------------------------------------
# 6️⃣ 获取当天密钥（若不存在自动生成）
# --------------------------------------------------------------
async def get_current_daily_tokens() -> tuple[str, str, int, int]:
    """返回 (token_one, token_two, points_one, points_two)"""
    await ensure_daily_tokens_up_to_date()
    async with engine.begin() as conn:
        result = await conn.execute(
            daily_tokens.select().where(daily_tokens.c.id == 1)
        )
        row = result.first()
        if not row:
            raise RuntimeError("⚠️ daily_tokens 表缺失记录，请检查 init_database()")
        return (row.token_one, row.token_two, row.points_one, row.points_two)

# --------------------------------------------------------------
# 7️⃣ 密钥兑换（隐藏指令）——直接发送完整密钥即可领积分
# --------------------------------------------------------------
async def handle_token_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """用户完整发送 token_one / token_two 即可领取对应积分"""
    received = update.message.text or ""
    token_one, token_two, points_one, points_two = await get_current_daily_tokens()
    async with engine.begin() as conn:
        result = await conn.execute(
            daily_tokens.select().where(daily_tokens.c.id == 1)
        )
        token_row = result.first()
        if not token_row:
            return

        if received == token_row.token_one and not token_row.used_one:
            # 领取 8 积分
            async with engine.begin() as conn2:
                res = await conn2.execute(
                    users.select().where(users.c.telegram_id == update.effective_user.id)
                )
                user_row = res.first()
                if not user_row:
                    await conn2.execute(
                        users.insert(),
                        {
                            "telegram_id": update.effective_user.id,
                            "username": "",
                            "balance": 0,
                            "points_balance": 0,
                        },
                    )
                    user_row = {"points_balance": 0}
                new_pts = (user_row.points_balance or 0) + token_row.points_one
                await conn2.execute(
                    users.update()
                    .where(users.c.telegram_id == update.effective_user.id)
                    .values(points_balance=new_pts),
                )
                await conn2.commit()
            await update.message.reply_text(
                f"🎉 恭喜领取密钥一，获得 <b>{token_row.points_one}</b> 积分！",
                parse_mode="HTML",
            )
            await conn.execute(
                daily_tokens.update()
                .where(daily_tokens.c.id == 1)
                .values(used_one=True)
            )
        elif received == token_row.token_two and not token_row.used_two:
            # 领取 6 积分
            async with engine.begin() as conn2:
                res = await conn2.execute(
                    users.select().where(users.c.telegram_id == update.effective_user.id)
                )
                user_row = res.first()
                if not user_row:
                    await conn2.execute(
                        users.insert(),
                        {
                            "telegram_id": update.effective_user.id,
                            "username": "",
                            "balance": 0,
                            "points_balance": 0,
                        },
                    )
                    user_row = {"points_balance": 0}
                new_pts = (user_row.points_balance or 0) + token_row.points_two
                await conn2.execute(
                    users.update()
                    .where(users.c.telegram_id == update.effective_user.id)
                    .values(points_balance=new_pts),
                )
                await conn2.commit()
            await update.message.reply_text(
                f"🎉 恭喜领取密钥二，获得 <b>{token_row.points_two}</b> 积分！",
                parse_mode="HTML",
            )
            await conn.execute(
                daily_tokens.update()
                .where(daily_tokens.c.id == 1)
                .values(used_two=True)
            )
        else:
            await update.message.reply_text(
                "❌ 该密钥已失效或已使用，请等待明日 10:00 自动更换。"
            )

# --------------------------------------------------------------
# 8️⃣ 基础用户指令
# --------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start 永远显示欢迎页（包含四个按钮）"""
    await send_home_page(update, context)

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """查看余额"""
    user_id = update.effective_user.id
    async with engine.begin() as conn:
        result = await conn.execute(
            users.select().where(users.c.telegram_id == user_id)
        )
        row = result.first()
        if not row:
            await update.message.reply_text(
                "❓ 你还不是注册用户，先发送 /start"
            )
            return
        await update.message.reply_text(
            f"💰 你的余额是 <b>{row.balance}</b> 个单位。",
            parse_mode="HTML",
        )

async def deposit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """存入金额"""
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("用法： /deposit <正整数>")
        return
    amount = int(context.args[0])
    async with engine.begin() as conn:
        await conn.execute(
            users.update()
            .where(users.c.telegram_id == update.effective_user.id)
            .values(balance=text("balance + :amt")),  # type: ignore[arg-type]
            {"amt": amount},
        )
    await update.message.reply_text(f"✅ 已存入 <b>{amount}</b> 个单位。", parse_mode="HTML")

async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """提取金额（需余额足够）"""
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("用法： /withdraw <正整数>")
        return
    amount = int(context.args[0])
    async with engine.begin() as conn:
        res = await conn.execute(
            users.select()
            .where(users.c.telegram_id == update.effective_user.id)
            .with_for_update()
        )
        row = res.first()
        if not row or row.balance < amount:
            await update.message.reply_text("🚫 余额不足或用户不存在")
            return
        await conn.execute(
            users.update()
            .where(users.c.telegram_id == update.effective_user.id)
            .values(balance=text("balance - :amt")),  # type: ignore[arg-type]
            {"amt": amount},
        )
    await update.message.reply_text(f"✅ 已提取 <b>{amount}</b> 个单位。", parse_mode="HTML")

async def jf_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """直接打开积分页面（每日签到）"""
    await points_page(update, context)

# --------------------------------------------------------------
# 9️⃣ 积分页面 & 每日签到
# --------------------------------------------------------------
async def points_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """显示当前积分并提供签到按钮（回调 data = "sign_in"）"""
    user_id = update.effective_user.id
    async with engine.begin() as conn:
        result = await conn.execute(
            users.select().where(users.c.telegram_id == user_id)
        )
        row = result.first()
        if not row:
            await update.message.reply_text(
                "❓ 你还没有积分记录，先发送 /start 进入系统。"
            )
            return
        points = row.points_balance or 0
        await update.message.reply_text(
            f"📊 你的当前积分是 <b>{points}</b> 点。",
            parse_mode="HTML",
        )
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("签到", callback_data="sign_in")]]
        )
        await update.message.reply_text(
            "点击下方按钮进行每日签到。", reply_markup=keyboard
        )

async def attempt_sign_in(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理签到按钮：每天只能签到一次，首次 10 积分，之后随机 3‑8 积分"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    async with engine.begin() as conn:
        res = await conn.execute(
            users.select().where(users.c.telegram_id == user_id)
        )
        user_row = res.first()
        if not user_row:
            await query.edit_message_text(
                "❓ 你还没有积分记录，先发送 /start 进入系统。"
            )
            return

        today_str = datetime.utcnow().date().isoformat()
        last_sign_in = user_row.last_sign_in
        cur_points = user_row.points_balance or 0

        if last_sign_in and last_sign_in.date().isoformat() == today_str:
            await query.edit_message_text("✅ 你今天已经签到过了，请明天再来。")
            return

        reward = 10 if cur_points == 0 else random.randint(3, 8)
        new_points = cur_points + reward
        await conn.execute(
            users.update()
            .where(users.c.telegram_id == user_id)
            .values(
                points_balance=new_points,
                last_sign_in=text("CURRENT_TIMESTAMP"),
            ),
        )
        await conn.commit()

    await query.edit_message_text(
        f"🎉 恭喜签到！本次获得 <b>{reward}</b> 积分，当前积分 <b>{new_points}</b> 点。",
        parse_mode="HTML",
    )
    await points_page(query, context)

# --------------------------------------------------------------
# 10️⃣ “开始验证”付费验证（只接受 20260 开头、最多两次输入、二次失败锁定 5h）
# --------------------------------------------------------------
async def paid_verify_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """点击 “✅ 我已付款，开始验证” 进入付费验证流程"""
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "🧾 请发送您的订单号（系统会自动识别以 20260 开头的订单号）\n"
        "您最多有 2 次机会，失败后将锁定 5 小时。",
        reply_markup=ForceReply(selective=True),
    )
    context.user_data["order_state"] = "awaiting_order"
    context.user_data["order_attempts"] = 0
    context.user_data["verify_locked_until"] = None  # 清除旧的锁定时间

async def handle_order_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理付费验证阶段的订单号输入"""
    if context.user_data.get("order_state") != "awaiting_order":
        return

    text = update.message.text or ""
    attempts = context.user_data.get("order_attempts", 0) + 1
    context.user_data["order_attempts"] = attempts

    if text.startswith("20260"):
        join_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔗 加入群组", url="https://t.me/joinchat/xxxxxx")]]
        )
        await update.message.reply_text(
            "✅ 订单号验证成功！已为您打开加入群组的链接。",
            reply_markup=join_kb,
        )
        context.user_data.pop("order_state", None)
        context.user_data.pop("order_attempts", None)
        await send_home_page(update, context)          # 回到首页
        return

    if attempts >= 2:
        lock_until = datetime.utcnow() + timedelta(hours=5)
        context.user_data["verify_locked_until"] = lock_until
        await send_home_page(update, context)
        await update.message.reply_text(
            f"❌ 两次失败，验证功能已锁定至 {lock_until.strftime('%Y-%m-%d %H:%M')}（UTC）"
        )
        context.user_data.pop("order_state", None)
        context.user_data.pop("order_attempts", None)
    else:
        await update.message.reply_text(
            "❌ 未识别的订单号，请重新输入（仅支持以 20260 开头的订单号）。"
        )
        # 仍保持 awaiting_order 状态，可继续输入

# --------------------------------------------------------------
# 11️⃣ 管理员后台（/admin、文件‑ID 收集、删除）
# --------------------------------------------------------------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """显示管理员面板（仅限管理员）"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ 你没有管理员权限")
        return

    keyboard = [
        [
            InlineKeyboardButton("🗂 查看文件 ID", callback_data="admin_file_view"),
            InlineKeyboardButton("➕ 添加文件 ID", callback_data="admin_file_add"),
        ],
        [InlineKeyboardButton("❌ 删除全部文件 ID", callback_data="admin_file_delete_all")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🔐 **管理员后台**\n请选择您想要的操作（以下功能仅限管理员使用）",
        reply_markup=reply_markup,
        parse_mode="HTML",
    )

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """管理员后台回调（保持原有功能不变）"""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.edit_message_text("❌ 你已失去管理员权限")
        return

    data = query.data
    if data == "admin_file_add":
        await query.edit_message_text(
            "📁 请发送您想要保存的 **图片/文件**（只支持一次一个），"
            "随后机器人会为您保存其 `file_id`。",
            reply_markup=ForceReply(selective=True),
            parse_mode="HTML",
        )
        context.user_data["awaiting_file"] = True
        return

    if data == "admin_file_view":
        await show_saved_files(query, context)
        return

    if data == "admin_file_delete_all":
        confirm_kb = [
            [
                InlineKeyboardButton("✅ 确认删除", callback_data="admin_file_delete_confirm_yes"),
                InlineKeyboardButton("❌ 取消", callback_data="admin_file_delete_confirm_no"),
            ]
        ]
        await query.edit_message_text(
            "⚠️ 你确定要删除 **全部** 保存的文件 ID 吗？此操作不可撤销。",
            reply_markup=InlineKeyboardMarkup(confirm_kb),
            parse_mode="HTML",
        )
        return

    if data.startswith("admin_file_delete_confirm_"):
        confirm = data.split("_")[-1]
        if confirm == "yes":
            async with engine.begin() as conn:
                await conn.execute(file_ids.delete())
            await query.edit_message_text("🗑 已删除所有文件 ID 记录。")
        else:
            await query.edit_message_text("✅ 已放弃删除操作。")
        return

    if data.startswith("admin_file_delete_"):
        # 删除单条交给 admin_delete_single 处理
        return

    await query.edit_message_text("⚙️ 未识别的操作，请返回管理员面板。")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """管理员面板阶段的文件上传处理（保存 file_id）"""
    if "awaiting_file" not in context.user_data:
        return
    msg = update.message
    if not msg:
        return
    async with engine.begin() as conn:
        await conn.execute(
            file_ids.insert(),
            {"admin_id": context.user_data.get("admin_id", -1), "file_id": msg.message_id},
        )
    context.user_data.pop("awaiting_file", None)
    await admin_panel(update, context)

    if msg.photo:
        file_id = msg.photo[-1].file_id
        await context.bot.send_message(
            chat_id=context.user_data.get("admin_id", -1),
            text=f"✅ 已保存文件 ID：`{file_id}`，现在发送回原图以便你确认。",
        )
    elif msg.document:
        file_id = msg.document.file_id
        await context.bot.send_message(
            chat_id=context.user_id,
            text=f"✅ 已保存文件 ID：`{file_id}`。",
        )
    else:
        await context.bot.send_message(
            chat_id=context.user_id,
            text="✅ 已保存文件 ID，但当前不支持直接显示内容。",
        )

async def show_saved_files(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """向管理员展示已保存的 file_id 并提供单条删除按钮"""
    async with engine.begin() as conn:
        rows = await conn.execute(
            file_ids.select().order_by(file_ids.c.created_at.desc())
        )
        all_rows = rows.fetchall()

    if not all_rows:
        await query.edit_message_text("📂 当前没有任何保存的文件 ID。", parse_mode="HTML")
        return

    rows_markup = []
    for idx, row in enumerate(all_rows):
        rows_markup.append(
            [
                InlineKeyboardButton(
                    f"❌ 删除 #{idx+1}",
                    callback_data=f"admin_file_delete_{row.id}",
                )
            ]
        )
    rows_markup.append([InlineKeyboardButton("🔙 返回管理面板", callback_data="admin_panel")])
    reply_markup = InlineKeyboardMarkup(rows_markup)

    file_list = "\n".join(
        f"🗂 <b>#{i+1}</b> – 保存时间 {row.created_at.strftime('%Y-%m-%d %H:%M:%S')}\nFile ID: `{row.file_id}`"
        for i, row in enumerate(all_rows)
    )
    await query.edit_message_text(
        f"📂 **已保存的文件 ID 列表**\n\n{file_list}",
        reply_markup=reply_markup,
        parse_mode="HTML",
    )

async def admin_delete_single(callback: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """删除单条 file_id（带确认）"""
    query = callback.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.edit_message_text("❌ 你已失去管理员权限")
        return

    try:
        record_id = int(query.data.split("_")[-1])
    except ValueError:
        await query.edit_message_text("⚠️ 参数错误")
        return

    async with engine.begin() as conn:
        result = await conn.execute(
            file_ids.select().where(file_ids.c.id == record_id)
        )
        row = result.first()
        if not row:
            await query.edit_message_text("⚠️ 该记录不存在")
            return
        await conn.execute(file_ids.delete().where(file_ids.c.id == record_id))
        await conn.commit()

    await query.edit_message_text(
        f"✅ 已删除记录 <b>{record_id}</b>（File ID: `{row.file_id}`)",
        parse_mode="HTML",
    )
    await show_saved_files(query, context)

# --------------------------------------------------------------
# 12️⃣ /my 命令 – 查看/更新今日密钥（无限次查看/更新）
# --------------------------------------------------------------
async def my_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    用法：
        /my                         → 仅查看今日密钥（十位随机字符）
        /my <新链接1> <新链接2>    → 更新「获取密钥」按钮使用的 Quark 链接（可随时调用）
    """
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ 只有管理员可以使用此命令")
        return

    args = context.args
    if len(args) == 0:
        # 仅显示今日密钥
        token_one, token_two, _, _ = await get_current_daily_tokens()
        await update.message.reply_text(
            f"🔑 今日密钥（10 位随机字符）\n"
            f"密钥 1（8 积分）: `{token_one}`\n"
            f"密钥 2（6 积分）: `{token_two}`\n"
            "请把对应的文字完整发送给机器人即可领取积分。"
        )
        return

    # 提供了两个参数 → 更新 Quark 链接
    if len(args) == 2:
        url_one, url_two = args[0], args[1]
        async with engine.begin() as conn:
            await conn.execute(
                admin_links.update()
                .where(admin_links.c.id == 1)
                .values(url_one=url_one, url_two=url_two, updated_at=text("CURRENT_TIMESTAMP"))
            )
            await conn.commit()
        await update.message.reply_text(
            f"✅ 已更新链接。\n第一个链接: {url_one}\n第二个链接: {url_two}"
        )
        # 私信管理员确认（可选）
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="✅ 链接已更新，将在「获取密钥」按钮中使用新链接。",
            )
        except Exception:
            pass
        return

    await update.message.reply_text(
        "用法：\n"
        "/my                → 查看今日密钥\n"
        "/my <链接1> <链接2> → 更新「获取密钥」按钮跳转的 Quark 链接"
    )

# --------------------------------------------------------------
# 13️⃣ “获取密钥” 按钮及 WebApp（3 秒后跳转到 Quark 链接）
# --------------------------------------------------------------
async def send_home_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    生成并发送欢迎页（/start 页面）。
    包含四个按钮：
        1️⃣ 开始验证
        2️⃣ 积分
        3️⃣ 开业活动（WebApp → /hd）
        4️⃣ 获取密钥（WebApp，点击后打开 /mid?target=1 或 2）
    """
    user = update.effective_user
    async with engine.begin() as conn:
        result = await conn.execute(
            users.select().where(users.c.telegram_id == user.id)
        )
        row = result.first()
        if not row:
            await conn.execute(
                users.insert(),
                {"telegram_id": user.id, "username": user.username},
            )

    now = datetime.utcnow()
    locked_until = context.user_data.get("verify_locked_until")
    # 若开始验证被锁定，把按钮改为不可点击状态
    if locked_until and locked_until > now:
        disabled_text = f"验证已锁定，请等待 {locked_until.strftime('%H:%M')} 后再试"
        start_button = InlineKeyboardButton(disabled_text, callback_data="noop")
    else:
        start_button = InlineKeyboardButton("开始验证", callback_data="verify")

    paid_button = InlineKeyboardButton("✅ 我已付款，开始验证", callback_data="paid_verify")
    points_button = InlineKeyboardButton("积分", callback_data="points")

    # ---------- “获取密钥” 按钮 ----------
    click_count = context.user_data.get("key_clicks", 0)
    async with engine.begin() as conn:
        link_row = await conn.execute(admin_links.select())
        link_record = link_row.first()
        url_one = link_record.url_one if link_record else ""
        url_two = link_record.url_two if link_record else ""

    if not url_one or not url_two:
        key_button = InlineKeyboardButton("⏳ 请等待管理员更换链接", callback_data="noop")
    else:
        if click_count == 0:
            key_button = InlineKeyboardButton(
                "获取密钥",
                web_app=WebAppInfo(url=f"{REPLY_WEBHOOK_URL}/mid?target=1"),
            )
        elif click_count == 1:
            key_button = InlineKeyboardButton(
                "获取密钥",
                web_app=WebAppInfo(url=f"{REPLY_WEBHOOK_URL}/mid?target=2"),
            )
        else:
            key_button = InlineKeyboardButton("已达上限，请明天再试", callback_data="noop")

    keyboard = InlineKeyboardMarkup(
        [
            [start_button],
            [paid_button],
            [points_button],
            [key_button],
        ]
    )

    welcome_text = (
        "👋 <b>欢迎加入【VIP中转】！</b> 我是守门员小卫，你的身份验证小助手~\n"
        "📢 <b>小卫小卫，守门员小卫！</b>\n"
        "一键入群，小卫帮你搞定！\n"
        "新人来报到，小卫查身份！\n"
        "💎 <b>VIP 会员特权说明：</b>\n"
        "✅ 专属中转通道\n"
        "✅ 优先审核入群\n"
        "✅ 7×24 小时客服支持\n"
        "✅ 定期福利活动"
    )
    await update.message.reply_text(
        welcome_text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )

# --------------------------------------------------------------
# 14️⃣ 开业活动页面（/hd）以及中转页面（/mid）
# --------------------------------------------------------------
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

fastapi_app = FastAPI()

@fastapi_app.get("/hd", response_class=HTMLResponse)
async def hd_page(request: Request):
    """开业活动页面（包含观看视频按钮和获取密钥按钮）"""
    html = """
    <html>
    <head>
        <meta charset="UTF-8">
        <title>开业活动 - 观看视频得积分</title>
        <script src='//libtl.com/sdk.js' data-zone='10489957' data-sdk='show_10489957'></script>
        <style>
            body{font-family:Arial,sans-serif;text-align:center;margin-top:40px;}
            button{font-size:18px;padding:10px 20px;margin-top:15px;background:#4CAF50;color:#fff;border:none;border-radius:4px;cursor:pointer;}
            .counter{margin-top:10px;font-weight:bold;}
            .note{margin-top:15px;color:#555;}
        </style>
    </head>
    <body>
        <h2>🎉 开业特惠·观看视频得积分</h2>
        <p>点击下方按钮观看 rewarded 广告，观看至结束后即可获得积分奖励。</p>
        <button id="watchBtn">开始观看</button>
        <div class="counter" id="counter">观看次数：0/3</div>
        <p><a href="/mid?target=1" style="display:inline-block;margin-top:10px;">获取密钥</a></p>
        <div class="note">
            每天可通过夸克网盘获取密钥。页面会 3 秒后自动跳转至对应的密钥链接，请耐心等待。
        </div>
        <script>
            const counterEl=document.getElementById('counter');
            const btn=document.getElementById('watchBtn');
            let completed=0;
            function updateCounter(){counterEl.textContent='观看次数：'+completed+'/3';}
            updateCounter();

            btn.onclick=()=>{ 
                if(completed>=3){
                    alert('每天最多可观看 3 次，已达上限！');
                    return;
                }
                show_10489957('pop').then(()=>{ 
                    fetch(`/reward?user_id=${window.Telegram?.WebApp?.initDataUnsafe?.user?.id}`)
                        .then(r=>r.json())
                        .then(d=>{
                            if(d.success){
                                completed++;
                                updateCounter();
                                alert('✅ 观看完成，已获得积分！');
                            }else{
                                alert('❌ 观看过程中出现错误，请重新尝试。');
                            }
                        })
                        .catch(()=>{alert('❌ 请求出错，请稍后重试。');});
                }).catch(()=>{alert('广告加载失败，请检查网络或稍后重试。');});
            };
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

@fastapi_app.get("/mid")
async def mid_page(request: Request):
    """
    中转页面。`target` 必须是 1 或 2，分别对应
    admin_links.url_one / admin_links.url_two。
    页面会在 3 秒后自动跳转到对应的 Quark 链接。
    """
    query_params = await request.query_params
    target = query_params.get("target")
    if target not in ("1", "2"):
        return HTMLResponse(content="<html><body>参数错误</body></html>", status_code=400)

    async with engine.begin() as conn:
        result = await conn.execute(admin_links.select())
        row = result.first()
        if not row:
            return HTMLResponse(
                content="<html><body>暂未设置链接，请管理员使用 /my 命令。</body></html>"
            )
        target_url = row.url_one if target == "1" else row.url_two
        if not target_url:
            return HTMLResponse(content="<html><body>对应链接未设置。</body></html>")

    html = f"""
    <html>
    <head>
        <meta charset="UTF-8">
        <title>密钥获取 – 中转中...</title>
        <style>
            body{{font-family:Arial,sans-serif;text-align:center;margin-top:40px;}}
            .note{{color:#555;margin-top:10px;}}
        </style>
        <script>
            const targetUrl = "{target_url}";
            setTimeout(()=>{{location.href=targetUrl;}}, 3000);
        </script>
    </head>
    <body>
        <h2>🔑 获取密钥中...</h2>
        <p class="note">页面将在 3 秒后自动跳转至对应的夸克网盘链接。</p>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

# ------------------- /reward（奖励积分） -------------------
daily_claims: dict[str, set[int]] = {}
today_str = date.today().isoformat()

@fastapi_app.get("/reward")
async def reward(user_id: int):
    """
    用户完成观看 rewarded 广告后调用此接口。
    每日最多 3 次，奖励规则为 10 → 6 → 3‑10 随机。
    """
    global daily_claims, today_str
    if today_str != date.today().isoformat():
        daily_claims = set()
        today_str = date.today().isoformat()

    if user_id in daily_claims:
        return JSONResponse(
            {"success": False, "reward": 0, "message": "每日上限已达，请明天再试。"}
        )
    daily_claims.add(user_id)

    # 计算奖励
    if len(daily_claims) == 1:
        reward = 10
    elif len(daily_claims) == 2:
        reward = 6
    else:
        reward = random.randint(3, 10)

    # 写入积分
    async with engine.begin() as conn:
        res = await conn.execute(
            users.select().where(users.c.telegram_id == user_id)
        )
        user_row = res.first()
        if not user_row:
            await conn.execute(
                users.insert(),
                {
                    "telegram_id": user_id,
                    "username": "",
                    "balance": 0,
                    "points_balance": 0,
                },
            )
            user_row = {"points_balance": 0}
        new_points = (user_row.points_balance or 0) + reward
        await conn.execute(
            users.update()
            .where(users.c.telegram_id == user_id)
            .values(points_balance=new_points),
        )
        await conn.commit()

    return JSONResponse(
        {"success": True, "reward": reward, "message": f"积分已加 {reward}"}
    )

# --------------------------------------------------------------
# 15️⃣ 调度器 – 每天北京时间 10:00 自动更新密钥
# --------------------------------------------------------------
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()               # ← 使用 BackgroundScheduler（不依赖事件循环）
scheduler.add_job(
    func=lambda: asyncio.run(ensure_daily_tokens_up_to_date()),
    trigger="cron",
    hour=10,
    minute=0,
    timezone="Asia/Shanghai",
)
scheduler.start()        # ← 直接调用 start() 即可，无需担心 “no running event loop”

# --------------------------------------------------------------
# 16️⃣ 通用回调处理（非管理员）
# --------------------------------------------------------------
async def general_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /start 页面的普通回调（verify、points、sign_in）"""
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "verify":
        await query.edit_message_text("验证已提交，感谢使用！")
    elif data == "points":
        await points_page(query, context)
    elif data == "sign_in":
        await attempt_sign_in(query, context)
    else:
        await query.edit_message_text("未识别的操作，请返回主菜单。")

# --------------------------------------------------------------
# 17️⃣ 主入口 – 同时运行 Bot 与 FastAPI
# --------------------------------------------------------------
def main() -> None:
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )

    application = Application.builder().token(BOT_TOKEN).build()

    # 基础指令
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("deposit", deposit))
    application.add_handler(CommandHandler("withdraw", withdraw))
    application.add_handler(CommandHandler("jf", jf_command))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("my", my_command))

    # 回调与消息
    application.add_handler(CallbackQueryHandler(admin_callback))
    application.add_handler(CallbackQueryHandler(general_callback, pattern=r"^(verify|points|sign_in)$"))
    application.add_handler(CallbackQueryHandler(paid_verify_handler, pattern="paid_verify"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_order_input))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_token_message))
    application.add_handler(MessageHandler(filters.ALL, handle_message))   # 文件‑ID 收集等
    application.add_handler(CallbackQueryHandler(lambda u, c: None))      # 防止未捕获的回调报错

    # 初始化数据库（仅第一次创建表）
    asyncio.run(init_database())

    # --------------------------  启动 FastAPI  --------------------------
    async def start_fastapi():
        import uvicorn

        port = int(os.getenv("PORT", 10000))
        config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=port)
        server = uvicorn.Server(config)
        await server.serve()

    async def runner():
        # 1️⃣ 启动调度器（这里可以安全地调用 start()，因为我们已经在 async 环境中）
        scheduler.start()                     # ← 关键点：放在这里
        # 2️⃣ 启动 Bot（webhook）和 FastAPI 两个并发任务
        bot_task = asyncio.create_task(
            application.run_webhook(
                listen="0.0.0.0",
                port=port,
                url_path=BOT_TOKEN,
                webhook_url=f"{REPLY_WEBHOOK_URL}/{BOT_TOKEN}",
            )
        )
        fastapi_task = asyncio.create_task(start_fastapi())
        await asyncio.gather(bot_task, fastapi_task)

    # 最终入口
    asyncio.run(runner())


if __name__ == "__main__":
    main()
