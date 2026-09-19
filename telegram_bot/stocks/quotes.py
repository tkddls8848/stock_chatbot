"""요약 컨텍스트: 시세·자금흐름·섹터·인기순위·涨停·용호방 스냅샷.

원시 지표를 요약해 (a) 브리핑 메시지와 (b) 시장뷰 분석 payload에
주입한다. 자체 스코어링·백테스트는 하지 않는다(원시 지표 요약까지만).
모든 조회는 블로킹이므로 asyncio.to_thread로 호출해야 하며, 시장 전체
테이블은 TTL 캐시로 재사용한다.

데이터소스: 동방재부 push2* 시세 API는 해외 IP를 차단하므로
시세·자금흐름·섹터는 텐센트(qt.gtimg.cn)·시나 API를 쓰고,
해외에서도 열려 있는 涨停(push2ex)·용호방(datacenter-web)만 AkShare를 유지한다.
"""

import json
import logging
import time
from datetime import timedelta
from telegram_bot.core.clock import now
from typing import Any, Callable

import akshare as ak
import pandas as pd
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
_SINA_MONEYFLOW_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "MoneyFlow.ssi_ssfx_flzjtj"
)
_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
    "Referer": "https://finance.sina.com.cn",
}
_TENCENT_BATCH_SIZE = 60


def _akshare_retry(func):
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((requests.exceptions.RequestException, ConnectionError, TimeoutError, OSError, ValueError)),
        reraise=True,
    )(func)


class ProviderCooldownError(RuntimeError):
    """A provider recently failed, so another network call is temporarily skipped."""


def _safe_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(result) else result


# ── 텐센트/시나 응답 파싱(순수 함수) ─────────────────

def tencent_symbol(code: str) -> str | None:
    """관심종목 코드 → 텐센트 심볼(sh600519/sz000001/hk00700). 미지원은 None."""
    if len(code) == 6 and code.isdigit():
        if code[0] in ("6", "9"):
            return f"sh{code}"
        if code[0] in ("0", "3"):
            return f"sz{code}"
        return f"bj{code}"
    if len(code) == 5 and code.isdigit():
        return f"hk{code}"
    return None


_KR_YAHOO_SUFFIXES = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}


def yahoo_symbol(code: str) -> str | None:
    """미국·한국 universe 키 → Yahoo 심볼. 미지원은 None.

    텐센트는 A주·홍콩만 제공하므로 그 밖의 시장은 Yahoo로 조회한다.
    한국 6자리 코드는 A주 코드와 형식이 겹쳐 단독으로는 시장을 알 수 없으니
    거래소가 담긴 universe 키(KR:KOSDAQ:247540)만 받는다.
    """
    parts = str(code or "").split(":")
    if len(parts) != 3:
        return None
    market, exchange, raw = (part.strip().upper() for part in parts)
    if not raw:
        return None
    if market == "US":
        return raw.replace(".", "-")
    if market == "KR" and raw.isdigit():
        suffix = _KR_YAHOO_SUFFIXES.get(exchange)
        return f"{raw.zfill(6)}{suffix}" if suffix else None
    return None


def parse_yahoo_closes(close_frame: Any, symbols: list[str]) -> dict[str, dict[str, Any]]:
    """yfinance 종가 프레임 → 심볼별 {price, pct_change, amount}.

    단일 심볼이면 Series, 복수면 심볼별 컬럼을 가진 DataFrame이 온다.
    전일 종가가 없으면 등락률은 비운다(가격만으로도 쓸모가 있다).
    """
    if close_frame is None:
        return {}
    if isinstance(close_frame, pd.Series):
        close_frame = close_frame.to_frame(symbols[0] if symbols else "value")

    quotes: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        if symbol not in close_frame.columns:
            continue
        series = pd.to_numeric(close_frame[symbol], errors="coerce").dropna()
        if series.empty:
            continue
        price = _safe_float(series.iloc[-1])
        previous = _safe_float(series.iloc[-2]) if len(series) >= 2 else None
        pct_change = (
            (price / previous - 1) * 100
            if price is not None and previous not in (None, 0)
            else None
        )
        quotes[symbol] = {"price": price, "pct_change": pct_change, "amount": None}
    return quotes


