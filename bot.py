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
GROUP_LINK = "https://t.me/+495j5rWmApsxYzg9"  # 固定的群组链接

# 图片URL配置
VIP_SERVICE_IMAGE_URL = "https://i.postimg.cc/QtkVBw7N/photo-2026-01-13-17-04-27.jpg"
SUCCESS_IMAGE_URL = "https://i.postimg.cc/QtkVBw7N/photo-2026-01-13-17-04-27.jpg"

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
                return False, f"⏳ 验证机会已用完，请 {hours}小时{minutes}分钟后再试"
        
        return True, ""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/start命令"""
    user = update.effective_user
    UserState.reset_attempts(user.id)
    
    # 精美欢迎消息
    welcome_message = """
🎉✨ *【VIP中转】欢迎您！* ✨🎉

🤖 我是守门员小卫，您的专属身份验证助手！

🌟 *我的职责：*
   • 快速验证您的身份
   • 为您开启VIP通道
   • 守护您的专属权益

💫 一键操作，轻松入群！
🎯 新朋友报到，小卫来帮忙！
🚀 尊贵体验，即刻开启！

🔐 小卫将引导您完成验证流程，请放心操作~
    """
    
    # 精美的按钮设计
    keyboard = [
        [InlineKeyboardButton("✨ 探索VIP特权 ✨", callback_data='vip_service')],
        [InlineKeyboardButton("📋 查看使用指南", callback_data='help_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(welcome_message, parse_mode='Markdown')
    await update.message.reply_text(
        "👇 请选择您需要的服务：",
        reply_markup=reply_markup
    )

async def vip_service_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """VIP服务说明 - 带图片"""
    query = update.callback_query
    await query.answer()
    
    # VIP特权说明 - 美化格式
    vip_message = """
🏆 *【VIP会员尊享特权】*

✅ *专属服务通道*
   🔹 专属高速中转通道
   🔹 优先审核快速入群
   🔹 专人客服随时待命

✅ *尊贵体验特权*
   🔹 7×24小时专属客服支持
   🔹 定期独家福利活动
   🔹 高级隐私保护服务

✅ *增值服务权益*
   🔹 优先体验新功能
   🔹 专属技术咨询服务
   🔹 会员专属社区交流

💫 立即验证身份，解锁全部特权！
    """
    
    # 精美按钮设计
    keyboard = [
        [InlineKeyboardButton("💰 已付款，开始验证", callback_data='start_verification')],
        [InlineKeyboardButton("🏠 返回主菜单", callback_data='restart')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        # 发送图片 + 文字 + 按钮
        await query.message.reply_photo(
            photo=VIP_SERVICE_IMAGE_URL,
            caption=vip_message,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        # 删除之前的消息
        await query.message.delete()
    except Exception as e:
        logger.error(f"发送图片失败: {e}")
        # 如果图片发送失败，发送纯文本版本
        await query.edit_message_text(
            vip_message,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def start_verification_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """开始验证流程"""
    query = update.callback_query
    await query.answer()
    
    # 精美的验证教程
    formatted_message = """
🔍 *【订单号查找指南】*

📱 请按照以下步骤查找您的订单号：

1️⃣ *进入个人中心*
   👉 点击【我的】进入个人页面

2️⃣ *查看账单记录*
   👉 选择【账单】查看支付记录

3️⃣ *打开详情页面*
   👉 点击【账单详情】查看详情

4️⃣ *查看更多信息*
   👉 点击【更多】查看更多选项

