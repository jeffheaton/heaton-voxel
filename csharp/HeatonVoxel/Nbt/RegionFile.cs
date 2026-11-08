// Twin of heaton_voxel/nbt.py (Chunk and the NBT file class). One
// sanctioned rename: Python's class `NBT` is `NbtFile` here, because C#
// cannot give a class the same name as its namespace. Everything else
// mirrors the reference: lazy chunk decoding, original-bytes reuse (the
// mechanism that keeps untouched round trips byte-exact in any language),
// hash-based change detection, sector slack, gap sectors, timestamps of
// vanished chunks, .mcc sidecars, and zero-byte empty regions.

using System;
using System.Buffers.Binary;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.RegularExpressions;

namespace HeatonVoxel.Nbt
{
    public enum NbtKind
    {
        Standalone,
        Region,
    }

    /// <summary>One chunk inside a region file. NBT decodes lazily: a
    /// freshly loaded region holds only compressed bytes, and the tree is
    /// built the first time <see cref="Data"/> is read; <see cref="Release"/>
    /// hands the memory back, flushing edits into the bytes first.</summary>
    public sealed class Chunk
    {
        public int LocalX;
        public int LocalZ;
        public uint Timestamp;
        public byte CompressionScheme;
        public bool External;

        /// <summary>True once the payload has been re-encoded, i.e. the
        /// chunk's bytes changed since load. Only decides which chunks
        /// touch_modified re-stamps; whether the file needs writing is
        /// settled by comparing digests, never by this flag.</summary>
        public bool Dirty;

        private TagCompound? _data;
        private string _dataName = "";
        private bool _decoded;
        private string? _parseError;
        internal byte[]? Blob;
        private byte[]? _digest;
        internal int SectorOffset;
        internal int SectorCount;
        internal byte[] Padding = Array.Empty<byte>();

        public Chunk(int localX, int localZ, TagCompound? data = null,
                     byte compressionScheme = Compression.SchemeZlib)
        {
            LocalX = localX;
            LocalZ = localZ;
            CompressionScheme = compressionScheme;
            _data = data;
            _decoded = data != null;
            Dirty = data != null;
        }

        private void DecodeNow()
        {
            _decoded = true;
            if (Blob == null)
                return;
            try
            {
                byte[] payload = Compression.Decompress(CompressionScheme, Blob);
                (_dataName, _data) = Codec.Decode(payload);
                _digest = Digest.Of(payload);
            }
            catch (Exception exc)  // keep the bytes, report the problem
            {
                _parseError = $"{exc.GetType().Name}: {exc.Message}";
            }
        }

        public TagCompound? Data
        {
            get
            {
                if (!_decoded) DecodeNow();
                return _data;
            }
            set
            {
                _data = value;
                _decoded = true;
                _parseError = null;
            }
        }

        public string DataName
        {
            get
            {
                if (!_decoded) DecodeNow();
                return _dataName;
            }
            set
            {
                if (!_decoded) DecodeNow();
                _dataName = value;
            }
        }

        public string? ParseError
        {
            get
            {
                if (!_decoded) DecodeNow();
                return _parseError;
            }
        }

        /// <summary>Is the chunk's NBT currently decoded in memory?</summary>
        public bool Loaded => _decoded && _data != null;

        /// <summary>Bytes of compressed payload currently held; zero for a
        /// chunk built in memory and never flushed.</summary>
        public int CompressedSize => Blob?.Length ?? 0;

        public int Index => LocalX + LocalZ * 32;

        internal Chunk HistoryCopy()
        {
            Release();
            // Payload, digest and padding arrays are replaced, never edited.
            return (Chunk)MemberwiseClone();
        }

        /// <summary>Drop the decoded tree, keeping the compressed bytes.
        /// Edits are flushed into those bytes first, so this is safe on a
        /// modified chunk.</summary>
        public void Release()
        {
            if (_decoded && _data != null)
                Payload();  // flush into Blob/_digest, recording any change
            _data = null;
            _decoded = false;
            _parseError = null;
        }

