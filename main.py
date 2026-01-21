# ------------------------------------------------------------
# bot.py
# ------------------------------------------------------------
# 這個檔案同時完成：
#   •  Telegram Bot（/start、/admin、File‑ID、积分、 moontag 等）
#   •  FastAPI 伺服器（提供 HTML、廣告回調、密鑰驗證等）
# ------------------------------------------------------------

import asyncio
import json
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
    CommandHandler as TGCommandHandler,
    MessageHandler,
    filters,
)

# ------------------------------------------------------------
# 0️⃣ 全局定義與參數
# ------------------------------------------------------------

# ---- Telegram 基本參數 -------------------------------------------------
# 這裡的 Token 必須在 Railway 環境變數中設置，或在程式碼裡直接寫入（僅作測試用）
TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"   # ← 請自行替換

# ---- 時區、獎懋常數 --------------------------------------------------
BEIJING_TIMEZONE = pytz.timezone("Asia/Shanghai")

# 積分表與密鑰表使用的 SQLite 檔案路徑
DB_FILE = "data.sqlite"

# 积分表欄位
TABLE_POINTS = "points"
TABLE_AD_COUNTS = "daily_ad_counts"
TABLE_REWARD_ATTEMPTS = "reward_attempts"
TABLE_KEYS = "daily_keys"                # 保存每天生成的兩個密鑰
TABLE_KEY_USAGE = "key_usage"            # 記錄密鑰是否已使用

# 獎勵值
REWARD_FIRST_TIME = 10                     # 第一次觀看廣告獲得的积分
REWARD_SECOND_TIME = 6                     # 第二次觀看廣告獲得的积分
REWARD_THIRD_MIN = 3                       # 第三次隨機下限
REWARD_THIRD_MAX = 10                      # 第三次隨機上限

# 密鑰相關
KEY_POINT_1 = 8                            # 密鑰 1（第一次點擊）可得的积分
KEY_POINT_2 = 6                            # 密鑰 2（第二次點擊）可得的积分
# 實際的密鑰值會在每天北京時間 10:00 自動生成

# 防作弊與重置
MAX_DAILY_AD_WATCHES = 3                  # 每位使用者每天最多看 3 次廣告
MAX_KEY_CLICKS_PER_DAY = 2                # 每位使用者每天最多使用兩次密鑰
KEY_RESET_HOUR = 10                       # 每天凌晨 10:00 自動重置相關計數

# ------------------------------------------------------------
# 1️⃣ SQLite 連線與表結構
# ------------------------------------------------------------

async def get_db_connection() -> aiosqlite.Connection:
    """取得 SQLite 連線，自動創建檔案若不存在。"""
    conn = await aiosqlite.connect(DB_FILE)
    conn.row_factory = aiosqlite.Row
    return conn


async def ensure_schema() -> None:
    """確保所有必要的表都存在。"""
    async with await get_db_connection() as conn:
        # points 表：儲存用戶的總积分與最後一次簽到日期
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_POINTS} (
                user_id          INTEGER PRIMARY KEY,
                balance          INTEGER NOT NULL DEFAULT 0,
                last_sign_date   TEXT,                     -- 格式 YYYY‑MM‑DD
                created_at       TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )

        # daily_ad_counts 表：記錄每日看完廣告的次數
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_AD_COUNTS} (
                user_id      INTEGER PRIMARY KEY,
                count_today  INTEGER NOT NULL DEFAULT 0,
                last_reset   TEXT NOT NULL               -- 格式 YYYY‑MM‑DD
            );
            """
        )

        # reward_attempts 表：記錄用戶看完廣告的次數（1、2、3）
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_REWARD_ATTEMPTS} (
                user_id      INTEGER PRIMARY KEY,
                attempt_cnt  INTEGER NOT NULL DEFAULT 0
            );
            """
        )

        # daily_keys 表：保存每天生成的兩個密鑰（10 位字母數字）
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_KEYS} (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                key1         TEXT,
                key2         TEXT,
                generated_at TEXT NOT NULL               -- 格式 YYYY‑MM‑DD HH:MM:SS
            );
            """
        )

        # key_usage 表：記錄當前是否已使用過密鑰（防止重複領取）
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_KEY_USAGE} (
                key_id       INTEGER PRIMARY KEY,
                used         INTEGER NOT NULL DEFAULT 0   -- 0 表示未使用，1 表示已使用
            );
            """
        )
        await conn.commit()


