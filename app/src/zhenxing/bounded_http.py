"""Bounded synchronous model HTTP, shared by cloud adapters.

Caller deadlines do not claim to cancel a request already received by a remote
server. A late in-flight call retains its capacity slot until it actually ends;
its result is discarded. This bounds daemon I/O, not kills Python threads.
"""
import math
from collections.abc import Mapping
import threading
import time
import zlib

import httpx


_SAFE_TRANSPORT_CODES = frozenset({'HTTP_BOUND_INVALID','HTTP_TRANSPORT_CONFLICT','HTTP_HEADERS_INVALID',
    'HTTP_CAPACITY_EXHAUSTED','HTTP_CAPACITY_UNAVAILABLE','HTTP_OUTCOME_UNKNOWN_DEADLINE','HTTP_TIMEOUT',
    'HTTP_TRANSPORT_FAILED','HTTP_RESPONSE_TOO_LARGE','HTTP_ENCODING_UNSUPPORTED','HTTP_ENCODING_INVALID',
    'HTTP_CONTENT_LENGTH_INVALID'})
_SAFE_FAILURE_KINDS = frozenset({'HTTP_STATUS','CONNECT_TIMEOUT','READ_TIMEOUT','WRITE_TIMEOUT','POOL_TIMEOUT',
    'DEADLINE_EXCEEDED','CONNECT_FAILURE','READ_FAILURE','WRITE_FAILURE','PROTOCOL_FAILURE','TRANSPORT_FAILURE',
    'CAPACITY','RESPONSE_BOUND','ENCODING','LOCAL_INPUT','TIMEOUT','INTERNAL_ERROR'})
_CODE_KIND = {'HTTP_OUTCOME_UNKNOWN_DEADLINE':'DEADLINE_EXCEEDED','HTTP_TIMEOUT':'TIMEOUT',
    'HTTP_TRANSPORT_FAILED':'TRANSPORT_FAILURE','HTTP_CAPACITY_EXHAUSTED':'CAPACITY',
    'HTTP_CAPACITY_UNAVAILABLE':'CAPACITY','HTTP_RESPONSE_TOO_LARGE':'RESPONSE_BOUND',
    'HTTP_ENCODING_UNSUPPORTED':'ENCODING','HTTP_ENCODING_INVALID':'ENCODING','HTTP_CONTENT_LENGTH_INVALID':'ENCODING'}


def _status(value):
    return value if type(value) is int and 100 <= value <= 599 else None


def _set_failure_witness(error, *, code=None, failure_kind=None, http_status=None):
    error.transport_error_code = code if isinstance(code,str) and code in _SAFE_TRANSPORT_CODES else None
    error.http_failure_kind = failure_kind if isinstance(failure_kind,str) and failure_kind in _SAFE_FAILURE_KINDS else _CODE_KIND.get(error.transport_error_code, 'LOCAL_INPUT')
    error.http_status = _status(http_status)


class BoundedHTTPError(ValueError):
    def __init__(self, code, *, http_issued=False, outcome_unknown=False, failure_kind=None, http_status=None):
        super().__init__(code)
        self.code, self.http_issued, self.outcome_unknown = code, http_issued, outcome_unknown
        _set_failure_witness(self, code=code, failure_kind=failure_kind, http_status=http_status)


class ModelHTTPTimeout(httpx.ReadTimeout):
    """Safe httpx-compatible timeout carrying the actual transport witness."""

    def __init__(self, *, http_issued=False, outcome_unknown=False, transport_error_code=None, failure_kind=None, http_status=None):
        super().__init__("model attempt wall-clock budget exceeded")
        self.http_issued = bool(http_issued)
        self.outcome_unknown = bool(outcome_unknown)
        _set_failure_witness(self, code=transport_error_code, failure_kind=failure_kind or 'TIMEOUT', http_status=http_status)


class ModelHTTPTransportFailure(httpx.ConnectError):
    """Safe transport failure; no original URL, headers or exception text."""

    def __init__(self, *, http_issued=False, outcome_unknown=False, transport_error_code=None, failure_kind=None, http_status=None):
        super().__init__("bounded model transport unavailable")
        self.http_issued = bool(http_issued)
        self.outcome_unknown = bool(outcome_unknown)
        _set_failure_witness(self, code=transport_error_code, failure_kind=failure_kind or 'TRANSPORT_FAILURE', http_status=http_status)


