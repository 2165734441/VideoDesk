"""Read live/following data produced by ordinary navigation in our login browser."""
import json
import re
import time
from urllib.parse import urlparse


def normalize(url):
    match = re.search(r'https?://[^\s<>"，。]+', str(url))
    if not match: raise ValueError('请粘贴抖音直播间、主播主页或分享链接')
    url = match.group(0).rstrip('.,;!）)')
    p = urlparse(url)
    if p.scheme not in ('http', 'https') or p.username or p.password or p.port not in (None, 80, 443):
        raise ValueError('直播链接格式无效')
    if p.hostname not in ('live.douyin.com', 'www.douyin.com', 'douyin.com', 'v.douyin.com'):
        raise ValueError('当前直播录播仅支持抖音链接')
    if p.hostname in ('douyin.com', 'www.douyin.com') and not p.path.startswith(('/user/', '/follow', '/live')):
        raise ValueError('请使用主播主页或直播间链接，不是已发布的视频链接')
    return url


def walk(payload):
    if isinstance(payload, dict):
        yield payload
        for value in payload.values():
            if isinstance(value, (dict, list)): yield from walk(value)
    elif isinstance(payload, list):
        for value in payload:
            if isinstance(value, (dict, list)): yield from walk(value)


def room_data(payload, quality='origin'):
    for obj in walk(payload):
        stream = obj.get('stream_url') or obj.get('streamUrl')
        if not isinstance(stream, dict): continue
        urls = stream.get('flv_pull_url') or stream.get('hls_pull_url_map') or {}
        if not isinstance(urls, dict): continue
        choices = {str(k).lower(): v for k, v in urls.items() if isinstance(v, str) and v.startswith('https://')}
        if not choices: continue
        preferred = {'origin': ['origin', 'full_hd1', 'hd1', 'sd1'], 'high': ['hd1', 'full_hd1', 'sd1'],
                     'standard': ['sd1', 'ld1', 'hd1']}[quality]
        stream_url = next((choices[k] for k in preferred if k in choices), next(iter(choices.values())))
        owner = obj.get('owner') or obj.get('anchor') or {}
        return {'online': str(obj.get('status', 2)) == '2', 'url': stream_url,
                'name': owner.get('nickname') or obj.get('title') or '抖音直播',
                'room_id': str(obj.get('id_str') or obj.get('id') or obj.get('room_id') or '')}
    return None


def following_data(payload):
    """Only user objects from an observed following-list response are supplied here."""
    found = {}
    for obj in walk(payload):
        sec = obj.get('sec_uid') or obj.get('secUid')
        nick = obj.get('nickname')
        if isinstance(sec, str) and re.fullmatch(r'[A-Za-z0-9_-]{8,200}', sec) and isinstance(nick, str):
            found[sec] = {'url': 'https://www.douyin.com/user/'+sec, 'name': nick[:100]}
    return list(found.values())


def profile_room(payload, sec_uid):
    for user in walk(payload):
        if str(user.get('sec_uid', '')) != sec_uid: continue
        room = user.get('room_data') or {}
        if isinstance(room, str):
            try: room = json.loads(room)
            except ValueError: room = {}
        rid = room.get('web_rid') or user.get('web_rid')
        if rid and re.fullmatch(r'\d{4,30}', str(rid)): return 'https://live.douyin.com/'+str(rid)
    return None


