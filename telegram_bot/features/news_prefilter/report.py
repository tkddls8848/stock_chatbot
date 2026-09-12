"""`/system prefilter` 화면 문자열.

사전선별이 자기 보고서를 직접 그린다. 예전에는 system_admin이 `report()`의
dict 모양과 `SHADOW_CAVEATS`를 알고 그렸는데, 그러면 사전선별의 관측 항목을
바꿀 때마다 남의 기능 파일을 함께 고쳐야 했다.
"""

from telegram_bot.features.news_prefilter.service import SHADOW_CAVEATS


# 판단 기준은 code_guide.md의 "사전선별의 거취" 항목에 있다. 여기 숫자를 바꿀
# 때는 그 문서도 함께 고친다 — 기준이 두 벌이 되면 읽는 사람이 어느 쪽을 믿을지
# 모른다.
DECISION_DEADLINE = "2026-10-15"
MIN_LABELS_TO_DECIDE = 500
DROP_DISAGREEMENT = 0.10
DROP_AUC = 0.55
PROMOTE_DISAGREEMENT = 0.20
PROMOTE_AUC = 0.65
PROMOTE_AP_LIFT = 1.5


def _verdict_lines(report: dict) -> list[str]:
    """지금 지표로 올릴지 지울지 판단할 수 있는지, 있다면 어느 쪽인지.

    화면이 판정까지 내놓지 않으면 매번 기준 문서를 열어 손으로 대조하게 되고,
    그러다 "좀 더 보자"로 미뤄진다. 이 기능이 반년 가까이 shadow에 머문 경위가
    그것이다.
    """
    labeled = report["labeled"]
    decided = report["agree"] + report["latest_only"] + report["prefilter_only"]
    disagreement = (
        (report["latest_only"] + report["prefilter_only"]) / decided if decided else None
    )
    auc = report["auc"]
    ap, base = report["model_validation_ap"], report["model_prevalence"]

    lines = ["", f"<b>거취 판단</b> (기한 {DECISION_DEADLINE}, 기본값 삭제)"]
    if disagreement is not None:
        lines.append(f"  불일치율 {disagreement:.0%}")
    if labeled < MIN_LABELS_TO_DECIDE:
        lines.append(
            f"  ⏳ 라벨 {labeled}/{MIN_LABELS_TO_DECIDE}건 — 아직 판단하지 않습니다."
        )
        return lines

    drop = (disagreement is not None and disagreement < DROP_DISAGREEMENT) or (
        auc is not None and auc < DROP_AUC
    )
    promote = (
        disagreement is not None
        and disagreement >= PROMOTE_DISAGREEMENT
        and auc is not None
        and auc >= PROMOTE_AUC
        and ap is not None
        and base
        and float(ap) >= float(base) * PROMOTE_AP_LIFT
    )
    if drop:
        lines.append("  ⛔ <b>삭제 기준에 해당합니다.</b> 기능을 지웁니다.")
    elif promote:
        lines.append("  ✅ <b>승격 기준을 모두 만족합니다.</b> active로 올립니다.")
    else:
        lines.append("  ◐ 어느 쪽도 아닙니다. 2주 연장은 한 번만 허용합니다.")
    return lines


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

    lines.extend(_verdict_lines(report))
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
    elif not report["labeled"]:
        # 라벨 0건과 '아직 덜 모임'은 원인이 다르다. 0건은 공급이 끊긴
        # 것이고, 그건 기다려서 해결되지 않는다. 실제로 13일 동안 그 상태로
        # 돌았는데 화면이 둘을 구분하지 않아 아무도 몰랐다.
        lines.append(
            "  ⛔ 라벨이 0건입니다. 학습이 시작될 수 없습니다 — "
            "3시간 보고서가 highlights를 만들고 있는지, 그 결과가 "
            "record_outcome으로 돌아오는지 확인하십시오(news/report.py)."
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
