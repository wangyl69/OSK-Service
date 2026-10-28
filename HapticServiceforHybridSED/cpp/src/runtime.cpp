#include "runtime.hpp"
#include "policy.hpp"
#include "win_io.hpp"
#include <hidsdi.h>
#include <setupapi.h>
#include <sddl.h>
#include <wtsapi32.h>
#include <chrono>
#include <fstream>
#include <filesystem>
#include <iostream>
#include <memory>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <thread>

namespace hybrid {
std::atomic_bool stopping=false, paused=false;
static std::mutex logMutex;
static std::ofstream logStream;
static std::filesystem::path logPath;
static std::uintmax_t logSize=0;
void configureLog(const std::wstring& path) {
    if (!path.empty()) {
        auto p=std::filesystem::path(path);
        if (p.has_parent_path()) std::filesystem::create_directories(p.parent_path());
        logPath=p;
        logSize=std::filesystem::exists(p) ? std::filesystem::file_size(p) : 0;
        logStream.open(p, std::ios::app);
        if (!logStream) throw std::runtime_error("Cannot open log file");
    }
}
void log(const std::string& text) {
    std::lock_guard lock(logMutex);
    auto line=std::to_string(GetTickCount64())+" "+text+"\n";
    std::cout << line << std::flush;
    if (logStream) {
        if(logSize+line.size()>2*1024*1024) {
            logStream.close();
            std::error_code ec;
            for(int i=3;i>=1;--i) {
                auto dst=logPath;dst+=L"."+std::to_wstring(i);
                auto src=logPath;if(i>1) src+=L"."+std::to_wstring(i-1);
                std::filesystem::remove(dst,ec);ec.clear();
                if(std::filesystem::exists(src,ec)) std::filesystem::rename(src,dst,ec);
            }
            logStream.open(logPath,std::ios::trunc);logSize=0;
        }
        logStream << line << std::flush;logSize+=line.size();
    }
    OutputDebugStringA(line.c_str());
}
static void fail(const char* operation) {
    throw std::runtime_error(std::string(operation)+" error="+std::to_string(GetLastError()));
}
static int latencyPriority(const Options& options,const char* role) {
    const auto requested=options.threadPriority=="highest" ? THREAD_PRIORITY_HIGHEST : options.threadPriority=="above-normal" ? THREAD_PRIORITY_ABOVE_NORMAL : THREAD_PRIORITY_NORMAL;
    if(!SetThreadPriority(GetCurrentThread(),requested))
        log(std::string("Thread priority failed role=")+role+" error="+std::to_string(GetLastError()));
    const auto effective=GetThreadPriority(GetCurrentThread());
    log(std::string("Latency thread role=")+role+" Win32Id="+std::to_string(GetCurrentThreadId())+
        " requested="+options.threadPriority+" effective="+std::to_string(effective));
    return effective;
}
class Handle {
    HANDLE h_=INVALID_HANDLE_VALUE;
public:
    explicit Handle(HANDLE h=INVALID_HANDLE_VALUE):h_(h){}
    ~Handle(){ reset(); }
    Handle(const Handle&)=delete;
    Handle& operator=(const Handle&)=delete;
    HANDLE get() const{return h_;}
    bool valid() const{return h_ && h_!=INVALID_HANDLE_VALUE;}
    void reset(HANDLE h=INVALID_HANDLE_VALUE){if(valid()) CloseHandle(h_); h_=h;}
};
struct Security {
    PSECURITY_DESCRIPTOR descriptor=nullptr;
    SECURITY_ATTRIBUTES attrs{sizeof(SECURITY_ATTRIBUTES),nullptr,FALSE};
    Security() {
        // LocalSystem/admin + interactive desktop users, never anonymous/network.
        if(!ConvertStringSecurityDescriptorToSecurityDescriptorW(
            L"D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;IU)",SDDL_REVISION_1,&descriptor,nullptr))
            fail("Pipe security");
        attrs.lpSecurityDescriptor=descriptor;
    }
    ~Security(){if(descriptor) LocalFree(descriptor);}
};
static std::wstring path(const std::wstring& name){return L"\\\\.\\pipe\\"+name;}
// Cancellable overlapped I/O. Never leave an OVERLAPPED on a dead stack.
static bool io(HANDLE h, void* data, DWORD size, bool write, DWORD timeout, DWORD& done,
               bool honorStop=true) {
    return overlappedIo(h,data,size,write,timeout,done,stopping,honorStop);
}

static bool connect(HANDLE h) {
    Handle event(CreateEventW(nullptr,TRUE,FALSE,nullptr));
    if(!event.valid()) fail("CreateEvent (pipe connect)");
    OVERLAPPED ov{}; ov.hEvent=event.get();
    if(ConnectNamedPipe(h,&ov)) return true;
    auto error=GetLastError();
    if(error==ERROR_PIPE_CONNECTED) return true;
    if(error!=ERROR_IO_PENDING) fail("ConnectNamedPipe");
    while(!stopping) {
        const auto result=WaitForSingleObject(event.get(),20);
        if(result==WAIT_OBJECT_0) {
            DWORD done=0;
            return GetOverlappedResult(h,&ov,&done,FALSE)!=FALSE;
        }
        if(result==WAIT_FAILED) {
            const auto waitError=GetLastError();
            CancelIoEx(h,&ov);DWORD done=0;GetOverlappedResult(h,&ov,&done,TRUE);
            SetLastError(waitError);
            fail("WaitForSingleObject (pipe connect)");
        }
    }
    CancelIoEx(h,&ov); DWORD done=0; GetOverlappedResult(h,&ov,&done,TRUE);
    return false;
}
static HANDLE server(const std::wstring& name, bool messages, Security& security,bool duplex=false) {
    auto h=CreateNamedPipeW(path(name).c_str(),(duplex ? PIPE_ACCESS_DUPLEX : PIPE_ACCESS_INBOUND)|FILE_FLAG_OVERLAPPED|
        FILE_FLAG_FIRST_PIPE_INSTANCE,(messages ? PIPE_TYPE_MESSAGE|PIPE_READMODE_MESSAGE :
        PIPE_TYPE_BYTE|PIPE_READMODE_BYTE)|PIPE_REJECT_REMOTE_CLIENTS,1,4096,4096,0,&security.attrs);
    if(h==INVALID_HANDLE_VALUE) fail("CreateNamedPipe (another receiver may own this pipe)");
    return h;
}

struct Snapshot {
    bool valid=false, rejectedAvailable=false, countersAvailable=false;
    unsigned state=0,wave=0,intensity=0,rawQueue=0,inputQueue=0;
    unsigned accepted=0,rejected=0,i2cErrors=0;
};
static unsigned u16(const Report& r,unsigned index){return r[index]|(r[index+1]<<8);}
class Device {
    Handle handle_;
    AsyncReportRead statusRead_;
    bool statusPending_=false;
    ULONGLONG statusStarted_=0;
    Options options_;
    bool open_=false;
    std::uint16_t seq_=0;
    unsigned mockAccepted_=0;
    std::uint8_t wave_=1,intensity_=1;
    std::string mode_;
    std::string capabilities_="null";
    std::uint16_t next(){if(++seq_==0) ++seq_;return seq_;}
    Report feature(std::uint8_t id) {
        Report r{};r[0]=id;
        if(options_.mock) {
            if(options_.mockFeatureUnavailable) throw std::runtime_error("Feature unavailable (mock)");
            r[1]=2;r[2]=id==0x10 ? 1 : 2;r[3]=1;r[6]=intensity_;r[7]=wave_;r[14]=open_ ? 3 : 0;
            if(id==0x10){r[6]=0x87;r[7]=0;r[8]=2;r[9]=2;r[11]=4;r[14]=32;r[15]=1;r[16]=2;r[17]=0x81;r[18]=0x10;r[19]=0x11;r[20]=10;r[21]=30;}
            else word(r,17,static_cast<std::uint16_t>(mockAccepted_));
            r[31]=crc8(r);
        }else if(!HidD_GetFeature(handle_.get(),r.data(),static_cast<ULONG>(r.size()))) fail("HidD_GetFeature");
        if(r[0]!=id||r[1]!=2||r[2]!=(id==0x10 ? 1 : 2)||((r[3]&1)&&r[31]!=crc8(r)))
            throw std::runtime_error("Invalid feature report/version/CRC");
        return r;
    }
    void send(Report r,bool final=false) {
        if(options_.mock) {
            if(r[0]==1) {++mockAccepted_;if(!options_.mockQuiet)log("MOCK PLAY key="+std::to_string(r[7]|(r[8]<<8))+
                " waveform="+std::to_string(r[14])+" intensity="+std::to_string(r[15]));}
            return;
        }
        DWORD done=0;
        if(!io(handle_.get(),r.data(),static_cast<DWORD>(r.size()),true,250,done,!final)||done!=r.size())
            throw std::runtime_error("HID WriteFile failed/timed out");
    }
    Report command(std::uint8_t op,std::uint8_t value=0) {
        auto r=control(op,value,next());
        if(mode_=="interrupt") {r[3]|=2;r[31]=crc8(r);}
        // Drain old responses before a control request; firmware ACK has no request ID.
        if(mode_=="interrupt"&&!options_.mock) {
            for(unsigned i=0;i<8;++i) {Report old{};DWORD n=0;if(!io(handle_.get(),old.data(),32,false,0,n)) break;}
        }
        send(r);
        if(mode_!="interrupt") return {};
        if(options_.mock) {
            Report ack{};ack[0]=0x81;ack[1]=2;ack[2]=1;ack[6]=1;ack[7]=2;ack[8]=op;
            ack[9]=3;ack[13]=wave_;ack[14]=intensity_;word(ack,28,static_cast<std::uint16_t>(mockAccepted_));
            if(op==0x33){ack[2]=3;ack[10]=0x87;ack[11]=0;ack[12]=2;ack[13]=2;ack[15]=4;ack[18]=32;ack[19]=1;ack[20]=2;ack[21]=0x81;ack[22]=0x10;ack[23]=0x11;ack[24]=10;ack[25]=30;}
            ack[31]=crc8(ack);
            return ack;
        }
        auto deadline=GetTickCount64()+250;
        do {
            Report ack{};DWORD count=0;
            if(!io(handle_.get(),ack.data(),32,false,250,count)) break;
            if(count!=32||ack[0]!=0x81||ack[1]!=2||((ack[3]&1)&&ack[31]!=crc8(ack))) continue;
            if(ack[7]!=2||ack[8]!=op) continue;
            if(ack[6]!=1) throw std::runtime_error("MCU control rejected result="+std::to_string(ack[6]));
            return ack;
        }while(GetTickCount64()<deadline&&!stopping);
        throw std::runtime_error("MCU interrupt ACK timeout");
    }
public:
    explicit Device(const Options& options):options_(options),mode_(options.statusMode){}
    ~Device(){close();}
    std::string mode() const{return mode_;}
    std::string capabilities() const{return capabilities_;}
    bool statusPending() const{return statusPending_;}
    // 2026-10-23: runtime interrupt status must not synchronously wait for ACK
    // on the PLAY worker. Only one control query may be in flight (ACK has no ID).
    void beginStatus() {
        if(statusPending_)throw std::runtime_error("Concurrent status query");
        if(mode_!="interrupt")throw std::runtime_error("Async status requires interrupt mode");
        if(!options_.mock) {
            for(unsigned i=0;i<8;++i){Report old{};DWORD n=0;if(!io(handle_.get(),old.data(),32,false,0,n))break;}
            statusRead_.start(handle_.get());
        }
        statusPending_=true;statusStarted_=GetTickCount64();
        auto r=control(0x32,0,next());r[3]|=2;r[31]=crc8(r);send(r);
    }
    std::optional<Snapshot> pollStatus() {
        if(!statusPending_)return std::nullopt;
        if(GetTickCount64()-statusStarted_>=250)throw std::runtime_error("MCU async status ACK timeout");
        if(options_.mock) {
            if(GetTickCount64()-statusStarted_<options_.mockStatusDelayMs)return std::nullopt;
            statusPending_=false;return snapshot();
        }
        Report ack{};DWORD count=0;
        if(!statusRead_.poll(ack,count))return std::nullopt;
        if(count!=32||ack[0]!=0x81||ack[1]!=2||((ack[3]&1)&&ack[31]!=crc8(ack))||ack[7]!=2||ack[8]!=0x32) {
            statusRead_.start(handle_.get());return std::nullopt;
        }
        if(ack[6]!=1)throw std::runtime_error("MCU async status rejected");
        Snapshot s;s.valid=true;s.state=ack[9];s.wave=ack[13];s.intensity=ack[14];s.rawQueue=ack[11];s.inputQueue=ack[26];
        statusPending_=false;return s;
    }
    void rememberCaps(unsigned flags,unsigned motors,unsigned active,unsigned waves,unsigned heartbeat,unsigned failsafe) {
        capabilities_="{\"flags\":"+std::to_string(flags)+",\"motor_count\":"+std::to_string(motors)+
            ",\"active_motors\":"+std::to_string(active)+",\"waveform_count\":"+std::to_string(waves)+
            ",\"heartbeat_seconds\":"+std::to_string(heartbeat)+",\"failsafe_seconds\":"+std::to_string(failsafe)+"}";
    }
    Snapshot snapshot() {
        if(mode_=="write-only") return {};
        Snapshot s;s.valid=true;
        if(mode_=="feature") {
            auto r=feature(0x11);s.state=r[14];s.wave=r[7];s.intensity=r[6];s.rawQueue=r[15];s.inputQueue=r[16];
            s.accepted=u16(r,17);s.rejected=u16(r,19);s.i2cErrors=u16(r,21);s.rejectedAvailable=true;s.countersAvailable=true;
        }else {
            auto r=command(0x32);s.state=r[9];s.wave=r[13];s.intensity=r[14];
            s.rawQueue=r[11];s.inputQueue=r[26];
            // Current firmware ACK leaves 28..30 reserved/zero; do not report
            // them as confirmed acceptance/error counters.
        }
        return s;
    }
    void open(unsigned waveform,unsigned intensity) {
        wave_=static_cast<std::uint8_t>(waveform);intensity_=static_cast<std::uint8_t>(intensity);
        if(!options_.mock) {
            GUID guid{};HidD_GetHidGuid(&guid);
            auto info=SetupDiGetClassDevsW(&guid,nullptr,nullptr,DIGCF_PRESENT|DIGCF_DEVICEINTERFACE);
            if(info==INVALID_HANDLE_VALUE) fail("SetupDiGetClassDevs");
            struct Cleanup{HDEVINFO h;~Cleanup(){SetupDiDestroyDeviceInfoList(h);}} cleanup{info};
            SP_DEVICE_INTERFACE_DATA entry{};entry.cbSize=sizeof(entry);
            for(DWORD i=0;SetupDiEnumDeviceInterfaces(info,nullptr,&guid,i,&entry);++i) {
                DWORD required=0;SetupDiGetDeviceInterfaceDetailW(info,&entry,nullptr,0,&required,nullptr);
                if(required<sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA_W)) continue;
                std::vector<std::uint8_t> bytes(required);
                auto detail=reinterpret_cast<SP_DEVICE_INTERFACE_DETAIL_DATA_W*>(bytes.data());detail->cbSize=sizeof(*detail);
                if(!SetupDiGetDeviceInterfaceDetailW(info,&entry,detail,required,nullptr,nullptr)) continue;
                Handle candidate(CreateFileW(detail->DevicePath,GENERIC_READ|GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE,
                    nullptr,OPEN_EXISTING,FILE_FLAG_OVERLAPPED,nullptr));
                if(!candidate.valid()) continue;
                HIDD_ATTRIBUTES a{};a.Size=sizeof(a);
                if(!HidD_GetAttributes(candidate.get(),&a)||a.VendorID!=0x06CB||a.ProductID!=0xCF00) continue;
                PHIDP_PREPARSED_DATA pp=nullptr;if(!HidD_GetPreparsedData(candidate.get(),&pp)) continue;
                HIDP_CAPS caps{};auto result=HidP_GetCaps(pp,&caps);HidD_FreePreparsedData(pp);
                if(result!=HIDP_STATUS_SUCCESS||caps.OutputReportByteLength!=32||caps.UsagePage<0xFF00) continue;
                HANDLE copy=nullptr;
                if(!DuplicateHandle(GetCurrentProcess(),candidate.get(),GetCurrentProcess(),&copy,0,FALSE,DUPLICATE_SAME_ACCESS)) fail("DuplicateHandle");
                handle_.reset(copy);break;
            }
            if(!handle_.valid()) throw std::runtime_error("NO_DEVICE: HID 06CB:CF00 vendor collection not found");
        }
        if(mode_=="auto"||mode_=="feature") {
            try {
                auto caps=feature(0x10);
                if(!capabilitiesMatch(caps,true,waveform))
                    throw std::runtime_error("INCOMPATIBLE: MCU capabilities/layout mismatch");
                rememberCaps(u16(caps,6),caps[8],caps[9],caps[11],caps[20],caps[21]);
                mode_="feature";
            }catch(const std::exception& e) {
                if(mode_!="auto"||std::string(e.what()).find("INCOMPATIBLE")!=std::string::npos||
                   std::string(e.what()).find("Invalid")!=std::string::npos) throw;
                mode_="interrupt";log("Feature unavailable; trying confirmed interrupt mode");
            }
        }
        if(mode_=="interrupt") {
            auto caps=command(0x33);
            if(!capabilitiesMatch(caps,false,waveform))
                throw std::runtime_error("INCOMPATIBLE: interrupt capabilities/layout mismatch");
            rememberCaps(u16(caps,10),caps[12],caps[13],caps[15],caps[24],caps[25]);
        }
        open_=true;command(0x20);
        if(mode_=="write-only") Sleep(80);
        else {
            bool warm=false;
            for(unsigned i=0;i<50&&!stopping;++i){auto s=snapshot();if((s.state&3)==3){warm=true;break;}Sleep(20);}
            if(!warm) throw std::runtime_error("SESSION_OPEN did not become WARM");
        }
        configure(waveform,intensity);
        log(mode_=="write-only" ? "HID DEGRADED: explicit write-only, feedback unconfirmed" : "HID WARM; waveform/intensity confirmed");
    }
    void configure(unsigned waveform,unsigned intensity) {
        wave_=static_cast<std::uint8_t>(waveform);intensity_=static_cast<std::uint8_t>(intensity);
        command(0x31,wave_);command(0x30,intensity_);
        if(mode_=="write-only") return;
        for(unsigned i=0;i<25&&!stopping;++i) {auto s=snapshot();if(s.wave==wave_&&s.intensity==intensity_)return;Sleep(20);}
        throw std::runtime_error("MCU settings not confirmed");
    }
    Snapshot heartbeat(bool verify) {
        if(verify) {
            command(0x22);
            auto s=snapshot();if(s.valid&&!sessionAlive(static_cast<std::uint8_t>(s.state))) throw std::runtime_error("Session no longer OPEN");return s;
        }
        send(control(0x22,0,next()));return {};
    }
    void trigger(const Edge& edge,std::uint64_t now) {
        send(play(edge.key,static_cast<std::uint16_t>(std::min<std::uint64_t>((now-edge.tick)*1000,65535)),wave_,intensity_,next()));
    }
    void close() noexcept {
        // Cancellation must finish before the device handle/buffer can be closed.
        statusRead_.cancel();statusPending_=false;
        if(open_){try{send(control(0x21,0,next()),true);}catch(...){}open_=false;}
        handle_.reset();
    }
};

