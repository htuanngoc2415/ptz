#!/usr/bin/env python3
"""Read-only RTSP capture probe. No AI/GUI startup, camera RPC or motor calls.

Reuses production MPVPlayer options/capture loop; optional subtype override is
local to this connection. PTS progress measures drift, NOT sensor latency.
"""
import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import sys
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from camera.mpv_player import MPVPlayer
from telemetry.benchmark import distribution


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def summarize(samples, costs, received):
    valid = [s for s in samples if finite(s.get('time_pos'))]
    result = dict(samples=len(samples), received_frames=received,
                  screenshot_ms=distribution(costs), sensor_to_display_ms=None)
    if len(valid) > 1:
        first, last = valid[0], valid[-1]
        wall = last['monotonic'] - first['monotonic']
        pts = last['time_pos'] - first['time_pos']
        # A backward PTS jump invalidates this diagnostic, e.g. a reconnect.
        reset = any(b['time_pos'] < a['time_pos'] for a, b in zip(valid, valid[1:]))
        result.update(observed_seconds=wall, media_seconds=pts, pts_reset=reset,
                      media_progress_ratio=None if reset or wall <= 0 else pts / wall,
                      relative_lag_growth_seconds=None if reset else wall - pts)
    for key in ('demuxer_cache_duration', 'process_cpu_percent'):
        result[key] = distribution([s[key] for s in samples if finite(s.get(key))])
    result['property_poll_ms'] = distribution([s['property_poll_ms'] for s in samples if finite(s.get('property_poll_ms'))])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'camera.json')
    parser.add_argument('--seconds', type=float, default=20)
    parser.add_argument('--subtype', choices=('0', '1'), help='Override only this read-only connection')
    parser.add_argument('--decode-only', action='store_true', help='Same decode/scale options, no screenshots')
    parser.add_argument('--minimal-sampling', action='store_true', help='Only query PTS during measurement to check observer impact')
    parser.add_argument('--no-scale', action='store_true', help='Decode-only control: remove scale filter on this probe player')
    parser.add_argument('--frame-profiler', choices=('off', 'on'),
                        help='Publish through production FrameManager with latency sink OFF/ON; no AI or GUI events')
    parser.add_argument('--ai-load', action='store_true',
                        help='Isolated model workload from decoder_probe; no production control scheduler or GUI')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 3 <= args.seconds <= 120:
        parser.error('--seconds must be between 3 and 120')
    if args.no_scale and not args.decode_only:
        parser.error('--no-scale requires --decode-only')
    if args.frame_profiler and args.decode_only:
        parser.error('--frame-profiler requires capture, not --decode-only')
    if args.ai_load and args.decode_only:
        parser.error('--ai-load requires capture')
    if args.ai_load and args.frame_profiler is None:
        args.frame_profiler = 'on'
    cfg = json.loads(args.config.read_text())
    if args.subtype is not None:
        parts = urlsplit(cfg['uri'])
        query = dict(parse_qsl(parts.query)); query['subtype'] = args.subtype
        cfg['uri'] = urlunsplit(parts._replace(query=urlencode(query)))
    import mpv
    received = 0
    costs = []
    samples = []
    errors = []
    stream_info = {}
    publish_costs = []
    frames = frame_profiler = None
    ai = None
    if args.frame_profiler:
        from runtime.frame_manager import FrameManager
        from telemetry import latency
        latency.profiler.close()
        # Explicit OFF must not be overridden by launcher environment variables.
        frame_profiler = latency.LatencyProfiler(
            directory=args.output.parent / (args.output.stem + '_trace'),
            enabled=args.frame_profiler == 'on')
        latency.profiler = frame_profiler
        frames = FrameManager()
    if args.ai_load:
        from tools.decoder_probe import PerceptionLoad
        try:
            ai = PerceptionLoad(frames)
        except Exception as exc:
            frame_profiler.close()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(dict(status='AI_INIT_FAILED',
                error_type=type(exc).__name__, measured_seconds=0, ai='isolated_perception'), indent=2)+'\n')
            print('AI_INIT_FAILED:', type(exc).__name__)
            return 2

    def frame_callback(frame):
        nonlocal received
        if frames is not None:
            began = time.monotonic()
            frames.publish(frame)
            publish_costs.append((time.monotonic() - began) * 1000)
        received += 1

    receiver = MPVPlayer(cfg, frame_callback)
    receiver._mpv = mpv
    player = receiver._open()
    if args.no_scale:
        player.vf = []

    class TimedPlayer:
        def __getattr__(self, name):
            return getattr(player, name)

        def screenshot_raw(self, **kwargs):
            start = time.monotonic()
            try:
                return player.screenshot_raw(**kwargs)
            finally:
                costs.append((time.monotonic() - start) * 1000)

    def capture():
        try:
            receiver._capture(TimedPlayer())
        except Exception as exc:
            # Do not record exception messages that could contain credentials.
            errors.append(type(exc).__name__)
            receiver._stop.set()

    thread = None
    started = time.monotonic()
    if ai:
        ai.active = lambda now: started <= now < started + args.seconds
        ai.thread.start()
    if not args.decode_only:
        thread = threading.Thread(target=capture, daemon=True)
        thread.start()
    last_cpu, last_wall = time.process_time(), started
    try:
        while time.monotonic() - started < args.seconds and not receiver._stop.is_set():
            if ai and ai.errors:
                errors.append('AI_RUNTIME_FAILED')
                break
            now = time.monotonic(); cpu = time.process_time()
            row = dict(monotonic=now, elapsed=now-started, received_frames=received,
                       process_cpu_percent=(cpu-last_cpu)/(now-last_wall)*100 if now > last_wall else None)
            last_cpu, last_wall = cpu, now
            keys = ('time_pos',) if args.minimal_sampling else ('time_pos', 'demuxer_cache_duration', 'demuxer_cache_state',
                        'hwdec_current', 'video_params', 'estimated_vf_fps',
                        'decoder_frame_drop_count', 'frame_drop_count')
            for key in keys:
                query_start = time.monotonic()
                try:
                    row[key] = getattr(player, key)
                except Exception:
                    row[key] = None
                if key == 'time_pos':
                    query_end = time.monotonic()
                    row['monotonic'] = (query_start + query_end) / 2
                    row['pts_query_ms'] = (query_end - query_start) * 1000
            row['property_poll_ms'] = (time.monotonic() - now) * 1000
            samples.append(row)
            receiver._stop.wait(1)
    finally:
        receiver._stop.set()
        if thread:
            thread.join(timeout=3)
        for key in ('video_params', 'hwdec_current', 'mpv_version', 'video_codec', 'estimated_vf_fps'):
            try:
                stream_info[key] = getattr(player, key)
            except Exception:
                stream_info[key] = None
        player.terminate()
        if ai:
            ai.stop.set()
            ai.thread.join(timeout=15)
            if ai.thread.is_alive():
                errors.append('AI_SHUTDOWN_TIMEOUT')
        if frame_profiler is not None:
            frame_profiler.close()
    result = dict(created_at=datetime.now().astimezone().isoformat(),
                  ai='isolated_perception' if ai else 'OFF',
                  ai_results=None if ai is None else dict(counts=dict(ai.counts), errors=ai.errors,
                      providers=ai.providers, metrics={k:distribution(v) for k,v in ai.metrics.items()}),
                  mode='decode_only' if args.decode_only else 'production_capture',
                  sampling_mode='pts_only' if args.minimal_sampling else 'detailed',
                  scale_filter_enabled=not args.no_scale,
                  subtype=dict(parse_qsl(urlsplit(cfg['uri']).query)).get('subtype'),
                  requested_seconds=args.seconds,
                  options={k: cfg.get(k) for k in ('video_backend','video_hwdec','video_fps',
                           'video_max_width','video_max_height','transport')},
                  summary=summarize(samples, costs, received), errors=errors, samples=samples,
                  stream_info_at_end=stream_info,
                  frame_profiling=dict(mode=args.frame_profiler,
                      publish_ms=distribution(publish_costs),
                      dropped_log_events=frame_profiler.counters.get('dropped_log_events', 0) if frame_profiler else None,
                      writer_error=frame_profiler.error if frame_profiler else None,
                      scope='FrameManager producer/writer only; full AI/GUI profiling overhead is not measured'),
                  limitations=['No GUI/motor. Optional AI is isolated perception, not production scheduler/target selection/zoom/face gating.',
                               'PTS is a media clock, not a synchronized sensor timestamp.',
                               'Relative lag growth does not measure initial latency.',
                               'demuxer cache is a player buffer metric, not total camera/display latency.'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['summary'], indent=2))
    print('Evidence:', args.output)
    return 1 if errors or not any(finite(s.get('time_pos')) for s in samples) else 0


if __name__ == '__main__':
    raise SystemExit(main())
