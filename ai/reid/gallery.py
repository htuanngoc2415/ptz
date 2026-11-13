import cv2
import numpy as np
import torch


def illumination_normalized(crop, gamma=.75, clahe=1.5):
    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)
    lab[:, :, 0] = cv2.createCLAHE(clipLimit=clahe, tileGridSize=(4,4)).apply(lab[:, :, 0])
    result = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    table = np.clip((np.arange(256)/255.)**gamma*255, 0, 255).astype(np.uint8)
    return cv2.LUT(result, table)


def target_variants(crop):
    # Mild transforms only; never replace the raw image or alter geometry.
    def linear(alpha, beta=0):
        return np.clip(crop.astype(np.float32)*alpha+beta, 0, 255).astype(np.uint8)
    gamma = np.clip((np.arange(256)/255.)**.9*255, 0, 255).astype(np.uint8)
    return [crop, linear(.85), linear(1.1, 3), cv2.LUT(crop, gamma), linear(.9, 12)]


class TargetEmbeddingGallery:
    def __init__(self, embeddings):
        self.embeddings = embeddings.detach()
        if self.embeddings.ndim != 2 or self.embeddings.shape[1] != 512 or not len(self.embeddings):
            raise ValueError('Expected nonempty Nx512 target gallery')
        norms = self.embeddings.norm(dim=1)
        if not torch.isfinite(self.embeddings).all() or not torch.allclose(norms, torch.ones_like(norms), atol=1e-4):
            raise ValueError('Invalid normalized target gallery')

    @classmethod
    def build(cls, model, crop, enabled=True):
        variants = target_variants(crop) if enabled else [crop]
        return cls(model.extract_embeddings(variants))

    def score(self, embeddings):
        return (embeddings @ self.embeddings.T).max(dim=1).values
