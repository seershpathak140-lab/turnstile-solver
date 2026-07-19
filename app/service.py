import logging
import os
import sys
from aiohttp import web
from .leetcode_login import handle_leetcode_login

PORT = int(os.environ.get("PORT", 10000))

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s %(message)s"
)

async def handle_health(request):
    return web.json_response({"status": "ok"})

def main():
    app = web.Application()
    app.router.add_get("/health", handle_health)
    app.router.add_post("/leetcode-login", handle_leetcode_login)

    print(f"[solver] listening on http://0.0.0.0:{PORT}", flush=True)
    web.run_app(app, host="0.0.0.0", port=PORT, print=None, access_log=None)

if __name__ == "__main__":
    main()
