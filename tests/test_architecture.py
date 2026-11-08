"""The two tiers, held apart by a test rather than a promise.

``heaton_voxel`` proper is the deterministic editing library: one correct
answer per question, twin-ported to C#, gated by the conformance corpus.
``heaton_voxel.craft`` is the judged tier above it: palettes, roofs, road
mixes, block colors — game knowledge and taste, Python-only by policy.

The rules this file enforces are the ones that keep that split real:

* the dependency arrow points down and only down — no module below the
  line imports ``craft``;
* the top-level namespace re-exports nothing from ``craft``, so taste is
  always imported explicitly and visibly;
* the conformance corpus generator never touches ``craft``, because the
  corpus defines what the C# twin must reproduce and the judged tier is
  deliberately outside that contract.

Imports are read with ``ast``, not regex: prose mentions a module's name
in docstrings freely, and only an actual import is a violation.
"""

from __future__ import annotations

import ast
import importlib
import os
import re
import shutil
import subprocess
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

PACKAGE = os.path.join(_ROOT, "heaton_voxel")


def _imports_of(path: str) -> set[str]:
    """Every module name a file imports, absolute or relative."""
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            # Relative imports keep their dots so ".craft" and
            # "..craft.build" stay recognizable as craft-bound.
            found.add("." * node.level + (node.module or ""))
    return found


def _lower_tier_files() -> list[str]:
    """The deterministic library's modules — not its entry points.

    ``__main__.py`` and the ``cli.py`` files are consumers standing beside
    the tiers, like ``examples/`` and ``tools/``: PORTING.md already scopes
    them out of the twin contract, and the render subcommand is exactly a
    CLI reaching up into the judged tier on a user's behalf.
    """
    files = []
    for base, dirs, names in os.walk(PACKAGE):
        dirs[:] = [d for d in dirs if d not in ("craft", "__pycache__")]
        files.extend(os.path.join(base, n) for n in names
                     if n.endswith(".py")
                     and n not in ("__main__.py", "cli.py"))
    return sorted(files)


def _mentions_craft(module: str) -> bool:
    stripped = module.lstrip(".")
    return stripped == "craft" or stripped.startswith("craft.") \
        or "heaton_voxel.craft" in module


class TierBoundaryTests(unittest.TestCase):
    def test_the_arrow_points_down(self):
        """No deterministic module imports the judged tier."""
        offenders = {
            os.path.relpath(path, _ROOT): sorted(
                module for module in _imports_of(path)
                if _mentions_craft(module))
            for path in _lower_tier_files()
        }
        offenders = {path: found for path, found in offenders.items() if found}
        self.assertEqual(
            offenders, {},
            "the editing library must never import heaton_voxel.craft: "
            f"{offenders}")

    def test_top_level_reexports_no_taste(self):
        """``from heaton_voxel import Builder`` must stay impossible —
        the judged tier is imported explicitly or not at all. Not only
        ``craft.__all__``: most of the vocabulary lives in its modules
        (``geometry.line``, ``noise.cell_hash``, ``style.ROLES``), so the
        top level's own source must import nothing from craft, no top-level
        name may be an object craft defines, and no name any craft module
        exports may be the same object at the top level."""
        import importlib
        import pkgutil

        import heaton_voxel
        from heaton_voxel import craft

        for name in craft.__all__:
            self.assertFalse(
                hasattr(heaton_voxel, name),
                f"heaton_voxel re-exports {name!r} from craft")

        init = os.path.join(PACKAGE, "__init__.py")
        self.assertEqual(
            sorted(m for m in _imports_of(init) if _mentions_craft(m)), [],
            "heaton_voxel/__init__.py imports from the judged tier")

        for name, value in vars(heaton_voxel).items():
            if name == "craft" or name.startswith("__"):
                continue                  # the subpackage itself, once loaded
            home = getattr(value, "__module__", None)
            if isinstance(value, type(heaton_voxel)):
                home = value.__name__
            self.assertFalse(
                isinstance(home, str)
                and home.startswith("heaton_voxel.craft"),
                f"heaton_voxel.{name} is craft's ({home})")

        modules = [craft] + [
            importlib.import_module(info.name)
            for info in pkgutil.walk_packages(craft.__path__,
                                              "heaton_voxel.craft.")
            if not info.name.endswith(".cli")]
        for module in modules:
            for name in getattr(module, "__all__", ()):
                value = getattr(module, name)
                home = getattr(value, "__module__", None)
                lower = (isinstance(home, str)
                         and home.startswith("heaton_voxel.")
                         and not home.startswith("heaton_voxel.craft"))
                self.assertFalse(
                    getattr(heaton_voxel, name, None) is value and not lower,
                    f"heaton_voxel re-exports {name!r} from "
                    f"{module.__name__}")

    #: The lower tier's private names the judged tier imports, each by the
    #: craft file that imports it. They are not the lower tier's contract
    #: -- the C# twin has no such names, and a refactor below the line may
    #: move them -- so a new one is a choice to be made in review, here,
    #: and not by an import nobody notices.
    PRIVATE_DOORS = {
        ("connect", "_candidate"): {"craft/build/builder.py"},
        ("connect", "_derived_state"): {"craft/build/_common.py"},
        ("place", "_cells_for"): {"craft/build/builder.py",
                                  "craft/build/furniture.py"},
        # The water a placement takes, read by a plan before its first
        # write as place_block reads it before its own.
        ("place", "_with_water"): {"craft/build/builder.py"},
    }

    def test_the_judged_tier_reaches_below_only_through_named_private_names(self):
        """The arrow points down, and through public names but for the ones
        :attr:`PRIVATE_DOORS` lists."""
        found: dict = {}
        craft_root = os.path.join(PACKAGE, "craft")
        for base, dirs, names in os.walk(craft_root):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for file in sorted(n for n in names if n.endswith(".py")):
                path = os.path.join(base, file)
                rel = os.path.relpath(path, PACKAGE).replace(os.sep, "/")
                package = rel.split("/")[:-1]
                with open(path, encoding="utf-8") as handle:
                    tree = ast.parse(handle.read(), filename=path)
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ImportFrom):
                        continue
                    module = (node.module or "").split(".") if node.module else []
                    if node.level:
                        if node.level - 1 > len(package):
                            continue
                        target = package[:len(package) - node.level + 1] + module
                    elif module[:1] == ["heaton_voxel"]:
                        target = module[1:]
                    else:
                        continue
                    if not target or target[0] == "craft":
                        continue
                    for alias in node.names:
                        if alias.name.startswith("_"):
                            key = (".".join(target), alias.name)
                            found.setdefault(key, set()).add(rel)
        self.assertEqual(found, self.PRIVATE_DOORS,
                         "craft imports a private name of the lower tier "
                         "that PRIVATE_DOORS does not list: make it public, "
                         "or list it here on purpose")

    def test_corpus_generator_stays_below_the_line(self):
        """The conformance corpus defines what the C# twin must match;
        judged code is outside that contract by design."""
        generator = os.path.join(_ROOT, "fixtures", "make_conformance.py")
        craft_bound = sorted(
            module for module in _imports_of(generator)
            if _mentions_craft(module))
        self.assertEqual(
            craft_bound, [],
            f"make_conformance.py imports the judged tier: {craft_bound}")


