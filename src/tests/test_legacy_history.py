import concurrent.futures
import copy
import http.client
import http.server
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from inference_adapter import create_server
from inference_transport import connect_websocket
from legacy_history import LegacyHistory, LegacyHistoryError, visible_reasoning


OPAQUE = {'type': 'compaction', 'id': 'cmp_fixture', 'encrypted_content': 'synthetic-ciphertext-never-cache'}
HEADERS = {'Authorization': 'Bearer official-secret-never-cache', 'ChatGPT-Account-ID': 'account-fixture'}


def process_conversion(directory, count, results):
    def summarize(*args):
        with count.get_lock():
            count.value += 1
        time.sleep(.1)
        return 'Visible task facts across processes.'
    try:
        LegacyHistory(directory, 'gpt-fixture', summarize).convert({'input': [OPAQUE]}, HEADERS)
        results.put('ok')
    except Exception as exc:
        results.put(type(exc).__name__)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / 'private-history'
        self.calls = []

    def converter(self, **kwargs):
        def summarize(item, model, headers, cancelled):
            self.calls.append((item, model, dict(headers)))
            return 'Task goal MARKER_FACT; keep the user constraints; next run the pending verification.'
        return LegacyHistory(self.directory, kwargs.pop('model', 'gpt-fixture'),
                             kwargs.pop('summarizer', summarize), **kwargs)

    def body(self, item=None):
        return {'model': 'deepseek-fixture', 'input': [copy.deepcopy(item or OPAQUE),
            {'role': 'user', 'content': 'latest-visible-history-never-cache'}]}

    def test_lazy_cache_account_model_and_exact_opaque_identity(self):
        converter = self.converter()
        body = self.body()
        converted = converter.convert(body, HEADERS)
        self.assertEqual(body['input'][0], OPAQUE)
        self.assertEqual(converted['input'][0]['role'], 'assistant')
        self.assertIn('MARKER_FACT', converted['input'][0]['content'][0]['text'])
        self.assertEqual(converter.convert(body, HEADERS), converted)
        self.assertEqual(len(self.calls), 1)
        converter.convert(body, {**HEADERS, 'ChatGPT-Account-ID': 'another-account'})
        self.converter(model='gpt-another').convert(body, HEADERS)
        converter.convert(self.body({**OPAQUE, 'id': 'another-id'}), HEADERS)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(len(list(self.directory.glob('*.json'))), 4)
        for path in self.directory.glob('*.json'):
            raw = path.read_text(encoding='utf-8')
            for secret in ('synthetic-ciphertext-never-cache', 'official-secret-never-cache',
                           'latest-visible-history-never-cache', 'account-fixture', 'cmp_fixture'):
                self.assertNotIn(secret, raw)
            self.assertEqual(len(path.stem), 64)
        if os.name != 'nt':
            self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
            self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in self.directory.iterdir()))

    def test_empty_lock_file_is_locked_without_initializing_or_overwriting_bytes(self):
        converter = self.converter()
        path = self.directory / '.conversion.lock'
        with patch('legacy_history.os.write', side_effect=PermissionError('A competing handle owns byte zero')) as write:
            for _ in range(2):
                with converter._lock(threading.Event()):
                    self.assertEqual(path.stat().st_size, 0)
        write.assert_not_called()

    def test_lock_initialization_failure_closes_its_file_descriptor(self):
        converter = self.converter()
        original_open = os.open
        descriptors = []
        def opened(*args, **kwargs):
            descriptor = original_open(*args, **kwargs)
            descriptors.append(descriptor)
            return descriptor
        try:
            with patch('legacy_history.os.open', side_effect=opened), \
                 patch('legacy_history.os.chmod', side_effect=PermissionError('Synthetic initialization failure')):
                with self.assertRaises(PermissionError), converter._lock(threading.Event()):
                    self.fail('Lock initialization must not enter its body')
            self.assertEqual(len(descriptors), 1)
            with self.assertRaises(OSError):
                os.fstat(descriptors[0])
        finally:
            # A regressed implementation must not leak handles into test cleanup.
            for descriptor in descriptors:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    @unittest.skipUnless(os.name == 'nt', 'Windows byte-range locking semantics')
    def test_existing_empty_byte_lock_never_gets_overwritten_before_cancellation(self):
        import msvcrt
        converter = self.converter()
        path = self.directory / '.conversion.lock'
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            cancelled = threading.Event()
            cancelled.set()
            try:
                with self.assertRaises(LegacyHistoryError) as context, converter._lock(cancelled):
                    self.fail('A cancelled competing handle must not acquire the lock')
                self.assertEqual(context.exception.code, 'request_cancelled')
                self.assertEqual(os.fstat(descriptor).st_size, 0)
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(descriptor)

    def test_multiple_instances_convert_once_across_concurrent_threads(self):
        count, lock = [], threading.Lock()
        def summarize(*args):
            with lock:
                count.append(True)
            time.sleep(.05)
            return 'Visible task facts only.'
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.converter(summarizer=summarize).convert(self.body(), HEADERS), range(8)))
        self.assertEqual(len(count), 1)
        self.assertTrue(all(v == results[0] for v in results))
        self.assertEqual(list(self.directory.glob('*.tmp')), [])

    def test_multiple_processes_share_the_single_conversion_lock(self):
        context = multiprocessing.get_context('spawn')
        count, results = context.Value('i', 0), context.Queue()
        processes = [context.Process(target=process_conversion, args=(str(self.directory), count, results)) for _ in range(2)]
        try:
            for process in processes:
                process.start()
            self.assertEqual([results.get(timeout=10) for _ in processes], ['ok', 'ok'])
            self.assertEqual(count.value, 1)
        finally:
            for process in processes:
                process.join(5)
                if process.is_alive():
                    process.terminate()
                    process.join()
            results.close()

    def test_failure_or_cancellation_is_not_cached(self):
        def fail(*args):
            raise LegacyHistoryError('legacy_summary_failed', 'Official summary failed')
        with self.assertRaises(LegacyHistoryError):
            self.converter(summarizer=fail).convert(self.body(), HEADERS)
        self.assertFalse(list(self.directory.glob('*.json')))
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(LegacyHistoryError) as context:
            self.converter().convert(self.body(), HEADERS, cancelled=cancelled)
        self.assertEqual(context.exception.code, 'request_cancelled')
        self.assertFalse(self.calls)

    def test_expiry_count_and_byte_limits(self):
        now = [1000.0]
        converter = self.converter(clock=lambda: now[0], max_age=10, max_entries=2, max_bytes=600)
        converter.convert(self.body(), HEADERS)
        now[0] += 11
        converter.convert(self.body(), HEADERS)
        self.assertEqual(len(self.calls), 2)
        for number in range(4):
            converter.convert(self.body({**OPAQUE, 'id': 'cmp_' + str(number)}), HEADERS)
        paths = list(self.directory.glob('*.json'))
        self.assertLessEqual(len(paths), 2)
        self.assertLessEqual(sum(p.stat().st_size for p in paths), 600)

    def test_invalid_or_oversized_summary_and_absent_auth_fail(self):
        for summary in ('', None, 'x' * 30, HEADERS['Authorization'][7:]):
            with self.subTest(summary=summary), self.assertRaises(LegacyHistoryError):
                self.converter(summarizer=lambda *args: summary, max_summary_bytes=20).convert(self.body(), HEADERS)
        with self.assertRaisesRegex(LegacyHistoryError, 'authentication data'):
            self.converter(summarizer=lambda *args: HEADERS['Authorization'][7:]).convert(self.body(), HEADERS)
        with self.assertRaises(LegacyHistoryError) as context:
            self.converter().convert(self.body(), {})
        self.assertEqual(context.exception.status, 401)
        self.assertFalse(list(self.directory.glob('*.json')))

    def test_misconfigured_existing_directory_does_not_touch_unrelated_files(self):
        self.directory.mkdir()
        unrelated = self.directory / 'personal-settings.json'
        unrelated.write_text('{"keep":"exactly"}')
        original_mode = self.directory.stat().st_mode & 0o777
        with self.assertRaises(ValueError):
            self.converter()
        self.assertEqual(unrelated.read_text(), '{"keep":"exactly"}')
        self.assertEqual(self.directory.stat().st_mode & 0o777, original_mode)

    def test_only_existing_public_reasoning_summary_can_be_forwarded(self):
        item = {'type': 'reasoning', 'id': 'reasoning-fixture', 'encrypted_content': 'private-opaque',
                'summary': [{'type': 'summary_text', 'text': 'Public summary already returned by the official model.'}]}
        converted = visible_reasoning(self.body(item))
        self.assertNotIn('encrypted_content', converted['input'][0])
        self.assertEqual(converted['input'][0]['role'], 'assistant')
        self.assertEqual(converted['input'][0]['content'][0]['text'], item['summary'][0]['text'])
        self.assertIn('encrypted_content', item)
        with self.assertRaises(LegacyHistoryError) as context:
            visible_reasoning(self.body({'type': 'unknown', 'encrypted_content': 'opaque'}))
        self.assertEqual(context.exception.status, 501)

    def test_private_reasoning_is_dropped_while_all_visible_history_is_preserved(self):
        visible = [{'role': 'user', 'content': 'Keep exact task goal.'},
            {'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Completed task facts.'}]},
            {'type': 'function_call', 'call_id': 'same-call', 'name': 'read_file', 'arguments': '{}'},
            {'type': 'function_call_output', 'call_id': 'same-call', 'output': 'Exact visible tool result.'}]
        private = {'type': 'reasoning', 'id': 'private-state', 'summary': [], 'encrypted_content': 'opaque-private-reasoning'}
        original = {'input': [visible[0], private] + visible[1:]}
        self.assertEqual(visible_reasoning(original)['input'], visible)
        self.assertEqual(original['input'][1], private)
        public = {**private, 'content': [{'type': 'reasoning_text', 'text': 'Already public content.'}]}
        converted = visible_reasoning({'input': [public]})['input'][0]
        self.assertEqual(converted['content'][0]['text'], public['content'][0]['text'])
        self.assertEqual(converted['role'], 'assistant')
        self.assertNotIn('encrypted_content', converted)


