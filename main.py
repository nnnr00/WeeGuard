import os
import logging
import random
import re # 用于解析Telegram消息链接
import asyncio # 用于异步删除消息
import asyncpg
from datetime import datetime, date, timedelta
import pytz

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo, ChatMember
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    JobQueue,
)

# --- [1] 可配置项/替换点 ---
# !!! 请务必替换以下占位符和配置 !!!

# Telegram Bot API Token 和 Admin ID (从 Railway 环境变量获取)
# BOT_TOKEN = os.getenv('BOT_TOKEN')
# ADMIN_ID = os.getenv('ADMIN_ID') # 你的 Telegram 用户ID (数字)

# 数据库连接字符串 (从 Railway 环境变量获取)
# DATABASE_URL = os.getenv('DATABASE_URL')

# File IDs (替换为你自己的 File ID，通过 /admin 获取)
# 如果图片失效，请使用 /admin 命令重新获取并替换
VIP_PERKS_FILE_ID = "AgACAgUAAxkBAAIBJ2loboOm15d-Qog2KkzAVSTLG-1eAAKaD2sbQNhBV_UKRl5JPolfAQADAgADeAADOAQ" # VIP特权图片 File ID
ORDER_TUTORIAL_FILE_ID = "AgACAgUAAxkBAAIBHWlobOW8SVMC9dk6aKquMiQHPh1AAKVD2sbQNhBV9mV11AQnf1xAQADAgADeQADOAQ" # VIP订单号查找教程图片 File ID
WECHAT_TOPUP_QR_FILE_ID = "AgACAgUAAxkBAAIBImlobmPLtn9DWUFZJ53t1mhkVIA7AAKYD2sbQNhBV_A-2IdqoG-dAQADAgADeAADOAQ"
WECHAT_ORDER_TUTORIAL_FILE_ID = "AgACAgUAAxkBAAIBLWlocIlhveHnlgntE7dGi1ri56i2AAKeD2sbQNhBVyZ8_L3zE7qwAQADAgADeQADOAQ"
ALIPAY_TOPUP_QR_FILE_ID = "AgACAgUAAxkBAAIBJWlobnt_eXxhfHqg5bpF8WFwDDESAAKZD2sbQNhBVyWCVUCv9Q3iAQADAgADeAADOAQ"
ALIPAY_ORDER_TUTORIAL_FILE_ID = "AgACAgUAAxkBAAIBMGlocJCdAlLyJie451mVeM6gi7xhAAKfD2sbQNhBV-EDx2qKNqc-AQADAgADeQADOAQ"

# 会员群组链接 (替换为你自己的会员群链接)
MEMBER_GROUP_LINK = "https://t.me/+495j5rWmApsxYzg9" # VIP会员群的邀请链接

# 允许机器人发送欢迎语的群组ID列表 (替换为你的群组ID，负数)
# 注意: 群组ID是负数，例如 -100XXXXXXXXXX
ALLOWED_WELCOME_GROUPS = {
    -1002520416718, # 示例群组ID 1 (替换为你实际的群组ID)
    -1002933211039  # 示例群组ID 2 (替换为你实际的群组ID)
}
WELCOME_BONUS_POINTS = 20 # 首次入群赠送积分数量

# VIP 验证相关配置
VIP_VALIDATION_ATTEMPTS_MAX = 2 # 订单号重试次数
VIP_VALIDATION_COOLDOWN_SECONDS = 5 * 3600  # 失败后冷却时间：5小时 (单位：秒)
VIP_ORDER_PREFIX = "20260" # VIP订单号开头 (用于内部匹配，用户不会看到此数字)

# 积分获取配置
DAILY_CHECK_IN_POINTS_MIN = 3 # 每日签到最少积分
DAILY_CHECK_IN_POINTS_MAX = 8 # 每日签到最多积分

# 充值相关配置
PAYMENT_ATTEMPTS_MAX = 2 # 充值订单号重试次数
PAYMENT_COOLDOWN_SECONDS = 10 * 3600 # 失败后冷却时间：10小时 (单位：秒)

WECHAT_TOPUP_POINTS = 100 # 微信充值获得积分
WECHAT_ORDER_PREFIX = "4200" # 微信订单号开头 (用于内部匹配，用户不会看到此数字)

ALIPAY_TOPUP_POINTS = 100 # 支付宝充值获得积分
ALIPAY_ORDER_PREFIX = "4768" # 支付宝订单号开头 (用于内部匹配，用户不会看到此数字)

# 转发消息相关配置
FORWARDED_MESSAGE_LIFETIME_SECONDS = 20 * 60 # 转发消息自动删除时间：20分钟 (单位：秒)
DELETE_CHECK_INTERVAL_SECONDS = 5 * 60 # 每5分钟检查一次待删除消息 (单位：秒)

