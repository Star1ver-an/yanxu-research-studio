"""No real profile paths: simulate MSIX file redirection using temp files."""
import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.storage import Store, data_directory, _physical_data_directory


class DataDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.local=Path(self.temp.name).resolve()
        self.default=self.local/'YanxuResearchStudio'
        self.environment=patch.dict(os.environ,{'LOCALAPPDATA':str(self.local)},clear=False)
        self.environment.start();self.addCleanup(self.environment.stop)
        self.override=patch.dict(os.environ,{},clear=False)
        self.override.start();self.addCleanup(self.override.stop)
        os.environ.pop('YANXU_DATA_DIR',None)

    def database(self,root,projects=0,settings=None):
        root.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(root/'workspace.sqlite3')) as connection, connection:
            connection.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,body TEXT)')
            connection.execute('CREATE TABLE settings(id INTEGER PRIMARY KEY,body TEXT)')
            if projects:
                connection.execute('INSERT INTO projects VALUES(?,?)',('example','{}'))
            if settings is not None:
                connection.execute('INSERT INTO settings VALUES(1,?)',(json.dumps(settings),))
        return root.resolve()

    def package(self,suffix='test',**kwargs):
        root=self.local/'Packages'/('OpenAI.Codex_'+suffix)/'LocalCache'/'Local'/'YanxuResearchStudio'
        return self.database(root,**kwargs)

    def test_first_run_probe_is_removed_and_store_uses_same_root(self):
        result=data_directory()
        self.assertEqual(result,self.default)
        self.assertEqual(list(result.iterdir()),[])
        self.assertEqual(Store(result).root,result)
        self.assertFalse(any(result.glob('.yanxu-location-*')))

    def test_existing_database_file_resolves_physical_root(self):
        logical=self.database(self.default)
        physical=self.package(projects=1)
        resolve=Path.resolve
        def redirected(path,*args,**kwargs):
            if path==logical/'workspace.sqlite3':
                return physical/'workspace.sqlite3'
            return resolve(path,*args,**kwargs)
        with patch.object(Path,'resolve',redirected),patch.object(Store,'recover') as recover:
            self.assertEqual(_physical_data_directory(logical),physical)
            self.assertEqual(Store(logical).root,physical)
            self.assertEqual(recover.call_count,1,'Only explicit Store construction may recover')

    def test_inside_msix_and_outside_empty_workspace_choose_same_physical_library(self):
        logical=self.database(self.default)  # Old desktop launch created an empty schema.
        physical=self.package(projects=1)
        resolve=Path.resolve
        def redirected(path,*args,**kwargs):
            if path==logical/'workspace.sqlite3':
                return physical/'workspace.sqlite3'
            return resolve(path,*args,**kwargs)
        with patch.object(Path,'resolve',redirected),patch.object(Store,'recover'):
            inside=data_directory()
            self.assertEqual(inside,physical)
            self.assertEqual(Store().root,inside)
        # Without package virtualization, discovery must reach that exact library.
        outside=data_directory()
        self.assertEqual(outside,inside)
        self.assertEqual(outside,physical)

    def test_new_virtual_directory_uses_probe_file_resolution(self):
        physical=self.local/'physical-new'
        physical.mkdir()
        resolve=Path.resolve
        def redirected(path,*args,**kwargs):
            if path.parent==self.default and path.name.startswith('.yanxu-location-'):
                self.assertTrue(path.is_file())
                return physical/path.name
            return resolve(path,*args,**kwargs)
        with patch.object(Path,'resolve',redirected):
            self.assertEqual(data_directory(),physical)
        self.assertFalse(list(self.default.glob('.yanxu-location-*')))
        self.assertFalse(list(physical.iterdir()))

    def test_unique_populated_package_is_found_without_mutating_database(self):
        package=self.package(projects=1)
        original=(package/'workspace.sqlite3').read_bytes()
        with patch.object(Store,'recover',side_effect=AssertionError('discovery must not recover')):
            self.assertEqual(data_directory(),package)
        self.assertEqual((package/'workspace.sqlite3').read_bytes(),original)
        self.assertFalse((self.default/'workspace.sqlite3').exists())

    def test_saved_connection_is_sufficient_and_empty_packages_are_ignored(self):
        configured=self.package('configured',settings={'mode':'api','encrypted_key':'opaque-test-only'})
        self.package('empty')
        self.assertEqual(data_directory(),configured)

    def test_existing_normal_data_always_takes_priority(self):
        self.package(projects=1)
        self.database(self.default,projects=1)
        self.assertEqual(data_directory(),self.default)

    def test_existing_normal_connection_or_orphaned_material_is_preserved(self):
        self.package(projects=1)
        self.database(self.default,settings={'codex_path':'configured-codex.exe'})
        self.assertEqual(data_directory(),self.default)
        with closing(sqlite3.connect(self.default/'workspace.sqlite3')) as connection, connection:
            connection.execute('DELETE FROM settings')
        folder=self.default/'materials'/'project';folder.mkdir(parents=True)
        (folder/'source.txt').write_text('valuable source',encoding='utf-8')
        self.assertEqual(data_directory(),self.default)

    def test_multiple_existing_package_workspaces_are_not_arbitrarily_selected(self):
        self.package('one',projects=1)
        self.package('two',projects=1)
        self.assertEqual(data_directory(),self.default)

    def test_explicit_environment_override_and_store_path_never_auto_switch(self):
        self.package(projects=1)
        override=self.local/'isolated-qa'
        with patch.dict(os.environ,{'YANXU_DATA_DIR':str(override)}):
            self.assertEqual(data_directory(),override)
            self.assertEqual(Store().root,override)
        explicit=self.local/'explicit'
        self.assertEqual(Store(explicit).root,explicit)

    def test_corrupt_normal_database_is_not_hidden_by_package_discovery(self):
        self.package(projects=1)
        self.default.mkdir()
        original=b'not a sqlite database'
        (self.default/'workspace.sqlite3').write_bytes(original)
        self.assertEqual(data_directory(),self.default)
        self.assertEqual((self.default/'workspace.sqlite3').read_bytes(),original)


if __name__=='__main__':
    unittest.main()
