#include "touch_test_input.hpp"
#include <iostream>
#include <string>
int main(int argc,char** argv){
    if(argc==2&&(std::string(argv[1])=="--integration-inject"||std::string(argv[1])=="--integration-unmarked")){
        // Explicit test-only path, not included in the delivered GUI executable.
        for(unsigned i=0;i<3;++i){
            if(std::string(argv[1])=="--integration-inject"){if(touchtest::emit(VK_F23)!=2)return 1;}
            else{auto pair=touchtest::markedKey(VK_F23);for(auto& input:pair)input.ki.dwExtraInfo=0;if(SendInput(2,pair.data(),sizeof(INPUT))!=2)return 1;}
            Sleep(100);
        }
        std::cout<<"TEST ONLY: emitted three marked pairs; not a physical touch test\n";return 0;
    }
    RECT area{10,20,310,220};touchtest::ContactGate gate;bool ok=true;
    ok&=!gate.down(1,touchtest::Source::Mouse,false,true);
    ok&=!gate.down(1,touchtest::Source::Pen,false,true);
    ok&=!gate.down(1,touchtest::Source::Touch,false,false);
    ok&=!gate.down(1,touchtest::Source::Touch,false,true,true);
    ok&=gate.down(1,touchtest::Source::Touch,false,true);
    ok&=!gate.down(1,touchtest::Source::Touch,false,true);
    ok&=gate.down(2,touchtest::Source::Touch,false,true);gate.up(1);
    ok&=gate.down(1,touchtest::Source::Touch,false,true);gate.reset();
    ok&=gate.down(1,touchtest::Source::Mouse,true,true);
    ok&=!gate.down(3,touchtest::Source::Touch,true,true);
    ok&=touchtest::keyAt(area,{10,20})=='A'&&touchtest::keyAt(area,{110,20})=='S'&&touchtest::keyAt(area,{210,20})=='D';
    ok&=touchtest::keyAt(area,{310,20})==0&&touchtest::keyAt(area,{10,220})==0;
    for(unsigned key:{'A','S','D'}){const auto pair=touchtest::markedKey(key);ok&=pair[0].type==INPUT_KEYBOARD&&pair[1].type==INPUT_KEYBOARD;
        ok&=pair[0].ki.wVk==key&&pair[1].ki.wVk==key&&pair[0].ki.dwFlags==0&&pair[1].ki.dwFlags==KEYEVENTF_KEYUP;
        ok&=pair[0].ki.dwExtraInfo==touchtest::marker&&pair[1].ki.dwExtraInfo==touchtest::marker;}
    std::cout<<(ok?"PASS":"FAIL")<<": touch-only gating, duplicate/cancel/outside rejection, key geometry and marked input pairs\n";return ok?0:1;
}
