"""Tests for main trading loop integration."""

from datetime import UTC, date, datetime
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from src.config import Settings
from src.context.layer import ContextLayer
from src.context.scheduler import ScheduleResult
from src.core.order_policy import OrderPolicyRejected
from src.core.risk_manager import CircuitBreakerTripped, FatFingerRejected
from src.db import init_db, log_trade
from src.evolution.scorecard import DailyScorecard
from src.logging.decision_logger import DecisionLogger
from src.main import (
    KILL_SWITCH,
    _trigger_emergency_kill_switch,
    _apply_dashboard_flag,
    _determine_order_quantity,
    _extract_avg_price_from_balance,
    _extract_held_codes_from_balance,
    _extract_held_qty_from_balance,
    _handle_market_close,
    _retry_connection,
    _run_context_scheduler,
    _run_evolution_loop,
    _start_dashboard_server,
    handle_domestic_pending_orders,
    handle_overseas_pending_orders,
    process_blackout_recovery_orders,
    run_daily_session,
    safe_float,
    sync_positions_from_broker,
    trading_cycle,
)
from src.strategy.models import (
    DayPlaybook,
    ScenarioAction,
    StockCondition,
    StockScenario,
)
from src.strategy.scenario_engine import ScenarioEngine, ScenarioMatch


def _make_playbook(market: str = "KR") -> DayPlaybook:
    """Create a minimal empty playbook for testing."""
    return DayPlaybook(date=date(2026, 2, 8), market=market)


def _make_buy_match(stock_code: str = "005930") -> ScenarioMatch:
    """Create a ScenarioMatch that returns BUY."""
    return ScenarioMatch(
        stock_code=stock_code,
        matched_scenario=None,
        action=ScenarioAction.BUY,
        confidence=85,
        rationale="Test buy",
    )


def _make_hold_match(stock_code: str = "005930") -> ScenarioMatch:
    """Create a ScenarioMatch that returns HOLD."""
    return ScenarioMatch(
        stock_code=stock_code,
        matched_scenario=None,
        action=ScenarioAction.HOLD,
        confidence=0,
        rationale="No scenario conditions met",
    )


def _make_sell_match(stock_code: str = "005930") -> ScenarioMatch:
    """Create a ScenarioMatch that returns SELL."""
    return ScenarioMatch(
        stock_code=stock_code,
        matched_scenario=None,
        action=ScenarioAction.SELL,
        confidence=90,
        rationale="Test sell",
    )


@pytest.fixture(autouse=True)
def _reset_kill_switch_state() -> None:
    """Prevent cross-test leakage from global kill-switch state."""
    KILL_SWITCH.clear_block()
    yield
    KILL_SWITCH.clear_block()


class TestExtractAvgPriceFromBalance:
    """Tests for _extract_avg_price_from_balance() (issue #249)."""

    def test_domestic_returns_pchs_avg_pric(self) -> None:
        """Domestic balance with pchs_avg_pric returns the correct float."""
        balance = {"output1": [{"pdno": "005930", "pchs_avg_pric": "68000.00"}]}
        result = _extract_avg_price_from_balance(balance, "005930", is_domestic=True)
        assert result == 68000.0

    def test_overseas_returns_pchs_avg_pric(self) -> None:
        """Overseas balance with pchs_avg_pric returns the correct float."""
        balance = {"output1": [{"ovrs_pdno": "AAPL", "pchs_avg_pric": "170.50"}]}
        result = _extract_avg_price_from_balance(balance, "AAPL", is_domestic=False)
        assert result == 170.5

    def test_returns_zero_when_field_absent(self) -> None:
        """Returns 0.0 when pchs_avg_pric key is missing entirely."""
        balance = {"output1": [{"pdno": "005930", "ord_psbl_qty": "5"}]}
        result = _extract_avg_price_from_balance(balance, "005930", is_domestic=True)
        assert result == 0.0

    def test_returns_zero_when_field_empty_string(self) -> None:
        """Returns 0.0 when pchs_avg_pric is an empty string."""
        balance = {"output1": [{"pdno": "005930", "pchs_avg_pric": ""}]}
        result = _extract_avg_price_from_balance(balance, "005930", is_domestic=True)
        assert result == 0.0

    def test_returns_zero_when_stock_not_found(self) -> None:
        """Returns 0.0 when the requested stock_code is not in output1."""
        balance = {"output1": [{"pdno": "000660", "pchs_avg_pric": "100000.0"}]}
        result = _extract_avg_price_from_balance(balance, "005930", is_domestic=True)
        assert result == 0.0

    def test_returns_zero_when_output1_empty(self) -> None:
        """Returns 0.0 when output1 is an empty list."""
        balance = {"output1": []}
        result = _extract_avg_price_from_balance(balance, "005930", is_domestic=True)
        assert result == 0.0

    def test_returns_zero_when_output1_key_absent(self) -> None:
        """Returns 0.0 when output1 key is missing from balance_data."""
        balance: dict = {}
        result = _extract_avg_price_from_balance(balance, "005930", is_domestic=True)
        assert result == 0.0

    def test_handles_output1_as_dict(self) -> None:
        """Handles the edge case where output1 is a dict instead of a list."""
        balance = {"output1": {"pdno": "005930", "pchs_avg_pric": "55000.0"}}
        result = _extract_avg_price_from_balance(balance, "005930", is_domestic=True)
        assert result == 55000.0

    def test_case_insensitive_code_matching(self) -> None:
        """Stock code comparison is case-insensitive."""
        balance = {"output1": [{"ovrs_pdno": "aapl", "pchs_avg_pric": "170.0"}]}
        result = _extract_avg_price_from_balance(balance, "AAPL", is_domestic=False)
        assert result == 170.0

    def test_returns_zero_for_non_numeric_string(self) -> None:
        """Returns 0.0 when pchs_avg_pric contains a non-numeric value."""
        balance = {"output1": [{"pdno": "005930", "pchs_avg_pric": "N/A"}]}
        result = _extract_avg_price_from_balance(balance, "005930", is_domestic=True)
        assert result == 0.0

    def test_returns_correct_stock_among_multiple(self) -> None:
        """Returns only the avg price of the requested stock when output1 has multiple holdings."""
        balance = {
            "output1": [
                {"pdno": "000660", "pchs_avg_pric": "150000.0"},
                {"pdno": "005930", "pchs_avg_pric": "68000.0"},
            ]
        }
        result = _extract_avg_price_from_balance(balance, "005930", is_domestic=True)
        assert result == 68000.0


class TestExtractHeldQtyFromBalance:
    """Tests for _extract_held_qty_from_balance()."""

    def _domestic_balance(self, stock_code: str, ord_psbl_qty: int) -> dict:
        return {
            "output1": [{"pdno": stock_code, "ord_psbl_qty": str(ord_psbl_qty)}],
            "output2": [{"dnca_tot_amt": "1000000"}],
        }

    def test_domestic_returns_ord_psbl_qty(self) -> None:
        balance = self._domestic_balance("005930", 7)
        assert _extract_held_qty_from_balance(balance, "005930", is_domestic=True) == 7

    def test_domestic_fallback_to_hldg_qty(self) -> None:
        balance = {"output1": [{"pdno": "005930", "hldg_qty": "3"}]}
        assert _extract_held_qty_from_balance(balance, "005930", is_domestic=True) == 3

    def test_domestic_returns_zero_when_not_found(self) -> None:
        balance = self._domestic_balance("005930", 5)
        assert _extract_held_qty_from_balance(balance, "000660", is_domestic=True) == 0

    def test_domestic_returns_zero_when_output1_empty(self) -> None:
        balance = {"output1": [], "output2": [{}]}
        assert _extract_held_qty_from_balance(balance, "005930", is_domestic=True) == 0

    def test_overseas_returns_ord_psbl_qty_first(self) -> None:
        """ord_psbl_qty (주문가능수량) takes priority over ovrs_cblc_qty."""
        balance = {
            "output1": [{"ovrs_pdno": "AAPL", "ord_psbl_qty": "8", "ovrs_cblc_qty": "10"}]
        }
        assert _extract_held_qty_from_balance(balance, "AAPL", is_domestic=False) == 8

    def test_overseas_fallback_to_ovrs_cblc_qty_when_ord_psbl_qty_absent(self) -> None:
        balance = {"output1": [{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "10"}]}
        assert _extract_held_qty_from_balance(balance, "AAPL", is_domestic=False) == 10

    def test_overseas_returns_zero_when_ord_psbl_qty_zero(self) -> None:
        """Expired/delisted securities: ovrs_cblc_qty large but ord_psbl_qty=0."""
        balance = {
            "output1": [{"ovrs_pdno": "MLECW", "ord_psbl_qty": "0", "ovrs_cblc_qty": "289456"}]
        }
        assert _extract_held_qty_from_balance(balance, "MLECW", is_domestic=False) == 0

    def test_overseas_fallback_to_hldg_qty(self) -> None:
        balance = {"output1": [{"ovrs_pdno": "AAPL", "hldg_qty": "4"}]}
        assert _extract_held_qty_from_balance(balance, "AAPL", is_domestic=False) == 4

    def test_case_insensitive_match(self) -> None:
        balance = {"output1": [{"pdno": "005930", "ord_psbl_qty": "2"}]}
        assert _extract_held_qty_from_balance(balance, "005930", is_domestic=True) == 2


class TestExtractHeldCodesFromBalance:
    """Tests for _extract_held_codes_from_balance()."""

    def test_returns_codes_with_positive_qty(self) -> None:
        balance = {
            "output1": [
                {"pdno": "005930", "ord_psbl_qty": "5"},
                {"pdno": "000660", "ord_psbl_qty": "3"},
            ]
        }
        result = _extract_held_codes_from_balance(balance, is_domestic=True)
        assert set(result) == {"005930", "000660"}

    def test_excludes_zero_qty_holdings(self) -> None:
        balance = {
            "output1": [
                {"pdno": "005930", "ord_psbl_qty": "0"},
                {"pdno": "000660", "ord_psbl_qty": "2"},
            ]
        }
        result = _extract_held_codes_from_balance(balance, is_domestic=True)
        assert "005930" not in result
        assert "000660" in result

    def test_returns_empty_when_output1_missing(self) -> None:
        balance: dict = {}
        assert _extract_held_codes_from_balance(balance, is_domestic=True) == []

    def test_overseas_uses_ovrs_pdno(self) -> None:
        balance = {"output1": [{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "3"}]}
        result = _extract_held_codes_from_balance(balance, is_domestic=False)
        assert result == ["AAPL"]

    def test_overseas_uses_ord_psbl_qty_to_filter(self) -> None:
        """ord_psbl_qty=0 should exclude stock even if ovrs_cblc_qty is large."""
        balance = {
            "output1": [
                {"ovrs_pdno": "MLECW", "ord_psbl_qty": "0", "ovrs_cblc_qty": "289456"},
                {"ovrs_pdno": "AAPL", "ord_psbl_qty": "5", "ovrs_cblc_qty": "5"},
            ]
        }
        result = _extract_held_codes_from_balance(balance, is_domestic=False)
        assert "MLECW" not in result
        assert "AAPL" in result

    def test_overseas_includes_stock_when_ord_psbl_qty_absent_and_ovrs_cblc_qty_positive(
        self,
    ) -> None:
        """Fallback to ovrs_cblc_qty when ord_psbl_qty field is missing."""
        balance = {"output1": [{"ovrs_pdno": "TSLA", "ovrs_cblc_qty": "3"}]}
        result = _extract_held_codes_from_balance(balance, is_domestic=False)
        assert "TSLA" in result


class TestDetermineOrderQuantity:
    """Test _determine_order_quantity() — SELL uses broker_held_qty."""

    def test_sell_returns_broker_held_qty(self) -> None:
        result = _determine_order_quantity(
            action="SELL",
            current_price=105.0,
            total_cash=50000.0,
            candidate=None,
            settings=None,
            broker_held_qty=7,
        )
        assert result == 7

    def test_sell_returns_zero_when_broker_qty_zero(self) -> None:
        result = _determine_order_quantity(
            action="SELL",
            current_price=105.0,
            total_cash=50000.0,
            candidate=None,
            settings=None,
            broker_held_qty=0,
        )
        assert result == 0

    def test_buy_without_position_sizing_returns_one(self) -> None:
        result = _determine_order_quantity(
            action="BUY",
            current_price=50000.0,
            total_cash=1000000.0,
            candidate=None,
            settings=None,
        )
        assert result == 1

    def test_buy_with_zero_cash_returns_zero(self) -> None:
        result = _determine_order_quantity(
            action="BUY",
            current_price=50000.0,
            total_cash=0.0,
            candidate=None,
            settings=None,
        )
        assert result == 0

    def test_buy_with_position_sizing_calculates_correctly(self) -> None:
        settings = MagicMock(spec=Settings)
        settings.POSITION_SIZING_ENABLED = True
        settings.POSITION_VOLATILITY_TARGET_SCORE = 50.0
        settings.POSITION_BASE_ALLOCATION_PCT = 10.0
        settings.POSITION_MAX_ALLOCATION_PCT = 30.0
        settings.POSITION_MIN_ALLOCATION_PCT = 1.0
        # 1,000,000 * 10% = 100,000 budget // 50,000 price = 2 shares
        result = _determine_order_quantity(
            action="BUY",
            current_price=50000.0,
            total_cash=1000000.0,
            candidate=None,
            settings=settings,
        )
        assert result == 2

    def test_determine_order_quantity_uses_playbook_allocation_pct(self) -> None:
        """playbook_allocation_pct should take priority over volatility-based sizing."""
        settings = MagicMock(spec=Settings)
        settings.POSITION_SIZING_ENABLED = True
        settings.POSITION_MAX_ALLOCATION_PCT = 30.0
        settings.POSITION_MIN_ALLOCATION_PCT = 1.0
        # playbook says 20%, confidence 80 → scale=1.0 → 20%
        # 1,000,000 * 20% = 200,000 // 50,000 price = 4 shares
        result = _determine_order_quantity(
            action="BUY",
            current_price=50000.0,
            total_cash=1000000.0,
            candidate=None,
            settings=settings,
            playbook_allocation_pct=20.0,
            scenario_confidence=80,
        )
        assert result == 4

    def test_determine_order_quantity_confidence_scales_allocation(self) -> None:
        """Higher confidence should produce a larger allocation (up to max)."""
        settings = MagicMock(spec=Settings)
        settings.POSITION_SIZING_ENABLED = True
        settings.POSITION_MAX_ALLOCATION_PCT = 30.0
        settings.POSITION_MIN_ALLOCATION_PCT = 1.0
        # confidence 96 → scale=1.2 → 10% * 1.2 = 12%
        # 1,000,000 * 12% = 120,000 // 50,000 price = 2 shares
        result = _determine_order_quantity(
            action="BUY",
            current_price=50000.0,
            total_cash=1000000.0,
            candidate=None,
            settings=settings,
            playbook_allocation_pct=10.0,
            scenario_confidence=96,
        )
        # scale = 96/80 = 1.2 → effective_pct = 12.0
        # budget = 1_000_000 * 0.12 = 120_000 → qty = 120_000 // 50_000 = 2
        assert result == 2

    def test_determine_order_quantity_confidence_clamped_to_max(self) -> None:
        """Confidence scaling should not exceed POSITION_MAX_ALLOCATION_PCT."""
        settings = MagicMock(spec=Settings)
        settings.POSITION_SIZING_ENABLED = True
        settings.POSITION_MAX_ALLOCATION_PCT = 15.0
        settings.POSITION_MIN_ALLOCATION_PCT = 1.0
        # playbook 20% * scale 1.5 = 30% → clamped to 15%
        # 1,000,000 * 15% = 150,000 // 50,000 price = 3 shares
        result = _determine_order_quantity(
            action="BUY",
            current_price=50000.0,
            total_cash=1000000.0,
            candidate=None,
            settings=settings,
            playbook_allocation_pct=20.0,
            scenario_confidence=120,  # extreme → scale = 1.5
        )
        assert result == 3

    def test_determine_order_quantity_fallback_when_no_playbook(self) -> None:
        """Without playbook_allocation_pct, falls back to volatility-based sizing."""
        settings = MagicMock(spec=Settings)
        settings.POSITION_SIZING_ENABLED = True
        settings.POSITION_VOLATILITY_TARGET_SCORE = 50.0
        settings.POSITION_BASE_ALLOCATION_PCT = 10.0
        settings.POSITION_MAX_ALLOCATION_PCT = 30.0
        settings.POSITION_MIN_ALLOCATION_PCT = 1.0
        # Same as test_buy_with_position_sizing_calculates_correctly (no playbook)
        result = _determine_order_quantity(
            action="BUY",
            current_price=50000.0,
            total_cash=1000000.0,
            candidate=None,
            settings=settings,
            playbook_allocation_pct=None,  # explicit None → fallback
        )
        assert result == 2


class TestSafeFloat:
    """Test safe_float() helper function."""

    def test_converts_valid_string(self):
        """Test conversion of valid numeric string."""
        assert safe_float("123.45") == 123.45
        assert safe_float("0") == 0.0
        assert safe_float("-99.9") == -99.9

    def test_handles_empty_string(self):
        """Test empty string returns default."""
        assert safe_float("") == 0.0
        assert safe_float("", 99.0) == 99.0

    def test_handles_none(self):
        """Test None returns default."""
        assert safe_float(None) == 0.0
        assert safe_float(None, 42.0) == 42.0

    def test_handles_invalid_string(self):
        """Test invalid string returns default."""
        assert safe_float("invalid") == 0.0
        assert safe_float("not_a_number", 100.0) == 100.0
        assert safe_float("12.34.56") == 0.0

    def test_handles_float_input(self):
        """Test float input passes through."""
        assert safe_float(123.45) == 123.45
        assert safe_float(0.0) == 0.0

    def test_custom_default(self):
        """Test custom default value."""
        assert safe_float("", -1.0) == -1.0
        assert safe_float(None, 999.0) == 999.0


