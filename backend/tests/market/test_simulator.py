"""Tests for GBMSimulator."""

import math
import random
from unittest import mock

import numpy as np
import pytest

from app.market.seed_prices import SEED_PRICES
from app.market.simulator import DEFAULT_EVENT_PROBABILITY, GBMSimulator


class _FixedRng:
    """A random.Random stand-in with a known magnitude and sign, so a shock's
    exact arithmetic can be asserted."""

    def __init__(self, magnitude: float, sign: float) -> None:
        self.magnitude = magnitude
        self.sign = sign

    def uniform(self, a: float, b: float) -> float:
        return self.magnitude

    def choice(self, seq):
        return self.sign

    def random(self) -> float:
        return 1.0  # never below any probability, so step() fires no shocks


class TestGBMSimulator:
    """Unit tests for the GBM price simulator."""

    def test_step_returns_all_tickers(self):
        """Test that step() returns prices for all tickers."""
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        result = sim.step()
        assert set(result.keys()) == {"AAPL", "GOOGL"}

    def test_prices_are_positive(self):
        """GBM prices can never go negative (exp() is always positive)."""
        sim = GBMSimulator(tickers=["AAPL"])
        for _ in range(10_000):
            prices = sim.step()
            assert prices["AAPL"] > 0

    def test_initial_prices_match_seeds(self):
        """Test that initial prices match seed prices."""
        sim = GBMSimulator(tickers=["AAPL"])
        # Before any step, price should be the seed price
        assert sim.get_price("AAPL") == SEED_PRICES["AAPL"]

    def test_add_ticker(self):
        """Test adding a ticker dynamically."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.add_ticker("TSLA")
        result = sim.step()
        assert "TSLA" in result

    def test_remove_ticker(self):
        """Test removing a ticker."""
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        sim.remove_ticker("GOOGL")
        result = sim.step()
        assert "GOOGL" not in result
        assert "AAPL" in result

    def test_add_duplicate_is_noop(self):
        """Test that adding a duplicate ticker is a no-op."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.add_ticker("AAPL")
        assert len(sim._tickers) == 1

    def test_remove_nonexistent_is_noop(self):
        """Test that removing a non-existent ticker is a no-op."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.remove_ticker("NOPE")  # Should not raise

    def test_unknown_ticker_gets_random_seed_price(self):
        """Test that unknown tickers get random seed prices."""
        sim = GBMSimulator(tickers=["ZZZZ"])
        price = sim.get_price("ZZZZ")
        assert price is not None
        assert 50.0 <= price <= 300.0

    def test_empty_step(self):
        """Test stepping with no tickers."""
        sim = GBMSimulator(tickers=[])
        result = sim.step()
        assert result == {}

    def test_prices_change_over_time(self):
        """After many steps, prices should have drifted from their seeds."""
        sim = GBMSimulator(tickers=["AAPL"])
        initial_price = sim.get_price("AAPL")

        for _ in range(1000):
            sim.step()

        final_price = sim.get_price("AAPL")
        # Price should have changed (extremely unlikely to be exactly the seed)
        assert final_price != initial_price

    def test_cholesky_rebuilds_on_add(self):
        """Test that Cholesky matrix is rebuilt when tickers are added."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim._cholesky is None  # Only 1 ticker, no correlation matrix
        sim.add_ticker("GOOGL")
        assert sim._cholesky is not None  # Now 2 tickers, matrix exists

    def test_cholesky_none_with_one_ticker(self):
        """Test that Cholesky is None with only one ticker."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim._cholesky is None

    def test_get_price_returns_none_for_unknown(self):
        """Test that get_price returns None for unknown ticker."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim.get_price("UNKNOWN") is None

    def test_pairwise_correlation_tech_stocks(self):
        """Test that tech stocks have high correlation."""
        corr = GBMSimulator._pairwise_correlation("AAPL", "GOOGL")
        assert corr == 0.6

    def test_pairwise_correlation_finance_stocks(self):
        """Test that finance stocks have moderate correlation."""
        corr = GBMSimulator._pairwise_correlation("JPM", "V")
        assert corr == 0.5

    def test_pairwise_correlation_tsla(self):
        """Test that TSLA has lower correlation with everything."""
        corr = GBMSimulator._pairwise_correlation("TSLA", "AAPL")
        assert corr == 0.3
        corr = GBMSimulator._pairwise_correlation("TSLA", "JPM")
        assert corr == 0.3

    def test_pairwise_correlation_cross_sector(self):
        """Test cross-sector correlation."""
        corr = GBMSimulator._pairwise_correlation("AAPL", "JPM")
        assert corr == 0.3

    def test_default_dt_is_reasonable(self):
        """Test that default dt is a reasonable small value."""
        assert 0 < GBMSimulator.DEFAULT_DT < 0.0001

    def test_prices_rounded_to_two_decimals(self):
        """Test that prices are rounded to 2 decimal places."""
        sim = GBMSimulator(tickers=["AAPL"])
        result = sim.step()
        price_str = str(result["AAPL"])
        # Check that we have at most 2 decimal places
        if "." in price_str:
            decimal_part = price_str.split(".")[1]
            assert len(decimal_part) <= 2


