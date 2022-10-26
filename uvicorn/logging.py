import http
import logging
import sys
import time
import typing
from collections import abc
from copy import copy
from os import getpid
from typing import Callable, Dict, Iterator, Optional, cast

import click

if sys.version_info < (3, 8):  # pragma: py-gte-38
    from typing_extensions import Literal
else:  # pragma: py-lt-38
    from typing import Literal

if typing.TYPE_CHECKING:
    from asgiref.typing import ASGISendEvent, HTTPScope

    import uvicorn.protocols.utils

TRACE_LOG_LEVEL = 5


class ColourizedFormatter(logging.Formatter):
    """
    A custom log formatter class that:

    * Outputs the LOG_LEVEL with an appropriate color.
    * If a log call includes an `extras={"color_message": ...}` it will be used
      for formatting the output, instead of the plain text message.
    """

    level_name_colors = {
        TRACE_LOG_LEVEL: lambda level_name: click.style(str(level_name), fg="blue"),
        logging.DEBUG: lambda level_name: click.style(str(level_name), fg="cyan"),
        logging.INFO: lambda level_name: click.style(str(level_name), fg="green"),
        logging.WARNING: lambda level_name: click.style(str(level_name), fg="yellow"),
        logging.ERROR: lambda level_name: click.style(str(level_name), fg="red"),
        logging.CRITICAL: lambda level_name: click.style(
            str(level_name), fg="bright_red"
        ),
    }

    def __init__(
        self,
        fmt: Optional[str] = None,
        datefmt: Optional[str] = None,
        style: Literal["%", "{", "$"] = "%",
        use_colors: Optional[bool] = None,
    ):
        if use_colors in (True, False):
            self.use_colors = use_colors
        else:
            self.use_colors = sys.stdout.isatty()
        super().__init__(fmt=fmt, datefmt=datefmt, style=style)

    def color_level_name(self, level_name: str, level_no: int) -> str:
        def default(level_name: str) -> str:
            return str(level_name)  # pragma: no cover

        func = self.level_name_colors.get(level_no, default)
        return func(level_name)

    def should_use_colors(self) -> bool:
        return True  # pragma: no cover

    def formatMessage(self, record: logging.LogRecord) -> str:
        recordcopy = copy(record)
        levelname = recordcopy.levelname
        seperator = " " * (8 - len(recordcopy.levelname))
        if self.use_colors:
            levelname = self.color_level_name(levelname, recordcopy.levelno)
            if "color_message" in recordcopy.__dict__:
                recordcopy.msg = recordcopy.__dict__["color_message"]
                recordcopy.__dict__["message"] = recordcopy.getMessage()
        recordcopy.__dict__["levelprefix"] = levelname + ":" + seperator
        return super().formatMessage(recordcopy)


class DefaultFormatter(ColourizedFormatter):
    def should_use_colors(self) -> bool:
        return sys.stderr.isatty()  # pragma: no cover


