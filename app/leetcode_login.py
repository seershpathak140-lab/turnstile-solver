import aiohttp
from aiohttp import web
import logging
import os

log = logging.getLogger("leetcode")

LEETCODE_LOGIN_URL = "https://leetcode.com/accounts/login/"
SITEKEY = "0x4AAAAAAAQrSHUTor4iGTpW"

# Use the same PORT the service is listening on
PORT = int(os.environ.get("PORT", 10000))


async def handle_leetcode_login(request: web.Request) -> web.Response:
    """
    POST /leetcode-login
    Body: { "username": "...", "password": "..." }
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    if not username or not password:
        return web.json_response({"error": "username and password required"}, status=400)

    log.info("=== LeetCode Login Started ===")
    log.info(f"Username: {username}")

    # 1. Get Turnstile token from our own solver
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{PORT}/solve",
                json={
                    "sitekey": SITEKEY,
                    "siteurl": LEETCODE_LOGIN_URL,
                    "timeout": 90
                },
                timeout=aiohttp.ClientTimeout(total=120)
            ) as resp:
                solve_data = await resp.json()
    except Exception as e:
        log.exception("Failed to get Turnstile token")
        return web.json_response({"error": f"solve failed: {str(e)}"}, status=500)

    token = solve_data.get("token")
    if not token:
        log.error(f"No token returned: {solve_data}")
        return web.json_response({"error": "no token returned", "detail": solve_data}, status=500)

    log.info(f"cf_turnstile_token received: {token[:40]}...{token[-20:]}")
    log.info(f"Full token length: {len(token)}")

    # Note: LeetCode does NOT use csrfmiddlewaretoken
    # (that field was from a different website)
    csrf = None
    log.info(f"csrfmiddlewaretoken: {csrf} (not used by LeetCode)")

    # 2. Attempt login
    try:
        async with aiohttp.ClientSession() as session:
            # First visit the page to get cookies
            await session.get(LEETCODE_LOGIN_URL)

            form = {
                "username": username,
                "password": password,
                "cf-turnstile-response": token,
            }

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": LEETCODE_LOGIN_URL,
                "Origin": "https://leetcode.com",
            }

            log.info("=== Final request being sent to LeetCode ===")
            log.info(f"URL: {LEETCODE_LOGIN_URL}")
            log.info(f"Form data: {form}")
            log.info(f"Headers: {headers}")

            async with session.post(
                LEETCODE_LOGIN_URL,
                data=form,
                headers=headers,
                allow_redirects=True
            ) as login_resp:
                text = await login_resp.text()
                final_url = str(login_resp.url)
                status = login_resp.status

                log.info(f"LeetCode response status: {status}")
                log.info(f"Final URL: {final_url}")

                # Basic success check
                if "logout" in text.lower() or "sign out" in text.lower() or "/profile" in final_url:
                    log.info("✅ LOGIN SUCCESS")
                    return web.json_response({
                        "status": "success",
                        "message": "Logged in successfully",
                        "final_url": final_url,
                        "cf_turnstile_token": token[:50] + "...",
                        "csrfmiddlewaretoken": csrf
                    })
                else:
                    log.warning("❌ LOGIN FAILED")
                    return web.json_response({
                        "status": "failed",
                        "message": "Login failed or extra challenge required",
                        "status_code": status,
                        "final_url": final_url,
                        "cf_turnstile_token": token[:50] + "...",
                        "csrfmiddlewaretoken": csrf
                    }, status=400)

    except Exception as e:
        log.exception("Login request failed")
        return web.json_response({"error": str(e)}, status=500)
