"""Tests for the vault, its lamps and its bay rhythm, in craft.build.

These came off a hall in a live world: a fifty-block nave with a stepped
vault over it, lamps hung down the middle and two arcades carrying the
roof. The three properties worth testing are the three that hall gets right
and a hand-built one gets wrong.

A vault is laid in **half** blocks. Slabs come in two halves, so a roof made
of them has twice the vertical resolution a roof made of stairs has, and the
difference between a vault and a staircase is entirely that resolution.

A lamp is only as good as the floor under it. Hung from a ceiling it spends
most of its light getting down to head height, so the spacing that works at
floor level is wrong at ceiling level, and the failure is invisible until
nightfall.

And a rule read off somebody's building has to reproduce it. The vault that
motivated all this is 850 courses laid by hand; a rule that reads it wrongly
prints the mistake down the whole hall before anyone walks in.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest

from heaton_voxel.craft.build import Builder, Vault, arch_profile
from heaton_voxel.craft.errors import IllegalBlock
from heaton_voxel.craft.light import simulate_block_light
from heaton_voxel.nbt import NBTError
from heaton_voxel.world import MinecraftSave
from tests.test_world import make_save
from heaton_voxel.blocks import BlockState
from heaton_voxel.craft.build import pointed_arch_profile
from heaton_voxel.craft.errors import Refused
from heaton_voxel.craft.style import Mix

CHUNKS = [(x, z) for x in range(-1, 3) for z in range(-1, 3)]
AIR = "minecraft:air"

#: The rule read off the hall this came from: a half-block ramp seven blocks
#: each side of the crown, springing off a pier at three, and a haunch two
#: blocks deep where it turns down to the wall head.
NAVE = dict(half_span=7, pitch=1, depth=(1, 1, 1, 2, 1, 1, 1, 4),
            slab="minecraft:cobblestone_slab", block="minecraft:cobblestone",
            stair="minecraft:cobblestone_stairs",
            springer="minecraft:cobblestone_slab[type=double]")


class VaultBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="vault-tests-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, CHUNKS)
        self.save = MinecraftSave.load(self.dir)
        self.b = Builder(self.save)

    def surface(self, vault, along):
        """The top of the shell at each offset, in half blocks -- what you
        would measure walking a ruler up the outside of the roof."""
        out = {}
        for (offset, y), state in vault.section().items():
            kind = state.properties.get("type")
            top = 2 * y + (1 if state.name.endswith("_slab")
                           and kind == "bottom" else 2)
            out[offset] = max(out.get(offset, top), top)
        return out


class ProfileTests(VaultBase):
    def test_a_vault_falls_half_a_block_at_a_time(self):
        """The whole trick. A stepped gable drops a block per block; this
        drops half of one, which is why it reads as a curve."""
        vault = Vault((8, 80, 8), **NAVE)
        tops = self.surface(vault, 0)
        for d in range(vault.half_span):
            self.assertEqual(tops[d] - tops[d + 1], 1,
                             f"offset {d} to {d + 1} should fall half a block")

    def test_half_blocks_come_from_alternating_slab_halves(self):
        vault = Vault((8, 80, 8), **NAVE)
        kinds = []
        for d in range(3):
            state = vault.column(d)[max(vault.column(d))]
            kinds.append(state.properties.get("type"))
        self.assertEqual(kinds, ["bottom", "top", "bottom"],
                         "a bottom slab and a top slab are two heights in "
                         "one block; that is where the resolution comes from")

    def test_pitch_two_is_an_ordinary_stepped_gable(self):
        vault = Vault((8, 80, 8), half_span=4, pitch=2)
        tops = self.surface(vault, 0)
        for d in range(4):
            self.assertEqual(tops[d] - tops[d + 1], 2, "a whole block a step")

    def test_a_sequence_of_pitches_steepens_toward_the_eaves(self):
        vault = Vault((8, 80, 8), half_span=4, pitch=(1, 1, 2, 3))
        self.assertEqual(vault.profile, (0, 1, 2, 4, 7))

    def test_the_section_is_symmetric_and_odd_across(self):
        vault = Vault((8, 80, 8), **NAVE)
        self.assertEqual(vault.width, 15)
        section = vault.section()
        for (offset, y), state in section.items():
            mirror = section.get((-offset, y))
            self.assertIsNotNone(mirror, f"nothing opposite ({offset}, {y})")
            self.assertEqual(state.name, mirror.name)

    def test_stairs_face_the_crown_and_the_crown_has_none(self):
        vault = Vault((8, 80, 8), **NAVE)
        section = vault.section()
        facings = {offset: state.properties["facing"]
                   for (offset, y), state in section.items()
                   if state.name.endswith("_stairs")}
        self.assertEqual(facings, {-7: "east", 7: "west"})
        crown = [state for (offset, _), state in section.items() if offset == 0]
        self.assertFalse(any(s.name.endswith("_stairs") for s in crown),
                         "the crown has no outside, so no way for a stair "
                         "to face")

    def test_a_vault_along_x_faces_its_stairs_the_other_way(self):
        vault = Vault((8, 80, 8), axis="x", **NAVE)
        facings = {offset: state.properties["facing"]
                   for (offset, _), state in vault.section().items()
                   if state.name.endswith("_stairs")}
        self.assertEqual(facings, {-7: "south", 7: "north"})

    def test_a_thickened_course_gets_the_springer_and_the_rest_the_block(self):
        vault = Vault((8, 80, 8), **NAVE)
        springer = vault.column(3)[max(vault.column(3))]
        self.assertEqual(springer.properties.get("type"), "double",
                         "the course that lands on a pier is a whole block")
        haunch = vault.column(7)
        self.assertEqual(sorted(haunch), [75, 76], "two blocks deep")
        self.assertEqual(haunch[75].name, "minecraft:cobblestone",
                         "the haunch is the plain block, not the springer")

    def test_a_shell_leaves_no_gap_seen_from_the_end(self):
        """Each column reaches down to the top of the one outside it, so
        there is nothing to see through where the courses step."""
        vault = Vault((8, 80, 8), half_span=6, pitch=(1, 2, 1, 3, 2, 4))
        for d in range(vault.half_span):
            floor = vault.top(d) - vault.thickness(d)
            self.assertLessEqual(floor, vault.top(d + 1),
                                 f"a gap under offset {d}")

    def test_a_profile_that_climbs_outward_is_refused(self):
        with self.assertRaises(NBTError) as caught:
            Vault((8, 80, 8), profile=(0, 2, 1))
        self.assertIn("climbs outward", str(caught.exception))

    def test_a_vault_needs_slabs_to_be_a_vault(self):
        with self.assertRaises(NBTError) as caught:
            Vault((8, 80, 8), slab="minecraft:cobblestone")
        self.assertIn("not a slab", str(caught.exception))


class ArchProfileTests(unittest.TestCase):
    def test_a_circular_arch_never_climbs_outward(self):
        for half_span in range(1, 25):
            for rise in range(0, 25):
                profile = arch_profile(half_span, rise)
                self.assertEqual(len(profile), half_span + 1)
                self.assertEqual(profile[0], 0)
                self.assertEqual(list(profile), sorted(profile),
                                 f"arch_profile({half_span}, {rise}) climbs")

    def test_it_steepens_toward_the_springing(self):
        profile = arch_profile(10, 10)
        steps = [b - a for a, b in zip(profile, profile[1:])]
        self.assertEqual(steps, sorted(steps), "a circle gets steeper out")
        self.assertEqual(profile[-1], 20, "and reaches the full rise")

    def test_eaves_hang_one_more_course_below_the_last(self):
        self.assertEqual(arch_profile(4, 4, eaves=3)[-1],
                         arch_profile(4, 4)[-1] + 3)

    def test_a_vault_built_on_one_is_still_gapless(self):
        vault = Vault((8, 90, 8), profile=arch_profile(9, 9, eaves=2))
        for d in range(vault.half_span):
            self.assertLessEqual(vault.top(d) - vault.thickness(d),
                                 vault.top(d + 1))

    def test_a_pointed_arch_never_climbs_and_reaches_its_rise(self):
        for half_span in range(1, 25):
            for rise in range(half_span, 3 * half_span + 1):
                profile = pointed_arch_profile(half_span, rise)
                self.assertEqual(len(profile), half_span + 1)
                self.assertEqual(profile[0], 0)
                self.assertEqual(profile[-1], 2 * rise,
                                 "it springs from the full rise below")
                self.assertEqual(list(profile), sorted(profile),
                                 f"pointed_arch_profile({half_span}, {rise}) "
                                 f"climbs")

    def test_a_pointed_arch_as_tall_as_half_its_span_is_round(self):
        """At rise == half_span the two centers meet at the crown, and
        the arch is the circle arch_profile draws, course for course."""
        for half_span in range(1, 65):
            self.assertEqual(pointed_arch_profile(half_span, half_span),
                             arch_profile(half_span, half_span),
                             f"half_span {half_span}")

    def test_a_pointed_arch_has_a_point(self):
        """It falls away from its crown at once, where a circle is level."""
        self.assertEqual(pointed_arch_profile(7, 12),
                         (0, 1, 3, 5, 7, 10, 14, 24))
        self.assertEqual(arch_profile(7, 12), (0, 0, 1, 2, 4, 7, 12, 24))
        for half_span in range(1, 25):
            for rise in range(half_span, 3 * half_span + 1):
                self.assertGreaterEqual(pointed_arch_profile(half_span, rise)[1],
                                        arch_profile(half_span, rise)[1])
        self.assertEqual(pointed_arch_profile(4, 6, eaves=3)[-1],
                         pointed_arch_profile(4, 6)[-1] + 3)

    def test_a_pointed_arch_lower_than_its_half_span_is_refused(self):
        with self.assertRaises(NBTError) as caught:
            pointed_arch_profile(7, 6)
        self.assertIn("rises at least its half span", str(caught.exception))
        with self.assertRaises(NBTError):
            pointed_arch_profile(0, 4)

    def test_a_vault_on_a_pointed_arch_is_symmetric_and_gapless(self):
        vault = Vault((8, 90, 8), profile=pointed_arch_profile(7, 12))
        section = vault.section()
        for (offset, y), state in section.items():
            mirror = section.get((-offset, y))
            self.assertIsNotNone(mirror, f"nothing opposite ({offset}, {y})")
            self.assertEqual(state.name, mirror.name)
        for d in range(vault.half_span):
            self.assertLessEqual(vault.top(d) - vault.thickness(d),
                                 vault.top(d + 1), f"a gap under offset {d}")
        crown = section[(0, 90)]
        self.assertEqual(crown.properties.get("type"), "bottom",
                         "the point is a slab, with stairs either side")
        self.assertTrue(section[(1, 89)].name.endswith("_stairs"))


class BuildTests(VaultBase):
    def build(self, **kwargs):
        vault = Vault((8, 80, 8), **{**NAVE, **kwargs})
        result = self.b.vault(vault, 0, 12)
        return vault, result

    def test_what_it_writes_is_what_the_rule_says(self):
        vault, result = self.build()
        self.assertEqual(result.courses, 13)
        self.assertEqual(result.blocks, 13 * len(vault.section()))
        self.assertEqual(vault.verify(self.save, 0, 12), [])

    def test_a_vault_along_x_lands_the_other_way_round(self):
        vault = Vault((8, 80, 8), axis="x", **NAVE)
        self.b.vault(vault, 0, 6)
        self.assertEqual(vault.verify(self.save, 0, 6), [])
        # the crown line runs along x at the crown's z, not its x
        self.assertEqual(self.save.get_block(3, 80, 8).name,
                         "minecraft:cobblestone_slab")

    def test_it_refuses_cells_that_are_already_taken(self):
        self.save.set_block(8, 80, 4, "minecraft:stone")
        vault = Vault((8, 80, 8), **NAVE)
        with self.assertRaises(NBTError) as caught:
            self.b.vault(vault, 0, 12)
        self.assertIn("already taken", str(caught.exception))
        self.assertIn("(8, 80, 4)", str(caught.exception))
        self.assertEqual(self.save.get_block(8, 80, 5).name, AIR,
                         "and nothing was written before the refusal")

    def test_a_vault_extrudes_through_cave_air(self):
        vault = Vault((8, 80, 8), **NAVE)
        for along in range(0, 13):
            for offset, y in vault.section():
                self.save.set_block(*vault.cell(along, offset, y),
                                    "minecraft:cave_air")
        result = self.b.vault(vault, 0, 12)
        self.assertEqual(result.blocks, 13 * len(vault.section()))
        self.assertEqual(vault.verify(self.save, 0, 12), [])

    def test_overwrite_cuts_through(self):
        self.save.set_block(8, 80, 4, "minecraft:stone")
        vault = Vault((8, 80, 8), **NAVE)
        self.b.vault(vault, 0, 12, overwrite=True)
        self.assertEqual(vault.verify(self.save, 0, 12), [])

    def test_a_cell_that_already_holds_the_right_block_is_counted_not_rewritten(self):
        vault = Vault((8, 80, 8), **NAVE)
        self.b.vault(vault, 0, 6)
        again = self.b.vault(vault, 0, 6)
        self.assertEqual(again.already, again.blocks)

    def test_clear_under_empties_the_span_below_the_shell(self):
        # Under the shell, not through it: the wedge inside the roof line.
        self.b.fill((5, 75, 2), (11, 77, 8), "minecraft:stone")
        vault = Vault((8, 80, 8), **NAVE)
        self.b.vault(vault, 4, 8, clear_under=True)
        for y in (75, 76, 77):
            self.assertEqual(self.save.get_block(8, y, 6).name, AIR)
        self.assertEqual(self.save.get_block(8, 77, 2).name, "minecraft:stone",
                         "and only over the courses it laid")

    def test_a_course_spelled_short_is_the_state_the_game_writes_back(self):
        """The nave's springer is spelled ``[type=double]`` and the game
        stores it whole -- ``waterlogged=false`` too -- once it saves the
        chunk, as a 26.3 chunk decodes it from the first. Both are the
        block the rule wants: the vault verifies, and extends over its own
        standing courses rather than calling them taken."""
        vault = Vault((8, 80, 8), **NAVE)
        self.b.vault(vault, 0, 6)
        rewritten = 0
        for along in range(0, 7):
            for (offset, y), state in vault.section().items():
                whole = state.canonical()
                if whole != state:
                    self.save.set_block(*vault.cell(along, offset, y), whole)
                    rewritten += 1
        self.assertGreater(rewritten, 0, "the springer is spelled short")
        self.assertEqual(vault.verify(self.save, 0, 6), [])
        again = Builder(self.save).vault(vault, 0, 8)
        self.assertEqual(again.already, 7 * len(vault.section()))

    def test_a_vault_in_a_26_3_chunk_verifies_and_extends(self):
        from heaton_voxel.nbt import TagInt

        dim = self.save.dimension("minecraft:overworld")
        for x, z in CHUNKS:
            dim.chunk(x, z).data["DataVersion"] = TagInt(5023)
            dim.release(x, z)
        self.save.save()
        save = MinecraftSave.load(self.dir)
        vault = Vault((8, 80, 8), **NAVE)
        Builder(save).vault(vault, 0, 6)
        save.save()
        save = MinecraftSave.load(self.dir)
        self.assertEqual(vault.verify(save, 0, 6), [])
        again = Builder(save).vault(vault, 0, 8)
        self.assertEqual(again.already, 7 * len(vault.section()))

    def test_it_takes_a_vault_and_says_so_when_it_does_not(self):
        with self.assertRaises(NBTError) as caught:
            self.b.vault((8, 80, 8), 0, 4)
        self.assertIn("takes a Vault", str(caught.exception))


class ReadTests(VaultBase):
    def lay(self, first=0, last=12, **kwargs):
        vault = Vault((8, 80, 8), **{**NAVE, **kwargs})
        self.b.vault(vault, first, last)
        return vault

    def test_a_vault_reads_back_as_the_rule_that_built_it(self):
        built = self.lay()
        read = Vault.read(self.save, (8, 80, 6))
        self.assertEqual(read.profile, built.profile)
        self.assertEqual(read.depth, tuple(
            built.thickness(d) for d in range(len(built.profile))))
        self.assertEqual(str(read.slab), str(built.slab))
        self.assertEqual(str(read.block), str(built.block))
        self.assertEqual(str(read.springer), str(built.springer))
        self.assertEqual(read.stair.name, built.stair.name)
        self.assertEqual(read.found, (0, 12))

    def test_the_rule_it_reads_writes_the_same_blocks(self):
        self.lay()
        read = Vault.read(self.save, (8, 80, 6))
        self.assertEqual(read.section().keys(),
                         Vault((8, 80, 8), **NAVE).section().keys())
        self.assertEqual(read.verify(self.save, 0, 12), [])

    def test_it_reads_a_vault_running_along_x(self):
        vault = Vault((8, 80, 8), axis="x", **NAVE)
        self.b.vault(vault, 0, 12)
        read = Vault.read(self.save, (6, 80, 8), axis="x")
        self.assertEqual(read.profile, vault.profile)
        self.assertEqual(read.found, (0, 12))

    def test_the_run_stops_where_the_crown_course_stops(self):
        self.lay(first=3, last=9)
        read = Vault.read(self.save, (8, 80, 6))
        self.assertEqual(read.found, (3, 9))

    def test_low_and_high_bound_the_run(self):
        self.lay(first=0, last=12)
        read = Vault.read(self.save, (8, 80, 6), low=2, high=9)
        self.assertEqual(read.found, (2, 9))

    def test_a_rule_that_does_not_reproduce_the_vault_is_refused(self):
        self.lay()
        self.save.set_block(5, 78, 7, "minecraft:stone")   # one springer
        with self.assertRaises(NBTError) as caught:
            Vault.read(self.save, (8, 80, 6))
        message = str(caught.exception)
        self.assertIn("does not reproduce it", message)
        self.assertIn("strict=False", message)

    def test_an_end_open_to_a_cave_is_unfinished_and_not_wrong(self):
        vault = self.lay()
        for offset, y in vault.section():
            self.save.set_block(*vault.cell(12, offset, y), "minecraft:cave_air")
        self.assertTrue(vault.verify(self.save, 0, 12),
                        "strictly, the last course is missing")
        self.assertEqual(vault.verify(self.save, 0, 12, missing_ok=True), [])

    def test_not_being_strict_hands_the_list_back_instead(self):
        self.lay()
        self.save.set_block(5, 78, 7, "minecraft:stone")
        read = Vault.read(self.save, (8, 80, 6), strict=False)
        self.assertEqual(len(read.differences), 1)
        self.assertIn("(5, 78, 7)", read.differences[0])
        self.assertIn("unaccounted", repr(read))

    def test_a_ridge_thickened_to_hang_lamps_from_is_still_one_run(self):
        """The course a lamp hangs from is a whole block where the rest of
        the ridge is a half one. A read that stopped at the first of those
        would report a thirteen-course vault as one."""
        self.lay()
        anchored = (3, 7, 11)
        for z in anchored:
            self.save.set_block(8, 80, z,
                                "minecraft:cobblestone_slab[type=double]")
        read = Vault.read(self.save, (8, 80, 6), strict=False)
        self.assertEqual(read.found, (0, 12), "the whole run, anchors and all")
        self.assertEqual(read.profile, (0, 1, 2, 3, 4, 5, 6, 7))
        self.assertEqual(len(read.differences), len(anchored),
                         "and the anchors come back as the departures")

    def test_pointing_at_one_of_those_anchors_reads_the_same_vault(self):
        self.lay()
        self.save.set_block(8, 80, 6,
                            "minecraft:cobblestone_slab[type=double]")
        read = Vault.read(self.save, (8, 80, 6), strict=False)
        self.assertEqual(read.found, (0, 12))
        self.assertEqual(read.depth, (1, 1, 1, 2, 1, 1, 1, 4),
                         "the vote takes the ridge the run mostly has, not "
                         "the one that happened to be pointed at")

    def test_pointing_at_something_that_is_not_a_crown_is_refused(self):
        self.b.fill((4, 80, 4), (12, 80, 12), "minecraft:stone")
        with self.assertRaises(NBTError) as caught:
            Vault.read(self.save, (8, 80, 6))
        self.assertIn("crown course is a slab", str(caught.exception))

    def test_a_ridge_with_nothing_beside_it_is_not_a_vault(self):
        self.b.fill((8, 80, 0), (8, 80, 12), "minecraft:cobblestone_slab")
        with self.assertRaises(NBTError) as caught:
            Vault.read(self.save, (8, 80, 6))
        self.assertIn("ridge, not a vault", str(caught.exception))

    def test_the_rule_stops_where_the_run_stops_agreeing(self):
        """The eaves of a real hall vary -- buttresses, cross walls, a wall
        head in the vault's own stone. The rule should trim back to the part
        that really is one vault rather than read the wall as more roof."""
        self.lay()
        for z in (3, 9):
            self.b.set(0, 74, z, "minecraft:cobblestone")
            self.b.set(16, 74, z, "minecraft:cobblestone")
        read = Vault.read(self.save, (8, 80, 6))
        self.assertEqual(read.half_span, 7,
                         "the buttresses are not part of the section")

    def test_half_span_widens_it_back_deliberately(self):
        self.lay()
        for z in range(0, 13):
            self.b.set(0, 74, z,
                       "minecraft:cobblestone_stairs[facing=east,half=bottom,waterlogged=false]")
            self.b.set(16, 74, z,
                       "minecraft:cobblestone_stairs[facing=west,half=bottom,waterlogged=false]")
        read = Vault.read(self.save, (8, 80, 6), half_span=8)
        self.assertEqual(read.half_span, 8)
        self.assertEqual(read.profile[-1], 11, "the eave hangs 5.5 blocks")
        self.assertEqual(read.verify(self.save, 0, 12), [])

    def test_a_stone_vault_on_stone_brick_walls_reads_back_as_stone(self):
        """Stone bricks are not the stone a stone vault is cut from, they
        are the wall it lands on. A family read off the leading letters of
        a name took the walls for more of the roof and refused the read as
        two materials."""
        built = self.lay(slab="minecraft:stone_slab", block="minecraft:stone",
                         stair="minecraft:stone_stairs",
                         springer="minecraft:stone_slab[type=double]")
        for z in range(0, 13):
            for x in (1, 15):       # under the eaves, seven out either side
                self.b.fill((x, 70, z), (x, 74, z), "minecraft:stone_bricks")
        read = Vault.read(self.save, (8, 80, 6))
        self.assertEqual(read.profile, built.profile)
        self.assertEqual(read.depth, tuple(
            built.thickness(d) for d in range(len(built.profile))))
        self.assertEqual(str(read.block), str(built.block))
        self.assertEqual(read.found, (0, 12))
        self.assertEqual(read.verify(self.save, 0, 12), [])

    def test_a_family_is_the_block_its_pieces_are_cut_from(self):
        from heaton_voxel.craft.build import _family, _in_family

        stone = _family("minecraft:stone_slab")
        for name in ("minecraft:stone", "minecraft:stone_stairs",
                     "minecraft:stone_slab"):
            self.assertTrue(_in_family(name, stone), name)
        for name in ("minecraft:stone_bricks", "minecraft:stone_brick_slab",
                     "minecraft:stonecutter", "minecraft:smooth_stone"):
            self.assertFalse(_in_family(name, stone), name)
        self.assertFalse(_in_family("minecraft:sandstone",
                                    _family("minecraft:sand")))
        self.assertTrue(_in_family("minecraft:cobblestone_wall",
                                   _family("minecraft:cobblestone_slab")))

    def test_a_lopsided_vault_is_refused(self):
        self.lay()
        for z in range(0, 13):
            self.b.set(0, 74, z, "minecraft:cobblestone")
        with self.assertRaises(NBTError) as caught:
            Vault.read(self.save, (8, 80, 6))
        self.assertIn("symmetric", str(caught.exception))


class ContinueTests(VaultBase):
    def test_a_read_vault_continues_past_the_end_it_is_heading_for(self):
        vault = Vault((8, 80, 8), **NAVE)
        self.b.vault(vault, 0, 6)
        read = Vault.read(self.save, (8, 80, 3))
        result = self.b.vault(read, None, 12)
        self.assertEqual((result.first, result.last), (7, 12))
        self.assertEqual(read.verify(self.save, 0, 12), [],
                         "and the join has no seam in it")

    def test_it_continues_backwards_too(self):
        vault = Vault((8, 80, 8), **NAVE)
        self.b.vault(vault, 6, 12)
        read = Vault.read(self.save, (8, 80, 9))
        result = self.b.vault(read, None, 0)
        self.assertEqual((result.first, result.last), (0, 5))
        self.assertEqual(read.verify(self.save, 0, 12), [])

    def test_a_rule_read_off_a_building_is_held_to_the_game(self):
        """A rule copies the states it reads, and the game loads an id it
        does not have as air. A vault read off a modded hall is the mod's
        stone, so its continuation is refused naming every piece of it,
        before a course is written; ``allow=`` is the session saying the
        mod is there."""
        marble = dict(NAVE, slab="mymod:marble_slab", block="mymod:marble",
                      stair="mymod:marble_stairs",
                      springer="mymod:marble_slab[type=double]")
        vault = Vault((8, 80, 8), **marble)
        for along in range(0, 7):
            for (offset, y), state in vault.section().items():
                self.save.set_block(*vault.cell(along, offset, y), state)
        read = Vault.read(self.save, (8, 80, 3))
        self.assertEqual(read.found, (0, 6))
        self.assertEqual(read.slab.name, "mymod:marble_slab")

        with self.assertRaises(IllegalBlock) as caught:
            self.b.vault(read, None, 12)
        message = str(caught.exception)
        self.assertIn("vault", message)
        for name in ("mymod:marble_slab", "mymod:marble",
                     "mymod:marble_stairs"):
            self.assertIn(name, message)
        self.assertIn("allow=", message)
        self.assertEqual(self.b.blocks_set, 0)
        for (offset, y) in vault.section():
            self.assertEqual(self.save.get_block(
                *vault.cell(7, offset, y)).name, AIR, "nothing was written")

        result = Builder(self.save, allow=("mymod:",)).vault(read, None, 12)
        self.assertEqual((result.first, result.last), (7, 12))
        self.assertEqual(read.verify(self.save, 0, 12), [])

    def test_continuing_into_the_middle_of_the_run_is_refused(self):
        vault = Vault((8, 80, 8), **NAVE)
        self.b.vault(vault, 0, 12)
        read = Vault.read(self.save, (8, 80, 6))
        with self.assertRaises(NBTError) as caught:
            self.b.vault(read, None, 6)
        self.assertIn("inside the run", str(caught.exception))

    def test_a_fresh_vault_has_nothing_to_continue(self):
        with self.assertRaises(NBTError) as caught:
            self.b.vault(Vault((8, 80, 8), **NAVE), None, 12)
        self.assertIn("nothing to", str(caught.exception))


class HangingLampTests(VaultBase):
    def room(self, x0=2, z0=2, x1=14, z1=14, floor=64, ceiling=72):
        self.b.fill((x0, floor, z0), (x1, floor, z1), "minecraft:stone_bricks")
        self.b.fill((x0, ceiling, z0), (x1, ceiling, z1), "minecraft:stone_bricks")
        return (x0, z0, x1, z1, floor, ceiling)

    def darkest(self, x0, z0, x1, z1, floor, lamps):
        """The worst-lit cell of the floor -- the cell over it, where a
        walker's head is not and their feet are -- by the game's own flood
        over the world as it now stands, counting only the light that
        lasts: light that goes round a pier rather than through it. A cell
        with a block standing in it holds no light and is not counted."""
        self.assertTrue(lamps, "nothing was hung at all")
        field = simulate_block_light(self.save, (x0, floor + 1, z0),
                                     (x1, floor + 1, z1), durable=True)
        return min(field.level(x, floor + 1, z)
                   for z in range(z0, z1 + 1) for x in range(x0, x1 + 1)
                   if field.dampening(x, floor + 1, z) < 15)

    def lamps_placed(self):
        found = self.save.find_blocks((0, 0, 0), (47, 100, 47),
                                      "minecraft:glowstone")
        return {(x, z): y for x, y, z, _ in found.matches}

    def test_every_floor_cell_clears_the_level_asked_for(self):
        x0, z0, x1, z1, floor, ceiling = self.room()
        result = self.b.hang_lamps(x0, z0, x1, z1, ceiling, floor,
                                   drop=2, light_level=8)
        self.assertEqual(result.cells, (x1 - x0 + 1) * (z1 - z0 + 1))
        self.assertGreaterEqual(
            self.darkest(x0, z0, x1, z1, floor, self.lamps_placed()), 8)

    def test_a_higher_ceiling_needs_a_tighter_grid(self):
        self.room(ceiling=68)
        low = self.b.hang_lamps(2, 2, 14, 14, 68, 64, drop=1, light_level=8)
        self.b.fill((20, 64, 2), (32, 64, 14), "minecraft:stone_bricks")
        self.b.fill((20, 72, 2), (32, 72, 14), "minecraft:stone_bricks")
        high = self.b.hang_lamps(20, 2, 32, 14, 72, 64, drop=1, light_level=8)
        self.assertGreater(high.lights, low.lights,
                           "a lamp near a high ceiling has spent most of its "
                           "light before it reaches the floor")

    def test_the_chain_hangs_from_the_ceiling_down_to_the_lamp(self):
        self.room()
        self.b.hang_lamps(2, 2, 14, 14, 72, 64, drop=3,
                          chain="minecraft:iron_chain[axis=y]", light_level=8)
        (x, z), y = next(iter(self.lamps_placed().items()))
        self.assertEqual(y, 72 - 1 - 3)
        for link in range(y + 1, 72):
            self.assertEqual(self.save.get_block(x, link, z).name,
                             "minecraft:iron_chain", f"no link at y={link}")

    def test_a_lantern_hangs_by_its_own_hanging_property(self):
        # Once refused by the rule that keeps lanterns out of paving.
        self.room()
        result = self.b.hang_lamps(2, 2, 14, 14, 72, 64, drop=3,
                                   light="minecraft:lantern", light_level=8)
        self.assertGreater(result.lights, 0)
        found = self.save.find_blocks((0, 0, 0), (47, 100, 47), "minecraft:lantern")
        states = {str(state) for _, _, _, state in found.matches}
        self.assertEqual(states, {"minecraft:lantern[hanging=true,waterlogged=false]"})
        x, y, z, _ = found.matches[0]
        self.assertEqual(self.save.get_block(x, y + 1, z).name,
                         "minecraft:iron_chain", "it hangs from its chain")
        # Asked again, the room is lit already, by lanterns read off the
        # world: nothing more to hang, and nothing refused for the chains
        # already in the way.
        again = self.b.hang_lamps(2, 2, 14, 14, 72, 64, drop=3,
                                  light="minecraft:lantern", light_level=8)
        self.assertEqual((again.lights, again.chains), (0, 0))

    def test_a_lamp_you_would_walk_into_is_refused(self):
        self.room()
        with self.assertRaises(NBTError) as caught:
            self.b.hang_lamps(2, 2, 14, 14, 72, 64, drop=6, light_level=8)
        self.assertIn("walker", str(caught.exception))

    def test_a_lamp_that_cannot_reach_the_floor_is_refused(self):
        self.b.fill((2, 64, 2), (14, 64, 14), "minecraft:stone_bricks")
        self.b.fill((2, 90, 2), (14, 90, 14), "minecraft:stone_bricks")
        with self.assertRaises(NBTError) as caught:
            self.b.hang_lamps(2, 2, 14, 14, 90, 64, drop=1, light_level=8)
        message = str(caught.exception)
        self.assertIn("arrives there at", message)
        self.assertEqual(self.save.get_block(8, 88, 8).name, AIR,
                         "and nothing was hung before the refusal")

    def test_a_chain_does_not_hang_from_a_top_slab(self):
        """A top slab fills the upper half of its block, so a chain under
        it hangs from a gap: the game would drop a lantern there."""
        self.b.fill((2, 64, 2), (14, 64, 14), "minecraft:stone_bricks")
        self.b.fill((2, 72, 2), (14, 72, 14),
                    "minecraft:cobblestone_slab[type=top]")
        with self.assertRaises(NBTError) as caught:
            self.b.hang_lamps(2, 2, 14, 14, 72, 64, drop=2, light_level=8)
        self.assertIn("anchor=", str(caught.exception))
        self.assertIn("its underside is not sturdy at the center",
                      str(caught.exception))

    def test_a_chain_hangs_from_what_a_lantern_hangs_from(self):
        """The ceiling rule is the lantern's (``LanternBlock.canSurvive``:
        the block above sturdy at the middle of its underside), taken for
        the chain by choice -- the game keeps a chain under anything. It
        agrees state by state with the support audit's verdict on a
        hanging lantern under the same block."""
        from heaton_voxel.blocks import BlockState
        from heaton_voxel.craft.build import _hangs_from
        from heaton_voxel.craft.survey import unsupported

        slab = "minecraft:cobblestone_slab[type={},waterlogged=false]"
        stair = ("minecraft:oak_stairs[facing=north,half={},shape=straight,"
                 "waterlogged=false]")
        cases = {
            "minecraft:stone_bricks": True,
            slab.format("bottom"): True,
            slab.format("double"): True,
            slab.format("top"): False,
            stair.format("bottom"): True,
            stair.format("top"): False,
            "minecraft:oak_fence[east=false,north=false,south=false,"
            "waterlogged=false,west=false]": True,
            "minecraft:cobblestone_wall[east=none,north=none,south=none,"
            "up=true,waterlogged=false,west=none]": True,
            "minecraft:iron_chain[axis=y,waterlogged=false]": True,
            "minecraft:oak_leaves[distance=1,persistent=true,"
            "waterlogged=false]": False,
            "minecraft:air": False,
            "minecraft:cave_air": False,
        }
        lantern = "minecraft:lantern[hanging=true,waterlogged=false]"
        for x, (ceiling, hangs) in enumerate(cases.items()):
            with self.subTest(ceiling=ceiling):
                self.assertEqual(_hangs_from(BlockState.parse(ceiling)), hangs)
                plan = {(x, 90, 0): ceiling, (x, 89, 0): lantern}
                kept = not unsupported(self.save, (x, 89, 0), (x, 89, 0),
                                       overlay=plan)
                self.assertEqual(kept, hangs, "the audit's lantern rule")
        # An id the game does not have is air to it, whatever facts() says.
        self.assertFalse(_hangs_from(BlockState.parse("modded:ceiling_tile")))

    def test_the_ceiling_rule_is_the_games_state_by_state(self):
        """Held to ``fixtures/block_shapes.json``, which the probe reads
        out of the jar: a chain hangs from exactly the states whose
        underside the game calls sturdy at the center, over every state it
        has. The name list this replaced disagreed with it on 22,555 of
        35,723 -- hanging lamps from leaves and beds, and refusing bottom
        slabs and fences."""
        import fixtures
        from heaton_voxel.blocks import AIR_BLOCKS, BlockState
        from heaton_voxel.craft.block_facts import FACES
        from heaton_voxel.craft.build import _hangs_from

        down = 1 << FACES.index("down")
        wrong = [f"{name}{props}"
                 for name, props, shape in fixtures.block_shapes()
                 if _hangs_from(BlockState(name, dict(props)))
                 != (bool(shape.center & down) and name not in AIR_BLOCKS)]
        self.assertEqual(wrong[:10], [], f"{len(wrong)} states disagree")

    def test_a_fence_gate_holds_nothing_up(self):
        """``Block.canSupportCenter`` refuses the ``unstable_bottom_center``
        tag below before it asks the shape, and the probe's center mask is
        the shape alone. In 26.3 the tag is ``#fence_gates``, these
        thirteen, which the name rule must name exactly; no state of any of
        them is a ceiling, whatever an update does to a gate's underside."""
        import fixtures
        from heaton_voxel.blocks import BlockState
        from heaton_voxel.craft.block_registry import BLOCK_IDS
        from heaton_voxel.craft.build.vault import _hangs_from, _unstable_below

        self.assertEqual(sorted(n for n in BLOCK_IDS if _unstable_below(n)),
                         [f"minecraft:{wood}_fence_gate" for wood in (
                             "acacia", "bamboo", "birch", "cherry", "crimson",
                             "dark_oak", "jungle", "mangrove", "oak",
                             "pale_oak", "poplar", "spruce", "warped")])
        gates = [(name, props) for name, props, _ in fixtures.block_shapes()
                 if _unstable_below(name)]
        self.assertEqual(len(gates), 13 * 32)
        self.assertFalse(any(_hangs_from(BlockState(name, dict(props)))
                             for name, props in gates))

    def test_a_bottom_slab_crown_holds_a_hung_lamp(self):
        """A vault's crown course is a bottom slab, whose underside is
        whole: a lantern hangs from it, so a chain does, with no anchor and
        the ridge left as the rule laid it. Once refused, every column
        passed over, as a chain hung from a gap."""
        from heaton_voxel.craft.survey import unsupported

        for center, drop, light in ((8, 2, "minecraft:glowstone"),
                                    (30, 0, "minecraft:lantern")):
            with self.subTest(light=light, drop=drop):
                vault = Vault((center, 80, 0), **NAVE)
                self.b.vault(vault, 0, 20)
                self.b.fill((center - 6, 73, 0), (center + 6, 73, 20),
                            "minecraft:stone_bricks")
                lamps = self.b.hang_lamps(center, 1, center, 19, 80, 73,
                                          drop=drop, light=light,
                                          light_level=8)
                self.assertGreater(lamps.lights, 0)
                self.assertEqual((lamps.anchors, lamps.passed_over), (0, 0))
                self.assertEqual(vault.verify(self.save, 0, 20), [],
                                 "the ridge is the half slab it was laid as")
                hung = [z for z in range(1, 20)
                        if self.save.get_block(center, 79, z).name
                        != "minecraft:air"]
                self.assertEqual(len(hung), lamps.lights,
                                 "each hangs right under the crown course")
                self.assertEqual(unsupported(self.save, (center, 77, 0),
                                             (center, 79, 20)), [],
                                 "and the game would keep every lantern")

    def test_an_unknown_id_is_not_a_ceiling(self):
        """The game loads an id it does not have as air, so a chain under
        one hangs from nothing -- though ``facts()`` answers a cube for it,
        and once hung lamps from it."""
        self.b.fill((2, 64, 2), (14, 64, 14), "minecraft:stone_bricks")
        self.save.fill((2, 72, 2), (14, 72, 14), "modded:ceiling_tile")
        with self.assertRaises(NBTError) as caught:
            self.b.hang_lamps(2, 2, 14, 14, 72, 64, drop=2, light_level=8)
        self.assertIn("holds modded:ceiling_tile, which a chain does not "
                      "hang from -- the game has no such block, and loads it "
                      "as air", str(caught.exception))
        self.assertEqual(self.lamps_placed(), {})

    def test_an_anchor_is_set_above_every_lamp(self):
        """``anchor=`` is a boss over each lamp hung, whatever the ceiling
        holds there: over stone bricks a chain would hang from anyway,
        every lamp still gets one, and the rest of the ceiling is left as
        it was."""
        x0, z0, x1, z1, floor, ceiling = self.room()
        boss = "minecraft:chiseled_stone_bricks"
        result = self.b.hang_lamps(x0, z0, x1, z1, ceiling, floor, drop=2,
                                   light_level=8, anchor=boss)
        lamps = self.lamps_placed()
        self.assertEqual(len(lamps), result.lights)
        self.assertEqual(result.anchors, result.lights)
        bosses = {(x, z) for z in range(z0, z1 + 1) for x in range(x0, x1 + 1)
                  if self.save.get_block(x, ceiling, z).name == boss}
        self.assertEqual(bosses, set(lamps))

    def test_an_anchor_a_lantern_would_not_hang_from_is_refused(self):
        """The anchor is the ceiling every lamp hangs from, so it is held
        to the same rule, before anything is read or written."""
        self.room()
        written = self.b.blocks_set
        for anchor in ("minecraft:cobblestone_slab[type=top]",
                       "minecraft:oak_leaves"):
            with self.subTest(anchor=anchor):
                with self.assertRaises(NBTError) as caught:
                    self.b.hang_lamps(2, 2, 14, 14, 72, 64, drop=2,
                                      light_level=8, anchor=anchor)
                message = str(caught.exception)
                self.assertIn(f"anchor={anchor} is set above every lamp",
                              message)
                self.assertIn("not sturdy at the center", message)
                self.assertEqual(self.b.blocks_set, written)
                self.assertEqual(self.lamps_placed(), {})

    def test_a_chain_does_not_hang_from_cave_air(self):
        """The roof of a cave is the stone above its cave air, not the
        cave air: a ceiling course of nothing is refused, and nothing hung."""
        self.b.fill((2, 64, 2), (14, 64, 14), "minecraft:stone_bricks")
        for nothing in ("minecraft:cave_air", "minecraft:void_air"):
            with self.subTest(ceiling=nothing):
                self.save.fill((2, 72, 2), (14, 72, 14), nothing)
                with self.assertRaises(NBTError) as caught:
                    self.b.hang_lamps(2, 2, 14, 14, 72, 64, drop=2, light_level=8)
                message = str(caught.exception)
                self.assertIn(f"holds {nothing}, which a chain does not "
                              f"hang from -- it is air", message)
                self.assertEqual(self.lamps_placed(), {})

    def test_an_anchor_thickens_the_ceiling_where_a_lamp_needs_it(self):
        self.b.fill((2, 64, 2), (14, 64, 14), "minecraft:stone_bricks")
        self.b.fill((2, 72, 2), (14, 72, 14),
                    "minecraft:cobblestone_slab[type=bottom]")
        result = self.b.hang_lamps(
            2, 2, 14, 14, 72, 64, drop=2,
            anchor="minecraft:cobblestone_slab[type=double]", light_level=8)
        self.assertEqual(result.anchors, result.lights)
        for (x, z) in self.lamps_placed():
            self.assertEqual(
                self.save.get_block(x, 72, z).properties.get("type"), "double")
        # and nowhere else: the rest of the ceiling is still half slabs
        doubled = sum(
            self.save.get_block(x, 72, z).properties.get("type") == "double"
            for z in range(2, 15) for x in range(2, 15))
        self.assertEqual(doubled, result.lights)

    def test_columns_restrict_where_a_chain_may_hang(self):
        """Under a vault there is nowhere to hang a lamp but the few
        columns the shell leaves clear, so the room to light and the places
        to light it from are two different shapes."""
        self.room(x0=4, x1=12)
        rows = [(8, z) for z in range(2, 15)]
        result = self.b.hang_lamps(4, 2, 12, 14, 72, 64, drop=4,
                                   light_level=8, columns=rows)
        self.assertTrue(set(self.lamps_placed()) <= set(rows),
                        "every lamp hangs in a column that was allowed")
        self.assertGreaterEqual(
            self.darkest(4, 2, 12, 14, 64, self.lamps_placed()), 8)

    def test_a_hall_too_wide_for_one_row_of_lamps_is_refused(self):
        self.room(x0=0, x1=16)
        with self.assertRaises(NBTError) as caught:
            self.b.hang_lamps(0, 2, 16, 14, 72, 64, drop=4, light_level=8,
                              columns=[(8, z) for z in range(2, 15)])
        message = str(caught.exception)
        self.assertIn("no permitted lamp can reach", message)
        self.assertIn("Hang another row", message)
        self.assertEqual(self.save.get_block(8, 69, 8).name, AIR,
                         "and nothing was hung before the refusal")

    def test_lamps_outside_the_room_being_lit_are_refused(self):
        self.room()
        with self.assertRaises(NBTError) as caught:
            self.b.hang_lamps(4, 4, 12, 12, 72, 64, drop=2,
                              columns=[(20, 20)], light_level=8)
        self.assertIn("outside the room", str(caught.exception))

    def test_lamps_hang_through_a_room_of_cave_air(self):
        x0, z0, x1, z1, floor, ceiling = self.room()
        self.save.fill((x0, floor + 1, z0), (x1, ceiling - 1, z1),
                       "minecraft:cave_air")
        result = self.b.hang_lamps(x0, z0, x1, z1, ceiling, floor,
                                   drop=2, light_level=8)
        self.assertGreater(result.lights, 0)
        self.assertGreaterEqual(
            self.darkest(x0, z0, x1, z1, floor, self.lamps_placed()), 8)

    def test_something_already_in_the_chain_is_refused(self):
        self.room()
        self.b.fill((2, 69, 2), (14, 69, 14), "minecraft:stone")
        with self.assertRaises(NBTError) as caught:
            self.b.hang_lamps(2, 2, 14, 14, 72, 64, drop=2, light_level=8)
        self.assertIn("already holds", str(caught.exception))

    def test_a_partition_gets_lamps_on_both_sides(self):
        """Light goes round a wall, not through it: a room split floor to
        ceiling is two rooms to light. Straight-line arithmetic credited a
        lamp on one side with the floor on the other."""
        x0, z0, x1, z1, floor, ceiling = self.room()
        self.save.fill((8, floor + 1, z0), (8, ceiling - 1, z1),
                       "minecraft:stone")
        result = self.b.hang_lamps(x0, z0, x1, z1, ceiling, floor,
                                   drop=2, light_level=8)
        lamps = self.lamps_placed()
        self.assertTrue(any(x < 8 for x, _ in lamps), "west of it")
        self.assertTrue(any(x > 8 for x, _ in lamps), "east of it")
        self.assertNotIn(8, {x for x, _ in lamps}, "none inside it")
        self.assertEqual(result.blocked, z1 - z0 + 1, "the partition itself")
        self.assertGreaterEqual(self.darkest(x0, z0, 7, z1, floor, lamps), 8)
        self.assertGreaterEqual(self.darkest(9, z0, x1, z1, floor, lamps), 8)

    def test_a_pier_in_the_room_is_not_a_dark_cell(self):
        """A pier from floor to ceiling holds no light and needs none: it
        is counted as blocked, not refused as dark, no lamp hangs in it, and
        the floor round it is lit. These four stand on the lattice itself,
        so the lamps they displace are hung round them, not refused as a
        grid with something in its way."""
        x0, z0, x1, z1, floor, ceiling = self.room()
        piers = [(6, 6), (12, 6), (6, 12), (12, 12)]
        for x, z in piers:
            self.save.fill((x, floor + 1, z), (x, ceiling - 1, z),
                           "minecraft:stone")
        result = self.b.hang_lamps(x0, z0, x1, z1, ceiling, floor,
                                   drop=2, light_level=8)
        self.assertEqual(result.blocked, len(piers))
        self.assertEqual(result.passed_over, len(piers), "no chain in a pier")
        self.assertIn(f"{len(piers)} blocked", repr(result))
        self.assertFalse(set(piers) & set(self.lamps_placed()))
        self.assertGreaterEqual(
            self.darkest(x0, z0, x1, z1, floor, self.lamps_placed()), 8)

    def test_a_column_a_lamp_cannot_hang_in_is_passed_over(self):
        """Something in one column's chain, or a top slab over another,
        leaves those two places on the lattice with no lamp: they are
        passed over and counted, and the room is lit from the rest."""
        x0, z0, x1, z1, floor, ceiling = self.room()
        self.save.set_block(6, 70, 6, "minecraft:stone")
        self.save.set_block(12, ceiling, 12,
                            "minecraft:stone_brick_slab[type=top]")
        result = self.b.hang_lamps(x0, z0, x1, z1, ceiling, floor,
                                   drop=2, light_level=8)
        lamps = self.lamps_placed()
        self.assertNotIn((6, 6), lamps)
        self.assertNotIn((12, 12), lamps)
        self.assertEqual(result.passed_over, 2)
        self.assertIn("2 passed over", repr(result))
        self.assertEqual(result.blocked, 0, "the floor under both is open")
        self.assertGreaterEqual(self.darkest(x0, z0, x1, z1, floor, lamps), 8)

    def test_a_dark_room_names_the_columns_it_passed_over(self):
        """When the rest cannot light the room, the refusal says which
        columns were passed over, and why, beside the dark cells it holds
        as its faults."""
        self.room(x0=0, x1=16)
        self.save.set_block(8, 70, 8, "minecraft:stone")
        with self.assertRaises(NBTError) as caught:
            self.b.hang_lamps(0, 2, 16, 14, 72, 64, drop=4, light_level=8,
                              columns=[(8, z) for z in range(2, 15)])
        message = str(caught.exception)
        self.assertIn("no permitted lamp can reach", message)
        self.assertIn("1 of the columns a chain may hang in were passed "
                      "over, first: (8, 70, 8) already holds minecraft:stone",
                      message)
        faults = caught.exception.faults
        self.assertTrue(faults)
        self.assertTrue(all(isinstance(fault, tuple) and len(fault) == 4
                            for fault in faults), "the dark cells, x y z level")
        self.assertEqual(self.save.get_block(8, 69, 4).name, AIR)

    def test_two_calls_that_meet_light_the_seam_between_them(self):
        """The lattice is phase-locked to world coordinates and each call
        counts the lamps already hung, read off the world, so a hall lit in
        two sittings has no dark stripe down the join."""
        self.b.fill((2, 64, 2), (30, 64, 14), "minecraft:stone_bricks")
        self.b.fill((2, 72, 2), (30, 72, 14), "minecraft:stone_bricks")
        self.b.hang_lamps(2, 2, 15, 14, 72, 64, drop=2, light_level=8)
        left = set(self.lamps_placed())
        self.b.hang_lamps(16, 2, 30, 14, 72, 64, drop=2, light_level=8)
        self.assertGreaterEqual(
            self.darkest(2, 2, 30, 14, 64, self.lamps_placed()), 8,
            "the seam between the two calls is as bright as the rest")
        self.assertTrue(set(self.lamps_placed()) > left,
                        "and the second call did hang some of its own")