5️⃣ *复制订单号码*
   👉 找到【订单号】并完整复制

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
    
    await query.edit_message_text(formatted_message, parse_mode='Markdown')
    
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
            [InlineKeyboardButton("🔄 重新开始", callback_data='restart')],
            [InlineKeyboardButton("📞 联系客服", url="https://t.me/")]  # 请替换为实际客服链接
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"{message}\n\n📞 如需帮助请联系客服",
            reply_markup=reply_markup
        )
        return
    
    # 验证逻辑：检查是否以20260开头
    if order_number.startswith('20260'):
        # 验证成功
        UserState.reset_attempts(user_id)
        
        # 精美的成功消息
        success_message = f"""
🎊✨ *【验证成功】* ✨🎊

✅ *身份验证已完成*
   🎫 订单号：`{order_number}`
   👤 用户：{update.effective_user.first_name}
   ⏰ 时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}

🎉 *恭喜您！*
   已成功验证VIP身份！

🚀 *下一步操作*
   点击下方按钮立即加入VIP专属群组
        """
        
        keyboard = [
            [InlineKeyboardButton("🚀 立即加入VIP群", url=GROUP_LINK)],
            [InlineKeyboardButton("🎯 探索更多特权", callback_data='vip_service')],
            [InlineKeyboardButton("🏠 返回主菜单", callback_data='restart')]
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
            logger.error(f"发送成功图片失败: {e}")
            # 如果图片发送失败，发送纯文本版本
            await update.message.reply_text(
                success_message,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
    else:
        # 验证失败
        UserState.add_attempt(user_id)
        attempts_left = 2 - UserState.get_user_data(user_id)['attempts']
        
        if attempts_left > 0:
            # 还有尝试机会
            error_message = f"""
⚠️ *【验证未通过】*

❌ 未查询到有效的订单信息

📋 *输入内容：*
   `{order_number}`

🔄 *剩余验证机会：* {attempts_left}次

💡 *建议操作：*
   • 重新核对订单号是否正确
   • 确保完整复制了整个订单号
   • 确认订单号来自正确的支付记录

👇 请重新输入正确的订单号：
            """
            
            # 帮助按钮
            keyboard = [
                [InlineKeyboardButton("📖 查看查找教程", callback_data='show_tutorial')],
                [InlineKeyboardButton("🔄 重新输入", callback_data='retry_order')],
                [InlineKeyboardButton("📞 联系客服", url="https://t.me/")]  # 请替换为实际客服链接
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(error_message, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            # 无尝试机会
            lock_message = """
🔒 *【账户暂时锁定】*

⚠️ 验证机会已用完

⏳ *锁定说明：*
   • 已使用全部验证机会
   • 账户暂时无法验证
   • 系统保护机制已激活

🕐 *解锁时间：*
   15小时后自动解锁

📞 *紧急协助：*
   如需立即处理，请联系客服
            """
            
            keyboard = [
                [InlineKeyboardButton("📞 联系客服协助", url="https://t.me/")],  # 请替换为实际客服链接
                [InlineKeyboardButton("🔄 稍后重试", callback_data='restart')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(lock_message, reply_markup=reply_markup, parse_mode='Markdown')

async def show_tutorial_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示订单号查找教程"""
    query = update.callback_query
    await query.answer()
    
    tutorial_message = """
📚 *【详细查找教程】*

📍 *完整查找路径：*
   我的 → 账单 → 账单详情 → 更多 → 订单号

📝 *操作步骤详解：*

1. *进入个人中心*
   • 打开应用主界面
   • 点击底部导航栏【我的】

2. *查看账单记录*
   • 在个人中心页面找到【账单】
   • 点击进入账单列表

3. *打开账单详情*
   • 找到对应的付款记录
   • 点击【账单详情】查看详情

4. *查看更多信息*
   • 点击页面右上角或底部的【更多】
   • 展开更多操作选项

5. *复制订单号码*
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
    
    await query.edit_message_text(tutorial_message, parse_mode='Markdown')
    
    # 设置用户状态
    user_data = UserState.get_user_data(query.from_user.id)
    user_data['current_state'] = 'awaiting_order'

async def retry_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """重新输入订单号"""
    query = update.callback_query
    await query.answer()
    
    retry_message = """
🔄 *【重新输入订单号】*

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
    
    # 设置用户状态
    user_data = UserState.get_user_data(query.from_user.id)
    user_data['current_state'] = 'awaiting_order'

async def help_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """帮助菜单"""
    query = update.callback_query
    await query.answer()
    
    help_text = """
📚 *【使用指南】*

🤖 *机器人功能：*
   • 自动身份验证系统
   • VIP会员特权说明
   • 订单号验证服务

🔄 *完整流程：*
   1. 探索VIP特权
   2. 查看验证教程
   3. 输入订单号
   4. 验证成功后入群

🔍 *订单号查找：*
   我的 → 账单 → 账单详情 → 更多 → 订单号

⚠️ *注意事项：*
   • 每个用户有2次验证机会
   • 请确保订单号完全正确
   • 如有问题可联系客服

📞 *联系客服：*
   如需帮助，请联系专属客服
    """
    
    keyboard = [
        [InlineKeyboardButton("✨ 开始探索VIP", callback_data='vip_service')],
        [InlineKeyboardButton("🔙 返回上一页", callback_data='back_to_start')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(help_text, reply_markup=reply_markup, parse_mode='Markdown')

async def restart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """重新开始流程"""
    query = update.callback_query
    await query.answer()
    
    # 发送欢迎消息
    welcome_message = """
🔄 *流程已重置*

✨ 欢迎回到【VIP中转】！
🤖 我是守门员小卫，您的专属身份验证助手~

🎯 让我们重新开始体验尊贵服务！
    """
    
    keyboard = [
        [InlineKeyboardButton("✨ 探索VIP特权 ✨", callback_data='vip_service')],
        [InlineKeyboardButton("📋 查看使用指南", callback_data='help_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        welcome_message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    UserState.reset_attempts(query.from_user.id)

async def back_to_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """返回开始页面"""
    query = update.callback_query
    await query.answer()
    
    welcome_message = """
✨ *【VIP中转】欢迎您！*

🤖 我是守门员小卫，您的专属身份验证助手！

🌟 为您提供流畅的验证体验
🎯 助您快速加入VIP专属社区
🚀 尊贵服务，即刻开启！

👇 请选择您需要的服务：
    """
    
    keyboard = [
        [InlineKeyboardButton("✨ 探索VIP特权 ✨", callback_data='vip_service')],
        [InlineKeyboardButton("📋 查看使用指南", callback_data='help_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        welcome_message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """帮助命令"""
    help_text = """
🤖 *VIP中转机器人 - 使用指南*

✨ *可用命令：*
   /start - 开始使用机器人
   /help - 显示此帮助信息

🎯 *主要功能：*
   • VIP会员特权介绍
   • 身份验证系统
   • 订单号验证服务

📱 *操作流程：*
   1. 点击"探索VIP特权"
   2. 查看验证教程
   3. 输入订单号完成验证
   4. 成功加入VIP群组

💫 *温馨提示：*
   • 请确保网络连接稳定
   • 按照教程操作更顺畅
   • 遇到问题可联系客服

📞 *客服支持：*
   7×24小时专属客服为您服务
    """
    
    keyboard = [
        [InlineKeyboardButton("🚀 开始使用", callback_data='vip_service')],
        [InlineKeyboardButton("🏠 返回主菜单", callback_data='restart')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(help_text, reply_markup=reply_markup, parse_mode='Markdown')

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """错误处理"""
    logger.error(f"更新 {update} 导致错误 {context.error}")
    
    error_text = "❌ 抱歉，系统出现了一些问题，请稍后重试或联系客服"
    
    if update and update.effective_message:
        await update.effective_message.reply_text(error_text)

def main():
    """主函数"""
    # 检查Token是否设置
    if not BOT_TOKEN:
        print("❌ 错误：请在Railway的环境变量中设置 BOT_TOKEN")
        print("ℹ️ 提示：可以在Railway项目的Variables中添加BOT_TOKEN变量")
        return
    
    # 创建应用
    application = Application.builder().token(BOT_TOKEN).build()
    
    # 添加处理器
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    
    # 添加回调查询处理器
    application.add_handler(CallbackQueryHandler(vip_service_callback, pattern='^vip_service$'))
    application.add_handler(CallbackQueryHandler(start_verification_callback, pattern='^start_verification$'))
    application.add_handler(CallbackQueryHandler(show_tutorial_callback, pattern='^show_tutorial$'))
    application.add_handler(CallbackQueryHandler(retry_order_callback, pattern='^retry_order$'))
    application.add_handler(CallbackQueryHandler(restart_callback, pattern='^restart$'))
    application.add_handler(CallbackQueryHandler(help_menu_callback, pattern='^help_menu$'))
    application.add_handler(CallbackQueryHandler(back_to_start_callback, pattern='^back_to_start$'))
    
    # 添加消息处理器（处理订单号输入）
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_order_number))
    
    # 添加错误处理器
    application.add_error_handler(error_handler)
    
    # 启动机器人
    print("🤖 VIP中转机器人正在启动...")
    print("✨ 界面已美化，体验已优化")
    print("🎯 验证逻辑：检查订单号是否以20260开头")
    print("🚀 机器人已准备就绪！")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
