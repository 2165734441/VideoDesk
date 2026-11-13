import asyncio
import ast
import errno
import os
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from integrations.batch_pipeline import BatchRequest, Pipeline
from integrations.local_sources import BrowserReader, CHUNK
from integrations.media_probe import inspect, ProbeError
from integrations.performance import resolve
from integrations.workflow_settings import migrate
from integrations.material_history import History
from integrations.storage import atomic_json, relocate_file


class WorkflowContracts(unittest.TestCase):
    def test_cross_drive_publish_and_failed_copy_preserve_previous_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);source=folder/'source';target=folder/'target';source.write_bytes(b'new');target.write_bytes(b'old')
            replace=os.replace
            def crossdrive(src,dst):
                if Path(src)==source:raise OSError(errno.EXDEV,'different drives')
                return replace(src,dst)
            with patch('integrations.storage.os.replace',side_effect=crossdrive),patch('integrations.storage.shutil.copy2',side_effect=OSError(28,'full')):
                with self.assertRaises(OSError):relocate_file(source,target)
            self.assertEqual(source.read_bytes(),b'new');self.assertEqual(target.read_bytes(),b'old')
            with patch('integrations.storage.os.replace',side_effect=crossdrive):relocate_file(source,target)
            self.assertFalse(source.exists());self.assertEqual(target.read_bytes(),b'new')
            self.assertFalse(list(folder.glob('.*.tmp')))

    def test_log_archival_failure_still_finishes_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);(folder/'.ffmpeg.log').write_text('log')
            job={'id':'test','status':'error','directory':str(folder),'options':{'source_id':'source','parent_id':'parent'}}
            pipeline=Pipeline.__new__(Pipeline);pipeline.lock=threading.RLock();pipeline.scheduled={'test'};pipeline.workspace=folder/'work'
            pipeline.workspace.mkdir()
            pipeline.a=SimpleNamespace(job_by_id=lambda jid:job,guarded=lambda jid,work:None,change=lambda jid,**kw:job.update(kw))
            with patch.object(pipeline,'aggregate') as aggregate,patch('integrations.batch_pipeline.relocate_file',side_effect=OSError('archive denied')):
                pipeline.run('test')
            aggregate.assert_called_once_with('parent')
            self.assertFalse(pipeline.scheduled)

    def test_batch_probe_cancels_owned_process_without_waiting_for_timeout(self):
        event=threading.Event()
        class Cancelled(Exception):pass
        def check(jid):
            if event.is_set():raise Cancelled()
        def stop(proc):proc.terminate();proc.wait(timeout=5)
        pipeline=Pipeline.__new__(Pipeline)
        pipeline.a=SimpleNamespace(check_cancel=check,LOCK=threading.RLock(),PROCESSES={},stop_owned_process=stop)
        timer=threading.Timer(.2,event.set);timer.start();started=time.monotonic()
        try:
            with self.assertRaises(Cancelled):
                pipeline.probe_owned('test',[sys.executable,'-c','import time;time.sleep(30)'],45,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        finally:timer.cancel()
        self.assertLess(time.monotonic()-started,3)
        self.assertFalse(pipeline.a.PROCESSES)

    def test_full_disk_does_not_leave_failed_job_running(self):
        tree=ast.parse((Path(__file__).resolve().parents[1]/'app.py').read_text('utf-8'))
        nodes=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in ('guarded','refresh_partial_files')]
        job={'kind':'batch-video','status':'queued'}
        class Cancelled(Exception):pass
        class TakeoverRequired(Exception):pass
        class SourceUnavailable(Exception):pass
        class Event:
            def is_set(self):return False
        def change(jid,**values):job.update(values)
        def full(jid):raise OSError(28,'No space left on device')
        context={'change':change,'check_cancel':lambda jid:None,'refresh_files':full,'job_by_id':lambda jid:job,
            'Cancelled':Cancelled,'storyboard':SimpleNamespace(TakeoverRequired=TakeoverRequired),
            'local_sources':SimpleNamespace(SourceUnavailable=SourceUnavailable),'EVENTS':{'test':Event()}}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),context)
        context['guarded']('test',lambda:full('test'))
        self.assertEqual(job['status'],'error')
        self.assertIn('No space left',job['message'])

    def test_legacy_ai_preferences_cannot_contaminate_first_rules_session(self):
        saved=migrate({'settings':{'outputs':{'frames':False,'ai':True,'puzzles':True,'transcript':True},'ai_package':{'enabled':True,'combined':True}}})
        self.assertEqual([k for k,v in saved['rules']['outputs'].items() if v],['frames'])
        self.assertFalse(saved['rules']['ai_package']['enabled'])
        saved['rules']['segments']['seconds']=25
        self.assertEqual(saved['board']['segments']['seconds'],10)

    def test_new_workflow_neutralizes_injected_ai_flags_but_legacy_api_is_preserved(self):
        payload={'sources':['s'],'kind':'rules','outputs':{'frames':True,'ai':True,'transcript':True,'puzzles':True},'ai_package':{'enabled':True},'request_id':'contract25'}
        req=BatchRequest(**payload,workflow='rules')
        self.assertFalse(req.outputs.ai or req.outputs.transcript or req.outputs.puzzles or req.ai_package.enabled)
        self.assertTrue(BatchRequest(**payload).outputs.ai)
        with self.assertRaises(ValueError):BatchRequest(**payload,workflow='board')

    def test_new_board_always_includes_required_reading_material(self):
        req=BatchRequest(sources=['s'],workflow='board',kind='board',outputs={'audio':True},request_id='contract25')
        self.assertTrue(req.outputs.ai and req.outputs.puzzles and req.outputs.transcript and req.ai_package.enabled)

    def test_online_rule_requests_do_not_require_ai_exports(self):
        req=BatchRequest(sources=['online'],online_sources=[{'input':'https://example.com/video.mp4'}],workflow='rules',kind='rules',outputs={'frames':True},request_id='contract25')
        self.assertFalse(req.ai_package.enabled or req.outputs.ai)

    def test_online_episode_preferences_survive_settings_migration(self):
        saved=migrate({'workflows':{'board':{'online_episodes':{'mode':'one','items':[{'input':'https://example.com/a'}]}}}})
        self.assertEqual(saved['board']['online_episodes']['mode'],'one')
        self.assertEqual(len(saved['board']['online_episodes']['items']),1)

    def test_subtitle_tool_cannot_invoke_recognition(self):
        req=BatchRequest(sources=['s'],workflow='subtitles',kind='subtitles',outputs={'ai':True,'transcript':True},analysis={'ocr':True},request_id='contract25')
        self.assertEqual(req.analysis['transcript'],'off');self.assertFalse(req.analysis['ocr'])
        self.assertFalse(req.outputs.ai or req.outputs.transcript or req.ai_package.enabled)

    def test_resource_limits_respect_low_memory_and_cpu(self):
        self.assertEqual(resolve('high',cpu=1,memory=512*1024**2)['decode_threads'],1)
        for profile in ('auto','low','balanced','high'):
            resource=resolve(profile,cpu=2,memory=16*1024**3)
            self.assertLessEqual(resource['decode_threads'],2);self.assertEqual(resource['ocr_threads'],1)
        self.assertEqual(resolve('auto',cpu=20,memory=16*1024**3)['decode_threads'],8)


