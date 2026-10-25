"""Entry point for ``python -m clipnotate``."""

import sys


def _run() -> int:
    try:
        from .app import main
    except ImportError as exc:  # pragma: no cover - depends on the environment
        print(
            f"clipnotate: missing Python dependency: {exc.name or exc}\n"
            "Install the requirements first:\n"
            "    python3 -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1
    return main()


if __name__ == "__main__":
    sys.exit(_run())
