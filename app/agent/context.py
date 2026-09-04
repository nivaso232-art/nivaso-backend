"""Tool execution context.

This is where **rule 4** is enforced. ``business_id``, ``customer_id`` and
``conversation_id`` live here - resolved server-side from the verified webhook -
and are injected into every tool call. They are *not* parameters on any tool's
JSON schema, so the model has no channel through which to name a different
tenant, customer, or conversation. Asking it nicely to stay in its lane is not
the mechanism; not giving it a steering wheel is.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from functools import cached_property

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import Business
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.services.catalog_service import CatalogService
from app.services.conversation_service import ConversationService
from app.services.credential_service import CredentialService
from app.services.fulfillment_service import FulfillmentService
from app.services.knowledge_service import KnowledgeService
from app.services.order_service import OrderService
from app.services.payment_service import PaymentService
from app.services.support_service import SupportService


@dataclass
class ToolContext:
    """Everything a tool is allowed to know about who it is acting for.

    Services are built lazily via ``cached_property`` so a turn that only calls
    ``search_products`` does not construct the payment stack.
    """

    session: AsyncSession
    business: Business
    customer: Customer
    conversation: Conversation

    # Set by the runner so tools can attach notes the reply should mention.
    side_effects: list[str] = field(default_factory=list)

    @property
    def business_id(self) -> uuid.UUID:
        return self.business.id

    @property
    def customer_id(self) -> uuid.UUID:
        return self.customer.id

    @property
    def conversation_id(self) -> uuid.UUID:
        return self.conversation.id

    # -- services ---------------------------------------------------------

    @cached_property
    def catalog(self) -> CatalogService:
        return CatalogService(self.session, self.business_id)

    @cached_property
    def orders(self) -> OrderService:
        return OrderService(self.session, self.business_id)

    @cached_property
    def payments(self) -> PaymentService:
        return PaymentService(self.session, self.business_id)

    @cached_property
    def knowledge(self) -> KnowledgeService:
        return KnowledgeService(self.session, self.business_id)

    @cached_property
    def support(self) -> SupportService:
        return SupportService(self.session, self.business_id)

    @cached_property
    def fulfillment(self) -> FulfillmentService:
        return FulfillmentService(self.session, self.business_id)

    @cached_property
    def credentials(self) -> CredentialService:
        return CredentialService(self.session, self.business_id)

    @cached_property
    def conversations(self) -> ConversationService:
        return ConversationService(self.session, self.business_id)

    def note(self, message: str) -> None:
        """Record something the caller may want to act on after the turn."""
        self.side_effects.append(message)


@dataclass
class SuperAdminContext:
    """Minimal context for super-admin AI tools.

    Super-admin tools operate at the platform level — they query and mutate
    businesses, plans, and entitlements without being scoped to any single
    tenant.  They receive this context instead of ``ToolContext`` so they
    have no accidentally-exposed tenant steering wheel.
    """

    session: AsyncSession
    performed_by: str = "super-admin-ai"
