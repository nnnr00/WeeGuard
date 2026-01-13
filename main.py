import os
import re
import asyncio
from fastapi import FastAPI
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

app = FastAPI()
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ.get("ADMIN_ID", "1480512549"))  # ← 确保设了 ADMIN_ID

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

# 🔴 1. 新增 /a 命令处理器
async def addcmd_guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 🔴 关键：检查是否为管理员
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⚠️ 仅管理员可用")
        return
    
    guide = (
        "🛠️ 添加自定义命令指南\n\n"
        "1️⃣ 准备 JSON 配置（示例）：\n"
        "```json\n"
        '{\n'
        '  "规则": {"type": "photo", "content": "图片直链"},\n'
        '  "售后": {"type": "text", "content": "📞 微信：xiaowei"}\n'
        '}\n'
        "```\n\n"
        "2️⃣ Railway → Variables → 新建变量：\n"
        "- Name: `CUSTOM_COMMANDS`\n"
        "- Value: 粘贴上面的 JSON\n\n"
        "3️⃣ Save → Restart 服务"
    )
    await update.message.reply_text(guide, parse_mode="Markdown")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_MSG)
    context.user_data["welcomed"] = True

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("welcomed"):
        await update.message.reply_text(WELCOME_MSG)
        context.user_data["welcomed"] = True
        return

    text = update.message.text.strip()
    
    # 管理员命令：/listcmd
    if update.effective_user.id == ADMIN_ID and text == "/listcmd":
        raw = os.environ.get("CUSTOM_COMMANDS", "{}")
        try:
            replies = eval(raw) if raw else {}
            msg = "📌 当前关键词：\n" + "\n".join(f"• {k}" for k in replies.keys()) if replies else "📭 暂无"
        except:
            msg = "❌ CUSTOM_COMMANDS 格式错误"
        await update.message.reply_text(msg)
        return

    # 自定义关键词
    raw = os.environ.get("CUSTOM_COMMANDS", "{}")
    try:
        replies = eval(raw) if raw else {}
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
                    await update.message.reply_text("❌ 资源加载失败")
                    return
    except:
        pass

    # 订单号识别
    if re.search(r"20260\d*", text):
        await update.message.reply_text("✅ 查询成功！")
        await update.message.reply_text("https://t.me/+495j5rWmApsxYzg9")
    else:
        await update.message.reply_text("❌ 未识别")

# 🔴 2. 【关键】注册 /a 和 /start 处理器
@app.on_event("startup")
async def startup():
    application = Application.builder().token(BOT_TOKEN).build()
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
    except:
        pass
    
    # 🔴 注册命令处理器（顺序很重要！）
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("a", addcmd_guide))  # ← 新增这行！
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    
    await application.initialize()
    await application.start()
    asyncio.create_task(application.updater.start_polling())

async def keep_alive():
    while True:
        await asyncio.sleep(240)

@app.on_event("startup")
async def start_keep_alive():
    asyncio.create_task(keep_alive())

@app.get("/health")
async def health():
    return {"status": "ok"}
