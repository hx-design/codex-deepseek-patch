import copy
import http.client
import http.server
import json
import socket
import threading
import time
import unittest
from unittest.mock import patch

import inference_adapter as relay
from inference_transport import accept_websocket, connect_websocket


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode()


def observe_disconnect(connection, disconnected):
    """TCP cancellation may reach Windows as EOF or an explicit reset/abort."""
    try:
        while connection.recv(1):
            pass
    except (ConnectionResetError, ConnectionAbortedError):
        pass
    except OSError:
        return  # Timeouts and unrelated socket failures do not prove closure.
    disconnected.set()


class Upstream(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers['Content-Length']))
        body = json.loads(raw)
        self.server.seen.append((self.path, dict(self.headers), raw, body))
        if self.path.startswith('/redirect/'):
            self.send_response(302)
            self.send_header('Location', 'https://credential-leak.invalid/')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path.startswith('/fail/'):
            self.send_response(429)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        output = [{'id': 'message_' + str(len(self.server.seen)), 'type': 'message', 'role': 'assistant',
                   'content': [{'type': 'output_text', 'text': 'OK'}]}]
        if body.get('tools') and body['tools'][0].get('name') == 'apply_patch':
            output = [{'id': 'fc_fixture', 'type': 'function_call', 'name': 'apply_patch',
                       'call_id': 'call_fixture', 'arguments': '{"input":"exact tool input"}'}]
        response = {'id': 'resp_' + str(len(self.server.seen)), 'status': 'completed',
                    'model': body['model'], 'output': output}
        if not body.get('stream'):
            raw = encoded(response)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('X-Official-Fixture', 'preserved')
            self.end_headers()
            self.wfile.write(raw)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        if self.path.startswith('/hang/'):
            self.server.hanging.set()
            self.connection.settimeout(3)
            observe_disconnect(self.connection, self.server.cancelled)
            return
        events = [{'type': 'response.created', 'response': {**response, 'status': 'in_progress', 'output': []}},
                  {'type': 'response.output_item.done', 'output_index': 0, 'item': output[0]},
                  {'type': 'response.completed', 'response': response}]
        for event in events:
            self.wfile.write(b'event: ' + event['type'].encode() + b'\ndata: ' + encoded(event) + b'\n\n')
            self.wfile.flush()

    def do_GET(self):
        self.server.upgrades.append((self.path, dict(self.headers)))
        ws = accept_websocket(self, response_headers=[('X-Codex-Turn-State', 'fixture-sticky'),
                                                     ('X-Models-Etag', 'fixture-models-etag')])
        try:
            while (message := ws.recv()) is not None:
                opcode, raw = message
                self.server.ws_seen.append((opcode, raw))
                if opcode == 2:
                    ws.send(raw, 2)
                    continue
                body = json.loads(raw)
                if body.get('type') == 'response.cancel':
                    ws.send(encoded({'type': 'response.failed', 'response': {'id': 'gpt-cancelled'}}))
                elif body.get('input') == 'hang':
                    self.server.hanging.set()
                else:
                    ws.send(encoded({'type': 'response.completed', 'response': {'id': 'gpt-fixture', 'output': []}}))
        except OSError:
            pass
        finally:
            self.server.cancelled.set()
            ws.close()


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.upstream = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        self.upstream.daemon_threads = True
        self.upstream.seen, self.upstream.upgrades, self.upstream.ws_seen = [], [], []
        self.upstream.hanging, self.upstream.cancelled = threading.Event(), threading.Event()
        threading.Thread(target=self.upstream.serve_forever, kwargs={'poll_interval': .01}, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.upstream.server_port}'
        self.profile = {'format': 1, 'edition': 'ds', 'identity': 'fixture-relay', 'token': 't' * 40, 'port': 0,
                        'gpt': {'chatgpt_base_url': self.base + '/chatgpt', 'api_base_url': self.base + '/api'},
                        'routes': {'deepseek-fixture': {'base_url': self.base + '/ds',
                                  'credential': {'kind': 'file', 'path': '/synthetic/no-secret'},
                                  'tool_adapter': True, 'flatten_namespaces': True}}}
        self.server = relay.create_server(self.profile, credential_reader=lambda _: 'independent-ds-fixture-key')
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.path = '/' + self.profile['token'] + '/backend-api/codex/responses'
        self.wsurl = f'ws://127.0.0.1:{self.server.server_port}' + self.path

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.upstream.shutdown()
        self.upstream.server_close()

    def request(self, body=None, *, path=None, headers=None, method='POST'):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        conn.request(method, path or self.path, body=encoded(body) if body is not None else None,
                     headers={'Content-Type': 'application/json', **(headers or {})})
        result = conn.getresponse()
        status, response_headers, raw = result.status, dict(result.getheaders()), result.read()
        conn.close()
        return status, response_headers, raw

    def connect(self, **headers):
        return connect_websocket(self.wsurl, headers, timeout=3)[0]

    def receive_terminal(self, ws):
        result = []
        while True:
            opcode, raw = ws.recv()
            self.assertEqual(opcode, 1)
            value = json.loads(raw)
            result.append(value)
            if value['type'] in relay.TERMINAL:
                return result

    def test_health_path_token_host_origin_and_encoding_boundaries(self):
        status, _, raw = self.request(path='/' + self.profile['token'] + '/health', method='GET')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), {'ok': True, 'identity': 'fixture-relay', 'edition': 'ds'})
        for path, headers, expected in [('/wrong/health', {}, 401),
            (self.path, {'Host': 'evil.invalid'}, 403),
            (self.path, {'Origin': 'https://evil.invalid'}, 403),
            (self.path, {'Content-Encoding': 'zstd'}, 415),
            (self.path + '/compact', {}, 501)]:
            with self.subTest(path=path, headers=headers):
                actual, _, _ = self.request({'model': 'deepseek-fixture'}, path=path, headers=headers)
                self.assertEqual(actual, expected)
        self.assertFalse(self.upstream.seen)

    def test_rejected_headers_send_complete_reply_and_drain_body_without_forwarding(self):
        drains = []
        drained = threading.Event()
        original = relay.Handler.drain_rejected_body
        def observed(handler):
            original(handler)
            drains.append(handler.rfile.peek(1) == b'')
            drained.set()
        # Send headers separately: the client can read the error before sending
        # its remaining body, and the server must consume it before closing.
        for header, expected in (("Host: evil.invalid",403),("Content-Encoding: zstd",415)):
            with self.subTest(header=header):
                drained.clear()
                wire = socket.create_connection(('127.0.0.1',self.server.server_port),timeout=2)
                body = encoded({'model':'deepseek-fixture','input':'x'*65536})
                host = '' if header.startswith('Host:') else f'Host: 127.0.0.1:{self.server.server_port}\r\n'
                request = (f'POST {self.path} HTTP/1.1\r\n' + host + header +
                           f'\r\nContent-Length: {len(body)}\r\n\r\n').encode()
                with patch.object(relay.Handler,'drain_rejected_body',observed):
                    wire.sendall(request)
                    response = http.client.HTTPResponse(wire)
                    response.begin()
                    self.assertEqual(response.status,expected)
                    self.assertIn('error',json.loads(response.read()))
                    wire.sendall(body)
                    wire.shutdown(socket.SHUT_WR)
                    self.assertEqual(wire.recv(1),b'')
                    wire.close()
                    self.assertTrue(drained.wait(1))
                self.assertTrue(drains[-1])
        self.assertFalse(self.upstream.seen)

    def test_explicit_catalog_rejects_unknown_http_ws_and_hint_without_any_upstream(self):
        # Names are arbitrary: membership, rather than vendor-looking prefixes,
        # decides whether an unregistered model can reach the official service.
        self.server.profile['official_models'] = ['official-native-id']
        relay.validate_profile(self.server.profile)
        for model in ('removed-external','gpt-looking-but-unregistered'):
            with self.subTest(model=model):
                status, _, raw = self.request({'model':model},headers={'Authorization':'Bearer official-fixture'})
                self.assertEqual(status,400)
                error = json.loads(raw)['error']
                self.assertEqual(error['code'],'model_unavailable')
                self.assertIn('Select an available model',error['message'])
                ws = self.connect(**{'x-codex-routing-hint':'model=' + model})
                try:
                    self.assertFalse(self.upstream.upgrades)
                    ws.send(encoded({'type':'response.create','model':model,'input':[]}))
                    self.assertEqual(self.receive_terminal(ws)[-1]['error']['code'],'model_unavailable')
                    ws.send(encoded({'type':'response.create','model':model,'input':[],'generate':False}))
                    self.assertEqual(self.receive_terminal(ws)[-1]['error']['code'],'model_unavailable')
                finally:
                    ws.close()
        self.assertFalse(self.upstream.seen)
        self.assertFalse(self.upstream.upgrades)
        self.assertFalse(self.upstream.ws_seen)
        # Both explicitly registered classes still use their existing routes.
        self.assertEqual(self.request({'model':'official-native-id'})[0],200)
        self.assertEqual(self.upstream.seen[-1][0],'/api/responses')
        self.assertEqual(self.request({'model':'deepseek-fixture'})[0],200)
        self.assertEqual(self.upstream.seen[-1][0],'/ds/responses')
        ws = self.connect(**{'x-codex-routing-hint':'model=official-native-id'})
        try:
            ws.send(encoded({'type':'response.create','model':'official-native-id','input':[]}))
            self.assertEqual(self.receive_terminal(ws)[-1]['type'],'response.completed')
            ws.send(encoded({'type':'response.create','model':'deepseek-fixture','input':[]}))
            self.assertEqual(self.receive_terminal(ws)[-1]['type'],'response.completed')
        finally:
            ws.close()

    def test_official_http_body_headers_and_auth_reach_only_official_route(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        raw = b'{ "model" : "gpt-fixture", "input":"literal whitespace" }'
        headers = {'Authorization': 'Bearer original-official-fixture', 'ChatGPT-Account-ID': 'account-fixture',
                   'X-OpenAI-Fixture': 'native', 'Proxy-Authorization': 'never-forward'}
        conn.request('POST', self.path, body=raw, headers=headers)
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader('X-Official-Fixture'), 'preserved')
        response.read()
        conn.close()
        path, actual, forwarded, _ = self.upstream.seen[-1]
        self.assertEqual(path, '/chatgpt/responses')
        self.assertEqual(forwarded, raw)
        self.assertEqual(actual['Authorization'], headers['Authorization'])
        self.assertEqual(actual['X-OpenAI-Fixture'], 'native')
        self.assertNotIn('Proxy-Authorization', actual)
        self.request({'model': 'gpt-fixture'}, headers={'Authorization': 'Bearer api-fixture'})
        self.assertEqual(self.upstream.seen[-1][0], '/api/responses')

    def test_registered_route_has_independent_credential_and_tool_conversion(self):
        body = {'model': 'deepseek-fixture', 'input': [], 'tools': [{'type': 'custom', 'name': 'apply_patch'}]}
        status, _, raw = self.request(body, headers={'Authorization': 'Bearer official-secret',
            'ChatGPT-Account-ID': 'official-account', 'X-OpenAI-Fixture': 'secret-context'})
        self.assertEqual(status, 200)
        output = json.loads(raw)['output'][0]
        self.assertEqual((output['type'], output['input'], output['call_id']),
                         ('custom_tool_call', 'exact tool input', 'call_fixture'))
        path, headers, _, actual = self.upstream.seen[-1]
        self.assertEqual(path, '/ds/responses')
        self.assertEqual(headers['Authorization'], 'Bearer independent-ds-fixture-key')
        self.assertNotIn('ChatGPT-Account-ID', headers)
        self.assertFalse(any(k.lower().startswith('x-openai') for k in headers))
        self.assertEqual(actual['tools'][0]['type'], 'function')

    def test_anonymous_local_routes_omit_auth_in_http_and_websocket_translation(self):
        for reference in (None, {'kind': 'none'}):
            route = self.server.profile['routes']['deepseek-fixture']
            if reference is None:
                route.pop('credential', None)
            else:
                route['credential'] = reference
            relay.validate_profile(self.server.profile)
            self.request({'model': 'deepseek-fixture'}, headers={'Authorization': 'Bearer official-must-not-leak'})
            self.assertNotIn('Authorization', self.upstream.seen[-1][1])
            ws = self.connect(Authorization='Bearer official-must-not-leak')
            ws.send(encoded({'type': 'response.create', 'model': 'deepseek-fixture', 'input': []}))
            self.receive_terminal(ws)
            self.assertNotIn('Authorization', self.upstream.seen[-1][1])
            ws.close()

    def test_normalize_chat_only_and_remote_compaction(self):
        route = self.server.profile['routes']['deepseek-fixture']
        route.update(normalize_instructions=True, chat_only=True)
        self.request({'model': 'deepseek-fixture', 'instructions': 'initial',
                      'input': [{'role': 'developer', 'content': [{'text': 'developer text'}]},
                                {'role': 'user', 'content': 'hello'}],
                      'tools': [{'type': 'function', 'name': 'unused'}], 'tool_choice': 'auto'})
        actual = self.upstream.seen[-1][3]
        self.assertEqual(actual['instructions'], 'initial\n\ndeveloper text')
        self.assertEqual(actual['input'], [{'role': 'user', 'content': 'hello'}])
        self.assertNotIn('tools', actual)
        count = len(self.upstream.seen)
        status, _, raw = self.request({'model': 'deepseek-fixture', 'input': [{'type': 'compaction_trigger'}]})
        self.assertEqual(status, 501)
        self.assertEqual(json.loads(raw)['error']['code'], 'remote_compaction_unsupported')
        self.assertEqual(len(self.upstream.seen), count)

    def test_ws_warmup_and_incremental_native_history(self):
        ws = self.connect(Authorization='Bearer official-fixture')
        first = {'model': 'deepseek-fixture', 'type': 'response.create', 'input': [{'role': 'user', 'content': 'hello'}]}
        ws.send(encoded({**first, 'generate': False}))
        warmup = self.receive_terminal(ws)[-1]['response']
        self.assertEqual(self.upstream.seen, [])
        ws.send(encoded({**first, 'previous_response_id': warmup['id'], 'input': []}))
        complete = self.receive_terminal(ws)[-1]['response']
        self.assertEqual(self.upstream.seen[-1][3]['input'], first['input'])
        delta = [{'role': 'user', 'content': 'continue'}]
        ws.send(encoded({**first, 'previous_response_id': complete['id'], 'input': delta}))
        self.receive_terminal(ws)
        self.assertEqual(self.upstream.seen[-1][3]['input'], first['input'] + complete['output'] + delta)
        ws.send(encoded({**first, 'previous_response_id': 'unknown', 'input': delta}))
        self.assertEqual(self.receive_terminal(ws)[-1]['error']['code'], 'previous_response_not_found')
        ws.close()

    def test_ws_model_change_rejects_delta_and_accepts_full_input(self):
        self.server.profile['routes']['another-model'] = copy.deepcopy(self.profile['routes']['deepseek-fixture'])
        ws = self.connect()
        ws.send(encoded({'type': 'response.create', 'model': 'deepseek-fixture', 'input': []}))
        complete = self.receive_terminal(ws)[-1]['response']
        ws.send(encoded({'type': 'response.create', 'model': 'another-model', 'previous_response_id': complete['id'], 'input': []}))
        self.assertEqual(self.receive_terminal(ws)[-1]['error']['code'], 'previous_response_not_found')
        ws.send(encoded({'type': 'response.create', 'model': 'another-model', 'input': []}))
        self.assertEqual(self.receive_terminal(ws)[-1]['type'], 'response.completed')
        ws.close()

    def test_ws_custom_tool_roundtrip_preserves_native_output(self):
        ws = self.connect()
        body = {'type': 'response.create', 'model': 'deepseek-fixture', 'input': [],
                'tools': [{'type': 'custom', 'name': 'apply_patch'}]}
        ws.send(encoded(body))
        events = self.receive_terminal(ws)
        output = events[-1]['response']['output'][0]
        self.assertEqual(output['type'], 'custom_tool_call')
        self.assertEqual(output['input'], 'exact tool input')
        self.assertEqual(output['call_id'], 'call_fixture')
        ws.send(encoded({**body, 'previous_response_id': events[-1]['response']['id'],
                         'input': [{'type': 'custom_tool_call_output', 'call_id': 'call_fixture', 'output': 'done'}]}))
        self.receive_terminal(ws)
        self.assertEqual(self.upstream.seen[-1][3]['input'][0]['type'], 'function_call')
        self.assertEqual(self.upstream.seen[-1][3]['input'][1]['type'], 'function_call_output')
        ws.close()

    def test_ws_official_connection_is_reused_binary_and_cancel_are_transparent(self):
        ws = self.connect(Authorization='Bearer original-official-fixture', **{'ChatGPT-Account-ID': 'account-fixture'})
        for _ in range(2):
            ws.send(encoded({'type': 'response.create', 'model': 'gpt-fixture', 'input': []}))
            self.assertEqual(self.receive_terminal(ws)[-1]['type'], 'response.completed')
        self.assertEqual(len(self.upstream.upgrades), 1)
        self.assertEqual(self.upstream.upgrades[0][0], '/chatgpt/responses')
        self.assertEqual(self.upstream.upgrades[0][1]['Authorization'], 'Bearer original-official-fixture')
        ws.send(b'\x00\xffopaque', opcode=2)
        self.assertEqual(ws.recv(), (2, b'\x00\xffopaque'))
        ws.send(encoded({'type': 'response.create', 'model': 'gpt-fixture', 'input': 'hang'}))
        self.assertTrue(self.upstream.hanging.wait(1))
        ws.send(encoded({'type': 'response.cancel'}))
        self.assertEqual(self.receive_terminal(ws)[-1]['type'], 'response.failed')
        ws.close()
        self.assertTrue(self.upstream.cancelled.wait(1))

    def test_initial_gpt_hints_preserve_upstream_handshake_sticky_headers(self):
        for hint in ({'x-codex-routing-hint': 'model=gpt-fixture;tier=priority'},
                     {'x-codex-turn-metadata': json.dumps({'model': 'gpt-fixture'})}):
            ws, headers = connect_websocket(self.wsurl, hint, timeout=3)
            self.assertIn(('X-Codex-Turn-State', 'fixture-sticky'), headers)
            self.assertIn(('X-Models-Etag', 'fixture-models-etag'), headers)
            ws.send(encoded({'type': 'response.create', 'model': 'gpt-fixture', 'input': []}))
            self.receive_terminal(ws)
            # Frame routing still selects the registered route after this 101.
            ws.send(encoded({'type': 'response.create', 'model': 'deepseek-fixture', 'input': []}))
            self.receive_terminal(ws)
            self.assertEqual(self.upstream.seen[-1][0], '/ds/responses')
            ws.close()

    def test_ws_close_and_cancel_close_owned_http_connection_promptly(self):
        self.server.profile['routes']['deepseek-fixture']['base_url'] = self.base + '/hang'
        for cancel in (False, True):
            self.upstream.hanging.clear()
            self.upstream.cancelled.clear()
            ws = self.connect()
            ws.send(encoded({'type': 'response.create', 'model': 'deepseek-fixture', 'input': []}))
            self.assertTrue(self.upstream.hanging.wait(1))
            if cancel:
                ws.send(encoded({'type': 'response.cancel'}))
                self.assertEqual(self.receive_terminal(ws)[-1]['error']['code'], 'request_cancelled')
            else:
                ws.close()
            self.assertTrue(self.upstream.cancelled.wait(1), 'upstream socket remained open after cancellation')
            ws.close()

    def test_redirects_and_upstream_error_codes(self):
        route = self.server.profile['routes']['deepseek-fixture']
        route['base_url'] = self.base + '/redirect'
        status, _, raw = self.request({'model': 'deepseek-fixture'})
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(raw)['error']['code'], 'upstream_redirect')
        route['base_url'] = self.base + '/fail'
        ws = self.connect()
        ws.send(encoded({'type': 'response.create', 'model': 'deepseek-fixture'}))
        self.assertEqual(self.receive_terminal(ws)[-1]['error']['code'], 'upstream_http_429')
        ws.close()

    def test_credential_failure_is_safe_and_never_falls_back_to_official_auth(self):
        def unavailable(_):
            raise relay.RelayError(503, 'credential_unavailable', 'Credential unavailable')
        self.server.credential_reader = unavailable
        status, _, raw = self.request({'model': 'deepseek-fixture'}, headers={'Authorization': 'Bearer official-secret'})
        self.assertEqual(status, 503)
        self.assertNotIn(b'official-secret', raw)
        self.assertEqual(self.upstream.seen, [])