# Telegram 消息链接正则表达式 (通常无需修改)
TELEGRAM_MESSAGE_LINK_REGEX = re.compile(r"https://t\.me/(?:c/)?(?:([\d]+)|([a-zA-Z0-9_]+))/([\d]+)")

# 定义时区 (例如 'Asia/Shanghai' 代表北京时间，通常无需修改)
TIMEZONE = pytz.timezone('Asia/Shanghai')

# --- 日志设置 ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- 全局变量 ---
db_pool = None # 数据库连接池
# 会话状态（用于 ConversationHandler，用于在多步交互中跟踪用户当前所处阶段）
ASKING_FOR_VIP_ORDER_ID = 1
ASKING_WECHAT_ORDER_ID = 2
ASKING_ALIPAY_ORDER_ID = 3
CONFIRM_REDEMPTION = 4

ADMIN_PRODUCT_ADD_ID = 101
ADMIN_PRODUCT_ADD_TYPE = 102
ADMIN_PRODUCT_ADD_CONTENT = 103
ADMIN_PRODUCT_ADD_POINTS_COST = 104

ADMIN_FWD_CMD_ADD_NAME = 201
ADMIN_FWD_CMD_ADD_LINKS = 202
ADMIN_FWD_CMD_MANAGE_CONFIRM_DELETE = 203


# --- [2] 辅助函数 ---
def get_env_variable(name: str) -> str:
    """从环境变量获取值，如果不存在则抛出错误。"""
    value = os.getenv(name)
    if not value:
        logger.error(f"Environment variable {name} not set.")
        raise ValueError(f"Environment variable {name} not set.")
    return value

# --- [3] 数据库操作函数 ---
async def init_db_pool():
    """初始化数据库连接池并创建表（如果不存在）。"""
    global db_pool
    DATABASE_URL = get_env_variable('DATABASE_URL')
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        logger.info("Database pool created successfully.")
        await create_tables()
        logger.info("Database tables checked/created.")
    except Exception as e:
        logger.error(f"Failed to connect to database or create pool: {e}")
        raise

async def close_db_pool():
    """关闭数据库连接池。"""
    global db_pool
    if db_pool:
        await db_pool.close()
        logger.info("Database pool closed.")

