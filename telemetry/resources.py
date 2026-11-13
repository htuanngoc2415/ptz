"""Read-only, best-effort Linux resource samples. Missing sensors stay null."""
import os
from pathlib import Path
import time


def sample(previous=None):
    now, cpu = time.perf_counter(), time.process_time()
    result = dict(process_cpu_percent=None, system_cpu_percent=None,
                  rss_mb=None, gpu_percent=None, gpu_source=None,
                  cpu_scope='process may exceed 100% (one core = 100%)')
    ticks = None
    try:
        parts = [int(v) for v in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
        ticks = (sum(parts), parts[3]+parts[4])
        result['rss_mb'] = int(Path('/proc/self/statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')/1048576
    except (OSError, ValueError, IndexError):
        pass
    if previous and now > previous[0]:
        result['process_cpu_percent'] = 100*(cpu-previous[1])/(now-previous[0])
        if ticks and previous[2] and ticks[0] > previous[2][0]:
            result['system_cpu_percent'] = 100*(1-(ticks[1]-previous[2][1])/(ticks[0]-previous[2][0]))
    # NVIDIA Jetson devfreq reports utilization in per-mille. Do not assume
    # a generic devfreq "load" has these units.
    candidates = ([Path('/sys/devices/gpu.0/load'),
                   Path('/sys/devices/platform/17000000.gpu/load')]
                  if previous is None else previous[3])
    for path in candidates:
        try:
            value = float(path.read_text())/10
            if 0 <= value <= 100:
                result.update(gpu_percent=value, gpu_source=str(path))
                break
        except (OSError, ValueError):
            pass
    return result, (now, cpu, ticks, candidates)
