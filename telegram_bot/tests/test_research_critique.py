"""리서치 결과의 마켓 뷰 반론(view_critique) 파싱·렌더링 검증."""
import json

from telegram_bot.llm.market_view import MarketViewAnalyzer
from telegram_bot.research.results import format_result_sections


def _format_message(result: dict) -> str:
    """섹션을 합쳐 렌더링 결과 전체를 한 문자열로 본다."""
    return "\n\n".join(
        format_result_sections(result, {"add": [], "remove": []}, 3, 5)
    )


def _analyzer(tmp_path) -> MarketViewAnalyzer:
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("test prompt", encoding="utf-8")
    return MarketViewAnalyzer(
        backend=None,  # 파싱만 검증하므로 호출은 하지 않는다.
        timeout=10,
        num_predict=512,
        prompt_file=prompt_file,
    )


# evidence는 news_items의 id 참조이므로 파서에 같은 목록을 준다.
_NEWS_ITEMS = [
    {
        "source": "CLS",
        "title": "규제 발표",
        "published_at": "2026-05-02 10:15",
        "url": "https://www.cls.cn/detail/1",
    }
]


def _parse(tmp_path, payload: dict) -> dict:
    return _analyzer(tmp_path)._parse_analysis(
        json.dumps(payload, ensure_ascii=False), news_items=_NEWS_ITEMS
    )


def test_parse_normalizes_view_critique(tmp_path):
    result = _parse(
        tmp_path,
        {
            "summary": "요약",
            "actions": [],
            "risks": [],
            "view_critique": [
                {
                    "point": "반도체 수출 규제가 뷰와 상충한다",
                    "severity": 0.7,
                    "evidence": {"id": 0},
                },
                # severity가 숫자가 아니면 None으로 관용 처리
                {"point": "지표 둔화", "severity": "high"},
                # 문자열 항목도 수용
                "위안화 약세 지속",
            ],
        },
    )
    critique = result["view_critique"]
    assert len(critique) == 3
    assert critique[0]["point"] == "반도체 수출 규제가 뷰와 상충한다"
    assert critique[0]["severity"] == 0.7
    assert critique[0]["evidence"]["title"] == "규제 발표"
    assert critique[0]["evidence"]["url"] == "https://www.cls.cn/detail/1"
    assert critique[1]["severity"] is None
    assert critique[2] == {"point": "위안화 약세 지속", "severity": None, "evidence": None}


def test_parse_view_critique_fail_soft(tmp_path):
    # 필드 누락 → 빈 목록
    assert _parse(tmp_path, {"summary": "s", "actions": [], "risks": []})[
        "view_critique"
    ] == []
    # 형식 오류(비배열, point 없는 dict, 빈 문자열) → 항목 제외, 예외 없음
    assert _parse(
        tmp_path,
        {"summary": "s", "actions": [], "risks": [], "view_critique": "말도 안 됨"},
    )["view_critique"] == []
    result = _parse(
        tmp_path,
        {
            "summary": "s",
            "actions": [],
            "risks": [],
            "view_critique": [{"severity": 0.5}, "", 42, {"point": "  유효  "}],
        },
    )
    assert result["view_critique"] == [
        {"point": "유효", "severity": None, "evidence": None}
    ]


def test_parse_view_critique_caps_at_limit(tmp_path):
    result = _parse(
        tmp_path,
        {
            "summary": "s",
            "actions": [],
            "risks": [],
            "view_critique": [f"반론 {i}" for i in range(9)],
        },
    )
    assert [c["point"] for c in result["view_critique"]] == [
        f"반론 {i}" for i in range(5)
    ]


def test_format_message_renders_critique_section():
    result = {
        "summary": "요약",
        "actions": [],
        "risks": [],
        "view_critique": [
            {
                "point": "수요 둔화 신호",
                "severity": 0.6,
                "evidence": {"title": "판매 부진 <보도>", "source": "Sina"},
            },
            {"point": "정책 리스크", "severity": None, "evidence": None},
        ],
    }
    text = _format_message(result)
    assert "🗣 내 뷰 반론" in text
    assert "[60%] 수요 둔화 신호" in text
    assert "판매 부진 &lt;보도&gt; (Sina)" in text  # HTML 이스케이프 확인
    assert "- 정책 리스크" in text


def test_format_message_critique_empty():
    result = {"summary": "요약", "actions": [], "risks": []}
    text = _format_message(result)
    assert "🗣 내 뷰 반론" in text
    assert "상충하는 근거 없음" in text
