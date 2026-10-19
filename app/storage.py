import base64
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid

from .workflows import FLOWS

def now():
    return datetime.now(timezone.utc).isoformat()

def data_directory():
    override=os.environ.get('YANXU_DATA_DIR')
    if override:
        return Path(override).resolve()
    return Path(os.environ.get('LOCALAPPDATA',Path.home()))/'YanxuResearchStudio'

class Blob(ctypes.Structure):
    _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_byte))]

def protect(text, decrypt=False):
    if os.name!='nt':
        raise ValueError('此版本只在 Windows 上保存加密密钥。')
    raw=base64.b64decode(text) if decrypt else text.encode('utf-8')
    buf=ctypes.create_string_buffer(raw)
    source=Blob(len(raw),ctypes.cast(buf,ctypes.POINTER(ctypes.c_byte)))
    dest=Blob()
    fn=ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(dest)):
        raise ValueError('Windows 无法处理加密密钥。请重新输入。')
    try:
        out=ctypes.string_at(dest.pbData,dest.cbData)
        return out.decode('utf-8') if decrypt else base64.b64encode(out).decode('ascii')
    finally:
        ctypes.windll.kernel32.LocalFree(dest.pbData)

DEFAULT_SETTINGS=dict(mode='codex',codex_path='',codex_model='',provider='compatible',
                      base_url='https://api.deepseek.com/v1',api_model='deepseek-chat',max_tokens=6000)

class Store:
    def __init__(self,path=None):
        self.root=Path(path or data_directory())
        self.root.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.db=self.root/'workspace.sqlite3'
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
        self.recover()

    @contextmanager
    def connect(self):
        connection=sqlite3.connect(self.db,timeout=15)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def get_settings(self,secret=False):
        with self.connect() as c:
            row=c.execute('SELECT body FROM settings WHERE id=1').fetchone()
        s={**DEFAULT_SETTINGS,**(json.loads(row[0]) if row else {})}
        encrypted=s.pop('encrypted_key','')
        if secret:
            s['api_key']=protect(encrypted,True) if encrypted else ''
        else:
            s['has_key']=bool(encrypted)
        return s

    def save_settings(self,data):
        with self.lock:
            with self.connect() as c:
                row=c.execute('SELECT body FROM settings WHERE id=1').fetchone()
                s={**DEFAULT_SETTINGS,**(json.loads(row[0]) if row else {})}
                for k in DEFAULT_SETTINGS:
                    if k in data:
                        s[k]=data[k]
                if s['mode'] not in ('codex','api') or s['provider'] not in ('compatible','anthropic'):
                    raise ValueError('不支持的连接类型。')
                s['max_tokens']=max(256,min(32000,int(s['max_tokens'])))
                if data.get('clear_key'):
                    s.pop('encrypted_key',None)
                elif data.get('api_key','').strip():
                    s['encrypted_key']=protect(data['api_key'].strip())
                c.execute('INSERT OR REPLACE INTO settings VALUES(1,?)',(json.dumps(s),))
        return self.get_settings()

    def list(self):
        with self.connect() as c:
            values=[json.loads(r[0]) for r in c.execute('SELECT body FROM projects')]
        return sorted(values,key=lambda p:p['updated'],reverse=True)

    def get(self,pid):
        with self.connect() as c:
            row=c.execute('SELECT body FROM projects WHERE id=?',(pid,)).fetchone()
        if not row:
            raise ValueError('未找到这个项目。')
        return json.loads(row[0])

    def put(self,p):
        p['updated']=now()
        with self.lock,self.connect() as c:
            c.execute('INSERT OR REPLACE INTO projects VALUES(?,?)',(p['id'],json.dumps(p,ensure_ascii=False)))
        return p

    def create(self,data):
        flow=data.get('flow','full')
        if flow not in FLOWS:
            raise ValueError('请选择有效的研究流程。')
        route=data.get('route',FLOWS[flow]['route'])
        if route not in ('ars','scientific') or route!=FLOWS[flow]['route']:
            raise ValueError('研究路径与工作流不匹配，请重新选择。')
        selected_skill=str(data.get('selected_skill',''))
        if selected_skill:
            if route!='scientific':raise ValueError('专业技能只能用于科研工具与分析路径。')
            from .scientific import skill_text
            skill_text(selected_skill,0)
        if flow=='scientific_task' and not selected_skill:
            raise ValueError('请先为专业技能专题选择一项技能。')
        from .routing import recommend
        recommendation=recommend(data) if data.get('route_choice')=='recommended' else None
        if recommendation and (recommendation['route']!=route or recommendation['flow']!=flow):
            raise ValueError('推荐与当前目标或选择不一致，请重新获取推荐或手动选择路径。')
        title=str(data.get('title','')).strip()[:140]
        if not title:
            raise ValueError('请填写项目名称。')
        declaration=data.get('experiments','none')
        if declaration not in ('none','declared','undecided'):
            raise ValueError('请选择实验来源声明。')
        p=dict(id=uuid.uuid4().hex,title=title,topic=str(data.get('topic',''))[:20000],
               discipline=str(data.get('discipline','跨学科'))[:200],flow=flow,route=route,
               selected_skill=selected_skill,goal=str(data.get('goal','unsure'))[:30],
               data_kind=str(data.get('data_kind','unknown'))[:30],
               route_choice='recommended' if data.get('route_choice')=='recommended' else 'manual',
               routing_recommendation=recommendation,
               citation=str(data.get('citation','APA 7'))[:100],experiments=declaration,
               requirements=str(data.get('requirements',''))[:20000],created=now(),updated=now(),
               cursor=0,assets=[],literature=[],runs=[],outputs={},confirmations=[],demo=False)
        return self.put(p)

    def recover(self):
        for p in self.list():
            changed=False
            for r in p.get('runs',[]):
                if r['status']=='running':
                    r.update(status='interrupted',error='上次运行随软件关闭而中断，可重新运行。')
                    changed=True
            if changed:
                self.put(p)

def extract_file(name,raw):
    suffix=Path(name).suffix.lower()
    if suffix=='.pdf':
        from pypdf import PdfReader
        reader=PdfReader(io.BytesIO(raw))
        if reader.is_encrypted and not reader.decrypt(''):
            raise ValueError('该 PDF 已加密，请先解密后导入。')
        if len(reader.pages)>600:
            raise ValueError('PDF 超过 600 页，请拆分后导入。')
        text='\n\n'.join(f'[第 {i+1} 页]\n'+(page.extract_text() or '') for i,page in enumerate(reader.pages))
        if len(text.strip())<35:
            raise ValueError('未提取到可用文本，这可能是扫描版 PDF。请先 OCR 或导入文字版。')
    elif suffix=='.docx':
        from docx import Document
        doc=Document(io.BytesIO(raw))
        parts=[p.text for p in doc.paragraphs]
        for table in doc.tables:
            parts += [' | '.join(cell.text for cell in row.cells) for row in table.rows]
        text='\n'.join(parts)
    elif suffix in ('.txt','.md','.csv','.ris','.nbib','.bib','.json','.tsv'):
        try:
            text=raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text=raw.decode('gb18030')
    else:
        raise ValueError('支持 PDF、DOCX、TXT、Markdown、CSV、RIS、NBIB、BibTeX 和 JSON。')
    if not text.strip():
        raise ValueError('文件没有可用文本。')
    if len(text)>1200000:
        raise ValueError('提取文本过长，请拆分文件后导入。')
    return text
