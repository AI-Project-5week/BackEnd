"""SQLite 연결과 쿼리 모음.

통계(get_stats)는 대시보드뿐 아니라 AI 프롬프트에도 그대로 재사용한다.
"계산은 코드가 책임진다" → 합계·집계는 전부 여기서 SQL로 처리.
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "ledger.db"


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # row["amount"]처럼 컬럼 이름으로 접근
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS expenses (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                date     TEXT    NOT NULL,
                item     TEXT    NOT NULL,
                amount   INTEGER NOT NULL,
                category TEXT    NOT NULL,
                memo     TEXT    NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS budgets (
                month  TEXT PRIMARY KEY,
                amount INTEGER NOT NULL
            );
            """
        )


# 날짜가 'YYYY-MM-DD' 문자열이라 월 필터는 LIKE '2026-09-%'로 충분하다.
def _month_like(month: str) -> str:
    return f"{month}-%"


# ---------- 예산 ----------

def get_budget(month: str) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT amount FROM budgets WHERE month = ?", (month,)).fetchone()
    return row["amount"] if row else 0


def set_budget(month: str, amount: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO budgets (month, amount) VALUES (?, ?)",
            (month, amount),
        )


# ---------- 지출 내역 ----------

def list_expenses(month: str, category: str | None = None) -> list[dict]:
    sql = "SELECT * FROM expenses WHERE date LIKE ?"
    params: list = [_month_like(month)]
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY date DESC, id DESC"  # 최신순, 같은 날이면 나중에 입력한 것 먼저
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def add_expense(date: str, item: str, amount: int, category: str, memo: str) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO expenses (date, item, amount, category, memo) VALUES (?, ?, ?, ?, ?)",
            (date, item, amount, category, memo),
        )
        new_id = cur.lastrowid
    return {"id": new_id, "date": date, "item": item, "amount": amount, "category": category, "memo": memo}


def delete_expense(expense_id: int) -> bool:
    """삭제했으면 True, 해당 id가 없으면 False."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    return cur.rowcount > 0


# ---------- 통계 ----------

def get_stats(month: str) -> dict:
    like = _month_like(month)
    with get_conn() as conn:
        total, count = conn.execute(
            "SELECT COALESCE(SUM(amount), 0), COUNT(*) FROM expenses WHERE date LIKE ?",
            (like,),
        ).fetchone()
        by_category = conn.execute(
            "SELECT category, SUM(amount) AS s FROM expenses WHERE date LIKE ? "
            "GROUP BY category ORDER BY s DESC",
            (like,),
        ).fetchall()
        by_day = conn.execute(
            "SELECT date, SUM(amount) AS s FROM expenses WHERE date LIKE ? "
            "GROUP BY date ORDER BY date",
            (like,),
        ).fetchall()
    return {
        "total": total,
        "count": count,
        "by_category": {r["category"]: r["s"] for r in by_category},
        "by_day": {r["date"]: r["s"] for r in by_day},
    }
