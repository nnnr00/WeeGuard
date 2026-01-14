import os
import time
import random
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# =========================================================
# CONFIG（只改这里！都在这里标注清楚）
# =========================================================

# [Railway变量] 在 Railway -> Variables 设置：BOT_TOKEN=xxxx
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Please set it in Railway Variables.")

# [必须修改] 管理员 Telegram 数字ID（可多个）
# 说明：只有这些ID才能看到/使用管理员按钮（上架兑换礼品等）
ADMIN_IDS = {111111111}  # <<< 在这里替换为你的管理员TG数字ID，例如 {123456789}

# [必须确认] VIP 群链接（你提供的）
VIP_GROUP_LINK = "https://t.me/+495j5rWmApsxYzg9"

# [可修改] 管理员私信链接（VIP说明页里用）
ADMIN_CONTACT_LINK = "https://t.me/"  # <<< 改成 https://t.me/你的username

# ============ 图片：支持 file_id 或 URL（二选一，推荐 file_id） ============
# 你只要把 file_id 填进去即可。若 file_id 为空，则尝试用 URL；两者都空则只发文字，不报错。

# [可选] 首页欢迎图
HOME_IMAGE_FILE_ID = ""  # <<< 可选替换
HOME_IMAGE_URL = ""

# [必须建议设置] 点击首页「开始验证」显示：VIP特权说明图
VIP_PRIVILEGE_IMAGE_FILE_ID = ""  # <<< 在这里替换：VIP特权说明页图片 file_id
VIP_PRIVILEGE_IMAGE_URL = ""      # <<< 或者填图片直链URL

# [必须建议设置] 点击「我已付款，开始验证」显示：验证教程图
VIP_TUTORIAL_IMAGE_FILE_ID = ""   # <<< 在这里替换：VIP验证教程页图片 file_id
VIP_TUTORIAL_IMAGE_URL = ""       # <<< 或者填图片直链URL

# [必须建议设置] 输入订单号页面图片（失败重试会自动回到此页）
VIP_INPUT_IMAGE_FILE_ID = ""      # <<< 在这里替换：VIP订单输入页图片 file_id
VIP_INPUT_IMAGE_URL = ""          # <<< 或者填图片直链URL

# [可选] 积分中心页图
POINTS_CENTER_IMAGE_FILE_ID = ""  # <<< 可选替换
POINTS_CENTER_IMAGE_URL = ""

# [充值相关图片：建议设置]
WECHAT_PAY_IMAGE_FILE_ID = ""     # <<< 在这里替换：微信充值页图片 file_id
WECHAT_PAY_IMAGE_URL = ""
ALIPAY_PAY_IMAGE_FILE_ID = ""     # <<< 在这里替换：支付宝充值页图片 file_id
ALIPAY_PAY_IMAGE_URL = ""

# [充值输入页图片：必须建议设置（你要求输入页带自定义图）]
WECHAT_INPUT_IMAGE_FILE_ID = ""   # <<< 在这里替换：微信-请输入交易单号页图片 file_id
WECHAT_INPUT_IMAGE_URL = ""
ALIPAY_INPUT_IMAGE_FILE_ID = ""   # <<< 在这里替换：支付宝-请输入商家订单号页图片 file_id
ALIPAY_INPUT_IMAGE_URL = ""

# [可选] 点击“我已付款”后额外展示的图片
WECHAT_PAID_CLICK_IMAGE_FILE_ID = ""  # <<< 可选替换
WECHAT_PAID_CLICK_IMAGE_URL = ""
ALIPAY_PAID_CLICK_IMAGE_FILE_ID = ""  # <<< 可选替换
ALIPAY_PAID_CLICK_IMAGE_URL = ""

# ============ 内部识别规则（文案不出现这些前缀提示） ============
VIP_ORDER_PREFIX = "20260"
WECHAT_ORDER_PREFIX = "4200"
ALIPAY_ORDER_PREFIX = "4768"

# VIP订单验证：每人最多输入2次，然后锁15小时
VIP_MAX_TRIES = 2
VIP_COOLDOWN_SECONDS = 15 * 60 * 60

# 三天排行榜窗口
RANK_WINDOW_SECONDS = 3 * 24 * 60 * 60

# =========================================================

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ==========================
# 内存数据（Railway重启会清空）
# ==========================
user_state = {}   # {user_id: {"mode": "...", ...}}

# VIP验证限次
vip_attempts = {}  # {user_id: {"count":int, "locked_until":ts}}

# 积分
user_points = {}       # {user_id: int}
user_signin_day = {}   # {user_id: "YYYYMMDD"}

# 充值限制（每渠道一次）
user_recharge_used = {}  # {user_id: {"wechat": bool, "alipay": bool}}

# 积分账本（余额记录）
points_ledger = {}  # {user_id: [{"ts":int,"delta":int,"reason":str}, ...]}

# 三天排行事件（近3天净变化）
rank_events = []  # [{"ts":int,"user_id":int,"delta":int}]

