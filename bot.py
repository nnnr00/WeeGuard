# bot.py —— 自动触发 /2026 命令版（Railway 100% 可用）
import os
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

# ================== 改这里 ==================
TOKEN = "8515162052:AAFyZu2oKv9CjgtKaA0nQHc-PydLRaV5BZI"                 # ← 改你的 Bot Token
ADMIN_ID = 1480512549                # ← 改你的 Telegram 数字ID
# ===========================================

# 自定义 /2026 命令的回复内容（支持文字、图片、视频）
CUSTOM_2026_REPLY = {
    "type": "text",                     # 可选: "text", "photo", "video"
    "content": "您的账单查询结果如下：\n\n订单号：202608888888\n金额：¥ 299.00\n状态：已完成\n付款时间：2025-04-01 14:32",  # 如果是 text 就填这里
    # "content": "AgACAgQ..." ,      # 如果是 photo/video 就填 file_id
    # "caption": "账单详情"           # 可选 caption
}

# 开始
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("请输入账单订单号")

# 订单号识别
async def handle_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text.startswith("20260"):
        await update.message.reply_text(
            "账单订单号识别成功！\n请稍等，正在为您查询..."
        )
        # 自动触发 /2026 命令
        await cmd_2026(update, context)
        return

    # 识别失败
    await update.message.reply_text("识别失败，请重新输入。")

# /2026 命令（用户输入订单号后自动触发）
async def cmd_2026(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = CUSTOM_2026_REPLY

    if reply["type"] == "text":
        await update.message.reply_text(reply["content"])
    elif reply["type"] == "photo":
        await update.message.reply_photo(
            photo=reply["content"],
            caption=reply.get("caption", "")
        )
    elif reply["type"] == "video":
        await update.message.reply_video(
            video=reply["content"],
            caption=reply.get("caption", "")
        )

# ================== 管理员快速修改 /2026 内容 ==================
async def set2026(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        "请发送你要设置的 /2026 命令回复内容（文字/图片/视频）\n"
        "发送后我会自动保存为 /2026 的回复"
    )
    context.user_data["setting_2026"] = True

async def save_2026_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.user_data.get("setting_2026"):
        return

    context.user_data["setting_2026"] = False

    if update.message.text:
        CUSTOM_2026_REPLY.update({
            "type": "text",
            "content": update.message.text
        })
        await update.message.reply_text("已更新 /2026 为文字回复")

    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        CUSTOM_2026_REPLY.update({
            "type": "photo",
            "content": file_id,
            "caption": update.message.caption or ""
        })
        await update.message.reply_text("已更新 /2026 为图片回复")

    elif update.message.video:
        file_id = update.message.video.file_id
        CUSTOM_2026_REPLY.update({
            "type": "video",
            "content": file_id,
            "caption": update.message.caption or ""
        })
        await update.message.reply_text("已更新 /2026 为视频回复")

    else:
        await update.message.reply_text("请发送文字、图片或视频")

# ================== 主程序 ==================
def main():
    print("账单订单号 → 自动触发 /2026 机器人启动中...")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("2026", cmd_2026))        # 真实命令
    app.add_handler(CommandHandler("set2026", set2026))     # 管理员修改

    # 处理所有文字消息（订单号识别）
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_order))

    # 管理员设置新回复内容
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.TEXT, save_2026_reply))

    print("机器人已启动！输入 20260 开头订单号将自动触发 /2026")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
