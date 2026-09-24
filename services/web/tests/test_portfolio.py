"""개인 화면(`/portfolio`): 잠금·REST 자원·공유 관심종목·규칙 진단·조언·외부 자료 파서.

외부 API와 LLM은 전부 가짜로 바꾼다. 지키는 규칙은 `code_guide.md`의 「개인 화면」이다.
"""

import json
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from services.web import server
from services.web.core.clock import JST
from services.web.portfolio import advisor as advisor_module
from services.web.portfolio import market_data, service as service_module, store as store_module
from services.web.portfolio.advisor import AdviceError, PortfolioAdvisor, validate
from services.web.portfolio.auth import LoginThrottle
from services.web.portfolio.diagnosis import diagnose
from services.web.portfolio.service import AdviceService
from services.web.portfolio.store import AdviceStore, AssetStore, WatchlistStore, canonical_code

PASSWORD = "correct horse"
NOW = datetime(2026, 9, 24, 10, 0, tzinfo=JST)
GOOD_ADVICE = (
    "총자산은 3억원이고 부동산 비중이 66.7%로 한쪽에 치우쳐 있습니다. 예적금은 만기가 가까운 상품이 있어 "
    "재예치 계획을 세워 두는 것이 좋겠습니다. 자산군을 나눠 보면 주식과 예적금의 몫이 작아, 급할 때 쓸 수 있는 "
    "돈이 얇다는 점도 눈여겨볼 만합니다.\n\n가장 먼저 볼 것은 부동산 대출 비율입니다. 금리가 오르면 이자 부담이 "
    "커지므로 상환 계획과 여유 자금을 함께 점검해 볼 만합니다. 예금 금리는 은행권 최고 기본금리보다 낮은 편이라 "
    "만기 때 조건을 비교해 보는 것을 검토해 볼 만합니다.\n\n시장 금리 흐름은 채권과 예금 모두에 영향을 줍니다. "
    "외부 자료가 없는 항목은 판단하지 않았습니다. 결정은 본인의 사정에 맞춰 내리시기 바랍니다."
)


class FakeAdvisor:
    def __init__(self, text=GOOD_ADVICE, error=None):
        self.text, self.error, self.calls = text, error, []

    def advise(self, diagnosis, context):
        self.calls.append((diagnosis, context))
        if self.error:
            raise self.error
        return self.text


def _ok_rates():
    return {"status": "ok", "deposit": {"best_rate_pct": 3.6, "top": [], "count": 10},
            "saving": {"best_rate_pct": 4.1, "top": [], "count": 8}}


