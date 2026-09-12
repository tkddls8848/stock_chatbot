# 무료 이미지·영상 소스 심층 조사 — 내부 근거 원본

- 대상: `polymarket_shorts` 자동 제작 파이프라인
- 조사일: 2026-09-05 (Asia/Seoul)
- 독자: 프로젝트 운영자 및 구현 담당자
- 범위: Pexels, Pixabay, Unsplash, Wikimedia Commons, NASA Image and Video Library, Coverr, Mixkit, YouTube 정책
- 판단 기준: 상업적 YouTube 이용, 표시 의무, API 비용·한도, 세로 자산, 자동 다운로드, 정책 변경 및 제3자 권리 위험
- 제외: 유료 전용 스톡 서비스, 음악 라이선스, 법률 의견

## 직접 결론

주력은 Pexels, 보조는 Pixabay가 가장 현실적이다. 둘 다 사진과 영상을 API로 제공하며 하루 1편 규모는 기본 한도보다 매우 작다. API로 얻은 자산은 라이선스상 표시가 면제되더라도 API 문서의 출처 표시 요청/지침을 따라 영상 설명란과 자산 원장에 크레딧을 남긴다. 정치 장면에는 두 서비스의 식별 가능한 인물·브랜드 자산을 쓰지 않는다.

Wikimedia Commons는 실제 인물·기관·사건의 편집 보도용 보완재다. 파일별 라이선스가 다르므로 Public Domain, CC0, CC BY만 허용하고 CC BY-SA·NC·ND 및 불명확한 항목은 자동 제외한다. NASA는 우주·기후·과학 주제의 사실적 설명에만 제한 사용한다. Unsplash는 이미지 품질은 좋지만 API의 핫링크·다운로드 추적·표시 의무가 오프라인 렌더러에 번거롭다. Coverr는 공식 문서끼리 상업 API 이용 및 무료 다운로드 표시 의무가 충돌하므로 서면 확인 전 비활성화한다. Mixkit은 자산별 Free/Restricted 구분이 있고 공개 API를 확인하지 못해 수동 큐레이션 전용으로 둔다.

## 핵심 판단

1. 무료 콘텐츠 라이선스와 무료 API 약관은 별개다. Pexels와 Unsplash는 일반 라이선스상 표시가 필요 없지만 API 통합에는 별도 표시 지침이 있다.
2. 라이선스가 있어도 초상권·상표권·건축물·제3자 저작권은 자동 해결되지 않는다.
3. Polymarket의 정치 주제를 일반 스톡 인물 이미지와 결합하면 인물의 정치적 지지나 연관성을 암시할 위험이 있다.
4. YouTube 수익화는 저작권 허용 여부와 별개다. 반복 템플릿, 단순 슬라이드쇼, 최소 변형 스톡 영상은 재사용/비진정성 콘텐츠로 판단될 수 있다.
5. 1분을 넘는 Shorts는 claimed content가 있으면 차단될 수 있으므로 모든 다운로드 증빙과 해시를 보존하고, 특히 음악은 별도 검증한다.

## 근거 매트릭스

| 주장 | 근거 | 신뢰도 | 충돌/제약 |
|---|---|---:|---|
| Pexels 사진·영상은 상업 이용 가능 | Pexels Terms §5, Help Center commercial-use article | 높음 | 제3자 권리 및 정치 맥락 제한 |
| Pexels API는 무료, 200회/시간·20,000회/월 | Pexels API docs | 높음 | API 표시 지침, 약관 변경 가능 |
| Pixabay는 사진·영상 API, 100회/60초 | Pixabay API docs | 높음 | 24시간 캐시, 대량 자동 쿼리 금지 |
| Unsplash API는 이미지 전용이고 표시·핫링크·다운로드 추적 필요 | Unsplash API docs/guidelines/terms | 높음 | 일반 라이선스의 무표시 원칙과 API 의무가 다름 |
| Commons는 상업 재사용 가능하지만 파일별 조건이 다름 | Commons reuse guide | 높음 | 정확성 보증 없음, 초상·상표 등 별도 |
| Wikimedia 식별 User-Agent는 200회/분 | Wikimedia API rate-limit docs (2026) | 높음 | 실험 중이며 변경 가능 |
| NASA 자료는 대체로 미국 저작권 대상이 아니며 사실적 사용 가능 | NASA media guidelines | 높음 | NASA 로고·직원·제3자 자료·보증 암시 제한 |
| Coverr API 조건에 공식 문서 충돌이 있음 | Coverr license/API docs/developer page | 높음 | 서면 확인 필요 |
| Mixkit Free 자산은 상업 사용 가능, Restricted는 불가 | Mixkit license/video page | 높음 | 자산별 확인, 공개 API 미확인 |
| YouTube는 반복·대량생산형 자동 콘텐츠와 최소 변형 재사용 콘텐츠의 수익화를 제한 | YouTube monetization policy | 높음 | 채널 전체를 검토할 수 있음 |

