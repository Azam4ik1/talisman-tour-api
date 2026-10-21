from datetime import datetime,timedelta,timezone
from urllib.parse import urlencode
import pytest
from tour_api.domain import SearchRequest
from tour_api.source import verify_submitted
from tour_api.parsers import SourceFormatError


def test_wrong_submitted_dates_are_rejected():
    day=(datetime.now(timezone(timedelta(hours=5)))+timedelta(days=5)).date()
    req=SearchRequest(country='Турция',date_from=day,date_to=day,nights_from=7,nights_to=7,adults=1,children=[7])
    values={'datefrom':day.strftime('%d.%m.%Y'),'dateto':day.strftime('%d.%m.%Y'),'nightsfrom':'7','nightsto':'7','adults':'1','child':'1','childage1':'7'}
    assert verify_submitted('https://tourvisor.ru/xml/modsearch.php?'+urlencode(values),req)['adults']=='1'
    values['adults']='2'
    with pytest.raises(SourceFormatError):
        verify_submitted('https://tourvisor.ru/xml/modsearch.php?'+urlencode(values),req)
