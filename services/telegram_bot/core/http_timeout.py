"""타임아웃 없이 나가는 `requests` 호출에 기본 타임아웃을 채운다.

akshare는 내부에서 `requests.get(url)`처럼 타임아웃 없이 부른다. `requests`는 이때
"타임아웃 없음"을 명시적으로 넘기므로 `socket.setdefaulttimeout()`도 먹지 않는다
(서버에서 실측: 기본값 5초를 둬도 응답 없는 sina 호출이 120초 넘게 붙잡혔다).
그래서 `Session.request` 한 곳에서 비어 있는 타임아웃만 채운다. 호출자가 직접 넘긴
값은 건드리지 않는다.

2026-09-24 공유 호스트에서 sina·eastmoney가 응답하지 않자 섹터 조회 하나가 수십 분
걸렸고(리서치 한 번 33분), 같은 조회에 묶인 브리핑 버튼이 동시 처리 칸
(`TELEGRAM_CONCURRENT_UPDATES`)을 채워 하단 버튼이 전부 응답하지 않았다.

텔레그램 연결은 httpx라 영향이 없다.
"""

from __future__ import annotations

import requests

_MARKER = "_stock_chatbot_default_timeout"


def install_default_requests_timeout(timeout: tuple[float, float]) -> None:
    """(연결, 읽기) 초. 여러 번 불러도 한 번만 감싼다."""
    original = requests.sessions.Session.request
    if getattr(original, _MARKER, False):
        return

    def request(self, method, url, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = timeout
        return original(self, method, url, **kwargs)

    setattr(request, _MARKER, True)
    requests.sessions.Session.request = request
