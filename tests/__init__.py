"""Test suite for heaton-voxel.

Importing this package puts the repository root on ``sys.path`` so the tests
resolve ``heaton_voxel`` (the package under test) and ``fixtures`` (the
vendored game data next to it) no matter which directory the runner is invoked
from. ``test_examples`` additionally adds ``examples/`` for the example
modules it exercises.
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