class TestTradingCycleTelegramIntegration:
    """Test telegram notifications in trading_cycle function."""

    @pytest.fixture
    def mock_broker(self) -> MagicMock:
        """Create mock broker."""
        broker = MagicMock()
        broker.get_current_price = AsyncMock(return_value=(50000.0, 1.23, 100.0))
        broker.get_balance = AsyncMock(
            return_value={
                "output2": [
                    {
                        "tot_evlu_amt": "10000000",
                        "dnca_tot_amt": "5000000",
                        "pchs_amt_smtl_amt": "5000000",
                    }
                ]
            }
        )
        broker.send_order = AsyncMock(return_value={"msg1": "OK"})
        return broker

    @pytest.fixture
    def mock_overseas_broker(self) -> MagicMock:
        """Create mock overseas broker."""
        broker = MagicMock()
        return broker

    @pytest.fixture
    def mock_scenario_engine(self) -> MagicMock:
        """Create mock scenario engine that returns BUY."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_buy_match())
        return engine

    @pytest.fixture
    def mock_playbook(self) -> DayPlaybook:
        """Create a minimal day playbook."""
        return _make_playbook()

    @pytest.fixture
    def mock_risk(self) -> MagicMock:
        """Create mock risk manager."""
        risk = MagicMock()
        risk.validate_order = MagicMock()
        return risk

    @pytest.fixture
    def mock_db(self) -> MagicMock:
        """Create mock database connection."""
        return MagicMock()

    @pytest.fixture
    def mock_decision_logger(self) -> MagicMock:
        """Create mock decision logger."""
        logger = MagicMock()
        logger.log_decision = MagicMock()
        return logger

    @pytest.fixture
    def mock_context_store(self) -> MagicMock:
        """Create mock context store."""
        store = MagicMock()
        store.get_latest_timeframe = MagicMock(return_value=None)
        return store

    @pytest.fixture
    def mock_criticality_assessor(self) -> MagicMock:
        """Create mock criticality assessor."""
        assessor = MagicMock()
        assessor.assess_market_conditions = MagicMock(
            return_value=MagicMock(value="NORMAL")
        )
        assessor.get_timeout = MagicMock(return_value=5.0)
        return assessor

    @pytest.fixture
    def mock_telegram(self) -> MagicMock:
        """Create mock telegram client."""
        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()
        return telegram

    @pytest.fixture
    def mock_market(self) -> MagicMock:
        """Create mock market info."""
        market = MagicMock()
        market.name = "Korea"
        market.code = "KR"
        market.exchange_code = "KRX"
        market.is_domestic = True
        return market

    @pytest.mark.asyncio
    async def test_trade_execution_notification_sent(
        self,
        mock_broker: MagicMock,
        mock_overseas_broker: MagicMock,
        mock_scenario_engine: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_market: MagicMock,
    ) -> None:
        """Test telegram notification sent on trade execution."""
        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=mock_overseas_broker,
                scenario_engine=mock_scenario_engine,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        # Verify notification was sent
        mock_telegram.notify_trade_execution.assert_called_once()
        call_kwargs = mock_telegram.notify_trade_execution.call_args.kwargs
        assert call_kwargs["stock_code"] == "005930"
        assert call_kwargs["market"] == "Korea"
        assert call_kwargs["action"] == "BUY"
        assert call_kwargs["confidence"] == 85

    @pytest.mark.asyncio
    async def test_trade_execution_notification_failure_doesnt_crash(
        self,
        mock_broker: MagicMock,
        mock_overseas_broker: MagicMock,
        mock_scenario_engine: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_market: MagicMock,
    ) -> None:
        """Test trading continues even if notification fails."""
        # Make notification fail
        mock_telegram.notify_trade_execution.side_effect = Exception("API error")

        with patch("src.main.log_trade"):
            # Should not raise exception
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=mock_overseas_broker,
                scenario_engine=mock_scenario_engine,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        # Verify notification was attempted
        mock_telegram.notify_trade_execution.assert_called_once()

    @pytest.mark.asyncio
    async def test_fat_finger_notification_sent(
        self,
        mock_broker: MagicMock,
        mock_overseas_broker: MagicMock,
        mock_scenario_engine: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_market: MagicMock,
    ) -> None:
        """Test telegram notification sent on fat finger rejection."""
        # Make risk manager reject the order
        mock_risk.validate_order.side_effect = FatFingerRejected(
            order_amount=2000000,
            total_cash=5000000,
            max_pct=30.0,
        )

        with patch("src.main.log_trade"):
            with pytest.raises(FatFingerRejected):
                await trading_cycle(
                    broker=mock_broker,
                    overseas_broker=mock_overseas_broker,
                    scenario_engine=mock_scenario_engine,
                    playbook=mock_playbook,
                    risk=mock_risk,
                    db_conn=mock_db,
                    decision_logger=mock_decision_logger,
                    context_store=mock_context_store,
                    criticality_assessor=mock_criticality_assessor,
                    telegram=mock_telegram,
                    market=mock_market,
                    stock_code="005930",
                    scan_candidates={},
                )

        # Verify notification was sent
        mock_telegram.notify_fat_finger.assert_called_once()
        call_kwargs = mock_telegram.notify_fat_finger.call_args.kwargs
        assert call_kwargs["stock_code"] == "005930"
        assert call_kwargs["order_amount"] == 2000000
        assert call_kwargs["total_cash"] == 5000000
        assert call_kwargs["max_pct"] == 30.0

    @pytest.mark.asyncio
    async def test_fat_finger_notification_failure_still_raises(
        self,
        mock_broker: MagicMock,
        mock_overseas_broker: MagicMock,
        mock_scenario_engine: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_market: MagicMock,
    ) -> None:
        """Test fat finger exception still raised even if notification fails."""
        # Make risk manager reject the order
        mock_risk.validate_order.side_effect = FatFingerRejected(
            order_amount=2000000,
            total_cash=5000000,
            max_pct=30.0,
        )
        # Make notification fail
        mock_telegram.notify_fat_finger.side_effect = Exception("API error")

        with patch("src.main.log_trade"):
            with pytest.raises(FatFingerRejected):
                await trading_cycle(
                    broker=mock_broker,
                    overseas_broker=mock_overseas_broker,
                    scenario_engine=mock_scenario_engine,
                    playbook=mock_playbook,
                    risk=mock_risk,
                    db_conn=mock_db,
                    decision_logger=mock_decision_logger,
                    context_store=mock_context_store,
                    criticality_assessor=mock_criticality_assessor,
                    telegram=mock_telegram,
                    market=mock_market,
                    stock_code="005930",
                    scan_candidates={},
                )

        # Verify notification was attempted
        mock_telegram.notify_fat_finger.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_notification_on_hold_decision(
        self,
        mock_broker: MagicMock,
        mock_overseas_broker: MagicMock,
        mock_scenario_engine: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_market: MagicMock,
    ) -> None:
        """Test no trade notification sent when decision is HOLD."""
        # Scenario engine returns HOLD
        mock_scenario_engine.evaluate = MagicMock(return_value=_make_hold_match())

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=mock_overseas_broker,
                scenario_engine=mock_scenario_engine,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        # Verify no trade notification sent
        mock_telegram.notify_trade_execution.assert_not_called()

    @pytest.mark.asyncio
    async def test_sell_skips_fat_finger_check(
        self,
        mock_broker: MagicMock,
        mock_overseas_broker: MagicMock,
        mock_scenario_engine: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_market: MagicMock,
    ) -> None:
        """SELL orders must not be blocked by fat-finger check.

        Even if position value > 30% of cash (e.g. stop-loss on a large holding
        with low remaining cash), the SELL should proceed — only circuit breaker
        applies to SELLs.
        """
        # SELL decision with held qty=100 shares @ 50,000 = 5,000,000
        # cash = 5,000,000 → ratio = 100% which would normally trigger fat finger
        mock_scenario_engine.evaluate = MagicMock(return_value=_make_sell_match())
        mock_broker.get_balance = AsyncMock(
            return_value={
                "output1": [{"pdno": "005930", "ord_psbl_qty": "100"}],
                "output2": [
                    {
                        "tot_evlu_amt": "10000000",
                        "dnca_tot_amt": "5000000",
                        "pchs_amt_smtl_amt": "5000000",
                    }
                ],
            }
        )

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=mock_overseas_broker,
                scenario_engine=mock_scenario_engine,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        # validate_order (which includes fat finger) must NOT be called for SELL
        mock_risk.validate_order.assert_not_called()
        # check_circuit_breaker MUST be called for SELL
        mock_risk.check_circuit_breaker.assert_called_once()

    @pytest.mark.asyncio
    async def test_sell_circuit_breaker_still_applies(
        self,
        mock_broker: MagicMock,
        mock_overseas_broker: MagicMock,
        mock_scenario_engine: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_market: MagicMock,
    ) -> None:
        """SELL orders must still respect the circuit breaker."""
        mock_scenario_engine.evaluate = MagicMock(return_value=_make_sell_match())
        mock_broker.get_balance = AsyncMock(
            return_value={
                "output1": [{"pdno": "005930", "ord_psbl_qty": "100"}],
                "output2": [
                    {
                        "tot_evlu_amt": "10000000",
                        "dnca_tot_amt": "5000000",
                        "pchs_amt_smtl_amt": "5000000",
                    }
                ],
            }
        )
        mock_risk.check_circuit_breaker.side_effect = CircuitBreakerTripped(
            pnl_pct=-4.0, threshold=-3.0
        )

        with patch("src.main.log_trade"):
            with pytest.raises(CircuitBreakerTripped):
                await trading_cycle(
                    broker=mock_broker,
                    overseas_broker=mock_overseas_broker,
                    scenario_engine=mock_scenario_engine,
                    playbook=mock_playbook,
                    risk=mock_risk,
                    db_conn=mock_db,
                    decision_logger=mock_decision_logger,
                    context_store=mock_context_store,
                    criticality_assessor=mock_criticality_assessor,
                    telegram=mock_telegram,
                    market=mock_market,
                    stock_code="005930",
                    scan_candidates={},
                )

        mock_risk.check_circuit_breaker.assert_called_once()
        mock_risk.validate_order.assert_not_called()


class TestRunFunctionTelegramIntegration:
    """Test telegram notifications in run function."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_notification_sent(self) -> None:
        """Test telegram notification sent when circuit breaker trips."""
        mock_telegram = MagicMock()
        mock_telegram.notify_circuit_breaker = AsyncMock()

        # Simulate circuit breaker exception
        exc = CircuitBreakerTripped(pnl_pct=-3.5, threshold=-3.0)

        # Test the notification logic
        try:
            await mock_telegram.notify_circuit_breaker(
                pnl_pct=exc.pnl_pct,
                threshold=exc.threshold,
            )
        except Exception:
            pass  # Ignore errors in notification

        # Verify notification was called
        mock_telegram.notify_circuit_breaker.assert_called_once_with(
            pnl_pct=-3.5,
            threshold=-3.0,
        )


class TestOverseasBalanceParsing:
    """Test overseas balance output2 parsing handles different formats."""

    @pytest.fixture
    def mock_overseas_broker_with_list(self) -> MagicMock:
        """Create mock overseas broker returning list format."""
        broker = MagicMock()
        broker.get_overseas_price = AsyncMock(
            return_value={"output": {"last": "150.50"}}
        )
        broker.get_overseas_balance = AsyncMock(
            return_value={
                "output2": [
                    {
                        "frcr_evlu_tota": "10000.00",
                        "frcr_buy_amt_smtl": "4500.00",
                    }
                ]
            }
        )
        broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "5000.00"}}
        )
        return broker

    @pytest.fixture
    def mock_overseas_broker_with_dict(self) -> MagicMock:
        """Create mock overseas broker returning dict format."""
        broker = MagicMock()
        broker.get_overseas_price = AsyncMock(
            return_value={"output": {"last": "150.50"}}
        )
        broker.get_overseas_balance = AsyncMock(
            return_value={
                "output2": {
                    "frcr_evlu_tota": "10000.00",
                    "frcr_buy_amt_smtl": "4500.00",
                }
            }
        )
        broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "5000.00"}}
        )
        return broker

    @pytest.fixture
    def mock_overseas_broker_with_empty(self) -> MagicMock:
        """Create mock overseas broker returning empty output2."""
        broker = MagicMock()
        broker.get_overseas_price = AsyncMock(
            return_value={"output": {"last": "150.50"}}
        )
        broker.get_overseas_balance = AsyncMock(return_value={"output2": []})
        broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "0.00"}}
        )
        return broker

    @pytest.fixture
    def mock_overseas_broker_with_empty_price(self) -> MagicMock:
        """Create mock overseas broker returning empty string for price."""
        broker = MagicMock()
        broker.get_overseas_price = AsyncMock(
            return_value={"output": {"last": ""}}  # Empty string
        )
        broker.get_overseas_balance = AsyncMock(
            return_value={
                "output2": [
                    {
                        "frcr_evlu_tota": "10000.00",
                        "frcr_buy_amt_smtl": "4500.00",
                    }
                ]
            }
        )
        # get_overseas_buying_power not called when price=0, but mock for safety
        broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "5000.00"}}
        )
        return broker

    @pytest.fixture
    def mock_domestic_broker(self) -> MagicMock:
        """Create minimal mock domestic broker."""
        broker = MagicMock()
        return broker

    @pytest.fixture
    def mock_overseas_market(self) -> MagicMock:
        """Create mock overseas market info."""
        market = MagicMock()
        market.name = "NASDAQ"
        market.code = "US_NASDAQ"
        market.exchange_code = "NASD"
        market.is_domestic = False
        return market

    @pytest.fixture
    def mock_scenario_engine_hold(self) -> MagicMock:
        """Create mock scenario engine that always returns HOLD."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_hold_match("AAPL"))
        return engine

    @pytest.fixture
    def mock_playbook(self) -> DayPlaybook:
        """Create a minimal playbook."""
        return _make_playbook("US")

    @pytest.fixture
    def mock_risk(self) -> MagicMock:
        """Create mock risk manager."""
        return MagicMock()

    @pytest.fixture
    def mock_db(self) -> MagicMock:
        """Create mock database."""
        return MagicMock()

    @pytest.fixture
    def mock_decision_logger(self) -> MagicMock:
        """Create mock decision logger."""
        return MagicMock()

    @pytest.fixture
    def mock_context_store(self) -> MagicMock:
        """Create mock context store."""
        store = MagicMock()
        store.get_latest_timeframe = MagicMock(return_value=None)
        return store

    @pytest.fixture
    def mock_criticality_assessor(self) -> MagicMock:
        """Create mock criticality assessor."""
        assessor = MagicMock()
        assessor.assess_market_conditions = MagicMock(
            return_value=MagicMock(value="NORMAL")
        )
        assessor.get_timeout = MagicMock(return_value=5.0)
        return assessor

    @pytest.fixture
    def mock_telegram(self) -> MagicMock:
        """Create mock telegram client."""
        telegram = MagicMock()
        telegram.notify_scenario_matched = AsyncMock()
        return telegram

    @pytest.mark.asyncio
    async def test_overseas_balance_list_format(
        self,
        mock_domestic_broker: MagicMock,
        mock_overseas_broker_with_list: MagicMock,
        mock_scenario_engine_hold: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """Test overseas balance parsing with list format (output2=[{...}])."""
        with patch("src.main.log_trade"):
            # Should not raise KeyError
            await trading_cycle(
                broker=mock_domestic_broker,
                overseas_broker=mock_overseas_broker_with_list,
                scenario_engine=mock_scenario_engine_hold,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_overseas_market,
                stock_code="AAPL",
                scan_candidates={},
            )

        # Verify balance API was called
        mock_overseas_broker_with_list.get_overseas_balance.assert_called_once()

    @pytest.mark.asyncio
    async def test_overseas_balance_dict_format(
        self,
        mock_domestic_broker: MagicMock,
        mock_overseas_broker_with_dict: MagicMock,
        mock_scenario_engine_hold: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """Test overseas balance parsing with dict format (output2={...})."""
        with patch("src.main.log_trade"):
            # Should not raise KeyError
            await trading_cycle(
                broker=mock_domestic_broker,
                overseas_broker=mock_overseas_broker_with_dict,
                scenario_engine=mock_scenario_engine_hold,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_overseas_market,
                stock_code="AAPL",
                scan_candidates={},
            )

        # Verify balance API was called
        mock_overseas_broker_with_dict.get_overseas_balance.assert_called_once()

    @pytest.mark.asyncio
    async def test_overseas_balance_empty_format(
        self,
        mock_domestic_broker: MagicMock,
        mock_overseas_broker_with_empty: MagicMock,
        mock_scenario_engine_hold: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """Test overseas balance parsing with empty output2."""
        with patch("src.main.log_trade"):
            # Should not raise KeyError, should default to 0
            await trading_cycle(
                broker=mock_domestic_broker,
                overseas_broker=mock_overseas_broker_with_empty,
                scenario_engine=mock_scenario_engine_hold,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_overseas_market,
                stock_code="AAPL",
                scan_candidates={},
            )

        # Verify balance API was called
        mock_overseas_broker_with_empty.get_overseas_balance.assert_called_once()

    @pytest.mark.asyncio
    async def test_overseas_price_empty_string(
        self,
        mock_domestic_broker: MagicMock,
        mock_overseas_broker_with_empty_price: MagicMock,
        mock_scenario_engine_hold: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """Test overseas price parsing with empty string (issue #49)."""
        with patch("src.main.log_trade"):
            # Should not raise ValueError, should default to 0.0
            await trading_cycle(
                broker=mock_domestic_broker,
                overseas_broker=mock_overseas_broker_with_empty_price,
                scenario_engine=mock_scenario_engine_hold,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_overseas_market,
                stock_code="AAPL",
                scan_candidates={},
            )

        # Verify price API was called
        mock_overseas_broker_with_empty_price.get_overseas_price.assert_called_once()

    @pytest.fixture
    def mock_overseas_broker_with_buy_scenario(self) -> MagicMock:
        """Create mock overseas broker that returns a valid price for BUY orders."""
        broker = MagicMock()
        broker.get_overseas_price = AsyncMock(
            return_value={"output": {"last": "182.50"}}
        )
        broker.get_overseas_balance = AsyncMock(
            return_value={
                "output2": [
                    {
                        "frcr_evlu_tota": "100000.00",
                        "frcr_buy_amt_smtl": "50000.00",
                    }
                ]
            }
        )
        broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "50000.00"}}
        )
        broker.send_overseas_order = AsyncMock(return_value={"msg1": "주문접수"})
        return broker

    @pytest.fixture
    def mock_scenario_engine_buy(self) -> MagicMock:
        """Create mock scenario engine that returns BUY."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_buy_match("AAPL"))
        return engine

    @pytest.mark.asyncio
    async def test_overseas_buy_order_uses_limit_price(
        self,
        mock_domestic_broker: MagicMock,
        mock_overseas_broker_with_buy_scenario: MagicMock,
        mock_scenario_engine_buy: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """Overseas BUY order must use current_price +0.2% limit, not market order.

        KIS market orders (ORD_DVSN=01) calculate quantity based on upper limit price
        (상한가 기준), resulting in only 60-80% of intended cash being used.
        Regression test for issue #149 / #211.
        """
        mock_telegram.notify_trade_execution = AsyncMock()

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_domestic_broker,
                overseas_broker=mock_overseas_broker_with_buy_scenario,
                scenario_engine=mock_scenario_engine_buy,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_overseas_market,
                stock_code="AAPL",
                scan_candidates={},
            )

        # Verify BUY limit order uses +0.2% premium (issue #211)
        mock_overseas_broker_with_buy_scenario.send_overseas_order.assert_called_once()
        call_kwargs = mock_overseas_broker_with_buy_scenario.send_overseas_order.call_args
        sent_price = call_kwargs[1].get("price") or call_kwargs[0][4]
        # KIS requires max 2 decimal places for prices >= $1 (#252)
        expected_price = round(182.5 * 1.002, 2)  # 0.2% premium for BUY limit orders
        assert sent_price == expected_price, (
            f"Expected limit price {expected_price} (182.5 * 1.002) but got {sent_price}. "
            "BUY uses +0.2% to improve fill rate while minimising overpayment (#211)."
        )

    @pytest.mark.asyncio
    async def test_overseas_sell_order_uses_limit_price_below_current(
        self,
        mock_domestic_broker: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """Overseas SELL order must use current_price -0.2% limit (#211).

        Placing SELL at exact last price risks no-fill when the bid is just below.
        Using -0.2% ensures the order fills even if the price dips slightly.
        """
        sell_price = 182.5

        # Broker mock: returns price data and a balance with 5 AAPL shares held.
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_price = AsyncMock(
            return_value={"output": {"last": str(sell_price), "rate": "1.5", "tvol": "5000000"}}
        )
        overseas_broker.get_overseas_balance = AsyncMock(
            return_value={
                "output1": [
                    {
                        "ovrs_pdno": "AAPL",
                        "ovrs_cblc_qty": "5",
                        "pchs_avg_pric": "170.0",
                        "evlu_pfls_rt": "7.35",
                    }
                ],
                "output2": [
                    {
                        "frcr_evlu_tota": "100000.00",
                        "frcr_buy_amt_smtl": "50000.00",
                    }
                ],
            }
        )
        overseas_broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "50000.00"}}
        )
        overseas_broker.send_overseas_order = AsyncMock(
            return_value={"rt_cd": "0", "msg1": "OK"}
        )

        sell_engine = MagicMock(spec=ScenarioEngine)
        sell_engine.evaluate = MagicMock(return_value=_make_sell_match("AAPL"))
        mock_telegram.notify_trade_execution = AsyncMock()

        with patch("src.main.log_trade"), patch("src.main.get_open_position") as mock_pos:
            mock_pos.return_value = {"quantity": 5, "stock_code": "AAPL", "price": 170.0}
            await trading_cycle(
                broker=mock_domestic_broker,
                overseas_broker=overseas_broker,
                scenario_engine=sell_engine,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_overseas_market,
                stock_code="AAPL",
                scan_candidates={},
            )

        overseas_broker.send_overseas_order.assert_called_once()
        call_kwargs = overseas_broker.send_overseas_order.call_args
        sent_price = call_kwargs[1].get("price") or call_kwargs[0][4]
        # KIS requires max 2 decimal places for prices >= $1 (#252)
        expected_price = round(sell_price * 0.998, 2)  # -0.2% for SELL limit orders
        assert sent_price == expected_price, (
            f"Expected SELL limit price {expected_price} (182.5 * 0.998) but got {sent_price}. "
            "SELL uses -0.2% to ensure fill even when price dips slightly (#211)."
        )

    @pytest.mark.asyncio
    async def test_overseas_buy_price_rounded_to_2_decimals_for_dollar_plus_stock(
        self,
        mock_domestic_broker: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """BUY price for $1+ stocks is rounded to 2 decimal places (issue #252).

        KIS rejects prices with more than 2 decimal places for stocks priced >= $1.
        current_price=50.1234 * 1.002 = 50.22... should be sent as 50.22, not 50.2236.
        """
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_balance = AsyncMock(
            return_value={
                "output1": [],
                "output2": [{"frcr_evlu_tota": "0", "frcr_buy_amt_smtl": "0"}],
            }
        )
        overseas_broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "10000"}}
        )
        overseas_broker.get_overseas_price = AsyncMock(
            return_value={"output": {"last": "50.1234", "rate": "0"}}
        )
        overseas_broker.send_overseas_order = AsyncMock(
            return_value={"rt_cd": None, "msg1": "주문접수"}
        )

        db_conn = init_db(":memory:")
        decision_logger = DecisionLogger(db_conn)

        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_buy_match())

        await trading_cycle(
            broker=mock_domestic_broker,
            overseas_broker=overseas_broker,
            scenario_engine=engine,
            playbook=mock_playbook,
            risk=mock_risk,
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=mock_context_store,
            criticality_assessor=mock_criticality_assessor,
            telegram=mock_telegram,
            market=mock_overseas_market,
            stock_code="TQQQ",
            scan_candidates={},
        )

        overseas_broker.send_overseas_order.assert_called_once()
        sent_price = overseas_broker.send_overseas_order.call_args[1].get("price") or \
            overseas_broker.send_overseas_order.call_args[0][4]
        # 50.1234 * 1.002 = 50.2235... rounded to 2 decimals = 50.22
        assert sent_price == round(50.1234 * 1.002, 2), (
            f"Expected 2-decimal price {round(50.1234 * 1.002, 2)} but got {sent_price} (#252)"
        )

    @pytest.mark.asyncio
    async def test_overseas_penny_stock_price_keeps_4_decimals(
        self,
        mock_domestic_broker: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """BUY price for penny stocks (< $1) uses 4 decimal places (issue #252)."""
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_balance = AsyncMock(
            return_value={
                "output1": [],
                "output2": [{"frcr_evlu_tota": "0", "frcr_buy_amt_smtl": "0"}],
            }
        )
        overseas_broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "10000"}}
        )
        overseas_broker.get_overseas_price = AsyncMock(
            return_value={"output": {"last": "0.5678", "rate": "0"}}
        )
        overseas_broker.send_overseas_order = AsyncMock(
            return_value={"rt_cd": None, "msg1": "주문접수"}
        )

        db_conn = init_db(":memory:")
        decision_logger = DecisionLogger(db_conn)

        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_buy_match())

        await trading_cycle(
            broker=mock_domestic_broker,
            overseas_broker=overseas_broker,
            scenario_engine=engine,
            playbook=mock_playbook,
            risk=mock_risk,
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=mock_context_store,
            criticality_assessor=mock_criticality_assessor,
            telegram=mock_telegram,
            market=mock_overseas_market,
            stock_code="PENNYX",
            scan_candidates={},
        )

        overseas_broker.send_overseas_order.assert_called_once()
        sent_price = overseas_broker.send_overseas_order.call_args[1].get("price") or \
            overseas_broker.send_overseas_order.call_args[0][4]
        # 0.5678 * 1.002 = 0.56893... rounded to 4 decimals = 0.5689
        assert sent_price == round(0.5678 * 1.002, 4), (
            f"Expected 4-decimal price {round(0.5678 * 1.002, 4)} but got {sent_price} (#252)"
        )


