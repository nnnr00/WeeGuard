# main.py
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes
)

# === 图片占位链接（替换成你自己的 HTTPS 图片）===
VIP_IMAGE_URL = "https://via.placeholder.com/600x300.png?text=VIP+Membership"
ORDER_GUIDE_IMAGE_URL = "https://via.placeholder.com/600x300.png?text=How+to+Find+Order+ID"

# === /start 欢迎语 ===
WELCOME_MESSAGE = """👋 欢迎加入【VIP中转站】！我是守门员小卫，你的身份验证小助手~

📢 小卫小卫，守门员小卫！
- 一键入群，小卫帮你搞定！
- 新人来报到，小卫查身份！"""

# === /a 命令菜单 ===
SERVICE_TEXT = "请选择服务类型："

# === VIP 权益图文内容 ===
VIP_CAPTION = """💎 VIP会员特权说明：
✅ 专属中转通道
✅ 优先审核入群
✅ 7x24小时客服支持
✅ 定期福利活动

👉 请私信管理员"""

# === 付款后引导按钮消息 ===
PAYMENT_DONE_TEXT = "🎉 付款后请点击下方按钮开始验证"

# === 订单指引图文内容 ===
ORDER_GUIDE_CAPTION = """1️⃣ 发送你的订单号
订单号在 我的 - 账单 - 账单详情 - 更多 - 订单号  全部复制

2️⃣ 审核通过后自动入群
⏱️ 审核通常在1-5分钟内完成

➡️ 请直接发送账单订单编号："""

# === 成功/失败提示 ===
SUCCESS_TEXT = "🎉 验证成功！点击下方按钮加入群组 👇"
SUCCESS_BUTTON_TEXT = "🚀 立即入群"
GROUP_LINK = "https://t.me/+495j5rWmApsxYzg9"

FAIL_TEXT = "❌ 订单号无效，请确认是否以 20260 开头并重试。"

# === 处理 /a 命令：显示“点此加入VIP”按钮 ===
async def command_a(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("1️⃣ 点此加入VIP", callback_data="show_vip")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(SERVICE_TEXT, reply_markup=reply_markup)

# === 处理按钮点击事件 ===
async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "show_vip":
        # 第一条：发送VIP图文
        await query.message.reply_photo(photo=VIP_IMAGE_URL, caption=VIP_CAPTION)

        # 第二条：发送“付款后点击”按钮
        keyboard = [[InlineKeyboardButton("✅ 我已付款，开始验证", callback_data="start_order_verify")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(PAYMENT_DONE_TEXT, reply_markup=reply_markup)

    elif query.data == "start_order_verify":
        # 第三条：发送订单指引图文
        await query.message.reply_photo(photo=ORDER_GUIDE_IMAGE_URL, caption=ORDER_GUIDE_CAPTION)

        # 设置状态：等待用户发送订单号
        context.user_data['awaiting'] = 'order_id'

# === 处理用户发送的订单号 ===
async def handle_order_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('awaiting') != 'order_id':
        return  # 不在等待订单号阶段，忽略

    user_text = update.message.text.strip()
    context.user_data['awaiting'] = None  # 清除状态

    if user_text.startswith("20260"):
        # 验证成功 → 发送入群按钮
        keyboard = [[InlineKeyboardButton(SUCCESS_BUTTON_TEXT, url=GROUP_LINK)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(SUCCESS_TEXT, reply_markup=reply_markup)
    else:
        # 验证失败
        await update.message.reply_text(FAIL_TEXT)

# === /start 命令 ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_MESSAGE)

# === 主函数 ===
def main():
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        raise ValueError("请设置环境变量 TELEGRAM_BOT_TOKEN")

    application = Application.builder().token(TOKEN).build()

    # 注册处理器
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("a", command_a))
    application.add_handler(CallbackQueryHandler(handle_button_click))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_order_id))

    print("守门员小卫已上线 ✅ 正在等待用户...")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
