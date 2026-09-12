"""`/system prefilter` 화면 문자열.

사전선별이 자기 보고서를 직접 그린다. 예전에는 system_admin이 `report()`의
dict 모양과 `SHADOW_CAVEATS`를 알고 그렸는데, 그러면 사전선별의 관측 항목을
바꿀 때마다 남의 기능 파일을 함께 고쳐야 했다.
"""

from telegram_bot.features.news_prefilter.service import SHADOW_CAVEATS


def format_prefilter_report(report: dict) -> str:
    """두 정책의 불일치와 판별력, CPU 예산을 한 화면에 그린다."""
    mode = report["mode"]
    cpu = report["cpu"]
    used_hours = float(cpu["used_seconds"]) / 3600
    budget_hours = float(cpu["budget_seconds"]) / 3600
    lines = [
        "<b>뉴스 사전선별 (로컬 사건 메모리)</b>",
        f"모드: <b>{mode}</b>"
        + ("  — 점수만 기록하고 번역 순서는 최신순 그대로" if mode == "shadow" else ""),
        "",
        "<b>수집</b>",
        f"  주기 {report['cycles']}회 · 후보 {report['candidates_seen']:,}건"
        f" · 관측 기록 {report['logged']:,}건",
        f"  사건 메모리 {report['events']:,}건"
        + (
            f" · 신규 사건 비율 {report['new_event_ratio']:.0%}"
            if report["new_event_ratio"] is not None
            else ""
        ),
        "",
        "<b>두 정책의 불일치</b>",
        f"  둘 다 선택 {report['agree']}건 · 최신순만 {report['latest_only']}건"
        f" · 사전선별만 {report['prefilter_only']}건",
    ]
    if not report["latest_only"] and not report["prefilter_only"]:
        lines.append("  두 정책이 같은 기사를 고르고 있어 바꿀 이유가 아직 없습니다.")

    lines.extend(["", "<b>판별력</b>"])
    auc = report["auc"]
    if auc is None:
        lines.append(
            f"  라벨 {report['labeled']}건(양성 {report['positives']}건)"
            " — 양쪽 라벨이 모두 모이기 전에는 계산하지 않습니다."
        )
    else:
        lines.append(
            f"  라벨 {report['labeled']}건 중 양성 {report['positives']}건 · "
            f"점수 AUC <b>{auc:.3f}</b> (0.5 = 무작위)"
        )
    if report["model_trained_at"]:
        lines.append(
            f"  모델 학습 {report['model_trained_at']} · "
            f"검증 AP {report['model_validation_ap']} "
            f"(기저 {report['model_prevalence']}) · 라벨 {report['model_label_count']}건"
        )
    else:
        lines.append("  ⏸ 아직 학습된 모델이 없습니다(라벨 120건·5일이 모이면 시작).")

    foreground_hours = float(cpu.get("foreground_seconds", 0.0)) / 3600
    lines.extend(
        [
            "",
            "<b>CPU 예산</b>",
            f"  보정 {used_hours:.2f}h / {budget_hours:.2f}h 사용 (UTC {cpu['utc_day']})",
            f"  참고: foreground(선별) {foreground_hours:.2f}h — 이 예산을 깎지 않음",
            "",
            "<b>섀도가 답하지 못하는 것</b>",
        ]
    )
    lines.extend(f"  • {caveat}" for caveat in SHADOW_CAVEATS)
    return "\n".join(lines)


async def render_prefilter_status(bot_data) -> str | None:
    """`/system prefilter` 진입점. 기능이 꺼져 있으면 None."""
    prefilter = bot_data.get("news_prefilter")
    if prefilter is None:
        return None
    return format_prefilter_report(await prefilter.report())
