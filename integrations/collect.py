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

import asyncio
import html
import http.cookiejar
import json
import re
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlparse
import urllib.parse
import urllib.request

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
  const junk = /违法和不良信息|举报|算法推荐|营业执照|京ICP|网文|友情链接|用户协议|隐私政策|Copyright|©|下载客户端|登录后可|协议汇总|活动专题|帮助中心|社区中心|壁纸站|名人堂|MCN|品牌号|广告合作/;
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
  const preferred = document.querySelector('[data-e2e="user-post-list"],[data-e2e="scroll-list"],[class*="search-page-video"],[class*="video-list"]');
  let anchors = preferred ? [...preferred.querySelectorAll(select)] : [];
  if (!anchors.length) anchors = [...document.querySelectorAll(select)];
  anchors = anchors.filter(a => !a.closest('footer,[data-e2e="page-footer"],[data-e2e="douyin-navigation"],header,[class*="footer"],[class*="Footer"],[class*="bili-header"],[class*="nav-bar"],[class*="navigation"]'));
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
        if (p === document.body || p === document.documentElement) break;
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
         folder_index=None, platform: str = '', debug: str = '') -> dict:
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
                if debug:
                    try:
                        state = page.evaluate(STATE)
                        Path(debug).write_text(json.dumps({
                            'url': state.get('url'), 'title': state.get('title'),
                            'text': (state.get('text') or '')[:5000],
                            'anchors': page.evaluate("() => document.querySelectorAll('a[href*=\"/video/\"]').length"),
                        }, ensure_ascii=False, indent=2), encoding='utf-8')
                        page.screenshot(path=str(Path(debug).with_suffix('.png')))
                    except Exception:
                        pass
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


