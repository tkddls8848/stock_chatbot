# stock_chatbot

**작업을 시작하기 전에 `code_guide.md`를 읽는다.** 이 저장소의 코드·구조·검증·
리팩터링 기준이 전부 거기 있고, 판단이 부딪히면 그 문서가 이긴다.

이 파일은 포인터다. 규칙을 여기 옮겨 적지 않는다 — 두 벌이 되면 갈라진다.

## 최소한 이것만은

```bash
python -m telegram_bot.main        # 봇 (저장소 루트에서)
python -m pytest -q                # 테스트
python -m pytest -q shorts/tests   # 쇼츠는 루트 pytest에 안 잡힌다
python -m ruff check shared telegram_bot web conftest.py
```

- 저장소 루트가 import root다. 진입점은 전부 루트에서 `-m`으로 부른다.
- **기능은 다른 기능을 import하지 않는다.** `telegram_bot/tests/test_feature_isolation.py`가 강제한다.
- 시각은 `shared/core/clock.py`의 `now()`·`today()`만 쓴다(ruff `DTZ`가 막는다).
- 상태 파일은 `shared/core/storage.py`의 원자적 쓰기로만 저장한다.

나머지는 `code_guide.md`에 있다.
