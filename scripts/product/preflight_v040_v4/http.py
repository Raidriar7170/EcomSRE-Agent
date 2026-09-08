"""Bounded fixed-loopback HTTP with create-once request/response evidence."""

from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any
import httpx
from .common import Failure, now, require, seal


class LocalHTTP:
    def __init__(self, root: Path, *, port: int, token: str | None = None) -> None:
        require(
            port in (18080, 18016, 19090, 19200, 11686, 18001),
            "HTTP_PORT_NOT_AUTHORIZED",
        )
        self.root = root
        self.port = port
        self.client = httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            trust_env=False,
            follow_redirects=False,
            headers={"Authorization": "Bearer " + token} if token else {},
            timeout=30,
        )

    def request(
        self,
        key: str,
        method: str,
        path: str,
        payload: Any = None,
        *,
        timeout: float = 30,
        maximum: int = 10_000_000,
    ) -> dict[str, Any]:
        require(
            path.startswith("/") and not path.startswith("//") and "://" not in path,
            "HTTP_PATH_NOT_LOCAL",
        )
        require(method in ("GET", "POST"), "HTTP_METHOD_DENIED")
        if method == "POST":
            require(
                (self.port == 18080 and path in ("/api/cart", "/api/checkout"))
                or (
                    self.port == 18016
                    and path == "/ofrep/v1/evaluate/flags/loadGeneratorVUs"
                )
                or (
                    self.port == 18001
                    and (
                        path in ("/v1/environments", "/v1/incidents")
                        or re.fullmatch(
                            r"/v1/environments/env-[a-f0-9]{24}/(?:verify-jobs|baseline-jobs)",
                            path,
                        )
                        is not None
                        or re.fullmatch(
                            r"/v1/incidents/inc-[a-f0-9]{24}/diagnosis-jobs", path
                        )
                        is not None
                    )
                ),
                "HTTP_WRITE_ROUTE_DENIED",
            )
        seal(
            self.root,
            f"{key}-intent.json",
            {
                "method": method,
                "port": self.port,
                "path": path,
                "payload": payload,
                **now(),
            },
        )
        result: dict[str, Any]
        try:
            with self.client.stream(
                method, path, json=payload, timeout=timeout
            ) as response:
                chunks = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    require(size <= maximum, "HTTP_RESPONSE_BOUNDS")
                    chunks.append(chunk)
                content = b"".join(chunks)
                text = content.decode("utf-8")
                try:
                    body = json.loads(text)
                except ValueError:
                    body = None
                result = {
                    "status": response.status_code,
                    "body": body,
                    "text": text,
                    "bytes": size,
                    "retries": 0,
                    **now(),
                }
        except (httpx.HTTPError, UnicodeDecodeError, Failure) as error:
            result = {
                "status": None,
                "error_type": type(error).__name__,
                "retries": 0,
                **now(),
            }
        seal(self.root, f"{key}-response.json", result)
        return result

    def json(self, key: str, method: str, path: str, payload: Any = None) -> Any:
        response = self.request(key, method, path, payload)
        require(
            response["status"] is not None
            and 200 <= response["status"] < 300
            and response["body"] is not None,
            "HTTP_REQUEST_FAILED:" + key,
        )
        return response["body"]
