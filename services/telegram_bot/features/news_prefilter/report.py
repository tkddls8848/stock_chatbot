"""`/system prefilter` 화면 문자열.

사전선별이 자기 보고서를 직접 그린다. 예전에는 system_admin이 `report()`의
dict 모양과 `SHADOW_CAVEATS`를 알고 그렸는데, 그러면 사전선별의 관측 항목을
바꿀 때마다 남의 기능 파일을 함께 고쳐야 했다.
"""

from services.telegram_bot.features.news_prefilter.service import SHADOW_CAVEATS


# active는 운영자가 요청한 실험이다. 검증 성과를 보장하는 자동 승격이 아니다.
DECISION_DEADLINE = "2026-10-15"


def _verdict_lines(report: dict) -> list[str]:
    labeled = report["labeled"]
    negatives = labeled - report["positives"]
    decided = report["agree"] + report["latest_only"] + report["prefilter_only"]
    lines = ["", f"<b>효과 검증</b> (재평가 {DECISION_DEADLINE})"]
    if decided:
        disagreement = (report["latest_only"] + report["prefilter_only"]) / decided
        lines.append(f"  불일치율 {disagreement:.0%} — 선택 변화이며 품질 향상률은 아님")
    days = report.get("observation_days", 0)
    if labeled < 500 or negatives < 50 or days < 7:
        lines.append(f"  ⏳ 라벨 {labeled}/500 · 음성 {negatives}/50 · 관측 {days}/7일 — 아직 판단하지 않습니다.")
    elif report["auc"] is not None and report["auc"] < 0.55:
        lines.append("  점수 판별력이 낮습니다. 중요도순 운영을 재검토해야 합니다.")
    else:
        lines.append("  탐색 후보의 평가·반복 차단 성과를 함께 검토합니다. 자동 승격·삭제하지 않습니다.")
    ap, base = report["model_validation_ap"], report["model_prevalence"]
    if ap is not None and base is not None and float(base) < 1:
        gain = (float(ap) - float(base)) / (1 - float(base))
        lines.append(f"  검증 AP 개선 여지 중 {gain:.0%} 달성 (선택 편향이 남은 모델 선택용 검증)")
    return lines


def format_prefilter_report(report: dict) -> str:
    """두 정책의 불일치와 판별력, CPU 사용량을 한 화면에 그린다."""
    mode = report["mode"]
    cpu = report["cpu"]
    used_hours = float(cpu["used_seconds"]) / 3600
    lines = [
        "<b>뉴스 사전선별 (로컬 사건 메모리)</b>",
        f"모드: <b>{mode}</b>"
        + ("  — 반복 차단 적용, 보고서 후보는 최신순" if mode == "shadow"
           else "  — 중요도순 선별 + 무작위 탐색을 보고서에 적용"),
        "",
        f"소스당 {report.get('selection_limit', 12)}건 · 탐색 {report.get('exploration_slots', 2)}건 (active)",
        f"<b>수집</b> (최근 {report.get('retention_days', 7)}일)",
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

    gated = report.get("gated", {})
    lines.append(f"  반복 차단: 기보고 {gated.get('gated_translated_event', 0):,} · "
                 f"큐 대기 {gated.get('gated_queued_event', 0):,} · 소스 간 {gated.get('gated_cycle_duplicate', 0):,} · 소스 내 {gated.get('gated_source_duplicate', 0):,}건")
    active = report.get("active_labels", {})
    lines.extend([
        "", "<b>active 후보의 후속 평가</b>",
        f"  중요도순 평가 {active.get('ranked', 0)}건 · 탐색 평가 {active.get('exploration', 0)}건",
        f"  최신순 밖에서 발견 {active.get('discovered', 0)}건 중 중요도 중·상 {active.get('discovered_positive', 0)}건",
        "  미평가는 음성이 아닙니다. 위 수치는 평가된 후보 안의 결과입니다.",
    ])
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
            "시장상황 보고서가 highlights를 만들고 있는지, 그 결과가 "
            "record_outcome으로 돌아오는지 확인하십시오(news/report.py)."
        )
    else:
        lines.append("  ⏸ 아직 학습된 모델이 없습니다(라벨 120건·3일, 학습/검증 양쪽 라벨이 필요).")

    foreground_hours = float(cpu.get("foreground_seconds", 0.0)) / 3600
    lines.extend(
        [
            "",
            "<b>CPU 사용</b>",
            f"  오늘 학습 {used_hours:.2f}h (UTC {cpu['utc_day']}) · 일일 상한 없음",
            f"  참고: 봇 foreground 전체 {foreground_hours:.2f}h",
            "",
            "<b>섀도가 답하지 못하는 것</b>",
        ]
    )
    maintenance = report.get("maintenance", {})
    reasons = {
        "insufficient_labels": "학습 라벨 120건·3일 대기",
        "invalid_time_split": "시간 분할 후 양성·음성 부족 (학습 각 10건, 검증 각 5건)",
        "search_complete": "현재 자료 32회 검증 완료, 새 라벨 대기",
        "slice_complete": "이번 CPU 조각 완료, 다음 주기에 이어 학습",
        "burst": "리서치·보고서 우선 작업에 양보", "urgent": "긴급 작업에 양보",
        "load": "호스트 부하로 양보",
        "no_work": "수행할 작업 없음",
    }
    if maintenance:
        reason = reasons.get(maintenance["reason"], maintenance["reason"])
        # 섀도 한계 앞에 CPU 진행 상황을 넣는다.
        lines[-2:-2] = [
            f"  최근 보정: {reason}",
            f"  {maintenance['cpu_seconds']:.2f}s · 이번 완료 {maintenance['trials']}회 · "
            f"자료당 {maintenance.get('search_trials', 0)}/32회 · {maintenance['at']}",
        ]
    else:
        lines[-2:-2] = ["  보정 상태: 기동 후 첫 예약 실행 대기"]
    lines.extend(f"  • {caveat}" for caveat in SHADOW_CAVEATS)
    return "\n".join(lines)


async def render_prefilter_status(bot_data) -> str | None:
    """`/system prefilter` 진입점. 기능이 꺼져 있으면 None."""
    prefilter = bot_data.get("news_prefilter")
    if prefilter is None:
        return None
    return format_prefilter_report(await prefilter.report())
