"""Read live/following data produced by ordinary navigation in our login browser."""
import json
import re
import time
from urllib.parse import urlparse, unquote, parse_qs
from . import following
from .video_sources import douyin_video_id


SUPPORTED_SOURCES = ('user', 'live', 'reflow', 'video', 'shop', 'short')


def normalize(url):
    """Canonicalise a Douyin profile, live, video or store share link.

    A video/store link is a *source* for finding an anchor, not a live room
    itself. Keeping it canonical here lets the caller save the anchor quickly
    and resolve the author in the background instead of blocking the phone's
    short HTTP read timeout.
    """
    match = re.search(r'https?://[^\s<>"，。]+', str(url))
    if not match: raise ValueError('请粘贴抖音主播主页、直播间、视频或店铺分享链接')
    url = match.group(0).rstrip('.,;!）)')
    p = urlparse(url)
    if p.scheme not in ('http', 'https') or p.username or p.password or p.port not in (None, 80, 443):
        raise ValueError('链接格式无效')
    host = (p.hostname or '').lower()
    allowed = ('live.douyin.com', 'www.douyin.com', 'douyin.com', 'm.douyin.com',
               'v.douyin.com', 'www.iesdouyin.com', 'webcast.amemv.com',
               'haohuo.jinritemai.com', 'jinritemai.com', 'z.douyin.com')
    if host not in allowed:
        raise ValueError('当前主播录播仅支持抖音主页、直播、视频或店铺链接')
    # A direct profile/live address is safe to keep after removing tracking
    # parameters; those query values never identify a different room.
    if host == 'live.douyin.com':
        if not re.fullmatch(r'/\d{4,30}/?', p.path): raise ValueError('请使用包含完整房间号的抖音直播间链接')
        return 'https://live.douyin.com/'+p.path.strip('/')
    if host in ('www.douyin.com', 'douyin.com', 'm.douyin.com') and p.path.startswith('/user/'):
        if not re.fullmatch(r'/user/[A-Za-z0-9_-]{8,200}/?', p.path):
            raise ValueError('主播主页链接不完整')
        return 'https://www.douyin.com'+p.path.rstrip('/')
    if host in ('www.douyin.com','douyin.com','m.douyin.com') and re.fullmatch(r'/live/\d{4,30}/?',p.path):
        return 'https://live.douyin.com/'+p.path.strip('/').split('/')[-1]
    if host == 'webcast.amemv.com':
        if not re.fullmatch(r'/douyin/webcast/reflow/\d{4,30}/?',p.path):
            raise ValueError('请使用抖音直播分享链接或主播主页链接')
        # A reflow room_id is NOT a public web_rid. Preserve its namespace.
        return 'https://webcast.amemv.com'+p.path.rstrip('/')+'/'
    if host in ('www.douyin.com','douyin.com','m.douyin.com'):
        if re.fullmatch(r'/video/\d{5,30}/?', p.path):
            return 'https://www.douyin.com'+p.path.rstrip('/')
        if p.path.startswith('/shop/') and len(p.path) > 6:
            return 'https://www.douyin.com'+p.path.rstrip('/')
        raise ValueError('请使用主播主页、直播间、视频或店铺分享链接')
    if host == 'www.iesdouyin.com':
        found = re.fullmatch(r'/share/video/(\d{5,30})/?', p.path)
        if not found: raise ValueError('抖音视频分享链接不完整')
        return 'https://www.douyin.com/video/'+found.group(1)
    if host == 'v.douyin.com':
        if not re.fullmatch(r'/[A-Za-z0-9_-]{4,80}/?', p.path):
            raise ValueError('抖音分享短链不完整')
        return 'https://v.douyin.com'+p.path.rstrip('/')
    if host == 'z.douyin.com':
        if not re.fullmatch(r'/[A-Za-z0-9_-]{4,80}/?', p.path):
            raise ValueError('抖音分享短链不完整')
        return 'https://z.douyin.com'+p.path.rstrip('/')
    if host in ('haohuo.jinritemai.com', 'jinritemai.com'):
        if not (p.path.startswith('/views/shop/index') or p.path.startswith('/ecommerce/trade/detail')):
            raise ValueError('请使用抖音店铺首页链接')
        return p._replace(fragment='').geturl()
    raise ValueError('请使用主播主页、直播间、视频或店铺分享链接')


