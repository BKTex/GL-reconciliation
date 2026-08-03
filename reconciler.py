"""
GL Reconciliation: Matching and reconciliation logic

Compares RVW invoices against Craftable invoices and classifies matches/discrepancies.
Supports fuzzy vendor matching and configurable amount threshold.
"""

from typing import List, Dict, Tuple, Optional, Set
from dataclasses import dataclass
from collections import defaultdict
import pandas as pd
from normalize import fuzzy_match_vendor


@dataclass
class MatchResult:
    """Result of reconciling a single invoice/GL line."""
    invoice_number: str          # Normalized invoice number
    gl_code: str                 # GL account code (normalized, code only)
    gl_description: str          # Human-readable GL description, e.g. "Food Expenses"
    vendor_rvw: str              # Vendor from RVW (or empty if missing from RVW)
    vendor_craftable: str        # Vendor from Craftable (or empty if missing from Craftable)
    amount_rvw: float            # Amount from RVW (summed across duplicate invoice+GL rows)
    amount_craftable: float      # Amount from Craftable (summed across duplicate invoice+GL rows)
    date: str                    # Date (YYYY-MM-DD)
    match_type: str              # 'matched' | 'amount_diff' | 'missing_rvw' | 'missing_craftable'
    difference: float            # amount_rvw - amount_craftable
    variance_pct: float          # Percentage variance
    is_split: bool                # True if invoice spans multiple GL codes
    split_group_id: str           # Invoice number - used to group related GL lines
    fuzzy_match: bool             # True if vendor matched using fuzzy matching (possible reclass)
    invoice_total_rvw: float = 0.0        # Sum of all RVW GL lines for this invoice
    invoice_total_craftable: float = 0.0  # Sum of all Craftable GL lines for this invoice


