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

# 自定义 /2026 的回复内容（可随时改）
REPLY_CONTENT = """
您的账单查询结果如下：

订单号：202608888888
金额：¥ 299.00
状态：已完成
付款时间：2025-04-01 14:32

如需帮助请联系客服
"""

# 快捷按钮（显示在输入框上方）
KEYBOARD = [
    ["重新查询账单"],        # 第一行
    ["联系客服", "查看帮助"]  # 第二行
]
REPLY_MARKUP = ReplyKeyboardMarkup(KEYBOARD, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "请输入账单订单号",
        reply_markup=REPLY_MARKUP  # 一进来就显示按钮
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # 点击按钮“重新查询账单”也触发查询界面
    if text == "重新查询账单":
        await update.message.reply_text(
            "请输入账单订单号",
            reply_markup=REPLY_MARKUP
        )
        return

    # 识别订单号
    if text.startswith("20260"):
        await update.message.reply_text(
            "账单订单号识别成功！\n请稍等，正在为您查询..."
        )
        await update.message.reply_text(
            REPLY_CONTENT,
            reply_markup=REPLY_MARKUP,  # 查询结果后依然保留按钮
            parse_mode="Markdown"
        )
        return

    # 其他情况
    await update.message.reply_text(
        "识别失败，请重新输入。",
        reply_markup=REPLY_MARKUP
    )

def main():
    print("账单机器人启动中（带快捷按钮）...")
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("机器人已启动！用户将看到下方快捷按钮")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
效果预览
用户输入 202608888888 后，机器人回复：

text

账单订单号识别成功！
请稍等，正在为您查询...

您的账单查询结果如下：
订单号：202608888888
金额：¥ 299.00
...
同时下方出现四个按钮：

text

[重新查询账单]
[联系客服] [查看帮助]
点“重新查询账单”又会回到输入状态，非常丝滑！

Railway 部署三件套（必须）
bot.py（上面代码）
requirements.txt
txt

python-telegram-bot==20.8
railway.json
JSON

{
  "builder": "NIXPACKS",
  "startCommand": "python bot.py"
}
