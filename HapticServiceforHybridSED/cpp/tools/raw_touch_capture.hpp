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
#include "hid_report_decode.hpp"

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
    struct Device {unsigned index=0,vid=0,pid=0;bool matchesSource=false;std::vector<unsigned char> preparsed;std::string path;};
    USHORT sourceUsage_=4;
    std::map<HANDLE,Device> devices_;
    std::ostringstream reports_;
    unsigned count_=0,errors_=0,ignored_=0;
    static constexpr unsigned limit=10000;
    bool guided_=false,cancelled_=false;
    ULONGLONG started_=0;
    unsigned seconds_=0,pointerCount_=0;
    RECT screen_{};
    std::ostringstream pointers_;
    static std::string jsonPath(const std::string& path) {
        std::string result="\"";constexpr char hex[]="0123456789ABCDEF";
        for(unsigned char c:path){
            if(c=='\\'||c=='\"'){result+='\\';result+=static_cast<char>(c);}
            else if(c<32){result+="\\u00";result+=hex[c>>4];result+=hex[c&15];}
            else result+=static_cast<char>(c);
        }
        return result+'\"';
    }
    unsigned targetIndex() const {
        return std::min(4U,static_cast<unsigned>((GetTickCount64()-started_)*5/(seconds_*1000ULL)));
    }
    POINT targetPoint(unsigned index) const {
        const int x[]={10,90,50,10,90},y[]={10,10,50,90,90};
        return {screen_.left+(screen_.right-screen_.left)*x[index]/100,
                screen_.top+(screen_.bottom-screen_.top)*y[index]/100};
    }
    LARGE_INTEGER frequency_{};
    static LRESULT CALLBACK procedure(HWND window,UINT message,WPARAM wParam,LPARAM lParam) noexcept {
        auto self=reinterpret_cast<TouchCapture*>(GetWindowLongPtrW(window,GWLP_USERDATA));
        if(self&&self->guided_){
            if(message==WM_CLOSE||(message==WM_KEYDOWN&&wParam==VK_ESCAPE)){self->cancelled_=true;return 0;}
            if(message==WM_PAINT){self->paint(window);return 0;}
            if(message==WM_POINTERDOWN||message==WM_POINTERUPDATE||message==WM_POINTERUP){
                try{self->pointer(GET_POINTERID_WPARAM(wParam),message);}catch(...){++self->errors_;}
            }
        }
        if(message==WM_INPUT&&self){
            try{self->input(reinterpret_cast<HRAWINPUT>(lParam));}catch(...){++self->errors_;}
        }
        // Foreground WM_INPUT requires DefWindowProc cleanup after reading.
        return DefWindowProcW(window,message,wParam,lParam);
    }
    void paint(HWND window) {
        PAINTSTRUCT ps{};auto dc=BeginPaint(window,&ps);
        RECT client{};GetClientRect(window,&client);FillRect(dc,&client,static_cast<HBRUSH>(GetStockObject(WHITE_BRUSH)));
        SetBkMode(dc,TRANSPARENT);SetTextColor(dc,RGB(0,0,0));
        const auto index=targetIndex();const auto point=targetPoint(index);
        const auto label=L"Touch target "+std::to_wstring(index+1)+L" / 5. Tap, move slightly, release. Esc cancels.";
        TextOutW(dc,24,24,label.c_str(),static_cast<int>(label.size()));
        const auto pen=CreatePen(PS_SOLID,4,RGB(200,0,0));auto old=SelectObject(dc,pen);
        const int x=point.x-screen_.left,y=point.y-screen_.top;
        Ellipse(dc,x-24,y-24,x+24,y+24);MoveToEx(dc,x-40,y,nullptr);LineTo(dc,x+40,y);
        MoveToEx(dc,x,y-40,nullptr);LineTo(dc,x,y+40);
        SelectObject(dc,old);DeleteObject(pen);EndPaint(window,&ps);
    }
    void pointer(UINT32 id,UINT message) {
        POINTER_INFO info{};
        if(!GetPointerInfo(id,&info)||info.pointerType!=PT_TOUCH||pointerCount_>=limit)return;
        if(pointerCount_++)pointers_<<',';
        LARGE_INTEGER timestamp{};QueryPerformanceCounter(&timestamp);
        pointers_<<"{\"host_qpc\":"<<timestamp.QuadPart<<",\"elapsed_ms\":"<<GetTickCount64()-started_
                 <<",\"target\":"<<targetIndex()+1<<",\"pointer_id\":"<<id<<",\"message\":"<<message
                 <<",\"flags\":"<<info.pointerFlags<<",\"screen_x\":"<<info.ptPixelLocation.x
                 <<",\"screen_y\":"<<info.ptPixelLocation.y<<'}';
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
            UINT nameLength=0;
            if(GetRawInputDeviceInfoW(payload.header.hDevice,RIDI_DEVICENAME,nullptr,&nameLength)!=static_cast<UINT>(-1)&&nameLength&&nameLength<=32768){
                std::vector<wchar_t> name(nameLength+1,0);
                if(GetRawInputDeviceInfoW(payload.header.hDevice,RIDI_DEVICENAME,name.data(),&nameLength)!=static_cast<UINT>(-1)){
                    const auto needed=WideCharToMultiByte(CP_UTF8,0,name.data(),-1,nullptr,0,nullptr,nullptr);
                    if(needed>1){device.path.resize(needed);WideCharToMultiByte(CP_UTF8,0,name.data(),-1,device.path.data(),needed,nullptr,nullptr);device.path.pop_back();}
                }
            }
            RID_DEVICE_INFO info{};info.cbSize=sizeof(info);UINT length=sizeof(info);
            if(GetRawInputDeviceInfoW(payload.header.hDevice,RIDI_DEVICEINFO,&info,&length)!=static_cast<UINT>(-1)&&info.dwType==RIM_TYPEHID){
                device.vid=info.hid.dwVendorId;device.pid=info.hid.dwProductId;
                device.matchesSource=info.hid.usUsagePage==0x0D&&info.hid.usUsage==sourceUsage_;
                UINT required=0;
                if(device.matchesSource&&GetRawInputDeviceInfoW(payload.header.hDevice,RIDI_PREPARSEDDATA,nullptr,&required)!=static_cast<UINT>(-1)&&required&&required<=1048576){
                    device.preparsed.resize(required);
                    if(GetRawInputDeviceInfoW(payload.header.hDevice,RIDI_PREPARSEDDATA,device.preparsed.data(),&required)==static_cast<UINT>(-1))device.preparsed.clear();
                }
            }
        }
        if(!device.matchesSource){++ignored_;return;}
        if(payload.reportSize>4096){++errors_;return;}
        LARGE_INTEGER timestamp{};QueryPerformanceCounter(&timestamp);
        constexpr char hex[]="0123456789ABCDEF";
        for(DWORD report=0;report<payload.reportCount&&count_<limit;++report){
            if(count_++)reports_<<',';
            reports_<<"{\"device_index\":"<<device.index<<",\"vid\":"<<device.vid<<",\"pid\":"<<device.pid
                    <<",\"device_path\":"<<jsonPath(device.path)
                    <<",\"host_qpc\":"<<timestamp.QuadPart<<",\"report_bytes\":"<<payload.reportSize<<",\"hex\":\"";
            for(DWORD byte=0;byte<payload.reportSize;++byte){auto value=payload.data[static_cast<std::size_t>(report)*payload.reportSize+byte];reports_<<hex[value>>4]<<hex[value&15];}
            auto data=payload.data.subspan(static_cast<std::size_t>(report)*payload.reportSize,payload.reportSize);
            reports_<<"\",\"elapsed_ms\":"<<GetTickCount64()-started_
                    <<",\"foreground_hwnd\":"<<reinterpret_cast<std::uintptr_t>(GetForegroundWindow());
            if(guided_)reports_<<",\"target\":"<<targetIndex()+1;
            reports_<<",\"decoded\":"<<decodeHidValues(device.preparsed.empty()?nullptr:reinterpret_cast<PHIDP_PREPARSED_DATA>(device.preparsed.data()),data)<<'}';
        }
    }
