# 쇼츠 배경을 Seedance 생성 클립으로 바꾸는 구현 계획

- 대상: `polymarket_shorts` 일 1회 자동 제작 파이프라인
- 작성일: 2026-09-26 (Asia/Seoul)
- 독자: 프로젝트 운영자 및 구현 담당자
- 범위: 이슈 장면의 정지 배경을 짧은 생성 영상 클립으로 바꾸는 설계, 비용·운영 조건, 단계별 작업, 검증 기준
- 제외: 원고·TTS·자막·선정 로직(바뀌지 않는다), 유튜브 업로드 자동화, 법률 의견
- 상태: **단계 1~4 구현 완료(2026-09-26), 꺼진 채 보류.** 운영자가 fal을 쓰지 않기로
  했다(월 약 $290). 코드는 기본 꺼짐으로 남겨 둔다 — 켜지 않으면 비용·동작 영향이 없다.
  다시 진행하려면 제공처부터 정한다(BytePlus 등): `clips._generate_clip`의 호출부만 바꾸면
  렌더(`clip_render.py`)·캐시·검수 표시는 그대로 쓴다. 그 뒤 사흘 치 실측(아래 검증 기준)과
  단계 5(음악) 결정이 남는다

## 직접 결론

화질을 올리는 자리는 배경 한 곳이다. 지금 영상은 장면마다 Pillow가 그린 정지 PNG를
FFmpeg가 이어 붙이고, 움직임은 카운트업·선택지 쌓기·±7px 드리프트뿐이다. 이슈 장면의
배경을 **이미 만들고 있는 flux 정지 이미지를 입력으로 한 Seedance 이미지→영상 클립(8초, 720p,
9:16)** 으로 바꾸고, 그 클립을 장면 길이만큼 반복 재생한 위에 지금의 텍스트 레이어를 얹는다.
글자·인물을 배제한 그림 통제와 이슈별 캐시는 그대로 두므로 자연어 수정·재렌더에서 클립을
다시 만들지 않는다. 실패하면 오늘처럼 정지 배경으로 그날 제작을 이어간다.

Artlist는 이 파이프라인에 **스톡 푸티지 소스로 붙일 수 없다.** Artlist MCP는 AI 생성(이미지·
영상·음악)만 열려 있고 카탈로그 검색은 MCP로 제공하지 않으며, 엔터프라이즈 API는 음악
전용이다. Artlist 크레딧으로 Seedance를 부르는 길(MCP)은 있지만 대화형 클라이언트의 OAuth
로그인을 전제로 해서 systemd 무인 실행에 맞지 않는다. 생성은 REST API 키를 주는 제공처
(fal 권장)로 직접 부르고, Artlist는 음악·수동 푸티지 보충용으로 별도 단계에 둔다.

## 현재 구조에서 바뀌는 자리

| 파일 | 지금 | 바뀌는 것 |
|---|---|---|
| `src/polymarket_shorts/media.py` | `_generate_background`가 Cloudflare flux로 정사각 PNG를 만들어 9:16으로 자른다. `backgrounds_for`가 이슈 텍스트 해시로 캐시한다 | 같은 해시 이름의 `.mp4` 클립을 flux PNG에서 이미지→영상으로 만든다. 클립이 없거나 실패하면 PNG를 돌려준다 |
| `src/polymarket_shorts/render.py` | `render_frame`이 배경까지 합성한 PNG를 만들고, concat demuxer 한 번으로 인코딩한다(`render_video`) | 배경이 클립인 장면은 `render_frame(transparent=True)`로 텍스트 레이어만 만들고, 클립을 반복 재생한 위에 얹는다. 정지 배경 장면은 지금 경로 그대로다 |
| `src/polymarket_shorts/config.py` | `generated_backgrounds`, `image_model` | `generated_clips`, `video_model`, `video_api_key` 추가 |
| `src/polymarket_shorts/pipeline.py` | `scenario.json`의 `visuals`에 `source: "GPT Image / built-in"` | 클립이면 `source: "Seedance image-to-video"`, `kind: "clip"` |
| `src/polymarket_shorts/review.py` | 검수 한 장 | 합성 배경을 썼다는 줄과 게시 시 "변경·합성 콘텐츠" 표시 안내 한 줄 |
| `infra/systemd/polymarket-shorts.service` | `TimeoutStartSec=20min`, `MemoryMax=768M` | `TimeoutStartSec=40min`. 메모리는 유지하고 실측 뒤 조정 |
| `.env.example`, `README.md` | 이미지 생성 항목 | 클립 생성 항목과 비용 상한 설명 |
| `tests/` | `test_generated_backgrounds.py`, `test_render.py` | 클립 생성 mock, 클립 배경 렌더 테스트 |

