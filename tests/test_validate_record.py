"""
tests/test_validate_record.py — Tests unitaires pour process.py

Couvre les 4 règles de validation de gouvernance :
  1. arc_id obligatoire et non vide
  2. t_1h obligatoire et format ISO datetime
  3. q (débit) dans [0, MAX_DEBIT]
  4. k (taux d'occupation) dans [0, 100]

Lancer : python -m pytest tests/ -v
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

# Ajout du chemin pour importer depuis scripts/ et config.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from process import validate_record


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _row(**kwargs) -> pd.Series:
    """Crée une pd.Series avec des valeurs par défaut valides, surchargeable."""
    defaults = {
        "arc_id":  "75056_e0001",
        "libelle": "Rue de Rivoli",
        "t_1h":    "2026-03-26T10:00:00",
        "q":       500.0,
        "k":       30.0,
    }
    defaults.update(kwargs)
    return pd.Series(defaults)


# ─── Cas valides ──────────────────────────────────────────────────────────────

class TestEnregistrementValide:

    def test_enregistrement_complet_accepte(self):
        ok, reason, score = validate_record(_row())
        assert ok is True
        assert reason == ""
        assert score == 1.0

    def test_score_complet_est_1(self):
        _, _, score = validate_record(_row())
        assert score == 1.0

    def test_libelle_absent_accepte(self):
        """libelle n'est pas une règle de validation — doit passer."""
        ok, _, score = validate_record(_row(libelle=None))
        assert ok is True
        assert score == 1.0

    def test_q_zero_accepte(self):
        """Débit = 0 est valide (route fermée ou pas de trafic)."""
        ok, _, _ = validate_record(_row(q=0))
        assert ok is True

    def test_k_zero_accepte(self):
        """Taux d'occupation = 0 est valide (route vide)."""
        ok, _, _ = validate_record(_row(k=0))
        assert ok is True

    def test_k_100_accepte(self):
        """Taux d'occupation = 100 est valide (saturation totale)."""
        ok, _, _ = validate_record(_row(k=100))
        assert ok is True

    def test_q_max_accepte(self):
        """Débit = MAX_DEBIT (10000) est valide (borne incluse)."""
        ok, _, _ = validate_record(_row(q=10000))
        assert ok is True

    def test_t1h_format_avec_espace_accepte(self):
        """Format '2026-03-26 10:00:00' doit être accepté."""
        ok, _, _ = validate_record(_row(t_1h="2026-03-26 10:00:00"))
        assert ok is True

    def test_q_absent_na_accepte(self):
        """q absent (NaN) est optionnel et ne bloque pas la validation."""
        ok, _, score = validate_record(_row(q=float("nan")))
        assert ok is True
        assert score < 1.0  # score réduit car champ absent


# ─── Règle 1 : arc_id ─────────────────────────────────────────────────────────

class TestRegle1ArcId:

    def test_arc_id_vide_rejete(self):
        ok, reason, score = validate_record(_row(arc_id=""))
        assert ok is False
        assert "arc_id" in reason.lower()
        assert score == 0.0

    def test_arc_id_espace_seul_rejete(self):
        ok, reason, _ = validate_record(_row(arc_id="   "))
        assert ok is False
        assert "arc_id" in reason.lower()

    def test_arc_id_none_rejete(self):
        ok, reason, _ = validate_record(_row(arc_id=None))
        assert ok is False

    def test_arc_id_nan_rejete(self):
        ok, _, _ = validate_record(_row(arc_id=float("nan")))
        assert ok is False


# ─── Règle 2 : t_1h format ────────────────────────────────────────────────────

class TestRegle2T1h:

    def test_t1h_absent_rejete(self):
        ok, reason, _ = validate_record(_row(t_1h=None))
        assert ok is False
        assert "t_1h" in reason.lower()

    def test_t1h_vide_rejete(self):
        ok, _, _ = validate_record(_row(t_1h=""))
        assert ok is False

    def test_t1h_format_invalide_rejete(self):
        ok, reason, _ = validate_record(_row(t_1h="26/03/2026 10:00"))
        assert ok is False
        assert "t_1h" in reason.lower()

    def test_t1h_date_seule_rejete(self):
        """Une date sans heure ne satisfait pas le regex ISO datetime."""
        ok, _, _ = validate_record(_row(t_1h="2026-03-26"))
        assert ok is False

    def test_t1h_format_correct_accepte(self):
        ok, _, _ = validate_record(_row(t_1h="2026-03-26T10:00:00+02:00"))
        assert ok is True


# ─── Règle 3 : débit q ────────────────────────────────────────────────────────

class TestRegle3Debit:

    def test_q_negatif_rejete(self):
        ok, reason, _ = validate_record(_row(q=-1))
        assert ok is False
        assert "débit" in reason.lower() or "debit" in reason.lower()

    def test_q_depasse_max_rejete(self):
        ok, reason, _ = validate_record(_row(q=10001))
        assert ok is False
        assert "10000" in reason or "debit" in reason.lower() or "débit" in reason.lower()

    def test_q_tres_grand_rejete(self):
        ok, _, _ = validate_record(_row(q=99999))
        assert ok is False

    def test_q_negatif_score_partiel(self):
        """Un rejet sur q arrive après avoir validé arc_id et t_1h (score > 0)."""
        _, _, score = validate_record(_row(q=-1))
        # arc_id + t_1h valides → score >= 0.5
        assert score >= 0.5


# ─── Règle 4 : taux d'occupation k ───────────────────────────────────────────

class TestRegle4TauxOccupation:

    def test_k_negatif_rejete(self):
        ok, reason, _ = validate_record(_row(k=-0.1))
        assert ok is False
        assert "taux" in reason.lower() or "k" in reason.lower()

    def test_k_superieur_100_rejete(self):
        ok, reason, _ = validate_record(_row(k=100.1))
        assert ok is False
        assert "taux" in reason.lower() or "k" in reason.lower()

    def test_k_tres_grand_rejete(self):
        ok, _, _ = validate_record(_row(k=200))
        assert ok is False

    def test_k_absent_na_accepte(self):
        """k absent est optionnel."""
        ok, _, _ = validate_record(_row(k=float("nan")))
        assert ok is True


# ─── Score de qualité ─────────────────────────────────────────────────────────

class TestQualityScore:

    def test_score_tous_champs(self):
        _, _, score = validate_record(_row())
        assert score == 1.0

    def test_score_q_absent(self):
        """q absent → score = 3/4 = 0.75."""
        _, _, score = validate_record(_row(q=float("nan")))
        assert score == pytest.approx(0.75)

    def test_score_q_et_k_absents(self):
        """q et k absents → score = 2/4 = 0.5."""
        _, _, score = validate_record(_row(q=float("nan"), k=float("nan")))
        assert score == pytest.approx(0.5)