        /// <summary>The compressed payload, reusing the original bytes when
        /// the chunk's NBT is byte-for-byte what was loaded. Reads the
        /// private tree rather than <see cref="Data"/>, so saving a region
        /// nobody touched does no decoding at all.</summary>
        public byte[] Payload()
        {
            if (!_decoded || _data == null)
            {
                if (Blob == null)
                    throw new NbtException(
                        $"chunk ({LocalX}, {LocalZ}) has neither decoded data "
                        + "nor original bytes");
                return Blob;
            }
            byte[] raw = Codec.Encode(_data, _dataName);
            byte[] digest = Digest.Of(raw);
            if (Blob != null && _digest != null && digest.AsSpan().SequenceEqual(_digest))
                return Blob;
            Blob = Compression.Compress(CompressionScheme, raw);
            _digest = digest;
            Dirty = true;
            return Blob;
        }
    }

    internal static class Digest
    {
        public static byte[] Of(byte[] data)
        {
            // Internal change detection only; nothing persists this, so any
            // hash serves (the reference uses blake2b, unavailable here).
            using var sha = SHA256.Create();
            return sha.ComputeHash(data);
        }
    }

    /// <summary>A loaded Minecraft NBT file: either a standalone file
    /// (level.dat and friends) or an Anvil region container.</summary>
    public sealed class NbtFile : IEnumerable<Chunk>
    {
        public const int Sector = 4096;
        public const byte ExternalFlag = 0x80;

        private static readonly Regex RegionName =
            new Regex(@"^r\.(-?\d+)\.(-?\d+)\.mc[ar]$", RegexOptions.IgnoreCase);

        public NbtKind Kind = NbtKind.Standalone;
        public string? Path;

        // Kind == Standalone
        public TagCompound? Root;
        public string RootName = "";
        public byte CompressionScheme = Compression.SchemeNone;

        // Kind == Region
        public readonly Dictionary<(int X, int Z), Chunk> Chunks =
            new Dictionary<(int, int), Chunk>();
        public int? RegionX;
        public int? RegionZ;

        private byte[]? _raw;
        private byte[]? _digest;
        private uint[] _timestamps = new uint[1024];
        private int _totalSectors;
        private readonly Dictionary<int, byte[]> _gapSectors = new Dictionary<int, byte[]>();
        private bool _loadedEmpty;
        private byte[]? _loadDigest;
        private readonly HashSet<string> _loadedSidecars =
            new HashSet<string>(StringComparer.Ordinal);

        /// <summary>Loaded from disk, as opposed to built in memory (which
        /// is modified by definition).</summary>
        public bool FromDisk => _loadDigest != null;

        internal NbtFile HistoryCopy()
        {
            var copy = new NbtFile
            {
                Kind = Kind, Path = Path, RootName = RootName,
                CompressionScheme = CompressionScheme,
                Root = Root == null ? null : Codec.Decode(Codec.Encode(Root)).Root,
                RegionX = RegionX, RegionZ = RegionZ,
                _raw = _raw, _digest = _digest, _loadDigest = _loadDigest,
                _timestamps = (uint[])_timestamps.Clone(),
                _totalSectors = _totalSectors, _loadedEmpty = _loadedEmpty,
            };
            foreach (var pair in Chunks)
                copy.Chunks[pair.Key] = pair.Value.HistoryCopy();
            foreach (var pair in _gapSectors)
                copy._gapSectors[pair.Key] = pair.Value;
            copy._loadedSidecars.UnionWith(_loadedSidecars);
            return copy;
        }

        internal void RestoreHistory(NbtFile state)
        {
            var copy = state.HistoryCopy();
            Kind = copy.Kind; Root = copy.Root; RootName = copy.RootName;
            CompressionScheme = copy.CompressionScheme;
            RegionX = copy.RegionX; RegionZ = copy.RegionZ;
            _raw = copy._raw; _digest = copy._digest;
            _timestamps = copy._timestamps; _totalSectors = copy._totalSectors;
            _loadedEmpty = copy._loadedEmpty;
            Chunks.Clear();
            foreach (var pair in copy.Chunks) Chunks[pair.Key] = pair.Value;
            _gapSectors.Clear();
            foreach (var pair in copy._gapSectors) _gapSectors[pair.Key] = pair.Value;
            // The disk baseline belongs to the live handle, not the record.
            // Keep sidecars from both states so a later save can remove stale ones.
            _loadedSidecars.UnionWith(copy._loadedSidecars);
        }

