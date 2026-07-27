"""Quote verification is the anti-hallucination control; test it hard."""

from __future__ import annotations

import pytest

from app.core.enums import QuoteVerification
from app.utils.text import (
    clean_extracted_text,
    collapse_whitespace,
    dehyphenate,
    normalize_entity_key,
    normalize_for_match,
    split_sentences,
    truncate,
    verify_quote,
)

SOURCE = (
    "NG-101 is a brain-penetrant allosteric LRRK2 kinase inhibitor with an IC50 of "
    "3.2 nM in a biochemical assay. Selectivity across a 468-kinase panel exceeds "
    "100-fold."
)


class TestVerifyQuote:
    def test_exact_quote_verifies(self):
        verification, score = verify_quote("allosteric LRRK2 kinase inhibitor", SOURCE)
        assert verification is QuoteVerification.EXACT
        assert score == 1.0

    def test_whitespace_and_case_differences_still_exact(self):
        quote = "Allosteric   LRRK2\nkinase  inhibitor"
        verification, _ = verify_quote(quote, SOURCE)
        assert verification is QuoteVerification.EXACT

    def test_smart_punctuation_normalises(self):
        source = "The company's lead asset — NG-101 — is “first-in-class”."
        verification, _ = verify_quote(
            'The company\'s lead asset - NG-101 - is "first-in-class".', source
        )
        assert verification is QuoteVerification.EXACT

    def test_ligature_normalises(self):
        verification, _ = verify_quote("efficacy profile", "The eﬃcacy proﬁle was favourable.")
        assert verification is QuoteVerification.EXACT

    def test_near_miss_is_fuzzy(self):
        quote = (
            "NG-101 is a brain penetrant allosteric LRRK2 kinase inhibtor with an IC50 of 3.2 nM"
        )
        verification, score = verify_quote(quote, SOURCE)
        assert verification is QuoteVerification.FUZZY
        assert 0.85 <= score < 1.0

    def test_fabricated_quote_is_rejected(self):
        quote = "NG-101 achieved complete remission in 84% of patients in a Phase 2 trial"
        verification, _ = verify_quote(quote, SOURCE)
        assert verification is QuoteVerification.NOT_FOUND

    def test_plausible_but_absent_quote_is_rejected(self):
        """The dangerous case: same vocabulary, different assertion."""
        quote = "LRRK2 kinase inhibitor with an IC50 of 0.2 nM in a cellular assay"
        verification, _ = verify_quote(quote, SOURCE)
        assert verification is QuoteVerification.NOT_FOUND

    def test_empty_quote_is_not_applicable(self):
        assert verify_quote("", SOURCE)[0] is QuoteVerification.NOT_APPLICABLE
        assert verify_quote("   ", SOURCE)[0] is QuoteVerification.NOT_APPLICABLE

    def test_empty_haystack_is_not_found(self):
        assert verify_quote("anything at all", "")[0] is QuoteVerification.NOT_FOUND

    def test_short_quote_is_not_fuzzy_matched(self):
        """Short strings match anything fuzzily; refuse to verify them."""
        verification, _ = verify_quote("IC51 nM", SOURCE)
        assert verification is QuoteVerification.NOT_FOUND


class TestNormalisation:
    def test_dehyphenate_joins_across_lines(self):
        assert dehyphenate("neurodegener-\nation") == "neurodegeneration"

    def test_clean_preserves_paragraphs(self):
        cleaned = clean_extracted_text("Line one\n\n\n\nLine two")
        assert cleaned == "Line one\n\nLine two"

    def test_collapse_whitespace(self):
        assert collapse_whitespace("  a \n\t b  ") == "a b"

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("KRAS G12C", "kras-g12c"),
            ("The LRRK2", "lrrk2"),
            ("anti-PD-1", "anti pd 1"),
        ],
    )
    def test_entity_keys_collapse_variants(self, left, right):
        assert normalize_entity_key(left) == normalize_entity_key(right)

    def test_normalize_for_match_keeps_numbers_and_operators(self):
        result = normalize_for_match("IC50 of 3.2 nM (p<0.01)")
        assert "3.2" in result
        assert "p<0.01" in result

    def test_split_sentences(self):
        sentences = split_sentences("First one. Second one! Third one?")
        assert len(sentences) == 3

    def test_truncate_adds_suffix(self):
        assert truncate("abcdefghij", 5) == "ab..."
        assert truncate("abc", 10) == "abc"
