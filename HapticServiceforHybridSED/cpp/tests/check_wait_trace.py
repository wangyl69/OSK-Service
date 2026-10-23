"""Validate full bounded trace replies and the real-HID-capable diagnostic flag using mock."""
import time
import check_service_features as features

def main():
    features.REPORT = features.ROOT / 'logs/wait-trace-contract'
    features.REPORT.mkdir(parents=True, exist_ok=True)
    backend = features.Backend('enabled', '--mock-quiet', '--latency-trace')
    try:
        backend.wait(lambda s: s['ready'])
        for _ in range(128):
            backend.send();time.sleep(0.005)
        status=backend.wait(lambda s: s['play_sent']==128)
        assert not status['dropped']
        trace=backend.control('trace')
        assert trace['completion_kind']=='mock'
        assert len(trace['samples'])==len(trace['stage_samples'])==len(trace['wait_samples'])==128
        for legacy,s,w in zip(trace['samples'],trace['stage_samples'],trace['wait_samples']):
            assert legacy==[s[0],s[1],s[4]] and s[0]==w[0]
            assert s[1]<=w[1]<=w[2]<=s[2]<=s[3]<=s[4]
            assert w[3]<=w[4]<=s[2] and w[6]>0 and w[7]>0
        backend.control('metrics reset')
        assert backend.control('trace')['wait_samples']==[]
    finally: backend.close()
    backend=features.Backend('disabled','--mock-quiet')
    try:
        backend.wait(lambda s:s['ready'])
        assert not backend.control('trace',allow_error=True)['ok']
    finally: backend.close()
    print('PASS: 128 complete trace replies, wait ordering/thread IDs, reset and default-off contract; mock only.')

if __name__=='__main__':main()
