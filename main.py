import os
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from datetime import datetime, timedelta

load_dotenv()

VIP_IMAGE_URL = "https://i.postimg.cc/QtkVBw7N/photo-2026-01-13-17-04-27.jpg"
ORDER_GUIDE_IMAGE_URL = "https://i.postimg.cc/QtkVBw7N/photo-2026-01-13-17-04-27.jpg"

WELCOME_MESSAGE = """欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~

小卫小卫，守门员小卫！
一键入群，小卫帮你搞定！
新人来报到，小卫查身份！"""

SERVICE_TEXT = "请选择您需要的服务："

VIP_CAPTION = """VIP会员特权说明：
- 专属中转通道
- 优先审核入群
- 7x24小时客服支持
- 定期福利活动

请私信管理员开通"""

PAYMENT_DONE_TEXT = "付款成功后，请点击下方按钮开始身份验证"

ORDER_GUIDE_CAPTION = """1️⃣ 发送你的订单号
订单号在 我的 → 账单 → 账单详情 → 更多 → 订单号（全部复制）

2️⃣ 审核通过后自动拉你入群

请直接发送订单编号："""

SUCCESS_TEXT = "订单审核通过！\n\n恭喜获得VIP专属权限！\n请点击下方按钮进入中转群"

FAIL_TEXT = "订单获取失败 请重试（还剩 {} 次机会）"
BLOCK_MESSAGE = "您已连续输入错误2次，为防止恶意操作，已临时限制验证功能。\n\n请 15 小时后再次尝试。"

GROUP_LINK = "https://t.me/+495j5rWmApsxYzg9"
MAX_FAILS = 2
COOLDOWN_HOURS = 15

async def auto_start_and_a(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("welcome_sent"):
        return
    await update.message.reply_text(WELCOME_MESSAGE)
    keyboard = [[InlineKeyboardButton("点此开通VIP会员", callback_data="show_vip")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(SERVICE_TEXT, reply_markup=reply_markup)
    context.user_data["welcome_sent"] = True

async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "show_vip":
        await query.message.reply_photo(photo=VIP_IMAGE_URL, caption=VIP_CAPTION)
        keyboard = [[InlineKeyboardButton("我已付款，开始验证", callback_data="start_order_verify")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(PAYMENT_DONE_TEXT, reply_markup=reply_markup)

    elif query.data == "start_order_verify":
        context.user_data.pop("fail_count", None)
        context.user_data.pop("blocked_until", None)
        sent = await query.message.reply_photo(photo=ORDER_GUIDE_IMAGE_URL, caption=ORDER_GUIDE_CAPTION)
        context.user_data['order_guide_msg_id'] = sent.message_id
        context.user_data['awaiting'] = 'order_id'

async def resend_guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.edit_message_caption(
            chat_id=update.effective_chat.id,
            message_id=context.user_data.get('order_guide_msg_id'),
            caption=ORDER_GUIDE_CAPTION
        )
    except:
        sent = await update.message.reply_photo(photo=ORDER_GUIDE_IMAGE_URL, caption=ORDER_GUIDE_CAPTION)
        context.user_data['order_guide_msg_id'] = sent.message_id
    context.user_data['awaiting'] = 'order_id'

async def handle_order_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('awaiting') != 'order_id':
        return

    text = update.message.text.strip()

    blocked_until = context.user_data.get("blocked_until")
    if blocked_until and datetime.now() < blocked_until:
        await update.message.reply_text(BLOCK_MESSAGE)
        return

    if text.startswith("20260"):
        context.user_data.clear()
        keyboard = [[InlineKeyboardButton("立即加入VIP群", url=GROUP_LINK)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "订单审核通过！\n\n恭喜获得VIP专属权限！\n请点击下方按钮进入中转群",
            reply_markup=reply_markup
        )
        return

    fail_count = context.user_data.get("fail_count", 0) + 1
    context.user_data["fail_count"] = fail_count

    if fail_count >= MAX_FAILS:
        context.user_data["blocked_until"] = datetime.now() + timedelta(hours=COOLDOWN_HOURS)
        context.user_data['awaiting'] = None
        await update.message.reply_text(BLOCK_MESSAGE)
        return

    remaining = MAX_FAILS - fail_count
    await update.message.reply_text(FAIL_TEXT.format(remaining))
    await resend_guide(update, context)

def main():
    print("正在启动守门员小卫【终极无错版】...")
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        print("错误：未找到 TOKEN")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS | filters.COMMAND, auto_start_and_a))
    app.add_handler(CommandHandler("start", auto_start_and_a))
    app.add_handler(CommandHandler("a", auto_start_and_a))
    app.add_handler(CallbackQueryHandler(handle_button_click))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_order_id))

    print("守门员小卫启动成功！输错2次封15小时 + 进群按钮")
    app.run_polling(drop_pending_updates=True, timeout=20)

if __name__ == '__main__':
    main()
