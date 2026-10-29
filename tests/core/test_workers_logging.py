from __future__ import annotations

import logging
from pathlib import Path

from materials_data_studio.core import logging_config
from materials_data_studio.core.workers import Worker


def test_rotating_log_is_configured_in_selected_user_directory(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(logging_config, "user_data_directory", lambda: tmp_path)
    logger = logging.getLogger("materials_data_studio")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    path = logging_config.configure_logging()
    logger.warning("diagnostic test")
    for handler in logger.handlers:
        handler.flush()
    assert path == tmp_path / "logs" / "materials-data-studio.log"
    assert "diagnostic test" in path.read_text()


def test_worker_emits_result_and_finished() -> None:
    results: list[object] = []
    finished: list[bool] = []
    worker = Worker(lambda left, right: left + right, 2, 3)
    worker.signals.result.connect(results.append)
    worker.signals.finished.connect(lambda: finished.append(True))
    worker.run()
    assert results == [5]
    assert finished == [True]


def test_worker_emits_actionable_error_and_traceback() -> None:
    errors: list[tuple[str, str]] = []

    def fail() -> None:
        raise ValueError("expected failure")

    worker = Worker(fail)
    worker.signals.error.connect(lambda message, trace: errors.append((message, trace)))
    worker.run()
    assert errors[0][0] == "expected failure"
    assert "ValueError" in errors[0][1]

