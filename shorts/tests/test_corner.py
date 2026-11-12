"""추적 코너: 기록 → 견줄 값 → 장면(`corner.py`, `scenario._watch_scene`, `render`의 추이선 화면)."""
import json
import re
from dataclasses import replace
from datetime import date, timedelta

import pytest

from conftest import requires_cjk_font
from polymarket_shorts import corner, render, speech, workflow
from polymarket_shorts.client import Snapshot
from polymarket_shorts.config import Settings
from polymarket_shorts.core.storage import write_json
from polymarket_shorts.highlights import HighlightError
from polymarket_shorts.pipeline import metadata_for
from polymarket_shorts.scenario import build_scenario
from polymarket_shorts.watch import history_path, ledger_dir, watch_dir

TODAY = date(2026, 11, 10)


def _points(*pairs):
    """(며칠 전, 확률) 쌍을 날짜별 기록으로."""
    return {(TODAY - timedelta(days=back)).isoformat(): value for back, value in pairs}


FED = _points((30, .655), (20, .60), (7, .70), (2, .735), (1, .775), (0, .715))


def _store(tmp_path, *, slot=None, markets=None, lines=None):
    settings = replace(Settings.from_env(), output_dir=tmp_path / "shorts")
    slot = {"topic_key": "fed decision", "event_id": "fed", "title": "Fed Decision in January?", "theme": "연준 금리",
            "event_type": "exclusive_multi", "since": "2026-11-08", "score": .9, **(slot or {})}
    markets = markets or [
        {"id": "hold", "label": "No change", "question": "Will the Fed hold rates in January?", "yes": .715},
        {"id": "hike", "label": "25 bps increase", "question": "Will the Fed raise rates by 25 bps in January?",
         "yes": .255}]
    write_json(watch_dir(settings) / "topics.json", {"tracked": [slot], "updated_on": TODAY.isoformat(), "ranking": [
        {"id": "fed", "title": "Fed Decision in January?", "theme": "연준 금리", "importance": 3, "score": .9},
        {"id": "iran", "title": "Will the U.S. invade Iran before 2027?", "theme": "미-이란 전쟁", "importance": 3,
         "score": .85}]})
    write_json(ledger_dir(settings) / f"{TODAY.isoformat()}.json", {
        "events": [{"id": slot["event_id"], "end_date": "2027-01-28T04:59:00Z"}],
        "details": {slot["event_id"]: markets}})
    write_json(history_path(settings, slot["event_id"]), {"markets": lines or {
        "hold": {"label": "No change", "points": FED},
        "hike": {"label": "25 bps increase", "points": _points((1, .185), (0, .255))}}})
    return settings


def _script(issues, settings, **kwargs):
    (issue,) = issues
    return [{"id": issue["id"], "headline": "연준 1월 금리 결정", "question": "연준이 1월에 금리를 어떻게 결정할까요?",
             "context": "연준의 1월 결정은 미 국채 금리와 달러의 방향에 닿습니다.",
             "market_labels": [{"id": "hold", "label": "동결", "outlook": "연준이 1월에 금리를 동결할"},
                               {"id": "hike", "label": "25bp 인상"}][:len(issue["markets"])]}]


def test_trend_compares_today_with_the_window_start_and_only_a_real_yesterday():
    measured = corner.trend(FED, TODAY)

    assert (measured["now"], measured["base"], measured["base_days"]) == (.715, .655, 30)
    assert measured["yesterday"] == .775 and measured["week"] == .70
    assert [day for day, _ in measured["points"]] == sorted(FED)
    # 어제 기록이 없으면 그제 값을 어제라고 부르지 않는다. 창 밖의 옛 기록은 버린다.
    gap = _points((40, .2), (30, .655), (20, .6), (7, .7), (2, .735), (0, .715))
    assert corner.trend(gap, TODAY)["yesterday"] is None and len(corner.trend(gap, TODAY)["points"]) == 5
    # 오늘 값이 없거나 기록이 모자라면 코너를 세우지 않는다.
    assert corner.trend(_points((30, .6), (20, .6), (7, .7), (2, .7), (1, .7)), TODAY) is None
    assert corner.trend(_points((2, .7), (1, .7), (0, .7)), TODAY) is None


