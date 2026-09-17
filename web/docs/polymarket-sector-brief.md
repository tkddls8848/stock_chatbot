# 폴리마켓 섹터 컨센서스 줄글과 주기 간 이동 추적

> 이 문서는 계획서다. 항목이 끝나면 지우고, 다 끝나면 파일째 지운다.
> 완료 기록은 git 이력이 맡는다.
>
> `web/docs/polymarket-dashboard.md`(현재 스냅숏 대시보드)를 **전제로** 한다. 그
> 문서가 먼저 지워져도 이 계획은 남아야 해서 파일을 나눴다.

## 1. 무엇을 만드나

지금 `/polymarket` 화면은 **숫자만** 보여 준다. 열린 event 22,000건의 확률·거래량·
순위가 표와 막대로 있고, 읽는 사람이 스스로 해석해야 한다.

여기에 **경제·금융과 지정학에 한정한 줄글 컨센서스**를 얹는다. 그리고 한 주기의
스냅숏을 요약하는 데서 그치지 않고, **직전 줄글 시점 대비 확률이 어디로
움직였는지**를 계산해 "사람들이 어디로 향하고 있는가"를 쓴다.

핵심 원칙 하나로 요약된다.

> **이동은 코드가 계산하고 LLM은 서술만 한다.**

방향·크기·역전 여부를 모델에게 묻지 않는다. 산수로 구한 값을 넘기고 문장만
받는다. 철수한 폴리마켓 컨센서스가 방향을 allowlist로만 정했던 것과 같은
이유다 — 모델이 방향을 지어내면 그 문장은 검증할 수 없고, 검증할 수 없으면
틀렸을 때 알아챌 방법이 없다.

## 2. 대상 선정

### 2-1. 감시 태그

```text
ECON = economy, finance, fed, fed-rates, interest-rates,
       inflation, equities, stocks, pre-market, macro-indicators
GEO  = geopolitics, foreign-policy
```

`web/polymarket/dashboard/taxonomy.py`에 둔다. 태그 지식은 이미 그 모듈이
소유하고 있고, 나누면 두 곳을 같이 고쳐야 하는 날이 온다.

### 2-2. `category`가 아니라 `tags`로 고른다

`classify()`가 매기는 `category`를 쓰지 않는다. 둘 이상 분야에 걸린 event를
`other`로 보내기 때문이다(`ambiguous:economy_finance,geopolitics`). 그런데 그
경계 event가 **정확히 이 기능이 보려는 것**이다 — 지정학이 경제를 움직이는
자리가 거기다.

`tags`는 compact manifest에 그대로 있으므로 `current.json`만 읽으면 된다.
`category_reason`을 위해 detail을 seek할 필요가 없고, `classify()`도 건드리지
않아 대시보드의 다른 분야 숫자가 움직이지 않는다.

실측이 이 선택을 뒷받침한다. `finance` 태그는 387건인데 `economy_finance`
카테고리는 249건이다. 차이는 전부 ambiguous로 흘러간 것들이다.

### 2-3. 섹터 (서로 겹치지 않는다)

event 하나는 정확히 한 섹터에만 들어간다.

| 섹터 | 조건 |
|---|---|
| **복합** | `tags ∩ ECON ≠ ∅` **그리고** `tags ∩ GEO ≠ ∅` |
| **경제·금융** | `tags ∩ ECON ≠ ∅` (복합 아님) |
| **지정학** | `tags ∩ GEO ≠ ∅` (복합 아님) |

`iran`·`russia`·`china`·`middle-east`는 감시 태그가 **아니다.** 국가 태그는
축구 리그(`ukraine-premier-liha`, `chinese-super-league`)와 선거까지 끌고 들어와
노이즈가 크다. 지정학을 뜻하는 태그만 본다.

### 2-4. 그룹

| 섹터 | 그룹 | 태그 | 2026-09-01 실측 |
|---|---|---|---|
| 복합 | 복합 | ECON ∩ GEO 동시 | **8** |
| 경제·금융 | 주식·시장 | `equities`, `stocks`, `pre-market` | **349** |
| | 거시·통화 | `macro-indicators`, `fed`, `fed-rates`, `interest-rates`, `inflation` | **89** |
| | 기타 경제·금융 | `economy`, `finance` | **304** |
| 지정학 | 지정학 | `geopolitics`, `foreign-policy` | **405** |

