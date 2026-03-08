#pragma once

#include "controller_device.h"

#include <cstdint>
#include <map>
#include <string>
#include <unordered_map>
#include <vector>

enum class ControllerState { IDLE, LOADED, PLAYING, PAUSED, STOPPED, ENDED };

struct ControllerStrip {
    std::string strip_id;
    uint16_t length;
    ControllerDevice* device;   // non-owning
};

struct CompiledStripBlob {
    std::string strip_id;
    uint16_t length;
    std::vector<uint8_t> blob;
};

struct CompiledProgram {
    std::string artifact_id;
    float duration;
    std::vector<CompiledStripBlob> strips;   // canonical strip order
    bool loop = false;
};

struct ProgramFrame {
    uint32_t frame_index;
    float t_rel;
    std::vector<std::vector<uint8_t>> strips;  // canonical strip order
};

struct ControllerEvent {
    enum Kind { SESSION_STARTED, STATE_CHANGED, LOOPED, ERROR } kind;
    ControllerState state = ControllerState::IDLE;
    uint64_t session_id = 0;
    uint32_t epoch = 0;
    std::string message;
};

class SimController {
public:
    explicit SimController(std::vector<ControllerStrip> strips);

    bool load(const CompiledProgram& program);
    void play();
    void pause();
    void seek(float t_rel);
    void stop();

    void tick_once();

    ControllerState state() const;
    uint64_t session_id() const;
    uint32_t epoch() const;
    float duration() const;
    float current_t_rel() const;

    std::vector<ProgramFrame> drain_program_frames();
    std::vector<ControllerEvent> drain_events();

private:
    void queue_event(ControllerEvent::Kind kind, const std::string& msg = "");

    struct Bucket {
        uint32_t frame_index = 0;
        float t_rel = 0.0f;
        std::vector<std::vector<uint8_t>> strips;
        size_t present = 0;
    };

    std::vector<ControllerStrip> _strips;
    std::unordered_map<std::string, size_t> _strip_id_to_index;

    ControllerState _state = ControllerState::IDLE;
    uint64_t _session_id = 0;
    uint32_t _epoch = 0;
    uint16_t _gen = 0;
    float _duration = 0.0f;
    float _paused_t_rel = 0.0f;
    bool _loop = false;

    std::vector<uint16_t> _expected_gen;
    std::map<uint32_t, Bucket> _buckets;

    std::vector<ProgramFrame> _program_frames;
    std::vector<ControllerEvent> _events;
};
