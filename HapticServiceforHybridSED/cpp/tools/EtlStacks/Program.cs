using System.Text.Json;
using Microsoft.Diagnostics.Tracing;
using Microsoft.Diagnostics.Tracing.Etlx;
using Microsoft.Diagnostics.Tracing.Parsers.Kernel;
using Microsoft.Diagnostics.Symbols;

if(args.Length<1)throw new ArgumentException("Expected capture directory [--symbols]");
var dir=Path.GetFullPath(args[0]);
using var data=JsonDocument.Parse(File.ReadAllText(Path.Combine(dir,"results.json")));
using var metadata=JsonDocument.Parse(File.ReadAllText(Path.Combine(dir,"capture.json")));
var freq=metadata.RootElement.GetProperty("qpc_frequency").GetDouble();
var rows=data.RootElement.GetProperty("sed_stage_records").EnumerateArray()
    .Where(r=>r.GetProperty("mock_complete").GetInt64()-r.GetProperty("inject").GetInt64()>5000).ToArray();
var tids=new HashSet<int>{data.RootElement.GetProperty("injector_thread").GetProperty("tid").GetInt32()};
foreach(var t in data.RootElement.GetProperty("verified_sed_priorities").EnumerateObject())tids.Add(t.Value.GetProperty("win32_id").GetInt32());
foreach(var r in rows){tids.Add(r.GetProperty("service_worker_tid").GetInt32());tids.Add(r.GetProperty("service_input_tid").GetInt32());}
Console.WriteLine("Opening/indexing ETL for stack association");
using var trace=TraceLog.OpenOrConvert(Path.Combine(dir,"scheduler.etl"));
var selected=new List<TraceEvent>();
// CSwitch's ordinary CallStack belongs to the switched-IN thread. The
// switched-OUT thread's waiting dependency must use BlockingStack instead.
TraceCallStack? RelevantStack(TraceEvent e){
    if(e is not CSwitchTraceData sw)return e.CallStack();
    var index=sw.BlockingStack();
    return index==CallStackIndex.Invalid?null:trace.CallStacks[index];
}
foreach(var e in trace.Events){
    var us=e.TimeStampQPC*1000000.0/freq;
    if(!rows.Any(r=>r.GetProperty("inject").GetInt64()-1000<=us&&us<=r.GetProperty("mock_complete").GetInt64()+1000))continue;
    if(e is CSwitchTraceData sw&&(tids.Contains(sw.OldThreadID)||tids.Contains(sw.NewThreadID)||sw.OldProcessID==4||sw.NewProcessID==4))selected.Add(e.Clone());
    else if(e is DispatcherReadyThreadTraceData rd&&tids.Contains(rd.AwakenedThreadID))selected.Add(e.Clone());
}
var modules=new Dictionary<string,TraceModuleFile>();
foreach(var e in selected)for(var stack=RelevantStack(e);stack!=null;stack=stack.Caller){var module=stack.CodeAddress.ModuleFile;if(module!=null)modules.TryAdd(module.FilePath,module);}
if(args.Contains("--symbols")){
    var cache=Path.Combine(dir,"symbols");Directory.CreateDirectory(cache);
    using var reader=new SymbolReader(Console.Out,$"srv*{cache}*https://msdl.microsoft.com/download/symbols");
    foreach(var module in modules.Values.Where(m=>new[]{"ntoskrnl","win32kbase","win32kfull","win32k","win32u","user32","ntdll","kernelbase","fvevol","ntfs","fltmgr"}.Contains(Path.GetFileNameWithoutExtension(m.Name).ToLowerInvariant()))){
        Console.WriteLine("Resolving "+module.Name);trace.CodeAddresses.LookupSymbolsForModule(reader,module);
    }
}
var output=new List<object>();
foreach(var e in selected){
    var frames=new List<object>();
    for(var stack=RelevantStack(e);stack!=null&&frames.Count<80;stack=stack.Caller){var code=stack.CodeAddress;frames.Add(new{module=code.ModuleName,name=code.FullMethodName,address=$"0x{code.Address:x}"});}
    object? scheduling=e is CSwitchTraceData s?new{old_tid=s.OldThreadID,new_tid=s.NewThreadID,old_pid=s.OldProcessID,new_pid=s.NewProcessID,old_priority=s.OldThreadPriority,new_priority=s.NewThreadPriority,old_state=s.OldThreadState.ToString(),reason=s.OldThreadWaitReason.ToString()}:null;
    output.Add(new{qpc_us=e.TimeStampQPC*1000000.0/freq,event_name=e.EventName,cpu=e.ProcessorNumber,tid=e.ThreadID,stack_role=e is CSwitchTraceData?"switched_out_blocking":"event_thread",scheduling,frames});
}
File.WriteAllText(Path.Combine(dir,"slow-stacks.json"),JsonSerializer.Serialize(output,new JsonSerializerOptions{WriteIndented=true}));
Console.WriteLine($"Selected={selected.Count}, stacks={selected.Count(e=>RelevantStack(e)!=null)}, modules={modules.Count}");
