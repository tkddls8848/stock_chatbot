from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
import requests

from polymarket_shorts import accuracy, cli, pipeline
from polymarket_shorts.config import Settings
from polymarket_shorts.core.storage import read_json, write_json
from polymarket_shorts.status import current_status
from polymarket_shorts.youtube import revision_id


@pytest.fixture
def settings(tmp_path):
    return replace(Settings.from_env(), output_dir=tmp_path / 'shorts')


@pytest.fixture
def moment(monkeypatch):
    value = datetime.fromisoformat('2026-11-15T12:00:00+09:00')
    monkeypatch.setattr(accuracy, 'now', lambda: value)
    return value


def records(settings):
    return read_json(settings.output_dir / 'state' / 'accuracy.json')['records']


def save(settings, rows):
    write_json(settings.output_dir / 'state' / 'accuracy.json', {'records': rows})


def row(key, **extra):
    return {'market_id': key, 'side': 'yes', 'probability': 0.7, **extra}


def market(key, prices, **extra):
    return {'id': key, 'closed': True, 'outcomes': '["Yes","No"]',
            'outcomePrices': prices, 'umaResolutionStatus': 'resolved', **extra}


def session(payload):
    client = Mock()
    client.get.return_value.json.return_value = payload
    return client


def published(target, key, p, stamp, *, publish=True):
    review = {'produced_at': stamp, 'video_sha256': key, 'youtube': {'title': key}}
    write_json(target / 'review.json', review)
    write_json(target / 'scenario.json', {'scenes': [
        {'kind': 'consensus', 'market_ids': [key], 'options': [[key, '확률', p]], 'event_id': 'event'},
        {'kind': 'watch', 'market_ids': [key], 'options': [[key, '확률', p]]},
    ]})
    if publish:
        write_json(target / 'upload.json', {'revisions': {
            revision_id(target, review): {'video_id': 'video-id', 'uploaded_at': stamp}}})


def test_collect_matches_published_revision_and_preserves_first_probability(settings):
    day = settings.output_dir / '2026-11-01'
    published(day, 'old', .8, '2026-11-01T10:00:00+09:00')
    published(day / 'revisions' / 'posted', 'a', .6, '2026-11-01T11:00:00+09:00')
    published(day / 'revisions' / 'draft', 'draft', .9, 'later', publish=False)
    write_json(day / 'workflow.json', {'current': 'revisions/draft'})
    published(settings.output_dir / '2026-11-02', 'a', .9, '2026-11-02T11:00:00+09:00')
    published(settings.output_dir / '2026-11-03', 'unpublished', .5, 'later', publish=False)
    assert accuracy.collect(settings) == 1
    assert accuracy.collect(settings) == 0
    result = records(settings)
    assert set(result) == {'a'}
    assert (result['a']['probability'], result['a']['times']) == (.6, 2)
    assert (result['a']['first_date'], result['a']['last_date']) == ('2026-11-01', '2026-11-02')
    pipeline.prune_old_days(settings, datetime(2026, 12, 1).date())
    assert not day.exists() and records(settings) == result


def test_settle_scores_the_displayed_side_and_named_outcomes(settings, moment):
    save(settings, {key: row(key, side=side) for key, side in [('no', 'no'), ('named', 'yes'), ('reverse', 'yes')]})
    client = session([market('no', '["0","1"]'),
                      market('named', ['1', '0'], outcomes=['Up', 'Down']),
                      market('reverse', ['0', '1'], outcomes=['No', 'Yes'])])
    assert accuracy.settle(settings, client)['settled'] == 3
    assert all(r['outcome'] == 1 and r['settled_at'] == moment.isoformat() for r in records(settings).values())
    assert client.get.call_args.kwargs == {'params': [('id', 'no'), ('id', 'named'), ('id', 'reverse')], 'timeout': 20}


