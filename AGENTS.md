# Working in this repo

Heaton Voxel — a from-scratch Minecraft (Java Edition) save toolkit,
distributed as the `heaton-voxel` package (import name `heaton_voxel`).
**Two tiers.** The lower tier is *deterministic editing*: for every question
it answers (what bytes encode this chunk, what state a block update derives)
there is one correct answer, the C# twin must agree to the bit, and the
conformance corpus enforces it. The upper tier, `heaton_voxel.craft`, is
*judged construction*: palettes, roofs, road mixes, block colors — game
knowledge and taste, where answers are choices. Choices get renders and
judgment, not corpus artifacts, so `craft` is **Python-only by policy** and
outside the port by definition. The dependency arrow points down and only
down; `tests/test_architecture.py` fails the build if the lower tier ever
imports `craft`, or the top-level namespace ever re-exports it.

The deterministic tier — three layers, each built on the one below, plus
interchange and semantics:

| module | what it owns |
| --- | --- |
| `heaton_voxel/nbt.py` | the NBT and Anvil region **file formats**; `snbt.py` beside it is NBT's **text form**, `parse_snbt`/`to_snbt`, read as the game's `TagParser` reads it and written as `Tag.toString()` writes it, held case by case to the game's own answers in `fixtures/snbt.json` (`tools/probe_snbt.py` asks them), with three refusals it names: `\N{...}`, nesting past 512, a non-ASCII `uuid()` digit |
| `heaton_voxel/blocks.py` | one chunk's **contents** — paletted, bit-packed blocks, biomes, heightmaps, stored light. `block_defaults.py` beside it is the **generated** default-state table (`tools/generate_block_defaults.py`) the 26.3 palette codec needs, and `heightmap_blocks.py` the **generated** table (`tools/generate_heightmap_blocks.py`, from `craft`'s probe output) of what the game's heightmaps count, so `rebuild_heightmaps` writes all four maps exactly. A chunk Minecraft 1.13 to 1.17 saved reads through the same accessor, read-only: everything under `Level`, y 0..255 whatever it stores, the sections it left out read as air, before 1.16 its indices and heightmaps run on across longs, before 1.15 a biome a column, before 1.14 its finished stage `postprocessed` or `fullchunk`, before 1.17 its entities in its own `Level`, its blocks under the names it stored them by, and its numbered biomes named as the game's own upgrade names them -- `legacy_biomes.py` beside it, **generated** with its C# twin by `tools/probe_legacy_chunks.py`, which also writes `fixtures/legacy/upgraded.nbt` and `fixtures/legacy/older.nbt`, the game's upgrade of the 1.17 fixture and of the 1.13 to 1.16 ones (OldWorld's 1.15.2 and 1.16.5 chunks, and chunks it stamps as 1.13 and 1.14 saved them, each palette state upgraded alone too), that `tests/test_legacy.py` holds the reader to cell by cell |
| `heaton_voxel/world/` | the whole **save** — a package, one concern per file: `save.py` (`MinecraftSave` core) with mixins `player.py`, `settings.py`, `editing.py`, `aftercare.py`, `interchange.py`, `history.py`, plus `dimension.py`, `results.py`, `cli.py`. The layout mirrors the C# port's partial classes file-for-file (PORTING.md has the map). `save.begin_edit` is undo: a record of every file the save holds or opens before `complete()`, as it stood and as it was left, so `undo`/`redo` take any writer's edit back whole, before a save or after one, in memory only and never a backup (`tests/test_history.py` holds the writers to it, those that came after it included); a backup is `save(before_write=)`, which offers every file a save is about to replace, create or remove before touching it and stops the save on an exception, each file written having been offered first (both twins, `save_hook.json`) -- the Unity app's backups are built on it. `save.create_chunks` builds finished chunks of air where the world has none (`blocks.empty_chunk`, held key for key to both fixtures' game-written chunks, stamped with the world's DataVersion or 26.3's, refusing a world older than 26.2), leaving every chunk the world holds as it stands. A save in the layout before 26.1 opens read-only (`save.layout == "old"`, `ReadOnlySave` from every write), its settings and player read from `level.dat` and `playerdata/` as the game's own `FileFixerUpper` would move them -- `tools/probe_old_layout.py` asks it, `fixtures/old_layout.json` is its answer for five worlds from 1.13.2 to 4671, and `tests/test_old_layout.py` holds every reader to it |
| `heaton_voxel/schem/` | **interchange** — Sponge `.schem` files (v2 + v3), vanilla structure `.nbt` (`structure.py`, held to the game's own `StructureTemplate` through `tools/probe_structures.py`'s `fixtures/structures.json`) and Litematica `.litematic` (`litematic.py`, held to litemapy by `tools/check_litematic.py` and `fixtures/litematic/`), one `Schematic` for all three, a cell a structure or litematic leaves out read as `structure_void`, which paste leaves as the world has it; also a package: `schematic.py`, `transforms.py` (the direction vocabulary), `copy.py` (world↔schematic), `support.py`, `results.py`, `cli.py` |
| `heaton_voxel/connect.py` | **derived states** — `save.connect_blocks`: fence/wall/pane/bar connections, gate `in_wall`, stair corner shapes, as a vanilla block update would set them (validated: 0 disagreements in 22,616 vanilla blocks). `wall_cover.py` beside it is the **generated** table (`tools/generate_wall_cover.py`) of what every block state's underside shows a wall below it, which is how a wall's sides and post are derived, and `connect_faces.py` the **generated** table (`tools/generate_connect_faces.py`) of which sides of every state are full, which is what any of them joins |
| `heaton_voxel/place.py` | **placement semantics** — `save.place_block`: like placing an item, not writing a state — beds/doors/tall plants get both halves atomically, connections settle on placed cells and neighbors, a water source waterlogs and flowing water does not (the game's `getStateForPlacement`; corals and the conduit also take falling water, the small dripleaf any water, the light block and a double slab none, and a campfire placed into water is out) |

The judged tier — `heaton_voxel/craft/`, for AI building sessions and any
human driving a REPL. The Unity app, which lives in a repo of its own, never
imports it, but its generators bake `craft.colors` (and
`craft.biomes.FREEZING_BIOMES`, `craft.blockinfo.cut_shapes()`, and the
block list off `craft.block_registry` and `block_defaults`) into the app's
source, through public names only -- the app's own tests hold its
generators to that, and `tests/test_architecture.py`'s `DownstreamApiTests`
holds the names to existing here:

| module | what it owns |
| --- | --- |
| `heaton_voxel/craft/build/` | **construction** — a package (its docstring tables where every default came from: the first town's materials, the JeffWorld nave's vault and lamps, the Stronghold link's boosters — builds, or choices made with them, never the game's rules), one family per file with each rule beside its writer: `stairs.py` (`Ladder` beside the stairs, named by the wall it hangs on), `vault.py`, `railway.py`, `lighting.py`, `site.py`, `water.py` (`WaterMixin`: `basin` and its `BasinResult`, below), `planting.py` (`PlantingMixin`: `scatter`, with `Tree` and `ScatterResult`, below), `approach.py` (`ApproachMixin`: `approach`, with `ApproachResult`, below), `extrude.py` (`ExtrudeMixin`: `extrude`, with `CrossSection`, `Merlon` and `ExtrudeResult`, below), `shells.py`, `walls.py` (`Wall`, where a run stands -- its along coordinate the world's own x or z, its rows, stories and thickness, `Wall.ring` the four closing a rectangle and `Wall.perimeter` the corner path its parapet and a battlement share; `Facade`, the rule its face follows -- corner, pier and bay columns by the congruence `(along - phase) % pitch`, plinth, band and head courses, a window in every bay of every story -- held cell for cell in `tests/test_walls.py` to the secret lab's HQ and main hall, its stables as they were meant, the Roman insula's courses and the m4 insula's mass and hollow; `Opening`, a rect, a round one by geometry's disc, or an arch springing from its jambs on a vault's profile, round or pointed; `Footprint`, a plan that is no rectangle, rectangles joined and others taken out, its walls the runs of its outline, named by the way they face, `shell(footprint=)` building on one; and what crowns a wall: `merlon_offsets`, the castle example's rule promoted and held equal to its copy, and `battlement`), `elevation.py` (`Elevation`, a wall's face as `Band`s bottom up, each some rows, a `Rhythm` across -- named columns from the wall's low end, added, repeated, reversed and mirrored -- and what each role holds: a block, a `Glazing`, the rows a bay holds up each story from its floor, a `spandrel` for a punched window or none for a curtain wall run past the floors, `Doors`, every state of which follows from where they hang, or `Layers`, its cells front to back, which may reach past the wall's own thickness; beside `Facade` and replacing nothing, `wall`, `shell` and `cut_opening` taking either through `walls._FaceRule`; a plinth, a belt, a crown and a head course are bands like any other, one band may stretch to the wall's height, and `examples/elevations.py`'s `OFFICE_NORTH`, `OFFICE_SOUTH` and `OFFICE_EAST_WEST` were replayed against the building they were read off; `Elevation.read`, the rule of a wall that stands, proposed and replayed over it, refused unless it reproduces every cell, with `floors_behind` finding the stories from the rows of full cubes behind a wall, and `describe` saying a rule in words; `python -m heaton_voxel elevation` is both over a building's four walls; `Rhythm.spaced`, the bays a width asks for, mirrored and within a column of one another, where a drawn rhythm the wrong width is still refused, `Fitted`, a rule composed for each wall it is laid over, and `recolored`, any rule in other blocks with its pattern untouched), `massing.py` (`Massing`, a tower as a stack of `Tier`s bottom up, each a rectangle or, by `Tier.over`, a `walls.Footprint`, its rows, stories, rule and the `terrace` it leaves open, each standing on the nearest tier under it that holds it, so tiers side by side are two towers on one base with a `Core` each; `Massing.read`, the tiers of a building from a box alone, a row's outline the rectangle whose edges hold most of its solid blocks, so piers, signs and fins fall outside it, each tier's walls read by `Elevation.read`, and a tier that will not read saying why in `unread`; `Core`, the wall round what climbs a tower, which no floor crosses, its doors where a `SwitchbackStair` lands, a floor the stair does not land on refused; and `MassingMixin.tower`, the tiers, floors, terraces, core and crown planned as one call and written whole or not at all, walked from a `doorstep` to every floor and back before its first write, with `_plan_shell` the plan `shell` itself makes), `design.py` (`design_tower`, a whole building worked out from a lot, a height and a `TowerStyle`: its `Scheme` of colors, plan, tiers and roof rolled with `noise.cell_hash` on the lot's corner and a seed, never the session's `rng`, every face a `Fitted` rule; a plan is a rectangle with parts out of its back, each tier that plan `Footprint.eroded`, and a `TowerDesign` is held to `tower`'s walk when it is laid; styles are taste, so `examples/tower_styles.py` has them and `examples/skyline.py` lays a row), `streets.py` (`StreetNetwork`, every street of a place drawn once as axis-aligned `Street` polylines, its nodes found at ends, bends and wherever two meet; `StreetSection`, a roadway and raised pavements; `Builder.streets`, one plan: crossing heights first, by the midpoint of the highest and lowest functions the streets' climb allows, each street's half-block profile between them by `_profile`, then every check any writer is held to and a no-jump walk over every column on `extrude._OnTheBand`, and every street and crossing a keep-out afterwards; `Builder.street_lamps`, `line_lamps` a street at a time, posts on its pavements and its crossings' corners, every roadway closed by `avoid=`), `city.py` (`SiteMap`, what stands over an area, sampled and no world in it after; `plan_city`, a city worked out from one: a loose grid of uneven blocks whose stretches of street are each judged alone, kept if dry, unbuilt and able to follow the ground, blocks kept with two such sides and grown from the cheapest near the site's center, lots or a park or left alone by what is on them, stories falling away from downtown; `CityPlan` with `describe`, `picture` and `avoiding`, `CityBlock`, `CityLot`; and `Builder.city`, streets first since they settle every height, each lot leveled to the pavement at its door and its tower designed there, a lot that turns out to be taken giving way to a `spare`, a street that meets what the samples missed drawing the plan again, then `blend`, parks and lamps, and a walk to every doorstep; `examples/city.py` is the whole of it), `crown.py` (`Crown`: rings one inside another counted in from the rectangle, fins up the diagonals, a cornice on brackets, read off the Palace Hotel's crown, 470 of its 474 cells, the other four a hatch; and `CrownMixin.crown`), `roof.py` (`Roof`, the one roof rule -- a half-block rise over distance from the outline, from its eaves or from square or round rings, square rings over a rectangle or any footprint, a set of columns whose rings are `field.chessboard`'s and whose holes are courtyards left open (an overhang that would close one is refused) -- with `rise_of` turning a vault's profile into a rise; `Wings`, several Roofs joined where they meet, every column the highest top any wing gives it and the first wing listed owning a tie, a valley a plain block and a wing's end closed only where it faces out of the plan, and each wing keeping what it is alone where a join of tops would undo it -- its building under its walls from its own springing, which no column reaches below, an overhang over open ground an eave, and its eave, where a higher wing roofs it over, still closing its attic; `Builder.roof` laying either, and `gable_roof` moved beside it as a `Roof` at pitch 2, byte for byte as it always wrote every roof it wrote before (sizes 1 to 12 on both axes pinned by digest) -- its corners now taken in either order, where reversed ones once wrote nothing, and a one-block-long roof's planks written once; its columns are laid by the loop `Vault` lays its own with, `vault._lay_column`), `furniture.py` (`Piece`, a small rule of `(dx, dy, dz)` cells to blocks with one height convention for every piece -- `dy=0` is the cell your feet stand in, on the floor block a frame's origin names (`piece.at(x, floor_y, z)`), the suburban house's `fy` -- in a vocabulary of seats named by their back, tables on the floor or on legs, beds, stacks, standing lamps and pendants, a bed, a door or a tall plant one placement given by either half or both, water in a piece -- a tub's, a fountain's -- held by the water check, its `spill` the piece's own cells placed with it, and lava and flowing water no furniture; `Piece.turned(frame)` turning its cells and states through a `Frame`, defaults included, as a framed write does; and `Builder.furnish`, which places every cell through `place`, refuses two pieces in one cell, a taken cell (`overwrite=`), a flight's or an approach's claim, what `_audit_plan` says the game would not keep, and a room that walks worse after than before -- floor sealed behind a shelf, made one-way, or left to a jump, in a `room` that must hold every piece -- with a room from each vocabulary that came before, the suburban dining room, a yellow tower flat and the Roman triclinium, reproduced cell for cell in `tests/test_furniture.py`), `framed.py` (`b.framed(frame)`: the Builder seen through a `geometry.Frame`, its `set`/`place`/`fill` taking a component's own cells and states and writing them turned through the Builder's own `_commit` — byte for byte the build written by hand at `frame.cell` with `frame.state`, except that the properties a state leaves at their defaults turn too (a bare stair faces north, so framed west it faces east; `Frame.state`, like a schematic's turn, turns only what a state carries), with a `Mix` picked at the *world* cell and the pick then turned, so a component framed four ways lays the same stones), `journal.py` (the write journal: every cell the Builder asked for, once, counted under the `stage()` that wrote it — a record, never an undo, and never a diff: what changed is `Worksite.diff`'s to say), `_common.py` (what they share, among it `_empty` — any of the game's airs, for the writers and the readers alike — the two halves of the one seal rule (`Builder._seal_state`) that `_contain_plan`'s seals and a railway's are held to -- `_walls_off_water`, whole to water and nothing it can enter, which a basin's rim is held to as well, and `_seal_gives_way`, a wall that stays one -- `_falls_through`, the ground writers' void-under test, which is the survival audit's own falls test, `survey._falls_into`, and nothing past the world's floor (the game's `FallingBlock.isFree`: `survey._FALLS_THROUGH`, a hand copy of the `replaceable` tag the probe does not yet vendor -- any air, water, lava, fire, a snow layer, grass, roots, leaf litter, a light block; kelp holds sand up -- with an old name read as the block it became and an id the game loads as air), `_takes`, what an earthwork writer takes without `overwrite=` (air, cover, natural ground, ores, trees, and with `wet` the water, freeze ice and lily pads -- never what somebody built), `basin`'s, `approach`'s, `extrude`'s and, through `Builder._refuse_built`, `grade`'s, `level_pad`'s and `road`'s one occupancy rule, and the stair and vault writers' refusals: where a continuation starts, what is in the way, head room), and `builder.py` whose `Builder` is assembled from their mixins, the way `world/` assembles `MinecraftSave`, and whose `_commit` every multi-write writer hands its planned call to, so a call reaching an ungenerated or unfinished chunk, or past the world, or naming a malformed state, or a block, property or value the game does not have (`IllegalBlock`, every such state at once; `Builder(allow=)` passes modded ids), is refused whole before its first write (the primitives check their own cells the same way), and whose `_audit_plan` is the one survival audit a writer runs over its plan before writing: `unsupported` over the plan's cells and two blocks round them, with the plan laid over the world and without it, refusing only the faults the plan would add — a block it writes that the game will not keep, and a door counted where it is shut further: two-way to one-way, one-way to sealed, or a door hung that opens from one side or none (stricter than `finish`, which never refuses a one-way door) — and never one somebody else left; both it and `_contain_plan`, the one water check, read the plan through `_plan_cells`, a placement into water taking the water its rule takes as `place_block` writes it, and `_contain_plan` runs `survey.contained` over it, refusing every `Leak` unless `seal=` walls each one's water off where it stands (railway's way) -- a full block that stays a wall, over water alone, never over a block standing in it -- checked again with its seals, and never sealing lava or a chunk that is not there, its `spill` world cells the plan's own water may run into (an aqueduct's fall into its basin, a fountain's spout); `grade` (`level_pad` through it), `road`, `basin`, `approach` and `extrude` run it, and `furnish` with no seal. `Builder`: the ground it builds on (`probe`/`ground` through the column reader — under the snow, never on a canopy or a torch — with `set_ground`/`known_ground`/`forget_ground` for what a session shaped and `stand_below` for the floor under a roof), earthwork (`grade`, the one earthwork writer, with `level_pad` a thin call to it that builds every pad it still builds byte for byte as it always did (a payload pin in `tests/test_grade.py`), refusing what these checks refuse and a clearance that is no whole number: cut, fill and drain over a rectangle or any columns to a target, a dry column taken by `cut=`/`fill=` and every wet one by `drain=True`, filled from the bed the column reader finds -- or, where the target lies under the bed, cut down to it bed and all, whatever `cut=` says -- and never a lid, the fill under its edge the retaining wall or `retain=None`; what somebody built in any cell it would take refused without `overwrite=`, since a keep-out lasts a session and the column reader stands on a roof as on a hill, a fill that starves flowing water refused unless `allow_dam=True`, a cut that lets water out unless `seal=` walls it off, and what the cut leaves hanging by `_audit_plan`; the outline consults no keep-out, its seals do; a `GradeResult` counting every column once and naming the water left `beside_drained`, since a grade may split a pond as its caller drew it), basins (`basin`: water over any outline, every cell a source, each column `max(1, round(slope * d))` deep from the shore -- `d` `field.distance_to_edge`'s, `slope` `SHORE_SLOPE`, the big lake's 0.75, any slope up to 1 taken as cliff-free and no more -- or a tank, a mapping such as `park_plan.depth_map`, which lays the park's 499 beds where `build_park.py` laid them by hand, or a profile of the distance; a floor over air, water, ice or anything a falling block falls through, or over sand or gravel standing on one, lined with one course, and low ground built up, both refused without `lining=` -- after the chunks and heights, so a floor under the world is past the world, not a lid; what somebody built in its cut refused without `overwrite=`; a pond it lowers that fed a flow refused without `allow_dam=True`; a gap in its bank walled by `rim=`, never over anything built, and another water let in walled off by `seal=`, or refused; lamps, `lamp=` with `light_level=`, set in the floor or the lowest water and placed by the flood, every ice lid within their reach -- by the taxicab bound, from the places a lamp could go -- held at 10, one the world already lights past it, and would still with the basin laid, named in `melting` rather than held or refused, and one the basin's own floor, rim or cut would light past it refused; and every column's ground learned at the surface, so it is water to the writers after it), planting at scale (`scatter`: flowers, grass, saplings, tall plants, azaleas, dead bushes, sugar cane, ornamental `Tree`s and boulders over any area, every die `noise.cell_hash` at the column's `y = 0` keyed on `seed=` and a CRC of what is planted -- never the session's `rng`, so a call that draws from it moves nothing and the same area, things and seed roll the same dice in any session, while what the dice land on is read: the world, the session's ground, keep-outs, paths and claims, so an earlier call that writes or plants there changes the planting -- thinned by `density=` per column and `cover=` per `noise.patch` of `clump=` columns, one kind per patch, and spaced by dart throwing in a keyed total order, maximal, `apart_from=` an earlier call's columns; each on the footing `survey.unsupported` holds it to -- the four `supports_*` tags, cane with water beside its block, a `Tree` on its sapling's soil, a block sturdy on every face that does not melt anywhere (ice, frosted ice and snow layers refused) -- and never on water, in a keep-out or on a path the session paved (`_paved`, which `road` and `pave` record; an earlier session's go in `avoid=`), a tree by every column its canopy reaches, nor into a flight's or an approach's claim; the ground's `snowy` flag written as the planting on it sets it; anything whose footing craft does not copy -- a cactus, bamboo, a mushroom, a nether `Tree` -- refused before a column is read, the whole plan held to `_audit_plan`, and a call that plants nothing refused), approaches (`approach`: steps or a ramp from a cell, or a column's ground, to another along x or z, `kind=` and `material=` always named -- stairs where a row rises or half a block a row in slabs and blocks -- every row level across its width and the profile the least earthwork of the monotone ones, the last row flush with the high end and the first held level with a low end under a lintel, the latest-rising on a tie, the earthwork counted as the cells the cut and fill really move; `HEAD_ROOM` cut over every row, a bank cut open to the sky and a row under an overhang -- a ledge, a tunnel's roof, whose top the column reader stands on -- cut to its head room only, the overhang left; what nobody built taken by `_takes` and anything else refused without `overwrite=`, water refused unless `over_water=` speaks for its column, lava in the plan or beside a cell it opens refused outright (the water check follows water), a cut that opens a pond refused unless `seal=` walls it off, `_audit_plan` over it, its surface and head room claimed beside the flights' so neither is laid in the other's -- a refusal naming whose claim it is -- its run a path the session paved, and the plan walked both ways with `allow_jump=False`, over the run's own columns only, before it is written: the park's whole-block gate steps were a jump at every step; with as many rows as it climbs its treads are a `StraightStair`'s, and over the park's own bank it lays the park's profile, cut and fill, a stair where the park left a block to jump), extrusions (`extrude` + `CrossSection`: a section of `(across, up)` pieces drawn once facing south -- blocks, a `Mix` picked at the world cell, a cut, a `Merlon` -- swept along waypoints, one line per across value mitered exactly at each corner and rasterized by `geometry.line`, a run free to bend within its heading but turning only at a right angle between straight runs, a closed path a rectangle, and a turn beside a diagonal, a fold, a turn back or a hairpin refused by name; each run's states turned by its heading through a `Frame`, defaults and all, so a log drawn `axis=z` lies along every run; at `follow="terrain"` -- the highest ground under each centerline cell through `smoothed_profile`, road's window and its clamp, every turn then laid level over the section's reach so no two cells side by side differ by more than a block -- at a level, or on a caller's profile level through its turns (a road's surface along its centerline, `[r.top(*cell) for cell in r.line]` -- not its `heights`, the profile, laid `width // 2` cells further along -- for a road with no turns); `pier=` at the world-locked congruence `(u - phase) % pitch`, a footing `bury` blocks into the ground, nothing founded in a keep-out named in `through`, and under `"terrain"` every column founded or refused; every column read first and each whose ground the terrain or a footing needs and lacks named in one `NoSurface`, where the lab's wall assumed its plane; water refused unless `over_water=` speaks for it -- under the terrain everywhere, at a height given only where the section reaches the waterline -- and water it never reaches refused as water; the keep-outs and the band come back over itself judged on the cells laid, so a pier wider than its deck reaches past it only at a pier; what nobody built taken by `_takes`, a flight's or an approach's claim refused, lava refused, starved or split water unless `allow_dam=True`, and water the plan lets out -- a cut opening a pond, a channel's open end -- unless `spill=` names where it runs or `seal=` walls somebody else's off (`_contain_plan`), `_audit_plan` over it, and a `walk` walked both ways on the band's own columns, never round a gap over the ground beside; a `Merlon` counted per run from its line's corners, so on level ground it lays what `battlement` lays and up a slope each tooth stands on its course; written in one total order, a cell already as planned left as it stands; each column's ground learned as the column reader will find it with the plan laid -- a wall's top, a ditch's floor, never a hedge's leaves -- a span excepted, and so a terrain-following call over an extrusion this session laid refused, since it would stand a second on the first, as is a cut following it down a ditch an extrusion cut, which would dig as deep again; a section holding water a channel, laid level -- `"terrain"` or a stepping profile refused -- and `examples/sections.py`'s `ROMAN_WALL`, `LAB_WALL`, `AQUEDUCT` and `HEDGE` held to the scripts they were read off), smoothed roads (which refuse to cross registered pads, or the keep-outs `reserve` registers without leveling; pads, roads and `pave` refuse water before writing unless `over_water=` bridges it or fills it from the bed -- a fill from the bed refused where it starves flowing water (`survey.starved`), and so a build bridged at the waterline or under it, which takes the water it stands in, `pave`'s and `light_cells`' refusals pointing at `grade(drain=True, allow_dam=True)` -- and `light_cells` unless it bridges; a road returns a `RoadResult` -- its points and width to follow it by, its centerline, the profile it rides, one height per cell, a high anchor's step among them, its swath and the height each column was cut to, and what `RoadRule`, `examples/road.py`'s classifier promoted and held equal to it, says the ground called for: grade, cut, a tunnel it still cuts open, or a bridge -- and refuses a fill deeper than the rule's `shallow_fill` of 3 as a dam unless `allow_dam=True`, a side-hill's fill under a cut included, and so a fill across water that starves a flow or cuts the water in two -- `Builder._split_faults`, the one split check, refused for routed fills and never for a grade -- while a cut that lets water in is refused unless `seal=`; its swath and a pave's rectangle are recorded as the session's paths), `blend` (the ground over any columns eased into what stands round them at no more than a block in `run` columns, a column within that left exactly where it is, its surface kept: water and what somebody built are heights to meet, and a column whose move would take something built, let hidden water out or leave sand over a cave stays put, counted `held`), `pave`/`light_cells` (plaza floors lit to the level asked for, which has no default and is named for the game's rule it meets — `MELT_SNOW` melts snow, `NO_SPAWNS` lets nothing spawn: a lattice derived from the light budget proposes the lamps and `plan_lights` checks every cell by the game's flood, crediting the light already in the world and patching behind walls and bumps), `line_lamps` (lamp posts beside a road, the post and the lamp named by the caller, stood wherever the same flood needs them — never in the road's swath or a keep-out, nor on water unless bridged — and audited so the lamp stays on its post; `road(lanterns=True)`, one build's post every twelve cells, was removed for it, a `TypeError` naming it), shells (each material a caller leaves out read from the Builder's `palette=`; `window(block=)` joins a pane or bars along its wall and writes plain or tinted glass as it is), `wall`/`shell`/`cut_opening` (a run of a `Facade`; a whole building's walls, stories, floors, deck and parapet or a `Roof`, planned as one call; and a hole cut in a wall that stands -- each refusing what stands in its way but the wall's own fabric an opening cuts, keeping a door hung in an opening, never writing into a flight's or an approach's claim, and audited by `_audit_plan`, so a cut that strands a torch or an iron door's button, or a floor that shuts a chest, is refused; doors are hung after with `doorway(*wall.door(a))` and flights built after, through the floor they climb to, which leaves their stairwell open), `furnish` + `Piece` (the rooms furnished last, each piece placed as an item is and the room walked from its doorstep before and after, both ways), `roof` + `Roof` (any roof over a rectangle, a disc or a plan -- gable, shed, hip, mansard, tiers, upturned eaves, cone, dome, spire, a vault's course, a hip over an L, a T or a courtyard -- as one rule, and roofs that meet joined as `Wings`; the hand-written ones it covers are reproduced cell for cell in `tests/test_roof.py` -- the Roman hip, low hip, pediment and cones, JeffWorld's baths dome and macellum cone, the castle's spire, the suburban rear gable, `gable_roof` and a `Vault` course, with the Roman shed's down-facing stairs turned on purpose -- and the suburban main roof, a cross gable, in shape as three `Wings` held to a grid read off JeffWorld, while the Roman `common.dome` (exact radius, an oculus) is not this rule, and mansards, tiers and upturns are settings no script wrote: the attic a required choice, hollow or filled, a stair facing up its slope, a `Mix` picked at the world cell in the whole-block roles, and `verify` holding it to what stands; there is no `read`, since a roof read off a building reproduces only when nothing is cut through it), entity/villager spawning (a villager typed by the biome at its feet, as the game types one), `shaft`/`spiral_stair` + `SpiralStair` and `switchback_stair` + `SwitchbackStair` (winding and dogleg stairs, and continuing one somebody else started; both share the read-then-verify half as `StairRule`), `arcade`/`vault` + `Vault` (half-block stepped vaults, read and extruded the same way, on a round `arch_profile` or a Gothic `pointed_arch_profile`; `close_ends=` walls a fresh run off at both ends from its `springing` up, every top slab over the wall made whole), `battlement` (a parapet course and its merlons along straight runs, the teeth counted per run from its own corners so every corner is a merlon, or alternating round a `geometry.ring`; the block named at the call, never a palette's, and a course already laid taken as it is, a fence or wall crown that `finish` has joined up included; audited by `_audit_plan`, as a wall is, so `overwrite=` cutting a gap from behind a torch is refused), `hang_lamps` (lamps on chains, spaced from the floor they must light and checked by the same flood, so a pier or a partition gets a lamp on each side; hung only from what the game would hang a lantern from, with `anchor=` a block set above every lamp), `doorway` (a door hung whole, an iron one with a button on each side, audited by `_audit_plan` so a door over a hole is refused rather than dropped), `flight` + `StraightStair` (a straight run that cuts its own stairwell, refuses short head room and stacked flights -- or one in an `approach`'s claim -- and walks both ways first), `railway` + `Railway` (rail lines from waypoints: shapes, boosters, the bed and the tunnel, and a `verify` that refuses water and walks the line both ways; its seal held to the one seal rule), `stamp` (a schematic pasted whole by the lower tier's paste, so it encodes what `import_schem(rotate=, flip=)` encodes in all eight orientations, block entities and entities with it — after every Builder check over it, and two of its own: a state its turn cannot turn, and a block entity or an entity whose receiving chunk's DataVersion is not the schematic's, which `entities=False` drops for the entities and nothing excuses for a block entity; it alone clears the stale block entities it writes over), and `finish()` running the whole aftercare litany over what the journal holds, chunk by chunk — connections one block past each chunk's writes, heightmaps and POI on the chunks written, lighting on those and their neighbors — and auditing what the game will not keep two blocks past them |
| `heaton_voxel/craft/survey.py` | **reading the ground** before touching it — `walk_surface` (the block you stand on, with snow, water and ice sheets reported rather than folded into the height — read from the game's maps, or from the blocks of a finished chunk that lost them, with the same answer), `survey` (that over a rectangle, plus the character map, the height field, and why each hole is one), `reachable`/`StepSpace`/`step_kind` (walking a build the way the player does, forwards *and* back again, through iron doors only where something opens them, and up and down the game's `climbable` tag, `_CLIMBABLE`, held to the vendored tag), `unsupported` (every block the game will not keep: lone halves, torches and ladders with nothing to hang on, sand over a hole -- through what the game's `FallingBlock.isFree` lets it, its `replaceable` tag (a hand copy, `_FALLS_THROUGH`, not yet vendored by the probe and so held only to the block list), hanging roots and leaf litter as much as air, an id the game loads as air and an old name as what it became, and never kelp -- plants off the footing their block class asks the game's own tag for -- `supports_vegetation`, `supports_azalea`, `supports_dry_vegetation`, `supports_sugar_cane`, each copied and held to the vendored tag, so cane on farmland and a dead bush on glazed terracotta go -- and cane with no water beside its block by the game's `#water` (`holds_water`: seagrass, kelp and a bubble column water it) or frosted ice, chests under a block that conducts power — which the game will not open, by conduction and not opacity — and iron doors nothing opens), `spawnable` + `SpawnCell` (every cell a monster can spawn in at night, by the light that lasts and the game's own floor, room and collision, asked per monster — a stray spawns in powder snow, a wither skeleton in a wither rose or a fire — and per dimension by `MONSTER_SPAWN_LIGHT`, which `craft.light` keeps), `water_body` + `WaterBody` (every cell joined to a seed through cells that hold water, 3-D, so pools that meet under their surfaces are one body -- the big lake takes in the fortress moat -- with the freeze ice on it crossed, its lids, its flows, its dry shore, lazy per-column probes, and what `within=` clipped and which chunks it could not read; past `limit=` a `Refused`), and `contained` + `Containment`/`Leak` and `starved` on one water model, the game's `getNewLiquid` (falling water amount 8 at every level, walls by `block_facts`' collision faces, two sources making a third under `WATER_SOURCE_CONVERSION`, taken as on): every cell a plan's water would enter by the game's first step of spread, every side its rules allow and so never a leak missed, with the leaks standing today kept apart from the plan's (lava, or a block the plan puts where water already runs, stays the plan's) -- every side it reports from the cells of JeffWorld's big lake, city moat and frozen lake, read-only, accounted for by the game's own `getSpread` -- and every flow a removal cuts off from its feed |
| `heaton_voxel/craft/audit.py` | **a build already standing, judged in one call** — `audit` and `Audit`, and `python -m heaton_voxel audit`: what the game will not keep (`survey.unsupported`), water still moving (`survey.water_body`, since a settled flood has nowhere left to go and `contained` passes it), water that runs at the next block update (`survey.contained`), stored heightmaps against the blocks, and a walk both ways from a doorstep the caller names. Read-only, and nothing refuses: a finding is a thing to look at. It is the check a paste from the app or a schematic never ran |
| `heaton_voxel/craft/render.py` | **pictures** of any of it — area maps, floor plans, cutaways and `render_elevation`, a box as you see it from outside, recess and all; the eyes every build must be checked with. `render_area` reads every finished chunk it touches against its blocks and names what its map misdraws in `ColorMap.stale_maps`, which the command line prints as a warning |
| `heaton_voxel/craft/colors.py` | **the colors** — every block and biome color, in one public place: `block_palette()`, `block_heuristics()`, the wood fallback and the unknown gray, `BIOME_COLORS` and `biome_color()`. A leaf of literals importing nothing from the package; `render.ColorMap` draws from it -- a stair, slab or wall the game has in the color of the block it is cut from, by `blockinfo.cut_shapes()` between the exact table and the substring rules, so the expansion over the game's registry lives in blockinfo and this file stays literals; a modded stair keeps the rules' gray, and a cut whose block has no color is unknown, counted under that block -- `craft.biomes` answers for the biome half under its older names, and the Unity app's generators read it and nothing private (the block one `cut_shapes()` too, as `CutFrom`) |
| `heaton_voxel/craft/biomes.py` | **biome facts that are not colors** — `pretty(name)`, `FREEZING_BIOMES`/`freezes()` (what freezes back after a thaw; the Unity app's biome palette generator bakes it into the app and audits it against the jar), and `VILLAGER_TYPES`/`villager_type()` with `VILLAGER_KINDS`: the villager type the game gives a villager standing in a biome — its `VillagerType.BY_BIOME`, and plains (`VILLAGER_TYPE_DEFAULT`) for any biome it does not name — curated here and held entry for entry to `fixtures/block_shapes.json`, which `tools/probe_block_shapes.py` reads out of the jar. It still answers for the biome colors under their older names |
| `heaton_voxel/craft/light.py` | **derived block light** — `simulate_block_light`: the flood the game will run once it relights (stopped at the faces shapes close — a slab's solid half, a stair's back, a snow layer's underside — and held to the light the game stored in both fixtures, cell by cell), over a box with an `overlay=` of what a plan would add, so "does this melt / freeze / spawn" is answered before a block is written; `durable_emission` (only the light that lasts: a lit furnace or redstone lamp is dark, an unwaxed copper bulb is the 4 it weathers to) and `plan_lights` + `LightPlan` (where the lamps go so every target reaches a level by that flood and no `at_most` cell passes its limit — dark is a result, `require()` the refusal; what `pave`, `light_cells`, `hang_lamps`, `line_lamps`, `shaft` and `basin` plan their lamps with); and the levels the game has a rule for, each read off the jar, which those six writers require one of (or a plain 1..15) -- `basin` only when it is given a `lamp=`, and a lit `shaft` only: `NO_SPAWNS` (one past `MONSTER_SPAWN_LIGHT`, kept here: 1 in the overworld and the end, 8 in the nether, and deliberately not an int), `NO_NEW_SNOW_OR_ICE` (10), `MELT_ICE` (11), `MELT_SNOW` (12) |
| `heaton_voxel/craft/block_facts.py` | **generated** game facts — every block state's sturdy faces, solidity, redstone conduction, light emission and dampening, the faces its shape closes to light, whether a monster can stand on it, and whether it passes the game's per-cell test of room to spawn in (the mob's box must also clear collision, which no one state answers); how water moves through it, in a table of its own: whether water can enter the cell at all (`enters`, the game's `canHoldAnyFluid` -- a container, or a block tagged `washed_away_by_fluids`, which holds water and lava themselves), whether it is a container (`container`) that takes a water source (`takes_source`; none takes a flowing one), and the faces of its collision shape that water's wall test sees (`collision`, held to the game's `canPassThroughWall` pair by pair); per state `holds_fluid` (water or lava; a bare sea pickle is wet: an omitted `waterlogged` takes its default) and `holds_water` (the game's `#water`, a flow as much as a source, never lava: the one wetness test survey's reader and cane check and render's depth shade read); by block `in_heightmap` and `known`. Written by `tools/probe_block_shapes.py` from the jar; what the audit, the iron-door check and the light simulation read |
| `heaton_voxel/craft/block_registry.py` | **generated** block list — every block id the game has, the values each of its properties takes (`properties_of`: None for an id the game lacks, `{}` for a block with no properties), which blocks fall and which are always fluid (`FLUIDS`) or always water (`WATER_FLUIDS`, every one but lava). Same probe, same run; craft's alone, since the lower tier writes whatever it is given |
| `heaton_voxel/craft/blockinfo.py` | **block knowledge**, hand-written over the two generated tables — `check_state` (refuses an id the game lacks, naming its replacement if it was renamed (`RENAMED`) or else the cuts its block does come in and the nearest real spellings, and refuses every property or value the block does not take; it returns the state as given, defaults unfilled, and `allow=` passes modded ids), `is_block`, `is_air`, `falls`, and the categories a builder judges by: `is_tree`, `is_vegetation`, `subsoil_of`/`is_natural_ground`, `material_of` (the full block a stair, slab or wall is cut from, and `cut_shapes()`, every one the game has to that block: what render draws one in the color of) and `form_of` (that, plus the panes: which of `FORMS` — block, stairs, slab, wall, pane — of which block an id is). Each category is a copy of the game's own block tags, held to them by `tests/test_blockinfo.py`, and every departure is a named choice with its reason that fails as stale once it excuses nothing. The one `holds_fluid` and the one `holds_water` are `block_facts`', re-exported (`holds_water` by `craft` too). The builders judge by these: `clear_vegetation` fells and strips by `is_tree`/`is_vegetation`, `smooth_terrain` raises ground with `subsoil_of`, `Vault` reads its material by `material_of`, and a railway's water and falling supports are `holds_fluid` and `falls`, as is `unsupported`'s falls audit |
| `heaton_voxel/craft/geometry.py` | **shape, with no world in it** — the one compass (`COMPASS`, `CLOCKWISE`, `OPPOSITE`, `turn`, `axis_of`, and `heading_of`, whose diagonal tie is named at the call: `ties="z"` is the castle's, `"x"` the secret lab's), round shapes by one rule at a named rounding (`disc`, `ring` in order around its center; `ROUNDINGS` `"inside"`/`"quarter"`/`"half"` are the park's, the lab's and the castle's and Roman city's three disc rules), `line` (road's rasterization, and the one road walks: half to even, so a line is not its reverse), `smoothed_profile` (the ground along a path averaged over a named window either side -- `ROAD_WINDOW` road's 3, `WALL_WINDOW` the Roman wall's 5 -- then lowered to the lower envelope that steps at most `max_step`, anchors pinned before and after; one sweep each way reaches it, so the three or four passes road and the two walls ran never did anything; `closed=True` wraps window and envelope round a loop; it only lowers, so a high anchor stays a step; road rides it) and `Frame` (a component's local across/up/out placed facing any way, mirrored or not, its states turned by the schematic's own vocabulary so a frame and `Schematic.flipped`/`rotated` agree; a state that vocabulary cannot turn is refused; `Builder.framed` writes through one and `Builder.stamp` lands a schematic exactly as one would; no other writer is built on it). A leaf: the builders' and survey's compass tables are derived from it. `examples/castle.py` keeps its own disc, ring and facing, and `tools/` its own disc, ring and facing functions, all pinned to it cell for cell by `tests/test_geometry.py`; `line` is pinned to road's loop as it stood and held to be the package's only one, and the two `tools/` copies of that loop, inside scripts that write a world when run, to its step expressions by their source; `smoothed_profile` to road's smoothing as it stood, and to the Roman wall's `smoothed_line` and the lab wall's `_wall_tops`, compiled out of the tools' own source and run |
| `heaton_voxel/craft/noise.py` | **dice keyed on a cell** — `cell_hash(x, y, z, seed)` (a 32-bit roll in whole-number arithmetic, no `hash()` and no `random`, the same on every run and platform; at `y = 0` it is `tools/lake/noise.py`'s `_hash` bit for bit, held to it by loading that file by path) and `patch` (a jittered grid's nearest feature: the clump a cell falls in, by floor division, ties broken by a stated total order; a bad size is an `NBTError`, as a Mix's `clump` is). A leaf: nothing from the judged tier, and only `NBTError` from below |
| `heaton_voxel/craft/field.py` | **distance fields over a set of columns** — `distance_to_edge(cells)` (the exact Euclidean distance from each column to the nearest one not in the set, the lake bed's metric and `Builder.basin`'s: `edt` square-rooted, each 4-connected piece on its own box padded by one, the padding the lake had to learn) and `chessboard(cells)` (how many king steps it is out, the hip roof's metric: a straight 45-degree valley where a Euclidean distance puts a bump), one convention for both -- center to center, 1 on the edge, a hole is outside, a dict in sorted `(x, z)` order -- and `edt` and `blur` promoted verbatim from `tools/lake/field.py`, held to it node for node and output for output by loading it by path. A leaf: nothing from the judged tier, and only `NBTError` from below |
| `heaton_voxel/craft/style.py` | **materials as a builder names them** — `Mix` (weighted blocks picked by position: `pick(x, y, z)` is a pure function of the Mix and the cell, keyed on its seed folded with a CRC of its entries so two mixes on one seed are two fields, and `clump=` picks whole `noise.patch`es, keeping the weights), `Material` (a family named by any of its blocks or its stem — `brick`, `bricks` and `brick_stairs` are one — with its forms checked against the registry, `form()` refusing one the game lacks by name with the nearest real ones), `Palette` + `ROLES` (the thirteen materials the eight legacy writers read, each defaulting to what that writer wrote before it read a palette, so `Palette()` is the defaults and not a style -- the two sample styles, read off the Roman town and the secret lab, are `examples/palettes.py`'s, and `examples/craft_session.py` is a whole guarded session built in either; a role it lacks is a `TypeError`, never a fallback) and the `FROM_PALETTE` sentinel those writers' material arguments default to, read from `Builder(palette=)` (`site.builder(palette=)` too) through `Builder._materials`/`_surface`: an argument given always wins, a Material becomes the form the writer writes (block, stairs and slab, or pane — refused by name where the family lacks it), and a Builder with no palette writes today's bytes. `ROAD_MIX`, `LANE_MIX` and `PLAZA_MIX` live here, the same objects under `craft.build`. Values with no world in them: a Builder's `set`, `fill`, `level_pad`, `road` and `pave` write a Mix as each cell's pick (`Builder._sweep` for a fill, in `y, z, x` order), while a Mix handed to any lamp, and a Mix or a Material handed to `check_state`, a plan overlay, a stair, vault or railway rule (`SpiralStair`, `SwitchbackStair`, `StraightStair`, `Vault`, `Railway`), a placement or a lamp outside the palette's two (`pave(light=)` and `light_cells(light=)`, which read a Material as its block), is a `TypeError` naming what it is (`_plan.refuse_many`, by the `_craft_style` each class carries, since `_plan` cannot import this, or `Builder._materials` for the palette's writers); the newer rule objects -- `Roof`, `Facade`, `Opening`, `battlement`, `Piece` -- take a Material as the form each role needs and a Mix in their whole-block roles, picked at the world cell |
| `heaton_voxel/craft/worksite.py` | **session safety** — `Worksite`: byte-exact snapshot of the files a build box can touch, drift-refused resume, protected zones (`protect`/`check`) that trip on one flipped block, `check().uncovered` naming every file `save()` would write that the snapshot does not hold, `builder()` (a `Builder` whose every write is admitted first: refused whole before it lands in a region file the snapshot does not hold, reaches one through the chunks `finish()` settles around it, or touches a protected zone; `restore()` ends it), `diff()` (what the build changed, block by block, `before` read from the snapshot's own copies — the content truth the journal is not), before/after renders, `restore()` making the build never have happened, `rollback()` doing it from a later session, and `list_worksites` reading every manifest a save holds without loading it. `retire()` removing an accepted worksite's own snapshot and never a file of the world, and a dry run unless told otherwise. `craft/cli.py` is the command line over them, `python -m heaton_voxel worksite list|rollback|retire`, and it only looks, but for `retire --delete`. Use it for every real-world build; it is the jw_snapshot / verify_paths pattern, promoted |

The package lives under `heaton_voxel/`; layers import from each other with
relative imports (`from .nbt import ...`). The deterministic tier is
re-exported from `heaton_voxel/__init__.py`; `craft` never is — taste is
imported explicitly (`from heaton_voxel.craft import Builder`) so its
presence in a script is visible. `pyproject.toml` defines the build and the
`heaton-voxel` / `heaton-voxel-nbt` console scripts.

`examples/`, tests and `fixtures/` sit at the repo root and import the package
by name (`from heaton_voxel.world import ...`). See [API.md](API.md) for the
contract they code against; `fixtures/` holds vendored real game data.

## Hard rules

1. **Standard library only.** No Minecraft libraries — the formats are parsed
   from the binary directly. If a package ever becomes necessary, use a venv
   and produce a `requirements.txt`.
2. **Current Minecraft, and the old formats read-only.** `blocks` reads and
   writes DataVersion >= 2825 (1.18), reads chunks Minecraft 1.13 to 1.17.1
   saved (1519..2730: 1.17 decided 2026-10-23, 1.13 to 1.16 on 2026-11-03)
   and refuses every write to one (`ReadOnlyChunk`, every writer asking
   `Dimension.writable_blocks`), and refuses anything else with a clear
   error -- older than 1.13, whose blocks are numbers, 1.13's own snapshots,
   and the 1.18 snapshots' formats in between. The save layout written is
   the current one only (every dimension under `dimensions/`); a save in the
   layout before 26.1 (DataVersion 4772: `region/`, `DIM-1/`, `DIM1/`,
   settings and the single player in `level.dat`) is read where it stands
   and every write to it refused (`ReadOnlySave`, `save.layout == "old"`),
   whatever version its chunks are, and both layouts at once are refused.
   Do not widen either range, or write an old chunk or an old-layout save,
   without being asked: older formats differ in ways that would yield
   plausible-looking nonsense instead of an obvious failure, and the game
   rebuilds an old chunk when it loads one, and moves an old save's files
   when it opens it, which nothing here can do for it.
3. **Byte-exact round-tripping is the core promise.** `load()` then `save()`
   must reproduce the input byte for byte. If you change encoding, re-run the
   round-trip checks below.
4. **Nothing is hardwired to one save.** Every tool and example takes a world
   path. Tests run from `fixtures/`; `MINECRAFT_SAVE=<path>` additionally
   exercises a live world.
5. **American spellings, everywhere.** Code, comments, prose, docs and
   commit messages: color, center, neighbor, gray, behavior, serialize,
   initialize, analyze, canceled, labeled, modeling, license, defense, and
   *story* for a building level. Minecraft's own ids and NBT keys are
   already American -- `center_x`, `center_z`, `light_gray`, `armor_stand`
   -- so this keeps our words and the game's words matching, which is the
   point. The one exception is an external API that dictates the spelling:
   mirror those exactly and never "fix" them (Unity's `MonoBehaviour`,
   .NET's `CancellationToken`). See "A spelling sweep is four words away
   from breaking the build" below before running one.
6. **Refuse rather than damage.** When input is corrupt or unsupported, raise
   with a message naming the cause. Never silently coerce, and never let
   "could not read" be indistinguishable from "nothing there".

## Verifying

```bash
python -m unittest discover -s tests                               # ~60s idle
python -m heaton_voxel nbt <file.mca> --verify --check             # byte-exact round trip
MINECRAFT_SAVE=~/path/to/world python -m unittest discover -s tests # also hit a real save
python -m venv /tmp/mypy && /tmp/mypy/bin/pip install mypy && /tmp/mypy/bin/mypy  # types
```

CI runs the same checks on every push that can change them:
`.github/workflows/test-lib.yml` runs the suite on Linux, macOS and Windows
under Python 3.10 to 3.14, with mypy beside it, and
`test-lib-dotnet.yml` runs the conformance suite on all three, checks the C#
against `dotnet format` and packs the NuGet package. One run of each
measures coverage for Codecov (`codecov.yml`; the flags are `python` and
`dotnet`), which is where the README's badges come from. `.gitattributes`
turns off line-ending conversion for every file, because the corpus and
fixtures are compared byte for byte.

Releases follow dynaface's pattern, two manual steps per language: a build
that tests, stamps `heaton_voxel/version.py` or `Version.cs` with the date
and run number, and stages the packages on S3 (`build-lib.yml`,
`build-lib-dotnet.yml`), then a deploy that publishes what was staged, by
version (`deploy-lib.yml` to PyPI, `deploy-lib-dotnet.yml` to NuGet.org,
both by Trusted Publishing). [RELEASING.md](RELEASING.md) has the one-time
setup and the steps. A version bump touches four files together:
`pyproject.toml`, the csproj, `heaton_voxel/version.py` and
`csharp/HeatonVoxel/Version.cs`.

The package ships `py.typed`, which tells a caller's type checker to read
its annotations, and what that vouches for is the deterministic tier:
`[tool.mypy]` in `pyproject.toml` lists it, a bare `mypy` from the root
checks it, and it stood at no issues on 2026-11-03. Keep it there when you
change the lower tier, and add a new lower-tier module to that list
(`tests/test_architecture.py` fails naming one that is missing). mypy is a
development tool from a venv, never a dependency. `craft` ships its
annotations too, partial and unchecked (712 issues at mypy's defaults on the
same day), which a caller's checker reads as they are. The world mixins share
`MinecraftSave`'s own state through `world/_state.py`, a class that declares it
for the checker and is empty at run time.

The suite is 2,615 tests and about 120 s on an idle machine, down from
about 90 s by a pass that dropped no case and weakened no assertion; a
busy machine can take two or three times as long, in CPU time as much as
on the clock, so time a change against the code before it, run side by
side, not against a number. A quarter of what it spent went into
rebuilding its own scenes: the block-state table expanded afresh by every
test that read it (`fixtures.block_shapes` expands it once per content of
the file now), the same few saves encoded and fsynced 1,173 times over
(`tests.test_world.make_save` writes the bytes it made the first time),
the hills on which two tests in `tests/test_grade.py` build four worlds,
raised all four times (now once, and copied), and one function of a tool
parsed out of its script for each of 500 trials (`tests/test_geometry.py`'s
`_tool_function`). A scene that costs and is wanted more than once is
built once and copied, and a memo is held to what it stands in for:
`MakeSaveTests` to the library's own write, directories, bytes and modes,
and `FixtureReadingTests` to the expansion uncached.

Any change to encoding, packing or the region layout should be checked against
a real world, not just the fixture — the fixture is a 16-chunk sample and
cannot represent everything a decade-old save contains. There are two
fixtures now: `fixtures/r.-1.0.mca` (26.2, the legacy palette codec) and
`fixtures/modern/r.-1.0.mca` (26.3, the bare-id codec); a palette change
must keep both round-tripping. After a game update, regenerate the
default-state table with `python tools/generate_block_defaults.py
--version <id>` (it runs the game's own data generator from the launcher's
JRE) and commit both generated files. Then rerun `python
tools/probe_block_shapes.py --version <id>`, which rewrites
`fixtures/block_shapes.json` (every state's collision height, rigid top,
sturdy faces, light, light-occluding faces, spawn floor and space, falling
and fluid, from the game; how water moves through it -- whether its fluid
is water, whether water can enter the cell at all (`canHoldAnyFluid`,
whose `washed_away_by_fluids` tag holds water and lava themselves),
whether it is a container and takes a water source, and the faces of its
collision shape that water's wall test sees; per block its heightmap
membership; the block tags the judged tier is held to, the four
`supports_*` footings among them, `climbable`, and the block that waters
sugar cane besides water (`supports_sugar_cane_adjacently`); each dimension type's
spawn light rule; which villager type each biome gives) and
the run-time `heaton_voxel/craft/block_facts.py` and
`heaton_voxel/craft/block_registry.py`, and makes the craft tier's block
tables (`blockinfo`'s categories among them, held to the vendored tags)
fail by name wherever the update added a block they misread, and
`craft.biomes`' villager table wherever it retyped a biome. It
refuses rather than writes when its own models disagree with the game:
the face masks are checked against `Shapes.faceShapeOccludes` pair by pair,
the collision masks against the game's own `canPassThroughWall` for every
ordered pair of distinct collision shapes each way along every axis, no
container may take flowing water, and the tags read out of the jar are
checked against the ones the game binds. New facts go in tables of their
own beside the old ones, so a regeneration that adds one leaves every
existing table in `block_facts.py` and `block_registry.py` byte for byte as
it was; in the fixture, a block's entry gains keys beside its old ones, and
every value it held before reads back unchanged. Then rerun `python
tools/probe_legacy_chunks.py --version <id>`, which asks the game's own
chunk upgrade for the name of every 1.17 biome number (both twins) and for
what the 1.17 fixture's chunks become (`fixtures/legacy/upgraded.nbt`);
`tests/test_legacy.py` then fails by name on any block, biome or surface the
reader no longer agrees with it on, and on any rename craft's `RENAMED` does
not list. Then rerun `python tools/probe_old_layout.py --version <id>`,
which hands worlds in the layout before 26.1 to the game's own
`FileFixerUpper` and records what it moves where
(`fixtures/old_layout.json`); `tests/test_old_layout.py` then fails by name
on any setting or player the old-layout readers no longer answer as the
game's move does. Then rerun `python tools/probe_snbt.py --version <id>`, which puts
its fixed corpus of SNBT strings and binary tags to the game's own
`TagParser` and `Tag.toString()` and rewrites `fixtures/snbt.json`;
`tests/test_snbt.py` then fails by name on every case the library no longer
reads or writes as the game does. Last,
rerun `python tools/generate_wall_cover.py --version <id>`, which writes the
wall-cover table into both twins (`heaton_voxel/wall_cover.py`,
`csharp/HeatonVoxel/WallCover.generated.cs`) from the game's own collision
shapes and `wall_post_override` tag, then `python
tools/generate_heightmap_blocks.py` and `python
tools/generate_connect_faces.py`, which need no game: they copy what
the probe read into `heaton_voxel/heightmap_blocks.py`,
`heaton_voxel/connect_faces.py` and their twins under `csharp/HeatonVoxel/`.
Then regenerate the corpus. `tests/test_connect.py` fails by name on any state the shape
fixture lists and the table does not answer, and `tests/test_heightmaps.py`
fails until the heightmap table is what the generator writes, and holds it
to every map the game stored in both fixtures.

`fixtures/conformance/` is the language-neutral corpus ports verify against
(see [PORTING.md](PORTING.md)). `tests/test_conformance.py` regenerates it
every run and fails on drift; after a deliberate behavior change, rerun
`python fixtures/make_conformance.py` and commit the diff — never edit the
corpus by hand.

The C# port lives in `csharp/` and is **complete**: it is the deterministic
tier, whole — nbt, blocks, world, connect, place, schem. `heaton_voxel.craft`
and the CLIs are outside it by definition, not omission (PORTING.md states
the policy). Verify it with `dotnet run --project
csharp/HeatonVoxel.Conformance`, which checks every corpus artifact and
exits nonzero on failure. The twins workflow: Python changes first, corpus
regenerated, then the C# diff — never the reverse.

**The Unity app lives in a repo of its own** (split out 2026-11-08) and takes
the C# as a Release build of `HeatonVoxel.dll` (netstandard2.1), built from
a checkout of this repo once the conformance suite passes -- never as copied
source. So nothing here reads or writes the app: no tool writes into it and
no test reads it. What it depends on is the DLL's public surface and, for
its generated palettes and block list, the craft names `DownstreamApiTests`
lists; change either on purpose, and say so in the commit.

**Analyze with the library, not scratch scripts.** The utilities exist
because ad-hoc versions kept being rewritten: `save.surface_y(x, z)` for
ground level, `save.histogram(c1, c2)` for what-is-here,
`save.find_blocks(c1, c2, "_door", match="suffix")` for where-is-it (it
prunes on the section palette, so it is a fraction of the cost of a loop),
`save.entities_in(c1, c2, ids="villager")` for who-is-here,
`dim.chunk_status()` for generation stage, `craft.survey` /
`craft.walk_surface` for the whole "what is actually on this ground"
question, and `craft.water_body` for "where is this lake, all of it" --
the five hand floods in `tools/` each answered a piece of it. One-command
pictures via
`python -m heaton_voxel render <world> --area X0 Z0 X1 Z1` (top-down),
`--plan Y X0 Z0 X1 Z1` (floor plan — where the doors and the gaps in a wall
are) or `--slice x AT Z0 Z1 Y0 Y1` (cutaway — shows dungeons/interiors).
After editing blocks: `save.reset_lighting(c1, c2)` before `save()`, or
torches light only themselves.

**Surveying by hand gets the same three things wrong.** A top-down render
draws the roof, so a floor plan is the picture that finds a door — the
stadium's only entrance took six probing rounds to find with area maps and
one `--plan 69` to see. `surface_y` lands *under* snow and *on* water: no
motion-blocking map counts a snow layer, so the default map never sees
snow at all, while `WORLD_SURFACE` lands on the snow, the leaves and the
torch. A hand-rolled "what
am I standing on" misses the snow on one map and reports it as terrain on
the other; `craft.walk_surface` reads the snow from the cell above the
ground, steps through water (kelp and seagrass included) to the bed,
reports both separately, and answers the same from the game's maps as
from the blocks. And an opening-finder that filters out all-solid and
all-air columns hides the very thing it is looking for.

## Things that have already cost time

The lessons below came out of real builds in Jeff's own saves, and they cite
the scripts that made them. Only the ones the tests pin craft to are in
`tools/` here (`tools/README.md` lists them); a script named below that is
not there was a one-off world build, kept in a private archive.

**The worlds are live.** Jeff plays these saves while work is in progress.
Region files change under you mid-read, and a torn read looks like corruption.
Call `nbt.world_is_open()` before writing; `save()` already refuses by default.
Tests snapshot the fixture rather than reading a live save for this reason.

**Visiting old chunks in-game converts them, permanently and one-way.** Loading
a pre-1.18 chunk makes Minecraft run its DataFixerUpper and re-save at the
current version. Do not suggest this casually as a "fix" — it rewrites the
player's archive. It also grafts a new deepslate underworld below y=0 on
chunks that predate 1.18.

**So a world is mostly the version it was made in, and a blank view is a
read error nobody shows.** The game upgrades a chunk only when it loads it,
never a whole world, so a map downloaded from an older version stays that
version wherever nobody has stood: Greenfield v0.5.4, opened in 26.3, held
281,996 of its 296,000 overworld chunks as 1.17.1 saved them (DataVersion
2730, read 2026-10-23), and only the 14,000 round its spawn as 26.3. The
library refused every 1.17 chunk then, and the app draws a chunk it cannot
read as nothing -- by design, since a popup per chunk while flying would be
unusable -- so the city simply was not there. That is why 1.17 is read now
(hard rule 2). The same day the app showed nothing even near spawn, because
the build being run predated the 26.3 codec and every read of a 26.3
chunk failed in `BlockStateCodec.Decode`: 3,555 `InvalidCastException`s in
the app's log, and nothing on screen. When a viewer built on the library
draws nothing, read its log before reading the world, and check the
library build it carries against the library's.
Greenfield's surface maps are also stale in places -- `WORLD_SURFACE` one
above grass whose cover its makers cleared -- and the game's own upgrade
keeps them as they are, so a map that disagrees with the blocks is not by
itself a misread.

**`assertEqual` on large sequences.** Comparing two differing 4096-element
lists spends *minutes* building a diff nobody reads — it once looked exactly
like a hang. `test_examples.py` and `test_blocks.py` have an
`assertSequencesEqual` helper that reports the first mismatch instead. Use it.

**`pgrep -f "foo.py"` matches its own wait-loop.** `until ! pgrep -f "x.py"; do
sleep; done` never exits, because the loop's own command line contains the
pattern. Use `pgrep -f "[x]"` or check differently.

**NBT longs are signed.** `>>` sign-extends, so a field read across a long
boundary picks up phantom 1 bits that survive an `|=`. Mask to the bits the
word actually holds. This was a real bug, found only because the test data
happened to contain negative longs.

**Flooring, not truncating.** Chunk and region indices come from `>>` and `&`
(or `//` and `%` — both floor in Python). Never `int(x / 16)`, which truncates
toward zero and lands a block at x=-1 in chunk 0 instead of chunk -1.

**Set iteration order reaching the bytes.** A state enters a section's
palette the first time it is written, so *the order a sweep writes cells is
the order of the palette*, which is the chunk's bytes. `place_block` once
ordered its re-derivation sweep by height alone (`sorted(touched, key=-y)`
over a **set**), leaving same-height cells in set order: identical builds
encoded differently. Any sweep that writes must have a total order. Found
only because the C# port's byte digests disagreed while every block state
matched.

**Caches that short-circuit side effects.** A color cache once skipped the
"unmapped block" counter, making every unknown block report a count of 1.
If a cached function also records something, record before the cache check.

**A path that never matches looks exactly like a default.** `world_border()`
read `<save>/data/minecraft/world_border.dat` for months. No Minecraft version
has ever written that file: the border belongs to a *dimension*, and the game
keeps one per dimension at
`dimensions/minecraft/<dim>/data/minecraft/world_border.dat`. The read missed
every time, fell through to "no file means the default border", and reported
`stored=False` and walls at +/-29,999,984 for every real save ever passed to
it. Nothing raised. The Unity app drew `BorderWall` 16 blocks inside
JeffWorld's actual wall and nobody could have noticed from the code.

Three things kept it alive, and each is the general lesson. The docstring
asserted a premise nobody checked -- "the game only writes the file once the
border differs from its defaults" -- which is false; it writes them
unconditionally from a world's first save. The commit said the rule was
"proven against three 26.2 saves", but what those saves proved was that the
file is *not at the path the code looks in*. And the conformance corpus wrote
its test file to the path the library expected, so both twins agreed -- on the
wrong path. **A vector that passes before and after a fix proves nothing**;
make it write where the game writes, and check it fails against the old code.

The border is also the one entry in the game's own
`LevelDatToSavedDataFileFix` that goes to a dimension while weather, clocks and
game rules go world-wide -- which is how it got filed under the same top-level
table by analogy. When a settings file's home is in question, `26.2.jar` ships
unobfuscated and the `net/minecraft/util/filefix/` classes answer it outright.

**A section of light is not a section of world.** The game stores light one
section past each end of the world, so a chunk can hold a section at Y=-5
or Y=20 with light and no `block_states`. `Blocks.min_y` was
`min(sections) * 16`, so such a chunk said -80: every heightmap read in it
came out 16 low, and `rebuild_heightmaps` wrote maps the game, counting
from `yPos * 16`, reads 16 high. Both twins agreed, and `modern.json` pinned
the wrong bytes, since it rebuilds exactly such a chunk. It surfaced when a
block walk using the game's own heightmap predicate matched every stored
map in the fixtures except one chunk, off by exactly 16 in every column.
The top was the same: a Y=20 light section made `max_y` 335, so
`place_block`'s atomic pre-check let a door on y=319 write its lower half.
Now `min_y` is `yPos * 16` and `max_y` the top of the highest section
carrying blocks, and the corpus reads that chunk's maps before any edit.
The range is also what `get` and `set` answer to: a cell in a light
section is outside the chunk, as it is in every chunk that stores none,
rather than air in the few that do -- which had `unfreeze` reporting ice
on the world's floor as water left floating over air.

In JeffWorld's four central regions 75 of 4,096 chunks store the lower
section (35 the upper), and 11 carried a rebuilt `WORLD_SURFACE` the
game read about 16 above the real top in every column (13 to 24 on the
2026-10-20 copies, nearly all 15 to 17, building since having moved tops
under the stale maps): (-12, -8), (-1, 4), (-1, 5), (-1, 6), (-3, 16),
(-3, 17), (-11, 19), (-10, 19), (-12, 20), (-11, 20) and (-10, 20). The
three at x=-1 are the granite plaza's "stale strips at y47/48": the
library was reading the game's correct map 16 low, and the rebuild that
"healed" them wrote the error into the world. (-1, 6) had been re-saved
without its light section, so a scan of only the chunks that store one
misses it -- the chunk list, not a scan, is what the repair worked from.
They were **repaired 2026-10-22** under the worksite `heightmap-repair`,
with Jeff's go-ahead, the world backed up and closed: a
`rebuild_heightmaps` over the eleven with the fixed library, after which
every column of them reads 0 off, in memory and again from disk
(`tools/jeffworld_repairs_2026_10_22.py`, historical, beside the two
other repairs it made that day).

**A spelling sweep is four words away from breaking the build.** Making the
repo American took 1,298 substitutions across 174 files and three passes,
because each of the first two lists was too narrow. One of them ran a
case-insensitive `behaviour -> behavior` across the Unity project, which
renames `MonoBehaviour` in 25 files and stops everything compiling. Hold
engine and framework type names out by name -- `MonoBehaviour`,
`StateMachineBehaviour`, `PlayableBehaviour`.

Four ordinary words merely *contain* a British-looking run: chec**kerb**oard,
**optimis**tic, **realis**tic (and realism), and Enti**tyRe**gions. A
substitution that has not been told about them writes `checcurboard`. And a
sweep over `.py/.cs/.md` silently misses `.shader`, `.csproj` and
`package.json`; the two shaders each had a `colour` field, and **only a
player build compiles a shader** -- every batchmode harness passes without
looking at one.

The conformance corpus is the twinned part. `derived_states.json` keyed its
expectations `"neighbours"`, `fixtures/make_conformance.py` wrote it and
`HeatonVoxel.Conformance/Program.cs` read it back, so Python, the regenerated
corpus and the C# reader move in one commit or the twins disagree.

**Partly-generated chunks have no heightmap.** Chunks stopped at
`structure_starts` or `biomes` have no surface. Scanning for one is ~90x slower
than a heightmap lookup and produces terrain that does not exist. Skip them.
Nor build in one: the game generates over whatever is written into a chunk
it has not finished, and a `level_pad` over a `features` chunk once wrote
225 blocks there without complaint, while one running into a missing chunk
wrote 36 before it found out. Every `Builder` writer now plans its whole
call and hands it to `_commit`, which checks every chunk and height the plan
reaches before the first write and refuses it whole, as `set`, `place` and
`fill` check their own cells -- for a placement, the neighbors it settles
as well, since a fence placed at a chunk border rewrites the fence across
it.

**A stair is a rule, not a pile of blocks.** Continuing a spiral stair
somebody built by hand means writing hundreds of blocks from a pattern
inferred out of a handful, and a misread pattern prints itself all the way
up the tower before anyone sees it. `SpiralStair.read` derives the rule and
then **replays it over every level already standing**, refusing unless it
reproduces them exactly — that check is the tool, the writing is the easy
part. Two more things it knows that surveying by hand does not: a run that
stops mid-level (stair placed, treads not) is *unfinished*, not a different
pattern, so continuing must pick up **inside** that level rather than above
it; and a shaft carved level with its top step leaves that step a ceiling,
which no top-down render will ever show you.

**A build you saved is drift, so `Worksite.open` is never the way back.**
The resume guard compares file digests, and from those alone a build you
meant is indistinguishable from a week of Jeff's play on the same regions
— so once `save()` has run, reopening the worksite refuses, and
`resnapshot=True` would rebase the baseline onto the build you wanted
undone. Undoing from a later session is `Worksite.rollback(world, name)`,
which reads the manifest and reinstates the snapshot without `open()` in
the path. It returns a receipt and never a worksite, on purpose: nothing
should go on to build against a baseline it has just reverted. `restore()`
is still the in-session call, and both now refuse a snapshot whose own
bytes have rotted — writing corruption over a world is worse than the
build it was undoing. The same refusal is why the session that builds is
the one that checks: `site.diff(c1, c2, save=MinecraftSave.load(world))`
right after `save()` is what reached the disk, block by block against the
snapshot, and a later session cannot open the worksite to ask. Look
first with `python -m heaton_voxel worksite rollback <world> <name>`: it
names every file the undo would move, the other worksites whose builds
go back with it, and the files written since acceptance. Those others
are every *later* worksite over the same files and every earlier one not
accepted before the snapshot was taken, because a snapshot copies whole
region files and two sessions open over one region undo each other;
taken first is not saved first. In JeffWorld
`dimensions/minecraft/overworld/region/r.-1.0.mca` is covered by four
worksites, three of them accepted, and it was last written about a month
after the latest acceptance and two after the first — which is why that
command has no `--write`.

`worksite retire` has a `--delete` because it moves no file of the world:
what it costs is an undo, never a build. It refuses a worksite never
accepted, since nothing then says its build is settled, and it cannot be
talked past that from a later session: `open` refuses a saved build as
drift, so `accept` is out of reach, and a rollback stamps nothing. Five of
JeffWorld's thirteen worksite directories stand that way (`lab-gantry-chain`,
`lab-repairs`, `lake-smoothing`, `roman-town-repairs` and `suburban-house`,
read 2026-11-01), and `building-swap-backup` has no manifest at all, so
neither kind can be retired: each is a person's to judge and remove by hand.
A session that means its build to stand ends with `accept()`, or leaves a
snapshot nothing can clear.

**A stair you can climb and cannot descend passes every picture.** A
tread needs *three* clear cells above it, not the two a standing body
occupies. The game resolves horizontal movement before gravity, so for one
tick as you step off a tread your feet are still at its height while your
box already overlaps the next cell down the run — and your head is in the
third cell above that neighbor. Clearance for two builds a one-way tower:
the yellow tower's sixteen stories all had to have two blocks chopped out
of them, found by Jeff walking down it, after the top-down renders, the
floor plans and the cutaways had all drawn it finished.
`craft.build.HEAD_ROOM` is that three, and `SwitchbackStair.claimed` is
what states it — a floor slab or a stick of furniture must clear the
treads at `y-1`, `y-2` **and** `y-3`.

The spiral kept checking two for a whole phase after the switchback and
the flight checked three, and the `shaft` docs said to carve two levels
past the top step — which is a stair you climb to a door above it and
cannot come back down from. A stone three over one diagonal tread of a
33-step spiral leaves 34 cells above it one-way, and the old check built
it without a word. The winding stair's one real exception is its *step*:
the third cell over it is the tread the stair turns onto three levels up,
and a step is entered level from the tread beside it, never from above. So
`spiral_stair` checks `HEAD_ROOM` over its whole walkway and passes the
rule's own block there and nothing else; the glass tower, read back,
holds to it over all 378 levels without a fault, and walks both ways.

**Getting there and getting home are two questions.** The move relation is
not symmetric — a slab overhanging a tread is half a block going up and a
wall coming down — so a forward-only reachability search happily certifies
a one-way building: 2,332 cells walked, nothing stranded, on the tower
above. `craft.survey.reachable` therefore records its forward moves and
walks them backwards from the doorstep, and reports `.stranded` whether
you ask or not; the round trip is not an option on the call. It is also
the only check that finds a stack of shelves sealing twelve floor cells
behind it, or a stairwell opening one cell too narrow — both of which a
floor plan draws as a finished room. `Builder.furnish` walks a room before
and after its furniture, and refuses floor the furniture cuts off.

**Classify block names exactly, or by whole suffix or prefix — never by
bare substring — and default the unknown to solid.** A walk checker that
matched substrings made `grass_block` non-solid (it starts with "grass")
and every `light_blue_*` block with it (it starts with "light"), so it
deleted the floor — and a walk check that deletes the floor reports every
room as reachable no matter what is standing in it. A wrong "solid" raises
a false alarm somebody reads; a wrong "air" hides a wall nobody sees. The
walk checks keep that rule. The light flood and `spawnable` are the named
exception: they read an id the game does not have as the air the game
loads it as (`block_facts.known`), since a lamp's light and a monster
both go where the game's air is, not where a guess put a wall.

**Then check the tables against the game, because a name is a guess about
a shape.** Run against every state the game has, `step_kind` disagreed
with 12,908 of them. It walked through chains, end rods, bamboo, mangrove
roots, flower pots and candles, all of which collide. It called a floor
`torch` a wall (the `_torch` suffix misses the bare name). And it took a
fence or a wall for a whole block — a jump — when they are a block and a
half, so every pen and walled garden had a door; they are `tall` now, and
`StepSpace` neither stands on nor climbs them.
`tests/test_survey.py` now holds the classifier to `fixtures/block_shapes.json`
state by state, and every place it departs from the game is a named choice
with its reason: a door or gate is air because you open it, a stair is half
because you climb it from the low side, lava and fire are solid because
nobody should walk into them. A choice that stops excusing anything fails
as stale.

**A stair read out of a finished building competes with the furniture.**
A seat is a stair, and a pair of them facing each other across a lounge is
not a flight, so a greedy `read` counts the sitting room as more staircase
— the same shape of error as a `Vault.read` counting a wall head as more
roof. `SwitchbackStair.read` derives the rule only from levels offering
exactly one flight, votes on the axis and the width across the run, and
lets the replay judge the rest; a block standing where the rule wants
nothing is an *extra*, not a fault, and conflating the two either refuses
a furnished tower or accepts a broken one. It compares whole states but
only the properties the rule **names**: a tread's `facing` is the rule's,
while the `axis` of the log under it is the aim of whoever placed it, and
a hand-built flight has logs lying every which way — the yellow tower's
stringer is 265 `axis=x`, 64 `axis=y` and 19 `axis=z`.

**A roof made of stairs cannot make the step a vault needs.** A stepped
gable falls a whole block per block outward; a vault falls *half* of one,
because a bottom slab and a top slab are two heights inside the same block.
That doubled resolution is the entire difference between a curve and a
staircase, and `craft.build.Vault` exists to keep it as a rule (two tuples
of half-blocks) rather than a pile of slabs — `Vault.read` lifts it off a
building and replays it over every course standing before handing it back,
the way `SpiralStair.read` does. Two things it knows that a hand survey
does not: the width of a vault has to be decided by **vote across the run**,
because the wall head it lands on is often its own stone and a greedy read
counts the building as more roof; and a ridge course may be thickened to a
whole block wherever something hangs from it — `hang_lamps(anchor=)` sets
one over every lamp — so a run that stopped at the first of those would
report a fifty-block nave as one.

**A vault's end wall stops half a block short of every top-slab course.**
A wall filled up to the underside of a vault -- to the block under each
column's lowest piece -- leaves the lower half of every top slab open:
with the nave's depth (1,1,1,2,1,1,1,4) that is four slots at each end,
offsets ±1 and ±5, and `tools/nave/build_hall.py` builds exactly that into
CastleWorld's `vaulted-hall` (top slabs over brick at x=415, 419, 421 and
425 at both z=-584 and z=-556). Jeff's own walls under the nave follow
another rule, the same sixteen cells at z=89, 95 and 111 (read
2026-10-21): quartz from the springing at y=89 up to the shell, and every
top slab over the wall made a double slab. `Vault(close_ends=)` is that
rule -- `springing` where it starts, which is where `clear_under` starts
too, `section(closed=True)` the end course, and `verify` holding a closed
run's two ends to it -- for fresh runs only. `Vault.read` reads no closed
ends (the nave's own run ends, z=72 and z=121, are open), so a loose read
of a closed run hands back an open rule, and extending it would leave the
old end wall standing across the hall. Two things stop that: the strict
read trips on the whole slabs, and `Builder.vault` refuses a continuation
whose old end course is closed that way, however the rule was read. With
`clear_under` the walls stand in the span it empties, and are cut through
it like the rest. `build_hall.py` stays as it is, historical, slots and
all.

**A roof stair faces up its slope, and nothing drawn from above can tell.**
A stair's full side is the way it faces, so on a roof it faces uphill and
shows its low half to the course below; turned down the slope, every
course puts its full side at its low edge, and the roof comes out a
sawtooth. The Roman `shed_roof` (`tools/roman_city/common.py`) faced its
stairs `toward`, the word the roof was named for, and TestWorld (2)'s
stables (x -213..-191, z 218..226) climb south from y=-55 to -47 with all
207 of their stairs facing north (read 2026-10-21); an area map draws the
same granite either way. `craft.build.Roof` lays a gable, a shed, a
hip, a cone, a dome or a vault's course as one rise over distance from
the outline, and faces every stair away from a neighbor it falls to; a
hip corner picks between its two falls, so its choice is made by the
falls and not by the rise, which once turned the corners of a ring that
peaks down their own slope because the ring inside was lower too -- a
neighbor a square ring's corner touches only across its diagonal.
`tests/test_roof.py` holds every stair of every rise it tries to that,
runs each hand-written roof the rule reproduces beside it, and the shed's
facings are the one place it departs on purpose.

**A hip over a plan is a king's walk out of it, and a cross gable is
roofs joined.** A roof over a plan that is no rectangle is two questions.
A hip over an L rises with the chessboard distance to the outside -- how
many steps a king takes to walk out -- which runs a straight 45-degree
valley up from the reentrant corner. The lake's Euclidean distance,
floored, stands a ring high where the valleys climb to the ridge (1 cell
on a seven-wide L, 3 on a T, 9 on a cross), a bump in every valley; and a
max of the rectangles' hips is the same roof only where they overlap to
each other's far edges -- a bar against a square's side leaves a trough
down the seam, three columns lower than the roof on either side. Gables
are the other way about: no one distance serves them. The suburban
house's main roof and rear gable share one rectangle (x -314..-277, z
-14..12) whose distance from its outline is one gable 27 deep, while what
stands is two gables meeting in a valley at z=2, and a front bay whose
ridge runs across them. So `Roof(rings="square")` takes a footprint, a
set of columns, and `Wings` joins rules: every column the highest top any
wing gives it, the valleys falling where the planes cross, and a wing's
end closed only where it faces out of the plan, since an end that runs
into another wing is inside the roof. Held to a grid read off JeffWorld
(2026-10-21, and unchanged on 2026-10-22), the house's three wings stand
at Jeff's height in 1,067 of 1,073 columns and lay his top block in all
but 96: two rows of the south slope he laid in stone, the rear gable's
verges, and the eight valley cells where he laid stairs for `finish()` to
turn into inner corners and the rule lays plain blocks -- inner-corner
valleys would turn the notches of every stair cone too, which no script
or save has, so that is left to the owner. A hole in a footprint is a
courtyard, left open with the roof sloping into it -- the Roman domus was
written as a hip with a box of air cut through it afterwards -- and an
overhang that would close one over is refused: roofing over an opening
somebody drew is no roof's to decide.

A join of tops alone undoes the wings it joins, and nothing drawn from
above shows it. The first `Wings` filled every wing's attic, and closed
its ends, from the lowest springing of them all, so a tall wing over a
lean-to was filled down through its top stories, and the suburban rear
attic from y=76 through the ceiling `tools/suburban_house/roof.py` filled
above; an eave on a wall line reached down the house's wall to the roof
beside it, built into its windows or refused by its bricks; and where a
higher wing roofed over a lower one's eave, the eave course that closed
the lower attic was gone. Of 3,400 random joins of two or three gables
and hips at bases up to eight blocks apart, 2,547 wrote into a building
below its springing and 699 left an attic open to the sky. Each wing now
keeps what it is alone: its building under its walls from its own
springing, an overhang over open ground an eave with air under it, and
its eave, roofed over, the floor it has alone wherever it closes its
attic. None of the 3,400 does either.

**A crenellation counted around a perimeter leaves its corners bare.** The
crowns written by hand walked a tower top's rim as one list, its sides
concatenated, and put a merlon on every other cell (`i % 2`). That
alternates cleanly only while every side is odd: a side of even length
shifts the count for every side after it. All seven of JeffWorld's Roman
field towers built that way (`tools/roman_city/jw_groundwork.py`; x=318,
356, 432 and 470 on the south wall, z=338 and 376 on the east, x=454 on
the north, read 2026-10-21) wear the same crown -- two bare corners, a
doubled merlon and a doubled gap each -- and so do both gatehouse turrets
of the castle example in CastleWorld (x 33..39 and 45..51, z 292..300),
whose rim is two rows and two columns joined. Every top-down render draws
them crenellated. `Builder.battlement` counts each straight run from its
own corners with `merlon_offsets`, the castle's tested rule for its
curtain walls -- both ends always merlons, an even run doubled beside its
far corner -- so a corner is a merlon by construction. A round tower has
no corners, and its crown alternates round `geometry.ring`'s order, which
is why the castle's round towers come out right: every ring above radius
0 is even, and `battlement(ring=)` refuses an odd one.
`examples/castle.py` keeps its own `merlon_offsets`, held equal to
craft's by `tests/test_walls.py`, and its turret crowns as they are -- a
change to the example's output is the owner's call -- and the historical
scripts keep theirs.

**A facade's bays count along the wall's own run, and a test of the
wrong coordinate builds every column alike.** The secret lab's stables
(`tools/secret_lab/m2_buildings.py`, `stables`) put a post wherever
`x % 4 == 0` on the walls at x=741 and x=757, and wherever `z % 4 == 0`
on the walls at z=924 and z=936 -- in each case the coordinate the wall
holds constant. 924 and 936 are multiples of four and 741 and 757 are
not, so in JeffWorld the north and south walls are posts in all 17
columns of rows 67..72 and the east and west walls have none (read
2026-10-21). The lab's HQ, which tested `z % 3` along its walls of constant x the right way
round, is one rule: its south, west and east faces match
`Facade(pitch=3, phase=0, ...)` in all 825 cells, and its north face in
all but the 9 of its entrance. `craft.build.Facade` takes the congruence
on `Wall.along`, which *is* the world's x or z along the run, so the
stables' mistake cannot be spelled; and because it is the world's, an
integer `phase` locks bays to the world -- walls that share one agree on
every pier wherever they stand, as the lab's HQ and main hall do on 0 --
while `phase=None` counts each wall from its own low end, the same corner
for all four walls of a `Wall.ring`, so two shells side by side agree only
if they are given one phase. `tests/test_walls.py` runs the stables, the
HQ and the hall beside the rule.

**A tower is composed, and one rule for a whole wall cannot say it.** Read
off the five-story block pasted into FlatWorld (2026-11-01): piers 5, 5, 4,
5, 5 apart between glass corner pavilions, a ground story on a rhythm of its
own two columns out of line with the piers above, and a crown that is the
shaft with its piers in another material. Every `Facade` of 2,560 tried
matched 45.8% of its south wall at best, 413 of 902 cells, and the reason is
not a missing setting: `(along - phase) % pitch` is one rhythm, and this wall
has two, neither of them a congruence. It is still a small rule. Each face
is 5 to 7 distinct columns, stories repeat cell for cell and the outer walls
mirror to 99.7%, so an `Elevation` of a few bands says all of it, and
`Elevation.verify` holds all four walls to it, 3,348 planned cells with none
wrong, the ground glass a block behind its piers and the three doors among
them. Its chunks are somebody else's, a sign says "Made by _Balance", so
`tests/test_elevation.py` holds the sample to the numbers read off it, its
piers, its rows, its hinges and the census of each face, and not to a copy of
them. The same building is what found that every stained pane drew as clear
glass: the dye family stopped at the block, so the one thing that makes this
facade a pattern was invisible in any picture.

Depth took two things the writers had never met, because a Facade's cells
stay in the wall's own layers. A wall can reach into the footprint now, so a
cell the hollow is about to clear is no longer a cell somebody took, and two
walls can reach one cell, which `shell` refuses unless they agree, rather
than letting whichever came last win without a word. And a rule can hold a
door, which somebody will open: `_same_crown` compared `open`, so walking
through the entrance turned the wall wrong. A door's `open` and `powered` are
how it stands in play, not what was hung, and the one comparison every writer
and reader asks says so.

**A skyscraper is tiers, and a wall rule cannot see them.** The Palace Hotel in
Greenfield (x 106..128, z -523..-485, ground y=68, read 2026-11-01, exported to
`tall_multi_green.schem` and pasted into FlatWorld at (-417, -60, 228)) is 138
rows on a 23 by 39 plan: a lobby behind an arcade, then 21 by 37 to row 79, 17
by 33 to row 109, 13 square to row 123, and a crown of glass rings and corner
fins, full of furnished hotel floors and a garden with a pool at rows 68 to 78.
`Elevation.read` reproduced every wall the first time it was asked, so nothing
about its facades was missing. Everything missing was above the wall: nothing
found the tiers, nothing said they were one building, and nothing built the
top. `Massing.read` finds twelve from a box alone, and `Crown` matches 470 of
its 474 cells, the other four a hatch somebody cut to reach the terrace.

The walk was the surprise. Its only way up is ladders, and `reachable` could not
climb, so `audit` reported 869 cells and "round trip everywhere" for a 138-row
tower: a walk bounded by what it can do reads as a building that ends there.
It climbs now, by the game's `climbable` tag (`LivingEntity` lifts a body for a
collision or a held jump, javap, 26.3, so a bare vine climbs as a ladder does),
and it found what no picture would: the ladder at (10, 17) runs from the lobby
to row 111, and every opening off the shaft stands two cells across open air
from it, so the hotel's floors are reached only by a leap off a ladder, which
the walk does not credit. Its elevators are for show as well: 143 sealed iron
doors, nothing wired to open them, which is most of what `audit` says of it.
`tower(doorstep=)` walks a plan before writing it for this reason.

What the lower tier was asked and answered: `connect_blocks` rewrote none of the
tower's 610 connecting blocks, rotation and mirroring left nothing behind and
four quarter turns came back identical, and `import_schem` left no stale map in
the tower's chunks. So it changed nothing. FlatWorld's own copy has stale maps
in those 9 chunks because the app that pasted it was built on 2026-10-23,
before `import_schem` rebuilt them. That copy also differs from the schematic
in 431 cells: the sign reading PALACE HOTEL in wool pixels over redstone lamps
on the south face and the fountain along the north are in the file and not in
the world.

**A keep-out dies with its session, and a tower reads as a hill.** Trying a
city grid on a scratch copy of CastleWorld (2026-11-01), a plaza drawn over the
setback tower at (726, -349) took 11,043 blocks out of it, the whole of
every overlapping column from the tower's roof down, and `grade` said nothing.
Its pad was registered in the session that built it, which was over; `probe`
stands on a roof as it stands on a hill, so the tower was ground 126 blocks
high; and `_takes`' docstring said in as many words that `grade` cuts whatever
stands in its outline. `basin`, `approach` and `extrude` had asked since they
were written. `grade`, `level_pad` and `road` ask now, cell by cell, through
`Builder._refuse_built`, `overwrite=True` being the way past it; a road may
still cross a path this session laid.

**A city is what the land allows, so the first plan is drawn from the land.**
Fifty towers round CastleWorld's lake (scratch copy, 2026-11-01) took four rewrites
of what a block is, each forced by the ground. Wanting four dry sides kept 15 of
72 blocks, since water there runs in long streaks, and a lakefront block has three
sides of street and one of water, so each stretch of street is judged alone and a
block wants two. Dry was not enough either: one street crossed a ravine 22 blocks
deep, so a stretch has to be able to follow the ground, by the same midpoint a
network settles its crossings with. And a site sampled every four columns does not
see what is one block wide. A mineshaft that breaks the surface in badlands put
rails in one plaza's cut and timber in a yard, and a stray magma block stood on a
street line. Each of those was right to refuse and wrong to stop a city for, so a
lot gives way to a spare, a street draws the plan again, and `blend` holds the
column, since it is only tidying land and nobody asked for that cell. Hidden water
and sand over a cave are the same fault and come back through the same loop: what
is under the ground is found by trying. The walk was the check that could not be
argued with: 50 towers, 25,960 street columns, every door reached with no jump.
Blending took the worst face from 32 blocks to 15, and 26 of 53 plazas still have
one over three: a yard two or three columns wide can only take up so much of a
street climbing a block in four.

**A grid of roads is not a street network.** Laid road by road over CastleWorld's
terraces (2026-11-01), a five by five grid disagreed with itself at 6 of its 25
crossings, each road laid over the last cut a step of up to five blocks at the
edge of a crossing, and every rise of `geometry.smoothed_profile`'s clamp was a
whole block, a jump, so 71% of it could be walked without one. Nothing was wrong
with any one road. `Builder.streets` settles the crossings first, as a set of
heights no further apart than the streets joining them can climb, and only then
each street between two flat ends, in half blocks, so the same grid is 25,025
columns every one of which walks both ways. Two things it found on the way. A
post's flood runs to the end of a strip, so a street could not light the far side
of its crossings until their corners were opened to it, and a keep-out is a
rectangle, too coarse to open a corner and not the road through it, which is
`line_lamps(avoid=)`. And cactus was somebody's: `_takes` read it as built until
`is_vegetation` named it, so a street across the desert stopped on one.

**A street of towers wants a rule that fits, and "never cut to fit" still
stands.** Every rule in `examples/elevations.py` refuses a wall of any width but
its own: a rhythm that is the wrong width is a decision nobody made. A generated
building makes the decision on purpose, so `Rhythm.spaced` works the bays out
for a width and `Fitted` composes a rule per wall, and the Elevation stays exact.
Swept over 11,410 rhythms none came out uneven or lopsided. The walk found what
the pictures did not, twice: glass set a block back pinches a floor one wide
into pockets behind its piers, and a core in a narrow floor cuts a wing off. So
`piers` recess only on a rectangle with a floor two wide round its core, and a
tier that a core would split does not step back. A crown over a one-tier plan
once went on a tier flush with the wall under it, its wall standing in that
wall's parapet, which `verify` found and every render drew finished: a tier
steps back or there is no crown.

**Reading a wall reads the building behind it unless it is told not to, and
"solid" is not "floor".** `Elevation.read` on that block, given nothing but its
four corners, finds its four floors, proposes a rule for each wall and replays
it: 3,348 cells, none wrong, and at no cell different from the rule written by
hand. Three things it took. `Facts.solid` is what a sign stands on, so glass
panes count, and read that way the ground story was all floors and a row of
desks 67% of one; `floors_behind` counts full cubes, where floors stand at 92 to
100% of a row and nothing else passes 33%. Layer 1 behind a window is
somebody's desk, so a read past the wall's own thickness goes only in rows
where a column stands open through it, and only as far as those columns meet
something. And cutting bands greedily from the bottom glued the belt course to
the story over it, which replayed perfectly and read wrongly: the replay proves
a rule true, not that it is the rule, so the cut is the shortest description
that the rows allow. A rotated cycle was the same kind of fault: `Glazing`
counted from the floor row, so a ground story's piers came out "bricks,
sandstone, sandstone, bricks, bricks" with the sandstone at the bottom. All
true, and no use to a reader, which is who the output is for.

**A hanging lamp is not a floor lamp.** `light_cells` spends one level
getting out of the paving; a lamp on a chain spends the whole climb down to
head height before it travels a block sideways, so the ceiling height is
part of the spacing and a chandelier that works at 4 blocks is dark at 12.
`Builder.hang_lamps` derives that, refuses a lamp that arrives under the
level asked for, and takes `columns=` because under a vault there is nowhere
to hang a chain but the few columns the shell leaves clear — a hall wider
than one row can reach is a refusal, not a dark aisle nobody notices until
nightfall. The derived spacing is only a proposal: light goes round a pier,
not through it, and `plan_lights` checks every floor cell by the game's
flood, so a hall split by a partition gets lamps on both sides where the
arithmetic credited one lamp with both.

A chain hangs from what a lantern hangs from, and that is a choice: the
game keeps a chain under anything, air included (`ChainBlock` has no
`canSurvive`), while `LanternBlock.canSurvive` asks `Block.canSupportCenter`
of the block above: never a fence gate (the `unstable_bottom_center` tag),
and otherwise sturdy at the middle of its underside — the `center` mask in
`block_facts`, which `_hangs_from` reads and `tests/test_vault.py` holds to
the jar state by state. The name list it replaced hung chains from 5,138 states (243 blocks)
the game would drop a lantern under — leaves, saplings, flowers, beds,
banners, shelves, rails — and refused 17,417 (382 blocks) it keeps one
under: bottom slabs, bottom stairs, fences, walls, panes, carpets. It had
the half slab backwards, since the gap is under a *top* slab. Its comment
said the nave it came from thickened its ridge to a double slab at every
lamp. Read again on 2026-10-21, the hall thickens a course where a lantern
could not hang without it, and not along its ridge: all fifty ridge courses
are bottom slabs, the eight lamps down the nave hang straight from them on
nether brick fences, and each of the nine upper aisle lamps hangs from a
double slab set into a course of top slabs (six lamps lower down do hang
under top slabs). So a bottom-slab crown needs no boss, and `anchor=` is a
block set above *every* lamp of a call — `tools/nave/build_hall.py`'s
habit, whose output is byte for byte what it was; a hall like JeffWorld's
hangs its ridge without one and its aisles with one. The anchor is held to
the same rule, and an id the game does not have is no ceiling, since the
game loads it as air.

**A light level is a rule of the game, so a light names the one it is
for.** `pave` and `light_cells` lit to 12 unless told otherwise, and
`hang_lamps` and `shaft` to 8, and neither number said why. 12 is where a
snow layer melts (`SnowLayerBlock`: block light over 11). 8 is the
*nether's* no-spawn level, whose dimension type sets no block light limit
and refuses a spawn at a light level over 7, and it is no rule of the
overworld's or the end's, where any block light at all refuses one. So the
four writers now take `light_level` with no default, and `craft.light`
names the rules, each read off the 26.3 jar with javap: `NO_SPAWNS` (1 in
the overworld and the end, 8 in the nether, one past `MONSTER_SPAWN_LIGHT`
and so from the dimension types the probe vendors), `NO_NEW_SNOW_OR_ICE`
(10: `Biome.shouldSnow`/`shouldFreeze` test block light under 10),
`MELT_ICE` (11: `IceBlock` melts over 11 less its own dampening of 1) and
`MELT_SNOW` (12). The choice is worth making on purpose because it is most
of the lamps: a glowstone floor's lattice is `2 * (15 - level - 1)` apart,
4 at `MELT_SNOW`, 8 at `NO_NEW_SNOW_OR_ICE` and 26 at `NO_SPAWNS`, so level
10 with `clear_above=True` keeps a cleared floor bare on about a quarter of
`MELT_SNOW`'s lamps, and `NO_SPAWNS` spawn-proofs it on a lattice a
forty-second as dense (edges add their own, and how many depends on where
the floor sits against the world-locked lattice: over every placement, a
208-block square took 5,460 to 5,461, 1,403 to 1,430 and 144 to 172).
`NO_SPAWNS` is deliberately not an int: a literal 1 is 1 in every
dimension, and a type equal to 1 that meant 8 in the nether would let the
two be confused exactly where they differ.

**A rail stands on less than a player does, and a tunnel open to water
fills again.** The game keeps a rail only on a *rigid* top face
(`BaseRailBlock.canSurvive`): leaves, fences, walls, panes, path blocks,
farmland, chests and bottom slabs all hold a player and drop a rail at the
next block update, and so does sand or gravel with a hole under it; a
climbing rail also needs a rigid block level with it where it climbs to.
`craft.build._holds_rail_top` agrees with the game's own
`isFaceSturdy(UP, RIGID)` on all 35,723 block states of 26.3, and
`tests/test_railway.py` keeps it that way. The game's answer comes from
`tools/probe_block_shapes.py`: a few lines of Java run against the jar and
the launcher's libraries (the launcher's runtime is a full JDK), which
settles any "can X stand on Y" question outright. The Stronghold link in JeffWorld
taught the rest, and `Railway` enforces it: the low cell of every change of
level needs `HEAD_ROOM` (its walk check stranded 56 cells until one block
came out at the foot of the ramp), and a bore whose mouth opens onto a lake
refills however well it is drained, so `Builder.railway` refuses water
touching the running space unless told what to `seal=` it with.

**A retired block name is air, and nothing says so.** The game loads an
id it does not know as air, logs a line nobody reads and writes the air
back. `minecraft:chain` became `minecraft:iron_chain` at DataVersion 4541,
and `Builder.hang_lamps` kept the old name as its default, as did four
scripts. In JeffWorld the secret lab's TNT "hanging charge" lost its chain
that way and floated until three `iron_chain` were hung back over it on
2026-10-20, under the worksite `lab-gantry-chain`. The Roman town's 286
chains survived only because their chunks predated the rename, so the
game's own fixer renamed them.
A name the game never had is the same air: the two Roman domus
peristyles (`tools/roman_city/jw_districts.py`, `domus`) stood their
colonnades on `minecraft:quartz_wall` -- smooth quartz has no wall form --
so all 62 posts, 186 cells, loaded as air and the smooth quartz beams hung
over nothing, which a picture from above draws the same. They
were rebuilt as `minecraft:diorite_wall` on 2026-10-22, under the
worksite `domus-posts-2026-10-22`.
`tests/test_block_ids.py` now holds every literal `minecraft:` id in
`heaton_voxel/` and `examples/` to the game's block list, allowing an old
name only in the files that *read* old chunks. The renames are one table,
`craft.blockinfo.RENAMED` -- chain at 4541 and `grass` to `short_grass` at
3692, every block rename the game's own fixers have made since 1.18 (the
jar's `DataFixers` registers no other `LegacyBlockRenameFix` past 2825).
The categories read an old name as the block it became, and
`blockinfo.check_state` refuses one, naming its replacement.
`place.py` takes waterlogging from the generated defaults table rather than a
family list; the list had missed 98 blocks and given the bell a property it
does not have.
A write of an old name is air wherever the game does not rename it on
load -- one a stair or vault rule copied off an old building included
-- and every write a Builder makes is held to the game's own registry,
an id it never had and a value its block does not take alike
(`Builder(allow=)` passes a modded block): Builder refuses it.

**Cave air is air, on both sides of a rule at once.** The carvers wrote
`cave_air` into every cave until 1.18, and a chunk keeps it through every
upgrade since; dungeons, mineshafts, strongholds and lakes still write it
(today's carvers write plain `air`, through the aquifer). `void_air` is
what the game answers past the world's edge. All three are its own `air`
tag, `blocks.AIR_BLOCKS`, and `isAir` is true of each, which is what the
game's block rules ask -- though a handful of places in the 26.3 jar test
for plain `air` by identity, among them `execute if blocks ... masked`,
which skips plain air only. The older builders asked for plain
`minecraft:air`, the newer ones kept a set of their own, and craft had
three such sets. So `spiral_stair` refused a shaft through an old cave,
`hang_lamps` refused a cave as a room and then hung lamps from a ceiling
of cave air, an ornamental tree grew no canopy into one, and a stair read
out of a cave counted the cave air between its steps as furniture and the
cave air in its unfinished level as a second tread material. Emptiness is
one predicate now, `build._common._empty`, and the writers and the readers
moved to it together: a writer that builds into cave air beside a reader
that refuses it lays a stair nobody can continue.
`tests/test_blockinfo.py` reads craft's source and fails on a name
compared with plain air (`AIR` or `"minecraft:air"`, by `==`, `!=`, `in`
or `not in`) and on any string naming `cave_air` or `void_air`, bare or
whole. The generated tables are exempt, and survey's two tables of bare
names that list the airs among what is not air (what a body walks through,
and what a falling block drops through) are named exceptions that fail as
stale once they excuse nothing. A *write* of air is still plain
`minecraft:air`: clearing is not carving.

**A felled tree goes to its roots, and only the open column over the
ground is cleared.** `clear_vegetation` swept 26 blocks up from the ground
and felled the ground itself only if it was a log, one block of it. A
rebuilt map was `WORLD_SURFACE` alone then, which tops out on the canopy,
so over ground built in an earlier session the "ground" was the top leaf:
the tree lost that leaf and kept four logs standing, a leaf over open
ground left the column's ground in mid-air, and a jungle giant kept
everything above 26. Its two hand lists missed every sapling, every
`_wood`, the nether stems and two dozen flowers. It sweeps up to the
column's own top now, and walks down from a ground read off a trunk, a
canopy or a torch to the block the game's map will land on once they are
gone, so the cells cleared *and* the ground left are the same with the
game's maps or a rebuilt one, on every finished chunk of both fixtures.
The first pass at that got three things wrong. A bound of
`WORLD_SURFACE` alone stranded a canopy the same session had planted,
because no Builder write moves a map; a chunk the session has built in
is read from its blocks. A pale oak grows a creaking heart into its trunk
in place of a log, so the felling stopped on it halfway down until it was
a tree too. And air written over a waterlogged leaf or lichen is a hole in
the pond: the game puts the water back when it removes one, and so does
the clear. Hanging roots and glow lichen are cover by the game's
`replaceable_by_trees` tag, and they cover cave ceilings as much as
cliffs, but nothing below a column's ground is touched -- roots under an
overhang whose top is the ground stay, while roots hanging into the open
column over a pad set beneath one go -- and the sweep up stops at the
first ledge or roof, so what grows on top of one stays too. A huge
fungus's shroomlights are left, since a shroomlight is a lamp as well,
and a village's log walls are logs and come down with the trees. Beside
it, `smooth_terrain` raised a column with whatever lay under its
surface, so a hollow under the grass put cave air into the hill and a
vein put diamond ore into it; it copies only what `subsoil_of` calls
ground now, and dirt otherwise -- which needed snow block, calcite,
dripstone, soul sand and soil and end stone in that table too, or every
snowfield, soul sand valley and End island it raised would have had a
seam of dirt. And `Vault`'s family was a two-way prefix test, so stone
bricks under a stone vault were more roof and stone was a stonecutter; it
is `material_of`, by whole name.

**A stair is only climbable if the floor above lets your head through,
and the scripts that cut the hole by hand got it wrong every time.** None
of the Roman town's insula stairs could be climbed past the first tread:
each story's run reversed in the same column and only two cells of the
slab above it were cut. A wall torch in the stairwell is no obstruction,
though: head room is what a body can pass, as the walk judges it. The castle example stacked its runs in one pair of
columns, so every flight stood in the head room of the one below. Cut rows
must cover `HEAD_ROOM` treads, not two. `Builder.flight` sizes the opening
from `claimed()`, refuses a flight stacked into an earlier one, and walks
the plan both ways before writing.

**A gate approach of whole-block steps is a jump at every step.** The
park's gates (`tools/park/build_park.py`, `approach`) were made walkable
by raising or cutting each column on its own until no step was more than
a block, and JeffWorld's north gate reads 61, 62, 63 (read 2026-10-22).
A block's top is a whole block up and a walker rises only 0.6 unbidden,
so each of those steps is a jump: from the park's edge at (-9, 61, 172) a
walk that may not jump reaches 8 cells, the edge row and one beside the
gate, while one that may reaches the road at z=168. The south gate, cut
column by column, reads 62, 61, 60, 59 down one column and 62, 63, 63,
63 down the next, so no two neighbors agree where the step is. An area
map and a floor plan draw both gates open. `Builder.approach` lays one
level a row across its whole width, a stair or half a block a row
higher, its last row flush with the high end -- a tread one under a
full-block end is a jump onto it, the rule `StraightStair` keeps -- and
its first held level under a lintel, and walks the plan both ways with
`allow_jump=False` before it writes; over the park's own bank it lays the
park's profile, cut and fill, with a stair where the park left a block
to jump.

**Attached blocks hang by their state, and a wrong facing is invisible.**
A ladder attaches to the block *opposite* its `facing`. The Roman scripts'
`ladder_shaft` wrote `facing=north` against walls that stand to the north,
so all 337 of the town's ladders faced into their walls, ready to drop at
the next block update nearby. Seventeen lanterns there were `hanging=true`
while standing on columns. Every render drew both correctly. `craft.unsupported`
checks every attached block against the game's sturdy faces from
`block_facts`, and `finish()` runs it. An iron door needs an opener on the
side you come from, on a block that conducts: a button on glass sticks and
opens nothing. Of the secret lab's five iron doors, two opened from neither
side and three from one side only. Both builds were repaired 2026-10-20 by
`tools/roman_city/repair_audit.py` and `tools/secret_lab/repair_audit.py`,
under the worksites `roman-town-repairs` and `lab-repairs`.

**A plant stands on its class's tag, and a name guesses the class and
the water both.** A plant the game keeps asks one block tag of the block
under it (`VegetationBlock.mayPlaceOn`), and which tag is its block
class's, not its id's. Survey's tables had guessed: an azalea went
unjudged (`AzaleaBlock` reads `supports_azalea`); a bamboo shoot was held
to the saplings' soil because its id ends in `_sapling`, when
`BambooSaplingBlock` is no sapling and reads `supports_bamboo`, which
takes sand; and sugar cane was watered only by blocks named water, while
seagrass, kelp and a bubble column are water to the game's `#water`, so
cane beside them stood the whole time -- of the canes JeffWorld still
has dry, all but two stand beside plain ice, which waters nothing (javap
and a read-only scan, 2026-10-22). Each footing is a copy of its tag now,
held to the vendored one by `tests/test_survey.py`, with every block it
judges on that soil named so a new one fails by name; the shoot, whose
tag is not vendored, is left unjudged as a named choice rather than
judged wrong; and the wetness test is `holds_water`. The azalea rule
found ten in the secret lab the game drops at their next neighbor update
-- eight on polished deepslate planters at y=68, two on the HQ's black
concrete at y=82 -- and they were given `minecraft:rooted_dirt` under
them on 2026-10-22, under the worksite `lab-azalea-soil`. The gravel
paths are the same fault in another town: TestWorld (2)'s Roman
`garden_block` (`tools/roman_city/m4_districts.py`) put a flower on any
air cell, and ten stand on its gravel paths at y=-60 (read 2026-10-22) --
gravel is natural ground and no flower's soil, so the game drops them
too. The other half is what the game keeps and a builder does not want:
a lane of `LANE_MIX` is three parts in eighteen coarse dirt, which *is*
soil, so a planting judged by the block alone would flower a sixth of the
lane. A path is something a session laid, not something a block is:
`scatter` keeps off what `road`, `pave` and `approach` recorded as paved,
an earlier session's paths go in its `avoid=`, and the park kept its own
record of what it paved for the same reason.

**A seat is named by its back, because a stair faces the way its back
points.** A stair's full side is its `facing` side -- in `block_facts`,
`oak_stairs[facing=west,half=bottom]` is sturdy west and down and nowhere
else -- so a stair set as a chair has its backrest on the side it faces
and its seat open the other way. The suburban house's furniture recorded
the rule (`tools/suburban_house/furniture.py`) and kept it; the Roman
scripts named their benches by the way the sitter looks, "facing in"
(`m2_forum.py`), and every one came out backwards: in JeffWorld the
curia's benches at (415, 65, 380..388) and (423, 65, 380..388) are
`spruce_stairs` facing east and west, their backs to the floor and their
seats against the walls at x=414 and x=424, and the basilica's bench at
z=346 faces south, its back to the nave and its seat to the north wall a
block away (read 2026-10-22;
`tools/roman_city/jw_monuments.py:93,112-113`). The castle example's
throne, `facing=south` on the dais against its hall's north wall, sits its
king facing the wall -- read from the code, and the example keeps it. A
top-down render draws a stair the same whichever way it faces, and a
cutaway shows a step, not the side you sit on. `craft.build.Piece.seat`
takes `back=`, never a facing, and refuses a stair that names its own;
`tests/test_furniture.py` holds it to the game's sturdy faces and to the
suburban chairs and sofas, and builds the curia bench as the backwards
seat it is.

**Blocks are not items.** Writing states is not placing things: fences,
walls, panes, bars and stair `shape`s carry neighbor-derived state the game
only recomputes on a *block update* (bare ones render disconnected); beds and
doors are **two blocks**, and a lone half breaks on the game's next update;
water displaced by a write does not waterlog anything. All found by walking a
freshly built town. Build with `save.place_block` (halves, connections and
waterlogging in one call), or run `save.connect_blocks(c1, c2)` over
bulk-built areas the way `reset_lighting` is run over edited ones.

**A wall reads the block above it by shape, and a name cannot say which.**
"Validated against 22,616 vanilla blocks" was true of the walls in the save
it was checked against -- ruins under air, stone and other walls -- and the
wall rule still disagreed with the game on 162,906 of the 571,568 pairings
of a block state above and a joining of the four sides. It took "tall" from
the attachment table, which rejects leaves (a full cube nothing attaches to)
and every slab, and it raised a post under anything that was not air, solid
or a wall -- a top slab, an open trapdoor, a carpet. It also never read a
posted wall above, which raises the post outright, so `connect_blocks` and
`finish()` would have rewritten JeffWorld walls under a spruce and under a
posted wall into states the game would not have set. The game's answer is
the above block's collision underside against four strips and a post, plus a
tag, so it is a generated table now (`heaton_voxel/wall_cover.py` and its
twin, from `tools/generate_wall_cover.py`), and the rule is the game's
`shouldRaisePost`, in its order: 0 disagreements on the same sweep, which
the generator reruns against the game's own `updateSides` and
`shouldRaisePost` by reflection every time, putting the old table back on
the first disagreement. The attachment rule (`_face_full`, what fences, walls and panes join as a
solid neighbor) stayed a list of name endings for longer than it should
have. A phase-4 review caught it rewriting two of the fixture village's own
walls, and the game's `Block.isExceptionForConnection` went in by name, but
it was still wrong on 10,308 state-sides: it joined 3,188 the game does not
and missed 7,120, on stems, copper, concrete powder and every full cube no
ending named. The note here said the cure was a table generated from the
game, and it took the first city to make it happen: a copper tower in
CastleWorld (2026-11-01) stood with every pane stopping short of its piers,
680 sides within a few blocks of Jeff, since `waxed_oxidized_copper` ended in
nothing on the list, and a pane does not re-read its neighbors until a block
update. Every top-down render and every check drew it finished, because a
render cannot see a pane's arms and no check asks: Jeff saw it from inside the
building. `heaton_voxel/connect_faces.py` and its twin are written from the
probe's `SupportType.FULL` faces by `tools/generate_connect_faces.py`, held
state by state by `tests/test_connect.py`, and a stair stays read by its
`facing`, since its `shape` is derived in the same sweep.

**Roads carved through buildings, and top-down renders hid it.** A lane
routed through a lot cuts a channel under the roof; `render_area` shows the
intact roof, so the damage is invisible from above. `build.Builder.road`
now refuses swaths that enter a registered pad (`through=` for deliberate
crossings) — use it, and verify interiors with `render_slice`, not just
area maps.

**A paste moved the building and none of the maps, and every picture
agreed with the maps.** The app pasted a five-story building into
FlatWorld on 2026-11-01: 41,860 blocks, relit, saved. `import_schem`
relit and stopped, so `WORLD_SURFACE` stayed wrong in 1,487 of the box's
1,610 columns and `MOTION_BLOCKING` in 1,515. The app's map and
`render_area` drew bare dirt under the roof, and a height scan by
`surface_y` found a few stripes: the columns the game had rewritten since
were the rooftop daylight detectors, whose states it changes. Nothing
raised, because the blocks were all there. A rebuild that kept only
`WORLD_SURFACE` was half a cure too: plain `surface_y` then answered
`None` until the game next loaded the chunk, which the app read as "never
generated: nowhere to stand". So `rebuild_heightmaps` writes all four now,
by the game's tags, and over both fixtures it is byte for byte what the
game stored; `import_schem` rebuilds the chunks it moved; and `render_area`
says when a map disagrees with the blocks. A read of real regions the same
day shows what those maps look like after years of this: 44 columns wrong
in one JeffWorld region, about 77,000 in one of CastleWorld, each read
against the blocks. `craft.audit` asks the rest of what a paste never
asked, and its first run found water on the fourth story of that building,
52 of 53 cells moving from a source at (6, -44, 15), left as it is: the
log does not say where the paste came from, so nothing tells whether the
flood came with it.

A paste also stays out of a chunk the game has not finished, reported in
`unfinished_chunks` beside `missing_chunks`, since the game generates over
what is written there; FlatWorld held 3,652 of those in 8,835 chunks a few
minutes after it was made. Note what `rebuild_heightmaps` no longer does to
such a chunk. A chunk set aside from its terrain stage on stores all four
maps, and the column reader trusts them at any status, on purpose:
`test_a_partly_generated_chunk_is_still_probed_while_it_has_maps` pins it.
So `walk_surface` answers over a `terrain` or `initialize_light` chunk, and
only a writer's `_commit` refuses to build there.

**The smoothing passes never did anything.** Road smoothed the ground
under its centerline in three rounds, the Roman wall
(`tools/roman_city/jw_groundwork.py`, `smoothed_line`) in four and the
lab's curtain wall (`tools/secret_lab/m1_groundwork.py`, `_wall_tops`) in
three: a sweep forward letting each height rise at most a block past the
last, then one back, repeated as if each round smoothed a little more.
None did. One sweep each way already is the lower envelope,
`min_j(p[j] + |i - j|)` -- each height lowered exactly as far as some
neighbor needs it -- so a second round reproduces the first; pinning the
anchors again after it changes nothing, since the triangle inequality
keeps a re-pinned anchor from lowering anything further. All three loops
were checked against it over 20,000 random profiles, and the closed form
over 60,000 more, before `geometry.smoothed_profile` took road's place:
it has no `passes=`, and `tests/test_geometry.py` runs road's loop as it
stood and the two walls' compiled out of their own source beside it. The
envelope only lowers, so a high anchor stands a step above its neighbors,
as it always did on a road, and `RoadResult.heights` shows the step. It is
the profile, not the surface laid along the centerline: a column is cut by
the first stretch that reaches it, so a road three wide lays its profile a
cell further along, and a hedge that followed `heights` stood a block off
the road's edge at every change of level. What a follower wants is
`[r.top(*cell) for cell in r.line]`.

**26.3 stores a default state as its bare id, and the id alone is not the
state.** From DataVersion 5006 (`BlockStateFieldNamesFix`) a palette entry
is `id`/`properties` instead of `Name`/`Properties`, a state equal to its
block's default is written as just the id string (`minecraft:water` *is*
`water[level=0]`, `minecraft:grass_block` is `snowy=false`), and a list
holding both forms wraps each bare id as `{"": id}` so the NBT list stays
one type. Reading the id as "a state with no properties" makes every
default fence, stair and grass block look different from the same block
in an older chunk, so `connect_blocks` rewrites them, digests trip, and a
stair rule read off a 26.3 building matches nothing. The library
therefore restores the defaults on decode from a table generated from
the game's own `reports/blocks.json` (884 blocks carry properties), and
writes bare only what equals its defaults. The form is chosen **per
chunk from its DataVersion**, never from the game version: a world played
across an update holds both codecs side by side, and each chunk must be
written in the form the game will read it in. The jar is unobfuscated;
`javap -p -c` on `net.minecraft.world.level.block.state.BlockState`
answers a codec question in seconds, and `net.minecraft.data.Main
--reports` (launcher JRE, classpath from the version's json) is how the
defaults table is regenerated.

**Ice over air is a lid, and light opens it.** The frozen lake had 71
ice cells sitting directly on air — rooms and caves cut under the lake
bed with the ice as their ceiling — when it was lit on 2026-09-21
(`tools/frozen_lake/plan.json`, counting the open lake its mask kept in
the box x -392..-278, z 152..288). Ice melts at block light 11, so
lighting the lake to thaw it would have poured the lake into them. A
lid must be held at 10 or less, and a source has to stand far enough
away that its light has fallen to 10 by the time it arrives (five blocks
for a surface pickle cluster, four for one a block down); the cells
between lids then stay frozen on purpose. `simulate_block_light` with
the plan as an `overlay=` is what finds this before anything is written,
and `plan_lights(..., at_most=dict.fromkeys(lids, 10))` places the
sources so: its taxicab ceiling is exactly that five-and-four rule.
`Builder.basin` asks it of its own lamps without being told: every
`minecraft:ice` over air within their reach (`find_blocks`, then the
taxicab bound from the places a lamp could go, a lamp of light `L`
reaching past 10 only within `L - 11`) is held at 10, and a basin its
lamps cannot light without melting one is refused. A lid the world
already lights past 10, and still would with the basin laid, melts
whatever its lamps do, so it is named in `BasinResult.melting` and neither
held nor refused on: the basin answers only for the light it adds, as
`_audit_plan` does for what it leaves -- and a lid lit by lava the basin
fills with water is held like any other. Read again on 2026-10-22,
`water_body` finds 95 lids in that box: 47 of the plan's 71 (the rest
have thawed onto water since, most of them round (-362, 228)) and 48 in
water the plan's mask left out, a strip along x=-326 among them -- a
count is a date and a definition, not a fact about the lake. A
top-down render draws ice over air and ice over sand the same color.

**A body of water is joined under its surface, so a flood at the
waterline is not the lake.** The floods written by hand in `tools/` --
`lake/lakelib.py`'s `surface_flood`, `lake/lakeplan.py`'s mask, the frozen
lake's plan and survey -- each walked one plane, the waterline, column to
column, which is the lake as a picture from above draws it.
`craft.water_body` floods in three dimensions through every cell that
holds water as the game loads it, and the bodies come out bigger than the
pictures. JeffWorld's big lake (seeded at (-596, 62, -56)) takes in the
fortress moat beside it, whose surface stands a block higher at 63,
through water that meets under the waterline: 1,182 of the lake's 38,177
columns lie in `lakeplan.py`'s protect box for that moat, and of the
columns at the waterline the body has three that lakelib's flood at 62
never reaches (38,084 against 38,081), joined only below it. The frozen
lake leaves its own box at z=152 and drains down a one-wide shaft to
y=6, and unbounded passes two million cells; the walled city's moat is
6,870 cells within its box, 12,513 within sixteen of it, and has joined
frozen water by sixty-four. So `water_body` is bounded on purpose --
`within=` the rectangle the question is about, and past `limit=` a
`Refused` that says to pass one -- and filtering its `columns` by
waterline is what gives one surface; a writer that follows water, as a
road's split check does, bounds itself the same way and refuses water it
could not follow far enough to say.

**Falling water is amount 8 at every level, and flowing water never
waterlogs -- but two sources do.** `LiquidBlock.getFluidState` reads
`min(level, 8)`, so `water[level=9]` and `water[level=15]` are the same
fluid as `level=8`: falling, amount 8, and reaching the sides with 7
wherever it lands. Counting a falling cell as 8 less its level past 8
calls the level-9 water beside the Roman town's drain a 7, and reports a
flow it feeds as cut off when the game keeps it.
A container -- a stair, a slab, a fence -- takes only a source
(`SimpleWaterloggedBlock.canPlaceLiquid`; never a double slab), so a dry
stair beside one source, or beside any flow, is a wall the water stops at.
But a cell two sources reach through their walls, over a block that is
solid or a source, becomes a source itself (`getNewLiquid`, under the game
rule `WATER_SOURCE_CONVERSION`, on by default), and a dry container there
is waterlogged: a new source where the plan drew a dry step. And the tag
water asks a cell for, `washed_away_by_fluids`, holds water and lava
themselves, so "can water enter it" is true of a flow beside a flow --
`contained` asks "holds no water" besides, or it reports water running
into water. `survey`'s one amount model is what `contained` and `starved`
both read, and `tests/test_survey.py` holds every level 8..15 to it both
ways. The model reports every side the game's rules let water into, where
the game takes only the sides nearest a way down -- a refusal it would not
need, never a leak missed. A plan of the world's own blocks has no leaks
by construction, so what checks the model against real water is what it
puts in `standing` from such a plan: over the cells of JeffWorld's settled
big lake, city moat and frozen lake (377,836 of them, read-only) that is
74 sides, and the game's own `getSpread` accounts for every one -- 57 are
that over-report, and 17 are cells the game itself will run water into at
its next block update there. Those 17 are **not repaired**: settling or
sealing them is world work, and the owner's choice. Nine are air at z=0
in the ruined portal beside the big lake, (-737, 62, 0), (-736, 62, 0),
(-734, 62, 0) and (-729..-724, 62, 0), each beside one of the lake's
sources at z=-1 -- six over the portal's blackstone, and the three at
x=-736, -734 and -726 over air; (-754, 63, -1) and (-753, 63, -1) are beside
a bank's falling water at z=0; (-561, 63, -4) beside the fortress moat's
edge at (-560, 63, -4); (-518, 63, -31) and (-517, 63, -32) beside a
source in the sand bank at (-517, 63, -31); (-270, 66, 238) beside the
city moat's source at (-269, 66, 238); and (-350, 45, 221) and
(-348, 45, 222), over a hole by the frozen lake, beside a flow at
(-350, 45, 222) and falling water at (-349, 45, 222) (all read
2026-10-22, and again on 2026-10-23).

The walled city's moat is the falling-water shape at its largest: eleven or
twelve blocks of `level=8` under one skin of sources, poured in from the
top, so it is not `still`, and no bubble column stands in it --
`BubbleColumnBlock.canOccupy` takes a liquid block only where its fluid is
a source (javap, 26.3), so magma on its bed raises a column no further than
the sources at its foot. No writer that checks its water lays that shape:
a source written over air falls, and `contained` names every one a
`"falls"` leak, so the plan is refused (`set` and `fill` still write what
they are told). `Builder.basin` is the replacement -- every cell a
source, from a floor up, the floor lined over any hole -- and a moat laid
by it is still from bed to surface (`tests/test_basin.py` lays one twelve
deep in the trench the skin would have fallen into).

**Water meets a wall at the grid layer on the face, and only the game's
own test can say which.** Whether water passes from one cell into the
next is `FlowingFluid.canPassThroughWall`: the two collision shapes'
faces where the cells meet, merged, must not cover it. Water's design
read a face as `getCollisionShape().getFaceShape(face)`, and the probe's
own proof refused that model: a pointed dripstone, offset at the origin,
keeps an empty grid layer outside its cell, so the game lets water pass
north into an extended sticky piston where the face shapes say the two
together are whole. The game's `Shapes.mergedFaceOccludes` cuts a shape
at its own grid layer nearest the face, and only where the shape ends on
it, so a fence, a block and a half tall, shows its top nothing. And
chains and bamboo collide on thirty-seconds, which the light-face reader
refuses outright; the water masks count only the sixteenths a shape
covers whole, rounding inward. None of it is taken on trust:
`tools/probe_block_shapes.py` runs the game's private `canPassThroughWall`
itself over every ordered pair of the 338 distinct collision shapes along
all six directions -- 685,464 calls -- and writes nothing on one
disagreement, and it calls `canHoldAnyFluid` by reflection rather than
restating it, whose tag holds water and lava themselves (above).

**A drain's magic floor writes into the ground, and the water it leaves
beside it comes back.** The two drains written by hand -- the Roman town's
(`tools/roman_city/jw_groundwork.py`, `drain`) and the secret lab's
(`tools/secret_lab/m1_groundwork.py`, `wetfill`) -- took the bed to fill
from off `OCEAN_FLOOR`, and where that map stood at the plane or above it
fell back on a floor nothing measured, 58 and 60, and filled dirt from
there up. On the pristine snapshots (read-only, 2026-10-22) that floor lay
under a bed at the plane or above it in 151 of the Roman loop's 1,887
columns and 66 of the lab's 3,573: 604 and 264 cells under the bed
rewritten with dirt. Each cut the water over its plane to air, beside
water it left standing, which runs back in at the next block update --
the lab's plaza was laid over it, and 22 cells of it still stand at y=65
under the plaza's smooth quartz (JeffWorld, read 2026-10-22) -- and a fill
can take away a flow's feed, which the game re-reads and dries
(`getNewLiquid`; a source it never re-reads). `Builder.grade` is a drain
now: `drain=True` stands every wet column on the bed the column reader
finds, refuses a lid, refuses a fill that starves flowing water
(`survey.starved`) unless `allow_dam=True`, refuses a cut that leaves
water against air unless `seal=` walls it off where it stands, and names
the water it left `beside_drained`. `tests/test_grade.py` runs both loops
as grades (Roman: `cut=False, fill=False, drain=True, retain=None,
clearance=0`; lab: `cut=False, fill=True, drain=True`) over a pond whose
bed is the plane, and nothing under a bed moves; a target under a bed
takes the column down to it, bed and all, as a cut would, and is counted
drained. The scripts stay historical.

**Water a script wrote stands until something updates beside it, so its
leak is invisible until it happens.** The game moves water on a scheduled
tick, which `LiquidBlock.onPlace`, `updateShape` and `neighborChanged`
set (javap, 26.3; water has no random tick), and a block written outside
the game schedules none: it stands where it was put until a block beside
it changes, and then runs. JeffWorld's aqueduct
(`tools/roman_city/jw_groundwork.py`, `aqueduct`) holds its channel at
(477, 73, 451..495) with its south end open onto air at z=496 -- nothing
of the arcade or its springhouse stands south of it -- and the
castellum's spout basin at (467..472, 64, 442..448) stands over an air
trench at y=63, x 469..471, fourteen of its twenty cells of water over
nothing and one more on an oak log (read 2026-10-22: `contained` over the
world's own cells puts each of the fourteen in `standing`, beside the
spout's own fall from (472, 69, 445), the fifteenth it names there). Both
have stood since they were written, and both pour the first time a block
beside them changes. So the writers that lay water
check it before anything is written -- `extrude` refuses a channel's open
end unless `spill=` names the cells it falls through, `furnish` a tub
with a gap or a spout running past its piece's `spill`, `basin` a gap in
its bank -- and a channel is laid level, since one laid down a slope runs
out of itself at every step. JeffWorld's Roman fountain at (350, 350) is
the other side of it: the source on its pillar runs into exactly the
eight cells beside and under it, and with those as its spill it is held.
Neither leak is repaired: world work, the owner's.

**A mix is a proportion and a picture, and a die keeps only one.** A die
per cell gets the weights right and the picture wrong: television static.
The lake bed was redone for it (`tools/lake/rebed.py`), and the Roman
forum floor in JeffWorld still is static — of its 2,913 paved cells, 24.9%
of neighbors match, against 29.6% for independent draws from its mix.
Thresholding smooth noise at the weights gets the picture and loses the
weights, because value noise piles up mid-range: over `ROAD_MIX`, andesite
goes from 25% to over half and mossy cobblestone from 5% to none.
`craft.style.Mix(clump=)` rolls once per `noise.patch` instead, which
keeps both.

**A mix is a field, not a draw.** A list of `(block, weight)` pairs is
drawn from the session's `rng` in write order, so every list-mixed surface
is a function of every draw before it: paving one more row of a 6×6 floor
early in a session re-rolled 96 of the 131 surface cells of a road laid
after it -- as many as two unrelated rolls would. A `Mix` handed to `set`,
`fill`, `level_pad`, `road` or `pave` is picked at each cell it lands in,
so the same Mix over the same cells is the same blocks in any session, and
the same bytes, because a Mix fill is a sweep in `y, z, x` order, never a
`save.fill`, and the order a sweep writes is its palette's (see "Set
iteration order reaching the bytes" above). The lists stay
lists, drawn exactly as before, since changing how they draw would change
every build already made with them. And a pick belongs to a cell, so a Mix
is never one exact state: a placement of one, or a stair, vault or
railway rule given one, is a `TypeError`, because which cells a door
writes and what a stair replays depend on the one block each is. (The
rules that came later -- `Roof`, `Facade`, `Opening`, `battlement`,
`Piece` -- take one in their whole-block roles, picked at each world cell
as the plan is made, and nothing replays it.) Teaching the plan's name check to read a
Mix took away the early refusal every one-block argument had been getting
from it for free. A flight's `rail=` is set only where the floor beside the
well is found, so a Mix rail over a well with no floor round it was taken
without a word and the flight built. Each such argument now refuses a Mix
itself, before anything is read, and `tests/test_style.py` holds each one
to that with any chunk read made to fail.

## Design decisions worth not relitigating

**Change detection is a content hash, not dirty flags.** `NBT.is_modified()`
serializes and compares against the hash taken at load. Flags at the editing
entry points would be marginally faster and *wrong* — nothing stops
`chunk.data["x"] = y` bypassing them, and a save that trusted a flag would
silently drop the edit. Measured cost of hashing a 62 MiB world: 61 ms.

**Laziness is layered.** Opening a save indexes region files without reading
them; a region reads chunk bytes without decoding them; a chunk decodes on
first access to `.data`. `release()` flushes edits back into bytes and frees
the tree, so a whole-world pass stays flat in memory.

**Iterating a dimension opens every region.** `for region in dim` deliberately
reads unopened files; a sweep that silently skipped them would look like a
clean pass over an empty world. `dim.loaded_regions()` is the cheap subset.

**Writes are atomic.** Temp file in the same directory, `fsync`, `os.replace`.
A plain write truncates first, so an interruption would destroy a multi-megabyte
region with no copy anywhere.
