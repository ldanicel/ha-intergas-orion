#!/usr/bin/env python3
"""Read-only probe for the Intergas Orion (ARCH_2_0) cloud API.

Signs in and prints your heaters, zones and statistics as JSON, so you can
check what the API returns for your setup (useful when reporting issues).
Only GET requests are sent after signing in; nothing is changed.

Usage:
    python3 orion_probe.py EMAIL                 # prompts for the password
    INTERGAS_PASSWORD=... python3 orion_probe.py EMAIL
    python3 orion_probe.py EMAIL --energy-only   # heaters, statistics, weather

Ids, serial numbers, names and tokens are redacted by default so the output
is safe to paste into an issue. Use --no-redact to see everything.
"""
import argparse
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = "https://app-api.orion.intergas.nl"  # fixed https base, so urlopen schemes are safe

# The API rate-limits bursts of requests (HTTP 429), so space them out.
PAUSE = 1.5  # seconds

# Plus every key named "id" or ending in "_id" (heater, gateway, preset ids).
REDACT_KEYS = {
    "jwt", "refresh_token", "access_token", "serial_number", "email", "name",
    "mac", "mac_address",
}


def _decode(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return f"<non-JSON body, {len(raw)} bytes>"


def request(method, path, token=None, body=None):
    """Return (status, decoded body); error bodies are returned too."""
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(2):
        time.sleep(PAUSE)
        req = urllib.request.Request(BASE + path, data=data, method=method)  # noqa: S310
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        if token:
            req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
                return resp.status, _decode(resp.read().decode(errors="replace"))
        except urllib.error.HTTPError as err:
            if err.code == 429 and attempt == 0:
                try:
                    wait = min(int(err.headers.get("Retry-After", "")), 120)
                except ValueError:
                    wait = 30
                print(f"# rate limited, waiting {wait}s ...", file=sys.stderr)
                time.sleep(wait)
                continue
            if path == "/v1/accounts/platform_check":
                return err.code, None
            return err.code, _decode(err.read().decode(errors="replace"))
        except urllib.error.URLError as err:
            return 0, f"<request failed: {err.reason}>"
    return 429, None


def redact(value):
    if isinstance(value, dict):
        return {
            k: (
                "<redacted>"
                if v is not None and (k in REDACT_KEYS or k == "id" or k.endswith("_id"))
                else redact(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("email")
    parser.add_argument("--no-redact", action="store_true")
    parser.add_argument(
        "--energy-only", action="store_true",
        help="only print the heaters, statistics and weather (skip zones, presets, schedules)",
    )
    args = parser.parse_args()
    show = (lambda x: x) if args.no_redact else redact

    password = os.environ.get("INTERGAS_PASSWORD") or getpass.getpass("Password: ")

    status, data = request("POST", "/v1/accounts/platform_check", body={"email": args.email})
    print("# platform_check:", status, json.dumps(data))
    if not isinstance(data, dict) or data.get("platform") != "ARCH_2_0":
        # platform_check also answers ARCH_1_0 for addresses that have no
        # account at all, so let the sign-in below decide.
        print(
            "# warning: not reported as ARCH_2_0 (Orion). The API answers ARCH_1_0 for"
            " unknown email addresses too; use the exact address configured in"
            " Home Assistant. Trying to sign in anyway.",
            file=sys.stderr,
        )

    status, auth = request(
        "POST", "/v1/accounts/authenticate",
        body={"email": args.email, "password": password},
    )
    # The response body is never printed: on errors it may echo the password.
    if status != 200 or not isinstance(auth, dict) or not auth.get("jwt"):
        sys.exit(
            f"authenticate failed: HTTP {status}. Check the email address and password"
            " (they must be the ones you use in the Comfort Touch app)."
        )
    token, aid = auth["jwt"], auth["account_id"]
    print("# authenticate: OK")

    status, heaters = request("GET", f"/v1/accounts/{aid}/heaters", token)
    print("# heaters:", status)
    print(json.dumps(show(heaters), indent=2))

    for heater in heaters if isinstance(heaters, list) else []:
        hid = heater.get("heater_id")
        base = f"/v1/accounts/{aid}/heaters/{hid}"
        names = ("statistics", "weather")
        if not args.energy_only:
            names += ("capabilities", "presets")
        for name in names:
            status, data = request("GET", f"{base}/{name}", token)
            print(f"# {name}:", status)
            print(json.dumps(show(data), indent=2))
        if args.energy_only:
            continue
        status, zones = request("GET", f"{base}/zones", token)
        print("# zones:", status)
        print(json.dumps(show(zones), indent=2))
        for zone in zones if isinstance(zones, list) else []:
            n = zone.get("zone_number")
            status, data = request("GET", f"{base}/zones/{n}/scheme/week", token)
            print(f"# zone {n} week scheme:", status)
            print(json.dumps(show(data), indent=2))


if __name__ == "__main__":
    main()
