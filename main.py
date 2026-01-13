import os
import re
import asyncio
from fastapi import FastAPI
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# 🔴 1. Token 安全读取
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("❌ 请在 Railway Variables 中设置 BOT_TOKEN！")

app = FastAPI()
application = None

# 🔴 2. 【新增】自定义关键词回复（支持 text/photo/video）
# 格式：{"关键词": {"type": "类型", "content": "内容"}}
# type 可选: "text", "photo", "video"
CUSTOM_REPLIES = {
    # 示例：发送"视频" → 自动发视频
    "视频": {
        "type": "video",
        "content": "https://github.com/python-telegram-bot/python-telegram-bot/raw/master/tests/data/video.mp4"
    },
    # 示例：发送"图片" → 自动发图片
    "图片": {
        "type": "photo",
        "content": "https://github.com/python-telegram-bot/python-telegram-bot/raw/master/tests/data/telegram.jpg"
    },
    # 示例：发送"帮助" → 自动发文本
    "帮助": {
        "type": "text",
        "content": "💡 发送订单号（20260开头）即可入群审核～"
    },
    # 🔴 你可以在这里添加自己的关键词 ↓
    # "售后": {"type": "text", "content": "请联系 @admin"},
    # "规则": {"type": "photo", "content": "https://your-domain.com/rules.jpg"},
}

# 🔴 3. 欢迎语（纯文本 + 真实 emoji）
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
    if not context.user_data.get("welcomed"):
        await update.message.reply_text(WELCOME_MSG)
        context.user_data["welcomed"] = True
        return

    text = update.message.text.strip()
    
    # 🔴 4. 【新增】检查自定义关键词（优先级：关键词 > 订单号）
    for keyword, reply in CUSTOM_REPLIES.items():
        if keyword in text:
            try:
                if reply["type"] == "text":
                    await update.message.reply_text(reply["content"])
                elif reply["type"] == "photo":
                    await update.message.reply_photo(reply["content"])
                elif reply["type"] == "video":
                    await update.message.reply_video(reply["content"])
                print(f"✅ 触发关键词: {keyword}")
                return  # 匹配后直接返回，不继续
            except Exception as e:
                print(f"⚠️ 回复 {keyword} 失败: {e}")
                await update.message.reply_text("❌ 资源加载失败，请稍后再试")
                return

    # 🔴 5. 订单号识别（20260开头）
    if re.search(r"20260\d*", text):
        await update.message.reply_text("✅ 查询成功！")
        await update.message.reply_text("/VIP 1")
    else:
        await update.message.reply_text("❌ 未识别")

# 🔴 6. 启动 Bot（带 webhook 清理）
@app.on_event("startup")
async def startup():
    global application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # 清理 webhook（解决 Conflict）
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
        print("🧹 Webhook cleared")
    except Exception as e:
        print(f"⚠️ Webhook clear failed (ignored): {e}")
    
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    await application.initialize()
    await application.start()
    asyncio.create_task(application.updater.start_polling(drop_pending_updates=True))
    print("✅ Bot started")

# 防休眠
async def keep_alive():
    while True:
        print("🏓 Keep-alive")
        await asyncio.sleep(240)

@app.on_event("startup")
async def start_keep_alive():
    asyncio.create_task(keep_alive())

@app.get("/health")
async def health():
    return {"status": "ok"}
