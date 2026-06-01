"""Tests for SNOMED CT client."""
import pytest

from engine.snomed_client import (
    is_allergy_cross_reactive,
    get_allergy_cross_reactions,
    SnomedConcept,
)


class TestAlleryCrossReactivity:
    def test_penicillin_amoxicillin(self):
        assert is_allergy_cross_reactive("Penicillin", "amoxicillin") is True

    def test_penicillin_ampicillin(self):
        assert is_allergy_cross_reactive("penicillin", "Ampicillin") is True

    def test_penicillin_cephalosporin_cross_class(self):
        """Penicillin allergy flags cephalosporins (~2% cross-reactivity)."""
        assert is_allergy_cross_reactive("penicillin", "cephalexin") is True
        assert is_allergy_cross_reactive("penicillin", "ceftriaxone") is True

    def test_sulfa_celecoxib(self):
        assert is_allergy_cross_reactive("sulfa", "celecoxib") is True

    def test_nsaid_ibuprofen(self):
        assert is_allergy_cross_reactive("nsaid", "ibuprofen") is True

    def test_opioid_morphine(self):
        assert is_allergy_cross_reactive("codeine", "morphine") is True

    def test_no_cross_reactivity(self):
        assert is_allergy_cross_reactive("penicillin", "metformin") is False
        assert is_allergy_cross_reactive("sulfa", "lisinopril") is False

    def test_statin_class(self):
        assert is_allergy_cross_reactive("statin", "atorvastatin") is True
        assert is_allergy_cross_reactive("simvastatin", "rosuvastatin") is True

    def test_ace_inhibitor_class(self):
        assert is_allergy_cross_reactive("ace inhibitor", "lisinopril") is True


class TestGetAlleryCrossReactions:
    def test_penicillin(self):
        reactions = get_allergy_cross_reactions("penicillin")
        assert "amoxicillin" in reactions
        assert "ampicillin" in reactions
        # Should also include cephalosporins
        assert "cephalexin" in reactions

    def test_nsaid(self):
        reactions = get_allergy_cross_reactions("nsaid")
        assert "ibuprofen" in reactions
        assert "naproxen" in reactions
        assert "aspirin" in reactions

    def test_unknown_allergy(self):
        reactions = get_allergy_cross_reactions("banana")
        assert reactions == []
