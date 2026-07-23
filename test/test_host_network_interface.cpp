#include <catch2/catch_test_macros.hpp>

#include "platform/sim/host_network_interface.h"

TEST_CASE("HostNetworkInterface is always up", "[host_network_interface]") {
    HostNetworkInterface net;
    net.begin();

    CHECK(net.is_up());
}

TEST_CASE("HostNetworkInterface never reports a transition", "[host_network_interface]") {
    HostNetworkInterface net;
    net.begin();

    // No association edge exists on the host, so every poll stays unchanged
    // (a spurious link_up here would mislead a future edge consumer).
    CHECK(net.poll() == NetworkTransition::unchanged);
    CHECK(net.poll() == NetworkTransition::unchanged);
    CHECK(net.is_up());
}
