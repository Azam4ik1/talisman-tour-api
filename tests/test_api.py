import asyncio
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from tour_api.app import create_app
from tour_api.config import Config
from tour_api.store import Store


class FakeSource:
    async def search(self, request):
        return {'items':[{'id':'test-hotel','title':'Test hotel','price':{'amount':100,'currency':'USD'},'price_basis':'per_person'}],
                'selection_verified':True,'selected':request.model_dump(mode='json'),'truncated':True}

    async def refresh(self, kind):
        return [{'id':kind+'-1','title':'Fresh'}]


def client_and_store(tmp_path, key=''):
    config=Config(data_dir=tmp_path, api_key=key, auto_refresh=False)
    store=Store(config.db_path)
    return TestClient(create_app(config, FakeSource(), store)), store


def test_no_data_is_503_and_health_is_readable(tmp_path):
    client,_=client_and_store(tmp_path)
    with client:
        assert client.get('/health').status_code==200
        assert client.get('/api/tours').status_code==503


def test_key_protects_reads_and_search_but_not_health(tmp_path):
    client,store=client_and_store(tmp_path,'secret')
    store.save_snapshot('catalogue',[{'id':'a','title':'A'}])
    with client:
        assert client.get('/health').status_code==200
        assert client.get('/api/tours').status_code==401
        assert client.get('/api/tours',headers={'X-API-Key':'wrong'}).status_code==401
        assert client.get('/api/tours',headers={'X-API-Key':'secret'}).status_code==200


def test_filter_does_not_mix_currencies_or_null_prices(tmp_path):
    client,store=client_and_store(tmp_path)
    store.save_snapshot('hot',[
        {'id':'a','title':'A','country':'Турция','price':{'amount':500,'currency':'EUR'}},
        {'id':'b','title':'B','country':'Турция','price':{'amount':400,'currency':'USD'}},
        {'id':'c','title':'C','country':'Турция','price':{'amount':None,'currency':None}}])
    with client:
        assert client.get('/api/hot-tours?max_price=600').status_code==422
        data=client.get('/api/hot-tours?currency=EUR&max_price=600').json()
        assert data['total']==1 and data['items'][0]['id']=='a'
        assert client.get('/api/hot-tours?limit=0').status_code==422
        assert client.get('/api/hot-tours?offset=-1').status_code==422
        assert client.get('/api/hot-tours?currency=EUR&min_price=700&max_price=500').status_code==422


def test_failed_refresh_exposes_stale_previous_snapshot(tmp_path):
    client,store=client_and_store(tmp_path)
    store.save_snapshot('catalogue',[{'id':'a','title':'A'}])
    store.record_error('catalogue','Source unavailable')
    with client:
        data=client.get('/api/tours').json()
        assert data['items'][0]['id']=='a'
        assert data['stale'] is True
        assert data['last_error']=='Source unavailable'


def test_publication_export_keeps_provenance(tmp_path):
    client,store=client_and_store(tmp_path)
    store.save_snapshot('hot',[{'id':'a','title':'A','price':{'amount':100,'currency':'EUR'},
                               'source_url':'https://talisman-tour.uz/goryashchie-tury','price_basis':'per_person'}])
    with client:
        data=client.get('/api/publications?source=hot').json()
        assert 'за человека' in data['items'][0]['text']
        assert data['items'][0]['publication_status']=='draft'


def test_search_job_and_invalid_request(tmp_path):
    client,_=client_and_store(tmp_path)
    date=(datetime.now(timezone(timedelta(hours=5)))+timedelta(days=3)).date().isoformat()
    with client:
        assert client.post('/api/search',json={'country':'Турция'}).status_code==422
        response=client.post('/api/search',json={'country':'Турция','date_from':date,'date_to':date})
        assert response.status_code==202
        job_id=response.json()['id']
        import time
        for _ in range(20):
            result=client.get('/api/search/'+job_id).json()
            if result['status']=='complete': break
            time.sleep(0.02)
        assert result['status']=='complete'
        assert result['selection_verified'] is True
        assert result['items'][0]['id']=='test-hotel'
        assert client.get('/api/search/missing').status_code==404


def test_old_search_and_publication_are_explicitly_stale(tmp_path):
    client,store=client_and_store(tmp_path)
    store.save_job('old', {'id':'old','status':'complete','completed_at':'2020-01-01T00:00:00+00:00',
                          'items':[{'id':'a','title':'A','price':{'amount':100,'currency':'EUR'}}]})
    with client:
        assert client.get('/api/search/old').json()['stale'] is True
        assert client.get('/api/search/old/publications').json()['stale'] is True


def test_new_hotel_snapshot_does_not_make_old_records_fresh(tmp_path):
    client,store=client_and_store(tmp_path)
    store.save_snapshot('hotels',[
        {'id':'old','fetched_at':'2020-01-01T00:00:00+00:00'},
        {'id':'new','fetched_at':datetime.now(timezone.utc).isoformat()}])
    with client:
        data=client.get('/api/hotels').json()
        assert data['stale'] is True
        assert data['items'][0]['stale'] is True
        assert data['items'][1]['stale'] is False
