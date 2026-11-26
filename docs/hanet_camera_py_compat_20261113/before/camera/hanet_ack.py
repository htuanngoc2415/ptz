"""Conservative PTZ ACK contract and bounded, value-free schema diagnostics.

Boolean True is the existing software contract, NOT a captured firmware fixture.
No object/string/numeric success schema is enabled without hardware evidence.
An ACK never establishes physical motor position or standstill.
"""
from dataclasses import dataclass
from itertools import islice
import json

ACK_SUCCESS = 'ACK_SUCCESS'
ACK_REJECTED = 'ACK_REJECTED'
ACK_UNKNOWN = 'ACK_UNKNOWN'
TRANSPORT_ERROR = 'TRANSPORT_ERROR'
PROTOCOL_ERROR = 'PROTOCOL_ERROR'


class HanetRPCRejected(RuntimeError):
    classification = ACK_REJECTED


class HanetACKUnknown(RuntimeError):
    classification = ACK_UNKNOWN


class HanetProtocolError(RuntimeError):
    classification = PROTOCOL_ERROR


@dataclass(frozen=True)
class AckDecision:
    classification: str
    reason: str


def validate_response(message, request_id, *, method='Control.DoPtz', operation=None, code=None):
    """Validate a matched JSON object; never infer success from no error."""
    if not isinstance(message, dict):
        return AckDecision(PROTOCOL_ERROR, 'MALFORMED_RESPONSE')
    if ('id' not in message or type(message['id']) is not type(request_id)
            or message['id'] != request_id):
        return AckDecision(PROTOCOL_ERROR, 'MISMATCHED_REQUEST_ID')
    if message.get('jsonrpc') != '2.0':
        return AckDecision(PROTOCOL_ERROR, 'MALFORMED_RESPONSE')
    if 'error' in message:
        error = message['error']
        if ('result' in message or not isinstance(error, dict)
                or type(error.get('code')) is not int
                or not isinstance(error.get('message'), str)):
            return AckDecision(PROTOCOL_ERROR, 'MALFORMED_RESPONSE')
        return AckDecision(ACK_REJECTED, 'EXPLICIT_REJECTION')
    if 'result' not in message:
        return AckDecision(ACK_UNKNOWN, 'MISSING_RESULT')
    if method != 'Control.DoPtz':
        return AckDecision(ACK_UNKNOWN, 'METHOD_NOT_SUPPORTED')
    if operation is not None and operation not in ('START', 'STOP'):
        return AckDecision(PROTOCOL_ERROR, 'INVALID_PTZ_OPERATION')
    if code is not None and code not in (
            'kCmdZoomTele', 'kCmdZoomWide', 'kCmdLeft', 'kCmdRight', 'kCmdUp', 'kCmdDown'):
        return AckDecision(PROTOCOL_ERROR, 'UNSUPPORTED_PTZ_CODE')
    if message.get('result') is False:
        return AckDecision(ACK_REJECTED, 'EXPLICIT_REJECTION')
    if message.get('result') is True:
        # Existing synthetic boolean contract; NOT firmware verification.
        return AckDecision(ACK_SUCCESS, 'VALID_METHOD_SUCCESS')
    if isinstance(message.get('result'), dict) and not message['result']:
        # Observed STOP kCmdZoomWide response on 2026-11-13. Its envelope is
        # known; device command semantics and physical STOP are NOT verified.
        return AckDecision(ACK_UNKNOWN, 'EMPTY_RESULT_UNVERIFIED')
    return AckDecision(ACK_UNKNOWN, 'UNVERIFIED_RESULT_SCHEMA')


def failure_classification(exc):
    return getattr(exc, 'classification', TRANSPORT_ERROR)


# Only fixed field names survive. Unknown keys may themselves contain secrets.
# No response string values (including status/message/session/id) are recorded.
_SAFE_KEYS = frozenset(('id', 'jsonrpc', 'result', 'error', 'data', 'status',
                        'code', 'success', 'errorCode', 'error_code', 'message',
                        'ret', 'returnCode', 'value'))


def _shape(value, depth=0, budget=None):
    budget = [24] if budget is None else budget
    budget[0] -= 1
    result = {'type': type(value).__name__}
    if isinstance(value, dict):
        fields = {}
        omitted = 0
        for key, item in islice(value.items(), 16):
            if budget[0] <= 0:
                break
            if key in _SAFE_KEYS:
                budget[0] -= 1
                fields[key] = (_shape(item, depth+1, budget) if depth < 2
                               else {'type': type(item).__name__})
            else:
                omitted += 1
        result.update(fields=fields, omitted_keys=omitted,
                      truncated=len(value) > 16 or budget[0] <= 0)
    return result


def response_schema(message, request_id):
    """Bounded work: <=16 fields/level, depth <=2, no raw response values.

Vendor numeric status meanings are not yet verified, so even numeric codes are
withheld. A matching locally generated UUID is safe to repeat; foreign IDs are
represented by a marker. This is sufficient to discover nesting and types.
"""
    if not isinstance(message, dict):
        return {'response_type': type(message).__name__, 'response_id': None}
    shape = _shape(message)
    matched = type(message.get('id')) is type(request_id) and message.get('id') == request_id
    result = message.get('result')
    result_shape = shape['fields'].get('result', {})
    diagnostic = {'response_id': request_id if matched else '<unmatched>',
            'response_id_type': type(message.get('id')).__name__,
            'response_keys': list(shape['fields']),
            'response_shape': shape,
            'result_type': type(result).__name__ if 'result' in message else 'missing',
            'result_keys': list(result_shape.get('fields', {}))}
    if len(json.dumps(diagnostic)) > 4096:
        diagnostic.pop('response_shape')
        diagnostic['schema_truncated'] = True
    return diagnostic
