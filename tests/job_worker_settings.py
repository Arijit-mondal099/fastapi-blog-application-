"""Worker entry point used by process-level tests with a fake ImageKit."""

import asyncio
import os
from pathlib import Path

from app.jobs import media_jobs


class _FakeImageKit:
    """Slow or instant fake so a worker can be killed mid-delete."""

    async def get_file_details(self, file_id: str):
        return None

    async def delete_file(self, file_id: str) -> None:
        start_marker = os.environ.get("TEST_IMAGEKIT_START")
        if start_marker:
            Path(start_marker).write_text("started", encoding="utf-8")
        delay = float(os.environ.get("TEST_IMAGEKIT_DELAY", "0"))
        if delay:
            await asyncio.sleep(delay)
        log_path = os.environ.get("TEST_IMAGEKIT_LOG")
        if log_path:
            with Path(log_path).open("a", encoding="utf-8") as log:
                log.write(f"delete:{file_id}\n")


media_jobs.get_imagekit_client = lambda: _FakeImageKit()

from app.jobs.worker import WorkerSettings  # noqa: E402

__all__ = ["WorkerSettings"]
