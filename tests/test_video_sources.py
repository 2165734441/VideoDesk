"""Remote format selection, split YouTube audio/video and HTTP boundaries."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from yt_dlp.cookies import YoutubeDLCookieJar
from integrations.video_sources import cookie_origins, ffmpeg_input, resolved_streams, local_video, youtube_support


class VideoSourceTests(unittest.TestCase):
    def test_youtube_short_link_uses_youtube_login_cookie_origins(self):
        self.assertIn('https://www.youtube.com/',cookie_origins('https://youtu.be/video'))
        self.assertEqual(cookie_origins('https://youtube.com.example.org/video'),['https://youtube.com.example.org/video'])

    def test_separate_video_audio_and_format_specific_headers(self):
        info={'http_headers':{'User-Agent':'Video Desk','Referer':'https://www.youtube.com/'},'requested_formats':[
            {'vcodec':'avc1','acodec':'none','url':'https://cdn.example/video.mp4','http_headers':{'Origin':'https://www.youtube.com'}},
            {'vcodec':'none','acodec':'mp4a','url':'https://cdn.example/audio.m4a'}]}
        video,audio=resolved_streams(info,YoutubeDLCookieJar())
        self.assertEqual(video[-1],'https://cdn.example/video.mp4')
        self.assertEqual(audio[-1],'https://cdn.example/audio.m4a')
        self.assertIn('Origin: https://www.youtube.com\r\n',video[video.index('-headers')+1])

    def test_header_injection_local_protocol_fragments_and_live_rejected(self):
        fmt={'url':'https://cdn.example/video.mp4','http_headers':{'User-Agent':'ok','Referer':'bad\r\nCookie: secret','Cookie':'secret'}}
        args=ffmpeg_input({},fmt,YoutubeDLCookieJar())
        self.assertNotIn('secret',''.join(args))
        for url in ('file:///C:/private.mp4','http://user:password@example.com/video'):
            with self.assertRaises(ValueError):ffmpeg_input({},{'url':url},None)
        with self.assertRaises(ValueError):resolved_streams({'is_live':True},None)
        with self.assertRaises(ValueError):ffmpeg_input({},{'url':'https://cdn.example/video','protocol':'http_dash_segments'},None)

    def test_local_video_does_not_accept_missing_relative_or_non_video_files(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'test.mp4';path.write_bytes(b'video')
            self.assertEqual(local_video(str(path)),path.resolve())
            for invalid in ('relative.mp4',str(path.with_suffix('.txt')),str(Path(temp))):
                with self.assertRaises(ValueError):local_video(invalid)

    def test_installed_youtube_runtime_and_ejs_available(self):
        self.assertTrue(youtube_support()['ready'],youtube_support())

    def test_existing_system_http_proxy_used_for_cdn_and_loopback_bypassed(self):
        with patch('integrations.video_sources.proxy_bypass',return_value=False),patch('integrations.video_sources.getproxies',return_value={'https':'http://127.0.0.1:12345','http':'http://127.0.0.1:12345'}):
            remote=ffmpeg_input({},{'url':'https://cdn.example/video.mp4'},None)
            self.assertEqual(remote[remote.index('-http_proxy')+1],'http://127.0.0.1:12345')
            local=ffmpeg_input({},{'url':'http://127.0.0.1:45678/test.mp4'},None)
            self.assertNotIn('-http_proxy',local)


if __name__=='__main__':unittest.main()
