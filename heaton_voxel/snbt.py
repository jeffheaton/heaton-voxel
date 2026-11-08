"""SNBT -- the text form of NBT, read and written the way the game does.

SNBT is what commands, data packs and ``/data get`` speak::

    from heaton_voxel.snbt import parse_snbt, to_snbt

    tag = parse_snbt('{id: "minecraft:stone", count: 1b, tags: [a, b]}')
    to_snbt(tag)    # '{count:1b,id:"minecraft:stone",tags:["a","b"]}'

:func:`parse_snbt` reads what the game's ``TagParser`` reads, the grammar
of 1.21.5 and later: integers in decimal, hex (``0x``) and binary (``0b``)
with underscores between digits and a suffix that names the type and may
name its signedness (``255ub`` is the byte ``-1``), floats with or without
a whole part, a fraction or an exponent, booleans in any case, quoted
strings with every escape the game has, unquoted words, compounds and lists
that may end in a comma, arrays (``[B;1b,2b]``), lists of mixed types, and
the two built-in operations, ``bool(x)`` and ``uuid("...")``.
:func:`to_snbt` writes what ``Tag.toString()`` writes: compound keys sorted
the way Java sorts strings and quoted only when they must be, each string
in the quote it needs fewer escapes in, and every float and double spelled
as Java's ``Float.toString`` and ``Double.toString`` spell them.

Both are held to the game, case by case, by ``tests/test_snbt.py``, over the
answers ``tools/probe_snbt.py`` asked of the game itself and recorded in
``fixtures/snbt.json``.

A list of mixed types is stored the way the game stores it: a list of
compounds, each element that is not already a plain compound wrapped under
the empty key, ``{"": 1}``. That is the tag ``encode`` writes and the game
reads back, and :func:`to_snbt` unwraps those wrappers again, as the game's
reader does.

Three departures, each a refusal with its reason, never a different answer:

* ``\\N{name}`` escapes. The game looks the name up in the JDK's Unicode
  name table (``Character.codePointOf``), which neither this library nor
  its C# twin carries; write the character, or ``\\u``/``\\U`` its code.
* Nesting deeper than :data:`~heaton_voxel.nbt.MAX_DEPTH` levels. The game's
  parser takes any depth, but its own binary reader stops at 512, so a
  deeper tag is one no world can hold.
* A ``uuid("...")`` string with a digit outside ASCII. The game reads it
  with ``UUID.fromString``, whose ``Character.digit`` takes digits from
  every script.

Pure standard library, like the rest of the package.
"""

from __future__ import annotations

import math
import re
import struct
from fractions import Fraction
from typing import Any

from .nbt import (
    MAX_DEPTH,
    TAG_COMPOUND,
    TAG_END,
    NBTError,
    Tag,
    TagByte,
    TagByteArray,
    TagCompound,
    TagDouble,
    TagFloat,
    TagInt,
    TagIntArray,
    TagList,
    TagLong,
    TagLongArray,
    TagShort,
    TagString,
)

__all__ = ["parse_snbt", "to_snbt"]


# --------------------------------------------------------------------------
# Characters
# --------------------------------------------------------------------------

#: Java's ``Character.isWhitespace``, which the parser skips: the Unicode
#: space, line and paragraph separators except the three non-breaking
#: spaces, and nine controls. Spelled out rather than asked of
#: ``unicodedata``, so a Python on another Unicode version reads the same.
WHITESPACE = frozenset(
    chr(c) for c in (
        0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x1C, 0x1D, 0x1E, 0x1F, 0x20, 0x1680,
        0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2008,
        0x2009, 0x200A, 0x2028, 0x2029, 0x205F, 0x3000,
    )
)

_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_DIGITS = frozenset("0123456789")
_HEX = frozenset("0123456789abcdefABCDEF")
#: What an unquoted word is made of, and what one that is a value starts
#: with (a key may start with any of them).
_WORD = frozenset(_LETTERS + "0123456789_-.+")
_WORD_START = frozenset(_LETTERS + "_")
#: A key ``toString`` writes without quotes (``StringTagVisitor``).
_BARE_KEY = re.compile(r"[A-Za-z._][A-Za-z0-9._+-]*\Z")

