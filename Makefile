.PHONY: build cmake network_sim firmware flash test clean cleanall

build: cmake

cmake:
	cmake -B build
	cmake --build build

network_sim:
	cmake -B build
	cmake --build build --target network_sim

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
