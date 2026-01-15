import os
import ssl
import re
import asyncio
import random
import datetime
from html import escape
from typing import Dict, Any, Optional, List, Tuple

import asyncpg
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

# ============================================================
# Railway Variables（不是 .env 文件）
# 必须在 Railway -> Variables 配置：
#   BOT_TOKEN=xxxx
#   DATABASE_URL=postgresql://...
# ============================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if not BOT_TOKEN or not DATABASE_URL:
    raise RuntimeError("缺少 Railway Variables：BOT_TOKEN 或 DATABASE_URL")

# ============================================================
# 【需要你修改】管理员 Telegram user_id（可多个）
# 用 @userinfobot 查看自己的 user_id
# ============================================================
ADMIN_IDS = {1480512549}

GROUP_LINK = "https://t.me/+495j5rWmApsxYzg9"

# ============================================================
# ✅ 你的页面 File ID（已按你要求写死）
# ============================================================
VIP_PAGE_FILE_ID = "AgACAgUAAxkBAAIBJ2loboOm15d-Qog2KkzAVSTLG-1eAAKaD2sbQNhBV_UKRl5JPolfAQADAgADeAADOAQ"
VIP_ORDER_PAGE_FILE_ID = "AgACAgUAAxkBAAIBHWlobOW8SVMC9dk6a5KquMiQHPh1AAKVD2sbQNhBV9mV11AQnf1xAQADAgADeQADOAQ"

WECHAT_PAY_PAGE_FILE_ID = "AgACAgUAAxkBAAIBImlobmPLtn9DWUFZJ53t1mhkVIA7AAKYD2sbQNhBV_A-2IdqoG-dAQADAgADeAADOAQ"
WECHAT_ORDER_PAGE_FILE_ID = "AgACAgUAAxkBAAIBLWlocIlhveHnlgntE7dGi1ri56i2AAKeD2sbQNhBVyZ8_L3zE7qwAQADAgADeQADOAQ"

ALIPAY_PAY_PAGE_FILE_ID = "AgACAgUAAxkBAAIBJWlobnt_eXxhfHqg5bpF8WFwDDESAAKZD2sbQNhBVyWCVUCv9Q3iAQADAgADeAADOAQ"
ALIPAY_ORDER_PAGE_FILE_ID = "AgACAgUAAxkBAAIBMGlocJCdAlLyJie451mVeM6gi7xhAAKfD2sbQNhBV-EDx2qKNqc-AQADAgADeQADOAQ"

# ============================================================
# 自动清理：只在私聊生效
# 删除“用户触发命令消息 + copyMessage转发消息”，20分钟后删除
# 删除后发提示文案 + 再发首页欢迎（相当于跳转首页）
# ============================================================
AUTO_DELETE_SECONDS = 20 * 60

EXPIRE_NOTICE = (
    "⏳ 本次内容仅保留 20 分钟，现已自动清理。\n"
    "如需再次查看，请回到「购买入口」重新获取；已购买用户无需重复付款。"
)

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
    "👉 如需帮助请私信管理员"
)

VIP_ORDER_PROMPT_TEXT = (
    "🔍 <b>请输入您的订单号</b>\n"
    "我将为您核验通过后，发送入群入口。"
)

TOPUP_BIG_WARN = (
    "<b>温馨提示（重要）</b>\n"
    "• 微信充值与支付宝充值 <b>各仅允许成功一次</b>\n"
    "• 请确认支付无误后再提交订单号\n"
    "• 请勿重复充值，如需协助请联系管理员"
)

WECHAT_ORDER_PROMPT = (
    "🔎 <b>请发送微信「交易单号」</b>\n\n"
    "路径：微信 → 我 → 服务/钱包 → 账单 → 该笔付款 → 详情 → 交易单号"
)

ALIPAY_ORDER_PROMPT = (
    "🔎 <b>请发送支付宝「商家订单号」</b>\n\n"
    "路径：支付宝 → 我的 → 账单 → 该笔交易 → 账单详情 → 更多 → 商家订单号"
)

ADMIN_WELCOME = (
    "🛠️ <b>管理员系统</b>\n"
    "• 商品：添加/上下架\n"
    "• 📣 频道转发库：命令（支持中文/大写）+ 粘贴消息链接 → 用户输入命令自动 copyMessage 转发\n"
)

# =========================
# 工具
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

