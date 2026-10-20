"""Explicit installation of a private scientific runtime, separate from user Python."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import threading
from urllib.request import Request, urlopen
import zipfile

MODULES = ['numpy','pandas','scipy','sklearn','statsmodels','lifelines','flowio',
           'anndata','h5py','umap','scanpy','igraph','leidenalg','pydeseq2','gseapy']
PACKAGES = ['numpy>=2,<3','pandas==2.3.3','scipy==1.16.3','scikit-learn==1.7.2',
            'statsmodels==0.14.6','lifelines==0.30.0','flowio==1.4.0','formulaic==1.2.1',
            'matplotlib>=3.9,<4','openpyxl>=3.1,<4','Pillow>=11',
            'anndata>=0.11,<0.13','h5py>=3.12,<4','umap-learn>=0.5,<0.6',
            'scanpy>=1.10,<1.12','igraph>=0.11,<2','leidenalg>=0.10,<0.12',
            'pydeseq2>=0.5,<0.6','gseapy>=1.1,<1.2']


def hidden():
    return {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}


def stop_process(process):
    """Frozen one-file workers have a bootloader child; terminate their whole tree."""
    if process.poll() is not None: return
    if os.name=='nt':
        try:
            taskkill=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/taskkill.exe'
            subprocess.run([str(taskkill),'/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,timeout=15,**hidden())
        except (OSError,subprocess.TimeoutExpired): pass
    if process.poll() is None: process.kill()


class AnalysisRuntime:
    def __init__(self, root):
        self.root = Path(root) / 'analysis-runtime'
        self.root.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.phase, self.error, self.python = 'idle', '', ''
        self.source='official'
        self.message='基础分析随软件提供；高级组件按需安装。安装只写入独立分析环境。'
        self.capabilities = {m: importlib.util.find_spec(m) is not None for m in MODULES}
        self.base_capabilities = dict(self.capabilities)
        self.process = None
        self.closed = threading.Event()
        self.thread = None
        self.settings = self.root / 'runtime.json'
        try:
            saved = json.loads(self.settings.read_text(encoding='utf-8'))
            self.source=saved.get('source','official') if saved.get('source') in {'official','china'} else 'official'
            candidate = Path(saved['python'])
            if candidate.is_file():
                self.check({'python': str(candidate)})
        except (OSError, ValueError, KeyError):
            pass

    def status(self, _=None):
        with self.lock:
            return {'phase': self.phase, 'error': self.error, 'python': self.python,
                    'capabilities': dict(self.capabilities), 'installing': self.phase == 'installing','source':self.source,
                    'private': bool(self.python and Path(self.python).is_relative_to(self.root)),
                    'message': self.message}

    def _run(self, command, timeout, log=None):
        if self.closed.is_set(): raise ValueError('分析环境操作已取消。')
        env = dict(os.environ, PYTHONUTF8='1', PYTHONNOUSERSITE='1', OMP_NUM_THREADS='2')
        with subprocess.Popen(command, stdout=log or subprocess.PIPE, stderr=log or subprocess.PIPE,
                              env=env, **hidden()) as process:
            self.process = process
            if self.closed.is_set(): stop_process(process)
            try:
                out, _ = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                stop_process(process); process.communicate()
                raise ValueError('分析环境操作超时，请检查网络后重试。') from None
            finally:
                self.process = None
            if process.returncode: raise ValueError('分析环境操作失败，请检查 Python 路径和安装日志。')
            return (out or b'').decode('utf-8', errors='replace')

    def check(self, data):
        with self.lock:
            if self.phase == 'installing': return self.status()
            value = data.get('python') or self.python
            if not value:
                self.capabilities = dict(self.base_capabilities)
                return self.status()
            path = Path(value)
            if not path.is_absolute() or not path.is_file() or path.name.lower() not in {'python.exe','python3.exe','python','python3'}:
                raise ValueError('请选择已安装的 Python 可执行文件的绝对路径。')
            script = ('import importlib,json,sys; assert (3,11)<=sys.version_info[:2]<(3,14); result={}; '
                      'exec("for m in '+repr(MODULES)+':\\n try: importlib.import_module(m); result[m]=True\\n except Exception: result[m]=False"); print(json.dumps(result))')
            result = json.loads(self._run([str(path), '-I', '-c', script], 120).strip().splitlines()[-1])
            self.python = str(path.resolve())
            self.capabilities = result
            self.error = ''
            self.settings.write_text(json.dumps({'python': self.python,'source':self.source}), encoding='utf-8')
            return self.status()

    def _download_python(self,source='official'):
        # Official CPython NuGet package; catalog SHA-512 verifies every archive byte.
        version = '3.13.15'
        # Pinned from api.nuget.org/v3/registration5-semver1/python/3.13.15.json
        # and its packageHashAlgorithm=SHA512 catalog entry. No large catalog
        # download is needed each time a friend installs the analysis runtime.
        expected='CtMWTkEpEkEtie6eip2Ckok0J4EqZ7nkPY72dmhx+qfxDcFYmeNpHBTgM2/XnaPTnqqEPqwePgVqkVGtM2usBA=='
        prefix={'official':'https://api.nuget.org/v3-flatcontainer/',
                'china':'https://repo.huaweicloud.com/artifactory/api/nuget/v3/nuget-remote/'}
        if source not in prefix: raise ValueError('请选择官方源或国内镜像。')
        url = prefix[source]+'python/'+version+'/python.'+version+'.nupkg'
        target = self.root / 'python.nupkg'
        h = hashlib.sha512(); total = 0
        with urlopen(url, timeout=60) as response, target.open('wb') as out:
            while True:
                if self.closed.is_set(): raise ValueError('安装已取消。')
                chunk = response.read1(64*1024)
                if not chunk: break
                total += len(chunk)
                if total > 50*1024*1024: raise ValueError('Python 安装包超出大小限制。')
                h.update(chunk); out.write(chunk)
                self.message=f'正在下载官方 Python：{total/1024/1024:.1f} / 13.7 MB'
        if base64.b64encode(h.digest()).decode() != expected:
            target.unlink(missing_ok=True); raise ValueError('Python 下载校验失败，请重试。')
        folder = self.root / ('python-'+version)
        folder.mkdir(exist_ok=True)
        with zipfile.ZipFile(target) as archive:
            if sum(i.file_size for i in archive.infolist()) > 200*1024*1024: raise ValueError('Python 安装包解压过大。')
            for info in archive.infolist():
                parts = PurePosixPath(info.filename).parts
                if not parts or parts[0] != 'tools' or '..' in parts or '\\' in info.filename: continue
                path = (folder / Path(*parts)).resolve()
                if not path.is_relative_to(folder.resolve()): raise ValueError('Python 安装包路径异常。')
                if info.is_dir(): path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(archive.read(info))
        target.unlink(missing_ok=True)
        return folder / 'tools' / 'python.exe'

    def install(self, data):
        with self.lock:
            if self.phase == 'installing': return self.status()
            source=data.get('source','official')
            if source not in {'official','china'}: raise ValueError('请选择官方源或国内镜像。')
            self.source=source
            self.phase, self.error = 'installing', ''
            self.message='正在准备独立分析环境；首次安装需要联网下载组件。'
            requested = data.get('python') or self.python
            def work():
                try:
                    candidate = Path(requested) if requested else (Path(sys.executable) if not getattr(sys,'frozen',False) else None)
                    if candidate is None: candidate = self._download_python(source)
                    if not candidate.is_absolute() or not candidate.is_file() or candidate.name.lower() not in {'python.exe','python3.exe','python','python3'}: raise ValueError('Python 路径无效。')
                    venv = self.root / 'venv'
                    python = venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
                    with (self.root/'install.log').open('ab') as log:
                        self._run([str(candidate),'-I','-c','import sys; assert (3,11)<=sys.version_info[:2]<(3,14), "Supported Python versions: 3.11-3.13"'],30,log)
                        if not python.is_file(): self._run([str(candidate), '-I', '-m', 'venv', str(venv)], 180, log)
                        self.message='正在安装分析组件，详情保存在 analysis-runtime/install.log；不会修改系统 Python。'
                        index=['--index-url','https://pypi.tuna.tsinghua.edu.cn/simple'] if source=='china' else []
                        command=[str(python), '-I', '-m', 'pip', 'install', '--disable-pip-version-check', '--retries','3','--timeout','45',*index,*PACKAGES]
                        try:
                            self._run(command,1800,log)
                        except ValueError:
                            if self.closed.is_set(): raise
                            self.message='组件下载暂未完成，正在重试一次；已下载内容可复用。'
                            self._run(command,1800,log)
                    with self.lock: self.phase = 'idle'
                    self.check({'python': str(python)})
                    missing=[name for name in MODULES if not self.capabilities.get(name)]
                    if missing: raise ValueError('组件安装后仍无法导入：'+', '.join(missing)+'。请查看安装日志后重试。')
                    with self.lock: self.phase = 'ready'
                    self.message='独立分析环境已安装并完成组件导入检查。'
                except Exception as exc:
                    with self.lock:
                        self.phase = 'failed'
                        self.error = str(exc) if isinstance(exc,ValueError) else '可选分析组件安装未完成，请检查网络后重试。'
            self.thread = threading.Thread(target=work, daemon=True, name='analysis-runtime')
            self.thread.start()
            return self.status()

    def close(self):
        self.closed.set()
        if self.process and self.process.poll() is None:
            stop_process(self.process)
