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

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 从环境变量获取配置
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROUP_LINK = "https://t.me/+495j5rWmApsxYzg9"

# 使用你提供的图片链接
VIP_SERVICE_IMAGE_URL = "https://i.postimg.cc/zBYtqtKb/photo-2026-01-13-17-04-32.jpg"  # VIP特权图片
TUTORIAL_IMAGE_URL = ""    # 订单号查找教程图片

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
    
    # 欢迎消息
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
        logger.error(f"发送欢迎消息失败: {e}")
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
        [InlineKeyboardButton("✅ 我已付款，开始验证", callback_data='start_verification')],
        [InlineKeyboardButton("🔙 返回", callback_data='restart')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        # 直接发送新消息，包含VIP特权图片
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=VIP_SERVICE_IMAGE_URL,
            caption=vip_message,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
        # 尝试删除之前的消息
        try:
            await query.message.delete()
        except:
            pass
            
    except Exception as e:
        logger.error(f"发送VIP图片失败: {e}")
        
        # 如果图片失败，使用文本模式
        try:
            await query.edit_message_text(vip_message, reply_markup=reply_markup, parse_mode='Markdown')
        except Exception as edit_error:
            logger.error(f"编辑消息也失败: {edit_error}")
            # 如果编辑也失败，发送新消息
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=vip_message,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )

async def start_verification_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """开始验证流程 - 带图片"""
    query = update.callback_query
    await query.answer("正在加载验证教程...")
    
    # 详细的订单号查找教程
    verification_message = """
📋 *如何查找订单号？*

请按照以下步骤查找您的订单号：

1️⃣ *第一步：进入个人中心*
   👉 打开应用，点击底部【我的】

2️⃣ *第二步：查看账单记录*
   👉 在个人中心找到【账单】选项并点击

3️⃣ *第三步：打开账单详情*
   👉 选择对应的付款记录，点击【账单详情】

4️⃣ *第四步：查看更多信息*
   👉 点击页面上的【更多】按钮

5️⃣ *第五步：复制订单号*
   👉 找到【订单号】字段，长按选择【全部复制】

⚠️ *重要提醒*
   • 请完整复制整个订单号
   • 粘贴时不要添加空格
   • 确保订单号完全正确

🔄 *验证规则说明*
   • 每个用户有2次验证机会
   • 验证通过即可加入VIP群
   • 如有问题可联系客服协助

👇 *现在请在下方输入您的订单号：*
    """
    
    keyboard = [
        [InlineKeyboardButton("🔙 返回上一步", callback_data='vip_service')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        # 发送订单号查找教程图片
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=TUTORIAL_IMAGE_URL,
            caption=verification_message,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
        # 尝试删除之前的消息
        try:
            await query.message.delete()
        except:
            pass
            
    except Exception as e:
        logger.error(f"发送教程图片失败: {e}")
        
        # 如果图片失败，使用文本模式
        try:
            await query.edit_message_text(verification_message, reply_markup=reply_markup, parse_mode='Markdown')
        except Exception as edit_error:
            logger.error(f"编辑消息也失败: {edit_error}")
            # 如果编辑也失败，发送新消息
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=verification_message,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
    
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
        
        # 验证成功后只发送文本，不再发送图片
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
📚 *订单号查找详细教程*

📍 *完整查找路径：*
我的 → 账单 → 账单详情 → 更多 → 订单号

📝 *操作步骤详解：*

1️⃣ *进入个人中心*
   • 打开应用主界面
   • 点击底部导航栏【我的】

2️⃣ *查看账单记录*
   • 在个人中心页面找到【账单】
   • 点击进入账单列表

3️⃣ *打开账单详情*
   • 找到对应的付款记录
   • 点击【账单详情】查看详情

4️⃣ *查看更多信息*
   • 点击页面右上角或底部的【更多】
   • 展开更多操作选项

5️⃣ *复制订单号码*
   • 在详情中找到【订单号】字段
   • 长按订单号选择【全部复制】

⚠️ *注意事项：*
   • 请确保复制完整的订单号
   • 不要手动输入，避免错误
   • 直接从应用中复制粘贴

🔍 *常见问题：*
   • 找不到订单号？请检查所有账单记录
   • 订单号不完整？请确保选择了"全部复制"
   • 仍然有问题？请联系客服协助

👇 请重新输入您的订单号：
    """
    
    keyboard = [
        [InlineKeyboardButton("🔙 返回验证流程", callback_data='start_verification')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(tutorial, reply_markup=reply_markup, parse_mode='Markdown')

async def retry_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """重新输入"""
    query = update.callback_query
    await query.answer("重新输入")
    
    retry_message = """
🔄 *重新输入订单号*

💡 *输入提示：*
   • 请粘贴您从应用中复制的订单号
   • 确保订单号完全正确
   • 不要添加任何空格或特殊字符

📱 *操作建议：*
   • 直接从应用复制后粘贴
   • 不要手动输入避免错误
   • 确认复制了整个订单号

👇 *现在请输入您的订单号：*
    """
    
    await query.edit_message_text(retry_message, parse_mode='Markdown')

async def help_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """帮助菜单"""
    query = update.callback_query
    await query.answer("帮助信息")
    
    help_text = """
🤖 *使用帮助*

🚀 *验证流程：*
1. 点击"开始验证"
2. 查看VIP特权
3. 点击"我已付款，开始验证"
4. 查看订单号查找方法
5. 输入订单号
6. 验证成功后加群

📋 *订单号查找路径：*
我的 → 账单 → 账单详情 → 更多 → 订单号

📞 *需要帮助？*
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
    print(f"VIP特权图片URL: {VIP_SERVICE_IMAGE_URL}")
    print(f"订单号查找教程图片URL: {TUTORIAL_IMAGE_URL}")
    
    # 启动机器人
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )

if __name__ == '__main__':
    main()
