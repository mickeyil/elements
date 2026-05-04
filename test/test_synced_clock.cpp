#include <catch2/catch_test_macros.hpp>

#include "../src/synced_clock.h"
#include "test_platform_clock.h"

// ---------------------------------------------------------------------------
// Initial state
// ---------------------------------------------------------------------------

TEST_CASE("SyncedClock: default-constructed is unsynced", "[synced_clock]") {
    set_test_now_us(1'000'000);

    SyncedClock c;
    CHECK_FALSE(c.is_synced());

    // No offset has ever been applied, so remote == local.
    CHECK(c.now_local_us() == 1'000'000);
    CHECK(c.now_remote_us() == 1'000'000);
}

// ---------------------------------------------------------------------------
// Sign convention round-trip
// ---------------------------------------------------------------------------

TEST_CASE("SyncedClock: positive offset means local leads remote", "[synced_clock]") {
    set_test_now_us(1'000'000);

    SyncedClock c;
    c.apply_sync_offset(/*offset*/ 250, /*valid_for*/ 1'000'000);

    REQUIRE(c.is_synced());
    CHECK(c.now_local_us() == 1'000'000);
    CHECK(c.now_remote_us() == 999'750);
    CHECK(c.now_local_us() - c.now_remote_us() == 250);
}

TEST_CASE("SyncedClock: negative offset means remote leads local", "[synced_clock]") {
    set_test_now_us(1'000'000);

    SyncedClock c;
    c.apply_sync_offset(/*offset*/ -250, /*valid_for*/ 1'000'000);

    REQUIRE(c.is_synced());
    CHECK(c.now_remote_us() == 1'000'250);
    CHECK(c.now_local_us() - c.now_remote_us() == -250);
}

// ---------------------------------------------------------------------------
// Lease expiry
// ---------------------------------------------------------------------------

TEST_CASE("SyncedClock: lease expires at the exact boundary", "[synced_clock]") {
    set_test_now_us(1'000'000);

    SyncedClock c;
    c.apply_sync_offset(/*offset*/ 0, /*valid_for*/ 500'000);
    REQUIRE(c.is_synced());

    // is_synced() uses strict less-than: valid_until is exclusive.
    set_test_now_us(1'499'999);
    CHECK(c.is_synced());

    set_test_now_us(1'500'000);
    CHECK_FALSE(c.is_synced());

    set_test_now_us(1'500'001);
    CHECK_FALSE(c.is_synced());
}

TEST_CASE("SyncedClock: valid_for_us == 0 expires immediately", "[synced_clock]") {
    set_test_now_us(42);

    SyncedClock c;
    c.apply_sync_offset(/*offset*/ 1000, /*valid_for*/ 0);

    // Push-revoke: the lease is dead on arrival.
    CHECK_FALSE(c.is_synced());
    // But the last applied mapping is preserved for now_remote_us().
    CHECK(c.now_remote_us() == 42 - 1000);
}

TEST_CASE("SyncedClock: re-apply refreshes the lease relative to current now", "[synced_clock]") {
    set_test_now_us(1'000'000);

    SyncedClock c;
    c.apply_sync_offset(/*offset*/ 0, /*valid_for*/ 500'000);
    // First lease covers [1'000'000, 1'500'000).

    // Time advances within the active lease.
    set_test_now_us(1'200'000);
    REQUIRE(c.is_synced());

    // Re-apply: the new lease is anchored to now=1'200'000, not stacked.
    c.apply_sync_offset(/*offset*/ 0, /*valid_for*/ 500'000);
    // New lease covers [1'200'000, 1'700'000).

    set_test_now_us(1'699'999);
    CHECK(c.is_synced());

    set_test_now_us(1'700'000);
    CHECK_FALSE(c.is_synced());
}

TEST_CASE("SyncedClock: remote mapping persists across lease expiry", "[synced_clock]") {
    set_test_now_us(1'000'000);

    SyncedClock c;
    c.apply_sync_offset(/*offset*/ 300, /*valid_for*/ 100);

    set_test_now_us(1'500'000);

    // Lease is long gone, but the last-known offset is still applied to
    // now_remote_us(). Callers gate on is_synced() if they need a fresh
    // estimate.
    REQUIRE_FALSE(c.is_synced());
    CHECK(c.now_remote_us() == 1'500'000 - 300);
}

// ---------------------------------------------------------------------------
// clear_sync
// ---------------------------------------------------------------------------

TEST_CASE("SyncedClock: clear_sync drops sync and wipes the offset", "[synced_clock]") {
    set_test_now_us(1'000'000);

    SyncedClock c;
    c.apply_sync_offset(/*offset*/ 300, /*valid_for*/ 1'000'000);
    REQUIRE(c.is_synced());

    c.clear_sync();

    CHECK_FALSE(c.is_synced());
    // Unlike lease expiry, clear_sync wipes the offset entirely:
    // remote == local until a new offset is applied.
    CHECK(c.now_remote_us() == c.now_local_us());
}

TEST_CASE("SyncedClock: clear_sync is idempotent on an already-expired lease", "[synced_clock]") {
    set_test_now_us(1'000'000);

    SyncedClock c;
    c.apply_sync_offset(/*offset*/ 300, /*valid_for*/ 10);

    set_test_now_us(2'000'000);
    REQUIRE_FALSE(c.is_synced());

    c.clear_sync();

    CHECK_FALSE(c.is_synced());
    CHECK(c.now_remote_us() == c.now_local_us());
}

TEST_CASE("SyncedClock: re-apply after clear_sync restores synced state", "[synced_clock]") {
    set_test_now_us(1'000'000);

    SyncedClock c;
    c.apply_sync_offset(/*offset*/ 100, /*valid_for*/ 500'000);
    c.clear_sync();
    REQUIRE_FALSE(c.is_synced());

    c.apply_sync_offset(/*offset*/ 200, /*valid_for*/ 500'000);

    CHECK(c.is_synced());
    CHECK(c.now_remote_us() == 1'000'000 - 200);
}

// ---------------------------------------------------------------------------
// Arithmetic at realistic scales
// ---------------------------------------------------------------------------

TEST_CASE("SyncedClock: int64 arithmetic holds at year-scale magnitudes", "[synced_clock]") {
    // Local clock at ~30 years of microseconds, offset at ~10 years.
    // Both well inside int64 range; this test catches accidental int32
    // narrowing in the math path.
    constexpr int64_t LOCAL_US  = 1'000'000'000'000'000LL;  // ~31.7 years
    constexpr int64_t OFFSET_US =   100'000'000'000'000LL;  //  ~3.17 years
    set_test_now_us(LOCAL_US);

    SyncedClock c;
    c.apply_sync_offset(OFFSET_US, /*valid_for*/ 1'000'000'000LL);

    REQUIRE(c.is_synced());
    CHECK(c.now_local_us() == LOCAL_US);
    CHECK(c.now_remote_us() == LOCAL_US - OFFSET_US);
}
