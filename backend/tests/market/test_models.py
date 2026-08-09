"""Tests for PriceUpdate, MarketEvent and normalize_ticker."""

import pytest

from app.market.models import MarketEvent, PriceUpdate, normalize_ticker


class TestPriceUpdate:
    """Unit tests for the PriceUpdate model."""

    def test_price_update_creation(self):
        """Test basic PriceUpdate creation."""
        update = PriceUpdate(
            ticker="AAPL", price=190.50, previous_price=190.00, timestamp=1234567890.0
        )
        assert update.ticker == "AAPL"
        assert update.price == 190.50
        assert update.previous_price == 190.00
        assert update.timestamp == 1234567890.0

    def test_change_calculation(self):
        """Test price change calculation."""
        update = PriceUpdate(
            ticker="AAPL", price=190.50, previous_price=190.00, timestamp=1234567890.0
        )
        assert update.change == 0.50

    def test_change_negative(self):
        """Test negative price change."""
        update = PriceUpdate(
            ticker="AAPL", price=189.50, previous_price=190.00, timestamp=1234567890.0
        )
        assert update.change == -0.50

    def test_change_percent_up(self):
        """Test percentage change calculation (up)."""
        update = PriceUpdate(
            ticker="AAPL", price=190.00, previous_price=100.00, timestamp=1234567890.0
        )
        assert update.change_percent == 90.0

    def test_change_percent_down(self):
        """Test percentage change calculation (down)."""
        update = PriceUpdate(
            ticker="AAPL", price=100.00, previous_price=200.00, timestamp=1234567890.0
        )
        assert update.change_percent == -50.0

    def test_change_percent_zero_previous(self):
        """Test percentage change with zero previous price."""
        update = PriceUpdate(
            ticker="AAPL", price=100.00, previous_price=0.00, timestamp=1234567890.0
        )
        assert update.change_percent == 0.0

    def test_direction_up(self):
        """Test direction calculation (up)."""
        update = PriceUpdate(
            ticker="AAPL", price=191.00, previous_price=190.00, timestamp=1234567890.0
        )
        assert update.direction == "up"

    def test_direction_down(self):
        """Test direction calculation (down)."""
        update = PriceUpdate(
            ticker="AAPL", price=189.00, previous_price=190.00, timestamp=1234567890.0
        )
        assert update.direction == "down"

    def test_direction_flat(self):
        """Test direction calculation (flat)."""
        update = PriceUpdate(
            ticker="AAPL", price=190.00, previous_price=190.00, timestamp=1234567890.0
        )
        assert update.direction == "flat"

    def test_to_dict(self):
        """Test serialization to dictionary."""
        update = PriceUpdate(
            ticker="AAPL", price=190.50, previous_price=190.00, timestamp=1234567890.0
        )
        result = update.to_dict()

        assert result["ticker"] == "AAPL"
        assert result["price"] == 190.50
        assert result["previous_price"] == 190.00
        assert result["timestamp"] == 1234567890.0
        assert result["change"] == 0.50
        assert result["change_percent"] == 0.2632  # (0.50 / 190.00) * 100
        assert result["direction"] == "up"

    def test_immutability(self):
        """Test that PriceUpdate is immutable."""
        update = PriceUpdate(
            ticker="AAPL", price=190.50, previous_price=190.00, timestamp=1234567890.0
        )

        with pytest.raises(AttributeError):
            update.price = 200.00  # Should raise error


class TestDayChange:
    """Session-over-session change — the watchlist's 'daily change %' column.

    Distinct from tick-over-tick `change`, which exists only to flash a cell.
    Conflating them ships a watchlist showing +-0.004%.
    """

    def test_day_change_from_previous_close(self):
        update = PriceUpdate(
            ticker="AAPL", price=195.00, previous_price=194.90, previous_close=190.00
        )
        assert update.day_change == 5.00
        assert update.day_change_percent == 2.6316

    def test_day_change_negative(self):
        update = PriceUpdate(
            ticker="AAPL", price=185.00, previous_price=185.10, previous_close=190.00
        )
        assert update.day_change == -5.00
        assert update.day_change_percent == -2.6316

    def test_day_change_is_none_without_previous_close(self):
        """Consumers render an em dash rather than inventing a baseline."""
        update = PriceUpdate(ticker="AAPL", price=195.00, previous_price=194.90)
        assert update.day_change is None
        assert update.day_change_percent is None

    def test_day_change_differs_from_tick_change(self):
        update = PriceUpdate(
            ticker="AAPL", price=195.00, previous_price=194.99, previous_close=190.00
        )
        assert update.change == 0.01  # tick: near zero
        assert update.day_change == 5.00  # session: the real story

    def test_to_dict_includes_day_fields(self):
        update = PriceUpdate(
            ticker="AAPL",
            price=195.00,
            previous_price=194.90,
            timestamp=1234567890.0,
            previous_close=190.00,
        )
        result = update.to_dict()
        assert result["previous_close"] == 190.00
        assert result["day_change"] == 5.00
        assert result["day_change_percent"] == 2.6316


class TestNormalizeTicker:
    @pytest.mark.parametrize(
        "raw,expected",
        [("aapl", "AAPL"), ("  AAPL  ", "AAPL"), (" googl ", "GOOGL"), ("MSFT", "MSFT")],
    )
    def test_normalisation(self, raw, expected):
        assert normalize_ticker(raw) == expected

    def test_is_idempotent(self):
        assert normalize_ticker(normalize_ticker(" tsla ")) == "TSLA"


class TestMarketEvent:
    def test_creation_and_serialisation(self):
        event = MarketEvent(
            ticker="TSLA", magnitude_percent=-3.4, price=241.50, timestamp=1234567890.0
        )
        assert event.to_dict() == {
            "ticker": "TSLA",
            "magnitude_percent": -3.4,
            "price": 241.50,
            "timestamp": 1234567890.0,
        }

    def test_timestamp_defaults_to_now(self):
        assert MarketEvent(ticker="AAPL", magnitude_percent=2.0, price=190.0).timestamp > 0

    def test_immutability(self):
        event = MarketEvent(ticker="TSLA", magnitude_percent=-3.4, price=241.50)
        with pytest.raises(AttributeError):
            event.price = 250.0
