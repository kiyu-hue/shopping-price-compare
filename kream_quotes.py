import sys
"""User-entered KREAM quotes, kept per model code and size. No login automation."""
import csv
import json
import re
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk, messagebox, filedialog

DATA_FILE = (Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent) / 'kream_size_quotes.json'
SIZES = ['220','225','230','235','240','245','250','255','260','265','270','275','280','285','290','295','300','FREE']


def key_for(model):
    return re.sub(r'\s+', ' ', str(model).strip()).upper()


def money(value):
    value = str(value).replace(',', '').replace('원', '').strip()
    if not value:
        return None
    if not value.isdecimal():
        raise ValueError('가격에는 0 이상의 정수만 입력해주세요.')
    return int(value)


def load_quotes():
    try:
        if not DATA_FILE.exists(): return {}
        data = json.loads(DATA_FILE.read_text(encoding='utf-8'))
        if data.get('version') == 1 and isinstance(data.get('models'), dict):
            return data['models']
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return {}


def save_quotes(models):
    temp = DATA_FILE.with_suffix('.tmp')
    temp.write_text(json.dumps({'version':1, 'models':models}, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(DATA_FILE)


def get_quote(model, size):
    model = key_for(model)
    size = str(size).strip().upper()
    return load_quotes().get(model, {}).get(size, {})


class KreamQuotesWindow:
    def __init__(self, parent, model='', on_saved=None):
        self.win = tk.Toplevel(parent)
        self.win.title('KREAM 사이즈별 시세 기록')
        self.win.geometry('930x650')
        self.win.minsize(740, 520)
        self.on_saved = on_saved
        self.models = load_quotes()
        self.model_var = tk.StringVar(value=key_for(model))
        self.size_var = tk.StringVar(value='260')
        self.sell_var = tk.StringVar()
        self.last_var = tk.StringVar()
        self.buy_var = tk.StringVar()
        self.status_var = tk.StringVar(value='KREAM 로그인 후 직접 확인한 가격을 입력하세요. 로그인 정보는 저장하지 않습니다.')
        self._build()
        self._render()

    def _build(self):
        body = ttk.Frame(self.win, padding=13); body.pack(fill='both',expand=True)
        ttk.Label(body,text='KREAM 사이즈별 가격 · 수동 확인/저장',font=('Malgun Gothic',15,'bold')).pack(anchor='w',pady=(0,8))
        ttk.Label(body,text='즉시판매가 = 지금 판매 시 받아들일 수 있는 매수 호가(표시 방식은 KREAM에서 직접 확인). 최근 체결가는 과거 거래 참고값입니다. 모두 직접 입력한 시세이며 자동 수집하지 않습니다.',wraplength=870).pack(anchor='w',pady=(0,10))
        toolbar=ttk.Frame(body);toolbar.pack(fill='x',pady=4)
        ttk.Label(toolbar,text='모델번호 / 상품 식별자').pack(side='left')
        ttk.Entry(toolbar,textvariable=self.model_var,width=36).pack(side='left',padx=7)
        ttk.Button(toolbar,text='이 모델 불러오기',command=self._render).pack(side='left')
        ttk.Button(toolbar,text='CSV 내보내기',command=self._export).pack(side='right')
        entry=ttk.LabelFrame(body,text='사이즈별 KREAM 시세 입력',padding=10);entry.pack(fill='x',pady=8)
        fields=[('사이즈',self.size_var),('즉시판매가',self.sell_var),('최근 체결가',self.last_var),('즉시구매가(선택)',self.buy_var)]
        for col,(name,var) in enumerate(fields):
            ttk.Label(entry,text=name).grid(row=0,column=col,sticky='w',padx=5)
            if col == 0: ttk.Combobox(entry,textvariable=var,values=SIZES,width=12).grid(row=1,column=col,sticky='ew',padx=5,pady=5)
            else: ttk.Entry(entry,textvariable=var,width=18).grid(row=1,column=col,sticky='ew',padx=5,pady=5)
            entry.columnconfigure(col,weight=1)
        ttk.Button(entry,text='이 사이즈 시세 저장',command=self._save_one).grid(row=2,column=3,sticky='e',padx=5,pady=5)
        frame=ttk.Frame(body);frame.pack(fill='both',expand=True,pady=8)
        cols=('size','sell','last','buy','time')
        self.tree=ttk.Treeview(frame,columns=cols,show='headings',height=12,selectmode='browse')
        heads={'size':'사이즈','sell':'즉시판매가','last':'최근 체결가','buy':'즉시구매가','time':'직접 확인/수정 시각'}
        for c in cols:
            self.tree.heading(c,text=heads[c]); self.tree.column(c,width=120 if c!='time' else 175,anchor='center')
        self.tree.pack(side='left',fill='both',expand=True)
        ttk.Scrollbar(frame,orient='vertical',command=self.tree.yview).pack(side='right',fill='y')
        self.tree.bind('<<TreeviewSelect>>',self._select)
        row=ttk.Frame(body);row.pack(fill='x',pady=5)
        ttk.Button(row,text='선택 사이즈 삭제',command=self._delete).pack(side='left')
        ttk.Button(row,text='시세표 새로고침',command=self._render).pack(side='left',padx=6)
        ttk.Button(row,text='비교표로 돌아가기',command=self.win.destroy).pack(side='right')
        ttk.Label(body,textvariable=self.status_var,wraplength=875).pack(anchor='w',pady=4)

    def _current(self):
        k=key_for(self.model_var.get())
        if not k: raise ValueError('모델번호나 상품 식별자를 입력해주세요.')
        return k

    def _render(self):
        for node in self.tree.get_children():self.tree.delete(node)
        model=key_for(self.model_var.get())
        data=self.models.get(model,{})
        for size in sorted(data,key=lambda s: (0,int(s)) if s.isdigit() else (1,s)):
            d=data[size]
            fmt=lambda n: '' if n is None else f'{n:,}'
            self.tree.insert('','end',iid=size,values=(size,fmt(d.get('sell')),fmt(d.get('last')),fmt(d.get('buy')),d.get('updated','')))
        self.status_var.set(f'{model or "모델 미입력"}: {len(data)}개 사이즈 저장됨 · 직접 확인한 값만 사용합니다.')

    def _select(self,event=None):
        selected=self.tree.selection()
        if not selected:return
        size=selected[0]; d=self.models.get(key_for(self.model_var.get()),{}).get(size,{})
        self.size_var.set(size)
        for var,k in ((self.sell_var,'sell'),(self.last_var,'last'),(self.buy_var,'buy')):
            var.set('' if d.get(k) is None else str(d[k]))

    def _save_one(self):
        try:
            model=self._current();size=self.size_var.get().strip().upper()
            if not size or len(size)>24:raise ValueError('사이즈를 확인해주세요.')
            record={'sell':money(self.sell_var.get()),'last':money(self.last_var.get()),'buy':money(self.buy_var.get()),'updated':datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')}
            if all(record[k] is None for k in ('sell','last','buy')):raise ValueError('최소 한 가지 가격을 입력해주세요.')
            self.models.setdefault(model,{})[size]=record
            save_quotes(self.models)
            self._render()
            if self.on_saved:self.on_saved()
            self.status_var.set(f'{model} / {size} 가격을 저장했습니다.')
        except (ValueError,OSError) as e:messagebox.showwarning('저장 확인',str(e),parent=self.win)

    def _delete(self):
        try:model=self._current()
        except ValueError as e:messagebox.showwarning('선택 확인',str(e),parent=self.win);return
        selected=self.tree.selection()
        if not selected:return
        if not messagebox.askyesno('삭제 확인',f'{model} / {selected[0]} 시세를 삭제할까요?',parent=self.win):return
        self.models.get(model,{}).pop(selected[0],None)
        try:save_quotes(self.models)
        except OSError as e:messagebox.showerror('삭제 실패',str(e),parent=self.win);return
        self._render()
        if self.on_saved:self.on_saved()

    def _export(self):
        try:model=self._current()
        except ValueError as e:messagebox.showwarning('모델 확인',str(e),parent=self.win);return
        path=filedialog.asksaveasfilename(parent=self.win,title='KREAM 시세표 CSV',defaultextension='.csv',filetypes=[('CSV','*.csv')])
        if not path:return
        try:
            with open(path,'w',encoding='utf-8-sig',newline='') as f:
                w=csv.writer(f);w.writerow(['모델번호','사이즈','즉시판매가','최근 체결가','즉시구매가','직접 확인/수정 시각'])
                for size,d in sorted(self.models.get(model,{}).items()):
                    w.writerow([model,size,d.get('sell'),d.get('last'),d.get('buy'),d.get('updated','')])
            messagebox.showinfo('저장 완료','CSV 시세표를 저장했습니다.',parent=self.win)
        except OSError as e:messagebox.showerror('내보내기 실패',str(e),parent=self.win)