class ArcadeTests(VaultBase):
    def test_piers_stand_at_the_pitch_asked_for(self):
        piers = self.b.arcade((4, 0), (4, 24), 64, 70, pitch=6)
        self.assertEqual(piers, ((4, 0), (4, 6), (4, 12), (4, 18), (4, 24)))
        for x, z in piers:
            for y in range(64, 71):
                self.assertEqual(self.save.get_block(x, y, z).name,
                                 "minecraft:quartz_block")
        self.assertEqual(self.save.get_block(4, 67, 3).name, AIR,
                         "and nowhere between them")

    def test_two_runs_that_start_level_share_the_bays(self):
        left = self.b.arcade((4, 1), (4, 20), 64, 68, pitch=5)
        right = self.b.arcade((12, 1), (12, 20), 64, 68, pitch=5)
        self.assertEqual({z for _, z in left}, {z for _, z in right})

    def test_a_run_that_starts_elsewhere_is_lined_up_with_phase(self):
        left = self.b.arcade((4, 1), (4, 20), 64, 68, pitch=5)
        adrift = self.b.arcade((12, 3), (12, 20), 64, 68, pitch=5)
        self.assertFalse({z for _, z in left} & {z for _, z in adrift},
                         "starting elsewhere gives its own rhythm")
        aligned = self.b.arcade((20, 3), (20, 20), 64, 68, pitch=5, phase=1)
        self.assertTrue({z for _, z in aligned} <= {z for _, z in left},
                        "and phase= puts it back on the hall's bays")

    def test_a_run_extended_later_picks_the_same_bays_back_up(self):
        first = self.b.arcade((4, 1), (4, 11), 64, 68, pitch=5)
        more = self.b.arcade((4, 12), (4, 30), 64, 68, pitch=5, phase=1)
        pitches = sorted(z for _, z in first + more)
        self.assertEqual([b - a for a, b in zip(pitches, pitches[1:])],
                         [5] * (len(pitches) - 1), "no stutter at the join")

    def test_a_base_and_a_cap_bracket_the_pier(self):
        piers = self.b.arcade((4, 0), (4, 12), 64, 70, pitch=6,
                              base="minecraft:chiseled_quartz_block",
                              cap="minecraft:quartz_slab[type=double]")
        for x, z in piers:
            self.assertEqual(self.save.get_block(x, 64, z).name,
                             "minecraft:chiseled_quartz_block")
            self.assertEqual(self.save.get_block(x, 70, z).name,
                             "minecraft:quartz_slab")

    def test_it_runs_along_x_as_well(self):
        piers = self.b.arcade((0, 8), (12, 8), 64, 66, pitch=4)
        self.assertEqual(piers, ((0, 8), (4, 8), (8, 8), (12, 8)))

    def test_a_diagonal_arcade_is_refused(self):
        with self.assertRaises(NBTError) as caught:
            self.b.arcade((0, 0), (8, 8), 64, 70)
        self.assertIn("diagonal", str(caught.exception))

    def test_piers_stand_at_least_one_apart(self):
        with self.assertRaises(NBTError):
            self.b.arcade((4, 0), (4, 12), 64, 70, pitch=0)


