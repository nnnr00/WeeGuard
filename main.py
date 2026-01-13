import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Tuple
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    CallbackQueryHandler, 
    ContextTypes, 
    filters
)

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 常量定义
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')
GROUP_LINK = "https://t.me/+495j5rWmApsxYzg9"  # 固定的群组链接

# 用户状态存储
user_data_store: Dict[int, Dict] = {}

class UserState:
    """用户状态管理"""
    @staticmethod
    def get_user_data(user_id: int) -> Dict:
        if user_id not in user_data_store:
            user_data_store[user_id] = {
                'attempts': 0,
                'last_attempt': None,
                'current_state': 'start'
            }
        return user_data_store[user_id]
    
    @staticmethod
    def reset_attempts(user_id: int):
        data = UserState.get_user_data(user_id)
        data['attempts'] = 0
        data['last_attempt'] = None
    
    @staticmethod
    def add_attempt(user_id: int):
        data = UserState.get_user_data(user_id)
        data['attempts'] += 1
        data['last_attempt'] = datetime.now()
    
    @staticmethod
    def can_retry(user_id: int) -> Tuple[bool, str]:
        data = UserState.get_user_data(user_id)
        
        if data['attempts'] >= 2 and data['last_attempt']:
            time_passed = datetime.now() - data['last_attempt']
            if time_passed < timedelta(hours=15):
                remaining = timedelta(hours=15) - time_passed
                hours = int(remaining.total_seconds() // 3600)
                minutes = int((remaining.total_seconds() % 3600) // 60)
                return False, f"⏰ 请等待 {hours}小时{minutes}分钟后再试"
        
        return True, ""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/start命令"""
    user = update.effective_user
    UserState.reset_attempts(user.id)
    
    # 欢迎消息 - 使用更美观的格式
    welcome_message = """
╔══════════════════════════════════╗
║       👋 欢迎加入【VIP中转】      ║
╠══════════════════════════════════╣
║    我是守门员小卫，你的身份       ║
║        验证小助手~               ║
╚══════════════════════════════════╝

📢 小卫小卫，守门员小卫！
🚀 一键入群，小卫帮你搞定！
👤 新人来报到，小卫查身份！
    """
    
    # 直接显示服务选择按钮
    keyboard = [
        [InlineKeyboardButton("🌟 点此加入VIP", callback_data='vip_service')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(welcome_message)
    await update.message.reply_text(
        "请选择你需要的服务：",
        reply_markup=reply_markup
    )

async def vip_service_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """VIP服务说明"""
    query = update.callback_query
    await query.answer()
    
    # VIP特权说明 - 美化格式
    vip_message = """
┌──────────────────────────────────┐
│        💎 VIP会员特权说明         │
├──────────────────────────────────┤
│   ✅ 专属高速中转通道             │
│   ✅ 优先快速审核入群             │
│   ✅ 7x24小时客服支持             │
│   ✅ 定期独家福利活动             │
└──────────────────────────────────┘

👉 完成付款后即可开始验证流程
    """
    
    keyboard = [
        [InlineKeyboardButton("💳 我已付款，开始验证", callback_data='start_verification')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        vip_message,
        reply_markup=reply_markup
    )

async def start_verification_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """开始验证流程"""
    query = update.callback_query
    await query.answer()
    
    # 验证流程说明
    verification_message = """
┌──────────────────────────────────┐
│        📋 验证流程                │
├──────────────────────────────────┤
│   🔹 输入你的订单号               │
│   🔹 系统自动验证                 │
│   🔹 验证成功加入VIP群            │
└──────────────────────────────────┘

⚠️ 注意事项：
   • 每个用户有2次验证机会
   • 2次失败后需等待15小时
   • 请确保订单号正确无误

👇 请在下方输入你的订单号：
    """
    
    await query.edit_message_text(verification_message)
    
    # 设置用户状态
    user_data = UserState.get_user_data(query.from_user.id)
    user_data['current_state'] = 'awaiting_order'

async def handle_order_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理用户输入的订单号"""
    user_id = update.effective_user.id
    order_number = update.message.text.strip()
    
    # 检查是否可以重试
    can_retry, message = UserState.can_retry(user_id)
    if not can_retry:
        keyboard = [
            [InlineKeyboardButton("🔄 重新开始", callback_data='restart')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"{message}\n\n点击下方按钮重新开始：",
            reply_markup=reply_markup
        )
        return
    
    # 模拟验证逻辑 - 这里可以替换为实际验证逻辑
    # 简化的验证：只要订单号长度在10-15位且包含数字
    is_valid = (10 <= len(order_number) <= 15 and 
                any(char.isdigit() for char in order_number))
    
    if is_valid:
        # 验证成功
        UserState.reset_attempts(user_id)
        
        # 成功消息
        success_message = """
╔══════════════════════════════════╗
║        ✅ 验证成功！             ║
╠══════════════════════════════════╣
║   🎫 订单号：{}                 ║
║   👤 用户：{}                 ║
║   ⏰ 时间：{}       ║
╚══════════════════════════════════╝

欢迎加入VIP大家庭！ 🎉
        """.format(
            order_number,
            update.effective_user.first_name,
            datetime.now().strftime('%Y-%m-%d %H:%M')
        )
        
        keyboard = [
            [InlineKeyboardButton("🚀 立即加入VIP群", url=GROUP_LINK)],
            [InlineKeyboardButton("🏠 返回主菜单", callback_data='restart')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(success_message, reply_markup=reply_markup)
        
        # 通知管理员
        if ADMIN_CHAT_ID:
            admin_message = f"""
📋 新用户验证成功
👤 用户：{update.effective_user.first_name}
🆔 ID：{update.effective_user.id}
🎫 订单号：{order_number}
⏰ 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            """
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=admin_message
            )
            
    else:
        # 验证失败
        UserState.add_attempt(user_id)
        attempts_left = 2 - UserState.get_user_data(user_id)['attempts']
        
        if attempts_left > 0:
            # 还有尝试机会
            error_message = """
⚠️ 未查询到订单信息，请重试

📝 你输入的订单号：{}
🔄 剩余尝试次数：{}次

请重新输入正确的订单号：
            """.format(order_number, attempts_left)
            
            await update.message.reply_text(error_message)
        else:
            # 无尝试机会
            lock_message = """
🔒 验证失败次数过多
⏰ 账户已被临时锁定

请等待15小时后重试
或联系管理员处理
            """
            
            keyboard = [
                [InlineKeyboardButton("📞 联系管理员", url=f"https://t.me/{ADMIN_CHAT_ID}" if ADMIN_CHAT_ID else "#")],
                [InlineKeyboardButton("🔄 稍后重试", callback_data='restart')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(lock_message, reply_markup=reply_markup)

async def restart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """重新开始流程"""
    query = update.callback_query
    await query.answer()
    
    # 发送欢迎消息
    welcome_message = """
🔄 流程已重置

欢迎回到【VIP中转】！
我是守门员小卫，你的身份验证小助手~
    """
    
    keyboard = [
        [InlineKeyboardButton("🌟 点此加入VIP", callback_data='vip_service')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        welcome_message,
        reply_markup=reply_markup
    )
    
    UserState.reset_attempts(query.from_user.id)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """帮助命令"""
    help_text = """
🤖 VIP中转机器人 - 使用指南

可用命令：
/start - 开始使用机器人
/help - 显示此帮助信息

验证流程：
1. 点击"点此加入VIP"
2. 查看VIP特权
3. 点击"我已付款，开始验证"
4. 输入订单号
5. 验证成功后加入VIP群

联系管理员：
如有问题，请私信管理员处理
    """
    
    await update.message.reply_text(help_text)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """错误处理"""
    logger.error(f"更新 {update} 导致错误 {context.error}")
    
    error_text = "❌ 抱歉，出现了系统错误，请稍后重试或联系管理员"
    
    if update and update.effective_message:
        await update.effective_message.reply_text(error_text)

def main():
    """主函数"""
    # 创建应用
    application = Application.builder().token(BOT_TOKEN).build()
    
    # 添加处理器
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    
    # 添加回调查询处理器
    application.add_handler(CallbackQueryHandler(vip_service_callback, pattern='^vip_service$'))
    application.add_handler(CallbackQueryHandler(start_verification_callback, pattern='^start_verification$'))
    application.add_handler(CallbackQueryHandler(restart_callback, pattern='^restart$'))
    
    # 添加消息处理器（处理订单号输入）
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_order_number))
    
    # 添加错误处理器
    application.add_error_handler(error_handler)
    
    # 启动机器人
    print("🤖 VIP中转机器人已启动...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
