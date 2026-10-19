import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.desktop import DesktopInstance


@unittest.skipUnless(os.name=='nt','Windows native instance IPC')
class DesktopInstanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.handles=[]

    def tearDown(self):
        for instance in reversed(self.handles):
            instance.close()
        self.temp.cleanup()

    def instance(self,path=None):
        instance=DesktopInstance(path or self.temp.name)
        self.handles.append(instance)
        return instance

    def test_second_launch_reuses_instance_and_closed_handles_allow_restart(self):
        first=self.instance()
        second=self.instance()
        self.assertTrue(first.primary)
        self.assertFalse(second.primary)
        second.close()
        first.close()
        self.assertTrue(self.instance().primary)

    def test_separate_workspaces_do_not_block_each_other(self):
        self.assertTrue(self.instance(Path(self.temp.name)/'a').primary)
        self.assertTrue(self.instance(Path(self.temp.name)/'b').primary)

    def test_early_activation_is_delivered_after_window_becomes_available(self):
        first=self.instance()
        second=self.instance()
        self.assertTrue(second.activate_existing(wait_ms=0))
        shown=[]
        activated=threading.Event()
        class Native:
            IsHandleCreated=True
        class Window:
            native=Native()
        def show(window):
            shown.append(window)
            if len(shown)>=2:
                activated.set()
        with patch.object(DesktopInstance,'_show',side_effect=show):
            first.start(Window())
            self.assertTrue(activated.wait(3),'Queued activation was lost')
            first.close()
        self.assertGreaterEqual(len(shown),2)
        self.assertFalse(first._thread.is_alive())

    def test_primary_cannot_signal_itself_or_start_multiple_listeners(self):
        first=self.instance()
        self.assertFalse(first.activate_existing())
        ready=threading.Event()
        class Native:
            IsHandleCreated=True
        class Window:
            native=Native()
        with patch.object(DesktopInstance,'_show',side_effect=lambda window:ready.set()):
            first.start(Window())
            self.assertTrue(ready.wait(3))
            thread=first._thread
            first.start(Window())
            self.assertIs(first._thread,thread)
            first.close()
            first.close()


if __name__=='__main__':
    unittest.main()
