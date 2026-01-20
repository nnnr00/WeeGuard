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

# ‼️ APScheduler 导入 (用于可靠的定时任务)
from apscheduler.schedulers.asyncio import AsyncIOScheduler 

# --- 配置 & 状态定义 (请替换为您自己的值) ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

try: ADMIN_ID = int(os.getenv("ADMIN_ID"))
except: ADMIN_ID = None

# --- 基础配置 ---
MOONTAG_ACTIVITY_URL = "https://your-external-host.com/activity.html" # ‼️ 替换为您的活动页面URL
VOUCHER_EXPIRY_SECONDS = 3600 * 6 
AD_VIEW_LIMIT = 3
AD_POINTS_TIER = {1: 10, 2: 6, 3: (3, 10)} 

# --- VIP 验证配置 ---
MAX_ATTEMPTS = 2
LOCKOUT_HOURS = 5
ORDER_PREFIX = "20260" 

# --- 频道转发配置 ---
MAX_ITEMS_PER_PAGE = 10
MAX_CONTENT_ITEMS = 100
FORWARD_EXPIRY_MINUTES = 5 # ‼️ 频道转发消息的自动删除时间 (5分钟)

# --- 充值配置 ---
RECHARGE_AMOUNT = 5 
RECHARGE_POINTS = 100 
RECHARGE_ATTEMPTS = 2
RECHARGE_LOCKOUT_HOURS = 5 

# --- 积分兑换配置 ---
TEST_EXCHANGE_ITEM_NAME = "🎁 零积分测试礼包"
TEST_EXCHANGE_COST = 0

# --- 状态定义 (ConversationHandler) ---
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
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    # ‼️ 修复此处：直接使用 Column(Integer, primary_key=True) 来定义自增主键
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
    id = Column(Base.metadata.tables.primary_key[0].type, primary_key=True)
    voucher_id = Column(String, unique=True, nullable=False) 
    user_telegram_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    is_used = Column(Boolean, default=False)
    __table_args__ = (UniqueConstraint('voucher_id', name='uix_voucher_id'),)

class DailySignIn(Base):
    __tablename__ = 'daily_sign_in'
    id = Column(Base.metadata.tables.primary_key[0].type, primary_key=True)
    user_telegram_id = Column(Integer, nullable=False, unique=True)
    last_signed_in_date = Column(DateTime, nullable=False)

class AdViewsTracker(Base):
    __tablename__ = 'ad_views_tracker'
    id = Column(Base.metadata.tables.primary_key[0].type, primary_key=True)
    user_telegram_id = Column(Integer, nullable=False)
    view_date = Column(DateTime, nullable=False)
    views_count = Column(Integer, default=0)
    __table_args__ = (UniqueConstraint('user_telegram_id', 'view_date', name='uix_ad_view_daily'),)

class ChannelForwardLibrary(Base):
    __tablename__ = 'channel_forward_library'
    id = Column(Base.metadata.tables.primary_key[0].type, primary_key=True)
    custom_command = Column(String(50), nullable=False, unique=True) 
    source_chat_id = Column(String, nullable=False) 
    content_data = Column(String, nullable=False) 
    created_at = Column(DateTime, default=datetime.utcnow)

class PointExchangeItem(Base):
    __tablename__ = 'point_exchange_item'
    id = Column(Base.metadata.tables.primary_key[0].type, primary_key=True)
    item_name = Column(String(100), nullable=False)
    cost = Column(Integer, default=0)
    content_data = Column(String, nullable=False) 
    is_available = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class PointTransaction(Base):
    __tablename__ = 'point_transaction'
    id = Column(Base.metadata.tables.primary_key[0].type, primary_key=True)
    user_telegram_id = Column(Integer, nullable=False)
    item_id = Column(Integer, nullable=False) 
    transaction_time = Column(DateTime, default=datetime.utcnow)
    points_spent = Column(Integer, default=0) 
    is_successful = Column(Boolean, default=False)
    content_delivered = Column(String, nullable=True) 

