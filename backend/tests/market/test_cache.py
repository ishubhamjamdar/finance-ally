"""Tests for PriceCache."""

from app.market.cache import PriceCache


class TestPriceCache:
    """Unit tests for the PriceCache."""

    def test_update_and_get(self):
        """Test updating and getting a price."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.50)
        assert update.ticker == "AAPL"
        assert update.price == 190.50
        assert cache.get("AAPL") == update

    def test_first_update_is_flat(self):
        """Test that the first update has flat direction."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.50)
        assert update.direction == "flat"
        assert update.previous_price == 190.50

    def test_direction_up(self):
        """Test price update with upward direction."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        update = cache.update("AAPL", 191.00)
        assert update.direction == "up"
        assert update.change == 1.00

    def test_direction_down(self):
        """Test price update with downward direction."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        update = cache.update("AAPL", 189.00)
        assert update.direction == "down"
        assert update.change == -1.00

    def test_remove(self):
        """Test removing a ticker from cache."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.remove("AAPL")
        assert cache.get("AAPL") is None

    def test_remove_nonexistent(self):
        """Test removing a ticker that doesn't exist."""
        cache = PriceCache()
        cache.remove("AAPL")  # Should not raise

    def test_get_all(self):
        """Test getting all prices."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.update("GOOGL", 175.00)
        all_prices = cache.get_all()
        assert set(all_prices.keys()) == {"AAPL", "GOOGL"}

    def test_version_increments(self):
        """Test that version counter increments."""
        cache = PriceCache()
        v0 = cache.version
        cache.update("AAPL", 190.00)
        assert cache.version == v0 + 1
        cache.update("AAPL", 191.00)
        assert cache.version == v0 + 2

    def test_get_price_convenience(self):
        """Test the convenience get_price method."""
        cache = PriceCache()
        cache.update("AAPL", 190.50)
        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("NOPE") is None

    def test_len(self):
        """Test __len__ method."""
        cache = PriceCache()
        assert len(cache) == 0
        cache.update("AAPL", 190.00)
        assert len(cache) == 1
        cache.update("GOOGL", 175.00)
        assert len(cache) == 2

    def test_contains(self):
        """Test __contains__ method."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        assert "AAPL" in cache
        assert "GOOGL" not in cache

    def test_custom_timestamp(self):
        """Test updating with a custom timestamp."""
        cache = PriceCache()
        custom_ts = 1234567890.0
        update = cache.update("AAPL", 190.50, timestamp=custom_ts)
        assert update.timestamp == custom_ts

    def test_price_rounding(self):
        """Test that prices are rounded to 2 decimal places."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.12345)
        assert update.price == 190.12

    def test_zero_timestamp_is_preserved(self):
        """0.0 is falsy — `timestamp or time.time()` would silently discard it."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.50, timestamp=0.0)
        assert update.timestamp == 0.0


class TestNormalisation:
    def test_writes_are_normalised(self):
        cache = PriceCache()
        cache.update(" aapl ", 190.00)
        assert cache.get_price("AAPL") == 190.00

    def test_reads_are_normalised(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        assert cache.get_price(" aapl ") == 190.00
        assert "aapl" in cache

    def test_remove_is_normalised(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.remove(" aapl ")
        assert cache.get("AAPL") is None

    def test_case_variants_are_one_entry(self):
        cache = PriceCache()
        cache.update("aapl", 190.00)
        cache.update("AAPL", 191.00)
        assert len(cache) == 1
        assert cache.get("AAPL").direction == "up"


class TestPreviousClose:
    def test_stored_when_supplied(self):
        cache = PriceCache()
        update = cache.update("AAPL", 195.00, previous_close=190.00)
        assert update.previous_close == 190.00
        assert update.day_change_percent == 2.6316

    def test_is_sticky_across_updates(self):
        """The simulator passes it every tick, but Massive may only have it on
        some polls — a later update omitting it must not blank the column."""
        cache = PriceCache()
        cache.update("AAPL", 195.00, previous_close=190.00)
        update = cache.update("AAPL", 196.00)
        assert update.previous_close == 190.00

    def test_can_be_replaced_on_a_new_session(self):
        cache = PriceCache()
        cache.update("AAPL", 195.00, previous_close=190.00)
        update = cache.update("AAPL", 196.00, previous_close=195.00)
        assert update.previous_close == 195.00

    def test_absent_by_default(self):
        cache = PriceCache()
        assert cache.update("AAPL", 190.00).previous_close is None


class TestConcurrency:
    def test_concurrent_writers_and_readers(self):
        """The lock is a threading.Lock precisely because MassiveDataSource
        writes from an asyncio.to_thread worker on a real OS thread."""
        from concurrent.futures import ThreadPoolExecutor

        cache = PriceCache()

        def write(n):
            for i in range(1000):
                cache.update(f"T{n}", 100.0 + i * 0.01)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write, range(8)))

        assert len(cache) == 8
        assert cache.version == 8000  # no lost updates

    def test_reads_during_writes_are_consistent(self):
        from concurrent.futures import ThreadPoolExecutor

        cache = PriceCache()
        cache.update("AAPL", 100.0)
        errors: list[Exception] = []

        def write():
            for i in range(500):
                cache.update("AAPL", 100.0 + i * 0.01)

        def read():
            try:
                for _ in range(500):
                    snapshot = cache.get_all()
                    for ticker, update in snapshot.items():
                        assert update.ticker == ticker
                        assert update.price > 0
            except Exception as exc:  # pragma: no cover - only on a real race
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(write), pool.submit(read), pool.submit(write), pool.submit(read)]
            for future in futures:
                future.result()

        assert errors == []


