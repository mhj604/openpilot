"""Save a bounded receive-only C2 capture in a new Mac-only directory.

No existing diagnostic, vehicle, or parameter files are overwritten. Run only
when ready to collect; launching this file starts the 15-minute collection.
"""
import base64
import datetime
import gzip
import hashlib
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
stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
capture_dir = root / 'outputs' / ('steering-geometry-' + stamp)
capture_dir.mkdir(mode=0o700, exist_ok=False)
image_dir = capture_dir / 'road-images'
image_dir.mkdir(mode=0o700)
source = Path(__file__).with_name('steering_receive_geometry.py').read_bytes()
with (capture_dir / 'collector-source.py').open('xb') as snapshot:
    snapshot.write(source)
seconds = min(900, max(1, int(sys.argv[1]) if len(sys.argv) > 1 else 900))
cmd = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
       '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2',
       ssh_target,
       'cd /data/openpilot && PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 - %d' % seconds]
process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, start_new_session=True)
status = {'directory': str(capture_dir), 'local_pid': os.getpid(), 'ssh_pid': process.pid,
          'seconds_limit': seconds, 'state': 'connecting', 'received_bytes': 0,
          'images_saved': 0, 'capture_inputs_present': False,
          'collector_sha256': hashlib.sha256(source).hexdigest(),
          'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}


def save_status():
    temporary = capture_dir / 'status.tmp'
    temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2))
    temporary.replace(capture_dir / 'status.json')


save_status()
print('CAPTURE_DIRECTORY ' + str(capture_dir), flush=True)
selector = selectors.DefaultSelector()
selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
pending = b''
start = time.monotonic()
reason = 'remote_exit'
try:
    process.stdin.write(source)
    process.stdin.close()
    with (capture_dir / 'telemetry.jsonl.gz').open('xb') as raw_file, \
         gzip.GzipFile(fileobj=raw_file, mode='wb', compresslevel=1) as telemetry, \
         (capture_dir / 'ssh-stderr.log').open('xb') as errors, \
         (capture_dir / 'image-index.jsonl').open('x') as index:
        while selector.get_map():
            if time.monotonic() - start > seconds + 45:
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
                status['received_bytes'] += len(chunk)
                pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    try:
                        item = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        status['non_json_lines'] = status.get('non_json_lines', 0) + 1
                        errors.write(line + b'\n')
                        continue
                    kind = item.get('kind')
                    data = item.get('data', {})
                    if kind in ('road_image', 'thumbnail') and 'jpeg_base64' in data:
                        jpeg = base64.b64decode(data.pop('jpeg_base64'), validate=True)
                        serial = status['images_saved']
                        filename = '%s-%06d.jpg' % (kind, serial)
                        with (image_dir / filename).open('xb') as picture:
                            picture.write(jpeg)
                        data['image_file'] = 'road-images/' + filename
                        data['jpeg_bytes'] = len(jpeg)
                        status['images_saved'] += 1
                        index.write(json.dumps(item, separators=(',', ':')) + '\n')
                    telemetry.write((json.dumps(item, separators=(',', ':')) + '\n').encode())
                    if kind == 'metadata':
                        with (capture_dir / 'metadata.json').open('x') as metadata:
                            json.dump(item, metadata, ensure_ascii=False, indent=2)
                    elif kind in ('collector_started', 'status', 'end'):
                        status['state'] = 'recording' if kind != 'end' else 'complete'
                        status['last_event'] = item
                        if kind == 'status':
                            status['capture_inputs_present'] = bool(data.get('capture_inputs_present'))
                            print('RECORDING ' + json.dumps({
                                'elapsed_s': data.get('elapsed_s'),
                                'capture_inputs_present': status['capture_inputs_present'],
                                'missing_or_stale': data.get('missing_or_stale'),
                                'images': data.get('images'), 'last': data.get('last'),
                                'valid': data.get('valid'),
                            }, ensure_ascii=False), flush=True)
                        else:
                            print(kind.upper() + ' ' + json.dumps(data), flush=True)
                        save_status()
                        telemetry.flush()
                        raw_file.flush()
                        index.flush()
            if len(pending) > 16 * 1024 * 1024:
                reason = 'oversized_incomplete_line'
                break
            if status['received_bytes'] >= 1024 * 1024 * 1024:
                reason = 'size_limit'
                break
        if pending:
            with (capture_dir / 'trailing-incomplete-message.bin').open('xb') as remainder:
                remainder.write(pending)
except KeyboardInterrupt:
    reason = 'user_stop'
except Exception as exc:
    reason = 'capture_error'
    status['error'] = '%s: %s' % (type(exc).__name__, exc)
finally:
    selector.close()
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
    status['capture_inputs_present'] = False
    status['ended_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save_status()
    print('CAPTURE_STOPPED ' + json.dumps(status, ensure_ascii=False), flush=True)