class UserAccess(Base):
    __tablename__ = 'user_access'
    id = Column(Base.metadata.tables.primary_key[0].type, primary_key=True)
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
        Base.metadata.create_all(bind=engine)
        logger.info("数据库表已初始化。")

def get_db():
    if SessionLocal:
        db = SessionLocal()
        try: yield db
        finally: db.close()

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

# --- APScheduler 调度函数 ---
scheduler = AsyncIOScheduler()

def schedule_message_deletion(chat_id, message_ids, delay_minutes, update, context):
    if not scheduler.running:
        logger.warning("Scheduler 未运行，无法调度删除任务。")
        return
        
    run_time = datetime.utcnow() + timedelta(minutes=delay_minutes)
    
    # 由于 context 不能直接被调度，我们传递所需的参数
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
        
        # --- 菜单按钮 ---
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

# --- 积分签到 / 广告/充值/兑换/频道/FileID/Admin 占位函数 (需在上文补全) ---
async def points_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: pass 
async def sign_in_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: pass 
async def activity_center_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: pass 
async def recharge_menu_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass 
async def start_wx_recharge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass 
async def wx_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def start_ali_recharge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def ali_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def exchange_menu_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass 
async def exchange_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def exchange_view_content(update: Update, context: ContextTypes.DEFAULT_TYPE): pass
async def exchange_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def view_point_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def admin_view_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def admin_channel_bind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def view_vouchers_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def admin_manage_items_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
# --- 积分签到 / 充值流程 (占位函数体，需补全) ---
async def points_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: pass 
async def sign_in_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: pass 

