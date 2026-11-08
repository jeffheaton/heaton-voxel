"""Ask the game how it reads and writes SNBT, the text form of NBT.

    python tools/probe_snbt.py --version 26.3

SNBT is what commands, data packs and ``/data get`` speak: ``{id:"minecraft:
stone",Count:1b}``. ``heaton_voxel.snbt`` reads it the way the game's
``TagParser`` does and writes it the way ``Tag.toString()`` does
(``StringTagVisitor``), and both answers are the game's to give, so this
asks the game for them over a corpus of cases and records what it said in
``fixtures/snbt.json``, which ``tests/test_snbt.py`` holds the library to
case by case:

* **parse** -- an SNBT string, and either the tag the game parsed it into
  (as uncompressed binary NBT, the value under the key ``v`` of a root
  compound, the way ``NbtIo.write`` writes it) and that tag's
  ``toString()``, or that the game refused it, and why.
* **print** -- binary NBT made here, and the game's ``toString()`` of the
  tag under ``v`` once ``NbtIo.read`` has read it: the floats and doubles
  no SNBT literal can spell (NaN, infinities, every subnormal, negative
  zero), strings no quoted literal carries (lone surrogates, every control
  character), and lists of the wrapper compounds the game writes for a list
  of mixed types.

The cases are hand-picked edges, random well-formed trees spelled every
way the grammar allows, single-character mutations of both, and decimals
on either side of the halfway point between two floats. They are drawn
from a fixed seed, so a rerun on the same game writes the same file. The
game runs as a few lines of Java against the installed jar and its
libraries (``generate_block_defaults.game_classpath``), the way
``tools/probe_block_shapes.py`` does. Regenerate after a game update, and
commit what it writes.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)

from generate_block_defaults import JRE, game_classpath  # noqa: E402

from heaton_voxel import nbt  # noqa: E402

OUT = os.path.join(REPO, "fixtures", "snbt.json")
SEED = 26_3

JAVA = r"""
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import com.google.gson.*;
import net.minecraft.SharedConstants;
import net.minecraft.nbt.*;
import net.minecraft.server.Bootstrap;

public class SnbtProbe {
    public static void main(String[] args) throws Exception {
        SharedConstants.tryDetectVersion();
        Bootstrap.bootStrap();
        JsonObject in = JsonParser.parseString(
            Files.readString(Path.of(args[0]), StandardCharsets.US_ASCII)).getAsJsonObject();
        TagParser<Tag> parser = TagParser.create(NbtOps.INSTANCE);
        StringBuilder out = new StringBuilder();
        out.append("{\"game\":").append(q(SharedConstants.getCurrentVersion().name()));
        out.append(",\n\"data_version\":")
           .append(SharedConstants.getCurrentVersion().dataVersion().version());
        out.append(",\n\"parse\":[\n");
        boolean first = true;
        for (JsonElement e : in.getAsJsonArray("parse")) {
            String s = e.getAsString();
            if (!first) out.append(",\n");
            first = false;
            out.append("{\"in\":").append(q(s));
            try {
                Tag t = parser.parseFully(s);
                out.append(",\"nbt\":").append(q(encode(t)))
                   .append(",\"str\":").append(q(t.toString()));
            } catch (Exception ex) {
                String why = ex.getMessage() == null ? ex.getClass().getSimpleName() : ex.getMessage();
                out.append(",\"error\":").append(q(why));
            }
            out.append("}");
        }
        out.append("\n],\n\"print\":[\n");
        first = true;
        for (JsonElement e : in.getAsJsonArray("print")) {
            if (!first) out.append(",\n");
            first = false;
            byte[] raw = Base64.getDecoder().decode(e.getAsString());
            CompoundTag c = NbtIo.read(new DataInputStream(new ByteArrayInputStream(raw)));
            out.append("{\"nbt\":").append(q(e.getAsString()))
               .append(",\"str\":").append(q(c.get("v").toString())).append("}");
        }
        out.append("\n]}\n");
        Files.writeString(Path.of(args[1]), out.toString(), StandardCharsets.US_ASCII);
    }

    static String encode(Tag t) throws IOException {
        CompoundTag c = new CompoundTag();
        c.put("v", t);
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        NbtIo.write(c, new DataOutputStream(b));
        return Base64.getEncoder().encodeToString(b.toByteArray());
    }

