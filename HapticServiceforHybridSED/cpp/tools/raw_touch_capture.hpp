#pragma once
#include <windows.h>
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <map>
#include <span>
#include <sstream>
#include <stdexcept>
#include <vector>

namespace panel {
struct HidPayload {
    RAWINPUTHEADER header{};
    DWORD reportSize=0,reportCount=0;
    std::span<const unsigned char> data;
};
inline bool parseHidPayload(std::span<const unsigned char> packet,HidPayload& result) {
    constexpr auto offset=offsetof(RAWINPUT,data)+offsetof(RAWHID,bRawData);
    if(packet.size()<offset)return false;
    RAWINPUTHEADER header{};std::memcpy(&header,packet.data(),sizeof(header));
    if(header.dwType!=RIM_TYPEHID||header.dwSize<offset||header.dwSize>packet.size())return false;
    DWORD size=0,count=0;
    std::memcpy(&size,packet.data()+offsetof(RAWINPUT,data),sizeof(size));
    std::memcpy(&count,packet.data()+offsetof(RAWINPUT,data)+sizeof(size),sizeof(count));
    const auto total=static_cast<std::uint64_t>(size)*count;
    if(!size||!count||total>header.dwSize-offset)return false;
    result={header,size,count,packet.subspan(offset,static_cast<std::size_t>(total))};return true;
}
class TouchCapture {
    struct Device {unsigned index=0,vid=0,pid=0;bool matchesSource=false;};
    USHORT sourceUsage_=4;
    std::map<HANDLE,Device> devices_;
    std::ostringstream reports_;
    unsigned count_=0,errors_=0,ignored_=0;
    static constexpr unsigned limit=2000;
    LARGE_INTEGER frequency_{};
    static LRESULT CALLBACK procedure(HWND window,UINT message,WPARAM wParam,LPARAM lParam) noexcept {
        auto self=reinterpret_cast<TouchCapture*>(GetWindowLongPtrW(window,GWLP_USERDATA));
        if(message==WM_INPUT&&self){
            try{self->input(reinterpret_cast<HRAWINPUT>(lParam));}catch(...){++self->errors_;}
        }
        // Foreground WM_INPUT requires DefWindowProc cleanup after reading.
        return DefWindowProcW(window,message,wParam,lParam);
    }
    void input(HRAWINPUT raw) {
        if(count_>=limit)return;
        UINT size=0;
        if(GetRawInputData(raw,RID_INPUT,nullptr,&size,sizeof(RAWINPUTHEADER))!=0||!size||size>65536){++errors_;return;}
        std::vector<unsigned char> bytes(size);
        auto received=GetRawInputData(raw,RID_INPUT,bytes.data(),&size,sizeof(RAWINPUTHEADER));
        if(received==static_cast<UINT>(-1)||received>bytes.size()){++errors_;return;}
        bytes.resize(received);
        HidPayload payload;
        if(!parseHidPayload(bytes,payload)){++errors_;return;}
        auto [entry,added]=devices_.try_emplace(payload.header.hDevice);
        auto& device=entry->second;
        if(added){
            device.index=static_cast<unsigned>(devices_.size());
            RID_DEVICE_INFO info{};info.cbSize=sizeof(info);UINT length=sizeof(info);
            if(GetRawInputDeviceInfoW(payload.header.hDevice,RIDI_DEVICEINFO,&info,&length)!=static_cast<UINT>(-1)&&info.dwType==RIM_TYPEHID){
                device.vid=info.hid.dwVendorId;device.pid=info.hid.dwProductId;
                device.matchesSource=info.hid.usUsagePage==0x0D&&info.hid.usUsage==sourceUsage_;
            }
        }
        if(!device.matchesSource){++ignored_;return;}
        if(payload.reportSize>4096){++errors_;return;}
        LARGE_INTEGER timestamp{};QueryPerformanceCounter(&timestamp);
        constexpr char hex[]="0123456789ABCDEF";
        for(DWORD report=0;report<payload.reportCount&&count_<limit;++report){
            if(count_++)reports_<<',';
            reports_<<"{\"device_index\":"<<device.index<<",\"vid\":"<<device.vid<<",\"pid\":"<<device.pid
                    <<",\"host_qpc\":"<<timestamp.QuadPart<<",\"report_bytes\":"<<payload.reportSize<<",\"hex\":\"";
            for(DWORD byte=0;byte<payload.reportSize;++byte){auto value=payload.data[static_cast<std::size_t>(report)*payload.reportSize+byte];reports_<<hex[value>>4]<<hex[value&15];}
            reports_<<"\"}";
        }
    }
public:
    explicit TouchCapture(USHORT usage=4):sourceUsage_(usage){}
    std::string run(unsigned seconds) {
        auto module=GetModuleHandleW(nullptr);
        constexpr wchar_t name[]=L"HybridSED_ReadOnlyTouchCapture";
        WNDCLASSW cls{};cls.hInstance=module;cls.lpszClassName=name;cls.lpfnWndProc=procedure;
        if(!RegisterClassW(&cls))throw std::runtime_error("Touch capture RegisterClass failed");
        auto window=CreateWindowExW(0,name,L"",0,0,0,0,0,HWND_MESSAGE,nullptr,module,nullptr);
        if(!window){UnregisterClassW(name,module);throw std::runtime_error("Touch capture CreateWindow failed");}
        SetWindowLongPtrW(window,GWLP_USERDATA,reinterpret_cast<LONG_PTR>(this));
        RAWINPUTDEVICE registration{0x0D,sourceUsage_,RIDEV_INPUTSINK,window};
        if(!RegisterRawInputDevices(&registration,1,sizeof(registration))){
            auto error=GetLastError();DestroyWindow(window);UnregisterClassW(name,module);
            throw std::runtime_error("Touch capture RegisterRawInputDevices error="+std::to_string(error));
        }
        QueryPerformanceFrequency(&frequency_);
        const auto started=GetTickCount64(),deadline=started+seconds*1000;
        while(GetTickCount64()<deadline&&count_<limit){
            MSG message{};
            for(unsigned i=0;i<64&&PeekMessageW(&message,nullptr,0,0,PM_REMOVE);++i){
                TranslateMessage(&message);DispatchMessageW(&message);
            }
            auto now=GetTickCount64();
            if(now>=deadline||count_>=limit)break;
            if(MsgWaitForMultipleObjectsEx(0,nullptr,static_cast<DWORD>(std::min<ULONGLONG>(50,deadline-now)),QS_RAWINPUT,MWMO_INPUTAVAILABLE)==WAIT_FAILED){++errors_;break;}
        }
        registration.dwFlags=RIDEV_REMOVE;registration.hwndTarget=nullptr;
        const bool unregistered=RegisterRawInputDevices(&registration,1,sizeof(registration))!=FALSE;
        DestroyWindow(window);UnregisterClassW(name,module);
        std::ostringstream output;
        output<<"{\"registered\":true,\"unregistered\":"<<(unregistered?"true":"false")
              <<",\"usage_page\":13,\"usage\":"<<sourceUsage_<<",\"source\":\""<<(sourceUsage_==5?"touchpad":"touchscreen")<<"\",\"duration_ms\":"<<GetTickCount64()-started
              <<",\"qpc_frequency\":"<<frequency_.QuadPart<<",\"sample_count\":"<<count_
              <<",\"report_limit_reached\":"<<(count_>=limit?"true":"false")
              <<",\"read_errors\":"<<errors_<<",\"ignored_messages\":"<<ignored_
              <<",\"timestamp_scope\":\"Host message receipt, not sensor or bus timing\",\"reports\":["<<reports_.str()<<"]}";
        return output.str();
    }
};
}
