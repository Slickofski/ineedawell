"""Stage 6 — Driller match: rank the researched shortlist by distance.

Reads drillers.json (hand-researched; emails harvested from each company's own
website — never guessed). Ranks by haversine distance from the parcel to the
driller's base town. Writes orders/<id>/drillers_ranked.json.
"""
import json
import math
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def haversine_miles(lat1, lon1, lat2, lon2):
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def rank_drillers(order: dict, geo: dict, order_dir: str, n: int = 5) -> dict:
    with open(os.path.join(BASE_DIR, "drillers.json")) as f:
        data = json.load(f)
    ranked = []
    for d in data["drillers"]:
        miles = haversine_miles(geo["lat"], geo["lon"], d["lat"], d["lon"])
        ranked.append({**d, "distance_miles": round(miles, 1)})
    ranked.sort(key=lambda d: d["distance_miles"])
    result = {
        "parcel": {"lat": geo["lat"], "lon": geo["lon"]},
        "drillers": ranked[:n],
        "provenance": data["provenance"],
    }
    with open(f"{order_dir}/drillers_ranked.json", "w") as f:
        json.dump(result, f, indent=2)
    return result
