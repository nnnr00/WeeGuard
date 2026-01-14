import os
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from telegram import (
    InlineKeyboardMarkup, InlineKeyboardButton, Update
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)
from models import Base, User, Reward, RewardCode

# ========== 配置 ==========
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_IDS = [123456789]  # 替换为你的 TG ID
GROUP_LINK = "https://t.me/+495j5rWmApsxYzg9"

VIP_SERVICE_IMAGE_URL = "https://i.postimg.cc/QtkVBw7N/photo-2026-01-13-17-04-27.jpg" # VIP特权图片
TUTORIAL_IMAGE_URL = "https://i.postimg.cc/zBYtqtKb/photo-2026-01-13-17-04-32.jpg" # 订单号查找教程图片

engine = create_engine("sqlite:///db.sqlite3")
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# 状态用于对话控制
AWAIT_ORDER = range(1)
TEMP_REWARD = {}
ADMIN_TEMP = {}
# bot.py - Part 2：签到 / 查询积分 / 兑换菜单

# ✅ 签到功能
async def handle_signin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    session = Session()
    user = session.query(User).filter_by(user_id=user_id).first()

    if not user:
        user = User(user_id=user_id, points=0)
        session.add(user)

    now = datetime.utcnow()
    if user.last_signin and user.last_signin.date() == now.date():
        text = "✅ 你今天已经签到过啦~"
    else:
        gain = 5
        user.points += gain
        user.last_signin = now
        session.commit()
        text = f"🎉 签到成功！获得 {gain} 积分。\n🎯 当前积分：{user.points}"

    session.close()
    await query.message.reply_text(text)

# ✅ 积分查询
async def handle_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    session = Session()
    user = session.query(User).filter_by(user_id=user_id).first()
    points = user.points if user else 0
    session.close()

    await query.message.reply_text(f"💰 当前积分：{points}")

