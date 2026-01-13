import os
import re
import asyncio
from fastapi import FastAPI
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# 🔴 1. Token 安全读取（Railway 中设 BOT_TOKEN）
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("❌ 请在 Railway Variables 中设置 BOT_TOKEN！")

app = FastAPI()
application = None

# 🔴 2. 欢迎语（纯文本 + 真实 emoji，无任何 HTML/Markdown）
WELCOME_MSG = (
    "👋 欢迎加入【守门员小卫】！我是守门员小卫，你的身份验证小助手～\n\n"
    "🔐 请先完成以下步骤：\n"
    "1️⃣ 发送你的订单号或邀请码\n"
    "2️⃣ 审核通过后自动入群\n\n"
    "⏱️ 审核通常在1-5分钟内完成\n\n"
    "🎉 通过后即可参与讨论！\n\n"
    "📢 小卫小卫，守门员小卫！\n"
    "- 一键入群，小卫帮你搞定！\n"
    "- 新人来报到，小卫查身份！\n\n"
    "💬 如有疑问，请私信我。\n\n"
    "➡️ 请直接发送订单编号："
)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 🔴 3. 首次发消息触发欢迎语（无 parse_mode！）
    if not context.user_data.get("welcomed"):
        await update.message.reply_text(WELCOME_MSG)  # ← 关键：不加 parse_mode
        context.user_data["welcomed"] = True
        return

    text = update.message.text.strip()
    
    # 🔴 4. 订单号识别（20260开头任意长度）
    if re.search(r"20260\d*", text):
        await update.message.reply_text("✅ 查询成功！")
        await update.message.reply_text("/VIP 1")  # 自动跳转
    else:
        await update.message.reply_text("❌ 未识别")

# 🔴 5. 【关键】启动 Bot（带 webhook 清理）
@app.on_event("startup")
async def startup():
    global application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # 🔴 6. 【关键】启动前清除 webhook（解决 Conflict 问题！）
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
        print("🧹 Webhook cleared")
    except Exception as e:
        print(f"⚠️ Webhook clear failed (ignored): {e}")
    
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    await application.initialize()
    await application.start()
    
    # 启动 polling（非阻塞）
    asyncio.create_task(
        application.updater.start_polling(drop_pending_updates=True)
    )
    print("✅ Bot started")

# 🔴 7. 防 Railway 休眠
async def keep_alive():
    while True:
        print("🏓 Keep-alive")
        await asyncio.sleep(240)

@app.on_event("startup")
async def start_keep_alive():
    asyncio.create_task(keep_alive())

# 🔴 8. 健康检查（Railway 必需）
@app.get("/health")
async def health():
    return {"status": "ok"}
