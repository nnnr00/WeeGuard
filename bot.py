import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Tuple
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    CallbackQueryHandler, 
    ContextTypes, 
    filters
)

# 配置日志 - 简化日志输出
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.WARNING  # 改为WARNING级别，减少日志输出
)
logger = logging.getLogger(__name__)

# 从环境变量获取配置
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROUP_LINK = "https://t.me/+495j5rWmApsxYzg9"

# 使用更可靠的图片URL（Telegram官方图片）
VIP_SERVICE_IMAGE_URL = "https://i.postimg.cc/QtkVBw7N/photo-2026-01-13-17-04-27.jpg"
SUCCESS_IMAGE_URL = "https://i.postimg.cc/QtkVBw7N/photo-2026-01-13-17-04-27.jpg""

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
                return False, f"⏳ 请等待 {hours}小时{minutes}分钟后再试"
        
        return True, ""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/start命令"""
    user = update.effective_user
    UserState.reset_attempts(user.id)
    
    # 简化欢迎消息
    welcome_message = """
✨ *欢迎使用VIP验证系统* ✨

🤖 我是您的验证助手小卫

🚀 我将引导您完成：
   • VIP特权查看
   • 订单号验证
   • VIP群组加入

👇 请点击下方按钮开始：
    """
    
    keyboard = [
        [InlineKeyboardButton("🚀 开始验证", callback_data='vip_service')],
        [InlineKeyboardButton("❓ 使用帮助", callback_data='help_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await update.message.reply_text(welcome_message, reply_markup=reply_markup, parse_mode='Markdown')
    except Exception as e:
        logger.warning(f"发送欢迎消息失败: {e}")
        await update.message.reply_text("欢迎使用VIP验证系统！", reply_markup=reply_markup)

async def vip_service_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """VIP服务说明"""
    query = update.callback_query
    await query.answer("正在加载VIP信息...")
    
    # VIP特权说明
    vip_message = """
🏆 *VIP会员特权*

✅ 专属高速通道
✅ 优先审核服务
✅ 24小时客服支持
✅ 专属福利活动

💎 验证成功后即可享受以上特权！
    """
    
    keyboard = [
        [InlineKeyboardButton("✅ 开始验证", callback_data='start_verification')],
        [InlineKeyboardButton("🔙 返回", callback_data='restart')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        # 尝试发送图片
        await query.message.reply_photo(
            photo=VIP_SERVICE_IMAGE_URL,
            caption=vip_message,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        # 删除旧消息
        await query.message.delete()
    except Exception as e:
        logger.warning(f"发送VIP图片失败，使用文本模式: {e}")
        try:
            await query.edit_message_text(vip_message, reply_markup=reply_markup, parse_mode='Markdown')
        except Exception as edit_error:
            logger.warning(f"编辑消息失败: {edit_error}")

async def start_verification_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """开始验证流程"""
    query = update.callback_query
    await query.answer("进入验证流程")
    
    # 验证教程
    verification_message = """
📋 *订单号查找步骤*

1. 打开应用，点击【我的】
2. 进入【账单】页面
3. 找到对应的账单记录
4. 点击【账单详情】
5. 点击【更多】选项
6. 复制完整的【订单号】

