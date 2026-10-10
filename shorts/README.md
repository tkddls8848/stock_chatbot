# Polymarket Daily Shorts

기존 공개 웹 앱이 수집한 개별 이벤트에서 거래가 활발하고 금융시장 관련성이 높은 이슈를 골라 한국어 영상을 만듭니다.
홈페이지의 분야 요약문은 영상 원재료로 사용하지 않습니다.

- `/api/forecast/summary`: 수집 세대와 신선도 검증
- `/api/forecast/events?status=ok&sort=volume24hr&order=desc&page_size=100&page=N`: 정상 상태 이벤트 전체 목록
- `/api/forecast/trending`: 관측된 확률 변동 보조 지표
- `/api/forecast/events/{id}`: 최종 선정 이벤트의 실제 질문·개별 베팅 가격·설명

공개 HTTP API만 읽습니다. 페이지·상세의 generation이 다르거나 원자료가 지연되면 제작을 중단합니다.
정상 이벤트 10,000개(100페이지)를 초과하면 일부 목록으로 제작하지 않고 조회 예산 오류를 냅니다.

## 결과물

한 번 실행하면 공유 저장소 `storage/shorts/YYYY-MM-DD/` 아래에 다음 파일이 생깁니다(`STORAGE_DIR`로 바꿀 수 있고, 봇·웹과 같은 값을 씁니다).

- `polymarket-YYYY-MM-DD.mp4`: 1080×1920, H.264/AAC 세로 영상
- `selection.json`: 전체 조회 수·제외 집계·후보 점수·최종 선정 이유·실제 API 및 모델 호출 수
- `source.json`: 선정 이벤트의 원문 설명·개별 베팅 질문과 가격·관련 뉴스 제목·원문 링크
- `scenario.json`: 사용한 generation, 장면, 전체 내레이션과 실제 길이
- `review.md`: 검수용 한 장 — 게시 제목·설명·태그와 장면별 멘트·화면 문구
  게시 제목은 `yyyy-mm-dd 시장 컨센서스`(예: `2026-09-27 시장 컨센서스`)로 통일하고, 그날 다룬 이슈는 설명에 적습니다. 자연어 편집으로도 제목은 바꾸지 않습니다.
- `review.json`: 검수 상태·영상 정보·제목/설명/태그

영상은 인트로, 선정한 개별 이슈 최대 5개, 고지문 순서입니다. 인트로와 마무리는 그날 내용과 무관한 고정 화면입니다("오늘의 집단 예측 컨센서스 요약 / 지금 시작합니다", "자세한 내용은 nunchi.live에서 확인하세요", 2026-10-07 운영자 결정). 두 화면은 자막을 띄우지 않고 머리말을 안전 영역 가운데에 크게 세웁니다(2026-10-10 운영자 결정, 멘트는 그대로). 약한 분야를 억지로 채우지 않습니다. 완성과 음성 보존이 길이보다 우선입니다. `max_duration_seconds`는 150초(2분 30초)의 허용 상한이며, 초과하면 경고 로그를 남기되 자동 배속이나 생성 중단은 하지 않습니다(완성·음성 보존 우선). 기본 발화 속도는 +0%입니다. Blender와 HyperFrames 모두 전체 원고를 한 번에 합성한 단일 음성을 사용하고 끝에 0.6초 여유를 둡니다. 화면은 edge-tts가 보고한 단어별 발화 시각에 맞춰 장면별로 전환하며, 장면이 바뀌는 자리에는 0.9~1.3초의 호흡을 둡니다(자리마다 다릅니다). 플랫폼의 Shorts 분류 조건과 제작 목표는 별개이므로 긴 완성본은 게시 전 확인합니다.

## 시장상황 보고서 롱폼

영어판 쇼츠 대신, 봇이 발행한 시장상황 보고서 한 편을 가로(1920×1080) 영상으로 만듭니다(`longform.py`·`longform_render.py`, 2026-10-10 운영자 결정).

```bash
.venv/bin/python -m polymarket_shorts.cli --longform US            # 미국 최신 보고서
.venv/bin/python -m polymarket_shorts.cli --longform KR --force    # 이미 만든 보고서도 다시
.venv/bin/python -m polymarket_shorts.cli --longform US --report-id "report:US:2026-10-10T08:00:00+09:00"
```

- 원재료: 공개 웹 `/api/search`의 그 시장 최신 보고서(`kind: report`), 그 보고서 구간 안에 발행된 공개 기사 최대 7건, `/api/market`의 최근 14일 일일 감성.
- 순서(운영자 수정 대사 2026-10-10): 시작(고정 화면·자막 없음) → 목차 → 최근 14일 뉴스 감성 막대 → 주요 기사(제목 + 매체·원제 한 줄 요약) → 시장 분석(보고서 한 장면, 주제는 화면에·내용은 자막으로) → 마무리(고정 화면·자막 없음).
- 쇼츠 원고와 같은 모델(`editor_model`)을 한 번 부릅니다. 보고서를 2~3문단의 분석 원고로 줄이고 기사마다 원제에만 있는 정보를 한 문장으로 씁니다. 보고서에 없는 숫자, 보고서 문장 옮겨 적기, 같은 말 되풀이는 검증에서 막고, 걸리면 사유를 붙여 한 번 더 묻습니다. **길이를 채우려고 늘리지 않습니다** — 재료가 적으면 영상이 짧아집니다.
- 산출물: `storage/shorts/longform/<날짜>/<시장>-<HHMM>/` — `report-<시장>-<날짜>-<HHMM>.mp4`·`.timeline.json`(화면 전환·자막), `scenario.json`(원고), `source.json`(보고서·기사·감성 원자료와 모델 원고), `narration.mp3`, `words.jsonl`, `result.json`. 쇼츠 제작일 폴더와 같이 2주 보관합니다.
- 검토·YouTube 업로드·예약 제작·텔레그램 운영에는 아직 묶여 있지 않습니다.

## 설치