class NaveTests(VaultBase):
    """The three of them together, which is what the hall actually is."""

    def test_arcade_then_vault_then_lamps(self):
        # The piers stop level with the course the vault springs from: the
        # springer at offset 3 is the impost, and it lands on top of them.
        piers = self.b.arcade((5, 0), (5, 20), 64, 77, pitch=6,
                              cap="minecraft:quartz_slab[type=double]")
        self.b.arcade((11, 0), (11, 20), 64, 77, pitch=6,
                      cap="minecraft:quartz_slab[type=double]")
        vault = Vault((8, 80, 0), **NAVE)
        laid = self.b.vault(vault, 0, 20)
        self.assertEqual(vault.verify(self.save, 0, 20), [])
        self.b.fill((2, 73, 0), (14, 73, 20), "minecraft:stone_bricks")
        # Down the crown line, which is the only column the vault leaves
        # clear all the way from the ridge -- the same place the hall this
        # came from hangs its nave lamps.
        lamps = self.b.hang_lamps(
            8, 1, 8, 19, 80, 73, drop=2, light_level=8,
            chain="minecraft:iron_chain[axis=y]",
            anchor="minecraft:cobblestone_slab[type=double]")

        self.assertEqual(len(piers), 4)
        self.assertEqual(laid.courses, 21)
        self.assertGreater(lamps.lights, 0)
        self.assertEqual(lamps.anchors, lamps.lights)
        # Every lamp hangs from a course thickened for it, and the rest of
        # the ridge is still the half slab it was laid as -- which is also
        # the only place the roof now departs from the rule that laid it.
        hung = {z for (x, z) in HangingLampTests.lamps_placed(self)}
        for z in range(0, 21):
            kind = self.save.get_block(8, 80, z).properties.get("type")
            self.assertEqual(kind, "double" if z in hung else "bottom",
                             f"the ridge at z={z}")
        self.assertEqual(len(vault.verify(self.save, 0, 20)), len(hung))

    def test_finish_settles_the_corners_the_vault_left_square(self):
        vault = Vault((8, 80, 8), **NAVE)
        self.b.vault(vault, 0, 12)
        report = self.b.finish()
        self.assertIsNotNone(report.connections)
        self.assertEqual(vault.verify(self.save, 0, 12), [],
                         "settling shapes does not disturb the rule, which "
                         "does not compare them")


