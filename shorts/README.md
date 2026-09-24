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
- `review.json`: 검수 상태·영상 정보·제목/설명/태그

영상은 인트로, 선정한 개별 이슈 최대 5개, 고지문 순서입니다. 약한 분야를 억지로 채우지 않습니다. 완성과 음성 보존이 길이보다 우선입니다. `SHORTS_MAX_DURATION_SECONDS`는 기본 180초의 참고 목표이며, 초과해도 자동 배속이나 생성 중단을 하지 않습니다. 기본 발화 속도는 +0%입니다. FFmpeg와 HyperFrames 모두 전체 원고를 한 번에 합성한 단일 음성을 사용하고 끝에 0.6초 여유를 둡니다. 화면은 edge-tts가 보고한 단어별 발화 시각에 맞춰 장면별로 전환하며, 장면이 바뀌는 자리에는 1.4초의 호흡을 둡니다. 플랫폼의 Shorts 분류 조건과 제작 목표는 별개이므로 긴 완성본은 게시 전 확인합니다.

## 설치

Ubuntu에서는 FFmpeg와 한글 폰트가 먼저 필요합니다.

```bash
cd ~/stock_chatbot/shorts
sudo apt-get update
sudo apt-get install -y ffmpeg fonts-noto-cjk python3-venv
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env
```

Windows PowerShell에서는 다음처럼 준비합니다.

```powershell
cd C:\Users\PSI\orca\stock_chatbot\shorts
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
winget install --id Gyan.FFmpeg -e
Copy-Item .env.example .env
```

FFmpeg 설치 직후에는 새 PowerShell 창을 열어야 `ffmpeg`와 `ffprobe`가 PATH에서 잡힐 수 있습니다.

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
Workers AI API 토큰입니다. `shorts/.env.example`을 참고해 `shorts/.env`에 채웁니다.
기존 `.env`는 덮어쓰지 말고 필요한 키만 추가하세요.

```dotenv
CLOUDFLARE_ACCOUNT_ID=계정_ID
CLOUDFLARE_API_TOKEN=Workers_AI_API_토큰
SHORTS_EDITOR_MODEL=@cf/meta/llama-3.3-70b-instruct-fp8-fast
```

### 개별 베팅에서 영상까지

1. 수집된 정상 이벤트 목록을 하루 제작 시 한 번 페이지 순회합니다. 수천 건에 AI를 호출하지 않습니다.
2. 경제·지정학, 거시·통화, 주식·시장, 지정학, 기타 경제·금융으로 태그를 분류합니다.
   24시간 거래량 2,000달러·유동성 1,000달러 이상, 종료 예정일이 지나지 않은 이벤트를 대상으로 합니다.
   분야 안에서 거래량 로그 점수 50%, 유동성 20%, 관측된 가격 변동 20%, 종료일까지의 거리 10%로 정렬합니다.
   같은 주제의 날짜·가격 변형은 후보 두 개까지 허용하고, 분야별 최대 10개(전체 최대 50개)를 남깁니다.
3. 기존 Cloudflare 모델을 한 번 호출해 시장 관련성과 시의성을 평가합니다. 각 0~3점 중 모두 2점 이상인 이슈를
   분야·주제당 최대 하나, 전체 최대 `SHORTS_MAX_GROUPS`개(상한 5개) 선정합니다. 중복 제안은 제외 기록을 남깁니다.
4. 선정한 이슈만 상세와 Google News RSS를 조회합니다. 각각 최대 5회입니다.
   자료 시점 이전 7일 이내 뉴스 제목 최대 3개만 보조 자료로 쓰며 기사 본문을 읽었다고 주장하지 않습니다.
5. 두 번째 모델 호출로 질문·선택지를 한국어로 옮기고 시장 연결점과 관찰 조건을 작성합니다.
   각 이벤트의 거래량 상위 유효 베팅 최대 두 개를 보여 줍니다. 예·아니오 가격은 해당 시장 ID의 원자료에서
   직접 넣고, 독립적인 질문들의 확률 합계를 100%로 바꾸지 않습니다. 거래량은 이벤트 전체 값입니다.
6. 선정 기록·원자료·원고를 저장한 뒤 기존 TTS·영상 렌더·검수 흐름으로 진행합니다.

한 번의 실행은 모델 최대 2회, 상세 최대 5회, 뉴스 검색 최대 5회입니다. 자격증명이나 원고 검증에 문제가 있으면
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
| `/shorts done` | `--complete` |

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

## 하루 한 번 실행

한국시간 21시에 실행되는 systemd timer가 저장소의 `infra/systemd/`에 있습니다.

