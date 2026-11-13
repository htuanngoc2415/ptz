"""Conservative detection association; track IDs never replace ReID confirmation."""
from ai.reid_target import iou
from telemetry import latency


class PersonTracker:
    def __init__(self, threshold, margin, max_missing, grace):
        self.threshold, self.margin = threshold, margin
        self.max_missing, self.grace = max_missing, grace
        self.tracks = {}
        self.next_id = 0

    def reset(self):
        self.tracks.clear()  # IDs are never reused during this application's run.

    @latency.measured('TRACK_ASSOCIATION', 'track_association_ms')
    def update(self, persons, received):
        for track in self.tracks.values():
            track['missing'] += 1
        proposed = {}
        for index, person in enumerate(persons):
            ranked = sorted(((iou(person['bbox'], track['bbox']), ident)
                             for ident, track in self.tracks.items()), reverse=True)
            if (ranked and ranked[0][0] >= self.threshold and
                    (len(ranked) == 1 or ranked[0][0]-ranked[1][0] >= self.margin)):
                proposed[index] = ranked[0][1]
        counts = {}
        for ident in proposed.values():
            counts[ident] = counts.get(ident, 0)+1
        for index, person in enumerate(persons):
            ident = proposed.get(index)
            if ident is None or counts[ident] != 1:
                self.next_id += 1
                ident = self.next_id
            person['track_id'] = ident
            self.tracks[ident] = dict(bbox=person['bbox'], missing=0, last_seen=received)
        self.tracks = {ident: track for ident, track in self.tracks.items()
                       if track['missing'] < self.max_missing or received-track['last_seen'] < self.grace}
        return persons
