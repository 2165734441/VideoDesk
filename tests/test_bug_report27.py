"""Account isolation, live output/state and credential regressions from VD27."""
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI, HTTPException
from integrations import following, live, live_browser, translation
from integrations.login_browser import LoginBrowser


def observed(browser, names, owner=None, cursor=0, total=None, more=False):
    url='https://www.douyin.com/aweme/v1/web/user/following/list/?cursor='+str(cursor)
    if owner:url+='&sec_user_id='+owner
    payload={'has_more':more,'followings':[{'sec_uid':'MS4wLjAB'+n,'nickname':n} for n in names]}
    if total is not None:payload['total']=total
    return following.observe(browser,url,payload,live_browser.following_data)


class AccountRegressions(unittest.TestCase):
    def test_switch_with_or_without_owner_and_profile(self):
        for profile_present in (False,True):
            for owner_present in (False,True):
                with self.subTest(profile=profile_present,owner=owner_present):
                    browser=SimpleNamespace(live_following_profile={'sec_uid':'accountA','uid':'1','total':2} if profile_present else {})
                    following.set_session(browser,'sessionA')
                    observed(browser,['alice','bob'],'accountA' if owner_present else None,total=2,more=True)
                    following.set_session(browser,'sessionB')
                    if profile_present:following.set_profile(browser,{'sec_uid':'accountB','uid':'2','following_count':1})
                    result=following.summary(observed(browser,['charlie'],'accountB' if owner_present else None,cursor=50,total=1))
                    self.assertEqual([u['name'] for u in result['users']],['charlie'])
                    self.assertEqual(result['expected_count'],1)
                    self.assertFalse(result['complete'])

    def test_profile_change_without_session_or_owner_drops_old_rows(self):
        browser=SimpleNamespace(live_following_profile={'sec_uid':'accountA','uid':'1','total':2})
        observed(browser,['alice','bob'],'accountA',total=2)
        browser.live_following_profile={'sec_uid':'accountB','uid':'2','total':1}
        result=following.summary(observed(browser,['charlie'],cursor=50,total=1))
        self.assertEqual([u['name'] for u in result['users']],['charlie'])

    def test_missing_all_identity_evidence_never_merges_old_account(self):
        browser=SimpleNamespace()
        observed(browser,['alice'],'accountA')
        result=following.summary(observed(browser,['charlie'],cursor=50,total=1))
        self.assertEqual([u['name'] for u in result['users']],['charlie'])
        self.assertFalse(result['complete'])
        self.assertIn('身份尚未确认',result['message'])

    def test_ownerless_pages_keep_verified_session_and_resume_without_duplicates(self):
        browser=SimpleNamespace()
        following.set_session(browser,'sessionA')
        observed(browser,['alice'],total=2,more=True)
        observed(browser,['alice'],total=2,more=True)
        result=following.summary(observed(browser,['alice','bob'],cursor=20,total=2))
        self.assertEqual(result['loaded_count'],2)
        self.assertTrue(result['complete'])

    def test_profile_changes_invalidate_snapshot_immediately(self):
        browser=SimpleNamespace()
        following.set_profile(browser,{'sec_uid':'accountA','following_count':2})
        observed(browser,['alice'],total=2,more=True)
        following.set_profile(browser,{'sec_uid':'accountB','following_count':1})
        self.assertEqual(following.summary(browser.live_following)['loaded_count'],0)

    def test_session_marker_is_irreversible_and_ignores_cookie_order_duplicates(self):
        cookies=[{'name':'sessionid','value':'private-test-session'},{'name':'unrelated','value':'rotate'}]
        marker=following.session_fingerprint(cookies)
        self.assertNotIn('private-test-session',marker)
        self.assertEqual(marker,following.session_fingerprint(list(reversed(cookies))+[cookies[0]]))
        self.assertIsNone(following.session_fingerprint([]))

    def test_delayed_response_from_old_session_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            browser=LoginBrowser(tmp)
            try:
                browser.context=Mock()
                browser.context.cookies.return_value=[{'name':'sessionid','value':'new'}]
                following.set_session(browser,following.session_fingerprint([{'name':'sessionid','value':'old'}]))
                observed(browser,['alice'],'accountA',total=1)
                page=Mock();browser.observe(page)
                callback=page.on.call_args.args[1]
                response=Mock(status=200,url='https://www.douyin.com/aweme/v1/web/user/following/list/?cursor=0')
                response.request.header_value.return_value='sessionid=old'
                callback(response)
                self.assertEqual(browser.live_following,{})
                response.json.assert_not_called()
                response.request.header_value.return_value='sessionid=new'
                response.json.return_value={'followings':[{'sec_uid':'MS4wLjABcharlie','nickname':'charlie'}],'has_more':False,'total':1}
                callback(response)
                self.assertEqual([u['name'] for u in following.summary(browser.live_following)['users']],['charlie'])
            finally:browser.close()

    def test_numeric_gap_unknown_total_and_conflicting_total(self):
        browser=SimpleNamespace()
        result=following.summary(observed(browser,[str(n) for n in range(120)],'accountA',total=500,more=True))
        self.assertEqual(result['missing_count'],380);self.assertIn('380',result['message'])
        result=following.summary(observed(browser,['a','b'],'accountA',total=1))
        self.assertEqual(result['missing_count'],0);self.assertFalse(result['complete'])
        self.assertIn('数量不一致',result['message'])
        result=following.summary(observed(browser,['a'],'accountA',cursor=20))
        self.assertFalse(result['complete'])
        result=following.summary(observed(SimpleNamespace(),['a'],'accountA',cursor=20))
        self.assertIsNone(result['missing_count'])


