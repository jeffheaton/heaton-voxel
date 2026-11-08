# API contract

What a program in `examples/` — or any other consumer — can rely on. The three
modules are layered, and you should reach for the highest one that does the
job:

```
heaton_voxel.world     MinecraftSave     whole save, global block coordinates
   |
heaton_voxel.blocks    Blocks            one chunk's blocks and biomes
   |
heaton_voxel.nbt       NBT, Chunk, Tag*  region files and raw tag trees
```

Most examples only need `world`. Drop to `blocks` for per-chunk work
(heightmaps, palettes, histograms) and to `nbt` for anything the block layer
refuses or does not model. Every name below is also re-exported from the
top-level `heaton_voxel` package.

Beside the layers sit two more modules. `heaton_voxel.schem` moves boxes of
world through Sponge `.schem` files (WorldEdit's format) — it is format
knowledge and re-exported like everything else; its entry points also hang
off `MinecraftSave` as `export_schem`/`import_schem`. Above the
deterministic layers sits `heaton_voxel.craft`, the judged tier:
`craft.build` constructs and `craft.render` draws top-down area maps and
vertical cutaway PNGs of any of it. Both are imported explicitly
(`from heaton_voxel.craft import render_area`), never re-exported: their
palettes and colors are game knowledge and taste, unlike the formats.
See the end of this file for both.

---

## Conventions that hold everywhere

**Coordinates.** `world` speaks **global block coordinates** throughout,
negatives included. `blocks` speaks **chunk-local `x`/`z` (0–15)** with a
**world `y`**, because that is how the data is laid out. `nbt` speaks
**region-local chunk coordinates (0–31)**.

Derive indices with `>>` and `&`, never `int(x / 16)`:

```python
chunk_x  = x >> 4      # block  -> chunk        (-1 -> -1, not 0)
local_x  = x & 15      # block  -> within chunk (-1 -> 15)
region_x = chunk_x >> 5
```

**`None` means absent. An exception means present but unreadable.** This
distinction is load-bearing: a chunk that exists but is too old to parse must
never look like empty space, or a tool will happily bulldoze a decade-old
build. Anything that reports "nothing here" separates the two.

**Nothing is written until you ask.** Mutating a tree changes memory only.
`MinecraftSave.save()` / `NBT.save()` is the only thing that touches disk, and
it refuses by default to write into a world Minecraft has open.

**Take the world path as an argument.** No example hardcodes a save.

---

## `heaton_voxel.world` — whole saves

### `MinecraftSave`

```python
save = MinecraftSave.load(path, *, dimensions=None, eager=False)
```

Indexes region files without reading them. Each is opened the first time a
chunk inside it is wanted. `eager=True` reads everything up front.

**A save in the layout before Minecraft 26.1 opens where it stands,
read-only.** Until DataVersion 4772 (`DIMENSION_STORAGE_DATA_VERSION`) a
world kept its overworld in `region/` beside `level.dat`, the nether in
`DIM-1/region`, the End in `DIM1/region`, its players in `playerdata/`, and
its weather, clock, border, spawn and difficulty as fields of `level.dat`;
every 1.13 to 1.21 world nobody has opened since is that way, current-format
chunks and all. `save.layout` is then `"old"` and `save.read_only` says
why. Every reader answers what the game's own `FileFixerUpper` would make
of the save (`tools/probe_old_layout.py` asks it, `tests/test_old_layout.py`
holds every reader to it): `difficulty()` names `Difficulty` as
`LevelDatDifficultyFix` does, `weather()` and `day_time()` read the old
fields (one clock, the overworld's), `spawn_position()` reads `SpawnX`..`Z`
or the `spawn` compound, `world_border(dim)` gives every dimension the one
border `level.dat` holds, flat `Border*` keys or the `world_border`
compound of 4544, the nether's centered an eighth as far out, and the player
is `playerdata/<uuid>.dat` of the one `level.dat` holds -- the copy the game
keeps -- else `level.dat`'s own (which the game's move drops), else the first
of `playerdata/`. `save()`, every setter and every writer raise
`ReadOnlySave` (a `ReadOnlyChunk`), or report every chunk as one they will
not write, the way they report a chunk Minecraft 1.17 saved. A save holding
region directories in both layouts is refused, as is one whose `level.dat`
is newer than the old layout. Open it in a current Minecraft to move it.

| method | returns | notes |
| --- | --- | --- |
| `player_position()` | `(float, float, float)` | exact entity position; `y` is the feet |
| `player_block_position()` | `(int, int, int)` | **floored**, so correct west/north of origin |
| `player_rotation()` | `(float, float)` | yaw, pitch in degrees; yaw 0 = south, 90 = west |
| `player_dimension()` | `str` | e.g. `"minecraft:overworld"`; a number, as a player before 1.16 names it, read as the game's `LegacyDimensionIdFix` reads it |
| `spawn_position()` | `(int, int, int)` | world spawn |
| `set_spawn_position(x, y, z, *, dimension=None, yaw=None, pitch=None)` | `(int, int, int)` | move the world spawn; unstated dimension/facing are **kept**, not reset |
| `game_type()` / `set_game_type(mode, *, include_player=True)` | `str` / — | `"survival"`, `"creative"`, `"adventure"`, `"spectator"`; the setter also updates the single player's own mode unless told not to |
| `allow_commands()` / `set_allow_commands(allowed)` | `bool` / — | cheats |
| `difficulty()` / `set_difficulty(name)` | `str` / — | `"peaceful"`…`"hard"`; the setter **refuses while locked** |
| `hardcore()` / `set_hardcore(value)` | `bool` / — | |
| `difficulty_locked()` / `set_difficulty_locked(value)` | `bool` / — | the way through a refused `set_difficulty` |
| `weather()` | `WeatherState` | `.kind` is `"clear"`/`"rain"`/`"thunder"`; raw timers on the fields |
| `set_weather(kind, *, duration=None)` | — | `/weather` semantics: ticks, `None` lets the game roll the next change |
| `day_time(dimension=None)` | `int` | that dimension's clock in total ticks; day = `//24000`, time of day = `%24000` |
| `set_day_time(time, dimension=None)` | `int` | absolute ticks, or `"day"`/`"noon"`/`"night"`/`"midnight"` advancing to the **next** occurrence, never backward |
| `world_border(dimension=None)` | `WorldBorder` | read-only; **per dimension**, overworld by default. A dimension with no border file answers with the defaults (`stored=False`) rather than raising |
| `resolve_coordinates(tokens, axes=None)` | `tuple[int, ...]` | user-typed coordinates, `~`/`~5`/`~-3` player-relative; tokens cycle x,y,z unless `axes` (e.g. `"xzxz"`) says otherwise |
| `get_block(x, y, z, *, dimension=None)` | `BlockState \| None` | `None` if ungenerated, out of height range, **or unreadable** |
| `set_block(x, y, z, block, *, dimension=None)` | `bool` | writes **exactly** the state given; `False` if it could not be written |
| `place_block(x, y, z, block, *, dimension=None)` | `bool` | places like the game places an **item**: beds/doors/tall plants get both halves, connections settle, a water source waterlogs (flowing water does not); see below |
| `surface_y(x, z, heightmap="MOTION_BLOCKING", *, dimension=None)` | `int \| None` | heightmap lookup, never a scan; `None` = no surface to report |
| `histogram(c1, c2, *, dimension=None, keep_loaded=False)` | `HistogramResult` | box census; uniform sections count in constant time |
| `find_blocks(c1, c2, names, *, match="name", dimension=None, limit=None, keep_loaded=False)` | `FindResult` | where a block is; sections whose palette lacks it are never unpacked. See below |
| `fill(c1, c2, block, *, dimension=None, prune=True, keep_loaded=False)` | `FillResult` | see below |
| `replace_blocks(c1, c2, mapping, *, dimension=None, prune=True, keep_loaded=False)` | `ReplaceResult` | rewrite only the states a `{state: state}` mapping names; stays out of unfinished chunks and drops the block entities it orphans; see below |
| `unfreeze(c1, c2, *, dimension=None, keep_loaded=False, deep_snow=False)` | `UnfreezeResult` | rewrite the three states **freezing** writes — the snow layer, ice, `snowy=true` — and nothing that merely looks wintry; see below |
| `get_biome(x, y, z, *, dimension=None)` | `str \| None` | `None` on the same cases as `get_block`, plus a section with no biome data |
| `set_biome(x, y, z, biome, *, dimension=None)` | `bool` | writes the whole 4×4×4 cell containing the block |
| `fill_biome(c1, c2, biome, *, dimension=None, prune=True, keep_loaded=False)` | `BiomeFillResult` | see below |
| `reset_lighting(c1, c2, *, dimension=None, neighbors=True)` | `LightResetResult` | call over every **edited** area before saving; see below |
| `connect_blocks(c1, c2, *, dimension=None, stairs=True)` | `ConnectResult` | call over every area where fences, walls, panes, bars, gates or stairs were **placed**; see below |
| `rebuild_heightmaps(c1, c2, *, dimension=None, keep_loaded=False)` | `HeightmapResult` | rewrite all four maps from the blocks, by the game's tags, byte for byte what it stores for a map that was right; y ignored, whole chunks; ungenerated and unfinished chunks are skipped and listed (`missing_chunks`, `unfinished_chunks`) |
| `invalidate_poi(c1, c2, *, dimension=None)` | `int` | drop cached bed/workstation/bell/portal records so the game rescans the chunks on load; y ignored |
| `delete_chunks(c1, c2, *, dimension=None)` | `ChunkDeleteResult` | remove chunks from all three trees so Minecraft **regenerates** them; y ignored, chunk-aligned; see below |
| `spawn_entity(id, x, y, z, *, yaw=0, pitch=0, extra=None, dimension=None)` | `bool` | adds an entity with the structural NBT (exact `Pos`, `Rotation`, **fresh `UUID`**) handled; `extra` for kind-specific tags |
| `entities_in(c1, c2, *, ids=None, dimension=None, limit=None, keep_loaded=False)` | `EntityQueryResult` | the read twin of `spawn_entity`; absent storage is **not** a failure. See below |
| `export_schem(c1, c2, path, *, origin=None, entities=False, biomes=False, version=3, ...)` | `SchemExportResult` | box → `.schem` file; see the schem section |
| `import_schem(source, at, *, anchor="origin", entities=True, paste_air=True, relight=True, heightmaps=True, ...)` | `SchemImportResult` | `.schem` file or `Schematic` → world; see the schem section |
| `save(*, force=False, touch_modified=False, force_all=False, before_write=None)` | `int` | files written |
| `dimension(name=None)` | `Dimension` | defaults to the player's, then overworld |

Reporting: `region_count`, `loaded_region_count`, `chunk_count` (loaded only),
`memory_estimate()`.

`block` arguments accept a `BlockState` or a string like
`"minecraft:oak_stairs[facing=north]"`.

`save()` writes only files whose bytes actually differ, decided by hashing.
Editing one chunk in a 397-file world rewrites one file. Player data is saved
too if it was read and changed, and so are `level.dat` and the split settings
files. `force=True` overrides the session-lock check; `force_all=True`
rewrites even unchanged files.

`before_write` (`beforeWrite` in C#) is called with the absolute path of
every file the save is about to replace, create or remove -- a region's
`.mcc` sidecars, the region itself, then any sidecar it no longer refers to
-- once the save is sure to write that file and before any of them is
touched. An exception from it stops the save there: every file written
before it was offered first, and nothing after it is written, so the edit is
still in memory to save again. It is what a backup taken of each file before
it changes is built on; the Unity app's backups are:

```python
kept = {}
def keep(path):
    if path not in kept:
        kept[path] = open(path, "rb").read() if os.path.exists(path) else None
save.save(before_write=keep)   # a file created by the save is kept as None
```

The settings accessors target the current save layout (Minecraft 26.x), where
weather and the per-dimension day clocks live in `data/minecraft/weather.dat`
and `world_clocks.dat` rather than inside `level.dat`; a save in the layout
before 26.1 is read from `level.dat`'s own fields, as above, and never
written.

**The border is not one of those two.** It belongs to a dimension, not to the
world, and each has its own at
`dimensions/minecraft/<dim>/data/minecraft/world_border.dat` — which is why
`world_border()` takes a dimension the way `day_time()` does. The game writes
those files unconditionally from a world's first save, so on any current save
the file is there and `stored` is True even for a border nobody has moved.

`world_border()` is still the one exception to the refusal rule: a dimension
with no file answers with the **default** border (walls at ±29,999,984)
rather than raising, and flat `Border*` keys in `level.dat` are still read
rather than shrugged off as defaults. The
returned `WorldBorder` carries `center_x`/`center_z`/`size` (the full
diameter) plus the warning and damage settings, derives
`min_x`/`max_x`/`min_z`/`max_z`, and answers geometry:
`contains(x, z)`, `distance_to_edge(x, z)` (signed — negative outside), and
`clamp(x, z, margin=0.0)` for pulling a point back inside the walls.

### `fill` and `FillResult`

Corners may be given in **any order**; the box is inclusive and may span any
number of chunks, sections and region files. Sections landing entirely inside
the box are replaced wholesale with a single-entry palette, which is why a
solid fill is constant-time per section rather than per block.

A fill is **not all-or-nothing**, and the result says exactly what happened:

```python
result.blocks_filled        # int
result.sections_replaced    # int — took the constant-time path
result.chunks_modified      # int
result.missing_chunks       # [(cx, cz), ...]        never generated
result.unreadable_chunks    # [((cx, cz), reason)]   there, but unparseable
result.blocks_out_of_range  # int — outside the world's height, or in the above
result.complete             # bool — everything was written
```

**Check `complete`, and report `unreadable_chunks` separately from
`missing_chunks`.** They mean opposite things to a user.

Ungenerated chunks are skipped, never created — an invented chunk would have
no terrain, heightmaps or lighting.

`keep_loaded=False` (the default) releases each chunk after writing it, so an
arbitrarily large fill stays flat in memory.

### `replace_blocks` and `ReplaceResult`

A state-for-state rewrite: every cell in the box whose state the mapping
names becomes that state's target, and every other cell is left as it
stands. A mapping naming every state in a box is a fill, which is how the
app's Fill is built — what it gains over `fill` is the two rules below.

```python
result = save.replace_blocks((x0, y0, z0), (x1, y1, z1), {
    "minecraft:dirt": "minecraft:coarse_dirt",
    "minecraft:chest[type=left]": "minecraft:chest[type=single]",
})
result.blocks_replaced         # int
result.sections_replaced       # int — collapsed to one entry, as a fill does
result.chunks_modified         # int
result.block_entities_removed  # int — orphaned by a change of block id
result.missing_chunks          # [(cx, cz), ...]        never generated
result.unfinished_chunks       # [(cx, cz), ...]        not finished; left alone
result.unreadable_chunks       # [((cx, cz), reason)]   there, but not writable
result.blocks_out_of_range     # int — in any of the above, or past the world
result.complete                # bool — every block in the box was read
```

- **Keys match as the 26.3 codec reads them**: each state with its block's
  defaults filled in (`BlockState.canonical()`), so `"minecraft:furnace"`
  names `furnace[facing=north,lit=false]` in a chunk of either codec. A key
  without a namespace is the game's. Targets are written canonical too, so
  one target spelled two ways enters a palette once. Two keys naming one
  state with different targets are refused.
- **A chunk the game has not finished is left alone** and reported, as
  `import_schem` leaves one, since the game generates over what is written
  there. A 1.13–1.17 chunk is refused as a write and reported among
  `unreadable_chunks`.
- **Block entities**: where a cell's block *id* changes, its stored block
  entity is dropped; where only properties change (a chest turned) it is
  kept, contents and all.
- The writing order is total, so the same replace writes the same bytes.
  **No aftercare** — `connect_blocks`, `rebuild_heightmaps`,
  `invalidate_poi` and `reset_lighting` are the caller's, as after `fill`.

### `fill_biome` and `BiomeFillResult`

Same shape as `fill` — inclusive box, corners in any order, spans anything,
skips ungenerated chunks, collapses fully-covered sections — but biomes live at
**4×4×4 resolution**, so the counts are in cells and the box is grown outward
to cell boundaries before anything is written.

```python
result = save.fill_biome((1, 69, 1), (6, 70, 6), "minecraft:snowy_taiga")
result.written_box          # ((0, 68, 0), (7, 71, 7)) — the box actually written
result.cells_filled         # int — cells, not blocks
result.sections_replaced    # int — took the constant-time path
result.chunks_modified      # int
result.missing_chunks       # [(cx, cz), ...]
result.unreadable_chunks    # [((cx, cz), reason)]
result.cells_out_of_range   # int
result.complete             # bool
```

**`written_box` is the one to check.** The smallest writable unit is a cell, so
a box that starts mid-cell writes the whole cell — the four blocks either side
of a coordinate share an answer, and there is no finer granularity in the
format. Rather than coerce silently, the result reports the real extent.

This is a genuine 3D box, not a column operation: biomes vary with height (that
is how cave biomes work), so re-climating a surface area top to bottom means
passing the vertical bounds explicitly.

Biome ids are **not validated** against the game's registry — an unknown id
writes cleanly and fails when Minecraft loads the chunk.

Changing a biome does not retroactively change the world. Snow layers and water
are already-placed blocks and stay put; grass and foliage color is client-side
and follows the new biome; weather and mob spawning follow immediately.

### `unfreeze` and `UnfreezeResult`

Rewrites the three states Minecraft's freezing writes, and **nothing else**:

```
minecraft:snow[layers=1]               ->  minecraft:air
minecraft:ice                          ->  minecraft:water[level=0]
grass_block/podzol/mycelium[snowy=true]->  the same state with snowy=false,
                                           where no snow is left above it
```

**This is not an undo, and no save can make it one.** Two writers produce
those states, through the same `Biome.shouldFreeze` / `Biome.shouldSnow`
gates: `ServerLevel.tickPrecipitation` on a cold chunk tick, and worldgen's
`SnowAndFreezeFeature` — `Feature.FREEZE_TOP_LAYER`, shipped as the
`minecraft:freeze_top_layer` placed feature and listed by `snowy_taiga` and
every other cold biome. Generated snow and weather snow are the same bytes,
so over a naturally cold biome this is a **de-icing**: the weather lays back
what it clears, and so would regeneration.

**`snowy` is positional.** `SnowyBlock.isSnowySetting` is
`state.is(BlockTags.SNOW)` on the block *above* — and that tag is
`{snow, snow_block, powder_snow}`, not the layer alone. So the flag is
cleared only where nothing in the tag is left standing above the ground once
the thaw is done, whether that block is outside the box or one this call
deliberately spares. Writing `snowy=false` under a surviving snow_block
would be a state vanilla never produces and never repairs: `updateShape`
fires only on a neighbor change, so the ground would render dirt-sided
forever.

**Only `layers=1` is a freeze product.** `tickPrecipitation` deepens snow to
the `maxSnowAccumulationHeight` game rule and no further, and 26.2 registers
that `registerInteger("max_snow_accumulation_height", UPDATES, 1, 0, 8)` —
default **1**; worldgen places `Blocks.SNOW.defaultBlockState()`, also one
layer. On a default world every `snow[layers=2..8]` was stacked by a player,
so it stays and is reported as `deep_snow_kept`. Pass `deep_snow=True` for a
world that raised the rule.

**What it never touches is the point.** `snow_block`, `powder_snow`,
`packed_ice`, `blue_ice`, `frosted_ice` and `powder_snow_cauldron` all stay
— one live world here holds 1,289,399 snow_blocks and 2,381,441 packed_ice,
and a thaw that ate them would take snowy hillsides and real builds with
them. Most of them freezing never writes at all; the cauldron is excluded by
choice rather than innocence, since `tickPrecipitation` does call
`Block.handlePrecipitation` and `CauldronBlock.handlePrecipitation` fills a
cauldron with powder snow on `Precipitation.SNOW`. A container's level is
not ground covering.

```python
result = save.unfreeze((x0, y0, z0), (x1, y1, z1))
result.snow_removed      # int — layers taken off
result.ice_melted        # int
result.snowy_cleared     # int — the cosmetic flag on the ground below
result.deep_snow_kept    # int — snow[layers=2..8] left standing
result.chunks_modified   # int
result.floating_water    # int — melted ice that was standing over air
result.missing_chunks    # [(cx, cz), ...]
result.unreadable_chunks # [((cx, cz), reason)]
result.complete          # bool
```

`floating_water` is a **report, never a refusal**: a frozen lid over a drained
lake thaws into water hanging in the sky, which is invisible from a top-down
render and rarely what anyone pictured. The block below is read from the
world, not from the box, so a lid on the box's own floor is still judged
correctly — as is the block above, for the `snowy` question.

Like `fill`, this writes states and stops — **no aftercare runs**. Afterwards:
`connect_blocks` (ice is a sturdy full cube, so melting it changes how the
fences, walls and panes beside it connect), `rebuild_heightmaps` (a snow layer
is part of `WORLD_SURFACE` — the motion-blocking maps sit under it — so
removing it lowers that surface), and `reset_lighting`.

### `create_chunks` and `ChunkCreateResult`

```python
r = save.create_chunks(corner1, corner2, *, dimension=None, biome=None)
r.chunks_created, r.created_chunks, r.regions_created
r.existing_chunks, r.unfinished_chunks, r.unreadable_chunks   # left as they stand
r.data_version, r.min_y, r.max_y, r.biome, r.complete
```

Builds a finished chunk of air wherever the box (block corners, y ignored)
has none, so a world can be built on past the ground the game generated. A
built chunk is the game's finished chunk with nothing in it -- every key the
game writes, in its order, held to both fixtures' -- with `Status`
`minecraft:full`, so the game loads it as it stands and never generates over
it; one biome (the dimension's own when not given: plains, nether wastes, the
end); the height of the dimension's other chunks (`Dimension.section_range`,
or the vanilla height of a vanilla dimension holding none); heightmaps rebuilt;
and `isLightOn` 0 with no light stored, so the game lights it on first load,
as after `reset_lighting`. Region files are made as needed, and a vanilla
dimension the world never visited is made too; `save()` writes them.

Only a chunk with **no terrain record** is built. A finished one is left and
listed in `existing_chunks`; one the generator set aside part-way in
`unfinished_chunks` (the game finishes it on load -- `delete_chunks` first to
build there afresh); one that cannot be read in `unreadable_chunks`, with
why. `complete` says every chunk the box covers is now finished. Chunks are
stamped with level.dat's DataVersion, or 26.3's (5023) for a newer world,
which the game upgrades on load; a world last saved before 26.2 (4903) is
refused, since its chunks may carry fields these do not. Build on them with
`fill`, `set_block` and `place_block`, then `rebuild_heightmaps`, as after
any edit.

### `delete_chunks` and `ChunkDeleteResult`

Removes whole chunks so Minecraft generates them again on next visit. Block
coordinates in any order, **y ignored** — a chunk is a column and the whole
column goes.

```python
result = save.delete_chunks((5, 0, 5), (20, 0, 20))
result.deleted_box            # ((0, 0), (31, 31)) — chunk-aligned, no y
result.chunks_deleted         # int — terrain records removed
result.chunks_absent          # int — nothing there to remove
result.entity_chunks_deleted  # int
result.poi_chunks_deleted     # int
result.regions_touched        # int — (rx, rz) places, not files
```

**All three trees are cleared, and that is not optional.** `EntityStorage`
reads `entities/` without consulting the terrain chunk, so leftover villagers
and armor stands reappear floating in fresh terrain; `PoiSection.refresh`
returns early on a persisted `Valid: true`, so stale points of interest are
never rescanned and villagers keep claims on beds that no longer exist.

Deleting never decodes a chunk, so one too damaged to read deletes like any
other — usually the reason for reaching for this. An emptied region file is
left in place: a file with holes in it is exactly what the game writes for
ground nobody has visited.

**This is not an undo.** Regeneration runs the *current* version's generator,
so what comes back is new terrain, not what was there before. Take a
`Worksite` snapshot first.

### `reset_lighting` and `LightResetResult`

**Chunk light is baked.** The game stores computed light per section and
trusts it for as long as a chunk's `isLightOn` flag stands, so editing blocks
leaves light that describes the world as it was: a torch placed in a newly
carved room lights only its own block, a removed roof leaves the room dark at
noon. After editing an area, hand its lighting back to the game:

```python
save.fill((x0, y0, z0), (x1, y1, z1), "minecraft:stone_bricks")
save.reset_lighting((x0, y0, z0), (x1, y1, z1))   # same corners will do
save.save()
```

Light is stored per chunk, so whole chunks are reset and the y coordinates
are ignored; corners may be in any order. `neighbors=True` (the default)
widens the reset one chunk outward, because light propagates up to 15 blocks
across borders and a relit chunk beside an unmarked one pulls the neighbor's
stale darkness back in.

```python
result.chunks_reset        # int — flag cleared, stored arrays dropped
result.arrays_removed      # int — stale BlockLight/SkyLight arrays removed
result.chunks_skipped      # int — ungenerated: nothing to reset, not an error
result.unreadable_chunks   # [((cx, cz), reason)] — stale light that CANNOT be cleared
```

Ungenerated chunks are skipped on purpose — generation lights a chunk when it
builds it. Unreadable chunks (damaged, or a 1.17 chunk, which is read and
never written) are the ones to look at:
their stale light cannot be cleared, so an edit beside one may keep a dark
seam at the border. The per-chunk primitive is `Blocks.reset_lighting()`.

### `place_block` — building, as opposed to writing

`set_block` writes exactly the state it is given, which is the right
primitive for a format library and the wrong tool for construction: vanilla
*items* place more than one block and derive state from surroundings. For
building code, use `place_block` and never think about either problem:

```python
save.place_block(x, y, z, "minecraft:red_bed[facing=south]")  # both halves
save.place_block(x, y, z, "minecraft:oak_door[facing=west]")  # both halves
save.place_block(x, y, z, "minecraft:oak_fence")              # connected, and
                                                              # neighbors updated
```

It completes multi-block things (beds toward their `facing` — the given
cell is the foot unless `part=head` says otherwise; doors and tall plants
upward, the tall plants being every two-block plant the game places whole:
sunflower, lilac, rose bush, peony, tall grass, large fern, pitcher plant,
small dripleaf and tall seagrass, while a pitcher crop is planted as one
block), refuses **atomically** when any cell would fall outside the
generated world (a bed can never end up half-placed), settles
neighbor-derived state on the placed cells *and* their neighbors using
the same validated rules as `connect_blocks`, and sets `waterlogged` the
way the game's own placement does (explicit `waterlogged=` is respected;
fence gates never waterlog — vanilla's rule). A capable block waterlogs
only into a water **source**: `water[level=0]` (a bare `minecraft:water`,
as a legacy chunk stores one, is that default), seagrass, tall seagrass,
kelp, a bubble column, or a block that is itself waterlogged. Flowing
water (`level` 1–7) and falling water (8–15) are another fluid, and a
stair dropped into a stream displaces it and stays dry, since waterlogging
it would write a new source into the stream. The game's own exceptions
come with it: corals and the conduit also take falling water, the small
dripleaf takes any water (each half its own cell's), and the light block
and a double slab never waterlog. A campfire that takes water is placed
out, `lit=false`, unless `lit=` is given. A block wet by default — a sea
pickle, a coral, a conduit — placed where its rule finds no water is
written `waterlogged=false`, as the game writes it, rather than left bare
to read back wet. When a block that can take water is placed with
`waterlogged` unsaid, water under it at a level, or a `waterlogged`, the
game does not have raises `NBTError` with nothing written; a block that
takes none (stone, a double slab, the light block) never reads it. A
`craft.Builder` plan reads that water for every placement it holds before
its first write, so the refusal never comes halfway through one. Both cells of a
pair are overwritten; this is an editor, not survival mode, and no support
rules are simulated. `connect_blocks` remains the batch pass for areas
built with raw `set_block`/`fill`.

### `connect_blocks` and `ConnectResult`

**Connection states are stored, not derived.** A fence's `north=true`, a
wall's post flag, a stair's corner `shape` — vanilla recomputes these only
on a *block update*, so blocks written directly into a save render exactly
as stored: bare fences stand as disconnected posts until something nudges
them. After placing any fences, walls, glass panes, iron bars, fence gates
or stairs, hand their derived state back the same way lighting is handed
back:

```python
save.fill((x0, y, z0), (x1, y, z1), "minecraft:oak_fence")
save.connect_blocks((x0, y, z0), (x1, y, z1))
save.reset_lighting((x0, y, z0), (x1, y, z1))
```

The pass rewrites exactly what a vanilla neighbor-update would: fence sides
(wooden with wooden, nether brick with nether brick, gates along their bar,
full solid faces — a stair's back and a double slab included), wall sides as
`none`/`low`/`tall` and the post flag the way the game's `WallBlock` derives
them from the block above (below), pane and iron-bar sides, gate `in_wall`,
and stair corner shapes per the game's own `getStairShape`. `waterlogged` is
deliberately untouched — it is placement history, not derived state; set it
when placing into water.

A wall reads the block above it by shape, not by name. A side it joins runs
`tall` where that block's collision underside covers the side's strip — a
solid block, a leaf, a bottom slab, the joined side of a wall above — and
`low` otherwise (a top slab, an open trapdoor, a torch, the sky). The post
follows `WallBlock.shouldRaisePost`: a wall above with its post up raises
this one's outright; an end, a lone block, a T, an L or a lopsided cross
raises one; a run tall on both sides of an axis is flush; and otherwise the
post rises only under what is in `wall_post_override` (torches, signs,
banners, pressure plates) or covers the post's center (a lantern, a pot).
Those answers come from `heaton_voxel.wall_cover`, a table **generated**
from the game's own shapes and tags by `tools/generate_wall_cover.py`
(edit neither twin by hand), which also asks the game's own `updateSides`
and `shouldRaisePost` what a wall under every block state becomes, joined
on every set of its sides, and refuses to keep a table under which the rule
here answers otherwise even once.

The rules live in `heaton_voxel.connect` and are game knowledge, validated
against **22,616 vanilla-placed blocks with zero disagreements**; unknown
blocks count as "not solid", so drift errs toward a free-standing post,
never an arm reaching at nothing -- and an unknown block above a wall
raises its post and runs no side tall. Give the box a one-block margin around
new construction — neighbors are read beyond it, but only blocks inside
are rewritten. Sections with nothing connectable in their palettes are
skipped, so sweeping a large build costs little.

```python
result.fences_updated / walls_updated / panes_updated / gates_updated / stairs_updated
result.blocks_scanned        # connectable blocks examined
result.total_updated
result.missing_chunks, result.unreadable_chunks, result.complete
```

### `histogram` and `HistogramResult`

The census counterpart of `fill`: same inclusive box, corners in any order,
spanning anything, with the same honest accounting — a count that treated
unreachable ground as zero would look exactly like "nothing there".

```python
result = save.histogram((x0, y0, z0), (x1, y1, z1))
result.counts               # {BlockState: int}
result.top(10)              # most common first
result.blocks_counted       # int
result.chunks_scanned       # int
result.missing_chunks       # [(cx, cz), ...]
result.unreadable_chunks    # [((cx, cz), reason)]
result.blocks_out_of_range  # int
result.complete             # bool — every block in the box was counted
```

A section entirely inside the box holding one uniform state is counted in
constant time, so sweeping a large volume is cheap; everything else unpacks
once per section. `keep_loaded=False` (the default) releases chunks as it
goes, so a census over half a world stays flat in memory.

### `find_blocks` and `FindResult`

```python
found = save.find_blocks((x0, y0, z0), (x1, y1, z1), "_door", match="suffix")

found.matches            # [(x, y, z, BlockState), ...] in a fixed order
found.positions()        # just the coordinates, same order
found.counts             # {BlockState: n}
found.top(5)             # the five most common matched states
found.truncated          # True if `limit` cut the search short
found.sections_scanned   # sections whose indices had to be unpacked
found.sections_skipped   # sections dismissed on their palette alone
found.complete           # False if anything was missed, truncation included
```

`match` picks how a state is compared: `"name"` (the default) ignores
properties, so `"oak_door"` finds every hinge and half; `"state"` compares
the whole thing, property order irrelevant; `"suffix"` and `"substring"`
take a family at a time (`"_stairs"`, `"glass"`). Unqualified names are read
as `minecraft:`, the way the game's commands read them.

The speed is the palette short-circuit: a section stores its distinct states
in a palette *before* the bit-packed indices, so a section whose palette
holds nothing wanted cannot hold the block and is never unpacked. Sweeping a
region for something rare costs a fraction of reading it. `sections_skipped`
next to `sections_scanned` says how much that pruning bought — a low ratio
means the search terms were too broad to prune on.

Order is chunk by chunk, then section, then y, z, x within it — fixed, so two
runs agree and two ports can be compared line for line. Sort it yourself if
you want world order. Sections the world stores nothing for are air and are
skipped, so a search for air will not report the implicit air of a section
that was never written.

### `entities_in` and `EntityQueryResult`

```python
found = save.entities_in((x0, y0, z0), (x1, y1, z1), ids="villager")

found.entities                 # [(id, x, y, z, TagCompound), ...]
found.counts                   # {"minecraft:villager": n}
found.chunks_without_storage   # chunks holding no entities — not a failure
found.unreadable_chunks        # storage that exists and cannot be read
found.complete                 # False only on unreadable storage or truncation
```

Membership is by the block an entity stands in: the stored doubles are
**floored**, so an entity at `x = -0.3` is in block `-1`. Each entry carries
the whole compound, so `VillagerData`, `CustomName` and the rest need no
second pass.

A chunk with no entity storage is a complete answer, not a partial failure —
absent storage means no entities — so those are counted and nothing more.
Only storage that exists and cannot be read makes a result incomplete.

The tags handed back are the live ones while their chunk is loaded, and this
releases each chunk as it finishes; a tag edited after its chunk was released
is edited on a tree the world no longer reads. Pass `keep_loaded=True` when
you mean to modify what comes back.

### `Dimension`

```python
dim = save.dimension()                 # or save.dimension(NETHER)
dim.region(rx, rz, create=False) -> NBT | None  # opens on demand; create builds one in memory
dim.chunk(cx, cz)       -> Chunk | None
dim.blocks(cx, cz)      -> Blocks | None   # None = ungenerated; RAISES if unreadable
dim.writable_blocks(cx, cz) -> Blocks | None  # the same, RAISING ReadOnlyChunk for 1.17
dim.chunk_status(cx, cz) -> str | None     # "minecraft:full" or an earlier stage
dim.surface_y(x, z, heightmap=...) -> int | None   # global block coordinates
dim.release(cx, cz)                        # flush edits, free the tree + caches
dim.load_all()                             # open everything
dim.loaded_regions()    -> iterator        # cheap: only what is already open
dim.section_range()     -> (low, high)     # sections a chunk here spans, read off one
dim.create_chunk(cx, cz, *, data_version, biome, sections=None) -> Blocks
                                           # one finished chunk of air; refuses where one stands
for region in dim: ...                     # opens every region file
```

Entities live apart from terrain — `entities/r.X.Z.mca` beside `region/`,
same Anvil format — and the dimension addresses those too:

```python
dim.entity_region(rx, rz, create=False) -> NBT | None
dim.entity_chunk(cx, cz, create=False, data_version=None) -> Chunk | None
```

An entity chunk's root is `{Position, DataVersion, Entities}`; positions
inside the entity compounds are absolute world doubles. `None` means the
world stores no entities there. `create=True` builds the chunk (and its
region file if needed) in memory; creation requires a `data_version`, and
`MinecraftSave.save()` writes any entity region that changed.

Constants: `OVERWORLD`, `NETHER`, `END`. Beneath `create_chunk`,
`heaton_voxel.blocks.empty_chunk(cx, cz, *, data_version, min_section,
max_section, biome)` is the chunk compound itself, for `data_version` from
`EMPTY_CHUNK_MIN_DATA_VERSION` (4903) to `EMPTY_CHUNK_MAX_DATA_VERSION` (5023).

---

### Reversible edits

`save.begin_edit()` (`BeginEdit()` in C#) starts an in-memory history record.
Finish it with `record.complete()`, or call `record.rollback()` after a failed
operation. An incomplete edit prevents `save.save()` and nested edits.

```python
record = save.begin_edit()
try:
    save.fill((0, 64, 0), (5, 70, 5), "minecraft:stone")
    record.complete()
except Exception:
    record.rollback()
    raise
save.save()
record.undo()        # restores memory, including after Save
save.save()          # writes the restored state
record.redo()        # reapplies the completed edit in memory
```

Records capture loaded NBT files and files first opened during the operation:
terrain, entities, POI, lighting, player data, and world settings. The original
file must remain readable until completion. Hold the world lock throughout an
editing session. Reacquire cached chunk/block accessors after beginning or
restoring a record. Apply records in stack order; the caller owns the stack,
redo branching, and its memory budget. `changed`, `paths`, and `byte_size`
(`Changed`, `Paths`, `ByteSize`) describe a completed record. History lasts only
in memory and is not a persistent backup. Undoing a region file the edit made
(`create_chunks`, `entity_chunk(create=True)`) before a save leaves no file;
undoing it after the save leaves an empty one, since it restores contents, not
the former absence of a file. A dimension `create_chunks` made from nothing
stays known to the save, holding no chunk. A save that refuses every write --
one in the layout before 26.1 -- records nothing, and still refuses `save()`.

---

## `heaton_voxel.schem` — schematics: Sponge, vanilla structures, Litematica

Boxes of world as files, in the three formats builds are shared in:

* **Sponge `.schem`** -- what WorldEdit's `//copy`/`//schem save` produce and
  `//paste` consumes. Container versions 2 and 3 are read; either is written
  (3, the current spec, by default).
* **Vanilla structure `.nbt`** (`schem/structure.py`) -- what a structure
  block saves and a data pack's jigsaws assemble, held to the game's own
  `StructureTemplate` by `tests/test_structures.py`. A cell the file leaves
  out is one the structure leaves as it stands, and reads as
  `minecraft:structure_void`; a `structure_void` cell is written out of the
  list. States carry every property, `{id, properties}` from 26.3 and
  `{Name, Properties}` before. Of several palettes (`palettes`), the first is
  read and `source_palettes` says how many there were. No offset, no biomes.
* **Litematica `.litematic`** (`schem/litematic.py`) -- version 5 to 7 read,
  7 written, held to litemapy by `tests/test_litematic.py` and
  `tools/check_litematic.py`. Regions may have negative sizes and several
  regions read as one box, the cells no region covers `structure_void`;
  pending ticks are not carried, and one region, `Unnamed`, is written.

`Schematic.load` tells the formats apart by what a file holds; `save` picks
one from the extension (`.nbt`, `.litematic`, anything else `.schem`) or
`format=`. Refused with a clear message: the pre-1.13 `.schematic` format
(numeric ids — a different format, not an old version of this one), Sponge
version 1, Litematica before version 5, any file without a `DataVersion`, and
any `DataVersion` older than 1.18.

**Structure voids.** A paste leaves every `structure_void` cell as the world
has it -- the block's meaning: the game never saves one into a structure, and
places nothing where a structure leaves a cell out -- and counts them in
`result.voids_skipped`. `paste_voids=True` writes them as the block.

```python
result = save.export_schem((x0, y0, z0), (x1, y1, z1), "keep.schem",
                           entities=True)          # SchemExportResult
result = save.import_schem("keep.schem", (x, y, z))  # SchemImportResult
save.save()                                          # nothing on disk before this
```

**Coordinates.** Inside a schematic they are relative to its **minimum
corner** (`x` 0..width-1 east, `y` up, `z` south). The file's `Offset`
records where that corner sat relative to the author's chosen anchor
(`origin=` on export). On import, `anchor="origin"` (default) lands that
anchor at `at` — WorldEdit's `//paste` behavior, and the round-trip
identity: `import_schem(export_schem(c1, c2, origin=p), at=p)` puts every
block back where it was. `anchor="min"` puts the minimum corner at `at` and
ignores the stored offset.

**What a paste leaves behind.** `relight=True` hands the box's lighting
back to the game, and `heightmaps=True` rewrites the four maps of every
chunk whose blocks the paste moved (`result.heightmaps_rebuilt`), so the
game, `surface_y` and every map read the paste rather than the ground under
it. A chunk the game has not finished generating is skipped and listed in
`result.unfinished_chunks`, beside `missing_chunks`: the game generates over
whatever is written there, so it is treated as a chunk that does not exist
yet, blocks, block entities and entities alike, and `complete` is false.

**What travels.** Block entities always ride with their blocks (contents,
text, spawner settings), stripped of their absolute positions and re-stamped
on paste — and a paste *removes* stale block entities from positions it
overwrites. Entities are opt-in on export (`entities=False` default, like
`//copy` without `-e`) and honored by default on import; every pasted
entity gets a fresh `UUID`, remapped consistently across the paste so
leashes, riders and owners inside it stay linked. `Pos`, `TileX/Y/Z`, leash
knots and `sleeping_pos` are translated; other entity-specific absolute
positions (brain memories and such) are copied verbatim. Biomes are v3-only,
opt-in both ways, stored per block and applied per 4×4×4 cell — cells the
box partly covers are recolored whole, as with `fill_biome`.

**Rotation and mirroring.** `import_schem(..., rotate=90, flip="x")` (flip
first, then rotate) transforms the copy on the way in; standalone,
`schematic.rotated(degrees)` and `schematic.flipped(axis)` return
transformed copies. Rotations are multiples of 90° about the vertical axis,
positive **clockwise seen from above** (north faces become east), applied
about the schematic's *origin* — so export with `origin=p`, paste rotated at
`p`, and the build swings around that point exactly like WorldEdit's
`//rotate` + `//paste`. Flips run along `"x"` (east↔west), `"z"`
(north↔south) or `"y"` (upside down: halves, floor/ceiling attachments and
hanging things follow). Everything turns together: block positions, the
direction-bearing state properties (`facing`, `axis`, sign `rotation`, rail
and stair shapes, per-face booleans like a wall's `north=tall`, mirrored
chirality — stair corners, chest halves, door hinges), block entities,
biomes, and entities (position, yaw, `Motion`, `block_pos`/`TileX/Y/Z`
anchors, frame and painting facing bytes). The property vocabulary is game
knowledge and value-guarded: states it cannot cover pass through unchanged,
and any that still *look* directional are itemized on
`transform_warnings` (on the returned schematic and on the paste result) —
reported, never guessed at.

**Turning one state.** The vocabulary's three functions are public, in
`heaton_voxel.schem` and at the top level, under the names their C# twins
have (`Transforms.PropertiesRotatedCw`, `PropertiesMirrored`,
`DirectionalLeftovers`), for turning a state with no schematic around it:

```python
from heaton_voxel import properties_mirrored, properties_rotated_cw, directional_leftovers

properties_rotated_cw({"facing": "north", "half": "top"})   # {"facing": "east", "half": "top"}
properties_mirrored({"facing": "east", "hinge": "left"}, "x")  # {"facing": "west", "hinge": "right"}
directional_leftovers([BlockState("mod:thing", {"side": "north"})])  # one warning
```

Each takes a property dict and returns a new one in the same key order (a
per-face key, `north=true`, renamed where it stands), leaving its argument
alone; `directional_leftovers` takes any iterable of `BlockState` and
returns the `transform_warnings` a turn of them would carry.

**Refusals and accounting.** Export **refuses** a box touching ungenerated
or unreadable chunks unless `allow_incomplete=True` (the format cannot say
"unknown"; air-filled gaps are then itemized on the result). Import mirrors
`fill`: missing chunks are skipped and reported, never invented, and
`result.complete` says whether everything landed. A schematic stamped by a
newer Minecraft than the world is refused — the game will not data-fix
blocks inside chunks that already claim to be current; assigning
`schematic.data_version` is the deliberate override. `paste_air=False`
masks the schematic's air (terrain shows through); `relight=True` (default)
hands the pasted box's lighting back to the game and reports it as
`result.lighting`.

### `Schematic` standalone

```python
Schematic.load(path)                    # .schem v2/v3, structure .nbt, .litematic
schematic.save(path, version=3, format=None)  # format from the extension;
                                        # deterministic bytes; v2, .nbt and
                                        # .litematic refuse biomes
schematic.source_format                 # "sponge", "structure", "litematic"
Schematic.new(w, h, l, data_version)    # data_version required, never assumed
Schematic.from_save(save, c1, c2, ...)  # copy without writing a file
schematic.paste(save, at, ...)          # same rules as import_schem
schematic.rotated(90)                   # new Schematic, turned clockwise
schematic.flipped("x")                  # new Schematic, mirrored
schematic.get_block(x, y, z)            # also schematic[x, y, z], get/set
schematic.indices()                     # bulk read: palette indices, y,z,x order
                                        # cell (x,y,z) at (y*length+z)*width+x
schematic.set_block_entity(x, y, z, TagCompound({...}))   # needs "id" inside
schematic.set_biome(x, y, z, name)      # first call fills the whole box
schematic.histogram()                   # {BlockState: count}, air included
schematic.size / volume / palette / offset / data_version / metadata
schematic.block_entities                # {(x, y, z): TagCompound}
schematic.entities                      # entity NBT with *relative* Pos
```

Memory is ~8 bytes per cell. A version-2 file's column biome data is noted
(`had_2d_biomes`) but never applied — it has no vertical dimension to give —
and asking for it raises. `python -m heaton_voxel schem` has `info`,
`export`, `import` and `transform` subcommands; `import` is a dry run
without `--write`, and `transform` rewrites a file rotated or mirrored
(`--rotate 90`, `--flip x`), keeping the input's container version.

Every coordinate the CLIs take — `schem --box/--at/--origin`, `world
--fill`, `render --area/--slice` — accepts the game's relative notation via
`save.resolve_coordinates`: `~` is the player's block position on that axis,
`~5` five past it, `~-3` three short, mixable with absolutes per component.
So `schem import <world> keep.schem --at '~' '~' '~'` is `//paste` where the
player last stood. Quote the tokens (`'~'`, `'~-5'`) — an unquoted `~` is
expanded by the shell into the home directory, which the resolver detects
and refuses with exactly that hint. Worlds without player data still take
absolute coordinates; a `~` there fails saying why.

---

## `heaton_voxel.blocks` — one chunk

```python
b = Blocks(chunk)          # 1.18+ read/write; 1.13..1.17 read-only; else NBTError
b[0, 70, 0]                # -> BlockState   (x, z are 0-15; y is world)
b[0, 70, 0] = "minecraft:glass"
```

| member | notes |
| --- | --- |
| `get(x, y, z)` / `set(x, y, z, state)` | `IndexError` if outside the chunk |
| `get_biome` / `set_biome` | biomes are per 4×4×4 cell |
| `block_light(x, y, z)` / `sky_light(x, y, z)` | stored 0–15 light, or `None` where no array is stored — absent means "the game will recompute", **not darkness**; bulk readers use the `Section` methods |
| `surface_y(x, z, heightmap="MOTION_BLOCKING")` | chunk-local lookup; unpacked once and cached per accessor |
| `drop_heightmap_cache()` | after rewriting the `Heightmaps` tags in place, so `surface_y` sees the rewrite |
| `status` | generation stage, e.g. `"minecraft:full"`; anything else has no surface yet |
| `reset_lighting()` | clear `isLightOn`, drop stored light; the game relights on load |
| `world_x`, `world_z` | the chunk's origin in global coordinates |
| `min_y`, `max_y` | the world's vertical range in this chunk: `yPos * 16` (the floor every heightmap counts from) up to the top of the highest section carrying blocks. The game stores light one section past each end of the world; a section of light alone is no part of the range, so block and biome access raises `IndexError` there as anywhere outside it, and only `block_light`/`sky_light` reach it |
| `sections` | `{section_y: Section}` |
| `histogram()` | `{BlockState: count}` — decodes everything |
| `prune()` | drop palette entries editing has orphaned |

Writes go **straight through** into the NBT: setting a block usually rewrites a
single 64-bit word. There is no buffer to flush.

**Two palette codecs, chosen per chunk.** From Minecraft 26.3 -- DataVersion
`MODERN_STATE_DATA_VERSION` (5006) and later -- a palette entry is
`{id, properties}` rather than `{Name, Properties}`, a state at its block's
*default* is stored as the bare id (`minecraft:water` is `water[level=0]`,
`minecraft:grass_block` is `snowy=false`), and a list holding both forms wraps
each bare id as `{"": id}` so the NBT list stays one tag type. `Blocks` reads
`DataVersion` once (`b.modern_states`) and every container in the chunk
decodes and encodes in that chunk's form, so a world played across the
update is written back the way the game that made each chunk will read it.
Decoding **restores the defaults**, from `heaton_voxel.block_defaults`
(generated from the game's own `reports/blocks.json` by
`tools/generate_block_defaults.py`; both twins carry it), so a 26.3 chunk
reads exactly as an older one: every property present, in name order.
Encoding writes bare only what equals its defaults, and fills a partial
state (`sea_pickle[pickles=4]`) before storing it, so `water` and
`water[level=0]` are one palette entry as they are one block to the game.
Older chunks are untouched by all of this: what they store is what they
mean, and it is written back byte for byte.

**Minecraft 1.17 chunks are read, never written.** A chunk 1.17 or 1.17.1
saved (DataVersion `LEGACY_MIN_DATA_VERSION`..`LEGACY_MAX_DATA_VERSION`,
2724..2730; `b.legacy`) keeps everything under `Level`, stores a section
only where it holds something, and runs from y=0 to 255. `Blocks` reads it
as any other: `min_y`/`max_y` are 0 and 255, every section from 0 to 15 is
in `sections` (one 1.17 left out holds one entry of air), its states are
the ones it stored (`minecraft:grass` included -- craft's `RENAMED` reads it
as `short_grass`), `status` is as stored (`"full"`, no namespace), its
heightmaps count from 0, and its biome numbers are named the way the game's
own upgrade names them (`heaton_voxel.legacy_biomes`: `mountains` reads as
`windswept_hills`). Every write raises `ReadOnlyChunk`, an `NBTError`: the
accessor's `set`, `set_biome`, `reset_lighting` and `prune`, every
container's mutators, and every world writer, which asks
`Dimension.writable_blocks` and so reports or refuses a 1.17 chunk the way
it always reported one it could not read -- `set_block` and `place_block`
return `False`, the bulk writers name it in `unreadable_chunks` with the
reason, `spawn_entity` raises, an export refuses it or (with
`allow_incomplete=True`) exports air there, and a paste skips its blocks and
its entities. `b.refuse_legacy(doing)` is the check, for code of your own
about to write. Read against the game's own upgrade of the same chunks,
`tests/test_legacy.py`, every block, biome and surface agrees.

**So are chunks Minecraft 1.13 to 1.16 saved** (`LEGACY_MIN_DATA_VERSION`
is 1519, 1.13's release; `legacy_release(data_version)` names the release a
chunk belongs to), the same shape with three differences, each at the
schema of the game's own fix for it: before `LEGACY_ALIGNED_DATA_VERSION`
(2527) a section's indices and a heightmap's entries run on across longs,
a value straddling two where it falls on the seam (`BitStorageAlignFix`),
and are repacked once on read; before `LEGACY_CELL_BIOMES_DATA_VERSION`
(2202) a chunk keeps one biome a column, 256 of them, and each 4x4x4 cell
reads the column at its center, (2, 2) in (`ChunkBiomeFix`); and `finished`
reads the stage names of the chunk's day, `postprocessed` or `fullchunk`
before 1905 and `fullchunk` until 1911. Before 2681 a chunk keeps its own
entities in `Level.Entities`: `b.legacy_entities` is that list (`None` for a
chunk that keeps them in `entities/`), and `entities_in` reads it where the
dimension has no entity storage for the chunk. The 1.13 snapshots before
1519, and anything older, are refused. `tests/test_legacy.py` holds all of
it to the game's upgrade of OldWorld's 1.15.2 and 1.16.5 chunks and of
chunks stamped as 1.13 and 1.14 saved them, cell by cell, and the
per-state upgrade of every palette entry.

### `BlockState`

Immutable and hashable, so it works as a dict key for tallies.

```python
BlockState.parse("minecraft:oak_stairs[facing=north,half=bottom]")
state.name          # "minecraft:oak_stairs"
state.properties    # {"facing": "north", ...}  (a copy)
state.with_properties(facing="south")
str(state)          # round-trips through parse()
state == "minecraft:stone"    # True only when it has no properties
state.canonical()   # defaults filled in, name order: the 26.3 identity
BlockState.from_nbt(tag)        # any palette form; defaults restored for 26.3 forms
state.to_nbt(modern=True)       # TagString for a default state, else {id, properties}
```

`from heaton_voxel.block_defaults import default_properties` answers
"what is this block's default state" for 884 blocks -- a fresh dict in name
order, empty for a block with no properties or one the table has never
heard of (which reads its bare id as a state with no properties, the
harmless direction to be wrong in for a block newer than the table).

### `Section` and `PalettedContainer`

`section.blocks` and `section.biomes` are `PalettedContainer | None`:

```python
c.palette                 # list of values, in index order
c.bits                    # bits per packed index
c.get(i)                  # one cell, without unpacking the array
c.indices()               # all indices — the expensive path
c.histogram()
c.set(i, value)           # patches one word
c.set_many(positions, v)  # unpacks and repacks once
c.set_all(values)         # replace every cell, rebuilding the palette once
c.fill_all(value)         # collapse to a single-entry palette, no array
c.prune()
```

`section.block_light()` and `section.sky_light()` unpack the stored light
arrays — one 0–15 value per cell, cached — or return `None` where the game
stores none; this is the bulk path a renderer shades from.

Free functions: `unpack_indices(longs, count, bits)`,
`pack_indices(values, count, bits)`, `unpack_nibbles(data)` (light arrays:
two 4-bit levels per byte, low nibble first), `bits_for_palette(size,
minimum)`.

`AIR_BLOCKS` is what the game calls air, its own `#minecraft:air` tag:
`air`, `cave_air` and `void_air`. It is the one set the deterministic
tier tests emptiness with (`schem.AIR_BLOCKS` is the same object), so a
cave reads as empty as the sky does.

**Heightmaps are derived too**: `derive_heightmaps()` reads all four off the
blocks, `{name: 256 heights}` as the game stores them, by
`heightmap_mask(state)` (one bit per `HEIGHTMAP_NAMES` entry: `WORLD_SURFACE`
anything but air, `OCEAN_FLOOR` a block the game's
`blocks_motion_in_heightmap` tag names, and the two motion-blocking maps a
block their tags name or a state that holds fluid; the generated table is
`heightmap_blocks`). `rebuild_heightmaps()` writes them in the game's order,
and `stale_heightmaps()` says how many columns of each map the chunk stores
disagree with its blocks, `{}` for a chunk whose maps are right; a map it does
not store is not stale, since the game derives a missing one when it loads
the chunk. All three refuse a 1.17 chunk. `finished` is whether the game
finished generating the chunk, by `chunk_finished(status)`.

**Heightmaps are wrapped**: `surface_y(x, z, heightmap=...)` on `Blocks`
(chunk-local), `Dimension` and `MinecraftSave` (global). This used to be a
raw-tag recipe every consumer copied — four examples grew four subtly
different versions before it moved here. The lookup is still what it always
was, `unpack_indices(chunk.data["Heightmaps"][name], 256, heightmap_bits(...))`,
unpacked once per accessor and cached; a map whose length contradicts the
chunk's height raises rather than unpacking nonsense.

The names, in `HEIGHTMAP_NAMES`: `WORLD_SURFACE` is the topmost non-air block
(the flower you stand *in*); `MOTION_BLOCKING` the topmost thing that stops
you (the block you stand *on* — water counts, torches do not);
`MOTION_BLOCKING_NO_LEAVES` the same ignoring foliage; `OCEAN_FLOOR` the
topmost solid ignoring fluids — the seabed. A partly generated chunk has
**none of them** — `surface_y` reports `None`; treat that as "no surface
here" and skip, do not scan.

---

## `heaton_voxel.nbt` — files and tags

```python
region = NBT.load(path, *, eager=False)    # .mca/.mcr or a standalone .dat
region.save(path=None, *, force=False, touch_modified=False, skip_unchanged=False)
region.to_bytes()          # no filesystem side effects
region.is_modified()       # hash compare against what was loaded
region.from_disk           # loaded from a file, vs built in memory
region.chunk(x, z)         # region-local 0-31
```

`kind` is `"region"` or `"nbt"`. Regions expose `chunks`; standalone files
expose `root` and `root_name`.

### `Chunk`

| member | notes |
| --- | --- |
| `data` | root `TagCompound`, decoded on first access; `None` if unparseable |
| `parse_error` | why, if it would not decode |
| `loaded` | is the tree currently in memory |
| `release()` | flush edits into the compressed bytes, free the tree |
| `compressed_size` | bytes of compressed payload held; 0 for a chunk built in memory |
| `timestamp`, `compression`, `external` | as stored |

### Tags

Tags subclass their Python counterparts, so a tree reads and mutates normally:

```python
TagByte TagShort TagInt TagLong   -> int
TagFloat TagDouble                -> float
TagString                         -> str
TagList                           -> list   (carries .element_type)
TagCompound                       -> dict
TagByteArray TagIntArray TagLongArray -> array.array
```

Values put into a tree must be tag instances — a bare `5` is rejected with a
message naming the type to use. `encode`/`decode` work on raw bytes; `dumps()`
pretty-prints a tree, and `heaton_voxel.snbt` (below) writes it as the game's
text.

### SNBT — `heaton_voxel.snbt`

```python
parse_snbt(text) -> Tag      # what the game's TagParser.parseFully reads
to_snbt(tag)     -> str      # what Tag.toString() writes
```

SNBT is the text form of NBT that commands, data packs and `/data get`
speak. `parse_snbt` reads the grammar of 1.21.5 and later: decimal, hex
(`0x`) and binary (`0b`) integers with underscores between digits and a
suffix naming the type and, if it likes, the signedness (`255ub` is the byte
-1; hex and binary are unsigned unless an `s` says otherwise), floats with or
without a whole part, a point or an exponent, booleans in any case, quoted
strings with every escape the game has, unquoted words, trailing commas,
arrays (`[B;1b,2b]`, whose elements may be any narrower integer), lists of
mixed types, and the built-ins `bool(x)` (true for any number but zero) and
`uuid("...")` (Java's `UUID.fromString`, leniency and all). Whitespace is
Java's `Character.isWhitespace`, and the game skips it before every piece of
a number literal, though never inside a run of digits, so `- 0 x1F u b` is
the byte 31. Every zero reads as the positive one, as the game's
`FloatTag.valueOf` and `DoubleTag.valueOf` make it.

`to_snbt` sorts compound keys as Java sorts strings (by UTF-16 code unit),
quotes a key only when it must, quotes each string in whichever quote it
needs fewer escapes in, and spells every float and double as Java's
`Float.toString` and `Double.toString` do (`1.0E7f`, `4.9E-324d`), computed
in exact arithmetic. A list of mixed types is stored as the game stores it —
compounds, each item that is not already a plain compound wrapped under the
empty key — and `to_snbt` unwraps those wrappers as the game's reader does.

Both are held case by case to the game's own answers in `fixtures/snbt.json`
(`tools/probe_snbt.py`). Three inputs the game reads are refused, each with
its reason: a `\N{name}` escape (the JDK's Unicode name table is not
carried), nesting past `MAX_DEPTH` (512, where the game's own binary reader
stops), and a `uuid()` digit outside ASCII. `python -m heaton_voxel nbt FILE
--snbt` prints a file's root as SNBT, and a region's chunk with `--dump X,Z`.

### Filesystem helpers

```python
world_is_open(world_root) -> bool     # is Minecraft holding session.lock
find_world_root(path)     -> str|None # walk up to the save directory
atomic_write(path, data)              # temp file, fsync, rename — never truncate
```

**Call `world_is_open()` before writing** if you are not going through
`MinecraftSave.save()`. A world open in the game keeps its own chunk cache and
will discard your edit when it next saves.

---

## `heaton_voxel.craft` — conventions every part of it keeps

What holds across the judged tier, so that what one writer returns, what a
reader reports and what the next writer takes are the same kinds of thing.

**Cells are `(x, y, z)` and columns `(x, z)`.** Global block coordinates,
negatives included, in every argument, result and refusal: a cell is one
block, and a column the vertical line of them a pad, a road's swath, a
scatter's spacing, a roof's footprint and the ground are made of. Pass
whole numbers; a writer that checks refuses a float or a bool by name
rather than round it (a `clearance` of `2.0` once cleared two, and `True`
one). Where a call takes either -- `light_cells`' cells, `water_body`'s
seed, `approach`'s ends -- a column means what the column reader finds
there (the ground, or the waterline over water) and a cell means itself.

**Rectangles are inclusive.** A rectangle is `(x0, z0, x1, z1)` of
columns with both corners in it, so `(0, 0, 15, 15)` is one chunk's 256
columns and `(5, 5, 5, 5)` one column; a box is two `(x, y, z)` corners,
inclusive the same way, as the lower tier's `fill` takes them. The corners
come in either order and are sorted, by every writer and reader but the
first town's shell, `frame_walls` and `clear_interior`, kept writing
exactly what they always wrote and counting from the first corner:
reversed, `frame_walls` writes nothing and `clear_interior` clears a box
past the walls, so give those two the low corner first. An outline that
is no rectangle is any `(x, z)` columns -- `grade`, `basin` and `scatter`
take a `geometry.disc`, a town less its harbor, a column given twice taken
once -- and is planned and written in `(z, x)` order.

**A height names the block you stand on, and your feet are one over
it.** Every height craft takes or gives for a floor is the y of the floor
block: `floor_y` (a `Wall`'s, a `shell`'s and its `stories`, a flight's,
`hang_lamps`'), a pad's `top_y`, a grade's `target`, `pave`'s `y`,
`ground()` and `walk_surface`, `Piece.at`'s `y`, a villager's `y=`, and
`reachable`'s doorstep with `ReachMap.footing(y)`. The cell a body stands
in is `feet_y = floor_y + 1`: a `Piece`'s `dy=0`, the cell a villager
stands in, `SpawnCell.y`, and the cell a floor is lit at -- `pave`,
`light_cells` and `hang_lamps` light the cell over every floor cell to
`light_level`. A wall's rows run from `floor_y + 1` up to its `top`,
inclusive. Water counts the same way from its top: a `basin`'s `surface`,
and the waterline `ground()` gives over a lake, are the y of the top cell
of water. The one height that names the feet is `spawn(x, y, z)`, which
places an entity where `MinecraftSave.spawn_entity` does; the tools that
came before count their own ways (the yellow tower's layouts put the cell
on the floor at `dy=1`, the Roman city's furnishings take the feet cell as
their `y`).

**Every writer plans its whole call, then commits it.** A writer reads
what it needs, plans every cell it will write, and checks the plan whole
before the first of them: the names against the game's registry
(`IllegalBlock`), the chunks and heights it reaches (generated, finished,
inside the world), a worksite's guard, the keep-outs it consults, the
water it would let out (`_contain_plan`), what the game would not keep
(`_audit_plan`) and its own rules -- head room, a walk both ways -- each
as it applies. Only then does it write, in one stated total order (an
outline's columns in `(z, x)`, a `Mix` fill in `y, z, x`), through
`Builder._commit`, since the order a sweep writes is the order of its
palette and so of the bytes. So **a refusal writes nothing and journals
nothing**: one `craft.Refused` (`NoSurface` and `IllegalBlock` are kinds of
it) naming every fault, a plain `NBTError` for an argument wrong in itself,
a `TypeError` for the wrong kind of thing, and the world as it was. A
`Mix` draws nothing from the session's `rng`; a list mix does, and a
refused `pave` or `ornamental_tree` puts it back. `set`, `place` and
`fill` check their own cells the same way and write what they are told,
auditing nothing, which is what `finish()` is for. `finish()` is the
aftercare over what was written, not a writer of a plan: what it refuses
-- a protected zone its connection pass would change, which it puts back
first, and with `strict=True` what its audit finds -- it refuses after
its passes have run, in memory and unsaved.

**What a caller names, the caller has seen.** A writer handed its cells
outright -- `set`, `fill`, a pad's or a grade's or a basin's outline,
`pave`, the shells, a roof, `furnish` -- consults no keep-out, though most
still read what stands in those cells and refuse what somebody built; the cells a
writer places itself -- a road's swath, a railway's line, an approach's
run, an extrusion's band, the seals and rims any of them add -- do, and
`scatter` passes over one (see "A keep-out is a pad or a reservation",
below).

**A pad is a grade.** `grade(outline, target, ...)` is the one earthwork
writer and `level_pad` is it over a rectangle, cut and fill on and the
pad registered, byte for byte as it always built (below). So a pad that
only cuts or only fills, fills with something other than dirt, wears a
mixed surface, has no retaining ring or registers no keep-out needs no
`fill=` or `register=` on `level_pad`: it is a grade,
`grade((x0, z0, x1, z1), y, cut=..., fill=..., subsoil=...,
surface=Mix(...), retain=None, name=None)`, over any columns as well as a
rectangle, and draining besides with `drain=True`. `level_pad` itself
takes a `Mix` surface and ring, and `wall=None` for no ring.

---

## `heaton_voxel.craft.build` — construction, for builders not pipelines

The judged tier (`heaton_voxel.craft`) sits above the deterministic
library, and is imported explicitly because material palettes, roof shapes
and what counts as vegetation are game knowledge and taste — where the
deterministic tier has one correct answer per question, `craft` has
choices, which is also why it is Python-only and outside the C# port. The
`Builder` is a construction session that journals everything it writes.
`heaton_voxel.craft.build` is a package, one module per family (`stairs`,
`vault`, `railway`, `lighting`, `site`, `water`, `shells`, `walls`, `roof`,
`furniture`, `framed`, `planting`, `approach`, `extrude`) with each rule
beside its writer, but every name is imported from `heaton_voxel.craft.build` or
`heaton_voxel.craft`: which file a name lives in is not part of the
contract.


```python
from heaton_voxel.craft import Builder, Mix, ROAD_MIX, LANE_MIX, PLAZA_MIX
from heaton_voxel.craft import MELT_SNOW, NO_SPAWNS, Facade, Frame, Material, Palette, Roof
from heaton_voxel.craft import geometry
from heaton_voxel.craft import CrossSection, Merlon, Piece, Tree

b = Builder(save, seed=7)
b.clear_vegetation((x0, z0), (x1, z1))       # fell trees, strip ground cover
b.level_pad(x0, z0, x1, z1, 64, name="inn")  # terrace + retaining ring
b.level_pad(..., over_water="fill")          # over a pond: filled from its bed
g = b.grade(geometry.disc(9, center=(cx, cz)), 62,  # any columns: cut, fill,
            drain=True, seal="minecraft:cobblestone")   # drain; leaks sealed
g.beside_drained                             # the water the outline cut across
w = b.basin(geometry.disc(9, center=(px, pz)), 62,         # a pond: deepest
            floor="minecraft:sand", lining="minecraft:stone")  # far from shore
w.floor                                      # {(x, z): y}, 0.75 a block per block
b.frame_walls(...); b.floor(...); b.clear_interior(...)
house = b.shell(x0, z0, x1, z1, Facade(STUCCO, pitch=4, phase=0, pier=QUOIN),
                floor_y=64, top=72, stories=(68,), floor=PLANKS,  # walls, stories,
                deck=GRANITE, parapet="minecraft:stone_bricks")    # checked
b.battlement(house.perimeter, house.top + 2,  # on the parapet's row:
             block="minecraft:stone_bricks")  # every corner a merlon
curtain = CrossSection({(-1, 0): STONE, (0, 0): STONE, (1, 0): STONE,
                        (-1, 1): Merlon(STONE), (1, 1): STONE_SLAB},
                       walk=[(0, 0)], footing=COBBLE)  # drawn facing south
b.extrude([(x0, z0), (x1, z0), (x1, z1), (x0, z1), (x0, z0)], curtain)
                                             # on the ground, walked first
b.gable_roof(sx0, sz0, sx1, sz1, 69, "x")     # a shed's gable, on its own walls
b.place(dx, 65, dz, "minecraft:oak_door[facing=north]")   # via place_block
b.window(...); b.fence_rect(..., gate=(gx, gz, "west"))
b.roof(Roof(rings="square", block=TILE, slab=TILE_SLAB, stair=TILE_STAIR),
       (hx0, hz0, hx1, hz1), 73, attic="hollow")  # a hip from walls topped
                                                  # at 72: one rule, every roof
b.window(wx, wy, wz, "x", block="minecraft:tinted_glass") # no pane: as it is
b.furnish([Piece.seat((0, 0), back="north", stair=OAK_STAIRS).at(cx, 64, cz)],
          room=(x0+1, z0+1, x1-1, z1-1), doorstep=(dx, 64, dz))  # walked both ways
b.palette = Palette(roof="minecraft:granite",             # what every legacy
                    gable="minecraft:smooth_sandstone")   # writer reads unasked
b.gable_roof(...)                            # granite stairs and slab, sandstone
b.gable_roof(..., stair=Material("minecraft:bricks"))     # an argument wins
r = b.road([(ax, az), (bx, bz)], width=3, mix=ROAD_MIX)   # cut, filled, smoothed;
r.stretches                                  # what the ground called for
b.road(gully, allow_dam=True)                # a fill over 3 deep is a dam
b.road(by_the_pond, seal="minecraft:cobblestone")         # a cut it opens: walled
b.road(lane, mix=Mix(LANE_MIX, clump=2))     # a Mix: each cell its pick, no rng
b.fill((x0, 64, z0), (x1, 66, z1), Mix(ROAD_MIX))         # swept cell by cell
b.line_lamps(r.points, width=r.width,        # posts beside it, where the
             post="minecraft:cobblestone_wall",          # flood needs them
             lamp="minecraft:lantern", light_level=NO_SPAWNS)
b.pave(x0, z0, x1, z1, 64, trim=GRANITE,     # plaza floor, lit to stay bare:
       light_level=MELT_SNOW)                # a light names the rule it is for
b.light_cells(street_cells,                  # light what is already there,
              light_level=NO_SPAWNS)         # so nothing spawns on it
grove = b.scatter(geometry.disc(20, center=(gx, gz)), Tree("oak"),
                  spacing=6)                 # a wood, every die keyed on its
b.scatter(lawn, [("minecraft:poppy", 3), ("minecraft:dandelion", 2)],
          clump=4, cover=0.3, apart_from=grove.columns)   # column: clumps,
                                             # each on its soil, none on a path
b.villager("librarian", vx, vz)              # profession + fresh identity,
                                             # typed by the biome at its feet
b.villager("farmer", vx, vz, y=deck_y)       # standing on a deck: feet at y + 1
b.villager("cleric", vx, vz, kind="snow")    # a type given: one of VILLAGER_KINDS
b.spawn("minecraft:sheep", sx, sy, sz)       # ints center on their block
gate = b.framed(Frame((gx, 64, gz), "west")) # a component's own cells, turned
gate.set(2, 0, 1, "minecraft:oak_stairs[facing=north]")   # lands facing east
b.stamp("lodge.schem", (lx, 64, lz), rotate=90, mirror="x")  # pasted, checked
print(b.finish())                            # then save.save()
```

Two rules learned the hard way are built in. **Roads cannot cross pads or
reservations**: every `level_pad` registers its rectangle and `road()`
refuses a swath that enters one, naming the pad — a lane through a lot
carves a channel under the roof, invisible from above; declare deliberate
crossings with `through=("plaza",)`. And **`finish()` is the whole
aftercare litany** — `connect_blocks`, `rebuild_heightmaps`,
`invalidate_poi`, `reset_lighting` over what it wrote, chunk by chunk,
so beds get claimed, fences join, surfaces are true and torches light.

**A road says what it laid, and refuses a dam.** `road()` returns a
`RoadResult`: `points` (the waypoints, as checked) and `width`, which
`line_lamps` and anything else laid along the road follow it by; `line`,
the centerline, `geometry.line` through the points; `heights`, the
profile the road rides, one height per cell of it -- the ground's,
smoothed and anchored, so a high anchor shows as the step it stands above
its neighbors, since the smoothing only lowers and never ramps up to one
-- each the surface of that cell's stretch; `swath`, a read-only
`{(x, z): index into line}` of every column paved, in the order paved,
with `top(x, z)` the height the column was cut to, its stretch's height;
and `kinds` and `stretches`, what the ground called for at each cell and
the same as runs of `Stretch(kind, start, end)`, inclusive indices into
`line`. `heights` is not the surface laid along the centerline: each
column is cut by the first stretch whose square reaches it, so on a road
wider than 1 the surface at `line[i]` is an earlier stretch's --
`heights` shifted `width // 2` cells along a straight road, an anchor's
step with it. The surface laid along the centerline is
`[r.top(*cell) for cell in r.line]`, and on a straight road along x or z
that is the road's surface across its whole width at each cell: what a
hedge or a wall beside it follows. The kinds are `craft.RoadRule`'s,
which is `examples/road.py`'s classifier with its thresholds named
(`RoadRule(shallow_fill=3, tunnel_from=7, skylight=3)`, that example's
choices and no rule of the game's; `rule=` takes another):
water among a cell's columns is a `"bridge"`; ground over the road is a
`"cut"`, and a `"tunnel"` where it stands 7 or more over it; more than 3
blocks of fill between the lowest ground and the road's bed is a
`"bridge"`; the rest is `"grade"`, and a run of 3 or fewer cells between
two tunnels is tunnel too. They are reported, not built: a road cuts a
tunnel open to the sky and fills what a bridge would span. A fill more
than 3 deep under any dry column is refused, before anything is written or
drawn -- a fill that deep across a valley is a dam -- one `Refused` with a
fault for every stretch of the centerline standing on one, naming it, the
depth and the deepest column; `allow_dam=True` builds the embankment, and
rerouting or `anchors=` keeps the road off it. That includes a side-hill,
ground over the road on one side and falling away on the other: the rule
asks about the hill first and calls the stretch a cut or a tunnel, and its
fault says it is a side-hill, the fill on its low side a dam all the same.
A fill over water is `over_water`'s to decide, and not counted as an
embankment -- but water a road fills is a dam of its own two ways, each
refused until `allow_dam=True`. A fill that starves the flowing water it
fed (below) is refused first, on its own `Refused`, as the ground is read
-- and so, once the road is planned, is a causeway laid at the waterline
or under it, whose surface takes the water it stands in. And a fill that
cuts a body of water in two, the water either side of it joined through
the fill today and nowhere else once it is filled, is refused with the
embankments, a fault in their one `Refused` after theirs. A causeway
across a canal is that; a jetty into a lake, round whose end the lake
still joins, is not, and nor is a road along the edges of two ponds, each
left joined round the strip filled out of it. The water is followed with
`craft.water_body`, the fill laid over it as ground, first within 16
columns of the fill and then as far as 65,536 cells, an implementation
bound: water followed into a chunk that is not there, or past the bound,
is refused as water that could not be followed far enough to say. A road
cut below the water beside it -- its channel down past a pond's
waterline, a causeway laid under the surface -- is refused naming every
cell the water would run into, unless `seal=` walls it off where it
stands, as `grade` does (below), the seals outside every keep-out not
named in `through=`; its list surface is read for the check as dirt, which
water enters no more than any whole block. A road is not held to the
survival audit (`_audit_plan`), since a list surface has no block to audit
until it is drawn: a causeway laid at the waterline or a block over it
stands on the water with nothing filled under it, so a surface that falls
-- the gravel in `ROAD_MIX` and `LANE_MIX`, sand -- drops through the
water at the game's next update, which `finish()` reports (and
`finish(strict=True)` refuses) once the road is written. The road's swath,
like a `pave`'s rectangle, is recorded as the session's paths once it is
written, and `scatter` plants nothing on them, nor grows a tree's canopy
over them.

**A pad is a grade.** `grade(outline, target, *, cut=True, fill=True,
drain=False, retain=FROM_PALETTE, surface=FROM_PALETTE, subsoil=DIRT,
clearance=6, over_water=None, allow_dam=False, seal=None, through=(),
name=None)` is the one earthwork writer, and `level_pad` is it over a
rectangle with both flags on and the pad registered: every pad it still
builds it builds byte for byte as it always did, held to that by digest
and by `tests/test_grade.py`'s payload pin. What it refuses now and once
wrote is each of the checks below -- a fill that starves a flow, a bridge
laid into a flow's feed, a cut that lets water out, what a cut leaves
hanging -- and a `top_y` or `clearance` that is no whole number (a
clearance of `2.0` once cleared two, and `True` one). The `outline` is
an inclusive rectangle `(x0, z0, x1, z1)` or any `(x, z)` columns (a
`geometry.disc`, a town less its harbor), planned and written in `(z, x)`
order. Each column taken has its surface at `target`, `clearance` blocks of
air over it and its ground learned there: a dry column above the target is
cut down to it (and the cell over its ground with it), only with `cut`; one
under it filled up to it, only with `fill`; one already there resurfaced by
either; the rest left exactly as they were. Fill under an edge column --
one with a side outside the outline, a rectangle's border -- is `retain`,
the retaining wall (the palette's `retaining`; a block, `Material` or
`Mix`; `None` for none), and `subsoil` everywhere else (never `None`, a
`TypeError` before the ground is read). `level_pad` takes
`allow_dam=`, `seal=` and `through=` (which binds its seals alone) as a
grade does, and `wall=None` now means no ring there too. Water is refused
unless the call says what to do with it: `over_water` as `level_pad` takes
it, or `drain=True` (with `over_water` left out -- a `ValueError`
otherwise), which takes the water out wherever it stands, whatever the
flags: filled from the bed the column reader finds up to the target, cut
to air from there to the waterline, ice, kelp and lily pads with it.
Nothing under a bed is rewritten where the target stands at the bed or
over it; a target under the bed takes the column down to it, bed and all,
as a cut takes dry ground, `cut=False` or not, and counts it `drained`. A
lid -- ice over air -- is never drained. `name` registers a rectangle as a
pad, as `level_pad`'s does (only a rectangle is one); `None` registers
nothing. It returns a `craft.GradeResult`: every column counted once as
`cut`, `raised`, `surfaced`, `drained`, `bridged` or `left` (their sum is
`columns`), `retained` for the edge columns walled, `sealed` for the seals
written, and `beside_drained`, the water left standing beside what was
drained -- the pond the outline cut across -- sorted. Splitting a body of
water is never refused here: the caller drew the outline.

Before anything is written a grade (and so a pad, and a road) reads every cell
it would cut, fill, surface or clear, and one holding what somebody built --
anything `_takes` does not take, and that is not already what the plan leaves
there -- is refused, every one named, unless `overwrite=True`. A keep-out lasts
one session and the column reader stands on a roof as it stands on a hill, so
this is what keeps a later session out of a tower it was never told of: without
it, a plaza drawn over a standing tower on a copy of CastleWorld took about
11,000 blocks out of it and nothing said a word. A road may cross a path this
session laid.

A grade (and so a pad) is also held to three of the
game's rules. A fill from a bed that takes away water a flow was fed by
is refused, naming every cell starved (`craft.starved`: the game re-reads
a flow from what feeds it, `getNewLiquid`, and never a source, so falling
water fed from above keeps its feed), unless `allow_dam=True` -- and so is
a bridge laid at the waterline or under it, whose surface takes the water
it stands in. `pave` and `light_cells`, which have no such argument, refuse
a fill or a bridged floor or light that starves a flow and point at
draining the water first with `grade(..., drain=True, allow_dam=True)`. A
cut that lets water out --
a pond's bank opened, a drain below the waterline of a pond the outline
straddles -- is refused naming each leak, unless `seal=` walls it off
(below); the seals are written after the grade, refused inside a keep-out
not named in `through=`, and held to the same starve check. And what the
cut leaves hanging -- a torch or a ladder on a block it takes, sand over a
hole it opens -- is refused by `_audit_plan`. The two drains written by
hand in `tools/` are grades now, and `tests/test_grade.py` runs them as
such: the Roman town's (`cut=False, fill=False, drain=True, retain=None,
clearance=0`) and the secret lab's wetfill (`cut=False, fill=True,
drain=True`).

**A keep-out is a pad or a reservation.** `b.reserve(x0, z0, x1, z1,
name="grove")` registers a column rectangle without leveling it — a grove, a
garden, a lot for later — writes and journals nothing, and returns the
rectangle sorted. `road()`, `railway()`, `approach()` and `extrude()`
refuse either kind and `smooth_terrain()` holds either kind's columns;
`through=` names either.
The refusal is one `craft.Refused` with a fault per keep-out entered, in the
order they were registered, each naming the first of its columns the writer
meets — a road along its centerline, a railway along its line and, for the
seals beside it, in the order its plan is written, an approach row by row
from its start, an extrusion by the cells its band lays and then its seals
— and how many it reaches.
`line_lamps` passes a post's place in a keep-out over rather than refusing
the call, and `scatter` a column in one, counting it skipped. `railway()` checks
every column its plan writes, the seals walling water off beside the line
included. A name is never shared: `reserve` refuses an empty name or one a
pad or reservation already has, and `level_pad(name=)` refuses a reserved
one, since `through=` could no longer say which it means, and another pad's
unless it is the same rectangle leveled again: a second lot under the first
one's name would take its keep-out with it, and a road would carve straight
through the first. `reserve` also refuses a fractional corner rather than
guess which column it meant. The writers handed their cells outright — `set`,
`fill`, `level_pad`, a `grade`'s or a `basin`'s outline,
`clear_vegetation`, `pave`, the shells, a `battlement`, a `roof`, the
stairs, `furnish`, a `framed` view and `stamp` — do not consult keep-outs,
as what a caller names, the caller has seen; the cells a writer places
itself, a grade's, a road's, a basin's and an approach's seals and a
basin's rim among them, do.

**A refusal is `craft.Refused`, and it names every fault.** It is an
`NBTError`, so existing handlers still catch it, and `.faults` lists every
reason, not just the one the message names, so one pass can fix them all.
Having no ground to build on is `craft.NoSurface`, a kind of refusal.
Catch those two rather than `NBTError` itself, which is also what a damaged
file raises: an `except NBTError` around a judged call turns a broken chunk
into "nothing there". `Builder.place` journals every cell a placement
writes, a bed's head in the next chunk included, so `finish()` settles it.

**Strict writes.** Every block a `Builder` writes is one the game has: an
id the registry lacks, or a property or a value its block does not take,
is refused as `craft.IllegalBlock`, a `Refused`, because the game would
load the id as air and the value as the block's default and say so only in
a log nobody reads. The check is `blockinfo.check_state`, run on every
state a call plans, before its first write and before the chunk checks —
a primitive's one cell, a writer's whole plan, and the states a stair or
vault rule read off a building copies (a `minecraft:chain` newel read off
a chunk saved before the rename is refused, naming `minecraft:iron_chain`).
One refusal names every illegal state in the call at once, each fault in
`.faults` and the nearest real ids in `.suggestions`. The check reads
nothing and draws nothing: a road's or a floor's mix is checked entry by
entry, by name, so a mix with one bad entry is refused with the session's
`rng` where it was -- and so is a `Mix` anywhere it is written, an entry no
cell would happen to pick included. A lamp is weighed first (`pave`, `light_cells`,
`hang_lamps`, `shaft`), so a block the game has that is dark is refused
for giving no light, and only one the game lacks is the `IllegalBlock` —
the call's, naming with it every other block of the call the game lacks.
`allow=` writes what the registry cannot know — a modded block, or one
newer than the game it was generated from — by exact id or by a namespace
ending in `":"`, and an entry that could match no id raises `NBTError` as
the `Builder` is made. It is fixed from then on: `Builder.allow` reads it
and cannot be reassigned, because a state that passed is not checked
again that session, so a different allowance is a new `Builder`. The
lower tier's `set_block`, `fill` and `place_block` stay permissive: they
write whatever they are given.

```python
b = Builder(save, allow=("create:",))
b.set(x, y, z, "create:gearbox")            # modded: written unchecked
b.set(x, y, z, "minecraft:quartz_wall")     # IllegalBlock: quartz_block has no wall
```

**A call writes all of itself or nothing.** Every writer plans its whole call
and checks the plan before its first write. Every state it names must parse;
a typo raises the parser's own `ValueError` while the save is still
untouched. Every state must be one the game has (strict writes, above). Every
chunk it reaches must be generated and *finished*: `Status` `minecraft:full`,
or the bare `full` a hand-made chunk may carry, since the game generates over
whatever is written into a chunk it set aside part-way. And every height must
be inside that chunk's world. A plan that fails is refused whole, one
`Refused` with a fault per chunk it cannot build in (`chunk (2, 0) is not
generated`, `chunk (1, 0) is at minecraft:features, not finished terrain`)
and one per end of the world it passes (`y=320..321 is past the world's
-64..319`). Nothing is written, and a road, whose surface is drawn from its
mix as each column is written, draws nothing from the session's `rng`. A
chunk the check cannot read is corruption, and raises its own `NBTError`. The
writers that read the ground as they plan — `level_pad`, `road`, `pave`,
`clear_vegetation`, and `fence_rect`, `ornamental_tree` and `light_cells`
where they follow the ground — check the blocks they would write first, then
meet a column with no ground there, and refuse with `NoSurface`, still before
anything is written; a column in a chunk that cannot be read is corruption
there too. The writers that read the cells their rule needs before they know
what to write — `spiral_stair`, `switchback_stair`, `flight`, `vault`,
`battlement`, `roof`, `railway` and an iron `doorway`'s buttons — check the plan's states and its
reach before that read, so a stair into a chunk that is not generated, or
past the top of the world, is the same `Refused`, every fault named, and
never the plain `NBTError` of a read that found nothing. A floor and its
lights are one plan, and so are the lights and the snow they melt, and a
chain and its lamp. `set`, `place` and `fill` hold their own cells to the
same checks — for a placement, every cell of it and every neighbor it
settles: a fence placed beside an unfinished chunk is refused when a fence
stands across the border, which the placement would rewrite — and `spawn`
(and so `villager`) the block its entity stands in, before a yaw is drawn.
A plan is written in the order it was made, through the same writes the
primitives make, so it encodes byte for byte as writing it cell by cell did.

**A `Mix` is written where it is picked.** `set`, `fill`,
`level_pad(surface=, wall=)`, `road(mix=)` and `pave(mix=, trim=)` take a
`craft.style.Mix` and write each cell its pick there, `mix.pick(x, y, z)`:
no `rng`, no call order and nothing written before moves it, so the same
Mix over the same cells is the same blocks from any session, and -- written
in the same order -- the same bytes. A list of `(block, weight)` pairs
(`ROAD_MIX`, `LANE_MIX`, `PLAZA_MIX`, or your own) is still what it was,
drawn from the session's `rng` as the columns are written, byte for byte as
before: which is why one more row of a floor paved early in a session
re-rolls every list-mixed surface laid after it, in as many cells as two
unrelated rolls of that list would differ in (1 - Σp²) -- about three in
four of a `ROAD_MIX` road, seven in ten of a `PLAZA_MIX` floor and three in
five of a `LANE_MIX` lane. A Mix `fill` is a
sweep, each cell its own pick, written in `y`, `z`, `x` order and journaled
cell by cell (`blocks_set` counts every cell), never `save.fill`'s section
of one state, so it costs per cell; it is checked once, over the box, as
any fill is. A writer that writes a block it is given into its own cells
unchanged -- `frame_walls`' plinth, logs and infill, a `floor`, a gable's
planks, an `arcade`'s piers, base and cap, a battlement's course and
merlons, a tree's `leaves=` -- picks a Mix
the same way, through the
same write path, and the survival audit a writer runs before it writes
reads a Mix as each cell's pick. What a Mix cannot be is one exact state.
A placement of one -- `place`, or a plan's place op -- is a `TypeError`,
since which cells a placement writes depends on which block it is; so is
a Mix given to a rule (`SpiralStair`, `SwitchbackStair`, `StraightStair`,
`Vault`, `Railway`, and the stairs' `read`s), and one given where a writer
derives a state from its block or reads its light off it: a lamp (`pave`,
`light_cells`, `hang_lamps`, `shaft`, `line_lamps`, `basin`), a chain, an
anchor, a
door or its button, a post, a railway's seal, a shaft's lining, a flight's
rail, a gable's stairs and ridge slab, a window's glass -- each refused
before anything is read, even where no cell would have wanted it.

**A palette is where a writer's materials come from.** `Builder(save, *,
..., palette=None)` holds a `craft.style.Palette`, `b.palette`, and the
eight legacy writers -- `level_pad`, `road`, `pave`, `light_cells`,
`frame_walls`, `floor`, `gable_roof` and `window` -- read every material
their caller leaves out from it: each such argument defaults to
`FROM_PALETTE`, and resolves to its role (the `read by` column of the roles
table under `craft.style`). `palette=None` is `DEFAULT_PALETTE`, the
writers' own old defaults, so a Builder given no palette writes what it
always wrote, byte for byte and draw for draw; anything that is not a
Palette is a `TypeError`. An argument given always wins and passes through
untouched, a name or a `BlockState` as it is -- but for the two
connections a window sets itself, below. A `Material`, given or held
by the palette, is the form the writer writes: its block for a block,
`gable_roof`'s stairs and ridge slab for `stair=` and `slab=` -- so one
family roofs a whole course -- and its pane for a window; every form a
call's Materials lack is one `IllegalBlock` naming them all ("2 forms the
game does not have (stairs of minecraft:calcite, slab of
minecraft:calcite)"), before anything is read. A `Mix` stands where the
block is written as it is, and is a `TypeError` where the writer spells a
facing, a type or a connection onto it or reads its light off it. `None`
means none where it always did -- `pave(trim=None)` lays no trim under a
palette with one, and `pave(light=None)` an unlit floor -- and is a
`TypeError` for any other block argument (`light_cells(light=None)`
included: light_cells exists to set lamps); a surface's `None` is the
palette's, as it always meant the default. A surface (`road(mix=)`,
`pave(mix=)`) is a list of pairs drawn from the `rng`, a Mix, or one block
laid all along; left out or `None` it is the palette's, and an empty list
is an `NBTError` instead of quietly the default. `pave` resolves its light before asking whether
there is one, so a palette's light is a lamp whose `light_level` is
required. `window(x, y, z, axis, *, block=FROM_PALETTE)` spells the
connections along its wall (`east`/`west` for a wall along x,
`north`/`south` along z) only onto a block whose defaults carry all four
sides -- a pane, iron bars -- and writes plain or tinted glass as it is, a
connection being a property those blocks do not have (one the game would
drop, and a strict write refuses). That pair is the window's to set: the
block given is checked as given first, so `glass_pane[east=maybe]` is an
`IllegalBlock` in the window's name, and a side of the pair given as
anything but `true` (`glass_pane[east=false]` in a wall along x) is an
`NBTError` rather than quietly joined. `b.palette` is
settable, unlike `allow` and `guard`, since nothing is cached on it: one
session can build in more than one style. Rule objects -- `Vault`,
`Railway`, the stairs -- are made without a Builder and have no palette;
pass their materials, `b.palette[role]` among them.

**Where the defaults came from.** Each default below is one a real build
used, or a choice made while building with one, kept so that a call naming
nothing builds what it always built; none is a rule of the game's, and each
is meant to be replaced. The table is the materials and the worked
examples' numbers, not every default there is: a minimum a writer refuses
to go below -- a railway's `clearance` of 2, a rider's head being in the
block above, or `HEAD_ROOM`'s three treads -- is the game's, and its
refusal says why.

| default | where it came from |
| --- | --- |
| `level_pad`'s grass and stone-brick ring; `road`'s `ROAD_MIX`; `pave`'s `PLAZA_MIX`; `frame_walls`' cobblestone plinth, stripped-spruce corners and white terracotta; `floor`'s spruce; `gable_roof`'s dark oak; `window`'s glass pane | the construction kit the first town and castle builds were distilled into (e107f54, 2026-08-14): the medieval town's materials, `LANE_MIX` among them |
| `pave`'s and `light_cells`' glowstone | the plaza floors first lit to stay bare (48873df, 2026-08-20) |
| `fence_rect`'s and `ornamental_tree`'s oak | the same construction kit |
| `RoadRule(shallow_fill=3, tunnel_from=7, skylight=3)`: the fill under a road's bed that is still grade, the ground over it that makes a cut a tunnel, the gap between tunnels closed -- and so where `road` sees a dam | `examples/road.py` (67647ea, 2026-08-11): its `SHALLOW_FILL`, the air an embankment may swallow, its `TUNNEL_FROM`, and the three-cell gap `smooth_kinds` closes; `road` refuses a deeper fill unless `allow_dam=True` |
| `basin`'s `slope=SHORE_SLOPE`, 0.75 of a block of depth per block from the shore | the big lake around Jeff's island, re-cut on 2026-08-27 (`tools/lake/lakeplan.py`, `SLOPE`): the slope it chose to leave room for its ripple. Any slope up to 1 is cliff-free and taken; past it is the game's geometry, not taste, and refused. `basin` has no material default: its `floor` is always named |
| `Vault(half_span=7, pitch=1)` and its cobblestone slab, block and stairs | the vault over JeffWorld's great hall (x=-63, z=72..121), read off it by `tools/nave/read_nave.py`: profile (0..7), depth (1,1,1,2,1,1,1,4) (9d30260, 2026-08-31; re-read 2026-10-21) |
| `arcade`'s `pier=quartz_block` and `pitch=6` | the hall's own stone; `pitch=6` is a choice, the demonstration hall of `tools/nave/build_hall.py`: the real hall's arcade lines are solid quartz walls and its cross walls stand irregularly, so it has no bay pitch to measure |
| `hang_lamps(drop=3)` and its glowstone; `chain=iron_chain` | the hall's lamps are glowstone, hung by the ceiling they hang from: over the vault's run (z=72..121, read 2026-10-21) mostly three links under its y=76 ceilings, one under y=83, two from the aisle vault and five from the ridge, so `drop=3` is a choice among them (the nave row of the demonstration hall in `tools/nave/build_hall.py`); the chain is the library's, since the hall hangs every lamp on nether brick fences |
| `Railway(boost_every=26)`, its cobblestone bed and redstone power | the Stronghold link, JeffWorld's z=13 line (laid 2026-10-10, made a rule in 829bbf9): boosters on redstone blocks every 26 cells from x=-909 to -753. 26 is that build's choice; no game rule picks it |
| `SpiralStair`'s oak stairs and planks | the stair Jeff began by hand up JeffWorld's glass tower at (-171, -107) (9d30260); `SwitchbackStair` and `StraightStair` took the same oak, though the yellow tower the switchback was read off is birch |
| `doorway`'s oak door, stone button and left hinge | the oak door and left hinge are the suburban house's doorway helper (`tools/suburban_house/furniture.py`, the house finished 2026-08-27, kept in 3c71810); the stone button is a choice, made with the iron door's buttons (6037887, 2026-10-20) |
| `battlement(height=1)` | every crown built so far: the castle example's curtain walls and towers and the Roman city's field towers stand one-block teeth on their course; `battlement` has no material default, its block is always named |
| `Roof(pitch=2, crown="slab")` | `gable_roof`'s own pitch and slab ridge, from the same construction kit (e107f54), which the Roman city's hip and tholos cone and the castle example's spire share; `overhang=0` because the outline is the walls' (`gable_roof` passes 1); `Roof` has no material default, its `block`, `slab` and `stair` are always named |
| `Opening.arch`'s flat head when no `profile` is given: a springer in each end cell of the head row | the castle example's arched windows and keep entrance (`examples/castle.py`, `keep_windows`, `keep_entrance`), whose springers face their jambs |
| `Facade(phase=None)` counting from each wall's low end, and `Wall.ring` giving its corners to the north and south walls | a choice, after `arcade`'s pier on the first column of its run -- though `arcade` counts from whichever end is named first; the lab's buildings passed `phase=0`. `Wall`, `Facade` and `Opening` have no material default: every block is named |
| `Piece.lamp`'s one-block `post`, and `Piece.pendant`'s lamp hung straight from its ceiling (`links=0`) | the suburban house's nightstand, a lantern on one block of stripped oak, and its ceiling lights, a lantern hung straight from the ceiling (`tools/suburban_house/furniture.py`) -- and the Roman city's candelabrum, a torch on one sandstone wall (`tools/roman_city/common.py`). `Piece` has no material default: every block is named |

The first two rows are the eight legacy writers' materials and the roles of
`DEFAULT_PALETTE`, and a Builder with no palette reproduces them exactly;
the rest are literal defaults, replaced by passing the argument. The same
table is in the `heaton_voxel.craft.build` docstring.

**Inside a worksite, a write is one its rollback can take back.**
`site.builder()` (`Builder(save, *, dimension=None, seed=0, allow=(),
guard=None, palette=None)` with the worksite's guard) admits every write
after those checks and before the first block moves: one landing in a region
file the snapshot does not hold, or reaching one through the eight chunks
around it that `finish()` settles, or touching a protected zone — even with
the state already there, or through a neighbor a placement re-derives —
refuses the call whole, and so does a spawn outside the snapshot's entities
files.
`finish()` refuses before heightmaps and light when settling would change
a zone. A guard is fixed as the Builder is made (`b.guard`), and one made
for another save or another dimension is refused; see the worksite
section for the rest.

**Every write is journaled, and `finish()` settles what the journal
holds.** `b.journal` records each write once it is in the save — a `set`,
every cell of a `place`, a `fill`'s box — and a refused call records
nothing. `b.journal.written()` lists the cells once each, by chunk and
then `y`, `z`, `x`: what the Builder asked for, not what changed. A cell
set to the state it held is listed, the fences, walls and stairs beside a
placement that `place_block` re-derives are not (`finish()` settles them
anyway), and nothing in it can undo anything. What a build changed is a
content question, and `Worksite.diff` is its answer: every cell whose state
differs from the worksite's snapshot, whoever wrote it (below).
`b.journal.chunks()` is the chunks written in, and `b.blocks_set` is
`b.journal.total`, every write counted each time it is made and a fill as
the blocks it filled. A stage
names what a block of work writes:

```python
with b.stage("pad"):
    b.level_pad(x0, z0, x1, z1, 64, name="inn")
with b.stage("roof"):
    b.gable_roof(x0 + 1, z0 + 1, x1 - 1, z1 - 1, 68, "x")
b.set(x, y, z, "minecraft:bell")
b.journal.tally()     # {"pad": ..., "roof": ..., None: 1}, in first-use order
```

Stages nest (the innermost name counts, and leaving one restores the one
around it), a name is a non-empty string, and a stage scopes nothing but
the count. `finish()` runs over what the journal holds, chunk by chunk:
connection states over each chunk's writes and one block around them,
clipped to the chunk — a fence, wall, pane, gate or stair takes its state
from the blocks across its faces, so nothing farther can change, and the
margin settles the fence across a chunk border as a block update would —
heightmaps and POI on the chunks written in, lighting on those and the
eight chunks around each, and the support audit over each chunk's writes
and two blocks around them, the farthest its checks read. A chunk the
margin reaches that the world never generated is skipped unreported. One
that is not finished is left out — the game generates over it, and nothing
of the build is in it — and, when a fence, wall, pane, gate or stair
stands in the margin there, listed on `connections.unreadable_chunks` with
why, so `connections.complete` does not call that border settled. One that
cannot be read is reported on `connections` and `lighting` and not
audited. Within a chunk the box is one bounding box — of the writes there
and their margin, and of every margin reaching in from the chunks next
door — so a chunk written at y=64 and y=200 is settled through everything
between, and so is the span from a write in one corner to a margin
reaching in across the far edge. A writer that spreads over a chunk, a
`scatter` or a `basin`, therefore puts every fence, wall, pane, gate and
stair standing anywhere in that chunk's box through the pass, the game's
own village walls included — and the pass is the lower tier's, whose
answer to what a fence, a wall or a pane joins as solid is still mostly a
table of names: it departs from the game on corner stairs, on door, ladder
and shelf plates, and on full cubes it does not name (CLAUDE.md has the
count). Earlier work outside those boxes is left as it is — a hand-set
railing ten blocks from the only write in its chunk keeps its states — and
work right beside a write is re-derived like the build's own, so pass
`connect=False` when a pass placed no connecting blocks and runs beside
work you would rather leave alone, and run `save.connect_blocks` yourself
over anything built by hand.

**The ground is read once per column, the way the survey reads it.**
`b.probe(x, z)` is the column's `SurfaceProbe` from the column reader
(`walk_surface`, below), cached for the session as the world was when it was
first read — no write of the Builder's updates it. `b.ground(x, z)` is where
a build stands: the height the session set where it shaped the column (a pad,
a road, paving, smoothing, a felled tree, or `b.set_ground(x, z, y)` from a
script that built it by hand), and otherwise the probe's `waterline`: the
ground under any snow, never a canopy, a torch or a flower standing on it,
and the water's surface over a lake — read the same off the game's maps or,
where a rebuild took them away, off a finished chunk's blocks.
`b.known_ground(x, z)` answers that without reading anything, None where the
session knows nothing; `b.forget_ground(x, z)` drops both the height and the
probe so the next read is fresh, and `b.forget_ground()` forgets every
column. `b.stand_below(x, z, y)` is the floor at or under `y` — a room under
a roof — read from the blocks as they are now and never cached. Where there
is no ground, both reads raise `NoSurface`, whose `.reason` says why
(`"ungenerated"`, `"partial"`, `"empty"`) and `.cell` where; a chunk that
cannot be read is corruption, a plain `NBTError`.

**Water is refused unless the call says what to do with it.** `level_pad`,
`road` and `pave` read the ground under their whole footprint before they
plan, and a column of water or an ice sheet — the probe's `wet` — refuses
the call before anything is written: one `Refused` naming the first and
how many, whose `.faults` are the wet columns' probes. `over_water="bridge"`
builds on the water's surface, where a heightmap's ground always put it,
and leaves the water under the build. `over_water="fill"` fills it from the
bed — a pad's ring wall and dirt, a road's or a floor's dirt, where the
water was — and refuses where the surface asked for lies under the
waterline, which would leave the water standing on the build, and where
an ice sheet has nothing under it to fill from: a lid over whatever is
below. Any other value is a `ValueError`. Ground the session shaped stands
as it was set, so water a script filled is ground; a height left on water
or an ice sheet still standing — the pond a felled branch hung over, a
`basin`, which sets every column's ground at its surface — is read under
itself and refused, bridged or filled like any other wet column. A fill
from the bed that starves the flowing water beside it is refused as
`grade`'s is (above), and so is a build bridged at the waterline or under
it, standing in the water a flow is fed by: `level_pad` and `road` take
`allow_dam=True`, and `pave`'s and `light_cells`' refusals point at
`grade`. `line_lamps` passes a post on water over unless
`over_water="bridge"` stands it on the water's surface, and refuses a
strip over water as `light_cells` does. `light_cells` reads the ground
under its bare `(x, z)` cells the same way and refuses water unless
bridged; it lays no floor, so it has nothing to fill, and `"fill"` is a
`ValueError` there. `villager`, `fence_rect` and `ornamental_tree` take
`y=`, the floor they stand on — a deck, an upper story — with the feet,
the posts or the trunk at `y + 1`, the convention `pave(y)` and
`level_pad(top_y)` keep.

**A plan that lets water out is refused, or sealed.** The water check a
writer runs over its whole plan before it writes is
`Builder._contain_plan(ops, *, what, seal=None, spill=())`, as
`_audit_plan` is the survival audit; `grade`, `level_pad` through it,
`road`, `basin`, `approach` and `extrude` run it, and take `seal=`, and
`furnish` runs it with none -- furniture keeps its own water in, and
clear of anybody else's. `spill` is world cells, from every writer that
takes one: an extrusion's `spill=`, and each furniture piece's own
`spill` cells placed with it. The plan's
cells laid over the world,
read as the writes will leave them (a placement into water taking the
water its rule takes, as `place_block` writes it), and `craft.contained`
run from every cell of water near them. Each `Leak` it finds -- a pond's
bank cut open, a source set beside a stale rim -- refuses the plan, one
`Refused` whose `.faults` are every leak in cell order, unless `seal=`
names a full block: then each leak's water is walled off where it stands,
a seal at its `source` (railway's way), and the seals come back for the
writer to write after its plan, audit and count. The plan with its seals
is checked again, since a seal can open a way of its own -- falling water
over a sealed cell turns to its sides -- and what still gets out is the
same refusal. Two leaks no seal mends, so either refuses the plan with
or without one: lava touching the water, and water running into a chunk
that is not there. Nor is water the plan writes itself sealed, since the
seal would go over the plan, and a seal replaces water alone -- the water,
or kelp, seagrass or a bubble column, which are water at every state --
so a leak whose water is held by something standing in it, a waterlogged
chest, stair or lichen, refuses the plan, naming it, rather than burying
it -- and a refusal without a seal says so of that water, rather than
offering one. A seal water can pass, enter or waterlog (a slab, leaves, a pane, a
door), one that does not stay a wall (sand, gravel or concrete powder,
which fall; ice and frosted ice, which melt; a sponge, which soaks the
water up), a `Mix` or a `Material`, and a block the game lacks are
refused before anything is read -- a railway's seal is held to the same
rule, leaves, a grate and a chest among what it now refuses -- and a
modded block, which `allow=` passes and the tables answer as a cube, is
taken at its word: one that says it is waterlogged is refused. The seals'
own cells are the caller's to audit and keep out of
keep-outs. `WATER_SOURCE_CONVERSION` is taken as
on, its default, as `contained` takes it.

**A villager's type is the game's for where it stands.** The game types a
villager by the biome at its feet when it first spawns
(`VillagerType.byBiome`) and takes the `VillagerData` type a save hands it
as final, so a villager written into the snow as `plains` stays one. So
`villager(profession, x, z, *, yaw=0.0, kind=None, y=None)` reads the
biome of the 4×4×4 cell its feet stand in — after the spot's own refusal,
the one `spawn` gives, so nothing is read over the world — and writes
`craft.biomes.villager_type(biome)`: desert in the badlands, snow on snowy
plains or in a grove, plains wherever the game's table names nothing, a
datapack's biome included. A cell that records no biome is an `NBTError`
rather than a guess. The game picks its biome through a seeded zoom
(`BiomeManager`), so within about two blocks of a biome border it may have
chosen the neighbor's type; `kind=` settles any doubt. An explicit `kind`
— `"snow"` or `"minecraft:snow"` — is written as given once it is one of
`craft.biomes.VILLAGER_KINDS`, and any other is an `NBTError` naming the
seven before anything is read, since the game would load it as plains.

**Cave air is air, to the writers and the readers alike.** What a builder
builds into without being told to cut through it, and what a rule read off
the world takes for nothing, is `AIR_BLOCKS`, the game's own `air` tag. So
a stair winds up a shaft through an old cave, a vault extrudes and lamps
hang through a room of cave air, a canopy grows into it; and a stair read
back out of a cave finds nothing in its gaps rather than furniture, and
reads the cave air in an unfinished level as missing treads rather than a
second material. Nor does a chain hang from a ceiling of it: the roof of a
cave is the stone above its air. What a builder clears, it writes as plain
`minecraft:air`.

**`clear_vegetation` fells whole trees, over the ground and nowhere else.**
Every tree block and plant over a column's ground goes, up to the top of
the column however tall the tree (`height=` caps it): its `WORLD_SURFACE`,
or its blocks where this session has built in the chunk, since no Builder
write moves a map. Where the ground is a tree -- the top log, which the
game's map stops at -- or a height a script set on a torch, a rail or a
carpet, the column is walked down to the block the game's
`MOTION_BLOCKING_NO_LEAVES` will land on once the tree is gone, clearing
the trunk on the way, and that is the column's ground from then on. A
waterlogged block leaves its water, as the game's own removal does.
Nothing below the ground is touched, so the hanging roots and glow
lichen under an overhang whose top is the ground, or in a cave, are left
while lichen on an open cliff face goes; and the first ledge or roof over
the ground stops the sweep, so what grows on top of it is left too, while
roots hanging from it into the column over a pad set beneath it go. A huge
fungus's shroomlights are left, since one is a lamp as well, and a felled
cap's ground can stop on one. A village's log walls are logs, and come
down with the trees: keep a village out of the rectangle.
`smooth_terrain` raises a column with what lies under its surface where
that is natural ground, and with dirt over cave air, an ore or a fluid. It
holds every column the probe finds wet, kelp at the waterline and a frozen
pond among them, and its report counts the columns it found no ground in
under `"absent"`, by reason.

**`pave` derives its lighting rather than guessing it.** An outdoor floor is
asked for two things — stay bare through winter, be safe to cross at night —
and each is a block light the game has a rule for, so `light_level` names
the one the light is for and has no default: `MELT_SNOW` (12) melts snow
lying on it, `NO_NEW_SNOW_OR_ICE` (10) lets none settle on a floor
`clear_above` has cleared, and `NO_SPAWNS` lets nothing spawn on it — 1, or
8 in the nether, read for the `Builder`'s dimension (see
[light levels](#light-levels-the-rules-a-lamp-is-for)). Any level 1 to 15 is
taken as well; leaving it out of `pave`, `light_cells`, `hang_lamps`,
`line_lamps` or a lit `shaft` is a `TypeError` naming the rules, before
anything is read (a `basin` given a `lamp=` and no level, or a level and
no lamp, is an `NBTError` saying it needs both), and
`pave(light=None)` needs none. A source set flush *in*
the floor loses one level reaching the air a walker stands in and one more
per block traveled, so across open ground a lamp of strength `L` holds
every cell within `L - light_level - 1` of it; the lattice is laid at
exactly that reach, phase-locked to world coordinates so two calls that
meet tile seamlessly, and each cell it leaves short, in row order, gets of
the lamps reaching it the one that reaches the most cells still short, ties
to the furthest -- so a road or a colonnade the lattice misses is lit at
the full reach, and a wide floor's margin along its edge rather than by a
row of lamps one per column. That is only the proposal. **Every cell is then checked
by the game's own flood** (`craft.light.plan_lights`, over the floor about
to be laid, the dirt under a filled cell and the cover the lights clear) —
the cell over each floor cell, where a walker's feet are — and patched
where the light falls short, so paving that runs behind a building, round
a pillar or over a bump in the ground gets the lamps the arithmetic said it
did not need. On open ground the two agree, and the lamps are where the
arithmetic put them.
Light already in the world is counted, read off the blocks: an earlier
call's lamps, or anybody's, are not doubled, and a lamp broken since is not
credited. A cell with a block standing in it holds no light and needs none
(`PaveResult.blocked`); one that stays dark wherever the lamps go — snow
left lying on it with `clear_above=False`, since a snow layer closes its
underside to the lamp below — is refused before anything is written, and a
refused floor draws nothing from `rng`. `PaveResult.patched` counts the
lamps off the lattice. `light_cells` lights cells that are already there the
same way: a bare `(x, z)` stands on its ground, and one over water or an
ice sheet is refused unless `over_water="bridge"` sets its light into the
water's surface. A lamp is credited with the light it keeps
(`durable_emission`): a lit redstone lamp, dark again at the next block
update with nothing to power it, is refused, and an unwaxed copper bulb
counts as the 4 it weathers down to.

**`line_lamps` lights a road from posts beside it.**
`line_lamps(points, *, post, lamp, light_level, side="both", width=3,
through=(), over_water=None)` walks its waypoints by `geometry.line`, as
`road()` does, so given a road's waypoints and `width` it follows that road
cell for cell, and its **strip** is the road's swath. Every cell over a
strip column — where a walker's feet are — clears `light_level` by the
game's flood, and the level has no default, as `pave`'s has none:
`NO_SPAWNS` keeps the road free of spawns. `post` is what the lamp stands
on, a block or blocks from the ground up, and `lamp` what stands on top;
both are required, since a lamp post is one build's look. `side` is
`"right"`, `"left"` or `"both"`, seen traveling from the first waypoint to
the last; each line cell offers a post on each of its sides, in the nearest
column off the strip square to the step the line takes there, by the
compass word nearest that step — `geometry.heading_of(dx, dz, ties="z")`,
named at the call, so a road heading south-east has its right side west
and its left side east (along a diagonal run either tie offers the same
columns, so the tie shows where the run starts and stops). The strip runs
past each end of the line by half its width, and the line is walked on
that far, so the strip's ends offer posts beside them too and the middle
of a road's end is lit from beside the road, never by a post across it.
A place is passed over, never written, in a pad or a reservation not in
`through`, on water unless `over_water="bridge"`, with no ground, where
anything but air (or a flower, grass or snow at its foot) stands in its
cells, past the top of the world, or where the worksite's guard would
refuse it; no post ever stands in the strip. The
posts are chosen as `pave` chooses lamps: straight-line arithmetic proposes
them — the first cell no post reaches gets the one that reaches the most of
the strip after it, alternating sides on a tie — and `plan_lights` checks
every cell and patches the inside of a bend, the far side of a wall. Light
already there is counted, so a lit road gets no posts; a strip its posts
cannot light is refused with the dark cells as `.faults`. The plan is
audited for what the game will not keep before it is written
(`_audit_plan`): a standing lantern needs the block under it sturdy at
the middle of its top (`LanternBlock.canSurvive`), which a wall or a fence
is, so one on a bottom slab, or `lantern[hanging=true]` on a post, is
refused. Returns a `LineLampsResult`:
`posts` (each post's foot, in write order), `cells`, `patched`, `blocked`,
`passed_over` and `light_level`.

**`road(lanterns=True)` was removed.** It is a `TypeError` naming
`line_lamps`, raised before anything else about the call is checked, read,
written or drawn; `lanterns=False`, the default, is still taken, so the
calls that turned the old post off read as they did. That post — a
cobblestone wall and a lantern every twelve cells, a block off the
swath's +x, +z corner — was one build's look placed by a rule that does
not survive a network: the next road beside it carved it away, and the
road's own swath paved over it or stood it on the paving wherever the road
ran toward +x and +z, or toward -x and -z, at once. Light a road with
`line_lamps`, given its `RoadResult`'s `points` and `width`.

### Basins: water held where it is put

```python
from heaton_voxel.craft import NO_NEW_SNOW_OR_ICE, SHORE_SLOPE, geometry

w = b.basin(geometry.disc(9, center=(px, pz)), 62, floor="minecraft:sand",
            lining="minecraft:stone")    # 0.75 deep per block from the shore
b.basin(lake, 60, floor="minecraft:sand", depth=bands)   # the park's three bands
b.basin((x0, z0, x1, z1), 64, floor=STONE, depth=1,      # a trough, walled
        rim="minecraft:stone_bricks")                    # where its bank is open
b.basin(pond, 62, floor="minecraft:sand", lining="minecraft:stone",
        lamp="minecraft:sea_lantern", light_level=NO_NEW_SNOW_OR_ICE)
w.floor, w.water, w.cut, w.raised, w.lined, w.rim, w.sealed, w.light, w.lids
w.melting
```

`basin(outline, surface, *, floor, depth=None, slope=SHORE_SLOPE,
max_depth=None, lining=None, rim=None, light_level=None, lamp=None,
seal=None, allow_dam=False, overwrite=False, through=())` lays a body of
water over `outline` -- a rectangle `(x0, z0, x1, z1)` or any `(x, z)`
columns, planned and written in `(z, x)` order as a grade's are -- with
its top cell at `surface`, every cell of it a source, on a `floor` set in
each column at `surface - depth`. What stood over the surface -- the ground
a cut went through and the flower or snow on it, a tall plant's upper half
with its lower one, the water of a pond the basin lowers with the lily pad
lying on it, and a snow layer on a sheet it turns to water (frosted or
blue ice: the game keeps none on ice or packed ice, its
`cannot_support_snow_layer`) -- is cleared to air, and the session learns each
column's ground at the surface, which is water: every ground writer after
it reads the column as water, and refuses, bridges or fills it.

**The depth is the distance to the shore.** Left out, a column `d` from the
nearest column outside the outline (`craft.field.distance_to_edge`) is
`max(1, round(slope * d))` deep: the nearest whole depth to the slope's
cone, never more than half a block past it, never less than one. The field
moves by at most one between side neighbors, so at any slope up to 1 no two
neighbors' floors are more than a block apart -- the deepest bed with no
cliff in it, which is how the big lake was re-cut -- and a slope that is
not a number over 0 and at most 1 is an `NBTError`. `SHORE_SLOPE` is that
lake's 0.75 (see the defaults table). `depth` may instead be a whole number
(a tank), a mapping of every column to its depth (columns it names outside
the outline are not read; one it leaves out, or a depth under one, is an
`NBTError`), or a callable of the distance; `max_depth` caps whichever it
is. Given `tools/park/park_plan.py`'s own `depth_map`, a basin puts every
one of the park lake's 499 beds where `build_park.py` laid it by hand --
103, 244 and 152 columns one, two and three deep, `tests/test_basin.py`
holds it so.

**A floor over a hole is a lid.** Each column is read before anything is
planned, the ground it stands on or, under water, the bed (`_stand`). A
floor more than a block over that ground is built up to with `lining`; a
floor on it or under it whose cell below holds nothing up -- what the
game's `FallingBlock.isFree` lets a falling block through, its
`replaceable` tag: any air, water, lava, a bubble column, fire, a snow
layer, grass, a vine, hanging roots, leaf litter, a light block -- or is
ice, which the basin's own lamps could melt, or is sand or gravel standing
on such a hole, which the game drops first and the floor after it, gets one
course of `lining` in that cell, enough to hold a gravity block up. Kelp
holds water and holds sand up, and is no hole. Without `lining` either is
refused, one `Refused` naming every such column -- once the chunks and
heights the plan reaches are known to be there, so a floor under the
world's floor is refused as past the world, not as a lid. `floor`,
`lining` and `rim` are each a block, a `Material` (its block) or a `Mix`,
picked at every cell it lands in. Every cell the basin writes over must
hold what nobody built -- air, ground cover, natural ground, an ore, a
tree, water, lava, kelp, freeze ice, a lily pad -- or the very block the
basin writes there, or is named in one refusal unless `overwrite=True`: a
wall, a rail, a sea pickle somebody set as a lamp, a modded block.

**Then the water, before anything is written.** Water the basin takes out
-- a pond lowered, or filled under a raised floor -- that a flow beside it
was fed by is refused, naming every cell starved (`craft.starved`),
unless `allow_dam=True`. A
cell the new water would run, fall or waterlog into (`craft.contained`) --
a gap in the bank, a cave beside the shore, lava -- is refused, naming
each `Leak`, unless `rim` walls it: the rim goes into every cell beside the
outline the basin's own water would enter that holds air, ground cover, a
snow layer or lava, and never over a rail, a torch or anything built,
which stays a leak. The rim is held before anything is read to being a
wall to water -- whole on every face, nothing water can enter, and not ice
or a sponge -- while a rim that falls, sand on a bank, is left to the
survival audit, which keeps sand on firm ground. Water the basin lets in
from elsewhere -- a river beside a cut lower than its waterline -- is not
the rim's: it is refused unless `seal=` walls it off where it stands, and
a seal that takes the feed of a flow below it is refused for its starve
until `allow_dam=True`. The rim and the seals are refused inside a
keep-out not named in `through=`; the outline is the caller's. Last, what
the plan leaves the game will not keep -- a torch on a bank the cut took,
sand lining over a cave -- is refused (`_audit_plan`). Cutting a body of
water in two is not refused: the caller drew the outline.

**A lit basin keeps the ice near it frozen.** `light_level` and `lamp` go
together -- one without the other is an `NBTError` -- and the lamp is one
block, checked before the ground is read (`_light_budget`): a full block
set flush into the floor (a sea lantern, glowstone, a froglight), or a
block that holds water, set in the lowest cell of water over the floor
(four sea pickles, as the frozen lake was lit; in water one deep that is
the surface itself). The lamps are placed by the game's flood
(`plan_lights`) so every surface cell reaches the level --
`NO_NEW_SNOW_OR_ICE` keeps the water open all winter -- and every
`minecraft:ice` cell over air the plan leaves standing within the lamps'
reach (found with `find_blocks`, and kept where it is no further by the
taxicab bound from some place a lamp could go than `L - 11`, since a lamp
of light `L` can pass 10 nowhere else) is held at 10 or under, since ice
melts at 11 into whatever room it roofs: "Ice over air is a lid"
(CLAUDE.md), here as `at_most`. A lid the world already lights past 10
-- a glowstone beside it -- and still would with the basin laid, its lamps
left out, melts whatever the lamps do: it is not held, nor refused on,
and `BasinResult.melting` names it. One the basin darkens, lit by lava it
fills with water, is held like any other. Given a cluster of four pickles
in every one of the frozen lake's 6,614 surface columns (JeffWorld,
read-only, 2026-10-22), the finder names every one of the 95 lids
`water_body` finds on the lake within its box, and 13 more in the canal
just past it, which `within=` had clipped. (Not the 71 the pickle plan
counted on 2026-09-21, which CLAUDE.md records: 47 of those are lids
still, the rest thawed onto water since, and 48 of the 95 lie in water the
plan's mask left out.) A lid one block off a pond's shore, which the
lantern the lattice would put at the corner lights to 13, gets the lamps
moved; a trough with a lid two off its side has nowhere to put a lamp that
the taxicab bound clears, and is refused, naming its dark cells and the
lids, with nothing written. No lamp ever lights a lid past 10, since every
place one could is held back; a basin whose own floor, lining or rim --
glowstone laid beside a lid -- or whose cut lets the world's light through
would, is refused as "basin would melt the ice near its water".
`BasinResult.light` is the `LightPlan`, `lids` the lids it held, and
`melting` the ones it found melting already.

The order is fixed and nothing is written or drawn before the last: the
arguments; the lamp's light; every block by name; the ground; the chunks
and heights the plan reaches (`_reach`, the write's own refusal word for
word); its lining; what it writes over; the starve check; the rim and
its keep-outs; the lamps; the water (`_contain_plan`, then the seals'
keep-outs and their starve); the survival audit; and the write -- each
column bottom up, lining, floor, water and air, then the rim, then the
seals. `WATER_SOURCE_CONVERSION` is taken as on, as the water check takes
it.
`BasinResult` holds `floor` (`{(x, z): y}` in `(z, x)` order), `water`
(cells of water written), `cut` and `raised` (columns dug into the ground
and built up from it), `lined` and `rim` (cells), `sealed`, `light`,
`lids` and `melting`.

**A moat poured from the top is falling water, and a basin lays sources.**
The walled city's moat in JeffWorld is eleven or twelve blocks of falling
water (`level=8`) under one skin of sources, poured in from above: not
`still`, and no bubble column can stand in it, since
`BubbleColumnBlock.canOccupy` takes a liquid block only where its fluid is
a source (javap, 26.3) -- a magma block on its bed raises a column only as
far as the water over it is a source. No writer that checks its water
lays that shape -- a source written over air falls, and `contained` names
it a `"falls"` leak -- though `set` and `fill` still write what they are
told. A basin writes every cell of its water as a source, up from a floor,
so a moat laid as one is still from bed to surface.

### Planting at scale

```python
from heaton_voxel.craft import Mix, Tree, geometry

grove = b.scatter(geometry.disc(20, center=(gx, gz)), Tree("oak"),
                  spacing=6, seed=3)                # a wood, spaced
b.scatter(lawn, [("minecraft:poppy", 3), ("minecraft:dandelion", 2),
                 ("minecraft:lilac", 1)],
          clump=4, cover=0.3, apart_from=grove.columns)   # flowers in clumps
b.scatter(shore, "minecraft:sugar_cane", density=0.2)     # where water is beside
b.scatter(field, "minecraft:mossy_cobblestone", spacing=9)  # boulders
b.scatter(bed, Mix([("minecraft:poppy", 2), ("minecraft:cornflower", 1)],
                   clump=2), overwrite=True)   # over the grass tufts there
grove.planted, grove.columns, grove.kinds         # what each call returns:
grove.skipped, grove.thinned, grove.crowded      # a ScatterResult
```

`scatter(area, things, *, density=1.0, spacing=1, clump=1, cover=1.0,
seed=0, on=None, avoid=(), apart_from=(), through=(), overwrite=False)`
plants over `area` -- a rectangle `(x0, z0, x1, z1)` or any `(x, z)`
columns, taken once each and in `(z, x)` order whatever order they come in
-- what the park was planted with by hand: woods, meadows, flower clumps
and an understory (`tools/park/build_park.py`). `things` is one block, one
`Tree`, a `Mix` of blocks (picked at `(x, 0, z)`, its own seed and clump
drawing its picture), or `(thing, weight)` pairs of blocks and Trees.
`Tree(kind, *, leaves=None)` is `ornamental_tree`'s tree as a value: a
wood, required, and its trunk four or five logs under a persistent canopy,
`leaves=` naming one where the wood's name does not; the two nether woods
are refused as the Tree is made, since a fungus grows on nylium, a footing
craft holds nothing to (`ornamental_tree` still plants one where it is
told). Two Trees that grow one tree are equal and plant one grove: the
wood's own leaves named outright are the wood's own, so `Tree("oak",
leaves="minecraft:oak_leaves[persistent=true]")` is `Tree("oak")`.

**Keyed, not drawn.** Every die is `craft.noise.cell_hash` at the column,
`y = 0`, keyed on `seed` folded with a CRC of the things and their weights
(written as a `Mix` writes its entries), each roll under its own salt:
`density`, a share of the columns over 0 and at most 1, rolled per column;
`cover`, the same, rolled per `noise.patch` of about `clump` columns (1 to
`noise.MAX_PATCH`), so `clump=4, cover=0.3` plants three patches in ten
whole -- the park's seventy clumps, one flower each -- and from a list one
kind is picked per patch too, in proportion to the weights, by a Mix's own
arithmetic. Nothing is drawn from the session's `rng`, so a call that
draws from it -- a list-mix road, an `ornamental_tree` -- moves no
planting after it, the same area, things and seed roll the same dice in
any session and in any order the area comes in, and regrading the ground
under a planting does not re-roll it. Where the dice land is read, though:
the same call over the same world, session ground, keep-outs, paths,
flight claims and guard plants the same columns and writes the same bytes,
and an earlier call that writes, paves, reserves or plants where it plants
changes what it plants -- two scatters over one lawn plant differently in
the two orders.

**Only what craft holds to the game's rule, on the soil that rule names.**
Before a column is read, every thing is checked by name: a block the game
lacks is the call's `IllegalBlock`, and a thing is planted only if
`craft.survey.unsupported` holds it to what keeps it -- a flower, a tuft of
grass or a fern, a sapling, a berry bush, petals or one of the six tall
plants on the `supports_vegetation` tag; an azalea on `supports_azalea`; a
dead bush on `supports_dry_vegetation`; sugar cane on
`supports_sugar_cane`, with water (`holds_water`) or frosted ice beside
its block; a `Tree` on its sapling's soil, `supports_vegetation`; and a
block the game has that is sturdy on all six faces, that the survival
audit asks nothing of and that does not melt -- a boulder of mossy
cobblestone, a pumpkin, packed ice -- on anything. Ice, frosted ice and
snow layers are refused as boulders: ice turns into a water source on the
lawn at block light 11, frosted ice by daylight, a snow layer goes at 12
(`IceBlock`, `FrostedIceBlock`, `SnowLayerBlock`, 26.3). That is all a
boulder is held to: copper still weathers and a coral block dies out of
water, and a block that carries a block entity is written as its state
alone, as every Builder writer writes one. The tables are survey's, held
there to the tags vendored from the jar.
Anything else -- a cactus, bamboo or a bamboo shoot, a mushroom, a lily
pad, a pitcher plant, dry grass, sand, a chest, a modded plant under
`allow=` -- is an `NBTError` naming it, as is a `Tree` with `spacing`
under 3: a canopy reaches two columns out, and a trunk at Euclidean
distance 3 or more is outside every other canopy. Planting by guess is how
ten of TestWorld (2)'s Roman garden flowers stand on its gravel paths
(read 2026-10-22): gravel is natural ground, and no flower's soil.

**Where nothing is planted.** Each column is passed over, never written,
and counted in `ScatterResult.skipped` under its reason, in this order:
`"avoided"` (in `avoid`); `"paved"` (a path this session laid -- a `road`'s
swath, a `pave`'s floor, recorded once written -- since a lane's coarse
dirt, a sixth of `LANE_MIX`, is soil to a poppy; a path laid in an earlier
session goes in `avoid`); `"keep-out"` (a pad or a reservation not named in
`through`) -- each of those three for a tree wherever a column its
canopy reaches is, the twenty round its trunk, since leaves hung over a
road's edge are a hedge across it; then, the dice passed, its ground read
(`probe`, or the height the session gave it): `"no ground"` (a chunk not
generated or not finished, or nothing to stand on), `"water"` (water or an
ice sheet), `"wrong ground"` (a ground block `on` refuses -- a predicate on
its id, `is_natural_ground` unless given), `"off its soil"` and `"dry cane"` (the
thing picked there will not keep on it), `"occupied"` (its cells -- a
plant's one or two, a tree's trunk -- hold anything but air; with
`overwrite=True` ground cover there, lying snow included, is replaced and
the other half of a two-block plant cleared with it, unless something
fragile stands on what it takes, a stack of cane on its foot);
`"claimed"` (a cell it could fill, a tree's whole canopy among them, is a
tread or the head room of a `flight` or an `approach` this session built:
nothing goes there, a leaf no more than a stone, as `shell` and `furnish`
hold);
`"cannot be written"` (its cells, a tree's whole canopy box, reach an
unfinished chunk or past the world) and `"refused by worksite"` (the
Builder's guard refuses them: a file the snapshot does not hold, a
protected zone). A chunk that cannot be read is corruption and raises.

**Spaced by dart throwing.** The columns left are taken in an order keyed
on the cell -- its roll under its own salt, then `z`, then `x`, a total
order -- and each is kept unless a planting kept before it, or a column of
`apart_from` (an earlier call's `ScatterResult.columns`), stands closer
than `spacing`, `dx² + dz² < spacing²`: random sequential adsorption, so
the planting is maximal -- every column passed over for the spacing is
within it of a planting, counted in `crowded` -- and depends on nothing
but the inputs. Whether a column's cells can be written, and whether a
worksite admits them, is asked only of one nobody crowds out, and one that
cannot be written crowds out nobody.

**One plan, audited.** The plantings are written in `(z, x)` order: a
block set in the cell over its ground, a tall plant placed whole, a tree
as `ornamental_tree` grows one, its trunk height a die of its own and its
canopy filling only the cells that hold nothing -- the world's, or a
planting's before it, so a leaf two trees could both grow is the first's,
exactly `ornamental_tree` at each in that order. Under a grass block,
podzol or mycelium the ground's `snowy` flag is written as the block now
on it sets it, as the game's block update would (`SnowyBlock.updateShape`):
cleared where a planting took a snow layer's place, set under a snow block
boulder. The whole plan goes through the survival audit (`_audit_plan`)
before anything is written --
a boulder `on` let stand on a chest is refused there, since a chest under
a conductor will not open -- and a call that would plant nothing is
refused, naming how many columns each reason passed over. `ScatterResult`
holds `planted` (the cell each planting stands in, the cell over its
ground, in write order), `columns` (the same as `(x, z)`), `kinds` (a
count by the block's state text or the Tree's, `"tree:oak"`), `skipped`
(each reason that passed a column over, with the columns, `(z, x)`),
`thinned` (columns the dice passed over) and `crowded`.

### Approaches: steps and ramps on the ground

```python
from heaton_voxel.craft import Material

a = b.approach((px, 61, pz), (px, 64, pz - 6), kind="steps",
               material="minecraft:stone_bricks", width=3)   # a plaza to a bank
b.approach((dx, dz + 5), (dx, 64, dz), kind="ramp",          # a column's ground
           material=Material("minecraft:cut_sandstone"),     # to a doorstep
           fill="minecraft:sandstone")
b.approach(shore, jetty, kind="steps", material="minecraft:oak_planks",
           over_water="bridge")
b.approach(lawn, bank, kind="steps", material="minecraft:stone_bricks",
           seal="minecraft:cobblestone")    # a cut beside a pond, walled off
a.start, a.end, a.heading, a.kind, a.profile, a.columns
a.treads, a.slabs, a.blocks, a.filled, a.cut, a.sealed
```

`approach(start, end, *, kind, material, width=1, fill=None,
over_water=None, seal=None, through=(), overwrite=False, walk=True)` lays
the run between two places a walker must get between -- a plaza and the
bank above it, a road and a doorstep -- cut and filled out of the ground,
and walks it both ways **without a jump** before anything is written.
`start` and `end` are each the block stood on there, `(x, y, z)` (for a
door, the block under its lower half; for a pad, its edge cell), or a
column `(x, z)` standing on its `ground` -- the height this session gave
it, or its probe's -- refused where that is water or an ice sheet. Neither
end is written. They lie on one line along x or z with at least a row
between them -- a diagonal, adjacent ends and anything but those two forms
are an `NBTError`, a dogleg being two approaches to a landing the caller
lays -- and the run is the cells between, `width` columns across each row
(a whole number, rounded up to odd and centered, as a road's is).

`kind` is required: `"steps"` lays a stair of the material's wherever a row
rises and its block where it does not; `"ramp"` climbs half a block a row,
a bottom slab and then a block. `material` is required too, a `Material`
or any block of the family; a form the kind needs and the family lacks is
its `IllegalBlock`, and a `Mix` a `TypeError`, before anything is read.
`fill`, what goes under the surface where the ground is lower, is a block,
a `Material` (its block) or a `Mix`, the material's block when left out
(see the defaults table: the park built its gates up in stone bricks).

**A whole-block step is a jump.** The park's gates (`tools/park/build_park.py`,
`approach`) were made walkable by lifting or cutting each column until no
step was more than a block -- 61, 62, 63 through JeffWorld's north gate,
read 2026-10-22 -- and a block's top is a whole block up, where a walker
rises 0.6 unbidden: from the park's edge at (-9, 61, 172), a walk that may
not jump reaches 8 cells, the edge row and one beside the gate, and one that
may reaches the road at z=168. An approach's profile is one level per row from
side to side (the park's south gate, cut column by column, reads 62, 61,
60, 59 down one column and 62, 63, 63, 63 down the next), monotone from
the low end up, rising at most a level a row as steps or half a block as a
ramp, with its last row flush with the high end -- a tread one under a
full-block end is a jump onto it, `StraightStair`'s "last tread flush with
the exit floor" -- or, for a ramp, its slab half a block under. Where the
low end has something a body cannot pass three over it with room under it
(a door's lintel), the first row is held level with the end, since a walker
stepping down onto it from a row a level higher has their head in that
cell (`HEAD_ROOM`). Of those profiles the one that cuts and fills least --
the cells it changes between holding something and holding nothing, which
over open ground is `sum |level - ground|` -- is laid, and of those the one
that rises latest: the lowest row by row, a total order. Where there is
none it is refused, saying why: too steep (`n` rows climb at most `n`
blocks as steps and `(n + 1) // 2` as a ramp), the lintel, or a waterline
above what the ends let a row stand at. With as many rows as blocks to
climb, the treads are a `StraightStair`'s from the low end's next row,
cell for cell, and run beside the park's own approach over a bank two
blocks over its lawn, the profile, the cut and the fill are the park's --
only the surface differs, a stair where the park left a whole block to
jump (`tests/test_approach.py`).

**The ground.** Every column is read (`_stand`), and one with no ground
raises `NoSurface`. Water and ice are refused unless `over_water` says
what to do (`_build_base`): `"bridge"` lays the row at the waterline or
over it, on the water; `"fill"` fills it from the bed, refused where that
starves flowing water and pointed at `grade(drain=True, allow_dam=True)`.
Each column is filled from its ground up to its surface, the surface laid,
and cut to air over it: a row cut into a bank, solid up to its ground, is
cut open to the sky, up to the cell over the ground -- a flower, the snow,
a tall plant's upper half with its lower -- and every row to at least
`HEAD_ROOM` over its surface. **An overhang is not the ground**: under a
ledge, a tunnel's roof or a cave's ceiling the column reader stands the
column on the overhang's top, so a column with a hollow between the row
and that top is cut only to the head room (and the cover standing on the
top of that cut), the overhang left standing, and a surface laid into the
hollow is filled under, down to what holds it. A level run into a tunnel
bored through a hill fifteen high cuts nothing over the bore, and a run
under a ledge eight over the lawn stays on the lawn and rises late, where
counting the ledge's top as the ground climbed toward it on a causeway
and sliced the ledge through (`tests/test_approach.py`).

**Before anything is written**, each refusing the whole call: the
arguments and every block by name; the keep-outs, a pad or a reservation
the run crosses unless named in `through`; the ground; the profile; the
water and ice under the run; the chunks and heights the plan reaches;
lava -- a plan cell holding it, or one the plan opens beside lava or under
it, air or anything the fluids wash away where it held what kept the lava
out -- which no flag passes and no seal mends, since `_contain_plan`
follows water and lava runs all the same; what it writes over -- air,
ground cover, natural ground, an ore or a tree it takes
(`build._common._takes`, the occupancy rule basin, approach and extrude
share),
and the water and ice of the columns `over_water` bridges or fills; water
or ice in a column whose ground is dry, a spring in the bank, which no
flag speaks for; and anything else, a fence, an eave, a wall, a path, is
refused, every cell named, unless
`overwrite=True` (an eave over the run is somebody's, and the alternative
is `level_pad`'s cut-anything, which would cut one over a door); the cells
a `flight` or another approach this session built claims, which no
`overwrite` passes; a cut that would open a pond's bank to the air
(`_contain_plan`, each `Leak` named) unless `seal=` walls the water off
where it stands -- the seals checked against the keep-outs and the starve
check, as a causeway laid at the waterline is; the survival audit, a torch
on the bank the cut takes or sand over a hole; and, unless `walk=False`,
the round trip, `reachable(..., allow_jump=False)` from `start` over the
run's own columns and its ends and nothing beside them -- so the way back
down is the approach's own, not a drop off its side onto the lawn --
refused unless `end` and every surface cell are reached and walked home
from. A body of water a filled approach cuts in two is not checked: the
split check is a road's.

Then the plan is written, row by row from `start`, each column bottom up,
the seals after. The session learns each column's ground at its surface;
the surface cells and the `HEAD_ROOM` over them join the flights' claims
(`_claimed`), so a later flight or approach, a shell, a piece of furniture
or a planting is refused there or passes it over, the refusal saying whose
claim it is; and the run is a path the session paved, which `scatter` plants
nothing on. Nothing is drawn from `rng`. `ApproachResult` holds `start` and
`end` (the cells stood on), `heading` (the way it climbs, which its stairs
face -- from `start` to `end` where they stand level), `kind`, `profile`
(one `(level, "stairs" | "slab" | "block")` per row, `start` to `end`),
`columns` (the run in write order), and counts: `treads`, `slabs` and
`blocks` (surface cells), `filled` (under the surface), `cut` (cells that
held something and were cut to air) and `sealed`.

### A component through a frame, and a schematic stamped whole

A gatehouse, a stall or a window bay is designed once, facing one way, and
built facing every way the plan needs. `b.framed(frame)` is the session
seen through a `craft.geometry.Frame`: a `Framed` view whose `set(across,
up, out, block)`, `place(...)` and `fill(corner1, corner2, block)` take the
component's own cells and states, designed facing the identity (out is
+z, south), and whose `get(across, up, out)` reads a block back in the
frame's terms (`frame.unstate`). The frame turns each cell and state
together, and the Builder writes the result through its own `_commit`, one
op per call, so a framed write is byte for byte the write made by hand at
`frame.cell(...)` with `frame.state(...)`: the same journal (world cells),
stage, `rng`, guard and `finish()`. One thing more is turned: a property
the state leaves unsaid is at its default -- a bare `minecraft:oak_stairs`
faces north, a bare `minecraft:wall_torch` hangs on the block to its south,
a door that names no hinge is hinged left -- and `Frame.state`, like a
schematic's turn, turns only what a state carries. A framed write turns the
defaults too and writes each one the turn moved, and only those: through a
frame facing west the bare stair is `oak_stairs[facing=east]`, mirrored the
door gains `hinge=right` on both halves, a bare fence or log stays bare,
and a `waterlogged` left unsaid stays unsaid, so a placement into a water
source still waterlogs. `get` reads a bare state back the same way. Its
checks come in the Builder's order: the block as the caller wrote it
(`IllegalBlock` names the state unturned), then the turn (a state with a
direction the schematic vocabulary does not cover, a modded one under
`allow=`, is `craft.Refused` in any frame that turns, naming the state and
the frame), then the chunks, heights and guard over the world cells, each
of those refusals opened by the local cell and the frame. A `Mix` is
picked at the **world** cell and the pick then turned, so four turned
copies of a component lay the same stones, each facing its own way -- the
field is the Mix's, and turning its entries into another Mix would be
another field. A list mix a writer draws from the `rng` is turned entry by
entry and drawn as it would be. A view holds nothing of its own; frames
compose through `Frame.within` (`b.framed(inner.within(outer))`), and a
non-Frame is an `NBTError`.

```python
from heaton_voxel.craft import Frame

for facing in ("south", "west", "north", "east"):      # four gatehouses
    gate = b.framed(Frame(ring_cell[facing], facing))
    gate.fill((0, 0, 0), (4, 5, 0), Mix([("minecraft:stone_bricks", 3),
                                          ("minecraft:mossy_stone_bricks", 1)]))
    gate.place(2, 1, 0, "minecraft:oak_door[facing=north,half=lower]")
```

`b.stamp(schematic, at, *, rotate=0, mirror=None, paste_air=True,
entities=True)` pastes a `Schematic` or a `.schem` path with its origin at
`at`, its stored offset honored as `//paste` honors it. `mirror` (`"x"`
swaps east and west, `"z"` north and south) and then `rotate` (a multiple
of 90, clockwise from above) turn it about that origin as `import_schem`'s
`flip=` and `rotate=` do. Its cells, counted from its origin, land where
`Frame(at, turn("south", rotate // 90), mirrored=True)` puts them with
`mirror="x"`, turned as that frame turns them -- with `"z"` the same a half
turn further, and with `None` unmirrored. Like `import_schem`, a stamp
turns the properties each state carries, so one the schematic stores bare
keeps its defaults' facing; a schematic the game writes, or one exported
from a 26.3 chunk, carries every property. It is written by the lower
tier's paste, so it encodes exactly what `save.import_schem(schematic, at,
rotate=, flip=, relight=False, keep_loaded=True)` encodes, block entities
and entities (with fresh identities) included -- and before that paste,
every Builder check runs over it: every state it writes is one the game
has (`IllegalBlock`, a 1.20 file's `minecraft:chain` named with
`minecraft:iron_chain`); a stamp that turns turns every state it writes
(`Refused` otherwise); its whole box -- air too, even with
`paste_air=False` -- and the cell each entity lands in are in generated,
finished chunks inside the world; and a worksite's guard admits the box as
a block write and each entity as a spawn. A schematic made by a newer
Minecraft than `level.dat` is an `NBTError`, as the lower tier's paste
refuses it, and so are bad arguments (a `"y"` mirror among them: flip the
`Schematic` itself), before anything is read. Biomes are never written,
and nothing is relit until `finish()`, which settles a stamp as it settles
any write. The journal holds the box, counted as the blocks pasted, or with
`paste_air=False` each cell written; `entities_added` counts the entities;
the result is the lower tier's `SchemImportResult`.

**A block entity or an entity does not cross a DataVersion.** The game
reads a stored block entity and a stored entity by the DataVersion of the
chunk holding it, and data-fixes it from there, so one saved by another
version is misread and nothing here can convert it. A stamp is refused,
naming each, for every block entity it would write into a terrain chunk
whose DataVersion is not the schematic's -- whatever `entities=` says,
since a block entity is not an entity -- and, unless `entities=False`, for
every entity whose entity chunk (the one already stored there, or the one
the paste would make at `level.dat`'s version) is not at the schematic's.
`entities=False` leaves the entities out, written and checked not at all.
Blocks themselves are never refused for it: each chunk keeps its own palette
codec, and legality covers the states. Entity storage that cannot be read
is a plain `NBTError`.

**Only a stamp clears stale block entities.** A stamp removes the block
entities its paste overwrites, inside its own box, as the lower tier's
paste does: stone stamped over a chest takes the chest's inventory with
it. `Builder.set`, `fill` and `place` -- and the lower tier's `set_block`,
`fill` and `place_block` under them -- write states only, and leave a
block entity where the block that owned it was; a per-cell
`save.set_block_entity` is a separate, twinned item still to come.

### Winding stairs and the shafts they go in

A spiral stair is one rule plus a phase, and `SpiralStair` holds them apart
from the blocks so a stair can be *continued* — read one out of the world
and the same object writes the rest of it, in the same hand, on the same
phase, out of the same materials:

```python
from heaton_voxel import craft
from heaton_voxel.craft import NO_SPAWNS, Builder, SpiralStair

b = Builder(save)
b.shaft((x, z), 64, 131, radius=1,           # hollow, line and light it;
        wall="minecraft:stone_bricks",       # carve three levels past the
        light="minecraft:glowstone",         # top step, for headroom
        light_level=NO_SPAWNS)
b.spiral_stair((x, z), 64, 128)              # a fresh oak stair
craft.spawnable(save, (x - 1, 64, z - 1),    # the stair shades what the
                (x + 1, 128, z + 1))         # shaft's light was planned for

spiral = SpiralStair.read(save, (x, z))      # or continue one that exists
print(spiral, spiral.found, spiral.unfinished)
b.spiral_stair(spiral, None, 318)            # None = pick up where it stopped
```

Every level is a 2×2 quadrant of the 3×3 shaft — newel at the center, a
stair in the corner you step off, treads on the other two — and the
quadrant turns 90° per level, so the period is four. Two properties fall
out of that rule and are worth knowing: the tread you land on coming down —
the diagonal beside each step — gets **`HEAD_ROOM` clear blocks above it**,
because its column is not filled again until four levels up, while the step
gets two and then the tread the stair turns onto three levels higher, which
is no ceiling, since a step is entered level from the tread beside it and
never from above; and every step lands on a tread the level below already
put there, because one level's tread is the next one's footing.

| call | does |
| --- | --- |
| `SpiralStair(center, *, turn, start, start_y, stair, tread, newel)` | a fresh rule; `turn` is `"clockwise"`/`"counterclockwise"` **as you ascend**, `start`/`start_y` fix the phase |
| `SpiralStair.read(save, center, *, low, high, tread, newel)` | lift the rule off a stair that exists, and **refuse if it does not reproduce it** |
| `.cell(y)` / `.facing(y)` / `.level(y)` / `.walkway(y)` / `.interior` | the geometry: where the step is, which way it faces, the whole level, the two columns you stand on, the nine columns of shaft |
| `.tread_cells(y)` / `.support_cells(y)` / `.claimed(y)` / `.clear(y, columns)` | the walkway, what the level above stands on (the next step's footing and the newel), and what the stair owns — the whole shaft, at every level, since every gap is the head room of a tread below it |
| `.verify(save, y0, y1, *, missing_ok=False)` | every level that is not what the rule says, as readable complaints |
| `.found` / `.unfinished` | the run `read` lifted this from, and which of its levels were never finished |
| `Builder.spiral_stair(spiral, y0, y1, *, newel, overwrite, headroom)` | build it; `y0=None` continues a stair that was read |
| `Builder.shaft(center, y0, y1, *, radius, wall, hollow, light, light_side, light_level, every)` | hollow, line and light the shaft it goes in |

**`read` verifies before it is trusted.** Continuing a stair means writing
hundreds of blocks from a rule inferred out of a handful, and a misread
rule prints itself all the way up the tower before anybody sees it — so
`read` replays what it derived over every level it found and refuses on the
first disagreement, naming it. It also refuses a run with a gap (a landing,
a second stair, or a mistake — say which segment you mean with `low`/`high`)
and a single step (which cannot say which way it turns). An **unfinished**
top level is expected rather than an error: a stair placed with its treads
not yet is where a hand-built run always stops, so it lands in `.unfinished`
and `y0=None` picks up *inside* it instead of stepping past the hole.

**`spiral_stair` refuses twice, before writing anything.** Something already
in the shaft stops the call by name (carve first with `shaft`, or pass
`overwrite=True`); and any column of the walkway without `HEAD_ROOM` clear
blocks above it stops it too — three, for the reason a switchback's are, so
a shaft carved only two past its top step is a stair you climb to a door
above it and cannot come back down from. Clear means empty, any of the
game's airs, because a spiral's gaps are part of its pattern and `read`
refuses anything in them; the one block passed is the stair's own tread
three levels over each step. A stair that runs into its own ceiling looks
perfectly good in a top-down render. `shaft` derives its lamp spacing the
way `pave` does — a lamp in the wall is `3 * radius + 1` blocks from the far
interior corner, so a source of strength `L` can stand
`2 * (L - reach - light_level) + 1` levels from the next and still hold the
shaft — then checks every cell inside it by the flood, over the shaft as it
will stand, and patches what falls dark with another lamp up the same wall:
the top of a run, and the far side of a newel in a shaft left solid round it
(`hollow=False`), which light has to go round. That derived spacing is a
proposal, and a level the world already lights gets no lamp; `every=` sets
the spacing instead and keeps it, a lamp every `every` levels up from the
bottom whatever the flood would spare, patched only where those leave dark.
A shaft its wall cannot light anyway is refused before anything is carved.

### Switchback stairs

```python
from heaton_voxel.craft import SwitchbackStair, HEAD_ROOM

stair = SwitchbackStair((x0, z0), (x1, z1), axis="z", start="northeast",
                        start_y=68, landing=3,
                        stair="minecraft:birch_stairs",
                        wood="minecraft:stripped_birch_wood")
b.switchback_stair(stair, 68, 186)

stair = SwitchbackStair.read(save, (x0, z0), (x1, z1))   # or continue one
b.switchback_stair(stair, None, 186)                     # None = where it stopped
```

A dogleg: two flights side by side, walked in opposite directions, with a
landing turning you round at each end. The rule is a rectangle — what the
treads sweep, `run` rows long by twice `width` columns across — and a
period of `2 * run`. Because **a turn happens twice a cycle**, the story
pitch such a stair permits is `run` and not the period; that is the whole
reason to keep it as a rule, since the staircase decides where a floor can
go and the arithmetic does not.

| call | does |
| --- | --- |
| `SwitchbackStair(corner1, corner2, *, axis, start, start_y, landing, stair, wood, skip, span)` | a fresh rule over the tread rectangle; `start` is the corner the first flight sets off from, which fixes both its end and its half |
| `SwitchbackStair.read(save, corner1, corner2, *, low, high, axis, landing, wood, skip)` | lift the rule off a stair that exists, and **refuse if it does not reproduce it** |
| `.tread_cells(y)` / `.tread_facing(y)` / `.support_cells(y)` / `.landing_cells(y)` / `.level(y)` | the geometry: the treads, the way you walk, the stringer under the tread above, the turn, the whole level |
| `.claimed(y)` / `.clear(y, columns)` | what the stair owns at a level, and its inverse — **where a floor slab and any furniture may go** |
| `.is_landing(y)` / `.landing_levels(y0, y1)` / `.pitch` | where a floor lands flush with a turn, and how often |
| `.walkway(y)` / `.columns` | the cells you stand on, and every column the stair can touch |
| `.check(save, y0, y1)` / `.verify(...)` | `(disagreements, extras)`, or both lists at once |
| `.found` / `.span` / `.unfinished` / `.extras` | the run `read` lifted this from, what it occupies, its half-built levels, and what stands in its gaps |
| `Builder.switchback_stair(stair, y0, y1, *, overwrite, headroom)` | build it; `y0=None` continues a stair that was read |

**`claimed` is the part no picture shows, and the head room is three.**
Three things own a cell at a level: the tread standing in it, the wood a
tread above will sit on, and the head of whoever is on the treads below.
Standing squarely on a tread your head reaches two cells — but the game
resolves horizontal movement *before* gravity, so for one tick as you step
off, your feet are still at the old height while your box already overlaps
the next cell down the run, and your head is in the **third** cell. Two
cells of clearance is a stair you can climb and cannot descend, which every
render, floor plan and cutaway draws finished. So a sweep laying a story
at `y` must start at `y - HEAD_ROOM`, and `Builder.switchback_stair`
refuses a ceiling closer than that — a ceiling being what a body cannot
pass, as the walk judges it, so a torch or a sign on the stairwell wall is
none.

**`read` tells the treads from the furniture.** A stair read out of a
finished building competes with what people put in it — a seat is a stair,
and a pair of them facing each other across a lounge is not a flight — so
the rule is derived only from the levels offering exactly one flight (one
contiguous row, all of it facing one way), the axis and width are decided
by **vote across the run**, and the replay judges the rest. A block
standing where the rule wants nothing is an `extra` and not a fault;
conflating the two is how a read either refuses a furnished tower or
accepts a broken one. Whole states are compared, but only the properties
the rule *names*: the `facing` of a tread is the rule's, while the `axis`
of the log under it is the aim of whoever placed it, and a hand-built
flight has logs lying every which way.

Both stairs share that read-then-verify half as `StairRule` — `level`,
`check`/`verify`, and the replay that refuses a rule which cannot
reproduce the levels already standing. The refusal is the tool; the
writing is the easy part.

### Vaults, arcades and the lamps that hang under them

A stepped gable drops a **whole** block per block outward, which is why a
roof laid in stairs reads as a staircase. A vault drops **half** a block,
because a bottom slab and a top slab are two different heights inside one
block — slabs give a roof twice the vertical resolution stairs do, and at
that resolution a curve stops looking like steps. `Vault` is that rule, held
apart from the blocks the same way `SpiralStair` is, so a vault can be read,
extended, or lifted off one building and extruded over another:

```python
from heaton_voxel.craft import NO_SPAWNS, Builder, Vault, arch_profile
from heaton_voxel.craft import pointed_arch_profile

b = Builder(save)
b.arcade((cx-3, z0), (cx-3, z1), 64, 76, pitch=6,   # the piers, at a pitch
         pier="minecraft:quartz_block",
         cap="minecraft:quartz_slab[type=double]")  # the impost it springs from
vault = Vault((cx, 80, z0), axis="z", half_span=7, pitch=1, eaves=4)
b.vault(vault, z0, z1)                              # the shell, half a course
b.hang_lamps(cx-3, z0+1, cx+3, z1-1, 80, 64,        # room to light, floor
             drop=7, columns=[(cx, z) for z in range(z0+1, z1)],
             light_level=NO_SPAWNS,                 # the rule it is lit for
             anchor="minecraft:cobblestone_slab[type=double]")  # a boss over each

read = Vault.read(save, (cx, 80, z1))               # or lift one that exists
print(read.profile, read.depth, read.found, read.differences)
b.vault(read, None, z1 + 40)                        # None = extend the run

lancet = Vault((cx, 90, z0), profile=pointed_arch_profile(7, 12),
               close_ends="minecraft:quartz_block")  # walled at both ends
b.vault(lancet, z0, z1)
```

The rule is two short tuples of integers. `profile` is how far the top of
the shell has fallen below the crown at each block outward, in **half
blocks**; `depth` is how thick the shell is at each of those, defaulting to
whatever reaches the top of the column outside it — which is what leaves the
shell gapless seen from the end, and makes the haunch of a steep profile
come out solid without anybody saying so. `pitch=1` is the half-block ramp,
`pitch=2` an ordinary stepped gable, a sequence steepens toward the eaves,
and `arch_profile(half_span, rise)` quantizes a circle to half blocks.
`pointed_arch_profile(half_span, rise)` is the Gothic one: two arcs
centered on the springing line past the crown, meeting at a point,
quantized the same way. It rises at least its half span — at `rise ==
half_span` it is `arch_profile`'s round arch, course for course, and lower
is an `NBTError` — and it falls away from its crown at once where a circle
is level: `(7, 12)` is `(0, 1, 3, 5, 7, 10, 14, 24)` against
`(0, 0, 1, 2, 4, 7, 12, 24)`.

Four materials, each with a job: `slab` is the half course; `block` a whole
one; `stair` the top course of a column that falls a whole block or more to
the next, facing the crown; and `springer` a whole course at an offset
thickened past what the profile needs — the block a vault lands on where it
crosses a pier.

**`close_ends=` walls a run off at both ends.** A fifth material, and no
default: the block the end walls are built of, from the vault's
`springing` — the lowest block any course reaches down into, which is
also where `clear_under` starts — up to the shell, with every top slab
over the wall made a double slab. That is how the walls under JeffWorld's
nave meet its vault: quartz from the springing, and a whole slab over each
wall, the same sixteen cells at z=89, 95 and 111. A wall filled only up to
the underside of the shell, as `tools/nave/build_hall.py` fills its gable
ends, leaves the lower half of each top slab open — four slots per end of
that hall. `b.vault(v, first, last)` closes the courses at `first` and
`last` with `v.section(closed=True)`, held to the same occupancy check as
the shell — with `clear_under`, what stands in the span it empties is cut
for the walls as for the rest of it — and `v.verify(save, first, last)`
holds those two courses to it (`missing_ok` forgives air in a wall too).
Left out, or `False`, the run is open, and builds byte for byte as before.
It is refused as air or `True` (`NBTError`) and as a `Mix` (`TypeError`).
Only a fresh run closes: a continuation's first course is the seam with
the run already standing, which a wall would close off, so a read vault
given `close_ends` and continued (`first=None`) is an `NBTError`. Nor is a
run closed in the world continued. `Vault.read` reads no end walls — a
strict read of a closed run is refused on its whole slabs, and a loose one
comes back open with them among its `differences` — so `b.vault(read,
None, last)` looks at the end course it picks up from, and when that is
closed as `close_ends` closes one, one block from the springing to the
shell and its top slabs whole, refuses by name before anything is written
rather than leave the wall standing across the hall. A gable filled some
other way, as `build_hall.py` fills its own, is not that, and is the
caller's to see.

| call | does |
| --- | --- |
| `Vault(crown, *, axis, half_span, pitch, eaves, profile, depth, slab, block, stair, springer, close_ends)` | a fresh rule; `crown` is `(x, y, z)` and the coordinate along `axis` is ignored |
| `Vault.read(save, crown, *, axis, low, high, half_span, block, springer, strict)` | lift the rule off a vault that exists, and **refuse if it does not reproduce it** |
| `.profile` / `.depth` / `.width` / `.rise` / `.half_span` / `.springing` | the rule, and what it comes to; `springing` is the lowest block level the shell reaches |
| `.top(d)` / `.thickness(d)` / `.column(d)` / `.section(*, closed=False)` / `.cell(along, offset, y)` | the geometry, in half blocks and then in blocks and then in the world; `closed=True` is an end course with its wall |
| `.verify(save, first, last, *, missing_ok=False)` | every cell that is not what the rule says, the two end courses held to the closed section when `close_ends` is set |
| `.close_ends` / `.found` / `.differences` | the end walls' block or None, the run `read` lifted this from, and the cells it does not account for |
| `arch_profile(half_span, rise, *, eaves)` | a circular arch, quantized to half blocks |
| `pointed_arch_profile(half_span, rise, *, eaves)` | a pointed (two-centered) arch, quantized the same way; `rise >= half_span` |
| `Builder.vault(vault, first, last, *, overwrite, clear_under)` | extrude it; `first=None` continues a vault that was read, never past an end closed in the world; returns a `VaultResult`, whose `.closed` is the end walls' block |
| `Builder.arcade(start, end, y0, y1, *, pitch, pier, cap, base, phase)` | piers at a pitch; returns the bay columns |
| `Builder.hang_lamps(x0, z0, x1, z1, ceiling_y, floor_y, *, drop, light, chain, light_level, anchor, columns, overwrite)` | lamps on chains, spaced so the floor clears `light_level` |

**`read` decides the width by vote, not by where you were standing.** A hall
is not the same all the way along: it has gable ends and buttresses, a wall
head often in the vault's own stone, and perhaps a course thickened to a
whole block at every place a lamp hangs from it (`hang_lamps(anchor=)` sets
one). So `read` samples sections across the
run, takes each course by majority, and stops where no majority is left —
that is where the roof stops being one vault. Then it replays the derived
rule over the whole run and refuses on disagreement, because extruding a
vault is thousands of blocks written from one section. `strict=False` hands
the rule back with the departures in `.differences` instead, which is the
setting for reading a building somebody made by hand. `half_span=` widens
the rule deliberately when the outside is regular too.

**`hang_lamps` is `light_cells` from above.** A floor lights itself from
underfoot; a hall lights itself from the ceiling, and the spacing is not the
same: a lamp near a high ceiling has spent most of its light climbing down to
head height before it travels a block sideways. That climb is subtracted from
the reach, the lattice is laid at what is left and every floor cell is then
checked by the flood and patched — a partition or a pier shades what a lamp
across it would light over open floor, so each side gets its own — and a lamp
that arrives at the floor already under `light_level` is a refusal naming
both heights, before a block is written. Light the hall already has is
counted, so asking again over a room its lanterns light hangs nothing, and a
pier's own cells are `blocked`, not dark. A lamp hangs only where it can:
from a ceiling the game would hang a lantern from, with nothing already in
the cells its chain and lamp take unless `overwrite=True` cuts through; other
columns are passed over and counted (`PaveResult.passed_over`), and a room
the rest cannot light is refused, its dark cells the refusal's faults, naming
the first column passed over and why. Under a vault there is nowhere to hang a
chain but the few columns the shell leaves clear, so `columns=` says where
one may go and cells no permitted lamp can reach are refused: that is the
honest answer to a hall too wide to light from one row down its ridge.

**A chain hangs from what a lantern hangs from.** `LanternBlock.canSurvive`
keeps a hanging lantern only while the block above is sturdy at the middle
of its underside and is not a fence gate (`Block.canSupportCenter`), and
`hang_lamps` holds a chain's ceiling to that, by the game's own faces
(`block_facts`): a full block, a bottom or double slab, a
bottom stair, a fence, a wall or a pane will do, and a top slab, a top stair
or leaves will not — the gap is under a *top* slab. The game itself keeps a
chain under anything, so this is a choice, the one that leaves a hung
lantern where it was hung. An id the game does not have is no ceiling, since
it loads as air. `anchor=` is a block set above *every* lamp hung, in place
of what the ceiling course holds there — a boss over each, or the ceiling a
column without one needs — and is refused before anything is read unless a
chain could hang from it too. A vault's own crown, a bottom slab, needs none.

### Walls, shells and openings

```python
from heaton_voxel.craft import Builder, Facade, Opening, StraightStair, Wall
from heaton_voxel.craft import pointed_arch_profile

b = Builder(save)
lancet = Opening.arch(3, 4, springer="minecraft:stone_brick_stairs",
                      profile=pointed_arch_profile(1, 2))   # a pointed head
front = Facade("minecraft:white_terracotta", pitch=4, phase=0,  # bays locked to the world
               pier="minecraft:cut_sandstone", corner="minecraft:cut_sandstone",
               plinth=(2, "minecraft:bricks"), bands={0: "minecraft:spruce_wood"},
               window=lancet, window_stories=(1,))
house = b.shell(x0, z0, x1, z1, front, floor_y=63, top=73, stories=(68,),
                floor="minecraft:spruce_planks", deck="minecraft:granite",
                parapet="minecraft:stone_bricks",
                openings=[("south", Opening.rect(1, 2), dx)])
b.doorway(*house.walls["south"].door(dx))                  # hung in the opening
b.flight(StraightStair((sx, 64, sz), "north", 5), floor_y=68)  # cuts its well
b.battlement(house.perimeter, house.top + 2,               # the parapet's row
             block="minecraft:stone_bricks")
b.cut_opening(house.walls["east"], Opening.round(1, sill=2), az, facade=front)
```

A wall is three things kept apart. **`Wall`** is where it stands: a
straight run from `start` to `end`, `(x, z)` on one axis, its rows from
`floor_y + 1` up to `top`, `stories` stacked inside it (two clear rows each
at least), `outward` the side the outside is on, and `thickness` layers,
layer 0 the run's own line and layer `k` that many blocks inward.
**`Facade`** is the rule its face follows, and **`Opening`** a hole of one
shape. No world in any of the three: `Facade.plan` and `Opening.cells` say
what they would write, and the writers lay them -- `wall` one run, `shell`
a whole building, `cut_opening` a hole in a wall already standing.

**Bays count along the wall's own run, from the world.** A wall's *along*
coordinate is the world's own x for a wall running east and west and z for
one running north and south, never a count from its end. Each column is a
corner (the ends of a wall with `ends`, `thickness` columns each, so a thick
corner is square), a pier where `(along - phase) % pitch < pier_width` --
`arcade`'s congruence -- or a bay. An integer `phase`, a coordinate a pier
stands on, makes two walls agree on every pier wherever they stand, which is
how the secret lab lined its buildings up (`phase=0`); `phase=None` counts
from each wall's `origin`, its low end, which `Wall.ring` makes one corner
for all four -- so two shells side by side agree only when given one phase.
A test per cell of the wrong coordinate is how the lab's stables came out
with two walls all posts and two with none.

| call | does |
| --- | --- |
| `Wall(start, end, *, floor_y, top, outward, thickness=1, stories=(), ends=False, origin=None)` | a run: `.along`, `.rows`, `.floors` (`(floor_y, *stories)`), `.inward`, `.cell(along, y, layer=0)`, `.story(y)`, `.floor_of(story)`, `.door(along, story=0)` |
| `Wall.ring(x0, z0, x1, z1, *, floor_y, top, thickness=1, stories=())` | `{"north", "east", "south", "west": Wall}`, in that order: north and south own the corners (`ends=True`), east and west fill between |
| `Wall.perimeter(x0, z0, x1, z1)` | the closed corner path round a rectangle: where a shell lays its parapet, and what `battlement` takes |
| `Facade(field, *, corner=None, pier=None, pitch=None, phase=None, pier_width=1, plinth=None, bands=None, head=None, window=None, window_stories=None, window_offset=None, margin=0)` | the rule: `.plan(wall)` every cell sorted, `.verify(save, wall, *, dimension=None, missing_ok=False)` what departs, `.kind(wall, along)` `"corner"`, `"pier"` or `"bay"` |
| `Opening.rect(width, height, *, sill=1, fill=None, head=None)` | a rectangle, `head` a lintel over it through every layer; `width=None` fills a facade's bay |
| `Opening.arch(width, height, *, springer, sill=1, fill=None, keystone=None, profile=None, slab=None)` | an arch `height` clear at its crown, its head following `profile` |
| `Opening.round(radius, *, sill=1, fill=None)` | `geometry.disc`'s rule on its side, `2r + 1` across |
| `Opening.cells(wall, at, *, story=0)` | every cell it writes from column `at`, sorted; pure |
| `Builder.wall(wall, facade, *, openings=(), overwrite=False)` | lay one run, `openings` `(Opening, at[, story])`; a `WallResult` |
| `Builder.shell(x0, z0, x1, z1, facade, *, floor_y, top, stories=(), faces=None, thickness=1, openings=(), ground=None, floor=None, deck=None, parapet=None, parapet_height=1, roof=None, attic=None, hollow=True, overwrite=False)` | a building, `openings` `(face, Opening, at[, story])`; a `WallResult` |
| `Builder.cut_opening(wall, opening, at, *, story=0, facade=None, overwrite=False)` | cut and dress one hole; returns the cells written |

- **Courses.** Up a bay each row is the first of the `head`, `(rows,
  block)`, the wall's top rows; a band, `bands={offset: block}`, `offset`
  rows from each upper story's floor row (`{-1: b, 0: b}` is the lab HQ's,
  under and on each floor); the `plinth`, `(rows, block)`, the ground
  story's bottom rows; and `field`. Piers run through every course, and so
  does a corner -- `corner`, else `pier` -- while a corner given neither
  takes a bay's courses, as the Roman insula's carry its plinth and bands.
- **Windows.** `window`, an `Opening`, goes in every bay of every story in
  `window_stories` (None for all), its bottom row `sill` over the story's
  floor, `window_offset` columns into its bay or centered between the piers
  -- the odd column of slack on the high-along side. It is placed whole or
  not at all: every column a bay's, inside the wall's end columns and
  `margin` columns from each end, so a window is never truncated and never
  cut through a pier or a corner. `Opening.rect(None, h)` glazes every
  column of each bay the wall holds instead -- the stables' band of panes.
  A window reaching the next story's floor row, or past the top, a story
  the wall does not have, a window wider than its bay or offset onto a pier,
  and a pier or a window with no `pitch`, are each an `NBTError`.
- **An arch springs from its jambs.** `profile` is a vault's, in half
  blocks: how far the head falls below the crown at each column outward,
  crown first, `(width + 1) // 2` entries -- `arch_profile` round,
  `pointed_arch_profile` pointed, a lancet. Each column is clear to its
  fall; where half a block is left, the block over it is a top `slab`, and
  where the head falls a whole block or more to the column outside -- the
  jamb, past the last -- the block over it is a top `springer` stair facing
  that way, its full side carrying the head down onto the wall, as the
  castle example's windows and entrance spring theirs. With no `profile`
  the head is the castle's: flat, a springer in each of its two end cells.
  A `keystone` sits over an odd arch's crown in layer 0. A `fill` -- glazing
  -- goes in layer 0 only, with air behind it in a thicker wall.
- **Materials are named**, never a palette's (pass `b.palette[role]` for
  one), and `field` is required. A `Material` is its block -- or, for an
  arch's `springer`, its stairs, and its slab where `slab` is left out --
  and a `Mix` goes wherever a block goes in as it is, a facade's every
  role, a fill, a lintel, a keystone and a shell's own courses, picked at
  each world cell as the plan is made. A Mix for a springer or a slab is a
  `TypeError`, and a block of the wrong shape an `NBTError`. A glass
  `Material` in a `fill` is its block, full glass, as everywhere a block
  goes in as it is; the palette's `glass` is a pane only where
  `window(block=)` reads it, so a window of panes is a glass Material's
  `.form("pane")`, as `examples/craft_session.py` glazes its house.
- **What a shell writes, in order:** each story's inside cleared, bottom
  up (`hollow`, on by default, a cut through whatever is there); `ground`
  over the footprint at `floor_y`, a cut too; the four walls of
  `Wall.ring`, each laid by `facade` or by `faces[name]`, every cell in
  `(x, y, z)` order, `openings` cut in them; `floor` on every upper story
  inside the walls; a `deck` over the footprint at `top + 1`; a `parapet`,
  a solid course `parapet_height` high from `top + 2`, on exactly the cells
  of `Wall.perimeter`; or a `roof`, a `Roof` over the walls' outline from
  `top + 1` with its required `attic`. Its `WallResult` gives `walls`,
  `floors`, `top`, `outline` and `perimeter`, and counts `blocks`,
  `already`, `openings` and `kept_open`. An east or west facade naming a
  corner the north and south walls do not build, a parapet with no deck,
  and a deck or parapet with a roof, are each an `NBTError`.
- **What stands in the way.** A cell already holding what the plan puts
  there -- the same game state, apart from what a block update derives --
  is left and counted `already`, and an open cell of an opening with a door
  or a gate hung in it is the hole already there, whatever `overwrite`
  says: a shell laid again over its own doorway keeps the door. Nothing
  else a body walks through is: water, a bubble column, powder snow or a
  cobweb in a doorway is something standing in it. An opening may cut what
  the wall's facade builds (`cut_opening` takes it as `facade=`). Anything
  else is a `Refused` naming every cell before a block is written;
  `overwrite=True` builds through it, and clears an opening to air. A block
  planned into a cell a flight or an approach this session built claims is
  refused whatever `overwrite` says, the refusal naming which, and a
  `hollow` holding one is refused: build the shell before its flights.
- **Then the plan is audited** (`Builder._audit_plan`), and refused if it
  would leave a block the game will not keep or a door shut further than
  it was -- a torch or an iron door's button whose wall a cut takes away, a
  chest put under a floor or a fill that conducts. A fault that stood
  before the call is not the call's.
- **Then the building is used.** Doors are hung after the shell,
  `b.doorway(*wall.door(a), door=...)`, in an opening cut for them; an
  iron door needs a conducting jamb for its buttons, which a glazed bay
  has only beside a pier (`Facade.kind`), and `doorway` refuses one between
  panes. Flights are built after it, through the floor they climb to,
  which `flight` cuts as its stairwell; a shell's `floor` laid after a
  flight leaves the flight's claimed cells open (`kept_open`). A
  `battlement` over `perimeter` at the parapet's top row, `top + 1 +
  parapet_height` (`top + 2` for the default one-block parapet), takes that
  row as its course, so its corners are merlons by construction; a row
  lower, its gaps hold the parapet and it is refused. Its rooms are
  furnished last (`furnish`, below), walked from the door they were given.
  `frame_walls` and `floor` stay the first town's writers, byte for byte.

### Elevations: a wall's face as bands

A `Facade` is one rule for a whole wall: a pier every `pitch` columns, a bay
between them, the same from the plinth to the head. An `Elevation` is for a
wall that is composed: bays of 7, 4, 4, 3, 4, 4, 7, which no congruence says,
or a ground story on a rhythm of its own. It stacks `Band`s from the bottom
up, each some rows, a `Rhythm` across and what each role holds, and a plinth,
a belt course, a crown and a head course are bands like any other. It is a
rule like a Facade, and it replaces nothing: `wall`, `shell` (and `faces=`)
and `cut_opening(facade=)` take either, and a shell may mix them.

```python
from heaton_voxel.craft import Band, Doors, Elevation, Glazing, Layers, Rhythm

bay = Rhythm("pier", ("bay", 4))
long = (Rhythm("post", ("glass", 7)) + bay * 2 + "pier").mirrored(center=("bay", 3))

face = Elevation(
    Band(2, ground, {"pier": Layers(SANDSTONE, SANDSTONE),  # the plinth
                     "open": Layers(AIR, GLASS)}),          # glass set back
    Band(4, ground, {"pier": Layers(BRICK, BRICK), "open": Layers(AIR, GLASS)}),
    Band(None, long, {"post": WOOL, "pier": BRICK,         # the rest of the wall
                      "bay": Glazing(GRAY, LIGHT, GRAY, spandrel=BRICK),
                      "glass": Glazing(GRAY, GRAY, LIGHT, LIGHT)}),
    Band(3, long, {...}))                                  # the crown

b.wall(Wall((x0, z), (x1, z), floor_y=y, top=y + 22, outward="south",
            stories=(y + 6, y + 10, y + 14, y + 18)), face)
```

| | |
| --- | --- |
| `Rhythm(*runs)` | The columns across a wall, each a named role, from its low end: west to east or north to south, whichever way it faces. A run is a role name, `(role, width)` or another Rhythm. Rhythms add (`+`, a run or a Rhythm) and repeat (`*`), and `.reversed()` and `.mirrored(center=None)` make new ones. `.columns`, `.runs`, `.roles`, `.symmetric`, `len()`. A value: equal rhythms are one rhythm |
| `Glazing(*rows, spandrel=None, start=None)` | What a bay holds up each story, repeating, counted from the floor of the story each row stands in, so it follows the wall's stories whatever their heights. With a `spandrel` the floor row is that block and `rows` begin over it, a punched window; without one they run through the floor row too, a curtain wall. `start` is how far over the floor the first row stands, where that is neither: `start=1` reads a ground story's rows from the bottom, since it has no floor row in the wall |
| `Layers(*values)` | What a role holds at each depth, front to back from the wall's line inward: `Layers(AIR, GLASS)` is glass set back behind its piers, `Layers(BRICK, BRICK)` a pier two deep, `Layers(BLACK, BRICK)` a fin before a jamb. Each is a block, a `Material`, a `Mix`, a `Glazing` or `Doors`, and it says exactly those layers whatever the wall's thickness |
| `Doors(door)` | A door hung in every column of a role's run, in the two rows over its story's floor, so the bands that hold it have to be those rows. It opens inward, as `Wall.door` hangs one, and each hinges toward the nearer end of its run, so a pair meets in the middle. An iron door is refused: it needs `Builder.doorway`'s buttons |
| `Band(rows, rhythm, roles)` | `rows` high, or `None` to take what the other bands leave; `roles` maps every role of the rhythm to a block, a `Material`, a `Mix`, picked at each world cell, a `Glazing`, `Doors` or `Layers`. A column with nothing in it holds air, by name, and a writer keeps it clear |
| `Elevation(*bands)` | The bands, bottom up. `.plan(wall)`, every cell with its state, pure; `.verify(save, wall)`, as `Facade.verify`; `.role(wall, along, y)`, the role of a column in the band at a row, as `Facade.kind`; `.describe()`, the rule in words; `Elevation.read(save, wall)`, below |

Every band is as wide as the wall: a rhythm covers its whole wall, corners
included, and one the wrong width is refused rather than cut, since a bay that
did not fit is a decision. `Wall.ring` gives a rectangle's corners to its north
and south walls, so those rhythms carry the corner columns and the east and
west ones run between them. Between them the bands are as high as the wall, and
one may stretch to make up the height, so a rule builds five stories or fifty
with its base and crown keeping theirs; where the others take the whole wall it
comes to no rows, and the crown stands on the base. A role without `Layers`
goes through every layer of a thick wall, as a Facade's courses do.

`Layers` may reach past a wall's own thickness, which is how a wall one block
thick stands on a ground story two deep. So in a `shell` it reaches into the
footprint: it is laid after the hollow, which clears what stood there, a
story's `floor` runs through it, and two walls that reach one cell have to
agree on it or the shell is refused, naming the cell. A corner two square is
that agreement: the long walls own the corners, two columns of `Layers`, and
each short wall's end column meets them.

Refused as a rule is made: no columns, a role that is no name, a width under
one; a band's rows under one, a role the rhythm has and `roles` does not or
the other way about, either of which is one of them spelled wrong; no bands,
and two that stretch; no layers; what is no door, an iron door, and a door
naming its own `facing`, `half` or `hinge`, which are the wall's (each an
`NBTError`); a material that is no block or is `None` or `FROM_PALETTE`, and
a `Mix` or a `Material` for a door (a `TypeError`): every material is named
at the call. Refused when it is laid over a wall, naming both numbers: a band
the wrong width, and bands that do not make the wall's height or pass it; and
a door not in the two rows over its floor.

A writer holds an elevation to everything it holds a facade to: what stands
in a cell, a flight's claim, the audit of what the game would not keep, so
`deck="minecraft:gravel"` over a hollow top story is refused, because gravel
falls, and so are doors over no floor. A door somebody has opened is still the
door that was hung, to `verify` and to a writer laying the rule again: its
`open` and `powered` say how it stands in play. `examples/elevations.py` holds
a sample read off a real building and replayed against it, `OFFICE_NORTH`,
`OFFICE_SOUTH`, `OFFICE_EAST_WEST` and `OFFICE_FACES` for `shell(faces=)`,
with `office_stories(n)` for any number of stories over its ground one.

#### Reading a wall that stands

`Elevation.read` is how a building is studied: it proposes the rule of a wall
and replays it over that wall, and the replay is the reading, as it is for
`SpiralStair.read` and `Vault.read`. A rule that does not reproduce every cell
is a `Refused` with each cell in its faults. It does not refuse a wall with no
rule in it: random blocks come back as many roles and many bands, which says
so, and a block out of place comes back as more rule rather than being hidden.

```python
from heaton_voxel.craft import Elevation, Wall, floors_behind

ring = dict(floor_y=-60, top=-38)
stories = floors_behind(save, Wall.ring(x0, z0, x1, z1, **ring)["north"])
south = Wall.ring(x0, z0, x1, z1, stories=stories, **ring)["south"]

rule = Elevation.read(save, south)         # refused unless it reproduces the wall
print(rule.describe())                     # the bands, the rhythm, the roles
b.wall(Wall((x, z0), (x + 40, z0), outward="south", floor_y=y, top=y + 42,
            stories=range(y + 6, y + 39, 4)), rule)   # stands again, taller
```

`floors_behind(save, wall, *, share=0.6, dimension=None)` reads the stories
from what stands behind the wall: a floor is a row of full cubes in the layer
behind the wall's own, in `share` of its columns. A pane is no cube and neither
is a desk, and a run of such rows is a wall or a mass, not a floor. A wall read
with no stories is one tall story, every row its own height, and comes back as
many short bands.

`Elevation.read(save, wall, *, depth=2, stretch=True, dimension=None)` puts
rows in one band while no two at one height in their stories disagree, and of
the ways to cut the wall into such bands takes the one that says it in the
fewest words, which is what stands a belt course apart from the story over it.
Columns holding the same are a role, named from the first thing in it:
`glass`, `doors`, `courses` for rows of anything else, the block's id, or
`open`. A role that changes up a story is a `Glazing`, read from its lowest
row, over a `spandrel` where its floor row holds what no other row does; a run
of doors hung as `Doors` would hang them is `Doors`, and any others, an iron
door or one hinged another way, keep the states that stand. What a block update
derives is left out, so a joined pane reads as its id, and a door somebody
opened reads as the door.

`depth` is how many layers it may read. It goes past the wall's thickness only
in a row with a column open through it, and only as far as those columns meet
something, because behind a pier or a window is the building, and a rule that
took it would have the furniture in it. Every column of such a row is read
that deep, as `Layers`. `stretch` gives the longest band that repeats
`rows=None`, so what comes back stands on a wall of another height; the same
wall replays the same either way. A cell with nothing to read is an `NBTError`.

```
python -m heaton_voxel elevation <world> --box X0 Z0 X1 Z1 --floor Y --top Y
                                 [--stories Y ...] [--depth N]
```

reads a building's four walls and says each, its stories read from the floors
behind the north wall unless they are given. It reads only, exits 2 for a
refusal, and says so when the world is open: a file the game is writing can
read as torn.

### Towers: tiers, a core and a crown

An `Elevation` says one wall. A tower that steps back has several sets of
walls, one over another, a shaft through all of them and something on top, and
`Builder.tower` lays the whole of that as one call:

```python
from heaton_voxel.craft import (Builder, Core, Crown, Massing, SwitchbackStair,
                                Tier)

stair = SwitchbackStair((x - 2, z - 2), (x + 1, z + 1), axis="z",
                        start="southwest", start_y=y + 1, landing=1)
floors = [y + 8 + 4 * i for i in range(12)]

tower = Massing(
    Tier(x - 10, z - 18, x + 10, z + 18, faces=LOWER, floor_y=y,
         top=floors[7] + 2, stories=floors[:8], floor=PLANKS, terrace=WOOL),
    Tier(x - 8, z - 16, x + 8, z + 16, faces=UPPER, floor_y=floors[7],
         top=floors[-1] + 5, stories=floors[8:], floor=PLANKS))

result = b.tower(
    tower,
    core=Core(x - 3, z - 3, x + 2, z + 2, wall=WOOL, stair=stair, lamp=LAMP),
    crown=Crown(((2, 2), (2, 3), (2, 4), (2, 5)), skin=GLASS, corner=WOOL,
                liner=WOOL, fin=WOOL, reach=-1, inner=4, rise=3, pitch=2,
                lamp=LAMP, lamps=4, cornice=WOOL, brackets=2, deck=WOOL),
    doorstep=(x, y, z + 20))

for level, cells in result.floors.items():
    b.light_cells(cells, light=LAMP, light_level=NO_SPAWNS, clear_above=False)
```

`examples/setback_tower.py` is a whole guarded session, and the part of it that
builds is a third the length of `examples/office_tower.py`'s, which lays the
same things by hand.

| name | what it is |
| --- | --- |
| `Tier(x0, z0, x1, z1, rule=None, *, floor_y, top, stories=(), faces=None, floor=None, terrace=None, ground=None, deck=None, thickness=1, openings=())` | One rectangle: walls from over `floor_y` to `top`, by `rule` and `faces` as `shell` takes them, `stories` floored in `floor`. `terrace` is what the part of its last floor left open by the tier over it is laid in. `.walls()`, `.rule_of(side)`, `.rows`, `.width`, `.depth`, `.rectangle`, `.inside`, `.holds(other)`, `.floor_cells(level, voids=())` |
| `Massing(*tiers)` | The tiers, bottom up. `.top`, `.stories`, `.verify(save)`, `.describe()`, `Massing.read(save, corner1, corner2)`; `.unread` is `{index: why}` for tiers read and given no rule |
| `Core(x0, z0, x1, z1, *, wall, stair=None, ladder=None, doors=None, top=None, height=3, lamp=None)` | The wall round what climbs, the rectangle no floor crosses. `wall` a block or a face rule. `doors` is `{floor: (face, at, width)}`; given a `SwitchbackStair` and no doors, it opens the end of the shaft each flight arrives at, and refuses a floor the stair does not land on |
| `Crown(rings, *, skin, corner=None, liner=None, fin=None, reach=None, inner=None, rise=1, pitch=1, lamp=None, lamps=0, cornice=None, brackets=0, deck=None)` | `rings` is `(rows, inset)` pairs, bottom up, an inset the columns in from the rectangle's edge. Fins run the diagonals from inset `reach`, negative outside the rectangle, to `inner`, `rise` rows high at the foot and `pitch` rows a column higher going in, the last `lamps` steps topped with `lamp`. `.plan(x0, z0, x1, z1, y)`, pure; `.verify(save, x0, z0, x1, z1, y)`; `.height` |
| `Ladder(x, z, bottom, top, *, on)` | A ladder named by the wall it hangs on, `on` a compass word; the state's `facing` follows. `.cells()`, `.wall`, `.verify(save)` |

`b.tower(massing, *, core=None, crown=None, doorstep=None, overwrite=False)`
plans every cell before writing the first, so a tower that cannot stand writes
nothing: a taken cell anywhere is named and stops the call unless `overwrite`,
and the whole plan is audited as a shell is. A tier must stand on one of the
stories under it, or the row over its walls, and within that tier's rectangle;
the core must fit inside every tier it rises through. With a `doorstep`, the
block you stand on outside, the plan is walked without a jump to every cell of
every floor and back before it is written, and a floor it cannot reach whole is
refused by level. It returns a `TowerResult`: `.floors` and `.terraces`, each
`{level: cells}`, core left out, for lighting and furnishing; `.walls`, `.walk`,
`.blocks`, `.already`.

`b.crown(rule, x0, z0, x1, z1, y, *, overwrite=False)` and
`b.ladder(rule, *, overwrite=False)` lay one on its own, each returning the
cells it wrote. A ladder with nothing to hang on is refused by the survival
audit, so build its wall first. `shell` takes `voids=`, columns no floor
crosses: a core or an atrium, counted in `kept_open`.

#### Reading a tower that stands

```python
tower = Massing.read(save, (x0, y0, z0), (x1, y1, z1))
print(tower.describe())
```

    rows 8..79: 21 by 37 at (-416, 229), 15 upper floors
    rows 80..109: 17 by 33 at (-414, 231), set back 2, 6 upper floors
    rows 110..122: 13 by 13 at (-412, 241), set back 2, 2, 10, 10

A row's outline is the rectangle whose edges hold at least `OUTLINE_SHARE`, 75%,
of the row's solid blocks, trimmed from its bounding box, so an arcade's piers,
a sign on a wall and a crown's fins fall outside it and water counts for
nothing; rows sharing an outline are a tier. Each tier's stories come from
`floors_behind` and each of its walls from `Elevation.read`, which replays what
it reads. A tier too small to have walls, or one whose wall does not reproduce,
keeps no rule and says why in `unread`, and a wall that cannot be read at all
raises. On the Palace Hotel in Greenfield it finds twelve from nothing but a
box and every wall it reads replays, 12,226 cells, none wrong.
`python -m heaton_voxel massing <world> --box X1 Y1 Z1 X2 Y2 Z2 [--walls]` prints
it, and `--walls` each tier's rules too.

### Towers worked out: variety from a style

A tower above is drawn: every rectangle and row is somebody's choice. A street
wants many and no two alike, so `design_tower` works one out from a lot, a
height and a style:

```python
from heaton_voxel.craft import design_tower, Material, Scheme, TowerStyle

style = TowerStyle(
    "brick",
    (Scheme("minecraft:bricks", "minecraft:gray_stained_glass_pane",
            trim="minecraft:smooth_stone",
            roof=Material("minecraft:deepslate_tiles")),
     Scheme("minecraft:mud_bricks", "minecraft:brown_stained_glass_pane",
            trim="minecraft:smooth_sandstone",
            roof=Material("minecraft:dark_prismarine"))),
    window="punched", bay=2, pitch=5, tiers=(1, 2),
    roofs=("flat", "hip"), shapes=("box", "ell", "you", "tee"))

design = design_tower((100, 200, 132, 232), 64, 20, style, seed=7)
print(design.describe())
b.grade(design.plaza, 64, surface="minecraft:smooth_stone", retain=None)
result = design.lay(b)            # walked to every floor before it is written
```

`design_tower(lot, y, stories, style, *, seed=0, front="south", shape=None,
roof=None, tiers=None, scheme=None)` returns a `TowerDesign`: its `massing`,
`cores` and `crown`, the `doorstep` and the `plaza` rectangle to level round it,
and what it chose. `lay(b)` is one `Builder.tower` call and the lamps every floor
and terrace needs so that nothing spawns on one, `verify(save)` holds what stands
to its rules, and `describe()` says it in words. What a call leaves out is
rolled with `noise.cell_hash`, keyed on the lot's corner and `seed`, so one lot
and seed is one building in any session and a tower laid before it changes
nothing. A plan a lot cannot hold is not rolled, a roof with no room falls back
on a flat one, and a lot too small for a core, or for a plan named at the call,
is refused.

A `Scheme` is one tower's colors, each named by what it does: `frame`, `glass`,
and `trim`, `tint`, `spandrel`, `floor`, `ground`, `stair`, `landing`, `door`,
`lamp` and, for a hip, `roof`, a `Material` in stairs and slabs. A `TowerStyle` is
a family: its schemes; its `window`, from `WINDOWS` (`punched`, `curtain`,
`ribbon`, `piers` and `grid`); `bay`, the columns a bay would like; `pitch`, four
or five rows floor to floor; `lobby`, one or two stories; and what a design rolls,
`tiers`, `roofs` from `ROOFS` (`flat`, `crown`, `stepped`, `hip`) and `shapes`
from `SHAPES` (`box`, `ell`, `tee`, `you`, `cross`, `twin`). `step` is the columns
a tier steps back. Styles are taste, so none lives in the package:
`examples/tower_styles.py` has six, and `examples/skyline.py` lays a row of them.

**`Rhythm.spaced(width, *, pier="pier", bay="bay", target=3, closed=True,
pier_width=1, ends=None, center=None)`** works out a rhythm for a width: bays as
near `target` as the width lets, within a column of one another, the wider toward
the middle, the same from both ends. `closed` ends on piers, for a wall that owns
its corners (`wall.ends`); `ends` is a pavilion at each end; `center` a role of
its own for the middle bay. An `Elevation` still refuses a rhythm the wrong width:
this decides the bays, and nothing is cut.

**`Fitted(make)`** is a rule made for each wall it is laid over: `make(wall)`
returns the `Elevation` or `Facade` for that wall, so one style stands on any
footprint.

```python
def face(wall):
    across = Rhythm.spaced(len(wall.along), closed=wall.ends)
    return Elevation(Band(None, across, {"pier": BRICK, "bay": GLASS}))

Tier(0, 0, 30, 20, Fitted(face), floor_y=64, top=100)
```

**`rule.recolored({block: block})`**, on an `Elevation`, a `Facade`, a `Fitted`, a
`Crown`, a `Core` or a `Massing`, is the same rule in other blocks, the pattern
untouched: a key is a block by id, every state of it, and a value a block, a
`Material` or a `Mix`. A state keeps the properties the new block takes, and a
`Mix` keeps its weights, seed and clump. A key the rule does not hold is refused,
as a role spelled wrong is, but for a `Fitted`, which cannot say until it has a
wall. So a building read off a save can be repainted:
`Massing.read(...).recolored({...})`.

**`Footprint(*rectangles, less=())`** is a plan that is no rectangle: rectangles
joined, others taken out, a hole being a courtyard. `Footprint((0, 0, 30, 20),
less=[(16, 0, 30, 9)])` is an L. `walls(floor_y=, top=, stories=)` gives the runs
of its outline, named by the way they face, `"south"`, then `"south-2"`, the
north and south walls owning the corners, inside ones too; `columns`, `inner` and
`rim` are its parts, `bounds` the rectangle round it, and `eroded(by)` it a step
further in, an L an L still. Walls here are a block thick, and a wing under three
across or an inside that comes apart is refused. `shell(..., footprint=)` builds
on one, and `Tier.over(footprint, rule, ...)` makes a tier of one; `faces` names
walls by the way they face, or one by its whole name.

**A massing need not be one stack.** Each tier stands on the nearest tier under
it that holds it and has a floor there, so tiers listed side by side over one
tier are two towers on one base. They may not overlap, `tower(core=)` takes a
sequence of `Core`s, each rising through the tiers it fits, and `crown=` stands on
every tier with none over it. A `Tier` also takes `parapet=` and `parapet_height=`
round a `deck`, and `roof=` and `attic=` in its place, as `shell` does.

### Street networks: crossings first, half blocks between

`road` lays one road and knows of no other. A `StreetNetwork` is every street of
a place, drawn once and laid as one plan:

```python
from heaton_voxel.craft import NO_SPAWNS, Street, StreetNetwork, StreetSection

section = StreetSection(7, 2, surface="minecraft:polished_andesite",
                        pavement="minecraft:smooth_stone")
network = StreetNetwork.grid(range(0, 300, 60), range(0, 300, 60), section,
                             name="downtown")
result = b.streets(network)
b.street_lamps(result, post=("minecraft:stone_brick_wall",) * 2,
               lamp="minecraft:lantern", light_level=NO_SPAWNS)
```

`StreetSection(road=7, walk=2, *, surface=, pavement=, raised=True)` is what is
across a street: an odd roadway, a pavement either side and half a block above it
unless `raised=False`, each a block or a `Material` that comes as a slab.

`StreetNetwork(section, *streets, pins=None, name="street")` takes `Street(*points,
section=None)` objects or plain sequences of `(x, z)` points, each straight on from
the last along x or z, since a row is level across a street; a bend is how one goes
round a lake, and a diagonal is refused. It finds its own nodes: ends, bends, and
wherever two streets meet or cross. `pins` holds a node to a height, where it meets
something already there. `StreetNetwork.grid(xs, zs, section)` draws the common case.
Streets that run along one another, crossings too close for their squares and two
streets in one column are refused as it is made. No world in either.

`b.streets(network, *, run=2, clearance=4, retain=, subsoil=, over_water=None,
allow_dam=False, seal=None, through=(), rule=None, overwrite=False)` settles the
crossings first, each a whole block as near its ground as the streets joining them
can climb, then each street between them. A row is level across its width and at
most half a block from the last, one row in `run` at most, a slab on the row it
rises to. Between its ends a profile is the cheapest there is, a half block of earth
under a row worth two changes of level. So a crossing has one height, and a walker
never jumps.

Nothing is written until all of it has passed: every keep-out, ground under every
column (`NoSurface`), pins the streets can join, water unless `over_water` says, a
fill deeper than `rule.shallow_fill` unless `allow_dam=True`, what somebody built
unless `overwrite=True` (a street may cross a path its session laid), water the cuts
let out unless `seal=` walls it off, what the game will not keep, and a walk over
every column both ways with no jump, on the street's own columns and never the ground
beside them. It returns a `StreetsResult`: `heights`, `{node: y}`; `streets`, a
`LaidStreet` each, with its `name`, `points`, `rows` and `tops`; `tops`, `{(x, z): half
blocks}`; `top(x, z)` and `level(x, z)`; `walk`; and `cut`, `filled`, `slabs`, `sealed`.

Every street and crossing is a keep-out from then on, in its own name
(`network.names`), and a path the session paved, so a later `road`, a post or a
planting keeps out of it. `b.street_lamps(result, *, post, lamp, light_level,
over_water=None)` is `line_lamps` over each street's own rows, its pavement open to it, then over each
crossing from its corners, every roadway closed by `line_lamps(avoid=)`, columns a
keep-out is too coarse for. A post on a bottom slab stands in the slab's cell, on
the whole block under it.

Laid over CastleWorld's terraces (2026-11-01), the same five by five grid that
disagreed with itself under `road` at 6 of 25 crossings, stepped up to five blocks at
their edges and could be walked over 71% of without a jump, was 25,025 columns in
twelve seconds, one height at every crossing and every column walked to and back.
That was with `over_water="fill", allow_dam=True`: a straight grid over a lake and a
ravine is refused for both until it is told, or bent round them. There are no piers:
a street over a deep ravine is an embankment.

### Cities: planned from the land, laid from the streets out

Fifty towers on a grid is a pattern, and it falls apart on real ground. A city
here is worked out from what the land allows, drawn, and only then laid:

```python
from heaton_voxel.craft import plan_city, SiteMap, Worksite

site = SiteMap.read(save, (x, z), 420)       # reads; no world in it after
plan = plan_city(site, 50, ZONES, seed=7)    # no world at all
print(plan.describe())
plan.picture("plan.png")                     # look before anything is written

work = Worksite.open(world, *corners, name="city")
b = work.builder(seed=7)
result = b.city(plan)
b.finish()
```

`examples/city.py <world> --towers 50` is all of that, a dry run unless `--write`.

**`SiteMap.read(save, center, radius, *, step=4, dimension=None, keep_out=())`**
samples a square: `heights`, `wet`, `built` and `absent`, what somebody built
being what no earthwork writer takes (`_takes`) and every worksite the save
records. `open(rectangle)`, `relief(rectangle)`, `height(x, z)` and
`samples(rectangle)` ask it things.

**`plan_city(site, towers, styles, *, seed=0, block=76, stories=(6, 40),
parks=0.15, relief=20, section=None, avenue=None, name="city", band=16, depth=6,
tries=12)`** returns a `CityPlan`. Street lines start as a loose grid of uneven
blocks, and each stretch between two crossings is judged alone: it is there if
its whole width is dry and unbuilt and it can follow the ground within `depth`,
so a ravine is not crossed. A block is kept if two of its sides are, so one on a
lakefront has streets on three sides, and the city grows from the best block near
the site's center, the cheapest next that shares a street with it, so streets step
round water by themselves. What a block is, the land says: dry and gentler than
`relief`, one to four lots; wet or steeper, a park; with something built in it,
`left` exactly as it is. Enough more go green to make `parks`, and one near the
middle. Every lot fronts a street, its entrance on the side nearest downtown.
Stories fall away from downtown by rank, dice over that, and a slender lot is kept
short. `styles` is a sequence or `{"core": ..., "mid": ..., "edge": ...}`. Land
that holds too few is a `Refused` saying how many it does.

A `CityPlan` has `network`, `blocks` (`CityBlock`: `rectangle`, `kind` of `towers`,
`park` or `left`, `lots`), `lots` and `spare` (`CityLot`: `rectangle`, `building`,
`front`, `stories`, `style`, `seed`, `zone`, `door`), `parks`, `downtown` and
`bounds`, with `describe()`, `picture(path, scale=2)`, and `avoiding(cells)`, the
plan drawn again with those cells marked built.

**`b.city(plan, *, post=, lamp=, plaza=, run=2, park_biome="minecraft:plains",
over_water=None, allow_dam=True, replans=8)`** lays it from the streets out, each
stage planned whole, in `stage()`s of its own. Streets come first, since they
settle every height; one that meets something the samples missed is refused before
anything is written, and the plan is drawn again round it. Each lot is leveled to
the pavement at its door, flush or half a block under it, and `design_tower` works
its building out at that height. A lot that turns out to hold something is refused
like any other and its tower goes on a `spare`. `blend` then eases the yards and a
`band` of the land outside the streets. Parks are sown: grass, a lit path each way
through the middle, trees, flowers, and `park_biome` so grass is green in a desert.
Last the street lamps, and a walk from the streets to every doorstep with no jump.
It returns a `CityResult`: `plan`, `streets`, `designs`, `towers`, `skipped`, `blend`,
`lamps`, `replanned` and `unreached`. Since stages run in turn, lay one in a
`Worksite` and save what came through: a save open in the game cannot be written.

**`b.blend(columns, *, run=1, clearance=4)`** eases ground into what stands round
it: every height round the set, and every column in it that cannot move, is to be
met at no more than a block in `run` columns, a king's step counting one, and a
column already within that stays exactly where it is. It moves as `smooth_terrain`
moves, its surface kept. Water, ice and what somebody built do not move. A column
something built is in the way of, or whose cut would let hidden water out or leave
sand over a cave, stays put and is counted `held`; heights no slope can join meet
halfway and are counted `steep`. It returns a `BlendResult`.

### Battlements

```python
from heaton_voxel.craft import Builder, merlon_offsets
from heaton_voxel.craft.geometry import ring

b = Builder(save)
tops = b.battlement([(x0, z0), (x1, z0), (x1, z1), (x0, z1), (x0, z0)], 72,
                    block="minecraft:stone_bricks")      # a closed path
for x, y, z in tops[::4]:
    b.set(x, y + 1, z, "minecraft:torch")                # every fourth merlon
b.battlement(y=80, ring=ring(6, center=(cx, cz)),        # a round tower's rim
             block="minecraft:stone_bricks", height=2)
merlon_offsets(8)                                        # [0, 2, 4, 6, 7]
```

`battlement` lays a parapet course of `block` at `y` and merlons on it,
`merlon` (`block` when left out) `height` blocks tall, with the gaps
between them clear. **Its teeth are counted per straight run, from that
run's own corners**, by `merlon_offsets` — the castle example's rule: every
other cell from the first, the last always, so a run of even length doubles
up beside its far corner — and a run's ends are merlons, so every corner is
one by construction. A crown counted round the whole perimeter as one list,
a merlon on every other cell, alternates cleanly only while every side is
odd; seven of JeffWorld's Roman field towers and the castle example's
gatehouse turrets wear the result, two bare corners each.

| call | does |
| --- | --- |
| `merlon_offsets(length)` | the offsets along a straight run that carry a merlon |
| `Builder.battlement(path=None, y=None, *, ring=None, block, height=1, merlon=None, overwrite=False)` | lay it along `path` or round `ring`; returns every merlon's top cell, `(x, y + height, z)`, in path order |

- `path` is `(x, z)` vertices along straight runs, closed when the last
  equals the first, and walked from its first corner; a vertex it runs
  straight through is no corner and keeps the rhythm. A diagonal pair, a
  turn back on itself, or a crossing at a cell that would be a merlon one
  way round and a gap the other is an `NBTError`.
- `ring` is a round tower's rim in order round it, `geometry.ring(r,
  center=)`: a merlon on its first cell and every other one after, as the
  castle crowns its towers, cell for cell. An odd count cannot alternate
  all the way round and is refused, and so is a ring out of order (two
  cells in a row that do not touch) or with a cell twice. Every
  `geometry.ring` above radius 0 is even.
- `block` and `merlon` are named at the call — no palette's, so pass
  `b.palette[role]` for one — and either may be a `Mix`, each cell its pick
  there, or a `Material`, its block. `block` left out or `None` is a
  `TypeError`; `merlon` left out or `None` is `block`.
- A course already standing is taken as it is: a cell already holding
  what the battlement writes there — the same game state, apart from what
  a block update derives: a fence's, a pane's or a wall's sides, a wall's
  post, a stair's corner shape — is no obstacle and is left as it stands,
  so a wall's solid top course, laid first -- a shell's `parapet`, over
  the cells of its `perimeter` -- is crenellated by laying the battlement
  over it, and the same call made again after `finish()` has
  joined a fence or wall crown up finds it whole and writes nothing.
  Anything else in a course or merlon cell, and anything but air in a gap
  as high as the merlons stand, is a `Refused` naming every cell, before a
  block is written; `overwrite=True` cuts through and empties the gaps.
- Then the plan is audited (`Builder._audit_plan`), as a wall's is: a gap
  cut from behind a torch or a button, or a course that leaves a block the
  game will not keep, is a `Refused` naming each fault, before a block is
  written. A fault that stood before the call is not the battlement's.
  One `y` per call: a battlement that follows the ground is a section of
  a course and a `Merlon`, swept along it by `extrude` (below), whose
  teeth are counted by the same rule.

### Extrusions: a cross-section swept along a line

```python
from heaton_voxel.craft import CrossSection, Merlon

wall = CrossSection.rows(["m.l",             # drawn facing south, top row
                          "sss",             # first: across -1..1, up 1..-1
                          "sss"],
                         {"s": "minecraft:stone_bricks",
                          "m": Merlon("minecraft:stone_bricks"),
                          "l": "minecraft:stone_brick_slab[type=bottom]"},
                         left=-1, top=1, walk=[(0, 0)],
                         footing="minecraft:cobblestone", bury=2)
w = b.extrude([(x0, z0), (x1, z0), (x1, z1), (x0, z1), (x0, z0)], wall)
w.line, w.profile, w.walk, w.merlons         # the centerline and its heights
w.reach.jumped                               # rises the walk needs a jump for
deck = CrossSection({(-1, 0): SLAB, (0, 0): SLAB, (1, 0): SLAB})
pier = CrossSection({(-1, 0): SLAB, (0, 0): SLAB, (1, 0): SLAB,
                     (0, -1): BRICK}, footing=BRICK)
b.extrude([(ax, az), (ax, bz)], deck, follow=73, pier=pier, pitch=6,
          pier_width=2, phase=0, through=("path",))   # an aqueduct over a path
hedge = CrossSection({(r.width // 2 + 1, 1): HEDGE,
                      (r.width // 2 + 1, 2): HEDGE})
b.extrude(r.points, hedge,                   # beside a straight road, on
          follow=[r.top(*cell) for cell in r.line])   # the surface it laid

from sections import AQUEDUCT, AQUEDUCT_PIERS    # examples/sections.py
b.set(ax, 73, az - 1, "minecraft:smooth_sandstone")  # one end built first
b.extrude([(ax, az), (ax, bz)], AQUEDUCT, follow=73, through=("path",),
          spill=[(ax, y, bz + 1) for y in range(g + 1, 74)],  # the other
          **AQUEDUCT_PIERS)                  # falls into a basin at y=g
```

`extrude(points, section, *, follow="terrain", window=ROAD_WINDOW,
max_step=1, anchors=None, pier=None, pitch=None, phase=None, pier_width=1,
over_water=None, through=(), overwrite=False, allow_dam=False, seal=None,
spill=())` sweeps a `CrossSection`
along `(x, z)` waypoints -- closed when the last is the first -- at a height
that follows the ground, one level, or a profile given, and returns an
`ExtrudeResult`. `examples/sections.py` holds four sections read off real
builds -- `ROMAN_WALL`, the JeffWorld Roman city's curtain wall; `LAB_WALL`
and its `LAB_PILASTER`, the secret lab's; `AQUEDUCT` and its
`AQUEDUCT_PIER`, the JeffWorld aqueduct's arcade; and `HEDGE`, the park's
parterre hedge -- each held to the script it is named for by
`tests/test_extrude.py`, cell for cell, and laid over the modern fixture's
own ground.

| call | does |
| --- | --- |
| `CrossSection(cells, *, walk=(), footing=None, bury=0)` | `{(across, up): piece}`: what stands across one cell of the run. Read-only `cells` (a `Material` kept as its block, any air as the cut), `across` (a range), `reach` (the largest `\|across\|`), `walk`, `footing`, `bury`, `water` (whether a piece may write water: a channel) and `lowest(across)` |
| `CrossSection.rows(lines, key, *, left, top, ...)` | the same drawn as text, top row first: character `j` of row `r` is `(left + j, top - r)`; `"."` and `" "` are nothing and never keys, and a character `key` does not name is an `NBTError` naming it |
| `Merlon(block)` | a piece that is a tooth where its line's battlement rhythm puts one and a gap elsewhere; `None` is a `TypeError`, air or a block holding water or lava an `NBTError` |
| `ExtrudeResult` | `line` (the centerline, a closed path's repeated cell dropped), `profile` (the height at each of its cells), `walk` and `merlons` (the walk cells and every tooth's top cell, in write order), `reach` (the walk check's `ReachMap`, or None), `blocks` (written), `already` (named and already as wanted) and `sealed` (the seals written after it, `seal=`) |

**A section is drawn once, facing south.** `up` counts from the profile's
height, the block stood on, so `up=0` is the ground course and a negative
`up` a foundation row; `across` is signed, + to the right of travel -- the
side `line_lamps` calls right, the inside of a circuit walked clockwise.
States are drawn as they stand on a run heading south, and each run turns
them by its own heading through a `Frame`, the properties a state leaves at
their defaults included, as `framed()` turns them: a log drawn `axis=z`
lies along every run and one drawn `axis=x` across it -- logs laid by axis
-- and a bare stair, facing north back up a south run, faces back up every
run. A piece is a block (an id, a `BlockState`, a `Material`'s block), a
`Mix` picked at the world cell and the pick then turned, a `Merlon`, or any
air: a **cut**, emptied where something stands and left where the cell is
already empty. A cell left out is left as it stands; `None` is an
`NBTError`, and a palette's `FROM_PALETTE` or a list of `(block, weight)`
pairs a `TypeError` -- a section names its materials, and has no rng to
draw a list from. A piece may hold water -- a source, a waterlogged block,
kelp -- and is then a channel (below); lava, which no water check follows,
and flowing water (`minecraft:water` at a level from 1 to 15), which the
game makes from its sources, are an `NBTError` as the section is made, a
`Mix`'s every entry asked, and so is a `Merlon` of water. `walk` is the
cells a body stands on, each a block a body
stands on, with nothing solid one or two over it; the third cell of head
room is the walk check's to judge. `footing` (never air, a fluid or a
`Merlon`) fills each across column whose lowest piece is a block, from
`bury` blocks into the ground up to that piece -- `bury` with no footing is
an `NBTError`.

**The band.** Each across value is its own line: the waypoints moved along
their normals, mitered exactly at a right-angle corner, and rasterized by
`geometry.line`, road's rule, which rounds differences only -- so a line
along a road's waypoints moved across is the road's line moved across. A
run may bend while it keeps its heading, every segment of it nearest one
compass word, since then each line is the centerline shifted across; it
turns only at a right angle between runs straight along x and z, and a
closed path is a rectangle. Before anything is read, an `NBTError` names
each of: a turn beside a diagonal, which folds the band over itself or
leaves holes in it (the design found 99.7% of such turns did one or the
other) -- put a straight run either side of the turn; a turn back; a run
too short for the turn inside it, which folds a line back on itself, with
the length it needs; a cell two lines reach, the band come back over
itself (a hairpin), judged on the cells laid -- a pier wider than its
section reaches past it only at a pier; and a closed path that is not a
rectangle. Every cell
takes the height of the centerline cell on its own run with the same
coordinate along it, clamped to the run's ends, so a corner's miter takes
the corner's.

**The profile.** `follow="terrain"` takes the highest ground under the
section at each centerline cell -- each column's `ground`, the waterline
over water -- smooths it with `geometry.smoothed_profile` over `window`
cells either side, clamped to `max_step` blocks a cell (`None`: no clamp),
`anchors` (`{(x, z): y}` on the centerline) pinned and the loop wrapped
round on a closed path, and then lays every turn level over the section's
reach either side, clamping again until nothing moves: the inner line of a
corner skips centerline cells, and a profile climbing a block a cell
through a corner puts `2 * reach + 1` blocks between two cells side by
side on its inner face. Leveled, no two cells of the band side by side
differ by more than `max_step`. An anchor off the centerline is an
`NBTError` (it would pin nothing), and so is one a turn or the clamp cannot
hold -- a high anchor is a step, since the profile only lowers. `follow=y`
lays the section level; a sequence of ints is the caller's own, one per
centerline cell, refused unless it is level through every turn -- so a
road's surface along its centerline, `[r.top(*cell) for cell in
r.line]` given the road's own `points`, serves only a road with no turns.
That, and not the road's `heights`, is the surface laid there: on a road
wider than 1 the profile is laid `width // 2` cells further along, and a
hedge following `heights` would stand a block off the road's edge at every
change of level. `window`, `max_step` and `anchors` with any other
`follow` are an `NBTError`.

**Piers and spans.** `pier` is a second `CrossSection` laid instead where
`(u - phase) % pitch < pier_width`, `u` the centerline cell's coordinate
along its run (x running east or west, z north or south) and `phase` the
first cell's when left out -- `Facade`'s and `arcade`'s world-locked
congruence; `pitch` is required with a pier and `pier_width` under it.
`through` names the pads and reservations the band may cross (any other
a cell laid enters is refused, as a road's swath is), and **nothing is
founded inside one**: the main section spans it, with no footing and no
pier, and its ground is not read.

**The ground is read, never assumed.** Every column laid outside a span is
read before anything is planned. Under `"terrain"`, and wherever a footing
founds a column, the ground is needed: each column with none is gathered
into one `NoSurface` naming the first and how many, its `reason` and
`cell` the first's and its `faults` every column's own `NoSurface`, in
write order. Laid at a height given, a column with no ground at all -- a
deck over a shaft to the world's floor -- is spanned. The secret lab's
curtain wall (`tools/secret_lab/m1_groundwork.py`, `_wall_tops`) took the
plane, 66, for any column it could not read and planned a wall at 73
there; the Roman wall's `smoothed_line` (`tools/roman_city/jw_groundwork.py`)
raised at the first missing column, after its script's earlier stages had
written. Water and ice under the band are refused unless `over_water` says
what to do -- everywhere under `"terrain"`, and laid at a height given
wherever the section or its footing reaches down to the waterline, so a
deck carried over a pond leaves the pond alone: `"bridge"` stands the
section on the waterline, burying none of the water under its footing,
`"fill"` fills the column from its bed with the footing (an `NBTError` for
a section with none), and either is held to what a fill from the bed is
held to, a flow it starves refused unless `allow_dam=True`. Under
`"terrain"` without piers every
column holding a block must be founded -- by its footing, or by its lowest
block standing on the ground -- or it is refused, every one named: give
the section a footing, or carry it level over the dip with `follow=y`
and `pier=`.

**Before anything is written**, each refusing the whole call: the
arguments and the band; the keep-outs a cell laid enters; every block by
name, each state turned for every heading the band runs, a Mix's every
entry (a direction the turn cannot carry is the `Frame`'s `Refused`); the
ground, an extrusion this session laid under the terrain followed, and the
profile; the water under the band; the chunks and heights the plan
reaches; lava written over or opened onto by a cut, which no flag passes;
what the plan writes over -- air, ground cover, natural ground, an ore or
a tree it takes (`_takes`, the occupancy rule it shares with basin and
approach), and the water and ice of the columns `over_water` bridges or
fills -- and anything else refused, every cell named, unless
`overwrite=True`, as is
water `over_water` never reaches, under dry ground or under a span,
refused as water; a block in a flight's or an approach's claim, which
nothing passes; water the plan takes out that
starves a flow or cuts a body of water in two (`_split_faults`, the split
check a road's fill is held to), each unless `allow_dam=True`; water the
plan lets out -- a cut opening a pond's bank, a channel's own water at an
end left open -- every `Leak` `contained` finds outside `spill` named in
one refusal (`_contain_plan`, the one water check), unless `seal=` walls
each one's water off where it stands, as a road's and an approach's
seals do: written after the extrusion, refused in a keep-out not named in
`through`, held to the starve check and audited and walked with it, and
never laid over lava touching water, a chunk not there or the channel's
own water; the survival audit;
and, for a section with a `walk`, the round trip, `reachable` from the
first walk cell over the plan, on the band's own columns only, from the
lowest walk cell to two over the highest. On a low wall the ground inside
a circuit lies within a block of its walk, and a walk free to step down
onto it passes a rise of two by jumping round it; here every walk cell
must be reached and walked home from along the wall itself, so a rise of
two, a step down with a block three over it, and a walk one cell wide that
touches itself only at the corners where a diagonal steps across are each
refused, the walk cells cut off named. A rise of one is a jump, reported in
`reach.jumped`. Both walls in the tools step a whole block, with a slab lip
beside the walk, and the walk check walks a lip as two half steps up each
rise: it stands a body on a bottom slab at the top of its cell, as on a
stair's back.

**Water a section holds lies level, and its ends are the caller's.** A
section, or a pier, holding water -- a canal's channel, an aqueduct's
trough, both of the Roman scripts' aqueducts a block of water between two
of sandstone -- is laid level: `follow="terrain"` is an `NBTError`, and so
is a sequence that steps, naming the cell it steps at, both before
anything is read. Its water is the plan's own and asked where it goes as
a cut's pond is: an open end, a gap in a bank, a trough carried over air
with nothing under its end, each refused, every cell the water would
enter named -- no seal mends a plan's own water -- unless `spill` names
it: world cells `(x, y, z)` its water may run into, as `contained` takes
them, the water carried on down them and the first cell past them the
leak. The fall from an aqueduct's end into the basin under it is its
spill; an end built first, or ground that closes it, holds it like any
bank. JeffWorld's aqueduct holds its water at `(477, 73, 451..495)`, its
north end closed by the castellum it feeds and its south end open onto
air at z=496, south of which nothing of the arcade or its springhouse
stands (read 2026-10-22): it stands only because nothing has updated
beside it, and `contained` over its own cells names that cell in
`standing`. A channel's columns are learned at its water's surface, so the ground
writers after it read them as water, and a block written over the water
at the waterline is learned as dry ground there. `WATER_SOURCE_CONVERSION`
is taken as on, as the water check takes it.

**A battlement that follows the ground is a section.** A `Merlon`'s line is
counted per run from its own corners by `merlon_offsets` -- the corners of
each line, where it changes direction, and an open line's ends -- so on
level ground a course and a `Merlon` lay what `battlement` lays over the
same path, cell for cell, and up a slope each tooth stands on the course
where the course is.

Then the plan is written in one total order -- by the centerline cell each
column takes its height from, then its across value, then its place along
its own line, each column bottom up -- so the same call on the same save
writes the same bytes. A cell already holding what it would get, apart
from what a block update derives, is left as it stands, so the same call
made again at the profile it was laid at (`follow=w.profile`) writes
nothing. Each column's ground is learned as the column reader will find it
with the plan laid -- the highest block that stops a fall: a wall's top, a
ditch's floor, the grass over a course laid under it, never a hedge's
leaves or a torch -- except that a span is not ground, so a deck between
piers or across a keep-out leaves the ground under it as it was. Following
the terrain where the ground is the top of an extrusion this session laid
is therefore refused, every such column named: it would stand a second
extrusion on the first, and the same call made twice would build two
walls. So is a section that cuts into the ground, followed where the
ground is still the floor an extrusion's cut left: a ditch made again
would dig as deep again under its own floor, every time it was made. A
wall following the terrain across a ditch stands on its floor, which is
ground. Lay it again with `follow=w.profile`, and extrude two runs that
meet as one path, mitered at its corner. A later session reads the game's
heightmap, which lands on the wall as well, and is given the profile.
Nothing is drawn from `rng`.

### Roofs: one rule, rising from the outline

```python
from heaton_voxel.craft import Builder, Material, Roof, Wings, arch_profile, rise_of

b = Builder(save)
tile = Material("minecraft:granite")
hip = Roof(rings="square", pitch=2, overhang=1,           # a hip roof
           block=tile, slab=tile, stair=tile, hip="minecraft:granite")
b.roof(hip, (x0, z0, x1, z1), 72, attic="hollow")         # the walls' outline
b.roof(Roof(eaves=("north", "south"), block=tile, slab=tile, stair=tile,
            close_ends="minecraft:smooth_sandstone"),     # a gable, ends closed
       (x0, z0, x1, z1), 72, attic="minecraft:stone")     # and the attic filled
b.roof(Roof(rings="round", rise=rise_of(arch_profile(6, 5)), block=tile,
            slab=tile, stair=None),                       # a dome
       (cx, cz, 6), 80, attic="hollow")
print(hip.verify(save, (x0, z0, x1, z1), 72, attic="hollow"))  # what departs

ell = {(x, z) for x in range(0, 21) for z in range(0, 9)} | \
      {(x, z) for x in range(0, 9) for z in range(0, 21)}
b.roof(hip, ell, 72, attic="hollow")                      # a hip over any plan

gable = Roof(eaves=("north", "south"), block=tile, slab=tile, stair=tile,
             close_ends="minecraft:smooth_sandstone")
bay = Roof(eaves=("west", "east"), block=tile, slab=tile, stair=tile,
           close_ends="minecraft:smooth_sandstone")
cross = Wings([(gable, (40, 0, 70, 10), 72),             # a cross gable: the
               (bay, (50, -8, 60, 5), 72)])               # bay drawn to the ridge
b.roof(cross, attic="hollow")                             # each wing its outline
print(cross.verify(save, attic="hollow"))
```

A roof over a rectangle, a disc or any plan is one rule at one setting: a
rise, in **half** blocks above the floor of `base_y`, over distance from the
outline. `eaves` measures it from one edge (a shed, rising away from it) or
two opposite ones (a gable, its ridge between them; the other two edges are
verges); `rings="square"` from every edge, ring by ring (a hip, a pyramid,
a mansard, tiers) -- over a rectangle, or over a footprint, any plan, where
the ring is how many steps a king takes to walk out of it; `rings="round"`
from a disc's rim, by `geometry.ring`'s rings (a cone, a dome, a spire).
Roofs that meet -- an L or a T of gables, a cross gable, wings at different
heights -- are several of them joined, `Wings`. Of the roofs the builds
wrote by hand, the Roman town's hip, low hip, pediment and cones, JeffWorld's
baths dome and macellum cone, the castle example's spire, the suburban
house's rear gable, `gable_roof` itself and one course of a `Vault` come out
of it cell for cell — the dome but for its finial and the spire but for its
mast; `tests/test_roof.py` runs each script beside the rule. Two differ on
purpose: the Roman shed's stairs face down its slope where a roof's face up
it, and the suburban rear gable laid stone in its two verge columns. The
suburban house's main roof, a cross gable built by hand, is three wings --
main roof, rear gable, front bay -- held in shape to a grid read off
JeffWorld: 1,067 of its 1,073 columns at the rule's height (Jeff left six a
block short) and every top block the rule's but 96, the stone he laid in two
rows of the south slope, the rear verges, and the eight valley cells where
he laid inner-corner stairs and the rule lays plain blocks. One is not the
rule at all: the Roman `common.dome`, whose height follows the exact radius
rather than the ring, around an oculus. The mansard, tiers and upturned
eaves below are settings the rule takes, which no script wrote.

| roof | arguments |
| --- | --- |
| gable | `eaves=("north", "south")` |
| shed | `eaves="north"`: climbs south, its stairs facing up the slope |
| hip, pyramid | `rings="square"` (`hip=` puts a block on the corners) |
| hip over an L, a T, a U, a courtyard | `rings="square"` over a footprint, a set of `(x, z)` columns |
| cross gable, an L or a T of gables | `Wings([(roof, outline, base_y), ...])`, each wing its own rule |
| mansard, flat top | `rise=(2, 6, 10)`: two blocks a ring, flat past the last value |
| tiers | `rise=(2, 4, 6, 14, 16, 18)`: ring 3 stands as a wall |
| upturned eaves | `rise=(4, 2, 4, 6, 8)`: the outer ring lifted a block off `base_y`, tips and corners facing out |
| two slopes | `rise={"north": ..., "south": ...}`: they meet where their planes cross |
| cone | `rings="round"` |
| castle spire | `rings="round", pitch=4, depth=(4,) * r` |
| dome, Gothic spire | `rise=rise_of(arch_profile(r, h))`, `rise_of(pointed_arch_profile(r, h))` |
| pediment | a one-block-long eaves roof, `stair=None`, `crown="block"`, `close_ends=` its stone |
| a vault's course | `rise=rise_of(vault.profile, eave=...)`, `depth` the vault's reversed |

| call | does |
| --- | --- |
| `Roof(*, eaves=None, rings=None, pitch=None, rise=None, overhang=0, crown="slab", block, slab, stair, springer=None, hip=None, depth=None, close_ends=None)` | the rule; `block`, `slab` and `stair` are always named, `None` for no slab or no stairs |
| `.plan(outline, base_y, *, attic, attic_floor=None)` | every cell it writes and the state, in write order; pure |
| `.verify(save, outline, base_y, *, attic, attic_floor=None, dimension=None, missing_ok=False)` | every planned cell that does not hold what the rule says |
| `.rise_at(d, eave=None)` | the rise `d` rings in, in half blocks |
| `rise_of(profile, *, eave=2)` | a vault profile turned outside in: `eave + profile[-1] - profile[-1 - d]` |
| `Wings(wings)` | several roofs joined where they meet: `wings` is `(roof, outline, base_y)` each, `.wings` as given |
| `Wings.plan(*, attic, attic_floor=None)`, `Wings.verify(save, *, attic, attic_floor=None, dimension=None, missing_ok=False)` | a `Roof`'s, over every wing at once |
| `Builder.roof(roof, outline=None, base_y=None, *, attic, attic_floor=None, overwrite=False)` | lay a `Roof` over `outline` from `base_y`, or a `Wings` (neither); returns a `RoofResult` (`blocks`, `stairs`, `closed`, `hollow`, `already`, `springing`) |
| `Builder.gable_roof(x0, z0, x1, z1, base_y, axis, *, stair, plank, slab)` | the stepped gable: a `Roof` at pitch 2, overhang 1, plank ends, the attic hollow, written as it always was and with no check of what stands in its way -- except that its corners may come in either order, where reversed ones once wrote nothing, and a roof one block long writes each plank once |

- `outline` is `(x0, z0, x1, z1)`, inclusive and in either order, for eaves
  and square rings, and `(cx, cz, radius)` for round ones, the disc of
  `geometry.disc` (half rounding). It is the walls' outline; `overhang`
  widens it on every side. `base_y` is the block the rise is counted from:
  a rise of 2 is a whole block in it, so it is the eave course of any
  `pitch` and of a `rise` starting at 2, and a first rise above 2 lifts the
  eave off it.
- Square rings also take a **footprint**: a set of `(x, z)` columns, or a
  list or a tuple of them (what `geometry.disc` returns, a tuple of pairs).
  The forms cannot be confused -- a footprint's members are pairs, a
  rectangle's and a disc's whole numbers -- so `((0, 0), (0, 1), (1, 0),
  (1, 1))` is a plan and `(0, 0, 1, 1)` a rectangle, the same roof. The one
  form that reads both ways is a list or a tuple of two columns:
  `((0, 0), (10, 8))` is a rectangle's two corners as plainly as two
  columns, so where the readings are different roofs it is an `NBTError`
  naming both forms, `(0, 0, 10, 8)` and the set `{(0, 0), (10, 8)}`. A
  column's ring is `field.chessboard` over the roofed columns, less one: a
  king's walk to the outside, which runs a straight 45-degree valley up
  from every reentrant corner where the Euclidean distance puts a bump in
  it, and is the rectangle's ring exactly on a rectangle -- a rectangle
  given as its cells is the rectangle's roof, byte for byte. `overhang`
  widens a footprint as a king walks: every column within it of a wall
  both ways. A **hole** -- columns outside the plan that cannot reach its
  bounding box without crossing it side to side, two walls meeting only at
  a corner closing one as surely as a wall -- is a courtyard, left open
  with the roof sloping down into it (the Roman domus's compluvium, cut by
  hand through a hip); an overhang that would roof one over entirely is an
  `NBTError` naming its first column. An eaves or a round roof refuses a
  footprint, naming `Wings`, as an empty footprint or a member that is not
  a pair of whole numbers is refused. Where ridges meet on a plan -- an
  L's corner, a cross's crossing -- the junction is no crown: it has no two
  opposite neighbors lower, so under `crown="slab"` it stands half a block
  over the slab ridges running into it, as a peaked ring's corners stand
  over its sides on a rectangle.
- `pitch` (2 when neither is given) climbs that many half blocks a block
  from 2 at the eave, and a sequence is a running sum, flat past its end;
  `rise` gives the tops outright, each at least 1, flat past the last, and
  may fall and climb again. Both together is an `NBTError`.
- `crown="slab"` ends a ridge or an apex on a whole block in a bottom slab
  half a block lower, unless that leaves it no higher than a neighbor below
  it — a half-block ramp's ridge keeps its top slab; `"block"` takes the
  rise as it is.
- Each column is laid from its top down to the top of its lowest neighbor,
  so the shell is gapless from anywhere, by the loop `Vault` lays its
  columns with: an eave a whole block deep, a plateau one piece, `depth`
  (half blocks per ring, outermost first) where it is given. A top course
  falling a block or more is a stair facing up the slope; a corner falling
  two ways is `hip`'s block, or a stair facing up its slope, away from
  whichever of its two falls lies along the axis the center lies more
  along — exactly diagonal goes to z (`geometry.heading_of(ties="z")`):
  toward the center wherever it falls outward, a peaked ring's corners
  included. A corner of the outline, which falls only off the roof's edge,
  faces the center too, or away from it on an eave turned up. A footprint
  has no center to face (an L's lies outside it), so there a corner faces
  up its slope along whichever axis the roof runs longer through it, a tie
  to z -- the same choice on a rectangle, since a ring's corner lies more
  along the axis the ring is longer on -- and its eave is turned up where
  the column diagonally inside the corner is the next ring in and lower,
  never because some ring of the plan elsewhere is.
  `springer` is the whole courses `depth` thickens past their reach, never
  an eave's.
- A `Material` in a role is its form for that role, so one family roofs
  the course; a `Mix` is taken in the whole-block roles — `block`,
  `springer`, `hip`, `close_ends` and the attic — picked at each world cell
  as the plan is made, so what is planned is what is written; where the
  rule spells a facing or a type, it is a `TypeError`. A stair or slab role
  given a block the game has of another shape is an `NBTError` (and so is
  `gable_roof`'s), a `None` slab the roof needs is one naming the first
  cell in write order that needs it, and an id the game lacks is the
  write's `IllegalBlock`.
- **The attic is required**: `"hollow"`, the space under the shell left as
  it is — `RoofResult.hollow` counts its empty cells, since dark enclosed
  air under a roof the size of a house is a mob farm with a house under it
  — or a block, filled under every column of the walls' outline from
  `attic_floor` (the springing when left out: the lowest block any column
  reaches down into) up to the shell, a top slab over it made a double
  slab. `close_ends` (eaves only) closes the two end planes from the
  springing up the same way, whatever the attic is.
- `Builder.roof` refuses a cell already holding anything but what it would
  write there — snow on an old roof included — every cell in the faults,
  before a block is written; `overwrite=True` cuts through. A cell already
  right is left as it stands and counted `already`. The roof is not ground:
  the session's ground cache is left as it was.
- **Wings** lays each wing as a roof over its own outline, then gives every
  column the highest top any wing gives it; the first wing listed with that
  top owns the column, and its settings -- materials, crown, depth, how its
  hips face -- lay it, so a tie goes to the wing listed first. Then it is
  one roof: each column reaches down to its lowest neighbor whichever wing
  that is, so a wing's verge over a lower wing becomes a gable face down to
  it and the shell has no gap; where two wings' slopes cross, the valley is
  a column with no lower side neighbor, a plain block as a plateau is (Jeff
  laid inner-corner stairs in his; the rule does not). A wing's end plane
  is closed (`close_ends`) only where it faces out of every wing's walls --
  an end that runs into another wing is inside the roof and stays open --
  with the block of the first wing listed that closes it.
- Each wing keeps what it is alone where a join of tops would undo it.
  Under its walls stands its **building**, from the springing the wing has
  laid alone (the highest, where several wings' walls hold a column): no
  column over them reaches below it, so an eave on a wall line is one
  course deep however low the roof beside it, never a wall built down the
  house to a lean-to's roof; and the wing's ends are closed and its attic
  filled, or counted hollow, from it -- a tall wing's attic never filled
  down through its top stories from a lower wing's eave course. Over
  **open ground** a column reaches down to its own wing's roof alone: an
  overhang beside a lower wing is an eave with air under it. And a wing's
  **eave roofed over** by a higher wing keeps the floor it has alone
  wherever it closes the wing's attic -- everywhere but inside another
  wing's walls, whose roof closes the attic the two share -- so an
  overhang drawn over a lower wing's walls reaches down to that wing's
  eave, a face that keeps its attic shut (draw the lower walls clear of
  the overhang to leave the wall under the eave open).
  `RoofResult.springing` is the whole roof's, the lowest block any column
  reaches down into, and the write is course by course along the first
  wing's ridge. A wing needing a half piece from a rule with `slab=None`
  -- a whole-block wing roofing over a half-block one's walls -- is
  refused naming the first cell in write order, as one roof is. A hip
  over a plan is a footprint, not Wings: the two are the same roof
  wherever the wings overlap to each other's far edges, but where two
  rectangles only touch, their join leaves a trough along the seam. Two
  `Builder.roof` calls over the same columns still refuse each other;
  Wings is the join.
- There is no `Roof.read`: a ring roof is never continued, and a read
  reproduces a building only when its outline is known exactly and nothing
  is cut through it — not the hand-built roofs it would be wanted for. A
  one-axis odd-span gable reads as a `Vault`. `verify` is the sound half.

### Doorways and straight flights

```python
from heaton_voxel.craft import StraightStair

b.doorway(x, 64, z, "south", door="minecraft:iron_door")   # buttons both sides
stair = StraightStair((x, 64, z), "north", 4, width=2)     # first tread, heading, treads
b.flight(stair, floor_y=stair.story_levels()[1], rail="minecraft:oak_fence")
```

`doorway` cuts the opening, places the door whole, and gives an **iron**
door a button on each side, on a wall block that conducts. A button on
glass sticks and opens nothing, so a doorway with no conducting wall beside
it is refused before anything is written -- a `Refused` naming each side
that has none. Then the plan is audited (`Builder._audit_plan`), as a
wall's is: a door with no floor under it, which the game drops at the next
block update, or a cut that leaves a torch beside the doorway on nothing,
is a `Refused` naming each fault, before anything is written. The hung door
is checked from both sides by the same rule the walk check uses.

`StraightStair` is a switchback's run without the turn: tread `k` is `k`
rows along `heading` and `k` levels up, every tread after the first stands
on the wood the level below laid for it, and `claimed(y)` (shared with
`SwitchbackStair` through `StairRule`) includes the head room the treads
below put into every level above them. `story_levels()`, `entry()` and
`exit()` are the floors it joins and the cells you step from and onto;
`well_rail(floor_y)` is where a railing goes round the opening.

`Builder.flight` cuts only the floor named by `floor_y`, at exactly the
cells the stair claims there, and refuses, listing every fault, when a tread
or its wood would land on something, when a step has less than `HEAD_ROOM`
clear above it (the sky past the top of the world is clear), or when the
cells it needs are already claimed by an earlier flight from the same
Builder, or by one of its `approach`es -- the refusal says which. Flights
stacked in one column leave
neither of them head room. Then it walks the plan both ways, from the floor
in front of the first tread to the floor past the last, before a block is
written, so both floors must already be there (`walk=False` to skip).

### Furniture

```python
from heaton_voxel.craft import Builder, Frame, Piece

b = Builder(save)
chair = Piece.seat((0, 0), back="north", stair="minecraft:oak_stairs")
table = Piece.table((0, 0), (1, 1), leg="minecraft:oak_fence",
                    top="minecraft:oak_pressure_plate")
couch = Piece({(0, 0, 0): "minecraft:spruce_stairs[facing=north]",   # its own
               (0, 0, 1): "minecraft:red_wool",                      # cells,
               (0, 0, 2): "minecraft:red_wool"}, name="couch")       # facing south
b.furnish([table.at(x, 63, z),                       # on the floor block at y=63
           chair.at(x, 63, z - 1),                   # its back to the north
           couch.turned(Frame((cx, 63, cz), "east")),  # turned: back west, runs east
           Piece.bed((0, 0), (0, 1), bed="minecraft:red_bed").at(bx, 63, bz),
           Piece.stack((0, 0), (2, 0), block="minecraft:bookshelf",
                       height=3).at(sx, 63, sz)],
          room=(x0, z0, x1, z1), doorstep=(dx, 63, dz))  # walked before and after
spout = [(dx, dy, dz) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
         for dy in (1, 2)]                   # where a fountain's spout runs
fountain = Piece(cells, name="fountain", spill=spout)  # cells: its basin,
b.furnish(fountain.at(fx, 63, fz), room=..., doorstep=...)  # pillar, spout
```

A **`Piece`** is a small rule -- `(dx, dy, dz)` cells to blocks, in the
piece's own coordinates -- and **one height convention holds for every
piece: `dy=0` is the cell your feet stand in, directly on the floor block
the piece stands on.** A seat, a table's top or its legs, a bed, the bottom
of a shelf all start there, and nothing reaches below it. A piece is placed
by a `Frame` whose origin is that floor block: `piece.at(x, y, z)` stands it
there unturned, its `(dx, dy, dz)` at `(x + dx, y + 1 + dy, z + dz)`, and
`piece.turned(frame)` turns it -- its cells through `Frame.cell`, one row
up, and its states as a `framed` write turns them, a property left at its
default turned too, so a bare stair (facing north) faces east in a frame
facing west, a bed's head goes where the frame puts it and a mirrored
door's hinge changes sides. A piece placed already is placed again inside
another frame by composing the two (`Frame.within`), so a room laid out
once on a floor at `y=0` of its own frame is turned with the room. The
suburban house's furniture takes the same floor block (its `fy`); the
yellow tower's layouts put the cell on it at `dy=1`, and the Roman
scripts' `y` is the feet cell itself.

**A seat is named by its back.** A stair's full side is the side it faces,
so a chair's backrest is its `facing`: `back="west"` is a bench against a
west wall, sat on facing the room. Named by the way the sitter looks, every
seat comes out backwards -- the curia benches in JeffWorld's Roman forum
face the room with their seats against the walls.

| call | does |
| --- | --- |
| `Piece(blocks, *, name="piece", spill=())` | any piece: `{(dx, dy, dz): block}`; `spill` its own cells its water may run into |
| `Piece.seat(corner1, corner2=None, *, back, stair, ends=None)` | a chair (one cell) or a bench or sofa (a run) of bottom `stair` facing `back`; `ends` an arm past each end of a one-row run, facing the seat |
| `Piece.table(corner1, corner2=None, *, top, leg=None, cloth=None)` | `top` on the floor (a counter), or on a `leg` under every cell -- a fence and a pressure plate or a slab; `cloth` over it |
| `Piece.bed(foot, head, *, bed)` | a bed facing from `foot` to `head`, one placement, both halves |
| `Piece.stack(corner1, corner2=None, *, block, height=1, dy=0)` | `height` rows of `block` from `dy` up: shelves, a wardrobe, a rug, cupboards over a counter |
| `Piece.lamp(cell=(0, 0), *, lamp, post=None, height=None)` | a lamp standing on the floor, or on `height` blocks of `post` (one) |
| `Piece.pendant(cell=(0, 0), *, lamp, ceiling, chain=None, links=0)` | a lamp hung from the ceiling block at `dy=ceiling`, on `links` of `chain`; a lamp with a `hanging` property hangs |
| `.at(x, y, z)` / `.turned(frame)` | the piece placed on the floor block `(x, y, z)`, or by `frame` |
| `.blocks` / `.cells()` / `.placements()` / `.frame` / `.name` | its own cells; a placed piece's world cells, its placements (a bed by its foot), the frame placing it |
| `.spill` / `.spill_cells()` | its own spill cells; a placed piece's, placed and turned with it into world cells |
| `Builder.furnish(pieces, *, room=None, doorstep=None, walk=True, overwrite=False)` | place them, every one or none; a `FurnishResult` |

- **Two-block things are one placement.** A bed, a door and a tall plant
  come whole, given by either half or by both, as the yellow tower's
  layouts list a bed; two halves that do not make one thing are an
  `NBTError`, and so is an upper half given at `dy=0`.
- **Water in a piece is held by the water check.** A piece may hold water
  -- a tub's, a fountain's or a trough's sources, a waterlogged block, a
  bare sea pickle, kelp -- and `furnish` refuses one whose water gets out,
  naming every cell it would run, fall or waterlog into, unless the cell
  is in the piece's `spill`: its own `(dx, dy, dz)` cells, placed and
  turned with its blocks (`spill_cells()`), the water carried on through
  them and the first cell past them the leak -- the air a spout pours
  through into its basin. The suburban house's two tubs in JeffWorld hold
  their water, and its Roman fountain at (350, 350) holds its own exactly
  with the four cells beside its spout and the four under them as that
  spill (both read 2026-10-22). A spill under the floor row, on one of the
  piece's own blocks, or given to a piece with no water is an `NBTError`.
- **Refused as a piece is made** (`NBTError`): air, lava -- which no water
  check follows -- and flowing water (`minecraft:water` at a level from 1
  to 15), which the game makes from its sources: write the sources; a
  `dy` under 0, a seat's `stair` naming
  its own `facing` or `half`, a bed's naming `facing` or `part`, a bed's
  head not beside its foot. Every block is named, never a palette's (pass
  `b.palette[role]`); a `Material` is a seat's stairs or a stack's block,
  and a `TypeError` where a piece writes no one form of it; a `Mix` is a
  `TypeError` anywhere, since a piece is placed and a placement is one
  block. Whether the game has each block is asked by the Builder that
  furnishes it (`Builder(allow=)` passes a modded one).
- **`furnish` places every cell through `place`**, so a stair seat takes
  the corner shape its neighbors give it and a fence leg joins what stands
  beside it, as the game would. Written in piece order, each piece's
  placements in world cell order. Before the first write it refuses, each
  step naming every fault: two pieces in one cell (a bed with an amphora
  on its head, which the Roman insulae left as lone bed feet); a block the
  game lacks, a chunk not generated or not finished, a height past the
  world; a cell holding anything but air -- furniture never writes into a
  wall, the suburban vanity's lesson -- unless `overwrite=True`, while a
  placement already standing as planned, apart from what a block update
  derives, is left and counted `already`; a cell a flight or an approach
  this session built claims, whatever `overwrite` says; and what the survival audit
  (`Builder._audit_plan`) finds the plan would add: a lantern under nothing
  that holds it, a carpet on air, a lone half, a chest under a block that
  conducts power.
- **Then the room is walked**, from `doorstep` (the block you stand on to
  come in) over `room`'s columns, `(x0, z0, x1, z1)`, before and with the
  plan laid over it, by `reachable`, both ways. A cell you could get to
  and back from before -- walking, or with a jump, as onto a dais -- and
  can still stand on after, must still be so: one cut off is **sealed** --
  a bookshelf stack one cell from the wall seals the floor behind it, and
  every plan draws a finished room -- one reached and not left is
  **one-way** (cupboards over the floor beside a slab dais: you step up
  onto it and put your head into them stepping off), and one you walked
  to before and now reach only by jumping **now needs a jump**: a counter
  across the only way through. A cell a piece now stands on is furnished,
  not lost, and so is the floor under a piece's water, a tub's, which
  nobody walks in; a cell one-way before the call is not the call's, as
  the audit leaves a fault that stood before; and a doorstep a piece covers is a
  refusal of its own. `room` and `doorstep` are required unless
  `walk=False`. The walk vouches for nothing past the room's and the
  doorstep's columns, so a piece with a cell outside them is an `NBTError`
  before anything is read -- a shelf next door, across its only way in,
  would seal that room unseen -- and a piece in the room that closes the
  way on into a space beyond it is judged only when `room` takes that
  space in too. Name the room you mean: one whose only way round leaves
  those columns reads as sealed.
- **Last, the water** (`Builder._contain_plan`, the one water check):
  every way water gets out with the plan laid -- a tub's through a gap in
  its rim, a spout's past its basin, somebody's pool through the block a
  piece was placed over with `overwrite=True` -- outside the pieces'
  spill, each a `Leak` in one `Refused`. There is no seal: furniture keeps
  its own water in, and keeps clear of anybody else's.
- `FurnishResult` counts `pieces`, `placements`, `blocks` (both halves of
  a bed), `already` and `written`, and `reach` is the walk with the
  furniture in it. Like `Builder.place`, `furnish` writes states only:
  `overwrite=True` over a chest leaves the chest's block entity behind.

### Railways

```python
from heaton_voxel.craft import Builder, Railway

line = Railway([(-961, 66, 13),    # the end of the line being extended
                (-958, 63, 13),    # down off the platform, one per block
                (-912, 63, 13),    # level across the flats
                (-909, 60, 13),    # down through the portal
                (-744, 60, 13),    # the bore somebody already dug
                (-740, 64, 13),    # up the ramp
                (-687, 64, 13)])   # and onto the surviving line
print(line.verify(save))           # every reason it will not run, before
b = Builder(save)
print(b.railway(line, seal="minecraft:cobblestone"))
b.finish(connect=False)            # then save.save()
```

A line is a rule, like a stair: waypoints in, every block of the line out.
A waypoint's `y` is the **rail's** own block. Between waypoints the line runs
straight along x or z and is level or climbs one block per block; anything
else is refused rather than guessed, so put a waypoint wherever the gradient
changes and at every corner. From the cells the rule derives each rail's
`shape` (straight, curved at a corner, `ascending_<d>` toward the higher
neighbor); a powered rail on a redstone block on every climb and every
`boost_every` level cells; and the head room — `clearance` clear blocks over
each support, the rail's included, and `HEAD_ROOM` on the low side of every
change of level, for the reason a stair tread needs it.

| call | does |
| --- | --- |
| `Railway(points, *, clearance=2, boost_every=26, bed, power)` | the rule; `boost_every=None` lays no boosters, `bed` goes under anything that cannot hold a rail, `power` under each booster |
| `.cells` / `.shapes` / `.boosters` / `.state(i)` | the line, cell by cell, and the rail the rule lays in each |
| `.head_room(i)` / `.running_space(i)` / `.support(i)` | what each cell needs clear above its support, those cells, and the block it stands on |
| `.runs_on(rail, i)` | whether a rail already standing in cell `i` joins the line — how the end being extended is kept |
| `.verify(save, *, overlay=None)` | every reason it will not run: a missing rail, shapes that do not meet, a support that drops a rail, water in or beside the running space, a blocked head room, a walk that only goes one way |
| `Builder.railway(line, *, overwrite, seal, through)` | lay it, refusing before writing anything; returns a `RailwayResult` |

**`verify` checks what no picture shows.** The game keeps a rail only on a
*rigid* top face, which leaves, fences, walls, panes, path blocks, farmland
and bottom slabs are not, though each holds a player. A climbing rail also
needs one level with it where it climbs to. Sand or gravel with a hole
under it falls and takes the rail too. Water beside the running space runs
in the first time anything near it updates. And a ramp in a tunnel two
blocks high walks up and not back down. `overlay=` reads a plan of
`{(x, y, z): block}` laid over the save, so a line is checked before a
block of it exists.

**`railway` goes through the ground the way `road` does**, cutting what
stands in the running space and laying `bed` under whatever cannot hold a
rail; `RailwayResult.removed` lists by block everything it took out. It
refuses a registered pad or reservation anywhere its plan writes, seals
included (`through=` for a deliberate crossing), a rail it
does not own (`overwrite=True` to lay over it — a rail that already runs on
into the line, like a station stop, is kept as it is), cutting the block
another line's rail stands on, water touching the line (`seal=` names a
full block to wall each such cell off with, held to the one seal rule --
see "A plan that lets water out is refused, or sealed" -- before anything
is read, whether or not the line finds any water), and any plan whose
`verify` is not clean. Then it verifies the written line again.

---

## `heaton_voxel.craft.worksite` — the session's seatbelt

Every building session needs the same three habits: a pristine copy of
the files before the first edit, a content check over things that must
not change, and pictures. `Worksite` is the three as one object:

```python
from heaton_voxel.craft import Worksite

site = Worksite.open(world, (x0, z0), (x1, z1), name="harbor")
site.protect((px0, 60, pz0), (px1, 80, pz1), name="old-road")
site.render("before")

b = site.builder(seed=7)          # refuses what restore() could not undo
...build...
b.finish()

if site.check():                  # violated zones, then uncovered files; [] is clean
    site.restore()                # the build never happened
else:
    print(site.diff((x0, 60, z0), (x1, 90, z1)))   # what it changed, block by block
    site.save.save()
    site.render("after")
    site.accept()
```

The snapshot copies the region, entity and POI files the box (plus a
margin) can touch, byte for byte, into `<world>/worksites/<name>/` — and
records the files that *don't* exist, so a build that creates one is
undone by deleting it. Re-opening the same worksite resumes against the
snapshot and **refuses by file name if the world changed since**
(`resnapshot=True` rebases deliberately, keeping protected zones but
re-digesting them). `check()` reads through the open save, so it catches
in-memory damage before anything reaches disk. It returns a `CheckResult`,
a list of the violated zone names followed by the **uncovered** files —
every file `save()` would write that the snapshot does not hold, found the
way `save()` finds what to write: a region, entity or POI file of any
dimension, `level.dat`, the player file, a settings file. Those are exactly
what a `restore()` would leave behind, so `.zones` and `.uncovered` are the
two halves apart, and `if site.check():` still means something is wrong.
Reading a file is not a change; and once `save()` has run there is nothing
unsaved left to name. `open()` and `restore()`
both refuse while Minecraft has the world open, and `restore()` also
refuses a snapshot whose own bytes no longer match the manifest — writing
corruption over a world is worse than the build it was undoing. `accept()`
stamps the manifest and keeps the snapshot — disk is cheap, worlds are not.

### Building inside it, and seeing what the build changed

`site.builder(seed=0, allow=(), palette=None)` is a `Builder` over the
site's save and dimension carrying the worksite's guard (`b.guard`), and
**it cannot write where the rollback could not follow it**. Every write it makes is admitted
after every other check and before the first block moves, and a call
refused is refused whole — one `craft.Refused` with a fault per file and
per zone — with nothing written:

* **a region file the snapshot does not hold.** A block write reaches the
  file of its own chunk and of the eight chunks around it, because
  `finish()` relights those and settles connections a block across the
  border; so a write in the last chunk column of a held region is refused
  when the region next door is not held, though its own file is. Coverage
  is the manifest's list of files, region by region, so a write anywhere
  in a held region is restorable — including outside the box `render()`
  draws. A spawn needs its own region's entities file.
* **a protected zone**, read from the manifest at every write, so a zone
  protected after the builder was made holds it too. Strictly: a write of
  the state already there is refused, and a placement is refused when a
  neighbor it may re-derive — a fence, wall, pane, gate or stair beside it
  — stands in a zone, whether or not its state would change. A zone is
  block content; it does not keep entities out.
* **`finish()`** refuses, before heightmaps, POI and light, when its
  connection pass re-derived a fence or stair inside a zone. What connect
  changes cannot be known without running it, so this is the one refusal
  after a write, and it puts every zone cell it rewrote back before it
  raises: the zones are as `finish()` found them, the connections it
  settled outside them stay, and the build is in memory and unsaved.
  Asking again refuses again; `finish(connect=False)`, on the same
  builder, is the finish that leaves zones alone.

As on the write path, a surface drawn from its mix as it is written (a
road's) draws nothing from `rng` when the guard refuses it, and a refused
spawn draws no yaw; `pave` draws while it plans, before the guard is
asked, and puts `rng` back when refused; `ornamental_tree` draws while it
plans too, so a refusal there leaves `rng` past those draws.

`restore()` ends every builder the site handed out — their save is the
world it undid — so from then on anything one does there raises a plain
`NBTError`, never a `Refused`, whatever else would have refused it: a
write, a finish, a read of the ground, `b.save` itself, before a state is
checked or a keep-out consulted. `site.builder()` again makes a live one.
`Builder(save, guard=)` refuses a guard made for another save or a
`dimension=` other than the worksite's. The guard only reads, so a guarded
build that is admitted encodes byte for byte as the same build through
`Builder(save)`.

```python
d = site.diff((x0, 58, z0), (x1, 64, z1))         # {(x, y, z): CellChange}
assert set(d) == planned                          # changed exactly what was planned
assert all(c.before.name == "minecraft:water" for c in d.values())
lost = [cell for cell, c in d.items()             # a named block, gone or turned
        if c.before is not None and c.before.name in GROVE]
site.diff(c1, c2, save=MinecraftSave.load(world)) # after save(): what reached disk
```

`site.diff(corner1, corner2, *, save=None)` is **what the build actually
changed**: a `SiteDiff`, a dict of every cell in the box whose block state
differs from the snapshot's, keyed `(x, y, z)` in sorted order, each a
`CellChange(before, after)` — `before` read from the snapshot's own copies
of the region files, `after` from `save` (the site's own by default,
unsaved edits included; any save of the same world, such as a fresh load
after `save()`, for what reached the disk). `None` on a side is no block
there: no chunk, or outside the world. `SiteDiff.box` is the box compared.
This is the content truth the Builder's journal is not: whoever wrote a
cell, the diff has it, and a cell written with the state it held is not in
it. It is empty after `restore()`, and block states only — light,
heightmaps, biomes and entities are not compared. It refuses (`NBTError`)
a corner without a `y`; a box reaching a region file the snapshot does not
hold, since there is no "before" there; a snapshot whose copies under the
box no longer match the manifest, as `restore()` refuses one; a `save` of
another world; a manifest that cannot be read; and a chunk that differs
from the snapshot's and cannot be read on either side — "could not read"
never reads as "unchanged". A chunk whose bytes are the very bytes the
snapshot holds is unchanged whether or not it decodes (an old chunk the
game never converted, say): the same bytes are the same states. A corner
is floored to the block it is in, as `protect()` reads one — `-330.5` is
block `-331`. It only ever reads the snapshot.

### Undoing a build after the session that made it

The drift refusal has a consequence that looks like a bug the first time it
bites: **once a build is saved, its own bytes are drift.** From the digests
alone there is nothing to tell a build you meant from a week of somebody's
play on the same regions, so a later session cannot reopen the worksite —
and `resnapshot=True`, the documented way past that refusal, would rebase
the baseline onto the very build you wanted undone. `open()` is therefore
never the way back, and should not learn how to be.

```python
Worksite.rollback(world, "harbor", dry_run=True)   # what would move
print(Worksite.rollback(world, "harbor"))          # and move it
# <RollbackResult put back 1 file, 2 already matched,
#  snapshot taken 2026-08-24T23:25:01+00:00, accepted 2026-08-24T23:25:01+00:00>
```

`rollback` reads the manifest, checks every snapshot file still matches the
digest recorded for it, and puts those bytes back without `open()` in the
path at all. A manifest that is missing, cannot be read, is not JSON, or
records no files is refused by name with an `NBTError`, never a parser's
traceback, and one the process may not open is reported as unreadable,
never as missing. It is
deliberately **terminal** — it hands back a `RollbackResult`, never a
worksite, so nothing can go on to build against a baseline it has just
reverted. Open the worksite again afterwards to rebuild: the world matches
the snapshot once more, so the resume comes back clean and against the
*original* baseline, not a rebased one.

It also cannot tell what it is overwriting, and says so rather than
pretending otherwise. `dry_run=True` names every file first, and the result
carries the snapshot's own `taken`/`accepted` dates — an accepted worksite
is the one most likely to have been played in since, and that is where it
shows. `removed` names which of the files go by deletion (recorded absent,
or a sidecar the build made) rather than by having the snapshot written
over them.

### Finding a worksite, and looking before undoing one

```python
from heaton_voxel.craft import list_worksites

for entry in list_worksites(world):        # one per directory, in name order
    print(entry.name, entry.accepted, entry.paths, entry.problem)
```

`list_worksites` reads manifests, and each directory's size by `stat` — no
save is loaded, no world file hashed. Every directory under `<world>/worksites/` is listed,
including one without a manifest (`has_manifest` False: a backup somebody
made by hand) and one whose manifest cannot be read (`problem` says why), so
a damaged record neither hides the others nor reads as no worksite at all; a
directory the process may not look into is the second kind, never the first,
and a `worksites/` it may not list is refused. A path with no `level.dat` is
refused rather than listed as empty. `world` is the path the manifest
recorded, which differs from the save it sits in when the save was copied;
`sidecars_tracked` is False for snapshots older than `.mcc` tracking, which a
restore leaves alone. `size` is the bytes a directory holds, a hand-made
backup's as much as a worksite's, and None if some of it cannot be looked at.

The command line over both only looks, and `worksite list` ends with what
the save's snapshots come to:

```
python -m heaton_voxel worksite list <world>
python -m heaton_voxel worksite rollback <world> <name> [--dry-run]
```

`worksite rollback` runs `Worksite.rollback(..., dry_run=True)` — every
refusal the real undo makes, nothing moved — and prints the plan: `restore`
or `delete` for each file with its last-written time, flagged `written after
the build was accepted` where the mtime says so (a hint: a plain copy resets
it), the other worksites over the same files whose builds there would go
back with it, the sidecar caveat and any note. Snapshots copy whole region
files, so every *later* worksite is named, and so is every earlier one not
accepted before this snapshot was taken — two sessions open over the same
region at once undo each other — while one whose manifest does not read is
named as unchecked rather than left out. **There is no `--write`.** A
rollback overwrites whatever is in its files now, and in a played world one
region is commonly covered by several worksites and rewritten for months
after each was accepted; the undo stays a deliberate
`Worksite.rollback(world, name)` call, which the output ends by naming.
Exit status is 0 when everything read, 1 when a manifest could not be read
(any, for `list`; another worksite's, for `rollback`, which then cannot say
whether that build goes back too), and 2 for a refusal, with the reason on
stderr.

| call | does |
| --- | --- |
| `Worksite.builder(*, seed=0, allow=(), palette=None)` | a `Builder` over the site's save and dimension whose every write is admitted by the worksite's guard (`.guard`): refused before it writes in a region file the snapshot does not hold, reaches one through the chunks `finish()` settles, or touches a protected zone; ended by `restore()`. `palette` is the Builder's own |
| `Worksite.check()` | a `CheckResult`: the violated zones, then the files `save()` would write that the snapshot does not hold; `.zones`, `.uncovered` |
| `Worksite.diff(corner1, corner2, *, save=None)` | a `SiteDiff` of every cell whose state differs from the snapshot's, `{(x, y, z): CellChange(before, after)}`, sorted; `.box` |
| `Worksite.rollback(world, name, *, directory=None, dry_run=False)` | reinstate a snapshot from any process; returns a `RollbackResult` |
| `Worksite.restore(*, dry_run=False)` | the same, for the session that still has the worksite open; ends the builders `builder()` handed out |
| `Worksite.retire(world, name, *, dry_run=True)` | what removing an accepted worksite's snapshot would free and lose; with `dry_run=False`, remove it. Returns a `RetireResult` |
| `RollbackResult.files` / `.removed` / `.deleted` / `.already` / `.taken` / `.accepted` | what moved, which of those were deletions (and how many), what already matched, and how old a baseline it is |
| `list_worksites(world)` | every worksite a save holds, as `WorksiteSummary`, in name order; refuses a path with no `level.dat` |
| `WorksiteSummary` | `.name`, `.directory`, `.has_manifest`, `.problem`, `.world`, `.dimension`, `.box`, `.margin`, `.created`, `.accepted`, `.copies` / `.absent` (files snapshotted / recorded absent), `.paths`, `.zones`, `.sidecars_tracked`, `.note`, `.size` |
| `RetireResult` | `.name`, `.taken`, `.accepted`, `.size`, `.files`, `.zones`, `.note`, `.dry_run` |

### Retiring a snapshot

A snapshot is a whole copy of every region file a build reached, and `accept`
keeps it, so a save's worksites grow with every build until somebody decides
one is settled.

```python
plan = Worksite.retire(world, "old-plaza")            # looks: removes nothing
print(plan.size, plan.files, plan.zones, plan.accepted)
Worksite.retire(world, "old-plaza", dry_run=False)    # removes the snapshot
```

```
python -m heaton_voxel worksite retire <world> <name> [--delete]
```

`retire` removes that worksite's own directory and nothing else, so what the
build made stays built and all that goes is the way back to just before it,
with the protected zones it held. No other worksite leans on it, since each
snapshot is a whole copy; one taken earlier over the same files still goes
back past this build, with everything since. The command says all of that
before it does anything: the size, the dates, the zones and any note, then
the earlier snapshots over these files, how many of them each covers, and the
later ones.

It may have a `--delete` where `worksite rollback` has no `--write` because it
moves no file of the world: what it costs is an undo, never a build. It is a
dry run unless told otherwise, from Python as much as from the command line.

Refused, with nothing removed: a name that is not one directory under
`<world>/worksites/`; a path with no `level.dat`; a world open in Minecraft; a
worksite that is not there; a link; a directory with no manifest, since a
backup somebody made by hand is theirs to judge; a manifest that does not
read, since nothing then says what would be lost; and a worksite never
accepted, since nothing then says its build is settled and the snapshot may be
its only undo. A session that saved without `accept` leaves one of those for
good, because `open` refuses a saved build as drift, and `rollback` does not
stamp one either: such a snapshot stays until a person judges it and removes
its directory by hand.

The directory is renamed before it is deleted, so a delete cut short leaves
`.retiring-<name>` and never part of a snapshot under the name that restores
from it. `list` names that directory for what it is, nothing counts it as a
worksite however much of its manifest is left, and `retire` on that name
finishes the job. Exit status is 0 when everything read, 1 when a look could
not read another worksite's manifest (`--delete` goes on, or one damaged
record would stop all cleanup), and 2 for a refusal, with the reason on
stderr.

### A whole session

[`examples/craft_session.py`](examples/craft_session.py) is one, start to
finish, in the order every real one should run, and
[`examples/palettes.py`](examples/palettes.py) holds the two styles it
builds in, `ROMAN` and `LAB`, each role read off the build it is named for:

```python
from palettes import ROMAN

site = Worksite.open(world, (x0, z0), (x1, z1), name="craft-session")
b = site.builder(seed=7, palette=ROMAN)
site.protect(corner1, corner2, name="beside the lot, north")  # and east...
with b.stage("ground"):
    b.level_pad(x0, z0, x1, z1, y, name="lot", over_water="fill")
with b.stage("house"):
    house = b.shell(..., Facade(b.palette["wall"], ...),   # rules take a role
                    roof=Roof(..., block=b.palette["roof"], ...), attic=...)
    b.doorway(*house.walls["south"].door(dx), door="minecraft:iron_door")
    b.flight(StraightStair(...), floor_y=upper)
    b.furnish([...], room=room, doorstep=doorstep)
with b.stage("lane"):
    b.road(points, through=("lot",))                 # the palette's road
    b.line_lamps(points, post=..., lamp=..., light_level=NO_SPAWNS,
                 through=("lot",))
report = b.finish()
# then: report.unsupported against what stood before, site.check(),
# reachable() from the doorstep to the top story and back, spawnable() over
# what was lit, site.diff() -- and only then site.save.save(), site.accept()
```

It protects the land round a 32-block lot -- four strips two blocks out,
past the one block `finish()` settles connections in -- levels the lot and
builds a two-story house under a gabled roof (an iron door with its
buttons in the lab's palette), a lane lit by `line_lamps`, a square
`pave`d to `NO_NEW_SNOW_OR_ICE` with a villager in it, and a solid watch
tower crowned by `battlement` over its shell's `perimeter`, its deck lit
flush by `light_cells`, since nobody climbs to it and monsters would. The
legacy writers read the palette for every material left out; `Facade`,
`Roof` and `battlement` are handed `b.palette[role]`; and what a palette
has no role for -- the lamp post, the door, a flight's stair -- is a small
table in the example, `FITTINGS`, per palette (the furniture is the same
in both). `run(world, x, z, *, palette, y=None, name, write=False)`
returns its `Session` -- what each writer reported, the finish report, the
faults it added (none), the check, the walk, the spawns, the diff, the
journal's cells and its writes by stage, and the rollback's dry run -- and
`main(argv)` is the command line:

```
python examples/craft_session.py <world> --at X Z [--palette roman|lab] [--y Y] [--write]
```

Without `--write` the worksite's snapshot goes in a temporary directory and
nothing is written to the world at all; with it, the snapshot stays in
`<world>/worksites/<name>/`, and the build is saved only once every check
has passed, then read back from disk against the diff, accepted, and the
`worksite rollback` plan printed. A failed check exits 1 having restored
nothing, since nothing of the session's reached the disk: a restore then
could only put the snapshot back over whatever else wrote the world
meanwhile. Once the build is saved nothing raises: a read-back that fails
or differs exits 1, saved and not accepted, and a render or a rollback
report the game's opening the world prevents is a note.
`tests/test_examples.py` runs it on copies of the fixture in both
palettes, and makes each check fail in turn.

---

## `heaton_voxel.craft.light` — block light, derived

Stored light is baked and `reset_lighting` throws it away, so after an edit
nothing on disk says how bright a cell will be. This runs the flood the
game's light engine runs -- every emitter radiating its strength, one level
lost per block crossed, none at all into a solid block -- over a box, with
an `overlay` of blocks the world does not hold yet, so "will this melt, will
this freeze, will this spawn" is answered before anything is written; and
`plan_lights` decides where the lamps go so that it will not.

```python
from heaton_voxel.craft import (MELT_ICE, durable_emission, light_occlusion,
                                plan_lights, simulate_block_light)

field = simulate_block_light(save, (x0, 55, z0), (x1, 66, z1),
                             overlay={(x, 61, z): "minecraft:sea_pickle[pickles=4]"})
field.level(x, 62, z)            # 0..15; 0 anywhere outside the field
field.below(cells, MELT_ICE)     # [(x, y, z, level)] under a threshold
field.emitters                   # [(x, y, z, strength)] the flood started from
simulate_block_light(save, c1, c2, durable=True)    # only the light that lasts
durable_emission("minecraft:copper_bulb[lit=true]")  # 4: it weathers
light_occlusion("minecraft:oak_slab[type=bottom]")   # six face masks, down whole

plan = plan_lights(save, targets, 12,
                   {(x, 63, z): "minecraft:glowstone" for x, _, z in targets},
                   lattice=lambda key: key[0] % 4 == 0 and key[2] % 4 == 0,
                   at_most={lid: 10}, overlay=surface)
plan.lamps          # {key: {cell: BlockState}}: the chosen fixtures, by key
plan.overlay        # all of them as one plan, in cell order: write it, or check it
plan.dark           # [(x, y, z, level)] left under the level
plan.over           # [(x, y, z, level, limit)] lit past an at_most limit
plan.blocked        # targets inside an opaque block that gives off nothing
plan.excluded       # {key: why} for candidates never considered
plan.lit_before, plan.from_lattice, plan.patched, plan.field
plan.require()      # Refused with every dark and over cell as .faults, or the plan
```

The field reaches `margin` (15) blocks beyond the box on every side, because
a lamp fifteen blocks outside the box still lights its edge. Rules are the
game's, verified against the unobfuscated 26.3 jar: `max(1, dampening)` per
step, water and ice cost 1 like air, a full opaque cube 15 -- and a step is
stopped outright where the two blocks' shapes close the face between them
(`LightEngine.getOcclusionShape`, `light_occlusion`): the solid half of a
slab, the back of a stair, the underside of a snow layer, and the seam
between a bottom slab and a top slab side by side, whose two half faces make
one whole one. So a lamp set under lying snow does not light the snow, and a
top-slab ceiling holds the light under it. Emission, dampening and faces are
the game's own numbers for every block state, read from `craft.block_facts`
(generated by `tools/probe_block_shapes.py` from the jar) with omitted
properties taking their defaults: a bare `campfire` is lit and gives 15, a
bare `redstone_lamp` gives 0, a sea pickle gives `3 + 3 * pickles` only while
waterlogged. `tests/test_light.py` holds the flood to the light the game
itself stored in both fixture regions, cell by cell: it is never brighter,
and the game is never brighter except by light from outside the sample. **An
id the game does not have is air**, because air is what the game loads it as,
and one with no namespace is minecraft's (`stone` is `minecraft:stone`); a
cell in a chunk the world never generated stays solid and dark, so light from
where nothing is known is not counted. The game keeps light one section past
the top and the bottom of the world, and reads every block there as air, so
the flood does too: a lamp in the top layer lights the cell a walker on it
stands in, and a floor laid at the build limit is lit like any other. The
thresholds worth keeping beside it are named (see
[light levels](#light-levels-the-rules-a-lamp-is-for)): ice melts at block
light 11 (`MELT_ICE`), and water freezes below 10 (`NO_NEW_SNOW_OR_ICE`), so
10 is a dead band where nothing changes. Judged tier, Python only.

**Not all light lasts.** `durable_emission` is what a state keeps with
nothing powering it and nobody acting on it, never more than
`light_emission`: a lit furnace, blast furnace or smoker goes out on its
first tick with nothing in it, lit redstone ore fades, a lit redstone lamp
goes dark at its next block update unless something powers it, fire burns
out off netherrack, a flow of lava drains, and a trial spawner goes dark
for its cooldown -- all 0 -- while a vault keeps the 6 it gives off with
nobody near and an unwaxed copper bulb the 4 it weathers down to (wax it to
keep 15). It is judged from the state alone, so a lamp held lit by a lever
counts as dark, the direction that adds a lamp rather than leaving a floor
dark. Every other light source is named as lasting, and the test fails by
name when a game update adds one neither table has judged.
`simulate_block_light(..., durable=True)` floods with it; the default
counts every source as it stands, the light to hold a ceiling to. The
Builder's lamps (`pave`, `light_cells`, `hang_lamps`, `shaft`, `basin`)
are credited with the light they keep: `redstone_lamp[lit=true]` is
refused, saying it does not last, and a lit copper bulb is counted at 4.

**`plan_lights` is the guarantee.** `targets` are the cells whose light the
game reads (a walker's feet cell, an ice cell that must thaw) and `at_least`
the level each needs, 1 to 15 or a named one (`NO_SPAWNS` read for
`dimension`), reached by the durable flood over the world as
it is plus `overlay` plus the lamps chosen -- so a wall gets a lamp on each
side, a lamp already in the world is counted and one somebody broke is not.
`candidates` maps a key cell to a block (a lamp there) or to a whole fixture
`{cell: block}`: a lantern with its chain and the block it hangs from. A
candidate with no lasting light is refused by name. `lattice` proposes keys
to try first — a predicate on keys, tried in key order, or a sequence of
keys, tried in the order given — each placed if it lights a target still
dark; the rest are patched in greedily, the one lighting the most dark
targets first, ties to the lowest key, so the plan does not depend on the
order candidates came in. The Builder's floors pass a sequence: their lattice
and the arithmetic's patches, in the order it found them, so on open ground
the plan is the one the arithmetic made. A fixture that changes the shape of
the world (an anchor, glowstone in a path) shades what an earlier lamp lit,
which only a fresh flood sees, so the plan re-floods and patches again, up to
eight times. `at_most` holds cells to a ceiling, 0 to 14 -- an ice lid over a
room that must stay frozen: a candidate that could light one past its limit
by the taxicab bound (every step costs at least one, and a shape only ever
takes light away, so the bound holds whatever stands between), or that makes
any cell more transparent and so could let the world's own light through, is
excluded with its reason, and the final check counts every source as it
stands, transient ones included. Dark is a result, not an exception, as
stranded is for `reachable`; `require()` is the refusal. The world is read
only as far past the targets, the limits and the fixtures as light that could
decide them travels — `15 - at_least` blocks, or `14 -` the lowest limit — so
every target at the level and every limit passed reads exactly, and a dark
target's reported level counts only that light; `plan.field` covers that
much. A candidate's own flood is aimed at the dark targets, which leaves a
lamp hung over a floor flooding the column under it rather than the ball
around it. On a 64 x 64 stone floor, planning glowstone for every cell takes
about 0.08 s at level 12 and 0.35 s at level 1.

### Light levels: the rules a lamp is for

A light level is worth asking for by the rule of the game it meets, so the
ones the game has a rule for are named, each read off the 26.3 jar, and the
Builder's light writers — `pave`, `light_cells`, `hang_lamps`,
`line_lamps`, `shaft` with a light and `basin` with a `lamp=` — take one
of them, or any level 1 to 15, and no default:

```python
from heaton_voxel.craft import (MELT_ICE, MELT_SNOW, NO_NEW_SNOW_OR_ICE,
                                NO_SPAWNS, LightLevel, DimensionLightLevel)

b.pave(x0, z0, x1, z1, 64, light_level=NO_NEW_SNOW_OR_ICE)
b.pave(x0, z0, x1, z1, 64)          # TypeError: pave needs light_level=, ...
NO_SPAWNS.at("minecraft:the_nether")   # 8
MELT_SNOW == 12, str(MELT_SNOW)        # True, "12"; repr is "MELT_SNOW (12)"
```

| name | level | the game's rule | what a floor lit to it does |
| --- | --- | --- | --- |
| `NO_SPAWNS` | 1 in the overworld and the end, 8 in the nether | `Monster.isDarkEnoughToSpawn`: block light over the dimension type's `monster_spawn_block_light_limit` (0) refuses a spawn; where the limit is 15, as in the nether, a light level over its `monster_spawn_light_level` (7) does | nothing on the Monster light rule spawns on it — `survey.spawnable`'s scope |
| `NO_NEW_SNOW_OR_ICE` | 10 | `Biome.shouldSnow` and `Biome.shouldFreeze`: block light under 10 | no snow settles and no water freezes; what lies there already stays |
| `MELT_ICE` | 11 | `IceBlock.randomTick`: block light over 11 less ice's dampening, 1 | ice melts |
| `MELT_SNOW` | 12 | `SnowLayerBlock.randomTick`: block light over 11 | snow lying on it melts |

`NO_SPAWNS` is one past `MONSTER_SPAWN_LIGHT`, which lives here beside it
(`craft.survey` imports the same table), and so comes from the dimension
types the probe vendors out of the jar; a dimension whose rule is not known
is an `NBTError` rather than a guess. It is a `DimensionLightLevel` and,
deliberately, **not an int**: a literal `1` is 1 in every dimension and
`NO_SPAWNS` is 8 in the nether, so `NO_SPAWNS == 1` is false and `int()` of
it is a `TypeError` saying so. The writers and `plan_lights` read it for
their dimension, and report the number they planned to
(`PaveResult.light_level`, `LineLampsResult.light_level`,
`ShaftResult.light_level`, `LightPlan.at_least`).
The other three are `LightLevel`s: ints, equal to their number and printing
as it, with `.name` and `.rule`. `MELT_LIGHT`, the older name `craft.build`
keeps, is `MELT_SNOW` — the same object.

The choice is most of the lamps. A glowstone floor's lattice is laid
`2 * (15 - level - 1)` apart: 4 at `MELT_SNOW`, 8 at `NO_NEW_SNOW_OR_ICE` —
a quarter as dense — and 26 at `NO_SPAWNS` in the overworld, a forty-second
as dense. A real floor's edges add lamps of their own, lit along the edge
from the margin the lattice leaves, and how many depends on where the floor
sits against the lattice, which is laid in world coordinates rather than
from the floor's corner. Over every placement against it, a 208-block
square took 5,460 to 5,461 lamps at `MELT_SNOW`, 1,403 to 1,430 at
`NO_NEW_SNOW_OR_ICE` (still about a quarter) and 144 to 172 at `NO_SPAWNS`
(a thirty-eighth to a thirty-second, a thirty-fourth at the median); a
30-block one took 121, 36 to 41 and 5 to 9, which on a floor that small is
mostly edge.

## `heaton_voxel.craft.audit` — a build already standing, judged

A `Builder` checks what it writes before it writes it. A paste from the app
or a schematic checks none of that, and every picture draws it finished.
`audit` puts the same questions to what stands in a box, whoever built it.

```python
from heaton_voxel.craft import audit

found = audit(save, (x0, y0, z0), (x1, y1, z1), start=(dx, dy, dz))
print(found.report())
found.clean        # nothing found
```

| `Audit` | what it holds |
| --- | --- |
| `.unsupported` | every `Unsupported`: what the game will not keep, and doors nothing opens |
| `.flowing` | every `WaterBody` with moving water in the box, largest first; a settled flood has nowhere left to go, so `contained` passes it and this does not |
| `.leaks` | every `Leak` the box's water runs through at the next block update, by the model `contained` keeps: every side the game's rules allow, so it can name a side the game would not take first, and never misses one |
| `.heightmaps` | `{(chunk_x, chunk_z): {map name: columns wrong}}` |
| `.walk` | the `ReachMap` from `start=`, the block you stand on, or `None` where none was named: only the caller can say where a build is entered. `.stranded` is its one-way cells |
| `.skipped` | `{(chunk_x, chunk_z): why}`: never generated, unfinished, or saved by 1.17 |
| `.clean`, `.report()`, `.box` | nothing found (as far as it read); the findings in words; the box |

Read-only, and nothing here refuses: a one-way door or a fountain may be what
was meant, so a finding is a thing to look at. A chunk that cannot be read
raises a plain `NBTError`, since could-not-read must never pass for clean.
`allow_jump=False` walks without jumping. The command line is `python -m
heaton_voxel audit <world> --box X1 Y1 Z1 X2 Y2 Z2 [--from X Y Z] [--no-jump]
[--dimension D]`, whose coordinates take `~`; it exits 0 when nothing was
found, 1 when something was, and 2 for a refusal.

## `heaton_voxel.craft.render` — pictures, for eyes not pipelines

Read-only, and the other half of the judged tier: the block color table is
best-effort game knowledge — unmatched blocks draw gray and are counted on
the returned `ColorMap` (`colors.unknown`, most frequent first), and a
stair, slab or wall the game has is drawn in the color of the block it is
cut from. The tables themselves live in `craft.colors`, below. `render_area`
draws what a stored map says, so it reads every finished chunk it touches
against its blocks, and `colors.stale_maps` is `{(chunk_x, chunk_z): columns}`
for the map it drew from, which the CLI prints as a warning: a build pasted
without a rebuild draws as the ground under it, and nothing else would say so.

`render_elevation(save, side, corner1, corner2, path, *, dimension=None,
scale=1, shade=True, colors=None)` draws a box as you see it from outside.
`side` is the compass word you stand on, so `"south"` looks north at its south
face, and each pixel is the first thing a look straight in meets inside the
box. That is what `render_slice` cannot show: a slice is one plane, so glass set
back behind its piers and a cornice proud of its wall are in different
pictures. It is as you would see it, west on your left looking north and on your
right looking south, and `shade` darkens what stands behind the plane most of
the picture stands in, so a recess reads as one. Glass stops a look, as a block.

```python
from heaton_voxel.craft import render_area, render_slice

render_area(save, (x0, 0, z0), (x1, 0, z1), "site.png", scale=4)
render_slice(save, "x", 169, (0, 55, z0), (0, 150, z1), "profile.png")
render_plan(save, 66, (x0, 0, z0), (x1, 0, z1), "floor.png", scale=4)
```

`render_area` is a top-down map of a box: each column its surface block (any
of the four heightmaps), shaded by slope, water darkened by its depth as
`walk_surface` reads it (so a lake an edit's `rebuild_heightmaps` stripped of
`OCEAN_FLOOR` keeps its depth, and kelp at the surface is darkened with the
water around it while a waterlogged stair on the seabed is drawn as the bed
it is); black is ungenerated, magenta unreadable — the same vocabulary as
`world_map.py`, which now builds on this module. `render_slice` is a vertical
cutaway along one plane, the picture that answers "did the stairway actually
reach the dungeon": open sky and covered interior are shaded differently, so
caves read as caves. `render_plan` is the third permutation — a horizontal
cut at one `y`, the architect's floor plan. It answers what an area map
cannot, because an area map draws the roof over the whole building: where the
doors are, whether a wall is solid or has a gap, whether a room is enclosed
at head height. Air is shaded on the same reasoning as `render_slice` — open
air draws light, air with something above it draws cave-dark — so a room
reads as a room and a courtyard as a courtyard. `y` is ignored by
`render_area`, honored by `render_slice`, and supplied as its own argument to
`render_plan`; `scale` repeats pixels, nearest-neighbor.

Also here: `PNGWriter`, a streaming stdlib-only PNG encoder (rows in, flat
memory), and `ColorMap`. There is a CLI:

```
python -m heaton_voxel render <world> --area X0 Z0 X1 Z1 -o site.png --scale 4
python -m heaton_voxel render <world> --plan 66 X0 Z0 X1 Z1 -o floor.png
python -m heaton_voxel render <world> --slice x 169 20 164 55 150 -o profile.png
python -m heaton_voxel render <world> --elevation south X0 Y0 Z0 X1 Y1 Z1 -o face.png
```

### `heaton_voxel.craft.colors` — the colors, in one public place

Every color the pictures draw, and every color the Unity app bakes: the
app's palette generators read these names and nothing private, so a color
changes here and only here.
The module is a leaf of literals that imports nothing from the package.

| name | is |
| --- | --- |
| `block_palette()` | name → RGB for every block named exactly (302, the dye families expanded); no air, which render draws as the background |
| `block_heuristics()` | the ordered `(needle, rgb)` substring rules; the first needle in a name wins |
| `WOOD_SPECIES` / `WOOD_FALLBACK` | the tree names that catch a wooden thing no rule names, and its color |
| `BLOCK_UNKNOWN` | the gray for everything else |
| `BIOME_COLORS` / `biome_palette()` | the 66 vanilla biomes of 26.2 — chosen to be told apart, deliberately not the game's own greens. One behind 26.3, whose new `dappled_forest` takes the plain forest's color until one is chosen, so the generator's audit against the 26.3 jar refuses |
| `biome_heuristics()` / `biome_color(name)` / `BIOME_UNKNOWN` | the biome fallbacks and the resolver over all three |

`ColorMap` resolves a block in the order the generated C# does — the
table; then, for a stair, slab or wall the game has and the table does not
name, the block it is cut from (`blockinfo.cut_shapes()`, which the block
generator bakes in as `CutFrom`), down the rest of the ladder; then rules,
wood, unknown — and `biome_color` likewise for biomes, so a reordering here
recolors worlds in both. So an oak stair is drawn as oak planks, a quartz
slab as quartz and a stone brick wall as stone bricks, where one gray used
to draw every stair and slab; a modded stair, which the game's registry
does not name, keeps the rules' stair-and-slab gray rather than a guess
from its stem. A cut whose block has no color (today five 26.3 blocks:
cinnabar, sulfur, their polished forms, and polished tuff) is unknown, and
counted on every call under that block, so `unknown` names the one entry
that would color all its cuts. Accessors hand out fresh copies.
`craft.biomes` still answers for the biome colors under its older names
(`BIOME_COLORS`, `UNKNOWN`, `build_palette`, `heuristics`, `color_for`) and
keeps what is not a color: `pretty(name)`, `FREEZING_BIOMES` /
`freezes(name)`, and the game's villager table, which `Builder.villager`
types its villagers by:

| name | is |
| --- | --- |
| `VILLAGER_TYPES` | biome → villager type for the 29 biomes the game's `VillagerType.BY_BIOME` names: desert (the desert and the three badlands), jungle, savanna, snow (eleven cold biomes, from the frozen oceans to the jagged peaks), swamp (with the mangrove swamp) and taiga |
| `VILLAGER_TYPE_DEFAULT` | `minecraft:plains`, the game's `VillagerData.DEFAULT_TYPE`: every other biome's type, and what the game loads a type it lacks as |
| `VILLAGER_KINDS` | the seven types the game's `villager_type` registry holds |
| `villager_type(biome)` | the type a villager standing in `biome` gets: its entry, else the default — the game's own fallback |

All three are curated, the way `FREEZING_BIOMES` is, and held entry for
entry to `fixtures.villager_types()`, which `tools/probe_block_shapes.py`
reads out of the jar.

---

## `heaton_voxel.craft.survey` — reading the ground first

Read-only, and the half of a session that comes before any of the above.
Every build starts with an hour of working out what is actually there, and
the library had tools for writing a build and none for surveying one.

```python
from heaton_voxel.craft import survey, walk_surface

probe = walk_surface(save, -84, 320)
probe.y, probe.block.name, probe.snow, probe.water, probe.kind, probe.buried
probe.ice, probe.bed_y, probe.lid, probe.waterline, probe.wet, probe.status
walk_surface(save, x, z, below=69)    # the floor under a point: an interior

ground = survey(save, (x0, z0), (x1, z1), step=1)
print(ground.text(legend=True))       # the character map
print(ground.text(mode="height"))     # where the ground steps
ground.counts                         # {"paving": 812, "path": 74, ...}
ground.cells("path")                  # every path column, reading order
ground.missing                        # {(x, z): "ungenerated", ...}: why each hole
```

`walk_surface` exists because "the surface" is several different numbers and
picking the wrong one is silent. A snow layer is part of `WORLD_SURFACE` and
of no motion-blocking map — those sit *under* it, so `surface_y` never sees
snow — while water stops every motion-blocking map, so over a lake they land
on the water; and `WORLD_SURFACE` lands on the snow, the leaves and the
torch. A probe reports the block you
would stand on (`y`) and reports what it stepped past rather than folding it
into the height: `snow`, read from the cell above the ground; `water`,
stepped through to the bed and counted cell by cell — kelp, seagrass and
every waterlogged state nothing solid stands in count as water (waterlogged
leaves too: canopy is not ground, wet or dry), air hanging in the run under
a waterlogged sign or in a pocket of a lake does not, and lava is a surface.
An ice sheet over water or air is stood on: `y` is the ice, `ice` its
thickness, `water` what is sealed under it, and `bed_y` the bed under that
(`None` when the sheet lies on air; elsewhere `bed_y == y`). `lid` is True
when what the probe stands on has air directly under it — ice over a room, a
lake bed over a cave, a one-block roof — and `waterline` is the top of what
stops a fall (the ice, the water's surface, else `y`:
`MOTION_BLOCKING_NO_LEAVES`), which over open water is `y + water` unless
air hangs in the run. `top_y` is what `WORLD_SURFACE` and a top-down render
see, and `buried` is True when the two disagree — the case where the map
draws snow and the survey wants the path underneath.

The column reads the same whatever the chunk carries. Where the game wrote
`WORLD_SURFACE`, `MOTION_BLOCKING_NO_LEAVES` and `OCEAN_FLOOR`, the first
two are trusted, at any status, as far as the block a map names agrees — a
map written before 26.3 stops at a sign, a stale one can name air or a cell
above the world, and the probe walks down from any of them — and the bed
under water is walked down to, since its cells are counted on the way; where
a rebuild took any of the three away, a finished chunk is read from its
blocks by the game's own heightmap tags and fluids
(`block_facts.in_heightmap`, `holds_fluid`), which give back the maps the
game wrote — `tests/test_survey.py` holds both to that on every column of
both fixtures. `below=` always reads the blocks. `None` means nothing to
stand on: no chunk (`"ungenerated"`), a partly generated chunk without its
maps (`"partial"` — its blocks are not ground yet, and scanning them invents
terrain), or a column of nothing (`"empty"`); `survey` keeps the reason per
column in `SurveyMap.missing`, and its repr counts them. A chunk that cannot
be read, and a heightmap that contradicts its own chunk, raise a plain
`NBTError` from `walk_surface` — corruption never reads as empty ground —
while `survey` records the column `"unreadable"` and surveys around it.
`NoSurface` takes the same words as `.reason`, with `.cell`: `Builder.probe`
and `Builder.ground` read through this reader, and raise one for each kind
of absence and a plain `NBTError` for a chunk that cannot be read.

### Will the game keep it?

```python
from heaton_voxel.craft import unsupported

for fault in unsupported(save, (x0, 60, z0), (x1, 120, z1)):
    print(fault.kind, fault)        # "lone half", "no footing", "falls", ...
```

The questions a render never asks:
- a bed, door or tall plant missing its other half;
- a torch, ladder, button, lever, lantern, sign, banner, pressure plate or
  carpet with nothing able to hold it, judged by the game's own sturdy faces
  (so a ladder facing into its wall, a button on a fence and a torch on
  leaves are all caught);
- sand, gravel or concrete powder over a hole;
- a plant off its soil -- each on the footing its block class asks the game's
  own tag for: a flower, a sapling or grass on `supports_vegetation`, an
  azalea on `supports_azalea` (the soil and clay), a dead bush on
  `supports_dry_vegetation` (soil, sand and terracotta, never glazed) and
  sugar cane on `supports_sugar_cane` (soil and sand, never farmland), each
  copied in `craft.survey` and held to the vendored tag; a bamboo shoot,
  which stands on a tag not copied, is not judged -- and sugar cane with no
  water beside its block, read as the game reads it: any water (`holds_water`,
  so a flow, seagrass, kelp and a bubble column water it too, and an old
  chunk's waterlogged `chain` as the `iron_chain` it loads as) or frosted ice;
- a chest -- any of the eleven, ender and copper chests included -- under a
  block that conducts power (`"blocked chest"`): the game will not open it
  (`ChestBlock.isBlockedChestByBlock` tests conduction, not opacity, so
  glass, a slab or a carpet over a chest is fine and gravel is not), nor
  either half of a double chest one half of which is blocked;
- an iron door nothing can open (`"sealed door"`), or one that opens from
  one side only (`"one-way door"`, often meant).

`overlay=` audits a plan before it is written. Every block, the plan's and
the world's, is judged as the game loads it: a property a state leaves out
takes its block's default, so a bare `minecraft:wall_torch` faces north and
hangs on the block south of it, or on nothing. Sections with nothing fragile
in their palettes are skipped unread, so a whole town takes under a second.
`Builder.finish()` runs it over what it wrote and two blocks around it,
and lists the result as `.unsupported`; `finish(strict=True)` refuses
instead, except for one-way doors -- a blocked chest included.

### Can you walk it?

```python
from heaton_voxel.craft import reachable, StepSpace, step_kind

walk = reachable(save, doorstep, (x0, y0, z0), (x1, y1, z1))
walk.walked, walk.jumped, walk.stranded      # cells: the block you stand on
walk.missing(story_columns, y)              # what you cannot get to
print(walk.text(y, StepSpace(save)))         # . walked  j jumped  ! one-way  # marooned

plan = {(x, y, z): "minecraft:stone", ...}   # a build that has not happened
reachable(save, doorstep, c1, c2, overlay=plan, allow_jump=False)
```

The question no render answers. A two-cell body and the game's 0.6
auto-step, so a step onto a stair or a bottom slab (half a block) is one
you walk and a step onto a full block is one you must jump — that is the
whole reason a staircase of treads walks and a staircase of beams does
not. Give it an `overlay` and it walks a plan before the plan is written,
which costs nothing to change; the same check afterwards costs a rollback.
`Builder.flight` walks its stair that way, and `Builder.furnish` walks the
room before and after its furniture, refusing floor the furniture cuts off.

**A ladder is a way up.** The game's `climbable` tag -- ladders, vines,
scaffolding, weeping, twisting and cave vines, `survey._CLIMBABLE`, held to the
vendored tag -- is a body that holds on: a walk goes up and down it a row at a
time, neither a jump, and steps off onto a floor level with its top or anywhere
below, and a step into its column catches the rung at your own height. Nobody
jumps from a ladder, so that is as far as it reaches. A rung is a cell like a
floor's, the block under your feet, which may be air, so `walk.footing(y)`
counts the rungs of that level and a floor is asked after with
`walk.missing(columns, y)`. `StepSpace.climbable(x, y, z)` says which cells hold.

**The round trip is not optional.** Getting somewhere and getting home are
two questions, and the move relation is not symmetric: a slab overhanging
a tread is half a block going up and a wall coming down. A forward-only
search called a seventeen-story tower finished — 2,332 cells walked, none
stranded — when every floor above the door was a one-way trip. So
`reachable` records the moves it made and walks them backwards from the
doorstep; whatever cannot get home lands in `.stranded`, which is the
answer to read first.

**`step_kind` classifies by exact name, whole suffix or whole prefix —
never by bare substring — and defaults the unknown to solid.** A substring
test made `grass_block` passable because it starts with "grass", and took
every `light_blue_*` block with it. The checker deleted the floor, and a
walk check that deletes the floor reports every room as reachable whatever
is standing in it. A wrong `full` raises a false alarm somebody reads; a
wrong `air` hides a wall nobody sees.

**An iron door is a wall unless something opens it from your side.**
`step_kind` calls every door air, because its shape is a panel. Whether you
get *through* an iron door is a question of power, so `StepSpace` asks it
move by move with `iron_door_opens`: it needs an opener on the side you come
from, either in a cell touching the door or attached to a conductor that
touches it. A button on one side only is a door you go through and cannot
come back through, so it shows up as `stranded`.

It answers one of four: `air` (walk through), `half` (step onto at half a
block), `full` (jump onto) and `tall` — a fence or a wall, a block and a
half, which `StepSpace` neither stands on nor climbs, so a pen has no door
it did not build. The tables are held to the game's own collision shapes
on every block state by `tests/test_survey.py`, against
`fixtures/block_shapes.json` (`tools/probe_block_shapes.py` asks the game).
Where they depart from the game it is a choice the test names: doors and
gates are `air` because you open them, a bottom stair is `half` because you
climb it from its low side, lava and fire are `full` because nobody should
walk into them, and natural snow (one layer, with no collision) is `air`.

`survey` is that probe over a rectangle plus the character map, and `step=`
samples a coarser grid — a whole region at `step=4` costs a sixteenth of the
lookups and still shows where the roads run. The classification (`classify`,
`KIND_GLYPHS`) is game knowledge and taste, which is why it lives here and
not below: a cobbled square is *built* in a survey's eyes even though the
game will happily generate cobblestone in a cave.

### Will anything spawn in it?

```python
from heaton_voxel.craft import MONSTER_SPAWN_LIGHT, SpawnCell, spawnable

for cell in spawnable(save, (x0, 64, z0), (x1, 90, z1), overlay=plan):
    cell.x, cell.y, cell.z, cell.light    # y is the feet cell, one above the floor
MONSTER_SPAWN_LIGHT                       # {overworld: 0, the_nether: 7, the_end: 0}
                                          # (craft.light's; NO_SPAWNS is one past it)
```

Every cell in the box a monster on the game's Monster light rule can spawn
in at night, in `(y, z, x)` order. The block under it must be a floor a
monster stands on (`block_facts`' `spawn_floor`: a sturdy top, never glass,
ice, leaves or a bottom slab, and by default never a block giving off light
14 or more -- glowstone, a sea lantern, a froglight -- though a lit
redstone lamp and a jack o'lantern are floors; magma counts, for the
fire-immune), it and the cell above must pass the game's per-cell test of
room (`isValidEmptySpawnBlock`: not a whole collision cube, no signal
source -- a lever, a button, a redstone torch -- no fluid, no rail, nothing
that hurts the monster) for some monster on the rule, the feet cell's own
block must leave the middle of the cell clear -- a carpet, a fence, a slab,
a shut gate or scaffolding takes it, a ladder or a door at the cell's edge
does not -- and its lasting block light must be no more than
`MONSTER_SPAWN_LIGHT` allows for the dimension, from the dimension types
the probe vendors out of the jar. The game asks the per-cell test per
monster, and two on the rule are immune to what hurts a zombie: a stray
spawns in powder snow, and a wither skeleton, fire-immune, in a wither rose
or a fire. `block_facts`' `spawn_space` is a zombie's answer, so those four
are named beside it, each with the monster it lets in. Sky light never
makes a night spawn impossible in the overworld, so it is not read.
`overlay=` checks a plan before it is written. Drowned are on the light
rule too, but spawn in water, which this does not read; zombified piglins,
ghasts, magma cubes, blazes, piglins, slimes and phantoms spawn by rules of
their own. None of them is answered.

What is approximated errs toward listing a spawn, never toward missing one.
No single state says whether a monster's box fits, so the cell above needs
only the per-cell test: a spider is 0.9 tall and fits under a top slab at
head height that a zombie does not, and its neighbors are not read, so a
spawn listed there may be nobody's. Each cell is passed for whichever
monster it lets in rather than all of them for the same one, and neither a
stray's open sky nor a wither skeleton's third cell is read, so a spawn in
powder snow under a roof is listed too. The middle-of-the-cell test is held
to the game's collision shapes, with no departures, for every state a
monster may stand in -- asked with no entity, as the game asks it for a
spawn, which is why scaffolding stands whole there. Nothing reaches up into
the feet cell from below, because no block taller than a whole one is a
floor, which the tests hold to the game too. Only light that lasts counts,
so a lit furnace does not spawn-proof a room here. An id the game does not
have is air, and one with no namespace is minecraft's, as the game reads
it. A chunk in the box that was never generated, or that the generator set
aside part-way, is a refusal -- a `Refused` naming every such chunk -- since
"no spawns" is never the answer for ground that is not there; a chunk that
cannot be read is corruption, its own plain `NBTError`.

### Where is the water, and will it stay?

```python
from heaton_voxel.craft import water_body, contained, starved

body = water_body(save, (x, z))                   # a column: its waterline
body = water_body(save, (x, y, z), within=((x0, z0), (x1, z1)), limit=500_000)
body.cells, body.flowing, body.ice, body.lids     # frozensets of (x, y, z)
body.surface, body.still, body.shore              # shore: dry (x, z) beside it
body.clipped, body.missing                        # where it was cut off, and why
body.columns[(x, z)]                              # a SurfaceProbe, read lazily
body.depth                                        # {(x, z): cells of water}

held = contained(save, plan, spill=fountain_cells)
held.held                                         # nothing out that is not out today
for leak in held.leaks:                           # the plan's
    leak.cell, leak.block, leak.kind, leak.reason, leak.source
held.standing                                     # out already; not the plan's

starved(save, removed, overlay=plan)              # [(x, y, z), ...]: flows cut off
```

`water_body` floods from a seed -- a cell holding water or freeze ice, or
a column, meaning its waterline as `walk_surface` reads it -- through the
six neighbors of each cell, in a fixed order. A cell joins `cells` when it
holds water as the game loads it (`block_facts.holds_water`: water at any
level, a bubble column, kelp, seagrass, anything waterlogged), and freeze
ice (`minecraft:ice` and frosted ice; never packed or blue, which freezing
never writes) joins `ice` where it lies on the body, beside it, or beside
ice that has joined at its own y -- a lid on the same lake, crossed and
not counted. `lids` is the ice with air under it. `flowing` is every cell
of `minecraft:water` that is not a source: flowing and falling water, which
a tick moves, so a moat laid as sources over an air trench is not `still`.
The flood is 3-D on purpose and pools joined under their surfaces are one
body: JeffWorld's big lake takes in the fortress moat beside it, which
meets it under the waterline (1,182 columns of it), and the frozen lake
drains down a one-wide shaft to y=6. Filter `columns` by waterline and
pass `within=` for one surface. `within=` is a rectangle the flood never
leaves (a corner's y is ignored), and `clipped` names the cells past it
the flood would have entered; `missing` names every chunk it reached and
could not read -- `"ungenerated"`, `"partial"` or `"unreadable"` -- since
could not read is never dry. Past `limit` cells of water and ice it is a
`Refused`; a seed that holds neither is a plain `NBTError` naming the cell
and its block. `overlay=` reads a plan over the world (a fill's removed
cells as stone, say), though `columns` reads the world as it stands. On the
legacy fixture's sea it holds 28,948 cells -- 27,894 water, 751 kelp
plants, 141 kelp, 94 seagrass, 64 tall seagrass and 4 waterlogged glow
lichen, all sources -- over 1,280 columns, and names the 9 chunks the
fixture never had; JeffWorld's big lake is 354,213 cells over 38,177
columns, in 1.5 s and 0.7 s more for the columns.

`contained` is whether a plan keeps its water: the game's first step of
spread (`FlowingFluid.spread`, javap, 26.3), run over every cell holding
water within two steps of a plan cell, in the world with the plan over it.
Each `Leak` names the cell water would enter (or the lava it touches),
what stands there, and the water cell it comes from, `source` -- which is
where a seal goes. Its `kind` is `"falls"`, `"spreads"`, `"waterlogs"`,
`"lava"` or `"ungenerated"`, and the rules are the game's:

- water enters a cell only through its wall (`canPassThroughWall`: the two
  collision faces the cells meet on, `block_facts`' `collision`, do not
  cover the face between them), and only a cell that holds no water and
  that water can enter at all (`canHoldAnyFluid` -- the tag
  `washed_away_by_fluids` holds water and lava themselves, which is why
  "holds no water" is asked besides);
- **flowing water never waterlogs anything** -- a container takes a source
  only -- **but two sources do**: a cell two sources reach through their
  walls, over a block that is solid or a source, becomes a source, and a
  dry stair or slab there is waterlogged (`"waterlogs"`). That is the game
  rule `WATER_SOURCE_CONVERSION`, taken as on, its default; off, a
  `"waterlogs"` is a refusal the game would not need;
- water goes down first; where it does, it goes to the sides only with
  three sources beside it, and where it does not, a source goes to the
  sides always and a flow only where there is no water hole under it;
- a flow reaches the sides with one less than its amount, and **falling
  water -- every level from 8 to 15 -- is amount 8**, reaching them with 7;
- a flow the plan feeds is re-read by `getNewLiquid` first, along whole
  chains of flows, and keeps the more of that and what it holds, so a
  source set beside a stale rim of level-7 water lifts the rim and a cell
  four along it that the plan never touched spreads into the air beside
  it; every cell lifted widens the cells looked at by two steps more;
- lava touching water is a leak, and so is a side in a chunk never
  generated or only partly; a chunk that cannot be read raises its own
  `NBTError`, and a plan cell in a chunk not there to read, or past the
  world's top or floor, is a `Refused` naming every such chunk and the
  cells past the world;
- water at the world's floor falls into the void under it, as the game
  lets it, and that write goes nowhere: nothing is reported, and the cell,
  having fallen, goes to its sides only beside three sources.

The game then takes only the sides nearest a way down (`getSpread`, where a
flow beside a flow can even set the nearest without being taken); this
reports every side the rules let water into, so a side reported may be one
the game would not feed this tick -- a refusal it would not need, never a
leak missed. A shape the game offsets by position (bamboo, pointed
dripstone) is read at the origin. `spill=` is world cells water may run
into, carried on through them with what it would hold there, falls and
all: a fountain's spout over its basin, where the first cell past them is
the leak. A leak is in `standing`, not `leaks`, when water already enters
its cell the same way without the plan and the plan puts nothing of its
own there -- the lake laps against that cell today, so a build beside
water that already runs somewhere is not refused for it. Lava the plan
writes where water runs is still the plan's leak, and so is a torch, a
rail or a flower it writes there for the water to wash away; air written
there, or the block standing there written back as it is, changes
nothing. A Builder's water check, `_contain_plan`, asks it of a whole
plan and refuses what it finds or seals it (see "A plan that lets water
out is refused, or sealed", above); `grade`, `level_pad`, `road`,
`basin`, `approach`, `extrude` and `furnish` run it. Where nothing within
two steps of a plan can hold water -- read off the section palettes, a dry
pad's sections of stone, dirt and air -- it answers held without reading
the plan cell by cell, and the world without the plan is read only for the
steps into the cells the plan's water reaches.

So a plan made of the world's own blocks has no `leaks` by construction,
and `contained(save, {})` checks nothing at all: what such a plan tells
you is `standing`, every side water would take from its cells today. Read
that way against JeffWorld's settled water on 2026-10-22, read-only, with
each body's own cells passed back as the plan, the big lake (354,213
cells, 15 to 19 s; its rim of 22,663 cells, under 4), the walled city's
moat (12,513 cells within 16 of its box, 1.2 s) and the frozen lake
(11,110 cells, 0.5 s) stood at 21, 23 and 30 sides, and each of the 74 is
accounted for by the game's own `getSpread`, slope rule and all. 57 are
sides that rule passes over, the over-report above. The other 17 are
cells the game itself will run water into at its next block update there
-- water left beside a cell that was opened, or written, without one: 9
cells of air at y=62, z=0 in the ruined portal beside the big lake, each
beside one of the lake's sources at z=-1 (x=-737, -736, -734 and
-729..-724; six over the portal's blackstone, and x=-736, -734 and -726
over air); the 2 at (-754..-753, 63, -1), beside a bank's falling water
at z=0; (-561, 63, -4), beside the fortress moat's edge at (-560, 63, -4); and
(-518, 63, -31) and (-517, 63, -32), beside a source in the sand bank at
(-517, 63, -31) -- all over the lake; (-270, 66, 238), beside the city
moat's source at (-269, 66, 238); and, over a hole by the frozen lake,
(-350, 45, 221), beside a flow at (-350, 45, 222), and (-348, 45, 222),
beside falling water at (-349, 45, 222). Settling or sealing them is world
work, and nothing was written.

`starved` is what a fill across water cuts off. `removed` is the water
cells a plan takes away, read as the overlay has them or, where it says
nothing, as stone; every flow and every column of falling water beside
what is left is then re-read by the same `getNewLiquid` amounts
`contained` reads -- a source and falling water count 8, a flow its
amount, each only through its wall -- and wherever that gives less than
the cell holds, the cell is starved and the flows it fed are read again,
down to whatever else feeds them or to nothing. A source is never starved:
the game never re-reads one. A stream cut off from its source starves
whole; falling water beside a fill is fed from above, and is not.

## `heaton_voxel.craft.block_facts` and `block_registry` — the game's answers

Both are generated by `tools/probe_block_shapes.py` from the jar, never
edited, and held to `fixtures/block_shapes.json` state by state by
`tests/test_block_facts.py` and `tests/test_block_registry.py`. Omitted
properties take their defaults throughout.

```python
from heaton_voxel.craft.block_facts import (
    facts, holds_fluid, holds_water, in_heightmap, known)
from heaton_voxel.craft.block_registry import (
    BLOCK_IDS, FALLS, FLUIDS, WATER_FLUIDS, properties_of)

f = facts("minecraft:oak_slab", {"type": "bottom"})
f.holds("up"), f.solid, f.conductor  # what hangs on it, what power crosses it
f.emission, f.dampening              # the light it gives and takes
f.spawn_floor, f.spawn_space         # a floor to spawn on / per-cell room
f.faces                              # six 256-bit masks: what it closes to light
f.enters, f.container, f.takes_source  # water gets in; it waterlogs; a source
f.collision                          # six 256-bit masks: what it shows water
in_heightmap("minecraft:oak_leaves", leaves=False)  # False: MBNL passes leaves
holds_fluid("minecraft:sea_pickle")                 # True: waterlogged by default
holds_water("minecraft:lava")                       # False: fluid, not water
known("minecraft:quartz_wall")                      # False: the game has no such block
properties_of("minecraft:stone")                    # {}; None for an id it lacks
```

`facts` answers `CUBE` for an id it has never heard of, the safe guess for a
wall, while the game loads that id as air; `known` is how a caller tells the
two apart. The light flood and `spawnable` ask it, and take an unknown id as
the air the game makes of it; the walk checks keep it solid. `in_heightmap`
is the block test of the two `blocks_motion_in_heightmap` tags; the game also
stops MOTION_BLOCKING and MOTION_BLOCKING_NO_LEAVES at any state holding
fluid, so a reader adds `holds_fluid` the way the game does, and an unknown
block counts, so the unknown is solid. Snow is in neither tag: the
motion-blocking maps sit under it. `holds_fluid` is true of the seven
`FLUIDS` and of a waterlogged state, an omitted `waterlogged` taking the
block's default (a bare coral is wet), and never of a block that has no such
property. `spawn_floor` is the game's `isValidSpawn` for a zombie or a wither
skeleton (so magma counts) and `spawn_space` its `isValidEmptySpawnBlock` for
a zombie, both asked with the game's own block tags bound, so a rail or a
fire is no room, and nor is a signal source (a lever, a button, a redstone
torch) or anything that hurts a zombie (a wither rose, a berry bush, a
cactus, powder snow). `spawn_space` is the per-cell half of the game's test
only: a spawn also needs the mob's box clear of collision (`noCollision` on
`getSpawnAABB`), which no one state answers, so a fence, a wall, a chest or a
bottom slab passes it. Only stairs, slabs, snow layers, farmland, path,
shelves and a handful of other shaped blocks close faces to light; two
neighbors close the face between them exactly when their masks together make
`FULL_FACE`.

How water moves through a state is the game's `FlowingFluid`, asked the same
way. `enters` is its `canHoldAnyFluid`: a `LiquidBlockContainer`, or a block
tagged `washed_away_by_fluids` -- air, plants, torches, rails, carpets, snow
layers, and water and lava themselves, so a flow beside a flow is somewhere
water goes; sugar cane, cactus and doors are in neither half. `container` is
a `LiquidBlockContainer`, which water waterlogs rather than washes away, and
`takes_source` whether it takes a water source (`canPlaceLiquid`: a bottom
or top slab does, a double one does not); no container takes flowing water,
which the probe refuses to write otherwise. `collision` is the six faces of
the state's collision shape that water's wall test (`canPassThroughWall`)
sees, laid out as `faces` is, and water passes between two cells exactly
when the two masks they show each other together are not `FULL_FACE` -- held
to the game for every pair of distinct collision shapes when the table was
written. A shape that runs past a face shows it nothing (a fence's top), a
sixteenth counts only where the shape covers it whole (a chain stands on
thirty-seconds), and a block the game offsets by position (bamboo, pointed
dripstone) is read at the origin. A plain cube is a wall all over
(`FULL_FACES`), and so is an id the game does not have. `holds_water` is
`holds_fluid` without lava: the six `WATER_FLUIDS` and a waterlogged state,
the game's `#water` fluid tag, a flow as much as a source. It is the water
test survey and render read: what `classify` calls water, what the column
reader wades through, what `render_area` darkens by depth, and what waters
sugar cane in `unsupported` (a retired name read as the block it became, so
an old chunk's waterlogged `chain` is wet). The builders' own tests for
water -- a railway's, a session's shaped ground, `clear_vegetation`'s --
read `holds_fluid`, which counts lava too; those that follow water read
`holds_water`: a grade's `beside_drained`, the split check
(`_split_faults`), and the water a furniture piece or an extrusion holds,
which the water check follows. What those two refuse of fluid -- lava, and
flowing water, which the game re-reads from its sources -- is
`_fluid_unlaid`, which reads both.

`block_registry` is every block id the game has (`BLOCK_IDS`), the values
each property takes (`properties_of`, values ascending, as numbers where
they are numbers), the `Fallable` blocks (`FALLS`) and the blocks every
state of which holds fluid (`FLUIDS`) or water (`WATER_FLUIDS`: every one
but lava). It is the judged tier's: the lower
tier writes whatever it is given, by design, and `block_defaults` (which the
26.3 palette codec needs) is a different generator's table, held to this
one by test so the two cannot be regenerated from different games.

## `heaton_voxel.craft.blockinfo` — what a block is

The game refuses nothing: an id it does not know loads as air, and a
property or value a block does not take loads as the block's default, with a
line in a log nobody reads the only trace. `check_state` is the refusal the
game never makes, and every `Builder` write runs it (strict writes, under
`craft.build`). The categories beside it are a builder's judgments, each a
hand copy of the game's own block tags that `tests/test_blockinfo.py` holds
to the tags the probe vendors.

```python
from heaton_voxel.craft import IllegalBlock, check_state, material_of
from heaton_voxel.craft.blockinfo import RENAMED, subsoil_of

check_state("minecraft:oak_stairs[facing=east]")   # the state, exactly as given
check_state("create:gearbox", allow=("create:",))  # a modded namespace, unchecked
try:
    check_state("minecraft:chain[axis=y]")
except IllegalBlock as refused:
    refused.suggestions                            # ("minecraft:iron_chain",)
RENAMED["minecraft:chain"]                         # ("minecraft:iron_chain", 4541)
material_of("minecraft:stone_brick_stairs")        # ("minecraft:stone_bricks", "stairs")
subsoil_of("minecraft:grass_block")                # "minecraft:dirt"; None for an ore
```

`check_state` takes a name, a `BlockState` or a `(name, properties)` pair and
returns the state as given, defaults unfilled: a property left out is legal,
because the game fills it. It raises `IllegalBlock`, a `Refused`, for an id
the game lacks -- naming its replacement if the game renamed it
(`RENAMED`, the two block renames since 1.18), or else the cuts its block
does come in and the nearest real spellings, all in `.suggestions` -- and
for every property the block does not take and every value outside one,
each a fault. A value is text: `waterlogged=True` is refused. A name with no
namespace is refused too, naming the id it means (the game would read `stone`
as `minecraft:stone`, but every table here keys by the whole id). `allow=`
takes exact ids or namespaces ending in `":"`, and raises `NBTError` for an
entry no id could match (uppercase, a space, a state's `[...]`). `is_block`
and `is_air` (the game's `air` tag: cave air is air) answer by name, `falls`
is the game's `Fallable` blocks less the speleothems, which hang, and
`holds_fluid`, `holds_water` (also `craft.holds_water`: water at any level,
never lava), `properties_of` and `FLUIDS` are the generated tables' own,
re-exported. `AIR_BLOCKS` is `blocks.AIR_BLOCKS`, the deterministic tier's
own set, which `tests/test_block_registry.py` holds to the jar's `air` tag.

| category | the game's tags it copies | named choices on top |
| --- | --- | --- |
| `is_tree` | `logs`, `leaves`, `saplings`, `wart_blocks` (by whole suffix, so a modded log counts) | huge mushrooms, the bee nest and a pale oak's creaking heart are trees; a potted sapling, a bamboo shoot and a shroomlight are not |
| `is_vegetation` | `replaceable_by_trees` and `small_flowers`, less the leaves (trees, so nothing is both) | water and seagrass are left; berry bushes, cane, wild pumpkins and melons, small mushrooms and lying snow are cleared |
| `subsoil_of` / `is_natural_ground` | `dirt`, `sand`, `base_stone_overworld`, `base_stone_nether`, `substrate_overworld`, `terracotta` | suspicious sand is no ground; gravel, clay, both sandstones, snow block, calcite, dripstone, soul sand and soil and end stone are; grass, podzol and mycelium lie on dirt |
| `material_of` | `stairs`, `slabs`, `walls` (by whole suffix) | bamboo cuts are planks'; the petrified oak slab is oak planks' |
| `form_of` | `material_of`'s, plus the panes, which no tag lists: the ids ending `_pane` | none: each pane's stem leads to its glass |

`form_of(name)` is `(base, form)`, the form one of `FORMS` (`"block"`,
`"stairs"`, `"slab"`, `"wall"`, `"pane"`): what a `craft.style.Material`
reads a family by. `cut_shapes()` is every stair, slab and wall the game
has, mapped to `material_of`'s block in sorted id order (230 in 26.3; a
fresh dict on every call), and no modded cut, whose stem is only a guess:
what `render.ColorMap` draws a cut in the color of, and what the Unity
app's block palette generator bakes into the app.

The builders judge by these: `clear_vegetation` fells and strips what
`is_tree` and `is_vegetation` say, `smooth_terrain` raises a column with
`subsoil_of`, `Vault.read` takes a vault's stone by `material_of` (stone
bricks under a stone vault are its wall, not its roof), and a railway's
water and falling supports, like `unsupported`'s sand over a hole, are
`holds_fluid` and `falls`.

Every category reads a retired name as the block it became, since a chunk
saved before the rename still holds it; only `is_block` and `check_state` say
no to one. A choice that stops excusing anything -- the game adds a log to
its tags, or a stair whose name leads to its block -- fails the test as
stale, and the test keeps its own list of the choices, so an edit to a table
here that changes a verdict fails by name too.

## `heaton_voxel.craft.geometry` — one compass, round shapes, lines, profiles, frames

The shape of things, with no world in it: nothing here reads or writes a
save. Each piece was promoted out of builds that kept their own copies, and
where the copies differed the difference is a **named choice** at the call,
never a second rule.

```python
from heaton_voxel.craft.geometry import (
    CLOCKWISE, COMPASS, OPPOSITE, ROAD_WINDOW, ROUNDINGS, WALL_WINDOW, Frame,
    axis_of, disc, heading_of, line, ring, smoothed_profile, turn)

COMPASS["east"]                    # (1, 0): +x is east and +z is south, the game's axes
turn("north"), turn("north", -1)   # "east", "west": clockwise seen from above
OPPOSITE["west"], axis_of("west")  # "east", "x"
heading_of(5, -2)                  # "east": the word of the larger component
heading_of(3, -3)                  # "north": an exact diagonal breaks toward z...
heading_of(3, -3, ties="x")        # "east": ...unless the call names x
disc(4, center=(120, -40))         # a filled circle, 69 cells, sorted by (x, z)
ring(4, center=(120, -40))         # its rim, 32 cells in order around the center
disc(4, rounding="inside")         # 49 cells: the park's rule
line([(0, 0), (6, 3), (6, 9)])     # the cells road's centerline crosses
smoothed_profile(ground, window=ROAD_WINDOW)          # the heights a road rides at
smoothed_profile(ground, window=WALL_WINDOW, anchors={0: 70})   # a wall's, pinned
smoothed_profile(rim, window=0, max_step=1, closed=True)        # a loop's, clamped
```

`COMPASS` keeps the order the builders' tables always had, the z pair and
then the x pair, since refusals print it; it and `OPPOSITE` are read-only.
`turn` and `axis_of` raise `NBTError` for anything but the four words, and
`heading_of` for `(0, 0)` -- no step has no heading -- and for a `ties`
other than `"x"` or `"z"`. The default, `"z"`, is `examples/castle.py`'s
and the Roman city's rule; `"x"` is the secret lab's.

**Round shapes have one rule.** A cell is in a `disc(r)` when its center
lies within `r` plus the rounding's slack of the shape's center, tested in
exact integers as `16·(dx² + dz²) <= (4r + slack)²`, the slack in quarter
blocks (`ROUNDINGS`): `"inside"` (0) is `dx² + dz² <= r²`, the park's;
`"quarter"` (1) is `<= r² + r/2`, the secret lab's; `"half"` (2, the
default) is `<= r² + r`, the castle's and the Roman city's. `ring(r)` is
`disc(r)` less `disc(r - 1)`, ordered by angle as `atan2(dz, dx)` ascends
-- just north of due west, then clockwise seen from above, due west last --
computed exactly, so every other cell is a merlon marching around a tower
(`Builder.battlement(ring=)` crowns one so); `ring(0)` and `disc(0)` are the
center alone. A ring and the disc inside it
tile the disc, the ring holds every disc cell with a side neighbor outside
(it encloses), and every ring cell touches two others. A radius must be a
non-negative int and a center a pair of ints; an unknown rounding is
refused naming `ROUNDINGS`. `tests/test_geometry.py` holds all three
roundings to the tools' own functions and the default to the castle's
`disc_offsets` and `ring_offsets`, cell for cell and in order.

**A line is road's rule.** `line(points)` walks each segment in
`max(|dx|, |dz|)` steps, landing step `i` on
`(ax + round(dx·i/n), az + round(dz·i/n))` -- half to even, so
`line([(0, 0), (2, 1)])` is `((0, 0), (1, 0), (2, 1))` and its reverse
`((2, 1), (1, 1), (0, 0))`: a line is not its reverse. Consecutive cells
always touch, a waypoint shared by two segments is kept once, and a
segment of no length is skipped. It raises `NBTError` for a waypoint that
is not a pair of ints and when no two consecutive waypoints differ.
`Builder.road` walks it, and no other module of the package keeps a copy,
so anything laid along a road's waypoints follows the road cell for cell.
A road holds its waypoints to the same rule, in the same words, before it
reads or writes anything: a waypoint that is not a pair of ints -- a
float, even a whole one, or a bool -- is refused by name rather than
rounded into a road that ends somewhere it was not asked to go. A road
whose waypoints are all one cell keeps its own refusal, "a road needs at
least two distinct waypoints".

**A profile is road's smoothing.** `smoothed_profile(heights, *, window,
max_step=1, anchors=None, closed=False)` takes the ground along a path, one
int per cell, and returns a tuple of ints. Each height becomes the mean of
the heights within `window` cells of it, `round(sum / count)` -- half to
even, the float expression road always used -- clipped at the ends of an
open profile, and wrapped round a closed one (`closed=True`, a loop whose
last cell lies beside its first), where a cell is counted once however wide
the window, so a window as long as the loop gives every cell the loop's
mean. `anchors` (`{index: y}`) are pinned over the means; then every cell
is lowered to the lower envelope `min over j of (p[j] + max_step·|i - j|)`
(round the loop when closed), and the anchors are pinned again.
`max_step=None` skips the clamp, and 0 lowers the whole profile to its
lowest pinned mean, a low anchor's included -- the envelope does not stop
at an anchor -- leaving each anchor above that standing alone; `window=0`
takes the heights as they are. The window is the
named choice: `ROAD_WINDOW` (3) is road's, from the first town's road, and
`WALL_WINDOW` (5) the JeffWorld Roman wall's
(`tools/roman_city/jw_groundwork.py`); the secret lab's wall averaged
nothing, `window=0`. One sweep each way reaches the envelope exactly (twice
round when closed), so the three or four forward-and-back passes those
copies ran were one pass and repeats that moved nothing;
`tests/test_geometry.py` holds the rule to road's loop as it stood, over
lines that double back and anchors on every visit, to the two walls'
functions compiled out of the tools' own source, and to the envelope's
definition by brute force. It only lowers: nothing ends above its mean or
its anchor, so a low anchor pulls its neighbors down to within `max_step`
of it and a high one stays a step, never ramped up to, as road has always
left one where it meets a terrace above the ground. Every other step is
within `max_step`, the last cell to the first too when closed. An empty
profile is `()`; a height that is not an int (a bool is not one), a bad
`window`, `max_step` or `closed`, `anchors` that are not a mapping, and an
anchor outside the profile or not a whole height are `NBTError`, naming the
value.

`Builder.road` rides `smoothed_profile(window=ROAD_WINDOW)` over the ground
under its centerline. Its `anchors` are keyed by cell, `{(x, z): y}`: one
pins every visit the centerline makes to its cell (a line that doubles back
passes one twice), one off the line pins nothing, and a height that is not
an int -- a float, a whole one too, or a bool, which was once a road
anchored at y = 1 -- is refused naming its cell before anything is read.
The pin is the profile's, one height per centerline cell, and each swath
column is cut to the first stretch whose square reaches it. On a road
wider than 1 the stretch before every centerline cell but the first
already reaches it, so the anchored cell's own column stands at an earlier
stretch's height, and the anchor's height is laid on the columns its
stretch reaches first: `width // 2` cells further along a straight road
(over flat ground at 63, `road([(0, 4), (12, 4)], anchors={(6, 4): 66})`
stands (7, 4) at 66 and leaves (6, 4) at 63). At `width=1` every column is
its own cell's.

### `Frame`: a component placed facing some way

```python
frame = Frame((100, 64, 200), "west")          # out points west; mirrored=False
frame.cell(2, 0, 1)                            # (99, 64, 202): local (across, up, out) -> world
frame.local(99, 64, 202)                       # (2, 0, 1)
frame.step(1, 0), frame.heading("south")       # (0, 1), "west": an offset, a word, turned
frame.box((0, 0, 0), (4, 3, 6))                # ((94, 64, 200), (100, 67, 204))
frame.state("minecraft:oak_stairs[facing=north]")   # oak_stairs[facing=east]
frame.unstate(state)                           # a world state in the frame's own terms
inner.within(outer)                            # the one frame that is inner, then outer
```

The identity is `facing="south"`: across is +x, up is +y, out is +z, so a
schematic's own coordinates are local ones. `facing` is where out points,
and seen from outside the face +across runs to the viewer's right;
`mirrored=True` negates across before the turn, changing hands. `quarters`
is the clockwise turn from south (south 0, west 1, north 2, east 3). The
arithmetic is exact -- `cell`, `step`, `local` and `box` take ints and
refuse anything else, a bool included, with `NBTError` -- and states
turn with the cells by the schematic vocabulary --
`properties_mirrored(..., "x")` when mirrored, then
`properties_rotated_cw` once per quarter, each stage handing its input back
when nothing changes -- so a frame puts the same states in the same places,
property order included, as `Schematic.flipped("x").rotated(90 * quarters)`.
`state` and `unstate` take a name, a `BlockState` or a `(name, properties)`
pair and invert each other for every state the game has, in all eight
orientations. In a frame that turns, a state with a direction-looking
property the vocabulary does not cover (`directional_leftovers`) is
`craft.Refused`, never guessed at; the identity turns nothing and refuses
nothing. Frames are immutable values, equal when origin, facing and hand
are, and a copy, a deep copy or a pickle of one is an equal frame; a bad
origin, facing or `mirrored` is an `NBTError`. `Frame` is also importable
from `heaton_voxel.craft`. A Builder writes through one with
`b.framed(frame)`, and `b.stamp(rotate=, mirror=)` lands a schematic as
one would (see [a component through a frame](#a-component-through-a-frame-and-a-schematic-stamped-whole)).

## `heaton_voxel.craft.style` — mixes, families and palettes

The materials a builder names, as values with no world in them: a surface
mixed from several blocks, a family of blocks cut from one stone, and the
set of materials a style builds with. Each was written again by every
build script that needed it (`mixpick` twice, `weighted` twice, the Roman
and lab families by hand); these are the one copy, held to the game's own
block list.

```python
from heaton_voxel.craft import FROM_PALETTE, Material, Mix, Palette
from heaton_voxel.craft.style import DEFAULT_PALETTE, ROLES, ROAD_MIX

cobbles = Mix([("minecraft:cobblestone", 8), ("minecraft:andesite", 5),
               ("minecraft:mossy_cobblestone", 1)], seed=0, clump=3)
cobbles.pick(10, 64, -3)             # one of the entries' blocks, the same every time
Material("minecraft:brick_stairs").forms
# {"block": "minecraft:bricks", "stairs": "minecraft:brick_stairs",
#  "slab": "minecraft:brick_slab", "wall": "minecraft:brick_wall"}
Material("minecraft:glass").form("pane")         # "minecraft:glass_pane"
Material("minecraft:smooth_quartz").form("wall") # IllegalBlock: no such wall
roman = Palette(roof="minecraft:granite", gable="minecraft:smooth_sandstone")
roman["roof"].form("stairs")         # "minecraft:granite_stairs"
Palette()["road"] is ROAD_MIX        # True: the default palette is the defaults
```

**A `Mix` is picked by position.** `Mix(entries, *, seed=0, clump=1)` takes
`(block, weight)` pairs -- a block is an id or a `BlockState`, a weight a
whole number of at least 1 -- and `pick(x, y, z)` gives each cell one
entry's block, as given, in proportion to the weights. The pick is a pure
function of the Mix and the cell: `craft.noise.cell_hash` of the cell,
keyed on the seed folded with a CRC of the entries (each as its state,
properties sorted, and its weight), then scaled exactly onto the running
weights. No session, call order or random stream touches it, so the same
cell picks the same block from any Builder in any order on any run, and
two different mixes on one seed are independent fields rather than one
pattern in two materials (one mix spelled two ways is one field).
`clump=n` (1 to 1024) picks whole patches about `n` blocks across,
three-dimensional, so a wall clumps as a floor does: the proportions hold,
and what changes is the picture -- at 1 a neighbor matches about as often
as two independent dice (Σp², television static), at 3 about two in three
do and about 1% of cells stand alone. A plain list of pairs, as `ROAD_MIX`,
`LANE_MIX` and `PLAZA_MIX` are, is still a list: a Builder draws it from
its `rng` in the order its writes come, so one changed call re-rolls every
surface after it. A Builder's `set`, `fill`, `level_pad`, `road` and `pave`
pick a Mix at every cell they write (see the Builder's "A `Mix` is written
where it is picked"). A Mix checks syntax only -- no entries, a weight that is
not a whole number of at least 1, or a clump out of range is `NBTError`; an
entry that is not a block (a `Material` included) or a seed that is not a
whole number is `TypeError`; text that does not parse is the parser's
`ValueError` -- and leaves whether the game has a block to the writer, under
its own `allow=`. `pick` takes whole numbers only (`TypeError` for a float).
Mixes are immutable and hashable, and equal when they pick alike: the same
states in the same order however spelled (an id and its `BlockState`,
properties in any order), the same weights and clump, and seeds equal in
the 32 bits the key is made from; equal Mixes hash alike.

**A `Material` is a family.** `Material(name)` is named by any block of the
family or by its stem: `brick_stairs`, `bricks` and the stem `brick` give
the bricks; `quartz_stairs` and the stem `quartz` the quartz block;
`glass_pane` the glass; the oak stem the oak planks. A stem must lead to a
family's block: `oak_stair`, which the stem rule leads to `oak_stairs`, is
that id a letter short and is `IllegalBlock`, suggesting it. `.base` is
the block the family is named for -- the full block its forms are cut
from, or, for a block that is no form of another (a fence, a door,
calcite), that block itself, a family of one; `.forms` maps each form the
game has, of
`blockinfo.FORMS` (`"block"`, `"stairs"`, `"slab"`, `"wall"`, `"pane"`), to
its id, the block first. A cut is the one `material_of` reads, with its
named exceptions (the bamboo stairs and slab are the bamboo planks', oak
planks' slab is the oak slab and never the petrified one), and a pane the
`_pane` id its stem leads to (`blockinfo.form_of` answers which form of
which block any id is). `.form(f)` is that form's id; a form the family
lacks is `IllegalBlock`, naming the forms it does come in, the family
whose form the conventional id is (the bamboo plant's stairs are its
planks') and, in `.suggestions`, the nearest real ids of that form. `.has(f)`
says whether it exists. A form name not in `FORMS` is `NBTError` from
both, never a quiet no. A name with a state is `NBTError` (a Material is
named by an id), and a retired or invented name is `check_state`'s
`IllegalBlock`. A modded block has no family here; it goes in a block role
as itself. Materials are equal when their bases are.

**A `Palette` is a closed vocabulary.** `ROLES` is its thirteen roles, in
order, each a `Role(kind, default, read_by, mixes, none, forms)`: the
material one of the eight legacy writers reads, and the default that
writer wrote before it read a palette, so `Palette()` (`DEFAULT_PALETTE`) is
those defaults -- a name for them, not a style. A `Builder(palette=)` is
where a palette is read: each writer reads its roles for every material
its caller leaves out (see the Builder's "A palette is where a writer's
materials come from").

| role | kind | default | read by |
| --- | --- | --- | --- |
| `pad` | block, Mix ok | `minecraft:grass_block[snowy=false]` | `level_pad(surface=)` |
| `retaining` | block, Mix ok | `minecraft:stone_bricks` | `level_pad(wall=)` |
| `road` | surface | `ROAD_MIX` (the same list) | `road(mix=)` |
| `paving` | surface | `PLAZA_MIX` (the same list) | `pave(mix=)` |
| `trim` | block or None, Mix ok | `None` | `pave(trim=)` |
| `light` | one block | `minecraft:glowstone` | `pave(light=)`, `light_cells(light=)` |
| `plinth` | block, Mix ok | `minecraft:cobblestone` | `frame_walls(plinth=)` |
| `frame` | block, Mix ok | `minecraft:stripped_spruce_log[axis=y]` | `frame_walls(log=)` |
| `wall` | block, Mix ok | `minecraft:white_terracotta` | `frame_walls(infill=)` |
| `floor` | block, Mix ok | `minecraft:spruce_planks` | `floor(block=)` |
| `roof` | family with stairs and slab | `Material("minecraft:dark_oak_planks")` | `gable_roof(stair=, slab=)` |
| `gable` | block, Mix ok | `minecraft:dark_oak_planks` | `gable_roof(plank=)` |
| `glass` | one block, or a Material read as its pane | `minecraft:glass_pane` | `window(block=)` |

`Palette(**roles)` takes any of them by name and checks each as it is
given, so a palette is refused when it is made, not halfway through a
build: a role `ROLES` lacks is a `TypeError` listing them, with the
nearest (`Palette(rooof=...)` asks whether you meant `roof`) -- a typo
must not fall back to a default; a family role whose family lacks a form
its reader needs is one `IllegalBlock` naming every missing form
(`Palette(roof="minecraft:calcite")`), as is a glass Material with no
pane; and a value of the wrong kind is a `TypeError` -- a Mix where the
reader derives a state from the block (the roof's stairs, the window's
pane) or for a light, whose level is read off the one state it writes; a
list of pairs anywhere but a surface; `None` anywhere but `trim`. An empty
surface list or a bad weight is `NBTError`. Whether the game has a plain
block is left to the Builder that writes it, under its own `allow=`.
`palette[role]` is the value as held -- a family role's id made a
`Material`, everything else as given, so `Palette()["road"] is ROAD_MIX` --
and an unknown role is a `KeyError` naming the nearest. `.roles` is every
role in order, `.replace(**roles)` a checked copy, palettes are equal when
every role is, and a palette may hold a list, so it is not hashable.
`craft` ships no style: two samples, `ROMAN` and `LAB`, read off the Roman
town and the secret lab, are in `examples/palettes.py`, and
`examples/craft_session.py` builds in either (see "A whole session").

`FROM_PALETTE` is the sentinel a writer argument left to the palette
defaults to -- not `None`, which already means "none" to `pave(light=)` and
`pave(trim=)`; a copy or a pickle of it is itself. `ROAD_MIX`, `LANE_MIX`
and `PLAZA_MIX` live here and are the same objects under
`heaton_voxel.craft` and `heaton_voxel.craft.build`.

Where one exact state is read through the plan-entry reader or the
builders' coercion -- `check_state`, a plan overlay, the light tables,
`Builder.place`, the stair, vault and railway rules (`SpiralStair`,
`SwitchbackStair`, `StraightStair`, `Vault`, `Railway`) -- a Mix or a
Material is a `TypeError` saying what it is and what to pass instead
(`.pick(x, y, z)`, `.form(...)`), rather than a parser's complaint naming
neither. The rule objects that came after them -- `Roof`, `Facade`,
`Opening`, `battlement`'s blocks and a `Piece` -- take a Material as the
form each role needs and a Mix in their whole-block roles, picked at each
world cell as the plan is made (see each one's section).

### `heaton_voxel.craft.noise` — dice keyed on a cell

```python
from heaton_voxel.craft.noise import cell_hash, patch

cell_hash(-599, 0, -53, 4211)        # 4291811685: tools/lake/noise.py's _hash(-599, -53, 4211)
patch(10, 64, -3, 3, 0)              # (3, 21, -1): the cube whose patch the cell is in
```

`cell_hash(x, y, z, seed)` is a 32-bit roll: the coordinates and seed each
multiplied by an odd constant, folded, and put through murmur3's
finalizer, in whole-number arithmetic with no `hash()` and no `random`, so
it is the same on every run, platform and Python. At `y = 0` it is the
lake build's own `_hash` bit for bit, which `tests/test_noise.py` holds it
to by loading `tools/lake/noise.py` by path, with golden values besides.
`patch(x, y, z, size, seed)` is a jittered grid's nearest feature (Worley
F1): space cut into cubes `size` across by floor division, each cube's
feature placed in it by its own roll, the cell given the cube of the 27
around its own whose feature is nearest, ties to the least `(distance²,
cy, cz, cx)`. `size` 1 is the cell itself; a size that is not a whole
number from 1 to 1024 is `NBTError`, as a Mix's `clump` is, and a
coordinate that is not a whole number `TypeError`. Rolling once per patch
clumps a picture and keeps its weights; thresholding smooth noise at the
weights would not (value noise piles up mid-range, so a mix's rare ends
vanish). The module is a leaf: it imports nothing from the judged tier,
and from the lower tier only `NBTError`.

### `heaton_voxel.craft.field` — distance fields over columns

```python
from heaton_voxel.craft.field import blur, chessboard, distance_to_edge, edt

lake = {(x, z) for x in range(-4, 5) for z in range(-4, 5)}
distance_to_edge(lake)[(0, 0)]       # 5.0: the nearest dry column is five away
distance_to_edge(lake)[(-4, 0)]      # 1.0: on the shore, the box's edge included
chessboard(lake)[(3, 3)]             # 2: a king walks out in two steps
```

Both fields take any iterable of `(x, z)` pairs of ints and give every cell
of the set its distance to the nearest column **not** in it, center to
center, as a dict in sorted `(x, z)` order: 1 on the edge, never 0, and a
hole is outside, so it is a shore too. `distance_to_edge` is Euclidean and
exact (a float): the lake bed's metric, since a chamfer approximation put
diamond ridges down the lake that showed on the floor as terraces, and
`Builder.basin`'s, which cuts its default bed `round(slope * distance)`
deep. A
cell whose only outside neighbor is a corner away is `sqrt(2)`. Each
4-connected piece is measured on its own bounding box padded by a column
all round -- `lakeplan.build_floor`'s lesson that a column on the box's
edge would otherwise have no shore on that side -- which is exact, since
the nearest outside column always touches the cell's own piece (another
piece's column in the box is never nearer, so each piece's field is the
one it has alone), and costs two far-apart lakes what each costs. The
field is 1-Lipschitz between side neighbors, so `round(s * distance)`
never steps two blocks for a slope `s <= 1` (at 1 neither side can sit on
a half, since the square root of a whole number never does);
`tests/test_field.py` finds a two-block step at 1.05. `chessboard` is how
many king steps it is out (an int): a hip roof's metric --
`Roof(rings="square")` over a footprint rises by it, less one -- since it
puts a straight 45-degree valley at a reentrant corner, where the
Euclidean distance's floor puts a bump; on a rectangle it is `min(x - x0,
x1 - x, z - z0, z1 - z) + 1`, the rectangle's hip ring plus one. It is a
breadth-first walk by king moves, exact because a shortest king walk to
the nearest outside column stays inside until its last step. An empty set,
or a cell that is not a pair of ints (a bool included), is `NBTError`.

`edt(mask, w, h)` (squared distances from each True cell of a row-major
grid to the nearest False one, Felzenszwalb and Huttenlocher's separable
transform) and `blur(field, mask, w, h, passes, radius=1)` (a box blur over
the masked cells with the outside held at 0) are the lake build's own,
promoted verbatim from `tools/lake/field.py` with their docstrings;
`tests/test_field.py` loads that file by path and holds the two to it node
for node and output for output. A grid they are handed is measured inside
itself, so it must be padded, which `distance_to_edge` does. The module is
a leaf: it imports nothing from the judged tier, and from the lower tier
only `NBTError`.

---

## Writing a new example

Follow `examples/gold_box.py` (writes) or `examples/world_map.py` (read-only):

```python
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

- take the world path as a positional argument
- refuse, with a clear message, if `world_is_open()` and you intend to write
- offer `--dry-run` for anything destructive
- report what you could *not* do — incomplete fills, unreadable chunks,
  unmapped blocks — rather than quietly succeeding
- build with `save.place_block()` rather than `set_block()` — beds and
  doors are two blocks, and connections are stored state; placement
  handles both. Bulk-built areas get `save.connect_blocks()` instead
- call `save.reset_lighting()` over any area whose blocks you changed —
  stored light is baked, and a torch in a new room otherwise lights only
  itself
- call `release()` (or let `fill` do it) on any pass over many chunks
