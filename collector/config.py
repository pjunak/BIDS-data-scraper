import os
import yaml

DEFAULT_PATH = os.environ.get("COLLECTOR_CONFIG", "/app/config.yaml")


def load(path: str | None = None) -> dict:
    with open(path or DEFAULT_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)
