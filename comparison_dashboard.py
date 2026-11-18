"""Multiple public product-URL prices + manually entered KREAM bid comparison.

No authentication automation, headless browser, or private endpoint access.
"""
import csv
import json
import threading
import queue
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from tkinter import ttk, filedialog, messagebox
from urllib.parse import urlparse
import webbrowser

from price_fetch import get_public_price
from auto_discovery import discover_one
from kream_quotes import KreamQuotesWindow, get_quote, key_for

DATA_PATH = Path(__file__).resolve().parent / 'comparison_rows.json'
COLS = ('shop','size','price','shipping','kream','last','checked','fee','extra','profit','state')

def num(value):
    try:
        value = str(value).replace(',', '').replace('원', '').strip()
        if not value: return 0
        number = int(value)
        if number < 0: raise ValueError
        return number
    except (TypeError, ValueError):
        raise ValueError('금액은 0 이상의 정수로 입력해 주세요.')

def pct(value):
    try:
        number = float(str(value).strip())
        if not 0 <= number <= 100: raise ValueError
        return number
    except (TypeError, ValueError):
        raise ValueError('수수료율은 0~100 사이의 숫자로 입력해 주세요.')

def check_url(url):
    u = urlparse(url.strip())
    if u.scheme not in ('https', 'http') or not u.hostname or u.username or u.password:
        raise ValueError('http(s) 상품 상세페이지 주소를 입력해 주세요.')
    return url.strip()

