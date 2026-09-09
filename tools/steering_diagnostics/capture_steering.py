"""Save a bounded receive-only C2 telemetry stream on this Mac, exclusively."""
import datetime
import gzip
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time

ssh_target = os.environ.get('C2_SSH_TARGET')
if not ssh_target:
    raise SystemExit('Set C2_SSH_TARGET to your authorized SSH destination before capture.')

root = Path(__file__).resolve().parents[2]
capture_dir = root / 'outputs' / ('steering-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
capture_dir.mkdir(mode=0o700, exist_ok=False)
source = Path(__file__).with_name('steering_receive.py').read_bytes()
seconds = min(900, max(1, int(sys.argv[1]) if len(sys.argv) > 1 else 900))
cmd = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
       '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2',
       ssh_target,
       'cd /data/openpilot && PYTHONDONTWRITEBYTECODE=1 python3 - %d' % seconds]
process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, start_new_session=True)
process.stdin.write(source)
process.stdin.close()
selector = selectors.DefaultSelector()
selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
status = {'directory': str(capture_dir), 'local_pid': os.getpid(), 'ssh_pid': process.pid,
          'seconds_limit': seconds, 'state': 'connecting', 'received_bytes': 0,
          'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}


def save_status():
    # This run created this directory; no pre-existing user file is overwritten.
    (capture_dir / 'status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2))


save_status()
print('CAPTURE_DIRECTORY ' + str(capture_dir), flush=True)
pending = b''
start = time.monotonic()
reason = 'remote_exit'
try:
    with (capture_dir / 'telemetry.jsonl.gz').open('xb') as raw_file, \
         gzip.GzipFile(fileobj=raw_file, mode='wb', compresslevel=1) as telemetry, \
         (capture_dir / 'ssh-stderr.log').open('xb') as errors:
        while selector.get_map():
            if time.monotonic() - start > seconds + 35:
                reason = 'local_duration_limit'
                break
            for key, _ in selector.select(timeout=1):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == 'stderr':
                    errors.write(chunk)
                    errors.flush()
                    print(chunk.decode('utf8', errors='replace').strip(), flush=True)
                    continue
                telemetry.write(chunk)
                status['received_bytes'] += len(chunk)
                pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    try:
                        item = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        status['non_json_lines'] = status.get('non_json_lines', 0) + 1
                        continue
                    kind = item.get('kind')
                    if kind == 'metadata':
                        (capture_dir / 'metadata.json').write_text(json.dumps(item, indent=2, ensure_ascii=False))
                    elif kind in ('ready', 'status', 'end'):
                        status['state'] = 'recording' if kind != 'end' else 'complete'
                        status['last_event'] = item
                        save_status()
                        telemetry.flush()
                        if kind == 'status':
                            d = item['data']
                            print('RECORDING ' + json.dumps({'elapsed_s':d['elapsed_s'], 'last':d['last'], 'max_delivery_age_ms':d['max_delivery_age_ms']}, ensure_ascii=False), flush=True)
                        else:
                            print(kind.upper() + ' ' + json.dumps(item['data']), flush=True)
            if status['received_bytes'] >= 256 * 1024 * 1024:
                reason = 'size_limit'
                break
except KeyboardInterrupt:
    reason = 'user_stop'
finally:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    status['exit_code'] = process.returncode
    status['stop_reason'] = reason
    if status['state'] != 'complete':
        status['state'] = 'stopped' if reason != 'remote_exit' else 'incomplete'
    status['ended_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save_status()
    print('CAPTURE_STOPPED ' + json.dumps(status), flush=True)