class TestStaleness:
    """Receipt-time bookkeeping — what tells a wedged feed from a quiet one.

    The distinction that matters throughout: `PriceUpdate.timestamp` is the
    venue's trade time, which on Massive is hours old the moment the market
    closes. These ages are measured from when *this cache* was written.
    """

    def test_a_fresh_write_has_a_small_age(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        age = cache.age_of("AAPL")
        assert age is not None
        assert age < 1.0

    def test_a_ticker_never_written_has_no_age(self):
        assert PriceCache().age_of("AAPL") is None

    def test_age_is_normalised_like_every_other_lookup(self):
        cache = PriceCache()
        cache.update("aapl", 190.0)

        assert cache.age_of(" AAPL ") is not None

    def test_nothing_is_stale_without_a_source_to_set_the_bound(self):
        """A cache nobody is writing keeps `staleness_limit` at None, which is
        what leaves hand-populated test caches — and every API test — alone."""
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        cache._received["AAPL"] -= 3600  # an hour old

        assert cache.staleness_limit is None
        assert cache.is_stale("AAPL") is False

    def test_an_entry_older_than_the_bound_is_stale(self):
        cache = PriceCache()
        cache.staleness_limit = 10.0
        cache.update("AAPL", 190.0)
        cache._received["AAPL"] -= 11

        assert cache.is_stale("AAPL") is True

    def test_an_entry_inside_the_bound_is_not(self):
        cache = PriceCache()
        cache.staleness_limit = 10.0
        cache.update("AAPL", 190.0)
        cache._received["AAPL"] -= 9

        assert cache.is_stale("AAPL") is False

    def test_a_ticker_with_no_price_is_not_reported_as_stale(self):
        """ "No price at all" is a different refusal with a better message, and
        `_require_price` reaches it first."""
        cache = PriceCache()
        cache.staleness_limit = 10.0

        assert cache.is_stale("AAPL") is False

    def test_staleness_is_per_ticker(self):
        """One ticker dropping out of a Massive snapshot must not condemn the
        rest, and must not be hidden by them either."""
        cache = PriceCache()
        cache.staleness_limit = 10.0
        cache.update("AAPL", 190.0)
        cache.update("MSFT", 420.0)
        cache._received["MSFT"] -= 30

        assert cache.is_stale("AAPL") is False
        assert cache.is_stale("MSFT") is True

    def test_a_later_write_clears_staleness(self):
        cache = PriceCache()
        cache.staleness_limit = 10.0
        cache.update("AAPL", 190.0)
        cache._received["AAPL"] -= 30
        assert cache.is_stale("AAPL") is True

        cache.update("AAPL", 191.0)

        assert cache.is_stale("AAPL") is False

    def test_removing_a_ticker_forgets_when_it_arrived(self):
        """Otherwise a ticker removed and re-added carries the old receipt, and
        `remove` already exists to make re-adding a clean slate."""
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        cache.remove("AAPL")

        assert cache.age_of("AAPL") is None
