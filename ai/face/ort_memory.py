"""Bound ONNX allocator growth on Jetson's shared RAM without changing device."""
import os
from functools import lru_cache
import onnxruntime as ort
from event_logging import event_print


@lru_cache(maxsize=1)
def prepare_cuda_libraries():
    """Load pip-installed CUDA/cuDNN libraries before creating an ORT session."""
    preload = getattr(ort, 'preload_dlls', None)
    if preload is not None:
        # Prefer NVIDIA wheels over the CUDA 11 libraries bundled with torch.
        preload(directory='')


def session_options():
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.add_session_config_entry('session.intra_op.allow_spinning', '0')
    options.enable_cpu_mem_arena = False
    options.enable_mem_pattern = False
    return options


def provider_options(providers):
    limit_mb = int(os.getenv('PTZ_ORT_GPU_MEM_MB', '1024'))
    if limit_mb <= 0:
        raise ValueError('PTZ_ORT_GPU_MEM_MB must be a positive integer (MiB)')
    if 'CUDAExecutionProvider' in providers:
        prepare_cuda_libraries()
        event_print(f'[ONNX] CUDA requested arena_limit_mib={limit_mb} '
                    'arena_extend_strategy=kSameAsRequested '
                    'cudnn_conv_algo_search=HEURISTIC cudnn_conv_use_max_workspace=0')
    # Default CUDA arenas grow exponentially; cuDNN's exhaustive search can
    # also reserve large temporary workspaces while several models coexist.
    return [dict(arena_extend_strategy='kSameAsRequested',
                 cudnn_conv_algo_search='HEURISTIC',
                 cudnn_conv_use_max_workspace='0',
                 gpu_mem_limit=str(limit_mb * 1024 * 1024))
            if name == 'CUDAExecutionProvider' else {} for name in providers]
