"""주요 종목의 여러 언어 이름. 내 자산 점검(`watch.py`)이 "삼성전자"로 적은 종목을 영어·중국어·일본어
기사에서도 찾게 한다.

봇의 종목 DB에는 시장마다 한 언어 이름뿐이라(한국 종목은 한국어, 미국 종목은 영어) 옮겨 올 수 없고,
개인 자산 이름을 외부 AI로 보내 번역하지 않는다 — 그래서 손으로 적은 표다. 기사에 자주 나오는
대형주만 둔다. 없는 종목은 적힌 이름·코드 그대로만 찾는다.

열쇠는 종목 코드다. 한국·미국은 관심종목 정규 코드(`KR:KOSPI:005930`·`US:NASDAQ:NVDA`), 중국·홍콩은
숫자 코드, 일본은 `JP:`를 붙인 숫자다. 자산에 거래소 없이 적힌 코드도 맞춘다(`lookup`).
이름은 정규화(소문자·NFKC) 뒤 기사에서 찾는다. 다른 낱말의 일부가 되는 이름("메타"→메타버스,
"Meta"→meta-analysis)은 넣지 않는다.
"""

from __future__ import annotations

GROUPS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    # ── 한국 ──
    (("KR:KOSPI:005930",), ("삼성전자", "Samsung Electronics", "三星电子", "三星電子", "サムスン電子")),
    (("KR:KOSPI:000660",), ("SK하이닉스", "SK hynix", "SK海力士", "SKハイニックス")),
    (("KR:KOSPI:373220",), ("LG에너지솔루션", "LG엔솔", "LG Energy Solution")),
    (("KR:KOSPI:207940",), ("삼성바이오로직스", "삼바", "Samsung Biologics")),
    (("KR:KOSPI:005380",), ("현대차", "현대자동차", "Hyundai Motor", "现代汽车", "現代自動車")),
    (("KR:KOSPI:000270",), ("기아", "기아차", "Kia Corp", "Kia Motors")),
    (("KR:KOSPI:035420",), ("NAVER", "네이버")),
    (("KR:KOSPI:035720",), ("카카오", "Kakao")),
    (("KR:KOSPI:005490",), ("POSCO홀딩스", "포스코홀딩스", "POSCO Holdings")),
    (("KR:KOSPI:006400",), ("삼성SDI", "Samsung SDI")),
    (("KR:KOSPI:051910",), ("LG화학", "LG Chem")),
    (("KR:KOSPI:068270",), ("셀트리온", "Celltrion")),
    (("KR:KOSPI:012450",), ("한화에어로스페이스", "Hanwha Aerospace")),
    (("KR:KOSPI:329180",), ("HD현대중공업", "HD Hyundai Heavy")),
    (("KR:KOSPI:105560",), ("KB금융", "KB Financial")),
    # ── 미국 ──
    (("US:NASDAQ:NVDA",), ("NVIDIA", "엔비디아", "英伟达", "輝達", "エヌビディア")),
    (("US:NASDAQ:AAPL",), ("Apple", "애플", "苹果公司", "アップル")),
    (("US:NASDAQ:MSFT",), ("Microsoft", "마이크로소프트", "微软", "マイクロソフト")),
    (("US:NASDAQ:AMZN",), ("Amazon", "아마존", "亚马逊", "アマゾン")),
    (("US:NASDAQ:GOOGL", "US:NASDAQ:GOOG"), ("Alphabet", "Google", "구글", "알파벳", "谷歌", "グーグル")),
    (("US:NASDAQ:META",), ("Meta Platforms", "메타플랫폼스", "Facebook", "페이스북")),
    (("US:NASDAQ:TSLA",), ("Tesla", "테슬라", "特斯拉", "テスラ")),
    (("US:NASDAQ:AVGO",), ("Broadcom", "브로드컴", "博通")),
    (("US:NASDAQ:AMD",), ("AMD", "Advanced Micro Devices")),
    (("US:NASDAQ:INTC",), ("Intel", "인텔", "英特尔", "インテル")),
    (("US:NASDAQ:MU",), ("Micron", "마이크론", "美光")),
    (("US:NASDAQ:NFLX",), ("Netflix", "넷플릭스", "奈飞")),
    (("US:NASDAQ:PLTR",), ("Palantir", "팔란티어")),
    (("US:NASDAQ:ASML",), ("ASML", "阿斯麦")),
    (("US:NASDAQ:COIN",), ("Coinbase", "코인베이스")),
    (("US:NYSE:TSM",), ("TSMC", "台积电", "台積電")),
    (("US:NYSE:JPM",), ("JPMorgan", "JP Morgan", "JP모건", "摩根大通")),
    (("US:NYSE:GS",), ("Goldman Sachs", "골드만삭스", "高盛")),
    (("US:NYSE:BRK.B", "US:NYSE:BRK.A"), ("Berkshire Hathaway", "버크셔 해서웨이", "버크셔해서웨이", "伯克希尔")),
    (("US:NYSE:LLY",), ("Eli Lilly", "일라이 릴리", "일라이릴리", "礼来")),
    (("US:NYSE:ORCL",), ("Oracle", "오라클", "甲骨文")),
    (("US:NYSE:NVO",), ("Novo Nordisk", "노보 노디스크", "노보노디스크", "诺和诺德")),
    # ── 홍콩·중국 ──
    (("00700",), ("Tencent", "텐센트", "腾讯", "騰訊", "テンセント")),
    (("09988", "US:NYSE:BABA"), ("Alibaba", "알리바바", "阿里巴巴")),
    (("03690",), ("Meituan", "메이퇀", "美团", "美團")),
    (("01810",), ("Xiaomi", "샤오미", "小米")),
    (("01211", "002594"), ("BYD", "비야디", "比亚迪", "比亞迪")),
    (("09618", "US:NASDAQ:JD"), ("JD.com", "징둥", "京东", "京東")),
    (("09888", "US:NASDAQ:BIDU"), ("Baidu", "바이두", "百度")),
    (("00941",), ("China Mobile", "차이나모바일", "中国移动", "中國移動")),
    (("00005",), ("HSBC", "汇丰", "滙豐")),
    (("02318", "601318"), ("Ping An Insurance", "핑안보험", "中国平安", "中國平安")),
    (("01299",), ("AIA Group", "友邦保险", "友邦保險")),
    (("600519",), ("Kweichow Moutai", "구이저우마오타이", "마오타이", "贵州茅台", "茅台")),
    (("300750",), ("CATL", "닝더스다이", "宁德时代", "寧德時代")),
    # ── 일본 ──
    (("JP:7203",), ("Toyota", "도요타", "トヨタ", "丰田")),
    (("JP:6758",), ("Sony", "소니", "ソニー", "索尼")),
    (("JP:9984",), ("SoftBank Group", "소프트뱅크", "ソフトバンクグループ", "软银")),
    (("JP:8035",), ("Tokyo Electron", "도쿄일렉트론", "東京エレクトロン")),
    (("JP:7974",), ("Nintendo", "닌텐도", "任天堂")),
    (("JP:6861",), ("Keyence", "키엔스", "キーエンス")),
    (("JP:8306",), ("Mitsubishi UFJ", "MUFG", "미쓰비시UFJ", "三菱UFJ")),
)


def _code_keys(market: str, code: str) -> set[str]:
    """자산·관심종목 코드를 표의 열쇠 모양으로. 자산은 거래소 없이 "005930"·"NVDA"로 적힌다."""
    raw = code.strip().upper()
    if raw.startswith(("KR:", "US:")):
        return {raw}
    if market == "US" and raw:
        return {f"US:{exchange}:{raw}" for exchange in ("NASDAQ", "NYSE")}
    if not raw.isdigit():
        return set()
    if market == "KR":
        return {f"KR:{exchange}:{raw.zfill(6)}" for exchange in ("KOSPI", "KOSDAQ")}
    return {"CN": {raw.zfill(6)}, "HK": {raw.zfill(5)}, "JP": {f"JP:{raw}"}}.get(market, set())


def lookup(market: str, code: str, names: set[str]) -> tuple[str, ...]:
    """그 종목의 다른 언어 이름. 코드가 맞거나, 적힌 이름(정규화한 것)이 표의 이름과 같으면 그 묶음이다."""
    keys = _code_keys(market, code)
    for codes, aliases in GROUPS:
        if keys & set(codes) or names & {name.casefold() for name in aliases}:
            return aliases
    return ()
