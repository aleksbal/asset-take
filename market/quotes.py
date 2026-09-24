"""A provider price normalised to its currency's major unit (pence to
pounds), with the trading session it belongs to."""
from dataclasses import dataclass
from datetime import date
from typing import Optional

from market.money import Money

# Minor unit -> (major currency, scale). GBP is the major unit and not listed.
MINOR_UNITS = {"GBp": ("GBP", 0.01), "ZAc": ("ZAR", 0.01), "ILA": ("ILS", 0.01)}


def as_major(price, currency):
    """`price` in the major unit of `currency`, with that currency's code."""
    if price is None:
        return None, currency
    major, scale = MINOR_UNITS.get(currency, (currency, 1.0))
    return price * scale, major


@dataclass(frozen=True)
class Quote:
    """A price in its currency's major unit.

    `session` is the trading day it belongs to; `settled` is whether that day
    has closed. Only settled quotes are written into a price series.
    """
    price: float
    currency: str
    session: Optional[date] = None
    settled: bool = False

    @classmethod
    def from_provider(cls, price, unit, session=None, today=None):
        """A Quote from a raw provider price and its unit, or None if either
        is missing. `today` overrides the date used to decide `settled`."""
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
        """This price in `base` as a `Converted`, or None without a rate."""
        return fx.exchange(self.money, base, on=on)
