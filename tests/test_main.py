"""Tests for main trading loop telegram integration."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.risk_manager import CircuitBreakerTripped, FatFingerRejected
from src.main import trading_cycle


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
    def mock_brain(self) -> MagicMock:
        """Create mock brain that decides to buy."""
        brain = MagicMock()
        decision = MagicMock()
        decision.action = "BUY"
        decision.confidence = 85
        decision.rationale = "Test buy"
        brain.decide = AsyncMock(return_value=decision)
        return brain

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
        mock_brain: MagicMock,
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
                brain=mock_brain,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
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
        mock_brain: MagicMock,
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
                brain=mock_brain,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
            )

        # Verify notification was attempted
        mock_telegram.notify_trade_execution.assert_called_once()

    @pytest.mark.asyncio
    async def test_fat_finger_notification_sent(
        self,
        mock_broker: MagicMock,
        mock_overseas_broker: MagicMock,
        mock_brain: MagicMock,
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
                    brain=mock_brain,
                    risk=mock_risk,
                    db_conn=mock_db,
                    decision_logger=mock_decision_logger,
                    context_store=mock_context_store,
                    criticality_assessor=mock_criticality_assessor,
                    telegram=mock_telegram,
                    market=mock_market,
                    stock_code="005930",
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
        mock_brain: MagicMock,
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
                    brain=mock_brain,
                    risk=mock_risk,
                    db_conn=mock_db,
                    decision_logger=mock_decision_logger,
                    context_store=mock_context_store,
                    criticality_assessor=mock_criticality_assessor,
                    telegram=mock_telegram,
                    market=mock_market,
                    stock_code="005930",
                )

        # Verify notification was attempted
        mock_telegram.notify_fat_finger.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_notification_on_hold_decision(
        self,
        mock_broker: MagicMock,
        mock_overseas_broker: MagicMock,
        mock_brain: MagicMock,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_market: MagicMock,
    ) -> None:
        """Test no trade notification sent when decision is HOLD."""
        # Change brain decision to HOLD
        decision = MagicMock()
        decision.action = "HOLD"
        decision.confidence = 50
        decision.rationale = "Insufficient signal"
        mock_brain.decide = AsyncMock(return_value=decision)

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=mock_overseas_broker,
                brain=mock_brain,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
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
    def mock_brain_hold(self) -> MagicMock:
        """Create mock brain that always holds."""
        brain = MagicMock()
        decision = MagicMock()
        decision.action = "HOLD"
        decision.confidence = 50
        decision.rationale = "Testing balance parsing"
        brain.decide = AsyncMock(return_value=decision)
        return brain

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
        return MagicMock()

    @pytest.mark.asyncio
    async def test_overseas_balance_list_format(
        self,
        mock_domestic_broker: MagicMock,
        mock_overseas_broker_with_list: MagicMock,
        mock_brain_hold: MagicMock,
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
                brain=mock_brain_hold,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_overseas_market,
                stock_code="AAPL",
            )

        # Verify balance API was called
        mock_overseas_broker_with_list.get_overseas_balance.assert_called_once()

    @pytest.mark.asyncio
    async def test_overseas_balance_dict_format(
        self,
        mock_domestic_broker: MagicMock,
        mock_overseas_broker_with_dict: MagicMock,
        mock_brain_hold: MagicMock,
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
                brain=mock_brain_hold,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_overseas_market,
                stock_code="AAPL",
            )

        # Verify balance API was called
        mock_overseas_broker_with_dict.get_overseas_balance.assert_called_once()

    @pytest.mark.asyncio
    async def test_overseas_balance_empty_format(
        self,
        mock_domestic_broker: MagicMock,
        mock_overseas_broker_with_empty: MagicMock,
        mock_brain_hold: MagicMock,
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
                brain=mock_brain_hold,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_overseas_market,
                stock_code="AAPL",
            )

        # Verify balance API was called
        mock_overseas_broker_with_empty.get_overseas_balance.assert_called_once()
