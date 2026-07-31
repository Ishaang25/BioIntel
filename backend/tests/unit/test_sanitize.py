"""NUL-byte scrubbing.

PostgreSQL rejects ``U+0000`` in ``text`` and in ``jsonb``; SQLite accepts it
silently. These tests therefore assert the *invariant PostgreSQL requires* --
that no NUL survives to the driver -- rather than asserting that an insert
succeeds, which it would on SQLite either way.
"""

from __future__ import annotations

from app.core.enums import ClaimType
from app.db.sanitize import contains_nul, scrub_nul


class TestScrubNul:
    def test_strips_nul_from_a_string(self) -> None:
        assert scrub_nul("Phase\x003 trial") == "Phase3 trial"

    def test_strips_every_occurrence(self) -> None:
        assert scrub_nul("\x00a\x00b\x00") == "ab"

    def test_recurses_into_lists(self) -> None:
        assert scrub_nul(["a\x00", "b"]) == ["a", "b"]

    def test_recurses_into_dicts_including_keys(self) -> None:
        assert scrub_nul({"k\x00ey": "val\x00ue"}) == {"key": "value"}

    def test_recurses_into_nested_json_shapes(self) -> None:
        # The shape `claims.quantitative` actually holds.
        given = [{"metric": "ORR\x00", "value": 42, "units": None, "tags": ["a\x00", "b"]}]
        assert scrub_nul(given) == [
            {"metric": "ORR", "value": 42, "units": None, "tags": ["a", "b"]}
        ]

    def test_leaves_non_strings_alone(self) -> None:
        for value in (None, 42, 3.14, True):
            assert scrub_nul(value) is value

    def test_returns_clean_strings_unchanged_by_identity(self) -> None:
        # The fast path: no copy when there is nothing to do.
        value = "already clean"
        assert scrub_nul(value) is value

    def test_preserves_str_enum_members(self) -> None:
        """A ``StrEnum`` is a ``str``; rebuilding one would break identity."""
        assert scrub_nul(ClaimType.OTHER) is ClaimType.OTHER

    def test_handles_tuples_and_sets(self) -> None:
        assert scrub_nul(("a\x00",)) == ("a",)
        assert scrub_nul({"a\x00"}) == {"a"}

    def test_empty_string_survives(self) -> None:
        assert scrub_nul("") == ""

    def test_a_string_of_only_nuls_becomes_empty(self) -> None:
        assert scrub_nul("\x00\x00") == ""


class TestContainsNul:
    def test_detects_at_every_depth(self) -> None:
        assert contains_nul("a\x00")
        assert contains_nul(["ok", {"k": ["deep\x00"]}])
        assert contains_nul({"k\x00": "v"})

    def test_false_for_clean_values(self) -> None:
        assert not contains_nul("clean")
        assert not contains_nul(["clean", {"k": ["v"]}, 1, None, True])
        assert not contains_nul(None)
