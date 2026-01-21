import os
import logging
import random
import string
import re
from datetime import datetime, date, timedelta, timezone
import threading
import asyncio

from fastapi import FastAPI, Request
import uvicorn

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler,
    MessageHandler, filters, ConversationHandler
)
import asyncpg
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== 配置区（请替换） ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", "8000"))  # Railway自动设置

VIP_GROUP_LINK = "https://t.me/your_vip_group_link"

START_VERIFY_FILE_IDS = [
    "file_id_1_for_homepage",
    "file_id_2_for_homepage"
]

VIP_EXPLAIN_FILE_ID = "file_id_for_vip_explain"
ORDER_INPUT_FILE_ID = "file_id_for_order_input"

MOONTAG_AD_URL_BASE = "https://你的github用户名.github.io/你的仓库名/moontag.html"

MOONTAG_LINK_1 = "https://otieu.com/4/10489994"
MOONTAG_LINK_2 = "https://otieu.com/4/10489998"

SECRET_LINK_1 = "https://pan.quark.cn/s/c0cac0ff25a5"
SECRET_LINK_2 = "https://pan.quark.cn/s/b1dd3806ff25a5"

BUTTON_TWO_NAME = "🔑 密钥领取"
MAX_SECRET_REDEEM = 2

BJ_TZ = timezone(timedelta(hours=8))

WAITING_IMAGE = 1
CONFIRM_DELETE = 2
VERIFY_START, VERIFY_WAIT_ORDER = range(2)

db_pool = None
app = FastAPI()
application = None  # Telegram Application实例

