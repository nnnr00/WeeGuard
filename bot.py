import logging
import os
import asyncio
import random
import uuid
import ast 
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaDocument, InputMediaVideo
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler, ConversationHandler, MessageHandler, filters
)
from telegram.constants import ParseMode

from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, UniqueConstraint, asc, func, desc
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import declarative_base as declarative_base_20 # 兼容性修复

# ‼️ APScheduler 导入
from apscheduler.schedulers.asyncio import AsyncIOScheduler 

# ======================================================================
# === ‼️ 1. 核心配置：管理员替换区域 (请在此处替换占位符) ===
# ======================================================================

# --- Moontag 活动 URL ---
MOONTAG_ACTIVITY_URL = "https://your-external-host.com/activity.html" # ‼️ 替换为您的活动页面URL

# --- VIP 验证设置 ---
ORDER_PREFIX = "20260" 

# --- File ID 占位符 (‼️ 请替换为您的真实 File ID) ---
WX_RECHARGE_QR_FILE_ID = "AgACAgQAAxkb..." # ‼️ 替换
ALI_RECHARGE_QR_FILE_ID = "AgACAgQAAxkb..." # ‼️ 替换
VIP_VERIFICATION_IMAGE_FILE_ID = "AgACAgQAAxkb..." # ‼️ 替换
VIP_ORDER_IMAGE_FILE_ID = "AgACAgQAAxkb..." # ‼️ 替换

# --- 链接占位符 ---
VIP_JOIN_GROUP_LINK = "https://t.me/joinchat/..." # ‼️ 替换为真实入群链接

# ======================================================================
# === 2. 基础配置 & 状态定义 ===
# ======================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

try: ADMIN_ID = int(os.getenv("ADMIN_ID"))
except: ADMIN_ID = None

VOUCHER_EXPIRY_SECONDS = 3600 * 6 
AD_VIEW_LIMIT = 3
AD_POINTS_TIER = {1: 10, 2: 6, 3: (3, 10)} 

MAX_ATTEMPTS = 2
LOCKOUT_HOURS = 5 

MAX_ITEMS_PER_PAGE = 10
MAX_CONTENT_ITEMS = 100
FORWARD_EXPIRY_MINUTES = 5 # 频道转发消息的自动删除时间 (5分钟)

RECHARGE_AMOUNT = 5 
RECHARGE_POINTS = 100 
RECHARGE_ATTEMPTS = 2
RECHARGE_LOCKOUT_HOURS = 5 

TEST_EXCHANGE_ITEM_NAME = "🎁 零积分测试礼包"
TEST_EXCHANGE_COST = 0

# --- 状态定义 ---
GET_FILE_ID_STEP = 1
VIP_ORDER_INPUT = 100 
CHANNEL_BIND_CMD_INPUT = 200 
CHANNEL_BIND_SOURCE_INPUT = 201 
CHANNEL_BIND_CONTENT_COLLECT = 202
CHANNEL_BIND_CONFIRM = 203
RECHARGE_MENU = 300
RECHARGE_WX_INPUT = 301
RECHARGE_ALI_INPUT = 302
EXCHANGE_CMD_START = 400
EXCHANGE_CMD_CONFIRM = 401
ADMIN_ITEM_ADD_NAME = 501
ADMIN_ITEM_ADD_POINTS = 502
ADMIN_ITEM_ADD_CONTENT_TYPE = 503
ADMIN_ITEM_ADD_CONTENT = 504
ADMIN_ITEM_DELETE_CONFIRM = 505


logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- 数据库设置 ---
Base = declarative_base_20() 

class User(Base):
    __tablename__ = 'users'
    # ‼️ 修复：使用标准 Integer 主键定义
    id = Column(Integer, primary_key=True) 
    telegram_id = Column(Integer, unique=True)
    username = Column(String)
    is_admin = Column(Boolean, default=False)
    points = Column(Integer, default=0)
    # VIP 状态
    vip_attempts_left = Column(Integer, default=MAX_ATTEMPTS)
    vip_lockout_until = Column(DateTime, nullable=True)
    # 充值状态
    wx_recharge_used = Column(Boolean, default=False)
    wx_attempts_left = Column(Integer, default=RECHARGE_ATTEMPTS)
    wx_lockout_until = Column(DateTime, nullable=True)
    zhifubao_recharge_used = Column(Boolean, default=False)
    zhifubao_attempts_left = Column(Integer, default=RECHARGE_ATTEMPTS)
    zhifubao_lockout_until = Column(DateTime, nullable=True)

