import sys
import csv
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import webbrowser
import threading
from price_fetch import get_public_price
from urllib.parse import quote, urlsplit, urlunsplit, parse_qsl, urlencode, unquote

APP_TITLE = "쇼핑몰 ↔ KREAM 가격 비교기"
# 검색 주소는 {q} 자리에 URL 인코딩된 상품명만 넣습니다.
# confirmed: 사용자가 제공했거나 실제 검색 결과 URL이 확인된 경로
# trial: 공식 검색 경로로 추정되지만 사이트 개편 여부를 직접 확인하지 못한 경로
# fallback: 사이트 자체 검색 경로 미확인; 구글의 해당 사이트 검색 사용
DEFAULT_SITES = {
    "무신사": ("https://www.musinsa.com/search/goods?keyword={q}", "기존"),
    "네이버쇼핑": ("https://search.shopping.naver.com/search/all?query={q}", "기존"),
    "29CM": ("https://www.29cm.co.kr/store/search?keyword={q}&sort=RECOMMENDED&page=1", "기존"),
    "W컨셉": ("https://www.wconcept.co.kr/Search?keyword={q}", "기존"),
    "SSF샵": ("https://www.ssfshop.com/search/result?keyword={q}", "기존"),
    "KREAM": ("https://kream.co.kr/search?keyword={q}", "기존"),
    "ABC마트": ("https://abcmart.a-rt.com/display/search-word/result?channel=10001&searchWord={q}&smartSearchCheck=false&page=1&perPage=30&pageColumn=3&sort=point&dfltChnnlMv=&tabGubun=total&searchPageGubun=product&track=W0010", "사용자 제공"),
    "롯데ON": ("https://www.lotteon.com/csearch/search/search?render=search&platform=pc&q={q}&mallId=1&sort=ranking", "사용자 제공"),
    "SSG닷컴": ("https://www.ssg.com/search.ssg?target=all&query={q}", "사용자 제공"),
    "현대홈쇼핑(Hmall)": ("https://www.hmall.com/md/pde/search?gnbSearchYn=Y&requestPath=w&tab=all&searchTerm={q}&csfDepth=&csfNameCode=&onnuriGcStlmYn=&searchType=normal&gaSearchType=%EC%A7%81%EC%A0%91%EC%9E%85%EB%A0%A5", "사용자 제공"),
    "CJ온스타일": ("https://display.cjonstyle.com/p/search/searchAllList?k={q}&searchType=ALL", "사용자 제공"),
    "지그재그": ("https://zigzag.kr/search?keyword={q}", "사용자 제공"),
    "에이블리": ("https://mobile.a-bly.com/search/search-result?query={q}&search_type=DIRECT", "사용자 제공"),
    "LF몰": ("https://www.lfmall.co.kr/app/search/result/{q}", "검색결과 확인"),
    "갤러리아몰": ("https://www.galleria.co.kr/search/totalSearch.action?searchTerm={q}&page_idx=&cateLM=&totalFilter=&disp_type_cd_dq=10&mall_no=0000014&cateYn=&searchType=&searchNumCheck=&selectChkYn=", "사용자 제공"),
    "GS샵": ("https://www.gsshop.com/shop/search/main.gs?lseq=392814&tq={q}&initSrchYn=Y&ab=df&gsid=ECmain-AU392814-AU392814-1", "사용자 제공"),
    "롯데홈쇼핑": ("https://www.lotteimall.com/search/searchMain.lotte?slog=00101_1&headerQuery={q}", "사용자 제공"),
    "더현대닷컴": ("https://hi.thehyundai.com/search?tab=product&q={q}&disPlaySize=36", "사용자 제공"),
}
from pathlib import Path
import json
from urllib.parse import urlparse
CONFIG_FILE = (Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent) / "search_urls.json"
SEARCH_KEYS = ("q", "query", "keyword", "searchword", "searchterm", "k", "tq", "headerquery", "search_text", "searchquery", "kwd", "searchkey", "searchkeyword", "word", "search")

def valid_template(url):
    if not isinstance(url, str) or url.count("{q}") != 1 or len(url) > 4096:
        return False
    try:
        u = urlparse(url)
        return u.scheme in ("https", "http") and bool(u.hostname) and "{" not in url.replace("{q}", "") and "}" not in url.replace("{q}", "")
    except ValueError:
        return False

