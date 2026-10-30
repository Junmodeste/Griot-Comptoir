"""Check that MODEL_API_KEY, the base URL and the model answer.

Usage: python scripts/check_connection.py
"""

import time

from griot.client import MetaClient


def main() -> None:
    client = MetaClient.from_env()
    start = time.perf_counter()
    reply = client.ping()
    elapsed = time.perf_counter() - start
    print(f"model={client.config.model} base_url={client.config.base_url}")
    print(f"reply={reply!r} latency={elapsed:.2f}s")


if __name__ == "__main__":
    main()
