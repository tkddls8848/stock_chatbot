import base64
from dataclasses import replace
from pathlib import Path

import pytest
import requests

from polymarket_shorts import elevenlabs_tts as eleven
from polymarket_shorts import tts
from polymarket_shorts.config import Settings


def settings(tmp_path):
    return Settings(output_dir=tmp_path, state_file=tmp_path / 'state.json', elevenlabs_api_key='secret-key')


def response_for(text):
    return {'audio_base64': base64.b64encode(b'complete mp3').decode(),
            'alignment': {'characters': list(text),
                          'character_start_times_seconds': [i * .05 for i in range(len(text))],
                          'character_end_times_seconds': [(i + 1) * .05 for i in range(len(text))]}}


def install_response(monkeypatch, captured, *, status=200, corrupt=None):
    class Response:
        status_code = status

        def json(self):
            result = response_for(captured['json']['text'])
            if corrupt:
                corrupt(result)
            return result

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()
    monkeypatch.setattr(eleven.requests, 'post', post)


def fake_edge(monkeypatch, calls):
    class Edge:
        def __init__(self, text, voice, **kwargs):
            calls.append((text, voice, kwargs))

        async def save(self, audio, words):
            Path(audio).write_bytes(b'edge mp3')
            Path(words).write_text('{"offset": 0, "duration": 10000000, "text": "끝"}\n')
    monkeypatch.setattr(tts.edge_tts, 'Communicate', Edge)


def run(tmp_path, **kwargs):
    return tts.synthesize(kwargs.pop('narrations', ['끝']), audio_path=tmp_path / 'narration.mp3',
                          words_path=tmp_path / 'words.jsonl', voice='ko-KR-SunHiNeural',
                          rate='+0%', settings=kwargs.pop('settings', settings(tmp_path)), **kwargs)


def test_jian_success_preserves_audio_and_original_subtitle_positions(tmp_path, monkeypatch):
    captured, info = {}, {}
    install_response(monkeypatch, captured)
    monkeypatch.setattr(tts, '_decode', lambda *a: range(24000 * 100))
    monkeypatch.setattr(tts, '_pace_breaths', lambda *a, **k: pytest.fail('Do not edit Jian audio'))
    monkeypatch.setattr(tts.edge_tts, 'Communicate', lambda *a, **k: pytest.fail('No second paid/free synthesis'))
    narrations = ['한국 ETF(EWY)가 12.55%입니다.', '컨센서스 참여자의 70.5%는 동결-인상-동결을 기대합니다.']
    scenes = run(tmp_path, narrations=narrations, synthesis_info=info)
    assert '십이점오오퍼센트' in captured['json']['text']
    assert '칠십점오퍼센트' in captured['json']['text']
    assert captured['json']['voice_settings'] == {
        'speed': 1.16, 'stability': .75, 'similarity_boost': .85, 'style': 0., 'use_speaker_boost': False}
    assert captured['headers'] == {'xi-api-key': 'secret-key'}
    assert captured['timeout'] == (10, 150) and captured['allow_redirects'] is False
    assert (tmp_path / 'narration.mp3').read_bytes() == b'complete mp3'
    assert len(scenes) == 2
    assert [w.text for w in scenes[0]] == ['한국', 'ETF가', '12.55%입니다.']
    assert tts.locate(narrations[0], scenes[0]) == [0, 3, 13]
    assert scenes[0][-1].end < scenes[1][0].start
    assert info['provider'] == 'elevenlabs' and 'secret-key' not in str(info)
    saved = tts._read_words(tmp_path / 'words.jsonl')
    for actual, expected in zip(saved, (w for scene in scenes for w in scene), strict=True):
        assert actual.text == expected.text
        assert actual.start == pytest.approx(expected.start)
        assert actual.end == pytest.approx(expected.end)


