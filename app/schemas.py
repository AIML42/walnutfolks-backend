"""Request/response validation schemas (Pydantic v2)."""

from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class WebhookIn(BaseModel):
    """Incoming webhook payload from the payment processor."""

    transaction_id: str = Field(min_length=1, max_length=128)
    source_account: str = Field(min_length=1, max_length=128)
    destination_account: str = Field(min_length=1, max_length=128)
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def uppercase_currency(cls, v: str) -> str:
        return v.upper()

    @field_validator(
        "transaction_id", "source_account", "destination_account"
    )
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class WebhookAccepted(BaseModel):
    """Body returned with the 202 acknowledgement."""

    transaction_id: str
    status: str  # "ACCEPTED" (new) or "DUPLICATE" (already seen)


class HealthResponse(BaseModel):
    status: str
    current_time: str