async def get_user_balance(user_id: int) -> int:
    """返回用戶的當前积分餘額。"""
    async with await get_db_connection() as conn:
        async with conn.execute(
            f"SELECT balance FROM {TABLE_POINTS} WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row["balance"] if row else 0


async def add_points(user_id: int, points: int) -> None:
    """向用戶的积分表中加入 points 點數。"""
    async with await get_db_connection() as conn:
        async with conn.execute(
            f"""
            INSERT OR REPLACE INTO {TABLE_POINTS} (user_id, balance)
            VALUES (?, ?)
            """,
            (user_id, get_user_balance(user_id) + points),
        )
        await conn.commit()


async def get_daily_ad_count(user_id: int) -> int:
    """返回用戶今日已看完廣告的次數。"""
    async with await get_db_connection() as conn:
        async with conn.execute(
            f"SELECT count_today FROM {TABLE_AD_COUNTS} WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            return row["count_today"] if row else 0


async def increment_daily_ad_count(user_id: int) -> bool:
    """
    增加今日看完廣告的次數，並返回是否允許（未達上限）。
    若已達上限返回 False，若已經過新一天則自動把計數歸零。
    """
    today_str = datetime.now(BEIJING_TIMEZONE).strftime("%Y-%m-%d")
    async with await get_db_connection() as conn:
        # 檢查上一次記錄的日期是否為今天
        async with conn.execute(
            f"SELECT last_reset FROM {TABLE_AD_COUNTS} WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            stored_date = row["last_reset"] if row else None

        if stored_date != today_str:
            # 不是今天，重置计数
            async with conn.execute(
                f"""
                INSERT OR REPLACE INTO {TABLE_AD_COUNTS} (user_id, count_today, last_reset)
                VALUES (?, 1, ?)
                """,
                (user_id, today_str),
            )
            return True

        # 已經是今天，檢查上限
        async with conn.execute(
            f"SELECT count_today FROM {TABLE_AD_COUNTS} WHERE user_id = ?",
            (user_id,),
        ) as cur:
            current = await cur.fetchone()
            if current["count_today"] >= MAX_DAILY_AD_WATCHES:
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
    每天北京時間 10:00 自動執行：生成兩個新密鑰、把上一天的使用記錄歸零。
    """
    async with await get_db_connection() as conn:
        # 先把今天的舊密鑰刪除
        await conn.execute(f"DELETE FROM {TABLE_KEYS} WHERE id = 1")  # 只保留一條紀錄
        # 把 key_usage 表的所有已使用標記重置
        await conn.execute(f"UPDATE {TABLE_KEY_USAGE} SET used = 0")
        # 生成兩個隨機的 10 位 alphanumeric 大小寫+數字
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
        # 設置 key_usage 表中的每個 key_id 為 0（未使用）
        await conn.execute(f"INSERT INTO {TABLE_KEY_USAGE} (key_id) VALUES (1);")
        await conn.execute(f"INSERT INTO {TABLE_KEY_USAGE} (key_id) VALUES (2);")
        await conn.commit()


async def get_today_keys() -> List[Dict]:
    """返回今天生成的兩個密鑰以及它們的使用狀態。"""
    async with await get_db_connection() as conn:
        # 取出唯一的一條記錄（id 永遠是 1，因為我們只插入一次）
        async with conn.execute(f"SELECT * FROM {TABLE_KEYS} ORDER BY id DESC LIMIT 1") as cur:
            row = await cur.fetchone()
            if row is None:
                return []   # 尚未生成
        # 查詢兩個 key_usage 行的 used 狀態
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
                "used_1": usage_info[0]["used"],
                "key_id": 1,
            },
            {
                "key": row["key2"] if row["key2"] else "",
                "used_2": usage_info[1]["used"],
                "key_id": 2,
            },
        ]


# ------------------------------------------------------------
# 2️⃣ 积分功能（保持不變）
# ------------------------------------------------------------

# 這裡不再重新寫積分的 Wizard，因為它已在前一次回覆中完成，
# 只需要在這裡提供一個簡單的指令讓管理員查看今日密鑰即可。
#（實際的积分命令在前一次回覆的 adminScene 中已實現）


# ------------------------------------------------------------
# 3️⃣ Telegram Bot 基礎功能
# ------------------------------------------------------------

async def build_telegram_application() -> Application:
    """建立 Telegram Bot 的 Application 並掛載所有 handler。"""
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # ------------------- /start 菜單 -------------------
    async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/start 命令顯示三個主菜單按鈕。"""
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text="開始驗證", callback_data="menu_verify"
                    ),
                    InlineKeyboardButton(
                        text="积分", callback_data="menu_points"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="開業活動", callback_data="menu_campaign"
                    ),
                ],
            ]
        )
        if update.callback_query:          # 來自 inline 按鈕的點擊
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                "👋 欢迎使用本机器人！请选择下方功能：", reply_markup=keyboard
            )
        else:                               # 直接輸入 /start
            await update.message.reply_text(
                "👋 欢迎使用本机器人！请选择下方功能：", reply_markup=keyboard
            )

    # ------------------- 回调查询分发 -------------------
    async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """針對所有 inline 按鈕的統一分發。"""
        query = update.callback_query
        if not query:
            return
        await query.answer()      # 必須先回覆，否則前端會一直顯示 loading

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
            # 假設部署時的 GitHub 頁面 URL 為
            # https://<your_github_user>.github.io/<repo>/docs/webapp.html
            github_page = "https://YOUR_GITHUB_USERNAME.github.io/YOUR_REPO_NAME/docs/webapp.html"
            encoded_user_id = "?user_id=" + str(query.from_user.id)
            full_url = github_page + encoded_user_id

            await query.edit_message_text(
                "🎉 正在打開活動中心，請稍等…",
                reply_markup=InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton(
                            text="按鈕二 取得密钥", url=full_url
                        )
                    ]]
                ),
            )
        else:
            await query.edit_message_text("未知的按鈕操作，请重新選擇。")

    # ------------------- /admin 相關（保持不變） -------------------
    # 這裡直接引用前一次回覆中提供的 adminWizard（不再重寫）。
    # 假設 adminWizard 已在另一個檔案中定義並匯出。
    # 這邊只做占位示例，實際內容保持不變。
    from src.commands.admin import adminWizard  # 之前的 admin 逻辑

    app.add_handler(TGCommandHandler("admin", adminWizard))
    app.add_handler(CallbackQueryHandler(callback_handler))

    # ------------------- 积分指令（展示积分） -------------------
    async def points_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """使用者輸入 /points 時顯示自己的积分值。"""
        user_id = update.effective_user.id
        balance = await get_user_balance(user_id)
        await update.message.reply_text(
            f"🧮 您的當前积分為 <b>{balance}</b>，感謝您的使用！",
            parse_mode="HTML",
        )

    app.add_handler(TGCommandHandler("points", points_command))

    # ------------------- /jf（原积分页面） -------------------
    async def jf_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """使用者點擊 /jf 進入积分页面（與原功能相同）。"""
        await update.message.reply_text("此功能保持不變，將在後續實现。")
    app.add_handler(TGCommandHandler("jf", jf_handler))

    return app


