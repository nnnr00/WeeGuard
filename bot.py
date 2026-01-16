import os
import re
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMemberUpdated
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ChatMemberHandler,
    filters,
    ContextTypes,
)
from database import db

# 日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 环境变量
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
GROUP_ID = int(os.getenv("GROUP_ID", "0"))

# 配置
VIP_GROUP_LINK = "https://t.me/+495j5rWmApsxYzg9"

# File IDs
FILE_ID_VIP_INFO = os.getenv("FILE_ID_VIP_INFO", "")
FILE_ID_ORDER_TUTORIAL = os.getenv("FILE_ID_ORDER_TUTORIAL", "")
FILE_ID_WECHAT_QR = os.getenv("FILE_ID_WECHAT_QR", "")
FILE_ID_WECHAT_TUTORIAL = os.getenv("FILE_ID_WECHAT_TUTORIAL", "")
FILE_ID_ALIPAY_QR = os.getenv("FILE_ID_ALIPAY_QR", "")
FILE_ID_ALIPAY_TUTORIAL = os.getenv("FILE_ID_ALIPAY_TUTORIAL", "")

# 临时状态存储
user_states = {}
admin_states = {}
temp_command_data = {}
temp_product_data = {}
waiting_for_file = set()

DELETE_DELAY = 20 * 60
LEADERBOARD_CYCLE = 3


def is_admin(user_id):
    return user_id == ADMIN_ID


def parse_message_link(link):
    link = link.strip()
    match = re.search(r't\.me/c/(\d+)/(\d+)', link)
    if match:
        return int('-100' + match.group(1)), int(match.group(2))
    match = re.search(r't\.me/([^/]+)/(\d+)', link)
    if match and match.group(1) != 'c':
        return '@' + match.group(1), int(match.group(2))
    return None, None


def get_cycle_end_time():
    now = datetime.now()
    days_since_epoch = (now - datetime(2024, 1, 1)).days
    days_in_cycle = days_since_epoch % LEADERBOARD_CYCLE
    days_until_end = LEADERBOARD_CYCLE - days_in_cycle
    return now + timedelta(days=days_until_end)


# ==================== 群成员变动处理 ====================

