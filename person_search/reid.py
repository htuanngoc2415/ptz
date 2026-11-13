"""Standalone OSNet-AIN inference. NumPy crops are uint8 HWC BGR by default."""
from collections.abc import Mapping
from pathlib import Path

import numpy as np
from telemetry import latency
from runtime.scheduling import scheduled
from PIL import Image
import torch
from torch.nn import functional as F
from torchvision import transforms

if __package__:
    from .networks import osnet_ain_x1_0
else:
    from networks import osnet_ain_x1_0


def cosine_similarity(feature_a, feature_b):
    """Dot product of L2-normalized embeddings along the last dimension."""
    if feature_a.shape[-1] != 512 or feature_b.shape[-1] != 512:
        raise ValueError('Expected 512-D embeddings')
    return (feature_a * feature_b).sum(dim=-1)


class OSNetReID:
    def __init__(self, model_path, device='cuda', image_height=256,
                 image_width=128, batch_size=16, numpy_color='BGR'):
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(f'Checkpoint not found: {path}')
        if batch_size < 1 or min(image_height, image_width) < 32:
            raise ValueError('batch_size must be positive; image dimensions must be >= 32')
        if numpy_color not in ('RGB', 'BGR'):
            raise ValueError('numpy_color must be RGB or BGR')
        self.device = torch.device(device)
        if self.device.type == 'cuda':
            if not torch.cuda.is_available():
                raise RuntimeError('CUDA requested but unavailable; explicitly select device="cpu"')
            if self.device.index is None:
                self.device = torch.device('cuda:0')
        self.batch_size = batch_size
        self.numpy_color = numpy_color
        self.model = osnet_ain_x1_0(num_classes=1, pretrained=False)
        self._load_checkpoint(path)
        self.model.eval().to(self.device)
        self.transform = transforms.Compose([
            transforms.Resize((image_height, image_width)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])

    def _load_checkpoint(self, path):
        checkpoint = torch.load(path, map_location='cpu', weights_only=True)
        state = checkpoint.get('state_dict', checkpoint) if isinstance(checkpoint, Mapping) else None
        if not isinstance(state, Mapping):
            raise ValueError('Checkpoint must be a state_dict or contain state_dict')
        state = {k.removeprefix('module.'): v for k, v in state.items()}
        expected = self.model.state_dict()
        matched = {k: v for k, v in state.items()
                   if k in expected and isinstance(v, torch.Tensor) and v.shape == expected[k].shape}
        missing = sorted(set(expected) - set(matched))
        unexpected = sorted(set(state) - set(expected))
        self.load_summary = dict(loaded=len(matched), total=len(expected),
                                 skipped=len(state)-len(matched), missing=len(missing),
                                 unexpected=len(unexpected))
        print(f'Loaded parameters: {len(matched)}/{len(expected)}')
        print(f'Skipped parameters: {self.load_summary["skipped"]}')
        print(f'Missing parameters: {len(missing)}')
        print(f'Unexpected parameters: {len(unexpected)}')
        # Every feature weight and running statistic must load; old BN counters may be absent.
        critical = [k for k in missing if not k.startswith('classifier.')
                    and not k.endswith('num_batches_tracked')]
        if critical:
            raise RuntimeError(f'Incomplete backbone checkpoint: {len(critical)} missing/mismatched keys: {critical[:12]}')
        self.model.load_state_dict(matched, strict=False)
        params = dict(self.model.named_parameters())
        self.loaded_backbone_numel = sum(v.numel() for k, v in params.items()
                                         if not k.startswith('classifier.') and k in matched)
        print(f'Loaded backbone scalar parameters: {self.loaded_backbone_numel}')

    def preprocess(self, image):
        if isinstance(image, (str, Path)):
            with Image.open(image) as opened:
                return self.transform(opened.convert('RGB'))
        if isinstance(image, np.ndarray):
            if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3 or 0 in image.shape:
                raise ValueError('NumPy image must be non-empty uint8 HWC with 3 channels')
            if self.numpy_color == 'BGR':
                image = image[..., ::-1]
            image = Image.fromarray(np.ascontiguousarray(image))
        if not isinstance(image, Image.Image):
            raise TypeError('Expected image path, PIL image, or uint8 NumPy crop')
        return self.transform(image.convert('RGB'))

    @scheduled('reid')
    @torch.inference_mode()
    def extract_embeddings(self, images):
        images = list(images)
        if not images:
            return torch.empty((0, 512), device=self.device)
        outputs = []
        for start in range(0, len(images), self.batch_size):
            with latency.span('REID_PREPROCESS', 'reid_preprocess_ms'):
                batch = torch.stack([self.preprocess(im) for im in images[start:start+self.batch_size]]).to(self.device)
            with latency.span('REID_INFERENCE', 'reid_inference_ms', model='OSNet', device=str(self.device), timing='cpu_wall', reid_batch_size=len(batch)):
                with latency.torch_gpu_span('reid_gpu_execution_ms', self.device):
                    features = F.normalize(self.model(batch), p=2, dim=-1)

            with latency.span('REID_POSTPROCESS', 'reid_postprocess_ms'):
                if features.shape != (len(batch), 512) or not torch.isfinite(features).all():
                    raise RuntimeError('Invalid embedding shape or non-finite values')
                if not torch.allclose(features.norm(dim=-1), torch.ones(len(batch), device=self.device), atol=1e-5):
                    raise RuntimeError('Embedding norms are not approximately 1')
                outputs.append(features)
        return torch.cat(outputs)

    def extract_embedding(self, image):
        return self.extract_embeddings([image])[0]

