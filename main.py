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
VIP_PERKS_FILE_ID = "AgACAgUAAxkBAAIBJ2loboOm15d-Qog2KkzAVSTLG-1eAAKaD2sbQNhBV_UKRl5JPolfAQADAgADeAADOAQ"
ORDER_TUTORIAL_FILE_ID = "AgACAgUAAxkBAAIBHWlobOW8SVMC9dk6aKquMiQHPh1AAKVD2sbQNhBV9mV11AQnf1xAQADAgADeQADOAQ" # VIP订单号查找教程图
WECHAT_TOPUP_QR_FILE_ID = "AgACAgUAAxkBAAIBImlobmPLtn9DWUFZJ53t1mhkVIA7AAKYD2sbQNhBV_A-2IdqoG-dAQADAgADeAADOAQ"
WECHAT_ORDER_TUTORIAL_FILE_ID = "AgACAgUAAxkBAAIBLWlocIlhveHnlgntE7dGi1ri56i2AAKeD2sbQNhBVyZ8_L3zE7qwAQADAgADeQADOAQ"
ALIPAY_TOPUP_QR_FILE_ID = "AgACAgUAAxkBAAIBJWlobnt_eXxhfHqg5bpF8WFwDDESAAKZD2sbQNhBVyWCVUCv9Q3iAQADAgADeAADOAQ"
ALIPAY_ORDER_TUTORIAL_FILE_ID = "AgACAgUAAxkBAAIBMGlocJCdAlLyJie451mVeM6gi7xhAAKfD2sbQNhBV-EDx2qKNqc-AQADAgADeQADOAQ"

# 会员群组链接 (替换为你自己的会员群链接)
MEMBER_GROUP_LINK = "https://t.me/+495j5rWmApsxYzg9"

# 允许机器人发送欢迎语的群组ID列表 (替换为你的群组ID，负数)
# 注意: 群组ID是负数，例如 -100XXXXXXXXXX
ALLOWED_WELCOME_GROUPS = {
    -1002520416718, # 示例群组ID 1 (替换)
    -1002933211039  # 示例群组ID 2 (替换)
}
WELCOME_BONUS_POINTS = 20 # 首次入群赠送积分数量

# VIP 验证相关配置
VIP_VALIDATION_ATTEMPTS_MAX = 2 # 订单号重试次数
VIP_VALIDATION_COOLDOWN_SECONDS = 5 * 3600  # 失败后冷却时间：5小时
VIP_ORDER_PREFIX = "20260" # VIP订单号开头

# 积分获取配置
DAILY_CHECK_IN_POINTS_MIN = 3 # 每日签到最少积分
DAILY_CHECK_IN_POINTS_MAX = 8 # 每日签到最多积分

# 充值相关配置
PAYMENT_ATTEMPTS_MAX = 2 # 充值订单号重试次数
PAYMENT_COOLDOWN_SECONDS = 10 * 3600 # 失败后冷却时间：10小时

WECHAT_TOPUP_POINTS = 100 # 微信充值获得积分
WECHAT_ORDER_PREFIX = "4200" # 微信订单号开头

ALIPAY_TOPUP_POINTS = 100 # 支付宝充值获得积分
ALIPAY_ORDER_PREFIX = "4768" # 支付宝订单号开头

# 转发消息相关配置
FORWARDED_MESSAGE_LIFETIME_SECONDS = 20 * 60 # 转发消息自动删除时间：20分钟
DELETE_CHECK_INTERVAL_SECONDS = 5 * 60 # 每5分钟检查一次待删除消息

# Telegram 消息链接正则表达式
TELEGRAM_MESSAGE_LINK_REGEX = re.compile(r"https://t\.me/(?:c/)?(?:([\d]+)|([a-zA-Z0-9_]+))/([\d]+)")

# 定义时区，这里使用上海/北京时间
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
        
        if chat_id not in user_data['joined_groups']: # 确保只为该群组处理一次
            updated_joined_groups = list(set(user_data['joined_groups']) | {chat_id})

            if not user_data['welcome_bonus_given']:
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
                welcome_text += f"\n\n再次见到您，**{user_name}**！期待您继续活跃！"

        else: # 用户已经在 joined_groups 里面，但可能由于其他原因触发了 new_chat_members，不做额外处理
            welcome_text += f"\n\n再次见到您，**{user_name}**！期待您继续活跃！"
            
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
            # 尝试编辑消息，如果原消息是图片，编辑caption
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
        top_up_markup=reply_markup,
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
        await update_user_data(user_id, alipay_attempts=current_attempts + 1)
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
        await query.edit_message_text("❌ 商品不存在或已下架。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回兑换列表", callback_data="show_redeem_menu")]]))
        return ConversationHandler.END
    
    if has_redeemed: 
        await show_redeemed_content(update, product)
        await show_redeem_menu(update, context) 
        return ConversationHandler.END

    if user_data['points'] < product['points_cost']:
        await query.edit_message_text("❌ 余额不足，无法兑换该商品。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回兑换列表", callback_data="show_redeem_menu")]]))
        return ConversationHandler.END
    
    new_points = user_data['points'] - product['points_cost']
    await update_user_data(user_id, points=new_points)
    await add_transaction(user_id, 'redeem', -product['points_cost'], f"兑换商品 '{product['product_id']}' 消耗 {product['points_cost']} 积分")

    async with db_pool.acquire() as conn:
        await conn.execute(
            'INSERT INTO redemptions (user_id, product_id, points_cost_at_redemption) VALUES ($1, $2, $3)',
            user_id, product_id, product['points_cost']
        )
    
    del context.user_data['redeem_product_id'] 
    
    await query.message.reply_text(f"🎉 恭喜您成功兑换了商品 `{product['product_id']}`！", parse_mode='Markdown')
    await show_redeemed_content(update, product) 
    await show_redeem_menu(update, context) 
    return ConversationHandler.END


