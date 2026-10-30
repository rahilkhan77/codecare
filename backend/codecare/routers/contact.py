from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from codecare.db import get_db
from codecare.models import ContactMessage, new_id
from codecare.schemas import ContactRequest

router = APIRouter(prefix="/api/v1", tags=["contact"])


@router.post("/contact")
def contact(payload: ContactRequest, db: Session = Depends(get_db)):
    db.add(
        ContactMessage(
            id=new_id(),
            name=payload.name,
            email=str(payload.email),
            topic=payload.topic,
            message=payload.message,
        )
    )
    db.commit()
    return {"ok": True, "detail": "Message stored. A teammate will follow up if email delivery is configured later."}