@pytest.mark.parametrize('status', [401, 402, 403, 422, 429, 500, 503])
def test_http_failure_falls_back_once_without_leaking_key(tmp_path, monkeypatch, caplog, status):
    captured, calls, info = {}, [], {}
    install_response(monkeypatch, captured, status=status)
    fake_edge(monkeypatch, calls)
    scenes = run(tmp_path, synthesis_info=info)
    assert scenes == ((tts.Word(0, 1, '끝'),),)
    assert len(calls) == 1 and calls[0][1] == 'ko-KR-SunHiNeural'
    assert info['provider'] == 'edge' and info['fallback_reason'] == f'http_{status}'
    assert (tmp_path / 'narration.mp3').read_bytes() == b'edge mp3'
    assert 'secret-key' not in caplog.text


@pytest.mark.parametrize('error', [requests.Timeout('secret-key'), requests.ConnectionError('secret-key')])
def test_network_failure_falls_back(tmp_path, monkeypatch, caplog, error):
    def post(*a, **k):
        raise error
    monkeypatch.setattr(eleven.requests, 'post', post)
    calls, info = [], {}
    fake_edge(monkeypatch, calls)
    run(tmp_path, synthesis_info=info)
    assert len(calls) == 1 and info['fallback_reason'] == 'network_error'
    assert 'secret-key' not in caplog.text


@pytest.mark.parametrize('corrupt', [
    lambda r: r.update(audio_base64='!'),
    lambda r: r.update(alignment=None),
    lambda r: r['alignment'].update(characters=['다른 원고']),
    lambda r: r['alignment'].update(character_end_times_seconds=[]),
    lambda r: r['alignment'].update(character_end_times_seconds=[float('nan')]),
    lambda r: r['alignment'].update(character_end_times_seconds=[0]),
])
def test_bad_audio_or_alignment_falls_back(tmp_path, monkeypatch, corrupt):
    install_response(monkeypatch, {}, corrupt=corrupt)
    calls = []
    fake_edge(monkeypatch, calls)
    run(tmp_path)
    assert len(calls) == 1


def test_missing_key_skips_elevenlabs(tmp_path, monkeypatch):
    monkeypatch.setattr(eleven.requests, 'post', lambda *a, **k: pytest.fail('No key'))
    calls, info = [], {}
    fake_edge(monkeypatch, calls)
    run(tmp_path, settings=replace(settings(tmp_path), elevenlabs_api_key=''), synthesis_info=info)
    assert info['fallback_reason'] == 'missing_api_key' and len(calls) == 1


@pytest.mark.parametrize('truncated', [True, False])
def test_invalid_decoded_audio_falls_back(tmp_path, monkeypatch, truncated):
    install_response(monkeypatch, {})
    def decode(*args):
        if truncated:
            return []
        raise tts.TTSError('invalid file')
    monkeypatch.setattr(tts, '_decode', decode)
    calls = []
    fake_edge(monkeypatch, calls)
    # Enough characters that empty decoded audio cannot pass the encoder-delay tolerance.
    run(tmp_path, narrations=['끝 끝 끝 끝 끝 끝 끝'])
    assert len(calls) == 1


def test_both_providers_fail_without_reporting_success(tmp_path, monkeypatch):
    install_response(monkeypatch, {}, status=402)
    class Edge:
        def __init__(self, *a, **k):
            pass
        async def save(self, *args):
            raise tts.EdgeTTSException('unavailable')
    monkeypatch.setattr(tts.edge_tts, 'Communicate', Edge)
    with pytest.raises(tts.TTSError):
        run(tmp_path)


def test_normalization_keeps_numbers_and_breaks_probability_clauses():
    source = '70.5%는 동결-인상-동결을, 컨센서스 참여자의 12.55%는 인상을 기대합니다. 6월 10월 1,000.05 0%'
    spoken, mapping = eleven.pronunciation(source)
    assert spoken == ('칠십점오퍼센트는 동결한 뒤 인상한 뒤 동결을 기대하고 있습니다. '
                      '컨센서스 참여자의 십이점오오퍼센트는 인상을 기대합니다. 유월 시월 천점영오 영퍼센트')
    assert len(mapping) == len(spoken) and mapping == sorted(mapping)


def test_percent_unit_is_spoken_without_an_inserted_pause():
    spoken, _ = eleven.pronunciation("질문 3개. 70.5%와 12.55%입니다.")
    assert spoken == "질문 세개. 칠십점오퍼센트와 십이점오오퍼센트입니다."
