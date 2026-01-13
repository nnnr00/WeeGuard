import os
import re
import asyncio
import json
from fastapi import FastAPI
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
# 🔴 关键修复：添加 CallbackQueryHandler 导入
from telegram.ext import (
    Application, MessageHandler, CommandHandler, 
    filters, ContextTypes, ConversationHandler,
    CallbackQueryHandler  # ← 就加这一行！
)

app = FastAPI()
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ.get("ADMIN_ID", "1480512549"))

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

# /a 命令：显示按钮
async def addcmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⚠️ 仅管理员可用")
        return
    
    keyboard = [[InlineKeyboardButton("➕ 添加新关键词", callback_data="add_keyword")]]
    await update.message.reply_text(
        "🛠️ 管理员控制台",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# 按钮回调
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "add_keyword":
        await query.edit_message_text("🔧 请输入触发关键词（如：规则）：")
        return WAITING_KEYWORD

# 接收关键词
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
