# tools/

Two kinds of script live here.

## Library tooling

These write or check files this repo commits. Most of them ask the game
itself, so they need a Minecraft Java Edition install and its launcher's
runtime.

| script | what it writes or checks |
| --- | --- |
| `generate_block_defaults.py` | `heaton_voxel/block_defaults.py` and `csharp/HeatonVoxel/Blocks/BlockDefaults.generated.cs`, from the game's own block report |
| `probe_block_shapes.py` | `fixtures/block_shapes.json`, `heaton_voxel/craft/block_facts.py` and `heaton_voxel/craft/block_registry.py`, from the jar |
| `generate_wall_cover.py` | `heaton_voxel/wall_cover.py` and its C# twin |
| `generate_connect_faces.py` | `heaton_voxel/connect_faces.py` and its C# twin, from the probe's output (no game needed) |
| `generate_heightmap_blocks.py` | `heaton_voxel/heightmap_blocks.py` and its C# twin, from the probe's output (no game needed) |
| `probe_legacy_chunks.py` | `heaton_voxel/legacy_biomes.py`, its C# twin, and `fixtures/legacy/upgraded.nbt` and `older.nbt` |
| `probe_old_layout.py` | `fixtures/old_layout.json` |
| `probe_snbt.py` | `fixtures/snbt.json` |
| `probe_structures.py` | `fixtures/structures.json` |
| `check_litematic.py` | `fixtures/litematic/`, and checks the `.litematic` reader and writer against litemapy |

CLAUDE.md's "Verifying" section gives the order to run them in after a game
update.

## Reference builds

`roman_city/`, `secret_lab/`, `park/`, `lake/`, `frozen_lake/`,
`suburban_house/` and `yellow_tower/` hold the parts of real world builds
that the tests load, kept byte for byte as they were run. Craft's rules
were promoted out of these scripts, and the tests hold each rule to the
cells its script laid: a disc, a roof, a bench, a smoothing pass, a lake
bed. They are oracles, not tools: they were written against one person's
saves (some name `~/Library/Application Support/minecraft/saves/JeffWorld`
outright), and the scripts that imported them to build those worlds are not
published. Don't run them, and don't edit them: `tests/test_survey.py`
reads two of them by line number.
