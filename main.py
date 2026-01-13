import os
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from datetime import datetime, timedelta

load_dotenv()

# 配置
VIP_IMG = "https://i.postimg.cc/QtkVBw7N/photo-2026-01-13-17-04-27.jpg"
GUIDE_IMG = "https://i.postimg.cc/QtkVBw7N/photo-2026-01-13-17-04-27.jpg"
GROUP_LINK = "https://t.me/+495j5rWmApsxYzg9"

WELCOME = "欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~\n\n小卫小卫，守门员小卫！\n一键入群，小卫帮你搞定！"
VIP_INFO = "VIP会员特权说明：\n- 专属中转通道\n- 优先审核入群\n- 7x24小时客服支持\n- 定期福利活动\n\n请私信管理员开通"
ORDER_GUIDE = "1️⃣ 发送你的订单号\n订单号在 我的 → 账单 → 账单详情 → 更多 → 订单号（全部复制）\n\n请直接发送订单编号："

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("started"):
        return
    await update.message.reply_text(WELCOME)
    kb = [[InlineKeyboardButton("点此开通VIP会员", callback_data="vip")]]
    await update.message.reply_text("请选择您需要的服务：", reply_markup=InlineKeyboardMarkup(kb))
    context.user_data["started"] = True

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "vip":
        await query.message.reply_photo(VIP_IMG, caption=VIP_INFO)
        kb = [[InlineKeyboardButton("我已付款，开始验证", callback_data="verify")]]
        await query.message.reply_text("付款成功后，请点击下方按钮开始身份验证", reply_markup=InlineKeyboardMarkup(kb))

    elif query.data == "verify":
        context.user_data.update({"awaiting": "order", "fails": 0})
        msg = await query.message.reply_photo(GUIDE_IMG, caption=ORDER_GUIDE)
        context.user_data["guide_msg"] = msg.message_id

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting") != "order":
        return

    order = update.message.text.strip()
    user_id = update.effective_user.id

    # 检查封禁
    if context.user_data.get("blocked_until"):
        if datetime.now() < context.user_data["blocked_until"]:
            await update.message.reply_text("您已连续输错2次，已被临时限制。\n请15小时后再试。")
            return
        else:
            context.user_data.pop("blocked_until", None)

    # 正确订单
    if order.startswith("20260"):
        context.user_data.clear()
        kb = [[InlineKeyboardButton("立即加入VIP群", url=GROUP_LINK)]]
        await update.message.reply_text(
            "订单审核通过！\n\n恭喜获得VIP权限！\n点击下方按钮进入专属群",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    # 输错
    fails = context.user_data.get("fails", 0) + 1
    context.user_data["fails"] = fails

    if fails >= 2:
        context.user_data["blocked_until"] = datetime.now() + timedelta(hours=15)
        context.user_data["awaiting"] = None
        await update.message.reply_text("您已连续输错2次，为防刷已限制验证。\n15小时后可重试。")
        return

    await update.message.reply_text(f"订单获取失败 请重试（还剩 {2 - fails} 次机会）")

    # 重新显示指引图
    try:
        await context.bot.edit_message_caption(
            chat_id=update.effective_chat.id,
            message_id=context.user_data.get("guide_msg"),
            caption=ORDER_GUIDE
        )
    except:
        msg = await update.message.reply_photo(GUIDE_IMG, caption=ORDER_GUIDE)
        context.user_data["guide_msg"] = msg.message_id

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("错误：未找到 TELEGRAM_BOT_TOKEN")
        return

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("守门员小卫启动成功！输错2次封15小时 + 进群按钮")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