#: The escapes a quoted string takes after its backslash, but the numbered
#: ones (``x``, ``u``, ``U``) and the named one (``N``).
_ESCAPES = {"b": "\b", "s": " ", "t": "\t", "n": "\n", "f": "\f", "r": "\r",
            "\\": "\\", "'": "'", '"': '"'}
#: What ``toString`` writes after the backslash for a control character
#: (``SnbtGrammar.escapeControlCharacters``); any other below a space is
#: ``x`` and two uppercase hex digits (``HexFormat.of().withUpperCase()``).
_CONTROL_ESCAPES = {8: "b", 9: "t", 10: "n", 12: "f", 13: "r"}

#: Integer types by suffix letter: the tag and its width in bits.
_INTEGERS = {"b": (TagByte, 8), "s": (TagShort, 16), "i": (TagInt, 32),
             "l": (TagLong, 64)}
#: The array prefixes: the tag and the integer suffix of its elements.
_ARRAYS = {"B": (TagByteArray, "b"), "I": (TagIntArray, "i"),
           "L": (TagLongArray, "l")}

_NUMERIC = (TagByte, TagShort, TagInt, TagLong, TagFloat, TagDouble)


# --------------------------------------------------------------------------
# Floating point, exactly as Java does it
# --------------------------------------------------------------------------
#
# Both directions are done in exact rational arithmetic, so the answer is the
# IEEE-754 one on every platform and in both twins: a decimal rounds to the
# nearest float, ties to even, the way Float.parseFloat and
# Double.parseDouble round it, and a float prints as the decimal Java's
# toString picks.

#: ``(significand bits, smallest exponent of the last place, exponent past
#: the largest finite value)`` for a float and a double.
_FORMATS = {True: (24, -149, 128), False: (53, -1074, 1024)}


def _round_binary(value: Fraction, single: bool) -> float:
    """``value`` (positive) rounded to the nearest float or double, ties to
    even; ``inf`` past the largest finite one."""
    bits, quantum_floor, overflow = _FORMATS[single]
    n, d = value.numerator, value.denominator
    exponent = n.bit_length() - d.bit_length()      # 2**exponent <= value?
    if exponent >= 0:
        if n < d << exponent:
            exponent -= 1
    elif n << -exponent < d:
        exponent -= 1
    quantum = max(exponent - bits + 1, quantum_floor)
    if quantum >= 0:
        whole, rest = divmod(n, d << quantum)
        half = d << quantum
    else:
        whole, rest = divmod(n << -quantum, d)
        half = d
    if 2 * rest > half or (2 * rest == half and whole & 1):
        whole += 1
    if whole.bit_length() + quantum > overflow:
        return math.inf
    return math.ldexp(whole, quantum)


def _parse_decimal(whole: str, fraction: str, exponent: str, single: bool) -> float:
    """The float or double a decimal literal's digits round to (positive)."""
    digits = int((whole + fraction) or "0")
    if digits == 0:
        return 0.0
    power = int(exponent or "0") - len(fraction)
    # Too far out to round: past infinity or below half the least subnormal.
    magnitude = len(str(digits)) + power
    if magnitude > (40 if single else 310):
        return math.inf
    if magnitude < (-47 if single else -325):
        return 0.0
    value = Fraction(digits) * Fraction(10) ** power
    return _round_binary(value, single)


def _binary_parts(value: float, single: bool) -> tuple[int, int, bool]:
    """``(m, e, lower_closer)`` for a positive finite ``value``: it is
    ``m * 2**e``, and ``lower_closer`` says the float below it is half as
    far away as the one above (a power of two past the subnormals)."""
    if single:
        bits = struct.unpack(">I", struct.pack(">f", value))[0]
        stored, field, width, bias = bits & 0x7FFFFF, (bits >> 23) & 0xFF, 23, 150
    else:
        bits = struct.unpack(">Q", struct.pack(">d", value))[0]
        stored, field, width, bias = bits & 0xFFFFFFFFFFFFF, (bits >> 52) & 0x7FF, 52, 1075
    if field == 0:
        return stored, 1 - bias, False
    return stored | (1 << width), field - bias, stored == 0 and field > 1