        // -- loading -------------------------------------------------------

        public static NbtFile Load(string path, bool eager = false)
        {
            byte[] data = File.ReadAllBytes(path);
            var file = new NbtFile { Path = path };
            file._loadDigest = Digest.Of(data);
            if (LooksLikeRegion(path, data))
            {
                file.LoadRegion(data);
                if (eager)
                {
                    foreach (var chunk in file.Chunks.Values)
                        _ = chunk.ParseError;
                }
            }
            else
            {
                file.LoadStandalone(data);
            }
            return file;
        }

        private static bool LooksLikeRegion(string path, byte[] data)
        {
            if (RegionName.IsMatch(System.IO.Path.GetFileName(path)))
                return true;
            // Anything carrying a recognizable NBT container header is standalone.
            if (data.Length >= 2 && data[0] == 0x1F && data[1] == 0x8B)
                return false;
            if (data.Length >= 1 && data[0] == 0x0A)
                return false;
            if (Compression.HasZlibHeader(data))
                return false;
            // Otherwise fall back on shape: a whole number of 4 KiB sectors,
            // at least the two header sectors long.
            return data.Length >= 2 * Sector && data.Length % Sector == 0;
        }

        private void LoadStandalone(byte[] data)
        {
            Kind = NbtKind.Standalone;
            _raw = data;
            byte[] payload;
            if (data.Length >= 2 && data[0] == 0x1F && data[1] == 0x8B)
            {
                CompressionScheme = Compression.SchemeGzip;
                payload = Compression.GzipDecompress(data);
            }
            else if (data.Length >= 1 && data[0] == 0x0A)
            {
                CompressionScheme = Compression.SchemeNone;
                payload = data;
            }
            else
            {
                try
                {
                    payload = Compression.ZlibDecompress(data);
                }
                catch (NbtException exc)
                {
                    throw new NbtException($"unrecognized NBT container: {exc.Message}");
                }
                CompressionScheme = Compression.SchemeZlib;
            }
            (RootName, Root) = Codec.Decode(payload);
            _digest = Digest.Of(payload);
        }

        private void LoadRegion(byte[] data)
        {
            Kind = NbtKind.Region;
            var match = RegionName.Match(System.IO.Path.GetFileName(Path ?? ""));
            if (match.Success)
            {
                RegionX = int.Parse(match.Groups[1].Value);
                RegionZ = int.Parse(match.Groups[2].Value);
            }

            if (data.Length == 0)
            {
                // A zero-length file is a valid empty region (common under
                // poi/) and stays zero bytes on the way out.
                _loadedEmpty = true;
                return;
            }
            if (data.Length < 2 * Sector)
                throw new NbtException(
                    $"region file is only {data.Length} bytes; need at least 8192");

            _totalSectors = data.Length / Sector;
            for (int i = 0; i < 1024; i++)
                _timestamps[i] = BinaryPrimitives.ReadUInt32BigEndian(
                    data.AsSpan(Sector + i * 4, 4));

            var occupied = new bool[_totalSectors];
            occupied[0] = occupied[1] = true;

            for (int index = 0; index < 1024; index++)
            {
                int offset = (data[index * 4] << 16) | (data[index * 4 + 1] << 8)
                             | data[index * 4 + 2];
                int count = data[index * 4 + 3];
                if (offset == 0 && count == 0)
                    continue;
                if (offset < 2 || offset + count > _totalSectors)
                {
                    int trailing = data.Length % Sector;
                    string hint = trailing != 0
                        ? $". The file ends {trailing} bytes past a sector "
                          + "boundary, so it was probably read while being "
                          + "written; check WorldIsOpen and close the world first"
                        : "";
                    throw new NbtException(
                        $"chunk {index} claims sectors {offset}..{offset + count}, "
                        + $"outside a {_totalSectors}-sector file{hint}");
                }
                for (int sector = offset; sector < offset + count; sector++)
                    occupied[sector] = true;

                var chunk = ReadChunkRecord(data, index, offset, count);
                chunk.Timestamp = _timestamps[index];
                Chunks[(chunk.LocalX, chunk.LocalZ)] = chunk;
            }

            // Unallocated sectors are usually zero-filled; keep any that are
            // not, so a rewrite reproduces them.
            for (int sector = 0; sector < _totalSectors; sector++)
            {
                if (occupied[sector])
                    continue;
                var blob = data.AsSpan(sector * Sector, Sector);
                bool nonZero = false;
                foreach (byte b in blob)
                {
                    if (b != 0) { nonZero = true; break; }
                }
                if (nonZero)
                    _gapSectors[sector] = blob.ToArray();
            }
        }

