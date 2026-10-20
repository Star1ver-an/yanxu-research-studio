"""Small shared input helpers for controlled, local analysis kernels."""
from .plot_data import load_table


def source_table(config, sources, key='data'):
    identity = config.get('inputs', {}).get(key)
    matches = [s for s in sources if s.get('id') == identity]
    if len(matches) != 1:
        raise ValueError('分析材料缺失或重复，请重新选择：' + key)
    source = matches[0]
    return load_table(source['raw'], source['name'])
