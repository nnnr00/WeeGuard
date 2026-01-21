# src/models/point.py
# ------------------------------------------------------------
# 负责积分（points）的增、删、查、记录签到等操作
# ------------------------------------------------------------
import os
from datetime import datetime
from typing import Optional, Dict

import aiosqlite
import pytz

# 读取 Neon 连接串
DATABASE_URL: str = os.getenv("DATABASE_URL", "")

# 单例连接（每次调用新建，避免 “threads can only be started once”）
async def get_connection() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(DATABASE_URL)
    conn.row_factory = aiosqlite.Row
    return conn


async def get_balance(user_id: int) -> int:
    """返回用户当前的积分餘额。"""
    async with await get_connection() as conn:
        async with conn.execute(
            f"SELECT balance FROM points WHERE user_id = ?;", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row["balance"] if row else 0


async def add_points(user_id: int, points: int) -> None:
    """在积分表中为 `user_id` 加上 `points` 分数。"""
    async with await get_connection() as conn:
        current = await get_balance(user_id)
        new_balance = current + points
        await conn.execute(
            f"""
            INSERT OR REPLACE INTO points (user_id, balance)
            VALUES (?, ?);
            """,
            (user_id, new_balance),
        )
        await conn.commit()


async def get_user_point_info(user_id: int) -> Optional[Dict[str, Any]]:
    """返回该用户完整的积分信息（balance、last_sign、created_at）。"""
    async with await get_connection() as conn:
        async with conn.execute(
            f"SELECT balance, last_sign, created_at FROM points WHERE user_id = ?;",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def record_sign(user_id: int, earned: int) -> None:
    """
    记录一次签到并更新积分。
    - `earned` 为本次签到奖励的积分数额。
    - `last_sign` 会被设为当前日期（YYYY‑MM‑DD）。
    """
    now_str = datetime.datetime.now(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d")
    async with await get_connection() as conn:
        await conn.execute(
            """
            INSERT OR REPLACE INTO points (user_id, balance, last_sign)
            VALUES (?, ?, ?);
            """,
            (user_id, earned, now_str),
        )
        await conn.commit()
