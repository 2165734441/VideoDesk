"""Observed pagination boundaries and paired-device recording decisions."""
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError

from fastapi import HTTPException
from integrations import following,live_browser,mobile


class FollowingTests(unittest.TestCase):
    def setUp(self):self.browser=SimpleNamespace()
    def page(self,cursor,ids,more,total=None,owner='account1'):
        payload={'followings':[{'sec_uid':'MS4wLjAB'+i,'nickname':i} for i in ids],'has_more':more}
        if total is not None:payload['total']=total
        url='https://www.douyin.com/aweme/v1/web/user/following/?sec_user_id='+owner
        if cursor is not None:url+='&max_time='+str(cursor)
        return following.observe(self.browser,url,payload,live_browser.following_data)
    def test_full_pagination_deduplicates(self):
        self.page(0,['a','b'],1,3);s=self.page(100,['b','c'],0,3)
        self.assertTrue(following.summary(s)['complete']);self.assertEqual(len(s['users']),3)
    def test_last_page_alone_is_partial(self):
        self.assertFalse(following.summary(self.page(100,['b'],0))['complete'])
    def test_missing_cursor_does_not_erase_previous_pages(self):
        self.page(0,['a'],1);s=self.page(None,['b'],0)
        self.assertEqual(len(s['users']),2);self.assertTrue(s['first_seen'])
    def test_unknown_total_cannot_override_known_mismatch(self):
        self.page(0,['a'],1,8);s=self.page(100,['b'],0)
        self.assertFalse(following.summary(s)['complete']);self.assertEqual(s['expected'],8)
    def test_account_change_and_first_page_reset(self):
        self.page(0,['a'],1);s=self.page(100,['b'],0,owner='account2')
        self.assertEqual(len(s['users']),1);self.assertFalse(s['first_seen'])
        s=self.page(0,['c'],0,owner='account2');self.assertEqual(len(s['users']),1)
    def test_recommended_users_never_count_as_following(self):
        payload={'followings':[{'user':{'sec_uid':'MS4wLjAB12345','nickname':'关注'}}],
                 'recommendation':{'sec_uid':'MS4wLjAB67890','nickname':'推荐'}}
        self.assertEqual([v['name'] for v in live_browser.following_data(payload)],['关注'])
    def test_profile_total_is_only_used_for_same_owner(self):
        self.browser.live_following_profile={'sec_uid':'account1','total':20}
        self.assertEqual(self.page(0,['a'],0)['expected'],20)
        self.assertIsNone(self.page(0,['a'],0,owner='account2')['expected'])
    def test_shared_mobile_live_links_and_strict_hosts(self):
        self.assertEqual(live_browser.normalize('去看 https://www.douyin.com/live/123456?from=share'), 'https://live.douyin.com/123456')
        self.assertIn('/reflow/',live_browser.normalize('https://webcast.amemv.com/douyin/webcast/reflow/123456/?from=share'))
        for url in ('https://www.douyin.com/follow','https://www.douyin.com/livestreams','https://webcast.amemv.com/video/123456'):
            with self.assertRaises(ValueError):live_browser.normalize(url)


class PhoneTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.anchor={'id':'a','name':'主播','enabled':True,'windows':[],'record_target':'ask'}
        self.live=SimpleNamespace(lock=threading.RLock(),anchors={'a':self.anchor},start=Mock(return_value={'id':'job'}),state=lambda:{'anchors':[self.anchor]})
        self.owner=SimpleNamespace(DATA=self.root,ROOT=self.root,LIVE=self.live,load_json=lambda p,d:d)
        self.phone=mobile.Companion(self.owner)
    def tearDown(self):self.phone.close();self.temp.cleanup()
    def event(self,state='online',**kw):return self.phone.event({'anchor_id':'a','state':state,**kw})
    def test_rejects_missing_anchor_and_invalid_state(self):
        for v in ({'anchor_id':'missing','state':'online'},{'anchor_id':'a','state':'yes'}):
            with self.assertRaises(HTTPException):self.phone.event(v)
    def test_duplicate_event_and_click_create_only_one_job(self):
        eid=self.event()['event_id'];self.assertTrue(self.event()['duplicate'])
        self.phone.decide(eid,'pc');self.phone.decide(eid,'pc');self.live.start.assert_called_once_with('a')
    def test_offline_invalidates_pending_decisions(self):
        eid=self.event()['event_id'];self.event('offline');self.phone.decide(eid,'pc');self.live.start.assert_not_called()
    def test_disabled_anchor_and_time_window_prevent_automatic_start(self):
        self.anchor.update(enabled=False,record_target='pc');self.event();self.live.start.assert_not_called()
        self.phone.events=[];self.anchor['enabled']=True
        with patch('integrations.live.in_window',return_value=False):self.event()
        self.live.start.assert_not_called()
    def test_nickname_only_notice_requires_confirmation(self):
        self.anchor['record_target']='pc';self.event(require_confirmation=True);self.live.start.assert_not_called()
    def test_phone_command_and_stop_require_connection(self):
        with self.assertRaises(HTTPException):self.phone.request_record('a')
        self.phone.last_seen=time.time();self.phone.request_record('a');self.phone.request_record('a')
        self.assertEqual(len(self.phone.commands),1);self.phone.request_record('a','stop');self.assertEqual(len(self.phone.commands),2)
    def test_expired_decision_is_rejected(self):
        eid=self.event()['event_id'];self.phone.events[0]['time']-=601
        with self.assertRaises(HTTPException):self.phone.decide(eid,'pc')
        self.live.start.assert_not_called()
    def test_bridge_auth_command_expiry_and_ack(self):
        self.phone.start();base='http://127.0.0.1:'+str(self.phone.server.server_port)
        with self.assertRaises(HTTPError) as context:urlopen(base+'/v1/config')
        self.assertEqual(context.exception.code,401)
        self.phone.commands=[{'id':'expired','anchor_id':'a','time':time.time()-181},{'id':'valid','anchor_id':'a','time':time.time()}]
        def request(path,body=None):
            data=None if body is None else json.dumps(body).encode()
            with urlopen(Request(base+path,data=data,headers={'X-Phone-Key':self.phone.key})) as response:return json.load(response)
        self.assertEqual([c['id'] for c in request('/v1/config')['commands']],['valid'])
        request('/v1/status',{'ack':'valid','message':'test'});self.assertFalse(self.phone.commands)
        self.assertEqual(self.phone.server.server_address[0],'127.0.0.1')

if __name__=='__main__':unittest.main()
