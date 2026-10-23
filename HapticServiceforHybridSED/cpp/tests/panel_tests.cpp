#include "raw_touch_capture.hpp"
#include <iostream>
#include <limits>

int main(){
    constexpr auto offset=offsetof(RAWINPUT,data)+offsetof(RAWHID,bRawData);
    std::vector<unsigned char> bytes(offset+6);
    RAWINPUTHEADER header{};header.dwType=RIM_TYPEHID;header.dwSize=static_cast<DWORD>(bytes.size());
    std::memcpy(bytes.data(),&header,sizeof(header));
    DWORD size=3,count=2;
    std::memcpy(bytes.data()+offsetof(RAWINPUT,data),&size,4);
    std::memcpy(bytes.data()+offsetof(RAWINPUT,data)+4,&count,4);
    for(unsigned i=0;i<6;++i)bytes[offset+i]=static_cast<unsigned char>(i+1);
    panel::HidPayload result;
    bool ok=panel::parseHidPayload(bytes,result)&&result.reportSize==3&&result.reportCount==2&&result.data[3]==4;
    ok=ok&&!panel::parseHidPayload(std::span(bytes).first(bytes.size()-1),result);
    header.dwType=RIM_TYPEMOUSE;std::memcpy(bytes.data(),&header,sizeof(header));
    ok=ok&&!panel::parseHidPayload(bytes,result);
    header.dwType=RIM_TYPEHID;std::memcpy(bytes.data(),&header,sizeof(header));
    size=std::numeric_limits<DWORD>::max();std::memcpy(bytes.data()+offsetof(RAWINPUT,data),&size,4);
    ok=ok&&!panel::parseHidPayload(bytes,result);
    size=0;std::memcpy(bytes.data()+offsetof(RAWINPUT,data),&size,4);
    ok=ok&&!panel::parseHidPayload(bytes,result);
    std::cout<<"Raw HID multi-report boundary, truncation, mouse exclusion and size validation\n";
    return ok?0:1;
}
