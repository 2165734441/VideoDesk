"""Regressions from the independent 2.8.1 QA report; no production data/devices."""
import concurrent.futures
import http.client
import json
import runpy
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from fastapi import HTTPException
from integrations import following, live_browser, mobile, phone_import


class RuntimeBuildRegressions(unittest.TestCase):
    def test_opencv_long_path_config_patch_is_scoped_and_idempotent(self):
        patch=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/build_windows_exe.py'))['patch_runtime_paths']
        original='PYTHON_EXTENSIONS_PATHS = [LOADER_DIR] + PYTHON_EXTENSIONS_PATHS\n'
        with tempfile.TemporaryDirectory() as tmp:
            files=[]
            for runtime in ('main','crawler'):
                path=Path(tmp)/'runtime'/runtime/'Lib/site-packages/cv2/config-3.py'
                path.parent.mkdir(parents=True);path.write_text(original);files.append(path)
            patch(tmp);first=[p.read_text() for p in files];patch(tmp)
            self.assertEqual(first,[p.read_text() for p in files])
            import os
            for length,expected in ((100,'x'*100),(300,str(Path(tmp)/'Lib/site-packages/cv2'))):
                config={'os':SimpleNamespace(name='nt',path=SimpleNamespace(join=os.path.join,dirname=os.path.dirname,isdir=lambda p:True)),
                        'sys':SimpleNamespace(executable=str(Path(tmp)/'python.exe')),'LOADER_DIR':'x'*length,'PYTHON_EXTENSIONS_PATHS':[]}
                exec(compile(first[0],'<patched-config>','exec'),config,config)
                self.assertEqual(config['PYTHON_EXTENSIONS_PATHS'],[expected])


def room(rid, suffix='target', sec='target-user'):
    return {'status':2,'id_str':'7345000000000000123','web_rid':rid,
            'owner':{'sec_uid':sec,'nickname':suffix},
            'stream_url':{'flv_pull_url':{'origin':'https://media.invalid/'+suffix+'.flv'}}}


class IdentityRegressions(unittest.TestCase):
    def test_missing_identity_never_selects_a_recommended_stream(self):
        payload={'recommend':[room('999999','recommended')]}
        for expected in (None,{}, {'web_rid':''}, {'unrelated':'value'}):
            for trusted in (False,True):
                with self.subTest(expected=expected,trusted=trusted):
                    self.assertIsNone(live_browser.room_data(payload,expected=expected,trusted=trusted))

    def test_matching_target_is_selected_even_after_recommendations(self):
        payload={'recommend':[room('999999','recommended','other-user')],'room':room('123456')}
        for expected in ({'web_rid':'123456'},{'sec_uid':'target-user'}):
            self.assertTrue(live_browser.room_data(payload,expected=expected)['url'].endswith('/target.flv'))

    def test_conflicting_identity_is_not_overridden_by_one_matching_field(self):
        self.assertIsNone(live_browser.room_data(room('999999'),expected={'web_rid':'123456','sec_uid':'target-user'}))

    def test_response_trust_does_not_extend_to_recommendations(self):
        payload={'recommend':[room('999999','recommended')],'data':{'data':[room('123456')]}}
        self.assertTrue(live_browser.room_data(payload,expected={'web_rid':'123456'},trusted=True)['url'].endswith('/target.flv'))
        self.assertIsNone(live_browser.room_data({'recommend':[room('999999')]},expected={'web_rid':'123456'},trusted=True))

    def test_exact_requested_api_response_can_omit_public_id_in_primary_room(self):
        primary=room('123456');del primary['web_rid']
        payload={'data':{'data':[primary]},'recommend':[room('999999','recommended')]}
        self.assertIsNone(live_browser.room_data(payload,expected={'web_rid':'123456'}))
        self.assertTrue(live_browser.room_data(payload,expected={'web_rid':'123456'},trusted=True)['url'].endswith('/target.flv'))
        payload['data']['data'].append(room('999999'))
        self.assertIsNone(live_browser.room_data(payload,expected={'web_rid':'123456'},trusted=True))

    def test_trusted_primary_with_explicit_conflict_is_rejected(self):
        payload={'data':{'data':[room('999999')]}}
        self.assertIsNone(live_browser.room_data(payload,expected={'web_rid':'123456'},trusted=True))