실측(2026-09-01 22:58, 총 1,155건). 호출당 62 Neurons, 하루 7주기 기준 약
1,700 Neurons(무료 한도의 17%). **복합이 8건**인 것은 폴리마켓이 지정학과
경제를 한 event에 같이 태깅하는 일이 드물다는 뜻이다 — 지정학 자체는 405건이라
그 축은 지정학 그룹이 담는다.

**복합은 표본 기준을 훨씬 낮게 둔다**(`POLYMARKET_BRIEF_MIN_EVENTS_BY_GROUP`).
두 목록의 태그를 동시에 달아야 들어오는 구조라 얇을 수밖에 없는데, 지정학을
감시 목록에 넣은 이유가 바로 이 교차 지점이다. 얇다고 비워 두면 그 이유가
화면에서 사라진다. 대신 프롬프트가 `event_count`를 보고 10건 미만이면 분야
전체를 단정하지 않고 그 몇 건만 서술하게 한다 — 표본이 아니라 우연을 서술하는
문장을 막는 것은 임계값이 아니라 이 지시다.

한 event가 그룹 태그를 여럿 달면 **고정 우선순위 순서로 첫 일치**에 넣는다
(위 표의 순서). 매번 같은 결과가 나와야 "지난번과 뭐가 달라졌나"를 비교할 수
있다.

**금리와 물가를 따로 두지 않는 이유**: `fed`·`fed-rates`·`interest-rates`·
`inflation`이 전부 39건 미만이다(2026-09-01, 태그 빈도 목록이 39에서 끊긴 지점
아래). 각각 그룹으로 두면 매 주기 "표본 부족"만 뜬다. 가장 궁금한 주제인데
폴리마켓이 그렇게 태깅하지 않는다 — 거시·통화 하나로 합친다. 나중에 이 태그들이
커지면 그때 쪼갠다.

## 3. 이동 추적

### 3-1. 무엇과 비교하나

**직전 generation이 아니라 직전 줄글 시점과 비교한다.**

generation을 두 벌 남기므로 직전 manifest를 읽는 방법도 있지만 두 가지가
걸린다. 13 MiB manifest를 두 개 파싱해야 하고(1GB 인스턴스), 야간에 한 주기를
거르면 "직전 generation"과 "직전 줄글"이 어긋난다.

대신 `sector_brief.json`이 **자기가 쓴 시점의 event별 확률을 함께 저장**하고
다음 실행이 그것과 비교한다. 1,000여 건이면 수십 KB고, 비교 기준이 항상 직전
줄글이라 야간 공백과도 일관된다. generation 보존 정책(`KEEP_GENERATIONS`)에
의존하지 않는다.

### 3-2. 재는 것

event는 `id`로 조인한다. 양쪽에 다 있는 것만 이동을 계산한다.

| 신호 | 정의 | 대상 |
|---|---|---|
| 확률 변화 | 같은 event, 같은 기준 결과의 확률 차(pp) | binary |
| **우세 역전** | `leader`가 바뀐 event. 가장 강한 신호 | 전부 |
| 우세 확률 변화 | 우세 후보가 그대로일 때의 확률 차(pp) | 다지선다 |
| 그룹 순이동(`net_pp`) | **binary event의 거래량 가중 평균 변화** | binary만 |
| 신규·소멸 | 이번에 처음 보이거나 사라진 event 수 | 전부 |

`net_pp`에 다지선다를 섞지 않는다. 부호 있는 확률 차를 복원할 수 없어서(3-3),
넣으면 방향 없는 값이 방향 있는 평균을 오염시킨다. 다지선다는 역전 건수와
우세 확률 변화로만 기여하고, 그룹에 binary가 없으면 `net_pp`는 `null`이다.

### 3-3. 부호 문제 — 반드시 짚고 간다

`leader_probability`는 **항상 우세한 쪽의 값**이다. 0.60 → 0.70이 어느 방향인지
그 값만으로는 모른다.

- **binary**: `leader` 라벨로 기준 결과 확률을 복원한다. `leader == "No"`면
  `P(Yes) = 1 - leader_probability`. 복원한 `P(Yes)`끼리 비교한다.