class TestDeterminism:
    """Injected RNGs, so tests get reproducible paths without seeding the
    process-global RNGs that every other test shares."""

    def test_same_seed_gives_identical_paths(self):
        def run():
            sim = GBMSimulator(
                tickers=["AAPL", "MSFT"],
                rng=np.random.default_rng(42),
                py_rng=random.Random(42),
            )
            return [sim.step() for _ in range(50)]

        assert run() == run()

    def test_different_seeds_diverge(self):
        def run(seed):
            sim = GBMSimulator(
                tickers=["AAPL"], rng=np.random.default_rng(seed), py_rng=random.Random(seed)
            )
            return [sim.step() for _ in range(50)]

        assert run(1) != run(2)

    def test_unknown_ticker_price_is_in_range(self):
        """The AI-adds-a-ticker path."""
        sim = GBMSimulator(tickers=["ZZZZ"], py_rng=random.Random(3))
        assert 50.0 <= sim.get_price("ZZZZ") < 300.0


class TestStatisticalProperties:
    def test_prices_stay_positive_over_many_steps(self):
        """A negative price would corrupt every downstream portfolio figure."""
        sim = GBMSimulator(
            tickers=list(SEED_PRICES), rng=np.random.default_rng(0), py_rng=random.Random(0)
        )
        for _ in range(10_000):
            for price in sim.step().values():
                assert price > 0

    def test_volatility_matches_configuration(self):
        """A dropped sqrt(dt) or Ito term shows up here and nowhere else."""
        sim = GBMSimulator(
            tickers=["AAPL"],
            event_probability=0.0,  # isolate the GBM process from shocks
            rng=np.random.default_rng(11),
            py_rng=random.Random(11),
        )
        prev = sim.get_price("AAPL")
        log_returns = []
        for _ in range(20_000):
            price = sim.step()["AAPL"]
            log_returns.append(math.log(price / prev))
            prev = price

        # Per-tick std should be sigma * sqrt(dt) for sigma = 0.22.
        expected = 0.22 * math.sqrt(GBMSimulator.DEFAULT_DT)
        assert 0.7 * expected < float(np.std(log_returns)) < 1.4 * expected

    def test_sigma_ordering_is_preserved(self):
        """TSLA (0.50) must actually move more than JPM (0.18) — the whole point
        of dropping the shock probability to 2e-5."""

        def realised_std(ticker):
            sim = GBMSimulator(
                tickers=[ticker],
                event_probability=0.0,
                rng=np.random.default_rng(5),
                py_rng=random.Random(5),
            )
            prev = sim.get_price(ticker)
            returns = []
            for _ in range(20_000):
                price = sim.step()[ticker]
                returns.append(math.log(price / prev))
                prev = price
            return float(np.std(returns))

        assert realised_std("TSLA") > 2 * realised_std("JPM")

    def test_correlation_is_actually_applied(self):
        """Without this, dropping the Cholesky multiply passes every other test."""
        sim = GBMSimulator(
            tickers=["AAPL", "MSFT"],
            event_probability=0.0,
            rng=np.random.default_rng(42),
            py_rng=random.Random(42),
        )
        paths = {"AAPL": [], "MSFT": []}
        prev = {t: sim.get_price(t) for t in paths}
        for _ in range(20_000):
            prices = sim.step()
            for t in paths:
                paths[t].append(math.log(prices[t] / prev[t]))
                prev[t] = prices[t]

        rho = float(np.corrcoef(paths["AAPL"], paths["MSFT"])[0, 1])
        assert 0.5 < rho < 0.7  # configured INTRA_TECH_CORR = 0.6

    def test_tsla_stays_weakly_correlated(self):
        sim = GBMSimulator(
            tickers=["AAPL", "TSLA"],
            event_probability=0.0,
            rng=np.random.default_rng(13),
            py_rng=random.Random(13),
        )
        paths = {"AAPL": [], "TSLA": []}
        prev = {t: sim.get_price(t) for t in paths}
        for _ in range(20_000):
            prices = sim.step()
            for t in paths:
                paths[t].append(math.log(prices[t] / prev[t]))
                prev[t] = prices[t]

        rho = float(np.corrcoef(paths["AAPL"], paths["TSLA"])[0, 1])
        assert 0.2 < rho < 0.4  # configured TSLA_CORR = 0.3


