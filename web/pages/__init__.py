"""공개 웹 화면. 화면 하나가 파일 하나이고 공통 뼈대는 `shell.py`에 있다."""

from web.pages.about import ABOUT_HTML
from web.pages.market import INDEX_HTML
from web.pages.polymarket import POLYMARKET_HTML
from web.pages.research import RESEARCH_HTML

__all__ = [
    "ABOUT_HTML",
    "INDEX_HTML",
    "POLYMARKET_HTML",
    "RESEARCH_HTML",
]