def safe_http_failure(error):
    """Finite diagnostics only: never exception text, URL, headers or body."""
    name, kind = 'InternalError', 'INTERNAL_ERROR'
    classes = ((ModelHTTPTimeout,'ModelHTTPTimeout','TIMEOUT'),
        (ModelHTTPTransportFailure,'ModelHTTPTransportFailure','TRANSPORT_FAILURE'),
        (BoundedHTTPError,'BoundedHTTPError','LOCAL_INPUT'),(httpx.HTTPStatusError,'HTTPStatusError','HTTP_STATUS'),
        (TimeoutError,'TimeoutError','TIMEOUT'),
        (httpx.ConnectTimeout,'ConnectTimeout','CONNECT_TIMEOUT'),(httpx.ReadTimeout,'ReadTimeout','READ_TIMEOUT'),
        (httpx.WriteTimeout,'WriteTimeout','WRITE_TIMEOUT'),(httpx.PoolTimeout,'PoolTimeout','POOL_TIMEOUT'),
        (httpx.TimeoutException,'TimeoutException','TIMEOUT'),(httpx.ConnectError,'ConnectError','CONNECT_FAILURE'),
        (httpx.ReadError,'ReadError','READ_FAILURE'),(httpx.WriteError,'WriteError','WRITE_FAILURE'),
        (httpx.RemoteProtocolError,'RemoteProtocolError','PROTOCOL_FAILURE'),(httpx.HTTPError,'HTTPError','TRANSPORT_FAILURE'),
        (OSError,'OSError','INTERNAL_ERROR'),(RuntimeError,'RuntimeError','INTERNAL_ERROR'))
    for cls, label, category in classes:
        if isinstance(error, cls): name, kind = label, category; break
    supplied = getattr(error,'http_failure_kind',None)
    if isinstance(supplied,str) and supplied in _SAFE_FAILURE_KINDS: kind = supplied
    code = getattr(error,'transport_error_code',None)
    status = _status(getattr(error,'http_status',None))
    if isinstance(error,httpx.HTTPStatusError): status = _status(error.response.status_code)
    issued, unknown = getattr(error,'http_issued',None), getattr(error,'outcome_unknown',None)
    if isinstance(error,httpx.HTTPStatusError): issued, unknown = True, False
    return {'http_failure_kind':kind,'http_exception_type':name,
        'transport_error_code':code if isinstance(code,str) and code in _SAFE_TRANSPORT_CODES else None,
        'http_status':status,'http_failure_issued':issued if type(issued) is bool else None,
        'http_outcome_unknown':unknown if type(unknown) is bool else None}


def safe_http_diagnostics(value):
    """Recheck a transport projection before it enters persistent diagnostics."""
    if not isinstance(value,dict): return {}
    names={'InternalError','ModelHTTPTimeout','ModelHTTPTransportFailure','BoundedHTTPError','HTTPStatusError','TimeoutError',
        'ConnectTimeout','ReadTimeout','WriteTimeout','PoolTimeout','TimeoutException','ConnectError',
        'ReadError','WriteError','RemoteProtocolError','HTTPError','OSError','RuntimeError'}
    result={}
    if isinstance(value.get('http_failure_kind'),str) and value['http_failure_kind'] in _SAFE_FAILURE_KINDS:
        result['http_failure_kind']=value['http_failure_kind']
    if isinstance(value.get('http_exception_type'),str) and value['http_exception_type'] in names:
        result['http_exception_type']=value['http_exception_type']
    code=value.get('transport_error_code')
    result['transport_error_code']=code if isinstance(code,str) and code in _SAFE_TRANSPORT_CODES else None
    result['http_status']=_status(value.get('http_status'))
    for key in ('http_failure_issued','http_outcome_unknown'):
        result[key]=value[key] if type(value.get(key)) is bool else None
    return result if 'http_failure_kind' in result or 'http_exception_type' in result else {}


