# main.py
import os
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes
)

# 加载 .env 文件（仅用于本地开发）
load_dotenv()

# === 图片链接（替换成你上传到 Telegraph 的真实图片直链）===
VIP_IMAGE_URL = "https://telegra.ph/file/your-vip-image.jpg"          # ← 替换为你自己的图
ORDER_GUIDE_IMAGE_URL = "https://telegra.ph/file/your-order-guide.jpg"  # ← 替换为你自己的图

# === 文本内容 ===
WELCOME_MESSAGE = """👋 欢迎加入【守门员小卫】！我是守门员小卫，你的身份验证小助手~

📢 小卫小卫，守门员小卫！
- 一键入群，小卫帮你搞定！
- 新人来报到，小卫查身份！"""

SERVICE_TEXT = "请选择服务类型："

VIP_CAPTION = """💎 VIP会员特权说明：
✅ 专属中转通道
✅ 优先审核入群
✅ 7x24小时客服支持
✅ 定期福利活动

👉 请私信管理员开通：@YourAdminUsername"""

PAYMENT_DONE_TEXT = "🎉 付款后请点击下方按钮开始验证"

ORDER_GUIDE_CAPTION = """1️⃣ 发送你的订单号
订单号在 我的 - 账单 - 账单详情 - 更多 - 订单号  全部复制

2️⃣ 审核通过后自动入群
⏱️ 审核通常在1-5分钟内完成

➡️ 请直接发送账单订单编号："""

SUCCESS_TEXT = "🎉 验证成功！点击下方按钮加入群组 👇"
SUCCESS_BUTTON_TEXT = "🚀 立即入群"
GROUP_LINK = "https://t.me/+495j5rWmApsxYzg9"

FAIL_TEXT = "❌ 订单号无效，请确认是否以 20260 开头并重试。"

# === 处理 /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_MESSAGE)

# === 处理 /a 命令 ===
async def command_a(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("1️⃣ 点此加入VIP", callback_data="show_vip")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(SERVICE_TEXT, reply_markup=reply_markup)

# === 处理按钮点击 ===
async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "show_vip":
        # 发送第一条图文：VIP介绍
        await query.message.reply_photo(photo=VIP_IMAGE_URL, caption=VIP_CAPTION)

        # 发送第二条消息：付款确认按钮
        keyboard = [[InlineKeyboardButton("✅ 我已付款，开始验证", callback_data="start_order_verify")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(PAYMENT_DONE_TEXT, reply_markup=reply_markup)

    elif query.data == "start_order_verify":
        # 发送第三条图文：订单指引
        await query.message.reply_photo(photo=ORDER_GUIDE_IMAGE_URL, caption=ORDER_GUIDE_CAPTION)

        # 设置状态：等待用户发送订单号
        context.user_data['awaiting'] = 'order_id'

# === 处理用户发送的订单号 ===
async def handle_order_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('awaiting') != 'order_id':
        return  # 不在等待阶段，忽略

    user_text = update.message.text.strip()
    context.user_data['awaiting'] = None  # 清除状态

    if user_text.startswith("20260"):
        # 验证成功 → 发送入群按钮
        keyboard = [[InlineKeyboardButton(SUCCESS_BUTTON_TEXT, url=GROUP_LINK)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(SUCCESS_TEXT, reply_markup=reply_markup)
    else:
        # 验证失败
        await update.message.reply_text(FAIL_TEXT)

# === 主函数（含超强错误提示）===
def main():
    print("🚀 正在启动守门员小卫机器人...")

    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

    if not TOKEN:
        print("\n" + "="*60)
        print("❌ 致命错误：未找到 Telegram Bot Token！")
        print("="*60)
        print("请按以下步骤解决：")
        print("1️⃣ 登录 Railway 控制台 → 进入你的项目")
        print("2️⃣ 点击左侧菜单 'Variables'")
        print("3️⃣ 添加变量：")
        print("   Key: TELEGRAM_BOT_TOKEN")
        print("   Value: 你的BotToken（如 123456789:ABCdefGhI...）")
        print("4️⃣ 点击 'Add' 保存")
        print("5️⃣ 重要❗：点击顶部 'Deployments' → 'Trigger Deploy'")
        print("6️⃣ 等待重新部署完成")
        print("\n💡 提示：Token 从 @BotFather 获取")
        print("="*60 + "\n")
        return

    print(f"✅ 成功加载 Bot Token: {TOKEN[:5]}...{TOKEN[-5:]}")

    application = Application.builder().token(TOKEN).build()

    # 注册处理器
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("a", command_a))
    application.add_handler(CallbackQueryHandler(handle_button_click))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_order_id))

    print("🤖 守门员小卫已上线！等待用户指令中...")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
