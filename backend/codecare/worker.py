from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from codecare.db import SessionLocal
from codecare.logging_config import configure_logging
from codecare.models import MaintenanceJob
from codecare.services.jobs import run_job, schedule_due_scans

log = logging.getLogger("codecare.worker")


async def process_once() -> int:
    db = SessionLocal()
    try:
        scheduled = schedule_due_scans(db)
        jobs = db.scalars(
            select(MaintenanceJob).where(MaintenanceJob.status == "queued").order_by(MaintenanceJob.created_at.asc()).limit(5)
        ).all()
        ids = [job.id for job in jobs]
    finally:
        db.close()
    for job_id in ids:
        session = SessionLocal()
        try:
            await run_job(session, job_id)
        except Exception:
            log.exception("Job %s failed", job_id)
        finally:
            session.close()
    return len(ids) + scheduled


async def worker_loop() -> None:
    ticks = 0
    while True:
        count = await process_once()
        ticks += 1
        await asyncio.sleep(0.4 if count else 2.0)


def main() -> None:
    from codecare.config import get_settings
    from codecare.db import Base, engine
    from codecare.production import validate_settings

    settings = get_settings()
    configure_logging(settings.app_env)
    validate_settings(settings)
    Base.metadata.create_all(bind=engine)
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()
