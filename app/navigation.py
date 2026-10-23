"""Application-owned destinations shared by guidance, cards and navigation."""
import copy
import re
from .project_management import lifecycle

FEATURES = {
    'brief': dict(label='打开研究简报', name='研究简报', project=True, tab='brief'),
    'drawing': dict(label='打开绘图', name='绘图与结果重绘', project=True, tab='research', tool='figures', mode='draw'),
    'analysis': dict(label='打开数据分析', name='数据分析 → 出图', project=True, tab='research', tool='figures', mode='analysis'),
    'paper': dict(label='打开论文图表', name='整篇论文图表', project=True, tab='research', tool='figures', mode='paper'),
    'experiments': dict(label='打开实验方案', name='实验设计', project=True, tab='research', tool='experiments'),
    'work': dict(label='打开研究流程', name='研究流程', project=True, tab='work'),
    'materials': dict(label='打开文献与材料', name='文献与材料', project=True, tab='materials'),
    'research': dict(label='打开研究工具', name='研究工具', project=True, tab='research', tool='navigator'),
    'overview': dict(label='查看项目进展与记忆', name='继续研究', project=True, tab='overview'),
    'history': dict(label='查看运行记录', name='运行记录', project=True, tab='history'),
    'settings': dict(label='打开模型与连接', name='模型与连接', project=False, view='settings'),
    'backups': dict(label='打开备份与恢复', name='备份与恢复', project=False, view='backups'),
    'help': dict(label='打开使用指南', name='使用指南', project=False, view='help'),
    'pathways': dict(label='查看研究路径', name='研究路径', project=False, view='pathways'),
    'projects': dict(label='打开研究项目', name='研究项目', project=False, view='projects'),
}


def feature_for(text):
    if not isinstance(text, str): return None
    for target,pattern in [
        ('paper',r'paper figures?|manuscript figures?|論文圖表|論文図表|論文の図表|논문.*도표|논문.*그림'),
        ('analysis',r'data analysis|statistical analysis|資料分析|數據分析|データ分析|統計分析|데이터 분석|통계 분석'),
        ('drawing',r'draw|plot|create.*figure|繪圖|作圖|図を|作図|グラフ|描画|그리기|그래프|그림'),
        ('experiments',r'experiment(?:al)? (?:plan|design)|實驗方案|實驗設計|実験計画|実験設計|실험 설계|실험 계획'),
        ('settings',r'connection settings|model settings|接続設定|モデル設定|연결 설정|모델 설정'),
        ('backups',r'backup|バックアップ|백업'),('materials',r'upload|import materials|アップロード|資料の読|업로드|자료 가져'),
    ]:
        if re.search(pattern,text,re.I):return target
    for target, pattern in [
        ('brief', r'研究简报|study brief|研究簡報|研究ブリーフ|연구 개요'),
        ('paper', r'整篇.*图表|论文图表|图表交付|组合图|图形摘要'),
        ('analysis', r'数据分析|统计分析|原始数据.*出图'),
        ('drawing', r'绘图|画图|作图|图表与绘图'),
        ('experiments', r'实验方案|实验设计'), ('settings', r'模型与连接|连接设置|配置接口'),
        ('backups', r'备份|恢复备份'), ('materials', r'上传|导入材料|文献与材料'),
        ('work', r'研究流程|流程页'), ('history', r'运行记录'),
    ]:
        if re.search(pattern, text): return target
    return None


def navigation_intent(text):
    """Only explicit short entry questions bypass the model and context preview."""
    if not isinstance(text,str) or len(text)>250 or re.search(r'不要|不用|别|无需|not\b|don.t\b',text,re.I): return None
    if re.search(r"don.t|do not|without|하지 마|하지마|しない|不要|不用",text,re.I):return None
    if not re.search(r'where|how (?:do|can|to).*?(?:open|access|find)|take me|open|どこ|開く|開いて|어디|열어|哪裡|從哪|進入|開啟|打開|哪里|哪儿|入口|从哪|如何进入|怎么进入|怎么打开|如何打开|带我|跳转|打开.*(?:页|功能|绘图|分析|图表|方案|设置|备份|材料|记录)|进入.*(?:页|绘图|分析|图表|方案|设置|备份|材料|流程)',text,re.I): return None
    # Questions also asking for analysis, evaluation or authored content remain AI questions.
    if re.search(r'帮我(?:分析|设计|生成|计算|写)|如何(?:分析|设计|计算)|以及|并且|然后.*(?:分析|设计|计算)|help me (?:analy[sz]e|design|calculate|write)|and (?:analy[sz]e|calculate)',text,re.I): return None
    return feature_for(text)


def guidance(target, project=False):
    item=FEATURES[target]
    if item['project']:
        path='项目 → '+('研究工具 → '+('图表与绘图 → ' if item.get('tool')=='figures' else '')+item['name'] if item['tab']=='research' else item['name'])
        return f"{item['name']}在「{path}」。\n\n"+('点击下方按钮直接进入本项目。' if project else '选择下面的项目，即可直接进入对应功能。没有项目时可以先新建。')+'\n\n本条为本机入口指导，不调用模型。'
    return f"可以直接打开「{item['name']}」。点击下方按钮即可进入。\n\n本条为本机入口指导，不调用模型。"


def feature_catalog():
    return [{'id':key,**copy.deepcopy(value)} for key,value in FEATURES.items()]


def destination(store, data):
    key=data.get('target')
    if not isinstance(key,str) or key not in FEATURES: raise ValueError('此功能入口不可用，请重新选择。')
    item=copy.deepcopy(FEATURES[key]); pid=data.get('project','')
    if item['project']:
        if not isinstance(pid,str) or not re.fullmatch(r'[0-9a-f]{32}',pid): raise ValueError('请先选择要进入的研究项目。')
        try: p=store.get(pid)
        except (KeyError,ValueError): raise ValueError('项目已不存在，请重新选择其他项目。') from None
        state=lifecycle(p)
        if state!='active': raise ValueError('项目已归档或在回收站，请先恢复，或选择其他项目。')
        item.update(project_id=pid,project_title=p['title'])
    return {'target':key,**item}