class AccessFormatter(ColourizedFormatter):
    status_code_colours = {
        1: lambda code: click.style(str(code), fg="bright_white"),
        2: lambda code: click.style(str(code), fg="green"),
        3: lambda code: click.style(str(code), fg="yellow"),
        4: lambda code: click.style(str(code), fg="red"),
        5: lambda code: click.style(str(code), fg="bright_red"),
    }

    def get_status_code(self, status_code: int) -> str:
        try:
            status_phrase = http.HTTPStatus(status_code).phrase
        except ValueError:
            status_phrase = ""
        status_and_phrase = "%s %s" % (status_code, status_phrase)
        if self.use_colors:

            def default(code: int) -> str:
                return status_and_phrase  # pragma: no cover

            func = self.status_code_colours.get(status_code // 100, default)
            return func(status_and_phrase)
        return status_and_phrase

    def formatMessage(self, record: logging.LogRecord) -> str:
        if len(getattr(record, "args")) != 5:
            return super().formatMessage(record)
        recordcopy = copy(record)
        (
            client_addr,
            method,
            full_path,
            http_version,
            status_code,
        ) = recordcopy.args  # type: ignore[misc]
        status_code = self.get_status_code(int(status_code))  # type: ignore[arg-type]
        request_line = "%s %s HTTP/%s" % (method, full_path, http_version)
        if self.use_colors:
            request_line = click.style(request_line, bold=True)
        recordcopy.__dict__.update(
            {
                "client_addr": client_addr,
                "request_line": request_line,
                "status_code": status_code,
            }
        )
        return super().formatMessage(recordcopy)


class AccessLogFields(abc.Mapping):  # pragma: no cover
    """Container to provide fields for access logging.

    This class does a few things:
    - provide all fields necessary for access log formatter
    - collect info from ASGI messages (status_code/headers/response body size)
    - provide mapping interface that returns '-' for missing fields
    - escape double quotes found in fields strings
    """

    def __init__(
        self,
        scope: "HTTPScope",
        timing: "uvicorn.protocols.utils.RequestResponseTiming",
    ):
        self.scope = scope
        self.timing = timing
        self.status_code: Optional[int] = None
        self.response_headers: Dict[str, str] = {}
        self._response_length = 0

        self._request_headers: Optional[Dict[str, str]] = None

    @property
    def request_headers(self) -> Dict[str, str]:
        if self._request_headers is None:
            self._request_headers = {
                k.decode("ascii"): v.decode("ascii") for k, v in self.scope["headers"]
            }
        return self._request_headers

    @property
    def duration(self) -> float:
        return self.timing.total_duration_seconds()

    def on_asgi_message(self, message: "ASGISendEvent") -> None:
        if message["type"] == "http.response.start":
            self.status_code = message["status"]
            self.response_headers = {
                k.decode("ascii"): v.decode("ascii")
                for k, v in message.get("headers", {})
            }
        elif message["type"] == "http.response.body":
            self._response_length += len(message.get("body", ""))

    def _request_header(self, key: str) -> Optional[str]:
        return self.request_headers.get(key.lower())

    def _response_header(self, key: str) -> Optional[str]:
        return self.response_headers.get(key.lower())

    def _wsgi_environ_variable(self, key: str) -> None:
        # FIXME: provide fallbacks to access WSGI environ (at least the
        # required variables).
        raise NotImplementedError

    @classmethod
    def _log_format_atom(cls, val: Optional[str]) -> str:
        if val is None:
            return "-"
        if isinstance(val, str):
            return val.replace('"', '\\"')
        return val

    def __getitem__(self, key: str) -> str:
        retval: Optional[str]
        if key in self.HANDLERS:
            retval = self.HANDLERS[key](self)
        elif key.startswith("{"):
            if key.endswith("}i"):
                retval = self._request_header(key[1:-2])
            elif key.endswith("}o"):
                retval = self._response_header(key[1:-2])
            elif key.endswith("}e"):
                # retval = self._wsgi_environ_variable(key[1:-2])
                raise NotImplementedError("WSGI environ not supported")
            else:
                retval = None
        else:
            retval = None
        return self._log_format_atom(retval)

    _LogAtomHandler = Callable[["AccessLogFields"], Optional[str]]
    HANDLERS: Dict[str, _LogAtomHandler] = {}

    # mypy does not understand class-member decorators:
    #
    # https://github.com/python/mypy/issues/7778
    def _register_handler(  # type: ignore[misc]
        key: str, handlers: Dict[str, _LogAtomHandler] = HANDLERS
    ) -> Callable[[_LogAtomHandler], _LogAtomHandler]:
        _LogAtomHandler = Callable[["AccessLogFields"], Optional[str]]

        def decorator(fn: _LogAtomHandler) -> _LogAtomHandler:
            handlers[key] = fn
            return fn

        return decorator

    @_register_handler("h")
    def _remote_address(self) -> Optional[str]:
        client: tuple = cast(tuple, self.scope.get("client", ()))
        return client[0]

    @_register_handler("l")
    def _dash(self) -> str:
        return "-"

    @_register_handler("u")
    def _user_name(self) -> Optional[str]:
        pass

    @_register_handler("t")
    def date_of_the_request(self) -> Optional[str]:
        """Date and time in Apache Common Log Format"""
        return time.strftime("[%d/%b/%Y:%H:%M:%S %z]")

    @_register_handler("r")
    def status_line(self) -> Optional[str]:
        full_raw_path = self.scope["raw_path"] + self.scope["query_string"]
        full_path = full_raw_path.decode("ascii")
        return "{method} {full_path} HTTP/{http_version}".format(
            full_path=full_path, **self.scope
        )

    @_register_handler("m")
    def request_method(self) -> Optional[str]:
        return self.scope["method"]

    @_register_handler("U")
    def url_path(self) -> Optional[str]:
        return self.scope["raw_path"].decode("ascii")

    @_register_handler("q")
    def query_string(self) -> Optional[str]:
        return self.scope["query_string"].decode("ascii")

    @_register_handler("H")
    def protocol(self) -> Optional[str]:
        return "HTTP/%s" % self.scope["http_version"]

    @_register_handler("s")
    def status(self) -> Optional[str]:
        return str(self.status_code) or "-"

    @_register_handler("B")
    def response_length(self) -> Optional[str]:
        return str(self._response_length)

    @_register_handler("b")
    def response_length_or_dash(self) -> Optional[str]:
        return str(self._response_length or "-")

    @_register_handler("f")
    def referer(self) -> Optional[str]:
        return self.request_headers.get("referer")

    @_register_handler("a")
    def user_agent(self) -> Optional[str]:
        return self.request_headers.get("user-agent")

    @_register_handler("T")
    def request_time_seconds(self) -> Optional[str]:
        return str(int(self.duration))

    @_register_handler("D")
    def request_time_microseconds(self) -> str:
        return str(int(self.duration * 1_000_000))

    @_register_handler("L")
    def request_time_decimal_seconds(self) -> str:
        return "%.6f" % self.duration

    @_register_handler("p")
    def process_id(self) -> str:
        return "<%s>" % getpid()

    def __iter__(self) -> Iterator[str]:
        # FIXME: add WSGI environ
        yield from self.HANDLERS
        for k, _ in self.scope["headers"]:
            ks = k.decode("utf-8").lower()
            yield f"{ks}i"
        header_keys: list = list(self.response_headers.keys())
        for k in header_keys:
            ks = k.decode("utf-8").lower()
            yield f"{ks}o"

    def __len__(self) -> int:
        # FIXME: add WSGI environ
        headers: tuple = cast(tuple, self.scope.get("headers", ()))
        return len(self.HANDLERS) + len(headers) + len(self.response_headers)