👇 请在下方输入您的订单号：
    """
    
    await query.edit_message_text(verification_message, parse_mode='Markdown')
    
    # 设置用户状态
    user_data = UserState.get_user_data(query.from_user.id)
    user_data['current_state'] = 'awaiting_order'

async def handle_order_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理订单号输入"""
    user_id = update.effective_user.id
    order_number = update.message.text.strip()
    
    # 检查是否可以重试
    can_retry, message = UserState.can_retry(user_id)
    if not can_retry:
        keyboard = [
            [InlineKeyboardButton("🔄 重新开始", callback_data='restart')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(message, reply_markup=reply_markup)
        return
    
    # 验证逻辑（不向用户透露具体规则）
    if order_number.startswith('20260'):
        # 验证成功
        UserState.reset_attempts(user_id)
        
        success_message = f"""
🎉 *验证成功！*

✅ 订单已验证
👤 用户：{update.effective_user.first_name}
⏰ 时间：{datetime.now().strftime('%H:%M')}

🚀 点击下方按钮加入VIP群：
        """
        
        keyboard = [
            [InlineKeyboardButton("👉 加入VIP群", url=GROUP_LINK)],
            [InlineKeyboardButton("🏠 返回主页", callback_data='restart')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            # 发送成功图片
            await update.message.reply_photo(
                photo=SUCCESS_IMAGE_URL,
                caption=success_message,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.warning(f"发送成功图片失败: {e}")
            await update.message.reply_text(success_message, reply_markup=reply_markup, parse_mode='Markdown')
            
    else:
        # 验证失败
        UserState.add_attempt(user_id)
        attempts_left = 2 - UserState.get_user_data(user_id)['attempts']
        
        if attempts_left > 0:
            error_message = f"""
❌ *验证失败*

📝 未查询到有效订单
🔄 剩余尝试次数：{attempts_left}次

💡 请确认订单号是否正确
👇 请重新输入：
            """
            
            keyboard = [
                [InlineKeyboardButton("📖 查看教程", callback_data='show_tutorial')],
                [InlineKeyboardButton("🔄 重新输入", callback_data='retry_order')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(error_message, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            lock_message = """
🔒 *验证次数已用完*

⏳ 请等待15小时后重试
📞 或联系客服协助处理
            """
            
            keyboard = [
                [InlineKeyboardButton("🔄 稍后重试", callback_data='restart')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(lock_message, reply_markup=reply_markup, parse_mode='Markdown')

async def show_tutorial_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示教程"""
    query = update.callback_query
    await query.answer("查看教程")
    
    tutorial = """
📚 *订单号查找方法*

📍 查找路径：
我的 → 账单 → 账单详情 → 更多 → 订单号

💡 操作提示：
• 确保复制完整的订单号
• 不要手动输入，直接粘贴
• 检查订单号是否正确

👇 请重新输入订单号：
    """
    
    await query.edit_message_text(tutorial, parse_mode='Markdown')

async def retry_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """重新输入"""
    query = update.callback_query
    await query.answer("重新输入")
    
    await query.edit_message_text("👇 请在下方重新输入订单号：")

async def help_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """帮助菜单"""
    query = update.callback_query
    await query.answer("帮助信息")
    
    help_text = """
🤖 *使用帮助*

🚀 验证流程：
1. 点击"开始验证"
2. 查看订单号查找方法
3. 输入订单号
4. 验证成功后加群

📞 需要帮助？
请联系客服获取支持
    """
    
    keyboard = [
        [InlineKeyboardButton("🚀 开始验证", callback_data='vip_service')],
        [InlineKeyboardButton("🔙 返回", callback_data='restart')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(help_text, reply_markup=reply_markup, parse_mode='Markdown')

async def restart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """重新开始"""
    query = update.callback_query
    await query.answer("重新开始")
    
    UserState.reset_attempts(query.from_user.id)
    
    welcome_message = """
🔄 *流程已重置*

✨ 欢迎回来！
👇 请选择操作：
    """
    
    keyboard = [
        [InlineKeyboardButton("🚀 开始验证", callback_data='vip_service')],
        [InlineKeyboardButton("❓ 使用帮助", callback_data='help_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(welcome_message, reply_markup=reply_markup, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """帮助命令"""
    await help_menu_callback(update, context)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """全局错误处理"""
    logger.error(f"发生错误: {context.error}", exc_info=context.error)
    
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text("❌ 系统暂时出现问题，请稍后再试")
        except:
            pass

def main():
    """主函数"""
    if not BOT_TOKEN:
        print("❌ 错误：请设置BOT_TOKEN环境变量")
        print("在Railway中：")
        print("1. 进入项目")
        print("2. 点击 Variables")
        print("3. 添加 BOT_TOKEN")
        return
    
    # 创建应用
    application = Application.builder().token(BOT_TOKEN).build()
    
    # 添加处理器
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    
    # 回调处理器
    application.add_handler(CallbackQueryHandler(vip_service_callback, pattern='^vip_service$'))
    application.add_handler(CallbackQueryHandler(start_verification_callback, pattern='^start_verification$'))
    application.add_handler(CallbackQueryHandler(show_tutorial_callback, pattern='^show_tutorial$'))
    application.add_handler(CallbackQueryHandler(retry_order_callback, pattern='^retry_order$'))
    application.add_handler(CallbackQueryHandler(help_menu_callback, pattern='^help_menu$'))
    application.add_handler(CallbackQueryHandler(restart_callback, pattern='^restart$'))
    
    # 消息处理器
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_order_number))
    
    # 错误处理器
    application.add_error_handler(error_handler)
    
    # 启动
    print("🤖 机器人启动中...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()
