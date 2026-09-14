from datetime import date

from polymarket_shorts.client import Snapshot
from polymarket_shorts.pipeline import metadata_for
from polymarket_shorts.scenario import build_scenario, clip_at_sentence


def _snapshot(groups):
    return Snapshot(
        brief={
            "generation_id": "g1",
            "written_at": "2026-09-01T20:00:00+09:00",
            "groups": groups,
        },
        summary={
            "generation_id": "g1",
            "accounting": {"open_event_count": 22047},
        },
    )


def _group(key, label, volume, paragraph="첫 문장입니다. 두 번째 문장입니다."):
    return {
        "key": key,
        "label": label,
        "status": "ok",
        "event_count": 20,
        "volume24hr": volume,
        "paragraph": paragraph,
    }


def test_clip_keeps_complete_sentences():
    assert clip_at_sentence("첫 문장입니다. 두 번째 문장입니다.", 10) == "첫 문장입니다."


def test_clip_uses_remaining_space_when_next_sentence_is_long():
    text = "짧은 첫 문장입니다. " + "아주긴문장" * 20 + "."

    clipped = clip_at_sentence(text, 45)

    assert len(clipped) <= 45
    assert clipped.startswith("짧은 첫 문장입니다.")
    assert clipped.endswith("…")


def _label(scene) -> str:
    """표시용 제목에서 분야 라벨만 떼어 낸다.

    `scenario.py`가 제목에 `SIGNAL n · ` 접두사를 붙인다. 아래 테스트가 보는 것은
    어떤 분야가 어떤 순서로 뽑히는가이지 제목 서식이 아니라, 서식을 바꿔도 선정
    로직 테스트가 깨지지 않도록 라벨만 비교한다.
    """
    return scene.title.split(" · ")[-1]


def test_cards_follow_the_web_order_highest_24h_volume_first():
    """웹의 `?sort=volume24hr` 화면과 같은 순서여야 한다."""
    scenario = build_scenario(
        _snapshot(
            [
                _group("general", "기타 경제", 100),
                _group("macro", "거시", 300),
                _group("equities", "주식", 200),
                _group("composite", "복합", 1),
            ]
        ),
        production_date=date(2026, 9, 1),
    )

    labels = [_label(scene) for scene in scenario.scenes if scene.kind == "consensus"]
    assert labels == ["거시", "주식", "기타 경제", "복합"]
    assert scenario.scenes[0].kind == "intro"
    assert scenario.scenes[-1].kind == "outro"


def test_every_sector_gets_a_card_even_when_its_summary_failed():
    """요약 생성이 실패해도 카드는 세운다.

    건수와 거래량은 요약과 무관하게 늘 있다. 분야를 통째로 빼면 화면이
    "오늘은 네 분야뿐인가"로 읽힌다.
    """
    stale = {**_group("macro", "거시", 300), "stale": True}
    failed = {**_group("general", "기타", 200), "status": "failed"}
    scenario = build_scenario(
        _snapshot([stale, failed, _group("equities", "주식", 100)]),
        production_date=date(2026, 9, 1),
    )

    assert [_label(s) for s in scenario.scenes if s.kind == "consensus"] == [
        "거시",
        "기타",
        "주식",
    ]


def test_a_card_without_any_summary_says_so_instead_of_inventing_one():
    bare = {**_group("macro", "거시", 300), "paragraph": "", "overview": ""}
    scenario = build_scenario(_snapshot([bare]), production_date=date(2026, 9, 1))

    card = next(s for s in scenario.scenes if s.kind == "consensus")
    assert "준비하지 못했습니다" in card.narration


def test_each_card_carries_the_event_count_and_24h_volume():
    """사용자가 카드에서 먼저 보는 두 숫자다."""
    scenario = build_scenario(
        _snapshot([_group("macro", "거시", 20_578_090)]),
        production_date=date(2026, 9, 1),
    )

    card = next(s for s in scenario.scenes if s.kind == "consensus")
    assert any("20건" in b for b in card.bullets)
    assert card.metric == "20.6M"
    assert any("이벤트" in bullet for bullet in card.bullets)
    assert any("24시간 거래량" in bullet for bullet in card.bullets)


def test_youtube_metadata_has_disclaimer_and_shorts_marker():
    scenario = build_scenario(
        _snapshot([_group("macro", "거시", 300)]),
        production_date=date(2026, 9, 1),
    )

    metadata = metadata_for(scenario)

    assert "#Shorts" in metadata["title"]
    assert "투자 조언이 아닙니다" in metadata["description"]
    assert len(metadata["title"]) <= 100


def test_specific_sentence_preserves_decimal_and_subject_without_ellipsis():
    group = _group("composite", "복합", 100, "전체적으로 전망이 분산되어 있다. 호르무즈 해협 정상화 가능성은 20.5%로 낮게 나타난다.")
    scenario = build_scenario(_snapshot([group]), production_date=date(2026, 9, 1))
    card = scenario.scenes[1]
    assert "호르무즈 해협 정상화 가능성은 20.5%" in card.narration
    assert "…" not in scenario.narration
    assert "전체적으로" not in card.narration


def test_stale_summary_is_not_narrated_but_its_sector_metrics_remain():
    group = {**_group("macro", "거시", 300, "연준 인하 가능성은 99.9%다."), "stale": True}
    card = build_scenario(_snapshot([group]), production_date=date(2026, 9, 1)).scenes[1]
    assert "99.9" not in card.narration
    assert card.metric == "300"
    assert "갱신 대기" in card.source_note


