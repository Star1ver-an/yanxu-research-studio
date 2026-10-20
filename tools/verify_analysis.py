"""Offline end-to-end acceptance of all M2/M3 methods, using synthetic data.

Runs the same HTTP endpoints as the desktop and the real isolated computation
workers. Never calls an AI, touches port 59831, or opens the user's workspace.

Example (from the repository):
  python tools/verify_analysis.py --python <advanced-venv-python>
  python tools/verify_analysis.py --base-url http://127.0.0.1:59857 --python <...>

An external server must report a data_path under this repository's qa-runs/.
The default starts an ephemeral localhost HTTP server in a fresh QA directory.
"""
import argparse
import base64
import copy
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import random
import re
import struct
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
QA_ROOT = ROOT / 'qa-runs'
METHODS = {'km', 'cif', 'cox', 'binary_evaluation', 'logistic_model', 'meta_forest', 'meta_funnel',
           'meta_sensitivity', 'meta_baujat', 'expression_pca', 'differential_expression', 'deseq2',
           'enrichment_ora', 'gsea_prerank', 'cell_embedding', 'cell_expression', 'cell_composition',
           'spatial_expression', 'variant_summary', 'flow_gating'}


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True).encode()).hexdigest()


def csv_bytes(rows, delimiter=','):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=list(rows[0]), delimiter=delimiter)
    writer.writeheader(); writer.writerows(rows)
    return out.getvalue().encode('utf-8')


def asset(raw, name):
    return {'raw': raw.encode() if isinstance(raw, str) else raw, 'name': name}


def case(method, columns, parameters, files, name=None):
    return {'id': name or method, 'method': method, 'columns': columns, 'parameters': parameters, 'files': files}


def tenx_zip(matrix, cells, genes):
    entries = [(g + 1, c + 1, v) for c, row in enumerate(matrix) for g, v in enumerate(row) if v]
    text = '%%MatrixMarket matrix coordinate integer general\n% synthetic acceptance only\n'
    text += f'{len(genes)} {len(cells)} {len(entries)}\n'
    text += ''.join(f'{g} {c} {value}\n' for g, c, value in entries)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('matrix.mtx', text)
        z.writestr('barcodes.tsv', '\n'.join(cells) + '\n')
        z.writestr('features.tsv', '\n'.join(f'{g}\t{g}\tGene Expression' for g in genes) + '\n')
    return out.getvalue()


def synthetic_fcs(events):
    """Write FCS 3.1 float events with explicit, known spillover metadata."""
    data = struct.pack('<' + 'f' * (len(events) * 2), *(v for row in events for v in row))
    values = {'$BEGINANALYSIS': '0', '$ENDANALYSIS': '0', '$BEGINSTEXT': '0', '$ENDSTEXT': '0',
              '$BYTEORD': '1,2,3,4', '$DATATYPE': 'F', '$MODE': 'L', '$NEXTDATA': '0',
              '$PAR': '2', '$TOT': str(len(events)), '$BEGINDATA': '0', '$ENDDATA': '0',
              '$P1B': '32', '$P1E': '0,0', '$P1G': '1', '$P1R': '262144', '$P1N': 'channel_x',
              '$P2B': '32', '$P2E': '0,0', '$P2G': '1', '$P2R': '262144', '$P2N': 'channel_y',
              '$SPILL': '2,channel_x,channel_y,1,0.1,0.2,1'}
    for _ in range(10):
        text = ('|' + '|'.join(v for pair in values.items() for v in pair) + '|').encode()
        start = 58 + len(text); end = start + len(data) - 1
        if values['$BEGINDATA'] == str(start) and values['$ENDDATA'] == str(end):
            break
        values.update({'$BEGINDATA': str(start), '$ENDDATA': str(end)})
    header = f'FCS3.1    {58:8d}{start - 1:8d}{start:8d}{end:8d}{0:8d}{0:8d}'.encode()
    assert len(header) == 58
    return header + text + data


