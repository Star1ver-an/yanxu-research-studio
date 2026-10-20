from pathlib import Path
import zipfile

root=Path(__file__).resolve().parent.parent
archive=root/'dist'/'研序-ResearchStudio-Source.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for folder in ['app','web','resources','tests','tools','.github']:
        for p in (root/folder).rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc':
                z.write(p,'academic-workbench/'+p.relative_to(root).as_posix())
    for name in ['main.py','analysis_worker_entry.py','requirements.txt','README.md','THIRD_PARTY_NOTICES.txt','LICENSE','.gitignore','.gitattributes']:
        z.write(root/name,'academic-workbench/'+name)
print(archive.name,archive.stat().st_size)
