"""뷰를 실제 시장에서 표현할 ETF·ETN 표. 손으로 골라 적고 상장 목록과 대조한다.

컨센서스는 "금리가 오른다"·"유가가 내린다" 같은 방향을 말하고, 그 방향을 살 수 있는 상품은 대개
개별주가 아니라 ETF·ETN이다. 봇 종목 DB는 ETF를 일부러 뺀다(`stocks/universe.py` — 뉴스 종목명
매칭용이라서다). 그래서 방향별 대표 상품을 여기에 따로 둔다.

**표가 정하는 것은 "무엇이 무엇을 따라 움직이는가"뿐이다.** 어느 상품을 어느 쪽으로 볼지는 포지션
단계의 모델이 뷰와 컨센서스를 보고 고르고, 서버는 고른 코드가 이 표(또는 계정 관심종목)에 있는지만
확인한다. 모델은 표에 없는 상품을 낼 수 없다.

고른 기준: 주제마다 순자산·시가총액이 큰 대표 상품, 정방향과 역방향을 함께, 한국 상장과 미국 상장을
함께. 한국 상품은 숫자 여섯 자리 코드만 싣는다 — 관심종목 정규 코드(`KR:KOSPI:\\d{6}`)에 들어가야
"관심종목에 추가"가 된다(2024년 이후 상장분의 영문 섞인 코드는 뺐다). ETF·ETN은 전부 유가증권시장
상장이라 거래소는 KOSPI다.

**ETN은 만기가 있고 상품이 바뀐다.** 표가 낡았는지는 `tests/viewcheck_instruments_probe.py`가 공개 상장
목록(네이버 금융 ETF·ETN 목록, 나스닥 심볼 디렉터리)과 대조해 알려 준다. 2026-11-11 대조 통과.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    code: str        # 관심종목과 같은 정규 코드
    name: str
    market: str      # KR | US
    kind: str        # ETF | ETN
    side: int        # 1: 주제와 같은 방향, -1: 반대 방향(인버스)
    leverage: float  # 하루 수익률의 배수(절댓값)
    note: str = ""   # 주제의 대표 흐름과 다른 점(예: 원유 가격이 아니라 에너지 기업 주식)


@dataclass(frozen=True)
class Theme:
    key: str
    label: str
    moves: str  # 이 주제의 정방향 상품이 무엇을 따라 어떻게 움직이는지. 모델이 방향을 정하는 근거다.
    instruments: tuple[Instrument, ...]


def _kr(code: str, name: str, *, side: int = 1, leverage: float = 1, kind: str = "ETF", note: str = "") -> Instrument:
    return Instrument(f"KR:KOSPI:{code}", name, "KR", kind, side, leverage, note)


def _us(exchange: str, ticker: str, name: str, *, side: int = 1, leverage: float = 1, note: str = "") -> Instrument:
    return Instrument(f"US:{exchange}:{ticker}", name, "US", "ETF", side, leverage, note)


THEMES: tuple[Theme, ...] = (
    Theme("kr_equity", "한국 주가지수", "코스피200·코스닥150 지수를 따른다. 한국 증시가 오르면 오른다.", (
        _kr("069500", "KODEX 200"),
        _kr("122630", "KODEX 레버리지", leverage=2),
        _kr("114800", "KODEX 인버스", side=-1),
        _kr("252670", "KODEX 200선물인버스2X", side=-1, leverage=2),
        _kr("229200", "KODEX 코스닥150", note="코스닥150"),
        _kr("233740", "KODEX 코스닥150레버리지", leverage=2, note="코스닥150"),
        _kr("251340", "KODEX 코스닥150선물인버스", side=-1, note="코스닥150"),
        _kr("530107", "삼성 인버스 2X 코스닥150 선물 ETN", side=-1, leverage=2, kind="ETN", note="코스닥150"),
        _us("NYSEARCA", "EWY", "iShares MSCI South Korea ETF", note="달러로 본 한국 대형주"),
    )),
    Theme("us_equity", "미국 주가지수", "S&P500·나스닥100 지수를 따른다. 미국 증시가 오르면 오른다.", (
        _kr("360750", "TIGER 미국S&P500", note="환율 노출"),
        _kr("133690", "TIGER 미국나스닥100", note="환율 노출"),
        _kr("418660", "TIGER 미국나스닥100레버리지(합성)", leverage=2),
        _kr("225030", "TIGER 미국S&P500선물인버스(H)", side=-1),
        _us("NYSEARCA", "SPY", "SPDR S&P 500 ETF"),
        _us("NASDAQ", "QQQ", "Invesco QQQ(나스닥100)"),
        _us("NYSEARCA", "IWM", "iShares Russell 2000", note="미국 중소형주"),
        _us("NYSEARCA", "SH", "ProShares Short S&P500", side=-1),
        _us("NYSEARCA", "PSQ", "ProShares Short QQQ", side=-1),
        _us("NASDAQ", "TQQQ", "ProShares UltraPro QQQ", leverage=3),
        _us("NASDAQ", "SQQQ", "ProShares UltraPro Short QQQ", side=-1, leverage=3),
    )),
    Theme("us_rates", "미국 금리(국채 가격)",
          "미국 국채 가격을 따른다. 미국 금리가 내리면 오르고 금리가 오르면 내린다. 만기가 길수록 크게 움직인다.", (
        _kr("453850", "ACE 미국30년국채액티브(H)", note="장기"),
        _kr("305080", "TIGER 미국채10년선물", note="중기, 환율 노출"),
        _us("NASDAQ", "TLT", "iShares 20+ Year Treasury Bond", note="장기"),
        _us("NASDAQ", "IEF", "iShares 7-10 Year Treasury Bond", note="중기"),
        _us("NASDAQ", "SHY", "iShares 1-3 Year Treasury Bond", note="단기"),
        _us("NYSEARCA", "TIP", "iShares TIPS Bond", note="물가연동국채"),
        _us("NYSEARCA", "TBF", "ProShares Short 20+ Year Treasury", side=-1, note="장기"),
        _us("NYSEARCA", "TBT", "ProShares UltraShort 20+ Year Treasury", side=-1, leverage=2, note="장기"),
        _us("NYSEARCA", "TMF", "Direxion Daily 20+ Year Treasury Bull 3X", leverage=3, note="장기"),
        _us("NYSEARCA", "TMV", "Direxion Daily 20+ Year Treasury Bear 3X", side=-1, leverage=3, note="장기"),
    )),
    Theme("kr_rates", "한국 금리(국고채 가격)",
          "한국 국고채 가격을 따른다. 한국 금리가 내리면 오르고 금리가 오르면 내린다.", (
        _kr("114260", "KODEX 국고채3년", note="단기"),
        _kr("365780", "ACE 국고채10년", note="중기"),
        _kr("439870", "KODEX 국고채30년액티브", note="장기"),
        _kr("292770", "KODEX 국채선물3년인버스", side=-1, note="단기"),
        _kr("176950", "KODEX 국채선물10년인버스", side=-1, note="중기"),
    )),
    Theme("cash", "대기 자금(단기 금리)",
          "가격이 거의 움직이지 않고 단기 금리만큼 쌓인다. 방향을 정하지 못했을 때 기다리는 자리다.", (
        _kr("488770", "KODEX 머니마켓액티브"),
        _kr("459580", "KODEX CD금리액티브(합성)"),
        _us("NYSEARCA", "BIL", "SPDR Bloomberg 1-3 Month T-Bill"),
        _us("NYSE", "SGOV", "iShares 0-3 Month Treasury Bond"),
    )),
    Theme("credit", "신용(하이일드 회사채)",
          "신용도가 낮은 미국 회사채 가격을 따른다. 경기 침체·부도 우려가 커지면 내린다.", (
        _kr("468380", "KODEX iShares미국하이일드액티브"),
        _us("NYSEARCA", "HYG", "iShares iBoxx High Yield Corporate Bond"),
    )),
    Theme("semis", "반도체·AI 하드웨어", "반도체 기업 주가를 따른다. AI 투자·메모리 업황 기대가 커지면 오른다.", (
        _kr("091160", "KODEX 반도체", note="한국 반도체"),
        _kr("396500", "TIGER 반도체TOP10", note="한국 반도체"),
        _kr("494310", "KODEX 반도체레버리지", leverage=2, note="한국 반도체"),
        _kr("381180", "TIGER 미국필라델피아반도체나스닥", note="미국 반도체"),
        _kr("520099", "미래에셋 인버스 2X 반도체 ETN", side=-1, leverage=2, kind="ETN", note="한국 반도체"),
        _us("NASDAQ", "SMH", "VanEck Semiconductor ETF"),
        _us("NASDAQ", "SOXX", "iShares Semiconductor ETF"),
        _us("NYSEARCA", "SOXL", "Direxion Daily Semiconductor Bull 3X", leverage=3),
        _us("NYSEARCA", "SOXS", "Direxion Daily Semiconductor Bear 3X", side=-1, leverage=3),
    )),
    Theme("bigtech", "미국 빅테크·AI 소프트웨어", "미국 대형 기술주를 따른다. AI 기대와 금리에 민감하다.", (
        _kr("381170", "TIGER 미국테크TOP10 INDXX"),
        _kr("465580", "ACE 미국빅테크TOP7 Plus"),
        _us("NYSEARCA", "XLK", "Technology Select Sector SPDR"),
    )),
    Theme("power", "전력·원자력", "AI 전력 수요와 원자력 기업 주가를 따른다.", (
        _kr("487240", "KODEX AI전력핵심설비", note="한국 전력기기"),
        _kr("487230", "KODEX 미국AI전력핵심인프라", note="미국 전력 인프라"),
        _kr("434730", "HANARO 원자력iSelect", note="한국 원자력"),
        _us("NYSEARCA", "URA", "Global X Uranium ETF", note="우라늄"),
        _us("NYSEARCA", "XLU", "Utilities Select Sector SPDR", note="미국 전력 유틸리티"),
    )),
    Theme("oil", "원유", "WTI 원유 가격을 따른다. 유가가 오르면 오른다.", (
        _kr("261220", "KODEX WTI원유선물(H)"),
        _kr("271050", "KODEX WTI원유선물인버스(H)", side=-1),
        _kr("530031", "삼성 레버리지 WTI원유 선물 ETN", leverage=2, kind="ETN"),
        _kr("530134", "삼성 블룸버그 인버스2X WTI원유선물 ETN B", side=-1, leverage=2, kind="ETN"),
        _us("NYSEARCA", "USO", "United States Oil Fund"),
        _us("NYSEARCA", "UCO", "ProShares Ultra Bloomberg Crude Oil", leverage=2),
        _us("NYSEARCA", "SCO", "ProShares UltraShort Bloomberg Crude Oil", side=-1, leverage=2),
        _us("NYSEARCA", "XLE", "Energy Select Sector SPDR", note="원유 가격이 아니라 에너지 기업 주식"),
    )),
    Theme("natgas", "천연가스", "천연가스 선물 가격을 따른다.", (
        _kr("530145", "삼성 레버리지 천연가스 선물 ETN D", leverage=2, kind="ETN"),
        _kr("530136", "삼성 인버스 2X 천연가스 선물 ETN D", side=-1, leverage=2, kind="ETN"),
        _us("NYSEARCA", "UNG", "United States Natural Gas Fund"),
        _us("NYSEARCA", "KOLD", "ProShares UltraShort Bloomberg Natural Gas", side=-1, leverage=2),
    )),
    Theme("gold", "금", "금 가격을 따른다. 실질금리 하락·달러 약세·지정학 불안에 오르는 경향이 있다.", (
        _kr("411060", "ACE KRX금현물", note="환율 노출"),
        _kr("132030", "KODEX 골드선물(H)"),
        _kr("280940", "KODEX 골드선물인버스(H)", side=-1),
        _kr("500037", "신한 레버리지 금 선물 ETN", leverage=2, kind="ETN"),
        _kr("610104", "메리츠 인버스 2X 금 선물 ETN(H)", side=-1, leverage=2, kind="ETN"),
        _us("NYSEARCA", "GLD", "SPDR Gold Shares"),
        _us("NYSEARCA", "GDX", "VanEck Gold Miners ETF", note="금 가격이 아니라 금광 기업 주식"),
    )),
    Theme("metals", "은·구리(산업금속)", "은·구리 가격을 따른다. 제조업 경기 기대에 민감하다.", (
        _kr("144600", "KODEX 은선물(H)", note="은"),
        _kr("138910", "KODEX 구리선물(H)", note="구리"),
        _us("NYSEARCA", "SLV", "iShares Silver Trust", note="은"),
        _us("NYSEARCA", "CPER", "United States Copper Index Fund", note="구리"),
    )),
    Theme("usd_krw", "달러(원/달러 환율)", "원/달러 환율을 따른다. 달러가 원화보다 강해지면(환율 상승) 오른다.", (
        _kr("261240", "KODEX 미국달러선물"),
        _kr("261250", "KODEX 미국달러선물레버리지", leverage=2),
        _kr("261270", "KODEX 미국달러선물인버스", side=-1),
        _kr("261260", "KODEX 미국달러선물인버스2X", side=-1, leverage=2),
        _us("NYSEARCA", "UUP", "Invesco DB US Dollar Index Bullish", note="원화가 아니라 주요 통화 대비 달러"),
    )),
    Theme("jpy", "엔화", "엔화 가치를 따른다. 일본은행이 금리를 올리거나 위험 회피가 커지면 엔화가 강해진다.", (
        _kr("292560", "TIGER 일본엔선물", note="원화 대비 엔"),
        _us("NYSEARCA", "FXY", "Invesco CurrencyShares Japanese Yen", note="달러 대비 엔"),
    )),
    Theme("china", "중국·홍콩 주식", "중국 본토와 홍콩 상장 기업 주가를 따른다. 부양책·미중 관계에 민감하다.", (
        _kr("371160", "TIGER 차이나항셍테크", note="홍콩 기술주"),
        _kr("283580", "KODEX 차이나CSI300", note="본토 대형주"),
        _kr("371460", "TIGER 차이나전기차SOLACTIVE", note="중국 전기차"),
        _us("NYSEARCA", "FXI", "iShares China Large-Cap ETF"),
        _us("NYSEARCA", "KWEB", "KraneShares CSI China Internet ETF", note="중국 인터넷"),
        _us("NYSEARCA", "YINN", "Direxion Daily FTSE China Bull 3X", leverage=3),
        _us("NYSEARCA", "YANG", "Direxion Daily FTSE China Bear 3X", side=-1, leverage=3),
    )),
    Theme("japan", "일본 주식", "일본 대형주를 따른다.", (
        _kr("241180", "TIGER 일본니케이225", note="환율 노출"),
        _us("NYSEARCA", "EWJ", "iShares MSCI Japan ETF"),
    )),
    Theme("taiwan", "대만 주식", "대만 대형주(반도체 비중이 크다)를 따른다. 양안 긴장이 커지면 내린다.", (
        _us("NYSEARCA", "EWT", "iShares MSCI Taiwan ETF"),
    )),
    Theme("india", "인도 주식", "인도 대형주를 따른다.", (
        _kr("453810", "KODEX 인도Nifty50"),
        _us("CBOE", "INDA", "iShares MSCI India ETF"),
    )),
    Theme("europe", "유럽 주식", "유럽 대형주를 따른다.", (
        _kr("195930", "TIGER 유로스탁스50(합성 H)"),
        _us("NYSEARCA", "VGK", "Vanguard FTSE Europe ETF"),
    )),
    Theme("defense", "방산", "방위산업 기업 주가를 따른다. 전쟁·군비 증강 기대가 커지면 오른다.", (
        _kr("449450", "PLUS K방산", note="한국 방산"),
        _kr("463250", "TIGER K방산&우주", note="한국 방산"),
        _us("CBOE", "ITA", "iShares U.S. Aerospace & Defense ETF", note="미국 방산"),
    )),
    Theme("shipbuilding", "조선", "한국 조선 기업 주가를 따른다.", (
        _kr("466920", "SOL 조선TOP3플러스"),
        _kr("494670", "TIGER 조선TOP10"),
    )),
    Theme("banks", "은행·금융", "은행·금융 기업 주가를 따른다. 금리 수준과 경기에 민감하다.", (
        _kr("091170", "KODEX 은행", note="한국 은행"),
        _kr("466940", "TIGER 은행고배당플러스TOP10", note="한국 은행"),
        _us("NYSEARCA", "XLF", "Financial Select Sector SPDR", note="미국 금융"),
        _us("NYSEARCA", "KRE", "SPDR S&P Regional Banking ETF", note="미국 지역은행"),
    )),
    Theme("battery", "2차전지·전기차", "2차전지·전기차 공급망 기업 주가를 따른다.", (
        _kr("305720", "KODEX 2차전지산업"),
        _kr("305540", "TIGER 2차전지테마"),
        _kr("462330", "KODEX 2차전지산업레버리지", leverage=2),
        _us("NYSEARCA", "LIT", "Global X Lithium & Battery Tech ETF"),
    )),
    Theme("auto", "자동차", "한국 자동차 기업 주가를 따른다. 관세·환율에 민감하다.", (
        _kr("091180", "KODEX 자동차"),
    )),
    Theme("bio", "바이오·헬스케어", "바이오·헬스케어 기업 주가를 따른다. 금리와 약가 정책에 민감하다.", (
        _kr("244580", "KODEX 바이오"),
        _us("NYSEARCA", "XBI", "SPDR S&P Biotech ETF"),
        _us("NYSEARCA", "XLV", "Health Care Select Sector SPDR"),
    )),
    Theme("reits", "리츠(부동산)", "상장 부동산·리츠 가격을 따른다. 금리가 내리면 오르는 경향이 있다.", (
        _kr("329200", "TIGER 리츠부동산인프라", note="한국 리츠"),
        _us("NYSEARCA", "VNQ", "Vanguard Real Estate ETF", note="미국 리츠"),
    )),
    Theme("crypto", "가상자산", "비트코인·이더리움 가격을 따른다. 한국 상장 현물 상품은 없다.", (
        _us("NASDAQ", "IBIT", "iShares Bitcoin Trust ETF", note="비트코인"),
        _us("NASDAQ", "ETHA", "iShares Ethereum Trust ETF", note="이더리움"),
        _us("NYSEARCA", "BITI", "ProShares Short Bitcoin ETF", side=-1, note="비트코인"),
    )),
    Theme("volatility", "변동성(VIX)",
          "미국 증시 변동성 지수 선물을 따른다. 증시가 급락할 때 오른다. 오래 들고 가면 선물 교체 비용으로 깎인다.", (
        _kr("530130", "삼성 S&P500 VIX S/T 선물 ETN B", kind="ETN"),
        _us("CBOE", "VIXY", "ProShares VIX Short-Term Futures ETF"),
        _us("CBOE", "SVXY", "ProShares Short VIX Short-Term Futures ETF", side=-1, leverage=0.5),
    )),
)

BY_CODE: dict[str, Instrument] = {item.code: item for theme in THEMES for item in theme.instruments}
THEME_OF: dict[str, Theme] = {item.code: theme for theme in THEMES for item in theme.instruments}


def describe(item: Instrument) -> str:
    """상품 한 줄 꼴: 정방향·역방향과 배수."""
    side = "정방향" if item.side > 0 else "역방향"
    leverage = "" if item.leverage == 1 else f" {item.leverage:g}배"
    return side + leverage


def for_model() -> list[dict]:
    """포지션 단계 모델 입력. 주제마다 무엇을 따르는지와 상품 목록."""
    return [{
        "theme": theme.label, "moves": theme.moves,
        "instruments": [{
            "code": item.code, "name": item.name, "market": "한국" if item.market == "KR" else "미국",
            "type": item.kind, "direction": describe(item), **({"note": item.note} if item.note else {}),
        } for item in theme.instruments],
    } for theme in THEMES]
