#include <catch2/catch_test_macros.hpp>

#include <unistd.h>

#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <string>
#include <utility>

#include "platform/sim/file_key_value_store.h"
#include "platform/wifi_cred_store.h"

namespace {

constexpr char STORAGE_ROOT_ENV[] = "ELEMENTS_SIM_STORAGE_ROOT";
constexpr char SIM_UID[] = "sim-1";

std::filesystem::path unique_root_()
{
    static int counter = 0;
    return std::filesystem::temp_directory_path() /
           ("elements_wifi_cred_" + std::to_string(::getpid()) + "_" +
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
        if (_had_old) setenv(STORAGE_ROOT_ENV, _old.c_str(), 1);
        else          unsetenv(STORAGE_ROOT_ENV);
        std::error_code ignored;
        std::filesystem::remove_all(_root, ignored);
    }

private:
    std::filesystem::path _root;
    bool _had_old = false;
    std::string _old;
};

}  // namespace

TEST_CASE("wifi cred store starts empty", "[wifi_cred_store]")
{
    ScopedStorageRoot env(unique_root_());
    FileKeyValueStore kv(SIM_UID, "wifi");
    WifiCredStore store(kv);

    CHECK(store.count() == 0);
    CHECK(store.empty());
    CHECK_FALSE(store.contains("HomeWifi"));

    char password[WIFI_PASSWORD_BUF_SIZE];
    CHECK_FALSE(store.get("HomeWifi", password, sizeof(password)));
}

TEST_CASE("wifi cred store put and get a credential", "[wifi_cred_store]")
{
    ScopedStorageRoot env(unique_root_());
    FileKeyValueStore kv(SIM_UID, "wifi");
    WifiCredStore store(kv);

    REQUIRE(store.put("HomeWifi", "secret-pass"));
    CHECK(store.count() == 1);
    CHECK(store.contains("HomeWifi"));

    char password[WIFI_PASSWORD_BUF_SIZE];
    REQUIRE(store.get("HomeWifi", password, sizeof(password)));
    CHECK(std::string(password) == "secret-pass");
}

TEST_CASE("wifi cred store put overwrites existing ssid in place",
          "[wifi_cred_store]")
{
    ScopedStorageRoot env(unique_root_());
    FileKeyValueStore kv(SIM_UID, "wifi");
    WifiCredStore store(kv);

    REQUIRE(store.put("HomeWifi", "first-pass"));
    REQUIRE(store.put("HomeWifi", "second-pass"));
    CHECK(store.count() == 1);

    char password[WIFI_PASSWORD_BUF_SIZE];
    REQUIRE(store.get("HomeWifi", password, sizeof(password)));
    CHECK(std::string(password) == "second-pass");
}

TEST_CASE("wifi cred store rejects invalid input", "[wifi_cred_store]")
{
    ScopedStorageRoot env(unique_root_());
    FileKeyValueStore kv(SIM_UID, "wifi");
    WifiCredStore store(kv);

    const std::string too_long_ssid(WIFI_SSID_MAX_LEN + 1, 'a');
    const std::string too_long_pwd(WIFI_PASSWORD_MAX_LEN + 1, 'b');

    CHECK_FALSE(store.put("", "secret"));
    CHECK_FALSE(store.put(too_long_ssid.c_str(), "secret"));
    CHECK_FALSE(store.put("HomeWifi", too_long_pwd.c_str()));
    CHECK(store.put("OpenNetwork", ""));   // open networks allowed
}

TEST_CASE("wifi cred store rejects put when full", "[wifi_cred_store]")
{
    ScopedStorageRoot env(unique_root_());
    FileKeyValueStore kv(SIM_UID, "wifi");
    WifiCredStore store(kv);

    for (size_t i = 0; i < MAX_STORED_WIFI_CREDS; ++i) {
        const std::string ssid = "net" + std::to_string(i);
        REQUIRE(store.put(ssid.c_str(), "pwd"));
    }
    CHECK(store.count() == MAX_STORED_WIFI_CREDS);

    CHECK_FALSE(store.put("OneMore", "pwd"));

    // Overwriting an existing entry should still work at the cap.
    REQUIRE(store.put("net0", "different"));
    CHECK(store.count() == MAX_STORED_WIFI_CREDS);
}

TEST_CASE("wifi cred store removes by name with swap-with-last",
          "[wifi_cred_store]")
{
    ScopedStorageRoot env(unique_root_());
    FileKeyValueStore kv(SIM_UID, "wifi");
    WifiCredStore store(kv);

    REQUIRE(store.put("A", "pa"));
    REQUIRE(store.put("B", "pb"));
    REQUIRE(store.put("C", "pc"));

    REQUIRE(store.remove("B"));
    CHECK(store.count() == 2);
    CHECK_FALSE(store.contains("B"));
    CHECK(store.contains("A"));
    CHECK(store.contains("C"));

    // "C" was the tail; after swap-with-last it now lives where "B"
    // was. Either order is acceptable, but A must still be findable.
    char pwd[WIFI_PASSWORD_BUF_SIZE];
    REQUIRE(store.get("A", pwd, sizeof(pwd)));
    CHECK(std::string(pwd) == "pa");
    REQUIRE(store.get("C", pwd, sizeof(pwd)));
    CHECK(std::string(pwd) == "pc");

    CHECK_FALSE(store.remove("nope"));
}

