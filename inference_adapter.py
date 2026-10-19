"""Loopback-only model relay for an unmodified official Codex client.

The profile contains routing/credential references, never conversation history.
Official requests retain their own auth; registered models use independent auth.
"""
from __future__ import annotations

import argparse
import copy
import hmac
import http.client
import http.server
import ipaddress
import json
from pathlib import Path
import queue
import re
import select
import socket
import socketserver
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import urllib.request

from inference_transport import (WebSocketError, accept_websocket, connect_websocket,
                                 create_http_connection, sanitize_headers)
from responses_compat import ToolAdapter
from legacy_history import LegacyHistory, LegacyHistoryError, visible_reasoning
from reasoning_origin import (LegacyOriginIndex, LegacyOriginIndexError, ReasoningOriginError,
                               resolve_request, stamp_event, stamp_response)
from chat_completions_compat import ChatCompatibilityError, ResponsesToChat

MAX_BODY = 64_000_000
TERMINAL = {'response.completed', 'response.failed', 'response.incomplete', 'error'}


class RelayError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')


def _error(code, message):
    return {'type': 'error', 'error': {'type': 'invalid_request_error', 'code': code, 'message': message}}


def _loopback(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _endpoint(base, suffix='responses', *, allow_remote_http=False):
    parsed = urlsplit(base)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or any(ord(c) < 33 for c in base)
            or parsed.scheme == 'http' and not allow_remote_http and not _loopback(parsed.hostname)):
        raise ValueError('Endpoint requires HTTPS or an HTTP loopback fixture')
    parsed.port  # Validate before opening a socket.
    return base.rstrip('/') + '/' + suffix


def validate_profile(profile):
    if (profile.get('format') != 1 or profile.get('edition') not in ('ds', 'proxy')
            or not isinstance(profile.get('identity'), str) or not profile['identity']
            or not isinstance(profile.get('token'), str) or len(profile['token']) < 32
            or not all(c.isascii() and (c.isalnum() or c in '-_') for c in profile['token'])
            or type(profile.get('port')) is not int or not 0 <= profile['port'] <= 65535
            or not isinstance(profile.get('routes'), dict)):
        raise ValueError('Invalid local inference profile')
    if 'official_models' in profile:
        models = profile['official_models']
        if (not isinstance(models, list) or any(not isinstance(model, str) or not model for model in models)
                or len(set(models)) != len(models)):
            raise ValueError('Official models must be a list of unique model identifiers')
    if 'legacy_history_dir' in profile or 'summary_model' in profile:
        if (not isinstance(profile.get('legacy_history_dir'), str) or not Path(profile['legacy_history_dir']).is_absolute()
                or not isinstance(profile.get('summary_model'), str)
                or not profile['summary_model'].startswith(('gpt-', 'codex-'))):
            raise ValueError('Legacy history conversion requires an absolute cache directory and official summary model')
    if 'legacy_origins_file' in profile and (not isinstance(profile['legacy_origins_file'], str)
                                           or not Path(profile['legacy_origins_file']).is_absolute()):
        raise ValueError('Legacy reasoning origin index requires an absolute path')
    for key in ('chatgpt_base_url', 'api_base_url'):
        _endpoint(profile['gpt'][key])
    for model, route in profile['routes'].items():
        if not isinstance(model, str) or not model or not isinstance(route, dict):
            raise ValueError('Invalid model route')
        _endpoint(route['base_url'], allow_remote_http=True)
        if route.get('wire_api', 'responses') not in ('responses', 'chat_completions'):
            raise ValueError('Unsupported model wire protocol')
        if 'supports_reasoning_effort' in route and type(route['supports_reasoning_effort']) is not bool:
            raise ValueError('Reasoning effort support must be an explicit boolean')
        credential = route.get('credential')
        if credential is None or credential.get('kind') == 'none':
            continue
        required = {'file': ('path',), 'proxy': ('path',), 'keychain': ('account', 'service'), 'windows': ('target',)}
        if credential.get('kind') not in required or any(not isinstance(credential.get(k), str) or not credential[k]
                for k in required.get(credential.get('kind'), ())):
            raise ValueError('Invalid credential reference')
    return profile


def load_profile(path):
    return validate_profile(json.loads(Path(path).read_text(encoding='utf-8')))


