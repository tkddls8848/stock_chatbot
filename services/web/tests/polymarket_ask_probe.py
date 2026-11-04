"""질의응답 후보(1차 검색)의 적중을 실제 수집분으로 재는 독립 프로브. LLM은 부르지 않는다.

`docs/polymarket-consensus-qa.md` 2절의 "상위 5·30 안 정답"을 잰다. 운영 수집분을 읽기만 하고,
선택지 이름 색인은 임시 폴더의 색인 사본에 `annotate.refresh_labels`로 붙여 본다(운영 색인은 건드리지
않는다). 질문 셋은 둘이다 — 용어집을 맞출 때 본 20문항(`TUNED`)과 그 뒤에 따로 적은 문항(`HELD_OUT`).
앞의 점수는 낙관적이다(같은 문항으로 고쳤다). 정답은 원문 제목 정규식이고 None은 "직접 맞는 질문 없음"이다.

    python -m services.web.tests.polymarket_ask_probe [--root /srv/stock-chatbot/storage/public/polymarket]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import tempfile
import time

from services.web.polymarket.annotate import refresh_labels
from services.web.polymarket.repository import PolymarketRepository

TUNED: list[tuple[str, str | None]] = [
    ("연준 12월 금리 인하 확률", r"^Fed Decision in December"),
    ("올해 미국 금리 또 올릴까", r"Another Fed rate hike in 2026"),
    ("미국 경기침체 오나", r"US recession by end of 2026"),
    ("비트코인 연말 가격", r"What price will Bitcoin hit in 2026"),
    ("11월 유가 얼마까지", r"WTI.*November"),
    ("트럼프 관세 대법원 판결", None),
    ("이란 휴전 언제까지 유지", r"US-Iran ceasefire continues"),
    ("앤스로픽 상장", r"Anthropic IPO"),
    ("일본은행 금리 인상", r"Bank of Japan"),
    ("중국 대만 침공", r"China invade Taiwan"),
    ("금값 사상 최고치", r"Gold \((GC|XAUUSD)\) hit"),
    ("미국 실업률 전망", r"Unemployment"),
    ("미국 물가 CPI", r"CPI|Inflation US"),
    ("정부 셧다운", r"Government shutdown"),
    ("엔비디아 시가총액", r"Largest Company"),
    ("한국은행 기준금리", r"Bank of Korea"),
    ("코스피 3000", None),
    # 처음 측정(2026-11-04)에는 0건이라 "없음"으로 봤지만 *Will USD/KRW hit __ in 2026?*이 열려 있다.
    ("원달러 환율 1500원", r"USD/KRW hit"),
    ("멕시코 기준금리 결정", r"Bank of Mexico"),
    ("인공지능 최고 모델 회사", r"#1 AI model|best AI model"),
]
HELD_OUT: list[tuple[str, str | None]] = [
    ("2026년 연준 금리 인하 몇 번", r"How many Fed rate cuts in 2026"),
    ("이스라엘 이란 휴전 끝날까", r"Israel x Iran ceasefire"),
    ("호르무즈 해협 통행 정상화", r"Hormuz"),
    ("러시아 우크라이나 휴전", r"Russia x Ukraine ceasefire"),
    ("타이베이 시장 선거", r"Taipei Mayor"),
    ("애플 연말 시가총액", r"Apple.?s Market Cap"),
    ("이더리움 이번 달 가격", r"Ethereum"),
    ("미국 하원 다수당", r"House"),
    ("12월 연준 동결 가능성", r"^Fed Decision in December"),
    ("중국 물가 상승률", r"Inflation China"),
]


def _scratch(root: Path, into: Path) -> Path:
    """운영 수집분을 가리키는 임시 루트. 색인만 사본이고 선택지 이름을 붙인다."""
    for name in ("current.json", "generations"):
        (into / name).symlink_to(root / name)
    stored = json.loads((root / "search_index.json").read_text(encoding="utf-8"))
    events = json.loads((root / "current.json").read_text(encoding="utf-8"))["events"]
    refresh_labels(into, events, stored["events"])
    (into / "search_index.json").write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")
    return into


def _measure(repository: PolymarketRepository, cases, limit: int) -> dict:
    rows, hit5, hit30, gold_total, times = [], 0, 0, 0, []
    events = repository.load().get("events", [])
    for question, gold in cases:
        # 정답 질문이 있어도 확률을 읽지 못하면(price_status) 후보에서 빠진다 — 찾기 실패로 세지 않는다.
        if gold and not any(re.search(gold, str(e.get("title")), re.I) and e.get("price_status") == "ok"
                            for e in events):
            rows.append({"question": question, "gold": None, "rank": None, "top3": ["(정답 질문의 확률을 읽지 못함)"]})
            continue
        started = time.perf_counter()
        found = repository.candidates(question, limit=limit)
        times.append(time.perf_counter() - started)
        titles = [str(event.get("title")) for event, _ in found]
        rank = next((i + 1 for i, t in enumerate(titles) if gold and re.search(gold, t, re.I)), None)
        if gold:
            gold_total += 1
            hit5 += bool(rank and rank <= 5)
            hit30 += bool(rank)
        rows.append({"question": question, "gold": gold, "rank": rank, "top3": titles[:3]})
    times.sort()
    return {"rows": rows, "gold": gold_total, "hit5": hit5, "hit30": hit30,
            "p50_ms": round(times[len(times) // 2] * 1000), "max_ms": round(times[-1] * 1000)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/srv/stock-chatbot/storage/public/polymarket"))
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    folder = Path(tempfile.mkdtemp(prefix="ask-probe-"))
    try:
        repository = PolymarketRepository(_scratch(args.root, folder))
        for name, cases in (("tuned", TUNED), ("held_out", HELD_OUT)):
            result = _measure(repository, cases, args.limit)
            print(f"## {name}: 상위 5 {result['hit5']}/{result['gold']} · 상위 {args.limit} "
                  f"{result['hit30']}/{result['gold']} · 지연 p50 {result['p50_ms']}ms 최대 {result['max_ms']}ms")
            for row in result["rows"]:
                mark = "없음" if row["gold"] is None else (f"{row['rank']}위" if row["rank"] else "못 찾음")
                print(f"  - {row['question']} → {mark} | {' / '.join(row['top3'])}")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
