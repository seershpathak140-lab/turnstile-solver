import asyncio
import logging
import os
from typing import Optional
from camoufox.async_api import AsyncCamoufox

log = logging.getLogger("solver")

_pool = None
_pool_lock = None

class BrowserPool:
    def __init__(self):
        self._camoufox = None
        self.browser = None
        self.lock = asyncio.Lock()
        self.start_lock = asyncio.Lock()

    async def ensure(self):
        async with self.start_lock:
            if self.browser is not None:
                return

            os.environ["MOZ_DISABLE_CONTENT_SANDBOX"] = "1"
            os.environ["MOZ_DISABLE_GMP_SANDBOX"] = "1"
            os.environ["MOZ_DISABLE_RDD_SANDBOX"] = "1"
            os.environ["MOZ_DISABLE_SOCKET_PROCESS_SANDBOX"] = "1"

            print("[solver] launching camoufox (minimal)", flush=True)

            self._camoufox = AsyncCamoufox(
                headless=True,
                humanize=False,
                persistent_context=True,
                user_data_dir="/tmp/ts_profile",
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--single-process",
                    "--renderer-process-limit=1",
                    "--js-flags=--max-old-space-size=128",
                ]
            )
            self.browser = await self._camoufox.__aenter__()
            print("[solver] camoufox ready", flush=True)

    async def shutdown(self):
        if self._camoufox:
            try:
                await self._camoufox.__aexit__(None, None, None)
            except Exception:
                pass
        self._camoufox = None
        self.browser = None


async def get_pool():
    global _pool, _pool_lock
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()
    async with _pool_lock:
        if _pool is None:
            _pool = BrowserPool()
        return _pool


async def solve_async(sitekey: str, siteurl: str, timeout: int = 120, req_id: str = "-"):
    pool = await get_pool()
    async with pool.lock:
        await pool.ensure()

        page = await pool.browser.new_page()
        try:
            html = f"""
            <html>
            <head>
                <script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
            </head>
            <body>
                <div class="cf-turnstile" data-sitekey="{sitekey}"></div>
            </body>
            </html>
            """

            target = siteurl if siteurl.endswith("/") else siteurl + "/"
            await page.route(target, lambda route: route.fulfill(body=html, status=200))
            await page.goto(target)

            print(f"[{req_id}] page loaded, waiting for token...", flush=True)

            for i in range(timeout * 2):
                try:
                    token = await page.input_value("[name=cf-turnstile-response]", timeout=400)
                    if token:
                        print(f"[{req_id}] token obtained", flush=True)
                        return token
                except Exception:
                    pass

                try:
                    await page.click(".cf-turnstile", timeout=400)
                except Exception:
                    pass

                await asyncio.sleep(0.5)

            raise TimeoutError("Turnstile timeout")
        finally:
            try:
                await page.close()
            except Exception:
                pass
