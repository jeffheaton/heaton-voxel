// Twin of heaton_voxel/nbt.py (compression). Region chunks are zlib
// (RFC 1950), and this profile has no ZLibStream — so the 2-byte header
// and Adler-32 trailer are handled here around a raw DeflateStream, as
// PORTING.md prescribes. Compressed output legitimately differs from
// CPython's byte-for-byte; unmodified data is re-emitted from its original
// bytes, which is what keeps round trips exact.

using System;
using System.IO;
using System.IO.Compression;

namespace HeatonVoxel.Nbt
{
    public static class Compression
    {
        public const byte SchemeGzip = 1;
        public const byte SchemeZlib = 2;
        public const byte SchemeNone = 3;
        public const byte SchemeLz4 = 4;

        public static string SchemeName(byte scheme) => scheme switch
        {
            SchemeGzip => "gzip",
            SchemeZlib => "zlib",
            SchemeNone => "none",
            SchemeLz4 => "lz4",
            127 => "custom",
            _ => scheme.ToString(),
        };

        public static byte[] Decompress(byte scheme, byte[] blob) => scheme switch
        {
            SchemeZlib => ZlibDecompress(blob),
            SchemeGzip => GzipDecompress(blob),
            SchemeNone => blob,
            SchemeLz4 => throw new NbtException(
                "chunk uses LZ4 compression, which this port does not read"),
            _ => throw new NbtException($"unsupported compression scheme {scheme}"),
        };

        public static byte[] Compress(byte scheme, byte[] raw) => scheme switch
        {
            SchemeZlib => ZlibCompress(raw),
            SchemeGzip => GzipCompress(raw),
            SchemeNone => raw,
            _ => throw new NbtException($"unsupported compression scheme {scheme}"),
        };

        public static bool HasZlibHeader(byte[] data)
        {
            if (data.Length < 2)
                return false;
            int cmf = data[0], flg = data[1];
            return (cmf & 0x0F) == 8 && ((cmf << 8) | flg) % 31 == 0;
        }

        // -- zlib (RFC 1950) over DeflateStream ---------------------------

        public static byte[] ZlibDecompress(byte[] blob)
        {
            if (!HasZlibHeader(blob))
                throw new NbtException("invalid zlib header");
            if ((blob[1] & 0x20) != 0)
                throw new NbtException("zlib preset dictionaries are not supported");
            if (blob.Length < 6)
                throw new NbtException("zlib payload too short for header and trailer");

            using var body = new MemoryStream(blob, 2, blob.Length - 6);
            using var inflate = new DeflateStream(body, CompressionMode.Decompress);
            using var output = new MemoryStream();
            inflate.CopyTo(output);
            byte[] raw = output.ToArray();

            uint stored = (uint)((blob[^4] << 24) | (blob[^3] << 16)
                                 | (blob[^2] << 8) | blob[^1]);
            uint computed = Adler32(raw);
            if (stored != computed)
                throw new NbtException(
                    $"zlib Adler-32 mismatch: stored {stored:x8}, computed {computed:x8}");
            return raw;
        }

        public static byte[] ZlibCompress(byte[] raw)
        {
            using var output = new MemoryStream();
            output.WriteByte(0x78);  // deflate, 32K window
            output.WriteByte(0x9C);  // the conventional default-level header
            using (var deflate = new DeflateStream(
                       output, CompressionLevel.Optimal, leaveOpen: true))
            {
                deflate.Write(raw, 0, raw.Length);
            }
            uint adler = Adler32(raw);
            output.WriteByte((byte)(adler >> 24));
            output.WriteByte((byte)(adler >> 16));
            output.WriteByte((byte)(adler >> 8));
            output.WriteByte((byte)adler);
            return output.ToArray();
        }

        public static uint Adler32(byte[] data)
        {
            const uint Mod = 65521;
            uint a = 1, b = 0;
            int pos = 0;
            while (pos < data.Length)
            {
                // process in runs small enough that the sums cannot overflow
                int limit = Math.Min(data.Length, pos + 5552);
                for (; pos < limit; pos++)
                {
                    a += data[pos];
                    b += a;
                }
                a %= Mod;
                b %= Mod;
            }
            return (b << 16) | a;
        }

        // -- gzip ----------------------------------------------------------

        public static byte[] GzipDecompress(byte[] blob)
        {
            using var input = new MemoryStream(blob);
            using var gunzip = new GZipStream(input, CompressionMode.Decompress);
            using var output = new MemoryStream();
            gunzip.CopyTo(output);
            return output.ToArray();
        }

        public static byte[] GzipCompress(byte[] raw)
        {
            using var output = new MemoryStream();
            using (var gzip = new GZipStream(
                       output, CompressionLevel.Optimal, leaveOpen: true))
            {
                gzip.Write(raw, 0, raw.Length);
            }
            return output.ToArray();
        }
    }
}