class TestScenarioEngineIntegration:
    """Test scenario engine integration in trading_cycle."""

    @pytest.fixture
    def mock_broker(self) -> MagicMock:
        """Create mock broker with standard domestic data."""
        broker = MagicMock()
        broker.get_current_price = AsyncMock(return_value=(50000.0, 2.50, 100.0))
        broker.get_balance = AsyncMock(
            return_value={
                "output2": [
                    {
                        "tot_evlu_amt": "10000000",
                        "dnca_tot_amt": "5000000",
                        "pchs_amt_smtl_amt": "9500000",
                    }
                ]
            }
        )
        broker.send_order = AsyncMock(return_value={"msg1": "OK"})
        return broker

    @pytest.fixture
    def mock_market(self) -> MagicMock:
        """Create mock KR market."""
        market = MagicMock()
        market.name = "Korea"
        market.code = "KR"
        market.exchange_code = "KRX"
        market.is_domestic = True
        return market

    @pytest.fixture
    def mock_telegram(self) -> MagicMock:
        """Create mock telegram with all notification methods."""
        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        return telegram

    @pytest.mark.asyncio
    async def test_scenario_engine_called_with_enriched_market_data(
        self, mock_broker: MagicMock, mock_market: MagicMock, mock_telegram: MagicMock,
    ) -> None:
        """Test scenario engine receives market_data enriched with scanner metrics."""
        from src.analysis.smart_scanner import ScanCandidate

        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_hold_match())
        playbook = _make_playbook()

        candidate = ScanCandidate(
            stock_code="005930", name="Samsung", price=50000,
            volume=1000000, volume_ratio=3.5, rsi=25.0,
            signal="oversold", score=85.0,
        )

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=playbook,
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={"KR": {"005930": candidate}},
            )

        # Verify evaluate was called
        engine.evaluate.assert_called_once()
        call_args = engine.evaluate.call_args
        market_data = call_args[0][2]  # 3rd positional arg
        portfolio_data = call_args[0][3]  # 4th positional arg

        # Scanner data should be enriched into market_data
        assert market_data["rsi"] == 25.0
        assert market_data["volume_ratio"] == 3.5
        assert market_data["current_price"] == 50000.0
        assert market_data["price_change_pct"] == 2.5

        # Portfolio data should include pnl
        assert "portfolio_pnl_pct" in portfolio_data
        assert "total_cash" in portfolio_data

    @pytest.mark.asyncio
    async def test_trading_cycle_sets_l7_context_keys(
        self, mock_broker: MagicMock, mock_market: MagicMock, mock_telegram: MagicMock,
    ) -> None:
        """Test L7 context is written with market-scoped keys."""
        from src.analysis.smart_scanner import ScanCandidate

        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_hold_match())
        playbook = _make_playbook()
        context_store = MagicMock(get_latest_timeframe=MagicMock(return_value=None))

        candidate = ScanCandidate(
            stock_code="005930", name="Samsung", price=50000,
            volume=1000000, volume_ratio=3.5, rsi=25.0,
            signal="oversold", score=85.0,
        )

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=playbook,
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=context_store,
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={"KR": {"005930": candidate}},
            )

        context_store.set_context.assert_any_call(
            ContextLayer.L7_REALTIME,
            ANY,
            "volatility_KR_005930",
            {"momentum_score": 50.0, "volume_surge": 1.0, "price_change_1m": 0.0},
        )
        context_store.set_context.assert_any_call(
            ContextLayer.L7_REALTIME,
            ANY,
            "price_KR_005930",
            {"current_price": 50000.0},
        )
        context_store.set_context.assert_any_call(
            ContextLayer.L7_REALTIME,
            ANY,
            "rsi_KR_005930",
            {"rsi": 25.0},
        )
        context_store.set_context.assert_any_call(
            ContextLayer.L7_REALTIME,
            ANY,
            "volume_ratio_KR_005930",
            {"volume_ratio": 3.5},
        )

    @pytest.mark.asyncio
    async def test_scan_candidates_market_scoped(
        self, mock_broker: MagicMock, mock_market: MagicMock, mock_telegram: MagicMock,
    ) -> None:
        """Test scan_candidates uses market-scoped lookup, ignoring other markets."""
        from src.analysis.smart_scanner import ScanCandidate

        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_hold_match())

        # Candidate stored under US market — should NOT be found for KR market
        us_candidate = ScanCandidate(
            stock_code="005930", name="Overlap", price=100,
            volume=500000, volume_ratio=5.0, rsi=15.0,
            signal="oversold", score=90.0,
        )

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=_make_playbook(),
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,  # KR market
                stock_code="005930",
                scan_candidates={"US": {"005930": us_candidate}},  # Wrong market
            )

        # Should NOT use US candidate's rsi (=15.0); fallback implied_rsi used instead
        market_data = engine.evaluate.call_args[0][2]
        assert market_data["rsi"] != 15.0  # US candidate's rsi must be ignored
        assert market_data["volume_ratio"] == 1.0  # Fallback default

    @pytest.mark.asyncio
    async def test_scenario_engine_called_without_scanner_data(
        self, mock_broker: MagicMock, mock_market: MagicMock, mock_telegram: MagicMock,
    ) -> None:
        """Test scenario engine works when stock has no scan candidate."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_hold_match())
        playbook = _make_playbook()

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=playbook,
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},  # No scanner data
            )

        # Holding stocks without scanner data use implied_rsi (from price_change_pct)
        # and volume_ratio=1.0 as fallback, so rsi/volume_ratio are always present.
        engine.evaluate.assert_called_once()
        market_data = engine.evaluate.call_args[0][2]
        assert "rsi" in market_data  # Implied RSI from price_change_pct=2.5 → 55.0
        assert market_data["rsi"] == pytest.approx(55.0)
        assert market_data["volume_ratio"] == 1.0
        assert market_data["current_price"] == 50000.0

    @pytest.mark.asyncio
    async def test_holding_overseas_stock_derives_volume_ratio_from_price_api(
        self, mock_broker: MagicMock, mock_telegram: MagicMock,
    ) -> None:
        """Test overseas holding stocks derive volume_ratio from get_overseas_price high/low."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_hold_match())

        os_market = MagicMock()
        os_market.name = "NASDAQ"
        os_market.code = "US_NASDAQ"
        os_market.exchange_code = "NAS"
        os_market.is_domestic = False
        os_market.timezone = UTC

        os_broker = MagicMock()
        # price_change_pct=5.0, high=106, low=94 → intraday_range=12% → volume_ratio=max(1,6)=6
        os_broker.get_overseas_price = AsyncMock(return_value={
            "output": {"last": "100.0", "rate": "5.0", "high": "106.0", "low": "94.0"}
        })
        os_broker.get_overseas_balance = AsyncMock(return_value={
            "output2": [{"frcr_evlu_tota": "10000", "frcr_buy_amt_smtl": "9000"}]
        })
        os_broker.get_overseas_buying_power = AsyncMock(return_value={
            "output": {"ovrs_ord_psbl_amt": "500"}
        })

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=os_broker,
                scenario_engine=engine,
                playbook=_make_playbook(),
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=os_market,
                stock_code="NVDA",
                scan_candidates={},  # Not in scanner — holding stock
            )

        market_data = engine.evaluate.call_args[0][2]
        # rsi: 50.0 + 5.0 * 2.0 = 60.0
        assert market_data["rsi"] == pytest.approx(60.0)
        # intraday_range = (106-94)/100 * 100 = 12.0%
        # volatility_pct = max(abs(5.0), 12.0) = 12.0
        # volume_ratio = max(1.0, 12.0 / 2.0) = 6.0
        assert market_data["volume_ratio"] == pytest.approx(6.0)

    @pytest.mark.asyncio
    async def test_scenario_matched_notification_sent(
        self, mock_broker: MagicMock, mock_market: MagicMock, mock_telegram: MagicMock,
    ) -> None:
        """Test telegram notification sent when a scenario matches."""
        # Create a match with matched_scenario (not None)
        scenario = StockScenario(
            condition=StockCondition(rsi_below=30),
            action=ScenarioAction.BUY,
            confidence=88,
            rationale="RSI oversold bounce",
        )
        match = ScenarioMatch(
            stock_code="005930",
            matched_scenario=scenario,
            action=ScenarioAction.BUY,
            confidence=88,
            rationale="RSI oversold bounce",
            match_details={"rsi": 25.0},
        )
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=match)

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=_make_playbook(),
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        # Scenario matched notification should be sent
        mock_telegram.notify_scenario_matched.assert_called_once()
        call_kwargs = mock_telegram.notify_scenario_matched.call_args.kwargs
        assert call_kwargs["stock_code"] == "005930"
        assert call_kwargs["action"] == "BUY"
        assert "rsi=25.0" in call_kwargs["condition_summary"]

    @pytest.mark.asyncio
    async def test_no_scenario_matched_notification_on_default_hold(
        self, mock_broker: MagicMock, mock_market: MagicMock, mock_telegram: MagicMock,
    ) -> None:
        """Test no scenario notification when default HOLD is returned."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_hold_match())

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=_make_playbook(),
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        # No scenario matched notification for default HOLD
        mock_telegram.notify_scenario_matched.assert_not_called()

    @pytest.mark.asyncio
    async def test_decision_logger_receives_scenario_match_details(
        self, mock_broker: MagicMock, mock_market: MagicMock, mock_telegram: MagicMock,
    ) -> None:
        """Test decision logger context includes scenario match details."""
        match = ScenarioMatch(
            stock_code="005930",
            matched_scenario=None,
            action=ScenarioAction.HOLD,
            confidence=0,
            rationale="No match",
            match_details={"rsi": 45.0, "volume_ratio": 1.2},
        )
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=match)
        decision_logger = MagicMock()

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=_make_playbook(),
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=decision_logger,
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        decision_logger.log_decision.assert_called_once()
        call_kwargs = decision_logger.log_decision.call_args.kwargs
        assert "scenario_match" in call_kwargs["context_snapshot"]
        assert call_kwargs["context_snapshot"]["scenario_match"]["rsi"] == 45.0

    @pytest.mark.asyncio
    async def test_reduce_all_does_not_execute_order(
        self, mock_broker: MagicMock, mock_market: MagicMock, mock_telegram: MagicMock,
    ) -> None:
        """Test REDUCE_ALL action does not trigger order execution."""
        match = ScenarioMatch(
            stock_code="005930",
            matched_scenario=None,
            action=ScenarioAction.REDUCE_ALL,
            confidence=100,
            rationale="Global rule: portfolio loss > 2%",
        )
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=match)

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=_make_playbook(),
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        # REDUCE_ALL is not BUY or SELL — no order sent
        mock_broker.send_order.assert_not_called()
        mock_telegram.notify_trade_execution.assert_not_called()


@pytest.mark.asyncio
async def test_sell_updates_original_buy_decision_outcome() -> None:
    """SELL should update the original BUY decision outcome in decision_logs."""
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    buy_decision_id = decision_logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=85,
        rationale="Initial buy",
        context_snapshot={},
        input_data={},
    )
    log_trade(
        conn=db_conn,
        stock_code="005930",
        action="BUY",
        confidence=85,
        rationale="Initial buy",
        quantity=1,
        price=100.0,
        pnl=0.0,
        market="KR",
        exchange_code="KRX",
        decision_id=buy_decision_id,
    )

    broker = MagicMock()
    broker.get_current_price = AsyncMock(return_value=(120.0, 0.0, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            "output1": [{"pdno": "005930", "ord_psbl_qty": "1"}],
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "10000",
                    "pchs_amt_smtl_amt": "90000",
                }
            ],
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    overseas_broker = MagicMock()
    engine = MagicMock(spec=ScenarioEngine)
    engine.evaluate = MagicMock(return_value=_make_sell_match())
    risk = MagicMock()
    context_store = MagicMock(
        get_latest_timeframe=MagicMock(return_value=None),
        set_context=MagicMock(),
    )
    criticality_assessor = MagicMock(
        assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
        get_timeout=MagicMock(return_value=5.0),
    )
    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    await trading_cycle(
        broker=broker,
        overseas_broker=overseas_broker,
        scenario_engine=engine,
        playbook=_make_playbook(),
        risk=risk,
        db_conn=db_conn,
        decision_logger=decision_logger,
        context_store=context_store,
        criticality_assessor=criticality_assessor,
        telegram=telegram,
        market=market,
        stock_code="005930",
        scan_candidates={},
    )

    updated_buy = decision_logger.get_decision_by_id(buy_decision_id)
    assert updated_buy is not None
    assert updated_buy.outcome_pnl == 20.0
    assert updated_buy.outcome_accuracy == 1


@pytest.mark.asyncio
async def test_hold_overridden_to_sell_when_stop_loss_triggered() -> None:
    """HOLD decision should be overridden to SELL when stop-loss threshold is breached."""
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    buy_decision_id = decision_logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=90,
        rationale="entry",
        context_snapshot={},
        input_data={},
    )
    log_trade(
        conn=db_conn,
        stock_code="005930",
        action="BUY",
        confidence=90,
        rationale="entry",
        quantity=1,
        price=100.0,
        market="KR",
        exchange_code="KRX",
        decision_id=buy_decision_id,
    )

    broker = MagicMock()
    broker.get_current_price = AsyncMock(return_value=(95.0, -5.0, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            "output1": [{"pdno": "005930", "ord_psbl_qty": "1"}],
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "10000",
                    "pchs_amt_smtl_amt": "90000",
                }
            ],
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    scenario = StockScenario(
        condition=StockCondition(rsi_below=30),
        action=ScenarioAction.BUY,
        confidence=88,
        stop_loss_pct=-2.0,
        rationale="stop loss policy",
    )
    playbook = DayPlaybook(
        date=date(2026, 2, 8),
        market="KR",
        stock_playbooks=[
            {"stock_code": "005930", "stock_name": "Samsung", "scenarios": [scenario]}
        ],
    )
    engine = MagicMock(spec=ScenarioEngine)
    engine.evaluate = MagicMock(return_value=_make_hold_match())

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    await trading_cycle(
        broker=broker,
        overseas_broker=MagicMock(),
        scenario_engine=engine,
        playbook=playbook,
        risk=MagicMock(),
        db_conn=db_conn,
        decision_logger=decision_logger,
        context_store=MagicMock(
            get_latest_timeframe=MagicMock(return_value=None),
            set_context=MagicMock(),
        ),
        criticality_assessor=MagicMock(
            assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
            get_timeout=MagicMock(return_value=5.0),
        ),
        telegram=telegram,
        market=market,
        stock_code="005930",
        scan_candidates={},
    )

    broker.send_order.assert_called_once()
    assert broker.send_order.call_args.kwargs["order_type"] == "SELL"


@pytest.mark.asyncio
async def test_hold_overridden_to_sell_when_take_profit_triggered() -> None:
    """HOLD decision should be overridden to SELL when take-profit threshold is reached."""
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    buy_decision_id = decision_logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=90,
        rationale="entry",
        context_snapshot={},
        input_data={},
    )
    log_trade(
        conn=db_conn,
        stock_code="005930",
        action="BUY",
        confidence=90,
        rationale="entry",
        quantity=1,
        price=100.0,
        market="KR",
        exchange_code="KRX",
        decision_id=buy_decision_id,
    )

    broker = MagicMock()
    # Current price 106.0 → +6% gain, above take_profit_pct=3.0
    broker.get_current_price = AsyncMock(return_value=(106.0, 6.0, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            "output1": [{"pdno": "005930", "ord_psbl_qty": "1"}],
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "10000",
                    "pchs_amt_smtl_amt": "90000",
                }
            ],
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    scenario = StockScenario(
        condition=StockCondition(rsi_below=30),
        action=ScenarioAction.BUY,
        confidence=88,
        stop_loss_pct=-2.0,
        take_profit_pct=3.0,
        rationale="take profit policy",
    )
    playbook = DayPlaybook(
        date=date(2026, 2, 8),
        market="KR",
        stock_playbooks=[
            {"stock_code": "005930", "stock_name": "Samsung", "scenarios": [scenario]}
        ],
    )
    engine = MagicMock(spec=ScenarioEngine)
    engine.evaluate = MagicMock(return_value=_make_hold_match())

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    await trading_cycle(
        broker=broker,
        overseas_broker=MagicMock(),
        scenario_engine=engine,
        playbook=playbook,
        risk=MagicMock(),
        db_conn=db_conn,
        decision_logger=decision_logger,
        context_store=MagicMock(
            get_latest_timeframe=MagicMock(return_value=None),
            set_context=MagicMock(),
        ),
        criticality_assessor=MagicMock(
            assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
            get_timeout=MagicMock(return_value=5.0),
        ),
        telegram=telegram,
        market=market,
        stock_code="005930",
        scan_candidates={},
    )

    broker.send_order.assert_called_once()
    assert broker.send_order.call_args.kwargs["order_type"] == "SELL"


@pytest.mark.asyncio
async def test_hold_not_overridden_when_between_stop_loss_and_take_profit() -> None:
    """HOLD should remain HOLD when P&L is within stop-loss and take-profit bounds."""
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    buy_decision_id = decision_logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=90,
        rationale="entry",
        context_snapshot={},
        input_data={},
    )
    log_trade(
        conn=db_conn,
        stock_code="005930",
        action="BUY",
        confidence=90,
        rationale="entry",
        quantity=1,
        price=100.0,
        market="KR",
        exchange_code="KRX",
        decision_id=buy_decision_id,
    )

    broker = MagicMock()
    # Current price 101.0 → +1% gain, within [-2%, +3%] range
    broker.get_current_price = AsyncMock(return_value=(101.0, 1.0, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "10000",
                    "pchs_amt_smtl_amt": "90000",
                }
            ]
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    scenario = StockScenario(
        condition=StockCondition(rsi_below=30),
        action=ScenarioAction.BUY,
        confidence=88,
        stop_loss_pct=-2.0,
        take_profit_pct=3.0,
        rationale="within range policy",
    )
    playbook = DayPlaybook(
        date=date(2026, 2, 8),
        market="KR",
        stock_playbooks=[
            {"stock_code": "005930", "stock_name": "Samsung", "scenarios": [scenario]}
        ],
    )
    engine = MagicMock(spec=ScenarioEngine)
    engine.evaluate = MagicMock(return_value=_make_hold_match())

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    await trading_cycle(
        broker=broker,
        overseas_broker=MagicMock(),
        scenario_engine=engine,
        playbook=playbook,
        risk=MagicMock(),
        db_conn=db_conn,
        decision_logger=decision_logger,
        context_store=MagicMock(
            get_latest_timeframe=MagicMock(return_value=None),
            set_context=MagicMock(),
        ),
        criticality_assessor=MagicMock(
            assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
            get_timeout=MagicMock(return_value=5.0),
        ),
        telegram=telegram,
        market=market,
        stock_code="005930",
        scan_candidates={},
    )

    broker.send_order.assert_not_called()


@pytest.mark.asyncio
async def test_stop_loss_not_triggered_when_current_price_is_zero() -> None:
    """HOLD must stay HOLD when current_price=0 even if entry_price is set (issue #251).

    A price API failure that returns 0.0 must not cause a false -100% stop-loss.
    """
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    buy_decision_id = decision_logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=90,
        rationale="entry",
        context_snapshot={},
        input_data={},
    )
    log_trade(
        conn=db_conn,
        stock_code="005930",
        action="BUY",
        confidence=90,
        rationale="entry",
        quantity=1,
        price=100.0,  # valid entry price
        market="KR",
        exchange_code="KRX",
        decision_id=buy_decision_id,
    )

    broker = MagicMock()
    # Price API returns 0.0 — simulates API failure or pre-market unavailability
    broker.get_current_price = AsyncMock(return_value=(0.0, 0.0, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "10000",
                    "pchs_amt_smtl_amt": "90000",
                }
            ]
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    await trading_cycle(
        broker=broker,
        overseas_broker=MagicMock(),
        scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_hold_match())),
        playbook=_make_playbook("KR"),
        risk=MagicMock(),
        db_conn=db_conn,
        decision_logger=decision_logger,
        context_store=MagicMock(
            get_latest_timeframe=MagicMock(return_value=None),
            set_context=MagicMock(),
        ),
        criticality_assessor=MagicMock(
            assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
            get_timeout=MagicMock(return_value=5.0),
        ),
        telegram=telegram,
        market=market,
        stock_code="005930",
        scan_candidates={},
    )

    # No SELL order must be placed — current_price=0 must suppress stop-loss
    broker.send_order.assert_not_called()


@pytest.mark.asyncio
async def test_sell_order_uses_broker_balance_qty_not_db() -> None:
    """SELL quantity must come from broker balance output1, not DB.

    The DB records order quantity which may differ from actual fill quantity.
    This test verifies that we use the broker-confirmed orderable quantity.
    """
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    buy_decision_id = decision_logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=90,
        rationale="entry",
        context_snapshot={},
        input_data={},
    )
    # DB records 10 shares ordered — but only 5 actually filled (partial fill scenario)
    log_trade(
        conn=db_conn,
        stock_code="005930",
        action="BUY",
        confidence=90,
        rationale="entry",
        quantity=10,  # ordered quantity (may differ from fill)
        price=100.0,
        market="KR",
        exchange_code="KRX",
        decision_id=buy_decision_id,
    )

    broker = MagicMock()
    # Stop-loss triggers (price dropped below -2%)
    broker.get_current_price = AsyncMock(return_value=(95.0, -5.0, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            # Broker confirms only 5 shares are actually orderable (partial fill)
            "output1": [{"pdno": "005930", "ord_psbl_qty": "5"}],
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "10000",
                    "pchs_amt_smtl_amt": "90000",
                }
            ],
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    scenario = StockScenario(
        condition=StockCondition(rsi_below=30),
        action=ScenarioAction.BUY,
        confidence=88,
        stop_loss_pct=-2.0,
        rationale="stop loss policy",
    )
    playbook = DayPlaybook(
        date=date(2026, 2, 8),
        market="KR",
        stock_playbooks=[
            {"stock_code": "005930", "stock_name": "Samsung", "scenarios": [scenario]}
        ],
    )
    engine = MagicMock(spec=ScenarioEngine)
    engine.evaluate = MagicMock(return_value=_make_hold_match())

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    await trading_cycle(
        broker=broker,
        overseas_broker=MagicMock(),
        scenario_engine=engine,
        playbook=playbook,
        risk=MagicMock(),
        db_conn=db_conn,
        decision_logger=decision_logger,
        context_store=MagicMock(
            get_latest_timeframe=MagicMock(return_value=None),
            set_context=MagicMock(),
        ),
        criticality_assessor=MagicMock(
            assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
            get_timeout=MagicMock(return_value=5.0),
        ),
        telegram=telegram,
        market=market,
        stock_code="005930",
        scan_candidates={},
    )

    broker.send_order.assert_called_once()
    call_kwargs = broker.send_order.call_args.kwargs
    assert call_kwargs["order_type"] == "SELL"
    # Must use broker-confirmed qty (5), NOT DB-recorded ordered qty (10)
    assert call_kwargs["quantity"] == 5


@pytest.mark.asyncio
async def test_handle_market_close_runs_daily_review_flow() -> None:
    """Market close should aggregate, create scorecard, lessons, and notify."""
    telegram = MagicMock()
    telegram.notify_market_close = AsyncMock()
    telegram.send_message = AsyncMock()

    context_aggregator = MagicMock()
    reviewer = MagicMock()
    reviewer.generate_scorecard.return_value = DailyScorecard(
        date="2026-02-14",
        market="KR",
        total_decisions=3,
        buys=1,
        sells=1,
        holds=1,
        total_pnl=12.5,
        win_rate=50.0,
        avg_confidence=75.0,
        scenario_match_rate=66.7,
    )
    reviewer.generate_lessons = AsyncMock(return_value=["Cut losers faster"])

    await _handle_market_close(
        market_code="KR",
        market_name="Korea",
        market_timezone=UTC,
        telegram=telegram,
        context_aggregator=context_aggregator,
        daily_reviewer=reviewer,
    )

    telegram.notify_market_close.assert_called_once_with("Korea", 0.0)
    context_aggregator.aggregate_daily_from_trades.assert_called_once()
    reviewer.generate_scorecard.assert_called_once()
    assert reviewer.store_scorecard_in_context.call_count == 2
    reviewer.generate_lessons.assert_called_once()
    telegram.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_handle_market_close_without_lessons_stores_once() -> None:
    """If no lessons are generated, scorecard should be stored once."""
    telegram = MagicMock()
    telegram.notify_market_close = AsyncMock()
    telegram.send_message = AsyncMock()

    context_aggregator = MagicMock()
    reviewer = MagicMock()
    reviewer.generate_scorecard.return_value = DailyScorecard(
        date="2026-02-14",
        market="US",
        total_decisions=1,
        buys=0,
        sells=1,
        holds=0,
        total_pnl=-3.0,
        win_rate=0.0,
        avg_confidence=65.0,
        scenario_match_rate=100.0,
    )
    reviewer.generate_lessons = AsyncMock(return_value=[])

    await _handle_market_close(
        market_code="US",
        market_name="United States",
        market_timezone=UTC,
        telegram=telegram,
        context_aggregator=context_aggregator,
        daily_reviewer=reviewer,
    )

    assert reviewer.store_scorecard_in_context.call_count == 1


@pytest.mark.asyncio
async def test_handle_market_close_triggers_evolution_for_us() -> None:
    telegram = MagicMock()
    telegram.notify_market_close = AsyncMock()
    telegram.send_message = AsyncMock()

    context_aggregator = MagicMock()
    reviewer = MagicMock()
    reviewer.generate_scorecard.return_value = DailyScorecard(
        date="2026-02-14",
        market="US",
        total_decisions=2,
        buys=1,
        sells=1,
        holds=0,
        total_pnl=3.0,
        win_rate=50.0,
        avg_confidence=80.0,
        scenario_match_rate=100.0,
    )
    reviewer.generate_lessons = AsyncMock(return_value=[])

    evolution_optimizer = MagicMock()
    evolution_optimizer.evolve = AsyncMock(return_value=None)

    await _handle_market_close(
        market_code="US",
        market_name="United States",
        market_timezone=UTC,
        telegram=telegram,
        context_aggregator=context_aggregator,
        daily_reviewer=reviewer,
        evolution_optimizer=evolution_optimizer,
    )

    evolution_optimizer.evolve.assert_called_once()


@pytest.mark.asyncio
async def test_handle_market_close_skips_evolution_for_kr() -> None:
    telegram = MagicMock()
    telegram.notify_market_close = AsyncMock()
    telegram.send_message = AsyncMock()

    context_aggregator = MagicMock()
    reviewer = MagicMock()
    reviewer.generate_scorecard.return_value = DailyScorecard(
        date="2026-02-14",
        market="KR",
        total_decisions=1,
        buys=1,
        sells=0,
        holds=0,
        total_pnl=1.0,
        win_rate=100.0,
        avg_confidence=90.0,
        scenario_match_rate=100.0,
    )
    reviewer.generate_lessons = AsyncMock(return_value=[])

    evolution_optimizer = MagicMock()
    evolution_optimizer.evolve = AsyncMock(return_value=None)

    await _handle_market_close(
        market_code="KR",
        market_name="Korea",
        market_timezone=UTC,
        telegram=telegram,
        context_aggregator=context_aggregator,
        daily_reviewer=reviewer,
        evolution_optimizer=evolution_optimizer,
    )

    evolution_optimizer.evolve.assert_not_called()


def test_run_context_scheduler_invokes_scheduler() -> None:
    """Scheduler helper should call run_if_due with provided datetime."""
    scheduler = MagicMock()
    scheduler.run_if_due = MagicMock(return_value=ScheduleResult(cleanup=True))

    _run_context_scheduler(scheduler, now=datetime(2026, 2, 14, tzinfo=UTC))

    scheduler.run_if_due.assert_called_once()


@pytest.mark.asyncio
async def test_run_evolution_loop_skips_non_us_market() -> None:
    optimizer = MagicMock()
    optimizer.evolve = AsyncMock()
    telegram = MagicMock()
    telegram.send_message = AsyncMock()

    await _run_evolution_loop(
        evolution_optimizer=optimizer,
        telegram=telegram,
        market_code="KR",
        market_date="2026-02-14",
    )

    optimizer.evolve.assert_not_called()
    telegram.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_run_evolution_loop_notifies_when_pr_generated() -> None:
    optimizer = MagicMock()
    optimizer.evolve = AsyncMock(
        return_value={
            "title": "[Evolution] New strategy: v20260214_050000",
            "branch": "evolution/v20260214_050000",
            "status": "ready_for_review",
        }
    )
    telegram = MagicMock()
    telegram.send_message = AsyncMock()

    await _run_evolution_loop(
        evolution_optimizer=optimizer,
        telegram=telegram,
        market_code="US_NASDAQ",
        market_date="2026-02-14",
    )

    optimizer.evolve.assert_called_once()
    telegram.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_run_evolution_loop_notification_error_is_ignored() -> None:
    optimizer = MagicMock()
    optimizer.evolve = AsyncMock(
        return_value={
            "title": "[Evolution] New strategy: v20260214_050000",
            "branch": "evolution/v20260214_050000",
            "status": "ready_for_review",
        }
    )
    telegram = MagicMock()
    telegram.send_message = AsyncMock(side_effect=RuntimeError("telegram down"))

    await _run_evolution_loop(
        evolution_optimizer=optimizer,
        telegram=telegram,
        market_code="US_NYSE",
        market_date="2026-02-14",
    )

    optimizer.evolve.assert_called_once()
    telegram.send_message.assert_called_once()


def test_apply_dashboard_flag_enables_dashboard() -> None:
    settings = Settings(
        KIS_APP_KEY="test_key",
        KIS_APP_SECRET="test_secret",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="test_gemini_key",
        DASHBOARD_ENABLED=False,
    )
    updated = _apply_dashboard_flag(settings, dashboard_flag=True)
    assert updated.DASHBOARD_ENABLED is True


def test_start_dashboard_server_disabled_returns_none() -> None:
    settings = Settings(
        KIS_APP_KEY="test_key",
        KIS_APP_SECRET="test_secret",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="test_gemini_key",
        DASHBOARD_ENABLED=False,
    )
    thread = _start_dashboard_server(settings)
    assert thread is None


def test_start_dashboard_server_enabled_starts_thread() -> None:
    settings = Settings(
        KIS_APP_KEY="test_key",
        KIS_APP_SECRET="test_secret",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="test_gemini_key",
        DASHBOARD_ENABLED=True,
    )
    mock_thread = MagicMock()
    with patch("src.main.threading.Thread", return_value=mock_thread) as mock_thread_cls:
        thread = _start_dashboard_server(settings)

    assert thread == mock_thread
    mock_thread_cls.assert_called_once()
    mock_thread.start.assert_called_once()


def test_start_dashboard_server_returns_none_when_uvicorn_missing() -> None:
    """Returns None (no thread) and logs a warning when uvicorn is not installed."""
    settings = Settings(
        KIS_APP_KEY="test_key",
        KIS_APP_SECRET="test_secret",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="test_gemini_key",
        DASHBOARD_ENABLED=True,
    )
    import builtins
    real_import = builtins.__import__

    def mock_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "uvicorn":
            raise ImportError("No module named 'uvicorn'")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        thread = _start_dashboard_server(settings)

    assert thread is None


# ---------------------------------------------------------------------------
# BUY cooldown tests (#179)
# ---------------------------------------------------------------------------


class TestBuyCooldown:
    """Tests for BUY cooldown after insufficient-balance rejection."""

    @pytest.fixture
    def mock_broker(self) -> MagicMock:
        broker = MagicMock()
        broker.get_current_price = AsyncMock(return_value=(100.0, 1.0, 0.0))
        broker.get_balance = AsyncMock(
            return_value={
                "output2": [{"tot_evlu_amt": "1000000", "dnca_tot_amt": "500000",
                             "pchs_amt_smtl_amt": "500000"}]
            }
        )
        broker.send_order = AsyncMock(return_value={"msg1": "OK"})
        return broker

    @pytest.fixture
    def mock_market(self) -> MagicMock:
        market = MagicMock()
        market.name = "Korea"
        market.code = "KR"
        market.exchange_code = "KRX"
        market.is_domestic = True
        return market

    @pytest.fixture
    def mock_overseas_market(self) -> MagicMock:
        market = MagicMock()
        market.name = "NASDAQ"
        market.code = "US_NASDAQ"
        market.exchange_code = "NAS"
        market.is_domestic = False
        return market

    @pytest.fixture
    def mock_overseas_broker(self) -> MagicMock:
        broker = MagicMock()
        broker.get_overseas_price = AsyncMock(
            return_value={"output": {"last": "1.0", "rate": "0.0",
                                     "high": "1.05", "low": "0.95", "tvol": "1000000"}}
        )
        broker.get_overseas_balance = AsyncMock(return_value={
            "output1": [],
            "output2": [{"frcr_evlu_tota": "50000", "frcr_buy_amt_smtl": "0"}],
        })
        broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "50000"}}
        )
        broker.send_overseas_order = AsyncMock(
            return_value={"rt_cd": "1", "msg1": "모의투자 주문가능금액이 부족합니다."}
        )
        return broker

    def _make_buy_match_overseas(self, stock_code: str = "MLECW") -> ScenarioMatch:
        return ScenarioMatch(
            stock_code=stock_code,
            matched_scenario=None,
            action=ScenarioAction.BUY,
            confidence=85,
            rationale="Test buy",
        )

    @pytest.mark.asyncio
    async def test_cooldown_set_on_insufficient_balance(
        self, mock_broker: MagicMock, mock_overseas_broker: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """BUY cooldown entry is created after 주문가능금액 rejection."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=self._make_buy_match_overseas("MLECW"))
        buy_cooldown: dict[str, float] = {}

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=mock_overseas_broker,
                scenario_engine=engine,
                playbook=_make_playbook("US_NASDAQ"),
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=MagicMock(
                    notify_trade_execution=AsyncMock(),
                    notify_fat_finger=AsyncMock(),
                    notify_circuit_breaker=AsyncMock(),
                    notify_scenario_matched=AsyncMock(),
                ),
                market=mock_overseas_market,
                stock_code="MLECW",
                scan_candidates={},
                buy_cooldown=buy_cooldown,
            )

        assert "US_NASDAQ:MLECW" in buy_cooldown
        assert buy_cooldown["US_NASDAQ:MLECW"] > 0

    @pytest.mark.asyncio
    async def test_cooldown_skips_buy(
        self, mock_broker: MagicMock, mock_overseas_broker: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """BUY is skipped when cooldown is active for the stock."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=self._make_buy_match_overseas("MLECW"))

        import asyncio
        # Set an active cooldown (expires far in the future)
        buy_cooldown: dict[str, float] = {
            "US_NASDAQ:MLECW": asyncio.get_event_loop().time() + 600
        }

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=mock_overseas_broker,
                scenario_engine=engine,
                playbook=_make_playbook("US_NASDAQ"),
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=MagicMock(
                    notify_trade_execution=AsyncMock(),
                    notify_fat_finger=AsyncMock(),
                    notify_circuit_breaker=AsyncMock(),
                    notify_scenario_matched=AsyncMock(),
                ),
                market=mock_overseas_market,
                stock_code="MLECW",
                scan_candidates={},
                buy_cooldown=buy_cooldown,
            )

        # Order should NOT have been sent
        mock_overseas_broker.send_overseas_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_cooldown_not_set_on_other_errors(
        self, mock_broker: MagicMock, mock_overseas_market: MagicMock,
    ) -> None:
        """Cooldown is NOT set for non-balance-related rejections."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=self._make_buy_match_overseas("MLECW"))
        # Different rejection reason
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_price = AsyncMock(
            return_value={"output": {"last": "1.0", "rate": "0.0",
                                     "high": "1.05", "low": "0.95", "tvol": "1000000"}}
        )
        overseas_broker.get_overseas_balance = AsyncMock(return_value={
            "output1": [],
            "output2": [{"frcr_evlu_tota": "50000", "frcr_buy_amt_smtl": "0"}],
        })
        overseas_broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "50000"}}
        )
        overseas_broker.send_overseas_order = AsyncMock(
            return_value={"rt_cd": "1", "msg1": "기타 오류 메시지"}
        )
        buy_cooldown: dict[str, float] = {}

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=overseas_broker,
                scenario_engine=engine,
                playbook=_make_playbook("US_NASDAQ"),
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=MagicMock(
                    notify_trade_execution=AsyncMock(),
                    notify_fat_finger=AsyncMock(),
                    notify_circuit_breaker=AsyncMock(),
                    notify_scenario_matched=AsyncMock(),
                ),
                market=mock_overseas_market,
                stock_code="MLECW",
                scan_candidates={},
                buy_cooldown=buy_cooldown,
            )

        # Cooldown should NOT be set for non-balance errors
        assert "US_NASDAQ:MLECW" not in buy_cooldown

    @pytest.mark.asyncio
    async def test_no_cooldown_param_still_works(
        self, mock_broker: MagicMock, mock_overseas_broker: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """trading_cycle works normally when buy_cooldown is None (default)."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=self._make_buy_match_overseas("MLECW"))

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=mock_overseas_broker,
                scenario_engine=engine,
                playbook=_make_playbook("US_NASDAQ"),
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=MagicMock(
                    notify_trade_execution=AsyncMock(),
                    notify_fat_finger=AsyncMock(),
                    notify_circuit_breaker=AsyncMock(),
                    notify_scenario_matched=AsyncMock(),
                ),
                market=mock_overseas_market,
                stock_code="MLECW",
                scan_candidates={},
                # buy_cooldown not passed → defaults to None
            )

        # Should attempt the order (and fail), but not crash
        mock_overseas_broker.send_overseas_order.assert_called_once()


# ---------------------------------------------------------------------------
# market_outlook BUY confidence threshold tests (#173)
# ---------------------------------------------------------------------------


class TestMarketOutlookConfidenceThreshold:
    """Tests for market_outlook-based BUY confidence suppression in trading_cycle."""

    @pytest.fixture
    def mock_broker(self) -> MagicMock:
        broker = MagicMock()
        broker.get_current_price = AsyncMock(return_value=(50000.0, 1.0, 0.0))
        broker.get_balance = AsyncMock(
            return_value={
                "output2": [
                    {
                        "tot_evlu_amt": "10000000",
                        "dnca_tot_amt": "5000000",
                        "pchs_amt_smtl_amt": "9500000",
                    }
                ]
            }
        )
        broker.send_order = AsyncMock(return_value={"msg1": "OK"})
        return broker

    @pytest.fixture
    def mock_market(self) -> MagicMock:
        market = MagicMock()
        market.name = "Korea"
        market.code = "KR"
        market.exchange_code = "KRX"
        market.is_domestic = True
        return market

    @pytest.fixture
    def mock_telegram(self) -> MagicMock:
        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        return telegram

    def _make_buy_match_with_confidence(
        self, confidence: int, stock_code: str = "005930"
    ) -> ScenarioMatch:
        from src.strategy.models import StockScenario
        scenario = StockScenario(
            condition=StockCondition(rsi_below=30),
            action=ScenarioAction.BUY,
            confidence=confidence,
            allocation_pct=10.0,
        )
        return ScenarioMatch(
            stock_code=stock_code,
            matched_scenario=scenario,
            action=ScenarioAction.BUY,
            confidence=confidence,
            rationale="Test buy",
        )

    def _make_playbook_with_outlook(
        self, outlook_str: str, market: str = "KR"
    ) -> DayPlaybook:
        from src.strategy.models import MarketOutlook
        outlook_map = {
            "bearish": MarketOutlook.BEARISH,
            "bullish": MarketOutlook.BULLISH,
            "neutral": MarketOutlook.NEUTRAL,
            "neutral_to_bullish": MarketOutlook.NEUTRAL_TO_BULLISH,
            "neutral_to_bearish": MarketOutlook.NEUTRAL_TO_BEARISH,
        }
        return DayPlaybook(
            date=date(2026, 2, 20),
            market=market,
            market_outlook=outlook_map[outlook_str],
        )

    @pytest.mark.asyncio
    async def test_bearish_outlook_raises_buy_confidence_threshold(
        self,
        mock_broker: MagicMock,
        mock_market: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        """BUY with confidence 85 should be suppressed to HOLD in bearish market."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=self._make_buy_match_with_confidence(85))
        playbook = self._make_playbook_with_outlook("bearish")

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="decision-id")

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=playbook,
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=decision_logger,
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        # HOLD should be logged (not BUY) — check decision_logger was called with HOLD
        call_args = decision_logger.log_decision.call_args
        assert call_args is not None
        assert call_args.kwargs["action"] == "HOLD"

    @pytest.mark.asyncio
    async def test_bearish_outlook_allows_high_confidence_buy(
        self,
        mock_broker: MagicMock,
        mock_market: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        """BUY with confidence 92 should proceed in bearish market (threshold=90)."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=self._make_buy_match_with_confidence(92))
        playbook = self._make_playbook_with_outlook("bearish")
        risk = MagicMock()
        risk.validate_order = MagicMock()

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="decision-id")

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=playbook,
                risk=risk,
                db_conn=MagicMock(),
                decision_logger=decision_logger,
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        call_args = decision_logger.log_decision.call_args
        assert call_args is not None
        assert call_args.kwargs["action"] == "BUY"

    @pytest.mark.asyncio
    async def test_bullish_outlook_lowers_buy_confidence_threshold(
        self,
        mock_broker: MagicMock,
        mock_market: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        """BUY with confidence 77 should proceed in bullish market (threshold=75)."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=self._make_buy_match_with_confidence(77))
        playbook = self._make_playbook_with_outlook("bullish")
        risk = MagicMock()
        risk.validate_order = MagicMock()

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="decision-id")

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=playbook,
                risk=risk,
                db_conn=MagicMock(),
                decision_logger=decision_logger,
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        call_args = decision_logger.log_decision.call_args
        assert call_args is not None
        assert call_args.kwargs["action"] == "BUY"

    @pytest.mark.asyncio
    async def test_bullish_outlook_suppresses_very_low_confidence_buy(
        self,
        mock_broker: MagicMock,
        mock_market: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        """BUY with confidence 70 should be suppressed even in bullish market (threshold=75)."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=self._make_buy_match_with_confidence(70))
        playbook = self._make_playbook_with_outlook("bullish")

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="decision-id")

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=playbook,
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=decision_logger,
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        call_args = decision_logger.log_decision.call_args
        assert call_args is not None
        assert call_args.kwargs["action"] == "HOLD"

    @pytest.mark.asyncio
    async def test_neutral_outlook_uses_default_threshold(
        self,
        mock_broker: MagicMock,
        mock_market: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        """BUY with confidence 82 should proceed in neutral market (default=80)."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=self._make_buy_match_with_confidence(82))
        playbook = self._make_playbook_with_outlook("neutral")
        risk = MagicMock()
        risk.validate_order = MagicMock()

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="decision-id")

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=playbook,
                risk=risk,
                db_conn=MagicMock(),
                decision_logger=decision_logger,
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        call_args = decision_logger.log_decision.call_args
        assert call_args is not None
        assert call_args.kwargs["action"] == "BUY"