def source_kind(url):
    p = urlparse(normalize(url))
    host = (p.hostname or '').lower()
    if host in ('live.douyin.com','webcast.amemv.com'): return 'reflow' if host=='webcast.amemv.com' else 'live'
    if host in ('www.douyin.com','douyin.com','m.douyin.com'):
        if p.path.startswith('/user/'): return 'user'
        if p.path.startswith('/live/') or p.path.startswith('/video/'): return 'video' if p.path.startswith('/video/') else 'live'
        if p.path.startswith('/shop/'): return 'shop'
    if host in ('haohuo.jinritemai.com','jinritemai.com','z.douyin.com'): return 'shop'
    return 'short'


def walk(payload):
    if isinstance(payload, dict):
        yield payload
        for value in payload.values():
            if isinstance(value, (dict, list)): yield from walk(value)
    elif isinstance(payload, list):
        for value in payload:
            if isinstance(value, (dict, list)): yield from walk(value)


def page_payloads(scripts):
    """Decode page data, including streamed React records, without executing scripts."""
    chunks = []
    for script in scripts:
        text = script.strip()
        assignment = re.fullmatch(r'(?:window\.)?(?:_ROUTER_DATA|__INITIAL_STATE__|__NEXT_DATA__)\s*=\s*(.*?);?', text, re.S)
        if assignment: text = assignment[1].rstrip(';')
        for candidate in (text, unquote(text)):
            try:
                yield json.loads(candidate)
                break
            except (ValueError, TypeError): pass
        match = re.fullmatch(r'self\.__rsc_f\.push\((\[.*\])\);?', text, re.S)
        if match:
            try:
                chunk = json.loads(match[1])
                if len(chunk) > 1 and chunk[0] == 1 and isinstance(chunk[1], str): chunks.append(chunk[1])
            except (ValueError, TypeError): pass
    for chunk in [*chunks, ''.join(chunks)]:
        for line in chunk.split('\n'):
            prefix, sep, raw = line.partition(':')
            if sep and re.fullmatch(r'[0-9a-f]+', prefix, re.I):
                try: yield json.loads(raw)
                except ValueError: pass


def numeric_id(value):
    # JSON integers are exact in Python; floats can already have lost room ID digits.
    return str(value) if type(value) in (str, int) and re.fullmatch(r'\d{1,30}', str(value)) else ''


def room_identity(obj):
    owner = obj.get('owner') or obj.get('anchor') or {}
    if not isinstance(owner, dict): owner = {}
    sec = str(owner.get('sec_uid') or owner.get('secUid') or '')
    rid = numeric_id(obj.get('web_rid') or obj.get('webRid') or owner.get('web_rid') or owner.get('webRid'))
    room = numeric_id(obj.get('id_str') or obj.get('idStr') or obj.get('room_id_str') or obj.get('room_id') or obj.get('id'))
    return {'sec_uid':sec,'web_rid':rid,'room_id':room}


def room_matches(obj, expected):
    identity=room_identity(obj)
    comparable=[identity[k]==expected[k] for k in identity if expected and expected.get(k) and identity[k]]
    return bool(comparable) and all(comparable)


