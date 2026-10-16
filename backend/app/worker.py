"""Background worker: runs queued jobs and periodic tasks (planning, publishing, crawling,
token refresh, metrics, notifications). Start with:  python -m app.worker
"""

import asyncio
import logging
import signal

from app.modules import load_all
from app.services.jobs import Worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def main() -> None:
    load_all()
    worker = Worker(concurrency=3)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.stop)
        except (NotImplementedError, RuntimeError):  # Windows
            pass
    await worker.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
