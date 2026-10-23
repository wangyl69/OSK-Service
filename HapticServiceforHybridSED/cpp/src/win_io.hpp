#pragma once
#include <windows.h>
#include <algorithm>
#include <atomic>
#include <array>
#include <chrono>
#include <stdexcept>

namespace hybrid {
// Owned, nonblocking HID Input read. Never free a pending OVERLAPPED or buffer.
// Polling this completion does not wait for the MCU, leaving PLAY dispatch runnable.
class AsyncReportRead {
    HANDLE handle_=INVALID_HANDLE_VALUE;
    HANDLE event_=nullptr;
    OVERLAPPED operation_{};
    std::array<unsigned char,32> report_{};
    bool owned_=false;
public:
    AsyncReportRead() {
        event_=CreateEventW(nullptr,TRUE,FALSE,nullptr);
        if(!event_)throw std::runtime_error("CreateEvent (async Input) failed");
    }
    AsyncReportRead(const AsyncReportRead&)=delete;
    AsyncReportRead& operator=(const AsyncReportRead&)=delete;
    ~AsyncReportRead(){cancel();CloseHandle(event_);}
    void cancel() noexcept {
        if(owned_){CancelIoEx(handle_,&operation_);DWORD done=0;GetOverlappedResult(handle_,&operation_,&done,TRUE);owned_=false;}
    }
    void start(HANDLE handle) {
        if(owned_)throw std::runtime_error("Concurrent async Input request");
        handle_=handle;ResetEvent(event_);operation_={};operation_.hEvent=event_;
        DWORD done=0;
        const auto immediate=ReadFile(handle_,report_.data(),32,&done,&operation_);
        if(!immediate&&GetLastError()!=ERROR_IO_PENDING)throw std::runtime_error("Async HID ReadFile failed");
        owned_=true;
    }
    bool poll(std::array<unsigned char,32>& report,DWORD& count) {
        if(!owned_)throw std::runtime_error("No async Input request");
        if(!GetOverlappedResult(handle_,&operation_,&count,FALSE)) {
            if(GetLastError()==ERROR_IO_INCOMPLETE)return false;
            owned_=false;throw std::runtime_error("Async HID Input completion failed");
        }
        owned_=false;report=report_;return true;
    }
};
// Cancel and join every pending operation before OVERLAPPED/buffers go out of scope.
inline bool overlappedIo(HANDLE handle,void* buffer,DWORD size,bool write,DWORD timeout,
                         DWORD& done,const std::atomic_bool& stop,bool honorStop=true) {
    struct Event {HANDLE value;~Event(){if(value)CloseHandle(value);}} event{CreateEventW(nullptr,TRUE,FALSE,nullptr)};
    if(!event.value)throw std::runtime_error("CreateEvent failed");
    OVERLAPPED operation{};operation.hEvent=event.value;done=0;
    BOOL immediate=write ? WriteFile(handle,buffer,size,&done,&operation) : ReadFile(handle,buffer,size,&done,&operation);
    if(immediate)return true;
    if(GetLastError()!=ERROR_IO_PENDING)return false;
    if(WaitForSingleObject(event.value,0)==WAIT_OBJECT_0)
        return GetOverlappedResult(handle,&operation,&done,FALSE)!=FALSE;
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::milliseconds(timeout);
    while(!honorStop||!stop) {
        const auto now=std::chrono::steady_clock::now();if(now>=deadline)break;
        const auto remaining=std::chrono::ceil<std::chrono::milliseconds>(deadline-now).count();
        const auto slice=static_cast<DWORD>(std::min<std::int64_t>(10,remaining));
        const auto result=WaitForSingleObject(event.value,slice);
        if(result==WAIT_OBJECT_0)return GetOverlappedResult(handle,&operation,&done,FALSE)!=FALSE;
        if(result==WAIT_FAILED)break;
    }
    CancelIoEx(handle,&operation);
    GetOverlappedResult(handle,&operation,&done,TRUE);
    return false;
}
}
