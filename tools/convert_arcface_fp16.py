"""Create a separate FP16 ArcFace model with the existing float32 input/output API."""
import argparse
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=root/'models/face/w600k_r50.onnx')
    parser.add_argument('--output', type=Path, default=root/'models/face/w600k_r50.fp16.onnx')
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f'Output already exists: {args.output}')
    import onnx
    from onnxruntime.transformers.float16 import convert_float_to_float16
    from onnxruntime.transformers.onnx_model import OnnxModel
    model = convert_float_to_float16(onnx.load(str(args.input)), keep_io_types=True)
    # The converter appends I/O Cast nodes; restore graph order before validation.
    OnnxModel(model).topological_sort()
    onnx.checker.check_model(model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, str(args.output))
    print(f'Saved {args.output} ({args.output.stat().st_size / 2**20:.1f} MiB)')
    print('Use CUDA and validate embeddings before deploying this model.')


if __name__ == '__main__':
    main()
