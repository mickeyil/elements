#include <catch2/catch_test_macros.hpp>

#include <sys/wait.h>
#include <unistd.h>

#include "../src/sim/sim_system_platform.h"

// reboot() must terminate the process with the reboot sentinel. The child
// calls reboot(); if it wrongly returns, the _exit(99) fallback makes the
// parent see 99 instead, failing the test rather than passing by accident.
TEST_CASE("SimSystemPlatform::reboot exits with the sentinel", "[sim_system_platform]") {
    pid_t pid = fork();
    REQUIRE(pid >= 0);

    if (pid == 0) {
        SimSystemPlatform platform;
        platform.reboot();
        _exit(99);
    }

    int status = 0;
    REQUIRE(waitpid(pid, &status, 0) == pid);
    REQUIRE(WIFEXITED(status));
    CHECK(WEXITSTATUS(status) == SIM_REBOOT_EXIT_CODE);
}
