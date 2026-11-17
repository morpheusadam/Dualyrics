"""HTTP sessions that honour the proxy setting (none / system / custom) with retries and a clear User-Agent."""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from dualyrics import APP_NAME, REPO_URL, __version__

USER_AGENT = f"{APP_NAME}/{__version__}" + (f" (+{REPO_URL})" if REPO_URL else "")

# (connect, read) timeouts in seconds
TIMEOUT_FAST = (6, 15)
TIMEOUT_SLOW = (8, 75)


def make_session(proxy_mode: str, proxy_url: str = "") -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    retry = Retry(total=2, connect=2, read=1, backoff_factor=0.6, status_forcelist=(502, 503, 504),
                  allowed_methods=frozenset({"GET"}), raise_on_status=False)
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=8)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    if proxy_mode == "none":
        s.trust_env = False
        s.proxies = {}
    elif proxy_mode == "custom" and proxy_url:
        s.trust_env = False
        s.proxies = {"http": proxy_url, "https": proxy_url}
    else:  # "system": environment variables and the Windows Internet Options proxy
        s.trust_env = True
    return s


def proxy_of(session: requests.Session, scheme: str = "https") -> str:
    """The proxy URL a session would use for ``scheme`` ('' = direct)."""
    if session.trust_env:
        import urllib.request

        return urllib.request.getproxies().get(scheme, "") or ""
    return (session.proxies or {}).get(scheme, "") or ""


def ws_options(session: requests.Session) -> dict[str, object]:
    """websocket-client keyword arguments that send a WebSocket through the same proxy as ``session``
    (dubbing: Gemini Live and Edge speech)."""
    from urllib.parse import unquote, urlsplit

    url = proxy_of(session)
    if not url:
        return {}
    u = urlsplit(url if "://" in url else f"http://{url}")
    if not u.hostname:
        return {}
    kind = (u.scheme or "http").lower()
    opts: dict[str, object] = {"http_proxy_host": u.hostname,
                               "http_proxy_port": u.port or (1080 if kind.startswith("socks") else 8080),
                               "proxy_type": kind if kind in ("socks4", "socks4a", "socks5", "socks5h") else "http"}
    if u.username:
        opts["http_proxy_auth"] = (unquote(u.username), unquote(u.password or ""))
    return opts


def is_network_error(exc: BaseException) -> bool:
    """Transient failure (connection, timeout, broken transfer, server overload): retry later, never cache."""
    if isinstance(exc, (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError)):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return False
