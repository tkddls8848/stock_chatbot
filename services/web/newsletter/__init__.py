"""뉴스레터: 로그인 계정이 확인한 이메일로 하루 한 번 시장 요약을 보낸다.

규칙은 `code_guide.md`의 「개인 화면」 표와 「공유 저장소」에 있다.

- `subscription` `users/<계정키>/newsletter.json`의 주소·확인 코드·마지막 발송일
- `mailer`       SMTP 발송(TLS 필수). 확인 코드와 다이제스트가 같이 쓴다
- `digest`       공개 산출물(`market.json`·`news.json`)로 만드는 그날의 편지 한 통
- `routes`       `/api/account/newsletter` — 구독·확인·해지
- `send`         매일 도는 발송 one-shot — `python -m services.web.newsletter.send`
"""