# ========== 数据库初始化 ==========
async def init_db_pool():
    global db_pool
    db_pool = await asyncpg.create_pool(dsn=DATABASE_URL)
    async with db_pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS file_ids (
            id SERIAL PRIMARY KEY,
            file_id TEXT NOT NULL,
            added_by BIGINT NOT NULL,
            added_at TIMESTAMP DEFAULT NOW()
        )
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS user_points (
            user_id BIGINT PRIMARY KEY,
            points INTEGER NOT NULL DEFAULT 0,
            last_sign_date DATE
        )
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS moontag_ad (
            user_id BIGINT PRIMARY KEY,
            ad_date DATE,
            watch_count INTEGER NOT NULL DEFAULT 0
        )
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS verification_status (
            user_id BIGINT PRIMARY KEY,
            fail_count INTEGER NOT NULL DEFAULT 0,
            disabled_until TIMESTAMP
        )
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_secrets (
            id SERIAL PRIMARY KEY,
            secret1 TEXT,
            secret2 TEXT,
            secret1_link TEXT,
            secret2_link TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS user_secret_redeem (
            user_id BIGINT PRIMARY KEY,
            redeem1 BOOLEAN DEFAULT FALSE,
            redeem2 BOOLEAN DEFAULT FALSE,
            last_redeem_date DATE
        )
        """)

def is_admin(user_id):
    return user_id == ADMIN_ID

# 首页 /start 命令
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    disabled, disabled_until = await is_verification_disabled(user_id)
    if disabled:
        unlock_time = disabled_until.strftime("%Y-%m-%d %H:%M UTC")
        verify_btn = InlineKeyboardButton(f"🚫 验证锁定中，解锁时间：{unlock_time}", callback_data="disabled_verify")
    else:
        verify_btn = InlineKeyboardButton("🚀 开始验证", callback_data="start_verification")

    keyboard = [
        [verify_btn],
        [InlineKeyboardButton("💎 积分", callback_data="show_points")],
        [InlineKeyboardButton("🎉 开业活动", callback_data="moontag_hd")]
    ]

    welcome_text = (
        "👋 欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~\n\n"
        "📢 小卫小卫，守门员小卫！\n"
        "一键入群，小卫帮你搞定！\n"
        "新人来报到，小卫查身份！"
    )

    await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard))

    media_group = [
        InputMediaPhoto(media=START_VERIFY_FILE_IDS[0]),
        InputMediaPhoto(media=START_VERIFY_FILE_IDS[1])
    ]
    await update.message.reply_media_group(media_group)

# 拦截所有消息，非验证流程时显示首页
async def echo_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = context.user_data.get("verify_state")
    if state in [VERIFY_START, VERIFY_WAIT_ORDER]:
        return
    await start(update, context)

# 判断验证是否禁用
async def is_verification_disabled(user_id):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT fail_count, disabled_until FROM verification_status WHERE user_id=$1", user_id)
        if not row:
            return False, None
        disabled_until = row['disabled_until']
        if disabled_until and disabled_until > datetime.utcnow():
            return True, disabled_until
        return False, None

# 重置验证状态
async def reset_verification_status(user_id):
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM verification_status WHERE user_id=$1", user_id)

# 增加失败次数，禁用5小时
async def add_verification_fail(user_id):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT fail_count FROM verification_status WHERE user_id=$1", user_id)
        if not row:
            fail_count = 1
            disabled_until = None
            await conn.execute("INSERT INTO verification_status (user_id, fail_count) VALUES ($1, $2)", user_id, fail_count)
        else:
            fail_count = row['fail_count'] + 1
            disabled_until = None
            if fail_count >= 2:
                disabled_until = datetime.utcnow() + timedelta(hours=5)
                await conn.execute("UPDATE verification_status SET fail_count=$1, disabled_until=$2 WHERE user_id=$3", fail_count, disabled_until, user_id)
            else:
                await conn.execute("UPDATE verification_status SET fail_count=$1 WHERE user_id=$2", fail_count, user_id)
        return fail_count, disabled_until

# 点击开始验证按钮，显示VIP说明页
async def start_verification_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    disabled, disabled_until = await is_verification_disabled(user_id)
    if disabled:
        unlock_time = disabled_until.strftime("%Y-%m-%d %H:%M UTC")
        await query.edit_message_text(f"🚫 验证功能锁定中，解锁时间：{unlock_time}\n请稍后再试。")
        return

    text = (
        "💎 VIP会员特权说明：\n"
        "✅ 专属中转通道\n"
        "✅ 优先审核入群\n"
        "✅ 7x24小时客服支持\n"
        "✅ 定期福利活动\n"
    )
    keyboard = [
        [InlineKeyboardButton("✅ 我已付款，开始验证", callback_data="paid_start_verify")],
        [InlineKeyboardButton("⬅️ 返回首页", callback_data="back_start")]
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    await query.message.reply_photo(VIP_EXPLAIN_FILE_ID)
    context.user_data["verify_state"] = VERIFY_START

# 点击“我已付款，开始验证”，进入订单号输入页
async def paid_start_verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    disabled, disabled_until = await is_verification_disabled(user_id)
    if disabled:
        unlock_time = disabled_until.strftime("%Y-%m-%d %H:%M UTC")
        await query.edit_message_text(f"🚫 验证功能锁定中，解锁时间：{unlock_time}\n请稍后再试。")
        return

    context.user_data["verify_state"] = VERIFY_WAIT_ORDER
    context.user_data["verify_fail_count"] = 0

    text = (
        "请输入订单号。\n\n"
        "查找订单号的详细教程：\n"
        "我的 账单 账单详情 更多 订单号 详细步骤"
    )

    await query.edit_message_text(text)
    await query.message.reply_photo(ORDER_INPUT_FILE_ID)

# 订单号输入处理
async def verify_order_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = context.user_data.get("verify_state")
    if state != VERIFY_WAIT_ORDER:
        await start(update, context)
        return

    text = update.message.text.strip()
    if not re.match(r"^20260\d*$", text):
        fail_count = context.user_data.get("verify_fail_count", 0) + 1
        context.user_data["verify_fail_count"] = fail_count

        if fail_count >= 2:
            await add_verification_fail(user_id)
            await update.message.reply_text(
                "未查询到订单信息，请重试。\n\n"
                "验证失败次数过多，功能已锁定5小时。\n"
                "请稍后再试。"
            )
            context.user_data.pop("verify_state", None)
            context.user_data.pop("verify_fail_count", None)
            await start(update, context)
            return
        else:
            await update.message.reply_text("未查询到订单信息，请重试。")
            return

    await reset_verification_status(user_id)
    context.user_data.pop("verify_state", None)
    context.user_data.pop("verify_fail_count", None)

    keyboard = [
        [InlineKeyboardButton("🔗 加入VIP群", url=VIP_GROUP_LINK)],
        [InlineKeyboardButton("⬅️ 返回首页", callback_data="back_start")]
    ]
    await update.message.reply_text("验证成功！欢迎加入VIP群。", reply_markup=InlineKeyboardMarkup(keyboard))
    await start(update, context)

# 积分签到功能
async def get_user_points(user_id: int):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT points, last_sign_date FROM user_points WHERE user_id=$1", user_id)
        if row:
            return row['points'], row['last_sign_date']
        else:
            await conn.execute("INSERT INTO user_points (user_id, points) VALUES ($1, 0)", user_id)
            return 0, None

async def jf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    points, last_sign_date = await get_user_points(user_id)

    keyboard = [
        [InlineKeyboardButton("📝 签到", callback_data="sign_in")],
        [InlineKeyboardButton("⬅️ 返回首页", callback_data="back_start")]
    ]
    text = f"你的积分：{points}\n最后签到日期：{last_sign_date if last_sign_date else '未签到过'}"
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def sign_in_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    today = date.today()
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT points, last_sign_date FROM user_points WHERE user_id=$1", user_id)
        if not row:
            points = 0
            last_sign_date = None
            await conn.execute("INSERT INTO user_points (user_id, points) VALUES ($1, 0)", user_id)
        else:
            points = row['points']
            last_sign_date = row['last_sign_date']

        if last_sign_date == today:
            text = f"你今天已经签到过了，当前积分：{points}"
        else:
            if last_sign_date is None:
                add_points = 10
            else:
                add_points = random.randint(3, 8)
            points += add_points
            await conn.execute(
                "UPDATE user_points SET points=$1, last_sign_date=$2 WHERE user_id=$3",
                points, today, user_id
            )
            text = f"签到成功！获得积分：{add_points}\n当前积分：{points}"

    keyboard = [
        [InlineKeyboardButton("📝 签到", callback_data="sign_in")],
        [InlineKeyboardButton("⬅️ 返回首页", callback_data="back_start")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
from fastapi import FastAPI, Request
from telegram.ext import Application

app = FastAPI()

# moontag活动按钮一：看视频广告积分
async def moontag_watch_ad_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    today = date.today()

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT ad_date, watch_count FROM moontag_ad WHERE user_id=$1", user_id)
        watch_count = row['watch_count'] if row and row['ad_date'] == today else 0

    if watch_count >= 3:
        await query.edit_message_text("你今天的广告观看次数已达上限（3次）。明天再来吧！", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ 返回活动中心", callback_data="moontag_hd")]]
        ))
        return

    ad_url = f"{MOONTAG_AD_URL_BASE}?user_id={user_id}"

    keyboard = [
        [InlineKeyboardButton("点击观看广告", url=ad_url)],
        [InlineKeyboardButton("⬅️ 返回活动中心", callback_data="moontag_hd")]
    ]
    await query.edit_message_text(
        "请点击下面按钮观看广告，观看完成后网页会自动奖励积分。",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# moontag活动按钮二：密钥领取按钮
async def moontag_secret_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    today = datetime.now(BJ_TZ).date()
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT redeem1, redeem2, last_redeem_date FROM user_secret_redeem WHERE user_id=$1", user_id)
        redeem_count = 0
        if row and row['last_redeem_date'] == today:
            redeem_count = (1 if row['redeem1'] else 0) + (1 if row['redeem2'] else 0)

        secret_row = await conn.fetchrow("SELECT secret1_link, secret2_link FROM daily_secrets ORDER BY created_at DESC LIMIT 1")

    if redeem_count >= MAX_SECRET_REDEEM:
        await query.edit_message_text(f"您今天已领取{MAX_SECRET_REDEEM}次密钥积分，明天上午10点后再来哦~", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ 返回活动中心", callback_data="moontag_hd")]]
        ))
        return

    if not secret_row or not secret_row['secret1_link'] or not secret_row['secret2_link']:
        await query.edit_message_text(
            "管理员尚未绑定当天密钥链接，请等待管理员更换新密钥链接。\n\n"
            "请稍后再试。",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ 返回活动中心", callback_data="moontag_hd")]]
            )
        )
        return

    text = (
        f"{BUTTON_TWO_NAME} 功能说明：\n"
        "每天可通过夸克网盘获取密钥。\n"
        "点击“开始获得密钥”按钮后，将打开广告直链，\n"
        "3秒后自动跳转到密钥链接，请耐心等待。\n"
        "请保存网盘，重命名名字，复制文本发送给机器人领取积分。\n\n"
        f"密钥链接示例：\n1️⃣ {SECRET_LINK_1}\n2️⃣ {SECRET_LINK_2}"
    )
    keyboard = [
        [InlineKeyboardButton("🎯 开始获得密钥", url=MOONTAG_LINK_2)],
        [InlineKeyboardButton("⬅️ 返回活动中心", callback_data="moontag_hd")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# 用户输入密钥领取积分逻辑
async def secret_code_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    today = datetime.now(BJ_TZ).date()
    async with db_pool.acquire() as conn:
        secret_row = await conn.fetchrow("SELECT secret1, secret2 FROM daily_secrets ORDER BY created_at DESC LIMIT 1")
        if not secret_row:
            await update.message.reply_text("密钥尚未生成，请稍后再试。")
            return

        secret1 = secret_row['secret1']
        secret2 = secret_row['secret2']

        user_row = await conn.fetchrow("SELECT redeem1, redeem2, last_redeem_date FROM user_secret_redeem WHERE user_id=$1", user_id)
        if user_row and user_row['last_redeem_date'] == today:
            redeem1 = user_row['redeem1']
            redeem2 = user_row['redeem2']
        else:
            redeem1 = False
            redeem2 = False

        if text == secret1:
            if redeem1:
                await update.message.reply_text("您今天已经领取过密钥1积分，不能重复领取。")
                return
            await add_points(user_id, 8)
            await conn.execute("""
                INSERT INTO user_secret_redeem (user_id, redeem1, redeem2, last_redeem_date)
                VALUES ($1, TRUE, $2, $3)
                ON CONFLICT (user_id) DO UPDATE SET redeem1=TRUE, last_redeem_date=$3
            """, user_id, redeem2, today)
            await update.message.reply_text("密钥1验证成功，获得8积分！已返回活动中心。")
            await back_to_hd(update, context)
            return

        elif text == secret2:
            if redeem2:
                await update.message.reply_text("您今天已经领取过密钥2积分，不能重复领取。")
                return
            await add_points(user_id, 6)
            await conn.execute("""
                INSERT INTO user_secret_redeem (user_id, redeem1, redeem2, last_redeem_date)
                VALUES ($1, $2, TRUE, $3)
                ON CONFLICT (user_id) DO UPDATE SET redeem2=TRUE, last_redeem_date=$3
            """, user_id, redeem1, today)
            await update.message.reply_text("密钥2验证成功，获得6积分！已返回活动中心。")
            await back_to_hd(update, context)
            return

        else:
            await update.message.reply_text("密钥错误，请确认后重新输入。")

# 管理员 /my 命令绑定密钥链接逻辑
async def my_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("你不是管理员，无权限使用此命令。")
        return

    now = datetime.now(BJ_TZ)
    if now.hour < 10:
        await update.message.reply_text("请北京时间上午10点后再使用此命令。")
        return

    bind_count = context.user_data.get("my_bind_count", 0) + 1
    context.user_data["my_bind_count"] = bind_count

    if bind_count == 1:
        context.user_data["awaiting_secret1_link"] = True
        context.user_data["awaiting_secret2_link"] = False
        await update.message.reply_text("请输入密钥一的链接（示例：https://pan.quark.cn/s/xxxxxx）")
    elif bind_count == 2:
        context.user_data["awaiting_secret1_link"] = False
        context.user_data["awaiting_secret2_link"] = True
        await update.message.reply_text("请输入密钥二的链接")
    elif bind_count == 3:
        context.user_data["awaiting_secret1_link"] = True
        context.user_data["awaiting_secret2_link"] = False
        await update.message.reply_text("第三次绑定，覆盖之前所有密钥链接。\n请输入新的密钥一链接")
    else:
        context.user_data["my_bind_count"] = 1
        context.user_data["awaiting_secret1_link"] = True
        context.user_data["awaiting_secret2_link"] = False
        await update.message.reply_text("绑定次数超过3次，计数重置。\n请输入密钥一链接")

async def my_link_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return

    now = datetime.now(BJ_TZ)
    if now.hour < 10:
        await update.message.reply_text("请北京时间上午10点后再绑定密钥链接。")
        return

    text = update.message.text.strip()

    if context.user_data.get("awaiting_secret1_link"):
        async with db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE daily_secrets SET secret1_link=$1, created_at=NOW()
                WHERE id = (SELECT id FROM daily_secrets ORDER BY created_at DESC LIMIT 1)
            """, text)
        context.user_data["awaiting_secret1_link"] = False
        await update.message.reply_text("密钥一链接绑定完成。请输入密钥二链接。")
        context.user_data["awaiting_secret2_link"] = True
    elif context.user_data.get("awaiting_secret2_link"):
        async with db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE daily_secrets SET secret2_link=$1, created_at=NOW()
                WHERE id = (SELECT id FROM daily_secrets ORDER BY created_at DESC LIMIT 1)
            """, text)
        context.user_data["awaiting_secret2_link"] = False
        await update.message.reply_text("密钥二链接绑定完成。绑定流程结束。")
        context.user_data["my_bind_count"] = 0

# FastAPI接口，网页调用广告观看成功回调
@app.post("/api/mark_ad_watched")
async def mark_ad_watched(request: Request):
    data = await request.json()
    user_id = data.get("user_id")
    if not user_id:
        return {"success": False, "message": "缺少user_id"}

    async with db_pool.acquire() as conn:
        today = date.today()
        row = await conn.fetchrow("SELECT ad_date, watch_count FROM moontag_ad WHERE user_id=$1", int(user_id))
        if row and row['ad_date'] == today:
            watch_count = row['watch_count']
            if watch_count >= 3:
                return {"success": False, "message": "今日观看次数已达上限"}
            watch_count += 1
            await conn.execute("UPDATE moontag_ad SET watch_count=$1 WHERE user_id=$2", watch_count, int(user_id))
        else:
            await conn.execute("INSERT INTO moontag_ad (user_id, ad_date, watch_count) VALUES ($1, $2, 1)", int(user_id), today)

    return {"success": True, "message": "广告观看成功，积分已更新"}

# 定时任务，每天北京时间10点自动生成密钥并私信管理员
async def scheduled_secret_generation(application):
    now = datetime.now(BJ_TZ)
    secret1 = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
    secret2 = ''.join(random.choices(string.ascii_letters + string.digits, k=10))

    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO daily_secrets (secret1, secret2, created_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (id) DO UPDATE SET
                secret1=EXCLUDED.secret1,
                secret2=EXCLUDED.secret2,
                created_at=EXCLUDED.created_at
        """, secret1, secret2, now)

    try:
        await application.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"【每日密钥更新】\n"
                f"时间：{now.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"密钥1（8积分）：{secret1}\n"
                f"密钥2（6积分）：{secret2}\n\n"
                f"请使用 /my 命令绑定密钥链接。"
            )
        )
    except Exception as e:
        logger.error(f"发送管理员消息失败: {e}")

# 主函数启动
def run_fastapi():
    uvicorn.run(app, host="0.0.0.0", port=PORT)

def main():
    global application
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # 注册Telegram所有handler（请补充之前代码中的handler）

    application.add_handler(CommandHandler("my", my_command))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), my_link_input_handler))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), secret_code_handler))

    # APScheduler定时任务
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(scheduled_secret_generation, "cron", hour=10, minute=0, args=[application])
    scheduler.start()

    # 启动FastAPI服务线程
    threading.Thread(target=run_fastapi, daemon=True).start()

    # 启动Telegram机器人轮询
    application.run_polling()

if __name__ == "__main__":
    asyncio.run(init_db_pool())
    main()
