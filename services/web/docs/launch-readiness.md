# 상용 출시 준비 (launch readiness)

목표: `https://nunchi.live`를 **상용 서비스로 내놓아도 부끄럽지 않은 완성도**로 만든다.
`code_guide.md`의 제품 결정(DB·회원가입·SPA·실시간 갱신 없음, 공개 화면 GET 전용,
출처 서비스 이름 비노출, 라이트 전용, 쉬운 한국어)은 그대로 두고 그 안에서 끌어올린다.
항목이 끝나면 지우고, 전부 끝나면 이 파일째 지운다.

## 기준선 (2026-09-25 실측)

| 영역 | 상태 |
|---|---|
| 테스트 | venv 전체 pytest 2 실패(`test_shorts_panel.py`, Windows 하위 프로세스 환경), ruff 통과 |
| 보안 헤더 | HSTS·CSP·`X-Content-Type-Options`·`Referrer-Policy`·`frame-ancestors` 전부 없음. `Server: Caddy`+`uvicorn` 두 줄 노출 |
| HTTP | 화면 `HEAD`가 405(링크 미리보기·가용성 감시가 실패로 본다). 404는 JSON `{"detail":"Not Found"}`. favicon 404 |
| 신뢰·법적 고지 | 이용약관·개인정보 처리 안내 화면 없음. 면책은 꼬리말 한 줄 |
| 공유 | `meta description`·Open Graph 없음 — 카카오톡·슬랙 미리보기가 빈 카드 |
| 줄글 브리프 | 매 주기 5그룹 중 1~3그룹 실패. 원인은 거의 전부 검증 문구 "주제 나열 대신 전망의 차이…"(교정 1회 후에도 실패) |
| 시장상황 보고서 | 48시간에 5건이 `highlight index repeats`·`highlight missing title`로 통째 실패 |
| 봇 재기동 | `stock-chatbot.service: Failed with result 'timeout'` 3회(정지 제한 시간 초과) |
| 검색 주석 | 하루 Neurons 예산에 걸려 색인 938/18,824건. 상위 거래량부터 채우는지 확인 필요 |

## 출시 기준 (관찰 가능한 합격선)

1. **품질 게이트** — venv 전체 pytest·`shorts/tests`·ruff가 모두 통과한다.
2. **보안** — 공개 응답에 HSTS(Caddy), CSP(인라인은 해시 또는 nonce, `unsafe-eval` 금지),
   `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy`,
   `frame-ancestors 'none'`이 붙고 서버 버전 헤더가 하나 이하다. 화면이 CSP 위반 없이 그려진다.
3. **HTTP 위생** — 모든 공개 화면·API가 `HEAD`에 200, 모르는 주소는 한국어 HTML 404
   (API 경로는 JSON 유지), 처리하지 못한 예외는 스택을 드러내지 않는 500. favicon이 있다.
4. **신뢰** — `/terms` 한 화면에 이용 조건·투자 권유 아님·자료 출처 성격·개인정보 처리
   (공개 화면은 개인정보를 받지 않음, `/portfolio`는 운영자 1인 전용)를 쉬운 한국어로 적고
   모든 화면 꼬리말에서 연결한다.
5. **공유·접근성** — 화면마다 `meta description`·Open Graph 제목·설명, 360px 폭에서 가로
   스크롤 없음, 키보드 포커스가 보이고 입력칸에 라벨이 있다.
6. **자료 품질** — 줄글 브리프 그룹 실패율을 실측 대비 절반 이하로, 시장상황 보고서가
   하이라이트 한 건의 형식 오류로 통째 버려지지 않는다(비용 증가 없이, 엄격 검증 원칙 유지).
7. **운영** — 봇 재기동이 제한 시간 안에 끝난다. `verify-app.sh`가 브리프·트렌드·검색 주석의
   신선도를 점검한다.