- **다지선다(`exclusive_multi` 등)**: compact에 분포 전체가 없어 복원할 수
  없다. **우세 후보가 그대로일 때의 확률 변화**와 **우세 후보가 바뀐 사실**만
  쓴다. 억지로 하나의 숫자로 합치지 않는다.

이 구분을 지우고 `leader_probability`를 그냥 빼면 우세가 뒤집힌 event에서 부호가
거꾸로 나온다. **지운 채로 승격하지 않는다.**

### 3-4. 잡음 차단

3시간은 짧고 대부분의 event는 거의 안 움직인다. `POLYMARKET_BRIEF_MOVE_THRESHOLD_PP`
(초기 3.0) 미만은 "변화 없음"으로 묶어 개별 서술 대상에서 뺀다. 임계값이 없으면
매 주기 그럴듯한 헛소리가 나온다 — 모델은 0.4pp 움직임에도 서사를 붙인다.

집계(순이동)에는 임계값 미만도 그대로 포함한다. **빼는 것은 이름을 부르는
자리뿐이다.**

### 3-5. 첫 실행

비교 대상이 없다. 그 실행은 `baseline` 상태로 표시하고 이동 문장을 쓰지 않는다.
스냅숏 요약만 쓴다.

## 4. LLM

### 4-1. 호출 구조

그룹당 한 번 + 종합 한 번 = **주기당 6호출**.

- **그룹 호출**: 집계 + 이동 + 이름 있는 상위 event를 받아 그 그룹의 단락을 쓴다.
- **종합 호출**: 원문 event를 **넣지 않는다.** 그룹별로 계산된 이동값(순이동 pp,
  역전 건수, 거래량, 표본 수)과 라벨만 넘긴다. 지어낼 재료를 주지 않는 것이
  목적이고, "지정학이 경제의 변수"라는 교차 읽기가 들어갈 자리가 여기다.

### 4-2. 프롬프트에 들어가는 것

그룹마다 두 덩어리다.

- **집계 — 전부 반영**: event 수, `data_status` 분포, 24시간 거래량·유동성 합,
  확률 분포(중앙값, 0.9 이상 건수, 0.4~0.6 경합 건수), 이동 집계
- **이름 — 거래량 상위 `POLYMARKET_BRIEF_NAMED_LIMIT`건**(초기 120): 제목, 우세
  결과, 확률, 이동 pp, 역전 여부, 거래량, 종료일

대상이 1,000건이 넘어 상한이 실제로 걸린다. 화면과 문장은 **"집계는 전부, 이름은
거래량 상위 N건"**임을 명시한다. 전부 이름을 부르는 것처럼 쓰지 않는다.

### 4-3. 응답 계약

**평문 단락 하나를 받는다. JSON 봉투를 쓰지 않는다.**

처음에는 `{"paragraph": "..."}`로 받으려 했으나 실측(2026-09-01)에서 모델이 네
번 다 봉투를 무시하고 평문만 돌려줬다. 출력이 문자열 하나뿐이라 봉투가 검증에
보태는 것이 없고 출력 토큰만 더 쓴다. 검증은 코드가 직접 한다.

- 빈 문자열·공백만 → 실패
- 60자 미만 / 1,200자 초과 → 실패
- `{`나 `[`로 시작(봉투를 다시 만든 응답) → 실패
- 상위 event 제목을 통째로 되풀이 → 실패
- 빈 `<think></think>` 블록과 코드 블록 울타리는 벗겨서 본다
- `finish_reason=length` → `backends.py`가 이미 `truncated`로 실패시키고
  **재시도하지 않는다.** 그래서 프롬프트에 단락 길이를 못박고
  `POLYMARKET_BRIEF_NUM_PREDICT`에 여유를 둔다.

### 4-4. 확률의 방향은 코드가 정한다

`leader_probability`를 그대로 넘기고 방향을 프롬프트로 설명하면 **모델이 셋 중
둘꼴로 뒤집어 쓴다**(실측 2026-09-01). 제목이 질문형이라 모델이 거기 앵커링해
"제재 완화 가능성 74%"라고 쓰는데, 74%는 완화되지 **않을** 확률이다. 방향을
명시한 힌트 필드를 따로 넘겨도 고쳐지지 않았다.

