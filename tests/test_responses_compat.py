import copy
import io
import json
import unittest

from responses_compat import ToolAdapter, SEARCH_NAME


SEARCH = {'type': 'tool_search', 'execution': 'client', 'description': 'Find tools',
          'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']}}
LOADED = {'type': 'namespace', 'name': 'files', 'description': 'File tools', 'tools': [
    {'type': 'function', 'name': 'read', 'description': 'Read file', 'defer_loading': True,
     'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}}}]}


class ToolAdapterTests(unittest.TestCase):
    def test_search_round_trip_preserves_call_identity_and_loads_only_matches(self):
        adapter = ToolAdapter({'tools': [SEARCH]})
        function = adapter.body['tools'][0]
        self.assertEqual((function['type'], function['name']), ('function', SEARCH_NAME))
        native = adapter.output_item({'type': 'function_call', 'name': SEARCH_NAME, 'id': 'fc_a',
                                      'call_id': 'call_a', 'arguments': '{"query":"read","limit":1.0}'})
        self.assertEqual(native['arguments'], {'query': 'read', 'limit': 1})
        source = {'tools': [SEARCH], 'input': [native, {'type': 'tool_search_output',
                  'call_id': 'call_a', 'tools': [LOADED]}]}
        before = copy.deepcopy(source)
        next_call = ToolAdapter(source).body
        self.assertEqual(source, before)
        self.assertEqual([t['name'] for t in next_call['tools']], [SEARCH_NAME, 'files'])
        self.assertNotIn('defer_loading', next_call['tools'][1]['tools'][0])
        self.assertEqual(json.loads(next_call['input'][0]['arguments']), native['arguments'])
        self.assertEqual(next_call['input'][1]['call_id'], 'call_a')
        self.assertEqual(json.loads(next_call['input'][1]['output']), [LOADED])

    def test_repeated_search_does_not_duplicate_tools_or_discard_new_matches(self):
        extra = copy.deepcopy(LOADED); extra['tools'][0]['name'] = 'write'
        body = ToolAdapter({'tools': [SEARCH, LOADED], 'input': [
            {'type': 'tool_search_output', 'call_id': 'c', 'tools': [LOADED, extra]}]}).body
        self.assertEqual([t['name'] for t in body['tools'][1]['tools']], ['read', 'write'])

    def test_compaction_does_not_reenable_tools_from_old_discovery_history(self):
        for tools in (None, []):
            source={'input':[{'type':'tool_search_output','call_id':'c','tools':[LOADED]}]}
            if tools is not None:source['tools']=tools
            result=ToolAdapter(source).body
            self.assertFalse(result.get('tools'))

    def test_function_only_namespace_round_trip_and_history(self):
        source={'tools':[SEARCH], 'input':[{'type':'tool_search_output','call_id':'s','tools':[LOADED]},
            {'type':'function_call','namespace':'files','name':'read','call_id':'c','arguments':'{"path":"a"}'}]}
        adapter=ToolAdapter(source,flatten_namespaces=True)
        function=adapter.body['tools'][-1]
        self.assertEqual((function['type'],function['name']),('function','files__read'))
        self.assertEqual(adapter.body['input'][-1]['name'],'files__read')
        self.assertNotIn('namespace',adapter.body['input'][-1])
        native=adapter.output_item({'type':'function_call','name':'files__read','call_id':'c','arguments':'{"path":"a"}'})
        self.assertEqual(native,source['input'][-1])
        self.assertLessEqual(len(ToolAdapter.alias('x'*80,'y'*80)),64)

    def test_custom_tool_input_is_data_and_returns_as_native_call(self):
        source = {'tools': [{'type': 'custom', 'name': 'apply_patch', 'description': 'Patch files'}]}
        adapter = ToolAdapter(source)
        payload = '*** Begin Patch\n*** Add File: a\n+ok\n*** End Patch'
        native = adapter.output_item({'type': 'function_call', 'name': 'apply_patch', 'call_id': 'c',
                                      'arguments': json.dumps({'input': payload})})
        self.assertEqual(native['type'], 'custom_tool_call')
        self.assertEqual(native['input'], payload)
        history = ToolAdapter({'input': [native, {'type': 'custom_tool_call_output',
                              'call_id': 'c', 'output': 'done'}]}).body['input']
        self.assertEqual(json.loads(history[0]['arguments']), {'input': payload})
        self.assertEqual(history[1]['type'], 'function_call_output')

    def test_stream_filters_fragmented_arguments_and_converts_final_response(self):
        adapter = ToolAdapter({'tools': [SEARCH]})
        item = {'type': 'function_call', 'name': SEARCH_NAME, 'id': 'fc_1', 'call_id': 'call_1', 'arguments': ''}
        done = {**item, 'arguments': '{"query":"read"}'}
        events = [
            {'type': 'response.output_item.added', 'output_index': 0, 'item': item},
            {'type': 'response.function_call_arguments.delta', 'item_id': 'fc_1', 'delta': '{"query":'},
            {'type': 'response.function_call_arguments.delta', 'item_id': 'fc_1', 'delta': '"read"}'},
            {'type': 'response.function_call_arguments.done', 'item_id': 'fc_1', 'arguments': done['arguments']},
            {'type': 'response.output_item.done', 'output_index': 0, 'item': done},
            {'type': 'response.completed', 'response': {'output': [done], 'usage': {'input_tokens': 12}}},
        ]
        raw = ''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
        output = b''.join(adapter.stream(io.BytesIO(raw)))
        values = [json.loads(l[5:]) for l in output.splitlines() if l.startswith(b'data:')]
        self.assertEqual(len(values), 3)
        self.assertEqual([e['sequence_number'] for e in values], [0, 1, 2])
        self.assertEqual(values[1]['item']['arguments'], {'query': 'read'})
        self.assertEqual(values[1]['item']['call_id'], 'call_1')
        self.assertEqual(values[-1]['response']['output'][0]['type'], 'tool_search_call')
        self.assertEqual(values[-1]['response']['usage']['input_tokens'], 12)

    def test_unrelated_function_and_text_events_pass_unchanged(self):
        event = {'type': 'response.output_item.done', 'item': {'type': 'function_call', 'name': 'shell',
                 'arguments': '{"cmd":"pwd"}', 'call_id': 'normal'}}
        self.assertEqual(ToolAdapter({'tools': [SEARCH]}).events(event), [event])

    def test_invalid_native_call_and_alias_collision_are_explicit_errors(self):
        with self.assertRaises(ValueError):
            ToolAdapter({'tools': [{'type': 'function', 'name': SEARCH_NAME}]})
        adapter = ToolAdapter({'tools': [SEARCH]})
        with self.assertRaises(ValueError):
            adapter.output_item({'type': 'function_call', 'name': SEARCH_NAME, 'arguments': '{}'})


if __name__ == '__main__':
    unittest.main()
