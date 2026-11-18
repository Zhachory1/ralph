"""Start the isolated, tool-limited Council profile."""
import json
import os
from pathlib import Path
import sys

job = json.load(sys.stdin)
home = Path("/tmp/council-profile")
profile = home / "profiles" / "ralph-v4-council"
profile.mkdir(parents=True, mode=0o700)
config = {
    "platform_toolsets": {"cli": ["mcp-council"]},
    "agent": {
        "disabled_toolsets": ["terminal", "file", "kanban", "delegation", "code_execution", "browser",
                              "computer_use", "web", "skills", "memory", "vision", "image_gen",
                              "tts", "todo", "connections"],
        "api_max_retries": 0,
    },
    "memory": {"memory_enabled": False, "user_profile_enabled": False},
    "mcp_servers": {"council": {
        "url": f"http://host.docker.internal:{job['port']}/mcp",
        "headers": {"Authorization": "Bearer ${RALPH_COUNCIL_JOB_TOKEN}"},
        "strict_redirect_headers": True, "enabled": True,
        "tools": {"include": ["review_artifact"]}, "timeout": 130,
    }},
}
(profile / "config.yaml").write_text(json.dumps(config))
prompt = (
    "Call the Council review_artifact tool with no arguments for the registered job. "
    "Return the complete tool result, or report failure without inventing verdicts."
)
env = {
    "HOME": str(home), "HERMES_HOME": str(home), "PATH": os.environ["PATH"],
    "OPENROUTER_API_KEY": os.environ["OPENROUTER_API_KEY"],
    "RALPH_COUNCIL_JOB_TOKEN": os.environ["RALPH_COUNCIL_JOB_TOKEN"],
}
os.execvpe("hermes", ["hermes", "-p", "ralph-v4-council", "--ignore-rules",
                      "--provider", "openrouter", "--model", job["model"], "-z", prompt], env)