그래서 숫자를 모델이 읽는 방향에 맞춰 보낸다. binary는 `title_probability`
(제목이 사실로 판명될 확률, `leader == "No"`면 `1 - p`) 하나만 넘기고 `leader`는
넣지 않는다 — 같이 보내면 둘을 섞어 쓴다. 다지선다는 제목이 참·거짓 명제가
아니므로 `leader`와 `leader_probability`를 그대로 넘긴다.

이 정규화가 3-3이 요구한 부호 안정 값과 같다. 이동 계산도 이 값을 쓴다.

프롬프트 파일은 `web/prompts/polymarket_brief_ko.txt`(그룹)와
`web/prompts/polymarket_brief_overview_ko.txt`(종합) 둘이다.

**모델에게 URL을 받아 적게 하지 않는다.** event 링크가 필요하면 `slug`로 파이썬
쪽에서 만든다.

## 5. 산출물

`data/webpub/polymarket/sector_brief.json`. `web/core/storage.py`의 원자적 쓰기만
쓴다.

```json
{
  "schema_version": 1,
  "generation_id": "20260901T…",
  "written_at": "2026-09-01T09:00:00+09:00",
  "state": "ok",
  "groups": [
    {
      "key": "composite",
      "label": "복합(경제·지정학)",
      "sector": "composite",
      "status": "ok",
      "event_count": 37,
      "named_count": 37,
      "volume24hr": 1234567.0,
      "movement": {
        "net_pp": 1.8,
        "moved_count": 4,
        "flipped_count": 1,
        "new_count": 2,
        "gone_count": 1
      },
      "paragraph": "…"
    }
  ],
  "overview": {"status": "ok", "paragraph": "…"},
  "previous": {"<event_id>": {"p": 0.61, "leader": "Yes"}},
  "usage": {"calls": 6, "neurons": 187.4}
}
```

`status`는 `ok` · `insufficient_sample` · `failed` 셋. `state`는 실행 단위로
`ok` · `baseline` · `skipped_quiet_hours`.

`previous`는 다음 실행의 비교 기준이다. **화면에 내보내지 않는다** — API 응답에서
제외한다.

## 6. 실행과 배선

독립 one-shot `web/polymarket/sector_brief.py`. 봇 프로세스와 무관하다.

`infra/systemd/stock-chatbot-polymarket-refresh.service`의 `[Unit]`에 한 줄을 더한다.

```ini
OnSuccess=stock-chatbot-polymarket-brief.service
```

timer는 늘리지 않는다. refresh가 **성공했을 때만** 줄글이 돈다 — 순회가 실패한
주기에는 새로 요약할 것도 없다. 순차 실행이라 메모리가 겹치지 않는다.

**야간에는 줄글만 멈춘다.** `POLYMARKET_BRIEF_QUIET_HOURS`(초기 `{3}`)에 해당하는
시각에는 LLM을 부르지 않고 `state: "skipped_quiet_hours"`로 종료한다. refresh는
03시에도 계속 돌아 확률 숫자는 미장 마감 직전 구간을 놓치지 않는다.

시각 판단은 `web/core/clock.py`의 `now()`만 쓴다.

**왜 refresh 안에 넣지 않나**: LLM 실패가 generation 승격을 막으면 안 된다.
지금 refresh는 실패하면 `current.json`을 안 바꾸는데, 거기에 LLM을 넣으면
Cloudflare가 죽은 날 확률 숫자까지 멈춘다.

## 7. 실패 처리

- **그룹 단위로 격리한다.** 한 그룹의 호출이 실패해도 나머지는 쓴다. 실패한
  그룹은 `status: "failed"`.
- **전부 실패하면 아무것도 쓰지 않는다.** 직전 `sector_brief.json`이 last-good으로
  남는다. 부분 성공일 때만 새로 쓴다.
- 새로 쓸 때 실패한 그룹의 `previous` 항목은 **직전 값을 그대로 이어받는다.**
  덮어쓰면 다음 주기의 이동이 6시간치가 되면서 3시간치인 척한다.
- `current.json`이 없으면 조용히 종료한다.
- Cloudflare 할당량 소진(`quota_exhausted`)은 재시도하지 않고 그 실행을 끝낸다.
- 브리프가 없거나 낡아도 화면의 확률·순위·탐색기는 그대로 뜬다.

