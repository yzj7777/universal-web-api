"""入口：python -m agent <run|chat|doctor|tools> ..."""

from __future__ import annotations

import sys

from agent.cli import main

if __name__ == "__main__":
    sys.exit(main())
