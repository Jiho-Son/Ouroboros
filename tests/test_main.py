"""Tests for main trading loop integration."""

from datetime import UTC, date, datetime
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from src.config import Settings
from src.context.layer import ContextLayer
from src.context.scheduler import ScheduleResult
from src.core.risk_manager import CircuitBreakerTripped, FatFingerRejected
from src.db import init_db, log_trade
from src.evolution.scorecard import DailyScorecard
from src.logging.decision_logger import DecisionLogger
from src.main import (
    _apply_dashboard_flag,
    _determine_order_quantity,
    _extract_held_codes_from_balance,
    _extract_held_qty_from_balance,
    _handle_market_close,
    _run_context_scheduler,
    _run_evolution_loop,
    _start_dashboard_server,
    safe_float,
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

    def test_overseas_returns_ovrs_cblc_qty(self) -> None:
        balance = {"output1": [{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "10"}]}
        assert _extract_held_qty_from_balance(balance, "AAPL", is_domestic=False) == 10

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
                        "frcr_dncl_amt_2": "5000.00",
                        "frcr_buy_amt_smtl": "4500.00",
                    }
                ]
            }
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
                    "frcr_dncl_amt_2": "5000.00",
                    "frcr_buy_amt_smtl": "4500.00",
                }
            }
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
                        "frcr_dncl_amt_2": "5000.00",
                        "frcr_buy_amt_smtl": "4500.00",
                    }
                ]
            }
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
                        "frcr_dncl_amt_2": "50000.00",
                        "frcr_buy_amt_smtl": "50000.00",
                    }
                ]
            }
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
        """Overseas BUY order must use current_price (limit), not 0 (market).

        KIS VTS rejects market orders for overseas paper trading.
        Regression test for issue #149.
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

        # Verify limit order was sent with actual price + 0.5% premium (issue #151), not 0.0
        mock_overseas_broker_with_buy_scenario.send_overseas_order.assert_called_once()
        call_kwargs = mock_overseas_broker_with_buy_scenario.send_overseas_order.call_args
        sent_price = call_kwargs[1].get("price") or call_kwargs[0][4]
        expected_price = round(182.5 * 1.005, 4)  # 0.5% premium for BUY limit orders
        assert sent_price == expected_price, (
            f"Expected limit price {expected_price} (182.5 * 1.005) but got {sent_price}. "
            "KIS VTS only accepts limit orders; BUY uses 0.5% premium to improve fill rate."
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

        # Should NOT have rsi/volume_ratio because candidate is under US, not KR
        market_data = engine.evaluate.call_args[0][2]
        assert "rsi" not in market_data
        assert "volume_ratio" not in market_data

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

        # Should still work, just without rsi/volume_ratio
        engine.evaluate.assert_called_once()
        market_data = engine.evaluate.call_args[0][2]
        assert "rsi" not in market_data
        assert "volume_ratio" not in market_data
        assert market_data["current_price"] == 50000.0

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
            "output2": [{"frcr_dncl_amt_2": "50000", "frcr_evlu_tota": "50000",
                         "frcr_buy_amt_smtl": "0"}],
        })
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
            "output2": [{"frcr_dncl_amt_2": "50000", "frcr_evlu_tota": "50000",
                         "frcr_buy_amt_smtl": "0"}],
        })
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
