#pragma once

#include <cstdint>

// Program timeline units. The compiler rounds every boundary it schedules
// (event start and end, copy-op time, program length) to a whole
// millisecond once, and the blob carries those integers. The decoder,
// engine and playback then compare exact values, so adjacent events meet
// and a copy op lands on the frame its source ends. Effects still take
// float seconds; convert with seconds() at that boundary only.

// A position on the program timeline.
struct ProgramTime {
    uint32_t ms = 0;
};

// A span between two positions.
struct ProgramDuration {
    uint32_t ms = 0;
};

inline ProgramTime operator+(ProgramTime t, ProgramDuration d) { return {t.ms + d.ms}; }

// Caller guarantees a >= b.
inline ProgramDuration operator-(ProgramTime a, ProgramTime b) { return {a.ms - b.ms}; }

inline bool operator==(ProgramTime a, ProgramTime b) { return a.ms == b.ms; }
inline bool operator!=(ProgramTime a, ProgramTime b) { return a.ms != b.ms; }
inline bool operator< (ProgramTime a, ProgramTime b) { return a.ms <  b.ms; }
inline bool operator<=(ProgramTime a, ProgramTime b) { return a.ms <= b.ms; }
inline bool operator> (ProgramTime a, ProgramTime b) { return a.ms >  b.ms; }
inline bool operator>=(ProgramTime a, ProgramTime b) { return a.ms >= b.ms; }

inline float seconds(ProgramDuration d) { return static_cast<float>(d.ms) / 1000.0f; }
inline float seconds(ProgramTime t)     { return static_cast<float>(t.ms) / 1000.0f; }
