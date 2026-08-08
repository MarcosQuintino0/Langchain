"""Permite `python -m orquestrador ...`."""

import sys

from orquestrador.cli.principal import main

if __name__ == "__main__":
    sys.exit(main())