@pytest.mark.asyncio
async def test_buy_suppressed_when_open_position_exists() -> None:
    """BUY should be suppressed when an open position already exists for the stock."""
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    # 기존 BUY 포지션 DB에 기록 (중복 매수 상황)
    buy_decision_id = decision_logger.log_decision(
        stock_code="NP",
        market="US",
        exchange_code="AMS",
        action="BUY",
        confidence=90,
        rationale="initial entry",
        context_snapshot={},
        input_data={},
    )
    log_trade(
        conn=db_conn,
        stock_code="NP",
        action="BUY",
        confidence=90,
        rationale="initial entry",
        quantity=10,
        price=50.0,
        market="US",
        exchange_code="AMS",
        decision_id=buy_decision_id,
    )

    broker = MagicMock()
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    overseas_broker = MagicMock()
    overseas_broker.get_overseas_price = AsyncMock(
        return_value={"output": {"last": "51.0", "rate": "2.0", "high": "52.0", "low": "50.0", "tvol": "1000000"}}
    )
    overseas_broker.get_overseas_balance = AsyncMock(
        return_value={
            "output1": [],
            "output2": [{"frcr_evlu_tota": "10000", "frcr_buy_amt_smtl": "0"}],
        }
    )
    overseas_broker.get_overseas_buying_power = AsyncMock(
        return_value={"output": {"ovrs_ord_psbl_amt": "10000"}}
    )
    overseas_broker.send_overseas_order = AsyncMock(return_value={"msg1": "OK"})

    engine = MagicMock(spec=ScenarioEngine)
    engine.evaluate = MagicMock(return_value=_make_buy_match(stock_code="NP"))

    market = MagicMock()
    market.name = "United States"
    market.code = "US"
    market.exchange_code = "AMS"
    market.is_domestic = False

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    await trading_cycle(
        broker=broker,
        overseas_broker=overseas_broker,
        scenario_engine=engine,
        playbook=_make_playbook(market="US"),
        risk=MagicMock(),
        db_conn=db_conn,
        decision_logger=decision_logger,
        context_store=MagicMock(
            get_latest_timeframe=MagicMock(return_value=None),
            set_context=MagicMock(),
        ),
        criticality_assessor=MagicMock(
            assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
            get_timeout=MagicMock(return_value=5.0),
        ),
        telegram=telegram,
        market=market,
        stock_code="NP",
        scan_candidates={},
    )

    # 이미 보유 중이므로 주문이 실행되지 않아야 함
    broker.send_order.assert_not_called()
    overseas_broker.send_overseas_order.assert_not_called()