class ProbeDiagnostics(unittest.TestCase):
    def test_timeout_retries_with_longer_budget_and_keeps_diagnostics(self):
        result=SimpleNamespace(returncode=0,stdout=b'{"streams":[{"codec_type":"video"}],"format":{"duration":"2"}}',stderr=b'')
        with patch('integrations.media_probe.subprocess.run',side_effect=[subprocess.TimeoutExpired('ffprobe',12),result]) as run:
            info=inspect('http://127.0.0.1/media','probe')
        self.assertEqual([c.kwargs['timeout'] for c in run.call_args_list],[12,45])
        self.assertEqual(info['diagnostic']['attempts'][0]['timed_out'],True)
        self.assertEqual(info['diagnostic']['exit_code'],0)

    def test_errors_are_not_lumped_into_video_damage(self):
        for code,stdout,stderr,category in [(1,b'',b'Permission denied','permission_denied'),(1,b'',b'Unsupported codec','unsupported_codec'),(1,b'',b'moov atom not found','container_error'),(0,b'{"streams":[]}',b'','missing_video_stream'),(0,b'broken json',b'','container_error')]:
            with self.subTest(category=category),patch('integrations.media_probe.subprocess.run',return_value=SimpleNamespace(returncode=code,stdout=stdout,stderr=stderr)):
                with self.assertRaises(ProbeError) as error:inspect('http://127.0.0.1/media','probe')
                self.assertEqual(error.exception.diagnostic['category'],category)
                self.assertEqual(error.exception.diagnostic['exit_code'],code)

    def test_missing_source_component_and_disconnection_are_distinct(self):
        with self.assertRaises(ProbeError) as error:inspect('Z:/not-present-test25.mp4','probe')
        self.assertEqual(error.exception.diagnostic['category'],'missing_path')
        with patch('integrations.media_probe.subprocess.run',side_effect=FileNotFoundError):
            with self.assertRaises(ProbeError) as error:inspect('http://127.0.0.1/media','probe')
        self.assertEqual(error.exception.diagnostic['category'],'component_unavailable')
        with patch('integrations.media_probe.subprocess.run',side_effect=subprocess.TimeoutExpired('p',12)):
            with self.assertRaises(ProbeError) as error:inspect('http://127.0.0.1/media','probe',connected=lambda:False)
        self.assertEqual(error.exception.diagnostic['category'],'source_disconnected')


