import os
import re
import asyncio
from fastapi import FastAPI
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# 🔴 1. 安全读取 Token（加 fallback 防崩溃）
try:
    BOT_TOKEN = os.environ["BOT_TOKEN"]
except KeyError:
    raise RuntimeError("❌ 请在 Railway Variables 中设置 BOT_TOKEN！")

WELCOME_MSG = (
    '<span class="emoji emoji1f44b"></span> 欢迎加入【守门员小卫】！我是守门员小卫，你的身份验证小助手～\n\n'
    '<span class="emoji emoji1f510"></span> 请先完成以下步骤：\n'
    '1️⃣ 发送你的订单号或邀请码\n'
    '2️⃣ 审核通过后自动入群\n\n'
    '⏱️ 审核通常在1-5分钟内完成\n\n'
    '<span class="emoji emoji1f389"></span> 通过后即可参与讨论！\n\n'
    '<span class="emoji emoji1f4e2"></span> 小卫小卫，守门员小卫！\n'
    '- 一键入群，小卫帮你搞定！\n'
    '- 新人来报到，小卫查身份！\n\n'
    '💬 如有疑问，请私信我。\n\n'
    '➡️ 请直接发送订单编号：'
)

app = FastAPI()
application = None
bot_task = None  # 🔴 记录 bot 任务

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("welcomed"):
        await update.message.reply_text(WELCOME_MSG, parse_mode="HTML")
        context.user_data["welcomed"] = True
        return

    text = update.message.text.strip()
    if re.search(r"20260\d*", text):
        await update.message.reply_text("✅ 查询成功！")
        await update.message.reply_text("/VIP 1")
    else:
        await update.message.reply_text("❌ 未识别")

# 🔴 2. 【关键】独立 bot 启动函数（不阻塞 FastAPI）
async def start_bot():
    global application
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    
    await application.initialize()
    await application.start()
    
    # 🔴 3. 【关键】用 updater.start_polling() + 保存任务
    global bot_task
    bot_task = asyncio.create_task(
        application.updater.start_polling(drop_pending_updates=True)
    )
    print("✅ Bot polling started")

@app.on_event("startup")
async def startup():
    # 启动 bot
    asyncio.create_task(start_bot())
    # 防休眠
    asyncio.create_task(keep_alive())

async def keep_alive():
    while True:
        print("🏓 Keep-alive")
        await asyncio.sleep(240)

# 🔴 4. 【关键修复】Shutdown 时先 stop_polling 再 shutdown
@app.on_event("shutdown")
async def shutdown():
    global application, bot_task
    if application:
        print("🛑 Stopping bot...")
        # 1. 先停止 polling
        if bot_task and not bot_task.done():
            bot_task.cancel()
            try:
                await bot_task
            except asyncio.CancelledError:
                pass
        # 2. 再 stop & shutdown
        await application.stop()
        await application.shutdown()
        print("✅ Bot stopped gracefully")