def h5ad_fixture(python, path):
    """Fixed fixture writer, no generated analysis code or imported user content."""
    source = path.with_suffix('.csv')
    rng = random.Random(16)
    rows = [{'cell': f'cell-{i}', **{f'feature-{g}': rng.randrange(1, 30) + (i % 2) * g for g in range(10)}} for i in range(30)]
    source.write_bytes(csv_bytes(rows))
    program = (
        'import sys,pandas as pd,anndata as ad\n'
        'from scipy import sparse\n'
        'frame=pd.read_csv(sys.argv[1],index_col=0)\n'
        'data=ad.AnnData(sparse.csr_matrix(frame.to_numpy()),obs=pd.DataFrame({"known_label":["synthetic-A" if i%2 else "synthetic-B" for i in range(len(frame))]},index=frame.index),var=pd.DataFrame(index=frame.columns))\n'
        'data.write_h5ad(sys.argv[2],compression="gzip")\n')
    proc = subprocess.run([str(python), '-I', '-c', program, str(source), str(path)], stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE, timeout=120,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if proc.returncode:
        raise ValueError('无法创建合成 H5AD 验收材料；--python 需要已安装 anndata/scipy/pandas。')
    return path.read_bytes()


def fixtures(python, output, extended=True, advanced=True):
    """Every row is invented test data, not observations of actual research."""
    rng = random.Random(30)
    survival = []
    for i in range(160):
        load = rng.gauss(0, 1); group = 'accelerator' if i % 2 else 'baseline'
        failure = rng.expovariate(math.exp(.7 * load + .4 * (group == 'accelerator')) / 10)
        censor = rng.uniform(3, 20)
        survival.append({'unit': f'unit-{i}', 'time': min(failure, censor), 'event': int(failure <= censor), 'load': load, 'group': group})
    common = {'independent_units': True}
    surv_asset = asset(csv_bytes(survival), 'synthetic-device-reliability.csv')
    binary = []
    for i in range(300):
        load, memory = rng.gauss(0, 1), rng.gauss(0, 1)
        probability = 1 / (1 + math.exp(-(.5 * load - .3 * memory)))
        binary.append(dict(unit=f'inference-{i}', label=int(rng.random() < probability), probability=probability, load=load, memory=memory))
    binary_asset = asset(csv_bytes(binary), 'synthetic-ai-evaluation.csv')
    cases = [
        case('km', {'duration': 'time', 'event': 'event', 'subject_id': 'unit', 'group': 'group'}, common, {'data': surv_asset}),
        case('cif', {'duration': 'time', 'event': 'event'}, common,
             {'data': asset(csv_bytes([dict(time=i + 1, event=[1, 2, 0][i % 3]) for i in range(30)]), 'synthetic-device-competing-failure.csv')}),
        case('cox', {'duration': 'time', 'event': 'event', 'subject_id': 'unit', 'numeric_covariates': ['load']}, common, {'data': surv_asset}),
        case('binary_evaluation', {'outcome': 'label', 'probability': 'probability'},
             dict(common, evaluation_independent=True, bootstrap=100), {'data': binary_asset}),
        case('logistic_model', {'outcome': 'label', 'numeric_covariates': ['load', 'memory']},
             dict(common, bootstrap=100), {'data': binary_asset}),
    ]
    studies = [dict(study=f'Synthetic AI benchmark {i + 1}', effect=rng.gauss(.3, .2), se=rng.uniform(.08, .2)) for i in range(12)]
    for method in ['meta_forest', 'meta_funnel', 'meta_sensitivity', 'meta_baujat']:
        cases.append(case(method, {'study': 'study', 'effect': 'effect', 'standard_error': 'se'}, common,
                          {'data': asset(csv_bytes(studies, '\t'), 'synthetic-independent-benchmarks.tsv')}))
    matrix = [[rng.randrange(0, 20) for _ in range(10)] for _ in range(8)]
    cells = [f'item-{i}' for i in range(8)]; genes = [f'feature-{i}' for i in range(10)]
    cases.append(case('expression_pca', {'metadata_id': 'item', 'group': 'group'}, {'scale': True, 'log1p': True},
                      {'data': asset(tenx_zip(matrix, cells, genes), 'synthetic-features-10x.zip'),
                       'metadata': asset(csv_bytes([dict(item=x, group='group-A' if i % 2 else 'group-B') for i, x in enumerate(cells)]), 'synthetic-feature-metadata.csv')}))
    normalized = [dict(sample=f's{i}', **{f'g{g}': rng.gauss(5, 1) + (3 if i >= 3 and g < 3 else 0) for g in range(12)}) for i in range(6)]
    # XLSX is tested through the same real upload/preflight/worker path.
    import openpyxl
    book = openpyxl.Workbook(); sheet = book.active; sheet.append(list(normalized[0]))
    for row in normalized: sheet.append(list(row.values()))
    buf = io.BytesIO(); book.save(buf); book.close()
    annotation = [dict(sample=f's{i}', group='reference' if i < 3 else 'comparison', biological_unit=f'unit-{i}') for i in range(6)]
    de_cols = dict(id='sample', metadata_id='sample', group='group', replicate='biological_unit')
    de_params = dict(reference='reference', comparison='comparison', independent_replicates=True, normalized_log2=True)
    cases.append(case('differential_expression', de_cols, de_params,
                      {'data': asset(buf.getvalue(), 'synthetic-normalized-log2.xlsx'), 'metadata': asset(csv_bytes(annotation), 'synthetic-independent-units.csv')}))
    counts = []
    for i in range(8):
        record = dict(sample=f's{i}')
        for g in range(120):
            value = sum(int(math.log(max(rng.random(), 1e-15)) / math.log(.7)) for _ in range(8))
            record[f'g{g}'] = value * (5 if i >= 4 and g < 20 else 1)
        counts.append(record)
    annotation = [dict(sample=f's{i}', group='reference' if i < 4 else 'comparison', biological_unit=f'unit-{i}') for i in range(8)]
    cases.append(case('deseq2', de_cols, {k: v for k, v in de_params.items() if k != 'normalized_log2'},
                      {'data': asset(csv_bytes(counts), 'synthetic-raw-counts.csv'), 'metadata': asset(csv_bytes(annotation), 'synthetic-independent-count-units.csv')}))
    ora_sets = 'SET-A\tsynthetic-v1\tg1\tg2\tg3\tg4\nSET-B\tsynthetic-v1\tg8\tg9\tg10\nSET-C\tsynthetic-v1\tg15\tg16\n'
    cases.append(case('enrichment_ora', {'gene': 'gene', 'background_gene': 'gene'}, {'min_set_size': 2},
                      {'data': asset('gene\ng1\ng2\ng3\ng5', 'synthetic-selected-features.csv'),
                       'background': asset('gene\n' + '\n'.join(f'g{i}' for i in range(1, 21)), 'synthetic-complete-background.csv'),
                       'genesets': asset(ora_sets, 'synthetic-sets.gmt')}))
    gsea_sets = 'UP\tsynthetic-v1\t' + '\t'.join(f'g{i}' for i in range(22, 30)) + '\nDOWN\tsynthetic-v1\t' + '\t'.join(f'g{i}' for i in range(8))
    cases.append(case('gsea_prerank', {'gene': 'gene', 'rank': 'rank'}, {'min_set_size': 3, 'max_set_size': 15, 'permutations': 100, 'seed': 7},
                      {'data': asset('gene,rank\n' + '\n'.join(f'g{i},{i - 14.5}' for i in range(30)), 'synthetic-complete-ranking.csv'),
                       'genesets': asset(gsea_sets, 'synthetic-ranked-sets.gmt')}))
    if advanced:
        h5ad = h5ad_fixture(python, output / 'synthetic-cell-counts.h5ad')
        cases.append(case('cell_embedding', {'group': 'known_label'}, {'embedding': 'umap', 'cluster': True, 'n_neighbors': 5, 'seed': 23},
                          {'data': asset(h5ad, 'synthetic-cell-counts.h5ad')}))
    else:
        basic_counts = [dict(cell=f'cell-{i}', a=i + 1, b=(i * i) % 11 + 1, c=(i * 3) % 7 + 1) for i in range(30)]
        cases.append(case('cell_embedding', {'id': 'cell'}, {'embedding': 'pca'},
                          {'data': asset(csv_bytes(basic_counts), 'synthetic-bundled-cell-counts.csv')}))
        cases = [item for item in cases if item['method'] not in {'deseq2', 'gsea_prerank'}]
    expression = [dict(cell=f'cell-{i}', sample=f'sample-{i // 6}', annotation='known-A' if i % 2 else 'known-B', gene=gene, value=rng.randrange(0, 10))
                  for i in range(18) for gene in ['g1', 'g2', 'g3']]
    cases.append(case('cell_expression', {'cell_id': 'cell', 'sample': 'sample', 'cell_type': 'annotation', 'gene': 'gene', 'value': 'value'}, {},
                      {'data': asset(csv_bytes(expression), 'synthetic-annotated-expression.csv')}))
    composition = [dict(cell=f'cell-{i}', sample=f'sample-{i // 6}', annotation='known-A' if i % 2 else 'known-B') for i in range(18)]
    cases.append(case('cell_composition', {'cell_id': 'cell', 'sample': 'sample', 'cell_type': 'annotation'}, {},
                      {'data': asset(csv_bytes(composition), 'synthetic-annotated-composition.csv')}))
    spatial = [dict(spot=f'grid-{i}', x=i % 6, y=i // 6, expression=rng.random() * 5, section='synthetic-grid') for i in range(30)]
    cases.append(case('spatial_expression', {'id': 'spot', 'x': 'x', 'y': 'y', 'value': 'expression', 'section': 'section'}, {'section': 'synthetic-grid'},
                      {'data': asset(csv_bytes(spatial), 'synthetic-real-input-grid.csv')}))
    vcf = '##fileformat=VCFv4.3\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n1\t100\t.\tA\tG\t.\tPASS\t.\n1\t120\t.\tC\tT\t.\tPASS\t.\n1\t120\t.\tC\tG\t.\tPASS\t.\n2\t150\t.\tT\tA\t.\tPASS\t.\n'
    cases.append(case('variant_summary', {'chromosome': 'CHROM', 'position': 'POS'}, {'view': 'lollipop', 'chromosome': '1'}, {'data': asset(vcf, 'synthetic-existing-variants.vcf')}))
    true_events = [[rng.uniform(20, 200), rng.uniform(20, 200)] if i % 2 else [rng.uniform(700, 1000), rng.uniform(700, 1000)] for i in range(300)]
    measured = [[x + .2 * y, .1 * x + y] for x, y in true_events]
    gates = json.dumps([dict(name='synthetic-parent', type='rectangle', parent='all', bounds=[0, 300, 0, 300]),
                        dict(name='synthetic-child', type='polygon', parent='synthetic-parent', vertices=[[20, 20], [200, 20], [200, 200], [20, 200]])])
    cases.append(case('flow_gating', {'x': 'channel_x', 'y': 'channel_y'}, {'compensation': 'fcs', 'gates': gates},
                      {'data': asset(synthetic_fcs(measured), 'synthetic-events.fcs')}))
    if extended:
        cases.extend([
            case('variant_summary', {'sample': 'sample', 'gene': 'gene', 'variant_type': 'type'}, {'view': 'oncoplot'},
                 {'data': asset('sample,gene,type\nA,g1,synthetic-SNV\nB,g2,synthetic-INDEL\nC,g1,synthetic-SNV', 'synthetic-annotated-mutations.csv')}, 'variant_oncoplot'),
            case('variant_summary', {'sample': 'sample', 'chromosome': 'chr', 'position': 'start', 'end': 'end', 'value': 'ratio'}, {'view': 'cnv', 'chromosome': '1', 'sample': 'A'},
                 {'data': asset('sample,chr,start,end,ratio\nA,1,10,100,0.3\nA,1,110,200,-0.2\nB,1,10,100,0.8', 'synthetic-existing-cnv.csv')}, 'variant_cnv'),
        ])
        event_table = asset(csv_bytes([dict(channel_x=x, channel_y=y) for x, y in true_events]), 'synthetic-linear-event-table.csv')
        for view in ['density', 'histogram']:
            cases.append(case('flow_gating', {'x': 'channel_x', 'y': 'channel_y'}, {'gates': gates, 'view': view}, {'data': event_table}, 'flow_' + view))
        matrix_csv = asset(csv_bytes([dict(cell=f'cell-{i}', a=i + 1, b=(i * i) % 11 + 1, c=(i * 3) % 7 + 1) for i in range(30)]), 'synthetic-cell-features.csv')
        for embedding in ['pca', 'tsne']:
            cases.append(case('cell_embedding', {'id': 'cell'}, {'embedding': embedding, 'perplexity': 5, 'seed': 7}, {'data': matrix_csv}, 'cell_' + embedding))
    return cases


class Client:
    def __init__(self, base):
        parsed = urlparse(base)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.port == 59831 or parsed.path not in {'', '/'} or parsed.query or parsed.fragment:
            raise ValueError('验收仅允许 127.0.0.1 的独立 QA 端口；禁止使用真实用户端口 59831。')
        self.base = base.rstrip('/'); self.token = ''; self.calls = []
        with urlopen(self.base + '/', timeout=30) as response:
            root = response.read().decode('utf-8')
        match = re.search(r'<meta name="research-token" content="([^"]+)"', root)
        if not match:
            raise ValueError('未找到本地 QA 连接校验。')
        self.token = match.group(1)

    def request(self, path, payload=None, *, raw=False):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        req = Request(self.base + '/api/' + path, data=body,
                      headers={'Content-Type': 'application/json', 'X-Research-Token': self.token},
                      method='GET' if body is None else 'POST')
        started = time.monotonic()
        try:
            with urlopen(req, timeout=180) as response:
                data = response.read(); status = response.status
        except HTTPError as exc:
            data = exc.read()
            self.calls.append({'path': path, 'status': exc.code, 'seconds': round(time.monotonic() - started, 3)})
            try: message = json.loads(data).get('error', '请求失败')
            except (ValueError, UnicodeDecodeError): message = '请求失败'
            raise ValueError(path.rsplit('/', 1)[-1] + ': ' + message) from None
        self.calls.append({'path': path, 'status': status, 'seconds': round(time.monotonic() - started, 3)})
        return data if raw else json.loads(data)


def validate_binary(fmt, content):
    signatures = {'png': [b'\x89PNG\r\n\x1a\n'], 'pdf': [b'%PDF-'], 'tiff': [b'II*\x00', b'MM\x00*']}
    if fmt == 'svg':
        assert b'<svg' in content[:3000], 'SVG 内容无效'
    else:
        assert any(content.startswith(s) for s in signatures[fmt]), fmt + ' 文件头无效'
    assert len(content) > 100, fmt + ' 内容为空'


def execute_case(client, pid, item, directory, timeout):
    started = time.monotonic(); directory.mkdir(parents=True, exist_ok=True)
    inputs = {}; source_proofs = []
    for key, file in item['files'].items():
        (directory / ('input-' + key + Path(file['name']).suffix.lower())).write_bytes(file['raw'])
        project = client.request(f'project/{pid}/upload', {'name': file['name'], 'data': base64.b64encode(file['raw']).decode()})
        saved = project['assets'][-1]; inputs[key] = saved['id']
        assert saved['sha256'] == hashlib.sha256(file['raw']).hexdigest(), '上传后原始材料摘要不一致'
        source_proofs.append({'key': key, 'name': file['name'], 'sha256': saved['sha256'], 'size': len(file['raw'])})
    config = dict(method=item['method'], inputs=inputs, columns=copy.deepcopy(item['columns']), parameters=copy.deepcopy(item['parameters']))
    preflight = client.request(f'project/{pid}/analysis-preview', {'config': config})
    assert preflight['signature'] and len(preflight['sources']) == len(inputs)
    assert all('preview' not in s and 'rows' not in s for s in preflight['sources']), '预检泄露了原始行'
    config = preflight['config']
    spec = {'version': 1, 'kind': 'analysis', 'title': '合成计算机与 AI 验收 · ' + item['id'],
            'source_ids': list(dict.fromkeys(inputs.values())), 'mapping': {}, 'options': {},
            'style': {'width': 8, 'height': 6, 'dpi': 90, 'font_size': 9, 'palette': 'accessible'}, 'analysis': config}
    request = {'spec': spec, 'signature': preflight['signature'], 'request_id': 'synthetic-' + uuid.uuid4().hex}
    (directory / 'input-config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    (directory / 'preflight.json').write_text(json.dumps(preflight, ensure_ascii=False, indent=2), encoding='utf-8')
    job = client.request(f'project/{pid}/analysis-start', request)
    receipt = client.request(f'project/{pid}/analysis-start', request)
    assert receipt['id'] == job['id'], '重复点击创建了不同计算任务'
    deadline = time.monotonic() + timeout
    while job['status'] in {'queued', 'running'}:
        if time.monotonic() > deadline:
            client.request(f'project/{pid}/analysis-cancel', {'id': job['id']})
            raise ValueError('合成验收计算超时，已请求取消。')
        time.sleep(.3)
        job = client.request(f'project/{pid}/analysis-job', {'id': job['id']})
    assert job['status'] == 'completed', job.get('error') or '分析未完成'
    assert job.get('preview') and job['result']['method'] == item['method']
    validate_binary('png', base64.b64decode(job['preview']))
    public = client.request(f'project/{pid}/plot-save', {'spec': job['spec']})
    figure = public['plotting']['figures'][-1]
    assert figure['spec'].get('analysis_result') and figure['metadata']['analysis_performed'] is True
    assert figure['metadata']['model_called'] is False
    assert not figure.get('stale')
    reopened = client.request(f'project/{pid}')
    saved = next(row for row in reopened['plotting']['figures'] if row['id'] == figure['id'])
    assert saved['spec'] == figure['spec'], '重开后配方改变'
    result = client.request(f'project/{pid}/plot-analysis-result', {'id': figure['id']})
    assert result['method'] == item['method']
    required_software = {
        'km': {'lifelines', 'numpy', 'pandas'}, 'cif': {'lifelines', 'numpy', 'pandas'}, 'cox': {'lifelines', 'numpy', 'pandas'},
        'binary_evaluation': {'scikit-learn', 'numpy', 'pandas'}, 'logistic_model': {'scikit-learn', 'numpy', 'pandas'},
        'expression_pca': {'numpy', 'scipy', 'scikit-learn'}, 'differential_expression': {'numpy', 'scipy'},
        'deseq2': {'numpy', 'pydeseq2', 'pandas'}, 'enrichment_ora': {'scipy'}, 'gsea_prerank': {'gseapy', 'pandas'},
        'cell_embedding': {'numpy', 'scipy', 'scikit-learn'}, 'flow_gating': {'numpy', 'flowio'},
    }.get(item['method'], {'statsmodels', 'scipy', 'numpy', 'pandas'} if item['method'].startswith('meta_') else set())
    if item['method'] == 'cell_embedding':
        if config['parameters'].get('embedding') == 'umap': required_software.add('umap-learn')
        if config['parameters'].get('cluster'): required_software.update({'scanpy', 'anndata', 'igraph', 'leidenalg'})
    assert required_software <= set(result.get('software', {})), '计算软件版本溯源不完整：' + ', '.join(sorted(required_software - set(result.get('software', {}))))
    exports = {}
    for fmt in ['png', 'svg', 'pdf', 'tiff']:
        raw = client.request(f'project/{pid}/plot-export', {'id': figure['id'], 'format': fmt}, raw=True)
        validate_binary(fmt, raw); (directory / ('figure.' + fmt)).write_bytes(raw)
        exports[fmt] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    bundle = client.request(f'project/{pid}/plot-export', {'id': figure['id'], 'format': 'zip'}, raw=True)
    (directory / 'figure-source-and-results.zip').write_bytes(bundle)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        assert {'recipe.json', 'figure.png', 'analysis/result.json'} <= set(archive.namelist())
        recipe = json.loads(archive.read('recipe.json')); full = json.loads(archive.read('analysis/result.json'))
        assert recipe['figure']['spec'] == figure['spec']
        assert full['method'] == item['method']
        frozen_hash = hashlib.sha256(json.dumps(full, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        assert frozen_hash == figure['spec']['analysis_result'], '冻结结果摘要不匹配'
        for aid, original in zip(inputs.values(), item['files'].values()):
            name = 'sources/' + aid + Path(original['name']).suffix.lower()
            assert archive.read(name) == original['raw'], '导出后原始材料被修改'
    tables = []
    for name, tab in full['tables'].items():
        raw = client.request(f'project/{pid}/analysis-table', {'id': figure['id'], 'table': name, 'format': 'json'}, raw=True)
        assert json.loads(raw) == tab, '完整结果表与冻结结果不一致'
        safe = re.sub(r'[^A-Za-z0-9_-]', '_', name)
        (directory / ('table-' + safe + '.json')).write_bytes(raw)
        csv_raw = client.request(f'project/{pid}/analysis-table', {'id': figure['id'], 'table': name, 'format': 'csv'}, raw=True)
        decoded = list(csv.reader(io.StringIO(csv_raw.decode('utf-8-sig'))))
        assert decoded and len(decoded) == len(tab['rows']) + 1, 'CSV 完整行数不一致'
        (directory / ('table-' + safe + '.csv')).write_bytes(csv_raw)
        tables.append(dict(name=name, rows=len(tab['rows']), columns=len(tab['columns'])))
    (directory / 'recipe.json').write_text(json.dumps(figure, ensure_ascii=False, indent=2), encoding='utf-8')
    (directory / 'result.json').write_text(json.dumps(full, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
    return dict(id=item['id'], method=item['method'], status='passed', project_id=pid, figure_id=figure['id'],
                result_sha256=figure['spec']['analysis_result'], source_files=source_proofs,
                exports=exports, bundle_bytes=len(bundle), tables=tables, warnings=full.get('warnings', []),
                software=full.get('software', {}), seconds=round(time.monotonic() - started, 2))


def run(args):
    output = Path(args.output).resolve()
    if not output.is_relative_to(QA_ROOT.resolve()):
        raise ValueError('验收输出必须放在当前仓库 qa-runs 目录中。')
    output.mkdir(parents=True, exist_ok=True)
    python = Path(args.python or sys.executable).resolve()
    if not python.is_file():
        raise ValueError('--python 必须是已安装高级组件的 Python 可执行文件。')
    server = None; report = {'started': utc(), 'status': 'running', 'synthetic_only': True, 'ai_requests': 0, 'cases': []}
    report_path = output / 'acceptance.json'
    def persist():
        report_path.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
    try:
        if args.base_url:
            base = args.base_url
        else:
            sys.path.insert(0, str(ROOT))
            from app.storage import Store
            from app.service import Service
            from app.server import LocalServer
            workspace = output / ('workspace-' + uuid.uuid4().hex[:10])
            workspace.mkdir()
            server = LocalServer(Service(Store(str(workspace))), port=0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = 'http://127.0.0.1:' + str(server.server_port)
        client = Client(base)
        bootstrap = client.request('bootstrap')
        data_root = Path(bootstrap['data_path']).resolve()
        if not data_root.is_relative_to(QA_ROOT.resolve()):
            raise ValueError('目标服务器未使用独立 QA 工作区；拒绝修改现有资料。')
        report.update(base_url=base, data_path=str(data_root), fixture_python=str(python),
                      execution_mode='bundled' if args.bundled else 'private-advanced-python', settings_before=digest(bootstrap['settings']))
        persist()
        runtime = client.request('analysis/runtime', {}) if args.bundled else client.request('analysis/runtime-check', {'python': str(python)})
        if args.bundled and runtime.get('python'):
            raise ValueError('--bundled 需要未配置外部 Python 的独立服务，以便真实验证自带计算进程。')
        report['configured_runtime_python'] = runtime.get('python') or ''
        required = {'anndata', 'h5py', 'pydeseq2', 'gseapy', 'umap', 'scanpy', 'igraph', 'leidenalg'}
        missing = [m for m in required if not runtime.get('capabilities', {}).get(m)]
        if missing and not args.bundled:
            raise ValueError('完整 M3 验收需高级组件：' + ', '.join(sorted(missing)))
        report['runtime_capabilities'] = runtime['capabilities']
        source_dir = output / 'synthetic-fixtures'; source_dir.mkdir(exist_ok=True)
        all_cases = fixtures(python, source_dir, not args.core_only, not args.bundled)
        if args.methods:
            chosen = set(args.methods.split(','))
            all_cases = [item for item in all_cases if item['method'] in chosen or item['id'] in chosen]
        if not all_cases:
            raise ValueError('没有匹配的验收方法。')
        projects = []
        for route, flow in [('ars', 'full'), ('scientific', 'scientific_data')]:
            project = client.request('project', dict(title='合成计算机分析验收 · ' + route + ' · ' + utc(), topic='合成设备可靠性与 AI 评估；仅用于程序验收。',
                                                      discipline='计算机科学与人工智能', route=route, flow=flow, experiments='none'))
            projects.append(project['id'])
        report['projects'] = projects
        methods = client.request(f'project/{projects[0]}/analysis-catalog', {})['methods']
        expected = METHODS if not args.methods and not args.bundled else {x['method'] for x in all_cases}
        assert expected <= {m['id'] for m in methods}, '方法目录缺少约定方法'
        report['catalog_methods'] = len(methods); persist()
        for index, item in enumerate(all_cases):
            print('Checking ' + item['id'] + ' ...', flush=True)
            try:
                entry = execute_case(client, projects[index % 2], item, output / item['id'], args.timeout)
            except Exception as exc:
                entry = {'id': item['id'], 'method': item['method'], 'status': 'failed', 'error': str(exc)[:1800]}
            report['cases'].append(entry); persist()
            print(entry['status'] + ': ' + item['id'] + ((' - ' + entry['error']) if entry.get('error') else ''), flush=True)
        # Verify persisted project-wide export includes immutable analysis results.
        report['project_bundles'] = []
        for index, pid in enumerate(projects):
            try:
                raw = client.request(f'project/{pid}/export', {'format': 'zip'}, raw=True)
                (output / f'project-{index + 1}.zip').write_bytes(raw)
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    results = [n for n in z.namelist() if n.startswith('analysis-results/') and n.endswith('.json')]
                    saved_project = json.loads(z.read('项目备份.json'))['project']
                    expected_results = {row['spec']['analysis_result'] for row in saved_project.get('plotting', {}).get('figures', []) if row['spec']['kind'] == 'analysis'}
                    assert expected_results <= {Path(n).stem for n in results}, '项目 ZIP 缺少冻结分析结果'
                report['project_bundles'].append(dict(project_id=pid, bytes=len(raw), analysis_files=len(results), status='passed'))
            except Exception as exc:
                report['project_bundles'].append(dict(project_id=pid, status='failed', error=str(exc)))
        final = client.request('bootstrap')
        report['settings_after'] = digest(final['settings'])
        assert report['settings_before'] == report['settings_after'], '验收改变了 AI 连接设置'
        assert not any(call['path'].endswith(('/plot-ai', '/run', 'assistant/send', 'settings/test')) for call in client.calls), '验收调用了非统计计算接口'
        report['http_requests'] = len(client.calls)
        report['http_failures'] = [c for c in client.calls if c['status'] >= 400]
        report['methods_verified'] = sorted({x['method'] for x in report['cases'] if x['status'] == 'passed'})
        failed = [x for x in report['cases'] if x['status'] != 'passed'] + [x for x in report['project_bundles'] if x['status'] != 'passed']
        report['status'] = 'passed' if not failed and expected <= set(report['methods_verified']) else 'failed'
        report['completed'] = utc(); persist()
        print(json.dumps({'status': report['status'], 'cases': len(report['cases']), 'methods': len(report['methods_verified']), 'report': str(report_path)}, ensure_ascii=False), flush=True)
        return 0 if report['status'] == 'passed' else 1
    except Exception as exc:
        report.update(status='failed', error=str(exc)[:2000], completed=utc()); persist()
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if server:
            server.shutdown(); server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', help='独立 QA localhost 服务；禁止真实用户端口 59831')
    parser.add_argument('--python', help='已装全部高级分析组件的 Python 绝对路径')
    parser.add_argument('--output', default=str(QA_ROOT / 'm2-m3' / 'http-acceptance'))
    parser.add_argument('--methods', help='仅复验逗号分隔的方法 ID / case ID')
    parser.add_argument('--core-only', action='store_true', help='只运行 20 方法；默认另验变异/流式/降维视图')
    parser.add_argument('--bundled', action='store_true', help='不配置外部 Python，验证应用自带 18 个基础计算方法')
    parser.add_argument('--timeout', type=int, default=600, help='单个分析任务超时秒数')
    return run(parser.parse_args())


if __name__ == '__main__':
    raise SystemExit(main())