def room_data(payload, quality='origin', expected=None, trusted=False):
    if not expected or not any(expected.get(k) for k in ('sec_uid','web_rid','room_id')):return None
    candidates=[obj for obj in walk(payload) if room_matches(obj,expected)]
    # A response requested with the exact web_rid may omit that field in its
    # single primary room. That trust never extends to recommendations/nesting.
    data=payload.get('data') if isinstance(payload,dict) else None
    primary=data.get('data') if isinstance(data,dict) else None
    if trusted and isinstance(primary,list) and len(primary)==1 and isinstance(primary[0],dict):
        obj=primary[0];identity=room_identity(obj)
        conflicts=any(identity[k] and expected.get(k) and identity[k]!=expected[k] for k in identity)
        if not conflicts and not room_matches(obj,expected):candidates.append(obj)
    for obj in candidates:
        status=str(obj.get('status'))
        if status=='4' and (obj.get('owner') or obj.get('id_str') or obj.get('idStr')):
            owner=obj.get('owner') or {}
            return {'online':False,'state':'offline','message':'主播未开播','name':owner.get('nickname',''),
                    'room_id':numeric_id(obj.get('id_str') or obj.get('idStr') or obj.get('room_id_str') or obj.get('room_id') or obj.get('id'))}
        stream = obj.get('stream_url') or obj.get('streamUrl')
        if not isinstance(stream, dict): continue
        def playable(value):
            if not isinstance(value, str): return False
            parsed = urlparse(value)
            return parsed.scheme in ('http', 'https') and bool(parsed.hostname) and not parsed.username and not parsed.password
        choices = {}
        for name in ('flv_pull_url', 'hls_pull_url_map', 'flvPullUrl', 'hlsPullUrlMap'):
            urls = stream.get(name)
            if isinstance(urls, dict):
                for key, value in urls.items():
                    if playable(value): choices.setdefault(str(key).lower(), value)
        # Newer players can publish their quality list only through the SDK data.
        core = stream.get('live_core_sdk_data') or stream.get('liveCoreSdkData') or {}
        pull = (core.get('pull_data') or core.get('pullData')) if isinstance(core, dict) else None
        raw = (pull.get('stream_data') or pull.get('streamData')) if isinstance(pull, dict) else None
        if isinstance(raw, str):
            try: raw = json.loads(raw)
            except ValueError: raw = None
        if isinstance(raw, dict):
            for key, value in (raw.get('data') or {}).items():
                main = value.get('main') if isinstance(value, dict) else None
                if not isinstance(main, dict): continue
                candidate = next((main.get(name) for name in ('flv', 'hls') if playable(main.get(name))), None)
                if candidate: choices.setdefault(str(key).lower(), candidate)
        hls=stream.get('hls_pull_url') or stream.get('hlsPullUrl')
        if not choices and playable(hls): choices['origin'] = hls
        if not choices: continue
        preferred = {'origin': ['origin', 'uhd', 'full_hd1', 'hd1', 'hd', 'sd1', 'sd'],
                     'high': ['hd1', 'hd', 'full_hd1', 'sd1', 'sd'],
                     'standard': ['sd1', 'sd', 'ld1', 'ld', 'hd1', 'hd']}[quality]
        stream_url = next((choices[k] for k in preferred if k in choices), next(iter(choices.values())))
        owner = obj.get('owner') or obj.get('anchor') or {}
        online=True if status=='2' else False if status=='4' else None
        return {'online': online, 'state': 'online' if online is True else 'offline' if online is False else 'unknown', 'url': stream_url,
                'name': owner.get('nickname') or obj.get('title') or '抖音直播',
                'room_id': numeric_id(obj.get('id_str') or obj.get('idStr') or obj.get('room_id_str') or obj.get('room_id') or obj.get('id'))}
    return None


def _payload_identity(payload, video_id='', strict=False):
    """Find the *author* of a video, not a recommended user beside it."""
    if not isinstance(payload, dict):
        return None
    detail = payload.get('aweme_detail') or {}
    if isinstance(detail, dict) and str(detail.get('aweme_id') or '') == str(video_id or ''):
        author = detail.get('author') or {}
        if isinstance(author, dict) and re.fullmatch(r'[A-Za-z0-9_-]{8,200}', str(author.get('sec_uid') or '')):
            return {'kind':'user','url':'https://www.douyin.com/user/'+str(author['sec_uid']),
                    'sec_uid':str(author['sec_uid']),'name':str(author.get('nickname') or '')[:100]}
    # Most page-data blobs put the author under a key literally named author.
    for obj in walk(payload):
        if not isinstance(obj, dict):
            continue
        if video_id and str(obj.get('aweme_id') or obj.get('id') or '') != str(video_id):
            continue
        author = obj.get('author')
        if not isinstance(author, dict):
            continue
        sec = str(author.get('sec_uid') or author.get('secUid') or '')
        if re.fullmatch(r'[A-Za-z0-9_-]{8,200}', sec):
            return {'kind':'user','url':'https://www.douyin.com/user/'+sec,'sec_uid':sec,
                    'name':str(author.get('nickname') or '')[:100]}
    if video_id or strict:
        return None
    # A profile page can expose the user itself; require a few profile-ish
    # fields so a recommendation card is not mistaken for the page owner.
    for obj in walk(payload):
        if not isinstance(obj, dict):
            continue
        sec = str(obj.get('sec_uid') or obj.get('secUid') or '')
        nick = obj.get('nickname')
        if not (isinstance(nick, str) and re.fullmatch(r'[A-Za-z0-9_-]{8,200}', sec)):
            continue
        if any(key in obj for key in ('unique_id','short_id','follower_count','following_count','aweme_count','total_favorited')):
            return {'kind':'user','url':'https://www.douyin.com/user/'+sec,'sec_uid':sec,'name':nick[:100]}
    return None


