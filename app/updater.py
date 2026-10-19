"""Opt-in installation of verified releases from this application's GitHub repo."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid
import zipfile

REPOSITORY='Star1ver-an/yanxu-research-studio'
RELEASES='https://github.com/'+REPOSITORY+'/releases'
LATEST='https://api.github.com/repos/'+REPOSITORY+'/releases/latest'
MAX_ARCHIVE=200*1024*1024
MAX_EXECUTABLE=300*1024*1024
HOSTS={'api.github.com','github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'}


def version(value):
    if not isinstance(value,str) or not re.fullmatch(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)',value):
        raise ValueError('更新版本号无效。')
    return tuple(map(int,value.lstrip('v').split('.')))


def checked_url(url):
    p=urlparse(url)
    if p.scheme!='https' or p.hostname not in HOSTS or p.username or p.password or p.port not in (None,443):
        raise ValueError('更新地址不属于受信任的 GitHub 下载服务。')
    return url


class GitHubRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        checked_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def open_url(url):
    checked_url(url)
    request=Request(url,headers={'User-Agent':'YanxuResearchStudio-Updater',
                    'Accept':'application/vnd.github+json' if url==LATEST else 'application/octet-stream'})
    return build_opener(GitHubRedirect()).open(request,timeout=25)


def release_info(data):
    if not isinstance(data,dict) or data.get('draft') or data.get('prerelease'):
        raise ValueError('当前发布不是可用的正式版本。')
    tag=data.get('tag_name','')
    version(tag)
    if tag!='v'+tag.lstrip('v'):
        raise ValueError('正式更新须使用 v 开头的版本标签。')
    page=RELEASES+'/tag/'+tag
    if data.get('html_url')!=page:
        raise ValueError('版本来源与本软件仓库不一致。')
    name='YanxuResearchStudio-Windows-'+tag+'.zip'
    assets=[a for a in data.get('assets',[]) if isinstance(a,dict) and a.get('name')==name and a.get('state')=='uploaded']
    if len(assets)!=1:
        raise ValueError('新版的 Windows 下载包尚未准备好，请稍后再检查。')
    asset=assets[0]
    url=RELEASES+'/download/'+tag+'/'+name
    if asset.get('browser_download_url')!=url:
        raise ValueError('更新包地址与版本不一致。')
    digest=asset.get('digest','')
    size=asset.get('size',0)
    if not re.fullmatch(r'sha256:[a-fA-F0-9]{64}',digest):
        raise ValueError('此版本尚未提供 SHA-256 校验，暂不能在软件内安装。')
    if type(size)!=int or not 0<size<=MAX_ARCHIVE:
        raise ValueError('更新包大小超出允许范围。')
    return dict(version=tag[1:],tag=tag,url=url,page=page,name=name,size=size,
                sha256=digest.split(':')[1].lower(),notes=str(data.get('body') or '')[:12000])


def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path,data):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    os.replace(temporary,path)


def extract_executable(archive,target):
    with zipfile.ZipFile(archive) as z:
        candidates=[]
        for item in z.infolist():
            parts=PurePosixPath(item.filename).parts
            if '\\' in item.filename or item.filename.startswith('/') or any(p in ('.','..') or ':' in p for p in parts):
                raise ValueError('更新包包含无效路径。')
            if parts and parts[-1]=='YanxuResearchStudio.exe':
                candidates.append(item)
        if len(candidates)!=1 or not 0<candidates[0].file_size<=MAX_EXECUTABLE:
            raise ValueError('更新包中的程序文件无效。')
        item=candidates[0]
        count=0
        with z.open(item) as source,Path(target).open('wb') as dest:
            while chunk:=source.read(1024*1024):
                count+=len(chunk)
                if count>MAX_EXECUTABLE:
                    raise ValueError('解压后的程序过大。')
                dest.write(chunk)
        with Path(target).open('rb') as stream:
            if stream.read(2)!=b'MZ':
                raise ValueError('更新包不是有效的 Windows 程序。')


class UpdateManager:
    def __init__(self,root,current,opener=open_url):
        self.root=Path(root)
        self.current=current
        self.opener=opener
        self.lock=threading.RLock()
        self.stop=threading.Event()
        self.timer=None
        self.phase='idle'
        self.error=''
        self.release=None
        self.ready=None
        self.received=0
        self.last_checked=None
        self.auto=True
        self.last_result=None
        try:
            self.auto=json.loads((self.root/'update-settings.json').read_text(encoding='utf-8')).get('auto_check',True) is True
        except (OSError,ValueError,AttributeError):
            pass
        try:
            self.last_result=json.loads((self.root/'update-result.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):
            pass

    def status(self,installable=False):
        with self.lock:
            return dict(phase=self.phase,current_version=self.current,release=self.release,
                        auto_check=self.auto,error=self.error,received=self.received,
                        last_checked=self.last_checked,installable=installable,last_result=self.last_result,
                        can_update=bool(self.release and version(self.release['version'])>version(self.current)))

    def set_auto(self,enabled):
        if type(enabled)!=bool:
            raise ValueError('更新检查开关无效。')
        with self.lock:
            self.auto=enabled
            write_json(self.root/'update-settings.json',{'auto_check':enabled})

    def start_auto(self):
        if self.stop.is_set():
            return
        if self.auto:
            self.check()
        self.timer=threading.Timer(6*3600,self.start_auto)
        self.timer.daemon=True
        self.timer.start()

    def check(self):
        with self.lock:
            if self.phase in ('checking','downloading','installing'):
                return
            self.phase='checking'
            self.error=''
        threading.Thread(target=self._check,daemon=True).start()

    def _check(self):
        try:
            with self.opener(LATEST) as response:
                raw=response.read(2*1024*1024+1)
            if len(raw)>2*1024*1024:
                raise ValueError('版本信息过大。')
            release=release_info(json.loads(raw))
            with self.lock:
                self.release=release
                self.last_checked=time.time()
                if self.ready and self.ready['version']!=release['version']:
                    self.ready=None
                self.phase='ready' if self.ready else 'available' if version(release['version'])>version(self.current) else 'current'
        except Exception as error:
            self._fail(error,'检查更新失败，请检查网络后重试。')

    def _fail(self,error,default):
        with self.lock:
            self.phase='error'
            self.error=str(error) if isinstance(error,ValueError) else \
                'GitHub 暂时限制请求，请稍后重试。' if isinstance(error,HTTPError) and error.code in (403,429) else default

    def download(self):
        with self.lock:
            if self.phase in ('checking','downloading','installing'):
                return
            if not self.release or version(self.release['version'])<=version(self.current):
                raise ValueError('请先检查可用的新版本。')
            release=dict(self.release)
            self.phase='downloading'
            self.error=''
            self.received=0
            self.ready=None
        threading.Thread(target=self._download,args=(release,),daemon=True).start()

    def _download(self,release):
        try:
            folder=self.root/'updates'/('staged-'+release['tag'])
            folder.mkdir(parents=True,exist_ok=True)
            if shutil.disk_usage(folder).free<release['size']+MAX_EXECUTABLE+30*1024*1024:
                raise ValueError('可用磁盘空间不足，请清理空间后重试。')
            archive=folder/'download.part'
            digest=hashlib.sha256()
            with self.opener(release['url']) as response,archive.open('wb') as out:
                while chunk:=response.read(256*1024):
                    if self.stop.is_set():
                        raise ValueError('下载已停止，可稍后重新下载。')
                    with self.lock:
                        self.received+=len(chunk)
                        count=self.received
                    if count>release['size'] or count>MAX_ARCHIVE:
                        raise ValueError('更新包大小与发布信息不一致。')
                    digest.update(chunk)
                    out.write(chunk)
            if self.received!=release['size'] or digest.hexdigest()!=release['sha256']:
                raise ValueError('更新包校验失败，未修改当前程序，请重新下载。')
            target=folder/'YanxuResearchStudio.exe'
            extract_executable(archive,target)
            with self.lock:
                self.ready=dict(path=str(target),sha256=file_hash(target),version=release['version'])
                self.phase='ready'
        except Exception as error:
            self._fail(error,'更新下载失败，请检查网络后重试。')

    def prepare_install(self,target,wait_pids,restart_args):
        with self.lock:
            if self.phase!='ready' or not self.ready:
                raise ValueError('请先下载并校验更新包。')
            if Path(target).name!='YanxuResearchStudio.exe':
                raise ValueError('请使用正式命名的 Windows 程序进行更新。')
            staged=Path(self.ready['path']).resolve()
            updates=(self.root/'updates').resolve()
            if not staged.is_relative_to(updates) or file_hash(staged)!=self.ready['sha256']:
                raise ValueError('待安装程序的校验失败，请重新下载。')
            target=Path(target).resolve(strict=True)
            probe=target.parent/('.yanxu-write-'+uuid.uuid4().hex)
            try:
                probe.write_bytes(b'update permission check')
                probe.unlink()
            except OSError:
                raise ValueError('程序目录不可写，请把软件解压到你可写的文件夹后重试。') from None
            folder=updates/('install-'+uuid.uuid4().hex)
            folder.mkdir()
            runner=folder/'update-runner.exe'
            shutil.copy2(target,runner)
            plan=dict(target=str(target),source=str(staged),sha256=self.ready['sha256'],
                version=self.ready['version'],wait_pids=wait_pids,restart_args=restart_args)
            path=folder/'plan.json'
            write_json(path,plan)
            self.phase='installing'
            return runner,path

    def close(self):
        self.stop.set()
        if self.timer:
            self.timer.cancel()


def reset_environment():
    return {**os.environ,'PYINSTALLER_RESET_ENVIRONMENT':'1'}


def wait_for_process(pid,timeout=45):
    if os.name!='nt' or not pid or pid==os.getpid():
        return
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x100000,False,pid)
    if handle:
        try:
            if kernel.WaitForSingleObject(handle,int(timeout*1000))!=0:
                raise ValueError('旧程序尚未退出，本次没有替换程序。')
        finally:
            kernel.CloseHandle(handle)


def apply_update(plan_path,root,launch=None,wait=wait_for_process):
    """Runs only in a separate copied helper; rollback before relaunch on failure."""
    root=Path(root).resolve()
    path=Path(plan_path).resolve(strict=True)
    updates=root/'updates'
    if not path.is_relative_to(updates) or path.name!='plan.json':
        raise ValueError('无效的安装计划位置。')
    plan=json.loads(path.read_text(encoding='utf-8'))
    source=Path(plan['source']).resolve(strict=True)
    target=Path(plan['target']).resolve(strict=True)
    if not source.is_relative_to(updates) or target.name!='YanxuResearchStudio.exe' or target.is_relative_to(path.parent):
        raise ValueError('安装计划中的程序位置无效。')
    version(plan['version'])
    if plan['restart_args'] not in ([],) and not (len(plan['restart_args'])==2 and plan['restart_args'][0]=='--port' and str(plan['restart_args'][1]).isdigit() and 0<=int(plan['restart_args'][1])<=65535):
        raise ValueError('重启参数无效。')
    launch=launch or (lambda exe,args:subprocess.Popen([str(exe),*args],cwd=exe.parent,
                        env=reset_environment(),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)))
    backup=path.parent/'previous.exe'
    temporary=target.with_name('.'+target.name+'.'+uuid.uuid4().hex+'.new')
    replaced=False
    try:
        for pid in plan['wait_pids']:
            wait(int(pid))
        if file_hash(source)!=plan['sha256']:
            raise ValueError('程序校验失败，旧程序保持不变。')
        shutil.copy2(target,backup)
        shutil.copy2(source,temporary)
        if file_hash(temporary)!=plan['sha256']:
            raise ValueError('复制后的程序校验失败。')
        os.replace(temporary,target)
        replaced=True
        result=dict(ok=True,version=plan['version'],message='已安装更新。旧程序备份保存在本机 updates 文件夹。')
        write_json(root/'update-result.json',result)
        launch(target,plan['restart_args'])
        return result
    except Exception:
        if replaced:
            shutil.copy2(backup,temporary)
            os.replace(temporary,target)
        result=dict(ok=False,version=plan['version'],message='更新未完成，已保留或恢复原程序。请稍后重试。')
        write_json(root/'update-result.json',result)
        if target.exists():
            try:
                launch(target,plan['restart_args'])
            except OSError:
                pass
        return result
