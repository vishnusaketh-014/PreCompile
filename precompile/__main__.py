"""Package entry point allowing execution via `python3 -m precompile`."""

import sys
from precompile.cli import main

if __name__ == "__main__":
    sys.exit(main())