class LiveRegressions(unittest.TestCase):
    def test_verified_landing_page_alias_deduplicates_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);owner=self.make_owner(root);manager=live.Manager(owner)
            rid='7381928374650910213'
            try:
                item=manager.put(live.Anchor(url='https://v.douyin.com/qaShort/'))
                manager.remember_room(item['id'],item,{'room_id':rid,'referer':'https://live.douyin.com/123456'})
                manager.close();manager=live.Manager(owner)
                for url in ('https://live.douyin.com/123456','https://webcast.amemv.com/douyin/webcast/reflow/'+rid+'/'):
                    with self.assertRaises(HTTPException) as caught:manager.put(live.Anchor(url=url))
                    self.assertEqual(caught.exception.status_code,409)
                edited=manager.put(live.Anchor(id=item['id'],url='https://live.douyin.com/654321'))
                self.assertNotIn(item['id'],manager.identities)
                manager.remember_room(item['id'],item,{'room_id':rid})
                self.assertNotIn(item['id'],manager.identities)
                self.assertEqual(edited['url'],'https://live.douyin.com/654321')
            finally:manager.close()

    def test_equal_digit_web_and_actual_room_ids_are_not_merged_without_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            owner=self.make_owner(Path(tmp));manager=live.Manager(owner)
            try:
                rid='7381928374650910213'
                manager.put(live.Anchor(url='https://live.douyin.com/'+rid))
                manager.put(live.Anchor(url='https://webcast.amemv.com/douyin/webcast/reflow/'+rid+'/'))
                self.assertEqual(len(manager.anchors),2)
            finally:manager.close()

    def test_reflow_variants_deduplicate_without_reinterpreting_real_room_id(self):
        rid='7381928374650910213'
        canonical='https://webcast.amemv.com/douyin/webcast/reflow/'+rid+'/'
        with tempfile.TemporaryDirectory() as tmp:
            owner=SimpleNamespace(DATA=Path(tmp),JOBS={},load_json=lambda p,d:d)
            manager=live.Manager(owner)
            try:
                manager.put(live.Anchor(url=canonical))
                for url in (canonical+'?from=share',canonical.rstrip('/'),canonical.replace('https:','http:')+'#share'):
                    self.assertEqual(live_browser.normalize(url),canonical)
                    with self.assertRaises(HTTPException) as caught:manager.put(live.Anchor(url=url))
                    self.assertEqual(caught.exception.status_code,409)
                self.assertNotEqual(live_browser.normalize('https://live.douyin.com/'+rid),canonical)
                self.assertEqual(len(manager.anchors),1)
            finally:manager.close()

    def test_live_status_is_three_valued(self):
        for status,expected in [('missing',None),(None,None),('2',True),(2,True),(4,False),(0,None),('bad',None)]:
            with self.subTest(status=status):
                payload={'id_str':'7381928374650910213','stream_url':{'flv_pull_url':{'origin':'https://media.invalid/live.flv'}}}
                if status!='missing':payload['status']=status
                result=live_browser.room_data(payload,expected={'room_id':'7381928374650910213'})
                self.assertIs(result['online'],expected)
                self.assertEqual(result['room_id'],'7381928374650910213')

    def make_owner(self,root):
        def load(path,default):
            return json.loads(path.read_text('utf-8')) if path.is_file() else default
        owner=SimpleNamespace(DATA=root,JOBS={},EVENTS={},LOCK=threading.RLock(),SETTINGS={'output':str(root/'globalA')},load_json=load)
        owner.output_dir=lambda output:Path(output or owner.SETTINGS['output'])
        def new_job(kind,title,output,source,options):
            jid=str(len(owner.JOBS)+1);directory=owner.output_dir(output)/jid;directory.mkdir(parents=True)
            job={'id':jid,'kind':kind,'title':title,'directory':str(directory),'status':'queued','message':''}
            owner.JOBS[jid]=job;owner.EVENTS[jid]=threading.Event();return job
        owner.new_job=new_job;owner.job_by_id=lambda jid:owner.JOBS[jid]
        owner.change=lambda jid,**kw:owner.JOBS[jid].update(kw)
        owner.refresh_partial_files=Mock()
        return owner

    def test_global_directory_following_explicit_override_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);owner=self.make_owner(root);app=FastAPI();manager=live.install(app,owner)
            save=next(r.endpoint for r in app.routes if getattr(r,'path','')=='/api/live/settings')
            try:
                with patch.object(manager.pool,'submit'):
                    save(live.LiveSettings(output=''))
                    owner.SETTINGS['output']=str(root/'globalB')
                    self.assertTrue(manager.state()['settings']['follow_global'])
                    self.assertEqual(manager.state()['settings']['output'],str(root/'globalB'))
                    key=manager.put(live.Anchor(url='https://live.douyin.com/123456'))['id']
                    job=owner.JOBS[manager.start(key)['id']]
                    self.assertEqual(Path(job['directory']).parent,root/'globalB')
                    save(live.LiveSettings(output=str(root/'explicit')))
                    owner.SETTINGS['output']=str(root/'globalC')
                    self.assertEqual(manager.state()['settings']['output'],str(root/'explicit'))
                    self.assertFalse(manager.state()['settings']['follow_global'])
                manager.close();manager=live.Manager(owner)
                self.assertEqual(manager.state()['settings']['output'],str(root/'explicit'))
            finally:manager.close()

    def test_unknown_status_never_starts_capture_or_reports_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);owner=self.make_owner(root);owner.BROWSER=Mock();manager=live.Manager(owner)
            try:
                key=manager.put(live.Anchor(url='https://live.douyin.com/123456'))['id']
                with patch.object(manager.pool,'submit'):
                    jid=manager.start(key)['id']
                with patch.object(live_browser,'resolve',return_value={'online':None}),patch.object(manager,'capture_segment') as capture:
                    manager.record(key,manager.anchors[key],jid,False)
                    capture.assert_not_called()
                    self.assertEqual(owner.JOBS[jid]['status'],'error')
                    self.assertIn('无法确认',owner.JOBS[jid]['message'])
                    self.assertNotIn('未开播',owner.JOBS[jid]['message'])
            finally:manager.close()


