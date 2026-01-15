# =========================
# VIP中转 - 守门员小卫机器人（完整版）
#
# 功能：
# 1) /start：首页欢迎 + 【开始验证】+【积分】
# 2) VIP验证：输入订单号核验（内部规则），失败2次锁10小时
#    ✅ 修改点1：VIP失败2次锁10小时后 -> 提示后自动跳转到 /start 首页
# 3) 积分中心：
#    - 签到：每天一次，随机+3~8
#    - 充值：微信/支付宝各只能成功一次；失败2次锁10小时
#      ✅ 修改点3：充值失败2次锁10小时后 -> 提示后自动跳转到 积分中心页面
#    - 兑换：有固定测试商品（0积分=哈哈），兑换前确认/取消
#    - 余额：显示积分 + 最近流水
#    - 排行榜：近3天【获得积分】排行（delta>0，扣除不算），显示昵称+总积分+我的排名
# 4) /admin：管理员系统（添加商品 文本/图片/视频，商品上下架）
#
# 部署：
# - BOT_TOKEN、DATABASE_URL 放 Railway Variables（不使用 .env 文件）
# =========================

import os
import ssl
import random
import datetime
from html import escape
from typing import Dict, Any, Optional, List

import asyncpg
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ============================================================
# 【需要你修改 1/2】Railway Variables：
#   BOT_TOKEN=xxxx
#   DATABASE_URL=postgresql://...  (建议用 Vercel 的 POSTGRES_URL_NON_POOLING)
# ============================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if not BOT_TOKEN or not DATABASE_URL:
    raise RuntimeError("缺少 Railway Variables：BOT_TOKEN 或 DATABASE_URL")

# ============================================================
# 【需要你修改 2/2】管理员 Telegram user_id（可多个）
# 你可以用 @userinfobot 查看你的 user_id
# ============================================================
ADMIN_IDS = {1480512549}  # ← 改成你的管理员ID，例如 {111,222}

# ============================================================
# 可选：图片 File ID（不需要就留空字符串）
# ============================================================
WELCOME_IMAGE_FILE_ID = ""   # /start 欢迎图
VIP_IMAGE_FILE_ID = ""       # VIP说明图
WECHAT_IMAGE_FILE_ID = ""    # 微信充值页图
ALIPAY_IMAGE_FILE_ID = ""    # 支付宝充值页图

GROUP_LINK = "https://t.me/+495j5rWmApsxYzg9"

# =========================
# 文案
# =========================
WELCOME_TEXT = (
    "👋 欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~\n\n"
    "📢 小卫小卫，守门员小卫！\n"
    "一键入群，小卫帮你搞定！\n"
    "新人来报到，小卫查身份！"
)

VIP_TEXT = (
    "💎 <b>VIP会员特权说明</b>\n"
    "✅ 专属中转通道\n"
    "✅ 优先审核入群\n"
    "✅ 7×24小时客服支持\n"
    "✅ 定期福利活动\n\n"
    "👉 如需帮助请私信管理员\n\n"
    "请点击下方按钮继续："
)

VIP_ORDER_PROMPT = (
    "🔍 <b>请输入您的订单号</b>\n"
    "我将为您核验通过后，发送入群入口。"
)

TOPUP_BIG_WARN = (
    "<b>温馨提示（重要）</b>\n"
    "• 微信充值与支付宝充值 <b>各仅允许成功一次</b>\n"
    "• 请确认支付无误后再提交订单号\n"
    "• 请勿重复充值，如需协助请联系管理员"
)

WECHAT_GUIDE = (
    "<b>🟩 微信充值（💰 5元 = 100积分）</b>\n\n"
    f"{TOPUP_BIG_WARN}\n\n"
    "完成支付后，点击下方按钮提交订单号。"
)

WECHAT_ORDER_PROMPT = (
    "🔎 <b>请发送微信「交易单号」</b>\n\n"
    "查找路径：\n"
    "微信 → 我 → 服务/钱包 → 账单 → 找到本次付款 → 进入详情\n"
    "复制「交易单号」发送给我即可。"
)

ALIPAY_GUIDE = (
    "<b>🔵 支付宝充值（💰 5元 = 100积分）</b>\n\n"
    f"{TOPUP_BIG_WARN}\n\n"
    "完成支付后，点击下方按钮提交订单号。"
)

ALIPAY_ORDER_PROMPT = (
    "🔎 <b>请发送支付宝「商家订单号」</b>\n\n"
    "查找路径：\n"
    "支付宝 → 我的 → 账单 → 选择该笔交易 → 账单详情 → 更多\n"
    "找到「商家订单号」并发送给我即可。"
)

ADMIN_WELCOME = (
    "🛠️ <b>管理员系统</b>\n"
    "你好，我是守门员小卫的后台助手。\n\n"
    "你可以在这里：\n"
    "• 自定义上架商品（文本 / 图片 / 视频）\n"
    "• 商品上下架管理\n"
)

# =========================
# 基础工具
# =========================
def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)

def today_utc() -> datetime.date:
    return utcnow().date()

