# 기능 카탈로그

각 하위 디렉터리의 `feature.py`가 해당 기능의 단일 조립 지점이다. 여기에는
기능 키, 의존 기능, 텔레그램 명령어, 메뉴, 콜백, 스케줄과 데이터 파일이
선언된다. 실제 활성 목록은 `.env`의 `FEATURES_ENABLED`가 결정한다.

| 기능 키 | 역할 |
|---|---|
| `instruments` | 종목 데이터베이스와 일별 갱신 |
| `quant` | 시세·자금흐름·섹터 정량 데이터 |
| `news_prefilter` | 번역 전 로컬 사건 메모리·후보 점수화(Neurons 0) |
| `watchlist` | 관심종목 추가·삭제·목록 |
| `news` | Futu·Sina·Google News(글로벌·미국·한국)·RSS 수집과 다이제스트 |
| `market_sentiment` | 날짜별 시장 감성 백필·집계·차트, Polymarket 컨센서스 스냅숏 |
| `research` | 뉴스·정량 데이터 기반 시장 리서치(중화권·미국·한국) |
| `briefing` | 모닝·마감 브리핑 |
| `signal_scoring` | 종목별 뉴스 감성 뷰 |
| `system_admin` | 도움말·기능 상태·소스 상태 |
| `web_admin` | 인증이 적용된 내장 관리 웹 대시보드 |

기능을 비활성화할 때는 `FEATURES_ENABLED`에서 키를 제거한다. 의존 기능이
빠지면 시작 단계에서 오류가 발생하므로 불완전한 조합으로 실행되지 않는다.
비활성화는 데이터 파일을 삭제하지 않는다.

텔레그램 `/system features`에서 같은 카탈로그의 현재 활성 상태를 확인할 수
있다. 새 기능은 `FeatureSpec`을 선언하고 `features/__init__.py`의
`ALL_FEATURES`에 추가한다.
