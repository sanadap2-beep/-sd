"""حارس SSRF مركزي — يمنع جلب عناوين داخلية من روابط المزودين/الموردين."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class SsrfError(ValueError):
    pass


_BLOCKED_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _ip_blocked(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        return True
    for net in _BLOCKED_NETS:
        try:
            if ip in net:
                return True
        except TypeError:
            continue
    return False


def validate_url(url: str, *, allow_http: bool = False, max_length: int = 2000) -> str:
    """يفحص URL ويرفع SsrfError عند أي خطر. يرجع URL بعد التنظيف."""
    url = (url or "").strip()
    if not url or len(url) > max_length:
        raise SsrfError("رابط غير صالح")
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise SsrfError("رابط غير صالح") from exc
    scheme = (parsed.scheme or "").lower()
    allowed = ("https", "http") if allow_http else ("https",)
    if scheme not in allowed:
        raise SsrfError("يُسمح بـ HTTPS فقط" if not allow_http else "بروتوكول غير مسموح")
    host = (parsed.hostname or "").strip().lower()
    if not host or host in {"localhost"} or host.endswith(".internal") or host.endswith(".local"):
        raise SsrfError("مضيف داخلي محظور")
    # حظر IP حرفية داخلية مباشرة
    try:
        ipaddress.ip_address(host)
        if _ip_blocked(host):
            raise SsrfError("عنوان IP داخلي محظور")
        return url
    except ValueError:
        pass
    # حل DNS وفحص كل عنوان
    try:
        infos = socket.getaddrinfo(host, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
    except Exception as exc:
        raise SsrfError("تعذر حل المضيف") from exc
    if not infos:
        raise SsrfError("تعذر حل المضيف")
    for info in infos:
        sockaddr = info[4]
        if sockaddr and _ip_blocked(str(sockaddr[0])):
            raise SsrfError("المضيف يحل لعنوان داخلي محظور")
    return url
