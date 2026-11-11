"""
Camera simulator: plays the IoT camera at the gate, so the demo runs itself.

Every few seconds it either reports a new car (POST /api/cars with a random
plate) or lets a parked car leave (DELETE /api/cars/<plate>). Watch the
dashboard while it runs. Stop it with Ctrl+C.

Only the Python standard library is used, so there is nothing to install:

    python tools/camera.py
    python tools/camera.py --url http://localhost:8080 --interval 1
"""

import argparse
import json
import random
import time
import urllib.error
import urllib.request


def call(method, url, body=None):
    """Send one request. Returns (HTTP status, parsed JSON body)."""
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        # 4xx and 5xx answers arrive as an exception; the body is still JSON.
        return error.code, json.load(error)


def random_plate():
    """7 or 8 random digits, like an Israeli plate."""
    length = random.choice([7, 8])
    return "".join(random.choice("0123456789") for _ in range(length))


def step(api):
    """One camera event: a car enters, or a parked car leaves."""
    status, lot = call("GET", api + "/ports")
    if status != 200:
        # For example 503 while Redis is down: skip this round, try again later.
        print(f"Lot state not available (HTTP {status}).")
        return
    parked = [port["plate"] for port in lot["ports"] if port["plate"] is not None]
    full = len(parked) == lot["capacity"]

    # An empty lot can only fill and a full lot can only empty. In between,
    # cars enter a little more often than they leave, so the lot fills slowly.
    if parked and (full or random.random() < 0.4):
        plate = random.choice(parked)
        status, answer = call("DELETE", api + "/cars/" + plate)
        if status == 200:
            print(f"out {plate}  {answer['port']}  fee {answer['fee']:.2f}")
        else:
            print(f"out {plate}  HTTP {status}: {answer['detail']}")
    else:
        plate = random_plate()
        status, answer = call("POST", api + "/cars", {"plate": plate})
        if status == 201:
            print(f"in  {plate}  {answer['port']}")
        else:
            print(f"in  {plate}  HTTP {status}: {answer['detail']}")


def main():
    parser = argparse.ArgumentParser(description="Simulate the parking lot camera.")
    parser.add_argument("--url", default="http://localhost:8080", help="where the dashboard is served")
    parser.add_argument("--interval", type=float, default=3.0, help="seconds between events")
    args = parser.parse_args()
    api = args.url.rstrip("/") + "/api"

    print(f"Camera simulator sending to {api}, one event every {args.interval}s. Ctrl+C to stop.")
    try:
        while True:
            try:
                step(api)
            except (OSError, ValueError) as error:
                # Stack not up yet, or an answer that is not JSON (nginx 502).
                print(f"API not reachable: {error}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Camera stopped.")


if __name__ == "__main__":
    main()
