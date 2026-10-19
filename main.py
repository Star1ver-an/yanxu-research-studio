"""Native desktop shell. Use --server for local development and UI verification."""
import argparse
import ctypes
import logging
import json
import os
from pathlib import Path
import sys
import threading
import webbrowser

from app.server import LocalServer
from app.storage import data_directory
from app.desktop import DesktopInstance

class DesktopAPI:
    def __init__(self,service):
        self._service=service
        self._window=None
        self._ready=False
        self._state_lock=threading.Lock()
        self._runtime_info={'pid':os.getpid(),'state':'starting'}

    def save_export(self,pid,fmt,stage=''):
        import webview
        name,raw,_=self._service.export(pid,fmt,stage)
        result=self._window.create_file_dialog(webview.FileDialog.SAVE,save_filename=name,
                                      file_types=(f'导出文件 (*.{fmt})',))
        if result:
            target=Path(result if isinstance(result,str) else result[0])
            target.write_bytes(raw)
            return {'saved':True,'path':str(target)}
        return {'saved':False}

    def open_data_folder(self):
        os.startfile(str(self._service.store.root))
        return True

    def report_ready(self):
        with self._state_lock:
            self._ready=True
            self._runtime_info['state']='ui_ready'
            (self._service.store.root/'runtime.json').write_text(json.dumps(self._runtime_info),encoding='utf-8')
        return True

    def _record_loaded(self):
        with self._state_lock:
            if not self._ready:
                self._runtime_info['state']='window_loaded'
                (self._service.store.root/'runtime.json').write_text(json.dumps(self._runtime_info),encoding='utf-8')

    def open_link(self,url):
        if isinstance(url,str) and url.startswith('https://'):
            webbrowser.open(url)
            return True
        return False

def message(text):
    if os.name=='nt':
        ctypes.windll.user32.MessageBoxW(None,text,'研序 Research Studio',0x40)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--server',action='store_true')
    parser.add_argument('--port',type=int,default=0)
    args=parser.parse_args()
    root=data_directory()
    root.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(filename=str(root/'application.log'),level=logging.ERROR,
                        format='%(asctime)s %(levelname)s %(message)s')
    instance=None
    if not args.server:
        instance=DesktopInstance(root)
        if not instance.primary:
            activated=instance.activate_existing()
            instance.close()
            if not activated:
                message('暂时无法唤起已有窗口。请从任务栏打开研序，或退出后重试。')
            return
    server=LocalServer(port=args.port)
    url=f'http://127.0.0.1:{server.server_port}'
    if args.server:
        print('Research Studio: '+url,flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return
    threading.Thread(target=server.serve_forever,daemon=True).start()
    runtime=root/'runtime.json'
    runtime.write_text(json.dumps({'pid':os.getpid(),'url':url,'state':'starting'}),encoding='utf-8')
    try:
        import webview
        api=DesktopAPI(server.service)
        api._runtime_info['url']=url
        server.native=api
        api._window=webview.create_window('研序 · Research Studio',url,js_api=api,
                      width=1360,height=900,min_size=(1040,700),background_color='#E4E8EC',
                      hidden=False,minimized=False,focus=True,text_select=True)
        def on_loaded():
            api._record_loaded()
        api._window.events.loaded += on_loaded
        webview.start(func=lambda:instance.start(api._window),gui='edgechromium',debug=False,private_mode=False,
                      storage_path=str(root/'webview'))
    except Exception:
        logging.exception('Desktop startup failed')
        message('软件窗口未能启动。请确认已安装 Microsoft Edge WebView2 Runtime。\n错误记录位于：'+str(root/'application.log'))
    finally:
        for job in server.service.jobs.values():
            job['cancel'].set()
        server.shutdown()
        server.server_close()
        runtime.write_text(json.dumps({'pid':os.getpid(),'state':'closed'}),encoding='utf-8')
        if instance:
            instance.close()

if __name__=='__main__':
    main()
