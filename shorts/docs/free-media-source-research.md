# 유튜브 쇼츠 무료 이미지·영상 소스 조사

조사일: 2026-09-05  
적용 대상: `polymarket_shorts` 일 1회 자동 제작 파이프라인

## 결론

`Pexels(주력 영상) → Pixabay(보조 영상·이미지) → Wikimedia Commons(실제 사건·인물의 편집 보도용)` 순서가 가장 적합합니다. NASA는 우주·기후·과학 주제에만 조건부로 사용합니다.

Unsplash는 이미지 품질은 좋지만 API 통합 시 핫링크, 다운로드 추적, 사진가와 Unsplash 링크 표시가 필요해 오프라인 영상 렌더링에는 효율이 떨어집니다. Coverr는 현재 공식 문서 사이에 상업 API 이용 조건과 표시 의무가 충돌하므로 서면 확인 전에는 비활성화하는 편이 안전합니다. Mixkit은 좋은 세로 영상이 있지만 자산별 Free/Restricted 라이선스가 다르고 공식 공개 API를 확인하지 못했으므로 수동 승인 라이브러리로만 권장합니다.

중요한 전제가 있습니다. 무료 스톡을 사용할 권리가 있어도 YouTube 수익화가 자동으로 보장되지는 않습니다. YouTube는 반복 템플릿, 최소한의 변형만 가한 스톡 영상, 단순 이미지 슬라이드쇼를 비진정성 또는 재사용 콘텐츠로 판단할 수 있습니다. 반대로 원본 데이터 분석, 고유한 논평, 스토리라인, 실질적인 시청각 편집을 더한 영상은 허용 사례에 가깝습니다. [`YouTube 채널 수익화 정책`](https://support.google.com/youtube/answer/1311392?hl=en-EN)

## 공식 정책 비교

표의 ‘변경 위험’은 공식 약관의 철회·변경 조항, 라이선스의 비가역성, 파일별 조건, 제3자 권리 위험을 종합한 조사상 판단입니다.

| 제공처 | 상업적 YouTube | 출처 표시 | API·요금·기본 한도 | 세로 지원 | 자동 다운로드 | 변경 위험 | 권고 |
|---|---|---|---|---|---|---:|---|
| **Pexels** | 가능. 사진·영상 모두 상업 이용 허용 | 콘텐츠 라이선스는 불필요. **API 지침은 Pexels 링크를 요구**하고 제작자 표시는 가능한 경우 권장 | 무료 API 키. 200회/시간, 20,000회/월. 승인 시 무료 상향 가능 | 사진·영상 검색 모두 `portrait` 지원 | 제공된 이미지·`video_file` URL 이용 가능 | 중간-낮음 | **1순위** |
| **Pixabay** | 가능. 단독 재판매가 아닌 해설·그래픽과 결합한 영상은 새 창작물에 해당 | 일반 이용은 불필요. API 검색 결과에는 출처 표시를 요청 | 무료 API 키. 100회/60초. 결과 24시간 캐시, 대량 자동 쿼리·체계적 다운로드 금지 | 이미지는 `vertical` 필터. 영상은 공식 방향 필터가 없어 크기로 후처리 | 이미지는 사용 시 로컬 다운로드 필요. 영상도 서버 저장 권장 | 중간 | **2순위** |
| **Wikimedia Commons** | 파일의 개별 라이선스가 허용하는 범위에서 가능 | CC BY/BY-SA는 저자·라이선스 표시. PD/CC0는 보통 법적 의무가 없지만 출처 권장 | 키 없는 Action API. 2026년 기준 식별 없음 10회/분, 적합한 User-Agent 200회/분 | 검색 필터 없음. `width/height`로 선별 후 크롭 | 파일 URL로 가능 | 중간-높음 | **조건부 3순위** |
| **NASA Images** | 사실적·편집적 사용은 대체로 가능. 보증·협찬 암시 금지 | NASA를 출처로 인정해야 함 | 키 없는 REST API. 공개 문서에 요금·고정 쿼터 미기재 | 방향 필터 없음. 크기로 후처리 | `/asset/{nasa_id}` manifest로 가능 | 중간-높음 | 우주·기후에만 |
| **Unsplash** | 이미지 상업 이용 가능 | 일반 라이선스는 불필요하지만 **API는 사진가·Unsplash 표시와 링크가 필수** | 무료. Demo 50회/시간, Production 승인 후 1,000회/시간 | 이미지 `portrait` 지원 | API 이미지 URL 핫링크 및 다운로드 이벤트 호출 필요 | 중간 | 기본 비활성 |
| **Coverr** | 콘텐츠 라이선스는 상업 이용을 허용하나 API 문구가 상충 | 무료 다운로드 및 API 표시 문구도 공식 페이지 내부에서 불일치 | Demo 50회/시간 무료. Production 2,000회/시간은 Pro/Ultimate 유료 | `is_vertical:true` 지원 | `mp4_download` 및 다운로드 통계 호출 지원 | **높음** | 서면 확인 전 제외 |
| **Mixkit** | **Free License 자산만** 가능. Restricted는 개인·비상업용 | Free License는 불필요 | 공식 공개 API를 확인하지 못함 | 세로 영상 제공 | 웹 수동 다운로드만 권장; 스크래핑 금지 | 높음 | 수동 큐레이션만 |

### 1. Pexels

Pexels는 사진과 영상을 상업적으로 사용할 수 있고, 다운로드 시 부여되는 라이선스는 비독점·전 세계·무상·비가역·영구로 규정됩니다. 단순 재판매는 금지되지만, 내레이션·자막·차트·전환과 결합한 쇼츠는 약관이 설명하는 새 창작물에 가깝습니다. [`Pexels 이용약관`](https://www.pexels.com/terms-of-service/), [`상업 이용 안내`](https://help.pexels.com/hc/en-us/articles/360042295214-Can-I-use-the-photos-and-videos-for-a-commercial-project)

API는 사진과 영상을 모두 제공하며 사진·영상 검색에 `portrait` 방향 필터가 있습니다. 무료 기본 한도는 시간당 200회 및 월 20,000회이므로 하루 1편 제작에는 충분합니다. 일반 라이선스는 출처 표시를 요구하지 않지만 API 문서는 API 사용 시 Pexels 링크를 눈에 띄게 제공하고 가능한 경우 제작자를 표시하라고 안내합니다. [`Pexels API 문서`](https://www.pexels.com/api/documentation/)

Polymarket 정치 콘텐츠에서는 특별한 제한이 중요합니다. Pexels는 식별 가능한 사람이 나온 콘텐츠를 정치적 정책·관점과 연결하는 것을 허용하지 않습니다. 따라서 정치 장면에는 투표함, 의회 외관, 지도, 국기 같은 비인물 이미지를 사용하고 정치인 사진은 Pexels에서 가져오지 않아야 합니다. [`Pexels 정치적 사용 안내`](https://help.pexels.com/hc/en-us/articles/360043229813-Can-I-use-photos-and-videos-from-Pexels-in-a-political-campaign)

### 2. Pixabay

Pixabay의 Content License는 상업 이용을 허용하고, 텍스트·그래픽·다른 영상과 조합해 새 창작물을 만드는 것을 허용 사례로 설명합니다. 일반 이용에는 출처 표시가 필요 없습니다. 다만 인물·상표·예술작품·건축물 등 제3자 권리는 별도이며, 특히 식별 가능한 사람이 포함된 콘텐츠를 정치적 맥락에 사용하는 것을 금지합니다. [`Pixabay 이용약관`](https://pixabay.com/service/terms/)

API는 사진과 영상을 제공하고 기본 한도는 60초당 100회입니다. 검색 결과를 24시간 캐시해야 하며, 많은 자동 쿼리나 체계적 대량 다운로드는 허용되지 않습니다. 이미지의 영구 핫링크는 금지되어 실제 사용 파일은 로컬로 다운로드해야 하며, 영상도 서버 저장을 권장합니다. 이미지에는 `vertical` 검색 필터가 있지만 영상 API에는 방향 필터가 문서화되어 있지 않으므로 응답의 너비·높이를 비교해 세로 영상을 골라야 합니다. [`Pixabay API 문서`](https://pixabay.com/api/docs/)

### 3. Wikimedia Commons

Commons는 상업적 재사용이 가능한 파일을 모으지만 하나의 통일된 라이선스가 아닙니다. 파일마다 저자 표시, 라이선스 링크, 동일조건변경허락 의무가 다르며 Wikimedia는 각 파일의 저작권 상태가 정확하다고 보증하지 않습니다. 상표·초상권 같은 비저작권 제한도 별도로 남습니다. [`Commons 재사용 안내`](https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia), [`비저작권 제한 안내`](https://commons.wikimedia.org/wiki/Commons:Non-copyright_restrictions/en)

자동화에서는 `imageinfo`의 URL, MIME, 크기, `extmetadata`를 가져올 수 있습니다. 다만 `extmetadata`는 비용이 큰 속성이므로 적은 수의 후보에만 요청해야 합니다. 2026년 도입된 한도는 식별 정보가 없는 요청 10회/분, 적합한 User-Agent를 가진 봇 요청 200회/분입니다. [`MediaWiki Imageinfo`](https://www.mediawiki.org/wiki/API:Imageinfo), [`Wikimedia API 한도`](https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits)

자동 허용 목록은 `Public Domain`, `CC0`, `CC BY 2.0/3.0/4.0`으로 제한하는 것을 권장합니다. CC BY-SA는 결과 영상 전체에 미칠 수 있는 동일조건 의무를 별도로 검토하지 않는 한 제외하고, NC·ND 및 라이선스 불명확 파일은 항상 제외해야 합니다.

### 4. NASA Image and Video Library

NASA 콘텐츠는 미국에서 대체로 저작권 대상이 아니며 사실적·정보적 용도로 사용할 수 있습니다. 그러나 NASA를 출처로 밝혀야 하고, NASA의 보증을 암시하면 안 됩니다. NASA 로고, 식별 가능한 직원·우주비행사, 제3자 저작물은 추가 허가가 필요할 수 있습니다. [`NASA 이미지·미디어 이용 지침`](https://www.nasa.gov/nasa-brand-center/images-and-media/)

공식 REST API는 이미지·영상·오디오 검색과 자산 manifest 다운로드를 제공하지만 방향 필터나 공개 쿼터는 문서에 없습니다. 우주·기후·발사체 등 NASA와 직접 관련된 사실 설명에만 쓰고, 사람 얼굴과 로고가 없는 자산을 우선해야 합니다. [`NASA Image Library API 문서`](https://images.nasa.gov/docs/images.nasa.gov_api_docs.pdf)

### 5. Unsplash

일반 Unsplash 라이선스는 이미지를 상업적으로 무료 사용하도록 하며 출처 표시를 요구하지 않습니다. 그러나 API 사용자는 사진가와 Unsplash를 표시하고 사진가 프로필로 링크해야 하며, API가 반환한 이미지 URL을 사용하고 다운로드에 준하는 이벤트마다 `download_location` 엔드포인트를 호출해야 합니다. [`Unsplash 라이선스`](https://unsplash.com/license), [`API 지침`](https://help.unsplash.com/en/articles/2511245-unsplash-api-guidelines), [`API 약관`](https://unsplash.com/api-terms)

사진만 제공하고 Demo 한도는 시간당 50회, Production 승인 후 1,000회입니다. `portrait` 필터는 지원합니다. 기술적으로 자동화할 수 있지만, 완성 MP4 안의 표시와 클릭 가능한 설명란 링크를 함께 관리해야 하므로 Pexels·Pixabay보다 구현 대비 가치가 낮습니다. [`Unsplash API 문서`](https://unsplash.com/documentation)

### 6. Coverr

Coverr의 콘텐츠 라이선스는 영상과 음악의 상업 이용을 허용합니다. 그러나 같은 공식 라이선스 페이지의 요약은 무료 다운로드에 표시가 필요하다고 하고 장문 조항은 불필요하다고 설명합니다. 별도의 API 소개 문서는 무료 접근을 비상업 이용으로 표현하는 반면, 현재 개발자 페이지는 API 콘텐츠가 상업적으로 라이선스된다고 설명합니다. [`Coverr 라이선스`](https://coverr.co/license/), [`API 소개`](https://api.coverr.co/docs), [`현재 API 요금 안내`](https://coverr.co/developers?ctx=header_navigation)

API는 `is_vertical` 필드·필터와 MP4 다운로드를 제공하고 다운로드 통계 호출을 필수로 요구합니다. Demo는 시간당 50회이며 Production 2,000회/시간은 유료 Pro/Ultimate 구독이 필요합니다. 기술적 적합성은 높지만 약관 문구가 해소되기 전까지 자동 게시에는 쓰지 않는 것이 좋습니다. [`Coverr 영상 API`](https://api.coverr.co/docs/videos/), [`API 한도`](https://api.coverr.co/docs/start/)

### 7. Mixkit

Mixkit은 세로 영상을 포함한 무료 영상이 많고 Free License 자산은 YouTube 등 상업 프로젝트에 사용할 수 있으며 표시도 필요 없습니다. 하지만 같은 카탈로그에 개인·비상업용 Restricted License 영상이 섞여 있으므로 개별 자산의 라이선스를 확인해야 합니다. [`Mixkit 라이선스`](https://mixkit.co/license/), [`무료 영상 안내`](https://mixkit.co/free-stock-video/)

공식 공개 API는 확인하지 못했습니다. 웹페이지를 자동 스크래핑하지 말고, 사람이 Free License를 확인한 파일만 `assets/approved/` 같은 내부 폴더에 넣어 사용하는 방식을 권장합니다.

## `polymarket_shorts` 적용 권고안

### 공급자 우선순위

```text
장면 생성
  ├─ 일반 경제·기술·산업 → Pexels portrait video
  │                         └─ 실패 → Pixabay video/image
  ├─ 정치·선거             → 자체 차트·지도·기관 외관
  │                         └─ 실제 인물 필요 → Commons 편집용 자산 + 수동 검토
  ├─ 우주·기후             → NASA(비인물·비로고) → Pexels
  └─ 모든 공급자 실패       → Polymarket 데이터 기반 자체 그래픽
```

현재 `media.py`는 Commons 이미지만 내려받는 초기 구현이며 `pipeline.py`에는 아직 연결되지 않았습니다. 다음 구현에서는 공급자 인터페이스를 분리해 Pexels와 Pixabay를 먼저 연결하고, Commons를 조건부 fallback으로 바꾸는 것이 좋습니다.

### 필수 자산 원장

다운로드할 때마다 영상 폴더에 `asset-manifest.json`을 저장합니다.

```json
{
  "provider": "pexels",
  "asset_id": "12345",
  "media_type": "video",
  "query": "global trade shipping containers",
  "source_page_url": "https://www.pexels.com/video/...",
  "creator": "Creator Name",
  "creator_url": "https://www.pexels.com/@creator",
  "license_name": "Pexels License",
  "license_url": "https://www.pexels.com/license/",
  "terms_checked_at": "2026-09-05T00:00:00Z",
  "downloaded_at": "2026-09-05T00:00:00Z",
  "sha256": "...",
  "width": 1080,
  "height": 1920,
  "duration_seconds": 12.4,
  "attribution_text": "Video by Creator Name via Pexels",
  "review_required": false
}
```

원장은 나중에 파일이 삭제되거나 라이선스 페이지가 바뀌거나 Content ID 이의 제기가 들어왔을 때 사용 권한을 입증하는 자료가 됩니다. 라이선스가 비가역으로 표현된 서비스라도 다운로드 시점의 원문 URL, 확인 시각, 파일 해시를 보존해야 합니다.

### 자동 선택 규칙

1. 자산의 페이지 URL과 API ID가 없는 결과는 거부합니다.
2. 세로 영상은 `height > width`, 최소 1080×1920 또는 크롭 가능한 고해상도만 선택합니다.
3. 영상 한 편에 동일 자산을 재사용하지 않고, 최근 90일 사용 자산도 제외합니다.
4. 인물·로고·상표가 탐지되면 `review_required=true`로 만들고 무인 게시에서 제외합니다.
5. 정치·건강·범죄·전쟁 장면에는 일반 스톡의 식별 가능한 사람을 절대 자동 배치하지 않습니다.
6. Commons는 PD/CC0/CC BY 허용 목록과 유효한 저자·라이선스 URL을 모두 만족해야 합니다.
7. Pixabay API 응답은 24시간 캐시하고, 모든 공급자에 429 재시도·지수 백오프를 적용합니다.
8. 원본 파일을 최종 영상에 그대로 길게 쓰지 않고, 데이터 차트·헤드라인·확률 변화·내레이션과 결합합니다.

### 크레딧 정책

법적으로 불필요한 경우도 모두 표시하는 단일 정책이 운영상 가장 간단합니다.

- 영상 설명란 끝에 `Visual sources` 블록 생성
- `제작자 — 제공처 — 자산 페이지 — 라이선스` 순서로 기록
- Commons의 CC BY는 영상 설명란 외에 마지막 1~2초 크레딧 카드에도 축약 표시
- NASA는 `Source: NASA`와 자산 페이지 기록
- 한 편에 너무 많은 표시가 생기지 않도록 서로 다른 외부 자산은 6개 이하 권장

### YouTube 수익화 안전장치

Shorts의 무료 스톡 사용 권한과 수익화 심사는 별개입니다. YouTube는 최소 변형 재업로드, 반복 템플릿, 설명 가치가 낮은 이미지 슬라이드쇼, 대량생산형 AI 콘텐츠를 수익화 부적격 사례로 듭니다. 반면 고유한 해설·스토리·시청각 효과로 실질적으로 변형된 편집물은 허용 사례에 포함합니다. [`YouTube 수익화 정책`](https://support.google.com/youtube/answer/1311392?hl=en-EN)

따라서 각 쇼츠는 다음 요소를 가져야 합니다.

- 당일 Polymarket 확률과 전일 대비 변화로 만든 자체 차트
- “무엇이 바뀌었나 → 왜 중요한가 → 경영진이 볼 신호”라는 고유한 분석 구조
- 장면마다 다른 데이터·문구·전환과 실제 기사/시장 맥락
- 스톡 영상은 짧은 B-roll로만 사용하고 화면의 주된 정보는 자체 시각화로 구성
- 동일 내레이션 문형과 동일 영상 배열의 반복 방지

또한 1분을 넘는 Shorts에 저작권 claimed content가 포함되면 차단될 수 있습니다. 라이선스가 있는 스톡도 잘못된 Content ID 청구가 발생할 수 있으므로 자산 원장을 보존하고, 배경음악은 별도의 검증된 정책을 사용해야 합니다. [`YouTube Shorts 수익화 정책`](https://support.google.com/youtube/answer/12504220?hl=en), [`Content ID 작동 방식`](https://support.google.com/youtube/answer/2797370?hl=en)

## 최종 선택

- **바로 자동 연동:** Pexels + Pixabay
- **엄격한 필터로 연동:** Wikimedia Commons
- **주제 제한 연동:** NASA
- **기본 비활성:** Unsplash
- **서면 확인 전 제외:** Coverr
- **수동 승인 자산만:** Mixkit

이 조합은 하루 1편의 요청량에서 API 비용을 0원으로 유지하면서 세로 영상 확보율과 라이선스 추적 가능성을 높입니다. 가장 큰 운영 위험은 API 한도가 아니라 정치·인물·브랜드의 제3자 권리와 YouTube의 반복형 콘텐츠 판단입니다.
