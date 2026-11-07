"""Dodo Payments subscriptions. Prices live on dashboard products, in USD."""

from __future__ import annotations

from dodopayments import DodoPayments, DodoPaymentsError

from codecare.config import Settings, get_settings

PLAN_RANK = {"starter": 1, "indie": 2, "studio": 3}


class DodoError(RuntimeError):
    pass


class DodoNotConfigured(DodoError):
    pass


def product_id(plan: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    ids = {
        "starter": settings.dodo_product_starter,
        "indie": settings.dodo_product_indie,
        "studio": settings.dodo_product_studio,
    }
    value = ids.get(plan, "")
    if plan not in ids or not value:
        raise DodoNotConfigured(f"Dodo Payments product id for {plan} is not configured.")
    return value


def plan_from_product(product: str | None, settings: Settings | None = None) -> str | None:
    if not product:
        return None
    settings = settings or get_settings()
    mapping = {
        settings.dodo_product_starter: "starter",
        settings.dodo_product_indie: "indie",
        settings.dodo_product_studio: "studio",
    }
    return mapping.get(product)


def _client(settings: Settings) -> DodoPayments:
    if not settings.dodo_payments_api_key:
        raise DodoNotConfigured("Dodo Payments API key is not configured.")
    if settings.dodo_payments_environment not in {"test_mode", "live_mode"}:
        raise DodoError("DODO_PAYMENTS_ENVIRONMENT must be test_mode or live_mode.")
    return DodoPayments(
        bearer_token=settings.dodo_payments_api_key,
        environment=settings.dodo_payments_environment,
        webhook_key=settings.dodo_payments_webhook_secret or None,
    )


def _call(settings: Settings, action):
    client = _client(settings)
    try:
        return action(client)
    except DodoNotConfigured:
        raise
    except DodoPaymentsError as exc:
        raise DodoError(f"Dodo Payments request failed: {str(exc)[:300]}") from exc
    finally:
        client.close()


def create_checkout(
    *,
    plan: str,
    organization_id: str,
    email: str,
    name: str,
    return_url: str,
    settings: Settings | None = None,
) -> dict:
    settings = settings or get_settings()
    chosen = product_id(plan, settings)

    def action(client: DodoPayments):
        session = client.checkout_sessions.create(
            product_cart=[{"product_id": chosen, "quantity": 1}],
            customer={"email": email, "name": name or email},
            return_url=return_url,
            cancel_url=return_url,
            metadata={"organization_id": organization_id, "plan": plan},
        )
        return {"checkout_url": session.checkout_url, "session_id": session.session_id}

    return _call(settings, action)


def change_plan(
    *,
    subscription_id: str,
    plan: str,
    immediate: bool,
    organization_id: str,
    settings: Settings | None = None,
) -> dict:
    """Upgrades bill the difference now. Downgrades start on the next billing date."""
    settings = settings or get_settings()
    chosen = product_id(plan, settings)

    def action(client: DodoPayments):
        response = client.subscriptions.change_plan(
            subscription_id,
            product_id=chosen,
            quantity=1,
            proration_billing_mode="difference_immediately" if immediate else "full_immediately",
            effective_at="immediately" if immediate else "next_billing_date",
            metadata={"organization_id": organization_id, "plan": plan},
        )
        return {"payment_link": response.payment_link}

    return _call(settings, action)


def cancel_at_next_billing_date(subscription_id: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()

    def action(client: DodoPayments):
        client.subscriptions.update(subscription_id, cancel_at_next_billing_date=True)

    _call(settings, action)


def create_portal(*, customer_id: str, return_url: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()

    def action(client: DodoPayments):
        session = client.customers.customer_portal.create(customer_id, return_url=return_url)
        return session.link

    return _call(settings, action)
