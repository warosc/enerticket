"""Punto de entrada: ``python -m app.seeds``."""

from __future__ import annotations

import logging
import sys

from app.core.config import settings
from app.seeds import run_all


def main() -> int:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        executed = run_all()
    except Exception:
        logging.getLogger("app.seeds").error("Seeds abortados")
        return 1
    logging.getLogger("app.seeds").info("Seeds completados (%d módulos)", len(executed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
