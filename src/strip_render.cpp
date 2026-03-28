// strip_render — Offline CLI renderer. Reads blob from stdin, writes binary
// RGB frames to stdout.
//
// Usage: strip_render <strip_length> <fps>

#include "playback_device.h"

#include <cstdio>
#include <cstdlib>
#include <vector>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

class RenderDevice : public PlaybackDevice {
public:
    explicit RenderDevice(uint16_t len)
        : PlaybackDevice(/*gamma_enabled=*/false)
    {
        apply_hardware_profile(HardwareProfile{len});
    }

    void set_now(int64_t us) { _now = us; }
    int64_t now_mono() const override { return _now; }
protected:
    void output_frame(float t_rel) override {
        fwrite(&t_rel, sizeof(float), 1, stdout);
        fwrite(rgb_data(), 1, strip_length() * 3, stdout);
    }
private:
    int64_t _now = 0;
};

int main(int argc, char** argv)
{
    if (argc != 3) {
        fprintf(stderr, "Usage: %s <strip_length> <fps>\n", argv[0]);
        return 1;
    }

    int strip_length = atoi(argv[1]);
    int fps = atoi(argv[2]);
    if (strip_length < 1 || fps < 1) {
        fprintf(stderr, "strip_length and fps must be positive integers\n");
        return 1;
    }

#ifdef _WIN32
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
#endif

    // Read blob from stdin
    std::vector<uint8_t> blob;
    uint8_t buf[4096];
    while (!feof(stdin)) {
        size_t n = fread(buf, 1, sizeof(buf), stdin);
        if (n > 0)
            blob.insert(blob.end(), buf, buf + n);
    }

    if (blob.empty()) {
        fprintf(stderr, "No blob data on stdin\n");
        return 1;
    }

    RenderDevice device((uint16_t)strip_length);
    if (!device.handle_load(blob.data(), blob.size(), 1)) {
        fprintf(stderr, "Failed to decode blob\n");
        return 1;
    }

    device.set_now(0);
    device.handle_start(0);

    for (int64_t i = 1; ; i++) {
        device.set_now((i * 1000000LL) / fps);
        if (!device.tick_once())
            break;
    }

    fflush(stdout);
    return 0;
}
