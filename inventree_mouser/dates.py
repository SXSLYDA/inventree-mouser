"""
The "Import orders placed from" setting's date: DD-MM-YYYY (e.g. 24-10-2026).

Kept free of InvenTree imports so the unit tests can check it; the
validator only imports Django's ValidationError when it runs.
"""

from datetime import datetime

START_DATE_FORMAT = '%d-%m-%Y'
START_DATE_HELP = 'DD-MM-YYYY, e.g. 24-10-2026'


def parse_start_date(text):
    """A DD-MM-YYYY setting value as a date, else None (blank or not a real date)."""
    text = (text or '').strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, START_DATE_FORMAT).date()
    except ValueError:
        return None


def validate_start_date(value):
    """Setting validator: blank, or a real DD-MM-YYYY date - anything else is refused when saved."""
    if str(value or '').strip() and parse_start_date(str(value)) is None:
        from django.core.exceptions import ValidationError
        raise ValidationError(f"'{value}' isn't a date - use {START_DATE_HELP}.")


def format_date(iso):
    """An ISO date (YYYY-MM-DD, as Mouser's dates are stored) shown as DD-MM-YYYY; other text as it is."""
    try:
        return datetime.strptime(str(iso)[:10], '%Y-%m-%d').strftime(START_DATE_FORMAT)
    except ValueError:
        return str(iso or '')
