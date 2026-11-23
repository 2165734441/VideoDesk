"""List a creator's videos before downloading any of them.

The download engines already accept links; what was missing was a way to see
what a profile, a collection or a search actually holds, and pick from it.
This module only browses the page the user would open themselves — the same
logged-in profile, in the same visible window — and reads the links the page
renders. No signed requests, no private endpoints, no bypass.

Collections on both sites have their own folder list. Douyin opens a folder
inside the same tab (the address bar does not change), so the reader clicks the
folder card exactly like a person would. Bilibili reaches a folder through
``?fid=`` but still needs the list of folders to choose from.
"""
from __future__ import annotations

import re
import time
import uuid
from urllib.parse import quote, urlparse

from fastapi import HTTPException
from pydantic import BaseModel, Field

SOURCE_LABELS = {
    'creator': '博主主页',
    'liked': '我的喜欢（点赞）',
    'fav': '我的收藏夹',
    'search': '关键词搜索',
}

# Reading a list means scrolling a lazy-loaded page. The browser module has a
# single worker shared with live checks, so this is deliberately bounded: the
# user gets a picker within a minute instead of holding the queue forever.
SCAN_SECONDS = 90
# Time allowed for the first card to appear before scrolling starts. Kept
# separate from SCAN_SECONDS so tests do not have to wait it out.
WARM_SECONDS = 35

# Douyin's collection tab is the only place the folders live. The tab URL is
# stable; a folder itself has no address of its own, which is why the reader
# has to click the card below.
DOUYIN_FOLDER_URL = ('https://www.douyin.com/user/self?showTab=favorite_collection'
                     '&showSubTab=favorite_folder')
DOUYIN_COLLECT_URL = 'https://www.douyin.com/user/self?showTab=favorite_collection'

# Sort names are normalised on the API so the UI can offer the same wording on
# both sites while each site gets its own query value.
DOUYIN_SORTS = {'comprehensive': '0', 'likes': '1', 'latest': '2'}
BILIBILI_SORTS = {
    'comprehensive': 'totalrank', 'latest': 'pubdate', 'plays': 'click',
    'favorites': 'stow', 'danmaku': 'dm',
}
DOUYIN_PUBLISH = {'all': '0', 'day': '1', 'week': '7', 'half_year': '180', 'year': '365'}

