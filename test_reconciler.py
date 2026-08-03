"""
Unit tests for reconciler.py module.

Covers the core correctness fixes: duplicate (invoice, gl) row summation,
GL-scope filtering, split-invoice detection, threshold classification, and
fuzzy vendor fallback matching for reclass candidates.
"""

import pandas as pd
import pytest
from reconciler import Reconciler, MatchResult


def make_rvw_df(rows):
    """rows: list of dicts with invoice_number, vendor_name, amount, date, gl_account."""
    defaults = {"gl_description": ""}
    return pd.DataFrame([{**defaults, **r} for r in rows])


def make_craft_df(rows):
    """rows: list of dicts with invoice_number, vendor_name, gl_amount, total_amount, date, gl_account."""
    defaults = {"gl_description": ""}
    return pd.DataFrame([{**defaults, **r} for r in rows])


class TestDuplicateAggregation:
    """Duplicate (invoice, gl) rows within a source must be summed, not overwritten."""

    def test_rvw_duplicate_rows_are_summed(self):
        df_rvw = make_rvw_df([
            {"invoice_number": "INV1", "vendor_name": "SYSCO", "amount": 100.0,
             "date": "2026-01-01", "gl_account": "40100"},
            {"invoice_number": "INV1", "vendor_name": "SYSCO", "amount": 50.0,
             "date": "2026-01-01", "gl_account": "40100"},
        ])
        df_craft = make_craft_df([
            {"invoice_number": "INV1", "vendor_name": "SYSCO", "gl_amount": 150.0,
             "total_amount": 150.0, "date": "2026-01-01", "gl_account": "40100"},
        ])

        rec = Reconciler(threshold=5.0)
        results = rec.reconcile(df_rvw, df_craft)

        assert len(results) == 1
        assert results[0].amount_rvw == 150.0
        assert results[0].match_type == "matched"

    def test_craftable_duplicate_rows_are_summed(self):
        df_rvw = make_rvw_df([
            {"invoice_number": "INV2", "vendor_name": "US FOODS", "amount": 300.0,
             "date": "2026-02-01", "gl_account": "40200"},
        ])
        df_craft = make_craft_df([
            {"invoice_number": "INV2", "vendor_name": "US FOODS", "gl_amount": 200.0,
             "total_amount": 300.0, "date": "2026-02-01", "gl_account": "40200"},
            {"invoice_number": "INV2", "vendor_name": "US FOODS", "gl_amount": 100.0,
             "total_amount": 300.0, "date": "2026-02-01", "gl_account": "40200"},
        ])

        rec = Reconciler(threshold=5.0)
        results = rec.reconcile(df_rvw, df_craft)

        assert len(results) == 1
        assert results[0].amount_craftable == 300.0
        assert results[0].match_type == "matched"


class TestGlScopeFiltering:
    """RVW rows for GL codes Craftable never touches should not spam 'missing_craftable'."""

    def test_rvw_only_gl_is_excluded_by_default(self):
        df_rvw = make_rvw_df([
            {"invoice_number": "PAYROLL1", "vendor_name": "ADP", "amount": 5000.0,
             "date": "2026-01-01", "gl_account": "60900"},  # not in craftable at all
            {"invoice_number": "INV3", "vendor_name": "SYSCO", "amount": 75.0,
             "date": "2026-01-01", "gl_account": "40100"},
        ])
        df_craft = make_craft_df([
            {"invoice_number": "INV3", "vendor_name": "SYSCO", "gl_amount": 75.0,
             "total_amount": 75.0, "date": "2026-01-01", "gl_account": "40100"},
        ])

        rec = Reconciler(threshold=5.0, restrict_to_craftable_gl_codes=True)
        results = rec.reconcile(df_rvw, df_craft)

        gl_codes_seen = {r.gl_code for r in results}
        assert "60900" not in gl_codes_seen
        assert "40100" in gl_codes_seen

    def test_can_disable_gl_scope_filtering(self):
        df_rvw = make_rvw_df([
            {"invoice_number": "PAYROLL1", "vendor_name": "ADP", "amount": 5000.0,
             "date": "2026-01-01", "gl_account": "60900"},
        ])
        df_craft = make_craft_df([
            {"invoice_number": "INV3", "vendor_name": "SYSCO", "gl_amount": 75.0,
             "total_amount": 75.0, "date": "2026-01-01", "gl_account": "40100"},
        ])

        rec = Reconciler(threshold=5.0, restrict_to_craftable_gl_codes=False)
        results = rec.reconcile(df_rvw, df_craft)

        gl_codes_seen = {r.gl_code for r in results}
        assert "60900" in gl_codes_seen


