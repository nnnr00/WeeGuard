# bot.py —— 自动触发 /2026 命令版（Railway 100% 可用）
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import json
import os

# ================== 改这里 ==================
TOKEN = "8515162052:AAFyZu2oKv9CjgtKaA0nQHc-PydLRaV5BZI"                 # ← 改你的 Bot Token
ADMIN_ID = 1480512549                # ← 改你的 Telegram 数字ID
# ===========================================

DB_FILE = "keywords.json"

def load_keywords():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_keywords(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

keywords = load_keywords()

# /2026 固定回复内容（你想要的群链接）
REPLY_2026 = "https://t.me/+495j5rWmApsxYzg9"

# 快捷按钮
KEYBOARD = [["重新查询账单"], ["联系客服"]]
MARKUP = ReplyKeyboardMarkup(KEYBOARD, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("请输入账单订单号", reply_markup=MARKUP)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # 自定义关键词
    for kw, reply in keywords.items():
        if text.lower() == kw.lower():
            if isinstance(reply, str):
                await update.message.reply_text(reply, reply_markup=MARKUP)
            elif reply.get("type") == "photo":
                await update.message.reply_photo(reply["file_id"], caption=reply.get("caption",""), reply_markup=MARKUP)
            elif reply.get("type") == "video":
                await update.message.reply_video(reply["file_id"], caption=reply.get("caption",""), reply_markup=MARKUP)
            return

    # 订单号识别
    if text.startswith("20260"):
        await update.message.reply_text("账单订单号识别成功！\n请稍等，正在为您查询...")
        await update.message.reply_text(REPLY_2026, reply_markup=MARKUP)
        return

    if text == "重新查询账单":
        await update.message.reply_text("请输入账单订单号", reply_markup=MARKUP)
        return

    await update.message.reply_text("识别失败，请重新输入。", reply_markup=MARKUP)

async def cmd_2026(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(REPLY_2026, reply_markup=MARKUP)

# ==================== 管理员功能 ====================
async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("请发送关键词回复内容（文字/图片/视频）\n发完后我会让你输入关键词")
    context.user_data["adding"] = True

async def save_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.user_data.get("adding"):
        return
    context.user_data["adding"] = False

    if update.message.text:
        context.user_data["pending"] = update.message.text
        await update.message.reply_text("已收到文字！现在请回复关键词（如：帮助）")
    elif update.message.photo:
        context.user_data["pending"] = {
            "type": "photo",
            "file_id": update.message.photo[-1].file_id,
            "caption": update.message.caption or ""
        }
        await update.message.reply_text("已收到图片！现在请回复关键词")
    elif update.message.video:
        context.user_data["pending"] = {
            "type": "video",
            "file_id": update.message.video.file_id,
            "caption": update.message.caption or ""
        }
        await update.message.reply_text("已收到视频！现在请回复关键词")
    else:
        await update.message.reply_text("请发送内容")
    context.user_data["wait_kw"] = True

async def save_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.user_data.get("wait_kw"):
        return
    kw = update.message.text.strip()
    reply = context.user_data.pop("pending", None)
    if kw and reply:
        keywords[kw] = reply
        save_keywords(keywords)
        await update.message.reply_text(f"关键词添加成功：{kw}")
    context.user_data.clear()

async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if context.args:
        kw = " ".join(context.args)
        if kw in keywords:
            del keywords[kw]
            save_keywords(keywords)
            await update.message.reply_text(f"已删除：{kw}")
        else:
            await update.message.reply_text("关键词不存在")

def main():
    print("机器人启动成功！")
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("2026", cmd_2026))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("del", delete))

    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.TEXT, save_reply))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, save_keyword))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