class Reconciler:
    """Main reconciliation engine."""

    def __init__(
        self,
        threshold: float = 5.0,
        fuzzy_threshold: float = 0.85,
        restrict_to_craftable_gl_codes: bool = True,
    ):
        """
        Initialize reconciler.

        Args:
            threshold: Dollar amount threshold for "matched" status (default: $5)
            fuzzy_threshold: Similarity threshold for fuzzy vendor matching (0.0-1.0, default: 0.85)
            restrict_to_craftable_gl_codes: If True (default), only reconcile RVW rows whose
                GL code also appears somewhere in the Craftable upload. RVW's GL Detail export
                includes every GL account in the business (payroll, rent, cash, etc.) - the vast
                majority of which Craftable never touches. Without this filter, every one of
                those unrelated RVW rows would surface as a false "missing from Craftable" line.
        """
        self.threshold = threshold
        self.fuzzy_threshold = fuzzy_threshold
        self.restrict_to_craftable_gl_codes = restrict_to_craftable_gl_codes
        self.results: List[MatchResult] = []

    def reconcile(self, df_rvw: pd.DataFrame, df_craftable: pd.DataFrame) -> List[MatchResult]:
        """
        Main reconciliation logic.

        1. Aggregate each source to (invoice, gl) -> summed amount (duplicate rows for the
           same invoice+GL are legitimate multi-line charges and must be summed, not overwritten)
        2. Optionally restrict RVW to GL codes seen in Craftable (removes unrelated-account noise)
        3. Determine which invoices are "split" (span more than one GL code in either source)
        4. Match on (invoice, gl); fall back to fuzzy vendor match across GL codes for the same
           invoice number when no exact GL match exists (flags a likely reclass)
        5. Classify each match as matched/amount_diff/missing_rvw/missing_craftable

        Args:
            df_rvw: RVW data (columns: invoice_number, vendor_name, amount, date, gl_account)
            df_craftable: Craftable data (columns: invoice_number, vendor_name, gl_amount,
                total_amount, date, gl_account)

        Returns:
            List of MatchResult objects
        """
        craft_gl_codes = set(df_craftable['gl_account']) if self.restrict_to_craftable_gl_codes else None

        rvw_agg = self._aggregate(df_rvw, amount_col='amount', gl_filter=craft_gl_codes)
        craft_agg = self._aggregate(df_craftable, amount_col='gl_amount', gl_filter=None)

        invoice_gl_map = self._build_invoice_gl_map(rvw_agg, craft_agg)
        invoice_totals_rvw = self._invoice_totals(rvw_agg)
        invoice_totals_craft = self._invoice_totals(craft_agg)

        self.results = []
        used_craft_keys: Set[Tuple[str, str]] = set()

        for (inv, gl), rvw_row in rvw_agg.items():
            fuzzy = False
            craft_row = craft_agg.get((inv, gl))

            if craft_row is None:
                # No exact (invoice, GL) match - look for the same invoice number under a
                # different GL code with a similar vendor name (likely a reclass candidate).
                craft_row, matched_key = self._find_fuzzy_candidate(
                    inv, rvw_row['vendor'], craft_agg, used_craft_keys
                )
                if craft_row is not None:
                    fuzzy = True
                    used_craft_keys.add(matched_key)
            else:
                used_craft_keys.add((inv, gl))

            if craft_row is not None:
                match_type, diff, var_pct = self._classify_match(rvw_row['amount'], craft_row['amount'])
            else:
                match_type = 'missing_craftable'
                craft_row = {'vendor': '', 'amount': 0.0, 'date': ''}
                diff = rvw_row['amount']
                var_pct = 100.0 if rvw_row['amount'] != 0 else 0.0

            self.results.append(MatchResult(
                invoice_number=inv,
                gl_code=gl,
                gl_description=rvw_row.get('gl_description') or craft_row.get('gl_description', ''),
                vendor_rvw=rvw_row['vendor'],
                vendor_craftable=craft_row.get('vendor', ''),
                amount_rvw=rvw_row['amount'],
                amount_craftable=craft_row.get('amount', 0.0),
                date=rvw_row['date'],
                match_type=match_type,
                difference=diff,
                variance_pct=var_pct,
                is_split=len(invoice_gl_map.get(inv, set())) > 1,
                split_group_id=inv,
                fuzzy_match=fuzzy,
                invoice_total_rvw=invoice_totals_rvw.get(inv, 0.0),
                invoice_total_craftable=invoice_totals_craft.get(inv, 0.0),
            ))

        # Craftable entries never matched to an RVW row
        for (inv, gl), craft_row in craft_agg.items():
            if (inv, gl) in used_craft_keys:
                continue
            self.results.append(MatchResult(
                invoice_number=inv,
                gl_code=gl,
                gl_description=craft_row.get('gl_description', ''),
                vendor_rvw='',
                vendor_craftable=craft_row['vendor'],
                amount_rvw=0.0,
                amount_craftable=craft_row['amount'],
                date=craft_row['date'],
                match_type='missing_rvw',
                difference=-craft_row['amount'],
                variance_pct=100.0 if craft_row['amount'] != 0 else 0.0,
                is_split=len(invoice_gl_map.get(inv, set())) > 1,
                split_group_id=inv,
                fuzzy_match=False,
                invoice_total_rvw=invoice_totals_rvw.get(inv, 0.0),
                invoice_total_craftable=invoice_totals_craft.get(inv, 0.0),
            ))

        return self.results

    def _aggregate(
        self,
        df: pd.DataFrame,
        amount_col: str,
        gl_filter: Optional[Set[str]],
    ) -> Dict[Tuple[str, str], Dict]:
        """
        Aggregate a parsed DataFrame to (invoice_number, gl_account) -> summed row data.

        Duplicate (invoice, gl) rows (e.g. multiple deliveries against one invoice/GL, or
        fee line splits) are summed rather than overwritten, so no line item is silently
        dropped.
        """
        agg: Dict[Tuple[str, str], Dict] = {}

        for _, row in df.iterrows():
            inv = row['invoice_number']
            gl = row['gl_account']
            if not inv or not gl:
                continue
            if gl_filter is not None and gl not in gl_filter:
                continue

            key = (inv, gl)
            amount = row[amount_col]
            gl_description = row.get('gl_description', '') or ''

            if key not in agg:
                agg[key] = {
                    'vendor': row['vendor_name'],
                    'amount': 0.0,
                    'date': row['date'],
                    'gl_description': gl_description,
                }
            agg[key]['amount'] += amount
            if not agg[key]['gl_description'] and gl_description:
                agg[key]['gl_description'] = gl_description

        return agg

    def _build_invoice_gl_map(
        self,
        rvw_agg: Dict[Tuple[str, str], Dict],
        craft_agg: Dict[Tuple[str, str], Dict],
    ) -> Dict[str, Set[str]]:
        """Map invoice_number -> set of GL codes it appears under, across both sources."""
        invoice_gl_map: Dict[str, Set[str]] = defaultdict(set)
        for (inv, gl) in rvw_agg.keys():
            invoice_gl_map[inv].add(gl)
        for (inv, gl) in craft_agg.keys():
            invoice_gl_map[inv].add(gl)
        return invoice_gl_map

    def _invoice_totals(self, agg: Dict[Tuple[str, str], Dict]) -> Dict[str, float]:
        """Sum amounts per invoice number across all its GL lines."""
        totals: Dict[str, float] = defaultdict(float)
        for (inv, _gl), row in agg.items():
            totals[inv] += row['amount']
        return dict(totals)

    def _classify_match(self, amt_rvw: float, amt_craft: float) -> Tuple[str, float, float]:
        """
        Classify match based on amount difference and threshold.

        Returns:
            (match_type, difference, variance_pct)
        """
        diff = amt_rvw - amt_craft

        if max(abs(amt_rvw), abs(amt_craft)) > 0:
            var_pct = (abs(diff) / max(abs(amt_rvw), abs(amt_craft))) * 100
        else:
            var_pct = 0.0

        if abs(diff) <= self.threshold:
            return ('matched', 0.0, 0.0)
        else:
            return ('amount_diff', diff, var_pct)

    def _find_fuzzy_candidate(
        self,
        invoice_number: str,
        rvw_vendor: str,
        craft_agg: Dict[Tuple[str, str], Dict],
        used_craft_keys: Set[Tuple[str, str]],
    ) -> Tuple[Optional[Dict], Optional[Tuple[str, str]]]:
        """
        Look for a Craftable row with the same invoice number under a different GL code,
        with a similar vendor name. This surfaces likely reclass candidates: same invoice,
        same vendor, but allocated to a different GL account in each system.

        Never reuses a Craftable row that has already been consumed by another match, so a
        single Craftable line can't be double-counted against multiple RVW lines.

        Returns:
            (craft_row, matched_key) or (None, None) if no candidate found
        """
        best_row = None
        best_key = None
        best_score = 0.0

        for (craft_inv, craft_gl), craft_row in craft_agg.items():
            if craft_inv != invoice_number:
                continue
            key = (craft_inv, craft_gl)
            if key in used_craft_keys:
                continue

            score, is_match = fuzzy_match_vendor(rvw_vendor, craft_row['vendor'], self.fuzzy_threshold)
            if is_match and score > best_score:
                best_score = score
                best_row = craft_row
                best_key = key

        return best_row, best_key

    def group_by_gl(self) -> Dict[str, List[MatchResult]]:
        """Group reconciliation results by GL code."""
        grouped: Dict[str, List[MatchResult]] = defaultdict(list)
        for result in self.results:
            grouped[result.gl_code].append(result)
        return dict(grouped)

    def group_by_invoice(self) -> Dict[str, List[MatchResult]]:
        """Group reconciliation results by invoice number (for split-invoice display)."""
        grouped: Dict[str, List[MatchResult]] = defaultdict(list)
        for result in self.results:
            grouped[result.invoice_number].append(result)
        return dict(grouped)

    def get_summary_for_gl(self, gl_code: str) -> Dict[str, int]:
        """Return matched/unmatched counts for a given GL code."""
        results = [r for r in self.results if r.gl_code == gl_code]
        matched = sum(1 for r in results if r.match_type == 'matched')
        unmatched = len(results) - matched
        return {'matched': matched, 'unmatched': unmatched, 'total': len(results)}

    def get_split_invoices(self) -> List[Dict]:
        """
        Build display-ready groups for invoices that span more than one GL code.

        Per BK's spec: show the invoice total once, plus each GL line below it
        (vendor name shown once at the group level, not repeated per line).

        Returns:
            List of dicts: {invoice_number, vendor, total_rvw, total_craftable, lines}
            sorted by invoice number, with lines sorted by GL code.
        """
        by_invoice = self.group_by_invoice()
        splits = []
        for inv, lines in by_invoice.items():
            if len(lines) < 2:
                continue
            sorted_lines = sorted(lines, key=lambda r: r.gl_code)
            vendor = next((l.vendor_rvw for l in sorted_lines if l.vendor_rvw), '') \
                or next((l.vendor_craftable for l in sorted_lines if l.vendor_craftable), '')
            splits.append({
                'invoice_number': inv,
                'vendor': vendor,
                'total_rvw': sorted_lines[0].invoice_total_rvw,
                'total_craftable': sorted_lines[0].invoice_total_craftable,
                'lines': sorted_lines,
            })
        splits.sort(key=lambda s: s['invoice_number'])
        return splits

