#!/usr/bin/env python3
"""Record externally measured trials; no camera commands and no fabricated labels."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from telemetry.benchmark import SCENARIOS, record_trial


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=SCENARIOS, required=True)
    parser.add_argument('--metrics', type=Path, required=True, help='JSON object; omit unknown metrics')
    parser.add_argument('--output', default='logs/benchmark.jsonl')
    parser.add_argument('--session')
    parser.add_argument('--notes', default='')
    args = parser.parse_args()
    record_trial(args.output, args.scenario, json.loads(args.metrics.read_text()), args.notes, args.session)
    print(f'Recorded {args.scenario} in {args.output}')


if __name__ == '__main__':
    main()
