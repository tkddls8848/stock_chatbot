import re
from datetime import date

import pytest

from polymarket_shorts.pipeline import metadata_for
from polymarket_shorts.scenario import build_scenario


def test_video_uses_individual_questions_and_preserves_probabilities(issue_source):
    snapshot, _, _, issue, script = issue_source
    scenario = build_scenario(snapshot, [issue], [script], production_date=date(2026, 9, 23))
    assert len(scenario.scenes) == 3  # Missing sectors are not filled with summaries.
    scene = scenario.scenes[1]
    # 화면은 선택지마다 이름과 '예' 확률 하나다. 아니오는 예의 나머지라 두 번 적지 않는다.
    assert scene.options == (("10월 금리 동결", "55%", .55), ("10월 금리 25bp 인상", "40%", .4))
    assert scene.body == "10월 금리 동결 — 예 55%\n10월 금리 25bp 인상 — 예 40%"
    # 화면은 정확한 수치를, 음성은 그 수치가 뜻하는 바를 맡는다.
    assert scene.metric == "55%" and scene.probability == .55
    assert "55%" in scene.narration and "40%" in scene.narration
    assert scene.volume_share == 0
    assert scene.market_ids == ("m1", "m2")
    assert "개별 판정 시각과 다를 수 있음" in " ".join(scene.evidence)
    assert scenario.source_written_at == snapshot.summary["generated_at"]
    meta = metadata_for(scenario)
    assert meta["title"] == "2026-09-23 시장 컨센서스"   # 제목은 날짜 + 시장 컨센서스로 통일
    assert script["headline"] in meta["description"]
    # 한국에서 공식적으로 접근이 막힌 서비스라 공개 설명·태그에 이름과 원문 링크를 싣지 않는다.
    public = meta["title"] + meta["description"] + " ".join(meta["tags"])
    assert "polymarket" not in public.lower() and "폴리마켓" not in public
    assert "예측시장" not in public and "베팅" not in public


def test_wrong_market_association_is_rejected_before_render(issue_source):
    snapshot, _, _, issue, script = issue_source
    script["market_labels"].reverse()
    with pytest.raises(ValueError, match="확률"):
        build_scenario(snapshot, [issue], [script], production_date=date(2026, 9, 23))


def _five_issues(issue_source):
    """같은 원자료로 이슈 다섯 개짜리 하루치를 만든다. 반복은 여기서만 드러난다."""
    snapshot, _, _, issue, script = issue_source
    issues, scripts = [], []
    for number in range(5):
        issues.append({**issue, "id": f"e{number}"})
        scripts.append({**script, "id": f"e{number}"})
    return build_scenario(snapshot, issues, scripts, production_date=date(2026, 9, 23))


def test_issue_narration_gives_the_reason_first_then_the_percent(issue_source):
    """선정 이유 → 질문 → 확률 순서로 말한다(운영자 결정 2026-09-27).

    확률은 화면과 같은 퍼센트로 말하고, 예·아니오 쌍은 읽지 않는다.
    """
    scenario = _five_issues(issue_source)
    scene = scenario.scenes[1]
    context_at = scene.narration.index("자금조달")
    question_at = scene.narration.index("?")
    percent_at = scene.narration.index("55%")
    assert context_at < question_at < percent_at
    assert not re.search(r"예 .*아니오", scene.narration)
    # 선택지는 확률을 말하기 시작할 때 뜬다.
    assert 0 < scene.options_at < 1
    assert "전체의 55%" in scene.narration[round(scene.options_at * len(scene.narration)):]
    assert scenario.scenes[1].options[0] == ("10월 금리 동결", "55%", .55)


def test_the_same_closing_line_is_not_repeated_every_scene(issue_source):
    """장면마다 "…확인하세요"를 붙이면 같은 당부를 다섯 번 듣는다."""
    scenario = _five_issues(issue_source)

    assert scenario.narration.count("확인해 보세요") == 1
    assert scenario.scenes[-1].narration.endswith("눈치 닷 라이브에 방문하여 확인해 보세요.")
    # 장면별 확인점은 검수 기록(review.md)에만 남는다 — 말하지 않는 당부를
    # 화면에만 띄우면 보는 것과 듣는 것이 어긋난다.
    assert scenario.scenes[1].takeaway == "연준의 공식 결정문을 확인하세요."
    endings = [scene.narration[-12:] for scene in scenario.scenes[1:-1]]
    assert len(set(endings)) == 1  # 같은 해설 문장이면 끝도 같다(입력 탓)
    # 둘째 이슈부터는 다음 테마를 알리며 연다(운영자 결정 2026-09-28).
    for scene in scenario.scenes[2:-1]:
        assert scene.narration.startswith("다음은 ") and "테마의 주요 컨센서스 현황을 살펴봅니다." in scene.narration


def test_screen_text_never_says_betting_in_any_language(issue_source):
    """예전 도입 kicker는 화면에 "BETTING ISSUES"를 그대로 띄우고 있었다."""
    scenario = _five_issues(issue_source)

    for scene in scenario.scenes:
        shown = f"{scene.kicker} {scene.title} {scene.body} {scene.takeaway} {scene.narration}"
        assert "bet" not in shown.lower()
        assert "베팅" not in shown and "배팅" not in shown
        assert "polymarket" not in shown.lower() and "폴리마켓" not in shown


def test_the_opening_scene_hooks_with_the_first_question_not_an_issue_count(issue_source):
    """도입의 "선정한 개별 이슈 2" 큰 숫자 카드는 계속 볼 이유가 되지 못한다."""
    snapshot, _, _, issue, script = issue_source
    scenario = build_scenario(snapshot, [issue], [script], production_date=date(2026, 9, 23))

    intro = scenario.scenes[0]
    assert intro.body == "연준은 10월에 금리를 어떻게 결정할까요?"
    assert intro.metric == "" and intro.metric_label == "" and intro.options == ()
    # 편수는 사라지지 않고 잔글씨 한 줄로 내려간다.
    assert intro.bullets == ("오늘의 질문 · 1개",)


def test_the_closing_scene_is_a_short_notice_that_matches_what_is_spoken(issue_source):
    """마무리의 "조건" 큰 카드는 자리만 차지했다. 화면은 마무리 멘트와 같은 말을 한다."""
    snapshot, _, _, issue, script = issue_source
    scenario = build_scenario(snapshot, [issue], [script], production_date=date(2026, 9, 23))

    outro = scenario.scenes[-1]
    assert outro.metric == "" and outro.options == () and outro.takeaway == ""
    assert outro.body == "질문마다 조건이 다릅니다.\n자세한 내용은 nunchi.live에서 확인해 보세요."
    # 짧은 고지 두 줄이고, 마무리 멘트가 같은 말을 한다(주소는 소리로 "눈치 닷 라이브").
    assert len(outro.body) < 50
    assert "눈치 닷 라이브" in outro.narration and "질문마다 조건이" in outro.narration


def test_the_screen_never_shows_a_line_the_voice_does_not_say(issue_source):
    """화면에 남아 있던 고정 CTA("…확인하세요")는 음성에서 이미 뺀 문구다."""
    scenario = _five_issues(issue_source)

    for scene in scenario.scenes[:-1]:
        shown = " ".join((scene.body, *(label for label, _, _ in scene.options), *scene.bullets))
        assert "확인하세요" not in shown, shown
    # 마무리 한 번만 남고, 그 문장은 멘트에도 그대로 있다.
    closing = scenario.scenes[-1]
    assert "nunchi.live" in closing.body and "눈치 닷 라이브" in closing.narration