def lock_remaining(locked_until: Optional[datetime.datetime]) -> Optional[str]:
    if not locked_until:
        return None
    delta = locked_until - utcnow()
    if delta.total_seconds() <= 0:
        return None
    minutes = int(delta.total_seconds() // 60)
    h, m = divmod(minutes, 60)
    return f"{h}小时{m}分钟" if h else f"{m}分钟"

def digits_only(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit())

# =========================
# DB helpers
# =========================
async def db_pool(app: Application) -> asyncpg.Pool:
    return app.bot_data["db_pool"]

async def ensure_user(app: Application, user_id: int):
    p = await db_pool(app)
    async with p.acquire() as conn:
        await conn.execute(
            "INSERT INTO users(user_id) VALUES($1) ON CONFLICT(user_id) DO NOTHING;",
            user_id
        )

async def upsert_user_nick(app: Application, tg_user) -> None:
    """
    排行榜昵称存储：
    优先 @username，否则 full_name，否则 "用户后4位"
    """
    user_id = tg_user.id
    if tg_user.username:
        nick = f"@{tg_user.username}"
    else:
        nick = (tg_user.full_name or tg_user.first_name or "").strip()
        if not nick:
            nick = f"用户{str(user_id)[-4:]}"

    p = await db_pool(app)
    async with p.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users(user_id, tg_nick)
            VALUES($1,$2)
            ON CONFLICT(user_id) DO UPDATE SET tg_nick=EXCLUDED.tg_nick;
            """,
            user_id, nick
        )

async def get_user(app: Application, user_id: int) -> Dict[str, Any]:
    await ensure_user(app, user_id)
    p = await db_pool(app)
    async with p.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE user_id=$1;", user_id)
    return dict(row)

async def add_ledger(app: Application, user_id: int, delta: int, reason: str):
    p = await db_pool(app)
    async with p.acquire() as conn:
        await conn.execute(
            "INSERT INTO points_ledger(user_id, delta, reason) VALUES($1,$2,$3);",
            user_id, delta, reason
        )

async def set_state(app: Application, user_id: int, state: Optional[str]):
    p = await db_pool(app)
    async with p.acquire() as conn:
        await conn.execute("UPDATE users SET state=$1 WHERE user_id=$2;", state, user_id)

# =========================
# 页面跳转工具（用于你要的“自动跳转”）
# =========================
async def push_home(message):
    """自动回到 /start 首页"""
    if WELCOME_IMAGE_FILE_ID:
        await message.reply_photo(
            photo=WELCOME_IMAGE_FILE_ID,
            caption=WELCOME_TEXT,
            reply_markup=kb_home()
        )
    else:
        await message.reply_text(
            WELCOME_TEXT,
            reply_markup=kb_home()
        )

async def push_points_center(message, app: Application, user_id: int):
    """自动跳转到积分中心页面"""
    u = await get_user(app, user_id)
    text = (
        "🎯 <b>积分中心</b>\n\n"
        f"当前积分：<b>{u['points']}</b>\n"
        "在这里你可以签到、充值、兑换、查看余额与排行榜。"
    )
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_points())

# =========================
# 按钮 UI
# =========================
def kb_home():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡️ 开始验证", callback_data="vip_intro")],
        [InlineKeyboardButton("🎯 积分", callback_data="points_home")],
    ])

# ✅ 修改点2：删除 VIP 页面里的“积分中心”按钮，只保留付款验证 + 返回首页
def kb_vip():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 我已付款，开始验证", callback_data="vip_pay")],
        [InlineKeyboardButton("⬅️ 返回首页", callback_data="home")]
    ])

def kb_points():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 签到领积分", callback_data="checkin")],
        [InlineKeyboardButton("💳 充值积分", callback_data="topup_menu")],
        [InlineKeyboardButton("🎁 兑换", callback_data="exchange_menu")],
        [InlineKeyboardButton("💰 余额", callback_data="balance")],
        [InlineKeyboardButton("🏆 排行榜（近3天）", callback_data="leaderboard")],
        [InlineKeyboardButton("⬅️ 返回首页", callback_data="home")]
    ])

def kb_topup_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟩 微信充值｜5元=100积分", callback_data="topup_wechat")],
        [InlineKeyboardButton("🔵 支付宝充值｜5元=100积分", callback_data="topup_alipay")],
        [InlineKeyboardButton("⬅️ 返回积分中心", callback_data="points_home")]
    ])

def kb_wechat_pay():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 我已支付｜提交订单号", callback_data="wechat_submit")],
        [InlineKeyboardButton("⬅️ 返回充值方式", callback_data="topup_menu")]
    ])

def kb_alipay_pay():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 我已支付｜提交订单号", callback_data="alipay_submit")],
        [InlineKeyboardButton("⬅️ 返回充值方式", callback_data="topup_menu")]
    ])

def kb_after_points():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 返回积分中心", callback_data="points_home")],
        [InlineKeyboardButton("⬅️ 返回首页", callback_data="home")]
    ])

def kb_join_group():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚪 加入会员群", url=GROUP_LINK)]
    ])

def kb_confirm_redeem(pid: str, cost: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ 确认兑换（消耗 {cost} 积分）", callback_data=f"redeem_confirm:{pid}")],
        [InlineKeyboardButton("❎ 取消", callback_data="exchange_menu")]
    ])

def kb_admin_home():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ 添加商品", callback_data="admin_add")],
        [InlineKeyboardButton("📦 商品列表｜上下架", callback_data="admin_toggle_menu")],
        [InlineKeyboardButton("⬅️ 返回首页", callback_data="home")]
    ])

def kb_admin_kind_select():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 文本", callback_data="admin_kind:text"),
         InlineKeyboardButton("🖼️ 图片", callback_data="admin_kind:photo"),
         InlineKeyboardButton("🎬 视频", callback_data="admin_kind:video")],
        [InlineKeyboardButton("❎ 取消", callback_data="admin_cancel")]
    ])

# =========================
# 商品/兑换
# =========================
async def fetch_active_products(app: Application) -> List[Dict[str, Any]]:
    p = await db_pool(app)
    async with p.acquire() as conn:
        rows = await conn.fetch(
            "SELECT product_id, name, cost, kind, active FROM products WHERE active=TRUE ORDER BY created_at ASC;"
        )
    return [dict(r) for r in rows]

async def fetch_user_redemptions(app: Application, user_id: int) -> set:
    p = await db_pool(app)
    async with p.acquire() as conn:
        rows = await conn.fetch("SELECT product_id FROM redemptions WHERE user_id=$1;", user_id)
    return {r["product_id"] for r in rows}

async def fetch_product(app: Application, pid: str) -> Optional[Dict[str, Any]]:
    p = await db_pool(app)
    async with p.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM products WHERE product_id=$1;", pid)
    return dict(row) if row else None

async def send_product_content(update: Update, product: Dict[str, Any]):
    kind = product["kind"]
    name = product["name"]

    if kind == "text":
        await update.effective_message.reply_text(
            f"🎁 <b>{escape(name)}</b>\n\n{escape(product.get('content_text') or '')}",
            parse_mode=ParseMode.HTML
        )
    elif kind == "photo":
        await update.effective_message.reply_photo(
            photo=product.get("file_id") or "",
            caption=f"🎁 {name}"
        )
    elif kind == "video":
        await update.effective_message.reply_video(
            video=product.get("file_id") or "",
            caption=f"🎁 {name}"
        )

async def build_exchange_keyboard(app: Application, user_id: int) -> InlineKeyboardMarkup:
    products = await fetch_active_products(app)
    redeemed = await fetch_user_redemptions(app, user_id)

    buttons = []
    for p in products:
        pid = p["product_id"]
        name = p["name"]
        cost = int(p["cost"])

        if pid in redeemed:
            buttons.append([InlineKeyboardButton(f"✅ 已兑换｜{name}", callback_data=f"redeem_show:{pid}")])
        else:
            buttons.append([InlineKeyboardButton(f"🎁 {name}｜{cost}积分", callback_data=f"redeem_ask:{pid}")])

        # 测试商品下方给管理员添加入口
        if pid == "test" and user_id in ADMIN_IDS:
            buttons.append([InlineKeyboardButton("➕ 管理员：添加商品", callback_data="admin_add")])

    buttons.append([InlineKeyboardButton("⬅️ 返回积分中心", callback_data="points_home")])
    return InlineKeyboardMarkup(buttons)

# =========================
# 管理员草稿（多步添加商品）
# =========================
async def draft_set(app: Application, admin_id: int, stage: str,
                    product_id: Optional[str] = None,
                    name: Optional[str] = None,
                    cost: Optional[int] = None,
                    kind: Optional[str] = None):
    p = await db_pool(app)
    async with p.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO admin_drafts(admin_id, stage, product_id, name, cost, kind)
            VALUES($1,$2,$3,$4,$5,$6)
            ON CONFLICT(admin_id) DO UPDATE SET
              stage=EXCLUDED.stage,
              product_id=COALESCE(EXCLUDED.product_id, admin_drafts.product_id),
              name=COALESCE(EXCLUDED.name, admin_drafts.name),
              cost=COALESCE(EXCLUDED.cost, admin_drafts.cost),
              kind=COALESCE(EXCLUDED.kind, admin_drafts.kind);
            """,
            admin_id, stage, product_id, name, cost, kind
        )