def read_credential(reference):
    """Read one existing credential; error text never contains credential data."""
    try:
        if reference is None or reference.get('kind') == 'none':
            return None
        kind = reference['kind']
        if kind == 'file':
            value = Path(reference['path']).read_text(encoding='utf-8').strip()
        elif kind == 'proxy':
            from proxy_support import read_api_key
            value = read_api_key(reference['path'])
        elif kind == 'keychain':
            result = subprocess.run(['/usr/bin/security', 'find-generic-password', '-a', reference['account'],
                                     '-s', reference['service'], '-w'], capture_output=True, check=True, timeout=15)
            value = result.stdout.decode('utf-8').strip()
        elif kind == 'windows':
            from windows_support import credential_get
            value = credential_get(reference['target'])
        else:
            raise ValueError()
        if not isinstance(value, str) or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError()
        return value
    except Exception:
        raise RelayError(503, 'credential_unavailable', 'The configured model credential is unavailable') from None


def _route_headers(server, route, *, stream):
    headers = [('Content-Type', 'application/json'),
               ('Accept', 'text/event-stream' if stream else 'application/json')]
    reference = route.get('credential')
    if reference is not None and reference.get('kind') != 'none':
        headers.append(('Authorization', 'Bearer ' + server.credential_reader(reference)))
    return headers


def prepare_request(body, route):
    body = copy.deepcopy(body)
    for key in ('type', 'generate', 'previous_response_id'):
        body.pop(key, None)
    items = body.get('input', [])
    if isinstance(items, list) and any(isinstance(v, dict) and v.get('type') in ('compaction', 'compaction_trigger') for v in items):
        raise RelayError(501, 'remote_compaction_unsupported', 'Remote compaction is unavailable for this model route')
    if route.get('chat_only'):
        for key in ('tools', 'tool_choice', 'parallel_tool_calls'):
            body.pop(key, None)
        if isinstance(body.get('input'), list):
            body['input'] = [v for v in body['input'] if not isinstance(v, dict) or v.get('type') != 'additional_tools']
    items = body.get('input', [])
    if route.get('normalize_instructions') and isinstance(items, list):
        instructions = [body['instructions']] if body.get('instructions') else []
        messages = []
        for item in items:
            if isinstance(item, dict) and item.get('role') in ('system', 'developer'):
                content = item.get('content', '')
                instructions.append(content if isinstance(content, str) else '\n'.join(
                    x.get('text', '') for x in content if isinstance(x, dict)))
            else:
                messages.append(item)
        body['instructions'], body['input'] = '\n\n'.join(instructions), messages
    chat = route.get('wire_api') == 'chat_completions'
    adapter = ToolAdapter(body, flatten_namespaces=chat or bool(route.get('flatten_namespaces'))) if chat or route.get('tool_adapter') else None
    return adapter.body if adapter else body, adapter


def _protocol_request(body, route):
    try:
        protocol = (ResponsesToChat(body, supports_reasoning_effort=route.get('supports_reasoning_effort', False))
                    if route.get('wire_api') == 'chat_completions' else None)
        return protocol.body if protocol else body, protocol
    except ChatCompatibilityError as exc:
        raise RelayError(exc.status, exc.code, str(exc)) from None


def _route_url(route):
    suffix = 'chat/completions' if route.get('wire_api') == 'chat_completions' else 'responses'
    return _endpoint(route['base_url'], suffix, allow_remote_http=True)


class Operation:
    """A connection owned by one request, including cancellation before connect."""
    def __init__(self):
        self.connection = None
        self.socket = None
        self.cancelled = threading.Event()
        self.lock = threading.Lock()

    def attach(self, connection):
        with self.lock:
            self.connection = connection
            if self.cancelled.is_set():
                connection.close()
                raise OSError('Request cancelled')

    def cancel(self):
        self.cancelled.set()
        self.close_connection()

    def close_connection(self):
        with self.lock:
            connection = self.connection
            if connection:
                wire_socket = self.socket or connection.sock
                if wire_socket:
                    try:
                        wire_socket.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                connection.close()
            self.connection = self.socket = None