# ------------------------------------------------------------
# 4️⃣ FastAPI 部分（提供靜態 HTML、廣告回調、密钥驗證）
# ------------------------------------------------------------

fastapi_app = FastAPI()

# 把 doc/ 目錄掛載為靜態資源
fastapi_app.mount("/docs", StaticFiles(directory="doc"), name="static")


# ------------------- 讀取 webapp.html -------------------
@fastapi_app.get(
    "/webapp",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def serve_webapp(request: Request) -> HTMLResponse:
    """提供 webapp.html，給廣告 SDK 使用。"""
    with open("doc/webapp.html", "r", encoding="utf-8") as f:
        content = f.read()
    return HTMLResponse(content=content)


# ------------------- /ad_completed（廣告完成回調） -------------------
fastapi_app.post(
    "/ad_completed",
    response_class=JSONResponse,
    include_in_schema=False,
)
async def ad_completed(request: Request) -> Dict[str, str]:
    """
    當用戶成功觀看獎勵廣告後，前端會向此網址 POST user_id.
    程式會檢查每日看廣告上限、計算獎勵、更新积分、並給用戶 Telegram 回覆。
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    user_id_str = payload.get("user_id")
    if not user_id_str:
        raise HTTPException(status_code=400, detail="Missing user_id")
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="user_id is not integer")

    # ---------- 1️⃣ 判斷是否可以再領取 ----------
    if not await increment_daily_ad_count(user_id):
        # 已達每日上限
        return {"status": "daily_limit_reached"}

    # ---------- 2️⃣ 計算獎勵 ----------
    # 記錄使用次數
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

        # 依照次數決定獎勵
        if attempt_number == 1:
            reward = REWARD_FIRST_TIME
        elif attempt_number == 2:
            reward = REWARD_SECOND_TIME
        else:   # 第三次及以後使用隨機 3~10
            reward = random.randint(REWARD_THIRD_MIN, REWARD_THIRD_MAX)

    # ---------- 3️⃣ 更新积分 ----------
    await add_points(user_id, reward)

    # ---------- 4️⃣ 向 Telegram 發送成功訊息 ----------
    # 這裡需要拿到全域的 telegram Application（稍後會把它掛在函式上）
    if hasattr(ad_completed, "telegram_app"):
        app: Application = ad_completed.telegram_app   # type: ignore
        await app.bot.send_message(
            chat_id=user_id,
            text=(
                f"✅ 恭喜您完成观看视频并获得 <b>{reward}</b> 积分！\n"
                f"您的积分已更新。"
            ),
            parse_mode="HTML",
        )

    # ---------- 5️⃣ 返回前端狀態 ----------
    return {"status": "ok"}


# ------------------- /api/submit_key（密鑰驗證與領取） -------------------
fastapi_app.post(
    "/api/submit_key",
    response_class=JSONResponse,
    include_in_schema=False,
)
async def submit_key(request: Request) -> Dict[str, str]:
    """
    這個端點由 key_link.html 的表單提交時呼叫。
    參數包括 key1、key2（用戶輸入的密鑰）以及 user_id。
    程式會判斷：
      • 密鑰是否屬於今天生成的那兩個
      • 是否已經使用過
      • 正確的話給予相應积分（8 或 6）
    之後把使用狀態標記為已使用，並返回提示信息。
    """
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    user_id_str = data.get("user_id")
    key1 = data.get("key1", "").strip()
    key2 = data.get("key2", "").strip()

    if not user_id_str:
        raise HTTPException(status_code=400, detail="Missing user_id")
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="user_id is not integer")

    # 1️⃣ 先取得今天的密鑰列表
    today_keys = await get_today_keys()
    k1_info = today_keys[0] if len(today_keys) > 0 else {}
    k2_info = today_keys[1] if len(today_keys) > 1 else {}

    # 記錄回傳訊息
    message = ""
    status = "error"

    # ---------- 檢查第一個密鑰 ----------
    if key1 and not k1_info.get("used_1"):
        if key1 == k1_info.get("key", ""):
            # 正確，且未使用
            await add_points(user_id, KEY_POINT_1)          # 8 分
            await _mark_key_as_used(1)                       # 標記為已用
            message = "✅ 首次密钥（密钥 1）领取成功，已發放 8 积分！"
            status = "ok"
        else:
            message = "❌ 首次密钥不正確，請重新檢查。"
    else:
        message = "⚠️ 首次密钥已使用或未填寫。"

    # ---------- 檢查第二個密鑰 ----------
    if status == "error" and key2 and not k2_info.get("used_2"):
        if key2 == k2_info.get("key", ""):
            # 正確，且未使用
            await add_points(user_id, KEY_POINT_2)          # 6 分
            await _mark_key_as_used(2)                       # 標記為已用
            message = "✅ 次次密钥（密钥 2）领取成功，已發放 6 积分！"
            status = "ok"
        else:
            message = "❌ 次次密钥不正確，请检查输入。"
    else:
        if not key2:
            message = "⚠️ 未輸入第二個密钥。"
        elif k2_info.get("used_2"):
            message = "⚠️ 第二個密钥已經使用過了。"

    # ---------- 返回結果 ----------
    return {"status": status, "message": message}


# ----------輔助函數：把某個 key_id 標記為已使用 ----------
async def _mark_key_as_used(key_id: int) -> None:
    async with await get_db_connection() as conn:
        await conn.execute(
            f"UPDATE {TABLE_KEY_USAGE} SET used = 1 WHERE key_id = ?", (key_id,)
        )
        await conn.commit()


# ------------------- 任務背景：每日自動生成密鑰與重置 -------------------
async def daily_key_task() -> None:
    """
    這個 coroutine 每天北京時間 10:00 執行一次：
      1. 生成兩個隨機的 10 位密钥
      2. 把上一天的使用狀態歸零
      3. 把新密鑰保存至資料庫
    """
    while True:
        now = datetime.now(BEIJING_TIMEZONE)
        # 計算距離今天 10:00 的秒數
        next_run = datetime.combine(
            now.date(), time(hour=KEY_RESET_HOUR, minute=0, second=0)
        )
        if now >= next_run:
            # 已經超過 10:00，但如果剛過午夜仍在同一天，我們需要等到明天
            next_run += timedelta(days=1)
        delay = (next_run - now).total_seconds()
        await asyncio.sleep(delay)

        # 生成新密鑰、更新 DB
        await reset_daily_key_records()
        print("✅ 每日密钥已更新。")


# ------------------------------------------------------------
# 5️⃣ 管理員專屬指令（/my、/my無限次）
# ------------------------------------------------------------

# 為了方便管理，我把所有與「今日密鑰」相關的指令放在一個小函式裡。
# 這些指令不會干擾原有的 admin 功能，只是額外掛載。

async def admin_only(func):
    """簡單的裝飾器，確保只有 admin_id 能執行。"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        admin_ids = (process_env("ADMIN_IDS") or "").split(",")
        if str(update.effective_user.id) not in admin_ids:
            await update.message.reply_text("❌ 您不是管理员，沒有權限執行此指令。")
            return
        await func(update, context)
    return wrapper


def process_env(key: str) -> str:
    """從環境變數讀取字串，若不存在返回空字串。"""
    import os
    return os.getenv(key, "")


@admin_only
async def cmd_my(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /my  指令顯示今日生成的兩個密钥以及它們的使用狀態。
    /my無限次 同樣顯示，但可以讓管理員在不重置的情況下多次查看。
    """
    keys_info = await get_today_keys()
    if not keys_info:
        await update.message.reply_text("尚未生成今日密钥，請稍等至 10:00。")
        return

    reply = "🗝️ 今日密钥列表（北京時間十點已更新）：\n\n"
    for idx, item in enumerate(keys_info, start=1):
        usage = "已使用" if item.get("used") else "未使用"
        reply += f"【密钥 {idx}】{item.get('key', '')} —— {usage}\n"

    await update.message.reply_text(reply)


@admin_only
async def cmd_set_new_keys(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    管理員可以手動輸入兩段 URL（或任何文字）作為新密钥的入口。
    流程如下：
      1. 先提示輸入「密钥一」的鏈接
      2. 驗證通過後顯示「密钥一绑定完成」
      3. 再提示輸入「密钥二」的鏈接
      4. 驗證通過後顯示「密钥二绑定完成」
    這樣可以在不走每日自動生成流程的情況下，臨時設置新鏈接。
    """
   await update.message.reply_text("🔎 請輸入 **密钥一** 的鏈接（低於 100 字），按下回车发送：")

    # 為了簡化，我們直接使用回覆按鈕的方式（在這個示例裡不實現 UI，只等文字）
    # 實際機器人需要使用 ConversationHandler 來收集多條訊息，
    # 這裡簡化為「管理員直接在同一條訊息後輸入兩個 URL」。
    # 為了不引入過多依賴，這裡僅示意概念，實際可自行擴充。

    # 下面的實作直接從 context.user_data 讀取已發送的文字
    # 簡化流程：假設管理員一次性把兩個 URL 直接寫在指令後面
    # 例如： /my無限次 https://example.com/key1 https://example.com/key2
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "⚠️ 使用方式：/my無限次 <密钥一链接> <密钥二链接>"
        )
        return

    link1, link2 = args[1], args[2]

    # 簡易驗證：只要不為空即通過
    if not link1 or not link2:
        await update.message.reply_text("❌ 鏈接不能為空。")
        return

    # 把這兩個鏈接寫入資料庫的 key_usage 表中，標記為「未使用」，
    # 同時保存到 daily_keys 表的 key1 / key2 欄位（這樣前端也能讀取）。
    async with await get_db_connection() as conn:
        await conn.execute(
            f"""
            INSERT INTO {TABLE_KEYS} (key1, key2, generated_at)
            VALUES (?, ?, ?)
            """,
            (link1, link2, datetime.now(BEIJING_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")),
        )
        # 確保 key_usage 表中有兩筆記錄
        await conn.execute("INSERT OR REPLACE INTO {TABLE_KEY_USAGE} (key_id, used) VALUES (1, 0);")
        await conn.execute("INSERT OR REPLACE INTO {TABLE_KEY_USAGE} (key_id, used) VALUES (2, 0);")
        await conn.commit()

    await update.message.reply_text(
        "密钥一绑定完成，請繼續輸入 **密钥二** 的鏈接："
    )
    # 等待第二次輸入（實際上可以用 ConversationHandler，這裡直接說明）
    await update.message.reply_text(
        "⚠️ 再次發送指令時把第二個鏈接放在參數後面：/my無限次 <link2>"
    )


# ----------掛載這兩個指令到 Application ----------
# 在 build_telegram_application() 內部加入以下兩行
# app.add_handler(TGCommandHandler("my", cmd_my))
# app.add_handler(TGCommandHandler("my 무한번", cmd_set_new_keys))

# ------------------------------------------------------------
# 6️⃣ 主程式入口 – 同時啟動 Bot 與 FastAPI
# ------------------------------------------------------------

async def main() -> None:
    """
    程式的總入口：
      1. 初始化資料庫結構
      2. 創建 Telegram Application 並掛載所有 handler
      3. 設置每天 10:00 自動生成密鑰的背景任務
      4. 以 uvicorn 啟動 FastAPI，端口 8000
      5. 同時運行 Telegram polling（非阻塞）與 FastAPI
    """
    # Step 1 – 確保資料庫與表 exist
    await ensure_schema()

    # Step 2 – 建立 Telegram Bot 的 Application
    telegram_app = await build_telegram_application()

    # 把 telegram_app掛在 ad_completed 里，以便它能發送訊息
    fastapi_app.view("/ad_completed")(lambda *args, **kwargs: ad_completed)  # dummy just to attach later
    # 直接把全域變量掛上去
    ad_completed.telegram_app = telegram_app   # type: ignore

    # Step 3 – 設置每日自動生成密鑰的背景工作
    asyncio.create_task(daily_key_task())

    # Step 4 – 以 uvicorn 啟動 FastAPI（非阻塞）
    uvicorn_task = asyncio.create_task(
        uvicorn.run(
            "bot:fastapi_app",
            host="0.0.0.0",
            port=8000,
            log_level="warning",
        )
    )

    # Step 5 – 啟動 Telegram 的 polling（非阻塞）
    polling_task = asyncio.create_task(telegram_app.run_polling())

    # 等待兩者都結束（通常是被系統終止）
    try:
        await asyncio.gather(polling_task, uvicorn_task)
    except (KeyboardInterrupt, SystemExit):
        # 伏筆：打斷時優雅關閉
        await telegram_app.shutdown()
        uvicorn_task.shutdown()
        print("✅ Bot 与 FastAPI 已安全關閉。")

# ------------------------------------------------------------
# 7️⃣ 直接執行程式
# ------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    import os

    # 為了在 Railway / Render 等平台上能正確讀取環境變數
    # 把所有環境變數印在 console 方便調試
    print("=== 環境變數 ===")
    for k, v in os.environ.items():
        print(f"{k} = {v}")

    asyncio.run(main()