def test_focus_follows_the_leader_but_keeps_a_ladder_rung_while_it_is_open():
    rungs = [{"id": "a", "yes": .30}, {"id": "b", "yes": .52}, {"id": "c", "yes": .97}]

    assert corner._focus({"event_type": "exclusive_multi", "focus": "a"}, rungs)["id"] == "c"
    # 사다리는 한 번 정한 칸을 유지한다 — 날마다 50%에 가까운 칸으로 옮기면 어제와 견줄 수 없다.
    assert corner._focus({"event_type": "independent_multi", "focus": "a"}, rungs)["id"] == "a"
    assert corner._focus({"event_type": "independent_multi"}, rungs)["id"] == "b"
    # 정한 칸이 사실상 닫히면(95% 초과) 50%에 가장 가까운 칸으로 옮긴다.
    assert corner._focus({"event_type": "independent_multi", "focus": "c"}, rungs)["id"] == "b"


def test_build_writes_the_script_once_and_reads_long_only_when_it_matters(tmp_path, monkeypatch):
    settings = _store(tmp_path)
    calls = []
    monkeypatch.setattr(corner, "write_issues", lambda *a, **kw: calls.append(a[0]) or _script(*a, **kw))

    views, audit = corner.build(settings, TODAY)

    (view,) = views
    assert audit == {"llm_calls": 1, "skipped": [], "scenes": 1, "same_theme": []}
    # 단일 답 묶음은 1위가 바뀔 수 있어 상위 답의 이름을 함께 받는다.
    assert [market["id"] for market in calls[0][0]["markets"]] == ["hold", "hike"]
    assert (view["headline"], view["label"], view["outlook"]) == ("연준 1월 금리 결정", "동결", "연준이 1월에 금리를 동결할")
    assert view["long"] and view["first_time"] and view["day"] == 3 and view["previous_leader"] is None
    saved = json.loads((watch_dir(settings) / "topics.json").read_text(encoding="utf-8"))["tracked"][0]
    assert saved["focus"] == "hold" and saved["deep"] == {"date": "2026-11-10", "value": .715, "first": True}

    # 같은 날 다시 만들면 저장한 글을 쓰고 여전히 길게 읽는다. 다음 날부터는 숫자만 짧게 잇는다.
    assert corner.build(settings, TODAY)[0][0]["long"] and len(calls) == 1
    tomorrow = TODAY + timedelta(days=1)
    lines = {"hold": {"label": "No change", "points": {**FED, tomorrow.isoformat(): .73}}}
    write_json(history_path(settings, "fed"), {"markets": lines})
    ledger = json.loads((ledger_dir(settings) / "2026-11-10.json").read_text(encoding="utf-8"))
    write_json(ledger_dir(settings) / f"{tomorrow.isoformat()}.json", ledger)
    (later,), audit = corner.build(settings, tomorrow)
    assert not later["long"] and not later["first_time"] and audit["llm_calls"] == 0
    # 마지막으로 길게 다룬 뒤 10%포인트 넘게 움직이면 다시 길게 다룬다.
    lines["hold"]["points"][tomorrow.isoformat()] = .60
    write_json(history_path(settings, "fed"), {"markets": lines})
    assert corner.build(settings, tomorrow)[0][0]["long"]


def test_only_one_topic_is_read_long_each_day(tmp_path, monkeypatch):
    """두 주제를 같은 날 길게 읽으면 영상이 한도를 넘는다. 둘째 주제는 다음 날 차례를 받는다."""
    settings = _store(tmp_path)
    monkeypatch.setattr(corner, "write_issues", _script)
    path = watch_dir(settings) / "topics.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["tracked"].append({**state["tracked"][0], "topic_key": "fed again"})
    write_json(path, state)

    for _ in range(2):   # 같은 날 다시 만들어도 같은 주제가 길다
        first, second = corner.build(settings, TODAY)[0]
        assert (first["long"], first["first_time"]) == (True, True)
        assert (second["long"], second["first_time"]) == (False, False)
    tracked = json.loads(path.read_text(encoding="utf-8"))["tracked"]
    assert tracked[0]["deep"]["date"] == "2026-11-10" and "deep" not in tracked[1]

    tomorrow = TODAY + timedelta(days=1)
    write_json(history_path(settings, "fed"), {"markets": {
        "hold": {"label": "No change", "points": {**FED, tomorrow.isoformat(): .72}}}})
    write_json(ledger_dir(settings) / f"{tomorrow.isoformat()}.json",
               json.loads((ledger_dir(settings) / "2026-11-10.json").read_text(encoding="utf-8")))
    first, second = corner.build(settings, tomorrow)[0]
    assert not first["long"] and (second["long"], second["first_time"]) == (True, True)


