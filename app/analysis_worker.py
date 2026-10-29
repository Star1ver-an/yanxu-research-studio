"""One computation per process. The application writes the manifest, not a model."""
import hashlib
import json
from pathlib import Path
import sys
import traceback


def run(manifest_path, output_path):
    from . import analysis
    manifest_path, output_path = Path(manifest_path).resolve(), Path(output_path).resolve()
    folder = manifest_path.parent
    if manifest_path.name != 'input.json' or output_path != folder / 'result.json':
        raise ValueError('分析任务路径无效。')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('version') != 1: raise ValueError('分析任务版本无效。')
    def progress(*args):
        text = ' '.join(str(x) for x in args)[:500]
        target = folder / 'progress.tmp'
        try:
            target.write_text(json.dumps({'message': text}, ensure_ascii=False), encoding='utf-8')
            target.replace(folder / 'progress.json')
        except OSError:
            # Advisory progress must never abort computation when Windows holds
            # the previous status file open (reader/indexer/antivirus).
            pass
    try:
        sources = []
        total = 0
        for row in manifest['sources']:
            path = Path(row['path']).resolve(strict=True)
            if not path.is_relative_to(folder) or path.name != row['id'] + Path(row['name']).suffix.lower():
                raise ValueError('分析输入路径越界。')
            total += path.stat().st_size
            if path.stat().st_size > 20*1024*1024 or total > 60*1024*1024: raise ValueError('分析输入过大。')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != row['sha256']: raise ValueError('分析输入校验失败。')
            sources.append({'id': row['id'], 'name': row['name'], 'raw': raw})
        progress('正在读取材料并计算；无需 AI 请求')
        if manifest.get('action')=='inspect':
            from .analysis_special import inspect_special
            value=inspect_special(sources[0]['raw'],sources[0]['name'])
            value={k:v for k,v in value.items() if k not in {'preview','rows','data'}}
            raw=json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
            if len(raw)>2*1024*1024: raise ValueError('专用文件结构摘要过大。')
            output_path.write_bytes(raw)
            return 0
        result = analysis.compute(manifest['config'], sources, progress=progress)
        raw = analysis.checked_result(result)
        temporary = folder / 'result.tmp'
        temporary.write_bytes(raw)
        temporary.replace(output_path)
        return 0
    except Exception as exc:
        # User-readable domain errors only; dependency exceptions can leak paths/data.
        message = str(exc)[:1200] if isinstance(exc, ValueError) else '计算未完成，请检查依赖、输入字段及方法适用条件。'
        diagnostic={'exception':type(exc).__name__,'frames':[{'file':Path(frame.filename).name,'line':frame.lineno,'function':frame.name}
                    for frame in traceback.extract_tb(exc.__traceback__)]}
        (folder / 'error.json').write_text(json.dumps({'error': message,'diagnostic':diagnostic}, ensure_ascii=False), encoding='utf-8')
        return 1


if __name__ == '__main__':
    sys.exit(run(*sys.argv[1:]))