def norm_key(s: str) -> str:
    t = (s or "").strip()
    if t.startswith("/"):
        t = t[1:]
    t = t.strip()
    t = t.split()[0]
    t = t.split("@")[0]
    return t.casefold()  # 支持中文/大写（中文不受影响，英文大小写统一）

def parse_links(text: str) -> List[str]:
    if not text:
        return []
    tokens = re.split(r"[\s\r\n]+", text.strip())
    links = []
    for tk in tokens:
        if "t.me/" in tk:
            links.append(re.split(r"[?#]", tk)[0])
    return [l for l in links if l]

def parse_message_link(url: str) -> Tuple[str, int]:
    u = re.split(r"[?#]", url.strip())[0]

    m = re.match(r"^https?://t\.me/c/(\d+)/(\d+)$", u)
    if m:
        internal = m.group(1)
        msgid = int(m.group(2))
        return f"-100{internal}", msgid

    m = re.match(r"^https?://t\.me/([A-Za-z0-9_]+)/(\d+)$", u)
    if m:
        username = m.group(1)
        msgid = int(m.group(2))
        return f"@{username}", msgid

    raise ValueError("无法识别链接格式，请使用频道消息的“复制链接”")

def cast_from_chat_id(s: str):
    s = str(s).strip()
    if s.startswith("@"):
        return s
    return int(s)

# =========================
# UI
# =========================
def kb_home():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡️ 开始验证", callback_data="vip_intro")],
        [InlineKeyboardButton("🎯 积分", callback_data="points_home")],
    ])

# “开始验证”页：去掉积分中心按钮（按你要求）
def kb_vip():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 已付款，开始验证", callback_data="vip_pay")],
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
        [InlineKeyboardButton("🟩 微信充值", callback_data="topup_wechat")],
        [InlineKeyboardButton("🔵 支付宝充值", callback_data="topup_alipay")],
        [InlineKeyboardButton("⬅️ 返回积分中心", callback_data="points_home")]
    ])

def kb_wechat_pay():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 我已支付，提交订单", callback_data="wechat_submit")],
        [InlineKeyboardButton("⬅️ 返回充值方式", callback_data="topup_menu")]
    ])

def kb_alipay_pay():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 我已支付，提交订单", callback_data="alipay_submit")],
        [InlineKeyboardButton("⬅️ 返回充值方式", callback_data="topup_menu")]
    ])

def kb_after_points():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 返回积分中心", callback_data="points_home")],
        [InlineKeyboardButton("⬅️ 返回首页", callback_data="home")]
    ])

def kb_join_group():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🚪 加入会员群", url=GROUP_LINK)]])

# 管理员：频道转发库菜单
def kb_admin_home():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 频道转发库（copyMessage）", callback_data="ccmd_menu")],
        [InlineKeyboardButton("⬅️ 返回首页", callback_data="home")]
    ])

def kb_ccmd_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ 新建/更新命令", callback_data="ccmd_add")],
        [InlineKeyboardButton("📄 查看命令列表", callback_data="ccmd_list")],
        [InlineKeyboardButton("🗑 删除命令", callback_data="ccmd_del")],
        [InlineKeyboardButton("⬅️ 返回后台", callback_data="admin_back")]
    ])

def kb_ccmd_collect():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 完成绑定", callback_data="ccmd_finish")],
        [InlineKeyboardButton("❎ 取消", callback_data="admin_cancel")]
    ])

# =========================
# DB
# =========================
async def pool(app: Application) -> asyncpg.Pool:
    return app.bot_data["db_pool"]

async def ensure_user(app: Application, user_id: int):
    p = await pool(app)
    async with p.acquire() as conn:
        await conn.execute(
            "INSERT INTO users(user_id) VALUES($1) ON CONFLICT(user_id) DO NOTHING;",
            user_id
        )

async def get_user(app: Application, user_id: int) -> Dict[str, Any]:
    await ensure_user(app, user_id)
    p = await pool(app)
    async with p.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE user_id=$1;", user_id)
    return dict(row)

async def add_ledger(app: Application, user_id: int, delta: int, reason: str):
    p = await pool(app)
    async with p.acquire() as conn:
        await conn.execute(
            "INSERT INTO points_ledger(user_id, delta, reason) VALUES($1,$2,$3);",
            user_id, delta, reason
        )

