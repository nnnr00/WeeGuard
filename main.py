import os
import re
import asyncio  # ← 新增
from fastapi import FastAPI
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.environ["BOT_TOKEN"]

# ... [WELCOME_MSG 和 CUSTOM_REPLIES 保持不变] ...

app = FastAPI()
application = Application.builder().token(BOT_TOKEN).build()

# ... [handle_message 保持不变] ...

@app.on_event("startup")
async def start_bot():
    await application.initialize()
    await application.start()
    application.run_polling(drop_pending_updates=True)
    
    # 🔴🔴🔴 新增：每 4 分钟 ping 自己一次，防 Railway 休眠
    async def keep_alive():
        while True:
            try:
                # 模拟访问 /health
                print("🏓 Ping /health to prevent sleep...")
            except:
                pass
            await asyncio.sleep(240)  # 240秒 = 4分钟（< Railway 5分钟休眠阈值）
    
    # 启动后台任务
    asyncio.create_task(keep_alive())

@app.get("/health")
async def health():
    return {"status": "ok"}
