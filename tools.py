"""
tools.py — The three real-world API calls the agent can use as "tools".

WHAT THIS FILE IS: plain Python functions that hit real APIs and return
plain Python data (tuples, lists, dicts). Nothing in this file talks to
OpenAI or knows anything about "agents" or "tool calls" — it's just three
ordinary functions that happen to be USED as tools by agent.py.

THE THREE FUNCTIONS, IN THE ORDER THE AGENT TYPICALLY CALLS THEM:
  1. geocode_destination()        -> turn a place name into coordinates
  2. search_points_of_interest()  -> find candidate places near those coordinates
  3. get_walking_time_matrix()    -> get real walking times between chosen places
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

    USES GEOAPIFY, NOT NOMINATIM. Nominatim rate-limits per SOURCE IP
    (~1 request/second), and Streamlit Community Cloud apps share a small,
    published pool of outbound IPs across the ENTIRE platform — every app
    hosted there, from every user, not just this one. Confirmed directly
    from Nominatim's own response body: a real 429 "Too many requests"
    persisted for over an hour of testing, and failed even on correctly-
    spelled, real destinations — not just typos. That ruled out "transient
    rate limit" as the explanation; it's ongoing saturation baked into
    using a free, IP-limited service from a heavily-shared hosting IP.

    Geoapify rate-limits per API KEY instead of per IP, so this app's
    usage is no longer entangled with unrelated traffic from every other
    app on the platform.

    Args:
        destination_name: free text like "Lisbon, Portugal" or just "Lisbon"
        max_retries: how many total attempts to make before giving up

    Returns:
        A tuple (latitude, longitude), both as plain Python floats.

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

            # Geoapify returns coordinates as [longitude, latitude] in
            # geometry.coordinates (standard GeoJSON order) — the reverse
            # of how we normally say "latitude, longitude" out loud.
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
    traveler's own stated interests as the literal search queries — not a
    generic hardcoded category list like "tourist attractions, museums,
    parks". This is what makes the results actually match what someone
    asked for instead of a one-size-fits-all set of results.

    HOW THE SEARCH ACTUALLY WORKS: Google Maps' search endpoint only
    accepts ONE query string per request — it can't take a list of
    interests in a single call. So this function splits interests_text on
    commas and runs one search per resulting term, then merges and
    de-duplicates everything into one combined list. A "restaurants"
    search is added automatically if food wasn't already mentioned, so
    there's always somewhere to eat in the itinerary regardless of what
    the traveler typed.

    Args:
        latitude, longitude: from geocode_destination()'s output — this
                              function has no way to know where to search
                              without them.
        interests_text: free text like "museums, viewpoints, local food".
                         Split on commas into individual search terms.
                         If left blank, falls back to a single generic
                         "top attractions" search.
        max_results: stop collecting new places once we hit this count,
                     to avoid burning extra SerpApi searches once we
                     already have plenty of candidates.

    Returns:
        A list of dicts, one per unique place found, each shaped like:
            {
                "place_id": "<Google's unique id for this place>",
                "name": "<place name, e.g. 'Castelo de São Jorge'>",
                "category": "<Google's category label, e.g. 'Historical landmark'>",
                "latitude": <float>,
                "longitude": <float>,
                "google_rating": <float>,  # 0.0-5.0 stars, 0 if unrated
            }
    """
    # SerpApi's Google Maps engine doesn't accept a plain radius parameter.
    # Instead you describe the map "viewport" you want results from, using
    # the format @latitude,longitude,zoomlevel — zoom 14 is roughly
    # equivalent to a 2-4 km radius around the center point, which is a
    # reasonable walking-trip scale for a single destination.
    map_viewport = f"@{latitude},{longitude},14z"

    # Turn "museums, viewpoints, local food" into
    # ["museums", "viewpoints", "local food"] — one search term per
    # comma-separated interest, with surrounding whitespace stripped and
    # any accidentally-empty entries (e.g. from a trailing comma) dropped.
    search_queries = [term.strip() for term in interests_text.split(",") if term.strip()]
    if not search_queries:
        # If the traveler left interests completely blank, fall back to a
        # sensible default rather than returning zero results.
        search_queries = ["top attractions"]

    # Always guarantee there's at least one food-related search, since an
    # itinerary with zero meal options isn't very useful — but don't add a
    # redundant search if the traveler already mentioned food/restaurants
    # themselves.
    if not any("restaurant" in query or "food" in query for query in search_queries):
        search_queries.append("restaurants")

    # Track which Google place_ids we've already added, since running
    # multiple searches (one per interest) commonly returns the same
    # popular place more than once — e.g. a famous viewpoint might show up
    # under both "viewpoints" and "top attractions".
    already_seen_place_ids = set()
    found_places = []

    for query in search_queries:
        # Stop making new API calls once we've already collected enough
        # candidates — no reason to spend more of the monthly search quota.
        if len(found_places) >= max_results:
            break

        response = requests.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google_maps",  # tells SerpApi which underlying search engine to use
                "q": query,                # the actual search term for this pass
                "ll": map_viewport,         # the map area to search within
                "type": "search",           # plain search results, not a single-place lookup
                "api_key": SERPAPI_KEY,
            },
            timeout=15,
        )
        response.raise_for_status()
        search_results = response.json()

        # Google Maps search results come back under the "local_results" key.
        for place in search_results.get("local_results", []):
            place_id = place.get("place_id")
            place_name = place.get("title")
            coordinates = place.get("gps_coordinates")

            # Defensive skip: occasionally SerpApi returns partial results
            # (e.g. an ad placement or a result missing coordinates) — skip
            # anything that doesn't have everything we need, and skip
            # duplicates we've already collected from an earlier query.
            if not place_name or not coordinates or not place_id or place_id in already_seen_place_ids:
                continue

            already_seen_place_ids.add(place_id)
            found_places.append({
                "place_id": place_id,
                "name": place_name,
                # Google's "type" field describes what kind of place this is
                # (e.g. "Museum", "Park"). If it's missing for some reason,
                # fall back to the search query itself as a rough category.
                "category": place.get("type", query),
                "latitude": coordinates.get("latitude"),
                "longitude": coordinates.get("longitude"),
                # Not every place has a rating (e.g. very small local spots) —
                # default to 0 rather than leaving this field missing.
                "google_rating": place.get("rating", 0),
            })

            if len(found_places) >= max_results:
                break  # stop mid-query too, not just between queries

    return found_places


