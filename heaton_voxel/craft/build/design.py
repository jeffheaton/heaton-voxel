"""Tower designs: a whole building worked out from a lot, a height and a style.

A :class:`~heaton_voxel.craft.build.massing.Massing` says one tower, and saying
it takes a script: ``examples/setback_tower.py`` sets every rectangle and row by
hand. A street wants fifty and no two alike, so :func:`design_tower` works one
out. Give it a lot, how many stories, and a :class:`TowerStyle`, and it returns
a :class:`TowerDesign`, whose :meth:`TowerDesign.lay` is one call:

    style = TowerStyle("brick", [Scheme("minecraft:bricks",
                                        "minecraft:gray_stained_glass_pane")],
                       window="punched", tiers=(1, 2),
                       shapes=("box", "ell"), roofs=("flat", "stepped"))
    design = design_tower((100, 200, 124, 230), 64, 20, style, seed=7)
    print(design.describe())
    design.lay(builder)

**A style is a family and a design is one of it.** A :class:`Scheme` is one
tower's colors, each named by what it does, and a :class:`TowerStyle` is the
choices a family leaves open: its schemes, how its windows are made, how many
tiers it steps back through, its roofs and its plans. :func:`design_tower` rolls
those choices with :func:`~heaton_voxel.craft.noise.cell_hash`, keyed on the
lot's corner and ``seed``, never a session's ``rng``: one lot and seed is one
building in any session, and a tower laid before it changes nothing. Name a
choice at the call and it is not rolled. Styles are taste, so none lives here:
``examples/tower_styles.py`` has a handful to start from.

**Everything is fitted to the lot.** A face is a
:class:`~heaton_voxel.craft.build.elevation.Fitted` rule, so its bays are worked
out for each wall by
:meth:`~heaton_voxel.craft.build.elevation.Rhythm.spaced`, and a tier stepped
back two columns has walls four columns shorter and bays to suit. A plan is a
rectangle with parts taken out of its back, so the front wall stays whole and
holds the entrance; the tiers above it are the same plan
:meth:`eroded <heaton_voxel.craft.build.walls.Footprint.eroded>`, and the last a
rectangle in its widest part. What climbs it stands in that part too.

**Five window kinds.** ``punched`` is windows in a wall, ``curtain`` glass run
past the floors, ``ribbon`` long strips of it with solid courses between,
``piers`` the glass and its spandrels set a block behind piers two wide, and
``grid`` a frame showing every floor. The rows of each follow ``pitch``, the
rows from one floor to the next.

**A design is held to what any tower is.** :meth:`TowerDesign.lay` goes through
:meth:`~heaton_voxel.craft.build.massing.MassingMixin.tower`, so its plan is
walked to every floor and back before a block of it is written, and a design
that cannot be built is refused whole. A lot too small for what was asked of it
is refused here first, by name. Part of :mod:`heaton_voxel.craft.build`. No
world in anything here but :meth:`TowerDesign.lay` and
:meth:`TowerDesign.verify`.
"""

from __future__ import annotations

from ...blocks import BlockState
from ...nbt import NBTError
from ..light import NO_SPAWNS
from ..noise import cell_hash
from ..style import Mix
from ._common import _whole
from .crown import Crown
from .elevation import Band, Doors, Elevation, Fitted, Glazing, Layers, Rhythm
from .massing import Core, Massing, Tier
from .roof import Roof
from .stairs import SwitchbackStair
from .walls import _whole_block, Footprint

#: How a style's windows are made. See the module docstring.
WINDOWS = ("punched", "curtain", "ribbon", "piers", "grid")
#: What stands on a tower: a deck and a parapet with a plant room on it, glass
#: rings and fins, rings stepping in to a point, or a hip.
ROOFS = ("flat", "crown", "stepped", "hip")
#: A tower's plan: a rectangle, one with parts out of its back, or two towers
#: on one base.
SHAPES = ("box", "ell", "tee", "you", "cross", "twin")

#: The smallest lot each plan stands on, ``(across, deep)``, past which its
#: wings have no room left in them.
_SMALLEST = {"box": (11, 13), "ell": (21, 23), "tee": (27, 23),
             "you": (31, 23), "cross": (29, 29), "twin": (29, 15)}
_AIR = "minecraft:air"


