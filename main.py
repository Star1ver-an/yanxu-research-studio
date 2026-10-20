"""Native desktop shell. Use --server for local development and UI verification."""
import argparse
import ctypes
import logging
import json
import os
from pathlib import Path
import sys
import threading
import subprocess
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
        self._updates=None
        self._restart_args=[]

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

    def save_figure_export(self,pid,identity,fmt):
        import webview
        name,raw,_=self._service.plot_export(pid,{'id':identity,'format':fmt})
        result=self._window.create_file_dialog(webview.FileDialog.SAVE,save_filename=name,
                                             file_types=(f'绘图文件 (*.{fmt})',))
        if result:
            target=Path(result if isinstance(result,str) else result[0])
            target.write_bytes(raw)
            return {'saved':True,'path':str(target)}
        return {'saved':False}

    def save_assistant_export(self, pid=''):
        import webview
        raw=self._service.assistant.export({'project':pid})
        result=self._window.create_file_dialog(webview.FileDialog.SAVE,save_filename='研序-AI助手对话.md',
                                              file_types=('Markdown (*.md)',))
        if result:
            target=Path(result if isinstance(result,str) else result[0])
            target.write_bytes(raw)
            return {'saved':True,'path':str(target)}
        return {'saved':False}

    def report_ready(self):
        with self._state_lock:
            first=not self._ready
            self._ready=True
            self._runtime_info['state']='ui_ready'
            (self._service.store.root/'runtime.json').write_text(json.dumps(self._runtime_info),encoding='utf-8')
        if first and self._updates:
            self._updates.start_auto()
        return True

    def save_progress_export(self, pid):
        import webview
        raw = self._service.assistant.progress_export({'project': pid})
        result = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename='研序-项目进度简报.md',
                                                 file_types=('Markdown (*.md)',))
        if result:
            target = Path(result if isinstance(result, str) else result[0])
            target.write_bytes(raw)
            return {'saved': True, 'path': str(target)}
        return {'saved': False}

    def _install_update(self):
        from app.updater import reset_environment
        with self._service.lock:
            if self._service.updating:
                raise ValueError('已经在准备安装更新。')
            if any(p['running'] for p in self._service.project_list()) or self._service.assistant.busy() or self._service.plotting_busy():
                raise ValueError('研究任务或 AI 问答仍在运行，请等待完成或先停止，再安装更新。')
            runner,plan=self._updates.prepare_install(sys.executable,[os.getpid(),os.getppid()],self._restart_args)
            self._service.updating=True
            try:
                environment=reset_environment()
                # MSIX can redirect LocalAppData files while leaving directory
                # resolve() unchanged. The copied helper must use this same store.
                environment['YANXU_DATA_DIR']=str(self._service.store.root)
                subprocess.Popen([str(runner),'--apply-update',str(plan)],cwd=runner.parent,
                    env=environment,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except OSError:
                self._service.updating=False
                self._updates.phase='ready'
                raise ValueError('无法启动更新程序，请重新下载或稍后重试。') from None
        close=threading.Timer(.4,self._window.destroy)
        close.daemon=True
        close.start()

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
    parser.add_argument('--apply-update',default='')
    args=parser.parse_args()
    root=data_directory()
    root.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(filename=str(root/'application.log'),level=logging.ERROR,
                        format='%(asctime)s %(levelname)s %(message)s')
    if args.apply_update:
        from app.updater import apply_update
        try:
            apply_update(args.apply_update,root)
        except Exception:
            logging.exception('Update helper could not apply the installation plan')
        return
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
        api._updates=server.updates
        api._restart_args=['--port',str(args.port)] if args.port else []
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