class TestSplitInvoiceDetection:
    """Invoices spanning more than one GL code must be flagged as split with correct totals."""

    def test_split_invoice_flagged_and_totaled(self):
        df_rvw = make_rvw_df([
            {"invoice_number": "SPLIT1", "vendor_name": "SYSCO", "amount": 600.0,
             "date": "2026-03-01", "gl_account": "40100"},
            {"invoice_number": "SPLIT1", "vendor_name": "SYSCO", "amount": 400.0,
             "date": "2026-03-01", "gl_account": "40200"},
        ])
        df_craft = make_craft_df([
            {"invoice_number": "SPLIT1", "vendor_name": "SYSCO", "gl_amount": 600.0,
             "total_amount": 1000.0, "date": "2026-03-01", "gl_account": "40100"},
            {"invoice_number": "SPLIT1", "vendor_name": "SYSCO", "gl_amount": 350.0,
             "total_amount": 1000.0, "date": "2026-03-01", "gl_account": "40200"},
        ])

        rec = Reconciler(threshold=5.0)
        results = rec.reconcile(df_rvw, df_craft)

        split_results = [r for r in results if r.invoice_number == "SPLIT1"]
        assert all(r.is_split for r in split_results)
        assert all(r.invoice_total_rvw == 1000.0 for r in split_results)
        assert all(r.invoice_total_craftable == 950.0 for r in split_results)

        splits = rec.get_split_invoices()
        assert len(splits) == 1
        assert splits[0]["invoice_number"] == "SPLIT1"
        assert splits[0]["total_rvw"] == 1000.0
        assert splits[0]["total_craftable"] == 950.0
        assert len(splits[0]["lines"]) == 2

    def test_non_split_invoice_not_in_split_list(self):
        df_rvw = make_rvw_df([
            {"invoice_number": "SINGLE1", "vendor_name": "SYSCO", "amount": 100.0,
             "date": "2026-03-01", "gl_account": "40100"},
        ])
        df_craft = make_craft_df([
            {"invoice_number": "SINGLE1", "vendor_name": "SYSCO", "gl_amount": 100.0,
             "total_amount": 100.0, "date": "2026-03-01", "gl_account": "40100"},
        ])

        rec = Reconciler(threshold=5.0)
        rec.reconcile(df_rvw, df_craft)

        assert rec.get_split_invoices() == []