class PointedVaultTests(VaultBase):
    def test_a_pointed_vault_builds_and_reads_back_as_its_rule(self):
        vault = Vault((8, 90, 8), profile=pointed_arch_profile(7, 12))
        self.b.vault(vault, 0, 12)
        self.assertEqual(vault.verify(self.save, 0, 12), [])
        read = Vault.read(self.save, (8, 90, 6))
        self.assertEqual(read.profile, vault.profile)
        self.assertEqual(read.found, (0, 12))


QUARTZ = "minecraft:quartz_block"

#: The walls under JeffWorld's nave, read off it (read-only, 2026-10-21)
#: at z=89, 95 and 111, offsets -7..-3 from its crown line at x=-63, y=89
#: up: the same 16 cells at all three. Quartz from the springing at y=89
#: up to the vault, and each top slab over the wall made whole. (The
#: vault's crown is at y=94; the springer at offset -3 is spelled out
#: whole, as the game writes it back.)
NAVE_WALL = {
    (-7, 89): "minecraft:cobblestone",
    (-7, 90): ("minecraft:cobblestone_stairs[facing=east,half=bottom,"
               "shape=straight,waterlogged=false]"),
    (-6, 89): QUARTZ,
    (-6, 90): QUARTZ,
    (-6, 91): "minecraft:cobblestone_slab[type=bottom,waterlogged=false]",
    (-5, 89): QUARTZ,
    (-5, 90): QUARTZ,
    (-5, 91): "minecraft:cobblestone_slab[type=double,waterlogged=false]",
    (-4, 89): QUARTZ,
    (-4, 90): QUARTZ,
    (-4, 91): QUARTZ,
    (-4, 92): "minecraft:cobblestone_slab[type=bottom,waterlogged=false]",
    (-3, 89): QUARTZ,
    (-3, 90): QUARTZ,
    (-3, 91): QUARTZ,
    (-3, 92): "minecraft:cobblestone_slab[type=double,waterlogged=false]",
}


