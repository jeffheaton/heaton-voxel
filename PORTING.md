# Porting heaton-voxel (C# / Unity notes)

This Python codebase is the **reference implementation**: its behavior has
been validated byte-for-byte against real game output, and its test
knowledge is exported as a language-neutral corpus under
[`fixtures/conformance/`](fixtures/conformance/). A port should treat that
corpus as its acceptance suite from day one, and treat this document as the
list of decisions and traps that are cheaper to read than to rediscover.

## Scope: what to port, what not to

Port, in dependency order:

| module | why |
| --- | --- |
| `nbt` | the NBT codec and Anvil region container — everything stands on it |
| `blocks` | paletted containers, bit packing, heightmaps, light, `BlockState` in both palette codecs — legacy `Name`/`Properties`, and from 26.3 bare ids beside `id`/`properties` — with the default-state table the newer one decodes against; and Minecraft 1.13 to 1.17 chunks, read-only, with the generated table naming their biome numbers |
| `world` | `MinecraftSave`: dimensions, global coordinates, fill, replace, census and search (`histogram`, `find_blocks`, `entities_in`), entities/POI plumbing, `reset_lighting`, `rebuild_heightmaps` |
| `schem` | `.schem` read/write, copy/paste, rotate/flip |
| `connect` + `place` | derived states and item-placement semantics — a paste or edit without these leaves visibly wrong blocks |

Do **not** port `heaton_voxel.craft` — the judged tier, everything under
`heaton_voxel/craft/` (`craft/build/` and every family in it, the
basins, planting, approaches and extrusions of `craft/build/water.py`,
`craft/build/planting.py`, `craft/build/approach.py` and
`craft/build/extrude.py` among them; `craft/survey.py`, its water model
-- `water_body`, `contained`, `starved` -- included; `craft/render.py`,
`craft/colors.py`, `craft/light.py`, `craft/worksite.py`, `craft/biomes.py`,
`craft/cli.py`, `craft/blockinfo.py`, `craft/errors.py`, `craft/_plan.py`,
`craft/geometry.py`, `craft/noise.py`, `craft/field.py`, `craft/style.py`,
and the game tables generated for it,
`craft/block_facts.py` -- the water flow facts and `holds_water` with the
rest -- and `craft/block_registry.py`) is outside the port
**by definition, not omission**. The
port's scope IS the deterministic tier: every question it answers has one
correct answer and the corpus checks both languages give it bit-for-bit.
`craft` is game knowledge and taste — palettes, roofs, road mixes, block
colors, what counts as *paving* rather than natural stone — where answers
are choices, and choices get renders and judgment, not corpus artifacts.
The line runs through closely related pairs, which is where it is worth
being careful: `find_blocks` is deterministic (exactly these names, in
exactly this order) and is ported; `craft.survey.classify`, which decides
that a cobbled square reads as *built*, is a judgment and is not. It stays Python-only as policy;
`tests/test_architecture.py` enforces that the deterministic tier never
imports it and the corpus generator never touches it. (Also: the app *is*
the renderer, and textures, block models and opacity tables belong to the
app's asset pipeline, not the library.)

## Keeping the twins aligned

The two codebases are meant to stay file-for-file mirrors, and the rules
that make that hold are:

1. **Python is the reference.** Behavior changes land here first, the
   conformance corpus is regenerated (`python fixtures/make_conformance.py`
   — the drift test forces the diff to be deliberate), and the C# side
   ports the diff with the corpus as its gate. Never the other direction.
2. **The file map below is the contract.** A change that moves code between
   files happens in both languages or not at all.
3. **Member names match modulo casing** (`get_block` ↔ `GetBlock`,
   `from_disk` ↔ `FromDisk`); no renames-in-translation.
4. **No single-language core features.** C#-side conveniences (async
   wrappers, Unity types) live in the app layer above the port, never
   inside it.

`world` and `schem` are packages split one-concern-per-file precisely so
this map is one-to-one; `MinecraftSave`'s mixins are Python's spelling of
C# partial classes.

