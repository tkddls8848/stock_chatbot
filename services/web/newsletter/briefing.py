"""공개 자료로 하루 한 편을 쓰고, 실패를 포함해 당일 결과를 보관한다."""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from services.web.core import config
from services.web.core.clock import ensure_jst
from services.web.core.storage import file_lock, read_json_object, write_json_atomic
from services.web.llm.factory import build_backend
from services.web.llm.terminology import read_prompt
from services.web.search import MARKETS

INPUT_LIMIT = 12_000
# 막는 것은 독자에게 행동을 시키는 말이다. "외국인 순매수"·"자사주 매입"·"지분 매각"은 사실 서술이라 막지 않는다 —
# 낱말째 막으면 평범한 시장 기사를 옮긴 브리핑이 매일 반려된다.
FORBIDDEN = re.compile(
    r"(?:매수|매도|매입|매각|투자)\s*(?:하세요|하십시오|하라|해야\s*합니다|를\s*권|를\s*추천|을\s*권|을\s*추천)"
    r"|추천(?:합니다|한다|드립니다|드려요)|사세요|파세요|손절|익절|비중을?\s*(?:늘리|줄이)(?:세요|십시오|라)"
    r"|polymarket|폴리마켓", re.I)


# 한국 경제 기사 제목이 쓰는 한 글자 약어. 모델이 교정 지시를 받고도 되풀이해서(2026-11-16: 日株·中·發 두 번 연속)
# 뜻이 하나로 정해진 것만 코드가 바꾼다. "南向"처럼 뜻을 풀어야 하는 말은 반려해 모델이 고친다.
_HAN_ABBREVIATIONS = {"美": "미국", "中": "중국", "日": "일본", "韓": "한국", "英": "영국", "獨": "독일",
                      "佛": "프랑스", "露": "러시아", "印": "인도", "亞": "아시아", "發": "발", "株": "주", "對": "대"}
_HAN_PATTERN = re.compile("|".join(_HAN_ABBREVIATIONS))


class BriefingError(ValueError):
    """모델 응답이 편지 계약을 어겼다."""


