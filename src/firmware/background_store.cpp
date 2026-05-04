#include "background_store.h"

#include <Arduino.h>
#include <FS.h>
#include <LittleFS.h>

#include "background_crc.h"
#include "diagnostics.h"

namespace {

static constexpr char kBackgroundNamespace[] = "background";
static constexpr char kBackgroundPresentKey[] = "present";
static constexpr char kBackgroundStripLengthKey[] = "strip_len";
static constexpr char kBackgroundBlobLenKey[] = "blob_len";
static constexpr char kBackgroundCrcKey[] = "crc32";
static constexpr char kBackgroundPath[] = "/background.bin";
static constexpr char kBackgroundTempPath[] = "/background.bin.tmp";

}  // namespace

void BackgroundStore::begin()
{
    _fs_ready = LittleFS.begin(true);
    if (!_fs_ready) {
        log_line("[bg] LittleFS mount failed");
    } else {
        log_line(
            "[bg] fs total=%lu used=%lu",
            static_cast<unsigned long>(LittleFS.totalBytes()),
            static_cast<unsigned long>(LittleFS.usedBytes())
        );
    }

    _preferences_ready = _preferences.begin(kBackgroundNamespace, false);
    if (!_preferences_ready) {
        log_line("[bg] failed to open preferences namespace=%s", kBackgroundNamespace);
    }

    reset_metadata_();
    load_metadata_();
}

bool BackgroundStore::ready() const
{
    return _fs_ready && _preferences_ready;
}

BackgroundMetadata BackgroundStore::metadata() const
{
    return _metadata;
}

bool BackgroundStore::store(
    const uint8_t* blob,
    size_t len,
    uint16_t strip_length,
    uint32_t expected_crc32
)
{
    if (!ready() || blob == nullptr || len == 0) {
        return false;
    }

    File tmp = LittleFS.open(kBackgroundTempPath, FILE_WRITE);
    if (!tmp) {
        log_line("[bg] failed to open temp file for write");
        return false;
    }
    const size_t written = tmp.write(blob, len);
    tmp.flush();
    tmp.close();
    if (written != len) {
        log_line(
            "[bg] short write bytes=%lu expected=%lu",
            static_cast<unsigned long>(written),
            static_cast<unsigned long>(len)
        );
        LittleFS.remove(kBackgroundTempPath);
        return false;
    }

    if (!LittleFS.rename(kBackgroundTempPath, kBackgroundPath)) {
        if (!LittleFS.exists(kBackgroundPath) || !LittleFS.remove(kBackgroundPath) ||
            !LittleFS.rename(kBackgroundTempPath, kBackgroundPath)) {
            log_line("[bg] failed to commit background file");
            LittleFS.remove(kBackgroundTempPath);
            return false;
        }
    }

    uint32_t actual_crc32 = 0;
    if (!validate_committed_file_(expected_crc32, static_cast<uint32_t>(len), &actual_crc32)) {
        log_line(
            "[bg] CRC/size validation failed expected_crc=%08lx",
            static_cast<unsigned long>(expected_crc32)
        );
        clear();
        return false;
    }

    if (!write_metadata_(strip_length, static_cast<uint32_t>(len), actual_crc32)) {
        log_line("[bg] failed to commit metadata");
        clear();
        return false;
    }

    log_line(
        "[bg] stored strip_length=%u bytes=%lu crc32=%08lx",
        static_cast<unsigned>(strip_length),
        static_cast<unsigned long>(len),
        static_cast<unsigned long>(actual_crc32)
    );
    return true;
}

bool BackgroundStore::clear()
{
    if (!ready()) {
        return false;
    }

    const BackgroundMetadata old = _metadata;
    bool ok = true;

    if (LittleFS.exists(kBackgroundTempPath) && !LittleFS.remove(kBackgroundTempPath)) {
        log_line("[bg] failed to remove temp background file");
        ok = false;
    }
    if (LittleFS.exists(kBackgroundPath) && !LittleFS.remove(kBackgroundPath)) {
        log_line("[bg] failed to remove background file");
        ok = false;
    }

    if (_preferences.getBool(kBackgroundPresentKey, false) &&
        !_preferences.remove(kBackgroundPresentKey)) {
        log_line("[bg] failed to clear background present flag");
        ok = false;
    }
    if (_preferences.getUShort(kBackgroundStripLengthKey, 0) != 0 &&
        !_preferences.remove(kBackgroundStripLengthKey)) {
        log_line("[bg] failed to clear background strip length");
        ok = false;
    }
    if (_preferences.getUInt(kBackgroundBlobLenKey, 0) != 0 &&
        !_preferences.remove(kBackgroundBlobLenKey)) {
        log_line("[bg] failed to clear background blob length");
        ok = false;
    }
    if (_preferences.getUInt(kBackgroundCrcKey, 0) != 0 &&
        !_preferences.remove(kBackgroundCrcKey)) {
        log_line("[bg] failed to clear background crc32");
        ok = false;
    }

    if (ok) {
        reset_metadata_();
    } else if (!LittleFS.exists(kBackgroundPath) ||
               !_preferences.getBool(kBackgroundPresentKey, false)) {
        reset_metadata_();
    } else {
        _metadata = old;
    }
    return ok;
}