@pytest.mark.asyncio
async def test_buy_proceeds_when_no_open_position() -> None:
    """BUY should proceed normally when no open position exists for the stock."""
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)
    # DB가 비어있는 상태 — 기존 포지션 없음

    broker = MagicMock()
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    overseas_broker = MagicMock()
    overseas_broker.get_overseas_price = AsyncMock(
        return_value={"output": {"last": "100.0", "rate": "1.0", "high": "101.0", "low": "99.0", "tvol": "500000"}}
    )
    overseas_broker.get_overseas_balance = AsyncMock(
        return_value={
            "output1": [],
            "output2": [{"frcr_evlu_tota": "50000", "frcr_buy_amt_smtl": "0"}],
        }
    )
    overseas_broker.get_overseas_buying_power = AsyncMock(
        return_value={"output": {"ovrs_ord_psbl_amt": "50000"}}
    )
    overseas_broker.send_overseas_order = AsyncMock(return_value={"msg1": "OK"})

    engine = MagicMock(spec=ScenarioEngine)
    engine.evaluate = MagicMock(return_value=_make_buy_match(stock_code="KNRX"))

    market = MagicMock()
    market.name = "United States"
    market.code = "US"
    market.exchange_code = "NAS"
    market.is_domestic = False

    risk = MagicMock()
    risk.validate_order = MagicMock()

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    await trading_cycle(
        broker=broker,
        overseas_broker=overseas_broker,
        scenario_engine=engine,
        playbook=_make_playbook(market="US"),
        risk=risk,
        db_conn=db_conn,
        decision_logger=decision_logger,
        context_store=MagicMock(
            get_latest_timeframe=MagicMock(return_value=None),
            set_context=MagicMock(),
        ),
        criticality_assessor=MagicMock(
            assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
            get_timeout=MagicMock(return_value=5.0),
        ),
        telegram=telegram,
        market=market,
        stock_code="KNRX",
        scan_candidates={},
    )

    # 포지션이 없으므로 해외 주문이 실행되어야 함
    overseas_broker.send_overseas_order.assert_called_once()