    // A JSON string with every char outside printable ASCII as a \\u escape
    // of its UTF-16 unit, so a lone surrogate survives the trip.
    static String q(String s) {
        StringBuilder b = new StringBuilder("\"");
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '"' || c == '\\') b.append('\\').append(c);
            else if (c >= 0x20 && c < 0x7f) b.append(c);
            else b.append(String.format("\\u%04x", (int) c));
        }
        return b.append('"').toString();
    }
}
"""

# --------------------------------------------------------------------------
# Hand-picked edges
# --------------------------------------------------------------------------

UUID = "f81d4fae-7dec-11d0-a765-00a0c91e6bf6"

HAND = [
    # integers: bases, signs, suffixes, signedness, ranges, underscores
    "0", "-0", "+0", "+5", "-5", "00", "007", "0_1", "10", "1_0", "1__0", "_1",
    "1_", "0x0", "0x_FF", "0xF_F", "0xF_", "0x", "0X1f", "0xff", "0xFFb",
    "0x7Fb", "0xFFsb", "0xFFub", "0x80sb", "-0x80sb", "-0x81sb", "-0x1",
    "-0x1sb", "+0x1", "-0b1sb", "0b", "0b0", "0b1b", "0b2", "0b_1", "0b1_0",
    "0b11111111ub", "0B101", "0b101", "0x80000000", "0xFFFFFFFF",
    "0xFFFFFFFFui", "0xFFFFFFFFsi", "0x7FFFFFFF", "0x100000000",
    "0xFFFFFFFFFFFFFFFFL", "0xFFFFFFFFFFFFFFFFul", "0x8000000000000000sl",
    "-0x8000000000000000sl", "0x1p3", "127b", "128b", "-128b", "-129b",
    "255ub", "256ub", "-1ub", "-0ub", "0ub", "32767s", "32768s", "-32768s",
    "-32769s", "65535us", "65536us", "2147483647", "2147483648",
    "-2147483648", "-2147483649", "4294967295ui", "4294967296ui",
    "9223372036854775807L", "9223372036854775808L", "-9223372036854775808L",
    "-9223372036854775809L", "18446744073709551615ul", "18446744073709551616ul",
    "1b", "1B", "1s", "1S", "1i", "1I", "1l", "1L", "1sb", "1SB", "1ub", "1Ub",
    "1uB", "1ss", "1us", "1si", "1ui", "1sl", "1ul", "1uL", "1bu", "1su", "1u",
    "1x", "1a", "1abc", "1_000L", "1_000_000", "0x1_0000", "0b1111_0000ub",
    "1(2)",
    # floats
    "0.5", "00.5", "01.5", "0.0", "-0.0", "-0.0f", "+0.0", "1.e5", ".5", "-.5",
    "+.5", ".5f", "5.", "-5.", "5.f", "5.d", ".", ".e5", "1e3", "1E3", "1e+3",
    "1e-3", "1e", "1e+", "1e-", "1e_3", "1e3_", "1e1_0", "1_0.0_1", "1_.5",
    "1._5", "1.0_", "1.5_f", "1f", "1d", "1F", "1D", "1.5F", "-1.5d", "00f",
    "00e1", "00d", "0e0", "1e0f", "1.5ub", "1e3b", "1.0L", "1.0s",
    "3.4028235e38f", "3.4028236e38f", "3.40282356e38f", "3.40282357e38f",
    "1e38f", "1e39f", "1e308", "1e309", "1.7976931348623157e308",
    "1.7976931348623158e308", "1.7976931348623159e308", "1e-50f", "1e-400",
    "1.4e-45f", "0.7e-45f", "0.71e-45f", "0.70064923216240862e-45f",
    "4.9e-324", "2.5e-324", "2.4703282292062328e-324", "1e-323",
    "0.1f", "0.1", "0.30000000000000004", "123456789.0f", "16777217f",
    "16777217", "9007199254740993d", "100000.0", "1e7", "9999999.0",
    "10000000.0", "0.001", "0.0001", "1e7f", "1e-3f", "1e-4f",
    "NaN", "nan", "Infinity", "-Infinity", "inf", "1e9999f",
    # booleans and unquoted strings
    "true", "false", "TRUE", "True", "tRuE", "FALSE", "truex", "false1", "tru",
    "abc", "a.b", "a-b", "a+b", "a_b", "_abc", "_", "A", "Z9", "e5", "E", ".abc",
    "-abc", "+abc", "a:b", "minecraft:stone", "\u00e9", "a\u00e9", "B", "I", "L",
    # quoted strings and escapes
    "''", '""', "'a'", '"a"', "'it\"s'", "\"it's\"", "'a\\'b'", '"a\\"b"',
    "'a\\\"b'", '"a\\\'b"', '"\\\\"', '"\\b\\s\\t\\n\\f\\r"', '"\\q"', '"\\"',
    '"\\x41"', '"\\x4"', '"\\x4G"', '"\\x4g"', '"\\xff"', '"\\xFF"', '"\\x00"',
    '"\\u0041"', '"\\u004"', '"\\u00e9"', '"\\u00E9"', '"\\uD800"',
    '"\\uDC00\\uD83D"', '"\\uD83D\\uDE00"', '"\\U0001F600"', '"\\U0001f600"',
    '"\\U00110000"', '"\\U0010FFFF"', '"\\U0000D800"', '"\\U00000041"',
    '"\\U0041"', '"\\N{SNOWMAN}"', '"\\N{snowman}"', '"\\N{NOPE}"', '"\\N{}"',
    '"\\N{LATIN SMALL LETTER A}"', '"\\N"', '"\x01"', '"\n"', '"\t"', '"\x7f"',
    '"\u00e9"', '"\U0001f600"', '"\u2028"', "'\\''", "'\"'", "\"'\"", '"\\\'"',
    '"unterminated', "'unterminated", '"a"b"',
    # compounds
    "{}", "{ }", "{a:1}", "{a:1,}", "{,}", "{a:1,,b:2}", "{a:1 b:2}",
    '{"a":1}', "{'a':1}", '{"":1}', "{'':1}", "{1a:1}", "{-a:1}", "{+a:1}",
    "{.a:1}", "{_:1}", "{1:1}", "{-1:1}", "{true:1}", "{TRUE:1}", "{a.b-c+d_e:1}",
    "{a:1,a:2}", "{a:1,b:2,a:3}", "{a:{}}", "{ a : 1 }", "{a : 1 , b : 2}",
    "{a:}", "{:1}", "{a 1}", "{a:1", "{a:1}}", '{"a b":1}', '{"a\\"b":1}',
    "{'a\\'b':1}", '{"\u00e9":1}', "{a\u00e9:1}", "{a:b:c}", '{"a":"b"}',
    "{b:1,a:2,B:3,_:4,.:5,0:6}", "{a:[],b:{},c:[B;],d:\"\"}",
    # lists and arrays
    "[]", "[ ]", "[1]", "[1,]", "[,]", "[1,,2]", "[1 2]", "[1,2,3]",
    "[1, 2 ,3 ]", '[1,"a"]', "[1,1b]", "[1b,1]", "[[],[1]]", '[[1],["a"]]',
    "[[],[]]", "[[]]", "[{},{a:1}]", "[{a:1},1]", "[{},1]", "[1,[1]]",
    '["a",\'b\']', "[a,b]", "[a,1]", "[B]", "[I]", "[L]", "[B,1]", "[B;]",
    "[B;1b,2b]", "[B;1,2]", "[B;1s]", "[B;1b,]", "[B;,]", "[B;1b,,2b]",
    "[I;1,2]", "[I;1b]", "[I;1s]", "[I;1sb]", "[I;1L]", "[L;1,2L]", "[L;1l]",
    "[L;1b,2s,3i,4L]", "[L;]", "[I;]", "[b;1b]", "[i;1]", "[B; 1b]", "[B ;1b]",
    "[ B;1b]", "[B;1b ,2b]", "[Q;1]", "[BB;1]", "[I;true]", "[B;true]",
    "[B;0xFFub]", "[B;0xFF]", "[B;0x7F]", "[B;-0x1sb]", "[I;0xFFub]",
    "[I;0xFFFFFFFF]", "[L;0xFFFFFFFFFFFFFFFF]", "[B;300]", "[B;-129]",
    "[B;128]", "[I;2147483648]", "[I;1.5]", "[I;1f]", '[I;1,"a"]',
    "[I;bool(1)]", "[I;[1]]", "[B;1b", "[1", "[1]]", "[I;1 2]",
    "[[I;1],[B;1b]]", "[[B;],[I;]]", "[bool(1),1b]", "[true,1b]", "[true,1]",
    '[uuid("' + UUID + '"),[I;1,2,3,4]]',
    # operations
    "bool(1)", "bool(0)", "bool(true)", "bool(false)", "bool(2)", "bool(-1)",
    "bool(256)", "bool(255)", "bool(256s)", "bool(1L)", "bool(4294967296L)",
    "bool(1.5)", "bool(0.5)", "bool(-0.5)", "bool(-1.5)", "bool(0.0)",
    "bool(-0.0)", "bool(256.0)", "bool(1e10)", "bool(-1e10)", "bool(1e300)",
    "bool(1.5f)", "bool(255.9)", "bool(256.5f)", 'bool("x")', "bool([1])",
    "bool({})", "bool([B;1b])", "bool(1,2)", "bool()", "bool( )", "bool(1,)",
    "bool(", "bool(1", "bool (1)", "bool( 1 )", "bool(bool(2))", "BOOL(1)",
    "Bool(1)", "bool", "(1)", "a(1)", "nope(1)", "true(1)", "_(1)",
    'uuid("' + UUID + '")', 'uuid("' + UUID.upper() + '")',
    "uuid('" + UUID + "')", "uuid( '" + UUID + "' )", 'uuid("bad")',
    "uuid(1)", "uuid()", 'uuid("' + UUID + '","x")', 'uuid("1-1-1-1-1")',
    'uuid("0-0-0-0-0")', 'uuid("+1-1-1-1-1")', 'uuid("-1-1-1-1-1")',
    'uuid("1--1-1-1")', 'uuid("-1-1-1-1")', 'uuid("1-1-1-1-")',
    'uuid("1-1-1-1-1-1")', 'uuid("1-1-1-1")', 'uuid("ffffffff-ffff-ffff-ffff-ffffffffffff")',
    'uuid("1ffffffff-1-1-1-1")', 'uuid("ffffffffffffffff-1-1-1-1")',
    'uuid("10000000000000000-1-1-1-1")', 'uuid("1-1-1-1-1ffffffffffff")',
    'uuid("f81d4fae7dec11d0a76500a0c91e6bf6")', 'uuid("' + UUID + ' ")',
    'uuid(" ' + UUID + '")', 'uuid("' + UUID[:35] + 'g")',
    'uuid("' + UUID + '0")', 'uuid("0x1-1-1-1-1")', 'uuid("1_0-1-1-1-1")',
    'uuid("\uff11-1-1-1-1")', 'uuid("\u0661-1-1-1-1")', 'uuid("\uff21-1-1-1-1")',
    'uuid("' + "1" * 36 + '")', 'uuid("1-1-1-1-' + "0" * 33 + '1")',
    'uuid("' + UUID[:8] + "+" + UUID[9:] + '")',
    # whitespace inside a number: before every piece, never inside digits
    "- 5", "+ 5", "-  5b", "0 x1F", "0x 1F", "0x1F ub", "0x1F u b", "0x1F u",
    "0x1 F", "0x1 s b", "1 e5", "1e 5", "1e+ 5", "1e +5", "1e5 f", "5 b",
    "5 sb", "5s b", "5 s b", "0b 101", "0 b101", "0 b", "0 b 1", "0 x", ". 5",
    "1 .5", "1. 5", "1.5 e3", "1 _0", "1_ 0", "1 0", "- .5", "-. 5", "1.5 f",
    "1 f", "1 d", "1 L", "1 s", "1\tb", "5\u3000b", "1 2b", "-\n0x1sb",
    "1 x", "0 0", "1.0 e 5", "1e-\t5", "1.5\u2028f", "-5 ub", "[B; 1 b, 2 b]",
    "[I; 1 s]", "{a: 1 b}", "[1 b, 2]", "bool( 1 b )", "1 e", "1 .", "1 . 5 e 1 f",
    # zero has no sign once read
    "- 0.0", "-0.0e5", "-1e-400", "-1e-50f", "-0x0sb", "-0b0sb", "-0s", "- 0",
    "bool(0.0f)", "bool(1e-400)", "bool(-1e-400)",
    # whitespace and trailing data
    " 1", "1 ", "\t1\n", "1\r\n", "1 2", "{}x", "{} ", " {a : 1 , b : [ 1 , 2 ] } ",
    "", " ", "\u00a01", "\u20281", "\u20291", "\u30001", "\u000b1", "\u000c1",
    "\u001c1", "\u001d1", "\u001e1", "\u001f1", "\u00851", "\u16801", "\u20001",
    "\u20061", "\u20071", "\u20081", "\u200a1", "\u200b1", "\u202f1", "\u205f1",
    "\ufeff1", "\u180e1", "1\u0000", "\u00001",
    # nesting the binary reader still takes
    "[" * 100 + "]" * 100, "[" * 511 + "]" * 511, "{a:" * 300 + "1" + "}" * 300,
]

# --------------------------------------------------------------------------
# Random well-formed trees, spelled every way the grammar allows
# --------------------------------------------------------------------------

KEY_CHARS = "abcAZ_.+-019"
WORD_FIRST = "abcxyzABCXYZ_"
WORD_REST = "abcxyzABCXYZ_019.+-"
STRING_CHARS = ("abcABC 019_.,:;{}[]()!?" "'\"\\" "\u00e9\u00df\u4e00\u2603"
                "\U0001f600\x01\x1f\x7f\t\n\r\x0b\x0c\x08")


def _digits_with_underscores(r: random.Random, digits: str) -> str:
    """Underscores dropped in between digits, never at either end."""
    if len(digits) < 2 or r.random() < 0.7:
        return digits
    out = [digits[0]]
    for d in digits[1:]:
        if r.random() < 0.3:
            out.append("_" * r.choice((1, 1, 2)))
        out.append(d)
    return "".join(out)


def _spell_integer(r: random.Random, value: int, width: int, letter: str,
                   explicit: bool) -> str:
    """``value`` as an integer literal of a type ``width`` bits wide: a
    decimal carrying its sign, or a hex or binary one either carrying its
    sign under an explicit ``s`` or giving the type's bits unsigned."""
    if letter == "i" and not explicit:
        kind = r.choice(("", "", "i", "I"))
    else:
        kind = r.choice((letter, letter.upper()))
    if r.random() < 0.6:
        sign = "-" if value < 0 else r.choice(("", "", "+"))
        signedness = ""
        if kind:
            choices = ["", "", "s", "S"] + (["u", "U"] if value >= 0 else [])
            signedness = r.choice(choices)
        body = _digits_with_underscores(r, str(abs(value)))
        return f"{sign}{body}{signedness}{kind}"
    base = r.choice("xX" if r.random() < 0.7 else "bB")
    if value < 0 or r.random() < 0.3:
        # signed: the sign written, which needs an explicit s and a type
        sign, magnitude = ("-" if value < 0 else ""), abs(value)
        signedness, kind = r.choice("sS"), kind or "i"
    else:
        # unsigned: the type's bits, the default for these bases, but a
        # hex byte must say so, since its b would be read as a digit
        sign, magnitude = "", value & ((1 << width) - 1)
        signedness = r.choice(("", "u", "U")) if kind else ""
        if base in "xX" and kind in ("b", "B") and not signedness:
            signedness = r.choice("uU")
    digits = format(magnitude, "b" if base in "bB" else r.choice("xX"))
    return f"{sign}0{base}{_digits_with_underscores(r, digits)}{signedness}{kind}"