## 출처 원장

모든 문서는 2026-09-05에 확인했다.

- Pexels, “Pexels Terms of Service,” last updated 2024-11-15: https://www.pexels.com/terms-of-service/
- Pexels, “API Documentation”: https://www.pexels.com/api/documentation/
- Pexels, “Can I use the photos and videos for a commercial project?”: https://help.pexels.com/hc/en-us/articles/360042295214-Can-I-use-the-photos-and-videos-for-a-commercial-project
- Pexels, “Can I use photos and videos from Pexels in a political campaign?”: https://help.pexels.com/hc/en-us/articles/360043229813-Can-I-use-photos-and-videos-from-Pexels-in-a-political-campaign
- Pixabay, “API Documentation”: https://pixabay.com/api/docs/
- Pixabay, “Terms of Service,” last updated 2024-11-18: https://pixabay.com/service/terms/
- Unsplash, “License”: https://unsplash.com/license
- Unsplash, “API Documentation”: https://unsplash.com/documentation
- Unsplash, “API Guidelines”: https://help.unsplash.com/en/articles/2511245-unsplash-api-guidelines
- Unsplash, “API Terms”: https://unsplash.com/api-terms
- Wikimedia Commons, “Reusing content outside Wikimedia”: https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia
- Wikimedia Commons, “Non-copyright restrictions”: https://commons.wikimedia.org/wiki/Commons:Non-copyright_restrictions
- MediaWiki, “Wikimedia APIs/Rate limits”: https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits
- MediaWiki, “API:Imageinfo”: https://www.mediawiki.org/wiki/API:Imageinfo
- NASA, “Images and Media Usage Guidelines”: https://www.nasa.gov/nasa-brand-center/images-and-media/
- NASA, “images.nasa.gov API Documentation,” release 1.22.0, 2023-01-06: https://images.nasa.gov/docs/images.nasa.gov_api_docs.pdf
- Coverr, “License”: https://coverr.co/license/
- Coverr, “Before you start”: https://api.coverr.co/docs
- Coverr, “How to access Coverr's Free Videos API”: https://api.coverr.co/docs/start/
- Coverr, “Listing free videos”: https://api.coverr.co/docs/videos/
- Coverr, “Content API”: https://coverr.co/developers
- Mixkit, “License”: https://mixkit.co/license/
- Mixkit, “Free Stock Video”: https://mixkit.co/free-stock-video/
- YouTube Help, “YouTube channel monetization policies”: https://support.google.com/youtube/answer/1311392
- YouTube Help, “YouTube Shorts monetization policies”: https://support.google.com/youtube/answer/12504220
- YouTube Help, “How Content ID works”: https://support.google.com/youtube/answer/2797370

## 조사 한계 및 중단 기준

- 한국에서의 구체적 초상권·상표권 판단은 자산과 사용 맥락별 법률 검토가 필요하다.
- API 문서에 없는 비공개 운영 한도나 내부 승인 기준은 확인할 수 없다.
- NASA API 문서는 공개 쿼터나 SLA를 명시하지 않는다.
- Mixkit의 공식 공개 API는 공식 사이트와 검색에서 확인하지 못했다. 이는 API가 절대 존재하지 않는다는 단정이 아니다.
- Coverr의 상충 문구가 해소되지 않아 추가 검색보다 운영사 서면 확인이 더 유효하다.
- 핵심 의사결정 슬롯에 1차 공식 근거가 확보됐고 추가 검색이 추천 조합을 바꿀 가능성이 낮아 조사를 종료했다.
