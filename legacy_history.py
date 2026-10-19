"""Lazy, account-scoped readable handoff cache for official compaction items.

Only the official service receives opaque compaction data. The cache stores its
visible task summary, never credentials, ciphertext, or original conversation.
"""
from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from reasoning_origin import public_reasoning_message, reasoning_key


class LegacyHistoryError(Exception):
    def __init__(self, code, message, status=502):
        self.code, self.message, self.status = code, message, status


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def _cache_file(path):
    return path.suffix == '.json' and len(path.stem) == 64 and all(c in '0123456789abcdef' for c in path.stem)


def account_scope(headers):
    headers = {k.lower(): v for k, v in headers.items()}
    authorization = headers.get('authorization', '')
    if not authorization.startswith('Bearer ') or not authorization[7:].strip():
        raise LegacyHistoryError('legacy_official_auth_required', 'The original official account authorization is required', 401)
    account = headers.get('chatgpt-account-id')
    if account:
        try:
            payload = authorization[7:].split('.')[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + '==='))
            user = (claims.get('https://api.openai.com/auth') or {}).get('chatgpt_user_id') or claims.get('sub')
            if not isinstance(user, str) or not user:
                raise ValueError()
        except (ValueError, IndexError, TypeError, AttributeError):
            user = hashlib.sha256(authorization.encode()).hexdigest()
        return _json(['chatgpt', account, user])
    return _json(['api', hashlib.sha256(authorization.encode()).hexdigest(),
                  headers.get('openai-project'), headers.get('openai-organization')])


def visible_reasoning(body, *, protected_reasoning=()):
    """Keep already public reasoning summaries; never send opaque reasoning on."""
    result = copy.deepcopy(body)
    items = result.get('input')
    if not isinstance(items, list):
        return result
    retained = []
    for item in items:
        if not isinstance(item, dict) or item.get('type') == 'compaction':
            retained.append(item)
            continue
        if item.get('type') == 'reasoning' and reasoning_key(item) in protected_reasoning:
            retained.append(item)
            continue
        if item.get('type') != 'reasoning':
            if 'encrypted_content' in item:
                raise LegacyHistoryError('legacy_reasoning_unsupported',
                    'This historical encrypted item requires the original official model', 501)
            retained.append(item)
            continue
        message = public_reasoning_message(item)
        if message is not None:
            retained.append(message)
    result['input'] = retained
    return result


