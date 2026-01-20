import logging
import os
import time
import random
import json
import uuid 
import requests 

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants, Message
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes

# --- 重点配置区 (请根据需要修改) ---
# 1. File ID 占位符：
WELCOME_IMAGE_FILE_ID = "REPLACE_WITH_YOUR_IMAGE_FILE_ID_HERE_1" # File ID 1: 用于 VIP 说明页
PAYMENT_IMAGE_FILE_ID = "REPLACE_WITH_YOUR_IMAGE_FILE_ID_HERE_2" # File ID 2: 用于 订单输入页

ORDER_PREFIX = "20260" 

# 2. 验证流程锁定时间 (秒)
LOCKOUT_DURATION_SECONDS = 5 * 3600 # 5小时
CHECKIN_COOLDOWN = 24 * 3600 # 每日签到冷却时间：24小时
VIDEO_DAILY_LIMIT = 3 # 每日视频观看次数限制
VIDEO_COOLDOWN = 24 * 3600 # 视频观看冷却时间：24小时

# 3. 模拟数据库/订单查询函数
def check_order_number(order_id: str) -> bool:
    return order_id.startswith(ORDER_PREFIX)

# --- 状态常量 ---
STATE_START = 'S_START'
STATE_AWAITING_ORDER_INPUT = 'S_ORDER_INPUT'
STATE_AWAITING_PAYMENT_CONFIRM = 'S_PAYMENT_CONFIRM' 
STATE_JF_MENU = 'S_JF_MENU' 
STATE_ADMIN_AWAITING_FILE = 'A_AWAITING_FILE' 
STATE_ADMIN_VIEW_FILES = 'A_VIEW_FILES' 
STATE_ADMIN_DELETE_FILE_CONFIRM = 'A_DEL_CONFIRM' 
STATE_WAITING_VIDEO_CONFIRM = 'STATE_WAITING_VIDEO_CONFIRM' # 新增：等待用户确认观看完成
# --- 重点配置区结束 ---


# --- 配置与初始化 ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- 环境变量读取 ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "") 
NEON_DATABASE_URL = os.getenv("NEON_DATABASE_URL", "placeholder_for_neon_db") 

try:
    ADMIN_IDS = [int(uid.strip()) for uid in ADMIN_IDS_STR.split(',') if uid.strip()]
except ValueError:
    logger.error("ADMIN_IDS 格式错误。")
    ADMIN_IDS = []

# 状态管理字典：Key: user_id, Value: (current_state, data_dict)
user_data_store = {} 

# 数据库连接对象 (Service B 不直接操作 DB)
DB_CONNECTION = None 

# --- 积分系统辅助函数 (与Service A协作所需) ---
API_SERVICE_A_URL = os.getenv("API_SERVICE_A_URL", "http://service-a-your-app-name.railway.app") 


# --- 辅助函数 ---

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def get_user_state(user_id: int) -> tuple:
    if user_id not in user_data_store:
        user_data_store[user_id] = (STATE_START, {'total_points': 0, 'last_checkin_time': 0, 'failed_attempts': 0, 'lock_until': 0, 'last_video_watch_time': 0, 'daily_video_count': 0})
    return user_data_store[user_id]

def set_user_state(user_id: int, state: str, data: dict = None):
    if data is None:
        data = {}
    user_data_store[user_id] = (state, data)

def clear_admin_state(user_id: int):
    state, _ = get_user_state(user_id)
    if state.startswith('A_'):
        user_data_store.pop(user_id, None)

# --- 验证流程辅助函数 ---
def is_user_locked(user_id: int) -> tuple[bool, int]:
    _, data = get_user_state(user_id)
    lock_until = data.get('lock_until', 0)
    if time.time() < lock_until:
        return True, int(lock_until - time.time())
    return False, 0

def lock_user_verification(user_id: int):
    lock_until = time.time() + LOCKOUT_DURATION_SECONDS
    set_user_state(user_id, STATE_START, {'lock_until': lock_until, 'failed_attempts': 3})

def unlock_user_verification(user_id: int):
    _, data = get_user_state(user_id)
    if 'lock_until' in data: data.pop('lock_until')
    if 'failed_attempts' in data: data.pop('failed_attempts')
    set_user_state(user_id, STATE_START, data)

# --- 积分系统辅助函数 ---
def get_user_points(user_id: int) -> int:
    _, data = get_user_state(user_id)
    return data.get('total_points', 0)

