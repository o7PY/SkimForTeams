import logging
import sys

from aiohttp import web

from .app import create_app
from .config import ConfigError, Settings


def main() -> None:
    try:
        settings = Settings.from_env()
    except ConfigError as e:
        sys.exit(str(e))
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    web.run_app(create_app(settings), host="0.0.0.0", port=settings.port, print=None)


if __name__ == "__main__":
    main()