def _shortest_digits(value: float, single: bool) -> tuple[int, int]:
    """``(D, k)``: the decimal ``D * 10**k`` Java's ``toString`` picks for a
    positive finite ``value`` -- the shortest that rounds back to it, the
    closest of those, the even one of two; and when the shortest is one
    digit, the closest of every one- or two-digit decimal that does.
    ``D`` has no trailing zeros."""
    m, e, lower_closer = _binary_parts(value, single)
    exact = Fraction(m) * Fraction(2) ** e
    step = Fraction(2) ** e
    low = exact - (step / 4 if lower_closer else step / 2)
    high = exact + step / 2
    inclusive = m % 2 == 0                           # round half to even

    k = len(str(high.numerator)) - len(str(high.denominator))
    while Fraction(10) ** k > high:
        k -= 1
    while Fraction(10) ** (k + 1) <= high:
        k += 1

    def candidates(scale: int) -> tuple[int, int] | None:
        unit = Fraction(10) ** scale
        lo, hi = low / unit, high / unit
        first = math.ceil(lo)
        if first == lo and not inclusive:
            first += 1
        last = math.floor(hi)
        if last == hi and not inclusive:
            last -= 1
        return (first, last) if first <= last else None

    def closest(scale: int, span: tuple[int, int]) -> int:
        target = exact / Fraction(10) ** scale
        nearest = math.floor(target)
        rest = target - nearest
        if rest > Fraction(1, 2) or (rest == Fraction(1, 2) and nearest & 1):
            nearest += 1
        return min(max(nearest, span[0]), span[1])

    while True:
        span = candidates(k)
        if span is not None:
            break
        k -= 1
    digits = closest(k, span)
    if digits < 10:
        # One digit: Java weighs every decimal of one or two digits, and the
        # two-digit ones sit at two scales when the interval straddles a
        # power of ten (9.9E-324 and 1.2E-323 both round to 1.0E-323's
        # double), so each scale offers its closest and the closest wins.
        best = None
        for scale in (k, k - 1, k - 2):
            span = candidates(scale)
            if span is None or span[0] > 99:
                continue
            pick = closest(scale, (span[0], min(span[1], 99)))
            distance = abs(Fraction(pick) * Fraction(10) ** scale - exact)
            plain = pick
            while plain % 10 == 0:
                plain //= 10
            rank = (distance, plain % 2)
            if best is None or rank < best[0]:
                best = (rank, pick, scale)
        if best is not None:  # always: scale k's own closest is among them
            digits, k = best[1], best[2]
    while digits % 10 == 0:
        digits //= 10
        k += 1
    return digits, k


def _java_decimal(value: float, single: bool) -> str:
    """``Float.toString`` or ``Double.toString`` of ``value``."""
    if value != value:
        return "NaN"
    if value == math.inf:
        return "Infinity"
    if value == -math.inf:
        return "-Infinity"
    if value == 0:
        return "-0.0" if math.copysign(1.0, value) < 0 else "0.0"
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    digits, k = _shortest_digits(magnitude, single)
    text = str(digits)
    point = len(text) + k                            # digits before the point
    exact = Fraction(magnitude)
    if Fraction(1, 1000) <= exact < 10 ** 7:
        if point <= 0:
            return f"{sign}0.{'0' * -point}{text}"
        if point >= len(text):
            return f"{sign}{text}{'0' * (point - len(text))}.0"
        return f"{sign}{text[:point]}.{text[point:]}"
    return f"{sign}{text[0]}.{text[1:] or '0'}E{point - 1}"


def _float32(value: float) -> float:
    """The float a TagFloat holds: its value rounded to 32 bits, as
    ``encode`` writes it."""
    try:
        return struct.unpack(">f", struct.pack(">f", value))[0]
    except OverflowError:
        return math.copysign(math.inf, value)


# --------------------------------------------------------------------------
# Java's UUID.fromString
# --------------------------------------------------------------------------


