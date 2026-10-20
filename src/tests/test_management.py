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

    def test_whale_cards_share_existing_model_rules_and_keep_editions_separate(self):
        self.assertEqual(len(ui.hub_shell(self.root)['categories']),6)
        for route in ('models','connections','updates','tools','diagnostics','preferences'):
            page=ui.hub_view(self.root,route)
            self.assertEqual(page['route'],route)
            self.assertEqual(page['revision'],ui.revision(self.root))
        result=ui.hub_dispatch(self.root,{'route':'models/edit','key':'local-test',
            'intent':'save_model','values':{'context':'8192','compact':'7000','visible':False}},
            ui.revision(self.root))
        self.assertEqual(result['route'],'models/edit')
        saved=json.loads((self.root/'model-preferences.json').read_text())
        self.assertEqual(saved['context_windows']['local-test'],8192)
        self.assertIn('local-test',saved['hidden'])
        with self.assertRaises(ui.SettingsError):
            ui.hub_dispatch(self.root,{'route':'models/edit','key':'local-test',
                'intent':'save_model','values':{'context':'NaN'}},ui.revision(self.root))
        self.save(self.root/'settings.json',{**self.settings,'edition':'ds'})
        self.save(self.build/'manifest.json',{**self.manifest,'edition':'ds'})
        with patch.object(ui.p,'EDITION','ds'):
            page=ui.hub_view(self.root,'connections')
            self.assertEqual([r['title'] for r in page['rows']],['DeepSeek'])
            with self.assertRaises(ui.SettingsError):ui.hub_view(self.root,'connections/edit')

    def test_update_page_embeds_progress_and_actions_keep_current_update_identity(self):
        import update_progress
        progress=update_progress.Progress(self.root)
        identity=progress.begin('proxy','1.0.0-proxy.1','fixture')
        progress.publish('downloading',2,7)
        page=ui.hub_view(self.root,'updates')
        row=next(r for r in page['rows'] if r['type']=='update')
        self.assertEqual((row['progress']['completed'],row['progress']['total']),(2,7))
        self.assertNotIn('show_progress',[b['intent'] for b in page['buttons']])
        with patch.object(update_progress,'action') as action:
            ui.hub_dispatch(self.root,{'route':'updates','intent':'update_action','id':identity,
                'command':'start','phase':'downloading'},ui.revision(self.root))
            action.assert_called_once_with(self.root,identity,'start','downloading')
            progress.begin('proxy','1.0.1-proxy.1','new')
            with self.assertRaises(ui.SettingsError):
                self.perform('update_action',{'id':identity,'command':'start'})
        self.save(self.root/'progress.json',{'id':'a'*32,'edition':'ds','phase':'available'})
        self.assertEqual(ui.hub_progress(self.root),{})

    def test_manual_model_sync_does_not_request_patch_download(self):
        with patch.object(ui.monitor,'request_update') as request,patch.object(ui.monitor,'wake_monitor'):
            self.perform('sync_models',{})
        request.assert_called_once_with(self.root,sync_now=True,code_now=False,retry=False)

    @unittest.skipUnless(sys.platform in ('darwin','win32'),'Native integrated update page')
    def test_native_update_card_badge_and_completion_share_the_settings_host(self):
        import update_progress
        channel=json.loads((ui.ROOT/'edition.json').read_text())['edition']
        self.save(self.root/'settings.json',{**self.settings,'edition':channel})
        self.save(self.build/'manifest.json',{**self.manifest,'edition':channel})
        progress=update_progress.Progress(self.root)
        progress.begin(channel,'1.0.0','fixture')
        for phase,attention,primary in [('available',True,'开始更新'),('completed',False,'启动 Codex')]:
            progress.publish(phase)
            with patch.object(ui.p,'EDITION',channel):result=ui.show(self.root,self_test=True,page='updates',notify=True)
            self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
            value=json.loads(result.stdout.decode('utf-8-sig'))
            self.assertEqual(value['route'],'updates')
            self.assertEqual(value['update_attention'],attention)
            self.assertTrue(value['badge_visible'])
            self.assertEqual(value['update_primary'],primary)
            self.assertFalse(value.get('draw_error'))

    def test_discovery_never_echoes_password_into_a_native_result(self):
        result={'available':['remote-x'],'specs':{'remote-x':{'context_window':131072}},'warnings':[]}
        with patch.object(ui,'probe_connection',return_value=result):
            page=ui.hub_dispatch(self.root,{'route':'connections/edit','intent':'probe_connection',
                'values':{'name':'Test','kind':'openai','wire':'chat_completions',
                          'url':'http://127.0.0.1:1234/v1','key':'synthetic-password'}},ui.revision(self.root))
        self.assertEqual(page['route'],'connections/discover')
        self.assertNotIn('synthetic-password',json.dumps(page))
        self.assertNotIn('key',page['draft'])

    def test_appearance_does_not_queue_a_model_rebuild_or_change_credentials(self):
        original=ui.revision(self.root)
        page=ui.hub_dispatch(self.root,{'route':'preferences','intent':'save_ui_options',
            'values':{'theme':'dark','motion':'reduced','hotkey':False}},original)
        self.assertEqual(page['options'],{'theme':'dark','motion':'reduced','hotkey':False,'sound':True})
        ui.hub_save_options(self.root,{'sound':False})
        self.assertFalse(ui.hub_options(self.root)['sound'])
        self.assertEqual(ui.revision(self.root),original)
        self.queued.assert_not_called()
        with self.assertRaises(ui.SettingsError):
            ui.hub_save_options(self.root,{'theme':'remote-script'})

    def test_upgrade_waits_for_old_editor_and_coalesces_one_new_host(self):
        import threading,time
        results=[]
        original=ui.revision(self.root)
        ui.monitor.atomic_json(self.root/'settings-ui/host.json',{'edition':'proxy','pid':os.getpid()})
        with patch.object(ui,'_show_locked',return_value='new-whale') as opened:
            with ui.monitor.state_lock(self.root/'settings-ui') as held:
                self.assertTrue(held)
                thread=threading.Thread(target=lambda:results.append(ui.show(self.root,notify=True)),daemon=True)
                thread.start()
                waiting=False
                for _ in range(100):
                    with ui.monitor.state_lock(self.root/'settings-ui/launch-control') as free:
                        if not free:waiting=True;break
                    time.sleep(.01)
                self.assertTrue(waiting,'Handoff launcher did not acquire its singleton lock')
                # An explicit reopen takes precedence over another background notice.
                ui.show(self.root,page='updates')
                ui.show(self.root,notify=True)
                opened.assert_not_called();self.assertTrue(thread.is_alive())
                self.assertFalse((self.root/'settings-ui/raise.json').exists())
            thread.join(3)
            self.assertFalse(thread.is_alive());self.assertEqual(results,['new-whale'])
            opened.assert_called_once()
            self.assertEqual(opened.call_args.kwargs['page'],'updates')
            self.assertFalse(opened.call_args.kwargs['notify'])
        self.assertEqual(ui.revision(self.root),original)

    def test_bubble_audio_is_soft_bounded_and_reused(self):
        import wave, struct
        sounds=ui.bubble_assets(self.root)
        before={name:path.stat().st_mtime_ns for name,path in sounds.items()}
        for path in sounds.values():
            with wave.open(str(path),'rb') as audio:
                self.assertEqual((audio.getnchannels(),audio.getsampwidth(),audio.getframerate()),(1,2,24000))
                duration=audio.getnframes()/audio.getframerate()
                self.assertGreater(duration,.1);self.assertLess(duration,.2)
                data=audio.readframes(audio.getnframes())
            values=struct.unpack('<'+'h'*(len(data)//2),data)
            self.assertEqual((values[0],values[-1]),(0,0))
            self.assertGreater(max(map(abs,values)),500)
            self.assertLess(max(map(abs,values)),8192)
        ui.bubble_assets(self.root)
        self.assertEqual(before,{name:path.stat().st_mtime_ns for name,path in sounds.items()})
        with self.assertRaises(ui.SettingsError):ui.hub_save_options(self.root,{'sound':'false'})

    def test_logo_has_one_whale_contour_and_one_terminal_mark(self):
        import xml.etree.ElementTree as ET
        paths=list(ET.fromstring((ui.ROOT/'brand.svg').read_bytes()).iter('{http://www.w3.org/2000/svg}path'))
        self.assertEqual(len(paths),2)
        self.assertEqual(len(ui.vector_contours(paths[0].attrib['d'])),1)
        self.assertEqual(len(ui.vector_contours(paths[1].attrib['d'])),2)

    @unittest.skipUnless(sys.platform in ('darwin','win32'),'Native settings widgets')
    def test_native_widgets_complete_private_snapshot_and_close_headlessly(self):
        channel=json.loads((ui.ROOT/'edition.json').read_text())['edition']
        self.save(self.root/'settings.json',{**self.settings,'edition':channel})
        self.save(self.build/'manifest.json',{**self.manifest,'edition':channel})
        with patch.object(ui.p,'EDITION',channel):result=ui.show(self.root,self_test=True)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        try:value=json.loads(result.stdout.decode('utf-8-sig'))
        except ValueError:self.fail('Native settings output: '+repr(result.stdout)+' stderr: '+repr(result.stderr))
        self.assertEqual(value['categories'],6)
        self.assertFalse(value.get('draw_error'))
        if sys.platform=='darwin':self.assertGreaterEqual(value['animation_top_clearance'],4)
        self.assertEqual(value['models'],2)
        self.assertTrue(value['request_completed'])
        self.assertEqual(value['status'],'操作完成。')
        self.assertTrue(value['closed'])
        motion=value['motion']
        self.assertGreaterEqual(motion['opened'],2)
        self.assertGreaterEqual(motion['closed'],2)
        for name in ('hover_revealed','leave_retracted','detached','form_held','reduced','idle_timer_stopped','release_stays_collapsed','reentry_reveals','arrival_hint','hint_cleared','spray_released'):
            self.assertTrue(motion[name],(name,motion))
        self.assertEqual(motion['edges'],[True]*4)
        self.assertEqual(motion['directions'],[90,-90,180,0])
        self.assertEqual(motion['audio_loaded'],2)
        self.assertFalse(value['update_attention'])

    @unittest.skipUnless(sys.platform in ('darwin','win32'),'Native settings event loop')
    def test_native_window_loop_refreshes_and_closes_without_hiding_windows_gui(self):
        channel=json.loads((ui.ROOT/'edition.json').read_text())['edition']
        self.save(self.root/'settings.json',{**self.settings,'edition':channel})
        self.save(self.build/'manifest.json',{**self.manifest,'edition':channel})
        with patch.object(ui.p,'EDITION',channel):result=ui.show(self.root,self_test=True,loop_test=True)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        value=json.loads(result.stdout.decode('utf-8-sig'))
        self.assertEqual(value['status'],'操作完成。')
        self.assertEqual(value['models'],2)
        self.assertTrue(value['event_loop']);self.assertTrue(value['closed'])
        if sys.platform=='win32':self.assertTrue(value['visible'])

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

    @unittest.skipUnless(sys.platform=='darwin','AppKit lazy list')
    def test_macos_long_model_list_keeps_search_and_scrolling(self):
        channel=json.loads((ui.ROOT/'edition.json').read_text())['edition']
        self.save(self.root/'settings.json',{**self.settings,'edition':channel})
        self.save(self.build/'manifest.json',{**self.manifest,'edition':channel})
        self.save(self.build/'desktop-models.json',{'models':[
            {'slug':f'gpt-{i:02d}','context_window':1000000} for i in range(40)]})
        with patch.object(ui.p,'EDITION',channel):result=ui.show(self.root,self_test=True,loop_test=True)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        value=json.loads(result.stdout.decode('utf-8-sig'))
        self.assertFalse(value.get('draw_error'))
        listing=value['motion']['list']
        self.assertEqual(listing['total'],40)
        self.assertLessEqual(listing['initial_rows'],8)
        for behavior in ('tail_visible','searchable','restored'):
            self.assertTrue(listing[behavior],behavior)

    @unittest.skipUnless(sys.platform in ('darwin','win32'),'Per-user native entry')
    def test_uninstall_removes_only_unchanged_owned_launcher(self):
        runtime=self.root/'runtime';runtime.mkdir();(runtime/'management.py').write_text('#fixture')
        self.save(self.root/'settings.json',{**self.settings,'python_path':sys.executable})
        with patch.object(Path,'home',return_value=self.root/'user'),patch.dict(os.environ,{'APPDATA':str(self.root/'appdata')}):
            destination=Path(ui.ensure_launcher(self.root))
            self.assertTrue(ui.remove_launcher(self.root));self.assertFalse(destination.exists())


if __name__ == '__main__':unittest.main()
