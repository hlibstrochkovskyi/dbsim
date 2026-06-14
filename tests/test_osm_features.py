"""Tests for OSM micro-feature ingestion (signals, switches, …) used by Phase 3."""

from __future__ import annotations

from dbsim.ingest.osm import parse_railway_features, railway_features_query


def _node(osm_id: int, lat: float, lon: float, tags: dict[str, str]) -> dict[str, object]:
    return {"type": "node", "id": osm_id, "lat": lat, "lon": lon, "tags": tags}


def test_query_includes_all_feature_kinds() -> None:
    q = railway_features_query((48.0, 9.0, 48.1, 9.1))
    for kind in ("signal", "switch", "buffer_stop", "railway_crossing", "crossing", "derail"):
        assert kind in q


def test_parse_groups_features_by_kind() -> None:
    data = {
        "elements": [
            _node(
                1,
                48.5,
                9.0,
                {
                    "railway": "signal",
                    "railway:signal:main": "DE-ESO",
                    "railway:signal:direction": "forward",
                },
            ),
            _node(2, 48.5, 9.0, {"railway": "switch"}),
            _node(3, 48.5, 9.0, {"railway": "buffer_stop"}),
            _node(4, 48.5, 9.0, {"railway": "railway_crossing"}),
            _node(5, 48.5, 9.0, {"railway": "station"}),  # not a micro feature
        ]
    }
    features = parse_railway_features(data)
    assert features.counts() == {
        "signals": 1,
        "switches": 1,
        "buffer_stops": 1,
        "crossings": 1,
    }
    signal = features.signals[0]
    assert signal.signal_type == "main"
    assert signal.direction == "forward"


def test_signal_fractions() -> None:
    data = {
        "elements": [
            _node(
                1,
                48.5,
                9.0,
                {
                    "railway": "signal",
                    "railway:signal:main": "x",
                    "railway:signal:direction": "backward",
                },
            ),
            _node(
                2, 48.5, 9.0, {"railway": "signal", "railway:signal:direction": "forward"}
            ),  # typed? no
            _node(3, 48.5, 9.0, {"railway": "signal"}),  # bare
        ]
    }
    features = parse_railway_features(data)
    assert features.typed_signal_fraction() == 1 / 3  # only the first has a function type
    assert features.directional_signal_fraction() == 2 / 3  # first two have a direction


def test_empty_features() -> None:
    features = parse_railway_features({"elements": []})
    assert features.counts() == {"signals": 0, "switches": 0, "buffer_stops": 0, "crossings": 0}
    assert features.typed_signal_fraction() == 0.0
