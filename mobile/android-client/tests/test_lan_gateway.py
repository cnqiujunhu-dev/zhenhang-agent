"""Disposable HTTP endpoints, no running application or banking data."""
from pathlib import Path
import importlib.util
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
import unittest

path = Path(__file__).resolve().parents[1] / 'lan_gateway.py'
spec = importlib.util.spec_from_file_location('lan_gateway', path)
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.records = []
        records = self.records

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_GET(self):
                records.append((self.command, self.path, dict(self.headers), b''))
                if self.path == '/api/redirect':
                    self.send_response(307)
                    self.send_header('Location', 'http://' + self.headers['Host'] + '/api/final/')
                    self.end_headers(); return
                payload = b'{"status":"ready"}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Connection', 'x-hidden')
                self.send_header('X-Hidden', 'not-forwarded')
                self.end_headers()
                self.wfile.write(payload)
            def do_HEAD(self):
                self.send_response(200); self.send_header('Content-Length', '17'); self.end_headers()
            def do_POST(self):
                data = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                records.append((self.command, self.path, dict(self.headers), data))
                self.send_response(201); self.send_header('Content-Length', str(len(data)))
                self.end_headers(); self.wfile.write(data)

        self.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        self.proxy = ThreadingHTTPServer(('127.0.0.1', 0), gateway.handler(self.upstream.server_port))
        self.threads = []
        for server in (self.upstream, self.proxy):
            t = threading.Thread(target=server.serve_forever, daemon=True); t.start(); self.threads.append(t)

    def tearDown(self):
        for server in (self.proxy, self.upstream): server.shutdown(); server.server_close()
        for thread in self.threads: thread.join(timeout=2)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.proxy.server_port, timeout=3)
        conn.request(method, path, body, headers or {})
        response = conn.getresponse(); data = response.read(); result = response.status, dict(response.headers), data
        conn.close(); return result

    def test_exact_health_and_ui_paths_and_query_preserved(self):
        for path in ['/api/v2/health/ready', '/ui/assets/app.js?v=2', '/']:
            status, headers, body = self.request('GET', path)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)['status'], 'ready')
            self.assertEqual(self.records[-1][1], path)
            self.assertNotIn('X-Hidden', headers)

    def test_no_arbitrary_proxy_or_unrelated_files(self):
        for path in ['http://example.com/api/data', '//example.com/api/data', '/etc/passwd', '/private']:
            self.assertEqual(self.request('GET', path)[0], 404)
        self.assertEqual(self.records, [])

    def test_post_body_and_auth_forwarded_exactly_once(self):
        payload = b'--boundary\r\nimage-data\x00\xff\r\n--boundary--'
        status, _, result = self.request('POST', '/api/v3/attachments', payload,
            {'Authorization': 'Bearer fixture-only', 'Content-Type': 'multipart/form-data; boundary=boundary',
             'Connection': 'x-remove', 'X-Remove': 'hide', 'X-Forwarded-For': 'untrusted'})
        self.assertEqual((status, result), (201, payload))
        self.assertEqual(len(self.records), 1)
        _, _, headers, received = self.records[0]
        self.assertEqual(received, payload)
        self.assertEqual(headers.get('Authorization'), 'Bearer fixture-only')
        self.assertNotIn('X-Remove', headers)
        self.assertNotIn('X-Forwarded-For', headers)

    def test_head_has_no_body(self):
        self.assertEqual(self.request('HEAD', '/ui/')[2], b'')

    def test_redirect_keeps_client_origin(self):
        status, headers, _ = self.request('GET', '/api/redirect')
        self.assertEqual(status, 307)
        self.assertEqual(headers['Location'], f'http://127.0.0.1:{self.proxy.server_port}/api/final/')

    def test_bad_length_rejected_without_upstream_call(self):
        self.assertEqual(self.request('POST', '/api/test', b'', {'Content-Length': 'invalid'})[0], 400)
        self.assertEqual(self.records, [])

    def test_oversized_body_rejected_before_reading(self):
        self.assertEqual(self.request('POST', '/api/test', b'', {'Content-Length': str(gateway.MAX_BODY_BYTES + 1)})[0], 413)
        self.assertEqual(self.records, [])

    def test_forged_host_rejected(self):
        self.assertEqual(self.request('GET', '/api/test', headers={'Host': 'example.com'})[0], 400)
        self.assertEqual(self.records, [])

    def test_upstream_unavailable_not_retried(self):
        self.upstream.shutdown(); self.upstream.server_close()
        status, _, body = self.request('POST', '/api/test', b'{"amount":1}')
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body)['error']['code'], 'LOCAL_SERVICE_UNAVAILABLE')
        self.assertEqual(self.records, [])

    def test_private_bind_addresses(self):
        for value in ['192.168.31.42', '10.0.2.2', '172.16.0.1', '172.31.255.254', '127.0.0.1']:
            self.assertTrue(gateway.private_ipv4(value), value)
        for value in ['0.0.0.0', '8.8.8.8', '169.254.1.2', '172.32.1.1', '192.168.01.1', 'localhost', '::1', None]:
            self.assertFalse(gateway.private_ipv4(value), value)


if __name__ == '__main__': unittest.main(verbosity=2)
