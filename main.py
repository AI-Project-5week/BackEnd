"""AI 가계부 백엔드 (FastAPI).

실행:  uvicorn main:app --reload --port 8000
테스트 화면:  http://localhost:8000/docs
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

import database as db
from schemas import CATEGORIES, MONTH_PATTERN, Budget, Expense, ExpenseIn, Stats


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()  # 서버 시작 시 테이블이 없으면 생성
    yield


app = FastAPI(title="AI 가계부 API", lifespan=lifespan)


def month_query():
    return Query(pattern=MONTH_PATTERN, examples=["2026-09"])


# ---------- 기본 정보 ----------

@app.get("/categories")
def get_categories() -> list[str]:
    return CATEGORIES


@app.get("/budget")
def get_budget(month: str = month_query()) -> Budget:
    return Budget(month=month, amount=db.get_budget(month))


@app.put("/budget")
def put_budget(budget: Budget) -> Budget:
    db.set_budget(budget.month, budget.amount)
    return budget


# ---------- 지출 내역 ----------

@app.get("/expenses")
def get_expenses(month: str = month_query(), category: str | None = None) -> list[Expense]:
    return db.list_expenses(month, category)


@app.post("/expenses")
def post_expense(expense: ExpenseIn) -> Expense:
    return db.add_expense(**expense.model_dump())


@app.delete("/expenses/{expense_id}")
def delete_expense(expense_id: int):
    if not db.delete_expense(expense_id):
        raise HTTPException(status_code=404, detail="해당 내역이 없습니다")
    return {"ok": True}


@app.get("/stats")
def get_stats(month: str = month_query()) -> Stats:
    return db.get_stats(month)


# ---------- AI (다음 단계에서 구현) ----------
# POST /parse, POST /chat, GET /report
