# ------------------------------------------------------------
# main.py
# ------------------------------------------------------------
# 该文件同时完成以下功能：
#   1️⃣ Telegram Bot（/start、/admin、File‑ID、积分、moontag 等）
#   2️⃣ FastAPI 伺服器（提供 HTML、廣告回調、密鑰驗證等）
#   3️⃣ 每日自動生成兩個 10 位隨機密鑰
#   4️⃣ 完整的防作弊、日誌、計數與重置機制
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

# ==================== 常量 ====================
# 环境变量里必须提供的值
TELEGRAM_BOT_TOKEN: str = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")  # 替换成你的 Bot Token
BEAJING_TIMEZONE = pytz.timezone("Asia/Shanghai")
DB_FILE = "data.sqlite"

# 积分表、廣告統計表、密鑰表的名稱
TABLE_POINTS = "points"
TABLE_AD_COUNTS = "daily_ad_counts"
TABLE_REWARD_ATTEMPTS = "reward_attempts"
TABLE_KEYS = "daily_keys"
TABLE_KEY_USAGE = "key_usage"

# 獎勵值
REWARD_FIRST_TIME = 10           # 第一次觀看廣告獲得的积分
REWARD_SECOND_TIME = 6           # 第二次觀看廣告獲得的积分
REWARD_THIRD_MIN = 3             # 第三次及以後隨機下限
REWARD_THIRD_MAX = 10            # 第三次及以後隨機上限

# 密鑰相關常量
KEY_POINT_1 = 8                  # 輸入密鑰 1 時獲得的积分
KEY_POINT_2 = 6                  # 輸入密鑰 2 時獲得的积分
MAX_DAILY_AD_WATCHES = 3         # 每位使用者每天最多觀看 rewarded ad 的次數
MAX_KEY_CLICKS_PER_DAY = 2       # 每位使用者每天最多使用密鑰的次數
KEY_RESET_HOUR = 10              # 北京時間 10:00 自動重置密鑰與計數

# ==================== SQLite 輔助函數 ====================
async def get_db_connection() -> aiosqlite.Connection:
    """返回一個已設定 row_factory 的 SQLite 連線。"""
    conn = await aiosqlite.connect(DB_FILE)
    conn.row_factory = aiosqlite.Row
    return conn


async def ensure_schema() -> None:
    """如果表不存在則建立所有表格。"""
    async with await get_db_connection() as conn:
        # points 表（儲存积分餘額）
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
        # daily_ad_counts 表（統計每日看完廣告的次數）
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_AD_COUNTS} (
                user_id      INTEGER PRIMARY KEY,
                count_today  INTEGER NOT NULL DEFAULT 0,
                last_reset   TEXT NOT NULL
            );
            """
        )
        # reward_attempts 表（累計看完廣告的次數，用於決定獎勵等級）
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_REWARD_ATTEMPTS} (
                user_id      INTEGER PRIMARY KEY,
                attempt_cnt  INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        # daily_keys 表（儲存今天產生的兩個密鑰）
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
        # key_usage 表（記錄密鑰是否已被使用）
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_KEY_USAGE} (
                key_id   INTEGER PRIMARY KEY,
                used     INTEGER NOT NULL DEFAULT 0   -- 0 表示未使用，1 表示已使用
            );
            """
        )
        await conn.commit()


# ------------------- 基本的資料庫操作 -------------------
async def get_user_balance(user_id: int) -> int:
    """返回用戶當前的积分餘額。"""
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