def test_void_waiting_and_failed_batches_preserve_pending_results(settings, moment):
    old = (moment - timedelta(days=14)).isoformat()
    save(settings, {k: row(k, **v) for k, v in {
        'split': {}, 'old': {}, 'fallback': {'closed_since': old}, 'missing': {'missing_since': old},
        'wait': {}, 'open': {}, 'newmissing': {},
    }.items()})
    client = session([market('split', [.5, .5]), market('old', [.3, .7], closedTime=old),
                      market('fallback', [.4, .6]), market('wait', [.3, .7]), market('open', [1, 0], closed=False)])
    result = accuracy.settle(settings, client)
    assert (result['void'], result['pending'], result['settled']) == (4, 3, 0)
    assert {r['void_reason'] for r in records(settings).values() if r.get('void')} == {
        'split_resolution', 'closed_14_days', 'missing_14_days'}
    client.get.side_effect = requests.Timeout('secret response')
    assert accuracy.settle(settings, client)['failed'] == 3
    assert accuracy.summary(records(settings))['pending'] == 3
    client.get.side_effect = None
    client.get.return_value.json.return_value = [market('wait', [1, 0]), market('open', ['bad', '0'])]
    assert accuracy.settle(settings, client)['failed'] == 3
    assert 'outcome' not in records(settings)['wait']  # 묶음 전체를 보류한다.


def test_settle_limits_requests_and_rotates_failed_checks(settings, moment):
    save(settings, {str(i): row(str(i)) for i in range(251)})
    client = session([])
    client.get.side_effect = requests.Timeout()
    assert accuracy.settle(settings, client)['failed'] == 200
    assert client.get.call_count == 4
    assert all(len(call.kwargs['params']) == 50 for call in client.get.call_args_list)
    client.reset_mock()
    accuracy.settle(settings, client)
    assert client.get.call_args_list[0].kwargs['params'][0] == ('id', '200')


def test_summary_bucket_boundaries_and_favored_denominator():
    ps = [.4, .5, .6, .7, .8, .9, 1.]
    rows = {str(i): row(str(i), probability=p, outcome=i % 2) for i, p in enumerate(ps)}
    rows.update(wait=row('wait'), void=row('void', void=True))
    result = accuracy.summary(rows)
    assert (result['settled'], result['void'], result['pending']) == (7, 1, 1)
    assert result['favored_hit_rate'] == .5
    assert result['brier'] == pytest.approx(sum((p - i % 2) ** 2 for i, p in enumerate(ps)) / 7)
    assert [b['count'] for b in result['buckets']] == [1, 1, 1, 1, 1, 2]
    assert [b['realized'] for b in result['buckets']] == [0, 1, 0, 1, 0, 1]


def test_update_publishes_only_anonymous_summary_after_thirty(settings, moment, monkeypatch):
    monkeypatch.setattr(accuracy.requests, 'Session', lambda: session([]))
    assert current_status(settings)['accuracy'] is None
    rows = {str(i): row(str(i), outcome=1, label='private', event_id='private') for i in range(29)}
    save(settings, rows)
    public = settings.public_dir / 'shorts' / 'accuracy.json'
    assert accuracy.update(settings)['settled'] == 29 and not public.exists()
    rows['29'] = row('29', outcome=0)
    save(settings, rows)
    accuracy.update(settings)
    payload = read_json(public)
    assert set(payload) == {'updated_at', 'settled', 'void', 'brier', 'favored_hit_rate', 'buckets'}
    assert all(word not in public.read_text() for word in ('market_id', 'private', 'event_id', 'probability'))
    assert current_status(settings)['accuracy']['settled'] == 30
    before = public.read_bytes()
    save(settings, {})
    accuracy.update(settings)
    assert public.read_bytes() == before


def test_accuracy_failure_never_changes_production_or_attempts(settings, monkeypatch, caplog):
    from polymarket_shorts import attempts
    from polymarket_shorts.pipeline import ProductionResult

    monkeypatch.setattr(accuracy, 'update', Mock(side_effect=RuntimeError('secret')))
    monkeypatch.setattr(pipeline, 'produce_daily', lambda *a, **k: ProductionResult(status='no_suitable_issues', date='2026-11-15'))
    result = attempts.run(settings, lambda: cli._produce_today(settings))
    assert result['status'] == 'no_suitable_issues'
    attempt = read_json(next(settings.output_dir.glob('*/attempts.json')))
    assert len(attempt['attempts']) == 1 and attempt['succeeded_at']
    assert 'error' not in attempt['attempts'][0] and not attempt.get('gave_up')
    assert 'secret' not in caplog.text
    monkeypatch.setattr(pipeline, 'produce_daily', Mock(side_effect=RuntimeError('production')))
    with pytest.raises(RuntimeError, match='production'):
        cli._produce_today(settings)
