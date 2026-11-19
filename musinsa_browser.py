"""Public Musinsa pages only. No login or private API access."""

from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import quote, urlparse

from price_fetch import MetadataParser, _json_prices, _price

DETAIL_RE = re.compile(r"^/products/(\d+)(?:/selling)?/?$")
NOISE_LABELS = {"상품 상세로 이동", "상세페이지로 이동", "바로가기", ""}


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


def _safe_dict(val):
    return val if isinstance(val, dict) else {}


def _safe_list(val):
    return val if isinstance(val, list) else []


def parse_musinsa_json(script_text):
    """무신사 스크립트 내부 JSON에서 가격, 사이즈, 품절여부 파싱"""
    if not script_text:
        return None, [], ""

    try:
        if "window.__MSS__" in script_text:
            match = re.search(
                r"window\.__MSS__\s*=\s*(\{.*?\})\s*;?\s*(?:</script>|$)",
                script_text,
                re.DOTALL,
            )
            if not match:
                return None, [], ""
            data = json.loads(match.group(1))
        else:
            data = json.loads(script_text)

        data = _safe_dict(data)
        state = _safe_dict(data.get("state")) or _safe_dict(
            _safe_dict(data.get("props")).get("pageProps")
        )
        goods = _safe_dict(state.get("goods")) or _safe_dict(state.get("goodsInfo"))

        # 1. 메인 가격 추출
        price_obj = goods.get("price")
        goods_price_obj = _safe_dict(goods.get("goodsPrice"))

        price = (
            _price(_safe_dict(price_obj).get("delivPrice"))
            or _price(goods_price_obj.get("salePrice"))
            or _price(price_obj)
        )

        # 2. 사이즈 및 품절 정보 추출
        sizes = []
        raw_options = goods.get("options")
        if not isinstance(raw_options, list):
            raw_options = _safe_list(_safe_dict(goods.get("option")).get("list"))

        for opt in _safe_list(raw_options):
            opt_dict = _safe_dict(opt)
            name = (
                opt_dict.get("optionName")
                or opt_dict.get("name")
                or opt_dict.get("optionValue")
                or opt_dict.get("size")
            )
            opt_price = (
                _price(opt_dict.get("price"))
                or _price(opt_dict.get("salePrice"))
                or price
            )
            sold_flag = opt_dict.get("isSoldOut")
            stock_qty = opt_dict.get("stockQty")
            is_soldout = bool(sold_flag) or (
                isinstance(stock_qty, (int, float)) and stock_qty <= 0
            )

            if name:
                sizes.append(
                    {
                        "size": str(name).strip(),
                        "price": opt_price,
                        "is_soldout": is_soldout,
                    }
                )

        if price is not None or sizes:
            return price, sizes, "JSON-State"
        return None, [], ""
    except Exception:
        return None, [], ""


def _extract_from_html_metadata(html):
    """price_fetch의 MetadataParser와 _json_prices로 meta 및 JSON-LD 가격 추출"""
    if not html:
        return None, ""
    parser = MetadataParser()
    try:
        parser.feed(html)
    except Exception:
        return None, ""

    candidates = []
    priority = {
        "product:price:amount",
        "og:price:amount",
        "product:sale_price:amount",
        "twitter:data1",
    }
    for meta in parser.meta:
        key = (
            meta.get("property") or meta.get("name") or meta.get("itemprop") or ""
        ).lower()
        if key in priority or key == "price":
            amount = _price(meta.get("content"))
            if amount:
                candidates.append((f"META {key}", amount))

    for chunk in parser.jsonld:
        try:
            _json_prices(json.loads(chunk), candidates)
        except Exception:
            pass

    if not candidates:
        return None, ""
    candidates.sort(key=lambda pair: (0 if pair[0] == "JSON-LD Offer" else 1))
    source, amount = candidates[0]
    return amount, source