class Scheme:
    """One tower's colors, each named by what it does. No world in it.

    ``frame`` is its walls and piers, ``glass`` a pane or a glass block.
    ``trim`` is what its belts, parapets, terraces and cornices are laid in,
    ``frame`` if none is given; ``tint`` a second glass for the rows that vary,
    ``glass`` if none; ``spandrel`` the floor rows of a bay, ``frame`` if none.
    Each of those is a block, a Material or a Mix, picked at the world cell.
    ``floor`` is what stories are laid in, ``ground`` the ground floor, ``trim``
    if none. ``stair`` and ``landing`` are the one block a stair is cut from and
    paved with, ``door`` the entrance, ``lamp`` what lights it, and ``roof`` a
    Material in stairs and slabs, for a hip.
    """

    __slots__ = ("frame", "glass", "trim", "tint", "spandrel", "floor",
                 "ground", "stair", "landing", "door", "lamp", "roof")

    def __init__(self, frame, glass, *, trim=None, tint=None, spandrel=None,
                 floor="minecraft:smooth_stone", ground=None,
                 stair="minecraft:stone_brick_stairs",
                 landing="minecraft:stone_bricks",
                 door="minecraft:dark_oak_door", lamp="minecraft:glowstone",
                 roof=None):
        owner, noun = "Scheme", "a scheme"
        self.frame = _whole_block(frame, "frame", owner, noun)
        self.glass = _whole_block(glass, "glass", owner, noun)
        self.trim = (self.frame if trim is None
                     else _whole_block(trim, "trim", owner, noun))
        self.tint = (self.glass if tint is None
                     else _whole_block(tint, "tint", owner, noun))
        self.spandrel = (self.frame if spandrel is None
                         else _whole_block(spandrel, "spandrel", owner, noun))
        self.floor = _whole_block(floor, "floor", owner, noun)
        self.ground = (self.trim if ground is None
                       else _whole_block(ground, "ground", owner, noun))
        for name, block in (("stair", stair), ("landing", landing),
                            ("door", door), ("lamp", lamp)):
            if not isinstance(block, (str, BlockState)):
                raise TypeError(f"Scheme({name}=) is one block, by id or "
                                f"state, not {block!r}")
        self.stair = stair
        self.landing = landing
        self.door = door
        self.lamp = lamp
        self.roof = roof

    def __repr__(self) -> str:
        return f"Scheme({_named(self.frame)}, {_named(self.glass)})"


class TowerStyle:
    """A family of towers: what its designs choose among. No world in it.

    ``schemes`` is one :class:`Scheme` or more. ``window`` is one of
    :data:`WINDOWS`, ``bay`` the columns a bay would like and ``pitch`` the rows
    from floor to floor, four or five. ``lobby`` is how many of those the ground
    story takes, one or two. The rest are what a design rolls: ``tiers``, the
    tiers it may step back through; ``roofs``, from :data:`ROOFS`; and
    ``shapes``, from :data:`SHAPES`. ``step`` is the columns a tier steps back.

    Refused (an ``NBTError``): none of a choice, one that is no choice, a
    ``pitch`` a stair cannot serve, a hip with no scheme to roof it.
    """

    __slots__ = ("name", "schemes", "window", "bay", "pitch", "lobby", "tiers",
                 "step", "roofs", "shapes")

    def __init__(self, name, schemes, *, window="punched", bay: int = 3,
                 pitch: int = 4, lobby: int = 1, tiers=(1,), step: int = 2,
                 roofs=("flat",), shapes=("box",)):
        self.name = str(name)
        self.schemes = ((schemes,) if isinstance(schemes, Scheme)
                        else tuple(schemes))
        if not self.schemes or not all(isinstance(scheme, Scheme)
                                       for scheme in self.schemes):
            raise NBTError(f"a style has at least one Scheme, got {schemes!r}")
        if window not in WINDOWS:
            raise NBTError(f"window is one of {WINDOWS}, got {window!r}")
        self.window = window
        self.bay = _whole(bay, "bay")
        if self.bay < 1:
            raise NBTError(f"a bay is at least one column, got {bay}")
        self.pitch = _whole(pitch, "pitch")
        if self.pitch not in (4, 5):
            raise NBTError(f"pitch is the rows from floor to floor, 4 or 5, "
                           f"got {pitch}: a stair's flights need the head "
                           f"room, and a taller story no stair here serves")
        self.lobby = _whole(lobby, "lobby")
        if self.lobby not in (1, 2):
            raise NBTError(f"lobby is 1 or 2 stories high, got {lobby}")
        self.step = _whole(step, "step")
        if self.step < 1:
            raise NBTError(f"a tier steps back at least a column, got {step}")
        self.tiers = tuple(_whole(count, "a tier count") for count in tiers)
        if not self.tiers or min(self.tiers) < 1:
            raise NBTError(f"tiers is how many a tower may have, got {tiers!r}")
        self.roofs = tuple(roofs)
        self.shapes = tuple(shapes)
        for given, known, word in ((self.roofs, ROOFS, "roofs"),
                                   (self.shapes, SHAPES, "shapes")):
            if not given or set(given) - set(known):
                raise NBTError(f"{word} is taken from {known}, got {given!r}")
        if "hip" in self.roofs and any(scheme.roof is None
                                       for scheme in self.schemes):
            raise NBTError("a hip is cut from a scheme's roof=, a Material in "
                           "stairs and slabs, and a scheme here has none")

    def __repr__(self) -> str:
        return f"TowerStyle({self.name!r}, {len(self.schemes)} schemes)"


