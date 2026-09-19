"""A price with the facts that make it mean something.

A bare float cannot say whether it is pounds or pence, or whether the session
it came from has closed. Six review rounds found the same defect in six
places: a provider price reaching storage or valuation with its unit left
behind, where 4,208 pence is a perfectly valid number that happens to be a
hundred times the truth.

`Quote` exists so that cannot be written. It is constructed at the provider
boundary and normalises there, so holding one means the unit is already known
and applied. A later reader has nothing to forget.
"""
from dataclasses import dataclass
from datetime import date
from typing import Optional

from money import Money

# Minor unit -> (major currency, scale). GBP is deliberately absent: it is the
# major unit, and scaling it divides genuine pound prices by a hundred.
MINOR_UNITS = {"GBp": ("GBP", 0.01), "ZAc": ("ZAR", 0.01), "ILA": ("ILS", 0.01)}


def as_major(price, currency):
    """`price` in the major unit of `currency`, with that currency's code."""
    if price is None:
        return None, currency
    major, scale = MINOR_UNITS.get(currency, (currency, 1.0))
    return price * scale, major


@dataclass(frozen=True)
class Quote:
    """A provider price, already in its currency's major unit.

    `session` is the trading day the price belongs to, and `settled` whether
    that day has closed. An unsettled quote is fine to value with - a
    dashboard wants the live number - but must not be written into a series
    as that day's close, because nothing corrects it afterwards.
    """
    price: float
    currency: str
    session: Optional[date] = None
    settled: bool = False

    @classmethod
    def from_provider(cls, price, unit, session=None, today=None):
        """Build from what a provider returned, or None if it cannot be read.

        Returns None where the unit is unknown: an unscaled price is
        indistinguishable from a scaled one, and guessing has been the single
        most expensive assumption in this codebase.
        """
        if price is None or not unit:
            return None
        value, currency = as_major(float(price), unit)
        settled = bool(session) and session < (today or date.today())
        return cls(price=value, currency=currency, session=session,
                   settled=settled)

    @property
    def money(self):
        """This price as an amount in its own currency."""
        return Money(self.price, self.currency)

    def converted(self, base, fx, on=None):
        """This price in `base` as a `Converted`, or None without a rate.

        A `Converted` rather than a float, for the same reason this class
        exists at all: the rate that produced a number is part of what the
        number means, and a reader who has only the float cannot recover it.
        """
        return fx.exchange(self.money, base, on=on)