def _random_float32(r: random.Random) -> float:
    while True:
        bits = r.getrandbits(32)
        value = struct.unpack(">f", struct.pack(">I", bits))[0]
        if value == value and abs(value) != float("inf"):
            return value


def _random_float64(r: random.Random) -> float:
    while True:
        bits = r.getrandbits(64)
        value = struct.unpack(">d", struct.pack(">Q", bits))[0]
        if value == value and abs(value) != float("inf"):
            return value


def _spell_float(r: random.Random, value: float, single: bool) -> str:
    text = repr(value)
    if "e" in text:
        mantissa, exponent = text.split("e")
    else:
        mantissa, exponent = text, None
    if r.random() < 0.3 and exponent is None and mantissa.endswith(".0"):
        mantissa = mantissa[:-1]             # "5." is a float too
    if r.random() < 0.2 and mantissa.startswith("0."):
        mantissa = mantissa[1:]              # so is ".5"
    if r.random() < 0.2 and mantissa.startswith("-0."):
        mantissa = "-" + mantissa[2:]
    if exponent is not None:
        exponent = r.choice(("e", "E")) + exponent
    else:
        exponent = ""
    suffix = r.choice(("f", "F")) if single else r.choice(("", "d", "D"))
    if not suffix and "." not in mantissa and not exponent:
        suffix = "d"
    return f"{mantissa}{exponent}{suffix}"


