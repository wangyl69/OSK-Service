#include "runtime.hpp"
#include <wtsapi32.h>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <vector>

namespace {
using namespace hybrid;
constexpr wchar_t serviceName[]=L"HybridSedHapticService";
SERVICE_STATUS_HANDLE statusHandle=nullptr;
SERVICE_STATUS status{};
Options serviceOptions;
std::atomic_uint pauseMask=0;
void update(DWORD state,DWORD error=NO_ERROR) {
    status.dwServiceType=SERVICE_WIN32_OWN_PROCESS;
    status.dwCurrentState=state;
    status.dwWin32ExitCode=error;
    status.dwControlsAccepted=state==SERVICE_RUNNING ? SERVICE_ACCEPT_STOP|SERVICE_ACCEPT_SHUTDOWN|
        SERVICE_ACCEPT_POWEREVENT|SERVICE_ACCEPT_SESSIONCHANGE : 0;
    status.dwWaitHint=state==SERVICE_STOP_PENDING ? 10000 : 0;
    SetServiceStatus(statusHandle,&status);
}
void setPause(unsigned bit,bool value) {
    if(value) pauseMask.fetch_or(bit); else pauseMask.fetch_and(~bit);
    paused=pauseMask.load()!=0;
}
DWORD WINAPI handler(DWORD control,DWORD event,void* data,void*) {
    if(control==SERVICE_CONTROL_STOP||control==SERVICE_CONTROL_SHUTDOWN) {
        update(SERVICE_STOP_PENDING);stopping=true;
    }else if(control==SERVICE_CONTROL_POWEREVENT) {
        if(event==PBT_APMSUSPEND) setPause(1,true);
        else if(event==PBT_APMRESUMEAUTOMATIC||event==PBT_APMRESUMESUSPEND) setPause(1,false);
    }else if(control==SERVICE_CONTROL_SESSIONCHANGE) {
        auto info=static_cast<WTSSESSION_NOTIFICATION*>(data);
        if(info && info->dwSessionId==WTSGetActiveConsoleSessionId()) {
            if(event==WTS_SESSION_LOCK||event==WTS_SESSION_LOGOFF) setPause(2,true);
            else if(event==WTS_SESSION_UNLOCK||event==WTS_SESSION_LOGON) setPause(2,false);
        }
    }else if(control==SERVICE_CONTROL_INTERROGATE) update(status.dwCurrentState);
    return NO_ERROR;
}
void WINAPI serviceMain(DWORD,wchar_t**) {
    statusHandle=RegisterServiceCtrlHandlerExW(serviceName,handler,nullptr);
    if(!statusHandle) return;
    update(SERVICE_START_PENDING);
    try {
        if(serviceOptions.logFile.empty()) {
            wchar_t dir[32768]{};
            auto count=GetEnvironmentVariableW(L"ProgramData",dir,32768);
            if(!count||count>=32768) throw std::runtime_error("ProgramData unavailable");
            serviceOptions.logFile=(std::filesystem::path(dir)/L"HybridSedHaptic"/L"service.log").wstring();
        }
        configureLog(serviceOptions.logFile);
        update(SERVICE_RUNNING);
        runBackend(serviceOptions);
        update(SERVICE_STOPPED);
    }catch(const std::exception& e){log(e.what());update(SERVICE_STOPPED,ERROR_SERVICE_SPECIFIC_ERROR);}
}
BOOL WINAPI consoleControl(DWORD event) {
    if(event==CTRL_C_EVENT||event==CTRL_BREAK_EVENT||event==CTRL_CLOSE_EVENT){stopping=true;return TRUE;}
    return FALSE;
}
std::wstring exePath() {
    std::vector<wchar_t> buffer(32768);
    auto count=GetModuleFileNameW(nullptr,buffer.data(),static_cast<DWORD>(buffer.size()));
    if(!count||count>=buffer.size()) throw std::runtime_error("Cannot determine executable path");
    return std::wstring(buffer.data(),count);
}
void manage(bool install,const Options& options) {
    auto manager=OpenSCManagerW(nullptr,nullptr,SC_MANAGER_CONNECT|SC_MANAGER_CREATE_SERVICE);
    if(!manager) throw std::runtime_error("OpenSCManager failed; use an elevated terminal");
    struct Close {SC_HANDLE h;~Close(){if(h)CloseServiceHandle(h);}} closeManager{manager};
    if(install) {
        auto command=L"\""+exePath()+L"\" --service --waveform "+std::to_wstring(options.waveform)+
            L" --intensity "+std::to_wstring(options.intensity)+L" --sed-pipe "+options.sedPipe+
            L" --keepalive-ms "+std::to_wstring(options.keepaliveMs)+L" --status-mode "+
            std::wstring(options.statusMode.begin(),options.statusMode.end())+L" --thread-priority "+
            std::wstring(options.threadPriority.begin(),options.threadPriority.end());
        command+=L" --idle-wait "+std::wstring(options.idleWaitMode.begin(),options.idleWaitMode.end());
        if(options.mock) command+=L" --mock";
        if(options.mockFeatureUnavailable) command+=L" --mock-feature-unavailable";
        // LocalSystem owns the device and receives SED directly.
        auto service=CreateServiceW(manager,serviceName,L"Hybrid SED Haptic Service",SERVICE_CHANGE_CONFIG,
            SERVICE_WIN32_OWN_PROCESS,SERVICE_AUTO_START,SERVICE_ERROR_NORMAL,command.c_str(),
            nullptr,nullptr,nullptr,nullptr,nullptr);
        if(!service) throw std::runtime_error("CreateService failed error="+std::to_string(GetLastError()));
        Close closeService{service};
        SERVICE_DESCRIPTIONW description{const_cast<wchar_t*>(L"Executes HybridSED KeyDown haptics over HID-over-I2C.")};
        ChangeServiceConfig2W(service,SERVICE_CONFIG_DESCRIPTION,&description);
        log("Installed. Start with sc.exe start HybridSedHapticService; start HybridSED in desktop session.");
    }else {
        auto service=OpenServiceW(manager,serviceName,DELETE|SERVICE_QUERY_STATUS);
        if(!service) throw std::runtime_error("OpenService failed");
        Close closeService{service};
        SERVICE_STATUS current{};
        if(!QueryServiceStatus(service,&current)||current.dwCurrentState!=SERVICE_STOPPED)
            throw std::runtime_error("Stop service before uninstalling");
        if(!DeleteService(service)) throw std::runtime_error("DeleteService failed");
        log("Uninstalled");
    }
}
unsigned number(const std::wstring& text,unsigned maximum) {
    if(text.empty()||text.find_first_not_of(L"0123456789")!=std::wstring::npos)throw std::runtime_error("Invalid number");
    auto value=std::stoul(text);if(value>maximum)throw std::runtime_error("Number out of range");return static_cast<unsigned>(value);
}
std::string ascii(const std::wstring& text) {
    std::string result;for(wchar_t ch:text){if(ch<32||ch>126)throw std::runtime_error("ASCII command required");result+=static_cast<char>(ch);}return result;
}
void pipeName(const std::wstring& name) {
    if(name.empty()||name.size()>100||name.find_first_not_of(
        L"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")!=std::wstring::npos)
        throw std::runtime_error("Pipe names may contain only letters, digits, dash and underscore");
}
}
int wmain(int argc,wchar_t** argv) {
    using namespace hybrid;
    try {
        Options options;
        std::wstring mode;
        std::string controlCommand;
        for(int i=1;i<argc;++i) {
            std::wstring arg=argv[i];
            auto value=[&]() -> std::wstring {if(++i>=argc) throw std::runtime_error("Missing argument value");return argv[i];};
            if(arg==L"--console"||arg==L"--service"||arg==L"--install"||arg==L"--uninstall") {
                if(!mode.empty()) throw std::runtime_error("Select exactly one mode"); mode=arg;
            }else if(arg==L"--mock") options.mock=true;
            else if(arg==L"--waveform") options.waveform=number(value(),4);
            else if(arg==L"--intensity") options.intensity=number(value(),3);
            else if(arg==L"--keepalive-ms") options.keepaliveMs=number(value(),10000);
            else if(arg==L"--status-mode") options.statusMode=ascii(value());
            else if(arg==L"--idle-wait") options.idleWaitMode=ascii(value());
            else if(arg==L"--thread-priority") options.threadPriority=ascii(value());
            else if(arg==L"--mock-feature-unavailable") options.mockFeatureUnavailable=true;
            else if(arg==L"--mock-quiet") options.mockQuiet=true;
            else if(arg==L"--mock-trace") options.mockTrace=true;
            else if(arg==L"--latency-trace") options.latencyTrace=true;
            else if(arg==L"--mock-status-delay-ms") options.mockStatusDelayMs=number(value(),1000);
            else if(arg==L"--control") {if(!mode.empty())throw std::runtime_error("Select exactly one mode");mode=L"--control";controlCommand=ascii(value());}
            else if(arg==L"--sed-pipe") options.sedPipe=value();
            else if(arg==L"--log") options.logFile=value();
            else if(arg==L"--help") {
                std::cout<<"HybridHaptic --console [--mock] | --service | --install | --uninstall\n"
                    "Options: --waveform 1..4 --intensity 1..3 --sed-pipe HybridSedHaptic --log FILE\n"
                    " --keepalive-ms 0|100..10000 --status-mode auto|feature|interrupt|write-only\n"
                    " --mock-status-delay-ms 0..1000 (mock runtime interrupt ACK delay only)\n"
                    " --idle-wait fixed|deadline (default fixed; deadline is an opt-in comparison)\n"
                    " --latency-trace (bounded QPC/thread diagnostics; real HID or mock, default off)\n"
                    " --thread-priority normal|above-normal|highest (dedicated input/HID threads only)\n"
                    " --control \"status|diagnose|set waveform N|set intensity N|set keepalive N|session open|session close|recover|mode MODE|metrics reset\"\n";
                return 0;
            }else throw std::runtime_error("Unknown argument");
        }
        if(options.waveform<1||options.waveform>4||options.intensity<1||options.intensity>3)
            throw std::runtime_error("Waveform/intensity out of range");
        if(options.keepaliveMs && options.keepaliveMs<100)throw std::runtime_error("keepalive must be 0 or 100..10000");
        if(options.statusMode!="auto"&&options.statusMode!="feature"&&options.statusMode!="interrupt"&&options.statusMode!="write-only")throw std::runtime_error("Unknown status mode");
        if(options.threadPriority!="normal"&&options.threadPriority!="above-normal"&&options.threadPriority!="highest")throw std::runtime_error("Unknown thread priority");
        if(options.mockFeatureUnavailable&&!options.mock)throw std::runtime_error("mock option requires --mock");
        if(options.mockQuiet&&!options.mock)throw std::runtime_error("mock-quiet requires --mock");
        if(options.mockTrace&&!options.mock)throw std::runtime_error("mock-trace requires --mock");
        if(options.idleWaitMode!="fixed"&&options.idleWaitMode!="deadline")throw std::runtime_error("idle-wait must be fixed or deadline");
        if(options.mockStatusDelayMs&&!options.mock)throw std::runtime_error("mock-status-delay-ms requires --mock");
        pipeName(options.sedPipe);
        if(mode==L"--control")return runControl(options,controlCommand);
        if(mode.empty()) throw std::runtime_error("Use --console, --install or --service; see --help");
        if(mode==L"--install"||mode==L"--uninstall") {manage(mode==L"--install",options);return 0;}
        if(mode==L"--service") {
            serviceOptions=options;
            SERVICE_TABLE_ENTRYW table[]={{const_cast<wchar_t*>(serviceName),serviceMain},{nullptr,nullptr}};
            if(!StartServiceCtrlDispatcherW(table)) throw std::runtime_error("SCM dispatcher failed; use --console for debugging");
            return 0;
        }
        configureLog(options.logFile);
        SetConsoleCtrlHandler(consoleControl,TRUE);
        return runBackend(options);
    }catch(const std::exception& e){log(std::string("Fatal: ")+e.what());return 1;}
}
