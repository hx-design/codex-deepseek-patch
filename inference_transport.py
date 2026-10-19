"""Small direct HTTP/WebSocket transport for the local inference relay.

No proxy discovery, redirects, compression, credentials in errors, or third party
runtime dependencies. A WebSocket has one reader; writes are safe across threads.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import http.client
import os
import re
import socket
import ssl
import struct
import threading
from urllib.parse import unquote, urlsplit


_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
_TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
        'te', 'trailer', 'transfer-encoding', 'upgrade', 'host', 'content-length'}


class WebSocketError(ConnectionError):
    """A malformed or unsupported WebSocket exchange."""


def _pairs(headers):
    return list(headers.items()) if hasattr(headers, 'items') else list(headers or ())


def _checked_headers(headers):
    result = []
    for name, value in _pairs(headers):
        if not isinstance(name, str) or not _TOKEN.fullmatch(name):
            raise ValueError('Invalid HTTP header name')
        if not isinstance(value, str) or any(ord(c) < 32 and c != '\t' or ord(c) == 127 for c in value):
            raise ValueError('Invalid HTTP header value')
        # http.client uses ISO-8859-1; reject unusable values before connecting.
        value.encode('latin-1')
        result.append((name, value))
    return result


def sanitize_headers(headers, *, websocket=False, request=True):
    """Return safe end-to-end header pairs, preserving duplicate response headers.

    Recompute Host/Content-Length at the destination. Proxy credentials never
    leave this boundary. WebSocket handshakes are generated independently.
    ``request`` documents caller intent; the same hop rules apply both ways.
    """
    pairs = _checked_headers(headers)
    drop = _HOP | {token.strip().lower() for name, value in pairs
                   if name.lower() == 'connection' for token in value.split(',')}
    return [(name, value) for name, value in pairs if name.lower() not in drop
            and not (websocket and name.lower().startswith('sec-websocket-'))]


def _tokens(value):
    return {s.strip().lower() for s in value.split(',')}


def _accept(key):
    return base64.b64encode(hashlib.sha1((key + _GUID).encode('ascii')).digest()).decode('ascii')


def _valid_close(code):
    return code in {1000, 1001, 1002, 1003, 1007, 1008, 1009, 1010, 1011, 1012, 1013, 1014} or 3000 <= code <= 4999


class WebSocket:
    def __init__(self, sock, reader=None, *, is_client=False, max_message=64_000_000):
        if not isinstance(max_message, int) or max_message < 1:
            raise ValueError('max_message must be positive')
        self.sock = sock
        self.reader = reader if reader is not None else sock.makefile('rb')
        self.is_client = is_client
        self.max_message = max_message
        self._write_lock = threading.Lock()
        self._closed = False
        self._close_sent = False

    def _read(self, size, *, eof_ok=False):
        chunks = bytearray()
        while len(chunks) < size:
            chunk = self.reader.read(size - len(chunks))
            if not chunk:
                if not chunks and eof_ok:
                    return None
                raise WebSocketError('Truncated WebSocket frame')
            chunks.extend(chunk)
        return bytes(chunks)

    def _frame(self, remaining):
        header = self._read(2, eof_ok=True)
        if header is None:
            return None
        first, second = header
        final, opcode = bool(first & 128), first & 15
        masked, length = bool(second & 128), second & 127
        if first & 112 or opcode not in {0, 1, 2, 8, 9, 10}:
            raise WebSocketError('Unsupported WebSocket frame flags or opcode')
        if masked == self.is_client:
            raise WebSocketError('Invalid WebSocket masking direction')
        if length == 126:
            length = struct.unpack('!H', self._read(2))[0]
            if length < 126:
                raise WebSocketError('Nonminimal WebSocket length')
        elif length == 127:
            length = struct.unpack('!Q', self._read(8))[0]
            if length < 65536 or length >> 63:
                raise WebSocketError('Invalid WebSocket length')
        if opcode >= 8:
            if not final or length > 125:
                raise WebSocketError('Invalid WebSocket control frame')
        elif length > remaining:
            raise _MessageTooLarge('WebSocket message exceeds limit')
        key = self._read(4) if masked else None
        payload = self._read(length)
        if key:
            payload = bytes(value ^ key[i % 4] for i, value in enumerate(payload))
        return final, opcode, payload

    def _send_frame(self, payload, opcode):
        with self._write_lock:
            if self._closed or self._close_sent and opcode != 8:
                raise WebSocketError('WebSocket is closed')
            length = len(payload)
            header = bytes([128 | opcode, (128 if self.is_client else 0) | min(length, 126)])
            if length >= 65536:
                header = bytes([128 | opcode, (128 if self.is_client else 0) | 127]) + struct.pack('!Q', length)
            elif length >= 126:
                header += struct.pack('!H', length)
            if self.is_client:
                key = os.urandom(4)
                header += key
                payload = bytes(value ^ key[i % 4] for i, value in enumerate(payload))
            self.sock.sendall(header + payload)
            if opcode == 8:
                self._close_sent = True

    def send(self, payload, opcode=1):
        """Send a complete text (1) or binary (2) message, without transforming it."""
        if opcode not in (1, 2):
            raise ValueError('send requires text or binary opcode')
        if isinstance(payload, str):
            payload = payload.encode('utf-8')
        else:
            payload = bytes(payload)
        if len(payload) > self.max_message:
            raise _MessageTooLarge('WebSocket message exceeds limit')
        if opcode == 1:
            payload.decode('utf-8')
        self._send_frame(payload, opcode)

    def recv(self):
        """Read a complete message as (opcode, bytes), or None after peer close.

        Control frames are handled here; continuation frames are reassembled.
        Invalid input closes the connection and raises WebSocketError.
        """
        if self._closed:
            return None
        opcode = None
        chunks = bytearray()
        try:
            while True:
                frame = self._frame(self.max_message - len(chunks))
                if frame is None:
                    if opcode is not None:
                        raise WebSocketError('Truncated fragmented WebSocket message')
                    self._shutdown()
                    return None
                final, kind, payload = frame
                if kind == 9:
                    self._send_frame(payload, 10)
                    continue
                if kind == 10:
                    continue
                if kind == 8:
                    if len(payload) == 1:
                        raise WebSocketError('Invalid WebSocket close payload')
                    if payload:
                        if not _valid_close(struct.unpack('!H', payload[:2])[0]):
                            raise WebSocketError('Invalid WebSocket close code')
                        payload[2:].decode('utf-8')
                    if not self._close_sent:
                        self._send_frame(payload, 8)
                    self._shutdown()
                    return None
                if kind == 0:
                    if opcode is None:
                        raise WebSocketError('Unexpected WebSocket continuation')
                elif opcode is not None:
                    raise WebSocketError('Interleaved WebSocket data messages')
                else:
                    opcode = kind
                chunks.extend(payload)
                if final:
                    result = bytes(chunks)
                    if opcode == 1:
                        result.decode('utf-8')
                    return opcode, result
        except (WebSocketError, UnicodeError) as exc:
            self.close(1009 if isinstance(exc, _MessageTooLarge) else 1007 if isinstance(exc, UnicodeError) else 1002)
            if isinstance(exc, UnicodeError):
                raise WebSocketError('Invalid WebSocket UTF-8') from None
            raise
        except OSError:
            self._shutdown()
            raise

    def _shutdown(self):
        self._closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
        self.reader.close()

    def close(self, code=1000, reason=''):
        if not _valid_close(code):
            raise ValueError('Invalid WebSocket close code')
        payload = struct.pack('!H', code) + reason.encode('utf-8')
        if len(payload) > 125:
            raise ValueError('WebSocket close reason is too long')
        if self._closed:
            return
        try:
            if not self._close_sent:
                self._send_frame(payload, 8)
        except OSError:
            pass
        finally:
            self._shutdown()


class _MessageTooLarge(WebSocketError):
    pass


def accept_websocket(handler, *, max_message=64_000_000, response_headers=None):
    """Upgrade an authenticated BaseHTTPRequestHandler. Caller checks Origin/auth.

    Invalid upgrades raise before writing a response, so the caller can return
    its own HTTP error. Existing buffered request bytes stay on handler.rfile.
    """
    headers = handler.headers
    key = headers.get('Sec-WebSocket-Key', '')
    try:
        valid_key = len(base64.b64decode(key, validate=True)) == 16
    except (ValueError, binascii.Error):
        valid_key = False
    if (handler.command != 'GET' or handler.request_version != 'HTTP/1.1'
            or _tokens(headers.get('Upgrade', '')) != {'websocket'}
            or 'upgrade' not in _tokens(headers.get('Connection', ''))
            or headers.get('Sec-WebSocket-Version') != '13' or not valid_key
            or any(len(headers.get_all(name, [])) != 1 for name in
                   ('Sec-WebSocket-Key', 'Sec-WebSocket-Version', 'Upgrade'))
            or headers.get('Transfer-Encoding') or headers.get('Content-Length', '0') != '0'):
        raise WebSocketError('Invalid WebSocket upgrade request')
    handler.send_response_only(101, 'Switching Protocols')
    handler.send_header('Upgrade', 'websocket')
    handler.send_header('Connection', 'Upgrade')
    handler.send_header('Sec-WebSocket-Accept', _accept(key))
    for name, value in sanitize_headers(response_headers, websocket=True, request=False):
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.flush()
    handler.close_connection = True
    return WebSocket(handler.connection, handler.rfile, max_message=max_message)


def create_http_connection(url, *, timeout=60, proxy_url=None, ssl_context=None):
    """Create a direct or explicitly HTTP-CONNECT-tunnelled HTTP(S) connection.

    A proxy URL may carry proxy credentials. They appear only on CONNECT, never
    on the request to the origin. No environment proxy discovery happens here.
    """
    parsed = urlsplit(url)
    secure = parsed.scheme in ('https', 'wss')
    if ssl_context is not None and (not ssl_context.check_hostname or ssl_context.verify_mode != ssl.CERT_REQUIRED):
        raise ValueError('TLS requires hostname and certificate verification')
    cls = http.client.HTTPSConnection if secure else http.client.HTTPConnection
    kwargs = {'timeout': timeout}
    if secure:
        kwargs['context'] = ssl_context or ssl.create_default_context()
    host, port = parsed.hostname, parsed.port or (443 if secure else 80)
    tunnel_headers = {}
    if proxy_url:
        proxy = urlsplit(proxy_url)
        if (proxy.scheme != 'http' or not proxy.hostname or proxy.path not in ('', '/')
                or proxy.query or proxy.fragment or any(ord(c) < 33 for c in proxy_url)):
            raise ValueError('An HTTP CONNECT proxy URL is required')
        if proxy.username is not None:
            credentials = unquote(proxy.username) + ':' + unquote(proxy.password or '')
            if any(ord(c) < 32 or ord(c) == 127 for c in credentials):
                raise ValueError('Invalid proxy credentials')
            tunnel_headers['Proxy-Authorization'] = 'Basic ' + base64.b64encode(credentials.encode()).decode('ascii')
        connection = cls(proxy.hostname, proxy.port or 80, **kwargs)
        connection.set_tunnel(host, port, headers=tunnel_headers)
    else:
        connection = cls(host, port, **kwargs)
    return connection


def connect_websocket(url, headers=None, *, timeout=60, max_message=64_000_000, ssl_context=None, proxy_url=None):
    """Connect directly to WS/WSS; return (WebSocket, response header pairs).

    TLS verifies hostname and trust by default. A custom context is useful for
    an explicitly trusted local test certificate. Redirects are never followed.
    """
    parsed = urlsplit(url)
    if (parsed.scheme not in ('ws', 'wss') or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment or any(ord(c) < 33 for c in url)):
        raise ValueError('Invalid WebSocket URL')
    pairs = sanitize_headers(headers, websocket=True)
    key = base64.b64encode(os.urandom(16)).decode('ascii')
    target = parsed.path or '/'
    if parsed.query:
        target += '?' + parsed.query
    connection = create_http_connection(url, timeout=timeout, proxy_url=proxy_url, ssl_context=ssl_context)
    response = None
    try:
        connection.putrequest('GET', target, skip_accept_encoding=True)
        for name, value in pairs:
            connection.putheader(name, value)
        for name, value in [('Upgrade', 'websocket'), ('Connection', 'Upgrade'),
                            ('Sec-WebSocket-Key', key), ('Sec-WebSocket-Version', '13')]:
            connection.putheader(name, value)
        connection.endheaders()
        # Keep HTTPResponse's buffered reader: it may already contain WS bytes.
        response = http.client.HTTPResponse(connection.sock)
        response.begin()
        result_headers = response.getheaders()
        if response.status != 101:
            raise WebSocketError(f'WebSocket upgrade rejected (HTTP {response.status})')
        if (_tokens(response.getheader('Upgrade', '')) != {'websocket'}
                or 'upgrade' not in _tokens(response.getheader('Connection', ''))
                or response.getheader('Sec-WebSocket-Accept') != _accept(key)
                or response.getheader('Sec-WebSocket-Extensions')
                or response.getheader('Sec-WebSocket-Protocol')):
            raise WebSocketError('Invalid WebSocket upgrade response')
        ws = WebSocket(connection.sock, response.fp, is_client=True, max_message=max_message)
        # Ownership moves to WebSocket. HTTPResponse.close() otherwise flushes
        # this reader again during GC after WS shutdown (an error on Python 3.13).
        response.fp = None
        response.close()
        ws._http_connection = connection
        return ws, result_headers
    except BaseException:
        connection.close()
        if response is not None:
            response.close()
        raise