# ✅ 兑换菜单
async def cart_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await query.answer()

        keyboard = [
            [InlineKeyboardButton("✅ 签到", callback_data="signin"),
             InlineKeyboardButton("💰 查询积分", callback_data="points")],
            [InlineKeyboardButton("🎁 奖品兑换", callback_data="rewards")],
            [InlineKeyboardButton("🏆 查看排行榜", callback_data="rank_menu")],
            [InlineKeyboardButton("🔙 返回首页", callback_data="restart")]
        ]
        await query.message.reply_text(
            "🎉 *小卫积分中心菜单*\n请选择操作：",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

elif query.data == "rank_menu":
    keyboard = [
        [
            InlineKeyboardButton("📊 查看排行榜", callback_data="show_rank_back")
        ],
        [
            InlineKeyboardButton("🔙 返回菜单", callback_data="cart_menu")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.reply_text("🏆 排行榜：", reply_markup=reply_markup)
# 🎁 展示奖品
async def show_rewards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    session = Session()
    rewards = session.query(Reward).all()
    if not rewards:
        await query.message.reply_text("暂无奖品，快通知管理员上架吧~")
        session.close()
        return

    text = "🎁 *可兑换奖品列表：*\n\n"
    keyboard = []

    for r in rewards:
        text += f"{r.id}. {r.title}（{r.cost}积分）\n"
        text += f"_{r.description}_\n\n"
        keyboard.append([
            InlineKeyboardButton(f"兑换 {r.title}", callback_data=f"redeem_{r.id}")
        ])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    session.close()

# 🎁 兑换奖品处理（发放卡密）
async def handle_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    reward_id = query.data.split("_")[1]

    session = Session()
    user = session.query(User).filter_by(user_id=user_id).first()
    reward = session.query(Reward).filter_by(id=reward_id).first()

    if not reward:
        await query.message.reply_text("❌ 奖品不存在。")
        session.close()
        return

    if user.points < reward.cost:
        await query.message.reply_text(f"❌ 积分不足！\n🎫 需要：{reward.cost}分，你当前有：{user.points}分")
        session.close()
        return

    # 积分抵扣
    user.points -= reward.cost

    # 发奖逻辑（找卡密库存）
    code = session.query(RewardCode).filter_by(reward_id=reward_id, is_used=0).first()
    if code:
        code.is_used = 1
        code.used_by = user_id
        code_text = f"🎁 奖品兑换成功！\n📦 奖励码：`{code.code}`"
    else:
        code_text = "🎁 奖品兑换成功（无奖励码）\n请稍后联系管理员发放。"

    session.commit()
    session.close()

    await query.message.reply_text(code_text, parse_mode="Markdown")
    # bot.py - Part 4：排行榜 + 管理员后台 + 卡密录入

# 🏆 查看排行榜按钮
async def show_rank_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    session = Session()
    user_id = query.from_user.id
    users = session.query(User).order_by(User.points.desc()).limit(10).all()
    all_users = session.query(User).order_by(User.points.desc()).all()

    text = "🏆 *小卫排行榜 Top 10*\n\n"
    icons = ['🥇', '🥈', '🥉']
    for idx, u in enumerate(users, 1):
        icon = icons[idx - 1] if idx <= 3 else f"{idx}."
        try:
            name = (await context.bot.get_chat(u.user_id)).first_name
        except:
            name = "匿名用户"
        text += f"{icon} {name} - {u.points}积分\n"

    rank_num = next((i + 1 for i, u in enumerate(all_users) if u.user_id == user_id), None)
    user_points = next((u.points for u in all_users if u.user_id == user_id), 0)

    if rank_num > 10:
        text += f"\n👤 你当前排名：#{rank_num}，积分：{user_points}分"

    session.close()
    await query.message.reply_text(text, parse_mode='Markdown')

# 👑 管理员奖品控制
async def admin_reward_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return await update.message.reply_text("❌ 你不是管理员！")
    
    keyboard = [
        [InlineKeyboardButton("➕ 添加奖品", callback_data="add_reward")],
        [InlineKeyboardButton("🗑 删除奖品", callback_data="delete_reward")],
        [InlineKeyboardButton("📦 查看奖品", callback_data="list_rewards")],
    ]
    await update.message.reply_text("👑 小卫奖品管理中心", reply_markup=InlineKeyboardMarkup(keyboard))

# 管理奖品逻辑
ADD_ID, ADD_TITLE, ADD_DESC, ADD_COST = range(4)

async def add_reward_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.message.reply_text("请输入奖品 ID（例：001）:")
    return ADD_ID

async def input_reward_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    TEMP_REWARD['id'] = update.message.text
    await update.message.reply_text("请输入奖品名称：")
    return ADD_TITLE

async def input_reward_title(update, context):
    TEMP_REWARD['title'] = update.message.text
    await update.message.reply_text("请输入奖品描述：")
    return ADD_DESC

async def input_reward_desc(update, context):
    TEMP_REWARD['desc'] = update.message.text
    await update.message.reply_text("请输入所需积分（整数）：")
    return ADD_COST

async def input_reward_cost(update, context):
    try:
        TEMP_REWARD['cost'] = int(update.message.text.strip())
        session = Session()
        reward = Reward(
            id=TEMP_REWARD['id'],
            title=TEMP_REWARD['title'],
            description=TEMP_REWARD['desc'],
            cost=TEMP_REWARD['cost']
        )
        session.add(reward)
        session.commit()
        session.close()
        await update.message.reply_text("✅ 奖品添加成功")
    except:
        await update.message.reply_text("❌ 输入错误，请重新开始")
    TEMP_REWARD.clear()
    return ConversationHandler.END

# 查看奖品
async def list_rewards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = Session()
    rewards = session.query(Reward).all()
    if not rewards:
        await update.message.reply_text("⛔ 目前无奖品")
    else:
        text = "📦 当前奖品列表：\n\n"
        for r in rewards:
            text += f"{r.id} - {r.title}（{r.cost}积分）\n"
        await update.message.reply_text(text)
    session.close()

# 删除奖品
async def delete_reward_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = Session()
    rewards = session.query(Reward).all()
    if not rewards:
        await update.message.reply_text("⛔ 没有奖品可以删除")
        session.close()
        return
    buttons = [[InlineKeyboardButton(f"{r.title}（{r.id}）", callback_data=f"del_{r.id}")] for r in rewards]
    await update.message.reply_text("请选择要删除的奖品：", reply_markup=InlineKeyboardMarkup(buttons))
    session.close()

async def confirm_delete_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    rid = query.data.split("_")[1]

    session = Session()
    reward = session.query(Reward).filter_by(id=rid).first()
    if reward:
        session.delete(reward)
        session.commit()
        await query.message.reply_text(f"✅ 已删除奖品：{reward.title}")
    else:
        await query.message.reply_text("❌ 奖品不存在")
    session.close()
    # bot.py - Part 5：导入卡密 + 回调注册 + 启动主程序

# ✅ 管理员导入卡密 / 兑换码
async def add_codes_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return await update.message.reply_text("❌ 你没有权限")

    if len(context.args) != 1:
        return await update.message.reply_text("用法：/add_codes 奖品ID")

    reward_id = context.args[0]
    ADMIN_TEMP[update.effective_user.id] = reward_id
    await update.message.reply_text(f"📥 请发送【一行一个】的卡密内容，每条一行。它们将绑定到奖品 ID `{reward_id}`", parse_mode='Markdown')

async def receive_codes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_TEMP:
        return  # 忽略非导入流程的用户输入

    reward_id = ADMIN_TEMP[user_id]
    lines = update.message.text.splitlines()

    session = Session()
    count = 0
    for line in lines:
        line = line.strip()
        if line:
            session.add(RewardCode(reward_id=reward_id, code=line))
            count += 1
    session.commit()
    session.close()

    await update.message.reply_text(f"✅ 成功导入 {count} 条奖励码到奖品 {reward_id}")
    del ADMIN_TEMP[user_id]

# ========== 启动 main() ==========

def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("请将 BOT_TOKEN 替换为你的机器人 Token 或设置环境变量。")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    # ✅ 注册命令
    app.add_handler(CommandHandler("start", cart_menu_callback))
    app.add_handler(CommandHandler("admin_rewards", admin_reward_menu))
    app.add_handler(CommandHandler("add_codes", add_codes_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_codes))

    # ✅ 注册按钮回调
    app.add_handler(CallbackQueryHandler(handle_signin, pattern="^signin$"))
    app.add_handler(CallbackQueryHandler(handle_points, pattern="^points$"))
    app.add_handler(CallbackQueryHandler(cart_menu_callback, pattern="^cart_menu$"))
    app.add_handler(CallbackQueryHandler(show_rewards, pattern="^rewards$"))
    app.add_handler(CallbackQueryHandler(handle_redeem, pattern="^redeem_"))
    app.add_handler(CallbackQueryHandler(show_rank_callback, pattern="^rank_menu$"))

    # 管理员按钮
    app.add_handler(CallbackQueryHandler(add_reward_start, pattern="^add_reward$"))
    app.add_handler(CallbackQueryHandler(delete_reward_start, pattern="^delete_reward$"))
    app.add_handler(CallbackQueryHandler(list_rewards, pattern="^list_rewards$"))
    app.add_handler(CallbackQueryHandler(confirm_delete_reward, pattern="^del_"))

    # 奖品添加对话流程
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_reward_start, pattern="^add_reward$")],
        states={
            ADD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_reward_id)],
            ADD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_reward_title)],
            ADD_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_reward_desc)],
            ADD_COST: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_reward_cost)],
        },
        fallbacks=[],
    )
    app.add_handler(conv_handler)

    print("✅ 机器人已启动，监听中...")
    app.run_polling()

if __name__ == "__main__":
    main()