`hyperframes_export.py`는 정지 PNG를 복사하는 지금 동작을 유지한다. 개발용 내보내기라
클립 지원은 범위 밖이다.

## 설계

### 1. 클립 생성 (`media.py`)

- 입력은 **이미 만든 flux PNG**다. 텍스트→영상이 아니라 이미지→영상을 쓰는 이유는 셋이다.
  글자·간판·인물을 걸러 둔 정지 이미지의 통제를 그대로 잇고, 영상 입력 단가가 텍스트 입력보다
  싸며, 원제에 정치인 이름이 자주 들어오는 이 채널에서 "움직이는 인물"은 정지 인물보다 큰
  리스크다.
- 프롬프트는 `_BACKGROUND_PROMPT`의 사물·풍경 묘사에 움직임 한 문장을 덧붙인다: 아주 느린
  카메라 드리프트, 일정한 속도, 새 사물·글자·사람이 등장하지 않음, 급격한 밝기 변화 없음.
  글자 금지 문구는 그대로 둔다.
- 길이 **8초**, 해상도 **720p(720×1280)**, 비율 9:16, 오디오 없음. 15초까지 지원하지만
  비용이 길이에 비례하므로 8초로 시작하고 이음매가 거슬리면 늘린다.
- 파일은 `backgrounds/<이슈 해시>.mp4`. PNG와 같은 해시라 `--edit`·`--force`·`--plan`
  재렌더가 같은 클립을 다시 쓴다. **하루 생성 상한은 이슈 수(`max_groups`, 최대 5)** 이고
  도입 장면은 첫 이슈 클립을 함께 쓴다. 마무리 고지는 저장 정지 배경을 유지한다.
- 제공처 호출은 fal의 queue 방식(제출 → 상태 조회 → 결과 URL → 다운로드)이다. 클립당 1~3분이
  걸리므로 **이슈 다섯 개를 동시에 제출**하고 전체 대기 상한을 10분으로 둔다. 상한을 넘긴
  이슈는 PNG로 간다.
- 다운로드한 클립은 `ffprobe`로 길이·해상도·코덱을 확인한다. 720p 이하, 4초 미만, 디코딩
  실패는 버리고 PNG로 간다.
- 실패는 지금 이미지 생성과 같은 등급이다: 경고 로그와 정지 배경. 배경 하나 때문에 그날
  제작을 멈추지 않는다.

### 2. 렌더 (`render.py`)

`render_video`는 지금 "합성된 PNG 목록 → concat → 필터 한 번 → 인코딩" 한 번 호출이다.
클립 장면이 섞이면 두 단계로 나눈다.

1. **장면·비트별 구간 인코딩.** `_beats`가 주는 비트(open/card/카운트업 프레임)마다 텍스트
   레이어 PNG 하나와 길이 하나가 있다. 클립 장면은 `-stream_loop -1 -i clip.mp4`를 그 길이만큼
   자르고(`-t hold`, 장면 안에서 누적 오프셋 `-ss`로 이어지게), `scale=1080:1920:flags=lanczos`
   뒤에 PNG를 `overlay`로 얹어 중간 파일로 인코딩한다(`libx264 -preset veryfast -crf 18`,
   30fps). 정지 배경 장면은 지금처럼 합성 PNG 한 장을 `-loop 1`로 같은 형식의 중간 파일로
   만든다. 입력은 항상 **영상 하나 + 정지 이미지 하나**라, 운영 FFmpeg에서 이미지 스트림 둘을
   `fps`/`overlay`에 먹였을 때 프레임이 GB 단위로 쌓이던 문제를 피한다.
2. **최종 합성.** 중간 파일들을 concat demuxer로 잇고 `_subtitle_filter`와 `tpad`를 얹어
   지금 설정(`libx264 -preset medium -crf 20`, AAC 192k)으로 한 번 더 인코딩한다.