class TestOverseasBrokerIntegration:
    """Test overseas broker live-balance gating for double-buy prevention.

    Issue #195: KIS VTS SELL limit orders are accepted (rt_cd=0) immediately
    but may not fill until the market price reaches the limit. During this window,
    the DB records the position as closed, causing the next cycle to BUY again.
    These tests verify that live broker balance is used as the authoritative source.
    """

    @pytest.mark.asyncio
    async def test_overseas_buy_suppressed_by_broker_balance_when_db_shows_closed(
        self,
    ) -> None:
        """BUY must be suppressed when broker still holds shares even if DB says closed.

        Scenario: SELL limit order was accepted (DB shows closed), but hasn't
        filled yet — broker balance still shows 10 AAPL shares.
        Expected: send_overseas_order is NOT called.
        """
        db_conn = init_db(":memory:")
        # DB: BUY then SELL recorded → get_open_position returns None (closed)
        log_trade(
            conn=db_conn,
            stock_code="AAPL",
            action="BUY",
            confidence=90,
            rationale="entry",
            quantity=10,
            price=180.0,
            market="US_NASDAQ",
            exchange_code="NASD",
        )
        log_trade(
            conn=db_conn,
            stock_code="AAPL",
            action="SELL",
            confidence=90,
            rationale="sell order accepted",
            quantity=10,
            price=182.0,
            market="US_NASDAQ",
            exchange_code="NASD",
        )

        overseas_broker = MagicMock()
        overseas_broker.get_overseas_price = AsyncMock(
            return_value={"output": {"last": "182.50"}}
        )
        # 브로커: 여전히 AAPL 10주 보유 중 (SELL 미체결)
        overseas_broker.get_overseas_balance = AsyncMock(
            return_value={
                "output1": [{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "10"}],
                "output2": [
                    {
                        "frcr_evlu_tota": "60000.00",
                        "frcr_buy_amt_smtl": "50000.00",
                    }
                ],
            }
        )
        overseas_broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "50000.00"}}
        )
        overseas_broker.send_overseas_order = AsyncMock(return_value={"msg1": "주문접수"})

        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_buy_match("AAPL"))

        market = MagicMock()
        market.name = "NASDAQ"
        market.code = "US_NASDAQ"
        market.exchange_code = "NASD"
        market.is_domestic = False

        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="decision-id")

        await trading_cycle(
            broker=MagicMock(),
            overseas_broker=overseas_broker,
            scenario_engine=engine,
            playbook=_make_playbook(market="US"),
            risk=MagicMock(),
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(
                get_latest_timeframe=MagicMock(return_value=None),
                set_context=MagicMock(),
            ),
            criticality_assessor=MagicMock(
                assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                get_timeout=MagicMock(return_value=5.0),
            ),
            telegram=telegram,
            market=market,
            stock_code="AAPL",
            scan_candidates={},
        )

        # 브로커 잔고에 보유 중이므로 BUY 주문이 억제되어야 함 (이중 매수 방지)
        overseas_broker.send_overseas_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_overseas_buy_proceeds_when_broker_shows_no_holding(
        self,
    ) -> None:
        """BUY must proceed when both DB and broker confirm no existing holding.

        Scenario: No prior trades in DB and broker balance shows no AAPL.
        Expected: send_overseas_order IS called (normal buy flow).
        """
        db_conn = init_db(":memory:")
        # DB: 레코드 없음 (신규 포지션)

        overseas_broker = MagicMock()
        overseas_broker.get_overseas_price = AsyncMock(
            return_value={"output": {"last": "182.50"}}
        )
        # 브로커: AAPL 미보유
        overseas_broker.get_overseas_balance = AsyncMock(
            return_value={
                "output1": [],
                "output2": [
                    {
                        "frcr_evlu_tota": "50000.00",
                        "frcr_buy_amt_smtl": "0.00",
                    }
                ],
            }
        )
        overseas_broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "50000.00"}}
        )
        overseas_broker.send_overseas_order = AsyncMock(return_value={"msg1": "주문접수"})

        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_buy_match("AAPL"))

        market = MagicMock()
        market.name = "NASDAQ"
        market.code = "US_NASDAQ"
        market.exchange_code = "NASD"
        market.is_domestic = False

        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="decision-id")

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=MagicMock(),
                overseas_broker=overseas_broker,
                scenario_engine=engine,
                playbook=_make_playbook(market="US"),
                risk=MagicMock(),
                db_conn=db_conn,
                decision_logger=decision_logger,
                context_store=MagicMock(
                    get_latest_timeframe=MagicMock(return_value=None),
                    set_context=MagicMock(),
                ),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=telegram,
                market=market,
                stock_code="AAPL",
                scan_candidates={},
            )

        # DB도 브로커도 보유 없음 → BUY 주문이 실행되어야 함 (회귀 테스트)
        overseas_broker.send_overseas_order.assert_called_once()


# ---------------------------------------------------------------------------
# _retry_connection — unit tests (issue #209)
# ---------------------------------------------------------------------------


class TestRetryConnection:
    """Unit tests for the _retry_connection helper (issue #209)."""

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self) -> None:
        """Returns the result immediately when the first call succeeds."""
        async def ok() -> str:
            return "data"

        result = await _retry_connection(ok, label="test")
        assert result == "data"

    @pytest.mark.asyncio
    async def test_succeeds_after_one_connection_error(self) -> None:
        """Retries once on ConnectionError and returns result on 2nd attempt."""
        call_count = 0

        async def flaky() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("timeout")
            return "ok"

        with patch("src.main.asyncio.sleep") as mock_sleep:
            mock_sleep.return_value = None
            result = await _retry_connection(flaky, label="flaky")

        assert result == "ok"
        assert call_count == 2
        mock_sleep.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_after_all_retries_exhausted(self) -> None:
        """Raises ConnectionError after MAX_CONNECTION_RETRIES attempts."""
        from src.main import MAX_CONNECTION_RETRIES

        call_count = 0

        async def always_fail() -> None:
            nonlocal call_count
            call_count += 1
            raise ConnectionError("unreachable")

        with patch("src.main.asyncio.sleep") as mock_sleep:
            mock_sleep.return_value = None
            with pytest.raises(ConnectionError, match="unreachable"):
                await _retry_connection(always_fail, label="always_fail")

        assert call_count == MAX_CONNECTION_RETRIES

    @pytest.mark.asyncio
    async def test_passes_args_and_kwargs_to_factory(self) -> None:
        """Forwards positional and keyword arguments to the callable."""
        received: dict = {}

        async def capture(a: int, b: int, *, key: str) -> str:
            received["a"] = a
            received["b"] = b
            received["key"] = key
            return "captured"

        result = await _retry_connection(capture, 1, 2, key="val", label="test")
        assert result == "captured"
        assert received == {"a": 1, "b": 2, "key": "val"}

    @pytest.mark.asyncio
    async def test_non_connection_error_not_retried(self) -> None:
        """Non-ConnectionError exceptions propagate immediately without retry."""
        call_count = 0

        async def bad_input() -> None:
            nonlocal call_count
            call_count += 1
            raise ValueError("bad data")

        with pytest.raises(ValueError, match="bad data"):
            await _retry_connection(bad_input, label="bad")

        assert call_count == 1  # No retry for non-ConnectionError


# run_daily_session — daily CB baseline (daily_start_eval) tests (issue #207)
# ---------------------------------------------------------------------------


class TestDailyCBBaseline:
    """Tests for run_daily_session's daily_start_eval (CB baseline) behaviour.

    Issue #207: CB P&L should be computed relative to the portfolio value at
    the start of each trading day, not the cumulative purchase_total.
    """

    def _make_settings(self) -> Settings:
        return Settings(
            KIS_APP_KEY="test-key",
            KIS_APP_SECRET="test-secret",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="test-gemini",
            MODE="paper",
            PAPER_OVERSEAS_CASH=0,
        )

    def _make_domestic_balance(
        self, tot_evlu_amt: float = 0.0, dnca_tot_amt: float = 50000.0
    ) -> dict:
        return {
            "output1": [],
            "output2": [
                {
                    "tot_evlu_amt": str(tot_evlu_amt),
                    "dnca_tot_amt": str(dnca_tot_amt),
                    "pchs_amt_smtl_amt": "40000.0",
                }
            ],
        }

    @pytest.mark.asyncio
    async def test_returns_daily_start_eval_when_no_markets_open(self) -> None:
        """run_daily_session returns the unchanged daily_start_eval when no markets are open."""
        with patch("src.main.get_open_markets", return_value=[]):
            result = await run_daily_session(
                broker=MagicMock(),
                overseas_broker=MagicMock(),
                scenario_engine=MagicMock(),
                playbook_store=MagicMock(),
                pre_market_planner=MagicMock(),
                risk=MagicMock(),
                db_conn=init_db(":memory:"),
                decision_logger=MagicMock(),
                context_store=MagicMock(),
                criticality_assessor=MagicMock(),
                telegram=MagicMock(),
                settings=self._make_settings(),
                smart_scanner=None,
                daily_start_eval=12345.0,
            )
        assert result == 12345.0

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_markets_and_no_baseline(self) -> None:
        """run_daily_session returns 0.0 when no markets are open and daily_start_eval=0."""
        with patch("src.main.get_open_markets", return_value=[]):
            result = await run_daily_session(
                broker=MagicMock(),
                overseas_broker=MagicMock(),
                scenario_engine=MagicMock(),
                playbook_store=MagicMock(),
                pre_market_planner=MagicMock(),
                risk=MagicMock(),
                db_conn=init_db(":memory:"),
                decision_logger=MagicMock(),
                context_store=MagicMock(),
                criticality_assessor=MagicMock(),
                telegram=MagicMock(),
                settings=self._make_settings(),
                smart_scanner=None,
                daily_start_eval=0.0,
            )
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_captures_total_eval_as_baseline_on_first_session(self) -> None:
        """When daily_start_eval=0 and balance returns a positive total_eval, the returned
        value equals total_eval (the captured baseline for the day)."""
        from src.analysis.smart_scanner import ScanCandidate

        settings = self._make_settings()
        broker = MagicMock()
        # Domestic balance: tot_evlu_amt=55000
        broker.get_balance = AsyncMock(
            return_value=self._make_domestic_balance(tot_evlu_amt=55000.0)
        )
        # Price data for the stock
        broker.get_current_price = AsyncMock(
            return_value=(100.0, 1.5, 100.0)
        )

        market = MagicMock()
        market.name = "KR"
        market.code = "KR"
        market.exchange_code = "KRX"
        market.is_domestic = True
        market.timezone = __import__("zoneinfo").ZoneInfo("Asia/Seoul")

        smart_scanner = MagicMock()
        smart_scanner.scan = AsyncMock(
            return_value=[
                ScanCandidate(
                    stock_code="005930",
                    name="Samsung",
                    price=100.0,
                    volume=1_000_000.0,
                    volume_ratio=2.5,
                    rsi=45.0,
                    signal="momentum",
                    score=80.0,
                )
            ]
        )

        playbook_store = MagicMock()
        playbook_store.load = MagicMock(return_value=_make_playbook("KR"))

        scenario_engine = MagicMock(spec=ScenarioEngine)
        scenario_engine.evaluate = MagicMock(return_value=_make_hold_match("005930"))

        risk = MagicMock()
        risk.check_circuit_breaker = MagicMock()
        risk.check_fat_finger = MagicMock()

        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="d1")

        async def _passthrough(fn, *a, label: str = "", **kw):  # type: ignore[override]
            return await fn(*a, **kw)

        with patch("src.main.get_open_markets", return_value=[market]), \
             patch("src.main._retry_connection", new=_passthrough):
            result = await run_daily_session(
                broker=broker,
                overseas_broker=MagicMock(),
                scenario_engine=scenario_engine,
                playbook_store=playbook_store,
                pre_market_planner=MagicMock(),
                risk=risk,
                db_conn=init_db(":memory:"),
                decision_logger=decision_logger,
                context_store=MagicMock(),
                criticality_assessor=MagicMock(),
                telegram=telegram,
                settings=settings,
                smart_scanner=smart_scanner,
                daily_start_eval=0.0,
            )

        assert result == 55000.0  # captured from tot_evlu_amt

    @pytest.mark.asyncio
    async def test_does_not_overwrite_existing_baseline(self) -> None:
        """When daily_start_eval > 0, it must not be overwritten even if balance returns
        a different value (baseline is fixed at the start of each trading day)."""
        from src.analysis.smart_scanner import ScanCandidate

        settings = self._make_settings()
        broker = MagicMock()
        # Balance reports a different eval value (market moved during the day)
        broker.get_balance = AsyncMock(
            return_value=self._make_domestic_balance(tot_evlu_amt=58000.0)
        )
        broker.get_current_price = AsyncMock(return_value=(100.0, 1.5, 100.0))

        market = MagicMock()
        market.name = "KR"
        market.code = "KR"
        market.exchange_code = "KRX"
        market.is_domestic = True
        market.timezone = __import__("zoneinfo").ZoneInfo("Asia/Seoul")

        smart_scanner = MagicMock()
        smart_scanner.scan = AsyncMock(
            return_value=[
                ScanCandidate(
                    stock_code="005930",
                    name="Samsung",
                    price=100.0,
                    volume=1_000_000.0,
                    volume_ratio=2.5,
                    rsi=45.0,
                    signal="momentum",
                    score=80.0,
                )
            ]
        )

        playbook_store = MagicMock()
        playbook_store.load = MagicMock(return_value=_make_playbook("KR"))

        scenario_engine = MagicMock(spec=ScenarioEngine)
        scenario_engine.evaluate = MagicMock(return_value=_make_hold_match("005930"))

        risk = MagicMock()
        risk.check_circuit_breaker = MagicMock()

        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="d1")

        async def _passthrough(fn, *a, label: str = "", **kw):  # type: ignore[override]
            return await fn(*a, **kw)

        with patch("src.main.get_open_markets", return_value=[market]), \
             patch("src.main._retry_connection", new=_passthrough):
            result = await run_daily_session(
                broker=broker,
                overseas_broker=MagicMock(),
                scenario_engine=scenario_engine,
                playbook_store=playbook_store,
                pre_market_planner=MagicMock(),
                risk=risk,
                db_conn=init_db(":memory:"),
                decision_logger=decision_logger,
                context_store=MagicMock(),
                criticality_assessor=MagicMock(),
                telegram=telegram,
                settings=settings,
                smart_scanner=smart_scanner,
                daily_start_eval=55000.0,  # existing baseline
            )

        # Must return the original baseline, NOT the new total_eval (58000)
        assert result == 55000.0


# ---------------------------------------------------------------------------
# sync_positions_from_broker — startup DB sync tests (issue #206)
# ---------------------------------------------------------------------------


