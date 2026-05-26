#include <catch2/catch_test_macros.hpp>

#include <unistd.h>

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <string>
#include <utility>

#include "hardware_profile_store.h"
#include "sim/file_key_value_store.h"

namespace {

constexpr char STORAGE_ROOT_ENV[] = "ELEMENTS_SIM_STORAGE_ROOT";

std::filesystem::path unique_root_()
{
    static int counter = 0;
    return std::filesystem::temp_directory_path() /
           ("elements_key_value_store_" + std::to_string(::getpid()) + "_" +
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

std::filesystem::path kvstore_root_(const std::filesystem::path& root,
                                    const char* uid)
{
    return root / "simstorage" / uid / "kvstore";
}

}  // namespace

TEST_CASE("file key value store writes one JSON file per namespace",
          "[key_value_store]")
{
    ScopedStorageRoot env(unique_root_());
    FileKeyValueStore store("sim-1", "profile");

    CHECK(store.state() == KeyValueStoreState::NotReady);
    REQUIRE(store.put_u16("strip_len", 144));
    CHECK(store.state() == KeyValueStoreState::Ready);

    const auto path = kvstore_root_(env.root(), "sim-1") / "profile.json";
    CHECK(std::filesystem::is_regular_file(path));

    uint16_t strip_length = 0;
    REQUIRE(store.get_u16("strip_len", strip_length));
    CHECK(strip_length == 144);
}

TEST_CASE("file key value store persists typed values", "[key_value_store]")
{
    ScopedStorageRoot env(unique_root_());

    {
        FileKeyValueStore store("sim-1", "profile");
        REQUIRE(store.put_u8("color_order", 1));
        REQUIRE(store.put_u16("strip_len", 240));
        REQUIRE(store.put_f32("gamma", 2.8f));
    }

    FileKeyValueStore store("sim-1", "profile");
    uint8_t color_order = 0;
    uint16_t strip_length = 0;
    float gamma = 0.0f;

    REQUIRE(store.get_u8("color_order", color_order));
    REQUIRE(store.get_u16("strip_len", strip_length));
    REQUIRE(store.get_f32("gamma", gamma));
    CHECK(color_order == 1);
    CHECK(strip_length == 240);
    CHECK(gamma == 2.8f);
}

TEST_CASE("file key value store rejects missing keys and type mismatches",
          "[key_value_store]")
{
    ScopedStorageRoot env(unique_root_());
    FileKeyValueStore store("sim-1", "profile");

    REQUIRE(store.put_u16("strip_len", 144));

    uint8_t small = 0;
    uint16_t strip_length = 0;
    CHECK_FALSE(store.get_u8("strip_len", small));
    CHECK_FALSE(store.get_u16("missing", strip_length));
    CHECK(store.has_key("strip_len"));
    CHECK_FALSE(store.has_key("missing"));
}

TEST_CASE("file key value store round-trips strings including empty",
          "[key_value_store]")
{
    ScopedStorageRoot env(unique_root_());
    FileKeyValueStore store("sim-1", "profile");

    REQUIRE(store.put_str("ssid", "HomeWifi"));
    REQUIRE(store.put_str("open", ""));        // open networks store ""

    char buf[64];
    const int len_ssid = store.get_str("ssid", buf, sizeof(buf));
    REQUIRE(len_ssid == 8);
    CHECK(std::string(buf) == "HomeWifi");

    const int len_open = store.get_str("open", buf, sizeof(buf));
    REQUIRE(len_open == 0);
    CHECK(buf[0] == '\0');

    // Buffer too small for the value plus NUL.
    char tiny[4];
    CHECK(store.get_str("ssid", tiny, sizeof(tiny)) == -1);

    // Missing key returns -1, not 0.
    CHECK(store.get_str("missing", buf, sizeof(buf)) == -1);
}

TEST_CASE("file key value store removes keys", "[key_value_store]")
{
    ScopedStorageRoot env(unique_root_());
    FileKeyValueStore store("sim-1", "profile");

    REQUIRE(store.put_u8("color_order", 1));
    CHECK(store.remove("color_order"));
    CHECK_FALSE(store.remove("color_order"));
    CHECK_FALSE(store.has_key("color_order"));
}

TEST_CASE("file key value store faults on unsafe names", "[key_value_store]")
{
    ScopedStorageRoot env(unique_root_());

    FileKeyValueStore unsafe_uid("sim:1", "profile");
    CHECK(unsafe_uid.state() == KeyValueStoreState::Faulted);
    CHECK_FALSE(unsafe_uid.put_u8("color_order", 1));

    FileKeyValueStore unsafe_namespace("sim-1", "bad-name");
    CHECK(unsafe_namespace.state() == KeyValueStoreState::Faulted);
    CHECK_FALSE(unsafe_namespace.put_u8("color_order", 1));
}

TEST_CASE("file key value store enforces key name rules", "[key_value_store]")
{
    ScopedStorageRoot env(unique_root_());
    FileKeyValueStore store("sim-1", "profile");

    const std::string max_name(KEY_VALUE_STORE_MAX_NAME_SIZE, 'a');
    const std::string too_long(KEY_VALUE_STORE_MAX_NAME_SIZE + 1, 'a');

    CHECK(store.put_u8(max_name.c_str(), 1));
    CHECK_FALSE(store.put_u8(too_long.c_str(), 1));
    CHECK_FALSE(store.put_u8("bad-name", 1));
    CHECK_FALSE(store.put_u8("_bad", 1));
}

TEST_CASE("file key value store faults on invalid JSON", "[key_value_store]")
{
    ScopedStorageRoot env(unique_root_());
    const auto dir = kvstore_root_(env.root(), "sim-1");
    std::filesystem::create_directories(dir);
    {
        std::ofstream out(dir / "profile.json");
        out << "{invalid\n";
    }

    FileKeyValueStore store("sim-1", "profile");
    uint16_t strip_length = 0;
    CHECK_FALSE(store.get_u16("strip_len", strip_length));
    CHECK(store.state() == KeyValueStoreState::Faulted);
}

TEST_CASE("hardware profile stores through key value store", "[key_value_store]")
{
    ScopedStorageRoot env(unique_root_());

    const HardwareProfile expected(320, ColorOrder::BGR, 2.8f);
    {
        FileKeyValueStore store("sim-1", HARDWARE_PROFILE_KV_NAMESPACE);
        REQUIRE(save_hardware_profile(store, expected));
    }

    FileKeyValueStore store("sim-1", HARDWARE_PROFILE_KV_NAMESPACE);
    HardwareProfile actual;
    REQUIRE(load_hardware_profile(store, actual));
    CHECK(actual == expected);
}
