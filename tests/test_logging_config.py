"""Tests for JSON structured logging configuration."""

from __future__ import annotations

import json
import logging
import sys

from src.logging_config import JSONFormatter, setup_logging


class TestJSONFormatter:
    """Test JSONFormatter output."""

    def test_basic_log_record(self) -> None:
        """JSONFormatter must emit valid JSON with required fields."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Hello %s",
            args=("world",),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["logger"] == "test.logger"
        assert data["message"] == "Hello world"
        assert "timestamp" in data

    def test_includes_exception_info(self) -> None:
        """JSONFormatter must include exception info when present."""
        formatter = JSONFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="oops",
            args=(),
            exc_info=exc_info,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert "exception" in data
        assert "ValueError" in data["exception"]

    def test_extra_trading_fields_included(self) -> None:
        """Extra trading fields attached to the record must appear in JSON."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="trade",
            args=(),
            exc_info=None,
        )
        record.stock_code = "005930"  # type: ignore[attr-defined]
        record.action = "BUY"  # type: ignore[attr-defined]
        record.confidence = 85  # type: ignore[attr-defined]
        record.pnl_pct = -1.5  # type: ignore[attr-defined]
        record.order_amount = 1_000_000  # type: ignore[attr-defined]
        output = formatter.format(record)
        data = json.loads(output)
        assert data["stock_code"] == "005930"
        assert data["action"] == "BUY"
        assert data["confidence"] == 85
        assert data["pnl_pct"] == -1.5
        assert data["order_amount"] == 1_000_000

    def test_none_extra_fields_excluded(self) -> None:
        """Extra fields that are None must not appear in JSON output."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="no extras",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert "stock_code" not in data
        assert "action" not in data
        assert "confidence" not in data


class TestSetupLogging:
    """Test setup_logging function."""

    def test_configures_root_logger(self) -> None:
        """setup_logging must attach a JSON handler to the root logger."""
        setup_logging(level=logging.DEBUG)
        root = logging.getLogger()
        json_handlers = [h for h in root.handlers if isinstance(h.formatter, JSONFormatter)]
        assert len(json_handlers) == 1
        assert root.level == logging.DEBUG

    def test_avoids_duplicate_handlers(self) -> None:
        """Calling setup_logging twice must not add duplicate handlers."""
        setup_logging()
        setup_logging()
        root = logging.getLogger()
        assert len(root.handlers) == 1