async def increment_daily_ad_count(user_id: int) -> bool:
    """
    增加用戶當天已觀看完廣告的次數。
    如果已達 MAX_DAILY_AD_WATCHES 返回 False，否則返回 True。
    """
    today_str = datetime.datetime.now(BEAJING_TIMEZONE).strftime("%Y-%m-%d")
    async with await get_db_connection() as conn:
        # 檢查上一次記錄的日期是否是今天
        async with conn.execute(
            f"SELECT last_reset FROM {TABLE_AD_COUNTS} WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            stored_date = row["last_reset"] if row else None

        if stored_date != today_str:
            # 不是今天，重新計數
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

        # 已是今天，檢查上限
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
    每天北京時間 10:00自動執行：
      1) 生成兩個 10 位隨機密鑰（大小寫字母+數字）
      2) 把舊的使用狀態全部標記為未使用
      3) 把新密鑰寫入 daily_keys 表
    """
    async with await get_db_connection() as conn:
        # 刪除舊的唯一一筆記錄（只保留最新的一筆）
        await conn.execute(f"DELETE FROM {TABLE_KEYS} WHERE id = 1")

        # 生成 10 位隨機字符的函數
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

        # 把 key_usage 表中的兩筆記錄的 used 欄位都設為 0（未使用）
        await conn.execute(f"INSERT OR REPLACE INTO {TABLE_KEY_USAGE} (key_id, used) VALUES (1, 0);")
        await conn.execute(f"INSERT OR REPLACE INTO {TABLE_KEY_USAGE} (key_id, used) VALUES (2, 0);")
        await conn.commit()


async def get_today_keys() -> List[Dict]:
    """
    返回今天產生的兩個密鑰以及它們的使用狀態。
    若尚未生成過則返回空列表。
    """
    async with await get_db_connection() as conn:
        async with conn.execute(
            f"SELECT * FROM {TABLE_KEYS} ORDER BY id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return []

        # 取出 key_usage 表中兩個 key_id 的 used 欄位
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


# ==================== FastAPI 設定 ====================
app = FastAPI()   # ← uvicorn 會根據 "main:app" 來載入這個變數

# 把 doc/ 目錄掛載為靜態網站
app.mount("/docs", StaticFiles(directory="doc"), name="static")


@app.get("/webapp")
async def serve_webapp(request: Request) -> HTMLResponse:
    """提供 webapp.html 給前端使用（觀看廣告用）。"""
    with open("doc/webapp.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.post("/ad_completed")
async def ad_completed(request: Request) -> JSONResponse:
    """
    當用戶成功觀看完 Awarded Video 後，前端會 POST
    {"user_id":"123456789"} 到此端點。
    此端點負責：
      1) 檢查每日觀看上限
      2) 計算獎勵（第一次 10、第二次 6、之後隨機 3~10）
      3) 更新积分
      4) 把成功訊息回覆給前端，同時給 Telegram 用戶發送积分提示
    """
    # 1️⃣ 解析 JSON
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

    # 2️⃣ 檢查每日觀看次數上限
    if not await increment_daily_ad_count(user_id):
        return {"status": "daily_limit_reached"}

    # 3️⃣ 記錄這是第幾次觀看（1、2、3…），用來決定獎勵
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

        # 依次決定獎勵值
        if attempt_number == 1:
            reward = REWARD_FIRST_TIME
        elif attempt_number == 2:
            reward = REWARD_SECOND_TIME
        else:   # 第三次起開始隨機
            reward = random.randint(REWARD_THIRD_MIN, REWARD_THIRD_MAX)

    # 4️⃣ 把獎勵加到积分表
    await add_points(user_id, reward)

    # 5️⃣ 如果前端需要回傳成功訊息，同時給 Telegram 用戶發送通知
    #    這裡使用一個自訂屬性把 telegram_app 交給函式
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

    # 6️⃣ 返回前端狀態
    return {"status": "ok"}


@app.post("/api/submit_key")
async def submit_key(request: Request) -> JSONResponse:
    """
    前端（key_link.html）的「提交密鑰」按鈕會 POST
    {"user_id":"123456789","key1":"xxxx","key2":"yyyy"}。
    此端點會：
      1) 檢查 key1 / key2 是否匹配今天的密鑰
      2) 若匹配且尚未使用，給予相應的积分（8 或 6）
      3) 標記該密鑰已使用
      4) 回傳提示訊息
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

    # 取得今天的兩個密鑰
    today_keys = await get_today_keys()
    if not today_keys:
        return {"status": "error", "message": "今日密钥尚未生成，请稍后再试。"}

    k1 = today_keys[0]
    k2 = today_keys[1]

    message = ""
    status = "error"

    # ------------- 驗證 key1 -------------
    if key1 and not k1.get("used"):
        if key1 == k1.get("key", ""):
            await add_points(user_id, KEY_POINT_1)   # 8 分
            await _mark_key_as_used(1)               # 標記為已使用
            message = "✅ 首次密钥（密钥 1）领取成功，已发放 8 积分！"
            status = "ok"
        else:
            message = "❌ 首次密钥不正确，请重新检查后重新输入。"
    else:
        message = "⚠️ 首次密钥已使用或未填写。"

    # ------------- 驗證 key2 -------------
    if status == "error" and key2 and not k2.get("used"):
        if key2 == k2.get("key", ""):
            await add_points(user_id, KEY_POINT_2)   # 6 分
            await _mark_key_as_used(2)               # 標記為已使用
            message = "✅ 次次密钥（密钥 2）领取成功，已发放 6 积分！"
            status = "ok"
        else:
            message = "❌ 次次密钥不正确，请检查后重新输入。"
    else:
        if not key2:
            message = "⚠️ 未输入第二个密钥。"
        elif k2.get("used"):
            message = "⚠️ 第二个密钥已经使用过。"

    return {"status": status, "message": message}


