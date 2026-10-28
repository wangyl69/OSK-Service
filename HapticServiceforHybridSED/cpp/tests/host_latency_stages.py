"""Non-overlapping stages and useful inclusive spans, QPC microseconds."""

BOUNDARIES = {
    'dll_hook': ('inject', 'hook'),
    'hook_publish': ('hook', 'publish'),
    'sed_queue': ('publish', 'dequeue'),
    'sed_write_prepare': ('dequeue', 'native_enter'),
    'pipe': ('native_enter', 'service_submit'),
    'service_queue': ('service_submit', 'service_dequeue'),
    'service_send_mock': ('service_dequeue', 'mock_complete'),
    'service': ('service_submit', 'mock_complete'),
    'pipe_service': ('native_enter', 'mock_complete'),
    'total': ('inject', 'mock_complete'),
}


def durations(row):
    values = {stage: row[end] - row[begin] for stage, (begin, end) in BOUNDARIES.items()}
    assert all(value >= 0 for value in values.values()), values
    return values