# 兑换礼品（管理员可添加），默认放一个 0积分测试礼品
redeem_goods = {
    "TEST0": {"name": "测试礼品", "cost": 0, "type": "text", "content": "哈哈哈", "active": True}
}

# 兑换确认中的临时状态
redeem_pending = {}  # {user_id: {"gid": "TEST0"}}

# 已购买记录：兑换成功后显示“已购买”，再点直接发内容
user_purchased = {}  # {user_id: set([gid, ...])}

# ==========================
# Mode
# ==========================
MODE_WAIT_VIP_ORDER = "wait_vip_order"
MODE_WAIT_WECHAT_ORDER = "wait_wechat_order"
MODE_WAIT_ALIPAY_ORDER = "wait_alipay_order"

MODE_ADMIN_REDEEM_WAIT_ID = "admin_redeem_wait_id"
MODE_ADMIN_REDEEM_WAIT_NAME = "admin_redeem_wait_name"
MODE_ADMIN_REDEEM_WAIT_COST = "admin_redeem_wait_cost"
MODE_ADMIN_REDEEM_WAIT_TEXT = "admin_redeem_wait_text"
MODE_ADMIN_REDEEM_WAIT_PHOTO = "admin_redeem_wait_photo"
MODE_ADMIN_REDEEM_WAIT_VIDEO = "admin_redeem_wait_video"


# ==========================
# 工具函数
# ==========================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def now_ts() -> int:
    return int(time.time())


def today_key() -> str:
    t = time.localtime()
    return f"{t.tm_year:04d}{t.tm_mon:02d}{t.tm_mday:02d}"


def send_photo_or_text(chat_id: int, file_id: str, url: str, caption: str, reply_markup=None):
    file_id = (file_id or "").strip()
    url = (url or "").strip()
    if file_id:
        bot.send_photo(chat_id, photo=file_id, caption=caption, reply_markup=reply_markup)
        return
    if url:
        bot.send_photo(chat_id, photo=url, caption=caption, reply_markup=reply_markup)
        return
    bot.send_message(chat_id, caption, reply_markup=reply_markup)


def ensure_recharge_flags(user_id: int):
    if user_id not in user_recharge_used:
        user_recharge_used[user_id] = {"wechat": False, "alipay": False}
    else:
        user_recharge_used[user_id].setdefault("wechat", False)
        user_recharge_used[user_id].setdefault("alipay", False)


def add_points(user_id: int, amount: int):
    user_points[user_id] = int(user_points.get(user_id, 0)) + int(amount)


def get_points(user_id: int) -> int:
    return int(user_points.get(user_id, 0))


def ledger_add(user_id: int, delta: int, reason: str):
    points_ledger.setdefault(user_id, [])
    points_ledger[user_id].append({"ts": now_ts(), "delta": int(delta), "reason": str(reason)})


def ledger_last(user_id: int, limit: int = 12):
    return points_ledger.get(user_id, [])[-limit:]


def rank_add_event(user_id: int, delta: int):
    rank_events.append({"ts": now_ts(), "user_id": int(user_id), "delta": int(delta)})


def rank_cleanup():
    cutoff = now_ts() - RANK_WINDOW_SECONDS
    global rank_events
    rank_events = [e for e in rank_events if e["ts"] >= cutoff]


# ==========================
# VIP限次/锁定
# ==========================
def vip_is_locked(user_id: int):
    info = vip_attempts.get(user_id)
    if not info:
        return False, 0
    locked_until = info.get("locked_until", 0)
    if locked_until and now_ts() < locked_until:
        return True, locked_until
    return False, 0


def vip_bump_attempt(user_id: int):
    info = vip_attempts.setdefault(user_id, {"count": 0, "locked_until": 0})
    info["count"] += 1
    if info["count"] >= VIP_MAX_TRIES:
        info["locked_until"] = now_ts() + VIP_COOLDOWN_SECONDS


def vip_reset_attempts(user_id: int):
    vip_attempts[user_id] = {"count": 0, "locked_until": 0}


# ==========================
# 键盘
# ==========================
def home_kb(user_id: int):
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("🚀 开始验证", callback_data="vip_privilege"))
    kb.row(InlineKeyboardButton("🎯 积分中心", callback_data="points_center"))
    return kb


def points_center_kb(user_id: int):
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("🗓️ 签到", callback_data="points_signin"),
        InlineKeyboardButton("💳 充值", callback_data="points_recharge"),
    )
    kb.row(
        InlineKeyboardButton("🎁 兑换", callback_data="points_redeem"),
        InlineKeyboardButton("💰 积分余额", callback_data="points_balance"),
    )
    kb.row(InlineKeyboardButton("🏆 三天排行", callback_data="points_rank"))
    kb.row(InlineKeyboardButton("⬅️ 返回首页", callback_data="back_home"))
    return kb


def recharge_choose_kb():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("🟩 微信充值", callback_data="recharge_wechat"),
        InlineKeyboardButton("🟦 支付宝充值", callback_data="recharge_alipay"),
    )
    kb.row(InlineKeyboardButton("⬅️ 返回积分中心", callback_data="points_center"))
    return kb


