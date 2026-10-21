import re
from urllib.parse import parse_qs, urljoin, urlparse
from bs4 import BeautifulSoup
from .domain import clean_text, now_iso, parse_price, stable_id

BASE = 'https://talisman-tour.uz'


class SourceFormatError(RuntimeError):
    pass


def text(node, selector):
    selected = node.select_one(selector)
    return selected.get_text(' ', strip=True) if selected else ''


def safe_url(value, base=BASE):
    url = urljoin(base, value or '')
    return url if value and urlparse(url).scheme in ('https', 'http') else None


def image_urls(node):
    urls = []
    for image in node.select('img[src], [style*="background-image"]'):
        value = image.get('src')
        if not value:
            match = re.search(r'url\([\"\']?(.*?)[\"\']?\)', image.get('style',''))
            value = match[1] if match else None
        url = safe_url(value)
        if url and url not in urls:
            urls.append(url)
    return urls


def price_basis(label):
    if re.search(r'чел|person',label,re.I):
        return 'per_person'
    if re.search(r'всех|туристов|total',label,re.I):
        return 'total'
    return 'unknown'


def nights(text_value):
    if re.search(r'(?:\d+\s*[-–—]\s*\d+|от\s+\d+)\s*ноч',text_value,re.I):
        return None
    match = re.search(r'(\d+)\s*ноч', text_value)
    return int(match[1]) if match else None


def parse_hot(html, basis_label=''):
    soup = BeautifulSoup(html,'html.parser')
    items = {}
    for card in soup.select('.TVHotItem'):
        title = text(card,'.TVHotHotel')
        date_text = text(card,'.TVHotDateNight')
        if not title:
            continue
        fields = dict(title=title, hotel_name=title, country=text(card,'.TVHotCountry'),
                      resort=text(card,'.TVHotResort'), departure_text=text(card,'.TVHotDeparture'),
                      date_text=date_text.split(',')[0], departure_date=None, nights=nights(date_text),
                      duration_text=date_text, meal=text(card,'.TVHotMeal'),
                      stars=len(card.select('.TVHotItemWrap .TVHotStarWrap > div')) or None,
                      price=parse_price(text(card,'.TVHotNewPrice')),
                      old_price=parse_price(text(card,'.TVHotOldPrice')),
                      price_basis=price_basis(basis_label), price_basis_label=basis_label or None,
                      images=image_urls(card), description=None,
                      source_url=BASE+'/goryashchie-tury', fetched_at=now_iso(), kind='hot',
                      availability_confirmed=False)
        discount = re.search(r'\d+', text(card,'.TVHotDiscount'))
        fields['discount_percent'] = int(discount[0]) if discount else None
        identity = '|'.join(str(fields.get(key,'')) for key in ['hotel_name','country','resort','departure_text','date_text','nights','meal'])
        fields['id'] = stable_id('hot',identity)
        fields['hotel_id'] = stable_id('hotel',fields['country']+'|'+title)
        if fields['price']['amount'] is None:
            raise SourceFormatError('У карточки горящего тура не прочитана цена')
        items[fields['id']] = fields
    if not items:
        raise SourceFormatError('Карточки горящих туров не найдены: источник не загрузился или изменил разметку')
    return list(items.values())


def parse_catalogue(html):
    soup=BeautifulSoup(html,'html.parser')
    items={}
    for card in soup.select('article.card'):
        title=text(card,'h3')
        link=card.select_one('a.media-link[href]')
        if not title or not link:
            continue
        url=safe_url(link['href'])
        if urlparse(url).hostname!='talisman-tour.uz':
            continue
        price=parse_price(text(card,'.price'))
        currency=card.select_one('[itemprop="priceCurrency"][content]')
        if currency and currency['content'] in ('USD','EUR','UZS','RUB'):
            price['currency']=currency['content']
            price['currency_source']='source_structured_data'
        badges=[b.get_text(' ',strip=True) for b in card.select('.badge')]
        duration=next((b for b in badges if re.search(r'ноч|дн',b)),None)
        ident=stable_id('tour',url)
        items[ident]={'id':ident,'title':title,'description':text(card,'.desc'),
                      'tags':[b.get_text(' ',strip=True) for b in card.select('.tag')],
                      'badges':badges, 'country':None, 'duration_text':duration,'nights':nights(duration or ''),
                      'images':image_urls(card),'price':price,'price_basis':'unknown',
                      'source_url':url,'fetched_at':now_iso(),'kind':'catalogue','availability_confirmed':False}
    if not items:
        raise SourceFormatError('Карточки каталога не найдены: источник не загрузился или изменил разметку')
    return list(items.values())


def parse_countries(html):
    soup=BeautifulSoup(html,'html.parser')
    items={}
    for link in soup.select('a[href]'):
        url=safe_url(link['href'])
        if not url:
            continue
        parsed=urlparse(url)
        if parsed.hostname!='talisman-tour.uz' or not re.fullmatch(r'/strany/[^/]+/?',parsed.path):
            continue
        name=link.get_text(' ',strip=True)
        if name:
            items[url]={'id':stable_id('country',url),'name':name,'source_url':url,'fetched_at':now_iso()}
    if not items:
        raise SourceFormatError('Список стран не найден')
    return list(items.values())


def parse_search(html, request):
    soup=BeautifulSoup(html,'html.parser')
    items={}
    for card in soup.select('.TVResultItem'):
        title=text(card,'.TVHotelInfoTitleLink')
        link=card.select_one('.TVHotelInfoTitleLink[href]')
        if not title or not link:
            continue
        hotel_url=safe_url(link['href'])
        # Preserve the fragment: the provider identifies each hotel there.
        parsed_url=urlparse(hotel_url or '')
        provider_id=parse_qs(parsed_url.fragment.lstrip('!/')).get('hotel', [None])[0]
        identity=(parsed_url.netloc+'|'+provider_id if provider_id else
                  '|'.join([hotel_url or '',str(request.get('country')),text(card,'.TVResultItemSubTitle'),title.casefold()]))
        hotel_id=stable_id('hotel',identity)
        price=parse_price(text(card,'.TVResultItemNewPrice'))
        if price['amount'] is None:
            continue
        label=text(card,'.TVResultItemPriceType')
        rating=text(card,'.TVHotelInfoRating')
        ident=stable_id('offer',hotel_id+'|'+str(request))
        items[ident]={'id':ident,'hotel_id':hotel_id,'title':title,'hotel_name':title,
                     'description':text(card,'.TVResultItemDescription'),
                     'country':request.get('country'),'resort':text(card,'.TVResultItemSubTitle'),
                     'stars':len(card.select('.TVHotelInfoStar')) or None,
                     'rating':float(rating) if re.fullmatch(r'\d+(\.\d+)?',rating) else None,
                     'price':price,'price_basis':price_basis(label),'price_basis_label':label,
                     'images':image_urls(card),'hotel_url':hotel_url,'source_url':BASE+'/poisk-turov',
                     'fetched_at':now_iso(),'kind':'hotel_minimum','search':request,
                     'departure_date':None,'nights':None,'availability_confirmed':False}
    return list(items.values())
