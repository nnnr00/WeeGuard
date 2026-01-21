# src/models/file.py
# ------------------------------------------------------------
# 负责对 PostgreSQL（Neon）表 `file_records` 的增删改查
# ------------------------------------------------------------
import datetime
import os
from typing import Any, Dict

import aiosqlite
import pytz

# 读取 Railway 注入的数据库连接字符串
DATABASE_URL: str = os.getenv("DATABASE_URL", "")

# 单例连接（每次调用都会新建，避免 “threads can only be started once”）
async def get_connection() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(DATABASE_URL)
    conn.row_factory = aiosqlite.Row
    return conn


async def insert_file(user_id: int, file_id: str, text: Optional[str] = None) -> Dict[str, Any]:
    """
    插入一条 file_records 记录，返回自动生成的 `id` 与 `created_at`。
    """
    async with await get_connection() as conn:
        async with conn.execute(
            """
            INSERT INTO file_records (user_id, username, file_id, text)
            VALUES (?, ?, ?, ?)
            RETURNING id, created_at;
            """,
            (user_id, None, file_id, text),
        ) as cur:
            row = await cur.fetchone()
            await conn.commit()
            return {"id": row["id"], "created_at": row["created_at"]}


async def list_files(limit: int = 10) -> List[Dict[str, Any]]:
    """
    返回最近 `limit` 条 file_records（按创建时间倒序）。
    """
    async with await get_connection() as conn:
        async with conn.execute(
            f"""
            SELECT id, user_id, username, file_id, text, created_at
            FROM file_records
            ORDER BY created_at DESC
            LIMIT ?;
            """,
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def get_file_by_id(record_id: int) -> Optional[Dict[str, Any]]:
    async with await get_connection() as conn:
        async with conn.execute(
            f"SELECT id, user_id, username, file_id, text, created_at FROM file_records WHERE id = ?;",
            (record_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def delete_file(record_id: int) -> bool:
    """
    删除指定 ID 的记录。返回 True 表示删除成功，False 表示记录不存在。
    """
    async with await get_connection() as conn:
        async with conn.execute(
            f"DELETE FROM file_records WHERE id = ?;", (record_id,)
        ) as cur:
            await conn.commit()
            return cur.rowcount > 0


async def delete_all_records() -> None:
    """删除 file_records 表中所有记录（管理员后台使用）。"""
    async with await get_connection() as conn:
        await conn.execute(f"DELETE FROM file_records;")
        await conn.commit()