유닛은 다른 앱 유닛과 같은 계정·경로를 쓴다 — `stockbot` 계정으로
`/srv/stock-chatbot/shorts`에서 실행하며, 가상환경(`.venv`)과 `.env`도 그 아래에 둔다
(`infra/host-contract.md`).

```bash
sudo cp /srv/stock-chatbot/infra/systemd/polymarket-shorts.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now polymarket-shorts.timer
systemctl list-timers | grep polymarket-shorts
```

21시 실행 시점에 웹 앱의 숫자 generation과 줄글 generation이 잠시 어긋나 있으면 서비스가 실패 후 15분 간격으로 최대 8번 재시도합니다. 날짜 상태 파일은 완성 후에만 기록하므로 실패한 시도가 당일 제작 기회를 소모하지 않습니다.

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
  → 최신 컨센서스 1~3개 선정
  → 3분 이하 시나리오 구성
  → Edge TTS 음성·VTT 자막
  → Pillow 세로 장면 + FFmpeg 렌더링
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

현재 영상은 오늘의 이슈 하나로 열고, 선정 분야 안의 거래 비중과 질문 비중을 비교합니다.
각 분야는 큰 거래량 숫자 → 완결된 설명 문장 순서로 전환합니다. 숫자는 화면에 남기고
내레이션은 구체적인 주제에 집중합니다. 오래되거나 실패한 요약은 읽지 않습니다.
FFmpeg 영상은 저장된 배경과 글자를 고정합니다. 장면 시작과 자막 시각은 모두 TTS가
돌려준 단어 경계에서 가져오고, 긴 자막은 구절로 나눕니다. 구절은 자기 첫 단어보다
0.05초 먼저 떠서 다음 구절이 뜰 때까지 남으므로 사이에 빈틈도 겹침도 없습니다.

장면이 바뀌는 자리는 따로 다룹니다. edge-tts는 장면 사이도 문장 사이와 똑같이
0.86초로 읽고, 원고를 무엇으로 이어 붙여도(줄바꿈·빈 줄·말줄임표) 그 값이 바뀌지
않습니다. 그래서 합성 뒤에 쉼을 넓힙니다 — 음성이 24kHz·48kbps·모노 CBR이라
144바이트 프레임 하나가 정확히 24ms이므로, 쉼 한가운데의 무음 프레임을 복제해
끼워 `tts.SCENE_PAUSE_SECONDS`(1.4초)에 맞춥니다. 말소리 프레임은 건드리지 않아
재인코딩이 없습니다. 이미 충분한 쉼은 유지하며, 복제할 무음이 없는 경계도 원래 음성과
발화 시각을 보존합니다. 다른 경계에 삽입한 쉼만큼만 후속 자막 시각을 이동합니다.
화면과 자막은 그 쉼의 뒤쪽 `render.SCENE_LEAD`(0.55초)
지점에서 넘어가므로, 새 장면이 먼저 자리를 잡은 뒤에 말이 시작됩니다.
화면 아래쪽 순서는 본문 패널 → 고지문·출처 → 자막이고 자막이 가장 아래입니다.
자막의 아래 끝은 y=1540 — Shorts 플레이어의 채널명·제목 바(아래 약 380px)에
가리지 않는 가장 아래입니다. 진행 상태바는 그 자리를 비우려고 헤더의 페이지 번호
옆(y=677)으로 올라갔습니다.
MP4 옆의 `.timeline.json`에서 장면과 숫자·해석 화면의 시각을 확인할 수 있습니다.

배경은 `assets/backgrounds/`에 미리 저장한 GPT Image PNG를 재사용합니다.
복합·공급망 장면은 `global-trade.png`, 나머지 장면은 `financial-city.png`를 사용합니다.
생성 방식과 원본 프롬프트는 같은 폴더의 `provenance.json`에 있습니다.
일일 렌더에는 이미지 API 키나 이미지 생성 비용이 필요하지 않습니다.
`SHORTS_VISUALS_ENABLED=false`이면 기본 단색 배경을 사용하며, 이미지가 없거나
손상됐을 때도 경고를 기록하고 단색 배경으로 진행합니다.
HyperFrames 내보내기는 선택한 PNG를 프로젝트 `assets/`로 복사합니다.
새 배경은 ChatGPT/Codex 내장 이미지 생성으로 준비해 위 파일을 교체하면 됩니다.
이미 생성한 MP4에는 소급 적용되지 않으며 다음 렌더부터 반영됩니다.

- 1분을 넘는 쇼츠는 활성 저작권 클레임이 있으면 전 세계 차단될 수 있으므로 기본 영상에는 배경음악을 넣지 않습니다.
- `storage/`(산출물·`storage/shorts/state/`), API 비밀값은 커밋하지 않습니다.
- 테스트: `.venv/bin/python -m pytest -q`