class RewardVoucher(Base):
    __tablename__ = 'reward_vouchers'
    # ‼️ 修复：使用标准 Integer 主键定义
    id = Column(Integer, primary_key=True) 
    voucher_id = Column(String, unique=True, nullable=False) 
    user_telegram_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    is_used = Column(Boolean, default=False)
    __table_args__ = (UniqueConstraint('voucher_id', name='uix_voucher_id'),)

class DailySignIn(Base):
    __tablename__ = 'daily_sign_in'
    # ‼️ 修复：使用标准 Integer 主键定义
    id = Column(Integer, primary_key=True)
    user_telegram_id = Column(Integer, nullable=False, unique=True)
    last_signed_in_date = Column(DateTime, nullable=False)

class AdViewsTracker(Base):
    __tablename__ = 'ad_views_tracker'
    # ‼️ 修复：使用标准 Integer 主键定义
    id = Column(Integer, primary_key=True)
    user_telegram_id = Column(Integer, nullable=False)
    view_date = Column(DateTime, nullable=False)
    views_count = Column(Integer, default=0)
    __table_args__ = (UniqueConstraint('user_telegram_id', 'view_date', name='uix_ad_view_daily'),)

class ChannelForwardLibrary(Base):
    __tablename__ = 'channel_forward_library'
    # ‼️ 修复：使用标准 Integer 主键定义
    id = Column(Integer, primary_key=True)
    custom_command = Column(String(50), nullable=False, unique=True) 
    source_chat_id = Column(String, nullable=False) 
    content_data = Column(String, nullable=False) 
    created_at = Column(DateTime, default=datetime.utcnow)

class PointExchangeItem(Base):
    __tablename__ = 'point_exchange_item'
    # ‼️ 修复：使用标准 Integer 主键定义
    id = Column(Integer, primary_key=True)
    item_name = Column(String(100), nullable=False)
    cost = Column(Integer, default=0)
    content_data = Column(String, nullable=False) 
    is_available = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class PointTransaction(Base):
    __tablename__ = 'point_transaction'
    # ‼️ 修复：使用标准 Integer 主键定义
    id = Column(Integer, primary_key=True)
    user_telegram_id = Column(Integer, nullable=False)
    item_id = Column(Integer, nullable=False) 
    transaction_time = Column(DateTime, default=datetime.utcnow)
    points_spent = Column(Integer, default=0) 
    is_successful = Column(Boolean, default=False)
    content_delivered = Column(String, nullable=True) 

class UserAccess(Base):
    __tablename__ = 'user_access'
    # ‼️ 修复：使用标准 Integer 主键定义
    id = Column(Integer, primary_key=True)
    user_telegram_id = Column(Integer, nullable=False)
    command_used = Column(String(50), nullable=False) 
    access_granted_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint('user_telegram_id', 'command_used', name='uix_user_command_access'),)


# 初始化数据库引擎和 Session
try:
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
except Exception as e:
    logger.critical(f"数据库引擎创建失败: {e}")
    engine = None
    SessionLocal = None

def init_db():
    if engine:
        # ✅ 修复：添加 checkfirst=True 避免重复创建表导致的 DuplicateTable 错误
        Base.metadata.create_all(bind=engine, checkfirst=True) 
        logger.info("数据库表已初始化。")

def get_db():
    if SessionLocal:
        db = SessionLocal()
        try: yield db
        finally: db.close()

# --- APScheduler 调度器定义 ---
scheduler = AsyncIOScheduler()
# --- 辅助函数 (完整定义) ---
def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if ADMIN_ID is None or user_id != ADMIN_ID:
            await update.message.reply_text("❌ 你没有权限执行此操作。"); return
        return await func(update, context, *args, **kwargs)
    return wrapper

def get_user_points(user_id):
    db = next(get_db())
    try:
        user = db.query(User).filter(User.telegram_id == user_id).first()
        return user.points if user else 0
    finally: db.close()

def add_points(user_id, amount):
    db = next(get_db())
    try:
        user = db.query(User).filter(User.telegram_id == user_id).first()
        if user:
            user.points += amount
            db.commit()
            logger.info(f"用户 {user_id} 获得 {amount} 积分。新积分: {user.points}")
            return user.points
    except Exception as e:
        db.rollback()
        logger.error(f"增加积分失败: {e}")
        return None
    finally: db.close()

def generate_voucher(user_id):
    db = next(get_db())
    try:
        new_voucher = RewardVoucher(
            voucher_id=str(uuid.uuid4()),
            user_telegram_id=user_id,
            expires_at=datetime.utcnow() + timedelta(seconds=VOUCHER_EXPIRY_SECONDS)
        )
        db.add(new_voucher)
        db.commit()
        logger.info(f"生成凭证 {new_voucher.voucher_id[:8]} 给用户 {user_id}")
        return new_voucher.voucher_id
    except Exception as e:
        db.rollback()
        logger.error(f"生成凭证失败: {e}")
        return None
    finally: db.close()

