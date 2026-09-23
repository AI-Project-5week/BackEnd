"""로컬 LLM(Ollama) 호출과 프롬프트.

원칙: 합계·비율·날짜·금액처럼 틀리면 안 되는 값은 Python이 확정하고,
LLM은 그 값을 근거로 설명·제안만 한다. (작은 모델은 계산을 자주 틀림)
"""
import json
import os
import re
from datetime import date, timedelta

import httpx
import ollama

import database as db
from schemas import CATEGORIES

MODEL = os.getenv("OLLAMA_MODEL", "exaone3.5:2.4b")
# 프론트 타임아웃(120초)보다 먼저 끊어서 에러 메시지를 돌려준다.
client = ollama.Client(host=os.getenv("OLLAMA_HOST", "http://localhost:11434"), timeout=110)

MAX_HISTORY = 10  # 대화가 길어지면 느려지므로 최근 N개만 보낸다
MAX_EXPENSES_IN_PROMPT = 50
WEEKDAYS = "월화수목금토일"


class AIError(Exception):
    """Ollama 호출 실패. main.py에서 503 응답으로 바꾼다."""


def _ask(messages: list[dict], **kwargs) -> str:
    try:
        res = client.chat(model=MODEL, messages=messages, **kwargs)
    except (ConnectionError, httpx.ConnectError) as e:
        raise AIError("Ollama에 연결할 수 없어요. Ollama가 실행 중인지 확인하세요.") from e
    except httpx.TimeoutException as e:
        raise AIError("AI 응답이 너무 오래 걸려요. 잠시 후 다시 시도하세요.") from e
    except ollama.ResponseError as e:
        if e.status_code == 404:
            raise AIError(f"모델이 없어요. 'ollama pull {MODEL}'로 받아 주세요.") from e
        raise AIError(f"Ollama 오류: {e.error}") from e
    return res.message.content.strip()


def _won(n: int) -> str:
    return f"{n:,}원"


def _build_context(month: str) -> str:
    """DB에서 계산한 확정 통계를 LLM이 읽을 텍스트로 만든다."""
    stats = db.get_stats(month)
    budget = db.get_budget(month)
    total, count = stats["total"], stats["count"]

    lines = [f"[{month} 소비 데이터] (백엔드가 계산한 확정값)"]
    if count == 0:
        lines.append("- 입력된 지출 내역 없음")
    else:
        lines.append(f"- 총지출: {_won(total)} ({count}건)")

    if budget:
        remaining = budget - total
        status = f"남은 예산 {_won(remaining)}" if remaining >= 0 else f"예산 초과 {_won(-remaining)}"
        lines.append(f"- 월 예산: {_won(budget)}, 사용률 {total / budget * 100:.1f}%, {status}")
    else:
        lines.append("- 월 예산: 설정 안 됨")

    if count:
        lines.append("- 카테고리별 합계 (금액 큰 순):")
        for cat, amt in stats["by_category"].items():
            lines.append(f"  - {cat}: {_won(amt)} ({amt / total * 100:.1f}%)")

        expenses = db.list_expenses(month)
        lines.append("- 지출 내역 (최신순):")
        for e in expenses[:MAX_EXPENSES_IN_PROMPT]:
            memo = f" / 메모: {e['memo']}" if e["memo"] else ""
            lines.append(f"  - {e['date']} {e['item']} {_won(e['amount'])} [{e['category']}]{memo}")
        if count > MAX_EXPENSES_IN_PROMPT:
            lines.append(f"  - ... 외 {count - MAX_EXPENSES_IN_PROMPT}건 생략")

    return "\n".join(lines)


# ---------- POST /chat ----------

CHAT_SYSTEM = """너는 사용자의 소비 습관을 함께 점검하는 친절한 AI 소비 코치야.

규칙:
1. 아래 [소비 데이터]만 근거로 답해. 데이터에 없는 지출은 지어내지 마.
2. 합계·비율은 [소비 데이터]에 적힌 값을 그대로 써. 직접 다시 계산하지 마.
3. 절약 방법은 카테고리와 금액을 구체적으로 제안하고, 제안 금액의 합이 사용자의 목표 금액과 같게 해.
4. 사용자가 줄이기 싫다고 한 카테고리는 이후 제안에서 빼고, 다른 카테고리로 대안을 제시해.
5. 투자·대출·금융상품은 추천하지 마.
6. 한국어로 3~6문장 정도, 짧고 친근하게 답해.

{context}"""


def chat(month: str, message: str, history: list[dict]) -> str:
    messages = [
        {"role": "system", "content": CHAT_SYSTEM.format(context=_build_context(month))},
        *history[-MAX_HISTORY:],  # 이전 대화 → "카페는 줄이기 싫어" 같은 조건을 기억
        {"role": "user", "content": message},
    ]
    return _ask(messages, options={"temperature": 0.3})


# ---------- GET /report ----------

