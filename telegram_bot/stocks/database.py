"""StockDatabase: 종목 코드/이름 캐시의 빌드·보강·조회.

외부 데이터 수집·파싱·분류 유틸리티는 stocks.sources에 분리되어 있다.
"""

import json
import logging
from pathlib import Path

from telegram_bot.core.storage import write_json_atomic
from telegram_bot.stocks.sources import (
    _classify_market,
    _fetch_a_code_name,
    _fetch_hk_spot,
    _stock_entry,
)
from telegram_bot.stocks.universe import fetch_kr_listed, fetch_us_listed, stock_key

logger = logging.getLogger(__name__)

_MARKET_SCOPE = {
    "CN": "CN",
    "SH": "CN",
    "SZ": "CN",
    "STAR": "CN",
    "CHI": "CN",
    "HK": "HK",
    "KR": "KR",
    "US": "US",
}


class StockDatabase:
    def __init__(self, cache_file: Path):
        self._cache_file = cache_file
        self._db: dict[str, dict] = {}
        self._aliases: dict[str, str | None] | None = None

    def build(self) -> None:
        """종목 코드·이름 목록만 갱신한다. 시총·업종 보강은 enrich()를 별도로 실행."""
        db: dict[str, dict] = {}
        old_db = self._load_existing_cache()
        a_loaded = False
        hk_loaded = False
        try:
            df_a = _fetch_a_code_name()
            if df_a is None or df_a.empty:
                raise RuntimeError("A-share universe is empty")
            cols = list(df_a.columns)
            if "code" in cols:
                code_col, name_col = "code", "name"
            else:
                code_col, name_col = "股票代码", "股票名称"
            for _, row in df_a.iterrows():
                code = str(row[code_col]).zfill(6)
                key = stock_key("CN", "", code)
                cn_name = row[name_col]
                market = _classify_market(code)
                ticker = f"{code}.SS" if market == "SH" else f"{code}.SZ"
                entry = _stock_entry(cn_name, market, ticker=ticker, exchange=market)
                # 기존 시장 보강 데이터 보존
                old = old_db.get(key, {})
                for field in ("market_cap_cny", "industry"):
                    if old.get(field):
                        entry[field] = old[field]
                db[key] = entry
            a_loaded = True
            logger.info("[StockDB] A주 %d종목 로드", len(db))
        except Exception as e:
            logger.warning("[StockDB] A주 빌드 실패: %s", e)
            self._preserve_markets(db, old_db, {"SH", "SZ", "STAR", "CHI"})

        hk_before = len(db)
        try:
            df_hk = _fetch_hk_spot()
            if df_hk is None or df_hk.empty:
                raise RuntimeError("Hong Kong universe is empty")
            cols = list(df_hk.columns)
            if "代码" in cols and "中文名称" in cols:
                hk_code_col, hk_name_col = "代码", "中文名称"
            elif "代码" in cols and "名称" in cols:
                hk_code_col, hk_name_col = "代码", "名称"
            else:
                hk_code_col, hk_name_col = cols[1], cols[2]
            for _, row in df_hk.iterrows():
                code = str(row[hk_code_col]).zfill(5)
                key = stock_key("HK", "HKEX", code)
                cn_name = row[hk_name_col]
                entry = _stock_entry(cn_name, "HK", ticker=f"{code.zfill(4)}.HK", exchange="HKEX")
                old = old_db.get(key, {})
                for field in ("market_cap_cny", "industry"):
                    if old.get(field):
                        entry[field] = old[field]
                db[key] = entry
            hk_loaded = True
            logger.info("[StockDB] 홍콩 %d종목 로드", len(db) - hk_before)
        except Exception as e:
            logger.warning("[StockDB] 홍콩 빌드 실패: %s", e)
            self._preserve_markets(db, old_db, {"HK"})

        self._build_market_universe(db, old_db, "KR", fetch_kr_listed)
        self._build_market_universe(db, old_db, "US", fetch_us_listed)

        if not a_loaded and not hk_loaded and not db:
            raise RuntimeError("A주와 홍콩 주식 DB 빌드가 모두 실패했습니다")

        # 캐시를 먼저 완성해 교체한 뒤에 메모리를 바꾼다. 저장이 실패하면
        # 예외가 /stockdb build까지 올라가고 이전 DB가 그대로 남는다.
        write_json_atomic(self._cache_file, db)
        self._db = db
        self._aliases = None
        logger.info("[StockDB] DB 빌드 완료: 총 %d종목", len(db))

    @staticmethod
    def _build_market_universe(db: dict[str, dict], old_db: dict[str, dict], market: str, fetcher) -> None:
        try:
            rows = fetcher()
        except Exception as exc:
            logger.warning("[StockDB] %s universe collection failed: %s", market, exc)
            StockDatabase._preserve_markets(db, old_db, {market})
            return
        if not rows:
            logger.warning("[StockDB] %s universe collection returned no rows", market)
            StockDatabase._preserve_markets(db, old_db, {market})
            return
        for row in rows:
            entry = {
                "display_name": str(row["display_name"]),
                "cn_name": "",
                "ko_name": str(row["display_name"]),
                "market": market,
                "ticker": str(row["code"]),
                "yahoo_ticker": str(row["yahoo_ticker"]),
                "exchange": str(row["exchange"]),
                "asset_type": str(row.get("asset_type") or "EQUITY"),
                "currency": str(row.get("currency") or ("KRW" if market == "KR" else "USD")),
                "status": "ACTIVE",
                "source": f"{market.lower()}-listed-universe",
                "schema_version": 3,
                "figi": "",
                "compositeFIGI": "",
                "shareClassFIGI": "",
                "isin": "",
            }
            db[str(row["key"])] = entry
        logger.info("[StockDB] %s universe loaded: %d tickers", market, len(rows))

    def _load_existing_cache(self) -> dict[str, dict]:
        if not self._cache_file.exists():
            return {}
        try:
            return json.loads(self._cache_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("[StockDB] 기존 캐시 읽기 실패: %s", e)
            return {}

    @staticmethod
    def _preserve_markets(
        db: dict[str, dict],
        old_db: dict[str, dict],
        markets: set[str],
    ) -> None:
        for code, entry in old_db.items():
            if (
                not isinstance(entry, dict)
                or str(entry.get("market") or "").upper() not in markets
                or code in db
            ):
                continue
            db[code] = dict(entry)

    def load(self) -> None:
        self._db = json.loads(self._cache_file.read_text(encoding="utf-8"))
        self._aliases = None
        logger.info("[StockDB] 캐시 로드: %d종목", len(self._db))

    def load_or_build(self) -> None:
        if self._cache_file.exists():
            try:
                self.load()
                return
            except Exception as e:
                logger.warning("[StockDB] 캐시 로드 실패, 재빌드: %s", e)
        try:
            self.build()
        except Exception as e:
            logger.warning("[StockDB] 빌드 실패, 빈 DB로 동작: %s", e)

    def _alias_index(self) -> dict[str, str | None]:
        """접미 코드를 고유 universe 키와 시장 한정 키로 연결한다."""
        if self._aliases is None:
            aliases: dict[str, str | None] = {}

            def register(alias: str, key: str) -> None:
                if alias not in aliases:
                    aliases[alias] = key
                elif aliases[alias] != key:
                    aliases[alias] = None

            for key, entry in self._db.items():
                upper_key = str(key).upper()
                suffix = upper_key.rsplit(":", 1)[-1]
                entry_market = (
                    str(entry.get("market") or "").upper()
                    if isinstance(entry, dict)
                    else ""
                )
                scope = _MARKET_SCOPE.get(entry_market)
                if ":" in upper_key:
                    register(suffix, key)
                    if scope is None:
                        scope = _MARKET_SCOPE.get(upper_key.split(":", 1)[0])
                if scope is not None:
                    register(f"{scope}:{suffix}", key)
            self._aliases = aliases
        return self._aliases

    def resolve_code(self, code: str) -> str | None:
        """DB 정확 키 또는 시장 보존 별칭을 정식 키로 해석한다."""
        raw = str(code or "").strip()
        if not raw:
            return None
        if raw in self._db:
            return raw
        upper = raw.upper()
        if upper in self._db:
            return upper
        if ":" not in upper:
            return self._alias_index().get(upper)

        parts = upper.split(":")
        if len(parts) != 3:
            return None
        market, exchange, suffix = parts
        scope = _MARKET_SCOPE.get(market)
        if scope is None:
            return None
        normalized = stock_key(scope, exchange, suffix)
        alias = normalized.rsplit(":", 1)[-1]
        return self._alias_index().get(f"{scope}:{alias}")

    def get_display_name(self, code: str) -> str | None:
        resolved = self.resolve_code(code)
        entry = self._db.get(resolved) if resolved else None
        return str(entry["display_name"]) if entry and entry.get("display_name") else None

    def get_market(self, code: str) -> str | None:
        """코드가 실제 속한 시장(권위 있는 값)을 DB에서 조회. 뉴스 로그의 출처 기반
        market 태그(기사 단위)와 달리 종목별로 정확하다."""
        resolved = self.resolve_code(code)
        entry = self._db.get(resolved) if resolved else None
        return str(entry["market"]) if entry and entry.get("market") else None

    def get_all(self) -> dict[str, dict]:
        return self._db.copy()

    def get_candidate_universe(self, limit: int | None = None) -> list[dict]:
        """리서치 후보 탐색용 종목 목록(코드·이름·시장·시총)."""
        items = [
            {
                "code": code,
                "display_name": str(entry.get("display_name") or ""),
                "cn_name": str(entry.get("cn_name") or entry.get("display_name") or ""),
                "ko_name": str(entry.get("ko_name") or ""),
                "market": str(entry.get("market") or ""),
                "market_cap_cny": float(entry.get("market_cap_cny") or 0.0),
            }
            for code, entry in self._db.items()
        ]
        return items[:limit] if limit else items