def _java_hex_long(text: str, where: str) -> int:
    """``Long.parseLong(text, 16)``, ASCII digits only."""
    body = text[1:] if text[:1] in "+-" else text
    if not body:
        raise ValueError(f"no digits in {where}")
    if not all(c in _HEX for c in body):
        if not text.isascii():
            raise ValueError(f"a digit outside ASCII in {where}, which the game "
                             f"reads by Character.digit and this library does not")
        raise ValueError(f"not hexadecimal: {where}")
    magnitude = int(body, 16)
    value = -magnitude if text[0] == "-" else magnitude
    if not -(1 << 63) <= value < (1 << 63):
        raise ValueError(f"out of range: {where}")
    return value


def _java_uuid(text: str) -> int:
    """The 128 bits ``UUID.fromString`` reads from ``text`` (JDK 25)."""
    if (len(text) == 36 and text[8] == text[13] == text[18] == text[23] == "-"):
        nibbles = text[0:8] + text[9:13] + text[14:18] + text[19:23] + text[24:36]
        if all(c in _HEX for c in nibbles):
            return int(nibbles, 16)
    if len(text) > 36:
        raise ValueError("UUID string too large")
    dash1 = text.find("-")
    dash2 = text.find("-", dash1 + 1)
    dash3 = text.find("-", dash2 + 1)
    dash4 = text.find("-", dash3 + 1)
    dash5 = text.find("-", dash4 + 1)
    if dash4 < 0 or dash5 >= 0:
        raise ValueError(f"Invalid UUID string: {text}")
    parts = [text[:dash1], text[dash1 + 1:dash2], text[dash2 + 1:dash3],
             text[dash3 + 1:dash4], text[dash4 + 1:]]
    values = [_java_hex_long(part, repr(part)) for part in parts]
    most = ((values[0] & 0xFFFFFFFF) << 32 | (values[1] & 0xFFFF) << 16
            | values[2] & 0xFFFF)
    least = (values[3] & 0xFFFF) << 48 | values[4] & 0xFFFFFFFFFFFF
    return most << 64 | least


def _signed32(word: int) -> int:
    return word - (1 << 32) if word & 0x80000000 else word


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def _combine_surrogates(text: str) -> str:
    """Join each high surrogate and the low one after it into the character
    they spell, as decoding the binary string would."""
    if not any("\ud800" <= ch <= "\udbff" for ch in text):
        return text
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if "\ud800" <= ch <= "\udbff" and i + 1 < len(text) and "\udc00" <= text[i + 1] <= "\udfff":
            out.append(chr(0x10000 + ((ord(ch) - 0xD800) << 10) + (ord(text[i + 1]) - 0xDC00)))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


