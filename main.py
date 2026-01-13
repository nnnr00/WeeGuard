import os
import re
import asyncio
import json
from fastapi import FastAPI
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

# 🔴 1. 必须先创建 app！
app = FastAPI()  # ←←← 这行必须在最前面！

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

WELCOME_MSG = (
    "🔐 请先完成以下步骤：\n"
    "1️⃣ 发送你的订单号或邀请码\n"
    "2️⃣ 审核通过后自动入群\n\n"
    "⏱️ 审核通常在1-5分钟内完成\n\n"
    "🎉 通过后即可参与讨论！\n\n"
    "📢 小卫小卫，守门员小卫！\n"
    "- 一键入群，小卫帮你搞定！\n"
    "- 新人来报到，小卫查身份！\n\n"
    "💬 如有疑问，请私信我。\n\n"
    "➡️ 请直接发送账单订单编号："
)

def get_custom_replies():
    raw = os.environ.get("CUSTOM_COMMANDS", "{}")
    try:
        return json.loads(raw)
    except:
        return {}

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_MSG)
    context.user_data["welcomed"] = True

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("welcomed"):
        await update.message.reply_text(WELCOME_MSG)
        context.user_data["welcomed"] = True
        return

    text = update.message.text.strip()
    
    if update.effective_user.id == ADMIN_ID and text == "/listcmd":
        replies = get_custom_replies()
        msg = "📌 当前关键词：\n" + "\n".join(f"• {k}" for k in replies.keys()) if replies else "📭 暂无"
        await update.message.reply_text(msg)
        return

    replies = get_custom_replies()
    for keyword, reply in replies.items():
        if keyword in text:
            try:
                if reply["type"] == "text":
                    await update.message.reply_text(reply["content"])
                elif reply["type"] == "photo":
                    await update.message.reply_photo(reply["content"])
                elif reply["type"] == "video":
                    await update.message.reply_video(reply["content"])
                return
            except:
                await update.message.reply_text("❌ 加载失败")
                return

    if re.search(r"20260\d*", text):
        await update.message.reply_text("✅ 查询成功！")
        await update.message.reply_text("https://t.me/+495j5rWmApsxYzg9")
    else:
        await update.message.reply_text("❌ 未识别")

# 🔴 2. startup 必须在 app 创建后定义
@app.on_event("startup")
async def startup():
    application = Application.builder().token(BOT_TOKEN).build()
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
    except:
        pass
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    
    await application.initialize()
    await application.start()
    asyncio.create_task(application.updater.start_polling())

# 防休眠
async def keep_alive():
    while True:
        await asyncio.sleep(240)

@app.on_event("startup")
async def start_keep_alive():
    asyncio.create_task(keep_alive())

@app.get("/health")
async def health():
    return {"status": "ok"}
