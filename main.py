import os
import re
import asyncio
from fastapi import FastAPI
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# 🔴 1. Token 安全读取
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("❌ 请设置 BOT_TOKEN")

# 🔴 2. 【关键】纯文本欢迎语（无任何 HTML/Markdown）
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

app = FastAPI()
application = None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 🔴 3. 【关键】首次发消息触发欢迎语（无 parse_mode！）
    if not context.user_data.get("welcomed"):
        await update.message.reply_text(WELCOME_MSG)  # ← 没有 parse_mode 参数！
        context.user_data["welcomed"] = True
        return

    text = update.message.text.strip()
    
    # 订单号识别
    if re.search(r"20260\d*", text):
        await update.message.reply_text("✅ 查询成功！")
        await update.message.reply_text("/VIP 1")
    else:
        await update.message.reply_text("❌ 未识别")

# 启动 Bot
@app.on_event("startup")
async def startup():
    global application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # 清理 webhook（防冲突）
    await application.bot.delete_webhook(drop_pending_updates=True)
    
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    await application.initialize()
    await application.start()
    asyncio.create_task(application.updater.start_polling())

# 健康检查
@app.get("/health")
async def health():
    return {"status": "ok"}
