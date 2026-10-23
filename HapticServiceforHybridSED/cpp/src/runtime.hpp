#pragma once
#include <windows.h>
#include <atomic>
#include <string>
#include "core.hpp"

namespace hybrid {
struct Options {
    bool mock=false;
    unsigned waveform=1, intensity=1;
    unsigned keepaliveMs=250;
    std::string statusMode="auto";
    std::string threadPriority="above-normal";
    std::string idleWaitMode="fixed";
    bool mockFeatureUnavailable=false;
    bool mockQuiet=false;
    bool mockTrace=false;
    bool latencyTrace=false; // Explicit diagnostic opt-in, also valid with real HID.
    unsigned mockStatusDelayMs=0;
    std::wstring sedPipe=L"HybridSedHaptic";
    std::wstring logFile;
};
extern std::atomic_bool stopping;
extern std::atomic_bool paused;
void log(const std::string& text);
void configureLog(const std::wstring& path);
int runBackend(const Options& options);
int runControl(const Options& options,const std::string& command);
}