def get_walking_time_matrix(places: list):
    """
    Get real walking times between every pair of places in the given list.

    WHY THIS MATTERS: without this, the model would have to guess distances
    from place names and general geography knowledge alone — which is
    unreliable and can produce itineraries that look fine on paper but
    involve an hour of unplanned walking between two "nearby-sounding"
    stops. This function grounds day-grouping and stop-ordering decisions
    in actual measured walking times.

    Args:
        places: a list of dicts, each of which must have at least
                'latitude' and 'longitude' keys. In practice, this is
                whatever subset of places the agent decided to pass in
                (see get_walking_time_matrix's tool description in
                schema.py — the agent is instructed to pass only its
                shortlist, not the full search results).

    Returns:
        A 2D list (a "matrix") of travel times in SECONDS, where
        matrix[i][j] is the time to walk from places[i] to places[j].
    """
    # OpenRouteService expects coordinates as [longitude, latitude] pairs 
    # note this is the REVERSE of how people normally say "latitude,
    # longitude" out loud, and a common source of silently-wrong results
    # if you get the order backwards (the request won't necessarily error,
    # it'll just compute distances for the wrong location).
    coordinate_pairs = [[place["longitude"], place["latitude"]] for place in places]

    response = requests.post(
        "https://api.openrouteservice.org/v2/matrix/foot-walking",  # "foot-walking" = walking directions, not driving
        json={
            "locations": coordinate_pairs,
            "metrics": ["duration"],  # we only need travel TIME, not distance in meters
        },
        headers={
            "Authorization": ORS_KEY,
            "Content-Type": "application/json",
        },
        timeout=15,
    )
    response.raise_for_status()

    # The response has several top-level keys; "durations" is the actual
    # 2D matrix of travel times in seconds that we care about.
    return response.json()["durations"]