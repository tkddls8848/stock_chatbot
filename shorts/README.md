# Polymarket Daily Shorts

기존 `stock_chatbot` 공개 웹 앱의 두 API를 읽어 한국어 YouTube Shorts를 하루 한 편 만듭니다.

- `/api/polymarket/sector-brief`: 영상 내레이션의 원재료인 컨센서스 줄글
- `/api/polymarket/summary`: generation·신선도·열린 event 수 검증

원본 대시보드 파일이나 봇 패키지를 import하지 않으므로 별도 프로세스와 가상환경으로 운용할 수 있습니다. API 두 응답의 generation이 다르거나 데이터가 지연·부실하면 잘못된 영상을 만드는 대신 그날 실행을 실패시킵니다.

## 결과물

한 번 실행하면 `output/YYYY-MM-DD/` 아래에 다음 파일이 생깁니다.

- `polymarket-YYYY-MM-DD.mp4`: 1080×1920, H.264/AAC 세로 영상
- `scenario.json`: 사용한 generation, 장면, 전체 내레이션과 실제 길이
- `youtube.json`: 제목, 설명, 태그

영상은 인트로, 최대 3개 컨센서스 분야, 고지문 순서입니다. `복합(경제·지정학)`을 우선 보존하고 나머지는 24시간 거래량 상위 분야를 사용합니다. 긴 단락은 문장 경계에서 줄여 약 2분 50초~2분 59초를 목표로 합니다. 첫 TTS가 179초를 넘으면 필요한 만큼만 발화 속도를 한 번 높여 다시 만들고, 그래도 넘으면 업로드하지 않습니다.

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
cd C:\Users\tkddl\orca\stock_chatbot\shorts
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
winget install --id Gyan.FFmpeg -e
Copy-Item .env.example .env
```

FFmpeg 설치 직후에는 새 PowerShell 창을 열어야 `ffmpeg`와 `ffprobe`가 PATH에서 잡힐 수 있습니다.

로컬 영상 생성 테스트:

```bash
.venv/bin/python -m polymarket_shorts.cli --no-upload
```

같은 한국 날짜에 다시 실행하면 `already_produced`로 끝납니다. 입력이나 디자인을 바꾸고 다시 만들 때만 `--force`를 사용합니다.

```bash
.venv/bin/python -m polymarket_shorts.cli --force --no-upload
```

## YouTube 연결

Google Cloud에서 YouTube Data API v3를 활성화하고 데스크톱 OAuth 클라이언트를 만든 뒤 JSON을 `client_secret.json`으로 저장합니다. 최초 인증은 브라우저를 사용할 수 있는 로컬 컴퓨터에서 실행합니다.

```bash
.venv/bin/polymarket-shorts-auth
```

생성된 `youtube_token.json`을 서버의 이 프로젝트 폴더에 안전하게 복사합니다. 두 파일은 `.gitignore` 대상입니다. `.env`에서 아래 값을 바꾸면 자동 업로드가 켜집니다.

```env
SHORTS_UPLOAD_ENABLED=true
SHORTS_YOUTUBE_PRIVACY=private
```

처음에는 `private`로 며칠 검수한 뒤 `unlisted` 또는 `public`으로 전환하는 편이 안전합니다. 합성 음성을 사용하므로 업로드 메타데이터의 `containsSyntheticMedia`는 `true`로 전송합니다.

## 하루 한 번 실행

한국시간 21시에 실행되는 systemd timer가 저장소의 `infra/systemd/`에 있습니다.

```bash
sudo cp ../infra/systemd/polymarket-shorts.{service,timer} /etc/systemd/system/
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

```text
기존 웹 앱 API
  → generation·freshness 검증
  → 최신 컨센서스 1~3개 선정
  → 3분 이하 시나리오 구성
  → Edge TTS 음성·VTT 자막
  → Pillow 세로 장면 + FFmpeg 렌더링
  → MP4·시나리오·메타데이터 저장
  → 선택적으로 YouTube Data API 업로드
  → 날짜별 상태 기록(하루 중복 방지)
```

## 운영상 주의

- 1분을 넘는 쇼츠는 활성 저작권 클레임이 있으면 전 세계 차단될 수 있으므로 기본 영상에는 배경음악을 넣지 않습니다.
- 자동 업로드용 Google API 프로젝트가 미검증 상태라면 API로 올린 영상이 비공개로 제한될 수 있습니다.
- `output/`, `state/`, OAuth 자격증명은 커밋하지 않습니다.
- 테스트: `.venv/bin/python -m pytest -q`
