"""Allow ``python -m mycord_telegram_repl`` as an alias for the CLI."""

import sys

from mycord_telegram_repl.cli import main

if __name__ == "__main__":
    sys.exit(main())
