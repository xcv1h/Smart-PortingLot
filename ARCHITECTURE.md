# Architecture decisions

Why the project is built the way it is. Each entry names the decision, what we could have done instead, and the reason we did not. The *what* lives in [README.md](README.md); this file is only the *why*.

## 1. Three containers, one compose file

**Decision:** `frontend` (nginx), `backend` (FastAPI), `redis`, started by one `compose.yaml`.

**Alternative:** one container running nginx, Python and Redis together.

**Why:** the brief asks for a multi-service stack, and one process per container is what Docker is designed around: each image stays small, each service restarts and scales on its own, and the compose file is the single place that shows how they are wired.

## 2. FastAPI for the backend

**Decision:** FastAPI served by uvicorn.

**Alternatives:** Flask, Django, a Node/Express service.

**Why:**
- The API is four routes. Flask would do, but FastAPI validates the request body for us (a missing or non-string `plate` is rejected before our code runs) and generates the interactive docs at `/api/docs` for free, which is useful in the demo.
- Django is far too much for four routes and no database models.
- Python keeps the whole backend in one language that every team member reads, and the official `redis` client is mature.

The app is one file on purpose, about 200 lines, so anyone can explain every line in a code review.

## 3. No Dockerfile for Redis

**Decision:** `redis` uses the official `redis:8.10-alpine` image straight from Docker Hub, configured with command-line flags in `compose.yaml`.

**Alternative:** a `redis/Dockerfile` that copies in a `redis.conf`.

**Why:** we change one setting (`--appendonly yes`) and pass the port through (`--port`, so `REDIS_PORT` moves the server and the backend together), and Redis accepts both as command arguments. A Dockerfile that only adds a config file would be an extra image to build, scan and explain, for no gain. The two services that *do* have Dockerfiles need them: nginx gets our config and static files, the backend gets our code and dependencies.

## 4. Redis as the only state

**Decision:** the backend keeps nothing in process memory. Every request reads and writes Redis.

**Alternative:** a Python dict in the backend, maybe saved to a file.

**Why:** a container can be restarted or recreated at any time, and the brief requires the data to survive that. With all state in Redis the backend is stateless, so restarting it, rebuilding it, or running two copies of it changes nothing. It also makes the demo honest: the data is visible with `redis-cli`, not hidden in a Python process.

## 5. One hash per occupied port

**Decision:** key `port:Gi0/1` is a Redis hash with fields `plate` and `entered_at`. A free port has no key.

**Alternatives:** a single JSON blob for the whole lot; a hash per car keyed by plate plus a plate-to-port index; a sorted set.

**Why:**
- One key type, readable by hand: `KEYS 'port:*'` lists the ports that are up, `HGETALL port:Gi0/3` shows who is there.
- Looking a car up by plate means scanning at most `CAPACITY` keys (8 by default). An index would make that O(1), at the cost of two keys per car that must be kept in sync. At this size the scan is cheaper to explain than the sync.
- A single JSON blob would need read-modify-write on every change, which is exactly the race condition the next decision avoids.

## 6. A Lua script for parking and releasing

**Decision:** "reject a duplicate plate, find the lowest free port, take it" runs as one Lua script sent to Redis with `EVAL`. Releasing a car uses a second script.

**Alternatives:** a Python lock; Redis `WATCH`/`MULTI`/`EXEC`; `SETNX` on each port in turn.

**Why:**
- Redis runs commands from all clients on a single thread, one at a time, and a script counts as one command. Nothing can run between the duplicate check and the write, so two cameras posting at the same moment cannot get the same port, and the same plate cannot be parked twice. This was verified with 30 parallel requests.
- A Python lock only protects one backend process. It breaks the moment there are two.
- `WATCH`/`MULTI` works but needs a retry loop when the transaction fails, which is more code to explain than ten lines of Lua.
- `SETNX` per port leaves half-finished states visible between steps.

## 7. nginx proxies `/api/`, only port 8080 is published

