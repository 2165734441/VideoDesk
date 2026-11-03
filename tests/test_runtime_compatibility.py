"""Capability isolation, honest status and preservation of basic operations."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from integrations import runtime_support, ai_models, image_dedup
from integrations.ocr_session import OcrSession

ROOT=Path(__file__).resolve().parents[1]


class RuntimeCompatibility(unittest.TestCase):
    def test_public_status_excludes_raw_diagnostics_and_accounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            report=Path(tmp)/'report.json'
            report.write_text(json.dumps({'cpu':'CPU','secret':'not-public','components':[
                {'id':'ocr','name':'画面文字识别','ready':False,'message':'请修复',
                 'error':'private raw error','stdout':'private path','exit_code':17}]}))
            with patch.dict(os.environ,{'VIDEO_DESK_RUNTIME_REPORT':str(report),'VIDEO_DESK_LAUNCHER':'1'}):
                state=runtime_support.status()
            self.assertTrue(state['degraded']);self.assertTrue(state['managed'])
            self.assertEqual(set(state['components'][0]),{'id','name','ready','message'})
            self.assertNotIn('private',json.dumps(state));self.assertNotIn('not-public',json.dumps(state))

    def test_missing_or_corrupt_report_still_shows_disabled_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'report.json'
            for content in (None,'not json'):
                if content:path.write_text(content)
                with patch.dict(os.environ,{'VIDEO_DESK_RUNTIME_REPORT':str(path),'VIDEO_DESK_DISABLED_COMPONENTS':'ocr,asr'}):
                    state=runtime_support.status()
                self.assertTrue(state['degraded'])
                self.assertEqual({c['id'] for c in state['components']},{'ocr','asr'})

    def test_ocr_disabled_never_spawns_a_worker(self):
        with patch.dict(os.environ,{'VIDEO_DESK_DISABLED_COMPONENTS':'ocr'}),patch('integrations.ocr_session.subprocess.Popen') as spawn:
            with OcrSession() as session:
                with self.assertRaisesRegex(ValueError,'检测并修复'):session.read('unused.png')
            spawn.assert_not_called()

    def test_exact_dedup_remains_usable_when_similar_engine_is_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();out=root/'out';out.mkdir()
            Image.new('RGB',(32,32),'green').save(source/'a.png')
            (source/'b.png').write_bytes((source/'a.png').read_bytes())
            with patch.dict(os.environ,{'VIDEO_DESK_DISABLED_COMPONENTS':'dedup'}):
                result=image_dedup.scan(source,out,mode='exact')
                self.assertEqual(result['summary']['duplicates'],1)
                with self.assertRaisesRegex(ValueError,'检测并修复'):image_dedup.scan(source,out,mode='similar')

    def test_model_state_does_not_execute_ocr_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'models').mkdir();(root/'models'/'fake.onnx').touch()
            spec=importlib.util.spec_from_file_location('rapidocr',root/'__init__.py')
            real=importlib.util.find_spec
            def find(name):return spec if name=='rapidocr' else real(name)
            with patch('integrations.ai_models.importlib.util.find_spec',side_effect=find):
                with patch.dict(os.environ,{'VIDEO_DESK_DISABLED_COMPONENTS':''}):self.assertTrue(ai_models.state(root)['ocr'])
                with patch.dict(os.environ,{'VIDEO_DESK_DISABLED_COMPONENTS':'ocr,asr'}):
                    state=ai_models.state(root);self.assertFalse(state['ocr']);self.assertFalse(state['asr_runtime'])

    def test_server_and_settings_do_not_import_native_ai_engines(self):
        with tempfile.TemporaryDirectory() as tmp:
            code='''
import builtins,json,sys
sys.path.insert(0,sys.argv[1])
original=builtins.__import__
def guarded(name,*args,**kwargs):
 if name.split('.')[0] in ('rapidocr','sherpa_onnx','onnxruntime','cv2','numpy','scipy','imagehash'):
  raise AssertionError('Native engine loaded in server: '+name)
 return original(name,*args,**kwargs)
builtins.__import__=guarded
import app
from integrations import ai_models
state=app.state()
assert state['runtime']['degraded']
assert not state['engines']['mediacrawler']
assert not state['engines']['yt_dlp']
assert not state['youtube']['ready']
assert not ai_models.state(app.DATA)['ocr']
assert state['jobs']==[]
from fastapi import HTTPException
import os
os.environ['VIDEO_DESK_LAUNCHER']='1'
job=app.new_job('capture','test',str(app.DATA/'out'))
try:app.runtime_repair();raise AssertionError('active repair was allowed')
except HTTPException as e:assert e.status_code==409
app.change(job['id'],status='done')
assert app.runtime_repair()['ok']
stamp=(app.DATA/'repair.request').stat().st_mtime_ns
assert app.runtime_repair()['ok']
assert (app.DATA/'repair.request').stat().st_mtime_ns==stamp
try:app.new_job('capture','too late',str(app.DATA/'out'));raise AssertionError('new task during repair')
except HTTPException as e:assert e.status_code==409
try:app.change(job['id'],status='running');raise AssertionError('retry during repair')
except HTTPException as e:assert e.status_code==409
print('server and settings isolated')
'''
            env={**os.environ,'VIDEO_DESK_DATA':tmp,'VIDEO_DESK_DISABLED_COMPONENTS':'ocr,asr,node,crawler,download,dedup'}
            result=subprocess.run([sys.executable,'-c',code,str(ROOT)],env=env,capture_output=True,text=True,timeout=30,
                                  creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_unknown_probe_returns_an_error_in_its_own_process(self):
        result=subprocess.run([sys.executable,str(ROOT/'integrations/runtime_probe.py'),'unknown'],
                              capture_output=True,text=True,timeout=15,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertNotEqual(result.returncode,0);self.assertIn('Unknown probe',result.stderr)


if __name__=='__main__':unittest.main()
