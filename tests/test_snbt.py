"""SNBT held to the game, case by case.

``fixtures/snbt.json`` is the game's own answer to every case
``tools/probe_snbt.py`` put to it: for each string, the tag
``TagParser.parseFully`` made of it and that tag's ``toString()``, or that it
refused; for each binary tag, its ``toString()``. ``heaton_voxel.snbt`` must
agree with all of it -- the same tag, compound keys in any order, the same
text, and a refusal wherever the game refuses -- but at the three
departures its docstring names, each of which is a refusal here and is
tested as one.
"""

from __future__ import annotations

import base64
import json
import math
import os
import struct
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from heaton_voxel import nbt  # noqa: E402
from heaton_voxel.nbt import (  # noqa: E402
    NBTError,
    TagByte,
    TagCompound,
    TagDouble,
    TagFloat,
    TagInt,
    TagIntArray,
    TagList,
    TagString,
)
from heaton_voxel.snbt import parse_snbt, to_snbt  # noqa: E402

FIXTURE = os.path.join(_ROOT, "fixtures", "snbt.json")


def _load():
    with open(FIXTURE, encoding="ascii") as handle:
        return json.load(handle)


_GAME = _load()


def _game_tag(row):
    _, root = nbt.decode(base64.b64decode(row["nbt"]))
    return root["v"]


