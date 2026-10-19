"""Official local Codex App Server and configurable HTTP model connections."""
import json
from http.client import HTTPException
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

class ProviderError(RuntimeError):
    pass

class Cancelled(ProviderError):
    pass


class ReplyCollector:
    """Keep authoritative item phases; commentary never becomes a report."""
    def __init__(self):
        self.items = {}
        self.completed = []

    def item(self, item, completed=False):
        if item.get('type') != 'agentMessage' or not item.get('id'):
            return
        key = item['id']
        old = self.items.setdefault(key, {'text':'', 'phase':None})
        if item.get('phase') is not None:
            old['phase'] = item['phase']
        if 'text' in item:
            old['text'] = item['text']
        if completed and key not in self.completed:
            self.completed.append(key)

    def delta(self, key, text):
        item = self.items.setdefault(key, {'text':'', 'phase':None})
        item['text'] += text

    def preview(self):
        return '\n\n'.join(i['text'] for i in self.items.values()
                           if i['phase'] in ('partial_answer','final_answer') and i['text'].strip())

    def result(self):
        final = [self.items[k]['text'] for k in self.completed
                 if self.items[k]['phase'] == 'final_answer' and self.items[k]['text'].strip()]
        if final:
            return '\n\n'.join(final)
        # Older Codex releases did not emit phase. Keep only the final completed
        # unlabelled message, never concatenate preambles. Explicit commentary
        # or partial answers alone are not a completed report.
        legacy = [self.items[k]['text'] for k in self.completed
                  if self.items[k]['phase'] is None and self.items[k]['text'].strip()]
        if legacy and not any(i['phase'] is not None for i in self.items.values()):
            return legacy[-1]
        raise ProviderError('Codex 未返回最终正文，进度说明未保存为报告。请重新运行。')

    def metadata(self):
        phases = {}
        for k in self.completed:
            phase = self.items[k]['phase'] or 'legacy_unlabelled'
            phases[phase] = phases.get(phase, 0) + 1
        return {'message_phases':phases,
                'legacy_fallback':bool(self.completed) and not any(i['phase'] for i in self.items.values())}

def find_codex(configured=''):
    if configured:
        p = Path(configured).expanduser()
        if p.is_file() and p.suffix.lower() in ('.exe', '.cmd', '.bat', ''):
            return str(p.resolve())
        raise ProviderError('指定的 Codex 路径不存在，请选择 codex.exe 或 codex.cmd。')
    found = shutil.which('codex')
    if found:
        return found
    local = Path(os.environ.get('LOCALAPPDATA', ''))
    candidates = list((local / 'OpenAI' / 'Codex' / 'bin').glob('*/codex.exe'))
    candidates += list((local / 'Programs' / 'Codex').glob('**/codex.exe'))
    winapps = local / 'Microsoft' / 'WindowsApps'
    candidates += list(winapps.glob('OpenAI.Codex*/codex.exe'))
    roaming = Path(os.environ.get('APPDATA', '')) / 'npm' / 'codex.cmd'
    if roaming.is_file():
        candidates.append(roaming)
    if candidates:
        return str(max(candidates, key=lambda p: p.stat().st_mtime))
    raise ProviderError('尚未找到 Codex。请先安装并登录 Codex，或在设置中指定 codex.exe 路径。')

def codex_command(path):
    # npm's Windows shim requires cmd.exe. Only a validated existing path is quoted;
    # all task content goes through stdin as JSON, never through a command string.
    if path.lower().endswith(('.cmd', '.bat')):
        if any(c in path for c in ['"', '&', '|', '<', '>', '^', '%', '!', '\n', '\r']):
            raise ProviderError('此 Codex 脚本路径含不支持的字符，请改用 codex.exe。')
        return [os.environ.get('COMSPEC','cmd.exe'), '/d', '/s', '/c', f'""{path}" app-server"']
    return [path, 'app-server']