def _spell_string(r: random.Random, text: str) -> str:
    quote = r.choice("\"'")
    out = [quote]
    for ch in text:
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == quote:
            out.append("\\" + quote)
        elif code < 0x20 or r.random() < 0.05:
            escape = {8: "\\b", 9: "\\t", 10: "\\n", 12: "\\f", 13: "\\r", 32: "\\s"}
            if code in escape and r.random() < 0.6:
                out.append(escape[code])
            elif code < 0x100 and r.random() < 0.5:
                out.append("\\x%02x" % code)
            elif code < 0x10000:
                out.append("\\u%04X" % code)
            else:
                out.append("\\U%08x" % code)
        else:
            out.append(ch)
    out.append(quote)
    return "".join(out)


def _ws(r: random.Random) -> str:
    return r.choice(("", "", "", " ", "  ", "\t", "\n", " \n "))


def _random_value(r: random.Random, depth: int, kind: str | None = None) -> str:
    kinds = ["int", "byte", "short", "long", "float", "double", "string",
             "word", "bool"]
    if depth < 4:
        kinds += ["compound", "compound", "list", "list", "array"]
    kind = kind or r.choice(kinds)
    if kind == "int":
        return _spell_integer(r, r.randint(-2**31, 2**31 - 1), 32, "i", False)
    if kind == "byte":
        return _spell_integer(r, r.randint(-128, 127), 8, "b", True)
    if kind == "short":
        return _spell_integer(r, r.randint(-2**15, 2**15 - 1), 16, "s", True)
    if kind == "long":
        return _spell_integer(r, r.randint(-2**63, 2**63 - 1), 64, "l", True)
    if kind == "float":
        value = _random_float32(r) if r.random() < 0.5 else r.choice((0.5, 1.0, -2.25, 3.0))
        return _spell_float(r, value, True)
    if kind == "double":
        value = _random_float64(r) if r.random() < 0.5 else r.uniform(-1e3, 1e3)
        return _spell_float(r, value, False)
    if kind == "string":
        return _spell_string(r, "".join(r.choice(STRING_CHARS) for _ in range(r.randint(0, 8))))
    if kind == "word":
        return r.choice(WORD_FIRST) + "".join(r.choice(WORD_REST) for _ in range(r.randint(0, 6)))
    if kind == "bool":
        return r.choice(("true", "false", "True", "FALSE", "bool(1)", "bool(0)", "bool(2b)"))
    if kind == "compound":
        entries = []
        for _ in range(r.randint(0, 4)):
            if r.random() < 0.6:
                key = "".join(r.choice(KEY_CHARS) for _ in range(r.randint(1, 5)))
            else:
                key = _spell_string(r, "".join(r.choice(STRING_CHARS) for _ in range(r.randint(1, 5))))
            entries.append(f"{_ws(r)}{key}{_ws(r)}:{_ws(r)}{_random_value(r, depth + 1)}{_ws(r)}")
        trailing = "," if entries and r.random() < 0.2 else ""
        return "{" + ",".join(entries) + trailing + "}"
    if kind == "list":
        if r.random() < 0.5:
            # one kind throughout, so the list is (nearly always) of one type
            one = r.choice(kinds)
            items = [_random_value(r, depth + 1, one) for _ in range(r.randint(0, 4))]
        else:
            items = [_random_value(r, depth + 1) for _ in range(r.randint(0, 4))]
        trailing = "," if items and r.random() < 0.2 else ""
        return "[" + ",".join(f"{_ws(r)}{v}{_ws(r)}" for v in items) + trailing + "]"
    prefix = r.choice("BIL")
    width = {"B": 8, "I": 32, "L": 64}[prefix]
    items = []
    for _ in range(r.randint(0, 5)):
        value = r.randint(-2 ** (width - 1), 2 ** (width - 1) - 1)
        letter = {"B": "b", "I": "i", "L": "l"}[prefix]
        items.append(_spell_integer(r, value, width, letter, r.random() < 0.5))
    trailing = "," if items and r.random() < 0.2 else ""
    return "[" + prefix + ";" + ",".join(f"{_ws(r)}{v}" for v in items) + trailing + "]"