def _top_slabs(section) -> list:
    """The cells of a section holding a top slab: each one's lower half is
    open to whatever is under it."""
    return sorted(key for key, state in section.items()
                  if state.name.endswith("_slab")
                  and state.properties.get("type") == "top")


class CloseEndsTests(VaultBase):
    """A run's end walls: from the springing up to the shell, and every top
    slab over the wall made whole -- the rule JeffWorld's nave walls
    follow, and the half block a wall filled up to the underside of the
    shell leaves open under each top-slab course."""

    def closed(self, **kwargs):
        return Vault((8, 80, 8), **{**NAVE, "close_ends": QUARTZ, **kwargs})

    def test_the_closure_is_the_rule_the_nave_walls_follow(self):
        vault = Vault((-63, 94, 89), close_ends=QUARTZ, **NAVE)
        self.assertEqual(vault.springing, 89)
        section = vault.section(closed=True)
        wall = {key: state for key, state in section.items()
                if -7 <= key[0] <= -3}
        self.assertEqual(sorted(wall), sorted(NAVE_WALL))
        for key, expect in NAVE_WALL.items():
            self.assertEqual(wall[key].canonical(),
                             BlockState.parse(expect).canonical(), key)

    def test_closed_ends_fill_from_the_springing_to_the_shell(self):
        vault = self.closed()
        result = self.b.vault(vault, 0, 12)
        self.assertEqual(vault.springing, 75)
        shell = Vault((8, 80, 8), **NAVE).section()
        for z in (0, 12):
            for offset in range(-7, 8):
                ys = [y for (o, y) in shell if o == offset]
                for y in range(75, max(ys) + 1):
                    found = self.save.get_block(8 + offset, y, z)
                    self.assertNotEqual(found.name, AIR,
                                        f"a hole in the end wall at "
                                        f"{(8 + offset, y, z)}")
                    if y < min(ys):
                        self.assertEqual(found.name, QUARTZ)
        self.assertEqual(self.save.get_block(8, 76, 6).name, AIR,
                         "and the courses between stay open")
        self.assertEqual(self.save.get_block(8, 79, 1).name, AIR)
        closed = vault.section(closed=True)
        self.assertEqual(result.blocks, 11 * len(shell) + 2 * len(closed))
        self.assertEqual(result.closed, BlockState(QUARTZ))
        self.assertIn("closed with minecraft:quartz_block", repr(result))
        self.assertIn("closed with quartz_block", repr(vault))

    def test_a_closed_end_leaves_no_half_block_open(self):
        """tools/nave/build_hall.py fills each end column from the wall head
        up to the block under its lowest piece: four of NAVE's columns end
        on a top slab, whose lower half that leaves open."""
        vault = self.closed()
        by_hand = dict(vault.section())
        head = vault.springing - 1
        for offset in range(-vault.half_span, vault.half_span + 1):
            soffit = min(y for (o, y) in by_hand if o == offset)
            for y in range(head + 1, soffit):
                by_hand[(offset, y)] = BlockState("minecraft:bricks")
        self.assertEqual([offset for offset, _ in _top_slabs(by_hand)],
                         [-5, -1, 1, 5])
        self.assertEqual(_top_slabs(vault.section(closed=True)), [])

    def test_verify_holds_the_ends_to_the_closed_section(self):
        vault = self.closed()
        self.b.vault(vault, 0, 12)
        self.assertEqual(vault.verify(self.save, 0, 12), [])
        # The open rule over the closed run finds only its doubled slabs.
        departs = Vault((8, 80, 8), **NAVE).verify(self.save, 0, 12)
        self.assertEqual(len(departs), 8)
        self.assertTrue(all(c.startswith(("z=0 ", "z=12 ")) for c in departs))

        # The closed rule over an open run finds its ends missing; with
        # missing_ok only the slabs a wall would have made whole remain.
        self.b.vault(Vault((30, 80, 8), **NAVE), 0, 12)
        closed = Vault((30, 80, 8), close_ends=QUARTZ, **NAVE)
        missing = closed.verify(self.save, 0, 12)
        self.assertTrue(all(c.startswith(("z=0 ", "z=12 ")) for c in missing))
        forgiven = closed.verify(self.save, 0, 12, missing_ok=True)
        self.assertEqual(len(forgiven), 8)
        self.assertTrue(all("type=double" in c for c in forgiven))
        self.assertGreater(len(missing), len(forgiven))

    def test_continuing_a_closed_vault_is_refused(self):
        # A fresh rule has nothing to continue, closed or open, and is told
        # so -- not sent to open its ends for a second refusal.
        with self.assertRaises(NBTError) as caught:
            self.b.vault(self.closed(), None, 20)
        self.assertIn("made from scratch", str(caught.exception))
        self.assertNotIn("close_ends", str(caught.exception))
        self.b.vault(Vault((8, 80, 8), **NAVE), 0, 12)
        read = Vault.read(self.save, (8, 80, 6))
        self.assertIsNone(read.close_ends, "a read vault is open")
        read.close_ends = BlockState(QUARTZ)
        before = self.b.blocks_set
        with self.assertRaises(NBTError) as caught:
            self.b.vault(read, None, 20)
        self.assertIn("closes a fresh run", str(caught.exception))
        self.assertNotIsInstance(caught.exception, Refused)
        self.assertEqual(self.b.blocks_set, before)

    def test_a_closed_run_does_not_read_back_as_its_open_rule(self):
        """Vault.read reads no end walls, so the strict replay of the open
        rule it derives trips on the closed ends' whole slabs -- the first
        of the two things standing between a closed run and a continuation
        that would leave its wall in the hall."""
        self.b.vault(self.closed(), 0, 12)
        with self.assertRaises(Refused) as caught:
            Vault.read(self.save, (8, 80, 6))
        self.assertEqual(len(caught.exception.faults), 8)
        self.assertTrue(all(fault.startswith(("z=0 ", "z=12 "))
                            and "type=double" in fault
                            for fault in caught.exception.faults))
        loose = Vault.read(self.save, (8, 80, 6), strict=False)
        self.assertIsNone(loose.close_ends)
        self.assertEqual(loose.found, (0, 12))
        self.assertEqual(len(loose.differences), 8)

    def test_a_run_closed_in_the_world_is_not_continued(self):
        """The second: however the rule was read, a continuation whose old
        end course is closed as close_ends closes one is refused by name,
        before anything is written, rather than build on past the wall."""
        self.b.vault(self.closed(), 0, 12)
        loose = Vault.read(self.save, (8, 80, 6), strict=False)
        before = self.b.blocks_set
        for last, seam in ((30, 12), (-10, 0)):
            with self.subTest(last=last):
                with self.assertRaises(Refused) as caught:
                    self.b.vault(loose, None, last)
                self.assertEqual(
                    caught.exception.faults,
                    [f"z={seam}: walled with {QUARTZ} from y=75 up to the "
                     f"shell, its top slabs made whole"])
                self.assertIn("Nothing was written", str(caught.exception))
        self.assertEqual(self.b.blocks_set, before)
        self.assertEqual(self.save.get_block(8, 80, 13).name, AIR)
        self.assertEqual(self.save.get_block(8, 80, -1).name, AIR)

        # Each end is judged on its own: closed at z=20, open at z=32.
        self.b.vault(Vault((8, 80, 8), close_ends=QUARTZ, **NAVE), 20, 26)
        self.b.vault(Vault((8, 80, 8), **NAVE), 27, 32)
        mixed = Vault.read(self.save, (8, 80, 24), strict=False)
        self.assertEqual(mixed.found, (20, 32))
        with self.assertRaises(Refused):
            self.b.vault(mixed, None, 15)
        result = self.b.vault(mixed, None, 40)
        self.assertEqual((result.first, result.last), (33, 40))
        self.assertEqual(Vault((8, 80, 8), **NAVE).verify(self.save, 27, 40),
                         [])

    def test_a_closed_run_its_strict_read_misses_is_still_not_continued(self):
        """A profile with no top slab at the foot of any column doubles
        nothing when it closes, so the strict read replays the open rule
        over the closed run without a fault; the continuation still finds
        the wall."""
        rule = dict(profile=(0, 0, 0), depth=(1, 1, 3))
        vault = Vault((8, 80, 8), close_ends=QUARTZ, **rule)
        self.b.vault(vault, 0, 12)
        self.assertEqual(vault.verify(self.save, 0, 12), [])
        read = Vault.read(self.save, (8, 80, 6))
        self.assertEqual(read.differences, ())
        with self.assertRaises(Refused) as caught:
            self.b.vault(read, None, 20)
        self.assertIn("z=12: walled with", caught.exception.faults[0])
        self.assertEqual(self.save.get_block(8, 80, 13).name, AIR)

    def test_clear_under_empties_the_end_walls_span_too(self):
        """A vault dropped onto a solid hall with clear_under empties the
        span under its shell, the end courses' with the rest, so the end
        walls set there need no overwrite, as an open run needs none; the
        shell itself is held to the occupancy check as before."""
        self.b.fill((2, 75, 0), (14, 76, 12), "minecraft:stone")
        vault = self.closed()
        result = self.b.vault(vault, 0, 12, clear_under=True)
        self.assertEqual(result.closed, BlockState(QUARTZ))
        self.assertEqual(vault.verify(self.save, 0, 12), [])
        self.assertEqual(self.save.get_block(8, 75, 6).name, AIR)
        self.assertEqual(self.save.get_block(2, 75, 0).name, QUARTZ)
        self.assertEqual(self.save.get_block(14, 76, 12).name, QUARTZ)

        # A block in the shell over the span is still in the way.
        self.save.set_block(3, 77, 20, "minecraft:oak_planks")
        self.b.fill((2, 75, 20), (14, 76, 26), "minecraft:stone")
        before = self.b.blocks_set
        with self.assertRaises(Refused) as caught:
            self.b.vault(self.closed(), 20, 26, clear_under=True)
        self.assertEqual(len(caught.exception.faults), 1)
        self.assertIn("(3, 77, 20) holds minecraft:oak_planks",
                      caught.exception.faults[0])
        self.assertEqual(self.b.blocks_set, before)
        self.assertEqual(self.save.get_block(8, 75, 23).name,
                         "minecraft:stone")

    def test_an_end_wall_of_nothing_is_refused(self):
        for block in (AIR, "minecraft:cave_air", True):
            with self.subTest(block=block):
                with self.assertRaises(NBTError):
                    self.closed(close_ends=block)
        with self.assertRaises(TypeError):
            self.closed(close_ends=Mix([(QUARTZ, 1)]))
        for block in (None, False):
            self.assertIsNone(self.closed(close_ends=block).close_ends)

    def test_a_closed_section_needs_its_block(self):
        with self.assertRaises(NBTError) as caught:
            Vault((8, 80, 8), **NAVE).section(closed=True)
        self.assertIn("no close_ends", str(caught.exception))

    def test_a_taken_cell_in_the_end_wall_is_refused(self):
        self.save.set_block(5, 76, 0, "minecraft:oak_planks")
        with self.assertRaises(Refused) as caught:
            self.b.vault(self.closed(), 0, 12)
        self.assertIn("(5, 76, 0) holds minecraft:oak_planks",
                      caught.exception.faults[0])
        self.assertEqual(self.b.blocks_set, 0)
        self.assertEqual(self.save.get_block(8, 80, 6).name, AIR)
        self.b.vault(self.closed(), 0, 12, overwrite=True)
        self.assertEqual(self.save.get_block(5, 76, 0).name, QUARTZ)

    def test_the_springing_is_where_clear_under_starts(self):
        self.assertEqual(Vault((8, 80, 8), **NAVE).springing, 75)
        arch = Vault((8, 90, 8), profile=arch_profile(9, 9, eaves=2))
        self.assertEqual(arch.springing, min(
            arch.top(d) - arch.thickness(d)
            for d in range(len(arch.profile))) // 2)
        self.b.fill((1, 70, 0), (15, 79, 12), "minecraft:stone")
        vault = self.closed()
        self.b.vault(vault, 0, 12, overwrite=True, clear_under=True)
        self.assertEqual(self.save.get_block(8, 75, 6).name, AIR)
        self.assertEqual(self.save.get_block(8, 74, 6).name, "minecraft:stone")
        self.assertEqual(self.save.get_block(8, 76, 0).name, QUARTZ,
                         "and the end walls stand in what it cleared")
        self.assertEqual(vault.verify(self.save, 0, 12), [])

    def test_a_vault_along_x_closes_its_ends_too(self):
        vault = Vault((8, 80, 8), axis="x", close_ends=QUARTZ, **NAVE)
        self.b.vault(vault, 0, 6)
        self.assertEqual(vault.verify(self.save, 0, 6), [])
        self.assertEqual(self.save.get_block(0, 76, 5).name, QUARTZ)
        self.assertEqual(self.save.get_block(6, 76, 5).name, QUARTZ)
        self.assertEqual(self.save.get_block(3, 76, 5).name, AIR)

    def test_a_run_of_one_course_is_closed_on_both_sides(self):
        vault = self.closed()
        self.b.vault(vault, 4, 4)
        self.assertEqual(vault.verify(self.save, 4, 4), [])
        self.assertEqual(self.save.get_block(5, 76, 4).name, QUARTZ)


if __name__ == "__main__":
    unittest.main()
