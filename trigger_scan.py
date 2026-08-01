"""Ask a running Playbook web service to start its daily market scan.

Used by the Render cron job, GitHub Actions, or any external scheduler. The
work happens inside the web service because that process owns the persistent
disk holding the price cache and scan history.
"""

import json
import os
import sys
import urllib.error
import urllib.request


def trigger(base_url, token, timeout=30, scheme="https"):
    if not base_url:
        raise SystemExit("Set PLAYBOOK_SCAN_URL to the deployed service host.")
    if not token:
        raise SystemExit("Set PLAYBOOK_SCAN_TOKEN to the service's scan token.")
    if not base_url.startswith("http"):
        normalized_scheme = str(scheme).strip().lower()
        if normalized_scheme not in {"http", "https"}:
            raise SystemExit("PLAYBOOK_SCAN_SCHEME must be http or https.")
        base_url = f"{normalized_scheme}://{base_url}"
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/opportunities/run",
        method="POST",
        headers={
            "X-Playbook-Scan-Token": token,
            "Content-Type": "application/json",
        },
        data=b"{}",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8") or "{}")
            print(f"{response.status}: {body.get('message', body)}", flush=True)
            return 0
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"{exc.code}: {detail}", file=sys.stderr, flush=True)
        # A 409 means a scan is already in flight, which is a success for cron.
        return 0 if exc.code == 409 else 1
    except urllib.error.URLError as exc:
        print(f"Could not reach the service: {exc.reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(
        trigger(
            os.getenv("PLAYBOOK_SCAN_URL", ""),
            os.getenv("PLAYBOOK_SCAN_TOKEN", ""),
            scheme=os.getenv("PLAYBOOK_SCAN_SCHEME", "https"),
        )
    )
