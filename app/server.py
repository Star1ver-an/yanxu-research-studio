from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import urlparse,parse_qs,unquote

from .service import Service
from .workflows import ROOT,catalog
from .providers import ProviderError,api_complete
from .updater import UpdateManager
from .workflows import VERSION
import threading

class LocalServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,service=None,port=0):
        self.service=service or Service()
        self.token=secrets.token_urlsafe(32)
        self.native=None
        self.updates=UpdateManager(self.service.store.root,VERSION)
        super().__init__(('127.0.0.1',port),Handler)
        self.service.backups.start()

    def server_close(self):
        self.service.assistant.close()
        self.updates.close()
        self.service.backups.close()
        super().server_close()

    def update_status(self):
        import sys,os
        return self.updates.status(bool(self.native and getattr(sys,'frozen',False) and os.name=='nt'))

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):
        pass

    def reply(self,body,status=200,mime='application/json; charset=utf-8',extra=None):
        if not isinstance(body,bytes):
            body=json.dumps(body,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        # pywebview's official JS bridge builds method wrappers with new Function.
        # Permit that factory only inside the desktop host; remote scripts remain forbidden.
        script_policy="'self' 'unsafe-eval'" if self.server.native is not None else "'self'"
        self.send_header('Content-Security-Policy',f"default-src 'self'; script-src {script_policy}; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'")
        for k,v in (extra or {}).items():
            self.send_header(k,v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):
            pass

    def authorized(self):
        expected=f'127.0.0.1:{self.server.server_port}'
        if self.headers.get('Host')!=expected:
            return False
        return secrets.compare_digest(self.headers.get('X-Research-Token',''),self.server.token)

    def do_GET(self):
        path=urlparse(self.path).path
        if path.startswith('/api/'):
            if not self.authorized():
                return self.reply({'error':'本地连接校验失败，请重新打开软件。'},403)
            try:
                service=self.server.service
                if path=='/api/updates/status':
                    return self.reply(self.server.update_status())
                if path=='/api/bootstrap':
                    return self.reply({'catalog':catalog(),'settings':service.store.get_settings(),
                         'projects':service.project_list(),'data_path':str(service.store.root),
                         'onboarding':service.store.onboarding(),
                         'desktop_ready':bool(getattr(self.server.native,'_ready',False))})
                if path=='/api/projects':
                    return self.reply(service.project_list())
                if path=='/api/backups':
                    return self.reply(service.backups.status())
                if path.startswith('/api/project/'):
                    return self.reply(service.public_project(path.split('/')[-1]))
                return self.reply({'error':'未找到此功能。'},404)
            except (ValueError,ProviderError) as e:
                return self.reply({'error':str(e)},400)
        if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}':
            return self.reply({'error':'Invalid host'},403)
        names={'/':'index.html','/ars':'index.html','/scientific':'index.html',
               '/app.js':'app.js','/pathways.js':'pathways.js','/appearance.js':'appearance.js','/updates.js':'updates.js',
               '/projects.js':'projects.js','/onboarding.js':'onboarding.js','/research.js':'research.js','/research.css':'research.css',
               '/assistant.js':'assistant.js','/assistant-proposals.js':'assistant-proposals.js','/assistant.css':'assistant.css',
               '/style.css':'style.css','/soft-ui.css':'soft-ui.css','/mark.svg':'mark.svg','/favicon.svg':'favicon.svg'}
        if path not in names:
            return self.reply({'error':'Not found'},404)
        p=ROOT/'web'/names[path]
        content=p.read_bytes()
        if names[path]=='index.html':
            content=content.replace(b'__RESEARCH_TOKEN__',self.server.token.encode())
        mime={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8',
              '.js':'application/javascript; charset=utf-8','.svg':'image/svg+xml'}[p.suffix]
        return self.reply(content,mime=mime)

    def do_POST(self):
        if not self.authorized():
            return self.reply({'error':'本地连接校验失败。'},403)
        try:
            length=int(self.headers.get('Content-Length',0))
            path=urlparse(self.path).path
            if path=='/api/backups/import':
                from .backups import MAX_BACKUP
                if not 0<length<=MAX_BACKUP:
                    return self.reply({'error':'备份压缩包请控制在 256 MB 内。'},413)
                preview=parse_qs(urlparse(self.path).query).get('preview')==['1']
                if not preview and self.headers.get('X-Research-Confirm')!='restore':
                    raise ValueError('请先预览并确认恢复为独立副本。')
                return self.reply(self.server.service.backups.restore(self.rfile.read(length),preview=preview))
            if length<0 or length>30*1024*1024:
                return self.reply({'error':'请求过大，单个文件请控制在 20 MB 内。'},413)
            data=json.loads(self.rfile.read(length) or '{}')
            if not isinstance(data,dict):
                raise ValueError('请求格式无效。')
            service=self.server.service
            path=urlparse(self.path).path
            if path.startswith('/api/assistant/'):
                action=path.rsplit('/',1)[-1]
                if action=='export':
                    return self.reply(service.assistant.export(data),mime='text/markdown; charset=utf-8')
                operations={'state':service.assistant.state,'preview':service.assistant.preview,
                            'send':service.assistant.send,'stop':service.assistant.stop,'clear':service.assistant.clear,
                            'proposal-preview':service.assistant.proposal_preview,
                            'proposal-apply':service.assistant.proposal_apply,
                            'proposal-discard':service.assistant.proposal_discard}
                if action not in operations:
                    raise ValueError('不支持的助手操作。')
                return self.reply(operations[action](data))
            if path.startswith('/api/updates/'):
                action=path.rsplit('/',1)[-1]
                if action=='check':
                    self.server.updates.check()
                elif action=='auto':
                    self.server.updates.set_auto(data.get('enabled'))
                elif action=='download':
                    if not self.server.update_status()['installable']:
                        raise ValueError('请在 Windows 桌面版中下载更新，开发模式可通过仓库更新源码。')
                    self.server.updates.download()
                elif action=='install':
                    if data.get('acknowledge') is not True or not self.server.update_status()['installable']:
                        raise ValueError('请保存工作，并在 Windows 桌面版确认安装。')
                    self.server.native._install_update()
                else:
                    return self.reply({'error':'未找到此更新操作。'},404)
                return self.reply(self.server.update_status())
            if path=='/api/project':
                with service.lock:
                    if service.updating:raise ValueError('软件正在更新，请稍后创建项目。')
                    return self.reply(service.store.create(data))
            if path=='/api/research/demo':
                return self.reply(service.research_demo())
            if path=='/api/projects/manage':
                return self.reply(service.manage_projects(data))
            if path=='/api/preferences/onboarding':
                return self.reply(service.store.save_onboarding(data))
            if path=='/api/backups/create':
                return self.reply(service.backups.create())
            if path=='/api/backups/settings':
                return self.reply(service.backups.configure(data))
            if path=='/api/backups/download':
                return self.reply(service.backups.path(data.get('id')).read_bytes(),mime='application/zip')
            if path=='/api/backups/restore':
                if not data.get('preview') and data.get('acknowledge') is not True:
                    raise ValueError('请确认恢复为独立副本。')
                return self.reply(service.backups.restore(service.backups.path(data.get('id')).read_bytes(),preview=bool(data.get('preview'))))
            if path=='/api/recommend':
                from .routing import recommend
                return self.reply(recommend(data))
            if path=='/api/scientific/skill':
                from .scientific import skill_detail
                return self.reply(skill_detail(str(data.get('id',''))))
            if path=='/api/settings':
                return self.reply(service.store.save_settings(data))
            if path=='/api/codex/inspect':
                return self.reply(service.inspect_codex())
            if path=='/api/connection/test':
                settings=service.store.get_settings(secret=True)
                if settings['mode']=='codex':
                    return self.reply(service.inspect_codex())
                result=api_complete(settings,settings.pop('api_key',''),
                         'You are a connection test. Reply OK only.','Reply OK.',lambda *a:None,threading.Event())
                return self.reply({'ok':True,'message':result[:200]})
            if path.startswith('/api/project/'):
                pieces=path.strip('/').split('/')
                pid,action=pieces[2],pieces[3]
                functions={'upload':service.upload,'toggle':service.toggle_asset,'edit':service.edit,
                           'confirm':service.confirm,'run':service.start,'info':service.update_info,
                           'literature-toggle':service.toggle_literature}
                functions.update({'research-save':service.research_save,'research-delete':service.research_delete,
                    'research-seed':service.research_seed,'research-style':service.research_style,
                    'material-page':service.material_page,'version-compare':service.version_compare,
                    'version-restore':service.version_restore,'issues-import':service.issues_import,
                    'reference-import':service.reference_import,'reference-verify':service.reference_verify})
                functions.update({'run-preview':service.run_preview,'research-context':service.research_context})
                if action in functions:
                    return self.reply(functions[action](pid,data))
                if action=='stop':
                    return self.reply(service.stop(pid))
                if action=='material-raw':
                    raw,mime=service.material_bytes(pid,data.get('id'))
                    return self.reply(raw,mime=mime)
                if action=='research-export':
                    name,raw,mime=service.research_export(pid,data.get('kind'))
                    return self.reply(raw,mime=mime)
                if action=='search':
                    return self.reply(service.search(pid,str(data.get('query','')),data.get('required_terms'),bool(data.get('journal_only',True)),data.get('source','crossref')))
                if action=='export':
                    name,raw,mime=service.export(pid,data.get('format','md'),data.get('stage',''))
                    return self.reply(raw,mime=mime,extra={'Content-Disposition':"attachment; filename*=UTF-8''"+__import__('urllib.parse',fromlist=['quote']).quote(name)})
            return self.reply({'error':'未找到此功能。'},404)
        except (ValueError,KeyError,TypeError,IndexError,ProviderError) as e:
            message=str(e) if isinstance(e,(ValueError,ProviderError)) else '请求信息不完整。'
            return self.reply({'error':message},400)
        except Exception:
            return self.reply({'error':'操作未完成，请重试。已有项目已保留。'},500)
