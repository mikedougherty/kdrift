"""Structured logging configuration using structlog."""

import logging
import sys
from pathlib import Path

import structlog

LOG_FILE = Path.home() / ".cache" / "kdrift" / "kdrift.log"


def configure_logging(
    log_level: str = "INFO",
    log_format: str = "json",
    stream: str = "stdout",
    log_file: bool = False,
) -> None:
    """Configure structlog with JSON output in production, pretty console in development.

    Args:
        log_level: Python log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_format: "json" for production, "console" for local development.
        stream: "stdout" or "stderr". Use "stderr" for LSP/MCP modes where stdout is the protocol channel.
        log_file: Also write JSON logs to ~/.cache/kdrift/kdrift.log.
    """
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if log_format == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    output = sys.stderr if stream == "stderr" else sys.stdout
    handler = logging.StreamHandler(output)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
    )

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    if log_file:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(LOG_FILE)
        file_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processors=[
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.JSONRenderer(),
                ],
            )
        )
        root_logger.addHandler(file_handler)

        pygls_logger = logging.getLogger("pygls")
        pygls_logger.addHandler(file_handler)

    root_logger.setLevel(log_level.upper())