def get_today_ad_views(user_id):
    db = next(get_db())
    today_str = datetime.utcnow().strftime('%Y-%m-%d')
    try:
        record = db.query(AdViewsTracker).filter(
            AdViewsTracker.user_telegram_id == user_id,
            AdViewsTracker.view_date == datetime.strptime(today_str, '%Y-%m-%d')
        ).first()
        return record.views_count if record else 0
    finally: db.close()

def increment_ad_view(user_id):
    db = next(get_db())
    today_str = datetime.utcnow().strftime('%Y-%m-%d')
    try:
        record = db.query(AdViewsTracker).filter(
            AdViewsTracker.user_telegram_id == user_id,
            AdViewsTracker.view_date == datetime.strptime(today_str, '%Y-%m-%d')
        ).first()
        if not record or record.views_count >= AD_VIEW_LIMIT:
            return None, 0, 0

        record.views_count += 1
        new_count = record.views_count
        db.commit()
        
        points_awarded = 0
        if new_count == 1: points_awarded = AD_POINTS_TIER[1]
        elif new_count == 2: points_awarded = AD_POINTS_TIER[2]
        elif new_count == 3: points_awarded = random.randint(*AD_POINTS_TIER[3])
        
        new_total = add_points(user_id, points_awarded)
        return new_count, points_awarded, new_total
        
    except Exception as e:
        db.rollback()
        logger.error(f"增加广告观看次数失败: {e}")
        return None, 0, get_user_points(user_id)
    finally: db.close()

def get_verification_status(user_id):
    db = next(get_db())
    try:
        user = db.query(User).filter(User.telegram_id == user_id).first()
        if not user: return None, None, None
        lockout_time = user.vip_lockout_until
        attempts_left = user.vip_attempts_left
        is_locked = lockout_time is not None and lockout_time > datetime.utcnow()
        return is_locked, attempts_left, user
    finally: db.close()

def process_order_attempt(user_id, order_input, method_type):
    db = next(get_db())
    try:
        user = db.query(User).filter(User.telegram_id == user_id).first()
        if not user: return "SYSTEM_ERROR", 0, None

        if method_type == 'VIP':
            is_locked, attempts, _ = get_verification_status(user_id)
            if is_locked: return "LOCKED", attempts, user.vip_lockout_until
            
            if not order_input.strip().startswith(ORDER_PREFIX):
                user.vip_attempts_left -= 1
                if user.vip_attempts_left <= 0:
                    user.vip_lockout_until = datetime.utcnow() + timedelta(hours=LOCKOUT_HOURS)
                    user.vip_attempts_left = 0
                    db.commit()
                    return "FAILED_AND_LOCKED", 0, user.vip_lockout_until
                else:
                    db.commit()
                    return "FAILED", user.vip_attempts_left, None
            else:
                user.vip_attempts_left = MAX_ATTEMPTS
                user.vip_lockout_until = None
                db.commit()
                return "SUCCESS", 0, None
        return "UNHANDLED_TYPE", 0, None
    except Exception as e:
        db.rollback()
        logger.error(f"VIP验证处理错误: {e}")
        return "SYSTEM_ERROR", 0, None
    finally: db.close()

def get_recharge_status(user_id, payment_type):
    db = next(get_db())
    try:
        user = db.query(User).filter(User.telegram_id == user_id).first()
        if not user: return None, None, None, None
        
        if payment_type == 'WX':
            used, lockout_time, attempts_left = user.wx_recharge_used, user.wx_lockout_until, user.wx_attempts_left
        else: # ALI
            used, lockout_time, attempts_left = user.zhifubao_recharge_used, user.zhifubao_lockout_until, user.zhifubao_attempts_left
            
        is_locked = lockout_time is not None and lockout_time > datetime.utcnow()
        return used, is_locked, attempts_left, user
    finally: db.close()

