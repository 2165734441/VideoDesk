"""Live profile navigation and previously paired USB reconnection regressions."""
import concurrent.futures
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock

from integrations import live_browser as lb, mobile


class Live272Tests(unittest.TestCase):
    def test_profile_nested_room_owner_and_camel_case(self):
        for room in ({'owner':{'web_rid':'123456'}},{'owner':{'webRid':'123456'}}):
            data={'user':{'sec_uid':'wanted123','room_data':json.dumps(room)},
                  'recommendation':{'sec_uid':'other123','room_data':{'web_rid':'999999'}}}
            self.assertEqual(lb.profile_room(data,'wanted123'),'https://live.douyin.com/123456')
            self.assertIsNone(lb.profile_room(data,'missing'))
        for value in ('broken','[]',[],7):
            self.assertIsNone(lb.profile_room({'sec_uid':'wanted123','room_data':value},'wanted123'))

    def test_page_data_decodes_json_assignments_and_split_rsc_without_execution(self):
        room={'room':{'idStr':'7707985402921356041','status':2,'owner':{'webRid':'123456'}}}
        line='5:'+json.dumps(room)+'\n'
        chunks=[line[:27],line[27:]]
        scripts=['window._ROUTER_DATA = '+json.dumps(room)+';',
                 *['self.__rsc_f.push('+json.dumps([1,c])+');' for c in chunks],
                 'self.__rsc_f.push((()=>{throw Error("not data")})())']
        decoded=list(lb.page_payloads(scripts))
        self.assertEqual(decoded,[room,room])

    def test_streams_require_requested_identity_and_accept_mobile_camel_case(self):
        room=lambda rid:{'idStr':'7707985402921356041','status':2,'owner':{'webRid':rid,'nickname':'Target'},
                         'streamUrl':{'flvPullUrl':{'SD1':'https://media.test/live.flv'}}}
        data={'recommendations':[room('999999')],'target':room('123456')}
        result=lb.room_data(data,'standard',{'web_rid':'123456'})
        self.assertEqual(result['room_id'],'7707985402921356041')
        self.assertIsNone(lb.room_data(data,expected={'web_rid':'555555'}))
        self.assertEqual(lb.numeric_id(7707985402921356041),'7707985402921356041')
        self.assertEqual(lb.numeric_id(float(7707985402921356041)),'')

    def test_resolve_profile_navigation_still_parses_room_page(self):
        profile='https://www.douyin.com/user/wanted123'
        room_url='https://live.douyin.com/123456'
        user={'user':{'sec_uid':'wanted123','room_data':json.dumps({'status':2,'owner':{'web_rid':'123456'}})}}
        room={'room':{'idStr':'7707985402921356041','status':2,'owner':{'webRid':'123456'},
                      'streamUrl':{'flvPullUrl':{'SD1':'https://media.test/live.flv'}}}}
        class Page:
            url=profile
            def __init__(self):self.navigations=[]
            def on(self,*args):pass
            def remove_listener(self,*args):pass
            def goto(self,url,**kw):self.url=url;self.navigations.append(url)
            def is_closed(self):return False
            def wait_for_timeout(self,*args):pass
            def evaluate(self,script,*args):
                # resolve() reads the DOM through one snapshot call so that a
                # poll no longer round-trips every script body.
                if 'document.scripts' in str(script):
                    payload=user if self.url==profile else room
                    return {'scripts':[json.dumps(payload)],'text':''}
                return 'test-agent'
        page=Page()
        browser=SimpleNamespace(pool=concurrent.futures.ThreadPoolExecutor(max_workers=1),page_for=lambda *a,**kw:page,
                                ensure=lambda:SimpleNamespace(cookies=lambda urls:[]),release=lambda page,keep=False:None)
        try:
            result=lb.resolve(browser,profile,'standard')
            self.assertEqual(page.navigations,[profile,room_url])
            self.assertTrue(result['online']);self.assertEqual(result['referer'],room_url)
        finally:browser.pool.shutdown()


class ReconnectTests(unittest.TestCase):
    def test_only_previously_paired_authorized_device_is_rebound(self):
        with tempfile.TemporaryDirectory() as tmp:
            owner=SimpleNamespace(DATA=Path(tmp),load_json=lambda p,d:{'key':'private-test-key','serial':'paired'})
            phone=mobile.Companion(owner);phone.server=SimpleNamespace(server_port=54321)
            phone.devices=Mock(return_value=[{'serial':'other','state':'device'}]);phone.adb=Mock(return_value='')
            # An unknown phone is never bound; the probe may run, the forward not.
            self.assertFalse(phone.reconnect())
            self.assertFalse(any('reverse' in call.args for call in phone.adb.call_args_list))
            phone.adb.return_value='UsbFfs tcp:47631 tcp:11111\n'
            self.assertFalse(phone.reconnect())
            self.assertFalse(any('reverse' in call.args for call in phone.adb.call_args_list))
            phone.adb.return_value='device'
            self.assertTrue(phone.reconnect())
            self.assertEqual(phone.adb.call_args.args,('-s','paired','reverse','tcp:47631','tcp:54321'))
            phone.paired_serial='';phone.adb.reset_mock();phone.devices.reset_mock()
            self.assertFalse(phone.reconnect());phone.devices.assert_not_called()
            self.assertFalse(any('reverse' in call.args for call in phone.adb.call_args_list))


if __name__=='__main__':unittest.main()