class TestShocks:
    def test_shock_rate_matches_configuration(self):
        """Guards against silent recalibration of the shock process."""
        sim = GBMSimulator(
            tickers=["AAPL"],
            event_probability=DEFAULT_EVENT_PROBABILITY,
            rng=np.random.default_rng(7),
            py_rng=random.Random(7),
        )
        for _ in range(46_800):  # one 6.5h session
            sim.step()
        assert 0 <= len(sim.drain_events()) <= 5  # expectation ~1

    def test_shock_applies_the_exponential_not_the_linear_form(self):
        """exp(m) = 1.0408 for m = 0.04, against 1.04 for the linear form. The
        difference is small but it is the whole point: assert the ratio, don't
        derive the magnitude from the result and invert it (that passes under
        either formula)."""
        sim = GBMSimulator(tickers=["AAPL"], py_rng=_FixedRng(magnitude=0.04, sign=1.0))
        start = sim.get_price("AAPL")

        sim._apply_shock("AAPL")

        assert sim.get_price("AAPL") == pytest.approx(start * math.exp(0.04), rel=1e-9)
        assert sim.get_price("AAPL") != pytest.approx(start * 1.04, rel=1e-6)

    def test_shocks_are_symmetric_in_log_space(self):
        """An up-shock followed by an equal down-shock returns to the starting
        price. `*= (1 +- m)` compounds with a positive skew and does not."""
        rng = _FixedRng(magnitude=0.04, sign=1.0)
        sim = GBMSimulator(tickers=["AAPL"], py_rng=rng)
        start = sim.get_price("AAPL")

        sim._apply_shock("AAPL")
        rng.sign = -1.0
        sim._apply_shock("AAPL")

        assert sim.get_price("AAPL") == pytest.approx(start, rel=1e-9)

    def test_shock_produces_a_market_event(self):
        sim = GBMSimulator(tickers=["AAPL"], event_probability=1.0, py_rng=random.Random(1))
        sim.step()
        events = sim.drain_events()
        assert len(events) == 1
        assert events[0].ticker == "AAPL"
        assert 2.0 <= abs(events[0].magnitude_percent) <= 5.0

    def test_drain_clears_the_buffer(self):
        sim = GBMSimulator(tickers=["AAPL"], event_probability=1.0, py_rng=random.Random(1))
        sim.step()
        assert len(sim.drain_events()) == 1
        assert sim.drain_events() == []

    def test_no_shocks_at_zero_probability(self):
        sim = GBMSimulator(tickers=["AAPL"], event_probability=0.0)
        for _ in range(1000):
            sim.step()
        assert sim.drain_events() == []


class TestCholeskyInvariant:
    """len(_tickers) == cholesky.shape[0] is what keeps z_correlated[i] lined up
    with _tickers[i]."""

    def test_invariant_holds_through_churn(self):
        sim = GBMSimulator(tickers=["AAPL", "GOOGL", "MSFT"])
        for ticker in ["TSLA", "NVDA", "PYPL"]:
            sim.add_ticker(ticker)
            assert sim._cholesky.shape[0] == len(sim.get_tickers())
        for ticker in ["GOOGL", "TSLA", "AAPL"]:
            sim.remove_ticker(ticker)
            if len(sim.get_tickers()) > 1:
                assert sim._cholesky.shape[0] == len(sim.get_tickers())

    def test_step_returns_exactly_the_tracked_tickers(self):
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        sim.add_ticker("TSLA")
        sim.remove_ticker("AAPL")
        assert set(sim.step()) == set(sim.get_tickers())

    def test_cholesky_succeeds_at_many_sizes(self):
        for n in (1, 2, 10, 100):
            sim = GBMSimulator(tickers=[f"T{i}" for i in range(n)])
            assert len(sim.step()) == n

    def test_tickers_are_normalised(self):
        sim = GBMSimulator(tickers=[" aapl "])
        assert sim.get_tickers() == ["AAPL"]
        assert sim.get_price("aapl") is not None


class TestSessionOpen:
    def test_previous_close_is_the_seed_price(self):
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim.get_previous_close("AAPL") == SEED_PRICES["AAPL"]

    def test_previous_close_survives_stepping(self):
        sim = GBMSimulator(tickers=["AAPL"])
        for _ in range(100):
            sim.step()
        assert sim.get_previous_close("AAPL") == SEED_PRICES["AAPL"]

    def test_previous_close_dropped_on_remove(self):
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        sim.remove_ticker("AAPL")
        assert sim.get_previous_close("AAPL") is None

    def test_unknown_ticker_gets_a_baseline(self):
        sim = GBMSimulator(tickers=["ZZZZ"], py_rng=random.Random(4))
        assert sim.get_previous_close("ZZZZ") is not None


class TestDegradation:
    def test_duplicate_in_constructor_is_deduped(self):
        sim = GBMSimulator(tickers=["AAPL", "AAPL", "GOOGL"])
        assert sim.get_tickers() == ["AAPL", "GOOGL"]

    def test_non_positive_definite_matrix_degrades_to_independent_draws(self):
        """Cannot happen with the group-based rule, but a crash inside
        add_ticker() would be user-visible."""
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        with mock.patch("numpy.linalg.cholesky", side_effect=np.linalg.LinAlgError("not PD")):
            sim.add_ticker("MSFT")

        assert sim._cholesky is None
        assert len(sim.step()) == 3  # still produces prices
