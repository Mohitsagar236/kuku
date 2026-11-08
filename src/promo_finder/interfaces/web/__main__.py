"""Run the confidential editor viewer on loopback using Uvicorn."""

import argparse
import logging

import uvicorn

from promo_finder.infrastructure.logging import setup_logging
from promo_finder.interfaces.web.app import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="promo-viewer")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--outputs", default="outputs")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    setup_logging()
    try:
        uvicorn.run(
            create_app(args.outputs),
            host="127.0.0.1",
            port=args.port,
            access_log=False,
            log_config=None,
        )
    except (OSError, SystemExit):
        logging.getLogger(__name__).error("viewer failed to start; check port %s", args.port)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