def bili_api_search(a, keyword: str, limit: int, pages: int, sort: str = '',
                    jid: str = '') -> list[dict]:
    """Read Bilibili's public search API with the account cookies already in the app.

    Search results are a normal public listing; using the API avoids spending
    ninety seconds driving a blank logged-out page.  The cookie files are the
    same ones the downloader uses, and are only read locally.
    """
    candidates = []
    for path in sorted(Path(a.DATA).glob('.cookies-*.txt')):
        try:
            jar = http.cookiejar.MozillaCookieJar(str(path))
            jar.load(ignore_discard=True, ignore_expires=True)
            if any('bilibili.com' in (cookie.domain or '') for cookie in jar):
                candidates.append((path, jar))
        except (OSError, http.cookiejar.CookieError):
            continue
    if not candidates:
        raise ValueError('B 站搜索需要登录：没有找到 B 站登录 Cookie，请先在映格浏览器登录 B 站')
    order = BILIBILI_SORTS.get(sort or 'comprehensive', 'totalrank')
    rows: dict[str, dict] = {}
    last_error = ''
    for path, jar in candidates:
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        got = 0
        try:
            for page in range(1, pages + 1):
                if jid:
                    a.check_cancel(jid)
                query = urllib.parse.urlencode({
                    'search_type': 'video', 'keyword': keyword, 'page': page,
                    'page_size': 50, 'order': order,
                })
                request = urllib.request.Request(
                    'https://api.bilibili.com/x/web-interface/search/type?' + query,
                    headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://search.bilibili.com/'})
                with opener.open(request, timeout=20) as response:
                    payload = json.loads(response.read().decode('utf-8', 'replace'))
                if payload.get('code') != 0:
                    last_error = str(payload.get('message') or payload.get('code'))
                    break
                for item in ((payload.get('data') or {}).get('result') or []):
                    if item.get('type') != 'video':
                        continue
                    bvid = item.get('bvid') or ''
                    url = ('https://www.bilibili.com/video/' + bvid + '/') if bvid else (item.get('arcurl') or '')
                    if not url:
                        continue
                    title = html.unescape(re.sub(r'<[^>]+>', '', item.get('title') or ''))
                    if not title:
                        continue
                    rows.setdefault(bvid or url, {
                        'id': bvid or url, 'kind': 'video', 'url': url,
                        'title': title[:150],
                        'date': time.strftime('%Y-%m-%d', time.localtime(item.get('pubdate') or 0)),
                    })
                    got += 1
                    if len(rows) >= limit:
                        return list(rows.values())
                if len(rows) >= limit or not ((payload.get('data') or {}).get('result') or []):
                    break
        except Exception as exc:
            last_error = str(exc)[:200]
            continue
        if got:
            return list(rows.values())[:limit]
    if rows:
        return list(rows.values())[:limit]
    raise ValueError('B 站搜索没有返回结果' + (f'（{last_error}）' if last_error else ''))


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


MENTION_EXTRACT = r"""() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const rows = [];
  const add = (name, url, text) => {
    name = clean(name).replace(/^@+/, '').trim();
    if (!name || name.length > 40) return;
    if (/^(douyin|bytedance|douyin\.com|bytedance\.com)$/i.test(name)) return;
    const old = rows.find(x => x.name === name);
    if (old) {
      if (!old.url && url) old.url = url;
      return;
    }
    rows.push({name, url: url || '', text: clean(text).slice(0, 120)});
  };
  const detail = document.querySelector('[data-e2e="user-info"]')
    || document.querySelector('[data-e2e="user-detail"]')
    || document.querySelector('header');
  let bio = '';
  if (detail) {
    // Profile header only: the recommendation cards and comments below are
    // not part of the author's public bio and must not be mistaken for it.
    for (const a of detail.querySelectorAll('a[href*="/user/"]')) {
      const text = clean(a.innerText || a.textContent || '');
      if (text.startsWith('@')) add(text, a.href, text);
    }
    for (const el of detail.querySelectorAll('span, div')) {
      const text = clean(el.innerText || '');
      if (!text || !text.includes('@') || text.length > 80) continue;
      for (const match of text.matchAll(/@[\w\u4e00-\u9fa5.-]{2,30}/g)) add(match[0], '', match[0]);
    }
    const candidates = [...detail.querySelectorAll('span, div')]
      .map(el => clean(el.innerText || ''))
      .filter(text => text.startsWith('@') && text.length < 300);
    if (candidates.length) bio = candidates.sort((a, b) => b.length - a.length)[0];
  }
  // A page that failed to expose the profile header should return no guessed
  // names at all; the caller then reports that the bio was unreadable.
  return {rows: rows.slice(0, 60), bio};
}"""


def profile_mentions(browser, url: str, platform: str = 'dy') -> dict:
    """Read the public @ names shown in a creator's profile copy.

    This only reads the page the user can open themselves.  It never follows,
    messages or resolves private accounts; links are returned for the user to
    review and choose from.
    """
    if platform != 'dy':
        raise ValueError('简介 @ 账号读取目前只支持抖音公开主页')
    if not re.match(r'https?://(www\.)?douyin\.com/user/', url or ''):
        raise ValueError('请粘贴抖音作者主页链接，例如 https://www.douyin.com/user/...')

    def action():
        page = browser.page_for(url, navigate=True)
        try:
            _safe_goto(page, url)
            deadline = time.monotonic() + 25
            data = {'rows': [], 'bio': ''}
            while time.monotonic() < deadline:
                state = page.evaluate(STATE)
                blocked = _blocked(state)
                if blocked:
                    raise ValueError(blocked)
                data = page.evaluate(MENTION_EXTRACT)
                if data.get('rows') or data.get('bio') or (state.get('text') or '').strip():
                    break
                page.wait_for_timeout(700)
            page.wait_for_timeout(1500)
            data = page.evaluate(MENTION_EXTRACT)
            return {'items': data.get('rows', []), 'bio': data.get('bio', ''),
                    'page': url, 'elapsed': round(25 - max(0, deadline - time.monotonic()), 1)}
        finally:
            browser.release(page, keep=True)

    return browser.pool.submit(action).result(timeout=60)


class MentionRequest(BaseModel):
    platform: str = 'dy'
    target: str = Field(min_length=1, max_length=800)


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


DOWNLOAD_MODES = ('video', 'package', 'package-combined')


def resolve_download_mode(mode: str, combined: bool = True) -> str:
    """Normalise the picker's download mode.

    An empty mode is the legacy API contract: ``combined`` decides whether the
    selected episodes become one AI package or one package per episode.  New
    callers use ``video`` for a plain download with no AI package at all.
    """
    value = (mode or '').strip()
    if not value:
        return 'package-combined' if combined else 'package'
    if value not in DOWNLOAD_MODES:
        raise ValueError('未知下载方式')
    return value


class Picked(BaseModel):
    url: str = Field(min_length=3, max_length=2048)
    title: str = ''
    kind: str = 'video'


class DownloadRequest(BaseModel):
    items: list[Picked] = Field(min_length=1, max_length=100)
    mode: str = Field(default='', max_length=20)
    combined: bool = True
    output: str = ''


class AutoSearchRequest(BaseModel):
    platform: str = 'dy'
    keyword: str = Field(min_length=1, max_length=160)
    limit: int = Field(default=30, ge=1, le=100)
    pages: int = Field(default=1, ge=1, le=10)
    sort: str = ''
    publish: str = ''
    mode: str = 'package'
    incremental: bool = False
    keep_video: bool = True
    output: str = ''


class OpenSearchRequest(BaseModel):
    platform: str = 'dy'
    keyword: str = Field(min_length=1, max_length=160)
    sort: str = ''
    publish: str = ''
    page: int = Field(default=1, ge=1, le=50)


# These are the daily defaults used by the analysis queue.  Search batches
# otherwise inherit the high-precision sample defaults (one frame per second,
# no cap), which is too heavy for a hundred short videos.
DAILY_ANALYSIS = {
    'strategy': 'compact', 'pick': 'mid', 'max_frames': 120, 'max_sheets': 12,
    'max_cells_per_sheet': 0, 'batch_seconds': 300, 'max_height': 720,
    'transcript': 'auto', 'language': 'auto', 'detail': 'med', 'ocr': False,
    'keep_frames': False, 'audio': False, 'scan_gap': 0, 'integral': True,
    'integral_seconds': 6, 'kind': 'board', 'performance': 'auto',
    'strip_step': 3, 'strip_count': 24,
}


def daily_package() -> dict:
    return {
        'enabled': True, 'combined': False, 'screen_text': True,
        'ocr_threads': 4, 'scan_seconds': 10, 'scene_threshold': .12,
        'max_visuals': 120, 'batch_files': 10,
    }



def queue_direct_videos(a, picked, output: str) -> list[str]:
    """Queue plain download jobs without the batch/AI package pipeline."""
    jobs = []
    for index, item in enumerate(picked):
        title = (item.title.strip() or ('视频 %02d' % (index + 1)))[:120]
        job = a.new_job('download', '直接采集 · ' + title, output, item.url,
                        {'video_saved': True, 'max_height': 0, 'collect_direct': True})
        request = {'login': True, 'cookie_id': '', 'extract': False, 'frames_only': False,
                   'max_height': 0, 'frames': {}, 'bundle': False}
        a.WORKERS.submit(a.guarded, job['id'],
                         lambda jid=job['id'], req=request: a.download_video(jid, req))
        jobs.append(job['id'])
    return jobs


def _auto_label(platform: str) -> str:
    return '抖音' if platform == 'dy' else 'B 站'


def _auto_canonical(item: dict) -> str:
    return (item.get('url') or '').split('?')[0].rstrip('/')


def _bili_cookie_count(a):
    count = 0
    for path in sorted(Path(a.DATA).glob('.cookies-*.txt')):
        try:
            jar = http.cookiejar.MozillaCookieJar(str(path))
            jar.load(ignore_discard=True, ignore_expires=True)
            if any('bilibili.com' in (cookie.domain or '') for cookie in jar):
                count += 1
        except (OSError, http.cookiejar.CookieError):
            continue
    return count


def _search_scope(platform, keyword, sort='', publish=''):
    """Stable identity for a watchlist entry; limit/pages are intentionally ignored."""
    return '|'.join((platform, str(keyword or '').strip().casefold(), sort or '', publish or ''))


def auto_search(a, pipeline, jid: str, req: AutoSearchRequest) -> str:
    """Read search results and create one package per video, without a picker.

    Douyin has one infinite result page; Bilibili can be paged.  The loop is
    deliberately bounded by the user's limit and a small page count so a typo
    in a keyword cannot start an unbounded crawl.  The actual download and
    package work is delegated to the existing batch pipeline.
    """
    from . import batch_pipeline
    platform = req.platform
    if platform not in ('dy', 'bili'):
        raise ValueError('请选择抖音或 B 站')
    if req.mode not in ('package', 'video'):
        raise ValueError('未知处理方式')
    pages = 1 if platform == 'dy' else min(req.pages, 10)
    base = Path(a.job_by_id(jid)['directory'])
    collected: dict[str, dict] = {}
    if platform == 'bili':
        a.change(jid, message=f'读取 B 站搜索结果（最多 {pages} 页）', progress=20)
        items = bili_api_search(a, req.keyword, req.limit, pages, req.sort, jid)
    else:
        for page in range(1, pages + 1):
            a.check_cancel(jid)
            remaining = req.limit - len(collected)
            if remaining <= 0:
                break
            url = listing_url(platform, 'search', req.keyword, sort=req.sort,
                              page=page, publish=req.publish)
            a.change(jid, message=f'读取第 {page} / {pages} 页搜索结果', progress=min(45, 8 + page * 8))
            try:
                result = scan(a.BROWSER, url, remaining, platform=platform,
                              debug=str(base / f'扫描诊断-第{page}页.txt'))
            except ValueError:
                if page == 1:
                    raise
                break
            added = 0
            for item in result['items']:
                if item.get('kind') == 'note' or not item.get('url'):
                    continue
                key = _auto_canonical(item) or item.get('id')
                if key in collected:
                    continue
                collected[key] = item
                added += 1
                if len(collected) >= req.limit:
                    break
            a.atomic_json(base / '搜索结果.json', {
                'keyword': req.keyword, 'platform': platform, 'pages': pages,
                'items': list(collected.values()), 'updated': time.time(),
            })
            # An empty page means the platform has no further results.  A page
            # that only repeats prior cards is also the end of the useful range.
            if not added:
                break
        items = list(collected.values())
    all_items = list(items)
    # A keyword is only useful as a monitor if the second run can tell what
    # changed.  Keep the last snapshot outside the job folder, then compare
    # canonical URLs (query strings and tracking parameters are ignored).
    scope = _search_scope(platform, req.keyword, req.sort, req.publish)
    snapshots = a.load_json(a.DATA / 'keyword-snapshots.json', {})
    previous = snapshots.get(scope) or {}
    previous_urls = set(previous.get('urls') or [])
    def canonical(item):
        return _auto_canonical(item) or str(item.get('id') or '')
    new_items = [item for item in all_items if canonical(item) not in previous_urls]
    snapshots[scope] = {
        'keyword': req.keyword.strip(), 'platform': platform, 'sort': req.sort,
        'publish': req.publish, 'urls': [canonical(item) for item in all_items],
        'items': [{'url': item.get('url'), 'title': item.get('title') or '', 'id': item.get('id')}
                  for item in all_items],
        'updated': time.time(),
    }
    a.atomic_json(a.DATA / 'keyword-snapshots.json', snapshots)
    a.atomic_json(base / '搜索结果.json', {
        'keyword': req.keyword, 'platform': platform, 'pages': pages,
        'items': all_items, 'new_items': new_items,
        'previous_count': len(previous_urls), 'new_count': len(new_items),
        'first_snapshot': not previous, 'incremental': req.incremental, 'updated': time.time(),
    })
    if previous:
        (base / '新增视频.txt').write_text(
            '\n'.join(f"{item.get('title') or '(无标题)'}\t{item.get('url') or ''}"
                      for item in new_items) or '本次没有新增视频。',
            encoding='utf-8')
    if req.incremental and not new_items:
        return f'本次没有新增视频；上次快照已记录 {len(previous_urls)} 条，未重复建包'
    if req.incremental:
        items = new_items
    if not items:
        raise ValueError('没有读到搜索结果；请确认关键词、平台登录状态或人工验证后重试')
    a.change(jid, message=f'已读取 {len(all_items)} 条搜索结果，本次新增 {len(new_items)} 条，准备批量处理', progress=55)
    picked = [Picked(url=item['url'], title=item.get('title') or '', kind=item.get('kind') or 'video')
              for item in items]
    if req.mode == 'video':
        ids = queue_direct_videos(a, picked, req.output)
        return f'已读取 {len(items)} 条搜索结果，已建立 {len(ids)} 个视频下载任务'
    sources = [
        batch_pipeline.OnlineSource(
            input=item.url, episode=index + 1,
            episode_title=(item.title.strip() or f'{req.keyword} · 第{index + 1:03d}条')[:120])
        for index, item in enumerate(picked)
    ]
    body = batch_pipeline.BatchRequest(
        sources=['online'],
        online_sources=sources,
        kind='board',
        workflow='board',
        performance='auto',
        output=req.output,
        outputs=batch_pipeline.Outputs(puzzles=True, transcript=True, ai=True, video=req.keep_video),
        analysis=DAILY_ANALYSIS,
        ai_package=batch_pipeline.AIPackage(**daily_package()),
        request_id='collect-auto-' + uuid.uuid4().hex,
    )
    parent = pipeline.create(body)
    a.change(jid, batch_job=parent['id'])
    return (f'已读取 {len(items)} 条搜索结果，已建立 {len(sources)} 期 AI 资料包任务；'
            f'批量任务 {parent["id"]} 会在素材库继续处理')



def install(app, a, pipeline):
    @app.post('/api/collect/open')
    async def collect_open(req: OpenSearchRequest):
        if req.platform not in ('dy', 'bili'):
            raise HTTPException(400, '请选择抖音或 B 站')
        if not req.keyword.strip():
            raise HTTPException(400, '请输入搜索关键词')
        try:
            url = listing_url(req.platform, 'search', req.keyword.strip(), sort=req.sort,
                              page=req.page, publish=req.publish)
            result = await asyncio.wrap_future(a.BROWSER.open(url))
            return {**result, 'url': url}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get('/api/collect/preflight')
    def collect_preflight(platform: str = 'dy', keyword: str = '', sort: str = '',
                          publish: str = '', incremental: bool = False):
        """Cheap checks before a keyword run: login material and disk headroom."""
        if platform not in ('dy', 'bili'):
            raise HTTPException(400, '请选择抖音或 B 站')
        checks, warnings, ok = [], [], True
        if platform == 'bili':
            cookies = _bili_cookie_count(a)
            ok = cookies > 0
            checks.append({'name': 'B 站登录材料', 'ok': ok, 'detail': f'找到 {cookies} 份 B 站 Cookie'})
            if not ok:
                warnings.append('没有找到 B 站登录 Cookie；请先在映格浏览器登录 B 站，否则搜索会失败。')
        else:
            checks.append({'name': '抖音可见浏览器', 'ok': True, 'detail': '将由可见浏览器读取搜索结果'})
            warnings.append('抖音搜索需要登录态；遇到滑块或验证码时请在可见浏览器人工完成，程序不会绕过验证。')
        try:
            free = shutil.disk_usage(str(a.DATA)).free
            disk_ok = free >= 500 * 1024 * 1024
            checks.append({'name': '磁盘余量', 'ok': disk_ok, 'detail': f'可用 {free / 1024 / 1024:.0f} MB'})
            if not disk_ok:
                ok = False
                warnings.append('磁盘剩余空间不足 500 MB，批量下载可能中途失败。')
        except OSError as exc:
            checks.append({'name': '磁盘余量', 'ok': False, 'detail': str(exc)})
            ok = False
            warnings.append('无法读取磁盘余量，请检查输出盘是否在线。')
        if keyword.strip():
            snapshots = a.load_json(a.DATA / 'keyword-snapshots.json', {})
            previous = snapshots.get(_search_scope(platform, keyword, sort, publish)) or {}
            recorded = len(previous.get('urls') or [])
            checks.append({'name': '增量快照', 'ok': True,
                           'detail': f'上次记录 {recorded} 条' if recorded else '尚无快照，首次运行会建立基线'})
            if incremental and not recorded:
                warnings.append('这是该关键词的第一次运行：会先建立全部结果作为基线，下一次才只处理新增。')
        return {'ok': ok, 'platform': platform, 'keyword': keyword.strip(),
                'checks': checks, 'warnings': warnings,
                'message': '预检未通过' if not ok else ('有提醒，但不影响开始' if warnings else '预检通过')}

    @app.post('/api/collect/auto')
    def collect_auto(req: AutoSearchRequest):
        if req.platform not in ('dy', 'bili'):
            raise HTTPException(400, '请选择抖音或 B 站')
        if not req.keyword.strip():
            raise HTTPException(400, '请输入搜索关键词')
        if req.mode not in ('package', 'video'):
            raise HTTPException(400, '未知处理方式')
        label = _auto_label(req.platform)
        job = a.new_job('collect', f'{label}关键词批量 · {req.keyword.strip()[:40]}',
                        req.output, options=req.model_dump())
        # Browser reads are serialized separately from media/transcription work,
        # so a keyword scan cannot hold the single media worker for a minute.
        executor = getattr(a, 'CRAWLERS', None) or a.WORKERS
        executor.submit(a.guarded, job['id'], lambda: auto_search(a, pipeline, job['id'], req))
        return {'id': job['id'], 'kind': 'collect', 'title': job['title'],
                'count': 0, 'message': '正在后台读取搜索结果'}

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

    @app.post('/api/collect/mentions')
    def collect_mentions(req: MentionRequest):
        if req.platform not in ('dy',):
            raise HTTPException(400, '简介 @ 账号读取目前只支持抖音')
        try:
            return profile_mentions(a.BROWSER, req.target.strip(), req.platform)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

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
                'hint': '%s勾选要下载的几期；可直接下载视频，也可生成 AI 包。' % lead}

    @app.post('/api/collect/download')
    def collect_download(req: DownloadRequest):
        # Same picker, two destinations: "video" saves the original files
        # through the normal download queue; package modes keep the existing
        # batch pipeline and AI package.
        from . import batch_pipeline
        picked = [item for item in req.items
                  if item.url.startswith(('http://', 'https://')) and item.kind != 'note']
        if not picked:
            raise HTTPException(400, '图文笔记暂不支持自动下载，请只勾选视频')
        try:
            mode = resolve_download_mode(req.mode, req.combined)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if mode == 'video':
            try:
                ids = queue_direct_videos(a, picked, req.output)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(400, str(exc)[:200])
            return {'ids': ids, 'title': '直接下载视频', 'count': len(ids),
                    'mode': 'video', 'combined': False}
        combined = mode == 'package-combined'
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
            ai_package=batch_pipeline.AIPackage(enabled=True, combined=combined),
            request_id='collect-' + uuid.uuid4().hex,
        )
        try:
            job = pipeline.create(body)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, str(exc)[:200])
        return {'id': job['id'], 'title': job.get('title'), 'count': len(sources),
                'mode': mode, 'combined': combined}




