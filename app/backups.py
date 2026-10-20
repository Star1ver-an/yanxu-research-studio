"""Portable project-only backups. Restores are validated, independent copies."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import threading
import uuid
import unicodedata
import zipfile

from .storage import now, extract_file
from .workflows import FLOWS
from .research import COLLECTIONS, ENUMS, identifier, workspace, pages, doi

MAX_BACKUP = 256 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024


class Backups:
    def __init__(self, service):
        self.service = service
        self.root = service.store.root / 'backups'
        self.root.mkdir(exist_ok=True)
        self.stop = threading.Event()
        self.timer = None
        self.worker = None
        self.error = ''

    def settings(self):
        with self.service.store.connect() as c:
            row = c.execute("SELECT body FROM preferences WHERE key='backups_v1'").fetchone()
        return json.loads(row[0]) if row else {'enabled': True, 'retain': 7}

    def configure(self, data):
        if type(data.get('enabled')) != bool or type(data.get('retain')) != int or not 3 <= data['retain'] <= 30:
            raise ValueError('请设置自动备份开关与 3–30 份保留数量。')
        settings = {'enabled': data['enabled'], 'retain': data['retain']}
        with self.service.lock, self.service.store.connect() as c:
            c.execute('INSERT OR REPLACE INTO preferences VALUES(?,?)', ('backups_v1', json.dumps(settings)))
        return self.status()

    def status(self):
        items = []
        for path in sorted(self.root.glob('*.zip'), key=lambda p: p.stat().st_mtime, reverse=True):
            if re.fullmatch(r'(?:auto|manual)-[0-9a-f]{32}\.zip', path.name):
                items.append({'id': path.name, 'size': path.stat().st_size, 'modified': path.stat().st_mtime,
                              'automatic': path.name.startswith('auto-')})
        return {'settings': self.settings(), 'items': items, 'error': self.error, 'interval_minutes': 30}

    def path(self, name):
        if not isinstance(name, str) or not re.fullmatch(r'(?:auto|manual)-[0-9a-f]{32}\.zip', name):
            raise ValueError('备份标识无效。')
        path = (self.root / name).resolve(strict=True)
        if path.parent != self.root.resolve() or path.is_symlink():
            raise ValueError('备份位置无效。')
        return path

    def create(self, automatic=False):
        service = self.service
        with service.lock:
            if service.updating or service.plotting_busy() or any(p['running'] for p in service.project_list()):
                if automatic:
                    return self.status()
                raise ValueError('请等待任务结束后再备份，以保留一致的成果。')
            projects = service.store.list()
            if automatic and not projects:
                return self.status()
            if not projects:
                raise ValueError('工作区尚无项目，请创建项目后再备份。')
            if len(projects) > 200:
                raise ValueError('整库备份最多 200 个项目，请分别导出项目备份。')
            digest = hashlib.sha256(json.dumps(projects, sort_keys=True).encode()).hexdigest()
            checkpoint = self.root / 'checkpoint.json'
            if automatic and checkpoint.exists():
                try:
                    previous = json.loads(checkpoint.read_text(encoding='utf-8'))
                    if previous['digest'] == digest and self.path(previous['id']).exists():
                        return self.status()
                except (OSError, ValueError, KeyError):
                    pass
            name = ('auto-' if automatic else 'manual-') + uuid.uuid4().hex + '.zip'
            target = self.root / name
            temporary = target.with_suffix('.tmp')
            total = 0
            try:
                with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
                    saved_analysis=set()
                    manifest=json.dumps({'format': 'yanxu-workspace-v1', 'created': now(),
                        'projects': projects, 'meaning': '项目与材料备份；不含连接设置、密钥、登录凭据或运行目录。'}, ensure_ascii=False)
                    if len(manifest.encode())>40*1024*1024:
                        raise ValueError('整库记录过大，请分别导出项目备份。')
                    archive.writestr('manifest.json',manifest)
                    for p in projects:
                        if automatic and self.stop.is_set():raise ValueError('自动备份已停止。')
                        folder = service._owned_folder('materials', p['id'])
                        for asset in p['assets']:
                            if automatic and self.stop.is_set():raise ValueError('自动备份已停止。')
                            path = (service.store.root / asset['file']).resolve(strict=True)
                            if not path.is_relative_to(folder) or not path.is_file():
                                raise ValueError('材料路径无效，未生成不完整备份。')
                            raw = path.read_bytes()
                            total += len(raw)
                            if total > MAX_EXPANDED - 40 * 1024 * 1024:
                                raise ValueError('整库材料超过备份大小上限，请分别导出项目备份。')
                            if asset.get('sha256') and hashlib.sha256(raw).hexdigest() != asset['sha256']:
                                raise ValueError('材料校验失败，请重新导入后再备份。')
                            archive.writestr('materials/' + p['id'] + '/' + path.name, raw)
                        for key,raw in service.analysis_bundle(p).items():
                            if key in saved_analysis: continue
                            total+=len(raw)
                            if total>MAX_EXPANDED-40*1024*1024: raise ValueError('整库分析结果过大，请分别导出项目备份。')
                            archive.writestr('analysis-results/'+key+'.json',raw)
                            saved_analysis.add(key)
                if temporary.stat().st_size > MAX_BACKUP:
                    raise ValueError('压缩包超过 256 MB，请分别导出项目备份。')
                temporary.replace(target)
                if automatic:
                    checkpoint.write_text(json.dumps({'digest': digest, 'id': name}), encoding='utf-8')
                    older = [x for x in self.status()['items'] if x['automatic']][self.settings()['retain']:]
                    for item in older:
                        self.path(item['id']).unlink()
                self.error = ''
            finally:
                if temporary.exists():
                    temporary.unlink()
        return self.status()

    def start(self):
        def tick():
            self.worker=threading.current_thread()
            if self.stop.is_set():
                return
            try:
                if self.settings()['enabled']:
                    self.create(automatic=True)
            except Exception as error:
                self.error = str(error) if isinstance(error, ValueError) else '自动备份未完成，请检查磁盘与文件占用。'
            if not self.stop.is_set():
                self.timer = threading.Timer(30 * 60, tick)
                self.timer.daemon = True
                self.timer.start()
        self.worker=threading.Thread(target=tick, daemon=True)
        self.worker.start()

    def close(self):
        self.stop.set()
        if self.timer:
            self.timer.cancel()
        if self.worker and self.worker is not threading.current_thread() and self.worker.is_alive():
            self.worker.join(timeout=5)

    def parse(self, raw):
        if len(raw) > MAX_BACKUP:
            raise ValueError('备份压缩包请控制在 256 MB 内。')
        try:
            archive = zipfile.ZipFile(io.BytesIO(raw))
            with archive:
                infos = archive.infolist()
                names = [i.filename for i in infos]
                if len(infos) > 17000 or len(names) != len(set(names)) or sum(i.file_size for i in infos) > MAX_EXPANDED:
                    raise ValueError('备份文件数量、重复路径或解压大小无效。')
                for info in infos:
                    path = PurePosixPath(info.filename)
                    if path.is_absolute() or '..' in path.parts or '\\' in info.filename or ':' in info.filename or (info.external_attr >> 16) & 0o170000 == 0o120000 or info.flag_bits & 1:
                        raise ValueError('备份包含无效路径、符号链接或加密条目。')
                    if info.file_size > 40 * 1024 * 1024 and info.filename.endswith('.json'):
                        raise ValueError('备份记录过大。')
                if 'manifest.json' in names:
                    manifest = json.loads(archive.read('manifest.json'))
                    if manifest.get('format') != 'yanxu-workspace-v1':
                        raise ValueError('工作区备份格式不受支持。')
                    originals = manifest['projects']
                    multi = True
                elif '项目备份.json' in names:
                    manifest = json.loads(archive.read('项目备份.json'))
                    if manifest.get('format') != 'yanxu-project-v1':
                        raise ValueError('项目备份格式不受支持。')
                    originals = [manifest['project']]
                    multi = False
                else:
                    raise ValueError('未找到研序备份清单。')
                if not isinstance(originals, list) or not 1 <= len(originals) <= 200:
                    raise ValueError('备份须包含 1–200 个项目。')
                if len({p.get('id') for p in originals if isinstance(p,dict) and isinstance(p.get('id'),str)})!=len(originals):
                    raise ValueError('备份含重复或无效的项目标识。')
                prepared = []
                for original in originals:
                    if not isinstance(original, dict) or not identifier(original.get('id')) or original.get('flow') not in FLOWS:
                        raise ValueError('备份项目标识或工作流无效。')
                    for key in ('title', 'topic', 'discipline', 'citation', 'requirements'):
                        if not isinstance(original.get(key), str) or len(original[key]) > 20000:
                            raise ValueError('备份项目概况无效。')
                    if not 1 <= len(original['title']) <= 140 or original.get('experiments') not in ('none', 'declared', 'undecided'):
                        raise ValueError('备份项目名称或来源声明无效。')
                    assets = original.get('assets')
                    outputs = original.get('outputs')
                    if not isinstance(assets, list) or len(assets) > 80 or not isinstance(outputs, dict):
                        raise ValueError('备份材料或报告格式无效。')
                    p = {key: original[key] for key in ('title', 'topic', 'discipline', 'citation', 'requirements', 'experiments', 'flow')}
                    p.update(id=uuid.uuid4().hex, route=FLOWS[p['flow']]['route'], title=(p['title'][:132] + ' 恢复副本'),
                             created=now(), updated=now(), cursor=0, runs=[], confirmations=[], outputs={}, assets=[], literature=[], demo=False,
                             lifecycle='active', pinned=False, restored_from={'id': original['id'], 'time': now(), 'meaning': '独立恢复，报告需重新确认'})
                    for key in ('goal','data_kind'):
                        value=original.get(key,'unsure' if key=='goal' else 'unknown')
                        if not isinstance(value,str) or len(value)>30:raise ValueError('备份任务目标无效。')
                        p[key]=value
                    skill = str(original.get('selected_skill', ''))
                    if skill:
                        from .scientific import skill_text
                        skill_text(skill, 0)
                        p['selected_skill'] = skill
                    elif p['flow'] == 'scientific_task':
                        raise ValueError('专题备份缺少专业技能。')
                    for sid, out in outputs.items():
                        if sid not in FLOWS[p['flow']]['stages'] or not isinstance(out, dict) or not isinstance(out.get('text'), str) or len(out['text']) > 800000:
                            raise ValueError('备份报告格式无效。')
                        p['outputs'][sid] = {'text': out['text'], 'stale': True, 'approved': False, 'restored': now()}
                    mapping = {}; files = []
                    for asset in assets:
                        if not isinstance(asset, dict) or not identifier(asset.get('id')) or asset['id'] in mapping or not isinstance(asset.get('name'), str):
                            raise ValueError('备份材料标识或名称无效。')
                        suffix = Path(asset['name']).suffix.lower()
                        filename = Path(str(asset.get('file', '')).replace('\\', '/')).name
                        if not re.fullmatch('[0-9a-f]{32}\\.[a-z0-9]+', filename):
                            raise ValueError('备份材料文件名无效。')
                        member = 'materials/' + (original['id'] + '/' if multi else '') + filename
                        if member not in names or archive.getinfo(member).file_size > 20 * 1024 * 1024:
                            raise ValueError('备份缺少材料或材料超过 20 MB。')
                        blob = archive.read(member)
                        digest = hashlib.sha256(blob).hexdigest()
                        if asset.get('sha256') and asset['sha256'] != digest:
                            raise ValueError('备份材料校验失败，未恢复任何项目。')
                        text = extract_file(asset['name'], blob)
                        aid = uuid.uuid4().hex
                        mapping[asset['id']] = aid
                        rel = str(Path('materials') / p['id'] / (aid + suffix))
                        p['assets'].append({'id': aid, 'name': Path(asset['name'].replace('\\', '/')).name[:200], 'file': rel,
                                            'text': text, 'size': len(blob), 'chars': len(text), 'sha256': digest,
                                            'enabled': bool(asset.get('enabled', True)), 'created': now()})
                        files.append((rel, blob))
                    if 'plotting' in original:
                        from .plotting import remap_plotting
                        p['plotting']=remap_plotting(original['plotting'],mapping)
                        from . import analysis
                        keys=set()
                        for figure in original['plotting']['figures']:
                            from .paper_engine import walk_specs
                            versions = []
                            for record in [figure,*figure.get('history',[])]:
                                for spec in walk_specs(record.get('spec',{})):
                                    versions.append(record if spec is record.get('spec') else {
                                        'spec': spec, 'metadata': {'analysis_key': spec.get('analysis_result',''),
                                                                 'result_sha256': spec.get('analysis_result','')}})
                            for version in versions:
                                if version.get('spec',{}).get('kind')!='analysis': continue
                                meta=version.get('metadata',{})
                                key=version['spec'].get('analysis_result') or meta.get('analysis_key','')
                                if not re.fullmatch('[0-9a-f]{64}',key): raise ValueError('分析备份缺少有效结果标识。')
                                member='analysis-results/'+key+'.json'
                                if member not in names or archive.getinfo(member).file_size>20*1024*1024: raise ValueError('备份缺少冻结的分析结果。')
                                blob=archive.read(member)
                                if hashlib.sha256(blob).hexdigest()!=meta.get('result_sha256'): raise ValueError('备份分析结果校验失败。')
                                result=json.loads(blob)
                                analysis.checked_result(result)
                                if result.get('method')!=version['spec']['analysis']['method']: raise ValueError('备份分析结果方法不匹配。')
                                if key not in keys: files.append((member,blob));keys.add(key)
                    literature = original.get('literature', [])
                    if not isinstance(literature, list) or len(literature) > 10000:
                        raise ValueError('文献记录无效。')
                    for item in literature:
                        if not isinstance(item, dict) or not isinstance(item.get('title'), str) or not isinstance(item.get('authors', []), list) or any(not isinstance(x, str) for x in item.get('authors', [])):
                            raise ValueError('文献条目无效。')
                        p['literature'].append({k: v for k, v in item.items() if k in ('title', 'authors', 'year', 'journal', 'doi', 'pmid', 'abstract', 'url', 'source')} | {'id': uuid.uuid4().hex, 'enabled': False})
                    research = original.get('research', {})
                    if not isinstance(research, dict):
                        raise ValueError('研究工具记录无效。')
                    w = workspace(p)
                    for key, fields in COLLECTIONS.items():
                        rows = research.get(key, [])
                        if not isinstance(rows, list) or len(rows) > 200:
                            raise ValueError('研究工具条目数量无效。')
                        seen = set()
                        for item in rows:
                            if not isinstance(item, dict) or not identifier(item.get('id')) or item['id'] in seen or any(not isinstance(item.get(field, ''), str) or len(item.get(field, '')) > 16000 for field in fields):
                                raise ValueError('研究工具字段无效。')
                            seen.add(item['id'])
                            row = {field: item.get(field, '') for field in fields}
                            row.update(id=item['id'], created=now(), updated=now())
                            if not row['title'] or len(row['title'])>500:raise ValueError('研究工具名称无效。')
                            for field in fields:
                                allowed=ENUMS.get((key,field))
                                if allowed:
                                    row[field]=row[field] or allowed[0]
                                    if row[field] not in allowed:raise ValueError('研究工具状态无效。')
                            if row.get('stage') and row['stage'] not in FLOWS[p['flow']]['stages']:raise ValueError('关联阶段无效。')
                            for field in ('url','source_url'):
                                if row.get(field) and not re.match(r'^https?://[^\s<>"\x00-\x1f]+$',row[field]):raise ValueError('研究工具来源链接无效。')
                            if row.get('asset_id'):
                                if row['asset_id'] not in mapping:
                                    raise ValueError('研究记录关联材料缺失。')
                                row['asset_id'] = mapping[row['asset_id']]
                                row['source_sha256'] = next(a['sha256'] for a in p['assets'] if a['id'] == row['asset_id'])
                            if key=='references':row['doi']=doi(row.get('doi',''))
                            if key=='evidence':
                                try:
                                    asset=next(a for a in p['assets'] if a['id']==row.get('asset_id'))
                                    page=int(row.get('page',''))
                                    parts=pages(asset)
                                    clean=lambda text:re.sub(r'\s+','',unicodedata.normalize('NFKC',text))
                                    if not 1<=page<=len(parts) or not row.get('quote') or clean(row['quote']) not in clean(parts[page-1]):raise ValueError()
                                    row['quote_matched']=True
                                except (ValueError,StopIteration):raise ValueError('备份证据摘录与来源页不匹配。') from None
                            if key == 'claims':
                                row['assessment'] = 'unknown'
                            if key == 'figures':
                                row['status'] = 'draft'
                            if key == 'checklist':
                                row['status'] = 'todo'
                            w[key].append(row)
                    for rows in (w[key] for key in COLLECTIONS):
                        for row in rows:
                            for field,target in {'reference_id':'references','evidence_id':'evidence','experiment_id':'experiments','depends_on':'tasks'}.items():
                                if row.get(field) and not any(item['id']==row[field] for item in w[target]):raise ValueError('备份含缺失的关联记录。')
                    tasks={task['id']:task for task in w['tasks']}
                    for task in tasks.values():
                        seen={task['id']};node=task.get('depends_on')
                        while node:
                            if node in seen:raise ValueError('备份含循环任务依赖。')
                            seen.add(node);node=tasks[node].get('depends_on')
                    if research.get('citation_style') in ('numeric', 'author-year'):
                        w['citation_style'] = research['citation_style']
                    versions = original.get('versions', [])
                    if not isinstance(versions, list) or len(versions) > 40:
                        raise ValueError('版本记录无效。')
                    p['versions'] = []
                    for version in versions:
                        if not isinstance(version, dict) or version.get('stage') not in FLOWS[p['flow']]['stages'] or not isinstance(version.get('text'), str) or len(version['text']) > 800000:
                            raise ValueError('历史报告格式无效。')
                        p['versions'].append({'id': uuid.uuid4().hex, 'stage': version['stage'], 'text': version['text'], 'created': now(), 'reason': '从备份恢复', 'sha256': hashlib.sha256(version['text'].encode()).hexdigest()})
                    memory = original.get('assistant_memory', {})
                    if not isinstance(memory, dict) or not isinstance(memory.get('items', []), list) or len(memory.get('items', [])) > 30:
                        raise ValueError('项目记忆格式无效。')
                    memory_items = []
                    for item in memory.get('items', []):
                        if (not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k].strip()
                            or len(item[k]) > limit for k, limit in (('title', 100), ('text', 4000)))
                            or type(item.get('enabled', True)) is not bool):
                            raise ValueError('项目记忆内容无效。')
                        memory_items.append({'id': uuid.uuid4().hex, 'title': item['title'], 'text': item['text'],
                                             'enabled': False, 'source': '从备份恢复，待用户核对',
                                             'created': now(), 'updated': now()})
                    if memory_items: p['assistant_memory'] = {'revision': 0, 'items': memory_items}
                    prepared.append((p, files))
                return prepared
        except ValueError:
            raise
        except Exception:
            raise ValueError('备份损坏或格式无效，未恢复任何项目。') from None

    def restore(self, raw, preview=False):
        prepared = self.parse(raw)
        if preview:
            return {'projects': [{'title': p['title'], 'assets': len(p['assets']), 'outputs': len(p['outputs'])} for p, _ in prepared],
                    'meaning': '创建独立恢复副本，不覆盖现有项目。报告需重确认，文献默认停用，论点及检查状态需复核。'}
        service = self.service
        created = []
        with service.lock:
            if service.updating or service.plotting_busy() or any(p['running'] for p in service.project_list()):
                raise ValueError('请等待任务结束或软件更新完成后恢复。')
            try:
                for p, files in prepared:
                    folder = service._owned_folder('materials', p['id'])
                    if files:
                        folder.mkdir(parents=True)
                        created.append(folder)
                    for relative, blob in files:
                        target = (service.store.root / relative).resolve()
                        if target.parent==service.analysis_cache.resolve() and re.fullmatch('[0-9a-f]{64}\\.json',target.name):
                            if target.exists() and target.read_bytes()!=blob: raise ValueError('已存在的分析结果与备份不一致，未覆盖。')
                        elif target.parent != folder:
                            raise ValueError('材料恢复位置无效。')
                        target.write_bytes(blob)
                with service.store.connect() as connection:
                    for p, _ in prepared:
                        connection.execute('INSERT INTO projects VALUES(?,?)', (p['id'], json.dumps(p, ensure_ascii=False)))
            except Exception:
                service._remove_owned(created)
                raise
        return {'created_ids': [p['id'] for p, _ in prepared], 'count': len(prepared)}