class _Parser:
    __slots__ = ("text", "end")

    def __init__(self, text: str):
        self.text = text
        self.end = len(text)

    # -- reporting ---------------------------------------------------------

    def fail(self, message: str, at: int) -> NBTError:
        shown = self.text[max(0, at - 20):at]
        lead = "..." if at > 20 else ""
        return NBTError(f"SNBT: {message} at position {at}: {lead}{shown}<--[HERE]")

    # -- scanning ----------------------------------------------------------

    def skip(self, i: int) -> int:
        while i < self.end and self.text[i] in WHITESPACE:
            i += 1
        return i

    def at(self, i: int) -> str:
        return self.text[i] if i < self.end else ""

    def run(self, i: int, allowed) -> int:
        while i < self.end and self.text[i] in allowed:
            i += 1
        return i

    # -- values ------------------------------------------------------------
    #
    # Containers are read on a stack of their own rather than by recursion,
    # so nesting costs no Python frame: a compound's entries, a list's items
    # and an operation's arguments wait in a frame until it closes, and the
    # value that closes one is handed on to the frame under it.

    def value(self, i: int) -> tuple[Any, int]:
        stack: list[list] = []
        containers = 0
        while True:
            i = self.skip(i)
            c = self.at(i)
            if c == "{" or c == "[":
                inside = self.skip(i + 1)
                semicolon = self.skip(inside + 1)
                if c == "[" and self.at(inside) in _ARRAYS and self.at(semicolon) == ";":
                    value, i = self.array(self.at(inside), semicolon + 1)
                else:
                    if containers >= MAX_DEPTH:
                        raise self.fail(f"nesting deeper than {MAX_DEPTH} levels, which "
                                        f"the game's own binary reader refuses", i)
                    if self.at(inside) == ("}" if c == "{" else "]"):
                        value = TagCompound() if c == "{" else TagList([], TAG_END)
                        i = inside + 1
                    elif c == "{":
                        key, i = self.entry_key(inside)
                        stack.append(["{", TagCompound(), key])
                        containers += 1
                        continue
                    else:
                        stack.append(["[", []])
                        containers += 1
                        i = inside
                        continue
            elif c in ("'", '"'):
                value, i = self.quoted(i)
            elif c and (c in _DIGITS or c in "+-."):
                value, i, _ = self.number(i, "i")
            elif c and c in _WORD_START:
                j = self.run(i, _WORD)
                word = self.text[i:j]
                k = self.skip(j)
                if self.at(k) == "(":
                    k = self.skip(k + 1)
                    if self.at(k) != ")":
                        stack.append(["(", word, []])
                        i = k
                        continue
                    value, i = self.apply(word, [], k + 1), k + 1
                elif word.lower() == "true":
                    value, i = TagByte(1), j
                elif word.lower() == "false":
                    value, i = TagByte(0), j
                else:
                    value, i = TagString(word), j
            else:
                raise self.fail("expected a value", i)

            # The value is whole: hand it to its frame, and close every frame
            # it finishes.
            while stack:
                frame = stack[-1]
                kind = frame[0]
                if kind == "{":
                    frame[1][frame[2]] = value
                elif kind == "[":
                    frame[1].append(value)
                else:
                    frame[2].append(value)
                close = _CLOSERS[kind]
                i = self.skip(i)
                c = self.at(i)
                if c == ",":
                    i = self.skip(i + 1)
                    if self.at(i) != close:
                        if kind == "{":
                            frame[2], i = self.entry_key(i)
                        break                    # on to the frame's next item
                elif c != close:
                    raise self.fail(f"expected ',' or '{close}'", i)
                stack.pop()
                i += 1
                if kind == "{":
                    value = frame[1]
                    containers -= 1
                elif kind == "[":
                    value = _make_list(frame[1])
                    containers -= 1
                else:
                    value = self.apply(frame[1], frame[2], i)
            else:
                return value, i

    def entry_key(self, i: int) -> tuple[str, int]:
        """A compound entry's key and its colon: the key, and where its
        value starts."""
        key, i = self.key(i)
        i = self.skip(i)
        if self.at(i) != ":":
            raise self.fail("expected ':'", i)
        return key, i + 1

    def key(self, i: int) -> tuple[str, int]:
        c = self.at(i)
        if c in ("'", '"'):
            key, j = self.quoted(i)
            if not key:
                raise self.fail("empty key", j)
            return str(key), j
        j = self.run(i, _WORD)
        if j == i:
            raise self.fail("expected a key", i)
        return self.text[i:j], j

    def array(self, prefix: str, i: int) -> tuple[Any, int]:
        cls, letter = _ARRAYS[prefix]
        width = _INTEGERS[letter][1]
        values = []
        i = self.skip(i)
        if self.at(i) == "]":
            return cls([]), i + 1
        while True:
            c = self.at(i)
            if not c or not (c in _DIGITS or c in "+-"):
                raise self.fail("expected an integer", i)
            tag, j, is_float = self.number(i, letter)
            if is_float or _INTEGERS_BY_TAG[type(tag)] > width:
                raise self.fail("invalid array element type", j)
            values.append(int(tag))
            i = self.skip(j)
            c = self.at(i)
            if c == ",":
                i = self.skip(i + 1)
                if self.at(i) == "]":
                    break
            elif c == "]":
                break
            else:
                raise self.fail("expected ',' or ']'", i)
        return cls(values), i + 1

    def quoted(self, i: int) -> tuple[TagString, int]:
        quote = self.text[i]
        out: list[str] = []
        j = i + 1
        text = self.text
        while True:
            if j >= self.end:
                raise self.fail("unterminated string", j)
            c = text[j]
            if c == quote:
                return TagString(_combine_surrogates("".join(out))), j + 1
            if c != "\\":
                out.append(c)
                j += 1
                continue
            e = self.at(j + 1)
            if e in _ESCAPES:
                out.append(_ESCAPES[e])
                j += 2
            elif e in ("x", "u", "U"):
                count = {"x": 2, "u": 4, "U": 8}[e]
                digits = text[j + 2:j + 2 + count]
                if len(digits) != count or not all(d in _HEX for d in digits):
                    raise self.fail(f"expected {count} hex digits", j + 2)
                code = int(digits, 16)
                if code > 0x10FFFF:
                    raise self.fail(f"invalid Unicode character U+{code:08X}", j + 2 + count)
                out.append(chr(code))
                j += 2 + count
            elif e == "N":
                raise self.fail("\\N{...} names a character by the JDK's Unicode name "
                                "table, which this library does not carry; write "
                                "the character, or \\u or \\U its code", j)
            else:
                raise self.fail("invalid escape", j + 1)

    def apply(self, name: str, args: list, i: int) -> Any:
        """A built-in operation's result (``SnbtOperations``), its arguments
        read and its closing parenthesis at ``i - 1``."""
        if name == "bool" and len(args) == 1:
            # true for any number but zero: the game asks the number's
            # doubleValue(), so bool(256) and bool(0.5) are both true
            arg = args[0]
            if not isinstance(arg, _NUMERIC):
                raise self.fail("expected a number or a boolean", i)
            return TagByte(1 if float(arg) != 0 else 0)
        if name == "uuid" and len(args) == 1:
            arg = args[0]
            if not isinstance(arg, TagString):
                raise self.fail("expected a string representing a valid UUID", i)
            try:
                bits = _java_uuid(str(arg))
            except ValueError as exc:
                raise self.fail(f"expected a string representing a valid UUID ({exc})", i) from None
            words = [(bits >> shift) & 0xFFFFFFFF for shift in (96, 64, 32, 0)]
            return TagIntArray([_signed32(w) for w in words])
        raise self.fail(f"no such operation: {name}/{len(args)}", i)

    # -- numbers -----------------------------------------------------------

    def number(self, i: int, default: str) -> tuple[Any, int, bool]:
        """A numeric literal at ``i``: ``(tag, end, is_float)``. An integer
        without a type suffix is of type ``default``.

        The game reads a literal as a sequence of pieces -- the sign, the
        ``0`` and ``x`` or ``b`` of a prefix, each run of digits, the point,
        the ``e`` and its sign, each suffix letter -- and skips whitespace
        before every one of them, though never inside a run of digits, so
        ``- 0 x1F u b`` is the byte 31 and ``1 .5 f`` the float 1.5."""
        text = self.text
        sign = ""
        if self.at(i) in ("+", "-"):
            sign, i = text[i], self.skip(i + 1)
        if self.at(i) == "0":
            prefix = self.skip(i + 1)
            letter = self.at(prefix)
            if letter in ("x", "X"):
                start = self.skip(prefix + 1)
                j = self.run(start, _HEX_DIGITS)
                digits = text[start:j]
                if not digits or digits[0] == "_" or digits[-1] == "_":
                    raise self.fail("expected a hexadecimal number", start)
                return (*self.integer(sign, digits, 16, j, default), False)
            if letter in ("b", "B"):
                start = self.skip(prefix + 1)
                if self.at(start) in ("0", "1"):
                    j = self.run(start, _BINARY_DIGITS)
                    digits = text[start:j]
                    if digits[-1] == "_":
                        raise self.fail("an underscore cannot end a number", j)
                    return (*self.integer(sign, digits, 2, j, default), False)

        j = self.run(i, _DECIMAL_DIGITS) if self.at(i) in _DIGITS else i
        whole = text[i:j]
        if whole.endswith("_"):
            raise self.fail("an underscore cannot end a number", j)
        fraction, has_point = "", False
        k = self.skip(j)
        if self.at(k) == ".":
            has_point = True
            j = k + 1
            k = self.skip(j)
            if self.at(k) in _DIGITS:
                m = self.run(k, _DECIMAL_DIGITS)
                fraction = text[k:m]
                if fraction.endswith("_"):
                    raise self.fail("an underscore cannot end a number", m)
                j = m
        if not whole and not fraction:
            raise self.fail("expected a number", i)
        exponent, has_exponent = "", False
        k = self.skip(j)
        if self.at(k) in ("e", "E"):
            k = self.skip(k + 1)
            exp_sign = ""
            if self.at(k) in ("+", "-"):
                exp_sign, k = text[k], self.skip(k + 1)
            if self.at(k) in _DIGITS:
                m = self.run(k, _DECIMAL_DIGITS)
                digits = text[k:m]
                if digits.endswith("_"):
                    raise self.fail("an underscore cannot end a number", m)
                exponent, has_exponent, j = exp_sign + digits, True, m
        k = self.skip(j)
        float_suffix = self.at(k) in ("f", "F", "d", "D")
        if has_point or has_exponent or float_suffix:
            single = self.at(k) in ("f", "F")
            if float_suffix:
                j = k + 1
            value = _parse_decimal(whole.replace("_", ""), fraction.replace("_", ""),
                                   exponent.replace("_", ""), single)
            if value == math.inf:
                raise self.fail("infinity is not allowed", j)
            # A zero is the positive one whatever its sign: the game's
            # FloatTag.valueOf and DoubleTag.valueOf hand back their ZERO for
            # anything == 0, and -0.0 == 0.0 -- on reading binary too, which
            # is why to_snbt writes a stored -0.0 as 0.0.
            if sign == "-" and value:
                value = -value
            return (TagFloat(value) if single else TagDouble(value)), j, True
        digits = whole.replace("_", "")
        if len(digits) > 1 and digits[0] == "0":
            raise self.fail("a leading zero is not allowed", i)
        return (*self.integer(sign, whole, 10, j, default), False)

    def integer(self, sign: str, digits: str, radix: int, j: int,
                default: str) -> tuple[Any, int]:
        signedness = None
        k = self.skip(j)
        c = self.at(k)
        if c and c in "sSuU":
            after = self.skip(k + 1)
            following = self.at(after)
            if following and following in "bBsSiIlL":
                signedness, k, c = c.lower(), after, following
        if c and c in "bBsSiIlL":
            letter, j = c.lower(), k + 1
        else:
            letter = default
        cls, width = _INTEGERS[letter]
        magnitude = int(digits.replace("_", ""), radix)
        if signedness is None:
            signedness = "s" if radix == 10 else "u"
        if signedness == "u":
            if sign == "-":
                raise self.fail("expected a non-negative number", j)
            if magnitude >= 1 << width:
                raise self.fail(f"out of range for an unsigned {width}-bit integer", j)
            value = magnitude - (1 << width) if magnitude >> (width - 1) else magnitude
        else:
            value = -magnitude if sign == "-" else magnitude
            if not -(1 << (width - 1)) <= value < 1 << (width - 1):
                raise self.fail(f"out of range for a {width}-bit integer", j)
        return cls(value), j