- `_drift_filter`는 정지 배경 구간에만 의미가 있다. 클립 장면은 자체 움직임이 있으므로 최종
  합성에서 드리프트를 빼고, 정지 장면의 드리프트는 1단계 중간 인코딩 안으로 옮긴다. 자막은
  지금처럼 마지막에 얹어 고정된다.
- 반복 이음매는 받아들인다. `reverse`·`xfade`는 프레임을 메모리에 쌓아 `MemoryMax=768M`
  안에서 위험하다. 이슈 장면이 20~35초라 8초 클립은 2~4번 돈다. 프롬프트의 "일정한 속도·
  느린 드리프트"가 이음매를 눈에 덜 띄게 하고, 그래도 거슬리면 클립 길이를 12초로 올린다.
- 720p를 1080×1920으로 키우면 약간 무르다. 스크림과 색조 블렌드가 위에 있고 글자는
  텍스트 레이어에서 원본 해상도로 그려지므로 가독성은 그대로다. `_background`의 크롭·
  반전·색조 처리 중 색조와 밝기는 FFmpeg `colorchannelmixer`/`eq`로 옮기고, 크롭 이동과
  좌우 반전은 클립에서는 뺀다(장면마다 다른 클립이라 필요 없다).
- 타임라인(`.timeline.json`)은 비트 단위 기록이 그대로다.

### 3. 설정과 비용 상한 (`config.py`)

| 환경변수 | 기본 | 뜻 |
|---|---|---|
| `SHORTS_GENERATED_CLIPS` | `false` | 켜면 이슈 장면 배경을 클립으로 만든다. 꺼져 있으면 지금과 같다 |
| `SHORTS_VIDEO_MODEL` | `bytedance/seedance-2.0/fast/image-to-video` | fal 모델 경로 |
| `SHORTS_VIDEO_API_KEY` | 빈 값 | fal API 키. 비면 클립 생성을 건너뛴다 |
| `SHORTS_CLIP_SECONDS` | `8` | 클립 길이(4~15) |

기본값을 `false`로 두는 이유는 비용이 새로 생기기 때문이다. 켜는 것은 운영자의 결정이다.
클립 수 상한은 `max_groups`가 이미 막고 있으므로 별도 카운터를 두지 않는다.

### 4. 검수와 표시

- `review.md`에 "배경: Seedance 이미지→영상 합성(이슈 n개)" 한 줄과 "게시 시 YouTube
  '변경·합성 콘텐츠' 표시" 안내를 넣는다. 정지 배경만 쓴 날에는 지금 문구다.
- 화면 하단의 ` · AI 배경` 표기는 유지한다.
- 클립에 글자·인물이 들어왔는지는 사람이 본다. 자동 검출은 두지 않는다. 대신 `--edit`로
  "n번 장면 배경을 정지로" 요청을 받으면 그 장면만 PNG로 되돌리는 편집을 허용한다
  (`workflow.py`의 허용 필드에 `background: still` 추가).

### 5. 운영

- `TimeoutStartSec`를 40분으로 올린다. 생성 대기 최대 10분 + 두 단계 인코딩 실측을 더한
  값이며, 실측 뒤 줄인다.
- 실패 재시도(15분 간격 8회)는 그대로다. 캐시 덕에 재시도가 클립을 다시 사지 않는다.
- 봇의 `/shorts` 계약(`status.py` JSON)은 바뀌지 않는다.
- 비용 기준선: 클립 5개 × 8초 = 40초/일.

| 경로 | 초당 | 하루 | 한 달 |
|---|---:|---:|---:|
| fal Seedance 2.0 fast 720p | 약 $0.24 | 약 $9.7 | 약 $290 |
| fal Seedance 2.0 표준 720p | 약 $0.30 | 약 $12.1 | 약 $360 |
| BytePlus ModelArk 480~720p(영상 입력) | 약 $0.04~0.10 | 약 $2~4 | 약 $60~120 |

BytePlus가 싸지만 계정 개설·결제·리전 조건을 확인하지 못했다. 단계 1에서 fal로 시작하고
비용이 문제면 BytePlus로 옮기는 것은 `media.py`의 호출 함수 하나 교체다.

## 단계

각 단계는 PR 하나이고, 앞 단계가 머지된 뒤 다음을 시작한다.