def update_user_points(user_id: int, points_change: int):
    _, data = get_user_state(user_id)
    data['total_points'] = data.get('total_points', 0) + points_change
    set_user_state(user_id, get_user_state(user_id)[0], data)

# --- 视频观看辅助函数 ---
def get_video_reward_data(user_id: int) -> dict:
    _, data = get_user_state(user_id)
    return {
        'count': data.get('daily_video_count', 0),
        'last_time': data.get('last_video_watch_time', 0)
    }

def update_video_watch_data(user_id: int, count: int, points: int):
    _, data = get_user_state(user_id)
    if time.time() > data.get('last_video_watch_time', 0) + VIDEO_COOLDOWN:
        data['daily_video_count'] = 1
        data['last_video_watch_time'] = time.time()
    else:
        data['daily_video_count'] = count
        data['last_video_watch_time'] = time.time()
        
    update_user_points(user_id, points)
    set_user_state(user_id, STATE_JF_MENU, data) 


# --- 命令处理函数 ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    is_locked, remaining_time = is_user_locked(user_id)
    
    keyboard = []
    welcome_text = (
        f"👋 欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~\n\n"
        "📢 小卫小卫，守门员小卫！\n"
        "一键入群，小卫帮你搞定！\n"
        "新人来报到，小卫查身份！\n\n"
    )
    
    if is_locked:
        friendly_time = f"{remaining_time // 3600}小时 {int((remaining_time % 3600) / 60)}分钟后解锁"
        welcome_text += f"⏳ 身份验证系统冷却中，请 {friendly_time} 后重试。"
        keyboard.append([InlineKeyboardButton("⏳ 验证锁定中...", callback_data="locked")])
        set_user_state(user_id, STATE_START, {'lock_until': time.time() + remaining_time, 'failed_attempts': 3}) 
    else:
        keyboard.append([InlineKeyboardButton("▶️ 开始身份验证", callback_data="verify_start")])
        keyboard.append([InlineKeyboardButton("💰 积分中心", callback_data="jf_menu")]) 
        set_user_state(user_id, STATE_START) 
        
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=reply_markup,
        parse_mode=constants.ParseMode.MARKDOWN
    )

