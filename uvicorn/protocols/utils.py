import asyncio
import time
import urllib.parse
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from asgiref.typing import WWWScope


def get_remote_addr(transport: asyncio.Transport) -> Optional[Tuple[str, int]]:
    socket_info = transport.get_extra_info("socket")
    if socket_info is not None:
        try:
            info = socket_info.getpeername()
            return (str(info[0]), int(info[1])) if isinstance(info, tuple) else None
        except OSError:  # pragma: no cover
            # This case appears to inconsistently occur with uvloop
            # bound to a unix domain socket.
            return None

    info = transport.get_extra_info("peername")
    if info is not None and isinstance(info, (list, tuple)) and len(info) == 2:
        return (str(info[0]), int(info[1]))
    return None


def get_local_addr(transport: asyncio.Transport) -> Optional[Tuple[str, int]]:
    socket_info = transport.get_extra_info("socket")
    if socket_info is not None:
        info = socket_info.getsockname()

        return (str(info[0]), int(info[1])) if isinstance(info, tuple) else None
    info = transport.get_extra_info("sockname")
    if info is not None and isinstance(info, (list, tuple)) and len(info) == 2:
        return (str(info[0]), int(info[1]))
    return None


def is_ssl(transport: asyncio.Transport) -> bool:
    return bool(transport.get_extra_info("sslcontext"))


def get_client_addr(scope: "WWWScope") -> str:
    client = scope.get("client")
    if not client:
        return ""
    return "%s:%d" % client


def get_path_with_query_string(scope: "WWWScope") -> str:
    path_with_query_string = urllib.parse.quote(scope["path"])
    if scope["query_string"]:
        path_with_query_string = "{}?{}".format(
            path_with_query_string, scope["query_string"].decode("ascii")
        )
    return path_with_query_string


class RequestResponseTiming:
    # XXX: switch to "time.perf_counter" because apparently on windows
    # time.monotonis is using GetTickCount64 which has ~15ms resolution (it
    # caused problems in tests on windows)
    #
    # ref: https://github.com/python-trio/trio/issues/33#issue-202432431
    def __init__(self) -> None:
        self._request_start_time: Optional[float] = None
        self._request_end_time: Optional[float] = None
        self._response_start_time: Optional[float] = None
        self._response_end_time: Optional[float] = None

    def request_started(self) -> None:
        self._request_start_time = time.monotonic()

    @property
    def request_start_time(self) -> float:
        if self._request_start_time is None:
            raise ValueError("request_started() was not called")
        return self._request_start_time

    def request_ended(self) -> None:
        self._request_end_time = time.monotonic()

    @property
    def request_end_time(self) -> float:
        if self._request_end_time is None:
            raise ValueError("request_ended() was not called")
        return self._request_end_time

    def response_started(self) -> None:
        self._response_start_time = time.monotonic()

    @property
    def response_start_time(self) -> float:
        if self._response_start_time is None:
            raise ValueError("response_started() was not called")
        return self._response_start_time

    def response_ended(self) -> None:
        self._response_end_time = time.monotonic()

    @property
    def response_end_time(self) -> float:
        if self._response_end_time is None:
            raise ValueError("response_ended() was not called")
        return self._response_end_time

    def request_duration_seconds(self) -> float:
        return self.request_end_time - self.request_start_time

    def response_duration_seconds(self) -> float:
        return self.response_end_time - self.response_start_time

    def total_duration_seconds(self) -> float:
        return self.response_end_time - self.request_start_time
