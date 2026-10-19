"""Windows single-instance activation; no network endpoint or research data."""
import ctypes
from ctypes import wintypes
import hashlib
import json
import logging
import os
from pathlib import Path
import threading


class DesktopInstance:
    def __init__(self, root):
        self.root=Path(root)
        self._stop=threading.Event()
        self._thread=None
        self._activations=0
        self._mutex=None
        self._event=None
        self._ack=None
        self.primary=True
        if os.name!='nt':
            return
        self._kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self._kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
        self._kernel.CreateMutexW.restype=wintypes.HANDLE
        self._kernel.CreateEventW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.BOOL,wintypes.LPCWSTR]
        self._kernel.CreateEventW.restype=wintypes.HANDLE
        self._kernel.SetEvent.argtypes=[wintypes.HANDLE]
        self._kernel.SetEvent.restype=wintypes.BOOL
        self._kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        self._kernel.WaitForSingleObject.restype=wintypes.DWORD
        self._kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        self._kernel.CloseHandle.restype=wintypes.BOOL
        suffix=hashlib.sha256(os.path.normcase(str(self.root.resolve())).encode()).hexdigest()[:24]
        name='Local\\YanxuResearchStudio.'+suffix
        # Create the auto-reset event first so a second launch during startup is queued.
        self._event=self._kernel.CreateEventW(None,False,False,name+'.activate')
        if not self._event:
            raise ctypes.WinError(ctypes.get_last_error())
        self._ack=self._kernel.CreateEventW(None,False,False,name+'.shown')
        if not self._ack:
            error=ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)
        self._mutex=self._kernel.CreateMutexW(None,False,name+'.instance')
        error=ctypes.get_last_error()
        if not self._mutex:
            self.close()
            raise ctypes.WinError(error)
        self.primary=error!=183

    def activate_existing(self,wait_ms=5000):
        if self.primary or not self._event:
            return False
        # A user-initiated second launch may grant its existing process foreground focus.
        try:
            state=json.loads((self.root/'runtime.json').read_text(encoding='utf-8'))
            pid=int(state['pid'])
            if 0<pid<2**32:
                user=ctypes.WinDLL('user32',use_last_error=True)
                user.AllowSetForegroundWindow.argtypes=[wintypes.DWORD]
                user.AllowSetForegroundWindow.restype=wintypes.BOOL
                user.AllowSetForegroundWindow(pid)
        except (OSError,ValueError,KeyError,TypeError):
            pass
        signaled=bool(self._kernel.SetEvent(self._event))
        if signaled and wait_ms:
            self._kernel.WaitForSingleObject(self._ack,max(0,min(5000,int(wait_ms))))
        return signaled

    def start(self, window):
        if not self.primary or self._thread:
            return
        self._thread=threading.Thread(target=self._listen,args=(window,),daemon=True)
        self._thread.start()

    def _listen(self, window):
        # Invoke on the UI thread once its native form is constructed. This also
        # overrides a launcher that accidentally supplied Windows' hidden show state.
        while not self._stop.wait(.1):
            native=window.native
            if native is not None and native.IsHandleCreated:
                break
        if self._stop.is_set():
            return
        self._show(window)
        if not self._event:
            return
        while not self._stop.is_set():
            result=self._kernel.WaitForSingleObject(self._event,250)
            if self._stop.is_set():
                break
            if result==0:
                if self._show(window):
                    self._kernel.SetEvent(self._ack)
            elif result!=258:
                logging.error('Desktop activation wait failed: %s',result)
                break

    def _show(self, window):
        try:
            from System import Action
            from System.Drawing import Point, Rectangle
            from System.Windows.Forms import FormWindowState, Screen
            form=window.native
            def show():
                if form.IsDisposed:
                    return
                if form.WindowState==FormWindowState.Minimized:
                    form.WindowState=FormWindowState.Normal
                form.ShowInTaskbar=True
                form.Show()
                # Recover a window left on a disconnected monitor.
                visible=False
                for screen in Screen.AllScreens:
                    overlap=Rectangle.Intersect(form.Bounds,screen.WorkingArea)
                    if overlap.Width>=100 and overlap.Height>=60:
                        visible=True
                        break
                if not visible:
                    area=Screen.PrimaryScreen.WorkingArea
                    form.Location=Point(area.Left+max(0,(area.Width-form.Width)//2),
                                        area.Top+max(0,(area.Height-form.Height)//2))
                form.BringToFront()
                form.Activate()
                user=ctypes.WinDLL('user32',use_last_error=True)
                user.SetForegroundWindow.argtypes=[wintypes.HWND]
                user.SetForegroundWindow.restype=wintypes.BOOL
                user.SetForegroundWindow(form.Handle.ToInt64())
                self._activations+=1
                (self.root/'window-state.json').write_text(json.dumps({
                    'pid':os.getpid(),'visible':bool(form.Visible),
                    'window_state':str(form.WindowState),'handle':form.Handle.ToInt64(),
                    'activations':self._activations,
                }),encoding='utf-8')
            if form.InvokeRequired:
                form.Invoke(Action(show))
            else:
                show()
            return True
        except Exception:
            logging.exception('Unable to show desktop window')
            return False

    def close(self):
        self._stop.set()
        if self._event and self._thread:
            self._kernel.SetEvent(self._event)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        # A blocked UI invocation must finish before its event handle is closed.
        if self._thread and self._thread.is_alive():
            return
        for name in ('_event','_ack','_mutex'):
            handle=getattr(self,name,None)
            if handle:
                self._kernel.CloseHandle(handle)
                setattr(self,name,None)