async def set_state(app: Application, user_id: int, state: Optional[str]):
    p = await pool(app)
    async with p.acquire() as conn:
        await conn.execute("UPDATE users SET state=$1 WHERE user_id=$2;", state, user_id)

# ============== channel forwarding DB ==============
async def ccmd_reset(app: Application, key_norm: str, display_key: str, from_chat_id: str, admin_id: int):
    p = await pool(app)
    async with p.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM channel_items WHERE key_norm=$1;", key_norm)
            await conn.execute(
                """
                INSERT INTO channel_commands(key_norm, display_key, from_chat_id, active, created_by)
                VALUES($1,$2,$3,FALSE,$4)
                ON CONFLICT(key_norm) DO UPDATE SET
                  display_key=EXCLUDED.display_key,
                  from_chat_id=EXCLUDED.from_chat_id,
                  active=FALSE,
                  created_by=EXCLUDED.created_by;
                """,
                key_norm, display_key, from_chat_id, admin_id
            )

async def ccmd_add_item(app: Application, key_norm: str, seq: int, message_id: int, message_link: str):
    p = await pool(app)
    async with p.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO channel_items(key_norm, seq, message_id, message_link)
            VALUES($1,$2,$3,$4)
            ON CONFLICT(key_norm, seq) DO UPDATE SET
              message_id=EXCLUDED.message_id,
              message_link=EXCLUDED.message_link;
            """,
            key_norm, seq, message_id, message_link
        )

async def ccmd_finish(app: Application, key_norm: str) -> int:
    p = await pool(app)
    async with p.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM channel_items WHERE key_norm=$1;", key_norm)
        if count and count > 0:
            await conn.execute("UPDATE channel_commands SET active=TRUE WHERE key_norm=$1;", key_norm)
        return int(count or 0)

async def ccmd_get(app: Application, key_norm: str) -> Optional[Dict[str, Any]]:
    p = await pool(app)
    async with p.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT key_norm, display_key, from_chat_id, active FROM channel_commands WHERE key_norm=$1;",
            key_norm
        )
    return dict(row) if row else None

async def ccmd_items(app: Application, key_norm: str) -> List[Dict[str, Any]]:
    p = await pool(app)
    async with p.acquire() as conn:
        rows = await conn.fetch(
            "SELECT seq, message_id FROM channel_items WHERE key_norm=$1 ORDER BY seq ASC;",
            key_norm
        )
    return [dict(r) for r in rows]

async def ccmd_list(app: Application, limit: int = 50) -> List[Dict[str, Any]]:
    p = await pool(app)
    async with p.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.display_key, c.key_norm, c.active, c.from_chat_id, COUNT(i.*) AS parts
            FROM channel_commands c
            LEFT JOIN channel_items i ON i.key_norm=c.key_norm
            GROUP BY c.display_key, c.key_norm, c.active, c.from_chat_id
            ORDER BY c.display_key ASC
            LIMIT $1;
            """,
            limit
        )
    return [dict(r) for r in rows]

async def ccmd_delete(app: Application, key_norm: str):
    p = await pool(app)
    async with p.acquire() as conn:
        await conn.execute("DELETE FROM channel_commands WHERE key_norm=$1;", key_norm)

# ============== admin_drafts minimal ==============
async def draft_set(app: Application, admin_id: int, stage: str, product_id: Optional[str]=None,
                    name: Optional[str]=None, cost: Optional[int]=None, kind: Optional[str]=None):
    p = await pool(app)
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
    p = await pool(app)
    async with p.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM admin_drafts WHERE admin_id=$1;", admin_id)
    return dict(row) if row else None

async def draft_clear(app: Application, admin_id: int):
    p = await pool(app)
    async with p.acquire() as conn:
        await conn.execute("DELETE FROM admin_drafts WHERE admin_id=$1;", admin_id)

# =========================
# 首页/积分中心输出工具
# =========================
async def push_home(bot, chat_id: int):
    # 你未提供首页图片，这里用纯文本首页
    await bot.send_message(chat_id=chat_id, text=WELCOME_TEXT, reply_markup=kb_home())

async def push_points_center(bot, app: Application, chat_id: int, user_id: int):
    u = await get_user(app, user_id)
    text = (
        "🎯 <b>积分中心</b>\n\n"
        f"当前积分：<b>{u['points']}</b>\n"
        "在这里你可以签到、充值、兑换、查看余额与排行榜。"
    )
    await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=kb_points())