MUTATION_CHARS = list("{}[],:;'\"\\ .+-_0179abefxBILsu()") + ["\u00e9", "\n"]


def _mutate(r: random.Random, text: str) -> str:
    chars = list(text)
    for _ in range(r.choice((1, 1, 2))):
        op = r.random()
        at = r.randint(0, len(chars))
        if op < 0.35 and chars:
            del chars[min(at, len(chars) - 1)]
        elif op < 0.7:
            chars.insert(at, r.choice(MUTATION_CHARS))
        elif chars:
            chars[min(at, len(chars) - 1)] = r.choice(MUTATION_CHARS)
    return "".join(chars)


# --------------------------------------------------------------------------
# Rounding: decimals either side of the halfway point between two floats
# --------------------------------------------------------------------------


def _halfway_cases(r: random.Random, count: int, single: bool) -> list[str]:
    """Decimals at and either side of the point halfway between a float and
    the next one up: the exact midpoint, which rounds to the even one, and
    25 significant digits truncated, and one more in the last place."""
    from fractions import Fraction

    fmt, bits_count = (">f", 32) if single else (">d", 64)
    unsigned = ">I" if single else ">Q"
    suffix = "f" if single else "d"
    out = []
    while len(out) < 3 * count:
        bits = r.getrandbits(bits_count - 1)       # positive
        low = struct.unpack(fmt, struct.pack(unsigned, bits))[0]
        high = struct.unpack(fmt, struct.pack(unsigned, bits + 1))[0]
        if low != low or high in (float("inf"), low) or high != high:
            continue
        middle = (Fraction(low) + Fraction(high)) / 2
        twos = middle.denominator.bit_length() - 1      # a power of two
        whole = middle.numerator * 5 ** twos            # middle = whole / 10**twos
        exponent = len(str(whole)) - 1 - twos           # of its leading digit
        if len(str(whole)) <= 200:
            out.append(f"{whole}e-{twos}{suffix}")
        else:
            out.append(f"{whole // 10 ** (len(str(whole)) - 200)}e{exponent - 199}{suffix}")
        scaled = middle * Fraction(10) ** (24 - exponent)
        below = scaled.numerator // scaled.denominator
        for digits in (below, below + 1):
            out.append(f"{digits}e{exponent - 24}{suffix}")
    return out


