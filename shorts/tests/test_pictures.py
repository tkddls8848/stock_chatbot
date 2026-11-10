"""질문마다 붙는 그림: 그리기·검사(`media.py`) → 그림 창과 여는 컷(`render.py`) → 검수에서 빼기(`workflow.py`)."""
import base64
from dataclasses import replace
from datetime import date
import io
import json

from PIL import Image, ImageChops
import pytest
import requests

from conftest import requires_cjk_font
from polymarket_shorts import corner, media, render, workflow
from polymarket_shorts.config import Settings
from polymarket_shorts.pipeline import metadata_for
from polymarket_shorts.scenario import build_scenario
from polymarket_shorts.watch import watch_dir

TODAY = date(2026, 11, 10)
FED = {"2026-10-11": .655, "2026-10-21": .60, "2026-11-03": .70, "2026-11-08": .735, "2026-11-09": .775,
       "2026-11-10": .715}


def _view(**changes):
    return {"event_id": "fed", "topic_key": "fed decision", "title": "Fed Decision in January?", "theme": "연준 금리",
            "since": "2026-11-08", "score": .9, "day": 3, "first_time": False, "end_date": "2027-01-28T04:59:00Z",
            "market_id": "hold", "market_question": "Will the Fed hold rates in January?",
            "headline": "연준 1월 금리 결정", "question": "연준이 1월에 금리를 어떻게 결정할까요?",
            "context": "연준의 1월 결정은 미 국채 금리와 달러의 방향에 닿습니다", "label": "동결",
            "outlook": "연준이 1월에 금리를 동결할", "previous_leader": None, "long": False,
            "image_scene": "a brass balance scale on dark marble", **corner.trend(FED, TODAY), **changes}


@pytest.fixture
def scenario(issue_source):
    snapshot, _, _, issue, script = issue_source
    return build_scenario(snapshot, [issue], [{**script, "image_scene": "oil tankers crossing a narrow strait"}],
                          production_date=TODAY, tracked=[_view()])


@pytest.fixture
def settings(tmp_path):
    return replace(Settings.from_env(), output_dir=tmp_path / "shorts", editor_account_id="acct",
                   editor_api_token="token", visuals_enabled=True)


@pytest.fixture
def painter(monkeypatch):
    """그림 모델과 검사 모델 대신 서는 것. `flags`에 넣어 둔 값이 차례로 검사 결과가 된다."""
    drawn, flags = [], []
    def draw(subject, size, settings):
        drawn.append((subject, size))
        return Image.new("RGB", size, (200, 120, 40))
    monkeypatch.setattr(media, "_draw", draw)
    monkeypatch.setattr(media, "_flagged", lambda image, settings: flags.pop(0) if flags else [])
    return drawn, flags


def _picture(path, color=(200, 120, 40), size=(1248, 400)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format="JPEG")
    return str(path)


# ── 그리기 ──

def test_each_question_gets_its_picture_and_a_tracked_one_keeps_it(scenario, settings, tmp_path, painter):
    drawn, _ = painter
    day = tmp_path / "day"

    made, report = media.attach_pictures(scenario, day, settings)

    watch, issue = made.scenes[1], made.scenes[2]
    kept = watch_dir(settings) / "pictures"
    # 추적 장면은 그림 창용 가로 그림과 여는 컷용 세로 그림을 추적 폴더에, 새 이슈는 가로 그림을 그날 폴더에 둔다.
    assert watch.picture == str(kept / "fed-wide.jpg") and watch.cut == str(kept / "fed-tall.jpg")
    assert issue.picture == str(day / "pictures" / "e1-wide.jpg") and issue.cut == ""
    assert drawn == [("a brass balance scale on dark marble", media.WINDOW_SIZE),
                     ("a brass balance scale on dark marble", media.CUT_SIZE),
                     ("oil tankers crossing a narrow strait", media.WINDOW_SIZE)]
    with Image.open(watch.cut) as tall:
        assert tall.size == media.CUT_SIZE
    assert made.scenes[0].picture == made.scenes[-1].picture == ""
    assert watch.evidence[-1] == "그림(AI 생성, 글자·사람 검사 통과): a brass balance scale on dark marble"
    assert [row["outcome"] for row in report["pictures"]] == ["새로 그림"] * 3
    # 다음 날(다른 날짜 폴더)에도 추적 주제는 같은 그림을 쓰고, 새 이슈만 새로 그린다.
    again, report = media.attach_pictures(scenario, tmp_path / "next", settings)
    assert again.scenes[1].picture == watch.picture and len(drawn) == 4
    assert [row["outcome"] for row in report["pictures"]] == ["있던 그림", "있던 그림", "새로 그림"]