async def recharge_menu_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    current_points = get_user_points(user_id)
    
    db = next(get_db())
    try:
        user = db.query(User).filter(User.telegram_id == user_id).first()
        wx_locked = user.wx_lockout_until is not None and user.wx_lockout_until > datetime.utcnow()
        ali_locked = user.zhifubao_lockout_until is not None and user.zhifubao_lockout_until > datetime.utcnow()
        
        wx_btn_text = "💳 微信充值 (¥5=100P)"
        if user.wx_recharge_used or wx_locked: wx_btn_text = "🚫 微信已用/锁定"
            
        ali_btn_text = "💳 支付宝充值 (¥5=100P)"
        if user.zhifubao_recharge_used or ali_locked: ali_btn_text = "🚫 支付宝已用/锁定"
            
    finally: db.close()
        
    keyboard = [
        [InlineKeyboardButton(wx_btn_text, callback_data='recharge_wx_start' if not user.wx_recharge_used and not wx_locked else 'recharge_disabled')],
        [InlineKeyboardButton(ali_btn_text, callback_data='recharge_ali_start' if not user.zhifubao_recharge_used and not ali_locked else 'recharge_disabled')],
        [InlineKeyboardButton("🔙 返回积分主菜单", callback_data='points_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    response_text = (f"💎 <b>积分充值中心</b>\n\n当前积分: <b>{current_points}</b>\n\n--- 充值选项 (每笔 ¥{RECHARGE_AMOUNT} = {RECHARGE_POINTS} 积分) ---")
    
    await query.edit_message_text(text=response_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    return RECHARGE_MENU

async def start_wx_recharge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    used, locked, attempts, user = get_recharge_status(user_id, 'WX')
    if used or locked:
        await query.edit_message_text("此渠道已使用或当前处于锁定状态，请返回积分菜单。");
        return await recharge_menu_start(update, context)

    FILE_ID_WX_STEP1 = "AgACAgQAAxkb..." # ‼️ 替换为微信支付二维码 File ID
    
    keyboard = [
        [InlineKeyboardButton("✅ 我已支付，开始验证订单号", callback_data='recharge_wx_input_start')],
        [InlineKeyboardButton("🔙 返回充值菜单", callback_data='recharge_menu')]
    ]
    
    warning_text = ("⚠️ <b>【微信充值】温馨提示</b>\n"
                    "此渠道 <b>仅限使用一次</b>。失败两次后，此按钮将锁定 5 小时。")
    info_text = f"请向商家支付 <b>¥{RECHARGE_AMOUNT}</b>，然后点击下方按钮进行订单验证。"
    
    await query.edit_message_text(
        f"{warning_text}\n\n{info_text}\n\n<b>【请扫描下方二维码】</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )
    await query.message.reply_photo(photo=FILE_ID_WX_STEP1)
    return RECHARGE_WX_INPUT

async def wx_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    order_input = update.message.text.strip()
    user_id = update.effective_user.id
    
    status, next_attempts, _ = process_recharge_attempt(user_id, order_input, 'WX')
    
    if status == "SUCCESS":
        await update.message.reply_text(f"✅ 支付成功！已为您增加 <b>{RECHARGE_POINTS}</b> 积分。", parse_mode=ParseMode.HTML)
        return await points_menu(update, context) 

    elif status == "FAILED_AND_LOCKED":
        await update.message.reply_text(f"❌ 订单识别失败。已锁定 5 小时。", parse_mode=ParseMode.HTML)
        return await points_menu(update, context)

    elif status == "FAILED":
        await update.message.reply_text(f"❌ 订单识别失败。请在微信支付的账单找到交易单号。\n剩余机会: {next_attempts} 次。")
        return RECHARGE_WX_INPUT
        
    else: 
        await update.message.reply_text("系统忙碌或您已完成该支付方式的充值。")
        return await points_menu(update, context)

async def start_ali_recharge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    used, locked, attempts, user = get_recharge_status(user_id, 'ALI')
    if used or locked:
        await query.edit_message_text("此渠道已使用或当前处于锁定状态，请返回积分菜单。");
        return await recharge_menu_start(update, context)
        
    FILE_ID_ALI_STEP1 = "AgACAgQAAxkb..." # ‼️ 替换为支付宝支付二维码 File ID
    
    keyboard = [
        [InlineKeyboardButton("✅ 我已支付，开始验证订单号", callback_data='recharge_ali_input_start')],
        [InlineKeyboardButton("🔙 返回充值菜单", callback_data='recharge_menu')]
    ]
    
    warning_text = ("⚠️ <b>【支付宝充值】温馨提示</b>\n"
                    "此渠道 <b>仅限使用一次</b>。失败两次后，此按钮将锁定 5 小时。")
    info_text = f"请向商家支付 <b>¥{RECHARGE_AMOUNT}</b>，然后点击下方按钮进行订单验证。"
    
    await query.edit_message_text(
        f"{warning_text}\n\n{info_text}\n\n<b>【请扫描下方二维码】</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )
    await query.message.reply_photo(photo=FILE_ID_ALI_STEP1)
    return RECHARGE_ALI_INPUT

async def ali_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    order_input = update.message.text.strip()
    user_id = update.effective_user.id
    
    status, next_attempts, _ = process_recharge_attempt(user_id, order_input, 'ALI')
    
    if status == "SUCCESS":
        await update.message.reply_text(f"✅ 支付成功！已为您增加 <b>{RECHARGE_POINTS}</b> 积分。", parse_mode=ParseMode.HTML)
        return await points_menu(update, context) 

    elif status == "FAILED_AND_LOCKED":
        await update.message.reply_text(f"❌ 订单识别失败。已锁定 5 小时。", parse_mode=ParseMode.HTML)
        return await points_menu(update, context)

    elif status == "FAILED":
        await update.message.reply_text(f"❌ 订单识别失败。请在我的账单详情中找到商家订单号。\n剩余机会: {next_attempts} 次。")
        return RECHARGE_ALI_INPUT
        
    else: 
        await update.message.reply_text("系统忙碌或您已完成该支付方式的充值。")
        return await points_menu(update, context)
        # --- 兑换流程 (/dh) ---
async def exchange_menu_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    current_points = get_user_points(user_id)
    
    db = next(get_db())
    try:
        user = db.query(User).filter(User.telegram_id == user_id).first()
        is_vip_locked, _, _ = get_verification_status(user_id)
        if is_locked:
            await query.edit_message_text(f"⏳ 兑换功能受限，请先完成VIP身份验证或等待锁定时间后重试。"); return ConversationHandler.END

        items = db.query(PointExchangeItem).filter(PointExchangeItem.is_available == True).order_by(PointExchangeItem.cost.asc()).all()
        
        test_item = db.query(PointExchangeItem).filter(PointExchangeItem.item_name == TEST_EXCHANGE_ITEM_NAME).first()
        if not test_item:
            test_item = PointExchangeItem(item_name=TEST_EXCHANGE_ITEM_NAME, cost=TEST_EXCHANGE_COST, content_data="哈哈", is_available=True)
            db.add(test_item)
            db.commit()
            items = db.query(PointExchangeItem).filter(PointExchangeItem.is_available == True).order_by(PointExchangeItem.cost.asc()).all()
            
    except Exception as e:
        logger.error(f"加载兑换菜单出错: {e}")
        await query.edit_message_text("兑换系统加载失败，请稍后再试。"); return await points_menu(update, context)
    finally: db.close()

    keyboard = []
    for item in items:
        is_claimed = False
        if item.item_name == TEST_EXCHANGE_ITEM_NAME and item.cost == TEST_EXCHANGE_COST:
            tx_db = next(get_db())
            try:
                already_claimed = tx_db.query(PointTransaction).filter(PointTransaction.user_telegram_id == user_id, PointTransaction.item_id == item.id, PointTransaction.is_successful == True).first()
                if already_claimed: is_claimed = True
            finally: tx_db.close()

        if is_claimed:
            btn_text = f"🎁 {item.item_name} (已兑换)"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f'exchange_view_{item.id}')])
        elif item.cost > current_points:
            btn_text = f"🔒 {item.item_name} ({item.cost} P)"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data='exchange_insufficient')])
        else:
            btn_text = f"✨ {item.item_name} ({item.cost} P)"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f'exchange_confirm_{item.id}')])

    keyboard.append([InlineKeyboardButton("📜 查看积分明细", callback_data='history_page_1')])
    keyboard.append([InlineKeyboardButton("🔙 返回积分主菜单", callback_data='points_menu')])
    
    await query.edit_message_text(
        f"💎 <b>兑换中心 /dh</b>\n\n当前积分: <b>{current_points}</b> P\n\n请选择您想兑换的商品：",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )
    return EXCHANGE_CMD_START 

