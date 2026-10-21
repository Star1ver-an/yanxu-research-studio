"""Exercise assistant navigation over real HTTP using disposable projects only."""
import argparse
import json
from pathlib import Path
import sys
import threading
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.verify_analysis import Client, digest, utc


def execute(client):
    rows=[]
    for route,flow,title in [('ars','full','RAG 证据与拒答评测'),('scientific','scientific_data','边缘模型压缩评测')]:
        p=client.request('project',{'route':route,'flow':flow,'title':title+' · 助手验收'})
        pid=p['id'];before=client.request('project/'+pid)
        for target,question in [('drawing','绘图在哪里'),('analysis','数据分析在哪里'),('paper','整篇论文图表在哪里'),('experiments','实验方案在哪里')]:
            data={'project':pid,'view':'project','text':question,'request_id':uuid.uuid4().hex}
            preview=client.request('assistant/preview',data)
            assert preview['local'] and preview['calls']==0
            state=client.request('assistant/send',data)
            assert state['messages'][-1]['mode']=='local' and state['messages'][-1]['actions'][0]['id']==target
            dest=client.request('assistant/navigate',{'target':target,'project':pid})
            assert dest['project_id']==pid and dest['tab']=='research'
            if target!='experiments': assert dest['mode']=={'drawing':'draw','analysis':'analysis','paper':'paper'}[target]
        assert before==client.request('project/'+pid),'Navigation mutated research content'
        rows.append({'route':route,'project':pid,'destinations':4,'local_calls':0})
    global_data={'text':'我想绘图从哪里进入呢？','request_id':uuid.uuid4().hex}
    state=client.request('assistant/send',global_data)
    assert state['messages'][-1]['actions'][0]['project']==''
    catalog=client.request('assistant/catalog',{})
    assert len(catalog['features'])==14 and any(s['id']=='' for s in catalog['scopes'])
    assert all('topic' not in s and 'messages' not in s for s in catalog['scopes'])
    try:client.request('assistant/navigate',{'target':'https://untrusted.invalid'})
    except ValueError:pass
    else:raise AssertionError('Unknown destination accepted')
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url');parser.add_argument('--output',default='qa-runs/assistant-ux/http')
    args=parser.parse_args();output=Path(args.output).resolve()
    if not output.is_relative_to((ROOT/'qa-runs').resolve()):raise ValueError('Only qa-runs output is allowed')
    output.mkdir(parents=True,exist_ok=True);server=None
    try:
        if args.base_url:base=args.base_url
        else:
            from app.server import LocalServer
            from app.service import Service
            from app.storage import Store
            server=LocalServer(Service(Store(str(output/('workspace-'+uuid.uuid4().hex[:8])))),port=0)
            threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
        client=Client(base);boot=client.request('bootstrap')
        if not Path(boot['data_path']).resolve().is_relative_to((ROOT/'qa-runs').resolve()):raise ValueError('Only isolated QA workspaces are allowed')
        rows=execute(client)
        assert digest(boot['settings'])==digest(client.request('bootstrap')['settings'])
        report={'timestamp':utc(),'version':boot['catalog']['version'],'routes':rows,'passed':True,'paid_model_calls':0}
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))
    finally:
        if server:server.shutdown();server.server_close()


if __name__=='__main__':main()
