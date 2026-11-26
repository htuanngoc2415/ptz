"""HANET JSON-RPC/WebSocket protocol, ported from the workspace camera.py.

This module is self-contained; the PTZ application does not import parent files.
"""
from __future__ import annotations
import base64
import hashlib
import json
import math
import os
import socket
import struct
import time
import threading
from telemetry import latency
from event_logging import redact
from uuid import uuid4
import requests
from .hanet_ack import (ACK_SUCCESS, ACK_REJECTED, ACK_UNKNOWN, PROTOCOL_ERROR,
                        AckDecision, HanetRPCRejected, HanetACKUnknown,
                        HanetProtocolError, validate_response, response_schema,
                        failure_classification)


class HanetPTZClient:
    """Client toi thieu cho JSON-RPC/WebSocket cua firmware HANET."""

    def __init__(self, host: str, port: int, username: str, password: str, timeout=5.0,
                 response_diagnostics=None):
        self._lock = threading.RLock()
        self._closed = False
        self._io_deadline = None
        self.generation = 0
        self.transport_state = 'DISCONNECTED'
        self._reconnect_attempt = 0
        self._next_reconnect = 0.
        self._needs_login = False
        self.response_diagnostics = (os.getenv('PTZ_HANET_RESPONSE_DIAGNOSTICS', '1') == '1'
                                     if response_diagnostics is None else bool(response_diagnostics))
        self._schema_last = {}
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self.timeout = float(timeout)
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError('HANET timeout must be finite and positive')
        self.http_url = f"http://{host}:{port}/mtrpc"
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self.session_id: str | None = None
        self.heartbeat_interval = 30.0
        self.next_heartbeat = float("inf")
        self._request_id = 0
        self._ws: socket.socket | None = None
        self._ws_buffer = b""
        try:
            self._login()
        except Exception:
            self.session.close()
            raise

    @staticmethod
    def _md5(text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def _call(self, method: str, params: dict | None = None) -> dict:
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or {},
        }
        response = self.session.post(self.http_url, json=payload, timeout=self._remaining())
        response.raise_for_status()
        message = response.json()
        if message.get("error"):
            raise HanetRPCRejected(f"HANET RPC rejected {method}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"HANET RPC returned invalid {method} response")
        return result

    def _login(self) -> None:
        challenge = self._call(
            "Auth.LoginChallenge",
            {
                "data": {
                    "encrypt_type": "kEncryptDigest",
                    "login_type": "kLoginWeb",
                    "username": self.username,
                }
            },
        )
        digest = (challenge.get("data") or {}).get("digest") or {}
        challenge_session = challenge.get("session_id")
        realm = digest.get("realm")
        nonce = digest.get("nonce")
        qop = digest.get("qop")
        if not all((challenge_session, realm, nonce, qop)):
            raise RuntimeError("HANET login challenge is incomplete")

        nc = "00000001"
        cnonce = str(uuid4())
        uri = "/mtrpc"
        user_hash = self._md5(f"{self.username}:{realm}:{self.password}")
        request_hash = self._md5(f"POST:{uri}")
        response_hash = self._md5(
            f"{user_hash}:{nonce}:{nc}:{cnonce}:{qop}:{request_hash}"
        )
        login_data = {
            "username": self.username,
            "encrypt_type": "kEncryptDigest",
            "login_type": "kLoginWeb",
            "digest": {
                "realm": realm,
                "uri": uri,
                "nonce": nonce,
                "nc": nc,
                "cnonce": cnonce,
                "qop": qop,
                "response": response_hash,
            },
        }
        result = self._call(
            "Auth.Login",
            {"data": login_data, "session_id": challenge_session},
        )
        data = result.get("data") or {}
        self.session_id = data.get("session_id")
        if not self.session_id:
            raise RuntimeError("HANET authentication rejected")

        interval = float(data.get("heartbeat_interval") or 30.0)
        if interval > 10:
            interval = max(1.0, float(int(interval + 2) // 3))
        elif interval > 1:
            interval -= 1
        self.heartbeat_interval = interval
        self.next_heartbeat = time.monotonic() + interval

    def _event(self, event, **fields):
        latency.profiler.event(event, backend='hanet', connection_generation=self.generation,
                               timeout=self.timeout, transport_state=self.transport_state, **fields)

    def _safe_error(self, exc):
        message = redact(str(exc))
        for secret in (self.password, self.session_id):
            if secret:
                message = message.replace(str(secret), '[REDACTED]')
        return message[:512]

    def _remaining(self):
        deadline = getattr(self, '_io_deadline', None)
        remaining = self.timeout if deadline is None else deadline-time.monotonic()
        if remaining <= 0:
            raise TimeoutError('HANET operation deadline exceeded')
        return remaining

    def _invalidate(self):
        connection, self._ws = self._ws, None
        self._ws_buffer = b''
        self.session_id = None
        self._needs_login = True
        self.transport_state = 'CLOSED' if self._closed else 'DISCONNECTED'
        clean = True
        if connection is not None:
            try:
                connection.close()
            except Exception as exc:
                clean = False
                self._event('HANET_CLEANUP_ERROR', exception_type=type(exc).__name__,
                            exception_message='Socket close failed')
            self._event('HANET_CONNECTION_CLOSED')
        return clean

    def heartbeat(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError('HANET client closed')
            # Recovery belongs to STOP or a later explicitly requested command.
            if self._needs_login:
                raise RuntimeError('HANET session requires recovery')
            if time.monotonic() < self.next_heartbeat:
                return
            self._io_deadline = time.monotonic()+self.timeout
            try:
                self._call('Auth.Heartbeat', {'session_id': self.session_id})
                self.next_heartbeat = time.monotonic()+self.heartbeat_interval
            except Exception as exc:
                self._event('HANET_TRANSPORT_ERROR', operation='heartbeat',
                            exception_type=type(exc).__name__, exception_message=self._safe_error(exc))
                self._invalidate()
                raise
            finally:
                self._io_deadline = None

    def _recv_exact(self, size: int) -> bytes:
        while len(self._ws_buffer) < size:
            if self._ws is None:
                raise RuntimeError("WebSocket is closed")
            self._ws.settimeout(self._remaining())
            chunk = self._ws.recv(max(4096, size - len(self._ws_buffer)))
            if not chunk:
                raise RuntimeError("WebSocket connection closed")
            self._ws_buffer += chunk
        result, self._ws_buffer = self._ws_buffer[:size], self._ws_buffer[size:]
        return result

    def _connect_websocket(self) -> None:
        if self._ws is not None:
            return
        connection = socket.create_connection((self.host, self.port), timeout=self._remaining())
        try:
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            request = (
                "GET /mtrpcoverwebsocket HTTP/1.1\r\n"
                f"Host: {self.host}:{self.port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                f"Origin: http://{self.host}:{self.port}\r\n\r\n"
            )
            connection.settimeout(self._remaining())
            connection.sendall(request.encode("ascii"))
            response = b""
            while b"\r\n\r\n" not in response:
                connection.settimeout(self._remaining())
                chunk = connection.recv(4096)
                if not chunk:
                    raise RuntimeError("WebSocket handshake closed")
                response += chunk
                if len(response) > 65536:
                    raise RuntimeError("WebSocket handshake is too large")
            headers, remainder = response.split(b"\r\n\r\n", 1)
            if not headers.startswith(b"HTTP/1.1 101"):
                raise RuntimeError("WebSocket handshake rejected")
            connection.settimeout(self._remaining())
            self._ws = connection
            self._ws_buffer = remainder
        except Exception:
            try:
                connection.close()
            except Exception as exc:
                self._event('HANET_CLEANUP_ERROR', exception_type=type(exc).__name__,
                            exception_message='Handshake socket close failed')
            raise
        self.generation += 1
        self.transport_state = 'CONNECTED'
        self._event('HANET_CONNECTION_OPEN')

    def _send_frame(self, payload: bytes, opcode=0x1) -> None:
        if self._ws is None:
            raise RuntimeError("WebSocket is closed")
        mask = os.urandom(4)
        length = len(payload)
        header = bytearray([0x80 | opcode])
        if length < 126:
            header.append(0x80 | length)
        elif length <= 0xFFFF:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self._ws.settimeout(self._remaining())
        self._ws.sendall(bytes(header) + masked)

    def _recv_frame(self) -> tuple[int, bytes]:
        first, second = self._recv_exact(2)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        if length > 1024*1024:
            raise HanetProtocolError('HANET WebSocket frame exceeds limit')
        if not first & 0x80:
            raise HanetProtocolError('Fragmented HANET response unsupported')
        mask = self._recv_exact(4) if masked else None
        payload = self._recv_exact(length)
        if mask:
            payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        return opcode, payload

    def _response_diagnostic(self, message, request_id, operation, code, decision):
        if not getattr(self, 'response_diagnostics', False):
            return
        # Fixed key space (operation/classification), independent of remote IDs,
        # keys, generations and command rate. First failure cannot be hidden by
        # an earlier success. Capture at most once/second for each category.
        key = (operation, decision.classification)
        now = time.monotonic()
        if now-self._schema_last.get(key, float('-inf')) < 1.:
            return
        self._schema_last[key] = now
        self._event('HANET_RESPONSE_SCHEMA', request_id=request_id,
                    method='Control.DoPtz', rpc_code=code, operation=operation,
                    ack_classification=decision.classification,
                    parser_reason=decision.reason,
                    **response_schema(message, request_id))

    def _receive_response(self, request_id, *, operation=None, code=None, generation=None) -> bool:
        deadline = min(time.monotonic() + self.timeout,
                       getattr(self, '_io_deadline', None) or float('inf'))
        mismatch = False
        try:
            while time.monotonic() < deadline:
                opcode, payload = self._recv_frame()
                if time.monotonic() >= deadline:
                    raise TimeoutError('HANET PTZ acknowledgement arrived after deadline')
                if (generation is not None and generation != self.generation
                        or getattr(self, '_closed', False)):
                    self._response_diagnostic(None, request_id, operation, code,
                        AckDecision(PROTOCOL_ERROR, 'STALE_CONNECTION_GENERATION'))
                    raise HanetProtocolError('Stale HANET connection response')
                if opcode == 0x9:  # ping
                    self._send_frame(payload, opcode=0xA)
                    continue
                if opcode == 0x8:
                    raise RuntimeError("WebSocket was closed by camera")
                if opcode == 0xA:  # pong carries no RPC acknowledgement
                    continue
                if opcode != 0x1:
                    self._response_diagnostic(None, request_id, operation, code,
                        AckDecision(PROTOCOL_ERROR, 'UNEXPECTED_WEBSOCKET_OPCODE'))
                    raise HanetProtocolError('Unexpected HANET WebSocket opcode')
                try:
                    message = json.loads(payload.decode("utf-8"))
                except (ValueError, UnicodeError, RecursionError):
                    self._response_diagnostic(None, request_id, operation, code,
                        AckDecision(PROTOCOL_ERROR, 'MALFORMED_RESPONSE'))
                    raise HanetProtocolError('Malformed HANET response') from None
                decision = validate_response(message, request_id, operation=operation, code=code)
                self._response_diagnostic(message, request_id, operation, code, decision)
                if decision.reason == 'MISMATCHED_REQUEST_ID':
                    mismatch = True
                    continue
                if decision.classification == ACK_REJECTED:
                    raise HanetRPCRejected("Camera rejected PTZ command")
                if decision.classification == ACK_UNKNOWN:
                    raise HanetACKUnknown('HANET response has no verified success result')
                if decision.classification != ACK_SUCCESS:
                    raise HanetProtocolError(decision.reason)
                return True
            raise TimeoutError("HANET PTZ acknowledgement timed out")
        except socket.timeout:
            if mismatch:
                raise HanetProtocolError('No matching HANET response before deadline') from None
            raise TimeoutError("HANET PTZ acknowledgement timed out") from None

    def motion(self, code: str, start: bool, speed: int) -> bool:
        # One transaction owns the socket and session through ACK validation.
        # Never replay a START (or any command) within this call.
        with self._lock:
            if self._closed:
                raise RuntimeError('HANET client closed')
            if time.monotonic() < self._next_reconnect:
                raise RuntimeError('HANET reconnect backoff')
            self._io_deadline = time.monotonic()+self.timeout
            reconnect = self._needs_login
            request_id = str(uuid4())
            operation = 'START' if start else 'STOP'
            transmission = 'not_attempted'
            try:
                if reconnect:
                    self.transport_state = 'TRANSPORT_RECOVERING'
                    self._reconnect_attempt += 1
                    self._event('HANET_RECONNECT_ATTEMPT', retry_attempt=self._reconnect_attempt)
                    self._login()
                if not self.session_id:
                    raise RuntimeError('HANET session is unavailable')
                self._connect_websocket()
                if reconnect:
                    self._needs_login = False
                    self._event('HANET_RECONNECT_SUCCESS', retry_attempt=self._reconnect_attempt,
                                motor_safe=False)
                generation = self.generation
                payload = self._motion_payload(request_id, code, start, speed)
                transmission = 'attempted_unknown'
                self._send_frame(json.dumps(payload, separators=(',', ':')).encode('utf-8'))
                transmission = 'sent'
                self._event('PAN_TILT_'+operation+'_SENT', rpc_command_id=request_id,
                            operation=operation, rpc_code=code)
                ack = self._receive_response(request_id, operation=operation,
                                             code=code, generation=generation)
                if generation != self.generation or self._closed:
                    raise HanetProtocolError('Stale HANET connection response')
                self._event('PAN_TILT_'+operation+'_ACK', rpc_command_id=request_id,
                            operation=operation, rpc_code=code, acknowledged=ack,
                            ack_classification=ACK_SUCCESS, evidence='LEGACY_BOOLEAN_TRUE',
                            physical_motor_stopped=None)
                self._reconnect_attempt = 0
                self._next_reconnect = 0.
                return ack
            except Exception as exc:
                self._event('PAN_TILT_'+operation+'_UNCONFIRMED', rpc_command_id=request_id,
                            operation=operation, rpc_code=code, acknowledged=False,
                            transmission=transmission,
                            ack_classification=failure_classification(exc),
                            parser_reason='CONNECTION_FAILURE' if failure_classification(exc) == 'TRANSPORT_ERROR' else None,
                            exception_type=type(exc).__name__, exception_message=self._safe_error(exc),
                            exception_errno=getattr(exc, 'errno', None))
                self._event('HANET_TRANSPORT_ERROR', rpc_command_id=request_id, operation=operation,
                            exception_type=type(exc).__name__, exception_message='Connection invalidated',
                            failure_kind=failure_classification(exc))
                self._invalidate()
                if reconnect:
                    self._event('HANET_RECONNECT_FAILED', retry_attempt=self._reconnect_attempt,
                                exception_type=type(exc).__name__, exception_message='Recovery did not confirm command')
                # No sleeps in the execution lane, and no retry loop here.
                self._next_reconnect = time.monotonic()+min(8., .5*2**min(self._reconnect_attempt, 4))
                raise
            finally:
                self._io_deadline = None

    def _motion_payload(self, request_id, code, start, speed):
        payload = {
            "id": request_id,
            "jsonrpc": "2.0",
            "method": "Control.DoPtz",
            "params": {
                "session_id": self.session_id,
                "data": {
                    "cmd": code,
                    "operate": "kOperateStart" if start else "kOperateStop",
                    "channel": 0,
                    "step": max(1, min(8, int(speed))),
                    "preset": 0,
                    "patrol": 0,
                    "preset_name": "",
                },
            },
        }
        return payload

    def abort(self):
        """Wake blocked socket I/O during shutdown; never grants motor safety."""
        self._closed = True
        connection = self._ws
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except Exception as exc:
                self._event('HANET_CLEANUP_ERROR', exception_type=type(exc).__name__,
                            exception_message='Socket shutdown failed')

    def close(self) -> bool:
        self.abort()
        if not self._lock.acquire(timeout=self.timeout):
            self._event('HANET_CLEANUP_ERROR', exception_type='TimeoutError',
                        exception_message='RPC transaction did not exit before close deadline')
            return False
        try:
            clean = self._invalidate()
            # No logout round trip on shutdown: local session is invalidated and
            # the camera expires its lease. Closing is not a motor STOP proof.
            try:
                self.session.close()
            except Exception as exc:
                clean = False
                self._event('HANET_CLEANUP_ERROR', exception_type=type(exc).__name__,
                            exception_message='HTTP session close failed')
            return clean
        finally:
            self._lock.release()
