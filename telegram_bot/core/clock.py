"""앱 전체가 공유하는 단일 시각 기준(JST).

`datetime.now()`와 `date.today()`는 **호스트 타임존**을 따라간다. 이 앱의 "하루"는
항상 JST여야 하는데(장 일정, `/market`의 하루 경계, 보존 기간), 호스트에 맡기면
서버를 다른 타임존에 올리거나 컨테이너 기본값(UTC)으로 돌리는 순간 하루 경계가
통째로 밀린다. 그래서 여기서 명시적으로 고정한다. **`datetime.now()`를 직접
부르지 말고 `now()`·`today()`를 쓴다.**

예외가 하나 있다. Cloudflare 무료 할당량 리셋은 UTC 00시 기준이라
`llm/backends.py`의 `_next_utc_midnight()`는 의도적으로 UTC를 쓴다. 그건 우리
달력이 아니라 Cloudflare의 달력이다.

뉴스 기사 시각은 또 다른 축이다. 소스가 준 시각에는 소스의 타임존이 있고
(`news/utils.py`가 중국 소스를 CST로 간주해 JST로 변환한다), 여기 함수들은
"지금 몇 시인가"만 답한다. 둘을 섞지 않는다.
"""

from datetime import date, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9), name="JST")


def now() -> datetime:
    """JST 기준 현재 시각(timezone-aware)."""
    return datetime.now(JST)


def today() -> date:
    """JST 기준 오늘 날짜."""
    return now().date()


def ensure_jst(value: datetime) -> datetime:
    """저장된 타임스탬프를 JST aware로 정규화한다.

    오프셋이 없는 값은 JST로 간주한다. 이 앱이 기록한 시각은 언제나 JST이고,
    aware로 바꾸기 전에 쓴 `data/` 파일에는 오프셋이 없기 때문이다. 이 정규화가
    없으면 저장값(naive)과 `now()`(aware) 비교가 TypeError로 죽는다.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=JST)
    return value.astimezone(JST)
