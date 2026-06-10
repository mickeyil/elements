#include "animation_store.h"

#include <cstdio>
#include <cstring>
#include <new>

#include "background_crc.h"
#include "blob_reader.h"
#include "decoder.h"
#include "link_protocol.h"

namespace {

constexpr char PLAYLIST_FILE[] = "playlist.txt";

// Blob prefix the store peeks at before accepting or indexing a blob:
//   magic=4B | version=1B | flags=1B | fps=1B | layers=1B |
//   strip_length=2B | buffers=2B | views=2B | copy_ops=2B | duration=4B
// The decoder owns full validation; the store reads only through
// strip_length and requires the rest to be present.
constexpr size_t BLOB_HEADER_BYTES = 20;

// Worst-case playlist text: per line, name + space + 8-hex crc + newline.
constexpr size_t PLAYLIST_BUF_SIZE =
    MAX_STORED_ANIMATIONS * (ANIM_NAME_SIZE + 10) + 1;

bool is_anim_name(const char* name)
{
    // Leading '.' is rejected, which also covers "." and "..".
    if (name == nullptr || *name == '\0' || *name == '.') return false;

    size_t len = 0;
    for (const char* p = name; *p != '\0'; ++p) {
        const char c = *p;
        const bool ok = (c >= 'A' && c <= 'Z') ||
                        (c >= 'a' && c <= 'z') ||
                        (c >= '0' && c <= '9') ||
                        c == '.' || c == '_' || c == '-';
        if (!ok) return false;
        ++len;
        if (len > ANIM_NAME_SIZE) return false;
    }
    return true;
}

void anim_filename(const char* name, char (&out)[ANIM_NAME_BUF_SIZE])
{
    std::snprintf(out, sizeof(out), "%s.anim", name);
}

bool peek_blob_header(const uint8_t* blob, size_t len,
                      uint16_t& strip_length_out, bool& requires_sync_out)
{
    if (len < BLOB_HEADER_BYTES) return false;

    BlobReader r(blob, len);
    const uint8_t* magic = r.take(4);
    if (magic == nullptr || std::memcmp(magic, BLOB_MAGIC, 4) != 0) return false;

    uint8_t version = 0;
    if (!r.read_u8(version) || version != BLOB_VERSION) return false;

    uint8_t flags = 0;
    if (!r.read_u8(flags)) return false;
    if ((flags & ~uint8_t{0x01}) != 0) return false;

    uint8_t skipped = 0;
    if (!r.read_u8(skipped)) return false;  // target_fps
    if (!r.read_u8(skipped)) return false;  // layer_count
    if (!r.read_u16_le(strip_length_out)) return false;

    requires_sync_out = (flags & 0x01) != 0;
    return true;
}

// Advance past whitespace and return the next token, null-terminated in
// place, or nullptr at end of line.
char* next_token(char*& p)
{
    while (*p == ' ' || *p == '\t' || *p == '\r') ++p;
    if (*p == '\0') return nullptr;
    char* tok = p;
    while (*p != '\0' && *p != ' ' && *p != '\t' && *p != '\r') ++p;
    if (*p != '\0') {
        *p = '\0';
        ++p;
    }
    return tok;
}

bool parse_crc_token(const char* tok, uint32_t& out)
{
    if (tok == nullptr || *tok == '\0') return false;

    uint32_t value = 0;
    size_t digits = 0;
    for (const char* p = tok; *p != '\0'; ++p) {
        const char c = *p;
        uint32_t nibble;
        if      (c >= '0' && c <= '9') nibble = uint32_t(c - '0');
        else if (c >= 'a' && c <= 'f') nibble = uint32_t(c - 'a' + 10);
        else if (c >= 'A' && c <= 'F') nibble = uint32_t(c - 'A' + 10);
        else return false;
        ++digits;
        if (digits > 8) return false;
        value = (value << 4) | nibble;
    }
    out = value;
    return true;
}

}  // namespace

AnimationStore::AnimationStore(FileStore& files)
    : _files(files)
{
    load_index_();
}

bool AnimationStore::store(const char* name, const uint8_t* blob, size_t len)
{
    if (!is_anim_name(name)) return false;
    if (blob == nullptr || len > MAX_BLOB_BYTES) return false;

    uint16_t strip_length = 0;
    bool requires_sync = false;
    if (!peek_blob_header(blob, len, strip_length, requires_sync)) return false;
    if (requires_sync) return false;

    int idx = find_(name);
    if (idx < 0 && _count == MAX_STORED_ANIMATIONS) return false;

    // Blob first, playlist second: a crash in between leaves either an
    // orphan file (new name; playlist rule ignores it) or a stale crc
    // (overwrite; the controller sees the mismatch and re-uploads).
    char file[ANIM_NAME_BUF_SIZE];
    anim_filename(name, file);
    if (!_files.write(file, blob, len)) return false;

    const bool is_new = idx < 0;
    if (is_new) {
        idx = static_cast<int>(_count);
        ++_count;
        std::strncpy(_entries[idx].name, name, sizeof(_entries[idx].name) - 1);
        _entries[idx].name[sizeof(_entries[idx].name) - 1] = '\0';
    }
    _entries[idx].strip_length = strip_length;
    _entries[idx].blob_len = static_cast<uint32_t>(len);
    _entries[idx].crc32 = crc32_ieee(blob, len);

    if (!write_playlist_()) {
        load_index_();  // resync with whatever the disk actually says
        return false;
    }
    return true;
}

