import os
import time
import random
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# =========================================================
# CONFIG（只改这里：我已精确标注你需要更改的地方）
# =========================================================

# Railway Variables 设置：BOT_TOKEN=xxxx（更安全，不写死）
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Please set it in Railway Variables.")

# 管理员 Telegram 数字ID（可多个）
ADMIN_IDS = {111111111}  # <<<【在这里替换】例如 {123456789}

# 会员群邀请链接（你指定）
VIP_GROUP_LINK = "https://t.me/+495j5rWmApsxYzg9"  # <<<【确认/可替换】

# 管理员私信链接（可选）
ADMIN_CONTACT_LINK = "https://t.me/"  # <<<【可选替换】https://t.me/你的username

# ========== 图片：支持 file_id 或 URL（二选一）==========
# 规则：file_id 优先；file_id 为空才会用 URL；都为空只发文字不报错

# ① 点击“开始验证”后显示：VIP特权页图片（自定义 file_id/URL）
VIP_PRIVILEGE_IMAGE_FILE_ID = ""  # <<<【在这里替换】VIP特权页图片 file_id
VIP_PRIVILEGE_IMAGE_URL = ""      # <<< 或填 URL（二选一）

# ② 点击“我已付款，开始验证”后显示：订单输入引导页图片（自定义 file_id/URL）
VIP_INPUT_IMAGE_FILE_ID = ""      # <<<【在这里替换】订单输入引导页图片 file_id
VIP_INPUT_IMAGE_URL = ""          # <<< 或填 URL（二选一）

# （可选）首页欢迎图
HOME_IMAGE_FILE_ID = ""  # <<<【可选替换】
HOME_IMAGE_URL = ""

# （可选）积分中心图
POINTS_CENTER_IMAGE_FILE_ID = ""  # <<<【可选替换】
POINTS_CENTER_IMAGE_URL = ""

# ========== 内部识别规则（文案不出现前缀提示）==========
VIP_ORDER_PREFIX = "20260"
VIP_MAX_TRIES = 2
VIP_COOLDOWN_SECONDS = 15 * 60 * 60  # 15小时

# =========================================================

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ==========================
# 内存数据（Railway重启会清空；如需SQLite持久化我可继续升级）
# ==========================
user_state = {}        # {user_id: {"mode": "..."}}
vip_attempts = {}      # {user_id: {"count":int,"locked_until":ts}}

user_points = {}       # {user_id: int}
user_signin_day = {}   # {user_id: "YYYYMMDD"}

redeem_goods = {
    "TEST0": {"name": "测试礼品", "cost": 0, "type": "text", "content": "哈哈哈", "active": True}
}
redeem_pending = {}    # {user_id: {"gid": "..."}}
user_purchased = {}    # {user_id: set([gid,...])}

MODE_WAIT_VIP_ORDER = "wait_vip_order"

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


def add_points(user_id: int, delta: int):
    user_points[user_id] = int(user_points.get(user_id, 0)) + int(delta)


def get_points(user_id: int) -> int:
    return int(user_points.get(user_id, 0))


# ==========================
# VIP 锁定逻辑（2次失败锁15小时）
# ==========================
def vip_is_locked(user_id: int):
    info = vip_attempts.get(user_id)
    if not info:
        return False, 0
    locked_until = int(info.get("locked_until", 0))
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
    # ✅ 首页只有两个按钮（按你要求）
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("🚀 开始验证", callback_data="vip_privilege"))
    kb.row(InlineKeyboardButton("🎯 积分中心", callback_data="points_center"))
    return kb


def points_center_kb(user_id: int):
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("🗓️ 签到", callback_data="points_signin"),
        InlineKeyboardButton("🎁 兑换", callback_data="points_redeem"),
    )
    kb.row(InlineKeyboardButton("⬅️ 返回首页", callback_data="back_home"))
    return kb


def redeem_list_kb(user_id: int):
    kb = InlineKeyboardMarkup()
    purchased = user_purchased.get(user_id, set())
    active_goods = [(gid, g) for gid, g in redeem_goods.items() if g.get("active")]

    if not active_goods:
        kb.row(InlineKeyboardButton("（暂无可兑换礼品）", callback_data="noop"))
    else:
        for gid, g in active_goods[:50]:
            # ✅ 兑换后：积分显示替换为“已购买”（按你要求）
            if gid in purchased:
                label = f"🎁 {g.get('name','礼品')}｜已购买"
                kb.row(InlineKeyboardButton(label, callback_data=f"redeem_reget|{gid}"))
            else:
                label = f"🎁 {g.get('name','礼品')}｜{int(g.get('cost',0))}积分"
                kb.row(InlineKeyboardButton(label, callback_data=f"redeem_choose|{gid}"))

    kb.row(InlineKeyboardButton("⬅️ 返回积分中心", callback_data="points_center"))
    kb.row(InlineKeyboardButton("🏠 返回首页", callback_data="back_home"))
    return kb


