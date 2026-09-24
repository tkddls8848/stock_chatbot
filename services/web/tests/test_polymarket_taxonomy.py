"""raw tag → 대표 분야. 둘 이상 분야에 걸리면 미분류가 아니라 `composite`다."""

from services.web.polymarket.dashboard.taxonomy import CATEGORY_TAGS, classify


def _tags(*slugs):
    return [{"slug": slug} for slug in slugs]


def test_single_category_keeps_its_field():
    econ = sorted(CATEGORY_TAGS["economy_finance"])[0]
    result = classify(_tags(econ))
    assert result["category"] == "economy_finance"
    assert result["category_reason"] == "tag:" + econ


def test_multiple_categories_are_composite_not_other():
    econ = sorted(CATEGORY_TAGS["economy_finance"])[0]
    geo = sorted(CATEGORY_TAGS["geopolitics"])[0]
    result = classify(_tags(econ, geo))
    assert result["category"] == "composite"
    assert result["category_label"] == "복합"
    assert result["category_reason"] == "composite:economy_finance,geopolitics"


def test_unmapped_stays_other():
    result = classify(_tags("no-such-tag"))
    assert result["category"] == "other"
    assert result["category_reason"] == "unmapped"
