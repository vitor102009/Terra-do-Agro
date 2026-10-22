from datetime import datetime, timezone

from app.extensions import db


def utcnow():
    """Data e hora atual em UTC sem fuso (padrão de armazenamento do banco)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)


Money = db.Numeric(14, 2, asdecimal=True)
Quantity = db.Numeric(14, 3, asdecimal=True)