class CredentialRegressions(unittest.TestCase):
    def test_explicit_clear_omission_rotation_partial_role_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            def load(path,default):return json.loads(path.read_text('utf-8')) if path.is_file() else default
            owner=SimpleNamespace(DATA=Path(tmp),load_json=load)
            app=FastAPI();pipeline=translation.install(app,owner)
            routes={r.path:r.endpoint for r in app.routes if getattr(r,'path','')=='/api/translation/services' and 'POST' in r.methods}
            save=routes['/api/translation/services']
            get=next(r.endpoint for r in app.routes if getattr(r,'path','')=='/api/translation/services' and 'GET' in r.methods)
            try:
                save(translation.CloudSettings(asr={'api_key':'fake-old','model':'asr-model'},tts={'api_key':'fake-tts'}))
                save(translation.CloudSettings(asr={'model':'new-model'}))
                self.assertEqual(pipeline.services()['asr']['api_key'],'fake-old')
                self.assertEqual(pipeline.services()['tts']['api_key'],'fake-tts')
                save(translation.CloudSettings(asr={'api_key':''}))
                self.assertFalse(get()['asr']['has_key'])
                self.assertNotIn('fake-old',pipeline.services_path.read_text())
                save(translation.CloudSettings(asr={'api_key':'fake-new'}))
                self.assertTrue(get()['asr']['has_key']);self.assertEqual(get()['asr']['api_key'],'')
                save(translation.CloudSettings(asr={'clear_api_key':True}))
                self.assertFalse(get()['asr']['has_key'])
                self.assertNotIn('fake-new',pipeline.services_path.read_text())
                self.assertNotIn('clear_api_key',pipeline.services_path.read_text())
                self.assertEqual(pipeline.services()['asr']['model'],'new-model')
                self.assertNotIn('fake-tts',json.dumps(get()))
            finally:pipeline.close()


if __name__=='__main__':unittest.main()
