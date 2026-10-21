import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Literal
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.security import APIKeyHeader
from .config import Config
from .domain import SearchRequest, now_iso, publication
from .store import Store

logger=logging.getLogger('tour_api')


def create_app(config=None, source=None, store=None):
    config=config or Config()
    store=store or Store(config.db_path,config.max_saved_jobs,config.job_retention_seconds)
    if source is None:
        from .source import PublicSource
        source=PublicSource()
    tasks=set()
    pending=set()
    cached={}
    active_refresh=set()
    browser_lock=asyncio.Lock()

    def spawn(coroutine):
        task=asyncio.create_task(coroutine)
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return task

    async def refresh_one(kind):
        if kind in active_refresh:
            return
        active_refresh.add(kind)
        try:
            async with browser_lock:
                items=await asyncio.wait_for(source.refresh(kind),timeout=180)
            store.save_snapshot(kind,items)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message='Источник не ответил вовремя' if isinstance(exc,TimeoutError) else str(exc)
            store.record_error(kind,message)
            logger.warning('Refresh %s failed: %s',kind,message[:300])
        finally:
            active_refresh.discard(kind)

    async def refresh_loop(kind, interval):
        while True:
            snapshot=store.snapshot(kind)
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(snapshot['fetched_at'])).total_seconds() if snapshot else interval
            if age<interval:
                await asyncio.sleep(interval-age)
            await refresh_one(kind)
            await asyncio.sleep(interval)

    @asynccontextmanager
    async def lifespan(app):
        store.interrupt_pending_jobs()
        if config.auto_refresh:
            for kind,interval in [('hot',config.hot_interval),('catalogue',config.catalogue_interval),('countries',config.catalogue_interval)]:
                spawn(refresh_loop(kind,interval))
        yield
        for task in list(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks,return_exceptions=True)

    app=FastAPI(title='Tour API',version='0.1.0',lifespan=lifespan,
                description='Поиск и сбор публичных предложений Talisman Tour. Цены требуют подтверждения. Данные могут быть устаревшими.')
    if config.cors_origins:
        app.add_middleware(CORSMiddleware,allow_origins=config.cors_origins,allow_methods=['GET','POST'],allow_headers=['X-API-Key','Content-Type'])
    header=APIKeyHeader(name='X-API-Key',auto_error=False)

    async def authorize(key=Depends(header)):
        if config.api_key and not hmac.compare_digest(key or '',config.api_key):
            raise HTTPException(401,'Нужен правильный X-API-Key')

    router=APIRouter(prefix='/api',dependencies=[Depends(authorize)])

    def is_stale(timestamp, max_age):
        if not timestamp:
            return True
        try:
            return (datetime.now(timezone.utc)-datetime.fromisoformat(timestamp)).total_seconds()>max_age
        except (ValueError,TypeError):
            return True

    def snapshot_data(kind):
        snapshot=store.snapshot(kind)
        if snapshot is None:
            raise HTTPException(503,{'message':'Данные ещё не собраны. Проверьте /api/status или запустите обновление.','source':kind})
        interval=config.hot_interval if kind=='hot' else config.catalogue_interval
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(snapshot['fetched_at'])).total_seconds()
        return {**snapshot,'stale': bool(snapshot['last_error']) or age>interval*2}

    def list_snapshot(kind, country=None, q=None, currency=None, min_price=None, max_price=None, limit=20, offset=0):
        if (min_price is not None or max_price is not None) and not currency:
            raise HTTPException(422,'Для фильтра цены укажите currency')
        if min_price is not None and max_price is not None and min_price>max_price:
            raise HTTPException(422,'min_price не должен превышать max_price')
        snapshot=snapshot_data(kind)
        rows=snapshot.pop('items')
        if country:
            rows=[row for row in rows if str(row.get('country') or '').casefold()==country.casefold()]
        if q:
            rows=[row for row in rows if q.casefold() in ' '.join(str(row.get(k) or '') for k in ['title','description','country','resort','tags']).casefold()]
        if currency:
            rows=[row for row in rows if (row.get('price') or {}).get('currency')==currency.upper()]
        if min_price is not None:
            rows=[row for row in rows if (row.get('price') or {}).get('amount') is not None and row['price']['amount']>=min_price]
        if max_price is not None:
            rows=[row for row in rows if (row.get('price') or {}).get('amount') is not None and row['price']['amount']<=max_price]
        return {**snapshot,'items':rows[offset:offset+limit],'total':len(rows),'limit':limit,'offset':offset}

    @app.get('/',response_class=HTMLResponse,include_in_schema=False)
    def home():
        return '<!doctype html><html lang="ru"><meta charset="utf-8"><title>Tour API</title><body style="font:18px system-ui;max-width:800px;margin:60px auto"><h1>Tour API</h1><p>Поиск туров, горящие предложения и карточки для публикации.</p><p><a href="/docs">Открыть документацию и проверить API</a></p><p><a href="/health">Состояние сервиса</a></p><p>Цены получены со страниц источника и требуют подтверждения перед бронированием.</p></body></html>'

    @app.get('/health')
    def health():
        return {'status':'ok','ready':all(store.snapshot(k) is not None for k in ['catalogue','hot','countries']), 'automatic_refresh':config.auto_refresh}

    @router.get('/status')
    def status():
        snapshots={}
        for kind in ['hot','catalogue','countries']:
            value=store.snapshot(kind)
            snapshots[kind]={'count':len(value['items']),'fetched_at':value['fetched_at'],'last_error':value['last_error']} if value else None
        return {'snapshots':snapshots,'errors':store.errors(),'refreshing':sorted(active_refresh),'pending_searches':len(pending)}

    @router.get('/countries')
    def countries():
        return snapshot_data('countries')

    def filters(country: str|None=None,q: str|None=None,currency: str|None=None,
                min_price: float|None=Query(None,ge=0,allow_inf_nan=False),max_price: float|None=Query(None,ge=0,allow_inf_nan=False),
                limit: int=Query(20,ge=1,le=100),offset: int=Query(0,ge=0)):
        return dict(country=country,q=q,currency=currency,min_price=min_price,max_price=max_price,limit=limit,offset=offset)

    @router.get('/tours')
    def tours(params=Depends(filters)):
        return list_snapshot('catalogue',**params)

    @router.get('/hot-tours')
    def hot_tours(params=Depends(filters)):
        return list_snapshot('hot',**params)

    @router.get('/tours/{tour_id}')
    def tour(tour_id: str):
        for kind in ['catalogue','hot']:
            snapshot=store.snapshot(kind)
            for row in (snapshot or {}).get('items',[]):
                if row['id']==tour_id:
                    return {**row,'stale':snapshot_data(kind)['stale']}
        raise HTTPException(404,'Тур не найден')

    @router.get('/hotels')
    def hotels(limit: int=Query(20,ge=1,le=100),offset: int=Query(0,ge=0)):
        snapshot=snapshot_data('hotels')
        rows=snapshot.pop('items')
        selected=[{**row,'stale':is_stale(row.get('fetched_at'),config.catalogue_interval*2)} for row in rows[offset:offset+limit]]
        return {**snapshot,'stale':snapshot['stale'] or any(row['stale'] for row in selected),
                'items':selected,'total':len(rows),'limit':limit,'offset':offset}

    @router.get('/publications')
    def publications(source: Literal['hot','catalogue']='hot',params=Depends(filters)):
        data=list_snapshot(source,**params)
        data['items']=[publication(row) for row in data['items']]
        return data

    async def run_search(job_id,request,cache_key):
        job={'id':job_id,'status':'queued','request':request.model_dump(mode='json'),'created_at':now_iso(),'items':[]}
        store.save_job(job_id,job)
        try:
            async with browser_lock:
                job.update(status='running',started_at=now_iso())
                store.save_job(job_id,job)
                result=await asyncio.wait_for(source.search(request),timeout=config.search_timeout)
            job.update(result,status='complete',completed_at=now_iso())
            store.save_job(job_id,job)
            cached[cache_key]=(time.monotonic(),job_id)
            if len(cached)>100:
                cached.pop(next(iter(cached)))
            if result.get('items'):
                previous=store.snapshot('hotels')
                hotel_map={row['id']:row for row in (previous or {}).get('items',[])}
                for row in result['items']:
                    hotel={k:row.get(k) for k in ['title','hotel_name','description','images','stars','rating','country','resort','source_url','fetched_at']}
                    hotel['id']=row.get('hotel_id') or row['id']
                    hotel_map[hotel['id']]=hotel
                store.save_snapshot('hotels',list(hotel_map.values())[-1000:])
        except asyncio.CancelledError:
            job.update(status='failed',error='Сервис остановлен; повторите поиск')
            store.save_job(job_id,job)
            raise
        except Exception as exc:
            job.update(status='failed',error='Источник не ответил вовремя' if isinstance(exc,TimeoutError) else str(exc)[:1000],completed_at=now_iso())
            store.save_job(job_id,job)
        finally:
            pending.discard(job_id)

    @router.post('/search',status_code=202)
    async def start_search(request: SearchRequest):
        cache_key=hashlib.sha256(json.dumps(request.model_dump(mode='json'),sort_keys=True).encode()).hexdigest()
        old=cached.get(cache_key)
        if old and time.monotonic()-old[0]<300 and store.job(old[1]):
            return {'id':old[1],'cached':True,'status_url':'/api/search/'+old[1]}
        if len(pending)>=config.max_queue:
            raise HTTPException(429,'Очередь поиска заполнена. Повторите позже.')
        job_id=uuid.uuid4().hex
        pending.add(job_id)
        store.save_job(job_id,{'id':job_id,'status':'queued','request':request.model_dump(mode='json'),'items':[]})
        spawn(run_search(job_id,request,cache_key))
        return {'id':job_id,'cached':False,'status_url':'/api/search/'+job_id}

    @router.get('/search/{job_id}')
    def search_result(job_id: str):
        job=store.job(job_id)
        if job is None:
            raise HTTPException(404,'Поиск не найден')
        return {**job,'stale':job.get('status')!='complete' or is_stale(job.get('fetched_at') or job.get('completed_at'),300)}

    @router.get('/search/{job_id}/publications')
    def search_publications(job_id: str):
        job=search_result(job_id)
        return {**job,'items':[publication(item) for item in job.get('items',[])]}

    @router.post('/refresh',status_code=202)
    async def refresh(kind: Literal['all','hot','catalogue','countries']='all'):
        kinds=['hot','catalogue','countries'] if kind=='all' else [kind]
        for value in kinds:
            if value not in active_refresh:
                spawn(refresh_one(value))
        return {'requested':kinds,'status_url':'/api/status'}

    app.include_router(router)
    return app
