"""Regenerate the vendored test fixture from a real Minecraft save.

    python fixtures/make_fixture.py <region.mca> <level.dat>
    python fixtures/make_fixture.py <region.mca> <level.dat> --out fixtures/modern \\
        --chunks 6 --min-data-version 5006

Chunks are copied verbatim -- their compressed payload, their record and the
stale slack bytes in the tail of their last sector -- and each keeps its
original position in the region so its own xPos/zPos stay consistent with the
file name. Nothing here synthesizes NBT; the point of the fixture is that it
is real game output.

Selection covers every packed bit width the source region contains, rarest
first, then fills up with chunks carrying slack bytes and a couple of
partially-generated chunks for Status variety.

``--min-data-version`` keeps only chunks the game has saved at that
DataVersion or later, which is how ``fixtures/modern/`` was cut: a world
that has been played across an update holds both palette codecs side by
side, and a fixture for the newer one must not pick up the older by
accident. ``--max-data-version`` keeps only those at or before it, which
with the other is how ``fixtures/legacy/`` was cut: the 1.17 chunks of a
region OldWorld kept from 1.17.1, read-only to the library::

    python fixtures/make_fixture.py <OldWorld>/dimensions/minecraft/overworld/region/r.5.-1.mca \
        <OldWorld>/level.dat --out fixtures/legacy --min-data-version 2724 --max-data-version 2730

``--keys`` names the chunks to keep instead, by their local ``x,z`` in the
region, which is how ``fixtures/legacy/1.16.5-end/`` was cut, since what
its region varies in is not bit width (every section there is one block,
packed at four bits) but whether a chunk holds any blocks at all::

    python fixtures/make_fixture.py <OldWorld>/dimensions/minecraft/the_end/region/r.-1.-1.mca \
        <OldWorld>/level.dat --out fixtures/legacy/1.16.5-end --min-data-version 2566 \
        --max-data-version 2586 --keys 28,12 15,17 31,12 27,13 30,10 20,22 22,2 31,8

``fixtures/legacy/1.15.2/`` is the 1.15.2 chunks of OldWorld's overworld
``r.0.-5.mca``, ``--min-data-version 2225 --max-data-version 2230``, and
the 1.15 chunks it holds all chosen.

``--out`` and ``--chunks`` place and size the result.

You only need this if you want to rebuild the fixture from a different world.
The checked-in files are enough to run the suite.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heaton_voxel.blocks import Blocks  # noqa: E402
from heaton_voxel.nbt import NBT, Chunk  # noqa: E402

TARGET_CHUNKS = 16
OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def survey(region: NBT, min_data_version: int = 0,
           max_data_version: int | None = None):
    """Describe each chunk: status, slack, bit widths, biome arrays.

    Chunks below ``min_data_version`` or above ``max_data_version`` are
    left out entirely, as if the region never held them.
    """
    rows = []
    for chunk in region:
        data = chunk.data
        if data is None:
            continue
        version = int(data.get("DataVersion", 0))
        if version < min_data_version or (
                max_data_version is not None and version > max_data_version):
            chunk.release()
            continue
        widths, biome_arrays = set(), 0
        if "sections" in data or "Level" in data:
            try:
                for section in Blocks(chunk):
                    if section.blocks and "data" in section.blocks._tag:
                        widths.add(section.blocks.bits)
                    if section.biomes and "data" in section.biomes._tag:
                        biome_arrays += 1
                        widths.add(section.biomes.bits)
            except Exception:
                pass
        # A 1.17 chunk keeps its stage under Level, and without the
        # namespace; both read as the one stage here.
        status = data.get("Status", data.get("Level", {}).get("Status", ""))
        rows.append(
            (
                chunk,
                "minecraft:" + str(status).removeprefix("minecraft:"),
                bool(chunk._padding.strip(b"\x00")),
                widths,
                biome_arrays,
            )
        )
        chunk.release()
    return rows


def choose(rows, target: int = TARGET_CHUNKS):
    full = [r for r in rows if r[1] == "minecraft:full"]
    other = [r for r in rows if r[1] != "minecraft:full"]

    frequency: dict[int, int] = {}
    for *_, widths, _ in rows:
        for width in widths:
            frequency[width] = frequency.get(width, 0) + 1

    # Rarest width first: one that occurs once in a world is exactly the one
    # worth pinning down against regressions.
    chosen, covered = [], set()
    for width in sorted(frequency, key=lambda w: frequency[w]):
        if width in covered:
            continue
        candidates = [r for r in full if width in r[3]]
        candidates = candidates or [r for r in rows if width in r[3]]
        if not candidates:
            continue
        pick = max(candidates, key=lambda r: (r[2], len(r[3] - covered), r[4]))
        if pick not in chosen:
            chosen.append(pick)
            covered |= pick[3]

    for row in sorted(full, key=lambda r: (not r[2], -r[4])):
        if len(chosen) >= target - 2:
            break
        if row not in chosen:
            chosen.append(row)

    seen = set()
    for row in other:
        if len(chosen) >= target:
            break
        if row[1] not in seen:
            seen.add(row[1])
            chosen.append(row)
    return chosen, covered


def main(source_region: str, source_level: str, *, out_dir: str = OUT_DIR,
         target: int = TARGET_CHUNKS, min_data_version: int = 0,
         max_data_version: int | None = None, keys=None) -> None:
    source = NBT.load(source_region)
    rows = survey(source, min_data_version, max_data_version)
    if keys:
        by_key = {(row[0].local_x, row[0].local_z): row for row in rows}
        missing = [key for key in keys if key not in by_key]
        if missing:
            raise SystemExit(f"{source_region}: no chunk at {missing} in the "
                             f"DataVersions asked for")
        chosen = [by_key[key] for key in keys]
        covered = set().union(*(row[3] for row in chosen))
    else:
        chosen, covered = choose(rows, target)
    if not chosen:
        raise SystemExit(
            f"{source_region}: no chunks at DataVersion {min_data_version}"
            f"..{max_data_version if max_data_version is not None else ''}")
    os.makedirs(out_dir, exist_ok=True)

    out = NBT()
    out.kind = "region"
    out.region_x, out.region_z = source.region_x, source.region_z
    for chunk, *_ in chosen:
        copy = Chunk(
            chunk.local_x,
            chunk.local_z,
            timestamp=chunk.timestamp,
            compression=chunk.compression,
        )
        copy._blob = chunk._blob  # genuine Minecraft-compressed payload
        copy._padding = chunk._padding  # genuine stale slack bytes
        copy._sector_count = chunk._sector_count
        out.chunks[(copy.local_x, copy.local_z)] = copy

    destination = os.path.join(out_dir, os.path.basename(source_region))
    out.save(destination)
    os.chmod(destination, 0o644)
    shutil.copyfile(source_level, os.path.join(out_dir, "level.dat"))
    os.chmod(os.path.join(out_dir, "level.dat"), 0o644)

    print(f"wrote {destination}: {os.path.getsize(destination):,} bytes")
    print(f"  chunks           : {len(out.chunks)} "
          f"{sorted((c.local_x, c.local_z) for c in out.chunks.values())}")
    print(f"  bit widths       : {sorted(covered)}")
    print(f"  statuses         : {sorted({row[1] for row in chosen})}")
    print(f"  carrying slack   : {sum(1 for row in chosen if row[2])}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Cut a vendored test fixture from a real save.")
    parser.add_argument("region", help="source r.<x>.<z>.mca")
    parser.add_argument("level", help="source level.dat")
    parser.add_argument("--out", default=OUT_DIR, help="destination directory")
    parser.add_argument("--chunks", type=int, default=TARGET_CHUNKS,
                        help="how many chunks to keep")
    parser.add_argument("--min-data-version", type=int, default=0,
                        help="skip chunks the game saved at an older DataVersion")
    parser.add_argument("--max-data-version", type=int, default=None,
                        help="skip chunks the game saved at a newer DataVersion")
    parser.add_argument("--keys", nargs="+", default=None, metavar="X,Z",
                        help="the chunks to keep, by local x,z in the region")
    args = parser.parse_args()
    keys = [tuple(int(v) for v in key.split(",")) for key in args.keys] if args.keys else None
    main(args.region, args.level, out_dir=args.out, target=args.chunks,
         min_data_version=args.min_data_version,
         max_data_version=args.max_data_version, keys=keys)
