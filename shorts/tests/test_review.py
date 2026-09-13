from zoneinfo import ZoneInfo

from polymarket_shorts import review
from polymarket_shorts.scenario import Scenario, Scene


SEOUL = ZoneInfo("Asia/Seoul")
METADATA = {"title": "제목 | 2026.09.13 #Shorts", "description": "설명", "tags": ["예측시장"]}


def _produced(tmp_path, body=b"video-bytes"):
    video = tmp_path / "short.mp4"
    video.write_bytes(body)
    scenario = Scenario(
        "2026-09-13", "g1", "2026-09-13T15:00:00+09:00",
        (Scene("intro", "질문", "", "화면 문구", "들어가는 멘트입니다."),),
    )
    review.write_review(
        tmp_path, scenario=scenario, metadata=METADATA,
        video=video, duration=125.5, timezone=SEOUL,
    )
    return video


def test_review_script_carries_the_narration_and_publish_metadata(tmp_path):
    _produced(tmp_path)

    script = review.read_script(tmp_path)

    assert "들어가는 멘트입니다." in script
    assert "화면 문구" in script
    assert METADATA["title"] in script
    assert "2분 05.5초" in script