async def create_tables():
    """创建必要的数据库表。"""
    async with db_pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id BIGINT PRIMARY KEY,
                points INTEGER DEFAULT 0,
                total_acquired_points INTEGER DEFAULT 0, -- 用于排行榜，不随兑换减少
                last_check_in_date DATE,
                wechat_used BOOLEAN DEFAULT FALSE,
                alipay_used BOOLEAN DEFAULT FALSE,
                welcome_bonus_given BOOLEAN DEFAULT FALSE, -- 标记是否已获得首次入群积分
                joined_groups BIGINT[] DEFAULT ARRAY[]::BIGINT[], -- 存储用户所在受监控群组ID
                
                vip_validation_attempts INTEGER DEFAULT 0,
                vip_validation_cooldown_until TIMESTAMP WITH TIME ZONE,
                
                wechat_attempts INTEGER DEFAULT 0,
                wechat_cooldown_until TIMESTAMP WITH TIME ZONE,
                
                alipay_attempts INTEGER DEFAULT 0,
                alipay_cooldown_until TIMESTAMP WITH TIME ZONE
            );

            CREATE TABLE IF NOT EXISTS products (
                product_id TEXT PRIMARY KEY,
                type TEXT NOT NULL, -- 'text', 'photo', 'video'
                content TEXT NOT NULL, -- 文本内容或文件ID
                points_cost INTEGER NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS redemptions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id),
                product_id TEXT NOT NULL REFERENCES products(product_id),
                redeemed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                points_cost_at_redemption INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id),
                type TEXT NOT NULL, -- 'check_in', 'wechat_topup', 'alipay_topup', 'redeem', 'welcome_bonus', 'points_reset'
                amount INTEGER NOT NULL, -- 正数表示获得，负数表示消耗
                description TEXT,
                timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS forward_commands (
                command_name TEXT PRIMARY KEY, -- 存储为小写，用于匹配
                message_links TEXT[] NOT NULL, -- 存储原始链接
                parsed_messages JSONB[] NOT NULL, -- 存储解析后的 chat_id, message_id
                created_by BIGINT REFERENCES users(id),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS scheduled_deletions (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                message_id BIGINT NOT NULL,
                deletion_time TIMESTAMP WITH TIME ZONE NOT NULL
            );
        ''')

async def get_user_data(user_id: int):
    """从数据库获取用户数据，如果不存在则创建新用户。"""
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow('SELECT * FROM users WHERE id = $1', user_id)
        if user:
            return user
        else:
            await conn.execute(
                'INSERT INTO users (id) VALUES ($1)', user_id
            )
            return await conn.fetchrow('SELECT * FROM users WHERE id = $1', user_id)

async def update_user_data(user_id: int, **kwargs):
    """更新用户数据。"""
    async with db_pool.acquire() as conn:
        set_parts = []
        values = []
        i = 1
        for key, value in kwargs.items():
            set_parts.append(f"{key} = ${i}")
            values.append(value)
            i += 1
        
        if not set_parts:
            return
            
        values.append(user_id)
        query = f"UPDATE users SET {', '.join(set_parts)} WHERE id = ${i}"
        await conn.execute(query, *values)

async def add_transaction(user_id: int, type: str, amount: int, description: str):
    """添加积分交易记录。"""
    async with db_pool.acquire() as conn:
        await conn.execute(
            'INSERT INTO transactions (user_id, type, amount, description, timestamp) VALUES ($1, $2, $3, $4, $5)',
            user_id, type, amount, description, datetime.now(TIMEZONE)
        )

# --- [4] 定时任务函数 ---
async def delete_old_messages_job(context: ContextTypes.DEFAULT_TYPE):
    """JobQueue 任务：检查并删除过期的转发消息。"""
    bot = context.bot
    async with db_pool.acquire() as conn:
        # 获取所有已到删除时间的记录
        messages_to_delete = await conn.fetch(
            'SELECT id, chat_id, message_id FROM scheduled_deletions WHERE deletion_time <= $1',
            datetime.now(TIMEZONE)
        )
    
    for msg in messages_to_delete:
        try:
            await bot.delete_message(chat_id=msg['chat_id'], message_id=msg['message_id'])
            logger.info(f"Deleted message {msg['message_id']} in chat {msg['chat_id']}.")
            
            # 发送消息存在时间有限的提示
            # 确保只在私人聊天中发送提示，避免在群组中刷屏
            if msg['chat_id'] > 0: # Telegram user IDs are positive, group/channel IDs are negative
                await bot.send_message(
                    chat_id=msg['chat_id'],
                    text="⚠️ **温馨提示**\n\n您最近查看的消息已在20分钟后自动删除，为保障内容私密性。您可以在【积分中心】兑换处重新获取（已兑换商品无需二次付费即可再次查看）。",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 返回首页", callback_data="start_command")]])
                )
        except Exception as e:
            logger.warning(f"Failed to delete message {msg['message_id']} in chat {msg['chat_id']}: {e}")
        finally:
            # 无论是否成功删除，都从数据库中移除记录
            async with db_pool.acquire() as conn:
                await conn.execute('DELETE FROM scheduled_deletions WHERE id = $1', msg['id'])

# --- [5] 群组欢迎/离开消息处理器 ---
async def new_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理新成员加入群组的事件。"""
    chat_id = update.effective_chat.id
    if chat_id not in ALLOWED_WELCOME_GROUPS:
        return

    for member in update.message.new_chat_members:
        if member.id == context.bot.id: # 机器人自己加入
            await update.message.reply_text("👋 大家好！我是守门员小卫，很高兴加入这个群组！我将为您提供积分服务和VIP验证。")
            continue

        user_id = member.id
        user_name = member.full_name
        user_data = await get_user_data(user_id)

        welcome_text = (
            f"🎉 欢迎 **{user_name}** 加入我们的【VIP中转】群组！\n\n"
            f"我是守门员小卫，您的专属智能助手。\n\n"
            f"在这里，您可以体验：\n"
            f"🎁 **积分福利**：通过签到、充值获取积分，兑换丰厚礼品！\n"
            f"💎 **VIP特权**：验证您的VIP身份，尊享专属中转通道和会员群权益，一键畅览精彩内容！\n"
        )
        
        # 检查用户是否已在该群组的 joined_groups 列表中，避免重复处理
        if chat_id not in user_data['joined_groups']: 
            updated_joined_groups = list(set(user_data['joined_groups']) | {chat_id}) # 添加当前群组ID

            if not user_data['welcome_bonus_given']: # 仅在首次加入任何受监控群组时发放
                new_points = user_data['points'] + WELCOME_BONUS_POINTS
                new_total_acquired_points = user_data['total_acquired_points'] + WELCOME_BONUS_POINTS
                
                await update_user_data(
                    user_id, 
                    points=new_points, 
                    total_acquired_points=new_total_acquired_points,
                    welcome_bonus_given=True, # 标记为已获得首次入群奖励
                    joined_groups=updated_joined_groups # 更新加入的群组列表
                )
                await add_transaction(user_id, 'welcome_bonus', WELCOME_BONUS_POINTS, f"首次入群 {update.effective_chat.title} 获得 {WELCOME_BONUS_POINTS} 积分")
                welcome_text += f"\n\n**✨ 惊喜奖励：** 已为您赠送 `{WELCOME_BONUS_POINTS}` 积分，开启您的特权之旅！"
            else:
                # 只是更新 joined_groups，不重复发积分
                await update_user_data(user_id, joined_groups=updated_joined_groups)
                welcome_text += f"\n\n再次见到您，**{user_name}**！您的积分和权益已保留。"

        else: # 用户已经在 joined_groups 里面，但可能由于其他原因触发了 new_chat_members，不做额外处理
            welcome_text += f"\n\n再次见到您，**{user_name}**！您的积分和权益已保留。"
            
        welcome_text += "\n\n点击下方按钮，开始您的探索之旅吧！"
        
        keyboard = [
            [InlineKeyboardButton("🚀 VIP会员验证", callback_data="start_validation")],
            [InlineKeyboardButton("🌟 积分中心", callback_data="show_points_menu")],
            [InlineKeyboardButton("🎉 加入会员群", url=MEMBER_GROUP_LINK)]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            welcome_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def left_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理成员离开群组的事件。"""
    chat_id = update.effective_chat.id
    if chat_id not in ALLOWED_WELCOME_GROUPS:
        return

    member = update.message.left_chat_member
    if member.id == context.bot.id: # 机器人自己离开
        logger.info(f"Bot left chat {chat_id}")
        return

    user_id = member.id
    user_name = member.full_name
    user_data = await get_user_data(user_id)

    # 从 joined_groups 中移除当前群组ID
    updated_joined_groups = [g for g in user_data['joined_groups'] if g != chat_id]
    
    if not updated_joined_groups: # 如果用户已离开所有受监控的群组
        if user_data['points'] > 0:
            await update_user_data(user_id, points=0, joined_groups=updated_joined_groups)
            await add_transaction(user_id, 'points_reset', -user_data['points'], f"离开所有受监控群组，积分重置为0")
            await update.message.reply_text(
                f"💔 遗憾送别 **{user_name}**！\n"
                f"您已离开所有受监控群组，根据规则，当前积分已重置为0。\n"
                f"期待您的再次加入，重新启程，获取更多福利！",
                parse_mode='Markdown'
            )
        else: # 积分已经是0，只更新群组列表
             await update_user_data(user_id, joined_groups=updated_joined_groups)
             await update.message.reply_text(
                f"👋 再见，**{user_name}**！\n"
                f"希望未来还能与您在其他地方相遇。",
                parse_mode='Markdown'
            )
    else: # 用户仍在其他受监控群组中
        await update_user_data(user_id, joined_groups=updated_joined_groups)
        await update.message.reply_text(
            f"👋 再见，**{user_name}**！\n"
            f"您已离开本群，但您在其他受监控群组的积分和权益仍然保留。",
            parse_mode='Markdown'
        )

# --- [6] 用户主菜单和VIP验证 ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理 /start 命令，发送欢迎语和主要功能按钮。"""
    user_name = update.effective_user.first_name
    welcome_text = (
        f"👋 欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~\n\n"
        f"📢 小卫小卫，守门员小卫！\n"
        f"一键入群，小卫帮你搞定！\n"
        f"新人来报到，小卫查身份！"
    )
    keyboard = [
        [InlineKeyboardButton("🚀 VIP会员验证", callback_data="start_validation")],
        [InlineKeyboardButton("🌟 积分中心", callback_data="show_points_menu")],
        [InlineKeyboardButton("💼 我的余额", callback_data="show_balance")],
        [InlineKeyboardButton("🏆 积分排行榜", callback_data="show_leaderboard")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
    else: # 可能是CallbackQueryHandler重新进入start_command
        query = update.callback_query
        if query:
            await query.answer()
            # 尝试编辑消息，如果原消息是图片，发送新消息并删除旧消息，因为edit_message_text不能改变消息类型
            if query.message.photo:
                await query.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')
                try:
                    await query.message.delete()
                except Exception as e:
                    logger.warning(f"Failed to delete old message: {e}")
            else:
                await query.edit_message_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')
        
    return ConversationHandler.END

async def start_validation_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理“开始验证”按钮点击，显示VIP特权和付款验证按钮。"""
    query = update.callback_query
    await query.answer() 

    user_id = query.from_user.id
    user_data = await get_user_data(user_id)
    
    if user_data['vip_validation_cooldown_until'] and user_data['vip_validation_cooldown_until'] > datetime.now(TIMEZONE):
        remaining_seconds = int((user_data['vip_validation_cooldown_until'] - datetime.now(TIMEZONE)).total_seconds())
        hours, remainder = divmod(remaining_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        await query.message.reply_text(
            f"⚠️ 您VIP验证失败次数过多，请在 {hours} 小时 {minutes} 分钟 {seconds} 秒后重试。请联系客服获取帮助。",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 返回首页", callback_data="start_command")]])
        )
        return ConversationHandler.END

    await update_user_data(user_id, vip_validation_attempts=0)

    vip_perks_text = (
        "💎 VIP会员特权说明：\n"
        "✅ 专属中转通道\n"
        "✅ 优先审核入群\n"
        "✅ 7x24小时客服支持\n"
        "✅ 定期福利活动"
    )
    keyboard = [[InlineKeyboardButton("✅ 我已付款，开始验证", callback_data="ask_vip_order_id")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.reply_photo(
        photo=VIP_PERKS_FILE_ID,
        caption=vip_perks_text,
        reply_markup=reply_markup
    )
    return ConversationHandler.END

async def ask_vip_order_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理“我已付款，开始验证”按钮点击，显示VIP订单号教程并提示输入。"""
    query = update.callback_query
    await query.answer()

    tutorial_text = (
        "请提供您的订单号进行验证。\n"
        "🔍 **如何查找订单号：**\n"
        "📱 [购买平台/APP] -> 我的 -> 账单 -> 账单详情 -> 更多 -> 订单号\n"
        "\n请直接回复您的订单号。"
    )

    await query.message.reply_photo(
        photo=ORDER_TUTORIAL_FILE_ID,
        caption=tutorial_text
    )
    return ASKING_FOR_VIP_ORDER_ID 

async def process_vip_order_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理用户输入的VIP订单号，进行验证。"""
    user_id = update.effective_user.id
    order_id = update.message.text.strip()
    user_data = await get_user_data(user_id)
    
    current_attempts = user_data['vip_validation_attempts']
    
    await update_user_data(user_id, vip_validation_attempts=current_attempts + 1)

    if order_id.startswith(VIP_ORDER_PREFIX): 
        keyboard = [[InlineKeyboardButton("🎉 加入会员群", url=MEMBER_GROUP_LINK)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("🎉 订单验证成功！恭喜您成为尊贵的VIP会员！", reply_markup=reply_markup)
        
        await update_user_data(user_id, vip_validation_attempts=0, vip_validation_cooldown_until=None) 
        return ConversationHandler.END
    else:
        if user_data['vip_validation_attempts'] < VIP_VALIDATION_ATTEMPTS_MAX:
            remaining_attempts = VIP_VALIDATION_ATTEMPTS_MAX - user_data['vip_validation_attempts']
            await update.message.reply_text(f"❌ 未查询到VIP订单信息，请核对后重试。您还剩下 {remaining_attempts} 次机会。")
            return ASKING_FOR_VIP_ORDER_ID 
        else:
            cooldown_until = datetime.now(TIMEZONE) + timedelta(seconds=VIP_VALIDATION_COOLDOWN_SECONDS)
            await update_user_data(user_id, vip_validation_cooldown_until=cooldown_until)
            await update.message.reply_text(
                "❌ VIP验证失败次数过多。您将在5小时后才能再次尝试。\n"
                "请仔细检查订单号是否正确，或联系客服寻求帮助。",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 返回首页", callback_data="start_command")]])
            )
            return ConversationHandler.END

# --- [7] 积分中心：签到、充值、兑换 ---
async def show_points_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """显示积分中心菜单，包括当前积分、签到和充值按钮。"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    user_data = await get_user_data(user_id)

    points_text = (
        f"✨ **积分中心** ✨\n\n"
        f"您当前的积分余额：`{user_data['points']}` 积分\n\n"
    )

    keyboard = [
        [InlineKeyboardButton("🗓️ 每日签到", callback_data="daily_check_in")],
        [InlineKeyboardButton("💰 充值积分", callback_data="top_up_points_menu")],
        [InlineKeyboardButton("🎁 兑换礼品", callback_data="show_redeem_menu")],
        [InlineKeyboardButton("🔙 返回首页", callback_data="start_command")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        points_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def daily_check_in(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理每日签到。"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    user_data = await get_user_data(user_id)
    
    today = datetime.now(TIMEZONE).date()

    if user_data['last_check_in_date'] == today:
        await query.message.reply_text("您今天已经签到过了哦，请明天再来！")
    else:
        gained_points = random.randint(DAILY_CHECK_IN_POINTS_MIN, DAILY_CHECK_IN_POINTS_MAX)
        new_points = user_data['points'] + gained_points
        new_total_acquired_points = user_data['total_acquired_points'] + gained_points
        
        await update_user_data(user_id, points=new_points, total_acquired_points=new_total_acquired_points, last_check_in_date=today)
        await add_transaction(user_id, 'check_in', gained_points, f"每日签到获得 {gained_points} 积分")

        await query.message.reply_text(
            f"🎉 签到成功！您获得了 {gained_points} 积分。\n"
            f"当前总积分：`{new_points}`",
            parse_mode='Markdown'
        )
    
    await show_points_menu(update, context) 
    return ConversationHandler.END

async def top_up_points_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """显示充值积分选项。"""
    query = update.callback_query
    await query.answer()

    top_up_text = (
        "💰 **选择充值方式** 💰\n\n"
        "请选择您偏好的支付方式进行积分充值。"
    )
    keyboard = [
        [InlineKeyboardButton("💚 微信充值", callback_data="show_wechat_top_up")],
        [InlineKeyboardButton("💙 支付宝充值", callback_data="show_alipay_top_up")],
        [InlineKeyboardButton("🔙 返回积分中心", callback_data="show_points_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        top_up_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def show_wechat_top_up(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """显示微信充值二维码和说明。"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    user_data = await get_user_data(user_id)

    if user_data['wechat_used']:
        await query.message.reply_text(
            "⚠️ 您已成功使用微信充值过积分，每位用户仅限一次。\n"
            "如果您有疑问，请联系客服。",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回积分中心", callback_data="show_points_menu")]])
        )
        return ConversationHandler.END
    
    if user_data['wechat_cooldown_until'] and user_data['wechat_cooldown_until'] > datetime.now(TIMEZONE):
        remaining_seconds = int((user_data['wechat_cooldown_until'] - datetime.now(TIMEZONE)).total_seconds())
        hours, remainder = divmod(remaining_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        await query.message.reply_text(
            f"⚠️ 您的微信充值验证失败次数过多，请在 {hours} 小时 {minutes} 分钟 {seconds} 秒后重试。",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回积分中心", callback_data="show_points_menu")]])
        )
        return ConversationHandler.END
    
    await update_user_data(user_id, wechat_attempts=0) 

    wechat_text = (
        "💰 **微信充值通道** 💰\n\n"
        f"💎 **充值方案：** 5元 = `{WECHAT_TOPUP_POINTS}` 积分\n\n"
        "⚠️ **温馨提示：** 每位用户仅限通过微信充值一次，请勿重复支付。重复支付的款项将不予退还。\n\n"
        "请扫描下方二维码或前往微信支付此金额。\n"
        "支付成功后，请点击“✅ 我已支付，提交验证”按钮。"
    )
    keyboard = [[InlineKeyboardButton("✅ 我已支付，提交验证", callback_data="confirm_wechat_payment")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.reply_photo(
        photo=WECHAT_TOPUP_QR_FILE_ID,
        caption=wechat_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def confirm_wechat_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """提示用户输入微信交易单号。"""
    query = update.callback_query
    await query.answer()

    tutorial_text = (
        "✅ 支付成功，请提交交易单号以完成积分充值。\n\n"
        "🔍 **如何查找交易单号：**\n"
        "1. 打开微信。\n"
        "2. 进入 **我** -> **服务** (或支付)。\n"
        "3. 点击 **钱包** -> **账单**。\n"
        "4. 找到并点击您刚才完成的5元充值交易。\n"
        "5. 进入**账单详情**页面，即可看到并复制**交易单号**。\n\n"
        "请直接回复您的交易单号进行验证。"
    )
    await query.message.reply_photo(
        photo=WECHAT_ORDER_TUTORIAL_FILE_ID,
        caption=tutorial_text,
        parse_mode='Markdown'
    )
    return ASKING_WECHAT_ORDER_ID

async def process_wechat_order_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理用户输入的微信订单号。"""
    user_id = update.effective_user.id
    order_id = update.message.text.strip()
    user_data = await get_user_data(user_id)

    if user_data['wechat_used']: 
        await update.message.reply_text("⚠️ 您已成功使用微信充值过积分，每位用户仅限一次。")
        return ConversationHandler.END

    current_attempts = user_data['wechat_attempts']
    
    if order_id.startswith(WECHAT_ORDER_PREFIX):
        new_points = user_data['points'] + WECHAT_TOPUP_POINTS
        new_total_acquired_points = user_data['total_acquired_points'] + WECHAT_TOPUP_POINTS
        
        await update_user_data(user_id, points=new_points, total_acquired_points=new_total_acquired_points,
                               wechat_used=True, wechat_attempts=0, wechat_cooldown_until=None)
        await add_transaction(user_id, 'wechat_topup', WECHAT_TOPUP_POINTS, f"微信充值获得 {WECHAT_TOPUP_POINTS} 积分")

        await update.message.reply_text(
            f"🎉 微信充值成功！您的 `{WECHAT_TOPUP_POINTS}` 积分已到账。\n"
            f"当前总积分：`{new_points}`",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回积分中心", callback_data="show_points_menu")]])
        )
        return ConversationHandler.END
    else:
        await update_user_data(user_id, wechat_attempts=current_attempts + 1)
        if current_attempts + 1 < PAYMENT_ATTEMPTS_MAX:
            remaining_attempts = PAYMENT_ATTEMPTS_MAX - (current_attempts + 1)
            await update.message.reply_text(f"❌ 订单识别失败，请核对后重试。您还剩下 {remaining_attempts} 次机会。")
            return ASKING_WECHAT_ORDER_ID 
        else:
            cooldown_until = datetime.now(TIMEZONE) + timedelta(seconds=PAYMENT_COOLDOWN_SECONDS)
            await update_user_data(user_id, wechat_cooldown_until=cooldown_until)
            await update.message.reply_text(
                "❌ 微信充值验证失败次数过多。您将在10小时后才能再次尝试。\n"
                "请仔细检查交易单号是否正确，或联系客服寻求帮助。",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回积分中心", callback_data="show_points_menu")]])
            )
            return ConversationHandler.END

async def show_alipay_top_up(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """显示支付宝充值二维码和说明。"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    user_data = await get_user_data(user_id)

    if user_data['alipay_used']:
        await query.message.reply_text(
            "⚠️ 您已成功使用支付宝充值过积分，每位用户仅限一次。\n"
            "如果您有疑问，请联系客服。",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回积分中心", callback_data="show_points_menu")]])
        )
        return ConversationHandler.END
    
    if user_data['alipay_cooldown_until'] and user_data['alipay_cooldown_until'] > datetime.now(TIMEZONE):
        remaining_seconds = int((user_data['alipay_cooldown_until'] - datetime.now(TIMEZONE)).total_seconds())
        hours, remainder = divmod(remaining_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        await query.message.reply_text(
            f"⚠️ 您的支付宝充值验证失败次数过多，请在 {hours} 小时 {minutes} 分钟 {seconds} 秒后重试。",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回积分中心", callback_data="show_points_menu")]])
        )
        return ConversationHandler.END
    
    await update_user_data(user_id, alipay_attempts=0) 

    alipay_text = (
        "💰 **支付宝充值通道** 💰\n\n"
        f"💎 **充值方案：** 5元 = `{ALIPAY_TOPUP_POINTS}` 积分\n\n"
        "⚠️ **温馨提示：** 每位用户仅限通过支付宝充值一次，请勿重复支付。重复支付的款项将不予退还。\n\n"
        "请扫描下方二维码或前往支付宝支付此金额。\n"
        "支付成功后，请点击“✅ 我已支付，提交验证”按钮。"
    )
    keyboard = [[InlineKeyboardButton("✅ 我已支付，提交验证", callback_data="confirm_alipay_payment")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.reply_photo(
        photo=ALIPAY_TOPUP_QR_FILE_ID,
        caption=alipay_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def confirm_alipay_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """提示用户输入支付宝商家订单号。"""
    query = update.callback_query
    await query.answer()

    tutorial_text = (
        "✅ 支付成功，请提交商家订单号以完成积分充值。\n\n"
        "🔍 **如何查找商家订单号：**\n"
        "1. 打开支付宝。\n"
        "2. 进入 **我的** -> **账单**。\n"
        "3. 找到并点击您刚才完成的5元充值交易。\n"
        "4. 进入**交易详情**页面，即可看到并复制**商家订单号**。\n\n"
        "请直接回复您的商家订单号进行验证。"
    )
    await query.message.reply_photo(
        photo=ALIPAY_ORDER_TUTORIAL_FILE_ID,
        caption=tutorial_text,
        parse_mode='Markdown'
    )
    return ASKING_ALIPAY_ORDER_ID

async def process_alipay_order_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理用户输入的支付宝订单号。"""
    user_id = update.effective_user.id
    order_id = update.message.text.strip()
    user_data = await get_user_data(user_id)

    if user_data['alipay_used']: 
        await update.message.reply_text("⚠️ 您已成功使用支付宝充值过积分，每位用户仅限一次。")
        return ConversationHandler.END

    current_attempts = user_data['alipay_attempts']
    
    if order_id.startswith(ALIPAY_ORDER_PREFIX):
        new_points = user_data['points'] + ALIPAY_TOPUP_POINTS
        new_total_acquired_points = user_data['total_acquired_points'] + ALIPAY_TOPUP_POINTS

        await update_user_data(user_id, points=new_points, total_acquired_points=new_total_acquired_points,
                               alipay_used=True, alipay_attempts=0, alipay_cooldown_until=None)
        await add_transaction(user_id, 'alipay_topup', ALIPAY_TOPUP_POINTS, f"支付宝充值获得 {ALIPAY_TOPUP_POINTS} 积分")

        await update.message.reply_text(
            f"🎉 支付宝充值成功！您的 `{ALIPAY_TOPUP_POINTS}` 积分已到账。\n"
            f"当前总积分：`{new_points}`",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回积分中心", callback_data="show_points_menu")]])
        )
        return ConversationHandler.END
    else:
        await update.user_data(user_id, alipay_attempts=current_attempts + 1)
        if current_attempts + 1 < PAYMENT_ATTEMPTS_MAX:
            remaining_attempts = PAYMENT_ATTEMPTS_MAX - (current_attempts + 1)
            await update.message.reply_text(f"❌ 订单识别失败，请核对后重试。您还剩下 {remaining_attempts} 次机会。")
            return ASKING_ALIPAY_ORDER_ID 
        else:
            cooldown_until = datetime.now(TIMEZONE) + timedelta(seconds=PAYMENT_COOLDOWN_SECONDS)
            await update_user_data(user_id, alipay_cooldown_until=cooldown_until)
            await update.message.reply_text(
                "❌ 支付宝充值验证失败次数过多。您将在10小时后才能再次尝试。\n"
                "请仔细检查商家订单号是否正确，或联系客服寻求帮助。",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回积分中心", callback_data="show_points_menu")]])
            )
            return ConversationHandler.END

async def show_redeem_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """显示可兑换礼品列表。"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    user_data = await get_user_data(user_id)
    
    async with db_pool.acquire() as conn:
        products = await conn.fetch('SELECT * FROM products WHERE is_active = TRUE ORDER BY created_at')
        redeemed_products = await conn.fetch('SELECT product_id FROM redemptions WHERE user_id = $1', user_id)
    
    redeemed_ids = {r['product_id'] for r in redeemed_products}

    keyboard = []
    redeem_text = f"🎁 **兑换礼品中心** 🎁\n\n您当前的积分余额：`{user_data['points']}` 积分\n\n可兑换商品列表：\n\n"
    
    if not products:
        redeem_text += "暂无商品可供兑换，敬请期待！"
    else:
        for product in products:
            status_text = " (已兑换)" if product['product_id'] in redeemed_ids else f" (`{product['points_cost']}` 积分)"
            button_text = f"✨ {product['product_id']}{status_text}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"select_redeem_item:{product['product_id']}")])

    keyboard.append([InlineKeyboardButton("🔙 返回积分中心", callback_data="show_points_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        redeem_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def select_redeem_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """用户选择兑换商品后的处理。"""
    query = update.callback_query
    await query.answer()

    product_id = query.data.split(':')[1]
    user_id = query.from_user.id

    async with db_pool.acquire() as conn:
        product = await conn.fetchrow('SELECT * FROM products WHERE product_id = $1 AND is_active = TRUE', product_id)
        user_data = await get_user_data(user_id)
        has_redeemed = await conn.fetchrow('SELECT id FROM redemptions WHERE user_id = $1 AND product_id = $2', user_id, product_id)

    if not product:
        await query.edit_message_text("❌ 商品不存在或已下架。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回兑换列表", callback_data="show_redeem_menu")]]))
        return ConversationHandler.END

    if has_redeemed:
        await show_redeemed_content(update, product)
        await show_redeem_menu(update, context)
        return ConversationHandler.END

    context.user_data['redeem_product_id'] = product_id

    confirm_text = (
        f"您正在兑换商品：`{product['product_id']}`\n"
        f"所需积分：`{product['points_cost']}`\n"
        f"您当前积分余额：`{user_data['points']}`\n\n"
        "确定要兑换吗？兑换后积分将自动扣除。"
    )
    keyboard = [
        [InlineKeyboardButton("✅ 确认兑换", callback_data="confirm_redeem")],
        [InlineKeyboardButton("↩️ 取消", callback_data="show_redeem_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(confirm_text, reply_markup=reply_markup, parse_mode='Markdown')
    return CONFIRM_REDEMPTION

async def confirm_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """确认兑换商品并进行扣除。"""
    query = update.callback_query
    await query.answer()

    product_id = context.user_data.get('redeem_product_id')
    user_id = query.from_user.id

    if not product_id:
        await query.edit_message_text("❌ 兑换请求已失效，请重新选择。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回兑换列表", callback_data="show_redeem_menu")]]))
        return ConversationHandler.END

    async with db_pool.acquire() as conn:
        product = await conn.fetchrow('SELECT * FROM products WHERE product_id = $1 AND is_active = TRUE', product_id)
        user_data = await get_user_data(user_id)
        has_redeemed = await conn.fetchrow('SELECT id FROM redemptions WHERE user_id = $1 AND product_id = $2', user_id, product_id)

    if not product:
        await query.edit_message_text("❌ 商品不存在或已下架。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回兑换列表", 
