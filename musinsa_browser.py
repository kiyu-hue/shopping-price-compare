"""Public Musinsa pages only. No login or private API access."""

from datetime import datetime
from html import unescape
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import quote, urlparse
from price_fetch import MetadataParser, _json_prices, _price

DETAIL_RE = re.compile(r"^/products/(\d+)(?:/selling)?/?$")


def normalize_detail(url):
    p = urlparse(url)
    if p.scheme != "https" or p.hostname not in (
        "www.musinsa.com",
        "musinsa.com",
        "m.musinsa.com",
    ):
        return ""
    m = DETAIL_RE.fullmatch(p.path)
    return "https://www.musinsa.com/products/" + m.group(1) if m else ""


def parse_musinsa_json(script_text):
    """무신사 스크립트 내부 JSON에서 가격, 사이즈, 품절여부 파싱"""
    if not script_text:
        return None, [], ""

    try:
        # window.__MSS__ = {...}; 형태 처리
        if "window.__MSS__" in script_text:
            match = re.search(
                r"window\.__MSS__\s*=\s*(\{.*?\});", script_text, re.DOTALL
            )
            data = json.loads(match.group(1)) if match else {}
        else:
            data = json.loads(script_text)

        # 무신사 state 데이터 탐색
        state = data.get("state", {}) or data.get("props", {}).get(
            "pageProps", {}
        )
        goods = state.get("goods", {}) or state.get("goodsInfo", {})

        # 1. 메인 가격 추출
        price = goods.get("price", {}).get("delivPrice") or goods.get(
            "goodsPrice", {}
        ).get("salePrice")
        if not price and "price" in goods:
            price = goods.get("price")

        # 2. 사이즈 및 품절 정보 추출
        sizes = []
        options = goods.get("options", []) or goods.get("option", {}).get(
            "list", []
        )
        for opt in options:
            name = (
                opt.get("optionName")
                or opt.get("name")
                or opt.get("optionValue")
            )
            opt_price = (
                opt.get("price") or opt.get("salePrice") or price or "기본가"
            )
            is_soldout = opt.get("isSoldOut", False) or opt.get("stockQty", 1) == 0

            if name:
                sizes.append(
                    {
                        "size": name,
                        "price": opt_price,
                        "is_soldout": is_soldout,
                    }
                )

        return price, sizes, "JSON-State"
    except Exception:
        return None, [], ""


def find_first_link(links):
    """Return first actual visible product link in DOM order."""
    for item in links:
        if item.get("visible") is False:
            continue
        link = normalize_detail(str(item.get("href", "")))
        if link:
            return {"url": link, "label": str(item.get("label", "")).strip()[:240]}
    return None


def discover(query, template):
    result = {
        "shop": "무신사",
        "size": "사이즈 미확인",
        "price": None,
        "url": "",
        "name": "",
        "state": "검색불가",
        "checked": "",
        "source": "",
        "search_url": "",
        "detail": "",
    }
    if not query.strip():
        return result
    search = template.replace("{q}", quote(query.strip(), safe=""))
    result["search_url"] = search

    if getattr(sys, "frozen", False):
        bundled = (
            Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
            / "playwright_browsers"
        )
        if bundled.is_dir():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        result["state"] = "브라우저 모듈 필요"
        result["detail"] = (
            "브라우저 자동화 구성 요소 누락: 배포 폴더의 파일을 확인하세요."
        )
        return result

    try:
        with sync_playwright() as p:
            browser = None
            err = None
            # 차단 방지를 위한 User-Agent 설정
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

            for channel in (None, "chrome", "msedge"):
                try:
                    opts = {"headless": True, "timeout": 15000}
                    if channel:
                        opts["channel"] = channel
                    browser = p.chromium.launch(**opts)
                    break
                except Exception as e:
                    err = e

            if browser is None:
                result["state"] = "브라우저 실행불가"
                result["detail"] = (
                    "포함된 Chromium 실행 실패: " + str(err)[:150]
                )
                return result

            try:
                # 브라우저 컨텍스트 생성 시 User-Agent 차단 해제
                context = browser.new_context(
                    user_agent=user_agent,
                    locale="ko-KR",
                    viewport={"width": 1380, "height": 920},
                    extra_http_headers={"Referer": "https://www.musinsa.com/"},
                )
                page = context.new_page()

                # 1. 검색 페이지 이동
                page.goto(search, wait_until="domcontentloaded", timeout=25000)
                try:
                    page.locator('a[href*="/products/"]').first.wait_for(
                        state="attached", timeout=12000
                    )
                except Exception:
                    pass

                raw = page.locator('a[href*="/products/"]').evaluate_all(
                    """els => els.map(a => ({href:a.href, label:(a.innerText || a.getAttribute('aria-label') || a.querySelector('img')?.alt || '').trim(), visible:!!a.getClientRects().length})).filter(x=>x.visible)"""
                )
                first = find_first_link(raw)
                if not first:
                    result["state"] = "상품미확인"
                    return result

                result["url"] = first["url"]
                result["name"] = first["label"]

                # 2. 상세페이지 이동
                page.goto(
                    first["url"], wait_until="domcontentloaded", timeout=25000
                )
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass

                final = normalize_detail(page.url)
                if not final:
                    result["url"] = ""
                    result["state"] = "상세페이지 접근불가"
                    return result

                result["url"] = final
                actual_name = (
                    page.locator('meta[property="og:title"]')
                    .first.get_attribute("content")
                    if page.locator('meta[property="og:title"]').count()
                    else ""
                )
                result["name"] = (
                    actual_name or result["name"] or "상품명 미확인"
                )[:240]

                # 3. 무신사 JSON 스크립트 데이터 추출
                script_text = page.evaluate("""() => {
                    const el = document.querySelector('script#__NEXT_DATA__') || 
                               Array.from(document.querySelectorAll('script')).find(s => s.innerText.includes('window.__MSS__'));
                    return el ? el.innerText : '';
                }""")

                price, sizes_data, source = parse_musinsa_json(script_text)

                # JSON 파싱 실패 시 메타 태그 / visible 파싱 시도 (Fallback)
                if price is None:
                    og_price = (
                        page.locator('meta[property="product:price:amount"]')
                        .first.get_attribute("content")
                        if page.locator(
                            'meta[property="product:price:amount"]'
                        ).count()
                        else None
                    )
                    if og_price:
                        try:
                            price = int(float(og_price))
                            source = "META"
                        except ValueError:
                            pass

                # 4. 결과 정리
                result["price"] = price
                result["source"] = source or "상세화면"

                if sizes_data:
                    # UI 테이블 규격에 맞추어 사이즈 문자열 생성 (예: 260(품절), 270: 59,000원)
                    formatted_sizes = []
                    for s in sizes_data:
                        status = " [품절]" if s["is_soldout"] else ""
                        formatted_sizes.append(f"{s['size']}{status}")

                    result["size"] = ", ".join(formatted_sizes)
                    result["state"] = (
                        "조회성공" if price else "사이즈확인·가격미확인"
                    )
                elif price is not None:
                    result["size"] = "사이즈 정보 없음"
                    result["state"] = "가격확인·사이즈미확인"
                else:
                    result["state"] = "가격미확인"

                result["checked"] = (
                    datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
                )

            finally:
                browser.close()

    except Exception as e:
        result["state"] = "조회실패"
        result["detail"] = str(e)[:250]

    return result
