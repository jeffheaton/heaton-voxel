import sys

from .cli import _main

raise SystemExit(_main(sys.argv[1:]))