def parse_tencent_quotes(text: str) -> dict[str, dict[str, Any]]:
    """`v_sh600519="1~贵州茅台~600519~..."` 응답 → 코드별 {price, pct_change, amount}.

    필드: [3] 최신가, [32] 등락률(%), [37] 거래대금(A주는 만위안, HK는 원 단위).
    """
    quotes: dict[str, dict[str, Any]] = {}
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if "=" not in chunk:
            continue
        key, _, value = chunk.partition("=")
        key = key.strip().removeprefix("v_")
        market, code = key[:2], key[2:]
        if market not in ("sh", "sz", "bj", "hk") or not code.isdigit():
            continue
        fields = value.strip().strip('"').split("~")
        if len(fields) < 38:
            continue
        price = _safe_float(fields[3])
        amount = _safe_float(fields[37])
        if amount is not None and market != "hk":
            amount *= 1e4  # A주 거래대금은 만위안 → 위안
        quotes[code] = {
            "price": price if price else None,  # 거래정지 등 0은 값 없음으로
            "pct_change": _safe_float(fields[32]),
            "amount": amount,
        }
    return quotes


def parse_sina_moneyflow(payload: dict[str, Any]) -> dict[str, Any] | None:
    """시나 MoneyFlow 응답 → {main_net_inflow, main_net_inflow_pct}.

    r0=초대단, r1=대단, r2=중단, r3=소단(_in/_out은 매수/매도 주도 금액).
    주력 순유입 = (r0_in+r1_in) - (r0_out+r1_out), 점유율은 전체 유출입 대비.
    """
    values: dict[str, float] = {}
    for name in ("r0_in", "r0_out", "r1_in", "r1_out", "r2_in", "r2_out", "r3_in", "r3_out"):
        number = _safe_float(payload.get(name))
        if number is None:
            return None
        values[name] = number
    main_net = (values["r0_in"] + values["r1_in"]) - (values["r0_out"] + values["r1_out"])
    turnover = sum(values.values())
    return {
        "date": "",
        "main_net_inflow": main_net,
        "main_net_inflow_pct": (main_net / turnover * 100) if turnover > 0 else None,
    }


class _TTLCache:
    def __init__(self, ttl_seconds: float, failure_cooldown_seconds: float):
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}
        self._failure_cooldown = failure_cooldown_seconds
        self._failures: dict[str, tuple[float, Exception]] = {}

    def get_or_fetch(self, key: str, fetch: Callable[[], Any]):
        now = time.monotonic()
        cached = self._store.get(key)
        if cached and now - cached[0] < self._ttl:
            return cached[1]
        previous_failure = self._failures.get(key)
        if previous_failure and now - previous_failure[0] < self._failure_cooldown:
            if cached:
                return cached[1]
            raise ProviderCooldownError(f"{key} is in failure cooldown") from previous_failure[1]
        try:
            value = fetch()
        except Exception as exc:
            self._failures[key] = (now, exc)
            if cached:
                return cached[1]
            raise
        self._store[key] = (now, value)
        self._failures.pop(key, None)
        return value