# Video cards differ between the two sites, but both expose the canonical
# /video/<id> link. A card usually links twice (cover and title), and its text
# starts with view/like counters, so the title is picked from the most
# title-like candidate instead of the first line of the card.
EXTRACT = """() => {
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
  const junk = /违法和不良信息|举报|算法推荐|营业执照|京ICP|网文|友情链接|用户协议|隐私政策|Copyright|©|下载客户端|登录后可/;
  // Douyin puts the title and the whole description in one text node, separated
  // only by a space, so a card can look like "5.6万 标题…了！ 本视频由…". The
  // visible card is clipped; take the first sentence (or first 40 chars).
  const stripCounter = s => (s || '').replace(/^[\\d.,]+\\s*(万|亿)?\\s*/, '');
  const shorten = s => {
    s = (s || '').trim();
    if (s.length <= 60) return s;
    const head = s.slice(0, 60);
    const cut = Math.max(head.lastIndexOf('！'), head.lastIndexOf('？'), head.lastIndexOf('。'),
                         head.lastIndexOf('!'), head.lastIndexOf('?'));
    if (cut >= 8) return s.slice(0, cut + 1);
    const marker = s.search(/ (本视频由|视频由|简介|作者|#)/);
    if (marker >= 6) return s.slice(0, marker).trim();
    return s.slice(0, 40).trim() + '…';
  };
  const cardSelector = '[data-e2e="scroll-list-item"],[data-e2e="feed-video"],[class*="bili-video-card"],[class*="video-card"],[class*="video-item"],[class*="feed-card"],[class*="search-page-video"]';
  const groups = new Map();
  // Prefer the page's own list container. On Douyin the footer also carries
  // /video/ links (recommendations), and reading those would hand the user
  // somebody else's videos.
  const select = 'a[href*="/video/"],a[href*="/note/"]';
  const preferred = document.querySelector('[data-e2e="user-post-list"],[data-e2e="scroll-list"]');
  let anchors = preferred ? [...preferred.querySelectorAll(select)] : [];
  if (!anchors.length) anchors = [...document.querySelectorAll(select)];
  anchors = anchors.filter(a => !a.closest('footer,[data-e2e="page-footer"],[data-e2e="douyin-navigation"]'));
  for (const a of anchors) {
    const m = (a.href || '').match(/\\/(video|note)\\/([A-Za-z0-9_-]{6,})/);
    if (!m) continue;
    const kind = m[1] === 'note' ? 'note' : 'video';
    const id = m[2];
    let g = groups.get(id);
    if (!g) { g = {id, kind, url: (a.href || '').split('?')[0], titles: [], date: ''}; groups.set(id, g); }
    const own = clean(a.getAttribute('title') || '');
    const direct = clean(a.innerText || '');
    if (own && !junk.test(own)) g.titles.push({text: own, weight: 3});
    if (direct && direct.length <= 160 && !junk.test(direct)) g.titles.push({text: direct, weight: 2});
    // The card is only trusted while it is small: an over-broad ancestor would
    // hand back the page footer, which used to become every row's title.
    let card = a.closest(cardSelector);
    if (!card || (card.innerText || '').length > 600) {
      card = null;
      let p = a.parentElement;
      for (let i = 0; i < 5 && p; i++, p = p.parentElement) {
        const size = (p.innerText || '').length;
        if (size > 0 && size < 600) { card = p; break; }
      }
    }
    if (card) {
      const el = card.querySelector('[data-e2e="video-desc"],[class*="tit"],[class*="video-name"],h3,[title]');
      const found = clean(el ? (el.getAttribute('title') || el.innerText) : '');
      if (found.length >= 2 && !junk.test(found)) g.titles.push({text: found, weight: 5});
      const text = card.innerText || '';
      const lines = text.split('\\n').map(clean).filter(t => t && !junk.test(t));
      // Counters, durations, dates and badges are not titles. Picking the
      // longest remaining line also survives renamed class names.
      if (lines.length && lines.length <= 24) {
        const good = lines.filter(t => t.length >= 4 && t.length <= 120 &&
          !/^[\\d.万亿\\s]+$/.test(t) && !/^\\d{1,2}:\\d{2}$/.test(t) && !/^[\\d-]{3,}$/.test(t) &&
          !/^(合作|广告|充电专属|置顶|热门)$/.test(t));
        if (good.length) g.titles.push({text: good.sort((x, y) => y.length - x.length)[0], weight: 4});
      }
      const long = lines.find(t => t.length > 120);
      if (long) {
        let candidate = stripCounter(long);
        candidate = candidate.replace(/^[^：:\\n]{1,24}[：:]\\s*/, '');
        const t = shorten(candidate);
        if (t.length >= 4 && !junk.test(t)) g.titles.push({text: t, weight: 4});
      }
      // The cover's alt text carries "uploader：title…" even when the card's
      // visible text is clipped, and it is often the cleanest title on Douyin.
      const img = (card || a).querySelector('img[alt]');
      if (img) {
        let alt = (img.getAttribute('alt') || '').replace(/\\s+/g, ' ').trim();
        alt = alt.replace(/^[^：:]{1,24}[：:]\\s*/, '');
        const t = shorten(alt);
        if (t.length >= 4 && t.length <= 160 && !/^(视频封面|封面|头像)$/.test(t) && !junk.test(t)) {
          g.titles.push({text: t, weight: 6});
        }
      }
      const date = text.match(/(\\d{4}年\\d{1,2}月\\d{1,2}日|\\d{1,2}月\\d{1,2}日|\\d+天前|\\d+小时前|昨天|今天)/);
      if (date && !g.date) g.date = date[1];
    }
  }
  const out = [];
  for (const g of groups.values()) {
    g.titles.sort((a, b) => b.weight - a.weight || b.text.length - a.text.length);
    let title = (g.titles[0] || {}).text || '';
    if (!title || junk.test(title) || /^[\\d.万亿\\s-]+$/.test(title) || /^\\d{1,2}:\\d{2}/.test(title)) title = '';
    out.push({id: g.id, kind: g.kind, url: g.url, title: title.slice(0, 150), date: g.date});
  }
  return out;
}"""