class LateReads(unittest.TestCase):
    def test_late_reply_does_not_close_or_poison_current_request(self):
        reader=BrowserReader(100);reader.connected=True;reader.pending=('a'*32,3)
        self.assertFalse(reader.accept(b'b'*32+b'old'))
        self.assertIsNone(reader.result);self.assertTrue(reader.connected)
        self.assertTrue(reader.accept(b'a'*32+b'new'));self.assertEqual(reader.result,b'new')
        reader.pending=None
        self.assertFalse(reader.accept(b'a'*32+b'new'))
        with self.assertRaises(ValueError):reader.accept(b'a'*(CHUNK+33))


class CleanupOwnership(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.jobs={}
        def refresh(jid):
            base=Path(self.jobs[jid]['directory']);self.jobs[jid]['files']=[{'name':p.relative_to(base).as_posix()} for p in base.rglob('*') if p.is_file()]
        self.a=SimpleNamespace(DATA=self.root,JOBS=self.jobs,load_json=lambda p,d:json.loads(p.read_text('utf-8')) if p.exists() else d,
            atomic_json=atomic_json,job_by_id=lambda jid:self.jobs[jid],refresh_files=refresh,change=lambda jid,**v:self.jobs[jid].update(v))
        self.history=History(self.a)

    def job(self,jid,folder=None):
        folder=folder or self.root/jid;folder.mkdir(exist_ok=True)
        self.jobs[jid]={'id':jid,'status':'done','kind':'batch-video','directory':str(folder),'files':[]}
        return folder

    def record(self,jid):
        self.a.refresh_files(jid)
        self.history.created(jid,[Path(self.jobs[jid]['directory'])/f['name'] for f in self.jobs[jid]['files']])
        self.history.record(jid)

    def test_other_retained_job_prevents_shared_file_cleanup(self):
        folder=self.job('one');(folder/'owned.txt').write_text('keep');self.record('one')
        self.job('two',folder);self.record('two')
        result=self.history.preview_many(['one'])
        self.assertFalse(result['files']);self.assertIn('共同引用',result['skipped'][0]['reason'])

    def test_shared_check_skips_unrelated_image_filesystem_probes(self):
        folder=self.job('one');path=folder/'owned.txt';path.write_text('owned');self.record('one')
        self.job('two');self.jobs['two']['files']=[{'name':f'frames/{i}.png'} for i in range(1000)]
        with patch.object(self.history,'safe',wraps=self.history.safe) as checked:
            self.assertFalse(self.history.shared(path,{'one'}))
        self.assertEqual(checked.call_count,0)

    def test_batch_parent_child_selection_counts_each_file_once(self):
        parent=self.job('parent');child=self.job('child',parent/'child');(child/'file.txt').write_text('owned')
        self.jobs['parent'].update(kind='batch',children=['child']);self.record('child');self.record('parent')
        result=self.history.preview_many(['parent','child']);self.assertEqual(len(result['files']),1)

    def test_failed_recycle_preserves_file_and_visible_task(self):
        folder=self.job('one');path=folder/'file.txt';path.write_text('owned');self.record('one')
        with patch('integrations.material_history.recycle',side_effect=OSError('cannot recycle')):
            result=self.history.clean_many(['one'])
        self.assertTrue(path.exists());self.assertEqual(result['deleted'],0);self.assertTrue(result['errors'])
        self.assertFalse(self.jobs['one']['materials_deleted'])

    def test_generated_cleanup_preserves_user_modifications_and_new_files(self):
        folder=self.job('one');owned=folder/'owned.txt';owned.write_text('owned');changed=folder/'changed.txt';changed.write_text('old');self.record('one')
        changed.write_text('edited');new=folder/'personal.txt';new.write_text('new')
        with patch('integrations.material_history.recycle',side_effect=lambda p:p.unlink()):result=self.history.clean_many(['one'])
        self.assertEqual(result['deleted'],1);self.assertTrue(changed.exists() and new.exists());self.assertTrue(self.jobs['one']['materials_deleted'])


if __name__=='__main__':unittest.main()
