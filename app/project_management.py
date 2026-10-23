"""Atomic project metadata operations and independent, traceable copies."""
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import uuid

from .storage import now


def lifecycle(project):
    return project.get('lifecycle','active')


def project_title(value):
    if not isinstance(value,str) or not value.strip() or len(value.strip())>140:
        raise ValueError('项目名称须为 1–140 个字符。')
    return value.strip()


class ProjectManagement:
    def _owned_folder(self,kind,identity):
        if kind not in ('materials','invocations','assistant-invocations') or not re.fullmatch(r'[0-9a-f]{32}',identity):
            raise ValueError('项目文件位置无效。')
        root=self.store.root.resolve()
        path=(root/kind/identity).resolve()
        if not path.is_relative_to(root/kind) or not path.is_relative_to(root):
            raise ValueError('项目文件位置超出工作区。')
        return path

    def _remove_owned(self,paths):
        for path in paths:
            checked=path.resolve()
            root=(self.store.root/'materials').resolve()
            if checked.parent!=root or not re.fullmatch(r'[0-9a-f]{32}',checked.name):
                raise ValueError('副本清理位置无效。')
            if checked.exists():shutil.rmtree(checked)

    def _copy_project(self,original,mode,title,created_folders):
        result=copy.deepcopy(original)
        from .studies import reset_brief
        reset_brief(result)
        result.pop('study_origin',None)
        result.update(id=uuid.uuid4().hex,title=title,created=now(),updated=now(),lifecycle='active',pinned=False,
                      runs=[],confirmations=[],cursor=0,
                      copied_from={'id':original['id'],'title':original['title'],'time':now(),'mode':mode})
        result.pop('trash_from',None)
        result.pop('trashed_at',None)
        if mode=='materials':
            result['outputs']={}
        else:
            result['outputs']={sid:{'text':out['text'],'stale':True,'approved':False,
                                  'copied_from':original['id']} for sid,out in original['outputs'].items()}
        destination=self._owned_folder('materials',result['id'])
        if result['assets']:
            destination.mkdir(parents=True)
            created_folders.append(destination)
        original_folder=self._owned_folder('materials',original['id'])
        asset_mapping={}
        for asset in result['assets']:
            source=(self.store.root/asset['file']).resolve(strict=True)
            if not source.is_relative_to(original_folder) or not source.is_file():
                raise ValueError('材料路径不属于原项目，未创建副本。')
            identity=uuid.uuid4().hex
            asset_mapping[asset['id']]=identity
            target=destination/(identity+source.suffix)
            shutil.copy2(source,target)
            with target.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
            if asset.get('sha256') and digest!=asset['sha256']:
                raise ValueError('原始材料校验失败，未创建副本。')
            asset.update(id=identity,file=str(target.relative_to(self.store.root.resolve())),sha256=digest)
        for entry in result['literature']:entry['id']=uuid.uuid4().hex
        result.pop('versions',None)
        result.pop('assistant_chat',None)
        result.pop('assistant_followup',None)
        if 'plotting' in result:
            from .plotting import remap_plotting
            result['plotting']=remap_plotting(result['plotting'],asset_mapping)
        if 'research' in result:
            for collection,records in result['research'].items():
                if not isinstance(records,list):
                    continue
                for record in records:
                    if record.get('asset_id') in asset_mapping:
                        record['asset_id']=asset_mapping[record['asset_id']]
                    if collection=='claims':
                        record['assessment']='unknown'
                    elif collection=='figures':
                        record['status']='draft'
                    elif collection=='checklist':
                        record['status']='todo'
        return result

    def _duplicate_projects(self,projects,data,created_folders):
        mode=data.get('mode','materials')
        if mode not in ('materials','reports'):
            raise ValueError('请选择有效的副本内容。')
        used_titles={project['title'] for project in self.store.list()}
        copies=[]
        for project in projects:
            if len(projects)==1 and data.get('title') is not None:
                title=project_title(data['title'])
            else:
                base=project['title'][:126]
                title=base+' 副本'
                number=2
                while title in used_titles:
                    title=f'{base} 副本 {number}'
                    number+=1
            used_titles.add(title)
            copies.append(self._copy_project(project,mode,title,created_folders))
        return copies

    def _purge_projects(self,projects):
        # Stage only validated owned folders; roll back moves if SQLite cannot commit.
        root=self.store.root.resolve()
        staging=root/'deleted-projects'/uuid.uuid4().hex
        if not staging.resolve().is_relative_to(root/'deleted-projects'):
            raise ValueError('清理位置无效。')
        moves=[]
        try:
            for p in projects:
                if getattr(self,'plotting_busy',None) and self.plotting_busy(p['id']):
                    raise ValueError('此项目正在绘图或请求绘图 AI，请等待完成后再管理项目。')
                folders=[self._owned_folder('materials',p['id'])]
                folders += [self._owned_folder('invocations',r['id']) for r in p['runs']]
                folders += [self._owned_folder('assistant-invocations',m['turn'])
                            for m in p.get('assistant_chat',{}).get('messages',[]) if m['role']=='assistant']
                for folder in folders:
                    if folder.exists():
                        staging.mkdir(parents=True,exist_ok=True)
                        destination=staging/(folder.parent.name+'-'+folder.name)
                        folder.rename(destination);moves.append((folder,destination))
            with self.store.connect() as connection:
                connection.executemany('DELETE FROM projects WHERE id=?',[(p['id'],) for p in projects])
        except Exception:
            for folder,destination in reversed(moves):destination.rename(folder)
            raise
        warnings=[]
        if staging.exists():
            # This exact generated, resolved folder remains within this data root.
            if not staging.resolve().is_relative_to(root/'deleted-projects'):
                raise ValueError('清理位置发生变化。')
            try:shutil.rmtree(staging)
            except OSError:warnings.append('项目记录已删除；部分占用中的文件暂留在数据目录 deleted-projects 中。')
        return warnings

    def manage_projects(self,data):
        ids=data.get('ids');action=data.get('action')
        if not isinstance(ids,list) or not 1<=len(ids)<=50 or any(not isinstance(p,str) or not re.fullmatch(r'[0-9a-f]{32}',p) for p in ids) or len(set(ids))!=len(ids):
            raise ValueError('请选择 1–50 个不同的项目。')
        if action not in ('rename','duplicate','archive','trash','restore','purge','pin','unpin'):
            raise ValueError('不支持的项目管理操作。')
        with self.lock:
            if self.updating:raise ValueError('软件正在安装更新，请稍后管理项目。')
            projects=[self.store.get(pid) for pid in ids]
            for p in projects:
                if getattr(self,'plotting_busy',None) and self.plotting_busy(p['id']):
                    raise ValueError('此项目正在绘图或请求绘图 AI，请等待完成后再管理项目。')
                if getattr(self,'assistant',None) and self.assistant.busy(p['id']):
                    raise ValueError('此项目的 AI 助手正在回答，请等待或停止后再管理项目。')
                self.assert_idle(p,allow_inactive=True)
                if any(r['id'] in self.jobs for r in p['runs']):raise ValueError('项目任务正在结束，请稍后再试。')
            if action=='purge':
                if data.get('confirmation')!='永久删除' or any(lifecycle(p)!='trash' for p in projects):
                    raise ValueError('仅回收站项目可永久删除，请输入“永久删除”确认。')
                warnings=self._purge_projects(projects)
                return {'ids':ids,'created_ids':[],'action':action,'warnings':warnings,'count':len(ids)}
            if action=='restore' and any(lifecycle(p) not in ('archived','trash') for p in projects):
                raise ValueError('请选择归档或回收站中的项目进行恢复。')
            if action!='restore' and any(lifecycle(p)=='trash' for p in projects):
                raise ValueError('请先从回收站恢复项目。')
            if action=='trash' and data.get('acknowledge') is not True:
                raise ValueError('请确认将所选项目移入回收站。')
            titles=data.get('titles',{})
            if action=='rename':
                if not isinstance(titles,dict) or set(titles)!=set(ids):raise ValueError('请为每个所选项目提供新名称。')
                titles={pid:project_title(value) for pid,value in titles.items()}
            copies=[]
            created_folders=[]
            try:
                if action=='duplicate':
                    copies=self._duplicate_projects(projects,data,created_folders)
                else:
                    for project in projects:
                        if action=='rename':
                            project['title']=titles[project['id']]
                        elif action=='archive':
                            project['lifecycle']='archived'
                        elif action=='trash':
                            project.update(trash_from=lifecycle(project),lifecycle='trash',trashed_at=now())
                        elif action=='restore':
                            project['lifecycle']=project.pop('trash_from','active') if lifecycle(project)=='trash' else 'active'
                            if project['lifecycle'] not in ('active','archived'):
                                project['lifecycle']='active'
                            project.pop('trashed_at',None)
                        elif action in ('pin','unpin'):
                            project['pinned']=action=='pin'
                changed=copies if action=='duplicate' else projects
                with self.store.connect() as connection:
                    for project in changed:
                        project['updated']=now()
                        connection.execute('INSERT OR REPLACE INTO projects VALUES(?,?)',(project['id'],json.dumps(project,ensure_ascii=False)))
            except Exception:
                self._remove_owned(created_folders)
                raise
            return {'ids':ids,'created_ids':[p['id'] for p in copies],'action':action,'warnings':[],'count':len(ids)}
