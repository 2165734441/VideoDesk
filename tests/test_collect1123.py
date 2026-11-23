"""Listing a profile / collection / search result before downloading."""
import concurrent.futures
import unittest
from unittest import mock

from integrations import collect


class ListingUrl(unittest.TestCase):
    def test_douyin_profile(self):
        url = 'https://www.douyin.com/user/MS4wLjABAAAAabc'
        self.assertEqual(collect.listing_url('dy', 'creator', url), url)
        self.assertEqual(collect.listing_url('dy', 'creator', 'MS4wLjABAAAAabc'), url)

    def test_douyin_liked_and_favourite_need_no_target(self):
        self.assertIn('showTab=like', collect.listing_url('dy', 'liked', ''))
        self.assertIn('showTab=favorite_collection', collect.listing_url('dy', 'fav', ''))

    def test_douyin_search_is_url_encoded(self):
        url = collect.listing_url('dy', 'search', '开播教学')
        self.assertTrue(url.startswith('https://www.douyin.com/search/'))
        self.assertIn('%E5%BC%80%E6%92%AD', url)

    def test_bilibili_profile_from_link_or_uid(self):
        expected = 'https://space.bilibili.com/12345/upload/video'
        self.assertEqual(collect.listing_url('bili', 'creator', 'https://space.bilibili.com/12345'), expected)
        self.assertEqual(collect.listing_url('bili', 'creator', '12345'), expected)

    def test_bilibili_liked_and_favourite(self):
        self.assertEqual(collect.listing_url('bili', 'liked', '12345'),
                         'https://space.bilibili.com/12345/like')
        self.assertEqual(collect.listing_url('bili', 'fav', 'https://space.bilibili.com/12345/video'),
                         'https://space.bilibili.com/12345/favlist')

    def test_bilibili_favourite_folder_is_kept(self):
        folder = 'https://space.bilibili.com/12345/favlist?fid=987&ftype=create'
        self.assertEqual(collect.listing_url('bili', 'fav', folder), folder)

    def test_rejects_wrong_input(self):
        for args in (('dy', 'creator', ''), ('bili', 'creator', 'abc'), ('dy', 'search', ''),
                     ('dy', 'creator', 'https://www.bilibili.com/video/BV1')):
            with self.assertRaises(ValueError):
                collect.listing_url(*args)


class SearchControls(unittest.TestCase):
    def test_douyin_search_sort_and_publish(self):
        url = collect.listing_url('dy', 'search', '开播', sort='latest', publish='day')
        self.assertIn('sort_type=2', url)
        self.assertIn('publish_time=1', url)

    def test_bilibili_search_order_and_page(self):
        url = collect.listing_url('bili', 'search', '开播', sort='plays', page=3)
        self.assertIn('order=click', url)
        self.assertIn('page=3', url)
        self.assertTrue(url.startswith('https://search.bilibili.com/all?keyword='))

    def test_douyin_search_has_no_page(self):
        with self.assertRaises(ValueError):
            collect.listing_url('dy', 'search', '开播', page=2)

    def test_unknown_sort_is_rejected(self):
        with self.assertRaises(ValueError):
            collect.listing_url('dy', 'search', '开播', sort='plays')
        with self.assertRaises(ValueError):
            collect.listing_url('bili', 'search', '开播', sort='likes')


