import os
import re
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# 🔴🔴🔴 请在这里替换为你的 Bot Token（仅限本地测试！）
# ⚠️ 警告：部署到 Railway/GitHub 前务必删除或改用环境变量，否则会泄露！
BOT_TOKEN = "8515162052:AAFyZu2oKv9CjgtKaA0nQHc-PydLRaV5BZI"  # ←←← 就改这一行！

# ✅ 安全做法（Railway 推荐）：取消下面两行的注释，并删除上面的硬编码行
# import os
# BOT_TOKEN = os.environ["BOT_TOKEN"]

app = FastAPI()
application = Application.builder().token(BOT_TOKEN).build()  # ← 这里自动用上面的 Token

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if re.search(r"20260\d*", text):
        await update.message.reply_text("✅ 订单号识别成功！")
        await update.message.reply_text("/2026 1")
    else:
        await update.message.reply_text("未识别")

application.add_handler(MessageHandler(filters.TEXT, handle_message))

@app.on_event("startup")
async def startup():
    await application.initialize()
    await application.start()
    url = os.getenv("RAILWAY_STATIC_URL", "").rstrip("/")
    if url:
        await application.bot.set_webhook(url + "/webhook")

@app.post("/webhook")
async def webhook(req: Request):
    update = Update.de_json(await req.json(), application.bot)
    await application.update_queue.put(update)
    return {"ok": True}

@app.get("/health")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    application.run_polling()
