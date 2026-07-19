import logging
import aiohttp
from aiohttp import web
from .solver import solve_async

log = logging.getLogger("leetcode")

LEETCODE_LOGIN_URL = "https://leetcode.com/accounts/login/"
SITEKEY = "0x4AAAAAAAQrSHUTor4iGTpW"


async def handle_leetcode_login(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    if not username or not password:
        return web.json_response({"error": "username and password required"}, status=400)

    print("\n" + "="*60, flush=True)
    print("=== LeetCode Login Started ===", flush=True)
    print(f"Username: {username}", flush=True)
    print("="*60 + "\n", flush=True)

    log.info("=== LeetCode Login Started ===")
    log.info(f"Username: {username}")

    # 1. Get Turnstile token
    try:
        print("[*] Calling solve_async to get Turnstile token...", flush=True)
        token = await solve_async(
            sitekey=SITEKEY,
            siteurl=LEETCODE_LOGIN_URL,
            timeout=180,
            req_id="leetcode"
        )
    except Exception as e:
        print(f"[ERROR] Failed to get Turnstile token: {e}", flush=True)
        log.exception("Failed to get Turnstile token")
        return web.json_response({"error": f"solve failed: {str(e)}"}, status=500)

    if not token:
        print("[ERROR] No token returned", flush=True)
        return web.json_response({"error": "no token returned"}, status=500)

    # ===== LOGGING TOKEN =====
    print("\n" + "="*60, flush=True)
    print("cf_turnstile_token RECEIVED", flush=True)
    print(f"Length : {len(token)}", flush=True)
    print(f"Token  : {token}", flush=True)
    print("="*60 + "\n", flush=True)

    log.info(f"cf_turnstile_token received ({len(token)} chars)")
    log.info(f"FULL TOKEN: {token}")

    # LeetCode does not use csrfmiddlewaretoken
    csrf = None
    print(f"csrfmiddlewaretoken: {csrf}", flush=True)
    log.info(f"csrfmiddlewaretoken: {csrf}")

    # 2. Attempt login
    try:
        async with aiohttp.ClientSession() as session:
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

            # ===== LOGGING FINAL REQUEST =====
            print("\n" + "="*60, flush=True)
            print("=== FINAL REQUEST BEING SENT TO LEETCODE ===", flush=True)
            print(f"URL     : {LEETCODE_LOGIN_URL}", flush=True)
            print(f"Username: {username}", flush=True)
            print(f"Password: {password}", flush=True)
            print(f"cf_turnstile_token: {token[:80]}...", flush=True)
            print(f"csrfmiddlewaretoken: {csrf}", flush=True)
            print("="*60 + "\n", flush=True)

            log.info("=== FINAL REQUEST BEING SENT TO LEETCODE ===")
            log.info(f"URL: {LEETCODE_LOGIN_URL}")
            log.info(f"Form: {form}")

            async with session.post(
                LEETCODE_LOGIN_URL,
                data=form,
                headers=headers,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=60)
            ) as login_resp:
                text = await login_resp.text()
                final_url = str(login_resp.url)
                status = login_resp.status

                print(f"LeetCode Response Status: {status}", flush=True)
                print(f"Final URL: {final_url}", flush=True)
                log.info(f"LeetCode response status: {status}")
                log.info(f"Final URL: {final_url}")

                if "logout" in text.lower() or "sign out" in text.lower() or "/profile" in final_url:
                    print("✅ LOGIN SUCCESS", flush=True)
                    log.info("✅ LOGIN SUCCESS")
                    return web.json_response({
                        "status": "success",
                        "message": "Logged in successfully",
                        "final_url": final_url,
                        "cf_turnstile_token": token,
                        "csrfmiddlewaretoken": csrf
                    })
                else:
                    print("❌ LOGIN FAILED", flush=True)
                    log.warning("❌ LOGIN FAILED")
                    return web.json_response({
                        "status": "failed",
                        "message": "Login failed or extra challenge required",
                        "status_code": status,
                        "final_url": final_url,
                        "cf_turnstile_token": token,
                        "csrfmiddlewaretoken": csrf
                    }, status=400)

    except Exception as e:
        print(f"[ERROR] Login request failed: {e}", flush=True)
        log.exception("Login request failed")
        return web.json_response({"error": str(e)}, status=500)
