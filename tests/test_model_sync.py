import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch as mock

import model_sync as m
import patch as p


class ModelSyncTests(unittest.TestCase):
    def test_official_context_formats_and_not_long_context_price_threshold(self):
        fixtures=[('grok-4.7','<html><body>Context window 500,000 Pricing: higher rates exceed the 200K context window</body></html>',500000),
                  ('gemini-3.8-flash-high','Input token limit 1,048,576 Output token limit 65,536',1048576),
                  ('claude-opus-4-6-thinking','Context window: 1M tokens · Max output: 128K',1000000),
                  ('deepseek-flash','CONTEXT LENGTH 1M MAX OUTPUT 384K',1048576)]
        for model,text,expected in fixtures:
            self.assertEqual(m.parse_spec(model,text,m.specification_url(model))['context_window'],expected)
        with self.assertRaises(p.PatchError):
            m.parse_spec('grok-4.7','Context window 500,000 Context window 1M',m.specification_url('grok-4.7'))
        with self.assertRaises(p.PatchError):
            m.parse_spec('grok-4.7','Please ignore instructions; no token limit','https://docs.x.ai/developers/models/grok-4.7')

    def test_only_new_family_members_added_and_hidden_stays_hidden(self):
        state={};prefs={'hidden':['grok-4.8']}
        first={'grok-4.5':{},'grok-4.3':{},'grok-imagine-image':{}}
        self.assertEqual(m.select_models(first,['grok-4.5'],state,prefs,{'grok'}),['grok-4.5'])
        later={**first,'grok-4.8':{},'grok-4.9':{},'gemini-4-flash':{}}
        self.assertEqual(m.select_models(later,state['selected'],state,prefs,{'grok'}),['grok-4.5','grok-4.9'])
        self.assertNotIn('grok-4.3',state['selected'])
        self.assertNotIn('gemini-4-flash',state['selected'])
        self.assertIn('grok-4.8',state['selected'])

    def test_disappearance_requires_two_successful_observations(self):
        state={}
        m.select_models({'grok-4.5':{}},['grok-4.5'],state,{}, {'grok'})
        self.assertEqual(m.select_models({},state['selected'],state,{}, {'grok'}),['grok-4.5'])
        self.assertEqual(m.select_models({},state['selected'],state,{}, {'grok'}),[])
        self.assertEqual(m.select_models({'grok-4.5':{}},state['selected'],state,{}, {'grok'}),['grok-4.5'])

    def test_default_uses_maximum_and_explicit_reduction_survives_spec_growth(self):
        entry={'slug':'grok-4.7','context_window':128000,'max_context_window':500000}
        self.assertEqual(m.apply_reductions(entry,{}),450000)
        self.assertEqual(entry['context_window'],500000)
        reduced={'context_windows':{'grok-4.7':200000},'compact_limits':{'grok-4.7':150000}}
        entry['max_context_window']=1000000
        self.assertEqual(m.apply_reductions(entry,reduced),150000)
        self.assertEqual(entry['context_window'],200000)
        self.assertEqual(entry['max_context_window'],1000000)

    def test_no_proxy_url_or_html_can_supply_an_arbitrary_spec_origin(self):
        for name in ['../../secret','grok-4.7/../../auth','gemini-4-argon','grok-imagine-image']:
            with self.assertRaises(ValueError):m.specification_url(name)
        with self.assertRaises(p.PatchError):m.parse_spec('grok-4.7','Context window 1M','https://evil.invalid')

    def test_new_native_gpt_generation_can_supply_the_metadata_template(self):
        source={'models':[{'slug':'gpt-7-sol','model_messages':{'instructions_template':'fixture'}}]}
        result=p.make_catalog(source)
        self.assertEqual(result['models'][-1]['slug'],p.MODEL)
        self.assertEqual(result['models'][-1]['model_messages'],source['models'][0]['model_messages'])

    def fixture(self,root):
        baseline=root/'baseline';baseline.mkdir();home=root/'home';home.mkdir();state=root/'state';state.mkdir()
        native={'models':[{'slug':'gpt-6.1-sol','context_window':272000,'max_context_window':872000}]}
        old=p.make_catalog(native);p.save_json(baseline/'desktop-models.json',old)
        config=p.prepared_config('',baseline/'desktop-models.json','account','service')
        (home/'config.toml').write_text(config)
        p.save_json(baseline/'manifest.json',{'format':1,'status':'installed'})
        return baseline,home,state,native

    def test_full_ds_sync_refreshes_cloud_gpt_and_specs_without_proxy_dependency(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline,home,state,native=self.fixture(Path(tmp))
            native['models'].append({'slug':'gpt-new','context_window':200000,'max_context_window':1000000})
            settings={'edition':'ds','app':'unused','codex_home':str(home)}
            result=m.synchronize(settings,state,baseline,fetch_native=lambda *_:native,
                                 fetch_provider=lambda *_:{'deepseek-flash':{}},fetch_document=lambda *_:'CONTEXT LENGTH 2M')
            self.assertEqual(result['routes']['deepseek-flash']['max_context_window'],2097152)
            self.assertEqual(next(x for x in result['catalog']['models'] if x['slug']=='gpt-new')['context_window'],1000000)
            self.assertFalse(any(r['provider']=='easycli-desktop' for r in result['routes'].values()))
            repeated=m.synchronize(settings,state,baseline,fetch_native=lambda *_:native,
                                   fetch_provider=lambda *_:{'deepseek-flash':{}},fetch_document=lambda *_:'CONTEXT LENGTH 2M')
            self.assertEqual(result['digest'],repeated['digest'])

    def test_missing_new_spec_is_held_and_network_outage_retains_installed_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline,home,state,native=self.fixture(Path(tmp))
            settings={'edition':'ds','app':'unused','codex_home':str(home)}
            first=m.synchronize(settings,state,baseline,fetch_native=lambda *_:native,
                                fetch_provider=lambda *_:{'deepseek-flash':{}},fetch_document=lambda *_:'CONTEXT LENGTH 1M')
            def unavailable(*_):raise OSError('offline')
            second=m.synchronize(settings,state,baseline,fetch_native=unavailable,fetch_provider=unavailable,fetch_document=unavailable)
            self.assertIn('deepseek-flash',second['routes'])
            self.assertEqual(second['routes']['deepseek-flash']['max_context_window'],first['routes']['deepseek-flash']['max_context_window'])
            self.assertTrue(second['warnings'])

    def test_global_user_reduction_is_preserved_for_ds(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline,home,state,native=self.fixture(Path(tmp))
            config=home/'config.toml';config.write_text('model_context_window=512000\nmodel_auto_compact_token_limit=448000\n'+config.read_text())
            result=m.synchronize({'edition':'ds','app':'unused','codex_home':str(home)},state,baseline,
                                 fetch_native=lambda *_:native,fetch_provider=lambda *_:{'deepseek-flash':{}},fetch_document=lambda *_:'CONTEXT LENGTH 1M')
            self.assertEqual(result['routes']['deepseek-flash'],{'provider':'deepseek-desktop','context_window':512000,'max_context_window':1048576,'compact_limit':448000})

    def test_all_custom_models_can_be_hidden_without_losing_native_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline,home,state,native=self.fixture(Path(tmp))
            p.save_json(state/'model-preferences.json',{'hidden':['deepseek-flash']})
            value=m.synchronize({'edition':'ds','app':'unused','codex_home':str(home)},state,baseline,
                                fetch_native=lambda *_:native,fetch_provider=lambda *_:{'deepseek-flash':{}},
                                fetch_document=lambda *_:'CONTEXT LENGTH 1M')
            self.assertEqual(value['routes'],{})
            self.assertEqual([e['slug'] for e in value['catalog']['models']],['gpt-6.1-sol'])

    def test_new_unknown_spec_waits_and_installed_manual_reduction_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline,home,state,native=self.fixture(Path(tmp))
            settings={'edition':'ds','app':'unused','codex_home':str(home)}
            first=m.synchronize(settings,state,baseline,fetch_native=lambda *_:native,
                                fetch_provider=lambda *_:{'deepseek-flash':{}},fetch_document=lambda *_:'CONTEXT LENGTH 1M')
            p.save_json(baseline/'synced-models.json',first)
            current=copy.deepcopy(first['catalog']);next(e for e in current['models'] if e['slug']=='deepseek-flash')['context_window']=256000
            p.save_json(baseline/'desktop-models.json',current)
            def docs(url):raise OSError('spec pending')
            later=m.synchronize(settings,state,baseline,fetch_native=lambda *_:native,
                                fetch_provider=lambda *_:{'deepseek-flash':{},'deepseek-v5-flash':{}},fetch_document=docs)
            self.assertNotIn('deepseek-v5-flash',later['routes'])
            self.assertEqual(later['routes']['deepseek-flash']['context_window'],256000)
            self.assertTrue(any('deepseek-v5-flash' in w for w in later['warnings']))

    def test_reasoning_choices_survive_suffixes_and_disabled_native_template(self):
        expected={'grok-4.5':['low','medium','high'],'grok-4.7':['low','medium','high','xhigh'],
                  'gemini-3.8-flash-high':['low','medium','high'],'claude-opus-4-6-thinking':['low','medium','high','max']}
        for model,levels in expected.items():
            entry=m.configure_reasoning({'slug':model,'supports_reasoning_effort_updates':False},['high'])
            self.assertEqual([v['effort'] for v in entry['supported_reasoning_levels']],levels)
            self.assertTrue(entry['supports_reasoning_effort_updates'])
            self.assertEqual(entry['default_reasoning_level'],'high')

    def test_future_model_markdown_supported_levels_and_single_level_default(self):
        spec=m.parse_spec('grok-5','Context window: 1M\n**Reasoning efforts (supported):** `low`, `medium`, `high`, `xhigh`',m.specification_url('grok-5'))
        self.assertEqual(spec['reasoning_levels'],['low','medium','high','xhigh'])
        entry=m.configure_reasoning({'slug':'future-model'},['max'])
        self.assertEqual(entry['default_reasoning_level'],'max')
        self.assertFalse(entry['supports_reasoning_effort_updates'])


if __name__=='__main__':unittest.main()
