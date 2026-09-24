"""개인 화면(`/portfolio`): 한 사람의 전체 자산 포트폴리오 어드바이저.

주식·채권·예적금·부동산을 입력하고, 관심종목을 관리하고, 요청할 때 조언을 받는다.
규칙은 `code_guide.md`의 「개인 화면」과 「공유 저장소」에 있다.

- `auth`        비밀번호 하나로 여는 간단한 잠금(세션 저장소 없는 HMAC 쿠키)
- `store`       `storage/portfolio/`의 자산·관심종목·조언 파일
- `market_data` 금감원 예적금 금리·한국은행 ECOS 금리·국토부 실거래가
- `diagnosis`   규칙 진단 — 숫자는 여기서만 만든다
- `advisor`     진단을 해석하는 LLM 한 단락
- `service`     조언 생성의 잠금·하루 상한·저장
- `routes`      `/api/portfolio/*` REST 라우트
"""
