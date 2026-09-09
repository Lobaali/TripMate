"""
tools.py — The three real-world API calls the agent can use as "tools".
"""

import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

GEOAPIFY_KEY = os.environ["GEOAPIFY_KEY"]  # https://myprojects.geoapify.com (free tier: 3000 req/day)
SERPAPI_KEY = os.environ["SERPAPI_KEY"]    # https://serpapi.com/manage-api-key (free tier: 100 searches/month)
ORS_KEY = os.environ["ORS_KEY"]            # https://openrouteservice.org/dev/#/signup (free tier: 2000 req/day)


def geocode_destination(destination_name: str, max_retries: int = 2):
    """
    Turn a place name like 'Lisbon, Portugal' into (latitude, longitude).
    Uses Geoapify (per-key rate limit) rather than Nominatim (per-shared-IP
    rate limit, confirmed unreliable on Streamlit Community Cloud).

    Returns:
        A tuple (latitude, longitude).

    Raises:
        ValueError if Geoapify genuinely found nothing for this name.
        requests.exceptions.HTTPError if every retry still fails.
    """
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(
                "https://api.geoapify.com/v1/geocode/search",
                params={"text": destination_name, "limit": 1, "apiKey": GEOAPIFY_KEY},
                timeout=10,
            )
            response.raise_for_status()

            features = response.json().get("features", [])
            if not features:
                raise ValueError(f"Could not geocode '{destination_name}' — check the spelling or try a broader name.")

            longitude, latitude = features[0]["geometry"]["coordinates"]
            return float(latitude), float(longitude)

        except requests.exceptions.HTTPError as e:
            last_error = e
            is_rate_limited = e.response is not None and e.response.status_code == 429
            if is_rate_limited and attempt < max_retries:
                time.sleep(attempt)
                continue
            raise

    raise last_error


def search_points_of_interest(latitude: float, longitude: float, interests_text: str = "", max_results: int = 30):
    """
    Search for candidate places near (latitude, longitude), using the
    traveler's own stated interests as the literal search queries.

    NEW: tracks how many results EACH individual interest found, not just
    the combined total. Previously this function silently merged results
    from every interest into one list with no way to tell whether a
    specific interest (e.g. "beaches") found real matches or found
    nothing at all — meaning a landlocked destination with no beaches
    would just quietly produce a beach-free itinerary with no explanation.
    Now the agent can see exactly which interests came up empty, and is
    instructed (see schema.py's tool description and agent.py's
    SYSTEM_PROMPT) to say so honestly via unmatched_interests_note instead
    of silently dropping them.

    Args:
        latitude, longitude: from geocode_destination()'s output.
        interests_text: free text like "museums, viewpoints, local food".
        max_results: stop collecting new places once we hit this count.

    Returns:
        A dict shaped like:
            {
                "places": [ {...}, {...}, ... ],   # same shape as before
                "results_per_interest": {
                    "museums": 8,
                    "beaches": 0,     # <- zero means genuinely not found
                    "local food": 5,
                },
            }
    """
    map_viewport = f"@{latitude},{longitude},14z"

    search_queries = [term.strip() for term in interests_text.split(",") if term.strip()]
    if not search_queries:
        search_queries = ["top attractions"]

    if not any("restaurant" in query or "food" in query for query in search_queries):
        search_queries.append("restaurants")

    already_seen_place_ids = set()
    found_places = []
    results_per_interest = {}

    for query in search_queries:
        query_result_count = 0

        if len(found_places) < max_results:
            response = requests.get(
                "https://serpapi.com/search.json",
                params={
                    "engine": "google_maps",
                    "q": query,
                    "ll": map_viewport,
                    "type": "search",
                    "api_key": SERPAPI_KEY,
                },
                timeout=15,
            )
            response.raise_for_status()
            search_results = response.json()

            for place in search_results.get("local_results", []):
                place_id = place.get("place_id")
                place_name = place.get("title")
                coordinates = place.get("gps_coordinates")

                if not place_name or not coordinates or not place_id:
                    continue

                # Count this result toward the query even if it's a
                # duplicate we've already seen under another interest —
                # it's a real result FOR THIS QUERY, which is what we're
                # measuring. Only skip adding it to found_places twice.
                query_result_count += 1

                if place_id in already_seen_place_ids:
                    continue

                already_seen_place_ids.add(place_id)
                found_places.append({
                    "place_id": place_id,
                    "name": place_name,
                    "category": place.get("type", query),
                    "latitude": coordinates.get("latitude"),
                    "longitude": coordinates.get("longitude"),
                    "google_rating": place.get("rating", 0),
                })

        results_per_interest[query] = query_result_count

    return {
        "places": found_places,
        "results_per_interest": results_per_interest,
    }


def get_walking_time_matrix(places: list):
    """
    Get real walking times between every pair of places in the given list.

    Returns:
        A 2D list of travel times in SECONDS.
    """
    coordinate_pairs = [[place["longitude"], place["latitude"]] for place in places]

    response = requests.post(
        "https://api.openrouteservice.org/v2/matrix/foot-walking",
        json={
            "locations": coordinate_pairs,
            "metrics": ["duration"],
        },
        headers={
            "Authorization": ORS_KEY,
            "Content-Type": "application/json",
        },
        timeout=15,
    )
    response.raise_for_status()

    return response.json()["durations"]