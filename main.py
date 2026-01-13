import os
import re
import asyncio
from fastapi import FastAPI
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, MessageHandler, CommandHandler, 
    filters, ContextTypes, ConversationHandler
)

# 🔴 配置（Railway 中设环境变量）
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("❌ 请在 Railway Variables 中设置 BOT_TOKEN！")

ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))  # 用 @userinfobot 获取你的 ID

# 状态常量
WAITING_KEYWORD, WAITING_TYPE, WAITING_CONTENT = range(3)

# 🔴 欢迎语（完全按你要求定制）
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
    "➡️ 请直接发送账单订单编号：\n\n"
    "🛠️ 管理员指令：\n"
    "/a - 添加新关键词\n"
    "/listcmd - 查看所有关键词"
)

# 🔴 /start 命令（触发欢迎语）
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_MSG)
    context.user_data["welcomed"] = True

# 🔴 
