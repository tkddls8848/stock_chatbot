


from polymarket_shorts.media import ASSET_DIR, background_for


def test_local_backgrounds_are_present_and_missing_images_use_plain_background(tmp_path):
    assert background_for("intro", "") == ASSET_DIR / "financial-city.png"
    assert background_for("consensus", "global cargo shipping containers trade") == ASSET_DIR / "global-trade.png"
    assert background_for("intro", "", tmp_path) is None
    (tmp_path / "financial-city.png").write_bytes(b"invalid png")
    assert background_for("intro", "", tmp_path) is None