def test_a_picture_showing_people_or_text_is_redrawn_and_given_up_after_three(scenario, settings, tmp_path, painter):
    drawn, flags = painter
    flags.extend([["글자"], [], ["사람"], ["글자"], ["사람", "글자"]])

    made, report = media.attach_pictures(scenario, tmp_path / "day", settings)

    # 가로 그림은 두 번째에 통과했고, 세로 그림은 세 번 모두 걸려 여는 컷 없이 간다. 제작은 멈추지 않는다.
    assert made.scenes[1].picture and made.scenes[1].cut == ""
    assert report["pictures"][1]["outcome"] == "실패: 3번 모두 사람·글자가 보임"
    assert not (watch_dir(settings) / "pictures" / "fed-tall.jpg").exists()
    assert len(drawn) == 2 + 3 + 1


def test_a_failed_drawing_leaves_the_scene_as_it_was(scenario, settings, tmp_path, monkeypatch):
    def fail(subject, size, settings):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(media, "_draw", fail)

    made, report = media.attach_pictures(scenario, tmp_path / "day", settings)

    assert made.scenes == scenario.scenes
    assert all(row["outcome"].startswith("실패: down") for row in report["pictures"])


def test_pictures_stop_when_they_have_taken_too_long(scenario, settings, tmp_path, painter, monkeypatch):
    drawn, _ = painter
    clock = iter([0.0, 1.0, 500.0, 501.0, 502.0, 503.0])
    monkeypatch.setattr(media.time, "monotonic", lambda: next(clock))

    made, report = media.attach_pictures(scenario, tmp_path / "day", settings)

    # 첫 장을 그린 뒤 한도를 넘겼다. 남은 그림은 묻지 않고 그림 없이 간다.
    assert len(drawn) == 1 and made.scenes[1].picture and not made.scenes[1].cut and not made.scenes[2].picture
    assert [row["outcome"] for row in report["pictures"][1:]] == ["실패: 그림에 쓸 시간을 넘김"] * 2


def test_nothing_is_drawn_without_credentials_or_for_a_crowded_list(scenario, settings, tmp_path, painter, issue_source):
    drawn, _ = painter
    for off in (replace(settings, editor_api_token=""), replace(settings, visuals_enabled=False)):
        assert media.attach_pictures(scenario, tmp_path / "day", off)[0] is scenario
    assert not drawn
    # 새 이슈가 넷이면 목록 화면에 그림 창을 둘 자리가 없어 이슈 그림을 그리지 않는다. 추적 장면은 그대로 그린다.
    issues = tuple(replace(scenario.scenes[2], event_id=f"e{n}") for n in range(4))
    crowded = replace(scenario, scenes=(*scenario.scenes[:2], *issues, scenario.scenes[-1]))
    made, _ = media.attach_pictures(crowded, tmp_path / "day", settings)
    assert [bool(scene.picture) for scene in made.scenes] == [False, True, False, False, False, False, False]