STATE = """() => ({
  url: location.href,
  title: document.title,
  text: (document.body ? document.body.innerText : '').slice(0, 3000)
})"""

FOLDER_CARDS = """() => {
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
  const list = document.querySelector('[data-e2e="scroll-list"]');
  if (!list) return [];
  return [...list.querySelectorAll('li')]
    .filter(li => !clean(li.innerText).startsWith('新建收藏夹'))
    .map((li, index) => {
      const text = clean(li.innerText);
      const count = text.match(/共\\s*(\\d+)\\s*作品/);
      const r = li.getBoundingClientRect();
      return {
        index,
        name: text.replace(/\\s*共\\s*\\d+\\s*作品.*$/, '').trim(),
        count: count ? Number(count[1]) : null,
        x: r.x, y: r.y, w: r.width, h: r.height
      };
    })
    .filter(card => card.name && card.w > 60 && card.h > 40);
}"""


def listing_url(platform: str, source: str, target: str, sort: str = '', page: int = 1,
                publish: str = '', folder: str = '') -> str:
    """Turn the user's input into the page that actually lists the videos."""
    target = (target or '').strip()
    sort = (sort or '').strip()
    publish = (publish or '').strip()
    if source == 'search':
        if not target:
            raise ValueError('请输入搜索关键词')
        if platform == 'dy':
            if page > 1:
                raise ValueError('抖音搜索没有页码，想读更多请调大“最多读取”')
            if sort and sort not in DOUYIN_SORTS:
                raise ValueError('抖音搜索不支持这种排序，请选综合、最新或最多点赞')
            if publish and publish not in DOUYIN_PUBLISH:
                raise ValueError('抖音搜索不支持这个发布时间')
            url = 'https://www.douyin.com/search/' + quote(target) + '?type=video'
            if sort:
                url += '&sort_type=' + DOUYIN_SORTS[sort]
            if publish:
                url += '&publish_time=' + DOUYIN_PUBLISH[publish]
            return url
        if sort and sort not in BILIBILI_SORTS:
            raise ValueError('B 站搜索不支持这种排序')
        url = 'https://search.bilibili.com/all?keyword=' + quote(target)
        if sort:
            url += '&order=' + BILIBILI_SORTS[sort]
        if page > 1:
            url += '&page=' + str(page)
        return url
    if platform == 'dy':
        if source == 'liked':
            return 'https://www.douyin.com/user/self?showTab=like'
        if source == 'fav':
            # Both "all collected videos" and one folder start from the folder
            # tab; a chosen folder is opened by clicking its card, because
            # Douyin keeps the same address for every folder.
            return DOUYIN_FOLDER_URL if folder else DOUYIN_COLLECT_URL
        if not target:
            raise ValueError('请粘贴抖音博主主页链接，或博主主页里的 sec_uid')
        if target.startswith(('http://', 'https://')):
            host = urlparse(target).hostname or ''
            if not host.endswith('douyin.com'):
                raise ValueError('这不是抖音主页链接')
            return target
        return 'https://www.douyin.com/user/' + target
    # Bilibili
    if source in ('liked', 'fav'):
        mid = ''
        if target.startswith(('http://', 'https://')):
            # A favourite page can point at one folder (?fid=123). Keeping that
            # address is the difference between "all folders" and the videos
            # the user actually picked.
            if source == 'fav' and 'fid=' in target and 'bilibili.com' in (urlparse(target).hostname or ''):
                if not folder:
                    return target
            found = re.search(r'space\.bilibili\.com/(\d+)', target)
            if found:
                mid = found.group(1)
            if source == 'fav' and not mid:
                found = re.search(r'/favlist', target)
                if not found:
                    raise ValueError('请粘贴 B 站个人空间链接，或空间链接里的 UID')
        else:
            mid = target or ''
        if not mid.isdigit():
            raise ValueError('请填写 B 站 UID（个人空间链接里的数字）')
        if source == 'liked':
            return 'https://space.bilibili.com/%s/like' % mid
        url = 'https://space.bilibili.com/%s/favlist' % mid
        if folder:
            url += '?fid=%s&ftype=create' % quote(str(folder))
        return url
    if not target:
        raise ValueError('请粘贴 B 站 UP 主空间链接，或 UID')
    if target.startswith(('http://', 'https://')):
        found = re.search(r'space\.bilibili\.com/(\d+)', target)
        if not found:
            raise ValueError('这不是 B 站 UP 主空间链接')
        return 'https://space.bilibili.com/%s/upload/video' % found.group(1)
    if target.isdigit():
        return 'https://space.bilibili.com/%s/upload/video' % target
    raise ValueError('请粘贴 B 站 UP 主空间链接，或 UID')


