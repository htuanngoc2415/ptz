"""Capabilities reflect fields actually read from this firmware, not guesses."""
from dataclasses import dataclass, field


@dataclass
class CameraImageCapabilities:
    fields: set = field(default_factory=set)
    unsupported: set = field(default_factory=set)

    @classmethod
    def from_results(cls, results):
        return cls({k for k,v in results.items() if v == 'verified'},
                   {k for k,v in results.items() if v == 'unsupported'})

    @property
    def supports_blc(self): return 'blc_level' in self.fields
    @property
    def supports_hlc(self): return 'hlc_enable' in self.fields
    @property
    def supports_wdr(self): return {'wdr_sensor', 'wdr_level_sensor'} <= self.fields
    @property
    def supports_gamma(self): return 'gamma' in self.fields
    @property
    def supports_ae_strategy(self): return 'AE_strategy_mode' in self.fields
    @property
    def supports_manual_exposure(self): return 'exposure' in self.fields
    @property
    def supports_gain(self): return 'gain' in self.fields
