import asyncio
from pathlib import Path
import tempfile
import unittest

from integrations.manual_gate import attach_manual_gate


class ManualGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_stays_paused_until_manual_resume_and_refreshes_session(self):
        class Page:
            async def bring_to_front(self): pass
            def is_closed(self): return False

        class Client:
            def __init__(self):
                self.attempts=0
                self.headers={'Cookie':'old'}
                self.cookie_dict={}

            async def get(self, uri, params=None, headers=None):
                self.attempts+=1
                if self.attempts==1:
                    raise RuntimeError('403 captcha: complete in the browser')
                return {'cookie':headers['Cookie'], 'ok':True}

            async def update_cookies(self, context):
                self.headers['Cookie']='fresh'

        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'control.json'
            client=Client();attach_manual_gate(client,object(),Page(),str(path))
            request=asyncio.create_task(client.get('/video',headers={'Cookie':'old'}))
            for _ in range(40):
                if path.exists():break
                await asyncio.sleep(.01)
            self.assertTrue(path.exists())
            self.assertFalse(request.done())
            self.assertEqual(client.attempts,1)
            path.with_suffix('.resume').write_text('resume',encoding='utf-8')
            result=await asyncio.wait_for(request,timeout=2)
            self.assertEqual(result,{'cookie':'fresh','ok':True})
            self.assertFalse(path.exists())

    async def test_network_error_does_not_get_mislabeled_as_captcha(self):
        class Client:
            async def get(self,*args,**kwargs):raise RuntimeError('connection reset')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'control.json'
            client=Client();attach_manual_gate(client,None,None,str(path))
            with self.assertRaisesRegex(RuntimeError,'connection reset'):
                await client.get('/video')
            self.assertFalse(path.exists())
