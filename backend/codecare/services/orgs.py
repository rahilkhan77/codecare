from __future__ import annotations

import re

from sqlalchemy.orm import Session

from codecare.models import Organization, OrganizationMember, Profile, Subscription, new_id

SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    slug = SLUG_RE.sub("-", value.lower()).strip("-")
    return (slug or "workspace")[:48]


def bootstrap_org(db: Session, profile: Profile) -> Organization:
    org = Organization(
        id=new_id(),
        name=f"{profile.full_name or profile.email}'s workspace",
        slug=f"{slugify(profile.email.split('@')[0])}-{profile.id[:8]}",
        owner_id=profile.id,
    )
    db.add(profile)
    db.flush()
    db.add(org)
    db.add(OrganizationMember(id=new_id(), organization_id=org.id, profile_id=profile.id, role="owner"))
    db.add(Subscription(id=new_id(), organization_id=org.id, plan="none", status="inactive"))
    db.flush()
    return org