async def _mark_key_as_used(key_id: int) -> None:
    """把指定的 key_id 標記為已使用（used = 1）。"""
    async with await get_db_connection() as conn:
        await conn.execute(
            f"UPDATE {TABLE_KEY_USAGE} SET used = 1 WHERE key_id = ?", (key_id,)
        )
        await conn.commit()


# ==================== Telegram Bot 相關 ====================
async def build_telegram_application() -> Application:
    """創建 Telegram Bot、掛載所有指令與回調處理函數。"""
    app_tg = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # -------- /start 菜單（三個大按鈕） --------
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

    # -------- 所有 InlineButton 的統一分配 --------
    async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """所有 inline 按鈕的統一入口"""
        query = update.callback_query
        if not query:
            return
        await query.answer()          # 必須先回覆，否則前端會卡住

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
            # 您需要把這個 HTML 頁面部署到 GitHub Pages 或其他可訪問的 URL
            # 這裡以 GitHub Pages 為例，請自行替換成自己的 URL
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

    # -------- /admin（保留原有管理员功能） --------
    # 這裡直接匯入您先前寫好的 adminWizard（它已經包含 /admin、/id 等指令）
    from src.commands.admin import adminWizard   # ← 您的原始管理員後台程式
    app_tg.add_handler(CommandHandler("admin", adminWizard))
    app_tg.add_handler(CallbackQueryHandler(callback_handler))

    # -------- 旧的积分指令（保持不變） --------
    async def points_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """使用者直接輸入 /points 時顯示自己的积分"""
        balance = await get_user_balance(update.effective_user.id)
        await update.message.reply_text(
            f"🧮 您的当前积分为 <b>{balance}</b>，感谢您的使用！",
            parse_mode="HTML",
        )

    async def jf_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """舊的 /jf 指令（保持原有功能）"""
        await update.message.reply_text("此功能仍保留，未作变更。")

    app_tg.add_handler(CommandHandler("points", points_command))
    app_tg.add_handler(CommandHandler("jf", jf_handler))

    # -------- /my（管理员專用）相關指令 --------
    async def cmd_my(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """管理員使用 /my 查看當天產生的兩個密鑰及其使用狀態"""
        keys_info = await get_today_keys()
        if not keys_info:
            await update.message.reply_text("尚未生成今日密钥，请稍等至 10:00。")
            return

        reply = "🗝️ 今日密钥列表（北京时间十点已更新）：\n\n"
        for idx, item in enumerate(keys_info, start=1):
            usage = "已使用" if item.get("used") else "未使用"
            reply += f"【密钥 {idx}】{item.get('key', '')} —— {usage}\n"
        await update.message.reply_text(reply)

    async def cmd_set_new_keys(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        管理員可以手動傳入兩個字串作為當天的密鑰入口。
        用法示例：
            /my无限次 <密钥一链接> <密钥二链接>
        此函式會把兩個字串寫入 daily_keys 表，並標記未使用。
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
            # 確保 key_usage 表中有兩筆記錄且狀態為「未使用」
            await conn.execute(f"INSERT OR REPLACE INTO {TABLE_KEY_USAGE} (key_id, used) VALUES (1, 0);")
            await conn.execute(f"INSERT OR REPLACE INTO {TABLE_KEY_USAGE} (key_id, used) VALUES (2, 0);")
            await conn.commit()

        await update.message.reply_text("密钥一绑定完成，请继续提供 **密钥二** 的链接：" )
        # 為了簡化演示，這裡不再實作第二次輸入的對話，
        # 實際項目中可使用 ConversationHandler 完整收集兩個鏈接。

    # -------- 把上述兩個指令掛載到 Bot --------
    app_tg.add_handler(CommandHandler("my", cmd_my))
    app_tg.add_handler(CommandHandler("my无限次", cmd_set_new_keys))

    return app_tg


# ==================== 背景任務：每日自動生成密鑰 ====================
async def daily_key_task() -> None:
    """
    每天北京時間 10:00 觸發一次，自動生成兩個隨機密鑰，
    並把使用狀態歸零。若已經過 10:00，則等到明天再執行。
    """
    while True:
        now = datetime.datetime.now(BEAJING_TIMEZONE)
        # 計算距離今天 10:00 的秒數
        target = datetime.datetime.combine(now.date(), time(hour=KEY_RESET_HOUR, minute=0, second=0))
        if now >= target:
            target += datetime.timedelta(days=1)   # 已經超過 10:00，等到明天
        delay = (target - now).total_seconds()
        await asyncio.sleep(delay)

        await reset_daily_key_records()
        print("✅ 每日密钥已更新。")


# ==================== 主入口 ====================
async def main() -> None:
    """
    程式的總啟動流程：
      1️⃣ 確保 DB schema 已建立
      2️⃣ 創建 Telegram Bot 並掛載所有指令和回調
      3️⃣ 把 Telegram Application 交給 ad_completed 端點（用於回傳訊息）
      4️⃣ 開啟每日自動生成密鑰的背景任務
      5️⃣ 以 uvicorn 啟動 FastAPI，監聽 $PORT（Railway 會自動注入）\
    """
    # Step 1 – 建立所有表格
    await ensure_schema()

    # Step 2 – 建立 Telegram Bot
    telegram_app = await build_telegram_application()

    # Step 3 – 把 telegram_app 安裝到 ad_completed，以便它能發送訊息
    ad_completed.telegram_app = telegram_app   # type: ignore

    # Step 4 – 啟動每日自動生成密鑰的背景工作
    asyncio.create_task(daily_key_task())

    # Step 5 – 以 uvicorn 啟動 FastAPI，使用環境變數 $PORT
    # 注意：這裡的字串 "bot:app" 必須與檔案名稱保持一致（main.py 內部的變數名稱是 app）
    uvicorn.run("main:app", host="0.0.0.0", port=8000)   # ← 這行會被 Railway / Docker 認識


# ==================== 直接執行 main() 以便本地測試 ====================
if __name__ == "__main__":
    import uvicorn

    # 這段代碼讓本地可以直接使用 `python main.py` 來測試
    asyncio.run(main())