class CodexClient:
    def __init__(self, settings, cwd, cancelled=None):
        self.events = queue.Queue()
        self.seq = 0
        self.cancelled = cancelled or threading.Event()
        self.saved_events = []
        self.path = find_codex(settings.get('codex_path',''))
        self.cwd = cwd
        env = os.environ.copy()
        # This mode must not silently use an API environment key.
        for key in ('OPENAI_API_KEY', 'CODEX_API_KEY'):
            env.pop(key, None)
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        self.proc = subprocess.Popen(codex_command(self.path), cwd=cwd, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, encoding='utf-8', errors='replace', bufsize=1,
                                     creationflags=flags, env=env)
        threading.Thread(target=self._reader, daemon=True).start()
        try:
            self.request('initialize', {'clientInfo': {'name':'yanxu_research_studio',
                        'title':'研序 Research Studio','version':'0.1.1'}})
            self.send({'method':'initialized'})
        except Exception:
            self.close()
            raise

    def _reader(self):
        try:
            for line in self.proc.stdout:
                try:
                    self.events.put(json.loads(line))
                except json.JSONDecodeError:
                    pass
        finally:
            self.events.put({'_closed':True})

    def send(self, obj):
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False)+'\n')
        self.proc.stdin.flush()

    def next(self, deadline):
        while time.monotonic() < deadline:
            if self.cancelled.is_set():
                raise Cancelled('任务已停止，之前的成果已保留。')
            try:
                event = self.events.get(timeout=.2)
            except queue.Empty:
                continue
            if event.get('_closed'):
                raise ProviderError('Codex 连接已关闭。请确认 Codex 版本与登录状态。')
            # Never grant permissions or proxy arbitrary tool calls.
            if 'id' in event and 'method' in event:
                self.send({'id':event['id'], 'error':{'code':-32601,
                          'message':'Research Studio does not approve device actions.'}})
                continue
            return event
        raise ProviderError('等待 Codex 超时。可停止后重新运行。')

    def request(self, method, params=None, timeout=30):
        self.seq += 1
        request_id = self.seq
        self.send({'id':request_id,'method':method,'params':params or {}})
        deadline = time.monotonic()+timeout
        while True:
            event = self.next(deadline)
            if event.get('id') == request_id:
                if 'error' in event:
                    raise ProviderError(str(event['error'].get('message','Codex 请求失败')))
                return event.get('result',{})
            self.saved_events.append(event)

    def account(self):
        return self.request('account/read', {'refreshToken':False}).get('account')

    def inspect(self):
        account = self.account()
        models = self.request('model/list', {'limit':100,'includeHidden':False})
        limits = None
        if account and account.get('type')=='chatgpt':
            try:
                limits = self.request('account/rateLimits/read')
            except ProviderError:
                pass
        return dict(found=True,path=self.path,account=account,models=models.get('data',[]),limits=limits)

    def complete(self, system, prompt, model, progress):
        account = self.account()
        if not account or account.get('type') != 'chatgpt':
            raise ProviderError('本机 Codex 尚未通过 ChatGPT 登录。请先在 Codex 中使用 ChatGPT 账号登录；此连接不会自动改用 API 计费。')
        params = {'cwd':str(Path.cwd()), 'approvalPolicy':'never', 'sandbox':'read-only',
                  'ephemeral':True, 'developerInstructions':system}
        # Use the dedicated invocation directory, not the source workspace.
        params['cwd'] = str(self.cwd)
        if model:
            params['model']=model
        started = self.request('thread/start', params, timeout=45)
        thread_id = started['thread']['id']
        self.saved_events.clear()
        turn = self.request('turn/start', {'threadId':thread_id,
                     'input':[{'type':'text','text':prompt}], 'approvalPolicy':'never',
                     'sandboxPolicy':{'type':'readOnly'}}, timeout=60)
        turn_id=turn['turn']['id']
        deadline = time.monotonic()+1200
        replies = ReplyCollector()
        pending = self.saved_events[:]
        self.saved_events.clear()
        while True:
            event = pending.pop(0) if pending else self.next(deadline)
            method = event.get('method','')
            p = event.get('params',{})
            if p.get('threadId',thread_id) != thread_id or p.get('turnId',turn_id) != turn_id:
                continue
            if method == 'item/agentMessage/delta':
                k=p.get('itemId','message')
                replies.delta(k,p.get('delta',''))
                progress('正在生成报告', replies.preview())
            elif method in ('item/started','item/completed'):
                replies.item(p.get('item',{}), completed=method=='item/completed')
                progress('正在生成报告', replies.preview())
            elif method == 'turn/completed' and p.get('turn',{}).get('id') == turn_id:
                result=p['turn']
                if result.get('status')!='completed':
                    error=result.get('error') or {}
                    raise ProviderError(error.get('message', 'Codex 任务未完成：'+str(result.get('status'))))
                for item in result.get('items',[]):
                    replies.item(item,completed=True)
                self.response_metadata = replies.metadata()
                return replies.result()

    @property
    def cwd(self):
        return self._cwd

    @cwd.setter
    def cwd(self,value):
        self._cwd=value

    def close(self):
        if getattr(self,'proc',None):
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            for p in (self.proc.stdin, self.proc.stdout):
                if p:
                    p.close()

    def __enter__(self):
        return self

    def __exit__(self,*args):
        self.close()

