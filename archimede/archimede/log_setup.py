"""Configurazione del logging strutturato per Archimede.

Per il formato JSON si usa una **sink function** (non `format=`) per stdout,
perché Loguru 0.7.3 tratta le callable in `format=` come generatori di
template stringa con `{placeholder}`, non come formatter finali.

Per i file JSON si usa invece una format-string con i placeholder nativi
di Loguru: supporta rotation/retention, ma non i campi `extra`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from archimede.config import LoggingConfig


def json_sink(message) -> None:
    """Sink Loguru per stdout in formato JSON.

    Scrive il record come JSON su stdout, uno per riga.
    I campi extra (passati con `extra={...}`) vengono inclusi per audit trail.
    """
    record = message.record
    entry: dict[str, Any] = {
        "timestamp": record["time"].strftime("%Y-%m-%dT%H:%M:%S.%f%z"),
        "level": record["level"].name,
        "module": record["name"],
        "function": record["function"],
        "line": record["line"],
        "message": record["message"],
    }

    if record.get("exception"):
        exc = record["exception"]
        entry["exception"] = {
            "type": type(exc.value).__name__ if exc.value is not None else None,
            "value": str(exc.value) if exc.value is not None else None,
        }

    extras = record.get("extra", {})
    if extras:
        entry["extra"] = extras

    sys.stdout.write(json.dumps(entry, default=str) + "\n")
    sys.stdout.flush()


# Template JSON per Loguru (placeholder nativi, NON supporta extra)
_JSON_FILE_FORMAT = (
    '{{'
    '"timestamp": "{time:YYYY-MM-DDTHH:mm:ss.SSSZ}", '
    '"level": "{level}", '
    '"module": "{name}", '
    '"function": "{function}", '
    '"line": "{line}", '
    '"message": "{message}"'
    '}}'
)

# Template testo con colori per terminale
_TEXT_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)

# Template testo semplice per file (senza colori)
_TEXT_FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss} | "
    "{level: <8} | "
    "{name}:{function}:{line} | "
    "{message}"
)


def setup_logging(cfg: LoggingConfig) -> None:
    """Configura Loguru secondo le specifiche di configurazione."""
    logger.remove()

    log_format = cfg.format
    log_level = cfg.level.upper()

    # --- Handler stdout ---
    if log_format == "json":
        logger.add(json_sink, level=log_level)
    else:
        logger.add(sys.stdout, format=_TEXT_FORMAT, level=log_level, colorize=True)

    # --- Handler su file ---
    log_path = Path(cfg.file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if log_format == "json":
        logger.add(
            str(log_path),
            format=_JSON_FILE_FORMAT,
            level=log_level,
            rotation=cfg.rotation,
            retention=cfg.retention,
        )
    else:
        logger.add(
            str(log_path),
            format=_TEXT_FILE_FORMAT,
            level=log_level,
            rotation=cfg.rotation,
            retention=cfg.retention,
        )

    logger.info("Logging inizializzato", extra={"config": cfg.model_dump()})