async def handle_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理群成员变动"""
    result = update.chat_member
    if not result:
        return
    
    chat_id = result.chat.id
    user = result.new_chat_member.user
    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    
    # 用户加入群
    if old_status in ['left', 'kicked'] and new_status in ['member', 'administrator']:
        is_first, points = await db.user_join_group(user.id, user.first_name)
        
        if is_first:
            # 首次进群
            keyboard = [
                [InlineKeyboardButton("🎁 领取更多福利", url=f"https://t.me/{context.bot.username}")],
                [InlineKeyboardButton("💎 VIP会员专区", url=VIP_GROUP_LINK)],
            ]
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🎉 **热烈欢迎 [{user.first_name}](tg://user?id={user.id}) 加入！**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "🎁 **新人专属福利已到账！**\n"
                    f"💰 首次进群奖励：**+{points}积分**\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "✨ **这里有什么？**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "📦 **积分商城** - 海量好礼等你兑换\n"
                    "💎 **VIP专区** - 一键进入会员群畅享\n"
                    "🎯 **每日签到** - 坚持打卡积分翻倍\n"
                    "🏆 **排行榜** - 争当积分王者\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "👇 点击下方按钮开启你的专属之旅"
                ),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        else:
            # 非首次进群
            keyboard = [[InlineKeyboardButton("🎁 进入机器人", url=f"https://t.me/{context.bot.username}")]]
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👋 **欢迎回来，{user.first_name}！**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "🏠 老朋友回归，我们很高兴~\n"
                    "📦 积分商城等你来逛！"
                ),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
    
    # 用户离开群
    elif old_status in ['member', 'administrator'] and new_status in ['left', 'kicked']:
        await db.user_leave_group(user.id)
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👋 **{user.first_name} 离开了群聊**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "📉 根据群规，已收回进群奖励积分\n"
                "🚪 期待下次再见~\n\n"
                "💡 _温馨提示：再次进群将不再获得新人奖励_"
            ),
            parse_mode='Markdown'
        )


# ==================== 欢迎页面 ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.first_name or "用户"
    user = await db.get_user(user_id, username)
    
    keyboard = [
        [InlineKeyboardButton("🚀 VIP会员验证", callback_data="start_verify")],
        [InlineKeyboardButton("💰 积分中心", callback_data="points_center")],
    ]
    
    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👋 **欢迎来到【VIP中转站】！**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🤖 我是守门员小卫，你的专属服务助手~\n\n"
        "📢 **小卫小卫，守门员小卫！**\n"
        "🎯 一键入群，小卫帮你搞定！\n"
        "🔍 新人报到，小卫查身份！\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 你的积分：**{user['points']}**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👇 选择你需要的服务",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


async def show_home(query, context, user):
    keyboard = [
        [InlineKeyboardButton("🚀 VIP会员验证", callback_data="start_verify")],
        [InlineKeyboardButton("💰 积分中心", callback_data="points_center")],
    ]
    
    await query.edit_message_text(
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👋 **欢迎来到【VIP中转站】！**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🤖 我是守门员小卫，你的专属服务助手~\n\n"
        "📢 **小卫小卫，守门员小卫！**\n"
        "🎯 一键入群，小卫帮你搞定！\n"
        "🔍 新人报到，小卫查身份！\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 你的积分：**{user['points']}**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👇 选择你需要的服务",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


# ==================== 按钮回调 ====================

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    username = query.from_user.first_name or "用户"
    data = query.data
    user = await db.get_user(user_id, username)
    
    await query.answer()
    
    # ========== 首页 ==========
    if data == "go_home":
        user_states.pop(user_id, None)
        user = await db.get_user(user_id)
        await show_home(query, context, user)
        return
    
    # ========== VIP验证 ==========
    if data == "start_verify":
        if user['vip_verified']:
            keyboard = [
                [InlineKeyboardButton("🎉 进入VIP专属群", url=VIP_GROUP_LINK)],
                [InlineKeyboardButton("🏠 返回首页", callback_data="go_home")],
            ]
            await query.edit_message_text(
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "✅ **VIP验证已完成**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "🎊 您已是尊贵的VIP会员！\n\n"
                "👇 点击按钮进入专属群聊",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return
        
        if user['vip_cooldown'] and datetime.now() < user['vip_cooldown']:
            remaining = user['vip_cooldown'] - datetime.now()
            hours = int(remaining.total_seconds() // 3600)
            minutes = int((remaining.total_seconds() % 3600) // 60)
            
            keyboard = [[InlineKeyboardButton("🏠 返回首页", callback_data="go_home")]]
            await query.edit_message_text(
                f"⏳ **验证暂时不可用**\n\n请在 {hours}小时{minutes}分钟 后重试",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return
        
        keyboard = [[InlineKeyboardButton("✅ 我已付款，立即验证", callback_data="vip_paid")]]
        
        if FILE_ID_VIP_INFO:
            try:
                await query.message.delete()
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=FILE_ID_VIP_INFO,
                    caption=(
                        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        "💎 **VIP会员尊享特权**\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "✨ 专属中转通道 - 极速稳定\n"
                        "✨ 优先审核入群 - 快人一步\n"
                        "✨ 7×24小时客服 - 随时响应\n"
                        "✨ 定期福利活动 - 惊喜不断\n\n"
                        "👇 已完成付款请点击下方按钮"
                    ),
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
                return
            except:
                pass
        
        await query.edit_message_text(
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "💎 **VIP会员尊享特权**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "✨ 专属中转通道\n✨ 优先审核入群\n✨ 7×24小时客服\n✨ 定期福利活动\n\n"
            "👇 已付款请点击下方按钮",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "vip_paid":
        keyboard = [[InlineKeyboardButton("🏠 返回首页", callback_data="go_home")]]
        user_states[user_id] = "waiting_vip_order"
        
        if FILE_ID_ORDER_TUTORIAL:
            try:
                await query.message.delete()
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=FILE_ID_ORDER_TUTORIAL,
                    caption=(
                        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        "📋 **订单号查找教程**\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "1️⃣ 打开支付APP\n2️⃣ 我的 → 账单\n3️⃣ 账单详情 → 更多\n4️⃣ 复制订单号\n\n"
                        "✏️ **请输入订单号：**"
                    ),
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
                return
            except:
                pass
        
        await query.edit_message_text(
            "📋 **请输入订单号**\n\n支付APP → 我的 → 账单 → 详情 → 订单号",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    # ========== 积分中心 ==========
    elif data == "points_center":
        user_states.pop(user_id, None)
        user = await db.get_user(user_id)
        keyboard = [
            [InlineKeyboardButton("📅 每日签到", callback_data="checkin")],
            [InlineKeyboardButton("💳 积分充值", callback_data="recharge")],
            [InlineKeyboardButton("🎁 积分兑换", callback_data="exchange")],
            [InlineKeyboardButton("💎 我的余额", callback_data="my_balance")],
            [InlineKeyboardButton("🏆 积分排行榜", callback_data="leaderboard")],
            [InlineKeyboardButton("🏠 返回首页", callback_data="go_home")],
        ]
        
        await query.edit_message_text(
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "💰 **积分中心**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💎 当前积分：**{user['points']}**\n"
            f"📊 累计获得：**{user['total_earned']}**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📌 **积分获取方式：**\n"
            "• 📅 每日签到：3~8积分\n"
            "• 💳 充值：5元=100积分\n"
            "• 🎉 首次进群：20积分\n\n"
            "👇 请选择操作",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    # ========== 签到 ==========
    elif data == "checkin":
        points = await db.checkin(user_id)
        user = await db.get_user(user_id)
        keyboard = [[InlineKeyboardButton("◀️ 返回积分中心", callback_data="points_center")]]
        
        if points is None:
            await query.edit_message_text(
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📅 **今日已签到**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "✅ 你今天已经签到过啦~\n⏰ 请明天再来！\n\n"
                f"💎 当前积分：**{user['points']}**",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "🎉 **签到成功！**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🎁 获得 **+{points}** 积分\n\n"
                f"💎 当前积分：**{user['points']}**",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
    
    # ========== 充值 ==========
    elif data == "recharge":
        keyboard = [
            [InlineKeyboardButton("💚 微信支付", callback_data="pay_wechat")],
            [InlineKeyboardButton("💙 支付宝支付", callback_data="pay_alipay")],
            [InlineKeyboardButton("◀️ 返回积分中心", callback_data="points_center")],
        ]
        await query.edit_message_text(
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "💳 **积分充值**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💰 充值比例：**5元 = 100积分**\n\n"
            "👇 请选择支付方式",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    # ========== 微信充值 ==========
    elif data == "pay_wechat":
        if user['wechat_used']:
            keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="recharge")]]
            await query.edit_message_text(
                "⚠️ **微信通道已使用**\n\n每人仅限一次，请使用支付宝",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return
        
        if user['wechat_cooldown'] and datetime.now() < user['wechat_cooldown']:
            remaining = user['wechat_cooldown'] - datetime.now()
            hours = int(remaining.total_seconds() // 3600)
            keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="recharge")]]
            await query.edit_message_text(f"⏳ 请在 {hours} 小时后重试", reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        keyboard = [[InlineKeyboardButton("✅ 已支付，验证订单", callback_data="wechat_paid")]]
        
        if FILE_ID_WECHAT_QR:
            try:
                await query.message.delete()
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=FILE_ID_WECHAT_QR,
                    caption=(
                        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        "💚 **微信充值**\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "💰 **5元 = 100积分**\n\n"
                        "⚠️━━━ **重要提醒** ━━━⚠️\n"
                        "🔴 每人仅限充值 **1次**\n"
                        "🔴 请勿重复支付\n\n"
                        "👇 支付完成后点击按钮"
                    ),
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
                return
            except:
                pass
        
        await query.edit_message_text(
            "💚 **微信充值** - 5元=100积分\n\n⚠️ 每人仅限1次\n\n👇 支付完成后点击",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "wechat_paid":
        keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="recharge")]]
        user_states[user_id] = "waiting_wechat_order"
        
        if FILE_ID_WECHAT_TUTORIAL:
            try:
                await query.message.delete()
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=FILE_ID_WECHAT_TUTORIAL,
                    caption="📋 **微信交易单号**\n\n微信→我→服务→钱包→账单→详情→交易单号\n\n✏️ 请输入：",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
                return
            except:
                pass
        
        await query.edit_message_text(
            "📋 请输入微信交易单号",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    # ========== 支付宝充值 ==========
    elif data == "pay_alipay":
        if user['alipay_used']:
            keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="recharge")]]
            await query.edit_message_text(
                "⚠️ **支付宝通道已使用**\n\n每人仅限一次，请使用微信",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return
        
        if user['alipay_cooldown'] and datetime.now() < user['alipay_cooldown']:
            remaining = user['alipay_cooldown'] - datetime.now()
            hours = int(remaining.total_seconds() // 3600)
            keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="recharge")]]
            await query.edit_message_text(f"⏳ 请在 {hours} 小时后重试", reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        keyboard = [[InlineKeyboardButton("✅ 已支付，验证订单", callback_data="alipay_paid")]]
        
        if FILE_ID_ALIPAY_QR:
            try:
                await query.message.delete()
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=FILE_ID_ALIPAY_QR,
                    caption=(
                        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        "💙 **支付宝充值**\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "💰 **5元 = 100积分**\n\n"
                        "⚠️━━━ **重要提醒** ━━━⚠️\n"
                        "🔴 每人仅限充值 **1次**\n"
                        "🔴 请勿重复支付\n\n"
                        "👇 支付完成后点击按钮"
                    ),
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
                return
            except:
                pass
        
        await query.edit_message_text(
            "💙 **支付宝充值** - 5元=100积分\n\n⚠️ 每人仅限1次",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "alipay_paid":
        keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="recharge")]]
        user_states[user_id] = "waiting_alipay_order"
        
        if FILE_ID_ALIPAY_TUTORIAL:
            try:
                await query.message.delete()
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=FILE_ID_ALIPAY_TUTORIAL,
                    caption="📋 **支付宝订单号**\n\n支付宝→我的→账单→详情→更多→商家订单号\n\n✏️ 请输入：",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
                return
            except:
                pass
        
        await query.edit_message_text(
            "📋 请输入支付宝商家订单号",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    # ========== 兑换商城 ==========
    elif data == "exchange":
        products = await db.get_products('on')
        redeemed = await db.get_user_redeemed(user_id)
        keyboard = []
        
        for pid, prod in products.items():
            if pid in redeemed:
                btn_text = f"✅ {prod['name']} 【已兑换】"
            else:
                btn_text = f"{prod['name']} - {prod['price']}积分"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"prod_{pid}")])
        
        keyboard.append([InlineKeyboardButton("◀️ 返回积分中心", callback_data="points_center")])
        
        await query.edit_message_text(
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🎁 **积分兑换商城**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💎 当前积分：**{user['points']}**\n\n"
            "👇 选择要兑换的商品",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data.startswith("prod_"):
        pid = data[5:]
        prod = await db.get_product(pid)
        if not prod:
            return
        
        is_redeemed = await db.is_redeemed(user_id, pid)
        
        if is_redeemed:
            keyboard = [[InlineKeyboardButton("◀️ 返回商城", callback_data="exchange")]]
            if prod['content_type'] == 'text':
                await query.edit_message_text(
                    f"🎁 **{prod['name']}**\n\n📦 内容：\n{prod['content']}",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
            elif prod['file_id']:
                try:
                    await query.message.delete()
                    if prod['content_type'] == 'photo':
                        await context.bot.send_photo(
                            chat_id=query.message.chat_id,
                            photo=prod['file_id'],
                            caption=f"🎁 {prod['name']}\n\n{prod.get('content', '')}",
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                    elif prod['content_type'] == 'video':
                        await context.bot.send_video(
                            chat_id=query.message.chat_id,
                            video=prod['file_id'],
                            caption=f"🎁 {prod['name']}\n\n{prod.get('content', '')}",
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                except:
                    await query.edit_message_text(f"🎁 {prod['name']}", reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        keyboard = [
            [InlineKeyboardButton("✅ 确认兑换", callback_data=f"confirm_{pid}")],
            [InlineKeyboardButton("❌ 取消", callback_data="exchange")],
        ]
        await query.edit_message_text(
            f"🛒 **确认兑换**\n\n"
            f"🎁 商品：{prod['name']}\n"
            f"💰 所需：{prod['price']}积分\n"
            f"💎 当前：{user['points']}积分\n\n"
            "确定兑换？",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data.startswith("confirm_"):
        pid = data[8:]
        prod = await db.get_product(pid)
        if not prod:
            return
        
        user = await db.get_user(user_id)
        
        if user['points'] < prod['price']:
            keyboard = [
                [InlineKeyboardButton("💳 去充值", callback_data="recharge")],
                [InlineKeyboardButton("◀️ 返回", callback_data="exchange")],
            ]
            await query.edit_message_text(
                f"❌ **积分不足**\n\n当前：{user['points']}，需要：{prod['price']}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return
        
        await db.spend_points(user_id, prod['price'], f"兑换：{prod['name']}")
        await db.add_redeem(user_id, pid)
        user = await db.get_user(user_id)
        
        keyboard = [[InlineKeyboardButton("◀️ 返回商城", callback_data="exchange")]]
        
        if prod['content_type'] == 'text':
            await query.edit_message_text(
                f"🎉 **兑换成功！**\n\n"
                f"🎁 {prod['name']}\n"
                f"💰 消耗：-{prod['price']}积分\n"
                f"💎 剩余：{user['points']}积分\n\n"
                f"📦 内容：\n{prod['content']}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        elif prod['file_id']:
            try:
                await query.message.delete()
                caption = f"🎉 兑换成功！\n\n🎁 {prod['name']}\n💰 -{prod['price']}积分\n\n{prod.get('content', '')}"
                if prod['content_type'] == 'photo':
                    await context.bot.send_photo(
                        chat_id=query.message.chat_id,
                        photo=prod['file_id'],
                        caption=caption,
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                elif prod['content_type'] == 'video':
                    await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=prod['file_id'],
                        caption=caption,
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
            except:
                await query.edit_message_text(f"🎉 兑换成功！{prod['content']}", reply_markup=InlineKeyboardMarkup(keyboard))
    
    # ========== 我的余额 ==========
    elif data == "my_balance":
        user = await db.get_user(user_id)
        history = await db.get_history(user_id, 10)
        
        history_text = ""
        for h in history:
            time_str = h['created_at'].strftime("%m-%d %H:%M")
            if h['action_type'] == 'earn':
                history_text += f"🟢 +{h['amount']} | {h['description']} | {time_str}\n"
            else:
                history_text += f"🔴 -{h['amount']} | {h['description']} | {time_str}\n"
        
        if not history_text:
            history_text = "暂无记录"
        
        keyboard = [[InlineKeyboardButton("◀️ 返回积分中心", callback_data="points_center")]]
        
        await query.edit_message_text(
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "💎 **我的余额**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 当前积分：**{user['points']}**\n"
            f"📊 累计获得：**{user['total_earned']}**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📜 **最近记录**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{history_text}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    # ========== 排行榜 ==========
    elif data == "leaderboard":
        leaderboard = await db.get_leaderboard(10)
        user_rank = await db.get_user_rank(user_id)
        user = await db.get_user(user_id)
        end_time = get_cycle_end_time()
        remaining = end_time - datetime.now()
        
        medals = ["🥇", "🥈", "🥉"]
        rank_text = ""
        for i, u in enumerate(leaderboard):
            medal = medals[i] if i < 3 else f"{i+1}."
            name = (u['username'] or "用户")[:8]
            if u['user_id'] == user_id:
                rank_text += f"**{medal} {name} - {u['total_earned']}积分 ← 你**\n"
            else:
                rank_text += f"{medal} {name} - {u['total_earned']}积分\n"
        
        if not rank_text:
            rank_text = "暂无数据"
        
        keyboard = [[InlineKeyboardButton("◀️ 返回积分中心", callback_data="points_center")]]
        
        await query.edit_message_text(
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🏆 **积分排行榜**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📅 本期剩余：{remaining.days}天\n"
            f"📊 你的排名：第{user_rank}名\n"
            f"💎 你的积分：{user['total_earned']}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{rank_text}\n"
            "💡 按累计获得排名，每3天刷新",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    # ========== 管理员功能 ==========
    elif data == "admin_panel":
        if not is_admin(user_id):
            return
        user_count = await db.get_user_count()
        product_count = await db.get_product_count()
        commands = await db.get_all_commands()
        
        keyboard = [
            [InlineKeyboardButton("📁 获取 File ID", callback_data="get_file_id")],
            [InlineKeyboardButton("📚 频道转发库", callback_data="channel_library")],
            [InlineKeyboardButton("🛒 商品管理", callback_data="product_manage")],
            [InlineKeyboardButton("📊 用户统计", callback_data="user_stats")],
        ]
        await query.edit_message_text(
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔧 **小卫管理后台**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "👋 欢迎回来，尊敬的管理员！\n\n"
            f"👥 用户数：**{user_count}**\n"
            f"🛒 商品数：**{product_count}**\n"
            f"📚 命令数：**{len(commands)}**\n\n"
            "👇 请选择功能",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "get_file_id":
        if not is_admin(user_id):
            return
        waiting_for_file.add(user_id)
        keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="admin_panel")]]
        await query.edit_message_text(
            "📁 **获取 File ID**\n\n请发送文件（图片/视频/文档）\n\n/cancel 取消",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "channel_library":
        if not is_admin(user_id):
            return
        commands = await db.get_all_commands()
        keyboard = [
            [InlineKeyboardButton("➕ 添加命令", callback_data="add_command")],
            [InlineKeyboardButton("📋 查看列表", callback_data="list_commands")],
            [InlineKeyboardButton("🗑 删除命令", callback_data="delete_command")],
            [InlineKeyboardButton("◀️ 返回", callback_data="admin_panel")],
        ]
        await query.edit_message_text(
            f"📚 **频道转发库**\n\n当前命令数：{len(commands)}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "add_command":
        if not is_admin(user_id):
            return
        admin_states[user_id] = "waiting_command"
        keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="channel_library")]]
        await query.edit_message_text(
            "➕ **添加命令**\n\n请输入命令名称：\n\n/cancel 取消",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "list_commands":
        if not is_admin(user_id):
            return
        commands = await db.get_all_commands()
        text = "📋 **命令列表**\n\n"
        if commands:
            for cmd, count in commands.items():
                text += f"• `{cmd}` → {count}条\n"
        else:
            text += "暂无命令"
        keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="channel_library")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
    elif data == "delete_command":
        if not is_admin(user_id):
            return
        commands = await db.get_all_commands()
        if not commands:
            keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="channel_library")]]
            await query.edit_message_text("暂无命令", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            keyboard = [[InlineKeyboardButton(f"🗑 {cmd}", callback_data=f"delcmd_{cmd}")] for cmd in commands]
            keyboard.append([InlineKeyboardButton("◀️ 返回", callback_data="channel_library")])
            await query.edit_message_text("选择要删除的命令：", reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("delcmd_"):
        if not is_admin(user_id):
            return
        cmd = data[7:]
        await db.delete_command(cmd)
        keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="channel_library")]]
        await query.edit_message_text(f"✅ 已删除：`{cmd}`", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
    # ========== 商品管理 ==========
    elif data == "product_manage":
        if not is_admin(user_id):
            return
        products = await db.get_products()
        on_count = sum(1 for p in products.values() if p['status'] == 'on')
        
        keyboard = [
            [InlineKeyboardButton("➕ 添加商品", callback_data="add_product")],
            [InlineKeyboardButton("📋 查看商品", callback_data="list_products")],
            [InlineKeyboardButton("🔄 上下架", callback_data="toggle_products")],
            [InlineKeyboardButton("🗑 删除商品", callback_data="delete_products")],
            [InlineKeyboardButton("◀️ 返回", callback_data="admin_panel")],
        ]
        await query.edit_message_text(
            f"🛒 **商品管理**\n\n总数：{len(products)} | 上架：{on_count}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "add_product":
        if not is_admin(user_id):
            return
        admin_states[user_id] = "waiting_product_id"
        keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="product_manage")]]
        await query.edit_message_text(
            "➕ **添加商品**\n\n第1步：输入商品ID\n\n/cancel 取消",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "list_products":
        if not is_admin(user_id):
            return
        products = await db.get_products()
        text = "📋 **商品列表**\n\n"
        for pid, prod in products.items():
            status = "✅" if prod['status'] == 'on' else "❌"
            text += f"{status} **{pid}**: {prod['name']} - {prod['price']}积分\n"
        if not products:
            text += "暂无商品"
        keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="product_manage")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
    elif data == "toggle_products":
        if not is_admin(user_id):
            return
        products = await db.get_products()
        if not products:
            keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="product_manage")]]
            await query.edit_message_text("暂无商品", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            keyboard = []
            for pid, prod in products.items():
                status = "🟢" if prod['status'] == 'on' else "🔴"
                keyboard.append([InlineKeyboardButton(f"{status} {pid}", callback_data=f"toggle_{pid}")])
            keyboard.append([InlineKeyboardButton("◀️ 返回", callback_data="product_manage")])
            await query.edit_message_text("点击切换状态：", reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("toggle_"):
        if not is_admin(user_id):
            return
        pid = data[7:]
        await db.toggle_product(pid)
        keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="toggle_products")]]
        await query.edit_message_text(f"✅ 已切换：{pid}", reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "delete_products":
        if not is_admin(user_id):
            return
        products = await db.get_products()
        if not products:
            keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="product_manage")]]
            await query.edit_message_text("暂无商品", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            keyboard = [[InlineKeyboardButton(f"🗑 {pid}", callback_data=f"delprod_{pid}")] for pid in products]
            keyboard.append([InlineKeyboardButton("◀️ 返回", callback_data="product_manage")])
            await query.edit_message_text("选择删除：", reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("delprod_"):
        if not is_admin(user_id):
            return
        pid = data[8:]
        await db.delete_product(pid)
        keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="product_manage")]]
        await query.edit_message_text(f"✅ 已删除：{pid}", reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "user_stats":
        if not is_admin(user_id):
            return
        user_count = await db.get_user_count()
        total_points, total_earned = await db.get_total_points()
        vip_count = await db.get_vip_count()
        
        keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="admin_panel")]]
        await query.edit_message_text(
            "📊 **用户统计**\n\n"
            f"👥 用户总数：{user_count}\n"
            f"💎 VIP会员：{vip_count}\n"
            f"💰 积分总额：{total_points}\n"
            f"📈 累计发放：{total_earned}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    # 商品类型选择
    elif data.startswith("prodtype_"):
        if not is_admin(user_id):
            return
        ptype = data[9:]
        if user_id in temp_product_data:
            temp_product_data[user_id]["type"] = ptype
            if ptype == "text":
                admin_states[user_id] = "waiting_product_content"
                await query.edit_message_text("📝 第5步：输入文本内容\n\n/cancel 取消")
            else:
                admin_states[user_id] = "waiting_product_file"
                await query.edit_message_text(f"📤 第5步：发送{'图片' if ptype == 'photo' else '视频'}\n\n/cancel 取消")


# ==================== 消息处理 ====================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    username = update.effective_user.first_name or "用户"
    user = await db.get_user(user_id, username)
    
    # VIP订单验证
    if user_states.get(user_id) == "waiting_vip_order":
        if re.match(r'^20260\d+$', text):
            await db.update_user(user_id, vip_verified=True)
            user_states.pop(user_id, None)
            
            keyboard = [
                [InlineKeyboardButton("🎉 进入VIP群", url=VIP_GROUP_LINK)],
                [InlineKeyboardButton("🏠 返回首页", callback_data="go_home")],
            ]
            await update.message.reply_text(
                "🎊 **验证成功！**\n\n✅ 恭喜成为VIP会员！",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        else:
            new_attempts = user['vip_attempts'] + 1
            if new_attempts >= 2:
                await db.update_user(user_id, vip_attempts=new_attempts, vip_cooldown=datetime.now() + timedelta(hours=5))
                user_states.pop(user_id, None)
                keyboard = [[InlineKeyboardButton("🏠 返回首页", callback_data="go_home")]]
                await update.message.reply_text("❌ 验证失败，请5小时后重试", reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await db.update_user(user_id, vip_attempts=new_attempts)
                keyboard = [[InlineKeyboardButton("🏠 返回首页", callback_data="go_home")]]
                await update.message.reply_text(f"❌ 订单未找到\n\n剩余次数：{2-new_attempts}", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # 微信订单验证
    if user_states.get(user_id) == "waiting_wechat_order":
        if re.match(r'^4200\d+$', text):
            await db.add_points(user_id, 100, "微信充值")
            await db.update_user(user_id, wechat_used=True)
            user_states.pop(user_id, None)
            user = await db.get_user(user_id)
            keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="points_center")]]
            await update.message.reply_text(
                f"🎉 **充值成功！**\n\n+100积分\n当前：{user['points']}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        else:
            new_attempts = user['wechat_attempts'] + 1
            if new_attempts >= 2:
                await db.update_user(user_id, wechat_attempts=new_attempts, wechat_cooldown=datetime.now() + timedelta(hours=10))
                user_states.pop(user_id, None)
                keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="points_center")]]
                await update.message.reply_text("❌ 验证失败，请10小时后重试", reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await db.update_user(user_id, wechat_attempts=new_attempts)
                keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="points_center")]]
                await update.message.reply_text(f"❌ 订单识别失败\n\n剩余次数：{2-new_attempts}", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # 支付宝订单验证
    if user_states.get(user_id) == "waiting_alipay_order":
        if re.match(r'^4768\d+$', text):
            await db.add_points(user_id, 100, "支付宝充值")
            await db.update_user(user_id, alipay_used=True)
            user_states.pop(user_id, None)
            user = await db.get_user(user_id)
            keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="points_center")]]
            await update.message.reply_text(
                f"🎉 **充值成功！**\n\n+100积分\n当前：{user['points']}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        else:
            new_attempts = user['alipay_attempts'] + 1
            if new_attempts >= 2:
                await db.update_user(user_id, alipay_attempts=new_attempts, alipay_cooldown=datetime.now() + timedelta(hours=10))
                user_states.pop(user_id, None)
                keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="points_center")]]
                await update.message.reply_text("❌ 验证失败，请10小时后重试", reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await db.update_user(user_id, alipay_attempts=new_attempts)
                keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="points_center")]]
                await update.message.reply_text(f"❌ 订单识别失败\n\n剩余次数：{2-new_attempts}", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # 管理员添加命令
    if admin_states.get(user_id) == "waiting_command":
        if await db.command_exists(text):
            await update.message.reply_text(f"❌ 命令 `{text}` 已存在", parse_mode='Markdown')
            return
        temp_command_data[user_id] = {"command": text, "links": []}
        admin_states[user_id] = "waiting_links"
        await update.message.reply_text(
            f"✅ 命令：`{text}`\n\n请发送链接（每行一个，最多50条）\n\n/done 完成\n/cancel 取消",
            parse_mode='Markdown'
        )
        return
    
    if admin_states.get(user_id) == "waiting_links":
        lines = text.split('\n')
        added = 0
        for line in lines:
            if len(temp_command_data[user_id]["links"]) >= 50:
                break
            chat_id_parsed, message_id = parse_message_link(line.strip())
            if chat_id_parsed and message_id:
                temp_command_data[user_id]["links"].append({"chat_id": chat_id_parsed, "message_id": message_id})
                added += 1
        await update.message.reply_text(f"✅ 添加：{added}条\n总计：{len(temp_command_data[user_id]['links'])}/50\n\n继续发送或 /done")
        return
    
    # 管理员添加商品
    if admin_states.get(user_id) == "waiting_product_id":
        prod = await db.get_product(text)
        if prod:
            await update.message.reply_text(f"❌ ID `{text}` 已存在", parse_mode='Markdown')
            return
        temp_product_data[user_id] = {"id": text}
        admin_states[user_id] = "waiting_product_name"
        await update.message.reply_text(f"✅ ID：`{text}`\n\n第2步：输入商品名称", parse_mode='Markdown')
        return
    
    if admin_states.get(user_id) == "waiting_product_name":
        temp_product_data[user_id]["name"] = text
        admin_states[user_id] = "waiting_product_price"
        await update.message.reply_text(f"✅ 名称：{text}\n\n第3步：输入价格（数字）")
        return
    
    if admin_states.get(user_id) == "waiting_product_price":
        try:
            price = int(text)
            temp_product_data[user_id]["price"] = price
            admin_states[user_id] = "waiting_product_type"
            keyboard = [
                [InlineKeyboardButton("📝 文本", callback_data="prodtype_text")],
                [InlineKeyboardButton("🖼 图片", callback_data="prodtype_photo")],
                [InlineKeyboardButton("🎬 视频", callback_data="prodtype_video")],
            ]
            await update.message.reply_text(
                f"✅ 价格：{price}积分\n\n第4步：选择内容类型",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except:
            await update.message.reply_text("❌ 请输入数字")
        return
    
    if admin_states.get(user_id) == "waiting_product_content":
        pid = temp_product_data[user_id]["id"]
        await db.add_product(
            pid,
            temp_product_data[user_id]["name"],
            temp_product_data[user_id]["price"],
            "text",
            text
        )
        admin_states.pop(user_id, None)
        temp_product_data.pop(user_id, None)
        keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="product_manage")]]
        await update.message.reply_text(f"✅ 商品 `{pid}` 添加成功！", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return
    
    # 用户命令（频道转发库）
    links = await db.get_command(text)
    if links:
        messages_to_delete = []
        try:
            await update.message.delete()
        except:
            pass
        
        for item in links:
            try:
                sent = await context.bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=item["chat_id"],
                    message_id=item["message_id"]
                )
                messages_to_delete.append(sent.message_id)
            except Exception as e:
                logger.error(f"转发失败: {e}")
        
        if messages_to_delete:
            tip_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ 获取成功（{len(messages_to_delete)}条）\n⏰ 20分钟后删除"
            )
            messages_to_delete.append(tip_msg.message_id)
            
            context.job_queue.run_once(
                delete_messages_later,
                when=DELETE_DELAY,
                data={"chat_id": chat_id, "message_ids": messages_to_delete}
            )


async def delete_messages_later(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    for msg_id in data['message_ids']:
        try:
            await context.bot.delete_message(chat_id=data['chat_id'], message_id=msg_id)
        except:
            pass
    
    keyboard = [[InlineKeyboardButton("🏠 返回首页", callback_data="go_home")]]
    await context.bot.send_message(
        chat_id=data['chat_id'],
        text="⏰ **内容已过期**\n\n已购买用户可重新发送命令查看",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message = update.message
    
    if user_id in waiting_for_file:
        waiting_for_file.discard(user_id)
        file_id = None
        file_type = None
        
        if message.photo:
            file_id = message.photo[-1].file_id
            file_type = "📷 图片"
        elif message.video:
            file_id = message.video.file_id
            file_type = "🎬 视频"
        elif message.document:
            file_id = message.document.file_id
            file_type = "📄 文档"
        elif message.sticker:
            file_id = message.sticker.file_id
            file_type = "🎭 贴纸"
        elif message.animation:
            file_id = message.animation.file_id
            file_type = "🖼 GIF"
        
        if file_id:
            keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="admin_panel")]]
            await message.reply_text(
                f"✅ {file_type}\n\n**File ID：**\n`{file_id}`",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        return
    
    if admin_states.get(user_id) == "waiting_product_file":
        file_id = None
        if message.photo:
            file_id = message.photo[-1].file_id
        elif message.video:
            file_id = message.video.file_id
        
        if file_id and user_id in temp_product_data:
            pid = temp_product_data[user_id]["id"]
            await db.add_product(
                pid,
                temp_product_data[user_id]["name"],
                temp_product_data[user_id]["price"],
                temp_product_data[user_id]["type"],
                "",
                file_id
            )
            admin_states.pop(user_id, None)
            temp_product_data.pop(user_id, None)
            keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="product_manage")]]
            await message.reply_text(f"✅ 商品 `{pid}` 添加成功！", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ 无权限")
        return
    
    user_count = await db.get_user_count()
    product_count = await db.get_product_count()
    commands = await db.get_all_commands()
    
    keyboard = [
        [InlineKeyboardButton("📁 获取 File ID", callback_data="get_file_id")],
        [InlineKeyboardButton("📚 频道转发库", callback_data="channel_library")],
        [InlineKeyboardButton("🛒 商品管理", callback_data="product_manage")],
        [InlineKeyboardButton("📊 用户统计", callback_data="user_stats")],
    ]
    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔧 **小卫管理后台**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👋 欢迎回来，尊敬的管理员！\n\n"
        f"👥 用户数：**{user_count}**\n"
        f"🛒 商品数：**{product_count}**\n"
        f"📚 命令数：**{len(commands)}**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    waiting_for_file.discard(user_id)
    admin_states.pop(user_id, None)
    temp_command_data.pop(user_id, None)
    temp_product_data.pop(user_id, None)
    user_states.pop(user_id, None)
    await update.message.reply_text("❌ 已取消\n\n/start 首页\n/admin 后台")


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if admin_states.get(user_id) != "waiting_links":
        return
    
    if not temp_command_data.get(user_id, {}).get("links"):
        await update.message.reply_text("❌ 还没有添加链接！")
        return
    
    cmd = temp_command_data[user_id]["command"]
    await db.add_command(cmd, temp_command_data[user_id]["links"])
    
    del admin_states[user_id]
    del temp_command_data[user_id]
    
    keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="channel_library")]]
    await update.message.reply_text(
        f"✅ 命令 `{cmd}` 添加成功！",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


async def post_init(application):
    """启动时连接数据库"""
    await db.connect()
    logger.info("数据库已连接")


def main():
    if not BOT_TOKEN:
        raise ValueError("请设置 BOT_TOKEN！")
    if not ADMIN_ID:
        raise ValueError("请设置 ADMIN_ID！")
    
    logger.info(f"Bot启动中... 管理员: {ADMIN_ID}")
    
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    # 命令
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("done", done_command))
    
    # 群成员变动
    app.add_handler(ChatMemberHandler(handle_chat_member, ChatMemberHandler.CHAT_MEMBER))
    
    # 按钮
    app.add_handler(CallbackQueryHandler(button_callback))
    
    # 文件
    app.add_handler(MessageHandler(
        filters.PHOTO | filters.VIDEO | filters.Document.ALL |
        filters.AUDIO | filters.Sticker.ALL | filters.ANIMATION,
        handle_file
    ))
    
    # 文本
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    logger.info("小卫机器人已启动！")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