async def show_redeemed_content(update: Update, product) -> None:
    """显示已兑换商品的内容。"""
    content_text = f"✨ **您的兑换内容如下** ✨\n\n`{product['product_id']}`:\n\n"
    
    # 根据是query还是message触发，选择回复方式
    if update.callback_query:
        reply_func = update.callback_query.message.reply_text
        reply_photo_func = update.callback_query.message.reply_photo
        reply_video_func = update.callback_query.message.reply_video
    else: # 可能是admin直接测试内容
        reply_func = update.message.reply_text
        reply_photo_func = update.message.reply_photo
        reply_video_func = update.message.reply_video

    if product['type'] == 'text':
        await reply_func(content_text + product['content'], parse_mode='Markdown')
    elif product['type'] == 'photo':
        await reply_photo_func(product['content'], caption=content_text, parse_mode='Markdown')
    elif product['type'] == 'video':
        await reply_video_func(product['content'], caption=content_text, parse_mode='Markdown')
    else:
        await reply_func("🤔 无法识别的商品类型。", parse_mode='Markdown')

# --- [8] 我的余额和积分排行榜 ---
async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """显示用户积分余额和历史记录按钮。"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    user_data = await get_user_data(user_id)

    balance_text = (
        f"💼 **我的余额** 💼\n\n"
        f"您当前的积分余额：`{user_data['points']}` 积分\n\n"
        "点击下方按钮查看积分明细。"
    )
    keyboard = [
        [InlineKeyboardButton("📊 积分明细 (获得)", callback_data="show_acquisition_history")],
        [InlineKeyboardButton("📉 积分明细 (消耗)", callback_data="show_usage_history")],
        [InlineKeyboardButton("🔙 返回首页", callback_data="start_command")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        balance_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def show_acquisition_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """显示积分获得记录。"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    async with db_pool.acquire() as conn:
        transactions = await conn.fetch(
            'SELECT * FROM transactions WHERE user_id = $1 AND amount > 0 ORDER BY timestamp DESC LIMIT 10',
            user_id
        )
    
    history_text = "📊 **您的积分获得记录** 📊\n\n"
    if not transactions:
        history_text += "暂无获得记录。"
    else:
        for t in transactions:
            history_text += f"📅 {t['timestamp'].astimezone(TIMEZONE).strftime('%Y-%m-%d %H:%M')}\n"
            history_text += f"   ➕ {t['description']}：获得 `{t['amount']}` 积分\n\n"
    
    keyboard = [[InlineKeyboardButton("🔙 返回我的余额", callback_data="show_balance")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        history_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def show_usage_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """显示积分消耗记录。"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    async with db_pool.acquire() as conn:
        transactions = await conn.fetch(
            'SELECT * FROM transactions WHERE user_id = $1 AND amount < 0 ORDER BY timestamp DESC LIMIT 10',
            user_id
        )
    
    history_text = "📉 **您的积分消耗记录** 📉\n\n"
    if not transactions:
        history_text += "暂无消耗记录。"
    else:
        for t in transactions:
            history_text += f"📅 {t['timestamp'].astimezone(TIMEZONE).strftime('%Y-%m-%d %H:%M')}\n"
            history_text += f"   ➖ {t['description']}：消耗 `{abs(t['amount'])}` 积分\n\n"
    
    keyboard = [[InlineKeyboardButton("🔙 返回我的余额", callback_data="show_balance")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        history_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """显示积分排行榜。"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    
    async with db_pool.acquire() as conn:
        # 获取前10名
        top_users = await conn.fetch(
            'SELECT id, total_acquired_points FROM users ORDER BY total_acquired_points DESC LIMIT 10'
        )
        # 获取当前用户的排名
        user_rank_data = await conn.fetchrow(
            '''
            SELECT rank, id, total_acquired_points
            FROM (
                SELECT RANK() OVER (ORDER BY total_acquired_points DESC) as rank, id, total_acquired_points
                FROM users
            ) AS ranked_users
            WHERE id = $1
            ''', user_id
        )
    
    leaderboard_text = "🏆 **积分排行榜** 🏆 (基于总获得积分)\n\n"
    if not top_users:
        leaderboard_text += "暂无数据。\n"
    else:
        for i, user in enumerate(top_users):
            is_current_user = " (您)" if user['id'] == user_id else ""
            leaderboard_text += f"*{i+1}.* 用户`{user['id']}`: `{user['total_acquired_points']}` 积分 {is_current_user}\n"
    
    if user_rank_data:
        leaderboard_text += (
            f"\n---您的排名---\n"
            f"*{user_rank_data['rank']}.* 用户`{user_rank_data['id']}`: `{user_rank_data['total_acquired_points']}` 积分\n"
        )
    else:
        leaderboard_text += "\n您暂未上榜。"

    keyboard = [[InlineKeyboardButton("🔙 返回首页", callback_data="start_command")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        leaderboard_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return ConversationHandler.END

# --- [9] 管理员功能 ---
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /admin 命令，仅管理员可用。"""
    if str(update.effective_user.id) != get_env_variable('ADMIN_ID'):
        await update.message.reply_text("抱歉，您不是管理员。")
        return

    admin_text = (
        "✨ **管理员面板** ✨\n\n"
        "👋 欢迎，亲爱的管理员小卫！\n"
        "在这里您可以管理商品、配置频道转发命令、获取文件ID等。\n\n"
    )
    keyboard = [
        [InlineKeyboardButton("🔑 获取 File ID", callback_data="admin_file_id")],
        [InlineKeyboardButton("📦 管理商品", callback_data="admin_manage_products")],
        [InlineKeyboardButton("📺 频道转发库", callback_data="admin_fwd_cmd_menu")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        admin_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def admin_prompt_for_file_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """管理员点击“获取 File ID”按钮后的提示。"""
    query = update.callback_query
    await query.answer()
    if str(query.from_user.id) == get_env_variable('ADMIN_ID'):
        await query.edit_message_text("请现在发送您想要获取 File ID 的图片、视频或文件。")
    else:
        await query.edit_message_text("您不是管理员。")

async def admin_get_file_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理管理员发送的媒体文件，回复其 File ID。"""
    if str(update.effective_user.id) != get_env_variable('ADMIN_ID'):
        return 

    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id 
    elif update.message.document:
        file_id = update.message.document.file_id
    elif update.message.video:
        file_id = update.message.video.file_id
    
    if file_id:
        await update.message.reply_text(f"文件 File ID: `{file_id}`", parse_mode='Markdown')
    else:
        await update.message.reply_text("未检测到图片、视频或文件。请发送图片、视频或文件以获取其 File ID。")

# --- [10] 管理员：商品管理功能 ---
async def admin_manage_products_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """管理员商品管理主菜单。"""
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("➕ 添加新商品", callback_data="admin_add_product_start")],
        [InlineKeyboardButton("✍️ 编辑/删除商品", callback_data="admin_list_products_to_manage")],
        [InlineKeyboardButton("🔙 返回管理员面板", callback_data="admin_back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "📦 **商品管理中心** 📦\n\n请选择您的操作：",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def admin_add_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """开始添加新商品流程。"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("请输入新商品的唯一ID (例如：`test_item_001`)：", parse_mode='Markdown')
    return ADMIN_PRODUCT_ADD_ID

async def admin_receive_product_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """接收商品ID。"""
    product_id = update.message.text.strip()
    if not product_id:
        await update.message.reply_text("商品ID不能为空，请重新输入。")
        return ADMIN_PRODUCT_ADD_ID
    
    async with db_pool.acquire() as conn:
        existing_product = await conn.fetchrow('SELECT product_id FROM products WHERE product_id = $1', product_id)
        if existing_product:
            await update.message.reply_text(f"商品ID `{product_id}` 已存在，请重新输入一个唯一的ID。", parse_mode='Markdown')
            return ADMIN_PRODUCT_ADD_ID
            
    context.user_data['temp_product_id'] = product_id
    keyboard = [
        [InlineKeyboardButton("文本", callback_data="set_product_type:text")],
        [InlineKeyboardButton("图片", callback_data="set_product_type:photo")],
        [InlineKeyboardButton("视频", callback_data="set_product_type:video")],
        [InlineKeyboardButton("↩️ 取消添加", callback_data="admin_manage_products")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"商品ID设置为：`{product_id}`\n请选择商品内容类型：", 
        reply_markup=reply_markup, 
        parse_mode='Markdown'
    )
    return ADMIN_PRODUCT_ADD_TYPE

async def admin_receive_product_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """接收商品类型。"""
    query = update.callback_query
    await query.answer()
    product_type = query.data.split(':')[1]
    context.user_data['temp_product_type'] = product_type

    if product_type == 'text':
        await query.edit_message_text("请输入商品内容文本：")
    elif product_type in ['photo', 'video']:
        await query.edit_message_text(f"请发送商品内容（{product_type}），机器人将自动获取其 File ID。")
    return ADMIN_PRODUCT_ADD_CONTENT

async def admin_receive_product_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """接收商品内容（文本或文件ID）。"""
    product_type = context.user_data.get('temp_product_type')
    product_content = None

    if product_type == 'text':
        if update.message.text:
            product_content = update.message.text.strip()
        else:
            await update.message.reply_text("请发送文本内容。")
            return ADMIN_PRODUCT_ADD_CONTENT
    elif product_type == 'photo':
        if update.message.photo:
            product_content = update.message.photo[-1].file_id
        else:
            await update.message.reply_text("请发送图片。")
            return ADMIN_PRODUCT_ADD_CONTENT
    elif product_type == 'video':
        if update.message.video:
            product_content = update.message.video.file_id
        else:
            await update.message.reply_text("请发送视频。")
            return ADMIN_PRODUCT_ADD_CONTENT
    
    if not product_content:
        await update.message.reply_text("无法获取内容，请重试。")
        return ADMIN_PRODUCT_ADD_CONTENT
        
    context.user_data['temp_product_content'] = product_content
    await update.message.reply_text("请输入商品所需积分（必须是整数）：")
    return ADMIN_PRODUCT_ADD_POINTS_COST

async def admin_receive_product_points_cost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """接收商品所需积分并保存商品。"""
    try:
        points_cost = int(update.message.text.strip())
        if points_cost < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("积分必须是一个非负整数，请重新输入。")
        return ADMIN_PRODUCT_ADD_POINTS_COST
    
    product_id = context.user_data.get('temp_product_id')
    product_type = context.user_data.get('temp_product_type')
    product_content = context.user_data.get('temp_product_content')

    if not all([product_id, product_type, product_content]):
        await update.message.reply_text("商品信息不完整，请从头重新添加。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回商品管理", callback_data="admin_manage_products")]]) )
        return ConversationHandler.END

    async with db_pool.acquire() as conn:
        await conn.execute(
            'INSERT INTO products (product_id, type, content, points_cost) VALUES ($1, $2, $3, $4)',
            product_id, product_type, product_content, points_cost
        )
    
    for key in ['temp_product_id', 'temp_product_type', 'temp_product_content']:
        if key in context.user_data:
            del context.user_data[key]

    await update.message.reply_text(
        f"✅ 商品 `{product_id}` 添加成功！",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回商品管理", callback_data="admin_manage_products")]])
    )
    return ConversationHandler.END

async def admin_list_products_to_manage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """列出所有商品供管理员编辑/删除。"""
    query = update.callback_query
    await query.answer()

    async with db_pool.acquire() as conn:
        products = await conn.fetch('SELECT product_id, is_active FROM products ORDER BY created_at')
    
    keyboard = []
    if not products:
        await query.edit_message_text("暂无商品可管理。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回商品管理", callback_data="admin_manage_products")]]) )
        return ConversationHandler.END
    
    for product in products:
        status = "🟢 上架中" if product['is_active'] else "🔴 已下架"
        keyboard.append([InlineKeyboardButton(f"{product['product_id']} ({status})", callback_data=f"admin_select_product:{product['product_id']}")])

    keyboard.append([InlineKeyboardButton("🔙 返回商品管理", callback_data="admin_manage_products")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "✍️ **选择要管理的商品：**\n\n点击商品ID进入管理界面。",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def admin_select_product_to_manage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """管理员选择特定商品进行管理。"""
    query = update.callback_query
    await query.answer()

    product_id = query.data.split(':')[1]
    context.user_data['admin_managing_product_id'] = product_id

    async with db_pool.acquire() as conn:
        product = await conn.fetchrow('SELECT * FROM products WHERE product_id = $1', product_id)
    
    if not product:
        await query.edit_message_text("❌ 商品不存在。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回商品列表", callback_data="admin_list_products_to_manage")]]) )
        return ConversationHandler.END
    
    status_text = "🟢 上架中" if product['is_active'] else "🔴 已下架"
    toggle_action = "下架" if product['is_active'] else "上架"

    product_details = (
        f"**商品详情：** `{product['product_id']}`\n"
        f"类型：`{product['type']}`\n"
        f"内容：`{product['content'][:50]}...` (过长则截断)\n" 
        f"所需积分：`{product['points_cost']}`\n"
        f"状态：{status_text}\n"
    )
    keyboard = [
        [InlineKeyboardButton(f"切换为 {toggle_action}", callback_data=f"admin_toggle_product_status:{product_id}")],
        [InlineKeyboardButton("🗑️ 删除商品", callback_data=f"admin_delete_product:{product_id}")],
        [InlineKeyboardButton("🔙 返回商品列表", callback_data="admin_list_products_to_manage")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(product_details, reply_markup=reply_markup, parse_mode='Markdown')
    return ConversationHandler.END

async def admin_toggle_product_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """管理员切换商品上架/下架状态。"""
    query = update.callback_query
    await query.answer()

    product_id = query.data.split(':')[1]

    async with db_pool.acquire() as conn:
        current_status = await conn.fetchval('SELECT is_active FROM products WHERE product_id = $1', product_id)
        new_status = not current_status
        await conn.execute('UPDATE products SET is_active = $1 WHERE product_id = $2', new_status, product_id)
    
    status_message = "已上架" if new_status else "已下架"
    await query.edit_message_text(
        f"✅ 商品 `{product_id}` 已成功切换为 `{status_message}`。",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回商品列表", callback_data="admin_list_products_to_manage")]])
    )
    return ConversationHandler.END

async def admin_delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """管理员删除商品。"""
    query = update.callback_query
    await query.answer("商品删除前将清除所有兑换记录，请谨慎操作！", show_alert=True) 

    product_id = query.data.split(':')[1]

    async with db_pool.acquire() as conn:
        await conn.execute('DELETE FROM redemptions WHERE product_id = $1', product_id)
        await conn.execute('DELETE FROM products WHERE product_id = $1', product_id)
    
    await query.edit_message_text(
        f"✅ 商品 `{product_id}` 及相关兑换记录已成功删除。",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回商品列表", callback_data="admin_list_products_to_manage")]])
    )
    return ConversationHandler.END

# --- [11] 管理员：频道转发库功能 ---
async def admin_fwd_cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """管理员频道转发库主菜单。"""
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("➕ 添加转发命令", callback_data="admin_add_fwd_cmd_start")],
        [InlineKeyboardButton("✍️ 管理转发命令", callback_data="admin_list_fwd_cmds_to_manage")],
        [InlineKeyboardButton("🔙 返回管理员面板", callback_data="admin_back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "📺 **频道转发库** 📺\n\n请选择您的操作：",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def admin_add_fwd_cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """开始添加转发命令流程。"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "请输入新的转发命令名称 (例如：`/channel_content` 或 `/我的频道`，支持中文和大小写)。\n"
        "**请勿使用机器人内置命令**，例如 `/start`, `/admin`。",
        parse_mode='Markdown'
    )
    return ADMIN_FWD_CMD_ADD_NAME

async def admin_receive_fwd_cmd_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """接收转发命令名称。"""
    command_name_input = update.message.text.strip()
    
    if not command_name_input.startswith('/'):
        command_name_input = '/' + command_name_input
    
    command_name_lower = command_name_input.lower()
    
    reserved_commands = {"/start", "/admin"}
    if command_name_lower in reserved_commands:
        await update.message.reply_text(f"命令名称 `{command_name_input}` 为机器人内置命令，请勿使用。请重新输入。", parse_mode='Markdown')
        return ADMIN_FWD_CMD_ADD_NAME
    
    if not command_name_input or len(command_name_input) < 2:
        await update.message.reply_text("命令名称不能为空且至少包含一个字符（斜杠除外），请重新输入。")
        return ADMIN_FWD_CMD_ADD_NAME
    
    async with db_pool.acquire() as conn:
        existing_cmd = await conn.fetchrow('SELECT command_name FROM forward_commands WHERE command_name = $1', command_name_lower) # 数据库存小写
        if existing_cmd:
            await update.message.reply_text(f"转发命令 `{command_name_input}` 已存在，请重新输入一个唯一的命令名称。", parse_mode='Markdown')
            return ADMIN_FWD_CMD_ADD_NAME
            
    context.user_data['temp_fwd_command_name'] = command_name_input # 存储原始输入格式，但实际匹配会用小写
    await update.message.reply_text(
        f"命令名称设置为：`{command_name_input}`\n\n"
        "请粘贴您要转发的频道消息链接，每行一个（最多支持50条）。\n"
        "例如：\n"
        "`https://t.me/c/123456789/100`\n"
        "`https://t.me/public_channel/200`\n"
        "**重要提示：** 请确保机器人是这些频道的管理员且拥有**转发频道内容**的权限。",
        parse_mode='Markdown'
    )
    return ADMIN_FWD_CMD_ADD_LINKS

async def admin_receive_fwd_message_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """接收转发消息链接，解析并保存。"""
    links_input = update.message.text.strip()
    raw_links = [link.strip() for link in links_input.split('\n') if link.strip()]

    if not raw_links:
        await update.message.reply_text("没有检测到消息链接，请重新输入。")
        return ADMIN_FWD_CMD_ADD_LINKS
    if len(raw_links) > 50:
        await update.message.reply_text(f"消息链接数量过多（{len(raw_links)}条），最多支持50条。请精简后重新输入。")
        return ADMIN_FWD_CMD_ADD_LINKS
    
    parsed_messages = []
    invalid_links = []
    
    for link in raw_links:
        match = TELEGRAM_MESSAGE_LINK_REGEX.search(link)
        if match:
            chat_identifier = match.group(1) or match.group(2) # Numerical or username
            message_id = int(match.group(3))

            chat_id = None
            if chat_identifier.isdigit(): # Numerical chat ID (private channel)
                chat_id = int("-100" + chat_identifier) 
            elif chat_identifier: # Username (public channel)
                chat_id = "@" + chat_identifier
            
            if chat_id:
                parsed_messages.append({'chat_id': str(chat_id), 'message_id': message_id})
            else:
                invalid_links.append(link)
        else:
            invalid_links.append(link)
    
    if invalid_links:
        await update.message.reply_text(
            f"以下链接格式不正确或无法解析，请检查后重新输入：\n`{'`\n`'.join(invalid_links)}`",
            parse_mode='Markdown'
        )
        return ADMIN_FWD_CMD_ADD_LINKS

    if not parsed_messages:
        await update.message.reply_text("没有有效的消息链接被解析出来，请重新输入。")
        return ADMIN_FWD_CMD_ADD_LINKS

    command_name_input = context.user_data.get('temp_fwd_command_name')
    if not command_name_input:
        await update.message.reply_text("命令名称丢失，请从头重新添加命令。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回频道转发库", callback_data="admin_fwd_cmd_menu")]]) )
        return ConversationHandler.END

    async with db_pool.acquire() as conn:
        await conn.execute(
            'INSERT INTO forward_commands (command_name, message_links, parsed_messages, created_by) VALUES ($1, $2, $3, $4)',
            command_name_input.lower(), raw_links, parsed_messages, update.effective_user.id # 存储小写命令名
        )
    
    for key in ['temp_fwd_command_name']:
        if key in context.user_data:
            del context.user_data[key]

    await update.message.reply_text(
        f"✅ 转发命令 `{command_name_input}` 添加成功！包含 {len(parsed_messages)} 条消息。",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回频道转发库", callback_data="admin_fwd_cmd_menu")]])
    )
    return ConversationHandler.END