REPORT_PROMPT = """아래 소비 데이터로 월간 소비 리포트를 마크다운으로 작성해.

형식:
### 요약
(총지출, 예산 대비 상황을 2~3문장으로)
### 눈에 띄는 소비 패턴
- (비중이 큰 카테고리, 자주 쓴 곳 등 2~3개)
### 다음 달 절약 제안
- (카테고리와 금액을 구체적으로 2~3개)

규칙: 숫자는 데이터에 적힌 값만 쓰고 새로 계산하지 마. 데이터에 없는 내용은 지어내지 마.
투자·금융상품은 추천하지 마. 리포트 본문만 출력해.

{context}"""


def report(month: str) -> str:
    if db.get_stats(month)["count"] == 0:
        return f"### 요약\n{month}에 입력된 지출 내역이 없어요. 지출을 입력하면 리포트를 만들어 드릴게요."
    messages = [{"role": "user", "content": REPORT_PROMPT.format(context=_build_context(month))}]
    return _ask(messages, options={"temperature": 0.3})


# ---------- POST /parse ----------

PARSE_SYSTEM = """너는 가계부 입력 도우미야. 사용자의 문장에서 지출 정보를 뽑아 JSON 하나로만 답해.

오늘 날짜: {today} ({weekday}요일)
카테고리 목록: {categories}

JSON 형식: {{"date": "YYYY-MM-DD", "item": "가게나 품목", "amount": 정수, "category": "카테고리"}}

규칙:
- 날짜 언급이 없으면 오늘 날짜.
- amount는 원 단위 정수. 금액이 없으면 0.
- item은 줄임말을 풀어서 써. (스벅 → 스타벅스, 맥날 → 맥도날드)
- category는 반드시 카테고리 목록 중 하나. 애매하면 "기타"."""


def _example(d: date, item: str, amount: int, category: str) -> str:
    return json.dumps(
        {"date": d.isoformat(), "item": item, "amount": amount, "category": category},
        ensure_ascii=False,
    )


def parse_expense(text: str) -> dict:
    today = date.today()
    system = PARSE_SYSTEM.format(
        today=today.isoformat(), weekday=WEEKDAYS[today.weekday()], categories=", ".join(CATEGORIES)
    )
    # 작은 모델은 예시를 보여주면 형식을 훨씬 잘 지킨다 (few-shot)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": "어제 스벅 5800원"},
        {"role": "assistant", "content": _example(today - timedelta(days=1), "스타벅스", 5800, "카페")},
        {"role": "user", "content": "점심 김밥천국 1만2천원"},
        {"role": "assistant", "content": _example(today, "김밥천국", 12000, "식비")},
        {"role": "user", "content": text},
    ]
    content = _ask(messages, format="json", options={"temperature": 0})
    try:
        raw = json.loads(content)
    except json.JSONDecodeError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return clean_parsed(raw, text, today)


def clean_parsed(raw: dict, text: str, today: date) -> dict:
    """LLM 결과를 검사하고, 코드로 확실히 알 수 있는 값(날짜·금액)은 코드 결과를 우선한다."""
    parsed_date = resolve_date(text, today) or _valid_date(raw.get("date"), today) or today

    amount = parse_amount(text)
    if amount is None:
        amount = _to_int(raw.get("amount"))

    category = raw.get("category")
    if category not in CATEGORIES:
        category = "기타"

    item = str(raw.get("item") or "").strip() or text.strip()

    return {"date": parsed_date.isoformat(), "item": item, "amount": amount, "category": category}


def resolve_date(text: str, today: date) -> date | None:
    """'어제', '3일 전', '9월 20일', '9/20' 같은 표현을 날짜로 바꾼다. 못 찾으면 None."""
    m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", text) or re.search(r"(?<!\d)(\d{1,2})/(\d{1,2})(?!\d)", text)
    if m:
        try:
            d = date(today.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
        if d > today:  # 1월에 "12월 30일"이라고 하면 작년
            try:
                d = d.replace(year=today.year - 1)
            except ValueError:
                return None
        return d

    m = re.search(r"(\d+)\s*일\s*전", text)
    if m:
        return today - timedelta(days=int(m.group(1)))

    for word, days_ago in (("그저께", 2), ("그제", 2), ("어제", 1), ("오늘", 0)):
        if word in text:
            return today - timedelta(days=days_ago)
    return None


_AMOUNT_RE = re.compile(r"((?:\d[\d,]*\s*[만천]\s*)*(?:\d[\d,]*)?)\s*원")
_UNITS = {"만": 10000, "천": 1000, "": 1}


def parse_amount(text: str) -> int | None:
    """'5800원', '5,800원', '1만2천원', '3만 원' → 정수. 못 찾으면 None."""
    for m in _AMOUNT_RE.finditer(text):
        body = m.group(1).replace(",", "").replace(" ", "")
        if not re.search(r"\d", body):
            continue
        return sum(int(num) * _UNITS[unit] for num, unit in re.findall(r"(\d+)([만천]?)", body))
    return None


def _valid_date(value, today: date) -> date | None:
    try:
        d = date.fromisoformat(str(value))
    except ValueError:
        return None
    return d if d <= today else None


def _to_int(value) -> int:
    try:
        n = int(float(str(value).replace(",", "")))
    except ValueError:
        return 0
    return max(n, 0)