_HEX_DIGITS = frozenset("0123456789abcdefABCDEF_")
_CLOSERS = {"{": "}", "[": "]", "(": ")"}
_BINARY_DIGITS = frozenset("01_")
_DECIMAL_DIGITS = frozenset("0123456789_")
_INTEGERS_BY_TAG = {TagByte: 8, TagShort: 16, TagInt: 32, TagLong: 64}


def _make_list(items: list) -> TagList:
    """A list of the parsed items, stored as the game stores it: as is when
    they share one type, and otherwise as compounds, every item that is not
    already a plain compound wrapped under the empty key."""
    if not items:
        return TagList([], TAG_END)
    first = items[0].ID
    if all(item.ID == first for item in items):
        return TagList(items, first)
    wrapped = [item if isinstance(item, TagCompound) and not _is_wrapper(item)
               else TagCompound({"": item}) for item in items]
    return TagList(wrapped, TAG_COMPOUND)


def _is_wrapper(compound: TagCompound) -> bool:
    return len(compound) == 1 and "" in compound


def parse_snbt(text: str) -> Tag:
    """The tag ``text`` spells, as the game's ``TagParser.parseFully`` reads
    it: one value, with any whitespace Java's ``Character.isWhitespace``
    takes around it, and nothing after. Refuses with :class:`NBTError`,
    naming the position, wherever the game refuses, and in the three places
    this module's docstring lists."""
    if not isinstance(text, str):
        raise TypeError(f"parse_snbt reads a str, not {type(text).__name__}")
    parser = _Parser(text)
    value, i = parser.value(0)
    i = parser.skip(i)
    if i != parser.end:
        raise parser.fail("unexpected trailing data", i)
    return value


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def _quote(text: str) -> str:
    """``StringTag.quoteAndEscape``: in double quotes, unless a double quote
    comes before any single one, then in single; a backslash, the quote
    itself and every character below a space escaped."""
    out = []
    quote = None
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"' or ch == "'":
            if quote is None:
                quote = "'" if ch == '"' else '"'
            if ch == quote:
                out.append("\\")
            out.append(ch)
        elif ch < " ":
            code = ord(ch)
            out.append("\\" + _CONTROL_ESCAPES.get(code, f"x{code:02X}"))
        else:
            out.append(ch)
    quote = quote or '"'
    return quote + "".join(out) + quote


