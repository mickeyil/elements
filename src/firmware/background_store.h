#pragma once

#include <Preferences.h>

#include <cstddef>
#include <cstdint>

namespace firmware {

struct BackgroundMetadata {
    bool present = false;
    uint16_t strip_length = 0;
    uint32_t blob_len = 0;
    uint32_t crc32 = 0;
};

class BackgroundStore {
public:
    void begin();
    bool ready() const;
    BackgroundMetadata metadata() const;
    bool store(
        const uint8_t* blob,
        size_t len,
        uint16_t strip_length,
        uint32_t expected_crc32
    );
    bool clear();
    bool read_blob(uint8_t* out, size_t out_len) const;

private:
    void load_metadata_();
    bool validate_committed_file_(uint32_t expected_crc32, uint32_t expected_len, uint32_t* actual_crc32) const;
    bool write_metadata_(uint16_t strip_length, uint32_t blob_len, uint32_t crc32);
    void reset_metadata_();

    Preferences _preferences;
    bool _fs_ready = false;
    bool _preferences_ready = false;
    BackgroundMetadata _metadata;
};

}  // namespace firmware
