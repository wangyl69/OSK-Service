using System.Text.Json;
using Microsoft.Diagnostics.Tracing;
Dictionary<string,string?> Payload(TraceEvent e){
    var values=new Dictionary<string,string?>();
    for(var i=0;i<e.PayloadNames.Length;i++){
        var name=e.PayloadNames[i];
        if(values.ContainsKey(name))name+="#"+i;
        values.Add(name,Convert.ToString(e.PayloadValue(i)));
    }
    return values;
}
if(args.Length==1&&args[0]=="--schema"){
    foreach(var t in new[]{typeof(Microsoft.Diagnostics.Tracing.Parsers.Kernel.CSwitchTraceData),typeof(Microsoft.Diagnostics.Tracing.Parsers.Kernel.DPCTraceData),typeof(Microsoft.Diagnostics.Tracing.Parsers.Kernel.ISRTraceData)})
        Console.WriteLine(t.Name+": "+string.Join(",",t.GetProperties().Select(p=>p.Name)));
    Console.WriteLine(string.Join(",",typeof(Microsoft.Diagnostics.Tracing.Parsers.KernelTraceEventParser).GetEvents().Select(e=>e.Name).Where(n=>n.Contains("DPC")||n.Contains("ISR")||n.Contains("Priority"))));
    return;
}
if(args.Length!=1)throw new ArgumentException("Expected capture directory");
var dir=Path.GetFullPath(args[0]);
using var results=JsonDocument.Parse(File.ReadAllText(Path.Combine(dir,"results.json")));
var tids=results.RootElement.GetProperty("sed_stage_records").EnumerateArray().SelectMany(r=>new[]{r.GetProperty("service_worker_tid").GetInt32(),r.GetProperty("service_input_tid").GetInt32()}).ToHashSet();
if(results.RootElement.TryGetProperty("injector_thread",out var injector))tids.Add(injector.GetProperty("tid").GetInt32());
if(results.RootElement.TryGetProperty("verified_sed_priorities",out var sedThreads))
    foreach(var thread in sedThreads.EnumerateObject())tids.Add(thread.Value.GetProperty("win32_id").GetInt32());
using var capture=JsonDocument.Parse(File.ReadAllText(Path.Combine(dir,"capture.json")));
var frequency=capture.RootElement.GetProperty("qpc_frequency").GetInt64();
var events=new List<object>();long switches=0,ready=0;
var rows=results.RootElement.GetProperty("sed_stage_records").EnumerateArray().ToArray();
var selected=rows.OrderByDescending(r=>r.GetProperty("mock_complete").GetInt64()-r.GetProperty("native_enter").GetInt64()).Take(20)
    .Concat(rows.Where(r=>r.GetProperty("mock_complete").GetInt64()-r.GetProperty("native_enter").GetInt64()>5000))
    .Concat(rows.OrderByDescending(r=>r.GetProperty("hook").GetInt64()-r.GetProperty("inject").GetInt64()).Take(20))
    .Concat(rows.Where(r=>r.GetProperty("hook").GetInt64()-r.GetProperty("inject").GetInt64()>2000))
    .Concat(rows.OrderByDescending(r=>r.GetProperty("mock_complete").GetInt64()-r.GetProperty("inject").GetInt64()).Take(20))
    .Concat(rows.Where(r=>r.GetProperty("mock_complete").GetInt64()-r.GetProperty("inject").GetInt64()>2000))
    .DistinctBy(r=>r.GetProperty("service_submit").GetInt64()).ToArray();
