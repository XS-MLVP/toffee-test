try:
    from . import __version

    __version__ = __version.version

except ImportError:
    __version__ = "unknown"

from toffee_test.testcase import testcase, fixture
from toffee_test.request import ToffeeRequest, PreRequest
from toffee_test.utils import base64_decode, base64_encode

__all__ = ["testcase", "fixture", "ToffeeRequest", "PreRequest", "base64_decode", "base64_encode"]