def test_volume_share_uses_only_selected_sectors_and_handles_zero_volume():
    scenario = build_scenario(_snapshot([_group("macro", "거시", 300), _group("equities", "주식", 100)]), production_date=date(2026, 9, 1))
    assert scenario.scenes[0].metric == "75%"
    assert "선정 2개 분야" in scenario.scenes[0].metric_label
    zero = build_scenario(_snapshot([_group("macro", "거시", 0)]), production_date=date(2026, 9, 1))
    assert zero.scenes[0].volume_share == 0


def test_long_summaries_are_cut_at_a_clause_not_mid_word():
    """그냥 자르면 카드에 '우세한 방향이 나…'처럼 단어 한가운데가 남는다."""
    text = "전체적으로 전망이 금리 정책과 경제 지표에 집중되어 있으며, 일부 질문에서는 우세한 방향이 나타난다."

    clipped = clip_at_sentence(text, 45)

    assert clipped.endswith("…")
    assert not clipped.rstrip("…").endswith(" ")
    # 잘린 자리가 어절 경계여야 한다 — 마지막 토막이 통째로 남는다.
    last = clipped.rstrip("…").split()[-1]
    assert last in {word.strip(",.") for word in text.split()}


def test_card_bullets_use_the_label_separator_the_renderer_expects():
    """렌더러가 " · "로 라벨과 값을 나눈다. 값 안에 그 구분자를 넣으면 잘린다."""
    scenario = build_scenario(
        _snapshot([_group("macro", "거시", 300)]), production_date=date(2026, 9, 1)
    )

    card = next(s for s in scenario.scenes if s.kind == "consensus")
    for bullet in card.bullets:
        label, separator, value = bullet.partition(" · ")
        assert separator, f"라벨이 없다: {bullet}"
        assert " · " not in value, f"값 안에 구분자가 또 있다: {bullet}"


def test_title_is_a_claim_with_a_number_not_a_category_label():
    """잘되는 경제 쇼츠의 제목은 예외 없이 주장이다.

    실측(슈카월드·부읽남TV 상위 14편): "미국 국채 '6% 금리' 찍히면 한국증시
    초토화됩니다", "SK하이닉스 '40조 소각' 우리가 오해하는 것". 전부 구체적
    숫자나 고유명사를 걸고 결과를 말한다. "오늘의 OO 컨센서스"는 분류 라벨이라
    아무것도 약속하지 않는다.
    """
    scenario = build_scenario(
        _snapshot([_group("macro", "거시·통화", 20_578_090)]),
        production_date=date(2026, 9, 1),
    )

    title = metadata_for(scenario)["title"]

    assert "거시·통화" in title
    assert "20.6M달러" in title
    assert "오늘의 폴리마켓 컨센서스" not in title


def test_picked_issue_replaces_the_generic_summary_on_screen_and_in_speech():
    """분야 문단의 총론 대신 뽑아 온 이슈를 말하고 보여 준다.

    문단의 첫 문장은 거의 항상 "전체적으로 … 분산되어 있다"라, 그대로 읽으면
    다섯 장면이 같은 말을 다섯 번 한다.
    """
    from polymarket_shorts.highlights import Highlight, Highlights

    picked = Highlights(
        hook="호르무즈 해협 정상화 가능성은 17.5%입니다.",
        picks={"macro": Highlight(
            key="macro", headline="연준 인상 88.5%",
            caption="연준의 금리 인상 가능성은 88.5%입니다.",
            narration="연준의 금리 인상 가능성은 88.5%로 나타났습니다.",
        )},
    )
    scenario = build_scenario(
        _snapshot([_group("macro", "거시", 300, "전체적으로 전망이 분산되어 있다. 연준 인상 가능성은 88.5%다.")]),
        production_date=date(2026, 9, 1),
        picker=lambda groups: picked,
    )

    intro, card = scenario.scenes[0], scenario.scenes[1]
    assert intro.narration.startswith("호르무즈 해협 정상화 가능성은 17.5%입니다.")
    assert intro.title == picked.hook
    assert card.title == "연준 인상 88.5%"
    assert card.kicker == "01 · 거시"       # 분야 이름은 kicker가 짊어진다
    assert card.body == "연준의 금리 인상 가능성은 88.5%입니다."
    assert card.narration == "거시. 연준의 금리 인상 가능성은 88.5%로 나타났습니다."
    assert "전체적으로" not in scenario.narration


def test_a_sector_without_a_pick_keeps_its_paragraph_summary():
    """한 분야를 못 뽑아도 그 분야만 기존 요약으로 세운다."""
    from polymarket_shorts.highlights import Highlight, Highlights

    picked = Highlights(hook="연준 인상 가능성은 88.5%입니다.", picks={"macro": Highlight(
        key="macro", headline="연준 인상 88.5%", caption="연준 인상 가능성은 88.5%입니다.",
        narration="연준의 금리 인상 가능성은 88.5%로 나타났습니다.")})
    scenario = build_scenario(
        _snapshot([
            _group("macro", "거시", 300, "연준 인상 가능성은 88.5%다."),
            _group("equities", "주식", 100, "삼성전자 상승 가능성은 40.5%다."),
        ]),
        production_date=date(2026, 9, 1),
        picker=lambda groups: picked,
    )

    assert scenario.scenes[2].title == "주식"
    assert "40.5%" in scenario.scenes[2].narration


def test_stale_sectors_are_never_offered_to_the_picker():
    """갱신 대기 문단은 말로 옮기지 않는다. 고를 대상에서도 빼야 한다."""
    seen = []
    build_scenario(
        _snapshot([
            {**_group("macro", "거시", 300, "연준 인상 가능성은 88.5%다."), "stale": True},
            _group("equities", "주식", 100, "삼성전자 상승 가능성은 40.5%다."),
        ]),
        production_date=date(2026, 9, 1),
        picker=lambda groups: seen.append([g["key"] for g in groups]) or None,
    )

    assert seen == [["equities"]]
