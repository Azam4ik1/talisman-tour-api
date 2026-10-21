"""Collect only data rendered by the ordinary public pages, using a fresh browser."""
from contextlib import asynccontextmanager
import asyncio
from datetime import date
import re
from urllib.parse import parse_qs, urlparse
import httpx
from playwright.async_api import async_playwright, TimeoutError as BrowserTimeout
from .domain import SearchRequest, now_iso
from .parsers import BASE, SourceFormatError, parse_catalogue, parse_countries, parse_hot, parse_search

MONTHS=['январь','февраль','март','апрель','май','июнь','июль','август','сентябрь','октябрь','ноябрь','декабрь']
SHORT=['янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек']


def verify_submitted(url, request):
    query=parse_qs(urlparse(url).query)
    expected={'datefrom':request.date_from.strftime('%d.%m.%Y'),
              'dateto':request.date_to.strftime('%d.%m.%Y'),
              'nightsfrom':str(request.nights_from),'nightsto':str(request.nights_to),
              'adults':str(request.adults),'child':str(len(request.children))}
    for index,age in enumerate(request.children,1):
        expected[f'childage{index}']=str(age)
    for key,value in expected.items():
        actual=query.get(key,['0' if key=='child' else ''])[0]
        if actual!=value:
            raise SourceFormatError(f'Источник применил другие параметры ({key}); результат не выдан')
    return expected


