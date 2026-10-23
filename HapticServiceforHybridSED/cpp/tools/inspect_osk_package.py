"""Read-only inspection of the supplied OSK config and blob coordinate tables.

Does not detect touch sensor compatibility, alter files, or calibrate a panel.
"""
import argparse
import json
from pathlib import Path
import struct


def inspect(directory):
    config = json.loads((directory / 'AppConfig.json').read_text(encoding='utf-8-sig'))
    blob = (directory / 'Resources/osk.bin').read_bytes()
    offset = 0
    blocks = []
    coordinates = {}
    while offset < len(blob):
        if offset + 96 > len(blob):
            raise ValueError('Truncated blob header')
        sync, identifier, subid, label, size, count, stride, reserved = struct.unpack_from('<16sHH64sIHHI', blob, offset)
        if sync.split(b'\0')[0] != b'BLOCK HEADER' or size < 96 or offset + size > len(blob):
            raise ValueError('Invalid blob block at ' + str(offset))
        blocks.append({'id': identifier, 'label': label.split(b'\0')[0].decode('ascii'),
                       'elements': count, 'stride': stride})
        if identifier == 2:
            payload = blob[offset + 96:offset + size]
            if len(payload) % 32:
                raise ValueError('Invalid touchscreen coordinate table')
            for entry in range(0, len(payload), 32):
                name, value = struct.unpack_from('<28sI', payload, entry)
                coordinates[name.split(b'\0')[0].decode('ascii')] = value
        offset += size
        if identifier == 65535:
            break
    reset_fields = ('CutFvkC CutFvkNc FrIntervalShort FrIntervalLong FrWindowMax TcMin '
                    'TcMinCritical TcMax TdMax TdMaxCritical ThHp ThTt ThTypingF ThTypingM '
                    'TInitJudge TsMin TsMinCritical TsMax TvMax TvMaxCritical').split()
    parameters = config['DllParams']
    startup = {name: {'file_value': parameters[name]['Value'],
                      'startup_default': parameters[name]['Default']}
               for name in reset_fields}
    return {'scope': 'Static published-package inspection, no live panel verification',
            'app_params': config['AppParams'], 'blob_coordinate_table': coordinates,
            'startup_reset_fields_from_decompiled_AppConfigs_SetDefault': startup,
            'blob_blocks': blocks}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('osk_directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(inspect(args.osk_directory), indent=2, ensure_ascii=False))
