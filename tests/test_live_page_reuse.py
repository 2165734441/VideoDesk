"""Live-check tabs must be torn down, not stacked.

Douyin pages keep hundreds of MB per tab alive (a single playing page was
measured at 1.2 GB); the earlier build left every tab open, so a machine left
running climbed to several GB.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from integrations import login_browser


class FakePage:
    def __init__(self, url='about:blank'):
        self.url = url
        self.closed = False
        self.visits = []

    def is_closed(self):
        return self.closed

    def on(self, *args, **kwargs):
        pass

    def close(self):
        self.closed = True

    def goto(self, url, **options):
        self.visits.append(url)
        self.url = url


class FakeContext:
    def __init__(self):
        self.pages = []
        self.created = 0

    def new_page(self):
        self.created += 1
        page = FakePage()
        self.pages.append(page)
        return page


class PageReuseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.browser = login_browser.LoginBrowser(Path(self.tmp.name))
        self.context = FakeContext()
        self.browser.ensure = Mock(return_value=self.context)

    def tearDown(self):
        self.tmp.cleanup()

    def test_release_closes_the_check_tab(self):
        page = self.browser.page_for('https://www.douyin.com/user/AAA', navigate=False)
        self.browser.release(page)
        self.assertTrue(page.closed)
        self.assertNotIn(page, self.browser.scratch)
        self.assertNotIn(page, self.browser.observed)

    def test_next_check_gets_a_clean_tab(self):
        first = self.browser.page_for('https://www.douyin.com/user/AAA', navigate=False)
        self.browser.release(first)
        second = self.browser.page_for('https://www.douyin.com/user/BBB', navigate=False)
        self.assertIsNot(second, first)
        self.assertEqual(self.context.created, 2)
        self.assertEqual(first.visits, [])

    def test_page_already_on_the_anchor_is_preferred(self):
        page = FakePage('https://www.douyin.com/user/AAA')
        self.context.pages.append(page)
        self.assertIs(self.browser.page_for('https://www.douyin.com/user/AAA'), page)
        self.assertEqual(self.context.created, 0)

    def test_release_ignores_tabs_the_user_opened(self):
        mine = FakePage('https://www.douyin.com/user/AAA')
        self.context.pages.append(mine)
        self.browser.release(mine)
        self.assertFalse(mine.closed)
        self.assertEqual(mine.visits, [])

    def test_release_twice_is_safe(self):
        page = self.browser.page_for('https://www.douyin.com/user/AAA')
        self.browser.release(page)
        self.browser.release(page)
        self.assertTrue(page.closed)

    def test_recycle_keeps_one_warm_blank_tab(self):
        """Everything else is closed, but one blank tab stays warm.

        Rebuilding a renderer for every single check was what made five anchors
        queue past the timeout; the survivor is parked on about:blank, so it
        holds no Douyin page and no video buffer.
        """
        a = self.browser.page_for('https://www.douyin.com/user/AAA')
        a.goto('https://www.douyin.com/user/AAA')
        b = self.browser.page_for('https://www.douyin.com/user/BBB')
        self.browser.recycle()
        self.assertTrue(a.closed)
        self.assertFalse(b.closed)
        self.assertEqual(self.browser.scratch, {b})
        # It still counts as free for the next check.
        self.assertIs(self.browser.page_for('https://www.douyin.com/user/CCC'), b)


if __name__ == '__main__':
    unittest.main()