class TestThresholdClassification:

    def test_within_threshold_is_matched(self):
        df_rvw = make_rvw_df([
            {"invoice_number": "T1", "vendor_name": "SYSCO", "amount": 100.0,
             "date": "2026-01-01", "gl_account": "40100"},
        ])
        df_craft = make_craft_df([
            {"invoice_number": "T1", "vendor_name": "SYSCO", "gl_amount": 104.0,
             "total_amount": 104.0, "date": "2026-01-01", "gl_account": "40100"},
        ])
        rec = Reconciler(threshold=5.0)
        results = rec.reconcile(df_rvw, df_craft)
        assert results[0].match_type == "matched"

    def test_over_threshold_is_amount_diff(self):
        df_rvw = make_rvw_df([
            {"invoice_number": "T2", "vendor_name": "SYSCO", "amount": 100.0,
             "date": "2026-01-01", "gl_account": "40100"},
        ])
        df_craft = make_craft_df([
            {"invoice_number": "T2", "vendor_name": "SYSCO", "gl_amount": 110.0,
             "total_amount": 110.0, "date": "2026-01-01", "gl_account": "40100"},
        ])
        rec = Reconciler(threshold=5.0)
        results = rec.reconcile(df_rvw, df_craft)
        assert results[0].match_type == "amount_diff"
        assert results[0].difference == pytest.approx(-10.0)

    def test_threshold_is_configurable(self):
        df_rvw = make_rvw_df([
            {"invoice_number": "T3", "vendor_name": "SYSCO", "amount": 100.0,
             "date": "2026-01-01", "gl_account": "40100"},
        ])
        df_craft = make_craft_df([
            {"invoice_number": "T3", "vendor_name": "SYSCO", "gl_amount": 110.0,
             "total_amount": 110.0, "date": "2026-01-01", "gl_account": "40100"},
        ])
        rec = Reconciler(threshold=20.0)
        results = rec.reconcile(df_rvw, df_craft)
        assert results[0].match_type == "matched"

    def test_missing_from_craftable(self):
        df_rvw = make_rvw_df([
            {"invoice_number": "T4", "vendor_name": "SYSCO", "amount": 100.0,
             "date": "2026-01-01", "gl_account": "40100"},
        ])
        df_craft = make_craft_df([
            {"invoice_number": "OTHER", "vendor_name": "SYSCO", "gl_amount": 50.0,
             "total_amount": 50.0, "date": "2026-01-01", "gl_account": "40100"},
        ])
        rec = Reconciler(threshold=5.0)
        results = rec.reconcile(df_rvw, df_craft)
        t4 = [r for r in results if r.invoice_number == "T4"][0]
        assert t4.match_type == "missing_craftable"

    def test_missing_from_rvw(self):
        df_rvw = make_rvw_df([
            {"invoice_number": "OTHER", "vendor_name": "SYSCO", "amount": 50.0,
             "date": "2026-01-01", "gl_account": "40100"},
        ])
        df_craft = make_craft_df([
            {"invoice_number": "T5", "vendor_name": "SYSCO", "gl_amount": 100.0,
             "total_amount": 100.0, "date": "2026-01-01", "gl_account": "40100"},
        ])
        rec = Reconciler(threshold=5.0)
        results = rec.reconcile(df_rvw, df_craft)
        t5 = [r for r in results if r.invoice_number == "T5"][0]
        assert t5.match_type == "missing_rvw"


