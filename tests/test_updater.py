import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch as mock

import monitor
import patch as p
import updater as u


class UpdateTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Real Windows background worker')
    def test_update_worker_has_no_console_and_keeps_result_file_transport(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'update_worker.py').write_text("from pathlib import Path\nimport ctypes,json,sys\np=Path(sys.argv[2]); p.with_suffix('.result.json').write_text(json.dumps({'console':int(ctypes.windll.kernel32.GetConsoleWindow()),'input':json.loads(p.read_text())}))\n", encoding='utf-8')
            self.assertEqual(u.worker(root, 'identity', {'probe':'unchanged'}, root/'job'),
                             {'console':0, 'input':{'probe':'unchanged'}})

    def release(self, edition='ds'):
        names=['patch.py','monitor.py','updater.py','update_worker.py','model_sync.py','edition.json','tests/test_updater.py','tests/test_model_sync.py','tests/route.test.cjs']
        contents={name:b'fixture\n' for name in names}
        contents['edition.json']=json.dumps({'edition':edition,'branch':u.CHANNELS[edition],'version':'1.0.0'}).encode()
        release={'format':1,'edition':edition,'branch':u.CHANNELS[edition],'version':'1.0.0','sequence':2,
                 'commit':'1'*40,'files':{name:hashlib.sha256(raw).hexdigest() for name,raw in contents.items()}}
        return release,contents

    def test_edition_and_branch_are_both_pinned(self):
        release,_=self.release('ds')
        self.assertEqual(u.validate_release(release,'ds'),release)
        with self.assertRaisesRegex(p.PatchError,'different edition'):u.validate_release(release,'proxy')
        release['branch']=u.CHANNELS['proxy']
        with self.assertRaises(p.PatchError):u.validate_release(release,'ds')

    def test_rollback_mutable_commit_and_traversal_are_rejected(self):
        release,_=self.release()
        with self.assertRaisesRegex(p.PatchError,'rollback'):u.validate_release(release,'ds',3)
        changed=copy.deepcopy(release);changed['commit']='main'
        with self.assertRaises(p.PatchError):u.validate_release(changed,'ds')
        for name in ['../secret','/absolute','C:/secret','x/../../secret','x\\secret']:
            changed=copy.deepcopy(release);changed['files'][name]='2'*64
            with self.assertRaises(p.PatchError):u.validate_release(changed,'ds')

    def test_hash_mismatch_never_activates_or_keeps_partial_package(self):
        release,contents=self.release()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);runtime=root/'runtime';runtime.mkdir();(runtime/'old').write_text('keep')
            with self.assertRaisesRegex(p.PatchError,'digest mismatch'):
                u.source_package(release,root,fetch=lambda _:b'wrong')
            self.assertEqual((runtime/'old').read_text(),'keep')
            self.assertFalse((root/'packages'/release['commit']).exists())

    def test_preflight_isolated_and_cached_files_are_reverified(self):
        release,contents=self.release()
        def fetch(url):return contents[url.split('/'+release['commit']+'/',1)[1]]
        with tempfile.TemporaryDirectory() as tmp, mock.object(u.subprocess,'run',return_value=Mock(returncode=0,stderr='')) as run:
            root=Path(tmp)
            with mock.dict(u.os.environ,{'OPENAI_API_KEY':'must-not-inherit','CODEX_HOME':'private'}):
                target=u.source_package(release,root,fetch)
            self.assertEqual(run.call_count,2)
            for call in run.call_args_list:
                self.assertNotIn('OPENAI_API_KEY',call.kwargs['env'])
                self.assertNotEqual(call.kwargs['env']['CODEX_HOME'],'private')
            self.assertEqual(u.source_package(release,root,lambda _:self.fail('cached package must not redownload')),target)
            (target/'patch.py').write_text('modified')
            with self.assertRaisesRegex(p.PatchError,'digest mismatch'):
                u.source_package(release,root,fetch)

    def test_wrong_package_edition_fails_before_executing_tests(self):
        release,contents=self.release('ds')
        contents['edition.json']=json.dumps({'edition':'proxy','branch':u.CHANNELS['proxy'],'version':'1.0.0'}).encode()
        release['files']['edition.json']=hashlib.sha256(contents['edition.json']).hexdigest()
        with tempfile.TemporaryDirectory() as tmp, mock.object(u.subprocess,'run') as runner:
            with self.assertRaisesRegex(p.PatchError,'edition/version'):
                u.source_package(release,tmp,lambda url:contents[url.split('/'+release['commit']+'/',1)[1]])
            runner.assert_not_called()

    def test_model_or_code_change_triggers_new_composite_identity(self):
        base=u.UpdateBackend.combined('same-app','code-1','models-1')
        self.assertNotEqual(base,u.UpdateBackend.combined('same-app','code-2','models-1'))
        self.assertNotEqual(base,u.UpdateBackend.combined('same-app','code-1','models-2'))
        self.assertEqual(base,u.UpdateBackend.combined('same-app','code-1','models-1'))

    def test_adoption_uses_installed_metadata_not_pending_model_defaults(self):
        obj=object.__new__(u.UpdateBackend);obj.edition='ds';obj.settings={}
        obj.base=Mock();obj.base.adopt.return_value={'fingerprint':'app','baseline':'old'}
        obj.installed_metadata=lambda:{'edition':'ds','runtime_digest':'old-code','model_digest':'old-models'}
        obj.desired_runtime='new-code';obj.model_snapshot={'digest':'new-models'}
        value=obj.adopt()
        self.assertEqual(value['fingerprint'],u.UpdateBackend.combined('app','old-code','old-models'))

    def test_channel_change_cannot_overwrite_existing_monitor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);current=u.edition()['edition'];other='ds' if current=='proxy' else 'proxy'
            monitor.atomic_json(root/'settings.json',{'edition':other,'app':'untouched'})
            args=Mock(state_dir=root,interval=300,baseline=None)
            with self.assertRaisesRegex(ValueError,'pinned to another edition'), mock.object(monitor.sys,'platform','darwin'):
                monitor.enable(args)
            self.assertEqual(monitor.load_json(root/'settings.json')['app'],'untouched')
            self.assertFalse((root/'runtime').exists())

    def test_preferences_survive_monitor_ticks_and_reset_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp, mock('builtins.print'):
            root=Path(tmp)
            self.assertEqual(monitor.main(['--state-dir',str(root),'model-options','--model','grok-4.7','--context-window','200000','--hide']),0)
            prefs=monitor.load_json(root/'model-preferences.json')
            self.assertEqual(prefs['context_windows']['grok-4.7'],200000)
            self.assertIn('grok-4.7',prefs['hidden'])
            self.assertEqual(monitor.load_json(root/'updates.json')['models_checked'],0)
            monitor.main(['--state-dir',str(root),'model-options','--model','grok-4.7','--show','--reset-context'])
            prefs=monitor.load_json(root/'model-preferences.json')
            self.assertNotIn('grok-4.7',prefs['hidden'])
            self.assertIsNone(prefs['context_windows']['grok-4.7'])

    def test_runtime_activation_failure_is_recoverable_after_app_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);build=root/'build';build.mkdir()
            p.save_json(build/'manifest.json',{'status':'installed'})
            obj=object.__new__(u.UpdateBackend);obj.directory=root;obj.package=root;obj.edition='ds'
            obj.base=Mock();obj.base.snapshot.return_value={'fingerprint':'new-app'}
            obj.snapshot=lambda:{'fingerprint':'candidate'};obj.desired_runtime='code'
            obj.promote=Mock(side_effect=OSError('locked runtime'))
            with mock.object(u,'worker'):
                result=obj.install({'directory':str(build)},{'fingerprint':'candidate','model_digest':'models'})
            self.assertEqual(result['baseline'],str(build))
            self.assertTrue((root/'activation-pending.json').exists())
            obj.prepared=True;obj.promote=Mock()
            # A fresh process retries the journal before model or code discovery.
            obj.prepared=False;obj.settings={'auto_update':False,'model_sync':False};obj.cache={}
            obj.cache_path=root/'updates.json';obj.warnings=[]
            with mock.object(u,'worker',return_value={'edition':'ds','runtime_digest':'code'}):obj.prepare()
            obj.promote.assert_called_once_with(build)
            self.assertFalse((root/'activation-pending.json').exists())

    def test_activation_releases_runtime_working_directory_before_rename(self):
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); runtime = root / 'runtime'; runtime.mkdir()
            (runtime / 'version').write_text('old')
            package = root / 'package'; package.mkdir(); (package / 'version').write_text('new')
            identity = {'edition': 'proxy', 'files': ['version'], 'runtime_digest': u.runtime_digest(package, ['version'])}
            try:
                u.os.chdir(runtime)
                with mock.object(u, 'worker', return_value=identity):
                    u.activate_runtime(package, root, 'proxy')
                self.assertEqual(Path.cwd(), root)
                self.assertEqual((runtime / 'version').read_text(), 'new')
                self.assertEqual(next(root.glob('runtime-backup-*')).joinpath('version').read_text(), 'old')
            finally:
                u.os.chdir(previous)

    def test_release_cannot_omit_required_regression_tests(self):
        release,_=self.release()
        del release['files']['tests/test_updater.py']
        with self.assertRaisesRegex(p.PatchError,'Incomplete'):u.validate_release(release,'ds')