class SummaryFixture(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers['Content-Length']))
        body = json.loads(raw)
        self.server.records.append((self.path, dict(self.headers), raw, body))
        if self.path.startswith('/official/') and self.server.fail_summary:
            self.send_response(getattr(self.server, 'summary_error_status', 403))
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        text = 'Task goal MARKER_FACT; preserve constraints; pending verification.' if self.path.startswith('/official/') else 'TARGET_OK'
        response = {'id': 'resp_fixture', 'status': 'completed', 'model': body['model'],
            'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': text}]}]}
        if body.get('stream'):
            raw = ('data: ' + json.dumps({'type': 'response.completed', 'response': response}) + '\n\n').encode()
            content_type = 'text/event-stream'
        else:
            raw, content_type = json.dumps(response).encode(), 'application/json'
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class AdapterConversionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.upstream = http.server.ThreadingHTTPServer(('127.0.0.1', 0), SummaryFixture)
        self.upstream.daemon_threads = True
        self.upstream.records, self.upstream.fail_summary = [], False
        endpoint = f'http://127.0.0.1:{self.upstream.server_port}'
        self.profile = {'format': 1, 'edition': 'ds', 'identity': 'legacy-fixture', 'token': 'x' * 40, 'port': 0,
            'gpt': {'chatgpt_base_url': endpoint + '/official', 'api_base_url': endpoint + '/official'},
            'routes': {'deepseek-fixture': {'base_url': endpoint + '/target',
                       'credential': {'kind': 'file', 'path': '/synthetic-only'}}},
            'legacy_history_dir': str(Path(self.temporary.name) / 'cache'), 'summary_model': 'gpt-fixture'}
        self.adapter = create_server(self.profile, credential_reader=lambda _: 'independent-target-key')
        for server in (self.upstream, self.adapter):
            threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True).start()
        self.path = '/' + self.profile['token'] + '/backend-api/codex/responses'

    def tearDown(self):
        for server in (self.adapter, self.upstream):
            server.shutdown()
            server.server_close()
        self.temporary.cleanup()

    def request(self, model='deepseek-fixture', **changes):
        body = {'model': model, 'input': [OPAQUE, {'role': 'user', 'content': 'continue task'}], **changes}
        connection = http.client.HTTPConnection('127.0.0.1', self.adapter.server_port, timeout=3)
        connection.request('POST', self.path, body=json.dumps(body).encode(), headers={**HEADERS,
            'X-OpenAI-Internal-Codex-Responses-Lite': 'true', 'X-Codex-Routing-Hint': 'model=deepseek-fixture'})
        response = connection.getresponse()
        status, raw = response.status, response.read()
        connection.close()
        return status, raw

    def test_http_lazy_conversion_cache_and_strict_auth_separation(self):
        self.assertEqual(self.request()[0], 200)
        self.assertEqual(self.request()[0], 200)
        records = self.upstream.records
        self.assertEqual([v[0] for v in records], ['/official/responses', '/target/responses', '/target/responses'])
        _, official_headers, _, summary_request = records[0]
        self.assertEqual(official_headers['Authorization'], HEADERS['Authorization'])
        self.assertEqual(official_headers['ChatGPT-Account-ID'], HEADERS['ChatGPT-Account-ID'])
        self.assertNotIn('X-OpenAI-Internal-Codex-Responses-Lite', official_headers)
        self.assertNotIn('X-Codex-Routing-Hint', official_headers)
        self.assertEqual(summary_request['tools'], [])
        self.assertEqual(summary_request['tool_choice'], 'none')
        self.assertNotIn('summary', summary_request['reasoning'])
        self.assertTrue(summary_request['stream'])
        self.assertEqual(summary_request['input'][0], OPAQUE)
        for _, headers, raw, body in records[1:]:
            self.assertEqual(headers['Authorization'], 'Bearer independent-target-key')
            self.assertNotIn('ChatGPT-Account-ID', headers)
            self.assertNotIn(b'synthetic-ciphertext', raw)
            self.assertIn('MARKER_FACT', body['input'][0]['content'][0]['text'])

    def test_failed_official_conversion_never_sends_to_target_or_caches(self):
        self.upstream.fail_summary = True
        status, raw = self.request()
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(raw)['error']['code'], 'legacy_summary_http_403')
        self.assertEqual(len(self.upstream.records), 1)
        self.assertFalse(list(Path(self.profile['legacy_history_dir']).glob('*.json')))

    def test_exhausted_gpt_quota_only_blocks_uncached_opaque_history(self):
        self.upstream.fail_summary = True
        self.upstream.summary_error_status = 429
        status, raw = self.request()
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(raw)['error']['code'], 'legacy_summary_http_429')
        self.assertEqual([v[0] for v in self.upstream.records], ['/official/responses'])
        self.upstream.records.clear()
        self.assertEqual(self.request(input=[{'role':'user','content':'continue visible task'}])[0], 200)
        self.assertEqual([v[0] for v in self.upstream.records], ['/target/responses'])
        self.assertEqual(self.upstream.records[0][1]['Authorization'], 'Bearer independent-target-key')

    def test_cached_handoff_remains_usable_when_gpt_quota_is_exhausted(self):
        self.assertEqual(self.request()[0], 200)
        self.upstream.records.clear()
        self.upstream.fail_summary = True
        self.upstream.summary_error_status = 429
        self.assertEqual(self.request()[0], 200)
        self.assertEqual([v[0] for v in self.upstream.records], ['/target/responses'])

    def test_gpt_history_is_untouched_and_unconfigured_route_stays_501(self):
        self.assertEqual(self.request(model='gpt-original')[0], 200)
        self.assertEqual(self.upstream.records[-1][3]['input'][0], OPAQUE)
        self.assertFalse(Path(self.profile['legacy_history_dir']).exists())
        self.adapter.profile.pop('legacy_history_dir')
        self.adapter.profile.pop('summary_model')
        self.assertEqual(self.request()[0], 501)
        self.assertEqual(len(self.upstream.records), 1)

    def test_websocket_converts_once_and_reuses_summary_for_incremental_input(self):
        ws, _ = connect_websocket(f'ws://127.0.0.1:{self.adapter.server_port}' + self.path, HEADERS, timeout=3)
        body = {'type': 'response.create', 'model': 'deepseek-fixture', 'input': [OPAQUE]}
        ws.send(json.dumps(body))
        first = json.loads(ws.recv()[1])
        self.assertEqual(first['type'], 'response.completed')
        ws.send(json.dumps({**body, 'previous_response_id': first['response']['id'],
                            'input': [{'role': 'user', 'content': 'next action'}]}))
        self.assertEqual(json.loads(ws.recv()[1])['type'], 'response.completed')
        self.assertEqual([v[0] for v in self.upstream.records],
                         ['/official/responses', '/target/responses', '/target/responses'])
        ws.close()

    def test_remote_compaction_trigger_is_rejected_before_any_summary_cost(self):
        self.assertEqual(self.request(input=[OPAQUE, {'type': 'compaction_trigger'}])[0], 501)
        self.assertFalse(self.upstream.records)


if __name__ == '__main__':
    unittest.main()