def stamp(value):
    try:
        return ensure_jst(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def collect_input(public_dir: Path, moment: datetime) -> dict | None:
    moment = ensure_jst(moment)
    since = moment - timedelta(hours=24)
    documents = read_json_object(public_dir / "news.json").get("documents", [])
    recent = []
    for d in documents if isinstance(documents, list) else []:
        if not isinstance(d, dict) or d.get("kind") not in ("report", "news"):
            continue
        at = stamp(d.get("published_at") or d.get("date"))
        if at and since <= at <= moment:
            recent.append((at, d))
    recent.sort(key=lambda pair: pair[0], reverse=True)
    market = read_json_object(public_dir / "market.json")
    generated = stamp(market.get("generated_at"))
    rows = market.get("markets", {}) if generated and timedelta(0) <= moment - generated <= timedelta(hours=36) else {}
    payload, sources = {"markets": []}, {}
    for code, name in MARKETS.items():
        docs = [d for _, d in recent if d.get("market") == code]
        reports = [d for d in docs if d["kind"] == "report"]
        articles = [d for d in docs if d["kind"] == "news"][:8]
        daily = (rows.get(code) or {}).get("daily", [])
        last = daily[-1] if daily else {}
        if not (reports or articles or last):
            continue
        row = {"code": code, "name": name, "avg_sentiment": last.get("avg_sentiment"),
               "summary": str(last.get("summary") or "")[:400], "report": None, "news": []}
        if reports:
            row["report"] = {k: str(reports[0].get(k) or "") for k in ("title", "text")}
            row["report"]["title"] = row["report"]["title"][:200]
        for article in articles:
            nid = f"n{len(sources) + 1}"
            title = str(article.get("title") or "")
            sources[nid] = {"title": title, "source": str(article.get("source") or ""),
                            "url": str(article.get("url") or "")}
            row["news"].append({"id": nid, "title": title[:200] if re.search("[가-힣]", title) else "",
                                "text": str(article.get("text") or title)[:300],
                                "source": sources[nid]["source"][:80]})
        payload["markets"].append(row)
    if not any(r["report"] or r["news"] for r in payload["markets"]):
        return None
    # JSON을 자르지 않고 필드를 줄인다. 먼저 보고서 본문, 다음 긴 기사 원문 순이다.
    fields = [(r["report"], "text") for r in payload["markets"] if r["report"]]
    fields += [(n, k) for r in payload["markets"] for n in r["news"] for k in ("text", "title", "source")]
    for obj, key in fields:
        excess = len(encode(payload)) - INPUT_LIMIT
        if excess <= 0:
            break
        obj[key] = obj[key][:max(0, len(obj[key]) - excess)]
    return {"payload": payload, "sources": sources}


def numbers(text: str) -> set[Decimal]:
    text = unicodedata.normalize("NFKC", text).replace("−", "-")
    # 천 단위 구분자만 제거: 서로 다른 '10 20'을 1020으로 합치지 않는다.
    text = re.sub(r"(?<=\d)(?:\s*,\s*|\s+)(?=\d{3}(?:\D|$))", "", text)
    return {Decimal(n) for n in re.findall(r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)", text)}


def validate(raw: str, data: dict) -> dict:
    try:
        result = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise BriefingError("JSON 객체로 작성하십시오") from exc
    if not isinstance(result, dict) or not {"subject", "lead", "items", "watch"} <= set(result):
        raise BriefingError("subject, lead, items, watch 필드가 필요합니다")
    # 모양만 어긋난 응답은 코드가 맞춘다 — 내용(숫자·근거·금지어·한자)은 아래에서 그대로 검증한다. 형식 위반으로 교정을
    # 요청하면 모델이 다른 형식을 또 어겨 편지 전체가 대체본으로 떨어졌다(2026-11-16 실측 5회 중 2회: 관찰점 3개·문자열
    # 관찰점·항목의 군더더기 필드·네 문장 본문).
    result = {key: result[key] for key in ("subject", "lead", "items", "watch")}
    if isinstance(result["items"], list):
        result["items"] = [{key: item.get(key) for key in ("title", "body", "markets", "news")}
                           for item in result["items"][:5] if isinstance(item, dict)]
    if isinstance(result["watch"], list):
        result["watch"] = [{"text": point} if isinstance(point, str) else
                           {"text": point.get("text")} if isinstance(point, dict) else point
                           for point in result["watch"][:2]]
    prose = []

    def text(value, low, high, sentences=False):
        if not isinstance(value, str) or not low <= len(value.strip()) <= high:
            raise BriefingError(f"문자 수는 {low}~{high}자여야 합니다")
        if sentences and not 1 <= len(re.findall(r"[.!?](?:\s|$)", value)) <= 4:
            raise BriefingError("리드와 본문은 한두 문장에서 네 문장 사이여야 합니다")
        if not re.search("[가-힣]", value) or re.search(r"https?://|www\.", value):
            raise BriefingError("주소 없이 한국어로 작성하십시오")
        # 중국·일본 기사 원문을 옮기다 "A股"·"回购"·"南向"처럼 한자·가나가 섞였다(2026-11-16 미리보기). 신문식 한 글자
        # 약어는 `_HAN_ABBREVIATIONS`가 먼저 바꾸고, 남은 것은 반려해 고치게 한다.
        foreign = re.findall(r"[぀-ヿ㐀-鿿]+", value)
        if foreign:
            raise BriefingError(f"한자·가나({', '.join(dict.fromkeys(foreign))})를 쓰지 말고 한국어로 풀어 쓰십시오")
        prose.append(value)

    for owner, key in [(result, "subject"), (result, "lead"),
                       *[(item, field) for item in result.get("items") or [] if isinstance(item, dict)
                         for field in ("title", "body")],
                       *[(point, "text") for point in result.get("watch") or [] if isinstance(point, dict)]]:
        if isinstance(owner.get(key), str):
            owner[key] = _HAN_PATTERN.sub(lambda m: _HAN_ABBREVIATIONS[m.group()], owner[key])
    text(result["subject"], 15, 40)
    if "\n" in result["subject"] or "\r" in result["subject"]:
        raise BriefingError("편지 제목은 한 줄이어야 합니다")
    text(result["lead"], 80, 220, True)
    items, watch = result["items"], result["watch"]
    if not isinstance(items, list) or not 3 <= len(items) <= 5 or not isinstance(watch, list) or not 1 <= len(watch) <= 2:
        raise BriefingError("항목은 세다섯 개, 관찰점은 한두 개여야 합니다")
    codes = {r["code"] for r in data["payload"]["markets"]}
    ids = {n["id"] for r in data["payload"]["markets"] for n in r["news"]}
    for item in items:
        text(item["title"], 8, 40)
        text(item["body"], 80, 260, True)
        # 번호·코드는 근거를 가리키는 표시일 뿐 문장이 아니다. 모델이 기사 번호를 넷 이상 달고 교정 지시도 따르지
        # 않아 편지 전체가 대체본으로 떨어졌다(2026-11-16 실측 6회 중 2회). 입력에 있는 것만 남겨 중복을 없애고 기사는
        # 앞의 셋을 쓴다. 남는 것이 없을 때만 반려한다.
        for key, allowed, high in (("markets", codes, len(codes)), ("news", ids, 3)):
            values = item[key] if isinstance(item[key], list) else []
            kept = list(dict.fromkeys(v for v in values if isinstance(v, str) and v in allowed))[:high]
            if not kept:
                raise BriefingError(f"항목 '{item.get('title')}'의 {key}에 입력에 있는 번호·코드를 하나 이상 쓰십시오")
            item[key] = kept
    if len({i["title"] for i in items}) != len(items) or len({i["body"] for i in items}) != len(items):
        raise BriefingError("서로 다른 사건을 작성하십시오")
    for point in watch:
        if not isinstance(point, dict):
            raise BriefingError("관찰점은 text를 가진 객체여야 합니다")
        text(point["text"], 20, 120)
    joined = "\n".join(prose)
    if FORBIDDEN.search(unicodedata.normalize("NFKC", joined)):
        raise BriefingError("행동 지시·출처 서비스 이름을 제거하십시오")
    # 참조 번호는 사실 수치가 아니다. 원문 내용만 대조해 n3가 3%의 근거가 되지 않게 한다.
    evidence = [{k: v for k, v in r.items() if k != "news"} | {
        "news": [{k: v for k, v in n.items() if k != "id"} for n in r["news"]]
    } for r in data["payload"]["markets"]]
    if not numbers(joined) <= numbers(encode(evidence)):
        raise BriefingError("입력에 없는 숫자를 제거하십시오")
    for item in items:
        item["sources"] = [data["sources"][nid] for nid in item["news"]]
    return result


def generate(data: dict, backend, usage: dict) -> dict | None:
    prompt = read_prompt(config.NEWSLETTER_PROMPT_FILE)
    revision = ""
    for attempt in range(2):
        try:
            raw = backend.generate(system_prompt=prompt + revision, user_prompt=encode(data["payload"]),
                                   max_tokens=config.NEWSLETTER_MAX_TOKENS, temperature=0.2)
        except Exception:
            return None  # 통신·잘림 실패는 교정 대상이 아니다.
        finally:
            current = getattr(backend, "last_usage", None)
            if current:
                for key, value in asdict(current).items():
                    if value is not None:
                        usage[key] = usage.get(key, 0) + value
        try:
            return validate(raw, data)
        except BriefingError as exc:
            if attempt:
                return None
            # 직전 응답을 함께 줘서 그 부분만 고치게 한다. 사유만 주면 처음부터 다시 쓰다 같은 실수를 되풀이했다.
            revision = (f"\n직전 응답 반려 사유: {exc}\n직전 응답의 해당 부분만 고쳐 같은 형식의 JSON 전체를 다시 반환하십시오."
                        f" 원래 입력만 근거로 합니다.\n직전 응답: {raw}")
    return None


def get_briefing(public_dir: Path, moment: datetime, *, cache_dir: Path | None = None) -> dict:
    moment = ensure_jst(moment)
    directory = cache_dir if cache_dir is not None else config.NEWSLETTER_DIR
    path = directory / f"briefing-{moment.date().isoformat()}.json"
    with file_lock(directory / "briefing.lock", stale_seconds=900, timeout=310):
        for old in directory.glob("briefing-????-??-??.json"):
            day = stamp(old.stem.removeprefix("briefing-"))
            if day and day.date() < (moment - timedelta(days=14)).date():
                old.unlink()
        if path.exists():
            return read_json_object(path)
        data = collect_input(public_dir, moment)
        record = {"generated_at": moment.isoformat(), "model": config.NEWSLETTER_MODEL, "usage": {},
                  "input_chars": len(encode(data["payload"])) if data else 0, "briefing": None}
        # 호출 전에 예약을 남긴다. 프로세스 중단 뒤에도 같은 날 중복 과금하지 않는다.
        write_json_atomic(path, record)
        if data:
            try:
                config.require_cloudflare_credentials()
            except config.ConfigurationError:
                return record
            backend = build_backend("newsletter", model=config.NEWSLETTER_MODEL, timeout=120, max_attempts=1)
            record["briefing"] = generate(data, backend, record["usage"])
        write_json_atomic(path, record)
        return record
