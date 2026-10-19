"""Bounded local tabular profiling; no generated code or network execution."""
import csv
import hashlib
import io
import math
import statistics

MISSING={'','na','n/a','null','none'}


def profile_csv(name,text):
    delimiter='\t' if name.lower().endswith('.tsv') else ','
    try:rows=list(csv.reader(io.StringIO(text.lstrip('\ufeff')),delimiter=delimiter,strict=True))
    except csv.Error:raise ValueError('表格格式无效，请检查引号与分隔符。') from None
    rows=[row for row in rows if any(cell.strip() for cell in row)]
    if len(rows)<2:raise ValueError('表格需要一行列名和至少一行数据。')
    header=[x.strip() for x in rows[0]]
    if not all(header) or len(set(header))!=len(header):raise ValueError('表格列名不能为空或重复，请先更正。')
    if len(header)>200 or len(rows)>50001:raise ValueError('内置表格质控支持最多 200 列、50,000 行，请拆分数据。')
    data=rows[1:]
    if any(len(row)!=len(header) for row in data):raise ValueError('表格行列数不一致，请检查缺少或多余的分隔符。')
    columns=[]
    for index,label in enumerate(header):
        values=[row[index].strip() for row in data]
        observed=[x for x in values if x.lower() not in MISSING]
        nums=[];nonnumeric=0;nonfinite=0
        for value in observed:
            try:n=float(value)
            except ValueError:nonnumeric+=1;continue
            if not math.isfinite(n):nonfinite+=1
            else:nums.append(n)
        column=dict(name=label,missing=len(values)-len(observed),observed=len(observed),
            unique=len(set(observed)),nonnumeric=nonnumeric,nonfinite=nonfinite,
            kind='numeric' if observed and not nonnumeric and not nonfinite else 'mixed' if nums else 'text')
        if column['kind']=='numeric':
            try:sd=statistics.stdev(nums) if len(nums)>1 else None
            except OverflowError:sd=None
            if sd is not None and not math.isfinite(sd):sd=None
            column.update(n=len(nums),mean=statistics.mean(nums),median=statistics.median(nums),
                minimum=min(nums),maximum=max(nums),sd=sd)
        columns.append(column)
    return dict(name=name,rows=len(data),columns=len(header),duplicate_rows=len(data)-len(set(map(tuple,data))),
        sha256=hashlib.sha256(text.encode('utf-8')).hexdigest(),fields=columns)


def table_report(assets):
    tables=[a for a in assets if a.get('enabled',True) and a['name'].lower().endswith(('.csv','.tsv'))]
    if not tables:raise ValueError('请先在“文献与材料”导入并启用真实 CSV/TSV 表格，再运行本机质控。')
    profiles=[profile_csv(a['name'],a['text']) for a in tables]
    def cell(text):return str(text).replace('|','\\|').replace('\n',' ').replace('\r',' ')
    def number(n):return '—' if n is None else format(n,'.6g')
    body=['# 本机表格数据质控','> 以下数字由应用在本机实际计算；未调用 AI 模型。未运行假设检验、差异表达或因果分析。']
    for p in profiles:
        body += ['\n## '+cell(p['name']),f"数据行：{p['rows']}；列数：{p['columns']}；完全重复的额外行：{p['duplicate_rows']}。",
            '完全重复不等于错误样本，请结合样本 ID 和采集记录判断。',
            '| 列名 | 类型 | 缺失 | 非有限数 | 非空唯一值 |','|---|---|---:|---:|---:|']
        for f in p['fields']:
            body.append(f"| {cell(f['name'])} | {dict(numeric='数值',mixed='混合',text='文本')[f['kind']]} | {f['missing']} | {f['nonfinite']} | {f['unique']} |")
        numeric=[f for f in p['fields'] if f['kind']=='numeric']
        if numeric:
            body += ['\n### 数值列描述统计','| 列名 | n | 均值 | 中位数 | 标准差 | 最小值 | 最大值 |','|---|---:|---:|---:|---:|---:|---:|']
            for f in numeric:
                body.append('| '+' | '.join([cell(f['name']),str(f['n'])]+[number(f[k]) for k in ['mean','median','sd','minimum','maximum']])+' |')
        body += ['\n输入文本 SHA-256：`'+p['sha256']+'`。']
    body += ['\n## 待研究者确认','确认样本/实验单位、列含义、分组、缺失值编码和重复记录处理。数值格式不能证明计数来源；本步骤不判断是否符合 DESeq2 输入要求。',
             '\n## 证据与局限','空白、NA、N/A、null、none 视为缺失；NaN/Inf 记为非有限数。混合列不自动计算数值统计。均值和标准差不代表组间效应或显著性；当前计算不验证数据真实性。']
    return '\n'.join(body),dict(tool='local-csv-profile-v1',status='completed',profiles=profiles,
        network=False,model_called=False)