var windows=selected.Select(r=>(Start:Math.Min(r.GetProperty("inject").GetDouble(),r.GetProperty("service_wait_enter").GetDouble()>0?r.GetProperty("service_wait_enter").GetDouble():r.GetProperty("inject").GetDouble())-1000,End:r.GetProperty("mock_complete").GetDouble()+1000)).ToArray();
var nearby=new List<object>();
var interrupts=new List<object>();
var powerEvents=new List<object>();
var priorityEvents=new List<object>();
var decodedPriorityEvents=new List<object>();
var powerCounts=new Dictionary<string,int>();
var cpuState=new Dictionary<int,object>();
var windowStates=new object[]?[windows.Length];
var threadProcesses=new Dictionary<int,int>();
var processNames=new Dictionary<int,string>();
using var source=new ETWTraceEventSource(Path.Combine(dir,"scheduler.etl"));
source.Dynamic.All+=e=>{
    var threadGuid=new Guid("3d6fa8d1-fe05-11d0-9dda-00c04fd7ba7c");
    if((e.ProviderGuid==threadGuid||e.TaskGuid==threadGuid)&&new[]{48,49,51,52}.Contains((int)e.Opcode))
        decodedPriorityEvents.Add(new{name=e.EventName,opcode=(int)e.Opcode,qpc_us=e.TimeStampQPC*(1000000.0/frequency),payload=Payload(e)});
    if(!e.ProviderName.Contains("Power",StringComparison.OrdinalIgnoreCase))return;
    var key=e.ProviderName+"/"+e.EventName;
    powerCounts[key]=powerCounts.GetValueOrDefault(key)+1;
    var time=e.TimeStampQPC*(1000000.0/frequency);
    if(e.EventName is "ParkCore" or "UnparkCore"||windows.Any(w=>w.Start<=time&&time<=w.End))powerEvents.Add(new{provider=e.ProviderName,name=e.EventName,id=(int)e.ID,qpc_us=time,cpu=e.ProcessorNumber,
        payload=Payload(e)});
};
source.UnhandledEvents+=e=>{
    var threadGuid=new Guid("3d6fa8d1-fe05-11d0-9dda-00c04fd7ba7c");
    if(e.TaskGuid!=threadGuid&&e.ProviderGuid!=threadGuid)return;
    var opcode=(int)e.Opcode;
    if(opcode<48||opcode>80)return;
    priorityEvents.Add(new{opcode,version=(int)e.Version,qpc_us=e.TimeStampQPC*(1000000.0/frequency),cpu=e.ProcessorNumber,
        raw=Convert.ToHexString(e.EventData())});
};
source.Kernel.ProcessStartGroup+=e=>processNames[e.ProcessID]=e.ImageFileName;
source.Kernel.ThreadStartGroup+=e=>threadProcesses[e.ThreadID]=e.ProcessID;
source.Kernel.ThreadCSwitch+=e=>{
    switches++;
    var time=e.TimeStampQPC*(1000000.0/frequency);
    for(var i=0;i<windows.Length;i++)if(windowStates[i]==null&&time>=windows[i].Start)windowStates[i]=cpuState.Values.ToArray();
    int? oldPid=threadProcesses.TryGetValue(e.OldThreadID,out var op)?op:null;
    int? newPid=threadProcesses.TryGetValue(e.NewThreadID,out var np)?np:null;
    var item=new{kind="switch",qpc_us=time,cpu=e.ProcessorNumber,old_tid=e.OldThreadID,new_tid=e.NewThreadID,
        old_pid=oldPid,new_pid=newPid,
        old_process=oldPid.HasValue?processNames.GetValueOrDefault(oldPid.Value):null,
        new_process=newPid.HasValue?processNames.GetValueOrDefault(newPid.Value):null,
        old_priority=e.OldThreadPriority,new_priority=e.NewThreadPriority,
        old_state=e.OldThreadState.ToString(),reason=e.OldThreadWaitReason.ToString()};
    if(tids.Contains(e.NewThreadID)||tids.Contains(e.OldThreadID))events.Add(item);
    if(windows.Any(w=>w.Start<=time&&time<=w.End))nearby.Add(item);
    cpuState[e.ProcessorNumber]=item;
};
source.Kernel.DispatcherReadyThread+=e=>{ready++;if(tids.Contains(e.AwakenedThreadID))events.Add(new{kind="ready",qpc_us=e.TimeStampQPC*(1000000.0/frequency),cpu=e.ProcessorNumber,tid=e.AwakenedThreadID,waker_tid=e.ThreadID});};
void Interrupt(string kind,double end,double elapsed,int cpu,ulong routine){
    var start=end-elapsed*1000;
    if(windows.Any(w=>w.Start<=end&&start<=w.End))interrupts.Add(new{kind,start_us=start,end_us=end,cpu,routine=$"0x{routine:x}"});
}
source.Kernel.PerfInfoISR+=e=>Interrupt("ISR",e.TimeStampQPC*(1000000.0/frequency),e.ElapsedTimeMSec,e.ProcessorNumber,e.Routine);
source.Kernel.PerfInfoDPC+=e=>Interrupt("DPC",e.TimeStampQPC*(1000000.0/frequency),e.ElapsedTimeMSec,e.ProcessorNumber,e.Routine);
source.Kernel.PerfInfoTimerDPC+=e=>Interrupt("TimerDPC",e.TimeStampQPC*(1000000.0/frequency),e.ElapsedTimeMSec,e.ProcessorNumber,e.Routine);
source.Kernel.PerfInfoThreadedDPC+=e=>Interrupt("ThreadedDPC",e.TimeStampQPC*(1000000.0/frequency),e.ElapsedTimeMSec,e.ProcessorNumber,e.Routine);
source.Process();
File.WriteAllText(Path.Combine(dir,"kernel-thread-events.json"),JsonSerializer.Serialize(new{switches,ready,lost=source.EventsLost,thread_ids=tids,events,nearby_switches=nearby,window_states=windows.Select((w,i)=>new{start_us=w.Start,end_us=w.End,prior_switches=windowStates[i]}),interrupts,power_counts=powerCounts,power_events=powerEvents,priority_decoded_events=decodedPriorityEvents,unhandled_thread_raw_events=priorityEvents,thread_processes=threadProcesses,process_names=processNames},new JsonSerializerOptions{WriteIndented=true}));
Console.WriteLine($"Decoded CSwitch={switches}, ReadyThread={ready}, retained={events.Count}, lost={source.EventsLost}");
if(switches==0||ready==0)throw new InvalidOperationException("Required scheduler events not decoded");