def network_proxy(url, profile):
    """Respect the existing network proxy while keeping every loopback direct."""
    parsed = urlsplit(url)
    if _loopback(parsed.hostname):
        return None
    if 'network_proxy' in profile:
        return profile['network_proxy'] or None
    if urllib.request.proxy_bypass(parsed.hostname):
        return None
    proxies = urllib.request.getproxies()
    scheme = 'https' if parsed.scheme in ('https', 'wss') else 'http'
    return proxies.get(scheme) or proxies.get('all')


def _open(url, raw, headers, operation, profile):
    parsed = urlsplit(url)
    connection = create_http_connection(url, timeout=120, proxy_url=network_proxy(url, profile))
    operation.attach(connection)
    target = parsed.path or '/'
    connection.request('POST', target, body=raw, headers=dict(headers))
    operation.socket = connection.sock
    if operation.cancelled.is_set():
        operation.cancel()
        raise OSError('Request cancelled')
    response = connection.getresponse()
    if 300 <= response.status < 400:
        raise RelayError(502, 'upstream_redirect', 'Upstream redirects are disabled')
    return response


def _events(response):
    """Bounded SSE decoder supporting comments and multiline data."""
    parts, size = [], 0
    while True:
        line = response.readline(MAX_BODY + 1)
        if not line:
            if parts:
                raise RelayError(502, 'truncated_stream', 'Upstream event stream ended inside an event')
            return
        size += len(line)
        if size > MAX_BODY:
            raise RelayError(502, 'event_too_large', 'Upstream event exceeds the size limit')
        if line.strip():
            if line.startswith(b'data:'):
                parts.append(line[5:].strip())
            continue
        if parts:
            data = b'\n'.join(parts)
            if data != b'[DONE]':
                event = json.loads(data)
                if not isinstance(event, dict):
                    raise ValueError('Invalid stream event')
                yield event
        parts, size = [], 0


def _wire_events(response, protocol):
    if protocol is None:
        yield from _events(response)
    else:
        def chunks():
            while chunk := response.read1(16384):
                yield chunk
        yield from protocol.events(chunks())


def _adapt_events(adapter, event):
    for value in adapter.events(event) if adapter else [event]:
        if adapter:
            value = {**value, 'sequence_number': adapter.sequence}
            adapter.sequence += 1
        yield value


def _official_task_summary(handler, item, model, operation):
    """Ask only for a visible factual handoff, using original official auth."""
    allowed = {'authorization', 'chatgpt-account-id', 'openai-project', 'openai-organization', 'user-agent'}
    headers = [(k, v) for k, v in handler.headers.items() if k.lower() in allowed]
    headers += [('Content-Type', 'application/json'), ('Accept', 'text/event-stream'), ('Accept-Encoding', 'identity')]
    body = {'model': model, 'stream': True, 'store': False, 'tools': [], 'tool_choice': 'none',
        'parallel_tool_calls': False, 'reasoning': {'effort': 'low'},
        'text': {'format': {'type': 'text'}, 'verbosity': 'low'},
        'instructions': 'Create a concise factual handoff for another assistant. Include only the task goal, user constraints, '
            'work already completed, visible tool results, current state, and remaining work. Preserve exact task facts and identifiers. '
            'Exclude authentication secrets and bearer tokens. Do not provide hidden chain of thought, internal reasoning, '
            'or a decryption explanation. Do not call tools.',
        'input': [item, {'role': 'user', 'content': [{'type': 'input_text', 'text':
            'Summarize the visible task facts and remaining work from the preceding compacted conversation. '
            'Write a short standalone handoff using only facts already present in that conversation.'}]}]}
    fragments, completed = [], None
    try:
        response = _open(handler.gpt_url(), _json(body), headers, operation, handler.server.profile)
        if response.status != 200:
            raise LegacyHistoryError('legacy_summary_http_' + str(response.status),
                'The original official service rejected the task summary request')
        if 'text/event-stream' not in response.getheader('Content-Type', ''):
            raise LegacyHistoryError('legacy_summary_failed', 'The original official service could not summarize this history')
        size = 0
        for event in _events(response):
            if event.get('type') == 'response.output_text.delta':
                text = event.get('delta', '')
                if isinstance(text, str):
                    size += len(text.encode())
                    if size > 256_000:
                        raise LegacyHistoryError('legacy_summary_invalid', 'The official task summary exceeds the size limit')
                    fragments.append(text)
            if event.get('type') == 'response.completed':
                completed = event.get('response', {})
                break
            if event.get('type') in TERMINAL:
                raise LegacyHistoryError('legacy_summary_failed', 'The original official service could not summarize this history')
        if completed is None:
            raise LegacyHistoryError('legacy_summary_failed', 'The official task summary did not complete')
        text = '\n'.join(part.get('text', '') for output in completed.get('output', [])
            if isinstance(output, dict) and output.get('type') == 'message' for part in output.get('content', [])
            if isinstance(part, dict) and part.get('type') == 'output_text')
        return text or ''.join(fragments)
    except LegacyHistoryError:
        raise
    except Exception:
        raise LegacyHistoryError('legacy_summary_failed', 'The original official service could not summarize this history') from None
    finally:
        operation.close_connection()


