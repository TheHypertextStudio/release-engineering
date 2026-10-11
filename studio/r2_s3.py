"""Bounded R2 PUT/GET using an account-owned token, without stored S3 keys."""
from datetime import datetime, timezone
import hashlib
import hmac
import http.client
import json
import os
import re
import socket
import ssl
import threading
import time
from urllib.parse import quote

MAX_OBJECT_BYTES = 64 * 1024 * 1024
MAX_CACHE_BYTES = 512 * 1024 * 1024
MAX_CACHE_ENTRIES = 10_000
MAX_METADATA_BYTES = 64 * 1024
REQUEST_SECONDS = 90
SOCKET_SECONDS = 30


class R2TransportError(RuntimeError):
    """Safe diagnostics never include headers, credentials, or response bodies."""

    def __init__(self, message, *, retryable=False, status=None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class R2ReadbackMismatch(R2TransportError):
    pass


def endpoint(account, jurisdiction=None):
    if not isinstance(account, str) or not re.fullmatch(r'[a-f0-9]{32}', account):
        raise ValueError('R2 S3 requires a reviewed account identity')
    if jurisdiction not in (None, 'default', 'eu', 'us', 'fedramp'):
        raise ValueError('R2 S3 jurisdiction is not supported by this runtime')
    suffix = '' if jurisdiction in (None, 'default') else '.' + jurisdiction
    return account + suffix + '.r2.cloudflarestorage.com'


def _path(bucket, key):
    if not isinstance(bucket, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', bucket):
        raise ValueError('R2 S3 requires an explicit bucket identity')
    if (not isinstance(key, str) or not key or len(key.encode()) > 1024
            or any(part in ('', '.', '..') for part in key.split('/'))
            or '\\' in key or any(ord(char) < 32 or ord(char) == 127 for char in key)):
        raise ValueError('R2 S3 requires a safe bounded object key')
    return '/' + bucket + '/' + quote(key, safe='/-_.~')


def _headers(method, host, path, payload_sha256, access_key_id, secret, now):
    """SigV4 for the fixed, query-free R2 object endpoint; sign the raw payload."""
    timestamp = now.strftime('%Y%m%dT%H%M%SZ')
    day = now.strftime('%Y%m%d')
    scope = day + '/auto/s3/aws4_request'
    signed = 'host;x-amz-content-sha256;x-amz-date'
    canonical_headers = f'host:{host}\nx-amz-content-sha256:{payload_sha256}\nx-amz-date:{timestamp}\n'
    if method == 'PUT':
        signed = 'content-type;' + signed
        canonical_headers = 'content-type:application/octet-stream\n' + canonical_headers
    canonical = '\n'.join((method, path, '', canonical_headers, signed, payload_sha256))
    to_sign = '\n'.join(('AWS4-HMAC-SHA256', timestamp, scope,
                         hashlib.sha256(canonical.encode()).hexdigest()))
    signing_key = ('AWS4' + secret).encode()
    for value in (day, 'auto', 's3', 'aws4_request'):
        signing_key = hmac.new(signing_key, value.encode(), hashlib.sha256).digest()
    signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
    return {'Host': host, 'x-amz-date': timestamp, 'x-amz-content-sha256': payload_sha256,
            'Authorization': f'AWS4-HMAC-SHA256 Credential={access_key_id}/{scope}, SignedHeaders={signed}, Signature={signature}',
            'Accept-Encoding': 'identity'}


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise R2TransportError('R2 request deadline exceeded', retryable=True)
    return min(SOCKET_SECONDS, remaining)


def _read(transport_socket, response, count, deadline):
    timeout = _remaining(deadline)
    if response.isclosed():
        return b''
    transport_socket.settimeout(timeout)
    # read1 returns after one underlying read, so slow chunked delivery cannot
    # postpone the deadline check until an entire requested block arrives.
    data = response.read1(count)
    _remaining(deadline)
    return data


def _exchange(host, method, path, headers, body, consume, *, before_send=None):
    deadline = time.monotonic() + REQUEST_SECONDS
    connection = http.client.HTTPSConnection(host, timeout=SOCKET_SECONDS,
                                             context=ssl.create_default_context())
    response = None
    timer = None
    try:
        connection.connect()
        transport_socket = connection.sock

        def expire():
            try:
                transport_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        # Bound header parsing and writes too, including slow peer delivery.
        _remaining(deadline)
        timer = threading.Timer(max(0, deadline - time.monotonic()), expire)
        timer.daemon = True
        timer.start()
        if before_send is not None:
            before_send()
        transport_socket.settimeout(_remaining(deadline))
        connection.request(method, path, body=body, headers=headers)
        transport_socket.settimeout(_remaining(deadline))
        response = connection.getresponse()
        if response.status != 200:
            status = response.status
            raise R2TransportError(f'R2 {method} rejected (HTTP {status})',
                                   retryable=status in (408, 429) or 500 <= status <= 599,
                                   status=status)
        length = response.getheader('Content-Length')
        transfer = response.getheader('Transfer-Encoding')
        if (length is not None and (not re.fullmatch(r'[0-9]{1,20}', length)
                                    or int(length) > MAX_OBJECT_BYTES)
                or transfer is not None and transfer.strip().lower() != 'chunked'
                or length is not None and transfer is not None):
            raise R2TransportError('R2 returned malformed HTTP framing')
        encoding = response.getheader('Content-Encoding', 'identity').strip().lower()
        if encoding not in ('', 'identity'):
            raise R2TransportError('R2 returned an unexpected content encoding')
        return consume(transport_socket, response, deadline)
    except R2TransportError:
        raise
    except (OSError, http.client.RemoteDisconnected):
        raise R2TransportError('R2 request did not complete', retryable=True) from None
    except http.client.HTTPException:
        if time.monotonic() >= deadline:
            raise R2TransportError('R2 request deadline exceeded', retryable=True) from None
        raise R2TransportError('R2 returned malformed HTTP framing') from None
    finally:
        if timer is not None:
            timer.cancel()
        if response is not None:
            response.close()
        connection.close()


def _bounded_body(transport_socket, response, deadline):
    body = bytearray()
    while block := _read(transport_socket, response, MAX_METADATA_BYTES - len(body) + 1, deadline):
        body.extend(block)
        if len(body) > MAX_METADATA_BYTES:
            raise R2TransportError('R2 metadata response exceeds bound')
    length = response.getheader('Content-Length')
    if length is not None and int(length) != len(body):
        raise R2TransportError('R2 metadata response is truncated')
    return bytes(body)


def _date(value):
    if not isinstance(value, str):
        raise R2TransportError('Account token time metadata is malformed')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise R2TransportError('Account token time metadata is malformed') from None
    if parsed.tzinfo is None:
        raise R2TransportError('Account token time metadata requires a timezone')
    return parsed


class R2S3Client:
    def __init__(self, account, jurisdiction=None, *, token=None):
        self.host = endpoint(account, jurisdiction)
        token = os.environ.get('CLOUDFLARE_API_TOKEN') if token is None else token
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', token):
            raise ValueError('An account-owned CLOUDFLARE_API_TOKEN is required for S3 seeding')
        raw = _exchange('api.cloudflare.com', 'GET', f'/client/v4/accounts/{account}/tokens/verify',
                        {'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
                         'Accept-Encoding': 'identity'}, None, _bounded_body)
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError('invalid verification response')
            record = payload['result']
            if (payload.get('success') is not True or not isinstance(record, dict)
                    or not re.fullmatch(r'[a-f0-9]{32}', str(record.get('id', '')))
                    or record.get('status') != 'active'):
                raise ValueError('invalid verification response')
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise R2TransportError('Account-owned token verification failed') from None
        now = datetime.now(timezone.utc)
        if (record.get('not_before') is not None and _date(record['not_before']) > now
                or record.get('expires_on') is not None and _date(record['expires_on']) <= now):
            raise R2TransportError('Account-owned token is outside its valid time interval')
        self._access_key_id = record['id']
        self._secret = hashlib.sha256(token.encode()).hexdigest()
        self.credential_receipt = {'kind': 'account-owned', 'account_id': account,
                                   'token_id': record['id'], 'status': 'active',
                                   'verified_at': now.isoformat(),
                                   'not_before': record.get('not_before'),
                                   'expires_on': record.get('expires_on')}

    def put(self, bucket, key, source, expected_size, expected_sha256, *, authorize=None):
        if not 0 <= expected_size <= MAX_OBJECT_BYTES:
            raise ValueError('R2 object exceeds retained cache bound')
        with source.open('rb') as file:
            data = file.read(expected_size + 1)
        if len(data) != expected_size or hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ValueError('OpenNext cache source changed before S3 upload')
        path = _path(bucket, key)
        headers = _headers('PUT', self.host, path, expected_sha256, self._access_key_id,
                           self._secret, datetime.now(timezone.utc))
        headers.update({'Content-Type': 'application/octet-stream', 'Content-Length': str(len(data))})
        _exchange(self.host, 'PUT', path, headers, data, _bounded_body, before_send=authorize)

    def verify(self, bucket, key, expected_size, expected_sha256):
        if not 0 <= expected_size <= MAX_OBJECT_BYTES:
            raise ValueError('R2 object exceeds retained cache bound')
        path = _path(bucket, key)
        headers = _headers('GET', self.host, path, hashlib.sha256(b'').hexdigest(),
                           self._access_key_id, self._secret, datetime.now(timezone.utc))

        def consume(connection, response, deadline):
            length = response.getheader('Content-Length')
            if length is not None and length != str(expected_size):
                raise R2ReadbackMismatch('OpenNext cache readback length mismatch')
            digest = hashlib.sha256()
            received = 0
            while True:
                block = _read(connection, response, min(64 * 1024, expected_size - received + 1), deadline)
                if not block:
                    break
                received += len(block)
                if received > expected_size:
                    raise R2ReadbackMismatch('OpenNext cache readback exceeds retained length')
                digest.update(block)
            if received != expected_size or digest.hexdigest() != expected_sha256:
                raise R2ReadbackMismatch('OpenNext cache readback digest mismatch')

        _exchange(self.host, 'GET', path, headers, None, consume)
