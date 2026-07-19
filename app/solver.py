"""
Low-memory version for Render Free (512MB)
"""

import asyncio
import json
import logging
import os
import random
import time
from typing import Optional
from urllib.parse import urlparse

import aiohttp
from camoufox import DefaultAddons
from camoufox.async_api import AsyncCamoufox

log = logging.getLogger("solver")

def _step(req_id: str, msg: str):
    print(f"  [{req_id}] {msg}", flush=True)

def _get_profile_dir() -> str:
    return "/tmp/ts_profile"

def _solver_proxy() -> Optional[str]:
    return (os.environ.get("SOLVER_PROXY") or "").strip() or None

def _headless_mode():
    return True  # force headless for lower memory

class BrowserSingleton:
    def __init__(self, max_concurrent: int = 1):
        self._camoufox: Optional[AsyncCamoufox] = None
        self.browser = None
        self.sem = asyncio.Semaphore(1)
        self.solve_lock = asyncio.Lock()
        self.max_concurrent = 1
        self._start_lock = asyncio.Lock()
        self.solve_count = 0
        self.stopped = False

    def _is_alive(self) -> bool:
        if self.browser is None or self.stopped:
            return False
        try:
            b = getattr(self.browser, "browser", None)
            if b is not None and hasattr(b, "is_connected"):
                return bool(b.is_connected())
            return True
        except Exception:
            return False

    async def ensure(self):
        async with self._start_lock:
            if self._is_alive():
                return
            if self.browser is not None or self._camoufox is not None:
                try:
                    if self._camoufox is not None:
                        await self._camoufox.__aexit__(None, None, None)
                except Exception:
                    pass
                self._camoufox = None
                self.browser = None

            profile = _get_profile_dir()
            os.makedirs(profile, exist_ok=True)

            # Aggressive low-memory environment
            os.environ["MOZ_DISABLE_CONTENT_SANDBOX"] = "1"
            os.environ["MOZ_DISABLE_GMP_SANDBOX"] = "1"
            os.environ["MOZ_DISABLE_RDD_SANDBOX"] = "1"
            os.environ["MOZ_DISABLE_SOCKET_PROCESS_SANDBOX"] = "1"
            os.environ["MOZ_DISABLE_NPAPI_SANDBOX"] = "1"
            os.environ["MOZ_WEBRENDER"] = "0"
            os.environ["MOZ_ACCELERATED"] = "0"

            log.info("launching camoufox (low-memory mode)")

            kwargs = dict(
                headless=True,
                humanize=False,                    # disable humanize → less CPU/RAM
                persistent_context=True,
                user_data_dir=profile,
                os=["linux"],
                locale="en-US",
                exclude_addons=[DefaultAddons.UBO],
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--disable-extensions",
                    "--disable-background-networking",
                    "--disable-default-apps",
                    "--disable-sync",
                    "--disable-translate",
                    "--hide-scrollbars",
                    "--metrics-recording-only",
                    "--mute-audio",
                    "--no-first-run",
                    "--safebrowsing-disable-auto-update",
                    "--disable-features=TranslateUI,BlinkGenPropertyTrees",
                    "--single-process",            # important for low RAM
                    "--renderer-process-limit=1",
                    "--js-flags=--max-old-space-size=128",
                ],
            )

            proxy = _solver_proxy()
            if proxy:
                kwargs["proxy"] = {"server": proxy}
                kwargs["geoip"] = True

            self._camoufox = AsyncCamoufox(**kwargs)
            self.browser = await self._camoufox.__aenter__()
            self.stopped = False
            log.info("camoufox ready (low-memory)")

    async def new_page(self, url: str = ""):
        await self.ensure()
        page = await self.browser.new_page()
        if url:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            except Exception as e:
                log.warning("initial goto failed: %s", e)
        return page

    async def shutdown(self):
        if self.stopped:
            return
        self.stopped = True
        if self._camoufox is not None:
            try:
                await self._camoufox.__aexit__(None, None, None)
            except Exception:
                pass
        self._camoufox = None
        self.browser = None


_pool: Optional[BrowserSingleton] = None
_pool_lock: Optional[asyncio.Lock] = None


async def get_pool(size: Optional[int] = None) -> BrowserSingleton:
    global _pool, _pool_lock
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()
    async with _pool_lock:
        if _pool is None:
            _pool = BrowserSingleton(1)
        return _pool


_HOST_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js?onload=onloadTurnstileCallback" async defer></script>
</head>
<body>
__WIDGET__
</body>
</html>"""


async def _turnstile_on_page(page, sitekey: str, siteurl: str, req_id: str,
                              timeout: int, action: Optional[str] = None,
                              cdata: Optional[str] = None) -> str:
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    target = siteurl if siteurl.endswith("/") else siteurl + "/"

    widget_div = f'<div class="cf-turnstile" data-sitekey="{sitekey}"></div>'
    body = _HOST_HTML.replace("__WIDGET__", widget_div)

    try:
        await page.unroute_all()
    except Exception:
        pass

    await page.route(target, lambda route: route.fulfill(body=body, status=200))
    await page.goto(target)
    _step(req_id, f"route intercepted {target}")

    try:
        await page.eval_on_selector("//div[@class='cf-turnstile']", "el => el.style.width = '70px'")
    except Exception:
        pass

    deadline = t0 + timeout
    while loop.time() < deadline:
        try:
            value = await page.input_value("[name=cf-turnstile-response]", timeout=500)
            if value:
                _step(req_id, f"token obtained ({loop.time() - t0:.1f}s)")
                return value
        except Exception:
            pass

        try:
            await page.locator("//div[@class='cf-turnstile']").click(timeout=500)
        except Exception:
            pass

        await asyncio.sleep(0.4)

    raise TimeoutError(f"turnstile timeout after {timeout}s")


_DEAD_BROWSER_HINTS = (
    "connection closed", "browser has been closed",
    "target page, context or browser has been closed",
    "browser context has been closed",
)

def _is_dead_browser_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(h in msg for h in _DEAD_BROWSER_HINTS)


async def solve_async(sitekey: str, siteurl: str, req_id: str = "-",
                      timeout: int = 90, action: Optional[str] = None,
                      cdata: Optional[str] = None) -> str:
    pool = await get_pool()
    async with pool.sem:
        async with pool.solve_lock:
            _step(req_id, f"opening tab for {siteurl}")
            for attempt in (1, 2):
                page = None
                try:
                    await pool.ensure()
                    page = await pool.browser.new_page()
                    return await _turnstile_on_page(
                        page, sitekey, siteurl, req_id, timeout, action, cdata
                    )
                except Exception as exc:
                    if attempt == 1 and _is_dead_browser_error(exc):
                        _step(req_id, f"browser dead, relaunching")
                        await pool.shutdown()
                        continue
                    raise
                finally:
                    pool.solve_count += 1
                    if page is not None:
                        try:
                            await page.close()
                        except Exception:
                            pass
            raise RuntimeError("solve_async failed")


# Keep the other functions so imports don't break
async def solve_challenge_async(siteurl: str, req_id: str = "-", timeout: int = 45) -> dict:
    raise NotImplementedError("solve_challenge disabled in low-memory mode")

async def solve_recaptcha_v3_async(*args, **kwargs):
    raise NotImplementedError("recaptcha disabled in low-memory mode")

async def solve_aws_token_async(*args, **kwargs):
    raise NotImplementedError("aws-token disabled in low-memory mode")

def _challenge_proxy():
    return None, ""
