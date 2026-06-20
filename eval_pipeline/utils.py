"""Shared utilities for the eval pipeline — DNS-aware OpenAI client factory."""

from __future__ import annotations

import httpx
from openai import OpenAI

_DNS_OVERRIDE: dict[str, str] = {
    "api.tabcode.cc": "154.44.10.152",
}


class _PatchedTransport(httpx.HTTPTransport):
    def __init__(self, dns_map: dict[str, str], **kwargs):
        kwargs.pop("verify", None)
        super().__init__(verify=False, **kwargs)
        self._dns_map = dns_map

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host in self._dns_map:
            real_ip = self._dns_map[host]
            new_url = request.url.copy_with(host=real_ip)
            headers = httpx.Headers({**dict(request.headers), "host": host})
            request = httpx.Request(
                method=request.method,
                url=new_url,
                headers=headers,
                content=request.content,
                extensions=request.extensions,
            )
        return super().handle_request(request)


def make_openai_client(
    api_key: str,
    base_url: str | None,
    timeout: float = 180.0,
    dns_override: dict[str, str] | None = None,
) -> OpenAI:
    """Create an OpenAI client that bypasses local DNS hijacking."""
    override = _DNS_OVERRIDE if dns_override is None else dns_override
    if override:
        transport = _PatchedTransport(dns_map=override)
        http_client = httpx.Client(transport=transport, timeout=timeout)
        kwargs = {"api_key": api_key, "http_client": http_client, "timeout": timeout}
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAI(**kwargs)

    kwargs = {"api_key": api_key, "timeout": timeout}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)