        private Chunk ReadChunkRecord(byte[] data, int index, int offset, int count)
        {
            int start = offset * Sector;
            uint length = BinaryPrimitives.ReadUInt32BigEndian(data.AsSpan(start, 4));
            byte scheme = data[start + 4];
            bool external = (scheme & ExternalFlag) != 0;
            scheme = (byte)(scheme & ~ExternalFlag);

            var chunk = new Chunk(index % 32, index / 32, null, scheme)
            {
                External = external,
                SectorOffset = offset,
                SectorCount = count,
                Dirty = false,
            };

            if (length < 1 || start + 4 + length > (long)(offset + count) * Sector)
                throw new NbtException(
                    $"chunk {index} declares a {length}-byte record that overruns "
                    + $"its {count} allocated sectors");

            if (external)
            {
                string side = ExternalPath(chunk, null);
                if (!File.Exists(side))
                    throw new NbtException($"missing external chunk file {side}");
                chunk.Blob = File.ReadAllBytes(side);
                _loadedSidecars.Add(side);
            }
            else
            {
                chunk.Blob = data.AsSpan(start + 5, (int)length - 1).ToArray();
            }

            // Trailing bytes in the final sector: Minecraft does not clear
            // these when a chunk shrinks, so they routinely hold fragments.
            int recordEnd = start + 4 + (int)length;
            chunk.Padding = data.AsSpan(recordEnd, (offset + count) * Sector - recordEnd)
                                .ToArray();
            return chunk;
        }

        private string ExternalPath(Chunk chunk, string? directory)
        {
            directory ??= System.IO.Path.GetDirectoryName(Path ?? "") ?? "";
            if (RegionX == null || RegionZ == null)
                throw new NbtException(
                    "cannot locate .mcc sidecar files: the region's coordinates "
                    + "are unknown because the file is not named r.<x>.<z>.mca");
            return System.IO.Path.Combine(
                directory,
                $"c.{RegionX * 32 + chunk.LocalX}.{RegionZ * 32 + chunk.LocalZ}.mcc");
        }

        // -- saving --------------------------------------------------------

        /// <summary>Write the file back out, atomically: sidecars first,
        /// then the file, then the sidecars it no longer refers to.
        /// <c>beforeWrite</c> is called with the full path of every file
        /// this save is about to replace, create or remove, in that order,
        /// once the save is sure to write and before any of them is
        /// touched; an exception from it stops the save with nothing of
        /// this file written.</summary>
        public bool Save(string? path = null, bool force = false,
                         bool touchModified = false, bool skipUnchanged = false,
                         Action<string>? beforeWrite = null)
        {
            string target = path ?? Path
                ?? throw new NbtException("no path given and this NBT was not loaded from a file");
            bool inPlace = System.IO.Path.GetFullPath(target)
                == System.IO.Path.GetFullPath(Path ?? target);

            if (!force)
            {
                string? world = FileSafety.FindWorldRoot(target);
                if (world != null && FileSafety.WorldIsOpen(world))
                    throw new NbtException(
                        $"the world at '{world}' is open in Minecraft (its "
                        + $"{FileSafety.SessionLock} is held). Writing to it now "
                        + "would be lost when the game flushes, or corrupt the "
                        + "region. Close the world first, or pass force.");
            }

            string directory = System.IO.Path.GetDirectoryName(
                System.IO.Path.GetFullPath(target))!;
            byte[] data;
            Dictionary<string, byte[]> sidecars;
            if (Kind == NbtKind.Region)
                (data, sidecars) = BuildRegion(directory, touchModified);
            else
                (data, sidecars) = (BuildStandalone(), new Dictionary<string, byte[]>());

            byte[] digest = Digest.Of(data);
            if (skipUnchanged && inPlace && _loadDigest != null
                && digest.AsSpan().SequenceEqual(_loadDigest))
                return false;

            var written = new HashSet<string>(sidecars.Keys, StringComparer.Ordinal);
            if (beforeWrite != null)
            {
                foreach (string touched in sidecars.Keys.Append(target)
                             .Concat(StaleSidecars(directory, written)).ToList())
                    beforeWrite(System.IO.Path.GetFullPath(touched));
            }

            // Sidecars first: the region file references them, so it must not
            // become visible before the data it points at exists.
            foreach (var pair in sidecars)
                FileSafety.AtomicWrite(pair.Key, pair.Value);
            FileSafety.AtomicWrite(target, data);

            RemoveStaleSidecars(directory, written);
            Path = target;
            _loadDigest = digest;  // disk now matches this object
            return true;
        }

