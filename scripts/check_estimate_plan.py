#!/usr/bin/env python3
"""Calls POST /v1/estimate of this service in-process (real PrusaSlicer/UVtools, no network, no HTTP client needed) and prints the part of the answer that matters for quoting: orientation, footprint, time and the plate plan.

    docker exec -i <container> python3 - STL QUANTITY [auto|as-uploaded] < scripts/check_estimate_plan.py

The bearer token is taken from the container's own environment and is never printed. To try modified code without rebuilding the image, put a copy of `app` in /tmp/x and add `-e RESIN_APP_ROOT=/tmp/x`.
"""
import asyncio
import json
import os
import pathlib
import sys

sys.path.insert(0, os.environ.get("RESIN_APP_ROOT") or (str(pathlib.Path(__file__).resolve().parent.parent) if "__file__" in globals() else "/app"))

from app import main  # noqa: E402


def multipart(fields, filename, data):
    boundary = "----check"
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: model/stl\r\n\r\n'.encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return boundary, b"".join(parts)


async def post_estimate(stl_path, quantity, orientation):
    data = pathlib.Path(stl_path).read_bytes()
    fields = {"printer_profile": "elegoo-mars-2", "resin_profile": "elegoo-water-washable-grey", "quality": "balanced-0p05-medium",
              "requested_quantity": str(quantity), "orientation": orientation}
    boundary, body = multipart(fields, pathlib.Path(stl_path).name, data)
    headers = [(b"content-type", f"multipart/form-data; boundary={boundary}".encode()), (b"content-length", str(len(body)).encode()),
               (b"authorization", f"Bearer {main.PROJECT_TOKEN}".encode())]
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "path": "/v1/estimate", "raw_path": b"/v1/estimate",
             "query_string": b"", "headers": headers, "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8080), "scheme": "http", "root_path": ""}
    sent, out = [False], {"status": None, "body": b""}

    async def receive():
        if not sent[0]:
            sent[0] = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(3600)

    async def send(message):
        if message["type"] == "http.response.start":
            out["status"] = message["status"]
        elif message["type"] == "http.response.body":
            out["body"] += message.get("body", b"")

    await main.app(scope, receive, send)
    return out["status"], json.loads(out["body"] or b"{}")


async def run(stl_path, quantity, orientation):
    status, payload = await post_estimate(stl_path, quantity, orientation)
    summary = payload.get("summary", {})
    print(json.dumps({"http": status, "orientation_mode": orientation, "orientation_deg": summary.get("orientation_deg"),
                      "print_footprint_mm": summary.get("print_footprint_mm"), "layer_count": summary.get("layer_count"),
                      "print_time_seconds": summary.get("print_time_seconds"), "total_resin_ml": summary.get("total_resin_ml"),
                      "plate_plan": payload.get("plate_plan"), "detail": payload.get("detail")}, indent=1))


args = sys.argv if "__file__" in globals() else ["check_estimate_plan.py", *sys.argv[1:]]
if len(args) < 3:
    raise SystemExit("usage: check_estimate_plan.py STL QUANTITY [auto|as-uploaded]")
asyncio.run(run(args[1], int(args[2]), args[3] if len(args) > 3 else "auto"))