class LegacyHistory:
    def __init__(self, directory, summary_model, summarizer, *, max_bytes=32 * 1024 * 1024,
                 max_entries=512, max_age=90 * 86400, max_summary_bytes=256_000, clock=time.time):
        self.directory = Path(directory)
        if not self.directory.is_absolute() or self.directory.is_symlink():
            raise ValueError('Legacy history cache requires an absolute private directory')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        marker = self.directory / '.legacy-history-v1'
        if marker.is_symlink():
            raise ValueError('Invalid legacy cache ownership marker')
        if marker.exists():
            if marker.read_bytes() != b'codex-legacy-history-v1\n':
                raise ValueError('Invalid legacy cache ownership marker')
        else:
            if any(not (_cache_file(path) or path.name in ('.conversion.lock', '.legacy-history-v1')
                        or path.name.startswith(('.summary-', '.legacy-init-'))) for path in self.directory.iterdir()):
                raise ValueError('Legacy cache directory contains unrelated files')
            fd, temporary = tempfile.mkstemp(prefix='.legacy-init-', dir=self.directory)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(b'codex-legacy-history-v1\n')
                try:
                    os.link(temporary, marker)
                except FileExistsError:
                    if marker.is_symlink() or marker.read_bytes() != b'codex-legacy-history-v1\n':
                        raise ValueError('Invalid legacy cache ownership marker') from None
            finally:
                Path(temporary).unlink(missing_ok=True)
        self.directory.chmod(0o700)
        self.summary_model, self.summarizer = summary_model, summarizer
        self.max_bytes, self.max_entries, self.max_age = max_bytes, max_entries, max_age
        self.max_summary_bytes, self.clock = max_summary_bytes, clock

    @contextlib.contextmanager
    def _lock(self, cancelled):
        path = self.directory / '.conversion.lock'
        if path.is_symlink():
            raise LegacyHistoryError('legacy_cache_unavailable', 'The private history cache is unavailable')
        flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(path, flags, 0o600)
        locked = False
        try:
            os.chmod(path, 0o600)
            # CRT byte locks may extend past EOF. Do not initialize a byte:
            # another handle may already own that range while this file is empty.
            deadline = time.monotonic() + 150
            while not locked:
                if cancelled.is_set():
                    raise LegacyHistoryError('request_cancelled', 'The model request was cancelled', 499)
                try:
                    if os.name == 'nt':
                        import msvcrt
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except (BlockingIOError, PermissionError):
                    if time.monotonic() >= deadline:
                        raise LegacyHistoryError('legacy_cache_busy', 'Another legacy history conversion is still running') from None
                    cancelled.wait(.05)
            yield
        finally:
            try:
                if locked:
                    if os.name == 'nt':
                        import msvcrt
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _read(self, path, *, matching_model=True):
        try:
            if path.is_symlink() or path.stat().st_size > self.max_summary_bytes + 4096:
                return None
            value = json.loads(path.read_text(encoding='utf-8'))
            summary = value.get('summary')
            if (value.get('format') != 1 or not isinstance(value.get('summary_model'), str)
                    or matching_model and value['summary_model'] != self.summary_model
                    or not isinstance(summary, str) or not summary.strip()
                    or len(summary.encode()) > self.max_summary_bytes
                    or not isinstance(value.get('created_at'), (int, float))
                    or not 0 <= self.clock() - value['created_at'] <= self.max_age):
                return None
            return summary
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def _prune(self):
        retained = []
        for path in self.directory.glob('*.json'):
            if not _cache_file(path):
                continue
            if self._read(path, matching_model=False) is None:
                path.unlink(missing_ok=True)
            else:
                stat = path.stat()
                retained.append((stat.st_mtime, path, stat.st_size))
        total = sum(v[2] for v in retained)
        retained.sort()
        while retained and (len(retained) > self.max_entries or total > self.max_bytes):
            _, path, size = retained.pop(0)
            path.unlink(missing_ok=True)
            total -= size
        for path in self.directory.glob('.summary-*.tmp'):
            path.unlink(missing_ok=True)

    def _write(self, path, summary):
        raw = _json({'format': 1, 'summary_model': self.summary_model, 'created_at': self.clock(), 'summary': summary})
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix='.summary-', suffix='.tmp', dir=self.directory)
            with os.fdopen(fd, 'wb') as stream:
                os.chmod(temporary, 0o600)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)

    def convert(self, body, headers, *, cancelled=None, protected_reasoning=()):
        result = visible_reasoning(body, protected_reasoning=protected_reasoning)
        items = result.get('input')
        if not isinstance(items, list):
            return result
        opaque = [v for v in items if isinstance(v, dict) and v.get('type') == 'compaction']
        if not opaque:
            return result
        scope, cancelled = account_scope(headers), cancelled or threading.Event()
        for index, item in enumerate(items):
            if not isinstance(item, dict) or item.get('type') != 'compaction':
                continue
            if not isinstance(item.get('encrypted_content'), str) or not item['encrypted_content']:
                raise LegacyHistoryError('legacy_compaction_invalid', 'The historical compaction item is incomplete', 400)
            key = hashlib.sha256(scope + b'\0' + _json(item) + b'\0' + self.summary_model.encode()).hexdigest()
            path = self.directory / (key + '.json')
            with self._lock(cancelled):
                self._prune()
                summary = self._read(path)
                if summary is None:
                    summary = self.summarizer(copy.deepcopy(item), self.summary_model, headers, cancelled)
                    if cancelled.is_set():
                        raise LegacyHistoryError('request_cancelled', 'The model request was cancelled', 499)
                    if not isinstance(summary, str) or not summary.strip() or len(summary.encode()) > self.max_summary_bytes:
                        raise LegacyHistoryError('legacy_summary_invalid', 'The official service did not return a usable task summary')
                    authorization = next((v for k, v in headers.items() if k.lower() == 'authorization'), '')
                    if authorization[7:].strip() in summary:
                        raise LegacyHistoryError('legacy_summary_invalid', 'The task summary contains authentication data')
                    summary = summary.strip()
                    self._write(path, summary)
                    self._prune()
            items[index] = {'role': 'assistant', 'content': [{'type': 'output_text', 'text':
                'Earlier task facts recovered from the original official conversation:\n' + summary}]}
        return result
