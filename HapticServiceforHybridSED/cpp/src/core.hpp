#pragma once
#include <array>
#include <algorithm>
#include <cstdint>
#include <deque>
#include <optional>
#include <vector>

namespace hybrid {
constexpr std::uint64_t marker = 0x5345445F4F534B;
using Report = std::array<std::uint8_t, 32>;
// WARM is an idle-ready observation, not a persistent session liveness bit.
inline bool sessionAlive(std::uint8_t flags) { return (flags & 0x01) != 0; }
inline void word(Report& r, unsigned offset, std::uint16_t value) {
    r[offset] = static_cast<std::uint8_t>(value);
    r[offset + 1] = static_cast<std::uint8_t>(value >> 8);
}
inline std::uint8_t crc8(const Report& r) {
    std::uint8_t crc = 0;
    for (unsigned i = 0; i < 31; ++i) {
        crc ^= r[i];
        for (unsigned b = 0; b < 8; ++b)
            crc = static_cast<std::uint8_t>((crc << 1) ^ ((crc & 0x80) ? 0x07 : 0));
    }
    return crc;
}
// Feature 0x10 and interrupt 0x33 carry the same capability layout at
// different offsets. Verify every report ID, including separate caps/config IDs.
inline bool capabilitiesMatch(const Report& r,bool feature,unsigned waveform) {
    const unsigned first=feature ? 6 : 10;
    const unsigned ids=first+8;
    const unsigned required=feature ? 0x86 : 0x83;
    const unsigned flags=r[first]|(r[first+1]<<8);
    return r[0]==(feature ? 0x10 : 0x81)&&r[1]==2&&r[2]==(feature ? 1 : 3)&&
        (flags&required)==required&&r[first+5]>=waveform&&r[ids]==32&&
        r[ids+1]==1&&r[ids+2]==2&&r[ids+3]==0x81&&r[ids+4]==0x10&&r[ids+5]==0x11;
}
inline Report base(std::uint8_t id, std::uint8_t op, std::uint16_t seq) {
    Report r{};
    r[0] = id; r[1] = 2; r[2] = op; r[3] = 1; // CRC enabled; no per-PLAY ACK
    word(r, 4, seq);
    return r;
}
inline Report control(std::uint8_t op, std::uint8_t value, std::uint16_t seq) {
    auto r = base(2, op, seq);
    r[6] = value; r[31] = crc8(r);
    return r;
}
inline Report play(std::uint16_t key, std::uint16_t age, std::uint8_t waveform,
                   std::uint8_t intensity, std::uint16_t seq) {
    auto r = base(1, 1, seq);
    word(r, 7, key); r[14] = waveform; r[15] = intensity;
    r[16] = 1; // touch phase: down
    word(r, 18, age); r[31] = crc8(r);
    return r;
}

struct Edge {
    std::uint8_t code{};
    std::uint16_t key{};
    bool down{};
    std::uint64_t tick{};
};
}
