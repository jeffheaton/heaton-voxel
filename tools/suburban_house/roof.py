"""Close the hole in the roof over the southern third of the house.

The shell arrived with a side-gabled roof whose ridge runs east-west at
z=-5, y=79, its south eave landing at z=2, y=76 -- ten rows short of the
south wall. Behind that eave the upper floor was open to the sky from
x=-295 east: no ceiling, no roof, a wooden deck half laid at y=76 over
the western half and a single line of stone left standing at x=-296 where
the setting-out stopped.

Rather than invent a shape, the rear gable copies the rule the existing
roof is built to, read off it before anything was written:

    a course is stone, and its first row going *up* the slope is a
    stone_brick_stairs facing up-slope; the ridge is plain stone

and the eave heights are pinned to what the walls already give: the south
wall tops out at y=76, so the rear roof eaves at y=77 and rises to a
ridge at y=79, the same height as the main ridge. The two roofs meet in a
valley at z=2 -- which is what two parallel gables do, and it is why the
south eave was left where it was.

The attic that leaves between the y=76 ceiling and the roof is *filled*,
not lit. Two blocks of dark enclosed air over 38 by 10 is a mob farm with
a house under it, and nothing up there is ever seen.
"""

from __future__ import annotations

import house
from house import ROOF_STAIR, ROOF_STONE, CEILING


#: The rear gable, z -> the y of its roof surface. Eaves at z=3 and z=12,
#: a three-row ridge at y=79 the way the main roof's is at z -6..-4.
REAR_PROFILE = {3: 77, 4: 77, 5: 78, 6: 78, 7: 79, 8: 79,
                9: 79, 10: 78, 11: 78, 12: 77}

#: Rows that carry a stair rather than plain stone, and the way it faces.
#: A stair's full face is its ``facing`` side, so a stair faces *up* the
#: slope and shows its low half to the course below -- which is what the
#: existing roof does at z=-14, -11, -8, -6 (south) and z=-4 (north).
REAR_STAIRS = {3: "south", 5: "south", 7: "south",
               9: "north", 11: "north", 12: "north"}

#: One block of the main roof's south eave was never placed: the column
#: at x=-296, z=2 is open from y=76 to y=79. It is the valley between the
#: two roofs, so it closes with the stone its neighbors are made of.
VALLEY_HOLE = (-296, 2)

ROOF_X0, ROOF_X1 = -314, -277        # the roof is flush with the walls
CEIL_Y = 76

#: The ceiling Jeff had already laid, and the part that was still sky.
CEILING_DONE = (-313, -297)
CEILING_GAP = (-296, -278)
CEILING_Z = (3, 11)


def clear_above_rear(builder) -> int:
    """Strip everything above the new roof line, including the stray
    setting-out course at x=-296 that climbed from y=77 to y=79."""
    removed = 0
    for z in range(3, 13):
        top = REAR_PROFILE[z]
        for x in range(ROOF_X0, ROOF_X1 + 1):
            for y in range(top + 1, 84):
                state = builder.save.get_block(x, y, z)
                if state is not None and state.name != "minecraft:air":
                    builder.set(x, y, z, "minecraft:air")
                    removed += 1
    # The valley column, which stood proud of the main roof by three.
    vx, vz = VALLEY_HOLE
    for y in range(CEIL_Y + 1, 84):
        state = builder.save.get_block(vx, y, vz)
        if state is not None and state.name != "minecraft:air":
            builder.set(vx, y, vz, "minecraft:air")
            removed += 1
    return removed


def lay_ceiling(builder) -> int:
    """Finish the y=76 wood ceiling across the gap Jeff stopped at."""
    laid = 0
    z0, z1 = CEILING_Z
    for x in range(CEILING_GAP[0], CEILING_GAP[1] + 1):
        for z in range(z0, z1 + 1):
            state = builder.save.get_block(x, CEIL_Y, z)
            if state is None or state.name != "minecraft:stripped_oak_wood":
                builder.set(x, CEIL_Y, z, CEILING)
                laid += 1
    builder.set(VALLEY_HOLE[0], CEIL_Y, VALLEY_HOLE[1], ROOF_STONE)
    return laid + 1


def raise_gable(builder) -> tuple[int, int]:
    """The rear gable itself: roof surface, gable ends, and a solid attic."""
    surface = filled = 0
    for z, top in REAR_PROFILE.items():
        facing = REAR_STAIRS.get(z)
        for x in range(ROOF_X0, ROOF_X1 + 1):
            end_wall = x in (ROOF_X0, ROOF_X1)
            if facing and not end_wall:
                builder.set(x, top, z,
                            f"{ROOF_STAIR}[facing={facing},half=bottom,"
                            "shape=straight,waterlogged=false]")
            else:
                # The gable ends read as masonry, the way the existing
                # ends at x=-314 and x=-277 do north of the valley.
                builder.set(x, top, z, ROOF_STONE)
            surface += 1
            # Everything between the ceiling and the roof is solid: an
            # enclosed dark void is a spawner nobody would ever look in.
            for y in range(CEIL_Y + 1, top):
                builder.set(x, y, z, ROOF_STONE)
                filled += 1
    return surface, filled


def build(builder) -> dict:
    """The whole roof repair, in the order the geometry needs."""
    report = {}
    report["cleared"] = clear_above_rear(builder)
    report["ceiling"] = lay_ceiling(builder)
    report["surface"], report["attic"] = raise_gable(builder)
    return report


def main() -> None:
    site = house.open_site()
    builder = house.Builder(site.save)
    report = build(builder)
    print(report)


if __name__ == "__main__":
    main()