class Folders(unittest.TestCase):
    def test_douyin_folder_page_and_id(self):
        url = collect.listing_url('dy', 'fav', '', folder='7705397966449678106')
        self.assertIn('showSubTab=favorite_folder', url)

    def test_bilibili_folder_url_keeps_fid(self):
        url = collect.listing_url('bili', 'fav', '12345', folder='987')
        self.assertIn('/favlist?fid=987', url)

    def test_folders_only_for_favourites(self):
        with self.assertRaises(ValueError):
            collect.folders_url('dy', 'creator', '123')

    def test_parse_douyin_folders(self):
        payload = {'collects_list': [{
            'collects_id': 7705397966449678106,
            'collects_id_str': '7705397966449678106',
            'collects_name': '软件制作功能术语',
            'total_number': 1,
        }]}
        rows = collect.parse_douyin_folders(payload)
        self.assertEqual(rows[0]['id'], '7705397966449678106')
        self.assertEqual(rows[0]['name'], '软件制作功能术语')
        self.assertEqual(rows[0]['count'], 1)

    def test_parse_bilibili_folders(self):
        payload = {'code': 0, 'data': {'list': [
            {'id': 123, 'title': '默认收藏夹', 'media_count': 5},
            {'id': 456, 'title': '学习', 'media_count': 3},
        ]}}
        rows = collect.parse_bilibili_folders(payload)
        self.assertEqual([r['id'] for r in rows], ['123', '456'])
        self.assertEqual(rows[1]['name'], '学习')

    def test_pick_folder_card_by_name_or_index(self):
        cards = [{'name': '软件制作功能术语', 'index': 0},
                 {'name': '摄影', 'index': 1}]
        self.assertEqual(collect.pick_folder_card(cards, '软件制作功能术语')['index'], 0)
        self.assertEqual(collect.pick_folder_card(cards, '', 1)['index'], 1)
        self.assertIsNone(collect.pick_folder_card(cards, '不存在'))
        self.assertIsNone(collect.pick_folder_card([], '', 0))

    def test_parse_folder_dom_drops_blank_cards(self):
        rows = collect.parse_folder_dom([{'name': '摄影', 'count': 2}, {'name': ''}])
        self.assertEqual(rows, [{'id': '', 'name': '摄影', 'count': 2}])


class Blocked(unittest.TestCase):
    def test_captcha_interstitial_is_reported(self):
        reason = collect._blocked({'url': 'https://www.douyin.com/search/x', 'title': '验证码中间页', 'text': ''})
        self.assertIn('验证码', reason)

    def test_login_redirect_is_reported(self):
        self.assertIn('登录', collect._blocked({'url': 'https://passport.bilibili.com/login', 'title': '', 'text': ''}))

    def test_normal_page_is_not_blocked(self):
        self.assertEqual(collect._blocked({'url': 'https://space.bilibili.com/2/upload/video',
                                           'title': '投稿视频', 'text': 'TA的视频'}), '')


class Page:
    def __init__(self, rows):
        self.rows = rows

    def evaluate(self, script, *args):
        if 'location.href' in script:
            return {'url': 'https://www.douyin.com/user/x', 'title': '主页', 'text': '作品'}
        return self.rows

    def wait_for_timeout(self, _ms):
        pass


class Browser:
    def __init__(self, rows):
        self.page = Page(rows)
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.released = []

    def page_for(self, url, navigate=False):
        return self.page

    def release(self, page, keep=False):
        self.released.append(keep)


class Scan(unittest.TestCase):
    def test_keeps_page_order_and_stops_at_limit(self):
        rows = [{'id': str(i), 'kind': 'video', 'url': 'https://www.douyin.com/video/%d' % i,
                 'title': '第%d期' % i, 'date': ''} for i in range(10)]
        browser = Browser(rows)
        try:
            result = collect.scan(browser, 'https://www.douyin.com/user/x', 4)
        finally:
            browser.pool.shutdown()
        self.assertEqual([i['id'] for i in result['items']], ['0', '1', '2', '3'])
        # The tab is parked, never closed, so the next read reuses its renderer.
        self.assertEqual(browser.released, [True])

    def test_empty_page_reports_instead_of_returning_nothing(self):
        browser = Browser([])
        try:
            with mock.patch.object(collect, 'WARM_SECONDS', 0.2), \
                    mock.patch.object(collect, 'SCAN_SECONDS', 0.6):
                with self.assertRaises(ValueError):
                    collect.scan(browser, 'https://www.douyin.com/user/x', 4)
        finally:
            browser.pool.shutdown()


if __name__ == '__main__':
    unittest.main()
