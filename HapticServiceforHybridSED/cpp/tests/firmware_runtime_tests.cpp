#include <cstdint>
#include <iostream>
#include <stdexcept>
static std::uint32_t mask;
static bool pendingOutput,pendingUart,fastPoll,irqPending,injectAtBarrier,wfiCalled,handled;
static int injectionPoint=-1;
static bool injected;
static volatile std::uint32_t* epochAddress;
static void deliver(){handled=true;irqPending=false;if(epochAddress)*epochAddress=*epochAddress+1;}
static void inject(int point){
    if(injectionPoint==point&&!injected){injected=true;if(mask)irqPending=true;else deliver();}
}
static std::uint32_t __get_PRIMASK(){const auto prior=mask;inject(0);return prior;}
static void __disable_irq(){mask=1;inject(1);}
static void __set_PRIMASK(std::uint32_t value){inject(6);mask=value;if(!mask&&irqPending)deliver();inject(7);}
static std::uint8_t APP_I2CHID_HasPendingOutput(){inject(2);return pendingOutput;}
static std::uint8_t APP_UART_HasPendingRx(){return pendingUart;}
static std::uint8_t APP_HapticTx_NeedsFastPoll(){inject(3);return fastPoll;}
static void __DSB(){if(injectAtBarrier)irqPending=true;inject(4);}
static void __WFI(){if(mask!=1)throw std::runtime_error("check and sleep must remain atomic");wfiCalled=true;inject(5);}
static void __ISB(){}
#include "app_runtime_idle.h"
static void check(bool condition,const char* label){if(!condition)throw std::runtime_error(label);}
int main(){try{
    volatile std::uint32_t epoch=9;
    check(!APP_Runtime_TryIdle(&epoch,8)&&!wfiCalled&&!mask,"IRQ handled before sleep skips WFI");
    pendingOutput=true;check(!APP_Runtime_TryIdle(&epoch,9),"remaining FIFO batch never sleeps");pendingOutput=false;
    pendingUart=true;check(!APP_Runtime_TryIdle(&epoch,9),"remaining debug UART bytes never sleep");pendingUart=false;
    fastPoll=true;check(!APP_Runtime_TryIdle(&epoch,9),"DMA FIFO drain never sleeps");fastPoll=false;
    injectAtBarrier=true;
    check(APP_Runtime_TryIdle(&epoch,9)&&wfiCalled&&handled&&!mask,"IRQ after check remains pending until mask restored");
    wfiCalled=false;mask=1;
    check(!APP_Runtime_TryIdle(&epoch,9)&&mask==1&&!wfiCalled,"preserve caller interrupt mask and avoid masked sleep");
    unsigned cases=0,injectedCases=0;
    injectAtBarrier=false;epochAddress=&epoch;
    // Exercise a handled IRQ before disable, pending IRQs throughout the atomic
    // check/sleep region, and IRQs around mask restoration. Conditional paths
    // intentionally do not reach every injection point in every case.
    for(unsigned repeat=0;repeat<1000;++repeat)for(unsigned flags=0;flags<32;++flags)for(int point=0;point<8;++point){
        epoch=9;mask=(flags&1)?1:0;pendingOutput=(flags&2)!=0;fastPoll=(flags&4)!=0;
        pendingUart=(flags&16)!=0;
        const bool changed=(flags&8)!=0;const auto originalMask=mask;
        irqPending=injected=wfiCalled=handled=false;injectionPoint=point;
        const auto slept=APP_Runtime_TryIdle(&epoch,changed?8:9);
        check(mask==originalMask,"IRQ mask was not restored");
        check(bool(slept)==wfiCalled,"sleep return disagrees with WFI call");
        if(originalMask||pendingOutput||pendingUart||fastPoll||changed)check(!slept,"slept despite known work or masked caller");
        if(injected&&!originalMask)check(handled&&!irqPending&&epoch==10,"arriving IRQ lost across sleep boundary");
        if(injected&&originalMask)check(!handled&&irqPending&&epoch==9,"masked caller's IRQ was executed");
        ++cases;if(injected)++injectedCases;
    }
    std::cout<<"PASS: production idle handshake "<<cases<<" boundary cases, "<<injectedCases
             <<" injected IRQs (CPU instruction behavior modeled; not board validation)\n";
    return 0;
}catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}}
