"""Offline five-language acceptance on a disposable HTTP workspace."""
import argparse
import json
from pathlib import Path
import sys
import threading
import uuid
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.verify_analysis import Client,digest,utc
from app.i18n import LANGUAGES

QUESTIONS={'zh-CN':'绘图在哪里','zh-TW':'繪圖在哪裡','en':'Where can I create a figure?','ja':'作図はどこから開く？','ko':'그래프는 어디에서 열어요?'}

def execute(client):
    boot=client.request('bootstrap');rows=[]
    if not Path(boot['data_path']).resolve().is_relative_to((ROOT/'qa-runs').resolve()):raise ValueError('Only disposable qa-runs workspaces may be tested')
    settings=digest(boot['settings']);original=boot['language']['ui_language']
    try:
        for locale in LANGUAGES:
            assert client.request('preferences/language',{'ui_language':locale})['ui_language']==locale
            for route,flow in [('ars','full'),('scientific','scientific_data')]:
                p=client.request('project',{'title':'原始中文课题 · '+locale,'topic':'Original source 研究目标','route':route,'flow':flow,'output_language':locale})
                before=client.request('project/'+p['id'])
                data={'project':p['id'],'text':QUESTIONS[locale],'request_id':uuid.uuid4().hex}
                preview=client.request('assistant/preview',data);assert preview['local'] and preview['calls']==0
                chat=client.request('assistant/send',data)
                assert chat['messages'][-1]['navigation_guide']=={'target':'drawing','project':True}
                destination=client.request('assistant/navigate',{'target':'drawing','project':p['id']})
                assert destination['project_id']==p['id'] and destination['mode']=='draw'
                after=client.request('project/'+p['id']);assert after==before,'Navigation changed source research'
                assert after['title']==p['title'] and after['output_language']==locale
                rows.append({'language':locale,'route':route,'local_navigation':True,'original_content_preserved':True})
        assert digest(client.request('bootstrap')['settings'])==settings,'Language changed model settings'
        return rows
    finally:client.request('preferences/language',{'ui_language':original})

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--base-url');parser.add_argument('--output',default='qa-runs/i18n/http')
    args=parser.parse_args();folder=Path(args.output).resolve();server=None
    if not folder.is_relative_to((ROOT/'qa-runs').resolve()):raise ValueError('Only qa-runs output is allowed')
    folder.mkdir(parents=True,exist_ok=True)
    try:
        if args.base_url:base=args.base_url
        else:
            from app.server import LocalServer
            from app.service import Service
            from app.storage import Store
            server=LocalServer(Service(Store(str(folder/('workspace-'+uuid.uuid4().hex[:8])))),port=0)
            threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
        client=Client(base);rows=execute(client)
        report={'timestamp':utc(),'version':client.request('bootstrap')['catalog']['version'],'passed':True,'paid_model_calls':0,'matrix':rows}
        (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print('Five-language HTTP acceptance passed on both routes; no paid model calls.')
    finally:
        if server:server.shutdown();server.server_close()

if __name__=='__main__':main()