def test_models_are_asked_in_their_own_request_shapes(settings, monkeypatch):
    buffer = io.BytesIO()
    Image.new("RGB", (32, 16), (10, 20, 30)).save(buffer, format="PNG")
    sent = []

    class Reply:
        def __init__(self, result):
            self.result = result
        def raise_for_status(self):
            pass
        def json(self):
            return {"result": self.result}

    def post(url, **kwargs):
        sent.append((url, kwargs))
        return Reply({"image": base64.b64encode(buffer.getvalue()).decode()} if "files" in kwargs
                     else {"description": " Yes, there is"})
    monkeypatch.undo()  # conftest가 막아 둔 요청 함수를 되돌리고, 그 아래의 requests.post만 바꾼다.
    monkeypatch.setattr(media.requests, "post", post)

    image = media._draw("a globe", (1248, 400), settings)
    flagged = media._flagged(image, settings)

    assert image.size == (32, 16) and flagged == ["사람", "글자"]
    url, drawing = sent[0]
    assert url.endswith(f"/accounts/acct/ai/run/{settings.image_model}")
    assert drawing["headers"] == {"Authorization": "Bearer token"}
    assert drawing["files"]["width"] == (None, "1248") and drawing["files"]["height"] == (None, "400")
    assert "a globe" in drawing["files"]["prompt"][1] and "no text" in drawing["files"]["prompt"][1]
    assert sent[1][0].endswith(settings.vision_model) and "yes or no" in sent[1][1]["json"]["prompt"]


def test_the_tracked_script_keeps_its_picture_description(tmp_path, monkeypatch):
    from polymarket_shorts.core.storage import write_json
    from polymarket_shorts.watch import history_path, ledger_dir

    settings = replace(Settings.from_env(), output_dir=tmp_path / "shorts")
    slot = {"topic_key": "fed decision", "event_id": "fed", "title": "Fed Decision in January?", "theme": "연준 금리",
            "event_type": "binary", "since": "2026-11-08", "score": .9}
    write_json(watch_dir(settings) / "topics.json", {"tracked": [slot], "updated_on": TODAY.isoformat()})
    write_json(ledger_dir(settings) / f"{TODAY.isoformat()}.json", {
        "events": [{"id": "fed", "end_date": "2027-01-28T04:59:00Z"}],
        "details": {"fed": [{"id": "hold", "label": "No change", "question": "Will the Fed hold?", "yes": .715}]}})
    write_json(history_path(settings, "fed"), {"markets": {"hold": {"label": "No change", "points": FED}}})
    monkeypatch.setattr(corner, "write_issues", lambda issues, settings, **kw: [{
        "id": "fed", "headline": "연준 1월 금리 결정", "question": "연준이 1월에 금리를 동결할까요?",
        "context": "연준의 결정은 금리에 닿습니다.", "market_labels": [{"id": "hold", "label": "동결"}],
        "image_scene": "a brass balance scale on dark marble"}])

    (view,), _ = corner.build(settings, TODAY)

    assert view["image_scene"] == "a brass balance scale on dark marble"
    stored = json.loads((watch_dir(settings) / "topics.json").read_text(encoding="utf-8"))
    assert stored["tracked"][0]["script"]["image_scene"] == view["image_scene"]


def test_scenes_carry_the_description_to_draw(scenario, issue_source):
    assert scenario.scenes[1].visual_query.endswith("topic: a brass balance scale on dark marble")
    assert scenario.scenes[2].visual_query.endswith("topic: oil tankers crossing a narrow strait")
    # 묘사가 없는 추적 주제(예전에 쓴 글)는 기본 풍경을 그린다.
    snapshot, _, _, issue, script = issue_source
    plain = build_scenario(snapshot, [issue], [script], production_date=TODAY, tracked=[_view(image_scene=None)])
    assert "topic: a quiet financial district skyline" in plain.scenes[1].visual_query


