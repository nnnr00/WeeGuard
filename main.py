# main.py
import os
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes
)

load_dotenv()

# === 图片链接（请替换成你自己的直链）===
VIP_IMAGE_URL = "https://i.postimg.cc/QtkVBw7N/photo-2026-01-13-17-04-27.jpg"
ORDER_GUIDE_IMAGE_URL = "https://i.postimg.cc/QtkVBw7N/photo-2026-01-13-17-04-27.jpg"

# === 文本内容 ===
WELCOME_MESSAGE = """欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~

小卫小卫，守门员小卫！
- 一键入群，小卫帮你搞定！
- 新人来报到，小卫查身份！"""

SERVICE_TEXT = "请选择您需要的服务："

VIP_CAPTION = """VIP会员特权说明：
- 专属中转通道
- 优先审核入群
- 7×24小时专属客服
- 定期空投福利 & 活动

立即享有以上全部权益，请私信管理员开通：
@YourAdminUsername"""  # ← 记得改成你的管理员用户名

PAYMENT_DONE_TEXT = "付款成功后，请点击下方按钮开始身份验证"

ORDER_GUIDE_CAPTION = """1️⃣ 发送你的订单号
订单号在 我的 → 账单 → 账单详情 → 更多 → 订单号（全部复制）

2️⃣ 审核通过后自动拉你入群
审核通常 1-5 分钟完成

请直接发送订单编号："""

SUCCESS_TEXT = "验证成功！欢迎成为VIP会员"
SUCCESS_BUTTON_TEXT = "立即加入专属群"
GROUP_LINK = "https://t.me/+495j5rWmApsxYzg9"  # ← 改成你的真实群链接

FAIL_TEXT = "订单识别失败，请检查是否复制完整并重试"

# === /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_MESSAGE)

# === /a 命令（入口）===
async def command_a(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("点此开通VIP会员", callback_data="show_vip")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(SERVICE_TEXT, reply_markup=reply_markup)

# === 按钮点击处理 ===
async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "show_vip":
        # 1. VIP介绍图文
        await query.message.reply_photo(photo=VIP_IMAGE_URL, caption=VIP_CAPTION)

        # 2. 已付款按钮（加钻石符号）
        keyboard = [[InlineKeyboardButton("我已付款，开始验证", callback_data="start_order_verify")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(PAYMENT_DONE_TEXT, reply_markup=reply_markup)

    elif query.data == "start_order_verify":
        # 3. 发送订单指引图文并记录消息ID
        sent = await query.message.reply_photo(
            photo=ORDER_GUIDE_IMAGE_URL,
            caption=ORDER_GUIDE_CAPTION
        )
        context.user_data['order_guide_msg_id'] = sent.message_id
        context.user_data['awaiting'] = 'order_id'

# === 重新显示订单指引（验证失败时调用）===
async def resend_order_guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.edit_message_caption(
            chat_id=update.effective_chat.id,
            message_id=context.user_data['order_guide_msg_id'],
            caption=ORDER_GUIDE_CAPTION
        )
    except:
        # 如果原消息被删或编辑失败，重新发
        sent = await update.message.reply_photo(
            photo=ORDER_GUIDE_IMAGE_URL,
            caption=ORDER_GUIDE_CAPTION
        )
        context.user_data['order_guide_msg_id'] = sent.message_id
    context.user_data['awaiting'] = 'order_id'

# === 处理用户发送的订单号 ===
async def handle_order_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('awaiting') != 'order_id':
        return

    order_id = update.message.text.strip()
    context.user_data['awaiting'] = None

    if order_id.startswith("20260"):
        # 成功
        keyboard = [[InlineKeyboardButton(SUCCESS_BUTTON_TEXT, url=GROUP_LINK)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(SUCCESS_TEXT, reply_markup=reply_markup)
    else:
        # 失败 → 提示 + 重新显示指引
        await update.message.reply_text(FAIL_TEXT)
        await resend_order_guide(update, context)

# === 主函数 ===
def main():
    print("正在启动【守门员小卫】机器人...")

    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        print("\n" + "="*60)
        print("错误：未检测到 TELEGRAM_BOT_TOKEN")
        print("请在 Railway / Render / VPS 等环境变量中添加：")
        print("TELEGRAM_BOT_TOKEN = 你的机器人Token")
        print("="*60 + "\n")
        return

    print(f"Token加载成功: {TOKEN[:8]}...{TOKEN[-4:]}")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("a", command_a))
    app.add_handler(CallbackQueryHandler(handle_button_click))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_order_id))

    print("守门员小卫已上线！正在监听消息...")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