bool BackgroundStore::read_blob(uint8_t* out, size_t out_len) const
{
    if (!ready() || !_metadata.present || out == nullptr || out_len < _metadata.blob_len) {
        return false;
    }
    File file = LittleFS.open(kBackgroundPath, FILE_READ);
    if (!file) {
        return false;
    }
    size_t total = 0;
    while (total < _metadata.blob_len) {
        const size_t want = _metadata.blob_len - total;
        const int n = file.read(out + total, want);
        if (n <= 0) {
            file.close();
            return false;
        }
        total += static_cast<size_t>(n);
    }
    file.close();
    return total == _metadata.blob_len;
}

void BackgroundStore::load_metadata_()
{
    if (!ready()) {
        return;
    }

    const bool present = _preferences.getBool(kBackgroundPresentKey, false);
    if (!present) {
        return;
    }

    _metadata.present = true;
    _metadata.strip_length = _preferences.getUShort(kBackgroundStripLengthKey, 0);
    _metadata.blob_len = _preferences.getUInt(kBackgroundBlobLenKey, 0);
    _metadata.crc32 = _preferences.getUInt(kBackgroundCrcKey, 0);

    uint32_t actual_crc32 = 0;
    if (!validate_committed_file_(_metadata.crc32, _metadata.blob_len, &actual_crc32)) {
        log_line("[bg] invalid stored background artifact; clearing");
        clear();
        return;
    }

    log_line(
        "[bg] background present strip_length=%u bytes=%lu crc32=%08lx",
        static_cast<unsigned>(_metadata.strip_length),
        static_cast<unsigned long>(_metadata.blob_len),
        static_cast<unsigned long>(_metadata.crc32)
    );
}

bool BackgroundStore::validate_committed_file_(
    uint32_t expected_crc32,
    uint32_t expected_len,
    uint32_t* actual_crc32
) const
{
    if (!_fs_ready) {
        return false;
    }
    File file = LittleFS.open(kBackgroundPath, FILE_READ);
    if (!file) {
        return false;
    }
    if (file.size() != expected_len) {
        file.close();
        return false;
    }

    uint8_t buffer[512];
    uint32_t crc = 0;
    size_t total = 0;
    while (total < expected_len) {
        const size_t want = expected_len - total;
        const size_t chunk = want < sizeof(buffer) ? want : sizeof(buffer);
        const int n = file.read(buffer, chunk);
        if (n <= 0) {
            file.close();
            return false;
        }
        crc = crc32_ieee_continue(crc, buffer, static_cast<size_t>(n));
        total += static_cast<size_t>(n);
    }
    file.close();
    if (actual_crc32 != nullptr) {
        *actual_crc32 = crc;
    }
    return total == expected_len && crc == expected_crc32;
}

bool BackgroundStore::write_metadata_(uint16_t strip_length, uint32_t blob_len, uint32_t crc32)
{
    if (!_preferences_ready) {
        return false;
    }

    if (!_preferences.putUShort(kBackgroundStripLengthKey, strip_length)) {
        return false;
    }
    if (!_preferences.putUInt(kBackgroundBlobLenKey, blob_len)) {
        return false;
    }
    if (!_preferences.putUInt(kBackgroundCrcKey, crc32)) {
        return false;
    }
    if (!_preferences.putBool(kBackgroundPresentKey, true)) {
        return false;
    }

    _metadata.present = true;
    _metadata.strip_length = strip_length;
    _metadata.blob_len = blob_len;
    _metadata.crc32 = crc32;
    return true;
}

void BackgroundStore::reset_metadata_()
{
    _metadata = BackgroundMetadata{};
}
