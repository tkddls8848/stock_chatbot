"""확률 정규화(`dashboard/models.py`)가 무엇을 읽고 무엇을 버리는지 고정한다.

2026-09-25 실측에서 열린 event 19,054건 중 13,522건(71%)이 `unavailable`이었다.
원인은 가격이 없어서가 아니라 **읽는 쪽이 좁아서**였다.

1. 결과가 둘인 시장을 라벨이 `Yes`/`No`일 때만 읽었다. `Up`/`Down`(5분 가격),
   팀 이름, `Over`/`Under`는 가격이 붙어 있어도 통째로 버려졌다.
2. 다지선다는 자식이 **전부** 유효할 때만 읽었다. 자식 하나가 닫혔거나 아직
   가격이 없으면 나머지 자식의 가격을 버리고 event를 unavailable로 떨어뜨렸다.

이 파일은 그 두 경우를 원문 모양 그대로 재현하고, 동시에 **정말로 읽을 수 없는
경우는 여전히 unavailable이어야 한다**는 것도 함께 고정한다.
"""

import json

import pytest

from services.web.polymarket.dashboard.models import (
    normalize_event,
    title_probability,
)

LOW_LIQUIDITY = 1000.0


def _market(
    *,
    identity: str,
    question: str,
    outcomes: list[str] | str | None,
    prices: list[str] | str | None,
    group_item_title: str = "",
    closed: bool = False,
    active: bool = True,
    liquidity: float | None = 5000.0,
) -> dict[str, object]:
    """Gamma가 주는 market 한 건. `outcomes`·`outcomePrices`는 문자열 JSON이다."""
    market: dict[str, object] = {
        "id": identity,
        "question": question,
        "groupItemTitle": group_item_title,
        "slug": identity,
        "active": active,
        "closed": closed,
        "liquidityNum": liquidity,
        "volume24hr": 100.0,
    }
    if outcomes is not None:
        market["outcomes"] = outcomes if isinstance(outcomes, str) else json.dumps(outcomes)
    if prices is not None:
        market["outcomePrices"] = prices if isinstance(prices, str) else json.dumps(prices)
    return market


def _event(
    *,
    identity: str,
    title: str,
    markets: list[dict[str, object]],
    neg_risk: bool | None = None,
    liquidity: float | None = 5000.0,
) -> dict[str, object]:
    event: dict[str, object] = {
        "id": identity,
        "title": title,
        "slug": identity,
        "active": True,
        "closed": False,
        "liquidity": liquidity,
        "volume24hr": 1000.0,
        "endDate": "2026-12-31T00:00:00Z",
        "markets": markets,
    }
    if neg_risk is not None:
        event["negRisk"] = neg_risk
    return event


