#include <catch2/catch_test_macros.hpp>

#include <cstring>
#include <map>
#include <string>
#include <vector>

#include "app/animation_store.h"
#include "app/crc32.h"
#include "core/decoder.h"
#include "app/link_protocol.h"

namespace {

// In-memory FileStore. Pins AnimationStore to the FileStore contract
// (no POSIX behavior leaks in) and allows fault injection plus
// read-size accounting for the boot-cost tests.
class FakeFileStore : public FileStore
{
public:
    FileStoreState state() const override { return FileStoreState::Ready; }

    bool write(const char* name, const uint8_t* src, size_t len) override
    {
        if (fail_writes) return false;
        _files[name].assign(src, src + len);
        return true;
    }

    int size(const char* name) override
    {
        auto it = _files.find(name);
        if (it == _files.end()) return -1;
        return static_cast<int>(it->second.size());
    }

    int read(const char* name, uint8_t* dst, size_t max_len) override
    {
        if (fail_reads) return -1;
        auto it = _files.find(name);
        if (it == _files.end()) return -1;
        const size_t n = std::min(max_len, it->second.size());
        std::memcpy(dst, it->second.data(), n);
        bytes_read_of[name] += n;
        return static_cast<int>(n);
    }

    bool remove(const char* name) override
    {
        if (fail_removes) return false;
        return _files.erase(name) > 0;
    }

    bool has(const std::string& name) const { return _files.count(name) > 0; }

    std::string text(const std::string& name) const
    {
        auto it = _files.find(name);
        if (it == _files.end()) return "";
        return std::string(it->second.begin(), it->second.end());
    }

    void put_text(const std::string& name, const std::string& content)
    {
        _files[name].assign(content.begin(), content.end());
    }

    bool fail_writes = false;
    bool fail_reads = false;
    bool fail_removes = false;
    std::map<std::string, size_t> bytes_read_of;

private:
    std::map<std::string, std::vector<uint8_t>> _files;
};

// A blob with a valid 20-byte header; the store never reads past it.
// `filler` varies content (and thus crc) and length.
std::vector<uint8_t> make_blob(uint16_t strip_length,
                               bool requires_sync = false,
                               size_t filler = 4)
{
    std::vector<uint8_t> b;
    b.insert(b.end(), BLOB_MAGIC, BLOB_MAGIC + 4);
    b.push_back(BLOB_VERSION);
    b.push_back(requires_sync ? 0x01 : 0x00);          // flags
    b.push_back(30);                                    // target_fps
    b.push_back(0);                                     // layer_count
    b.push_back(uint8_t(strip_length & 0xFF));
    b.push_back(uint8_t(strip_length >> 8));
    b.insert(b.end(), 6, 0);                            // buffers/views/copy_ops
    const uint8_t duration[4] = { 0x00, 0x00, 0x80, 0x3F };  // 1.0f
    b.insert(b.end(), duration, duration + 4);
    for (size_t i = 0; i < filler; ++i) b.push_back(uint8_t(i * 7 + 1));
    return b;
}

bool store_blob(AnimationStore& s, const char* name,
                const std::vector<uint8_t>& blob)
{
    return s.store(name, blob.data(), blob.size());
}

std::vector<std::string> order_of(const AnimationStore& s)
{
    std::vector<std::string> names;
    for (size_t i = 0; i < s.count(); ++i) names.push_back(s.entry(i).name);
    return names;
}

}  // namespace

TEST_CASE("fresh store is empty")
{
    FakeFileStore files;
    AnimationStore store(files);
    CHECK(store.count() == 0);
}

