"""Freeze the current decisions as the regression baseline.

    docker compose exec api python freeze.py          # show what drifted
    docker compose exec api python freeze.py --write  # accept the new baseline

Run the check after every change. Accept a new baseline only when you meant
the change - that is the whole point of the file.
"""

import argparse
import sys

from app import regression


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="accept the current decisions")
    args = parser.parse_args()

    now = regression.current()

    if args.write:
        count = regression.save(now)
        print(f"Froze {count} decisions into {regression.FROZEN.name}")
        return 0

    frozen = regression.load()
    if not frozen:
        print("No baseline yet. Run with --write to create one.")
        return 1

    drifts = regression.compare(frozen, now)
    if not drifts:
        print(f"{len(frozen)} frozen decisions, none changed.")
        return 0

    print(f"{len(drifts)} changes against the frozen baseline:\n")
    for drift in drifts:
        print(f"  {drift}")
    print("\nIf these were intended, re-freeze with --write.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
