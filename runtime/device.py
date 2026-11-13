"""Select a usable inference device before constructing AI models."""
from event_logging import event_print


def resolve_device(requested, strict=False):
    import torch
    value = str(requested).lower().strip()
    if value == 'cpu':
        return 'cpu'
    if not (value.isdecimal() or value.startswith('cuda')):
        return requested
    index = int(value) if value.isdecimal() else int(value.split(':')[1]) if ':' in value else 0
    try:
        if not torch.cuda.is_available() or index >= torch.cuda.device_count():
            raise RuntimeError('CUDA unavailable')
        # Enumeration alone does not prove the driver can execute tensors.
        probe = torch.ones((32, 32), device=f'cuda:{index}')
        (probe @ probe).sum().item()
    except Exception as exc:
        detail = (str(exc) or type(exc).__name__).splitlines()[0]
        if strict:
            raise RuntimeError(
                f'GPU {index} không sẵn sàng: {detail}. '
                'Đóng các ứng dụng dùng nhiều RAM/GPU rồi chạy lại. '
                'Nếu muốn chủ động dùng CPU: PTZ_DEVICE=cpu bash run_camera.sh'
            ) from exc
        event_print(f'[AI] CUDA {index} unavailable ({type(exc).__name__}: {detail}); using CPU')
        return 'cpu'
    return index
