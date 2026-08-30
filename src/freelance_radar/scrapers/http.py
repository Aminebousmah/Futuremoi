"""Client HTTP poli : rate-limit par domaine, robots.txt, cache disque, retries.

Toutes les sources passent par ici. C'est le seul endroit du projet qui parle
au reseau, ce qui garantit que les regles de politesse sont appliquees partout.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.robotparser as robotparser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import Config, user_agent

log = logging.getLogger(__name__)


class RobotsDisallowed(RuntimeError):
    """Levee quand robots.txt interdit l'URL demandee."""


class PoliteClient:
    """Wrapper httpx applique a une campagne de scraping."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.ua = user_agent()
        self._last_call: dict[str, float] = {}
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self._cache_dir = cfg.cache_path
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.Client(
            timeout=cfg.http.timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": self.ua,
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            },
        )

    # ------------------------------------------------------------------ #
    #  Politesse
    # ------------------------------------------------------------------ #
    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        delay = self.cfg.http.delay_seconds
        elapsed = time.monotonic() - self._last_call.get(host, 0.0)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_call[host] = time.monotonic()

    def _robots_allows(self, url: str, params: dict[str, Any] | None = None) -> bool:
        if not self.cfg.http.respect_robots_txt:
            return True
        # Les directives peuvent cibler la query (ex. `Disallow: /*search=`) :
        # on teste donc l'URL complete, parametres compris.
        if params:
            url = str(httpx.URL(url).copy_merge_params(params))
        parts = urlparse(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self._robots:
            rp = robotparser.RobotFileParser()
            rp.set_url(f"{host}/robots.txt")
            try:
                resp = self._client.get(f"{host}/robots.txt")
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    rp = None  # pas de robots.txt => autorise
            except Exception as exc:  # reseau HS : on n'empeche pas la campagne
                log.debug("robots.txt illisible pour %s : %s", host, exc)
                rp = None
            self._robots[host] = rp
        rp = self._robots[host]
        return True if rp is None else rp.can_fetch(self.ua, url)

    # ------------------------------------------------------------------ #
    #  Cache disque
    # ------------------------------------------------------------------ #
    def _cache_file(self, url: str, params: dict | None) -> Path:
        seed = url + json.dumps(params or {}, sort_keys=True)
        return self._cache_dir / f"{hashlib.sha1(seed.encode()).hexdigest()}.cache"

    def _cache_read(self, path: Path) -> str | None:
        ttl = self.cfg.http.cache_ttl_minutes
        if ttl <= 0 or not path.exists():
            return None
        if time.time() - path.stat().st_mtime > ttl * 60:
            return None
        return path.read_text(encoding="utf-8", errors="replace")

    def _cache_write(self, path: Path, body: str) -> None:
        if self.cfg.http.cache_ttl_minutes > 0:
            path.write_text(body, encoding="utf-8", errors="replace")

    # ------------------------------------------------------------------ #
    #  Requetes
    # ------------------------------------------------------------------ #
    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        use_cache: bool = True,
        check_robots: bool = True,
    ) -> str:
        """GET poli.

        `check_robots=False` est reserve aux API : robots.txt regit l'exploration
        d'un site web, pas l'appel d'une API documentee que l'on est autorise a
        consommer. Les hotes d'API servent d'ailleurs souvent un `Disallow: /`
        global (rien a indexer), qui bloquerait a tort un client legitime.
        """
        cache_file = self._cache_file(url, params)
        if use_cache:
            cached = self._cache_read(cache_file)
            if cached is not None:
                log.debug("cache hit %s", url)
                return cached

        if check_robots and not self._robots_allows(url, params):
            raise RobotsDisallowed(f"robots.txt interdit l'acces a {url}")

        last_error: Exception | None = None
        for attempt in range(1, self.cfg.http.max_retries + 1):
            self._throttle(url)
            try:
                resp = self._client.get(url, params=params, headers=headers)
                if resp.status_code == 429 or resp.status_code >= 500:
                    wait = min(2 ** attempt, 30)
                    log.warning("HTTP %s sur %s, nouvelle tentative dans %ss",
                                resp.status_code, url, wait)
                    time.sleep(wait)
                    last_error = httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}", request=resp.request, response=resp
                    )
                    continue
                resp.raise_for_status()
                body = resp.text
                if use_cache:
                    self._cache_write(cache_file, body)
                return body
            except httpx.HTTPStatusError as exc:
                # 4xx (hors 429) : inutile de reessayer
                if exc.response is not None and 400 <= exc.response.status_code < 500:
                    raise
                last_error = exc
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(min(2 ** attempt, 15))

        raise RuntimeError(
            f"Echec de {url} apres {self.cfg.http.max_retries} tentatives"
        ) from last_error

    def get_json(self, url: str, params: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None,
                 check_robots: bool = True) -> Any:
        return json.loads(
            self.get(url, params=params, headers=headers, check_robots=check_robots)
        )

    def post_form(self, url: str, data: dict[str, Any],
                  headers: dict[str, str] | None = None) -> Any:
        """POST form-urlencoded, utilise uniquement pour les flux OAuth."""
        self._throttle(url)
        resp = self._client.post(url, data=data, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