def folders_url(platform: str, source: str, target: str) -> str:
    """The page whose own data lists the user's collection folders."""
    if source != 'fav':
        raise ValueError('只有“我的收藏夹”需要选择文件夹')
    if platform == 'dy':
        return DOUYIN_FOLDER_URL
    return listing_url('bili', 'fav', target)


def parse_douyin_folders(payload: dict) -> list[dict]:
    """Read folder id / name / count from Douyin's own ``collects/list`` reply."""
    data = payload or {}
    items = data.get('collects_list')
    if items is None:
        items = (data.get('data') or {}).get('collects_list')
    rows = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        fid = str(item.get('collects_id_str') or item.get('collects_id') or '').strip()
        name = str(item.get('collects_name') or '').strip()
        count = item.get('total_number')
        if not fid and not name:
            continue
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = None
        rows.append({'id': fid, 'name': name, 'count': count})
    return rows


def parse_bilibili_folders(payload: dict) -> list[dict]:
    """Read folder id / title / count from Bilibili's own folder-list reply."""
    data = (payload or {}).get('data') or {}
    items = data.get('list') or data.get('media_list') or []
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        fid = str(item.get('id') or item.get('fid') or item.get('media_id') or '').strip()
        name = str(item.get('title') or item.get('name') or '').strip()
        count = item.get('media_count')
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = None
        if fid or name:
            rows.append({'id': fid, 'name': name, 'count': count})
    return rows


def parse_folder_dom(cards: list[dict]) -> list[dict]:
    """Normalise the DOM fallback cards into the same folder shape."""
    rows = []
    for card in cards or []:
        if not isinstance(card, dict):
            continue
        name = str(card.get('name') or '').strip()
        if not name:
            continue
        rows.append({'id': str(card.get('id') or ''), 'name': name, 'count': card.get('count')})
    return rows


def pick_folder_card(cards: list[dict], name: str = '', index=None) -> dict | None:
    """Choose the card the user meant: exact name first, then a prefix match."""
    name = (name or '').strip()
    if name:
        exact = [card for card in cards if str(card.get('name') or '') == name]
        if exact:
            return exact[0]
        partial = [card for card in cards
                   if name in str(card.get('name') or '') or
                   (card.get('name') and str(card['name']) in name)]
        if partial:
            return partial[0]
    if index is not None:
        try:
            index = int(index)
        except (TypeError, ValueError):
            index = -1
        if 0 <= index < len(cards):
            return cards[index]
    return None


