# ------------------------------------------------------------
# main.py
# ------------------------------------------------------------
# 這個檔案同時完成：
#   • Telegram Bot（/start、/admin、File‑ID、积分、moontag 等）
#   • FastAPI 伺服器（提供 HTML、廣告回調、密鑰驗證等）
#   • 之後會被 uvicorn 以 "main:app" 的方式啟動
# ------------------------------------------------------------

import asyncio
import json
import os
import random
from datetime import datetime, date, time, timedelta
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
TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")   # 環境變數
BEIJING_TIMEZONE = pytz.timezone("Asia/Shanghai")
DB_FILE = "data.sqlite"

# 积分與鑰匙相關的常量
TABLE_POINTS = "points"
TABLE_AD_COUNTS = "daily_ad_counts"
TABLE_REWARD_ATTEMPTS = "reward_attempts"
TABLE_KEYS = "daily_keys"
TABLE_KEY_USAGE = "key_usage"

REWARD_FIRST_TIME = 10
REWARD_SECOND_TIME = 6
REWARD_THIRD_MIN = 3
REWARD_THIRD_MAX = 10

KEY_POINT_1 = 8      # 單次取得密钥 1 的积分
KEY_POINT_2 = 6      # 單次取得密钥 2 的积分
MAX_DAILY_AD_WATCHES = 3
MAX_KEY_CLICKS_PER_DAY = 2
KEY_RESET_HOUR = 10   # 北京時間 10:00 自動重置

# ------------------- SQLite 輔助 -------------------
async def get_db_connection() -> aiosqlite.Connection:
    """返回一個已設定 row_factory 的 SQLite 連線。"""
    conn = await aiosqlite.connect(DB_FILE)
    conn.row_factory = aiosqlite.Row
    return conn


