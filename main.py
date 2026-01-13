import os
import re
from fastapi import FastAPI
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# 🔴🔴🔴 1. Token 从环境变量读取（Railway 中设 BOT_TOKEN）
BOT_TOKEN = os.environ["BOT_TOKEN"]

# 🔴🔴🔴 2. 自定义关键词回复（支持文本/图片/视频）
# 格式：{"关键词": {"type": "text", "content": "回复内容"}}
# type 可选: "text", "photo", "video"
CUSTOM_REPLIES = {
    "视频": {
        "type": "video",
        "content": "https://github.com/python-telegram-bot/python-telegram-bot/raw/master/tests/data/video.mp4"
    },
    "图片": {
        "type": "photo",
        "content": "https://github.com/python-telegram-bot/python-telegram-bot/raw/master/tests/data/telegram.jpg"
    },
    "帮助": {
        "type": "text",
        "content": "💡 发送订单号（20260开头）即可入群审核～"
    }
}

# 创建 FastAPI（仅用于健康检查）
app = FastAPI()

# 创建机器人（Polling 模式）
application = Application.builder().token(BOT_TOKEN).build()

# 🔴🔴🔴 3. 欢迎消息（HTML 格式，带 emoji）
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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 🔴🔴🔴 4. 首次对话发欢迎语
    if not context.user_data.get("welcomed"):
        await update.message.reply_text(WELCOME_MSG, parse_mode="HTML")
        context.user_data["welcomed"] = True
        return

    text = update.message.text.strip()

    # 🔴🔴🔴 5. 检查自定义关键词（优先级高于订单号）
    for keyword, reply in CUSTOM_REPLIES.items():
        if keyword in text:
            if reply["type"] == "text":
                await update.message.reply_text(reply["content"])
            elif reply["type"] == "photo":
                await update.message.reply_photo(reply["content"])
            elif reply["type"] == "video":
                await update.message.reply_video(reply["content"])
            return

    # 🔴🔴🔴 6. 识别订单号（20260开头任意长度数字）
    if re.search(r"20260\d*", text):
        await update.message.reply_text("✅ 查询成功！")
        await update.message.reply_text("/VIP 1")  # 自动跳转命令
    else:
        await update.message.reply_text("❌ 未识别")

# 注册处理器
application.add_handler(MessageHandler(filters.TEXT, handle_message))

# 启动机器人（后台轮询）
@app.on_event("startup")
async def start_bot():
    await application.initialize()
    await application.start()
    application.run_polling(drop_pending_updates=True)

# Railway 必需：健康检查
@app.get("/health")
async def health():
    return {"status": "ok"}
