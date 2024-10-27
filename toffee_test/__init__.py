try:
    from . import __version
    __version__ = __version.version

except ImportError:
    __version__ = "unknown"