class FollowingRegressions(unittest.TestCase):
    def test_unknown_or_invalid_total_is_not_complete_even_after_last_page(self):
        for expected in (None,True):
            state={'identity_confirmed':True,'first_seen':True,'has_more':0,'users':{'a':{},'b':{}},'expected':expected}
            result=following.summary(state)
            self.assertFalse(result['complete']);self.assertNotIn('已从首页加载到末页',result['message'])

    def test_known_totals_and_missing_first_page_keep_their_meaning(self):
        state={'identity_confirmed':True,'first_seen':True,'has_more':0,'users':{'a':{},'b':{}}}
        self.assertTrue(following.summary(dict(state,expected=2))['complete'])
        self.assertFalse(following.summary(dict(state,expected=1))['complete'])
        self.assertFalse(following.summary(dict(state,expected=2,first_seen=False))['complete'])
        self.assertTrue(following.summary(dict(state,expected=0,users={}))['complete'])


class CompanionRegressions(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.anchor={'id':'a','name':'主播','enabled':True,'windows':[],'record_target':'phone'}
        self.live=SimpleNamespace(lock=threading.RLock(),anchors={'a':self.anchor},
                                  start=Mock(return_value={'id':'job'}),state=lambda:{'anchors':[self.anchor]})
        self.owner=SimpleNamespace(DATA=self.root,ROOT=self.root,LIVE=self.live,load_json=lambda p,d:d)
        self.phone=mobile.Companion(self.owner)

    def tearDown(self):self.phone.close();self.temp.cleanup()

    def test_manual_then_automatic_record_uses_one_pending_command(self):
        self.phone.last_seen=time.time();self.phone.request_record('a')
        result=self.phone.event({'anchor_id':'a','state':'online'})
        self.assertEqual(result['decision'],'phone');self.assertEqual(len(self.phone.commands),1)

    def test_automatic_then_manual_record_uses_one_pending_command(self):
        self.phone.last_seen=time.time();self.phone.event({'anchor_id':'a','state':'online'})
        self.phone.request_record('a');self.assertEqual(len(self.phone.commands),1)

    def test_offline_auto_event_does_not_queue_or_claim_a_phone_decision(self):
        with self.assertRaises(HTTPException) as caught:self.phone.event({'anchor_id':'a','state':'online'})
        self.assertEqual(caught.exception.status_code,409);self.assertEqual(self.phone.commands,[])
        self.assertEqual(self.phone.events[-1]['decision'],'pending')
        self.phone.last_seen=time.time();self.phone.decide(self.phone.events[-1]['id'],'phone')
        self.assertEqual(len(self.phone.commands),1)

    def test_concurrent_manual_and_event_decisions_share_deduplication(self):
        self.phone.last_seen=time.time()
        event=self.phone.event({'anchor_id':'a','state':'online','require_confirmation':True})
        def start(index):
            return self.phone.request_record('a') if index%2 else self.phone.decide(event['event_id'],'phone')
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(start,range(40)))
        self.assertEqual(len(self.phone.commands),1)

    def connection(self):return http.client.HTTPConnection('127.0.0.1',self.phone.server.server_port,timeout=5)

    def request(self,path,body,key=None):
        conn=self.connection()
        try:
            conn.request('POST',path,body,{'X-Phone-Key':self.phone.key if key is None else key})
            response=conn.getresponse();return response.status,json.loads(response.read())
        finally:conn.close()

    def test_oversize_upload_consistently_returns_413_without_corrupting_bridge(self):
        self.phone.start();body=b'x'*(phone_import.CHUNK+1)
        for _ in range(5):
            code,result=self.request('/v1/recordings/chunk/test',body)
            self.assertEqual(code,413);self.assertIn('error',result)
            conn=self.connection()
            try:
                conn.request('GET','/v1/config',headers={'X-Phone-Key':self.phone.key})
                response=conn.getresponse();self.assertEqual(response.status,200);self.assertIn('anchors',json.loads(response.read()))
            finally:conn.close()

    def test_unauthenticated_upload_returns_401_and_json_limit_returns_413(self):
        self.phone.start()
        self.assertEqual(self.request('/v1/status',b'x'*65537,key='wrong-key')[0],401)
        self.assertEqual(self.request('/v1/status',b'x'*65537)[0],413)

    def test_oversize_header_without_body_returns_promptly(self):
        self.phone.start();conn=self.connection();started=time.monotonic()
        try:
            conn.putrequest('POST','/v1/recordings/chunk/test')
            conn.putheader('X-Phone-Key',self.phone.key);conn.putheader('Content-Length',str(2**40));conn.endheaders()
            response=conn.getresponse();self.assertEqual(response.status,413);response.read()
            self.assertLess(time.monotonic()-started,3)
        finally:conn.close()


if __name__=='__main__':unittest.main()
