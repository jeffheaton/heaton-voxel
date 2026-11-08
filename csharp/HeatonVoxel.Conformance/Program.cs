// The conformance runner: verifies the port against every artifact in
// fixtures/conformance/ exactly as PORTING.md prescribes, printing one line
// per check group and exiting nonzero on any failure. Artifacts whose
// layers are not yet ported are reported as PENDING, never silently
// skipped, so the port's true coverage is always visible.

using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;
using HeatonVoxel;
using HeatonVoxel.Blocks;
using HeatonVoxel.Nbt;
using HeatonVoxel.Schem;
using HeatonVoxel.World;

internal static class Program
{
    private static int _failures;
    private static int _checks;

    private static int Main(string[] args)
    {
        string corpus = args.Length > 0 ? args[0] : FindCorpus();
        Console.WriteLine($"corpus: {corpus}");

        Run("manifest", () => VerifyManifest(corpus));
        Run("nbt_cases", () => VerifyNbtCases(corpus));
        Run("packing", () => VerifyPacking(corpus));
        Run("varints", () => VerifyVarints(corpus));
        Run("nibbles", () => VerifyNibbles(corpus));
        Run("region", () => VerifyRegion(corpus));
        Run("world", () => VerifyWorld(corpus));
        Run("world_edits", () => VerifyWorldEdits(corpus));
        Run("block_defaults", () => VerifyBlockDefaults(corpus));
        Run("wall_cover", () => VerifyWallCover(corpus));
        Run("heightmaps", () => VerifyHeightmaps(corpus));
        Run("modern", () => VerifyModern(corpus));
        Run("legacy", () => VerifyLegacy(corpus));
        Run("older_chunks", () => VerifyOlderChunks(corpus));
        Run("old_layout", () => VerifyOldLayout(corpus));
        Run("chunk_deletes", () => VerifyChunkDeletes(corpus));
        Run("world_settings", () => VerifyWorldSettings(corpus));
        Run("history", () => VerifyHistory(corpus));
        Run("save_hook", () => VerifySaveHook(corpus));
        Run("derived_states", () => VerifyDerivedStates(corpus));
        Run("transforms", () => VerifyTransforms(corpus));
        Run("snbt", () => VerifySnbt(corpus));
        Run("chunk_creation", () => VerifyChunkCreation(corpus));
        Run("schem goldens", () => VerifySchemGoldens(corpus));

        Console.WriteLine();
        Console.WriteLine(_failures == 0
            ? $"PASS: {_checks} checks"
            : $"FAIL: {_failures} failures out of {_checks} checks");
        return _failures == 0 ? 0 : 1;
    }

    private static string FindCorpus()
    {
        string? directory = Environment.CurrentDirectory;
        while (directory != null)
        {
            string candidate = Path.Combine(directory, "fixtures", "conformance");
            if (File.Exists(Path.Combine(candidate, "manifest.json")))
                return candidate;
            directory = Path.GetDirectoryName(directory);
        }
        throw new InvalidOperationException(
            "cannot find fixtures/conformance/ above the working directory; "
            + "pass its path as the first argument");
    }

    private static void Run(string name, Action action)
    {
        try
        {
            int before = _failures;
            action();
            Console.WriteLine(_failures == before
                ? $"  PASS  {name}"
                : $"  FAIL  {name}");
        }
        catch (Exception exc)
        {
            _failures++;
            Console.WriteLine($"  FAIL  {name}: {exc.GetType().Name}: {exc.Message}");
        }
    }

    private static void Pending(string name, string waitingOn)
    {
        Console.WriteLine($"  PEND  {name} (awaiting {waitingOn})");
    }

    private static void Check(bool condition, string what)
    {
        _checks++;
        if (!condition)
        {
            _failures++;
            Console.WriteLine($"        FAILED: {what}");
        }
    }

    // -- helpers -----------------------------------------------------------

    private static JsonDocument Load(string corpus, string name) =>
        JsonDocument.Parse(File.ReadAllBytes(Path.Combine(corpus, name)));

    private static string Sha256Hex(byte[] data)
    {
        using var sha = SHA256.Create();
        return Convert.ToHexString(sha.ComputeHash(data)).ToLowerInvariant();
    }

    private static byte[] FromHex(string hex) => Convert.FromHexString(hex);

    private static sbyte[] ToSigned(byte[] raw)
    {
        var signed = new sbyte[raw.Length];
        for (int i = 0; i < raw.Length; i++)
            signed[i] = unchecked((sbyte)raw[i]);
        return signed;
    }

    private static string ExpectedString(JsonElement node)
    {
        if (node.TryGetProperty("cp", out var codepoints))
            return string.Concat(codepoints.EnumerateArray()
                .Select(cp => char.ConvertFromUtf32(cp.GetInt32())));
        return node.GetProperty("v").GetString()!;
    }

    // -- chunk creation ----------------------------------------------------

    private static List<int[]> Pairs(JsonElement node) =>
        node.EnumerateArray().Select(p => new[] { p[0].GetInt32(), p[1].GetInt32() }).ToList();

    private static bool SamePairs(List<(int X, int Z)> actual, JsonElement expected) =>
        actual.Select(p => $"{p.X},{p.Z}").SequenceEqual(
            Pairs(expected).Select(p => $"{p[0]},{p[1]}"));

    private static void VerifyChunkCreation(string corpus)
    {
        using var doc = Load(corpus, "chunk_creation.json");
        string fixtures = Path.GetDirectoryName(corpus.TrimEnd(Path.DirectorySeparatorChar))!;
        foreach (var run in doc.RootElement.GetProperty("runs").EnumerateArray())
        {
            string world = run.GetProperty("world").GetString()!;
            string directory = Path.Combine(Path.GetTempPath(),
                "conformance-create-" + Guid.NewGuid().ToString("N"));
            try
            {
                string regionDir = Path.Combine(directory, "dimensions", "minecraft", "overworld", "region");
                Directory.CreateDirectory(regionDir);
                File.Copy(Path.Combine(fixtures, run.GetProperty("region").GetString()!),
                          Path.Combine(regionDir, "r.-1.0.mca"));
                File.Copy(Path.Combine(fixtures, run.GetProperty("level").GetString()!),
                          Path.Combine(directory, "level.dat"));
                var save = MinecraftSave.Load(directory);
                int n = 0;
                foreach (var step in run.GetProperty("steps").EnumerateArray())
                    RunCreationStep(save, step, $"{world} step {n++}");
                foreach (var row in run.GetProperty("blocks_after").EnumerateArray())
                {
                    int x = row[0].GetInt32(), y = row[1].GetInt32(), z = row[2].GetInt32();
                    string dim = row[3].GetString()!;
                    string? got = save.GetBlock(x, y, z, dim)?.ToString();
                    Check(got == row[4].GetString(), $"{world} block ({x},{y},{z}) {dim}: {got}");
                }
                foreach (var row in run.GetProperty("surfaces_after").EnumerateArray())
                {
                    int x = row[0].GetInt32(), z = row[1].GetInt32();
                    string dim = row[2].GetString()!;
                    int? got = save.SurfaceY(x, z, dimension: dim);
                    int? want = row[3].ValueKind == JsonValueKind.Null ? null : row[3].GetInt32();
                    Check(got == want, $"{world} surface ({x},{z}) {dim}: {got}, expected {want}");
                }
                foreach (var entry in run.GetProperty("chunk_payload_sha256").EnumerateArray())
                {
                    int cx = entry.GetProperty("chunk")[0].GetInt32();
                    int cz = entry.GetProperty("chunk")[1].GetInt32();
                    var dim = save.Dimension(entry.GetProperty("dimension").GetString()!);
                    var chunk = dim.Chunk(cx, cz)!;
                    Check(dim.ChunkStatus(cx, cz) == entry.GetProperty("status").GetString(),
                          $"{world} chunk ({cx},{cz}): status");
                    Check(Sha256Hex(Codec.Encode(chunk.Data!, chunk.DataName))
                          == entry.GetProperty("payload_sha256").GetString(),
                          $"{world} chunk ({cx},{cz}): payload digest");
                }
            }
            finally
            {
                if (Directory.Exists(directory))
                    Directory.Delete(directory, true);
            }
        }
    }

    private static void RunCreationStep(MinecraftSave save, JsonElement step, string label)
    {
        string op = step.GetProperty("op").GetString()!;
        var want = step.GetProperty("result");
        bool refused = step.TryGetProperty("refused", out var flag) && flag.GetBoolean();
        string? Optional(string name) =>
            step.TryGetProperty(name, out var value) ? value.GetString() : null;
        switch (op)
        {
            case "stamp_level":
                ((TagCompound)save.Level!.Root!["Data"])["DataVersion"] =
                    new TagInt(step.GetProperty("data_version").GetInt32());
                break;
            case "create_chunks" when refused:
            {
                bool threw = false;
                try
                {
                    save.CreateChunks(Corner(step, "from"), Corner(step, "to"));
                }
                catch (NbtException)
                {
                    threw = true;
                }
                Check(threw, $"{label}: create_chunks must be refused");
                break;
            }
            case "create_chunks":
            {
                var r = save.CreateChunks(Corner(step, "from"), Corner(step, "to"),
                                          Optional("dimension"), Optional("biome"));
                Check(r.ChunksCreated == want.GetProperty("chunks_created").GetInt32(), $"{label}: chunks_created");
                Check(SamePairs(r.CreatedChunks, want.GetProperty("created_chunks")), $"{label}: created_chunks");
                Check(r.RegionsCreated == want.GetProperty("regions_created").GetInt32(), $"{label}: regions_created");
                Check(SamePairs(r.ExistingChunks, want.GetProperty("existing_chunks")), $"{label}: existing_chunks");
                Check(SamePairs(r.UnfinishedChunks, want.GetProperty("unfinished_chunks")), $"{label}: unfinished_chunks");
                Check(r.UnreadableChunks.Count == want.GetProperty("unreadable_chunks").GetInt32(), $"{label}: unreadable_chunks");
                Check(r.DataVersion == want.GetProperty("data_version").GetInt32(), $"{label}: data_version");
                int? Maybe(string name) => want.GetProperty(name).ValueKind == JsonValueKind.Null
                    ? null : want.GetProperty(name).GetInt32();
                Check(r.MinY == Maybe("min_y") && r.MaxY == Maybe("max_y"), $"{label}: height");
                Check(r.Biome == want.GetProperty("biome").GetString(), $"{label}: biome");
                Check(r.Complete == want.GetProperty("complete").GetBoolean(), $"{label}: complete");
                break;
            }
            case "create_chunk":
            {
                var chunk = step.GetProperty("chunk");
                bool threw = false;
                try
                {
                    save.Dimension(Optional("dimension")).CreateChunk(
                        chunk[0].GetInt32(), chunk[1].GetInt32(),
                        step.GetProperty("data_version").GetInt32(), step.GetProperty("biome").GetString()!);
                }
                catch (NbtException)
                {
                    threw = true;
                }
                Check(threw == want.GetProperty("refused").GetBoolean(), $"{label}: create_chunk refusal");
                break;
            }
            case "fill":
            {
                var r = save.Fill(Corner(step, "from"), Corner(step, "to"),
                                  BlockState.Parse(step.GetProperty("block").GetString()!), keepLoaded: true);
                Check(r.BlocksFilled == want.GetProperty("blocks_filled").GetInt32()
                      && r.ChunksModified == want.GetProperty("chunks_modified").GetInt32(), $"{label}: fill");
                break;
            }
            case "place":
            {
                var at = step.GetProperty("at");
                bool placed = save.PlaceBlock(at[0].GetInt32(), at[1].GetInt32(), at[2].GetInt32(),
                                              step.GetProperty("block").GetString()!);
                Check(placed == want.GetProperty("placed").GetBoolean(), $"{label}: place");
                break;
            }
            case "rebuild_heightmaps":
            {
                var r = save.RebuildHeightmaps(Corner(step, "from"), Corner(step, "to"), keepLoaded: true);
                Check(r.ChunksRebuilt == want.GetProperty("chunks_rebuilt").GetInt32(), $"{label}: rebuild");
                break;
            }
            case "import":
            {
                var size = step.GetProperty("size");
                var paste = new Schematic(size[0].GetInt32(), size[1].GetInt32(), size[2].GetInt32(),
                                          step.GetProperty("data_version").GetInt32());
                foreach (var cell in step.GetProperty("cells").EnumerateArray())
                    paste.SetBlock(cell[0].GetInt32(), cell[1].GetInt32(), cell[2].GetInt32(),
                                   cell[3].GetString()!);
                bool voids = step.TryGetProperty("paste_voids", out var flagVoids) && flagVoids.GetBoolean();
                var r = save.ImportSchem(paste, Corner(step, "at"), anchor: "min", pasteVoids: voids);
                Check(r.BlocksPasted == want.GetProperty("blocks_pasted").GetInt32()
                      && r.AirSkipped == want.GetProperty("air_skipped").GetInt32()
                      && r.VoidsSkipped == want.GetProperty("voids_skipped").GetInt32()
                      && r.ChunksModified == want.GetProperty("chunks_modified").GetInt32(),
                      $"{label}: import pasted {r.BlocksPasted}, voids skipped {r.VoidsSkipped}");
                break;
            }
            default:
                throw new InvalidOperationException($"unknown chunk_creation op {op}");
        }
    }

    // -- snbt --------------------------------------------------------------

    /// <summary>A string as snbt.json spells one: a JSON string, or the
    /// code points of one holding a lone surrogate, which
    /// char.ConvertFromUtf32 would refuse.</summary>
    private static string CorpusText(JsonElement node)
    {
        if (!node.TryGetProperty("cp", out var codepoints))
            return node.GetProperty("v").GetString()!;
        var text = new System.Text.StringBuilder();
        foreach (var cp in codepoints.EnumerateArray())
        {
            int code = cp.GetInt32();
            if (code >= 0x10000)
                text.Append(char.ConvertFromUtf32(code));
            else
                text.Append((char)code);
        }
        return text.ToString();
    }

    private static string Shown(string text) =>
        text.Length <= 40 ? text : text.Substring(0, 37) + "...";

    private static void VerifySnbt(string corpus)
    {
        using var doc = Load(corpus, "snbt.json");
        foreach (var item in doc.RootElement.GetProperty("parse").EnumerateArray())
        {
            string text = CorpusText(item.GetProperty("in"));
            Tag? tag = null;
            string? why = null;
            try
            {
                tag = Snbt.ParseSnbt(text);
            }
            catch (NbtException exc)
            {
                why = exc.Message;
            }
            if (item.TryGetProperty("refused", out _))
            {
                Check(tag == null, $"snbt {Shown(text)}: must be refused, read as "
                                   + (tag == null ? "" : Snbt.ToSnbt(tag)));
                continue;
            }
            Check(tag != null, $"snbt {Shown(text)}: refused ({why})");
            if (tag == null)
                continue;
            var root = new TagCompound();
            root["v"] = tag;
            string binary = Convert.ToHexString(Codec.Encode(root)).ToLowerInvariant();
            Check(binary == item.GetProperty("nbt").GetString(), $"snbt {Shown(text)}: binary");
            string written = Snbt.ToSnbt(tag);
            string expected = CorpusText(item.GetProperty("snbt"));
            Check(written == expected, $"snbt {Shown(text)}: wrote {Shown(written)}, expected {Shown(expected)}");
        }
        foreach (var item in doc.RootElement.GetProperty("print").EnumerateArray())
        {
            string hex = item.GetProperty("nbt").GetString()!;
            var (_, root) = Codec.Decode(FromHex(hex));
            string written = Snbt.ToSnbt(root["v"]);
            string expected = CorpusText(item.GetProperty("snbt"));
            Check(written == expected, $"snbt print {Shown(hex)}: wrote {Shown(written)}, expected {Shown(expected)}");
        }
    }

    // -- manifest ----------------------------------------------------------

    private static void VerifyManifest(string corpus)
    {
        using var manifest = Load(corpus, "manifest.json");
        foreach (var artifact in manifest.RootElement.GetProperty("artifacts")
                     .EnumerateObject())
        {
            byte[] data = File.ReadAllBytes(Path.Combine(corpus, artifact.Name));
            Check(Sha256Hex(data) == artifact.Value.GetProperty("sha256").GetString(),
                  $"{artifact.Name}: manifest digest");
            Check(data.Length == artifact.Value.GetProperty("bytes").GetInt32(),
                  $"{artifact.Name}: manifest size");
        }
    }

    // -- nbt_cases ---------------------------------------------------------

    private static void VerifyNbtCases(string corpus)
    {
        using var doc = Load(corpus, "nbt_cases.json");
        foreach (var item in doc.RootElement.GetProperty("cases").EnumerateArray())
        {
            string name = item.GetProperty("name").GetString()!;
            byte[] binary = FromHex(item.GetProperty("binary").GetString()!);
            var (rootName, root) = Codec.Decode(binary);
            Check(rootName == ExpectedRootName(item), $"{name}: root name");
            CompareTag(name, item.GetProperty("expected"), root);
            if (item.GetProperty("roundtrip_exact").GetBoolean())
            {
                byte[] reencoded = Codec.Encode(root, rootName);
                Check(reencoded.AsSpan().SequenceEqual(binary),
                      $"{name}: re-encode must be byte-identical");
            }
        }
    }

    private static string ExpectedRootName(JsonElement item)
    {
        // root_name is plain JSON text; non-ASCII arrives as \u escapes,
        // which JsonDocument already decodes to UTF-16.
        return item.GetProperty("root_name").GetString()!;
    }

    private static void CompareTag(string context, JsonElement expected, Tag actual)
    {
        string kind = expected.GetProperty("t").GetString()!;
        switch (kind)
        {
            case "compound":
            {
                Check(actual is TagCompound, $"{context}: expected compound");
                if (actual is not TagCompound compound) return;
                var children = expected.GetProperty("v");
                int expectedCount = children.EnumerateObject().Count();
                Check(compound.Count == expectedCount,
                      $"{context}: compound has {compound.Count} children, "
                      + $"expected {expectedCount}");
                foreach (var child in children.EnumerateObject())
                {
                    var tag = compound.Get(child.Name);
                    Check(tag != null, $"{context}: missing key '{child.Name}'");
                    if (tag != null)
                        CompareTag($"{context}.{child.Name}", child.Value, tag);
                }
                break;
            }
            case "list":
            {
                Check(actual is TagList, $"{context}: expected list");
                if (actual is not TagList list) return;
                Check(Tag.NameOf(list.Count > 0 ? list.Items[0].Id : list.ElementType)
                          == expected.GetProperty("e").GetString(),
                      $"{context}: list element type");
                var items = expected.GetProperty("v").EnumerateArray().ToList();
                Check(list.Count == items.Count, $"{context}: list length");
                for (int i = 0; i < Math.Min(list.Count, items.Count); i++)
                    CompareTag($"{context}[{i}]", items[i], list.Items[i]);
                break;
            }
            case "byte":
                Check(actual is TagByte b && b.Value == expected.GetProperty("v").GetSByte(),
                      $"{context}: byte value");
                break;
            case "short":
                Check(actual is TagShort s && s.Value == expected.GetProperty("v").GetInt16(),
                      $"{context}: short value");
                break;
            case "int":
                Check(actual is TagInt intTag
                          && intTag.Value == expected.GetProperty("v").GetInt32(),
                      $"{context}: int value");
                break;
            case "long":
                Check(actual is TagLong l
                          && l.Value == long.Parse(expected.GetProperty("v").GetString()!),
                      $"{context}: long value");
                break;
            case "float":
                Check(actual is TagFloat f && FloatBitsHex(f) ==
                          expected.GetProperty("bits").GetString(),
                      $"{context}: float bits");
                break;
            case "double":
                Check(actual is TagDouble d && DoubleBitsHex(d) ==
                          expected.GetProperty("bits").GetString(),
                      $"{context}: double bits");
                break;
            case "string":
                Check(actual is TagString str && str.Value == ExpectedString(expected),
                      $"{context}: string value");
                break;
            case "byte_array":
                Check(actual is TagByteArray bytes && bytes.Values.SequenceEqual(
                          expected.GetProperty("v").EnumerateArray()
                              .Select(v => v.GetSByte())),
                      $"{context}: byte array");
                break;
            case "int_array":
                Check(actual is TagIntArray ints && ints.Values.SequenceEqual(
                          expected.GetProperty("v").EnumerateArray()
                              .Select(v => v.GetInt32())),
                      $"{context}: int array");
                break;
            case "long_array":
                Check(actual is TagLongArray longs && longs.Values.SequenceEqual(
                          expected.GetProperty("v").EnumerateArray()
                              .Select(v => long.Parse(v.GetString()!))),
                      $"{context}: long array");
                break;
            default:
                Check(false, $"{context}: unknown expected kind '{kind}'");
                break;
        }
    }