class CompareWindow:
    def __init__(self, parent, sites, query=''):
        self.win = tk.Toplevel(parent)
        self.win.title('사이즈별 가격비교')
        self.win.geometry('1330x800')
        self.win.minsize(1050, 620)
        self.site_templates = dict(sites) if isinstance(sites,dict) else {}
        self.sites = list(sites)
        self.rows = []
        self.busy = False
        self._discovery_queue = None
        self._ui(query)
        self._load()
        if query.strip():
            self.query.set(query.strip())
        self.win.protocol('WM_DELETE_WINDOW', self._close)

    def _ui(self, query):
        main = ttk.Frame(self.win, padding=12)
        main.pack(fill='both', expand=True)
        ttk.Label(main, text='같은 모델번호·사이즈 연결 → KREAM 즉시판매가/최근 체결가 → 예상 차익',
                  font=('Malgun Gothic', 14, 'bold')).pack(anchor='w', pady=(0,5))
        ttk.Label(main, text='현재 자동 탐색은 무신사 한 곳만 시험합니다. 다른 쇼핑몰 검색 열기 기능은 유지됩니다. 상세 URL·가격을 확인하지 못하면 미확인으로 표시합니다. KREAM 시세는 직접 입력합니다.', wraplength=1240).pack(anchor='w', pady=(0,8))

        line = ttk.Frame(main)
        line.pack(fill='x', pady=4)
        ttk.Label(line, text='상품명 / 모델번호').pack(side='left')
        self.query = tk.StringVar(value=query)
        self.query.trace_add('write', lambda *args: self._render() if hasattr(self, 'tree') else None)
        ttk.Entry(line, textvariable=self.query, width=34).pack(side='left', padx=8)
        ttk.Label(line, text='기본 수수료 %').pack(side='left')
        self.fee = tk.StringVar(value='5')
        ttk.Entry(line, textvariable=self.fee, width=7).pack(side='left', padx=6)
        ttk.Label(line, text='기본 추가비용(원)').pack(side='left')
        self.extra = tk.StringVar(value='0')
        ttk.Entry(line, textvariable=self.extra, width=10).pack(side='left', padx=6)
        ttk.Button(line, text='KREAM 검색 열기', command=lambda: self._open_shop('KREAM')).pack(side='right', padx=3)
        ttk.Button(line, text='KREAM 사이즈별 시세 관리', command=self._quotes).pack(side='right', padx=3)

        entry = ttk.LabelFrame(main, text='가격 비교할 상품 추가', padding=8)
        entry.pack(fill='x', pady=7)
        self.shop = tk.StringVar(value=self.sites[0] if self.sites else '')
        self.size = tk.StringVar(value='')
        self.url = tk.StringVar()
        self.price = tk.StringVar()
        self.shipping = tk.StringVar(value='0')
        self.kream = tk.StringVar()
        ttk.Label(entry, text='쇼핑몰').grid(row=0,column=0,sticky='w')
        ttk.Label(entry, text='사이즈 (공란=전체)').grid(row=0,column=1,sticky='w')
        ttk.Label(entry, text='상품 상세 URL').grid(row=0,column=2,sticky='w')
        ttk.Label(entry, text='가격 직접 입력').grid(row=2,column=0,sticky='w')
        ttk.Label(entry, text='배송비').grid(row=2,column=1,sticky='w')
        ttk.Label(entry, text='KREAM 즉시판매가').grid(row=2,column=2,sticky='w')
        ttk.Combobox(entry, textvariable=self.shop, values=self.sites, width=20).grid(row=1,column=0,sticky='ew',padx=(0,8),pady=3)
        ttk.Entry(entry, textvariable=self.size, width=12).grid(row=1,column=1,sticky='ew',padx=(0,8))
        ttk.Entry(entry, textvariable=self.url).grid(row=1,column=2,columnspan=2,sticky='ew',padx=(0,8))
        ttk.Entry(entry, textvariable=self.price, width=19).grid(row=3,column=0,sticky='ew',padx=(0,8))
        ttk.Entry(entry, textvariable=self.shipping, width=12).grid(row=3,column=1,sticky='ew',padx=(0,8))
        ttk.Entry(entry, textvariable=self.kream, width=20).grid(row=3,column=2,sticky='ew',padx=(0,8))
        ttk.Button(entry, text='비교표에 추가', command=self._add).grid(row=3,column=3,sticky='ew')
        entry.columnconfigure(2,weight=1)
        entry.columnconfigure(3,weight=0)

        buttons = ttk.Frame(main)
        buttons.pack(fill='x', pady=6)
        self.discover_button = ttk.Button(buttons, text='무신사 자동 탐색 (시범)', command=self._discover_all)
        self.discover_button.pack(side='left', padx=(0,5))
        self.fetch_button = ttk.Button(buttons, text='무신사 가격 새로고침', command=self._refresh_all)
        self.fetch_button.pack(side='left', padx=(0,5))
        self.progress = ttk.Progressbar(buttons, orient='horizontal', mode='determinate', length=130)
        self.progress.pack(side='left', padx=6)
        ttk.Button(buttons, text='선택 행 수정', command=self._edit).pack(side='left', padx=3)
        ttk.Button(buttons, text='선택 행 삭제', command=self._delete).pack(side='left', padx=3)
        ttk.Button(buttons, text='선택 행 상품 열기', command=self._open_selected).pack(side='left', padx=3)
        ttk.Button(buttons, text='가격순 정렬', command=self._sort).pack(side='left', padx=3)
        ttk.Button(buttons, text='사이즈순 정렬', command=self._sort_size).pack(side='left', padx=3)
        ttk.Button(buttons, text='CSV 저장', command=self._csv).pack(side='right', padx=3)
        ttk.Button(buttons, text='설정/비교표 저장', command=self._save_clicked).pack(side='right', padx=3)

        frame = ttk.Frame(main)
        frame.pack(fill='both', expand=True)
        headings = {'shop':'쇼핑몰', 'size':'사이즈','price':'판매가','shipping':'배송비',
                    'kream':'KREAM 즉시판매가','last':'최근 체결가','checked':'시세 확인 시각','fee':'수수료(%)','extra':'기타비용',
                    'profit':'예상 차익','state':'조회 상태'}
        widths = {'shop':140,'size':65,'price':105,'shipping':80,'kream':135,
                  'last':120,'checked':170,'fee':85,'extra':90,'profit':115,'state':230}
        self.tree = ttk.Treeview(frame,columns=COLS,show='headings',selectmode='browse',height=15)
        for key in COLS:
            self.tree.heading(key,text=headings[key])
            self.tree.column(key,width=widths[key],anchor='center',stretch=(key=='state'))
        self.tree.pack(side='left',fill='both',expand=True)
        scroll = ttk.Scrollbar(frame,orient='vertical',command=self.tree.yview)
        scroll.pack(side='right',fill='y'); self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<Double-1>',lambda e: self._edit())

        self.status = tk.StringVar(value='같은 모델번호와 사이즈의 저장된 KREAM 가격이 자동 반영됩니다. KREAM 시세는 사용자가 직접 확인한 값입니다.')
        ttk.Label(main,textvariable=self.status,wraplength=1250).pack(anchor='w',pady=(8,4))
        self.summary = tk.StringVar(value='비교 가능한 가격 없음')
        ttk.Label(main,textvariable=self.summary,font=('Malgun Gothic',11,'bold')).pack(anchor='w',pady=(2,5))
        self._render()
        ttk.Label(main,text='예상 차익 = 즉시판매가 − 쇼핑몰 가격 − 배송비 − 예상 수수료 − 기타비용. 최근 체결가는 참고 지표이며 수익 계산 기준이 아닙니다. 8차 행에 직접 입력된 KREAM 가격이 있으면 사이즈 시세표보다 우선합니다. 실제 정산액·세금·시세 변동을 확인하세요.',wraplength=1270).pack(anchor='w')

    def _quotes(self):
        if not self.query.get().strip():
            messagebox.showinfo('모델번호', '비교할 모델번호 또는 상품 식별자를 먼저 입력하세요.', parent=self.win)
            return
        KreamQuotesWindow(self.win, self.query.get().strip(), on_saved=self._render)

    def _quote_for(self, row):
        return get_quote(self.query.get().strip(), row.get('size',''))

    def _effective_kream(self,row):
        # Row-level manual values take precedence for compatibility with 8th-version saved rows.
        if row.get('size','') in ('사이즈 미확인','', '미확인'): return None
        if row.get('kream') is not None: return row['kream']
        return self._quote_for(row).get('sell')

    def _open_shop(self, name):
        if not self.query.get().strip():
            messagebox.showinfo('검색어', '먼저 상품명이나 모델번호를 입력해 주세요.',parent=self.win)
            return
        from urllib.parse import quote
        if name == 'KREAM': webbrowser.open_new_tab('https://kream.co.kr/search?keyword='+quote(self.query.get().strip()))

    def _add(self):
        try:
            url = self.url.get().strip()
            if url: check_url(url)
            if not url and not self.price.get().strip():
                raise ValueError('상품 상세 URL 또는 직접 입력 가격이 필요합니다.')
            row = {'shop':self.shop.get().strip() or '기타', 'size':self.size.get().strip() or '사이즈 미확인',
                   'url':url, 'price':num(self.price.get()) if self.price.get().strip() else None,
                   'shipping':num(self.shipping.get()),
                   'kream':num(self.kream.get()) if self.kream.get().strip() else None,
                   'fee':pct(self.fee.get()),'extra':num(self.extra.get()),
                   'state':'직접 입력' if self.price.get().strip() else '가격미확인', 'source':'', 'checked':''}
        except ValueError as e:
            messagebox.showwarning('입력 확인',str(e),parent=self.win);return
        self.rows.append(row)
        self._render()
        self.url.set('');self.price.set('')
        self.status.set('비교표에 추가했습니다. 직접 입력 가격과 자동 확인 가격은 구별하여 표시됩니다.')

    def _selected_index(self):
        sel = self.tree.selection()
        if not sel:return None
        return int(sel[0])

    def _edit(self):
        i = self._selected_index()
        if i is None:return
        row = self.rows[i]
        dialog = tk.Toplevel(self.win);dialog.title('비교 항목 수정');dialog.geometry('780x400');dialog.transient(self.win);dialog.grab_set()
        area = ttk.Frame(dialog,padding=14);area.pack(fill='both',expand=True)
        fields = [('쇼핑몰','shop'),('사이즈','size'),('상품 URL','url'),('판매가','price'),('배송비','shipping'),
                  ('KREAM 즉시판매가','kream'),('수수료율 %','fee'),('기타비용','extra')]
        vars = {}
        for idx,(name,key) in enumerate(fields):
            ttk.Label(area,text=name).grid(row=idx,column=0,sticky='w',pady=6)
            v=tk.StringVar(value='' if row.get(key) is None else str(row[key]));vars[key]=v
            ttk.Entry(area,textvariable=v,width=75).grid(row=idx,column=1,sticky='ew',padx=8)
        area.columnconfigure(1,weight=1)
        def save():
            try:
                d={k:v.get().strip() for k,v in vars.items()}
                if d['url']:check_url(d['url'])
                for k in ('price','kream'):
                    d[k]=num(d[k]) if d[k] else None
                for k in ('shipping','extra'):d[k]=num(d[k])
                d['fee']=pct(d['fee'])
                if d['price'] is None and not d['url']:raise ValueError('상품 URL이나 가격을 입력해 주세요.')
                d['state']='직접 수정' if d['price'] is not None else '미확인'
                d['source']=''
            except ValueError as ex:
                messagebox.showwarning('입력 확인',str(ex),parent=dialog);return
            self.rows[i]=d;dialog.destroy();self._render()
        ttk.Button(area,text='수정 저장',command=save).grid(row=len(fields),column=1,sticky='e',pady=10)

    def _delete(self):
        i=self._selected_index()
        if i is not None:
            self.rows.pop(i);self._render()

    def _open_selected(self):
        i=self._selected_index()
        if i is not None:
            link = self.rows[i].get('url') or self.rows[i].get('search_url')
            if link: webbrowser.open_new_tab(link)
            else: self.status.set('등록된 검색 링크가 없습니다.')

    def _profit(self,row):
        kream=self._effective_kream(row)
        if row.get('price') is None or kream is None:return None
        return round(kream-row['price']-row['shipping']-kream*row['fee']/100-row['extra'])

    def _render(self):
        for i in self.tree.get_children():self.tree.delete(i)
        profits=[];prices=[]
        for i,row in enumerate(self.rows):
            pr=self._profit(row)
            if pr is not None:profits.append(pr)
            if row['price'] is not None:prices.append((row['price']+row['shipping'],row['shop']))
            fmt=lambda v:'' if v is None else f'{v:,}'
            quote=self._quote_for(row)
            kr=self._effective_kream(row)
            self.tree.insert('', 'end', iid=str(i), values=(row['shop'],row['size'],fmt(row['price']),
                             fmt(row['shipping']),fmt(kr),fmt(quote.get('last')),quote.get('updated',''),
                             str(row['fee']),fmt(row['extra']),fmt(pr),row['state']))
        lowest=f'배송비 포함 최저가: {min(prices)[0]:,}원 ({min(prices)[1]})' if prices else '판매가 없음'
        best=f'예상 차익 최대: {max(profits):+,}원' if profits else 'KREAM 가격 입력 시 예상 차익 계산'
        self.summary.set(f'{len(self.rows)}개 상품 | {lowest} | {best}')

    def _sort(self):
        self.rows.sort(key=lambda r: (r['price'] is None, (r['price'] or 0)+r['shipping']))
        self._render();self.status.set('확인된 상품 가격 + 배송비 기준으로 정렬했습니다. 미확인 가격은 아래에 표시합니다.')

    def _sort_size(self):
        def key(r):
            size = str(r.get('size', '')).strip()
            return (0, int(size), r.get('shop','')) if size.isdigit() else (1, 0, size)
        self.rows.sort(key=key)
        self._render();self.status.set('사이즈순으로 정렬했습니다. 미확인 사이즈는 뒤에 표시합니다.')

    def _refresh_all(self):
        # Pilot refresh re-discovers Musinsa; no pasted URL necessary.
        # This also re-reads public prices for any product pages found during discovery.
        self._discover_all()

    def _discover_all(self):
        if self.busy:
            self.status.set('이미 탐색을 진행 중입니다.')
            return
        query=self.query.get().strip()
        if len(query)<3:
            messagebox.showinfo('검색어 확인','상품 모델번호 또는 상품명을 입력해 주세요.',parent=self.win);return
        # Each saved site has {url: '...', status: '...'}, not a URL string.
        # Passing the whole dict caused URL parsing failures for every site.
        targets=[]
        for name,entry in self.site_templates.items():
            if name.upper() == 'KREAM':
                continue
            url = entry.get('url','') if isinstance(entry,dict) else entry
            targets.append((name,url if isinstance(url,str) else ''))
        if not targets:
            messagebox.showinfo('사이트 목록','등록된 쇼핑몰을 확인해 주세요.',parent=self.win);return
        targets=[(name,url) for name,url in targets if name == '무신사']
        if not targets:
            messagebox.showinfo('무신사','등록된 쇼핑몰 목록에 무신사가 없습니다.',parent=self.win);return
        self.busy=True
        self.discover_button.configure(state='disabled')
        self.fetch_button.configure(state='disabled')
        self.progress.configure(maximum=len(targets),value=0)
        self.status.set(f'탐색 진행: 0/{len(targets)}개 · 사이즈 {self.size.get().strip() or "전체"} · 상품 URL 확인 중')
        messages=queue.Queue()
        self._discovery_queue=messages
        def work():
            results=[]
            with ThreadPoolExecutor(max_workers=4) as pool:
                fs={pool.submit(discover_one,name,template,query):name for name,template in targets}
                for future in as_completed(fs):
                    try: result=future.result()
                    except Exception: result={'shop':fs[future],'url':'','price':None,'size':'사이즈 미확인','state':'검색불가'}
                    results.append(result)
                    messages.put(('progress',result))
            messages.put(('done',results))
        threading.Thread(target=work,daemon=True).start()
        self._poll_discovery(query,messages,len(targets),0)

    def _poll_discovery(self,query,messages,total,completed):
        # All tkinter work stays on the UI thread (worker only writes to Queue).
        try:
            while True:
                kind,payload=messages.get_nowait()
                if kind=='done':
                    self.progress.configure(value=total)
                    self._discovery_done(query,payload)
                    return
                completed+=1
                self.progress.configure(value=completed)
                self.status.set(f'탐색 진행: {completed}/{total}개 · 마지막: {payload.get("shop", "")}: {payload.get("state", "검색불가")}')
        except queue.Empty:
            pass
        try:
            if self.win.winfo_exists():
                self.win.after(150,lambda:self._poll_discovery(query,messages,total,completed))
        except tk.TclError:
            pass

    def _discovery_done(self,query,results):
        # A different query is never merged with an existing product table.
        if self.query.get().strip() != query:
            self.status.set('검색어가 변경되어 이전 자동 탐색 결과를 반영하지 않았습니다.')
        else:
            # Keep manually entered rows. Replace only prior auto-discovery rows for same search.
            self.rows=[r for r in self.rows if not r.get('auto_discovered')]
            for d in results:
                if self.size.get().strip() and d.get('state') not in ('검색불가','상품미확인','로그인 필요'):
                    d['state'] = '사이즈 미확인'  # not guaranteed available in requested size
                self.rows.append({'shop':d['shop'], 'size':'사이즈 미확인',
                   'search_url':d.get('search_url',''), 'url':d.get('url',''), 'price':d.get('price'), 'shipping':0,
                   'kream':None,'fee':pct(self.fee.get()) if self.fee.get().strip() else 5.0,
                   'extra':num(self.extra.get()),'state':d['state'], 'source':d.get('source',''),
                   'checked':d.get('checked',''),'auto_discovered':True})
            self._render()
            successes=sum(bool(r.get('url')) for r in results)
            prices=sum(r.get('price') is not None for r in results)
            self.status.set(f'탐색 완료: {len(results)}개 사이트 · 상세 URL {successes}개 · 가격 {prices}개. 상세 URL이 없으면 행을 선택해 상품 열기로 사이트 검색을 확인하세요. 사이즈/품절은 미확인 상태입니다.')
        self.busy=False
        self._discovery_queue=None
        self.discover_button.configure(state='normal')
        self.fetch_button.configure(state='normal')

    def _fetch_all(self):
        if self.busy:return
        targets=[(i,r['url']) for i,r in enumerate(self.rows) if r.get('url') and r.get('shop') != 'KREAM']
        if not targets:
            messagebox.showinfo('가격 확인','KREAM 이외의 쇼핑몰 상품 상세 URL을 먼저 추가해 주세요.',parent=self.win);return
        if len(targets)>40:
            messagebox.showinfo('수집 제한','한 번에 40개 URL까지만 확인할 수 있습니다.',parent=self.win);return
        self.busy=True;self.fetch_button.configure(state='disabled');self.discover_button.configure(state='disabled')
        self.status.set(f'공개 상품 페이지 {len(targets)}개를 확인 중입니다. 실패 사이트는 직접 입력해 주세요.')
        def worker():
            results=[]
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures={pool.submit(get_public_price,url):i for i,url in targets}
                for f in as_completed(futures):
                    i=futures[f]
                    try:
                        value,source=f.result()
                        results.append((i,value,source,None))
                    except Exception as e:
                        results.append((i,None,'',str(e)))
            try:self.win.after(0,lambda:self._done(results))
            except (tk.TclError,RuntimeError):pass
        threading.Thread(target=worker,daemon=True).start()

    def _done(self,results):
        ok=0
        for i,value,source,error in results:
            if i>=len(self.rows):continue
            row=self.rows[i]
            if error:
                row['state']='갱신실패'
                row['last_error']=error[:160]
            else:
                row['price']=value;row['source']=source;row['state']='가격 확인·사이즈 미확인' if row.get('size')=='사이즈 미확인' else '가격 확인';row['checked']=datetime.now().astimezone().strftime('%Y-%m-%d %H:%M');ok+=1
        self.busy=False;self.fetch_button.configure(state='normal');self.discover_button.configure(state='normal')
        self._render();self.status.set(f'가격 자동 확인: {len(results)}개 중 {ok}개 성공. 가격/옵션/쿠폰/배송비가 다를 수 있으므로 상품 상세페이지에서 확인하세요.')

    def _csv(self):
        path=filedialog.asksaveasfilename(parent=self.win,title='가격 비교표 CSV로 저장',defaultextension='.csv',filetypes=[('CSV','*.csv')])
        if not path:return
        try:
            with open(path,'w',newline='',encoding='utf-8-sig') as f:
                writer=csv.writer(f)
                writer.writerow(['모델번호','쇼핑몰','사이즈','상품 URL','판매가','배송비','KREAM 즉시판매가','KREAM 최근 체결가','KREAM 직접확인 시각','수수료율 %','기타비용','예상 차익','가격 확인 상태','출처','가격 확인 시각'])
                for r in self.rows:
                    writer.writerow([self.query.get(),r['shop'],r['size'],r.get('url','') or r.get('search_url',''),r.get('price'),r['shipping'],self._effective_kream(r),self._quote_for(r).get('last'),self._quote_for(r).get('updated'),r['fee'],r['extra'],self._profit(r),r.get('state',''),r.get('source',''),r.get('checked','')])
            messagebox.showinfo('완료','CSV로 저장했습니다.',parent=self.win)
        except OSError as ex:messagebox.showerror('저장 실패',str(ex),parent=self.win)

    def _save(self):
        data={'version':1,'query':self.query.get(),'fee':self.fee.get(),'extra':self.extra.get(),'rows':self.rows}
        temporary=DATA_PATH.with_suffix('.tmp')
        temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(DATA_PATH)

    def _save_clicked(self):
        try:self._save();self.status.set('비교표를 프로그램 폴더에 저장했습니다. 다음 실행에도 유지됩니다.')
        except OSError as ex:messagebox.showerror('저장 실패',str(ex),parent=self.win)

    def _load(self):
        if not DATA_PATH.exists():return
        try:
            saved=json.loads(DATA_PATH.read_text(encoding='utf-8'))
            if saved.get('version')!=1:return
            self.query.set(str(saved.get('query','')))
            self.fee.set(str(saved.get('fee','5')))
            self.extra.set(str(saved.get('extra','0')))
            rows=saved.get('rows',[])
            if not isinstance(rows,list):return
            cleaned=[]
            for r in rows[:200]:
                if not isinstance(r,dict):continue
                try:
                    d={'shop':str(r.get('shop','기타')),'size':str(r.get('size','FREE')),
                       'url':str(r.get('url','')), 'search_url':str(r.get('search_url','')),
                       'price':num(r['price']) if r.get('price') is not None else None,
                       'shipping':num(r.get('shipping',0)),
                       'kream':num(r['kream']) if r.get('kream') is not None else None,
                       'fee':pct(r.get('fee',5)),'extra':num(r.get('extra',0)),
                       'state':str(r.get('state','저장된 항목')),'source':str(r.get('source','')),'checked':str(r.get('checked','')),'auto_discovered':bool(r.get('auto_discovered',False))}
                    if d['url']:check_url(d['url'])
                    cleaned.append(d)
                except ValueError:continue
            self.rows=cleaned;self._render()
        except (OSError,ValueError,TypeError):pass

    def _close(self):
        if self.busy:
            if not messagebox.askyesno('진행 중','가격 조회 중입니다. 창을 닫으시겠습니까?',parent=self.win):return
        try:self._save()
        except OSError:pass
        self.win.destroy()
