UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Darwin)
JOBS ?= $(shell sysctl -n hw.ncpu)
else
JOBS ?= $(shell nproc)
endif

.PHONY: build cmake firmware flash test clean cleanall

build: cmake

cmake:
	cmake -B build
	cmake --build build --parallel $(JOBS)

firmware:
	pio run

flash:
	pio run -e esp32dev --target upload

test: cmake
	ctest --test-dir build --output-on-failure

clean:
	rm -rf build/

cleanall:
	rm -rf build/ local/