def resolve(browser, url, quality='origin', cancelled=lambda:False):
    url = normalize(url)
    def action():
        page = browser.page_for(url, navigate=True)
        found = []
        destination = []
        expected_user = urlparse(url).path.split('/user/')[-1].rstrip('/') if '/user/' in urlparse(url).path else None
        def response(value):
            if value.status != 200 or not any(p in value.url for p in ('/webcast/room/', '/aweme/v1/web/user/profile/')): return
            try:
                from urllib.parse import parse_qs
                payload = value.json()
                if expected_user and '/user/profile/' in value.url:
                    room = profile_room(payload, expected_user)
                    if room: destination.append(room)
                    elif any(str(user.get('sec_uid','')) == expected_user and user.get('live_status') == 0 for user in walk(payload)):
                        found.append({'online': False, 'message': '主播未开播'})
                    return
                requested_rid = parse_qs(urlparse(value.url).query).get('web_rid', [''])[0]
                actual_page = urlparse(page.url)
                if actual_page.hostname == 'live.douyin.com' and requested_rid and requested_rid != actual_page.path.strip('/'):
                    return
                result = room_data(payload, quality)
                if result: found.append(result)
            except Exception: pass
        page.on('response', response)
        try:
            # Attach the observer before this navigation, including on an existing tab.
            try: page.goto(url, wait_until='domcontentloaded', timeout=25000)
            except Exception: pass
            if urlparse(page.url).hostname in ('www.douyin.com','douyin.com') and '/video/' in urlparse(page.url).path:
                raise ValueError('分享链接指向已发布的视频，请粘贴直播间或主播主页链接')
            deadline = time.monotonic()+12
            while time.monotonic() < deadline:
                if cancelled(): raise ValueError('已停止读取直播间')
                if page.is_closed(): raise ValueError('登录浏览器已关闭，请重新打开')
                if destination and page.url != destination[-1]:
                    try: page.goto(destination[-1], wait_until='domcontentloaded', timeout=20000)
                    except Exception: pass
                if found: break
                scripts = page.locator('script[type="application/json"], script#RENDER_DATA').all_text_contents()
                for script in scripts:
                    try:
                        from urllib.parse import unquote
                        data = room_data(json.loads(unquote(script)), quality)
                        if data: found.append(data); break
                    except (ValueError, TypeError): pass
                if found: break
                text = page.locator('body').inner_text(timeout=3000)
                if any(s in text for s in ('直播已结束', '暂未开播', '还未开播', '暂时没有开播')):
                    return {'online': False, 'message': '主播未开播'}
                page.wait_for_timeout(500)
            if not found: raise ValueError('没有取得直播流。请在登录浏览器确认能播放，完成登录或验证后重试')
            result = found[-1]
            if not result.get('online'): return result
            result.update(referer=page.url, user_agent=page.evaluate('navigator.userAgent'))
            result['cookies'] = browser.ensure().cookies([result['url']])
            return result
        finally:
            page.remove_listener('response', response)
    future = browser.pool.submit(action)
    import concurrent.futures
    deadline = time.monotonic()+300
    while time.monotonic() < deadline:
        if cancelled():
            future.cancel()
            raise ValueError('已停止读取直播间')
        try: return future.result(timeout=.5)
        except concurrent.futures.TimeoutError: pass
    future.cancel()
    raise ValueError('直播间读取队列超时，请减少同时检查的主播并重试')


def import_following(browser):
    def action():
        page = browser.page_for('https://www.douyin.com/follow', navigate=False)
        cached = getattr(browser, 'live_following', {})
        valid_cache = time.monotonic()-cached.get('time', 0) < 300
        users = dict(cached.get('users', {})) if valid_cache else {}
        observed = {'has_more': cached.get('has_more') if valid_cache else None}
        def response(value):
            if value.status != 200 or not any(s in value.url for s in ('/user/following/', '/following/list/')): return
            try:
                payload = value.json()
                for user in following_data(payload): users[user['url']] = user
                observed['has_more'] = payload.get('has_more')
            except Exception: pass
        page.on('response', response)
        try:
            if not users:
                page.goto('https://www.douyin.com/follow', wait_until='domcontentloaded', timeout=30000)
            page.bring_to_front()
            # Click the visible following-list entry, without constructing private API calls.
            button = page.get_by_text('关注的人', exact=True)
            if button.count(): button.first.click(timeout=3000)
            stable = 0
            for _ in range(35):
                before = len(users)
                page.mouse.wheel(0, 1600)
                page.wait_for_timeout(700)
                stable = stable+1 if len(users) == before else 0
                if observed['has_more'] in (0, False) or stable >= 6: break
            return {'users': list(users.values()), 'complete': observed['has_more'] in (0, False),
                    'message': '请在登录浏览器打开关注列表并完成登录，再重试导入；仅导入网页实际加载的关注用户' if not users
                    else '已读取网页加载的关注用户，请选择需要监控的主播；未加载部分可再次导入'}
        finally: page.remove_listener('response', response)
    return browser.pool.submit(action).result(timeout=100)
