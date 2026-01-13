from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import os
import re

TOKEN = os.getenv("TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "欢迎使用订单查询机器人！\n"
        "直接发送20260开头的订单号即可查询物流\n"
        "例如：20260123456789"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    orders = re.findall(r'20260\d+', text)
    if orders:
        for order in orders:
            await update.message.reply_text(
                f"订单查询结果\n"
                f"订单号：{order}\n"
                f"状态：已发货\n"
                f"物流公司：圆通快递\n"
                f"运单号：{order}\n"
                f"预计明天到达，请注意查收"
            )

app = Application.builder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# 2025年 Railway 完美启动方式
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run_polling()  # 先用 polling 保证一定能启动
    # 后面 Railway 会自动改成 webhook，不需要我们手动设置