def cases() -> tuple[list[str], list[str]]:
    """``(parse, print)``: SNBT strings, and base64 binary NBT."""
    r = random.Random(SEED)
    parse = list(HAND)
    valid = [_random_value(r, 0) for _ in range(700)]
    parse += valid
    pool = HAND + valid
    parse += [_mutate(r, r.choice(pool)) for _ in range(1400)]
    parse += _halfway_cases(r, 120, single=True)
    parse += _halfway_cases(r, 60, single=False)
    seen, ordered = set(), []
    for text in parse:
        if text not in seen:
            seen.add(text)
            ordered.append(text)
    return ordered, print_cases(r)


# --------------------------------------------------------------------------
# Tags no literal spells, printed
# --------------------------------------------------------------------------


def _encoded(value) -> str:
    return base64.b64encode(nbt.encode(nbt.TagCompound({"v": value}))).decode("ascii")


def _float_from_bits(bits: int, single: bool):
    if single:
        tag = nbt.TagFloat(struct.unpack(">f", struct.pack(">I", bits))[0])
        raw = struct.pack(">I", bits)
    else:
        tag = nbt.TagDouble(struct.unpack(">d", struct.pack(">Q", bits))[0])
        raw = struct.pack(">Q", bits)
    if (struct.pack(">f", tag) if single else struct.pack(">d", tag)) != raw:
        tag._raw = raw
    return tag


