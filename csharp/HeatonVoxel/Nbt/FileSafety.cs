// Twin of heaton_voxel/nbt.py (filesystem safety), plus the C#-only
// WorldLock. Atomic writes, the is-the-world-open probe, and holding the
// game's own session lock. Locking is per-platform by nature: Minecraft
// holds a Java FileLock on session.lock — POSIX record locks on
// macOS/Linux, LockFileEx on Windows — and FileStream.Lock maps onto the
// right primitive under Mono (the runtime the Unity app ships on) and
// under CoreCLR on Windows. Under CoreCLR on macOS, Lock/Unlock throw
// PlatformNotSupportedException (verified empirically, .NET 10), so there
// the probe reports "not open" and WorldLock reports Unsupported: the
// conformance runner cannot see or hold locks on a Mac, the shipped app
// can. See PORTING.md, "Platform and runtime notes".

using System;
using System.Collections.Generic;
using System.IO;

namespace HeatonVoxel.Nbt
{
    public static class FileSafety
    {
        public const string SessionLock = "session.lock";

        /// <summary>Write data to a path without ever leaving it
        /// half-written: sibling temp file, flush to disk, rename over. A
        /// plain truncating write would destroy a multi-megabyte region on
        /// a crash with no copy anywhere.</summary>
        public static void AtomicWrite(string path, byte[] data)
        {
            string directory = Path.GetDirectoryName(Path.GetFullPath(path))!;
            string temp = Path.Combine(directory, $".nbt-{Guid.NewGuid():N}.tmp");
            try
            {
                using (var stream = new FileStream(
                           temp, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    stream.Write(data, 0, data.Length);
                    stream.Flush(flushToDisk: true);
                }
                // netstandard2.1 has no overwriting Move; Replace handles an
                // existing destination atomically, Move a fresh one.
                if (File.Exists(path))
                    File.Replace(temp, path, destinationBackupFileName: null);
                else
                    File.Move(temp, path);
            }
            catch
            {
                try { File.Delete(temp); } catch (IOException) { }
                throw;
            }
        }

        /// <summary>Walk up from a file to the save directory holding
        /// session.lock, or null.</summary>
        public static string? FindWorldRoot(string path)
        {
            string? directory = Path.GetDirectoryName(Path.GetFullPath(path));
            while (directory != null)
            {
                if (File.Exists(Path.Combine(directory, SessionLock)))
                    return directory;
                directory = Path.GetDirectoryName(directory);
            }
            return null;
        }

        /// <summary>True if something holds the world's session.lock — a
        /// running Minecraft, whose own chunk cache will clobber or corrupt
        /// anything written underneath it. Best-effort: on a platform where
        /// the probe cannot be made, this reports false and the caller's
        /// writes proceed at the user's risk.</summary>
        public static bool WorldIsOpen(string worldRoot)
        {
            // If WE hold the game's lock, the world cannot be open in
            // Minecraft — and this check must come before touching the file.
            // POSIX drops every record lock a process holds on a file the
            // moment ANY handle to it closes, so probing underneath our own
            // WorldLock would silently release it; on Windows the probe
            // would read our own region lock as "open" and make Save()
            // refuse the very session the lock protects.
            if (WorldLock.HeldByThisProcess(worldRoot))
                return false;

            string lockPath = Path.Combine(worldRoot, SessionLock);
            if (!File.Exists(lockPath))
                return false;
            try
            {
                using var stream = new FileStream(
                    lockPath, FileMode.Open, FileAccess.ReadWrite, FileShare.ReadWrite);
                try
                {
#pragma warning disable CA1416 // Lock maps to fcntl/LockFileEx per platform
                    stream.Lock(0, long.MaxValue);
                    stream.Unlock(0, long.MaxValue);
#pragma warning restore CA1416
                    return false;
                }
                catch (IOException)
                {
                    return true;  // the lock is held: the world is open
                }
            }
            catch (PlatformNotSupportedException)
            {
                return false;
            }
            catch (IOException)
            {
                // could not even open the lock file for probing; on Windows a
                // running game holds it exclusively, which answers the question
                return true;
            }
            catch (UnauthorizedAccessException)
            {
                return false;
            }
        }
    }

    /// <summary>What <see cref="WorldLock.TryAcquire"/> found.</summary>
    public enum WorldLockOutcome
    {
        /// <summary>The lock is held; Minecraft will refuse to open the
        /// world until it is disposed.</summary>
        Acquired,
        /// <summary>Someone else — a running game — holds it.</summary>
        HeldElsewhere,
        /// <summary>This runtime cannot hold file locks (CoreCLR on macOS);
        /// the caller proceeds unprotected and should say so.</summary>
        Unsupported,
    }

