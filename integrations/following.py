"""A following-list snapshot consists of observed pages from one account."""
import hashlib
import time
from urllib.parse import parse_qs,urlparse


def is_following_response(url):
    path=urlparse(url).path
    return '/user/following/' in path or '/following/list/' in path or path.endswith('/following/list')


def rows(payload):
    if not isinstance(payload,dict):return []
    for root in (payload,payload.get('data')):
        if not isinstance(root,dict):continue
        for key in ('followings','following_list','user_list'):
            value=root.get(key)
            if isinstance(value,list):return [u.get('user',u) if isinstance(u,dict) else u for u in value]
    return []


def session_fingerprint(cookies):
    """Keep only an irreversible account-session marker, never raw credentials."""
    names = {'sessionid', 'sessionid_ss', 'sid_tt', 'uid_tt', 'uid_tt_ss'}
    values = sorted({(c['name'], c.get('value', '')) for c in cookies if c.get('name') in names})
    return hashlib.sha256(repr(values).encode()).hexdigest() if values else None


def set_session(browser, marker):
    if hasattr(browser, 'live_following_session') and browser.live_following_session != marker:
        browser.live_following = {}
        browser.live_following_profile = {}
    browser.live_following_session = marker


def set_profile(browser, user):
    profile = {'sec_uid': user.get('sec_uid'), 'uid': str(user.get('uid') or ''),
               'total': user.get('following_count')}
    previous = getattr(browser, 'live_following_profile', {})
    identity = lambda p: (p.get('sec_uid') or '', p.get('uid') or '')
    if identity(previous) != identity(profile): browser.live_following = {}
    browser.live_following_profile = profile


def observe(browser,url,payload,parse_users):
    query=parse_qs(urlparse(url).query)
    owner=next((query[k][0] for k in ('sec_user_id','user_id','uid') if query.get(k)),None)
    previous=getattr(browser,'live_following',{})
    profile=getattr(browser,'live_following_profile',{})
    profile_key=(profile.get('sec_uid') or '',profile.get('uid') or '')
    profile_ids={str(v) for v in profile_key if v}
    session=getattr(browser,'live_following_session',None)
    # An owner-less response may still be bound to a verified self profile/session.
    # If all identity evidence disappears, do not merge another account's pages.
    effective_owner=owner or profile.get('sec_uid') or profile.get('uid')
    values=[query[k][0] for k in ('cursor','max_time','offset','min_time') if query.get(k)]
    cursor=next((v for v in values if v not in ('0','0.0')),values[0] if values else None)
    # An absent cursor is not evidence of the first page.
    first=bool(values) and all(v in ('0','0.0') for v in values)
    stale=time.monotonic()-previous.get('time',0)>900
    changed_profile=previous.get('profile_key',('', ''))!=profile_key
    changed_owner=bool(effective_owner and previous.get('owner') and effective_owner!=previous['owner']
                       and not {str(effective_owner),str(previous['owner'])}<=profile_ids)
    unconfirmed=not (effective_owner or session)
    reset=(stale or changed_profile or changed_owner or first or unconfirmed
           or previous.get('session')!=session)
    state={} if reset else dict(previous)
    users=dict(state.get('users',{}))
    for user in parse_users(payload):users[user['url']]=user
    root=payload.get('data') if isinstance(payload.get('data'),dict) else payload
    more=root.get('has_more',payload.get('has_more'))
    if more not in (0,1,False,True):more=None
    expected=next((root[k] for k in ('total','total_count','following_count') if isinstance(root.get(k),int) and not isinstance(root[k],bool) and root[k]>=0),None)
    if expected is None and effective_owner in profile_ids:
        total=profile.get('total')
        if isinstance(total,int) and not isinstance(total,bool) and total>=0:expected=total
    pages=list(state.get('cursors',[]))
    if cursor not in pages:pages.append(cursor)
    state.update(users=users,owner=effective_owner,profile_key=profile_key,session=session,
                 identity_confirmed=not unconfirmed,first_seen=first or state.get('first_seen',False),
                 has_more=more,expected=expected if expected is not None else state.get('expected'),
                 cursors=pages,time=time.monotonic())
    browser.live_following=state
    return state


def summary(state):
    users=list(state.get('users',{}).values());expected=state.get('expected')
    complete=bool(state.get('identity_confirmed',True) and state.get('first_seen') and state.get('has_more') in (0,False) and state.get('has_more') is not None
                  and type(expected) is int and expected==len(users))
    missing=max(0,expected-len(users)) if expected is not None else None
    count=f'已读取 {len(users)} 位关注'
    if expected is not None:count+=f'，账号显示共 {expected} 位，未读取差额 {missing} 位'
    if complete:message=count+'；已从首页加载到末页'
    elif not users:message='尚未读取到关注用户。请在专用浏览器登录，打开自己的“关注”人员列表后重试'
    else:message=count+'；尚未确认完整，请继续加载。平台总数可能包含隐藏、失效账号，不保证可全部读取'
    if expected is not None and len(users)>expected:message=count+'；数量不一致，尚未确认完整，请刷新当前账号的关注列表'
    if state.get('identity_confirmed') is False:message=count+'；账号身份尚未确认，未合并旧列表，请登录并重新从首页读取'
    return {'users':users,'loaded_count':len(users),'expected_count':expected,'missing_count':missing,
            'unread_count':missing,'complete':complete,'message':message}