def print_cases(r: random.Random) -> list[str]:
    out = []
    # floats and doubles: special values, subnormals, powers of ten, randoms
    special32 = [0, 0x80000000, 1, 2, 3, 0x007FFFFF, 0x00800000, 0x7F7FFFFF,
                 0x7F800000, 0xFF800000, 0x7FC00000, 0xFFC00001, 0x7F800001,
                 0x3F800000, 0x3DCCCCCD, 0x4B800001, 0x4CBEBC20, 0x3A83126F,
                 0x38D1B717, 0x4B189680, 0x4B18967F]
    special64 = [0, 1 << 63, 1, 2, 0x000FFFFFFFFFFFFF, 0x0010000000000000,
                 0x7FEFFFFFFFFFFFFF, 0x7FF0000000000000, 0xFFF0000000000000,
                 0x7FF8000000000000, 0x7FF0000000000001, 0x3FF0000000000000,
                 0x3FB999999999999A, 0x4415AF1D78B58C40, 0x416312D000000000,
                 0x416312CFFFFFFFFF, 0x3F50624DD2F1A9FC, 0x3F1A36E2EB1C432D]
    for bits in special32:
        out.append(_encoded(_float_from_bits(bits, True)))
    for bits in special64:
        out.append(_encoded(_float_from_bits(bits, False)))
    def single(text: str) -> nbt.TagFloat:
        return nbt.TagFloat(struct.unpack(">f", struct.pack(">f", float(text)))[0])

    for exponent in range(-46, 38):
        out.append(_encoded(nbt.TagList([single(f"1e{exponent}"), single(f"9.999999e{exponent}"),
                                         single(f"1.5e{exponent}")], nbt.TAG_FLOAT)))
    out.append(_encoded(nbt.TagList([single("1e38"), single("3.4e38")], nbt.TAG_FLOAT)))
    for exponent in range(-325, 310, 5):
        out.append(_encoded(nbt.TagList([nbt.TagDouble(float(f"1e{exponent}")),
                                         nbt.TagDouble(float(f"4.9e{exponent}"))],
                                        nbt.TAG_DOUBLE)))
    for _ in range(40):
        out.append(_encoded(nbt.TagList([_float_from_bits(r.getrandbits(32), True)
                                         for _ in range(50)], nbt.TAG_FLOAT)))
        out.append(_encoded(nbt.TagList([_float_from_bits(r.getrandbits(64), False)
                                         for _ in range(50)], nbt.TAG_DOUBLE)))
        subnormal = r.getrandbits(23)
        out.append(_encoded(_float_from_bits(subnormal, True)))
        out.append(_encoded(_float_from_bits(r.getrandbits(52), False)))
    # strings: every control character, lone surrogates, quotes either way
    for code in list(range(0, 0x21)) + [0x7F, 0x85, 0xA0, 0x2028, 0xD800, 0xDBFF,
                                         0xDC00, 0xDFFF, 0xFFFE, 0xFFFF]:
        out.append(_encoded(nbt.TagString(chr(code))))
    for text in ["", "'", '"', "'\"", "\"'", "a'b\"c", "\\", "\\'", "\U0001f600",
                 "\ud83d", "\ude00\ud83d", "x\ud800y", "\u00e9\u4e00"]:
        out.append(_encoded(nbt.TagString(text)))
    for _ in range(60):
        text = "".join(r.choice(STRING_CHARS + "\ud800\udfff") for _ in range(r.randint(0, 10)))
        out.append(_encoded(nbt.TagString(text)))
    # keys: the unquoted pattern, true and false in any case, sort order
    keys = ["a", "A", "_", ".", "+", "-", "0", "a0", "0a", ".a", "+a", "-a", "a+",
            "a-b", "a.b", "true", "TRUE", "True", "false", "fAlse", "truex",
            "", " ", "a b", "\u00e9", "\U0001f600", "\uffff", "\ud800", "B", "b",
            "_a", "Z", "z", "a:b", "'", '"', "\\"]
    out.append(_encoded(nbt.TagCompound({k: nbt.TagByte(i) for i, k in enumerate(keys)})))
    for key in keys:
        out.append(_encoded(nbt.TagCompound({key: nbt.TagInt(1)})))
    for _ in range(20):
        sample = r.sample(keys, r.randint(2, 8))
        out.append(_encoded(nbt.TagCompound({k: nbt.TagString(k) for k in sample})))
    # lists: empty of every declared type, wrappers, wrappers of wrappers
    for element_type in range(0, 13):
        out.append(_encoded(nbt.TagList([], element_type)))
    wrap = lambda v: nbt.TagCompound({"": v})  # noqa: E731
    out.append(_encoded(nbt.TagList([wrap(nbt.TagInt(1)), wrap(nbt.TagString("a"))], nbt.TAG_COMPOUND)))
    out.append(_encoded(nbt.TagList([wrap(nbt.TagInt(1)), nbt.TagCompound({"a": nbt.TagInt(1)})], nbt.TAG_COMPOUND)))
    out.append(_encoded(nbt.TagList([wrap(wrap(nbt.TagInt(1)))], nbt.TAG_COMPOUND)))
    out.append(_encoded(nbt.TagList([nbt.TagCompound({"": nbt.TagInt(1), "a": nbt.TagInt(2)})], nbt.TAG_COMPOUND)))
    out.append(_encoded(nbt.TagList([wrap(nbt.TagList([wrap(nbt.TagByte(1))], nbt.TAG_COMPOUND))], nbt.TAG_COMPOUND)))
    out.append(_encoded(nbt.TagCompound({"": nbt.TagInt(1)})))
    out.append(_encoded(nbt.TagList([nbt.TagCompound()], nbt.TAG_COMPOUND)))
    # arrays at their limits
    out.append(_encoded(nbt.TagByteArray([-128, -1, 0, 1, 127])))
    out.append(_encoded(nbt.TagIntArray([-2**31, -1, 0, 1, 2**31 - 1])))
    out.append(_encoded(nbt.TagLongArray([-2**63, -1, 0, 1, 2**63 - 1])))
    out.append(_encoded(nbt.TagByteArray([])))
    out.append(_encoded(nbt.TagIntArray([])))
    out.append(_encoded(nbt.TagLongArray([])))
    for value in (-128, 127, 0):
        out.append(_encoded(nbt.TagByte(value)))
    for value in (-2**15, 2**15 - 1):
        out.append(_encoded(nbt.TagShort(value)))
    for value in (-2**63, 2**63 - 1):
        out.append(_encoded(nbt.TagLong(value)))
    return out


