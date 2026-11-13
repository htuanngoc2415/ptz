"""Small temporal context owned by the AI worker, reset on person loss."""

class IdentityConfirmation:
    def __init__(self, required, timeout):
        self.required = required
        self.timeout = timeout
        self.reset()

    def reset(self):
        self.candidate = None
        self.count = 0
        self.last_time = float('-inf')

    def update(self, result, now):
        if now - self.last_time > self.timeout:
            self.reset()
        identity = result.get('identity') if result.get('matched') else None
        if not identity or identity.lower() == 'unknown':
            self.reset()
        else:
            self.count = min(self.count + 1, self.required) if identity == self.candidate else 1
            self.candidate = identity
            self.last_time = now
        return dict(result, confirmed=self.count >= self.required,
                    confirm_count=self.count,
                    final_identity=self.candidate if self.count >= self.required else None)
