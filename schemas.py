"""요청·응답 JSON 형태 정의 (Pydantic).

여기서 정의한 규칙에 안 맞는 요청은 FastAPI가 자동으로 422 에러를 돌려준다.
"""
from pydantic import BaseModel, Field, field_validator

CATEGORIES = ["식비", "카페", "교통", "쇼핑", "생활", "문화", "의료", "기타"]

MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"  # 2026-09
DATE_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"  # 2026-09-22


class Budget(BaseModel):
    month: str = Field(pattern=MONTH_PATTERN, examples=["2026-09"])
    amount: int = Field(ge=0, examples=[500000])


class ExpenseIn(BaseModel):
    """POST /expenses 요청 (id 없음)."""

    date: str = Field(pattern=DATE_PATTERN, examples=["2026-09-22"])
    item: str = Field(min_length=1, examples=["스타벅스"])
    amount: int = Field(ge=0, examples=[5800])
    category: str = Field(examples=["카페"])
    memo: str = ""

    @field_validator("category")
    @classmethod
    def check_category(cls, v: str) -> str:
        if v not in CATEGORIES:
            raise ValueError(f"category는 {CATEGORIES} 중 하나여야 합니다")
        return v


class Expense(ExpenseIn):
    """저장된 지출 내역 (id 포함)."""

    id: int


class Stats(BaseModel):
    total: int
    count: int
    by_category: dict[str, int]
    by_day: dict[str, int]