def _legacy_request(handler, body, operation):
    profile = handler.server.profile
    items = body.get('input', [])
    if isinstance(items, list) and any(isinstance(v, dict) and v.get('type') == 'compaction_trigger' for v in items):
        raise RelayError(501, 'remote_compaction_unsupported', 'Remote compaction is unavailable for this model route')
    has_compaction = isinstance(items, list) and any(isinstance(v, dict) and v.get('type') == 'compaction' for v in items)
    try:
        body, protected, _ = _resolve_origin(handler.server, body)
        if not has_compaction:
            return visible_reasoning(body, protected_reasoning=protected)
        if not profile.get('legacy_history_dir') or not profile.get('summary_model'):
            raise RelayError(501, 'remote_compaction_unsupported', 'Legacy history conversion is not configured for this model route')
        converter = LegacyHistory(profile['legacy_history_dir'], profile['summary_model'],
            lambda item, model, headers, cancelled: _official_task_summary(handler, item, model, operation))
        return converter.convert(body, handler.headers, cancelled=operation.cancelled, protected_reasoning=protected)
    except ReasoningOriginError:
        raise RelayError(400, 'invalid_reasoning_origin', 'The historical reasoning origin container is invalid') from None
    except LegacyHistoryError as exc:
        raise RelayError(exc.status, exc.code, exc.message) from None
    except RelayError:
        raise
    except Exception:
        raise RelayError(503, 'legacy_cache_unavailable', 'The private legacy history cache is unavailable') from None


