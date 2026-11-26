"""One accelerator lane; CPU-only face work does not block GPU tracking."""
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
import threading
from telemetry import latency

background_work = ContextVar('background_inference', default=False)


class BackgroundBusy(TimeoutError):
    pass


@contextmanager
def opportunistic_inference():
    """Thread/context-local admission: never queue background work ahead of live work."""
    token = background_work.set(True)
    try:
        yield
    finally:
        background_work.reset(token)


class InferenceScheduler:
    def __init__(self):
        self.condition = threading.Condition()
        self.owner = None
        self.depth = 0
        self.waiting_detection = 0
        self.waiting_face = 0
        self.owner_branch = None
        self.waiting_foreground = 0

    @contextmanager
    def use(self, branch, valid=None, resource="accelerator"):
        ident = threading.get_ident()
        background = background_work.get() and resource != 'cpu'
        if background:
            branch = 'identity_shadow'
        start = latency.profiler.event('INFERENCE_QUEUE_ENTER', branch=branch, resource=resource,
            observed_owner=self.owner_branch, waiting_detection=self.waiting_detection,
            waiting_face=self.waiting_face)
        if resource == 'cpu':
            # Each CPU session has a single worker owner. Independent CPU sessions may overlap.
            with nullcontext():
                latency.profiler.elapsed('INFERENCE_QUEUE_EXIT', start, branch+'_queue_wait_ms',
                                         branch=branch, resource=resource)
                granted = valid is None or valid()
                if not granted:
                    latency.profiler.count('stale_inference_jobs_skipped')
                    latency.profiler.count('stale_reason_ADMISSION_VALIDITY_REJECTED')
                    latency.profiler.event('INFERENCE_JOB_SKIPPED', branch=branch,
                                           resource=resource, reason='STALE_BEFORE_INFERENCE')
                yield granted
            return
        with self.condition:
            nested = self.owner == ident
            if background and not nested and (self.owner is not None or self.waiting_foreground):
                # Raise rather than yield False: decorators must not run the model.
                raise BackgroundBusy('Foreground accelerator work active or waiting')
            if not nested:
                if branch == 'detection':
                    self.waiting_detection += 1
                if branch == 'face':
                    self.waiting_face += 1
                if not background:
                    self.waiting_foreground += 1
                try:
                    self.condition.wait_for(lambda: self.owner is None and
                        (branch == 'detection' or self.waiting_detection == 0) and
                        (branch != 'recognition' or self.waiting_face == 0))
                    self.owner = ident
                    self.owner_branch = branch
                finally:
                    if not background:
                        self.waiting_foreground -= 1
                    if branch == 'detection':
                        self.waiting_detection -= 1
                    if branch == 'face':
                        self.waiting_face -= 1
            self.depth += 1
        latency.profiler.elapsed('INFERENCE_QUEUE_EXIT', start, branch+'_queue_wait_ms', branch=branch, resource=resource)
        granted_ns = latency.profiler.event('RESOURCE_ACQUIRED', branch=branch, resource=resource, nested=nested)
        try:
            granted = valid is None or valid()
            if not granted:
                latency.profiler.count('stale_inference_jobs_skipped')
                latency.profiler.count('stale_reason_ADMISSION_VALIDITY_REJECTED')
                latency.profiler.event('INFERENCE_JOB_SKIPPED', branch=branch, reason='STALE_BEFORE_INFERENCE')
            yield granted
        finally:
            latency.profiler.elapsed('RESOURCE_RELEASED', granted_ns, branch+'_resource_hold_ms',
                branch=branch, resource=resource, nested=nested)
            with self.condition:
                self.depth -= 1
                if not self.depth:
                    self.owner = None
                    self.owner_branch = None
                    self.condition.notify_all()


scheduler = InferenceScheduler()


def scheduled(branch):
    from functools import wraps
    def decorate(function):
        @wraps(function)
        def invoke(*args, **kwargs):
            with scheduler.use(branch):
                return function(*args, **kwargs)
        return invoke
    return decorate


def inference_resource(model):
    """Only a concrete, CPU-only ORT provider list can bypass the accelerator lane."""
    session = getattr(model, 'session', None)
    try:
        providers = session.get_providers()
    except (AttributeError, TypeError):
        return 'accelerator'
    return ('cpu' if isinstance(providers, (list, tuple)) and providers
            and all(p == 'CPUExecutionProvider' for p in providers) else 'accelerator')