    private static string FloatBitsHex(TagFloat tag)
    {
        byte[] bits = tag.RawBits ?? new byte[4];
        if (tag.RawBits == null)
            System.Buffers.Binary.BinaryPrimitives.WriteInt32BigEndian(
                bits, BitConverter.SingleToInt32Bits(tag.Value));
        return Convert.ToHexString(bits).ToLowerInvariant();
    }

    private static string DoubleBitsHex(TagDouble tag)
    {
        byte[] bits = tag.RawBits ?? new byte[8];
        if (tag.RawBits == null)
            System.Buffers.Binary.BinaryPrimitives.WriteInt64BigEndian(
                bits, BitConverter.DoubleToInt64Bits(tag.Value));
        return Convert.ToHexString(bits).ToLowerInvariant();
    }

    // -- packing, varints, nibbles ----------------------------------------

    private static void VerifyPacking(string corpus)
    {
        using var doc = Load(corpus, "packing.json");
        foreach (var item in doc.RootElement.GetProperty("cases").EnumerateArray())
        {
            int bits;
            if (item.TryGetProperty("kind", out var kind)
                && kind.GetString() == "wider_than_palette")
            {
                int[] values = item.GetProperty("values").EnumerateArray()
                    .Select(v => v.GetInt32()).ToArray();
                long[] longs = item.GetProperty("longs").EnumerateArray()
                    .Select(v => long.Parse(v.GetString()!)).ToArray();
                int count = item.GetProperty("count").GetInt32();
                int inferred = Packing.InferBits(
                    item.GetProperty("palette_size").GetInt32(), longs.Length,
                    count, item.GetProperty("minimum_bits").GetInt32());
                Check(inferred == item.GetProperty("packed_bits").GetInt32(),
                      "wider_than_palette: inferred width");
                Check(Packing.UnpackIndices(longs, count, inferred).SequenceEqual(values),
                      "wider_than_palette: unpack at the inferred width");
                continue;
            }

            bits = item.GetProperty("bits").GetInt32();
            int caseCount = item.GetProperty("count").GetInt32();
            if (item.TryGetProperty("values_uniform", out var uniform))
            {
                var values = Enumerable.Repeat(uniform.GetInt32(), caseCount).ToArray();
                long[] packed = Packing.PackIndices(values, caseCount, bits);
                byte[] digestInput = new byte[packed.Length * 8];
                for (int i = 0; i < packed.Length; i++)
                    System.Buffers.Binary.BinaryPrimitives.WriteInt64BigEndian(
                        digestInput.AsSpan(i * 8, 8), packed[i]);
                Check(Sha256Hex(digestInput)
                          == item.GetProperty("longs_sha256").GetString(),
                      $"packing bits={bits} uniform: longs digest");
            }
            else
            {
                int[] values = item.GetProperty("values").EnumerateArray()
                    .Select(v => v.GetInt32()).ToArray();
                long[] expected = item.GetProperty("longs").EnumerateArray()
                    .Select(v => long.Parse(v.GetString()!)).ToArray();
                long[] packed = Packing.PackIndices(values, caseCount, bits);
                Check(packed.SequenceEqual(expected), $"packing bits={bits}: pack");
                Check(Packing.UnpackIndices(expected, caseCount, bits).SequenceEqual(values),
                      $"packing bits={bits}: unpack");
            }
        }
    }

    private static void VerifyVarints(string corpus)
    {
        using var doc = Load(corpus, "varints.json");
        foreach (var item in doc.RootElement.GetProperty("singles").EnumerateArray())
        {
            int value = item.GetProperty("value").GetInt32();
            byte[] expected = FromHex(item.GetProperty("bytes").GetString()!);
            sbyte[] encoded = Support.WriteVarints(new[] { value });
            Check(ToSigned(expected).SequenceEqual(encoded), $"varint {value}: encode");
            Check(Support.ReadVarints(ToSigned(expected), 1, "varint")[0] == value,
                  $"varint {value}: decode");
        }
        var sequence = doc.RootElement.GetProperty("sequence");
        int[] values2 = sequence.GetProperty("values").EnumerateArray()
            .Select(v => v.GetInt32()).ToArray();
        byte[] raw = FromHex(sequence.GetProperty("bytes").GetString()!);
        Check(Support.ReadVarints(ToSigned(raw), values2.Length, "sequence")
                  .SequenceEqual(values2), "varint sequence: decode");
        Check(ToSigned(raw).SequenceEqual(Support.WriteVarints(values2)),
              "varint sequence: encode");
    }

    private static void VerifyNibbles(string corpus)
    {
        using var doc = Load(corpus, "nibbles.json");
        var example = doc.RootElement.GetProperty("example");
        byte[] doc4 = FromHex(example.GetProperty("bytes").GetString()!);
        int[] head = new int[8];
        for (int i = 0; i < 4; i++)
        {
            int b = doc4[i];
            head[i * 2] = b & 0xF;
            head[i * 2 + 1] = b >> 4;
        }
        Check(head.SequenceEqual(example.GetProperty("first_values").EnumerateArray()
                  .Select(v => v.GetInt32())), "nibble example");

        var full = doc.RootElement.GetProperty("full");
        int[] values = Packing.UnpackNibbles(
            ToSigned(FromHex(full.GetProperty("bytes").GetString()!)));
        Check(values.Take(32).SequenceEqual(full.GetProperty("first_values")
                  .EnumerateArray().Select(v => v.GetInt32())),
              "nibbles: first values");
        Check(Sha256Hex(values.Select(v => (byte)v).ToArray())
                  == full.GetProperty("values_sha256").GetString(),
              "nibbles: full digest");
    }

    // -- region ------------------------------------------------------------

    private static void VerifyRegion(string corpus)
    {
        using var doc = Load(corpus, "region.json");
        string file = Path.GetFullPath(Path.Combine(
            corpus, doc.RootElement.GetProperty("file").GetString()!));
        byte[] raw = File.ReadAllBytes(file);
        Check(Sha256Hex(raw) == doc.RootElement.GetProperty("file_sha256").GetString(),
              "region: fixture digest");

        var region = NbtFile.Load(file);
        Check(region.Kind == NbtKind.Region, "region: detected as a region");
        Check(region.ToBytes().AsSpan().SequenceEqual(raw),
              "region: untouched round trip must be byte-identical");
        Check(!region.IsModified(), "region: unmodified after load");

        var chunkSpecs = doc.RootElement.GetProperty("chunks").EnumerateArray().ToList();
        Check(region.Chunks.Count == chunkSpecs.Count, "region: chunk count");
        foreach (var spec in chunkSpecs)
        {
            int x = spec.GetProperty("x").GetInt32();
            int z = spec.GetProperty("z").GetInt32();
            var chunk = region.Chunk(x, z);
            Check(chunk != null, $"region: chunk ({x}, {z}) present");
            if (chunk == null)
                continue;
            Check(chunk.CompressionScheme == spec.GetProperty("compression").GetByte(),
                  $"chunk ({x}, {z}): compression");
            Check(chunk.Timestamp == spec.GetProperty("timestamp").GetUInt32(),
                  $"chunk ({x}, {z}): timestamp");
            var data = chunk.Data;
            Check(data != null && chunk.ParseError == null,
                  $"chunk ({x}, {z}): decodes ({chunk.ParseError})");
            if (data == null)
                continue;
            byte[] payload = Codec.Encode(data, chunk.DataName);
            Check(Sha256Hex(payload) == spec.GetProperty("payload_sha256").GetString(),
                  $"chunk ({x}, {z}): payload digest");
            Check(((TagInt)data["DataVersion"]).Value
                      == spec.GetProperty("data_version").GetInt32(),
                  $"chunk ({x}, {z}): DataVersion");
            Check(((TagString)data["Status"]).Value
                      == spec.GetProperty("status").GetString(),
                  $"chunk ({x}, {z}): Status");
            chunk.Release();
        }
        Check(region.ToBytes().AsSpan().SequenceEqual(raw),
              "region: still byte-identical after decode + release of every chunk");

        var emptySpec = doc.RootElement.GetProperty("empty_region");
        string emptyPath = Path.Combine(corpus, emptySpec.GetProperty("file").GetString()!);
        var empty = NbtFile.Load(emptyPath);
        Check(empty.Kind == NbtKind.Region, "empty region: detected as a region");
        Check(empty.Chunks.Count == 0, "empty region: no chunks");
        Check(empty.ToBytes().Length == 0, "empty region: stays zero bytes");
    }

    // -- world -------------------------------------------------------------

    /// <summary>Assemble a throwaway world the way the corpus prescribes:
    /// the vendored region under the overworld, level.dat at the root.</summary>
    /// <summary>The legacy fixture (fixtures/r.-1.0.mca and its level.dat)
    /// as a one-region overworld, the way world.json, world_edits.json and
    /// chunk_deletes.json describe it.</summary>
    private static string AssembleWorld(string corpus, bool sidecars = false)
    {
        string temp = AssembleWorldFrom(corpus, ".");
        string overworld = Path.Combine(temp, "dimensions", "minecraft", "overworld");
        // chunk_deletes.json is the one artifact that ships its own inputs:
        // the vendored region has no entity or POI storage, and those two
        // trees are exactly what a delete forgets.
        if (sidecars)
        {
            foreach (string tree in new[] { "entities", "poi" })
            {
                string directory = Path.Combine(overworld, tree);
                Directory.CreateDirectory(directory);
                File.Copy(Path.Combine(corpus, "chunk_deletes", tree, "r.-1.0.mca"),
                          Path.Combine(directory, "r.-1.0.mca"));
            }
            // A second entities file one region up-left, holding a single
            // chunk at negative z. It is what makes a box span two region
            // files, and what a delete empties outright.
            File.Copy(
                Path.Combine(corpus, "chunk_deletes", "entities", "r.-1.-1.mca"),
                Path.Combine(overworld, "entities", "r.-1.-1.mca"));
        }
        return temp;
    }

    /// <summary>A one-region overworld in a fresh temp directory, from the
    /// region file and level.dat of one subdirectory of fixtures/: "." is
    /// the 26.2 fixture, "modern" the four 26.3 chunks, and "legacy" (its
    /// region r.5.-1.mca) the sixteen 1.17.1 ones.</summary>
    private static string AssembleWorldFrom(string corpus, string fixture,
                                            string region = "r.-1.0.mca")
    {
        string temp = Path.Combine(
            Path.GetTempPath(), "hv-conformance-world-" + Guid.NewGuid().ToString("N"));
        string regionDir = Path.Combine(
            temp, "dimensions", "minecraft", "overworld", "region");
        Directory.CreateDirectory(regionDir);
        string source = Path.GetFullPath(Path.Combine(corpus, "..", fixture));
        File.Copy(Path.Combine(source, region), Path.Combine(regionDir, region));
        File.Copy(Path.Combine(source, "level.dat"),
                  Path.Combine(temp, "level.dat"));
        return temp;
    }

    private static void VerifyWorld(string corpus)
    {
        using var doc = Load(corpus, "world.json");
        var root = doc.RootElement;

        string temp = AssembleWorld(corpus);
        try
        {
            var save = MinecraftSave.Load(
                temp, new[] { DimensionNames.Overworld });
            var dim = save.Dimension(DimensionNames.Overworld);

            foreach (var sample in root.GetProperty("blocks").EnumerateArray())
            {
                int x = sample[0].GetInt32();
                int y = sample[1].GetInt32();
                int z = sample[2].GetInt32();
                string? expected = sample[3].ValueKind == JsonValueKind.Null
                    ? null : sample[3].GetString();
                string? actual = save.GetBlock(x, y, z)?.ToString();
                Check(actual == expected,
                      $"block ({x}, {y}, {z}): got {actual ?? "null"}, "
                      + $"want {expected ?? "null"}");
            }

            foreach (var sample in root.GetProperty("biomes").EnumerateArray())
            {
                int x = sample[0].GetInt32();
                int y = sample[1].GetInt32();
                int z = sample[2].GetInt32();
                string? expected = sample[3].ValueKind == JsonValueKind.Null
                    ? null : sample[3].GetString();
                string? actual = save.GetBiome(x, y, z);
                Check(actual == expected,
                      $"biome ({x}, {y}, {z}): got {actual ?? "null"}, "
                      + $"want {expected ?? "null"}");
            }

            CheckSurfaces(save, root.GetProperty("surfaces"), "");

            foreach (var sample in root.GetProperty("light")
                         .GetProperty("columns").EnumerateArray())
            {
                int x = sample[0].GetInt32();
                int y = sample[1].GetInt32();
                int z = sample[2].GetInt32();
                var accessor = dim.Blocks(x >> 4, z >> 4)!;
                int? sky = accessor.SkyLight(x & 15, y, z & 15);
                int? block = accessor.BlockLight(x & 15, y, z & 15);
                int? wantSky = sample[3].ValueKind == JsonValueKind.Null
                    ? (int?)null : sample[3].GetInt32();
                int? wantBlock = sample[4].ValueKind == JsonValueKind.Null
                    ? (int?)null : sample[4].GetInt32();
                Check(sky == wantSky && block == wantBlock,
                      $"light ({x}, {y}, {z}): got sky={sky?.ToString() ?? "null"} "
                      + $"block={block?.ToString() ?? "null"}, want "
                      + $"sky={wantSky?.ToString() ?? "null"} "
                      + $"block={wantBlock?.ToString() ?? "null"}");
            }

            var entitySpec = root.GetProperty("block_entities");
            int chunkX = entitySpec.GetProperty("chunk")[0].GetInt32();
            int chunkZ = entitySpec.GetProperty("chunk")[1].GetInt32();
            var chunk = dim.Chunk(chunkX, chunkZ)!;
            var entries = entitySpec.GetProperty("entries").EnumerateArray().ToList();
            var stored = (TagList)chunk.Data!["block_entities"];
            Check(stored.Count == entries.Count,
                  $"block entities in ({chunkX}, {chunkZ}): got {stored.Count}, "
                  + $"want {entries.Count}");
            for (int i = 0; i < Math.Min(stored.Count, entries.Count); i++)
            {
                var entity = (TagCompound)stored[i];
                Check(Tag.AsString(entity["id"]) == entries[i].GetProperty("id").GetString()
                      && Tag.AsInt(entity["x"]) == entries[i].GetProperty("x").GetInt32()
                      && Tag.AsInt(entity["y"]) == entries[i].GetProperty("y").GetInt32()
                      && Tag.AsInt(entity["z"]) == entries[i].GetProperty("z").GetInt32(),
                      $"block entity {i} in ({chunkX}, {chunkZ})");
            }

            var histogramSpec = root.GetProperty("histogram_top10");
            chunkX = histogramSpec.GetProperty("chunk")[0].GetInt32();
            chunkZ = histogramSpec.GetProperty("chunk")[1].GetInt32();
            // The corpus object's keys are alphabetized by the generator's
            // sort_keys, so compare the top-10 as a mapping: which states
            // made the cut, and each one's exact count.
            var top = dim.Blocks(chunkX, chunkZ)!.Histogram()
                .Select(pair => (Key: pair.Key.ToString(), pair.Value))
                .OrderByDescending(pair => pair.Value)
                .ThenBy(pair => pair.Key, StringComparer.Ordinal)
                .Take(10)
                .OrderBy(pair => pair.Key, StringComparer.Ordinal)
                .ToList();
            var wantTop = histogramSpec.GetProperty("counts").EnumerateObject()
                .Select(property => (Key: property.Name, Value: property.Value.GetInt32()))
                .ToList();
            Check(top.Count == wantTop.Count, "histogram: top-10 size");
            for (int i = 0; i < Math.Min(top.Count, wantTop.Count); i++)
            {
                Check(top[i].Key == wantTop[i].Key && top[i].Value == wantTop[i].Value,
                      $"histogram entry {i}: got {top[i].Key}={top[i].Value}, "
                      + $"want {wantTop[i].Key}={wantTop[i].Value}");
            }

            // FindBlocks: every match mode, and the counters that prove the
            // palette short-circuit fired. Match ORDER is part of the
            // contract -- chunk, then section, then y, z, x -- so the lists
            // are compared position by position, not as sets.
            int search = 0;
            foreach (var spec in root.GetProperty("find_blocks").EnumerateArray())
            {
                var box = spec.GetProperty("box");
                var lo = (box[0][0].GetInt32(), box[0][1].GetInt32(), box[0][2].GetInt32());
                var hi = (box[1][0].GetInt32(), box[1][1].GetInt32(), box[1][2].GetInt32());
                string[] names = spec.GetProperty("names").EnumerateArray()
                    .Select(value => value.GetString()!).ToArray();
                string mode = spec.GetProperty("match").GetString()!;
                var limitValue = spec.GetProperty("limit");
                int? limit = limitValue.ValueKind == JsonValueKind.Null
                    ? (int?)null : limitValue.GetInt32();

                var found = save.FindBlocks(lo, hi, names, mode, limit: limit);
                var wantMatches = spec.GetProperty("matches").EnumerateArray().ToList();
                string label = $"find_blocks[{search}] {mode} {string.Join("+", names)}";
                Check(found.Matches.Count == wantMatches.Count,
                      $"{label}: {found.Matches.Count} matches, want {wantMatches.Count}");
                for (int i = 0; i < Math.Min(found.Matches.Count, wantMatches.Count); i++)
                {
                    var (mx, my, mz, state) = found.Matches[i];
                    var want = wantMatches[i];
                    Check(mx == want[0].GetInt32() && my == want[1].GetInt32()
                          && mz == want[2].GetInt32()
                          && string.CompareOrdinal(state.ToString(),
                                                   want[3].GetString()) == 0,
                          $"{label} match {i}: got ({mx},{my},{mz}) {state}, want "
                          + $"({want[0].GetInt32()},{want[1].GetInt32()},"
                          + $"{want[2].GetInt32()}) {want[3].GetString()}");
                }
                var wantCounts = spec.GetProperty("counts").EnumerateObject()
                    .Select(property => (property.Name, property.Value.GetInt32()))
                    .ToList();
                var gotCounts = found.Counts
                    .Select(pair => (Name: pair.Key.ToString(), pair.Value))
                    .OrderBy(pair => pair.Name, StringComparer.Ordinal)
                    .ToList();
                Check(gotCounts.Count == wantCounts.Count,
                      $"{label}: {gotCounts.Count} distinct states, want {wantCounts.Count}");
                for (int i = 0; i < Math.Min(gotCounts.Count, wantCounts.Count); i++)
                {
                    Check(string.CompareOrdinal(gotCounts[i].Name, wantCounts[i].Name) == 0
                          && gotCounts[i].Value == wantCounts[i].Item2,
                          $"{label} count {i}: got {gotCounts[i].Name}="
                          + $"{gotCounts[i].Value}, want {wantCounts[i].Name}="
                          + $"{wantCounts[i].Item2}");
                }
                Check(found.ChunksScanned == spec.GetProperty("chunks_scanned").GetInt32(),
                      $"{label}: chunks_scanned {found.ChunksScanned}");
                Check(found.SectionsScanned == spec.GetProperty("sections_scanned").GetInt32(),
                      $"{label}: sections_scanned {found.SectionsScanned}");
                Check(found.SectionsSkipped == spec.GetProperty("sections_skipped").GetInt32(),
                      $"{label}: sections_skipped {found.SectionsSkipped} (the "
                      + "palette short-circuit)");
                Check(found.MissingChunks.Count
                          == spec.GetProperty("missing_chunks").GetArrayLength(),
                      $"{label}: missing_chunks {found.MissingChunks.Count}");
                Check(found.Truncated == spec.GetProperty("truncated").GetBoolean(),
                      $"{label}: truncated {found.Truncated}");
                Check(found.Complete == spec.GetProperty("complete").GetBoolean(),
                      $"{label}: complete {found.Complete}");
                search++;
            }

            // EntitiesIn over a world that stores no entities at all: the
            // case a port is most likely to get wrong, because absent
            // storage is a complete answer, not a failure.
            var entityQuerySpec = root.GetProperty("entities_in");
            var ebox = entityQuerySpec.GetProperty("box");
            var elo = (ebox[0][0].GetInt32(), ebox[0][1].GetInt32(), ebox[0][2].GetInt32());
            var ehi = (ebox[1][0].GetInt32(), ebox[1][1].GetInt32(), ebox[1][2].GetInt32());
            var entities = save.EntitiesIn(elo, ehi);
            Check(entities.Entities.Count
                      == entityQuerySpec.GetProperty("entities").GetArrayLength(),
                  $"entities_in: {entities.Entities.Count} entities");
            Check(entities.ChunksScanned
                      == entityQuerySpec.GetProperty("chunks_scanned").GetInt32(),
                  $"entities_in: chunks_scanned {entities.ChunksScanned}");
            Check(entities.ChunksWithoutStorage
                      == entityQuerySpec.GetProperty("chunks_without_storage").GetInt32(),
                  $"entities_in: chunks_without_storage {entities.ChunksWithoutStorage}");
            Check(entities.UnreadableChunks.Count
                      == entityQuerySpec.GetProperty("unreadable_chunks").GetArrayLength(),
                  $"entities_in: unreadable_chunks {entities.UnreadableChunks.Count}");
            Check(entities.Truncated == entityQuerySpec.GetProperty("truncated").GetBoolean(),
                  $"entities_in: truncated {entities.Truncated}");
            Check(entities.Complete == entityQuerySpec.GetProperty("complete").GetBoolean(),
                  $"entities_in: complete {entities.Complete} -- absent storage "
                  + "is not a failure");
        }
        finally
        {
            Directory.Delete(temp, recursive: true);
        }
    }

