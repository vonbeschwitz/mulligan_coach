"""Tests for :mod:`mulligan_coach_overlay.auto_update.manifest`.

Covers happy-path parsing, forward-compat behaviour (unknown
artifact kinds get dropped, unknown top-level fields ignored), and
the must-fail validation cases.
"""

from __future__ import annotations

import json

import pytest
from mulligan_coach_overlay.auto_update.manifest import (
    AUTO_UPDATE_SCHEMA_VERSION,
    ManifestParseError,
    parse_manifest,
)


def _valid_artifact(**overrides: object) -> dict:
    """Build a known-valid ratings artifact, optionally overriding fields."""
    base = {
        "kind": "ratings",
        "set_code": "TLA",
        "url": "https://example.invalid/TLA-PremierDraft.parquet",
        "sha256": "a" * 64,
        "version": "2026-05-22",
        "size_bytes": 105_432,
    }
    base.update(overrides)
    return base


def _valid_payload(**overrides: object) -> dict:
    """Build a known-valid manifest dict, optionally overriding fields."""
    base = {
        "schema_version": AUTO_UPDATE_SCHEMA_VERSION,
        "generated_at": "2026-05-23T12:00:00Z",
        "artifacts": [_valid_artifact()],
    }
    base.update(overrides)
    return base


def test_parse_valid_manifest_round_trip() -> None:
    """Happy path: well-formed JSON returns a populated Manifest."""
    payload = _valid_payload(
        artifacts=[
            _valid_artifact(kind="ratings", set_code="TLA"),
            _valid_artifact(kind="parsed_cards", set_code="TMT", sha256="b" * 64),
            {
                "kind": "model",
                "name": "choice_v6",
                "url": "https://example.invalid/choice_v6.zip",
                "sha256": "c" * 64,
                "version": "2026-04-30",
            },
        ]
    )

    manifest = parse_manifest(json.dumps(payload))

    assert manifest.schema_version == AUTO_UPDATE_SCHEMA_VERSION
    assert len(manifest.artifacts) == 3
    ratings, parsed, model = manifest.artifacts
    assert ratings.kind == "ratings" and ratings.set_code == "TLA"
    assert parsed.kind == "parsed_cards" and parsed.set_code == "TMT"
    assert model.kind == "model" and model.name == "choice_v6"
    # Identity labels are stable for log lines.
    assert ratings.label() == "ratings:TLA"
    assert model.label() == "model:choice_v6"


def test_unknown_kind_is_dropped_not_raised() -> None:
    """Forward-compat: a new manifest with a future kind shouldn't crash."""
    payload = _valid_payload(
        artifacts=[
            _valid_artifact(kind="ratings", set_code="TLA"),
            {
                "kind": "opponent_cache",  # hypothetical future kind
                "url": "https://example.invalid/x",
                "sha256": "d" * 64,
                "version": "v1",
            },
        ]
    )
    manifest = parse_manifest(json.dumps(payload))
    assert len(manifest.artifacts) == 1
    assert manifest.artifacts[0].kind == "ratings"


def test_unknown_top_level_field_tolerated() -> None:
    """Adding new top-level keys doesn't break old clients."""
    payload = _valid_payload(notes="experimental release with new metadata")
    manifest = parse_manifest(json.dumps(payload))
    assert manifest.schema_version == AUTO_UPDATE_SCHEMA_VERSION


def test_newer_schema_version_rejected() -> None:
    """A schema bump beyond what this client knows must refuse to parse.

    Better to fail loudly than to misparse the fields we don't
    understand yet — the overlay will keep using its previously-
    downloaded data and the next EXE update fixes things.
    """
    payload = _valid_payload(schema_version=AUTO_UPDATE_SCHEMA_VERSION + 1)
    with pytest.raises(ManifestParseError, match="newer than this client"):
        parse_manifest(json.dumps(payload))


def test_malformed_json_raises() -> None:
    with pytest.raises(ManifestParseError, match="not valid JSON"):
        parse_manifest("{not json")


def test_top_level_array_rejected() -> None:
    """JSON that decodes to a list (not dict) fails validation."""
    with pytest.raises(ManifestParseError, match="top-level must be an object"):
        parse_manifest(json.dumps([{"kind": "ratings"}]))


def test_missing_required_artifact_field_raises() -> None:
    """Known-kind artifacts must carry every required field."""
    bad = _valid_artifact()
    del bad["url"]
    with pytest.raises(ManifestParseError, match="missing 'url'"):
        parse_manifest(json.dumps(_valid_payload(artifacts=[bad])))


