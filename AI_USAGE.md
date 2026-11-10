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