    // -- world edits -------------------------------------------------------

    /// <summary>Assemble the fixture world, run the corpus's edit script,
    /// and check the reported counts, the blocks read back, and the
    /// resulting chunk NBT byte-for-byte. The script is data in the corpus,
    /// so this executes the same operations the reference did rather than a
    /// transcription of them.</summary>
    private static void VerifyHistory(string corpus)
    {
        using var doc = Load(corpus, "history.json");
        string temp = AssembleWorld(corpus);
        try
        {
            var save = MinecraftSave.Load(temp);
            var dim = save.Dimension(DimensionNames.Overworld);
            void Capture(string name)
            {
                var want = doc.RootElement.GetProperty(name);
                var chunk = dim.Chunk(-25, 1);
                string? digest = chunk == null ? null
                    : Sha256Hex(Codec.Encode(chunk.Data!, chunk.DataName));
                Check(digest == want.GetProperty("chunk").GetString(), name + " chunk bytes");
                Check(Sha256Hex(Codec.Encode(save.Level!.Root!, save.Level.RootName))
                    == want.GetProperty("level").GetString(), name + " level bytes");
                Check(dim.Region(-1, 0)!.Chunks.Count == want.GetProperty("chunks").GetInt32(),
                    name + " chunk count");
            }
            var edit = save.BeginEdit();
            Capture("before");
            save.Fill((-400, 90, 20), (-398, 92, 22), "minecraft:gold_block");
            save.SetAllowCommands(!save.AllowCommands());
            edit.Complete();
            Check(edit.Changed && edit.ByteSize > 0, "history retains edited data");
            Capture("edited");
            var deletion = save.BeginEdit();
            save.DeleteChunks((-400, 0, 16), (-385, 0, 31), DimensionNames.Overworld);
            deletion.Complete();
            Capture("deleted");
            deletion.Undo();
            Capture("delete_undone");
            save.Save();
            edit.Undo();
            Capture("undo_after_save");
            save.Save();
            Check(Sha256Hex(File.ReadAllBytes(dim.Region(-1, 0)!.Path!))
                == doc.RootElement.GetProperty("restored_region").GetString(),
                "history restores original region bytes on disk");
            edit.Redo();
            Capture("redone");
            // Chunks built from nothing, in a region file the world lacks.
            var want = doc.RootElement.GetProperty("built");
            save.Save();
            var built = save.BeginEdit();
            save.CreateChunks((640, 0, 640), (655, 0, 655));
            built.Complete();
            var paths = built.Paths.Select(path =>
                System.IO.Path.GetRelativePath(temp, path).Replace('\\', '/')).ToArray();
            Check(paths.SequenceEqual(want.GetProperty("paths").EnumerateArray()
                .Select(item => item.GetString())), "history names the region it built");
            built.Undo();
            Check(dim.Chunk(40, 40) == null == want.GetProperty("undone_absent").GetBoolean(),
                "history takes built chunks back");
            Check(save.Save() == want.GetProperty("undone_written").GetInt32(),
                "history writes nothing for chunks it took back");
            Check(File.Exists(System.IO.Path.Combine(temp, paths[0]))
                == want.GetProperty("undone_file").GetBoolean(),
                "history leaves no file for chunks it took back");
            built.Redo();
            Check(dim.Chunk(40, 40) != null == want.GetProperty("redone_present").GetBoolean(),
                "history builds the chunks again");
            var empty = save.BeginEdit();
            bool refused = false;
            try { save.Save(); } catch (NbtException) { refused = true; }
            Check(refused, "history refuses save before completion");
            empty.Complete();
            Check(!empty.Changed, "history omits no-op edits");
        }
        finally { Directory.Delete(temp, recursive: true); }
    }

    /// <summary>What Save(beforeWrite:) is offered: every file the save
    /// writes, as it stood, nothing on a save with nothing to write, and a
    /// refusal that stops the save with the file it was offered
    /// untouched.</summary>
    private static void VerifySaveHook(string corpus)
    {
        using var doc = Load(corpus, "save_hook.json");
        var want = doc.RootElement;
        string temp = AssembleWorld(corpus);
        try
        {
            var save = MinecraftSave.Load(temp);
            save.Fill((-400, 90, 20), (-398, 92, 22), "minecraft:gold_block");
            save.SetAllowCommands(!save.AllowCommands());
            save.CreateChunks((640, 0, 640), (655, 0, 655));
            var offered = new List<(string Path, bool Existed)>();
            void Record(string path) => offered.Add(
                (System.IO.Path.GetRelativePath(temp, path).Replace('\\', '/'), File.Exists(path)));
            Check(save.Save(beforeWrite: Record) == want.GetProperty("written").GetInt32(),
                  "save_hook files written");
            var expected = want.GetProperty("offered").EnumerateArray()
                .Select(item => (item.GetProperty("path").GetString()!,
                                 item.GetProperty("existed").GetBoolean())).ToList();
            Check(offered.OrderBy(item => item.Path, StringComparer.Ordinal).SequenceEqual(expected),
                  "save_hook offers every file, as it stood, before writing it");
            offered.Clear();
            Check(save.Save(beforeWrite: Record) == want.GetProperty("unchanged_written").GetInt32()
                  && offered.Count == want.GetProperty("unchanged_offered").GetInt32(),
                  "save_hook offers nothing when nothing changed");
            save.Fill((-400, 93, 20), (-398, 93, 22), "minecraft:stone");
            string region = System.IO.Path.Combine(temp, "dimensions", "minecraft", "overworld",
                                                   "region", "r.-1.0.mca");
            string before = Sha256Hex(File.ReadAllBytes(region));
            bool refused = false;
            try { save.Save(beforeWrite: _ => throw new IOException("refused")); }
            catch (IOException) { refused = true; }
            Check(refused == want.GetProperty("refused").GetBoolean(),
                  "save_hook refusal stops the save");
            Check((Sha256Hex(File.ReadAllBytes(region)) == before)
                  == want.GetProperty("refused_left_file").GetBoolean(),
                  "save_hook refusal leaves the file it was offered");
            Check(save.Save() == want.GetProperty("then_written").GetInt32(),
                  "save_hook edit survives a refused save");
        }
        finally { Directory.Delete(temp, recursive: true); }
    }

    private static void VerifyWorldEdits(string corpus)
    {
        using var doc = Load(corpus, "world_edits.json");
        var root = doc.RootElement;
        string temp = AssembleWorld(corpus);
        try
        {
            var save = MinecraftSave.Load(temp, new[] { DimensionNames.Overworld });
            RunEditScript(save, root.GetProperty("steps"));

            CheckBlockReads(save, root.GetProperty("blocks_after"), "after edits, ");

            foreach (var sample in root.GetProperty("biomes_after").EnumerateArray())
            {
                int x = sample[0].GetInt32(), y = sample[1].GetInt32(),
                    z = sample[2].GetInt32();
                string? expected = sample[3].ValueKind == JsonValueKind.Null
                    ? null : sample[3].GetString();
                string? actual = save.GetBiome(x, y, z);
                Check(actual == expected,
                      $"after edits, biome ({x}, {y}, {z}): got {actual ?? "null"}, "
                      + $"want {expected ?? "null"}");
            }

            foreach (var column in root.GetProperty("surfaces_after").EnumerateArray())
            {
                int x = column.GetProperty("x").GetInt32();
                int z = column.GetProperty("z").GetInt32();
                var raw = column.GetProperty("WORLD_SURFACE");
                int? expected = raw.ValueKind == JsonValueKind.Null
                    ? (int?)null : raw.GetInt32();
                int? actual = save.SurfaceY(x, z, "WORLD_SURFACE");
                Check(actual == expected,
                      $"after edits, surface ({x}, {z}): got "
                      + $"{actual?.ToString() ?? "null"}, "
                      + $"want {expected?.ToString() ?? "null"}");
            }

            // The thaw box counted exhaustively: the samples above say what
            // changed, this says nothing else did.
            var census = root.GetProperty("histogram_after");
            var censusBox = census.GetProperty("box");
            var counted = save.Histogram(
                (censusBox[0][0].GetInt32(), censusBox[0][1].GetInt32(),
                 censusBox[0][2].GetInt32()),
                (censusBox[1][0].GetInt32(), censusBox[1][1].GetInt32(),
                 censusBox[1][2].GetInt32()),
                keepLoaded: true);
            Check(counted.Complete == census.GetProperty("complete").GetBoolean(),
                  "thaw census: complete");
            var present = counted.Counts.ToDictionary(
                pair => pair.Key.ToString(), pair => pair.Value, StringComparer.Ordinal);
            foreach (var entry in census.GetProperty("counts").EnumerateObject())
            {
                present.TryGetValue(entry.Name, out int actual);
                Check(actual == entry.Value.GetInt32(),
                      $"thaw census, {entry.Name}: got {actual}, "
                      + $"want {entry.Value.GetInt32()}");
            }
            foreach (var pair in present)
            {
                Check(census.GetProperty("counts").TryGetProperty(pair.Key, out _),
                      $"thaw census: {pair.Key} is in the box {pair.Value} times "
                      + "and the reference never saw it there");
            }

            CheckBlockEntities(save.Dimension(DimensionNames.Overworld),
                               root.GetProperty("block_entities_after"), "after edits");

            // The strongest check: the edited chunks must encode to exactly
            // the bytes the reference produced.
            CheckChunkDigests(save.Dimension(DimensionNames.Overworld),
                              root.GetProperty("chunk_payload_sha256"), "cs");
        }
        finally
        {
            Directory.Delete(temp, recursive: true);
        }
    }

    /// <summary>Run an edit script and check what each step reports. The
    /// corpus's steps are the operations themselves, as data, so a port
    /// executes the reference's script rather than a transcription of it;
    /// world_edits.json and modern.json share this one interpreter.</summary>
    private static void RunEditScript(MinecraftSave save, JsonElement steps)
    {
        int stepIndex = 0;
        foreach (var step in steps.EnumerateArray())
        {
            string op = step.GetProperty("op").GetString()!;
            string label = $"step {stepIndex} ({op})";
            var want = step.GetProperty("result");
            switch (op)
            {
                case "fill":
                {
                    var got = save.Fill(Corner(step, "from"), Corner(step, "to"),
                                        step.GetProperty("block").GetString()!,
                                        keepLoaded: true);
                    CheckInt(label, "blocks_filled", got.BlocksFilled, want);
                    CheckInt(label, "sections_replaced", got.SectionsReplaced, want);
                    CheckInt(label, "chunks_modified", got.ChunksModified, want);
                    CheckInt(label, "blocks_out_of_range", got.BlocksOutOfRange, want);
                    Check(got.Complete == want.GetProperty("complete").GetBoolean(),
                          $"{label}: complete");
                    CheckMissingChunks(label, got.MissingChunks, want);
                    break;
                }
                case "replace":
                {
                    var mapping = step.GetProperty("mapping").EnumerateArray()
                        .Select(pair => new KeyValuePair<string, string>(
                            pair[0].GetString()!, pair[1].GetString()!))
                        .ToList();
                    var got = save.ReplaceBlocks(Corner(step, "from"), Corner(step, "to"),
                                                 mapping, keepLoaded: true);
                    CheckInt(label, "blocks_replaced", got.BlocksReplaced, want);
                    CheckInt(label, "sections_replaced", got.SectionsReplaced, want);
                    CheckInt(label, "chunks_modified", got.ChunksModified, want);
                    CheckInt(label, "block_entities_removed", got.BlockEntitiesRemoved, want);
                    CheckInt(label, "blocks_out_of_range", got.BlocksOutOfRange, want);
                    Check(got.Complete == want.GetProperty("complete").GetBoolean(),
                          $"{label}: complete");
                    CheckMissingChunks(label, got.MissingChunks, want);
                    CheckChunkList(label, "unfinished_chunks", got.UnfinishedChunks, want);
                    CheckChunkList(label, "unreadable_chunks",
                                   got.UnreadableChunks.Select(bad => bad.Chunk), want);
                    break;
                }
                case "unfreeze":
                {
                    bool deepSnow = step.TryGetProperty("deep_snow", out var deep)
                                    && deep.GetBoolean();
                    var got = save.Unfreeze(Corner(step, "from"), Corner(step, "to"),
                                            deepSnow: deepSnow, keepLoaded: true);
                    CheckInt(label, "snow_removed", got.SnowRemoved, want);
                    CheckInt(label, "ice_melted", got.IceMelted, want);
                    CheckInt(label, "snowy_cleared", got.SnowyCleared, want);
                    CheckInt(label, "deep_snow_kept", got.DeepSnowKept, want);
                    CheckInt(label, "chunks_modified", got.ChunksModified, want);
                    CheckInt(label, "floating_water", got.FloatingWater, want);
                    Check(got.Complete == want.GetProperty("complete").GetBoolean(),
                          $"{label}: complete");
                    CheckMissingChunks(label, got.MissingChunks, want);
                    break;
                }
                case "fill_biome":
                {
                    var got = save.FillBiome(Corner(step, "from"), Corner(step, "to"),
                                             step.GetProperty("biome").GetString()!,
                                             keepLoaded: true);
                    CheckInt(label, "cells_filled", got.CellsFilled, want);
                    CheckInt(label, "sections_replaced", got.SectionsReplaced, want);
                    CheckInt(label, "chunks_modified", got.ChunksModified, want);
                    CheckInt(label, "cells_out_of_range", got.CellsOutOfRange, want);
                    Check(got.Complete == want.GetProperty("complete").GetBoolean(),
                          $"{label}: complete");
                    var box = want.GetProperty("written_box");
                    var (min, max) = (got.WrittenBox!.Value.Min, got.WrittenBox.Value.Max);
                    Check(min.X == box[0][0].GetInt32() && min.Y == box[0][1].GetInt32()
                          && min.Z == box[0][2].GetInt32()
                          && max.X == box[1][0].GetInt32() && max.Y == box[1][1].GetInt32()
                          && max.Z == box[1][2].GetInt32(),
                          $"{label}: written_box, got ({min.X},{min.Y},{min.Z})"
                          + $"-({max.X},{max.Y},{max.Z})");
                    break;
                }
                case "place":
                {
                    var at = step.GetProperty("at");
                    if (want.TryGetProperty("refused", out var refused)
                        && refused.GetBoolean())
                    {
                        // Water the game does not have under a block that
                        // would take it: refused, and the read-back shows
                        // nothing was written.
                        bool threw = false;
                        try
                        {
                            save.PlaceBlock(
                                at[0].GetInt32(), at[1].GetInt32(), at[2].GetInt32(),
                                step.GetProperty("block").GetString()!);
                        }
                        catch (NbtException)
                        {
                            threw = true;
                        }
                        Check(threw, $"{label}: must refuse");
                        break;
                    }
                    bool got = save.PlaceBlock(
                        at[0].GetInt32(), at[1].GetInt32(), at[2].GetInt32(),
                        step.GetProperty("block").GetString()!);
                    Check(got == want.GetProperty("placed").GetBoolean(),
                          $"{label}: placed");
                    break;
                }
                case "connect":
                {
                    var got = save.ConnectBlocks(Corner(step, "from"),
                                                 Corner(step, "to"),
                                                 keepLoaded: true);
                    CheckInt(label, "blocks_scanned", got.BlocksScanned, want);
                    CheckInt(label, "fences_updated", got.FencesUpdated, want);
                    CheckInt(label, "walls_updated", got.WallsUpdated, want);
                    CheckInt(label, "panes_updated", got.PanesUpdated, want);
                    CheckInt(label, "gates_updated", got.GatesUpdated, want);
                    CheckInt(label, "stairs_updated", got.StairsUpdated, want);
                    CheckInt(label, "total_updated", got.TotalUpdated, want);
                    Check(got.Complete == want.GetProperty("complete").GetBoolean(),
                          $"{label}: complete");
                    break;
                }
                case "rebuild_heightmaps":
                {
                    var got = save.RebuildHeightmaps(Corner(step, "from"),
                                                     Corner(step, "to"),
                                                     keepLoaded: true);
                    CheckInt(label, "chunks_rebuilt", got.ChunksRebuilt, want);
                    Check(got.Complete == want.GetProperty("complete").GetBoolean(),
                          $"{label}: complete");
                    break;
                }
                case "reset_lighting":
                {
                    var got = save.ResetLighting(
                        Corner(step, "from"), Corner(step, "to"),
                        neighbors: step.GetProperty("neighbors").GetBoolean());
                    CheckInt(label, "chunks_reset", got.ChunksReset, want);
                    CheckInt(label, "arrays_removed", got.ArraysRemoved, want);
                    CheckInt(label, "chunks_skipped", got.ChunksSkipped, want);
                    break;
                }
                case "light_section":
                {
                    // Not an edit: the case the fixture lacks, a section of
                    // light alone past the top of the world. Appended at the
                    // end, where the game keeps the top one, and released so
                    // the next accessor is read with it.
                    int chunkX = step.GetProperty("chunk")[0].GetInt32();
                    int chunkZ = step.GetProperty("chunk")[1].GetInt32();
                    var dim = save.Dimension(DimensionNames.Overworld);
                    var sections = (TagList)dim.Chunk(chunkX, chunkZ)!.Data!["sections"];
                    var light = new sbyte[2048];
                    Array.Fill(light, (sbyte)-1);
                    sections.Add(new TagCompound
                    {
                        ["Y"] = new TagByte((sbyte)step.GetProperty("y").GetInt32()),
                        ["SkyLight"] = new TagByteArray(light),
                    });
                    dim.Release(chunkX, chunkZ);
                    var accessor = dim.Blocks(chunkX, chunkZ)!;
                    CheckInt(label, "sections", accessor.Sections.Count, want);
                    CheckInt(label, "min_y", accessor.MinY, want);
                    CheckInt(label, "max_y", accessor.MaxY, want);
                    break;
                }
                case "drop_section":
                {
                    // Not an edit either: a chunk without the section at its
                    // floor still counts from the yPos it records, not from
                    // the lowest section it stores.
                    int chunkX = step.GetProperty("chunk")[0].GetInt32();
                    int chunkZ = step.GetProperty("chunk")[1].GetInt32();
                    int sectionY = step.GetProperty("y").GetInt32();
                    var dim = save.Dimension(DimensionNames.Overworld);
                    var sections = (TagList)dim.Chunk(chunkX, chunkZ)!.Data!["sections"];
                    var dropped = sections.Items.FindAll(
                        tag => Tag.AsInt(((TagCompound)tag).Get("Y")!) == sectionY);
                    Check(dropped.Count == 1, $"{label}: one section Y={sectionY}");
                    sections.Items.Remove(dropped[0]);
                    dim.Release(chunkX, chunkZ);
                    var accessor = dim.Blocks(chunkX, chunkZ)!;
                    CheckInt(label, "sections", accessor.Sections.Count, want);
                    CheckInt(label, "min_y", accessor.MinY, want);
                    CheckInt(label, "max_y", accessor.MaxY, want);
                    break;
                }
                default:
                    Check(false, $"{label}: unknown op");
                    break;
            }
            stepIndex++;
        }
    }