def _difference(a, b, path="v") -> str | None:
    """Where two tags differ, or None: the same types and values, list
    order and declared types kept, compound keys in any order, floats to
    the bit."""
    if type(a) is not type(b):
        return f"{path}: {type(a).__name__} against {type(b).__name__}"
    if isinstance(a, TagCompound):
        if set(a) != set(b):
            return f"{path}: keys {sorted(a)} against {sorted(b)}"
        for key in a:
            found = _difference(a[key], b[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(a, TagList):
        if a.element_type != b.element_type or len(a) != len(b):
            return (f"{path}: list of {len(a)} of type {a.element_type} against "
                    f"{len(b)} of type {b.element_type}")
        for i, (x, y) in enumerate(zip(a, b)):
            found = _difference(x, y, f"{path}[{i}]")
            if found:
                return found
        return None
    if isinstance(a, TagFloat):
        if struct.pack(">f", a) != struct.pack(">f", b):
            return f"{path}: {float(a)!r} against {float(b)!r}"
        return None
    if isinstance(a, TagDouble):
        if struct.pack(">d", a) != struct.pack(">d", b):
            return f"{path}: {float(a)!r} against {float(b)!r}"
        return None
    if list(a) != list(b) if not isinstance(a, (int, str)) else a != b:
        return f"{path}: {a!r} against {b!r}"
    return None


def _departure(text: str) -> bool:
    """A string the game reads and this library refuses by design: a
    character named with ``\\N{...}``, or a uuid() with a digit outside
    ASCII."""
    return "\\N{" in text or ("uuid(" in text and not text.isascii())


def _logical(tag):
    """A tag as SNBT can say it: every wrapper the game puts round an item
    of a mixed list taken off, and an empty list's declared type, which no
    text carries, forgotten."""
    if isinstance(tag, TagList):
        items = [_logical(item[""] if isinstance(item, TagCompound) and len(item) == 1
                          and "" in item else item) for item in tag]
        return ("list", tuple(items))
    if isinstance(tag, TagCompound):
        return ("compound", tuple(sorted((k, _logical(v)) for k, v in tag.items())))
    if isinstance(tag, (TagFloat, TagDouble)):
        fmt = ">f" if isinstance(tag, TagFloat) else ">d"
        return (type(tag).__name__, struct.pack(fmt, float(tag) + 0.0))
    if isinstance(tag, (int, str)):
        return (type(tag).__name__, tag)
    return (type(tag).__name__, tuple(tag))


class GameAgreementTests(unittest.TestCase):
    """Every case the game answered, answered the same way."""

    def report(self, failures: list[str], total: int) -> None:
        if failures:
            shown = "\n".join(failures[:25])
            self.fail(f"{len(failures)} of {total} cases disagree with the game:\n{shown}")

    def test_the_fixture_is_the_game_s(self):
        self.assertEqual(_GAME["game"], "26.3")
        self.assertGreater(len(_GAME["parse"]), 2500)
        self.assertGreater(len(_GAME["print"]), 500)

    def test_every_string_parses_as_the_game_parses_it(self):
        failures = []
        for row in _GAME["parse"]:
            text = row["in"]
            accepted = "nbt" in row
            try:
                ours = parse_snbt(text)
            except NBTError as exc:
                if accepted and not _departure(text):
                    failures.append(f"{text!r}: refused ({exc}), the game read {row['str']!r}")
                continue
            if not accepted:
                failures.append(f"{text!r}: read as {to_snbt(ours)!r}, the game refused: "
                                f"{row['error']}")
                continue
            found = _difference(ours, _game_tag(row))
            if found:
                failures.append(f"{text!r}: {found}")
        self.report(failures, len(_GAME["parse"]))

    def test_every_parsed_tag_prints_as_the_game_prints_it(self):
        failures = []
        for row in _GAME["parse"]:
            if "nbt" not in row:
                continue
            ours = to_snbt(_game_tag(row))
            if ours != row["str"]:
                failures.append(f"{row['in']!r}: {ours!r}, the game wrote {row['str']!r}")
        self.report(failures, len(_GAME["parse"]))

    def test_every_binary_tag_prints_as_the_game_prints_it(self):
        failures = []
        for row in _GAME["print"]:
            ours = to_snbt(_game_tag(row))
            if ours != row["str"]:
                failures.append(f"{row['nbt']}: {ours!r}, the game wrote {row['str']!r}")
        self.report(failures, len(_GAME["print"]))

    def test_what_the_game_prints_reads_back_as_the_same_tag(self):
        """``to_snbt`` and ``parse_snbt`` are inverses wherever SNBT can say
        the value at all: no NaN, no infinity, no empty compound key -- and
        a zero is the positive one, as the game reads every zero."""
        failures = []
        for row in _GAME["parse"] + _GAME["print"]:
            if "nbt" not in row:
                continue
            tag = _game_tag(row)
            text = to_snbt(tag)
            if any(word in text for word in ("NaN", "Infinity")) or '"":' in text:
                continue
            try:
                back = parse_snbt(text)
            except NBTError as exc:
                failures.append(f"{text!r}: {exc}")
                continue
            if _logical(back) != _logical(tag):
                failures.append(f"{text!r}: read back as {to_snbt(back)!r}")
        self.report(failures, len(_GAME["parse"]) + len(_GAME["print"]))


class DepartureTests(unittest.TestCase):
    """Where the library refuses what the game reads, it says so."""

    def test_a_named_character_is_refused_by_name(self):
        with self.assertRaisesRegex(NBTError, r"\\N\{\.\.\.\}"):
            parse_snbt('"\\N{SNOWMAN}"')
        self.assertEqual(parse_snbt('"\\u2603"'), "\u2603")

    def test_nesting_stops_where_the_binary_reader_does(self):
        deepest = "[" * nbt.MAX_DEPTH + "]" * nbt.MAX_DEPTH
        tag = parse_snbt(deepest)
        self.assertEqual(to_snbt(tag), deepest)
        with self.assertRaisesRegex(NBTError, "deeper than 512"):
            parse_snbt("[" + deepest + "]")
        with self.assertRaisesRegex(NBTError, "deeper than 512"):
            parse_snbt("{a:" * (nbt.MAX_DEPTH + 1) + "1" + "}" * (nbt.MAX_DEPTH + 1))

    def test_a_uuid_digit_outside_ascii_is_refused_by_name(self):
        with self.assertRaisesRegex(NBTError, "outside ASCII"):
            parse_snbt('uuid("\uff11-1-1-1-1")')


class BehaviorTests(unittest.TestCase):
    """The rules a reader leans on, spelled out."""

    def test_a_mixed_list_is_stored_as_the_game_stores_it(self):
        tag = parse_snbt('[1, "a", {b: 2}]')
        self.assertEqual(tag.element_type, nbt.TAG_COMPOUND)
        self.assertEqual([dict(x) for x in tag], [{"": 1}, {"": "a"}, {"b": 2}])
        self.assertEqual(to_snbt(tag), '[1,"a",{b:2}]')
        # and the bytes are the ones the game reads back as that list
        back = nbt.decode(nbt.encode(TagCompound({"v": tag})))[1]["v"]
        self.assertEqual(to_snbt(back), '[1,"a",{b:2}]')

    def test_keys_are_sorted_as_java_sorts_strings(self):
        tag = TagCompound({"\U0001f600": TagInt(1), "\uffff": TagInt(2), "b": TagInt(3),
                           "B": TagInt(4)})
        # a supplementary character is two UTF-16 units starting 0xD83D,
        # which sorts before 0xFFFF, though its code point is above it
        self.assertEqual(to_snbt(tag), '{B:4,b:3,"\U0001f600":1,"\uffff":2}')

    def test_types_survive_the_text(self):
        self.assertIs(type(parse_snbt("1b")), TagByte)
        self.assertIs(type(parse_snbt("1")), TagInt)
        self.assertIs(type(parse_snbt("1.0")), TagDouble)
        self.assertIs(type(parse_snbt("1f")), TagFloat)
        self.assertIs(type(parse_snbt("true")), TagByte)
        self.assertIs(type(parse_snbt("abc")), TagString)
        self.assertIs(type(parse_snbt('uuid("f81d4fae-7dec-11d0-a765-00a0c91e6bf6")')),
                      TagIntArray)

    def test_floats_print_as_java_prints_them(self):
        cases = [
            (TagFloat(1.0), "1.0f"), (TagFloat(0.1), "0.1f"), (TagFloat(1e7), "1.0E7f"),
            (TagFloat(1.4e-45), "1.4E-45f"), (TagDouble(4.9e-324), "4.9E-324d"),
            (TagDouble(1e-323), "9.9E-324d"), (TagDouble(0.001), "0.001d"),
            (TagDouble(0.0001), "1.0E-4d"), (TagDouble(100.0), "100.0d"),
            (TagDouble(math.inf), "Infinityd"), (TagDouble(math.nan), "NaNd"),
            # every zero is the positive one, as the game reads it
            (TagDouble(-0.0), "0.0d"), (TagFloat(-0.0), "0.0f"),
        ]
        for tag, text in cases:
            with self.subTest(text=text):
                self.assertEqual(to_snbt(tag), text)

    def test_a_float_rounds_once_not_twice(self):
        """``1.00000017881393432617187499f`` is just under the halfway point
        between two floats, and so rounds down; through a double first it
        would land on the halfway point itself and round up to even."""
        tag = parse_snbt("1.00000017881393432617187499f")
        self.assertEqual(struct.pack(">f", tag), struct.pack(">f", 1.0000001192092896))

    def test_a_refusal_names_its_position(self):
        with self.assertRaisesRegex(NBTError, r"position 6"):
            parse_snbt("{a:1b c}")

    def test_only_tags_are_written(self):
        with self.assertRaises(NBTError):
            to_snbt(TagCompound({"a": 1}))
        with self.assertRaises(TypeError):
            parse_snbt(b"1")


if __name__ == "__main__":
    unittest.main()
