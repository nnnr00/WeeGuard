# -*- coding: utf-8 -*-
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import re

# ============ 这里改成你自己的 ============
TOKEN = "8515162052:AAFyZu2oKv9CjgtKaA0nQHc-PydLRaV5BZI"   # ← 改成第1步拿到的 token
ADMIN_ID = 1480512549                                        # ← 改成第2步拿到的你的ID
# =========================================

quick_replies = {
    "发货": "您的订单已经发货啦，物流单号请查看私信",
    "查物流": "请提供完整物流单号，我马上帮您查询！",
}

ORDER_REPLY = """
检测到您的订单号：{order}

我们已收到，正在火速处理！
预计 1-3 个工作日内发货
如需加急请直接回复“加急”
"""

ORDER_PATTERN = re.compile(r"\b(20260\d{5,})\b")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "嗨！我是订单查询机器人\n"
        "直接发 20260 开头的订单号给我就行啦\n"
        "输入 /help 查看更多功能"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("/start - 打招呼\n/help - 帮助\n直接发订单号自动识别")

async def reply_manage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("只有管理员能用这条命令哦")
        return
    if len(context.args) < 2:
        await update.message.reply_text("用法：/reply 关键词 回复内容\n例：/reply 发货 已发货啦")
        return
    keyword = context.args[0]
    content = " ".join(context.args[1:])
    quick_replies[keyword] = content
    await update.message.reply_text(f"已设置：{keyword} → {content}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    
    # 快捷回复
    for kw, reply in quick_replies.items():
        if kw.lower() in text.lower():
            await update.message.reply_text(reply)
            return
    
    # 订单号识别
    match = ORDER_PATTERN.search(text)
    if match:
        order = match.group(1)
        await update.message.reply_text(ORDER_REPLY.format(order=order))

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("reply", reply_manage))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
