from __future__ import annotations

from pdf2zh_engine.server import main as server_main


def main(argv: list[str] | None = None) -> int:
    return server_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
