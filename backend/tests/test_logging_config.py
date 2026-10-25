import logging
from datetime import date


def test_configure_logging_writes_daily_service_and_error_files(tmp_path):
    from app.core.logging_config import configure_logging

    configure_logging(tmp_path, "worker", current_date=date(2026, 8, 9))
    logger = logging.getLogger("app.test")

    logger.info("worker started")
    logger.error("parse failed")

    service_log = tmp_path / "worker-2026-08-09.log"
    error_log = tmp_path / "worker-error-2026-08-09.log"

    assert service_log.exists()
    assert error_log.exists()
    assert "worker started" in service_log.read_text(encoding="utf-8")
    assert "parse failed" in service_log.read_text(encoding="utf-8")
    assert "worker started" not in error_log.read_text(encoding="utf-8")
    assert "parse failed" in error_log.read_text(encoding="utf-8")


def test_configure_logging_replaces_handlers_without_duplication(tmp_path):
    from app.core.logging_config import configure_logging

    configure_logging(tmp_path, "backend", current_date=date(2026, 8, 9))
    configure_logging(tmp_path, "backend", current_date=date(2026, 8, 9))

    managed_handlers = [
        handler
        for handler in logging.getLogger().handlers
        if getattr(handler, "talent_logging_handler", False)
    ]

    assert len(managed_handlers) == 3
