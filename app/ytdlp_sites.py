from __future__ import annotations

import re

YTDLP_PREFERRED_BASE_HOSTS = {
    "drtuber.com",
    "empflix.com",
    "eporner.com",
    "moviefap.com",
    "pornhub.com",
    "pornhub.net",
    "pornhub.org",
    "pornhubpremium.com",
    "pornhubpremium.net",
    "pornhubpremium.org",
    "redtube.com",
    "redtube.com.br",
    "spankbang.com",
    "thisvid.com",
    "tnaflix.com",
    "tube8.com",
    "xhday.com",
    "xhms.pro",
    "xhamster.com",
    "xhamster.desi",
    "xhamster.one",
    "xhvid.com",
    "xnxx.com",
    "xnxx3.com",
    "xvideos.com",
    "xvideos2.com",
    "youjizz.com",
    "youporn.com",
}
YTDLP_PREFERRED_HOST_PATTERNS = (
    re.compile(r"^xhamster\d*\.(?:com|desi)$"),
)


def is_ytdlp_preferred_host(host: str) -> bool:
    host = host.lower().strip(".").removeprefix("www.")
    while host:
        if host in YTDLP_PREFERRED_BASE_HOSTS:
            return True
        if any(pattern.match(host) for pattern in YTDLP_PREFERRED_HOST_PATTERNS):
            return True
        _subdomain, separator, remainder = host.partition(".")
        if not separator:
            return False
        host = remainder
    return False
