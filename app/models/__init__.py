"""Model registry.

Importing this package imports every model, which is what populates
``Base.metadata``. Alembic's ``env.py`` relies on that: a model not reachable
from here is invisible to autogenerate and will be silently omitted from
migrations.
"""

from app.models.agent_run import AgentRun
from app.models.base import Base
from app.models.business import Business
from app.models.business_rule import BusinessRule
from app.models.business_admin import BusinessAdmin
from app.models.business_entitlement import BusinessEntitlement
from app.models.conversation import Conversation, Message
from app.models.credential import ProductCredential
from app.models.customer import Customer, CustomerChannel
from app.models.entitlement_audit_log import EntitlementAuditLog
from app.models.feature_request import FeatureRequest
from app.models.fulfillment import Fulfillment
from app.models.knowledge import Knowledge
from app.models.order import Order, OrderItem
from app.models.payment import Payment
from app.models.plan_definition import PlanDefinition
from app.models.product import Product
from app.models.support_ticket import SupportTicket
from app.models.webhook_event import WebhookEvent

__all__ = [
    "AgentRun",
    "Base",
    "Business",
    "BusinessAdmin",
    "BusinessRule",
    "BusinessEntitlement",
    "Conversation",
    "Customer",
    "CustomerChannel",
    "EntitlementAuditLog",
    "FeatureRequest",
    "Fulfillment",
    "Knowledge",
    "Message",
    "Order",
    "OrderItem",
    "Payment",
    "PlanDefinition",
    "Product",
    "ProductCredential",
    "SupportTicket",
    "WebhookEvent",
]