static std::uint64_t micros() {
    static const auto frequency=[] {LARGE_INTEGER f{};QueryPerformanceFrequency(&f);return static_cast<std::uint64_t>(f.QuadPart);}();
    LARGE_INTEGER counter{};QueryPerformanceCounter(&counter);
    const auto ticks=static_cast<std::uint64_t>(counter.QuadPart);
    return (ticks/frequency)*1000000+(ticks%frequency)*1000000/frequency;
}
struct Queued {Edge edge{};std::uint64_t receivedUs=0;WaitTrace wait{};};
class Worker {
    Options options_;
    std::atomic_bool asyncStatusPending_=false;
    std::atomic_uint64_t statusQueries_=0;
    std::mutex mutex_;
    std::mutex metricsMutex_;
    WorkSignal changed_;
    FixedQueue<Queued,8> queue_;
    const bool traceEnabled_;
    std::thread thread_;
    bool ready_=false,enabled_=true,recover_=false,diagnose_=false,preferInterrupt_=false,fault_=false;
    unsigned requested_=1,applied_=0,failures_=0;
    std::string state_="CONNECTING",mode_="unknown",lastError_,phase_,capabilities_="null";
    std::uint64_t generation_=0,heartbeatCount_=0,keepaliveCount_=0,backgroundSkipped_=0;
    std::atomic_int inputPriority_=THREAD_PRIORITY_ERROR_RETURN,hidPriority_=THREAD_PRIORITY_ERROR_RETURN;
    std::uint64_t stale_=0,overflow_=0,unavailable_=0,failedPlay_=0,healthAlerts_=0,snapshotTick_=0;
    Snapshot cached_;
    Samples queueTimes_,hidTimes_,totalTimes_,backgroundTimes_;
    TraceRing trace_;
    TraceRing signals_; // Opt-in diagnostic ring, keyed by submit QPC timestamp.
    void state(const std::string& value){std::lock_guard lock(mutex_);state_=value;}
    void cache(const Snapshot& value){
        bool alert=false;
        {
            std::lock_guard lock(mutex_);
            alert=value.valid&&cached_.valid&&((value.rejectedAvailable&&value.rejected>cached_.rejected)||value.i2cErrors>cached_.i2cErrors);
            if(alert)++healthAlerts_;
            cached_=value;snapshotTick_=GetTickCount64();
        }
        // Never hold the input queue lock across filesystem/console logging.
        if(alert)log("MCU health warning: rejected PLAY or I2C error counter increased");
    }
    bool active(){std::lock_guard lock(mutex_);return enabled_&&!recover_&&!paused&&!stopping;}
public:
    std::atomic_uint64_t sent=0,dropped=0;
    std::atomic_uint64_t invalidCategories=0;
    explicit Worker(Options options):options_(std::move(options)),traceEnabled_(options_.mockTrace||options_.latencyTrace){}
    void inputPriority(int value){inputPriority_=value;}
    void submit(const Edge& edge) {
        const auto receivedUs=micros();
        {
            std::lock_guard lock(mutex_);
            WaitTrace wait{};
            if(traceEnabled_){wait.submitLockedUs=micros();wait.inputThread=GetCurrentThreadId();}
            if(!ready_||!enabled_||paused){++dropped;++unavailable_;return;}
            if(traceEnabled_)wait.enqueuedUs=micros();
            if(!queue_.tryPush({edge,receivedUs,wait})){++dropped;++overflow_;return;}
        }
        // Publish under lock, notify after release: a woken worker can acquire it.
        const auto signalEnter=traceEnabled_?micros():0;
        changed_.notify_one();
        const auto signalReturn=traceEnabled_?micros():0;
        if(traceEnabled_){std::lock_guard lock(metricsMutex_);signals_.add(receivedUs,signalReturn,signalEnter);}
    }
    std::string status() {
        Samples q,h,t,b;Snapshot snap;
        std::string stateValue,mode,error,phase,capabilities,requestedMode;
        unsigned requested,applied,failures,wave,intensity,interval;
        bool enabled,ready;
        std::uint64_t gen,hb,ka,skips,stale,overflow,unavailable,failed,alerts,snapshotAge;
        std::size_t depth;
        {
            std::lock_guard lock(mutex_);
            snap=cached_;
            stateValue=state_;mode=mode_;error=lastError_;phase=phase_;capabilities=capabilities_;requestedMode=options_.statusMode;
            requested=requested_;applied=applied_;failures=failures_;wave=options_.waveform;intensity=options_.intensity;
            interval=options_.keepaliveMs;enabled=enabled_;ready=ready_;gen=generation_;
            hb=heartbeatCount_;ka=keepaliveCount_;skips=backgroundSkipped_;
            stale=stale_;overflow=overflow_;unavailable=unavailable_;failed=failedPlay_;depth=queue_.size();
            alerts=healthAlerts_;snapshotAge=snapshotTick_ ? GetTickCount64()-snapshotTick_ : 0;
        }
        {std::lock_guard lock(metricsMutex_);q=queueTimes_;h=hidTimes_;t=totalTimes_;b=backgroundTimes_;}
        auto metrics=[](const Samples& x){return std::string("{\"samples\":")+std::to_string(x.count)+
            ",\"p50_us\":"+std::to_string(x.percentile(50))+",\"p95_us\":"+std::to_string(x.percentile(95))+
            ",\"p99_us\":"+std::to_string(x.percentile(99))+"}";};
        std::ostringstream o;
        o<<"{\"ok\":true,\"state\":"<<jsonString(stateValue)<<",\"status_mode\":"<<jsonString(mode)
         <<",\"requested_thread_priority\":"<<jsonString(options_.threadPriority)
         <<",\"idle_wait_mode\":"<<jsonString(options_.idleWaitMode)
         <<",\"worker_wake_mode\":\"native-event\""
         <<",\"input_thread_priority\":"<<inputPriority_.load()<<",\"hid_thread_priority\":"<<hidPriority_.load()
         <<",\"process_priority_class\":"<<GetPriorityClass(GetCurrentProcess())
         <<",\"query_thread_priority\":"<<GetThreadPriority(GetCurrentThread())
         <<",\"requested_status_mode\":"<<jsonString(requestedMode)<<",\"capabilities\":"<<capabilities
         <<",\"ready\":"<<(ready?"true":"false")<<",\"enabled\":"<<(enabled?"true":"false")
         <<",\"feedback_confirmed\":"<<((ready&&mode!="write-only")?"true":"false")
         <<",\"waveform\":"<<wave<<",\"intensity\":"<<intensity<<",\"keepalive_ms\":"<<interval
         <<",\"requested_revision\":"<<requested<<",\"applied_revision\":"<<applied
         <<",\"generation\":"<<gen<<",\"consecutive_failures\":"<<failures
         <<",\"last_error\":"<<jsonString(error)<<",\"error_phase\":"<<jsonString(phase)
         <<",\"play_sent\":"<<sent.load()<<",\"dropped\":"<<dropped.load()<<",\"queue_depth\":"<<depth
         <<",\"invalid_categories\":"<<invalidCategories.load()
         <<",\"stale\":"<<stale<<",\"overflow\":"<<overflow<<",\"unavailable\":"<<unavailable
         <<",\"play_write_failed\":"<<failed<<",\"verify_count\":"<<hb<<",\"keepalive_count\":"<<ka
         <<",\"background_deferred\":"<<skips<<",\"queue_time\":"<<metrics(q)<<",\"hid_write_time\":"<<metrics(h)
         <<",\"host_time\":"<<metrics(t)<<",\"background_io_time\":"<<metrics(b)
         <<",\"snapshot_valid\":"<<(snap.valid?"true":"false")<<",\"health_alerts\":"<<alerts<<",\"snapshot_age_ms\":"<<snapshotAge
         <<",\"async_status_pending\":"<<(asyncStatusPending_?"true":"false")<<",\"runtime_status_queries\":"<<statusQueries_.load()
         <<",\"mcu_state_flags\":"<<(snap.valid?std::to_string(snap.state):"null")
         <<",\"mcu_waveform\":"<<(snap.valid?std::to_string(snap.wave):"null")<<",\"mcu_intensity\":"<<(snap.valid?std::to_string(snap.intensity):"null")
         <<",\"mcu_raw_queue\":"<<(snap.valid?std::to_string(snap.rawQueue):"null")<<",\"mcu_input_queue\":"<<(snap.valid?std::to_string(snap.inputQueue):"null")
         <<",\"mcu_play_accepted\":"<<(snap.valid&&snap.countersAvailable?std::to_string(snap.accepted):"null")
         <<",\"mcu_play_rejected\":"<<(snap.valid&&snap.rejectedAvailable?std::to_string(snap.rejected):"null")
         <<",\"mcu_i2c_errors\":"<<(snap.valid&&snap.countersAvailable?std::to_string(snap.i2cErrors):"null")<<"}";
        return o.str();
    }
    std::string control(const std::string& request) {
        if(request=="status"||request=="ping") return status();
        if(request=="trace") {
            if(!traceEnabled_)return "{\"ok\":false,\"error\":\"trace requires --latency-trace or --mock --mock-trace\"}";
            std::vector<TraceSample> samples;
            std::vector<TraceSample> signals;
            {std::lock_guard lock(metricsMutex_);samples=trace_.snapshot();signals=signals_.snapshot();}
            std::ostringstream out;out<<"{\"ok\":true,\"clock\":\"QPC_microseconds\",\"samples\":[";
            bool first=true;for(const auto& item:samples){if(!first)out<<',';first=false;out<<'['<<item.sequence<<','<<item.receivedUs<<','<<item.completedUs<<']';}
            out<<"],\"stage_samples\":[";
            first=true;for(const auto& item:samples){if(!first)out<<',';first=false;
                out<<'['<<item.sequence<<','<<item.receivedUs<<','<<item.dequeuedUs<<','<<item.triggerUs<<','<<item.completedUs<<']';}
            out<<"],\"wait_samples\":[";
            first=true;for(const auto& item:samples){if(!first)out<<',';first=false;const auto& w=item.wait;
                out<<'['<<item.sequence<<','<<w.submitLockedUs<<','<<w.enqueuedUs<<','<<w.workerLockRequestUs<<','<<w.workerLockedUs<<','<<w.waitReturnedUs<<','<<w.inputThread<<','<<w.workerThread<<','<<w.waitEnteredUs<<','<<w.waitTimeoutMs<<','<<w.waitSignaled<<']';}
            out<<"],\"signal_samples\":[";
            first=true;for(const auto& item:signals){if(!first)out<<',';first=false;out<<'['<<item.receivedUs<<','<<item.dequeuedUs<<','<<item.completedUs<<']';}
            out<<"],\"signal_schema\":\"submit,notify_enter,notify_return\",\"wait_schema\":\"sequence,submit_locked,enqueue_stamp,worker_lock_request,worker_locked,wait_returned_before_lock,input_tid,worker_tid,wait_enter,timeout_ms,signaled\""
               <<",\"completion_kind\":"<<jsonString(options_.mock?"mock":"hid_write_complete")
               <<",\"stage_schema\":"<<jsonString(options_.mock?"sequence,submit,dequeue,trigger_entry,mock_complete":"sequence,submit,dequeue,trigger_entry,hid_write_complete")<<"}";return out.str();
        }
        std::istringstream input(request);std::string command,arg,extra;unsigned value=0;
        input>>command;
        if(request=="metrics reset") {
            // Metrics reset must not hold the input queue lock while clearing rings.
            unsigned revision;
            {std::lock_guard lock(mutex_);revision=requested_;}
            {
                std::lock_guard metrics(metricsMutex_);
                queueTimes_=Samples{};hidTimes_=Samples{};totalTimes_=Samples{};backgroundTimes_=Samples{};trace_=TraceRing{};signals_=TraceRing{};
            }
            return "{\"ok\":true,\"queued\":true,\"requested_revision\":"+std::to_string(revision)+"}";
        }
        std::lock_guard lock(mutex_);
        if(command=="set") {
            if(!(input>>arg>>value)||(input>>extra)) return "{\"ok\":false,\"error\":\"expected set waveform|intensity|keepalive VALUE\"}";
            if(arg=="waveform"&&value>=1&&value<=4){options_.waveform=value;++requested_;}
            else if(arg=="intensity"&&value>=1&&value<=3){options_.intensity=value;++requested_;}
            else if(arg=="keepalive"&&(value==0||(value>=100&&value<=10000))) options_.keepaliveMs=value;
            else return "{\"ok\":false,\"error\":\"invalid setting/range\"}";
        }else if(command=="session") {
            if(!(input>>arg)||(input>>extra)||(arg!="open"&&arg!="close")) return "{\"ok\":false,\"error\":\"expected session open|close\"}";
            enabled_=arg=="open";
            if(!enabled_){dropped+=queue_.size();queue_.clear();ready_=false;}
        }else if(command=="mode") {
            if(!(input>>arg)||(input>>extra)||(arg!="auto"&&arg!="feature"&&arg!="interrupt"&&arg!="write-only"))
                return "{\"ok\":false,\"error\":\"invalid status mode\"}";
            options_.statusMode=arg;preferInterrupt_=false;recover_=true;fault_=false;failures_=0;++requested_;
        }else if(request=="diagnose") diagnose_=true;
        else if(request=="recover"){recover_=true;fault_=false;failures_=0;}
        else return "{\"ok\":false,\"error\":\"unknown command\"}";
        changed_.notify_all();
        return "{\"ok\":true,\"queued\":true,\"requested_revision\":"+std::to_string(requested_)+"}";
    }
    void start(){thread_=std::thread([this]{run();});}
    void stop(){changed_.notify_all();if(thread_.joinable()) thread_.join();}
    void run() {
        hidPriority_=latencyPriority(options_,"hid-worker");
        while(!stopping) {
            Options current;unsigned revision;
            {
                std::unique_lock lock(mutex_);
                if(!enabled_||paused||fault_) {
                    state_=(!enabled_||paused)?"PAUSED":"FAULT";
                    lock.unlock();changed_.wait(100);lock.lock();
                    if(!enabled_||paused)continue;
                    if(fault_&&!recover_)continue;
                }
                recover_=false;current=options_;if(current.statusMode=="auto"&&preferInterrupt_)current.statusMode="interrupt";revision=requested_;state_="CONNECTING";
            }
            std::string phase="open/configure";
            try {
                Device device(current);device.open(current.waveform,current.intensity);
                auto snapshot=device.snapshot();
                {
                    std::lock_guard lock(mutex_);queue_.clear();ready_=enabled_&&!paused;
                    state_=device.mode()=="write-only"?"DEGRADED":"WARM";mode_=device.mode();capabilities_=device.capabilities();
                    applied_=revision;failures_=0;++generation_;cached_=snapshot;snapshotTick_=GetTickCount64();
                }
                auto verified=GetTickCount64(),lastIo=verified,lastStatus=verified;
                bool pendingVerify=false;
                std::uint64_t waitReturnedUs=0;
                std::uint64_t waitEnteredUs=0;
                unsigned waitTimeoutMs=0,waitSignaled=0;
                while(!stopping) {
                    if(device.statusPending()) {
                        phase="async_status";
                        if(auto completed=device.pollStatus()) {
                            if(pendingVerify&&!sessionAlive(static_cast<std::uint8_t>(completed->state)))
                                throw std::runtime_error("Session no longer OPEN");
                            cache(*completed);
                            asyncStatusPending_=false;
                            if(pendingVerify){verified=GetTickCount64();std::lock_guard lock(mutex_);++heartbeatCount_;}
                            pendingVerify=false;
                        }
                    }
                    Work work;std::optional<Queued> event;unsigned wave,intensity,configRevision;
                    std::uint64_t dequeuedUs=0;
                    const auto lockRequestUs=traceEnabled_?micros():0;
                    {
                        std::unique_lock lock(mutex_);
                        const auto lockedUs=traceEnabled_?micros():0;
                        // Check state with this acquisition, instead of an
                        // unmeasured active() lock before every queue lookup.
                        if(!enabled_||recover_||paused||stopping)break;
                        auto now=GetTickCount64();
                        work=chooseWork(!queue_.empty(),requested_!=applied_,diagnose_&&now-lastStatus>=1000,now-verified,now-lastIo,options_.keepaliveMs);
                        // An in-flight ACK never blocks PLAY. Serialize all other
                        // control traffic until it completes; coalesce explicit
                        // diagnose requests and rate-limit real reads to <=1 Hz.
                        if(device.statusPending())work=queue_.empty()?Work::Wait:Work::Play;
                        wave=options_.waveform;intensity=options_.intensity;configRevision=requested_;
                        if(work==Work::Play){event=queue_.front();queue_.pop_front();if(traceEnabled_){dequeuedUs=micros();event->wait.workerLockRequestUs=lockRequestUs;event->wait.workerLockedUs=lockedUs;event->wait.waitReturnedUs=waitReturnedUs;event->wait.workerThread=GetCurrentThreadId();event->wait.waitEnteredUs=waitEnteredUs;event->wait.waitTimeoutMs=waitTimeoutMs;event->wait.waitSignaled=waitSignaled;}waitReturnedUs=0;waitEnteredUs=0;waitTimeoutMs=0;waitSignaled=0;if(now-verified>=10000)++backgroundSkipped_;}
                        if(work==Work::Wait){
                            // Deadline wait is opt-in: fewer wakeups can allow deeper
                            // CPU idle states. Preserve fixed 10ms until first-key A/B
                            // evidence supports a default change. Both modes wake on
                            // submit/control notifications rather than awaiting timeout.
                            const auto delay=current.idleWaitMode=="deadline"
                                ? idleWaitMs(device.statusPending(),requested_!=applied_,diagnose_,
                                             now-verified,now-lastIo,now-lastStatus,options_.keepaliveMs)
                                : 10u;
                            // 2026-10-26: wait outside the queue mutex. A retained
                            // native event avoids condition_variable's hidden
                            // re-acquire; the next loop measures explicit lock
                            // acquisition separately. Queue drain remains first.
                            lock.unlock();
                            if(traceEnabled_){waitEnteredUs=micros();waitTimeoutMs=delay;}
                            const bool signaled=changed_.wait(delay);
                            if(traceEnabled_)waitSignaled=signaled?1u:0u;
                            if(traceEnabled_)waitReturnedUs=micros();
                            continue;
                        }
                    }
                    if(!active()){if(event)++dropped;break;}
                    if(event) {
                        auto start=micros();auto now=GetTickCount64();
                        if(now<event->edge.tick||now-event->edge.tick>50){std::lock_guard lock(mutex_);++stale_;++dropped;continue;}
                        phase="play_write";
                        const auto triggerUs=traceEnabled_?micros():0;
                        try{device.trigger(event->edge,now);}catch(...){std::lock_guard lock(mutex_);++failedPlay_;++dropped;throw;}
                        auto end=micros();++sent;
                        {std::lock_guard lock(metricsMutex_);queueTimes_.add(start-event->receivedUs);hidTimes_.add(end-start);totalTimes_.add(end-event->receivedUs);if(traceEnabled_)trace_.add(event->receivedUs,end,dequeuedUs,triggerUs,event->wait);}
                    }else {
                        auto start=micros();
                        if(work==Work::Configure){phase="configure";device.configure(wave,intensity);cache(device.snapshot());std::lock_guard lock(mutex_);applied_=configRevision;}
                        else if(work==Work::Diagnose){
                            phase="diagnose";lastStatus=GetTickCount64();
                            ++statusQueries_;
                            if(device.mode()=="interrupt"){device.beginStatus();asyncStatusPending_=true;}else cache(device.snapshot());
                            std::lock_guard lock(mutex_);diagnose_=false;
                        }
                        else if(work==Work::Verify){
                            phase="verify";lastStatus=GetTickCount64();
                            ++statusQueries_;
                            if(device.mode()=="interrupt"){device.heartbeat(false);device.beginStatus();asyncStatusPending_=true;pendingVerify=true;}
                            else {cache(device.heartbeat(true));verified=GetTickCount64();std::lock_guard lock(mutex_);++heartbeatCount_;}
                        }
                        else if(work==Work::Keepalive){phase="keepalive";device.heartbeat(false);std::lock_guard lock(mutex_);++keepaliveCount_;}
                        std::lock_guard lock(metricsMutex_);backgroundTimes_.add(micros()-start);
                    }
                    lastIo=GetTickCount64();
                }
            }catch(const std::exception& e) {
                std::lock_guard lock(mutex_);lastError_=e.what();phase_=phase;++failures_;
                if(options_.statusMode=="auto"&&(lastError_.find("HidD_GetFeature")!=std::string::npos||lastError_.find("Feature unavailable")!=std::string::npos))preferInterrupt_=true;
                bool absent=lastError_.find("NO_DEVICE")!=std::string::npos;
                fault_=enterFault(failures_,absent);
                state_=absent?"NO_DEVICE":(fault_?"FAULT":"RECOVERING");
                log("HID "+state_+" phase="+phase+" error="+lastError_);
            }
            {
                std::unique_lock lock(mutex_);ready_=false;cached_.valid=false;asyncStatusPending_=false;
                dropped+=queue_.size();queue_.clear();
                if(stopping)break;
                if(recover_||!enabled_||paused)continue;
                if(fault_)continue;
                if(failures_) {
                    // Keep the full retry deadline: a diagnose/set notification
                    // must not accelerate repeated failed hardware reconnects.
                    const auto deadline=GetTickCount64()+retryDelay(failures_);
                    while(!stopping&&!recover_&&enabled_&&!paused) {
                        const auto now=GetTickCount64();if(now>=deadline)break;
                        lock.unlock();changed_.wait(static_cast<DWORD>(deadline-now));lock.lock();
                    }
                }
            }
        }
        state("STOPPED");
    }
};