def _ok_market():
    return {"status": "ok", "base_rate": {"value_pct": 2.5, "as_of": "20260924"},
            "treasury_3y": {"value_pct": 2.7, "as_of": "20260923"}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    for module in (store_module, service_module):
        monkeypatch.setattr(module, "now", lambda: NOW)
    public = tmp_path / "public"
    public.mkdir()
    (public / "market.json").write_text(json.dumps(
        {"generated_at": "2026-09-24T07:40:00+09:00", "markets": {"KR": {"avg_sentiment": 0.12}}}), encoding="utf-8")
    assets = AssetStore(tmp_path / "portfolio" / "assets.json")
    advice_store = AdviceStore(tmp_path / "portfolio" / "advice")
    fake = FakeAdvisor()
    estimates = []
    advice = AdviceService(
        assets=assets, advice=advice_store, public_dir=public, max_daily=2, history_limit=5,
        advisor_factory=lambda: fake,
        fetch_deposit_rates=_ok_rates, fetch_market_rates=_ok_market,
        estimate_property=lambda *a, **k: estimates.append((a, k)) or {
            "status": "ok", "estimate_krw": 230_000_000, "basis": "region", "trade_count": 12},
    )
    router = server.build_portfolio_router(
        password=PASSWORD, assets=assets, advice_store=advice_store, advice=advice,
        watchlist=WatchlistStore(tmp_path / "portfolio" / "watchlist.json"),
        throttle=LoginThrottle(3, 600),
    )
    client = TestClient(server.build_app(portfolio_router=router))
    return {"client": client, "root": tmp_path / "portfolio", "fake": fake, "estimates": estimates,
            "advice": advice}


def _unlock(client):
    assert client.post("/api/portfolio/session", json={"password": PASSWORD}).status_code == 204


# ── 잠금 ────────────────────────────────────────────────────────────────────

def test_everything_is_closed_without_a_configured_password(tmp_path):
    router = server.build_portfolio_router(password="", assets=AssetStore(tmp_path / "a.json"),
                                           advice_store=AdviceStore(tmp_path / "adv"))
    client = TestClient(server.build_app(portfolio_router=router))
    assert client.get("/api/portfolio/session").json() == {"configured": False, "unlocked": False}
    assert client.post("/api/portfolio/session", json={"password": "x"}).status_code == 503
    assert client.get("/api/portfolio/assets").status_code == 503


def test_every_resource_is_locked_until_the_password_opens_it(env):
    client = env["client"]
    for method, path in (("get", "assets"), ("post", "assets"), ("get", "watchlist"), ("put", "watchlist"),
                         ("get", "advice"), ("post", "advice"), ("get", "advice/latest")):
        assert getattr(client, method)("/api/portfolio/" + path).status_code in (401, 422), path
        assert client.request(method.upper(), "/api/portfolio/" + path, json={}).status_code == 401, path
    assert client.post("/api/portfolio/session", json={"password": "wrong"}).status_code == 401
    _unlock(client)
    assert client.get("/api/portfolio/session").json()["unlocked"] is True
    assert client.get("/api/portfolio/assets").status_code == 200
    assert client.delete("/api/portfolio/session").status_code == 204
    client.cookies.clear()
    assert client.get("/api/portfolio/assets").status_code == 401


def test_repeated_wrong_passwords_are_throttled(env):
    client = env["client"]
    for _ in range(3):
        assert client.post("/api/portfolio/session", json={"password": "nope"}).status_code == 401
    assert client.post("/api/portfolio/session", json={"password": PASSWORD}).status_code == 429


def test_cookie_is_httponly_strict_and_secure_behind_https(env):
    client = env["client"]
    response = client.post("/api/portfolio/session", json={"password": PASSWORD},
                           headers={"x-forwarded-proto": "https"})
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "secure" in cookie
    assert PASSWORD not in response.headers["set-cookie"]


def test_private_pages_are_not_cached_or_indexed(env):
    client = env["client"]
    page = client.get("/portfolio")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert "noindex" in page.headers["x-robots-tag"]
    assert "Disallow: /portfolio" in client.get("/robots.txt").text


def test_routes_are_nouns_not_verbs():
    paths = {route.path for route in server.build_portfolio_router(password="x").routes}
    assert not any(verb in path for path in paths for verb in ("login", "logout", "advise", "run"))


# ── 자산 ────────────────────────────────────────────────────────────────────

def test_asset_crud_keeps_only_fields_of_its_kind(env):
    client = env["client"]
    _unlock(client)
    created = client.post("/api/portfolio/assets", json={
        "kind": "deposit", "name": "정기예금", "value_krw": 50_000_000, "rate_pct": 3.1,
        "maturity": "2026-10-10", "product": "deposit", "region_code": "11680"})
    assert created.status_code == 201
    row = created.json()
    assert "region_code" not in row and row["rate_pct"] == 3.1 and row["id"]
    changed = client.put(f"/api/portfolio/assets/{row['id']}", json={
        "kind": "deposit", "name": "정기예금", "value_krw": 51_000_000, "rate_pct": 3.1})
    assert changed.json()["value_krw"] == 51_000_000
    assert client.get("/api/portfolio/assets").json()["assets"][0]["value_krw"] == 51_000_000
    assert client.delete(f"/api/portfolio/assets/{row['id']}").status_code == 204
    assert client.delete(f"/api/portfolio/assets/{row['id']}").status_code == 404
    assert client.post("/api/portfolio/assets", json={"kind": "stock", "name": "x", "value_krw": 1,
                                                       "surprise": True}).status_code == 422
    assert client.post("/api/portfolio/assets", json={"kind": "crypto", "name": "x",
                                                       "value_krw": 1}).status_code == 422


def test_assets_never_reach_public_artifacts(env):
    client = env["client"]
    _unlock(client)
    client.post("/api/portfolio/assets", json={"kind": "stock", "name": "비밀종목", "value_krw": 1})
    client.cookies.clear()
    for path in ("/", "/search", "/forecast", "/research", "/about", "/api/meta", "/api/research"):
        assert "비밀종목" not in client.get(path).text, path
    assert client.get("/api/search", params={"q": "비밀종목"}).json()["total"] == 0


# ── 관심종목(봇과 공유) ────────────────────────────────────────────────────

@pytest.mark.parametrize(("market", "exchange", "raw", "code"), [
    ("CN", "SH", "600519", "600519"),
    ("HK", "HKEX", "700", "00700"),
    ("KR", "KOSPI", "5930", "KR:KOSPI:005930"),
    ("US", "NASDAQ", "nvda", "US:NASDAQ:NVDA"),
    ("US", "NASDAQ", "005930", None),
    ("KR", "NASDAQ", "5930", None),
])
def test_watchlist_codes_use_the_bots_canonical_form(market, exchange, raw, code):
    assert canonical_code(market, exchange, raw) == code


def test_watchlist_put_replaces_the_shared_file_in_the_bot_format(env):
    client = env["client"]
    _unlock(client)
    (env["root"]).mkdir(parents=True, exist_ok=True)
    (env["root"] / "watchlist.json").write_text(json.dumps({"00700": "텐센트"}), encoding="utf-8")
    assert client.get("/api/portfolio/watchlist").json()["items"] == {"00700": "텐센트"}
    response = client.put("/api/portfolio/watchlist", json={"items": [
        {"code": "00700", "name": "텐센트"},
        {"code": "5930", "name": "삼성전자", "market": "KR", "exchange": "KOSPI"}]})
    assert response.status_code == 200
    # 봇 `watchlist/manager.py`가 읽는 형식 그대로다: {정규코드: 이름}.
    assert json.loads((env["root"] / "watchlist.json").read_text(encoding="utf-8")) == {
        "00700": "텐센트", "KR:KOSPI:005930": "삼성전자"}
    assert not (env["root"] / "watchlist.json.lock").exists()
    assert client.put("/api/portfolio/watchlist", json={"items": [
        {"code": "garbage!", "name": "x"}]}).status_code == 422


def test_broken_shared_file_is_reported_not_swallowed(env):
    client = env["client"]
    _unlock(client)
    env["root"].mkdir(parents=True, exist_ok=True)
    (env["root"] / "watchlist.json").write_text("{broken", encoding="utf-8")
    response = client.get("/api/portfolio/watchlist")
    assert response.status_code == 500 and "읽을 수 없습니다" in response.json()["detail"]


# ── 규칙 진단 ──────────────────────────────────────────────────────────────

def _assets():
    return [
        {"id": "s", "kind": "stock", "name": "주식계좌", "value_krw": 50_000_000},
        {"id": "d", "kind": "deposit", "name": "정기예금", "value_krw": 50_000_000, "rate_pct": 2.8,
         "maturity": "2026-10-10", "product": "deposit"},
        {"id": "r", "kind": "real_estate", "name": "아파트", "value_krw": 200_000_000,
         "loan_krw": 130_000_000, "region_code": "11680", "area_m2": 84.9},
    ]


def test_diagnosis_computes_every_number_and_flags_rules():
    report = diagnose(_assets(), today=date(2026, 9, 24), deposit_rates=_ok_rates(),
                      market_rates=_ok_market(),
                      estimates={"r": {"status": "ok", "estimate_krw": 240_000_000, "basis": "region",
                                       "trade_count": 12}})
    assert report["total_krw"] == 300_000_000 and report["net_worth_krw"] == 170_000_000
    assert report["total_text"] == "3억원"
    assert report["by_class"]["real_estate"]["share_pct"] == 66.7
    codes = {f["code"] for f in report["findings"]}
    assert {"class_concentration", "single_concentration", "maturity", "rate_gap", "ltv",
            "estimate_gap"} <= codes
    assert report["rate_gaps"][0]["gap_pp"] == 0.8
    assert report["maturities"][0]["days_left"] == 16
    assert report["real_estate"][0]["ltv_pct"] == 65.0


def test_diagnosis_skips_what_external_data_could_not_supply():
    report = diagnose(_assets(), today=date(2026, 9, 24), deposit_rates={"status": "error"},
                      market_rates={"status": "missing_key"}, estimates={})
    codes = {f["code"] for f in report["findings"]}
    assert "rate_gap" not in codes and "estimate_gap" not in codes
    assert report["deposit_best"] == {} and "estimate_krw" not in report["real_estate"][0]


def test_empty_portfolio_is_not_diagnosed():
    report = diagnose([], today=date(2026, 9, 24), deposit_rates={}, market_rates={}, estimates={})
    assert report["findings"][0]["code"] == "empty"


# ── 조언 ────────────────────────────────────────────────────────────────────

def test_advice_is_created_saved_and_listed(env):
    client = env["client"]
    _unlock(client)
    for asset in _assets():
        body = {k: v for k, v in asset.items() if k != "id"}
        assert client.post("/api/portfolio/assets", json=body).status_code == 201
    response = client.post("/api/portfolio/advice")
    assert response.status_code == 201
    advice = response.json()
    assert advice["text"] == GOOD_ADVICE and advice["llm_status"] == "ok"
    assert advice["sources"] == {"deposit_rates": "ok", "market_rates": "ok", "real_estate": "ok"}
    assert advice["market_context"]["news_sentiment"] == {"KR": 0.12}
    assert env["estimates"][0][0] == ("11680", 84.9)
    listed = client.get("/api/portfolio/advice").json()
    assert listed["usage"] == {"today": 1, "max_daily": 2} and listed["items"][0]["id"] == advice["id"]
    assert client.get("/api/portfolio/advice/latest").json()["id"] == advice["id"]
    assert client.get(f"/api/portfolio/advice/{advice['id']}").status_code == 200
    assert client.get("/api/portfolio/advice/../../etc").status_code == 404
    # 봇 /web이 읽는 자리.
    assert json.loads((env["root"] / "advice" / "latest.json").read_text(encoding="utf-8"))["id"] == advice["id"]


def test_daily_cap_returns_429(env):
    client = env["client"]
    _unlock(client)
    client.post("/api/portfolio/assets", json={"kind": "stock", "name": "a", "value_krw": 1})
    # 같은 초에 두 번 만들어도 id가 겹치지 않는다(뒤에 무작위 네 자리).
    first = client.post("/api/portfolio/advice").json()["id"]
    second = client.post("/api/portfolio/advice").json()["id"]
    assert first != second
    assert client.post("/api/portfolio/advice").status_code == 429


def test_concurrent_request_returns_409(env):
    client = env["client"]
    _unlock(client)
    env["advice"]._running.acquire()
    try:
        assert client.post("/api/portfolio/advice").status_code == 409
    finally:
        env["advice"]._running.release()


def test_llm_failure_still_saves_the_diagnosis(env):
    client = env["client"]
    _unlock(client)
    env["fake"].error = AdviceError("boom")
    client.post("/api/portfolio/assets", json={"kind": "stock", "name": "a", "value_krw": 1})
    advice = client.post("/api/portfolio/advice").json()
    assert advice["text"] is None and advice["llm_status"].startswith("failed")
    assert advice["diagnosis"]["total_krw"] == 1


def test_advice_validation_rejects_invented_numbers_and_solicitation():
    source = json.dumps({"total": "3억원", "share_pct": 66.7, "gap_pp": 0.8})
    validate(GOOD_ADVICE.replace("66.7%", "66.7%"), source + " 3 66.7")
    with pytest.raises(AdviceError, match="입력에 없는 숫자"):
        validate(GOOD_ADVICE + " 연 7.5% 수익이 기대됩니다.", source + " 3 66.7")
    with pytest.raises(AdviceError, match="금지어"):
        validate(GOOD_ADVICE + " 지금 매수하세요.", source + " 3 66.7")
    with pytest.raises(AdviceError, match="금지어"):
        validate(GOOD_ADVICE + " 폴리마켓 참고.", source + " 3 66.7")


def test_advisor_retries_once_with_the_failure_reason():
    class Backend:
        def __init__(self):
            self.prompts = []

        def generate(self, **kwargs):
            self.prompts.append(json.loads(kwargs["user_prompt"]))
            return GOOD_ADVICE + " 12.34%" if len(self.prompts) == 1 else GOOD_ADVICE

    backend = Backend()
    report = {"total_text": "3억원", "by_class": {"real_estate": {"share_pct": 66.7}}}
    text = PortfolioAdvisor(backend, "prompt", 100).advise(report, {})
    assert text == GOOD_ADVICE and "revision" in backend.prompts[1]


def test_prompt_file_exists_and_forbids_new_numbers():
    from services.web.core.config import PORTFOLIO_ADVICE_PROMPT_FILE

    prompt = PORTFOLIO_ADVICE_PROMPT_FILE.read_text(encoding="utf-8")
    assert "숫자는 diagnosis와 market_context에 있는 값만" in prompt
    assert advisor_module.MIN_CHARS < advisor_module.MAX_CHARS


# ── 외부 자료 파서 ──────────────────────────────────────────────────────────

class _Response:
    def __init__(self, payload=None, text=""):
        self._payload, self.text = payload, text

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_fss_picks_the_best_12_month_basic_rate():
    body = {"result": {"err_cd": "000", "baseList": [
        {"fin_co_no": "1", "fin_prdt_cd": "A", "kor_co_nm": "가은행", "fin_prdt_nm": "정기예금"},
        {"fin_co_no": "2", "fin_prdt_cd": "B", "kor_co_nm": "나은행", "fin_prdt_nm": "더예금"}],
        "optionList": [
            {"fin_co_no": "1", "fin_prdt_cd": "A", "save_trm": "12", "intr_rate": 3.2, "intr_rate2": 3.5},
            {"fin_co_no": "2", "fin_prdt_cd": "B", "save_trm": "12", "intr_rate": 3.6, "intr_rate2": 3.6},
            {"fin_co_no": "2", "fin_prdt_cd": "B", "save_trm": "24", "intr_rate": 3.9, "intr_rate2": 3.9}]}}
    session = _Session([_Response(body), _Response({"result": {"err_cd": "000"}})])
    result = market_data.fetch_deposit_rates(session, key="k")
    assert result["deposit"]["best_rate_pct"] == 3.6 and result["deposit"]["top"][0]["bank"] == "나은행"
    assert result["saving"]["best_rate_pct"] is None
    assert market_data.fetch_deposit_rates(session, key="") == {"status": "missing_key"}


def test_ecos_reads_key_statistics_by_name():
    rows = [{"KEYSTAT_NAME": "한국은행 기준금리", "DATA_VALUE": "2.5", "CYCLE": "20260924"},
            {"KEYSTAT_NAME": "국고채수익률(3년)", "DATA_VALUE": "2.71", "CYCLE": "20260923"},
            {"KEYSTAT_NAME": "회사채수익률(3년,AA-)", "DATA_VALUE": "3.2", "CYCLE": "20260923"}]
    session = _Session([_Response({"KeyStatisticList": {"row": rows}})])
    result = market_data.fetch_market_rates(session, key="k")
    assert result["status"] == "ok" and result["treasury_3y"]["value_pct"] == 2.71
    assert result["corporate_3y"]["value_pct"] == 3.2
    error = market_data.fetch_market_rates(_Session([_Response({"RESULT": {"CODE": "INFO-100"}})]), key="k")
    assert error["status"] == "error"


def test_molit_estimates_from_the_median_price_per_m2_in_both_tag_styles():
    new_style = ("<response><header><resultCode>000</resultCode></header><body><items>"
                 "<item><dealAmount> 100,000</dealAmount><excluUseAr>100</excluUseAr><aptNm>가단지</aptNm></item>"
                 "<item><dealAmount>120,000</dealAmount><excluUseAr>100</excluUseAr><aptNm>가단지</aptNm></item>"
                 "</items></body></response>")
    old_style = ("<response><header><resultCode>00</resultCode></header><body><items>"
                 "<item><거래금액>90,000</거래금액><전용면적>100</전용면적><아파트>나단지</아파트></item>"
                 "</items></body></response>")
    session = _Session([_Response(text=new_style), _Response(text=old_style), _Response(text=new_style)])
    result = market_data.estimate_property("11680", 50.0, today=date(2026, 9, 24), session=session, key="k")
    # ㎡당 1000만·1200만·900만·1000만·1200만의 중앙값 1000만 × 50㎡.
    assert result == {"status": "ok", "basis": "region", "trade_count": 5, "months": 3,
                      "median_per_m2_krw": 10_000_000, "estimate_krw": 500_000_000}
    assert [call[1]["params"]["DEAL_YMD"] for call in session.calls] == ["202609", "202608", "202607"]
    failing = _Session([_Response(text="<response><header><resultCode>30</resultCode></header></response>")])
    assert market_data.estimate_property("11680", 50.0, today=date(2026, 9, 24), session=failing,
                                         key="k")["status"] == "error"
