from pathlib import Path
import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app.providers import CodexClient
from app.storage import Store
store=Store()
workdir=store.root/'connection-check'
workdir.mkdir(exist_ok=True)
with CodexClient(store.get_settings(),str(workdir)) as client:
    result=client.complete('这是研序本机连接的测试。只返回用户要求的短文本；不调用任何工具。',
                          '只回复：研序连接成功', '', lambda *args:None)
    print(result)