def recharge_page_kb(channel: str):
    kb = InlineKeyboardMarkup()
    if channel == "wechat":
        kb.row(InlineKeyboardButton("✅ 我已付款｜提交订单号", callback_data="wechat_paid"))
    else:
        kb.row(InlineKeyboardButton("✅ 我已付款｜提交订单号", callback_data="alipay_paid"))
    kb.row(InlineKeyboardButton("⬅️ 返回充值方式", callback_data="points_recharge"))
    return kb


def redeem_confirm_kb(gid: str):
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("✅ 确认兑换", callback_data=f"redeem_confirm|{gid}"),
        InlineKeyboardButton("❌ 取消", callback_data="redeem_cancel"),
    )
    kb.row(InlineKeyboardButton("⬅️ 返回兑换列表", callback_data="points_redeem"))
    return kb


def redeem_list_kb(user_id: int):
    kb = InlineKeyboardMarkup()
    active_goods = [(gid, g) for gid, g in redeem_goods.items() if g.get("active")]

    purchased = user_purchased.get(user_id, set())

    if not active_goods:
        kb.row(InlineKeyboardButton("（暂无可兑换礼品）", callback_data="noop"))
    else:
        for gid, g in active_goods[:50]:
            if gid in purchased:
                label = f"🎁 {g.get('name','礼品')}｜已购买"
            else:
                label = f"🎁 {g.get('name','礼品')}｜{int(g.get('cost',0))}积分"
            kb.row(InlineKeyboardButton(label, callback_data=f"redeem_choose|{gid}"))

    kb.row(InlineKeyboardButton("⬆️ 上传/添加兑换礼品（仅管理员）", callback_data="redeem_admin_add"))
    kb.row(
        InlineKeyboardButton("⬅️ 返回积分中心", callback_data="points_center"),
        InlineKeyboardButton("🏠 返回首页", callback_data="back_home"),
    )
    return kb


def admin_redeem_type_kb():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("📝 文本内容", callback_data="admin_redeem_type_text"),
        InlineKeyboardButton("🖼 图片内容", callback_data="admin_redeem_type_photo"),
        InlineKeyboardButton("🎬 视频内容", callback_data="admin_redeem_type_video"),
    )
    kb.row(InlineKeyboardButton("⬅️ 返回兑换列表", callback_data="points_redeem"))
    return kb


# ==========================
# 页面函数：主页 / VIP输入页 / 充值输入页
# ==========================
def send_home(chat_id: int, user_id: int):
    text = (
        "👋 <b>欢迎来到【VIP中转】</b>\n"
        "我是守门员小卫，你的验证与积分助手。\n\n"
        "请选择操作："
    )
    send_photo_or_text(chat_id, HOME_IMAGE_FILE_ID, HOME_IMAGE_URL, text, home_kb(user_id))


def send_vip_input_page(chat_id: int):
    caption = (
        "🧾 <b>请输入订单号</b>\n\n"
        "<b>查找步骤（请按顺序打开）</b>\n"
        "1）进入「我的」\n"
        "2）点击「账单」\n"
        "3）打开对应记录进入「账单详情」\n"
        "4）点击「更多」\n"
        "5）找到「订单号」并 <b>全部复制</b>\n\n"
        "📌 请直接粘贴发送：不要手动输入、不要加空格。"
    )
    send_photo_or_text(chat_id, VIP_INPUT_IMAGE_FILE_ID, VIP_INPUT_IMAGE_URL, caption)
    bot.send_message(chat_id, "请直接粘贴订单号：")


def prompt_wechat_order_input(chat_id: int):
    send_photo_or_text(
        chat_id=chat_id,
        file_id=WECHAT_INPUT_IMAGE_FILE_ID,
        url=WECHAT_INPUT_IMAGE_URL,
        caption=(
            "🧾 <b>请发送：交易单号</b>\n\n"
            "<b>如何准确找到交易单号（微信）</b>\n"
            "1）微信 →「我」→「服务/支付」\n"
            "2）进入「钱包」→「账单」\n"
            "3）找到本次付款 → 点进详情\n"
            "4）找到「交易单号」→ 复制\n\n"
            "📌 请直接粘贴发送，不要手动输入。"
        )
    )
    bot.send_message(chat_id, "请直接粘贴交易单号：")


def prompt_alipay_order_input(chat_id: int):
    send_photo_or_text(
        chat_id=chat_id,
        file_id=ALIPAY_INPUT_IMAGE_FILE_ID,
        url=ALIPAY_INPUT_IMAGE_URL,
        caption=(
            "🧾 <b>请发送：商家订单号</b>\n\n"
            "<b>如何准确找到商家订单号（支付宝）</b>\n"
            "1）支付宝 →「我的」→「账单」\n"
            "2）找到本次付款 → 进入账单详情\n"
            "3）点击「更多/…」\n"
            "4）找到「商家订单号」→ 复制\n\n"
            "📌 请完整复制后粘贴发送。"
        )
    )
    bot.send_message(chat_id, "请直接粘贴商家订单号：")