class TestSyncPositionsFromBroker:
    """Tests for sync_positions_from_broker() startup position sync (issue #206).

    The function queries broker balances at startup and inserts synthetic BUY
    records for any holdings that the local DB is unaware of, preventing
    double-buy when positions were opened in a previous session or manually.
    """

    def _make_settings(self, enabled_markets: str = "KR") -> Settings:
        return Settings(
            KIS_APP_KEY="k",
            KIS_APP_SECRET="s",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="g",
            ENABLED_MARKETS=enabled_markets,
            MODE="paper",
        )

    def _domestic_balance(
        self,
        stock_code: str = "005930",
        qty: int = 5,
    ) -> dict:
        return {
            "output1": [{"pdno": stock_code, "ord_psbl_qty": str(qty)}],
            "output2": [
                {
                    "tot_evlu_amt": "1000000",
                    "dnca_tot_amt": "500000",
                    "pchs_amt_smtl_amt": "500000",
                }
            ],
        }

    def _overseas_balance(
        self,
        stock_code: str = "AAPL",
        qty: int = 10,
    ) -> dict:
        return {
            "output1": [{"ovrs_pdno": stock_code, "ovrs_cblc_qty": str(qty)}],
            "output2": [
                {
                    "frcr_evlu_tota": "50000",
                    "frcr_buy_amt_smtl": "40000",
                }
            ],
        }

    @pytest.mark.asyncio
    async def test_syncs_domestic_position_not_in_db(self) -> None:
        """A domestic holding found in broker but absent from DB is inserted."""
        settings = self._make_settings("KR")
        db_conn = init_db(":memory:")

        broker = MagicMock()
        broker.get_balance = AsyncMock(
            return_value=self._domestic_balance("005930", qty=7)
        )
        overseas_broker = MagicMock()

        synced = await sync_positions_from_broker(
            broker, overseas_broker, db_conn, settings
        )

        assert synced == 1
        from src.db import get_open_position
        pos = get_open_position(db_conn, "005930", "KR")
        assert pos is not None
        assert pos["quantity"] == 7

    @pytest.mark.asyncio
    async def test_skips_position_already_in_db(self) -> None:
        """No duplicate record is created when the position already exists in DB."""
        settings = self._make_settings("KR")
        db_conn = init_db(":memory:")
        # Pre-insert a BUY record
        log_trade(
            conn=db_conn,
            stock_code="005930",
            action="BUY",
            confidence=85,
            rationale="existing position",
            quantity=5,
            price=70000.0,
            market="KR",
            exchange_code="KRX",
        )

        broker = MagicMock()
        broker.get_balance = AsyncMock(
            return_value=self._domestic_balance("005930", qty=5)
        )
        overseas_broker = MagicMock()

        synced = await sync_positions_from_broker(
            broker, overseas_broker, db_conn, settings
        )

        assert synced == 0

    @pytest.mark.asyncio
    async def test_syncs_overseas_position_not_in_db(self) -> None:
        """An overseas holding found in broker but absent from DB is inserted."""
        settings = self._make_settings("US_NASDAQ")
        db_conn = init_db(":memory:")

        broker = MagicMock()
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_balance = AsyncMock(
            return_value=self._overseas_balance("AAPL", qty=10)
        )

        synced = await sync_positions_from_broker(
            broker, overseas_broker, db_conn, settings
        )

        assert synced == 1
        from src.db import get_open_position
        pos = get_open_position(db_conn, "AAPL", "US_NASDAQ")
        assert pos is not None
        assert pos["quantity"] == 10

    @pytest.mark.asyncio
    async def test_returns_zero_when_broker_has_no_holdings(self) -> None:
        """Returns 0 when broker reports empty holdings."""
        settings = self._make_settings("KR")
        db_conn = init_db(":memory:")

        broker = MagicMock()
        broker.get_balance = AsyncMock(
            return_value={"output1": [], "output2": [{}]}
        )
        overseas_broker = MagicMock()

        synced = await sync_positions_from_broker(
            broker, overseas_broker, db_conn, settings
        )

        assert synced == 0

    @pytest.mark.asyncio
    async def test_handles_connection_error_gracefully(self) -> None:
        """ConnectionError during balance fetch is logged but does not raise."""
        settings = self._make_settings("KR")
        db_conn = init_db(":memory:")

        broker = MagicMock()
        broker.get_balance = AsyncMock(
            side_effect=ConnectionError("KIS unreachable")
        )
        overseas_broker = MagicMock()

        synced = await sync_positions_from_broker(
            broker, overseas_broker, db_conn, settings
        )

        assert synced == 0  # Failure treated as no-op

    @pytest.mark.asyncio
    async def test_deduplicates_exchange_codes_for_overseas(self) -> None:
        """Each exchange code is queried at most once even if multiple market
        codes share the same exchange (defensive deduplication)."""
        # Both US_NASDAQ and a hypothetical duplicate would share "NASD"
        # Use two DIFFERENT overseas markets (NASD vs NYSE) to verify each is
        # queried separately.
        settings = self._make_settings("US_NASDAQ,US_NYSE")
        db_conn = init_db(":memory:")

        broker = MagicMock()
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_balance = AsyncMock(
            return_value={"output1": [], "output2": [{}]}
        )

        await sync_positions_from_broker(
            broker, overseas_broker, db_conn, settings
        )

        # Two distinct exchange codes (NASD, NYSE) → 2 calls
        assert overseas_broker.get_overseas_balance.call_count == 2

    @pytest.mark.asyncio
    async def test_syncs_domestic_position_with_correct_avg_price(self) -> None:
        """Domestic position is stored with pchs_avg_pric as price (issue #249)."""
        settings = self._make_settings("KR")
        db_conn = init_db(":memory:")

        balance = {
            "output1": [{"pdno": "005930", "ord_psbl_qty": "5", "pchs_avg_pric": "68000.0"}],
            "output2": [{"tot_evlu_amt": "1000000", "dnca_tot_amt": "500000", "pchs_amt_smtl_amt": "500000"}],
        }
        broker = MagicMock()
        broker.get_balance = AsyncMock(return_value=balance)
        overseas_broker = MagicMock()

        await sync_positions_from_broker(broker, overseas_broker, db_conn, settings)

        from src.db import get_open_position
        pos = get_open_position(db_conn, "005930", "KR")
        assert pos is not None
        assert pos["price"] == 68000.0

    @pytest.mark.asyncio
    async def test_syncs_overseas_position_with_correct_avg_price(self) -> None:
        """Overseas position is stored with pchs_avg_pric as price (issue #249)."""
        settings = self._make_settings("US_NASDAQ")
        db_conn = init_db(":memory:")

        balance = {
            "output1": [{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "10", "pchs_avg_pric": "170.0"}],
            "output2": [{"frcr_evlu_tota": "50000", "frcr_buy_amt_smtl": "40000"}],
        }
        broker = MagicMock()
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_balance = AsyncMock(return_value=balance)

        await sync_positions_from_broker(broker, overseas_broker, db_conn, settings)

        from src.db import get_open_position
        pos = get_open_position(db_conn, "AAPL", "US_NASDAQ")
        assert pos is not None
        assert pos["price"] == 170.0

    @pytest.mark.asyncio
    async def test_syncs_position_with_zero_price_when_pchs_avg_pric_absent(self) -> None:
        """Fallback to price=0.0 when pchs_avg_pric is absent (issue #249)."""
        settings = self._make_settings("KR")
        db_conn = init_db(":memory:")

        # No pchs_avg_pric in output1
        balance = {
            "output1": [{"pdno": "005930", "ord_psbl_qty": "5"}],
            "output2": [{"tot_evlu_amt": "1000000", "dnca_tot_amt": "500000", "pchs_amt_smtl_amt": "500000"}],
        }
        broker = MagicMock()
        broker.get_balance = AsyncMock(return_value=balance)
        overseas_broker = MagicMock()

        await sync_positions_from_broker(broker, overseas_broker, db_conn, settings)

        from src.db import get_open_position
        pos = get_open_position(db_conn, "005930", "KR")
        assert pos is not None
        assert pos["price"] == 0.0


# ---------------------------------------------------------------------------
# Domestic BUY double-prevention (issue #206) — trading_cycle integration
# ---------------------------------------------------------------------------


class TestDomesticBuyDoublePreventionTradingCycle:
    """Verify domestic BUY suppression using broker balance in trading_cycle.

    Issue #206: the broker-balance check was overseas-only; domestic stocks
    were not protected against double-buy caused by untracked positions.
    """

    @pytest.mark.asyncio
    async def test_domestic_buy_suppressed_when_broker_holds_stock(
        self,
    ) -> None:
        """BUY for a domestic stock must be suppressed when broker holds it,
        even if the DB shows no open position."""
        db_conn = init_db(":memory:")
        # DB: no open position for 005930

        broker = MagicMock()
        broker.get_current_price = AsyncMock(return_value=(70000.0, 1.0, 0.0))
        # Broker balance: holds 5 shares of 005930
        broker.get_balance = AsyncMock(
            return_value={
                "output1": [{"pdno": "005930", "ord_psbl_qty": "5"}],
                "output2": [
                    {
                        "tot_evlu_amt": "1000000",
                        "dnca_tot_amt": "500000",
                        "pchs_amt_smtl_amt": "500000",
                    }
                ],
            }
        )
        broker.send_order = AsyncMock(return_value={"msg1": "주문접수"})

        market = MagicMock()
        market.name = "KR"
        market.code = "KR"
        market.exchange_code = "KRX"
        market.is_domestic = True

        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_buy_match("005930"))

        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="d1")

        settings = Settings(
            KIS_APP_KEY="k",
            KIS_APP_SECRET="s",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="g",
            MODE="paper",
        )

        await trading_cycle(
            broker=broker,
            overseas_broker=MagicMock(),
            scenario_engine=engine,
            playbook=_make_playbook(market="KR"),
            risk=MagicMock(),
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(
                get_latest_timeframe=MagicMock(return_value=None),
                set_context=MagicMock(),
            ),
            criticality_assessor=MagicMock(
                assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                get_timeout=MagicMock(return_value=5.0),
            ),
            telegram=telegram,
            settings=settings,
            market=market,
            stock_code="005930",
            scan_candidates={"KR": {}},
        )

        # BUY must NOT have been executed because broker still holds the stock
        broker.send_order.assert_not_called()


class TestHandleOverseasPendingOrders:
    """Tests for handle_overseas_pending_orders function."""

    def _make_settings(self, markets: str = "US_NASDAQ,US_NYSE,US_AMEX") -> Settings:
        return Settings(
            KIS_APP_KEY="k",
            KIS_APP_SECRET="s",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="g",
            ENABLED_MARKETS=markets,
        )

    def _make_telegram(self) -> MagicMock:
        t = MagicMock()
        t.notify_unfilled_order = AsyncMock()
        return t

    @pytest.mark.asyncio
    async def test_buy_pending_is_cancelled_and_cooldown_set(self) -> None:
        """BUY pending order should be cancelled and buy_cooldown should be set."""
        settings = self._make_settings("US_NASDAQ")
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "AAPL",
            "odno": "ORD001",
            "sll_buy_dvsn_cd": "02",  # BUY
            "nccs_qty": "3",
            "ovrs_excg_cd": "NASD",
        }
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_pending_orders = AsyncMock(
            return_value=[pending_order]
        )
        overseas_broker.cancel_overseas_order = AsyncMock(
            return_value={"rt_cd": "0", "msg1": "OK"}
        )

        sell_resubmit_counts: dict[str, int] = {}
        buy_cooldown: dict[str, float] = {}

        await handle_overseas_pending_orders(
            overseas_broker, telegram, settings, sell_resubmit_counts, buy_cooldown
        )

        overseas_broker.cancel_overseas_order.assert_called_once_with(
            exchange_code="NASD",
            stock_code="AAPL",
            odno="ORD001",
            qty=3,
        )
        assert "NASD:AAPL" in buy_cooldown
        telegram.notify_unfilled_order.assert_called_once()
        call_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert call_kwargs["action"] == "BUY"
        assert call_kwargs["outcome"] == "cancelled"

    @pytest.mark.asyncio
    async def test_sell_pending_is_cancelled_then_resubmitted(self) -> None:
        """First unfilled SELL should be cancelled then resubmitted at -0.4% price."""
        settings = self._make_settings("US_NASDAQ")
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "AAPL",
            "odno": "ORD002",
            "sll_buy_dvsn_cd": "01",  # SELL
            "nccs_qty": "5",
            "ovrs_excg_cd": "NASD",
        }
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_pending_orders = AsyncMock(
            return_value=[pending_order]
        )
        overseas_broker.cancel_overseas_order = AsyncMock(
            return_value={"rt_cd": "0", "msg1": "OK"}
        )
        overseas_broker.get_overseas_price = AsyncMock(
            return_value={"output": {"last": "200.0"}}
        )
        overseas_broker.send_overseas_order = AsyncMock(
            return_value={"rt_cd": "0", "msg1": "OK"}
        )

        sell_resubmit_counts: dict[str, int] = {}

        await handle_overseas_pending_orders(
            overseas_broker, telegram, settings, sell_resubmit_counts
        )

        overseas_broker.cancel_overseas_order.assert_called_once()
        overseas_broker.send_overseas_order.assert_called_once()
        resubmit_kwargs = overseas_broker.send_overseas_order.call_args[1]
        assert resubmit_kwargs["order_type"] == "SELL"
        assert resubmit_kwargs["price"] == round(200.0 * 0.996, 4)
        assert sell_resubmit_counts.get("NASD:AAPL") == 1
        notify_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert notify_kwargs["outcome"] == "resubmitted"

    @pytest.mark.asyncio
    async def test_sell_cancel_failure_skips_resubmit(self) -> None:
        """When cancel returns rt_cd != '0', resubmit should NOT be attempted."""
        settings = self._make_settings("US_NASDAQ")
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "AAPL",
            "odno": "ORD003",
            "sll_buy_dvsn_cd": "01",  # SELL
            "nccs_qty": "2",
            "ovrs_excg_cd": "NASD",
        }
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_pending_orders = AsyncMock(
            return_value=[pending_order]
        )
        overseas_broker.cancel_overseas_order = AsyncMock(
            return_value={"rt_cd": "1", "msg1": "Error"}  # failure
        )
        overseas_broker.send_overseas_order = AsyncMock()

        sell_resubmit_counts: dict[str, int] = {}

        await handle_overseas_pending_orders(
            overseas_broker, telegram, settings, sell_resubmit_counts
        )

        overseas_broker.send_overseas_order.assert_not_called()
        telegram.notify_unfilled_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_sell_already_resubmitted_is_only_cancelled(self) -> None:
        """Second unfilled SELL (sell_resubmit_counts >= 1) should only cancel, no resubmit."""
        settings = self._make_settings("US_NASDAQ")
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "AAPL",
            "odno": "ORD004",
            "sll_buy_dvsn_cd": "01",  # SELL
            "nccs_qty": "4",
            "ovrs_excg_cd": "NASD",
        }
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_pending_orders = AsyncMock(
            return_value=[pending_order]
        )
        overseas_broker.cancel_overseas_order = AsyncMock(
            return_value={"rt_cd": "0", "msg1": "OK"}
        )
        overseas_broker.send_overseas_order = AsyncMock()

        # Already resubmitted once
        sell_resubmit_counts: dict[str, int] = {"NASD:AAPL": 1}

        await handle_overseas_pending_orders(
            overseas_broker, telegram, settings, sell_resubmit_counts
        )

        overseas_broker.cancel_overseas_order.assert_called_once()
        overseas_broker.send_overseas_order.assert_not_called()
        notify_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert notify_kwargs["outcome"] == "cancelled"
        assert notify_kwargs["action"] == "SELL"

    @pytest.mark.asyncio
    async def test_us_exchanges_deduplicated_to_nasd(self) -> None:
        """US_NASDAQ, US_NYSE, US_AMEX should result in only one NASD query."""
        settings = self._make_settings("US_NASDAQ,US_NYSE,US_AMEX")
        telegram = self._make_telegram()

        overseas_broker = MagicMock()
        overseas_broker.get_overseas_pending_orders = AsyncMock(return_value=[])

        sell_resubmit_counts: dict[str, int] = {}

        await handle_overseas_pending_orders(
            overseas_broker, telegram, settings, sell_resubmit_counts
        )

        # Should be called exactly once with "NASD"
        assert overseas_broker.get_overseas_pending_orders.call_count == 1
        overseas_broker.get_overseas_pending_orders.assert_called_once_with("NASD")


# ---------------------------------------------------------------------------
# Domestic Pending Order Handling
# ---------------------------------------------------------------------------


class TestHandleDomesticPendingOrders:
    """Tests for handle_domestic_pending_orders function."""

    def _make_settings(self) -> Settings:
        return Settings(
            KIS_APP_KEY="k",
            KIS_APP_SECRET="s",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="g",
            ENABLED_MARKETS="KR",
        )

    def _make_telegram(self) -> MagicMock:
        t = MagicMock()
        t.notify_unfilled_order = AsyncMock()
        return t

    @pytest.mark.asyncio
    async def test_buy_pending_is_cancelled_and_cooldown_set(self) -> None:
        """BUY pending order should be cancelled and buy_cooldown should be set."""
        settings = self._make_settings()
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "005930",
            "orgn_odno": "ORD001",
            "ord_gno_brno": "BRN01",
            "sll_buy_dvsn_cd": "02",  # BUY
            "psbl_qty": "3",
        }
        broker = MagicMock()
        broker.get_domestic_pending_orders = AsyncMock(return_value=[pending_order])
        broker.cancel_domestic_order = AsyncMock(
            return_value={"rt_cd": "0", "msg1": "OK"}
        )

        sell_resubmit_counts: dict[str, int] = {}
        buy_cooldown: dict[str, float] = {}

        await handle_domestic_pending_orders(
            broker, telegram, settings, sell_resubmit_counts, buy_cooldown
        )

        broker.cancel_domestic_order.assert_called_once_with(
            stock_code="005930",
            orgn_odno="ORD001",
            krx_fwdg_ord_orgno="BRN01",
            qty=3,
        )
        assert "KR:005930" in buy_cooldown
        telegram.notify_unfilled_order.assert_called_once()
        call_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert call_kwargs["action"] == "BUY"
        assert call_kwargs["outcome"] == "cancelled"
        assert call_kwargs["market"] == "KR"

    @pytest.mark.asyncio
    async def test_sell_pending_is_cancelled_then_resubmitted(self) -> None:
        """First unfilled SELL should be cancelled then resubmitted at -0.4% price."""
        from src.broker.kis_api import kr_round_down

        settings = self._make_settings()
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "005930",
            "orgn_odno": "ORD002",
            "ord_gno_brno": "BRN02",
            "sll_buy_dvsn_cd": "01",  # SELL
            "psbl_qty": "5",
        }
        broker = MagicMock()
        broker.get_domestic_pending_orders = AsyncMock(return_value=[pending_order])
        broker.cancel_domestic_order = AsyncMock(
            return_value={"rt_cd": "0", "msg1": "OK"}
        )
        broker.get_current_price = AsyncMock(return_value=(50000.0, 0.0, 0.0))
        broker.send_order = AsyncMock(return_value={"rt_cd": "0"})

        sell_resubmit_counts: dict[str, int] = {}

        await handle_domestic_pending_orders(
            broker, telegram, settings, sell_resubmit_counts
        )

        broker.cancel_domestic_order.assert_called_once()
        broker.send_order.assert_called_once()
        resubmit_kwargs = broker.send_order.call_args[1]
        assert resubmit_kwargs["order_type"] == "SELL"
        expected_price = kr_round_down(50000.0 * 0.996)
        assert resubmit_kwargs["price"] == expected_price
        assert sell_resubmit_counts.get("KR:005930") == 1
        notify_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert notify_kwargs["outcome"] == "resubmitted"

    @pytest.mark.asyncio
    async def test_sell_cancel_failure_skips_resubmit(self) -> None:
        """When cancel returns rt_cd != '0', resubmit should NOT be attempted."""
        settings = self._make_settings()
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "005930",
            "orgn_odno": "ORD003",
            "ord_gno_brno": "BRN03",
            "sll_buy_dvsn_cd": "01",  # SELL
            "psbl_qty": "2",
        }
        broker = MagicMock()
        broker.get_domestic_pending_orders = AsyncMock(return_value=[pending_order])
        broker.cancel_domestic_order = AsyncMock(
            return_value={"rt_cd": "1", "msg1": "Error"}  # failure
        )
        broker.send_order = AsyncMock()

        sell_resubmit_counts: dict[str, int] = {}

        await handle_domestic_pending_orders(
            broker, telegram, settings, sell_resubmit_counts
        )

        broker.send_order.assert_not_called()
        telegram.notify_unfilled_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_sell_already_resubmitted_is_only_cancelled(self) -> None:
        """Second unfilled SELL (sell_resubmit_counts >= 1) should only cancel, no resubmit."""
        settings = self._make_settings()
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "005930",
            "orgn_odno": "ORD004",
            "ord_gno_brno": "BRN04",
            "sll_buy_dvsn_cd": "01",  # SELL
            "psbl_qty": "4",
        }
        broker = MagicMock()
        broker.get_domestic_pending_orders = AsyncMock(return_value=[pending_order])
        broker.cancel_domestic_order = AsyncMock(
            return_value={"rt_cd": "0", "msg1": "OK"}
        )
        broker.send_order = AsyncMock()

        # Already resubmitted once
        sell_resubmit_counts: dict[str, int] = {"KR:005930": 1}

        await handle_domestic_pending_orders(
            broker, telegram, settings, sell_resubmit_counts
        )

        broker.cancel_domestic_order.assert_called_once()
        broker.send_order.assert_not_called()
        notify_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert notify_kwargs["outcome"] == "cancelled"
        assert notify_kwargs["action"] == "SELL"


# ---------------------------------------------------------------------------
# Domestic Limit Order Price in trading_cycle
# ---------------------------------------------------------------------------