    /// <summary>Point reads through the world accessor against
    /// [x, y, z, state-or-null] samples; context prefixes each report.</summary>
    private static void CheckBlockReads(MinecraftSave save, JsonElement samples,
                                        string context)
    {
        foreach (var sample in samples.EnumerateArray())
        {
            int x = sample[0].GetInt32(), y = sample[1].GetInt32(),
                z = sample[2].GetInt32();
            string? expected = sample[3].ValueKind == JsonValueKind.Null
                ? null : sample[3].GetString();
            string? actual = save.GetBlock(x, y, z)?.ToString();
            Check(actual == expected,
                  $"{context}block ({x}, {y}, {z}): got {actual ?? "null"}, "
                  + $"want {expected ?? "null"}");
        }
    }

    /// <summary>Every heightmap of each listed column, through the save's
    /// global lookup. world.json and modern.json share it; modern.json's
    /// chunk (-13, 19) stores light alone below the world, where a port
    /// that counts from the lowest stored section reads 16 low.</summary>
    private static void CheckSurfaces(MinecraftSave save, JsonElement columns,
                                      string context)
    {
        foreach (var column in columns.EnumerateArray())
        {
            int x = column.GetProperty("x").GetInt32();
            int z = column.GetProperty("z").GetInt32();
            foreach (string map in ChunkBlocks.HeightmapNames)
            {
                var raw = column.GetProperty(map);
                int? expected = raw.ValueKind == JsonValueKind.Null
                    ? (int?)null : raw.GetInt32();
                int? actual = save.SurfaceY(x, z, map);
                Check(actual == expected,
                      $"{context}surface ({x}, {z}) {map}: got "
                      + $"{actual?.ToString() ?? "null"}, "
                      + $"want {expected?.ToString() ?? "null"}");
            }
        }
    }

    /// <summary>Each listed chunk's uncompressed NBT must encode to exactly
    /// the bytes the reference produced — the write path's equivalent of
    /// the untouched round trip. With HV_DUMP naming a directory, a
    /// mismatching payload is written there as prefix.x.z.nbt for a
    /// byte-level diff against the reference's.</summary>
    private static void CheckChunkDigests(Dimension dim, JsonElement entries, string prefix)
    {
        foreach (var entry in entries.EnumerateArray())
        {
            int chunkX = entry.GetProperty("chunk")[0].GetInt32();
            int chunkZ = entry.GetProperty("chunk")[1].GetInt32();
            var chunk = dim.Chunk(chunkX, chunkZ)!;
            byte[] payload = Codec.Encode(chunk.Data!, chunk.DataName);
            string digest = Sha256Hex(payload);
            bool same = digest == entry.GetProperty("payload_sha256").GetString();
            Check(same, $"chunk ({chunkX}, {chunkZ}) NBT after edits is not "
                        + "byte-identical to the reference");
            if (!same && Environment.GetEnvironmentVariable("HV_DUMP") is string dir)
                File.WriteAllBytes(
                    Path.Combine(dir, $"{prefix}.{chunkX}.{chunkZ}.nbt"), payload);
        }
    }

    private static (int X, int Y, int Z) Corner(JsonElement step, string key)
    {
        var value = step.GetProperty(key);
        return (value[0].GetInt32(), value[1].GetInt32(), value[2].GetInt32());
    }

    private static void CheckInt(string label, string field, int actual,
                                 JsonElement want)
    {
        int expected = want.GetProperty(field).GetInt32();
        Check(actual == expected,
              $"{label}: {field}, got {actual}, want {expected}");
    }

    /// <summary>Chunks the box reached that the world has never generated —
    /// sorted before comparing, since the corpus records a set and only the
    /// membership is contractual.</summary>
    private static void CheckMissingChunks(
        string label, IEnumerable<(int X, int Z)> actual, JsonElement want)
    {
        var missing = actual.OrderBy(chunk => chunk).ToList();
        var wantMissing = want.GetProperty("missing_chunks").EnumerateArray()
            .Select(entry => (entry[0].GetInt32(), entry[1].GetInt32()))
            .OrderBy(chunk => chunk).ToList();
        Check(missing.SequenceEqual(wantMissing),
              $"{label}: missing_chunks, got [{string.Join(" ", missing)}], "
              + $"want [{string.Join(" ", wantMissing)}]");
    }

    private static void CheckChunkList(
        string label, string field, IEnumerable<(int X, int Z)> actual, JsonElement want)
    {
        var got = actual.OrderBy(chunk => chunk).ToList();
        var wanted = want.GetProperty(field).EnumerateArray()
            .Select(entry => (entry[0].GetInt32(), entry[1].GetInt32()))
            .OrderBy(chunk => chunk).ToList();
        Check(got.SequenceEqual(wanted),
              $"{label}: {field}, got [{string.Join(" ", got)}], "
              + $"want [{string.Join(" ", wanted)}]");
    }

    /// <summary>The block entities a chunk stores, in its order, against
    /// the reference's: what a replace kept and what it took away, said
    /// outright rather than only through the chunk's digest.</summary>
    private static void CheckBlockEntities(Dimension dim, JsonElement report, string label)
    {
        int chunkX = report.GetProperty("chunk")[0].GetInt32();
        int chunkZ = report.GetProperty("chunk")[1].GetInt32();
        var data = dim.Chunk(chunkX, chunkZ)?.Data;
        var got = new List<string>();
        if (data?.Get("block_entities") is TagList stored)
            foreach (var item in stored)
            {
                var entry = (TagCompound)item;
                got.Add($"{Tag.AsInt(entry["x"])},{Tag.AsInt(entry["y"])},"
                        + $"{Tag.AsInt(entry["z"])},{Tag.AsString(entry["id"])}");
            }
        var wanted = report.GetProperty("entities").EnumerateArray()
            .Select(entry => $"{entry[0].GetInt32()},{entry[1].GetInt32()},"
                             + $"{entry[2].GetInt32()},{entry[3].GetString()}")
            .ToList();
        Check(got.SequenceEqual(wanted),
              $"{label}, chunk ({chunkX}, {chunkZ}) block entities: got "
              + $"[{string.Join(" ", got)}], want [{string.Join(" ", wanted)}]");
    }

    // -- the 26.3 palette codec --------------------------------------------

    /// <summary>The default-state table, entry by entry against the
    /// reference's dump of its own, so the port's generated copy is checked
    /// rather than trusted to have come from the same report.</summary>
    private static void VerifyBlockDefaults(string corpus)
    {
        using var doc = Load(corpus, "block_defaults.json");
        var root = doc.RootElement;

        string game = root.GetProperty("game").GetString()!;
        Check(BlockDefaults.GameVersion == game,
              $"game version: got {BlockDefaults.GameVersion}, want {game}");
        int dataVersion = root.GetProperty("data_version").GetInt32();
        Check(BlockDefaults.DataVersion == dataVersion,
              $"table DataVersion: got {BlockDefaults.DataVersion}, want {dataVersion}");
        int threshold = root.GetProperty("modern_state_data_version").GetInt32();
        Check(ChunkBlocks.ModernStateDataVersion == threshold,
              $"modern_state_data_version: got {ChunkBlocks.ModernStateDataVersion}, "
              + $"want {threshold}");

        int listed = 0;
        foreach (var entry in root.GetProperty("defaults").EnumerateObject())
        {
            listed++;
            string want = entry.Value.GetString()!;
            string got = string.Join(",", BlockDefaults.DefaultProperties(entry.Name)
                .Select(pair => $"{pair.Key}={pair.Value}"));
            Check(got == want, $"defaults of {entry.Name}: got [{got}], want [{want}]");
        }
        // The table's own size, so a stale extra entry cannot hide behind
        // every listed one matching. The generated class exposes only the
        // lookup, so the count comes off its private table by reflection;
        // a regeneration that renames the field fails here, visibly.
        int? tableSize = GeneratedTableSize();
        Check(tableSize == listed,
              $"table size: got {tableSize?.ToString() ?? "unknown"}, want {listed}");

        // A block with no properties is not listed, and neither is one the
        // game has never heard of: both read as "no defaults".
        foreach (string name in new[] { "example:widget", "minecraft:stone", "" })
        {
            var unlisted = BlockDefaults.DefaultProperties(name);
            Check(unlisted.Length == 0,
                  $"unlisted '{name}' has {unlisted.Length} defaults, want none");
        }

        // A fresh array each call: a caller that sorts or overwrites its
        // copy must not be editing the table.
        var first = BlockDefaults.DefaultProperties("minecraft:oak_stairs");
        Check(first.Length > 0, "oak_stairs has defaults");
        if (first.Length > 0)
        {
            first[0] = new KeyValuePair<string, string>("tampered", "yes");
            var second = BlockDefaults.DefaultProperties("minecraft:oak_stairs");
            Check(second[0].Key != "tampered",
                  "DefaultProperties must hand out a fresh array, not the table's");
        }
    }

    private static int? GeneratedTableSize()
    {
        var field = typeof(BlockDefaults).GetField(
            "Table", BindingFlags.NonPublic | BindingFlags.Static);
        return field?.GetValue(null) is System.Collections.ICollection table
            ? table.Count : (int?)null;
    }

    /// <summary>The wall-cover table, block by block and state by state
    /// against the reference's dump of its own, so the port's generated
    /// copy is checked rather than trusted to have come from the same run
    /// of the game.</summary>
    private static void VerifyHeightmaps(string corpus)
    {
        using var doc = Load(corpus, "heightmaps.json");
        var root = doc.RootElement;

        string game = root.GetProperty("game").GetString()!;
        Check(HeightmapBlocks.GameVersion == game,
              $"game version: got {HeightmapBlocks.GameVersion}, want {game}");
        int dataVersion = root.GetProperty("data_version").GetInt32();
        Check(HeightmapBlocks.DataVersion == dataVersion,
              $"table DataVersion: got {HeightmapBlocks.DataVersion}, want {dataVersion}");
        foreach (var bit in root.GetProperty("bits").EnumerateObject())
        {
            int at = Array.IndexOf(ChunkBlocks.HeightmapNames, bit.Name);
            Check(at >= 0 && 1 << at == bit.Value.GetInt32(),
                  $"bit of {bit.Name}: {at}");
        }
        foreach (var (field, held) in new[]
                 {
                     ("not_in_heightmap", HeightmapBlocks.NotInHeightmap),
                     ("leaves", HeightmapBlocks.Leaves),
                     ("fluids", HeightmapBlocks.Fluids),
                 })
        {
            var want = root.GetProperty(field).EnumerateArray()
                .Select(name => name.GetString()!).ToHashSet();
            Check(want.SetEquals(held), $"{field}: {held.Count} entries, want {want.Count}");
        }
        foreach (var entry in root.GetProperty("masks").EnumerateObject())
        {
            int got = ChunkBlocks.HeightmapMask(BlockState.Parse(entry.Name));
            Check(got == entry.Value.GetInt32(),
                  $"mask of {entry.Name}: got {got}, want {entry.Value.GetInt32()}");
        }

        // What the game wrote: every finished chunk derives its own stored
        // maps and rebuilds them byte for byte.
        foreach (var region in root.GetProperty("regions").EnumerateArray())
        {
            string fixture = region.GetProperty("fixture").GetString()!;
            string temp = AssembleWorldFrom(corpus, fixture);
            try
            {
                var save = MinecraftSave.Load(temp, new[] { DimensionNames.Overworld });
                var dim = save.Dimension(DimensionNames.Overworld);
                foreach (var entry in region.GetProperty("finished").EnumerateArray())
                {
                    int chunkX = entry[0].GetInt32(), chunkZ = entry[1].GetInt32();
                    string label = $"{fixture} chunk ({chunkX}, {chunkZ})";
                    var chunk = dim.Chunk(chunkX, chunkZ);
                    var accessor = dim.Blocks(chunkX, chunkZ);
                    if (chunk == null || accessor == null)
                    {
                        Check(false, $"{label}: not in the fixture");
                        continue;
                    }
                    Check(accessor.Finished, $"{label}: not finished");
                    var stale = accessor.StaleHeightmaps();
                    Check(stale.Count == 0,
                          $"{label}: stale {string.Join(", ", stale.Keys)}");
                    var before = (TagCompound)chunk.Data!["Heightmaps"];
                    var names = before.Keys.ToList();
                    var values = names.Select(
                        name => ((TagLongArray)before[name]).Values).ToList();
                    accessor.RebuildHeightmaps();
                    var after = (TagCompound)chunk.Data!["Heightmaps"];
                    Check(after.Keys.SequenceEqual(names), $"{label}: map order");
                    for (int i = 0; i < names.Count; i++)
                        Check(((TagLongArray)after[names[i]]).Values.SequenceEqual(values[i]),
                              $"{label}: {names[i]} changed");
                }
            }
            finally { Directory.Delete(temp, recursive: true); }
        }

        string scene = AssembleWorld(corpus);
        try
        {
            var save = MinecraftSave.Load(scene, new[] { DimensionNames.Overworld });
            var dim = save.Dimension(DimensionNames.Overworld);
            var paste = new Schematic(2, 2, 2, ChunkBlocks.MinDataVersion);
            for (int x = 0; x < 2; x++)
                for (int y = 0; y < 2; y++)
                    for (int z = 0; z < 2; z++)
                        paste.SetBlock(x, y, z, "minecraft:stone");

            static (int, int, int) Xyz(JsonElement at) =>
                (at[0].GetInt32(), at[1].GetInt32(), at[2].GetInt32());
            static void CheckChunks(string label, string field, JsonElement want,
                                    List<(int X, int Z)> got)
            {
                var listed = want.GetProperty(field).EnumerateArray()
                    .Select(c => (c[0].GetInt32(), c[1].GetInt32())).ToList();
                Check(got.OrderBy(c => c).SequenceEqual(listed), $"{label}: {field}");
            }

            int index = 0;
            foreach (var step in root.GetProperty("scene").EnumerateArray())
            {
                string op = step.GetProperty("op").GetString()!;
                string label = $"step {index++} ({op})";
                var want = step.GetProperty("result");
                switch (op)
                {
                    case "set":
                    {
                        var (x, y, z) = Xyz(step.GetProperty("at"));
                        save.SetBlock(x, y, z, step.GetProperty("block").GetString()!);
                        break;
                    }
                    case "stale":
                    {
                        var at = step.GetProperty("chunk");
                        var got = dim.Blocks(at[0].GetInt32(), at[1].GetInt32())!
                            .StaleHeightmaps();
                        Check(got.Count == want.EnumerateObject().Count(),
                              $"{label}: {got.Count} maps stale");
                        foreach (var entry in want.EnumerateObject())
                            Check(got.TryGetValue(entry.Name, out int columns)
                                  && columns == entry.Value.GetInt32(),
                                  $"{label}: {entry.Name}");
                        break;
                    }
                    case "rebuild":
                    {
                        var got = save.RebuildHeightmaps(Xyz(step.GetProperty("from")),
                                                         Xyz(step.GetProperty("to")));
                        CheckInt(label, "chunks_rebuilt", got.ChunksRebuilt, want);
                        CheckChunks(label, "missing", want, got.MissingChunks);
                        CheckChunks(label, "unfinished", want, got.UnfinishedChunks);
                        Check(got.Complete == want.GetProperty("complete").GetBoolean(),
                              $"{label}: complete");
                        break;
                    }
                    case "tops":
                    {
                        var column = step.GetProperty("column");
                        foreach (var entry in want.EnumerateObject())
                        {
                            int? got = save.SurfaceY(column[0].GetInt32(),
                                                     column[1].GetInt32(), entry.Name);
                            Check(got == entry.Value.GetInt32(),
                                  $"{label}: {entry.Name} got {got}");
                        }
                        break;
                    }
                    case "import":
                    {
                        var got = save.ImportSchem(
                            paste, Xyz(step.GetProperty("at")), anchor: "min",
                            heightmaps: step.GetProperty("heightmaps").GetBoolean());
                        CheckInt(label, "blocks_pasted", got.BlocksPasted, want);
                        CheckInt(label, "blocks_out_of_range", got.BlocksOutOfRange, want);
                        CheckInt(label, "chunks_modified", got.ChunksModified, want);
                        CheckInt(label, "heightmaps_rebuilt", got.HeightmapsRebuilt, want);
                        CheckChunks(label, "missing", want, got.MissingChunks);
                        CheckChunks(label, "unfinished", want, got.UnfinishedChunks);
                        Check(got.Complete == want.GetProperty("complete").GetBoolean(),
                              $"{label}: complete");
                        break;
                    }
                    default:
                        Check(false, $"{label}: unknown op");
                        break;
                }
            }
        }
        finally { Directory.Delete(scene, recursive: true); }
    }