class BoundedHTTPClient:
    def __init__(self, max_in_flight=4):
        if type(max_in_flight) is not int or not 1 <= max_in_flight <= 16:
            raise ValueError("HTTP_CAPACITY_INVALID")
        self.capacity = max_in_flight
        self._slots = threading.BoundedSemaphore(max_in_flight)
        self._lock = threading.Lock()
        self._metrics = {"active": 0, "http_attempts": 0, "timeout_count": 0,
                         "rejected_count": 0, "too_large_count": 0, "late_discard_count": 0}

    def metrics(self):
        with self._lock:
            return {"capacity": self.capacity, **self._metrics}

    def _bump(self, **deltas):
        with self._lock:
            for key, delta in deltas.items():
                self._metrics[key] += delta

    def post_json(self, url, payload, headers, *, deadline_seconds, max_response_bytes,
                  transport=None, legacy_post=None):
        if type(deadline_seconds) not in {int, float} or not math.isfinite(deadline_seconds) or not 0 < deadline_seconds <= 360 \
                or type(max_response_bytes) is not int or not 1 <= max_response_bytes <= 4*1024*1024:
            raise BoundedHTTPError("HTTP_BOUND_INVALID")
        if transport is not None and legacy_post is not None:
            raise BoundedHTTPError("HTTP_TRANSPORT_CONFLICT")
        # Optional adapter headers and malformed local input must be handled
        # before taking a slot: no HTTP has been issued and no I/O worker exists
        # yet to release capacity if normalization raises.
        if headers is None:
            headers = {}
        if not isinstance(headers, Mapping) or any(
                not isinstance(key, str) or not isinstance(value, (str, bytes))
                for key, value in headers.items()):
            raise BoundedHTTPError("HTTP_HEADERS_INVALID")
        request_headers = {key: value for key, value in headers.items() if key.lower() != "accept-encoding"}
        request_headers["Accept-Encoding"] = "gzip, identity"
        try:
            httpx.Headers(request_headers)
        except (TypeError, ValueError, UnicodeError):
            raise BoundedHTTPError("HTTP_HEADERS_INVALID") from None
        if not self._slots.acquire(blocking=False):
            self._bump(rejected_count=1)
            raise BoundedHTTPError("HTTP_CAPACITY_EXHAUSTED")
        self._bump(active=1)
        deadline = time.monotonic()+deadline_seconds
        done, abandoned = threading.Event(), threading.Event()
        state = {"issued": False}

        def check():
            if abandoned.is_set() or time.monotonic() >= deadline:
                raise BoundedHTTPError("HTTP_OUTCOME_UNKNOWN_DEADLINE", http_issued=state["issued"],
                                       outcome_unknown=state["issued"], http_status=state.get('http_status'))

        def perform():
            try:
                check()
                state["issued"] = True
                self._bump(http_attempts=1)
                if legacy_post:
                    # Compatibility does not promise streaming memory bounds:
                    # only hard caller deadline, in-flight cap, final byte limit.
                    response = legacy_post(url, headers=request_headers, json=payload, timeout=deadline_seconds)
                    state['http_status'] = _status(response.status_code)
                    check()
                    content = response.content
                    if len(content) > max_response_bytes:
                        raise BoundedHTTPError("HTTP_RESPONSE_TOO_LARGE", http_issued=True)
                    result = httpx.Response(response.status_code, content=content,
                        headers={"Content-Type": response.headers.get("content-type", "application/json")})
                else:
                    with httpx.Client(transport=transport, trust_env=False, follow_redirects=False,
                                      timeout=max(.001, deadline-time.monotonic())) as client:
                        with client.stream("POST", url, headers=request_headers, json=payload) as response:
                            state['http_status'] = _status(response.status_code)
                            check()
                            encoding = response.headers.get("content-encoding", "identity").strip().lower()
                            if encoding not in {"", "identity", "gzip"}:
                                raise BoundedHTTPError("HTTP_ENCODING_UNSUPPORTED", http_issued=True)
                            declared = response.headers.get("content-length")
                            if declared:
                                try:
                                    if int(declared) > max_response_bytes:
                                        raise BoundedHTTPError("HTTP_RESPONSE_TOO_LARGE", http_issued=True)
                                except ValueError as error:
                                    if isinstance(error, BoundedHTTPError):
                                        raise
                                    raise BoundedHTTPError("HTTP_CONTENT_LENGTH_INVALID", http_issued=True) from None
                            buffered = response.is_stream_consumed
                            decoder = zlib.decompressobj(16+zlib.MAX_WBITS) if encoding == "gzip" and not buffered else None
                            output, raw_size = bytearray(), 0
                            pieces = [response.content] if buffered else response.iter_raw(chunk_size=8192)
                            for chunk in pieces:
                                check()
                                raw_size += len(chunk)
                                if raw_size > max_response_bytes:
                                    raise BoundedHTTPError("HTTP_RESPONSE_TOO_LARGE", http_issued=True)
                                if decoder:
                                    try:
                                        chunk = decoder.decompress(chunk, max_response_bytes+1-len(output))
                                    except zlib.error:
                                        raise BoundedHTTPError("HTTP_ENCODING_INVALID", http_issued=True) from None
                                    if decoder.unconsumed_tail or decoder.unused_data:
                                        raise BoundedHTTPError("HTTP_RESPONSE_TOO_LARGE", http_issued=True)
                                output.extend(chunk)
                                if len(output) > max_response_bytes:
                                    raise BoundedHTTPError("HTTP_RESPONSE_TOO_LARGE", http_issued=True)
                            check()
                            if decoder and not decoder.eof:
                                raise BoundedHTTPError("HTTP_ENCODING_INVALID", http_issued=True)
                            result = httpx.Response(response.status_code, content=bytes(output),
                                headers={"Content-Type": response.headers.get("content-type", "application/json")})
                check()
                if not abandoned.is_set():
                    state["response"] = result
            except BoundedHTTPError as error:
                if error.http_status is None: error.http_status = state.get('http_status')
                if error.code == "HTTP_RESPONSE_TOO_LARGE":
                    self._bump(too_large_count=1)
                if not abandoned.is_set():
                    state["error"] = error
            except (httpx.ConnectError, httpx.ConnectTimeout) as error:
                # A connection-stage failure is a real transport attempt, but
                # it cannot have reached model inference. Read/write failures
                # after connection keep the conservative unknown outcome.
                if isinstance(error, httpx.ConnectTimeout):
                    self._bump(timeout_count=1)
                if not abandoned.is_set():
                    state["error"] = BoundedHTTPError(
                        "HTTP_TIMEOUT" if isinstance(error, httpx.ConnectTimeout) else "HTTP_TRANSPORT_FAILED",
                        http_issued=state["issued"], outcome_unknown=False,
                        failure_kind='CONNECT_TIMEOUT' if isinstance(error,httpx.ConnectTimeout) else 'CONNECT_FAILURE',
                        http_status=state.get('http_status'))
            except httpx.TimeoutException as error:
                self._bump(timeout_count=1)
                if not abandoned.is_set():
                    state["error"] = BoundedHTTPError("HTTP_TIMEOUT", http_issued=state["issued"],
                        outcome_unknown=state["issued"], failure_kind=safe_http_failure(error)['http_failure_kind'],
                        http_status=state.get('http_status'))
            except httpx.HTTPError as error:
                if not abandoned.is_set():
                    state["error"] = BoundedHTTPError("HTTP_TRANSPORT_FAILED", http_issued=state["issued"],
                        outcome_unknown=state["issued"], failure_kind=safe_http_failure(error)['http_failure_kind'],
                        http_status=state.get('http_status'))
            except Exception:
                if not abandoned.is_set():
                    state["error"] = BoundedHTTPError("HTTP_TRANSPORT_FAILED", http_issued=state["issued"],
                        outcome_unknown=state["issued"], failure_kind='INTERNAL_ERROR',http_status=state.get('http_status'))
            finally:
                if abandoned.is_set():
                    self._bump(late_discard_count=1)
                self._bump(active=-1)
                self._slots.release()
                done.set()

        try:
            threading.Thread(target=perform, name="bounded-model-http", daemon=True).start()
        except RuntimeError:
            self._bump(active=-1)
            self._slots.release()
            raise BoundedHTTPError("HTTP_CAPACITY_UNAVAILABLE") from None
        if not done.wait(max(0, deadline-time.monotonic())):
            abandoned.set()
            self._bump(timeout_count=1)
            raise BoundedHTTPError("HTTP_OUTCOME_UNKNOWN_DEADLINE", http_issued=state["issued"],
                                   outcome_unknown=state["issued"],http_status=state.get('http_status'))
        if "error" in state:
            raise state["error"]
        if "response" not in state or time.monotonic() >= deadline:
            self._bump(timeout_count=1)
            raise BoundedHTTPError("HTTP_OUTCOME_UNKNOWN_DEADLINE", http_issued=state["issued"],
                                   outcome_unknown=state["issued"],http_status=state.get('http_status'))
        return state["response"]


_SHARED_CLIENT = BoundedHTTPClient(max_in_flight=4)


def bounded_post_json(url, payload, headers, *, deadline_seconds, max_response_bytes, transport=None, legacy_post=None):
    return _SHARED_CLIENT.post_json(url, payload, headers, deadline_seconds=deadline_seconds,
        max_response_bytes=max_response_bytes, transport=transport, legacy_post=legacy_post)


def get_bounded_http_metrics():
    return _SHARED_CLIENT.metrics()