def _utf16_order(key: str) -> bytes:
    """Java's ``String.compareTo`` order, which compares UTF-16 code units."""
    return key.encode("utf-16-be", "surrogatepass")


def _write(tag: Any, out: list, depth: int) -> None:
    if isinstance(tag, TagCompound):
        if depth >= MAX_DEPTH:
            raise NBTError(f"tag nesting exceeds the {MAX_DEPTH}-level limit")
        out.append("{")
        for n, key in enumerate(sorted(tag, key=_utf16_order)):
            if not isinstance(key, str):
                raise NBTError(f"compound key {key!r} is not a string")
            if n:
                out.append(",")
            if key.lower() in ("true", "false") or not _BARE_KEY.match(key):
                out.append(_quote(key))
            else:
                out.append(key)
            out.append(":")
            _write(tag[key], out, depth + 1)
        out.append("}")
    elif isinstance(tag, TagList):
        if depth >= MAX_DEPTH:
            raise NBTError(f"tag nesting exceeds the {MAX_DEPTH}-level limit")
        out.append("[")
        for n, item in enumerate(tag):
            if n:
                out.append(",")
            if isinstance(item, TagCompound) and _is_wrapper(item):
                item = item[""]
            _write(item, out, depth + 1)
        out.append("]")
    elif isinstance(tag, TagString):
        out.append(_quote(tag))
    elif isinstance(tag, TagByte):
        out.append(f"{int(tag)}b")
    elif isinstance(tag, TagShort):
        out.append(f"{int(tag)}s")
    elif isinstance(tag, TagInt):
        out.append(str(int(tag)))
    elif isinstance(tag, TagLong):
        out.append(f"{int(tag)}L")
    elif isinstance(tag, TagFloat):
        # + 0.0 folds a negative zero into the positive one, as the game's
        # FloatTag.valueOf and DoubleTag.valueOf do on every read
        out.append(_java_decimal(_float32(float(tag)) + 0.0, True) + "f")
    elif isinstance(tag, TagDouble):
        out.append(_java_decimal(float(tag) + 0.0, False) + "d")
    elif isinstance(tag, TagByteArray):
        out.append("[B;" + ",".join(f"{v}B" for v in tag) + "]")
    elif isinstance(tag, TagIntArray):
        out.append("[I;" + ",".join(str(v) for v in tag) + "]")
    elif isinstance(tag, TagLongArray):
        out.append("[L;" + ",".join(f"{v}L" for v in tag) + "]")
    else:
        raise NBTError(
            f"cannot write {type(tag).__name__} as SNBT; wrap it in a Tag "
            f"subclass (TagInt, TagString, TagCompound, ...)")


def to_snbt(tag: Tag) -> str:
    """``tag`` as the game's ``Tag.toString()`` writes it -- the compact
    SNBT of ``/data get`` without its colors and spaces, which
    :func:`parse_snbt` reads back into the same tag."""
    out: list[str] = []
    _write(tag, out, 0)
    return "".join(out)