def _blocked(state: dict) -> str:
    """Return a human reason when the page is not showing a list at all."""
    url = state.get('url') or ''
    text = state.get('text') or ''
    title = state.get('title') or ''
    if 'passport.bilibili.com' in url or '/login' in url or 'douyin.com/login' in url:
        return '页面跳到了登录，请先在映格登录浏览器里登录该平台，再读取清单'
    # A slider/captcha interstitial reports an empty body and a title like
    # "验证码中间页". Without this the user would only see "没有读到视频".
    if '验证码' in title or '中间页' in title:
        return '平台弹出了验证码（抖音搜索里很常见，不是程序故障），请在映格登录浏览器里手动完成验证后，再读取一次'
    for marker in ('请完成验证', '安全验证', '访问过于频繁', '请完成下方验证'):
        if marker in text:
            return '平台要求安全验证，请在映格登录浏览器里手动完成，再读取清单'
    if '封禁' in text:
        return '这个 B 站账号显示“封禁中”，无法读取收藏夹；请换一个正常登录的账号，或直接粘贴带 fid= 的收藏夹链接'
    return ''


def _safe_goto(page, url: str) -> None:
    """Force a fresh navigation; recycled tabs may still show the old pane."""
    goto = getattr(page, 'goto', None)
    if not goto:
        return
    try:
        goto(url, wait_until='domcontentloaded', timeout=30000)
    except Exception:
        pass


def _click_douyin_folder(page, folder_name: str, index) -> None:
    """Click the folder card by name (or by the order it appeared in)."""
    deadline = time.monotonic() + 18
    cards: list[dict] = []
    target = None
    scrolls = 0
    while time.monotonic() < deadline:
        try:
            cards = page.evaluate(FOLDER_CARDS) or []
        except Exception:
            cards = []
        target = pick_folder_card(cards, folder_name, index)
        if target and target.get('w', 0) > 0 and target.get('h', 0) > 0:
            break
        if cards and not target and scrolls < 3:
            # A long folder list only renders its visible rows; scroll it the
            # way a person would instead of giving up on the 7th folder.
            scrolls += 1
            try:
                page.mouse.wheel(0, 600)
            except Exception:
                pass
            page.wait_for_timeout(900)
            continue
        page.wait_for_timeout(500)
    if not target:
        raise ValueError('没有找到这个收藏夹，可能已删除或改名，请重新读取收藏夹列表')
    try:
        page.bring_to_front()
    except Exception:
        pass
    page.mouse.click(target['x'] + target['w'] / 2, target['y'] + min(target['h'], 80) / 2)
    # The folder opens inside the same tab; wait until its video cards replace
    # the folder cards before letting the normal extractor run.
    end = time.monotonic() + 15
    while time.monotonic() < end:
        try:
            if page.evaluate(EXTRACT):
                return
        except Exception:
            pass
        page.wait_for_timeout(500)
    raise ValueError('这个收藏夹里没有视频，请换一个收藏夹，或先在抖音网页里确认它还能打开')


