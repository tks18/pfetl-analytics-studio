"""Locale-aware currency, percentage, ratio, and horizon formatting helpers.

Owns
----
- INR (`Cr` / `L` compact + Indian comma grouping) and ISO currency formatting.
- Fractional ratio-to-percentage formatting aligned with PFETL Gold numeric conventions.
- Ratio, months-of-runway, index, and ISO date string formatters.

Does not own
------------
- Financial computations or NiceGUI/Plotly widget construction.
"""

from __future__ import annotations

from datetime import date

from pfetl_analytics_studio.semantic.metrics import MetricFormat

_CURRENCY_SYMBOLS: dict[str, str] = {
    "INR_INR": "₹",
    "INR": "₹",
    "USD_USD": "$",
    "USD": "$",
    "EUR_EUR": "€",
    "EUR": "€",
    "GBP_GBP": "£",
    "GBP": "£",
}


def currency_symbol(currency_id: str | None) -> str:
    """Resolve short display symbol from PFETL `DEFAULT_CURRENCY_ID` tokens.

    Parameters
    ----------
    currency_id : str | None
        PFETL currency token such as `'INR_INR'` or `'USD_USD'`.

    Returns
    -------
    str
        Currency prefix symbol (defaults to `'₹'`).
    """
    if not currency_id:
        return "₹"
    token = currency_id.strip().upper()
    if token in _CURRENCY_SYMBOLS:
        return _CURRENCY_SYMBOLS[token]
    base = token.split("_", 1)[0]
    return _CURRENCY_SYMBOLS.get(base, f"{base} ")


def _format_indian_integer(abs_int: int) -> str:
    """Format a non-negative integer using Indian `12,34,56,789` grouping."""
    digits = str(abs_int)
    if len(digits) <= 3:
        return digits
    head = digits[:-3]
    tail = digits[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.append(head[-2:])
        head = head[:-2]
    if head:
        groups.append(head)
    groups.reverse()
    return ",".join(groups) + "," + tail


def format_currency(
    value: float | None,
    currency_id: str | None = "INR_INR",
    *,
    compact: bool = True,
    signed: bool = False,
    decimals: int = 2,
) -> str:
    """Format a monetary amount with optional Indian `Cr`/`L` compact scaling.

    Parameters
    ----------
    value : float | None
        Monetary figure to format.
    currency_id : str | None, optional
        PFETL currency identifier (`'INR_INR'`, `'USD_USD'`, etc.).
    compact : bool, optional
        When True, renders INR magnitudes using `Cr` / `L` (`10M`/`100K`) or `M`/`K`.
    signed : bool, optional
        When True, prefixes positive values with `'+'`.
    decimals : int, optional
        Decimal places used in compact scale notation.

    Returns
    -------
    str
        Formatted monetary string or `'—'` if missing.
    """
    if value is None:
        return "—"
    sym = currency_symbol(currency_id)
    sign = "-" if value < 0 else ("+" if signed and value > 0 else "")
    mag = abs(float(value))
    is_inr = sym == "₹"

    if compact:
        if is_inr:
            if mag >= 1e7:
                return f"{sign}{sym}{mag / 1e7:.{decimals}f} Cr"
            if mag >= 1e5:
                return f"{sign}{sym}{mag / 1e5:.{decimals}f} L"
            if mag >= 1e3:
                return f"{sign}{sym}{mag / 1e3:.1f}K"
        else:
            if mag >= 1e9:
                return f"{sign}{sym}{mag / 1e9:.{decimals}f}B"
            if mag >= 1e6:
                return f"{sign}{sym}{mag / 1e6:.{decimals}f}M"
            if mag >= 1e3:
                return f"{sign}{sym}{mag / 1e3:.1f}K"

    rounded = round(mag)
    if is_inr:
        grouped = _format_indian_integer(rounded)
    else:
        grouped = f"{rounded:,}"
    return f"{sign}{sym}{grouped}"


def format_percent(
    fraction: float | None,
    *,
    decimals: int = 1,
    signed: bool = False,
) -> str:
    """Format a fractional Gold percentage (`0.6752` -> `'67.5%'`).

    Parameters
    ----------
    fraction : float | None
        Decimal fraction value from Gold tables (e.g. `0.276` for `27.6%`).
    decimals : int, optional
        Decimal places in formatted percentage.
    signed : bool, optional
        Whether positive values receive an explicit `'+'` sign.

    Returns
    -------
    str
        Formatted percentage string or `'—'` when `None`.
    """
    if fraction is None:
        return "—"
    pct = float(fraction) * 100.0
    if signed:
        return f"{pct:+.{decimals}f}%"
    return f"{pct:,.{decimals}f}%"


def format_ratio(
    value: float | None,
    *,
    decimals: int = 2,
    suffix: str = "x",
) -> str:
    """Format a scalar multiplier or concentration score cleanly."""
    if value is None:
        return "—"
    return f"{float(value):,.{decimals}f}{suffix}"


def format_months(value: float | None, *, decimals: int = 1) -> str:
    """Format months-of-runway float cleanly (`88.4 mos`)."""
    if value is None:
        return "—"
    return f"{float(value):,.{decimals}f} mos"


def format_metric_value(
    value: float | date | str | None,
    fmt: MetricFormat,
    currency_id: str | None = "INR_INR",
    *,
    compact: bool = True,
    unit_suffix: str = "",
) -> str:
    """Format a typed metric value cleanly according to its `MetricFormat`.

    Parameters
    ----------
    value : float | date | str | None
        Raw scalar metric value.
    fmt : MetricFormat
        Metric display format classification.
    currency_id : str | None, optional
        Currency code used when `fmt == MetricFormat.CURRENCY`.
    compact : bool, optional
        Use compact notation for large currencies.
    unit_suffix : str, optional
        Optional suffix for `NUMBER`/`RATIO` formats (`' mos'`, `'x'`, etc.).

    Returns
    -------
    str
        Clean human-readable representation.
    """
    if value is None:
        return "Unavailable"
    if isinstance(value, date):
        return value.strftime("%b %Y")
    if isinstance(value, str):
        return value

    num = float(value)
    if fmt == MetricFormat.CURRENCY:
        return format_currency(num, currency_id=currency_id, compact=compact)
    if fmt == MetricFormat.PERCENT:
        return format_percent(num, decimals=1)
    if fmt == MetricFormat.RATIO:
        return format_ratio(num, decimals=2, suffix=unit_suffix or "x")
    if fmt == MetricFormat.INTEGER:
        return f"{int(round(num)):,}{unit_suffix}"
    if fmt == MetricFormat.DATE:
        return str(value)
    if unit_suffix == " mos":
        return format_months(num)
    return f"{num:,.2f}{unit_suffix}"
