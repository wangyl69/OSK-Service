"""Execute the actual MCU idle-health functions with native peripheral substitutes.

No hardware timing claim: this verifies scheduling, progress, and fault behavior.
The selected function bodies are extracted unchanged, not rewritten in Python.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def function(source, name):
    match = re.search(r'^(?:static )?(?:uint8_t|aw_motor_name) ' + name + r'\([^)]*\)\s*\{', source, re.M)
    if not match:
        raise ValueError('Production function missing: ' + name)
    # The current functions have balanced braces with no brace literals. Fail
    # compilation on future unsupported syntax rather than silently substituting.
    start = match.start()
    level = 1
    for i in range(match.end(), len(source)):
        level += (source[i] == '{') - (source[i] == '}')
        if not level:
            return source[start:i+1]
    raise ValueError('Unbalanced production function: ' + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', type=Path, help='GNU-style C++ compiler; defaults to the Host CMake compiler')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    firmware = root.parent.parent / 'Athena_Haptic'
    source_path = firmware / 'Core/Src/app_haptic_recovery.c'
    source_bytes = source_path.read_bytes()
    source = source_bytes.decode('utf-8-sig')
    config = (firmware / 'config/project_config.h').read_text(encoding='utf-8-sig')
    defines = []
    def literal(name, seen=()):
        if name in seen:
            raise ValueError('Recursive configuration macro: ' + name)
        match = re.search(r'^#define\s+' + name + r'\s+(\w+)\b', config, re.M)
        if not match:
            raise ValueError('Review changed production configuration: ' + name)
        token = match[1]
        return token if re.fullmatch(r'\d+U?', token) else literal(token, (*seen, name))
    for name in ('APP_KB_MODE_ENABLE', 'APP_KB_HEALTH_CHECK_PERIOD', 'APP_AW_DEVICE_COUNT'):
        defines.append('#define ' + name + ' ' + literal(name))
    if not args.compiler:
        cache = (root / 'build/CMakeCache.txt').read_text(encoding='utf8')
        args.compiler = Path(re.search(r'^CMAKE_CXX_COMPILER:[^=]+=(.+)$', cache, re.M)[1].strip())
    directory = root / 'logs/firmware-idle-health-debug'
    directory.mkdir(parents=True, exist_ok=True)
    template = Path(__file__).with_name('firmware_idle_health_harness.cpp.in').read_text(encoding='utf8')
    functions = '\n\n'.join(function(source, name) for name in (
        'APP_HapticRecovery_MotorFromIndex', 'APP_HapticRecovery_RunPlaybackHealthCheck',
        'APP_HapticRecovery_RunIdleHealthCheck'))
    generated = directory / 'health.cpp'
    generated.write_text(template.replace('// PRODUCTION_CONFIG', '\n'.join(defines)).replace('// PRODUCTION_FUNCTIONS', functions), encoding='utf8')
    exe = directory / 'health.exe'
    command = [str(args.compiler), '-std=c++20', '-Wall', '-Wextra', '-static', str(generated), '-o', str(exe)]
    build = subprocess.run(command, capture_output=True, text=True, timeout=60)
    (directory / 'build.txt').write_text(build.stdout + build.stderr, encoding='utf8')
    build.check_returncode()
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=10)
    (directory / 'run.txt').write_text(result.stdout + result.stderr, encoding='utf8')
    (directory / 'result.json').write_text(json.dumps(dict(
        source=str(source_path), source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        config_sha256=hashlib.sha256(config.encode('utf8')).hexdigest(),
        scope='Production function logic, mocked peripherals; not MCU timing',
        exit_code=result.returncode, output=result.stdout + result.stderr), indent=2), encoding='utf8')
    result.check_returncode()
    print(result.stdout.strip())


if __name__ == '__main__':
    main()
