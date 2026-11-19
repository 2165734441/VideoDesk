"""Persistent manual-login browser; reads only the requested ordinary player."""
import concurrent.futures
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlparse

from .video_sources import douyin_video_id


def detail_media(payload, video_id):
    detail = payload.get('aweme_detail') or {}
    if str(detail.get('aweme_id', '')) != video_id:
        return None
    video = detail.get('video') or {}
    address = video.get('play_addr') or {}
    urls = address.get('url_list') or []
    url = next((v for v in urls if isinstance(v,str) and urlparse(v).scheme in ('http','https')), None)
    if not url:
        return None
    return {'url':url,'title':detail.get('desc') or '抖音视频',
            'duration':video.get('duration',0)/1000 or None,
            'width':video.get('width'),'height':video.get('height'),'video_id':video_id}


class LoginBrowser:
    def __init__(self, data):
        self.data=Path(data)
        # One worker is not a tuning choice: Playwright's sync API and the
        # persistent context belong to the thread that created them. Every
        # caller therefore shares a queue, and the queue depth has to be
        # visible so a waiter knows how long its own turn is away.
        self.pool=concurrent.futures.ThreadPoolExecutor(max_workers=1,thread_name_prefix='login')
        self.context=None
        self.engine=None
        self.details={}
        self.observed=set()
        # Tabs this module opened for live checks. They are recycled through
        # about:blank instead of being left on a Douyin page, which otherwise
        # keeps several hundred MB alive per anchor for as long as the app runs.
        self.scratch=set()
        self.queue_lock=threading.Lock()
        self.queued=0

    def submit(self, fn):
        """Queue work and expose how much of it is still waiting."""
        with self.queue_lock: self.queued += 1
        def wrapped():
            # The moment this runs the job has left the queue; the waiter only
            # needs to know what is still ahead of it.
            with self.queue_lock: self.queued = max(0, self.queued - 1)
            return fn()
        return self.pool.submit(wrapped)

    def depth(self):
        with self.queue_lock: return self.queued

    def observe(self,page):
        if page in self.observed:return
        self.observed.add(page)
        def response(value):
            # These are normal responses to the user's browser navigation.
            # Do not construct signed API requests or solve challenges.
            if value.status != 200:return
            from . import following
            if '/user/profile/self/' in value.url or following.is_following_response(value.url):
                # Bind responses to the current cookie session; a delayed response
                # from the previous account must not repopulate its cleared cache.
                from http.cookies import SimpleCookie
                marker=following.session_fingerprint(self.context.cookies(['https://www.douyin.com']))
                jar=SimpleCookie();jar.load(value.request.header_value('cookie') or '')
                requested=following.session_fingerprint([{'name':k,'value':v.value} for k,v in jar.items()])
                following.set_session(self,marker)
                if requested!=marker:return
            if '/user/profile/self/' in value.url:
                try:
                    payload=value.json();user=payload.get('user') or (payload.get('data') or {}).get('user') or {}
                    following.set_profile(self,user)
                except Exception:pass
            if following.is_following_response(value.url):
                try:
                    from .live_browser import following_data
                    payload=value.json()
                    following.observe(self,value.url,payload,following_data)
                except Exception:pass
                return
            if '/aweme/v1/web/aweme/detail/' not in value.url:return
            try:
                payload=value.json();detail=payload.get('aweme_detail') or {}
                vid=str(detail.get('aweme_id',''));media=detail_media(payload,vid)
                if media:self.details[(page,vid)]=media
            except Exception:pass
        page.on('response',response)

    def ensure(self):
        from .runtime_support import require
        require('browser')
        if self.context:
            try:self.context.cookies();return self.context
            except Exception:self.context=None
        from playwright.sync_api import sync_playwright
        if not self.engine:self.engine=sync_playwright().start()
        bundled=os.environ.get('VIDEO_DESK_BROWSER_EXE')
        choices=[{'executable_path':bundled}] if bundled else []
        choices += [{'channel':c} for c in ('msedge','chrome')]
        for options in choices:
            try:
                self.context=self.engine.chromium.launch_persistent_context(
                    str(self.data/'browser-profile'),**options,headless=False,viewport=None,accept_downloads=False)
                self.details.clear();self.observed.clear()
                for page in self.context.pages:self.observe(page)
                self.context.on('page',self.observe)
                return self.context
            except Exception:pass
        raise RuntimeError('登录浏览器未能打开。请关闭上次映格的登录窗口后重试，或安装 Edge / Chrome。')

    def page_for(self,url,navigate=False):
        context=self.ensure();vid=douyin_video_id(url)
        pages=[p for p in context.pages if not p.is_closed()]
        page=next((p for p in reversed(pages) if
                   (douyin_video_id(p.url)==vid if vid else p.url==url)),None)
        if page is None:
            # Reuse a recycled check tab before opening another one. Leftover
            # tabs are the reason a long run climbed to several GB.
            page=next((p for p in pages if p.url=='about:blank' and p in self.scratch),None)
            if page is None:
                page=context.new_page();self.scratch.add(page);self.observe(page)
            if navigate:
                try:page.goto(url,wait_until='domcontentloaded',timeout=30000)
                except Exception:pass
        return page

    def release(self,page,keep=False):
        """Close a check tab so its renderer (and video buffers) are freed.

        keep=True parks the tab on about:blank and leaves it in scratch. One
        warm renderer makes every later navigation much faster than building a
        fresh one, and about:blank still drops the player and its buffers.
        """
        if page is None or page not in self.scratch:return
        if keep and not page.is_closed():
            try:
                page.goto('about:blank',wait_until='domcontentloaded',timeout=5000)
                try:page.evaluate('() => { try { window.__vd_seen = null } catch (e) {} }')
                except Exception:pass
                for key in [k for k in self.details if k[0] is page]:self.details.pop(key,None)
                return
            except Exception:pass
        try:
            if not page.is_closed():page.close()
        except Exception:
            # Leave it in scratch: recycle() retries instead of leaking the tab.
            try:
                if not page.is_closed():page.goto('about:blank',wait_until='domcontentloaded',timeout=5000)
            except Exception:pass
            return
        self.scratch.discard(page);self.observed.discard(page)
        for key in [k for k in self.details if k[0] is page]:self.details.pop(key,None)

    def recycle(self):
        """Close every check tab this module opened; used before a new sweep.

        A single blank tab is kept: with one worker there is never a second
        check running, so that tab is the warm renderer the next check reuses.
        """
        warm=None
        for page in list(self.scratch):
            try:
                if warm is None and not page.is_closed() and page.url=='about:blank':
                    warm=page;continue
            except Exception:pass
            self.release(page)
        return warm

    def open(self,url):
        def action():
            page=self.page_for(url,navigate=True);page.bring_to_front()
            return {'message':'请在映格登录浏览器中确认视频可以播放，完成验证后返回继续。'}
        return self.pool.submit(action)

    def cookies(self,url):
        # Reopen the saved profile after service restart, instead of returning
        # an empty jar simply because this process has not opened a page yet.
        return self.pool.submit(lambda:self.ensure().cookies([url])).result(timeout=45)

    def close(self):
        def action():
            if self.context:self.context.close();self.context=None
            if self.engine:self.engine.stop();self.engine=None
        try:self.pool.submit(action).result(timeout=15)
        finally:self.pool.shutdown(wait=False)

    def playable_media(self,url):
        if not douyin_video_id(url):return None
        def action():
            vid=douyin_video_id(url);page=self.page_for(url,navigate=True)
            deadline=time.monotonic()+40
            while time.monotonic()<deadline:
                if page.is_closed():return None
                # Navigation to a different video must never export it under
                # the requested video's id.
                if douyin_video_id(page.url)!=vid:return None
                exact=self.details.get((page,vid))
                if exact:return {**exact,'user_agent':page.evaluate('navigator.userAgent')}
                media=page.evaluate("""() => {
                    const videos=[...document.querySelectorAll('video')].filter(v=>{
                        const r=v.getBoundingClientRect();const s=getComputedStyle(v);
                        return r.width>80&&r.height>80&&r.bottom>0&&r.top<innerHeight&&
                            s.visibility!=='hidden'&&s.display!=='none'&&v.readyState>=2;
                    }).sort((a,b)=>Number(a.paused)-Number(b.paused)||
                        b.getBoundingClientRect().width*b.getBoundingClientRect().height-
                        a.getBoundingClientRect().width*a.getBoundingClientRect().height);
                    const v=videos[0];if(!v)return null;
                    return {url:v.currentSrc||v.src,duration:Number.isFinite(v.duration)?v.duration:null,
                        width:v.videoWidth,height:v.videoHeight,title:document.title,user_agent:navigator.userAgent};
                }""")
                if media and urlparse(media.get('url','')).scheme in ('http','https'):return media
                page.wait_for_timeout(400)
            page.bring_to_front()
            return None
        return self.pool.submit(action).result(timeout=80)
