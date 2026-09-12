import pytest

from telegram_bot.stocks.quotes import ProviderCooldownError, _TTLCache


def test_cache_uses_stale_value_and_skips_retry_during_failure_cooldown():
    cache = _TTLCache(ttl_seconds=0, failure_cooldown_seconds=60)
    assert cache.get_or_fetch("endpoint", lambda: "latest") == "latest"

    calls = 0

    def unavailable():
        nonlocal calls
        calls += 1
        raise ConnectionError("provider unavailable")

    assert cache.get_or_fetch("endpoint", unavailable) == "latest"
    assert cache.get_or_fetch("endpoint", unavailable) == "latest"
    assert calls == 1


def test_cache_skips_new_network_request_after_failure_without_stale_value():
    cache = _TTLCache(ttl_seconds=60, failure_cooldown_seconds=60)

    def unavailable():
        raise ConnectionError("provider unavailable")

    with pytest.raises(ConnectionError):
        cache.get_or_fetch("endpoint", unavailable)
    with pytest.raises(ProviderCooldownError):
        cache.get_or_fetch("endpoint", unavailable)
