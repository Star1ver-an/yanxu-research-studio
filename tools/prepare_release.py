"""Create stable release asset names and checksum manifest after building."""
import argparse
import hashlib
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from app.workflows import VERSION


def prepare(tag,root=ROOT):
    if tag!='v'+VERSION:
        raise ValueError(f'Tag {tag} does not match application v{VERSION}')
    dist=root/'dist'
    files=[]
    for source,kind in [('研序-ResearchStudio-Windows.zip','Windows'),('研序-ResearchStudio-Source.zip','Source')]:
        target=dist/f'YanxuResearchStudio-{kind}-{tag}.zip'
        shutil.copy2(dist/source,target)
        files.append(target)
    sums=dist/'SHA256SUMS.txt'
    lines=[]
    for p in files:
        with p.open('rb') as stream:
            lines.append(hashlib.file_digest(stream,'sha256').hexdigest()+'  '+p.name+'\n')
    sums.write_text(''.join(lines),encoding='utf-8')
    return [*files,sums]


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('tag')
    for path in prepare(parser.parse_args().tag):
        print(path)
