// Twin of world/history.py: reversible in-memory edits with lazy file capture.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using HeatonVoxel.Nbt;

namespace HeatonVoxel.World
{
    public sealed partial class MinecraftSave
    {
        internal SaveEdit? ActiveEdit;

        /// <summary>Begin a reversible edit. Complete or roll back before
        /// saving. Reacquire block accessors after beginning or restoring.
        /// The caller owns its world lock, history budget and stack order.</summary>
        public SaveEdit BeginEdit()
        {
            if (ActiveEdit != null)
                throw new NbtException("finish the active edit before beginning another");
            var record = new SaveEdit(this);
            ActiveEdit = record;
            return record;
        }

        internal void ClearBlockAccessors()
        {
            foreach (var dim in Dimensions.Values) dim.ClearBlockAccessors();
        }

        internal SortedDictionary<string, NbtFile> HistoryFiles()
        {
            var result = new SortedDictionary<string, NbtFile>(StringComparer.Ordinal);
            void Add(NbtFile? file)
            {
                if (file?.Path != null)
                    result[System.IO.Path.GetFullPath(file.Path)] = file;
            }
            Add(Level); Add(_player);
            foreach (var file in _settingsFiles.Values) Add(file);
            foreach (var dim in Dimensions.Values)
            {
                foreach (var file in dim.Regions.Values) Add(file);
                foreach (var file in dim.EntityRegions.Values) Add(file);
                foreach (var file in dim.PoiRegions.Values) Add(file);
            }
            return result;
        }
    }

    /// <summary>Undo/redo change memory only. Complete captures the original
    /// contents of files first opened during the operation, before Save can
    /// replace them. Payload arrays are shared until a chunk is re-encoded.</summary>
    public sealed class SaveEdit
    {
        private readonly MinecraftSave _save;
        private readonly Dictionary<string, NbtFile> _before = new();
        private readonly Dictionary<string, NbtFile> _after = new();
        private readonly Dictionary<string, NbtFile> _targets = new();
        private bool _complete;

        internal SaveEdit(MinecraftSave save)
        {
            _save = save;
            save.ClearBlockAccessors();
            foreach (var pair in save.HistoryFiles())
                _before[pair.Key] = pair.Value.HistoryCopy();
        }

        public bool Changed => _targets.Count > 0;
        public IReadOnlyList<string> Paths => _targets.Keys.OrderBy(path => path,
            StringComparer.Ordinal).ToArray();
        public long ByteSize => _before.Values.Concat(_after.Values).Sum(file =>
            file.Chunks.Values.Sum(chunk => (long)chunk.CompressedSize)
            + (file.Root == null ? 0 : Codec.Encode(file.Root, file.RootName).LongLength));

        private static bool Same(NbtFile a, NbtFile b)
        {
            if (a.Kind != b.Kind || a.RootName != b.RootName
                || a.CompressionScheme != b.CompressionScheme) return false;
            if (a.Kind != NbtKind.Region)
            {
                if (a.Root == null || b.Root == null) return a.Root == b.Root;
                return Codec.Encode(a.Root, a.RootName).AsSpan()
                    .SequenceEqual(Codec.Encode(b.Root, b.RootName));
            }
            if (a.Chunks.Count != b.Chunks.Count) return false;
            foreach (var pair in a.Chunks)
            {
                if (!b.Chunks.TryGetValue(pair.Key, out var other)) return false;
                var chunk = pair.Value;
                if (chunk.Timestamp != other.Timestamp
                    || chunk.CompressionScheme != other.CompressionScheme
                    || !chunk.Payload().AsSpan().SequenceEqual(other.Payload())) return false;
            }
            return true;
        }

        public void Complete()
        {
            if (_complete) return;
            _save.ClearBlockAccessors();
            foreach (var pair in _save.HistoryFiles())
            {
                string path = pair.Key;
                var file = pair.Value;
                if (!_before.ContainsKey(path))
                {
                    if (File.Exists(path)) _before[path] = NbtFile.Load(path).HistoryCopy();
                    else if (file.Kind == NbtKind.Region)
                    {
                        var empty = file.HistoryCopy();
                        empty.Chunks.Clear();
                        _before[path] = empty;
                    }
                    else throw new NbtException(
                        $"cannot capture original state of missing file '{path}'");
                }
                var after = file.HistoryCopy();
                if (!Same(_before[path], after))
                {
                    _targets[path] = file;
                    _after[path] = after;
                }
            }
            foreach (string path in _before.Keys.Except(_targets.Keys).ToArray())
                _before.Remove(path);
            _complete = true;
            _save.ActiveEdit = null;
        }

        private void Restore(Dictionary<string, NbtFile> states)
        {
            if (!_complete) throw new NbtException("complete the edit before restoring it");
            if (_save.ActiveEdit != null)
                throw new NbtException("finish the active edit before restoring history");
            _save.ClearBlockAccessors();
            foreach (var pair in states.OrderBy(pair => pair.Key, StringComparer.Ordinal))
                _targets[pair.Key].RestoreHistory(pair.Value);
        }

        public void Undo() => Restore(_before);
        public void Redo() => Restore(_after);
        public void Rollback() { Complete(); Undo(); }
    }
}
