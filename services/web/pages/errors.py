"""오류 화면도 공용 셸로 기동 시 한 번만 조립한다."""

from services.web.pages.shell import JS_UTIL, page

ERROR_HTML = {
    status: page(
        title, "", "<h1 class='ph'>" + title + "</h1><p>" + message
        + "</p><p><a href='/'>홈으로 돌아가기</a></p>",
        "<script>" + JS_UTIL + "</script>", description=message,
    )
    for status, title, message in (
        (404, "페이지를 찾을 수 없습니다", "주소가 올바른지 확인하거나 홈에서 다시 찾아 주세요."),
        (500, "잠시 후 다시 시도해 주세요", "요청을 처리하지 못했습니다. 잠시 후 다시 이용해 주세요."),
    )
}
