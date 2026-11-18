"""Public Musinsa pages only. No login or private API access."""
import json
import os
import sys
from pathlib import Path
import re
from datetime import datetime
from html import unescape
from urllib.parse import quote, urlparse
from price_fetch import MetadataParser, _json_prices, _price

DETAIL_RE = re.compile(r'^/products/(\d+)(?:/selling)?/?$')

def normalize_detail(url):
    p=urlparse(url)
    if p.scheme != 'https' or p.hostname not in ('www.musinsa.com','musinsa.com','m.musinsa.com'):
        return ''
    m=DETAIL_RE.fullmatch(p.path)
    return 'https://www.musinsa.com/products/'+m.group(1) if m else ''

def price_from_html(html):
    """Accept only explicit KRW product Offer / price metadata, not arbitrary numbers."""
    parser=MetadataParser()
    parser.feed(html)
    prices=[]
    for chunk in parser.jsonld:
        try: _json_prices(json.loads(chunk), prices)
        except (ValueError, TypeError): pass
    if prices: return prices[0][1],prices[0][0]
    for meta in parser.meta:
        prop=(meta.get('property') or meta.get('name') or '').lower()
        if prop in ('product:sale_price:amount','product:price:amount','og:price:amount'):
            value=_price(meta.get('content'))
            if value: return value,'META '+prop
    return None,''

def visible_sale_price(text):
    """Conservative fallback for the rendered Musinsa product's MAIN price block.

    Only accept an explicitly won-denominated number near a sale price label,
    or a discount percentage immediately preceding a won price. Ignore coupon,
    rewards, delivery, original list prices and arbitrary numbers elsewhere.
    """
    if not text:
        return None, ''
    clean=unescape(str(text)).replace('\u00a0',' ')
    lines=[re.sub(r'\s+',' ',line).strip() for line in clean.splitlines()]
    lines=[line for line in lines if line]
    # Musinsa sometimes shows a membership/coupon price BEFORE the actual sale price.
    # Evaluate actual discount-tagged prices first.
    for i,line in enumerate(lines):
        if any(x in line for x in ('쿠폰적용가','쿠폰 적용가','적립','배송','혜택')):
            continue
        m=re.search(r'(?<!\d)([1-9]\d?)\s*%\s*([1-9]\d{0,2}(?:,\d{3})+)\s*원',line)
        if m:
            amount=int(m.group(2).replace(',',''))
            if 1000 <= amount <= 100000000:
                return amount,'상세화면 할인 판매가'
    for i,line in enumerate(lines):
        if any(x in line for x in ('쿠폰적용가','쿠폰 적용가','적립','배송','혜택','정가','소비자가')):
            continue
        if not any(x in line for x in ('판매가','할인가','판매 가격')):
            continue
        m=re.search(r'([1-9]\d{0,2}(?:,\d{3})+)\s*원',line)
        if m:
            amount=int(m.group(1).replace(',',''))
            if 1000 <= amount <= 100000000:
                return amount,'상세화면 판매가'
    return None,''


def product_price(html, visible_text=''):
    """Use verified structured product offers; fallback to labeled visible sale price."""
    price,source=price_from_html(html)
    if price is not None:
        return price,source
    return visible_sale_price(visible_text)


def find_first_link(links):
    """Return first actual visible product link in DOM order. No model-code matching."""
    for item in links:
        if item.get('visible') is False: continue
        link=normalize_detail(str(item.get('href','')))
        if link:
            return {'url':link,'label':str(item.get('label','')).strip()[:240]}
    return None

def discover(query, template):
    result={'shop':'무신사','size':'사이즈 미확인','price':None,'url':'','name':'',
            'state':'검색불가','checked':'','source':'','search_url':'','detail':''}
    if not query.strip(): return result
    search=template.replace('{q}',quote(query.strip(),safe=''))
    result['search_url']=search
    if getattr(sys, 'frozen', False):
        bundled = Path(getattr(sys, '_MEIPASS', Path(sys.executable).resolve().parent)) / 'playwright_browsers'
        if bundled.is_dir():
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(bundled)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        result['state']='브라우저 모듈 필요'
        result['detail']='브라우저 자동화 구성 요소 누락: 배포 폴더의 파일을 확인하세요.'
        return result
    try:
        with sync_playwright() as p:
            browser=None
            err=None
            for channel in (None, 'chrome', 'msedge'):
                try:
                    opts={'headless':True, 'timeout':12000}
                    if channel: opts['channel']=channel
                    browser=p.chromium.launch(**opts)
                    break
                except Exception as e: err=e
            if browser is None:
                result['state']='브라우저 실행불가'
                result['detail']='포함된 Chromium 실행 실패: '+str(err)[:150]
                return result
            try:
                page=browser.new_page(locale='ko-KR', viewport={'width':1380,'height':920})
                page.goto(search,wait_until='domcontentloaded',timeout=25000)
                try: page.locator('a[href*="/products/"]').first.wait_for(state='attached',timeout=12000)
                except Exception: pass
                # Document order for product anchors; deduplicate product-card links.
                raw=page.locator('a[href*="/products/"]').evaluate_all("""els => els.map(a => ({href:a.href, label:(a.innerText || a.getAttribute('aria-label') || a.querySelector('img')?.alt || '').trim(), visible:!!a.getClientRects().length})).filter(x=>x.visible)""")
                first=find_first_link(raw)
                if not first:
                    result['state']='상품미확인'
                    return result
                result['url']=first['url']
                result['name']=first['label']
                page.goto(first['url'],wait_until='domcontentloaded',timeout=25000)
                try:
                    page.locator('body').wait_for(state='visible',timeout=8000)
                    page.wait_for_timeout(1400)
                except Exception: pass
                # Verify actual destination is still a product URL.
                final=normalize_detail(page.url)
                if not final:
                    result['url']=''
                    result['state']='상세페이지 접근불가'
                    return result
                result['url']=final
                actual_name=page.locator('meta[property="og:title"]').first.get_attribute('content') if page.locator('meta[property="og:title"]').count() else ''
                result['name']=(actual_name or result['name'] or '상품명 미확인')[:240]
                # MAIN is preferred to avoid footer/recommendation prices; a whole page
                # fallback is not used because it could mix prices from other products.
                visible=page.locator('main').first.inner_text(timeout=5000) if page.locator('main').count() else ''
                value,source=product_price(page.content(), visible)
                result['price']=value
                result['source']=source
                result['state']='가격확인·사이즈미확인' if value is not None else '가격미확인'
                if value is None: result['detail']='상세페이지에서 확인 가능한 판매가 정보가 없습니다.'
                result['checked']=datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')
            finally:
                browser.close()
    except Exception as e:
        result['state']='조회실패'
        result['detail']=str(e)[:250]
    return result
