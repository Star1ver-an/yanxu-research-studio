"""Smoke-test the actual packaged EXE before a release can be published."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.analysis_runtime import hidden,stop_process


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',default=str(ROOT/'dist/YanxuResearchStudio.exe'))
    parser.add_argument('--methods',default='km,meta_forest,expression_pca,flow_gating')
    parser.add_argument('--output',default='')
    args=parser.parse_args()
    output=Path(args.output).resolve() if args.output else ROOT/'qa-runs/ci-frozen'/uuid.uuid4().hex
    if not output.is_relative_to((ROOT/'qa-runs').resolve()): raise ValueError('冻结验收仅允许写入 qa-runs。')
    output.mkdir(parents=True,exist_ok=True)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    if port==59831: raise RuntimeError('请重新运行以避开真实工作区端口。')
    env=dict(os.environ,YANXU_DATA_DIR=str(output/'workspace'),PYINSTALLER_RESET_ENVIRONMENT='1')
    process=subprocess.Popen([str(Path(args.exe).resolve()),'--server','--port',str(port)],
        cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**hidden())
    try:
        base=f'http://127.0.0.1:{port}'
        end=time.monotonic()+120
        while time.monotonic()<end:
            if process.poll() is not None: raise RuntimeError('冻结程序启动失败。')
            try:
                with urlopen(base,timeout=2) as response:
                    if response.status==200:break
            except OSError: time.sleep(.4)
        else: raise RuntimeError('冻结程序未在 120 秒内启动。')
        for asset in ['paper.js','paper.css','plotting.js','analysis.js','assistant.js','assistant.css','assistant-proposals.js','app.js','followup.js','onboarding.js','i18n.js','language.js','language.css']:
            with urlopen(base+'/'+asset,timeout=15) as response:
                if response.read() != (ROOT/'web'/asset).read_bytes():
                    raise RuntimeError('冻结界面文件与当前源码不一致：'+asset)
        with urlopen(base+'/locales.js',timeout=15) as response:
            bundle=response.read().decode('utf-8')
            assert bundle=='window.ResearchLocales='+ (ROOT/'resources/locales/messages.json').read_text(encoding='utf-8')+';', 'Locale bundle differs from source'
        command=[sys.executable,str(ROOT/'tools/verify_analysis.py'),'--base-url',base,'--bundled','--core-only',
                 '--methods',args.methods,'--output',str(output/'acceptance')]
        result=subprocess.run(command,cwd=ROOT,env=dict(os.environ,PYTHONUTF8='1'),timeout=1200,**hidden())
        if result.returncode: return result.returncode
        # Exercise paper-management endpoints and frozen plot workers before publishing.
        paper=subprocess.run([sys.executable,str(ROOT/'tools/verify_paper.py'),'--base-url',base,
            '--output',str(output/'paper-acceptance')],cwd=ROOT,env=dict(os.environ,PYTHONUTF8='1'),timeout=900,**hidden())
        if paper.returncode:return paper.returncode
        assistant=subprocess.run([sys.executable,str(ROOT/'tools/verify_assistant.py'),'--base-url',base,
            '--output',str(output/'assistant-acceptance')],cwd=ROOT,env=dict(os.environ,PYTHONUTF8='1'),timeout=120,**hidden())
        if assistant.returncode:return assistant.returncode
        return subprocess.run([sys.executable,str(ROOT/'tools/verify_i18n.py'),'--base-url',base,
            '--output',str(output/'i18n-acceptance')],cwd=ROOT,env=dict(os.environ,PYTHONUTF8='1'),timeout=120,**hidden()).returncode
    finally:
        stop_process(process)
        process.wait(timeout=30)


if __name__=='__main__':sys.exit(main())