async def admin_list_fwd_cmds_to_manage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """列出所有转发命令供管理员管理。"""
    query = update.callback_query
    await query.answer()

    async with db_pool.acquire() as conn:
        commands = await conn.fetch('SELECT command_name FROM forward_commands ORDER BY created_at')
    
    keyboard = []
    if not commands:
        await query.edit_message_text("暂无转发命令。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回频道转发库", callback_data="admin_fwd_cmd_menu")]]) )
        return ConversationHandler.END
    
    for cmd in commands:
        # 显示存储的命令名 (小写)
        keyboard.append([InlineKeyboardButton(cmd['command_name'], callback_data=f"admin_select_fwd_cmd:{cmd['command_name']}")])

    keyboard.append([InlineKeyboardButton("🔙 返回频道转发库", callback_data="admin_fwd_cmd_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "✍️ **选择要管理的转发命令：**",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def admin_select_fwd_cmd_to_manage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """管理员选择特定转发命令进行管理。"""
    query = update.callback_query
    await query.answer()

    cmd_name_lower = query.data.split(':')[1] # This will be the lowercased version stored in DB
    context.user_data['admin_managing_fwd_cmd_name'] = cmd_name_lower

    async with db_pool.acquire() as conn:
        command_data = await conn.fetchrow('SELECT command_name, message_links FROM forward_commands WHERE command_name = $1', cmd_name_lower)
    
    if not command_data:
        await query.edit_message_text("❌ 转发命令不存在。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回命令列表", callback_data="admin_list_fwd_cmds_to_manage")]]) )
        return ConversationHandler.END
    
    links_text = "\n".join(command_data['message_links'])

    cmd_details = (
        f"**转发命令详情：** `{command_data['command_name']}`\n\n"
        f"消息链接 ({len(command_data['message_links'])} 条)：\n`{links_text}`"
    )
    keyboard = [
        [InlineKeyboardButton("🗑️ 删除该命令", callback_data=f"admin_confirm_delete_fwd_cmd:{cmd_name_lower}")],
        [InlineKeyboardButton("🔙 返回命令列表", callback_data="admin_list_fwd_cmds_to_manage")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(cmd_details, reply_markup=reply_markup, parse_mode='Markdown')
    return ConversationHandler.END

async def admin_confirm_delete_fwd_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """管理员确认删除转发命令。"""
    query = update.callback_query
    await query.answer("确认删除此转发命令？", show_alert=True)

    cmd_name_lower = query.data.split(':')[1]

    keyboard = [
        [InlineKeyboardButton(f"✅ 确认删除 {cmd_name_lower}", callback_data=f"admin_delete_fwd_cmd_final:{cmd_name_lower}")],
        [InlineKeyboardButton("↩️ 取消", callback_data=f"admin_select_fwd_cmd:{cmd_name_lower}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"您确定要删除转发命令 `{cmd_name_lower}` 吗？此操作不可撤销。",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return ADMIN_FWD_CMD_MANAGE_CONFIRM_DELETE

async def admin_delete_fwd_cmd_final(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """管理员最终执行删除转发命令。"""
    query = update.callback_query
    await query.answer()

    cmd_name_lower = query.data.split(':')[1]

    async with db_pool.acquire() as conn:
        await conn.execute('DELETE FROM forward_commands WHERE command_name = $1', cmd_name_lower)
    
    await query.edit_message_text(
        f"✅ 转发命令 `{cmd_name_lower}` 已成功删除。",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回转发命令列表", callback_data="admin_list_fwd_cmds_to_manage")]])
    )
    return ConversationHandler.END

# --- [12] 用户触发动态转发命令 ---
async def handle_dynamic_forward_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理用户发送的动态转发命令。"""
    if not update.message or not update.message.text:
        return

    # Extract command, ignoring arguments and making it lowercase for matching
    command_text = update.message.text.split(' ')[0].lower() 
    user_chat_id = update.effective_chat.id
    original_message_id = update.message.message_id

    # Check if the command is /start or /admin (built-in commands) to avoid re-processing
    # These are handled by specific CommandHandlers
    reserved_commands = {"/start", "/admin"}
    if command_text in reserved_commands:
        return

    async with db_pool.acquire() as conn:
        command_data = await conn.fetchrow('SELECT parsed_messages FROM forward_commands WHERE command_name = $1', command_text)

    if command_data:
        # 1. 删除用户发送的命令消息
        try:
            await context.bot.delete_message(chat_id=user_chat_id, message_id=original_message_id)
        except Exception as e:
            logger.warning(f"Failed to delete user's command message {original_message_id} in chat {user_chat_id}: {e}")

        # 2. 转发消息并记录以便定时删除
        messages_to_copy = command_data['parsed_messages']
        deletion_time = datetime.now(TIMEZONE) + timedelta(seconds=FORWARDED_MESSAGE_LIFETIME_SECONDS)
        
        for msg_info in messages_to_copy:
            try:
                copied_message = await context.bot.copy_message(
                    chat_id=user_chat_id,
                    from_chat_id=msg_info['chat_id'], 
                    message_id=msg_info['message_id']
                )
                async with db_pool.acquire() as conn:
                    await conn.execute(
                        'INSERT INTO scheduled_deletions (chat_id, message_id, deletion_time) VALUES ($1, $2, $3)',
                        copied_message.chat_id, copied_message.message_id, deletion_time
                    )
            except Exception as e:
                logger.error(f"Failed to copy message from {msg_info['chat_id']}/{msg_info['message_id']} to {user_chat_id}: {e}")
                await context.bot.send_message(
                    chat_id=user_chat_id,
                    text="❌ 抱歉，部分内容暂时无法转发。请稍后再试或联系客服。\n"
                         "请确保机器人是该频道的管理员且拥有转发权限。",
                    parse_mode='Markdown'
                )
        
        # 3. 成功转发后，发送确认消息并提供跳转首页按钮
        await context.bot.send_message(
            chat_id=user_chat_id,
            text="✨ 内容已成功发送！请注意消息存在时间限制。",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 返回首页", callback_data="start_command")]])
        )
    # else: If no dynamic command matches, this handler implicitly finishes, allowing other handlers to run if applicable.

# --- [13] 错误处理器 ---
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """记录所有未处理的错误。"""
    logger.error(f'Update "{update}" caused error "{context.error}"')
    if update.effective_message:
        await update.effective_message.reply_text("🚧 抱歉，机器人遇到了一些问题，请稍后再试。")

# --- [14] 主函数和启动逻辑 ---
async def post_init(application: Application) -> None:
    """Application 初始化后执行，用于数据库连接和启动定时任务。"""
    await init_db_pool()
    job_queue: JobQueue = application.job_queue
    job_queue.run_repeating(delete_old_messages_job, interval=DELETE_CHECK_INTERVAL_SECONDS, first=5)
    logger.info("Delete old messages job scheduled.")

async def post_shutdown(application: Application) -> None:
    """Application 关闭前执行，用于关闭数据库连接。"""
    await close_db_pool()

def main() -> None:
    """主函数，设置并运行机器人。"""
    BOT_TOKEN = get_env_variable('BOT_TOKEN')
    
    application = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()

    # ConversationHandler 用于管理多步交互
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start_command),
            CallbackQueryHandler("start_validation", start_validation_flow),
            CallbackQueryHandler("ask_vip_order_id", ask_vip_order_id),
            CallbackQueryHandler("confirm_wechat_payment", confirm_wechat_payment),
            CallbackQueryHandler("confirm_alipay_payment", confirm_alipay_payment),
            CallbackQueryHandler(f"select_redeem_item", select_redeem_item, pattern=r"^select_redeem_item:.*$"),
            CallbackQueryHandler("confirm_redeem", confirm_redeem),
            # Admin conversation entry points
            CallbackQueryHandler("admin_add_product_start", admin_add_product_start),
            CallbackQueryHandler("admin_add_fwd_cmd_start", admin_add_fwd_cmd_start),
        ],
        states={
            ASKING_FOR_VIP_ORDER_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_vip_order_id)
            ],
            ASKING_WECHAT_ORDER_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_wechat_order_id)
            ],
            ASKING_ALIPAY_ORDER_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_alipay_order_id)
            ],
            CONFIRM_REDEMPTION: [
                CallbackQueryHandler("confirm_redeem", confirm_redeem),
                CallbackQueryHandler("show_redeem_menu", show_redeem_menu),
            ],
            # Admin Product Add States
            ADMIN_PRODUCT_ADD_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_receive_product_id),
            ],
            ADMIN_PRODUCT_ADD_TYPE: [
                CallbackQueryHandler(r"^set_product_type:.*$", admin_receive_product_type),
            ],
            ADMIN_PRODUCT_ADD_CONTENT: [
                MessageHandler((filters.TEXT | filters.PHOTO | filters.VIDEO) & ~filters.COMMAND, admin_receive_product_content),
            ],
            ADMIN_PRODUCT_ADD_POINTS_COST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_receive_product_points_cost),
            ],
            # Admin Forward Command States
            ADMIN_FWD_CMD_ADD_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_receive_fwd_cmd_name),
            ],
            ADMIN_FWD_CMD_ADD_LINKS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_receive_fwd_message_links),
            ],
            ADMIN_FWD_CMD_MANAGE_CONFIRM_DELETE: [
                CallbackQueryHandler(r"^admin_delete_fwd_cmd_final:.*$", admin_delete_fwd_cmd_final),
                CallbackQueryHandler(r"^admin_select_fwd_cmd:.*$", admin_select_fwd_cmd_to_manage), # 取消删除
            ]
        },
        fallbacks=[
            CommandHandler("start", start_command), 
            CallbackQueryHandler("start_command", start_command), 
            CallbackQueryHandler("show_points_menu", show_points_menu), 
            CallbackQueryHandler("daily_check_in", daily_check_in),
            CallbackQueryHandler("top_up_points_menu", top_up_points_menu),
            CallbackQueryHandler("show_redeem_menu", show_redeem_menu),
            CallbackQueryHandler("show_wechat_top_up", show_wechat_top_up),
            CallbackQueryHandler("show_alipay_top_up", show_alipay_top_up),
            CallbackQueryHandler("show_balance", show_balance),
            CallbackQueryHandler("show_acquisition_history", show_acquisition_history),
            CallbackQueryHandler("show_usage_history", show_usage_history),
            CallbackQueryHandler("show_leaderboard", show_leaderboard),
            # Admin fallbacks
            CallbackQueryHandler("admin_back_to_main", admin_command),
            CallbackQueryHandler("admin_manage_products", admin_manage_products_menu),
            CallbackQueryHandler("admin_fwd_cmd_menu", admin_fwd_cmd_menu),
            CallbackQueryHandler("admin_list_products_to_manage", admin_list_products_to_manage),
            CallbackQueryHandler("admin_list_fwd_cmds_to_manage", admin_list_fwd_cmds_to_manage),
            # 通用取消，防止用户卡在某个输入流程
            # 这个 MessageHandler 应该放在最后，以确保其他更具体的 MessageHandler 优先
            MessageHandler(filters.COMMAND, start_command),
            # 如果不是命令，但是 ConversationHandler 的当前状态需要文本输入，且用户输入了其他东西，
            # 那么这个 fallback 会被触发。这里我们不处理它，让它继续。
            # 或者可以添加一个 generic_message_handler_fallback，但目前不是必须的
        ],
        allow_reentry=True,
    )
    application.add_handler(conv_handler)

    # 独立的处理器 (某些操作即使在对话中也能直接触发)
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CallbackQueryHandler("admin_file_id", admin_prompt_for_file_id))
    # admin_get_file_id 放在所有 CommandHandler/CallbackQueryHandler 之后，以免它干扰其他指令
    application.add_handler(MessageHandler(filters.PHOTO | filters.DOCUMENT | filters.VIDEO, admin_get_file_id, block=False)) 

    # 管理员按钮的回调
    application.add_handler(CallbackQueryHandler("admin_back_to_main", admin_command))
    application.add_handler(CallbackQueryHandler("admin_manage_products", admin_manage_products_menu))
    application.add_handler(CallbackQueryHandler("admin_list_products_to_manage", admin_list_products_to_manage))
    application.add_handler(CallbackQueryHandler(r"^admin_select_product:.*$", admin_select_product_to_manage))
    application.add_handler(CallbackQueryHandler(r"^admin_toggle_product_status:.*$", admin_toggle_product_status))
    application.add_handler(CallbackQueryHandler(r"^admin_delete_product:.*$", admin_delete_product))
    
    application.add_handler(CallbackQueryHandler("admin_fwd_cmd_menu", admin_fwd_cmd_menu))
    application.add_handler(CallbackQueryHandler("admin_list_fwd_cmds_to_manage", admin_list_fwd_cmds_to_manage))
    application.add_handler(CallbackQueryHandler(r"^admin_select_fwd_cmd:.*$", admin_select_fwd_cmd_to_manage))
    application.add_handler(CallbackQueryHandler(r"^admin_confirm_delete_fwd_cmd:.*$", admin_confirm_delete_fwd_cmd))

    # 群组欢迎/离开处理器 (优先处理)
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_member_handler))
    application.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, left_member_handler))

    # 动态转发命令处理器 (必须放在 CommandHandler 和 ConversationHandler 之后，因为它匹配所有命令)
    application.add_handler(MessageHandler(filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE, handle_dynamic_forward_command))


    # 错误处理器
    application.add_error_handler(error_handler)

    logger.info("Bot started polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
