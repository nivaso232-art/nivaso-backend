"""Unit of Work - the explicit transaction boundary.

Rule: any code path that writes more than one row uses a UnitOfWork. Order
creation touches ``orders`` + ``order_items`` (+ sometimes ``payments``);
those must land together or not at all.

    async with UnitOfWork(session) as uow:
        order = await order_svc.create_order(...)
        await payment_svc.create_attempt(order)
        # commit on clean exit, rollback on any exception
"""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession


class UnitOfWork:
    """Wrap a session in a single atomic transaction.

    Nesting is supported: if the session already has a transaction in
    progress, this becomes a no-op passthrough so the outermost block owns the
    commit. That lets a service call another service without either of them
    needing to know who started the transaction.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._owns_transaction = False

    async def __aenter__(self) -> UnitOfWork:
        if not self.session.in_transaction():
            await self.session.begin()
            self._owns_transaction = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if not self._owns_transaction:
            return
        if exc_type is not None:
            await self.session.rollback()
        else:
            await self.session.commit()

    async def flush(self) -> None:
        """Push pending INSERTs so server-side defaults (ids) are available."""
        await self.session.flush()
