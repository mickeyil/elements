#include <catch2/catch_test_macros.hpp>

#include <cstring>
#include <string>

#include "controller/slog.h"
#include "test_platform_clock.h"

namespace {

struct SlogFixture
{
    SlogFixture()
    {
        set_test_now_us(0);
        slog_reset();
    }
};

}  // namespace

TEST_CASE_METHOD(SlogFixture, "peek on an empty ring returns false")
{
    SlogRecord rec;
    CHECK_FALSE(slog_peek(rec));
}

TEST_CASE_METHOD(SlogFixture, "records carry level, text, seq, and uptime")
{
    set_test_now_us(12'345'000);   // 12.345 s
    slog_warn("beat %d missed by %s", 7, "a lot");

    SlogRecord rec;
    REQUIRE(slog_peek(rec));
    CHECK(rec.seq == 1);
    CHECK(rec.uptime_ms == 12'345);
    CHECK(rec.level == SLOG_LEVEL_WARN);
    CHECK(std::string(rec.text) == "beat 7 missed by a lot");
}

TEST_CASE_METHOD(SlogFixture, "records pop oldest first with increasing seq")
{
    slog_info("first");
    slog_error("second");

    SlogRecord rec;
    REQUIRE(slog_peek(rec));
    CHECK(rec.seq == 1);
    CHECK(std::string(rec.text) == "first");
    CHECK(rec.level == SLOG_LEVEL_INFO);
    slog_pop();

    REQUIRE(slog_peek(rec));
    CHECK(rec.seq == 2);
    CHECK(std::string(rec.text) == "second");
    CHECK(rec.level == SLOG_LEVEL_ERROR);
    slog_pop();

    CHECK_FALSE(slog_peek(rec));
}

TEST_CASE_METHOD(SlogFixture, "a full ring overwrites the oldest record")
{
    for (int i = 1; i <= static_cast<int>(SLOG_RING_RECORDS) + 4; ++i) {
        slog_info("msg %d", i);
    }

    // The 4 oldest were overwritten; the survivors are 5..20 in order.
    for (int i = 5; i <= static_cast<int>(SLOG_RING_RECORDS) + 4; ++i) {
        SlogRecord rec;
        REQUIRE(slog_peek(rec));
        CHECK(rec.seq == static_cast<uint32_t>(i));
        CHECK(std::string(rec.text) == "msg " + std::to_string(i));
        slog_pop();
    }
    SlogRecord rec;
    CHECK_FALSE(slog_peek(rec));
}

TEST_CASE_METHOD(SlogFixture, "overlong text is truncated, not split")
{
    std::string big(2 * SLOG_TEXT_CAP, 'x');
    slog_info("%s", big.c_str());

    SlogRecord rec;
    REQUIRE(slog_peek(rec));
    CHECK(std::strlen(rec.text) == SLOG_TEXT_CAP - 1);
    slog_pop();
    CHECK_FALSE(slog_peek(rec));
}

TEST_CASE_METHOD(SlogFixture, "reset forgets records and restarts seq")
{
    slog_info("before");
    slog_reset();

    SlogRecord rec;
    CHECK_FALSE(slog_peek(rec));

    slog_info("after");
    REQUIRE(slog_peek(rec));
    CHECK(rec.seq == 1);
    CHECK(std::string(rec.text) == "after");
}

TEST_CASE_METHOD(SlogFixture, "pop on an empty ring is harmless")
{
    slog_pop();
    slog_info("still fine");
    SlogRecord rec;
    REQUIRE(slog_peek(rec));
    CHECK(rec.seq == 1);
}