    private static void VerifyWallCover(string corpus)
    {
        using var doc = Load(corpus, "wall_cover.json");
        var root = doc.RootElement;

        string game = root.GetProperty("game").GetString()!;
        Check(WallCover.GameVersion == game,
              $"game version: got {WallCover.GameVersion}, want {game}");
        int dataVersion = root.GetProperty("data_version").GetInt32();
        Check(WallCover.DataVersion == dataVersion,
              $"table DataVersion: got {WallCover.DataVersion}, want {dataVersion}");
        var bits = root.GetProperty("bits");
        Check(WallCover.North == bits.GetProperty("north").GetInt32()
              && WallCover.East == bits.GetProperty("east").GetInt32()
              && WallCover.South == bits.GetProperty("south").GetInt32()
              && WallCover.West == bits.GetProperty("west").GetInt32()
              && WallCover.Post == bits.GetProperty("post").GetInt32()
              && WallCover.Override == bits.GetProperty("override").GetInt32(),
              "the code's bits differ from the reference's");

        int listed = 0;
        foreach (var entry in root.GetProperty("uniform").EnumerateObject())
        {
            listed++;
            int want = entry.Value.GetInt32();
            int? got = WallCover.Of(new BlockState(entry.Name));
            Check(got == want, $"cover of {entry.Name}: got {got}, want {want}");
        }
        var tables = root.GetProperty("tables").EnumerateArray().ToArray();
        foreach (var entry in root.GetProperty("keyed").EnumerateObject())
        {
            listed++;
            var table = tables[entry.Value.GetInt32()];
            string[] keys = table.GetProperty("keys").EnumerateArray()
                .Select(key => key.GetString()!).ToArray();
            foreach (var code in table.GetProperty("codes").EnumerateObject())
            {
                string[] values = code.Name.Split(',');
                var state = new BlockState(entry.Name, keys.Select(
                    (key, i) => new KeyValuePair<string, string>(key, values[i])));
                int? got = WallCover.Of(state);
                Check(got == code.Value.GetInt32(),
                      $"cover of {state}: got {got}, want {code.Value.GetInt32()}");
            }
        }
        // The table's own size, so a stale extra entry cannot hide behind
        // every listed one matching (by reflection, as for BlockDefaults).
        int size = 0;
        foreach (string field in new[] { "Codes", "Tables" })
        {
            var found = typeof(WallCover).GetField(
                field, BindingFlags.NonPublic | BindingFlags.Static);
            size += found?.GetValue(null) is System.Collections.ICollection held
                ? held.Count : -1_000_000;
        }
        Check(size == listed, $"table size: got {size}, want {listed}");

        // A block the game does not have reads as null, and a property a
        // state leaves out as its block's default: a bare top slab is no
        // top slab, it is the default bottom one.
        Check(WallCover.Of(new BlockState("example:widget")) == null,
              "an unknown block has no cover");
        Check(WallCover.Of(new BlockState("minecraft:stone_slab"))
              == WallCover.Of(BlockState.Parse("minecraft:stone_slab[type=bottom]")),
              "a property left out reads as its default");
    }

    /// <summary>modern.json: four chunks a 26.3 game wrote. What each stored
    /// palette entry looks like and what it decodes to; point reads and
    /// surface maps; then an edit script chosen for the codec's
    /// transitions, the palette forms it leaves behind, and the whole-chunk
    /// digests — where a port that wraps a bare id differently, orders id
    /// and properties differently, or fills defaults differently is caught
    /// by the bytes rather than by a sample.</summary>
    private static void VerifyModern(string corpus)
    {
        using var doc = Load(corpus, "modern.json");
        var root = doc.RootElement;
        int threshold = root.GetProperty("modern_state_data_version").GetInt32();
        Check(ChunkBlocks.ModernStateDataVersion == threshold,
              $"modern_state_data_version: got {ChunkBlocks.ModernStateDataVersion}, "
              + $"want {threshold}");

        string temp = AssembleWorldFrom(corpus, "modern");
        try
        {
            var save = MinecraftSave.Load(temp, new[] { DimensionNames.Overworld });
            var dim = save.Dimension(DimensionNames.Overworld);

            // 1. What the game wrote, section by section.
            foreach (var chunkNode in root.GetProperty("chunks").EnumerateArray())
            {
                int chunkX = chunkNode.GetProperty("chunk")[0].GetInt32();
                int chunkZ = chunkNode.GetProperty("chunk")[1].GetInt32();
                string label = $"chunk ({chunkX}, {chunkZ})";
                var chunk = dim.Chunk(chunkX, chunkZ);
                var accessor = dim.Blocks(chunkX, chunkZ);
                if (chunk == null || accessor == null)
                {
                    Check(false, $"{label}: not in the fixture");
                    continue;
                }
                int dataVersion = chunkNode.GetProperty("data_version").GetInt32();
                Check(accessor.DataVersion == dataVersion,
                      $"{label}: DataVersion, got {accessor.DataVersion}, want {dataVersion}");
                bool modern = chunkNode.GetProperty("modern_states").GetBoolean();
                Check(accessor.ModernStates == modern,
                      $"{label}: modern_states, got {accessor.ModernStates}, want {modern}");
                var sections = chunkNode.GetProperty("sections");
                Check(accessor.Sections.Count == sections.GetArrayLength(),
                      $"{label}: {accessor.Sections.Count} sections, "
                      + $"want {sections.GetArrayLength()}");
                foreach (var section in sections.EnumerateArray())
                    CheckPaletteReport(label, chunk, accessor,
                                       section.GetProperty("y").GetInt32(), section);
            }

            // 2. Point reads and every surface map, before any edit.
            CheckBlockReads(save, root.GetProperty("reads"), "");
            CheckSurfaces(save, root.GetProperty("surfaces"), "");

            // 3. The edit script, then what it left behind: the states, the
            // palette forms, and the bytes.
            RunEditScript(save, root.GetProperty("steps"));
            CheckBlockReads(save, root.GetProperty("blocks_after"), "after edits, ");
            foreach (var report in root.GetProperty("palettes_after").EnumerateArray())
            {
                int chunkX = report.GetProperty("chunk")[0].GetInt32();
                int chunkZ = report.GetProperty("chunk")[1].GetInt32();
                string label = $"after edits, chunk ({chunkX}, {chunkZ})";
                var chunk = dim.Chunk(chunkX, chunkZ);
                var accessor = dim.Blocks(chunkX, chunkZ);
                if (chunk == null || accessor == null)
                {
                    Check(false, $"{label}: not in the fixture");
                    continue;
                }
                CheckPaletteReport(label, chunk, accessor,
                                   report.GetProperty("y").GetInt32(), report);
            }
            CheckBlockEntities(dim, root.GetProperty("block_entities_after"), "after edits");
            CheckChunkDigests(dim, root.GetProperty("chunk_payload_sha256"), "cs.modern");
        }
        finally
        {
            Directory.Delete(temp, recursive: true);
        }
    }

    private static string TextSha256(IEnumerable<string> lines) =>
        Sha256Hex(System.Text.Encoding.UTF8.GetBytes(string.Join("\n", lines)));

    private static string BitsOf(double value)
    {
        var bytes = new byte[8];
        BinaryPrimitives.WriteInt64BigEndian(bytes, BitConverter.DoubleToInt64Bits(value));
        return Convert.ToHexString(bytes).ToLowerInvariant();
    }

    private static bool SameDouble(JsonElement want, double got) =>
        want.GetProperty("bits").GetString() == BitsOf(got);

    /// <summary>older_chunks.json: chunks Minecraft 1.13 to 1.16 saved --
    /// OldWorld's 1.15.2 overworld and 1.16.5 End chunks, and chunks made
    /// from one and stamped as 1.13 and 1.14 saved them -- read: the release
    /// each names, its stage and whether that is finished, each section's
    /// palette and the bits a cell it is read at, and digests of every
    /// block, every biome cell, every heightmap, and the entities a chunk
    /// before 2681 keeps in its Level.</summary>
    private static void VerifyOlderChunks(string corpus)
    {
        using var doc = Load(corpus, "older_chunks.json");
        string legacy = Path.GetFullPath(Path.Combine(corpus, "..", "legacy"));
        var regions = new Dictionary<string, NbtFile>(StringComparer.Ordinal);
        var (_, older) = Codec.Decode(Compression.GzipDecompress(
            File.ReadAllBytes(Path.Combine(legacy, "older.nbt"))));
        var cases = (TagList)older["cases"];
        foreach (var node in doc.RootElement.GetProperty("chunks").EnumerateArray())
        {
            string label = node.GetProperty("name").GetString()!;
            var input = node.GetProperty("input");
            TagCompound data;
            if (input.TryGetProperty("region", out var source))
            {
                string relative = source.GetString()!;
                if (!regions.TryGetValue(relative, out var region))
                    regions[relative] = region = NbtFile.Load(
                        Path.Combine(legacy, relative.Replace('/', Path.DirectorySeparatorChar)));
                var key = input.GetProperty("key");
                data = region.Chunk(key[0].GetInt32(), key[1].GetInt32())!.Data!;
            }
            else
            {
                data = (TagCompound)((TagCompound)cases[input.GetProperty("older_nbt_case")
                    .GetInt32()])["chunk"];
            }
            var accessor = new ChunkBlocks(data);
            Check(accessor.Legacy, $"{label}: not read as an old chunk");
            Check(accessor.DataVersion == node.GetProperty("data_version").GetInt32(),
                  $"{label}: DataVersion {accessor.DataVersion}");
            Check(ChunkBlocks.LegacyRelease(accessor.DataVersion)
                  == node.GetProperty("release").GetString(),
                  $"{label}: release {ChunkBlocks.LegacyRelease(accessor.DataVersion)}");
            Check(accessor.Status == node.GetProperty("status").GetString(),
                  $"{label}: status {accessor.Status}");
            Check(accessor.Finished == node.GetProperty("finished").GetBoolean(),
                  $"{label}: finished {accessor.Finished}");
            Check((accessor.WorldX >> 4) == node.GetProperty("chunk")[0].GetInt32()
                  && (accessor.WorldZ >> 4) == node.GetProperty("chunk")[1].GetInt32(),
                  $"{label}: stands at ({accessor.WorldX >> 4}, {accessor.WorldZ >> 4})");
            foreach (var want in node.GetProperty("sections").EnumerateArray())
            {
                int y = want.GetProperty("y").GetInt32();
                var blocks = accessor.Sections[y].Blocks!;
                Check(blocks.Palette.Select(s => s.ToString()).SequenceEqual(
                          want.GetProperty("palette").EnumerateArray().Select(e => e.GetString()!)),
                      $"{label} section {y}: palette");
                Check(blocks.Bits == want.GetProperty("bits").GetInt32(),
                      $"{label} section {y}: {blocks.Bits} bits a cell");
            }
            var cells = new List<string>();
            for (int y = 0; y < 256; y++)
                for (int z = 0; z < 16; z++)
                    for (int x = 0; x < 16; x++)
                        cells.Add(accessor.Get(x, y, z).ToString());
            Check(TextSha256(cells) == node.GetProperty("blocks_sha256").GetString(),
                  $"{label}: every block, digested, disagrees");
            var biomes = new List<string>();
            for (int y = 0; y < 256; y += 4)
                for (int z = 0; z < 16; z += 4)
                    for (int x = 0; x < 16; x += 4)
                        biomes.Add(accessor.GetBiome(x, y, z));
            Check(TextSha256(biomes) == node.GetProperty("biomes_sha256").GetString(),
                  $"{label}: every biome cell, digested, disagrees");
            var surfaces = node.GetProperty("surfaces_sha256");
            foreach (string name in ChunkBlocks.HeightmapNames)
            {
                var column = new List<string>();
                for (int z = 0; z < 16; z++)
                    for (int x = 0; x < 16; x++)
                        column.Add(accessor.SurfaceY(x, z, name)?.ToString() ?? "None");
                bool any = column.Any(v => v != "None");
                if (surfaces.TryGetProperty(name, out var digest))
                    Check(TextSha256(column) == digest.GetString(), $"{label}: {name} disagrees");
                else
                    Check(!any, $"{label}: {name} reads a surface the corpus has none of");
            }
            var wantEntities = node.GetProperty("legacy_entities");
            var entities = accessor.LegacyEntities as TagList;
            if (wantEntities.ValueKind == JsonValueKind.Null)
            {
                Check(accessor.LegacyEntities == null, $"{label}: entities in its Level");
            }
            else
            {
                Check(entities != null && entities.Count == wantEntities.GetArrayLength(),
                      $"{label}: {entities?.Count} entities in its Level");
                int index = 0;
                foreach (var want in wantEntities.EnumerateArray())
                {
                    if (entities == null || index >= entities.Count)
                        break;
                    var entity = (TagCompound)entities[index++];
                    var pos = (TagList)entity["Pos"];
                    Check(Tag.Text(entity.Get("id") ?? new TagString(""))
                          == want.GetProperty("id").GetString()
                          && Enumerable.Range(0, 3).All(
                              a => SameDouble(want.GetProperty("pos")[a], Tag.AsDouble(pos[a]))),
                          $"{label}: entity {index - 1}");
                }
            }
        }
    }

    /// <summary>old_layout.json: saves in the layout before Minecraft 26.1,
    /// assembled from ../old_layout.json and read where they stand: their
    /// dimensions and regions, settings and player as the game's own move
    /// of them would answer, and every write refused.</summary>
    private static void VerifyOldLayout(string corpus)
    {
        using var doc = Load(corpus, "old_layout.json");
        string fixtures = Path.GetFullPath(Path.Combine(corpus, ".."));
        using var recorded = JsonDocument.Parse(File.ReadAllText(
            Path.Combine(fixtures, "old_layout.json")));
        var cases = recorded.RootElement.GetProperty("cases");
        foreach (var world in doc.RootElement.GetProperty("worlds").EnumerateArray())
        {
            string label = world.GetProperty("name").GetString()!;
            var before = cases[world.GetProperty("case").GetInt32()].GetProperty("before");
            string temp = Path.Combine(
                Path.GetTempPath(), "hv-conformance-old-" + Guid.NewGuid().ToString("N"));
            try
            {
                foreach (var dir in before.GetProperty("dirs").EnumerateArray())
                    Directory.CreateDirectory(Path.Combine(
                        temp, dir.GetString()!.Replace('/', Path.DirectorySeparatorChar)));
                foreach (var file in before.GetProperty("files").EnumerateObject())
                {
                    string path = Path.Combine(
                        temp, file.Name.Replace('/', Path.DirectorySeparatorChar));
                    Directory.CreateDirectory(Path.GetDirectoryName(path)!);
                    if (file.Value.ValueKind == JsonValueKind.Object)
                        File.Copy(Path.Combine(fixtures, file.Value.GetProperty("fixture")
                            .GetString()!.Replace('/', Path.DirectorySeparatorChar)), path);
                    else
                        File.WriteAllBytes(path, Convert.FromBase64String(file.Value.GetString()!));
                }
                var save = MinecraftSave.Load(temp);
                var reads = world.GetProperty("reads");
                Check(save.Layout == reads.GetProperty("layout").GetString(),
                      $"{label}: layout {save.Layout}");
                Check((save.ReadOnly != null) == reads.GetProperty("read_only").GetBoolean(),
                      $"{label}: read-only {save.ReadOnly != null}");
                var dims = reads.GetProperty("dimensions");
                Check(save.Dimensions.Count == dims.EnumerateObject().Count(),
                      $"{label}: {save.Dimensions.Count} dimensions");
                foreach (var dim in dims.EnumerateObject())
                {
                    var keys = save.Dimensions.TryGetValue(dim.Name, out var found)
                        ? found.Available.Keys.OrderBy(k => k).Select(k => $"{k.X},{k.Z}").ToList()
                        : null;
                    Check(keys != null && keys.SequenceEqual(dim.Value.EnumerateArray()
                              .Select(k => $"{k[0].GetInt32()},{k[1].GetInt32()}")),
                          $"{label}: regions of {dim.Name}");
                }

                void Read(string what, JsonElement want, Func<bool> agrees)
                {
                    bool refused = want.ValueKind == JsonValueKind.String
                                   && want.GetString() == "refused";
                    bool ok;
                    try
                    {
                        // a reader the corpus marks refused must refuse
                        bool same = agrees();
                        ok = !refused && same;
                    }
                    catch (NbtException)
                    {
                        ok = refused;
                    }
                    Check(ok, $"{label}: {what}");
                }

                Read("game type", reads.GetProperty("game_type"),
                     () => save.GameType() == reads.GetProperty("game_type").GetString());
                Read("cheats", reads.GetProperty("allow_commands"),
                     () => save.AllowCommands() == reads.GetProperty("allow_commands").GetBoolean());
                Read("difficulty", reads.GetProperty("difficulty"),
                     () => save.Difficulty() == reads.GetProperty("difficulty").GetString());
                Read("hardcore", reads.GetProperty("hardcore"),
                     () => save.Hardcore() == reads.GetProperty("hardcore").GetBoolean());
                Read("difficulty lock", reads.GetProperty("difficulty_locked"),
                     () => save.DifficultyLocked() == reads.GetProperty("difficulty_locked").GetBoolean());
                var weather = reads.GetProperty("weather");
                Read("weather", weather, () =>
                {
                    var w = save.Weather();
                    return w.Raining == weather.GetProperty("raining").GetBoolean()
                        && w.Thundering == weather.GetProperty("thundering").GetBoolean()
                        && w.RainTime == weather.GetProperty("rain_time").GetInt32()
                        && w.ThunderTime == weather.GetProperty("thunder_time").GetInt32()
                        && w.ClearWeatherTime == weather.GetProperty("clear_weather_time").GetInt32();
                });
                var spawn = reads.GetProperty("spawn");
                Read("spawn", spawn, () =>
                {
                    var (x, y, z) = save.SpawnPosition();
                    return x == spawn[0].GetInt32() && y == spawn[1].GetInt32()
                        && z == spawn[2].GetInt32();
                });
                foreach (var clock in reads.GetProperty("day_time").EnumerateObject())
                    Read($"clock of {clock.Name}", clock.Value,
                         () => save.DayTime(clock.Name).ToString() == clock.Value.GetString());
                foreach (var entry in reads.GetProperty("border").EnumerateObject())
                {
                    var want = entry.Value;
                    Read($"border of {entry.Name}", want, () =>
                    {
                        var b = save.WorldBorder(entry.Name);
                        return SameDouble(want.GetProperty("center_x"), b.CenterX)
                            && SameDouble(want.GetProperty("center_z"), b.CenterZ)
                            && SameDouble(want.GetProperty("size"), b.Size)
                            && SameDouble(want.GetProperty("safe_zone"), b.SafeZone)
                            && SameDouble(want.GetProperty("damage_per_block"), b.DamagePerBlock)
                            && b.WarningBlocks == want.GetProperty("warning_blocks").GetInt32()
                            && b.WarningTime == want.GetProperty("warning_time").GetInt32()
                            && SameDouble(want.GetProperty("lerp_target"), b.LerpTarget)
                            && b.LerpTime.ToString() == want.GetProperty("lerp_time").GetString()
                            && b.Stored == want.GetProperty("stored").GetBoolean();
                    });
                }
                var player = reads.GetProperty("player");
                Read("player", player, () =>
                {
                    var (px, py, pz) = save.PlayerPosition();
                    var (yaw, pitch) = save.PlayerRotation();
                    var pos = player.GetProperty("pos");
                    var rotation = player.GetProperty("rotation");
                    return SameDouble(pos[0], px) && SameDouble(pos[1], py) && SameDouble(pos[2], pz)
                        && SameDouble(rotation[0], yaw) && SameDouble(rotation[1], pitch)
                        && save.PlayerDimension() == player.GetProperty("dimension").GetString();
                });

                var refused = new List<string>();
                var writes = new (string Name, Action Write)[]
                {
                    ("save", () => save.Save()),
                    ("set_game_type", () => save.SetGameType("creative")),
                    ("set_weather", () => save.SetWeather("clear")),
                    ("set_day_time", () => save.SetDayTime("noon")),
                    ("set_spawn", () => save.SetSpawnPosition(0, 90, 0)),
                    ("create_chunks", () => save.CreateChunks((640, 0, 640), (640, 0, 640))),
                };
                foreach (var (name, write) in writes)
                {
                    try
                    {
                        write();
                    }
                    catch (ReadOnlySaveException)
                    {
                        refused.Add(name);
                    }
                }
                Check(refused.SequenceEqual(reads.GetProperty("refused_writes").EnumerateArray()
                          .Select(e => e.GetString()!)),
                      $"{label}: refused {string.Join(", ", refused)}");
            }
            finally
            {
                if (Directory.Exists(temp))
                    Directory.Delete(temp, recursive: true);
            }
        }
    }

