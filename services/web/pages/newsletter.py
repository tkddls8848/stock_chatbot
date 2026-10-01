"""편지 안의 해지 링크가 여는 화면(`/newsletter/unsubscribe`). 로그인이 필요 없다.

링크를 여는 것(GET)만으로는 끄지 않는다 — 메일 보안 검사기가 편지 속 링크를 미리 열어
보므로, 여는 순간 끄면 받는 사람이 누르지도 않은 해지가 일어난다. 버튼이 같은 주소로
`POST`를 보내고, 메일 서비스의 원클릭 해지(RFC 8058)도 같은 `POST`를 부른다.
세 화면은 같은 셸을 써서 CSP 해시가 하나다.
"""

from services.web.pages.shell import I_DOC, JS_UTIL, h1, page

_DESCRIPTION = "눈치 뉴스레터 수신을 끄는 화면입니다."


_STYLE = ("<style>.nl-go{min-height:var(--ctl);border:1px solid var(--gold);border-radius:var(--r1);"
          "background:var(--gold);color:#fff;padding:8px 14px;font:inherit;font-weight:700;cursor:pointer}</style>")


def _page(body: str) -> str:
    return page("뉴스레터 해지", "", _STYLE + h1(I_DOC, "뉴스레터 해지") + "<div class='login-panel'>" + body + "</div>",
                "<script>" + JS_UTIL + "</script>", description=_DESCRIPTION)


UNSUBSCRIBE_CONFIRM_HTML = _page(
    "<p>이 주소로 오는 눈치 일일 시장 요약을 끕니다. 저장한 이메일 주소도 함께 지웁니다.</p>"
    # action을 비워 두면 지금 주소(서명 포함)로 그대로 보낸다.
    "<form method='post'><button class='nl-go' type='submit'>뉴스레터 끄기</button></form>"
)
UNSUBSCRIBE_DONE_HTML = _page(
    "<p>뉴스레터를 껐습니다. 더는 보내지 않고 저장한 주소도 지웠습니다.</p>"
    "<p>다시 받으려면 <a href='/portfolio'>내 자산</a>에 로그인해 새로 신청하세요.</p>"
)
UNSUBSCRIBE_INVALID_HTML = _page(
    "<p>해지 링크가 맞지 않습니다. 주소를 바꾼 뒤라면 새 주소로 받은 편지의 링크를 쓰세요.</p>"
    "<p><a href='/portfolio'>내 자산</a>에 로그인하면 화면에서 바로 끌 수 있습니다.</p>"
)