def test_build_lists_other_topics_of_the_same_theme(tmp_path, monkeypatch):
    settings = _store(tmp_path)
    monkeypatch.setattr(corner, "write_issues", _script)
    path = watch_dir(settings) / "topics.json"
    write_json(path, {**json.loads(path.read_text(encoding="utf-8")), "importance": {
        "fed decision": {"theme": "연준 금리"}, "another fed rate hike": {"theme": "연준 금리"},
        "u s invade iran": {"theme": "미-이란 전쟁"}}})

    assert corner.build(settings, TODAY)[1]["same_theme"] == ["another fed rate hike", "fed decision"]


def test_build_names_yesterdays_leader_when_the_top_answer_changes(tmp_path, monkeypatch):
    settings = _store(tmp_path, lines={
        "hold": {"label": "No change", "points": {**FED, (TODAY - timedelta(days=1)).isoformat(): .40}},
        "hike": {"label": "25 bps increase", "points": _points((1, .55), (0, .255))}})
    monkeypatch.setattr(corner, "write_issues", _script)

    assert corner.build(settings, TODAY)[0][0]["previous_leader"] == "25bp 인상"


def test_build_skips_a_topic_for_the_day_and_says_why(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise HighlightError("이슈 fed label에 원문에 없는 숫자가 있습니다")
    settings = _store(tmp_path)
    monkeypatch.setattr(corner, "write_issues", refuse)
    views, audit = corner.build(settings, TODAY)
    assert views == [] and audit["skipped"][0]["reason"].startswith("글을 쓰지 못함")

    # 화면 한 줄을 넘는 이름은 저장하지 않는다 — 저장하면 매일 렌더가 실패한다. 다음 날 다시 쓴다.
    long_name = _store(tmp_path / "long")
    monkeypatch.setattr(corner, "write_issues", lambda issues, settings, **kw: [
        {**_script(issues, settings)[0], "market_labels": [{"id": "hold", "label": "가" * 25}, {"id": "hike", "label": "나"}]}])
    assert corner.build(long_name, TODAY)[1]["skipped"][0]["reason"] == "고른 질문의 이름이 화면 한 줄을 넘음"
    assert "script" not in json.loads((watch_dir(long_name) / "topics.json").read_text(encoding="utf-8"))["tracked"][0]

    thin = _store(tmp_path / "thin", lines={"hold": {"label": "No change", "points": _points((1, .7), (0, .715))}})
    assert corner.build(thin, TODAY)[1]["skipped"] == [{"event_id": "fed", "reason": "지난 기록이 모자람"}]
    # 질문 원문이 없는 옛 기록으로는 글을 쓸 수 없다.
    bare = _store(tmp_path / "bare", markets=[{"id": "hold", "label": "No change", "yes": .715}])
    assert corner.build(bare, TODAY)[1]["skipped"][0]["reason"] == "오늘 기록에 개별 질문이 없음"


def test_status_reports_tracked_topics_with_their_move_and_the_ranking(tmp_path, monkeypatch):
    settings = _store(tmp_path)
    monkeypatch.setattr(corner, "write_issues", _script)
    corner.build(settings, TODAY)
    write_json(watch_dir(settings) / "pins.json", {"topics": ["fed decision"]})

    status = corner.status(settings)

    (item,) = status["tracked"]
    assert item["headline"] == "연준 1월 금리 결정" and item["label"] == "동결" and item["pinned"]
    assert (item["now"], item["base"], item["base_days"], item["yesterday"]) == (.715, .655, 30, .775)
    assert [(row["id"], row["tracked"]) for row in status["ranking"]] == [("fed", True), ("iran", False)]


def test_watch_words_pick_particles_by_sound():
    assert speech.speak_leader_change("25bp 인상", "동결") == "가장 유력한 전망이 어제 25bp 인상에서 오늘 동결로 바뀌었습니다."
    assert speech._with_direction("25bp 인상") == "25bp 인상으로" and speech._with_direction("4.5%") == "4.5%로"
    assert speech._with_direction("상위 3") == "상위 3으로" and speech._with_direction("상위 7") == "상위 7로"
    assert speech.watch_opener("연준 결정", 0, first_time=True) == "먼저 앞으로 계속 지켜볼 질문, 연준 결정부터 보겠습니다."
    assert speech.watch_opener("연준 결정", 1, first_time=False) == "계속 지켜보는 또 다른 질문은 연준 결정입니다."
    assert "계속 지켜보는 질문 2개의 흐름을 먼저 보고" in speech.opening_line(3, TODAY, tracked=2)
    assert "질문 하나의 흐름을 먼저 보고, 금융시장과 관련된 새 질문 하나를" in speech.opening_line(1, TODAY, tracked=1)


def _view(**changes):
    return {"event_id": "fed", "topic_key": "fed decision", "title": "Fed Decision in January?", "theme": "연준 금리",
            "since": "2026-11-08", "score": .9, "day": 3, "first_time": False, "end_date": "2027-01-28T04:59:00Z",
            "market_id": "hold", "market_question": "Will the Fed hold rates in January?",
            "headline": "연준 1월 금리 결정", "question": "연준이 1월에 금리를 어떻게 결정할까요?",
            "context": "연준의 1월 결정은 미 국채 금리와 달러의 방향에 닿습니다", "label": "동결",
            "outlook": "연준이 1월에 금리를 동결할", "previous_leader": None, "long": False,
            **corner.trend(FED, TODAY), **changes}


def test_tracked_scenes_stand_after_the_intro_and_hand_over_to_new_issues(issue_source):
    snapshot, _, _, issue, script = issue_source
    script = {**script, "lead_in": "먼저 연준의 10월 결정부터 보겠습니다."}

    scenario = build_scenario(snapshot, [issue], [script], production_date=TODAY,
                              tracked=[_view(), _view(event_id="cpi", headline="미국 물가 상승률", long=True)])

    assert [scene.kind for scene in scenario.scenes] == ["intro", "watch", "watch", "consensus", "outro"]
    assert "계속 지켜보는 질문 2개의 흐름을 먼저 보고, 금융시장과 관련된 새 질문 하나를" in scenario.scenes[0].narration
    short, long, fresh = scenario.scenes[1:4]
    # 짧은 날은 여는 말 뒤에 오늘 확률뿐이다. 언제보다 얼마 올랐다·떨어졌다는 말은 읽지 않는다 — 추이선이 화면에
    # 있다(운영자 지시 2026-11-13). 대비 수치는 화면 글(칩·검수 문구)에만 남는다.
    assert short.narration == ("먼저 계속 지켜보는 질문, 연준 1월 금리 결정부터 보겠습니다. "
                               "컨센서스 참여자의 71.5%는 연준이 1월에 금리를 동결할 것을 기대하고 있습니다.")
    assert not re.search("포인트|어제|일 전|일주일", short.narration + long.narration)
    assert short.kicker == "계속 지켜보는 질문 · 3일째" and short.options == (("동결", "71.5%", .715),)
    assert short.bullets == ("30일 전 · 65.5%", "어제 · 77.5%", "종료 예정 · 2027-01-28 세계 표준시")
    assert short.body == "동결 — 예 71.5%\n30일 전 65.5% → 오늘 71.5% (+6%p)\n어제 77.5% (−6%p)"
    assert short.trend[0] == ("2026-10-11", .655) and short.trend[-1] == ("2026-11-10", .715)
    assert short.narration[:round(short.options_at * len(short.narration))].endswith("보겠습니다.")
    assert short.event_id == "fed" and short.market_ids == ("hold",)
    # 긴 날은 해설을 읽은 뒤에 숫자로 간다. 질문 문장은 읽지 않고 검수 근거에만 남긴다.
    assert long.narration.startswith("계속 지켜보는 또 다른 질문은 미국 물가 상승률입니다. 연준의 1월 결정은")
    assert long.narration.index("방향에 닿습니다.") < long.narration.index("71.5%")
    assert "결정할까요?" not in long.narration and "질문(읽지 않음): 연준이 1월에 금리를 어떻게 결정할까요?" in long.evidence
    # 코너 뒤의 첫 새 이슈는 영상의 첫 질문이 아니다.
    assert fresh.narration.startswith("이제 오늘의 새 질문으로 넘어가겠습니다. 연준의 10월 결정부터 보겠습니다.")
    description = metadata_for(scenario)["description"]
    assert "계속 지켜보는 질문: 연준 1월 금리 결정, 미국 물가 상승률\n오늘 다룬 이슈: 연준의 금리 결정\n" in description
    # 저장한 원고에서 되읽어도 같은 장면이다(추이선은 리스트로 돌아온다).
    assert workflow._scenario(json.loads(json.dumps(scenario.to_dict()))).scenes[1] == short


def test_a_filled_in_issue_does_not_open_with_first(issue_source):
    """빈자리를 채운 이슈는 혼자 원고를 받아 "먼저 …"로 열린다. 첫 장면이 아니면 그 말을 뗀다."""
    snapshot, _, _, issue, script = issue_source
    issues = [{**issue, "id": name} for name in ("e1", "e2")]
    scripts = [{**script, "id": name, "lead_in": "먼저 연준의 10월 결정부터 보겠습니다."} for name in ("e1", "e2")]

    scenario = build_scenario(snapshot, issues, scripts, production_date=TODAY)

    assert scenario.scenes[1].narration.startswith("먼저 연준의 10월 결정부터 보겠습니다.")
    assert scenario.scenes[2].narration.startswith("연준의 10월 결정부터 보겠습니다.")


def test_removing_tracked_scenes_in_review_updates_the_description(issue_source):
    snapshot, _, _, issue, script = issue_source
    scenario = build_scenario(snapshot, [issue], [script], production_date=TODAY,
                              tracked=[_view(), _view(event_id="cpi", headline="미국 물가 상승률")])
    metadata = metadata_for(scenario)
    patch = {"summary": "추적 장면 삭제", "changes": [], "metadata": {}, "tts_rate": "+0%"}

    _, one, _ = workflow.apply_edit(scenario, metadata, {**patch, "scene_order": [1, 2, 4, 5]}, Settings.from_env())
    _, none, _ = workflow.apply_edit(scenario, metadata, {**patch, "scene_order": [1, 4, 5]}, Settings.from_env())

    assert "계속 지켜보는 질문: 연준 1월 금리 결정\n오늘 다룬 이슈" in one["description"]
    assert "계속 지켜보는 질문" not in none["description"] and "오늘 다룬 이슈: 연준의 금리 결정" in none["description"]


def _watch_scene(issue_source):
    snapshot, _, _, issue, script = issue_source
    return build_scenario(snapshot, [issue], [script], production_date=TODAY, tracked=[_view()]).scenes[1]


def test_trend_scale_keeps_small_moves_small():
    assert render._trend_scale([.59, .775]) == (55, 80)
    # 1%포인트 흔들림이 화면 가득한 절벽으로 보이지 않게 최소 10%포인트 폭을 둔다.
    low, high = render._trend_scale([.71, .72])
    assert high - low == 10 and low <= 71 and high >= 72
    assert render._trend_scale([.004, .012]) == (0, 10) and render._trend_scale([.97, .995]) == (90, 100)


def test_watch_beats_draw_the_line_first_and_show_the_number_when_spoken(issue_source):
    scene = _watch_scene(issue_source)

    beats = render._watch_beats(scene, 12.0)

    # 선은 장면이 열리자마자 그려지고, 오늘 숫자는 확률을 말하기 시작할 때 뜬다.
    drawing = beats[:render.TREND_STEPS]
    assert all(beat[0] == "question" and beat[3] == 0 and 0 < beat[4] < 1 for beat in drawing)
    assert [beat[4] for beat in drawing] == sorted(beat[4] for beat in drawing)
    assert beats[-2][3:] == (0, 1.0) and beats[-1][0] == "trend" and beats[-1][3:] == (1, 1.0)
    assert sum(beat[1] for beat in beats[:-1]) == pytest.approx(12.0 * scene.options_at)
    assert sum(beat[1] for beat in beats) == pytest.approx(12.0)
    # 숫자가 바로 나오는 짧은 장면은 선을 그리는 움직임 없이 완성된 선으로 연다.
    assert [beat[3:] for beat in render._watch_beats(replace(scene, options_at=.1), 1.0)] == [(0, 1.0), (1, 1.0)]
    # 그 사이 길이면 선을 남은 시간에 맞춰 빨리 그린다.
    quick = render._watch_beats(replace(scene, options_at=.1), 5.0)
    assert 2 < len(quick) < render.TREND_STEPS and sum(beat[1] for beat in quick[:-1]) == pytest.approx(1.0)


@requires_cjk_font
def test_watch_frame_grows_the_line_and_adds_the_number_last(tmp_path, cjk_font, issue_source):
    from PIL import Image, ImageChops

    scene = _watch_scene(issue_source)
    paths = [tmp_path / name for name in ("half.png", "line.png", "full.png")]
    render.render_frame(scene, paths[0], font_path=cjk_font, index=2, total=4, shown=0, reveal=.3)
    render.render_frame(scene, paths[1], font_path=cjk_font, index=2, total=4, shown=0)
    render.render_frame(scene, paths[2], font_path=cjk_font, index=2, total=4)
    half, line, full = (Image.open(path).convert("RGB") for path in paths)

    plot = (render.SAFE_LEFT + render.CARD_PAD, render.WATCH_PLOT[0], render.BODY_RIGHT - render.WATCH_AXIS,
            render.WATCH_PLOT[1])
    green = tuple(int(render._COLORS["brand"][n:n + 2], 16) for n in (1, 3, 5))

    def inked(image):
        return sum(1 for pixel in image.crop(plot).getdata() if pixel == green)

    # 선은 왼쪽에서부터 자라고, 다 그려지기 전에는 오른쪽 끝에 닿지 않는다.
    assert 0 < inked(half) < inked(line) == inked(full)
    right_edge = (plot[2] - 80, plot[1], plot[2], plot[3])
    assert green not in set(half.crop(right_edge).getdata()) and green in set(line.crop(right_edge).getdata())
    # 오늘 확률과 변화 폭은 말이 닿을 때(shown=1) 선 위쪽에 뜬다.
    head = (render.SAFE_LEFT + render.CARD_PAD, render.WATCH_CARD_TOP + 110, render.BODY_RIGHT,
            render.WATCH_PLOT[0] - 10)
    assert ImageChops.difference(half.crop(head), line.crop(head)).getbbox() is None
    assert ImageChops.difference(line.crop(head), full.crop(head)).getbbox() is not None
    # 자막 자리(안전 영역 바닥 위 두 줄)는 비워 둔다.
    band = (0, render.FOOTER_Y + 70, render.WIDTH, render.SAFE_BOTTOM)
    assert ImageChops.difference(full.crop(band), render._plain_backdrop().crop(band)).getbbox() is None


@requires_cjk_font
def test_video_gives_tracked_scenes_their_own_frames_and_shifts_the_list_header(tmp_path, monkeypatch, issue_source,
                                                                                cjk_font):
    from polymarket_shorts import blender_render
    from polymarket_shorts import tts
    from polymarket_shorts.tts import Word

    snapshot, _, _, issue, script = issue_source
    scenario = build_scenario(snapshot, [issue], [script], production_date=TODAY, tracked=[_view()])
    words, clock = [], 0.0
    for scene in scenario.scenes:
        row = []
        for text in tts.spoken_text(scene.narration).split():
            row.append(Word(clock, clock + .3, text.strip(".,?")))
            clock += .4
        words.append(tuple(row))
        clock += 2  # 장면 사이의 쉼. 다음 장면의 첫 자막이 이만큼 앞당겨 뜬다.
    frames, lists = [], []
    monkeypatch.setattr(render, "probe_duration", lambda *a, **kw: clock)
    monkeypatch.setattr(render, "render_frame", lambda scene, path, **kw: frames.append((scene.kind, kw)))
    monkeypatch.setattr(render, "render_list_frame", lambda scenes, path, **kw: lists.append(kw))
    monkeypatch.setattr(render, "_caption_frame", lambda *a, **kw: None)
    monkeypatch.setattr(blender_render, "compose", lambda **kw: None)

    render.render_video(scenario, audio_path=tmp_path / "a.mp3", scene_words=words, output_path=tmp_path / "v.mp4",
                        work_dir=tmp_path, font_path=cjk_font, blender_bin="blender", ffprobe_bin="ffprobe")

    watch = [kw for kind, kw in frames if kind == "watch"]
    assert watch[0]["shown"] == 0 and watch[0]["reveal"] < 1 and watch[-1]["shown"] == 1
    assert watch[-1]["reveal"] == 1.0 and len(watch) == render.TREND_STEPS + 2
    # 목록 화면의 진행 점은 도입과 추적 장면 뒤의 자리를 가리킨다.
    assert lists and all(kw["leading"] == 2 and kw["active"] == 0 for kw in lists)
    timeline = json.loads((tmp_path / "v.timeline.json").read_text(encoding="utf-8"))
    assert [row["beat"] for row in timeline if row["scene"] == "연준 1월 금리 결정"] == ["question", "trend"]


def test_daily_selection_leaves_tracked_topics_out_and_picks_fewer_new_issues(tmp_path, monkeypatch, event_factory):
    from polymarket_shorts import highlights, pipeline

    summary = {"generation_id": "g1", "generated_at": "2026-11-10T20:00:00+09:00", "freshness": {"state": "normal"}}
    snapshot = Snapshot(summary, (
        event_factory("fed", title="Fed Decision in January?", end_date="2027-01-28T04:59:00Z"),
        event_factory("oil", title="Will crude oil hit $100 by December 31?", tags=["economy"],
                      end_date="2026-12-31T00:00:00Z", event_type="binary"),
        event_factory("hike", title="Another Fed rate hike in 2026?", end_date="2026-12-31T00:00:00Z",
                      event_type="binary"),
    ), {})

    class Client:
        requests = {"details": 0, "news": 0}
        def __init__(self, url):
            pass
        def snapshot(self):
            return snapshot
        def confirm(self, generation):
            pass

    asked = {}
    def select(candidates, settings, *, rejected=None, maximum=None):
        asked.update(ids=[row["id"] for row in candidates], maximum=maximum)
        return []
    monkeypatch.setattr(pipeline, "PolymarketWebClient", Client)
    monkeypatch.setattr(pipeline, "refresh_watch", lambda *a, **kw: {"ledger": {}})
    monkeypatch.setattr(pipeline, "build_corner", lambda settings, today: (
        [_view(), _view(event_id="cpi", topic_key="inflation")],
        {"llm_calls": 0, "skipped": [], "scenes": 2, "same_theme": ["another fed rate hike", "fed decision"]}))
    monkeypatch.setattr(pipeline, "select_issues", select)
    monkeypatch.setattr(highlights, "chat_json", lambda *a, **kw: pytest.fail("고를 이슈가 없으면 원고를 묻지 않는다"))
    settings = replace(Settings.from_env(), output_dir=tmp_path / "shorts")

    assert pipeline.prepare_daily(settings, TODAY, tmp_path) is None

    # 추적 중인 연준 결정과 같은 흐름의 "추가 인상"은 새 이슈 후보에서 빠지고, 추적 장면이 둘이면 새 이슈는 둘까지다.
    assert asked == {"ids": ["oil"], "maximum": 2}
    audit = json.loads((tmp_path / "selection.json").read_text(encoding="utf-8"))
    assert audit["watch"]["corner"]["scenes"] == 2
    assert {row["id"] for row in audit["recently_featured"]} == {"fed", "hike"}

    # 코너를 만들다 실패해도 제작은 새 이슈만으로 이어진다.
    def broken(settings, today):
        raise KeyError("since")
    monkeypatch.setattr(pipeline, "build_corner", broken)
    assert pipeline.prepare_daily(settings, TODAY, tmp_path) is None
    assert sorted(asked["ids"]) == ["fed", "hike", "oil"] and asked["maximum"] is None
    audit = json.loads((tmp_path / "selection.json").read_text(encoding="utf-8"))
    assert "since" in audit["watch"]["corner"]["error"]
