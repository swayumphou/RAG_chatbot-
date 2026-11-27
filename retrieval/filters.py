"""Facet filters: year, program, supervisor, domain, tech stack.

- build_facet_filter() makes a Qdrant filter. It is passed INTO the search,
  so Qdrant filters before ranking. We never filter results in Python after
  the search, because that silently loses good results.
- facet_counts() counts projects with SQLite GROUP BY over the whole corpus,
  not over the current page of results.
- encode_facets() / decode_facets() turn facets into a short text for links.
  An empty dict means "Clear all".
"""

from __future__ import annotations

from urllib.parse import parse_qsl, quote, urlencode

from qdrant_client import models

from store.db import as_list

FACET_KEYS = ("year", "program", "supervisor_id", "domain", "tech_stack")


def build_facet_filter(facets) -> models.Filter | None:
    """{"year": 2024, "program": "DSE"} -> Qdrant Filter. Returns None if there are no facets."""
    conditions = []
    for key, value in (facets or {}).items():
        values = as_list(value)
        if not values:
            continue
        if key not in FACET_KEYS:
            raise ValueError(f"unknown facet: {key}")

        if key == "year":
            values = [int(v) for v in values]
        else:
            values = [str(v) for v in values]

        if key == "tech_stack" or len(values) > 1:
            match = models.MatchAny(any=values)  # any of the listed values
        else:
            match = models.MatchValue(value=values[0])
        conditions.append(models.FieldCondition(key=key, match=match))

    if not conditions:
        return None
    return models.Filter(must=conditions)  # every facet must match


def facet_counts(db, facets) -> dict[str, int]:
    """{"total": 12, "program=DSE": 8, "program=ITE": 4, ...} from SQLite GROUP BY."""
    return db.facet_counts(facets)


def encode_facets(facets) -> str:
    """{"year": 2024, "program": "DSE"} -> "year=2024&program=DSE"."""
    pairs = []
    for key in FACET_KEYS:
        values = as_list((facets or {}).get(key))
        if values:
            pairs.append((key, ",".join(str(v) for v in values)))
    return urlencode(pairs, quote_via=quote, safe=",")


def decode_facets(text: str) -> dict:
    """"year=2024&program=DSE" -> {"year": 2024, "program": "DSE"}. Unknown keys are ignored."""
    facets = {}
    for key, value in parse_qsl(text or ""):
        if key not in FACET_KEYS or not value:
            continue
        values = value.split(",")
        if key == "year":
            values = [int(v) for v in values if v.isdigit()]
        if not values:
            continue
        if key == "tech_stack" or len(values) > 1:
            facets[key] = values
        else:
            facets[key] = values[0]
    return facets
