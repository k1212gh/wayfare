"""TapWalker mixins — cohesive method groups split by responsibility.

Each mixin targets ``self`` fields set up in ``TapWalker.__init__``; they
only work when mixed into the full class. They live here (not as free functions)
because too much state is shared with the main loop to pass as arguments
without inventing a parallel data class.

MRO order in ``tap_walker.py``::

    class TapWalker(ScanMixin, CaptureMixin, GuardsMixin, DeviceSessionMixin):
        ...
"""

from .device_session import DeviceSessionMixin
from .capture import CaptureMixin
from .guards import GuardsMixin
from .scan import ScanMixin

__all__ = [
    "DeviceSessionMixin",
    "CaptureMixin",
    "GuardsMixin",
    "ScanMixin",
]
