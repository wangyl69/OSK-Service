#include "core.hpp"
#include "app_i2chid_protocol.h"
#include <iostream>

// Build the fixture from the firmware's public wire-layout constants rather
// than copying the service mock. No STM32 toolchain or MCU is required.
static hybrid::Report capabilities(bool feature) {
    hybrid::Report r{};
    r[APP_I2CHID_OFFSET_REPORT_ID]=feature ? APP_I2CHID_CAPABILITIES_REPORT_ID : APP_I2CHID_STATUS_REPORT_ID;
    r[APP_I2CHID_OFFSET_VERSION]=APP_I2CHID_PROTOCOL_VERSION;
    r[APP_I2CHID_OFFSET_OPCODE]=feature ? APP_I2CHID_CAPABILITIES_SNAPSHOT : APP_I2CHID_STATUS_SNAPSHOT;
    const unsigned first=feature ? APP_I2CHID_CAP_OFFSET_CAP_FLAGS : APP_I2CHID_CAP_STATUS_OFFSET_CAP_FLAGS;
    hybrid::word(r,first,APP_I2CHID_CAP_STATUS_INPUT|APP_I2CHID_CAP_CONTROL_OUTPUT|
        APP_I2CHID_CAP_FEATURE_REPORTS|APP_I2CHID_CAP_CRC8);
    r[feature ? APP_I2CHID_CAP_OFFSET_WAVEFORM_COUNT : APP_I2CHID_CAP_STATUS_OFFSET_WAVEFORM_COUNT]=4;
    r[feature ? APP_I2CHID_CAP_OFFSET_REPORT_BYTES : APP_I2CHID_CAP_STATUS_OFFSET_REPORT_BYTES]=APP_I2CHID_REPORT_BYTES;
    r[feature ? APP_I2CHID_CAP_OFFSET_EVENT_ID : APP_I2CHID_CAP_STATUS_OFFSET_EVENT_ID]=APP_I2CHID_EVENT_REPORT_ID;
    r[feature ? APP_I2CHID_CAP_OFFSET_CONTROL_ID : APP_I2CHID_CAP_STATUS_OFFSET_CONTROL_ID]=APP_I2CHID_CONTROL_REPORT_ID;
    r[feature ? APP_I2CHID_CAP_OFFSET_STATUS_ID : APP_I2CHID_CAP_STATUS_OFFSET_STATUS_ID]=APP_I2CHID_STATUS_REPORT_ID;
    r[feature ? APP_I2CHID_CAP_OFFSET_CAPS_ID : APP_I2CHID_CAP_STATUS_OFFSET_CAPS_ID]=APP_I2CHID_CAPABILITIES_REPORT_ID;
    r[feature ? APP_I2CHID_CAP_OFFSET_CONFIG_ID : APP_I2CHID_CAP_STATUS_OFFSET_CONFIG_ID]=APP_I2CHID_CONFIG_REPORT_ID;
    return r;
}
int main() {
    for(bool feature:{true,false}) {
        auto r=capabilities(feature);
        if(!hybrid::capabilitiesMatch(r,feature,4))return 1;
        if(hybrid::capabilitiesMatch(r,feature,5))return 2;
        // The previous service incorrectly expected the config ID at caps ID.
        r[feature ? APP_I2CHID_CAP_OFFSET_CAPS_ID : APP_I2CHID_CAP_STATUS_OFFSET_CAPS_ID]=APP_I2CHID_CONFIG_REPORT_ID;
        if(hybrid::capabilitiesMatch(r,feature,1))return 3;
        r=capabilities(feature);
        r[feature ? APP_I2CHID_CAP_OFFSET_CONFIG_ID : APP_I2CHID_CAP_STATUS_OFFSET_CONFIG_ID]=0;
        if(hybrid::capabilitiesMatch(r,feature,1))return 4;
    }
    const auto play=hybrid::play(0x1234,0x5678,2,3,0x9abc);
    if(play[APP_I2CHID_OFFSET_REPORT_ID]!=APP_I2CHID_EVENT_REPORT_ID||
       play[APP_I2CHID_OFFSET_OPCODE]!=APP_I2CHID_EVENT_PLAY||
       play[APP_I2CHID_EVENT_OFFSET_KEY_ID]!=0x34||
       play[APP_I2CHID_EVENT_OFFSET_WAVEFORM]!=2||
       play[APP_I2CHID_EVENT_OFFSET_INTENSITY]!=3||
       play[APP_I2CHID_EVENT_OFFSET_TOUCH_PHASE]!=1||
       play[APP_I2CHID_EVENT_OFFSET_AGE_US]!=0x78)return 5;
    std::cout<<"PASS: Feature/interrupt capabilities and PLAY match the firmware public protocol\n";
    return 0;
}
