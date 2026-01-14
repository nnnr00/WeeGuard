以下是基于你描述的**完整轻量版代码模板**（aiogram 2.x 同步风格，与你现有仓库风格一致），**所有需要你手动修改的地方都用清晰注释标注**。

```python
import logging
import time
from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

# ============================== 需要你修改的地方 ==============================

BOT_TOKEN = "1234567890:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"          # ← 改成你的 Bot Token

# 收款码图片 file_id（发给机器人后获取）
FILE_ID_QRCODE = "AgACAgIAAxkBAAI..."                              # ← 替换

# 订单号查看教程图 file_id
FILE_ID_TUTORIAL = "AgACAgIAAxkBAAJ..."                            # ← 替换

# 输入订单号时的可选美化背景图（可留空）
FILE_ID_INPUT_BG = ""                                               # ← 可选替换

# 会员群邀请链接
GROUP_LINK = "https://t.me/+495j5rWmApsxYzg9"

# ============================== 全局变量 ==============================

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# 订单验证尝试记录 {uid: {"count": int, "next_try": timestamp}}
user_attempts = {}

# 积分 & 已购买商品（内存版，重启丢失；如需持久化请使用 Redis/SQLite）
user_points = {}        # uid -> points
user_purchased = {}     # uid -> set of product_id

# 示例商品（可自行扩展）
products = {
    "vip7d": {
        "name": "VIP 7天体验",
        "price": 100,
        "content": "这是 VIP 7 天体验内容...",
        "type": "text",          # text / photo / video
        "file_id": None,         # 如果是图片/视频请填 file_id
    },
    "test": {
        "name": "测试礼品",
        "price": 0,
        "content": "哈哈哈",
        "type": "text",
        "file_id": None,
    }
}

# ============================== 状态 ==============================
class VerifyOrder(StatesGroup):
    waiting_for_order = State()

class ExchangeStates(StatesGroup):
    pass

# ============================== 主菜单 ==============================
def get_main_menu():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("✅ 开始验证", callback_data="start_verify"),
        types.InlineKeyboardButton("💰 积分中心", callback_data="points_menu")
    )
    return markup

@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 <b>欢迎加入【VIP中转】！</b>\n"
        "我是守门员小卫，你的身份验证小助手～",
        reply_markup=get_main_menu(),
        parse_mode="HTML"
    )

# ============================== 开始验证流程 ==============================
@dp.callback_query_handler(text="start_verify")
async def cb_start_verify(call: types.CallbackQuery):
    vip_text = (
        "<b>💎 VIP会员特权说明</b>\n"
        "✅ 专属中转通道\n"
        "✅ 优先审核入群\n"
        "✅ 7×24小时客服支持\n"
        "✅ 定期福利活动"
    )
    await bot.send_photo(
        call.message.chat.id,
        photo=FILE_ID_QRCODE,
        caption=vip_text,
        parse_mode="HTML"
    )

    tutorial = (
        "<b>支付完成后，请按以下步骤查看订单号：</b>\n\n"
        "1. 我的 → 账单\n"
        "2. 进入账单详情\n"
        "3. 点击右上角「更多」\n"
        "4. 复制完整的<b>订单号</b>\n\n"
        "<b>直接回复订单号即可完成验证</b>"
    )

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✅ 我已付款，开始验证", callback_data="input_order"))

    await bot.send_photo(
        call.message.chat.id,
        photo=FILE_ID_TUTORIAL,
        caption=tutorial,
        reply_markup=markup,
        parse_mode="HTML"
    )
    await call.answer()

# ============================== 输入订单号 ==============================
@dp.callback_query_handler(text="input_order")
async def cb_input_order(call: types.CallbackQuery, state: FSMContext):
    text = "<b>请直接回复你的订单号：</b>"
    if FILE_ID_INPUT_BG:
        await bot.send_photo(call.message.chat.id, photo=FILE_ID_INPUT_BG, caption=text, parse_mode="HTML")
    else:
        await bot.send_message(call.message.chat.id, text, parse_mode="HTML")
    
    await VerifyOrder.waiting_for_order.set()
    await call.answer()

@dp.message_handler(state=VerifyOrder.waiting_for_order)
async def process_order(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    order = message.text.strip()

    # 校验规则（不提示具体前缀）
    if not order.startswith("20260"):
        attempts = user_attempts.get(uid, {"count": 0, "next_try": 0})

        if attempts["next_try"] > time.time():
            remain = int(attempts["next_try"] - time.time())
            await message.answer(f"输入错误次数过多，请约 {remain//3600} 小时后重试。")
            return

        count = attempts["count"] + 1
        if count >= 2:
            next_try = time.time() + 15*3600
            user_attempts[uid] = {"count": count, "next_try": next_try}
            await message.answer("输入错误次数过多，已锁定 15 小时。")
            await state.finish()
            return

        user_attempts[uid] = {"count": count, "next_try": 0}

        # 返回输入页面
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("重新输入订单号", callback_data="input_order"))

        await bot.send_photo(
            message.chat.id,
            photo=FILE_ID_TUTORIAL,
            caption="未查询到订单信息，请检查后重试～",
            reply_markup=markup,
            parse_mode="HTML"
        )
        return

    # 成功
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🎉 加入会员群", url=GROUP_LINK))

    await message.answer(
        "✅ <b>订单验证成功！</b>\n点击下方按钮加入会员群～",
        reply_markup=markup,
        parse_mode="HTML"
    )
    await state.finish()
    user_attempts.pop(uid, None)

# ============================== 积分中心 ==============================
@dp.callback_query_handler(text="points_menu")
async def points_menu(call: types.CallbackQuery):
    uid = call.from_user.id
    points = user_points.get(uid, 0)
    purchased = user_purchased.get(uid, set())

    text = f"<b>当前积分：</b> {points}\n\n"

    markup = types.InlineKeyboardMarkup(row_width=1)
    for pid, item in products.items():
        if not item.get("active", True):
            continue
        if pid in purchased:
            btn_text = f"{item['name']} - 已购买"
            cb_data = f"reget:{pid}"
        else:
            btn_text = f"{item['name']} - {item['price']}积分"
            cb_data = f"confirm_exchange:{pid}"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=cb_data))

    markup.add(types.InlineKeyboardButton("🔙 返回主菜单", callback_data="back_main"))
    await call.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await call.answer()

@dp.callback_query_handler(text="back_main")
async def back_main(call: types.CallbackQuery):
    await call.message.edit_text("主菜单", reply_markup=get_main_menu(), parse_mode="HTML")
    await call.answer()

# ============================== 兑换商品 ==============================
@dp.callback_query_handler(lambda c: c.data.startswith("confirm_exchange:"))
async def confirm_exchange(call: types.CallbackQuery):
    pid = call.data.split(":")[1]
    item = products.get(pid)
    if not item:
        await call.answer("商品不存在", show_alert=True)
        return

    uid = call.from_user.id
    points = user_points.get(uid, 0)

    text = (
        f"<b>确认兑换</b>\n\n"
        f"商品：{item['name']}\n"
        f"消耗：<b>{item['price']} 积分</b>\n"
        f"当前余额：{points} 积分"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ 确认兑换", callback_data=f"do_exchange:{pid}"),
        types.InlineKeyboardButton("❌ 取消", callback_data="points_menu")
    )
    await call.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("do_exchange:"))
async def do_exchange(call: types.CallbackQuery):
    pid = call.data.split(":")[1]
    item = products.get(pid)
    if not item:
        await call.answer("商品不存在", show_alert=True)
        return

    uid = call.from_user.id
    points = user_points.get(uid, 0)

    if points < item["price"]:
        await call.answer("积分不足", show_alert=True)
        return

    user_points[uid] = points - item["price"]
    user_purchased.setdefault(uid, set()).add(pid)

    # 发送商品内容
    if item["type"] == "text":
        await call.message.answer(item["content"])
    elif item["type"] == "photo" and item.get("file_id"):
        await call.message.answer_photo(item["file_id"], caption=item["content"])
    elif item["type"] == "video" and item.get("file_id"):
        await call.message.answer_video(item["file_id"], caption=item["content"])

    await call.message.answer(
        f"🎉 <b>兑换成功！</b>\n商品：{item['name']}\n<b>已购买</b>",
        parse_mode="HTML"
    )
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("reget:"))
async def reget_content(call: types.CallbackQuery):
    pid = call.data.split(":")[1]
    item = products.get(pid)
    if not item:
        await call.answer("商品不存在", show_alert=True)
        return

    if item["type"] == "text":
        await call.message.answer(item["content"])
    elif item["type"] == "photo" and item.get("file_id"):
        await call.message.answer_photo(item["file_id"], caption=item["content"])
    elif item["type"] == "video" and item.get("file_id"):
        await call.message.answer_video(item["file_id"], caption=item["content"])

    await call.answer("已重新发送商品内容")

# ============================== 启动 ==============================
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    executor.start_polling(dp, skip_updates=True)
```

### 必须修改的 4 处（总结）

1. `BOT_TOKEN`
2. `FILE_ID_QRCODE`
3. `FILE_ID_TUTORIAL`
4. `FILE_ID_INPUT_BG`（可选）

如需后续加入 **Redis / SQLite / PostgreSQL** 持久化，可在 `user_points` 和 `user_purchased` 部分替换为数据库操作。