1. **클립 생성과 캐시** — `media.py`에 fal 이미지→영상 호출·동시 제출·검증·캐시. `config.py`
   설정. 테스트는 `test_generated_backgrounds.py` 방식으로 HTTP를 mock한다. 렌더는 아직
   PNG만 받으므로 이 단계에서 클립은 만들어지되 쓰이지 않는다.
2. **클립 배경 렌더** — `render.py` 두 단계 인코딩. 테스트는 `ffmpeg -f lavfi testsrc`로 만든
   2초 클립으로 네트워크 없이 돈다. 정지 배경 경로의 기존 테스트가 그대로 통과해야 한다.
3. **검수·표시·편집** — `review.py` 문구, `pipeline.py` `visuals` 기록, `workflow.py`
   `background: still` 편집.
4. **운영 반영** — systemd 타임아웃, `.env.example`, README. 운영 서버에서 `--force`로 사흘
   치를 만들어 아래 기준으로 본다.
5. **(별도 결정) 음악** — Artlist 트랙을 수동으로 받아 `assets/music/`에 두고
   `SHORTS_MUSIC_ENABLED`로 켠다. 내레이션 아래 -18dB 더킹, 채널을 Artlist 클리어리스트에
   등록. README의 "1분 넘는 쇼츠는 클레임이 있으면 차단" 문장을 그에 맞게 고친다. 이 단계는
   Seedance와 무관하게 따로 결정한다.

## 검증 기준

- `pytest -q shorts/tests`가 네트워크 없이 통과한다.
- 운영 서버에서 `polymarket-shorts.service` 한 번 실행이 40분 안에 끝나고, `systemctl status`의
  peak 메모리가 768M 아래다.
- 사흘 치 산출물에서 클립 장면의 글자·인물·급격한 장면 전환이 0건이다. 1건이라도 있으면
  프롬프트를 고치고 다시 사흘을 본다.
- 자막·수치·선택지 막대의 위치와 시각이 정지 배경 산출물의 `.timeline.json`과 같다.
- 한 달 비용이 위 표의 추정 ±30% 안이다.

## 결정 대기

1. 제공처: fal(권장, 단순) / BytePlus(저렴, 계정 조건 미확인).
2. 클립 길이와 등급: 8초 fast(권장) / 8초 표준 / 12초.
3. 음악 단계(5)를 진행할지, 진행하면 Artlist 구독 여부.
4. 기본값 `SHORTS_GENERATED_CLIPS=false`로 두고 운영자가 켜는 방식에 동의하는지.

## 근거

- Artlist MCP는 AI 생성 도구만 제공하고 카탈로그 검색·무제한 생성은 MCP로 지원하지 않는다:
  [Artlist MCP: Connect Claude, ChatGPT, and VS Code to Artlist](https://help.artlist.io/hc/en-us/articles/38948588333469-Artlist-MCP-Connect-Claude-ChatGPT-and-VS-Code-to-Artlist)
- Artlist 엔터프라이즈 API는 음악 검색·다운로드 전용:
  [Artlist — API Provider (APIs.io)](https://apis.io/providers/artlist/)
- Artlist 크레딧 기준 Seedance 2.0 720p 8초 약 640크레딧:
  [Generate More AI Videos and Images With the Same Credits](https://artlist.io/blog/artlist-credits-reduction/)
- fal Seedance 2.0 텍스트→영상 720p 표준 $0.3034/초, fast $0.2419/초, 9:16 지원, 1080p 없음:
  [Seedance 2.0 API (Text to Video) on fal](https://fal.ai/models/bytedance/seedance-2.0/text-to-video),
  [How to Use Seedance 2.0 | fal](https://fal.ai/learn/tools/how-to-use-seedance-2-0)
- BytePlus ModelArk Seedance 2.0 초당 $0.04~0.78, 4~15초:
  [Dreamina Seedance 2.0 tutorial | ModelArk](https://docs.byteplus.com/en/docs/modelark/2291680),
  [Seedance 2.0 API Pricing (2026)](https://anikuku.com/blog/seedance-2-api-pricing-guide-2026)
- 조사일 기준 가격이며 제공처 페이지에서 다시 확인한다. 이 문서 작성 환경에서는 Artlist·fal·
  BytePlus 페이지를 직접 열지 못해 검색 결과에 인용된 문구를 근거로 했다.
