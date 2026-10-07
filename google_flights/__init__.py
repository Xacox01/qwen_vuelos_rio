"""Unofficial Google Flights scraper - no browser, no API key."""
from .core import Flights, SearchError, Blocked, build_tfs, parse_results

__all__ = ["Flights", "SearchError", "Blocked", "build_tfs", "parse_results", "search"]
__version__ = "0.1.0"


def search(origin, destination, date, return_date=None, cabin="economy", adults=1, children=0,
           max_stops=None, airlines=None, currency="USD", language="en", country="us"):
    """Return (flights, price_insights, google_flights_url) for one search."""
    return Flights().search(origin, destination, date, return_date, cabin,
                            {"adults": adults, "children": children}, max_stops, airlines, currency, language, country)