TEST_CASE("store records entry metadata and writes both files")
{
    FakeFileStore files;
    AnimationStore store(files);

    const auto blob = make_blob(150);
    REQUIRE(store_blob(store, "comet", blob));

    REQUIRE(store.count() == 1);
    const AnimationEntry e = store.entry(0);
    CHECK(std::strcmp(e.name, "comet") == 0);
    CHECK(e.strip_length == 150);
    CHECK(e.blob_len == blob.size());
    CHECK(e.crc32 == crc32_ieee(blob.data(), blob.size()));

    CHECK(files.has("comet.anim"));
    char expected_line[64];
    std::snprintf(expected_line, sizeof(expected_line), "comet %08lx\n",
                  static_cast<unsigned long>(e.crc32));
    CHECK(files.text("playlist.txt") == expected_line);
}

TEST_CASE("new names append to the play order")
{
    FakeFileStore files;
    AnimationStore store(files);

    REQUIRE(store_blob(store, "a", make_blob(10)));
    REQUIRE(store_blob(store, "b", make_blob(10, false, 8)));
    REQUIRE(store_blob(store, "c", make_blob(10, false, 12)));

    CHECK(order_of(store) == std::vector<std::string>{"a", "b", "c"});
}

TEST_CASE("overwrite updates the blob in place and keeps the order")
{
    FakeFileStore files;
    AnimationStore store(files);

    REQUIRE(store_blob(store, "a", make_blob(10)));
    REQUIRE(store_blob(store, "b", make_blob(10)));

    const auto updated = make_blob(20, false, 100);
    REQUIRE(store_blob(store, "a", updated));

    REQUIRE(store.count() == 2);
    CHECK(order_of(store) == std::vector<std::string>{"a", "b"});
    const AnimationEntry e = store.entry(0);
    CHECK(e.strip_length == 20);
    CHECK(e.blob_len == updated.size());
    CHECK(e.crc32 == crc32_ieee(updated.data(), updated.size()));
}

TEST_CASE("store rejects invalid names")
{
    FakeFileStore files;
    AnimationStore store(files);
    const auto blob = make_blob(10);

    CHECK_FALSE(store.store(nullptr, blob.data(), blob.size()));
    CHECK_FALSE(store_blob(store, "", blob));
    CHECK_FALSE(store_blob(store, ".", blob));
    CHECK_FALSE(store_blob(store, "..", blob));
    CHECK_FALSE(store_blob(store, ".hidden", blob));
    CHECK_FALSE(store_blob(store, "a/b", blob));
    CHECK_FALSE(store_blob(store, "a\\b", blob));
    CHECK_FALSE(store_blob(store, "a b", blob));
    CHECK_FALSE(store_blob(store, "name!", blob));
    CHECK_FALSE(store_blob(store, "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456", blob));

    CHECK(store_blob(store, "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345", blob));
    CHECK(store_blob(store, "dots.dashes-under_scores.ok", blob));
    CHECK(store.count() == 2);
}

TEST_CASE("store rejects malformed and synced blobs")
{
    FakeFileStore files;
    AnimationStore store(files);

    const auto good = make_blob(10);
    CHECK_FALSE(store.store("a", nullptr, good.size()));

    auto short_blob = std::vector<uint8_t>(good.begin(), good.begin() + 19);
    CHECK_FALSE(store.store("a", short_blob.data(), short_blob.size()));

    auto bad_magic = good;
    bad_magic[0] = 'X';
    CHECK_FALSE(store.store("a", bad_magic.data(), bad_magic.size()));

    auto bad_version = good;
    bad_version[4] = BLOB_VERSION + 1;
    CHECK_FALSE(store.store("a", bad_version.data(), bad_version.size()));

    auto reserved_flags = good;
    reserved_flags[5] = 0x02;
    CHECK_FALSE(store.store("a", reserved_flags.data(), reserved_flags.size()));

    const auto synced = make_blob(10, true);
    CHECK_FALSE(store.store("a", synced.data(), synced.size()));

    const auto oversize = make_blob(10, false, MAX_BLOB_BYTES);
    CHECK_FALSE(store.store("a", oversize.data(), oversize.size()));

    CHECK(store.count() == 0);
}