def _normalize(event: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    return normalize_event(event, identity=str(event["id"]), low_liquidity=LOW_LIQUIDITY)


# --- 1. 두 결과 시장은 라벨이 Yes/No가 아니어도 읽는다 ----------------------


def test_up_or_down_binary_is_read_with_its_own_labels():
    """실측 예: 'Bitcoin Up or Down'이 `non_binary_outcomes`로 버려지고 있었다.

    5분짜리 가격 방향 시장 하나가 참여 잔액 16,388인데도 확률을 읽지 못했다.
    같은 모양이 2026-09-25 current.json에 3,616건 있었다.
    """
    event = _event(
        identity="109965",
        title="Bitcoin Up or Down - September 25, 3:00AM-3:05AM ET",
        liquidity=16388.0,
        markets=[
            _market(
                identity="m-updown",
                question="Bitcoin Up or Down - September 25, 3:00AM-3:05AM ET",
                outcomes=["Up", "Down"],
                prices=["0.52", "0.48"],
                liquidity=16388.0,
            )
        ],
    )

    compact, detail = _normalize(event)

    assert compact["event_type"] == "binary"
    assert compact["price_status"] == "ok"
    assert compact["data_status"] == "ok"
    assert compact["leader"] == "Up"
    assert compact["leader_probability"] == pytest.approx(0.52)
    market = detail["markets"][0]
    assert market["price_valid"] is True
    assert market["price_warning"] is None
    assert market["yes_label"] == "Up"
    assert market["no_label"] == "Down"
    assert market["yes_probability"] == pytest.approx(0.52)
    assert market["no_probability"] == pytest.approx(0.48)


def test_the_trailing_label_leads_when_it_is_the_one_ahead():
    event = _event(
        identity="e-down",
        title="Ethereum Up or Down - September 25, 4:00AM-4:05AM ET",
        markets=[
            _market(
                identity="m-down",
                question="Ethereum Up or Down",
                outcomes=["Up", "Down"],
                prices=["0.30", "0.70"],
            )
        ],
    )

    compact, _ = _normalize(event)

    assert compact["leader"] == "Down"
    assert compact["leader_probability"] == pytest.approx(0.70)
    assert compact["leader_margin"] == pytest.approx(0.40)


def test_team_names_are_read_the_same_way():
    """실측 예: 단일 market 스포츠 event 544건이 팀 이름 때문에 버려졌다."""
    event = _event(
        identity="e-kbo",
        title="KBO: NC Dinos vs. Doosan Bears",
        markets=[
            _market(
                identity="m-kbo",
                question="KBO: NC Dinos vs. Doosan Bears",
                outcomes=["NC Dinos", "Doosan Bears"],
                prices=["0.41", "0.59"],
            )
        ],
    )

    compact, _ = _normalize(event)

    assert compact["price_status"] == "ok"
    assert compact["leader"] == "Doosan Bears"


def test_yes_no_markets_keep_the_old_labels_and_the_old_mapping():
    """예/아니오 계약은 그대로다 — shorts·트렌드가 HTTP로 이 뜻을 읽는다."""
    event = _event(
        identity="e-yes",
        title="Will the Fed cut rates in October?",
        markets=[
            _market(
                identity="m-yes",
                question="Will the Fed cut rates in October?",
                outcomes=["Yes", "No"],
                prices=["0.34", "0.66"],
            )
        ],
    )

    compact, detail = _normalize(event)

    assert compact["leader"] == "No"
    assert compact["leader_probability"] == pytest.approx(0.66)
    market = detail["markets"][0]
    assert market["yes_label"] == "Yes" and market["no_label"] == "No"
    assert market["yes_probability"] == pytest.approx(0.34)


def test_reversed_yes_no_order_is_matched_by_name_not_by_position():
    """`["No", "Yes"]`로 와도 `yes_probability`는 Yes 쪽이다."""
    event = _event(
        identity="e-rev",
        title="Will it rain?",
        markets=[
            _market(
                identity="m-rev",
                question="Will it rain?",
                outcomes=["No", "Yes"],
                prices=["0.75", "0.25"],
            )
        ],
    )

    compact, detail = _normalize(event)

    assert detail["markets"][0]["yes_probability"] == pytest.approx(0.25)
    assert detail["markets"][0]["no_probability"] == pytest.approx(0.75)
    assert compact["leader"] == "No"


def test_two_outcomes_without_usable_names_are_still_refused():
    """이름이 없거나 둘이 같으면 어느 확률이 어느 쪽인지 적을 수 없다."""
    event = _event(
        identity="e-blank",
        title="이름 없는 두 결과",
        markets=[
            _market(
                identity="m-blank",
                question="이름 없는 두 결과",
                outcomes=["", "  "],
                prices=["0.5", "0.5"],
            )
        ],
    )

    compact, detail = _normalize(event)

    assert compact["price_status"] == "unavailable"
    assert detail["markets"][0]["price_warning"] == "unusable_outcome_labels"


def test_more_than_two_outcomes_is_still_missing_binary_prices():
    event = _event(
        identity="e-three",
        title="세 결과",
        markets=[
            _market(
                identity="m-three",
                question="세 결과",
                outcomes=["A", "B", "C"],
                prices=["0.3", "0.3", "0.4"],
            )
        ],
    )

    compact, detail = _normalize(event)

    assert compact["price_status"] == "unavailable"
    assert detail["markets"][0]["price_warning"] == "missing_binary_prices"


def test_prices_outside_the_range_and_bad_sums_are_still_refused():
    out_of_range, _ = _normalize(
        _event(
            identity="e-range",
            title="범위 밖",
            markets=[
                _market(
                    identity="m-range",
                    question="범위 밖",
                    outcomes=["Up", "Down"],
                    prices=["1.4", "-0.4"],
                )
            ],
        )
    )
    bad_sum, detail = _normalize(
        _event(
            identity="e-sum",
            title="합이 틀림",
            markets=[
                _market(
                    identity="m-sum",
                    question="합이 틀림",
                    outcomes=["Up", "Down"],
                    prices=["0.2", "0.2"],
                )
            ],
        )
    )

    assert out_of_range["price_status"] == "unavailable"
    assert bad_sum["price_status"] == "unavailable"
    assert detail["markets"][0]["price_warning"] == "price_sum_invalid"


# --- 2. 다지선다는 닫힌 자식·가격 없는 자식을 빼고 계산한다 ------------------


def test_exclusive_multi_drops_the_unpriced_child_instead_of_the_whole_event():
    """실측 예: 'Iowa Senate Election Margin of Victory'.

    첫 자식은 `price_valid=True`(yes 0.0255)인데 아직 가격이 붙지 않은 자식
    하나 때문에 event 전체가 unavailable이었다.
    """
    event = _event(
        identity="e-iowa",
        title="Iowa Senate Election Margin of Victory",
        neg_risk=True,
        markets=[
            _market(
                identity="m-r20",
                question="Will Republicans win Iowa Senate by 20+?",
                group_item_title="Republicans by 20+",
                outcomes=["Yes", "No"],
                prices=["0.0255", "0.9745"],
            ),
            _market(
                identity="m-r10",
                question="Will Republicans win Iowa Senate by 10-20?",
                group_item_title="Republicans by 10-20",
                outcomes=["Yes", "No"],
                prices=["0.62", "0.38"],
            ),
            _market(
                identity="m-d10",
                question="Will Democrats win Iowa Senate by 0-10?",
                group_item_title="Democrats by 0-10",
                outcomes=["Yes", "No"],
                prices=["0.35", "0.65"],
            ),
            _market(
                identity="m-nopr",
                question="Will the Iowa Senate race be tied?",
                group_item_title="Exact tie",
                outcomes=None,
                prices=None,
            ),
        ],
    )

    compact, detail = _normalize(event)

    assert compact["event_type"] == "exclusive_multi"
    assert compact["price_status"] == "ok"
    assert compact["leader"] == "Republicans by 10-20"
    assert compact["leader_probability"] == pytest.approx(0.62 / 0.9955, rel=1e-6)
    assert detail["raw_yes_sum"] == pytest.approx(0.9955)
    assert detail["warnings"] == ["partial_child_prices"]


def test_exclusive_multi_excludes_closed_children_from_the_composition():
    """닫힌 자식의 가격은 예측이 아니라 결과(0·1)다. 섞으면 구성비가 망가진다."""
    event = _event(
        identity="e-closed",
        title="Who wins the primary?",
        neg_risk=True,
        markets=[
            _market(
                identity="m-a",
                question="Will A win?",
                group_item_title="A",
                outcomes=["Yes", "No"],
                prices=["0.7", "0.3"],
            ),
            _market(
                identity="m-b",
                question="Will B win?",
                group_item_title="B",
                outcomes=["Yes", "No"],
                prices=["0.3", "0.7"],
            ),
            _market(
                identity="m-dropped",
                question="Will C win?",
                group_item_title="C",
                outcomes=["Yes", "No"],
                prices=["0.0", "1.0"],
                closed=True,
            ),
        ],
    )

    compact, detail = _normalize(event)

    assert compact["price_status"] == "ok"
    assert compact["leader"] == "A"
    assert detail["raw_yes_sum"] == pytest.approx(1.0)
    assert detail["warnings"] == ["closed_children_excluded"]


def test_exclusive_multi_keeps_the_sum_warning_when_the_rest_does_not_add_up():
    """남은 구성비가 1에서 크게 벗어나면 100% 구성비를 만들지 않는 정책은 그대로."""
    event = _event(
        identity="e-sumoff",
        title="Who wins?",
        neg_risk=True,
        markets=[
            _market(
                identity="m-a",
                question="Will A win?",
                group_item_title="A",
                outcomes=["Yes", "No"],
                prices=["0.2", "0.8"],
            ),
            _market(
                identity="m-b",
                question="Will B win?",
                group_item_title="B",
                outcomes=["Yes", "No"],
                prices=["0.2", "0.8"],
            ),
            _market(
                identity="m-missing",
                question="Will C win?",
                group_item_title="C",
                outcomes=None,
                prices=None,
            ),
        ],
    )

    compact, detail = _normalize(event)

    assert compact["price_status"] == "unavailable"
    assert compact["data_status"] == "unavailable"
    assert detail["raw_yes_sum"] == pytest.approx(0.4)
    assert detail["warnings"] == ["partial_child_prices", "exclusive_price_sum_invalid"]


def test_independent_multi_reads_children_with_their_own_outcome_names():
    """실측 예: 'A vs. B' 스포츠 묶음 3,500건이 자식 라벨 때문에 통째로 버려졌다."""
    event = _event(
        identity="e-match",
        title="Tatarnykov Volodymyr vs. Hubenko Maksym",
        neg_risk=False,
        markets=[
            _market(
                identity="m-win",
                question="Match winner",
                group_item_title="Match winner",
                outcomes=["Tatarnykov Volodymyr", "Hubenko Maksym"],
                prices=["0.62", "0.38"],
            ),
            _market(
                identity="m-total",
                question="Total games over/under 21.5",
                group_item_title="Over/Under 21.5",
                outcomes=["Over", "Under"],
                prices=["0.47", "0.53"],
            ),
        ],
    )

    compact, detail = _normalize(event)

    assert compact["event_type"] == "independent_multi"
    assert compact["price_status"] == "ok"
    # 독립 묶음은 event 하나의 1위를 합성하지 않는다(계획서 3-4).
    assert compact["leader"] is None
    assert detail["warnings"] == []


def test_independent_multi_notes_the_children_it_left_out():
    event = _event(
        identity="e-partial",
        title="Some bundle",
        neg_risk=False,
        markets=[
            _market(
                identity="m-ok",
                question="Q1",
                outcomes=["Yes", "No"],
                prices=["0.4", "0.6"],
            ),
            _market(
                identity="m-closed",
                question="Q2",
                outcomes=["Yes", "No"],
                prices=["1.0", "0.0"],
                closed=True,
            ),
            _market(identity="m-none", question="Q3", outcomes=None, prices=None),
        ],
    )

    compact, detail = _normalize(event)

    assert compact["price_status"] == "ok"
    assert detail["warnings"] == ["closed_children_excluded", "partial_child_prices"]


# --- 3. 정말 읽을 수 없으면 여전히 unavailable ------------------------------


def test_a_multi_event_with_no_priced_active_child_stays_unavailable():
    event = _event(
        identity="e-empty",
        title="가격이 하나도 없는 묶음",
        neg_risk=False,
        markets=[
            _market(identity="m-1", question="Q1", outcomes=None, prices=None),
            _market(identity="m-2", question="Q2", outcomes=["Yes", "No"], prices=None),
        ],
    )

    compact, detail = _normalize(event)

    assert compact["price_status"] == "unavailable"
    assert compact["data_status"] == "unavailable"
    assert detail["warnings"] == ["partial_child_prices", "missing_child_price"]


def test_a_multi_event_whose_children_are_all_closed_stays_unavailable():
    event = _event(
        identity="e-allclosed",
        title="자식이 전부 닫힌 묶음",
        neg_risk=True,
        markets=[
            _market(
                identity="m-1",
                question="Q1",
                outcomes=["Yes", "No"],
                prices=["1.0", "0.0"],
                closed=True,
            ),
            _market(
                identity="m-2",
                question="Q2",
                outcomes=["Yes", "No"],
                prices=["0.0", "1.0"],
                closed=True,
            ),
        ],
    )

    compact, _ = _normalize(event)

    assert compact["price_status"] == "unavailable"


def test_unknown_multi_is_still_refused():
    event = _event(
        identity="e-unknown",
        title="negRisk가 없는 묶음",
        markets=[
            _market(identity="m-1", question="Q1", outcomes=["Yes", "No"], prices=["0.4", "0.6"]),
            _market(identity="m-2", question="Q2", outcomes=["Yes", "No"], prices=["0.6", "0.4"]),
        ],
    )

    compact, detail = _normalize(event)

    assert compact["event_type"] == "unknown_multi"
    assert compact["price_status"] == "unavailable"
    assert detail["warnings"] == ["unknown_multi_type"]


# --- 4. 제목 기준 확률 계약 -------------------------------------------------


def test_title_probability_still_flips_the_no_side():
    assert title_probability(
        {"event_type": "binary", "leader": "Yes", "leader_probability": 0.82}
    ) == pytest.approx(0.82)
    assert title_probability(
        {"event_type": "binary", "leader": "No", "leader_probability": 0.82}
    ) == pytest.approx(0.18)


def test_a_named_two_way_binary_has_no_title_probability():
    """'Bitcoin Up or Down'의 제목은 참·거짓 명제가 아니다.

    여기서 `leader_probability`를 그냥 돌려주면 1위가 `Up`에서 `Down`으로
    넘어간 주기에 트렌드의 뺄셈이 0에 가깝게 나온다 — 실제로는 반대편으로
    넘어간 순간이다. 호출자는 `leader`와 `leader_probability`를 함께 읽는다.
    """
    assert (
        title_probability(
            {"event_type": "binary", "leader": "Up", "leader_probability": 0.61}
        )
        is None
    )
    assert (
        title_probability(
            {"event_type": "binary", "leader": "Doosan Bears", "leader_probability": 0.59}
        )
        is None
    )


def test_multi_event_leaders_pass_through_unchanged():
    assert title_probability(
        {"event_type": "exclusive_multi", "leader": "Candidate A", "leader_probability": 0.55}
    ) == pytest.approx(0.55)


# --- 5. 판독률과 compact 크기 -----------------------------------------------


def _measured_population() -> list[tuple[str, dict[str, object]]]:
    """2026-09-25 current.json에서 센 `unavailable` 13,522건의 구성을 축소 재현한다.

    binary 4,206건(Up or Down 3,616 · 'A vs B' 544 · 나머지 46),
    independent_multi 3,507건(전부 'A vs B'), exclusive_multi 5,796건
    (자식이 빠져 통째로 버려진 군)이 그 구성이다.
    """
    return [
        (
            "updown",
            _event(
                identity="p-updown",
                title="Bitcoin Up or Down - September 25, 3:00AM-3:05AM ET",
                markets=[
                    _market(
                        identity="p-updown-m",
                        question="Bitcoin Up or Down",
                        outcomes=["Up", "Down"],
                        prices=["0.52", "0.48"],
                    )
                ],
            ),
        ),
        (
            "binary_teams",
            _event(
                identity="p-teams",
                title="KBO: KT Wiz vs. Kia Tigers",
                markets=[
                    _market(
                        identity="p-teams-m",
                        question="KBO: KT Wiz vs. Kia Tigers",
                        outcomes=["KT Wiz", "Kia Tigers"],
                        prices=["0.55", "0.45"],
                    )
                ],
            ),
        ),
        (
            "binary_yes_no",
            _event(
                identity="p-yesno",
                title="Will the Fed cut rates in October?",
                markets=[
                    _market(
                        identity="p-yesno-m",
                        question="Will the Fed cut rates in October?",
                        outcomes=["Yes", "No"],
                        prices=["0.34", "0.66"],
                    )
                ],
            ),
        ),
        (
            "exclusive_partial",
            _event(
                identity="p-excl",
                title="CA-22 House Election Winner",
                neg_risk=True,
                markets=[
                    _market(
                        identity="p-excl-a",
                        question="Will A win CA-22?",
                        group_item_title="A",
                        outcomes=["Yes", "No"],
                        prices=["0.66", "0.34"],
                    ),
                    _market(
                        identity="p-excl-b",
                        question="Will B win CA-22?",
                        group_item_title="B",
                        outcomes=["Yes", "No"],
                        prices=["0.33", "0.67"],
                    ),
                    _market(
                        identity="p-excl-c",
                        question="Will C win CA-22?",
                        group_item_title="C",
                        outcomes=None,
                        prices=None,
                    ),
                ],
            ),
        ),
        (
            "independent_teams",
            _event(
                identity="p-indep",
                title="Hanshin Tigers vs. Hiroshima Carp",
                neg_risk=False,
                markets=[
                    _market(
                        identity="p-indep-m1",
                        question="Match winner",
                        outcomes=["Hanshin Tigers", "Hiroshima Carp"],
                        prices=["0.52", "0.48"],
                    ),
                    _market(
                        identity="p-indep-m2",
                        question="Total runs over/under 7.5",
                        outcomes=["Over", "Under"],
                        prices=["0.5", "0.5"],
                    ),
                ],
            ),
        ),
        (
            "no_price_at_all",
            _event(
                identity="p-nopr",
                title="가격이 하나도 없는 묶음",
                neg_risk=False,
                markets=[
                    _market(identity="p-nopr-m", question="Q1", outcomes=None, prices=None)
                ],
            ),
        ),
        (
            "sum_invalid",
            _event(
                identity="p-sum",
                title="구성비가 성립하지 않는 다지선다",
                neg_risk=True,
                markets=[
                    _market(
                        identity="p-sum-a",
                        question="Will A win?",
                        group_item_title="A",
                        outcomes=["Yes", "No"],
                        prices=["0.2", "0.8"],
                    ),
                    _market(
                        identity="p-sum-b",
                        question="Will B win?",
                        group_item_title="B",
                        outcomes=["Yes", "No"],
                        prices=["0.15", "0.85"],
                    ),
                ],
            ),
        ),
    ]


def test_the_measured_shapes_are_read_and_the_unreadable_ones_are_not():
    """축소 모집단의 판독률. 예전 규칙이면 7건 중 1건(14%)만 읽혔다."""
    population = _measured_population()
    read = {
        key
        for key, event in population
        if _normalize(event)[0]["price_status"] == "ok"
    }

    assert read == {
        "updown",
        "binary_teams",
        "binary_yes_no",
        "exclusive_partial",
        "independent_teams",
    }
    assert len(read) / len(population) == pytest.approx(5 / 7)


def test_compact_gains_no_new_field():
    """compact 한 필드는 event 수(실측 19,054)만큼 곱해져 16 MiB 상한을 민다.

    이 변경은 compact에 필드를 더하지 않는다 — 두 라벨은 detail의 `markets`
    안에만 있다. 늘어나는 것은 지금까지 `null`이던 `leader` 세 값이 채워지는
    분량뿐이고, 2026-09-25 generation 전부에 채워 재면 11.61 MiB → 11.80 MiB다.
    """
    compact, detail = _normalize(
        _event(
            identity="e-keys",
            title="키 목록",
            markets=[
                _market(
                    identity="m-keys",
                    question="Q",
                    outcomes=["Up", "Down"],
                    prices=["0.5", "0.5"],
                )
            ],
        )
    )

    assert set(compact) == {
        "id",
        "title",
        "category",
        "category_label",
        "tags",
        "regions",
        "event_type",
        "data_status",
        "price_status",
        "liquidity_status",
        "liquidity",
        "volume24hr",
        "end_date",
        "leader",
        "leader_probability",
        "leader_margin",
    }
    assert "yes_label" not in compact and "markets" not in compact
    assert {"yes_label", "no_label"} <= set(detail["markets"][0])
