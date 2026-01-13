import os
import re
import asyncio
import json
from fastapi import FastAPI
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, MessageHandler, CommandHandler, 
    filters, ContextTypes, ConversationHandler
)

app = FastAPI()
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ.get("ADMIN_ID", "1480512549"))

# 状态常量
WAITING_KEYWORD, WAITING_TYPE, WAITING_CONTENT = range(3)

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

# 🔴 /a 命令：显示按钮
async def addcmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⚠️ 仅管理员可用")
        return
    
    keyboard = [[InlineKeyboardButton("➕ 添加新关键词", callback_data="add_keyword")]]
    await update.message.reply_text(
        "🛠️ 管理员控制台",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# 🔴 按钮回调：开始添加流程
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "add_keyword":
        await query.edit_message_text("🔧 请输入触发关键词（如：规则）：")
        return WAITING_KEYWORD

# 🔴 接收关键词
async def receive_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyword = update.message.text.strip()
    if not keyword:
        await update.message.reply_text("❌ 关键词不能为空，请重试：")
        return WAITING_KEYWORD
    
    context.user_data["new_keyword"] = keyword
    keyboard = [
        [InlineKeyboardButton("📝 文本", callback_data="type_text")],
        [InlineKeyboardButton("🖼️ 图片", callback_data="type_photo")],
        [InlineKeyboardButton("🎥 视频", callback_data="type_video")]
    ]
    await update.message.reply_text(
        f"✅ 关键词：{keyword}\n请选择回复类型：",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return WAITING_TYPE

# 🔴 接收类型
async def type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    type_map = {
        "type_text": "text",
        "type_photo": "photo",
        "type_video": "video"
    }
    reply_type = type_map.get(query.data)
    
    if not reply_type:
        await query.edit_message_text("❌ 无效选择，请重试")
        return WAITING_TYPE
    
    context.user_data["new_type"] = reply_type
    examples = {
        "text": "例如：群规请查看图片",
        "photo": "请发送图片直链（.jpg/.png）",
        "video": "请发送视频直链（.mp4）"
    }
    await query.edit_message_text(f"3️⃣ 请输入{reply_type}内容：\n{examples[reply_type]}")
    return WAITING_CONTENT

# 🔴 接收内容并保存
async def receive_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    content = update.message.text.strip()
    if not content:
        await update.message.reply_text("❌ 内容不能为空，请重试：")
        return WAITING_CONTENT

    keyword = context.user_data["new_keyword"]
    reply_type = context.user_data["new_type"]
    
    # 🔴 生成新配置（合并现有）
    raw = os.environ.get("CUSTOM_COMMANDS", "{}")
    try:
        replies = json.loads(raw)
    except:
        replies = {}
    
    replies[keyword] = {"type": reply_type, "content": content}
    new_json = json.dumps(replies, ensure_ascii=False)
    
    # 🔴 提示管理员更新环境变量
    guide = (
        "🎉 添加成功！\n\n"
        "📌 请按以下步骤保存：\n"
        "1️⃣ 复制下方 JSON\n"
        "2️⃣ Railway → Variables → 编辑 CUSTOM_COMMANDS\n"
        "3️⃣ 粘贴 → Save → Restart\n\n"
        f"```json\n{new_json}\n```"
    )
    await update.message.reply_text(guide, parse_mode="Markdown")
    return ConversationHandler.END

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_MSG)
    context.user_data["welcomed"] = True

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("welcomed"):
        await update.message.reply_text(WELCOME_MSG)
        context.user_data["welcomed"] = True
        return

    text = update.message.text.strip()
    
    # 管理员命令
    if update.effective_user.id == ADMIN_ID and text == "/listcmd":
        raw = os.environ.get("CUSTOM_COMMANDS", "{}")
        try:
            replies = json.loads(raw)
            msg = "📌 当前关键词：\n" + "\n".join(f"• {k}" for k in replies.keys()) if replies else "📭 暂无"
        except:
            msg = "❌ CUSTOM_COMMANDS 格式错误"
        await update.message.reply_text(msg)
        return

    # 自定义关键词
    raw = os.environ.get("CUSTOM_COMMANDS", "{}")
    try:
        replies = json.loads(raw)
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

# 🔴 启动 Bot（注册所有处理器）
@app.on_event("startup")
async def startup():
    application = Application.builder().token(BOT_TOKEN).build()
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
    except:
        pass
    
    # 注册命令和按钮
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("a", addcmd_start))
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    
    # 注册按钮回调
    application.add_handler(CallbackQueryHandler(button_callback, pattern="^add_keyword$"))
    application.add_handler(CallbackQueryHandler(type_callback, pattern="^type_"))
    
    # 注册对话流程
    conv_handler = ConversationHandler(
        entry_points=[],
        states={
            WAITING_KEYWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_keyword)],
            WAITING_TYPE: [],
            WAITING_CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_content)],
        },
        fallbacks=[]
    )
    application.add_handler(conv_handler)
    
    await application.initialize()
    await application.start()
    asyncio.create_task(application.updater.start_polling())

@app.get("/health")
async def health():
    return {"status": "ok"}