    /// <summary>The game's own world lock, held by this process.
    ///
    /// Minecraft keeps a FileLock on session.lock while a world is loaded
    /// and refuses, with its own "already locked" screen, to open a world
    /// whose lock someone else holds. Holding it here therefore excludes
    /// the game for as long as an edit session lives — the protection
    /// <see cref="FileSafety.WorldIsOpen"/> can only probe for, made
    /// mutual. OS advisory locks die with the process, so a crash never
    /// leaves a stale lock behind.
    ///
    /// One hold per world per process, enforced: POSIX record locks belong
    /// to the process and evaporate when ANY file handle to session.lock
    /// closes, so a second handle would silently drop the first. For the
    /// same reason nothing else in this library may open session.lock
    /// while a hold exists — <see cref="FileSafety.WorldIsOpen"/> checks
    /// <see cref="HeldByThisProcess"/> before touching the file.</summary>
    public sealed class WorldLock : IDisposable
    {
        private static readonly object Guard = new object();
        private static readonly HashSet<string> Held =
            new HashSet<string>(StringComparer.Ordinal);

        private FileStream? _stream;

        /// <summary>The save directory, normalized to a full path.</summary>
        public string WorldRoot { get; }

        private WorldLock(string worldRoot, FileStream stream)
        {
            WorldRoot = worldRoot;
            _stream = stream;
        }

        /// <summary>Try to hold the world's session lock. On
        /// <see cref="WorldLockOutcome.Acquired"/>, <paramref name="held"/>
        /// carries the lock; dispose it to let the game back in. Asking
        /// twice for the same world without disposing first is a
        /// programming error and throws, because the second handle would
        /// drop the first's lock on POSIX.</summary>
        public static WorldLockOutcome TryAcquire(string worldRoot,
                                                  out WorldLock? held)
        {
            held = null;
            string root = Normalize(worldRoot);
            if (!Directory.Exists(root))
                throw new DirectoryNotFoundException(
                    $"not a save directory: {root}");
            lock (Guard)
            {
                if (Held.Contains(root))
                    throw new InvalidOperationException(
                        $"this process already holds the session lock for "
                        + $"{root}; a second handle would drop the first "
                        + "when closed (POSIX record-lock semantics)");
            }

            string lockPath = Path.Combine(root, FileSafety.SessionLock);
            FileStream? stream = null;
            try
            {
                bool fresh = !File.Exists(lockPath);
                stream = new FileStream(lockPath, FileMode.OpenOrCreate,
                                        FileAccess.ReadWrite,
                                        FileShare.ReadWrite);
                if (fresh)
                {
                    // the snowman Minecraft itself writes, so the file
                    // looks native to the game and to other tools
                    var snowman = new byte[] { 0xE2, 0x98, 0x83 };
                    stream.Write(snowman, 0, snowman.Length);
                    stream.Flush();
                }
#pragma warning disable CA1416 // fcntl under Mono/POSIX, LockFileEx on Windows
                stream.Lock(0, long.MaxValue);
#pragma warning restore CA1416
            }
            catch (PlatformNotSupportedException)
            {
                stream?.Dispose();
                return WorldLockOutcome.Unsupported;
            }
            catch (IOException)
            {
                // the lock (or, on Windows, the file itself) is held
                stream?.Dispose();
                return WorldLockOutcome.HeldElsewhere;
            }
            catch (UnauthorizedAccessException)
            {
                stream?.Dispose();
                return WorldLockOutcome.Unsupported;
            }

            lock (Guard)
                Held.Add(root);
            held = new WorldLock(root, stream);
            return WorldLockOutcome.Acquired;
        }

        /// <summary>Does this process hold the lock for that world? The
        /// probe consults this before opening session.lock — see the class
        /// remarks for why it must.</summary>
        public static bool HeldByThisProcess(string worldRoot)
        {
            string root = Normalize(worldRoot);
            lock (Guard)
                return Held.Contains(root);
        }

        public void Dispose()
        {
            var stream = _stream;
            if (stream == null)
                return;
            _stream = null;
            try
            {
#pragma warning disable CA1416
                stream.Unlock(0, long.MaxValue);
#pragma warning restore CA1416
            }
            catch (IOException) { }
            catch (PlatformNotSupportedException) { }
            stream.Dispose();
            lock (Guard)
                Held.Remove(WorldRoot);
        }

        private static string Normalize(string worldRoot)
        {
            // netstandard2.1 has no Path.TrimEndingDirectorySeparator
            string full = Path.GetFullPath(worldRoot);
            string trimmed = full.TrimEnd(Path.DirectorySeparatorChar,
                                          Path.AltDirectorySeparatorChar);
            return trimmed.Length == 0 ? full : trimmed;
        }
    }
}