static void controlServer(HANDLE pipe,Worker& worker) {
    while(!stopping) {
        if(!connect(pipe)){if(stopping)break;DisconnectNamedPipe(pipe);continue;}
        ULONG session=0;
        if(!GetNamedPipeClientSessionId(pipe,&session)||session!=WTSGetActiveConsoleSessionId()){DisconnectNamedPipe(pipe);continue;}
        std::array<char,1024> buffer{};DWORD count=0;
        if(io(pipe,buffer.data(),static_cast<DWORD>(buffer.size()),false,1000,count)&&count>0&&count<buffer.size()) {
            std::string request(buffer.data(),count);
            auto result=worker.control(request);DWORD written=0;
            io(pipe,result.data(),static_cast<DWORD>(result.size()),true,1000,written);
            // DisconnectNamedPipe discards unread output. Wait for the client's
            // receipt ACK with a bounded read instead of an unbounded flush.
            char receipt=0;DWORD received=0;
            io(pipe,&receipt,1,false,1000,received);
        }
        DisconnectNamedPipe(pipe);
    }
}
int runControl(const Options& options,const std::string& command) {
    auto name=path(options.sedPipe+L"Control");
    Handle pipe;
    const auto deadline=GetTickCount64()+2000;
    while(!pipe.valid()) {
        const auto now=GetTickCount64();
        if(now>=deadline){SetLastError(ERROR_SEM_TIMEOUT);fail("Wait control pipe");}
        if(!WaitNamedPipeW(name.c_str(),static_cast<DWORD>(deadline-now)))fail("Wait control pipe");
        pipe.reset(CreateFileW(name.c_str(),GENERIC_READ|GENERIC_WRITE,0,nullptr,OPEN_EXISTING,FILE_FLAG_OVERLAPPED,nullptr));
        // Concurrent status/diagnose clients can both observe availability.
        // Retry only that race, within the original 2s control deadline. This
        // does not add retries or sleeps to the separate haptic data pipe.
        if(!pipe.valid()&&GetLastError()!=ERROR_PIPE_BUSY)fail("Open control pipe");
    }
    DWORD readMode=PIPE_READMODE_MESSAGE;
    if(!SetNamedPipeHandleState(pipe.get(),&readMode,nullptr,nullptr))fail("Set control message mode");
    DWORD done=0;auto request=command;
    if(!io(pipe.get(),request.data(),static_cast<DWORD>(request.size()),true,2000,done))fail("Write control request");
    // Bounded 128-event trace now includes wait stages and thread IDs. The old
    // 16 KiB client buffer silently truncated an otherwise valid response.
    std::array<char,65536> response{};
    if(!io(pipe.get(),response.data(),static_cast<DWORD>(response.size()),false,2000,done))fail("Read control response");
    std::string result(response.data(),done);std::cout<<result<<std::endl;
    char receipt=6;DWORD written=0;
    io(pipe.get(),&receipt,1,true,1000,written);
    return result.find("\"ok\":true")!=std::string::npos ? 0 : 2;
}

