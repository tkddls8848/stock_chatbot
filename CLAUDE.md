# stock_chatbot

**작업을 시작하기 전에 `code_guide.md`를 읽는다.** 이 저장소의 코드·구조·검증·
리팩터링 기준이 전부 거기 있고, 판단이 부딪히면 그 문서가 이긴다.

이 파일은 포인터다. 규칙을 여기 옮겨 적지 않는다 — 두 벌이 되면 갈라진다.

## 최소한 이것만은

```bash
python -m services.telegram_bot.main        # 봇 (저장소 루트에서)
python -m pytest -q                # 테스트
python -m pytest -q shorts/tests   # 쇼츠는 루트 pytest에 안 잡힌다
python -m ruff check services conftest.py tests
```

- 저장소 루트가 import root다. 진입점은 전부 루트에서 `-m`으로 부른다.
- **기능은 다른 기능을 import하지 않는다.** `services/telegram_bot/tests/test_feature_isolation.py`가 강제한다.
- 시각은 그 모듈의 `core/clock.py`가 주는 `now()`·`today()`만 쓴다(ruff `DTZ`가 막는다).
- 상태 파일은 그 모듈의 `core/storage.py`가 주는 원자적 쓰기로만 저장한다.
- **모듈 경계를 넘는 공용 계층을 만들지 않는다.** 공유는 모듈 안에서만 한다.

나머지는 `code_guide.md`에 있다.
