# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
import json
import urllib.error
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class NetworkRoute:
    name: str
    proxies: dict[str, str]

    @property
    def key(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self.proxies.items()))


class NetworkConnectionError(RuntimeError):
    def __init__(self, failures: list[str]):
        self.failures = failures
        super().__init__("所有网络通道都无法连接微软接口：\n" + "\n".join(failures))


class HttpResponseError(RuntimeError):
    def __init__(self, status: int, payload: dict, route_name: str):
        self.status = status
        self.payload = payload
        self.route_name = route_name
        message = payload.get("error_description") or payload.get("error") or json.dumps(payload, ensure_ascii=False)
        super().__init__(message)


def normalize_proxy(proxy: str | None) -> str:
    proxy = (proxy or "").strip()
    if not proxy:
        return ""
    if "://" not in proxy:
        proxy = "http://" + proxy
    return proxy


def proxy_map_from_server(proxy_server: str | None) -> dict[str, str]:
    proxy_server = (proxy_server or "").strip()
    if not proxy_server:
        return {}
    if "=" not in proxy_server:
        proxy = normalize_proxy(proxy_server)
        return {"http": proxy, "https": proxy}

    result: dict[str, str] = {}
    for item in proxy_server.split(";"):
        if "=" not in item:
            continue
        scheme, value = item.split("=", 1)
        scheme = scheme.strip().lower()
        if scheme in ("http", "https"):
            result[scheme] = normalize_proxy(value)
    return result


def windows_proxy_map() -> dict[str, str]:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            proxy_enable = winreg.QueryValueEx(key, "ProxyEnable")[0]
            proxy_server = winreg.QueryValueEx(key, "ProxyServer")[0]
    except Exception:
        return {}
    if not proxy_enable:
        return {}
    return proxy_map_from_server(proxy_server)


def discover_routes() -> list[NetworkRoute]:
    routes = [NetworkRoute("直连", {})]
    seen = {routes[0].key}

    env_proxies = urllib.request.getproxies()
    if env_proxies:
        route = NetworkRoute(f"Python 环境代理 {env_proxies}", env_proxies)
        if route.key not in seen:
            routes.append(route)
            seen.add(route.key)

    win_proxies = windows_proxy_map()
    if win_proxies:
        route = NetworkRoute(f"Windows 系统代理 {win_proxies}", win_proxies)
        if route.key not in seen:
            routes.append(route)
            seen.add(route.key)

    return routes


class NetworkClient:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.routes = discover_routes()
        self.preferred_key: tuple[tuple[str, str], ...] | None = None
        self.last_route_name = ""

    def refresh_routes(self) -> None:
        self.routes = discover_routes()
        self.preferred_key = None
        self.last_route_name = ""

    def ordered_routes(self) -> list[NetworkRoute]:
        if not self.preferred_key:
            return list(self.routes)
        preferred = [route for route in self.routes if route.key == self.preferred_key]
        rest = [route for route in self.routes if route.key != self.preferred_key]
        return preferred + rest

    def post_form(self, url: str, form: dict[str, str], timeout: int | None = None) -> dict:
        data = urllib.parse.urlencode(form).encode("utf-8")
        failures: list[str] = []

        for route in self.ordered_routes():
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler(route.proxies))
            try:
                with opener.open(req, timeout=timeout or self.timeout) as resp:
                    self.preferred_key = route.key
                    self.last_route_name = route.name
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                self.preferred_key = route.key
                self.last_route_name = route.name
                body = exc.read().decode("utf-8", errors="replace")
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    payload = {"error": "http_error", "error_description": body}
                raise HttpResponseError(exc.code, payload, route.name) from exc
            except Exception as exc:
                failures.append(f"{route.name}: {exc}")

        self.last_route_name = "全部失败"
        raise NetworkConnectionError(failures)
