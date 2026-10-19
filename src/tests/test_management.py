import json
from pathlib import Path
import tempfile
import sys
import os
import unittest
from unittest.mock import patch

import management as ui


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.build, self.home = self.root/'build', self.root/'home'
        self.build.mkdir(); self.home.mkdir()
        self.edition = patch.object(ui.p, 'EDITION', 'proxy')
        self.edition.start(); self.addCleanup(self.edition.stop)
        self.queue = patch.object(ui, '_queue')
        self.queued = self.queue.start(); self.addCleanup(self.queue.stop)
        self.settings = {'edition':'proxy','baseline':str(self.build),'codex_home':str(self.home),
            'app':str(self.root/'official'),'interval':300,'auto_update':True,'model_sync':True}
        self.manifest = {'edition':'proxy','architecture':'official-client-v1','tool_version':'fixture',
            'app_version':'native-fixture','deepseek_enabled':False,
            'locals':[{'id':'local-workpc','name':'Work PC','kind':'openai','base_url':'http://127.0.0.1:1234/v1',
                'key_file':str(self.root/'private-fixture-key'),'models':['local-test']}],
            'model_routes':{'local-test':{'provider':'local-workpc','context_window':32768,'max_context_window':262144}}}
        self.save(self.root/'settings.json', self.settings)
        self.save(self.build/'manifest.json', self.manifest)
        self.save(self.build/'desktop-models.json', {'models':[
            {'slug':'gpt-test','context_window':1000000,'max_context_window':1000000},
            {'slug':'local-test','context_window':32768,'max_context_window':262144}]})
        (self.home/'config.toml').write_text('model="gpt-test"\n[mcp_servers.personal]\ncommand="untouched"\n')

    def save(self, path, value):
        path.write_text(json.dumps(value), encoding='utf-8')

    def perform(self, action, payload):
        return ui.perform(self.root, action, payload, ui.revision(self.root))

    def test_model_reduction_visibility_and_explicit_reset_preserve_other_choices(self):
        self.save(self.root/'model-preferences.json', {'context_windows':{'gpt-test':500000},'hidden':['gpt-test']})
        self.perform('save_model', {'id':'local-test','context':16384,'compact':12000,'visible':False})
        value = json.loads((self.root/'model-preferences.json').read_text())
        self.assertEqual(value['context_windows'], {'gpt-test':500000,'local-test':16384})
        self.assertEqual(set(value['hidden']), {'gpt-test','local-test'})
        self.perform('save_model', {'id':'local-test','context':None,'compact':None,'visible':True})
        model = next(row for row in ui.snapshot(self.root)['models'] if row['id']=='local-test')
        self.assertEqual(model['effective'], 262144)
        self.assertEqual(model['installed_window'], 32768)
        self.assertTrue(model['visible'])
        self.assertEqual((self.home/'config.toml').read_text(), 'model="gpt-test"\n[mcp_servers.personal]\ncommand="untouched"\n')

    def test_invalid_window_and_stale_revision_never_overwrite_settings(self):
        before = ui.revision(self.root)
        for payload in ({'context':300000}, {'context':0}, {'context':True}, {'context':8192,'compact':8000}):
            with self.subTest(payload=payload), self.assertRaises(ui.SettingsError):
                ui.perform(self.root,'save_model',{'id':'local-test',**payload},before)
            self.assertEqual(ui.revision(self.root),before)
        self.perform('save_model', {'id':'local-test','context':8192})
        with self.assertRaisesRegex(ui.SettingsError,'刷新'):
            ui.perform(self.root,'save_model',{'id':'local-test','context':32768},before)

    def test_connection_save_keeps_other_connections_and_secret_out_of_document(self):
        connection={'id':'local-new','name':'New local','kind':'openai','base_url':'http://127.0.0.1:4567/v1',
            'wire_api':'chat_completions','models':['model-x'], 'model_specs':{'model-x':{'context_window':131072}}}
        self.perform('save_connection',{'connection':connection,'key':'synthetic-secret-not-exported'})
        value=json.loads((self.root/'connections.json').read_text())
        self.assertEqual([row['id'] for row in value['locals']],['local-workpc','local-new'])
        self.assertNotIn('synthetic-secret',json.dumps(value))
        stored=value['locals'][1]['key_file']
        self.assertEqual(Path(stored).read_text(),'synthetic-secret-not-exported')
        self.perform('remove_connection',{'id':'local-new'})
        self.assertEqual(len(json.loads((self.root/'connections.json').read_text())['locals']),1)
        self.assertTrue(Path(stored).exists())  # Disconnect is reversible; no credential purge.

    def test_key_write_is_rolled_back_when_connection_save_fails(self):
        original=ui.monitor.atomic_json
        def fail(path,value):
            if Path(path).name=='connections.json':raise OSError('synthetic write failure')
            return original(path,value)
        with patch.object(ui.monitor,'atomic_json',side_effect=fail), self.assertRaises(OSError):
            self.perform('save_connection',{'connection':{'id':'local-new','kind':'openai','base_url':'http://127.0.0.1:4567/v1'},'key':'secret'})
        self.assertFalse((self.root/'credentials/local-new.key').exists())
        self.assertFalse((self.root/'connections.json').exists())

    def test_disable_reenable_services_retains_connection_choices_and_model_reductions(self):
        proxy={'base_url':'http://127.0.0.1:8317/v1','config':str(self.root/'proxy.yaml'),'models':['grok-4.7']}
        preferences={'context_windows':{'grok-4.7':32768},'compact_limits':{'grok-4.7':28000},'hidden':['grok-4.5']}
        self.save(self.root/'model-preferences.json',preferences)
        self.perform('save_proxy',{'proxy':proxy,'auto_add':False})
        self.perform('save_proxy',{'proxy':None,'auto_add':False})
        snap=ui.snapshot(self.root)
        self.assertIsNone(snap['proxy']);self.assertEqual(snap['disabled_proxy'],proxy)
        self.perform('save_proxy',{'proxy':snap['disabled_proxy'],'auto_add':False})
        for enabled in (True,False,True):self.perform('save_deepseek',{'enabled':enabled})
        doc=json.loads((self.root/'connections.json').read_text())
        self.assertEqual(doc['proxy'],proxy);self.assertNotIn('disabled_proxy',doc)
        self.assertTrue(doc['deepseek_enabled']);self.assertFalse(doc['proxy_auto_add'])
        self.assertEqual(doc['locals'],self.manifest['locals'])
        self.assertEqual(json.loads((self.root/'model-preferences.json').read_text()),preferences)

    def test_wrong_edition_cannot_queue_or_change_other_channel(self):
        self.save(self.root/'settings.json',{**self.settings,'edition':'ds'})
        with patch.object(ui.monitor,'request_update') as enqueue, self.assertRaises(ui.SettingsError):
            self.perform('check_updates',{})
        enqueue.assert_not_called()

    def test_malformed_private_job_is_removed_and_returns_sanitized_result(self):
        jobs=self.root/'settings-ui/jobs';jobs.mkdir(parents=True)
        identifier='a'*32
        source=jobs/(identifier+'.request.json')
        source.write_text('{"key":"synthetic-secret",')
        result=ui.run_request(self.root,identifier)
        self.assertEqual(result['status'],'failed')
        self.assertFalse(source.exists())
        self.assertNotIn('synthetic-secret',json.dumps(result))
        self.assertEqual(json.loads((jobs/(identifier+'.result.json')).read_text(encoding='utf-8')),result)

    def test_diagnostics_do_not_export_connections_or_raw_errors(self):
        with patch('official_client_host.health_status',return_value={'ready':True}), \
             patch('official_client_update.managed_configuration'), \
             patch.object(ui.monitor.Scheduler,'status',return_value={'enabled':True}), \
             patch.object(ui,'probe_connection',side_effect=RuntimeError('secret /private/file https://private-host')):
            result=ui.diagnose(self.root,endpoints=True)
        raw=json.dumps(result)
        for private in ('secret','private-host','/private/file',str(self.root),'127.0.0.1'):
            self.assertNotIn(private,raw)
        self.assertTrue(any(row['status']=='error' for row in result['checks']))

    def test_schedule_failure_restores_previous_options(self):
        with patch.object(ui.monitor.Scheduler,'enable',side_effect=OSError('scheduler unavailable')), self.assertRaises(OSError):
            self.perform('save_options',{'interval':600,'auto_update':False})
        self.assertEqual(json.loads((self.root/'settings.json').read_text()),self.settings)

    @unittest.skipUnless(sys.platform in ('darwin','win32'),'Native settings widgets')
    def test_native_widgets_complete_private_snapshot_and_close_headlessly(self):
        channel=json.loads((ui.ROOT/'edition.json').read_text())['edition']
        self.save(self.root/'settings.json',{**self.settings,'edition':channel})
        self.save(self.build/'manifest.json',{**self.manifest,'edition':channel})
        with patch.object(ui.p,'EDITION',channel):result=ui.show(self.root,self_test=True)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        try:value=json.loads(result.stdout.decode('utf-8-sig'))
        except ValueError:self.fail('Native settings output: '+repr(result.stdout)+' stderr: '+repr(result.stderr))
        self.assertEqual(value['tabs'],3)
        self.assertEqual(value['models'],2)
        self.assertTrue(value['request_completed'])
        self.assertEqual(value['status'],'操作完成。')
        self.assertTrue(value['closed'])

    @unittest.skipUnless(sys.platform in ('darwin','win32'),'Per-user native entry')
    def test_settings_entry_is_separate_idempotent_and_preserves_user_edits(self):
        runtime=self.root/'runtime';runtime.mkdir();(runtime/'management.py').write_text('#fixture')
        self.save(self.root/'settings.json',{**self.settings,'python_path':sys.executable})
        with patch.object(Path,'home',return_value=self.root/'user'),patch.dict(os.environ,{'APPDATA':str(self.root/'appdata')}):
            destination=Path(ui.ensure_launcher(self.root))
            self.assertEqual(ui.ensure_launcher(self.root),str(destination))
            target=destination/'Contents/MacOS/settings' if destination.is_dir() else destination
            target.write_bytes(b'user changed entry')
            with self.assertRaises(ui.SettingsError):ui.ensure_launcher(self.root)
            self.assertFalse(ui.remove_launcher(self.root))
            self.assertEqual(target.read_bytes(),b'user changed entry')

    @unittest.skipUnless(sys.platform in ('darwin','win32'),'Per-user native entry')
    def test_uninstall_removes_only_unchanged_owned_launcher(self):
        runtime=self.root/'runtime';runtime.mkdir();(runtime/'management.py').write_text('#fixture')
        self.save(self.root/'settings.json',{**self.settings,'python_path':sys.executable})
        with patch.object(Path,'home',return_value=self.root/'user'),patch.dict(os.environ,{'APPDATA':str(self.root/'appdata')}):
            destination=Path(ui.ensure_launcher(self.root))
            self.assertTrue(ui.remove_launcher(self.root));self.assertFalse(destination.exists())


if __name__ == '__main__':unittest.main()