class TowerDesign:
    """One building, worked out and not yet laid. No world in it.

    ``massing`` is its tiers, ``cores`` what climbs them, ``crown`` what stands
    on top if anything does. ``doorstep`` is the block outside the entrance
    and ``plaza`` the rectangle to level round the building, which holds it.
    ``style``, ``scheme``, ``shape``, ``roof`` and ``front`` are what was chosen.
    """

    __slots__ = ("massing", "cores", "crown", "doorstep", "plaza", "style",
                 "scheme", "shape", "roof", "front", "stories")

    def __init__(self, massing, cores, crown, doorstep, plaza, *, style, scheme,
                 shape, roof, front, stories):
        self.massing = massing
        self.cores = cores
        self.crown = crown
        self.doorstep = doorstep
        self.plaza = plaza
        self.style = style
        self.scheme = scheme
        self.shape = shape
        self.roof = roof
        self.front = front
        self.stories = stories

    @property
    def top(self) -> int:
        """The highest row it reaches."""
        return self.massing.top + (self.crown.height if self.crown else 0)

    def lay(self, builder, *, overwrite: bool = False):
        """Lay it with ``builder``, walked before it is written, and light
        every floor and terrace so nothing spawns on one. Returns the
        :class:`~heaton_voxel.craft.build.massing.TowerResult`."""
        result = builder.tower(self.massing, core=self.cores, crown=self.crown,
                               doorstep=self.doorstep, overwrite=overwrite)
        for cells in (*result.floors.values(), *result.terraces.values()):
            if cells:
                builder.light_cells(cells, light=self.scheme.lamp,
                                    light_level=NO_SPAWNS, clear_above=False)
        return result

    def verify(self, save, *, dimension: str | None = None) -> list:
        """Every cell of its walls and crown not as the rules say, in words."""
        wrong = self.massing.verify(save, dimension=dimension)
        if self.crown is not None:
            tiers = self.massing.tiers
            for index, tier in enumerate(tiers):
                if not any(other.floor_y > tier.floor_y and tier.holds(other)
                           for other in tiers[index + 1:]):
                    wrong += self.crown.verify(save, *tier.rectangle,
                                               tier.top + 1,
                                               dimension=dimension)
        return wrong

    def describe(self) -> str:
        """It in words: what was chosen, then its tiers."""
        return (f"{self.style.name} tower, {self.stories} stories to row "
                f"{self.top}: {self.shape} plan, {self.roof} roof, in "
                f"{_named(self.scheme.frame)} and "
                f"{_named(self.scheme.glass)}\n{self.massing.describe()}")

    def __repr__(self) -> str:
        return (f"<TowerDesign {self.style.name} {self.shape}, "
                f"{self.stories} stories to row {self.top}>")


def _named(block) -> str:
    """A block in words, its namespace off."""
    if isinstance(block, Mix):
        return " and ".join(str(entry).removeprefix("minecraft:")
                            for entry, _ in block.entries)
    return str(block).removeprefix("minecraft:")


