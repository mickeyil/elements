cmake_minimum_required(VERSION 3.14)
project(elements CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ---------------------------------------------------------------------------
# Library: elements core (desktop build, not ESP32)
# ---------------------------------------------------------------------------

add_library(elements_core STATIC
    src/decoder.cpp
    src/engine.cpp
    src/compositor.cpp
    src/colors.cpp
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

    add_executable(test_decoder test/test_decoder.cpp)
    target_link_libraries(test_decoder elements_core Catch2::Catch2WithMain)

    add_executable(test_engine test/test_engine.cpp)
    target_link_libraries(test_engine elements_core Catch2::Catch2WithMain)

    add_executable(test_colors test/test_colors.cpp)
    target_link_libraries(test_colors elements_core Catch2::Catch2WithMain)

    add_executable(test_compositor test/test_compositor.cpp)
    target_link_libraries(test_compositor elements_core Catch2::Catch2WithMain)

    add_executable(test_animations test/test_animations.cpp)
    target_link_libraries(test_animations elements_core Catch2::Catch2WithMain)

    # Auto-generate test fixture if missing
    find_program(PYTHON3 python3)
    if(PYTHON3)
        add_custom_command(
            OUTPUT ${CMAKE_SOURCE_DIR}/test/fixtures/test_animation.bin
            COMMAND ${PYTHON3} ${CMAKE_SOURCE_DIR}/test/fixtures/generate.py
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
            COMMENT "Generating test fixture test_animation.bin"
        )
        add_custom_target(test_fixtures
            DEPENDS ${CMAKE_SOURCE_DIR}/test/fixtures/test_animation.bin
        )
        add_dependencies(test_decoder test_fixtures)
        add_dependencies(test_engine test_fixtures)
    endif()

    add_test(NAME test_decoder COMMAND test_decoder
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_engine COMMAND test_engine
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_colors COMMAND test_colors
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_compositor COMMAND test_compositor
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME test_animations COMMAND test_animations
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
    endif()
endif()