**Decision:** the browser talks only to nginx. nginx serves the static files and forwards `/api/` to `backend:8000`, stripping the prefix. Redis and the backend have no `ports:` entry.

**Alternative:** publish the backend on its own host port and let the dashboard call it directly.

**Why:**
- A page on `localhost:8080` calling `localhost:8000` is cross-origin, so the backend would need CORS headers. Through the proxy, page and API share one origin and the question never comes up.
- One published port is one thing to firewall and one thing to explain. Redis stays unreachable from outside the compose network.
- It gives the frontend Dockerfile a real job: copying in `nginx.conf`.

The backend's routes are therefore defined without `/api`, and uvicorn is told about the prefix with `--root-path /api` so the generated docs link to the right URL.

## 8. AOF persistence on a named volume

**Decision:** `redis-server --appendonly yes`, with `/data` on the named volume `redis-data`.

**Alternative:** default RDB snapshots, which also land on the volume.

**Why:** snapshots run every few minutes. A crash between two snapshots loses the cars that entered since the last one. AOF appends every write to a file as it happens, so after any restart the lot is exactly what it was. The volume outlives `docker compose down`; only `down -v` deletes it, which is the documented way to reset the lot.

## 9. Healthchecks and start order

**Decision:** `redis` is checked with `redis-cli ping`, `backend` with `wget` on `/health`, which returns 503 unless Redis answers. `backend` waits for `redis` to be healthy, `frontend` waits for `backend`.

**Alternative:** plain `depends_on`, which only waits for the container to *start*.

**Why:** without the backend check, `docker compose up --wait` returned about a second before uvicorn was listening and the first request got a 502 from nginx. The CI smoke test relies on `--wait` meaning "ready", so it has to be true.

Two details that cost us time, recorded so nobody repeats them:
- The healthcheck uses `127.0.0.1`, not `localhost`. Busybox `wget` resolves `localhost` to the IPv6 address first, and uvicorn listens on IPv4 only, so `localhost` fails every time.
- The timeout is 5 seconds because a dead Redis takes about 4 seconds to surface as a 503 (Docker's DNS takes that long to say a stopped container's name does not resolve). The Redis client's automatic retries are switched off in the backend for the same reason: with them, the 503 took 44 seconds.

## 10. Pinned image tags

**Decision:** `python:3.13-alpine`, `redis:8.10-alpine`, `nginx:stable-alpine`. Python packages are pinned to exact versions.

**Alternative:** `python:alpine`, `redis:alpine`, `latest`.

**Why:** a floating tag can change what gets built between two runs of the same command, which makes "it worked yesterday" impossible to debug. Minor-version pins still receive security patches. nginx's `stable` line only gets bug fixes, which is the same idea in nginx's own naming.

## 11. Small, non-root backend image

**Decision:** Alpine base, a dedicated user (`uid 1000`), only the app code and three runtime packages in the image. Tests and dev dependencies are kept out by `.dockerignore` and by copying only `app/`.

**Why:** less in the image means less to download, scan and attack. Nothing in the app needs root, and running as root inside a container is the first thing any reviewer flags. The uid is numeric in the `USER` line because hadolint, which CI runs, requires it.

## 12. Tests without a Redis server

**Decision:** the backend tests run against `fakeredis`, an in-memory stand-in that also runs the Lua scripts.

**Alternative:** a real Redis in a container during tests, or mocking the Redis client.

**Why:** `pytest` runs in seconds on any laptop and in CI with nothing else installed. Mocking the client would have skipped the Lua scripts, which are the one piece of logic worth testing. With `fakeredis[lua]` the real scripts execute, so the atomicity rules are what the tests check.

## 13. One readable log line per event

**Decision:** the backend prints one syslog-style line to stdout for car in, car out and lot full, for example `%PARK-5-UPDOWN: Slot Gi0/1, changed state to up (plate 12345678)`, the same wording the dashboard uses.

**Why:** `docker compose logs backend` is the operator's view during the demo, and stdout is where Docker collects logs from. The Cisco wording keeps the theme consistent with the dashboard, which prints its own lines in the browser.