bool AnimationStore::erase(const char* name)
{
    const int idx = find_(name);
    if (idx < 0) return false;

    for (size_t i = static_cast<size_t>(idx); i + 1 < _count; ++i) {
        _entries[i] = _entries[i + 1];
    }
    --_count;

    if (!write_playlist_()) {
        load_index_();
        return false;
    }

    // Playlist no longer names the file, so a failed remove just leaves
    // an ignored orphan.
    char file[ANIM_NAME_BUF_SIZE];
    anim_filename(name, file);
    _files.remove(file);
    return true;
}

bool AnimationStore::set_order(const char* const* names, size_t n)
{
    if (names == nullptr || n != _count) return false;

    AnimationEntry reordered[MAX_STORED_ANIMATIONS];
    bool used[MAX_STORED_ANIMATIONS] = {};
    for (size_t i = 0; i < n; ++i) {
        const int idx = find_(names[i]);
        if (idx < 0 || used[idx]) return false;
        used[idx] = true;
        reordered[i] = _entries[idx];
    }

    for (size_t i = 0; i < n; ++i) {
        _entries[i] = reordered[i];
    }

    if (!write_playlist_()) {
        load_index_();
        return false;
    }
    return true;
}

bool AnimationStore::read_blob(const char* name, uint8_t* out, size_t out_len) const
{
    const int idx = find_(name);
    if (idx < 0) return false;
    if (out == nullptr || out_len != _entries[idx].blob_len) return false;

    char file[ANIM_NAME_BUF_SIZE];
    anim_filename(name, file);
    if (_files.size(file) != static_cast<int>(out_len)) return false;
    return _files.read(file, out, out_len) == static_cast<int>(out_len);
}

int AnimationStore::find_(const char* name) const
{
    if (name == nullptr) return -1;
    for (size_t i = 0; i < _count; ++i) {
        if (std::strcmp(_entries[i].name, name) == 0) return static_cast<int>(i);
    }
    return -1;
}

bool AnimationStore::write_playlist_()
{
    char buf[PLAYLIST_BUF_SIZE];
    size_t pos = 0;
    for (size_t i = 0; i < _count; ++i) {
        const int n = std::snprintf(buf + pos, sizeof(buf) - pos, "%s %08lx\n",
                                    _entries[i].name,
                                    static_cast<unsigned long>(_entries[i].crc32));
        if (n < 0 || static_cast<size_t>(n) >= sizeof(buf) - pos) return false;
        pos += static_cast<size_t>(n);
    }
    return _files.write(PLAYLIST_FILE,
                        reinterpret_cast<const uint8_t*>(buf), pos);
}

void AnimationStore::load_index_()
{
    _count = 0;

    char buf[PLAYLIST_BUF_SIZE];
    const int n = _files.read(PLAYLIST_FILE,
                              reinterpret_cast<uint8_t*>(buf), sizeof(buf) - 1);
    if (n <= 0) return;
    buf[n] = '\0';

    char* cursor = buf;
    while (*cursor != '\0' && _count < MAX_STORED_ANIMATIONS) {
        char* line = cursor;
        char* nl = std::strchr(cursor, '\n');
        if (nl != nullptr) {
            *nl = '\0';
            cursor = nl + 1;
        } else {
            cursor += std::strlen(cursor);
        }

        char* p = line;
        const char* name = next_token(p);
        const char* crc_tok = next_token(p);
        if (!is_anim_name(name)) continue;
        if (find_(name) >= 0) continue;  // duplicate line; first wins

        char file[ANIM_NAME_BUF_SIZE];
        anim_filename(name, file);
        const int blob_len = _files.size(file);
        if (blob_len < static_cast<int>(BLOB_HEADER_BYTES) ||
            blob_len > static_cast<int>(MAX_BLOB_BYTES)) {
            continue;
        }

        uint8_t header[BLOB_HEADER_BYTES];
        if (_files.read(file, header, sizeof(header)) !=
            static_cast<int>(sizeof(header))) {
            continue;
        }
        uint16_t strip_length = 0;
        bool requires_sync = false;
        if (!peek_blob_header(header, sizeof(header), strip_length,
                              requires_sync)) {
            continue;
        }
        if (requires_sync) continue;

        uint32_t crc = 0;
        if (!parse_crc_token(crc_tok, crc)) {
            // Cached crc missing or garbled: recompute from the blob.
            uint8_t* blob = new (std::nothrow) uint8_t[blob_len];
            if (blob == nullptr) continue;
            const bool ok = _files.read(file, blob, blob_len) == blob_len;
            if (ok) crc = crc32_ieee(blob, static_cast<size_t>(blob_len));
            delete[] blob;
            if (!ok) continue;
        }

        AnimationEntry& e = _entries[_count];
        std::strncpy(e.name, name, sizeof(e.name) - 1);
        e.name[sizeof(e.name) - 1] = '\0';
        e.strip_length = strip_length;
        e.blob_len = static_cast<uint32_t>(blob_len);
        e.crc32 = crc;
        ++_count;
    }
}
