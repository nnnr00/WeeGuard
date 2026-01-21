# src/models/file.py
# ------------------------------------------------------------
# 這個模組實作對 PostgreSQL（Neon）表 file_records 的
#   • 插入 (insert_file)  
#   • 查詢全部或限制筆數 (list_files)  
#   • 依 ID 取出單筆資料 (get_file_by_id)  
#   • 刪除資料 (delete_file)  
#   • 一次性刪除全部紀錄 (delete_all_records)   ← 供 admin 管理員後台使用
# ------------------------------------------------------------
# 表結構（已在 src/db.ts 中建立）
#   CREATE TABLE IF NOT EXISTS file_records (
#       id           SERIAL PRIMARY KEY,
#       user_id      BIGINT NOT NULL,
#       username     TEXT,
#       file_id      TEXT   NOT NULL,
#       text         TEXT,
#       created_at   TIMESTAMP WITH TIME ZONE DEFAULT now()
#   );
# ------------------------------------------------------------

import os
from typing import Any, Dict, Optional

import aiosqlite
import pytz

# 讀取 Railway 環境變數中的資料庫連線字串
DATABASE_URL: str = os.getenv("DATABASE_URL", "")

# 建立一個只會在第一次呼叫時真正開啟連線的工廠函式
_aiosqlite_pool: Optional[aiosqlite.Pool] = None


async def get_pool() -> aiosqlite.Pool:
    """
    取得全域的 aiosqlite Pool。若還未建立則根據 DATABASE_URL 建立，
    並把 pool 存在 module‑level 的變數裡，之後直接回傳，避免每次呼叫都重新連線。
    """
    global _aiosqlite_pool
    if _aiosqlite_pool is None:
        _aiosqlite_pool = aiosqlite.connect(DATABASE_URL)
        _aiosqlite_pool.row_factory = aiosqlite.Row
        # Neon 需要 ssl reject_unauthorized=False，這裡直接在 connection string
        # 加入?sslmode=disable（如果你的連線字串已包含這段可以省略）
        if "sslmode=" not in _aiosqlite_pool.docroot:  # 簡易檢測
            _aiosqlite_pool = aiosqlite.connect(
                DATABASE_URL + "&sslmode=disable"
            )
            _aiosqlite_pool.row_factory = aiosqlite.Row
    return _aiosqlite_pool  # type: ignore[return-value]


# ------------------------------------------------------------
# 插入一筆 file_records 記錄
# ------------------------------------------------------------
async def insert_file(
    user_id: int,
    file_id: str,
    text: Optional[str] = None,
) -> Dict[str, Any]:
    """
    參數說明
        user_id      → 產生此紀錄的 Telegram user_id
        file_id      → Telegram 為這張圖/檔案所產生的 file_id
        text         → 可選的文字說明，若提供會一起存下來

    回傳值
        dict 包含插入後產生的 `id` 與 `created_at`
    """
    pool = await get_pool()
    async with pool.execute(
        """
        INSERT INTO file_records (user_id, username, file_id, text)
        VALUES ($1, $2, $3, $4)
        RETURNING id, created_at;
        """,
        (user_id, None, file_id, text),
    ) as cur:
        row = await cur.fetchone()
        await pool.commit()
        return {"id": row["id"], "created_at": row["created_at"]}


# ------------------------------------------------------------
# 取得最近 *limit* 筆 file_records（預設 10）
# ------------------------------------------------------------
async def list_files(limit: int = 10) -> list:
    """
    回傳最近 `limit` 筆紀錄，依 `created_at` 降序排列。
    每筆回傳的字典最少會有 `id、user_id、file_id、text、created_at`
    (如果 column 為 NULL 會顯示 None)。
    """
    pool = await get_pool()
    async with pool.execute(
        f"""
        SELECT id, user_id, username, file_id, text, created_at
        FROM file_records
        ORDER BY created_at DESC
        LIMIT $1;
        """,
        (limit,),
    ) as cur:
        rows = await cur.fetchall()
        # 把每一列轉成純字典（方便給前端或其他模組使用）
        return [dict(row) for row in rows]


# ------------------------------------------------------------
# 依 ID 取出單筆紀錄
# ------------------------------------------------------------
async def get_file_by_id(record_id: int) -> Optional[Dict[str, Any]]:
    """
    依主鍵 `id` 取出完整資料，若找不到回傳 None。
    """
    pool = await get_pool()
    async with pool.execute(
        """
        SELECT id, user_id, username, file_id, text, created_at
        FROM file_records
        WHERE id = $1;
        """,
        (record_id,),
    ) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


# ------------------------------------------------------------
# 刪除指定 ID 的紀錄
# ------------------------------------------------------------
async def delete_file(record_id: int) -> bool:
    """
    刪除 `id = record_id` 的那筆紀錄。
    回傳 True 代表刪除成功，False 代表找不到該筆紀錄。
    """
    pool = await get_pool()
    async with pool.execute(
        "DELETE FROM file_records WHERE id = $1;",
        (record_id,),
    ) as cur:
        await pool.commit()
        # rowcount 可能是 0 或 1；若大於 0 代表真的被刪除
        return cur.rowcount > 0


# ------------------------------------------------------------
# 一鍵刪除全部 file_records 紀錄（管理員後台使用）
# ------------------------------------------------------------
async def delete_all_records() -> None:
    """
    把 file_records 表全部內容清空。此函式只會在管理員
    透過 /admin → 「刪除全部」按鈕時呼叫，且已經過權限檢查。
    """
    pool = await get_pool()
    async with pool.execute("DELETE FROM file_records;", ())
    await pool.commit()