def redeem_confirm_kb(gid: str):
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("✅ 确认兑换", callback_data=f"redeem_confirm|{gid}"),
        InlineKeyboardButton("❌ 取消", callback_data="redeem_cancel"),
    )
    kb.row(InlineKeyboardButton("⬅️ 返回兑换列表", callback_data="points_redeem"))
    return kb


# ==========================
# 页面函数：主页/输入订单页/兑换内容发送
# ==========================
def send_home(chat_id: int, user_id: int):
    text = (
        "👋 <b>欢迎加入【VIP中转】</b>\n"
        "我是守门员小卫，你的身份验证小助手。\n\n"
        "请选择操作："
    )
    send_photo_or_text(chat_id, HOME_IMAGE_FILE_ID, HOME_IMAGE_URL, text, home_kb(user_id))


def send_vip_input_page(chat_id: int):
    # ✅ 点“我已付款，开始验证”后出现：图 + 教程 + 让用户输入订单号（按你要求）
    caption = (
        "🧾 <b>请输入订单号</b>\n\n"
        "<b>如何查找订单号（请按顺序打开）</b>\n"
        "1）进入「我的」\n"
        "2）点击「账单」\n"
        "3）打开对应记录进入「账单详情」\n"
        "4）点击「更多」\n"
        "5）找到「订单号」→ <b>全部复制</b>\n\n"
        "📌 请直接粘贴发送（不要手动输入、不要加空格）。"
    )
    send_photo_or_text(chat_id, VIP_INPUT_IMAGE_FILE_ID, VIP_INPUT_IMAGE_URL, caption)
    bot.send_message(chat_id, "请直接粘贴订单号：")


def send_redeem_content(chat_id: int, g: dict, reply_markup=None):
    ctype = g.get("type", "text")
    content = g.get("content", "")
    if ctype == "text":
        bot.send_message(chat_id, str(content), reply_markup=reply_markup)
    elif ctype == "photo":
        bot.send_photo(chat_id, photo=content, caption="🎁 已发送", reply_markup=reply_markup)
    elif ctype == "video":
        bot.send_video(chat_id, video=content, caption="🎁 已发送", reply_markup=reply_markup)
    else:
        bot.send_message(chat_id, "内容配置错误，请联系管理员。", reply_markup=reply_markup)


# ==========================
# /start（保留但不依赖）
# ==========================
@bot.message_handler(commands=["start"])
def on_start(message):
    user_state[message.from_user.id] = {"mode": None}
    send_home(message.chat.id, message.from_user.id)