| Python | C# |
| --- | --- |
| `nbt.py` | `Nbt/` — `Tags.cs`, `ModifiedUtf8.cs`, `Codec.cs`, `RegionFile.cs` (`Chunk` + `NBT`), `FileSafety.cs` (one module ↔ one folder) |
| `snbt.py` | `Nbt/Snbt.cs` — class `Snbt`, `parse_snbt` ↔ `ParseSnbt` and `to_snbt` ↔ `ToSnbt`. Floats are rounded and printed in exact arithmetic in both, `Fraction` there and `BigInteger` here, never through the runtime's own parse or format, so Unity's Mono gives the IEEE-754 answer too; the containers are read on an explicit stack in both, so 512 levels cost no frames; and the three departures (`\N{...}`, nesting past 512, a non-ASCII `uuid()` digit) are refusals in both |
| `blocks.py` | `Blocks/` — `BlockState.cs` (with `PropertyMap`, the insertion-ordered dict the palette bytes depend on), `Packing.cs`, `PalettedContainer.cs`, `Section.cs`, `ChunkBlocks.cs` (class `Blocks` renamed: it cannot share the `Blocks` namespace's name), and `BlockDefaults.generated.cs`, the twin of `block_defaults.py` — both are **generated** by `tools/generate_block_defaults.py` from the game's own block report in one run, so regenerate them together and never edit either |
| `world/results.py` | `World/Results.cs` (result classes; mutable counters, so plain classes rather than records) |
| `world/dimension.py` | `World/Dimension.cs` |
| `world/save.py` | `World/Save.cs` |
| `world/player.py` | `World/Save.Player.cs` (partial) |
| `world/settings.py` | `World/Save.Settings.cs` (partial) |
| `world/history.py` | `World/Save.History.cs` (partial + `SaveEdit`) |
| `world/editing.py` | `World/Save.Editing.cs` (partial) |
| `world/aftercare.py` | `World/Save.Aftercare.cs` (partial) |
| `world/interchange.py` | `World/Save.Interchange.cs` (partial) |
| `world/_state.py` | no twin: it declares, for Python's type checker alone, the state the mixins share, which C#'s partial classes share by being one class |
| `world/cli.py` | not ported |
| `schem/support.py` | `Schem/Support.cs` |
| `schem/results.py` | `Schem/Results.cs` |
| `schem/transforms.py` | `Schem/Transforms.cs` (tables generated from `tables.json`). Three of its functions are public in both twins under one name modulo casing — `properties_rotated_cw` ↔ `PropertiesRotatedCw`, `properties_mirrored` ↔ `PropertiesMirrored`, `directional_leftovers` ↔ `DirectionalLeftovers` — for turning a state with no schematic around it; the judged tier's `craft/geometry.py` `Frame` turns states through them, and stays Python-only |
| `schem/schematic.py` | `Schem/Schematic.cs` |
| `schem/structure.py` | `Schem/Structure.cs` — `read_structure` ↔ `ReadStructure`, `structure_root` ↔ `StructureRoot`, `full_state_tag` ↔ `FullStateTag` |
| `schem/litematic.py` | `Schem/Litematic.cs` — `read_litematic` ↔ `ReadLitematic`, `litematic_root` ↔ `LitematicRoot`, `pack_tight`/`unpack_tight` ↔ `PackTight`/`UnpackTight` (cells straddle longs, as a chunk's never do) |
| `schem/copy.py` | `Schem/Copy.cs` |
| `schem/cli.py` | not ported |
| `connect.py` | `Connect.cs` |
| `version.py` | `Version.cs` — class `HeatonVoxelVersion`, `VERSION`/`BUILD_DATE`/`BUILD` ↔ `Version`/`BuildDate`/`Build`. Both are written afresh by the build workflows (`build-lib.yml`, `build-lib-dotnet.yml`), which stamp the date and run number; a checkout carries build 0 |
| `heightmap_blocks.py` | `Blocks/HeightmapBlocks.generated.cs` — what the game's heightmaps count; both are **generated** by `tools/generate_heightmap_blocks.py` in one run, from what `tools/probe_block_shapes.py` read out of the jar, so regenerate them together and never edit either |
| `wall_cover.py` | `WallCover.generated.cs` — what every block state's underside shows a wall below it; both are **generated** by `tools/generate_wall_cover.py` from the game's own shapes in one run, so regenerate them together and never edit either |
| `place.py` | `Place.cs` |

## Principles the port must keep

- **Byte-exact round-tripping is the core promise.** `load()` then `save()`
  of untouched data reproduces the input byte for byte. This is achieved by
  **re-emitting original bytes** for anything unmodified (per chunk, per
  file), never by hoping a re-encode matches. Keep that design — it is also
  the only way the promise survives a language change (see Compression).
- **Change detection is a content hash, not dirty flags.** Serialize and
  compare digests; flags at the editing entry points miss edits made through
  the raw tree and silently drop work.
- **`None`/absent means "nothing there"; an exception means "something is
  there that cannot be read".** Never let the two collapse: an ungenerated
  chunk and an unreadable one mean opposite things to a user, and every
  result object here reports them separately.
- **Refuse rather than damage.** DataVersion < 2825 (pre-1.18) schematics,
  and chunks older than 1.13 or from the 1.18 snapshots in between, are
  refused with a message, not guessed at. Legacy `.schematic` (numeric ids)
  is a different format — refuse it by name.
- **A 1.17 chunk is read and never written.** DataVersion 2724..2730
  (`ChunkBlocks.LegacyMinDataVersion`..`LegacyMaxDataVersion`): everything
  under `Level`, `Sections` with `Palette` and `BlockStates` (the same
  no-straddle packing), y 0..255 whatever is stored, a section of the world
  it left out read as one entry of air, and the biomes one 1,024-entry int
  array, each entry's low byte a number named by `LegacyBiomes.ByNumber`
  (`LegacyBiomes.generated.cs`, generated with `legacy_biomes.py` by
  `tools/probe_legacy_chunks.py` from the game's own upgrade) and built into
  per-section containers in first-seen order. Every container is a
  read-only view, and every write throws `ReadOnlyChunkException` (Python's
  `ReadOnlyChunk`, an `NBTError`): the accessor's own mutators, and every
  world writer through `Dimension.WritableBlocks`, which reports or refuses
  the chunk the way it always reported one it could not read.
  `legacy.json` pins all of it, over the fixture in `fixtures/legacy/`.
- **So is a chunk 1.13 to 1.16 saved**, from DataVersion 1519
  (`LegacyMinDataVersion`), with three differences at the schemas of the
  game's own fixes: below `LegacyAlignedDataVersion` (2527) a section's
  `BlockStates` and each heightmap run on across longs, as many bits a cell
  as fill the array exactly (`Packing.UnpackRunOn`, the block indices
  repacked once into the no-straddle form the containers read); below
  `LegacyCellBiomesDataVersion` (2202) `Biomes` is 256 entries, one a
  column, and the 4x4x4 cell at `(x, z)` takes column
  `(z*4 + 2)*16 + x*4 + 2` at every height; and `Finished` takes
  `postprocessed` or `fullchunk` below 1905 and `fullchunk` below 1911
  (`LegacyFinished`). Below `LegacyEntityStorageDataVersion` (2681)
  `LegacyEntities` is the chunk's own `Level.Entities`, and `EntitiesIn`
  reads it where the dimension files no entity chunk. `LegacyRelease`
  names the release a refusal cites. `older_chunks.json` pins it over the
  chunks of `fixtures/legacy/1.15.2/`, `fixtures/legacy/1.16.5-end/` and
  those `fixtures/legacy/older.nbt` carries.
- **A save in the layout before 26.1 is read and never written.** No
  `dimensions/` tree but `region/`, `DIM-1/region` or `DIM1/region`:
  `MinecraftSave.Layout` is `"old"`, `ReadOnly` names why, and every
  `Dimension` carries the reason and throws `ReadOnlySaveException` (a
  `ReadOnlyChunkException`) from `RefuseReadOnly`, which `WritableBlocks`,
  every file or chunk it would make, `CreateChunks`, `DeleteChunks`,
  `SpawnEntity`, `InvalidatePoi`, `Save` and every setter ask first. The
  settings and the player read the old `level.dat` as the game's
  `FileFixerUpper` would move it: `Difficulty` 0/1/3 peaceful/easy/hard and
  anything else normal, the weather from `raining`, `rainTime`,
  `thunderTime` and `clearWeatherTime`, one clock (`DayTime`, the
  overworld's), the spawn from `SpawnX`..`SpawnZ` or `spawn`, every
  dimension's border the one `level.dat` holds (flat `Border*` or the
  `world_border` compound) with the nether's center divided by 8, and the
  player `playerdata/<uuid>.dat` of `level.dat`'s `Player`, else that
  `Player`, else the first of `playerdata/`. A numeric `Dimension` reads as
  `LegacyDimensionIdFix` does: -1 the nether, 1 the End, any other the
  overworld. Both layouts at once, or an old one under a `level.dat` of
  4772 or later, are refused. `old_layout.json` pins it over the worlds of
  `fixtures/old_layout.json`.
- **Laziness is layered.** Opening a save indexes files without reading
  them; a region holds compressed chunk bytes until `.data` is touched;
  release flushes edits back to bytes. This is what makes a 400-region world
  open instantly in a viewer.
- **Writes are atomic**: temp file, fsync, rename. A plain truncating write
  can destroy a multi-megabyte region on a crash.

## The C# tag model

Python's tags subclass their value types (`TagInt(int)`, `TagString(str)`,
`TagCompound(dict)`); C# cannot subclass sealed built-ins, so the port is a
re-expression, not a transliteration. Recommended shape: scalars, strings
and arrays as thin wrappers carrying a `TagId` and a `Value` (plus a
nullable `RawBytes` field replacing Python's dynamic `_raw` attribute — a
cleaner home for it anyway); `TagList` may extend `List<Tag>` and carries
its declared `ElementType`. **`TagCompound` must be an insertion-ordered
map** — see the first trap below; `Dictionary<K,V>` does not qualify.

## Binary format traps (each one was a real bug or refusal)

- **Compound key order is data.** Byte-exact round-tripping requires a
  compound's children to re-encode in file order. `Dictionary<K,V>`'s
  enumeration order is unspecified — use an insertion-ordered map (a
  list-backed dictionary), or every NBT conformance case fails and, worse,
  nearly passes.
- **A written list's element type comes from its first item** when the list
  is non-empty; the declared type only survives for empty lists. Keep that
  rule, and reject mixed-type lists with a clear error as the reference
  does.
- **A 26.3 palette entry has three shapes, and the list's element type is
  part of the data.** From DataVersion 5006 (`ChunkBlocks.ModernStateDataVersion`)
  a state at its block's defaults is stored as the bare id — a plain
  string — and any other state as `{id, properties}` with *every* property
  present, in name order; a list holding both wraps each bare id as
  `{"": id}`, the key being the empty string, so the list stays one tag
  type. Decoding restores the omitted defaults from the generated table, so
  `water` reads as `water[level=0]` whichever codec wrote it, and encoding
  canonicalizes the same way, so the two spellings share one palette slot.
  The chunk's own DataVersion picks the codec, chunk by chunk, because a
  world played across the update holds both; and a decoded entry's source
  tag is re-emitted only for a legacy entry going back into a legacy chunk,
  never across codecs. `modern.json` and `block_defaults.json` pin all of
  it.
- **All string comparison and sorting is ordinal.** `BlockState` sorts
  property keys to build its identity and its string form; C#'s
  culture-aware `string.CompareTo` will disagree with the corpus in some
  locales. Use `StringComparer.Ordinal` everywhere, always.

- **Java modified UTF-8**, not UTF-8: `NUL` is `C0 80`; astral characters
  are CESU-8 surrogate pairs (two 3-byte sequences). .NET's UTF-8 codec will
  silently change the bytes. Conformance: `nbt_cases.json` /
  `modified-utf8`.
- **Non-canonical string bytes must survive.** An overlong encoding decodes
  fine but re-encoding normalizes it; keep the raw bytes when a decode →
  encode trip would not reproduce them (`non-canonical-string` case).
- **An empty `TAG_List`'s declared element type is data** (`empty-lists`).
- **NaN payloads are data**: `float32 → double → float32` loses them. Keep
  raw bytes when re-packing would differ; compare floats by bits, never by
  value (`nan-payloads`).
- **NBT longs are signed; packed fields are not.** Reading a bit field from
  a long with an arithmetic shift sign-extends phantom 1s into the field.
  Mask to the field width. `packing.json` includes an all-maximum case whose
  words have their sign bits set for exactly this reason.
- **Packed entries never straddle a word**: each 64-bit long holds
  `floor(64/bits)` entries from its low bits up; leftover high bits are
  padding. The array length, not the palette size, is the authority when a
  shrunken palette left a wider packing behind.
- **Floor, never truncate**: chunk/region indices come from arithmetic
  shift and mask (`x >> 4`, `x & 15`); C#'s integer division truncates
  toward zero and puts block x=-1 in the wrong chunk.
- **Region files keep junk that must survive**: slack bytes in a chunk's
  final sector, timestamps of chunks no longer present, non-zero unallocated
  sectors, zero-length `.mca` files (valid empty regions — leave them zero
  bytes). Oversized chunks live in `.mcc` sidecars.
- **Light is nibbles**: 2048 bytes per section, two 4-bit levels per byte,
  low nibble first, same y,z,x order as blocks (`nibbles.json`). An absent
  array means "the game will recompute", not darkness.

## Arithmetic that does not translate

Python floors integer division and gives `%` the divisor's sign; C#
truncates toward zero and gives `%` the dividend's sign. Every risky site
in the reference is listed here with the portable form — and the corpus
pins each behavior, so getting one wrong fails a vector rather than a
world:

| reference site | Python meaning | portable form |
| --- | --- | --- |
| `-(-n // d)` (sector counts in `nbt`, bit-width inference in `blocks`) | ceiling division | `(n + d - 1) / d` |
| `(degrees // 90) % 4` (`Schematic.rotated`; `rotated(-90) ≡ rotated(270)`) | floored modulo | `((d / 90) % 4 + 4) % 4` |
| `(base - r) % 16` (sign `rotation` under mirror) | floored modulo | `((v % 16) + 16) % 16` |
| `((yaw + 180) % 360) - 180` (entity yaw wrap, **float** `%`) | result follows divisor sign | `((v % 360) + 540) % 360 - 180` |
| bit packing/unpacking | unbounded ints, mask after shift | do all field math in `ulong`; never shift signed words |
| chunk/region coordinates | `>> 4`, `& 15` floor for negatives | arithmetic shift and mask translate directly — never use `/ 16` |

One simplification runs the other way: modified UTF-8 encodes UTF-16 code
units, which is exactly what a C# `char` is — the encoder iterates `char`s
directly and skips Python's surrogate-pair recombination. Just never touch
`Encoding.UTF8`. And Python's `bool ⊂ int` guard in the entity transform
has no C# equivalent to worry about.

## Compression is not portable — design around it

zlib output differs between implementations and versions: .NET's deflate
will not reproduce CPython's, and neither reproduces the game's exactly.
Therefore:

- Byte-exact round trips of **untouched** data work in any language *only*
  because original compressed bytes are reused. Keep that mechanism.
- Any digest of NBT in the conformance corpus is of the **uncompressed**
  encoding. Never compare compressed bytes across implementations.
- The `.schem` goldens are readable by any port, but a writer verifies
  itself by **re-reading its own output** and checking the uncompressed
  payload digest — not by matching the golden's file bytes.
- When re-compressing modified chunks, use deflate level 6 (what the game
  writes); the exact bytes may differ from Python's and that is fine.
- **Unity's .NET profile has no `ZLibStream`** — only `GZipStream` and raw
  `DeflateStream` — and region chunks are zlib (RFC 1950). Budget a small
  wrapper: read/verify the 2-byte header and trailing Adler-32 around a
  `DeflateStream`, and emit them when writing (`0x78 0x9C` is the
  conventional level-6 header). Discover this here, not at runtime.

## Platform and runtime notes

- **`world_is_open` is per-platform by nature.** Minecraft holds a Java
  `FileLock` on `session.lock`: POSIX record locks on macOS/Linux (which is
  why the reference probes with `lockf`, not `flock` — separate lock
  spaces), `LockFileEx` on Windows (probe by attempting `FileStream.Lock`;
  the reference takes the file's first byte with `msvcrt.locking` there).
  Getting this wrong reports live worlds as free and invites torn reads.
  Caveat, verified empirically: **CoreCLR on macOS cannot lock at all** —
  `FileStream.Lock`/`Unlock` throw `PlatformNotSupportedException` there,
  so the probe is blind and `WorldLock` reports `Unsupported` under
  `dotnet` on a Mac. Under Mono (what Unity ships) `Lock` is fcntl and
  both work, and each twin sees the other's lock: a world one of them
  holds, the other reports as open.
- **`WorldLock` is C#-side only, deliberately.** The Unity editor app
  *holds* the game's session lock for the life of an edit session, so a
  running editor excludes the game and vice versa. The Python reference
  keeps only the probe: its established workflows read live worlds
  (surveys, renders) and its write pipelines are deterministic and
  re-runnable, so optimistic refuse-and-rebuild serves them better than
  exclusion. Two traps the implementation guards, worth knowing before
  touching it: POSIX drops **every** record lock a process holds on a file
  the moment **any** handle to it closes — which is why `WorldIsOpen`
  consults `WorldLock.HeldByThisProcess` before opening `session.lock`,
  and why acquiring the same world twice in-process throws instead of
  returning a second handle; and on Windows the un-short-circuited probe
  would read our own region lock as "world open" and make `Save()` refuse
  the very session the lock protects.
- **Atomic replace**: `File.Replace`/`MoveFileEx` on Windows,
  rename-over on POSIX; directory fsync after rename is a POSIX nicety
  with no Windows equivalent (skip it there). Never open-truncate a region.
- **Hashes are internal.** The reference digests with blake2b for change
  detection; nothing persists it, so the port may use any hash (SHA-256 is
  in every profile). Only the conformance corpus's SHA-256 digests are
  contractual.
- **Threading**: a save object and everything under it is single-threaded
  by design (shared caches, no locks) — one save, one thread. Parallelism
  belongs at the region level, where files are independent: a viewer loads
  regions on workers and hands results to the owning thread.
- **Hot paths should not throw.** The reference signals out-of-range with
  exceptions, which is idiomatic Python and expensive C#. Give the viewer
  `TryGet` variants for block/biome/light lookups and reserve exceptions
  for corruption; return `ReadOnlySpan`/`ReadOnlyMemory` from the bulk
  reads (`indices()`, light arrays) rather than copies.

## Game knowledge: consume the tables, don't re-type them

The rotation/mirror vocabulary, the fence/wall/pane connection rules, stair
corner chirality, hanging-entity facing bytes, the "sturdy face" and
waterloggable families — all of it was validated against vanilla-placed
blocks in a real save (**0 disagreements in 22,616**) and against real
shipwrecks, ruins and mineshafts. It is exported as data in
`conformance/tables.json`; generate C# tables from that file rather than
transcribing, so the two implementations cannot drift apart. Behavioral
vectors live beside it: `derived_states.json` (neighborhood → expected
state) and `transforms.json` (state × rotate/flip → expected state, facing
byte tables, geometry formulas with worked samples). The "sturdy face"
table is generated from the game (`tools/generate_connect_faces.py`, into
`heaton_voxel/connect_faces.py` and its C# twin) and held to it state by
state: `tables.json`'s `sturdy` gives each block's full sides as a mask, by
state where they turn on one, checked after the game's own connection
exceptions, `no_connection_exact` and `no_connection_suffixes`. A stair is
not in it: its `shape` is derived in the same sweep, so it is read by its
`facing` alone.

## Using the conformance corpus

Read `manifest.json` first — it states the encoding conventions (hex
binaries, 64-bit ints as decimal strings, floats by bits, non-ASCII strings
as codepoint arrays, SHA-256 digests) and digests every artifact. Then, per
artifact: decode the `nbt_cases.json` binaries and compare trees, re-encode
and compare bytes; pack/unpack `packing.json` (including the sign-bit case
and the wider-than-palette case, where the array length overrules the
palette size), `varints.json`, `nibbles.json`; load `../r.-1.0.mca` and
check `region.json`'s per-chunk uncompressed payload digests plus the
untouched byte-identical round trip, and `empty/r.0.0.mca` — a zero-byte
file that must load as a valid empty region and save back to zero bytes;
assemble the world as `world.json` describes and compare block, biome,
surface, light, block-entity and histogram samples; run `world_edits.json`
— whose `steps` are the operations themselves, as data, so a port executes
the reference's script rather than a transcription of it — and check the
reported counts, the blocks read back, the census over the thaw box **and
the per-chunk NBT digests**, which is the write path's equivalent of the
untouched round trip; check `block_defaults.json` entry by entry against
the port's generated default-state table — every block's properties in the
listed order, the table's size, and that an unlisted name yields none —
and `wall_cover.json` the same way against the port's generated wall-cover
table, every block and every combination of the properties that decide it;
check `heightmaps.json` the same way, its three block lists whole and the mask
of every state it lists, then, over each fixture it names, that every finished
chunk reads no map stale and rebuilds its `Heightmaps` tag unchanged, order
included, and replay its `scene`;
and run `modern.json` the way `world_edits.json` runs, over the 26.3
fixture in `../modern/`: its `chunks` say what each stored palette entry
looks like and what it decodes to, its `surfaces` read all four heightmaps
before any edit, its `steps` walk the codec's list transitions, and its
`palettes_after` and digests check the forms and the bytes the edits left
behind; run `chunk_deletes.json` over the same world plus the
two sidecar files it ships, and check the whole-file digests; load the
`.schem` goldens against `schem/expected.json`, including the pre-rotated
golden, and `sample.nbt` and `sample.litematic`, which a port also writes from
`sample_v2.schem` and must write to the same uncompressed bytes; replay `derived_states.json` and `transforms.json` — states, facing
bytes, geometry, the yaw-wrap vectors and the negative-rotation
equivalences — through the ported rules; and read every `snbt.json` string,
checking a refusal as a refusal (never its message), and otherwise the hex of
the root compound holding the tag under `v` — keys in the order read, so the
bytes, not just the tree — and the text written back, then write every one
of its binary tags and compare the text. Its strings are JSON strings but
for those holding a lone surrogate, which come as code points, since .NET's
JSON reader refuses an unpaired surrogate escape. Run `chunk_creation.json`'s
two scripts over copies of the two fixture worlds as their `assembly` says --
`create_chunks` with every outcome (built, left finished, left unfinished, a
new region file, a dimension never visited, a world stamped newer and one
older), a build over what it made -- and check each step's report, the blocks
and surfaces read back, and the status and uncompressed digest of every chunk
built and of the unfinished one beside them.

`world_edits.json` is the artifact that catches what the read samples
cannot: a fill that packs bits differently, a placement that lays a bed's
halves the other way round, a sweep whose *order* differs and so builds the
palette differently. Matching every state but not the digest means the port
is writing a different file that happens to read the same. A `place` step
whose `result` is `{"refused": true}` must raise the port's NBT error,
writing nothing: it places a block that would take water over water the
game does not have (`level=16`, `waterlogged=maybe`), and the blocks read
back show the cell untouched. Its rows at y=100 are what a placement
waterlogs into, which is the game's `getStateForPlacement` block by block
-- a source for nearly everything, falling water too for the corals and the
conduit, any water for the small dripleaf, none for the light block and a
double slab -- so every coral, every block that is water in all its states
and each exception has a row of its own.

`modern.json` is that artifact's 26.3 twin, over a fixture of its own
(`fixtures/modern/`, cut so every chunk carries DataVersion 5006 or later)
because the two codecs never share a chunk and the vendored region holds
only the older. Its script is chosen for the list transitions rather than
for the terrain: a default state joining a string list, a non-default one
widening it to compounds, a default one arriving wrapped in a list already
widened, and a spelled-out default finding the bare entry already there.
It also pins **where a chunk's range starts and ends**, which is not where
its stored sections do: the game keeps light one section past each end of
the world, so a chunk can store a section at Y=-5 or Y=20 holding light and
no blocks. `MinY` is `yPos * 16` (the lowest section carrying
`block_states` when there is no `yPos`) and `MaxY` the top of the highest
section carrying them. Chunk (-13, 19) stores the lower one: a port that
counts from the lowest stored section reads its `surfaces` 16 low and
rebuilds its heightmap 16 high, which the chunk's digest catches. A cell
outside `MinY..MaxY` is outside the chunk for `Get`, `Set` and the biome
accessors, even where such a section is stored — only the light reads
reach one — so its point read owes null, never air: the `reads` at y=-65
pin that below, and the read at (-478, 320, 3) above. The script's last
steps append the upper section to chunk (-30, 0) (`light_section`) and
expect a door on the top of the world to be refused whole rather than
written by its lower half, then take away that chunk's bottom section
(`drop_section`), so its floor is the `yPos` it records and not the
lowest section left. Those two are the steps that are not library calls.
`block_defaults.json` beside it dumps the whole default-state table, so a
port's generated copy is checked rather than trusted to have come from the
same report.

`chunk_deletes.json` is separate from it because a deleted chunk has no NBT
left to digest: its whole effect is the container around it, so that
artifact digests the region **files** — which it can, uniquely among the
write vectors, because deletion recompresses nothing. It is also the only
artifact that ships its own inputs (`chunk_deletes/entities/r.-1.0.mca` and
`chunk_deletes/poi/r.-1.0.mca`, uncompressed so their bytes are portable):
the vendored region has no entity or POI storage, and those two trees are
exactly what a port forgets, since nothing rebuilds them from the terrain.
Its reads *before* the script are load-bearing, not decoration — they leave
a decoded accessor behind, which is the only way the reads afterwards can
tell a deleted chunk from a cached one.

The Python suite regenerates this corpus on every test run
(`tests/test_conformance.py`), so it cannot silently drift from the
reference; a corpus change always appears as a reviewed diff.

## API shape for a viewer

The read path a mesher needs is already the library's shape — keep it:
per-section **palette + packed indices** (`section.blocks.palette`,
`.indices()`, biomes at 4×4×4, `section.block_light()` / `sky_light()`
returning per-cell levels or null), heightmaps as lookups, and
`Schematic.indices()` + `palette` for paste previews. Point lookups exist
for convenience; nothing in the app should iterate a chunk block-by-block
through them. Keep `missing` vs `unreadable` distinct all the way to the UI:
one renders as "beyond the world", the other as "cannot display this chunk",
and confusing them misleads the person holding the mouse.