def test_daily_preparation_attaches_pictures_and_records_them(tmp_path, monkeypatch, issue_source):
    from polymarket_shorts import pipeline

    snapshot, candidate, _, issue, script = issue_source

    class Client:
        requests = {}
        def __init__(self, url):
            pass
        def snapshot(self):
            return snapshot
        def market_news(self):
            return []
    monkeypatch.setattr(pipeline, "PolymarketWebClient", Client)
    monkeypatch.setattr(pipeline, "refresh_watch", lambda *a, **kw: {})
    monkeypatch.setattr(pipeline, "build_corner", lambda settings, today: ([], {"same_theme": []}))
    monkeypatch.setattr(pipeline, "select_issues", lambda candidates, settings, **kw: [candidate])
    monkeypatch.setattr(pipeline, "prepare_issue", lambda *a: issue)
    monkeypatch.setattr(Client, "detail", lambda self, *a: {}, raising=False)
    monkeypatch.setattr(Client, "news", lambda self, *a, **kw: [], raising=False)
    monkeypatch.setattr(Client, "confirm", lambda self, generation: None, raising=False)
    monkeypatch.setattr(pipeline, "write_issues", lambda issues, settings, **kw: [script])
    seen = {}
    def attach(scenario, day_dir, settings):
        seen["day_dir"] = day_dir
        return replace(scenario, scenes=tuple(replace(scene, picture="p.jpg") if scene.kind == "consensus" else scene
                                              for scene in scenario.scenes)), {"pictures": ["기록"]}
    monkeypatch.setattr(pipeline, "attach_pictures", attach)

    made = pipeline.prepare_daily(replace(Settings.from_env(), output_dir=tmp_path / "shorts"), TODAY, tmp_path)

    assert seen["day_dir"] == tmp_path and made.scenes[1].picture == "p.jpg"
    assert json.loads((tmp_path / "scenario.json").read_text(encoding="utf-8"))["scenes"][1]["picture"] == "p.jpg"
    assert json.loads((tmp_path / "selection.json").read_text(encoding="utf-8"))["pictures"] == {"pictures": ["기록"]}


# ── 화면 ──

WINDOW = (render.SAFE_LEFT + 40, render.PICTURE_TOP + 20, render.SAFE_RIGHT - 40, render.PICTURE_TOP + 60)


def _frame(tmp_path, name, draw):
    path = tmp_path / name
    draw(path)
    with Image.open(path) as image:
        return image.convert("RGB")