## 8. 비용

| 항목 | 값 |
|---|---|
| 호출 | 6/주기 × 7주기 = **42/일** |
| 입력 | 그룹당 ~5,000 토큰(이름 120건 기준), 종합은 ~500 |
| 추정 Neurons | 하루 1,500~2,500 (무료 한도 10,000의 15~25%) |

**추정하지 않고 실측한다.** Cloudflare가 응답에 담아 주는 Neurons를
`usage.neurons`에 누적한다. 첫 주 뒤 실제 값으로 이 표를 갱신한다.

넘치면 손대는 순서: ① 이름 상한(120) 축소 ② 종합 호출 제거 ③ 야간 정지 구간
확대 ④ 주기 축소. **모델을 바꾸거나 그룹을 지우는 것은 마지막이다.**

## 9. 화면

`GET /api/polymarket/sector-brief` (`previous` 제외). `/polymarket` 상단에 섹션을
붙인다.

- 그룹마다 라벨 · 표본 수 · 순이동 pp · 단락.
- `insufficient_sample`은 문장 자리에 그렇게 적는다. 빈 칸으로 두지 않는다.
- **기준 시각을 적는다.** `generation_id`가 현재와 다르면 그 글은 이전 스냅숏
  기준이라는 뜻이고, 정상 상태가 아니다(줄글이 실패했거나 야간 정지 중).
  `skipped_quiet_hours`와 `failed`를 화면에서 구분한다.
- 값은 `esc()`를 거친다. 단락은 모델이 쓴 문자열이다.
- 라이트 전용, 빨강이 긍정·파랑이 부정 규약을 따른다.

## 10. 설정

`web/core/config.py`의 리터럴 상수. 운영자가 조정하는 값이 아니므로 env가 아니다.

```text
POLYMARKET_BRIEF_FILE
POLYMARKET_BRIEF_NAMED_LIMIT        120
POLYMARKET_BRIEF_MIN_EVENTS           5     표본 미달 기준(실측 뒤 10→5)
POLYMARKET_BRIEF_MIN_EVENTS_BY_GROUP {"composite": 2}  복합만 예외
POLYMARKET_BRIEF_MOVE_THRESHOLD_PP    3.0
POLYMARKET_BRIEF_QUIET_HOURS         {3}
POLYMARKET_BRIEF_PROMPT_FILE
POLYMARKET_BRIEF_OVERVIEW_PROMPT_FILE
POLYMARKET_BRIEF_TIMEOUT
POLYMARKET_BRIEF_NUM_PREDICT
```

## 11. 테스트

LLM은 mock한다. 실제 호출은 opt-in 스모크에만 둔다.

- 섹터 배정이 겹치지 않는다 — 세 섹터가 한 event를 두 번 담지 않는다.
- 그룹 배정이 결정적이다 — 태그를 여럿 단 event가 우선순위 순서대로 간다.
- 감시 태그가 없는 event는 어느 섹터에도 안 들어간다.
- 집계는 전부 세고 이름은 상위 N만 — 상한이 걸릴 때 집계 수와 이름 수가 다르다.
- 표본 미달 그룹은 LLM을 부르지 않고 `insufficient_sample`.
- **이동 부호**: `leader`가 뒤집힌 binary event의 부호가 옳다.
- 다지선다는 역전 사실만 쓰고 확률 차를 만들어내지 않는다.
- 임계값 미만은 이름 목록에서 빠지되 순이동 집계에는 들어간다.
- 첫 실행은 `baseline`, 이동 문장 없음.
- 야간 시각에는 LLM을 부르지 않고 `skipped_quiet_hours`.
- 부분 실패 시 나머지는 쓰이고 실패 그룹의 `previous`는 이어받는다.
- 전부 실패 시 파일을 건드리지 않는다.
- 응답 검증 3종(빈 문자열 · 반향 · 길이 초과)이 실패로 처리된다.
- webpub 라우트가 `previous`를 내보내지 않는다.

## 12. 단계

각 단계는 그 지점에서 멈춰도 봇과 공개 웹을 깨뜨리지 않아야 한다.