class DownstreamApiTests(unittest.TestCase):
    """What the Unity app reads from the judged tier, held public.

    The app lives in a repo of its own and bakes its color tables and its
    block list out of ``craft`` with generators of its own, which its own
    tests hold to these public names and nothing private. This side holds
    the names to existing, so a rename here fails in this suite rather
    than the next time somebody regenerates the app's tables.
    """

    NAMES = {
        "heaton_voxel.craft.colors": (
            "block_palette", "block_heuristics", "WOOD_SPECIES",
            "WOOD_FALLBACK", "BLOCK_UNKNOWN", "biome_palette",
            "biome_heuristics", "BIOME_UNKNOWN", "BIOME_COLORS"),
        "heaton_voxel.craft.biomes": ("FREEZING_BIOMES", "color_for"),
        "heaton_voxel.craft.blockinfo": ("cut_shapes",),
        "heaton_voxel.craft.block_registry": (
            "BLOCK_IDS", "properties_of", "GAME_VERSION", "DATA_VERSION"),
        "heaton_voxel.block_defaults": ("default_properties",),
    }

    def test_the_names_the_app_reads_exist(self):
        """Every name the app's generators read is there, and listed in its
        module's ``__all__`` wherever the module keeps one."""
        for module_name, names in self.NAMES.items():
            module = importlib.import_module(module_name)
            exported = getattr(module, "__all__", None)
            for name in names:
                with self.subTest(name=f"{module_name}.{name}"):
                    self.assertTrue(hasattr(module, name),
                                    f"{module_name} has no {name}")
                    if exported is not None:
                        self.assertIn(name, exported)

    def test_the_color_tables_are_a_leaf(self):
        """``craft.colors`` imports nothing from the package, so render can
        stand on it and an app can vendor it whole."""
        path = os.path.join(PACKAGE, "craft", "colors.py")
        inward = sorted(module for module in _imports_of(path)
                        if module.startswith(".")
                        or module.split(".")[0] == "heaton_voxel")
        self.assertEqual(inward, [], "craft/colors.py imports the package")



