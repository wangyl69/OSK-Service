#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace hybrid {
enum class Work { Play, Verify, Configure, Diagnose, Keepalive, Wait };
inline Work chooseWork(bool queued,bool config,bool diagnose,std::uint64_t sinceVerify,
                       std::uint64_t idle,unsigned interval) {
    // Avoid starving the session deadline under an endless stream of input.
    if(sinceVerify>=20000) return Work::Verify;
    if(queued) return Work::Play;
    if(config && idle>=100) return Work::Configure;
    if(sinceVerify>=10000 && idle>=50) return Work::Verify;
    if(diagnose && idle>=100) return Work::Diagnose;
    if(interval && idle>=interval) return Work::Keepalive;
    return Work::Wait;
}
inline unsigned idleWaitMs(bool awaitingAck,bool config,bool diagnose,std::uint64_t sinceVerify,
                           std::uint64_t idle,std::uint64_t sinceStatus,unsigned interval) {
    if(awaitingAck)return 10; // Nonblocking ACK completion checks; input still notifies the CV.
    const auto remaining=[](std::uint64_t limit,std::uint64_t age){return age>=limit?0:limit-age;};
    auto wait=std::min(remaining(20000,sinceVerify),
                       std::max(remaining(10000,sinceVerify),remaining(50,idle)));
    if(config)wait=std::min(wait,remaining(100,idle));
    if(diagnose)wait=std::min(wait,std::max(remaining(100,idle),remaining(1000,sinceStatus)));
    if(interval)wait=std::min(wait,remaining(interval,idle));
    return static_cast<unsigned>(std::max<std::uint64_t>(1,wait));
}
inline unsigned retryDelay(unsigned failures) {
    return std::min(30000u,3000u << std::min(failures ? failures-1 : 0,4u));
}
inline bool enterFault(unsigned failures,bool missingDevice) {return !missingDevice&&failures>=6;}
struct Samples {
    std::array<std::uint64_t,4096> data{};
    std::size_t count=0,next=0;
    void add(std::uint64_t us) {data[next]=us;next=(next+1)%data.size();count=std::min(count+1,data.size());}
    std::uint64_t percentile(unsigned percent) const {
        if(!count) return 0;
        std::vector<std::uint64_t> values(data.begin(),data.begin()+count);
        std::sort(values.begin(),values.end());
        return values[(count*percent+99)/100-1];
    }
};
// Fixed storage avoids allocator work while the input/worker queue lock is held.
template<class T,std::size_t Capacity> class FixedQueue {
    std::array<T,Capacity> data_{};
    std::size_t head_=0,count_=0;
public:
    bool empty() const{return count_==0;}
    std::size_t size() const{return count_;}
    bool tryPush(const T& value){if(count_==Capacity)return false;data_[(head_+count_)%Capacity]=value;++count_;return true;}
    const T& front() const{return data_[head_];}
    void pop_front(){head_=(head_+1)%Capacity;--count_;}
    void clear(){head_=0;count_=0;}
};
struct WaitTrace {
    std::uint64_t submitLockedUs=0,enqueuedUs=0,workerLockRequestUs=0,workerLockedUs=0,waitReturnedUs=0;
    std::uint32_t inputThread=0,workerThread=0;
    std::uint64_t waitEnteredUs=0;
    unsigned waitTimeoutMs=0,waitSignaled=0;
};
struct TraceSample {std::uint64_t sequence=0,receivedUs=0,completedUs=0,dequeuedUs=0,triggerUs=0;WaitTrace wait{};};
struct TraceRing {
    std::array<TraceSample,128> data{};
    std::uint64_t total=0;
    void add(std::uint64_t received,std::uint64_t completed,std::uint64_t dequeued=0,std::uint64_t trigger=0,WaitTrace wait={}) {
        data[total%data.size()]={total+1,received,completed,dequeued,trigger,wait};++total;
    }
    std::vector<TraceSample> snapshot() const {
        std::vector<TraceSample> out;
        const auto count=std::min<std::uint64_t>(total,data.size());
        for(auto i=total-count;i<total;++i)out.push_back(data[i%data.size()]);
        return out;
    }
};
inline std::string jsonString(const std::string& text) {
    std::string out="\"";
    for(unsigned char ch:text) {
        if(ch=='"'||ch=='\\'){out+='\\';out+=static_cast<char>(ch);}
        else if(ch>=32) out+=static_cast<char>(ch);
        else out+=' ';
    }
    return out+'"';
}
}
