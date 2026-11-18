"""Best-effort public product-page price extraction; no logins or access bypass."""
import json
import re
from html.parser import HTMLParser
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = []
        self.jsonld = []
        self._script = False
        self._parts = []

    def handle_starttag(self, tag, attrs):
        props = dict(attrs)
        if tag == 'meta':
            self.meta.append(props)
        elif tag == 'script' and props.get('type', '').lower() == 'application/ld+json':
            self._script = True
            self._parts = []

    def handle_data(self, data):
        if self._script:
            self._parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self._script:
            self.jsonld.append(''.join(self._parts))
            self._script = False


def _price(value):
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r'[^0-9.]', '', value.replace(',', ''))
    try:
        number = float(cleaned)
        return int(number) if number > 0 else None
    except ValueError:
        return None


def _json_prices(item, results):
    if isinstance(item, list):
        for child in item:
            _json_prices(child, results)
    elif isinstance(item, dict):
        t = item.get('@type', '')
        types = t if isinstance(t, list) else [t]
        if any(x in ('Offer', 'AggregateOffer') for x in types):
            currency = item.get('priceCurrency', 'KRW')
            price = _price(item.get('price') or item.get('lowPrice'))
            if price and currency in ('KRW', '₩', None, ''):
                results.append(('JSON-LD Offer', price))
        for key, child in item.items():
            if key in ('offers', '@graph', 'itemListElement', 'mainEntity'):
                _json_prices(child, results)


def get_public_price(url, timeout=12):
    parsed = urlparse(url.strip())
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('http(s) 상품 주소를 입력해 주세요.')
    # This deliberately reads only public page metadata, not private APIs.
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; PriceCompareDesktop/2.0)', 'Accept': 'text/html'})
    with urlopen(req, timeout=timeout) as response:
        content_type = response.headers.get('Content-Type', '').lower()
        if 'text/html' not in content_type and 'application/xhtml' not in content_type:
            raise ValueError('HTML 상품 페이지가 아닙니다.')
        raw = response.read(2_000_000)
        charset = response.headers.get_content_charset() or 'utf-8'
        html = raw.decode(charset, errors='replace')
    parser = MetadataParser()
    parser.feed(html)
    candidates = []
    priority = {'product:price:amount', 'og:price:amount', 'product:sale_price:amount', 'twitter:data1'}
    for meta in parser.meta:
        key = (meta.get('property') or meta.get('name') or meta.get('itemprop') or '').lower()
        if key in priority or key == 'price':
            amount = _price(meta.get('content'))
            if amount:
                candidates.append((f'META {key}', amount))
    for chunk in parser.jsonld:
        try:
            _json_prices(json.loads(chunk), candidates)
        except (ValueError, TypeError):
            pass
    if not candidates:
        raise ValueError('공개 가격 메타데이터가 없습니다. 직접 입력하거나 해당 사이트의 공식 연동이 필요합니다.')
    # Prefer structured Offer pricing over generic social metadata.
    candidates.sort(key=lambda pair: (0 if pair[0] == 'JSON-LD Offer' else 1))
    source, amount = candidates[0]
    return amount, source
