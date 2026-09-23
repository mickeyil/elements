# Writes OUTPUT, a header defining ELEMENTS_VERSION for the sim build:
# the short commit hash, plus "+d" when anything under src/ differs from
# it (the sim is built from src/, so edits elsewhere do not change what
# it runs). "unknown" without git or outside a checkout.
#
# Run in script mode (cmake -P) on every build; OUTPUT is replaced only
# when its text changes so dependents recompile only on a real change.

if(NOT DEFINED OUTPUT)
    message(FATAL_ERROR "OUTPUT is required")
endif()

if(NOT DEFINED REPO_ROOT)
    message(FATAL_ERROR "REPO_ROOT is required")
endif()

set(VERSION "unknown")
if(DEFINED GIT_EXECUTABLE AND NOT GIT_EXECUTABLE STREQUAL "")
    execute_process(
        COMMAND "${GIT_EXECUTABLE}" -C "${REPO_ROOT}" rev-parse --short HEAD
        RESULT_VARIABLE GIT_RESULT
        OUTPUT_VARIABLE GIT_OUTPUT
        ERROR_QUIET
        OUTPUT_STRIP_TRAILING_WHITESPACE
    )
    if(GIT_RESULT EQUAL 0 AND NOT GIT_OUTPUT STREQUAL "")
        set(VERSION "${GIT_OUTPUT}")
        execute_process(
            COMMAND "${GIT_EXECUTABLE}" -C "${REPO_ROOT}" status --porcelain -- src
            RESULT_VARIABLE GIT_STATUS_RESULT
            OUTPUT_VARIABLE GIT_STATUS_OUTPUT
            ERROR_QUIET
            OUTPUT_STRIP_TRAILING_WHITESPACE
        )
        if(GIT_STATUS_RESULT EQUAL 0 AND NOT GIT_STATUS_OUTPUT STREQUAL "")
            set(VERSION "${VERSION}+d")
        endif()
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
