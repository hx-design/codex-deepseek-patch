import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest

from local_history import read_visible_history, summarize_records, thread_identity, visible_record
from legacy_history import LegacyHistoryError

THREAD = '11111111-1111-4111-8111-111111111111'
OPAQUE = {'type':'compaction','id':'fixture','encrypted_content':'cipher-never-forward'}


class LocalHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name)
        self.file=self.home/'sessions'/'2026'/'rollout-test-{}.jsonl'.format(THREAD)
        self.file.parent.mkdir(parents=True);self.cancelled=threading.Event()
        self.rows=[{'type':'session_meta','payload':{'id':THREAD}},
                   {'type':'response_item','payload':{'type':'message','role':'user','content':'GOAL_VISIBLE'}},
                   {'type':'response_item','payload':{'type':'function_call','name':'read_file','call_id':'c','arguments':'{}'}},
                   {'type':'response_item','payload':{'type':'function_call_output','call_id':'c','output':'TOOL_VISIBLE'}},
                   {'type':'compacted','payload':{'replacement_history':[OPAQUE]}},
                   {'type':'response_item','payload':{'type':'message','role':'user','content':'AFTER_MUST_NOT_REPLAY'}}]
        self.write()

    def tearDown(self):self.tmp.cleanup()
    def write(self):self.file.write_text(''.join(json.dumps(v)+'\n' for v in self.rows))
    def read(self):return read_visible_history(self.home,THREAD,OPAQUE,cancelled=self.cancelled)

    def test_only_matching_prefix_is_read_and_source_is_unchanged(self):
        before=self.file.read_bytes();result=self.read()
        self.assertIn('GOAL_VISIBLE',json.dumps(result));self.assertIn('TOOL_VISIBLE',json.dumps(result))
        self.assertNotIn('AFTER_MUST_NOT_REPLAY',json.dumps(result));self.assertNotIn('cipher',json.dumps(result))
        self.assertEqual(self.file.read_bytes(),before)

    def test_archived_file_and_native_metadata_identity(self):
        target=self.home/'archived_sessions'/self.file.name;target.parent.mkdir();self.file.rename(target)
        self.assertEqual(len(self.read()),3)
        self.assertEqual(thread_identity({'X-Codex-Turn-Metadata':json.dumps({'thread_id':THREAD,'session_id':THREAD})}),THREAD)

    def test_missing_mismatched_and_ambiguous_identities_fail(self):
        for headers in ({},{'thread_id':'../../private'}, {'thread_id':THREAD,'session_id':'22222222-2222-4222-8222-222222222222'},
                        {'x-codex-turn-metadata':'bad json'}):
            with self.subTest(headers=headers),self.assertRaises(LegacyHistoryError):thread_identity(headers)
        self.rows[0]['payload']['id']='different';self.write()
        with self.assertRaises(LegacyHistoryError):self.read()

    def test_missing_or_different_compaction_never_uses_a_similar_chat(self):
        self.rows[4]['payload']['replacement_history'][0]=dict(OPAQUE,encrypted_content='other');self.write()
        with self.assertRaises(LegacyHistoryError):self.read()

    def test_opaque_only_history_and_unverifiable_rollback_fail(self):
        for rows in ([self.rows[0],self.rows[4]],self.rows[:3]+[
            {'type':'event_msg','payload':{'type':'thread_rolled_back','num_turns':1}}]+self.rows[3:]):
            self.rows=rows;self.write()
            with self.assertRaises(LegacyHistoryError):self.read()

    def test_malformed_or_partial_records_fail_without_skipping(self):
        self.file.write_bytes(self.file.read_bytes().replace(b'GOAL_VISIBLE',b'GOAL_VISIBLE\n'))
        with self.assertRaises(LegacyHistoryError):self.read()
        self.write();self.file.write_bytes(self.file.read_bytes().split(b'\n')[0])
        with self.assertRaises(LegacyHistoryError):self.read()

    def test_symlink_and_duplicate_sources_are_not_followed(self):
        other=self.home/'elsewhere';self.file.rename(other)
        try:self.file.symlink_to(other)
        except OSError:self.skipTest('Symlinks unavailable')
        with self.assertRaises(LegacyHistoryError):self.read()
        self.file.unlink();other.rename(self.file)
        duplicate=self.home/'archived_sessions'/self.file.name;duplicate.parent.mkdir();duplicate.write_bytes(self.file.read_bytes())
        with self.assertRaises(LegacyHistoryError):self.read()

    def test_private_reasoning_and_media_payload_are_not_recovered(self):
        self.assertIsNone(visible_record({'type':'reasoning','encrypted_content':'private','summary':[]}))
        self.assertIsNone(visible_record({'type':'message','role':'assistant','channel':'analysis','content':'private'}))
        result=visible_record({'type':'message','role':'user','content':[{'type':'input_image','image_url':'data:image/png;base64,private-pixels'}]})
        self.assertNotIn('private-pixels',json.dumps(result))

    def test_paginated_fork_recovers_parent_only_to_exact_fork_boundary(self):
        parent='22222222-2222-4222-8222-222222222222'
        parent_path=self.file.with_name('rollout-'+parent+'.jsonl')
        prefix=[{'type':'session_meta','payload':{'id':parent},'ordinal':0},
                {'type':'response_item','payload':{'type':'message','role':'user','content':'PARENT_GOAL'},'ordinal':1}]
        raw=''.join(json.dumps(v)+'\n' for v in prefix).encode()
        tail={'type':'response_item','payload':{'type':'message','role':'user','content':'OTHER_BRANCH_SECRET'},'ordinal':2}
        parent_path.write_bytes(raw+(json.dumps(tail)+'\n').encode())
        self.rows[0]['payload'].update(history_base={'thread_id':parent,'end_ordinal_exclusive':2,'end_byte_offset':len(raw)},forked_from_id=parent)
        for ordinal,row in enumerate(self.rows,2):row['ordinal']=ordinal
        self.write();result=json.dumps(self.read())
        self.assertIn('PARENT_GOAL',result);self.assertIn('TOOL_VISIBLE',result)
        self.assertNotIn('OTHER_BRANCH_SECRET',result)
        self.rows[0]['payload']['history_base']['end_byte_offset']-=1;self.write()
        with self.assertRaises(LegacyHistoryError):self.read()

    def test_missing_paginated_prefix_is_not_treated_as_complete(self):
        for ordinal,row in enumerate(self.rows,20):row['ordinal']=ordinal
        self.write()
        with self.assertRaises(LegacyHistoryError):self.read()

    def test_selected_window_chunking_keeps_every_byte_and_reduces(self):
        source='开始🙂ABC'*2200;seen=[];calls=[]
        def summarize(text,tokens,size,level,index,total):
            calls.append((level,index,total))
            if level==0:seen.append(text)
            self.assertLessEqual(len(text.encode()),4096-512-2048)
            return 'Compact visible facts.'
        result=summarize_records([{'text':source}],4096,summarize,cancelled=self.cancelled)
        self.assertEqual(''.join(seen),json.dumps({'text':source},ensure_ascii=False,separators=(',',':')))
        self.assertEqual(result,'Compact visible facts.');self.assertTrue(any(v[0]>0 for v in calls))

    def test_cancelled_and_invalid_model_summaries_are_not_accepted(self):
        self.cancelled.set()
        with self.assertRaises(LegacyHistoryError):self.read()
        with self.assertRaises(LegacyHistoryError):summarize_records([{'x':'data'}],32768,lambda *a:'OK',cancelled=self.cancelled)
        self.cancelled.clear()
        for result in ('',None,'x'*25000):
            with self.subTest(result_type=type(result).__name__),self.assertRaises(LegacyHistoryError):
                summarize_records([{'x':'data'}],32768,lambda *a:result,cancelled=self.cancelled)


if __name__=='__main__':unittest.main()
