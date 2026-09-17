"""공개 웹 화면. 화면 하나가 파일 하나이고 공통 뼈대는 `shell.py`에 있다.

화면이 아닌 정적 산출물(`robots.txt`)도 같은 이유로 여기 둔다 — 기동 시 한 번
조립되고 요청마다 다시 만들지 않는다.
"""

from web.pages.about import ABOUT_HTML
from web.pages.market import INDEX_HTML
from web.pages.polymarket import POLYMARKET_HTML
from web.pages.research import RESEARCH_HTML
from web.pages.robots import ROBOTS_TXT

__all__ = [
    "ABOUT_HTML",
    "INDEX_HTML",
    "POLYMARKET_HTML",
    "RESEARCH_HTML",
    "ROBOTS_TXT",
]
