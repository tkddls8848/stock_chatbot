"""시작·도움말·시스템 제어 기능 선언."""

import logging

from services.telegram_bot.features.base import CommandSpec, FeatureSpec, MenuSpec, StatusReportSpec
from services.telegram_bot.features.system_admin.handlers import cmd_help, cmd_start, cmd_system
from services.telegram_bot.features.system_admin.llm_status import notify_quota_exhaustion, render_llm_status

logger = logging.getLogger(__name__)


def _install_jobs(scheduler, app) -> None:
    scheduler.add_job(
        notify_quota_exhaustion, trigger="interval", seconds=30,
        args=[app], id="llm_quota_notice", max_instances=1, coalesce=True,
    )


FEATURE = FeatureSpec(
    key="system_admin",
    label="시스템 관리",
    status_reports=(StatusReportSpec("llm", "LLM 회로 상태", render_llm_status),),
    install_jobs=_install_jobs,
    data_files=("storage/bot/system_admin/llm_quota_notice.json",),
    commands=(
        CommandSpec("start", "사용 안내", cmd_start),
        CommandSpec(
            "system",
            "시스템 상태",
            cmd_system,
            usage="[features|<기능 상태>]",
        ),
        CommandSpec("help", "명령어 안내", cmd_help),
    ),
    menus=(
        # 하단 고정 메뉴에서는 자주 쓰는 조회 버튼과 분리해 마지막 줄에 둔다.
        MenuSpec("⚙️ 시스템", "nav:system", 2, "⚙️ 관리", 2),
        MenuSpec("❔ 도움말", "nav:help", 3),
    ),
)
