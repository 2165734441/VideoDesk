import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from integrations.storage import atomic_json
from integrations.job_recovery import load_jobs
from integrations.ocr_session import OcrSession


class Recovery(unittest.TestCase):
    def test_synced_backup_survives_corrupted_primary_and_preserves_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp);path=data/'jobs.json'
            old=[{'id':'a','kind':'analysis','status':'done','options':{},'directory':str(data/'out')}]
            atomic_json(path,old,backup=True);atomic_json(path,[],backup=True)
            path.write_bytes(b'\0'*100)
            self.assertEqual(load_jobs(data,data/'out'),old)
            self.assertEqual(next(data.glob('jobs.corrupt-*.json')).read_bytes(),b'\0'*100)
            self.assertEqual(json.loads(path.read_text('utf-8')),old)

    def test_manifest_recovery_never_claims_partial_export_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp)/'data';data.mkdir();out=Path(tmp)/'output';base=out/'标题';base.mkdir(parents=True)
            atomic_json(base/'manifest.json',{'id':'x','kind':'analysis','title':'标题','source':'source','options':{'ai_package':{'enabled':True}}})
            (base/'original.mp4').write_bytes(b'unchanged');(data/'jobs.json').write_bytes(b'\0')
            result=load_jobs(data,out)
            self.assertEqual(result[0]['status'],'interrupted');self.assertTrue(result[0]['recovered'])
            self.assertEqual((base/'original.mp4').read_bytes(),b'unchanged')

    def test_replacement_failure_keeps_last_valid_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'jobs.json';atomic_json(path,[1])
            with patch('integrations.storage.os.replace',side_effect=PermissionError),patch('integrations.storage.time.sleep'):
                with self.assertRaises(PermissionError):atomic_json(path,[2])
            self.assertEqual(json.loads(path.read_text()),[1])
            self.assertFalse(list(Path(tmp).glob('*.tmp')))


class IsolatedOCR(unittest.TestCase):
    def test_worker_crash_then_next_video_and_periodic_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker=Path(tmp)/'worker.py'
            worker.write_text("import json,sys,os\nfor line in sys.stdin:\n r=json.loads(line)\n if r['path']=='crash':os._exit(17)\n print(json.dumps({'result':{'pid':os.getpid(),'text':[r['path']]}}),flush=True)\n")
            a=SimpleNamespace(LOCK=threading.RLock(),PROCESSES={},check_cancel=lambda j:None)
            with OcrSession(a,'test',worker=worker) as session:
                first=session.read('first')['pid']
                with self.assertRaisesRegex(ValueError,'退出'):session.read('crash')
                second=session.read('second')['pid'];self.assertNotEqual(first,second)
                for _ in range(23):session.read('same')
                self.assertNotEqual(second,session.read('recycled')['pid'])
            self.assertFalse(a.PROCESSES)

    def test_timeout_ends_worker_and_cancellation_is_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker=Path(tmp)/'slow.py';worker.write_text('import time\ntime.sleep(30)')
            with OcrSession(worker=worker,timeout=.1) as session:
                with self.assertRaisesRegex(ValueError,'超时'):session.read('file')
                self.assertIsNone(session.proc)
            class Cancelled(Exception):pass
            calls=[]
            def cancel(j):
                calls.append(j)
                if len(calls)>1:raise Cancelled()
            a=SimpleNamespace(LOCK=threading.RLock(),PROCESSES={},check_cancel=cancel)
            with OcrSession(a,'test',worker=worker) as session:
                with self.assertRaises(Cancelled):session.read('file')
            self.assertFalse(a.PROCESSES)


if __name__=='__main__':unittest.main()
