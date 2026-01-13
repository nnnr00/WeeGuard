import os
import re
import asyncio
import signal
from fastapi import FastAPI
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# 🔴 1. 安全读取 Token
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("❌ 请在 Railway Variables 中设置 BOT_TOKEN！")

# 🔴 2. 欢迎语（用真实 emoji，无 HTML）
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
    if not context.user_data.get("welcomed"):
        await update.message.reply_text(WELCOME_MSG)
        context.user_data["welcomed"] = True
        return

    text = update.message.text.strip()
    if re.search(r"20260\d*", text):
        await update.message.reply_text("✅ 查询成功！")
        await update.message.reply_text("/VIP 1")
    else:
        await update.message.reply_text("❌ 未识别")

# 🔴 3. 启动 Bot（带 webhook 清理）
async def start_bot():
    global application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # 清理可能的 webhook（防冲突）
    await application.bot.delete_webhook(drop_pending_updates=True)
    
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    await application.initialize()
    await application.start()
    
    # 启动 polling（非阻塞）
    asyncio.create_task(
        application.updater.start_polling(drop_pending_updates=True)
    )
    print("✅ Bot started")

@app.on_event("startup")
async def startup():
    asyncio.create_task(start_bot())
    # 防休眠
    asyncio.create_task(keep_alive())

async def keep_alive():
    while True:
        print("🏓 Keep-alive")
        await asyncio.sleep(240)

# 🔴 4. 【关键修复】优雅 shutdown（捕获所有异常 + 超时）
@app.on_event("shutdown")
async def shutdown():
    global application
    if not application:
        return
    
    print("🛑 Shutting down bot...")
    try:
        # 先 stop（不 shutdown，避免网络请求）
        await asyncio.wait_for(application.stop(), timeout=5.0)
        print("✅ Bot stopped")
    except asyncio.TimeoutError:
        print("⚠️ Bot stop timeout, forcing exit")
    except Exception as e:
        print(f"⚠️ Bot stop error (ignored): {e}")
    
    # 不调用 application.shutdown() —— Railway 会强杀，没必要
