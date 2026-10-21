from datetime import datetime, timedelta, timezone
import pytest
from pydantic import ValidationError
from tour_api.domain import parse_price, SearchRequest, stable_id, publication
from tour_api.store import Store


def test_price_keeps_currency_and_does_not_invent_zero():
    assert parse_price('от 1 045 у.е.') == {'amount': 1045.0, 'currency': 'CU', 'raw': 'от 1 045 у.е.'}
    assert parse_price('503 €') == {'amount': 503.0, 'currency': 'EUR', 'raw': '503 €'}
    assert parse_price('498 $')['currency'] == 'USD'
    assert parse_price('уточняйте')['amount'] is None
    assert parse_price('')['currency'] is None


def test_price_does_not_turn_decimal_comma_into_thousands():
    assert parse_price('1\u00a0234,50 EUR')['amount'] == 1234.5


def test_search_validates_dates_and_party():
    tomorrow = (datetime.now(timezone(timedelta(hours=5))) + timedelta(days=1)).date()
    request = SearchRequest(country='Турция', date_from=tomorrow, date_to=tomorrow + timedelta(days=2))
    assert request.adults == 2
    with pytest.raises(ValidationError):
        SearchRequest(country='Турция', date_from=tomorrow, date_to=tomorrow-timedelta(days=1))
    with pytest.raises(ValidationError):
        SearchRequest(country='Турция', date_from=tomorrow, date_to=tomorrow, children=[18])
    with pytest.raises(ValidationError):
        SearchRequest(country='Турция', date_from=tomorrow, date_to=tomorrow, nights_from=14, nights_to=6)


def test_ids_and_publication_are_stable_and_keep_price_basis():
    assert stable_id('tour', 'https://example.org/a#top') == stable_id('tour', 'https://example.org/a')
    card = {'id':'x','title':'Отель <script>x</script>', 'price':parse_price('503 €'),
            'price_basis':'per_person','nights':6,'source_url':'https://example.org/a','fetched_at':'2026-10-21T12:00:00+00:00'}
    item = publication(card)
    assert 'за человека' in item['text']
    assert '503' in item['text'] and 'EUR' in item['text']
    assert '<script>' not in item['text']
    assert item['source_url'] == card['source_url']


def test_snapshot_failure_preserves_previous_data(tmp_path):
    store = Store(tmp_path/'db.sqlite3')
    store.save_snapshot('hot', [{'id':'a','title':'First'}])
    store.record_error('hot', 'Timeout')
    assert store.snapshot('hot')['items'][0]['id'] == 'a'
    assert store.snapshot('hot')['last_error'] == 'Timeout'
    with pytest.raises(ValueError):
        store.save_snapshot('hot', [])
    assert store.snapshot('hot')['items'][0]['id'] == 'a'


def test_snapshot_uses_utc_and_has_no_data_state(tmp_path):
    store = Store(tmp_path/'db.sqlite3')
    assert store.snapshot('catalogue') is None
    store.save_snapshot('catalogue', [{'id':'a'}])
    assert datetime.fromisoformat(store.snapshot('catalogue')['fetched_at']).utcoffset() == timedelta(0)


def test_job_survives_read_and_filters_out_private_state(tmp_path):
    store = Store(tmp_path/'db.sqlite3')
    store.save_job('job', {'status':'queued','items':[]})
    assert store.job('job')['status'] == 'queued'
    store.save_job('job', {'status':'complete','items':[{'id':'hotel'}]})
    assert store.job('job')['items'][0]['id'] == 'hotel'


def test_job_retention_bounds_history_and_preserves_active_jobs(tmp_path):
    store = Store(tmp_path/'db.sqlite3', max_jobs=2, retention_seconds=3600)
    store.save_job('pending', {'status':'queued'})
    for number in range(4):
        store.save_job(str(number), {'status':'complete'})
    assert store.job('pending')['status'] == 'queued'
    assert store.job('0') is None and store.job('1') is None
    assert store.job('2') and store.job('3')
    with store.connect() as db:
        db.execute("UPDATE jobs SET updated_at='2020-01-01T00:00:00+00:00' WHERE id='2'")
    store.prune_jobs()
    assert store.job('2') is None
    store.interrupt_pending_jobs()
    assert store.job('pending')['status'] == 'failed'
