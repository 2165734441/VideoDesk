"""Regression cases from the independently supplied VD25-01..07 report."""
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from integrations.material_history import History, digest
from integrations.storage import atomic_json
from integrations.batch_pipeline import Pipeline


class ReportRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.jobs={}
        def refresh(jid):
            folder=Path(self.jobs[jid]['directory'])
            self.jobs[jid]['files']=[{'name':p.relative_to(folder).as_posix()} for p in folder.rglob('*') if p.is_file()]
        self.a=SimpleNamespace(DATA=self.root,JOBS=self.jobs,load_json=lambda p,d:json.loads(p.read_text('utf-8')) if p.exists() else d,
            atomic_json=atomic_json,job_by_id=lambda jid:self.jobs[jid],change=lambda jid,**v:self.jobs[jid].update(v),refresh_files=refresh)
        self.history=History(self.a);self.a.MATERIAL_HISTORY=self.history

    def job(self,jid):
        folder=self.root/jid;folder.mkdir()
        self.jobs[jid]={'id':jid,'directory':str(folder),'kind':'batch-video','status':'done','files':[]}
        return folder

    def test_01_runtime_user_note_is_not_owned_or_cleaned(self):
        folder=self.job('one');owned=folder/'frame.jpg';owned.write_bytes(b'generated')
        note=folder/'MY-NOTE-DO-NOT-DELETE.txt';note.write_text('my note')
        self.history.created('one',[owned]);self.a.refresh_files('one');self.history.record('one')
        self.assertEqual(self.history.preview('one')['files'],['frame.jpg'])
        with patch('integrations.material_history.recycle',side_effect=lambda p:p.unlink()):result=self.history.clean('one')
        self.assertEqual(result['deleted'],1);self.assertEqual(note.read_text(),'my note')

    def test_01_record_does_not_adopt_user_edit_or_legacy_snapshot(self):
        folder=self.job('one');path=folder/'frame.jpg';path.write_bytes(b'generated')
        self.history.created('one',[path]);path.write_bytes(b'user edit')
        self.a.refresh_files('one');self.history.record('one')
        self.assertFalse(self.history.preview('one')['files'])
        self.history.records['one']={'root':str(folder),'files':{'frame.jpg':{'size':path.stat().st_size,'sha256':digest(path)}}}
        self.assertFalse(self.history.preview('one')['files'])

    def test_01_staging_hash_rejects_concurrent_replacement(self):
        folder=self.job('one');path=folder/'frame.jpg';path.write_bytes(b'generated')
        expected={'size':path.stat().st_size,'sha256':digest(path)};path.write_bytes(b'personal')
        self.history.created('one',{path:expected})
        self.assertFalse(self.history.preview('one')['files'])

    def test_01_regeneration_preserves_new_directory_personal_file(self):
        old=self.job('old');new=self.job('new')
        (old/'old.jpg').write_bytes(b'old');(new/'new.jpg').write_bytes(b'new')
        self.history.created('old',[old/'old.jpg']);self.history.created('new',[new/'new.jpg'])
        (new/'personal.txt').write_text('my note')
        self.a.check_cancel=lambda jid:None
        note=self.history.replace('old','new')
        self.assertIn('另存',note);self.assertEqual((new/'personal.txt').read_text(),'my note')
        self.assertTrue((old/'old.jpg').is_file());self.assertTrue((new/'new.jpg').is_file())

    def test_01_parent_uses_child_proof_and_regeneration_still_works(self):
        for parent_id in ('old','new'):
            parent=self.job(parent_id);folder=parent/'video';folder.mkdir()
            jid=parent_id+'-child';self.jobs[jid]={'id':jid,'directory':str(folder),'kind':'batch-video','status':'done','files':[]}
            (folder/'frame.jpg').write_bytes(parent_id.encode());(folder/'.ffmpeg.log').write_bytes(b'generated log')
            self.history.created(jid,[folder/'frame.jpg',folder/'.ffmpeg.log'])
            self.jobs[parent_id].update(kind='batch',children=[jid]);self.a.refresh_files(parent_id);self.history.record(parent_id)
        self.a.check_cancel=lambda jid:None
        note=self.history.replace('old','new')
        self.assertIn('已覆盖',note)
        self.assertEqual((Path(self.jobs['new']['directory'])/'video/frame.jpg').read_bytes(),b'new')
        self.assertEqual(len(self.history.preview('new')['files']),2)

    @unittest.skipUnless(os.name=='nt','Windows native environment block')
    def test_04_duplicate_case_environment_block_starts_managed_child(self):
        root=Path(__file__).resolve().parents[1]
        compiler=Path(os.environ.get('SystemRoot','C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        if not compiler.is_file():self.skipTest('.NET Framework compiler unavailable')
        info=self.root/'BuildInfo.cs';info.write_text('internal static class BuildInfo { public const string Version="test"; public const string PayloadHash="test"; }','utf-8-sig')
        exe=self.root/'EnvironmentHarness.exe'
        command=[str(compiler),'/nologo','/target:exe','/platform:x64','/codepage:65001','/main:EnvironmentHarness','/out:'+str(exe)]
        command+=['/reference:'+r for r in ('System.Windows.Forms.dll','System.Drawing.dll','System.IO.Compression.dll','System.IO.Compression.FileSystem.dll','System.Web.Extensions.dll')]
        command+=[str(root/p) for p in ('tests/EnvironmentHarness.cs','launcher/Program.cs','launcher/Compatibility.cs')]+[str(info)]
        compiled=subprocess.run(command,capture_output=True,text=True,errors='replace',timeout=30)
        self.assertEqual(compiled.returncode,0,compiled.stdout+compiled.stderr)
        env={k:v for k,v in os.environ.items() if k.upper() not in ('HTTP_PROXY','HTTPS_PROXY','PYTHONPATH')}
        env.update(HTTP_PROXY='http://127.0.0.1:9',http_proxy='http://127.0.0.1:8',HTTPS_PROXY='http://127.0.0.1:9',https_proxy='http://127.0.0.1:8',PYTHONPATH='synthetic-test-path')
        result=subprocess.run([str(exe)],env=env,capture_output=True,text=True,errors='replace',timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_05_post_move_shell_error_is_not_reported_as_retained(self):
        folder=self.job('one');path=folder/'frame.jpg';path.write_bytes(b'generated');self.history.created('one',[path])
        def moved_then_failed(p):p.unlink();raise OSError('HRESULT 80070002')
        with patch('integrations.material_history.recycle',side_effect=moved_then_failed):result=self.history.clean('one')
        self.assertEqual(result['deleted'],1);self.assertFalse(result['errors']);self.assertTrue(result['warnings'])
        self.assertTrue(self.jobs['one']['materials_deleted'])
        self.assertNotIn('文件已保留',result['warnings'][0]['reason'])

    def test_06_cleanup_statistics_and_errors_belong_to_each_job(self):
        for jid in ('one','two'):
            folder=self.job(jid)
            for name in ('a.jpg','b.jpg'):(folder/name).write_bytes(b'generated')
            self.history.created(jid,list(folder.iterdir()))
        def recycle(p):
            if p.parent.name=='two' and p.name=='b.jpg':raise OSError('locked')
            p.unlink()
        with patch('integrations.material_history.recycle',side_effect=recycle):result=self.history.clean_many(['one','two'])
        self.assertEqual(result['deleted'],3)
        self.assertEqual(self.jobs['one']['material_cleanup']['deleted'],2)
        self.assertFalse(self.jobs['one']['material_cleanup']['errors']);self.assertTrue(self.jobs['one']['materials_deleted'])
        self.assertEqual(self.jobs['two']['material_cleanup']['deleted'],1)
        self.assertEqual(len(self.jobs['two']['material_cleanup']['errors']),1);self.assertFalse(self.jobs['two']['materials_deleted'])

    def test_06_unowned_zero_deletion_does_not_hide_job(self):
        folder=self.job('one');(folder/'personal.txt').write_text('mine')
        result=self.history.clean('one')
        self.assertEqual(result['deleted'],0);self.assertFalse(self.jobs['one']['materials_deleted'])

    def test_03_parent_distinguishes_failure_cancellation_and_full_success(self):
        for child_status,expected in [('error','error'),('cancelled','cancelled'),('done','done')]:
            with self.subTest(child=child_status):
                parent={'id':'p','children':['a','b'],'finalized':True}
                rows={'p':parent,'a':{'status':'done','progress':100},'b':{'status':child_status,'progress':100}}
                pipeline=Pipeline.__new__(Pipeline);pipeline.lock=threading.RLock();pipeline.finalizing=set()
                pipeline.a=SimpleNamespace(job_by_id=lambda jid:rows[jid],change=lambda jid,**v:rows[jid].update(v),refresh_files=lambda jid:None,EVENTS={'p':threading.Event()})
                pipeline.aggregate('p');self.assertEqual(parent['status'],expected)
                self.assertEqual(parent['progress'],100 if expected=='done' else 50)

    def test_02_zero_exit_with_partial_file_is_not_success(self):
        tree=ast.parse((Path(__file__).resolve().parents[1]/'app.py').read_text('utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_ffmpeg')
        job={'directory':str(self.root)}
        class Proc:
            returncode=0
            def poll(self):return 0
        def popen(args,**kw):kw['stderr'].write(b'[mov] partial file\n');kw['stderr'].flush();return Proc()
        import subprocess
        ns={'Path':Path,'re':re,'subprocess':subprocess,'FFMPEG':'ffmpeg','LOCK':threading.RLock(),'PROCESSES':{},'EVENTS':{'j':threading.Event()},
            'check_cancel':lambda jid:None,'job_by_id':lambda jid:job,'change':lambda jid,**kw:job.update(kw),'Cancelled':RuntimeError,
            'material_history':SimpleNamespace(register_created=lambda *args:None),'sys':sys,'__name__':__name__}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'ffmpeg-regression','exec'),ns)
        with patch.object(subprocess,'Popen',side_effect=popen):
            with self.assertRaisesRegex(RuntimeError,'不完整'):ns['run_ffmpeg']('j',['-i','fixture','out.jpg'])
        self.assertEqual(job['source_diagnostic']['category'],'incomplete_media')


if __name__=='__main__':unittest.main()