TEST_CASE("a full store rejects new names but still allows overwrites")
{
    FakeFileStore files;
    AnimationStore store(files);

    char name[8];
    for (size_t i = 0; i < MAX_STORED_ANIMATIONS; ++i) {
        std::snprintf(name, sizeof(name), "a%zu", i);
        REQUIRE(store_blob(store, name, make_blob(10)));
    }
    REQUIRE(store.count() == MAX_STORED_ANIMATIONS);

    CHECK_FALSE(store_blob(store, "overflow", make_blob(10)));
    CHECK(store_blob(store, "a3", make_blob(33)));
    CHECK(store.count() == MAX_STORED_ANIMATIONS);
    CHECK(store.entry(3).strip_length == 33);
}

TEST_CASE("erase drops the entry, compacts the order, and removes the file")
{
    FakeFileStore files;
    AnimationStore store(files);

    REQUIRE(store_blob(store, "a", make_blob(10)));
    REQUIRE(store_blob(store, "b", make_blob(10)));
    REQUIRE(store_blob(store, "c", make_blob(10)));

    REQUIRE(store.erase("b"));
    CHECK(order_of(store) == std::vector<std::string>{"a", "c"});
    CHECK_FALSE(files.has("b.anim"));
    CHECK(files.text("playlist.txt").find("b") == std::string::npos);

    CHECK_FALSE(store.erase("b"));
    CHECK_FALSE(store.erase("never-stored"));
    CHECK_FALSE(store.erase(nullptr));
}

TEST_CASE("set_order accepts exactly a permutation")
{
    FakeFileStore files;
    AnimationStore store(files);

    REQUIRE(store_blob(store, "a", make_blob(10)));
    REQUIRE(store_blob(store, "b", make_blob(10)));
    REQUIRE(store_blob(store, "c", make_blob(10)));

    const char* good[] = { "c", "a", "b" };
    REQUIRE(store.set_order(good, 3));
    CHECK(order_of(store) == std::vector<std::string>{"c", "a", "b"});

    const char* too_few[] = { "a", "b" };
    CHECK_FALSE(store.set_order(too_few, 2));

    const char* unknown[] = { "c", "a", "x" };
    CHECK_FALSE(store.set_order(unknown, 3));

    const char* duplicate[] = { "c", "c", "a" };
    CHECK_FALSE(store.set_order(duplicate, 3));

    CHECK_FALSE(store.set_order(nullptr, 3));

    // A failed set_order leaves the order untouched.
    CHECK(order_of(store) == std::vector<std::string>{"c", "a", "b"});
}

TEST_CASE("set_order persists: a rebuilt store sees the new order")
{
    FakeFileStore files;
    {
        AnimationStore store(files);
        REQUIRE(store_blob(store, "a", make_blob(10)));
        REQUIRE(store_blob(store, "b", make_blob(10)));
        const char* order[] = { "b", "a" };
        REQUIRE(store.set_order(order, 2));
    }
    AnimationStore rebuilt(files);
    CHECK(order_of(rebuilt) == std::vector<std::string>{"b", "a"});
}

TEST_CASE("read_blob round-trips and validates length")
{
    FakeFileStore files;
    AnimationStore store(files);

    const auto blob = make_blob(10, false, 60);
    REQUIRE(store_blob(store, "a", blob));

    std::vector<uint8_t> out(blob.size());
    REQUIRE(store.read_blob("a", out.data(), out.size()));
    CHECK(out == blob);

    CHECK_FALSE(store.read_blob("a", out.data(), out.size() - 1));
    CHECK_FALSE(store.read_blob("a", nullptr, out.size()));
    CHECK_FALSE(store.read_blob("missing", out.data(), out.size()));
}