# ==========================
# /start（保留但不依赖，用户发任何消息也能回主页）
# ==========================
@bot.message_handler(commands=["start"])
def on_start(message):
    user_state[message.from_user.id] = {"mode": None}
    send_home(message.chat.id, message.from_user.id)


# ==========================
# 文本消息：按当前 mode 处理；否则回主页
# ==========================
@bot.message_handler(func=lambda m: True, content_types=["text"])
def on_text(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    text = (message.text or "").strip()
    mode = user_state.get(user_id, {}).get("mode")

    # ===== 管理员：上架兑换礼品（文本流程）=====
    if mode == MODE_ADMIN_REDEEM_WAIT_ID:
        if not is_admin(user_id):
            user_state[user_id] = {"mode": None}
            send_home(chat_id, user_id)
            return
        gid = text
        user_state[user_id] = {"mode": MODE_ADMIN_REDEEM_WAIT_NAME, "redeem_gid": gid}
        bot.send_message(chat_id, f"✅ 礼品ID：<b>{gid}</b>\n请发送礼品名称：")
        return

    if mode == MODE_ADMIN_REDEEM_WAIT_NAME:
        if not is_admin(user_id):
            user_state[user_id] = {"mode": None}
            send_home(chat_id, user_id)
            return
        user_state[user_id]["redeem_name"] = text
        user_state[user_id]["mode"] = MODE_ADMIN_REDEEM_WAIT_COST
        bot.send_message(chat_id, "请发送所需积分（纯数字，例如 0 / 10 / 100）：")
        return

    if mode == MODE_ADMIN_REDEEM_WAIT_COST:
        if not is_admin(user_id):
            user_state[user_id] = {"mode": None}
            send_home(chat_id, user_id)
            return
        if not text.isdigit():
            bot.send_message(chat_id, "积分必须是纯数字，请重新发送：")
            return
        user_state[user_id]["redeem_cost"] = int(text)
        bot.send_message(chat_id, "请选择兑换内容类型：", reply_markup=admin_redeem_type_kb())
        return

    if mode == MODE_ADMIN_REDEEM_WAIT_TEXT:
        if not is_admin(user_id):
            user_state[user_id] = {"mode": None}
            send_home(chat_id, user_id)
            return
        gid = user_state[user_id].get("redeem_gid")
        name = user_state[user_id].get("redeem_name", "礼品")
        cost = int(user_state[user_id].get("redeem_cost", 0))
        redeem_goods[gid] = {"name": name, "cost": cost, "type": "text", "content": text, "active": True}
        user_state[user_id] = {"mode": None}
        bot.send_message(chat_id, f"✅ 已上架兑换礼品：<b>{gid}</b>（{cost}积分）", reply_markup=redeem_list_kb(user_id))
        return

    # ===== VIP订单输入 =====
    if mode == MODE_WAIT_VIP_ORDER:
        locked, locked_until = vip_is_locked(user_id)
        if locked:
            hours = max(1, (locked_until - now_ts()) // 3600)
            user_state[user_id] = {"mode": None}
            bot.send_message(
                chat_id,
                f"⏳ 尝试次数过多，请在 <b>{hours} 小时</b>后再试。",
                reply_markup=home_kb(user_id)
            )
            return

        if text.isdigit() and text.startswith(VIP_ORDER_PREFIX):
            vip_reset_attempts(user_id)
            user_state[user_id] = {"mode": None}
            bot.send_message(
                chat_id,
                "✅ <b>订单验证成功。</b>\n\n点击下方按钮加入会员群：",
                reply_markup=InlineKeyboardMarkup().row(
                    InlineKeyboardButton("🎟 进入会员群", url=VIP_GROUP_LINK)
                )
            )
            return

        vip_bump_attempt(user_id)
        locked, locked_until = vip_is_locked(user_id)
        if locked:
            user_state[user_id] = {"mode": None}
            bot.send_message(
                chat_id,
                "❌ 未查询到订单信息，请稍后再试。",
                reply_markup=home_kb(user_id)
            )
            return

        bot.send_message(chat_id, "❌ 未查询到订单信息，请重试。")
        send_vip_input_page(chat_id)
        return

    # ===== 微信充值订单输入 =====
    if mode == MODE_WAIT_WECHAT_ORDER:
        ensure_recharge_flags(user_id)
        if user_recharge_used[user_id]["wechat"]:
            user_state[user_id] = {"mode": None}
            bot.send_message(chat_id, "ℹ️ 你已完成过一次微信充值，本渠道不可重复使用。", reply_markup=points_center_kb(user_id))
            return

        if text.isdigit() and text.startswith(WECHAT_ORDER_PREFIX):
            user_recharge_used[user_id]["wechat"] = True
            add_points(user_id, 100)
            ledger_add(user_id, 100, "微信充值")
            rank_add_event(user_id, 100)
            user_state[user_id] = {"mode": None}
            bot.send_message(
                chat_id,
                f"✅ <b>充值成功</b>\n已到账 <b>100</b> 积分。\n当前积分：<b>{get_points(user_id)}</b>\n\n"
                "━━━━━━━━━━━━━━━━\n"
                "⚠️ <b>重要提醒</b>\n"
                "✅ <b>微信充值仅可成功一次</b>\n"
                "✅ <b>请勿重复充值</b>\n"
                "━━━━━━━━━━━━━━━━",
                reply_markup=points_center_kb(user_id)
            )
            return

        bot.send_message(chat_id, "❌ 未查询到订单信息，请重试。")
        prompt_wechat_order_input(chat_id)
        return

    # ===== 支付宝充值订单输入 =====
    if mode == MODE_WAIT_ALIPAY_ORDER:
        ensure_recharge_flags(user_id)
        if user_recharge_used[user_id]["alipay"]:
            user_state[user_id] = {"mode": None}
            bot.send_message(chat_id, "ℹ️ 你已完成过一次支付宝充值，本渠道不可重复使用。", reply_markup=points_center_kb(user_id))
            return

        if text.isdigit() and text.startswith(ALIPAY_ORDER_PREFIX):
            user_recharge_used[user_id]["alipay"] = True
            add_points(user_id, 100)
            ledger_add(user_id, 100, "支付宝充值")
            rank_add_event(user_id, 100)
            user_state[user_id] = {"mode": None}
            bot.send_message(
                chat_id,
                f"✅ <b>充值成功</b>\n已到账 <b>100</b> 积分。\n当前积分：<b>{get_points(user_id)}</b>\n\n"
                "━━━━━━━━━━━━━━━━\n"
                "⚠️ <b>重要提醒</b>\n"
                "✅ <b>支付宝充值仅可成功一次</b>\n"
                "✅ <b>请勿重复充值</b>\n"
                "━━━━━━━━━━━━━━━━",
                reply_markup=points_center_kb(user_id)
            )
            return

        bot.send_message(chat_id, "❌ 未查询到订单信息，请重试。")
        prompt_alipay_order_input(chat_id)
        return

    # 默认：不依赖命令，任何消息都回主页
    user_state[user_id] = {"mode": None}
    send_home(chat_id, user_id)


# ==========================
# 图片/视频：管理员上架兑换礼品（内容）
# ==========================
@bot.message_handler(content_types=["photo"])
def on_photo(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    mode = user_state.get(user_id, {}).get("mode")

    if mode == MODE_ADMIN_REDEEM_WAIT_PHOTO and is_admin(user_id):
        gid = user_state[user_id].get("redeem_gid")
        name = user_state[user_id].get("redeem_name", "礼品")
        cost = int(user_state[user_id].get("redeem_cost", 0))
        file_id = message.photo[-1].file_id
        redeem_goods[gid] = {"name": name, "cost": cost, "type": "photo", "content": file_id, "active": True}
        user_state[user_id] = {"mode": None}
        bot.send_message(chat_id, f"✅ 已上架兑换礼品：<b>{gid}</b>（图片 / {cost}积分）", reply_markup=redeem_list_kb(user_id))


@bot.message_handler(content_types=["video"])
def on_video(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    mode = user_state.get(user_id, {}).get("mode")

    if mode == MODE_ADMIN_REDEEM_WAIT_VIDEO and is_admin(user_id):
        gid = user_state[user_id].get("redeem_gid")
        name = user_state[user_id].get("redeem_name", "礼品")
        cost = int(user_state[user_id].get("redeem_cost", 0))
        file_id = message.video.file_id
        redeem_goods[gid] = {"name": name, "cost": cost, "type": "video", "content": file_id, "active": True}
        user_state[user_id] = {"mode": None}
        bot.send_message(chat_id, f"✅ 已上架兑换礼品：<b>{gid}</b>（视频 / {cost}积分）", reply_markup=redeem_list_kb(user_id))


# ==========================
# 回调按钮
# ==========================
@bot.callback_query_handler(func=lambda c: True)
def on_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    data = call.data

    if data == "noop":
        bot.answer_callback_query(call.id)
        return

    if data == "back_home":
        bot.answer_callback_query(call.id)
        user_state[user_id] = {"mode": None}
        send_home(chat_id, user_id)
        return

    # ====== 开始验证：VIP特权说明（发图）+ 我已付款按钮 ======
    if data == "vip_privilege":
        bot.answer_callback_query(call.id)
        caption = (
            "💎 <b>VIP 会员特权说明</b>\n"
            "✅ 专属中转通道\n"
            "✅ 优先审核入群\n"
            "✅ 7x24 小时客服支持\n"
            "✅ 定期福利活动\n\n"
            "完成支付后，请点击下方按钮开始验证。"
        )
        kb = InlineKeyboardMarkup()
        kb.row(InlineKeyboardButton("✅ 我已付款，开始验证", callback_data="vip_paid_start"))
        kb.row(InlineKeyboardButton("💬 私信管理员", url=ADMIN_CONTACT_LINK))
        kb.row(InlineKeyboardButton("⬅️ 返回首页", callback_data="back_home"))
        send_photo_or_text(chat_id, VIP_PRIVILEGE_IMAGE_FILE_ID, VIP_PRIVILEGE_IMAGE_URL, caption, kb)
        return

    # ====== 我已付款：教程（发图）+ 进入输入订单号模式 ======
    if data == "vip_paid_start":
        bot.answer_callback_query(call.id)
        caption = (
            "✅ <b>开始验证</b>\n\n"
            "请按以下路径找到订单号并完整复制：\n"
            "「我的」→「账单」→「账单详情」→「更多」→「订单号」（全部复制）\n\n"
            "接下来请粘贴发送订单号。"
        )
        send_photo_or_text(chat_id, VIP_TUTORIAL_IMAGE_FILE_ID, VIP_TUTORIAL_IMAGE_URL, caption,
                           InlineKeyboardMarkup().row(InlineKeyboardButton("⬅️ 返回首页", callback_data="back_home")))
        user_state[user_id] = {"mode": MODE_WAIT_VIP_ORDER}
        send_vip_input_page(chat_id)
        return

    # ====== 积分中心 ======
    if data == "points_center":
        bot.answer_callback_query(call.id)
        ensure_recharge_flags(user_id)
        caption = f"🎯 <b>积分中心</b>\n\n当前积分：<b>{get_points(user_id)}</b>"
        send_photo_or_text(chat_id, POINTS_CENTER_IMAGE_FILE_ID, POINTS_CENTER_IMAGE_URL, caption,
                           points_center_kb(user_id))
        return

    # ====== 签到 ======
    if data == "points_signin":
        bot.answer_callback_query(call.id)
        tk = today_key()
        if user_signin_day.get(user_id) == tk:
            bot.send_message(chat_id, f"🗓️ 今天已签到。\n当前积分：<b>{get_points(user_id)}</b>",
                             reply_markup=points_center_kb(user_id))
            return
        gained = random.randint(3, 8)
        user_signin_day[user_id] = tk
        add_points(user_id, gained)
        ledger_add(user_id, gained, "每日签到")
        rank_add_event(user_id, gained)
        bot.send_message(chat_id, f"✅ <b>签到成功</b>\n获得 <b>{gained}</b> 积分。\n当前积分：<b>{get_points(user_id)}</b>",
                         reply_markup=points_center_kb(user_id))
        return

    # ====== 充值入口 ======
    if data == "points_recharge":
        bot.answer_callback_query(call.id)
        ensure_recharge_flags(user_id)
        info = user_recharge_used[user_id]
        bot.send_message(
            chat_id,
            "💳 <b>充值积分</b>\n\n"
            "━━━━━━━━━━━━━━━━\n"
            "⚠️ <b>温馨提示（重要）</b>\n"
            "✅ <b>微信充值：每位用户仅可成功一次</b>\n"
            "✅ <b>支付宝充值：每位用户仅可成功一次</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            f"当前状态：微信 {'已使用' if info['wechat'] else '未使用'} ｜ 支付宝 {'已使用' if info['alipay'] else '未使用'}",
            reply_markup=recharge_choose_kb()
        )
        return

    # ====== 微信充值页 ======
    if data == "recharge_wechat":
        bot.answer_callback_query(call.id)
        ensure_recharge_flags(user_id)
        if user_recharge_used[user_id]["wechat"]:
            bot.send_message(chat_id, "ℹ️ 你已完成过一次微信充值，本渠道不可重复使用。", reply_markup=recharge_choose_kb())
            return
        caption = (
            "🟩 <b>微信充值</b>\n\n"
            "请先完成支付，然后点击下方按钮提交订单编号。\n\n"
            "━━━━━━━━━━━━━━━━\n"
            "⚠️ <b>温馨提示（重要）</b>\n"
            "✅ <b>微信充值：每位用户仅可成功一次</b>\n"
            "✅ <b>请勿重复充值/多次提交</b>\n"
            "━━━━━━━━━━━━━━━━"
        )
        send_photo_or_text(chat_id, WECHAT_PAY_IMAGE_FILE_ID, WECHAT_PAY_IMAGE_URL, caption, recharge_page_kb("wechat"))
        return

    if data == "wechat_paid":
        bot.answer_callback_query(call.id)
        ensure_recharge_flags(user_id)
        if user_recharge_used[user_id]["wechat"]:
            bot.send_message(chat_id, "ℹ️ 你已完成过一次微信充值，本渠道不可重复使用。", reply_markup=points_center_kb(user_id))
            return
        send_photo_or_text(chat_id, WECHAT_PAID_CLICK_IMAGE_FILE_ID, WECHAT_PAID_CLICK_IMAGE_URL,
                           "✅ <b>已收到你的提交请求</b>\n接下来请发送交易单号。")
        user_state[user_id] = {"mode": MODE_WAIT_WECHAT_ORDER}
        prompt_wechat_order_input(chat_id)
        return

    # ====== 支付宝充值页 ======
    if data == "recharge_alipay":
        bot.answer_callback_query(call.id)
        ensure_recharge_flags(user_id)
        if user_recharge_used[user_id]["alipay"]:
            bot.send_message(chat_id, "ℹ️ 你已完成过一次支付宝充值，本渠道不可重复使用。", reply_markup=recharge_choose_kb())
            return
        caption = (
            "🟦 <b>支付宝充值</b>\n\n"
            "请先完成支付，然后点击下方按钮提交订单编号。\n\n"
            "━━━━━━━━━━━━━━━━\n"
            "⚠️ <b>温馨提示（重要）</b>\n"
            "✅ <b>支付宝充值：每位用户仅可成功一次</b>\n"
            "✅ <b>请勿重复充值/多次提交</b>\n"
            "━━━━━━━━━━━━━━━━"
        )
        send_photo_or_text(chat_id, ALIPAY_PAY_IMAGE_FILE_ID, ALIPAY_PAY_IMAGE_URL, caption, recharge_page_kb("alipay"))
        return

    if data == "alipay_paid":
        bot.answer_callback_query(call.id)
        ensure_recharge_flags(user_id)
        if user_recharge_used[user_id]["alipay"]:
            bot.send_message(chat_id, "ℹ️ 你已完成过一次支付宝充值，本渠道不可重复使用。", reply_markup=points_center_kb(user_id))
            return
        send_photo_or_text(chat_id, ALIPAY_PAID_CLICK_IMAGE_FILE_ID, ALIPAY_PAID_CLICK_IMAGE_URL,
                           "✅ <b>已收到你的提交请求</b>\n接下来请发送商家订单号。")
        user_state[user_id] = {"mode": MODE_WAIT_ALIPAY_ORDER}
        prompt_alipay_order_input(chat_id)
        return

    # ====== 兑换入口 ======
    if data == "points_redeem":
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "🎁 <b>积分兑换</b>\n请选择礼品：", reply_markup=redeem_list_kb(user_id))
        return

    # 点击礼品：未购买先确认；已购买直接发内容
    if data.startswith("redeem_choose|"):
        bot.answer_callback_query(call.id)
        gid = data.split("|", 1)[1]
        g = redeem_goods.get(gid)

        if not g or not g.get("active"):
            bot.send_message(chat_id, "该礼品暂不可兑换。", reply_markup=redeem_list_kb(user_id))
            return

        purchased = user_purchased.get(user_id, set())
        if gid in purchased:
            ctype = g.get("type", "text")
            content = g.get("content", "")
            if ctype == "text":
                bot.send_message(chat_id, str(content), reply_markup=redeem_list_kb(user_id))
            elif ctype == "photo":
                bot.send_photo(chat_id, photo=content, caption="📦 已购买内容", reply_markup=redeem_list_kb(user_id))
            elif ctype == "video":
                bot.send_video(chat_id, video=content, caption="📦 已购买内容", reply_markup=redeem_list_kb(user_id))
            else:
                bot.send_message(chat_id, "内容格式错误，请联系管理员。", reply_markup=redeem_list_kb(user_id))
            return

        cost = int(g.get("cost", 0))
        redeem_pending[user_id] = {"gid": gid}
        bot.send_message(
            chat_id,
            "🧾 <b>确认兑换</b>\n\n"
            f"礼品：<b>{g.get('name','礼品')}</b>\n"
            f"所需积分：<b>{cost}</b>\n"
            f"当前积分：<b>{get_points(user_id)}</b>\n\n"
            "是否确认兑换？",
            reply_markup=redeem_confirm_kb(gid)
        )
        return

    if data == "redeem_cancel":
        bot.answer_callback_query(call.id)
        redeem_pending.pop(user_id, None)
        bot.send_message(chat_id, "已取消本次兑换。", reply_markup=redeem_list_kb(user_id))
        return

    if data.startswith("redeem_confirm|"):
        bot.answer_callback_query(call.id)
        gid = data.split("|", 1)[1]
        pending = redeem_pending.get(user_id)
        if not pending or pending.get("gid") != gid:
            bot.send_message(chat_id, "本次兑换已失效，请重新选择礼品。", reply_markup=redeem_list_kb(user_id))
            return

        g = redeem_goods.get(gid)
        if not g or not g.get("active"):
            redeem_pending.pop(user_id, None)
            bot.send_message(chat_id, "该礼品暂不可兑换。", reply_markup=redeem_list_kb(user_id))
            return

        purchased = user_purchased.get(user_id, set())
        if gid in purchased:
            redeem_pending.pop(user_id, None)
            # 已购买直接发内容
            ctype = g.get("type", "text")
            content = g.get("content", "")
            if ctype == "text":
                bot.send_message(chat_id, str(content), reply_markup=redeem_list_kb(user_id))
            elif ctype == "photo":
                bot.send_photo(chat_id, photo=content, caption="📦 已购买内容", reply_markup=redeem_list_kb(user_id))
            elif ctype == "video":
                bot.send_video(chat_id, video=content, caption="📦 已购买内容", reply_markup=redeem_list_kb(user_id))
            else:
                bot.send_message(chat_id, "内容格式错误，请联系管理员。", reply_markup=redeem_list_kb(user_id))
            return

        cost = int(g.get("cost", 0))
        if get_points(user_id) < cost:
            redeem_pending.pop(user_id, None)
            bot.send_message(chat_id, "❌ 余额不足。", reply_markup=redeem_list_kb(user_id))
            return

        if cost > 0:
            add_points(user_id, -cost)
            ledger_add(user_id, -cost, f"兑换：{g.get('name','礼品')}（{gid}）")
            rank_add_event(user_id, -cost)

        user_purchased.setdefault(user_id, set()).add(gid)
        redeem_pending.pop(user_id, None)

        bot.send_message(chat_id, "✅ <b>兑换成功</b>\n正在为你发送兑换内容…")

        ctype = g.get("type", "text")
        content = g.get("content", "")
        if ctype == "text":
            bot.send_message(chat_id, str(content), reply_markup=redeem_list_kb(user_id))
        elif ctype == "photo":
            bot.send_photo(chat_id, photo=content, caption="🎁 兑换内容已送达", reply_markup=redeem_list_kb(user_id))
        elif ctype == "video":
            bot.send_video(chat_id, video=content, caption="🎁 兑换内容已送达", reply_markup=redeem_list_kb(user_id))
        else:
            bot.send_message(chat_id, "兑换内容格式错误，请联系管理员。", reply_markup=redeem_list_kb(user_id))
        return

    # ====== 兑换礼品：管理员添加入口 ======
    if data == "redeem_admin_add":
        bot.answer_callback_query(call.id)
        if not is_admin(user_id):
            bot.send_message(chat_id, "⛔ 无权限。该功能仅管理员可用。", reply_markup=redeem_list_kb(user_id))
            return
        user_state[user_id] = {"mode": MODE_ADMIN_REDEEM_WAIT_ID}
        bot.send_message(chat_id, "⬆️ <b>添加兑换礼品</b>\n\n请发送礼品编号（ID）：")
        return

    if data == "admin_redeem_type_text":
        bot.answer_callback_query(call.id)
        if not is_admin(user_id):
            bot.send_message(chat_id, "⛔ 无权限。")
            return
        user_state[user_id]["mode"] = MODE_ADMIN_REDEEM_WAIT_TEXT
        bot.send_message(chat_id, "📝 请发送兑换后要发给用户的文本内容：")
        return

    if data == "admin_redeem_type_photo":
        bot.answer_callback_query(call.id)
        if not is_admin(user_id):
            bot.send_message(chat_id, "⛔ 无权限。")
            return
        user_state[user_id]["mode"] = MODE_ADMIN_REDEEM_WAIT_PHOTO
        bot.send_message(chat_id, "🖼 请发送兑换后要发给用户的图片：")
        return

    if data == "admin_redeem_type_video":
        bot.answer_callback_query(call.id)
        if not is_admin(user_id):
            bot.send_message(chat_id, "⛔ 无权限。")
            return
        user_state[user_id]["mode"] = MODE_ADMIN_REDEEM_WAIT_VIDEO
        bot.send_message(chat_id, "🎬 请发送兑换后要发给用户的视频：")
        return

    # ====== 积分余额 ======
    if data == "points_balance":
        bot.answer_callback_query(call.id)
        items = ledger_last(user_id, limit=12)
        if items:
            lines = []
            for it in items[::-1]:
                t = time.strftime("%m-%d %H:%M", time.localtime(it["ts"]))
                d = it["delta"]
                sign = "+" if d > 0 else ""
                lines.append(f"• <b>{t}</b>  {sign}{d}  {it['reason']}")
            history = "\n".join(lines)
        else:
            history = "（暂无记录）"

        bot.send_message(
            chat_id,
            "💰 <b>积分余额</b>\n\n"
            f"当前积分：<b>{get_points(user_id)}</b>\n\n"
            "📒 <b>最近记录</b>\n"
            f"{history}",
            reply_markup=points_center_kb(user_id)
        )
        return

    # ====== 三天排行 ======
    if data == "points_rank":
        bot.answer_callback_query(call.id)
        rank_cleanup()

        net = {}
        for e in rank_events:
            uid = e["user_id"]
            net[uid] = net.get(uid, 0) + e["delta"]

        sorted_list = sorted(net.items(), key=lambda x: x[1], reverse=True)

        top_lines = []
        for i, (uid, score) in enumerate(sorted_list[:10], start=1):
            top_lines.append(f"{i}. <code>{uid}</code>  <b>{score}</b>")

        my_rank = None
        for i, (uid, score) in enumerate(sorted_list, start=1):
            if uid == user_id:
                my_rank = (i, score)
                break

        top_text = "\n".join(top_lines) if top_lines else "（近三天暂无排行数据）"
        my_text = f"你当前排名：<b>第 {my_rank[0]} 名</b>（净变化 <b>{my_rank[1]}</b>）" if my_rank else "你当前排名：未上榜（近三天暂无积分变动记录）"

        bot.send_message(
            chat_id,
            "🏆 <b>三天积分排行榜</b>\n\n"
            "Top 榜：\n"
            f"{top_text}\n\n"
            f"{my_text}",
            reply_markup=points_center_kb(user_id)
        )
        return

    bot.answer_callback_query(call.id)


if __name__ == "__main__":
    bot.infinity_polling(timeout=60, long_polling_timeout=60)
