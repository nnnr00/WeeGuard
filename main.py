import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# 配置日志
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# 从环境变量读取配置
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("机器人已启动。")

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 权限检查
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ 您没有管理员权限。")
        return

    # 创建按钮
    keyboard = [[InlineKeyboardButton("获取文件 ID", callback_data='get_file_id')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🛠 管理员后台：\n请选择操作：", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'get_file_id':
        # 设置一个临时状态，告知下一步需要发送文件
        context.user_data['waiting_for_file'] = True
        await query.edit_message_text("请发送你想获取 ID 的文件（图片、视频或文档）：")

async def file_receiver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 检查是否处于等待文件的状态
    if context.user_data.get('waiting_for_file'):
        file_id = ""
        
        # 识别不同类型的文件 ID
        if update.message.document:
            file_id = update.message.document.file_id
        elif update.message.photo:
            file_id = update.message.photo[-1].file_id  # 获取最高画质
        elif update.message.video:
            file_id = update.message.video.file_id
        
        if file_id:
            await update.message.reply_text(f"✅ 获取成功！\n\n`{file_id}`", parse_mode='Markdown')
            # 关键：获取一次后立即重置状态
            context.user_data['waiting_for_file'] = False
        else:
            await update.message.reply_text("请发送有效的文件格式。")

if __name__ == '__main__':
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    # 监听所有文件/图片/视频
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, file_receiver))
    
    # Railway 部署通常使用轮询或 Webhook，此处以简单的轮询为例
    application.run_polling()
