"""Reset the demo's runtime state: cases, executed resolutions and order changes (D50, D51).

The agent's mock execution and the case records are written to `logs/` (never to `data/`). This
script removes those runtime files so the demo starts from clean data. It lists what it will
delete and asks for a yes first, and it never touches the audit or escalation logs.
Stop the server before running it (the files are in use while it runs).

Run: uv run python scripts/reset_state.py        (asks first)
     uv run python scripts/reset_state.py --yes  (no question, for scripts)
"""

import sys

from app import config

FILES = [config.CASES_FILE, config.ORDER_STATE_FILE, config.RESOLUTION_LOG]


def main(argv: list[str]) -> int:
    """List the runtime files, ask for confirmation, and delete them.

    Args:
        argv: Command-line arguments; `--yes` skips the question.

    Returns:
        0 when done or nothing to do, 1 when the user declined.
    """
    present = [f for f in FILES if f.exists()]
    if not present:
        print("Nothing to reset: no runtime state files found.")
        return 0
    print("These runtime files will be deleted:")
    for f in present:
        print(f"  {f}")
    if "--yes" not in argv and input("Delete them? Type yes to confirm: ").strip().lower() != "yes":
        print("Cancelled. Nothing was deleted.")
        return 1
    for f in present:
        f.unlink()
    print(f"Deleted {len(present)} file(s). The next message starts from clean data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
