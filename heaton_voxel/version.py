"""Which build of heaton-voxel this is.

``VERSION`` is the package's version: the one ``pyproject.toml`` and the C#
twin's csproj carry, and ``heaton_voxel.__version__``; tests/test_architecture.py
holds them equal. The Build Library workflow writes this file afresh before
it builds the wheel, stamping ``BUILD_DATE`` (UTC, ISO 8601) and ``BUILD``
(its run number), as the C# twin's ``Version.cs`` is stamped. A checkout
carries the values below: no date, build 0, meaning "not a release build".
"""

VERSION = "0.1.0"
BUILD_DATE = ""
BUILD = 0
