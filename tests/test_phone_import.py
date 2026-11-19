import hashlib,json,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from fastapi import HTTPException
from integrations.phone_import import Imports,CHUNK

class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name);self.jobs={}
        def load(p,d):return json.loads(p.read_text('utf-8')) if p.exists() else d
        def new(kind,title,options):
            jid=str(len(self.jobs));dest=root/jid;dest.mkdir();job=dict(id=jid,kind=kind,title=title,options=options,directory=str(dest),source='',created=0,frames=0)
            self.jobs[jid]=job;return job
        self.a=SimpleNamespace(DATA=root,ROOT=root,load_json=load,new_job=new,job_by_id=lambda jid:self.jobs[jid],change=lambda jid,**kw:self.jobs[jid].update(kw),refresh_files=Mock(),material_history=SimpleNamespace(register_created=Mock()))
        self.m=Imports(self.a);self.raw=b'video'*1000;self.value={'id':'a'*32,'sha256':hashlib.sha256(self.raw).hexdigest(),'size':len(self.raw),'name':'手机主播'}
    def tearDown(self):self.tmp.cleanup()
    def test_resume_survives_restart_and_does_not_create_duplicate_job(self):
        self.m.begin(self.value);self.m.chunk('a'*32,0,self.raw[:1700]);other=Imports(self.a)
        self.assertEqual(other.begin(self.value)['offset'],1700);other.chunk('a'*32,1700,self.raw[1700:]);self.assertEqual(len(self.jobs),1)
    def test_rejects_path_traversal_size_bool_and_bad_hash(self):
        for change in ({'id':'../x'},{'size':True},{'size':-1},{'sha256':'x'},{'size':101*1024**3}):
            with self.assertRaises(HTTPException):self.m.begin({**self.value,**change})
        self.assertFalse(self.jobs)
    def test_wrong_offset_oversize_and_changed_receipt_cannot_corrupt_resume(self):
        self.m.begin(self.value);self.m.chunk('a'*32,0,self.raw[:1000])
        for offset,data in ((0,b'x'),(1000,b'x'*(CHUNK+1)),(1000,self.raw)):
            with self.assertRaises(HTTPException):self.m.chunk('a'*32,offset,data)
        with self.assertRaises(HTTPException):self.m.begin({**self.value,'size':6000})
        self.assertEqual(self.m.begin(self.value)['offset'],1000)
    def test_bad_hash_resets_part_and_keeps_same_job(self):
        self.m.begin(self.value);self.m.chunk('a'*32,0,b'x'*len(self.raw))
        with self.assertRaises(HTTPException):self.m.complete('a'*32)
        self.assertEqual(self.m.begin(self.value)['offset'],0);self.assertEqual(len(self.jobs),1)
    def test_completion_is_idempotent_and_recovers_after_rename(self):
        self.m.begin(self.value);self.m.chunk('a'*32,0,self.raw);part=self.m.part(self.m.items['a'*32]);part.replace(part.with_name('phone-replay.mp4'))
        self.assertEqual(self.m.begin(self.value)['offset'],len(self.raw))
        probe=SimpleNamespace(returncode=0,stdout=json.dumps({'format':{'duration':'12'},'streams':[{'codec_type':'video'}]}))
        with patch('integrations.phone_import.subprocess.run',return_value=probe):first=self.m.complete('a'*32)
        self.assertTrue(first['done']);self.assertEqual(self.m.complete('a'*32)['job'],first['job']);self.assertEqual(len(self.jobs),1)
        self.assertEqual(self.jobs[first['job']]['status'],'done');self.assertTrue(self.m.begin(self.value)['done'])
    def test_disk_full_and_incomplete_files_remain_resumable(self):
        self.m.begin(self.value)
        with patch('integrations.phone_import.shutil.disk_usage',return_value=SimpleNamespace(free=1024)):
            with self.assertRaises(HTTPException):self.m.chunk('a'*32,0,self.raw)
        with self.assertRaises(HTTPException):self.m.complete('a'*32)
        self.assertEqual(self.m.begin(self.value)['offset'],0)

if __name__=='__main__':unittest.main()