async def draft_get(app: Application, admin_id: int) -> Optional[Dict[str, Any]]:
    p = await db_pool(app)
    async with p.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM admin_drafts WHERE admin_id=$1;", admin_id)
    return dict(row) if row else None

async def draft_clear(app: Application, admin_id: int):
    p = await db_pool(app)
    async with p.acquire() as conn:
        await conn.execute("DELETE FROM admin_drafts WHERE admin_id=$1;", admin_id)

# =========================
# /start /admin
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ensure_user(context.application, update.effective_user.id)
    await upsert_user_nick(context.application, update.effective_user)

    if WELCOME_IMAGE_FILE_ID:
        await update.message.reply_photo(photo=WELCOME_IMAGE_FILE_ID, caption=WELCOME_TEXT, reply_markup=kb_home())
    else:
        await update.message.reply_text(WELCOME_TEXT, reply_markup=kb_home())

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ensure_user(context.application, update.effective_user.id)
    await upsert_user_nick(context.application, update.effective_user)

    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ 无权限访问管理员系统。")
        return
    await update.message.reply_text(ADMIN_WELCOME, parse_mode=ParseMode.HTML, reply_markup=kb_admin_home())

# =========================
# Callback 入口（按钮）
# =========================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    app = context.application
    user_id = q.from_user.id

    await ensure_user(app, user_id)
    await upsert_user_nick(app, q.from_user)

    data = q.data

    # 首页
    if data == "home":
        await push_home(q.message)
        return

    # VIP 页面
    if data == "vip_intro":
        if VIP_IMAGE_FILE_ID:
            await q.message.reply_photo(photo=VIP_IMAGE_FILE_ID, caption=VIP_TEXT, parse_mode=ParseMode.HTML, reply_markup=kb_vip())
        else:
            await q.message.reply_text(VIP_TEXT, parse_mode=ParseMode.HTML, reply_markup=kb_vip())
        return

    if data == "vip_pay":
        u = await get_user(app, user_id)
        rem = lock_remaining(u["vip_locked_until"])
        if rem:
            await q.message.reply_text(f"⚠️ 当前通道暂不可用，请 {rem} 后再试。")
            return
        p = await db_pool(app)
        async with p.acquire() as conn:
            await conn.execute("UPDATE users SET state='vip_order', vip_attempts=0 WHERE user_id=$1;", user_id)
        await q.message.reply_text(VIP_ORDER_PROMPT, parse_mode=ParseMode.HTML)
        return

    # 积分中心
    if data == "points_home":
        await push_points_center(q.message, app, user_id)
        return

    # 签到
    if data == "checkin":
        u = await get_user(app, user_id)
        if u["last_checkin_date"] == today_utc():
            await q.message.reply_text("📅 今天已签到～明天再来领取新积分吧。", reply_markup=kb_points())
            return

        gain = random.randint(3, 8)
        p = await db_pool(app)
        async with p.acquire() as conn:
            await conn.execute(
                "UPDATE users SET points=points+$1, last_checkin_date=$2 WHERE user_id=$3;",
                gain, today_utc(), user_id
            )
        await add_ledger(app, user_id, gain, "签到奖励")

        u2 = await get_user(app, user_id)
        await q.message.reply_text(
            f"✅ 签到成功！本次获得 <b>{gain}</b> 积分\n当前积分：<b>{u2['points']}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_points()
        )
        return

    # 充值菜单
    if data == "topup_menu":
        u = await get_user(app, user_id)
        text = (
            "💳 <b>充值积分</b>\n\n"
            f"当前积分：<b>{u['points']}</b>\n\n"
            f"{TOPUP_BIG_WARN}"
        )
        await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_topup_menu())
        return

    # 微信充值
    if data == "topup_wechat":
        u = await get_user(app, user_id)
        if u["wechat_used"]:
            await q.message.reply_text("🟩 微信充值已成功使用过一次。请勿重复充值，可选择支付宝或联系管理员。", reply_markup=kb_topup_menu())
            return
        rem = lock_remaining(u["wechat_locked_until"])
        if rem:
            await q.message.reply_text(f"⚠️ 微信充值暂不可用，请 {rem} 后再试。", reply_markup=kb_topup_menu())
            return

        if WECHAT_IMAGE_FILE_ID:
            await q.message.reply_photo(photo=WECHAT_IMAGE_FILE_ID, caption=WECHAT_GUIDE, parse_mode=ParseMode.HTML, reply_markup=kb_wechat_pay())
        else:
            await q.message.reply_text(WECHAT_GUIDE, parse_mode=ParseMode.HTML, reply_markup=kb_wechat_pay())
        return

    if data == "wechat_submit":
        u = await get_user(app, user_id)
        if u["wechat_used"]:
            await q.message.reply_text("🟩 该方式已成功使用过一次，请勿重复充值。", reply_markup=kb_topup_menu())
            return
        rem = lock_remaining(u["wechat_locked_until"])
        if rem:
            await q.message.reply_text(f"⚠️ 当前暂不可提交，请 {rem} 后再试。", reply_markup=kb_topup_menu())
            return
        p = await db_pool(app)
        async with p.acquire() as conn:
            await conn.execute("UPDATE users SET state='wechat_order', wechat_attempts=0 WHERE user_id=$1;", user_id)
        await q.message.reply_text(WECHAT_ORDER_PROMPT, parse_mode=ParseMode.HTML)
        return

    # 支付宝充值
    if data == "topup_alipay":
        u = await get_user(app, user_id)
        if u["alipay_used"]:
            await q.message.reply_text("🔵 支付宝充值已成功使用过一次。请勿重复充值，可选择微信或联系管理员。", reply_markup=kb_topup_menu())
            return
        rem = lock_remaining(u["alipay_locked_until"])
        if rem:
            await q.message.reply_text(f"⚠️ 支付宝充值暂不可用，请 {rem} 后再试。", reply_markup=kb_topup_menu())
            return

        if ALIPAY_IMAGE_FILE_ID:
            await q.message.reply_photo(photo=ALIPAY_IMAGE_FILE_ID, caption=ALIPAY_GUIDE, parse_mode=ParseMode.HTML, reply_markup=kb_alipay_pay())
        else:
            await q.message.reply_text(ALIPAY_GUIDE, parse_mode=ParseMode.HTML, reply_markup=kb_alipay_pay())
        return

    if data == "alipay_submit":
        u = await get_user(app, user_id)
        if u["alipay_used"]:
            await q.message.reply_text("🔵 该方式已成功使用过一次，请勿重复充值。", reply_markup=kb_topup_menu())
            return
        rem = lock_remaining(u["alipay_locked_until"])
        if rem:
            await q.message.reply_text(f"⚠️ 当前暂不可提交，请 {rem} 后再试。", reply_markup=kb_topup_menu())
            return
        p = await db_pool(app)
        async with p.acquire() as conn:
            await conn.execute("UPDATE users SET state='alipay_order', alipay_attempts=0 WHERE user_id=$1;", user_id)
        await q.message.reply_text(ALIPAY_ORDER_PROMPT, parse_mode=ParseMode.HTML)
        return

    # 兑换
    if data == "exchange_menu":
        kb = await build_exchange_keyboard(app, user_id)
        await q.message.reply_text("🎁 <b>兑换中心</b>\n请选择要兑换的商品：", parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data.startswith("redeem_show:"):
        pid = data.split(":", 1)[1]
        product = await fetch_product(app, pid)
        if not product:
            await q.message.reply_text("该商品不存在或已下架。")
            return
        await send_product_content(update, product)
        return

    if data.startswith("redeem_ask:"):
        pid = data.split(":", 1)[1]
        product = await fetch_product(app, pid)
        if not product or not product["active"]:
            await q.message.reply_text("该商品不存在或已下架。")
            return
        cost = int(product["cost"])
        await q.message.reply_text(
            f"🎁 <b>{escape(product['name'])}</b>\n"
            f"需要消耗：<b>{cost}</b> 积分\n\n"
            "是否确认兑换？",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_confirm_redeem(pid, cost)
        )
        return

    if data.startswith("redeem_confirm:"):
        pid = data.split(":", 1)[1]
        product = await fetch_product(app, pid)
        if not product or not product["active"]:
            await q.message.reply_text("该商品不存在或已下架。")
            return

        p = await db_pool(app)
        async with p.acquire() as conn:
            already = await conn.fetchval(
                "SELECT 1 FROM redemptions WHERE user_id=$1 AND product_id=$2;",
                user_id, pid
            )
        if already:
            await send_product_content(update, product)
            return

        u = await get_user(app, user_id)
        cost = int(product["cost"])
        if u["points"] < cost:
            await q.message.reply_text("❌ 余额不足，请重试。", reply_markup=kb_after_points())
            return

        async with p.acquire() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE users SET points=points-$1 WHERE user_id=$2;", cost, user_id)
                await conn.execute(
                    "INSERT INTO points_ledger(user_id, delta, reason) VALUES($1,$2,$3);",
                    user_id, -cost, f"兑换商品：{product['name']}"
                )
                await conn.execute(
                    "INSERT INTO redemptions(user_id, product_id) VALUES($1,$2);",
                    user_id, pid
                )

        await q.message.reply_text("✅ 兑换成功！以下为兑换内容：")
        await send_product_content(update, product)
        return

    # 余额
    if data == "balance":
        u = await get_user(app, user_id)
        p = await db_pool(app)
        async with p.acquire() as conn:
            rows = await conn.fetch(
                "SELECT delta, reason, created_at FROM points_ledger WHERE user_id=$1 ORDER BY created_at DESC LIMIT 12;",
                user_id
            )

        bj = datetime.timezone(datetime.timedelta(hours=8))
        lines = []
        for r in rows:
            ts = r["created_at"].astimezone(bj)
            sign = "+" if r["delta"] > 0 else ""
            lines.append(f"{ts:%m-%d %H:%M}  {sign}{r['delta']}  · {r['reason']}")

        text = (
            "💰 <b>我的余额</b>\n\n"
            f"当前积分：<b>{u['points']}</b>\n\n"
            "<b>最近记录</b>\n" +
            ("\n".join(lines) if lines else "暂无记录")
        )
        await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_after_points())
        return

    # 排行榜（近3天只统计获得积分 delta>0；扣除不算；显示昵称+总积分）
    if data == "leaderboard":
        p = await db_pool(app)
        async with p.acquire() as conn:
            top = await conn.fetch(
                """
                WITH sums AS (
                  SELECT user_id, COALESCE(SUM(delta),0) AS earned
                  FROM points_ledger
                  WHERE created_at >= NOW() - INTERVAL '3 days'
                    AND delta > 0
                  GROUP BY user_id
                ),
                ranked AS (
                  SELECT s.user_id,
                         s.earned,
                         u.points AS total_points,
                         COALESCE(NULLIF(u.tg_nick,''), '用户' || RIGHT(s.user_id::text, 4)) AS nick,
                         DENSE_RANK() OVER (ORDER BY s.earned DESC) AS r
                  FROM sums s
                  JOIN users u ON u.user_id = s.user_id
                )
                SELECT user_id, earned, total_points, nick, r
                FROM ranked
                ORDER BY r ASC, user_id ASC
                LIMIT 10;
                """
            )

            my = await conn.fetchrow(
                """
                WITH sums AS (
                  SELECT user_id, COALESCE(SUM(delta),0) AS earned
                  FROM points_ledger
                  WHERE created_at >= NOW() - INTERVAL '3 days'
                    AND delta > 0
                  GROUP BY user_id
                ),
                ranked AS (
                  SELECT s.user_id,
                         s.earned,
                         u.points AS total_points,
                         COALESCE(NULLIF(u.tg_nick,''), '用户' || RIGHT(s.user_id::text, 4)) AS nick,
                         DENSE_RANK() OVER (ORDER BY s.earned DESC) AS r
                  FROM sums s
                  JOIN users u ON u.user_id = s.user_id
                )
                SELECT r, earned, total_points, nick
                FROM ranked
                WHERE user_id=$1;
                """,
                user_id
            )

            my_total = await conn.fetchval("SELECT points FROM users WHERE user_id=$1;", user_id)

        lines = [
            "🏆 <b>近3天积分排行榜</b>\n"
            "（仅统计获得积分；兑换扣除不计入；总积分为当前余额）\n"
        ]

        if not top:
            lines.append("暂无排行数据。")
        else:
            for idx, row in enumerate(top, start=1):
                nick = escape(row["nick"])
                lines.append(
                    f"{idx}. {nick} · 近3天获得 <b>{row['earned']}</b> · 总积分 <b>{row['total_points']}</b>"
                )

        if my:
            lines.append(
                f"\n你当前排名：<b>第 {my['r']} 名</b>\n"
                f"近3天获得：<b>{my['earned']}</b>\n"
                f"总积分：<b>{my['total_points']}</b>"
            )
        else:
            lines.append(
                f"\n你当前排名：<b>暂无上榜</b>\n"
                f"近3天获得：<b>0</b>\n"
                f"总积分：<b>{my_total}</b>"
            )

        await q.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb_after_points())
        return

    # 管理员按钮
    if data.startswith("admin"):
        if user_id not in ADMIN_IDS:
            await q.message.reply_text("⛔ 无权限操作。")
            return

        if data == "admin_cancel":
            await draft_clear(app, user_id)
            await q.message.reply_text("已取消本次操作。", reply_markup=kb_admin_home())
            return

        if data == "admin_add":
            await draft_set(app, user_id, stage="await_id")
            await q.message.reply_text(
                "➕ <b>添加商品</b>\n\n请输入商品编号（ID）。\n建议：字母/数字/短横线，尽量简短。",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❎ 取消", callback_data="admin_cancel")]])
            )
            return

        if data.startswith("admin_kind:"):
            kind = data.split(":", 1)[1]
            d = await draft_get(app, user_id)
            if not d or d["stage"] != "await_kind":
                await q.message.reply_text("当前没有进行中的添加流程。", reply_markup=kb_admin_home())
                return
            await draft_set(app, user_id, stage="await_content", kind=kind)
            tip = "请直接发送文本内容。" if kind == "text" else "请直接发送文件（图片/视频）。"
            await q.message.reply_text(
                f"✅ 类型已选择：<b>{kind}</b>\n{tip}",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❎ 取消", callback_data="admin_cancel")]])
            )
            return

        if data == "admin_toggle_menu":
            p = await db_pool(app)
            async with p.acquire() as conn:
                rows = await conn.fetch("SELECT product_id, name, cost, active FROM products ORDER BY created_at ASC;")

            if not rows:
                await q.message.reply_text("商品列表为空。", reply_markup=kb_admin_home())
                return

            buttons = []
            for r in rows:
                pid = r["product_id"]
                if pid == "test":
                    continue
                status = "🟢上架" if r["active"] else "⚫下架"
                buttons.append([InlineKeyboardButton(
                    f"{status}｜{r['name']}（{r['cost']}积分）",
                    callback_data=f"admin_toggle:{pid}"
                )])

            buttons.append([InlineKeyboardButton("⬅️ 返回后台", callback_data="admin_back")])
            await q.message.reply_text(
                "📦 <b>商品列表｜点击切换上下架</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return

        if data.startswith("admin_toggle:"):
            pid = data.split(":", 1)[1]
            p = await db_pool(app)
            async with p.acquire() as conn:
                await conn.execute("UPDATE products SET active = NOT active WHERE product_id=$1;", pid)
            await q.message.reply_text("✅ 已更新商品状态。", reply_markup=kb_admin_home())
            return

        if data == "admin_back":
            await q.message.reply_text(ADMIN_WELCOME, parse_mode=ParseMode.HTML, reply_markup=kb_admin_home())
            return

# =========================
# 文本消息入口（订单输入 / 管理员多步输入 / 非命令触发）
# =========================
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    app = context.application
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()

    await ensure_user(app, user_id)
    await upsert_user_nick(app, update.effective_user)

    # ---------- 管理员：添加商品多步输入（文本阶段） ----------
    if user_id in ADMIN_IDS:
        d = await draft_get(app, user_id)
        if d:
            stage = d["stage"]

            if stage == "await_id":
                pid = text
                if pid.lower() == "test":
                    await update.message.reply_text("该编号为固定测试商品，请换一个编号。")
                    return

                p = await db_pool(app)
                async with p.acquire() as conn:
                    exists = await conn.fetchval("SELECT 1 FROM products WHERE product_id=$1;", pid)
                if exists:
                    await update.message.reply_text("该编号已存在，请换一个 ID。")
                    return

                await draft_set(app, user_id, stage="await_name", product_id=pid)
                await update.message.reply_text("请输入商品名称（展示给用户）：")
                return

            if stage == "await_name":
                await draft_set(app, user_id, stage="await_cost", name=text)
                await update.message.reply_text("请输入兑换所需积分（数字，例如 0 / 10 / 50 / 100）：")
                return

            if stage == "await_cost":
                if not text.isdigit():
                    await update.message.reply_text("请发送纯数字积分，例如：100")
                    return
                cost = int(text)
                if cost < 0:
                    await update.message.reply_text("积分不能为负数，请重新输入。")
                    return
                await draft_set(app, user_id, stage="await_kind", cost=cost)
                await update.message.reply_text("请选择商品类型：", reply_markup=kb_admin_kind_select())
                return

            if stage == "await_content":
                if d.get("kind") != "text":
                    await update.message.reply_text("请发送对应文件（图片/视频），不要发送文字。")
                    return

                pid = d["product_id"]
                name = d["name"]
                cost = d["cost"]

                p = await db_pool(app)
                async with p.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO products(product_id, name, cost, kind, content_text, active)
                        VALUES($1,$2,$3,'text',$4,TRUE);
                        """,
                        pid, name, cost, text
                    )

                await draft_clear(app, user_id)
                await update.message.reply_text(f"✅ 商品已创建并上架：{name}（ID：{pid}）", reply_markup=kb_admin_home())
                return

    # ---------- 普通用户：订单输入状态优先 ----------
    u = await get_user(app, user_id)
    state = u["state"]

    # ========== VIP 验证 ==========
    if state == "vip_order":
        rem = lock_remaining(u["vip_locked_until"])
        if rem:
            await update.message.reply_text(f"⚠️ 当前通道暂不可用，请 {rem} 后再试。")
            return

        raw = digits_only(text) or text
        ok = str(raw).startswith("20260")  # 内部判断，不提示用户规则

        p = await db_pool(app)
        async with p.acquire() as conn:
            if ok:
                await conn.execute(
                    "UPDATE users SET state=NULL, vip_attempts=0, vip_locked_until=NULL WHERE user_id=$1;",
                    user_id
                )
                await update.message.reply_text("✅ 核验通过！点击下方按钮加入会员群。", reply_markup=kb_join_group())
            else:
                attempts = u["vip_attempts"] + 1
                if attempts < 2:
                    await conn.execute("UPDATE users SET vip_attempts=$1 WHERE user_id=$2;", attempts, user_id)
                    await update.message.reply_text("❌ 未查询到订单信息，请重试。")
                else:
                    locked_until = utcnow() + datetime.timedelta(hours=10)
                    await conn.execute(
                        "UPDATE users SET state=NULL, vip_attempts=0, vip_locked_until=$1 WHERE user_id=$2;",
                        locked_until, user_id
                    )
                    await update.message.reply_text("❌ 尝试次数已达上限，请 10 小时后重试。")
                    # ✅ 修改点1：自动跳转到 /start 首页
                    await push_home(update.message)
        return

    # ========== 微信充值 ==========
    if state == "wechat_order":
        if u["wechat_used"]:
            await set_state(app, user_id, None)
            await update.message.reply_text("🟩 该方式已成功使用过一次，请勿重复充值。")
            return

        rem = lock_remaining(u["wechat_locked_until"])
        if rem:
            await update.message.reply_text(f"⚠️ 当前暂不可提交，请 {rem} 后再试。")
            return

        order = digits_only(text)
        ok = order.startswith("4200") and len(order) >= 4  # 内部判断，不提示规则

        p = await db_pool(app)
        async with p.acquire() as conn:
            if ok:
                async with conn.transaction():
                    await conn.execute(
                        """
                        UPDATE users
                        SET points=points+100, wechat_used=TRUE, wechat_attempts=0, wechat_locked_until=NULL, state=NULL
                        WHERE user_id=$1;
                        """,
                        user_id
                    )
                    await conn.execute(
                        "INSERT INTO points_ledger(user_id, delta, reason) VALUES($1, 100, '微信充值');",
                        user_id
                    )
                u2 = await get_user(app, user_id)
                await update.message.reply_text(
                    f"✅ 已充值 <b>100</b> 积分\n当前积分：<b>{u2['points']}</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb_after_points()
                )
            else:
                attempts = u["wechat_attempts"] + 1
                if attempts < 2:
                    await conn.execute("UPDATE users SET wechat_attempts=$1 WHERE user_id=$2;", attempts, user_id)
                    await update.message.reply_text("❌ 订单识别失败，请重试。")
                    await update.message.reply_text(WECHAT_ORDER_PROMPT, parse_mode=ParseMode.HTML)
                else:
                    locked_until = utcnow() + datetime.timedelta(hours=10)
                    await conn.execute(
                        "UPDATE users SET state=NULL, wechat_attempts=0, wechat_locked_until=$1 WHERE user_id=$2;",
                        locked_until, user_id
                    )
                    await update.message.reply_text("❌ 尝试次数已达上限，请 10 小时后重试。")
                    # ✅ 修改点3：自动跳转到积分中心
                    await push_points_center(update.message, app, user_id)
        return

    # ========== 支付宝充值 ==========
    if state == "alipay_order":
        if u["alipay_used"]:
            await set_state(app, user_id, None)
            await update.message.reply_text("🔵 该方式已成功使用过一次，请勿重复充值。")
            return

        rem = lock_remaining(u["alipay_locked_until"])
        if rem:
            await update.message.reply_text(f"⚠️ 当前暂不可提交，请 {rem} 后再试。")
            return

        order = digits_only(text)
        ok = order.startswith("4768") and len(order) >= 4  # 内部判断，不提示规则

        p = await db_pool(app)
        async with p.acquire() as conn:
            if ok:
                async with conn.transaction():
                    await conn.execute(
                        """
                        UPDATE users
                        SET points=points+100, alipay_used=TRUE, alipay_attempts=0, alipay_locked_until=NULL, state=NULL
                        WHERE user_id=$1;
                        """,
                        user_id
                    )
                    await conn.execute(
                        "INSERT INTO points_ledger(user_id, delta, reason) VALUES($1, 100, '支付宝充值');",
                        user_id
                    )
                u2 = await get_user(app, user_id)
                await update.message.reply_text(
                    f"✅ 已充值 <b>100</b> 积分\n当前积分：<b>{u2['points']}</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb_after_points()
                )
            else:
                attempts = u["alipay_attempts"] + 1
                if attempts < 2:
                    await conn.execute("UPDATE users SET alipay_attempts=$1 WHERE user_id=$2;", attempts, user_id)
                    await update.message.reply_text("❌ 订单识别失败，请重试。")
                    await update.message.reply_text(ALIPAY_ORDER_PROMPT, parse_mode=ParseMode.HTML)
                else:
                    locked_until = utcnow() + datetime.timedelta(hours=10)
                    await conn.execute(
                        "UPDATE users SET state=NULL, alipay_attempts=0, alipay_locked_until=$1 WHERE user_id=$2;",
                        locked_until, user_id
                    )
                    await update.message.reply_text("❌ 尝试次数已达上限，请 10 小时后重试。")
                    # ✅ 修改点3：自动跳转到积分中心
                    await push_points_center(update.message, app, user_id)
        return

    # ---------- 非订单状态：允许不输入 /start ----------
    triggers_points = {"积分", "积分中心", "签到", "充值", "兑换", "余额", "排行榜"}
    triggers_start = {"开始验证", "验证", "start", "开始", "首页"}

    if text in triggers_start:
        await push_home(update.message)
        return

    if text in triggers_points:
        await push_points_center(update.message, app, user_id)
        return

    await update.message.reply_text("请选择一个功能继续：", reply_markup=kb_home())

# =========================
# 管理员：图片/视频商品内容上传
# =========================
async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    app = context.application
    user_id = update.effective_user.id

    await ensure_user(app, user_id)
    await upsert_user_nick(app, update.effective_user)

    if user_id not in ADMIN_IDS:
        return

    d = await draft_get(app, user_id)
    if not d or d["stage"] != "await_content":
        return

    kind = d.get("kind")
    pid, name, cost = d["product_id"], d["name"], d["cost"]

    file_id = None
    if kind == "photo" and update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif kind == "video" and update.message.video:
        file_id = update.message.video.file_id
    else:
        await update.message.reply_text("文件类型不匹配，请按所选类型发送（图片/视频）。")
        return

    p = await db_pool(app)
    async with p.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO products(product_id, name, cost, kind, file_id, active)
            VALUES($1,$2,$3,$4,$5,TRUE);
            """,
            pid, name, cost, kind, file_id
        )

    await draft_clear(app, user_id)
    await update.message.reply_text(f"✅ 商品已创建并上架：{name}（ID：{pid}）", reply_markup=kb_admin_home())

# =========================
# init / shutdown
# =========================
async def post_init(app: Application):
    ssl_ctx = ssl.create_default_context()
    app.bot_data["db_pool"] = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=5,
        ssl=ssl_ctx
    )

    # 兜底：确保测试商品永远存在且上架（0积分=哈哈）
    p = app.bot_data["db_pool"]
    async with p.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO products(product_id, name, cost, kind, content_text, active)
            VALUES ('test', '测试商品', 0, 'text', '哈哈', TRUE)
            ON CONFLICT (product_id) DO UPDATE SET active=TRUE;
            """
        )

async def post_shutdown(app: Application):
    p: asyncpg.Pool = app.bot_data.get("db_pool")
    if p:
        await p.close()

def main():
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("admin", admin_cmd))
    application.add_handler(CallbackQueryHandler(on_callback))

    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, on_media))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    # polling：同一个BOT_TOKEN只能跑一个实例
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
