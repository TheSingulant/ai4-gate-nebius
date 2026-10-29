"""Run the hackathon demo server: ``python -m gate_demo.server``."""

from __future__ import annotations

import argparse
import os

from gate_demo.api import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the AI4 Gate hackathon demo.")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args(argv)
    import uvicorn

    uvicorn.run(create_app(), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