class PublicSource:
    @asynccontextmanager
    async def page(self, path):
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            try:
                context=await browser.new_context(locale='ru-RU',timezone_id='Asia/Tashkent',viewport={'width':1440,'height':1000})
                page=await context.new_page()
                page.set_default_timeout(15000)
                response=await page.goto(BASE+path,wait_until='domcontentloaded',timeout=60000)
                if response and response.status>=400:
                    raise SourceFormatError(f'Источник вернул HTTP {response.status}')
                yield page
            finally:
                await browser.close()

    async def refresh(self, kind):
        if kind in ('catalogue','countries'):
            route='/tury' if kind=='catalogue' else '/strany'
            async with httpx.AsyncClient(timeout=40,follow_redirects=True) as client:
                response=await client.get(BASE+route)
                response.raise_for_status()
            return (parse_catalogue if kind=='catalogue' else parse_countries)(response.text)
        if kind!='hot':
            raise ValueError('Неизвестный раздел')
        async with self.page('/goryashchie-tury') as page:
            await page.locator('.TVHotItem .TVHotHotel').first.wait_for(timeout=60000)
            label=''
            human=page.locator('.TVHotPriceHuman').first
            if await human.count():
                label=await human.evaluate("el => [el.textContent,getComputedStyle(el,'::before').content,getComputedStyle(el,'::after').content].filter(x=>x && x!=='none' && x!=='normal').join(' ')")
            return parse_hot(await page.content(),label)

    async def ready(self,page):
        await page.locator('.TVSearchButton').wait_for(timeout=60000)
        for selector in ['.TVHotelListSelectWaiter','.TVResortListSelectWaiter']:
            await page.locator(selector).wait_for(state='hidden',timeout=60000)

    async def choose_named(self,page,field,item_selector,name):
        current=page.locator(field+' .TVMainSelectContent')
        if (await current.inner_text()).strip().casefold()==name.strip().casefold():
            return
        await page.locator(field).click()
        choice=page.locator(item_selector).filter(has_text=re.compile('^'+re.escape(name.strip())+'$',re.I)).first
        try:
            await choice.click(timeout=10000)
        except BrowserTimeout:
            raise SourceFormatError(f'В форме источника недоступен выбор: {name}') from None
        await page.locator('h1').click()
        await self.ready(page)
        if (await current.inner_text()).strip().casefold()!=name.strip().casefold():
            raise SourceFormatError(f'Не удалось подтвердить выбор: {name}')

    async def click_date(self,page,target):
        for _ in range(13):
            for sheet in await page.locator('.TVFlyDatesSelectTooltip .TVCalendarSheetControl').all():
                month=(await sheet.locator('.TVCalendarTitleControlMonth').inner_text()).strip().casefold()
                year=int((await sheet.locator('.TVCalendarTitleControlYear').inner_text()).strip())
                if month==MONTHS[target.month-1] and year==target.year:
                    cell=sheet.locator(f'.TVCalendarTableCell[data-value="{target.day}"]:not(.TVCalendarDisabledCell)')
                    if not await cell.count():
                        raise SourceFormatError(f'Дата {target.isoformat()} недоступна в календаре источника')
                    await cell.click()
                    return
            arrow=page.locator('.TVFlyDatesSelectTooltip .TVCalendarSliderViewRightButton:not(.TVDisabled)')
            if not await arrow.count():
                break
            await arrow.click()
        raise SourceFormatError('Нужный месяц недоступен в календаре источника')

    async def configure(self,page,request):
        await self.ready(page)
        await self.choose_named(page,'.TVDepartureFilter','.TVDepartureSelectTooltip .TVDepartureTableItemControl',request.departure)
        await self.choose_named(page,'.TVCountryFilter','.TVCountrySelectTooltip .TVComplexListItemContent',request.country)
        await page.locator('.TVFlyDatesFilter').click()
        await self.click_date(page,request.date_from)
        if not await page.locator('.TVFlyDatesSelectTooltip').is_visible():
            await page.locator('.TVFlyDatesFilter').click()
        await self.click_date(page,request.date_to)
        await page.locator('h1').click()
        await page.locator('.TVNightsFilter').click()
        for number in [request.nights_from,request.nights_to]:
            label=page.locator('.TVRangeCellLabel').filter(has_text=re.compile('^'+str(number)+'$'))
            option=page.locator('.TVRangeSelectTooltip .TVRangeTableCell').filter(has=label)
            if not await option.count():
                raise SourceFormatError(f'Источник не поддерживает длительность {number} ночей')
            await option.click()
        await page.locator('h1').click()
        await page.locator('.TVTouristsFilter').click()
        current=int(re.search(r'\d+',await page.locator('.TVTouristStatic .TVTouristCount').inner_text())[0])
        action='.TVTouristActionPlus' if request.adults>current else '.TVTouristActionMinus'
        for _ in range(abs(request.adults-current)):
            await page.locator('.TVTouristStatic '+action).click()
        for age in request.children:
            if age<2 or age>15:
                raise SourceFormatError('Текущая форма поддерживает точный возраст детей от 2 до 15 лет; для младенцев есть только общая группа «до 2»')
            await page.locator('.TVTouristButton').click()
            label=page.locator('.TVSelectChildAgeValue').filter(has_text=re.compile('^'+str(age)+'$'))
            await page.locator('.TVSelectChildAgeItem').filter(has=label).click()
        await page.locator('.TVTouristsSelectTooltipSubmit .TVButtonControl').click()
        selected={}
        for name,field in [('departure','.TVDepartureFilter'),('country','.TVCountryFilter'),('dates','.TVFlyDatesFilter'),('nights','.TVNightsFilter'),('tourists','.TVTouristsFilter')]:
            selected[name]=(await page.locator(field+' .TVMainSelectContent').inner_text()).strip()
        if selected['departure'].casefold()!=request.departure.casefold() or selected['country'].casefold()!=request.country.casefold():
            raise SourceFormatError('Форма изменила выбранное направление')
        return selected

    async def search(self,request: SearchRequest):
        async with self.page('/poisk-turov') as page:
            selected=await self.configure(page,request)
            sent=None
            # On this widget the first click can finish initialization without starting a request.
            # Retry once only after observing that no search request was sent.
            for attempt in range(2):
                try:
                    async with page.expect_request(lambda r: urlparse(r.url).path=='/xml/modsearch.php',timeout=12000) as event:
                        await page.locator('.TVSearchButton').click()
                    sent=await event.value
                    break
                except BrowserTimeout:
                    if attempt==1:
                        raise SourceFormatError('Форма не запустила поиск. Источник может быть недоступен или изменён') from None
            verified=verify_submitted(sent.url,request)
            try:
                await page.locator('.TVResultItem .TVHotelInfoTitleLink').first.wait_for(timeout=90000)
            except BrowserTimeout:
                results=page.locator('.TVSearchResults')
                message=(await results.inner_text()) if await results.count() else ''
                if re.search(r'тур[ыов]* не найден|нет туров|по вашему запросу ничего',message,re.I):
                    return {'items':[],'selection_verified':True,'selected':selected,'submitted':verified,'fetched_at':now_iso(),'truncated':False}
                raise SourceFormatError('Поиск не вернул карточки за отведённое время; это не означает отсутствие туров') from None
            # Images are loaded only when their cards enter the viewport.
            cards=page.locator('.TVResultItem')
            for index in range(min(await cards.count(),request.limit)):
                card=cards.nth(index)
                try:
                    await card.scroll_into_view_if_needed(timeout=2000)
                    await card.locator('.TVPhotoGalleryImage[style*="background-image"]').first.wait_for(state='attached',timeout=1500)
                except BrowserTimeout:
                    pass  # Missing source photographs remain an empty list, never fabricated.
            rows=parse_search(await page.content(),request.model_dump(mode='json'))
            if not rows:
                raise SourceFormatError('Результаты показаны, но их структуру не удалось прочитать')
            return {'items':rows[:request.limit],'selection_verified':True,'selected':selected,'submitted':verified,
                    'fetched_at':now_iso(),'truncated':True,'available_on_loaded_page':len(rows),
                    'coverage':'Минимальные цены по отелям из загруженной порции результатов; полный перебор не выполняется.'}
