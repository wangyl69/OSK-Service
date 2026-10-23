// Read-only metadata; optional bounded touchscreen/touchpad Raw Input capture.
// No Output reports, Feature requests, mouse/keyboard capture or serial numbers.
#include <windows.h>
#include <hidsdi.h>
#include <setupapi.h>
#include <iostream>
#include <string>
#include <vector>
#include "policy.hpp"
#include "raw_touch_capture.hpp"

static std::string utf8(const wchar_t* value) {
    int length=WideCharToMultiByte(CP_UTF8,0,value,-1,nullptr,0,nullptr,nullptr);
    if(length<=1)return {};
    std::string result(static_cast<size_t>(length),'\0');
    WideCharToMultiByte(CP_UTF8,0,value,-1,result.data(),length,nullptr,nullptr);
    result.pop_back();return result;
}
int main(int argc,char** argv) {
    unsigned seconds=0;
    USHORT usage=4;
    try {
        for(int index=1;index<argc;++index){
            auto option=std::string(argv[index]);
            if(index+1>=argc)throw std::runtime_error("Missing PanelProbe argument value");
            auto value=std::string(argv[++index]);
            if(option=="--capture-seconds"){
                std::size_t consumed=0;auto number=std::stoul(value,&consumed);
                if(consumed!=value.size()||number<1||number>120)throw std::runtime_error("capture-seconds must be 1..120");
                seconds=static_cast<unsigned>(number);
            }else if(option=="--capture-source"){
                if(value!="touchscreen"&&value!="touchpad")throw std::runtime_error("capture-source must be touchscreen or touchpad");
                usage=value=="touchpad"?5:4;
            }else throw std::runtime_error("Usage: PanelProbe.exe [--capture-seconds 1..120] [--capture-source touchscreen|touchpad]");
        }
        if(usage==5&&!seconds)throw std::runtime_error("capture-source touchpad requires capture-seconds");
    }catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 2;}
    std::string capture="null";
    if(seconds){
        std::cerr<<"Recording "<<(usage==5?"touchpad":"touchscreen")<<" Raw Input for up to "<<seconds<<" seconds; metadata/data go to stdout.\n";
        try{panel::TouchCapture recorder(usage);capture=recorder.run(seconds);}
        catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}
    }
    using hybrid::jsonString;
    std::cout<<"{\"scope\":\"Read-only HID/display metadata with optional digitizer samples; no calibration\",\"displays\":[";
    bool first=true;
    for(DWORD index=0;;++index){
        DISPLAY_DEVICEW display{};display.cb=sizeof(display);
        if(!EnumDisplayDevicesW(nullptr,index,&display,0))break;
        if(!(display.StateFlags&DISPLAY_DEVICE_ATTACHED_TO_DESKTOP))continue;
        DEVMODEW mode{};mode.dmSize=sizeof(mode);
        if(!EnumDisplaySettingsW(display.DeviceName,ENUM_CURRENT_SETTINGS,&mode))continue;
        if(!first)std::cout<<',';first=false;
        std::cout<<"{\"name\":"<<jsonString(utf8(display.DeviceName))<<",\"primary\":"
                 <<((display.StateFlags&DISPLAY_DEVICE_PRIMARY_DEVICE)?"true":"false")
                 <<",\"pixel_width\":"<<mode.dmPelsWidth<<",\"pixel_height\":"<<mode.dmPelsHeight
                 <<",\"desktop_x\":"<<mode.dmPosition.x<<",\"desktop_y\":"<<mode.dmPosition.y
                 <<",\"orientation\":"<<mode.dmDisplayOrientation<<"}";
    }
    std::cout<<"],\"digitizer_collections\":[";first=true;
    GUID guid{};HidD_GetHidGuid(&guid);
    auto info=SetupDiGetClassDevsW(&guid,nullptr,nullptr,DIGCF_PRESENT|DIGCF_DEVICEINTERFACE);
    if(info==INVALID_HANDLE_VALUE){std::cerr<<"Cannot enumerate HID\n";return 1;}
    unsigned inaccessible=0;
    SP_DEVICE_INTERFACE_DATA entry{};entry.cbSize=sizeof(entry);
    for(DWORD index=0;SetupDiEnumDeviceInterfaces(info,nullptr,&guid,index,&entry);++index){
        DWORD required=0;SetupDiGetDeviceInterfaceDetailW(info,&entry,nullptr,0,&required,nullptr);
        if(required<sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA_W))continue;
        std::vector<unsigned char> bytes(required);
        auto detail=reinterpret_cast<SP_DEVICE_INTERFACE_DETAIL_DATA_W*>(bytes.data());detail->cbSize=sizeof(*detail);
        if(!SetupDiGetDeviceInterfaceDetailW(info,&entry,detail,required,nullptr,nullptr))continue;
        auto handle=CreateFileW(detail->DevicePath,0,FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,OPEN_EXISTING,0,nullptr);
        if(handle==INVALID_HANDLE_VALUE){++inaccessible;continue;}
        PHIDP_PREPARSED_DATA parsed=nullptr;
        if(!HidD_GetPreparsedData(handle,&parsed)){CloseHandle(handle);continue;}
        HIDP_CAPS caps{};
        if(HidP_GetCaps(parsed,&caps)==HIDP_STATUS_SUCCESS&&caps.UsagePage==0x0D){
            if(!first)std::cout<<',';first=false;
            HIDD_ATTRIBUTES attributes{};attributes.Size=sizeof(attributes);HidD_GetAttributes(handle,&attributes);
            wchar_t manufacturer[256]{},product[256]{};
            HidD_GetManufacturerString(handle,manufacturer,sizeof(manufacturer));
            HidD_GetProductString(handle,product,sizeof(product));
            std::cout<<"{\"vid\":"<<attributes.VendorID<<",\"pid\":"<<attributes.ProductID
                     <<",\"version\":"<<attributes.VersionNumber<<",\"manufacturer\":"<<jsonString(utf8(manufacturer))
                     <<",\"product\":"<<jsonString(utf8(product))<<",\"usage_page\":"<<caps.UsagePage
                     <<",\"usage\":"<<caps.Usage<<",\"input_report_bytes\":"<<caps.InputReportByteLength
                     <<",\"input_values\":[";
            USHORT count=caps.NumberInputValueCaps;
            std::vector<HIDP_VALUE_CAPS> values(count);
            if(count&&HidP_GetValueCaps(HidP_Input,values.data(),&count,parsed)==HIDP_STATUS_SUCCESS){
                for(USHORT i=0;i<count;++i){
                    const auto& v=values[i];if(i)std::cout<<',';
                    std::cout<<"{\"report_id\":"<<unsigned(v.ReportID)<<",\"usage_page\":"<<v.UsagePage
                             <<",\"usage_min\":"<<(v.IsRange?v.Range.UsageMin:v.NotRange.Usage)
                             <<",\"usage_max\":"<<(v.IsRange?v.Range.UsageMax:v.NotRange.Usage)
                             <<",\"link_collection\":"<<v.LinkCollection<<",\"bit_size\":"<<v.BitSize
                             <<",\"report_count\":"<<v.ReportCount<<",\"logical_min\":"<<v.LogicalMin
                             <<",\"logical_max\":"<<v.LogicalMax<<",\"physical_min\":"<<v.PhysicalMin
                             <<",\"physical_max\":"<<v.PhysicalMax<<",\"units\":"<<v.Units<<",\"units_exp\":"<<v.UnitsExp<<"}";
                }
            }
            std::cout<<"]}";
        }
        HidD_FreePreparsedData(parsed);CloseHandle(handle);
    }
    SetupDiDestroyDeviceInfoList(info);
    std::cout<<"],\"inaccessible_hid_interfaces\":"<<inaccessible
             <<",\"touch_capture\":"<<capture
             <<",\"note\":\"Supply display scaling, desired window rectangle and vendor report descriptor separately. No observed samples does not prove compatibility.\"}\n";
    return 0;
}
