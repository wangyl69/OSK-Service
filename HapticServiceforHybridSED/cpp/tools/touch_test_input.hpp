#pragma once
#include <windows.h>
#include <array>
#include <cstdint>
#include <set>

namespace touchtest {
constexpr ULONG_PTR marker=0x5345445F4F534BULL;
enum class Source {Touch,Mouse,Pen};
class ContactGate {
    std::set<UINT32> active_;
public:
    bool down(UINT32 id,Source source,bool mouseTest,bool inside,bool cancelled=false) {
        if(cancelled||!inside||source!=(mouseTest?Source::Mouse:Source::Touch))return false;
        return active_.insert(id).second;
    }
    void up(UINT32 id){active_.erase(id);}
    void reset(){active_.clear();}
};
inline unsigned keyAt(const RECT& region,POINT point) {
    if(region.right<=region.left||region.bottom<=region.top||!PtInRect(&region,point))return 0;
    constexpr unsigned keys[]={'A','S','D'};
    const auto column=static_cast<unsigned>((static_cast<long long>(point.x-region.left)*3)/(region.right-region.left));
    return keys[column<3?column:2];
}
inline std::array<INPUT,2> markedKey(unsigned vk) {
    std::array<INPUT,2> inputs{};
    for(auto& input:inputs){input.type=INPUT_KEYBOARD;input.ki.wVk=static_cast<WORD>(vk);input.ki.dwExtraInfo=marker;}
    inputs[1].ki.dwFlags=KEYEVENTF_KEYUP;
    return inputs;
}
inline UINT emit(unsigned vk) {
    auto inputs=markedKey(vk);const auto sent=SendInput(2,inputs.data(),sizeof(INPUT));
    if(sent==1)SendInput(1,&inputs[1],sizeof(INPUT)); // Release a partially accepted down.
    return sent;
}
}
