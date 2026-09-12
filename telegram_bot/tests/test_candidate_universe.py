"""리서치 후보 유니버스가 요구하는 StockDatabase API 회귀 테스트.

/research run이 stock_db.get_candidate_universe() 부재로 AttributeError를
일으킨 회귀 방지(리서치 모듈 복원 시 발견).
"""
import json

from telegram_bot.research.candidates import build_research_candidate_universe
from telegram_bot.stocks import StockDatabase


def _db(tmp_path) -> StockDatabase:
    cache = tmp_path / "stock_db.json"
    cache.write_text(
        json.dumps(
            {
                "600519": {
                    "display_name": "귀주모태주",
                    "cn_name": "贵州茅台",
                    "ko_name": "귀주모태",
                    "market": "SH",
                },
                "09988": {
                    "display_name": "알리바바",
                    "cn_name": "阿里巴巴",
                    "ko_name": "아리파파",
                    "market": "HK",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    db = StockDatabase(cache_file=cache)
    db.load()
    return db


def test_get_candidate_universe_projects_entries(tmp_path):
    entries = _db(tmp_path).get_candidate_universe()
    assert {e["code"] for e in entries} == {"600519", "09988"}
    entry = next(e for e in entries if e["code"] == "600519")
    assert entry["cn_name"] == "贵州茅台"
    assert entry["market"] == "SH"
    assert entry["market_cap_cny"] == 0.0  # 필드 없으면 0.0


def test_get_candidate_universe_respects_limit(tmp_path):
    assert len(_db(tmp_path).get_candidate_universe(limit=1)) == 1


def test_build_research_candidate_universe_runs_with_current_db(tmp_path):
    # /research run 경로 회귀: 현재 StockDatabase로 후보 유니버스 생성이 동작해야 한다.
    candidates = build_research_candidate_universe(
        _db(tmp_path),
        watchlist={"600519": "귀주모태주"},
        news_items=[{"title": "贵州茅台 실적 발표", "content": "본문"}],
    )
    assert any(c["code"] == "600519" for c in candidates)
