"""Transport-independent document confirmation for a single browser transaction."""
import asyncio
from collections.abc import Awaitable, Callable
import math

from fichaxebot.logging_config import get_logger

logger = get_logger(__name__)


class Confirmation:
    """Create and resolve on the event loop; pass confirm_from_worker to sync APIs.

    ``deliver`` displays the original document and returns after delivery. The
    caller owns presentation, authorization and cleanup. One instance per request.
    """

    def __init__(self, deliver: Callable[[bytes], Awaitable[None]], *, timeout=60,
                 delivery_failure='No se pudo entregar el documento. No se envió la solicitud a USC.'):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('El tiempo de confirmación debe ser mayor que cero y finito.')
        self.deliver = deliver
        self.timeout = timeout
        self.delivery_failure = delivery_failure
        self.loop = asyncio.get_running_loop()
        self.decision = self.loop.create_future()
        self.deadline = None
        self.reason = 'Solicitud cancelada. No se envió a USC.'

    def abort(self, reason):
        if not self.decision.done():
            self.reason = reason
            self.decision.set_result(False)

    def resolve(self, accepted: bool) -> bool:
        """Return whether this decision was accepted, after delivery and before expiry."""
        if self.deadline is None or self.decision.done():
            return False
        if self.loop.time() >= self.deadline:
            self.abort('Tiempo de confirmación agotado. No se envió la solicitud a USC.')
            return False
        self.decision.set_result(accepted is True)
        return True

    def confirm_from_worker(self, document: bytes) -> bool:
        return asyncio.run_coroutine_threadsafe(self.request_confirmation(document), self.loop).result()

    async def request_confirmation(self, document: bytes) -> bool:
        if self.decision.done():
            return self.decision.result()
        delivery = None
        try:
            delivery = asyncio.ensure_future(self.deliver(document))
            await asyncio.wait((delivery, self.decision), return_when=asyncio.FIRST_COMPLETED)
            if self.decision.done():
                return self.decision.result()
            await delivery
        except asyncio.CancelledError:
            self.abort('Solicitud cancelada. No se envió a USC.')
            raise
        except Exception:
            logger.exception('Could not deliver confirmation document')
            self.abort(self.delivery_failure)
            return False
        finally:
            if delivery is not None:
                if not delivery.done():
                    delivery.cancel()
                await asyncio.gather(delivery, return_exceptions=True)
        self.deadline = self.loop.time() + self.timeout
        try:
            return await asyncio.wait_for(asyncio.shield(self.decision), timeout=self.timeout)
        except TimeoutError:
            self.abort('Tiempo de confirmación agotado. No se envió la solicitud a USC.')
            return self.decision.result()

    async def run(self, operation, *args, **kwargs):
        """Run the whole sync transaction on one worker, retaining its lock ownership."""
        worker = asyncio.create_task(asyncio.to_thread(
            operation, *args, confirm=self.confirm_from_worker, **kwargs,
        ))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            self.abort('Solicitud cancelada al detener la tarea. No se envió a USC.')
            await self._join(worker)
            raise

    async def stop(self, task, reason='El bot se está apagando. No se envió la solicitud a USC.'):
        self.abort(reason)
        if task is not None:
            await self._join(task)

    @staticmethod
    async def _join(task):
        # Cancelling an await cannot stop a Selenium thread. Join it even if the
        # caller receives another cancellation while shutdown is in progress.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            try:
                task.result()
            except Exception:
                logger.exception('Confirmed transaction finished with an error while stopping')