Ubuntu에서는 Blender 5.2.1 LTS·FFmpeg·한글 폰트가 필요합니다. 공유 호스트 설치 스크립트
`infra/scripts/install-shared-host.sh`가 [공식 배포본](https://download.blender.org/release/Blender5.2/)과
공식 SHA-256 파일을 받아 대조한 뒤 `/opt/blender-5.2.1`에 설치하고
`/usr/local/bin/blender`를 연결합니다. 같은 버전이 있으면 다운로드를 생략합니다.
FFmpeg는 TTS PCM 편집·입력 클립 검증에, ffprobe는 음성 길이 확인에 계속 필요합니다.

```bash
cd ~/stock_chatbot/shorts
sudo apt-get update
sudo apt-get install -y ffmpeg fonts-noto-cjk python3-venv
python3 -m venv .venv
.venv/bin/pip install -e .
# 저장소 루트의 .env.example을 참고해 기존 ../.env에 키를 추가합니다.
```

Windows PowerShell에서는 다음처럼 준비합니다.

```powershell
cd C:\Users\PSI\orca\stock_chatbot\shorts
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
winget install --id Gyan.FFmpeg -e
winget install --id BlenderFoundation.Blender -e
# 저장소 루트의 .env.example을 참고해 기존 ../.env에 키를 추가합니다.
```

Blender는 PATH 또는 `C:\Program Files\Blender Foundation\Blender *\blender.exe`에서 찾습니다.
찾지 못하면 명확한 렌더 오류로 중단합니다.
FFmpeg는 WinGet 설치 경로도 자동 탐색합니다.

로컬 영상 생성 테스트:

```bash
.venv/bin/python -m polymarket_shorts.cli
```

같은 한국 날짜에 다시 실행하면 `already_produced`로 끝납니다. 입력이나 디자인을 바꾸고 다시 만들 때만 `--force`를 사용합니다.

```bash
.venv/bin/python -m polymarket_shorts.cli --force
```

## 자연어 검수와 편집

로컬 PowerShell에서 영상 생성 → 원고·영상 확인 → 자연어 수정 → 재렌더 → 검수 완료로 진행합니다.
수정본과 검수 기록은 로컬에 저장합니다. 필요한 외부 설정은 이슈 선별·원고 작성·자연어 편집용 Cloudflare 계정 ID와
Workers AI API 토큰입니다. 저장소 루트 `.env.example`을 참고해 루트 `.env`에 채웁니다.
기존 `.env`는 덮어쓰지 말고 필요한 키만 추가하세요.

```dotenv
CLOUDFLARE_ACCOUNT_ID=계정_ID
CLOUDFLARE_API_TOKEN=Workers_AI_API_토큰
```

`.env`에는 비밀값과 `STORAGE_DIR`만 둡니다. 모델·음성·공개 범위·자동 승인 같은 조정값은
`src/polymarket_shorts/config.py`의 `Settings` 기본값입니다 — 바꾸면 git에 남습니다.
원고 모델은 `editor_model=@cf/deepseek-ai/deepseek-v4-flash-0731`, 추론 단계는 `editor_reasoning_effort=none`으로
끕니다(채팅 템플릿 인자로 끈다 — reasoning_effort "none"은 무시된다).

이슈 선정·원고·자연어 편집은 이 모델 하나가 맡습니다. 2026-09-28 같은 입력으로 6개 모델
(qwen3-30b·qwen3.8-27b·mistral-small-3.1·gpt-oss-20b·glm-4.7-flash·deepseek-v4-flash)을
비교해 한국어 어순·인명 표기·질문의 뜻을 모두 지킨 deepseek-v4-flash를 골랐습니다. 단가는 입력
100만 토큰당 $0.44·출력 $1.32로, 하루 한 편이면 한 달 약 $0.2~0.4입니다. 코드 기본값은 여전히
qwen3-30b라 `.env`에 적어야 바뀝니다. 생각 단계를 켜 두면 생각이 토큰 상한을 먹어 응답이 잘립니다.

### 개별 베팅에서 영상까지

1. 수집된 정상 이벤트 목록을 하루 제작 시 한 번 페이지 순회합니다. 수천 건에 AI를 호출하지 않습니다.
2. 경제·지정학, 거시·통화, 주식·시장, 지정학, 기타 경제·금융으로 태그를 분류합니다.
   24시간 거래량 2,000달러·유동성 1,000달러 이상, 종료 예정일이 지나지 않은 이벤트를 대상으로 합니다.
   분야 안에서 거래량 로그 점수 50%, 유동성 20%, 관측된 가격 변동 20%, 종료일까지의 거리 10%로 정렬합니다.
   같은 주제의 날짜·가격 변형은 후보 두 개까지 허용하고, 분야별 최대 10개(전체 최대 50개)를 남깁니다.
   최근 `repeat_days`(7)일 안에 영상으로 다룬 이벤트와 같은 주제(날짜·숫자만 다른 변형 포함)는 후보에서 뺍니다.
   2026-09-23~27 네 편이 모두 같은 연준·호르무즈 질문이었던 반복을 막습니다. 뺀 목록은 `selection.json`의 `recently_featured`에 남습니다.
3. `editor_model`을 한 번 호출해 시장 관련성과 시의성을 평가합니다. 각 0~3점 중 모두 2점 이상인 이슈를
   분야·주제당 최대 하나, 전체 최대 `max_groups`개(5개) 선정합니다. 기준을 넘는 후보가 둘 이상이면
   최소 2개를 고르라고 프롬프트에 적습니다(검증으로 강제하지 않아 호출이 늘지 않습니다). 중복 제안은 제외 기록을 남깁니다.
4. 선정한 이슈만 상세와 Google News RSS를 조회합니다. 각각 최대 5회입니다.
   자료 시점 이전 7일 이내 뉴스 제목 최대 3개만 보조 자료로 쓰며 기사 본문을 읽었다고 주장하지 않습니다.
   **우리가 모은 시장 뉴스도 붙입니다.** 공개 웹 `/api/search`(봇이 굽는 `news.json`)에서 최근 사흘치 기사를 한 번
   읽고(`client.market_news`, 최대 20쪽), 선정 모델이 이슈마다 낸 한국어 주체어(`keywords`: 유가·원유, 연준·FOMC)가
   제목·원문에 있는 기사만 이슈당 최대 6건 후보로 둡니다(`timely.related_news`). 주체어가 제목 앞 마디에 있는 기사,
   최근 기사, 같은 흐름을 다룬 기사가 많은(`coverage`) 기사 순입니다. 읽지 못하면 이 단계만 건너뜁니다.
5. 두 번째 모델 호출로 질문·선택지를 한국어로 옮기고 시장 연결점과 관찰 조건을 작성합니다.
   각 이벤트의 거래량 상위 유효 베팅 최대 두 개를 보여 줍니다. 예·아니오 가격은 해당 시장 ID의 원자료에서
   직접 넣고, 독립적인 질문들의 확률 합계를 100%로 바꾸지 않습니다. 거래량은 이벤트 전체 값입니다.
   선택지 이름은 선택지끼리 다른 부분(인물·수치·기한)만 씁니다. 모든 선택지에 공통인 날짜·연도는 제목과
   질문이 말하므로 검증도 요구하지 않습니다 — 예전에는 "니콜라스 마두로 지도자 2026년"처럼 군더더기가 붙었습니다.
   선택지를 가르는 수치와 상승·하락 방향("hit (LOW) $186" → "186달러까지 하락")은 계속 검사합니다.
   같은 호출이 후보 기사 하나를 골라 그날 보도를 전하는 한 문장(`news_hook`, "어제는 …했다는 소식이 전해졌습니다")을
   쓰고, 음성은 여는 말 → 이 문장 → 해설 → 질문 → 확률 순입니다. 문장의 숫자는 그 기사 제목·원문에 있는 것만,
   컨센서스·참여자·확률과 엮는 말은 허용하지 않으며, 문장과 낱말이 가장 많이 겹치는 후보를 그 문장의 기사로
   삼습니다(모델이 id를 잘못 적어도 근거가 다른 기사를 가리키지 않게). 어긋나면 이 문장만 빼고 원고는 살립니다.
   고른 기사는 검수 기록에 `시의 뉴스(…)`로 남습니다.
6. 선정 기록·원자료·원고를 저장한 뒤 기존 TTS·영상 렌더·검수 흐름으로 진행합니다.

한 번의 실행은 모델 최대 2회, 상세 최대 5회, 뉴스 검색 최대 5회, 수집 뉴스 조회 최대 20쪽입니다. 자격증명이나 원고 검증에 문제가 있으면
제작을 중단하고 실패 단계를 기록합니다. 적합한 이슈가 없으면 `no_suitable_issues`로 끝나며 당일 제작 완료로 기록하지 않습니다.
Jev는 연결하지 않습니다. 후보 축소는 로컬 수치 계산으로 처리합니다.

거래량·유동성은 관심의 대리 지표이지 고유 참여자 수나 검색량은 아닙니다. 변동 자료가 없는 이벤트를
변동 없는 이벤트라고 설명하지 않습니다. 수집 API의 이벤트 종료 예정일과 설명은 개별 베팅의 최종 판정 시각·규칙 전체와
다를 수 있습니다. 실제 조건은 함께 저장한 원문 링크에서 검수합니다. 숫자 검증은 번역의 의미 일치까지 보장하지 않으므로
게시 전 원문 질문·한국어 선택지·원고를 함께 확인합니다.

### 텔레그램에서 운영 (기준 절차)

운영은 텔레그램 봇의 `/shorts`(또는 🛠 웹 관리 → 🎬 쇼츠)에서 합니다. 봇은 이 패키지를
import하지 않고 이 venv의 파이썬으로 CLI를 하위 프로세스로 부릅니다.

| 텔레그램 | 부르는 CLI |
|---|---|
| `/shorts` | `--status` (최근 제작일·현재 수정본·검수 상태 JSON) |
| `/shorts run` · `run force` | 인자 없음 · `--force` |
| `/shorts preview` | `--status`로 영상 경로를 받아 MP4를 채팅으로 보냄 |
| `/shorts edit 수정할 내용` | `--edit "수정할 내용"` |
| `/shorts done [검토번호]` | `--review-approve TOKEN` (해당 원고 승인·업로드) |
| `/shorts hold [검토번호]` | `--review-pause TOKEN` (자동 승인 중단) |
| `/shorts review` | `--review-pending` (검토 중인 원고 보기) |
| `/shorts upload` | `--upload` (검수 완료본만 재시도) |

비대화형 명령은 stdout에 JSON 한 줄만 쓰고, 실패하면 stderr 마지막 줄에 이유를 남기고
0이 아닌 코드로 끝납니다. 이 JSON이 봇과의 계약입니다(`src/polymarket_shorts/status.py`).
아래 브라우저 패널과 대화형 검수는 개발용으로 남겨 둡니다.

### 브라우저 검수 패널

기존 산출물을 영상 플레이어와 장면별 원고가 있는 로컬 패널에서 검수할 수 있습니다.
서버는 `127.0.0.1`에만 열리며 외부 게시나 업로드를 하지 않습니다.

```powershell
cd C:\Users\PSI\orca\stock_chatbot
$env:PYTHONPATH='shorts/src'
.\venv\Scripts\python.exe -m polymarket_shorts.cli --browser storage/shorts/2026-09-13
```

브라우저가 자동으로 열립니다. 자연어 수정 요청을 보내면 새 음성·자막·MP4를 렌더하고
같은 화면이 최신 수정본으로 바뀝니다. `검수 완료`는 현재 수정본의 로컬 상태만 완료로
기록합니다. 기본 포트가 사용 중이면 `--port 8766`처럼 바꿀 수 있습니다.

오늘 영상을 먼저 만든 뒤 출력된 폴더를 `--browser`에 지정하면 됩니다.

저장소 루트에서 실행합니다.

```powershell
cd C:\Users\PSI\orca\stock_chatbot
.\venv\Scripts\python.exe -m pip install -e ./shorts
$env:PYTHONPATH='shorts/src'

# 오늘 영상 생성 후 자연어 검수 시작
.\venv\Scripts\python.exe -m polymarket_shorts.cli --interactive

# 기존 산출물 폴더에서 검수 이어가기
.\venv\Scripts\python.exe -m polymarket_shorts.cli --workflow storage/shorts/2026-09-13

# 제작 원고로 생성한 직후 검수
.\venv\Scripts\python.exe -m polymarket_shorts.cli --plan storage/shorts/editorial-2026-09-13/editorial.json --interactive
```

대화에는 원고와 MP4의 로컬 링크가 표시됩니다. 영상을 열어 본 뒤 수정할 내용을 입력합니다.

```text
쇼츠> 첫 멘트를 질문형으로 바꾸고 두 번째 장면 설명을 쉽게 줄여줘
쇼츠> 목소리를 10% 느리게 해줘
쇼츠> 보기
쇼츠> 완료
```

- `보기`: 현재 원고와 영상 경로 확인
- 자연어 입력: 수정 요청을 반영하고 음성·자막·영상을 다시 생성
- `완료` 또는 `검수 완료`: 현재 완성본의 검수 완료를 기록하고 종료
- `종료`: 검수 대기 상태를 보존하고 나중에 이어가기

지원 편집은 멘트·화면 문구·제목/설명/태그, 중간 장면 삭제·순서 변경,
발화 속도(-30%~+50%), 저장된 무역/금융 도시 배경 선택과 강조색 변경입니다.
새 이미지·음악 생성이나 임의의 영상 효과는 지원하지 않습니다.

수정본은 `revisions/<ID>/`에 MP4·원고·음성·자막과 `edit.json`(요청·수정 내용)을
보존합니다. `workflow.json`이 최신 완성본을 가리키므로 같은 원본 폴더의 `--workflow`로
이어집니다. 완료한 영상도 다시 수정하면 검수 대기로 돌아갑니다.
렌더 실패 시 이전 완성본은 남습니다. 최초 `editorial.json`은 덮어쓰지 않습니다.

검수 원고만 출력하려면 다음 명령을 사용합니다.

```powershell
.\venv\Scripts\python.exe -m polymarket_shorts.cli --review storage/shorts/2026-09-13
```

`review.md`만 직접 수정해도 영상에는 반영되지 않습니다. 대화형 편집을 사용하거나
제작 원고를 수정하고 `--plan`으로 다시 렌더하세요.

## YouTube 업로드 설정 (최초 한 번)

1. [Google Cloud Console](https://console.cloud.google.com/)에서 프로젝트를 만들고
   API 및 서비스 → 라이브러리에서 **YouTube Data API v3**를 사용 설정합니다.
2. Google Auth Platform의 브랜딩·대상(또는 OAuth 동의 화면)을 설정합니다.
   외부 앱을 테스트 상태로 만들고 테스트 사용자에 업로드할 채널 소유자의 Google 계정을 추가합니다.
3. 클라이언트(또는 사용자 인증 정보 → OAuth 클라이언트 ID 만들기)에서
   **데스크톱 앱**을 선택합니다. 발급된 ID와 secret을 운영자 PC의 저장소 루트 `.env`에
   `SHORTS_YOUTUBE_CLIENT_ID`, `SHORTS_YOUTUBE_CLIENT_SECRET`으로 넣습니다.
4. 저장소 루트에서 쇼츠 패키지가 설치된 Python으로 아래 명령을 실행하고 브라우저에서
   해당 YouTube 채널 계정으로 승인합니다. 서버에서 실행하지 않습니다.

   ```powershell
   $env:PYTHONPATH='shorts/src'
   python -m polymarket_shorts.cli --youtube-auth
   ```

   127.0.0.1 임의 포트로 승인 결과를 받으며 PKCE와 state를 검사합니다.
   화면에 출력된 `SHORTS_YOUTUBE_REFRESH_TOKEN=...`을 복사합니다. 도구는 토큰을
   파일로 저장하지 않습니다. 터미널 출력도 외부에 공유하지 마세요.
5. 서버의 `/srv/stock-chatbot/.env`에 같은 ID·secret과 refresh token을 넣습니다.
   공개 범위(`youtube_privacy=public`)와 카테고리(`youtube_category_id=25`)는 `config.py` 상수입니다.
   **테스트 상태 앱의 refresh token은 발급 7일 뒤 끊깁니다** — 2026-09-27에 받은 토큰이 10-04에 끊겨
   나흘 동안 영상만 만들고 올리지 못했습니다. 지속 운영하려면 Google Auth Platform → 대상에서 앱을
   프로덕션으로 게시합니다(미검증 앱 경고는 채널 소유자 본인 승인에는 지장이 없습니다). YouTube API
   미검증 프로젝트는 공개 전환이 제한될 수 있습니다.
   토큰이 끊기면 업로드가 `invalid_grant`로 거부되고 텔레그램에 그 문구가 옵니다. 4단계를 다시 실행해
   서버 `.env`의 `SHORTS_YOUTUBE_REFRESH_TOKEN` 한 줄만 바꿉니다. 쇼츠 CLI는 실행마다 `.env`를 읽으므로
   봇을 재시작하지 않아도 되고, 승인된 원고는 다음 확인 주기(1분)에 다시 올라갑니다.

제작한 시나리오는 텔레그램에 먼저 전송합니다. `auto_publish`(켜짐)이면 원고 전체가 전달된 뒤
`review_timeout_minutes`(60분) 동안 응답이 없을 때 승인·업로드합니다. 전송 실패에는 자동 승인 시간이
시작되지 않습니다. `config.py`에서 끄면 직접 승인할 때까지 기다립니다.

`/shorts done [검토번호]` 또는 승인 버튼으로 즉시 올리고, `/shorts hold [검토번호]`로 보류합니다.
`/shorts edit [검토번호] 수정할 내용`은 자동 승인을 멈춘 뒤 수정·재렌더합니다. 수정본을 보낸 뒤 다시
1시간을 기다리며 수정 실패 시 보류가 유지됩니다. 번호를 생략하면 최근 제작본이 대상입니다.
텍스트뿐 아니라 사용자가 명시한 자료 내용·선택지·확률 수정도 지원하며 원자료와 변경 기록을 보존합니다.

승인 상태는 날짜·수정본에 묶여 재시작 후에도 유지됩니다. 과거 버튼으로 새 수정본을 승인할 수 없고,
이미 업로드한 날에는 자동 중복 게시하지 않습니다. 업로드 재시도는 저장된 업로드 이력을 사용합니다.
새 검토 대상은 CLI `--complete`로 우회할 수 없습니다. CLI 직접 승인은 `--review-approve TOKEN`입니다.
기존 검수 완료본 수동 업로드는 다음과 같습니다.

```powershell
python -m polymarket_shorts.cli --upload
python -m polymarket_shorts.cli --upload storage/shorts/2026-09-27
python -m polymarket_shorts.cli --status
```

`--upload`는 `status`(uploaded/already_uploaded/not_reviewed/no_credentials),
`video_id`, `url`을 JSON 한 줄로 출력합니다. `--status`에는 기존 필드와 함께
`uploaded`·`url`이 있습니다. 금지 문구·영상 변경·HTTP 실패는 안전한 오류 한 줄로
거부합니다. 제목·설명·태그는 검수 기록 그대로이며 한국어·아동용 아님·
변경/합성 콘텐츠(`containsSyntheticMedia=true`) 표시를 함께 설정합니다.

각 수정본의 `upload.json`은 수정본 ID·영상 ID·URL·업로드 시각을 원자적으로 보존합니다.
전송 중에는 재개 세션 주소를 보존하므로 응답을 못 받았어도 같은 세션을 조회해 이어갑니다.
세션 만료(404)나 손상된 기록은 자동으로 새 영상을 만들지 않습니다. YouTube Studio에서
실제 게시 여부를 먼저 확인하고 복구해야 합니다. 이 파일은 비공개 저장소 안에 둡니다.

게시가 끝나면 웹 첫 화면의 "오늘의 영상"이 읽는 `storage/public/shorts/ko.json`에
가장 최근 게시본(날짜·영상 ID·제목)을 씁니다(`youtube.publish_latest`). 웹은 `storage/shorts/`를 읽지 않으므로
공개할 것만 이 파일로 넘깁니다. 이 파일을 쓰지 못해도 게시 결과는 그대로이며 다음 게시 때 따라잡습니다.

구현 기준: [재개 업로드 프로토콜](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol),
[데스크톱 OAuth](https://developers.google.com/identity/protocols/oauth2/native-app),
[영상 status 필드](https://developers.google.com/youtube/v3/docs/videos#status).

## 하루 한 번 실행

한국시간 20시에 실행되는 systemd timer가 저장소의 `infra/systemd/`에 있습니다.

유닛은 다른 앱 유닛과 같은 계정·경로를 쓴다 — `stockbot` 계정으로
`/srv/stock-chatbot/shorts`에서 실행하며, 가상환경(`.venv`)과 `.env`도 그 아래에 둔다
(`infra/host-contract.md`).

```bash
sudo cp /srv/stock-chatbot/infra/systemd/polymarket-shorts.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now polymarket-shorts.timer
systemctl list-timers | grep polymarket-shorts
```

20시 실행 시점에 웹 앱의 숫자 generation과 줄글 generation이 잠시 어긋나 있으면 서비스가 실패 후 15분 간격으로 최대 8번 재시도합니다. 날짜 상태 파일은 완성 후에만 기록하므로 실패한 시도가 당일 제작 기회를 소모하지 않습니다.

수동 실행과 로그 확인:

```bash
sudo systemctl start polymarket-shorts.service
journalctl -u polymarket-shorts -n 100 --no-pager
```

## 제작 흐름

### 검수한 제작 원고로 렌더하기

일관된 영상 연출 기준은 `prompts/editorial_ko.txt`에 있습니다. 원자료 snapshot과
필요한 질문 상세를 고정한 뒤 이 프롬프트로 `editorial.json`을 정제·검수합니다.
이번 원고 정제는 Codex 세션에서 수행했으며, CLI가 LLM에 자동 정제를 요청하지는 않습니다.
`editorial.json`에는 전체 내레이션, 화면 문구, 장면 목적, 근거, 검수 결과를 구분합니다.

```powershell
$env:PYTHONPATH='shorts/src'
.\venv\Scripts\python.exe -m polymarket_shorts.cli --plan storage/shorts/editorial-2026-09-13/editorial.json
```

`--plan`은 원자료를 다시 가져오거나 문장을 재요약하지 않습니다. 원고 폴더에 MP4와
`production.json`·`review.md`를 만들고 `media/`에 연속 합성한 MP3와 자막을 보존합니다.
시나리오와 자연어 편집은 장면별로 처리하지만 TTS에는 전체 원고를 한 번에 전달합니다.
따라서 장면 경계에서도 한 화자의 억양과 호흡이 이어지며, 연속 음성에서 얻은 자막
시각을 기준으로 화면만 전환합니다. 게시 상태·서버 설정은 변경하지 않습니다.

```text
기존 웹 앱 API
  → generation·freshness 검증
  → 최신 컨센서스 최대 5개 선정(가능하면 2개 이상)
  → 시나리오 구성(허용 상한 150초)
  → Edge TTS 음성·VTT 자막
  → Pillow 세로 장면 + Blender VSE 합성·인코딩
  → MP4·시나리오·검수 원고 저장
  → 날짜별 상태 기록(하루 중복 방지)
  → 원고·영상 확인 → 자연어 수정 → 재렌더 → 로컬 검수 완료
```

## 운영상 주의

### 쇼츠 연출과 대본

2026-09-13 기준, 슈카월드·부읽남TV의 공개 쇼츠 목록과 Planet Money 제작진 인터뷰를 참고했습니다.
국내 두 채널은 구체적인 사건·숫자와 시청자의 질문을 제목에서 연결하고, Planet Money는
일상적인 말과 사례로 경제 개념을 설명합니다. 공개 제목·조회수와 제작진 설명을 참고한
편집 판단이며, 영상 전체를 시청해 측정한 유지율 분석이나 성공의 인과관계 검증은 아닙니다.

- [슈카월드 표본](https://tenb.io/yt/channel/@syukaworld): 공개 목록 중 쇼츠 18개. 오해·질문을 앞세우는 제목을 참고했습니다.
- [부읽남TV 표본](https://tenb.io/yt/channel/@buiknam_tv): 공개 목록 중 쇼츠 19개. 숫자와 시청자의 이해관계를 연결하는 구성을 참고했습니다. 과장된 확신 표현은 채택하지 않았습니다.
- [Planet Money 제작진 인터뷰](https://www.linkinbio.news/p/lets-talk-about-brands-on-tiktok): 짧은 세로 영상에 맞춘 설명과 독립적인 스토리 구성.
- [Planet Money의 공개 성과 사례](https://www.nationalpublicmedia.com/insights/articles/planet-money-tiktok-builds-awareness-among-young-audiences/): 2023년 SVB 영상 170만 회 이상 사례. TikTok 사례이며 YouTube 성과와 동일시하지 않습니다.

도입은 "10월 7일 집단 예측 컨센서스 요약입니다. 오늘은 금융시장과 맞닿은 질문 N개를 차례로
짚어 보겠습니다"로 말합니다. 오래되거나 실패한 요약은 읽지 않습니다.

**이슈 장면은 여는 말 → 그날 보도(있을 때) → 해설 → 질문 → 확률 → 갈림 풀이 순서로 말합니다.** 여는 말(`lead_in`)은
원고가 이슈마다 다른 말로 쓰고(같은 이음말이 연달아 나오면 둘째부터 뗍니다), "먼저 국제유가부터 보겠습니다."처럼 한
문장으로 끝맺습니다. 장면의 첫 문장 뒤는 다른 문장 끝(0.7초)보다 조금 더 쉽니다(`tts.OPENER_PAUSE_SECONDS` 1.0초) —
주제를 알리자마자 사실·숫자를 쏟아내는 느낌이 들었습니다(2026-10-08 운영자 지적). 여는 말 뒤 질문 앞까지는 연결어미로
이은 두 문장 안팎으로 쓰게 합니다 — "~합니다. ~됩니다."로 짧게 끊으면 기계가 읽는 것처럼 들렸습니다. 쉼표로 끝난
조각은 마침표 없이 다음 문장에 잇습니다.
예전에는 고정 연결문과 숫자만 이어 붙여 문단을 붙인 것처럼 들렸습니다(2026-10-07 운영자 지적). 원고 프롬프트는 시청자가 경제·정치
배경지식이 있다고 보고 기본 개념을 풀어 쓰지 않게 합니다. 확률은 화면과 같은
퍼센트로 말하고, 참여자가 무엇을 **기대하고 있는지**로 말합니다(2026-10-08 운영자 결정 — "…쪽으로 봅니다"·
"…것으로 봅니다"를 쓰지 않습니다). 양자택일은 "컨센서스 참여자의 0.4%는 9월 30일까지 호르무즈 해협 통행이 정상화될 것을
기대하고 있습니다"로 '예' 쪽만 말하고, 기준이 여럿인 질문은 "컨센서스 참여자의 91.5%는 186달러까지 하락을,
컨센서스 참여자의 89%는 185달러까지 하락을 기대하고 있습니다"처럼 비율마다 주어를 다시 댑니다(2026-10-08 운영자 지시 —
주어는 "참여자"가 아니라 "컨센서스 참여자"). 여러 선택지 중 하나는 주제를 앞세웁니다.
**대상을 대명사로 퉁치는 말은 쓰지 않습니다**(2026-10-08 운영자 지시) — "그쪽이 우세합니다"·"반대쪽이 더 많습니다"·
"한쪽으로 쏠려 있습니다"·"99.6%는 그 반대를" 같은 말이 무엇을 가리키는지 이름을 대지 않았습니다. 확률 뒤 풀이
(`speech.consensus_mood`, 같은 풀이는 한 영상에 한 번)는 대상 없이도 뜻이 서는 경우("팽팽하게 갈립니다", "기준에 따라
엇갈립니다", "뚜렷하게 앞서는 답 없이 나뉩니다")만 말하고 나머지는 비웁니다. 원고 프롬프트도 같은 지칭 규칙을 둡니다. 괄호 속 영문
(`한국 ETF(EWY)`)은 화면·자막에만 두고 읽지 않습니다(`tts.spoken_text`, 2026-10-08). 마무리는 고정 멘트로 "자세한 내용은
눈치 닷 라이브에서 확인하세요"라고 말하고 화면에는 nunchi.live를 적습니다. 규칙은 `src/polymarket_shorts/speech.py`에
있고 LLM을 부르지 않습니다. 화면의 선택지는 확률을 말하기 시작하는 자리
(`Scene.options_at`, 원고 글자 비율)에 맞춰 뜹니다.
원고에 여는 말이 없으면 둘째 이슈부터 "이번에는 주식·시장 쪽 질문으로 넘어가 보겠습니다"처럼 다음
이슈의 분야를 알리는 대체 문장을 번갈아 씁니다(2026-09-28 운영자 결정 — 예전의 "이번엔 분위기가 좀
다릅니다"는 무엇이 다른지 알려 주지 않았습니다). 장면별 확인점(`watch_point`)은 **화면에 넣지 않고** 검수 기록
(`review.md`)에만 남기고, 고지문은
마무리에서 한 번만 말합니다. 말하지 않는 당부를 화면에만 띄우면 보는 것과
듣는 것이 어긋납니다. 장면마다 "…확인하세요"를 붙이면 같은 당부를 다섯 번
듣게 됩니다. 모델이 쓰는 경로(`highlights.PROMPT`·`workflow.EDITOR_PROMPT`·
`prompts/editorial_ko.txt`)에도 같은 기준을 적어 두었고 `tests/test_tone.py`가
셋 다 확인합니다.

**이슈 화면은 선택지 이름 + 큰 '예' 확률 + 막대입니다.** 2026-09-23 산출물은
원자료 형식을 그대로 옮겨 "9월 WTI 90달러 이하: 예 99.95%, 아니오 0.05%" 한
덩어리였고, 어색한 자리에서 줄이 바뀌고 어느 숫자를 봐야 하는지도 알 수
없었습니다. 이지선다에서 아니오는 예의 나머지이므로 화면은 '예' 확률 하나만
크게 세우고 막대로 그 크기를 보여 줍니다. 정확한 값은 원자료 그대로이고
(`Scene.options`), 같은 내용을 검수용으로 옮겨 적은 글이 `body`입니다.

**본론 목록은 셋으로 움직입니다.** 질문이 바뀌면 그 줄이 0.4초(12장) 동안 드롭다운처럼 펼쳐지고 앞 줄이 접히며,
큰 수치와 막대가 0에서 제자리까지 차오르고(1초,
영상 한 프레임마다 한 장인 30장, 처음에 빠르고 끝에서 느려지는 ease-out. 2026-10-07 전에는 8장이라 계단처럼 튀었습니다.
올라가는 동안에는 정수만 보여 주고 확정된 값은 마지막에 한 번 제대로 섭니다), 선택지 줄이 말의 순서대로 한 줄씩
밝아집니다(아직 말하지 않은 줄도 흐린 이름과 빈 게이지 홈으로 처음부터 그려 두어 이미 뜬 줄이 밀리지 않습니다). **이미지의 크기와 위치는 고정입니다** — 예전에는
프레임 전체를 ±7px 흘렸는데 화면 틀이 움직이는 것처럼 보여 없앴습니다(2026-09-27).
장면 프레임은 Pillow, 합성은 Blender VSE에서 처리하며 추가 네트워크 호출은 없습니다.
자막은 맨 위에 얹어 흔들리지 않습니다. 도입·마무리도 제목을 먼저 세운 뒤 문구를 얹습니다.

장면 시작과 자막 시각은 모두 TTS가 돌려준 단어 경계에서 가져오고, 긴 자막은
구절로 나눕니다. 나눌 때는 먼저 문장으로 끊고, 한 화면에 안 들어가는 문장만
**균등하게** 자릅니다 — 앞에서부터 한도까지 채우면 꼬리에 "분위기입니다." 한
조각만 남습니다. 쉼표가 있으면 거기서 끊고, edge-tts가 한 어절을 여러 단어로
돌려준 자리("25bp" → "25"·"bp")는 끊지 않습니다. 구절은 자기 첫 단어보다
0.05초 먼저 떠서 다음 구절이 뜰 때까지 남으므로 사이에 빈틈도 겹침도 없습니다.

호흡은 자리마다 다릅니다. edge-tts는 장면 사이도 문장 사이와 똑같이 0.86초로
읽고, 원고를 무엇으로 이어 붙여도(줄바꿈·빈 줄·말줄임표) 그 값이 바뀌지
않습니다. 그래서 합성 뒤에 쉼을 넓힙니다 — 음성이 24kHz·48kbps·모노 CBR이라
144바이트 프레임 하나가 정확히 24ms이므로, 쉼 한가운데의 무음 프레임을 복제해
끼웁니다. 목표는 도입에서 첫 이슈로 `tts.OPENING_PAUSE_SECONDS`(0.9초),
이슈 사이 `TOPIC_PAUSE_SECONDS`(1.2초), 마무리 고지문 앞
`CLOSING_PAUSE_SECONDS`(1.3초), 그리고 장면 안 문장 끝에
`SENTENCE_PAUSE_SECONDS`(0.7초)입니다(2026-10-07 운영자 요청으로 넓혔습니다). 한 값으로 고정하면 어디서나 똑같이
끊겨 사람이 읽는 리듬이 아니게 됩니다. 발화 속도·목소리는 `tts_rate`·
`tts_voice`로 조절하지만 기본값(+0%, `ko-KR-SunHiNeural`)은 그대로
둡니다 — 연출의 자연스러움은 속도나 억양을 과장해서가 아니라 호흡에서 옵니다.
말소리 프레임은 건드리지 않아
재인코딩이 없습니다. 이미 충분한 쉼은 유지하며, 복제할 무음이 없는 경계도 원래 음성과
발화 시각을 보존합니다. 다른 경계에 삽입한 쉼만큼만 후속 자막 시각을 이동합니다.
화면과 자막은 그 쉼의 뒤쪽 `render.SCENE_LEAD`(0.55초)
지점에서 넘어가므로, 새 장면이 먼저 자리를 잡은 뒤에 말이 시작됩니다.
**본론은 질문 목록 한 장입니다**(2026-10-08 운영자 결정). 예전에는 질문마다 화면을 통째로 갈아 끼워 2분 남짓한
영상에서 화면이 네다섯 번 바뀌었고 집중이 끊겼습니다. 이제 화면 전환은 시작 → 목록 → 마무리 두 번뿐입니다.
목록에는 그날 질문이 모두 놓이고, 지금 말하는 질문만 펼쳐져 선택지·확률·근거 수치 칩(24시간 참여 규모·종료 예정·
표시 선택지)을 보입니다. 설명을 마친 질문은 접히며 대표 확률 배지만 남습니다(`render.render_list_frame`).
질문이 많아 넘치면 선택지 줄을 줄이고, 그래도 넘치면 칩을 뺍니다(`render._list_layout`).
시작·마무리는 그날 내용과 무관한 고정 화면입니다(밑줄 없는 세 줄 머리말).

**때깔은 조코딩 쇼츠를 참고했습니다**(2026-10-08). 사진 없는 짙은 남색 한 장(`render._plain_backdrop`), 왼쪽 위
터미널 꼴 머리 `~/nunchi consensus`와 진행 점(`render._header`), 강조색은 초록 하나(`_COLORS["brand"]` — 노란색은
운영자가 바꾸라고 했습니다)이고 게이지는 하늘색입니다. 글씨의 빛 번짐은 약하게만 씁니다.
선택지는 질문을 말하는 동안 흐린 이름과 빈 게이지로 먼저 자리를 잡고, 확률을 말할 때 한 줄씩 밝아지며
숫자가 차오릅니다. 줄 테두리는 화면 오른쪽 끝(x=1008)까지 가지만 줄 안 내용과 칩은 좋아요·댓글
버튼 줄을 피해 x=878에서 멈춥니다. 안전 영역 아래에는 글자를 두지 않습니다.
자막의 아래 끝은 y=1540 — Shorts 플레이어의 채널명·제목 바(아래 약 380px)에
가리지 않는 가장 아래입니다. 고지문은 두 줄 자막보다 위(y≈1360)에서 끝납니다.
자막 줄바꿈은 렌더가 어절 경계에서 넣고 Blender text 스트립은 그대로 그립니다.
libass는 한글도 중국어·일본어처럼 아무 글자에서나 끊어서 "10월 금리 변동 없음과"가
"…없" / "음과 …"로 갈라졌습니다. 줄 수가 늘지 않는 선까지 폭을 좁혀 두 줄 길이를
고르게 맞추므로 뒷줄에 한 어절만 남지 않습니다.
MP4 옆의 `.timeline.json`에서 장면과 숫자·해석 화면의 시각을 확인할 수 있습니다.
합성은 Blender VSE의 image/movie/sound 스트립으로 수행하며 MPEG4/H.264·AAC 192k,
1080×1920·30fps·yuv420p로 출력합니다. 내레이션은 `blender_render.NARRATION_GAIN`(1.3배, +2.3dB)으로
키워 넣습니다 — 실측 최대치가 -3.6dB(한국어)·-3.1dB(영어)라 키워도 0dBFS 아래에 남습니다. 작업 폴더의 `blender-manifest.json`에 입력 경로와
비트·자막 시각, 반복할 클립 구간(이미지 크기·위치는 고정),
음성 뒤 0.6초 여운과 마지막 프레임 1초 연장 범위를 기록합니다. 최종 길이는 음성+0.6초로
제한하므로 프레임 연장이 영상 길이를 늘리지 않습니다. `phrases.srt`는 검수용으로 유지합니다.
자막은 렌더가 Pillow로 그린 화면 크기 투명 PNG(`render._caption_frame`)를 Blender image 스트립으로 얹습니다.
Noto Sans CJK KR Bold 50px(`render.CAPTION_RENDER_SIZE`) 흰 글씨를 **상자 없이** 옅은 그림자만 깔아 세우고,
숫자(`18%`·`5,000달러` 등)만 강조색 초록으로 짚습니다(2026-10-08). 바탕이 짙은 남색 한 장이라 상자 없이도 읽힙니다.
아래 끝은 y=1540, 좌우 190px 안전 영역 안에서 화면 가운데에 섭니다.

**지금 화면은 아래 배경 그림을 쓰지 않습니다**(2026-10-08). 바탕은 렌더가 그리는 남색 한 장이라 생성한 그림은
화면에 나오지 않습니다. 생성 단계는 걷어낼 때까지 아래처럼 그대로 돕니다.
배경은 그날 이슈로 새로 그립니다(`media.backgrounds_for`). 원고가 쓴 글자 없는 장면
묘사(`image_scene`)를 Cloudflare `image_model`(`flux-1-schnell`)로 그리고, 정사각형
결과의 가운데를 9:16으로 잘라 씁니다. 도입은 첫 이슈 그림을 다시 쓰지 않고 도입용 풍경을
따로 그립니다. 그린 뒤 `vision_model`(LLaVA)로 사람·글자가 보이는지 묻고, 보이면 최대
3번까지 다시 그립니다. 끝까지 걸리거나 호출이 실패하면 `assets/backgrounds/`의 저장 배경
(`global-trade.png`·`financial-city.png`)으로 갑니다 — 배경 한 장 때문에 제작을 멈추지 않습니다.
그림은 날짜 폴더 `backgrounds/`에 묘사의 해시로 저장해 수정·재렌더가 같은 그림을 다시 씁니다.
화면에 "AI 배경" 표기는 두지 않습니다(2026-09-28). FLUX.2 klein 4b도 비교했지만(2026-09-29)
배경으로는 차이가 거의 없고 한 장에 수 분이 걸려 schnell을 유지합니다.
저장 배경은 **장면마다 다르게 잡습니다** — 크롭 위치를 장면 순서대로 옮기고, 짝수 장면은
좌우를 뒤집고, 색조를 그 장면의 accent(gold/blue/red)로 입힙니다(`render._background`).
`generated_backgrounds=False`이면 생성하지 않고 저장 배경만 씁니다.
`visuals_enabled=False`이면 기본 단색 배경을 사용하며, 이미지가 없거나
손상됐을 때도 경고를 기록하고 단색 배경으로 진행합니다.
HyperFrames 내보내기는 선택한 PNG를 프로젝트 `assets/`로 복사합니다.

### 선택 기능: Seedance 모션 배경

`config.py`의 `generated_clips=True`와 `.env`의 `SHORTS_VIDEO_API_KEY`(fal 키)가 있으면 이미 만든
flux 이슈 PNG를 무음 영상으로 확장합니다. 기본값은 꺼짐이며 키가 비어 있거나 성공한
클립이 없으면 정지 PNG를 같은 Blender VSE 경로로 합성합니다.
`visuals_enabled`와 `generated_backgrounds`도 켜져 있어야 새 이슈 PNG를
만들 수 있습니다. 저장된 기본 배경은 영상 생성에 보내지 않습니다.

- 모델: `video_model=bytedance/seedance-2.0/fast/image-to-video`.
- 길이: `clip_seconds=8`(정수 4~15), 720p·9:16·무음.
- 최대 `max_groups`개(5개)를 동시에 제출하고 전체 10분까지만 기다립니다.
  다운로드·길이·해상도·디코딩 검증 실패와 시간 초과는 경고를 남기고 PNG를 사용합니다.
- `backgrounds/<이슈 해시>.mp4`를 캐시합니다. 도입은 따로 그린 정지 그림을 쓰고(클립 없음) 마무리는
  정지 배경입니다. 자연어 수정본은 원본 폴더의 캐시를 재사용합니다.
- 클립은 VSE movie 스트립으로 장면 끝까지 반복하고, 비트 PNG와 자막을 위에 얹어 한 번 인코딩합니다.
  정지 구간만 전체 영상 시각의 드리프트를 적용합니다. 음악은 추가하지 않습니다.
  업로드는 위 검수 완료 절차를 따릅니다.

8초 × 5개는 새 이슈 PNG 5장에 대해 최대 40초 분량의 유료 생성입니다. 계획서의
fast 720p 추정 기준은 하루 약 $9.7, 30일 약 $290이며 고정 요금 상한이 아닙니다.
길이를 늘리거나 이슈 묘사를 바꾸어 새 해시를 만들면 비용도 늘어납니다. 시간 초과는
원격 작업의 취소·환불을 보장하지 않습니다. 활성화 전에
[fal 모델 문서와 현재 요금](https://fal.ai/models/bytedance/seedance-2.0/fast/image-to-video)을 확인하세요.
입력 형식과 Queue API 근거는 `clips.py` 주석에 있습니다.

클립을 사용한 검수 원고에는 합성 배경 개수와 YouTube 변경·합성 콘텐츠 표시 안내가
추가됩니다. 글자·인물·급격한 밝기 변화를 직접 확인하고 문제가 있는 장면은
`--edit "2번 장면 배경을 정지로"`로 해당 장면만 기존 PNG로 돌릴 수 있습니다.
systemd 실행 상한은 40분이며 메모리 제한은 768M 그대로입니다. 실제 API 생성 품질,
운영 서버의 최대 메모리와 40분 내 완료 여부는 키 설정 후 별도로 실측해야 합니다.

- 1분을 넘는 쇼츠는 활성 저작권 클레임이 있으면 전 세계 차단될 수 있으므로 기본 영상에는 배경음악을 넣지 않습니다.
- `storage/`(산출물·`storage/shorts/state/`), API 비밀값은 커밋하지 않습니다.
- 테스트: `.venv/bin/python -m pytest -q`
