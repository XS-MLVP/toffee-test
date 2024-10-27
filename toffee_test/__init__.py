try:
    from . import __version

    __version__ = __version.version

except ImportError:
    __version__ = "unknown"

from toffee_test.testcase import case, fixture
from toffee_test.request import ToffeeRequest, PreRequest

__all__ = ["case", "fixture", "ToffeeRequest", "PreRequest"]
