"""Pause ordinary platform requests for explicit manual verification."""
import asyncio
import json
from pathlib import Path


def needs_verification(exc):
    nested = getattr(exc, 'last_attempt', None)
    if nested:
        try:
            exc = nested.exception() or exc
        except Exception:
            pass
    text = str(exc).lower()
    return any(marker in text for marker in (
        'captcha', '验证码', '人机', '安全验证', '风控', 'argussecurity',
        '访问频繁', '请求过于频繁', 'risk control', '403', '412', '-352', 'account blocked'))


def attach_manual_gate(client, context, page, control_path):
    control = Path(control_path)
    resume = control.with_suffix('.resume')
    serial = asyncio.Lock()

    async def pause():
        temp = control.with_suffix('.tmp')
        temp.write_text(json.dumps({'waiting': True}), encoding='utf-8')
        temp.replace(control)
        try:
            await page.bring_to_front()
        except Exception:
            pass
        try:
            while not resume.exists():
                if page.is_closed():
                    raise RuntimeError('采集浏览器已关闭，请重新创建任务')
                await asyncio.sleep(.25)
            resume.unlink(missing_ok=True)
            await client.update_cookies(context)
        finally:
            control.unlink(missing_ok=True)

    for method_name in ('get', 'post'):
        original = getattr(client, method_name, None)
        if not original:
            continue

        def wrapper(method):
            async def guarded(*args, **kwargs):
                async with serial:
                    for attempt in range(4):
                        try:
                            result = await method(*args, **kwargs)
                            if isinstance(result, dict) and (result.get('verify_info') or result.get('verify_data') or result.get('captcha')):
                                raise RuntimeError('平台要求人工验证码')
                            return result
                        except Exception as exc:
                            if attempt == 3 or not needs_verification(exc):
                                raise
                            await pause()
                            # Some upstream methods pass a copy of their headers and
                            # cookie-derived parameters. Refresh only those fields.
                            headers = kwargs.get('headers')
                            if headers is None and len(args) > 2 and isinstance(args[2], dict):
                                headers = args[2]
                            if isinstance(headers, dict) and 'Cookie' in getattr(client, 'headers', {}):
                                headers['Cookie'] = client.headers['Cookie']
                            params = kwargs.get('params')
                            if params is None and len(args) > 1 and isinstance(args[1], dict):
                                params = args[1]
                            if isinstance(params, dict):
                                for signature in ('a_bogus', 'X-Bogus', 'w_rid', 'wts'):
                                    params.pop(signature, None)
                                cookies = getattr(client, 'cookie_dict', {})
                                if 'uifid' in params:
                                    params['uifid'] = cookies.get('UIFID') or cookies.get('UIFID_TEMP', '')
                                for name in ('verifyFp', 'fp'):
                                    if name in params:
                                        params[name] = cookies.get('s_v_web_id', '')
            return guarded
        setattr(client, method_name, wrapper(original))
