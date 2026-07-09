"""Shared SSRF guard for outbound fetches of user-supplied URLs.

Every place the server fetches a URL the caller controls (media links, hero
photos, sharpness checks, webhooks) should gate on `url_is_public` first so an
attacker can't make the backend request internal/cloud-metadata addresses.

Limitation: this validates the INITIAL URL only. Callers that follow redirects
can still be pointed at an internal host by a redirect (a separate TOCTOU gap);
prefer follow_redirects=False, or pin the validated IP, where feasible.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse


async def url_is_public(url: str, *, allow_http: bool = False) -> bool:
    """True only if `url` is https (or http when allow_http) and every resolved
    IP is a public address — no private/loopback/link-local/reserved/multicast/
    unspecified (blocks 169.254.169.254 cloud metadata, 127.0.0.1, 10/8, etc.)."""
    try:
        u = urlparse(url)
    except ValueError:
        return False
    schemes = ("https", "http") if allow_http else ("https",)
    if u.scheme not in schemes or not u.hostname:
        return False
    try:
        # u.port is lazily parsed and raises ValueError for an out-of-range /
        # non-numeric port — keep it inside the guard so we return False, not raise.
        port = u.port or (443 if u.scheme == "https" else 80)
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(u.hostname, port, type=socket.SOCK_STREAM)
    except (OSError, ValueError):
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True
