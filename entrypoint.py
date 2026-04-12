#!/usr/bin/env python
"""Container entrypoint: ensure current-year out directory exists, then run process_rinex.
"""
from __future__ import annotations

import datetime
import os
import sys


def ensure_year_dir(base: str = "/app/out") -> None:
    year = str(datetime.datetime.now(datetime.UTC).year)
    path = os.path.join(base, year)
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        # Best-effort only; don't prevent container from running
        pass


def main() -> None:
    ensure_year_dir()
    # replace current process with the original module invocation
    args = [sys.executable, "-m", "process_rinex"] + sys.argv[1:]
    os.execv(sys.executable, args)


if __name__ == "__main__":
    main()
