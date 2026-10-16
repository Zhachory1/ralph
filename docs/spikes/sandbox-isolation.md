# Sandbox isolation: what the socket proxy does not buy

Run against Colima on macOS with Docker 29.5.2 and `tecnativa/docker-socket-proxy:0.3.0`.

**Result: the socket proxy alone is not enough to sandbox the builder.** It blocks the Docker APIs it advertises, and the one attack that matters still succeeds. `TERMINAL_ENV` stays `local` until this is closed.

## What was tried

The design calls for the builder to run its commands in a sibling sandbox container, reached through a socket proxy rather than a raw Docker socket, because raw socket access is root-equivalent on the host. The proxy was configured to allow container lifecycle and refuse everything else:

| Allowed | Refused |
| --- | --- |
| `CONTAINERS`, `IMAGES`, `POST`, `EXEC` | `VOLUMES`, `NETWORKS`, `SERVICES`, `SWARM`, `NODES`, `TASKS`, `SECRETS`, `CONFIGS`, `PLUGINS`, `SYSTEM`, `AUTH`, `DISTRIBUTION`, `INFO`, `BUILD`, `COMMIT`, `SESSION` |

## What holds

The refusals are real. From inside the hermes container:

```
$ docker volume ls
Error response from daemon: 403 Forbidden — Request forbidden by administrative rules.

$ docker info
(refused)
```

Container lifecycle works, so Hermes can create and run sandboxes.

## What does not hold

**`VOLUMES: 0` does not prevent bind mounts.** It governs the `/volumes` API — creating and listing named volumes. A bind mount is a field on the container-create call, which `CONTAINERS: 1` and `POST: 1` have to allow for sandboxes to exist at all.

So this succeeds from inside the hermes container:

```
$ docker run --rm -v /:/host alpine:3 ls /host
bin  dev  etc  home  root  sbin  srv  sys  tmp  usr  var
```

On macOS with Colima, the VM mounts the user's home directory, so "host root" is not only the VM:

```
$ docker run --rm -v /:/host alpine:3 ls /host/Users/<you>
AGENTS.md  Documents  Downloads  ...

$ docker run --rm -v /:/host alpine:3 sh -c 'touch /host/Users/<you>/x && echo WRITABLE'
WRITABLE
```

Read **and** write access to the whole home directory, from the container the builder's commands would run in.

This is worse than not sandboxing. Running the builder with `TERMINAL_ENV: local` keeps untrusted code inside the hermes container, which already holds the credentials — bad, but bounded by that container. Turning on the Docker backend as configured would let the same code escape to the machine, while the architecture diagram claims it is contained.

## Why the proxy still belongs in the stack

It is necessary and insufficient. It removes the daemon-reconfiguration and credential-reading paths, and it is the right place to add the missing control. Leaving it in means the remaining gap is one specific, named thing rather than "the socket is wide open."

## What would close it

Each needs testing before the builder ships; none is in place yet.

1. **Reject bind mounts at the proxy.** The tecnativa proxy filters by API path and method, not by request body, so this needs a filtering proxy that inspects `HostConfig.Binds` and `Mounts` on container create. The most direct fix, and the one that keeps the current architecture honest.
2. **Rootless Podman.** The daemon runs as an unprivileged user, so a mount of `/` yields that user's view rather than root's. Narrows the blast radius without a custom proxy; changes the runtime.
3. **A VM per sandbox.** Strongest and slowest; a container inside a throwaway VM cannot reach the outer host at all.
4. **Accept and scope.** Keep `local`, and give the builder a dedicated unprivileged user and a workspace-only view. Weakest, but honest — and strictly better than a sandbox that advertises isolation it does not have.

## Rule this establishes

Test the escape, not the configuration. The proxy's own documentation describes `VOLUMES: 0` as blocking volumes, and reading that would have produced a confident, wrong claim in the design. One `docker run -v /:/host` answered it in a second.

Any future isolation claim in this repository needs a test that tries to break out and fails.
