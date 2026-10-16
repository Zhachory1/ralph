# Sandbox isolation: what the builder's code can reach

Run against Colima on macOS with Docker 29.5.2, Hermes v0.22.2, and `tecnativa/docker-socket-proxy:0.3.0`.

**Result: the sandbox isolates the thing it needs to.** Agent commands run in a per-profile sibling container that cannot see the host filesystem, the Docker socket, the Kanban database, or any credential. `TERMINAL_ENV: docker` is enabled.

## Correction

An earlier version of this document claimed the opposite, and it was wrong.

That test ran `docker run -v /:/host` **from inside the hermes container** and found it could mount the host. True, and not the threat: the hermes container is the orchestrator, and it holds Docker API access precisely so it can create sandboxes. The builder's untrusted code never runs there. It runs in the sandbox, and **Hermes constructs the mounts — the agent does not choose them.**

The lesson survives in a sharper form. "Test the escape, not the configuration" is right, and it has to be the escape *from where the untrusted code actually runs*. Probing from the wrong container produced a confident, wrong conclusion, and nearly stopped a stage for no reason.

## What the sandbox gets

With `TERMINAL_ENV: docker`, a shell command from the `ralph-author` profile runs in `nousresearch/hermes-sandbox`, mounted like this:

| Mount | Access |
| --- | --- |
| `profiles/<profile>/sandboxes/docker/default/workspace` → `/workspace` | read-write |
| `profiles/<profile>/sandboxes/docker/default/home` → `/root` | read-write |
| `profiles/<profile>/skills`, caches, attachments, images | read-only |

Everything is under `profiles/<profile>/`, so **each profile gets its own sandbox**. Builder and experimenter never share one, which was an open question in the design and is now settled.

## What it cannot reach

Probed from inside a live sandbox:

| Probe | Result |
| --- | --- |
| `ls /Users` | No such file or directory |
| `/var/run/docker.sock` | not present |
| `docker` CLI | not installed |
| `DOCKER_HOST` | unset |
| `find / -name auth.json` | nothing |
| `find / -name kanban.db` | nothing |
| `/root/.hermes/profiles` | not present |

So the design's claim holds: the sandbox never sees the board, memory, or other profiles' tokens. A dependency install script or an injected Makefile reaches the task's working copy and nothing else.

## What the socket proxy does and does not do

The proxy refuses the volume, network, and daemon-reconfiguration APIs, verified:

```
$ docker volume ls
Error response from daemon: 403 Forbidden — Request forbidden by administrative rules.
```

It does **not** stop the hermes container from bind-mounting the host on a container it creates: `VOLUMES: 0` governs the `/volumes` API, while a bind mount is a field on container create, which has to be allowed for sandboxes to exist at all.

That is a real defence-in-depth gap, and a different, lower-priority threat than the one the sandbox addresses. It matters if the orchestrator itself is compromised — a prompt injection reaching the hermes container rather than the builder's workspace. The proxy still earns its place by removing the daemon-reconfiguration and credential-reading paths, and it is where a body-filtering proxy would go if that threat ever justifies one.

| Threat | Status |
| --- | --- |
| Untrusted code in the builder's workspace reaching credentials or the host | **Contained** by the per-profile sandbox |
| A compromised orchestrator mounting the host | Not contained; accepted for now |

## Rule this establishes

Test the escape from where the untrusted code actually runs. A configuration review would have missed the real boundary, and an escape test from the wrong container invented a breach that did not exist.
