import os
import logging
import asyncio
import re
import random
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)
from telegram.error import TelegramError

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 从环境变量获取配置
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))
DATABASE_URL = os.getenv('DATABASE_URL')
GROUP_INVITE_LINK = os.getenv('GROUP_INVITE_LINK', 'https://t.me/your_group')

# 会话状态
WAITING_FOR_FILE = 1
WAITING_FOR_ORDER = 2

# 用户验证失败记录
user_locks = {}

# 频道转发库存储
forward_library = {}

# 临时存储正在创建的命令
temp_commands = {}

# 用户消息删除任务
delete_tasks = {}

# File ID 临时存储
file_id_storage = {
    'PAYMENT_IMAGE': '',
    'TUTORIAL_IMAGE': '',
    'WECHAT_PAY_IMAGE': '',
    'WECHAT_TUTORIAL_IMAGE': '',
    'ALIPAY_PAY_IMAGE': '',
    'ALIPAY_TUTORIAL_IMAGE': ''
}

# ============== 积分系统数据 ==============

# 用户积分
user_points = {}

# 签到记录
signin_records = {}

# 充值记录
recharge_records = {}

# 充值失败记录
recharge_locks = {}

# 等待充值订单号输入
waiting_recharge_order = {}

# ============== 兑换系统数据 ==============

# 商品列表 {product_id: {'name': str, 'points': int, 'content': {'type': str, 'data': any}}}
products = {
    'test_product': {
        'name': '测试商品',
        'points': 0,
        'content': {
            'type': 'text',
            'data': '哈哈'
        }
    }
}

# 用户兑换记录 {user_id: [product_id, ...]}
user_exchanges = {}

# 临时存储正在创建的商品 {user_id: {'name': str, 'points': int, 'content': {...}}}
temp_products = {}

# 积分使用记录 {user_id: [{'time': datetime, 'type': str, 'points': int, 'desc': str}, ...]}
points_history = {}

# ============== 积分历史记录函数 ==============

def add_points_history(user_id: int, points_type: str, points: int, description: str):
    """添加积分历史记录"""
    if user_id not in points_history:
        points_history[user_id] = []
    
    points_history[user_id].append({
        'time': datetime.now(),
        'type': points_type,  # 'earn' 获得, 'spend' 消费
        'points': points,
        'desc': description
    })
    
    # 只保留最近50条记录
    if len(points_history[user_id]) > 50:
        points_history[user_id] = points_history[user_id][-50:]

def get_points_history(user_id: int) -> list:
    """获取积分历史记录"""
    return points_history.get(user_id, [])

# ============== 兑换系统函数 ==============

def has_exchanged(user_id: int, product_id: str) -> bool:
    """检查用户是否已兑换该商品"""
    if user_id not in user_exchanges:
        return False
    return product_id in user_exchanges[user_id]

def mark_exchanged(user_id: int, product_id: str):
    """标记用户已兑换"""
    if user_id not in user_exchanges:
        user_exchanges[user_id] = []
    if product_id not in user_exchanges[user_id]:
        user_exchanges[user_id].append(product_id)

def can_exchange(user_id: int, product_id: str) -> bool:
    """检查用户是否可以兑换"""
    if product_id not in products:
        return False
    
    product = products[product_id]
    user_pts = get_user_points(user_id)
    
    return user_pts >= product['points']

def exchange_product(user_id: int, product_id: str) -> bool:
    """兑换商品，返回是否成功"""
    if not can_exchange(user_id, product_id):
        return False
    
    product = products[product_id]
    
    # 扣除积分
    if user_id not in user_points:
        user_points[user_id] = 0
    
    user_points[user_id] -= product['points']
    
    # 标记已兑换
    mark_exchanged(user_id, product_id)
    
    # 记录消费历史
    add_points_history(user_id, 'spend', product['points'], f"兑换商品：{product['name']}")
    
    return True

# ============== 积分系统函数 ==============

def get_user_points(user_id: int) -> int:
    """获取用户积分"""
    return user_points.get(user_id, 0)

def add_points(user_id: int, points: int):
    """增加积分"""
    if user_id not in user_points:
        user_points[user_id] = 0
    user_points[user_id] += points

def can_signin(user_id: int) -> bool:
    """检查是否可以签到"""
    if user_id not in signin_records:
        return True
    
    last_signin = signin_records[user_id]
    today = datetime.now().date()
    
    return last_signin < today

def do_signin(user_id: int) -> int:
    """执行签到，返回获得的积分"""
    points = random.randint(3, 8)
    add_points(user_id, points)
    signin_records[user_id] = datetime.now().date()
    
    # 记录获得历史
    add_points_history(user_id, 'earn', points, '每日签到')
    
    return points

def has_recharged(user_id: int, pay_type: str) -> bool:
    """检查是否已充值过"""
    if user_id not in recharge_records:
        recharge_records[user_id] = {'wechat': False, 'alipay': False}
    return recharge_records[user_id].get(pay_type, False)

def mark_recharged(user_id: int, pay_type: str):
    """标记已充值"""
    if user_id not in recharge_records:
        recharge_records[user_id] = {'wechat': False, 'alipay': False}
    recharge_records[user_id][pay_type] = True

def is_recharge_locked(user_id: int, pay_type: str) -> tuple[bool, datetime]:
    """检查充值是否被锁定"""
    if user_id not in recharge_locks:
        return False, None
    
    if pay_type not in recharge_locks[user_id]:
        return False, None
    
    lock_info = recharge_locks[user_id][pay_type]
    if lock_info['locked_until'] > datetime.now():
        return True, lock_info['locked_until']
    else:
        del recharge_locks[user_id][pay_type]
        return False, None

def record_recharge_failed(user_id: int, pay_type: str):
    """记录充值失败"""
    if user_id not in recharge_locks:
        recharge_locks[user_id] = {}
    
    if pay_type not in recharge_locks[user_id]:
        recharge_locks[user_id][pay_type] = {'count': 0, 'locked_until': None}
    
    recharge_locks[user_id][pay_type]['count'] += 1
    
    if recharge_locks[user_id][pay_type]['count'] >= 2:
        recharge_locks[user_id][pay_type]['locked_until'] = datetime.now() + timedelta(hours=10)
        recharge_locks[user_id][pay_type]['count'] = 0

def get_recharge_attempts(user_id: int, pay_type: str) -> int:
    """获取充值失败次数"""
    if user_id not in recharge_locks:
        return 0
    if pay_type not in recharge_locks[user_id]:
        return 0
    return recharge_locks[user_id][pay_type].get('count', 0)

def verify_wechat_order(order_number: str) -> bool:
    """验证微信订单号"""
    return order_number.startswith('4200')

def verify_alipay_order(order_number: str) -> bool:
    """验证支付宝订单号"""
    return order_number.startswith('4768')

# ============== 余额页面 ==============

