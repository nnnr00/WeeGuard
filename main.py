# ------------------------------------------------------------
# 完整的 main.py（已修复 undetected string literal）
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
TELEGRAM_BOT_TOKEN: str = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
BEAJING_TIMEZONE = pytz.timezone("Asia/Shanghai")
DB_FILE = "data.sqlite"

TABLE_POINTS = "points"
TABLE_AD_COUNTS = "daily_ad_counts"
TABLE_REWARD_ATTEMPTS = "reward_attempts"
TABLE_KEYS = "daily_keys"
TABLE_KEY_USAGE = "key_usage"

REWARD_FIRST_TIME = 10
REWARD_SECOND_TIME = 6
REWARD_THIRD_MIN = 3
REWARD_THIRD_MAX = 10

KEY_POINT_1 = 8
KEY_POINT_2 = 6
MAX_DAILY_AD_WATCHES = 3
MAX_KEY_CLICKS_PER_DAY = 2
KEY_RESET_HOUR = 10

# ------------------- SQLite 輔助 -------------------
async def get_db_connection() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(DB_FILE)
    conn.row_factory = aiosqlite.Row
    return conn

async def ensure_schema() -> None:
    async with await get_db_connection() as conn:
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
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_AD_COUNTS} (
                user_id      INTEGER PRIMARY KEY,
                count_today  INTEGER NOT NULL DEFAULT 0,
                last_reset   TEXT NOT NULL
            );
            """
        )
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_REWARD_ATTEMPTS} (
                user_id      INTEGER PRIMARY KEY,
                attempt_cnt  INTEGER NOT NULL DEFAULT 0
            );
            """
        )
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
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_KEY_USAGE} (
                key_id   INTEGER PRIMARY KEY,
                used     INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        await conn.commit()

# ------------------- 基础資料庫操作 -------------------
async def get_user_balance(user_id: int) -> int:
    async with await get_db_connection() as conn:
        async with conn.execute(
            f"SELECT balance FROM {TABLE_POINTS} WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row["balance"] if row else 0

async def add_points(user_id: int, points: int) -> None:
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
    today_str = datetime.datetime.now(BEAJING_TIMEZONE).strftime("%Y-%m-%d")
    async with await get_db_connection() as conn:
        async with conn.execute(
            f"SELECT last_reset FROM {TABLE_AD_COUNTS} WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            stored_date = row["last_reset"] if row else None

        if stored_date != today_str:
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
    async with await get_db_connection() as conn:
        await conn.execute(f"DELETE FROM {TABLE_KEYS} WHERE id = 1")
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
        await conn.execute(f"INSERT OR REPLACE INTO {TABLE_KEY_USAGE} (key_id, used) VALUES (1, 0);")
        await conn.execute(f"INSERT OR REPLACE INTO {TABLE_KEY_USAGE} (key_id, used) VALUES (2, 0);")
        await conn.commit()

async def get_today_keys() -> List[Dict]:
    async with await get_db_connection() as conn:
        async with conn.execute(
            f"SELECT * FROM {TABLE_KEYS} ORDER BY id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return []
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
    async with await get_db_connection() as conn:
        await conn.execute(
            f"UPDATE {TABLE_KEY_USAGE} SET used = 1 WHERE key_id = ?", (key_id,)
        )
        await conn.commit()

# ------------------- FastAPI -------------------
app = FastAPI()
app.mount("/docs", StaticFiles(directory="doc"), name="static")

@app.get("/webapp")
async def serve_webapp(request: Request) -> HTMLResponse:
    with open("doc/webapp.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.post("/ad_completed")
async def ad_completed(request: Request) -> JSONResponse:
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

    if not await increment_daily_ad_count(user_id):
        return {"status": "daily_limit_reached"}

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
        if attempt_number == 1:
            reward = REWARD_FIRST_TIME
        elif attempt_number == 2:
            reward = REWARD_SECOND_TIME
        else:
            reward = random.randint(REWARD_THIRD_MIN, REWARD_THIRD_MAX)

    await add_points(user_id, reward)

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
    return {"status": "ok"}

@app.post("/api/submit_key")
async def submit_key(request: Request) -> JSONResponse:
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

    today_keys = await get_today_keys()
    if not today_keys:
        return {"status": "error", "message": "今日密钥尚未生成，请稍后再试。"}

    k1 = today_keys[0]
    k2 = today_keys[1]

    message = ""
    status = "error"

    if key1 and not k1.get("used"):
        if key1 == k1.get("key", ""):
            await add_points(user_id, KEY_POINT_1)
            await _mark_key_as_used(1)
            message = "✅ 首次密钥（密钥 1）领取成功，已发放 8 积分！"
            status = "ok"
        else:
            message = "❌ 首次密钥不正确，请重新检查后重新输入。"
    else:
        message = "⚠️ 首次密钥已使用或未填写。"

    if status == "error" and key2 and not k2.get("used"):
        if key2 == k2.get("key", ""):
            await add_points(user_id, KEY_POINT_2)
            await _mark_key_as_used(2)
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

# ------------------- Telegram Bot -------------------
async def build_telegram_application() -> Application:
    app_tg = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query:
            return
        await query.answer()
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

    from src.commands.admin import adminWizard          # <-- 导入 admin Wizard
    from src.commands.fileId import adminFileIdWizard   # <-- 导入 fileId Wizard
    app_tg.add_handler(CommandHandler("admin", adminWizard))
    app_tg.add_handler(CallbackQueryHandler(callback_handler))
    app_tg.add_handler(adminFileIdWizard)   # <-- 把 fileId Wizard 挂上

    # ---- 旧的积分指令保持不变 ----
    async def points_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        balance = await get_user_balance(update.effective_user.id)
        await update.message.reply_text(
            f"🧮 您的当前积分为 <b>{balance}</b>，感谢您的使用！",
            parse_mode="HTML",
        )

    async def jf_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("此功能仍保留，未作变更。")

    app_tg.add_handler(CommandHandler("points", points_command))
    app_tg.add_handler(CommandHandler("jf", jf_handler))

    # ---- 管理员专用指令 /my 与 /my无限次 ----
    async def cmd_my(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        keys_info = await get_today_keys()
        if not keys_info:
            await update.message.reply_text("尚未生成今日密钥，请稍等至 10:00。")
            return

        reply = (
            "🗝️ 今日密钥列表（北京时间十点已更新）：\n\n"
        )
        reply += "\n".join(
            f"【密钥 {idx}】{item.get('key', '')} —— "
            f"{'已使用' if item.get('used') else '未使用'}"
            for idx, item in enumerate(keys_info, start=1)
        )
        await update.message.reply_text(reply)

    async def cmd_set_new_keys(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            await conn.execute(f"INSERT OR REPLACE INTO {TABLE_KEY_USAGE} (key_id, used) VALUES (1, 0);")
            await conn.execute(f"INSERT OR REPLACE INTO {TABLE_KEY_USAGE} (key_id, used) VALUES (2, 0);")
            await conn.commit()

        await update.message.reply_text("密钥一绑定完成，请继续提供 **密钥二** 的链接：" )
        # 实际项目里可在这里继续等待第二个链接的消息，简化示例未实现完整对话。

    app_tg.add_handler(CommandHandler("my", cmd_my))
    app_tg.add_handler(CommandHandler("my无限次", cmd_set_new_keys))

    return app_tg

# ------------------- 背景任务：每日自动生成密钥 -------------------
async def daily_key_task() -> None:
    while True:
        now = datetime.datetime.now(BEAJING_TIMEZONE)
        target = datetime.datetime.combine(now.date(), time(hour=KEY_RESET_HOUR, minute=0, second=0))
        if now >= target:
            target += datetime.timedelta(days=1)
        delay = (target - now).total_seconds()
        await asyncio.sleep(delay)
        await reset_daily_key_records()
        print("✅ 每日密钥已更新。")

# ------------------- 主入口 -------------------
async def main() -> None:
    await ensure_schema()
    telegram_app = await build_telegram_application()
    ad_completed.telegram_app = telegram_app   # type: ignore
    asyncio.create_task(daily_key_task())
    uvicorn.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)))

if __name__ == "__main__":
    import uvicorn
    asyncio.run(main())