async def exchange_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    item_id = int(query.data.split('_')[2])
    
    db = next(get_db())
    try:
        item = db.query(PointExchangeItem).filter(PointExchangeItem.id == item_id, PointExchangeItem.is_available == True).first()
        user = db.query(User).filter(User.telegram_id == user_id).first()
        
        if not user or not item:
            await query.edit_message_text("系统错误：用户或商品不存在。"); return await exchange_menu_start(update, context)

        if user.points < item.cost:
            await query.edit_message_text(f"❌ 余额不足！请重试。")
            return await exchange_menu_start(update, context)

        user.points -= item.cost
        transaction = PointTransaction(
            user_telegram_id=user_id, item_id=item_id, points_spent=-item.cost, is_successful=True, content_delivered=item.content_data 
        )
        db.add(transaction)
        db.commit()
        
        if item.content_data:
            if item.item_name == TEST_EXCHANGE_ITEM_NAME:
                 await query.message.reply_text(f"🎁 兑换成功！您获得了测试礼包内容：{item.content_data}", parse_mode=ParseMode.HTML)
            else:
                 await query.message.reply_text(f"✨ 兑换成功！您获得了商品：{item.item_name}。内容已发送给您。")

        await query.edit_message_text(f"🎉 兑换 '{item.item_name}' 成功！已扣除 {item.cost} 积分。")
        return await exchange_menu_start(update, context)

    except Exception as e:
        db.rollback()
        logger.error(f"兑换执行失败: {e}")
        await query.edit_message_text("❌ 兑换处理中发生内部错误，请重试。")
        return await exchange_menu_start(update, context)
    finally:
        db.close()