# =========================
# ✅ 自动删除（仅私聊）
# =========================
async def auto_delete_private(bot, app: Application, chat_id: int, message_ids: List[int]):
    await asyncio.sleep(AUTO_DELETE_SECONDS)

    for mid in set(message_ids):
        try:
            await bot.delete_message(chat_id=chat_id, message_id=int(mid))
        except Exception:
            pass

    # 删除后提示 + 回首页
    try:
        await bot.send_message(chat_id=chat_id, text=EXPIRE_NOTICE)
        await push_home(bot, chat_id)
    except Exception:
        pass

def schedule_private_autodelete(context: ContextTypes.DEFAULT_TYPE, chat_type: str, chat_id: int, message_ids: List[int], app: Application):
    if chat_type != "private":
        return
    asyncio.create_task(auto_delete_private(context.bot, app, chat_id, message_ids))

# =========================
# ✅ 频道命令触发：copyMessage + 20分钟后删除（仅私聊）
# =========================
async def send_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE, key_norm: str) -> bool:
    app = context.application
    cmd = await ccmd_get(app, key_norm)
    if not cmd or not cmd.get("active"):
        return False

    items = await ccmd_items(app, key_norm)
    if not items:
        await update.effective_message.reply_text("该命令暂无可用内容。")
        return True

    from_chat_id = cast_from_chat_id(cmd["from_chat_id"])
    to_chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type

    # 删除列表：用户命令消息 + copyMessage消息（按你要求）
    delete_ids: List[int] = []
    delete_ids.append(update.effective_message.message_id)

    # 发送转发内容
    try:
        for it in items:
            mid_obj = await context.bot.copy_message(
                chat_id=to_chat_id,
                from_chat_id=from_chat_id,
                message_id=int(it["message_id"])
            )
            delete_ids.append(int(mid_obj.message_id))
            await asyncio.sleep(0.6)
    except Exception:
        await update.effective_message.reply_text(
            "❌ 转发失败。\n\n请管理员检查：\n"
            "1) 机器人是否在该频道，并且为管理员\n"
            "2) 链接对应消息是否存在\n"
            "3) 频道是否开启了内容保护（可能导致无法复制）"
        )
        return True

    # ✅ 只在私聊安排删除
    schedule_private_autodelete(context, chat_type, to_chat_id, delete_ids, app)
    return True

# =========================
# /start /admin
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ensure_user(context.application, update.effective_user.id)
    await push_home(context.bot, update.effective_chat.id)

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ 无权限访问管理员系统。")
        return
    await update.message.reply_text(ADMIN_WELCOME, parse_mode=ParseMode.HTML, reply_markup=kb_admin_home())

# =========================
# 处理 Telegram “/命令”（英文/数字/下划线的command）
# =========================
async def on_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key_norm = norm_key(update.message.text)
    if not key_norm:
        return
    await send_channel_command(update, context, key_norm)

