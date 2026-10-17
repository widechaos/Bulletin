import gzip
import queue
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import sources

RSS = b'<?xml version="1.0"?><rss version="2.0"><channel><title>News</title><item><guid>one</guid><title>First</title></item></channel></rss>'


class FeedHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        state = self.server.state
        state['requests'].append(dict(self.headers))
        if state.get('etag') and self.headers.get('If-None-Match') == state['etag']:
            self.send_response(304)
            self.send_header('ETag', state['etag'])
            self.end_headers()
            return
        if not state.get('etag') and state.get('modified') and self.headers.get('If-Modified-Since') == state['modified']:
            self.send_response(304)
            self.end_headers()
            return
        body = state.get('body', RSS)
        if state.get('gzip'):
            body = gzip.compress(body)
        self.send_response(200)
        self.send_header('Content-Type', 'application/rss+xml')
        if state.get('etag'):
            self.send_header('ETag', state['etag'])
        if state.get('modified'):
            self.send_header('Last-Modified', state['modified'])
        if state.get('gzip'):
            self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), FeedHandler)
        self.server.state = {'requests': [], 'etag': '"v1"', 'gzip': True}
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/feed'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()

    def test_compressed_feed_and_conditional_refresh(self):
        validators = sources.FeedValidators()
        first = sources.fetch_parse(self.url, '', validators)
        self.assertEqual(first.entries[0].title, 'First')
        self.assertIsNone(sources.fetch_parse(self.url, '', validators))
        self.assertEqual(self.server.state['requests'][0]['Accept-Encoding'], 'gzip')
        self.assertEqual(self.server.state['requests'][1]['If-None-Match'], '"v1"')
        self.server.state.update(etag='"v2"', body=RSS.replace(b'First', b'Second'))
        self.assertEqual(sources.fetch_parse(self.url, '', validators).entries[0].title, 'Second')
        self.assertEqual(validators.etag, '"v2"')

    def test_last_modified_fallback(self):
        self.server.state.update(etag='', modified='Wed, 14 Oct 2026 08:00:00 GMT', gzip=False)
        validators = sources.FeedValidators()
        self.assertEqual(len(sources.fetch_parse(self.url, '', validators).entries), 1)
        self.assertIsNone(sources.fetch_parse(self.url, '', validators))
        self.assertIn('If-Modified-Since', self.server.state['requests'][1])

    def test_without_validators_probe_always_gets_body(self):
        sources.fetch_parse(self.url, '')
        self.assertEqual(len(sources.fetch_parse(self.url, '').entries), 1)
        self.assertNotIn('If-None-Match', self.server.state['requests'][1])

    def test_changed_response_without_validators_clears_old_values(self):
        validators = sources.FeedValidators('"old"', 'old-date')
        self.server.state.update(etag='', gzip=False)
        sources.fetch_parse(self.url, '', validators)
        self.assertEqual(validators, sources.FeedValidators())

    def test_invalid_feed_does_not_advance_etag(self):
        self.server.state.update(body=b'not xml', etag='"bad"')
        validators = sources.FeedValidators('"old"')
        self.assertTrue(sources.fetch_parse(self.url, '', validators).bozo)
        self.assertEqual(validators.etag, '"old"')

    def test_private_auth_is_header_not_request_url(self):
        url = self.url.replace('http://', 'http://test:password@')
        sources.fetch_parse(url, '')
        self.assertEqual(self.server.state['requests'][0]['Authorization'], 'Basic dGVzdDpwYXNzd29yZA==')


class PollTests(unittest.TestCase):
    def test_source_cadence_and_configuration_changes(self):
        base = 'https://example.invalid/private'
        conf = {'rsshub_base': base, 'poll_interval': 120, 'conserve_bandwidth': True,
                'feeds': ['{rsshub}/zhihu/daily', '{rsshub}/weibo/search/hot', '{rsshub}/cls/telegraph']}
        with patch.object(sources.cfg, 'load_seen', return_value=set()):
            poller = sources.RSSPoller(conf, queue.Queue())
        with patch.object(sources, 'fetch_parse', return_value=None) as fetch, patch.object(sources.time, 'monotonic', return_value=1000) as clock:
            self.assertEqual(poller._poll(), [])
            self.assertEqual(fetch.call_count, 3)
            caches = [call.args[2] for call in fetch.call_args_list]
            self.assertEqual(len({id(c) for c in caches}), 3)
            clock.return_value = 1120
            poller._poll()
            self.assertEqual(fetch.call_count, 4)  # only live news
            clock.return_value = 1600
            poller._poll()
            self.assertEqual(fetch.call_count, 6)  # hot list + live news
            clock.return_value = 2800
            poller._poll()
            self.assertEqual(fetch.call_count, 9)
            conf['conserve_bandwidth'] = False
            clock.return_value = 2920
            poller._poll()
            self.assertEqual(fetch.call_count, 12)
            conf['feeds'] = ['{rsshub}/cls/telegraph']
            poller._poll()
            self.assertEqual(len(poller._validators), 1)

    def test_daily_rule_does_not_match_other_hosts_or_routes(self):
        conf = {'rsshub_base': 'https://example.invalid/private', 'poll_interval': 120}
        self.assertEqual(sources.feed_interval('https://other.invalid/private/zhihu/daily', conf), 120)
        self.assertEqual(sources.feed_interval('https://example.invalid/private/zhihu/daily-extra', conf), 120)
        self.assertEqual(sources.feed_interval('https://example.invalid/private/zhihu/daily', conf), 1800)


if __name__ == '__main__':
    unittest.main()
