#include "core.hpp"
#include "policy.hpp"
#include <cstdlib>
#include <iostream>
#include <stdexcept>
using namespace hybrid;
static void check(bool value,const char* label){if(!value)throw std::runtime_error(label);}
int main(){
    try {
        check(chooseWork(true,true,true,10001,500,250)==Work::Play,"queued PLAY precedes background verification/config");
        check(chooseWork(true,false,false,20000,500,250)==Work::Verify,"deadline verification cannot starve");
        check(chooseWork(false,false,false,100,10,250)==Work::Wait,"recent I/O suppresses keepalive");
        check(chooseWork(false,false,false,100,300,250)==Work::Keepalive,"idle keepalive");
        check(chooseWork(false,false,false,100,300,0)==Work::Wait,"keepalive can be disabled");
        check(chooseWork(false,true,true,100,15,250)==Work::Wait,"maintenance waits for an idle window");
        check(chooseWork(false,true,true,100,110,250)==Work::Configure,"idle maintenance can progress");
        check(idleWaitMs(false,false,false,1000,20,1000,250)==230,"idle worker waits until keepalive, not fixed 10ms polling");
        check(idleWaitMs(false,false,false,1000,200,1000,0)==9000,"disabled keepalive still schedules health verification");
        check(idleWaitMs(true,true,true,1000,200,1000,250)==10,"pending ACK keeps bounded completion checks");
        check(idleWaitMs(false,true,false,1000,60,1000,250)==40,"pending configuration deadline");
        check(idleWaitMs(false,false,true,1000,100,900,250)==100,"diagnostics rate limit retained");
        check(idleWaitMs(false,false,false,9990,0,1000,0)==50,"verification waits for age and idle window");
        check(idleWaitMs(false,false,false,19990,0,1000,0)==10,"forced health deadline retained");
        check(retryDelay(1)==3000&&retryDelay(2)==6000&&retryDelay(8)==30000,"bounded retry backoff");
        check(!enterFault(5,false)&&enterFault(6,false)&&!enterFault(20,true),"fault escalation distinguishes absent device");
        Samples samples;for(unsigned i=1;i<=100;++i)samples.add(i);
        FixedQueue<unsigned,8> queue;
        for(unsigned i=0;i<8;++i)check(queue.tryPush(i),"fixed queue accepts capacity");
        check(!queue.tryPush(8)&&queue.size()==8,"fixed queue overflow preserves contents");
        for(unsigned i=0;i<4;++i){check(queue.front()==i,"fixed queue FIFO");queue.pop_front();}
        for(unsigned i=8;i<12;++i)check(queue.tryPush(i),"fixed queue wraps");
        for(unsigned i=4;i<12;++i){check(queue.front()==i,"wrapped FIFO remains ordered");queue.pop_front();}
        check(queue.empty(),"queue drains");queue.tryPush(42);queue.clear();check(queue.empty()&&queue.tryPush(7)&&queue.front()==7,"clear resets queue");
        check(samples.percentile(50)==50&&samples.percentile(95)==95&&samples.percentile(99)==99,"latency percentiles");
        for(unsigned i=0;i<5000;++i)samples.add(42);
        check(samples.count==4096&&samples.percentile(99)==42,"bounded telemetry ring");
        TraceRing trace;for(unsigned i=0;i<200;++i)trace.add(i,i+1);
        const auto records=trace.snapshot();
        check(records.size()==128&&records.front().sequence==73&&records.back().sequence==200,"bounded trace preserves FIFO order");
        TraceRing detailed;detailed.add(10,40,20,30);auto stages=detailed.snapshot().front();
        check(stages.receivedUs==10&&stages.dequeuedUs==20&&stages.triggerUs==30&&stages.completedUs==40,"detailed trace preserves stage boundaries");
        check(jsonString("a\"b\\c")=="\"a\\\"b\\\\c\"","diagnostic JSON escaping");
        check(sessionAlive(0x03),"open and warm session alive");
        check(sessionAlive(0x05),"open and busy session must not force reconnect");
        check(!sessionAlive(0x00)&&!sessionAlive(0x02),"closed session not alive");
        auto r=play(0x1234,0x5678,2,3,0x9ABC);
        check(r[0]==1&&r[1]==2&&r[2]==1&&r[3]==1,"report preamble");
        check(r[4]==0xBC&&r[5]==0x9A&&r[7]==0x34&&r[8]==0x12,"LE sequence/key");
        check(r[14]==2&&r[15]==3&&r[16]==1&&r[18]==0x78&&r[19]==0x56,"play fields");
        check(r[31]==crc8(r),"CRC coverage");
        // Golden bytes from the existing Python v2 encoder (CRC enabled).
        const Report goldenPlay{0x01,0x02,0x01,0x01,0xbc,0x9a,0x00,0x34,
            0x12,0x00,0x00,0x00,0x00,0x00,0x02,0x03,0x01,0x00,0x78,0x56,
            0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xb2};
        check(r==goldenPlay,"PLAY parity with reference encoder");
        auto ctrl=control(0x30,3,1);check(ctrl[0]==2&&ctrl[2]==0x30&&ctrl[6]==3&&ctrl[31]==crc8(ctrl),"control");
        check(ctrl[31]==0xd7,"control parity with reference encoder");
        std::cout<<"PASS: HID v2 fields/CRC and reference encoder parity\n";return 0;
    }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