# 任意文本：不在输入订单模式就回主页（实现“不依赖/命令”）
@bot.message_handler(func=lambda m: True, content_types=["text"])
def on_text(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    text = (message.text or "").strip()
    mode = user_state.get(user_id, {}).get("mode")

    # ===== 输入订单号模式 =====
    if mode == MODE_WAIT_VIP_ORDER:
        locked, locked_until = vip_is_locked(user_id)
        if locked:
            hours = max(1, (locked_until - now_ts()) // 3600)
            user_state[user_id] = {"mode": None}
            bot.send_message(chat_id, f"⏳ 尝试次数过多，请在 <b>{hours} 小时</b>后再试。", reply_markup=home_kb(user_id))
            return

        # 内部规则校验（文案不出现提示）
        ok = text.isdigit() and text.startswith(VIP_ORDER_PREFIX)

        if ok:
            vip_reset_attempts(user_id)
            user_state[user_id] = {"mode": None}
            kb = InlineKeyboardMarkup().row(InlineKeyboardButton("🎟 进入会员群", url=VIP_GROUP_LINK))
            bot.send_message(chat_id, "✅ <b>订单验证成功。</b>\n\n点击下方按钮加入会员群：", reply_markup=kb)
            return

        # 失败：计次 + 自动回输入页（带图+教程）
        vip_bump_attempt(user_id)
        locked, locked_until = vip_is_locked(user_id)
        if locked:
            user_state[user_id] = {"mode": None}
            bot.send_message(chat_id, "❌ 未查询到订单信息，请稍后再试。", reply_markup=home_kb(user_id))
            return

        bot.send_message(chat_id, "❌ 未查询到订单信息，请重试。")
        send_vip_input_page(chat_id)
        return

    # 其他文本：回主页
    user_state[user_id] = {"mode": None}
    send_home(chat_id, user_id)


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

    # ==========================
    # 验证流程（两步）
    # ==========================
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

        # ✅ 点开始验证：发自定义图片（file_id/url）+ 按钮（按你要求）
        send_photo_or_text(chat_id, VIP_PRIVILEGE_IMAGE_FILE_ID, VIP_PRIVILEGE_IMAGE_URL, caption, kb)
        return

    if data == "vip_paid_start":
        bot.answer_callback_query(call.id)
        # ✅ 点我已付款：发输入引导图+教程，并进入输入订单号模式（按你要求）
        user_state[user_id] = {"mode": MODE_WAIT_VIP_ORDER}
        send_vip_input_page(chat_id)
        return

    # ==========================
    # 积分中心
    # ==========================
    if data == "points_center":
        bot.answer_callback_query(call.id)
        caption = f"🎯 <b>积分中心</b>\n\n当前积分：<b>{get_points(user_id)}</b>"
        send_photo_or_text(chat_id, POINTS_CENTER_IMAGE_FILE_ID, POINTS_CENTER_IMAGE_URL, caption, points_center_kb(user_id))
        return

    if data == "points_signin":
        bot.answer_callback_query(call.id)
        tk = today_key()
        if user_signin_day.get(user_id) == tk:
            bot.send_message(chat_id, f"🗓️ 今天已签到。\n当前积分：<b>{get_points(user_id)}</b>", reply_markup=points_center_kb(user_id))
            return

        gained = random.randint(3, 8)
        user_signin_day[user_id] = tk
        add_points(user_id, gained)
        bot.send_message(chat_id, f"✅ <b>签到成功</b>\n获得 <b>{gained}</b> 积分。\n当前积分：<b>{get_points(user_id)}</b>", reply_markup=points_center_kb(user_id))
        return

    # ==========================
    # 兑换（确认 + 已购买显示 + 已购买直接取回）
    # ==========================
    if data == "points_redeem":
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "🎁 <b>积分兑换</b>\n请选择礼品：", reply_markup=redeem_list_kb(user_id))
        return

    # 未购买 -> 确认页
    if data.startswith("redeem_choose|"):
        bot.answer_callback_query(call.id)
        gid = data.split("|", 1)[1]
        g = redeem_goods.get(gid)
        if not g or not g.get("active"):
            bot.send_message(chat_id, "该礼品暂不可兑换。", reply_markup=redeem_list_kb(user_id))
            return

        cost = int(g.get("cost", 0))
        redeem_pending[user_id] = {"gid": gid}

        bot.send_message(
            chat_id,
            "🧾 <b>确认兑换</b>\n\n"
            f"礼品：<b>{g.get('name','礼品')}</b>\n"
            f"消耗：<b>{cost}</b>\n"
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

        # 防重复扣分：确认前若已购买
        purchased = user_purchased.get(user_id, set())
        if gid in purchased:
            redeem_pending.pop(user_id, None)
            send_redeem_content(chat_id, g, reply_markup=redeem_list_kb(user_id))
            return

        cost = int(g.get("cost", 0))
        if get_points(user_id) < cost:
            redeem_pending.pop(user_id, None)
            bot.send_message(chat_id, "❌ 余额不足。", reply_markup=redeem_list_kb(user_id))
            return

        if cost > 0:
            add_points(user_id, -cost)

        user_purchased.setdefault(user_id, set()).add(gid)
        redeem_pending.pop(user_id, None)

        bot.send_message(chat_id, "✅ <b>兑换成功</b>\n已为你发送内容：")
        send_redeem_content(chat_id, g, reply_markup=redeem_list_kb(user_id))
        return

    # 已购买 -> 直接发内容（不确认）
    if data.startswith("redeem_reget|"):
        bot.answer_callback_query(call.id)
        gid = data.split("|", 1)[1]
        g = redeem_goods.get(gid)
        if not g or not g.get("active"):
            bot.send_message(chat_id, "该礼品暂不可用。", reply_markup=redeem_list_kb(user_id))
            return

        purchased = user_purchased.get(user_id, set())
        if gid not in purchased:
            bot.send_message(chat_id, "该礼品尚未兑换。", reply_markup=redeem_list_kb(user_id))
            return

        send_redeem_content(chat_id, g, reply_markup=redeem_list_kb(user_id))
        return

    bot.answer_callback_query(call.id)


if __name__ == "__main__":
    bot.infinity_polling(timeout=60, long_polling_timeout=60)
