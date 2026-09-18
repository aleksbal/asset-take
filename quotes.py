"""Quote units.

Some venues quote a minor unit: London reports pence under the code `GBp`,
Johannesburg cents under `ZAc`. A price in those units is a hundredth of the
currency it names, and valuation downstream has no idea - an unrecognised code
is given an exchange rate of 1.0, so a 4,208 pence share is valued as 4,208
pounds.

This lives on its own because the first attempt at it did not. Resolution
normalised its own prices while the valuation layer downloaded its own quotes
and bypassed the scaling entirely, so the stored row was right and every
snapshot was still a hundredfold out. Any layer that reads a price from the
provider converts it here first.
"""

# Minor unit -> (major currency, scale). GBP is deliberately absent: it is the
# major unit, and scaling it divides genuine pound prices by a hundred.
MINOR_UNITS = {"GBp": ("GBP", 0.01), "ZAc": ("ZAR", 0.01), "ILA": ("ILS", 0.01)}


def as_major(price, currency):
    """`price` in the major unit of `currency`, with that currency's code."""
    if price is None:
        return None, currency
    major, scale = MINOR_UNITS.get(currency, (currency, 1.0))
    return price * scale, major