async def hd_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """活动中心/开业活动"""
    keyboard = [
        [InlineKeyboardButton("📺 观看视频领积分 (每日3次)", callback_data="video_reward_menu")], 
        [InlineKeyboardButton("🔗 观看奖励广告 (Moontag)", callback_data="moontag_rewarded_ad")],
        [InlineKeyboardButton("⬅️ 返回首页", callback_data="back_to_start_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🎯 **活动中心**\n\n请选择您想参与的活动。",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /admin 命令"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("您没有权限使用此命令。")
        return
    clear_admin_state(user_id) 
    
    keyboard = [
        [InlineKeyboardButton("🔗 获取新的 File ID", callback_data="get_file_id_menu")],
        [InlineKeyboardButton("🖼️ 查看/删除已存 File ID", callback_data="admin_view_saved_files")],
        [InlineKeyboardButton("🛑 强制退出用户验证 (/c)", callback_data="admin_cancel_user_verification")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🎛️ **管理员后台**\n\n请选择您要执行的操作：",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# --- 取消用户验证命令 ---
async def admin_cancel_verification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """管理员 /c 命令：取消当前处于验证流程中的用户"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("您没有权限使用此命令。")
        return
        
    count = 0
    for uid, (state, data) in list(user_data_store.items()):
        if state == STATE_AWAITING_ORDER_INPUT or state == STATE_AWAITING_PAYMENT_CONFIRM:
            set_user_state(uid, STATE_START, {'lock_until': data.get('lock_until', 0)}) 
            count += 1
            
    await update.message.reply_text(f"✅ 已成功取消 {count} 个处于验证流程中的用户，并将其恢复到首页状态。")
    await admin_command(update, context) 

# --- 积分系统命令 ---
async def jf_menu_command(message: Message, context: ContextTypes.DEFAULT_TYPE) -> None:
    """积分菜单 (接收 Message 对象，修复了回调调用时的错误)"""
    user_id = message.from_user.id
    current_points = get_user_points(user_id)
    
    keyboard = [
        [InlineKeyboardButton("✅ 每日签到领积分 (固定/随机)", callback_data="jf_checkin")],
        [InlineKeyboardButton("📺 观看视频领积分 (每日3次)", callback_data="video_reward_menu")],
        [InlineKeyboardButton("⬅️ 返回首页", callback_data="back_to_start_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await message.reply_text(
        f"🌟 **积分中心**\n\n您当前的累计积分为：**{current_points}** 积分。",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_checkin(query: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理每日签到逻辑 (首次10分，后续3-8分)"""
    user_id = query.from_user.id
    _, data = get_user_state(user_id)
    last_checkin = data.get('last_checkin_time', 0)
    current_time = time.time()

    if current_time < last_checkin + CHECKIN_COOLDOWN:
        remaining = int((last_checkin + CHECKIN_COOLDOWN) - current_time)
        remaining_str = f"{remaining // 3600}小时 {int((remaining % 3600) / 60)}分钟"
        
        await query.edit_message_text(
            f"⏳ 您今天已经签到过了。\n请 {remaining_str} 后再来签到。",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回积分中心", callback_data="jf_menu")]])
        )
        return

    points_earned = 10 if last_checkin == 0 else random.randint(3, 8)
    
    update_user_points(user_id, points_earned)
    
    new_data = get_user_state(user_id)[1]
    new_data['last_checkin_time'] = current_time
    set_user_state(user_id, STATE_JF_MENU, new_data) 

    current_points = get_user_points(user_id)
    
    keyboard = [[InlineKeyboardButton("⬅️ 返回积分中心", callback_data="jf_menu")]]
    await query.edit_message_text(
        f"✅ **签到成功！**\n\n恭喜您获得了 **{points_earned}** 积分！\n\n"
        f"您当前的累计积分为：**{current_points}** 积分。",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

# --- 视频观看奖励逻辑 (新增) ---
async def video_reward_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    video_data = get_video_reward_data(user_id)
    
    count = video_data['count']
    last_time = video_data['last_time']
    current_time = time.time()
    
    keyboard = []
    msg = ""
    
    is_new_day = current_time > last_time + VIDEO_COOLDOWN or current_time < last_time 
    
    if not is_new_day and count >= VIDEO_DAILY_LIMIT:
        remaining = int((last_time + VIDEO_COOLDOWN) - current_time)
        remaining_str = f"{remaining // 3600}小时 {int((remaining % 3600) / 60)}分钟"
        msg = f"📺 今日观看次数已用完 ({count}/{VIDEO_DAILY_LIMIT})。\n请 {remaining_str} 后重试。"
        keyboard.append([InlineKeyboardButton("🔄 观看冷却中...", callback_data="video_reward_menu")])
        
    elif count == 0 or is_new_day:
        if is_new_day:
             data = get_user_state(user_id)[1]
             data['daily_video_count'] = 0
             data['last_video_watch_time'] = current_time
             set_user_state(user_id, STATE_JF_MENU, data)
             count = 0
             
        if count == 0:
            msg = "📺 第一次观看奖励丰厚！请点击下方按钮，看完视频后返回，系统将奖励您 10 积分。"
            keyboard.append([InlineKeyboardButton("▶️ 观看视频 (第 1 次/10分)", callback_data="video_watch_1")])
            
        elif count == 1:
            msg = "📺 第二次观看奖励！看完后返回，系统将奖励您 6 积分。"
            keyboard.append([InlineKeyboardButton("▶️ 观看视频 (第 2 次/6分)", callback_data="video_watch_2")])
            
        elif count == 2:
            msg = "📺 最后一次机会！看完后返回，系统将奖励您 3-10 随机积分。"
            keyboard.append([InlineKeyboardButton("▶️ 观看视频 (第 3 次/3-10分)", callback_data="video_watch_3")])
            
    else:
         msg = "系统状态异常，请返回积分中心重试。"
         
    keyboard.append([InlineKeyboardButton("⬅️ 返回积分中心", callback_data="jf_menu")])
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


async def handle_video_watch_init(query: Update, context: ContextTypes.DEFAULT_TYPE, watch_num: int) -> None:
    user_id = query.from_user.id
    _, data = get_user_state(user_id)
    
    if watch_num == 1: points = 10
    elif watch_num == 2: points = 6
    elif watch_num == 3: points = random.randint(3, 10)
    else: points = 0
        
    video_token = str(uuid.uuid4()) 
    
    data['video_token'] = video_token
    data['video_points_pending'] = points
    data['video_watch_num'] = watch_num
    set_user_state(user_id, STATE_WAITING_VIDEO_CONFIRM, data)
    
    AD_PAGE_URL = f"{API_SERVICE_A_URL}/start_video?token={video_token}" 

    keyboard = [
        [InlineKeyboardButton("▶️ 点击此处观看视频", url=AD_PAGE_URL)], 
        [InlineKeyboardButton("✅ 我已看完，点击确认领奖", callback_data=f"video_confirm_{watch_num}_{points}")] 
    ]

    await query.edit_message_text(
        f"🎬 请点击上方链接观看视频。\n"
        f"⚠️ **重要**: 观看完成后，请务必返回此聊天，并点击下方【确认领奖】按钮，积分才会到账。",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def confirm_video_reward(query: Update, context: ContextTypes.DEFAULT_TYPE, watch_num: int, points_claimed: int) -> None:
    user_id = query.from_user.id
    _, data = get_user_state(user_id)
    
    video_token = data.get('video_token')
    
    if not video_token:
        await query.answer("Token 信息丢失，请重新尝试。", callback_data="video_reward_menu")
        return

    try:
        validation_url = f"{API_SERVICE_A_URL}/validate_token?token={video_token}"
        validation_response = requests.get(validation_url, timeout=5)
        validation_response.raise_for_status()
        validation_data = validation_response.json()
        
        if validation_data.get('status') == 'TRIGGERED':
            claim_url = f"{API_SERVICE_A_URL}/claim_token?token={video_token}"
            requests.post(claim_url, timeout=5)
            
            update_user_points(user_id, points_claimed)
            
            current_time = time.time()
            new_count = data.get('daily_video_count', 0) + 1
            
            data['daily_video_count'] = new_count
            data['last_video_watch_time'] = current_time
            data.pop('video_token', None) 
            set_user_state(user_id, STATE_JF_MENU, data) 

            current_points = get_user_points(user_id)
            
            await query.edit_message_text(
                f"🌟 **积分发放成功！**\n\n您获得了 **{points_claimed}** 积分。\n\n"
                f"您当前的累计积分为：**{current_points}** 积分。",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回积分中心", callback_data="jf_menu")]])
            )
            
        else:
            await query.answer(f"Token 状态不正确: {validation_data.get('status')}")
            await video_reward_menu(query.message, context)

    except requests.exceptions.RequestException as e:
        logger.error(f"Bot 调用 Service A 失败: {e}")
        await query.answer("无法连接到奖励验证服务器，请稍后再试。")
        await video_reward_menu(query.message, context)


# --- 验证流程函数 (与上一版本一致) ---

def get_order_input_keyboard(user_id: int) -> InlineKeyboardMarkup:
    _, data = get_user_state(user_id)
    attempts = data.get('failed_attempts', 0)
    keyboard = [
        [InlineKeyboardButton(f"🔄 重新输入订单号 (剩余 {2 - attempts} 次)", callback_data="verify_input_order")]
    ]
    keyboard.append([InlineKeyboardButton("⬅️ 返回首页", callback_data="back_to_start_main")])
    return InlineKeyboardMarkup(keyboard)

async def send_order_input_page(update: Update, context: ContextTypes.DEFAULT_TYPE, is_retry: bool = False) -> None:
    user_id = update.effective_user.id
    _, data = get_user_state(user_id)
    attempts = data.get('failed_attempts', 0)
    
    if is_retry:
        message_text = f"🧐 请输入您的订单号。\n(您还有 {2 - attempts} 次机会)"
    else:
        message_text = ("🧐 请输入您的订单号。")
        
    file_id_placeholder_2 = f"[File ID 2 占位：{PAYMENT_IMAGE_FILE_ID[:10]}...]"
    if PAYMENT_IMAGE_FILE_ID and PAYMENT_IMAGE_FILE_ID != "REPLACE_WITH_YOUR_IMAGE_FILE_ID_HERE_2":
         message_text += f"\n\n--- 此处的提示信息 ---\n{file_id_placeholder_2}"
    
    keyboard = get_order_input_keyboard(user_id)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(message_text, reply_markup=keyboard, parse_mode=constants.ParseMode.MARKDOWN)
    else:
         await update.message.reply_text(message_text, reply_markup=keyboard, parse_mode=constants.ParseMode.MARKDOWN)
        
    set_user_state(user_id, STATE_AWAITING_ORDER_INPUT, data)

def get_payment_confirm_keyboard(user_id: int, is_success: bool) -> tuple[InlineKeyboardMarkup, str]:
    keyboard = []
    if is_success:
        keyboard.append([InlineKeyboardButton("🚀 立即加入VIP群聊 (点击跳转)", url="YOUR_VIP_GROUP_INVITE_LINK")]) 
        keyboard.append([InlineKeyboardButton("🏠 返回首页", callback_data="back_to_start_main")])
        unlock_user_verification(user_id) 
        content_text = ("🎉 **验证成功！恭喜您获得 VIP 权限！**\n\n"
                        "请点击上方按钮，立即加入我们的专属中转群聊。")
    else:
        keyboard.append([InlineKeyboardButton("⬅️ 返回订单输入", callback_data="verify_input_order")])
        content_text = ("⚠️ **订单未找到或格式错误**。\n\n"
                        "请仔细核对您的订单号。")
    return InlineKeyboardMarkup(keyboard), content_text

async def send_payment_confirmation_page(update: Update, context: ContextTypes.DEFAULT_TYPE, is_success: bool) -> None:
    user_id = update.effective_user.id
    TUTORIAL_TEXT = ("📜 **【订单号查找详细教程】**\n"
                     "--- 账单详情 ---\n"
                     "➡️ **我的账单**\n"
                     "➡️ **账单详情**\n"
                     "➡️ **更多** -> **订单号**\n"
                     "➡️ **详细步骤** (此处应为教程文字或链接)")
    
    file_id_placeholder_1 = f"[File ID 1 占位：{WELCOME_IMAGE_FILE_ID[:10]}...]"
    
    if not is_success:
        payment_button = [InlineKeyboardButton("✅ 我已付款，开始验证", callback_data="payment_confirm_paid")]
        navigation_button = [InlineKeyboardButton("⬅️ 返回首页", callback_data="back_to_start_main")]
        keyboard = InlineKeyboardMarkup([payment_button, navigation_button])
        
        content_text = ("💎 **VIP会员特权说明**：\n"
                        "✅ 专属中转通道\n"
                        "✅ 优先审核入群\n"
                        "✅ 7x24小时客服支持\n"
                        "✅ 定期福利活动")
        
        final_message = (content_text + "\n\n" + 
                         f"[File ID 1 占位：{file_id_placeholder_1}]" + 
                         "\n\n" + TUTORIAL_TEXT)
        
        if update.callback_query:
            await update.callback_query.edit_message_text(final_message, reply_markup=keyboard, parse_mode=constants.ParseMode.MARKDOWN)
        return
        
    else:
        keyboard, content_text = get_payment_confirm_keyboard(user_id, is_success=True)
        final_message = (content_text + "\n\n" + 
                         f"[File ID 2 占位：{PAYMENT_IMAGE_FILE_ID[:10]}...]" + 
                         "\n\n" + TUTORIAL_TEXT)
        
        if update.callback_query:
            await update.callback_query.edit_message_text(final_message, reply_markup=keyboard, parse_mode=constants.ParseMode.MARKDOWN)
        
    if is_success:
        await start_command(update, context)

async def handle_verification_input(update: Update, context: ContextTypes.DEFAULT_TYPE, next_step: str = None) -> None:
    user_id = update.effective_user.id
    current_state, data = get_user_state(user_id)
    
    if current_state not in [STATE_AWAITING_ORDER_INPUT, STATE_AWAITING_PAYMENT_CONFIRM]:
        await start_command(update, context)
        return

    if current_state == STATE_AWAITING_ORDER_INPUT:
        order_id = update.message.text.strip()
        attempts = data.get('failed_attempts', 0)
        
        if check_order_number(order_id):
            if 'failed_attempts' in data: data.pop('failed_attempts')
            if 'lock_until' in data: data.pop('lock_until')
            
            set_user_state(user_id, STATE_AWAITING_PAYMENT_CONFIRM, data)
            await send_payment_confirmation_page(update, context, is_success=True) 
        else:
            attempts += 1
            data['failed_attempts'] = attempts
            
            if attempts >= 2:
                lock_user_verification(user_id)
                await update.message.reply_text("❌ 订单查找失败。系统已锁定身份验证入口 5 小时，请稍后再试。")
                await start_command(update, context)
                return
            else:
                data['last_input_time'] = time.time()
                set_user_state(user_id, STATE_AWAITING_ORDER_INPUT, data)
                
                await update.message.reply_text(
                    f"⚠️ 未查询到订单信息，请重试。\n"
                    f"(您还有 {2 - attempts} 次机会)",
                    reply_markup=get_order_input_keyboard(user_id)
                )
    else:
        await update.message.reply_text("请使用界面上的按钮进行操作。", reply_markup=get_payment_confirm_keyboard(user_id, current_state == STATE_AWAITING_PAYMENT_CONFIRM)[0])


# --- 回调查询处理函数 (已修复调用逻辑) ---

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data
    
    current_state, current_data = get_user_state(user_id)

    if data == "locked":
        await query.answer("请等待身份验证系统冷却时间结束。")
        await start_command(update, context)
        return
        
    if data == "verify_start":
        is_locked, _ = is_user_locked(user_id)
        if is_locked:
            await start_command(update, context)
            return
        set_user_state(user_id, STATE_AWAITING_PAYMENT_CONFIRM, {'failed_attempts': 0}) 
        await send_payment_confirmation_page(query.message, context, is_success=False) 
        return
        
    if data == "back_to_start_main":
        await start_command(update, context)
        return

    if data == "activity_center":
        await hd_command(query.message, context)
        return
        
    if data == "moontag_rewarded_ad":
        if not API_SERVICE_A_URL or API_SERVICE_A_URL == "http://service-a-your-app-name.railway.app":
            await query.edit_message_text("❌ 配置错误：请设置 API_SERVICE_A_URL。")
            return
            
        keyboard = [[InlineKeyboardButton("⬅️ 返回活动中心", callback_data="activity_center")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        response_text = ("🌟 **奖励广告**\n\n"
                         "请**点击下方链接**，在浏览器中观看广告。\n"
                         f"🔗 **[点击此处进入广告页面]({API_SERVICE_A_URL}/start_video?token=DUMMY_TOKEN_HERE)")
        await query.edit_message_text(response_text, reply_markup=reply_markup, parse_mode='Markdown')
        return
        
    # --- 积分系统按钮 ---
    if data == "jf_menu":
        await jf_menu_command(query.message, context) 
        return
    
    if data == "jf_checkin":
        await handle_checkin(query, context)
        return
        
    if data.startswith("video_watch_"):
        watch_num = int(data.split('_')[2])
        await handle_video_watch_init(query, context, watch_num)
        return
        
    if data.startswith("video_confirm_"):
        parts = data.split('_')
        watch_num = int(parts[2])
        points = int(parts[3])
        await confirm_video_reward(query, context, watch_num, points)
        return

    # --- 验证流程按钮 ---
    if data == "verify_input_order":
        is_locked, _ = is_user_locked(user_id)
        if is_locked:
            await query.answer("验证系统仍在冷却中，请稍后再试。")
            await start_command(update, context)
            return
        await send_order_input_page(query.message, context, is_retry=True)
        return
        
    if data == "payment_confirm_paid":
        is_locked, _ = is_user_locked(user_id)
        if is_locked:
            await query.answer("当前系统锁定中，请稍后再试。")
            await start_command(update, context)
            return
        current_data['failed_attempts'] = current_data.get('failed_attempts', 0) 
        set_user_state(user_id, STATE_AWAITING_ORDER_INPUT, current_data)
        await send_order_input_page(query.message, context, is_retry=False)
        return
        
    # --- Admin 逻辑 ---
    if data.startswith("A_"):
        if not is_admin(user_id):
            await query.edit_message_text("您没有权限访问此菜单。")
            return

        if data == "admin_view_saved_files": await admin_view_files(query, context)
        
        if data.startswith("admin_view_file_"):
            file_key = data.split('_')[2]
            await admin_view_file_details(query, context, file_key)
            return
            
        if data.startswith("admin_confirm_delete_"):
            file_key = data.split('_')[2]
            await admin_delete_file_confirm(query, context, file_key)
            return
            
        if data.startswith("admin_confirm_delete_"):
            file_key = data.split('_')[2]
            await admin_delete_file(query, context, file_key)
            return

        if data == "get_file_id_menu":
            set_user_state(user_id, STATE_ADMIN_AWAITING_FILE)
            keyboard = [[InlineKeyboardButton("⬅️ 返回管理后台", callback_data="back_to_admin")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("➡️ **文件ID获取器**\n\n请发送您想要获取ID的图片或文件。", reply_markup=reply_markup, parse_mode='Markdown')
        
        elif data == "back_to_admin":
            clear_admin_state(user_id) 
            keyboard = [
                [InlineKeyboardButton("🔗 获取新的 File ID", callback_data="get_file_id_menu")],
                [InlineKeyboardButton("🖼️ 查看/删除已存 File ID", callback_data="admin_view_saved_files")],
                [InlineKeyboardButton("🛑 强制退出用户验证", callback_data="admin_cancel_user_verification")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("🎛️ **管理员后台**\n\n请选择您要执行的操作：", reply_markup=reply_markup, parse_mode='Markdown')
        
        elif data == "admin_cancel_user_verification":
            await admin_cancel_verification(query.message, context) 


# --- 消息处理函数 (全局拦截和 Admin File ID) ---

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    current_state, _ = get_user_state(user_id)
    
    if current_state == STATE_ADMIN_AWAITING_FILE:
        await handle_file_message(update, context)
        return

    if current_state == STATE_AWAITING_ORDER_INPUT and update.message.text:
        await handle_verification_input(update, context)
        return

    if not update.message.text or not update.message.text.startswith('/'):
        await start_command(update, context)


# --- Admin File ID 消息处理器 (占位未修改) ---
async def handle_file_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    current_state, _ = get_user_state(user_id)

    if current_state == STATE_ADMIN_AWAITING_FILE:
        file_id = None
        
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
        elif update.message.document:
            file_id = update.message.document.file_id
        
        if file_id:
            new_key = str(int(time.time() * 1000)) 
            description = f"Admin uploaded {time.strftime('%Y%m%d_%H%M')}"
            
            keyboard = [
                [InlineKeyboardButton("🔗 继续获取下一个 File ID", callback_data="get_file_id_menu")],
                [InlineKeyboardButton("🖼️ 查看/管理所有 File ID", callback_data="admin_view_saved_files")],
                [InlineKeyboardButton("⬅️ 返回管理后台", callback_data="back_to_admin")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            response_text = f"✅ **File ID 已获取 (Key: {new_key})**\n\n请复制以下ID：\n\n<code>{file_id}</code>\n\n<i>(注意：File ID 保存逻辑需适配 Service A)</i>"
            
            await update.message.reply_text(response_text, reply_markup=reply_markup, parse_mode='HTML')
            clear_admin_state(user_id) 
        else:
            await update.message.reply_text(
                "⚠️ 请发送一个图片或文件以便获取 File ID。",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ 返回管理后台", callback_data="back_to_admin")]
                ])
            )

# --- Admin File ID 查看与删除逻辑 (Bot 端占位) ---
def get_file_list_markup(user_id: int) -> InlineKeyboardMarkup:
    keyboard = []
    keyboard.append([InlineKeyboardButton("⚠️ 仅用于占位，请使用 Service A API", callback_data="admin_view_saved_files")])
    keyboard.append([InlineKeyboardButton("⬅️ 返回管理后台", callback_data="back_to_admin")])
    return InlineKeyboardMarkup(keyboard)

async def admin_view_files(query: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = query.from_user.id
    if not is_admin(user_id): return
    set_user_state(user_id, STATE_ADMIN_VIEW_FILES)
    markup = get_file_list_markup(user_id)
    await query.edit_message_text("🗄️ **已保存的 File ID 记录** (功能待完善，请使用 `/admin` 查看)", reply_markup=markup, parse_mode='Markdown')

async def admin_delete_file_confirm(query: Update, context: ContextTypes.DEFAULT_TYPE, file_key: str) -> None:
    await query.answer("删除确认功能应通过 Service A 接口实现。")
    await admin_view_files(query, context)

async def admin_delete_file(query: Update, context: ContextTypes.DEFAULT_TYPE, file_key: str) -> None:
    await query.answer("删除功能应通过 Service A 接口实现。")
    await admin_view_files(query, context)


# --- 主程序 ---

def main() -> None:
    if not BOT_TOKEN:
        logger.error("错误：未找到 BOT_TOKEN 环境变量。")
        return

    application = Application.builder().token(BOT_TOKEN).build()

    # 1. 命令处理器
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("c", admin_cancel_verification)) 
    application.add_handler(CommandHandler("hd", hd_command)) 
    application.add_handler(CommandHandler("jf", jf_menu_command)) 

    # 2. 回调查询处理器 (处理按钮点击)
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # 3. 消息处理器 (捕获所有文本和文件，实现全局拦截和状态驱动)
    application.add_handler(MessageHandler(filters.ALL, handle_message))

    # 启动机器人
    logger.info("Bot 启动成功，正在轮询...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
