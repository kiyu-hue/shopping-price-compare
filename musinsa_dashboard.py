import csv
import threading
import queue
import tkinter as tk
from datetime import datetime
from tkinter import ttk, filedialog, messagebox
import webbrowser
from urllib.parse import quote
from musinsa_browser import discover
from kream_quotes import get_quote, KreamQuotesWindow

class MusinsaDashboard:
    def __init__(self, parent, keyword, sites, root):
        self.keyword=keyword
        self.sites=sites
        self.root=root
        self.rows=[]
        self.busy=False
        self.current_search=''
        area=ttk.LabelFrame(parent,text='자동 가격비교 (무신사 시범)',padding=9)
        area.pack(fill='both',expand=True)
        top=ttk.Frame(area);top.pack(fill='x',pady=(0,7))
        ttk.Label(top,text='사이즈 (선택)').pack(side='left')
        self.size=tk.StringVar()
        ttk.Entry(top,textvariable=self.size,width=13).pack(side='left',padx=5)
        ttk.Label(top,text='공란이면 전체 · 확인되지 않은 사이즈는 미확인으로 표시',foreground='#555').pack(side='left',padx=4)
        self.find_button=ttk.Button(top,text='무신사 첫 상품 자동조회',command=self.start_lookup)
        self.find_button.pack(side='right',padx=3)
        self.refresh_button=ttk.Button(top,text='가격 새로고침',command=self.start_lookup)
        self.refresh_button.pack(side='right',padx=3)
        toolbar=ttk.Frame(area);toolbar.pack(fill='x',pady=(0,6))
        ttk.Button(toolbar,text='가격순 정렬',command=self.sort_price).pack(side='left',padx=3)
        ttk.Button(toolbar,text='사이즈순 정렬',command=self.sort_size).pack(side='left',padx=3)
        ttk.Button(toolbar,text='선택한 상품 열기',command=self.open_product).pack(side='left',padx=3)
        ttk.Button(toolbar,text='KREAM 시세 관리',command=self.open_kream).pack(side='left',padx=3)
        ttk.Button(toolbar,text='CSV 저장',command=self.export_csv).pack(side='right',padx=3)
        self.progress=ttk.Progressbar(toolbar,mode='indeterminate',length=120)
        self.progress.pack(side='right',padx=10)
        frame=ttk.Frame(area);frame.pack(fill='both',expand=True)
        self.cols=('shop','name','size','price','source','kream','last','profit','state','checked')
        headings=('쇼핑몰','실제 상품명','사이즈','판매가','가격 근거','KREAM 즉시판매가','최근 체결가','예상 차익','조회 상태','확인 시각')
        widths=(90,290,100,95,170,130,110,95,170,150)
        self.tree=ttk.Treeview(frame,columns=self.cols,show='headings',height=12)
        for col,title,width in zip(self.cols,headings,widths):
            self.tree.heading(col,text=title)
            self.tree.column(col,width=width,anchor='center' if col!='name' else 'w')
        self.tree.pack(side='left',fill='both',expand=True)
        scroll=ttk.Scrollbar(frame,orient='vertical',command=self.tree.yview)
        scroll.pack(side='right',fill='y')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<Double-1>',lambda e:self.open_product())
        self.status=tk.StringVar(value='검색어를 입력하고 자동조회를 눌러주세요. 무신사 첫 번째 노출 상품을 사용합니다.')
        ttk.Label(area,textvariable=self.status,wraplength=1290).pack(anchor='w',pady=(7,0))
        ttk.Label(area,text='첫 상품이 원하는 모델인지 실제 상품명을 확인하세요. 공개 메타데이터가 없으면 가격미확인. 사이즈·품절은 확인할 수 있을 때만 표시합니다. KREAM 로그인 가격 자동수집은 아직 미지원.',foreground='#555',wraplength=1290).pack(anchor='w',pady=(5,0))
        self._render()

    def open_kream(self):
        KreamQuotesWindow(self.root,self.keyword.get().strip(),on_saved=self._render)

    def start_lookup(self):
        if self.busy:return
        term=self.keyword.get().strip()
        if len(term)<2:
            messagebox.showinfo('상품 검색','상품명이나 모델번호를 입력하세요.',parent=self.root)
            return
        entry=self.sites.get('무신사')
        template=entry.get('url','') if isinstance(entry,dict) else entry
        if not template or '{q}' not in template:
            messagebox.showwarning('검색 주소','무신사 검색 URL 설정을 확인해 주세요.',parent=self.root)
            return
        self.busy=True
        self.current_search=term
        self.find_button.configure(state='disabled')
        self.refresh_button.configure(state='disabled')
        self.progress.start(10)
        self.status.set('무신사 검색 결과 → 첫 상품 상세페이지를 확인 중입니다...')
        q=queue.Queue()
        def work():
            try: q.put(discover(term,template))
            except Exception as e:q.put({'shop':'무신사','name':'','size':'사이즈 미확인','price':None,'url':'','state':'조회실패','detail':str(e)[:150]})
        threading.Thread(target=work,daemon=True).start()
        self._poll(q,term)

    def _poll(self,q,term):
        try: data=q.get_nowait()
        except queue.Empty:
            self.root.after(180,lambda:self._poll(q,term));return
        self.busy=False
        self.progress.stop()
        self.find_button.configure(state='normal')
        self.refresh_button.configure(state='normal')
        if self.keyword.get().strip()!=term:
            self.status.set('검색어가 바뀌어 이전 조회 결과를 버렸습니다.')
            return
        data['size']='사이즈 미확인'  # never guess size or inventory
        self.rows=[data]
        self._render()
        self.status.set('조회 결과: '+data.get('state','미확인')+' · '+(data.get('detail') or data.get('url') or '상품 상세 URL 미확인')+' · '+(data.get('source') or '가격 근거 없음'))

    def _num(self,v):
        try: return int(v) if v not in (None,'') else None
        except (TypeError,ValueError):return None

    def _render(self):
        self.tree.delete(*self.tree.get_children())
        for i,r in enumerate(self.rows):
            # An unknown size must never be paired with a random KREAM quote.
            sz=r.get('size','사이즈 미확인')
            quote=get_quote(self.keyword.get().strip(),sz) if sz.isdigit() else {}
            sell=self._num(quote.get('sell'));last=self._num(quote.get('last'))
            price=self._num(r.get('price'))
            profit=(round(sell-price-sell*0.05) if sell is not None and price is not None else None)
            f=lambda v: f'{v:,}' if v is not None else ''
            self.tree.insert('','end',iid=str(i),values=(r.get('shop',''),r.get('name',''),sz,f(price),r.get('source',''),f(sell),f(last),f(profit),r.get('state',''),r.get('checked','')))

    def sort_price(self):
        self.rows.sort(key=lambda r:(r.get('price') is None,r.get('price') or 0));self._render()
    def sort_size(self):
        self.rows.sort(key=lambda r:(not str(r.get('size','')).isdigit(),int(r.get('size')) if str(r.get('size','')).isdigit() else 9999));self._render()
    def open_product(self):
        sel=self.tree.selection()
        if not sel:return
        r=self.rows[int(sel[0])]
        url=r.get('url') or r.get('search_url')
        if url:webbrowser.open_new_tab(url)
        else:self.status.set('확인된 상품 URL이 없습니다.')
    def export_csv(self):
        if not self.rows:
            messagebox.showinfo('CSV 저장','먼저 조회해 주세요.',parent=self.root);return
        path=filedialog.asksaveasfilename(defaultextension='.csv',filetypes=[('CSV','*.csv')],parent=self.root)
        if not path:return
        try:
            with open(path,'w',encoding='utf-8-sig',newline='') as f:
                w=csv.writer(f);w.writerow(['쇼핑몰','검색어','실제 상품명','사이즈','판매가','가격 근거','상태','확인 시각','상품 상세 URL','공식 검색 URL'])
                for r in self.rows:w.writerow([r.get('shop',''),self.keyword.get(),r.get('name',''),r.get('size',''),r.get('price') or '',r.get('source',''),r.get('state',''),r.get('checked',''),r.get('url',''),r.get('search_url','')])
            self.status.set('CSV 저장 완료: '+path)
        except OSError as ex:messagebox.showerror('저장 오류',str(ex),parent=self.root)