def validate_endpoint(url):
    p=urlparse(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:
        raise ProviderError('请输入有效的模型服务地址。')
    if p.scheme=='http' and p.hostname not in ('localhost','127.0.0.1','::1'):
        raise ProviderError('远程模型服务请使用 HTTPS；HTTP 仅支持本机服务。')
    return url.rstrip('/')

def api_complete(settings, key, system, prompt, progress, cancelled):
    base=validate_endpoint(settings.get('base_url',''))
    model=settings.get('api_model','').strip()
    if not model:
        raise ProviderError('请先在模型设置中填写模型名称。')
    if not key:
        raise ProviderError('请先在模型设置中填写 API Key。')
    anth=settings.get('provider')=='anthropic'
    endpoint=base if base.endswith(('/messages','/chat/completions')) else base+('/messages' if anth else '/chat/completions')
    headers={'Content-Type':'application/json','User-Agent':'ResearchStudio/0.1.1'}
    if anth:
        headers.update({'x-api-key':key,'anthropic-version':'2023-06-01'})
        body={'model':model,'max_tokens':settings.get('max_tokens',6000),'system':system,
              'messages':[{'role':'user','content':prompt}]}
    else:
        headers['Authorization']='Bearer '+key
        body={'model':model,'max_tokens':settings.get('max_tokens',6000),
              'messages':[{'role':'system','content':system},{'role':'user','content':prompt}], 'stream':False}
    progress('正在等待模型服务返回','')
    if cancelled.is_set():
        raise Cancelled('任务已停止。')
    request=Request(endpoint,json.dumps(body).encode('utf-8'),headers,method='POST')
    try:
        with urlopen(request, timeout=240) as response:
            raw=response.read(16*1024*1024)
            data=json.loads(raw)
    except HTTPError as e:
        hint={401:'密钥无效或已过期，请重新填写。',403:'当前密钥没有模型或项目访问权限。',
              404:'端点或模型不存在，请检查接口协议、服务地址与模型名。',
              429:'额度不足或请求过于频繁，请检查服务商额度并稍后重试。'}.get(e.code,
              '服务暂时不可用，请检查服务地址或稍后重试。')
        raise ProviderError(f'模型接口返回 HTTP {e.code}。'+hint) from None
    except (URLError,TimeoutError,OSError,HTTPException) as e:
        raise ProviderError('无法连接模型接口，或请求超时。请检查网络和服务地址。') from e
    except (ValueError,KeyError) as e:
        raise ProviderError('接口返回的数据不是有效 JSON。请检查兼容协议。') from e
    if cancelled.is_set():
        raise Cancelled('任务已停止。本次已发出的 API 请求可能仍产生用量。')
    try:
        if not isinstance(data,dict):
            raise ValueError('invalid response')
        if anth:
            text='\n'.join(b.get('text','') for b in data['content'] if isinstance(b,dict) and b.get('type')=='text')
            stopped=data.get('stop_reason')=='max_tokens'
        else:
            choice=data['choices'][0]
            text=choice['message']['content']
            if isinstance(text,list):
                text='\n'.join(b.get('text','') for b in text
                               if isinstance(b,dict) and b.get('type') in ('text','output_text'))
            stopped=choice.get('finish_reason')=='length'
        if not isinstance(text,str) or not text.strip():
            raise ValueError('empty')
        if stopped:
            text+='\n\n> 输出达到长度上限，内容可能不完整。请增大输出上限或分章节重跑。'
        return text
    except (KeyError,IndexError,TypeError,ValueError) as e:
        raise ProviderError('接口没有返回文本回答，请确认服务支持所选协议。') from e