def _resolve_origin(server, body):
    try:
        origins = server.legacy_origin_index.get() if server.legacy_origin_index else {}
        return resolve_request(body, body['model'], legacy_origins=origins)
    except LegacyOriginIndexError:
        raise RelayError(503, 'legacy_origin_index_unavailable', 'The private reasoning origin index is unavailable or invalid') from None


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def create_server(profile, *, credential_reader=read_credential, session_factory=None):
    profile = validate_profile(copy.deepcopy(profile))
    server = Server(('127.0.0.1', profile['port']), Handler)
    server.profile, server.credential_reader = profile, credential_reader
    server.session_factory = session_factory or WebSocketSession
    server.legacy_origin_index = LegacyOriginIndex(profile['legacy_origins_file']) if profile.get('legacy_origins_file') else None
    server.active, server.last_activity = 0, time.monotonic()
    return server


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass

    def reply(self, status, value):
        raw = _json(value)
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        self.wfile.write(raw)
        self.wfile.flush()
        if status >= 400 and self.command == 'POST':
            self.drain_rejected_body()

    def drain_rejected_body(self):
        # A close with unread TCP input can discard the JSON reply as a reset
        # on Windows. Send its FIN first, then consume only a bounded remainder.
        lengths = self.headers.get_all('Content-Length', [])
        try:
            remaining = int(lengths[0]) - getattr(self, 'body_bytes_read', 0) if len(lengths) == 1 else 65536
        except ValueError:
            remaining = 65536
        remaining = min(max(remaining, 0), MAX_BODY)
        if not remaining:
            return
        timeout = self.connection.gettimeout()
        deadline = time.monotonic() + .25
        try:
            self.connection.shutdown(socket.SHUT_WR)
            while remaining > 0:
                wait = deadline - time.monotonic()
                if wait <= 0:
                    break
                self.connection.settimeout(wait)
                chunk = self.rfile.read1(min(16384, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass
        finally:
            self.connection.settimeout(timeout)

    def boundary(self):
        hosts = self.headers.get_all('Host', [])
        origins = self.headers.get_all('Origin', [])
        try:
            host = urlsplit('//' + (hosts[0] if len(hosts) == 1 else ''))
            if not _loopback(host.hostname) or host.username or host.path or host.port != self.server.server_port:
                raise ValueError()
            if origins:
                origin = urlsplit(origins[0])
                if (len(origins) != 1 or origin.scheme != 'http' or not _loopback(origin.hostname)
                        or origin.port != self.server.server_port or origin.username or origin.path or origin.query or origin.fragment):
                    raise ValueError()
        except (ValueError, TypeError):
            raise RelayError(403, 'local_boundary', 'This endpoint accepts local clients only') from None
        path = self.path.split('?', 1)[0]
        components = path.split('/', 2)
        candidate = components[1] if len(components) > 1 else ''
        if not hmac.compare_digest(candidate.encode('utf-8'), self.server.profile['token'].encode('ascii')):
            raise RelayError(401, 'unauthorized', 'Invalid local endpoint')
        return '/' + components[2] if len(components) > 2 else '/'

    def do_GET(self):
        upgraded = False
        try:
            endpoint = self.boundary()
            if endpoint == '/health':
                p = self.server.profile
                self.reply(200, {'ok': True, 'identity': p['identity'], 'edition': p['edition']})
            elif endpoint == '/backend-api/codex/responses' and self.headers.get('Upgrade', '').lower() == 'websocket':
                self.server.active += 1
                upstream = None
                try:
                    sticky = None
                    model = self.handshake_model()
                    if model and model not in self.server.profile['routes'] and _is_official_model(self.server.profile, model):
                        url = self.gpt_url().replace('https://', 'wss://', 1).replace('http://', 'ws://', 1)
                        try:
                            upstream, sticky = connect_websocket(url, self.headers, timeout=15,
                                proxy_url=network_proxy(url, self.server.profile))
                        except (OSError, ValueError):
                            raise RelayError(502, 'upstream_unavailable', 'The official model connection is unavailable') from None
                        upstream.sock.settimeout(120)
                    client = accept_websocket(self, response_headers=sticky)
                    upgraded = True
                    self.server.session_factory(self, client, upstream=upstream).run()
                finally:
                    if upstream:
                        upstream.close()
                    self.server.active -= 1
                    self.server.last_activity = time.monotonic()
            else:
                raise RelayError(404, 'unsupported_endpoint', 'Unsupported local endpoint')
        except RelayError as exc:
            self.reply(exc.status, _error(exc.code, exc.message))
        except WebSocketError:
            self.reply(400, _error('invalid_upgrade', 'Invalid WebSocket upgrade'))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError:
            if not upgraded:
                self.reply(502, _error('upstream_unavailable', 'The official model connection is unavailable'))

    def handshake_model(self):
        models = []
        for value in self.headers.get_all('x-codex-turn-metadata', []):
            try:
                model = json.loads(value).get('model')
                if isinstance(model, str):
                    models.append(model)
            except (ValueError, AttributeError):
                pass
        for value in self.headers.get_all('x-codex-routing-hint', []):
            match = re.search(r'(?:model=|"model"\s*:\s*")([a-zA-Z0-9._-]+)', value)
            if match:
                models.append(match.group(1))
        return models[0] if models and len(set(models)) == 1 else None

    def do_POST(self):
        started = False
        forwarded = False
        sent = False
        operation = Operation()
        stop = threading.Event()
        try:
            endpoint = self.boundary()
            if endpoint.endswith('/responses/compact'):
                raise RelayError(501, 'remote_compaction_unsupported', 'Remote compaction is unavailable at this endpoint')
            if endpoint != '/backend-api/codex/responses':
                raise RelayError(404, 'unsupported_endpoint', 'Unsupported local endpoint')
            if self.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                raise RelayError(415, 'unsupported_encoding', 'Use an uncompressed JSON request')
            lengths = self.headers.get_all('Content-Length', [])
            if self.headers.get('Transfer-Encoding') or len(lengths) != 1:
                raise RelayError(400, 'invalid_length', 'A single Content-Length is required')
            try:
                length = int(lengths[0])
            except ValueError:
                raise RelayError(400, 'invalid_length', 'Invalid Content-Length') from None
            if not 0 < length <= MAX_BODY:
                raise RelayError(413, 'request_too_large', 'Request exceeds the size limit')
            raw = self.rfile.read(length)
            self.body_bytes_read = len(raw)
            if len(raw) != length:
                raise RelayError(400, 'truncated_body', 'Incomplete request body')
            body = json.loads(raw)
            _check_body(body)
            _check_model(self.server.profile, body['model'])
            self.server.active += 1
            started = True
            self.watch_disconnect(operation, stop)
            route = self.server.profile['routes'].get(body['model'])
            adapter = None
            protocol = None
            if route:
                if body.get('previous_response_id'):
                    raise RelayError(400, 'previous_response_not_found', 'Incremental continuation requires the same WebSocket connection')
                body = _legacy_request(self, body, operation)
                body, adapter = prepare_request(body, route)
                wire_body, protocol = _protocol_request(body, route)
                raw = _json(wire_body)
                url = _route_url(route)
                headers = _route_headers(self.server, route, stream=bool(body.get('stream')))
            else:
                body, _, changed = _resolve_origin(self.server, body)
                if changed:
                    raw = _json(body)
                url = self.gpt_url()
                headers = sanitize_headers(self.headers)
            forwarded = True
            response = _open(url, raw, headers, operation, self.server.profile)
            response_headers = sanitize_headers(response.getheaders(), request=False)
            content_type = response.getheader('Content-Type', '')
            if route and 'text/event-stream' not in content_type:
                data = response.read(MAX_BODY + 1)
                if len(data) > MAX_BODY:
                    raise RelayError(502, 'response_too_large', 'Upstream response exceeds the size limit')
                payload = data
                if response.status < 400:
                    value = json.loads(data)
                    value = protocol.response(value) if protocol else value
                    value = adapter.response(value) if adapter else value
                    payload = _json(stamp_response(value, body['model']))
                self.send_response(response.status)
                for name, value in response_headers:
                    self.send_header(name, value)
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Connection', 'close')
                self.end_headers()
                sent = True
                self.close_connection = True
                self.wfile.write(payload)
            else:
                self.send_response(response.status)
                for name, value in response_headers:
                    self.send_header(name, value)
                self.send_header('Connection', 'close')
                self.end_headers()
                sent = True
                self.close_connection = True
                if route and response.status < 400:
                    for event in _wire_events(response, protocol):
                        for converted in _adapt_events(adapter, event):
                            converted = stamp_event(converted, body['model'])
                            self.wfile.write(b'event: ' + converted['type'].encode() + b'\ndata: ' + _json(converted) + b'\n\n')
                            self.wfile.flush()
                else:
                    while chunk := response.read1(16384):
                        self.wfile.write(chunk)
                        self.wfile.flush()
        except RelayError as exc:
            if not sent:
                self.reply(exc.status, _error(exc.code, exc.message))
        except (ValueError, TypeError, KeyError):
            if not sent:
                self.reply(502 if forwarded else 400, _error('invalid_upstream_response' if forwarded else 'invalid_request',
                    'Invalid upstream response' if forwarded else 'Invalid model request'))
        except Exception:
            if not sent:
                self.reply(502, _error('upstream_unavailable', 'The configured model service is unavailable'))
        finally:
            stop.set()
            operation.cancel()
            if started:
                self.server.active -= 1
                self.server.last_activity = time.monotonic()

    def watch_disconnect(self, operation, stop):
        def disconnected():
            while not stop.wait(.1):
                try:
                    readable, _, _ = select.select([self.connection], [], [], .1)
                    if readable:
                        if not self.connection.recv(1, socket.MSG_PEEK):
                            operation.cancel()
                        return
                except OSError:
                    return
        threading.Thread(target=disconnected, daemon=True).start()

    def gpt_url(self):
        key = 'chatgpt_base_url' if self.headers.get('ChatGPT-Account-ID') else 'api_base_url'
        return _endpoint(self.server.profile['gpt'][key])


def _check_body(body):
    if not isinstance(body, dict) or not isinstance(body.get('model'), str) or not body['model']:
        raise RelayError(400, 'invalid_request', 'A model name is required')


def _is_official_model(profile, model):
    # Older private profiles predate the native catalog registration boundary.
    return 'official_models' not in profile or model in profile['official_models']


def _check_model(profile, model):
    if model not in profile['routes'] and not _is_official_model(profile, model):
        raise RelayError(400, 'model_unavailable', 'This model is unavailable. Select an available model and try again.')


class WebSocketSession:
    def __init__(self, handler, client, upstream=None):
        self.handler, self.client = handler, client
        self.queue = queue.Queue(maxsize=32)
        self.closed = threading.Event()
        self.operation = None
        self.upstream = upstream
        self.upstream_lock = threading.Lock()
        self.request_id = 0
        self.active = False
        self.mode = None
        self.history = None
        self.pending_input = None

    def put(self, event):
        while not self.closed.is_set():
            try:
                self.queue.put(event, timeout=.1)
                return
            except queue.Full:
                pass

    def reader(self):
        try:
            while not self.closed.is_set():
                message = self.client.recv()
                self.put(('client', message))
                if message is None:
                    return
        except Exception:
            self.put(('client', None))

    def fail(self, code, message):
        self.client.send(_json(_error(code, message)))

    def run(self):
        if self.upstream:
            threading.Thread(target=self.gpt_reader, args=(self.upstream,), daemon=True).start()
        threading.Thread(target=self.reader, daemon=True).start()
        try:
            while True:
                event = self.queue.get()
                if event[0] == 'client':
                    if event[1] is None:
                        return
                    self.receive(*event[1])
                elif event[0] == 'upstream':
                    _, request_id, message = event
                    if request_id == 'gpt' and self.mode != 'gpt':
                        continue
                    if request_id == 'gpt' or request_id == self.request_id:
                        opcode, payload = message
                        self.client.send(payload, opcode)
                        if opcode == 1:
                            try:
                                value = json.loads(payload)
                            except ValueError:
                                continue
                            if not isinstance(value, dict):
                                continue
                            if value.get('type') in TERMINAL:
                                self.active = False
                                if request_id != 'gpt' and value['type'] == 'response.completed':
                                    response = value.get('response', {})
                                    self.history = {'model': self.pending_input['model'], 'id': response.get('id'),
                                                    'input': self.pending_input.get('input', []), 'output': response.get('output', [])}
                elif event[0] == 'failure':
                    _, request_id, code, message = event
                    if request_id == self.request_id or request_id == 'gpt' and self.active and self.mode == 'gpt':
                        self.fail(code, message)
                        self.active = False
                elif event[0] == 'finished' and event[1] == self.request_id and self.active:
                    self.fail('truncated_stream', 'Upstream stream ended before a terminal response')
                    self.active = False
        except (OSError, WebSocketError):
            pass
        finally:
            self.closed.set()
            if self.operation:
                self.operation.cancel()
            with self.upstream_lock:
                if self.upstream:
                    self.upstream.close()
            self.client.close()

    def receive(self, opcode, payload):
        if opcode == 2:
            if self.mode == 'gpt' and self.upstream:
                self.upstream.send(payload, 2)
            else:
                self.fail('invalid_request', 'A text response.create event is required')
            return
        try:
            body = json.loads(payload)
            if not isinstance(body, dict):
                raise ValueError()
            if body.get('type') in ('response.cancel', 'response.steer', 'response.interrupt'):
                if self.mode == 'gpt' and self.upstream:
                    self.upstream.send(payload)
                elif body['type'] == 'response.cancel':
                    if self.operation:
                        self.operation.cancel()
                    self.request_id += 1
                    self.active = False
                    self.fail('request_cancelled', 'The model request was cancelled')
                else:
                    self.fail('unsupported_event', 'This event requires the official model transport')
                return
            if body.get('type') != 'response.create':
                raise RelayError(400, 'unsupported_event', 'Expected response.create')
            if self.active:
                raise RelayError(409, 'response_in_progress', 'A response is already in progress')
            _check_body(body)
            _check_model(self.handler.server.profile, body['model'])
            route = self.handler.server.profile['routes'].get(body['model'])
            if route:
                previous = body.get('previous_response_id')
                if previous:
                    history = self.history
                    if not history or previous != history['id'] or body['model'] != history['model']:
                        raise RelayError(400, 'previous_response_not_found', 'Send the complete input when changing model or starting a new connection')
                    if not isinstance(body.get('input', []), list) or not isinstance(history['input'], list):
                        raise RelayError(400, 'invalid_input', 'Incremental continuation requires an input array')
                    body['input'] = history['input'] + history['output'] + body.get('input', [])
                if body.get('generate') is False:
                    self.mode = 'route'
                    self.request_id += 1
                    response = {'id': 'local_warmup_' + str(self.request_id), 'object': 'response', 'status': 'completed',
                                'model': body['model'], 'output': [], 'usage': {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}}
                    self.history = {'model': body['model'], 'id': response['id'], 'input': body.get('input', []), 'output': []}
                    self.client.send(_json({'type': 'response.completed', 'response': response}))
                    return
                self.pending_input = copy.deepcopy(body)
                if len(_json(body)) > MAX_BODY:
                    raise RelayError(413, 'request_too_large', 'Reconstructed input exceeds the size limit')
                self.mode = 'route'
                self.operation = Operation()
                self.request_id += 1
                self.active = True
                threading.Thread(target=self.route_worker, args=(self.request_id, body, route, self.operation), daemon=True).start()
            else:
                body, _, changed = _resolve_origin(self.handler.server, body)
                if changed:
                    payload = _json(body)
                self.mode, self.active = 'gpt', True
                self.history = None
                threading.Thread(target=self.gpt_send, args=(payload,), daemon=True).start()
        except RelayError as exc:
            self.fail(exc.code, exc.message)
        except (ValueError, TypeError, KeyError):
            self.fail('invalid_request', 'Invalid response.create event')

    def route_worker(self, request_id, body, route, operation):
        try:
            body = _legacy_request(self.handler, body, operation)
            body, adapter = prepare_request(body, route)
            body['stream'] = True
            wire_body, protocol = _protocol_request(body, route)
            if len(_json(wire_body)) > MAX_BODY:
                raise RelayError(413, 'request_too_large', 'Reconstructed input exceeds the size limit')
            headers = _route_headers(self.handler.server, route, stream=True)
            response = _open(_route_url(route), _json(wire_body), headers, operation, self.handler.server.profile)
            if response.status >= 400:
                raise RelayError(response.status, 'upstream_http_' + str(response.status), 'The model service rejected the request')
            if 'text/event-stream' not in response.getheader('Content-Type', ''):
                raise RelayError(502, 'invalid_stream', 'The model service did not return an event stream')
            for event in _wire_events(response, protocol):
                if operation.cancelled.is_set():
                    return
                for value in _adapt_events(adapter, event):
                    value = stamp_event(value, body['model'])
                    self.put(('upstream', request_id, (1, _json(value))))
                if event.get('type') in TERMINAL:
                    return
            self.put(('finished', request_id))
        except RelayError as exc:
            self.put(('failure', request_id, exc.code, exc.message))
        except Exception:
            if not operation.cancelled.is_set():
                self.put(('failure', request_id, 'upstream_unavailable', 'The model service is unavailable'))
        finally:
            operation.cancel()

    def gpt_send(self, payload):
        try:
            with self.upstream_lock:
                if not self.upstream:
                    url = self.handler.gpt_url().replace('https://', 'wss://', 1).replace('http://', 'ws://', 1)
                    upstream, _ = connect_websocket(url, self.handler.headers, timeout=15,
                        proxy_url=network_proxy(url, self.handler.server.profile))
                    upstream.sock.settimeout(120)
                    if self.closed.is_set():
                        upstream.close()
                        return
                    self.upstream = upstream
                    # Handshake sticky headers are not mirrored after local 101.
                    threading.Thread(target=self.gpt_reader, args=(upstream,), daemon=True).start()
                self.upstream.send(payload)
        except Exception:
            self.put(('failure', 'gpt', 'upstream_unavailable', 'The official model connection is unavailable'))

    def gpt_reader(self, upstream):
        try:
            while not self.closed.is_set():
                message = upstream.recv()
                if message is None:
                    break
                self.put(('upstream', 'gpt', message))
        except Exception:
            pass
        finally:
            with self.upstream_lock:
                if self.upstream is upstream:
                    self.upstream = None
            upstream.close()
            self.put(('failure', 'gpt', 'upstream_disconnected', 'The official model connection closed'))


def serve(profile_path):
    server = create_server(load_profile(profile_path))
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['serve'])
    parser.add_argument('profile', type=Path)
    args = parser.parse_args()
    try:
        serve(args.profile)
    except Exception:
        print('Local inference adapter could not start', file=sys.stderr)
        raise SystemExit(1)