def test_invalid_sha256_format_raises() -> None:
    """SHA must be 64 hex chars; anything else is almost certainly a typo."""
    with pytest.raises(ManifestParseError, match="invalid 'sha256'"):
        parse_manifest(
            json.dumps(_valid_payload(artifacts=[_valid_artifact(sha256="not-a-real-sha")]))
        )


def test_set_code_required_for_per_set_kinds() -> None:
    bad = _valid_artifact()
    del bad["set_code"]
    with pytest.raises(ManifestParseError, match="set_code"):
        parse_manifest(json.dumps(_valid_payload(artifacts=[bad])))


def test_name_required_for_model_kind() -> None:
    bad = {
        "kind": "model",
        "url": "https://example.invalid/x.zip",
        "sha256": "e" * 64,
        "version": "v1",
    }
    with pytest.raises(ManifestParseError, match="name"):
        parse_manifest(json.dumps(_valid_payload(artifacts=[bad])))


def test_uppercase_sha256_accepted_and_normalised() -> None:
    """Either case in the manifest is fine; we store lowercase."""
    payload = _valid_payload(artifacts=[_valid_artifact(sha256="A" * 64)])
    manifest = parse_manifest(json.dumps(payload))
    assert manifest.artifacts[0].sha256 == "a" * 64


def test_set_code_uppercased() -> None:
    """Set codes are case-normalised so manifests / data dirs agree."""
    payload = _valid_payload(artifacts=[_valid_artifact(set_code="tla")])
    manifest = parse_manifest(json.dumps(payload))
    assert manifest.artifacts[0].set_code == "TLA"


def test_negative_size_bytes_rejected() -> None:
    with pytest.raises(ManifestParseError, match="size_bytes"):
        parse_manifest(json.dumps(_valid_payload(artifacts=[_valid_artifact(size_bytes=-1)])))


# ---------------------------------------------------------------------------
# Path-traversal rejection (defence against a hostile manifest publisher).
# A compromised manifest source must not be able to plant attacker-controlled
# bytes outside the overlay's user-data dirs via ``set_code`` / ``name``
# components that contain separators, traversal tokens, or drive letters.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_set_code",
    [
        "../../etc/passwd",
        "..",
        "TLA/foo",
        "TLA\\foo",
        "C:TLA",
        "TLA ",
        "TLA.json",
        "",
        "X" * 16,  # too long
    ],
)
def test_unsafe_set_code_rejected(bad_set_code: str) -> None:
    """Path-separator / traversal / drive-letter values fail at parse time."""
    payload = _valid_payload(artifacts=[_valid_artifact(set_code=bad_set_code)])
    with pytest.raises(ManifestParseError):
        parse_manifest(json.dumps(payload))


@pytest.mark.parametrize(
    "bad_name",
    [
        "../../AppData/Roaming/evil",
        "..",
        "choice_v6/../evil",
        "choice/v6",
        "choice\\v6",
        "C:choice_v6",
        "-leading-hyphen",
        ".hidden",
        "choice v6",
        "",
        "x" * 128,  # too long
    ],
)
def test_unsafe_model_name_rejected(bad_name: str) -> None:
    """Model names with separators or traversal tokens fail at parse time."""
    payload = _valid_payload(
        artifacts=[
            {
                "kind": "model",
                "name": bad_name,
                "url": "https://example.invalid/x.zip",
                "sha256": "e" * 64,
                "version": "v1",
            }
        ]
    )
    with pytest.raises(ManifestParseError):
        parse_manifest(json.dumps(payload))


@pytest.mark.parametrize("safe_set_code", ["TLA", "TMT", "ECL", "SOS", "P22", "MH2", "10", "Y26"])
def test_safe_set_codes_accepted(safe_set_code: str) -> None:
    """All real-world Magic set code shapes still parse."""
    payload = _valid_payload(artifacts=[_valid_artifact(set_code=safe_set_code)])
    manifest = parse_manifest(json.dumps(payload))
    assert manifest.artifacts[0].set_code == safe_set_code


@pytest.mark.parametrize("safe_name", ["choice_v6", "win_v1", "choice-v6", "model_2026_05", "x"])
def test_safe_model_names_accepted(safe_name: str) -> None:
    """Reasonable model names still parse."""
    payload = _valid_payload(
        artifacts=[
            {
                "kind": "model",
                "name": safe_name,
                "url": "https://example.invalid/x.zip",
                "sha256": "e" * 64,
                "version": "v1",
            }
        ]
    )
    manifest = parse_manifest(json.dumps(payload))
    assert manifest.artifacts[0].name == safe_name


def test_size_bytes_optional() -> None:
    artifact = _valid_artifact()
    del artifact["size_bytes"]
    manifest = parse_manifest(json.dumps(_valid_payload(artifacts=[artifact])))
    assert manifest.artifacts[0].size_bytes is None