class QuoteService:
    """관심종목·시장 요약 스냅샷 제공자(블로킹, to_thread에서 호출)."""

    def __init__(
        self,
        cache_ttl_minutes: int = 10,
        sector_top_n: int = 5,
        failure_cooldown_minutes: int = 15,
    ):
        self._cache = _TTLCache(
            ttl_seconds=max(1, cache_ttl_minutes) * 60,
            failure_cooldown_seconds=max(1, failure_cooldown_minutes) * 60,
        )
        self._sector_top_n = max(1, sector_top_n)
        self._sector_labels: dict[str, str] = {}  # 시나 업종명 → label(구성종목 조회용)

    # ── 시세 ─────────────────────────────────────────

    def _fetch_tencent_quotes(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        """텐센트 배치 시세. 심볼 목록 단위로 TTL 캐시."""

        @_akshare_retry
        def fetch() -> dict[str, dict[str, Any]]:
            merged: dict[str, dict[str, Any]] = {}
            for i in range(0, len(symbols), _TENCENT_BATCH_SIZE):
                batch = symbols[i : i + _TENCENT_BATCH_SIZE]
                r = requests.get(
                    _TENCENT_QUOTE_URL + ",".join(batch),
                    headers=_HTTP_HEADERS,
                    timeout=10,
                )
                r.raise_for_status()
                r.encoding = "gbk"
                merged.update(parse_tencent_quotes(r.text))
            return merged

        key = "tencent_quotes:" + ",".join(sorted(symbols))
        return self._cache.get_or_fetch(key, fetch)

    def _fetch_yahoo_quotes(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        """미국·한국 종목 배치 시세(Yahoo). 심볼 목록 단위로 TTL 캐시."""

        @_akshare_retry
        def fetch() -> dict[str, dict[str, Any]]:
            import yfinance as yf

            frame = yf.download(
                symbols,
                period="5d",  # 주말·휴장을 건너뛰고 직전 종가를 확보하기 위한 여유
                progress=False,
                auto_adjust=True,
                threads=False,
            )
            if frame is None or frame.empty:
                return {}
            return parse_yahoo_closes(frame["Close"], symbols)

        key = "yahoo_quotes:" + ",".join(sorted(symbols))
        return self._cache.get_or_fetch(key, fetch)

    def get_watchlist_quotes(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        """코드별 {price, pct_change, amount}. 실패하면 조용히 비운다.

        A주·홍콩은 텐센트, 미국·한국은 Yahoo로 나눠 조회한다. 한쪽이 실패해도
        다른 시장 시세는 그대로 반환한다.
        """
        if not codes:
            return {}

        tencent_by_code = {c: s for c in codes if (s := tencent_symbol(c))}
        yahoo_by_code = {
            c: s for c in codes if c not in tencent_by_code and (s := yahoo_symbol(c))
        }

        quotes: dict[str, dict[str, Any]] = {}
        if tencent_by_code:
            try:
                fetched = self._fetch_tencent_quotes(sorted(set(tencent_by_code.values())))
                quotes.update({code: fetched[code] for code in tencent_by_code if code in fetched})
            except Exception as e:
                logger.warning("[SECTOR_SUMMARY] 시세 조회 실패: %s", e)
        if yahoo_by_code:
            try:
                fetched = self._fetch_yahoo_quotes(sorted(set(yahoo_by_code.values())))
                quotes.update(
                    {
                        code: fetched[symbol]
                        for code, symbol in yahoo_by_code.items()
                        if symbol in fetched
                    }
                )
            except Exception as e:
                logger.warning("[SECTOR_SUMMARY] 미국·한국 시세 조회 실패: %s", e)
        return quotes

    def get_price(self, code: str) -> float | None:
        """관심리스트 이벤트 기록용 현재가(최선 노력)."""
        try:
            return self.get_watchlist_quotes([code]).get(code, {}).get("price")
        except Exception:
            return None

    # ── 자금 흐름 ────────────────────────────────────

    def get_fund_flow(self, code: str) -> dict[str, Any] | None:
        """A주 개별 종목 최근 거래일 주력 자금 순유입(시나). HK는 미지원(None)."""
        if len(code) != 6:
            return None
        symbol = tencent_symbol(code)
        if not symbol or symbol.startswith("bj"):
            return None

        @_akshare_retry
        def fetch() -> dict[str, Any] | None:
            r = requests.get(
                _SINA_MONEYFLOW_URL,
                params={"daima": symbol},
                headers=_HTTP_HEADERS,
                timeout=10,
            )
            r.raise_for_status()
            payload = json.loads(r.text)
            if not isinstance(payload, dict):
                raise ValueError(f"unexpected moneyflow payload for {code}")
            return parse_sina_moneyflow(payload)

        try:
            return self._cache.get_or_fetch(f"fund_flow:{code}", fetch)
        except Exception as e:
            logger.warning("[SECTOR_SUMMARY] %s 자금흐름 조회 실패: %s", code, e)
            return None

    # ── 섹터/시장 온도 ───────────────────────────────

    def get_sector_rankings(self) -> dict[str, list[dict[str, Any]]]:
        """시나 업종 보드 등락률 상·하위 N개."""
        try:
            df = self._cache.get_or_fetch(
                "industry_boards",
                _akshare_retry(lambda: ak.stock_sector_spot(indicator="新浪行业")),
            )
            df = df.copy()
            df["涨跌幅"] = pd.to_numeric(df["涨跌幅"], errors="coerce")
            df = df.dropna(subset=["涨跌幅"]).sort_values("涨跌幅", ascending=False)
            self._sector_labels = {
                str(row["板块"]): str(row["label"]) for _, row in df.iterrows()
            }

            def rows_to_list(rows) -> list[dict[str, Any]]:
                return [
                    {
                        "name": str(row["板块"]),
                        "pct_change": _safe_float(row["涨跌幅"]),
                        "leader": str(row.get("股票名称") or ""),
                    }
                    for _, row in rows.iterrows()
                ]

            return {
                "top": rows_to_list(df.head(self._sector_top_n)),
                "bottom": rows_to_list(df.tail(self._sector_top_n).iloc[::-1]),
            }
        except Exception as e:
            logger.warning("[SECTOR_SUMMARY] 섹터 보드 조회 실패: %s", e)
            return {"top": [], "bottom": []}

    def get_sector_constituents(self, board_name: str) -> list[dict[str, str]]:
        """업종 보드 구성종목(리서치 후보군 발굴용). board_name은 시나 업종명."""
        if board_name not in self._sector_labels:
            self.get_sector_rankings()  # 라벨 매핑 채우기(최선 노력)
        label = self._sector_labels.get(board_name)
        if not label:
            logger.warning("[SECTOR_SUMMARY] %s 업종 라벨을 찾지 못해 구성종목 조회 생략", board_name)
            return []
        try:
            df = self._cache.get_or_fetch(
                f"board_cons:{board_name}",
                _akshare_retry(lambda: ak.stock_sector_detail(sector=label)),
            )
            return [
                {"code": str(row["code"]).zfill(6), "name": str(row["name"])}
                for _, row in df.iterrows()
            ]
        except Exception as e:
            logger.warning("[SECTOR_SUMMARY] %s 구성종목 조회 실패: %s", board_name, e)
            return []

    def get_zt_pool_summary(self) -> dict[str, Any]:
        """오늘 涨停 종목 수와 상위 몇 종목(단기 과열 온도계)."""
        try:
            date = now().strftime("%Y%m%d")
            df = self._cache.get_or_fetch(
                f"zt_pool:{date}",
                _akshare_retry(lambda: ak.stock_zt_pool_em(date=date)),
            )
            if df is None or df.empty:
                return {"count": 0, "names": []}
            return {
                "count": int(len(df)),
                "names": [str(n) for n in df["名称"].head(5).tolist()],
            }
        except Exception as e:
            logger.warning("[SECTOR_SUMMARY] 涨停 풀 조회 실패: %s", e)
            return {}

    def get_lhb_hits(self, codes: list[str], lookback_days: int = 5) -> list[dict[str, Any]]:
        """최근 용호방(龙虎榜)에 오른 관심종목."""
        if not codes:
            return []
        try:
            end = now()
            start = end - timedelta(days=max(1, lookback_days))
            df = self._cache.get_or_fetch(
                f"lhb:{end.strftime('%Y%m%d')}",
                _akshare_retry(lambda: ak.stock_lhb_detail_em(
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                )),
            )
            if df is None or df.empty:
                return []
            hits = []
            for _, row in df.iterrows():
                code = str(row.get("代码") or "").zfill(6)
                if code in codes:
                    hits.append(
                        {
                            "code": code,
                            "name": str(row.get("名称") or ""),
                            "date": str(row.get("上榜日") or ""),
                            "reason": str(row.get("解读") or row.get("上榜原因") or ""),
                        }
                    )
            return hits
        except Exception as e:
            logger.warning("[SECTOR_SUMMARY] 용호방 조회 실패: %s", e)
            return []

    # ── 종합 스냅샷 ──────────────────────────────────

    def build_sector_summary_context(
        self,
        watchlist: dict[str, str],
        include_fund_flow: bool = True,
    ) -> dict[str, Any]:
        """브리핑·시장뷰 분석에 주입할 요약 스냅샷. 각 항목은 최선 노력."""
        codes = list(watchlist.keys())
        quotes = self.get_watchlist_quotes(codes)

        watchlist_rows = []
        for code, name in watchlist.items():
            quote = quotes.get(code) or {}
            row: dict[str, Any] = {
                "code": code,
                "name": name,
                "price": quote.get("price"),
                "pct_change": quote.get("pct_change"),
            }
            if include_fund_flow and len(code) == 6:
                flow = self.get_fund_flow(code)
                if flow:
                    row["main_net_inflow"] = flow.get("main_net_inflow")
            watchlist_rows.append(row)

        sectors = self.get_sector_rankings()
        return {
            "as_of": now().isoformat(timespec="seconds"),
            "watchlist": watchlist_rows,
            "sector_top": sectors.get("top", []),
            "sector_bottom": sectors.get("bottom", []),
            "lhb_hits": self.get_lhb_hits(codes),
            "zt_pool": self.get_zt_pool_summary(),
        }


# ── 표시 헬퍼(순수 함수) ─────────────────────────────

def _fmt_pct(value: Any) -> str:
    number = _safe_float(value)
    return f"{number:+.2f}%" if number is not None else "-"


def _fmt_yi(value: Any) -> str:
    """위안화 금액을 억 단위로 축약."""
    number = _safe_float(value)
    if number is None:
        return "-"
    return f"{number / 1e8:+.1f}억"


def format_sector_summary_text(context: dict[str, Any], watchlist: dict[str, str]) -> str:
    """요약 스냅샷을 텔레그램 HTML 본문으로 변환한다."""
    import html as _html

    if not context:
        return ""
    lines: list[str] = []

    rows = context.get("watchlist") or []
    if rows:
        lines.append("<b>관심종목 시세</b>")
        for row in rows:
            name = _html.escape(str(row.get("name") or row.get("code")))
            price = row.get("price")
            price_part = f"{price:,.2f}" if isinstance(price, (int, float)) else "-"
            line = f"• {name} ({row.get('code')}): {price_part} ({_fmt_pct(row.get('pct_change'))})"
            if row.get("main_net_inflow") is not None:
                line += f" 주력 {_fmt_yi(row.get('main_net_inflow'))}"
            lines.append(line)

    sector_top = context.get("sector_top") or []
    sector_bottom = context.get("sector_bottom") or []
    if sector_top or sector_bottom:
        lines.append("")
        lines.append("<b>섹터 온도</b>")
        if sector_top:
            tops = ", ".join(
                f"{_html.escape(s['name'])} {_fmt_pct(s.get('pct_change'))}" for s in sector_top
            )
            lines.append(f"강세: {tops}")
        if sector_bottom:
            bottoms = ", ".join(
                f"{_html.escape(s['name'])} {_fmt_pct(s.get('pct_change'))}" for s in sector_bottom
            )
            lines.append(f"약세: {bottoms}")

    zt_pool = context.get("zt_pool") or {}
    if zt_pool.get("count") is not None:
        lines.append(f"涨停 {zt_pool.get('count', 0)}종목")

    lhb_hits = context.get("lhb_hits") or []
    if lhb_hits:
        lines.append("<b>용호방(龙虎榜) 관심종목</b>")
        for hit in lhb_hits[:5]:
            reason = _html.escape(str(hit.get("reason") or "")[:60])
            lines.append(
                f"• {_html.escape(hit['name'] or hit['code'])} ({hit['code']}) {hit.get('date', '')} {reason}"
            )

    return "\n".join(lines).strip()
