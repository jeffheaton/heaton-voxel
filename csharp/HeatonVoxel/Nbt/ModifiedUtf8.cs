// Twin of heaton_voxel/nbt.py (modified UTF-8). NBT strings use
// java.io.DataOutputStream.writeUTF's encoding: U+0000 as C0 80, and each
// UTF-16 code unit encoded independently (so astral characters become
// CESU-8 surrogate pairs). A C# char IS a UTF-16 code unit, which makes
// this side simpler than Python's: encode per char, decode per sequence,
// no surrogate recombination. Never use Encoding.UTF8 here.

using System.Text;

namespace HeatonVoxel.Nbt
{
    public static class ModifiedUtf8
    {
        public static string Decode(byte[] data, int start, int length)
        {
            // Overwhelmingly the common case in Minecraft data, and provably
            // identical under both encodings.
            bool ascii = true;
            for (int i = start; i < start + length; i++)
            {
                if (data[i] == 0 || data[i] >= 0x80) { ascii = false; break; }
            }
            if (ascii)
            {
                var chars = new char[length];
                for (int i = 0; i < length; i++)
                    chars[i] = (char)data[start + i];
                return new string(chars);
            }

            var text = new StringBuilder(length);
            int pos = start;
            int end = start + length;
            while (pos < end)
            {
                byte b = data[pos];
                if (b < 0x80)
                {
                    text.Append((char)b);
                    pos += 1;
                }
                else if ((b & 0xE0) == 0xC0)
                {
                    if (pos + 1 >= end)
                        throw new NbtException($"truncated 2-byte sequence at offset {pos - start}");
                    text.Append((char)(((b & 0x1F) << 6) | (data[pos + 1] & 0x3F)));
                    pos += 2;
                }
                else if ((b & 0xF0) == 0xE0)
                {
                    if (pos + 2 >= end)
                        throw new NbtException($"truncated 3-byte sequence at offset {pos - start}");
                    text.Append((char)(((b & 0x0F) << 12)
                                       | ((data[pos + 1] & 0x3F) << 6)
                                       | (data[pos + 2] & 0x3F)));
                    pos += 3;
                }
                else
                {
                    throw new NbtException(
                        $"invalid modified UTF-8 lead byte 0x{b:x2} at offset {pos - start}");
                }
            }
            return text.ToString();
        }

        public static byte[] Encode(string text)
        {
            bool ascii = true;
            foreach (char c in text)
            {
                if (c == 0 || c >= 0x80) { ascii = false; break; }
            }
            if (ascii)
            {
                var plain = new byte[text.Length];
                for (int i = 0; i < text.Length; i++)
                    plain[i] = (byte)text[i];
                return plain;
            }

            var buffer = new System.IO.MemoryStream(text.Length * 2);
            foreach (char c in text)
            {
                int unit = c;
                if (unit >= 0x01 && unit <= 0x7F)
                {
                    buffer.WriteByte((byte)unit);
                }
                else if (unit == 0 || unit <= 0x7FF)
                {
                    buffer.WriteByte((byte)(0xC0 | (unit >> 6)));
                    buffer.WriteByte((byte)(0x80 | (unit & 0x3F)));
                }
                else
                {
                    buffer.WriteByte((byte)(0xE0 | (unit >> 12)));
                    buffer.WriteByte((byte)(0x80 | ((unit >> 6) & 0x3F)));
                    buffer.WriteByte((byte)(0x80 | (unit & 0x3F)));
                }
            }
            return buffer.ToArray();
        }
    }
}
