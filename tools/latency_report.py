#!/usr/bin/env python3
"""Print the latest bounded latency summary or filter a JSONL trace by lock session."""
import argparse
import csv
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', nargs='?', type=Path)
    parser.add_argument('--session', help='lock_session_id to extract from a JSONL trace')
    args = parser.parse_args()
    path = args.path
    if path is None:
        paths = sorted((Path(__file__).resolve().parents[1]/'logs/latency').glob('latency_summary_*.csv'))
        if not paths:
            parser.error('No latency summary yet; enable profiling and run the application first')
        path = paths[-1]
    if path.suffix == '.csv':
        print(f"{'Stage':38} {'Count':>8} {'Mean':>9} {'P50':>9} {'P90':>9} {'P95':>9} {'P99':>9}")
        with path.open() as stream:
            for row in csv.DictReader(stream):
                print(f"{row['stage']:38} {row['count']:>8}"+''.join(f" {float(row[key]):9.2f}" for key in ('mean','p50','p90','p95','p99')))
        print('Durations in ms; percentiles use the bounded recent sample window.')
    else:
        with path.open() as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue  # tolerate the final line of a live file
                if args.session is None or row.get('lock_session_id') == args.session:
                    print(json.dumps(row, ensure_ascii=False))


if __name__ == '__main__':
    main()
