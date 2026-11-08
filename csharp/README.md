# HeatonVoxel — the C# port

The C# twin of the Python reference implementation one directory up,
written to the contract in [PORTING.md](../PORTING.md): file-for-file
structure, matching member names modulo casing, Python-first changes, and
the conformance corpus as the acceptance gate.

- **`HeatonVoxel/`** — the library. Targets `netstandard2.1` with C# 9 so
  Unity can consume it directly, and has **zero package references**: the
  formats are parsed straight from the bytes, the same rule the Python
  library lives by. Region-chunk zlib is handled with a small RFC 1950
  wrapper over `DeflateStream` (header + Adler-32), since this profile has
  no `ZLibStream`.
- **`HeatonVoxel.Conformance/`** — the test harness, a dependency-free
  console app that verifies every artifact in `../fixtures/conformance/`
  and exits nonzero on any failure. Layers not yet ported are reported as
  `PEND`, never silently skipped.

```
dotnet build csharp/HeatonVoxel.slnx
dotnet run --project csharp/HeatonVoxel.Conformance
```

**All 1,950 conformance checks pass**; nothing is `PEND`.

## Consuming it from Unity

Unity takes the library as a built DLL. A Release build emits
`HeatonVoxel.dll`, `HeatonVoxel.pdb` and `HeatonVoxel.xml` (the
IntelliSense docs) under `HeatonVoxel/bin/Release/netstandard2.1/`:

```bash
dotnet build csharp/HeatonVoxel/HeatonVoxel.csproj -c Release
```

Drop the three files anywhere under a project's `Assets/` or into a UPM
package. Unity's defaults for a managed plugin -- every platform, the
editor included, and *Auto Reference* on -- are what it wants; the assembly
is named `HeatonVoxel`. Run the conformance suite first: green is what says
the DLL a project takes is the verified one.

## Status

| layer | twin of | state |
| --- | --- | --- |
| `Nbt/` (tags, modified UTF-8, codec, compression, region container, file safety) | `nbt.py` | **ported** — byte-identical round trip of the vendored region, all NBT vectors green |
| `Blocks/` (BlockState, containers, sections, chunk access, packing) | `blocks.py` | **ported** — all `world.json` block/biome/light/heightmap samples green |
| `World/` (Dimension, Save + Player/Editing/Aftercare partials, Results) | `world/` | **ported** — `world.json` and `world_edits.json` green, including byte-identical chunk NBT after a 54-step edit script, and `chunk_deletes.json`, which digests whole region files |
| `Connect.cs`, `Place.cs` | `connect.py`, `place.py` | **ported** — all 35 `derived_states.json` cases green |
| `Schem/` (Support, Transforms, Schematic, Copy, Results) + `World/Save.Interchange.cs` | `schem/`, `world/interchange.py` | **ported** — goldens and `transforms.json` green, including the pre-rotated golden end to end |

Sanctioned naming deviations, noted where they happen and nowhere else:

- Python's class `NBT` is `NbtFile` (C# cannot name a class after its own
  namespace).
- Python's class `Blocks` is `ChunkBlocks` (same collision, with the
  `HeatonVoxel.Blocks` namespace).
- Python's module-level `OVERWORLD`/`NETHER`/`END` constants live on a
  `DimensionNames` holder class (C# has no module scope, and the natural
  name `Dimensions` is taken by `MinecraftSave.Dimensions`).

Everything else keeps its Python name modulo casing — including members
that shadow a type name, like `Dimension.Chunk(x, z)`, which C# permits.

Not ported, by the scope rule in PORTING.md: `render` (the app is the
renderer), `build` (authoring tooling), and both `cli.py` modules.

Two seams are `public` here that are module-private in Python —
`Connect.DerivedState` and the `Transforms` vocabulary — for exactly the
reason Python's packages re-export them to `fixtures/make_conformance.py`:
the corpus verifies those rules directly, so what ships is what was checked.

When a chunk digest fails, set `HV_DUMP=<dir>` and the runner writes the
offending chunk's uncompressed NBT there, so it can be decoded and diffed
against the reference's tree rather than guessed at.
