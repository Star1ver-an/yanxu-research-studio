"""Pin and bundle upstream skill sources, without installing or executing them."""
from pathlib import Path, PurePosixPath
import hashlib
import io
import json
import re
import sys
import tempfile
from urllib.request import Request, urlopen
import zipfile

ROOT=Path(__file__).resolve().parent.parent
REPO='K-Dense-AI/scientific-agent-skills'


def fetch(url):
    with urlopen(Request(url,headers={'User-Agent':'Yanxu-Source-Packager/0.2'}),timeout=60) as response:
        return response.read(80*1024*1024)


def main():
    commit=sys.argv[1] if len(sys.argv)>1 else json.loads(fetch('https://api.github.com/repos/'+REPO+'/commits/main'))['sha']
    if not re.fullmatch(r'[0-9a-f]{40}',commit):
        raise ValueError('A full commit SHA is required')
    print('Pinned source '+commit,flush=True)
    archive_file=tempfile.TemporaryFile()
    archive_hash=hashlib.sha256();archive_bytes=0
    with urlopen(Request('https://codeload.github.com/'+REPO+'/zip/'+commit,
                         headers={'User-Agent':'Yanxu-Source-Packager/0.2'}),timeout=60) as response:
        while chunk:=response.read(2*1024*1024):
            archive_bytes+=len(chunk)
            if archive_bytes>350*1024*1024:raise ValueError('Unexpected large archive')
            archive_file.write(chunk);archive_hash.update(chunk)
    archive_file.seek(0)
    target=ROOT/'resources'/'scientific'
    target.mkdir(parents=True,exist_ok=True)
    manifest=[];omitted=[]
    source_extensions={'.md','.py','.json','.yaml','.yml','.toml','.txt','.csv','.tsv','.gmt','.bib',
                       '.r','.sh','.js','.mjs','.cjs','.html','.svg','.tex','.bst','.cls','.sty','.c','.cpp','.h','.ipynb','.xml','.sql','.rmd',
                       '.cppipe','.fasta','.tdb','.mplstyle','.ant'}
    with zipfile.ZipFile(archive_file) as archive:
        for item in archive.infolist():
            if item.is_dir():continue
            parts=PurePosixPath(item.filename).parts[1:]
            if not parts or any(x in ('.','..') for x in parts):continue
            if parts[0]!='skills' and '/'.join(parts) not in ('LICENSE.md','README.md','plugin.json'):
                continue
            if item.file_size>5*1024*1024 or (Path(parts[-1]).suffix.lower() not in source_extensions and 'license' not in parts[-1].lower() and parts[-1]!='.gitignore'):
                omitted.append({'path':'/'.join(parts),'bytes':item.file_size,'reason':'Size or file-type packaging limit; see pinned upstream source'})
                continue
            dest=target.joinpath(*parts)
            dest.parent.mkdir(parents=True,exist_ok=True)
            content=archive.read(item)
            dest.write_bytes(content)
            manifest.append({'path':'/'.join(parts),'sha256':hashlib.sha256(content).hexdigest()})
    skills=[]
    for path in sorted((target/'skills').glob('*/SKILL.md')):
        text=path.read_text(encoding='utf-8')
        # Descriptions are read for discovery only; source remains the authority.
        fm=text.split('---',2)[1] if text.startswith('---') else ''
        desc=re.search(r'^description:\s*(.*?)(?=\n[\w-]+:|\Z)',fm,re.M|re.S)
        desc=re.sub(r'\s+',' ',desc[1].lstrip('>| \n')).strip().strip('"\'') if desc else path.parent.name
        lic=re.search(r'^license:\s*(.+)',fm,re.M)
        skills.append({'id':path.parent.name,'description':desc,'license':lic[1].strip() if lic else 'See source',
                       'has_scripts':(path.parent/'scripts').is_dir()})
    info={'repository':'https://github.com/'+REPO,'commit':commit,
          'version':json.loads((target/'plugin.json').read_text())['version'],
          'archive_sha256':archive_hash.hexdigest(),'skills':skills,'files':manifest,'omitted_reference_assets':omitted}
    (target/'manifest.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'version':info['version'],'skills':len(skills),'files':len(manifest),'bytes':archive_bytes,'omitted_reference_assets':len(omitted)}),flush=True)


if __name__=='__main__':main()
