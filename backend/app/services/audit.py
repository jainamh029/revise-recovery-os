from sqlalchemy.orm import Session

from ..models import AuditLog


def log(
    db: Session,
    entity_type: str,
    entity_id: str,
    action: str,
    old=None,
    new=None,
    user: str | None = "demo.user",
) -> None:
    db.add(
        AuditLog(
            user_id=user,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            old_value=old,
            new_value=new,
        )
    )
