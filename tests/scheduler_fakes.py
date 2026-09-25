from types import SimpleNamespace
from unittest.mock import AsyncMock


class FakeJob:
    def __init__(self, callback, data, name, **timing):
        self.callback = callback
        self.data = data
        self.name = name
        self.timing = timing
        self.removed = False

    def schedule_removal(self):
        self.removed = True


class FakeJobQueue:
    """Records jobs the way PTB's JobQueue is called; nothing runs until fire()."""

    def __init__(self):
        self.jobs = []

    def _add(self, kind, callback, data, name, **timing):
        job = FakeJob(callback, data, name, kind=kind, **timing)
        self.jobs.append(job)
        return job

    def run_once(self, callback, when, data=None, name=None, job_kwargs=None):
        return self._add("once", callback, data, name, when=when)

    def run_daily(self, callback, time, days=tuple(range(7)), data=None, name=None, job_kwargs=None):
        return self._add("daily", callback, data, name, time=time, days=days)

    def run_repeating(self, callback, interval, first=None, data=None, name=None, job_kwargs=None):
        return self._add("repeating", callback, data, name, interval=interval, first=first)

    def of(self, kind):
        return [job for job in self.jobs if job.timing["kind"] == kind and not job.removed]


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def fake_app():
    return SimpleNamespace(job_queue=FakeJobQueue(), bot=SimpleNamespace(send_message=AsyncMock()), bot_data={})


async def fire(app, job):
    """Run a recorded job the way PTB would: callback(context)."""
    await job.callback(SimpleNamespace(job=job, application=app, bot=app.bot, job_queue=app.job_queue))