def resolve_identity(browser, url, cancelled=lambda: False):
    """Turn a video/store/short share link into the author's profile.

    This is intentionally a separate, slower step. The phone can save an
    anchor immediately; the desktop resolves its author when it starts checking
    the anchor, so the phone's 12-second HTTP read timeout is never at risk.
    """
    url = normalize(url)
    kind = source_kind(url)
    if kind == 'user':
        m = re.search(r'/user/([A-Za-z0-9_-]{8,200})', url)
        return {'kind':'user','url':url,'sec_uid':m.group(1) if m else ''}
    if kind in ('live','reflow'):
        return {'kind':'live','url':url}
    # A store share link sometimes carries the creator in its query parameters.
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ('sec_uid','secUid','author_id','authorId'):
        value = next((v for v in query.get(key, []) if re.fullmatch(r'[A-Za-z0-9_-]{8,200}', v)), '')
        if value:
            if value.startswith('MS4wLj'): return {'kind':'user','url':'https://www.douyin.com/user/'+value,'sec_uid':value}
    if cancelled():
        raise ValueError('已停止读取分享链接')

    def action():
        page = browser.page_for(url, navigate=True)
        deadline = time.monotonic() + 35
        parsed_once = False
        try:
            while time.monotonic() < deadline:
                if cancelled():
                    raise ValueError('已停止读取分享链接')
                if page.is_closed():
                    raise ValueError('登录浏览器已关闭，请重新打开')
                final = page.url or ''
                fp = urlparse(final)
                fhost = (fp.hostname or '').lower()
                shop_page = fhost in ('haohuo.jinritemai.com','jinritemai.com','z.douyin.com') or fp.path.startswith('/shop/')
                if fhost in ('www.douyin.com','douyin.com','m.douyin.com') and '/user/' in fp.path:
                    sec = fp.path.split('/user/', 1)[-1].split('/', 1)[0].strip('/')
                    if re.fullmatch(r'[A-Za-z0-9_-]{8,200}', sec):
                        return {'kind':'user','url':'https://www.douyin.com/user/'+sec,
                                'sec_uid':sec,'name':str(page.title() or '').replace('的抖音 - 抖音','').strip()[:100]}
                if fhost == 'live.douyin.com' or fhost == 'webcast.amemv.com':
                    if fhost == 'live.douyin.com' and re.fullmatch(r'/\d{4,30}/?', fp.path):
                        return {'kind':'live','url':'https://live.douyin.com/'+fp.path.strip('/')}
                # LoginBrowser already captures the exact aweme detail response,
                # including its author; use it before parsing megabytes of page
                # scripts.
                for (owner_page, vid), detail in list(getattr(browser, 'details', {}).items()):
                    if owner_page is page and isinstance(detail, dict) and detail.get('sec_uid'):
                        sec = str(detail['sec_uid'])
                        return {'kind':'user','url':'https://www.douyin.com/user/'+sec,'sec_uid':sec,
                                'name':str(detail.get('nickname') or '')[:100]}
                if not parsed_once and time.monotonic() + 30 < deadline:
                    parsed_once = True
                    try:
                        scripts = page.locator('script').all_text_contents()
                        for payload in page_payloads(scripts):
                            ident = _payload_identity(payload, douyin_video_id(final), shop_page)
                            if ident:
                                return ident
                    except Exception:
                        pass
                page.wait_for_timeout(500)
            message = '没有从分享链接读取到主播身份。请在抖音打开该视频，分享主播主页链接；店铺链接请同时带上主播主页链接。'
            raise ValueError(message)
        finally:
            if not page.is_closed():
                try: browser.release(page, keep=True)
                except Exception: pass
    return browser.pool.submit(action).result(timeout=85)


