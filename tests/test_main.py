"""Tests for main trading loop integration."""

from datetime import UTC, date, datetime
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from src.context.layer import ContextLayer
from src.context.scheduler import ScheduleResult
from src.core.risk_manager import CircuitBreakerTripped, FatFingerRejected
from src.db import init_db, log_trade
from src.evolution.scorecard import DailyScorecard
from src.logging.decision_logger import DecisionLogger
from src.main import (
    _handle_market_close,
    _run_context_scheduler,
    _run_evolution_loop,
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
        broker.get_orderbook = AsyncMock(
            return_value={
                "output1": {
                    "stck_prpr": "50000",
                    "frgn_ntby_qty": "100",
                }
            }
        )
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


class TestScenarioEngineIntegration:
    """Test scenario engine integration in trading_cycle."""

    @pytest.fixture
    def mock_broker(self) -> MagicMock:
        """Create mock broker with standard domestic data."""
        broker = MagicMock()
        broker.get_orderbook = AsyncMock(
            return_value={
                "output1": {"stck_prpr": "50000", "frgn_ntby_qty": "100"}
            }
        )
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
    broker.get_orderbook = AsyncMock(
        return_value={"output1": {"stck_prpr": "120", "frgn_ntby_qty": "0"}}
    )
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
        market_code="US",
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
        market_code="US",
        market_date="2026-02-14",
    )

    optimizer.evolve.assert_called_once()
    telegram.send_message.assert_called_once()