def template_from_example(raw):
    """Turn an actual search results URL into a reusable safe query template."""
    raw = raw.strip().replace("\\&", "&").replace("&amp;", "&")
    if valid_template(raw):
        return raw
    try:
        u = urlsplit(raw)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError("https://로 시작하는 검색 결과 주소를 입력해주세요.")
        pairs = parse_qsl(u.query, keep_blank_values=True)
        matches = [(i, key) for i,(key,value) in enumerate(pairs) if key.lower() in SEARCH_KEYS]
        if matches:
            # Prefer longer unambiguous search-specific parameter names over 'q'.
            rank = {key: i for i, key in enumerate(SEARCH_KEYS)}
            idx, _ = min(matches, key=lambda a: rank[a[1].lower()])
            pairs[idx] = (pairs[idx][0], "__PRODUCT_QUERY__")
            query = urlencode(pairs, quote_via=quote).replace("__PRODUCT_QUERY__", "{q}")
            result = urlunsplit((u.scheme, u.netloc, u.path, query, ""))
            if valid_template(result): return result
        # Support common /search/result/<query> shapes, e.g. LF Mall.
        if any(x in u.path.lower() for x in ("/search/", "/searchresult/")):
            segments = u.path.split("/")
            if segments[-1] and segments[-1].lower() not in ("search", "result", "searchresult"):
                segments[-1] = "{q}"
                result = urlunsplit((u.scheme, u.netloc, "/".join(segments), u.query, ""))
                if valid_template(result): return result
        raise ValueError("검색어 위치를 자동 인식하지 못했습니다. 주소의 상품명 부분을 {q}로 바꿔주세요.")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"주소 형식을 확인해주세요: {exc}")

def load_sites():
    defaults = {name: {"url": url, "status": status} for name,(url,status) in DEFAULT_SITES.items()}
    if not CONFIG_FILE.exists(): return defaults
    try:
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(config, dict) and config.get("version") == 2:
            saved = config.get("sites")
            if isinstance(saved, dict):
                good = {}
                for name, entry in saved.items():
                    if isinstance(name, str) and name.strip() and isinstance(entry, dict) and valid_template(entry.get("url")):
                        good[name] = {"url": entry["url"], "status": str(entry.get("status") or "사용자 설정")}
                return good
        # 6차 저장 파일의 개별 주소 수정값도 자동으로 이어받습니다.
        if isinstance(config, dict):
            for name, entry in config.items():
                if name in defaults and isinstance(entry, dict) and valid_template(entry.get("url")):
                    defaults[name] = {"url": entry["url"], "status": "사용자 수정"}
    except (OSError, ValueError, TypeError):
        pass
    return defaults

def save_sites(sites):
    data = {"version": 2, "sites": sites}
    temporary = CONFIG_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(CONFIG_FILE)

SITES = load_sites()
SIZES = ["220","225","230","235","240","245","250","255","260","265","270","275","280","285","290","295","300","FREE"]

class PriceCompareApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.sites = SITES
        self.root.geometry("1360x850")
        self.root.minsize(960, 650)
        self._style()
        self._build()

    def _style(self):
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Malgun Gothic", 18, "bold"))
        style.configure("Sub.TLabel", font=("Malgun Gothic", 9), foreground="#555")
        style.configure("Treeview", rowheight=27, font=("Malgun Gothic", 9))
        style.configure("Treeview.Heading", font=("Malgun Gothic", 9, "bold"))

    def _build(self):
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="쇼핑몰 ↔ KREAM 가격 비교기", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="기존 쇼핑몰 검색 기능 유지 · 무신사 첫 상품 자동 탐색 시범 · 미확인 가격은 공란",
                  style="Sub.TLabel").pack(anchor="w", pady=(2, 10))
        info = ttk.Frame(outer)
        info.pack(fill="x", pady=(0, 8))
        ttk.Label(info, text="상품명 또는 모델번호").pack(side="left", padx=(0, 6))
        self.keyword = tk.StringVar()
        ttk.Entry(info, textvariable=self.keyword, width=38).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(info, text="선택한 사이트 검색", command=self.open_selected_searches).pack(side="left", padx=3)
        ttk.Button(info, text="전체 사이트 검색", command=self.open_all_searches).pack(side="left", padx=3)
        ttk.Button(info, text="KREAM 시세표", command=self.open_kream_quotes).pack(side="left", padx=3)
        self.sites_frame = ttk.LabelFrame(outer, text="검색할 쇼핑몰 선택 (사이트 열기 기능)", padding=7)
        self.sites_frame.pack(fill="x", pady=(0, 8))
        self.site_vars = {}
        self.render_sites()
        ttk.Label(outer, text="'쇼핑몰 관리'에서 검색 사이트 추가·수정·삭제 가능. 아래 자동 가격조회는 무신사 1곳만 시범 지원합니다.",
                  style="Sub.TLabel").pack(anchor="w", pady=(0, 8))
        from musinsa_dashboard import MusinsaDashboard
        self.dashboard = MusinsaDashboard(outer, self.keyword, self.sites, self.root)
        self.root.bind("<Control-s>", lambda e: self.dashboard.export_csv())

    def _insert(self, size, shop, k_buy, k_sell, k_last, note):
        vals = [size, shop, k_buy, k_sell, k_last, "", note]
        item = self.tree.insert("", "end", values=vals)
        self._recalculate_item(item)
        return item

    def add_row(self):
        self._insert("선택", "", "", "", "", "")

    def delete_selected(self):
        for item in self.tree.selection():
            self.tree.delete(item)
        self._update_summary()

    def _num(self, value):
        try:
            return int(str(value).replace(",", "").replace("원", "").strip())
        except (ValueError, TypeError):
            return None

    def _recalculate_item(self, item):
        vals = list(self.tree.item(item, "values"))
        shop = self._num(vals[1])
        k_buy = self._num(vals[2])
        if shop is not None and k_buy is not None:
            vals[5] = f"{k_buy-shop:+,}"
        else:
            vals[5] = ""
        self.tree.item(item, values=vals)
        self._update_summary()

    def _update_summary(self):
        profits = []
        for item in self.tree.get_children():
            vals = self.tree.item(item, "values")
            a, b = self._num(vals[1]), self._num(vals[2])
            if a is not None and b is not None:
                profits.append(b-a)
        if profits:
            self.summary.set(f"입력된 {len(profits)}개 사이즈의 단순 가격 차액 합계: {sum(profits):,}원  |  평균: {sum(profits)//len(profits):,}원")
        else:
            self.summary.set("사이즈별 가격을 입력하면 차액이 계산됩니다.")

    def edit_cell(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        row = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not row:
            return
        index = int(col[1:]) - 1
        x, y, w, h = self.tree.bbox(row, col)
        old = self.tree.set(row, self.tree["columns"][index])
        editor = ttk.Entry(self.tree)
        editor.place(x=x, y=y, width=w, height=h)
        editor.insert(0, old)
        editor.focus()
        def save(_=None):
            new = editor.get().strip()
            editor.destroy()
            vals = list(self.tree.item(row, "values"))
            vals[index] = new
            self.tree.item(row, values=vals)
            self._recalculate_item(row)
        editor.bind("<Return>", save)
        editor.bind("<FocusOut>", save)
        editor.bind("<Escape>", lambda e: editor.destroy())

    def set_all_sites(self, selected):
        for var in self.site_vars.values():
            var.set(selected)

    def render_sites(self):
        selected = {name: var.get() for name,var in self.site_vars.items()}
        for child in self.sites_frame.winfo_children(): child.destroy()
        self.site_vars = {}
        for i, (name,entry) in enumerate(self.sites.items()):
            var = tk.BooleanVar(value=selected.get(name, i < 6))
            self.site_vars[name] = var
            ttk.Checkbutton(self.sites_frame, text=name, variable=var).grid(row=i//6, column=i%6, padx=9, pady=3, sticky="w")
        buttons = ttk.Frame(self.sites_frame)
        buttons.grid(row=(len(self.sites)+5)//6, column=0, columnspan=6, sticky="w", pady=(8,0))
        ttk.Button(buttons, text="모두 선택", command=lambda: self.set_all_sites(True)).pack(side="left", padx=(0,6))
        ttk.Button(buttons, text="선택 해제", command=lambda: self.set_all_sites(False)).pack(side="left")
        ttk.Button(buttons, text="쇼핑몰 관리 (추가 / 수정 / 삭제)", command=self.edit_search_urls).pack(side="left", padx=(12, 0))
        self.root.title(f"{APP_TITLE} · {len(self.sites)}개")

    def edit_search_urls(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("쇼핑몰 관리 · 추가 / 수정 / 삭제")
        dialog.geometry("990x410")
        dialog.minsize(800, 390)
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="등록된 쇼핑몰 (새 사이트는 이름을 새로 입력)").grid(row=0, column=0, sticky="w")
        name_var = tk.StringVar(value=next(iter(self.sites), ""))
        chooser = ttk.Combobox(frame, values=list(self.sites), textvariable=name_var, width=26)
        chooser.grid(row=1, column=0, sticky="ew", padx=(0, 12), pady=6)
        ttk.Label(frame, text="실제 검색 결과 URL 또는 {q}가 포함된 주소").grid(row=0, column=1, sticky="w")
        url_var = tk.StringVar()
        ttk.Entry(frame, textvariable=url_var, width=85).grid(row=1, column=1, sticky="ew", pady=6)
        tip = tk.StringVar(value="")
        ttk.Label(frame, textvariable=tip, wraplength=900).grid(row=2, column=0, columnspan=2, sticky="w", pady=(5, 10))
        ttk.Label(frame, text="① 사이트 이름 입력  ② 쇼핑몰에서 상품을 검색한 결과 URL 붙여넣기  ③ 검색 테스트  ④ 추가/저장", style="Sub.TLabel").grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text="예: https://example.com/search?q=나이키 → https://example.com/search?q={q} 자동 변환", style="Sub.TLabel").grid(row=4, column=0, columnspan=2, sticky="w", pady=(5, 12))
        frame.columnconfigure(1, weight=1)
        current_name = [name_var.get()]
        def refresh(_=None):
            name = name_var.get().strip()
            entry = self.sites.get(name)
            url_var.set(entry["url"] if entry else "")
            current_name[0] = name if entry else None
            tip.set("기존 쇼핑몰 주소를 바꾸거나 이름을 변경할 수 있습니다." if entry else "새 쇼핑몰 이름과 검색 결과 주소를 입력한 다음 추가/저장하세요.")
        chooser.bind("<<ComboboxSelected>>", refresh)
        refresh()
        def candidate():
            name = name_var.get().strip()
            if not name: raise ValueError("쇼핑몰 이름을 입력해주세요.")
            if len(name) > 60: raise ValueError("쇼핑몰 이름은 60자 이내로 입력해주세요.")
            url = template_from_example(url_var.get())
            if not valid_template(url): raise ValueError("주소의 검색어 부분을 확인해주세요.")
            return name, url
        def save():
            try: name,url = candidate()
            except ValueError as exc:
                messagebox.showwarning("입력 확인", str(exc), parent=dialog); return
            old = current_name[0]
            if old and old != name and name in self.sites:
                messagebox.showwarning("이름 중복", "이미 등록된 쇼핑몰 이름입니다.", parent=dialog); return
            next_sites = dict(self.sites)
            if old and old != name:
                next_sites = { (name if k == old else k): v for k,v in next_sites.items() }
            next_sites[name] = {"url": url, "status": "사용자 설정"}
            try: save_sites(next_sites)
            except OSError as exc:
                messagebox.showerror("저장 실패", str(exc), parent=dialog); return
            self.sites.clear(); self.sites.update(next_sites)
            if old and old != name and old in self.site_vars:
                previous_selected = self.site_vars[old].get()
            else: previous_selected = False
            self.render_sites()
            if old != name and previous_selected: self.site_vars[name].set(True)
            current_name[0] = name
            url_var.set(url)
            chooser.configure(values=list(self.sites))
            tip.set(f"{name}: 저장 완료! 다음 실행에도 유지됩니다.")
        def delete():
            name = name_var.get().strip()
            if name not in self.sites:
                messagebox.showinfo("선택 확인", "삭제할 기존 쇼핑몰을 선택해주세요.", parent=dialog); return
            if not messagebox.askyesno("사이트 삭제", f"'{name}' 사이트를 목록에서 삭제할까요?", parent=dialog): return
            next_sites = {k:v for k,v in self.sites.items() if k != name}
            try: save_sites(next_sites)
            except OSError as exc:
                messagebox.showerror("저장 실패", str(exc), parent=dialog); return
            self.sites.clear(); self.sites.update(next_sites)
            self.render_sites()
            chooser.configure(values=list(self.sites))
            name_var.set(next(iter(self.sites), ""))
            refresh()
            tip.set(f"{name}: 삭제 완료. 다음 실행에도 유지됩니다.")
        def test():
            try: _,url = candidate()
            except ValueError as exc:
                messagebox.showwarning("주소 확인", str(exc), parent=dialog); return
            q = self.keyword.get().strip() or "나이키 에어포스 1"
            webbrowser.open_new_tab(url.replace("{q}", quote(q, safe="")))
            tip.set(f"검색 테스트: {q} (실제 사이트 접속 결과는 브라우저에서 확인해주세요.)")
        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky="w", pady=12)
        ttk.Button(buttons, text="검색 테스트", command=test).pack(side="left", padx=(0,8))
        ttk.Button(buttons, text="사이트 추가 / 수정 저장", command=save).pack(side="left", padx=(0,8))
        ttk.Button(buttons, text="선택한 사이트 삭제", command=delete).pack(side="left", padx=(0,8))
        ttk.Button(buttons, text="닫기", command=dialog.destroy).pack(side="left")

    def open_selected_searches(self):
        q = self.keyword.get().strip()
        if not q:
            messagebox.showwarning("검색어 필요", "상품명 또는 모델번호를 입력해 주세요.")
            return
        opened = 0
        for name, var in self.site_vars.items():
            if var.get():
                url = self.sites[name]["url"].replace("{q}", quote(q, safe=""))
                webbrowser.open_new_tab(url)
                opened += 1
        if opened == 0:
            messagebox.showinfo("사이트 선택", "검색할 쇼핑몰을 하나 이상 선택해 주세요.")

    def open_all_searches(self):
        if len(self.sites) > 15:
            if not messagebox.askyesno("탭 대량 열기", f"{len(self.sites)}개 쇼핑몰 검색 탭을 모두 여시겠습니까? 브라우저에 부담이 될 수 있습니다."):
                return
        self.set_all_sites(True)
        self.open_selected_searches()

    def open_kream_quotes(self):
        from kream_quotes import KreamQuotesWindow
        KreamQuotesWindow(self.root, self.keyword.get().strip())

    def open_price_dashboard(self):
        from comparison_dashboard import CompareWindow
        CompareWindow(self.root, self.sites, self.keyword.get().strip())

    def fetch_price(self):
        url = self.product_url.get().strip()
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("사이즈 선택", "가격을 반영할 사이즈 행을 하나 선택하세요.")
            return
        if not url:
            messagebox.showinfo("상품 주소", "상품 상세 페이지의 URL을 입력하세요.")
            return
        item = selected[0]
        self.fetch_button.configure(state="disabled")
        self.fetch_status.set("공개 상품 페이지의 가격 메타데이터를 확인하는 중입니다...")
        def work():
            try:
                value, source = get_public_price(url)
                self.root.after(0, lambda: self._finish_fetch(item, url, value, source, None))
            except Exception as exc:
                error_text = str(exc)
                self.root.after(0, lambda: self._finish_fetch(item, url, None, "", error_text))
        threading.Thread(target=work, daemon=True).start()

    def _finish_fetch(self, item, url, value, source, error):
        self.fetch_button.configure(state="normal")
        if error:
            self.fetch_status.set("자동 확인 실패: " + error)
            return
        if not self.tree.exists(item):
            self.fetch_status.set("원래 선택한 행이 삭제되어 값을 반영하지 않았습니다.")
            return
        vals = list(self.tree.item(item, "values"))
        vals[1] = str(value)
        vals[6] = ((vals[6] + " | ") if vals[6] else "") + f"자동 확인({source}), 재확인 필요: {url}"
        self.tree.item(item, values=vals)
        self._recalculate_item(item)
        self.fetch_status.set(f"{value:,}원 반영 완료 ({source}). 옵션/배송비/할인에 따라 달라질 수 있으니 상품 페이지에서 확인하세요.")

    def export_csv(self):
        path = filedialog.asksaveasfilename(
            title="가격 비교표 저장", defaultextension=".csv",
            filetypes=[("CSV 파일", "*.csv")], initialfile="가격비교표.csv")
        if not path:
            return
        headers = ["사이즈", "쇼핑몰 구매가", "KREAM 즉시구매가", "KREAM 즉시판매가",
                   "KREAM 최근거래가", "구매가 대비 차액", "메모"]
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                for item in self.tree.get_children():
                    writer.writerow(self.tree.item(item, "values"))
            messagebox.showinfo("저장 완료", f"CSV 파일을 저장했습니다.\n{path}")
        except OSError as exc:
            messagebox.showerror("저장 오류", str(exc))

if __name__ == "__main__":
    root = tk.Tk()
    app = PriceCompareApp(root)
    root.mainloop()
