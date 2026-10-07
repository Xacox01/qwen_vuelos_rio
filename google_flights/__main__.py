import argparse
import json

from . import search


def main():
    p = argparse.ArgumentParser(prog="google_flights", description="Search Google Flights from the command line.")
    p.add_argument("origin"), p.add_argument("destination"), p.add_argument("date", help="YYYY-MM-DD")
    p.add_argument("--return-date"), p.add_argument("--cabin", default="economy", choices=["economy", "premium_economy", "business", "first"])
    p.add_argument("--adults", type=int, default=1), p.add_argument("--nonstop", action="store_true")
    p.add_argument("--currency", default="USD"), p.add_argument("--json", action="store_true", help="print raw JSON")
    a = p.parse_args()
    flights, ins, url = search(a.origin, a.destination, a.date, a.return_date, a.cabin, a.adults,
                               max_stops=0 if a.nonstop else None, currency=a.currency)
    if a.json:
        print(json.dumps({"flights": flights, "insights": ins, "url": url}, indent=2, ensure_ascii=False))
        return
    for f in sorted(flights, key=lambda x: x["price"] or 1e9):
        nums = ", ".join(l["flightNumber"] or "?" for l in f["legs"])
        print(f"{f['price']:>7} {a.currency}  {f['departure']} -> {f['arrival']}  {f['durationMinutes']:>4} min  stops={f['stops']}  {', '.join(f['airlines'])}  ({nums})")
    if ins:
        print(f"\nTypical price range: {ins.get('typicalPriceLow')} - {ins.get('typicalPriceHigh')} {a.currency} (today: {ins.get('priceLevel')})")
    print(url)


if __name__ == "__main__":
    main()