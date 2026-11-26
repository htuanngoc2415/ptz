"""Opt-in OSNet shadow batching; baseline extraction and weights stay untouched."""
from runtime.scheduling import scheduler, opportunistic_inference


class PreparedShadowBatch:
    """One CPU minibatch, resumed at its missing accelerator admission only.

    Uses the existing OSNet preprocessing and eval module. Raw and normalized
    crops share batches; no inference result is reused across frames.
    """
    def __init__(self, model, gallery, crops):
        from person_search.reid import OSNetReID
        if type(model) is not OSNetReID:
            raise TypeError('Prepared shadow batching requires the standard OSNetReID backend')
        if model.model.training or model.batch_size < 1:
            raise ValueError('Prepared shadow requires eval model and positive batch size')
        self.model, self.gallery, self.crops = model, gallery, crops
        self.offset = 0
        self.prepared = None
        self.scores, self.vectors = [], []
        self.completed_passes = 0

    def run(self, valid):
        import torch
        from torch.nn import functional as F
        from telemetry import latency
        with torch.inference_mode():
            while self.offset < len(self.crops):
                if not valid():
                    raise TimeoutError('Shadow frame expired before preprocessing')
                if self.prepared is None:
                    # CPU transform has no model state and does not need the GPU lane.
                    chunk = self.crops[self.offset:self.offset+self.model.batch_size]
                    with latency.span('SHADOW_PREPROCESS', 'identity_shadow_preprocess_ms'):
                        self.prepared = torch.stack([self.model.preprocess(im) for im in chunk])
                with opportunistic_inference():
                    with scheduler.use('reid', valid=valid) as granted:
                        if not granted:
                            raise TimeoutError('Shadow frame expired before batch admission')
                        if self.model.model.training:
                            raise ValueError('OSNet must remain in eval mode')
                        batch = self.prepared.to(self.model.device)
                        with latency.span('SHADOW_INFERENCE', 'identity_shadow_inference_ms',
                                          reid_batch_size=len(batch), timing='cpu_wall'):
                            features = F.normalize(self.model.model(batch), p=2, dim=-1)
                        if features.shape != (len(batch),512) or not torch.isfinite(features).all():
                            raise RuntimeError('Invalid shadow embeddings')
                        if not torch.allclose(features.norm(dim=-1),torch.ones(len(batch),device=features.device),atol=1e-5):
                            raise RuntimeError('Invalid shadow embedding norms')
                        scores = self.gallery.score(features).detach().cpu().tolist()
                        vectors = features.detach().cpu().tolist()
                self.completed_passes += 1
                self.scores.extend(scores);self.vectors.extend(vectors)
                self.offset += len(vectors)
                self.prepared = None
            return self.scores, self.vectors