    /// <summary>legacy.json: sixteen chunks Minecraft 1.17.1 saved, which
    /// the library reads and never writes. The biome-number table; each
    /// chunk's shape -- y 0..255 whatever it stores, every section of the
    /// world there, sections 1.17 left out read as air, sections of light
    /// alone outside the world -- with each section's palette and biomes;
    /// digests of every block and every biome cell; point, biome and light
    /// reads and every surface map; and then every world write, each
    /// refused, after which a save leaves the region file the fixture's own
    /// bytes.</summary>
    private static void VerifyLegacy(string corpus)
    {
        using var doc = Load(corpus, "legacy.json");
        var root = doc.RootElement;
        var range = root.GetProperty("legacy_data_versions");
        Check(ChunkBlocks.LegacyMinDataVersion == range[0].GetInt32()
              && ChunkBlocks.LegacyMaxDataVersion == range[1].GetInt32(),
              $"legacy DataVersions: got {ChunkBlocks.LegacyMinDataVersion}.."
              + $"{ChunkBlocks.LegacyMaxDataVersion}");
        var table = root.GetProperty("legacy_biomes");
        Check(LegacyBiomes.ByNumber.Length == table.GetArrayLength(),
              $"legacy biome table: {LegacyBiomes.ByNumber.Length} entries, "
              + $"want {table.GetArrayLength()}");
        int number = 0;
        foreach (var name in table.EnumerateArray())
        {
            if (number < LegacyBiomes.ByNumber.Length)
                Check(LegacyBiomes.ByNumber[number] == name.GetString(),
                      $"legacy biome {number}: got {LegacyBiomes.ByNumber[number]}, "
                      + $"want {name.GetString()}");
            number++;
        }

        string temp = AssembleWorldFrom(corpus, "legacy", "r.5.-1.mca");
        string regionPath = Path.Combine(
            temp, "dimensions", "minecraft", "overworld", "region", "r.5.-1.mca");
        try
        {
            var save = MinecraftSave.Load(temp, new[] { DimensionNames.Overworld });
            var dim = save.Dimension(DimensionNames.Overworld);

            // 1. Each chunk's shape, and digests of everything it reads as.
            foreach (var chunkNode in root.GetProperty("chunks").EnumerateArray())
            {
                int chunkX = chunkNode.GetProperty("chunk")[0].GetInt32();
                int chunkZ = chunkNode.GetProperty("chunk")[1].GetInt32();
                string label = $"chunk ({chunkX}, {chunkZ})";
                var chunk = dim.Chunk(chunkX, chunkZ);
                var accessor = dim.Blocks(chunkX, chunkZ);
                if (chunk == null || accessor == null)
                {
                    Check(false, $"{label}: not in the fixture");
                    continue;
                }
                Check(accessor.DataVersion == chunkNode.GetProperty("data_version").GetInt32(),
                      $"{label}: DataVersion {accessor.DataVersion}");
                Check(accessor.Legacy == chunkNode.GetProperty("legacy").GetBoolean(),
                      $"{label}: legacy {accessor.Legacy}");
                Check(accessor.Status == chunkNode.GetProperty("status").GetString(),
                      $"{label}: status {accessor.Status}");
                Check(accessor.MinY == chunkNode.GetProperty("min_y").GetInt32()
                      && accessor.MaxY == chunkNode.GetProperty("max_y").GetInt32(),
                      $"{label}: spans {accessor.MinY}..{accessor.MaxY}");

                var stored = new Dictionary<int, TagCompound>();
                if ((chunk.Data!.Get("Level") as TagCompound)?.Get("Sections") is TagList raw)
                    foreach (var tag in raw)
                        stored[Tag.AsInt(((TagCompound)tag).Get("Y")!)] = (TagCompound)tag;
                var sections = chunkNode.GetProperty("sections");
                Check(accessor.Sections.Count == sections.GetArrayLength(),
                      $"{label}: {accessor.Sections.Count} sections, "
                      + $"want {sections.GetArrayLength()}");
                foreach (var want in sections.EnumerateArray())
                {
                    int y = want.GetProperty("y").GetInt32();
                    string context = $"{label} section y={y}";
                    if (!accessor.Sections.TryGetValue(y, out var section))
                    {
                        Check(false, $"{context}: no such section");
                        continue;
                    }
                    string wantBlocks = want.GetProperty("blocks").GetString()!;
                    var blocks = section.Blocks;
                    string kind = blocks == null ? "absent"
                        : stored.TryGetValue(y, out var held) && held.Get("Palette") != null
                            ? "stored" : "air";
                    Check(kind == wantBlocks, $"{context}: blocks {kind}, want {wantBlocks}");
                    if (blocks != null && want.TryGetProperty("palette", out var palette))
                    {
                        var got = blocks.Palette.Select(state => state.ToString()).ToList();
                        var expected = palette.EnumerateArray().Select(e => e.GetString()!).ToList();
                        Check(got.SequenceEqual(expected), $"{context}: block palette");
                        Check(blocks.ReadOnly, $"{context}: blocks writable");
                    }
                    var biomes = section.Biomes;
                    var wantBiomes = want.GetProperty("biomes");
                    if (wantBiomes.ValueKind == JsonValueKind.Null)
                        Check(biomes == null, $"{context}: biomes present, want none");
                    else
                        Check(biomes != null && biomes.Palette.SequenceEqual(
                                  wantBiomes.EnumerateArray().Select(e => e.GetString()!)),
                              $"{context}: biome palette");
                    var light = new List<string>();
                    foreach (string name in new[] { "BlockLight", "SkyLight" })
                        if (stored.TryGetValue(y, out var tag) && tag.Get(name) != null)
                            light.Add(name);
                    Check(light.SequenceEqual(want.GetProperty("light").EnumerateArray()
                              .Select(e => e.GetString()!)),
                          $"{context}: light arrays");
                }

                var cells = new List<string>();
                for (int y = accessor.MinY; y <= accessor.MaxY; y++)
                    for (int z = 0; z < 16; z++)
                        for (int x = 0; x < 16; x++)
                            cells.Add(accessor.Get(x, y, z).ToString());
                Check(TextSha256(cells) == chunkNode.GetProperty("blocks_sha256").GetString(),
                      $"{label}: every block, digested, disagrees");
                var biomeCells = new List<string>();
                for (int y = accessor.MinY; y <= accessor.MaxY; y += 4)
                    for (int z = 0; z < 16; z += 4)
                        for (int x = 0; x < 16; x += 4)
                            biomeCells.Add(accessor.GetBiome(x, y, z));
                Check(TextSha256(biomeCells) == chunkNode.GetProperty("biomes_sha256").GetString(),
                      $"{label}: every biome cell, digested, disagrees");
            }

            // 2. Point, biome and light reads, and every surface map.
            CheckBlockReads(save, root.GetProperty("reads"), "");
            foreach (var read in root.GetProperty("biome_reads").EnumerateArray())
            {
                int x = read[0].GetInt32(), y = read[1].GetInt32(), z = read[2].GetInt32();
                string? want = read[3].ValueKind == JsonValueKind.Null ? null : read[3].GetString();
                string? got = save.GetBiome(x, y, z);
                Check(got == want, $"biome ({x}, {y}, {z}): got {got ?? "null"}, "
                                   + $"want {want ?? "null"}");
            }
            foreach (var read in root.GetProperty("light_reads").EnumerateArray())
            {
                int chunkX = read.GetProperty("chunk")[0].GetInt32();
                int chunkZ = read.GetProperty("chunk")[1].GetInt32();
                int x = read.GetProperty("x").GetInt32(), y = read.GetProperty("y").GetInt32();
                int z = read.GetProperty("z").GetInt32();
                var accessor = dim.Blocks(chunkX, chunkZ)!;
                string block, sky;
                try
                {
                    block = accessor.BlockLight(x, y, z)?.ToString() ?? "null";
                    sky = accessor.SkyLight(x, y, z)?.ToString() ?? "null";
                }
                catch (ArgumentOutOfRangeException)
                {
                    block = sky = "outside";
                }
                string Text(JsonElement value) => value.ValueKind switch
                {
                    JsonValueKind.Null => "null",
                    JsonValueKind.String => value.GetString()!,
                    _ => value.GetInt32().ToString(),
                };
                Check(block == Text(read.GetProperty("block")) && sky == Text(read.GetProperty("sky")),
                      $"light at chunk ({chunkX}, {chunkZ}) ({x}, {y}, {z}): got {block}/{sky}");
            }
            CheckSurfaces(save, root.GetProperty("surfaces"), "");

            // 3. Every write, refused.
            int stepIndex = 0;
            foreach (var step in root.GetProperty("steps").EnumerateArray())
            {
                string op = step.GetProperty("op").GetString()!;
                string label = $"step {stepIndex} ({op})";
                var want = step.GetProperty("result");
                (int X, int Y, int Z) At() => (step.GetProperty("at")[0].GetInt32(),
                                               step.GetProperty("at")[1].GetInt32(),
                                               step.GetProperty("at")[2].GetInt32());
                List<((int X, int Z) Chunk, string Reason)>? refused = null;
                switch (op)
                {
                    case "set_block":
                    {
                        var (x, y, z) = At();
                        bool got = save.SetBlock(x, y, z, step.GetProperty("block").GetString()!);
                        Check(got == want.GetProperty("written").GetBoolean(), $"{label}: {got}");
                        break;
                    }
                    case "place":
                    {
                        var (x, y, z) = At();
                        bool got = save.PlaceBlock(x, y, z, step.GetProperty("block").GetString()!);
                        Check(got == want.GetProperty("placed").GetBoolean(), $"{label}: {got}");
                        break;
                    }
                    case "set_biome":
                    {
                        var (x, y, z) = At();
                        bool got = save.SetBiome(x, y, z, step.GetProperty("biome").GetString()!);
                        Check(got == want.GetProperty("written").GetBoolean(), $"{label}: {got}");
                        break;
                    }
                    case "spawn_entity":
                    {
                        var at = step.GetProperty("at");
                        bool threw = false;
                        try
                        {
                            save.SpawnEntity(step.GetProperty("id").GetString()!,
                                             at[0].GetDouble(), at[1].GetDouble(),
                                             at[2].GetDouble());
                        }
                        catch (ReadOnlyChunkException)
                        {
                            threw = true;
                        }
                        Check(threw == want.GetProperty("refused").GetBoolean(),
                              $"{label}: refused {threw}");
                        break;
                    }
                    case "fill":
                        refused = save.Fill(Corner(step, "from"), Corner(step, "to"),
                                            step.GetProperty("block").GetString()!).UnreadableChunks;
                        break;
                    case "fill_biome":
                        refused = save.FillBiome(Corner(step, "from"), Corner(step, "to"),
                                                 step.GetProperty("biome").GetString()!).UnreadableChunks;
                        break;
                    case "unfreeze":
                        refused = save.Unfreeze(Corner(step, "from"), Corner(step, "to")).UnreadableChunks;
                        break;
                    case "connect":
                        refused = save.ConnectBlocks(Corner(step, "from"), Corner(step, "to")).UnreadableChunks;
                        break;
                    case "reset_lighting":
                        refused = save.ResetLighting(Corner(step, "from"), Corner(step, "to"),
                                                     neighbors: step.GetProperty("neighbors").GetBoolean())
                                      .UnreadableChunks;
                        break;
                    case "rebuild_heightmaps":
                        refused = save.RebuildHeightmaps(Corner(step, "from"), Corner(step, "to"))
                                      .UnreadableChunks;
                        break;
                    case "export_schem":
                        refused = save.ExportSchem(Corner(step, "from"), Corner(step, "to"),
                                                   Path.Combine(temp, "out.schem"),
                                                   allowIncomplete: true).UnreadableChunks;
                        break;
                    case "import_schem":
                    {
                        var paste = new Schematic(2, 2, 2, ChunkBlocks.MinDataVersion);
                        foreach (var cell in step.GetProperty("cells").EnumerateArray())
                            paste.SetBlock(cell[0].GetInt32(), cell[1].GetInt32(), cell[2].GetInt32(),
                                           step.GetProperty("block").GetString()!);
                        refused = save.ImportSchem(paste, At()).UnreadableChunks;
                        break;
                    }
                    default:
                        Check(false, $"{label}: unknown op");
                        break;
                }
                if (refused != null)
                {
                    var got = refused.Select(entry => (entry.Chunk.X, entry.Chunk.Z))
                        .OrderBy(c => c.X).ThenBy(c => c.Z).ToList();
                    var expected = want.GetProperty("unreadable_chunks").EnumerateArray()
                        .Select(c => (c[0].GetInt32(), c[1].GetInt32())).ToList();
                    Check(got.SequenceEqual(expected),
                          $"{label}: refused {string.Join(", ", got)}");
                    Check(refused.All(entry => entry.Reason.Contains("Minecraft 1.17")),
                          $"{label}: a reason does not name Minecraft 1.17");
                }
                stepIndex++;
            }
            save.Save();
            Check(Sha256Hex(File.ReadAllBytes(regionPath)) == root.GetProperty("region_sha256").GetString(),
                  "after every refused write and a save, the region file changed");
        }
        finally
        {
            Directory.Delete(temp, recursive: true);
        }
    }

    /// <summary>A section's stored palette against the corpus's report of
    /// it: the list's element type ("string", "compound", or "absent" for
    /// a section with no block_states) and, per entry, the shape of the
    /// stored tag — string, wrapped, full or legacy — beside the text of
    /// the state it decodes to. The first proves the port writes what the
    /// game writes, the second that it reads what the game means.</summary>
    private static void CheckPaletteReport(string label, Chunk chunk, ChunkBlocks accessor,
                                           int y, JsonElement want)
    {
        string context = $"{label} section y={y}";
        string wantStored = want.GetProperty("stored").GetString()!;
        if (!accessor.Sections.TryGetValue(y, out var section))
        {
            Check(false, $"{context}: no such section");
            return;
        }
        var container = section.Blocks;
        var stored = StoredPalette(chunk, y);
        if (container == null || stored == null)
        {
            Check(container == null && stored == null && wantStored == "absent",
                  $"{context}: no block_states here, want {wantStored}");
            return;
        }
        string kind = stored.ElementType switch
        {
            TagId.String => "string",
            TagId.Compound => "compound",
            _ => "other",
        };
        Check(kind == wantStored, $"{context}: the palette list is {kind}, want {wantStored}");

        var decoded = container.Palette;
        var entries = want.GetProperty("entries");
        Check(stored.Count == entries.GetArrayLength() && decoded.Count == stored.Count,
              $"{context}: {stored.Count} stored and {decoded.Count} decoded entries, "
              + $"want {entries.GetArrayLength()}");
        int i = 0;
        foreach (var entry in entries.EnumerateArray())
        {
            if (i >= stored.Count || i >= decoded.Count)
                break;
            string form = EntryForm(stored[i]);
            string state = decoded[i].ToString();
            string wantForm = entry.GetProperty("form").GetString()!;
            string wantState = entry.GetProperty("state").GetString()!;
            Check(form == wantForm && state == wantState,
                  $"{context} entry {i}: got {form} {state}, want {wantForm} {wantState}");
            i++;
        }
    }

    /// <summary>The shape of one stored palette entry, in the corpus's
    /// vocabulary: a bare id, its {"": id} wrapper, the 26.3 full form, or
    /// a legacy Name/Properties compound.</summary>
    private static string EntryForm(Tag entry) => entry switch
    {
        TagString => "string",
        TagCompound wrapper when wrapper.Count == 1 && wrapper.ContainsKey("") => "wrapped",
        TagCompound full when full.ContainsKey("id") => "full",
        TagCompound legacy when legacy.ContainsKey("Name") => "legacy",
        _ => "other",
    };

    /// <summary>The palette list as it sits in the chunk's NBT — read off
    /// the raw tree rather than through the accessor, because "stored"
    /// means the bytes.</summary>
    private static TagList? StoredPalette(Chunk chunk, int y)
    {
        if (chunk.Data?.Get("sections") is not TagList sections)
            return null;
        foreach (var tag in sections)
        {
            if (tag is TagCompound section && section.Get("Y") is Tag sectionY
                && Tag.AsInt(sectionY) == y)
                return (section.Get("block_states") as TagCompound)?.Get("palette") as TagList;
        }
        return null;
    }

    // -- chunk deletion ----------------------------------------------------

