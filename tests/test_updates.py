import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch
from urllib.error import HTTPError
from urllib.request import Request
import zipfile

from app.updater import (UpdateManager,release_info,version,checked_url,GitHubRedirect,
    extract_executable,apply_update,file_hash,write_json,reset_environment,RELEASES,LATEST)
from app.server import LocalServer
from app.storage import Store
from app.service import Service
from main import DesktopAPI


def archive(entries=None):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:
        for name,data in (entries or [('app/YanxuResearchStudio.exe',b'MZnew executable'),('app/readme.txt',b'ok')]):
            z.writestr(name,data)
    return stream.getvalue()


def metadata(raw,tag='v0.2.4'):
    name='YanxuResearchStudio-Windows-'+tag+'.zip'
    return dict(tag_name=tag,draft=False,prerelease=False,html_url=RELEASES+'/tag/'+tag,body='<script>notes</script>',
        assets=[dict(name=name,state='uploaded',size=len(raw),digest='sha256:'+hashlib.sha256(raw).hexdigest(),
                     browser_download_url=RELEASES+'/download/'+tag+'/'+name)])


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.raw=archive();self.meta=metadata(self.raw)
        self.calls=[]
        def opener(url):
            self.calls.append(url)
            return io.BytesIO(json.dumps(self.meta).encode() if url==LATEST else self.raw)
        self.manager=UpdateManager(self.root,'0.2.3',opener);self.addCleanup(self.manager.close)

    def idle(self):
        deadline=time.monotonic()+5
        while self.manager.phase in ('checking','downloading') and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertNotIn(self.manager.phase,('checking','downloading'))

    def ready(self):
        self.manager.check();self.idle();self.assertEqual(self.manager.phase,'available')
        self.manager.download();self.idle();self.assertEqual(self.manager.phase,'ready',self.manager.error)

    def test_numeric_versions_and_invalid_tags(self):
        self.assertGreater(version('0.2.10'),version('v0.2.9'))
        for tag in ['0.2','v0.2.3-beta','1.02.3','../../app','v1.2.3x',None]:
            with self.assertRaises(ValueError):version(tag)

    def test_release_identity_assets_hash_and_size_required(self):
        self.assertEqual(release_info(self.meta)['version'],'0.2.4')
        for key,value in [('draft',True),('prerelease',True),('html_url','https://example.org'),('tag_name','0.2.4'),('assets',[])]:
            data=copy.deepcopy(self.meta);data[key]=value
            with self.assertRaises(ValueError):release_info(data)
        for key,value in [('digest',''),('size',0),('size',300*1024*1024),('browser_download_url','https://github.com/other/file')]:
            data=copy.deepcopy(self.meta);data['assets'][0][key]=value
            with self.assertRaises(ValueError):release_info(data)

    def test_redirect_rejects_untrusted_hosts_before_request(self):
        for url in ['http://github.com/x','https://evil.org','https://github.com@evil.org','https://github.com:8443/x']:
            with self.assertRaises(ValueError):checked_url(url)
        with self.assertRaises(ValueError):GitHubRedirect().redirect_request(Request(LATEST),None,302,'',{},'https://evil.org')
        self.assertTrue(checked_url('https://release-assets.githubusercontent.com/file'))

    def test_check_download_and_install_preserve_data(self):
        self.ready()
        self.assertEqual(self.calls,[LATEST,self.manager.release['url']])
        installed=self.root/'installed'/'YanxuResearchStudio.exe';installed.parent.mkdir();installed.write_bytes(b'MZold')
        data=self.root/'workspace.sqlite3';data.write_bytes(b'original research data')
        runner,plan=self.manager.prepare_install(installed,[123],['--port','59836'])
        self.assertEqual(runner.read_bytes(),b'MZold')
        launched=[];waited=[]
        result=apply_update(plan,self.root,lambda *args:launched.append(args),waited.append)
        self.assertTrue(result['ok']);self.assertEqual(installed.read_bytes(),b'MZnew executable')
        self.assertEqual(waited,[123]);self.assertEqual(launched[0][1],['--port','59836'])
        self.assertEqual((plan.parent/'previous.exe').read_bytes(),b'MZold')
        self.assertEqual(data.read_bytes(),b'original research data')
        self.assertEqual(reset_environment()['PYINSTALLER_RESET_ENVIRONMENT'],'1')

    def test_corrupted_and_truncated_download_never_ready(self):
        self.manager.check();self.idle();self.raw=self.raw[:-3]
        self.manager.download();self.idle()
        self.assertEqual(self.manager.phase,'error');self.assertIsNone(self.manager.ready)

    def test_tampered_staged_program_is_rejected(self):
        self.ready();Path(self.manager.ready['path']).write_bytes(b'MZtampered')
        target=self.root/'YanxuResearchStudio.exe';target.write_bytes(b'MZold')
        with self.assertRaises(ValueError):self.manager.prepare_install(target,[],[])
        self.assertEqual(target.read_bytes(),b'MZold')

    def test_launch_failure_restores_old_executable(self):
        self.ready();target=self.root/'YanxuResearchStudio.exe';target.write_bytes(b'MZold')
        _,plan=self.manager.prepare_install(target,[],[])
        launcher=Mock(side_effect=[OSError('launch failed'),None])
        result=apply_update(plan,self.root,launcher)
        self.assertFalse(result['ok']);self.assertEqual(target.read_bytes(),b'MZold')
        self.assertEqual(launcher.call_count,2)

    def test_copy_failure_preserves_old_executable(self):
        self.ready();target=self.root/'YanxuResearchStudio.exe';target.write_bytes(b'MZold')
        _,plan=self.manager.prepare_install(target,[],[])
        with patch('app.updater.shutil.copy2',side_effect=OSError('read only')):
            result=apply_update(plan,self.root,Mock())
        self.assertFalse(result['ok']);self.assertEqual(target.read_bytes(),b'MZold')

    def test_invalid_zip_paths_duplicate_exes_and_non_exe(self):
        for entries in [[('../escape',b'bad'),('app/YanxuResearchStudio.exe',b'MZx')],
                        [('a/YanxuResearchStudio.exe',b'MZa'),('b/YanxuResearchStudio.exe',b'MZb')],
                        [('app/YanxuResearchStudio.exe',b'not exe')]]:
            with self.assertRaises(ValueError):extract_executable(io.BytesIO(archive(entries)),self.root/'candidate')
        self.assertFalse((self.root.parent/'escape').exists())

    def test_older_release_never_downloads_and_auto_preference_persists(self):
        self.meta=metadata(self.raw,'v0.2.2');self.manager.check();self.idle()
        self.assertFalse(self.manager.status()['can_update'])
        with self.assertRaises(ValueError):self.manager.download()
        self.manager.set_auto(False)
        self.assertFalse(UpdateManager(self.root,'0.2.3').auto)
        with self.assertRaises(ValueError):self.manager.set_auto('false')

    def test_rate_limit_is_clear_and_can_retry(self):
        self.manager.opener=Mock(side_effect=HTTPError(LATEST,429,'rate limit',{},None))
        self.manager.check();self.idle();self.assertIn('限制请求',self.manager.error)

    def test_active_research_blocks_install_and_install_blocks_new_jobs(self):
        service=Service(Store(self.root/'research'));api=DesktopAPI(service);api._updates=Mock()
        with patch.object(service,'project_list',return_value=[{'running':True}]):
            with self.assertRaisesRegex(ValueError,'仍在运行'):api._install_update()
        api._updates.prepare_install.assert_not_called()
        service.updating=True
        with self.assertRaisesRegex(ValueError,'安装更新'):service.start('missing',{'stage':'research'})

    def test_native_update_helper_inherits_the_exact_existing_workspace(self):
        service=Service(Store(self.root/'physical-workspace'))
        self.addCleanup(service.close_plotting)
        self.addCleanup(service.assistant.close)
        self.addCleanup(service.backups.close)
        api=DesktopAPI(service);api._updates=Mock();api._window=Mock()
        api._updates.prepare_install.return_value=(self.root/'update-runner.exe',self.root/'plan.json')
        with patch('main.subprocess.Popen') as launch,patch('main.threading.Timer'):
            api._install_update()
        environment=launch.call_args.kwargs['env']
        self.assertEqual(environment['YANXU_DATA_DIR'],str(service.store.root))
        self.assertEqual(environment['PYINSTALLER_RESET_ENVIRONMENT'],'1')
        self.assertTrue(service.updating)

    def test_local_api_requires_auth_and_development_install_is_blocked(self):
        import urllib.request,urllib.error
        server=LocalServer(Service(Store(self.root/'api')));server.updates.opener=self.manager.opener
        server.updates.current='0.0.1'  # Keep this future-release test independent of VERSION.
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}/api/updates/'
        with self.assertRaises(urllib.error.HTTPError) as denied:urllib.request.urlopen(base+'status')
        self.assertEqual(denied.exception.code,403)
        headers={'X-Research-Token':server.token,'Content-Type':'application/json'}
        with urllib.request.urlopen(urllib.request.Request(base+'check',data=b'{}',headers=headers)) as response:
            self.assertIn(json.load(response)['phase'],('checking','available'))
        for action in ['download','install']:
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(base+action,data=b'{"acknowledge":true}',headers=headers))
            self.assertEqual(error.exception.code,400)


if __name__=='__main__':unittest.main()