def following_data(payload):
    """Only user objects from an observed following-list response are supplied here."""
    found = {}
    for obj in following.rows(payload):
        if not isinstance(obj,dict):continue
        sec = obj.get('sec_uid') or obj.get('secUid')
        nick = obj.get('nickname')
        if isinstance(sec, str) and re.fullmatch(r'[A-Za-z0-9_-]{8,200}', sec) and isinstance(nick, str):
            found[sec] = {'url': 'https://www.douyin.com/user/'+sec, 'name': nick[:100]}
    return list(found.values())


def profile_room(payload, sec_uid):
    for user in walk(payload):
        if str(user.get('sec_uid') or user.get('secUid') or '') != sec_uid: continue
        room = user.get('room_data') or user.get('roomData') or {}
        if isinstance(room, str):
            try: room = json.loads(room)
            except ValueError: room = {}
        if not isinstance(room,dict): continue
        owner=room.get('owner') or {}
        if not isinstance(owner,dict): owner={}
        rid = numeric_id(room.get('web_rid') or room.get('webRid') or owner.get('web_rid') or owner.get('webRid') or user.get('web_rid') or user.get('webRid'))
        if len(rid)>=4: return 'https://live.douyin.com/'+rid
    return None


def resolve(browser, url, quality='origin', cancelled=lambda:False):
    url = normalize(url)
    identity = None
    if source_kind(url) in ('video', 'shop', 'short'):
        identity = resolve_identity(browser, url, cancelled)
        resolved = normalize(identity.get('url') or '')
        if resolved != url:
            url = resolved
    identity_name = str((identity or {}).get('name') or '')[:100]
    # Read the queue before joining it: everybody already waiting is ahead of us.
    ahead = browser.depth() if hasattr(browser,'depth') else 0
    def action():
        # A previous check may have failed to close its tab; start from a clean
        # slate so a long-running machine cannot accumulate renderers.
        if hasattr(browser,'recycle'): browser.recycle()
        page = browser.page_for(url, navigate=False)
        keep_page=[False]
        found = []
        destination = []
        failures=[]
        expected_user = urlparse(url).path.split('/user/')[-1].rstrip('/') if '/user/' in urlparse(url).path else None
        expected={'sec_uid':expected_user} if expected_user else {}
        initial=urlparse(url)
        if initial.hostname=='live.douyin.com': expected['web_rid']=initial.path.strip('/')
        if initial.hostname=='webcast.amemv.com': expected['room_id']=initial.path.rstrip('/').rsplit('/',1)[-1]
        def consume(payload, trusted=False):
            if expected_user:
                room=profile_room(payload,expected_user)
                if room:
                    expected['web_rid']=urlparse(room).path.strip('/')
                    if room not in destination: destination.append(room)
                elif any(str(v.get('sec_uid') or v.get('secUid') or '')==expected_user and str(v.get('live_status'))=='0' for v in walk(payload)):
                    found.append({'online':False,'state':'offline','message':'主播未开播','name':identity_name})
            result=room_data(payload,quality,expected, trusted=trusted)
            if result:found.append(result)
        def response(value):
            if value.status in (401,403,429,444) and urlparse(value.url).hostname in ('www.douyin.com','live.douyin.com','webcast.amemv.com'):
                failures.append('抖音拒绝访问（HTTP '+str(value.status)+'），请在专用浏览器完成登录／验证，或改用手机抖音录屏')
            if value.status != 200 or not any(p in value.url for p in ('/webcast/room/', '/aweme/v1/web/user/profile/')): return
            try:
                payload = value.json()
                if expected_user and '/user/profile/' in value.url:
                    consume(payload)
                    return
                requested_rid = parse_qs(urlparse(value.url).query).get('web_rid', [''])[0]
                if requested_rid and requested_rid != expected.get('web_rid'): return
                consume(payload, trusted=bool(requested_rid and requested_rid==expected.get('web_rid')))
            except Exception: pass
        page.on('response', response)
        # Script bodies are several megabytes. Reading every one of them on
        # every 500 ms poll used to dominate the whole check, so the page
        # remembers which ones were already handed over.
        reader = """() => {
          window.__vd_seen = window.__vd_seen || new Set();
          const out = [];
          for (const s of document.scripts) {
            const t = s.textContent || '';
            if (!t) continue;
            const k = t.length + ':' + t.slice(0, 96);
            if (window.__vd_seen.has(k)) continue;
            window.__vd_seen.add(k);
            out.push(t);
          }
          return { scripts: out, text: document.body ? document.body.innerText : '' };
        }"""
        def snapshot():
            try: return page.evaluate(reader)
            except Exception: return {'scripts': [], 'text': ''}
        try:
            # Attach the observer before this navigation, including on an existing tab.
            try: page.goto(url, wait_until='domcontentloaded', timeout=25000)
            except Exception as exc:
                detail=str(exc)
                if 'ERR_' in detail:failures.append('直播页面连接失败：'+re.search(r'ERR_[A-Z_0-9]+',detail).group(0))
            if urlparse(page.url).hostname in ('www.douyin.com','douyin.com') and '/video/' in urlparse(page.url).path:
                raise ValueError('分享链接指向已发布的视频，请粘贴直播间或主播主页链接')
            landing=urlparse(page.url)
            if initial.hostname=='v.douyin.com':
                if landing.hostname=='live.douyin.com':expected['web_rid']=landing.path.strip('/')
                elif landing.hostname=='webcast.amemv.com':expected['room_id']=landing.path.rstrip('/').rsplit('/',1)[-1]
                elif '/user/' in landing.path:
                    expected_user=landing.path.split('/user/')[-1].rstrip('/');expected['sec_uid']=expected_user
            deadline = time.monotonic()+12
            while time.monotonic() < deadline:
                if cancelled(): raise ValueError('已停止读取直播间')
                if page.is_closed(): raise ValueError('登录浏览器已关闭，请重新打开')
                if destination and page.url != destination[-1]:
                    try: page.goto(destination[-1], wait_until='domcontentloaded', timeout=20000)
                    except Exception: pass
                if found: break
                view = snapshot()
                for payload in page_payloads(view['scripts']):consume(payload)
                if found: break
                text = view['text'] or ''
                if not found and any(t in text for t in ('访问过于频繁','请完成下方验证','请完成验证','安全验证')):
                    failures.append('抖音要求安全验证，请在专用浏览器手动完成，或用手机抖音录屏')
                if not expected_user and any(s in text for s in ('直播已结束', '暂未开播', '还未开播', '暂时没有开播')):
                    return {'online': False, 'message': '主播未开播'}
                page.wait_for_timeout(500)
            if not found:
                message=failures[-1] if failures else '没有取得直播流。请在专用浏览器确认能播放，完成登录或验证后重试；也可用手机抖音录屏'
                # Only a login/verification wall is worth keeping on screen.
                keep_page[0]=any(t in message for t in ('验证','登录'))
                raise ValueError(message)
            result = found[-1]
            if not result.get('online'): return result
            if not result.get('room_id') and expected.get('web_rid'):
                result['room_id'] = 'web:'+expected['web_rid']
            result.update(referer=page.url, user_agent=page.evaluate('navigator.userAgent'))
            result['cookies'] = browser.ensure().cookies([result['url']])
            return result
        finally:
            page.remove_listener('response', response)
            # Close the tab so the next check starts clean; a page parked at a
            # verification wall is the one thing the user must finish by hand,
            # so that single case stays open.
            if keep_page[0]:
                # Hand this tab to the user; recycle() must never close it.
                if hasattr(browser,'scratch'): browser.scratch.discard(page)
            else:
                browser.release(page, keep=True)
    future = browser.submit(action) if hasattr(browser,'submit') else browser.pool.submit(action)
    import concurrent.futures
    # A check takes up to ~40 s (navigation plus one poll window). Waiting on a
    # single shared worker means the real answer is "my turn plus everyone
    # still queued", so the deadline has to include them instead of reporting a
    # queue timeout that is really just our own turn not having arrived yet.
    deadline = time.monotonic()+60+45*ahead
    while time.monotonic() < deadline:
        if cancelled():
            if future.cancel() and hasattr(browser,'queue_lock'):
                # A cancelled job never runs its wrapper, so the counter must be
                # corrected here or every later check would wait for a ghost.
                with browser.queue_lock: browser.queued=max(0,browser.queued-1)
            raise ValueError('已停止读取直播间')
        try: return future.result(timeout=.5)
        except concurrent.futures.TimeoutError: pass
    if future.cancel() and hasattr(browser,'queue_lock'):
        with browser.queue_lock: browser.queued=max(0,browser.queued-1)
    raise ValueError('直播间读取队列超时，请减少同时检查的主播并重试')


