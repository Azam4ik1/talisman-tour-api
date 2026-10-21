from datetime import date, datetime, timedelta, timezone
import hashlib
import re
from urllib.parse import urldefrag
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, model_validator

LOCAL_TZ = timezone(timedelta(hours=5))


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def clean_text(text):
    return ' '.join(BeautifulSoup(str(text or ''), 'html.parser').get_text(' ', strip=True).split())


def stable_id(kind, value):
    return kind + '_' + hashlib.sha256(urldefrag(str(value))[0].encode()).hexdigest()[:20]


def parse_price(text):
    raw = ' '.join(str(text or '').split())
    currency = None
    for pattern, code in [(r'€|\bEUR\b', 'EUR'), (r'\$|\bUSD\b', 'USD'),
                          (r'₽|\bRUB\b|руб', 'RUB'), (r'\bUZS\b|сум', 'UZS'),
                          (r'у\.?\s*е\.?', 'CU')]:
        if re.search(pattern, raw, re.I):
            currency = code
            break
    match = re.search(r'\d[\d\s]*(?:[.,]\d{1,2})?', raw)
    amount = float(re.sub(r'\s+', '', match[0]).replace(',', '.')) if match else None
    return {'amount': amount, 'currency': currency, 'raw': raw}


class SearchRequest(BaseModel):
    departure: str = Field(default='Ташкент', min_length=2, max_length=80)
    country: str = Field(min_length=2, max_length=80)
    date_from: date
    date_to: date
    nights_from: int = Field(default=6, ge=1, le=30)
    nights_to: int = Field(default=14, ge=1, le=30)
    adults: int = Field(default=2, ge=1, le=6)
    children: list[int] = Field(default_factory=list, max_length=3)
    limit: int = Field(default=15, ge=1, le=50)

    @model_validator(mode='after')
    def validate_search(self):
        today = datetime.now(LOCAL_TZ).date()
        if self.date_from < today:
            raise ValueError('Дата вылета не может быть в прошлом')
        if self.date_to < self.date_from or (self.date_to-self.date_from).days > 30:
            raise ValueError('Диапазон дат должен составлять от 0 до 30 дней')
        if (self.date_to-today).days > 365:
            raise ValueError('Поиск ограничен ближайшими 365 днями')
        if self.nights_to < self.nights_from:
            raise ValueError('Конец диапазона ночей меньше начала')
        if any(age < 0 or age > 17 for age in self.children):
            raise ValueError('Возраст детей должен быть от 0 до 17 лет')
        return self


def publication(item):
    price = item.get('price') or {}
    lines = [clean_text(item.get('title') or item.get('hotel_name'))]
    for field in ['country', 'resort', 'duration_text', 'departure_text', 'meal']:
        if item.get(field):
            lines.append(clean_text(item[field]))
    if item.get('nights') and not item.get('duration_text'):
        lines.append(f"Ночей: {item['nights']}")
    if price.get('amount') is not None:
        basis = {'per_person':'за человека', 'total':'за весь состав', 'unknown':'база цены не указана'}.get(item.get('price_basis'), 'база цены не указана')
        lines.append(f"От {price['amount']:g} {price.get('currency') or ''} ({basis})")
    if item.get('description'):
        lines.append(clean_text(item['description']))
    lines.append('Цена и наличие требуют подтверждения перед бронированием.')
    return {**item, 'text':'\n'.join(filter(None, lines)), 'publication_status':'draft'}
