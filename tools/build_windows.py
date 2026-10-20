"""Produce a portable Windows executable and a friend-shareable archive."""
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from brand import write_assets

root=Path(__file__).resolve().parent.parent
icon=write_assets(root)
command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onefile','--windowed',
 '--name','YanxuResearchStudio','--icon',str(icon),
 '--add-data',str(root/'web')+';web', '--add-data',str(root/'resources')+';resources',
 '--collect-all','webview','--collect-all','pythonnet','--collect-all','clr_loader',
 '--collect-data','matplotlib','--hidden-import','matplotlib.backends.backend_agg',
 '--hidden-import','matplotlib.backends.backend_pdf','--hidden-import','matplotlib.backends.backend_svg',
 '--hidden-import','webview.platforms.edgechromium','--hidden-import','webview.platforms.winforms',
 '--exclude-module','PyQt5','--exclude-module','PyQt6','--exclude-module','PySide2','--exclude-module','PySide6',
 '--distpath',str(root/'dist'),'--workpath',str(root/'build'),str(root/'main.py')]
subprocess.run(command,cwd=root,check=True)
dist=root/'dist'
shutil.copy2(root/'README.md',dist/'使用说明.md')
shutil.copy2(root/'THIRD_PARTY_NOTICES.txt',dist/'来源与许可.txt')
shutil.copy2(root/'LICENSE',dist/'LICENSE.txt')
shutil.copy2(root/'resources'/'ars'/'LICENSE',dist/'ARS-LICENSE.txt')
shutil.copy2(root/'resources'/'scientific'/'LICENSE.md',dist/'Scientific-Agent-Skills-LICENSE.txt')
archive=dist/'研序-ResearchStudio-Windows.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for name in ['YanxuResearchStudio.exe','使用说明.md','来源与许可.txt','LICENSE.txt','ARS-LICENSE.txt','Scientific-Agent-Skills-LICENSE.txt']:
        z.write(dist/name,'研序 Research Studio/'+name)
print('BUILD_COMPLETE',str(archive),flush=True)