# =========================
# Callback（按钮逻辑）
# =========================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    app = context.application
    uid = q.from_user.id
    chat_id = q.message.chat_id

    # 首页
    if q.data == "home":
        await push_home(context.bot, chat_id)
        return

    # ====== 首页点“开始验证” -> VIP会员页面：插入 file_id(1) ======
    if q.data == "vip_intro":
        await q.message.reply_photo(
            photo=VIP_PAGE_FILE_ID,
            caption=VIP_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=kb_vip()
        )
        return

    # ====== VIP页点“已付款” -> 输入订单号页：插入 file_id(2) ======
    if q.data == "vip_pay":
        # 设置状态等待输入 VIP 订单号
        p = await pool(app)
        async with p.acquire() as conn:
            await conn.execute("UPDATE users SET state='vip_order', vip_attempts=0 WHERE user_id=$1;", uid)

        await q.message.reply_photo(
            photo=VIP_ORDER_PAGE_FILE_ID,
            caption=VIP_ORDER_PROMPT_TEXT,
            parse_mode=ParseMode.HTML
        )
        return

    # ====== 积分中心 ======
    if q.data == "points_home":
        await push_points_center(context.bot, app, chat_id, uid)
        return

    if q.data == "checkin":
        u = await get_user(app, uid)
        if u["last_checkin_date"] == today_utc():
            await q.message.reply_text("📅 今天已签到～明天再来领取新积分吧。", reply_markup=kb_points())
            return
        gain = random.randint(3, 8)
        p = await pool(app)
        async with p.acquire() as conn:
            await conn.execute("UPDATE users SET points=points+$1, last_checkin_date=$2 WHERE user_id=$3;", gain, today_utc(), uid)
        await add_ledger(app, uid, gain, "签到奖励")
        u2 = await get_user(app, uid)
        await q.message.reply_text(f"✅ 签到成功！本次获得 {gain} 积分\n当前积分：{u2['points']}", reply_markup=kb_points())
        return

    # ====== 充值 ======
    if q.data == "topup_menu":
        await q.message.reply_text(TOPUP_BIG_WARN, parse_mode=ParseMode.HTML, reply_markup=kb_topup_menu())
        return

    # 微信充值页：插入 file_id(3)
    if q.data == "topup_wechat":
        u = await get_user(app, uid)
        if u["wechat_used"]:
            await q.message.reply_text("🟩 微信充值已成功使用过一次，请勿重复充值。", reply_markup=kb_topup_menu())
            return
        rem = lock_remaining(u["wechat_locked_until"])
        if rem:
            await q.message.reply_text(f"⚠️ 微信充值暂不可用，请 {rem} 后再试。", reply_markup=kb_topup_menu())
            return

        await q.message.reply_photo(
            photo=WECHAT_PAY_PAGE_FILE_ID,
            caption=TOPUP_BIG_WARN,
            parse_mode=ParseMode.HTML,
            reply_markup=kb_wechat_pay()
        )
        return

    # 微信提交订单页：插入 file_id(4)
    if q.data == "wechat_submit":
        u = await get_user(app, uid)
        if u["wechat_used"]:
            await q.message.reply_text("🟩 该方式已成功使用过一次，请勿重复充值。", reply_markup=kb_topup_menu())
            return

        p = await pool(app)
        async with p.acquire() as conn:
            await conn.execute("UPDATE users SET state='wechat_order', wechat_attempts=0 WHERE user_id=$1;", uid)

        await q.message.reply_photo(
            photo=WECHAT_ORDER_PAGE_FILE_ID,
            caption=WECHAT_ORDER_PROMPT,
            parse_mode=ParseMode.HTML
        )
        return

    # 支付宝充值页：插入 file_id(5)
    if q.data == "topup_alipay":
        u = await get_user(app, uid)
        if u["alipay_used"]:
            await q.message.reply_text("🔵 支付宝充值已成功使用过一次，请勿重复充值。", reply_markup=kb_topup_menu())
            return
        rem = lock_remaining(u["alipay_locked_until"])
        if rem:
            await q.message.reply_text(f"⚠️ 支付宝充值暂不可用，请 {rem} 后再试。", reply_markup=kb_topup_menu())
            return

        await q.message.reply_photo(
            photo=ALIPAY_PAY_PAGE_FILE_ID,
            caption=TOPUP_BIG_WARN,
            parse_mode=ParseMode.HTML,
            reply_markup=kb_alipay_pay()
        )
        return

    # 支付宝提交订单页：插入 file_id(6)
    if q.data == "alipay_submit":
        u = await get_user(app, uid)
        if u["alipay_used"]:
            await q.message.reply_text("🔵 该方式已成功使用过一次，请勿重复充值。", reply_markup=kb_topup_menu())
            return

        p = await pool(app)
        async with p.acquire() as conn:
            await conn.execute("UPDATE users SET state='alipay_order', alipay_attempts=0 WHERE user_id=$1;", uid)

        await q.message.reply_photo(
            photo=ALIPAY_ORDER_PAGE_FILE_ID,
            caption=ALIPAY_ORDER_PROMPT,
            parse_mode=ParseMode.HTML
        )
        return

    # ====== 管理员：频道转发库（copyMessage） ======
    if q.data == "ccmd_menu":
        if uid not in ADMIN_IDS:
            await q.message.reply_text("⛔ 无权限。")
            return
        await q.message.reply_text("📣 频道转发库", reply_markup=kb_ccmd_menu())
        return

    if q.data == "ccmd_add":
        if uid not in ADMIN_IDS:
            return
        await draft_clear(app, uid)
        await draft_set(app, uid, stage="ccmd_key")
        await q.message.reply_text("➕ 请输入命令（支持中文/大写）：")
        return

    if q.data == "ccmd_list":
        if uid not in ADMIN_IDS:
            return
        rows = await ccmd_list(app, limit=50)
        if not rows:
            await q.message.reply_text("暂无命令。", reply_markup=kb_ccmd_menu())
            return
        lines = ["📄 命令列表："]
        for r in rows:
            lines.append(f"• {r['display_key']}（{r['parts']}条）")
        await q.message.reply_text("\n".join(lines), reply_markup=kb_ccmd_menu())
        return

    if q.data == "ccmd_del":
        if uid not in ADMIN_IDS:
            return
        await draft_clear(app, uid)
        await draft_set(app, uid, stage="ccmd_delete")
        await q.message.reply_text("🗑 请输入要删除的命令：")
        return

    if q.data == "ccmd_finish":
        if uid not in ADMIN_IDS:
            return
        d = await draft_get(app, uid)
        if not d or d.get("stage") != "ccmd_links":
            await q.message.reply_text("当前没有进行中的绑定。", reply_markup=kb_ccmd_menu())
            return
        count = await ccmd_finish(app, d["product_id"])
        await draft_clear(app, uid)
        await q.message.reply_text(f"✅ 绑定完成（{count}条）", reply_markup=kb_ccmd_menu())
        return

    if q.data == "admin_back":
        if uid not in ADMIN_IDS:
            return
        await q.message.reply_text(ADMIN_WELCOME, parse_mode=ParseMode.HTML, reply_markup=kb_admin_home())
        return

    if q.data == "admin_cancel":
        if uid not in ADMIN_IDS:
            return
        d = await draft_get(app, uid)
        if d and d.get("stage") == "ccmd_links" and d.get("product_id"):
            await ccmd_delete(app, d["product_id"])
        await draft_clear(app, uid)
        await q.message.reply_text("已取消。", reply_markup=kb_admin_home())
        return

