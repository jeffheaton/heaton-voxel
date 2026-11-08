# Heaton Voxel

[![PyPI](https://img.shields.io/pypi/v/heaton-voxel?style=flat-square)](https://pypi.org/project/heaton-voxel/)
[![NuGet](https://img.shields.io/nuget/v/HeatonVoxel?style=flat-square)](https://www.nuget.org/packages/HeatonVoxel)
[![Python versions](https://img.shields.io/pypi/pyversions/heaton-voxel?style=flat-square)](https://pypi.org/project/heaton-voxel/)
[![Python tests](https://img.shields.io/github/actions/workflow/status/jeffheaton/heaton-voxel/test-lib.yml?branch=main&label=Python%20tests&style=flat-square)](https://github.com/jeffheaton/heaton-voxel/actions/workflows/test-lib.yml)
[![Python coverage](https://img.shields.io/codecov/c/github/jeffheaton/heaton-voxel/main?flag=python&label=Python%20coverage&style=flat-square)](https://app.codecov.io/gh/jeffheaton/heaton-voxel)
[![.NET tests](https://img.shields.io/github/actions/workflow/status/jeffheaton/heaton-voxel/test-lib-dotnet.yml?branch=main&label=.NET%20tests&style=flat-square)](https://github.com/jeffheaton/heaton-voxel/actions/workflows/test-lib-dotnet.yml)
[![.NET coverage](https://img.shields.io/codecov/c/github/jeffheaton/heaton-voxel/main?flag=dotnet&label=.NET%20coverage&style=flat-square)](https://app.codecov.io/gh/jeffheaton/heaton-voxel)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square)](https://github.com/jeffheaton/heaton-voxel/blob/main/LICENSE)

**Read and edit Minecraft Java Edition worlds from Python, or from C# and
Unity.**

Open a save, read any block, change it, and write the world back the way the
game would have made it: a door placed as two halves, a fence joined to its
neighbors, a stair set in water waterlogged. Move builds in and out as
WorldEdit `.schem`, Litematica `.litematic` and vanilla structure files;
change the game mode, difficulty, weather and time of day; move the player;
undo an edit. Everything you didn't touch is written back byte for byte, and
saving refuses while Minecraft has the world open.

```python
from heaton_voxel import MinecraftSave

save = MinecraftSave.load("saves/MyWorld")
x, _, z = save.player_block_position()
ground = save.surface_y(x, z)
print(save.get_block(x, ground, z))          # minecraft:grass_block[snowy=false]

save.place_block(x, ground + 1, z, "minecraft:oak_fence")   # joins the fences beside it
save.reset_lighting((x, ground, z), (x, ground + 1, z))
save.rebuild_heightmaps((x, ground, z), (x, ground + 1, z))
save.save()
```

```
pip install heaton-voxel          # Python 3.10+, no dependencies
dotnet add package HeatonVoxel    # .NET Standard 2.1, so Unity too
```

The C# package is the Python one's twin, and one shared test corpus holds them
to the same answers, byte for byte. Python adds two things the C# leaves out:
pictures -- maps, floor plans and cutaways of any area -- and a kit for
building at scale (terrain, roads, roofs, stairs, lighting, whole towers) that
refuses a plan the game would undo, a torch with nothing to hang on or water
that would run out of its basin, before it writes a block.

It reads worlds as far back as Minecraft 1.13 and writes the current format,
1.18 and later. Java Edition only: Bedrock worlds are a different format.

## How it's organized

The package has two halves. The lower one edits worlds, and every question
it answers has one right answer -- which bytes encode this chunk, which state
a placed block takes -- so it has a C# twin that agrees with it byte for
byte. It is four modules: `heaton_voxel.nbt` reads and writes the files,
`heaton_voxel.blocks` a chunk's blocks, biomes, heightmaps and light,
`heaton_voxel.world` a whole save, and `heaton_voxel.schem` moves boxes of
world in and out as `.schem`, `.litematic` and structure files. Their names
are re-exported from `heaton_voxel`, so `from heaton_voxel import
MinecraftSave` is enough for most programs; the examples below import from
the submodules to show where each name lives.

The upper half, `heaton_voxel.craft`, is for building, where the answers are
choices rather than facts: which stone a road is paved in, how steep a roof
rises. `craft.build` lays terrain, roads, walls, roofs, stairs and lighting,
and checks each plan against the game's rules before it writes;
`craft.render` draws maps, floor plans and cutaways; and `craft.worksite`
snapshots every file a build can touch before the first edit, trips on any
change inside a protected area, and can put everything back as it was. It is
Python only, and it is imported by name (`from heaton_voxel.craft import
Builder`), so a script that uses it says so.

| document | for |
| --- | --- |
| this README | what it does and why it works that way |
| [API.md](https://github.com/jeffheaton/heaton-voxel/blob/main/API.md) | the contract to write an example or tool against |
| [PORTING.md](https://github.com/jeffheaton/heaton-voxel/blob/main/PORTING.md) | porting to another language, and the conformance corpus under `fixtures/conformance/` |
| [CLAUDE.md](https://github.com/jeffheaton/heaton-voxel/blob/main/CLAUDE.md) | project rules, verification, and traps already hit |

## The file formats directly

Under the world are the two kinds of file the game writes, and either can be
read and written directly:

- **Standalone NBT** — `level.dat`, `playerdata/*.dat`, structure files, in
  gzip, zlib or uncompressed form.
- **Anvil region containers** — `.mca` / `.mcr`, which pack up to 1024
  independently-compressed chunks behind a sector-allocation table.

```python
from heaton_voxel.nbt import NBT, TagString

region = NBT.load("r.0.0.mca")
print(region)                                  # <NBT region r.0.0 795 chunks, 1223 sectors>

chunk = region.chunk(0, 0)                     # region-local coords, 0-31
print(chunk.data["Status"])                    # minecraft:full
print(len(chunk.data["sections"]))             # 24

chunk.data["Status"] = TagString("minecraft:empty")
region.save("r.0.0.mca")
```

Standalone files use the same entry point:

```python
level = NBT.load("level.dat")
print(level.root["Data"]["LevelName"])
level.save("level.dat")
```

NBT's text form, SNBT -- what commands, data packs and `/data get` speak --
reads and writes the way the game does, held case by case to the game's own
answers:

```python
from heaton_voxel import parse_snbt, to_snbt

tag = parse_snbt('{id: "minecraft:stone", count: 1b, tags: [a, b]}')
to_snbt(tag)    # '{count:1b,id:"minecraft:stone",tags:["a","b"]}'
```

There is also a CLI:

```
heaton-voxel-nbt r.0.0.mca                  # summary
heaton-voxel-nbt r.0.0.mca --verify         # confirm the round trip is byte-exact
heaton-voxel-nbt r.0.0.mca --check          # decode every chunk, report failures
heaton-voxel-nbt r.0.0.mca --dump 0,0       # print a chunk's tag tree
heaton-voxel-nbt level.dat --dump           # print a standalone file's tree
heaton-voxel-nbt level.dat --snbt           # print it as SNBT, the game's text form

# without installing, from a checkout:
python -m heaton_voxel nbt r.0.0.mca --verify
```

## Working at world scale

Chunks decode lazily. A freshly loaded region holds only compressed bytes, and
the tree is built the first time you read `chunk.data`. `release()` hands that
memory back, flushing any edits into the compressed bytes first — so it is safe
to call on a chunk you have modified.

```python
for chunk in region:
    analyze(chunk.data)
    chunk.release()
```

That pattern matters. Streaming all 4,044 chunks of a real world — 22 region
files, 84.6 MiB of NBT — costs **25 MiB of peak memory and 2.3 seconds**.
Decoding a single region eagerly costs 139 MiB, so holding a whole world open
at once would need roughly 3 GB.

Copying a region does no decoding at all: load and save are each effectively
instantaneous, because untouched chunks never become tags in the first place.
Pass `eager=True` to `load()` if you would rather pay the cost up front and
have corrupt chunks surface immediately.

## Writing safely

`save()` writes to a temporary file in the same directory and renames it over
the target. A plain write truncates first, so a crash, a full disk or an I/O
error partway through a multi-megabyte region would otherwise destroy the
original with no copy anywhere. Permissions are carried across, and an
interrupted save leaves the original untouched.

It also refuses to write into a world Minecraft currently has open:

```python
region.save()
# NBTError: the world at '.../test_world' is open in Minecraft (its
# session.lock is held). Writing to it now would be lost when the game
# flushes, or corrupt the region. Close the world first, or pass force=True.
```

Minecraft holds a Java `FileLock` on `session.lock` for as long as a world is
loaded. Java implements that with POSIX record locks, so detecting it needs
`fcntl.lockf` — `flock` uses a separate lock space and would report the world
as free. On non-POSIX platforms the check is skipped.

`save(touch_modified=True)` additionally stamps the current time on chunks
whose bytes actually changed, as the game does. It is off by default because
it would otherwise break byte-exact round-tripping.

An edit can be taken back. `save.begin_edit()` starts a record of every file
the save holds or opens until `record.complete()`, as it stood and as it was
left; `record.undo()` and `record.redo()` put either back in memory, and the
next `save()` writes it, so an edit undone after it was saved is undone on
disk too. A failed edit is `record.rollback()`. The record lives in memory
and is gone when the save is, so it is undo, not a backup.

## Losslessness

`load()` followed by `save()` reproduces the input **byte for byte**, not
merely equivalently. Verified across every NBT file in a real save — 45 files,
4,044 chunks, 84.6 MiB of NBT decoded and re-encoded exactly, including the
6 zero-length region files and the 185 chunks carrying stale slack bytes.

Getting there means preserving several things that are easy to lose:

| Detail | Why it matters |
| --- | --- |
| Java's *modified* UTF-8 | Not standard UTF-8: `NUL` is `C0 80`, and astral characters are CESU-8 surrogate pairs. Encoding with Python's `utf-8` codec silently changes the bytes. |
| Non-canonical string bytes | An overlong encoding decodes fine but would be normalized on re-encode, so the original bytes are kept. |
| Empty `TAG_List` element type | For an empty list, the declared type is the only surviving record of what it held — and writers disagree on whether to emit `TAG_End` or the real type. |
| Float bit patterns | A signaling NaN's payload does not survive `float32 → float64 → float32`, so the raw bytes are kept when they would not re-pack identically. |
| Sector slack bytes | Minecraft does not zero the tail of a chunk's last sector when the chunk shrinks. **185 of the 795 chunks** in `r.0.0.mca` carry stale data there. |
| Sector layout & timestamps | Chunks stay at their original offsets, including timestamps of chunks no longer present. |
| Zero-length region files | Minecraft writes 0-byte `.mca` files (common under `poi/`). These are valid empty regions, and stay 0 bytes rather than growing an 8 KiB header. |

Unmodified data is re-emitted from its original compressed bytes. The check is
a digest of the freshly re-serialized NBT against the source — so if anything
*did* change, it is recompressed rather than assumed identical. Rewritten
chunks use zlib level 6, which is what the game itself produces.

## Design notes

- Tags subclass their natural Python counterparts (`TagInt(int)`,
  `TagString(str)`, `TagCompound(dict)`, `TagList(list)`), so a parsed tree
  reads and mutates with ordinary Python syntax while still carrying the type
  needed to write it back.
- The three array tags subclass `array.array`, so large payloads stay compact
  and endian conversion happens at C speed via `byteswap()`. This is why a
  5 MB region file — 19.5 MiB of NBT — loads and re-encodes in under a second.
- A chunk that cannot be parsed keeps its raw bytes and reports the reason in
  `chunk.parse_error`, so one corrupt chunk never costs you the other 794.
- Editing chunks in place keeps the file's layout; only when a chunk outgrows
  its allocated sectors is the file repacked.
- Chunks too large for the format's 255-sector limit move to `.mcc` sidecar
  files, as the game does, and move back if they shrink. `to_bytes()` raises
  rather than silently dropping such a payload, since it cannot write sidecars.

## Refusals rather than silent damage

Corrupt input and misuse are reported, not papered over:

- Nesting deeper than 512 levels raises instead of exhausting the stack with a
  `RecursionError` from somewhere inside the parser.
- A `TAG_List` with a negative length is rejected rather than coerced to empty,
  which would silently rewrite the file on save.
- Mixing types in a list (`TagInt` into a list of `TagLong`) is rejected; it
  would otherwise change type silently on the way back in.
- Out-of-range scalars name the offending key: `'hp': 300 does not fit in
  TAG_Byte`.

Inside a region, any chunk that fails to decode keeps its original bytes, so a
bad chunk costs you that chunk's tree and nothing else.

## Blocks and biomes — `heaton_voxel.blocks`

`nbt` is the file format. `blocks` is the game's meaning on top of it:
the paletted, bit-packed containers that actually hold the world.

```python
from heaton_voxel.nbt import NBT
from heaton_voxel.blocks import Blocks, BlockState

region = NBT.load("r.0.0.mca")
blocks = Blocks(region.chunk(0, 0))

print(blocks[0, -64, 0])                       # minecraft:bedrock
blocks[8, 70, 8] = BlockState.parse("minecraft:chest[facing=north]")
region.save()
```

`x`/`z` are chunk-local (0-15) and `y` is a world coordinate; out-of-range
values raise rather than silently wrapping into the wrong block.
`BlockState` is immutable and hashable, so counting a world is just a dict:

```python
from collections import Counter

tally = Counter()
for chunk in region:
    tally.update(Blocks(chunk).histogram())
    chunk.release()
```

Other operations: `get_biome` / `set_biome` (biomes are stored per 4x4x4
cell), `section.blocks.histogram()`, and `prune()` to drop palette entries
that editing has left unreferenced.

**Writes go straight through.** Setting a block normally rewrites a single
64-bit word in the underlying `TAG_Long_Array`, so there is no buffer to
flush and no way to lose an edit by forgetting to. Growing the palette past a
power of two widens every index and transparently repacks the section.

### The packing, and its trap

Each section stores a palette of distinct states plus indices packed at
`max(4, ceil(log2(len(palette))))` bits — 1 bit minimum for biomes. When every
cell is identical the game omits the index array entirely, and this module
materializes it on demand.

Before 1.16, indices ran continuously and a single entry could straddle two
longs. From 1.16 on, each long holds `64 // bits` whole entries and the spare
high bits are padding. Reading a modern chunk with the old rule returns
plausible-looking nonsense, so the layout is chosen from the chunk's
`DataVersion`.

The straddling case has a second trap. NBT longs are *signed*, so a long with
its top bit set is negative, and `>>` in Python sign-extends it — phantom 1
bits that survive the OR against the next long and silently corrupt the value.
Both packings are verified against each other in the tests, on data
deliberately containing negative longs.

## Whole saves — `heaton_voxel.world`

`MinecraftSave` is the world: every region file of every dimension behind one
set of global block coordinates. It takes the path to whichever save you want
to edit.

```python
from heaton_voxel.world import MinecraftSave

save = MinecraftSave.load("~/Library/.../saves/my_world")
print(save)                          # <MinecraftSave 'my_world' [overworld=16] 8,647 chunks>

print(save.player_position())        # (39.10, 68.00, -122.10)  exact, global
x, y, z = save.player_block_position()
print(save.get_block(x, y - 1, z))   # minecraft:grass_block[snowy=false]

save.fill((x - 8, y, z - 8), (x + 8, y + 16, z + 8), "minecraft:glass")
save.reset_lighting((x - 8, y, z - 8), (x + 8, y + 16, z + 8))
save.save()
```

`player_block_position()` floors rather than truncates, so it stays correct
west and north of the origin — a player at `z = -122.1` is standing in block
`-123`, not `-122`.

`reset_lighting` exists because chunk light is *baked*: the game stores
computed light and trusts it while a chunk's `isLightOn` flag stands, so any
edit leaves light describing the world as it was — a torch placed in a newly
carved room lights only its own block. Calling it over the edited area (same
corners as the fill; y is ignored, light is per-chunk) clears the flag and
the stale arrays for those chunks and one ring of neighbors — light crosses
chunk borders — and Minecraft relights them on first load. Ungenerated
chunks are skipped by design; unreadable ones are reported, since their
stale light cannot be cleared.

Two more questions come up constantly and have one-line answers.
`save.surface_y(x, z)` is *where is the ground* — a heightmap lookup, ~90x
faster than scanning blocks, with `MOTION_BLOCKING` (what you stand on),
`WORLD_SURFACE` (what you'd see from above), `OCEAN_FLOOR` (the seabed) and
`MOTION_BLOCKING_NO_LEAVES` to choose from; `None` means the ground there
does not exist yet. `save.histogram(c1, c2)` is *what is here* — a census of
a box with the same honest accounting as `fill`, counting uniform sections
in constant time. And for *what does it look like*, `heaton_voxel.craft.render`
draws top-down area maps and vertical cutaways of any box
(`python -m heaton_voxel render <world> --area X0 Z0 X1 Z1`) — the tooling
this project itself uses to verify builds like `castle.py`'s. For *is what
stands here sound*, `python -m heaton_voxel audit <world> --box X1 Y1 Z1 X2 Y2 Z2`
judges a box: what the game will not keep, water that is moving or about to,
and stale heightmaps. And for *what rule is this building*, `python -m
heaton_voxel elevation <world> --box X0 Z0 X1 Z1 --floor Y --top Y` reads its
four walls into `craft.Elevation` rules, each replayed over the wall it came
from, and `render --elevation south X0 Y0 Z0 X1 Y1 Z1` draws one from outside.
`python -m heaton_voxel massing <world> --box X1 Y1 Z1 X2 Y2 Z2` does the same
for a tower: it finds the tiers it steps back through, from the box alone.
`examples/setback_tower.py` builds one with `craft.Massing`, `Core` and `Crown`
through a single `Builder.tower` call, and `craft.design_tower` works one out
from a lot, a height and a style, its plan, tiers, roof and colors rolled from
the lot: `examples/skyline.py` lays a row of them, no two alike. For what runs
between them, `craft.StreetNetwork` is a grid or any streets drawn once, and
`Builder.streets` lays it whole: crossings first, half-block ramps between them,
pavements, and a walk over all of it with no jump before a block is written. And
for all of it at once, `craft.plan_city` works a city out from the land, streets
stepping round water and blocks becoming lots or parks by what is on them, draws
it, and `Builder.city` lays it: `examples/city.py <world> --towers 50`.

There is a CLI. Installed, `heaton-voxel` is the whole command line that
`python -m heaton_voxel` is, so every command above runs under it
(`heaton-voxel render`, `heaton-voxel audit`, `heaton-voxel massing` ...);
`heaton-voxel --help` lists them:

```
heaton-voxel world <world-path>
heaton-voxel world <world-path> --fill 0 70 0 8 78 8 minecraft:glass --write

# without installing, from a checkout:
python -m heaton_voxel world <world-path>
```

And a worked example in [`examples/gold_box.py`](https://github.com/jeffheaton/heaton-voxel/blob/main/examples/gold_box.py), which
places a solid cube next to the player:

```
python examples/gold_box.py <world-path>                     # 3x3x3 gold
python examples/gold_box.py <world-path> --size 5 --dry-run  # preview only
python examples/gold_box.py <world-path> --block minecraft:diamond_block
```

It refuses to run against a world Minecraft has open, since the game keeps its
own copy of the chunks in memory and would discard the edit when it next
saves. Close the world, run the script, *then* load it.

## Schematics — `heaton_voxel.schem`

Builds move between worlds — and between this toolkit and WorldEdit, Axiom,
Litematica, structure blocks and data packs — in the three formats they are
shared in: Sponge `.schem` (container versions 2 and 3, the latter written by
default), the vanilla structure `.nbt` a structure block saves (held to the
game's own `StructureTemplate`), and Litematica's `.litematic` (held to
litemapy). One `Schematic` reads all three, telling them apart by content, and
writes whichever the file name says; a cell a structure or a litematic leaves
out reads as `structure_void`, which a paste leaves as the world has it, as
the game does. The pre-1.13 `.schematic` format is a different animal
(numeric block ids) and is refused with a message saying so, as is any file
whose `DataVersion` predates 1.18.

```python
save.export_schem((11, 55, 247), (74, 112, 310), "castle.schem",
                  entities=True)                    # mobs, frames, stands
result = save.import_schem("castle.schem", (-120, 52, 100), anchor="min")
print(result)      # <SchemImportResult 237,568 blocks, 25 chunks, 4 entities>
save.save()
```

Block entities ride along automatically — chests keep their contents, signs
their text, spawners their settings — and pasting *removes* stale block
entities from overwritten positions, so stone pasted over a chest does not
leave the chest's inventory haunting the chunk. Entities are opt-in on export
(like WorldEdit's `//copy -e`); on paste every entity gets a fresh `UUID`,
remapped consistently so leashes and riders inside the paste stay attached,
because a world must never hold two entities with one identity. Version-3
biome data round-trips too (`biomes=True` on both sides).

Placement follows WorldEdit's convention: a file records where its author
stood (`origin=` on export), and `import_schem(..., at)` puts that point at
`at` — so the same coordinates round-trip a build exactly. `anchor="min"`
ignores all that and puts the box's minimum corner at `at`. `paste_air=False`
masks the schematic's air, letting terrain show through instead of punching
the bounding box into it; lighting over the pasted box is handed back to the
game automatically.

Pastes can also turn: `rotate=90` (multiples of 90°, clockwise from above,
swung about the origin like `//rotate`) and `flip="x"|"y"|"z"` — or
standalone, `schematic.rotated(90)` / `schematic.flipped("x")`. The whole
copy turns together: stairs re-face, logs swap axes, signs re-aim, rails
re-bend, walls carry their connections around, mirrors swap stair corners,
chest halves and door hinges, and entities turn with position, yaw and
hanging anchors intact. The property vocabulary is value-guarded game
knowledge: a state it cannot cover passes through unchanged and, if it looks
directional, is itemized in `transform_warnings` — reported, never guessed.

The same honest accounting as `fill` applies in both directions. An export
reaching into ungenerated or unreadable chunks **refuses** by default — a
schematic has no way to say "unknown" and would otherwise claim air — with
`allow_incomplete=True` to air-fill and itemize the gaps instead; a paste
skips missing chunks and reports them. A schematic stamped by a newer
Minecraft than the world is refused, since the game would not data-fix
blocks inside chunks that already claim to be current.

`Schematic` also stands alone: `Schematic.load(path)` / `.save(path)`,
`Schematic.new(w, h, l, data_version)`, `get_block`/`set_block`,
`set_block_entity`, `set_biome`, `histogram()` — build one from scratch or
inspect one without touching any world. And there is a CLI:

```
python -m heaton_voxel schem info castle.schem
python -m heaton_voxel schem export <world> castle.schem --box 11 55 247 74 112 310 --entities
python -m heaton_voxel schem import <world> castle.schem --at -120 52 100 --anchor min --rotate 90 --write
python -m heaton_voxel schem import <world> castle.schem --at '~' '~' '~' --write   # //paste where the player stands
python -m heaton_voxel schem transform castle.schem mirrored.schem --flip x
```

CLI coordinates accept the game's `~` notation — `~` is the player's
position, `~-5` five blocks short of it — in `schem`, `world --fill` and
`render` alike (`save.resolve_coordinates` in the API). Quote the tokens
(`'~'`, `'~-5'`): shells expand a bare `~` to your home directory, and the
CLI notices when that happened and tells you to quote rather than
misreading it.

### Re-climating an area — [`examples/biome_zone.py`](https://github.com/jeffheaton/heaton-voxel/blob/main/examples/biome_zone.py)

Sets the biome over a square centered on the player, from ground level to the
sky if the player is above ground, or down to bedrock if they are underground:

```
python examples/biome_zone.py <world-path>                          # 32x32 snowy taiga
python examples/biome_zone.py <world-path> --width 64 --dry-run     # preview only
python examples/biome_zone.py <world-path> --biome minecraft:lush_caves
python examples/biome_zone.py <world-path> --direction down         # from your feet, downward
python examples/biome_zone.py <world-path> --heightmap OCEAN_FLOOR  # measure to the seabed
```

`--direction` chooses which way the square extends vertically, and the two
modes anchor at different heights on purpose:

| | anchor | fills |
| --- | --- | --- |
| `auto` (default) | ground level | up if the player is above ground, down if below |
| `up` | the player's own y | up to the top of the world |
| `down` | the player's own y | down to the bottom of the world |

Standing on a hilltop and asking for `down` fills from your feet, not from a
ground level that is the same height you are. An explicit direction also needs
no heightmap, so it works in a partly-generated chunk where `auto` has nothing
to measure.

Ground level comes from the chunk's `MOTION_BLOCKING` heightmap in the player's
own column, which is a lookup rather than a scan. It is the topmost block that
stops you moving — deliberately not `WORLD_SURFACE`, which counts the flower or
snow layer you are standing *in* and so reports ground level at your own feet.
Water blocks motion, so a boat counts as above ground and diving counts as
below; `--heightmap OCEAN_FLOOR` measures to the seabed instead.

Biomes are stored per 4×4×4 cell, so the square is rounded outward to whole
cells; the script prints the box it actually wrote. Changing a biome does not
retroactively change the world — snow already on the ground is placed blocks
and stays put, while weather and mob spawning follow the new biome.

### Raising a castle — [`examples/castle.py`](https://github.com/jeffheaton/heaton-voxel/blob/main/examples/castle.py)

Builds an ornate stone castle sized to order: `--width` is the square
footprint everything — moat included — fits inside, `--height` is how far the
keep rises above the ground, and every other proportion follows from those
two.

```
python examples/castle.py <world-path>                        # 64 wide, 44 high, centered on the player
python examples/castle.py <world-path> --width 96 --height 56
python examples/castle.py <world-path> --at 120 -340 --y 64   # exact site and ground level
python examples/castle.py <world-path> --no-dungeon --dry-run # keep the ground solid; write nothing
```

What you get, scaled to fit: curtain walls with a crenellated wall-walk you
can patrol end to end (it tunnels through the towers), round corner towers
with ladders, floors, arrow slits, machicolations and conical spires, a
gatehouse with a raised portcullis, murder holes, drawbridge and pennants, a
moat with a grass verge banking the water, and a central keep with a great
hall, throne dais, switchback stairs, arched glazing, cornices, roof turrets
and a flag. Beneath the keep — unless `--no-dungeon` — a dungeon is cut into
the ground: a lantern-lit undercroft that grows pillars, barred cells and an
iron-caged treasury at scale, reached by a walled stair from the great hall
(a ladder shaft in small keeps), sealed in masonry first so a cut that runs
into a cave meets a wall rather than a hole. `--seed` weathers the same
stones the same way every run; by default the seed comes from the site, so
re-running one command rebuilds the identical castle.

The castle is centered on where the player logged out — they wake up standing
in their own great hall — on ground set to the median of the terrain it
replaces; `--at` and `--y` override. The site is leveled and everything
above it inside the footprint is cleared, so `--dry-run` first when unsure.
The foundation follows the land: every column fills down to solid ground
(through water, to the seabed), and where the site overhangs lower terrain
the surrounding ground is graded down at 45° — grass over dirt over stone,
one block per ring — until the embankment meets the hillside, so a castle
on a ledge stands on an earthwork rather than a floating slab. Chunks the
world has never generated are reported and skipped, never invented — a
castle overlapping the world's edge says exactly which chunks it could not
reach.

Three things happen that a naive block-placer would skip. Every column the
build touches has its four **heightmaps recomputed** — otherwise mobs spawn
on the meadow that no longer exists and `world_map.py` keeps drawing it —
with the block classifier pinned, in the tests, against genuine game output
from the fixture. Everything the build touched is **handed back to the
game's light engine** with one `save.reset_lighting()` call: chunk light is
baked data the game trusts, so without it a torch in the new-cut dungeon
lights only its own block. And the report keeps *ungenerated* apart from
*unreadable* chunks, because "nothing there" and "something there this
toolkit cannot see" call for different responses.

### Laying a road — [`examples/road.py`](https://github.com/jeffheaton/heaton-voxel/blob/main/examples/road.py)

Builds a level stone-brick road, three wide with lamp posts, straight ahead
from wherever the player logged out — the way they were facing, at the level
they were standing:

```
python examples/road.py <world-path> 200            # 200 blocks, the way you faced
python examples/road.py <world-path> 120 --dry-run  # survey and build in memory only
```

The road never climbs; the terrain gives way instead. Hills up to six blocks
are cut through — two level shoulders each side, walls tapering up at 45° —
and taller ones are tunneled with three blocks of headroom, stone lining and
torches. Shallow dips (three air blocks or fewer under the bed) are embanked
in stone with dirt shoulders leveling out up to six blocks aside; deeper
drops, and water at road level, get a railed bridge on piers. A bridge low
over water **arches**: the deck climbs on stone stairs, crosses with three
blocks of air under it for boats, and steps back down; a dry ravine keeps a
level deck. The whole route is **surveyed before anything is placed** —
heightmap lookups the whole way. A single span longer than 100 blocks ends
the road at the near shore (reported, not refused); a route onto ungenerated
ground is refused outright. Length is the only parameter, 500 the maximum.
Afterwards the heightmaps are refreshed and the touched chunks relit, so the
lamps really light.

### A whole building session — [`examples/craft_session.py`](https://github.com/jeffheaton/heaton-voxel/blob/main/examples/craft_session.py)

The construction tier, `heaton_voxel.craft`, start to finish, the way every
real session should run: a worksite snapshot first, the build in named
stages, the aftercare, the checks, and only then the save.

```
python examples/craft_session.py <world-path> --at X Z                  # dry run: writes nothing
python examples/craft_session.py <world-path> --at X Z --palette lab    # the secret lab's materials
python examples/craft_session.py <world-path> --at '~' '~' --write      # save it, under a worksite
python -m heaton_voxel worksite rollback <world-path> craft-session     # what undoing it would move
```

On a 32-block lot, leveled (and filled up from the bed over water): a
two-story house -- a `shell` whose walls follow a `Facade`, under a gabled
`Roof`, a `doorway` (an iron door with a button each side in the lab's
palette), a `flight` up through the floor it cuts, both stories furnished
-- a lane to a paved square, lit by `line_lamps` to `NO_SPAWNS` and by
`pave` to `NO_NEW_SNOW_OR_ICE`, a villager in the square, and a watch tower
crowned by `battlement`, its deck lit flush. It builds in one of two
sample palettes in [`examples/palettes.py`](https://github.com/jeffheaton/heaton-voxel/blob/main/examples/palettes.py), `ROMAN`
and `LAB`, each role read off the build it is named for. The land round
the lot is protected before anything is built; then it checks what it
built: nothing the game will not keep that was not there before, a clean
`site.check()` (that land as it was), the house walked from its doorstep
to the top story *and back*, and no cell it lit dark enough for a monster
-- and reports the diff against the snapshot block by block. Without `--write` even the
snapshot goes to a temporary directory, and the world is left byte for byte
as it was; with it, the build is saved only once every check has passed,
and the `worksite rollback` plan for undoing it is printed.

### Mapping a world — [`examples/world_map.py`](https://github.com/jeffheaton/heaton-voxel/blob/main/examples/world_map.py)

Renders a top-down PNG of every block in a dimension. Read-only.

```
python examples/world_map.py <world-path> -o map.png
python examples/world_map.py <world-path> --scale 4          # 4x4 px per block
python examples/world_map.py <world-path> --grid             # label region files
python examples/world_map.py <world-path> --dimension minecraft:the_nether
```

`--grid` overlays the 512-block region boundaries and writes each cell's file
name into it — `r.-1.0` is `r.-1.0.mca` — so anything you spot on the map leads
straight back to the file holding it. Only regions that actually exist get
labeled. There is no font dependency: the digits are a hand-carried 5×7
bitmap, drawn in block space so they scale with `--scale` like everything else,
over a darkened box so they stay readable on sand and snow. `--grid-text N`
adjusts the size.

| color | meaning |
| --- | --- |
| black | never generated |
| **magenta** | the chunk exists but is too old to read |
| everything else | the topmost block, shaded by slope, water shaded by depth |

Three things make this work at world scale:

- **Heightmaps, not scanning.** Every chunk already stores `WORLD_SURFACE` —
  256 entries at 9 bits, which is exactly `unpack_indices(longs, 256, 9)`.
  Measured against scanning down for the first non-air block: identical
  results, **94x faster**.
- **Streaming PNG.** A PNG is zlib plus four chunks, so the encoder is about
  60 lines of stdlib and writes rows as they are produced (it now lives in
  `heaton_voxel.craft.render` as `PNGWriter`, alongside `ColorMap`, which draws
  from the tables in `heaton_voxel.craft.colors`, and the
  `render_area`/`render_slice` helpers this map is built from). A
  13,824 × 17,920 map is 743 MB of raw pixels; peak memory is one 512-block
  band.
- **Partly-generated chunks are skipped.** Chunks stopped at
  `structure_starts` or `biomes` have no surface and no heightmap. Scanning
  them for one cost 97 of the first run's 128 seconds and produced terrain
  that does not really exist yet. Skipping them took the same world from
  **128.6s to 10.5s**.

The block palette is best-effort: exact colors for common terrain, the 16 dye
colors expanded across wool/concrete/terracotta/glass families, then, for a
stair, slab or wall the game has, the color of the block it is cut from, then
substring heuristics (`*_leaves`, `*_log`, `deepslate*`), then a wood-species
fallback. Anything still unmatched is drawn gray and **reported at the end,
most frequent first** (a cut under the block it is cut from), so the table is
easy to improve:

```
  unmapped block types: 7 (drawn gray; a stair, slab or wall counted as its block) -- most common:
             19  minecraft:wheat
              2  minecraft:campfire
```

### fill, and the boundaries it crosses

`fill(corner1, corner2, block)` takes two corners **in any order** and fills
the inclusive box between them. A box may span any number of chunks, sections
and region files, and the coordinates may be negative — chunk, section and
region indices are derived with arithmetic shifts, which floor, so block `-1`
lands in chunk `-1` at local `15` rather than chunk `0`.

A fill is not all-or-nothing, and pretending otherwise would be the wrong
thing: a box can reach past the edge of what the world has generated, or above
and below its buildable range. Those blocks are counted and reported rather
than silently dropped — and ungenerated chunks are **skipped, not created**,
since a chunk invented here would have no terrain, heightmaps or lighting.

```python
result = save.fill((0, 60, 0), (300, 200, 300), "minecraft:stone")
result.complete            # False
result.blocks_filled       # 8,208,081
result.missing_chunks      # [(17, 4), (17, 5), ...]
result.blocks_out_of_range # 1,140,000  (above y=319 here)
```

Sections that end up **entirely** inside the box are replaced wholesale with a
single-entry palette and no index array — the same shape the game uses for a
uniform section. That makes a solid fill constant-time per 16×16×16 section
instead of per block:

| | rate |
| --- | --- |
| Box aligned to whole sections | **13.4M blocks/s** |
| Same volume, offset by one so no section is whole | 7.0M blocks/s |

4.2 million blocks in 0.31s, and memory grew by **2 MiB** — chunks are flushed
and released as the fill sweeps, so the volume is not bounded by RAM. Pass
`keep_loaded=True` if you want them kept.

Nothing reaches disk until `save.save()`, which refuses by default to write
into a world Minecraft currently has open.

### Reading and writing only what you touch

Opening a save **indexes** its region files without reading them. Each is read
the first time something asks for a chunk inside it, and the chunks inside
that stay compressed until their contents are wanted.

Saving writes only files whose bytes actually differ, decided by hashing the
serialized region and comparing against the hash taken when it was loaded.
Editing 27 blocks in a 62 MiB world therefore reads one file and writes one
file:

| | before | now |
| --- | --- | --- |
| `MinecraftSave.load` | read 16 files, 62 MiB | **0 files** |
| 27-block fill | — | reads **1** file |
| `save()` | wrote **16** files | writes **1** |
| `save()` with no changes | wrote 16 | writes **0** |

Hashing the whole world costs about 60 ms, which is why it is done this way
rather than with dirty flags at the editing entry points. Flags would be
marginally faster and *wrong*: nothing stops

```python
chunk.data["Status"] = TagString("minecraft:empty")     # around the block API
```

and a save that trusted a flag would silently drop it. Comparing hashes
catches a change however it was made — through the block API, by reaching into
`chunk.data`, by adding or deleting a chunk, or by re-stamping a timestamp.
The failure mode of the cheaper design is losing work while reporting success,
which is not a trade worth 60 ms.

`Chunk.release()` makes the same point concretely: after a fill, **no chunk is
still decoded**, so any "has anything been touched?" shortcut based on object
state would find nothing and skip a region that genuinely changed.

Iterating a dimension (`for region in dimension`) deliberately opens every
region file rather than just the loaded ones — a sweep that silently skipped
unread regions would look like a clean pass over an empty world. Use
`dimension.loaded_regions()` for the cheap subset, or `MinecraftSave.load(...,
eager=True)` to read everything up front.

### Verified against the real thing

Every paletted container in the world — **80,312 containers, 205,574,720
cells** across 8,647 chunks — unpacks and repacks to bytes bit-identical to
what Minecraft wrote, at bit widths 1 through 7. Reading blocks never
perturbs the file: a chunk you have only inspected still saves byte-for-byte.

## Scope and known limitations

**Java Edition only.** Bedrock worlds are another format altogether — a
LevelDB database rather than region files — and nothing here reads them.

`heaton_voxel.nbt` handles the NBT and region formats generally. The block and world
layers target the **current Minecraft chunk format** — sections at the chunk
root, indices packed so no entry straddles a long, dimensions under
`dimensions/` — and also **read** what came before, never writing it:
chunks Minecraft 1.13 to 1.17.1 saved (DataVersion 1519 to 2730), which a
world played across the 1.18 update, or a map downloaded from that era,
still holds wherever nobody has stood since; and saves in the layout before
26.1 — `region/`, `DIM-1/`, `DIM1/`, settings in `level.dat` — which every
1.13 to 1.21 world nobody has opened since still is. The chunks read like
any other, y 0 to 255; a save in the old layout reads its settings and
player as the game's own upgrade of it would; and both refuse every write,
because the game rebuilds an old chunk when it loads one, deeper and with
new terrain under it, and moves an old save's files the first time it opens
it, and nothing here can do either for it. Each answer is held to the
game's own upgrade of real and made samples. Anything older than 1.13 is
refused with a clear error rather than misread, because its blocks are
numbers, and reading them as anything else produces plausible-looking
nonsense instead of an obvious failure. Open such a world in a current
Minecraft to upgrade it first.

- **No lighting recalculation.** `BlockLight` and `SkyLight` are preserved but
  not recomputed, so edited chunks keep stale lighting until the game relights
  them. (`save.reset_lighting` hands an area back to the game instead.)
- **Blocks are not items.** Fences, walls, panes, bars and stair corners
  store neighbor-derived state that vanilla only recomputes on a block
  update, and beds and doors are two blocks each. Build with
  `save.place_block`, which places like the game places an item — both
  halves, settled connections, waterlogging — or run `save.connect_blocks`
  over bulk-built areas. The connection rules were validated against 22,616
  vanilla-placed blocks with zero disagreements.
- **Heightmaps are rebuilt when asked.** Writing blocks leaves the stored
  `Heightmaps` describing the old terrain, as it leaves the light.
  `save.rebuild_heightmaps(c1, c2)` rewrites all four from the blocks by the
  game's own rules, byte for byte what the game stores for a map that is
  right, and `import_schem` rebuilds the chunks it writes into.
- **No block-state validation.** Nothing stops you placing
  `minecraft:not_a_real_block`, or a state whose properties the game does not
  recognize; it will be written faithfully and the game may reject it.
- **`fill` does not create chunks.** Ungenerated ones are reported, not
  invented. To build past the generated ground, `save.create_chunks(c1, c2)`
  makes finished chunks of air there first -- the game's own finished chunk
  with nothing in it, which it loads as it stands and lights on first load --
  and leaves every chunk the world already holds as it is.
- **LZ4 chunk compression** (available from 1.20.5, unused by default) needs
  the optional `lz4` package. Without it such chunks keep their bytes and
  report through `chunk.parse_error`.

## Tests

```
python -m unittest discover -s tests -v   # ~2,000 tests, ~60s idle
```

The suite is self-contained: it runs against `fixtures/`, which holds **real
Minecraft output** rather than synthesized data. Each chunk's compressed
payload, its record and the stale slack bytes in the tail of its last sector
are copied verbatim from a save; only the sector allocation order is ours, and
the game does not guarantee that anyway.

`fixtures/r.-1.0.mca` is 127 KB — 16 chunks chosen to cover every packed bit
width the source world contained (1, 2, 4, 5, 6 and 7; width 7 occurred
exactly once in 80,312 containers), three generation states, and 14 chunks
carrying non-zero slack. `fixtures/make_fixture.py` regenerates it, so the
provenance is auditable rather than asserted.

To also exercise a live world:

```
MINECRAFT_SAVE=~/Library/Application\ Support/minecraft/saves/my_world \
  python -m unittest discover -s tests
```

The package ships `py.typed`, so a type checker reads its annotations, and
the deterministic tier is checked clean by mypy (`[tool.mypy]` in
`pyproject.toml`; run `mypy` from the repository root, with mypy from a venv,
since it is never a dependency).

`test_world.py` builds complete saves from scratch in temp directories, so the
boundary cases are constructed exactly — chunks that stop at a known height,
regions that do and do not exist, coordinates either side of zero — and a
final group runs the same operations against the real fixture chunks.

Tests always work from a private copy, never the original — a world open in
Minecraft has its region files rewritten underneath a reader, which would make
results random, and the copy guarantees the suite cannot write to a real save.
If the copy will not parse, the tests skip with a message pointing at the
likely cause rather than reporting a bogus failure.

Three worth knowing about:

- `test_nbt_layer_is_exactly_lossless` re-encodes every chunk from its parsed
  tree and compares against the decompressed source — deliberately bypassing
  the original-bytes reuse, so it tests the encoder rather than the cache.
- `test_refuses_to_write_into_an_open_world` takes a real POSIX record lock
  from a subprocess, so the interlock is verified against an actual held lock
  rather than a mock.
- `test_repacking_reproduces_the_games_own_bytes` repacks every block and
  biome container and compares against what Minecraft wrote.

## License

Apache License 2.0 — see [LICENSE](https://github.com/jeffheaton/heaton-voxel/blob/main/LICENSE).

Not an official Minecraft product. Not approved by or associated with Mojang
or Microsoft.
