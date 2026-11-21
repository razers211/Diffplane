import os
from pathlib import Path

# Use the same Annet context as the orchestrator container (our generators and rulebook),
# before anything in Annet reads it.
os.environ["ANN_CONTEXT_CONFIG_PATH"] = str(Path(__file__).resolve().parents[1] / "deploy" / "annet" / "context.yml")

from annet import hardware, rulebook  # noqa: E402

# The annet CLI sets these once at start-up; Annet refuses to set them twice per process.
hardware.hardware_connector.set(hardware.AnnetHardwareProvider)
rulebook.rulebook_provider_connector.set(rulebook.DefaultRulebookProvider)

import pytest  # noqa: E402

from orchestrator import auth  # noqa: E402

# Hashing at the shipped strength (128 MiB per hash) doubles the suite's time; tests hash cheaply.
auth._SCRYPT = {"n": 2**12, "r": 8, "p": 1}


@pytest.fixture(autouse=True)
def fixed_secrets(monkeypatch):
    # Generated config must not depend on the developer's shell.
    monkeypatch.delenv("SNMP_COMMUNITY", raising=False)
    monkeypatch.delenv("SFTP_ADDRESS", raising=False)   # tests that want the key set it
    monkeypatch.setenv("MLAG_DFS_PASSWORD", "dfs-test-key")
    # Tests deploy: deploys allowed to `example` (the code's default is none), and plain-HTTP
    # test clients, which would not send back a Secure cookie (the code's default is Secure).
    if "DEPLOY_ALLOWED_SITES" not in os.environ:
        monkeypatch.setenv("DEPLOY_ALLOWED_SITES", "example")
    if "COOKIE_SECURE" not in os.environ:
        monkeypatch.setenv("COOKIE_SECURE", "false")
