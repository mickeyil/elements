cmake_minimum_required(VERSION 3.14)
project(elements_tests CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Fetch Catch2 at configure time
include(FetchContent)
FetchContent_Declare(
    Catch2
    GIT_REPOSITORY https://github.com/catchorg/Catch2.git
    GIT_TAG v3.8.0
)
FetchContent_MakeAvailable(Catch2)

# Decoder under test
add_library(decoder STATIC
    ../src/decoder.cpp
)
target_include_directories(decoder PUBLIC ../src)

# Test executable
add_executable(test_decoder
    test_decoder.cpp
)
target_link_libraries(test_decoder decoder Catch2::Catch2WithMain)

# CTest
enable_testing()
add_test(NAME test_decoder COMMAND test_decoder)

# Valgrind memcheck (optional)
find_program(VALGRIND valgrind)
if(VALGRIND)
    add_test(
        NAME test_decoder_memcheck
        COMMAND ${VALGRIND} --leak-check=full --errors-for-leak-kinds=definite,indirect,possible
                --error-exitcode=1 $<TARGET_FILE:test_decoder>
    )
    set_tests_properties(test_decoder_memcheck PROPERTIES
        WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}/..
    )
endif()

# Working directory for test blob loading
set_tests_properties(test_decoder PROPERTIES
    WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}/..
)
