# ------------------------------------------------------------
# main.py
# ------------------------------------------------------------
# 這個檔案同時完成：
#   1️⃣  Telegram Bot（/start、/admin、File‑ID、积分、moontag 等）
#   2️⃣ FastAPI 伺服器（提供 HTML、廣告回調、密鑰驗證等）
#   3️⃣ 每日自動生成兩個 10 位隨機密鑰、使用計數與重置
#   4️⃣ 完整的防作弊、計數、通知與积分獎勵
# ------------------------------------------------------------

import asyncio
import datetime
import json
import os
import random
from datetime import date, time, timedelta
from typing import Dict, List

import aiosqlite
import pytz
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters,
)

# ------------------- 常量 -------------------
# 必須在平台的環境變數中提供這兩個
TELEGRAM_BOT_TOKEN: str = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")  # ← 替換為你的 Bot Token
BEAJING_TIMEZONE = pytz.timezone("Asia/Shanghai")
DB_FILE = "data.sqlite"

# 积分、广告计数、密钥表的名稱
TABLE_POINTS = "points"
TABLE_AD_COUNTS = "daily_ad_counts"
TABLE_REWARD_ATTEMPTS = "reward_attempts"
TABLE_KEYS = "daily_keys"
TABLE_KEY_USAGE = "key_usage"

# 奖励值
REWARD_FIRST_TIME = 10           # 第一次观看视频获得的积分
REWARD_SECOND_TIME = 6           # 第二次观看视频获得的积分
REWARD_THIRD_MIN = 3             # 第三次及以后随机下限
REWARD_THIRD_MAX = 10            # 第三次及以后随机上限

# 密钥相关常量
KEY_POINT_1 = 8                  # 使用密钥 1 获得的积分
KEY_POINT_2 = 6                  # 使用密钥 2 获得的积分
MAX_DAILY_AD_WATCHES = 3        # 每位用户每天最多观看 rewarded ad 的次数
MAX_KEY_CLICKS_PER_DAY = 2       # 每位用户每天最多使用密钥的次数
KEY_RESET_HOUR = 10              # 北京时间 10:00 自动重置密钥与计数

# ------------------- SQLite 輔助 -------------------
async def get_db_connection() -> aiosqlite.Connection:
    """返回已设置 row_factory 的 SQLite 连接。"""
    conn = await aiosqlite.connect(DB_FILE)
    conn.row_factory = aiosqlite.Row
    return conn