def scan(browser, url: str, limit: int = 30, folder: str = '', folder_name: str = '',
         folder_index=None, platform: str = '') -> dict:
    """Scroll the listing page and collect the video links it reveals."""
    def action():
        page = browser.page_for(url, navigate=True)
        try:
            # A recycled tab can already be on this URL while showing another
            # pane (a Douyin folder stays on the same address). Reload it so the
            # reader always starts from the page the caller asked for.
            _safe_goto(page, url)
            start = time.monotonic()
            if folder and platform == 'dy':
                _click_douyin_folder(page, folder_name, folder_index)
                start = time.monotonic()
            deadline = start + SCAN_SECONDS
            rows: dict[str, dict] = {}
            # Both sites render their list client-side, so the first seconds are
            # a blank shell. Wait for a card before treating "no growth" as
            # the end of the list, otherwise a fast page reports zero videos.
            warm = min(deadline, start + WARM_SECONDS)
            reloaded = False
            while time.monotonic() < warm and not rows:
                state = page.evaluate(STATE)
                blocked = _blocked(state)
                if blocked:
                    raise ValueError(blocked)
                for row in page.evaluate(EXTRACT):
                    rows.setdefault(row['id'], row)
                if rows:
                    break
                # Bilibili occasionally paints "the owner has not posted yet"
                # before its own data call returns. One reload fixes that; for
                # a creator who really has no videos the second pass stays
                # empty and the caller gets the honest message below.
                text = state.get('text') or ''
                if not reloaded and time.monotonic() - start > 12 and any(
                        marker in text for marker in ('这里什么也没有', '还没投过视频', '暂无视频')):
                    reloaded = True
                    try:
                        page.reload(wait_until='domcontentloaded', timeout=20000)
                    except Exception:
                        pass
                    page.wait_for_timeout(1500)
                    continue
                page.wait_for_timeout(700)
            if not rows:
                if folder:
                    raise ValueError('这个收藏夹里没有视频，请换一个收藏夹')
                raise ValueError('页面没有加载出视频列表：请确认主页有公开视频、已登录该平台，或换一条链接后重试')
            quiet = 0
            while time.monotonic() < deadline:
                blocked = _blocked(page.evaluate(STATE))
                if blocked:
                    raise ValueError(blocked)
                for row in page.evaluate(EXTRACT):
                    rows.setdefault(row['id'], row)
                if len(rows) >= limit:
                    break
                before = len(rows)
                page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                page.wait_for_timeout(900)
                if len(rows) == before:
                    quiet += 1
                    if quiet >= 6:
                        break
                else:
                    quiet = 0
            return {'items': list(rows.values())[:limit], 'page': url,
                    'elapsed': round(time.monotonic() - start, 1)}
        finally:
            # Park the tab on about:blank: the renderer stays warm for the next
            # read, and no platform page (or its video buffers) stays alive.
            browser.release(page, keep=True)
    return browser.pool.submit(action).result(timeout=SCAN_SECONDS + 45)


def _read_douyin_folders(browser) -> dict:
    def action():
        page = browser.page_for(DOUYIN_FOLDER_URL, navigate=False)
        payloads: list[dict] = []

        def on_response(response):
            if '/aweme/v1/web/collects/list/' in response.url:
                try:
                    payloads.append(response.json())
                except Exception:
                    pass

        page.on('response', on_response)
        try:
            _safe_goto(page, DOUYIN_FOLDER_URL)
            start = time.monotonic()
            rows: dict[str, dict] = {}
            rounds = 0
            while time.monotonic() - start < 25:
                state = page.evaluate(STATE)
                blocked = _blocked(state)
                if blocked:
                    raise ValueError(blocked)
                for payload in list(payloads):
                    for row in parse_douyin_folders(payload):
                        rows.setdefault(row['id'] or row['name'], row)
                if rows:
                    has_more = any((payload or {}).get('has_more') for payload in list(payloads))
                    if has_more and rounds < 3:
                        rounds += 1
                        page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                        try:
                            page.mouse.wheel(0, 500)
                        except Exception:
                            pass
                        page.wait_for_timeout(1400)
                        continue
                    break
                page.wait_for_timeout(500)
            if not rows:
                # The DOM still shows the folder cards even if the reply was
                # slower than the first paint; use it rather than reporting 0.
                try:
                    cards = page.evaluate(FOLDER_CARDS) or []
                except Exception:
                    cards = []
                for row in parse_folder_dom(cards):
                    rows.setdefault(row['id'] or row['name'], row)
            return {'folders': list(rows.values()), 'page': DOUYIN_FOLDER_URL,
                    'elapsed': round(time.monotonic() - start, 1)}
        finally:
            try:
                page.remove_listener('response', on_response)
            except Exception:
                pass
            browser.release(page, keep=True)
    return browser.pool.submit(action).result(timeout=70)


