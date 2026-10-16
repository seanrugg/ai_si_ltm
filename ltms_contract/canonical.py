"""RFC 8785 JSON Canonicalization Scheme (JCS).

This is the one serializer every LTMS member must agree with byte-for-byte.
It is deliberately small and dependency-free so it can be vendored onto a robot.

Rules (RFC 8785):
* Objects: keys sorted by their UTF-16 code units, no whitespace.
* Strings: only ``"`` ``\\`` and control characters < U+0020 are escaped;
  ``\\b \\f \\n \\r \\t`` use short forms, others ``\\u00xx`` (lowercase hex).
  Everything else is emitted as raw UTF-8.
* Numbers: IEEE-754 doubles printed the way ECMAScript ``Number.prototype.toString``
  prints them (shortest round-trip digits). ``1.0`` and ``1`` are the same number
  and serialize as ``1``. NaN and Infinity are rejected.

LTMS adds two restrictions so every language parses the same values:
* Integers must fit in a double exactly: ``|n| <= 2**53 - 1``.
* Strings must not contain lone surrogates.
"""
from __future__ import annotations

import math
from decimal import Decimal

__all__ = ["canonicalize", "CanonicalizationError", "MAX_SAFE_INT"]

MAX_SAFE_INT = 2**53 - 1


class CanonicalizationError(ValueError):
    """Value cannot be represented in canonical JSON."""


_SHORT_ESCAPES = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
    0x22: '\\"',
    0x5C: "\\\\",
}


def _string(s: str) -> str:
    out = ['"']
    for ch in s:
        cp = ord(ch)
        if 0xD800 <= cp <= 0xDFFF:
            raise CanonicalizationError("lone surrogate in string")
        esc = _SHORT_ESCAPES.get(cp)
        if esc is not None:
            out.append(esc)
        elif cp < 0x20:
            out.append(f"\\u{cp:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _number(x: float | int) -> str:
    if isinstance(x, int):
        if abs(x) > MAX_SAFE_INT:
            raise CanonicalizationError(f"integer {x} exceeds 2**53-1")
        return str(x)
    if math.isnan(x) or math.isinf(x):
        raise CanonicalizationError("NaN/Infinity are not valid JSON")
    if x == 0:
        return "0"  # also covers -0.0
    sign = "-" if x < 0 else ""
    # repr() gives the shortest round-trip digits; Decimal splits them out.
    _, digit_tuple, exp = Decimal(repr(abs(x))).as_tuple()
    digits = "".join(map(str, digit_tuple)).rstrip("0")
    exp += len("".join(map(str, digit_tuple))) - len(digits)
    k = len(digits)
    n = k + exp  # value = 0.digits * 10**n
    if k <= n <= 21:
        body = digits + "0" * (n - k)
    elif 0 < n <= 21:
        body = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        mant = digits if k == 1 else digits[0] + "." + digits[1:]
        body = f"{mant}e{'+' if e >= 0 else '-'}{abs(e)}"
    return sign + body


def _emit(value: object, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, (int, float)):
        out.append(_number(value))
    elif isinstance(value, str):
        out.append(_string(value))
    elif isinstance(value, (list, tuple)):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _emit(item, out)
        out.append("]")
    elif isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise CanonicalizationError("object keys must be strings")
        out.append("{")
        for i, key in enumerate(sorted(value, key=lambda k: k.encode("utf-16-be"))):
            if i:
                out.append(",")
            out.append(_string(key))
            out.append(":")
            _emit(value[key], out)
        out.append("}")
    else:
        raise CanonicalizationError(f"unsupported type {type(value).__name__}")


def canonicalize(value: object) -> bytes:
    """Return the JCS canonical UTF-8 bytes of a JSON-compatible value."""
    out: list[str] = []
    _emit(value, out)
    return "".join(out).encode("utf-8")
