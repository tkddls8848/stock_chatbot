"""공통 테스트 부트스트랩과 샌드박스 친화적인 임시 경로 설정."""

import os
import sys
import tempfile
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parent
TEST_TEMP_ROOT = WORKSPACE_ROOT / ".test-tmp"

# 저장소 루트가 import root다. telegram_bot·web이 여기서 보인다.
sys.path.insert(0, str(WORKSPACE_ROOT))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test-chat")
# 빈 허용 목록은 기동을 막는다(core/config.py). 테스트도 같은 규칙을 따른다.
os.environ.setdefault("ALLOWED_CHAT_IDS", "1")
# 브리핑 LLM이 항상 켜져 있어 자격증명이 비면 config import가 멈춘다(core/config.py).
# 실제 스모크를 켤 때는 .env 값을 가리지 않도록 가짜 값을 넣지 않는다.
if os.environ.get("RUN_CLOUDFLARE_SMOKE") != "1":
    os.environ.setdefault("CLOUDFLARE_ACCOUNT_ID", "test-account")
    os.environ.setdefault("CLOUDFLARE_API_TOKEN", "test-token")

TEST_TEMP_ROOT.mkdir(exist_ok=True)
for variable in ("TMPDIR", "TEMP", "TMP", "PYTEST_DEBUG_TEMPROOT"):
    os.environ[variable] = str(TEST_TEMP_ROOT)
tempfile.tempdir = str(TEST_TEMP_ROOT)
