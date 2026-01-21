# src/models/point.py
# ------------------------------------------------------------
# 這個模組提供對 SQLite 表 `points` 的簡單讀寫操作。
# 表結構（在 src/db.ts 中建立）：
#   CREATE TABLE IF NOT EXISTS points (
#       user_id      BIGINT PRIMARY KEY,
#       balance      INTEGER NOT NULL DEFAULT 0,
#       last_sign    TEXT,                     -- YYYY‑MM‑DD，記錄最後一次簽到的日期
#       created_at   TIMESTAMP WITH TIME ZONE DEFAULT now()
#   );
# ------------------------------------------------------------
# 目前只需要以下四個函式：
#   • get_balance(user_id)          → 回傳目前的积分值
#   • add_points(user_id, points)   → 在原有餘額上加入 points
#   • get_user_point_info(user_id)  → 回傳完整資訊 (balance, last_sign, created_at)
#   • record_sign(user_id, earned)  → 記錄一次簽到獎勵，並更新 balance
# ------------------------------------------------------------

import os
from typing import Optional, Dict

import aiosqlite
import pytz

# 同樣從環境取得資料庫連線字串
DATABASE_URL: str = os.getenv("DATABASE_URL", "")

# 單例 Pool（懶漲 eager - 延遲到第一次使用時才真正建立）
_pool: Optional[aiosqlite.Connection] = None


async def get_connection() -> aiosqlite.Connection:
    """
    用全域的 `sqlite3` 連線（不使用 Pool，因為 `points` 表非常小，
    每次只會執行單一簡短的 INSERT/SELECT）。若還未建立連線就建立，
    並把 `row_factory` 設成 `sqlite3.Row` 以方便欄位名稱存取。
    """
    global _pool
    if _pool is None:
        _pool = await aiosqlite.connect(DATABASE_URL)
        _pool.row_factory = aiosqlite.Row
        # Neon 需要 sslmode=disable，直接在字串加上參數
        if "sslmode=" not in _pool.connection_params:  # simple guard
            _pool = await aiosqlite.connect(
                DATABASE_URL + "&sslmode=disable"
            )
            _pool.row_factory = aiosqlite.Row
    return _pool  # type: ignore[return-value]


# ------------------------------------------------------------
# 取得使用者目前的积分餘額
# ------------------------------------------------------------
async def get_balance(user_id: int) -> int:
    """
    回傳該 user_id 的 `balance` 欄位。若使用者不在表中則回傳 0。
    """
    conn = await get_connection()
    async with conn.execute(
        "SELECT balance FROM points WHERE user_id = ?;", (user_id,)
    ) as cur:
        row = await cur.fetchone()
        return row["balance"] if row else 0


# ------------------------------------------------------------
# 在使用者目前的 balance 上加入 points
# ------------------------------------------------------------
async def add_points(user_id: int, points: int) -> None:
    """
    先讀取目前的 balance，然後把 `points` 加上去，最後用
    INSERT OR REPLACE 把新的balance寫回去。
    """
    conn = await get_connection()
    current_balance = await get_balance(user_id)
    new_balance = current_balance + points
    await conn.execute(
        """
        INSERT OR REPLACE INTO points (user_id, balance)
        VALUES (?, ?);
        """,
        (user_id, new_balance),
    )
    await conn.commit()


# ------------------------------------------------------------
# 取得使用者的完整紀錄 (balance, last_sign, created_at)
# ------------------------------------------------------------
async def get_user_point_info(user_id: int) -> Optional[Dict[str, Any]]:
    """
    回傳 `{balance, last_sign, created_at}` 三個欄位的字典。
    若該使用者不存在則回傳 None。
    """
    conn = await get_connection()
    async with conn.execute(
        "SELECT balance, last_sign, created_at FROM points WHERE user_id = ?;",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


# ------------------------------------------------------------
# 記錄一次簽到並更新积分
#   - 如果是第一次簽到，獎勵固定 10 分（由外部呼叫端自行決定傳入的 earned 參數）
#   - 若已經簽過則不再更新（本函式假設 caller 已做過「是否當天簽過」的檢查）
# ------------------------------------------------------------
async def record_sign(user_id: int, earned: int) -> None:
    """
    這個函式負責把 `earned`（积分）寫入 `balance`，
    同時把 `last_sign` 更新為現在的日期（YYYY‑MM‑DD）。
    """
    conn = await get_connection()
    now_str = datetime.datetime.now(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d")
    await conn.execute(
        """
        INSERT OR REPLACE INTO points (user_id, balance, last_sign)
        VALUES (?, ?, ?);
        """,
        (user_id, earned, now_str),
    )
    await conn.commit()
