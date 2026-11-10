"""
Smart PortingLot backend: a parking lot that behaves like a Cisco switch.

Every parking slot is a switch port, Gi0/1 ... Gi0/<CAPACITY>. A parked car
is a port in the "up" state. The IoT camera POSTs plates to /cars and the
dashboard polls /ports. nginx strips the /api prefix before a request reaches
us, so the routes below are defined without it.

No state is kept in process memory. Everything lives in Redis, so the lot
survives a restart of this container.

Redis key layout (inspect it with `redis-cli` during the demo):

    port:Gi0/1   hash   One key per OCCUPIED port, named after the port.
                        Fields:
                          plate       digits only, e.g. "12345678"
                          entered_at  UTC ISO 8601, e.g. "2026-11-10T12:00:00Z"
                        A free port simply has no key.

    redis-cli KEYS 'port:*'          which ports are up
    redis-cli HGETALL port:Gi0/1     who is parked there, and since when

That is the whole model. Finding a car by plate means looking at (at most)
CAPACITY hashes, which is cheap at this size and keeps the data readable.
"""

import logging
import os
import re
import sys
from datetime import datetime, timezone

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# --- Configuration (environment variables, with defaults) --------------------

REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
CAPACITY = int(os.environ.get("CAPACITY", "8"))
RATE_PER_HOUR = float(os.environ.get("RATE_PER_HOUR", "12.0"))

PORTS = [f"Gi0/{n}" for n in range(1, CAPACITY + 1)]
PORT_KEYS = [f"port:{port}" for port in PORTS]

TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"  # UTC, ISO 8601, trailing Z

# Events go to stdout so they show up in `docker compose logs`.
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("parking")

# decode_responses=True makes Redis give us str instead of bytes.
# retries=0: by default redis-py retries a failed connection 10 times with
# backoff, so a stopped Redis took ~45 s to turn into a 503. Failing on the
# first try (a few seconds at most) lets the compose healthcheck react quickly.
redis_client = redis.Redis(
    host=REDIS_HOST, port=REDIS_PORT, decode_responses=True, retry=Retry(NoBackoff(), retries=0)
)

# The /api prefix nginx strips is given to uvicorn (--root-path /api in the
# Dockerfile), so the interactive docs opened through the proxy at /api/docs
# still find /api/openapi.json. FastAPI's own root_path parameter is deprecated.
app = FastAPI(title="Smart PortingLot")


# --- Atomic port assignment, done inside Redis -------------------------------
#
# Two cameras can POST at the same moment, so "find the first free port and
# take it" must be one indivisible step. We let Redis do it with a Lua script
# (the EVAL command). Redis runs commands from all clients on a single thread,
# one after another, and a whole script counts as one command. So no other
# client can squeeze a command in between the checks and the write below,
# which is what makes it impossible for two POSTs to get the same port or for
# the same plate to be parked twice. No Python lock is involved.
#
# KEYS = the port hashes in port order (port:Gi0/1, port:Gi0/2, ...)
# ARGV = [plate, entered_at]
# Returns the 1-based index of the port taken, or "DUPLICATE" / "FULL".
# (HGET on a missing key gives Lua `false`, which is how we spot a free port.)
PARK_SCRIPT = """
local free = 0
for i, key in ipairs(KEYS) do
    local plate = redis.call('HGET', key, 'plate')
    if plate == ARGV[1] then
        return 'DUPLICATE'
    end
    if plate == false and free == 0 then
        free = i
    end
end
if free == 0 then
    return 'FULL'
end
redis.call('HSET', KEYS[free], 'plate', ARGV[1], 'entered_at', ARGV[2])
return free
"""

