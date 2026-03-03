cmake_minimum_required(VERSION 3.14)
project(elements_tests CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Decoder under test
add_library(decoder STATIC
    ../src/decoder.cpp
)
target_include_directories(decoder PUBLIC ../src)

# Catch2 amalgamated
add_library(catch2 STATIC catch_amalgamated.cpp)
target_include_directories(catch2 PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})

# Test executable
add_executable(test_decoder
    test_decoder.cpp
)
target_link_libraries(test_decoder decoder catch2)

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
