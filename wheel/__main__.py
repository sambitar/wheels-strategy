"""Allow `python -m wheel` as an alternative to the `wheel` script."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