def process_recharge_attempt(user_id, order_input, payment_type):
    db = next(get_db())
    try:
        user = db.query(User).filter(User.telegram_id == user_id).first()
        if not user: return "SYSTEM_ERROR", 0, None

        is_used, is_locked, attempts_left, _ = get_recharge_status(user_id, payment_type)
        if is_used or is_locked: return "ALREADY_DONE_OR_LOCKED", 0, None

        prefix = "4200" if payment_type == 'WX' else "4768"

        if not order_input.startswith(prefix):
            if payment_type == 'WX':
                user.wx_attempts_left -= 1
                if user.wx_attempts_left <= 0:
                    user.wx_lockout_until = datetime.utcnow() + timedelta(hours=RECHARGE_LOCKOUT_HOURS)
                    user.wx_attempts_left = 0
            else: # ALI
                user.zhifubao_attempts_left -= 1
                if user.zhifubao_attempts_left <= 0:
                    user.zhifubao_lockout_until = datetime.utcnow() + timedelta(hours=RECHARGE_LOCKOUT_HOURS)
                    user.zhifubao_attempts_left = 0
            
            db.commit()
            remaining_attempts = user.wx_attempts_left if payment_type == 'WX' else user.zhifubao_attempts_left
            return "FAILED", remaining_attempts, None
        
        else:
            user.points += RECHARGE_POINTS
            if payment_type == 'WX':
                user.wx_recharge_used = True
                user.wx_attempts_left = RECHARGE_ATTEMPTS
                user.wx_lockout_until = None
            else:
                user.zhifubao_recharge_used = True
                user.zhifubao_attempts_left = RECHARGE_ATTEMPTS
                user.zhifubao_lockout_until = None
                
            db.commit()
            return "SUCCESS", RECHARGE_POINTS, user.points

    except Exception as e:
        db.rollback()
        logger.error(f"充值验证处理错误 ({payment_type}): {e}")
        return "SYSTEM_ERROR", 0, None
    finally: db.close()

def get_user_transactions(user_id, page=1):
    db = next(get_db())
    try:
        total_count = db.query(PointTransaction).filter(PointTransaction.user_telegram_id == user_id).count()
        offset = (page - 1) * MAX_ITEMS_PER_PAGE
        transactions = db.query(PointTransaction).filter(
            PointTransaction.user_telegram_id == user_id
        ).order_by(desc(PointTransaction.transaction_time)).offset(offset).limit(MAX_ITEMS_PER_PAGE).all()
        return total_count, transactions
    finally: db.close()

def check_user_command_access(user_id, command):
    db = next(get_db())
    try:
        user = db.query(User).filter(User.telegram_id == user_id).first()
        if user and user.is_admin: return True, None 
        
        access = db.query(UserAccess).filter(UserAccess.user_telegram_id == user_id, UserAccess.command_used == command).first()
        
        if access: return True, access.access_granted_at
            
        return False, None
    finally: db.close()

async def delete_forwarded_message_after_delay(chat_id, message_ids, delay_minutes, update, context):
    delay_seconds = delay_minutes * 60
    await asyncio.sleep(delay_seconds)
    if not isinstance(message_ids, list): message_ids = [message_ids]
    for msg_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            logger.info(f"定时删除成功: Chat {chat_id}, Msg {msg_id}")
        except Exception as e:
            logger.warning(f"定时删除失败: {e}")

# --- APScheduler 调度器定义 ---
scheduler = AsyncIOScheduler()

def schedule_message_deletion(chat_id, message_ids, delay_minutes, update, context):
    if not scheduler.running:
        logger.warning("Scheduler 未运行，无法调度删除任务。")
        return
        
    run_time = datetime.utcnow() + timedelta(minutes=delay_minutes)
    
    scheduler.add_job(
        delete_forwarded_message_after_delay, 
        'date', 
        run_date=run_time,
        args=[chat_id, message_ids, 0, update, context], 
        id=f"delete_{chat_id}_{hash(tuple(sorted(message_ids)))}_{datetime.now().timestamp()}"
    )
    logger.info(f"已调度删除任务在 {run_time.strftime('%H:%M:%S')}")

# --- 机器人基础命令 ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not engine: await update.message.reply_text("系统初始化错误，请稍后再试。"); return

    db = next(get_db())
    try:
        existing_user = db.query(User).filter(User.telegram_id == user.id).first()
        if not existing_user:
            new_user = User(telegram_id=user.id, username=user.username or str(user.id), is_admin=(user.id == ADMIN_ID))
            db.add(new_user)
            db.commit()
            logger.info(f"新用户注册: {user.id} ({user.username})")
        
        keyboard = [
            [InlineKeyboardButton("🎉 活动中心 /hd", callback_data='activity_center')],
            [InlineKeyboardButton("💰 积分系统 /jf", callback_data='points_menu')], 
            [InlineKeyboardButton("🛍️ 兑换中心 /dh", callback_data='exchange_menu')]
        ]
        
        if ADMIN_ID is not None and user.id == ADMIN_ID:
             keyboard.append([InlineKeyboardButton("⚙️ 管理后台", callback_data='admin_panel_main')])
             
        reply_markup = InlineKeyboardMarkup(keyboard)

        is_locked, attempts, _ = get_verification_status(user.id)
        welcome_message = f"👋 欢迎回来，{user.mention_html()}！请选择下方选项。"
        
        if is_locked: welcome_message += "\n\n⚠️ **注意：您的身份验证已锁定，请等待解锁时间。**"
        elif user.vip_attempts_left < MAX_ATTEMPTS and not is_locked:
             welcome_message += "\n\n💎 身份验证未完成，请先进行验证。"
        
        await update.message.reply_text(text=welcome_message, parse_mode=ParseMode.HTML, reply_markup=reply_markup)

    except Exception as e:
        db.rollback()
        logger.error(f"数据库操作错误: {e}")
        await update.message.reply_text("抱歉，在注册过程中发生错误。")
    finally:
        db.close()

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("请使用 /start 来开始。")

