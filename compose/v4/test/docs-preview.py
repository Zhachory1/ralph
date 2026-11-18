"""Exercise the disposable docs preview against a synthetic docs tree."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

COMPOSE = Path(__file__).resolve().parents[1] / "docs-preview.yml"
PNG = b"\x89PNG\r\n\x1a\n"


def wait_for(url, expected):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if expected in response.read():
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise AssertionError(f"Preview did not serve expected content: {url}")


with tempfile.TemporaryDirectory(prefix="ralph-docs-preview-") as directory:
    root = Path(directory)
    (root / "docs").mkdir()
    (root / "mkdocs.yml").write_text("site_name: Synthetic preview\ntheme:\n  name: material\n")
    index = root / "docs" / "index.md"
    index.write_text("# First synthetic preview\n")
    project = "ralph-docs-test-" + secrets.token_hex(8)
    env = {key: value for key, value in os.environ.items()
           if key in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG")}
    # Another process can claim these ports before Compose; the test then fails without touching it.
    with socket.socket() as docs_socket, socket.socket() as kroki_socket:
        docs_socket.bind(("127.0.0.1", 0))
        kroki_socket.bind(("127.0.0.1", 0))
        env.update(COMPOSE_DISABLE_ENV_FILE="1", RALPH_V4_DOCS_ROOT=str(root),
                   RALPH_V4_DOCS_PORT=str(docs_socket.getsockname()[1]),
                   RALPH_V4_KROKI_PORT=str(kroki_socket.getsockname()[1]))
    command = ["docker", "compose", "-p", project, "-f", str(COMPOSE)]

    def compose(*args, check=True):
        result = subprocess.run(command + list(args), env=env, capture_output=True, text=True, timeout=180)
        if check and result.returncode:
            raise RuntimeError(f"Compose {' '.join(args)} failed: {result.stderr}")
        return result

    missing_root = env.copy()
    del missing_root["RALPH_V4_DOCS_ROOT"]
    missing = subprocess.run(command + ["config"], env=missing_root, capture_output=True, text=True, timeout=30)
    assert missing.returncode != 0 and "RALPH_V4_DOCS_ROOT" in missing.stderr
    config = json.loads(compose("config", "--format", "json").stdout)
    assert set(config["services"]) == {"docs-portal", "kroki", "mermaid", "excalidraw"}
    for service in config["services"].values():
        assert "@sha256:" in service["image"]
        for port in service.get("ports", []):
            assert port["host_ip"] == "127.0.0.1"
    assert config["networks"]["preview"]["internal"]
    assert set(config["services"]["mermaid"]["networks"]) == {"preview"}
    assert set(config["services"]["excalidraw"]["networks"]) == {"preview"}
    assert config["services"]["kroki"]["environment"]["KROKI_SAFE_MODE"] == "secure"
    assert all(dependency["condition"] == "service_healthy"
               for dependency in config["services"]["kroki"]["depends_on"].values())
    assert config["services"]["docs-portal"]["volumes"][0]["read_only"]
    assert not config["services"]["mermaid"].get("ports")
    assert not config["services"]["excalidraw"].get("ports")

    started = False
    try:
        started = True
        compose("up", "-d")
        docs_address = compose("port", "docs-portal", "8000").stdout.strip()
        kroki_address = compose("port", "kroki", "8000").stdout.strip()
        assert docs_address.startswith("127.0.0.1:"), docs_address
        assert kroki_address.startswith("127.0.0.1:"), kroki_address
        container = compose("ps", "-q", "docs-portal").stdout.strip()
        inspect = subprocess.run(["docker", "inspect", container], env=env, check=True,
                                 capture_output=True, text=True, timeout=30)
        mounts = json.loads(inspect.stdout)[0]["Mounts"]
        assert any(mount["Destination"] == "/docs" and mount["RW"] is False for mount in mounts)
        docs_url = "http://" + docs_address + "/"
        kroki_url = "http://" + kroki_address
        wait_for(docs_url, b"First synthetic preview")
        wait_for(kroki_url + "/health", b"")

        diagrams = {
            "mermaid": "graph TD\n  A[Start] --> B[Finish]\n",
            "graphviz": "digraph { A -> B }",
            "plantuml": "@startuml\nAlice -> Bob: hello\n@enduml",
            "excalidraw": json.dumps({
                "type": "excalidraw", "version": 2,
                "elements": [{"id": "fixture", "type": "rectangle", "x": 0, "y": 0,
                              "width": 100, "height": 60, "angle": 0, "strokeColor": "#000000",
                              "backgroundColor": "transparent", "fillStyle": "solid",
                              "strokeWidth": 1, "strokeStyle": "solid", "roughness": 1,
                              "opacity": 100, "seed": 1, "version": 1, "versionNonce": 1,
                              "isDeleted": False, "groupIds": [], "boundElements": None,
                              "updated": 1, "link": None, "locked": False}],
                "appState": {"viewBackgroundColor": "#ffffff"}, "files": {},
            }),
        }
        for kind, source in diagrams.items():
            output_format = "svg" if kind == "excalidraw" else "png"
            request = urllib.request.Request(kroki_url + f"/{kind}/{output_format}", source.encode(),
                                             {"Content-Type": "text/plain"})
            with urllib.request.urlopen(request, timeout=60) as response:
                output = response.read()
                assert b"<svg" in output if output_format == "svg" else output.startswith(PNG), kind
            print(f"{kind} {output_format.upper()} passed")

        request = urllib.request.Request(kroki_url + "/mermaid/svg", diagrams["mermaid"].encode(),
                                         {"Content-Type": "text/plain"})
        with urllib.request.urlopen(request, timeout=60) as response:
            assert b"<svg" in response.read()
        print("mermaid SVG passed")

        index.write_text("# Updated synthetic preview\n")
        wait_for(docs_url, b"Updated synthetic preview")
        print("MkDocs preview and reload passed")
    except Exception:
        if started:
            print(compose("ps", "-a", check=False).stdout)
            print(compose("logs", "--no-color", check=False).stdout[-6000:])
            container = compose("ps", "-q", "docs-portal", check=False).stdout.strip()
            if container:
                print(subprocess.run(["docker", "inspect", "--format",
                                      "{{json .HostConfig.PortBindings}} {{json .NetworkSettings.Ports}}",
                                      container], env=env, capture_output=True, text=True, timeout=30).stdout)
        raise
    finally:
        if started:
            compose("down", "--timeout", "5")
            remaining = subprocess.run(
                ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"],
                env=env, check=True, capture_output=True, text=True, timeout=30,
            ).stdout.strip()
            assert not remaining, "Disposable preview containers remain"