def probe(version: str, parse: list[str], printed: list[str]) -> dict:
    classpath, _ = game_classpath(version)
    work = tempfile.mkdtemp(prefix="heaton-voxel-snbt-")
    try:
        source = os.path.join(work, "SnbtProbe.java")
        with open(source, "w", encoding="utf-8") as handle:
            handle.write(JAVA)
        given = os.path.join(work, "cases.json")
        with open(given, "w", encoding="ascii") as handle:
            json.dump({"parse": parse, "print": printed}, handle)
        answered = os.path.join(work, "answers.json")
        print(f"asking {version} about {len(parse)} strings and {len(printed)} tags ...",
              file=sys.stderr)
        run = subprocess.run(
            [JRE, "-Xss64m", "-Xmx2G", "-cp", os.pathsep.join(classpath), source,
             given, answered],
            cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if run.returncode != 0:
            raise SystemExit(f"the probe failed -- it needs a JDK, which the "
                             f"launcher's runtime is:\n{run.stdout[-2000:]}")
        with open(answered, encoding="ascii") as handle:
            return json.load(handle)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True, help="an installed game version, e.g. 26.3")
    args = parser.parse_args()
    parse, printed = cases()
    answers = probe(args.version, parse, printed)
    if [case["in"] for case in answers["parse"]] != parse:
        raise SystemExit("the game's answers do not line up with the questions")
    with open(OUT, "w", encoding="ascii") as handle:
        handle.write("{\n")
        handle.write(f'"game": {json.dumps(answers["game"])},\n')
        handle.write(f'"data_version": {answers["data_version"]},\n')
        for name in ("parse", "print"):
            handle.write(f'"{name}": [\n')
            rows = answers[name]
            for i, row in enumerate(rows):
                handle.write(json.dumps(row, ensure_ascii=True, separators=(",", ":")))
                handle.write(",\n" if i + 1 < len(rows) else "\n")
            handle.write("]" + (",\n" if name == "parse" else "\n"))
        handle.write("}\n")
    accepted = sum("nbt" in row for row in answers["parse"])
    print(f"wrote {os.path.relpath(OUT, REPO)}: {len(parse)} strings "
          f"({accepted} accepted) and {len(printed)} tags, from {answers['game']}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
