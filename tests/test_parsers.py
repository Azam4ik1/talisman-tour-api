from pathlib import Path
import pytest
from tour_api.parsers import parse_hot, parse_catalogue, parse_countries, parse_search, nights, SourceFormatError

FIXTURES = Path(__file__).parent/'fixtures'


def test_hot_extracts_real_card_once_without_confusing_old_price():
    rows = parse_hot((FIXTURES/'hot.html').read_text(encoding='utf-8'), 'за человека')
    assert len(rows) == 2
    item = rows[0]
    assert item['price']['amount'] == 503
    assert item['price']['currency'] == 'EUR'
    assert item['old_price']['amount'] == 751
    assert item['nights'] == 6
    assert item['country'] == 'Турция'
    assert item['price_basis'] == 'per_person'
    assert item['images'][0].startswith('https://')
    assert item['date_text'] == '23 окт'
    assert item['departure_date'] is None  # no year in the card: do not invent one


def test_hot_does_not_infer_per_person_without_visible_basis():
    rows = parse_hot((FIXTURES/'hot.html').read_text(encoding='utf-8'))
    assert rows[0]['price_basis'] == 'unknown'


def test_catalogue_reads_structured_currency_and_keeps_original_label():
    rows = parse_catalogue((FIXTURES/'catalogue.html').read_text(encoding='utf-8'))
    assert len(rows) == 2
    assert rows[0]['price']['amount'] == 525
    assert rows[0]['price']['currency'] == 'USD'
    assert 'у.е.' in rows[0]['price']['raw']
    assert rows[0]['nights'] == 6
    assert rows[0]['source_url'] == 'https://talisman-tour.uz/tury/dubai-oae'
    assert rows[0]['description']


def test_source_failure_is_not_a_valid_empty_snapshot():
    with pytest.raises(SourceFormatError):
        parse_hot('<html>Access denied</html>')
    with pytest.raises(SourceFormatError):
        parse_catalogue('<html>Ожидайте</html>')


def test_countries_only_accept_source_country_pages_and_deduplicate():
    html = '<a href="/strany/turciya">Турция</a><a href="/strany/turciya">Турция</a><a href="https://evil.invalid/strany/x">X</a><a href="/about">О нас</a>'
    assert len(parse_countries(html)) == 1


def test_search_price_description_and_hotel_identity():
    rows=parse_search((FIXTURES/'search.html').read_text(encoding='utf-8'), {'country':'Турция'})
    assert len(rows)==1
    assert rows[0]['price']['amount']==570
    assert rows[0]['price']['currency']=='EUR'
    assert rows[0]['price_basis']=='per_person'
    assert rows[0]['title']=='Oriella Hotel 3*'
    assert rows[0]['description']
    assert rows[0]['hotel_url'].endswith('#!/hotel=oriella-hotel')
    assert rows[0]['stars']==3


def test_variable_duration_is_not_reported_as_exact_nights():
    assert nights('7–14 ночей') is None
    assert nights('от 4 ночей') is None
    assert nights('7 дней / 6 ночей')==6


def test_same_name_hotels_with_different_provider_urls_do_not_collide():
    html=(FIXTURES/'search.html').read_text(encoding='utf-8')
    a=parse_search(html,{'country':'Турция'})[0]
    b=parse_search(html.replace('hotel=oriella-hotel','hotel=another-oriella'),{'country':'Турция'})[0]
    assert a['hotel_id']!=b['hotel_id']
    both=parse_search(html+html.replace('hotel=oriella-hotel','hotel=another-oriella'),{'country':'Турция'})
    assert len(both)==2
