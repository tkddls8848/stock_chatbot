"""이슈 장면은 그날 이슈로 그린 배경, 도입은 첫 이슈 그림, 마무리·실패는 저장 배경."""

from dataclasses import replace
from types import SimpleNamespace

from polymarket_shorts import media
from polymarket_shorts.config import Settings


def _scene(kind, query=""):
    return SimpleNamespace(kind=kind, visual_query=query)


def _settings():
    return replace(Settings.from_env(), editor_account_id="a", editor_api_token="t", generated_backgrounds=True)


def test_issue_scenes_get_generated_art_and_intro_reuses_the_first(tmp_path, monkeypatch):
    calls = []

    def fake(subject, target, settings):
        calls.append(subject)
        target.write_bytes(b"png")
        return target

    monkeypatch.setattr(media, "_generate_background", fake)
    scenes = (_scene("intro"), _scene("consensus", "central bank; topic: Fed Decision in October?"),
              _scene("consensus", "cargo; topic: Strait of Hormuz traffic"), _scene("outro"))

    chosen = media.backgrounds_for(scenes, tmp_path, _settings())

    assert calls == ["Fed Decision in October?", "Strait of Hormuz traffic"]   # 도입은 새로 그리지 않는다
    assert chosen[0] == chosen[1] and chosen[1] != chosen[2]
    assert chosen[3] == media.background_for("outro", "")                      # 마무리는 저장 배경


def test_failed_generation_falls_back_and_saved_art_is_reused(tmp_path, monkeypatch):
    scenes = (_scene("consensus", "cargo; topic: Hormuz"),)
    monkeypatch.setattr(media, "_generate_background", lambda *args: None)
    assert media.backgrounds_for(scenes, tmp_path, _settings()) == (media.background_for("consensus", "cargo; topic: Hormuz"),)

    # 이미 그려 둔 그림이 있으면 다시 부르지 않는다 — 검수 중 재렌더가 같은 배경을 쓴다.
    def must_not_call(*args):
        raise AssertionError("재생성하면 안 된다")

    monkeypatch.setattr(media, "_generate_background", must_not_call)
    saved = tmp_path / "saved.png"
    saved.write_bytes(b"png")
    name = media.hashlib.sha1(b"cargo; topic: Hormuz").hexdigest()[:12] + ".png"
    (tmp_path / name).write_bytes(b"png")
    assert media.backgrounds_for(scenes, tmp_path, _settings()) == (tmp_path / name,)


def test_generation_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "_generate_background", lambda *args: (_ for _ in ()).throw(AssertionError()))
    settings = replace(_settings(), generated_backgrounds=False)
    scenes = (_scene("consensus", "cargo; topic: Hormuz"),)
    assert media.backgrounds_for(scenes, tmp_path, settings) == (media.background_for("consensus", "cargo; topic: Hormuz"),)