async def show_balance_page(query, context: ContextTypes.DEFAULT_TYPE):
    """显示余额页面"""
    user_id = query.from_user.id
    points = get_user_points(user_id)
    history = get_points_history(user_id)
    
    text = f"💰 *我的余额*\n\n📊 当前积分：`{points}` 分\n\n"
    
    if history:
        text += "📝 *积分记录*\n\n"
        
        # 按时间倒序显示最近10条
        recent_history = sorted(history, key=lambda x: x['time'], reverse=True)[:10]
        
        for record in recent_history:
            time_str = record['time'].strftime('%m-%d %H:%M')
            points_str = f"+{record['points']}" if record['type'] == 'earn' else f"-{record['points']}"
            
            if record['type'] == 'earn':
                emoji = "📈"
            else:
                emoji = "📉"
            
            text += f"{emoji} {time_str} | {points_str} 分 | {record['desc']}\n"
    else:
        text += "📝 *积分记录*\n\n暂无记录"
    
    keyboard = [
        [InlineKeyboardButton("🔙 返回积分页", callback_data="show_points")]
    ]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

# ============== 兑换页面 ==============

async def show_exchange_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示兑换页面"""
    user_id = update.effective_user.id
    points = get_user_points(user_id)
    
    text = (
        f"🎁 *积分兑换*\n\n"
        f"💰 当前积分：`{points}` 分\n\n"
        f"📦 请选择要兑换的商品："
    )
    
    keyboard = []
    
    # 显示所有商品
    for product_id, product in products.items():
        if has_exchanged(user_id, product_id):
            button_text = f"✅ {product['name']} (已兑换)"
        else:
            button_text = f"🎁 {product['name']} ({product['points']}积分)"
        
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"exchange_{product_id}")])
    
    keyboard.append([InlineKeyboardButton("🔙 返回积分页", callback_data="show_points")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def handle_exchange(query, context: ContextTypes.DEFAULT_TYPE, product_id: str):
    """处理兑换请求"""
    user_id = query.from_user.id
    
    if product_id not in products:
        await query.answer("❌ 商品不存在", show_alert=True)
        return
    
    product = products[product_id]
    
    # 如果已兑换，直接发送内容
    if has_exchanged(user_id, product_id):
        await send_product_content(query, context, product_id)
        return
    
    # 未兑换，显示确认页面
    points = get_user_points(user_id)
    
    text = (
        f"🎁 *确认兑换*\n\n"
        f"商品名称：{product['name']}\n"
        f"所需积分：{product['points']} 分\n"
        f"当前积分：{points} 分\n\n"
        f"确定要兑换吗？"
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ 确认兑换", callback_data=f"confirm_exchange_{product_id}")],
        [InlineKeyboardButton("❌ 取消", callback_data="show_exchange")]
    ]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def confirm_exchange(query, context: ContextTypes.DEFAULT_TYPE, product_id: str):
    """确认兑换"""
    user_id = query.from_user.id
    
    if product_id not in products:
        await query.answer("❌ 商品不存在", show_alert=True)
        return
    
    # 执行兑换
    if exchange_product(user_id, product_id):
        await query.answer("✅ 兑换成功！", show_alert=True)
        await send_product_content(query, context, product_id)
    else:
        await query.answer("❌ 积分余额不足，请重试", show_alert=True)
        
        # 创建临时 update 对象
        class TempUpdate:
            def __init__(self, query_obj):
                self.callback_query = query_obj
                self.effective_user = query_obj.from_user
                self.message = None
        
        temp_update = TempUpdate(query)
        await show_exchange_page(temp_update, context)

async def send_product_content(query, context: ContextTypes.DEFAULT_TYPE, product_id: str):
    """发送商品内容"""
    user_id = query.from_user.id
    product = products[product_id]
    content = product['content']
    
    try:
        # 根据内容类型发送
        if content['type'] == 'text':
            await query.edit_message_text(
                f"✅ *兑换成功！*\n\n{content['data']}\n\n正在返回兑换页面...",
                parse_mode='Markdown'
            )
        
        elif content['type'] == 'photo':
            await query.edit_message_text("正在发送商品内容...")
            await context.bot.send_photo(
                chat_id=user_id,
                photo=content['data'],
                caption=f"✅ *兑换成功！*\n\n商品：{product['name']}",
                parse_mode='Markdown'
            )
        
        elif content['type'] == 'video':
            await query.edit_message_text("正在发送商品内容...")
            await context.bot.send_video(
                chat_id=user_id,
                video=content['data'],
                caption=f"✅ *兑换成功！*\n\n商品：{product['name']}",
                parse_mode='Markdown'
            )
        
        elif content['type'] == 'document':
            await query.edit_message_text("正在发送商品内容...")
            await context.bot.send_document(
                chat_id=user_id,
                document=content['data'],
                caption=f"✅ *兑换成功！*\n\n商品：{product['name']}",
                parse_mode='Markdown'
            )
        
        # 2秒后返回兑换页面
        await asyncio.sleep(2)
        
        class TempUpdate:
            def __init__(self, query_obj):
                self.callback_query = query_obj
                self.effective_user = query_obj.from_user
                self.message = None
        
        temp_update = TempUpdate(query)
        await show_exchange_page(temp_update, context)
        
    except Exception as e:
        logger.error(f"发送商品内容失败: {e}")
        await query.answer("❌ 发送失败，请联系管理员", show_alert=True)

# ============== 管理员商品管理 ==============

async def show_product_management(query, context: ContextTypes.DEFAULT_TYPE):
    """显示商品管理页面"""
    text = "📦 *商品管理*\n\n已上架商品："
    
    keyboard = []
    
    # 显示所有商品（除了测试商品）
    for product_id, product in products.items():
        if product_id == 'test_product':
            continue
        keyboard.append([InlineKeyboardButton(
            f"📦 {product['name']} ({product['points']}积分)",
            callback_data=f"manage_product_{product_id}"
        )])
    
    keyboard.append([InlineKeyboardButton("➕ 添加新商品", callback_data="add_product")])
    keyboard.append([InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def handle_add_product(query, context: ContextTypes.DEFAULT_TYPE):
    """处理添加商品"""
    await query.edit_message_text(
        "📝 *添加新商品*\n\n"
        "请输入商品名称：\n\n"
        "💡 支持中文、英文\n\n"
        "发送 /cancel 取消",
        parse_mode='Markdown'
    )
    
    context.user_data['waiting_product_name'] = True
    context.user_data['in_admin_process'] = True

async def handle_product_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理商品名称输入"""
    if not context.user_data.get('waiting_product_name'):
        return
    
    user_id = update.effective_user.id
    product_name = update.message.text.strip()
    
    temp_products[user_id] = {
        'name': product_name,
        'points': 0,
        'content': {}
    }
    
    context.user_data['waiting_product_name'] = False
    context.user_data['waiting_product_points'] = True
    
    await update.message.reply_text(
        f"✅ 商品名称：{product_name}\n\n"
        f"💰 请输入所需积分：\n\n"
        f"💡 输入纯数字即可",
        parse_mode='Markdown'
    )

