"""Owned, cancellable process jobs and portable frozen statistical results."""
import base64
import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import analysis
from .analysis_runtime import AnalysisRuntime, hidden, stop_process
from .plotting import digest


def cache_key(config, sources):
    config = copy.deepcopy(config)
    by_id = {s['id']: s for s in sources}
    config['inputs'] = {k: {'name': by_id[v]['name'], 'sha256': hashlib.sha256(by_id[v]['raw']).hexdigest()}
                        for k, v in config['inputs'].items()}
    return digest({'engine': analysis.VERSION, 'config': config})


def result_preview(result):
    out = {k: copy.deepcopy(v) for k,v in result.items() if k != 'tables'}
    out['tables'] = {k: {'columns': v['columns'], 'rows': v['rows'][:20], 'row_count': len(v['rows']),
                         'truncated': len(v['rows']) > 20} for k,v in result['tables'].items()}
    return out


class AnalysisJobs:
    def init_analysis(self):
        self.analysis_runtime = AnalysisRuntime(self.store.root)
        self.analysis_jobs = {}
        self.analysis_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='analysis-supervisor')
        self.analysis_closed = False
        self.analysis_cache = self.store.root / 'analysis-results'
        self.analysis_cache.mkdir(exist_ok=True)
        jobs=self.store.root/'analysis-jobs'
        if jobs.is_dir():
            for path in jobs.iterdir():
                if path.is_dir() and not path.is_symlink() and re.fullmatch('[0-9a-f]{32}',path.name) and path.resolve().parent==jobs.resolve():
                    shutil.rmtree(path)

    def analysis_busy(self, pid=None):
        return any(j['status'] in {'queued','running'} and (pid is None or j['pid']==pid)
                   for j in list(self.analysis_jobs.values()))

    def close_analysis(self):
        self.analysis_closed = True
        for job in list(self.analysis_jobs.values()):
            job['cancel'].set()
            process = job.get('process')
            if process and process.poll() is None: stop_process(process)
        self.analysis_pool.shutdown(wait=False, cancel_futures=True)
        self.analysis_runtime.close()

    def analysis_catalog(self,pid,data):
        self.store.get(pid)
        runtime = self.analysis_runtime.status()
        return {'methods': analysis.catalog(runtime['capabilities']), 'runtime': runtime}

    def _analysis_require(self,config,sources):
        capabilities=self.analysis_runtime.capabilities
        required=[]
        for field in ('independent_units','evaluation_independent','independent_replicates','normalized_log2'):
            if field in config['parameters'] and config['parameters'][field] is not True:
                raise ValueError('请先确认研究条件：'+field+'；软件不能代替作者确认。')
        if any(Path(s['name']).suffix.lower()=='.h5ad' for s in sources): required+=['anndata','h5py']
        if config['method']=='cell_embedding':
            if config['parameters'].get('embedding')=='umap': required+=['umap']
            if config['parameters'].get('cluster'): required+=['scanpy','igraph','leidenalg']
        missing=[m for m in required if not capabilities.get(m)]
        if missing: raise ValueError('当前输入或参数需要额外分析组件：'+', '.join(missing)+'。请先安装/检查分析环境。')

    def _analysis_inspect_source(self,source):
        from .plot_data import inspect_source
        from .analysis_special import inspect_special,SPECIAL_SUFFIXES
        special=Path(source['name']).suffix.lower() in SPECIAL_SUFFIXES
        if not special or not self.analysis_runtime.python:
            info=inspect_special(source['raw'],source['name']) if special else inspect_source(source['raw'],source['name'])
        else:
            folder=self.store.root/'analysis-jobs'/uuid.uuid4().hex
            folder.mkdir(parents=True)
            try:
                path=folder/(source['id']+Path(source['name']).suffix.lower());path.write_bytes(source['raw'])
                manifest=folder/'input.json';out=folder/'result.json'
                manifest.write_text(json.dumps({'version':1,'action':'inspect','sources':[{'id':source['id'],'name':source['name'],'path':str(path),'sha256':hashlib.sha256(source['raw']).hexdigest()}]}),encoding='utf-8')
                root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent.parent))
                process=subprocess.run([self.analysis_runtime.python,'-I',str(root/'analysis_worker_entry.py'),str(manifest),str(out)],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60,**hidden())
                if process.returncode or not out.is_file():
                    error=folder/'error.json'
                    raise ValueError(json.loads(error.read_text(encoding='utf-8'))['error'] if error.is_file() else '专用文件预检失败，请检查分析环境。')
                if out.stat().st_size>2*1024*1024: raise ValueError('文件结构摘要过大。')
                info=json.loads(out.read_text(encoding='utf-8'))
            finally:
                shutil.rmtree(folder)
        return {k:v for k,v in info.items() if k not in {'preview','rows','data'}}

    def analysis_inspect(self,pid,data):
        p=self.store.get(pid)
        source=self._plot_sources(p,[data.get('asset_id')])[0]
        return self._analysis_inspect_source(source)

    def _analysis_sources(self,pid,config):
        with self.lock:
            p = self.store.get(pid)
            self.assert_idle(p,allow_analysis=True)
            return self._plot_sources(p,list(dict.fromkeys(config['inputs'].values())))

    def analysis_preview(self,pid,data):
        config = analysis.validate(data.get('config'))
        sources = self._analysis_sources(pid,config)
        self._analysis_require(config,sources)
        method = next(m for m in analysis.catalog(self.analysis_runtime.capabilities) if m['id']==config['method'])
        if not method['available']: raise ValueError('此方法缺少分析组件：'+', '.join(method['missing_dependencies'])+'。请先安装或检查分析环境。')
        summaries=[]
        for source in sources:
            info = self._analysis_inspect_source(source)
            # Only structure crosses UI boundary; no event rows, coordinates or pixels.
            summaries.append({k:v for k,v in info.items() if k not in {'preview','rows','data'}} | {'id':source['id'],'name':source['name']})
        by_id={s['id']:s for s in summaries}
        for inp in method['inputs']:
            aid=config['inputs'].get(inp['key'])
            if not aid: continue
            source=next(s for s in sources if s['id']==aid)
            if Path(source['name']).suffix.lower().lstrip('.') not in inp['formats']:
                raise ValueError(inp['label']+' 的文件格式不支持，请重新选择。')
        for col in method['columns']:
            aid=config['inputs'].get(col.get('source','data'))
            known=by_id.get(aid,{}).get('columns',[])
            names={c['name']:c for c in known}
            value=config['columns'].get(col['key']);values=value if isinstance(value,list) else [value] if value else []
            if known and any(v not in names for v in values): raise ValueError('所选字段不存在：'+col['label'])
            if known and col['type']=='numeric' and any(names[v].get('type') not in {'numeric','number','integer','float'} for v in values): raise ValueError('请选择数值字段：'+col['label'])
        return {'config':config,'sources':summaries,'method':method,'warnings':method.get('limitations',[]),
                'signature':cache_key(config,sources)}

    def _analysis_load(self,key):
        if not isinstance(key,str) or not re.fullmatch('[0-9a-f]{64}',key): raise ValueError('分析结果标识无效。')
        path=(self.analysis_cache/(key+'.json')).resolve()
        if not path.is_relative_to(self.analysis_cache.resolve()) or not path.is_file():
            raise ValueError('未找到已计算的分析结果，请从“数据分析→出图”重新运行；已有图表配方已保留。')
        if path.stat().st_size>20*1024*1024: raise ValueError('分析结果文件超过限制。')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=key: raise ValueError('冻结的分析结果校验失败，请从备份恢复或重新运行。')
        result=json.loads(raw)
        analysis.checked_result(result)
        return result,hashlib.sha256(raw).hexdigest()

    def _analysis_render(self,pid,spec,sources,fmt='png'):
        key=cache_key(spec['analysis'],sources)
        if not spec.get('analysis_result'): raise ValueError('请先通过分析向导完成计算，再预览或保存冻结的分析结果。')
        result,sha=self._analysis_load(spec['analysis_result'])
        if result.get('_yanxu',{}).get('config_key')!=key: raise ValueError('分析参数与冻结结果不匹配，请重新预检运行。')
        raw=analysis.render(result,spec['analysis'],spec['style'],spec['title'],fmt)
        method=next(m for m in analysis.catalog() if m['id']==spec['analysis']['method'])
        return {'data':raw,'mime':{'png':'image/png','svg':'image/svg+xml','pdf':'application/pdf','tiff':'image/tiff'}[fmt],
                'warnings':result.get('warnings',[]),'caption':method['label']+'。'+ '；'.join(result.get('notes',[]))[:1000],
                'source_hashes':{s['id']:hashlib.sha256(s['raw']).hexdigest() for s in sources},
                'metadata':{'engine':'yanxu-analysis','engine_version':analysis.VERSION,'analysis_performed':True,
                            'model_called':False,'analysis_key':sha,'config_key':key,'result_sha256':sha,
                            'method':spec['analysis']['method'],'software':result.get('software',{})}}

    def analysis_start(self,pid,data):
        from .plot_engine import validate_spec
        spec=validate_spec(data.get('spec'))
        spec.pop('analysis_result',None)
        if spec['kind']!='analysis': raise ValueError('请先选择数据分析方法。')
        request=data.get('request_id')
        if not isinstance(request,str) or not re.fullmatch('[a-zA-Z0-9_-]{16,80}',request): raise ValueError('分析请求编号无效。')
        with self.lock:
            sources=self._analysis_sources(pid,spec['analysis'])
            self._analysis_require(spec['analysis'],sources)
            key=cache_key(spec['analysis'],sources)
            signature=data.get('signature')
            if not signature or signature!=key: raise ValueError('分析输入或参数已变化，请重新预检后再运行。')
            for j in self.analysis_jobs.values():
                if j['pid']==pid and j['request_id']==request:
                    if j['key']!=key or j['request_fingerprint']!=digest(spec): raise ValueError('此请求编号已用于不同分析。')
                    return self._analysis_public(j)
            if self.analysis_busy(pid): raise ValueError('此项目已有分析任务，请等待或取消后再运行。')
            if self.plotting_busy(pid): raise ValueError('此项目正在绘图，请等待完成。')
            if sum(j['status'] in {'queued','running'} for j in self.analysis_jobs.values())>=3: raise ValueError('分析队列已满，请稍后再试。')
            method=next(m for m in analysis.catalog(self.analysis_runtime.capabilities) if m['id']==spec['analysis']['method'])
            if not method['available']: raise ValueError('此方法缺少组件，请先安装分析环境。')
            jid=uuid.uuid4().hex
            job={'id':jid,'pid':pid,'request_id':request,'key':key,'request_fingerprint':digest(spec),'status':'queued','spec':spec,
                 'progress':'已排队，计算在本机进行','cancel':threading.Event(),'created':time.time()}
            # Bound old session receipts, retain every active job.
            completed=[k for k,j in self.analysis_jobs.items() if j['status'] not in {'running','queued'}]
            for old in completed[:-63]: self.analysis_jobs.pop(old,None)
            self.analysis_jobs[jid]=job
            self.analysis_pool.submit(self._analysis_execute,job,sources)
            return self._analysis_public(job)

    def _analysis_execute(self,job,sources):
        folder=self.store.root/'analysis-jobs'/job['id']
        try:
            if job['cancel'].is_set() or self.analysis_closed: raise ValueError('分析已取消。')
            job.update(status='running',progress='正在准备独立计算进程')
            folder.mkdir(parents=True,exist_ok=False)
            rows=[]
            for s in sources:
                path=folder/(s['id']+Path(s['name']).suffix.lower())
                path.write_bytes(s['raw'])
                rows.append({'id':s['id'],'name':s['name'],'path':str(path),'sha256':hashlib.sha256(s['raw']).hexdigest()})
            manifest=folder/'input.json'; output=folder/'result.json'
            manifest.write_text(json.dumps({'version':1,'config':job['spec']['analysis'],'sources':rows},ensure_ascii=False),encoding='utf-8')
            external=self.analysis_runtime.python
            if external:
                entry=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent.parent))/'analysis_worker_entry.py'
                command=[external,'-I',str(entry),str(manifest),str(output)]
            elif getattr(sys,'frozen',False):
                command=[sys.executable,'--analysis-worker',str(manifest),str(output)]
            else:
                command=[sys.executable,'-I',str(Path(__file__).resolve().parent.parent/'analysis_worker_entry.py'),str(manifest),str(output)]
            env=dict(os.environ,PYTHONUTF8='1',PYTHONNOUSERSITE='1',OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',MKL_NUM_THREADS='2')
            if getattr(sys,'frozen',False): env['PYINSTALLER_RESET_ENVIRONMENT']='1'
            logpath=folder/'worker.log'
            with logpath.open('wb') as log, subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=log,env=env,**hidden()) as process:
                job['process']=process; started=time.monotonic()
                while process.poll() is None:
                    if job['cancel'].wait(.15) or self.analysis_closed:
                        stop_process(process);process.wait(timeout=15);raise ValueError('分析已取消。')
                    if time.monotonic()-started>1200 or logpath.stat().st_size>2*1024*1024:
                        stop_process(process);process.wait(timeout=15);raise ValueError('分析超过 20 分钟或日志上限，请缩小输入后再试。')
                    try: job['progress']=json.loads((folder/'progress.json').read_text(encoding='utf-8'))['message']
                    except (OSError,ValueError,KeyError): pass
                if process.returncode:
                    error=folder/'error.json'
                    payload=json.loads(error.read_text(encoding='utf-8')) if error.is_file() else {}
                    message=payload.get('error','独立分析进程未完成，请检查分析环境。')
                    if payload.get('diagnostic'):
                        errors=self.store.root/'analysis-errors';errors.mkdir(exist_ok=True)
                        (errors/(job['id']+'.json')).write_text(json.dumps(payload['diagnostic']),encoding='utf-8')
                        for old in sorted(errors.glob('*.json'),key=lambda p:p.stat().st_mtime)[:-20]:
                            if re.fullmatch('[0-9a-f]{32}\\.json',old.name) and old.resolve().parent==errors.resolve():old.unlink()
                    raise ValueError(message)
            if job['cancel'].is_set(): raise ValueError('分析已取消。')
            if not output.is_file() or output.stat().st_size>20*1024*1024: raise ValueError('分析进程未返回有效的结果文件。')
            result=json.loads(output.read_text(encoding='utf-8'))
            result['_yanxu']={'config_key':job['key'],'engine_version':analysis.VERSION}
            raw=analysis.checked_result(result)
            if result.get('method')!=job['spec']['analysis']['method']: raise ValueError('分析返回了不匹配的方法。')
            # Compare source bytes again; refuse commit after concurrent file tampering.
            current=self._analysis_sources(job['pid'],job['spec']['analysis'])
            if cache_key(job['spec']['analysis'],current)!=job['key']: raise ValueError('计算期间来源已变化，请重新预检。')
            if sum(p.stat().st_size for p in self.analysis_cache.glob('*.json'))+len(raw)>512*1024*1024: raise ValueError('分析结果缓存已达 512 MB，请导出并清理后重试。')
            sha=hashlib.sha256(raw).hexdigest()
            target=self.analysis_cache/(sha+'.json')
            if not target.exists():
                tmp=self.analysis_cache/(sha+'.'+job['id']+'.tmp');tmp.write_bytes(raw);tmp.replace(target)
            job['spec']['analysis_result']=sha
            job.update(result=result_preview(result),progress='计算完成，正在绘图')
            rendered=self._analysis_render(job['pid'],job['spec'],current)
            if job['cancel'].is_set(): raise ValueError('分析已取消。')
            job.update(status='completed',progress='计算与绘图完成；保存后可继续导出和插入 Word',preview=base64.b64encode(rendered['data']).decode())
        except Exception as exc:
            job.update(status='cancelled' if job['cancel'].is_set() or self.analysis_closed else 'failed',
                       error=str(exc)[:1200] if isinstance(exc,ValueError) else '分析未完成，已有项目与图表已保留。')
        finally:
            job.pop('process',None)
            # No raw data duplicated into durable worker logs/directories.
            if folder.is_dir() and folder.resolve().parent==(self.store.root/'analysis-jobs').resolve(): shutil.rmtree(folder)

    def _analysis_public(self,job):
        return copy.deepcopy({k:v for k,v in job.items() if k in {'id','status','spec','progress','result','preview','error'}})

    def analysis_job(self,pid,data):
        self.store.get(pid)
        job=self.analysis_jobs.get(data.get('id'))
        if not job or job['pid']!=pid: raise ValueError('分析任务不存在或不属于当前项目。')
        return self._analysis_public(job)

    def analysis_cancel(self,pid,data):
        self.analysis_job(pid,data)
        self.analysis_jobs[data['id']]['cancel'].set()
        if self.analysis_jobs[data['id']]['status']=='queued':
            self.analysis_jobs[data['id']].update(status='cancelled',progress='排队任务已取消')
        return self.analysis_job(pid,data)

    def _analysis_result_for(self,pid,data):
        job=self.analysis_jobs.get(data.get('id'))
        if job and job['pid']==pid and job['status']=='completed': return self._analysis_load(job['spec']['analysis_result'])[0]
        p=self.store.get(pid);row=self._plot_row(p,data.get('id'))
        if row['spec']['kind']!='analysis': raise ValueError('此图表没有统计分析结果。')
        from .plotting import plot_stale
        if plot_stale(p,row): raise ValueError('分析来源已变化，请重新核对。')
        sources=self._plot_sources(p,row['spec']['source_ids'])
        result,sha=self._analysis_load(row['spec'].get('analysis_result') or row.get('metadata',{}).get('analysis_key'))
        if result.get('_yanxu',{}).get('config_key')!=cache_key(row['spec']['analysis'],sources): raise ValueError('分析来源或参数与冻结结果不一致。')
        if sha!=row.get('metadata',{}).get('result_sha256'): raise ValueError('保存后的分析结果校验不一致，请重新运行后核对。')
        return result

    def plot_analysis_result(self,pid,data):
        return result_preview(self._analysis_result_for(pid,data))

    def analysis_table(self,pid,data):
        result=self._analysis_result_for(pid,data)
        table=result['tables'].get(data.get('table'))
        if table is None: raise ValueError('未找到此分析结果表。')
        if data.get('format','csv')=='json': return json.dumps(table,ensure_ascii=False,allow_nan=False).encode(),'application/json; charset=utf-8'
        if data.get('format','csv')!='csv': raise ValueError('结果表格式支持 CSV / JSON。')
        output=io.StringIO(newline='');writer=csv.writer(output)
        def safe(v):
            # Prevent formulas executing when a downloaded result is opened in Excel.
            return "'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v
        writer.writerow([safe(v) for v in table['columns']])
        writer.writerows([[safe(row.get(c)) for c in table['columns']] for row in table['rows']])
        return output.getvalue().encode('utf-8-sig'),'text/csv; charset=utf-8'

    def analysis_bundle(self,p):
        """Frozen results referenced by current recipes and their undo history."""
        found={}
        for row in p.get('plotting',{}).get('figures',[]):
            for version in [row,*row.get('history',[])]:
                from .paper_engine import walk_specs
                for spec in walk_specs(version.get('spec',{})):
                    if spec.get('kind')!='analysis': continue
                    metadata=version.get('metadata',{}) if spec is version.get('spec') else {}
                    key=spec.get('analysis_result') or metadata.get('analysis_key')
                    if not key:
                        sources=self._plot_sources(p,spec['source_ids'])
                        key=cache_key(spec['analysis'],sources)
                    result,sha=self._analysis_load(key)
                    if metadata.get('result_sha256') and sha!=metadata['result_sha256']: raise ValueError('分析结果校验不一致，未生成不完整备份。')
                    found[key]=analysis.checked_result(result)
        return found

    def analysis_clean_cache(self,data=None):
        with self.lock:
            if self.analysis_busy(): raise ValueError('请等待分析完成后清理缓存。')
            protected=set()
            for project in self.store.list():
                for row in project.get('plotting',{}).get('figures',[]):
                    for version in [row,*row.get('history',[])]:
                        from .paper_engine import walk_specs
                        for spec in walk_specs(version.get('spec',{})):
                            key=spec.get('analysis_result')
                            if key: protected.add(key)
            for job in self.analysis_jobs.values():
                key=job.get('spec',{}).get('analysis_result')
                if key: protected.add(key)
            count=freed=0
            for path in self.analysis_cache.glob('*.json'):
                if path.is_symlink() or path.resolve().parent!=self.analysis_cache.resolve(): continue
                if re.fullmatch('[0-9a-f]{64}\\.json',path.name) and path.stem not in protected:
                    freed+=path.stat().st_size;path.unlink();count+=1
                elif re.fullmatch('[0-9a-f]{64}\\.ref\\.json',path.name):
                    try:
                        if json.loads(path.read_text(encoding='utf-8')).get('result') not in protected: path.unlink()
                    except (OSError,ValueError): pass
            return {'files_removed':count,'bytes_freed':freed,'protected_results':len(protected)}
