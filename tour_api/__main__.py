import argparse
import asyncio
import json
import os
from pathlib import Path
from .config import Config
from .domain import SearchRequest
from .source import PublicSource
from .store import Store


async def update(config,kind):
    source=PublicSource()
    store=Store(config.db_path)
    summary={}
    for section in (['catalogue','countries','hot'] if kind=='all' else [kind]):
        try:
            items=await asyncio.wait_for(source.refresh(section),timeout=180)
            store.save_snapshot(section,items)
            summary[section]={'ok':True,'count':len(items)}
        except Exception as exc:
            store.record_error(section,str(exc))
            summary[section]={'ok':False,'error':str(exc)}
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return all(item['ok'] for item in summary.values())


def main():
    parser=argparse.ArgumentParser(description='Tour API — публичные туры и поиск')
    sub=parser.add_subparsers(dest='command',required=True)
    refresh=sub.add_parser('update',help='Обновить сохранённые данные')
    refresh.add_argument('--kind',choices=['all','catalogue','countries','hot'],default='all')
    serve=sub.add_parser('serve',help='Запустить API и автоматическое обновление')
    serve.add_argument('--host',default='127.0.0.1')
    serve.add_argument('--port',type=int,default=8000)
    args=parser.parse_args()
    config=Config()
    if args.command=='update':
        raise SystemExit(0 if asyncio.run(update(config,args.kind)) else 1)
    if args.host not in ('127.0.0.1','localhost','::1') and not config.api_key:
        parser.error('Для внешнего доступа сначала задайте TOUR_API_KEY')
    import uvicorn
    uvicorn.run('tour_api.app:create_app',factory=True,host=args.host,port=args.port,workers=1)


if __name__=='__main__':
    main()
