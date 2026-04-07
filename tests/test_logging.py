from unittest.mock import patch


def test_setup_logging_configures_stdout_handler():
    from loguru import logger
    from src.main.python.sheng_wen.logging import setup_logging

    with patch("sys.stdout"):
        setup_logging(level="DEBUG")

    assert len(logger._core.handlers) >= 1


def test_setup_logging_removes_default_handler():
    from loguru import logger
    from src.main.python.sheng_wen.logging import setup_logging

    with patch("sys.stdout"):
        setup_logging(level="INFO")
    assert len(logger._core.handlers) == 1
