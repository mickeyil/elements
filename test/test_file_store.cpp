#include <catch2/catch_test_macros.hpp>

#include <unistd.h>

#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <string>
#include <utility>
#include <vector>

#include "sim/posix_file_store.h"

namespace {

constexpr char STORAGE_ROOT_ENV[] = "ELEMENTS_SIM_STORAGE_ROOT";

std::filesystem::path unique_root_()
{
    static int counter = 0;
    return std::filesystem::temp_directory_path() /
           ("elements_file_store_" + std::to_string(::getpid()) + "_" +
            std::to_string(counter++));
}

class ScopedStorageRoot {
public:
    explicit ScopedStorageRoot(std::filesystem::path root)
        : _root(std::move(root))
    {
        const char* old = std::getenv(STORAGE_ROOT_ENV);
        if (old != nullptr) {
            _had_old = true;
            _old = old;
        }
        std::filesystem::remove_all(_root);
        setenv(STORAGE_ROOT_ENV, _root.string().c_str(), 1);
    }

    ~ScopedStorageRoot()
    {
        if (_had_old) {
            setenv(STORAGE_ROOT_ENV, _old.c_str(), 1);
        } else {
            unsetenv(STORAGE_ROOT_ENV);
        }
        std::error_code ignored;
        std::filesystem::remove_all(_root, ignored);
    }

    const std::filesystem::path& root() const { return _root; }

private:
    std::filesystem::path _root;
    bool _had_old = false;
    std::string _old;
};

std::filesystem::path device_root_(const std::filesystem::path& root,
                                   const char* uid)
{
    return root / "simstorage" / uid / "filestore";
}

}  // namespace

TEST_CASE("posix file store writes under the sim storage root", "[file_store]")
{
    ScopedStorageRoot env(unique_root_());
    PosixFileStore store("sim-1");

    CHECK(store.state() == FileStoreState::NotReady);

    const uint8_t data[] = {1, 2, 3, 4};
    REQUIRE(store.write("flashy_green.anim", data, sizeof(data)));
    CHECK(store.state() == FileStoreState::Ready);

    const auto dir = device_root_(env.root(), "sim-1");
    CHECK(std::filesystem::is_regular_file(dir / "flashy_green.anim"));
    CHECK_FALSE(std::filesystem::exists(dir / ".flashy_green.anim"));
    CHECK(store.size("flashy_green.anim") == 4);

    uint8_t out[8] = {};
    REQUIRE(store.read("flashy_green.anim", out, sizeof(out)) == 4);
    CHECK(std::memcmp(out, data, sizeof(data)) == 0);
}

TEST_CASE("posix file store overwrites atomically through a visible temp name",
          "[file_store]")
{
    ScopedStorageRoot env(unique_root_());
    PosixFileStore store("sim-1");

    const uint8_t old_data[] = {1, 2, 3};
    const uint8_t new_data[] = {9, 8};
    REQUIRE(store.write("playlist.txt", old_data, sizeof(old_data)));
    REQUIRE(store.write("playlist.txt", new_data, sizeof(new_data)));

    uint8_t out[8] = {};
    REQUIRE(store.read("playlist.txt", out, sizeof(out)) == 2);
    CHECK(out[0] == 9);
    CHECK(out[1] == 8);

    const auto dir = device_root_(env.root(), "sim-1");
    CHECK_FALSE(std::filesystem::exists(dir / ".playlist.txt"));
}

TEST_CASE("posix file store supports empty files and removal", "[file_store]")
{
    ScopedStorageRoot env(unique_root_());
    PosixFileStore store("sim-1");

    REQUIRE(store.write("empty.bin", nullptr, 0));
    CHECK(store.size("empty.bin") == 0);
    CHECK(store.read("empty.bin", nullptr, 0) == 0);

    CHECK(store.remove("empty.bin"));
    CHECK(store.size("empty.bin") == -1);
    CHECK_FALSE(store.remove("empty.bin"));
}

TEST_CASE("posix file store enforces the flat filename limit", "[file_store]")
{
    ScopedStorageRoot env(unique_root_());
    PosixFileStore store("sim-1");

    const std::string max_name(FILE_STORE_MAX_NAME_SIZE, 'a');
    const std::string too_long(FILE_STORE_MAX_NAME_SIZE + 1, 'a');
    const uint8_t data[] = {1};

    CHECK(store.write(max_name.c_str(), data, sizeof(data)));
    CHECK(store.size(max_name.c_str()) == 1);

    CHECK_FALSE(store.write(too_long.c_str(), data, sizeof(data)));
    CHECK(store.size(too_long.c_str()) == -1);
}

TEST_CASE("posix file store reserves leading-dot names for temp files", "[file_store]")
{
    ScopedStorageRoot env(unique_root_());
    PosixFileStore store("sim-1");

    const uint8_t data[] = {1};
    CHECK_FALSE(store.write(".flashy_green.anim", data, sizeof(data)));
    CHECK(store.size(".flashy_green.anim") == -1);
    CHECK_FALSE(store.remove(".flashy_green.anim"));
}

TEST_CASE("posix file store faults on an unsafe sim uid", "[file_store]")
{
    ScopedStorageRoot env(unique_root_());

    const uint8_t data[] = {1};
    for (const char* uid : {"sim:1", ".sim-1", "-sim-1", "_sim-1"}) {
        PosixFileStore store(uid);

        CHECK(store.state() == FileStoreState::Faulted);
        CHECK_FALSE(store.write("x.bin", data, sizeof(data)));
        CHECK(store.read("x.bin", nullptr, 0) == -1);
    }
    CHECK_FALSE(std::filesystem::exists(env.root() / "simstorage"));
}

TEST_CASE("posix file store latches root creation failure", "[file_store]")
{
    const auto root = unique_root_();
    ScopedStorageRoot env(root);

    {
        std::ofstream blocker(root);
        blocker << "not a directory\n";
    }

    PosixFileStore store("sim-1");
    const uint8_t data[] = {1};
    CHECK_FALSE(store.write("x.bin", data, sizeof(data)));
    CHECK(store.state() == FileStoreState::Faulted);

    std::filesystem::remove(root);
    CHECK_FALSE(store.write("x.bin", data, sizeof(data)));
    CHECK(store.state() == FileStoreState::Faulted);
}