TEST_CASE("wifi cred store remembers last ssid", "[wifi_cred_store]")
{
    ScopedStorageRoot env(unique_root_());
    FileKeyValueStore kv(SIM_UID, "wifi");
    WifiCredStore store(kv);

    char buf[WIFI_SSID_BUF_SIZE];
    CHECK_FALSE(store.last_ssid(buf, sizeof(buf)));

    REQUIRE(store.set_last_ssid("HomeWifi"));
    REQUIRE(store.last_ssid(buf, sizeof(buf)));
    CHECK(std::string(buf) == "HomeWifi");

    CHECK_FALSE(store.set_last_ssid(""));   // invalid
}

TEST_CASE("wifi cred store merges from a compiled array", "[wifi_cred_store]")
{
    ScopedStorageRoot env(unique_root_());
    FileKeyValueStore kv(SIM_UID, "wifi");
    WifiCredStore store(kv);

    const WifiCredential seeds[] = {
        {"NetOne", "pwd1"},
        {"NetTwo", "pwd2"},
        {"NetThree", "pwd3"},
    };
    REQUIRE(store.merge_from(seeds, 3));
    CHECK(store.count() == 3);
    CHECK(store.contains("NetOne"));
    CHECK(store.contains("NetTwo"));
    CHECK(store.contains("NetThree"));

    char pwd[WIFI_PASSWORD_BUF_SIZE];
    REQUIRE(store.get("NetTwo", pwd, sizeof(pwd)));
    CHECK(std::string(pwd) == "pwd2");
}

TEST_CASE("merge_from reconciles a non-empty store", "[wifi_cred_store]")
{
    ScopedStorageRoot env(unique_root_());
    FileKeyValueStore kv(SIM_UID, "wifi");
    WifiCredStore store(kv);

    // Existing state: one compiled match (old password) and one user-
    // provisioned credential not in the compiled list.
    REQUIRE(store.put("NetOne", "old-pwd"));
    REQUIRE(store.put("UserNet", "user-pwd"));
    CHECK(store.count() == 2);

    const WifiCredential compiled[] = {
        {"NetOne", "new-pwd"},   // password changed
        {"NetTwo", "pwd2"},      // new SSID
    };
    REQUIRE(store.merge_from(compiled, 2));

    char pwd[WIFI_PASSWORD_BUF_SIZE];

    // Changed password updated.
    REQUIRE(store.get("NetOne", pwd, sizeof(pwd)));
    CHECK(std::string(pwd) == "new-pwd");

    // New SSID added.
    CHECK(store.contains("NetTwo"));
    REQUIRE(store.get("NetTwo", pwd, sizeof(pwd)));
    CHECK(std::string(pwd) == "pwd2");

    // Unrelated user-provisioned credential untouched.
    CHECK(store.contains("UserNet"));
    REQUIRE(store.get("UserNet", pwd, sizeof(pwd)));
    CHECK(std::string(pwd) == "user-pwd");

    CHECK(store.count() == 3);
}

TEST_CASE("merge_from is a no-op when nothing changed", "[wifi_cred_store]")
{
    ScopedStorageRoot env(unique_root_());
    FileKeyValueStore kv(SIM_UID, "wifi");
    WifiCredStore store(kv);

    const WifiCredential compiled[] = {
        {"NetOne", "pwd1"},
        {"NetTwo", "pwd2"},
    };
    REQUIRE(store.merge_from(compiled, 2));
    REQUIRE(store.merge_from(compiled, 2));   // second merge: all identical

    CHECK(store.count() == 2);
    char pwd[WIFI_PASSWORD_BUF_SIZE];
    REQUIRE(store.get("NetOne", pwd, sizeof(pwd)));
    CHECK(std::string(pwd) == "pwd1");
}

TEST_CASE("wifi cred store persists across instances", "[wifi_cred_store]")
{
    ScopedStorageRoot env(unique_root_());

    {
        FileKeyValueStore kv(SIM_UID, "wifi");
        WifiCredStore store(kv);
        REQUIRE(store.put("HomeWifi", "secret"));
        REQUIRE(store.set_last_ssid("HomeWifi"));
    }

    FileKeyValueStore kv(SIM_UID, "wifi");
    WifiCredStore store(kv);
    CHECK(store.count() == 1);

    char ssid[WIFI_SSID_BUF_SIZE];
    REQUIRE(store.last_ssid(ssid, sizeof(ssid)));
    CHECK(std::string(ssid) == "HomeWifi");

    char pwd[WIFI_PASSWORD_BUF_SIZE];
    REQUIRE(store.get("HomeWifi", pwd, sizeof(pwd)));
    CHECK(std::string(pwd) == "secret");
}
