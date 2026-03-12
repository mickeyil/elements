if(NOT DEFINED OUTPUT)
    message(FATAL_ERROR "OUTPUT is required")
endif()

if(NOT DEFINED REPO_ROOT)
    message(FATAL_ERROR "REPO_ROOT is required")
endif()

set(VERSION "unknown")
if(DEFINED GIT_EXECUTABLE AND NOT GIT_EXECUTABLE STREQUAL "")
    execute_process(
        COMMAND "${GIT_EXECUTABLE}" -C "${REPO_ROOT}" describe --tags --always --dirty
        RESULT_VARIABLE GIT_RESULT
        OUTPUT_VARIABLE GIT_OUTPUT
        ERROR_QUIET
        OUTPUT_STRIP_TRAILING_WHITESPACE
    )
    if(GIT_RESULT EQUAL 0 AND NOT GIT_OUTPUT STREQUAL "")
        set(VERSION "${GIT_OUTPUT}")
    endif()
endif()

get_filename_component(OUTPUT_DIR "${OUTPUT}" DIRECTORY)
file(MAKE_DIRECTORY "${OUTPUT_DIR}")

set(CONTENT "#pragma once\n#define ELEMENTS_VERSION \"${VERSION}\"\n")
set(TMP "${OUTPUT}.tmp")
file(WRITE "${TMP}" "${CONTENT}")

set(REPLACE_OUTPUT TRUE)
if(EXISTS "${OUTPUT}")
    file(READ "${OUTPUT}" CURRENT)
    if(CURRENT STREQUAL CONTENT)
        set(REPLACE_OUTPUT FALSE)
    endif()
endif()

if(REPLACE_OUTPUT)
    file(RENAME "${TMP}" "${OUTPUT}")
else()
    file(REMOVE "${TMP}")
endif()