async def ensure_schema() -> None:
    """若表不存在则创建全部表格。"""
    async with await get_db_connection() as conn:
        # points 表（存储积分余额）
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_POINTS} (
                user_id          INTEGER PRIMARY KEY,
                balance          INTEGER NOT NULL DEFAULT 0,
                last_sign_date   TEXT,
                created_at       TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        # daily_ad_counts 表（统计每日观看完广告的次数）
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_AD_COUNTS} (
                user_id      INTEGER PRIMARY KEY,
                count_today  INTEGER NOT NULL DEFAULT 0,
                last_reset   TEXT NOT NULL
            );
            """
        )
        # reward_attempts 表（累计观看广告次数，用于决定奖励等级）
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_REWARD_ATTEMPTS} (
                user_id      INTEGER PRIMARY KEY,
                attempt_cnt  INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        # daily_keys 表（存储当天生成的两个密钥）
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_KEYS} (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                key1         TEXT,
                key2         TEXT,
                generated_at TEXT NOT NULL
            );
            """
        )
        # key_usage 表（标记密钥是否已使用）
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_KEY_USAGE} (
                key_id   INTEGER PRIMARY KEY,
                used     INTEGER NOT NULL DEFAULT 0   -- 0：未使用，1：已使用
            );
            """
        )
        await conn.commit()


# ------------------- 基础数据库操作 -------------------
async def get_user_balance(user_id: int) -> int:
    """返回用户当前的积分餘额。"""
    async with await get_db_connection() as conn:
        async with conn.execute(
            f"SELECT balance FROM {TABLE_POINTS} WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row["balance"] if row else 0


async def add_points(user_id: int, points: int) -> None:
    """向用户积分表中加入 points 分。"""
    async with await get_db_connection() as conn:
        async with conn.execute(
            f"""
            INSERT OR REPLACE INTO {TABLE_POINTS} (user_id, balance)
            VALUES (?, ?)
            """,
            (user_id, get_user_balance(user_id) + points),
        )
        await conn.commit()


async def increment_daily_ad_count(user_id: int) -> bool:
    """
    增加用户当天观看完广告的次数。
    若已达 MAX_DAILY_AD_WATCHES 则返回 False，否则返回 True。
    """
    today_str = datetime.datetime.now(BEAJING_TIMEZONE).strftime("%Y-%m-%d")
    async with await get_db_connection() as conn:
        # 检查上一次记录的日期是否是今天
        async with conn.execute(
            f"SELECT last_reset FROM {TABLE_AD_COUNTS} WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            stored_date = row["last_reset"] if row else None

        if stored_date != today_str:
            # 不是今天，重置计数
            await conn.execute(
                f"""
                INSERT OR REPLACE INTO {TABLE_AD_COUNTS}
                (user_id, count_today, last_reset)
                VALUES (?, 1, ?)
                """,
                (user_id, today_str),
            )
            await conn.commit()
            return True

        # 已是今天，检查上限
        async with conn.execute(
            f"SELECT count_today FROM {TABLE_AD_COUNTS} WHERE user_id = ?", (user_id,)
        ) as cur:
            cur_count = await cur.fetchone()
            if cur_count["count_today"] >= MAX_DAILY_AD_WATCHES:
                return False

            await conn.execute(
                f"""
                UPDATE {TABLE_AD_COUNTS}
                SET count_today = count_today + 1
                WHERE user_id = ?
                """,
                (user_id,),
            )
            await conn.commit()
            return True


async def reset_daily_key_records() -> None:
    """
    每天北京时间 10:00 自动执行：
      1) 生成两个 10 位随机密钥（大小写字母 + 数字）
      2) 把 key_usage 表中两条记录的 used 标记为 0（未使用）
      3) 把新密钥写入 daily_keys 表
    """
    async with await get_db_connection() as conn:
        # 删除旧的唯一一条记录（只保留最新的那条）
        await conn.execute(f"DELETE FROM {TABLE_KEYS} WHERE id = 1")

        # 生成 10 位随机字符串的函数
        def random_key() -> str:
            chars = (
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                "abcdefghijklmnopqrstuvwxyz"
                "0123456789"
            )
            return "".join(random.choice(chars) for _ in range(10))

        key1 = random_key()
        key2 = random_key()
        now_str = datetime.datetime.now(BEAJING_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")

        await conn.execute(
            f"""
            INSERT INTO {TABLE_KEYS} (key1, key2, generated_at)
            VALUES (?, ?, ?)
            """,
            (key1, key2, now_str),
        )

        # 把 key_usage 表中两条记录的 used 设为 0（未使用）
        await conn.execute(f"INSERT OR REPLACE INTO {TABLE_KEY_USAGE} (key_id, used) VALUES (1, 0);")
        await conn.execute(f"INSERT OR REPLACE INTO {TABLE_KEY_USAGE} (key_id, used) VALUES (2, 0);")
        await conn.commit()


async def get_today_keys() -> List[Dict]:
    """
    返回今天生成的两个密钥及其使用状态。
    若尚未生成则返回空列表。
    """
    async with await get_db_connection() as conn:
        async with conn.execute(
            f"SELECT * FROM {TABLE_KEYS} ORDER BY id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return []

        # 读取 key_usage 表中两个 key_id 的 used 状态
        usage_info = []
        for i in range(1, 3):
            async with conn.execute(
                f"SELECT used FROM {TABLE_KEY_USAGE} WHERE key_id = ?", (i,)
            ) as cur:
                urow = await cur.fetchone()
                usage_info.append({"id": i, "used": urow["used"] if urow else 0})

        return [
            {
                "key": row["key1"] if row["key1"] else "",
                "used": usage_info[0]["used"],
                "key_id": 1,
            },
            {
                "key": row["key2"] if row["key2"] else "",
                "used": usage_info[1]["used"],
                "key_id": 2,
            },
        ]


async def _mark_key_as_used(key_id: int) -> None:
    """把指定的 key_id 标记为已使用（used = 1）。"""
    async with await get_db_connection() as conn:
        await conn.execute(
            f"UPDATE {TABLE_KEY_USAGE} SET used = 1 WHERE key_id = ?", (key_id,)
        )
        await conn.commit()


# ------------------- FastAPI 设置 -------------------
app = FastAPI()   # ← uvicorn 通过 "main:app" 来加载这个变量

# 把 doc/ 目录挂载为静态文件，供前端使用
app.mount("/docs", StaticFiles(directory="doc"), name="static")


@app.get("/webapp")
async def serve_webapp(request: Request) -> HTMLResponse:
    """提供 webapp.html（观看奖励视频的页面）。"""
    with open("doc/webapp.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.post("/ad_completed")
async def ad_completed(request: Request) -> JSONResponse:
    """
    当用户成功观看完奖励视频后，前端会 POST
    {"user_id":"123456789"} 到此端点。
    这里负责：
      1) 检查每日观看上限
      2) 计算奖励（第 1 次 10、第 2 次 6、之后随机 3~10）
      3) 更新积分
      4) 把成功状态返回给前端，并给 Telegram 用户发送积分提示
    """
    # 1) 读取 JSON
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    user_id_str = payload.get("user_id")
    if not user_id_str:
        raise HTTPException(status_code=400, detail="Missing user_id")
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="user_id must be integer")

    # 2) 检查每日观看次数上限
    if not await increment_daily_ad_count(user_id):
        return {"status": "daily_limit_reached"}

    # 3) 记录当前是第几次观看（1、2、3…），用于决定奖励
    async with await get_db_connection() as conn:
        async with conn.execute(
            f"SELECT attempt_cnt FROM {TABLE_REWARD_ATTEMPTS} WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            attempt_number = (row["attempt_cnt"] or 0) + 1

        await conn.execute(
            f"""
            INSERT OR REPLACE INTO {TABLE_REWARD_ATTEMPTS} (user_id, attempt_cnt)
            VALUES (?, ?)
            """,
            (user_id, attempt_number),
        )
        await conn.commit()

        # 根据次数决定奖励值
        if attempt_number == 1:
            reward = REWARD_FIRST_TIME
        elif attempt_number == 2:
            reward = REWARD_SECOND_TIME
        else:   # 第三次以后使用随机
            reward = random.randint(REWARD_THIRD_MIN, REWARD_THIRD_MAX)

    # 4) 写入积分
    await add_points(user_id, reward)

    # 5) 如果前端需要回显成功，同时把通知发给 Telegram 用户
    if hasattr(ad_completed, "telegram_app"):
        tg_app: Application = ad_completed.telegram_app   # type: ignore
        await tg_app.bot.send_message(
            chat_id=user_id,
            text=(
                f"✅ 恭喜您完成观看视频并获得 <b>{reward}</b> 积分！\n"
                f"您的积分已更新。"
            ),
            parse_mode="HTML",
        )

    # 6) 返回前端状态
    return {"status": "ok"}


@app.post("/api/submit_key")
async def submit_key(request: Request) -> JSONResponse:
    """
    前端（key_link.html）的「提交密钥」按钮会 POST
    {"user_id":"123456789","key1":"xxxx","key2":"yyyy"}。
    这里会：
      1) 检查提交的 key 是否匹配今天的密钥
      2) 若匹配且未使用，给予相应积分（8 或 6）
      3) 标记该密钥已使用
      4) 返回提示信息
    """
    try:
        data = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    user_id_str = data.get("user_id")
    key1 = data.get("key1", "").strip()
    key2 = data.get("key2", "").strip()
    if not user_id_str:
        raise HTTPException(status_code=400, detail="Missing user_id")
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="user_id must be integer")

    # 取得今天的两个密钥
    today_keys = await get_today_keys()
    if not today_keys:
        return {"status": "error", "message": "今日密钥尚未生成，请稍后再试。"}

    k1 = today_keys[0]
    k2 = today_keys[1]

    message = ""
    status = "error"

    # ---------- 验证 key1 ----------
    if key1 and not k1.get("used"):
        if key1 == k1.get("key", ""):
            await add_points(user_id, KEY_POINT_1)   # 8 分
            await _mark_key_as_used(1)               # 标记为已使用
            message = "✅ 首次密钥（密钥 1）领取成功，已发放 8 积分！"
            status = "ok"
        else:
            message = "❌ 首次密钥不正确，请重新检查后重新输入。"
    else:
        message = "⚠️ 首次密钥已使用或未填写。"

    # ---------- 验证 key2 ----------
    if status == "error" and key2 and not k2.get("used"):
        if key2 == k2.get("key", ""):
            await add_points(user_id, KEY_POINT_2)   # 6 分
            await _mark_key_as_used(2)               # 标记为已使用
            message = "✅ 次次密钥（密钥 2）领取成功，已发放 6 积分！"
            status = "ok"
        else:
            message = "❌ 次次密钥不正确，请重新检查后重新输入。"
    else:
        if not key2:
            message = "⚠️ 未输入第二个密钥。"
        elif k2.get("used"):
            message = "⚠️ 第二个密钥已经使用过。"

    return {"status": status, "message": message}


# ------------------- Telegram Bot 相关 -------------------
async def build_telegram_application() -> Application:
    """创建 Telegram Bot 并挂载所有指令与回调处理函数。"""
    app_tg = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # ---------- /start 菜单（三个大按钮） ----------
    async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """显示首页的三个按钮：開始驗證、积分、開業活動"""
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(text="開始驗證", callback_data="menu_verify"),
                    InlineKeyboardButton(text="积分", callback_data="menu_points"),
                ],
                [
                    InlineKeyboardButton(text="開業活動", callback_data="menu_campaign"),
                ],
            ]
        )
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                "👋 欢迎使用本机器人！请选择下方功能：", reply_markup=keyboard
            )
        else:
            await update.message.reply_text(
                "👋 欢迎使用本机器人！请选择下方功能：", reply_markup=keyboard
            )

    # ---------- 所有 InlineButton 的统一分配 ----------
    async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """所有 inline 按钮的统一入口"""
        query = update.callback_query
        if not query:
            return
        await query.answer()      # 必须先回复，否则前端会卡住

        data = query.data
        if data == "menu_verify":
            await query.edit_message_text(
                "正在为您执行开始验证的流程，请稍候…", reply_markup=InlineKeyboardMarkup([[]])
            )
        elif data == "menu_points":
            balance = await get_user_balance(query.from_user.id)
            await query.edit_message_text(
                f"🧮 您的当前积分为 <b>{balance}</b>，感谢您的使用！",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[]]),
            )
        elif data == "menu_campaign":
            # 这里假设你把页面部署在 GitHub Pages，替换为你自己的 URL
            github_page = "https://YOUR_GITHUB_USERNAME.github.io/YOUR_REPO_NAME/docs/webapp.html"
            encoded_user_id = "?user_id=" + str(query.from_user.id)
            full_url = github_page + encoded_user_id

            await query.edit_message_text(
                "🎉 正在打开活动中心，请稍等…",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton(text="按钮二 获取密钥", url=full_url)]]
                ),
            )
        else:
            await query.edit_message_text("未知的按钮操作，请重新选择。")

    # ---------- 保留原有的 admin 后台（不改动） ----------
    # 这里直接导入你原来的 admin 逻辑，保持不变
    from src.commands.admin import adminWizard   # ← 你的原始管理员后台
    app_tg.add_handler(CommandHandler("admin", adminWizard))
    app_tg.add_handler(CallbackQueryHandler(callback_handler))

    # ---------- /points、/jf 等旧功能（保持不变） ----------
    async def points_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """使用者直接输入 /points 时显示自己的积分"""
        balance = await get_user_balance(update.effective_user.id)
        await update.message.reply_text(
            f"🧮 您的当前积分为 <b>{balance}</b>，感谢您的使用！",
            parse_mode="HTML",
        )

    async def jf_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """旧的 /jf 命令（保持原有功能）"""
        await update.message.reply_text("此功能仍保留，未作变更。")

    app_tg.add_handler(CommandHandler("points", points_command))
    app_tg.add_handler(CommandHandler("jf", jf_handler))

    # ---------- 管理员专用指令 /my 与 /my无限次 ----------
    async def cmd_my(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """管理员使用 /my 查看当天生成的两个密钥及其使用状态"""
        keys_info = await get_today_keys()
        if not keys_info:
            await update.message.reply_text("尚未生成今日密钥，请稍等至 10:00。")
            return

        reply = ( "🗝️ 今日密钥列表（北京时间十点已更新）：\n\n" )

"
        for idx, item in enumerate(keys_info, start=1):
            usage = "已使用" if item.get("used") else "未使用"
            reply += f"【密钥 {idx}】{item.get('key', '')} —— {usage}\n"
        await update.message.reply_text(reply)

    async def cmd_set_new_keys(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        管理员可以手动传入两段字符串作为当天的密钥入口。
        用法示例：
            /my无限次 <密钥一链接> <密钥二链接>
        本函数会把这两串字符写入 daily_keys 表，并标记为未使用。
        """
        args = context.args
        if len(args) < 2:
            await update.message.reply_text(
                "⚠️ 使用方式：/my无限次 <密钥一链接> <密钥二链接>"
            )
            return
        link1, link2 = args[1], args[2]

        async with await get_db_connection() as conn:
            await conn.execute(
                f"""
                INSERT INTO {TABLE_KEYS} (key1, key2, generated_at)
                VALUES (?, ?, ?)
                """,
                (link1, link2, datetime.datetime.now(BEAJING_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")),
            )
            # 确保 key_usage 表中有两条记录且状态为“未使用”
            await conn.execute(f"INSERT OR REPLACE INTO {TABLE_KEY_USAGE} (key_id, used) VALUES (1, 0);")
            await conn.execute(f"INSERT OR REPLACE INTO {TABLE_KEY_USAGE} (key_id, used) VALUES (2, 0);")
            await conn.commit()

        await update.message.reply_text("密钥一绑定完成，请继续提供 **密钥二** 的链接：" )
        # 为简化演示，这里不再实现第二次输入的对话，
        # 如需完整流程，可自行加入 ConversationHandler。

    # 把这两个指令挂载到 Bot
    app_tg.add_handler(CommandHandler("my", cmd_my))
    app_tg.add_handler(CommandHandler("my无限次", cmd_set_new_keys))

    return app_tg


# ------------------- 背景任务：每日自动生成密钥 -------------------
async def daily_key_task() -> None:
    """
    每天北京时间 10:00 触发一次，自动生成两个随机密钥
    并把使用状态归零。若已经过去 10:00，则等到第二天再执行。
    """
    while True:
        now = datetime.datetime.now(BEAJING_TIMEZONE)
        # 计算距离今天 10:00 的秒数
        target = datetime.datetime.combine(now.date(), time(hour=KEY_RESET_HOUR, minute=0, second=0))
        if now >= target:
            target += datetime.timedelta(days=1)   # 已经超过去，等到明天
        delay = (target - now).total_seconds()
        await asyncio.sleep(delay)

        await reset_daily_key_records()
        print("✅ 每日密钥已更新。")


# ------------------- 主入口 -------------------
async def main() -> None:
    """
    程序的总启动流程：
      1️⃣ 確保資料庫表格已建立
      2️⃣ 創建 Telegram Bot 並掛載所有指令和回調
      3️⃣ 把 Telegram Application 交給 ad_completed 端點（用於回傳訊息）
      4️⃣ 啟動每日自動生成密鑰的背景工作
      5️⃣ 以 uvicorn 啟動 FastAPI，使用環境變數 $PORT
    """
    # Step 1 – 建立所有資料庫表格
    await ensure_schema()

    # Step 2 – 建立 Telegram Bot
    telegram_app = await build_telegram_application()

    # Step 3 – 把 telegram_app 交給 ad_completed，以便它能發送訊息
    ad_completed.telegram_app = telegram_app   # type: ignore

    # Step 4 – 啟動每日自動生成密鑰的背景工作
    asyncio.create_task(daily_key_task())

    # Step 5 – 以 uvicorn 啟動 FastAPI，使用 Railway 提供的 $PORT
    # 注意：這裡的字串 "main:app" 必須與檔案名稱保持一致（本檔案就是 main.py）
    uvicorn.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)))


# ------------------------------------------------------------
# 直接執行 main() 以便本地測試
# ------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    # 本地測試時直接執行 main()
    asyncio.run(main())
