"""Test package. Importing it blocks every socket: the pack must pass alone and offline.

This is the only file of the repository allowed to import a network module, and it does so to disable
it. ``tests/test_no_network.py`` fails if any other file imports one.
"""
import socket


class NetworkBlocked(RuntimeError):
    """Raised when anything tries to open a socket during the tests."""


def _blocked(*_args, **_kwargs):
    raise NetworkBlocked("network access is blocked in the tests of this pack")


class _BlockedSocket(socket.socket):
    def __init__(self, *args, **kwargs):  # noqa: D401 - never constructed
        _blocked()


socket.socket = _BlockedSocket
socket.create_connection = _blocked
socket.getaddrinfo = _blocked
socket.gethostbyname = _blocked