def _read_bilibili_folders(browser, url: str) -> dict:
    def action():
        page = browser.page_for(url, navigate=False)
        payloads: list[dict] = []

        def on_response(response):
            if '/x/v3/fav/folder/created/list' in response.url:
                try:
                    payloads.append(response.json())
                except Exception:
                    pass

        page.on('response', on_response)
        try:
            _safe_goto(page, url)
            page.wait_for_timeout(2500)
            state = page.evaluate(STATE)
            blocked = _blocked(state)
            if blocked:
                raise ValueError(blocked)
            # A logged-out or banned space page drops /favlist entirely. Waiting
            # the full 18 s would only end in the same empty answer.
            if 'favlist' not in (state.get('url') or ''):
                raise ValueError('B 站收藏夹页没有打开：需要先在映格浏览器登录 B 站（账号正常、未封禁），或直接粘贴带 fid= 的收藏夹链接')
            start = time.monotonic()
            rows: dict[str, dict] = {}
            while time.monotonic() - start < 18:
                state = page.evaluate(STATE)
                blocked = _blocked(state)
                if blocked:
                    raise ValueError(blocked)
                for payload in list(payloads):
                    for row in parse_bilibili_folders(payload):
                        rows.setdefault(row['id'] or row['name'], row)
                if rows:
                    break
                page.wait_for_timeout(500)
            if not rows:
                # Fallback: the sidebar links carry the same fid the page uses.
                try:
                    cards = page.evaluate("""() => [...document.querySelectorAll('a[href*="fid="]')]
                        .map(a => {
                          const m = (a.href || '').match(/[?&]fid=(\\d+)/);
                          const text = (a.innerText || '').replace(/\\s+/g, ' ').trim();
                          return m ? {id: m[1], name: text.slice(0, 80), count: null} : null;
                        }).filter(Boolean)""") or []
                except Exception:
                    cards = []
                for row in parse_folder_dom(cards):
                    rows.setdefault(row['id'] or row['name'], row)
            return {'folders': list(rows.values()), 'page': url,
                    'elapsed': round(time.monotonic() - start, 1)}
        finally:
            try:
                page.remove_listener('response', on_response)
            except Exception:
                pass
            browser.release(page, keep=True)
    return browser.pool.submit(action).result(timeout=60)


def read_folders(browser, platform: str, source: str, target: str) -> dict:
    """List the folders the user can pick from, using the page's own data."""
    url = folders_url(platform, source, target)
    if platform == 'dy':
        return _read_douyin_folders(browser)
    return _read_bilibili_folders(browser, url)


class ListRequest(BaseModel):
    platform: str = 'dy'
    source: str = 'creator'
    target: str = ''
    limit: int = Field(default=30, ge=1, le=100)
    folder: str = ''
    folder_name: str = ''
    folder_index: int | None = None
    sort: str = ''
    page: int = Field(default=1, ge=1, le=50)
    publish: str = ''


class FoldersRequest(BaseModel):
    platform: str = 'dy'
    source: str = 'fav'
    target: str = ''


class Picked(BaseModel):
    url: str = Field(min_length=3, max_length=2048)
    title: str = ''
    kind: str = 'video'


class DownloadRequest(BaseModel):
    items: list[Picked] = Field(min_length=1, max_length=100)
    combined: bool = True
    output: str = ''


