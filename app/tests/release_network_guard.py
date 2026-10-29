"""Test runner transport guard; disposable ASGI loopback is allowed, external hosts are not."""
import socket
_connect=socket.socket.connect
def _local_only(sock,address):
    if isinstance(address,tuple) and str(address[0])not in {'127.0.0.1','localhost','::1'}:
        raise AssertionError('Release unit checks forbid external network access')
    return _connect(sock,address)
socket.socket.connect=_local_only