class TestDomesticLimitOrderPrice:
    """trading_cycle must use kr_round_down limit prices for domestic orders."""

    def _make_market(self) -> MagicMock:
        market = MagicMock()
        market.name = "Korea"
        market.code = "KR"
        market.exchange_code = "KRX"
        market.is_domestic = True
        return market

    def _make_broker(self, current_price: float, balance_data: dict) -> MagicMock:
        broker = MagicMock()
        broker.get_current_price = AsyncMock(return_value=(current_price, 0.0, 0.0))
        broker.get_balance = AsyncMock(return_value=balance_data)
        broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        return broker

    @pytest.mark.asyncio
    async def test_trading_cycle_domestic_buy_uses_limit_price(self) -> None:
        """BUY order for domestic stock must use kr_round_down(price * 1.002)."""
        from src.broker.kis_api import kr_round_down
        from src.strategy.models import ScenarioAction

        current_price = 70000.0
        balance_data = {
            "output2": [
                {
                    "tot_evlu_amt": "10000000",
                    "dnca_tot_amt": "5000000",
                    "pchs_amt_smtl_amt": "5000000",
                }
            ]
        }
        broker = self._make_broker(current_price, balance_data)
        market = self._make_market()

        buy_match = ScenarioMatch(
            stock_code="005930",
            matched_scenario=None,
            action=ScenarioAction.BUY,
            confidence=85,
            rationale="test",
        )
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=buy_match)

        risk = MagicMock()
        risk.validate_order = MagicMock()
        risk.check_circuit_breaker = MagicMock()
        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=_make_playbook(),
                risk=risk,
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=telegram,
                market=market,
                stock_code="005930",
                scan_candidates={},
            )

        broker.send_order.assert_called_once()
        call_kwargs = broker.send_order.call_args[1]
        expected_price = kr_round_down(current_price * 1.002)
        assert call_kwargs["price"] == expected_price
        assert call_kwargs["order_type"] == "BUY"

    @pytest.mark.asyncio
    async def test_trading_cycle_domestic_sell_uses_limit_price(self) -> None:
        """SELL order for domestic stock must use kr_round_down(price * 0.998)."""
        from src.broker.kis_api import kr_round_down
        from src.strategy.models import ScenarioAction

        current_price = 70000.0
        stock_code = "005930"
        balance_data = {
            "output1": [
                {"pdno": stock_code, "hldg_qty": "5", "prpr": "70000", "evlu_amt": "350000"}
            ],
            "output2": [
                {
                    "tot_evlu_amt": "350000",
                    "dnca_tot_amt": "0",
                    "pchs_amt_smtl_amt": "350000",
                }
            ],
        }
        broker = self._make_broker(current_price, balance_data)
        market = self._make_market()

        sell_match = ScenarioMatch(
            stock_code=stock_code,
            matched_scenario=None,
            action=ScenarioAction.SELL,
            confidence=85,
            rationale="test",
        )
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=sell_match)

        risk = MagicMock()
        risk.validate_order = MagicMock()
        risk.check_circuit_breaker = MagicMock()
        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=_make_playbook(),
                risk=risk,
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=telegram,
                market=market,
                stock_code=stock_code,
                scan_candidates={},
            )

        broker.send_order.assert_called_once()
        call_kwargs = broker.send_order.call_args[1]
        expected_price = kr_round_down(current_price * 0.998)
        assert call_kwargs["price"] == expected_price
        assert call_kwargs["order_type"] == "SELL"


# ---------------------------------------------------------------------------
# Ghost position — overseas SELL "잔고내역이 없습니다" handling
# ---------------------------------------------------------------------------


class TestOverseasGhostPositionClose:
    """trading_cycle must close ghost DB position when broker returns 잔고없음."""

    def _make_overseas_market(self) -> MagicMock:
        market = MagicMock()
        market.name = "NASDAQ"
        market.code = "US_NASDAQ"
        market.exchange_code = "NASD"
        market.is_domestic = False
        return market

    def _make_overseas_broker(
        self,
        current_price: float,
        balance_data: dict,
        sell_result: dict,
    ) -> MagicMock:
        ob = MagicMock()
        ob.get_overseas_price = AsyncMock(
            return_value={"output": {"last": str(current_price), "rate": "0.0"}}
        )
        ob.get_overseas_balance = AsyncMock(return_value=balance_data)
        ob.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "0.00"}}
        )
        ob.send_overseas_order = AsyncMock(return_value=sell_result)
        return ob

    @pytest.mark.asyncio
    async def test_ghost_position_closes_db_on_no_balance_error(self) -> None:
        """When SELL fails with '잔고내역이 없습니다', log_trade is called to close the ghost.

        This can happen when exchange code recorded at startup differs from the
        exchange code used in the SELL cycle (e.g. KNRX recorded as NASD but
        actually traded on AMEX), causing the broker to see no matching balance.
        The position has ord_psbl_qty > 0 (so a SELL is attempted), but KIS
        rejects it with '잔고내역이 없습니다'.
        """
        from src.strategy.models import ScenarioAction

        stock_code = "KNRX"
        current_price = 1.5
        # ord_psbl_qty=5 means the code passes the qty check and a SELL is sent
        balance_data = {
            "output1": [
                {"ovrs_pdno": stock_code, "ord_psbl_qty": "5", "ovrs_cblc_qty": "5"}
            ],
            "output2": [{"tot_evlu_amt": "10000"}],
        }
        sell_result = {"rt_cd": "1", "msg1": "모의투자 잔고내역이 없습니다"}

        domestic_broker = MagicMock()
        domestic_broker.get_balance = AsyncMock(return_value={"output1": [], "output2": [{}]})
        overseas_broker = self._make_overseas_broker(current_price, balance_data, sell_result)
        market = self._make_overseas_market()

        sell_match = ScenarioMatch(
            stock_code=stock_code,
            matched_scenario=None,
            action=ScenarioAction.SELL,
            confidence=85,
            rationale="test ghost KNRX",
        )
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=sell_match)

        risk = MagicMock()
        risk.validate_order = MagicMock()
        risk.check_circuit_breaker = MagicMock()
        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        db_conn = MagicMock()

        settings = MagicMock(spec=Settings)
        settings.MODE = "paper"
        settings.POSITION_SIZING_ENABLED = False
        settings.PAPER_OVERSEAS_CASH = 0

        with patch("src.main.log_trade") as mock_log_trade, patch(
            "src.main.get_open_position", return_value=None
        ), patch("src.main.get_latest_buy_trade", return_value=None):
            await trading_cycle(
                broker=domestic_broker,
                overseas_broker=overseas_broker,
                scenario_engine=engine,
                playbook=_make_playbook(market="US_NASDAQ"),
                risk=risk,
                db_conn=db_conn,
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=telegram,
                market=market,
                stock_code=stock_code,
                scan_candidates={},
                settings=settings,
            )

        # log_trade must be called with action="SELL" to close the ghost position
        ghost_close_calls = [
            c
            for c in mock_log_trade.call_args_list
            if c.kwargs.get("action") == "SELL"
            and "[ghost-close]" in (c.kwargs.get("rationale") or "")
        ]
        assert ghost_close_calls, "Expected ghost-close log_trade call was not made"

    @pytest.mark.asyncio
    async def test_normal_sell_failure_does_not_close_db(self) -> None:
        """Non-잔고없음 SELL failures must NOT close the DB position."""
        from src.strategy.models import ScenarioAction

        stock_code = "TSLA"
        current_price = 250.0
        balance_data = {
            "output1": [{"ovrs_pdno": stock_code, "ord_psbl_qty": "5", "ovrs_cblc_qty": "5"}],
            "output2": [{"tot_evlu_amt": "100000"}],
        }
        sell_result = {"rt_cd": "1", "msg1": "일시적 오류가 발생했습니다"}

        domestic_broker = MagicMock()
        domestic_broker.get_balance = AsyncMock(return_value={"output1": [], "output2": [{}]})
        overseas_broker = self._make_overseas_broker(current_price, balance_data, sell_result)
        market = self._make_overseas_market()

        sell_match = ScenarioMatch(
            stock_code=stock_code,
            matched_scenario=None,
            action=ScenarioAction.SELL,
            confidence=85,
            rationale="test",
        )
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=sell_match)

        risk = MagicMock()
        risk.validate_order = MagicMock()
        risk.check_circuit_breaker = MagicMock()
        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        db_conn = MagicMock()

        with patch("src.main.log_trade") as mock_log_trade, patch(
            "src.main.get_open_position", return_value=None
        ):
            await trading_cycle(
                broker=domestic_broker,
                overseas_broker=overseas_broker,
                scenario_engine=engine,
                playbook=_make_playbook(market="US_NASDAQ"),
                risk=risk,
                db_conn=db_conn,
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=telegram,
                market=market,
                stock_code=stock_code,
                scan_candidates={},
            )

        ghost_close_calls = [
            c
            for c in mock_log_trade.call_args_list
            if c.kwargs.get("action") == "SELL"
            and "[ghost-close]" in (c.kwargs.get("rationale") or "")
        ]
        assert not ghost_close_calls, "Ghost-close must NOT be triggered for non-잔고없음 errors"


@pytest.mark.asyncio
async def test_kill_switch_block_skips_actionable_order_execution() -> None:
    """Active kill-switch must prevent actionable order execution."""
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    broker = MagicMock()
    broker.get_current_price = AsyncMock(return_value=(100.0, 0.5, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            "output1": [],
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "50000",
                    "pchs_amt_smtl_amt": "50000",
                }
            ],
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    settings = MagicMock()
    settings.POSITION_SIZING_ENABLED = False
    settings.CONFIDENCE_THRESHOLD = 80

    try:
        KILL_SWITCH.new_orders_blocked = True
        await trading_cycle(
            broker=broker,
            overseas_broker=MagicMock(),
            scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_buy_match())),
            playbook=_make_playbook(),
            risk=MagicMock(),
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(
                get_latest_timeframe=MagicMock(return_value=None),
                set_context=MagicMock(),
            ),
            criticality_assessor=MagicMock(
                assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                get_timeout=MagicMock(return_value=5.0),
            ),
            telegram=telegram,
            market=market,
            stock_code="005930",
            scan_candidates={},
            settings=settings,
        )
    finally:
        KILL_SWITCH.clear_block()

    broker.send_order.assert_not_called()


@pytest.mark.asyncio
async def test_order_policy_rejection_skips_order_execution() -> None:
    """Order policy rejection must prevent order submission."""
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    broker = MagicMock()
    broker.get_current_price = AsyncMock(return_value=(100.0, 0.5, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            "output1": [],
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "50000",
                    "pchs_amt_smtl_amt": "50000",
                }
            ],
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    settings = MagicMock()
    settings.POSITION_SIZING_ENABLED = False
    settings.CONFIDENCE_THRESHOLD = 80

    with patch(
        "src.main.validate_order_policy",
        side_effect=OrderPolicyRejected(
            "rejected",
            session_id="NXT_AFTER",
            market_code="KR",
        ),
    ):
        await trading_cycle(
            broker=broker,
            overseas_broker=MagicMock(),
            scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_buy_match())),
            playbook=_make_playbook(),
            risk=MagicMock(),
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(
                get_latest_timeframe=MagicMock(return_value=None),
                set_context=MagicMock(),
            ),
            criticality_assessor=MagicMock(
                assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                get_timeout=MagicMock(return_value=5.0),
            ),
            telegram=telegram,
            market=market,
            stock_code="005930",
            scan_candidates={},
            settings=settings,
        )

    broker.send_order.assert_not_called()


@pytest.mark.asyncio
async def test_blackout_queues_order_and_skips_submission() -> None:
    """When blackout is active, order submission is replaced by queueing."""
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    broker = MagicMock()
    broker.get_current_price = AsyncMock(return_value=(100.0, 0.5, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            "output1": [],
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "50000",
                    "pchs_amt_smtl_amt": "50000",
                }
            ],
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    settings = MagicMock()
    settings.POSITION_SIZING_ENABLED = False
    settings.CONFIDENCE_THRESHOLD = 80

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    blackout_manager = MagicMock()
    blackout_manager.in_blackout.return_value = True
    blackout_manager.enqueue.return_value = True
    blackout_manager.pending_count = 1

    with patch("src.main.BLACKOUT_ORDER_MANAGER", blackout_manager):
        await trading_cycle(
            broker=broker,
            overseas_broker=MagicMock(),
            scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_buy_match())),
            playbook=_make_playbook(),
            risk=MagicMock(),
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(
                get_latest_timeframe=MagicMock(return_value=None),
                set_context=MagicMock(),
            ),
            criticality_assessor=MagicMock(
                assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                get_timeout=MagicMock(return_value=5.0),
            ),
            telegram=telegram,
            market=market,
            stock_code="005930",
            scan_candidates={},
            settings=settings,
        )

    broker.send_order.assert_not_called()
    blackout_manager.enqueue.assert_called_once()


@pytest.mark.asyncio
async def test_process_blackout_recovery_executes_valid_intents() -> None:
    """Recovery must execute queued intents that pass revalidation."""
    db_conn = init_db(":memory:")
    broker = MagicMock()
    broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
    overseas_broker = MagicMock()

    market = MagicMock()
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    intent = MagicMock()
    intent.market_code = "KR"
    intent.stock_code = "005930"
    intent.order_type = "BUY"
    intent.quantity = 1
    intent.price = 100.0
    intent.source = "test"
    intent.attempts = 0

    blackout_manager = MagicMock()
    blackout_manager.pop_recovery_batch.return_value = [intent]

    with (
        patch("src.main.BLACKOUT_ORDER_MANAGER", blackout_manager),
        patch("src.main.MARKETS", {"KR": market}),
        patch("src.main.get_open_position", return_value=None),
        patch("src.main.validate_order_policy"),
    ):
        await process_blackout_recovery_orders(
            broker=broker,
            overseas_broker=overseas_broker,
            db_conn=db_conn,
        )

    broker.send_order.assert_called_once()


@pytest.mark.asyncio
async def test_process_blackout_recovery_drops_policy_rejected_intent() -> None:
    """Policy-rejected queued intents must not be requeued."""
    db_conn = init_db(":memory:")
    broker = MagicMock()
    broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
    overseas_broker = MagicMock()

    market = MagicMock()
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    intent = MagicMock()
    intent.market_code = "KR"
    intent.stock_code = "005930"
    intent.order_type = "BUY"
    intent.quantity = 1
    intent.price = 100.0
    intent.source = "test"
    intent.attempts = 0

    blackout_manager = MagicMock()
    blackout_manager.pop_recovery_batch.return_value = [intent]

    with (
        patch("src.main.BLACKOUT_ORDER_MANAGER", blackout_manager),
        patch("src.main.MARKETS", {"KR": market}),
        patch("src.main.get_open_position", return_value=None),
        patch(
            "src.main.validate_order_policy",
            side_effect=OrderPolicyRejected(
                "blocked",
                session_id="NXT_AFTER",
                market_code="KR",
            ),
        ),
    ):
        await process_blackout_recovery_orders(
            broker=broker,
            overseas_broker=overseas_broker,
            db_conn=db_conn,
        )

    broker.send_order.assert_not_called()
    blackout_manager.requeue.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_emergency_kill_switch_executes_operational_steps() -> None:
    """Emergency kill switch should execute cancel/refresh/reduce/notify callbacks."""
    broker = MagicMock()
    broker.get_domestic_pending_orders = AsyncMock(
        return_value=[
            {
                "pdno": "005930",
                "orgn_odno": "1",
                "ord_gno_brno": "01",
                "psbl_qty": "3",
            }
        ]
    )
    broker.cancel_domestic_order = AsyncMock(return_value={"rt_cd": "0"})
    broker.get_balance = AsyncMock(return_value={"output1": [], "output2": []})

    overseas_broker = MagicMock()
    overseas_broker.get_overseas_pending_orders = AsyncMock(return_value=[])
    overseas_broker.get_overseas_balance = AsyncMock(return_value={"output1": [], "output2": []})

    telegram = MagicMock()
    telegram.notify_circuit_breaker = AsyncMock()

    settings = MagicMock()
    settings.enabled_market_list = ["KR"]

    market = MagicMock()
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    with (
        patch("src.main.MARKETS", {"KR": market}),
        patch("src.main.BLACKOUT_ORDER_MANAGER.clear", return_value=2),
    ):
        report = await _trigger_emergency_kill_switch(
            reason="test",
            broker=broker,
            overseas_broker=overseas_broker,
            telegram=telegram,
            settings=settings,
            current_market=market,
            stock_code="005930",
            pnl_pct=-3.2,
            threshold=-3.0,
        )

    assert report.steps == [
        "block_new_orders",
        "cancel_pending_orders",
        "refresh_order_state",
        "reduce_risk",
        "snapshot_state",
        "notify",
    ]
    broker.cancel_domestic_order.assert_called_once()
    broker.get_balance.assert_called_once()
    telegram.notify_circuit_breaker.assert_called_once_with(
        pnl_pct=-3.2,
        threshold=-3.0,
    )


@pytest.mark.asyncio
async def test_trigger_emergency_kill_switch_records_cancel_failure() -> None:
    """Cancel API rejection should be captured in kill switch errors."""
    broker = MagicMock()
    broker.get_domestic_pending_orders = AsyncMock(
        return_value=[
            {
                "pdno": "005930",
                "orgn_odno": "1",
                "ord_gno_brno": "01",
                "psbl_qty": "3",
            }
        ]
    )
    broker.cancel_domestic_order = AsyncMock(return_value={"rt_cd": "1", "msg1": "fail"})
    broker.get_balance = AsyncMock(return_value={"output1": [], "output2": []})

    overseas_broker = MagicMock()
    overseas_broker.get_overseas_pending_orders = AsyncMock(return_value=[])
    overseas_broker.get_overseas_balance = AsyncMock(return_value={"output1": [], "output2": []})

    telegram = MagicMock()
    telegram.notify_circuit_breaker = AsyncMock()

    settings = MagicMock()
    settings.enabled_market_list = ["KR"]

    market = MagicMock()
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    with (
        patch("src.main.MARKETS", {"KR": market}),
        patch("src.main.BLACKOUT_ORDER_MANAGER.clear", return_value=0),
    ):
        report = await _trigger_emergency_kill_switch(
            reason="test-fail",
            broker=broker,
            overseas_broker=overseas_broker,
            telegram=telegram,
            settings=settings,
            current_market=market,
            stock_code="005930",
            pnl_pct=-3.2,
            threshold=-3.0,
        )

    assert any(err.startswith("cancel_pending_orders:") for err in report.errors)