class ProfileTests(unittest.TestCase):
    def test_official_model_registry_requires_exact_unique_string_ids(self):
        profile = {'format':1,'edition':'ds','identity':'fixture','token':'t'*40,'port':0,
                   'routes':{},'gpt':{'chatgpt_base_url':'https://example.invalid/codex',
                                     'api_base_url':'https://example.invalid/v1'}}
        for value in ('gpt-model',[None],['model','model'],['']):
            with self.subTest(value=value), self.assertRaises(ValueError):
                relay.validate_profile({**profile,'official_models':value})
        relay.validate_profile({**profile,'official_models':[]})
        relay.validate_profile({**profile,'official_models':['arbitrary-native-id']})

    def test_disconnect_fixture_accepts_only_eof_reset_or_abort_not_timeout(self):
        from unittest.mock import Mock
        for result in (b'',ConnectionResetError(10054,'fixture reset'),
                       ConnectionAbortedError(10053,'fixture abort'),
                       TimeoutError('still open'),OSError('unrelated socket failure')):
            with self.subTest(result=type(result).__name__):
                connection = Mock()
                if isinstance(result,Exception):
                    connection.recv.side_effect = result
                else:
                    connection.recv.return_value = result
                disconnected = threading.Event()
                observe_disconnect(connection,disconnected)
                self.assertEqual(disconnected.is_set(), isinstance(result,(bytes,ConnectionResetError,ConnectionAbortedError)))

    def test_existing_proxy_discovery_explicit_override_and_loopback_bypass(self):
        with patch('urllib.request.getproxies', return_value={'https': 'http://system-proxy.invalid:7890'}), \
             patch('urllib.request.proxy_bypass', return_value=False):
            self.assertEqual(relay.network_proxy('https://api.openai.com/v1', {}), 'http://system-proxy.invalid:7890')
            self.assertEqual(relay.network_proxy('wss://api.openai.com/v1', {'network_proxy': 'http://explicit.invalid:80'}),
                             'http://explicit.invalid:80')
            self.assertIsNone(relay.network_proxy('http://127.0.0.1:1234/v1', {'network_proxy': 'http://never.invalid:80'}))
            self.assertIsNone(relay.network_proxy('http://192.168.1.50:11434/v1', {'network_proxy': ''}))
        with patch('urllib.request.proxy_bypass', return_value=True):
            self.assertIsNone(relay.network_proxy('https://api.openai.com/v1', {}))

    def test_remote_http_and_short_token_are_rejected(self):
        profile = {'format': 1, 'edition': 'proxy', 'identity': 'fixture', 'token': 'x' * 32, 'port': 0,
                   'gpt': {'chatgpt_base_url': 'https://chatgpt.com/backend-api/codex',
                           'api_base_url': 'https://api.openai.com/v1'}, 'routes': {}}
        self.assertEqual(relay.validate_profile(copy.deepcopy(profile)), profile)
        for change in ({'token': 'short'}, {'gpt': {**profile['gpt'], 'api_base_url': 'http://public.invalid/v1'}}):
            with self.assertRaises(ValueError):
                relay.validate_profile({**profile, **change})
        local = {**profile, 'routes': {'local-fixture': {'base_url': 'http://192.168.1.50:11434/v1'}}}
        self.assertEqual(relay.validate_profile(local), local)

    def test_credential_reader_file_and_proxy_errors_are_sanitized(self):
        with patch('inference_adapter.Path.read_text', return_value='fixture-file-key\n'):
            self.assertEqual(relay.read_credential({'kind': 'file', 'path': '/fixture'}), 'fixture-file-key')
        with patch('inference_adapter.Path.read_text', side_effect=ValueError('SECRET NEVER IN ERROR')):
            with self.assertRaises(relay.RelayError) as ctx:
                relay.read_credential({'kind': 'file', 'path': '/fixture'})
            self.assertNotIn('SECRET', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