def install(app, a, pipeline):
    @app.post('/api/collect/folders')
    def collect_folders(req: FoldersRequest):
        if req.platform not in ('dy', 'bili'):
            raise HTTPException(400, '请选择抖音或 B 站')
        if req.source != 'fav':
            raise HTTPException(400, '只有“我的收藏夹”需要选择文件夹')
        try:
            result = read_folders(a.BROWSER, req.platform, req.source, req.target)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except Exception as exc:
            raise HTTPException(503, '读取收藏夹列表失败：' + str(exc)[:160])
        folders = result['folders']
        if folders:
            hint = ('读到 %d 个收藏夹。选一个再读取；也可以选“全部视频”读取全部收藏。' % len(folders))
        elif req.platform == 'dy':
            hint = '没有读到收藏夹。可以直接点“读取清单”走“全部视频”，读取全部收藏。'
        else:
            hint = ('没有读到收藏夹。B 站收藏夹需要先在映格浏览器登录（账号正常、未封禁）；'
                    '也可以直接把带 fid= 的收藏夹链接粘到“目标”里读取，默认收藏夹也一样。')
        return {'folders': folders, 'count': len(folders), 'page': result['page'],
                'elapsed': result['elapsed'], 'hint': hint}

    @app.post('/api/collect/list')
    def collect_list(req: ListRequest):
        if req.platform not in ('dy', 'bili'):
            raise HTTPException(400, '请选择抖音或 B 站')
        if req.source not in SOURCE_LABELS:
            raise HTTPException(400, '请选择要读取的来源')
        if req.folder and req.source != 'fav':
            raise HTTPException(400, '只有“我的收藏夹”可以指定文件夹')
        try:
            url = listing_url(req.platform, req.source, req.target, sort=req.sort,
                              page=req.page, publish=req.publish, folder=req.folder)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        try:
            result = scan(a.BROWSER, url, req.limit, folder=req.folder,
                          folder_name=req.folder_name, folder_index=req.folder_index,
                          platform=req.platform)
        except ValueError as exc:
            hint = ''
            if req.source == 'fav' and req.platform == 'bili' and 'fid=' not in (req.target or ''):
                hint = '；B 站收藏请先登录，或打开具体收藏夹后复制地址栏里带 fid= 的链接'
            raise HTTPException(400, str(exc) + hint)
        except Exception as exc:
            raise HTTPException(503, '读取清单失败：' + str(exc)[:160])
        items = result['items']
        if not items:
            raise HTTPException(400, '这个页面没有读到公开视频。请确认主页有视频、已登录，或换一条链接重试。')
        if req.source == 'search' and req.platform == 'bili':
            lead = '已读取第 %d 页 %d 条。' % (req.page, len(items))
        elif req.folder and req.folder_name:
            lead = '收藏夹「%s」已读取 %d 条。' % (req.folder_name, len(items))
        else:
            lead = '已按页面顺序读取 %d 条。' % len(items)
        return {'items': items, 'count': len(items), 'page': result['page'],
                'elapsed': result['elapsed'],
                'hint': '%s勾选要下载的几期，再生成 AI 包。' % lead}

    @app.post('/api/collect/download')
    def collect_download(req: DownloadRequest):
        # Same batch pipeline as the manual episode list: each picked video is
        # one episode, and "combined" writes the single upload-ready AI pack.
        from . import batch_pipeline
        picked = [item for item in req.items
                  if item.url.startswith(('http://', 'https://')) and item.kind != 'note']
        if not picked:
            raise HTTPException(400, '图文笔记暂不支持自动下载，请只勾选视频')
        sources = [
            batch_pipeline.OnlineSource(
                input=item.url,
                episode=index + 1,
                episode_title=(item.title.strip() or ('第%03d集' % (index + 1)))[:120])
            for index, item in enumerate(picked)
        ]
        body = batch_pipeline.BatchRequest(
            sources=['online'],
            online_sources=sources,
            kind='board',
            workflow='board',
            performance='auto',
            output=req.output,
            outputs=batch_pipeline.Outputs(puzzles=True, transcript=True, ai=True),
            ai_package=batch_pipeline.AIPackage(enabled=True, combined=req.combined),
            request_id='collect-' + uuid.uuid4().hex,
        )
        try:
            job = pipeline.create(body)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, str(exc)[:200])
        return {'id': job['id'], 'title': job.get('title'), 'count': len(sources),
                'combined': req.combined}