class _Lot:
    """A lot from its front: ``u`` across from the low end, ``v`` back from the
    front wall."""

    __slots__ = ("x0", "z0", "x1", "z1", "front", "across", "deep")

    def __init__(self, x0, z0, x1, z1, front):
        self.x0, self.z0, self.x1, self.z1 = x0, z0, x1, z1
        self.front = front
        wide, long = x1 - x0 + 1, z1 - z0 + 1
        self.across, self.deep = ((wide, long) if front in ("north", "south")
                                  else (long, wide))

    def cell(self, u: int, v: int) -> tuple:
        if self.front == "south":
            return self.x0 + u, self.z1 - v
        if self.front == "north":
            return self.x0 + u, self.z0 + v
        if self.front == "east":
            return self.x1 - v, self.z0 + u
        return self.x0 + v, self.z0 + u

    def box(self, u0: int, v0: int, u1: int, v1: int) -> tuple:
        ax, az = self.cell(u0, v0)
        bx, bz = self.cell(u1, v1)
        return min(ax, bx), min(az, bz), max(ax, bx), max(az, bz)

    @property
    def line(self) -> int:
        """The front wall's own coordinate, where its entrance goes."""
        return {"south": self.z1, "north": self.z0, "east": self.x1,
                "west": self.x0}[self.front]

    def doorstep(self, y: int) -> tuple:
        x, z = self.cell(self.across // 2, -2)
        return (x, y, z)


def _plan(lot: _Lot, shape: str) -> tuple:
    """``(footprint, heart)``: the plan and the rectangle at its widest."""
    w, d = lot.across, lot.deep
    whole = lot.box(0, 0, w - 1, d - 1)
    if shape == "box":
        return Footprint(whole), whole
    back = max(6, d * 2 // 5)
    bar = lot.box(0, 0, w - 1, d - back - 1)
    if shape == "ell":
        side = max(6, w * 2 // 5)
        cut = [lot.box(w - side, d - back, w - 1, d - 1)]
        stem = lot.box(0, 0, w - side - 1, d - 1)
        heart = max(bar, stem, key=_area)
    elif shape == "tee":
        side = (w - max(11, w * 2 // 5)) // 2
        cut = [lot.box(0, d - back, side - 1, d - 1),
               lot.box(w - side, d - back, w - 1, d - 1)]
        heart = bar
    elif shape == "you":
        side = (w - max(7, w // 3)) // 2
        cut = [lot.box(side, d - back, w - side - 1, d - 1)]
        heart = bar
    else:
        side = w * 2 // 7
        step = d * 2 // 7
        cut = [lot.box(0, d - step, side - 1, d - 1),
               lot.box(w - side, d - step, w - 1, d - 1),
               lot.box(0, 0, side - 1, step - 1),
               lot.box(w - side, 0, w - 1, step - 1)]
        heart = max(lot.box(0, step, w - 1, d - step - 1),
                    lot.box(side, 0, w - side - 1, d - 1), key=_area)
    return Footprint(whole, less=cut), heart


def _area(box) -> int:
    return (box[2] - box[0] + 1) * (box[3] - box[1] + 1)


def _pier(n: int) -> Rhythm:
    return Rhythm(("pier", n))


def _across(n: int, *, closed: bool, target: int, pier_width: int = 1,
            center=None) -> Rhythm:
    """The columns of a wall ``n`` long; a wall too short for a bay is all
    pier."""
    room = 1 + 2 * pier_width if closed else 1
    if center is not None:
        room += center[1] - 1
    if n < room:
        return _pier(n)
    return Rhythm.spaced(n, target=target, closed=closed,
                         pier_width=pier_width, center=center)


def _keep(rhythm: Rhythm, roles: dict) -> dict:
    """The roles a band's rhythm has, of those offered."""
    return {role: value for role, value in roles.items()
            if role in rhythm.roles}


def _shaft(style, scheme, wall, recess: bool) -> tuple:
    """``(rhythm, roles)`` of the stories of ``wall``. Glass set back from a
    wall that meets another at an inside corner pinches the floor there into
    a pocket nobody can reach, so ``recess`` is for a rectangle's walls."""
    n = len(wall.along)
    rows = style.pitch - 2
    window = style.window
    glass, tint = scheme.glass, scheme.tint
    if window == "punched":
        lights = (glass,) * (rows - 1) + (tint, scheme.frame)
        return (_across(n, closed=wall.ends, target=style.bay),
                {"pier": scheme.frame,
                 "bay": Glazing(*lights, spandrel=scheme.spandrel)})
    if window == "curtain":
        lights = (tint,) + (glass,) * (style.pitch - 1)
        return (_across(n, closed=wall.ends, target=style.bay),
                {"pier": scheme.frame, "bay": Glazing(*lights)})
    if window == "ribbon":
        lights = (glass,) * (rows + 1)
        return (_across(n, closed=wall.ends, target=style.bay * 4),
                {"pier": scheme.frame,
                 "bay": Glazing(*lights, spandrel=scheme.spandrel)})
    if window == "piers":
        lights = (glass,) * (rows - 1) + (tint, scheme.spandrel)
        bay = Glazing(*lights, spandrel=scheme.spandrel)
        return (_across(n, closed=True, target=style.bay, pier_width=2),
                {"pier": scheme.frame,
                 "bay": Layers(_AIR, bay) if recess else bay})
    lights = (tint,) + (glass,) * rows
    return (_across(n, closed=wall.ends, target=style.bay),
            {"pier": scheme.frame,
             "bay": Glazing(*lights, spandrel=scheme.trim)})


def _ground(style, scheme, wall, rows: int, entrance: bool) -> list:
    """The ground story of ``wall``: shop glass between piers, two rows of
    doors in the middle of the front."""
    n = len(wall.along)
    center = None
    if entrance:
        center = ("entrance", 2 if n % 2 == 0 else 3)
    across = _across(n, closed=wall.ends, target=max(5, style.bay + 2),
                     center=center)
    low = {"pier": scheme.frame, "bay": scheme.glass,
           "entrance": Doors(scheme.door)}
    high = {"pier": scheme.frame, "bay": scheme.tint,
            "entrance": scheme.glass}
    return [Band(2, across, _keep(across, low)),
            Band(rows - 2, across, _keep(across, high))]


def _faces(style, scheme, *, ground: int = 0, head: int = 1,
           front: tuple | None = None, recess: bool = True) -> Fitted:
    """The rule of a tier's walls: ``ground`` rows of ground story and a belt
    if it has one, its stories, and ``head`` rows of trim. ``front`` is
    ``(way, line)``, the wall that holds the entrance."""

    def face(wall):
        n = len(wall.along)
        belt = Rhythm(("trim", n))
        bands: list = []
        if ground:
            entrance = (front is not None and wall.outward == front[0]
                        and wall.at == front[1])
            bands += _ground(style, scheme, wall, ground, entrance)
            bands.append(Band(1, belt, {"trim": scheme.trim}))
        across, roles = _shaft(style, scheme, wall, recess)
        bands.append(Band(None, across, _keep(across, roles)))
        bands.append(Band(head, belt, {"trim": scheme.trim}))
        return Elevation(*bands)

    return Fitted(face)


def _plain(scheme) -> Fitted:
    """A wall with nothing in it, for a plant room."""
    return Fitted(lambda wall: Elevation(
        Band(None, Rhythm(("trim", len(wall.along))), {"trim": scheme.trim})))


def _shares(stories: int, tiers: int) -> list:
    """``stories`` over ``tiers``, the lowest taking most, two each at least."""
    weights = (5, 3, 2, 1, 1)[:tiers]
    total = sum(weights)
    counts = [max(2, stories * weight // total) for weight in weights]
    counts[0] += stories - sum(counts)
    return counts


def _solid_glass(block):
    """The block a pane is cut from, or ``block`` itself."""
    if isinstance(block, BlockState) and block.name.endswith("_pane"):
        return BlockState(block.name.removesuffix("_pane"), {})
    return block


def _crown(roof: str, rectangle: tuple, scheme):
    """What stands on the top tier, fitted to it, or None where it has no
    room."""
    x0, z0, x1, z1 = rectangle
    room = (min(x1 - x0, z1 - z0) + 1) // 2
    if roof == "crown":
        if room < 4:
            return None
        rings = tuple((2, inset) for inset in range(2, min(room - 1, 5) + 1))
        inner = rings[-1][1] - 1
        return Crown(rings, skin=_solid_glass(scheme.glass), corner=scheme.trim,
                     liner=scheme.trim, fin=scheme.trim, reach=-1, inner=inner,
                     rise=3, pitch=2, lamp=scheme.lamp,
                     lamps=min(4, inner + 2), cornice=scheme.trim, brackets=2,
                     deck=scheme.trim)
    if roof == "stepped":
        if room < 2:
            return None
        return Crown(tuple((1, inset) for inset in range(1, room)),
                     skin=scheme.trim, deck=scheme.trim)
    return None


def _along(box: tuple) -> str:
    """The way a stair runs in a rectangle: its longer one."""
    return "x" if box[2] - box[0] > box[3] - box[1] else "z"


def _core_box(cx: int, cz: int, pitch: int, axis: str) -> tuple:
    """The rectangle of a core, wall and all, round ``(cx, cz)``."""
    if axis == "x":
        return cx - 3, cz - 3, cx + pitch - 2, cz + 2
    return cx - 3, cz - 3, cx + 2, cz + pitch - 2


def _core(cx: int, cz: int, y: int, pitch: int, scheme, top: int,
          axis: str) -> Core:
    x0, z0, x1, z1 = _core_box(cx, cz, pitch, axis)
    stair = SwitchbackStair((x0 + 1, z0 + 1), (x1 - 1, z1 - 1), axis=axis,
                            start="southwest", start_y=y + 1, landing=1,
                            stair=scheme.stair, wood=scheme.landing)
    return Core(x0, z0, x1, z1, wall=scheme.frame, stair=stair, top=top,
                lamp=scheme.lamp)


def _recessed(plan, pitch: int) -> bool:
    """Whether glass may stand a block behind this plan's walls: a rectangle
    with a floor two wide round its core, since one wide would be all glass
    but for pockets behind the piers."""
    x0, z0, x1, z1 = plan.bounds
    short, long = sorted((x1 - x0, z1 - z0))
    return (len(plan.columns) == _area(plan.bounds) and short >= 12
            and long >= pitch + 8)


def _floors_join(plans, cx: int, cz: int, pitch: int, axis: str) -> bool:
    """Whether every plan's floor is one floor with a core at the center."""
    x0, z0, x1, z1 = _core_box(cx, cz, pitch, axis)
    core = {(x, z) for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)}
    return all(Footprint._joined(plan.inner - core) for plan in plans)


def _center(box: tuple) -> tuple:
    return (box[0] + box[2] + 1) // 2, (box[1] + box[3] + 1) // 2


def _inset(box: tuple, by: int) -> tuple:
    return box[0] + by, box[1] + by, box[2] - by, box[3] - by


def _holds_core(box: tuple, pitch: int) -> bool:
    """Whether a rectangle has room for a core and a floor round it, the core
    along its longer side."""
    short, long = sorted((box[2] - box[0], box[3] - box[1]))
    return short >= 9 and long >= pitch + 7


def _pick(x: int, z: int, seed: int, salt: int, options):
    """One of ``options``, rolled for the lot's corner."""
    return options[cell_hash(x, salt, z, seed) % len(options)]


def design_tower(lot, y: int, stories: int, style, *, seed: int = 0,
                 front: str = "south", shape=None, roof=None, tiers=None,
                 scheme=None) -> TowerDesign:
    """Work out a tower. See the module docstring.

    ``lot`` is the building's ``(x0, z0, x1, z1)``, ``y`` the ground it stands
    on and ``stories`` its floors over the ground one. ``front`` is the way its
    entrance faces. ``shape``, ``roof``, ``tiers`` and ``scheme`` name a choice
    rather than roll it, and may be outside the style's own. A plan the lot
    cannot hold falls back on a smaller one when it was rolled.

    Refused (an ``NBTError``): a style that is none, a ``front`` that is no
    compass word, fewer than two stories, and a lot too small for a plan named
    here, or for a core at all.
    """
    if not isinstance(style, TowerStyle):
        raise NBTError(f"design_tower takes a TowerStyle, got "
                       f"{type(style).__name__}")
    if front not in ("north", "east", "south", "west"):
        raise NBTError(f"front is north, east, south or west, got {front!r}")
    y = _whole(y, "y")
    stories = _whole(stories, "stories")
    seed = _whole(seed, "seed")
    if stories < 2:
        raise NBTError(f"a tower has two stories at least, got {stories}")
    xs = sorted((_whole(lot[0], "x0"), _whole(lot[2], "x1")))
    zs = sorted((_whole(lot[1], "z0"), _whole(lot[3], "z1")))
    place = _Lot(xs[0], zs[0], xs[1], zs[1], front)
    x0, z0 = xs[0], zs[0]

    pitch = style.pitch
    fits = [kind for kind, (w, d) in _SMALLEST.items()
            if place.across >= w and place.deep >= d + pitch - 4]
    if not fits:
        raise NBTError(f"a lot {place.across} across and {place.deep} deep "
                       f"has no room for a core and a floor round it")
    if shape is None:
        shape = _pick(x0, z0, seed, 1, [kind for kind in style.shapes
                                        if kind in fits] or ["box"])
    elif shape not in SHAPES:
        raise NBTError(f"shape is one of {SHAPES}, got {shape!r}")
    elif shape not in fits:
        wants = _SMALLEST[shape]
        raise NBTError(f"a {shape} plan wants a lot {wants[0]} across and "
                       f"{wants[1]} deep, and this one is {place.across} by "
                       f"{place.deep}")
    if scheme is None:
        scheme = _pick(x0, z0, seed, 2, style.schemes)
    if roof is None:
        roof = _pick(x0, z0, seed, 3, style.roofs)
    elif roof not in ROOFS:
        raise NBTError(f"roof is one of {ROOFS}, got {roof!r}")
    elif roof == "hip" and scheme.roof is None:
        raise NBTError("a hip is cut from a scheme's roof=, and this one has "
                       "none")
    if tiers is None:
        tiers = _pick(x0, z0, seed, 4, style.tiers)
    tiers = max(1, min(_whole(tiers, "tiers"), stories // 3 or 1))

    first = y + pitch * style.lobby
    floors = [first + pitch * index for index in range(stories)]
    ground = pitch * style.lobby - 1
    entry = dict(ground=ground, front=(front, place.line))
    plain = {(head, recess): _faces(style, scheme, head=head, recess=recess,
                                    **entry)
             for head in (1, 2, 3) for recess in (False, True)}
    settings = (style, scheme, y, floors, pitch, plain, roof)
    if shape == "twin":
        return _twin(place, stories, *settings)
    return _stack(place, shape, tiers, stories, *settings)


def _finish(place, massing, cores, crown, roof, style, scheme, shape, stories,
            y) -> TowerDesign:
    x0, z0, x1, z1 = (place.x0, place.z0, place.x1, place.z1)
    return TowerDesign(massing, cores, crown, place.doorstep(y),
                       (x0 - 3, z0 - 3, x1 + 3, z1 + 3), style=style,
                       scheme=scheme, shape=shape, roof=roof,
                       front=place.front, stories=stories)


def _top_of(crown, last: int, pitch: int) -> tuple:
    """``(top row, head rows, core's top row)`` of a tower's last tier."""
    if crown is not None:
        return last + pitch + 1, 2, last + pitch
    return last + pitch - 1, 1, last + pitch - 1


def _roofed(roof: str, scheme) -> dict:
    """What a last tier with no crown has over it."""
    if roof == "hip":
        return dict(roof=Roof(rings="square", pitch=2, block=scheme.roof,
                              slab=scheme.roof, stair=scheme.roof),
                    attic="hollow")
    return dict(deck=scheme.trim, parapet=scheme.trim)


def _plant_room(box: tuple, top: int, scheme) -> Tier | None:
    """A plain room on a flat roof, over the stair, or None where the roof is
    too small to leave a walk round it."""
    if box[2] - box[0] < 8 or box[3] - box[1] < 8:
        return None
    cx, cz = _center(box)
    return Tier(cx - 3, cz - 3, cx + 3, cz + 3, _plain(scheme),
                floor_y=top + 1, top=top + 4, deck=scheme.trim)


def _stack(place, shape, tiers, stories, style, scheme, y, floors, pitch, plain,
           roof) -> TowerDesign:
    """One tower, its tiers stepping back over its plan."""
    base, heart = _plan(place, shape)
    # Each tier is the plan a step further in, while that leaves a floor and
    # the heart's rectangle holds the core; the last is that rectangle where
    # a crown wants one.
    wanted = roof in ("crown", "stepped")
    plans: list = [base]
    boxes: list = [heart]
    for index in range(1, tiers):
        by = style.step * index
        box = _inset(heart, by)
        if not _holds_core(box, pitch):
            break
        try:
            plans.append(base.eroded(by))
        except NBTError:                 # pure geometry: a wing went away
            break
        boxes.append(box)
    if wanted and shape != "box" and len(plans) == 1:
        # A crown wants a rectangle, and a wall run straight on from the one
        # under it would stand in that one's parapet: step back, or do
        # without.
        box = _inset(heart, style.step)
        if _holds_core(box, pitch):
            plans.append(Footprint(box))
            boxes.append(box)
        else:
            wanted = False
            roof = "flat"
    if wanted and shape != "box":
        plans[-1] = Footprint(boxes[-1])
    if not _holds_core(boxes[-1], pitch):
        raise NBTError(f"a {shape} plan on a lot {place.across} by "
                       f"{place.deep} has no room for a core")
    # A core in a narrow floor can cut a wing off from it, and the walk would
    # refuse the tower: that tier does not step back.
    cx, cz = _center(boxes[-1])
    axis = _along(boxes[-1])
    while len(plans) > 1 and not _floors_join(plans, cx, cz, pitch, axis):
        plans.pop()
        boxes.pop()
        if wanted and shape != "box":
            plans[-1] = Footprint(boxes[-1])
        cx, cz = _center(boxes[-1])
        axis = _along(boxes[-1])
    count = len(plans)
    counts = _shares(stories, count)
    crown = _crown(roof, plans[-1].bounds, scheme)
    if roof in ("crown", "stepped") and crown is None:
        roof = "flat"
    made: list = []
    at = 0
    floor_y = y
    last_top = wall_top = None
    for index, (plan, stories_here) in enumerate(zip(plans, counts)):
        levels = tuple(floors[at:at + stories_here])
        at += stories_here
        last = index == count - 1
        kept = dict(floor_y=floor_y, stories=levels, floor=scheme.floor)
        if index == 0:
            kept["ground"] = scheme.ground
        if last:
            top, head, wall_top = _top_of(crown, levels[-1], pitch)
            if crown is None:
                kept.update(_roofed(roof, scheme))
        else:
            top, head = levels[-1] + 2, 3
            kept["terrace"] = scheme.trim
        recess = _recessed(plan, pitch)
        face = (plain[head, recess] if index == 0
                else _faces(style, scheme, head=head, recess=recess))
        made.append(Tier.over(plan, face, top=top, **kept))
        floor_y = levels[-1]
        last_top = top
    if roof == "flat":
        room = _plant_room(plans[-1].bounds, last_top, scheme)
        if room is not None and room.columns <= plans[-1].inner:
            made.append(room)
    core = _core(cx, cz, y, pitch, scheme, wall_top, axis)
    return _finish(place, Massing(*made), (core,), crown, roof, style, scheme,
                   shape, stories, y)


def _twin(place, stories, style, scheme, y, floors, pitch, plain,
          roof) -> TowerDesign:
    """Two towers on one base, the base a few stories and the rest theirs."""
    low = max(2, stories // 5)
    w, d = place.across, place.deep
    gap = 3 + (w % 2 == 0)
    side = (w - 2 - gap) // 2
    left = place.box(1, 1, side, d - 2)
    right = place.box(w - 1 - side, 1, w - 2, d - 2)
    base = (place.x0, place.z0, place.x1, place.z1)
    if not (_holds_core(left, pitch) and _holds_core(right, pitch)):
        raise NBTError(f"two towers on a lot {place.across} by {place.deep} "
                       f"have no room for a core each")
    if roof == "hip":
        roof = "flat"
    recess = _recessed(Footprint(left), pitch)
    podium = Tier(*base, plain[3, recess], floor_y=y, top=floors[low - 1] + 2,
                  stories=tuple(floors[:low]), floor=scheme.floor,
                  terrace=scheme.trim, ground=scheme.ground)
    rest = tuple(floors[low:])
    last = rest[-1]
    crown = _crown(roof, left, scheme)
    if crown is None:
        roof = "flat"
    top, head, wall_top = _top_of(crown, last, pitch)
    kept = dict(floor_y=floors[low - 1], top=top, stories=rest,
                floor=scheme.floor)
    if crown is None:
        kept.update(_roofed(roof, scheme))
    face = _faces(style, scheme, head=head, recess=recess)
    towers = [Tier(*box, face, **kept) for box in (left, right)]
    cores = tuple(_core(*_center(box), y, pitch, scheme, wall_top, _along(box))
                  for box in (left, right))
    return _finish(place, Massing(podium, *towers), cores, crown, roof, style,
                   scheme, "twin", stories, y)


__all__ = ["design_tower", "ROOFS", "Scheme", "SHAPES", "TowerDesign",
           "TowerStyle", "WINDOWS"]
