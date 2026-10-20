"""Explainable, local routing. Recommendations never create or change a project."""
import re

ROUTES={
 'ars':dict(id='ars',name='开题与写作',source='Academic Research Skills',color='green',
     description='按阶段完成开题、文献整理、写作、审阅与返修。',
     suitable='已有课题，需要形成开题报告、论文或审阅意见。',
     flows=['full','literature','write','review','screen']),
 'scientific':dict(id='scientific',name='科研工具与分析',source='Scientific Agent Skills',color='blue',
     description='结合专业技能开展证据探索、假说设计与表格数据分析。',
     suitable='需要查数据库、探索机制、检查数据或选择分析方法。',
     flows=['scientific_evidence','scientific_design','scientific_data','scientific_task'])}


def recommend(data):
    title=str(data.get('title','')).strip()
    topic=str(data.get('topic','')).strip()
    if not title and not topic:raise ValueError('请先填写课题或研究目标。')
    if len(title)>140 or len(topic)>20000:raise ValueError('课题或研究目标过长。')
    goal=data.get('goal','unsure');kind=data.get('data_kind','unknown')
    if goal not in ('unsure','proposal','write','review','screen','literature','mechanism','data'):
        raise ValueError('请选择有效的研究目标。')
    if kind not in ('unknown','none','papers','table','omics','mixed'):
        raise ValueError('请选择有效的材料类型。')
    text=(title+' '+topic).lower()
    no_data=kind=='none' or (kind=='unknown' and bool(re.search(
        r'(?:尚无|暂无|没有|无|未获得|还没有).{0,8}(?:实验数据|数据|前期结果)|no (?:experimental )?data',text)))
    # Remove local negations before matching activity keywords.
    active=re.sub(r'(?:不需要|无需|不要|暂不|不做|尚未|未开展|没有).{0,4}(?:数据分析|分析数据|单细胞分析|差异分析|写论文|撰写论文)','',text)
    reasons=[];followup=[];confidence='明确'
    if goal in ('proposal','write','review','screen'):
        route='ars';flow={'proposal':'full','write':'write','review':'review','screen':'screen'}[goal]
        reasons.append({'proposal':'你当前要交付开题报告，分阶段梳理、起草和核验更合适。',
            'write':'你当前要形成论文，先确认提纲再写作和核验。',
            'review':'你明确要审阅或返修已有稿件。','screen':'你需要固定筛选协议并记录纳入、排除及分歧。'}[goal])
        if kind in ('table','omics','mixed'):
            followup.append('可先在“科研工具与分析”检查数据，再把结果作为本路径的材料。')
    elif goal=='data':
        route='scientific';flow='scientific_data'
        reasons.append('你明确需要处理或分析数据，应先检查数据结构和质量。')
        if no_data:followup.append('当前没有数据，先完成分析计划；导入真实 CSV/TSV 后才能运行表格质控。')
    elif goal=='mechanism':
        route='scientific';flow='scientific_design'
        reasons.append('你要比较候选机制与实验设计，适合先建立证据边界和可区分的预测。')
    elif goal=='literature':
        route='scientific';flow='scientific_evidence'
        reasons.append('你要检索并整合证据，可先用数据库与文献技能建立证据表。')
    elif re.search(r'完整研究流程|开题与完整研究方案|从(?:零|研究问题|开题).{0,40}(?:定稿|全过程)|full research (?:workflow|pipeline)',active):
        route='ars';flow='full'
        reasons.append('你描述的是从开题到定稿的完整流程，审阅和返修属于后续阶段。')
    elif re.search(r'审稿|返修|reviewer|rebuttal|稿件审阅',active):
        route='ars';flow='review';reasons.append('课题描述包含明确的稿件审阅或返修任务。')
    elif kind in ('table','omics') or re.search(r'分析(?:这|已有|现有)?(?:份|个)?数据|数据分析|差异表达分析|单细胞分析|scanpy|deseq|rna.?seq|csv|tsv|h5ad|富集分析',active) and not no_data:
        route='scientific';flow='scientific_data';reasons.append('材料或任务涉及数据分析，先做数据检查有助于选择后续方法。')
    elif re.search(r'开题|撰写|写论文|写作|论文初稿|proposal|manuscript|论文定稿',active):
        route='ars';flow='full' if '开题' in active or 'proposal' in active else 'write'
        reasons.append('描述中的主要交付物是开题或论文文稿。')
    elif re.search(r'数据库|公共数据|geo数据|检索|文献证据|证据图谱|pubmed|uniprot',active):
        route='scientific';flow='scientific_evidence';reasons.append('当前重点是检索来源或整理数据库证据。')
    elif no_data:
        route='ars';flow='full';reasons.append('当前没有实验数据，建议先形成可审阅的开题与研究方案。')
    else:
        route='ars';flow='full';confidence='需要补充信息'
        reasons.append('只有课题名称还不足以确定研究阶段，暂建议从研究问题与材料盘点开始。')
        followup.append('补充主要目标和材料类型后，推荐会更具体。')
    if route=='scientific' and flow=='scientific_data':
        followup.append('当前内置一键计算支持 CSV/TSV 质控与描述统计；单细胞、原始测序等专业分析需按技能准备相应环境。')
    if no_data:reasons.append('已识别“暂无数据”的约束，不预设表达变化或实验结果。')
    secondary='scientific' if route=='ars' else 'ars'
    return dict(route=route,flow=flow,confidence=confidence,reasons=reasons,notes=followup,
        alternative=dict(route=secondary,flow='scientific_design' if route=='ars' else 'full',
            reason='两条路径可按研究阶段衔接；你也可以自行选择。'),
        basis=dict(goal=goal,data_kind=kind),method='local-rules-v1')
