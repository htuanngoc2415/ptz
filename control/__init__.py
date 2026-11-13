"""
Control package for PTZ AI camera.

This package contains high-level camera control logic:

    - Hierarchical Person -> Face state machine
    - Optical auto zoom
    - Auto focus
    - Digital PTZ

AI inference is handled by ai/.
Low-level camera communication is handled by camera/.
"""

from .target_state_machine import (
    TargetState,
    TargetStateMachine,
    StateMachineResult,
)

from .auto_zoom import (
    AutoZoomController,
)

from .auto_focus import (
    AutoFocusController,
)

from .digital_ptz import (
    DigitalPTZController,
)


__all__ = [
    "TargetState",
    "TargetStateMachine",
    "StateMachineResult",
    "AutoZoomController",
    "AutoFocusController",
    "DigitalPTZController",
]
