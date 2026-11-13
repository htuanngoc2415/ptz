"""Two-image check; missing inputs trigger a clearly labeled synthetic smoke test."""
from pathlib import Path
import numpy as np
from PIL import Image
import torch
from torch.nn import functional as F
if __package__:
    from .reid import OSNetReID, cosine_similarity
else:
    from reid import OSNetReID, cosine_similarity

BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / 'models/osnet_ain_x1_0_msmt17.pth'
QUERY_IMAGE = BASE_DIR / 'query/target.jpg'
GALLERY_IMAGE = BASE_DIR / 'gallery/person_001.jpg'
DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'


def main():
    print(f'CUDA available: {torch.cuda.is_available()}')
    print(f'Device: {DEVICE}')
    print('Model: OSNet-AIN x1.0')
    reid = OSNetReID(MODEL_PATH, device=DEVICE)
    if QUERY_IMAGE.is_file() and GALLERY_IMAGE.is_file():
        query, gallery = QUERY_IMAGE, GALLERY_IMAGE
    else:
        print('SYNTHETIC SMOKE TEST: real input images missing; ReID accuracy NOT evaluated.')
        rng = np.random.default_rng(42)
        query, gallery = [Image.fromarray(rng.integers(0, 256, (256, 128, 3), dtype=np.uint8)) for _ in range(2)]
    q, g = reid.extract_embedding(query), reid.extract_embedding(gallery)
    assert q.shape == g.shape == (512,)
    assert q.device == g.device == next(reid.model.parameters()).device == reid.device
    print(f'Model device: {next(reid.model.parameters()).device}; tensor device: {q.device}')
    print(f'Query embedding shape: {q.shape}')
    print(f'Gallery embedding shape: {g.shape}')
    print(f'Query norm: {q.norm().item():.6f}')
    print(f'Gallery norm: {g.norm().item():.6f}')
    print(f'Cosine similarity: {cosine_similarity(q, g).item():.6f}')
    with torch.inference_mode():
        output = reid.model(torch.randn(2, 3, 256, 128, device=reid.device))
        assert output.shape == (2, 512)
        assert torch.allclose(F.normalize(output, dim=-1).norm(dim=-1), torch.ones(2, device=reid.device), atol=1e-5)
    print('Random tensor architecture check: [2, 512] PASS')
    print('test_reid.py PASS')


if __name__ == '__main__':
    main()