async def exchange_view_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    item_id = int(query.data.split('_')[2])
    user_id = query.from_user.id
    
    db = next(get_db())
    try:
        item = db.query(PointExchangeItem).filter(PointExchangeItem.id == item_id).first()
        if not item:
            await query.edit_message_text("商品信息已丢失。"); return await exchange_menu_start(update, context)
            
        if item.item_name == TEST_EXCHANGE_ITEM_NAME:
            content = item.content_data
            await query.edit_message_text(
                f"🎁 您已兑换 <b>{item.item_name}</b>。\n\n内容: <code>{content}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回兑换列表", callback_data='exchange_menu')]])
            )
        else:
            await query.edit_message_text(
                f"✨ 您已兑换 <b>{item.item_name}</b>。请稍后查看您的私聊或购买记录。",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回兑换列表", callback_data='exchange_menu')]])
            )
    finally: db.close()
    return EXCHANGE_CMD_START

async def exchange_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.edit_message_text("操作已取消。")
    return await exchange_menu_start(update, context) 

# --- 积分明细查询 (新增) ---
async def view_point_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    current_points = get_user_points(user_id)
    
    page = 1
    if query.data.startswith('history_page_'):
        page = int(query.data.split('_')[2])
        
    total_tx, transactions = get_user_transactions(user_id, page)
    response = f"📊 <b>积分明细 (页: {page})</b>\n\n总积分: <b>{current_points}</b>\n总记录: {total_tx}\n\n"
    
    if not transactions:
        response += "暂无交易记录。"
    else:
        for i, tx in enumerate(transactions):
            item_name = "未知"
            if tx.item_id:
                db = next(get_db())
                try:
                    item = db.query(PointExchangeItem).filter(PointExchangeItem.id == tx.item_id).first()
                    item_name = item.item_name if item else "已删除商品"
                finally: db.close()
            elif tx.points_spent < 0 and tx.content_delivered is None: item_name = "广告观看奖励"
            elif tx.points_spent == 0: item_name = "签到奖励"
            
            response += f"--- {i + (page-1)*MAX_ITEMS_PER_PAGE + 1} ---\n"
            response += f"时间: {tx.transaction_time.strftime('%m/%d %H:%M')}\n"
            response += f"操作: {item_name} ({tx.points_spent} P)\n"
            
    nav_buttons = []
    if page > 1: nav_buttons.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f'history_page_{page-1}'))
    if (page * MAX_ITEMS_PER_PAGE) < total_tx: nav_buttons.append(InlineKeyboardButton("下一页 ➡️", callback_data=f'history_page_{page+1}'))
        
    action_buttons = [[InlineKeyboardButton("🔙 返回积分主菜单", callback_data='points_menu')]]
    keyboard = [nav_buttons] if nav_buttons else []
    keyboard.extend(action_buttons)

    await query.edit_message_text(response, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    return ConversationHandler.END
    # --- 充值流程实现 (主体逻辑) ---
async def recharge_menu_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    current_points = get_user_points(user_id)
    
    db = next(get_db())
    try:
        user = db.query(User).filter(User.telegram_id == user_id).first()
        wx_locked = user.wx_lockout_until is not None and user.wx_lockout_until > datetime.utcnow()
        ali_locked = user.zhifubao_lockout_until is not None and user.zhifubao_lockout_until > datetime.utcnow()
        
        wx_btn_text = "💳 微信充值 (¥5=100P)"
        if user.wx_recharge_used or wx_locked: wx_btn_text = "🚫 微信已用/锁定"
            
        ali_btn_text = "💳 支付宝充值 (¥5=100P)"
        if user.zhifubao_recharge_used or ali_locked: ali_btn_text = "🚫 支付宝已用/锁定"
            
    finally: db.close()
        
    keyboard = [
        [InlineKeyboardButton(wx_btn_text, callback_data='recharge_wx_start' if not user.wx_recharge_used and not wx_locked else 'recharge_disabled')],
        [InlineKeyboardButton(ali_btn_text, callback_data='recharge_ali_start' if not user.zhifubao_recharge_used and not ali_locked else 'recharge_disabled')],
        [InlineKeyboardButton("🔙 返回积分主菜单", callback_data='points_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    response_text = (f"💎 <b>积分充值中心</b>\n\n当前积分: <b>{current_points}</b>\n\n--- 充值选项 (每笔 ¥{RECHARGE_AMOUNT} = {RECHARGE_POINTS} 积分) ---")
    
    await query.edit_message_text(text=response_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    return RECHARGE_MENU

async def start_wx_recharge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    used, locked, attempts, user = get_recharge_status(user_id, 'WX')
    if used or locked:
        await query.edit_message_text("此渠道已使用或当前处于锁定状态，请返回积分菜单。");
        return await recharge_menu_start(update, context)

    FILE_ID_WX_STEP1 = "AgACAgQAAxkb..." # ‼️ 替换为微信支付二维码 File ID
    
    keyboard = [
        [InlineKeyboardButton("✅ 我已支付，开始验证订单号", callback_data='recharge_wx_input_start')],
        [InlineKeyboardButton("🔙 返回充值菜单", callback_data='recharge_menu')]
    ]
    
    warning_text = ("⚠️ <b>【微信充值】温馨提示</b>\n"
                    "此渠道 <b>仅限使用一次</b>。失败两次后，此按钮将锁定 5 小时。")
    info_text = f"请向商家支付 <b>¥{RECHARGE_AMOUNT}</b>，然后点击下方按钮进行订单验证。"
    
    await query.edit_message_text(
        f"{warning_text}\n\n{info_text}\n\n<b>【请扫描下方二维码】</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )
    await query.message.reply_photo(photo=FILE_ID_WX_STEP1)
    return RECHARGE_WX_INPUT

async def wx_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    order_input = update.message.text.strip()
    user_id = update.effective_user.id
    
    status, next_attempts, _ = process_recharge_attempt(user_id, order_input, 'WX')
    
    if status == "SUCCESS":
        await update.message.reply_text(f"✅ 支付成功！已为您增加 <b>{RECHARGE_POINTS}</b> 积分。", parse_mode=ParseMode.HTML)
        return await points_menu(update, context) 

    elif status == "FAILED_AND_LOCKED":
        await update.message.reply_text(f"❌ 订单识别失败。已锁定 5 小时。", parse_mode=ParseMode.HTML)
        return await points_menu(update, context)

    elif status == "FAILED":
        await update.message.reply_text(f"❌ 订单识别失败。请在微信支付的账单找到交易单号。\n剩余机会: {next_attempts} 次。")
        return RECHARGE_WX_INPUT
        
    else: 
        await update.message.reply_text("系统忙碌或您已完成该支付方式的充值。")
        return await points_menu(update, context)

async def start_ali_recharge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    used, locked, attempts, user = get_recharge_status(user_id, 'ALI')
    if used or locked:
        await query.edit_message_text("此渠道已使用或当前处于锁定状态，请返回积分菜单。");
        return await recharge_menu_start(update, context)
        
    FILE_ID_ALI_STEP1 = "AgACAgQAAxkb..." # ‼️ 替换为支付宝支付二维码 File ID
    
    keyboard = [
        [InlineKeyboardButton("✅ 我已支付，开始验证订单号", callback_data='recharge_ali_input_start')],
        [InlineKeyboardButton("🔙 返回充值菜单", callback_data='recharge_menu')]
    ]
    
    warning_text = ("⚠️ <b>【支付宝充值】温馨提示</b>\n"
                    "此渠道 <b>仅限使用一次</b>。失败两次后，此按钮将锁定 5 小时。")
    info_text = f"请向商家支付 <b>¥{RECHARGE_AMOUNT}</b>，然后点击下方按钮进行订单验证。"
    
    await query.edit_message_text(
        f"{warning_text}\n\n{info_text}\n\n<b>【请扫描下方二维码】</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )
    await query.message.reply_photo(photo=FILE_ID_ALI_STEP1)
    return RECHARGE_ALI_INPUT

async def ali_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    order_input = update.message.text.strip()
    user_id = update.effective_user.id
    
    status, next_attempts, _ = process_recharge_attempt(user_id, order_input, 'ALI')
    
    if status == "SUCCESS":
        await update.message.reply_text(f"✅ 支付成功！已为您增加 <b>{RECHARGE_POINTS}</b> 积分。", parse_mode=ParseMode.HTML)
        return await points_menu(update, context) 

    elif status == "FAILED_AND_LOCKED":
        await update.message.reply_text(f"❌ 订单识别失败。已锁定 5 小时。", parse_mode=ParseMode.HTML)
        return await points_menu(update, context)

    elif status == "FAILED":
        await update.message.reply_text(f"❌ 订单识别失败。请在我的账单详情中找到商家订单号。\n剩余机会: {next_attempts} 次。")
        return RECHARGE_ALI_INPUT
        
    else: 
        await update.message.reply_text("系统忙碌或您已完成该支付方式的充值。")
        return await points_menu(update, context)

# --- 兑换和记录查询 (占位，请保留上一个版本逻辑) ---
async def exchange_menu_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def exchange_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def exchange_view_content(update: Update, context: ContextTypes.DEFAULT_TYPE): pass
async def exchange_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
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
    # --- 频道转发逻辑 (占位函数) ---
async def admin_channel_bind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def bind_start_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def bind_cmd_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def bind_source_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def collect_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def handle_bind_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass

# --- 频道转发内容发送 (核心修改：5分钟删除，跳转/dh) ---
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
            
        user = db.query(User).filter(User.telegram_id == user_id).first()
        if not user or user.vip_lockout_until: 
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

# --- 商品管理逻辑 (占位，需要完整函数体) ---
async def admin_manage_items_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def admin_add_item_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def admin_add_points(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def admin_add_content_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def admin_add_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def admin_item_save_final(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def admin_delete_item_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
async def admin_execute_delete_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: pass
    # --- 主运行函数 ---
def main() -> None:
    if not BOT_TOKEN or not DATABASE_URL:
        logger.error("BOT_TOKEN 或 DATABASE_URL 未设置。请检查 Railway 环境变量。")
        return
    
    if not engine:
        logger.critical("数据库初始化失败，无法继续。")
        return

    init_db()
    
    # ‼️ 启动 APScheduler
    scheduler.start()
    logger.info("APScheduler 已启动，定时任务已准备就绪。")

    application = Application.builder().token(BOT_TOKEN).build()

    # 1. 注册基础命令
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("jf", points_menu)) 
    application.add_handler(CommandHandler("dh", exchange_menu_start)) 
    application.add_handler(CommandHandler("admin", admin_channel_bind_command)) 
    application.add_handler(CommandHandler("id", get_file_id_start))
    application.add_handler(CommandHandler("bind", admin_channel_bind_command))
    
    # 2. 注册所有 ConversationHandler (需要确保所有入口和状态的定义都完整)
    # ... (所有 ConversationHandler 的定义，如 File ID, VIP, 绑定, 充值, 兑换, 商品添加) ...
    
    # 3. 注册回调查询处理器 (处理所有按钮点击)
    application.add_handler(CallbackQueryHandler(admin_callback_handler))

    logger.info("机器人启动中...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