1. **선정과 집계** — 태그·섹터·그룹 배정과 집계까지. LLM 없이 JSON을 굽고
   그룹별 실제 표본 수를 실측한다. 여기서 표본이 예상과 다르면 2-4를 고친다.
2. **이동 계산** — `previous` 저장과 diff. 두 주기를 돌려 부호와 역전을 실측으로
   확인한다. 아직 LLM 없음.
3. **LLM 단락** — 그룹 호출과 검증. Neurons 실측.
4. **종합 단락** — 교차 읽기.
5. **화면과 API**.
6. **배선** — `OnSuccess=`, 야간 정지, server-ops 절차 추가.

## 13. 판정

1주일 뒤 이것으로 판단한다. 하나라도 미달이면 승격을 멈춘다.

| 항목 | 기준 |
|---|---|
| Neurons | 하루 2,500 이하이고 다른 기능의 실패를 늘리지 않았다 |
| 표본 | 다섯 그룹 중 넷 이상이 대부분의 주기에서 `ok` |
| 이동의 실질 | 역전·순이동이 실제 사건과 대응한다. 매 주기 비슷한 문장만 나오면 실패 |
| 부호 | 우세가 뒤집힌 event에서 방향이 옳다 |
| 실패 격리 | 그룹 하나의 실패가 나머지를 막지 않았다 |

## 14. 안 하는 것

- 다른 카테고리(스포츠·크립토·정치)로 넓히지 않는다.
- 줄글의 이력·시계열을 만들지 않는다. 비교는 항상 직전 한 벌이다.
- 웹에서 재생성을 트리거하지 않는다.
- 텔레그램에 노출하지 않는다. 철수한 `/polymarket` 명령을 되살리지 않는다.
- 모델에게 방향·인과를 묻지 않는다.
- 원자재 그룹을 만들지 않는다. `oil` 43건이 유일한 원자재 태그이고 `gold`·`gas`·
  `commodities`는 관측되지 않았다. 시장이 생기면 그때 태그를 추가한다.
# 섹터 전체 요약과 쇼츠 연결 (2026-09-06)

편집 검증 보강: 정상 집계가 있는 응답은 문장별 해라체 평서문(~있다/~이룬다), 금지어, 첫 문장의
전망 해설 단서와 소수 표본 한계를 검사합니다. 주제 소개만 있는 문장은 반려합니다.
검증 실패 시 원본 데이터와 실패 사유를 전달하여 1회 교정 요청합니다. 재실패하면
기존 분야별 실패/last-good 정책을 따릅니다. 이전 부적합 문장이 stale로 남을 수
있으므로 배포 후 status와 stale을 함께 확인해야 합니다. 쇼츠는 stale 분야를 제외합니다.
해설 단서 검사는 보수적인 문자열 규칙이며 의미나 모든 수치의 정확성을 보증하지는 않습니다.


브리프 첫 문장은 `전체적으로`로 시작하는 숫자 없는 정성적 전체 요약입니다.
이어지는 문장에 대표 질문과 참여자들의 전망을 설명합니다. 집계상의 선두 우세가
서로 다른 질문에서 나타났다고 해서 섹터 전체의 경제적 방향이 같다고 해석하지 않습니다.
상위 일부 질문만 제공되므로 주제에 대한 해석 범위도 상위 질문으로 한정합니다.

`groups[].overview`에는 첫 문장이 별도로 저장됩니다. `paragraph`에도 그대로 포함되어
기존 웹 화면은 변경 없이 전체 요약부터 보여줍니다. 쇼츠는 overview를 먼저 읽고
남은 분량에서 개별 사례를 고릅니다. 실패 시 이전 overview와 paragraph를 함께
이어받으며 stale로 표시합니다. 기존 API에서는 제한적인 집계 요약으로 호환합니다.

서버에 변경된 `web/llm/polymarket_brief.py`, `web/polymarket/sector_brief.py`,
`web/prompts/polymarket_brief_ko.txt`를 배포한 뒤
`sudo systemctl restart stock-chatbot-polymarket-brief.service`로 브리프를 다시 만듭니다.
새 overview는 다음 API 응답부터 제공됩니다. 코드 변경만으로 기존 영상이나
이미 저장된 시나리오가 자동 수정되지는 않습니다. 쇼츠는 새 시나리오로 생성합니다.
