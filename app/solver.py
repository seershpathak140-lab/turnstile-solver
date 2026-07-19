import asyncio
import logging
import os
import traceback
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
                print("[solver] browser already running", flush=True)
                return

            print("[solver] ===== STARTING BROWSER LAUNCH =====", flush=True)

            try:
                os.environ["MOZ_DISABLE_CONTENT_SANDBOX"] = "1"
                os.environ["MOZ_DISABLE_GMP_SANDBOX"] = "1"
                os.environ["MOZ_DISABLE_RDD_SANDBOX"] = "1"
                os.environ["MOZ_DISABLE_SOCKET_PROCESS_SANDBOX"] = "1"
                os.environ["MOZ_WEBRENDER"] = "0"

                print("[solver] environment variables set", flush=True)

                print("[solver] creating AsyncCamoufox instance...", flush=True)
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
                        "--js-flags=--max-old-space-size=96",
                    ]
                )
                print("[solver] AsyncCamoufox instance created", flush=True)

                print("[solver] calling __aenter__ (this is where it often crashes)...", flush=True)
                self.browser = await self._camoufox.__aenter__()
                print("[solver] ===== BROWSER LAUNCHED SUCCESSFULLY =====", flush=True)

            except Exception as e:
                print("[solver] !!!!! BROWSER LAUNCH FAILED !!!!!", flush=True)
                print(f"[solver] Error type: {type(e).__name__}", flush=True)
                print(f"[solver] Error message: {e}", flush=True)
                print("[solver] Full traceback:", flush=True)
                traceback.print_exc()
                self._camoufox = None
                self.browser = None
                raise

    async def shutdown(self):
        print("[solver] shutting down browser...", flush=True)
        if self._camoufox:
            try:
                await self._camoufox.__aexit__(None, None, None)
            except Exception as e:
                print(f"[solver] error during shutdown: {e}", flush=True)
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
    print(f"[{req_id}] ===== solve_async STARTED =====", flush=True)
    print(f"[{req_id}] sitekey = {sitekey}", flush=True)
    print(f"[{req_id}] siteurl = {siteurl}", flush=True)
    print(f"[{req_id}] timeout = {timeout}", flush=True)

    try:
        pool = await get_pool()
        print(f"[{req_id}] got pool", flush=True)

        async with pool.lock:
            print(f"[{req_id}] acquired lock, calling ensure()...", flush=True)
            await pool.ensure()
            print(f"[{req_id}] ensure() finished", flush=True)

            print(f"[{req_id}] creating new page...", flush=True)
            page = await pool.browser.new_page()
            print(f"[{req_id}] page created", flush=True)

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
                print(f"[{req_id}] setting up route for {target}", flush=True)

                await page.route(target, lambda route: route.fulfill(body=html, status=200))
                print(f"[{req_id}] route set, going to page...", flush=True)

                await page.goto(target, timeout=30000)
                print(f"[{req_id}] page loaded successfully", flush=True)

                print(f"[{req_id}] starting token polling (max {timeout}s)...", flush=True)

                for i in range(timeout * 2):
                    try:
                        token = await page.input_value("[name=cf-turnstile-response]", timeout=400)
                        if token:
                            print(f"[{req_id}] ===== TOKEN FOUND =====", flush=True)
                            print(f"[{req_id}] Token length: {len(token)}", flush=True)
                            print(f"[{req_id}] Token: {token}", flush=True)
                            return token
                    except Exception:
                        pass

                    try:
                        await page.click(".cf-turnstile", timeout=400)
                    except Exception:
                        pass

                    if i % 10 == 0:
                        print(f"[{req_id}] still waiting for token... ({i//2}s)", flush=True)

                    await asyncio.sleep(0.5)

                print(f"[{req_id}] ===== TIMEOUT - NO TOKEN =====", flush=True)
                raise TimeoutError("Turnstile timeout")

            finally:
                try:
                    await page.close()
                    print(f"[{req_id}] page closed", flush=True)
                except Exception as e:
                    print(f"[{req_id}] error closing page: {e}", flush=True)

    except Exception as e:
        print(f"[{req_id}] !!!!! solve_async FAILED !!!!!", flush=True)
        print(f"[{req_id}] Error type: {type(e).__name__}", flush=True)
        print(f"[{req_id}] Error message: {e}", flush=True)
        print(f"[{req_id}] Full traceback:", flush=True)
        traceback.print_exc()
        raise
