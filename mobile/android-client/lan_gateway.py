"""Explicit LAN endpoint to an existing loopback service; it never accesses a DB.

No generic forwarding targets, request logs, body logs, payment retry or automatic
firewall changes. A failed POST remains failed/unknown for the existing client
recovery workflow to reconcile.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import HTTPConnection, HTTPException
from urllib.parse import urlsplit
import argparse
import ipaddress
import json

MAX_BODY_BYTES = 20 * 1024 * 1024
HOP = {'connection', 'transfer-encoding', 'keep-alive', 'proxy-authenticate',
       'proxy-authorization', 'te', 'trailer', 'upgrade'}
PRIVATE_V4 = tuple(ipaddress.ip_network(value) for value in
                   ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8'))


def private_ipv4(value):
    """No wildcard, hostname resolution, IPv6 or link-local fallback."""
    try:
        address = ipaddress.IPv4Address(value)
        return str(address) == value and any(address in net for net in PRIVATE_V4)
    except (ipaddress.AddressValueError, TypeError):
        return False


def _hop_headers(headers):
    named = {part.strip().lower() for value in headers.get_all('Connection', [])
             for part in value.split(',') if part.strip()}
    return HOP | named


def handler(upstream):
    if type(upstream) is not int or not 1 <= upstream <= 65535:
        raise ValueError('Invalid upstream port')

    class Proxy(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'

        def setup(self):
            super().setup()
            self.connection.settimeout(30)

        def log_message(self, *_args):
            pass  # Paths, authorization and upload names stay out of logs.

        def _error(self, status, code):
            body = json.dumps({'error': {'code': code}}, separators=(',', ':')).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def relay(self):
            try:
                uri = urlsplit(self.path)
            except ValueError:
                self._error(400, 'LOCAL_PATH_INVALID')
                return
            if uri.scheme or uri.netloc or not self.path.startswith('/') or not (
                    uri.path == '/' or uri.path.startswith(('/api/', '/ui/'))):
                self._error(404, 'LOCAL_PATH_NOT_ALLOWED')
                return
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) > 1 or lengths and not lengths[0].isdigit():
                self._error(400, 'LOCAL_BODY_LENGTH_INVALID')
                return
            size = int(lengths[0]) if lengths else 0
            if size > MAX_BODY_BYTES or self.headers.get('Transfer-Encoding'):
                self._error(413, 'LOCAL_BODY_TOO_LARGE')
                return
            host = self.headers.get('Host')
            expected_host = '%s:%s' % self.server.server_address[:2]
            if host != expected_host:
                self._error(400, 'LOCAL_HOST_INVALID')
                return
            conn = None
            response_started = False
            try:
                body = self.rfile.read(size) if size else None
                if body is not None and len(body) != size:
                    self._error(400, 'LOCAL_BODY_INCOMPLETE')
                    return
                conn = HTTPConnection('127.0.0.1', upstream, timeout=125)
                removed = _hop_headers(self.headers) | {'content-length', 'expect',
                          'x-forwarded-for', 'x-forwarded-proto', 'forwarded'}
                headers = {k: v for k, v in self.headers.items() if k.lower() not in removed}
                # Preserve the validated LAN Host, so FastAPI slash redirects
                # keep the phone's origin instead of pointing at its loopback.
                headers['Host'] = host
                conn.request(self.command, self.path, body, headers)
                response = conn.getresponse()
                self.send_response(response.status)
                removed_response = _hop_headers(response.headers)
                for key, value in response.getheaders():
                    if key.lower() not in removed_response:
                        self.send_header(key, value)
                self.end_headers()
                response_started = True
                if self.command != 'HEAD':
                    while chunk := response.read(65536):
                        self.wfile.write(chunk)
            except (ConnectionError, TimeoutError, OSError, HTTPException):
                # Do not emit a second HTTP status after a partial response and
                # never retry the bank request on the phone's behalf.
                if not response_started:
                    try:
                        self._error(502, 'LOCAL_SERVICE_UNAVAILABLE')
                    except OSError:
                        pass
            finally:
                if conn is not None:
                    conn.close()

        do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = relay

    return Proxy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, default=18769)
    parser.add_argument('--upstream', type=int, default=18765)
    args = parser.parse_args()
    if not private_ipv4(args.host) or not 1 <= args.port <= 65535:
        raise SystemExit('Use a specific private IPv4 address and valid port')
    server = ThreadingHTTPServer((args.host, args.port), handler(args.upstream))
    server.daemon_threads = True
    print(json.dumps({'listening': f'http://{args.host}:{args.port}',
                      'upstream': f'http://127.0.0.1:{args.upstream}'}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