# --- VIP 验证流程 ---
async def start_vip_verification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    is_locked, attempts, lockout_time = get_verification_status(user_id)
    
    if is_locked:
        remaining = int((lockout_time - datetime.utcnow()).total_seconds())
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        await query.edit_message_text(f"⏳ 验证失败次数过多，请在 {hours} 小时 {minutes} 分钟后重试。", parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    keyboard = [[InlineKeyboardButton("✅ 我已付款，开始验证", callback_data='vip_start_input')]]
    FILE_ID_PLACEHOLDER = "AgACAgQAAxkb..." # ‼️ 请替换为您要展示的图片 File ID
    
    message_text = "👋 欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~\n\n📢 小卫小卫，守门员小卫！\n一键入群，小卫帮你搞定！\n新人来报到，小卫查身份！"
    
    try:
        await query.edit_message_text(text=message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    except:
        await query.message.reply_html(message_text, reply_markup=InlineKeyboardMarkup(keyboard))
        
    return VIP_ORDER_INPUT

async def vip_order_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    order_input = update.message.text.strip()
    
    status, next_attempts, result_info = process_order_attempt(user_id, order_input, 'VIP')
    
    if status == "SUCCESS":
        db = next(get_db())
        try:
            user = db.query(User).filter(User.telegram_id == user_id).first()
            user.vip_attempts_left = MAX_ATTEMPTS 
            db.commit()
        finally: db.close()
            
        FILE_ID_PLACEHOLDER = "AgACAgQAAxkb..." # ‼️ 请替换为您要展示的图片 File ID
        success_text = "🎉 订单验证成功！"
        
        group_keyboard = [
            [InlineKeyboardButton("🚀 立即加入专属群组", url="https://t.me/joinchat/...")], # ‼️ 替换为真实邀请链接
            [InlineKeyboardButton("返回积分主菜单", callback_data='points_menu')]
        ]
        
        await update.message.reply_photo(
            photo=FILE_ID_PLACEHOLDER, 
            caption=success_text,
            reply_markup=InlineKeyboardMarkup(group_keyboard),
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END 

    elif status == "FAILED_AND_LOCKED":
        await update.message.reply_text(f"❌ 订单识别失败。已锁定 5 小时。", parse_mode=ParseMode.HTML)
        return await points_menu(update, context) 

    elif status == "FAILED":
        await update.message.reply_text(f"❌ 未查询到有效订单信息。剩余机会: {next_attempts} 次。")
        return VIP_ORDER_INPUT
        
    else: 
        await update.message.reply_text("系统忙碌，请稍后再试。")
        return ConversationHandler.END

async def vip_verification_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    db = next(get_db())
    try:
        user = db.query(User).filter(User.telegram_id == user_id).first()
        if user and user.vip_attempts_left < MAX_ATTEMPTS and user.vip_lockout_until is None:
             user.vip_attempts_left = MAX_ATTEMPTS
             db.commit()
             await update.message.reply_text("验证流程已取消，您的尝试次数已重置。")
        else:
             await update.message.reply_text("验证流程已取消。")
    except:
        await update.message.reply_text("验证流程已取消。")
    finally:
        db.close()
    return ConversationHandler.END
    # --- 积分签到 ---
async def points_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: pass 
async def sign_in_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: pass 

# --- 广告/Moontag (广告次数限制) ---
async def activity_center_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    current_views = get_today_ad_views(user_id)
    
    if current_views >= AD_VIEW_LIMIT:
        await query.edit_message_text("🛑 今日观看次数已用尽 (上限 3 次)。请等待每天 00:00 UTC 后重试。", parse_mode=ParseMode.HTML)
        return

    voucher_code = generate_voucher(user_id)
    if not voucher_code:
        await query.edit_message_text("⚠️ 生成活动凭证失败，请稍后再试。"); return

    new_count, points_awarded, new_total = increment_ad_view(user_id)

    if new_count is None:
        await query.edit_message_text("⚠️ 统计观看次数失败，请稍后再试。"); return

    reward_link = f"{MOONTAG_ACTIVITY_URL}?voucher={voucher_code}&user={user_id}"
    
    keyboard = [
        [InlineKeyboardButton("▶️ 观看广告以获得积分", url=reward_link)],
        [InlineKeyboardButton("🔙 返回主菜单", callback_data='main_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    response_text = (f"🌟 <b>活动中心 /hd</b> 🌟\n\n"
                     f"您今日已观看 {current_views} 次。\n")
    
    if new_count == 1: response_text += f"🎁 首次观看成功！获得 <b>{AD_POINTS_TIER[1]}</b> 积分。\n"
    elif new_count == 2: response_text += f"🎁 第二次观看成功！获得 <b>{AD_POINTS_TIER[2]}</b> 积分。\n"
    elif new_count == 3: response_text += f"🎁 第三次观看成功！获得 <b>{points_awarded}</b> 积分 (随机)。\n"
    
    response_text += f"您的总积分为: <b>{new_total}</b>。\n\n"
    response_text += "请点击下方按钮观看广告。观看完毕后请务必返回 Telegram。"
    
    try:
        await query.edit_message_text(text=response_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except Exception:
        await query.message.reply_html(response_text, reply_markup=reply_markup)

# --- 充值流程 (占位函数体) ---
async def recharge_menu_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass 
async def start_wx_recharge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass 
async def wx_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass 
async def start_ali_recharge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass 
async def ali_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def recharge_disabled(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.callback_query.answer("此渠道当前不可用（已使用或锁定）。"); return await recharge_menu_start(update, context)

# --- 兑换流程 ---
async def exchange_menu_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def exchange_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def exchange_view_content(update: Update, context: ContextTypes.DEFAULT_TYPE): pass
async def exchange_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass

# --- 积分明细查询 ---
async def view_point_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass

# --- 管理员后台（用户查看） ---
@admin_only
async def admin_view_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    db = next(get_db())
    try:
        total_users = db.query(User).count()
        page = 1
        if query.data.startswith('admin_user_page_'):
            page = int(query.data.split('_')[2])
        
        offset = (page - 1) * MAX_ITEMS_PER_PAGE
        users = db.query(User).order_by(desc(User.points)).offset(offset).limit(MAX_ITEMS_PER_PAGE).all()
        
        response = f"<b>🔑 用户信息总览 (页: {page})</b>\n\n总用户数: {total_users}\n\n"
        
        for i, user in enumerate(users):
            response += f"--- {i + (page-1)*MAX_ITEMS_PER_PAGE + 1} ---\n"
            response += f"🆔 {user.telegram_id} ({user.username or 'N/A'})\n"
            response += f"💰 积分: {user.points} | VIP 尝试: {user.vip_attempts_left}\n"
            ad_days = db.query(AdViewsTracker).filter(AdViewsTracker.user_telegram_id == user.id).count()
            response += f"🌐 广告天数: {ad_days}\n"
            if user.vip_lockout_until: response += f"🔑 VIP 锁定至: {user.vip_lockout_until.strftime('%H:%M')}\n"
            
        nav_buttons = []
        if page > 1: nav_buttons.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f'admin_user_page_{page-1}'))
        if (page * MAX_ITEMS_PER_PAGE) < total_users: nav_buttons.append(InlineKeyboardButton("下一页 ➡️", callback_data=f'admin_user_page_{page+1}'))
        
        action_buttons = [[InlineKeyboardButton("🔙 返回管理面板", callback_data='admin_panel_main')]]
        keyboard = [nav_buttons] if nav_buttons else []
        keyboard.extend(action_buttons)
        
        await query.edit_message_text(response, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        
    finally: db.close()
    return ConversationHandler.END
    # --- 频道转发逻辑 (占位函数体) ---
async def admin_channel_bind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def bind_start_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def bind_cmd_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def bind_source_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def collect_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def handle_bind_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def view_vouchers_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass 

# --- 频道转发内容发送 (核心逻辑 - 使用 5分钟删除) ---
async def forward_user_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
        
    command = update.message.text.strip().upper()
    user_id = update.effective_user.id
    
    is_locked, _, _ = get_verification_status(user_id)
    if is_locked:
        await update.message.reply_text("⏳ 请先完成身份验证流程或等待锁定时间结束。")
        return

    db = next(get_db())
    try:
        lib_record = db.query(ChannelForwardLibrary).filter(ChannelForwardLibrary.custom_command == command).first()
        
        if not lib_record:
            await update.message.reply_text(f"未找到命令 `{command}`。请检查命令是否正确，或返回主菜单。")
            return
            
        user = db.query(User).filter(User.telegram_id == user_id)
        if not user.first() or user.first().vip_lockout_until: 
             await update.message.reply_text("🔒 请先通过身份验证才能访问内容。")
             return
             
        content_data_str = lib_record.content_data.replace("'", "\"") 
        content_list = ast.literal_eval(content_data_str)
        
        # 5. 分页发送 (每 10 条为一组)
        chunks = [content_list[i:i + 10] for i in range(0, len(content_list), 10)]
        sent_messages_ids = []
        
        for chunk in chunks:
            for item in chunk:
                msg_sent = None
                if item['type'] == 'text':
                    msg_sent = await update.message.reply_text(item['content'], parse_mode=ParseMode.HTML)
                elif item['type'] == 'photo' and item['content'].get('file_id'):
                    msg_sent = await update.message.reply_photo(photo=item['content']['file_id'], caption=item['content'].get('caption', ""), parse_mode=ParseMode.HTML)
                elif item['type'] == 'video' and item['content'].get('file_id'):
                    msg_sent = await update.message.reply_video(video=item['content']['file_id'], caption=item['content'].get('caption', ""), parse_mode=ParseMode.HTML)
                elif item['type'] == 'document' and item['content'].get('file_id'):
                    msg_sent = await update.message.reply_document(document=item['content']['file_id'], caption=item['content'].get('caption', ""), parse_mode=ParseMode.HTML)
                
                if msg_sent: sent_messages_ids.append(msg_sent.message_id)
            await asyncio.sleep(1) 

        # 6. 最终回复和定时删除 (5分钟)
        if sent_messages_ids:
            
            final_msg = await update.message.reply_text(
                "✅ 内容已全部发送完毕。\n\n"
                "⏳ <b>消息将在 5 分钟后自动清理。</b>\n"
                "<strong>请重新获取命令：</strong>购买的无需二次付费即可再次查看。",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➡️ 前往兑换中心 (/dh)", callback_data='exchange_menu')],
                    [InlineKeyboardButton("🔙 返回主菜单", callback_data='main_menu')]
                ])
            )
            
            schedule_message_deletion(update.effective_chat.id, sent_messages_ids, FORWARD_EXPIRY_MINUTES, update, context)
            schedule_message_deletion(update.effective_chat.id, update.message.message_id, FORWARD_EXPIRY_MINUTES, update, context)
            schedule_message_deletion(update.effective_chat.id, final_msg.message_id, FORWARD_EXPIRY_MINUTES, update, context)
            
    finally: db.close()

# --- 商品管理逻辑 (占位，需补全) ---
async def admin_manage_items_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def admin_add_item_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def admin_add_points(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def admin_add_content_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def admin_add_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def admin_item_save_final(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def admin_delete_item_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def admin_execute_delete_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass

# --- admin_command 入口 (显示主菜单) ---
@admin_only
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [
        [InlineKeyboardButton("🖼️ 获取图片 File ID", callback_data='get_file_id')],
        [InlineKeyboardButton("🔑 查看待处理奖励", callback_data='view_vouchers')],
        [InlineKeyboardButton("🗄️ 频道转发库", callback_data='admin_channel_list_1')], 
        [InlineKeyboardButton("📦 积分商品管理", callback_data='admin_item_page_1')], 
        [InlineKeyboardButton("👤 查看用户记录", callback_data='admin_user_page_1')], 
        [InlineKeyboardButton("🚪 退出管理", callback_data='exit_admin')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_html(f"<b>🔑 管理员面板 (ID: {ADMIN_ID})</b>\n\n请选择一个操作：", reply_markup=reply_markup)
    return ConversationHandler.END 
# --- 回调处理回调 (admin_callback_handler) ---
async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    # 权限检查
    admin_actions = ['get_file_id', 'view_vouchers', 'admin_channel_list_', 'bind_new', 'bind_confirm', 'bind_delete_confirm', 'bind_save', 'admin_item_page_', 'admin_user_page_', 'admin_item_delete_confirm_', 'admin_item_add_name', 'admin_item_add_points_retry', 'admin_item_save_final']
    if any(data.startswith(action) for action in admin_actions) and user_id != ADMIN_ID:
        await query.edit_message_text("❌ 权限不足。"); return ConversationHandler.END
    
    # --- 2. 管理后台导航 ---
    if data == 'admin_panel_main' or data == 'exit_admin':
        if data == 'exit_admin':
            await query.edit_message_text("👋 已退出管理面板。"); return ConversationHandler.END
            
        keyboard = [
            [InlineKeyboardButton("🖼️ 获取图片 File ID", callback_data='get_file_id')],
            [InlineKeyboardButton("🔑 查看待处理奖励", callback_data='view_vouchers')],
            [InlineKeyboardButton("🗄️ 频道转发库", callback_data='admin_channel_list_1')], 
            [InlineKeyboardButton("📦 积分商品管理", callback_data='admin_item_page_1')], 
            [InlineKeyboardButton("👤 查看用户记录", callback_data='admin_user_page_1')],
            [InlineKeyboardButton("🚪 退出管理", callback_data='exit_admin')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"<b>🔑 管理员面板 (ID: {ADMIN_ID})</b>\n\n请选择一个操作：", reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        return ConversationHandler.END 

    # --- 3. 导航分发 ---
    elif data == 'view_vouchers': return await view_vouchers_command(query.message, context)
    
    # 频道绑定
    elif data.startswith('admin_channel_list'): return await admin_channel_bind_command(query.message, context)
    elif data == 'bind_new': return await bind_start_new(update, context)
    elif data in ['bind_delete_confirm', 'bind_save', 'bind_delete_execute', 'bind_content_retry', 'bind_cmd_input_retry']: return await handle_bind_callback(update, context)
        
    # 用户查看
    elif data.startswith('admin_user_page_'): return await admin_view_users_command(query, context)

    # 商品管理
    elif data.startswith('admin_item_page_'): return await admin_manage_items_list(update, context)
    elif data == 'admin_item_add_name': return await admin_add_item_name(update, context)
    # ... (其他商品管理回调连接) ...
        
    # 活动/积分/VIP
    elif data == 'activity_center': return await activity_center_handler(update, context)
    elif data == 'points_menu': return await points_menu(update, context)
    elif data == 'sign_in_start': return await sign_in_start(update, context)
    elif data == 'start_vip_verify': return await start_vip_verification(update, context)
    
    # 充值
    elif data == 'recharge_menu': return await recharge_menu_start(update, context)
    elif data == 'recharge_wx_start': return await start_wx_recharge(update, context)
    elif data == 'recharge_ali_start': return await start_ali_recharge(update, context)
    elif data == 'recharge_disabled': await query.answer("此渠道当前不可用（已使用或锁定）。"); return await recharge_menu_start(update, context)
    
    # 兑换
    elif data == 'exchange_menu': return await exchange_menu_start(update, context)
    elif data.startswith('exchange_confirm_'): return await exchange_execute(update, context)
    elif data.startswith('exchange_view_'): return await exchange_view_content(update, context)
    elif data == 'exchange_cancel': return await exchange_cancel(update, context)
    
    # 记录
    elif data.startswith('history_page_'): return await view_point_history(update, context)
        
    # 返回
    elif data == 'main_menu': await query.edit_message_text("已返回主菜单。"); return await start_command(query.message, context)
        
    return ConversationHandler.END


# --- 主运行函数 ---
def main() -> None:
    if not BOT_TOKEN or not DATABASE_URL:
        logger.error("BOT_TOKEN 或 DATABASE_URL 未设置。请检查 Railway 环境变量。")
        return
    
    if not engine:
        logger.critical("数据库初始化失败，无法继续。")
        return

    init_db()
    
    application = Application.builder().token(BOT_TOKEN).build()

    # 1. 注册 CommandHandlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("admin", admin_command)) 
    application.add_handler(CommandHandler("id", get_file_id_start))
    application.add_handler(CommandHandler("bind", admin_channel_bind_command)) 
    application.add_handler(CommandHandler("jf", points_menu)) 
    application.add_handler(CommandHandler("dh", exchange_menu_start))
    
    # 2. 注册 ConversationHandler (此处需确保所有入口函数和状态处理函数都已定义)
    # ... (所有 ConversationHandler 的定义，必须完整) ...
    
    # 3. 注册回调查询处理器
    application.add_handler(CallbackQueryHandler(admin_callback_handler))

    logger.info("机器人启动中...")
    # ✅ 关键修复：使用 post_init 来启动 Scheduler
    application.run_polling(allowed_updates=Update.ALL_TYPES, post_init=post_init_hook)

# ‼️ 新增: Scheduler 启动钩子函数
async def post_init_hook(application: Application) -> None:
    if not scheduler.running:
        scheduler.start()
        logger.info("APScheduler 已启动，定时任务已准备就绪。")

if __name__ == '__main__':
    main()