TEST_CASE("boot rebuilds the index from cached crcs without blob reads")
{
    FakeFileStore files;
    uint32_t crc_a = 0;
    size_t len_a = 0;
    {
        AnimationStore store(files);
        const auto a = make_blob(150, false, 200);
        const auto b = make_blob(60, false, 300);
        REQUIRE(store_blob(store, "a", a));
        REQUIRE(store_blob(store, "b", b));
        crc_a = crc32_ieee(a.data(), a.size());
        len_a = a.size();
    }

    files.bytes_read_of.clear();
    AnimationStore rebuilt(files);

    REQUIRE(rebuilt.count() == 2);
    const AnimationEntry e = rebuilt.entry(0);
    CHECK(std::strcmp(e.name, "a") == 0);
    CHECK(e.strip_length == 150);
    CHECK(e.blob_len == len_a);
    CHECK(e.crc32 == crc_a);

    // Cached crcs mean boot peeks only the 20-byte header of each blob.
    CHECK(files.bytes_read_of["a.anim"] == 20);
    CHECK(files.bytes_read_of["b.anim"] == 20);
}

TEST_CASE("boot recomputes a missing or garbled crc from the blob")
{
    FakeFileStore files;
    const auto blob = make_blob(10, false, 50);
    {
        AnimationStore store(files);
        REQUIRE(store_blob(store, "a", blob));
        REQUIRE(store_blob(store, "b", blob));
    }
    files.put_text("playlist.txt", "a\nb not-hex\n");

    AnimationStore rebuilt(files);
    REQUIRE(rebuilt.count() == 2);
    CHECK(rebuilt.entry(0).crc32 == crc32_ieee(blob.data(), blob.size()));
    CHECK(rebuilt.entry(1).crc32 == crc32_ieee(blob.data(), blob.size()));
    CHECK(files.bytes_read_of["a.anim"] > 20);
}

TEST_CASE("boot skips bad playlist lines and orphan files")
{
    FakeFileStore files;
    const auto blob = make_blob(10);
    const auto synced = make_blob(10, true);
    {
        AnimationStore store(files);
        REQUIRE(store_blob(store, "good", blob));
        REQUIRE(store_blob(store, "dup", blob));
    }
    // Orphan blob not named by the playlist.
    files.write("orphan.anim", blob.data(), blob.size());
    // Hand-copied synced blob and a corrupt file, both named.
    files.write("synced.anim", synced.data(), synced.size());
    files.put_text("corrupt.anim", "not a blob at all, far too short");
    files.put_text("playlist.txt",
                   "missing\n"
                   ".bad/name\n"
                   "synced\n"
                   "corrupt\n"
                   "good\r\n"
                   "\n"
                   "dup\n"
                   "dup\n");

    AnimationStore rebuilt(files);
    CHECK(order_of(rebuilt) == std::vector<std::string>{"good", "dup"});
}

TEST_CASE("a failed playlist write resyncs the index from disk")
{
    FakeFileStore files;
    AnimationStore store(files);

    REQUIRE(store_blob(store, "a", make_blob(10)));
    REQUIRE(store_blob(store, "b", make_blob(10)));

    files.fail_writes = true;
    CHECK_FALSE(store_blob(store, "c", make_blob(10)));
    CHECK_FALSE(store.erase("a"));
    const char* order[] = { "b", "a" };
    CHECK_FALSE(store.set_order(order, 2));
    files.fail_writes = false;

    CHECK(order_of(store) == std::vector<std::string>{"a", "b"});
    CHECK(store_blob(store, "c", make_blob(10)));
    CHECK(order_of(store) == std::vector<std::string>{"a", "b", "c"});
}

TEST_CASE("erase succeeds even when the blob file fails to delete")
{
    FakeFileStore files;
    AnimationStore store(files);
    REQUIRE(store_blob(store, "a", make_blob(10)));

    files.fail_removes = true;
    CHECK(store.erase("a"));
    CHECK(store.count() == 0);

    // The leftover file is an orphan; a rebuilt store ignores it.
    CHECK(files.has("a.anim"));
    AnimationStore rebuilt(files);
    CHECK(rebuilt.count() == 0);
}