        private void RemoveStaleSidecars(string directory, HashSet<string> written)
        {
            if (!SidecarsHere(directory))
                return;
            foreach (var sidecar in StaleSidecars(directory, written))
            {
                try { File.Delete(sidecar); } catch (IOException) { }
            }
            _loadedSidecars.IntersectWith(written);
        }

        /// <summary>Whether this instance loaded sidecars from
        /// <paramref name="directory"/>, the only ones a save there may
        /// remove.</summary>
        private bool SidecarsHere(string directory) =>
            _loadedSidecars.Count > 0 && Path != null
            && System.IO.Path.GetDirectoryName(System.IO.Path.GetFullPath(Path)) == directory;

        /// <summary>The sidecars a save into <paramref name="directory"/>
        /// that writes <paramref name="written"/> removes, in a fixed
        /// order.</summary>
        private List<string> StaleSidecars(string directory, HashSet<string> written) =>
            SidecarsHere(directory)
                ? _loadedSidecars.Except(written).OrderBy(p => p, StringComparer.Ordinal).ToList()
                : new List<string>();

        /// <summary>Serialize without touching the filesystem. A region
        /// whose chunks need .mcc sidecars raises here; use Save.</summary>
        public byte[] ToBytes()
        {
            if (Kind == NbtKind.Region)
                return BuildRegion(null, touchModified: false).Data;
            return BuildStandalone();
        }

        private byte[] BuildStandalone()
        {
            if (Root == null)
                throw new NbtException("nothing to save: root tag is null");
            byte[] payload = Codec.Encode(Root, RootName);
            if (_raw != null && _digest != null
                && Digest.Of(payload).AsSpan().SequenceEqual(_digest))
                return _raw;
            return Compression.Compress(CompressionScheme, payload);
        }

