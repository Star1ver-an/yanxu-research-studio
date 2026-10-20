"""M4 HTTP acceptance on disposable QA data; never uses AI or the user's port."""
import argparse
import base64
import copy
import io
import json
from pathlib import Path
import sys
import threading
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.verify_analysis import Client, digest, utc, validate_binary


def wait(client, pid, job):
    end = time.monotonic() + 180
    while job['status'] in {'queued', 'running', 'cancelling'}:
        if time.monotonic() > end:
            client.request(f'project/{pid}/paper-cancel', {'id': job['id']})
            raise RuntimeError('M4 QA job timed out')
        time.sleep(.15)
        job = client.request(f'project/{pid}/paper-job', {'id': job['id']})
    assert job['status'] == 'completed', job.get('error', job)
    return job


def execute(client, route, output):
    title = ('RAG 证据追溯与拒答策略' if route == 'ars' else '边缘推理模型压缩与时延评估') + ' · 合成验收'
    flow = 'write' if route == 'ars' else 'scientific_data'
    stage = 'draft' if route == 'ars' else 'kd_deliver'
    pid = client.request('project', {'title': title, 'route': route, 'flow': flow})['id']
    prefix = f'project/{pid}/'
    def call(action, data=None, **kw): return client.request(prefix+action, data, **kw)
    def upload(name, raw):
        return call('upload', {'name': name, 'data': base64.b64encode(raw).decode()})['assets'][-1]['id']
    source = b'step,error_rate,latency_ms,strategy\n1,.35,82,BM25\n3,.29,96,BM25\n5,.26,115,BM25\n1,.32,91,Dense\n3,.25,109,Dense\n5,.22,130,Dense\n'
    if route == 'scientific': source = source.replace(b'BM25', b'FP32').replace(b'Dense', b'INT8')
    aid = upload('synthetic-evaluation.csv', source)
    updated = upload('synthetic-updated.csv', source.replace(b'.35', b'.34'))
    def save(spec, **extra):
        p = call('plot-save', {'spec': spec, 'caption': '合成数据用于软件功能验收，未运行真实研究实验。', **extra})
        return next(r for r in p['plotting']['figures'] if r['id'] == extra['id']) if extra.get('id') else p['plotting']['figures'][-1]
    rows = []
    for field, label in [('error_rate', '错误率'), ('latency_ms', '推理时延')]:
        rows.append(save({'version': 1, 'kind': 'line', 'title': title+' · '+label, 'source_ids': [aid],
            'mapping': {'x': 'step', 'y': [field], 'group': 'strategy'}, 'options': {},
            'style': {'width': 6, 'height': 4, 'dpi': 90, 'font_size': 10, 'palette': 'accessible', 'xlabel': '配置级别', 'ylabel': label}}))
    draft = call('paper-compose', {'figure_ids': [r['id'] for r in rows], 'columns': 2, 'shared_legend': True, 'title': '错误率与时延 · 合成组合图'})['spec']
    assert draft['style']['height'] < 4, '组合图默认高度没有适配单行面板'
    board = save(draft)
    abstract = call('paper-abstract', {})['spec']
    abstract['title'] = title+' · 研究方案图形摘要'
    abstract['diagram']['nodes'][0]['label'] = '可靠推理与资源约束'
    abstract['diagram']['nodes'][1]['label'] = '比较基线与候选策略'
    abstract['diagram']['nodes'][2]['label'] = '独立评估错误率与时延'
    overview = save(abstract)
    state = call('paper-state', {})
    plan = state['plan']; plan['title'] = title+'论文图表交付'; plan['stages'] = [stage]
    plan['requirements'].update(formats=['png', 'svg', 'pdf', 'tiff'], min_dpi=300)
    for entry in plan['entries']:
        entry.update(caption_reviewed=True, section='评估与讨论')
        if entry['figure_id'] == overview['id']: entry['role'] = 'abstract'
    state = call('paper-save', {'plan': plan, 'expected_revision': state['revision']})
    assert len(state['plan']['entries']) == 4
    conflict = False
    try: call('paper-save', {'plan': plan, 'expected_revision': 0})
    except ValueError: conflict = True
    assert conflict, '清单并发冲突未阻止'
    config = {'action': 'style', 'figure_ids': [r['id'] for r in [*rows, board, overview]],
              'style': {'font_size': 10, 'dpi': 300, 'palette': 'muted', 'width': 7.0}}
    preview = call('paper-batch-preview', config)
    request = {**config, 'etag': preview['etag'], 'request_id': uuid.uuid4().hex}
    job = call('paper-batch-start', request)
    assert call('paper-batch-start', request)['id'] == job['id']
    wait(client, pid, job)
    p = client.request('project/'+pid)
    assert all(r['revision'] == 2 and len(r['history']) == 1 for r in p['plotting']['figures'])
    assert all(e['caption_reviewed'] for e in call('paper-state', {})['plan']['entries']), '纯样式更新不应取消图注核对'
    # A single-figure data update must mark the frozen composition for refresh.
    replacement = {'action': 'sources', 'figure_ids': [r['id'] for r in rows], 'source_map': {aid: updated}}
    preview = call('paper-batch-preview', replacement)
    wait(client, pid, call('paper-batch-start', {**replacement, 'etag': preview['etag'], 'request_id': uuid.uuid4().hex}))
    state = call('paper-state', {})
    assert board['id'] in state['audit']['affected_figures']
    refresh = {'action': 'refresh', 'figure_ids': [board['id']]}
    preview = call('paper-batch-preview', refresh)
    wait(client, pid, call('paper-batch-start', {**refresh, 'etag': preview['etag'], 'request_id': uuid.uuid4().hex}))
    md = '# '+title+'\n\n本稿仅含合成数据，用于软件验收。\n\n## 评估设置\n\n'
    md += '比较检索或压缩策略对错误率和时延的影响，不代表已完成真实实验。\n\n'
    md += '\n\n'.join('[figure:'+r['id']+']' for r in [board, *rows, overview])
    md += '\n\n再次引用组合结果：[figure:'+board['id']+']\n\n## 局限\n\n真实数据、独立运行与投稿规格仍需作者确认。'
    call('edit', {'stage': stage, 'text': md})
    state = call('paper-state', {})
    assert state['audit']['errors'] == 0
    plan = state['plan']
    for entry in plan['entries']: entry.update(caption_reviewed=True, caption_basis='')
    state = call('paper-save', {'plan': plan, 'expected_revision': state['revision']})
    state = call('paper-review', {'etag': state['etag']})
    # Saved text changes must identify linked figures and clear on explicit review.
    call('edit', {'stage': stage, 'text': md+'\n\n补充合成验收说明。'})
    changed = call('paper-state', {})
    assert set(r['id'] for r in [board, *rows, overview]) <= set(changed['audit']['affected_figures'])
    state = call('paper-review', {'etag': changed['etag']})
    request = {'etag': state['etag'], 'request_id': uuid.uuid4().hex, 'include_sources': False, 'allow_warnings': True}
    job = wait(client, pid, call('paper-export-start', request))
    raw = call('paper-download', {'id': job['id']}, raw=True)
    output.mkdir(parents=True, exist_ok=True); (output/'delivery.zip').write_bytes(raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert archive.testzip() is None
        names = archive.namelist(); manifest = json.loads(archive.read('manifest.json'))
        assert len(manifest['figures']) == 4 and not manifest['source_files_included']
        assert not any(n.startswith('sources/') for n in names)
        assert len([n for n in names if n.startswith('panels/')]) == 4
        for number in range(1, 5):
            for fmt in ['png', 'svg', 'pdf', 'tiff']: validate_binary(fmt, archive.read(f'figures/Fig{number:02}.{fmt}'))
        docx = archive.read('论文与图表.docx'); (output/'paper.docx').write_bytes(docx)
        with zipfile.ZipFile(io.BytesIO(docx)) as document:
            xml = ET.fromstring(document.read('word/document.xml'))
            text = ''.join(xml.itertext())
            assert '[figure:' not in text
            assert len(xml.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}drawing')) == 4
    return {'route': route, 'project_id': pid, 'status': 'passed', 'figures': 4,
            'delivery_bytes': len(raw), 'document': str(output/'paper.docx'), 'expected_http_rejections': 1}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url')
    parser.add_argument('--output', default=str(ROOT/'qa-runs/m4/http-acceptance'))
    args = parser.parse_args(); output = Path(args.output).resolve()
    if not output.is_relative_to((ROOT/'qa-runs').resolve()): raise ValueError('QA output must stay under qa-runs')
    output.mkdir(parents=True, exist_ok=True); server = None
    report = {'started': utc(), 'status': 'running', 'cases': []}
    try:
        if args.base_url: base = args.base_url
        else:
            from app.storage import Store
            from app.service import Service
            from app.server import LocalServer
            server = LocalServer(Service(Store(str(output/('workspace-'+uuid.uuid4().hex[:8])))), port=0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = 'http://127.0.0.1:'+str(server.server_port)
        client = Client(base); before = client.request('bootstrap')
        if not Path(before['data_path']).resolve().is_relative_to((ROOT/'qa-runs').resolve()):
            raise ValueError('Refusing to modify a non-QA workspace')
        for route in ['ars', 'scientific']:
            print('M4 '+route+' ...', flush=True)
            report['cases'].append(execute(client, route, output/route))
        after = client.request('bootstrap'); assert digest(before['settings']) == digest(after['settings'])
        assert not any(c['path'].endswith(('/plot-ai', '/run', '/settings/test')) for c in client.calls)
        report.update(status='passed', model_requests=0, http_requests=len(client.calls), settings_unchanged=True)
        return 0
    except Exception as exc:
        report.update(status='failed', error=str(exc)); raise
    finally:
        report['completed'] = utc(); (output/'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        if server: server.shutdown(); server.server_close()


if __name__ == '__main__': sys.exit(main())