int runBackend(const Options& options) {
    Security security;
    Handle pipe(server(options.sedPipe,false,security));
    Handle controls(server(options.sedPipe+L"Control",true,security,true));
    // 2026-10-26: keep one completed-I/O event across reads/connections instead
    // of creating and closing a kernel handle for every category byte.
    ReusableOverlappedIo pipeRead;
    Worker worker(options); worker.start();
    std::thread controlThread([&]{try{controlServer(controls.get(),worker);}catch(const std::exception& e){log(std::string("Control server failed: ")+e.what());stopping=true;}});
    worker.inputPriority(latencyPriority(options,"pipe-input"));
    log(options.mock ? "Transport=MOCK; no physical HID access" : "Transport=Windows HID");
    log("Backend running; waiting for HybridSED directly (keydown-only contract)");
    std::uint64_t lastInvalidLog=0,reportedInvalid=0;
    try {
        while(!stopping) {
            if(!connect(pipe.get())) {if(stopping)break;DisconnectNamedPipe(pipe.get());continue;}
            ULONG session=0;
            if(!GetNamedPipeClientSessionId(pipe.get(),&session)||session!=WTSGetActiveConsoleSessionId()) {
                log("Rejected publisher outside active console session");
                DisconnectNamedPipe(pipe.get()); continue;
            }
            log("HybridSED connected");
            while(!stopping) {
                std::array<std::uint8_t,128> bytes{}; DWORD size=0;
                if(!pipeRead.perform(pipe.get(),bytes.data(),static_cast<DWORD>(bytes.size()),false,24*60*60*1000,size,stopping)) break;
                if(!size) break;
                if(session!=WTSGetActiveConsoleSessionId()) {log("Active desktop changed; closing old publisher connection");break;}
                for(DWORD i=0;i<size;++i) {
                    if(bytes[i]<1||bytes[i]>3) {++worker.invalidCategories;continue;}
                    // The byte carries no VK or source time. Timestamp starts at receipt,
                    // key_id stays 0; never misrepresent category as a virtual key.
                    worker.submit(Edge{bytes[i],0,true,GetTickCount64()});
                }
                const auto invalid=worker.invalidCategories.load();
                if(invalid!=reportedInvalid && GetTickCount64()-lastInvalidLog>=1000) {
                    log("Rejected unknown haptic categories; total="+std::to_string(invalid));
                    reportedInvalid=invalid;lastInvalidLog=GetTickCount64();
                }
            }
            DisconnectNamedPipe(pipe.get()); log("HybridSED disconnected");
        }
    } catch(...) {stopping=true;if(controlThread.joinable())controlThread.join();worker.stop();throw;}
    if(controlThread.joinable())controlThread.join();
    worker.stop();
    log("PLAY sent="+std::to_string(worker.sent.load())+" dropped="+std::to_string(worker.dropped.load()));
    return 0;
}
}
