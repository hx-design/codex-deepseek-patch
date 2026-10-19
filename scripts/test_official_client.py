"""Unmodified native CLI + loopback adapter regression, with synthetic auth/tools.

No real credentials or external inference. The same run(app, reportdir, edition)
entrypoint is used on macOS and Windows; --mcp reuses the 160-tool fixture.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import http.server
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import inference_adapter
from official_client_config import adapt_catalog, prepare_config
from inference_transport import accept_websocket
from responses_compat import SEARCH_NAME, ToolAdapter
import patch
from scripts.test_context import ResponsesStub, Rpc
from scripts.test_offline import isolated_environment, launch_options, probe_command, stop_probe
from scripts.test_switching import finish
from scripts.test_tools import Stub as ToolStub, mcp_fixture

PROVIDER = 'codex-model-adapter'
GPT = 'gpt-6.1-sol'
DS = 'deepseek-flash'


def mcp_runtime(executable=None):
    """Stdio helpers need python.exe even when an updater owns pythonw.exe.

    The official native client owns spawning this command with piped standard
    streams and its Windows CREATE_NO_WINDOW process option.
    """
    command = Path(executable or sys.executable)
    if command.name.lower() == 'pythonw.exe':
        command = command.with_name('python.exe')
        if not command.is_file():
            raise ValueError('Console Python is required for the stdio MCP fixture')
    return str(command)


class Fixture(ToolStub):
    def do_CONNECT(self):
        self.server.blocked_external += 1
        self.send_error(503, 'External connections disabled in this probe')

    def do_GET(self):
        if self.headers.get('Upgrade', '').lower() != 'websocket':
            self.send_error(404)
            return
        self.server.upgrades += 1
        ws = accept_websocket(self, response_headers=[('X-Codex-Turn-State', 'synthetic-sticky-state')])
        try:
            while (message := ws.recv()) is not None:
                opcode, raw = message
                patch.require(opcode == 1, 'Native Responses request must be JSON text')
                body = json.loads(raw)
                self.record(body, raw, 'websocket')
                response, events = self.simple_events(body, warmup=body.get('generate') is False)
                for index, event in enumerate(events):
                    ws.send(json.dumps({**event, 'sequence_number': index}))
        except OSError:
            pass
        finally:
            ws.close()

    def record(self, body, raw, transport):
        self.server.wire.append({'model': body.get('model'), 'transport': transport, 'path': self.path,
            'request_bytes': len(raw), 'effort': body.get('reasoning', {}).get('effort'),
            'generate': body.get('generate'), 'previous_response_id': bool(body.get('previous_response_id')),
            'tool_count': len(body.get('tools', [])), 'input_types': [v.get('type', 'message')
                for v in body.get('input', []) if isinstance(v, dict)],
            'auth_is_official_fixture': self.headers.get('Authorization') == 'Bearer official-api-fixture',
            'auth_is_route_fixture': self.headers.get('Authorization') == 'Bearer independent-route-fixture',
            'auth_is_anonymous': self.headers.get('Authorization') is None,
            'official_account_header': self.headers.get('ChatGPT-Account-ID') is not None})

    def simple_events(self, body, warmup=False, item=None):
        index = len(self.server.wire)
        usage = 0 if warmup else self.server.next_usage
        if not warmup:
            self.server.next_usage = 10
        item = item or self.message('CONTEXT_OK')
        response = {'id': 'resp_native_' + str(index), 'object': 'response', 'created_at': int(time.time()),
                    'status': 'completed', 'model': body['model'], 'output': [] if warmup else [item],
                    'usage': {'input_tokens': usage, 'input_tokens_details': {'cached_tokens': 0},
                              'output_tokens': 0 if warmup else 3, 'total_tokens': usage + (0 if warmup else 3)}}
        events = [{'type': 'response.created', 'response': {**response, 'status': 'in_progress', 'output': []}}]
        if not warmup:
            events += [{'type': 'response.output_item.added', 'output_index': 0, 'item': {**item, 'status': 'in_progress'}},
                       {'type': 'response.output_item.done', 'output_index': 0, 'item': item}]
        events.append({'type': 'response.completed', 'response': response})
        return response, events

    def chat_response(self, raw):
        body = json.loads(raw)
        tools = [v['function'] for v in body.get('tools', [])]
        messages = body['messages']
        self.record({'model':body['model'], 'reasoning':{'effort':body.get('reasoning_effort')},
                     'tools':tools, 'input':messages}, raw, 'http')
        phase, error = self.server.phase, None
        call = None
        text = 'CONTEXT_OK'
        if self.server.tool_mode and phase == 'search':
            if len(raw) > 120000 or not any(v['name'] == SEARCH_NAME for v in tools):
                error = 'Chat route eager catalog or missing native discovery'
            call = (SEARCH_NAME, 'chat_search', '{"query":"probe_tool_42","limit":1}')
            self.server.phase = 'call'
        elif self.server.tool_mode and phase == 'call':
            alias = ToolAdapter.alias('mcp__bulk', 'probe_tool_42')
            loaded = [v['name'] for v in tools if v['name'].startswith('mcp__bulk__')]
            if loaded != [alias]:
                error = 'Chat route did not expose exactly the discovered MCP tool'
            call = (alias, 'chat_tool', '{"value":"check"}')
            self.server.phase = 'answer'
        elif self.server.tool_mode and phase == 'answer':
            if not any(v.get('role') == 'tool' and 'LAZY_TOOL_OK' in str(v.get('content')) for v in messages):
                error = 'Chat route native discovered tool did not execute'
            text = 'LAZY_TOOL_OK'
            self.server.phase = 'search'
        elif phase == 'custom_call':
            declaration = next(v for v in tools if v['name'].endswith('apply_patch'))
            call = (declaration['name'], 'chat_custom', json.dumps({'input':
                '*** Begin Patch\n*** Add File: CHAT_CUSTOM_TOOL_OK.txt\n+CHAT_CUSTOM_TOOL_OK\n*** End Patch'}))
            self.server.phase = 'custom_answer'
        elif phase == 'custom_answer':
            if not any(v.get('role') == 'tool' and v.get('tool_call_id') == 'chat_custom' for v in messages):
                error = 'Chat route custom tool result did not return'
            text = 'CHAT_CUSTOM_TOOL_OK'
            self.server.phase = 'message'
        if self.server.tool_mode or phase.startswith('custom_'):
            self.server.records.append({'model':body['model'],'request_bytes':len(raw),'phase':phase,'error':error})
        if error:
            payload = json.dumps({'error':{'message':error}}).encode()
            self.send_response(400)
            self.send_header('Content-Length',str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        delta = ({'tool_calls':[{'index':0,'id':call[1],'type':'function',
            'function':{'name':call[0],'arguments':call[2]}}]} if call else {'role':'assistant','content':text})
        chunks = [{'id':'chat_fixture_'+str(len(self.server.wire)), 'model':body['model'],
                   'choices':[{'index':0,'delta':delta,'finish_reason':None}]},
                  {'choices':[{'index':0,'delta':{},'finish_reason':'tool_calls' if call else 'stop'}],
                   'usage':{'prompt_tokens':3000,'completion_tokens':3,'total_tokens':3003}}]
        payload = b''.join(b'data: '+json.dumps(v).encode()+b'\n\n' for v in chunks)+b'data: [DONE]\n\n'
        self.send_response(200)
        self.send_header('Content-Type','text/event-stream')
        self.send_header('Content-Length',str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        if self.path.endswith('/chat/completions'):
            return self.chat_response(raw)
        if not self.path.endswith('/responses'):
            self.server.auxiliary_requests += 1
            self.send_error(503)
            return
        patch.require(not self.headers.get('Content-Encoding'), 'Native compression must be disabled in config')
        body = json.loads(raw)
        self.record(body, raw, 'http')
        compact = not body.get('tools')
        original_reader = self.rfile
        try:
            self.rfile = io.BytesIO(raw)
            if self.server.tool_mode and self.server.phase not in ('custom_call', 'custom_answer'):
                return super().do_POST()
            if self.server.phase == 'custom_call' and not compact:
                candidates = [(None, t) for t in body.get('tools', []) if t.get('type') == 'function']
                candidates += [(t['name'], child) for t in body.get('tools', []) if t.get('type') == 'namespace'
                               for child in t.get('tools', [])]
                namespace, tool = next((ns, t) for ns, t in candidates if t.get('name', '').endswith('apply_patch'))
                item = {'type': 'function_call', 'id': 'fc_custom_fixture', 'call_id': 'custom_fixture',
                        'status': 'completed', 'name': tool['name'], 'arguments': json.dumps({'input':
                        '*** Begin Patch\n*** Add File: CUSTOM_TOOL_OK.txt\n+CUSTOM_TOOL_OK\n*** End Patch'})}
                if namespace:
                    item['namespace'] = namespace
                self.server.phase = 'custom_answer'
            elif self.server.phase == 'custom_answer' and not compact:
                patch.require(any(v.get('type') == 'function_call_output' and v.get('call_id') == 'custom_fixture'
                    for v in body.get('input', [])), 'Custom tool output did not return through native execution')
                item = self.message('CUSTOM_TOOL_OK')
                self.server.phase = 'message'
            else:
                item = self.message('A concise synthetic summary. Continue the pending test.' if compact else 'CONTEXT_OK')
            if compact:
                self.server.compactions += 1
            _, events = self.simple_events(body, item=item)
            payload = ''.join('event: ' + v['type'] + '\ndata: ' + json.dumps({**v, 'sequence_number': n}) + '\n\n'
                              for n, v in enumerate(events)).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        finally:
            self.rfile = original_reader


class ObservedSession(inference_adapter.WebSocketSession):
    """Record only synthetic frame metadata, without altering relay behavior."""
    def receive(self, opcode, payload):
        if opcode == 1:
            body = json.loads(payload)
            self.handler.server.native_frames.append({'model': body.get('model'),
                'type': body.get('type'), 'generate': body.get('generate'), 'bytes': len(payload)})
        return super().receive(opcode, payload)


def _windows(events):
    return sorted({event['params']['tokenUsage']['modelContextWindow'] for event in events
                   if event.get('method') == 'thread/tokenUsage/updated'})


def _compact(rpc, tid):
    rpc.request('thread/compact/start', {'threadId': tid})
    values, deadline = [], time.monotonic() + 60
    while time.monotonic() < deadline:
        event = rpc.pending.pop(0) if rpc.pending else rpc.receive_event(max(.1, deadline - time.monotonic()))
        if event.get('params', {}).get('threadId') != tid:
            continue
        values.append(event)
        if event.get('method') == 'turn/completed':
            patch.require(event['params']['turn']['status'] == 'completed', 'Native plain compaction failed')
            return values
    raise TimeoutError('Native plain compaction did not finish')


def require_tool_success(events, kind, marker=None):
    """Require native execution evidence, not an error quoting the tool input."""
    items = [event.get('params', {}).get('item', {}) for event in events
             if event.get('method') == 'item/completed']
    for item in items:
        if item.get('type') != kind or item.get('status') != 'completed':
            continue
        if kind == 'commandExecution':
            if type(item.get('exitCode')) is not int or item['exitCode'] != 0:
                continue
            if marker not in item.get('aggregatedOutput', '').splitlines():
                continue
        return item
    raise patch.PatchError('Native ' + kind + ' did not report successful execution')


def run(app, reportdir, edition):
    app, reportdir = Path(app), Path(reportdir)
    patch.require(edition in ('ds', 'proxy'), 'Unknown edition')
    reportdir.mkdir(parents=True, exist_ok=True)
    report = {'status': 'failed', 'edition': edition, 'external_inference_turns': 0,
              'real_credentials_used': False, 'provider': PROVIDER, 'mcp_tools': 160, 'checks': {}}
    report.update(runner_runtime=Path(sys.executable).name, mcp_runtime=Path(mcp_runtime()).name)
    process, adapter, upstream = None, None, None
    started_servers = []

    def stop_owned():
        nonlocal process
        if process:
            stop_probe(process)
            process = None

    def close_servers():
        nonlocal adapter, upstream
        if upstream:
            report.setdefault('wire', upstream.wire)
            report.setdefault('tool_records', upstream.records)
        for server in (adapter, upstream):
            if server:
                if server in started_servers:
                    server.shutdown()
                server.server_close()
        adapter = upstream = None

    try:
        with tempfile.TemporaryDirectory(prefix='codex-official-client-') as tmp, contextlib.ExitStack() as owned:
            base = Path(tmp)
            env = isolated_environment(base)
            work = base / 'work'
            work.mkdir()
            raw = subprocess.check_output([str(patch.cli_path(app)), 'debug', 'models', '--bundled'],
                env=env, stderr=subprocess.PIPE, timeout=60, **patch.background_options())
            native = json.loads(raw)
            template = patch.catalog_template(native)
            models = [(GPT, 272000), (DS, 1048576)]
            if edition == 'proxy':
                models += [('grok-4.7', 500000), ('gemini-3.8-flash-high', 1048576), ('local-probe', 256000)]
            entries = []
            for slug, window in models:
                entry = copy.deepcopy(template)
                levels = ['low', 'medium', 'high'] if slug != DS else ['low', 'high', 'max']
                entry.update(slug=slug, display_name=slug, context_window=window, max_context_window=window,
                    auto_compact_token_limit=int(window * .7), effective_context_window_percent=100,
                    supported_reasoning_levels=[{'effort': v, 'description': v} for v in levels],
                    default_reasoning_level='high', supports_reasoning_effort_updates=True,
                    use_responses_lite=slug == GPT, prefer_websockets=slug == GPT, tool_mode=None,
                    experimental_supported_tools=[], visibility='list',
                    supports_search_tool=True, shell_type='unified_exec')
                entries.append(entry)
            catalog = base / 'models.json'
            catalog_routes = {slug: {'context_window': window, 'max_context_window': window,
                'compact_limit': int(window * .7)} for slug, window in models if slug != GPT}
            patch.save_json(catalog, adapt_catalog({'models': entries}, catalog_routes, {}))
            upstream = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
            upstream.daemon_threads = True
            upstream.records, upstream.requests, upstream.wire = [], [], []
            upstream.phase, upstream.answer_usage, upstream.next_usage = 'message', 3000, 10
            upstream.tool_mode, upstream.compactions, upstream.upgrades = False, 0, 0
            upstream.blocked_external, upstream.auxiliary_requests = 0, 0
            endpoint = 'http://127.0.0.1:' + str(upstream.server_port)
            profile = {'format': 1, 'edition': edition, 'identity': 'synthetic-official-client-' + edition,
                'token': 'synthetic-local-token-' + 'x' * 32, 'port': 0, 'official_models': [GPT],
                'gpt': {'chatgpt_base_url': endpoint + '/official', 'api_base_url': endpoint + '/official'},
                'routes': {slug: {'base_url': endpoint + '/route', 'credential': {'kind': 'file', 'path': str(base / 'route.key')},
                    'tool_adapter': True, 'flatten_namespaces': slug == 'local-probe',
                    'normalize_instructions': slug == 'local-probe', 'chat_only': False} for slug, _ in models if slug != GPT}}
            if 'local-probe' in profile['routes']:
                profile['routes']['local-probe'].update(credential={'kind':'none'},
                    wire_api='chat_completions', supports_reasoning_effort=True)
            (base / 'route.key').write_text('independent-route-fixture')
            adapter = inference_adapter.create_server(profile, session_factory=ObservedSession)
            adapter.native_frames = []
            for server in (upstream, adapter):
                threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True).start()
                started_servers.append(server)
            owned.callback(close_servers)
            relay_url = 'http://127.0.0.1:' + str(adapter.server_port) + '/' + profile['token'] + '/backend-api/codex'
            current = ('cli_auth_credentials_store="file"\nweb_search="disabled"\nchatgpt_base_url=' + json.dumps(endpoint) + '\n'
                '[features]\nmemories=false\napps=false\n'
                '[mcp_servers.bulk]\ncommand=' + json.dumps(mcp_runtime()) + '\nargs=' +
                json.dumps([str(Path(__file__).resolve()), '--mcp']) + '\n')
            config, _ = prepare_config(current, catalog, relay_url, mode='native-summary')
            (base / 'home/config.toml').write_text(config, encoding='utf-8')
            patch.save_json(base / 'home/auth.json', {'OPENAI_API_KEY': 'official-api-fixture'})
            env.update(OPENAI_API_KEY='official-api-fixture', HTTP_PROXY=endpoint, HTTPS_PROXY=endpoint,
                       ALL_PROXY=endpoint, NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost')
            with (reportdir / ('official-client-' + edition + '.log')).open('w', encoding='utf-8') as log:
                process = subprocess.Popen(probe_command(app), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                    text=True, encoding='utf-8', errors='replace', env=env, cwd=work, **launch_options())
                owned.callback(stop_owned)
                rpc = Rpc(process)
                rpc.request('initialize', {'clientInfo': {'name': 'official_client_probe', 'version': '1'},
                                          'capabilities': {'experimentalApi': True}})
                listed = rpc.request('model/list', {'includeHidden': False, 'limit': 100})['data']
                for slug, _ in models:
                    item = next(v for v in listed if v.get('model') == slug)
                    patch.require(len(item.get('supportedReasoningEfforts', [])) >= 2, 'Native reasoning choices missing: ' + slug)
                report['checks']['native_catalog_reasoning'] = 'passed'
                # Only this disposable synthetic home/work directory uses the
                # Windows unrestricted fixture. Never change user permissions.
                sandbox = 'danger-full-access' if sys.platform == 'win32' else 'workspace-write'
                report['sandbox_requested'] = sandbox
                report['sandbox_scope'] = 'isolated temporary home and work directory'
                initial = rpc.request('thread/start', {'model': DS, 'modelProvider': PROVIDER, 'cwd': str(work),
                    'approvalPolicy': 'never', 'sandbox': sandbox, 'baseInstructions': 'This is a synthetic local regression.',
                    'config': {'web_search': 'disabled', 'features.memories': False, 'features.apps': False}})
                report['sandbox_effective'] = initial.get('sandbox')
                if sys.platform == 'win32':
                    patch.require(isinstance(initial.get('sandbox'), dict) and initial['sandbox'].get('type') == 'dangerFullAccess',
                                  'Native Windows did not grant the isolated synthetic fixture sandbox')
                tid = initial['thread']['id']
                turns = []

                def turn(model, label, effort='high'):
                    before_wire = len(upstream.wire)
                    result = rpc.request('turn/start', {'threadId': tid, 'model': model, 'effort': effort,
                        'input': [{'type': 'text', 'text': 'Run the synthetic local test and reply briefly.'}]})
                    events = finish(rpc, tid, result)
                    windows = _windows(events)
                    expected = dict(models)[model]
                    patch.require(windows == [expected] and expected > 128000, 'Native catalog window changed: ' + model + ' ' + str(windows))
                    turns.append({'model': model, 'label': label, 'context_windows': windows,
                        'effort': effort,
                        'compaction': any(v.get('params', {}).get('item', {}).get('type') == 'contextCompaction' for v in events)})
                    requests = [v for v in upstream.wire[before_wire:] if v['generate'] is not False]
                    patch.require(requests and all(v['effort'] == effort for v in requests),
                                  'Selected reasoning effort did not reach inference: ' + model + ' ' + effort)
                    return events

                turn(DS, 'registered model local WS prewarm')
                for model, _ in models:
                    turn(model, 'same-thread switch')
                turn(GPT, 'return to GPT')
                report['checks']['same_thread_model_switching'] = 'passed'
                target = DS if edition == 'ds' else 'grok-4.7'
                turn(target, 'enter compaction model')
                turn(target, 'reasoning switch to low', 'low')
                turn(target, 'reasoning switch back to high', 'high')
                report['checks']['reasoning_high_low_high'] = 'passed'
                upstream.next_usage = int(dict(models)[target] * .7) + 100
                turn(target, 'synthetic usage above compaction threshold')
                auto = turn(target, 'automatic plain compaction')
                patch.require(any(v.get('params', {}).get('item', {}).get('type') == 'contextCompaction' for v in auto),
                              'Automatic native plain compaction did not run')
                _compact(rpc, tid)
                turn(target, 'after manual plain compaction')
                turn(GPT, 'return to GPT after plain compaction')
                turn(target, 'return to registered model after plain compaction')
                report['checks']['plain_auto_manual_compaction_windows'] = 'passed'
                upstream.tool_mode, upstream.phase = True, 'search'
                before = len(upstream.records)
                turn(target, '160-tool lazy discovery and execution')
                records = upstream.records[before:]
                patch.require([v['phase'] for v in records] == ['search', 'call', 'answer'] and not any(v['error'] for v in records),
                              'Native lazy MCP discovery/execution failed')
                report['checks']['native_lazy_160_mcp_tools'] = 'passed'
                upstream.phase = 'integer_call'
                integer_events = turn(target, 'integral-decimal native tool arguments')
                integer = require_tool_success(integer_events, 'commandExecution', 'INTEGER_TOOL_OK')
                report['integer_execution'] = {'exit_code':integer['exitCode'], 'exact_marker_line':True}
                patch.require(not any(v['error'] for v in upstream.records), 'Native integral-decimal execution failed')
                report['checks']['native_integer_arguments'] = 'passed'
                upstream.tool_mode, upstream.phase = False, 'custom_call'
                custom_events = turn(target, 'native custom apply_patch execution')
                custom = require_tool_success(custom_events, 'fileChange')
                patch.require((work / 'CUSTOM_TOOL_OK.txt').read_text(encoding='utf-8').strip() == 'CUSTOM_TOOL_OK', 'Custom apply_patch did not execute')
                report['custom_execution'] = {'status':custom['status'], 'file_content_exact':True}
                report['checks']['native_custom_tool_execution'] = 'passed'
                if edition == 'proxy':
                    upstream.tool_mode, upstream.phase = True, 'search'
                    turn('local-probe', 'flattened namespace lazy tool execution')
                    patch.require(not any(v['error'] for v in upstream.records), 'Local flattened namespace tool execution failed')
                    report['checks']['local_flattened_tools'] = 'passed'
                    report['checks']['native_chat_completions_lazy_tools'] = 'passed'
                    upstream.tool_mode, upstream.phase = False, 'custom_call'
                    chat_custom_events = turn('local-probe', 'Chat Completions native custom tool execution')
                    require_tool_success(chat_custom_events, 'fileChange')
                    patch.require((work / 'CHAT_CUSTOM_TOOL_OK.txt').read_text().strip() == 'CHAT_CUSTOM_TOOL_OK',
                                  'Chat Completions custom apply_patch did not execute')
                    turn(GPT, 'return to GPT after Chat Completions tools')
                    report['checks']['native_chat_custom_tools_return_gpt'] = 'passed'
                thread = rpc.request('thread/read', {'threadId': tid, 'includeTurns': True})['thread']
                patch.require(thread['id'] == tid and len(thread['turns']) >= len(turns), 'Native thread history was lost')
                official = [v for v in upstream.wire if v['model'] == GPT]
                routed = [v for v in upstream.wire if v['model'] != GPT]
                patch.require(official and all(v['auth_is_official_fixture'] and not v['auth_is_route_fixture'] for v in official),
                              'Official GPT credential boundary failed')
                patch.require(routed and all((v['auth_is_anonymous'] if v['model'] == 'local-probe' else v['auth_is_route_fixture']) and not v['auth_is_official_fixture']
                    and not v['official_account_header'] and v['transport'] == 'http' for v in routed),
                              'Registered model credential/HTTP boundary failed')
                patch.require(any(v['transport'] == 'websocket' for v in official), 'Native GPT WebSocket transport was not exercised')
                report['checks']['gpt_ws_registered_http_auth_isolation'] = 'passed'
                patch.require(any(v['model'] == DS and v['generate'] is False for v in adapter.native_frames),
                              'Native registered-model WebSocket prewarm was not exercised')
                patch.require(not any(v['model'] != GPT and v['generate'] is False for v in upstream.wire),
                              'Registered-model prewarm reached the inference endpoint')
                report['checks']['native_registered_ws_local_prewarm'] = 'passed'
                report.update(status='passed', turns=turns, retained_turns=len(thread['turns']),
                    upstream_websocket_connections=upstream.upgrades, compact_requests=upstream.compactions,
                    blocked_external_connections=upstream.blocked_external, wire=upstream.wire,
                    tool_records=upstream.records, native_frames=adapter.native_frames)
    except Exception as exc:
        report['status'] = 'failed'
        report['error_type'], report['error'] = type(exc).__name__, str(exc)[:1000]
        if upstream:
            report.update(wire=upstream.wire, tool_records=upstream.records)
        raise
    finally:
        stop_owned()
        close_servers()
        patch.save_json(reportdir / ('official-client-' + edition + '-result.json'), report)
    return report


if __name__ == '__main__':
    if sys.argv[1:] == ['--mcp']:
        mcp_fixture()
    else:
        parser = argparse.ArgumentParser()
        parser.add_argument('--app', type=Path, required=True)
        parser.add_argument('--reportdir', type=Path, required=True)
        parser.add_argument('--edition', choices=['ds', 'proxy'], required=True)
        args = parser.parse_args()
        result = run(args.app, args.reportdir, args.edition)
        print(json.dumps({'status': result['status'], 'edition': args.edition, 'checks': result['checks']}), flush=True)
