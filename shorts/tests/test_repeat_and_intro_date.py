"""매일 같은 이슈를 다시 고르지 않고, 도입은 자료 기준 날짜를 말한다(2026-09-27 운영자 결정)."""

import json
from dataclasses import replace
from datetime import date

from polymarket_shorts import speech
from polymarket_shorts.config import Settings
from polymarket_shorts.markets import _topic
from polymarket_shorts.pipeline import recently_featured


def _day(root, day, issues):
    folder = root / "shorts" / day
    folder.mkdir(parents=True)
    (folder / "source.json").write_text(json.dumps({"issues": issues}), encoding="utf-8")


def test_recent_events_and_their_date_variants_are_excluded(tmp_path):
    settings = replace(Settings.from_env(), output_dir=tmp_path / "shorts", repeat_days=7)
    _day(tmp_path, "2026-09-26", [{"id": "660109", "title": "Strait of Hormuz traffic returns to normal by September 30?"}])
    _day(tmp_path, "2026-09-18", [{"id": "111", "title": "Old issue beyond the window?"}])
    _day(tmp_path, "2026-09-27", [{"id": "222", "title": "Today's own run is not counted?"}])

    ids, topics = recently_featured(settings, date(2026, 9, 27))

    assert ids == {"660109"}
    # "12월 31일까지" 변형도 같은 주제로 걸린다.
    assert _topic("Strait of Hormuz traffic returns to normal by December 31?") in topics
    assert _topic("Old issue beyond the window?") not in topics


def test_older_days_without_source_fall_back_to_scenario_event_ids(tmp_path):
    settings = replace(Settings.from_env(), output_dir=tmp_path / "shorts", repeat_days=7)
    folder = tmp_path / "shorts" / "2026-09-23"
    folder.mkdir(parents=True)
    (folder / "scenario.json").write_text(json.dumps({"scenes": [{"kind": "consensus", "event_id": "606422"}]}),
                                          encoding="utf-8")
    ids, _ = recently_featured(settings, date(2026, 9, 27))
    assert ids == {"606422"}


def test_the_intro_states_the_date_first():
    line = speech.opening_line(2, date(2026, 9, 27))
    assert line == ("2026년 9월 27일 시장 컨센서스 이슈를 선정하였습니다. "
                    "오늘은 질문 2개를 숫자와 함께 짚어 보겠습니다.")
