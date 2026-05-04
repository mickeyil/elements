cmake_minimum_required(VERSION 3.14)
project(elements CXX)

set(CMAKE_CXX_STANDARD 17)
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
    src/copy_ops.cpp
    src/gamma.cpp
    src/platform_clock_host.cpp
    src/posix_tcp_transport.cpp
    src/posix_udp_transport.cpp
    src/pixel_buffer_pool.cpp
    src/pixel_view.cpp
    src/pixel_views.cpp
    src/strip.cpp
    src/layer.cpp
    src/program.cpp
    src/animations/wave.cpp
    src/animations/spark.cpp
    src/animations/paint.cpp
    src/animations/shift.cpp
    src/playback.cpp
    src/clock_sync_client.cpp
)
target_include_directories(elements_core PUBLIC src)

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

    add_executable(test_decoder
        test/test_decoder.cpp
        src/decoder.cpp
        src/blob_reader.cpp
        src/program.cpp
        src/layer.cpp
        src/pixel_buffer_pool.cpp
        src/pixel_view.cpp
        src/pixel_views.cpp
        src/copy_ops.cpp
        src/colors.cpp
        src/animations/wave.cpp
        src/animations/spark.cpp
        src/animations/paint.cpp
        src/animations/shift.cpp
    )
    target_include_directories(test_decoder PRIVATE src)
    target_link_libraries(test_decoder Catch2::Catch2WithMain)

    add_executable(test_engine
        test/test_engine.cpp
        src/engine.cpp
        src/compositor.cpp
        src/strip.cpp
        src/program.cpp
        src/layer.cpp
        src/pixel_buffer_pool.cpp
        src/pixel_view.cpp
        src/pixel_views.cpp
        src/copy_ops.cpp
        src/colors.cpp
    )
    target_include_directories(test_engine PRIVATE src)
    target_link_libraries(test_engine Catch2::Catch2WithMain)

    # test_colors and test_gamma are standalone during the v3 migration:
    # elements_core compilation is broken until compositor is rewired in step
    # 13, so these targets link only their own module sources.
    add_executable(test_colors test/test_colors.cpp src/colors.cpp)
    target_include_directories(test_colors PRIVATE src)
    target_link_libraries(test_colors Catch2::Catch2WithMain)

    add_executable(test_gamma test/test_gamma.cpp src/gamma.cpp)
    target_include_directories(test_gamma PRIVATE src)
    target_link_libraries(test_gamma Catch2::Catch2WithMain)

    add_executable(test_hardware_profile test/test_hardware_profile.cpp)
    target_include_directories(test_hardware_profile PRIVATE src)
    target_link_libraries(test_hardware_profile Catch2::Catch2WithMain)

    add_executable(test_blob_reader test/test_blob_reader.cpp src/blob_reader.cpp)
    target_include_directories(test_blob_reader PRIVATE src)
    target_link_libraries(test_blob_reader Catch2::Catch2WithMain)

    # test_pixel_buffer_pool builds twice from the same test source: once
    # with the host (per-buffer allocation) path, once with the pooled
    # (-DARDUINO, contiguous) path. The pooled build also exercises the
    # adjacency invariant inside an `#ifdef ARDUINO` block in the test.
    add_executable(test_pixel_buffer_pool
        test/test_pixel_buffer_pool.cpp
        src/pixel_buffer_pool.cpp
    )
    target_include_directories(test_pixel_buffer_pool PRIVATE src)
    target_link_libraries(test_pixel_buffer_pool Catch2::Catch2WithMain)

    add_executable(test_pixel_buffer_pool_pooled
        test/test_pixel_buffer_pool.cpp
        src/pixel_buffer_pool.cpp
    )
    target_include_directories(test_pixel_buffer_pool_pooled PRIVATE src)
    target_compile_definitions(test_pixel_buffer_pool_pooled PRIVATE ARDUINO=1)
    target_link_libraries(test_pixel_buffer_pool_pooled Catch2::Catch2WithMain)

    add_executable(test_pixel_view
        test/test_pixel_view.cpp
        src/pixel_view.cpp
    )
    target_include_directories(test_pixel_view PRIVATE src)
    target_link_libraries(test_pixel_view Catch2::Catch2WithMain)

    add_executable(test_pixel_views
        test/test_pixel_views.cpp
        src/pixel_views.cpp
        src/pixel_view.cpp
        src/pixel_buffer_pool.cpp
    )
    target_include_directories(test_pixel_views PRIVATE src)
    target_link_libraries(test_pixel_views Catch2::Catch2WithMain)

    add_executable(test_copy_ops test/test_copy_ops.cpp src/copy_ops.cpp)
    target_include_directories(test_copy_ops PRIVATE src)
    target_link_libraries(test_copy_ops Catch2::Catch2WithMain)

    add_executable(test_strip
        test/test_strip.cpp
        src/strip.cpp
        src/gamma.cpp
    )
    target_include_directories(test_strip PRIVATE src)
    target_link_libraries(test_strip Catch2::Catch2WithMain)

    add_executable(test_layer test/test_layer.cpp src/layer.cpp)
    target_include_directories(test_layer PRIVATE src)
    target_link_libraries(test_layer Catch2::Catch2WithMain)

    add_executable(test_program
        test/test_program.cpp
        src/program.cpp
        src/layer.cpp
        src/pixel_buffer_pool.cpp
        src/pixel_view.cpp
        src/pixel_views.cpp
        src/copy_ops.cpp
    )
    target_include_directories(test_program PRIVATE src)
    target_link_libraries(test_program Catch2::Catch2WithMain)

    add_executable(test_udp_transport
        test/test_udp_transport.cpp
        src/posix_udp_transport.cpp
    )
    target_include_directories(test_udp_transport PRIVATE src)
    target_link_libraries(test_udp_transport Catch2::Catch2WithMain)

    add_executable(test_tcp_transport
        test/test_tcp_transport.cpp
        src/posix_tcp_transport.cpp
    )
    target_include_directories(test_tcp_transport PRIVATE src)
    target_link_libraries(test_tcp_transport Catch2::Catch2WithMain)

    # test_synced_clock is also standalone during the v3 migration: it
    # links the test platform_clock fake (test/platform_clock_test.cpp)
    # instead of any production platform_clock impl.
    add_executable(test_synced_clock
        test/test_synced_clock.cpp
        test/platform_clock_test.cpp
    )
    target_include_directories(test_synced_clock PRIVATE src test)
    target_link_libraries(test_synced_clock Catch2::Catch2WithMain)

    # test_clock_sync_client follows the same standalone pattern: it
    # uses the test platform_clock fake (so SyncedClock advances under
    # test control) and a fake UdpTransport defined in the test TU. No
    # link to elements_core (which links platform_clock_host).
    add_executable(test_clock_sync_client
        test/test_clock_sync_client.cpp
        test/platform_clock_test.cpp
        src/clock_sync_client.cpp
    )
    target_include_directories(test_clock_sync_client PRIVATE src test)
    target_link_libraries(test_clock_sync_client Catch2::Catch2WithMain)

    add_executable(test_compositor
        test/test_compositor.cpp
        src/compositor.cpp
        src/strip.cpp
        src/pixel_view.cpp
        src/colors.cpp
    )
    target_include_directories(test_compositor PRIVATE src)
    target_link_libraries(test_compositor Catch2::Catch2WithMain)

    add_executable(test_animations
        test/test_animations.cpp
        src/animations/wave.cpp
        src/animations/spark.cpp
        src/animations/paint.cpp
        src/animations/shift.cpp
        src/blob_reader.cpp
        src/pixel_view.cpp
        src/colors.cpp
    )
    target_include_directories(test_animations PRIVATE src)
    target_link_libraries(test_animations Catch2::Catch2WithMain)

    # test_playback is standalone: it links the test platform_clock fake
    # (test/platform_clock_test.cpp) instead of the production impl, so
    # SyncedClock advances under test control.
    add_executable(test_playback
        test/test_playback.cpp
        test/platform_clock_test.cpp
        src/playback.cpp
        src/decoder.cpp
        src/engine.cpp
        src/compositor.cpp
        src/strip.cpp
        src/gamma.cpp
        src/blob_reader.cpp
        src/program.cpp
        src/layer.cpp
        src/pixel_buffer_pool.cpp
        src/pixel_view.cpp
        src/pixel_views.cpp
        src/copy_ops.cpp
        src/colors.cpp
        src/animations/wave.cpp
        src/animations/spark.cpp
        src/animations/paint.cpp
        src/animations/shift.cpp
    )
    target_include_directories(test_playback PRIVATE src test)
    target_link_libraries(test_playback Catch2::Catch2WithMain)

    add_executable(test_background_crc test/test_background_crc.cpp)
    target_link_libraries(test_background_crc elements_core Catch2::Catch2WithMain)

    add_executable(test_sim_device_identity
        test/test_sim_device_identity.cpp
        src/sim_device_identity.cpp
    )
    target_include_directories(test_sim_device_identity PRIVATE src)
    target_link_libraries(test_sim_device_identity Catch2::Catch2WithMain)

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
    endif()

    add_test(NAME test_decoder COMMAND test_decoder
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_engine COMMAND test_engine
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_colors COMMAND test_colors
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_gamma COMMAND test_gamma
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_hardware_profile COMMAND test_hardware_profile
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_blob_reader COMMAND test_blob_reader
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_pixel_buffer_pool COMMAND test_pixel_buffer_pool
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_pixel_buffer_pool_pooled COMMAND test_pixel_buffer_pool_pooled
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_pixel_view COMMAND test_pixel_view
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_pixel_views COMMAND test_pixel_views
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_copy_ops COMMAND test_copy_ops
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_strip COMMAND test_strip
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_layer COMMAND test_layer
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_program COMMAND test_program
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_synced_clock COMMAND test_synced_clock
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_clock_sync_client COMMAND test_clock_sync_client
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_udp_transport COMMAND test_udp_transport
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_tcp_transport COMMAND test_tcp_transport
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_compositor COMMAND test_compositor
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_animations COMMAND test_animations
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_playback COMMAND test_playback
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_background_crc COMMAND test_background_crc
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_sim_device_identity COMMAND test_sim_device_identity
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
            NAME test_hardware_profile_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_hardware_profile>
        )
        set_tests_properties(test_hardware_profile_memcheck PROPERTIES
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        )
        add_test(
            NAME test_blob_reader_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_blob_reader>
        )
        set_tests_properties(test_blob_reader_memcheck PROPERTIES
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        )
        add_test(
            NAME test_pixel_buffer_pool_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_pixel_buffer_pool>
        )
        set_tests_properties(test_pixel_buffer_pool_memcheck PROPERTIES
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        )
        add_test(
            NAME test_pixel_buffer_pool_pooled_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_pixel_buffer_pool_pooled>
        )
        set_tests_properties(test_pixel_buffer_pool_pooled_memcheck PROPERTIES
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        )
        add_test(
            NAME test_pixel_view_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_pixel_view>
        )
        set_tests_properties(test_pixel_view_memcheck PROPERTIES
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        )
        add_test(
            NAME test_pixel_views_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_pixel_views>
        )
        set_tests_properties(test_pixel_views_memcheck PROPERTIES
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        )
        add_test(
            NAME test_copy_ops_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_copy_ops>
        )
        set_tests_properties(test_copy_ops_memcheck PROPERTIES
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        )
        add_test(
            NAME test_strip_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_strip>
        )
        set_tests_properties(test_strip_memcheck PROPERTIES
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        )
        add_test(
            NAME test_layer_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_layer>
        )
        set_tests_properties(test_layer_memcheck PROPERTIES
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        )
        add_test(
            NAME test_program_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_program>
        )
        set_tests_properties(test_program_memcheck PROPERTIES
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        )
        add_test(
            NAME test_udp_transport_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_udp_transport>
        )
        set_tests_properties(test_udp_transport_memcheck PROPERTIES
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        )
        add_test(
            NAME test_tcp_transport_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_tcp_transport>
        )
        set_tests_properties(test_tcp_transport_memcheck PROPERTIES
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
            NAME test_clock_sync_client_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_clock_sync_client>
        )
        set_tests_properties(test_clock_sync_client_memcheck PROPERTIES
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
            NAME test_playback_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_playback>
        )
        set_tests_properties(test_playback_memcheck PROPERTIES
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        )
    endif()
endif()
