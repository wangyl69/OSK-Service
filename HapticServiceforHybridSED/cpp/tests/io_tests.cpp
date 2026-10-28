#include "win_io.hpp"
#include <chrono>
#include <iostream>
#include <string>
#include <thread>

int main() {
    const auto name=L"\\\\.\\pipe\\HybridIoTest-"+std::to_wstring(GetCurrentProcessId());
    HANDLE server=CreateNamedPipeW(name.c_str(),PIPE_ACCESS_INBOUND|FILE_FLAG_OVERLAPPED,
                                   PIPE_TYPE_BYTE|PIPE_READMODE_BYTE|PIPE_WAIT,1,4096,4096,0,nullptr);
    if(server==INVALID_HANDLE_VALUE)return 1;
    HANDLE client=CreateFileW(name.c_str(),GENERIC_WRITE,0,nullptr,OPEN_EXISTING,0,nullptr);
    if(client==INVALID_HANDLE_VALUE){CloseHandle(server);return 2;}
    std::atomic_bool stop=false;
    char value=0;DWORD count=0;
    auto start=std::chrono::steady_clock::now();
    bool ok=true;
    for(int i=0;i<5;++i)ok=ok&&!hybrid::overlappedIo(server,&value,1,false,0,count,stop);
    const auto micros=std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now()-start).count();
    char sent='A';WriteFile(client,&sent,1,&count,nullptr);
    ok=ok&&hybrid::overlappedIo(server,&value,1,false,100,count,stop)&&value=='A'&&count==1;
    stop=true;
    ok=ok&&!hybrid::overlappedIo(server,&value,1,false,100,count,stop);
    sent='B';WriteFile(client,&sent,1,&count,nullptr);
    ok=ok&&hybrid::overlappedIo(server,&value,1,false,100,count,stop,false)&&value=='B';
    {
        hybrid::AsyncReportRead pending;
        std::array<unsigned char,32> report{};
        pending.start(server);
        ok=ok&&!pending.poll(report,count); // Returns while the Input report is absent.
        sent='C';WriteFile(client,&sent,1,&count,nullptr);
        for(unsigned i=0;i<100;++i){if(pending.poll(report,count)){ok=ok&&count==1&&report[0]=='C';break;}if(i==99)ok=false;Sleep(1);}
        pending.start(server);pending.cancel(); // Cancellation joins before buffer reuse.
        sent='D';WriteFile(client,&sent,1,&count,nullptr);
        pending.start(server);
        ok=ok&&pending.poll(report,count)&&report[0]=='D';
        pending.start(server); // Destructor must safely cancel a live request.
    }
    {
        // Repeated cancelled reads must not consume a later key or retain the
        // previous completion signal when one event is reused across requests.
        hybrid::ReusableOverlappedIo input;
        stop=false;
        for(unsigned i=0;i<1000;++i) {
            ok=ok&&!input.perform(server,&value,1,false,0,count,stop);
            sent=static_cast<char>(i%127+1);
            DWORD written=0;
            ok=ok&&WriteFile(client,&sent,1,&written,nullptr)&&written==1;
            ok=ok&&input.perform(server,&value,1,false,100,count,stop)&&count==1&&value==sent;
        }
    }
    {
        // A notification posted before entering wait is retained. Exercise
        // this handshake repeatedly to cover wake-before-wait transitions.
        hybrid::WorkSignal input,ack;
        input.notify_one();ok=ok&&input.wait(0)&&!input.wait(0);
        std::atomic_uint received=0;
        std::thread consumer([&]{for(unsigned i=0;i<1000;++i){if(!input.wait(2000))break;++received;ack.notify_one();}});
        for(unsigned i=0;i<1000;++i){input.notify_one();if(!ack.wait(2000)){ok=false;break;}}
        consumer.join();ok=ok&&received==1000;
    }
    CloseHandle(client);CloseHandle(server);
    std::cout<<"5 real pipe zero-wait drains: "<<micros<<" us; cancelled reads did not consume later data\n";
    return ok ? 0 : 3;
}