# Same idea for leaving: find the port holding this plate and clear it in one
# step, so a car cannot be released twice, and a port cannot be freed and
# re-taken by someone else halfway through.
# KEYS = the port hashes in port order, ARGV = [plate]
# Returns [port index, entered_at], or nil when the plate is not parked.
RELEASE_SCRIPT = """
for i, key in ipairs(KEYS) do
    if redis.call('HGET', key, 'plate') == ARGV[1] then
        local entered_at = redis.call('HGET', key, 'entered_at')
        redis.call('DEL', key)
        return {i, entered_at}
    end
end
return nil
"""


# --- Helpers ------------------------------------------------------------------

def utc_now() -> datetime:
    """Current UTC time, whole seconds (the tests replace this with a fixed clock)."""
    return datetime.now(timezone.utc).replace(microsecond=0)


def normalise_plate(raw: str) -> str:
    """Remove dashes, then require exactly 7 or 8 digits."""
    plate = raw.replace("-", "")
    if not re.fullmatch(r"[0-9]{7,8}", plate):
        raise HTTPException(422, "Plate must be 7 or 8 digits, dashes are allowed.")
    return plate


class CarIn(BaseModel):
    plate: str


# --- Routes -------------------------------------------------------------------

@app.get("/health")
def health():
    # ping() raises a RedisError when Redis is down; the handler at the bottom
    # turns that into a 503, which is what the compose healthcheck looks for.
    redis_client.ping()
    return {"status": "ok"}


@app.get("/ports")
def list_ports():
    ports = []
    for port, key in zip(PORTS, PORT_KEYS):
        car = redis_client.hgetall(key)  # {} when the port is free
        ports.append({"port": port, "plate": car.get("plate"), "entered_at": car.get("entered_at")})
    return {"capacity": CAPACITY, "ports": ports}


@app.post("/cars", status_code=201)
def park_car(body: CarIn):
    plate = normalise_plate(body.plate)
    entered_at = utc_now().strftime(TIME_FORMAT)
    result = redis_client.eval(PARK_SCRIPT, len(PORT_KEYS), *PORT_KEYS, plate, entered_at)
    if result == "DUPLICATE":
        raise HTTPException(409, f"Plate {plate} is already parked.")
    if result == "FULL":
        log.warning(f"%PARKING-4-FULL: No free port for plate {plate}")
        raise HTTPException(507, f"The lot is full, all {CAPACITY} ports are up.")
    port = PORTS[result - 1]
    log.info(f"%LINK-3-UPDOWN: Interface {port}, changed state to up (plate {plate})")
    return {"port": port, "plate": plate, "entered_at": entered_at}


@app.delete("/cars/{plate}")
def release_car(plate: str):
    plate = plate.replace("-", "")
    result = redis_client.eval(RELEASE_SCRIPT, len(PORT_KEYS), *PORT_KEYS, plate)
    if result is None:
        raise HTTPException(404, f"Plate {plate} is not parked.")
    index, entered_at = result
    port = PORTS[index - 1]
    duration = int((utc_now() - datetime.fromisoformat(entered_at)).total_seconds())
    fee = round(duration / 3600 * RATE_PER_HOUR, 2)
    log.info(
        f"%LINK-3-UPDOWN: Interface {port}, changed state to down (plate {plate}, {duration}s, fee {fee})"
    )
    return {"port": port, "plate": plate, "duration_seconds": duration, "fee": fee}


# --- Error responses ----------------------------------------------------------
# Every error must be {"detail": "<one sentence>"}. HTTPException already does
# that; these two cover the cases FastAPI would otherwise answer differently.

@app.exception_handler(RequestValidationError)
def invalid_request(request: Request, exc: RequestValidationError):
    # FastAPI's default is a list of error objects; we want one sentence.
    detail = 'Request body must be a JSON object like {"plate": "12345678"}.'
    return JSONResponse(status_code=422, content={"detail": detail})


@app.exception_handler(redis.RedisError)
def redis_unavailable(request: Request, exc: redis.RedisError):
    # Without this, a Redis outage would be a plain-text 500.
    return JSONResponse(status_code=503, content={"detail": "Redis is not reachable."})