    /// <summary>Deleting chunks, checked on the region files themselves.
    /// Every other write in this corpus is checked through chunk NBT, and a
    /// deleted chunk has none left to check: its whole effect is the
    /// container around it. Whole-file digests work here, and only here,
    /// because deletion is the one write that recompresses nothing.</summary>
    private static void VerifyChunkDeletes(string corpus)
    {
        using var doc = Load(corpus, "chunk_deletes.json");
        var root = doc.RootElement;

        // The two sidecar inputs are read straight out of the corpus rather
        // than through the save, because the reads the script makes before
        // its deletes are load-bearing and nothing may disturb them.
        foreach (string tree in new[] { "entities", "poi", "entities_far" })
        {
            var input = root.GetProperty("inputs").GetProperty(tree);
            string path = tree == "entities_far"
                ? Path.Combine(corpus, "chunk_deletes", "entities", "r.-1.-1.mca")
                : Path.Combine(corpus, "chunk_deletes", tree, "r.-1.0.mca");
            byte[] raw = File.ReadAllBytes(path);
            Check(Sha256Hex(raw) == input.GetProperty("sha256").GetString(),
                  $"input {tree}: file digest");
            Check(raw.Length == input.GetProperty("bytes").GetInt32(),
                  $"input {tree}: file length");

            var file = NbtFile.Load(path);
            var chunks = input.GetProperty("chunks");
            Check(file.Chunks.Count == chunks.GetArrayLength(),
                  $"input {tree}: {file.Chunks.Count} chunks read, "
                  + $"want {chunks.GetArrayLength()}");
            foreach (var entry in chunks.EnumerateArray())
            {
                int chunkX = entry.GetProperty("chunk")[0].GetInt32();
                int chunkZ = entry.GetProperty("chunk")[1].GetInt32();
                var chunk = file.Chunk(chunkX, chunkZ);
                if (chunk == null)
                {
                    Check(false, $"input {tree}: chunk ({chunkX}, {chunkZ}) missing");
                    continue;
                }
                Check(chunk.Timestamp == entry.GetProperty("timestamp").GetUInt32(),
                      $"input {tree} ({chunkX}, {chunkZ}): timestamp");
                Check(Sha256Hex(Codec.Encode(chunk.Data!, chunk.DataName))
                      == entry.GetProperty("payload_sha256").GetString(),
                      $"input {tree} ({chunkX}, {chunkZ}): payload digest — these "
                      + "chunks are stored uncompressed (scheme 3) so their bytes "
                      + "are portable");
            }
        }

        string temp = AssembleWorld(corpus, sidecars: true);
        try
        {
            var save = MinecraftSave.Load(temp, new[] { DimensionNames.Overworld });
            var dim = save.Dimension(DimensionNames.Overworld);
            string OnDisk(string tree) => Path.Combine(
                temp, "dimensions", "minecraft", "overworld", tree, "r.-1.0.mca");
            NbtFile Opened(string tree) => tree switch
            {
                "region" => dim.Region(-1, 0)!,
                "entities" => dim.EntityRegion(-1, 0)!,
                _ => dim.PoiRegion(-1, 0)!,
            };
            var trees = new[] { "region", "entities", "poi" };
            var before = trees.ToDictionary(
                tree => tree, tree => File.ReadAllBytes(OnDisk(tree)),
                StringComparer.Ordinal);

            // Reading a column decodes its chunk and leaves the accessor
            // cached, which is the only way the reads afterwards can tell a
            // deleted chunk from a cached one.
            void ReadColumns(string which)
            {
                foreach (var entry in root.GetProperty(which).EnumerateArray())
                {
                    int chunkX = entry.GetProperty("chunk")[0].GetInt32();
                    int chunkZ = entry.GetProperty("chunk")[1].GetInt32();
                    var wanted = entry.GetProperty("status");
                    string? wantStatus = wanted.ValueKind == JsonValueKind.Null
                        ? null : wanted.GetString();
                    string? gotStatus = dim.ChunkStatus(chunkX, chunkZ);
                    Check(gotStatus == wantStatus,
                          $"{which} ({chunkX}, {chunkZ}): status, got "
                          + $"{gotStatus ?? "null"}, want {wantStatus ?? "null"}");

                    int x = entry.GetProperty("x").GetInt32();
                    int z = entry.GetProperty("z").GetInt32();
                    var height = entry.GetProperty("surface_y");
                    int? wantSurface = height.ValueKind == JsonValueKind.Null
                        ? (int?)null : height.GetInt32();
                    int? gotSurface = save.SurfaceY(x, z);
                    Check(gotSurface == wantSurface,
                          $"{which} surface ({x}, {z}): got "
                          + $"{gotSurface?.ToString() ?? "null"}, "
                          + $"want {wantSurface?.ToString() ?? "null"}");
                }
            }

            ReadColumns("reads_before");

            int stepIndex = 0;
            foreach (var step in root.GetProperty("steps").EnumerateArray())
            {
                string op = step.GetProperty("op").GetString()!;
                string label = $"step {stepIndex} ({op})";
                stepIndex++;
                if (op != "delete_chunks")
                {
                    Check(false, $"{label}: unknown op");
                    continue;
                }
                var want = step.GetProperty("result");
                var got = save.DeleteChunks(Corner(step, "from"), Corner(step, "to"));
                CheckInt(label, "chunks_deleted", got.ChunksDeleted, want);
                CheckInt(label, "chunks_absent", got.ChunksAbsent, want);
                CheckInt(label, "entity_chunks_deleted", got.EntityChunksDeleted, want);
                CheckInt(label, "poi_chunks_deleted", got.PoiChunksDeleted, want);
                CheckInt(label, "regions_touched", got.RegionsTouched, want);
                var wantBox = want.GetProperty("deleted_box");
                var (min, max) = (got.DeletedBox!.Value.Min, got.DeletedBox.Value.Max);
                Check(min.X == wantBox[0][0].GetInt32()
                      && min.Z == wantBox[0][1].GetInt32()
                      && max.X == wantBox[1][0].GetInt32()
                      && max.Z == wantBox[1][1].GetInt32(),
                      $"{label}: deleted_box, got ({min.X},{min.Z})-({max.X},{max.Z})");
            }

            ReadColumns("reads_after");

            var untouched = root.GetProperty("untouched_region");
            Check((dim.EntityRegion(0, 0) == null)
                  == untouched.GetProperty("entity_region_0_0").GetBoolean(),
                  "a delete over a region the world never wrote conjured an "
                  + "entities file into existence");
            Check((dim.PoiRegion(0, 0) == null)
                  == untouched.GetProperty("poi_region_0_0").GetBoolean(),
                  "a delete over a region the world never wrote conjured a "
                  + "poi file into existence");

            var after = trees.ToDictionary(
                tree => tree, tree => Opened(tree).ToBytes(), StringComparer.Ordinal);
            foreach (string tree in trees)
            {
                CheckDeletedFile(tree, before[tree], after[tree], Opened(tree),
                                 root.GetProperty("files").GetProperty(tree));
            }

            var expected = root.GetProperty("save");
            int written = save.Save();
            Check(written == expected.GetProperty("files_written").GetInt32(),
                  $"save(): wrote {written} files, want "
                  + $"{expected.GetProperty("files_written").GetInt32()} — save "
                  + "writes only regions that were opened, so a port that read "
                  + "EntityAvailable without opening the file gets every count "
                  + "above right and writes nothing");
            foreach (var entry in
                     expected.GetProperty("on_disk_matches_serialized").EnumerateObject())
            {
                bool same = File.ReadAllBytes(OnDisk(entry.Name))
                    .AsSpan().SequenceEqual(after[entry.Name]);
                Check(same == entry.Value.GetBoolean(),
                      $"{entry.Name}: what save() left on disk");
            }
        }
        finally
        {
            Directory.Delete(temp, recursive: true);
        }
    }

    /// <summary>One region file's before and after, told in bytes: the
    /// header tables, the entries cleared, and every survivor still sitting
    /// unmoved in its own sectors.</summary>
    private static void CheckDeletedFile(
        string name, byte[] before, byte[] after, NbtFile region, JsonElement want)
    {
        byte[] Table(byte[] data, int which) =>
            data.AsSpan(which * NbtFile.Sector, NbtFile.Sector).ToArray();
        byte[] Sectors(byte[] data, int offset, int count) =>
            data.AsSpan(offset * NbtFile.Sector, count * NbtFile.Sector).ToArray();
        string Location(byte[] data, int index) =>
            Convert.ToHexString(data, index * 4, 4).ToLowerInvariant();
        (int Offset, int Count) Placement(byte[] data, int index) =>
            ((data[index * 4] << 16) | (data[index * 4 + 1] << 8) | data[index * 4 + 2],
             data[index * 4 + 3]);
        uint Stamp(byte[] data, int index) => BinaryPrimitives.ReadUInt32BigEndian(
            data.AsSpan(NbtFile.Sector + index * 4, 4));

        Check(Sha256Hex(before) == want.GetProperty("sha256_before").GetString(),
              $"{name}: file digest before");
        Check(Sha256Hex(after) == want.GetProperty("sha256_after").GetString(),
              $"{name}: file digest after");
        Check(before.Length == want.GetProperty("bytes_before").GetInt32(),
              $"{name}: file length before");
        Check(after.Length == want.GetProperty("bytes_after").GetInt32(),
              $"{name}: file length after");
        Check((before.Length == after.Length)
              == want.GetProperty("length_unchanged").GetBoolean(),
              $"{name}: length_unchanged — an emptied region keeps its sectors, "
              + "it is not truncated");

        var locations = want.GetProperty("location_table_sha256");
        Check(Sha256Hex(Table(before, 0)) == locations.GetProperty("before").GetString(),
              $"{name}: location table before");
        Check(Sha256Hex(Table(after, 0)) == locations.GetProperty("after").GetString(),
              $"{name}: location table after");
        var stamps = want.GetProperty("timestamp_table_sha256");
        Check(Sha256Hex(Table(before, 1)) == stamps.GetProperty("before").GetString(),
              $"{name}: timestamp table before");
        Check(Sha256Hex(Table(after, 1)) == stamps.GetProperty("after").GetString(),
              $"{name}: timestamp table after");
        Check(Table(before, 1).AsSpan().SequenceEqual(Table(after, 1))
              == want.GetProperty("timestamp_table_unchanged").GetBoolean(),
              $"{name}: timestamp_table_unchanged — vanilla's RegionFile.clear "
              + "stamps the current second, which nothing reads and which would "
              + "make the file unreproducible");

        foreach (var entry in want.GetProperty("cleared").EnumerateArray())
        {
            int chunkX = entry.GetProperty("chunk")[0].GetInt32();
            int chunkZ = entry.GetProperty("chunk")[1].GetInt32();
            int index = entry.GetProperty("index").GetInt32();
            string where = $"{name} cleared ({chunkX}, {chunkZ})";
            Check(index == (chunkX & 31) + (chunkZ & 31) * 32, $"{where}: header index");
            Check(Location(before, index)
                  == entry.GetProperty("location_before").GetString(),
                  $"{where}: location entry before");
            Check(Location(after, index)
                  == entry.GetProperty("location_after").GetString(),
                  $"{where}: location entry after, got {Location(after, index)}");
            int offset = entry.GetProperty("sectors_before")[0].GetInt32();
            int count = entry.GetProperty("sectors_before")[1].GetInt32();
            Check(Sectors(after, offset, count).All(b => b == 0)
                  == entry.GetProperty("freed_sectors_zeroed").GetBoolean(),
                  $"{where}: freed sectors zeroed");
            Check(Stamp(before, index) == entry.GetProperty("timestamp_before").GetUInt32(),
                  $"{where}: timestamp before");
            Check(Stamp(after, index) == entry.GetProperty("timestamp_after").GetUInt32(),
                  $"{where}: timestamp after, got {Stamp(after, index)}");
        }

        var survivors = want.GetProperty("survivors");
        Check(region.Chunks.Count == survivors.GetArrayLength(),
              $"{name}: {region.Chunks.Count} chunks left, "
              + $"want {survivors.GetArrayLength()}");
        foreach (var entry in survivors.EnumerateArray())
        {
            int chunkX = entry.GetProperty("chunk")[0].GetInt32();
            int chunkZ = entry.GetProperty("chunk")[1].GetInt32();
            string where = $"{name} survivor ({chunkX}, {chunkZ})";
            var chunk = region.Chunk(chunkX, chunkZ);
            if (chunk == null)
            {
                Check(false, $"{where}: deleted along with the box");
                continue;
            }
            int index = entry.GetProperty("index").GetInt32();
            int offset = entry.GetProperty("sectors")[0].GetInt32();
            int count = entry.GetProperty("sectors")[1].GetInt32();
            Check(Placement(after, index) == (offset, count),
                  $"{where}: sectors, got {Placement(after, index)}, "
                  + $"want ({offset}, {count})");
            byte[] record = Sectors(after, offset, count);
            Check(Sha256Hex(record) == entry.GetProperty("record_sha256").GetString(),
                  $"{where}: sector span digest");
            Check(Sectors(before, offset, count).AsSpan().SequenceEqual(record)
                  == entry.GetProperty("record_unmoved").GetBoolean(),
                  $"{where}: record unmoved");
            Check(Sha256Hex(Codec.Encode(chunk.Data!, chunk.DataName))
                  == entry.GetProperty("payload_sha256").GetString(),
                  $"{where}: NBT payload digest");
        }
    }

    // -- transforms --------------------------------------------------------

    private static void VerifyTransforms(string corpus)
    {
        using var doc = Load(corpus, "transforms.json");
        var root = doc.RootElement;

        string Apply(string stateText, string op)
        {
            var state = BlockState.Parse(stateText);
            if (op.StartsWith("rot", StringComparison.Ordinal))
            {
                int quarters = int.Parse(op.Substring(3)) / 90;
                return Transforms.MapState(state, properties =>
                {
                    for (int i = 0; i < quarters; i++)
                        properties = Transforms.PropertiesRotatedCw(properties);
                    return properties;
                }).ToString();
            }
            string axis = op.Substring(op.Length - 1);
            return Transforms.MapState(
                state, properties => Transforms.PropertiesMirrored(properties, axis))
                .ToString();
        }

        string[] ops = { "rot90", "rot180", "rot270", "flipx", "flipy", "flipz" };
        foreach (var item in root.GetProperty("states").EnumerateArray())
        {
            string state = item.GetProperty("state").GetString()!;
            foreach (string op in ops)
            {
                string actual = Apply(state, op);
                string expected = item.GetProperty(op).GetString()!;
                Check(actual == expected,
                      $"{op} of {state}: got {actual}, want {expected}");
            }
        }

        // The facing-byte tables are data, so compare them entry for entry.
        var bytes = root.GetProperty("facing_bytes");
        CheckByteTable("rot90 3D", bytes.GetProperty("rot90"), Transforms.Facing3Cw);
        CheckByteTable("rot90 2D", bytes.GetProperty("two_d_rot90"), Transforms.Facing2Cw);
        foreach (string axis in new[] { "x", "y", "z" })
        {
            CheckByteTable($"mirror {axis} 3D",
                           bytes.GetProperty("mirror").GetProperty(axis),
                           Transforms.Facing3Mirror[axis]);
            CheckByteTable($"mirror {axis} 2D",
                           bytes.GetProperty("two_d_mirror").GetProperty(axis),
                           Transforms.Facing2Mirror[axis]);
        }

        // Geometry: the cell map for a quarter turn, on the corpus's box.
        var geometry = root.GetProperty("geometry");
        var box = geometry.GetProperty("box");
        int length = box[2].GetInt32();
        foreach (var sample in geometry.GetProperty("cell_samples_rot90").EnumerateArray())
        {
            var from = sample[0];
            var want = sample[1];
            int x = from[0].GetInt32(), y = from[1].GetInt32(), z = from[2].GetInt32();
            var got = (length - 1 - z, y, x);
            Check(got == (want[0].GetInt32(), want[1].GetInt32(), want[2].GetInt32()),
                  $"cell rot90 of ({x}, {y}, {z}): got {got}");
        }

        // Yaw, including the wraps: every case runs through the same maps the
        // transforms hand to the entity rewriter.
        foreach (var item in root.GetProperty("yaw").GetProperty("cases").EnumerateArray())
        {
            double yaw = item.GetProperty("yaw").GetDouble();
            foreach (var (op, mapped) in new (string, double)[]
                     {
                         ("rot90", yaw + 90.0),
                         ("rot180", yaw + 180.0),
                         ("rot270", yaw + 270.0),
                         ("flipx", -yaw),
                         ("flipy", yaw),
                         ("flipz", 180.0 - yaw),
                     })
            {
                double actual = Transforms.WrapDegrees(mapped);
                double expected = item.GetProperty(op).GetDouble();
                Check(actual.Equals(expected),
                      $"yaw {yaw} under {op}: got {actual}, want {expected}");
            }
        }

        // Negative and over-full rotations normalize the same way. C#'s /
        // truncates where Python's // floors, so this is the guarded form.
        foreach (var pair in root.GetProperty("negative_rotation")
                     .GetProperty("equivalences").EnumerateArray())
        {
            int degrees = pair[0].GetInt32(), equivalent = pair[1].GetInt32();
            Check(Quarters(degrees) == Quarters(equivalent),
                  $"rotation {degrees} must normalize like {equivalent}");
        }
    }

    private static int Quarters(int degrees) => ((degrees / 90) % 4 + 4) % 4;

    private static void CheckByteTable(string what, JsonElement expected,
                                       Dictionary<int, int> actual)
    {
        var want = expected.EnumerateObject()
            .ToDictionary(property => int.Parse(property.Name),
                          property => property.Value.GetInt32());
        Check(want.Count == actual.Count
              && want.All(pair => actual.TryGetValue(pair.Key, out int got)
                                  && got == pair.Value),
              $"facing byte table {what}");
    }

    // -- schem goldens -----------------------------------------------------

