"""Production entry points. Values supplied only by the trusted service operator."""

import json
import os
from pathlib import Path

from .app import Application
from .config import Config
from .github import GitHub
from .store import Store
from .worker import Worker


def load():
    # No test-mode environment switch. The contract tests explicitly construct Config.
    config = Config(
        root=Path(os.environ["CLA_RELEASE_ROOT"]),
        data_dir=Path(os.environ["CLA_PRIVATE_DIR"]),
        origin=os.environ["CLA_ORIGIN"],
        repository="Tavotto/Tavotto",
        repository_id=int(os.environ["CLA_REPOSITORY_ID"]),
        holder_id=int(os.environ["CLA_HOLDER_ID"]),
        app_id=int(os.environ["CLA_APP_ID"]),
        app_slug=os.environ["CLA_APP_SLUG"],
        installation_id=int(os.environ["CLA_INSTALLATION_ID"]),
        client_id=os.environ["CLA_CLIENT_ID"],
        client_secret=Path(os.environ["CLA_CLIENT_SECRET_FILE"]).read_text().strip(),
        private_key=Path(os.environ["CLA_APP_PRIVATE_KEY_FILE"]).read_bytes(),
        webhook_secret=Path(os.environ["CLA_WEBHOOK_SECRET_FILE"]).read_bytes().strip(),
        evidence_key=Path(os.environ["CLA_EVIDENCE_KEY_FILE"]).read_bytes().strip(),
        exempt_ids=json.loads(os.environ["CLA_EXEMPT_IDS_JSON"]),
        final_check_enabled=os.environ.get("CLA_FINAL_CHECK_ENABLED") == "true",
        trusted_gates=json.loads(os.environ.get("CLA_TRUSTED_GATES_JSON", "{}")),
        merge_queue_enabled=os.environ.get("CLA_MERGE_QUEUE_ENABLED") == "true",
        signing_enabled=os.environ.get("CLA_SIGNING_ENABLED") == "true",
    )
    policy = config.validate()
    store = Store(config)
    store.register(policy)
    github = GitHub(config)
    return config, store, github, policy


def application():
    return Application(*load())


if __name__ == "__main__":
    Worker(*load()).run()
