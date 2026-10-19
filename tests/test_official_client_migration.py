import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from official_client_config import PROVIDER
from official_client_migration import migrate, rollback


class FakeNative:
    def __init__(self):
        self.threads = {str(i):{'id':str(i), 'cwd':'/original', 'name':'chat '+str(i),
                              'createdAt':1, 'updatedAt':2, 'modelProvider':provider}
                        for i, provider in enumerate(['openai','deepseek-desktop','openai','unmanaged'])}
        self.archived = {'2'}
        for row in self.threads.values():
            row.update(model='deepseek-flash' if row['id'] == '2' else 'gpt-6.1-sol',
                       reasoningEffort='low' if row['id'] == '2' else 'high')
        self.calls = []
        self.fail_once = None

    def request(self, method, params):
        self.calls.append((method, copy.deepcopy(params)))
        if self.fail_once == (method, params.get('threadId')):
            self.fail_once = None
            raise RuntimeError('Synthetic native error')
        if method == 'thread/list':
            return {'data':[copy.deepcopy(t) for t in self.threads.values()
                            if (t['id'] in self.archived) == params['archived']
                            and t['modelProvider'] in params['modelProviders']], 'nextCursor':None}
        tid = params['threadId']
        if method == 'thread/unarchive':
            self.archived.discard(tid)
        elif method == 'thread/archive':
            self.archived.add(tid)
        elif method == 'thread/resume':
            if tid in self.archived:
                raise RuntimeError('Archived thread')
            self.threads[tid]['modelProvider'] = params['modelProvider']
            self.threads[tid]['model'] = params.get('model', 'gpt-6.1-sol')
            self.threads[tid]['reasoningEffort'] = params.get('config', {}).get('model_reasoning_effort', 'high')
            return {'thread':copy.deepcopy(self.threads[tid]), 'modelProvider':params['modelProvider']}
        elif method != 'thread/unsubscribe':
            raise AssertionError('Migration must not generate a turn or compact history')
        return {}


class MigrationTest(unittest.TestCase):
    def test_preserves_history_metadata_archive_and_unmanaged_provider(self):
        native = FakeNative()
        before = copy.deepcopy(native.threads)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'migration.json'
            result = migrate(native, {'openai','deepseek-desktop'}, path)
            self.assertEqual(result['migrated_chats'], 3)
            self.assertEqual(result['inference_turns'], 0)
            self.assertEqual(native.archived, {'2'})
            self.assertEqual(native.threads['3'], before['3'])
            self.assertTrue(all(native.threads[str(i)]['modelProvider'] == PROVIDER for i in range(3)))
            call_count = len(native.calls)
            migrate(native, {'openai','deepseek-desktop'}, path)
            self.assertEqual(len(native.calls), call_count)
            rollback(native, path)
            self.assertEqual(native.threads, before)
            self.assertEqual(native.archived, {'2'})

    def test_failure_restores_archive_and_can_rollback_completed_mutations(self):
        native = FakeNative()
        before = copy.deepcopy(native.threads)
        native.fail_once = ('thread/resume', '2')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'migration.json'
            with self.assertRaises(RuntimeError):
                migrate(native, {'openai','deepseek-desktop'}, path)
            self.assertEqual(native.archived, {'2'})
            rollback(native, path)
            self.assertEqual(native.threads, before)

    def test_failed_migration_resumes_without_inference(self):
        native = FakeNative()
        native.fail_once = ('thread/resume','1')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'migration.json'
            with self.assertRaises(RuntimeError):
                migrate(native, {'openai','deepseek-desktop'}, path)
            self.assertEqual(migrate(native, {'openai','deepseek-desktop'}, path)['status'], 'passed')


if __name__ == '__main__':
    unittest.main()
