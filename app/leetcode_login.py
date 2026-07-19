import aiohttp
from aiohttp import web
import logging

log = logging.getLogger("leetcode")

LEETCODE_LOGIN_URL = "https://leetcode.com/accounts/login/"
SITEKEY = "0x4AAAAAAAQrSHUTor4iGTpW"

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

    # 1. Get Turnstile token from our own solver
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "http://127.0.0.1:9988/solve",   # internal call
                json={
                    "sitekey": SITEKEY,
                    "siteurl": LEETCODE_LOGIN_URL,
                    "timeout": 60
                },
                timeout=aiohttp.ClientTimeout(total=90)
            ) as resp:
                solve_data = await resp.json()
    except Exception as e:
        log.exception("solve failed")
        return web.json_response({"error": f"solve failed: {str(e)}"}, status=500)

    token = solve_data.get("token")
    if not token:
        return web.json_response({"error": "no token returned", "detail": solve_data}, status=500)

    # 2. Attempt login
    try:
        async with aiohttp.ClientSession() as session:
            # first get cookies
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

            async with session.post(
                LEETCODE_LOGIN_URL,
                data=form,
                headers=headers,
                allow_redirects=True
            ) as login_resp:
                text = await login_resp.text()
                final_url = str(login_resp.url)

                # very basic success check
                if "logout" in text.lower() or "sign out" in text.lower() or "/profile" in final_url:
                    return web.json_response({
                        "status": "success",
                        "message": "Logged in successfully",
                        "final_url": final_url
                    })
                else:
                    return web.json_response({
                        "status": "failed",
                        "message": "Login failed or extra challenge required",
                        "status_code": login_resp.status,
                        "final_url": final_url
                    }, status=400)

    except Exception as e:
        log.exception("login request failed")
        return web.json_response({"error": str(e)}, status=500)