    private static void VerifySchemGoldens(string corpus)
    {
        using var doc = Load(corpus, "schem/expected.json");
        foreach (var file in doc.RootElement.GetProperty("files").EnumerateObject())
        {
            string name = file.Name;
            var spec = file.Value;
            string path = Path.Combine(corpus, "schem", name);

            // The golden's own bytes are gzip, which is not portable; the
            // contract is the uncompressed payload's digest.
            byte[] payload = Compression.GzipDecompress(File.ReadAllBytes(path));
            Check(Sha256Hex(payload) == spec.GetProperty("payload_sha256").GetString(),
                  $"{name}: payload digest");

            var schematic = Schematic.Load(path);
            var size = spec.GetProperty("size");
            Check(schematic.Width == size[0].GetInt32()
                  && schematic.Height == size[1].GetInt32()
                  && schematic.Length == size[2].GetInt32(),
                  $"{name}: size, got {schematic.Size}");
            var offset = spec.GetProperty("offset");
            Check(schematic.Offset == (offset[0].GetInt32(), offset[1].GetInt32(),
                                       offset[2].GetInt32()),
                  $"{name}: offset, got {schematic.Offset}");
            Check(schematic.DataVersion == spec.GetProperty("data_version").GetInt32(),
                  $"{name}: data version");
            var container = spec.GetProperty("container_version");
            Check(schematic.SourceVersion
                      == (container.ValueKind == JsonValueKind.Null ? (int?)null : container.GetInt32()),
                  $"{name}: container version");
            string format = spec.TryGetProperty("source_format", out var formatNode)
                ? formatNode.GetString()! : "sponge";
            Check(schematic.SourceFormat == format, $"{name}: source format {schematic.SourceFormat}");
            Check(schematic.PaletteCount == spec.GetProperty("palette_size").GetInt32(),
                  $"{name}: palette size, got {schematic.PaletteCount}");
            Check(schematic.HasBiomes == spec.GetProperty("has_biomes").GetBoolean(),
                  $"{name}: has biomes");

            foreach (var sample in spec.GetProperty("block_samples").EnumerateArray())
            {
                int x = sample[0].GetInt32(), y = sample[1].GetInt32(),
                    z = sample[2].GetInt32();
                string actual = schematic.GetBlock(x, y, z).ToString();
                string expected = sample[3].GetString()!;
                Check(actual == expected,
                      $"{name}: block ({x}, {y}, {z}): got {actual}, want {expected}");
            }

            foreach (var sample in spec.GetProperty("biome_samples").EnumerateArray())
            {
                int x = sample[0].GetInt32(), y = sample[1].GetInt32(),
                    z = sample[2].GetInt32();
                string? expected = sample[3].ValueKind == JsonValueKind.Null
                    ? null : sample[3].GetString();
                string? actual = schematic.GetBiome(x, y, z);
                Check(actual == expected,
                      $"{name}: biome ({x}, {y}, {z}): got {actual ?? "null"}, "
                      + $"want {expected ?? "null"}");
            }

            var wantEntities = spec.GetProperty("block_entities").EnumerateArray()
                .ToList();
            Check(schematic.BlockEntities.Count == wantEntities.Count,
                  $"{name}: block entity count");
            foreach (var entry in wantEntities)
            {
                var pos = entry.GetProperty("pos");
                var key = (pos[0].GetInt32(), pos[1].GetInt32(), pos[2].GetInt32());
                Check(schematic.BlockEntities.TryGetValue(key, out var data)
                      && Tag.AsString(data["id"]) == entry.GetProperty("id").GetString(),
                      $"{name}: block entity at {key}");
            }

            var wantMobs = spec.GetProperty("entities").EnumerateArray().ToList();
            Check(schematic.Entities.Count == wantMobs.Count,
                  $"{name}: entity count");
            for (int i = 0; i < Math.Min(schematic.Entities.Count, wantMobs.Count); i++)
            {
                var entity = schematic.Entities[i];
                var want = wantMobs[i];
                Check(Tag.AsString(entity["id"]) == want.GetProperty("id").GetString(),
                      $"{name}: entity {i} id");
                var pos = (TagList)entity["Pos"];
                var wantPos = want.GetProperty("pos");
                Check(Tag.AsDouble(pos[0]) == wantPos[0].GetDouble()
                      && Tag.AsDouble(pos[1]) == wantPos[1].GetDouble()
                      && Tag.AsDouble(pos[2]) == wantPos[2].GetDouble(),
                      $"{name}: entity {i} position");
                if (want.TryGetProperty("block_pos", out var wantBlock))
                {
                    var block = (TagIntArray)entity["block_pos"];
                    Check(block.Values[0] == wantBlock[0].GetInt32()
                          && block.Values[1] == wantBlock[1].GetInt32()
                          && block.Values[2] == wantBlock[2].GetInt32(),
                          $"{name}: entity {i} block_pos");
                }
                if (want.TryGetProperty("facing_byte", out var wantFacing))
                    Check(Tag.AsInt(entity["Facing"]) == wantFacing.GetInt32(),
                          $"{name}: entity {i} facing byte");
                if (want.TryGetProperty("uuid", out var wantUuid))
                {
                    var uuid = (TagIntArray)entity["UUID"];
                    Check(uuid.Values.SequenceEqual(
                              wantUuid.EnumerateArray().Select(v => v.GetInt32())),
                          $"{name}: entity {i} UUID preserved");
                }
            }

            // A writer verifies itself by re-reading its own output: write the
            // schematic back at its own container version and check that the
            // reload is identical where it counts.
            if (spec.TryGetProperty("written_from", out var writtenFrom))
            {
                // This writer, from the same schematic, must write the bytes
                // the reference wrote (uncompressed: gzip is not portable).
                var source = Schematic.Load(Path.Combine(corpus, "schem", writtenFrom.GetString()!));
                byte[] ours = Compression.GzipDecompress(source.ToBytes(3, format));
                Check(Sha256Hex(ours) == spec.GetProperty("payload_sha256").GetString(),
                      $"{name}: written from {writtenFrom.GetString()} by this writer");
            }
            string extension = format == "structure" ? ".nbt" : format == "litematic" ? ".litematic" : ".schem";
            string temp = Path.Combine(Path.GetTempPath(),
                                       "hv-schem-" + Guid.NewGuid().ToString("N") + extension);
            try
            {
                schematic.Save(temp, schematic.SourceVersion is int v && format == "sponge" ? v : 3);
                var again = Schematic.Load(temp);
                Check(again.Size == schematic.Size && again.Offset == schematic.Offset
                      && again.PaletteCount == schematic.PaletteCount
                      && again.HasBiomes == schematic.HasBiomes
                      && again.Indices().SequenceEqual(schematic.Indices()),
                      $"{name}: round trip through this writer");
            }
            finally
            {
                File.Delete(temp);
            }
        }

        // The pre-rotated golden pins the transform end to end: rotating the
        // v3 sample here must reproduce it.
        var files = doc.RootElement.GetProperty("files");
        if (files.TryGetProperty("sample_v3_rot90.schem", out var rotatedSpec))
        {
            var rotated = Schematic.Load(Path.Combine(corpus, "schem", "sample_v3.schem"))
                .Rotated(90);
            var expected = Schematic.Load(
                Path.Combine(corpus, "schem", "sample_v3_rot90.schem"));
            Check(rotated.Size == expected.Size, "rot90: size matches the golden");
            Check(rotated.Offset == expected.Offset, "rot90: offset matches the golden");
            foreach (var sample in rotatedSpec.GetProperty("block_samples")
                         .EnumerateArray())
            {
                int x = sample[0].GetInt32(), y = sample[1].GetInt32(),
                    z = sample[2].GetInt32();
                string actual = rotated.GetBlock(x, y, z).ToString();
                Check(actual == sample[3].GetString(),
                      $"rot90 of sample_v3: block ({x}, {y}, {z}) is {actual}");
            }
            foreach (var sample in rotatedSpec.GetProperty("biome_samples")
                         .EnumerateArray())
            {
                int x = sample[0].GetInt32(), y = sample[1].GetInt32(),
                    z = sample[2].GetInt32();
                Check(rotated.GetBiome(x, y, z) == sample[3].GetString(),
                      $"rot90 of sample_v3: biome ({x}, {y}, {z})");
            }
            var wantEntity = rotatedSpec.GetProperty("entities")[0];
            var entity = rotated.Entities[0];
            var pos = (TagList)entity["Pos"];
            var wantPos = wantEntity.GetProperty("pos");
            Check(Tag.AsDouble(pos[0]) == wantPos[0].GetDouble()
                  && Tag.AsDouble(pos[1]) == wantPos[1].GetDouble()
                  && Tag.AsDouble(pos[2]) == wantPos[2].GetDouble(),
                  "rot90 of sample_v3: entity position");
            Check(Tag.AsInt(entity["Facing"])
                      == wantEntity.GetProperty("facing_byte").GetInt32(),
                  "rot90 of sample_v3: entity facing byte");
            var block = (TagIntArray)entity["block_pos"];
            var wantBlock = wantEntity.GetProperty("block_pos");
            Check(block.Values[0] == wantBlock[0].GetInt32()
                  && block.Values[1] == wantBlock[1].GetInt32()
                  && block.Values[2] == wantBlock[2].GetInt32(),
                  "rot90 of sample_v3: entity block_pos");
        }
    }

    // -- world settings ----------------------------------------------------

    /// <summary>Assemble the fixture world with the split settings files
    /// and the player, run the corpus's settings script, and check every
    /// read-back, every refusal, and the resulting NBT byte-for-byte.
    /// The digests come from files REWRITTEN by Save() and re-loaded, so
    /// this also proves the persistence path; digests are of the
    /// uncompressed encoding, so recompression differences cannot hide
    /// or cause a failure.</summary>
    private static void VerifyWorldSettings(string corpus)
    {
        using var doc = Load(corpus, "world_settings.json");
        var root = doc.RootElement;
        string temp = AssembleWorld(corpus);
        try
        {
            string dataDir = Path.Combine(temp, "data", "minecraft");
            Directory.CreateDirectory(dataDir);
            File.Copy(Path.GetFullPath(Path.Combine(corpus, "..", "weather.dat")),
                      Path.Combine(dataDir, "weather.dat"));
            File.Copy(Path.GetFullPath(Path.Combine(corpus, "..", "world_clocks.dat")),
                      Path.Combine(dataDir, "world_clocks.dat"));
            string playerTarget = Path.Combine(temp,
                root.GetProperty("player_target").GetString()!
                    .Replace('/', Path.DirectorySeparatorChar));
            Directory.CreateDirectory(Path.GetDirectoryName(playerTarget)!);
            File.Copy(Path.GetFullPath(Path.Combine(corpus, "..", "player.dat")),
                      playerTarget);

            var save = MinecraftSave.Load(temp, new[] { DimensionNames.Overworld });

            void CheckBorder(string label, JsonElement want,
                             string? dimension = null)
            {
                var border = save.WorldBorder(dimension);
                void Dbl(string name, double actual)
                {
                    ulong bits = ulong.Parse(
                        want.GetProperty(name).GetProperty("bits").GetString()!,
                        System.Globalization.NumberStyles.HexNumber);
                    Check((ulong)BitConverter.DoubleToInt64Bits(actual) == bits,
                          $"{label}: {name} bits");
                }
                Check(border.Stored == want.GetProperty("stored").GetBoolean(),
                      $"{label}: stored");
                Dbl("center_x", border.CenterX);
                Dbl("center_z", border.CenterZ);
                Dbl("size", border.Size);
                Dbl("safe_zone", border.SafeZone);
                Dbl("damage_per_block", border.DamagePerBlock);
                Check(border.WarningBlocks
                          == want.GetProperty("warning_blocks").GetInt32(),
                      $"{label}: warning_blocks");
                Check(border.WarningTime
                          == want.GetProperty("warning_time").GetInt32(),
                      $"{label}: warning_time");
                Dbl("lerp_target", border.LerpTarget);
                Check(border.LerpTime
                          == long.Parse(want.GetProperty("lerp_time").GetString()!),
                      $"{label}: lerp_time");
                Dbl("min_x", border.MinX);
                Dbl("max_x", border.MaxX);
                Dbl("min_z", border.MinZ);
                Dbl("max_z", border.MaxZ);
            }

            void CheckSnapshot(string label, JsonElement want)
            {
                CheckBorder($"{label}: world_border",
                            want.GetProperty("world_border"));
                CheckBorder($"{label}: world_border_the_end",
                            want.GetProperty("world_border_the_end"),
                            DimensionNames.End);
                Check(save.GameType() == want.GetProperty("game_type").GetString(),
                      $"{label}: game_type");
                Check(save.AllowCommands()
                          == want.GetProperty("allow_commands").GetBoolean(),
                      $"{label}: allow_commands");
                Check(save.Difficulty() == want.GetProperty("difficulty").GetString(),
                      $"{label}: difficulty");
                Check(save.Hardcore() == want.GetProperty("hardcore").GetBoolean(),
                      $"{label}: hardcore");
                Check(save.DifficultyLocked()
                          == want.GetProperty("difficulty_locked").GetBoolean(),
                      $"{label}: difficulty_locked");
                var weather = save.Weather();
                var wantWeather = want.GetProperty("weather");
                Check(weather.Kind == wantWeather.GetProperty("kind").GetString(),
                      $"{label}: weather kind");
                Check(weather.Raining == wantWeather.GetProperty("raining").GetBoolean()
                      && weather.Thundering
                          == wantWeather.GetProperty("thundering").GetBoolean()
                      && weather.RainTime
                          == wantWeather.GetProperty("rain_time").GetInt32()
                      && weather.ThunderTime
                          == wantWeather.GetProperty("thunder_time").GetInt32()
                      && weather.ClearWeatherTime
                          == wantWeather.GetProperty("clear_weather_time").GetInt32(),
                      $"{label}: weather fields");
                foreach (var clock in want.GetProperty("day_time").EnumerateObject())
                {
                    Check(save.DayTime(clock.Name)
                              == long.Parse(clock.Value.GetString()!),
                          $"{label}: day_time {clock.Name}");
                }
                var spawn = save.SpawnPosition();
                var wantSpawn = want.GetProperty("spawn");
                Check(spawn.X == wantSpawn[0].GetInt32()
                      && spawn.Y == wantSpawn[1].GetInt32()
                      && spawn.Z == wantSpawn[2].GetInt32(),
                      $"{label}: spawn");
            }

            CheckSnapshot("reads_before", root.GetProperty("reads_before"));

            int stepIndex = 0;
            foreach (var step in root.GetProperty("steps").EnumerateArray())
            {
                string op = step.GetProperty("op").GetString()!;
                string label = $"step {stepIndex} ({op})";
                if (step.TryGetProperty("refused", out var refused)
                    && refused.GetBoolean())
                {
                    bool threw = false;
                    try
                    {
                        if (op == "set_difficulty")
                            save.SetDifficulty(step.GetProperty("name").GetString()!);
                        else if (op == "world_border")
                            save.WorldBorder(
                                step.TryGetProperty("dimension", out var badDim)
                                    ? badDim.GetString() : null);
                        else
                            throw new InvalidOperationException(
                                $"no refusal path for '{op}'");
                    }
                    catch (NbtException)
                    {
                        threw = true;
                    }
                    Check(threw, $"{label}: must refuse");
                    stepIndex++;
                    continue;
                }
                var want = step.GetProperty("result");
                switch (op)
                {
                    case "set_game_type":
                        save.SetGameType(step.GetProperty("mode").GetString()!);
                        Check(save.GameType()
                                  == want.GetProperty("game_type").GetString(),
                              $"{label}: read-back");
                        break;
                    case "set_allow_commands":
                        save.SetAllowCommands(step.GetProperty("value").GetBoolean());
                        Check(save.AllowCommands()
                                  == want.GetProperty("allow_commands").GetBoolean(),
                              $"{label}: read-back");
                        break;
                    case "set_hardcore":
                        save.SetHardcore(step.GetProperty("value").GetBoolean());
                        Check(save.Hardcore()
                                  == want.GetProperty("hardcore").GetBoolean(),
                              $"{label}: read-back");
                        break;
                    case "set_difficulty":
                        save.SetDifficulty(step.GetProperty("name").GetString()!);
                        Check(save.Difficulty()
                                  == want.GetProperty("difficulty").GetString(),
                              $"{label}: read-back");
                        break;
                    case "set_difficulty_locked":
                        save.SetDifficultyLocked(
                            step.GetProperty("value").GetBoolean());
                        Check(save.DifficultyLocked()
                                  == want.GetProperty("locked").GetBoolean(),
                              $"{label}: read-back");
                        break;
                    case "set_weather":
                    {
                        int? duration = step.TryGetProperty("duration", out var d)
                            ? d.GetInt32() : (int?)null;
                        save.SetWeather(step.GetProperty("kind").GetString()!,
                                        duration);
                        Check(save.Weather().Kind
                                  == want.GetProperty("kind").GetString(),
                              $"{label}: read-back");
                        break;
                    }
                    case "set_day_time":
                    {
                        string? dimension = step.TryGetProperty("dimension", out var dim)
                            ? dim.GetString() : null;
                        var time = step.GetProperty("time");
                        long total = time.ValueKind == JsonValueKind.String
                            ? save.SetDayTime(time.GetString()!, dimension)
                            : save.SetDayTime(time.GetInt64(), dimension);
                        Check(total == long.Parse(
                                  want.GetProperty("total_ticks").GetString()!),
                              $"{label}: returned total");
                        break;
                    }
                    case "set_spawn":
                    {
                        var pos = step.GetProperty("pos");
                        double? yaw = step.TryGetProperty("yaw", out var y)
                            ? y.GetDouble() : (double?)null;
                        save.SetSpawnPosition(pos[0].GetInt32(), pos[1].GetInt32(),
                                              pos[2].GetInt32(), yaw: yaw);
                        var spawn = save.SpawnPosition();
                        var wantSpawn = want.GetProperty("spawn");
                        Check(spawn.X == wantSpawn[0].GetInt32()
                              && spawn.Y == wantSpawn[1].GetInt32()
                              && spawn.Z == wantSpawn[2].GetInt32(),
                              $"{label}: read-back");
                        break;
                    }
                    case "world_border":
                        CheckBorder($"{label}: read-back",
                                    want.GetProperty("border"),
                                    step.TryGetProperty("dimension", out var wbDim)
                                        ? wbDim.GetString() : null);
                        break;
                    case "inject_old_border_keys":
                    {
                        var level = (TagCompound)save.Level!.Root!["Data"];
                        int injected = 0;
                        foreach (var field in step.GetProperty("fields")
                                                  .EnumerateArray())
                        {
                            level[field.GetProperty("name").GetString()!] =
                                BorderFieldTag(field);
                            injected++;
                        }
                        Check(injected == want.GetProperty("injected").GetInt32(),
                              $"{label}: injected count");
                        break;
                    }
                    case "write_world_border_file":
                    {
                        var data = new TagCompound();
                        foreach (var field in step.GetProperty("fields")
                                                  .EnumerateArray())
                            data[field.GetProperty("name").GetString()!] =
                                BorderFieldTag(field);
                        var borderRoot = new TagCompound { ["data"] = data };
                        var handle = new NbtFile
                        {
                            Root = borderRoot,
                            CompressionScheme = Compression.SchemeGzip,
                        };
                        string borderTarget = Path.Combine(
                            temp,
                            step.GetProperty("path").GetString()!
                                .Replace('/', Path.DirectorySeparatorChar));
                        Directory.CreateDirectory(
                            Path.GetDirectoryName(borderTarget)!);
                        handle.Save(borderTarget);
                        Check(want.GetProperty("written").GetBoolean(),
                              $"{label}: written");
                        break;
                    }
                    default:
                        Check(false, $"{label}: unknown op");
                        break;
                }
                stepIndex++;
            }

            CheckSnapshot("reads_after", root.GetProperty("reads_after"));

            // The bytes: write everything back, reload each touched file
            // fresh from disk, digest the uncompressed encoding.
            save.Save(force: true);
            var digests = root.GetProperty("payload_sha256");
            var files = root.GetProperty("payload_files").EnumerateObject()
                .Select(entry => (
                    Key: entry.Name,
                    Relative: entry.Value.GetString()!
                        .Replace('/', Path.DirectorySeparatorChar)))
                .ToArray();
            foreach (var (key, relative) in files)
            {
                var reloaded = NbtFile.Load(Path.Combine(temp, relative));
                Check(Sha256Hex(Codec.Encode(reloaded.Root!, reloaded.RootName))
                          == digests.GetProperty(key).GetString(),
                      $"{key}: payload digest after save and reload");
            }
        }
        finally
        {
            Directory.Delete(temp, recursive: true);
        }
    }

    /// <summary>The NBT tag a border field spec describes; bits win over
    /// the informational value, per the corpus conventions.</summary>
    private static Tag BorderFieldTag(JsonElement field) =>
        field.GetProperty("t").GetString() switch
        {
            "double" => new TagDouble(BitConverter.Int64BitsToDouble(
                (long)ulong.Parse(
                    field.GetProperty("bits").GetString()!,
                    System.Globalization.NumberStyles.HexNumber))),
            "long" => new TagLong(
                long.Parse(field.GetProperty("v").GetString()!)),
            "int" => new TagInt(field.GetProperty("v").GetInt32()),
            var kind => throw new InvalidOperationException(
                $"unknown border field type '{kind}'"),
        };

    // -- derived states ----------------------------------------------------

    private static readonly Dictionary<string, (int X, int Y, int Z)> NeighborOffsets =
        new Dictionary<string, (int, int, int)>(StringComparer.Ordinal)
        {
            ["north"] = (0, 0, -1), ["south"] = (0, 0, 1),
            ["west"] = (-1, 0, 0), ["east"] = (1, 0, 0),
            ["above"] = (0, 1, 0), ["below"] = (0, -1, 0),
        };

    private static void VerifyDerivedStates(string corpus)
    {
        using var doc = Load(corpus, "derived_states.json");
        foreach (var item in doc.RootElement.GetProperty("cases").EnumerateArray())
        {
            string name = item.GetProperty("name").GetString()!;
            var center = BlockState.Parse(item.GetProperty("center").GetString()!);

            // Everything the case does not name is air, exactly as the
            // corpus states — the lookup never reports "unloaded" here.
            var states = new Dictionary<(int, int, int), BlockState>
            {
                [(0, 0, 0)] = center,
            };
            foreach (var neighbor in item.GetProperty("neighbors").EnumerateObject())
            {
                if (neighbor.Value.ValueKind == JsonValueKind.Null)
                    continue;
                states[NeighborOffsets[neighbor.Name]] =
                    BlockState.Parse(neighbor.Value.GetString()!);
            }

            BlockState? BlockAt(int x, int y, int z) =>
                states.TryGetValue((x, y, z), out var found)
                    ? found : new BlockState("minecraft:air");

            var derived = Connect.DerivedState(center, BlockAt, 0, 0, 0);
            string actual = (derived ?? center).ToString();
            string expected = item.GetProperty("expected").GetString()!;
            Check(actual == expected,
                  $"derived state {name}: got {actual}, want {expected}");
        }
    }
}
