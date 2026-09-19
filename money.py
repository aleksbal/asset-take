"""An amount of money, and what it becomes when converted.

`Quote` exists because a price that arrives without its unit is
indistinguishable from a correct one. A converted amount is the same defect one
level up: 236.41 does not say which rate produced it, or whether a rate was
applied at all. Seven call sites defaulted a missing rate to 1.0, which values
a dollar holding as though it were a euro one - wrong by whatever the rate
happens to be, and identical in appearance to a correct figure.

`Converted` keeps what the amount was, the rate applied and the day that rate
is from, so the arithmetic behind a displayed number can be read back instead
of inferred. Holding one means the conversion already happened and is on
record; there is nothing left for a later reader to assume.
"""
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class Money:
    """An amount in a stated currency.

    The currency is not optional and is not defaulted. A cost basis is
    denominated in whatever the broker charged in, which is not always what
    the listing quotes in, and the difference is the whole reason this type
    exists.
    """
    amount: float
    currency: str

    def scaled(self, factor):
        """This amount multiplied by `factor`, currency unchanged.

        Quantity times price, which is the only multiplication a money amount
        takes part in: multiplying two amounts is meaningless and adding two
        in different currencies is what `Converted` is for.
        """
        return Money(self.amount * factor, self.currency)


@dataclass(frozen=True)
class Converted:
    """An amount expressed in another currency, with its working shown.

    `on` is the day the rate is from, or None for the current rate. A cost
    basis converted at today's rate is not the same claim as one converted at
    the rate on the purchase date, and a reader cannot tell them apart from
    the figure alone.
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
    """The sum of several `Converted` amounts, or None if there are none.

    An empty sum is zero, and a portfolio whose cost basis is unknown reported
    `+0` of unrealised P&L - an unknown dressed as a certainty. Aggregate only
    over what actually contributed, and let the caller render the absence.
    """
    items = [c for c in converted if c is not None]
    if not items:
        return None
    return Money(sum(c.amount for c in items), base)
