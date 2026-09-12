"""관리 웹 대시보드 기능 선언.

웹 서버는 텔레그램 Application 기동 후(post_init) 시작해야 하므로
bot/main.py가 이 기능의 활성 여부를 보고 bot.webadmin.start_web_admin을 호출한다.
데이터는 소유하지 않고 다른 기능의 bot_data 매니저를 읽기 전용으로
사용하며, 기능이 꺼져 있으면 해당 API가 404로 응답한다.
"""

from telegram_bot.features.base import FeatureSpec

FEATURE = FeatureSpec(
    key="web_admin",
    label="관리 웹",
)
