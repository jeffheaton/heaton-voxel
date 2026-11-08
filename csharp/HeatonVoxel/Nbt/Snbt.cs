// Twin of heaton_voxel/snbt.py: SNBT, the text form of NBT, read the way
// the game's TagParser reads it and written the way Tag.toString() writes
// it. The module docstring there states the grammar, the storage of a list
// of mixed types and the three departures; this file follows it line for
// line. Floats are rounded and printed in exact BigInteger arithmetic, as
// the Python is in Fractions, so both twins give the IEEE-754 answer on
// every runtime, Unity's Mono included.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Numerics;
using System.Text;

namespace HeatonVoxel.Nbt
{
    public static class Snbt
    {
        // -- characters ----------------------------------------------------

        /// <summary>Java's Character.isWhitespace, which the parser skips:
        /// the Unicode space, line and paragraph separators but the three
        /// non-breaking spaces, and nine controls.</summary>
        private static readonly HashSet<char> Whitespace = new HashSet<char>(new[]
        {
            0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x1C, 0x1D, 0x1E, 0x1F, 0x20, 0x1680,
            0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2008,
            0x2009, 0x200A, 0x2028, 0x2029, 0x205F, 0x3000,
        }.Select(code => (char)code));

        private static bool IsLetter(char c) => (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z');
        private static bool IsDigit(char c) => c >= '0' && c <= '9';
        private static bool IsHex(char c) =>
            IsDigit(c) || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
        private static bool IsWord(char c) =>
            IsLetter(c) || IsDigit(c) || c == '_' || c == '-' || c == '.' || c == '+';
        private static bool IsWordStart(char c) => IsLetter(c) || c == '_';
        private static bool IsTypeLetter(char c) => "bBsSiIlL".IndexOf(c) >= 0;

        /// <summary>A key Tag.toString() writes without quotes
        /// (StringTagVisitor): [A-Za-z._][A-Za-z0-9._+-]*.</summary>
        private static bool IsBareKey(string key)
        {
            if (key.Length == 0)
                return false;
            char first = key[0];
            if (!(IsLetter(first) || first == '.' || first == '_'))
                return false;
            for (int i = 1; i < key.Length; i++)
            {
                char c = key[i];
                if (!(IsLetter(c) || IsDigit(c) || c == '.' || c == '_' || c == '+' || c == '-'))
                    return false;
            }
            return true;
        }

        // -- floating point, exactly as Java does it ------------------------

        private readonly struct Format
        {
            public readonly int Bits, QuantumFloor, Overflow;
            public Format(int bits, int quantumFloor, int overflow)
            {
                Bits = bits;
                QuantumFloor = quantumFloor;
                Overflow = overflow;
            }
        }

        private static readonly Format SingleFormat = new Format(24, -149, 128);
        private static readonly Format DoubleFormat = new Format(53, -1074, 1024);

        /// <summary>Python's int.bit_length() of a non-negative value
        /// (netstandard2.1 has no GetBitLength).</summary>
        private static int BitLength(BigInteger value)
        {
            if (value.Sign <= 0)
                return 0;
            byte[] bytes = value.ToByteArray();
            int top = bytes.Length - 1;
            while (top > 0 && bytes[top] == 0)
                top--;
            int bits = top * 8;
            for (int b = bytes[top]; b != 0; b >>= 1)
                bits++;
            return bits;
        }

        private static double Pow2(int exponent)
        {
            if (exponent >= -1022)
                return BitConverter.Int64BitsToDouble((long)(exponent + 1023) << 52);
            return BitConverter.Int64BitsToDouble(1L << (exponent + 1074));
        }

        /// <summary>n/d (positive) rounded to the nearest float or double,
        /// ties to even; +infinity past the largest finite one.</summary>
        private static double RoundBinary(BigInteger n, BigInteger d, Format format)
        {
            int exponent = BitLength(n) - BitLength(d);
            if (exponent >= 0)
            {
                if (n < d << exponent)
                    exponent--;
            }
            else if (n << -exponent < d)
            {
                exponent--;
            }
            int quantum = Math.Max(exponent - format.Bits + 1, format.QuantumFloor);
            BigInteger whole, rest, denominator;
            if (quantum >= 0)
            {
                denominator = d << quantum;
                whole = BigInteger.DivRem(n, denominator, out rest);
            }
            else
            {
                denominator = d;
                whole = BigInteger.DivRem(n << -quantum, d, out rest);
            }
            BigInteger twice = rest * 2;
            if (twice > denominator || (twice == denominator && !whole.IsEven))
                whole += 1;
            if (BitLength(whole) + quantum > format.Overflow)
                return double.PositiveInfinity;
            return (double)(long)whole * Pow2(quantum);
        }

        private static double ParseDecimal(string whole, string fraction, string exponent, bool single)
        {
            string all = whole + fraction;
            BigInteger digits = all.Length == 0 ? BigInteger.Zero : BigInteger.Parse(all, CultureInfo.InvariantCulture);
            if (digits.IsZero)
                return 0.0;
            BigInteger exact = exponent.Length == 0
                ? BigInteger.Zero
                : BigInteger.Parse(exponent, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture);
            BigInteger magnitude = digits.ToString(CultureInfo.InvariantCulture).Length + exact - fraction.Length;
            // Too far out to round: past infinity or below half the least
            // subnormal.
            if (magnitude > (single ? 40 : 310))
                return double.PositiveInfinity;
            if (magnitude < (single ? -47 : -325))
                return 0.0;
            int power = (int)(exact - fraction.Length);
            BigInteger n = digits, d = BigInteger.One;
            if (power >= 0)
                n *= BigInteger.Pow(10, power);
            else
                d = BigInteger.Pow(10, -power);
            return RoundBinary(n, d, single ? SingleFormat : DoubleFormat);
        }

        /// <summary>(m, e, lowerCloser) for a positive finite value: it is
        /// m * 2**e, and lowerCloser says the float below is half as far as
        /// the one above (a power of two past the subnormals).</summary>
        private static (long M, int E, bool LowerCloser) BinaryParts(double value, bool single)
        {
            long stored;
            int field, width, bias;
            if (single)
            {
                int bits = BitConverter.SingleToInt32Bits((float)value);
                stored = bits & 0x7FFFFF;
                field = (bits >> 23) & 0xFF;
                width = 23;
                bias = 150;
            }
            else
            {
                long bits = BitConverter.DoubleToInt64Bits(value);
                stored = bits & 0xFFFFFFFFFFFFFL;
                field = (int)((bits >> 52) & 0x7FF);
                width = 52;
                bias = 1075;
            }
            if (field == 0)
                return (stored, 1 - bias, false);
            return (stored | (1L << width), field - bias, stored == 0 && field > 1);
        }

        /// <summary>An exact positive rational, num / den.</summary>
        private readonly struct Ratio
        {
            public readonly BigInteger Num, Den;
            public Ratio(BigInteger num, BigInteger den) { Num = num; Den = den; }

            public static Ratio PowTen(int k) => k >= 0
                ? new Ratio(BigInteger.Pow(10, k), BigInteger.One)
                : new Ratio(BigInteger.One, BigInteger.Pow(10, -k));

            public static int Compare(Ratio a, Ratio b) => (a.Num * b.Den).CompareTo(b.Num * a.Den);

            public Ratio Divide(Ratio by) => new Ratio(Num * by.Den, Den * by.Num);

            public BigInteger Floor() => BigInteger.Divide(Num, Den);

            public bool IsInteger => (Num % Den).IsZero;
        }

        /// <summary>(D, k): the decimal D * 10**k Java's toString picks for
        /// a positive finite value -- the shortest that rounds back, the
        /// closest of those, the even one of two; and when the shortest is
        /// one digit, the closest of every one- or two-digit decimal that
        /// does. D has no trailing zeros.</summary>
        private static (BigInteger Digits, int K) ShortestDigits(double value, bool single)
        {
            var (m, e, lowerCloser) = BinaryParts(value, single);
            // Everything in quarter steps: 2**(e-2) is the unit.
            BigInteger unitNum = e - 2 >= 0 ? BigInteger.One << (e - 2) : BigInteger.One;
            BigInteger unitDen = e - 2 >= 0 ? BigInteger.One : BigInteger.One << (2 - e);
            var exact = new Ratio(new BigInteger(m) * 4 * unitNum, unitDen);
            var low = new Ratio((new BigInteger(m) * 4 - (lowerCloser ? 1 : 2)) * unitNum, unitDen);
            var high = new Ratio((new BigInteger(m) * 4 + 2) * unitNum, unitDen);
            bool inclusive = m % 2 == 0;

            int k = high.Num.ToString(CultureInfo.InvariantCulture).Length
                    - high.Den.ToString(CultureInfo.InvariantCulture).Length;
            while (Ratio.Compare(Ratio.PowTen(k), high) > 0)
                k--;
            while (Ratio.Compare(Ratio.PowTen(k + 1), high) <= 0)
                k++;

            (BigInteger First, BigInteger Last)? Candidates(int scale)
            {
                var unit = Ratio.PowTen(scale);
                var lo = low.Divide(unit);
                var hi = high.Divide(unit);
                BigInteger first = lo.Floor();      // then the ceiling
                if (!lo.IsInteger || !inclusive)
                    first += 1;
                BigInteger last = hi.Floor();
                if (hi.IsInteger && !inclusive)
                    last -= 1;
                return first <= last ? (first, last) : ((BigInteger, BigInteger)?)null;
            }

            BigInteger Closest(int scale, BigInteger first, BigInteger last)
            {
                var target = exact.Divide(Ratio.PowTen(scale));
                BigInteger nearest = target.Floor();
                BigInteger twice = (target.Num - nearest * target.Den) * 2;
                int side = twice.CompareTo(target.Den);
                if (side > 0 || (side == 0 && !nearest.IsEven))
                    nearest += 1;
                if (nearest < first) return first;
                if (nearest > last) return last;
                return nearest;
            }

            (BigInteger, BigInteger) span;
            while (true)
            {
                var found = Candidates(k);
                if (found.HasValue)
                {
                    span = found.Value;
                    break;
                }
                k--;
            }
            BigInteger digits = Closest(k, span.Item1, span.Item2);
            if (digits < 10)
            {
                // One digit: Java weighs every decimal of one or two digits,
                // at the two scales they sit at when the interval straddles
                // a power of ten; each scale offers its closest, the closest
                // wins.
                (Ratio Distance, int Odd, BigInteger Pick, int Scale)? best = null;
                foreach (int scale in new[] { k, k - 1, k - 2 })
                {
                    var found = Candidates(scale);
                    if (!found.HasValue || found.Value.First > 99)
                        continue;
                    BigInteger last = BigInteger.Min(found.Value.Last, 99);
                    BigInteger pick = Closest(scale, found.Value.First, last);
                    var unit = Ratio.PowTen(scale);
                    // |pick * 10**scale - exact|
                    BigInteger num = pick * unit.Num * exact.Den - exact.Num * unit.Den;
                    var distance = new Ratio(BigInteger.Abs(num), unit.Den * exact.Den);
                    BigInteger plain = pick;
                    while (!plain.IsZero && (plain % 10).IsZero)
                        plain /= 10;
                    int odd = plain.IsEven ? 0 : 1;
                    if (best == null)
                    {
                        best = (distance, odd, pick, scale);
                        continue;
                    }
                    int against = Ratio.Compare(distance, best.Value.Distance);
                    if (against < 0 || (against == 0 && odd < best.Value.Odd))
                        best = (distance, odd, pick, scale);
                }
                digits = best!.Value.Pick;
                k = best.Value.Scale;
            }
            while ((digits % 10).IsZero)
            {
                digits /= 10;
                k++;
            }
            return (digits, k);
        }

        /// <summary>Float.toString or Double.toString of value.</summary>
        private static string JavaDecimal(double value, bool single)
        {
            if (double.IsNaN(value))
                return "NaN";
            if (double.IsPositiveInfinity(value))
                return "Infinity";
            if (double.IsNegativeInfinity(value))
                return "-Infinity";
            if (value == 0)
                return BitConverter.DoubleToInt64Bits(value) < 0 ? "-0.0" : "0.0";
            string sign = value < 0 ? "-" : "";
            double magnitude = Math.Abs(value);
            var (digits, k) = ShortestDigits(magnitude, single);
            string text = digits.ToString(CultureInfo.InvariantCulture);
            int point = text.Length + k;
            var (m, e, _) = BinaryParts(magnitude, single);
            BigInteger num = e >= 0 ? new BigInteger(m) << e : new BigInteger(m);
            BigInteger den = e >= 0 ? BigInteger.One : BigInteger.One << -e;
            bool plain = num * 1000 >= den && num < den * 10_000_000;
            if (plain)
            {
                if (point <= 0)
                    return $"{sign}0.{new string('0', -point)}{text}";
                if (point >= text.Length)
                    return $"{sign}{text}{new string('0', point - text.Length)}.0";
                return $"{sign}{text.Substring(0, point)}.{text.Substring(point)}";
            }
            string rest = text.Length > 1 ? text.Substring(1) : "0";
            return $"{sign}{text[0]}.{rest}E{(point - 1).ToString(CultureInfo.InvariantCulture)}";
        }

        // -- Java's UUID.fromString -----------------------------------------

        private static long JavaHexLong(string text, string where)
        {
            string body = text.Length > 0 && (text[0] == '+' || text[0] == '-') ? text.Substring(1) : text;
            if (body.Length == 0)
                throw new FormatException($"no digits in {where}");
            foreach (char c in body)
            {
                if (!IsHex(c))
                {
                    if (text.Any(ch => ch > 0x7F))
                        throw new FormatException($"a digit outside ASCII in {where}, which the game "
                                                  + "reads by Character.digit and this library does not");
                    throw new FormatException($"not hexadecimal: {where}");
                }
            }
            BigInteger magnitude = BigInteger.Zero;
            foreach (char c in body)
                magnitude = magnitude * 16 + Convert.ToInt32(c.ToString(), 16);
            BigInteger value = text[0] == '-' ? -magnitude : magnitude;
            if (value < long.MinValue || value > long.MaxValue)
                throw new FormatException($"out of range: {where}");
            return (long)value;
        }

        /// <summary>The 128 bits UUID.fromString reads (JDK 25): the high
        /// and low 64.</summary>
        private static (ulong Most, ulong Least) JavaUuid(string text)
        {
            if (text.Length == 36 && text[8] == '-' && text[13] == '-' && text[18] == '-' && text[23] == '-')
            {
                string nibbles = text.Substring(0, 8) + text.Substring(9, 4) + text.Substring(14, 4)
                                 + text.Substring(19, 4) + text.Substring(24, 12);
                if (nibbles.All(IsHex))
                    return (ulong.Parse(nibbles.Substring(0, 16), NumberStyles.HexNumber, CultureInfo.InvariantCulture),
                            ulong.Parse(nibbles.Substring(16), NumberStyles.HexNumber, CultureInfo.InvariantCulture));
            }
            if (text.Length > 36)
                throw new FormatException("UUID string too large");
            int dash1 = text.IndexOf('-');
            int dash2 = text.IndexOf('-', dash1 + 1);
            int dash3 = dash2 < 0 ? -1 : text.IndexOf('-', dash2 + 1);
            int dash4 = dash3 < 0 ? -1 : text.IndexOf('-', dash3 + 1);
            int dash5 = dash4 < 0 ? -1 : text.IndexOf('-', dash4 + 1);
            if (dash4 < 0 || dash5 >= 0)
                throw new FormatException($"Invalid UUID string: {text}");
            string[] parts =
            {
                text.Substring(0, dash1), text.Substring(dash1 + 1, dash2 - dash1 - 1),
                text.Substring(dash2 + 1, dash3 - dash2 - 1), text.Substring(dash3 + 1, dash4 - dash3 - 1),
                text.Substring(dash4 + 1),
            };
            long[] values = parts.Select(p => JavaHexLong(p, "'" + p + "'")).ToArray();
            ulong most = ((ulong)values[0] & 0xFFFFFFFFUL) << 32 | ((ulong)values[1] & 0xFFFFUL) << 16
                         | ((ulong)values[2] & 0xFFFFUL);
            ulong least = ((ulong)values[3] & 0xFFFFUL) << 48 | ((ulong)values[4] & 0xFFFFFFFFFFFFUL);
            return (most, least);
        }

        // -- parsing ---------------------------------------------------------

        private sealed class Parser
        {
            private readonly string _text;
            private readonly int _end;

            public Parser(string text)
            {
                _text = text;
                _end = text.Length;
            }

            public NbtException Fail(string message, int at)
            {
                int from = Math.Max(0, at - 20);
                string shown = _text.Substring(from, Math.Min(at, _end) - from);
                string lead = at > 20 ? "..." : "";
                return new NbtException($"SNBT: {message} at position {at}: {lead}{shown}<--[HERE]");
            }

            public int Skip(int i)
            {
                while (i < _end && Whitespace.Contains(_text[i]))
                    i++;
                return i;
            }

            private char At(int i) => i < _end ? _text[i] : '\0';
            private bool Has(int i) => i < _end;

            private int Run(int i, Func<char, bool> allowed)
            {
                while (i < _end && allowed(_text[i]))
                    i++;
                return i;
            }

            // Containers are read on a stack of their own rather than by
            // recursion, so nesting costs no frame: a compound's entries, a
            // list's items and an operation's arguments wait in a frame
            // until it closes, and the value that closes one is handed on to
            // the frame under it.
            private sealed class Frame
            {
                public char Kind;
                public TagCompound? Compound;
                public string? Key;
                public List<Tag>? Items;
                public string? Name;
            }

            public (Tag Value, int End) Value(int i)
            {
                var stack = new List<Frame>();
                int containers = 0;
                while (true)
                {
                    i = Skip(i);
                    char c = At(i);
                    Tag value;
                    if (Has(i) && (c == '{' || c == '['))
                    {
                        int inside = Skip(i + 1);
                        int semicolon = Skip(inside + 1);
                        if (c == '[' && Has(inside) && "BIL".IndexOf(At(inside)) >= 0 && At(semicolon) == ';' && Has(semicolon))
                        {
                            (value, i) = Array(At(inside), semicolon + 1);
                        }
                        else
                        {
                            if (containers >= Codec.MaxDepth)
                                throw Fail($"nesting deeper than {Codec.MaxDepth} levels, which "
                                           + "the game's own binary reader refuses", i);
                            if (Has(inside) && At(inside) == (c == '{' ? '}' : ']'))
                            {
                                value = c == '{' ? new TagCompound() : (Tag)new TagList(TagId.End);
                                i = inside + 1;
                            }
                            else if (c == '{')
                            {
                                var (key, next) = EntryKey(inside);
                                stack.Add(new Frame { Kind = '{', Compound = new TagCompound(), Key = key });
                                containers++;
                                i = next;
                                continue;
                            }
                            else
                            {
                                stack.Add(new Frame { Kind = '[', Items = new List<Tag>() });
                                containers++;
                                i = inside;
                                continue;
                            }
                        }
                    }
                    else if (Has(i) && (c == '\'' || c == '"'))
                    {
                        TagString quoted;
                        (quoted, i) = Quoted(i);
                        value = quoted;
                    }
                    else if (Has(i) && (IsDigit(c) || c == '+' || c == '-' || c == '.'))
                    {
                        (value, i, _) = Number(i, 'i');
                    }
                    else if (Has(i) && IsWordStart(c))
                    {
                        int j = Run(i, IsWord);
                        string word = _text.Substring(i, j - i);
                        int k = Skip(j);
                        if (Has(k) && At(k) == '(')
                        {
                            k = Skip(k + 1);
                            if (!(Has(k) && At(k) == ')'))
                            {
                                stack.Add(new Frame { Kind = '(', Name = word, Items = new List<Tag>() });
                                i = k;
                                continue;
                            }
                            value = Apply(word, new List<Tag>(), k + 1);
                            i = k + 1;
                        }
                        else if (string.Equals(word, "true", StringComparison.OrdinalIgnoreCase))
                        {
                            value = new TagByte(1);
                            i = j;
                        }
                        else if (string.Equals(word, "false", StringComparison.OrdinalIgnoreCase))
                        {
                            value = new TagByte(0);
                            i = j;
                        }
                        else
                        {
                            value = new TagString(word);
                            i = j;
                        }
                    }
                    else
                    {
                        throw Fail("expected a value", i);
                    }

                    // The value is whole: hand it to its frame, and close
                    // every frame it finishes.
                    while (true)
                    {
                        if (stack.Count == 0)
                            return (value, i);
                        var frame = stack[stack.Count - 1];
                        if (frame.Kind == '{')
                            frame.Compound![frame.Key!] = value;
                        else
                            frame.Items!.Add(value);
                        char close = frame.Kind == '{' ? '}' : frame.Kind == '[' ? ']' : ')';
                        i = Skip(i);
                        char next = At(i);
                        if (Has(i) && next == ',')
                        {
                            i = Skip(i + 1);
                            if (!(Has(i) && At(i) == close))
                            {
                                if (frame.Kind == '{')
                                    (frame.Key, i) = EntryKey(i);
                                break;              // on to the frame's next item
                            }
                        }
                        else if (!(Has(i) && next == close))
                        {
                            throw Fail($"expected ',' or '{close}'", i);
                        }
                        stack.RemoveAt(stack.Count - 1);
                        i++;
                        if (frame.Kind == '{')
                        {
                            value = frame.Compound!;
                            containers--;
                        }
                        else if (frame.Kind == '[')
                        {
                            value = MakeList(frame.Items!);
                            containers--;
                        }
                        else
                        {
                            value = Apply(frame.Name!, frame.Items!, i);
                        }
                    }
                }
            }

            private (string Key, int End) EntryKey(int i)
            {
                var (key, j) = Key(i);
                j = Skip(j);
                if (!(Has(j) && At(j) == ':'))
                    throw Fail("expected ':'", j);
                return (key, j + 1);
            }

            private (string Key, int End) Key(int i)
            {
                char c = At(i);
                if (Has(i) && (c == '\'' || c == '"'))
                {
                    var (key, j) = Quoted(i);
                    if (key.Value.Length == 0)
                        throw Fail("empty key", j);
                    return (key.Value, j);
                }
                int end = Run(i, IsWord);
                if (end == i)
                    throw Fail("expected a key", i);
                return (_text.Substring(i, end - i), end);
            }

            private (Tag Value, int End) Array(char prefix, int i)
            {
                char letter = prefix == 'B' ? 'b' : prefix == 'I' ? 'i' : 'l';
                int width = Width(letter);
                var values = new List<long>();
                i = Skip(i);
                if (!(Has(i) && At(i) == ']'))
                {
                    while (true)
                    {
                        char c = At(i);
                        if (!Has(i) || !(IsDigit(c) || c == '+' || c == '-'))
                            throw Fail("expected an integer", i);
                        var (tag, j, isFloat) = Number(i, letter);
                        if (isFloat || Width(tag) > width)
                            throw Fail("invalid array element type", j);
                        values.Add(Tag.AsLong(tag));
                        i = Skip(j);
                        char next = At(i);
                        if (Has(i) && next == ',')
                        {
                            i = Skip(i + 1);
                            if (Has(i) && At(i) == ']')
                                break;
                        }
                        else if (Has(i) && next == ']')
                        {
                            break;
                        }
                        else
                        {
                            throw Fail("expected ',' or ']'", i);
                        }
                    }
                }
                Tag result = prefix switch
                {
                    'B' => new TagByteArray(values.Select(v => (sbyte)v).ToArray()),
                    'I' => new TagIntArray(values.Select(v => (int)v).ToArray()),
                    _ => new TagLongArray(values.ToArray()),
                };
                return (result, i + 1);
            }

            private (TagString Value, int End) Quoted(int i)
            {
                char quote = _text[i];
                var out_ = new StringBuilder();
                int j = i + 1;
                while (true)
                {
                    if (j >= _end)
                        throw Fail("unterminated string", j);
                    char c = _text[j];
                    if (c == quote)
                        return (new TagString(out_.ToString()), j + 1);
                    if (c != '\\')
                    {
                        out_.Append(c);
                        j++;
                        continue;
                    }
                    char e = At(j + 1);
                    if (!Has(j + 1))
                        throw Fail("invalid escape", j + 1);
                    switch (e)
                    {
                        case 'b': out_.Append('\b'); j += 2; continue;
                        case 's': out_.Append(' '); j += 2; continue;
                        case 't': out_.Append('\t'); j += 2; continue;
                        case 'n': out_.Append('\n'); j += 2; continue;
                        case 'f': out_.Append('\f'); j += 2; continue;
                        case 'r': out_.Append('\r'); j += 2; continue;
                        case '\\': out_.Append('\\'); j += 2; continue;
                        case '\'': out_.Append('\''); j += 2; continue;
                        case '"': out_.Append('"'); j += 2; continue;
                    }
                    if (e == 'x' || e == 'u' || e == 'U')
                    {
                        int count = e == 'x' ? 2 : e == 'u' ? 4 : 8;
                        if (j + 2 + count > _end)
                            throw Fail($"expected {count} hex digits", j + 2);
                        string digits = _text.Substring(j + 2, count);
                        if (!digits.All(IsHex))
                            throw Fail($"expected {count} hex digits", j + 2);
                        long code = long.Parse(digits, NumberStyles.HexNumber, CultureInfo.InvariantCulture);
                        if (code > 0x10FFFF)
                            throw Fail($"invalid Unicode character U+{code:X8}", j + 2 + count);
                        if (code >= 0x10000)
                        {
                            code -= 0x10000;
                            out_.Append((char)(0xD800 + (code >> 10)));
                            out_.Append((char)(0xDC00 + (code & 0x3FF)));
                        }
                        else
                        {
                            out_.Append((char)code);
                        }
                        j += 2 + count;
                    }
                    else if (e == 'N')
                    {
                        throw Fail("\\N{...} names a character by the JDK's Unicode name table, "
                                   + "which this library does not carry; write the character, "
                                   + "or \\u or \\U its code", j);
                    }
                    else
                    {
                        throw Fail("invalid escape", j + 1);
                    }
                }
            }

            /// <summary>A built-in operation's result (SnbtOperations), its
            /// arguments read and its closing parenthesis at i - 1.</summary>
            private Tag Apply(string name, List<Tag> args, int i)
            {
                if (name == "bool" && args.Count == 1)
                {
                    // true for any number but zero: the game asks the
                    // number's doubleValue(), so bool(256) and bool(0.5) are
                    // both true
                    var arg = args[0];
                    if (!(arg is TagByte || arg is TagShort || arg is TagInt || arg is TagLong
                          || arg is TagFloat || arg is TagDouble))
                        throw Fail("expected a number or a boolean", i);
                    return new TagByte(Tag.AsDouble(arg) != 0 ? (sbyte)1 : (sbyte)0);
                }
                if (name == "uuid" && args.Count == 1)
                {
                    if (!(args[0] is TagString text))
                        throw Fail("expected a string representing a valid UUID", i);
                    ulong most, least;
                    try
                    {
                        (most, least) = JavaUuid(text.Value);
                    }
                    catch (FormatException exc)
                    {
                        throw Fail($"expected a string representing a valid UUID ({exc.Message})", i);
                    }
                    return new TagIntArray(new[]
                    {
                        unchecked((int)(most >> 32)), unchecked((int)most),
                        unchecked((int)(least >> 32)), unchecked((int)least),
                    });
                }
                throw Fail($"no such operation: {name}/{args.Count}", i);
            }

            // -- numbers -----------------------------------------------------

            /// <summary>A numeric literal at i: (tag, end, isFloat). An
            /// integer without a type suffix is of type defaultLetter. The
            /// game skips whitespace before every piece of a literal -- the
            /// sign, the 0 and x or b of a prefix, each run of digits, the
            /// point, the e and its sign, each suffix letter -- though never
            /// inside a run of digits.</summary>
            public (Tag Value, int End, bool IsFloat) Number(int i, char defaultLetter)
            {
                char sign = '\0';
                if (Has(i) && (At(i) == '+' || At(i) == '-'))
                {
                    sign = At(i);
                    i = Skip(i + 1);
                }
                if (Has(i) && At(i) == '0')
                {
                    int prefix = Skip(i + 1);
                    char letter = At(prefix);
                    if (Has(prefix) && (letter == 'x' || letter == 'X'))
                    {
                        int start = Skip(prefix + 1);
                        int j = Run(start, ch => IsHex(ch) || ch == '_');
                        string digits = _text.Substring(start, j - start);
                        if (digits.Length == 0 || digits[0] == '_' || digits[digits.Length - 1] == '_')
                            throw Fail("expected a hexadecimal number", start);
                        var (tag, end) = Integer(sign, digits, 16, j, defaultLetter);
                        return (tag, end, false);
                    }
                    if (Has(prefix) && (letter == 'b' || letter == 'B'))
                    {
                        int start = Skip(prefix + 1);
                        if (Has(start) && (At(start) == '0' || At(start) == '1'))
                        {
                            int j = Run(start, ch => ch == '0' || ch == '1' || ch == '_');
                            string digits = _text.Substring(start, j - start);
                            if (digits[digits.Length - 1] == '_')
                                throw Fail("an underscore cannot end a number", j);
                            var (tag, end) = Integer(sign, digits, 2, j, defaultLetter);
                            return (tag, end, false);
                        }
                    }
                }

                bool DecimalDigit(char ch) => IsDigit(ch) || ch == '_';
                int jw = Has(i) && IsDigit(At(i)) ? Run(i, DecimalDigit) : i;
                string whole = _text.Substring(i, jw - i);
                if (whole.EndsWith("_", StringComparison.Ordinal))
                    throw Fail("an underscore cannot end a number", jw);
                string fraction = "";
                bool hasPoint = false;
                int end_ = jw;
                int k = Skip(end_);
                if (Has(k) && At(k) == '.')
                {
                    hasPoint = true;
                    end_ = k + 1;
                    k = Skip(end_);
                    if (Has(k) && IsDigit(At(k)))
                    {
                        int m = Run(k, DecimalDigit);
                        fraction = _text.Substring(k, m - k);
                        if (fraction.EndsWith("_", StringComparison.Ordinal))
                            throw Fail("an underscore cannot end a number", m);
                        end_ = m;
                    }
                }
                if (whole.Length == 0 && fraction.Length == 0)
                    throw Fail("expected a number", i);
                string exponent = "";
                bool hasExponent = false;
                k = Skip(end_);
                if (Has(k) && (At(k) == 'e' || At(k) == 'E'))
                {
                    k = Skip(k + 1);
                    string expSign = "";
                    if (Has(k) && (At(k) == '+' || At(k) == '-'))
                    {
                        expSign = At(k).ToString();
                        k = Skip(k + 1);
                    }
                    if (Has(k) && IsDigit(At(k)))
                    {
                        int m = Run(k, DecimalDigit);
                        string digits = _text.Substring(k, m - k);
                        if (digits.EndsWith("_", StringComparison.Ordinal))
                            throw Fail("an underscore cannot end a number", m);
                        exponent = expSign + digits;
                        hasExponent = true;
                        end_ = m;
                    }
                }
                k = Skip(end_);
                char suffix = At(k);
                bool floatSuffix = Has(k) && "fFdD".IndexOf(suffix) >= 0;
                if (hasPoint || hasExponent || floatSuffix)
                {
                    bool single = floatSuffix && (suffix == 'f' || suffix == 'F');
                    if (floatSuffix)
                        end_ = k + 1;
                    double value = ParseDecimal(whole.Replace("_", ""), fraction.Replace("_", ""),
                                                exponent.Replace("_", ""), single);
                    if (double.IsPositiveInfinity(value))
                        throw Fail("infinity is not allowed", end_);
                    // A zero is the positive one whatever its sign: the
                    // game's FloatTag.valueOf and DoubleTag.valueOf hand back
                    // their ZERO for anything == 0.
                    if (sign == '-' && value != 0)
                        value = -value;
                    Tag tag = single ? new TagFloat((float)value) : (Tag)new TagDouble(value);
                    return (tag, end_, true);
                }
                string plain = whole.Replace("_", "");
                if (plain.Length > 1 && plain[0] == '0')
                    throw Fail("a leading zero is not allowed", i);
                var (integer, integerEnd) = Integer(sign, whole, 10, jw, defaultLetter);
                return (integer, integerEnd, false);
            }

            private (Tag Value, int End) Integer(char sign, string digits, int radix, int j, char defaultLetter)
            {
                char signedness = '\0';
                int k = Skip(j);
                char c = At(k);
                if (Has(k) && "sSuU".IndexOf(c) >= 0)
                {
                    int after = Skip(k + 1);
                    char following = At(after);
                    if (Has(after) && IsTypeLetter(following))
                    {
                        signedness = char.ToLowerInvariant(c);
                        k = after;
                        c = following;
                    }
                }
                char letter;
                if (Has(k) && IsTypeLetter(c))
                {
                    letter = char.ToLowerInvariant(c);
                    j = k + 1;
                }
                else
                {
                    letter = defaultLetter;
                }
                int width = Width(letter);
                BigInteger magnitude = BigInteger.Zero;
                foreach (char d in digits)
                {
                    if (d == '_')
                        continue;
                    magnitude = magnitude * radix + Convert.ToInt32(d.ToString(), 16);
                }
                if (signedness == '\0')
                    signedness = radix == 10 ? 's' : 'u';
                BigInteger value;
                if (signedness == 'u')
                {
                    if (sign == '-')
                        throw Fail("expected a non-negative number", j);
                    if (magnitude >= BigInteger.One << width)
                        throw Fail($"out of range for an unsigned {width}-bit integer", j);
                    value = magnitude >= BigInteger.One << (width - 1) ? magnitude - (BigInteger.One << width) : magnitude;
                }
                else
                {
                    value = sign == '-' ? -magnitude : magnitude;
                    if (value < -(BigInteger.One << (width - 1)) || value >= BigInteger.One << (width - 1))
                        throw Fail($"out of range for a {width}-bit integer", j);
                }
                long v = (long)value;
                Tag tag = letter switch
                {
                    'b' => new TagByte((sbyte)v),
                    's' => new TagShort((short)v),
                    'i' => new TagInt((int)v),
                    _ => new TagLong(v),
                };
                return (tag, j);
            }
        }

        private static int Width(char letter) => letter switch
        {
            'b' => 8,
            's' => 16,
            'i' => 32,
            _ => 64,
        };

        private static int Width(Tag tag) => tag switch
        {
            TagByte _ => 8,
            TagShort _ => 16,
            TagInt _ => 32,
            _ => 64,
        };

        private static bool IsWrapper(TagCompound compound) =>
            compound.Count == 1 && compound.ContainsKey("");

        /// <summary>A list of the parsed items, stored as the game stores
        /// it: as is when they share one type, and otherwise as compounds,
        /// every item that is not already a plain compound wrapped under the
        /// empty key.</summary>
        private static TagList MakeList(List<Tag> items)
        {
            if (items.Count == 0)
                return new TagList(TagId.End);
            TagId first = items[0].Id;
            if (items.All(item => item.Id == first))
            {
                var same = new TagList(first);
                foreach (var item in items)
                    same.Add(item);
                return same;
            }
            var mixed = new TagList(TagId.Compound);
            foreach (var item in items)
            {
                if (item is TagCompound compound && !IsWrapper(compound))
                {
                    mixed.Add(compound);
                }
                else
                {
                    var wrapper = new TagCompound();
                    wrapper[""] = item;
                    mixed.Add(wrapper);
                }
            }
            return mixed;
        }

        /// <summary>The tag text spells, as the game's TagParser.parseFully
        /// reads it -- the twin of parse_snbt: one value, with whitespace
        /// around it and nothing after. Throws NbtException, naming the
        /// position, wherever the game refuses, and at the three
        /// departures.</summary>
        public static Tag ParseSnbt(string text)
        {
            if (text == null)
                throw new ArgumentNullException(nameof(text));
            var parser = new Parser(text);
            var (value, i) = parser.Value(0);
            i = parser.Skip(i);
            if (i != text.Length)
                throw parser.Fail("unexpected trailing data", i);
            return value;
        }

        // -- writing ---------------------------------------------------------

        /// <summary>StringTag.quoteAndEscape: in double quotes, unless a
        /// double quote comes before any single one, then in single; a
        /// backslash, the quote itself and every character below a space
        /// escaped.</summary>
        private static void Quote(string text, StringBuilder out_)
        {
            var body = new StringBuilder();
            char quote = '\0';
            foreach (char ch in text)
            {
                if (ch == '\\')
                {
                    body.Append("\\\\");
                }
                else if (ch == '"' || ch == '\'')
                {
                    if (quote == '\0')
                        quote = ch == '"' ? '\'' : '"';
                    if (ch == quote)
                        body.Append('\\');
                    body.Append(ch);
                }
                else if (ch < ' ')
                {
                    body.Append('\\');
                    body.Append(ch switch
                    {
                        '\b' => "b",
                        '\t' => "t",
                        '\n' => "n",
                        '\f' => "f",
                        '\r' => "r",
                        _ => "x" + ((int)ch).ToString("X2", CultureInfo.InvariantCulture),
                    });
                }
                else
                {
                    body.Append(ch);
                }
            }
            if (quote == '\0')
                quote = '"';
            out_.Append(quote).Append(body).Append(quote);
        }

        private static void Write(Tag tag, StringBuilder out_, int depth)
        {
            switch (tag)
            {
                case TagCompound compound:
                {
                    if (depth >= Codec.MaxDepth)
                        throw new NbtException($"tag nesting exceeds the {Codec.MaxDepth}-level limit");
                    out_.Append('{');
                    var keys = compound.Keys.ToList();
                    keys.Sort(StringComparer.Ordinal);
                    for (int n = 0; n < keys.Count; n++)
                    {
                        string key = keys[n];
                        if (n > 0)
                            out_.Append(',');
                        if (string.Equals(key, "true", StringComparison.OrdinalIgnoreCase)
                            || string.Equals(key, "false", StringComparison.OrdinalIgnoreCase)
                            || !IsBareKey(key))
                            Quote(key, out_);
                        else
                            out_.Append(key);
                        out_.Append(':');
                        Write(compound[key], out_, depth + 1);
                    }
                    out_.Append('}');
                    break;
                }
                case TagList list:
                {
                    if (depth >= Codec.MaxDepth)
                        throw new NbtException($"tag nesting exceeds the {Codec.MaxDepth}-level limit");
                    out_.Append('[');
                    for (int n = 0; n < list.Count; n++)
                    {
                        if (n > 0)
                            out_.Append(',');
                        var item = list[n];
                        if (item is TagCompound wrapper && IsWrapper(wrapper))
                            item = wrapper[""];
                        Write(item, out_, depth + 1);
                    }
                    out_.Append(']');
                    break;
                }
                case TagString text:
                    Quote(text.Value, out_);
                    break;
                case TagByte b:
                    out_.Append(b.Value.ToString(CultureInfo.InvariantCulture)).Append('b');
                    break;
                case TagShort s:
                    out_.Append(s.Value.ToString(CultureInfo.InvariantCulture)).Append('s');
                    break;
                case TagInt i:
                    out_.Append(i.Value.ToString(CultureInfo.InvariantCulture));
                    break;
                case TagLong l:
                    out_.Append(l.Value.ToString(CultureInfo.InvariantCulture)).Append('L');
                    break;
                case TagFloat f:
                    // + 0f folds a negative zero into the positive one, as
                    // the game's FloatTag.valueOf does on every read
                    out_.Append(JavaDecimal(f.Value + 0f, true)).Append('f');
                    break;
                case TagDouble d:
                    out_.Append(JavaDecimal(d.Value + 0.0, false)).Append('d');
                    break;
                case TagByteArray bytes:
                    out_.Append("[B;").Append(string.Join(",", bytes.Values.Select(v => v.ToString(CultureInfo.InvariantCulture) + "B"))).Append(']');
                    break;
                case TagIntArray ints:
                    out_.Append("[I;").Append(string.Join(",", ints.Values.Select(v => v.ToString(CultureInfo.InvariantCulture)))).Append(']');
                    break;
                case TagLongArray longs:
                    out_.Append("[L;").Append(string.Join(",", longs.Values.Select(v => v.ToString(CultureInfo.InvariantCulture) + "L"))).Append(']');
                    break;
                default:
                    throw new NbtException($"cannot write {tag.GetType().Name} as SNBT");
            }
        }

        /// <summary>tag as the game's Tag.toString() writes it -- the twin
        /// of to_snbt.</summary>
        public static string ToSnbt(Tag tag)
        {
            var out_ = new StringBuilder();
            Write(tag, out_, 0);
            return out_.ToString();
        }
    }
}
