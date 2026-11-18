"""Conservative public search discovery (no login/circumvention).

Bing public RSS discovery may return nothing or block requests; never fabricate results.
"""
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urlencode, urlparse, quote, urljoin, unquote
from urllib.request import Request, urlopen
from datetime import datetime
from price_fetch import get_public_price

SEARCH_HOST = 'https://www.bing.com/search'


def host_for(template):
    host = (urlparse(template).hostname or '').lower()
    return host[4:] if host.startswith('www.') else host


def product_matches(query, title, url):
    """Reject vague matches. For model codes insist exact normalized code in result."""
    q = query.strip().lower()
    if not q or len(q) < 4:
        return False
    hay = (title + ' ' + url).lower()
    if re.search(r'[a-z0-9]+[-_][a-z0-9-]+', q, re.I) or re.fullmatch(r'[a-z0-9]{6,}', q, re.I):
        normalized = lambda s: re.sub('[^a-z0-9]', '', s.lower())
        return normalized(q) in normalized(hay)
    # Names must have >= 2 meaningful words, to reduce false results.
    terms = [v for v in re.split(r'\s+', q) if len(v) >= 2]
    return len(terms) >= 2 and all(t in hay for t in terms)


def search_url(template, query):
    """Always provide an official search link, never mislabel it as product URL."""
    if not isinstance(template, str) or template.count('{q}') != 1:
        return ''
    url = template.replace('{q}', quote(query.strip(), safe=''))
    p = urlparse(url)
    if p.scheme not in ('http', 'https') or not p.hostname:
        return ''
    return url


class ProductLinks(HTMLParser):
    """Find visible public links in server-rendered shop search results."""
    def __init__(self):
        super().__init__()
        self.links = []
        self.current_url = None
        self.text_parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.current_url = dict(attrs).get('href')
            self.text_parts = []

    def handle_data(self, data):
        if self.current_url is not None:
            self.text_parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.current_url is not None:
            self.links.append((self.current_url, ' '.join(self.text_parts).strip()))
            self.current_url = None
            self.text_parts = []


def valid_detail_link(link, host):
    p = urlparse(link)
    domain = (p.hostname or '').lower()
    if p.scheme != 'https' or not (domain == host or domain.endswith('.' + host)):
        return False
    path = p.path.lower().rstrip('/')
    if not path or path == '/' or any(v in path for v in ('/search','/csearch','/category','/display/search','/login')):
        return False
    # Search links must not be accepted as product detail URLs.
    return True


def discover_from_shop(template, query, host, timeout):
    """Search official results first; do not pretend a search-page URL is a product page.

    Works only when search cards and their product links appear in public HTML.
    JavaScript-only shopping sites will correctly yield 'not found'.
    """
    search = search_url(template, query)
    if not search: return None, '검색불가'
    try:
        req = Request(search, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'text/html'})
        with urlopen(req, timeout=timeout) as response:
            ct = response.headers.get('Content-Type', '').lower()
            if 'text/html' not in ct: return None, '검색불가'
            raw = response.read(1_500_000)
            encoding = response.headers.get_content_charset() or 'utf-8'
        parser = ProductLinks()
        parser.feed(raw.decode(encoding, errors='replace'))
        for relative, title in parser.links:
            url = urljoin(search, relative)
            if valid_detail_link(url, host) and product_matches(query, title, unquote(url)):
                return url, '상품 상세 URL 확인'
        return None, '상품미확인'
    except Exception:
        return None, '검색불가'


def musinsa_product_url(url):
    """Accept only canonical Musinsa product detail links, not listings or ads."""
    p = urlparse(url)
    if p.scheme != 'https' or (p.hostname or '').lower() not in ('www.musinsa.com', 'musinsa.com', 'm.musinsa.com'):
        return ''
    match = re.fullmatch(r'/products/(\d+)(?:/selling)?/?', p.path)
    return 'https://www.musinsa.com/products/' + match.group(1) if match else ''


