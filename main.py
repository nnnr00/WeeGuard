import os
import re
import asyncio
from fastapi import FastAPI
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# 🔴 1. Token（保持从环境变量读取）
BOT_TOKEN = os.environ["BOT_TOKEN"]

# 🔴 2. 欢迎语（HTML 格式）
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

# 自定义回复（可选）
CUSTOM_REPLIES = {
    "帮助": {"type": "text", "content": "💡 发送订单号（20260开头）即可～"}
}

app = FastAPI()
application = None  # 全局变量

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("welcomed"):
        await update.message.reply_text(WELCOME_MSG, parse_mode="HTML")
        context.user_data["welcomed"] = True
        return

    text = update.message.text.strip()
    
    # 检查关键词
    for kw, reply in CUSTOM_REPLIES.items():
        if kw in text:
            await update.message.reply_text(reply["content"])
            return

    # 检查订单号
    if re.search(r"20260\d*", text):
        await update.message.reply_text("✅ 查询成功！")
        await update.message.reply_text("/VIP 1")
    else:
        await update.message.reply_text("❌ 未识别")

# 🔴 3. 【关键修复】Bot 启动函数（独立后台任务）
async def run_bot():
    global application
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    
    # ✅ 正确顺序：await initialize → await start → run_polling（非阻塞）
    await application.initialize()
    await application.start()
    print("✅ Bot started in background")
    
    # 🔴 4. 【关键】用 create_task 启动 polling（非阻塞！）
    await application.updater.start_polling(drop_pending_updates=True)

# 🔴 5. 【关键】Startup 事件中启动 Bot 任务
@app.on_event("startup")
async def startup_event():
    # 启动 bot 作为后台任务
    asyncio.create_task(run_bot())
    # 启动 keep-alive 防休眠
    asyncio.create_task(keep_alive())

# 🔴 6. 防 Railway 休眠（每 4 分钟 ping）
async def keep_alive():
    while True:
        print("🏓 Keep-alive ping...")
        await asyncio.sleep(240)

@app.get("/health")
async def health():
    return {"status": "ok"}

# 🔴 7. 【关键】Shutdown 事件中正确关闭 Bot
@app.on_event("shutdown")
async def shutdown_event():
    if application:
        await application.stop()
        await application.shutdown()
        print("🛑 Bot stopped gracefully")
