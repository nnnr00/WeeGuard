import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
import os

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 用户状态管理
user_states: Dict[int, Dict] = {}
ORDER_PREFIX = "20260"
MAX_ATTEMPTS = 2
LOCKOUT_TIME = timedelta(hours=15)

# 图片URL（请替换为你自己的图片链接）
VIP_SERVICE_IMAGE_URL = "https://i.postimg.cc/QtkVBw7N/photo-2026-01-13-17-04-27.jpg"
SUCCESS_IMAGE_URL = "https://i.postimg.cc/QtkVBw7N/photo-2026-01-13-17-04-27.jpg"

# 欢迎消息
WELCOME_MESSAGE = """
🌟 **欢迎来到VIP中转中心！**

👋 你好，我是守门员**小卫**，你的专属身份验证助手！

✨ **我能为你做什么？**
• 🔒 验证VIP会员身份
• 🚪 快速接入专属频道
• 🛡️ 保障社群安全环境
• 💫 提供尊贵会员体验

📢 **小卫口令**：新人报到，一键验证！
"""

# VIP特权说明
VIP_PRIVILEGES = """
🎯 **VIP尊享特权列表**

✅ **专属快速通道**
    ⚡ 高速稳定中转服务
    📶 优先网络资源分配

✅ **优先审核入群**
    🎫 快速身份验证
    🚀 即时通道开通

✅ **全天候客服支持**
    🕒 7×24小时在线协助
    💬 专属客服通道

✅ **定期福利活动**
    🎁 会员专属礼包
    🎉 优先活动参与权

💎 **升级VIP会员**，即刻尊享所有特权！
"""

# 验证教程
VERIFICATION_TUTORIAL = """
📋 **如何查找订单号？**

请按照以下步骤操作：

1️⃣ 点击右下角 **「我的」**
2️⃣ 进入 **「账单」** 页面
3️⃣ 选择 **「账单详情」**
4️⃣ 点击 **「更多」** 选项
5️⃣ 复制完整的 **「订单号」**

📍 **温馨提示**：
• 请确保复制完整的订单号
• 订单号通常由数字组成
• 如有疑问，可联系客服协助
"""

# 成功验证消息
SUCCESS_MESSAGE = """
✅ **身份验证成功！**

🎉 恭喜你，VIP会员身份已确认！

🌟 **欢迎加入VIP专属社群**
点击下方按钮，即刻进入会员专属通道：

👉 [VIP会员专属群](https://t.me/+495j5rWmApsxYzg9)

✨ 期待与你在社群相见！
"""

# 失败验证消息
FAILURE_MESSAGE = """
❌ **验证未通过**

⚠️ 未查询到对应的订单信息

🔍 **请检查以下事项**：
• 订单号是否完整复制
• 订单状态是否有效
• 是否已成功完成支付

🔄 请重新输入订单号，或联系客服协助
"""

# 尝试次数超过限制
LOCKOUT_MESSAGE = """
⏳ **验证次数超限**

🚫 您的验证尝试次数已达到上限

⏰ 请等待 **15小时** 后重新尝试
如需紧急协助，请联系客服处理
"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /start 命令"""
    user_id = update.effective_user.id
    
    # 初始化用户状态
    user_states[user_id] = {
        'attempts': 0,
        'first_attempt': None,
        'verified': False
    }
    
    # 发送欢迎消息
    await update.message.reply_text(
        WELCOME_MESSAGE,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🚪 进入验证流程", callback_data="start_verification")
        ]])
    )

async def start_verification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """开始验证流程"""
    query = update.callback_query
    await query.answer()
    
    # 发送VIP特权说明（带图片）
    await query.message.reply_photo(
        photo=VIP_IMAGE_URL,
        caption=VIP_PRIVILEGES,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("💳 我已付款，开始验证", callback_data="verify_payment")
        ]])
    )

async def verify_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """开始付款验证"""
    query = update.callback_query
    await query.answer()
    
    # 发送验证教程（带图片）
    await query.message.reply_photo(
        photo=VERIFY_IMAGE_URL,
        caption=VERIFICATION_TUTORIAL,
        parse_mode='Markdown'
    )
    
    # 请求输入订单号
    await query.message.reply_text(
        "🔢 **请输入您的订单号**\n\n"
        "请在下方输入完整的订单号进行验证：",
        parse_mode='Markdown'
    )

async def handle_order_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理订单号输入"""
    user_id = update.effective_user.id
    order_number = update.message.text.strip()
    
    # 获取或初始化用户状态
    if user_id not in user_states:
        user_states[user_id] = {
            'attempts': 0,
            'first_attempt': None,
            'verified': False
        }
    
    user_state = user_states[user_id]
    
    # 检查是否在锁定状态
    if user_state['first_attempt']:
        time_since_first_attempt = datetime.now() - user_state['first_attempt']
        if user_state['attempts'] >= MAX_ATTEMPTS and time_since_first_attempt < LOCKOUT_TIME:
            await update.message.reply_text(LOCKOUT_MESSAGE, parse_mode='Markdown')
            return
    
    # 记录第一次尝试时间
    if user_state['attempts'] == 0:
        user_state['first_attempt'] = datetime.now()
    
    # 检查订单号
    if order_number.startswith(ORDER_PREFIX):
        # 验证成功
        user_state['verified'] = True
        user_state['attempts'] = 0
        
        # 发送成功消息并加入群组
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🌟 加入VIP会员群", url="https://t.me/+495j5rWmApsxYzg9")
        ]])
        
        await update.message.reply_text(
            SUCCESS_MESSAGE,
            parse_mode='Markdown',
            reply_markup=keyboard,
            disable_web_page_preview=True
        )
    else:
        # 验证失败
        user_state['attempts'] += 1
        
        if user_state['attempts'] >= MAX_ATTEMPTS:
            # 超过尝试次数
            await update.message.reply_text(LOCKOUT_MESSAGE, parse_mode='Markdown')
        else:
            # 允许再次尝试
            remaining_attempts = MAX_ATTEMPTS - user_state['attempts']
            await update.message.reply_text(
                f"{FAILURE_MESSAGE}\n\n"
                f"🔄 **剩余尝试次数**：{remaining_attempts}次\n"
                f"请重新输入订单号：",
                parse_mode='Markdown'
            )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理按钮回调"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "start_verification":
        await start_verification(update, context)
    elif query.data == "verify_payment":
        await verify_payment(update, context)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """显示帮助信息"""
    help_text = """
🤖 **使用指南**

🔹 **开始流程**：发送 /start
🔹 **验证身份**：按照提示操作
🔹 **联系客服**：验证遇到问题时

💡 **温馨提示**：
• 请确保网络连接稳定
• 按照指引逐步操作
• 保存好订单信息
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

def main() -> None:
    """启动机器人"""
    # 从环境变量获取Token（Railway会自动设置）
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    
    if not BOT_TOKEN:
        logger.error("请设置 BOT_TOKEN 环境变量")
        return
    
    # 创建应用
    application = Application.builder().token(BOT_TOKEN).build()
    
    # 注册处理器
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_order_number))
    
    # 启动机器人
    port = int(os.environ.get("PORT", 8080))
    
    if "RAILWAY_ENVIRONMENT" in os.environ:
        # 在Railway上使用Webhook
        webhook_url = f"https://{os.environ.get('RAILWAY_PUBLIC_DOMAIN')}/"
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            webhook_url=webhook_url
        )
    else:
        # 本地开发使用轮询
        application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
