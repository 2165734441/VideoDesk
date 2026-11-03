import tempfile
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from yt_dlp.utils import DownloadError
from integrations.login_browser import LoginBrowser, detail_media
from integrations.video_sources import douyin_video_id, resolve_info


class LoginBrowserTests(unittest.TestCase):
    def test_modal_links_identify_only_the_requested_douyin_video(self):
        for url in ('https://www.douyin.com/video/7655660511175050609',
                    'https://www.douyin.com/user/self?modal_id=7655660511175050609&showTab=favorite_collection'):
            self.assertEqual(douyin_video_id(url),'7655660511175050609')
        for url in ('https://douyin.com.example.org/video/123','https://www.douyin.com/user/123',
                    'https://www.douyin.com/user/self?modal_id=wrong'):
            self.assertEqual(douyin_video_id(url),'')

    def test_normal_detail_response_cannot_supply_a_different_video(self):
        payload={'aweme_detail':{'aweme_id':'123','desc':'测试','video':{'duration':1500,
                 'width':320,'height':240,'play_addr':{'url_list':['javascript:bad','https://cdn.test/video.mp4']}}}}
        self.assertIsNone(detail_media(payload,'456'))
        self.assertEqual(detail_media(payload,'123')['duration'],1.5)
        self.assertEqual(detail_media(payload,'123')['url'],'https://cdn.test/video.mp4')

    def test_saved_profile_is_reopened_when_cookies_are_requested_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            browser=LoginBrowser(folder);context=SimpleNamespace(cookies=Mock(return_value=[{'name':'session'}]))
            with patch.object(browser,'ensure',return_value=context) as ensure:
                self.assertEqual(browser.cookies('https://www.douyin.com/'),[{'name':'session'}])
                ensure.assert_called_once();context.cookies.assert_called_once_with(['https://www.douyin.com/'])
            browser.pool.shutdown()

    def test_existing_modal_tab_is_reused_without_new_login_tabs(self):
        with tempfile.TemporaryDirectory() as folder:
            browser=LoginBrowser(folder);page=SimpleNamespace(url='https://www.douyin.com/user/self?modal_id=123',is_closed=lambda:False)
            context=SimpleNamespace(pages=[page],new_page=Mock())
            with patch.object(browser,'ensure',return_value=context):
                self.assertIs(browser.page_for('https://www.douyin.com/video/123',True),page)
                context.new_page.assert_not_called()
            browser.pool.shutdown()

    def test_matching_player_fallback_refreshes_cookie_jar_after_navigation(self):
        url='https://www.douyin.com/video/123';ydl=Mock();ydl.params={'format':'best[height<=720]'}
        ydl.extract_info.side_effect=DownloadError('Fresh cookies needed');ydl.process_ie_result.side_effect=lambda v,**_:v
        browser=SimpleNamespace(playable_media=Mock(return_value={'url':'https://cdn.test/123.mp4','user_agent':'browser-UA'}),
            cookies=Mock(return_value=[{'domain':'.douyin.com','path':'/','name':'session','value':'private','secure':True,'expires':-1}]))
        info=resolve_info(ydl,url,browser)
        self.assertEqual(info['id'],'123');self.assertEqual(info['http_headers']['User-Agent'],'browser-UA')
        self.assertNotIn('Cookie',info['http_headers']);ydl.cookiejar.set_cookie.assert_called_once()

    def test_network_error_and_other_platforms_do_not_open_douyin_browser(self):
        browser=Mock();ydl=Mock();ydl.extract_info.side_effect=DownloadError('error')
        with self.assertRaises(DownloadError):resolve_info(ydl,'https://www.bilibili.com/video/123',browser)
        browser.playable_media.assert_not_called()
        ydl.extract_info.side_effect=RuntimeError('connection reset')
        with self.assertRaises(RuntimeError):resolve_info(ydl,'https://www.douyin.com/video/123',browser)
        browser.playable_media.assert_not_called()

    def test_real_downloader_accepts_player_metadata_and_saves_complete_bytes(self):
        from yt_dlp import YoutubeDL
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);content=b'ordinary player response fixture'*200
            (root/'sample.mp4').write_bytes(content)
            class Handler(SimpleHTTPRequestHandler):
                def log_message(self,*_):pass
            server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=folder))
            threading.Thread(target=server.serve_forever,daemon=True).start()
            try:
                browser=SimpleNamespace(playable_media=lambda _: {'url':f'http://127.0.0.1:{server.server_port}/sample.mp4','height':1080,'width':1920},cookies=lambda _:[])
                with YoutubeDL({'quiet':True,'noprogress':True,'cachedir':False,'format':'best[height<=720]','outtmpl':str(root/'saved.%(ext)s')}) as ydl:
                    with patch.object(ydl,'extract_info',side_effect=DownloadError('Fresh cookies needed')):
                        info=resolve_info(ydl,'https://www.douyin.com/video/123',browser)
                    self.assertEqual(ydl.params['format'],'best[height<=720]')
                    ydl.process_info(info)
                self.assertEqual((root/'saved.mp4').read_bytes(),content)
            finally:server.shutdown();server.server_close()