def import_following(browser):
    def action():
        page = browser.page_for('https://www.douyin.com/follow', navigate=False)
        cached=getattr(browser,'live_following',{})
        if time.monotonic()-cached.get('time',0)>900:browser.live_following={}
        try:
            if not page.url.startswith('https://www.douyin.com'):
                page.goto('https://www.douyin.com/follow', wait_until='domcontentloaded', timeout=30000)
            page.bring_to_front()
            profile=getattr(browser,'live_following_profile',{})
            own=profile.get('sec_uid')
            cached=getattr(browser,'live_following',{})
            if own and cached.get('owner') not in (None,own,profile.get('uid')):
                browser.live_following={}
            if own and not getattr(browser,'live_following',{}).get('first_seen'):
                page.goto('https://www.douyin.com/user/'+own,wait_until='domcontentloaded',timeout=30000)
                page.wait_for_timeout(1500)
            # Click the visible following-list entry, without constructing private API calls.
            button = page.get_by_text('关注的人', exact=True)
            if button.count(): button.first.click(timeout=3000)
            elif own:
                button=page.get_by_text('关注',exact=True)
                if button.count()==1:button.click(timeout=3000)
            stable=0
            for _ in range(70):
                before=len(getattr(browser,'live_following',{}).get('users',{}))
                # Scroll the visible list containing profile links, not the feed behind a modal.
                moved=page.evaluate("""() => {
                  const links=[...document.querySelectorAll('a[href*="/user/"]')].filter(a=>a.getBoundingClientRect().height>0);
                  const counts=new Map();for(const a of links){let p=a.parentElement;while(p){const s=getComputedStyle(p);if(p.scrollHeight>p.clientHeight+50&&/(auto|scroll)/.test(s.overflowY))counts.set(p,(counts.get(p)||0)+1);p=p.parentElement;}}
                  const list=[...counts].sort((a,b)=>b[1]-a[1])[0]?.[0];if(!list)return false;
                  list.scrollTop+=Math.max(400,list.clientHeight*.8);list.dispatchEvent(new Event('scroll',{bubbles:true}));return true;
                }""")
                if not moved:page.mouse.wheel(0,900)
                page.wait_for_timeout(1000)
                state=getattr(browser,'live_following',{})
                if own and state.get('owner') not in (None,own,profile.get('uid')):
                    browser.live_following={}
                    raise ValueError('当前读取的是其他人的关注列表，请打开自己的主页和关注人员列表')
                stable=stable+1 if len(state.get('users',{}))==before else 0
                if following.summary(state)['complete'] or stable>=12:break
            return following.summary(getattr(browser,'live_following',{}))
        except Exception as exc:
            result=following.summary(getattr(browser,'live_following',{}))
            if not result['users']:raise ValueError('关注列表未能打开，请先在专用浏览器登录并打开自己的关注人员列表') from exc
            return result
    return browser.pool.submit(action).result(timeout=140)
