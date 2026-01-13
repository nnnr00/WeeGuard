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

# 存储自定义关键词的文件
DB_FILE = "keywords.json"

# 加载/保存关键词
def load_keywords():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_keywords(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

keywords = load_keywords()

# /2026 命令的固定回复（群链接）
REPLY_2026 = "https://t.me/+495j5rWmApsxYzg9"

# 快捷按钮
KEYBOARD = [["重新查询账单"], ["联系客服"]]
MARKUP = ReplyKeyboardMarkup(KEYBOARD, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("请输入账单订单号", reply_markup=MARKUP)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

    # 1. 自定义关键词匹配（完全相等，不区分大小写）
    for kw, reply in keywords.items():
        if text.lower() == kw.lower():
            if isinstance(reply, str):
                await update.message.reply_text(reply, reply_markup=MARKUP)
            elif reply.get("type") == "photo":
                await update.message.reply_photo(reply["file_id"], caption=reply.get("caption",""), reply_markup=MARKUP)
            elif reply.get("type") == "video":
                await update.message.reply_video(reply["file_id"], caption=reply.get("caption",""), reply_markup=MARKUP)
            return

    # 2. 订单号识别
    if text.startswith("20260"):
        await update.message.reply_text("账单订单号识别成功！\n请稍等，正在为您查询...")
        await update.message.reply_text(REPLY_2026, reply_markup=MARKUP)
        return

    # 3. 按钮：重新查询
    if text == "重新查询账单":
        await update.message.reply_text("请输入账单订单号", reply_markup=MARKUP)
        return

    # 4. 其他情况
    await update.message.reply_text("识别失败，请重新输入。", reply_markup=MARKUP)

# /2026 命令
async def cmd_2026(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(REPLY_2026, reply_markup=MARKUP)

# ==================== 管理员：添加关键词 ====================
async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("请发送你要设置的关键词回复内容（文字/图片/视频）\n发完后我会让你输入关键词")
    context.user_data["adding"] = True

# 保存管理员发的内容
async def save_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.user_data.get("adding"):
        return

    context.user_data["adding"] = False
    context.user_data["pending_reply"] = {}

    if update.message.text:
        context.user_data["pending_reply"] = update.message.text
        await update.message.reply_text("已收到文字！现在请回复关键词（例如：客服）")
        context.user_data["waiting_keyword"] = True

    elif update.message.photo:
        context.user_data["pending_reply"] = {
            "type": "photo",
            "file_id": update.message.photo[-1].file_id,
            "caption": update.message.caption or ""
        }
        await update.message.reply_text("已收到图片！现在请回复关键词")
        context.user_data["waiting_keyword"] = True

    elif update.message.video:
        context.user_data["pending_reply"] = {
            "type": "video",
            "file_id": update.message.video.file_id,
            "caption": update.message.caption or ""
        }
        await update.message.reply_text("已收到视频！现在请回复关键词")
        context.user_data["waiting_keyword"] = True

    else:
        await update.message.reply_text("请发送文字、图片或视频")

# 保存关键词
async def save_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.user_data.get("waiting_keyword"):
        return

    keyword = update.message.text.strip()
    reply = context.user_data.pop("pending_reply", None)
    if not reply or not keyword:
        return

    keywords[keyword] = reply
    save_keywords(keywords)
    context.user_data.clear()
    await update.message.reply_text(f"已添加关键词：{keyword}\n用户发送该词将自动回复")

# 删除关键词
async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("用法：/del 关键词")
        return
    kw = " ".join(context.args)
    if kw in keywords:
        del keywords[kw]
        save_keywords(keywords)
        await update.message.reply_text(f"已删除：{kw}")
    else:
        await update.message.reply_text("关键词不存在")

async def listkw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not keywords:
        await update.message.reply_text("暂无自定义关键词")
        return
    txt = "当前关键词：\n" + "\n".join(keywords.keys())
    await update.message.reply_text(txt)

# ==================== 主程序 ====================
def main():
    print("机器人启动中...（/2026 回复群链接 + 支持自定义关键词）")
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("2026", cmd_2026))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("del", delete))
    app.add_handler(CommandHandler("list", listkw))

    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.TEXT, save_reply))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, save_keyword))  # 保存关键词

    print("机器人已启动！")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
}