async def handle_product_points_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理商品积分输入"""
    if not context.user_data.get('waiting_product_points'):
        return
    
    user_id = update.effective_user.id
    
    try:
        points = int(update.message.text.strip())
        
        if points < 0:
            await update.message.reply_text("❌ 积分必须大于等于0，请重新输入：")
            return
        
        temp_products[user_id]['points'] = points
        
        context.user_data['waiting_product_points'] = False
        context.user_data['waiting_product_content'] = True
        
        await update.message.reply_text(
            f"✅ 所需积分：{points} 分\n\n"
            f"📤 *请发送商品内容*\n\n"
            f"支持的类型：\n"
            f"• 📝 文本消息\n"
            f"• 🖼 图片\n"
            f"• 🎬 视频\n"
            f"• 📄 文档\n\n"
            f"💡 发送内容后自动上架",
            parse_mode='Markdown'
        )
        
    except ValueError:
        await update.message.reply_text("❌ 请输入有效的数字：")

async def handle_product_content_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理商品内容输入"""
    if not context.user_data.get('waiting_product_content'):
        return
    
    user_id = update.effective_user.id
    message = update.message
    
    if user_id not in temp_products:
        await message.reply_text("❌ 会话已过期，请重新开始")
        context.user_data.clear()
        return
    
    # 获取内容
    content = {}
    
    if message.text:
        content = {
            'type': 'text',
            'data': message.text
        }
    elif message.photo:
        content = {
            'type': 'photo',
            'data': message.photo[-1].file_id
        }
    elif message.video:
        content = {
            'type': 'video',
            'data': message.video.file_id
        }
    elif message.document:
        content = {
            'type': 'document',
            'data': message.document.file_id
        }
    else:
        await message.reply_text("❌ 不支持的内容类型，请重新发送")
        return
    
    # 保存商品
    temp_products[user_id]['content'] = content
    
    # 生成商品ID
    product_id = f"product_{len(products)}_{int(datetime.now().timestamp())}"
    
    products[product_id] = temp_products[user_id]
    
    # 清除临时数据
    del temp_products[user_id]
    context.user_data.clear()
    
    await message.reply_text(
        f"✅ *商品上架成功！*\n\n"
        f"商品名称：{products[product_id]['name']}\n"
        f"所需积分：{products[product_id]['points']} 分\n\n"
        f"正在返回商品管理页面...",
        parse_mode='Markdown'
    )
    
    await asyncio.sleep(1)
    
    # 返回商品管理页面（需要构造一个临时的 query 对象）
    # 这里直接发送新消息
    keyboard = [
        [InlineKeyboardButton("📦 商品管理", callback_data="product_management")],
        [InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")]
    ]
    
    await message.reply_text(
        "✅ 上架完成",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def manage_product(query, context: ContextTypes.DEFAULT_TYPE, product_id: str):
    """管理单个商品"""
    if product_id not in products:
        await query.answer("❌ 商品不存在", show_alert=True)
        return
    
    product = products[product_id]
    
    text = (
        f"📦 *商品详情*\n\n"
        f"商品名称：{product['name']}\n"
        f"所需积分：{product['points']} 分\n"
        f"内容类型：{product['content']['type']}\n\n"
        f"确定要下架此商品吗？"
    )
    
    keyboard = [
        [InlineKeyboardButton("🗑 确认下架", callback_data=f"remove_product_{product_id}")],
        [InlineKeyboardButton("🔙 返回列表", callback_data="product_management")]
    ]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def remove_product(query, context: ContextTypes.DEFAULT_TYPE, product_id: str):
    """删除商品"""
    if product_id in products:
        del products[product_id]
        await query.answer("✅ 商品已下架", show_alert=True)
    else:
        await query.answer("❌ 商品不存在", show_alert=True)
    
    await show_product_management(query, context)

# ============== 积分页面 ==============

async def show_points_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示积分页面"""
    user_id = update.effective_user.id
    points = get_user_points(user_id)
    can_sign = can_signin(user_id)
    
    text = (
        f"💰 *积分中心*\n\n"
        f"👤 当前积分：`{points}` 分\n\n"
        f"📌 签到状态：{'✅ 可签到' if can_sign else '❌ 今日已签到'}\n"
        f"💡 每日签到可随机获得 3-8 积分"
    )
    
    keyboard = [
        [InlineKeyboardButton("✍️ 每日签到", callback_data="daily_signin")],
        [InlineKeyboardButton("💳 积分充值", callback_data="recharge_menu")],
        [InlineKeyboardButton("🎁 积分兑换", callback_data="show_exchange")],
        [InlineKeyboardButton("💼 我的余额", callback_data="show_balance")],
        [InlineKeyboardButton("🏠 返回首页", callback_data="back_home")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def handle_signin(query, context: ContextTypes.DEFAULT_TYPE):
    """处理签到"""
    user_id = query.from_user.id
    
    if not can_signin(user_id):
        await query.answer("❌ 今日已签到，明天再来吧！", show_alert=True)
        return
    
    points = do_signin(user_id)
    total_points = get_user_points(user_id)
    
    await query.answer(f"✅ 签到成功！获得 {points} 积分", show_alert=True)
    
    text = (
        f"🎉 *签到成功！*\n\n"
        f"🎁 本次获得：`{points}` 积分\n"
        f"💰 当前积分：`{total_points}` 分\n\n"
        f"📅 明天继续签到可获得更多积分哦~"
    )
    
    keyboard = [
        [InlineKeyboardButton("💳 积分充值", callback_data="recharge_menu")],
        [InlineKeyboardButton("🔙 返回积分页", callback_data="show_points")]
    ]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def show_recharge_menu(query, context: ContextTypes.DEFAULT_TYPE):
    """显示充值菜单"""
    user_id = query.from_user.id
    
    wechat_recharged = has_recharged(user_id, 'wechat')
    wechat_locked, wechat_unlock_time = is_recharge_locked(user_id, 'wechat')
    
    alipay_recharged = has_recharged(user_id, 'alipay')
    alipay_locked, alipay_unlock_time = is_recharge_locked(user_id, 'alipay')
    
    text = (
        "💳 *充值中心*\n\n"
        "💎 充值套餐：5元 = 100积分\n\n"
        "📢 请选择支付方式："
    )
    
    keyboard = []
    
    if wechat_recharged:
        wechat_text = "💚 微信支付（已使用）"
        wechat_callback = "recharge_used"
    elif wechat_locked:
        time_left = wechat_unlock_time - datetime.now()
        hours = int(time_left.total_seconds() // 3600)
        wechat_text = f"💚 微信支付（{hours}小时后重试）"
        wechat_callback = "recharge_locked_wechat"
    else:
        wechat_text = "💚 微信支付"
        wechat_callback = "recharge_wechat"
    
    keyboard.append([InlineKeyboardButton(wechat_text, callback_data=wechat_callback)])
    
    if alipay_recharged:
        alipay_text = "💙 支付宝支付（已使用）"
        alipay_callback = "recharge_used"
    elif alipay_locked:
        time_left = alipay_unlock_time - datetime.now()
        hours = int(time_left.total_seconds() // 3600)
        alipay_text = f"💙 支付宝支付（{hours}小时后重试）"
        alipay_callback = "recharge_locked_alipay"
    else:
        alipay_text = "💙 支付宝支付"
        alipay_callback = "recharge_alipay"
    
    keyboard.append([InlineKeyboardButton(alipay_text, callback_data=alipay_callback)])
    keyboard.append([InlineKeyboardButton("🔙 返回积分页", callback_data="show_points")])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def handle_recharge_wechat(query, context: ContextTypes.DEFAULT_TYPE):
    """处理微信充值"""
    user_id = query.from_user.id
    
    if has_recharged(user_id, 'wechat'):
        await query.answer("⚠️ 微信支付已使用过，每人仅限一次", show_alert=True)
        return
    
    locked, unlock_time = is_recharge_locked(user_id, 'wechat')
    if locked:
        time_left = unlock_time - datetime.now()
        hours = int(time_left.total_seconds() // 3600)
        await query.answer(f"⏰ 请等待 {hours} 小时后重试", show_alert=True)
        return
    
    text = (
        "💚 *微信支付充值*\n\n"
        "💎 充值金额：5 元\n"
        "🎁 获得积分：100 积分\n\n"
        "⚠️ *温馨提示*\n"
        "• 每个支付方式仅限使用一次\n"
        "• 请勿重复充值同一支付方式\n"
        "• 充值成功后积分立即到账\n"
        "• 如有问题请联系客服"
    )
    
    keyboard = [[InlineKeyboardButton("✅ 我已支付，开始验证", callback_data="wechat_paid")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if file_id_storage.get('WECHAT_PAY_IMAGE'):
        await query.edit_message_text("正在加载付款信息...")
        await context.bot.send_photo(
            chat_id=user_id,
            photo=file_id_storage['WECHAT_PAY_IMAGE'],
            caption=text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        await query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def handle_wechat_paid(query, context: ContextTypes.DEFAULT_TYPE):
    """处理微信已支付"""
    user_id = query.from_user.id
    
    text = (
        "📋 *如何查找交易单号*\n\n"
        "请按照以下步骤操作：\n\n"
        "1️⃣ 打开微信，进入「我」→「服务」→「钱包」\n"
        "2️⃣ 点击右上角「账单」\n"
        "3️⃣ 找到本次支付记录并点击\n"
        "4️⃣ 在交易详情中找到「交易单号」\n"
        "5️⃣ 长按复制交易单号\n\n"
        "💡 交易单号是一串数字\n\n"
        "请在下方输入你的交易单号："
    )
    
    if file_id_storage.get('WECHAT_TUTORIAL_IMAGE'):
        await query.edit_message_text("正在加载教程...")
        await context.bot.send_photo(
            chat_id=user_id,
            photo=file_id_storage['WECHAT_TUTORIAL_IMAGE'],
            caption=text,
            parse_mode='Markdown'
        )
    else:
        await query.edit_message_text(text, parse_mode='Markdown')
    
    waiting_recharge_order[user_id] = {
        'type': 'wechat',
        'attempt': get_recharge_attempts(user_id, 'wechat')
    }
    context.user_data['waiting_recharge_order'] = True
    context.user_data['in_verification'] = True

async def handle_recharge_alipay(query, context: ContextTypes.DEFAULT_TYPE):
    """处理支付宝充值"""
    user_id = query.from_user.id
    
    if has_recharged(user_id, 'alipay'):
        await query.answer("⚠️ 支付宝支付已使用过，每人仅限一次", show_alert=True)
        return
    
    locked, unlock_time = is_recharge_locked(user_id, 'alipay')
    if locked:
        time_left = unlock_time - datetime.now()
        hours = int(time_left.total_seconds() // 3600)
        await query.answer(f"⏰ 请等待 {hours} 小时后重试", show_alert=True)
        return
    
    text = (
        "💙 *支付宝支付充值*\n\n"
        "💎 充值金额：5 元\n"
        "🎁 获得积分：100 积分\n\n"
        "⚠️ *温馨提示*\n"
        "• 每个支付方式仅限使用一次\n"
        "• 请勿重复充值同一支付方式\n"
        "• 充值成功后积分立即到账\n"
        "• 如有问题请联系客服"
    )
    
    keyboard = [[InlineKeyboardButton("✅ 我已支付，开始验证", callback_data="alipay_paid")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if file_id_storage.get('ALIPAY_PAY_IMAGE'):
        await query.edit_message_text("正在加载付款信息...")
        await context.bot.send_photo(
            chat_id=user_id,
            photo=file_id_storage['ALIPAY_PAY_IMAGE'],
            caption=text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        await query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def handle_alipay_paid(query, context: ContextTypes.DEFAULT_TYPE):
    """处理支付宝已支付"""
    user_id = query.from_user.id
    
    text = (
        "📋 *如何查找商家订单号*\n\n"
        "请按照以下步骤操作：\n\n"
        "1️⃣ 打开支付宝，进入「我的」\n"
        "2️⃣ 点击「账单」\n"
        "3️⃣ 找到本次支付记录并点击\n"
        "4️⃣ 点击「账单详情」\n"
        "5️⃣ 点击「更多」展开详细信息\n"
        "6️⃣ 找到「商家订单号」并复制\n\n"
        "💡 商家订单号是一串数字\n\n"
        "请在下方输入你的商家订单号："
    )
    
    if file_id_storage.get('ALIPAY_TUTORIAL_IMAGE'):
        await query.edit_message_text("正在加载教程...")
        await context.bot.send_photo(
            chat_id=user_id,
            photo=file_id_storage['ALIPAY_TUTORIAL_IMAGE'],
            caption=text,
            parse_mode='Markdown'
        )
    else:
        await query.edit_message_text(text, parse_mode='Markdown')
    
    waiting_recharge_order[user_id] = {
        'type': 'alipay',
        'attempt': get_recharge_attempts(user_id, 'alipay')
    }
    context.user_data['waiting_recharge_order'] = True
    context.user_data['in_verification'] = True

async def handle_recharge_order_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理充值订单号输入"""
    if not context.user_data.get('waiting_recharge_order'):
        return False
    
    user_id = update.effective_user.id
    order_number = update.message.text.strip()
    
    if user_id not in waiting_recharge_order:
        return False
    
    pay_type = waiting_recharge_order[user_id]['type']
    
    is_valid = False
    if pay_type == 'wechat':
        is_valid = verify_wechat_order(order_number)
    elif pay_type == 'alipay':
        is_valid = verify_alipay_order(order_number)
    
    if is_valid:
        add_points(user_id, 100)
        mark_recharged(user_id, pay_type)
        total_points = get_user_points(user_id)
        
        # 记录充值历史
        pay_name = "微信支付" if pay_type == 'wechat' else "支付宝支付"
        add_points_history(user_id, 'earn', 100, f'{pay_name}充值')
        
        del waiting_recharge_order[user_id]
        context.user_data.clear()
        
        success_text = (
            f"✅ *充值成功！*\n\n"
            f"💳 支付方式：{pay_name}\n"
            f"🎁 到账积分：100 积分\n"
            f"💰 当前积分：{total_points} 积分\n\n"
            f"🎉 感谢您的支持！\n\n"
            f"正在返回积分页面..."
        )
        
        await update.message.reply_text(success_text, parse_mode='Markdown')
        await asyncio.sleep(2)
        
        class TempUpdate:
            def __init__(self, message):
                self.message = message
                self.callback_query = None
                self.effective_user = message.from_user
        
        temp_update = TempUpdate(update.message)
        await show_points_page(temp_update, context)
        
    else:
        record_recharge_failed(user_id, pay_type)
        current_attempt = get_recharge_attempts(user_id, pay_type)
        attempts_left = 2 - current_attempt
        
        if attempts_left > 0:
            fail_text = (
                f"❌ *订单号识别失败*\n\n"
                f"⚠️ 剩余尝试次数：{attempts_left} 次\n\n"
                f"请检查订单号是否正确，然后重新输入："
            )
            
            waiting_recharge_order[user_id]['attempt'] = current_attempt
            await update.message.reply_text(fail_text, parse_mode='Markdown')
        else:
            locked, unlock_time = is_recharge_locked(user_id, pay_type)
            
            if locked:
                time_left = unlock_time - datetime.now()
                hours = int(time_left.total_seconds() // 3600)
                
                pay_name = "微信支付" if pay_type == 'wechat' else "支付宝支付"
                
                lock_text = (
                    f"❌ *订单号识别失败*\n\n"
                    f"⚠️ 验证失败次数过多\n"
                    f"⏰ 请在 {hours} 小时后重试\n\n"
                    f"正在返回积分页面..."
                )
                
                await update.message.reply_text(lock_text, parse_mode='Markdown')
                
                del waiting_recharge_order[user_id]
                context.user_data.clear()
                
                await asyncio.sleep(2)
                
                class TempUpdate:
                    def __init__(self, message):
                        self.message = message
                        self.callback_query = None
                        self.effective_user = message.from_user
                
                temp_update = TempUpdate(update.message)
                await show_points_page(temp_update, context)
    
    return True

# ============== 工具函数 ==============

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def is_user_locked(user_id: int) -> tuple[bool, datetime]:
    if user_id in user_locks:
        lock_info = user_locks[user_id]
        if lock_info['locked_until'] > datetime.now():
            return True, lock_info['locked_until']
        else:
            del user_locks[user_id]
    return False, None

def record_failed_attempt(user_id: int):
    if user_id not in user_locks:
        user_locks[user_id] = {'count': 0, 'locked_until': None}
    
    user_locks[user_id]['count'] += 1
    
    if user_locks[user_id]['count'] >= 2:
        user_locks[user_id]['locked_until'] = datetime.now() + timedelta(hours=5)
        user_locks[user_id]['count'] = 0

def clear_user_attempts(user_id: int):
    if user_id in user_locks:
        del user_locks[user_id]

def verify_order_number(order_number: str) -> bool:
    return order_number.startswith('20260')

def extract_channel_id(text: str) -> int:
    patterns = [
        r't\.me/([a-zA-Z0-9_]+)',
        r'@([a-zA-Z0-9_]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            username = match.group(1)
            return f"@{username}"
    
    return None

async def schedule_message_deletion(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_ids: list, delay_minutes: int = 20):
    await asyncio.sleep(delay_minutes * 60)
    
    deleted_count = 0
    for msg_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            deleted_count += 1
        except Exception as e:
            logger.warning(f"删除消息失败 {chat_id}:{msg_id} - {e}")
    
    if deleted_count > 0:
        try:
            reminder_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "⏰ *消息已过期删除*\n\n"
                    "💡 消息存在时间有限，如需重新查看请返回购买处重新获取\n\n"
                    "✅ 已购买用户无需二次付费，可直接再次获取查看\n\n"
                    "正在返回首页..."
                ),
                parse_mode='Markdown'
            )
            
            await asyncio.sleep(3)
            await context.bot.delete_message(chat_id=chat_id, message_id=reminder_msg.message_id)
            await send_home_page(context.bot, chat_id)
            
        except Exception as e:
            logger.error(f"发送删除提示失败: {e}")

async def send_home_page(bot, chat_id: int):
    user_id = chat_id
    welcome_text = (
        "👋 欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~\n\n"
        "📢 小卫小卫，守门员小卫！\n"
        "一键入群，小卫帮你搞定！\n"
        "新人来报到，小卫查身份！"
    )
    
    reply_markup = get_home_keyboard(user_id)
    
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=welcome_text,
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"发送首页失败: {e}")

def get_home_keyboard(user_id: int):
    locked, unlock_time = is_user_locked(user_id)
    
    keyboard = []
    
    if locked:
        time_left = unlock_time - datetime.now()
        hours = int(time_left.total_seconds() // 3600)
        minutes = int((time_left.total_seconds() % 3600) // 60)
        button_text = f"🔒 验证已锁定 ({hours}小时{minutes}分钟后解锁)"
        callback_data = "locked"
    else:
        button_text = "✨ 开始验证"
        callback_data = "start_verify"
    
    keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    keyboard.append([InlineKeyboardButton("💰 积分中心", callback_data="show_points")])
    
    return InlineKeyboardMarkup(keyboard)

# ============== 首页功能 ==============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    context.user_data.clear()
    
    welcome_text = (
        "👋 欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~\n\n"
        "📢 小卫小卫，守门员小卫！\n"
        "一键入群，小卫帮你搞定！\n"
        "新人来报到，小卫查身份！"
    )
    
    reply_markup = get_home_keyboard(user_id)
    
    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.edit_message_text(welcome_text, reply_markup=reply_markup)

async def handle_normal_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('in_verification') or context.user_data.get('in_admin_process'):
        return
    
    user_id = update.effective_user.id
    message_text = update.message.text
    
    if message_text in forward_library:
        await handle_forward_command(update, context, message_text)
        return
    
    welcome_text = (
        "👋 欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~\n\n"
        "📢 小卫小卫，守门员小卫！\n"
        "一键入群，小卫帮你搞定！\n"
        "新人来报到，小卫查身份！"
    )
    
    reply_markup = get_home_keyboard(user_id)
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)

# ============== 验证流程 ==============

async def handle_order_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting_order'):
        await handle_normal_message(update, context)
        return
    
    user_id = update.effective_user.id
    order_number = update.message.text.strip()
    
    if verify_order_number(order_number):
        clear_user_attempts(user_id)
        context.user_data.clear()
        
        keyboard = [
            [InlineKeyboardButton("🎉 点击加入VIP群组", url=GROUP_INVITE_LINK)],
            [InlineKeyboardButton("🏠 返回首页", callback_data="back_home")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        success_text = (
            "✅ *验证成功！*\n\n"
            f"订单号：`{order_number}`\n\n"
            "🎊 恭喜你成为VIP会员！\n"
            "点击下方按钮即可加入专属群组~"
        )
        
        await update.message.reply_text(
            success_text,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
        
        await asyncio.sleep(3)
        await start(update, context)
        
    else:
        record_failed_attempt(user_id)
        current_count = user_locks.get(user_id, {}).get('count', 0)
        attempts_left = 2 - current_count
        
        if attempts_left > 0:
            fail_text = (
                "❌ *未查询到订单信息*\n\n"
                f"剩余尝试次数：{attempts_left}\n\n"
                "请检查订单号是否正确，然后重新输入："
            )
            await update.message.reply_text(fail_text, parse_mode='Markdown')
        else:
            locked, unlock_time = is_user_locked(user_id)
            time_left = unlock_time - datetime.now()
            hours = int(time_left.total_seconds() // 3600)
            minutes = int((time_left.total_seconds() % 3600) // 60)
            
            lock_text = (
                "❌ *验证失败次数过多*\n\n"
                f"⏰ 请在 {hours}小时{minutes}分钟 后重试\n\n"
                "正在返回首页..."
            )
            
            await update.message.reply_text(lock_text, parse_mode='Markdown')
            context.user_data.clear()
            await asyncio.sleep(1)
            await start(update, context)

# ============== 管理员后台 ==============

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("⛔ 抱歉，你没有权限访问管理后台。")
        return
    
    await admin_menu(update.message, context)

async def admin_menu(message_or_query, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🔍 获取文件 ID", callback_data="get_file_id")],
        [InlineKeyboardButton("📚 频道转发库", callback_data="forward_library")],
        [InlineKeyboardButton("📦 商品管理", callback_data="product_management")],
        [InlineKeyboardButton("❌ 关闭", callback_data="close_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "🔧 *管理员后台*\n\n请选择功能："
    
    if hasattr(message_or_query, 'edit_message_text'):
        await message_or_query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        await message_or_query.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

# ============== File ID 功能 ==============

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("⛔ 抱歉，你没有权限使用此命令。")
        return
    
    await update.message.reply_text(
        "📤 请发送文件\n\n"
        "支持的类型：图片、视频、文档、音频、贴纸等\n\n"
        "发送 /cancel 取消操作"
    )
    
    context.user_data['admin_getting_file'] = True
    context.user_data['in_admin_process'] = True

async def handle_admin_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    if not context.user_data.get('admin_getting_file'):
        return
    
    message = update.message
    
    if message.photo:
        file = message.photo[-1]
        file_type = "图片 (Photo)"
        file_id = file.file_id
        file_unique_id = file.file_unique_id
        file_size = file.file_size
    elif message.video:
        file = message.video
        file_type = "视频 (Video)"
        file_id = file.file_id
        file_unique_id = file.file_unique_id
        file_size = file.file_size
    elif message.document:
        file = message.document
        file_type = f"文档 (Document)\n📄 文件名: {file.file_name}"
        file_id = file.file_id
        file_unique_id = file.file_unique_id
        file_size = file.file_size
    elif message.audio:
        file = message.audio
        file_type = "音频 (Audio)"
        file_id = file.file_id
        file_unique_id = file.file_unique_id
        file_size = file.file_size
    elif message.voice:
        file = message.voice
        file_type = "语音 (Voice)"
        file_id = file.file_id
        file_unique_id = file.file_unique_id
        file_size = file.file_size
    elif message.sticker:
        file = message.sticker
        file_type = "贴纸 (Sticker)"
        file_id = file.file_id
        file_unique_id = file.file_unique_id
        file_size = file.file_size
    elif message.animation:
        file = message.animation
        file_type = "动画 (Animation/GIF)"
        file_id = file.file_id
        file_unique_id = file.file_unique_id
        file_size = file.file_size
    else:
        await message.reply_text("❌ 未识别的文件类型\n\n请发送图片、视频、文档等文件")
        return
    
    size_mb = file_size / (1024 * 1024) if file_size else 0
    
    response = (
        f"✅ *文件信息获取成功*\n\n"
        f"📋 类型: {file_type}\n"
        f"💾 大小: {size_mb:.2f} MB\n\n"
        f"🆔 *File ID:*\n`{file_id}`\n\n"
        f"🔑 *Unique ID:*\n`{file_unique_id}`\n\n"
        f"💡 点击 ID 即可复制"
    )
    
    keyboard = [
        [InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")],
        [InlineKeyboardButton("📤 继续获取", callback_data="get_file_id")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await message.reply_text(response, parse_mode='Markdown', reply_markup=reply_markup)
    context.user_data['admin_getting_file'] = False
    context.user_data['in_admin_process'] = False

# ============== 频道转发库功能 ==============

async def show_forward_library(query, context: ContextTypes.DEFAULT_TYPE):
    if not forward_library:
        text = "📚 *频道转发库*\n\n暂无命令，点击下方添加新命令："
    else:
        text = "📚 *频道转发库*\n\n已创建的命令："
    
    keyboard = []
    
    for cmd in forward_library.keys():
        msg_count = len(forward_library[cmd]['message_ids'])
        keyboard.append([InlineKeyboardButton(
            f"🗂 {cmd} ({msg_count}条消息)", 
            callback_data=f"view_cmd_{cmd}"
        )])
    
    keyboard.append([InlineKeyboardButton("➕ 添加新命令", callback_data="add_new_command")])
    keyboard.append([InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def handle_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    if query.data == "add_new_command":
        await query.edit_message_text(
            "✏️ *创建新命令*\n\n"
            "请输入自定义命令名称：\n\n"
            "💡 支持中文、英文、大小写\n"
            "💡 用户将通过此命令获取内容\n\n"
            "发送 /cancel 取消",
            parse_mode='Markdown'
        )
        context.user_data['waiting_command_name'] = True
        context.user_data['in_admin_process'] = True

async def handle_command_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('waiting_command_name'):
        return
    
    user_id = update.effective_user.id
    command_name = update.message.text.strip()
    
    if command_name in forward_library:
        await update.message.reply_text(
            f"❌ 命令 `{command_name}` 已存在！\n\n请输入其他命令名称：",
            parse_mode='Markdown'
        )
        return
    
    temp_commands[user_id] = {
        'command': command_name,
        'chat_id': None,
        'message_ids': []
    }
    
    context.user_data['waiting_command_name'] = False
    context.user_data['waiting_content'] = True
    
    await update.message.reply_text(
        f"✅ 命令名称：`{command_name}`\n\n"
        f"📤 *请添加内容*\n\n"
        f"支持的类型：\n"
        f"• 📝 文本消息\n"
        f"• 🖼 图片/视频\n"
        f"• 🔗 频道链接 (t.me/...)\n"
        f"• ↗️ 转发消息\n\n"
        f"💡 最多可添加 100 条消息\n"
        f"💡 添加完成后点击下方按钮\n\n"
        f"当前已添加：0 条",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 完成绑定", callback_data="finish_binding")],
            [InlineKeyboardButton("❌ 取消", callback_data="cancel_binding")]
        ])
    )

async def handle_content_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('waiting_content'):
        return
    
    user_id = update.effective_user.id
    
    if user_id not in temp_commands:
        await update.message.reply_text("❌ 会话已过期，请重新开始")
        context.user_data.clear()
        return
    
    message = update.message
    temp_cmd = temp_commands[user_id]
    
    if len(temp_cmd['message_ids']) >= 100:
        await update.message.reply_text(
            "⚠️ 已达到最大限制（100条消息）\n\n请点击「完成绑定」保存"
        )
        return
    
    if message.text and ('t.me/' in message.text or '@' in message.text):
        channel_id = extract_channel_id(message.text)
        if channel_id:
            temp_cmd['chat_id'] = channel_id
            temp_cmd['message_ids'].append(message.message_id)
        else:
            temp_cmd['message_ids'].append(message.message_id)
    elif message.forward_from_chat:
        chat_id = message.forward_from_chat.id
        temp_cmd['chat_id'] = chat_id
        temp_cmd['message_ids'].append(message.message_id)
    else:
        temp_cmd['message_ids'].append(message.message_id)
    
    count = len(temp_cmd['message_ids'])
    await update.message.reply_text(
        f"✅ 已添加第 {count} 条内容\n\n"
        f"继续添加或点击「完成绑定」",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 完成绑定", callback_data="finish_binding")],
            [InlineKeyboardButton("❌ 取消", callback_data="cancel_binding")]
        ])
    )

async def finish_binding(query, context: ContextTypes.DEFAULT_TYPE):
    user_id = query.from_user.id
    
    if user_id not in temp_commands:
        await query.answer("❌ 会话已过期", show_alert=True)
        return
    
    temp_cmd = temp_commands[user_id]
    
    if not temp_cmd['message_ids']:
        await query.answer("❌ 请至少添加一条内容", show_alert=True)
        return
    
    forward_library[temp_cmd['command']] = {
        'chat_id': temp_cmd['chat_id'],
        'message_ids': temp_cmd['message_ids'],
        'created_by': user_id
    }
    
    del temp_commands[user_id]
    context.user_data.clear()
    
    await query.answer("✅ 绑定成功！", show_alert=True)
    await show_forward_library(query, context)

async def view_command_detail(query, context: ContextTypes.DEFAULT_TYPE, command_name: str):
    if command_name not in forward_library:
        await query.answer("❌ 命令不存在", show_alert=True)
        return
    
    cmd_data = forward_library[command_name]
    msg_count = len(cmd_data['message_ids'])
    
    text = (
        f"🗂 *命令详情*\n\n"
        f"命令名称：`{command_name}`\n"
        f"消息数量：{msg_count} 条\n"
        f"频道ID：`{cmd_data['chat_id']}`\n\n"
        f"用户发送 `{command_name}` 即可获取内容"
    )
    
    keyboard = [
        [InlineKeyboardButton("🗑 删除此命令", callback_data=f"confirm_delete_{command_name}")],
        [InlineKeyboardButton("🔙 返回列表", callback_data="forward_library")]
    ]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def confirm_delete_command(query, context: ContextTypes.DEFAULT_TYPE, command_name: str):
    text = (
        f"⚠️ *确认删除*\n\n"
        f"确定要删除命令 `{command_name}` 吗？\n\n"
        f"此操作不可撤销！"
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ 确认删除", callback_data=f"delete_{command_name}")],
        [InlineKeyboardButton("❌ 取消", callback_data=f"view_cmd_{command_name}")]
    ]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def delete_command(query, context: ContextTypes.DEFAULT_TYPE, command_name: str):
    if command_name in forward_library:
        del forward_library[command_name]
        await query.answer("✅ 删除成功", show_alert=True)
    else:
        await query.answer("❌ 命令不存在", show_alert=True)
    
    await show_forward_library(query, context)

# ============== 用户使用转发库命令 ==============

async def handle_forward_command(update: Update, context: ContextTypes.DEFAULT_TYPE, command: str):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if command not in forward_library:
        return
    
    cmd_data = forward_library[command]
    message_ids_to_delete = [update.message.message_id]
    
    try:
        for msg_id in cmd_data['message_ids']:
            try:
                if cmd_data['chat_id']:
                    sent_msg = await context.bot.copy_message(
                        chat_id=chat_id,
                        from_chat_id=cmd_data['chat_id'],
                        message_id=msg_id
                    )
                else:
                    sent_msg = await context.bot.copy_message(
                        chat_id=chat_id,
                        from_chat_id=ADMIN_ID,
                        message_id=msg_id
                    )
                
                message_ids_to_delete.append(sent_msg.message_id)
                
            except TelegramError as e:
                logger.warning(f"复制消息失败 {msg_id}: {e}")
                continue
        
        complete_msg = await update.message.reply_text(
            "✅ *信息已全部发送*\n\n"
            "💡 消息将在 20 分钟后自动删除\n"
            "正在返回首页...",
            parse_mode='Markdown'
        )
        message_ids_to_delete.append(complete_msg.message_id)
        
        asyncio.create_task(
            schedule_message_deletion(context, chat_id, message_ids_to_delete, delay_minutes=20)
        )
        
        await asyncio.sleep(3)
        await send_home_page(context.bot, chat_id)
        
    except Exception as e:
        logger.error(f"转发消息失败: {e}")
        await update.message.reply_text("❌ 发送失败，请稍后重试")

# ============== 回调处理 ==============

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    # 积分系统回调
    if data == "show_points":
        await show_points_page(update, context)
        return
    
    if data == "daily_signin":
        await handle_signin(query, context)
        return
    
    if data == "recharge_menu":
        await show_recharge_menu(query, context)
        return
    
    if data == "show_exchange":
        await show_exchange_page(update, context)
        return
    
    if data == "show_balance":
        await show_balance_page(query, context)
        return
    
    if data.startswith("exchange_"):
        product_id = data.replace("exchange_", "")
        await handle_exchange(query, context, product_id)
        return
    
    if data.startswith("confirm_exchange_"):
        product_id = data.replace("confirm_exchange_", "")
        await confirm_exchange(query, context, product_id)
        return
    
    if data == "recharge_wechat":
        await handle_recharge_wechat(query, context)
        return
    
    if data == "recharge_alipay":
        await handle_recharge_alipay(query, context)
        return
    
    if data == "wechat_paid":
        await handle_wechat_paid(query, context)
        return
    
    if data == "alipay_paid":
        await handle_alipay_paid(query, context)
        return
    
    if data == "recharge_used":
        await query.answer("⚠️ 此支付方式已使用过，每人仅限一次", show_alert=True)
        return
    
    if data == "recharge_locked_wechat":
        locked, unlock_time = is_recharge_locked(user_id, 'wechat')
        if locked:
            time_left = unlock_time - datetime.now()
            hours = int(time_left.total_seconds() // 3600)
            await query.answer(f"⏰ 请等待 {hours} 小时后重试", show_alert=True)
        return
    
    if data == "recharge_locked_alipay":
        locked, unlock_time = is_recharge_locked(user_id, 'alipay')
        if locked:
            time_left = unlock_time - datetime.now()
            hours = int(time_left.total_seconds() // 3600)
            await query.answer(f"⏰ 请等待 {hours} 小时后重试", show_alert=True)
        return
    
    # 锁定状态
    if data == "locked":
        locked, unlock_time = is_user_locked(user_id)
        if locked:
            time_left = unlock_time - datetime.now()
            hours = int(time_left.total_seconds() // 3600)
            minutes = int((time_left.total_seconds() % 3600) // 60)
            await query.answer(f"⏰ 请等待 {hours}小时{minutes}分钟后重试", show_alert=True)
        return
    
    # 开始验证
    if data == "start_verify":
        context.user_data['in_verification'] = True
        
        vip_text = (
            "💎 *VIP会员特权说明：*\n\n"
            "✅ 专属中转通道\n"
            "✅ 优先审核入群\n"
            "✅ 7x24小时客服支持\n"
            "✅ 定期福利活动"
        )
        
        await query.edit_message_text(vip_text, parse_mode='Markdown')
        
        keyboard = [[InlineKeyboardButton("✅ 我已付款，开始验证", callback_data="paid_verify")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if file_id_storage.get('PAYMENT_IMAGE'):
            await context.bot.send_photo(
                chat_id=user_id,
                photo=file_id_storage['PAYMENT_IMAGE'],
                caption="💳 请按照上图完成付款",
                reply_markup=reply_markup
            )
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text="💳 请完成付款后继续",
                reply_markup=reply_markup
            )
        return
    
    # 已付款验证
    if data == "paid_verify":
        tutorial_text = (
            "📋 *如何查找订单号*\n\n"
            "请按照以下步骤操作：\n\n"
            "1️⃣ 打开支付应用\n"
            "2️⃣ 进入「我的」→「账单」\n"
            "3️⃣ 找到本次付款记录\n"
            "4️⃣ 点击「账单详情」\n"
            "5️⃣ 点击「更多」展开\n"
            "6️⃣ 复制「商户订单号」\n\n"
            "💡 订单号格式：一串数字\n\n"
            "请在下方输入你的订单号："
        )
        
        if file_id_storage.get('TUTORIAL_IMAGE'):
            await query.edit_message_text("正在加载教程...")
            await context.bot.send_photo(
                chat_id=user_id,
                photo=file_id_storage['TUTORIAL_IMAGE'],
                caption=tutorial_text,
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(tutorial_text, parse_mode='Markdown')
        
        context.user_data['awaiting_order'] = True
        return
    
    # 返回首页
    if data == "back_home":
        context.user_data.clear()
        await start(update, context)
        return
    
    # 管理员功能
    if not is_admin(user_id):
        await query.answer("⛔ 权限不足", show_alert=True)
        return
    
    # 获取文件ID
    if data == "get_file_id":
        await query.edit_message_text(
            "📤 请发送文件\n\n"
            "支持的类型：图片、视频、文档、音频、贴纸等\n\n"
            "发送 /cancel 返回后台"
        )
        context.user_data['admin_getting_file'] = True
        context.user_data['in_admin_process'] = True
        return
    
    # 转发库
    if data == "forward_library":
        await show_forward_library(query, context)
        return
    
    # 商品管理
    if data == "product_management":
        await show_product_management(query, context)
        return
    
    if data == "add_product":
        await handle_add_product(query, context)
        return
    
    if data.startswith("manage_product_"):
        product_id = data.replace("manage_product_", "")
        await manage_product(query, context, product_id)
        return
    
    if data.startswith("remove_product_"):
        product_id = data.replace("remove_product_", "")
        await remove_product(query, context, product_id)
        return
    
    # 添加新命令
    if data == "add_new_command":
        await handle_add_command(update, context)
        return
    
    # 完成绑定
    if data == "finish_binding":
        await finish_binding(query, context)
        return
    
    # 取消绑定
    if data == "cancel_binding":
        if user_id in temp_commands:
            del temp_commands[user_id]
        context.user_data.clear()
        await query.answer("✅ 已取消", show_alert=True)
        await show_forward_library(query, context)
        return
    
    # 查看命令
    if data.startswith("view_cmd_"):
        command_name = data.replace("view_cmd_", "")
        await view_command_detail(query, context, command_name)
        return
    
    # 确认删除
    if data.startswith("confirm_delete_"):
        command_name = data.replace("confirm_delete_", "")
        await confirm_delete_command(query, context, command_name)
        return
    
    # 删除命令
    if data.startswith("delete_"):
        command_name = data.replace("delete_", "")
        await delete_command(query, context, command_name)
        return
    
    # 返回后台
    if data == "back_to_admin":
        context.user_data.clear()
        await admin_menu(query, context)
        return
    
    # 关闭菜单
    if data == "close_menu":
        await query.edit_message_text("✅ 已关闭管理后台")
        return

# ============== 命令处理 ==============

async def jf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """积分命令"""
    await show_points_page(update, context)

async def dh_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """兑换命令"""
    await show_exchange_page(update, context)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """取消操作"""
    user_id = update.effective_user.id
    
    if user_id in temp_commands:
        del temp_commands[user_id]
    
    if user_id in waiting_recharge_order:
        del waiting_recharge_order[user_id]
    
    if user_id in temp_products:
        del temp_products[user_id]
    
    context.user_data.clear()
    
    if is_admin(user_id):
        await admin_menu(update.message, context)
    else:
        await start(update, context)

# ============== 主函数 ==============

def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN 未设置！")
        return
    
    if not ADMIN_ID:
        logger.error("❌ ADMIN_ID 未设置！")
        return
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # 命令处理
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('admin', admin))
    application.add_handler(CommandHandler('id', id_command))
    application.add_handler(CommandHandler('jf', jf_command))
    application.add_handler(CommandHandler('dh', dh_command))
    application.add_handler(CommandHandler('cancel', cancel))
    
    # 回调处理
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # 文件处理（管理员）
    application.add_handler(MessageHandler(
        (filters.PHOTO | filters.VIDEO | filters.Document.ALL | 
         filters.AUDIO | filters.VOICE | filters.Sticker.ALL | 
         filters.ANIMATION) & filters.User(ADMIN_ID),
        handle_admin_file
    ))
    
    # 文本消息处理
    async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # 充值订单号输入
        if context.user_data.get('waiting_recharge_order'):
            handled = await handle_recharge_order_input(update, context)
            if handled:
                return
        
        # 商品名称输入
        if context.user_data.get('waiting_product_name'):
            await handle_product_name_input(update, context)
            return
        
        # 商品积分输入
        if context.user_data.get('waiting_product_points'):
            await handle_product_points_input(update, context)
            return
        
        # 等待命令名称
        if context.user_data.get('waiting_command_name'):
            await handle_command_name_input(update, context)
        # 等待内容输入
        elif context.user_data.get('waiting_content'):
            await handle_content_input(update, context)
        # 等待订单号
        elif context.user_data.get('awaiting_order'):
            await handle_order_input(update, context)
        # 其他情况
        else:
            await handle_normal_message(update, context)
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    
    # 媒体消息处理（商品内容）
    async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if context.user_data.get('waiting_product_content'):
            await handle_product_content_input(update, context)
        elif context.user_data.get('waiting_content'):
            await handle_content_input(update, context)
    
    application.add_handler(MessageHandler(
        (filters.PHOTO | filters.VIDEO | filters.Document.ALL) & filters.User(ADMIN_ID) & ~filters.COMMAND,
        media_handler
    ))
    
    # 转发消息处理
    application.add_handler(MessageHandler(
        filters.FORWARDED & filters.User(ADMIN_ID),
        handle_content_input
    ))
    
    logger.info("🤖 机器人启动中...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