        private (byte[] Data, Dictionary<string, byte[]> Sidecars) BuildRegion(
            string? sidecarDir, bool touchModified)
        {
            if (Chunks.Count == 0 && _loadedEmpty)
                return (Array.Empty<byte>(), new Dictionary<string, byte[]>());

            uint now = (uint)DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            var records = new List<(Chunk Chunk, byte[] Record, byte[] Blob, int Needed)>();
            foreach (var chunk in Chunks.Values)
            {
                byte[] blob = chunk.Payload();
                if (touchModified && chunk.Dirty)
                    chunk.Timestamp = now;

                // Always size the in-region record first, so a chunk that has
                // shrunk back under the limit stops using a sidecar.
                byte[] record = MakeRecord(blob, chunk.CompressionScheme);
                int needed = (record.Length + Sector - 1) / Sector;
                if (needed > 255)
                {
                    // The sector-count field is one byte; Minecraft moves such
                    // payloads to a .mcc sidecar and leaves a flagged stub.
                    if (sidecarDir == null)
                        throw new NbtException(
                            $"chunk ({chunk.LocalX}, {chunk.LocalZ}) is too large "
                            + "for the region format and must be written to a "
                            + ".mcc sidecar; use Save rather than ToBytes");
                    chunk.External = true;
                    record = new byte[5];
                    BinaryPrimitives.WriteUInt32BigEndian(record, 1);
                    record[4] = (byte)(chunk.CompressionScheme | ExternalFlag);
                    needed = 1;
                }
                else
                {
                    chunk.External = false;
                }
                records.Add((chunk, record, blob, needed));
            }

            // Keeping every chunk at its original sector preserves the exact
            // layout; that is only possible while each record still fits.
            bool inPlace = records.All(r =>
                r.Chunk.SectorOffset >= 2 && r.Needed <= r.Chunk.SectorCount);
            int totalSectors;
            List<(Chunk Chunk, byte[] Record, int Offset, int Count)> placements;
            if (inPlace)
            {
                totalSectors = _totalSectors;
                placements = records
                    .Select(r => (r.Chunk, r.Record, r.Chunk.SectorOffset, r.Chunk.SectorCount))
                    .ToList();
            }
            else
            {
                int cursor = 2;
                placements = new List<(Chunk, byte[], int, int)>();
                foreach (var r in records
                             .OrderBy(r => r.Chunk.SectorOffset)
                             .ThenBy(r => r.Chunk.Index))
                {
                    placements.Add((r.Chunk, r.Record, cursor, r.Needed));
                    cursor += r.Needed;
                }
                totalSectors = cursor;
            }

            totalSectors = Math.Max(totalSectors, 2);
            foreach (var p in placements)
                totalSectors = Math.Max(totalSectors, p.Offset + p.Count);

            var output = new byte[totalSectors * Sector];
            foreach (var pair in _gapSectors)
            {
                if (pair.Key < totalSectors)
                    Array.Copy(pair.Value, 0, output, pair.Key * Sector, Sector);
            }

            var timestamps = (uint[])_timestamps.Clone();
            foreach (var (chunk, record, offset, count) in placements)
            {
                int index = chunk.Index;
                output[index * 4] = (byte)(offset >> 16);
                output[index * 4 + 1] = (byte)(offset >> 8);
                output[index * 4 + 2] = (byte)offset;
                output[index * 4 + 3] = (byte)count;
                timestamps[index] = chunk.Timestamp;

                int start = offset * Sector;
                Array.Copy(record, 0, output, start, record.Length);
                // Restore the original slack bytes when the record kept its
                // length; otherwise the zeros already in place stand.
                int slack = (offset + count) * Sector - (start + record.Length);
                if (slack > 0 && chunk.Padding.Length == slack)
                    Array.Copy(chunk.Padding, 0, output, start + record.Length, slack);
            }

            for (int i = 0; i < 1024; i++)
                BinaryPrimitives.WriteUInt32BigEndian(
                    output.AsSpan(Sector + i * 4, 4), timestamps[i]);

            var sidecars = new Dictionary<string, byte[]>(StringComparer.Ordinal);
            if (sidecarDir != null)
            {
                foreach (var r in records)
                {
                    if (r.Chunk.External)
                        sidecars[ExternalPath(r.Chunk, sidecarDir)] = r.Blob;
                }
            }
            return (output, sidecars);
        }

        private static byte[] MakeRecord(byte[] blob, byte scheme)
        {
            var record = new byte[5 + blob.Length];
            BinaryPrimitives.WriteUInt32BigEndian(record, (uint)(blob.Length + 1));
            record[4] = scheme;
            Array.Copy(blob, 0, record, 5, blob.Length);
            return record;
        }

        // -- change tracking ----------------------------------------------

        /// <summary>Whether this file differs from the bytes it was loaded
        /// from: serialize and compare digests, which catches a change
        /// however it was made.</summary>
        public bool IsModified()
        {
            if (_loadDigest == null)
                return true;  // built in memory, never written anywhere
            return !Digest.Of(ToBytes()).AsSpan().SequenceEqual(_loadDigest);
        }

        // -- convenience ---------------------------------------------------

        /// <summary>The chunk at region-local (x, z), or null.</summary>
        public Chunk? Chunk(int x, int z) =>
            Chunks.TryGetValue((x & 31, z & 31), out var chunk) ? chunk : null;

        /// <summary>Iterate this region's chunks (empty for a standalone
        /// NBT file). Ordered by chunk index for determinism — Python's
        /// dict iteration is insertion order, which for a loaded region is
        /// exactly index order.</summary>
        public IEnumerator<Chunk> GetEnumerator() =>
            Chunks.Values.OrderBy(chunk => chunk.Index).GetEnumerator();

        IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();
    }
}
