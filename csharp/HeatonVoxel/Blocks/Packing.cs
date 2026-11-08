// Twin of heaton_voxel/blocks.py (bit packing, nibbles, width rules).
// Entries never straddle a 64-bit word: each long holds floor(64/bits)
// entries from its low bits up, leftover high bits are padding. All field
// math happens in ulong — shifting signed words sign-extends phantom bits
// into the field, the reference's oldest real bug.

using System;

namespace HeatonVoxel.Blocks
{
    public static class Packing
    {
        public const int BlocksPerSection = 16 * 16 * 16;
        public const int BiomesPerSection = 4 * 4 * 4;
        public const int MinBlockBits = 4;
        public const int MinBiomeBits = 1;
        public const int ColumnsPerChunk = 256;

        /// <summary>Bits per index needed to address a palette of this size.</summary>
        public static int BitsForPalette(int size, int minimum)
        {
            if (size <= 1)
                return minimum;
            int bits = 0;
            int value = size - 1;
            while (value > 0) { bits++; value >>= 1; }
            return Math.Max(minimum, bits);
        }

        public static int[] UnpackIndices(long[] longs, int count, int bits)
        {
            ulong mask = (1UL << bits) - 1;
            int perLong = 64 / bits;
            var values = new int[count];
            int written = 0;
            foreach (long word in longs)
            {
                ulong bitsWord = unchecked((ulong)word);
                for (int slot = 0; slot < perLong && written < count; slot++)
                    values[written++] = (int)((bitsWord >> (slot * bits)) & mask);
                if (written >= count)
                    break;
            }
            if (written < count)
                throw new Nbt.NbtException(
                    $"packed array holds {written} entries at {bits} bits, need {count}");
            return values;
        }

        /// <summary>Indices packed end to end across longs, a value
        /// straddling two where it falls on the seam: a section or
        /// heightmap before 1.16. Python's _unpack_run_on.</summary>
        public static int[] UnpackRunOn(long[] longs, int count, int bits)
        {
            ulong mask = (1UL << bits) - 1;
            var values = new int[count];
            for (int index = 0; index < count; index++)
            {
                long start = (long)index * bits;
                int word = (int)(start >> 6), shift = (int)(start & 63);
                ulong value = unchecked((ulong)longs[word]) >> shift;
                if (shift + bits > 64)
                    value |= unchecked((ulong)longs[word + 1]) << (64 - shift);
                values[index] = (int)(value & mask);
            }
            return values;
        }

        public static long[] PackIndices(ReadOnlySpan<int> values, int count, int bits)
        {
            int perLong = 64 / bits;
            var words = new long[(count + perLong - 1) / perLong];
            for (int i = 0; i < count; i++)
            {
                int word = i / perLong;
                int slot = i - word * perLong;
                words[word] = unchecked((long)(
                    (ulong)words[word] | ((ulong)(uint)values[i] << (slot * bits))));
            }
            return words;
        }

        /// <summary>Bits per entry, cross-checked against the array's actual
        /// length: a section whose palette has shrunk can still be packed at
        /// the wider width the game last wrote, so the length is the
        /// authority when the two disagree.</summary>
        public static int InferBits(int paletteSize, int longCount, int count, int minimum)
        {
            int expected = BitsForPalette(paletteSize, minimum);
            int perLong = 64 / expected;
            if (perLong > 0 && (count + perLong - 1) / perLong == longCount)
                return expected;
            for (int bits = minimum; bits <= 64; bits++)
            {
                perLong = 64 / bits;
                if (perLong > 0 && (count + perLong - 1) / perLong == longCount)
                    return bits;
            }
            throw new Nbt.NbtException(
                $"cannot reconcile a {longCount}-long array with {count} entries "
                + $"and a {paletteSize}-entry palette");
        }

        /// <summary>Light arrays: one 0-15 level per cell in y,z,x order,
        /// two per byte, the low nibble first.</summary>
        public static int[] UnpackNibbles(ReadOnlySpan<sbyte> data)
        {
            if (data.Length != BlocksPerSection / 2)
                throw new Nbt.NbtException(
                    $"light array holds {data.Length} bytes, expected "
                    + $"{BlocksPerSection / 2}");
            var values = new int[BlocksPerSection];
            for (int i = 0; i < data.Length; i++)
            {
                int b = data[i] & 0xFF;
                values[i * 2] = b & 0xF;
                values[i * 2 + 1] = b >> 4;
            }
            return values;
        }

        /// <summary>Bits per heightmap entry for a world of this height:
        /// entries run 0..height inclusive.</summary>
        public static int HeightmapBits(int minY, int maxY)
        {
            int height = maxY - minY + 1;
            int bits = 0;
            while (height > 0) { bits++; height >>= 1; }
            return Math.Max(1, bits);
        }
    }
}
