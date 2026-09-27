from datetime import datetime
from zoneinfo import ZoneInfo


def now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Seoul"))
