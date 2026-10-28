"""Real production C# publisher -> Named Pipe -> C++ Service, mock HID.

Synthetic classified events bypass OSK, touch and keyboard hook; safe to run on
a locked desktop. Never substitute these measurements for DLL/hook latency.
"""
import json
import argparse
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid
import check_service_features as features
from sed_trace import SedTrace
from analyze_latency import summarize


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-label',default='debug-20261023')
    parser.add_argument('--probe-dll',type=Path,help='Saved probe/publisher binaries for controlled A/B')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--idle-only',action='store_true')
    mode.add_argument('--smoke-only',action='store_true')
    args=parser.parse_args()
    if not args.run_label or any(ch not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for ch in args.run_label):
        parser.error('run-label must contain letters, digits, dash or underscore')
    root=Path(__file__).resolve().parents[1]
    directory=root/('logs/publisher-latency-'+args.run_label)
    directory.mkdir(parents=True,exist_ok=True)
    features.REPORT=directory
    backend=features.Backend('native','--mock-quiet','--mock-trace','--status-mode','interrupt','--mock-status-delay-ms','200')
    probe=None;trace=None;reader=None;query_thread=None
    stop=threading.Event();lines=queue.Queue();rows=[];trials={};errors=[]
    stderr=(directory/'probe-stderr.txt').open('w',encoding='utf8')
    try:
        backend.wait(lambda s:s['ready'])
        trace_name='Local\\PublisherProbe-'+uuid.uuid4().hex
        dll=args.probe_dll.resolve() if args.probe_dll else Path(__file__).with_name('PublisherLatencyProbe')/'bin/Debug/net8.0/PublisherLatencyProbe.dll'
        probe=subprocess.Popen(['dotnet','--roll-forward','Major',str(dll),backend.pipe,trace_name],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr,text=True,
            creationflags=subprocess.CREATE_NO_WINDOW)
        def read():
            for line in probe.stdout:lines.put(line)
            lines.put(None)
        reader=threading.Thread(target=read);reader.start()
        def response(timeout):
            line=lines.get(timeout=timeout)
            if line is None:raise RuntimeError('Publisher probe exited; inspect probe-stderr.txt')
            return json.loads(line)
        ready=response(20);assert ready['ready']
        frequency=ready['frequency'];trace=SedTrace(trace_name,native_wake=True,require_hook=False)
        assert trace.frequency==frequency
        def publish(count,pattern):
            probe.stdin.write(json.dumps(dict(count=count,pattern_ms=pattern))+'\n');probe.stdin.flush()
            timeout=10+sum(pattern[i%len(pattern)] for i in range(count))/1000
            return [t*1_000_000//frequency for t in response(timeout)['entries']]
        publish(1,[10]);backend.wait(lambda s:s['play_sent']==1)
        def query():
            try:
                while not stop.is_set():
                    backend.control('diagnose');backend.control('status');stop.wait(0.03)
            except Exception as error:errors.append(repr(error))
        def trial(name,pattern,queries=False,count=100,pre_idle=0):
            nonlocal query_thread
            backend.control('metrics reset');before=backend.control('status');first=trace.count+1
            stop.clear();query_thread=threading.Thread(target=query) if queries else None
            if query_thread:query_thread.start()
            try:
                if pre_idle:time.sleep(pre_idle)
                entries=publish(count,pattern)
            finally:
                stop.set()
                if query_thread:query_thread.join(timeout=6)
            if query_thread and query_thread.is_alive():raise TimeoutError('query thread did not stop')
            assert not errors,errors
            state=backend.wait(lambda s:s['play_sent']>=before['play_sent']+count)
            assert state['play_sent']==before['play_sent']+count and state['dropped']==0 and state['generation']==1
            service=backend.control('trace');sed=trace.records(first,count)
            assert len(service['stage_samples'])==len(service['wait_samples'])==len(entries)==count and trace.count==first+count-1
            for index,(entry,r,s,w) in enumerate(zip(entries,sed,service['stage_samples'],service['wait_samples'])):
                assert s[0]==w[0] and r['code']==1+index%3
                assert entry<=r['publish']<=r['native_enter']<=s[1]<=w[1]<=w[2]<=s[2]<=s[3]<=s[4]
                assert w[3]<=w[4]<=s[2]
                if state.get('worker_wake_mode') == 'native-event' and w[5]:
                    assert w[5] <= w[3], 'Native wake timestamp must precede queue lock request'
                r.update(entry=entry,submit=s[1],complete=s[4],service_dequeue=s[2],
                    submit_locked=w[1],worker_lock_request=w[3],worker_locked=w[4],
                    wait_returned=w[5],enqueue_stamp=w[2],worker_tid=w[7],trial=name)
                rows.append(r)
            trials[name]=dict(pattern_ms=pattern,queries=queries,count=count,pre_idle_seconds=pre_idle,stages_us=stages(rows[-count:]))
        if args.smoke_only:
            trial('smoke',[5])
        elif args.idle_only:
            trial('first_keys_1000ms',[1000],count=20,pre_idle=1)
            trial('first_keys_5000ms',[5000],count=6,pre_idle=5)
            assert backend.control('status')['verify_count']>0
        else:
            trial('20hz',[50]);trial('200hz',[5]);trial('200hz_queries',[5],True)
            trial('alternating_idle',[5,5,5,200]);trial('alternating_idle_queries',[5,5,5,200],True)
            for i in range(6):trial('soak_'+str(i),[10],bool(i%2))
        result=dict(scope=__doc__,probe_binary=str(dll),sample_count=len(rows),trials=trials,stages_us=stages(rows),
            events=rows,final_status=backend.control('status'))
        (directory/'results.json').write_text(json.dumps(result,indent=2),encoding='utf8')
        print(json.dumps(dict(samples=len(rows),stages_us=result['stages_us'])),flush=True)
        print('PASS: real C# publisher/pipe/service; OSK and hook bypassed; HID mocked.')
    except Exception as error:
        failure=dict(scope=__doc__,failure=repr(error),completed_samples=len(rows),trials=trials,events=rows)
        try:failure['service_status']=backend.control('status')
        except Exception:pass
        (directory/'failure.json').write_text(json.dumps(failure,indent=2),encoding='utf8')
        raise
    finally:
        stop.set()
        if query_thread:query_thread.join(timeout=6)
        if probe:
            if probe.poll() is None:probe.terminate()
            probe.wait(timeout=5)
        if reader:reader.join(timeout=5)
        if trace:trace.close()
        stderr.close();backend.close()


def stages(rows):
    boundaries={'publisher_queue':('publish','dequeue'),'named_pipe':('native_enter','submit'),
        'service_queue':('submit','service_dequeue'),'service':('submit','complete'),
        'pipe_service':('native_enter','complete'),'publisher_to_mock':('entry','complete'),
        'submit_lock':('submit','submit_locked'),'worker_lock':('worker_lock_request','worker_locked')}
    return {k:summarize([r[b]-r[a] for r in rows]) for k,(a,b) in boundaries.items()}


if __name__=='__main__':main()