class ExtensionWorkerTests(unittest.TestCase):
    def test_task_migration_runs_only_after_all_extension_checks_pass(self):
        import update_worker as worker
        for fail in [True, False]:
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                job={'architecture':'legacy','app':str(root/'app'),'baseline':str(root/'baseline'),'output':str(root/'build'),
                     'settings':{'codex_home':str(root/'home'),'keychain_account':'account',
                                 'keychain_service':'service','state_dir':str(root/'state')}}
                calls=[]
                def verify(output):
                    calls.append('verify')
                    if fail:raise RuntimeError('probe failed')
                with mock.object(worker.backend,'build_compatible',side_effect=lambda _:calls.append('build')), \
                     mock.object(worker,'verify_build',side_effect=verify), \
                     mock.object(monitor,'migrate_windows_working_directory',side_effect=lambda path:calls.append(('migrate',path))):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError,'probe failed'):worker.execute('build',job)
                        self.assertEqual(calls,['build','verify'])
                    else:
                        result=worker.execute('build',job)
                        self.assertEqual(result['status'],'prepared')
                        self.assertEqual(calls,['build','verify',('migrate',str(root/'state'))])

    def test_extension_verification_keeps_large_tool_and_switching_gates(self):
        import update_worker as worker
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p.save_json(root/'manifest.json',{'model_routes':{'example':{}},
                'switching_probe_required':True,'tool_loading_probe_required':True})
            calls=[]
            from contextlib import ExitStack
            with ExitStack() as stack:
                for name in ['offline','reasoning','context','switching','tools','boot']:
                    stack.enter_context(mock('scripts.test_'+name+'.main',side_effect=lambda _,name=name:calls.append(name)))
                worker.verify_build(root)
            self.assertEqual(calls,['offline','reasoning','context','switching','tools','boot'])


if __name__=='__main__':unittest.main()
