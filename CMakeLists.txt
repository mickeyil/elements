cmake_minimum_required(VERSION 3.14)
project(elements CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(Git QUIET)

# ---------------------------------------------------------------------------
# Library: elements core (desktop build, not ESP32)
# ---------------------------------------------------------------------------

add_library(elements_core STATIC
    src/background_crc.cpp
    src/decoder.cpp
    src/engine.cpp
    src/compositor.cpp
    src/colors.cpp
    src/gamma.cpp
    src/platform_clock_steady.cpp
    src/playback_device.cpp
    src/esp_simulated.cpp
    src/sim_controller.cpp
)
target_include_directories(elements_core PUBLIC src)

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

add_executable(network_sim src/network_sim.cpp)
target_link_libraries(network_sim elements_core)
set(GENERATED_DIR ${CMAKE_BINARY_DIR}/generated)
set(ELEMENTS_VERSION_HEADER ${GENERATED_DIR}/elements_version.h)
add_custom_target(generate_network_sim_version ALL
    COMMAND ${CMAKE_COMMAND}
        -DOUTPUT=${ELEMENTS_VERSION_HEADER}
        -DREPO_ROOT=${CMAKE_SOURCE_DIR}
        -DGIT_EXECUTABLE=${GIT_EXECUTABLE}
        -P ${CMAKE_SOURCE_DIR}/cmake/GenerateVersionHeader.cmake
    BYPRODUCTS ${ELEMENTS_VERSION_HEADER}
    COMMENT "Generating network_sim version header"
    VERBATIM
)
add_dependencies(network_sim generate_network_sim_version)
target_include_directories(network_sim PRIVATE ${GENERATED_DIR})

add_executable(strip_render src/strip_render.cpp)
target_link_libraries(strip_render elements_core)

# ---------------------------------------------------------------------------
# Tests (Catch2, fetched at configure time)
# ---------------------------------------------------------------------------

option(BUILD_TESTS "Build tests" ON)

if(BUILD_TESTS)
    include(FetchContent)
    FetchContent_Declare(
        Catch2
        GIT_REPOSITORY https://github.com/catchorg/Catch2.git
        GIT_TAG v3.8.0
    )
    FetchContent_MakeAvailable(Catch2)

    enable_testing()

    add_executable(test_decoder test/test_decoder.cpp)
    target_link_libraries(test_decoder elements_core Catch2::Catch2WithMain)

    add_executable(test_engine test/test_engine.cpp)
    target_link_libraries(test_engine elements_core Catch2::Catch2WithMain)

    # test_colors and test_gamma are standalone during the v3 migration:
    # elements_core compilation is broken until compositor is rewired in step
    # 13, so these targets link only their own module sources.
    add_executable(test_colors test/test_colors.cpp src/colors.cpp)
    target_include_directories(test_colors PRIVATE src)
    target_link_libraries(test_colors Catch2::Catch2WithMain)

    add_executable(test_gamma test/test_gamma.cpp src/gamma.cpp)
    target_include_directories(test_gamma PRIVATE src)
    target_link_libraries(test_gamma Catch2::Catch2WithMain)

    # test_synced_clock is also standalone during the v3 migration: it
    # links the test platform_clock fake (test/platform_clock_test.cpp)
    # instead of any production platform_clock impl.
    add_executable(test_synced_clock
        test/test_synced_clock.cpp
        test/platform_clock_test.cpp
    )
    target_include_directories(test_synced_clock PRIVATE src test)
    target_link_libraries(test_synced_clock Catch2::Catch2WithMain)

    add_executable(test_compositor test/test_compositor.cpp)
    target_link_libraries(test_compositor elements_core Catch2::Catch2WithMain)

    add_executable(test_animations test/test_animations.cpp)
    target_link_libraries(test_animations elements_core Catch2::Catch2WithMain)

    add_executable(test_playback_device test/test_playback_device.cpp)
    target_link_libraries(test_playback_device elements_core Catch2::Catch2WithMain)

    add_executable(test_esp_simulated test/test_esp_simulated.cpp)
    target_link_libraries(test_esp_simulated elements_core Catch2::Catch2WithMain)

    add_executable(test_sim_controller test/test_sim_controller.cpp)
    target_link_libraries(test_sim_controller elements_core Catch2::Catch2WithMain)

    add_executable(test_background_crc test/test_background_crc.cpp)
    target_link_libraries(test_background_crc elements_core Catch2::Catch2WithMain)

    # Auto-generate test fixtures if missing
    find_program(PYTHON3 python3)
    if(PYTHON3)
        add_custom_command(
            OUTPUT ${CMAKE_SOURCE_DIR}/test/fixtures/test_animation.bin
            COMMAND ${CMAKE_COMMAND} -E env
                    PYTHONPATH=${CMAKE_SOURCE_DIR}/compiler
                    ${PYTHON3} ${CMAKE_SOURCE_DIR}/test/fixtures/generate.py
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
            COMMENT "Generating test fixture test_animation.bin"
        )
        add_custom_command(
            OUTPUT ${CMAKE_SOURCE_DIR}/test/fixtures/test_shift.bin
            COMMAND ${CMAKE_COMMAND} -E env
                    PYTHONPATH=${CMAKE_SOURCE_DIR}/compiler
                    ${PYTHON3} ${CMAKE_SOURCE_DIR}/test/fixtures/generate_shift.py
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
            COMMENT "Generating test fixture test_shift.bin"
        )
        add_custom_command(
            OUTPUT ${CMAKE_SOURCE_DIR}/test/fixtures/test_dual_shift_left.bin
                   ${CMAKE_SOURCE_DIR}/test/fixtures/test_dual_shift_right.bin
            COMMAND ${CMAKE_COMMAND} -E env
                    PYTHONPATH=${CMAKE_SOURCE_DIR}/compiler
                    ${PYTHON3} ${CMAKE_SOURCE_DIR}/test/fixtures/generate_dual_shift.py
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
            COMMENT "Generating dual-strip test fixtures"
        )
        add_custom_target(test_fixtures
            DEPENDS ${CMAKE_SOURCE_DIR}/test/fixtures/test_animation.bin
                    ${CMAKE_SOURCE_DIR}/test/fixtures/test_shift.bin
                    ${CMAKE_SOURCE_DIR}/test/fixtures/test_dual_shift_left.bin
                    ${CMAKE_SOURCE_DIR}/test/fixtures/test_dual_shift_right.bin
        )
        add_dependencies(test_decoder test_fixtures)
        add_dependencies(test_engine test_fixtures)
        add_dependencies(test_playback_device test_fixtures)
        add_dependencies(test_esp_simulated test_fixtures)
        add_dependencies(test_sim_controller test_fixtures)
    endif()

    add_test(NAME test_decoder COMMAND test_decoder
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_engine COMMAND test_engine
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_colors COMMAND test_colors
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_gamma COMMAND test_gamma
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_synced_clock COMMAND test_synced_clock
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_compositor COMMAND test_compositor
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_animations COMMAND test_animations
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_playback_device COMMAND test_playback_device
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_esp_simulated COMMAND test_esp_simulated
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_sim_controller COMMAND test_sim_controller
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_background_crc COMMAND test_background_crc
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})

    # Valgrind memcheck (optional)
    find_program(VALGRIND valgrind)
    if(VALGRIND)
        add_test(
            NAME test_decoder_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_decoder>
        )
        set_tests_properties(test_decoder_memcheck PROPERTIES
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        )
        add_test(
            NAME test_engine_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_engine>
        )
        set_tests_properties(test_engine_memcheck PROPERTIES
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        )
        add_test(
            NAME test_colors_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_colors>
        )
        set_tests_properties(test_colors_memcheck PROPERTIES
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        )
        add_test(
            NAME test_gamma_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_gamma>
        )
        set_tests_properties(test_gamma_memcheck PROPERTIES
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        )
        add_test(
            NAME test_synced_clock_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_synced_clock>
        )
        set_tests_properties(test_synced_clock_memcheck PROPERTIES
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        )
        add_test(
            NAME test_compositor_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_compositor>
        )
        set_tests_properties(test_compositor_memcheck PROPERTIES
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        )
        add_test(
            NAME test_animations_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_animations>
        )
        set_tests_properties(test_animations_memcheck PROPERTIES
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        )
        add_test(
            NAME test_playback_device_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_playback_device>
        )
        set_tests_properties(test_playback_device_memcheck PROPERTIES
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        )
        add_test(
            NAME test_esp_simulated_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_esp_simulated>
        )
        set_tests_properties(test_esp_simulated_memcheck PROPERTIES
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        )
        add_test(
            NAME test_sim_controller_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_sim_controller>
        )
        set_tests_properties(test_sim_controller_memcheck PROPERTIES
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        )
    endif()
endif()
