cmake_minimum_required(VERSION 3.14)
project(elements CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(Git QUIET)
include(FetchContent)

FetchContent_Declare(
    nlohmann_json
    URL https://github.com/nlohmann/json/releases/download/v3.11.3/json.tar.xz
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
)
FetchContent_MakeAvailable(nlohmann_json)

# ---------------------------------------------------------------------------
# Library: elements core (desktop build, not ESP32)
# ---------------------------------------------------------------------------

add_library(elements_core STATIC
    src/controller/animation_store.cpp
    src/controller/command_handler.cpp
    src/controller/local_animation.cpp
    src/controller/command_processor.cpp
    src/controller/controller_link.cpp
    src/controller/crc32.cpp
    src/core/decoder.cpp
    src/core/engine.cpp
    src/core/compositor.cpp
    src/core/colors.cpp
    src/core/copy_ops.cpp
    src/core/gamma.cpp
    src/controller/hardware_profile_store.cpp
    src/platform/wifi_cred_store.cpp
    src/platform/sim/file_key_value_store.cpp
    src/platform/sim/host_platform_clock.cpp
    src/platform/sim/posix_file_store.cpp
    src/platform/sim/posix_tcp_transport.cpp
    src/platform/sim/posix_udp_transport.cpp
    src/platform/sim/sim_storage.cpp
    src/platform/sim/sim_system_platform.cpp
    src/core/pixel_buffer_pool.cpp
    src/core/pixel_view.cpp
    src/core/pixel_views.cpp
    src/core/strip.cpp
    src/core/layer.cpp
    src/core/program.cpp
    src/core/animations/wave.cpp
    src/core/animations/spark.cpp
    src/core/animations/paint.cpp
    src/core/animations/shift.cpp
    src/core/animations/pacifica.cpp
    src/core/fx_math.cpp
    src/controller/playback.cpp
    src/controller/clock_sync_client.cpp
    src/controller/discovery.cpp
    src/controller/slog.cpp
    src/controller/log_sender.cpp
)
target_include_directories(elements_core PUBLIC src)
target_link_libraries(elements_core PUBLIC nlohmann_json::nlohmann_json)

# ---------------------------------------------------------------------------
# Sim device binary (the v3 device running on the host)
# ---------------------------------------------------------------------------

add_executable(sim_device
    src/platform/sim/main.cpp
    src/controller/app.cpp
    src/core/blob_reader.cpp
    src/controller/wire_reader.cpp
    src/controller/wire_writer.cpp
    src/platform/sim/sim_device_identity.cpp
    src/platform/sim/sim_frame_output.cpp
)
target_link_libraries(sim_device elements_core)

# ---------------------------------------------------------------------------
# Tests (Catch2, fetched at configure time)
# ---------------------------------------------------------------------------

option(BUILD_TESTS "Build tests" ON)

if(BUILD_TESTS)
    FetchContent_Declare(
        Catch2
        GIT_REPOSITORY https://github.com/catchorg/Catch2.git
        GIT_TAG v3.8.0
    )
    FetchContent_MakeAvailable(Catch2)

    enable_testing()

    add_executable(test_decoder
        test/test_decoder.cpp
        src/core/decoder.cpp
        src/core/blob_reader.cpp
        src/core/program.cpp
        src/core/layer.cpp
        src/core/pixel_buffer_pool.cpp
        src/core/pixel_view.cpp
        src/core/pixel_views.cpp
        src/core/copy_ops.cpp
        src/core/colors.cpp
        src/core/animations/wave.cpp
        src/core/animations/spark.cpp
        src/core/animations/paint.cpp
        src/core/animations/shift.cpp
        src/core/animations/pacifica.cpp
        src/core/fx_math.cpp
    )
    target_include_directories(test_decoder PRIVATE src)
    target_link_libraries(test_decoder Catch2::Catch2WithMain)

    add_executable(test_engine
        test/test_engine.cpp
        src/core/engine.cpp
        src/core/compositor.cpp
        src/core/strip.cpp
        src/core/program.cpp
        src/core/layer.cpp
        src/core/pixel_buffer_pool.cpp
        src/core/pixel_view.cpp
        src/core/pixel_views.cpp
        src/core/copy_ops.cpp
        src/core/colors.cpp
    )
    target_include_directories(test_engine PRIVATE src)
    target_link_libraries(test_engine Catch2::Catch2WithMain)

    # test_colors and test_gamma are standalone during the v3 migration:
    # elements_core compilation is broken until compositor is rewired in step
    # 13, so these targets link only their own module sources.
    add_executable(test_colors test/test_colors.cpp src/core/colors.cpp)
    target_include_directories(test_colors PRIVATE src)
    target_link_libraries(test_colors Catch2::Catch2WithMain)

    add_executable(test_gamma test/test_gamma.cpp src/core/gamma.cpp)
    target_include_directories(test_gamma PRIVATE src)
    target_link_libraries(test_gamma Catch2::Catch2WithMain)

    add_executable(test_hardware_profile test/test_hardware_profile.cpp)
    target_include_directories(test_hardware_profile PRIVATE src)
    target_link_libraries(test_hardware_profile Catch2::Catch2WithMain)

    add_executable(test_blob_reader test/test_blob_reader.cpp src/core/blob_reader.cpp)
    target_include_directories(test_blob_reader PRIVATE src)
    target_link_libraries(test_blob_reader Catch2::Catch2WithMain)

    add_executable(test_wire_reader test/test_wire_reader.cpp src/controller/wire_reader.cpp)
    target_include_directories(test_wire_reader PRIVATE src)
    target_link_libraries(test_wire_reader Catch2::Catch2WithMain)

    add_executable(test_wire_writer test/test_wire_writer.cpp src/controller/wire_writer.cpp)
    target_include_directories(test_wire_writer PRIVATE src)
    target_link_libraries(test_wire_writer Catch2::Catch2WithMain)

    add_executable(test_host_network_interface test/test_host_network_interface.cpp)
    target_include_directories(test_host_network_interface PRIVATE src)
    target_link_libraries(test_host_network_interface Catch2::Catch2WithMain)

    add_executable(test_device_status test/test_device_status.cpp)
    target_include_directories(test_device_status PRIVATE src)
    target_link_libraries(test_device_status Catch2::Catch2WithMain)

    add_executable(test_sim_system_platform
        test/test_sim_system_platform.cpp
        src/platform/sim/sim_system_platform.cpp)
    target_include_directories(test_sim_system_platform PRIVATE src)
    target_link_libraries(test_sim_system_platform Catch2::Catch2WithMain)

    # test_pixel_buffer_pool builds twice from the same test source: once
    # with the host (per-buffer allocation) path, once with the pooled
    # (-DARDUINO, contiguous) path. The pooled build also exercises the
    # adjacency invariant inside an `#ifdef ARDUINO` block in the test.
    add_executable(test_pixel_buffer_pool
        test/test_pixel_buffer_pool.cpp
        src/core/pixel_buffer_pool.cpp
    )
    target_include_directories(test_pixel_buffer_pool PRIVATE src)
    target_link_libraries(test_pixel_buffer_pool Catch2::Catch2WithMain)

    add_executable(test_pixel_buffer_pool_pooled
        test/test_pixel_buffer_pool.cpp
        src/core/pixel_buffer_pool.cpp
    )
    target_include_directories(test_pixel_buffer_pool_pooled PRIVATE src)
    target_compile_definitions(test_pixel_buffer_pool_pooled PRIVATE ARDUINO=1)
    target_link_libraries(test_pixel_buffer_pool_pooled Catch2::Catch2WithMain)

    add_executable(test_pixel_view
        test/test_pixel_view.cpp
        src/core/pixel_view.cpp
    )
    target_include_directories(test_pixel_view PRIVATE src)
    target_link_libraries(test_pixel_view Catch2::Catch2WithMain)

    add_executable(test_pixel_views
        test/test_pixel_views.cpp
        src/core/pixel_views.cpp
        src/core/pixel_view.cpp
        src/core/pixel_buffer_pool.cpp
    )
    target_include_directories(test_pixel_views PRIVATE src)
    target_link_libraries(test_pixel_views Catch2::Catch2WithMain)

    add_executable(test_copy_ops test/test_copy_ops.cpp src/core/copy_ops.cpp)
    target_include_directories(test_copy_ops PRIVATE src)
    target_link_libraries(test_copy_ops Catch2::Catch2WithMain)

    add_executable(test_strip
        test/test_strip.cpp
        src/core/strip.cpp
        src/core/gamma.cpp
    )
    target_include_directories(test_strip PRIVATE src)
    target_link_libraries(test_strip Catch2::Catch2WithMain)

    add_executable(test_layer test/test_layer.cpp src/core/layer.cpp)
    target_include_directories(test_layer PRIVATE src)
    target_link_libraries(test_layer Catch2::Catch2WithMain)

    add_executable(test_program
        test/test_program.cpp
        src/core/program.cpp
        src/core/layer.cpp
        src/core/pixel_buffer_pool.cpp
        src/core/pixel_view.cpp
        src/core/pixel_views.cpp
        src/core/copy_ops.cpp
    )
    target_include_directories(test_program PRIVATE src)
    target_link_libraries(test_program Catch2::Catch2WithMain)

    add_executable(test_udp_transport
        test/test_udp_transport.cpp
        src/platform/sim/posix_udp_transport.cpp
    )
    target_include_directories(test_udp_transport PRIVATE src)
    target_link_libraries(test_udp_transport Catch2::Catch2WithMain)

    add_executable(test_tcp_transport
        test/test_tcp_transport.cpp
        src/platform/sim/posix_tcp_transport.cpp
    )
    target_include_directories(test_tcp_transport PRIVATE src)
    target_link_libraries(test_tcp_transport Catch2::Catch2WithMain)

    add_executable(test_file_store
        test/test_file_store.cpp
        src/platform/sim/posix_file_store.cpp
        src/platform/sim/sim_storage.cpp
    )
    target_include_directories(test_file_store PRIVATE src)
    target_link_libraries(test_file_store Catch2::Catch2WithMain)

    add_executable(test_animation_store
        test/test_animation_store.cpp
        src/controller/animation_store.cpp
        src/controller/crc32.cpp
        src/core/blob_reader.cpp
        src/core/decoder.cpp
        src/core/program.cpp
        src/core/layer.cpp
        src/core/pixel_buffer_pool.cpp
        src/core/pixel_view.cpp
        src/core/pixel_views.cpp
        src/core/copy_ops.cpp
        src/core/colors.cpp
        src/core/animations/wave.cpp
        src/core/animations/spark.cpp
        src/core/animations/paint.cpp
        src/core/animations/shift.cpp
        src/core/animations/pacifica.cpp
        src/core/fx_math.cpp
    )
    target_include_directories(test_animation_store PRIVATE src)
    target_link_libraries(test_animation_store Catch2::Catch2WithMain)

    add_executable(test_key_value_store
        test/test_key_value_store.cpp
        src/controller/hardware_profile_store.cpp
        src/platform/sim/file_key_value_store.cpp
        src/platform/sim/sim_storage.cpp
    )
    target_include_directories(test_key_value_store PRIVATE src)
    target_link_libraries(test_key_value_store
        nlohmann_json::nlohmann_json
        Catch2::Catch2WithMain
    )

    add_executable(test_wifi_cred_store
        test/test_wifi_cred_store.cpp
        src/platform/wifi_cred_store.cpp
        src/platform/sim/file_key_value_store.cpp
        src/platform/sim/sim_storage.cpp
    )
    target_include_directories(test_wifi_cred_store PRIVATE src)
    target_link_libraries(test_wifi_cred_store
        nlohmann_json::nlohmann_json
        Catch2::Catch2WithMain
    )

    # test_synced_clock is also standalone during the v3 migration: it
    # links the test platform_clock fake (test/test_platform_clock.cpp)
    # instead of any production platform_clock impl.
    add_executable(test_synced_clock
        test/test_synced_clock.cpp
        test/test_platform_clock.cpp
    )
    target_include_directories(test_synced_clock PRIVATE src test)
    target_link_libraries(test_synced_clock Catch2::Catch2WithMain)

    # test_clock_sync_client follows the same standalone pattern: it
    # uses the test platform_clock fake (so SyncedClock advances under
    # test control) and a fake UdpTransport defined in the test TU. No
    # link to elements_core (which links host_platform_clock).
    add_executable(test_clock_sync_client
        test/test_clock_sync_client.cpp
        test/test_platform_clock.cpp
        src/controller/clock_sync_client.cpp
    )
    target_include_directories(test_clock_sync_client PRIVATE src test)
    target_link_libraries(test_clock_sync_client Catch2::Catch2WithMain)

    # test_discovery follows the same standalone pattern as
    # test_clock_sync_client.
    add_executable(test_discovery
        test/test_discovery.cpp
        test/test_platform_clock.cpp
        src/controller/discovery.cpp
    )
    target_include_directories(test_discovery PRIVATE src test)
    target_link_libraries(test_discovery Catch2::Catch2WithMain)

    add_executable(test_compositor
        test/test_compositor.cpp
        src/core/compositor.cpp
        src/core/strip.cpp
        src/core/pixel_view.cpp
        src/core/colors.cpp
    )
    target_include_directories(test_compositor PRIVATE src)
    target_link_libraries(test_compositor Catch2::Catch2WithMain)

    add_executable(test_animations
        test/test_animations.cpp
        src/core/animations/wave.cpp
        src/core/animations/spark.cpp
        src/core/animations/paint.cpp
        src/core/animations/shift.cpp
        src/core/animations/pacifica.cpp
        src/core/fx_math.cpp
        src/core/blob_reader.cpp
        src/core/pixel_view.cpp
        src/core/colors.cpp
    )
    target_include_directories(test_animations PRIVATE src)
    target_link_libraries(test_animations Catch2::Catch2WithMain)

    add_executable(test_fx_math test/test_fx_math.cpp src/core/fx_math.cpp)
    target_include_directories(test_fx_math PRIVATE src)
    target_link_libraries(test_fx_math Catch2::Catch2WithMain)

    add_executable(test_joystick_lamp
        test/test_joystick_lamp.cpp
        src/apps/joystick_lamp/lamp_logic.cpp
        src/core/pixel_view.cpp
        src/core/colors.cpp
    )
    target_include_directories(test_joystick_lamp PRIVATE src)
    target_link_libraries(test_joystick_lamp Catch2::Catch2WithMain)

    # test_playback is standalone: it links the test platform_clock fake
    # (test/test_platform_clock.cpp) instead of the production impl, so
    # SyncedClock advances under test control.
    add_executable(test_playback
        test/test_playback.cpp
        test/test_platform_clock.cpp
        src/controller/playback.cpp
        src/core/decoder.cpp
        src/core/engine.cpp
        src/core/compositor.cpp
        src/core/strip.cpp
        src/core/gamma.cpp
        src/core/blob_reader.cpp
        src/core/program.cpp
        src/core/layer.cpp
        src/core/pixel_buffer_pool.cpp
        src/core/pixel_view.cpp
        src/core/pixel_views.cpp
        src/core/copy_ops.cpp
        src/core/colors.cpp
        src/core/animations/wave.cpp
        src/core/animations/spark.cpp
        src/core/animations/paint.cpp
        src/core/animations/shift.cpp
        src/core/animations/pacifica.cpp
        src/core/fx_math.cpp
    )
    target_include_directories(test_playback PRIVATE src test)
    target_link_libraries(test_playback Catch2::Catch2WithMain)

    add_executable(test_command_handler
        test/test_command_handler.cpp
        test/test_platform_clock.cpp
        src/controller/command_handler.cpp
        src/controller/slog.cpp
        src/controller/local_animation.cpp
        src/controller/animation_store.cpp
        src/controller/crc32.cpp
        src/controller/hardware_profile_store.cpp
        src/controller/wire_reader.cpp
        src/controller/wire_writer.cpp
        src/controller/playback.cpp
        src/core/decoder.cpp
        src/core/engine.cpp
        src/core/compositor.cpp
        src/core/strip.cpp
        src/core/gamma.cpp
        src/core/blob_reader.cpp
        src/core/program.cpp
        src/core/layer.cpp
        src/core/pixel_buffer_pool.cpp
        src/core/pixel_view.cpp
        src/core/pixel_views.cpp
        src/core/copy_ops.cpp
        src/core/colors.cpp
        src/core/animations/wave.cpp
        src/core/animations/spark.cpp
        src/core/animations/paint.cpp
        src/core/animations/shift.cpp
        src/core/animations/pacifica.cpp
        src/core/fx_math.cpp
    )
    target_include_directories(test_command_handler PRIVATE src test)
    target_link_libraries(test_command_handler Catch2::Catch2WithMain)

    add_executable(test_command_processor
        test/test_command_processor.cpp
        test/test_platform_clock.cpp
        src/controller/command_processor.cpp
        src/controller/command_handler.cpp
        src/controller/slog.cpp
        src/controller/local_animation.cpp
        src/controller/animation_store.cpp
        src/controller/crc32.cpp
        src/controller/hardware_profile_store.cpp
        src/controller/wire_reader.cpp
        src/controller/wire_writer.cpp
        src/controller/playback.cpp
        src/core/decoder.cpp
        src/core/engine.cpp
        src/core/compositor.cpp
        src/core/strip.cpp
        src/core/gamma.cpp
        src/core/blob_reader.cpp
        src/core/program.cpp
        src/core/layer.cpp
        src/core/pixel_buffer_pool.cpp
        src/core/pixel_view.cpp
        src/core/pixel_views.cpp
        src/core/copy_ops.cpp
        src/core/colors.cpp
        src/core/animations/wave.cpp
        src/core/animations/spark.cpp
        src/core/animations/paint.cpp
        src/core/animations/shift.cpp
        src/core/animations/pacifica.cpp
        src/core/fx_math.cpp
    )
    target_include_directories(test_command_processor PRIVATE src test)
    target_link_libraries(test_command_processor Catch2::Catch2WithMain)

    add_executable(test_controller_link
        test/test_controller_link.cpp
        test/test_platform_clock.cpp
        src/controller/controller_link.cpp
        src/controller/discovery.cpp
        src/controller/command_processor.cpp
        src/controller/command_handler.cpp
        src/controller/slog.cpp
        src/controller/local_animation.cpp
        src/controller/animation_store.cpp
        src/controller/crc32.cpp
        src/controller/hardware_profile_store.cpp
        src/controller/wire_reader.cpp
        src/controller/wire_writer.cpp
        src/controller/playback.cpp
        src/core/decoder.cpp
        src/core/engine.cpp
        src/core/compositor.cpp
        src/core/strip.cpp
        src/core/gamma.cpp
        src/core/blob_reader.cpp
        src/core/program.cpp
        src/core/layer.cpp
        src/core/pixel_buffer_pool.cpp
        src/core/pixel_view.cpp
        src/core/pixel_views.cpp
        src/core/copy_ops.cpp
        src/core/colors.cpp
        src/core/animations/wave.cpp
        src/core/animations/spark.cpp
        src/core/animations/paint.cpp
        src/core/animations/shift.cpp
        src/core/animations/pacifica.cpp
        src/core/fx_math.cpp
    )
    target_include_directories(test_controller_link PRIVATE src test)
    target_link_libraries(test_controller_link Catch2::Catch2WithMain)

    add_executable(test_app
        test/test_app.cpp
        test/test_platform_clock.cpp
        src/controller/app.cpp
        src/controller/controller_link.cpp
        src/controller/clock_sync_client.cpp
        src/controller/discovery.cpp
        src/controller/command_processor.cpp
        src/controller/command_handler.cpp
        src/controller/slog.cpp
        src/controller/log_sender.cpp
        src/controller/local_animation.cpp
        src/controller/animation_store.cpp
        src/controller/crc32.cpp
        src/controller/hardware_profile_store.cpp
        src/controller/wire_reader.cpp
        src/controller/wire_writer.cpp
        src/controller/playback.cpp
        src/core/decoder.cpp
        src/core/engine.cpp
        src/core/compositor.cpp
        src/core/strip.cpp
        src/core/gamma.cpp
        src/core/blob_reader.cpp
        src/core/program.cpp
        src/core/layer.cpp
        src/core/pixel_buffer_pool.cpp
        src/core/pixel_view.cpp
        src/core/pixel_views.cpp
        src/core/copy_ops.cpp
        src/core/colors.cpp
        src/core/animations/wave.cpp
        src/core/animations/spark.cpp
        src/core/animations/paint.cpp
        src/core/animations/shift.cpp
        src/core/animations/pacifica.cpp
        src/core/fx_math.cpp
    )
    target_include_directories(test_app PRIVATE src test)
    target_link_libraries(test_app Catch2::Catch2WithMain)

    # test_slog and test_log_sender follow the standalone pattern: they
    # link the test platform_clock fake so uptime stamps are controllable.
    add_executable(test_slog
        test/test_slog.cpp
        test/test_platform_clock.cpp
        src/controller/slog.cpp
    )
    target_include_directories(test_slog PRIVATE src test)
    target_link_libraries(test_slog Catch2::Catch2WithMain)

    add_executable(test_log_sender
        test/test_log_sender.cpp
        test/test_platform_clock.cpp
        src/controller/slog.cpp
        src/controller/log_sender.cpp
        src/controller/discovery.cpp
        src/controller/wire_writer.cpp
    )
    target_include_directories(test_log_sender PRIVATE src test)
    target_link_libraries(test_log_sender Catch2::Catch2WithMain)

    add_executable(test_crc32 test/test_crc32.cpp)
    target_link_libraries(test_crc32 elements_core Catch2::Catch2WithMain)

    add_executable(test_sim_device_identity
        test/test_sim_device_identity.cpp
        src/platform/sim/sim_device_identity.cpp
    )
    target_include_directories(test_sim_device_identity PRIVATE src)
    target_link_libraries(test_sim_device_identity Catch2::Catch2WithMain)

    add_executable(test_sim_frame_output
        test/test_sim_frame_output.cpp
        src/platform/sim/sim_frame_output.cpp
        src/core/strip.cpp
        src/core/gamma.cpp
        src/controller/wire_writer.cpp
    )
    target_include_directories(test_sim_frame_output PRIVATE src)
    target_link_libraries(test_sim_frame_output Catch2::Catch2WithMain)

    # Auto-generate test fixtures if missing
    find_program(PYTHON3 python3)
    if(PYTHON3)
        # Fixtures are generated into the build tree (never the source tree) and
        # regenerated whenever a generator or the compiler package changes.
        set(FIXTURE_OUT_DIR ${CMAKE_BINARY_DIR}/test/fixtures)
        set(COMPILER_SOURCES
            ${CMAKE_SOURCE_DIR}/compiler/elements/compiler.py
            ${CMAKE_SOURCE_DIR}/compiler/elements/blob.py
            ${CMAKE_SOURCE_DIR}/compiler/elements/types.py
            ${CMAKE_SOURCE_DIR}/compiler/elements/dsl.py
            ${CMAKE_SOURCE_DIR}/compiler/elements/limits.py
        )
        add_custom_command(
            OUTPUT ${FIXTURE_OUT_DIR}/test_animation.bin
            COMMAND ${CMAKE_COMMAND} -E env PYTHONPATH=${CMAKE_SOURCE_DIR}/compiler
                    ${PYTHON3} ${CMAKE_SOURCE_DIR}/test/fixtures/generate.py ${FIXTURE_OUT_DIR}
            DEPENDS ${CMAKE_SOURCE_DIR}/test/fixtures/generate.py ${COMPILER_SOURCES}
            COMMENT "Generating test fixture test_animation.bin"
        )
        add_custom_command(
            OUTPUT ${FIXTURE_OUT_DIR}/test_shift.bin
            COMMAND ${CMAKE_COMMAND} -E env PYTHONPATH=${CMAKE_SOURCE_DIR}/compiler
                    ${PYTHON3} ${CMAKE_SOURCE_DIR}/test/fixtures/generate_shift.py ${FIXTURE_OUT_DIR}
            DEPENDS ${CMAKE_SOURCE_DIR}/test/fixtures/generate_shift.py ${COMPILER_SOURCES}
            COMMENT "Generating test fixture test_shift.bin"
        )
        add_custom_command(
            OUTPUT ${FIXTURE_OUT_DIR}/test_dual_shift_left.bin
                   ${FIXTURE_OUT_DIR}/test_dual_shift_right.bin
            COMMAND ${CMAKE_COMMAND} -E env PYTHONPATH=${CMAKE_SOURCE_DIR}/compiler
                    ${PYTHON3} ${CMAKE_SOURCE_DIR}/test/fixtures/generate_dual_shift.py ${FIXTURE_OUT_DIR}
            DEPENDS ${CMAKE_SOURCE_DIR}/test/fixtures/generate_dual_shift.py ${COMPILER_SOURCES}
            COMMENT "Generating dual-strip test fixtures"
        )
        add_custom_command(
            OUTPUT ${FIXTURE_OUT_DIR}/test_full_paint.bin
            COMMAND ${CMAKE_COMMAND} -E env PYTHONPATH=${CMAKE_SOURCE_DIR}/compiler
                    ${PYTHON3} ${CMAKE_SOURCE_DIR}/test/fixtures/generate_full_paint.py ${FIXTURE_OUT_DIR}
            DEPENDS ${CMAKE_SOURCE_DIR}/test/fixtures/generate_full_paint.py ${COMPILER_SOURCES}
            COMMENT "Generating full-strip per-pixel paint fixture"
        )
        add_custom_command(
            OUTPUT ${FIXTURE_OUT_DIR}/test_pacifica.bin
            COMMAND ${CMAKE_COMMAND} -E env PYTHONPATH=${CMAKE_SOURCE_DIR}/compiler
                    ${PYTHON3} ${CMAKE_SOURCE_DIR}/test/fixtures/generate_pacifica.py ${FIXTURE_OUT_DIR}
            DEPENDS ${CMAKE_SOURCE_DIR}/test/fixtures/generate_pacifica.py ${COMPILER_SOURCES}
            COMMENT "Generating test fixture test_pacifica.bin"
        )
        add_custom_command(
            OUTPUT ${FIXTURE_OUT_DIR}/test_decimal_timeline.bin
            COMMAND ${CMAKE_COMMAND} -E env PYTHONPATH=${CMAKE_SOURCE_DIR}/compiler
                    ${PYTHON3} ${CMAKE_SOURCE_DIR}/test/fixtures/generate_decimal_timeline.py ${FIXTURE_OUT_DIR}
            DEPENDS ${CMAKE_SOURCE_DIR}/test/fixtures/generate_decimal_timeline.py ${COMPILER_SOURCES}
            COMMENT "Generating test fixture test_decimal_timeline.bin"
        )
        set(HANDOFF_FIXTURES
            ${FIXTURE_OUT_DIR}/test_handoff_skipped_direct.bin
            ${FIXTURE_OUT_DIR}/test_handoff_skipped_copy.bin
            ${FIXTURE_OUT_DIR}/test_handoff_wave_endpoint.bin
            ${FIXTURE_OUT_DIR}/test_handoff_shift_chain.bin
            ${FIXTURE_OUT_DIR}/test_handoff_self_source.bin
            ${FIXTURE_OUT_DIR}/test_handoff_slot_reuse.bin
        )
        add_custom_command(
            OUTPUT ${HANDOFF_FIXTURES}
            COMMAND ${CMAKE_COMMAND} -E env PYTHONPATH=${CMAKE_SOURCE_DIR}/compiler
                    ${PYTHON3} ${CMAKE_SOURCE_DIR}/test/fixtures/generate_handoff.py ${FIXTURE_OUT_DIR}
            DEPENDS ${CMAKE_SOURCE_DIR}/test/fixtures/generate_handoff.py ${COMPILER_SOURCES}
            COMMENT "Generating source handoff test fixtures"
        )
        add_custom_target(test_fixtures
            DEPENDS ${FIXTURE_OUT_DIR}/test_animation.bin
                    ${FIXTURE_OUT_DIR}/test_shift.bin
                    ${FIXTURE_OUT_DIR}/test_dual_shift_left.bin
                    ${FIXTURE_OUT_DIR}/test_dual_shift_right.bin
                    ${FIXTURE_OUT_DIR}/test_full_paint.bin
                    ${FIXTURE_OUT_DIR}/test_pacifica.bin
                    ${FIXTURE_OUT_DIR}/test_decimal_timeline.bin
                    ${HANDOFF_FIXTURES}
        )

        # Cross-check that the Python v4 emitter's blobs decode and run in C++.
        add_executable(test_fixture_blobs
            test/test_fixture_blobs.cpp
            src/core/decoder.cpp
            src/core/blob_reader.cpp
            src/core/program.cpp
            src/core/layer.cpp
            src/core/pixel_buffer_pool.cpp
            src/core/pixel_view.cpp
            src/core/pixel_views.cpp
            src/core/copy_ops.cpp
            src/core/colors.cpp
            src/core/animations/wave.cpp
            src/core/animations/spark.cpp
            src/core/animations/paint.cpp
            src/core/animations/shift.cpp
            src/core/animations/pacifica.cpp
            src/core/fx_math.cpp
            src/core/engine.cpp
            src/core/compositor.cpp
            src/core/strip.cpp
        )
        target_include_directories(test_fixture_blobs PRIVATE src)
        target_compile_definitions(test_fixture_blobs PRIVATE
            FIXTURE_DIR="${FIXTURE_OUT_DIR}")
        target_link_libraries(test_fixture_blobs Catch2::Catch2WithMain)
        add_dependencies(test_fixture_blobs test_fixtures)

        add_test(NAME test_fixture_blobs COMMAND test_fixture_blobs)
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
    add_test(NAME test_wire_reader COMMAND test_wire_reader
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_wire_writer COMMAND test_wire_writer
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_host_network_interface COMMAND test_host_network_interface
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_device_status COMMAND test_device_status
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_sim_system_platform COMMAND test_sim_system_platform
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
    add_test(NAME test_discovery COMMAND test_discovery
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_udp_transport COMMAND test_udp_transport
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_tcp_transport COMMAND test_tcp_transport
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_file_store COMMAND test_file_store
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_key_value_store COMMAND test_key_value_store
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_wifi_cred_store COMMAND test_wifi_cred_store
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_compositor COMMAND test_compositor
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_animations COMMAND test_animations
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_fx_math COMMAND test_fx_math
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_joystick_lamp COMMAND test_joystick_lamp
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_playback COMMAND test_playback
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_crc32 COMMAND test_crc32
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_sim_device_identity COMMAND test_sim_device_identity
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_command_handler COMMAND test_command_handler
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_command_processor COMMAND test_command_processor
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_controller_link COMMAND test_controller_link
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_app COMMAND test_app
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_animation_store COMMAND test_animation_store
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_sim_frame_output COMMAND test_sim_frame_output
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_slog COMMAND test_slog
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_log_sender COMMAND test_log_sender
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
            NAME test_discovery_memcheck
            COMMAND ${VALGRIND} --leak-check=full
                    --errors-for-leak-kinds=definite,indirect,possible
                    --error-exitcode=1 $<TARGET_FILE:test_discovery>
        )
        set_tests_properties(test_discovery_memcheck PROPERTIES
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