class TestFuzzyReclassFallback:
    """When no exact (invoice, GL) match exists, a same-invoice/similar-vendor row
    under a different GL should surface as a reclass candidate, not silent noise."""

    def test_cross_gl_fuzzy_match_flagged_as_reclass(self):
        df_rvw = make_rvw_df([
            {"invoice_number": "RC1", "vendor_name": "SYSCO CORP", "amount": 200.0,
             "date": "2026-04-01", "gl_account": "40100"},
        ])
        df_craft = make_craft_df([
            {"invoice_number": "RC1", "vendor_name": "SYSCO CORPORATION", "gl_amount": 200.0,
             "total_amount": 200.0, "date": "2026-04-01", "gl_account": "40700"},
        ])

        rec = Reconciler(threshold=5.0, fuzzy_threshold=0.85, restrict_to_craftable_gl_codes=False)
        results = rec.reconcile(df_rvw, df_craft)

        assert len(results) == 1
        assert results[0].fuzzy_match is True
        assert results[0].match_type == "matched"

    def test_no_fuzzy_match_for_dissimilar_vendor(self):
        df_rvw = make_rvw_df([
            {"invoice_number": "RC2", "vendor_name": "SYSCO CORP", "amount": 200.0,
             "date": "2026-04-01", "gl_account": "40100"},
        ])
        df_craft = make_craft_df([
            {"invoice_number": "RC2", "vendor_name": "ACME BEVERAGE", "gl_amount": 200.0,
             "total_amount": 200.0, "date": "2026-04-01", "gl_account": "40700"},
        ])

        rec = Reconciler(threshold=5.0, fuzzy_threshold=0.85, restrict_to_craftable_gl_codes=False)
        results = rec.reconcile(df_rvw, df_craft)

        # No exact and no fuzzy match -> both sides surface as unmatched, not merged
        assert len(results) == 2
        match_types = {r.match_type for r in results}
        assert match_types == {"missing_craftable", "missing_rvw"}

    def test_fuzzy_match_never_double_consumes_craftable_row(self):
        df_rvw = make_rvw_df([
            {"invoice_number": "RC3", "vendor_name": "SYSCO CORP", "amount": 100.0,
             "date": "2026-04-01", "gl_account": "40100"},
            {"invoice_number": "RC3", "vendor_name": "SYSCO CORP", "amount": 90.0,
             "date": "2026-04-01", "gl_account": "40200"},
        ])
        df_craft = make_craft_df([
            {"invoice_number": "RC3", "vendor_name": "SYSCO CORPORATION", "gl_amount": 100.0,
             "total_amount": 100.0, "date": "2026-04-01", "gl_account": "40700"},
        ])

        rec = Reconciler(threshold=5.0, fuzzy_threshold=0.85, restrict_to_craftable_gl_codes=False)
        results = rec.reconcile(df_rvw, df_craft)

        # Only one RVW line can claim the single Craftable row via fuzzy match;
        # the other must fall back to missing_craftable.
        fuzzy_hits = [r for r in results if r.fuzzy_match]
        assert len(fuzzy_hits) == 1
        missing_craft = [r for r in results if r.match_type == "missing_craftable"]
        assert len(missing_craft) == 1


class TestGroupingHelpers:

    def test_group_by_gl(self):
        df_rvw = make_rvw_df([
            {"invoice_number": "G1", "vendor_name": "SYSCO", "amount": 100.0,
             "date": "2026-01-01", "gl_account": "40100"},
            {"invoice_number": "G2", "vendor_name": "SYSCO", "amount": 100.0,
             "date": "2026-01-02", "gl_account": "40200"},
        ])
        df_craft = make_craft_df([
            {"invoice_number": "G1", "vendor_name": "SYSCO", "gl_amount": 100.0,
             "total_amount": 100.0, "date": "2026-01-01", "gl_account": "40100"},
            {"invoice_number": "G2", "vendor_name": "SYSCO", "gl_amount": 100.0,
             "total_amount": 100.0, "date": "2026-01-02", "gl_account": "40200"},
        ])
        rec = Reconciler(threshold=5.0)
        rec.reconcile(df_rvw, df_craft)
        grouped = rec.group_by_gl()
        assert set(grouped.keys()) == {"40100", "40200"}

    def test_get_summary_for_gl(self):
        df_rvw = make_rvw_df([
            {"invoice_number": "S1", "vendor_name": "SYSCO", "amount": 100.0,
             "date": "2026-01-01", "gl_account": "40100"},
            {"invoice_number": "S2", "vendor_name": "SYSCO", "amount": 100.0,
             "date": "2026-01-02", "gl_account": "40100"},
        ])
        df_craft = make_craft_df([
            {"invoice_number": "S1", "vendor_name": "SYSCO", "gl_amount": 100.0,
             "total_amount": 100.0, "date": "2026-01-01", "gl_account": "40100"},
            {"invoice_number": "S2", "vendor_name": "SYSCO", "gl_amount": 500.0,
             "total_amount": 500.0, "date": "2026-01-02", "gl_account": "40100"},
        ])
        rec = Reconciler(threshold=5.0)
        rec.reconcile(df_rvw, df_craft)
        summary = rec.get_summary_for_gl("40100")
        assert summary == {"matched": 1, "unmatched": 1, "total": 2}