@requires_cjk_font
def test_a_tracked_scene_shows_its_picture_behind_the_title_only(scenario, tmp_path, cjk_font):
    scene = scenario.scenes[1]
    pictured = replace(scene, picture=_picture(tmp_path / "wide.jpg"))
    plain = _frame(tmp_path, "plain.png", lambda path: render.render_frame(scene, path, font_path=cjk_font, index=2, total=4))
    shown = _frame(tmp_path, "shown.png", lambda path: render.render_frame(pictured, path, font_path=cjk_font, index=2, total=4))
    lost = _frame(tmp_path, "lost.png", lambda path: render.render_frame(
        replace(scene, picture=str(tmp_path / "missing.jpg")), path, font_path=cjk_font, index=2, total=4))

    # 그림은 머리 줄 아래 창 안에만 있다. 창 위쪽은 그림 색 그대로이고, 카드부터 아래는 그림 없는 화면과 같다.
    red, green, blue = shown.getpixel((render.WIDTH // 2, render.PICTURE_TOP + 30))
    assert red > 150 and red > green > blue
    below = (0, render.WATCH_CARD_TOP - 50, render.WIDTH, render.HEIGHT)
    assert ImageChops.difference(shown.crop(below), plain.crop(below)).getbbox() is None
    above = (0, 0, render.WIDTH, render.PICTURE_TOP)
    assert ImageChops.difference(shown.crop(above), plain.crop(above)).getbbox() is None
    # 제목은 창의 어둡게 누른 아래쪽에 선다.
    title = shown.crop((render.SAFE_LEFT, render.PICTURE_BOTTOM - 120, render.SAFE_RIGHT, render.PICTURE_BOTTOM - 20))
    assert max(pixel[2] for pixel in title.getdata()) > 230
    # 그림 파일을 읽지 못하면 그림 없는 화면이다.
    assert ImageChops.difference(lost, plain).getbbox() is None


@requires_cjk_font
def test_the_opening_cut_fills_the_screen_and_leaves_the_caption_band_dark(scenario, tmp_path, cjk_font):
    scene = replace(scenario.scenes[1], cut=_picture(tmp_path / "tall.jpg", size=(720, 1280)))

    cut = _frame(tmp_path, "cut.png", lambda path: render.render_cut_frame(scene, path, font_path=cjk_font, index=2, total=4))

    assert cut.size == (render.WIDTH, render.HEIGHT)
    # 그림은 화면 가장자리까지 차고, 대상이 놓이는 가운데 위쪽이 가장 밝다.
    middle, low = cut.getpixel((20, 680)), cut.getpixel((20, render.SAFE_BOTTOM - 40))
    assert middle[0] > 120 and middle[0] > low[0] * 2
    # 코너 이름표(초록)와 제목(흰색)은 자막 자리 위에 선다.
    text = cut.crop((0, render.CUT_TEXT_BOTTOM - 320, render.WIDTH, render.CUT_TEXT_BOTTOM))
    colors = set(text.getdata())
    assert (61, 220, 132) in colors and any(min(color) > 235 for color in colors)
    captions = cut.crop((0, render.CUT_TEXT_BOTTOM + 40, render.WIDTH, render.SAFE_BOTTOM))
    assert max(max(color) for color in captions.getdata()) < 110


def test_the_cut_takes_at_most_half_of_the_words_before_the_number(scenario):
    scene = scenario.scenes[1]

    long = render._watch_beats(replace(scene, options_at=.5), 20.0, cut=True)
    short = render._watch_beats(replace(scene, options_at=.3), 9.0, cut=True)

    assert long[0][:2] == ("cut", render.CUT_SECONDS) and long[1][0] == "question"
    assert len(long) == 1 + render.TREND_STEPS + 2 and sum(beat[1] for beat in long) == pytest.approx(20.0)
    # 숫자만 짧게 잇는 날: 여는 말 2.7초 가운데 컷이 절반, 남은 시간에 선이 그려진 뒤 숫자가 뜬다.
    assert short[0][:2] == ("cut", pytest.approx(1.35))
    assert sum(beat[1] for beat in short[:-1]) == pytest.approx(2.7) and short[-1][0] == "trend"
    assert 5 < sum(beat[0] == "question" for beat in short) < render.TREND_STEPS
    assert render._watch_beats(scene, 20.0)[0][0] == "question"


@requires_cjk_font
def test_the_list_opens_a_picture_window_only_when_every_question_has_one(scenario, tmp_path, cjk_font):
    row = scenario.scenes[2]
    first = replace(row, picture=_picture(tmp_path / "a.jpg", (200, 120, 40)))
    second = replace(row, title="둘째 질문", picture=_picture(tmp_path / "b.jpg", (40, 120, 200)))
    three = tuple((label, "40%", .4) for label in ("가", "나", "다"))

    # 선택지가 둘이면 큰 창, 셋이면 목록이 좁아지지 않게 낮은 창이다.
    assert render._list_window([first, second])[0] == render.PICTURE_BOTTOM + render.LIST_PICTURE_GAP
    assert render._list_window([first, replace(second, options=three)])[0] == \
        render.LIST_PICTURE_BOTTOM + render.LIST_PICTURE_GAP
    # 하나라도 그림이 없거나 질문이 넷이면 창을 두지 않는다.
    assert render._list_window([first, row]) == (render.LIST_TOP, None)
    assert render._list_window([first, second, first, second]) == (render.LIST_TOP, None)

    def draw(active, previous, progress):
        return lambda path: render.render_list_frame([first, second], path, font_path=cjk_font, active=active,
                                                     previous=previous, progress=progress, shown=None)
    spot = (render.WIDTH // 2, render.PICTURE_TOP + 100)
    opened = _frame(tmp_path, "one.png", draw(0, None, 1.0)).getpixel(spot)
    moving = _frame(tmp_path, "mid.png", draw(1, 0, .3)).getpixel(spot)
    settled = _frame(tmp_path, "two.png", draw(1, 0, 1.0)).getpixel(spot)
    # 창은 지금 질문의 그림이고, 질문이 바뀌는 동안 앞 그림에서 새 그림으로 녹는다.
    assert opened[0] > 150 > opened[2] and settled[2] > 150 > settled[0]
    assert settled[0] < moving[0] < opened[0] and opened[2] < moving[2] < settled[2]
    # 목록은 창 아래에서 시작한다.
    plain = _frame(tmp_path, "plain.png", lambda path: render.render_list_frame(
        [row, replace(row, title="둘째 질문")], path, font_path=cjk_font, active=0, previous=None, progress=1.0,
        shown=None))
    strip = (render.SAFE_LEFT, render.LIST_TOP, render.SAFE_RIGHT, render.LIST_TOP + 60)
    assert ImageChops.difference(plain.crop(strip), render._plain_backdrop().crop(strip)).getbbox() is not None


@requires_cjk_font
def test_video_opens_a_tracked_scene_with_its_cut(scenario, tmp_path, monkeypatch, cjk_font):
    from polymarket_shorts import blender_render
    from polymarket_shorts.tts import Word

    tall = _picture(tmp_path / "tall.jpg", size=(720, 1280))
    scenario = replace(scenario, scenes=tuple(replace(scene, cut=tall) if scene.kind == "watch" else scene
                                              for scene in scenario.scenes))
    words, clock = [], 0.0
    for scene in scenario.scenes:
        row = []
        for text in scene.narration.split():
            row.append(Word(clock, clock + .3, text.strip(".,?")))
            clock += .4
        words.append(tuple(row))
        clock += 2
    order = []
    monkeypatch.setattr(render, "probe_duration", lambda *a, **kw: clock)
    monkeypatch.setattr(render, "render_cut_frame", lambda scene, path, **kw: order.append("cut"))
    monkeypatch.setattr(render, "render_frame", lambda scene, path, **kw: order.append(scene.kind))
    monkeypatch.setattr(render, "render_list_frame", lambda scenes, path, **kw: order.append("list"))
    monkeypatch.setattr(render, "_caption_frame", lambda *a, **kw: None)
    monkeypatch.setattr(blender_render, "compose", lambda **kw: None)

    render.render_video(scenario, audio_path=tmp_path / "a.mp3", scene_words=words, output_path=tmp_path / "v.mp4",
                        work_dir=tmp_path, font_path=cjk_font, blender_bin="blender", ffprobe_bin="ffprobe")

    assert order.count("cut") == 1 and order.index("cut") == order.index("watch") - 1
    timeline = json.loads((tmp_path / "v.timeline.json").read_text(encoding="utf-8"))
    beats = [row for row in timeline if row["scene"] == "연준 1월 금리 결정"]
    assert [row["beat"] for row in beats] == ["cut", "question", "trend"] and 0 < beats[0]["duration"] <= 2.0


# ── 검수 ──

def test_review_can_only_take_a_picture_out(scenario):
    pictured = replace(scenario, scenes=tuple(
        replace(scene, picture="wide.jpg", cut="tall.jpg") if scene.kind == "watch" else scene
        for scene in scenario.scenes))
    metadata = metadata_for(pictured)
    patch = {"summary": "그림 빼기", "metadata": {}, "scene_order": [1, 2, 3, 4], "tts_rate": "+0%"}

    edited, _, _ = workflow.apply_edit(pictured, metadata, {**patch, "changes": [{"scene": 2, "picture": ""}]},
                                       Settings.from_env())

    assert edited.scenes[1].picture == "" and edited.scenes[1].cut == ""
    assert edited.scenes[1].narration == pictured.scenes[1].narration
    with pytest.raises(workflow.ReviewError, match="뺄 수만"):
        workflow.apply_edit(pictured, metadata, {**patch, "changes": [{"scene": 2, "picture": "/etc/passwd"}]},
                            Settings.from_env())