def _extract_visible_hints(page):
    """화면 텍스트에서 사이즈/품절/가격 후보를 보조로 추출"""
    try:
        body_text = page.locator("body").inner_text(timeout=4000) or ""
    except Exception:
        body_text = ""

    lines = [ln.strip() for ln in body_text.splitlines() if ln.strip()]
    size_items = []
    seen_sizes = set()

    # 신발/의류 사이즈 패턴(예: 230, 260, 275, S, M, L, XL, FREE) + 주변 품절/가격 힌트
    size_re = re.compile(r"^(?:[23]\d\d(?:\.\d)?|XXS|XS|S|M|L|XL|XXL|FREE|F)$", re.I)

    for idx, line in enumerate(lines):
        if size_re.match(line):
            s_key = line.upper()
            if s_key in seen_sizes:
                continue
            window = " ".join(lines[idx : min(idx + 4, len(lines))])
            soldout = any(k in window for k in ("품절", "SOLD OUT", "일시품절"))
            # 사이즈 바로 뒤 줄에 금액이 있으면 보조 가격으로 연결
            p_cand = None
            for nxt in lines[idx + 1 : min(idx + 4, len(lines))]:
                if "원" in nxt and len(nxt) <= 20:
                    p_cand = _price(nxt)
                    if p_cand:
                        break
            seen_sizes.add(s_key)
            size_items.append(
                {"size": line.upper(), "price": p_cand, "is_soldout": soldout}
            )
            if len(size_items) >= 12:
                break

    price_cand = None
    for line in lines[:120]:
        if any(k in line for k in ("할인가", "판매가", "구매가", "회원가")) and "원" in line:
            p = _price(line)
            if p and 1000 <= p <= 50_000_000:
                price_cand = p
                break

    return price_cand, size_items


def find_first_link(links):
    """Return first actual visible product link in DOM order."""
    for item in links:
        if item.get("visible") is False:
            continue
        link = normalize_detail(str(item.get("href", "")))
        if not link:
            continue
        label = str(item.get("label", "")).strip()
        if label in NOISE_LABELS:
            label = ""
        return {"url": link, "label": label[:240]}
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
            user_agent = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )

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
                result["detail"] = "포함된 Chromium 실행 실패: " + str(err)[:150]
                return result

            try:
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
                    """els => els.map(a => ({
                        href: a.href,
                        label: (a.innerText || a.getAttribute('aria-label') || a.querySelector('img')?.alt || '').trim(),
                        visible: !!a.getClientRects().length
                    })).filter(x => x.visible)"""
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
                ) or ""
                if actual_name.strip() in NOISE_LABELS:
                    actual_name = ""
                if not actual_name.strip():
                    try:
                        h1_text = (page.locator("h1").first.inner_text(timeout=2000) or "").strip()
                        if h1_text not in NOISE_LABELS:
                            actual_name = h1_text
                    except Exception:
                        pass

                result["name"] = (
                    actual_name.strip() or result["name"] or "상품명 미확인"
                )[:240]

                # 3. 무신사 JSON 스크립트 데이터 추출
                script_text = page.evaluate(
                    """() => {
                        const el = document.querySelector('script#__NEXT_DATA__') ||
                            Array.from(document.querySelectorAll('script')).find(
                                s => (s.innerText || '').includes('window.__MSS__')
                            );
                        return el ? el.innerText : '';
                    }"""
                )

                price, sizes_data, source = parse_musinsa_json(script_text)

                # JSON 파싱 실패/누락 시 price_fetch의 MetadataParser + JSON-LD로 보완
                if price is None:
                    html = page.content()
                    meta_price, meta_source = _extract_from_html_metadata(html)
                    if meta_price is not None:
                        price = meta_price
                        source = meta_source

                # 화면 텍스트에서도 가격/사이즈/품절 후보 보완
                vis_price, vis_sizes = _extract_visible_hints(page)
                if price is None and vis_price is not None:
                    price = vis_price
                    source = source or "상세화면"
                if not sizes_data and vis_sizes:
                    sizes_data = vis_sizes
                    source = source or "상세화면"

                # 4. 결과 정리
                result["price"] = price
                result["source"] = source or "상세화면"

                if sizes_data:
                    formatted_sizes = []
                    for s in sizes_data:
                        p_str = f" ({s['price']:,}원)" if s.get("price") else ""
                        status = " [품절]" if s.get("is_soldout") else ""
                        formatted_sizes.append(f"{s['size']}{p_str}{status}")

                    result["size"] = ", ".join(formatted_sizes)
                    result["state"] = (
                        "조회성공" if price is not None else "사이즈확인·가격미확인"
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