class PackagingTests(unittest.TestCase):
    """A package that is not shipped, or not committed, exists only here.

    Splitting ``craft/build.py`` into a package found both at once: the
    package list in ``pyproject.toml`` is written by hand, so an installed
    heaton-voxel would have had no ``craft.build``, and ``.gitignore``'s
    ``build/`` -- meant for packaging output -- ignored every directory
    of that name, so a clone would have had none either. Every test still
    passed, here, where the files were.
    """

    def _packages(self) -> list[str]:
        found = []
        for base, dirs, names in os.walk(PACKAGE):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            if "__init__.py" in names:
                found.append(os.path.relpath(base, _ROOT).replace(os.sep, "."))
        return sorted(found)

    def test_every_package_is_in_the_package_list(self):
        with open(os.path.join(_ROOT, "pyproject.toml"), encoding="utf-8") as f:
            text = f.read()
        listed = re.search(r"^packages\s*=\s*\[(.*?)\]", text, re.S | re.M)
        self.assertIsNotNone(listed, "pyproject.toml lists no packages")
        listed = set(re.findall(r'"([^"]+)"', listed.group(1)))
        missing = [p for p in self._packages() if p not in listed]
        self.assertEqual(missing, [], "packages an install would leave out")

    def test_heaton_voxel_is_the_whole_command_line(self):
        """``heaton-voxel`` once ran the ``world`` command alone, while the
        README taught ``render``, ``schem``, ``audit`` and the rest through
        ``python -m heaton_voxel``: an install put the narrowest of them on
        PATH under the package's own name. Each script must also name a
        function that is there, which nothing short of an install checks.
        """
        with open(os.path.join(_ROOT, "pyproject.toml"), encoding="utf-8") as f:
            text = f.read()
        table = re.search(r"^\[project\.scripts\]\n(.*?)(?=^\[|\Z)", text, re.S | re.M)
        self.assertIsNotNone(table, "pyproject.toml declares no scripts")
        scripts = dict(re.findall(r'^([\w-]+)\s*=\s*"([^"]+)"', table.group(1), re.M))
        self.assertEqual(scripts.get("heaton-voxel"), "heaton_voxel.__main__:main")
        for name, target in scripts.items():
            module, _, attr = target.partition(":")
            with self.subTest(script=name):
                found = getattr(importlib.import_module(module), attr, None)
                self.assertTrue(callable(found), f"{name} names {target}, which is not there")

    def test_the_two_packages_are_one_release(self):
        """The twins ship as ``heaton-voxel`` on PyPI and ``HeatonVoxel`` on
        NuGet, and the corpus makes them one library, so they carry one
        version. The C# one was once unset, which packs as 1.0.0 beside a
        Python 0.1.0.
        """
        with open(os.path.join(_ROOT, "pyproject.toml"), encoding="utf-8") as f:
            python = re.search(r'^version\s*=\s*"([^"]+)"', f.read(), re.M)
        with open(os.path.join(_ROOT, "csharp", "HeatonVoxel", "HeatonVoxel.csproj"),
                  encoding="utf-8") as f:
            csharp = re.search(r"<Version>([^<]+)</Version>", f.read())
        self.assertIsNotNone(python, "pyproject.toml has no version")
        self.assertIsNotNone(csharp, "HeatonVoxel.csproj sets no <Version>")
        self.assertEqual(csharp.group(1), python.group(1))

    def test_the_types_ship_and_the_whole_lower_tier_is_checked(self):
        """``py.typed`` is what makes a caller's type checker read the
        annotations at all, and only an install carries it, so it must be in
        the package data the wheel is built from. What it vouches for is the
        deterministic tier, which ``[tool.mypy]`` checks clean: a module of
        that tier left off its list would ship annotations nobody checked.
        """
        self.assertTrue(os.path.isfile(os.path.join(PACKAGE, "py.typed")))
        with open(os.path.join(_ROOT, "pyproject.toml"), encoding="utf-8") as f:
            text = f.read()
        data = re.search(r"^\[tool\.setuptools\.package-data\]\n(.*?)(?=^\[|\Z)",
                         text, re.S | re.M)
        self.assertIsNotNone(data, "pyproject.toml ships no package data")
        self.assertRegex(data.group(1), r'^heaton_voxel\s*=\s*\[[^]]*"py\.typed"', )
        table = re.search(r"^\[tool\.mypy\]\n(.*?)(?=^\[|\Z)", text, re.S | re.M)
        self.assertIsNotNone(table, "pyproject.toml has no [tool.mypy]")
        listed = re.findall(r'"(heaton_voxel[^"]*)"', table.group(1))
        lower = []
        for base, dirs, names in os.walk(PACKAGE):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", "craft")]
            lower += [os.path.relpath(os.path.join(base, n), _ROOT).replace(os.sep, "/")
                      for n in names if n.endswith(".py")]
        unchecked = [path for path in sorted(lower)
                     if not any(path == entry or path.startswith(entry + "/")
                                for entry in listed)]
        self.assertEqual(unchecked, [], "lower-tier modules mypy does not check")

    def test_no_source_file_is_ignored(self):
        if shutil.which("git") is None or not os.path.isdir(os.path.join(_ROOT, ".git")):
            self.skipTest("not a git checkout")
        sources = []
        for base, dirs, names in os.walk(PACKAGE):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            sources += [os.path.relpath(os.path.join(base, n), _ROOT)
                        for n in names if n.endswith(".py")]
        run = subprocess.run(["git", "check-ignore", "--no-index", *sorted(sources)],
                             cwd=_ROOT, capture_output=True, text=True)
        self.assertEqual(run.stdout.split(), [],
                         "source files .gitignore keeps out of every clone")


if __name__ == "__main__":
    unittest.main(verbosity=2)