public:
    explicit TouchCapture(USHORT usage=4):sourceUsage_(usage){}
    std::string run(unsigned seconds,bool guided=false) {
        seconds_=seconds;guided_=guided;started_=GetTickCount64();
        const POINT origin{0,0};MONITORINFO monitor{};monitor.cbSize=sizeof(monitor);
        if(!GetMonitorInfoW(MonitorFromPoint(origin,MONITOR_DEFAULTTOPRIMARY),&monitor))throw std::runtime_error("Cannot read primary display bounds");
        screen_=monitor.rcMonitor;
        auto module=GetModuleHandleW(nullptr);
        constexpr wchar_t name[]=L"HybridSED_ReadOnlyTouchCapture";
        WNDCLASSW cls{};cls.hInstance=module;cls.lpszClassName=name;cls.lpfnWndProc=procedure;
        if(!RegisterClassW(&cls))throw std::runtime_error("Touch capture RegisterClass failed");
        auto window=CreateWindowExW(guided?WS_EX_TOPMOST:0,name,L"Read-only touch targets",
            guided?WS_POPUP:0,screen_.left,screen_.top,screen_.right-screen_.left,screen_.bottom-screen_.top,
            guided?nullptr:HWND_MESSAGE,nullptr,module,nullptr);
        if(!window){UnregisterClassW(name,module);throw std::runtime_error("Touch capture CreateWindow failed");}
        SetWindowLongPtrW(window,GWLP_USERDATA,reinterpret_cast<LONG_PTR>(this));
        if(guided){ShowWindow(window,SW_SHOW);SetForegroundWindow(window);}
        RAWINPUTDEVICE registration{0x0D,sourceUsage_,RIDEV_INPUTSINK,window};
        if(!RegisterRawInputDevices(&registration,1,sizeof(registration))){
            auto error=GetLastError();DestroyWindow(window);UnregisterClassW(name,module);
            throw std::runtime_error("Touch capture RegisterRawInputDevices error="+std::to_string(error));
        }
        QueryPerformanceFrequency(&frequency_);
        started_=GetTickCount64();const auto started=started_,deadline=started+seconds*1000;
        unsigned paintedTarget=99;
        while(GetTickCount64()<deadline&&count_<limit&&!cancelled_){
            if(guided&&paintedTarget!=targetIndex()){paintedTarget=targetIndex();InvalidateRect(window,nullptr,FALSE);UpdateWindow(window);}
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
              <<",\"cancelled\":"<<(cancelled_?"true":"false")
              <<",\"guided\":"<<(guided?"true":"false")<<",\"target_points\":[";
        if(guided)for(unsigned i=0;i<5;++i){const auto point=targetPoint(i);if(i)output<<',';output<<"{\"target\":"<<i+1<<",\"screen_x\":"<<point.x<<",\"screen_y\":"<<point.y<<'}';}
        output<<"],\"windows_touch_pointer_count\":"<<pointerCount_<<",\"windows_touch_pointers\":["<<pointers_.str()<<']'
              <<",\"timestamp_scope\":\"Host message receipt, not sensor or bus timing\",\"reports\":["<<reports_.str()<<"]}";
        return output.str();
    }
};
}