# =========================
# 文本消息入口：处理订单输入 + 频道命令触发 + 管理员绑定链接
# =========================
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    app = context.application
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    await ensure_user(app, uid)
    u = await get_user(app, uid)
    state = u.get("state")

    # 1) 管理员绑定频道命令
    if uid in ADMIN_IDS:
        d = await draft_get(app, uid)
        if d:
            stage = d.get("stage")

            if stage == "ccmd_key":
                display_key = text.strip()
                key_norm = norm_key(display_key)
                if not key_norm:
                    await update.message.reply_text("命令不能为空，请重新发送。")
                    return
                await draft_set(app, uid, stage="ccmd_links", product_id=key_norm, name=None, cost=1, kind=display_key)
                await update.message.reply_text("✅ 命令已记录。请粘贴频道消息链接（可多条，换行即可）。", reply_markup=kb_ccmd_collect())
                return

            if stage == "ccmd_links":
                key_norm = d["product_id"]
                display_key = d.get("kind") or key_norm
                seq = int(d.get("cost") or 1)
                cur_from = d.get("name")

                links = parse_links(text)
                if not links:
                    await update.message.reply_text("未检测到链接，请粘贴 t.me 的频道消息链接。", reply_markup=kb_ccmd_collect())
                    return

                for link in links:
                    from_chat_id, msgid = parse_message_link(link)
                    if cur_from is None:
                        cur_from = from_chat_id
                        await ccmd_reset(app, key_norm, display_key, cur_from, uid)
                    elif from_chat_id != cur_from:
                        await update.message.reply_text("❌ 检测到不同频道链接，已取消本次绑定。")
                        await ccmd_delete(app, key_norm)
                        await draft_clear(app, uid)
                        return
                    await ccmd_add_item(app, key_norm, seq, msgid, link)
                    seq += 1

                await draft_set(app, uid, stage="ccmd_links", product_id=key_norm, name=cur_from, cost=seq, kind=display_key)
                await update.message.reply_text("✅ 已添加链接。继续粘贴或点击「完成绑定」。", reply_markup=kb_ccmd_collect())
                return

            if stage == "ccmd_delete":
                key_norm = norm_key(text)
                await ccmd_delete(app, key_norm)
                await draft_clear(app, uid)
                await update.message.reply_text("✅ 已删除。", reply_markup=kb_ccmd_menu())
                return

    # 2) VIP订单输入
    if state == "vip_order":
        p = await pool(app)
        async with p.acquire() as conn:
            if str(text).startswith("20260"):
                await conn.execute("UPDATE users SET state=NULL, vip_attempts=0, vip_locked_until=NULL WHERE user_id=$1;", uid)
                await update.message.reply_text("✅ 核验通过！点击下方按钮加入会员群。", reply_markup=kb_join_group())
            else:
                attempts = u["vip_attempts"] + 1
                if attempts < 2:
                    await conn.execute("UPDATE users SET vip_attempts=$1 WHERE user_id=$2;", attempts, uid)
                    await update.message.reply_text("❌ 未查询到订单信息，请重试。")
                else:
                    locked_until = utcnow() + datetime.timedelta(hours=10)
                    await conn.execute("UPDATE users SET state=NULL, vip_attempts=0, vip_locked_until=$1 WHERE user_id=$2;", locked_until, uid)
                    await update.message.reply_text("❌ 尝试次数已达上限，请 10 小时后重试。")
                    await push_home(context.bot, chat_id)
        return

    # 3) 微信充值订单输入
    if state == "wechat_order":
        p = await pool(app)
        async with p.acquire() as conn:
            if digits_only(text).startswith("4200"):
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE users SET points=points+100, wechat_used=TRUE, wechat_attempts=0, wechat_locked_until=NULL, state=NULL WHERE user_id=$1;",
                        uid
                    )
                    await conn.execute("INSERT INTO points_ledger(user_id, delta, reason) VALUES($1, 100, '微信充值');", uid)
                await update.message.reply_text("✅ 已充值 100 积分。", reply_markup=kb_after_points())
            else:
                attempts = u["wechat_attempts"] + 1
                if attempts < 2:
                    await conn.execute("UPDATE users SET wechat_attempts=$1 WHERE user_id=$2;", attempts, uid)
                    await update.message.reply_text("❌ 订单识别失败，请重试。")
                else:
                    locked_until = utcnow() + datetime.timedelta(hours=10)
                    await conn.execute("UPDATE users SET state=NULL, wechat_attempts=0, wechat_locked_until=$1 WHERE user_id=$2;", locked_until, uid)
                    await update.message.reply_text("❌ 尝试次数已达上限，请 10 小时后重试。")
                    await push_points_center(context.bot, app, chat_id, uid)
        return

    # 4) 支付宝充值订单输入
    if state == "alipay_order":
        p = await pool(app)
        async with p.acquire() as conn:
            if digits_only(text).startswith("4768"):
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE users SET points=points+100, alipay_used=TRUE, alipay_attempts=0, alipay_locked_until=NULL, state=NULL WHERE user_id=$1;",
                        uid
                    )
                    await conn.execute("INSERT INTO points_ledger(user_id, delta, reason) VALUES($1, 100, '支付宝充值');", uid)
                await update.message.reply_text("✅ 已充值 100 积分。", reply_markup=kb_after_points())
            else:
                attempts = u["alipay_attempts"] + 1
                if attempts < 2:
                    await conn.execute("UPDATE users SET alipay_attempts=$1 WHERE user_id=$2;", attempts, uid)
                    await update.message.reply_text("❌ 订单识别失败，请重试。")
                else:
                    locked_until = utcnow() + datetime.timedelta(hours=10)
                    await conn.execute("UPDATE users SET state=NULL, alipay_attempts=0, alipay_locked_until=$1 WHERE user_id=$2;", locked_until, uid)
                    await update.message.reply_text("❌ 尝试次数已达上限，请 10 小时后重试。")
                    await push_points_center(context.bot, app, chat_id, uid)
        return

    # 5) 用户触发频道命令（中文/大写都支持）
    key_norm = norm_key(text)
    if key_norm:
        hit = await send_channel_command(update, context, key_norm)
        if hit:
            return

    # 默认提示
    await update.message.reply_text("请选择一个功能继续：", reply_markup=kb_home())

# =========================
# init / shutdown
# =========================
async def post_init(app: Application):
    ssl_ctx = ssl.create_default_context()
    app.bot_data["db_pool"] = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5, ssl=ssl_ctx)

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

    # 英文/数字命令（Telegram识别为 /command）也尝试触发频道库
    application.add_handler(MessageHandler(filters.COMMAND, on_command))

    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    application.run_polling(
        drop_pending_updates=True,
        poll_interval=1.0,
        timeout=30,
        read_timeout=90,
        connect_timeout=30,
        write_timeout=30,
        pool_timeout=30,
    )

if __name__ == "__main__":
    main()
