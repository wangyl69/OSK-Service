#include <cstdint>
#include <iostream>
#include <stdexcept>
static std::uint32_t mask;
static bool pendingOutput,fastPoll,irqPending,injectAtBarrier,wfiCalled,handled;
static std::uint32_t __get_PRIMASK(){return mask;}
static void __disable_irq(){mask=1;}
static void __set_PRIMASK(std::uint32_t value){mask=value;if(!mask&&irqPending){handled=true;irqPending=false;}}
static std::uint8_t APP_I2CHID_HasPendingOutput(){return pendingOutput;}
static std::uint8_t APP_HapticTx_NeedsFastPoll(){return fastPoll;}
static void __DSB(){if(injectAtBarrier)irqPending=true;}
static void __WFI(){if(mask!=1)throw std::runtime_error("check and sleep must remain atomic");wfiCalled=true;}
static void __ISB(){}
#include "app_runtime_idle.h"
static void check(bool condition,const char* label){if(!condition)throw std::runtime_error(label);}
int main(){try{
    volatile std::uint32_t epoch=9;
    check(!APP_Runtime_TryIdle(&epoch,8)&&!wfiCalled&&!mask,"IRQ handled before sleep skips WFI");
    pendingOutput=true;check(!APP_Runtime_TryIdle(&epoch,9),"remaining FIFO batch never sleeps");pendingOutput=false;
    fastPoll=true;check(!APP_Runtime_TryIdle(&epoch,9),"DMA FIFO drain never sleeps");fastPoll=false;
    injectAtBarrier=true;
    check(APP_Runtime_TryIdle(&epoch,9)&&wfiCalled&&handled&&!mask,"IRQ after check remains pending until mask restored");
    wfiCalled=false;mask=1;
    check(!APP_Runtime_TryIdle(&epoch,9)&&mask==1&&!wfiCalled,"preserve caller interrupt mask and avoid masked sleep");
    std::cout<<"PASS: production idle handshake boundary cases (CPU instruction behavior modeled; not board validation)\n";
    return 0;
}catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}}
