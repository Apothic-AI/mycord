"""Allow ``python -m mycord_repl`` as an alias for the ``mycord-repl`` CLI."""

import sys

from mycord_repl.cli import main

if __name__ == "__main__":
    sys.exit(main())
