"""Uptime ping: exit code 0 if /health answers with status ok, 1 otherwise.

It calls the /health endpoint of a deployed server and prints the answer.
It writes nothing to disk. The exit code is what a scheduler reads to know if the app is up.

Run: uv run python -m scripts.ping https://your-app.onrender.com
Used by .github/workflows/uptime.yml (every 10 minutes), which also keeps a free Render
instance from sleeping.
"""

import sys

import httpx


def main() -> int:
    """Call /health on the given base URL (first argument) and report the result.

    Returns:
        0 if the server answered with status "ok"; 1 if it is down or answered something else.
    """
    # Default to the local server. rstrip("/") avoids a double slash in the final URL.
    url = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
    try:
        body = httpx.get(f"{url}/health", timeout=60).json()  # cold start can take a while
    except (httpx.HTTPError, ValueError) as exc:
        # HTTPError = network or HTTP failure. ValueError = the answer was not valid JSON.
        print(f"DOWN: {type(exc).__name__}")
        return 1
    print(body)
    return 0 if body.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