async def ensure_schema() -> None:
    """若資料表不存在則建立全部表格。"""
    async with await get_db_connection() as conn:
        # points 表（积分）
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
        # daily_ad_counts 表（每日看廣告次數）
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_AD_COUNTS} (
                user_id      INTEGER PRIMARY KEY,
                count_today  INTEGER NOT NULL DEFAULT 0,
                last_reset   TEXT NOT NULL
            );
            """
        )
        # reward_attempts 表（累計看廣告次數，用來決定獎勵等級）
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_REWARD_ATTEMPTS} (
                user_id      INTEGER PRIMARY KEY,
                attempt_cnt  INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        # daily_keys 表（存儲每天產生的兩個密鑰）
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
        # key_usage 表（標記密鑰是否已被使用）
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_KEY_USAGE} (
                key_id   INTEGER PRIMARY KEY,
                used     INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        await conn.commit()


# ------------------- 基本 DB 操作 -------------------
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
    """
    增加今日看完廣告的次數，若已達上限則回傳 False。
    每天重置在北京時間 00:00 自動進行。
    """
    today_str = datetime.now(BEIJING_TIMEZONE).strftime("%Y-%m-%d")
    async with await get_db_connection() as conn:
        # 先判斷上一次記錄是否是今天
        async with conn.execute(
            f"SELECT last_reset FROM {TABLE_AD_COUNTS} WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            stored_date = row["last_reset"] if row else None

        if stored_date != today_str:
            # 不是今天 → 重設計數
            await conn.execute(
                f"""
                INSERT OR REPLACE INTO {TABLE_AD_COUNTS}
                (user_id, count_today, last_reset)
                VALUES (?, 1, ?)
                """,
                (user_id, today_str),
            )
            return True

        # 今天已有記錄，檢查上限
        async with conn.execute(
            f"SELECT count_today FROM {TABLE_AD_COUNTS} WHERE user_id = ?",
            (user_id,),
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
    每天北京時間 10:00 自動執行：
      1. 生成兩個隨機的 10 位密鑰
      2. 把舊的使用狀態歸零
      3. 把新密鑰寫入 daily_keys 表
    """
    async with await get_db_connection() as conn:
        # 清空舊的一次性記錄（只保留一條）
        await conn.execute(f"DELETE FROM {TABLE_KEYS} WHERE id = 1")

        # 產生 10 位大小寫+數字混合的隨機字串
        def random_key() -> str:
            chars = (
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                "abcdefghijklmnopqrstuvwxyz"
                "0123456789"
            )
            return "".join(random.choice(chars) for _ in range(10))

        key1 = random_key()
        key2 = random_key()
        now_str = datetime.now(BEIJING_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")

        await conn.execute(
            f"""
            INSERT INTO {TABLE_KEYS} (key1, key2, generated_at)
            VALUES (?, ?, ?)
            """,
            (key1, key2, now_str),
        )

        # 重置 key_usage 為「未使用」(0)
        await conn.execute(f"INSERT OR REPLACE INTO {TABLE_KEY_USAGE} (key_id, used) VALUES (1, 0);")
        await conn.execute(f"INSERT OR REPLACE INTO {TABLE_KEY_USAGE} (key_id, used) VALUES (2, 0);")
        await conn.commit()


async def get_today_keys() -> List[Dict]:
    """返回今天產生的兩個密鑰以及它們的使用狀態。"""
    async with await get_db_connection() as conn:
        async with conn.execute(
            f"SELECT * FROM {TABLE_KEYS} ORDER BY id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return []

        # 取出 key_usage 表裡的使用標記
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


# ------------------- FastAPI -------------------
app = FastAPI()   # ← 這個變數名稱必須叫作 `app`，因為 uvicorn 要 import "main:app"

# 靜態檔案（HTML、CSS）放在 project 的 doc/ 目錄
app.mount("/docs", StaticFiles(directory="doc"), name="static")


@app.get("/webapp")
async def serve_webapp(request: Request) -> HTMLResponse:
    """提供 `doc/webapp.html` 給前端使用。"""
    with open("doc/webapp.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.post("/ad_completed")
async def ad_completed(request: Request) -> JSONResponse:
    """
    當廣告觀看成功結束時，前端會向此端點 POST
    { "user_id": "123456789" }
    這裡負責：
      1️⃣ 檢查每日觀看上限
      2️⃣ 計算獎勵 (10 → 6 → 3~10 隨機)
      3️⃣ 更新积分
      4️⃣ 把成功訊息回傳給前端，並且把积分通知給 Telegram 用戶
    """
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

    # 1️⃣ 判斷是否可以再領取廣告獎勵
    if not await increment_daily_ad_count(user_id):
        return {"status": "daily_limit_reached"}

    # 2️⃣ 記錄已觀看的次數（1、2、3…），用來決定獎勵等級
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

        # 依照次數決定獎勵值
        if attempt_number == 1:
            reward = REWARD_FIRST_TIME
        elif attempt_number == 2:
            reward = REWARD_SECOND_TIME
        else:   # 第三次及以後使用隨機 3~10
            reward = random.randint(REWARD_THIRD_MIN, REWARD_THIRD_MAX)

    # 3️⃣ 寫入积分
    await add_points(user_id, reward)

    # 4️⃣ 把成功訊息回覆給前端（前端會顯示「✅ 积分已发放」）
    if hasattr(ad_completed, "telegram_app"):   # 這個屬性會在 main() 裡設置
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
    """
    前端（key_link.html）的「提交密钥」按鈕會向此端點 POST
    { "user_id": "...", "key1": "...", "key2": "..." }
    此端點會：
      1️⃣ 檢查傳入的 key 是否匹配今天的 key1 / key2
      2️⃣ 若匹配且尚未使用，就給予相應的积分（8 / 6）
      3️⃣ 標記該密鑰已使用
      4️⃣ 回傳提示訊息
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

    # 取出今天的兩個密鑰
    today_keys = await get_today_keys()
    if not today_keys:
        return {"status": "error", "message": "今日密钥尚未生成，請稍後再試。"}

    k1 = today_keys[0]
    k2 = today_keys[1]

    message = ""
    status = "error"

    # ---------- 驗證 key1 ----------
    if key1 and not k1.get("used"):
        if key1 == k1.get("key", ""):
            await add_points(user_id, KEY_POINT_1)   # 8 分
            await _mark_key_as_used(1)               # 標記為已使用
            message = "✅ 首次密钥（密钥 1）领取成功，已發放 8 积分！"
            status = "ok"
        else:
            message = "❌ 首次密钥不正確，請檢查後重新輸入。"
    else:
        message = "⚠️ 首次密钥已使用或未填寫。"

    # ---------- 驗證 key2 ----------
    if status == "error" and key2 and not k2.get("used"):
        if key2 == k2.get("key", ""):
            await add_points(user_id, KEY_POINT_2)   # 6 分
            await _mark_key_as_used(2)
            message = "✅ 次次密钥（密钥 2）领取成功，已發放 6 积分！"
            status = "ok"
        else:
            message = "❌ 次次密钥不正確，请檢查後重新輸入。"
    else:
        if not key2:
            message = "⚠️ 未輸入第二個密钥。"
        elif k2.get("used"):
            message = "⚠️ 第二個密钥已經使用過了。"

    return {"status": status, "message": message}


async def _mark_key_as_used(key_id: int) -> None:
    """把指定的 key_id 標記為「已使用」(used = 1)。"""
    async with await get_db_connection() as conn:
        await conn.execute(
            f"UPDATE {TABLE_KEY_USAGE} SET used = 1 WHERE key_id = ?", (key_id,)
        )
        await conn.commit()


# ------------------- Telegram Bot 相關 -------------------
async def build_telegram_application() -> Application:
    """創建 Telegram Bot 並掛載所有指令與回調。"""
    app_tg = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # ---- /start 菜單（三個大按鈕） ----
    async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """顯示首頁的三個按鈕：開始驗證、积分、開業活動"""
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

    # ---- 回調分發 ----
    async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """所有 inline_button 的統一入口"""
        query = update.callback_query
        if not query:
            return
        await query.answer()  # 必須先回覆，否則前端會卡住

        data = query.data
        if data == "menu_verify":
            await query.edit_message_text(
                "正在為您執行開始驗證的流程，請稍候…", reply_markup=InlineKeyboardMarkup([[]])
            )
        elif data == "menu_points":
            balance = await get_user_balance(query.from_user.id)
            await query.edit_message_text(
                f"🧮 您的當前积分为 <b>{balance}</b>，感謝您的使用！",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[]]),
            )
        elif data == "menu_campaign":
            # 假設您把靜態頁面部署在 GitHub Pages，URL 為：
            # https://<YOUR_GITHUB_USERNAME>.github.io/<REPO>/docs/webapp.html
            github_page = "https://YOUR_GITHUB_USERNAME.github.io/YOUR_REPO_NAME/docs/webapp.html"
            encoded_user_id = "?user_id=" + str(query.from_user.id)
            full_url = github_page + encoded_user_id

            await query.edit_message_text(
                "🎉 正在打開活動中心，請稍等…",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton(text="按鈕二 取得密钥", url=full_url)]]
                ),
            )
        else:
            await query.edit_message_text("未知的按鈕操作，请重新選擇。")

    # ---- /admin（保持原有功能）----
    # 這裡直接引用您之前寫好的 adminWizard（不再重寫），只要保證
    # adminWizard 已經在專案的 src/commands/admin.py 中存在即可。
    # 以下示範只做一個佔位匯入：
    from src.commands.admin import adminWizard  # ← 您的原始管理員後台

    app_tg.add_handler(CommandHandler("admin", adminWizard))
    app_tg.add_handler(CallbackQueryHandler(callback_handler))

    # ---- /points、/jf 等舊有指令（保持不變）----
    async def points_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """使用者直接輸入 /points 時顯示自己的积分"""
        balance = await get_user_balance(update.effective_user.id)
        await update.message.reply_text(
            f"🧮 您的當前积分为 <b>{balance}</b>，感謝您的使用！",
            parse_mode="HTML",
        )

    async def jf_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """舊的 /jf 指令（保持原有功能）"""
        await update.message.reply_text("此功能仍保留，未作變更。")

    app_tg.add_handler(CommandHandler("points", points_command))
    app_tg.add_handler(CommandHandler("jf", jf_handler))

    # ---- /my（管理员專用）相關指令 ----
    async def cmd_my(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """管理员使用 /my 查看當天生成的兩個密钥及其使用狀態"""
        keys_info = await get_today_keys()
        if not keys_info:
            await update.message.reply_text("尚未生成今日密钥，請稍等至 10:00。")
            return

        reply = "🗝️ 今日密钥列表（北京時間十點已更新）：\n\n"
        for idx, item in enumerate(keys_info, start=1):
            usage = "已使用" if item.get("used") else "未使用"
            reply += f"【密钥 {idx}】{item.get('key', '')} —— {usage}\n"
        await update.message.reply_text(reply)

    async def cmd_set_new_keys(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        管理员可以手動傳入兩個 URL（或任何字串）作為當天的密钥入口。
        用法示例：
            /my無限次 <密钥一链接> <密钥二链接>
        這裡不再實作完整的 conversation，只示意如何寫入資料庫。
        """
        # 這裡的 args 來源於指令後面的參數
        args = context.args
        if len(args) < 2:
            await update.message.reply_text(
                "⚠️ 使用方式：/my無限次 <密钥一链接> <密钥二链接>"
            )
            return
        link1, link2 = args[1], args[2]

        async with await get_db_connection() as conn:
            await conn.execute(
                f"""
                INSERT INTO {TABLE_KEYS} (key1, key2, generated_at)
                VALUES (?, ?, ?)
                """,
                (link1, link2, datetime.now(BEIJING_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")),
            )
            # 確保 key_usage 表中有兩筆記錄且未使用
            await conn.execute("INSERT OR REPLACE INTO {TABLE_KEY_USAGE} (key_id, used) VALUES (1, 0);")
            await conn.execute("INSERT OR REPLACE INTO {TABLE_KEY_USAGE} (key_id, used) VALUES (2, 0);")
            await conn.commit()

        await update.message.reply_text("密钥一绑定完成，请继续提供 **密钥二** 的链接：" )
        # 實際上需要進一步的 conversation 才能取得第二個鏈接，
        # 這裡僅示意，實作時可自行加入 ConversationHandler。

    # ---- 挂載這兩個管理員指令 ----
    app_tg.add_handler(CommandHandler("my", cmd_my))
    app_tg.add_handler(CommandHandler("my无限次", cmd_set_new_keys))

    return app_tg


# ------------------- 背景任務：每日自動生成密钥 -------------------
async def daily_key_task() -> None:
    """
    這個 coroutine 會在每天 Beijing 10:00 觸發一次，
    自動生成兩個隨機密鑰並寫入 DB。若已經過 10:00，則等到下一天。
    """
    while True:
        now = datetime.now(BEIJING_TIMEZONE)
        # 計算距離今天 10:00 的秒數
        target = datetime.combine(now.date(), time(hour=KEY_RESET_HOUR, minute=0, second=0))
        if now >= target:
            target += timedelta(days=1)   # 若已經超過 10:00，則往後一天
        delay = (target - now).total_seconds()
        await asyncio.sleep(delay)

        # 觸發一次重置與生成
        await reset_daily_key_records()
        print("✅ 每日密钥已更新。")


# ------------------- 主入口 -------------------
async def main() -> None:
    """
    程式的總啟動流程：
      1️⃣ 確保 DB schema 已建立
      2️⃣ 創建 Telegram Bot 並掛載所有 handler
      3️⃣ 把 Telegram Application 交給 ad_completed 端點（用於回傳訊息）
      4️⃣ 開啟背景任務（每日密鑰更新）
      5️⃣ 用 uvicorn 啟動 FastAPI（端口由 $PORT 決定）
    """
    # Step 1 – 建立資料庫表格
    await ensure_schema()

    # Step 2 – 產生 Telegram Bot 實例
    telegram_app = await build_telegram_application()

    # Step 3 – 把 telegram_app 掛到 ad_completed，以便它能發送訊息
    # 這行必須在這裡設定，因為 ad_completed 是一個普通函式
    ad_completed.telegram_app = telegram_app   # type: ignore

    # Step 4 – 開啟每日自動生成密鑰的背景工作
    asyncio.create_task(daily_key_task())

    # Step 5 – 以 uvicorn 運行 FastAPI，端口由環境變數 $PORT 决定
    # 這裡使用 `"bot:app"` 因為我們把 FastAPI 實例命名為 `app`
    uvicorn.run("bot:app", host="0.0.0.0", port=8000)   # ← 這行是啟動 FastAPI 的關鍵


# ------------------------------------------------------------
# 直接執行 main() 以便本地測試
# ------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    # 方便本地開發時直接使用 python main.py
    asyncio.run(main())
