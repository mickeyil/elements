cmake_minimum_required(VERSION 3.14)
project(elements CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ---------------------------------------------------------------------------
# Library: elements core (desktop build, not ESP32)
# ---------------------------------------------------------------------------

add_library(elements_core STATIC
    src/decoder.cpp
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

    add_test(NAME test_decoder COMMAND test_decoder
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
    endif()
endif()