def discover_musinsa(template, query, timeout=9):
    """Conservative Musinsa-only first-stage discovery with product identity check.

    No claimed live price if the actual product detail price metadata is absent.
    """
    fallback = search_url(template, query)
    result = {'shop': '무신사', 'url': '', 'price': None, 'state': '상품미확인',
              'size': '사이즈 미확인', 'search_url': fallback}
    if not fallback or len(query.strip()) < 3:
        result['state'] = '검색불가'
        return result
    candidates = []
    access_failed = False
    try:
        req = Request(fallback, headers={'User-Agent':'Mozilla/5.0', 'Accept':'text/html'})
        with urlopen(req, timeout=timeout) as response:
            if 'html' not in response.headers.get('Content-Type','').lower():
                raise ValueError('HTML 검색 페이지가 아닙니다')
            raw = response.read(2_000_000)
            encoding = response.headers.get_content_charset() or 'utf-8'
        html = raw.decode(encoding, errors='replace')
        parser = ProductLinks(); parser.feed(html)
        for href, title in parser.links:
            target = musinsa_product_url(urljoin(fallback, href))
            if target and product_matches(query, title, unquote(target)):
                candidates.append(target)
        # Some server rendered pages encode canonical product URLs in JSON rather than anchors.
        # Only model-code searches may match URL-independent snippets; names need anchored text.
        is_model = bool(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{5,}', query.strip()) and re.search(r'\d',query))
        if is_model:
            normalized = re.sub('[^a-z0-9]', '', query.lower())
            for m in re.finditer(r'(?:https?:\\?/\\?/)?(?:www\.)?musinsa\.com/products/\d+|/products/\d+', html):
                snippet = html[max(0,m.start()-450): min(len(html),m.end()+450)]
                if normalized in re.sub('[^a-z0-9]', '', snippet.lower()):
                    target = musinsa_product_url(urljoin(fallback, m.group().replace('\\/','/')))
                    if target: candidates.append(target)
    except Exception:
        access_failed = True
    # Fallback to publicly indexed detail pages, but validate actual product identity
    # against page title before using its URL.
    if not candidates:
        try:
            rss = SEARCH_HOST + '?' + urlencode({'q':f'site:musinsa.com/products/ "{query.strip()}"','format':'rss'})
            req = Request(rss, headers={'User-Agent':'Mozilla/5.0','Accept':'application/rss+xml'})
            with urlopen(req, timeout=timeout) as response:
                if 'xml' not in response.headers.get('Content-Type','').lower(): raise ValueError('RSS 조회불가')
                root=ET.fromstring(response.read(700_000))
            for item in root.findall('.//item'):
                link=musinsa_product_url((item.findtext('link') or '').strip())
                title=item.findtext('title') or ''
                if link and product_matches(query, title, link): candidates.append(link)
        except Exception:
            pass
    candidates = list(dict.fromkeys(candidates))
    if not candidates:
        result['state']='검색불가' if access_failed else '상품미확인'
        return result
    # Do not select a random among several colorways or variants.
    if len(candidates)>1:
        result['state']='상품 확인 필요'
        result['candidate_count']=len(candidates)
        return result
    result['url']=candidates[0]
    result['state']='가격미확인'
    try:
        price, source=get_public_price(candidates[0],timeout=timeout)
        result.update(price=price,source=source,state='가격 확인·사이즈 미확인',
                      checked=datetime.now().astimezone().strftime('%Y-%m-%d %H:%M'))
    except Exception:
        pass
    return result


def discover_one(site, template, query, timeout=9):
    """Find a likely product URL through a public indexed search listing.

    Returns only an explicit state if not reliably discoverable. Does not imply
    product is unavailable when public indexing has no results.
    """
    if site == '무신사':
        return discover_musinsa(template, query, timeout)
    fallback = search_url(template, query)
    if site.upper() == 'KREAM':
        return {'shop':site, 'url':'', 'price':None, 'state':'로그인 필요', 'size':'사이즈 미확인', 'search_url':fallback}
    host = host_for(template)
    if not host or len(query.strip()) < 3:
        return {'shop':site, 'url':'', 'price':None, 'state':'검색불가', 'size':'사이즈 미확인', 'search_url':fallback}
    direct_url, direct_state = discover_from_shop(template, query, host, timeout)
    if direct_url:
        result = {'shop':site, 'url':direct_url, 'price':None, 'state':'가격미확인',
                  'size':'사이즈 미확인', 'search_url':fallback}
        try:
            price, source = get_public_price(direct_url, timeout=timeout)
            result.update(price=price, state='가격 확인·사이즈 미확인', source=source,
                          checked=datetime.now().astimezone().strftime('%Y-%m-%d %H:%M'))
        except Exception:
            pass
        return result
    try:
        search = SEARCH_HOST + '?' + urlencode({'q':f'site:{host} "{query.strip()}"', 'format':'rss'})
        req = Request(search, headers={'User-Agent':'Mozilla/5.0', 'Accept':'application/rss+xml, application/xml, text/xml'})
        with urlopen(req, timeout=timeout) as response:
            if 'xml' not in response.headers.get('Content-Type','').lower() and 'rss' not in response.headers.get('Content-Type','').lower():
                raise ValueError('검색 결과 제공되지 않음')
            data = response.read(600_000)
        items = ET.fromstring(data).findall('.//item')
        found = []
        for item in items:
            u = (item.findtext('link') or '').strip()
            title = (item.findtext('title') or '').strip()
            p = urlparse(u)
            h = (p.hostname or '').lower()
            if p.scheme != 'https' or not (h == host or h.endswith('.'+host)):
                continue
            # Filter general search and home pages, even if the query is shown in URL.
            if not p.path or p.path == '/' or any(x in p.path.lower() for x in ('/search', '/csearch')):
                continue
            if product_matches(query, title, u):
                found.append(u)
        if not found:
            return {'shop':site,'url':'','price':None,'state':direct_state if direct_state == '검색불가' else '상품미확인','size':'사이즈 미확인', 'search_url':fallback}
        url = found[0]
        result={'shop':site,'url':url,'price':None,'state':'가격미확인','size':'사이즈 미확인', 'search_url':fallback}
        try:
            price, source = get_public_price(url,timeout=timeout)
            result.update(price=price, state='가격 확인·사이즈 미확인', source=source,
                          checked=datetime.now().astimezone().strftime('%Y-%m-%d %H:%M'))
        except Exception:
            pass
        result['search_url'] = fallback
        return result
    except Exception:
        return {'shop':site,'url':'','price':None,'state':'검색불가','size':'사이즈 미확인', 'search_url':fallback}
