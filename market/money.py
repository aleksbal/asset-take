"""Money amounts with their currency, and converted amounts with the rate
used."""
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class Money:
    """An amount in a stated currency."""
    amount: float
    currency: str

    def scaled(self, factor):
        """This amount multiplied by `factor`, in the same currency."""
        return Money(self.amount * factor, self.currency)


@dataclass(frozen=True)
class Converted:
    """An amount converted into `base` at `rate`.

    `on` is the date of the rate, or None for the current rate.
    """
    original: Money
    rate: float
    base: str
    on: Optional[date] = None

    @property
    def amount(self):
        return self.original.amount * self.rate

    @property
    def money(self):
        """The result as a `Money` in the base currency."""
        return Money(self.amount, self.base)


def total(converted, base):
    """The sum of `Converted` amounts as `Money` in `base`, or None if there
    are none."""
    items = [c for c in converted if c is not None]
    if not items:
        return None
    return Money(sum(c.amount for c in items), base)
