"""Grid-search the scoring weights on the tuning set.

    docker compose exec api python tune_weights.py

Never runs on the held-out set. Plan section 6.9.
"""

import sys

from app.dataset import Split
from app.tune import print_search, search


def main() -> int:
    print(f"Searching on the {Split.TUNING.value} set (45 records)")
    trials = search(Split.TUNING)
    print_search(trials)
    return 0


if __name__ == "__main__":
    sys.exit(main())
