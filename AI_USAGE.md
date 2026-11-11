Prompt 1:

let's outline the architecture:
frontend dockerfile built from nginx:stable-alpine, backend is fastapi over python:alpine, and redis... redis doesn't need a dockerfile imo.
all three images run from one compose, front@8080, back@8088, redis@8888. one volume for redis.

github actions workflow might very well be building the dockerfile, but it needs some ideas too

need some ideas for the theme/feature/touch mostly.

Reply 1:

The shape is right, and Redis indeed needs no Dockerfile. I’d change three things before you build.

Architecture changes
Don’t publish Redis at all. The backend reaches it at redis:6379 over the compose network, so a host port at 8888 only adds exposure and an easy code review question. If you want redis-cli from the host for debugging, bind it to 127.0.0.1 only.
Proxy the API through nginx instead of publishing 8088. The dashboard’s JS runs in the browser, so fetching localhost:8088 from a page on localhost:8080 is cross-origin and needs CORS in FastAPI. A location /api/ with proxy_pass to the backend avoids that and leaves one published port. It also gives the frontend Dockerfile a reason to exist, since it copies in a custom nginx.conf.
Turn on AOF for Redis. A named volume on /data survives down/up with default snapshots, but the brief says “in case of a crash”. Running with --appendonly yes covers that, and it’s one command: line in compose.

Smaller points:

Pin the Python tag (e.g. python:3.13-alpine) rather than floating python:alpine.
Add a redis-cli ping healthcheck and depends_on: condition: service_healthy on the backend.
Something has to play the camera. A small script or a fourth “camera” container that POSTs random plates makes the demo run itself.
Workflow ideas
Build: docker compose build on every push; this is the required part.
Smoke test: docker compose up -d --wait, POST a plate with curl, GET it back, fail if it’s missing.
Persistence test: POST, down, up, GET again. This is your demo’s step 2, run automatically on every push.
Lint: hadolint on the Dockerfiles and docker compose config to validate the compose file.
Bonus: on main only, log in to ghcr.io with GITHUB_TOKEN, tag with docker/metadata-action (short SHA, plus semver when you push a git tag), and push both images.

Prompt 2:

liked your idea of a cisco switch themed parking lot.

so, redis - unexposed, backend - proxied through nginx, python tag needs pinning, and the rest will come along the way. can you help skeleton out the readme?

Reply 2:

The skeleton is in README.md, with placeholders in <angle brackets> and TODO comments where a decision is still open.

It covers how it works (with a Mermaid diagram GitHub renders natively), quick start, services, API, data persistence, CI/CD, the switch theme mapping, repository layout, documentation and team.

Three things in it are my assumptions:

Track 2 is stated at the top.
The backend isn’t published to the host, so the test curl goes through nginx at localhost:8080/api/.
The second documentation file is a placeholder for an architecture-decisions doc, since the brief never names it.

The persistence section doubles as your Demo Day script for the down/up proof, and the theme table is the quickest thing to show the class before the live demo.

Prompt 3:

Attached - compose.yaml
good?

Reply 3:

Not yet. It fails validation, and it is missing the pieces the brief and the README promise. The output from `docker compose config`:

```
validating compose.yaml: services.frontend.ports must be a array
```

What I'd change, in order of importance:

1. **ports must be a list.** `ports: 8080:80` needs to be `ports: ["8080:80"]` or a dash item. Quote it, since YAML can read `a:b` as a mapping.
2. **No persistence.** The brief's "survives a crash" claim needs `command: ["redis-server", "--appendonly", "yes"]` and a named volume on `/data`, declared under a top-level `volumes:` block. Without it, `docker compose down` and `up` keeps the data only by luck of the default snapshot timing.
3. **No healthchecks or ordering.** Add a `redis-cli ping` check to redis, the wget check to backend, and `depends_on` with `condition: service_healthy` from backend to redis. The frontend can depend on backend plainly. This is also what the CI smoke test's `up --wait` relies on.
4. **Pin the Redis tag.** `redis:8-alpine` floats across minor versions, same reasoning as the Python tag. Something like `redis:8.2-alpine` is enough, whichever current tag you choose.

Nothing is wrong with the service names or build contexts; `frontend`, `backend` and `redis` are exactly what nginx.conf and the backend expect. Say the word and I'll write the full file and prove the down/up persistence.

Prompt 4:

Attached - compose.yaml
are you sure you read the file? 2-4 was done exactly as you've said to change it.

Reply 4: 

The file has probably changed since I read it. Let me re-read it now.

The command it ran, and the output:

```
$ cat -n compose.yaml && echo && echo "--- docker compose config:" && docker compose config --quiet && echo "config OK"
     1	services:
     2	  frontend:
     3	    build: ./frontend
...
--- docker compose config:
config OK
```

I read an older save earlier, but the current file has the persistence, Redis healthcheck and ordering, so I'm now checking it with the README's demo script: bring it up, park a car, restart, and confirm the car is still there.

Reply 4, continued (later in the same check, no new prompt):

My curl helper was firing three requests per call, which explains the parked cars and the wrong status, while the docs check is fine. I'm fixing the helper to send one request, flushing Redis, and rebuilding the image with the corrected ignore file.

What the AI got wrong:

Inaccuracy 1: stale compose.yaml led to inaccurate feedback. Reply 3 reviewed an older save of the file, so three of its four points (persistence, healthchecks, the pinned Redis tag) asked for changes that were already made. It only re-read the file after being challenged in Prompt 4.

Inaccuracy 2: a self-written curl helper sent three requests per call. While verifying the stack, the AI's own test helper fired every request three times, so extra cars were parked and the statuses it reported were wrong. The fault was in its test tooling, not in the project, and it had to flush Redis and repeat the check.
