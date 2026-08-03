"""
Unit tests for normalize.py module
"""

import pytest
from normalize import (
    normalize_invoice,
    normalize_vendor,
    fuzzy_match_vendor,
    normalize_gl_code,
    normalize_date,
    normalize_amount
)


class TestNormalizeInvoice:
    """Tests for normalize_invoice()"""
    
    def test_basic_alphanumeric(self):
        # Note: Leading zeros are only stripped from purely numeric invoices
        # "INV-00123" has letters, so we keep the structure, just remove special chars
        assert normalize_invoice("INV-00123") == "INV00123"
        # But purely numeric invoice numbers get leading zeros stripped
        assert normalize_invoice("00456") == "456"
        assert normalize_invoice("inv-789") == "INV789"
    
    def test_leading_zeros(self):
        assert normalize_invoice("00000") == "0"
        assert normalize_invoice("00001") == "1"
        assert normalize_invoice("000ABC") == "ABC"
    
    def test_special_characters(self):
        assert normalize_invoice("INV-12-345-A") == "INV12345A"
        assert normalize_invoice("INV_123") == "INV123"
        assert normalize_invoice("INV 123") == "INV123"
    
    def test_case_insensitive(self):
        assert normalize_invoice("inv-123") == "INV123"
        assert normalize_invoice("InV-123") == "INV123"
    
    def test_empty_and_none(self):
        assert normalize_invoice(None) == ""
        assert normalize_invoice("") == ""
        assert normalize_invoice("   ") == ""
    
    def test_numeric_input(self):
        assert normalize_invoice(123) == "123"
        assert normalize_invoice(0) == "0"


class TestNormalizeVendor:
    """Tests for normalize_vendor()"""
    
    def test_basic_normalization(self):
        assert normalize_vendor("SYSCO CORPORATION") == "SYSCO"
        assert normalize_vendor("abc foods inc") == "ABC FOODS"
        assert normalize_vendor("US FOODS, INC.") == "US FOODS"
    
    def test_suffix_removal(self):
        assert normalize_vendor("ABC Foods Inc") == "ABC FOODS"
        assert normalize_vendor("XYZ Supply LLC") == "XYZ"
        assert normalize_vendor("Company Corp") == "COMPANY"
        assert normalize_vendor("Business Ltd") == "BUSINESS"
    
    def test_case_insensitive(self):
        assert normalize_vendor("sysco corp") == "SYSCO"
        assert normalize_vendor("SYSCO CORP") == "SYSCO"
    
    def test_whitespace_handling(self):
        assert normalize_vendor("  SYSCO  ") == "SYSCO"
        assert normalize_vendor("ABC   FOODS") == "ABC   FOODS"  # Internal spaces preserved
    
    def test_empty_and_none(self):
        assert normalize_vendor(None) == ""
        assert normalize_vendor("") == ""


class TestFuzzyMatchVendor:
    """Tests for fuzzy_match_vendor()"""
    
    def test_exact_match(self):
        score, match = fuzzy_match_vendor("SYSCO", "SYSCO")
        assert score == 1.0
        assert match is True
    
    def test_close_match(self):
        score, match = fuzzy_match_vendor("SYSCO CORPORATION", "SYSCO CORP")
        assert match is True
        assert score > 0.85
    
    def test_no_match(self):
        score, match = fuzzy_match_vendor("ABC FOODS", "DEF FOODS")
        assert match is False
        assert score < 0.85
    
    def test_threshold_parameter(self):
        # After normalization: "SYSCO" vs "SYSCO" -> exact match
        # So we need vendors that are different but similar
        score, match = fuzzy_match_vendor("SYSCO COMPANY", "SYSCO CORP", threshold=0.99)
        assert match is False  # High threshold should reject partial match
    
    def test_empty_vendors(self):
        # Empty strings normalize to empty, which are "equal" but not useful
        # Current logic: empty + empty = exact match (1.0, True)
        # This is technically correct but may not be desired
        # For now, accept that empty = empty is an exact match
        score, match = fuzzy_match_vendor("", "")
        assert score == 1.0  # Empty strings are equal
        assert match is True
    
    def test_one_empty_vendor(self):
        # One empty vendor should not match
        score, match = fuzzy_match_vendor("SYSCO", "")
        assert match is False
        assert score == 0.0
    
    def test_case_insensitive_vendor(self):
        score, match = fuzzy_match_vendor("SYSCO corporation", "sysco corp")
        assert match is True


class TestNormalizeGLCode:
    """Tests for normalize_gl_code()"""
    
    def test_basic_gl_codes(self):
        assert normalize_gl_code("60100") == "60100"
        assert normalize_gl_code("5000") == "5000"
    
    def test_whitespace_stripping(self):
        assert normalize_gl_code("  60100  ") == "60100"
        assert normalize_gl_code("\t5000\n") == "5000"
    
    def test_case_handling(self):
        assert normalize_gl_code("6COGs") == "6COGS"
    
    def test_empty_and_none(self):
        assert normalize_gl_code(None) == ""
        assert normalize_gl_code("") == ""


class TestNormalizeAmount:
    """Tests for normalize_amount()"""
    
    def test_numeric_input(self):
        assert normalize_amount(1234.56) == 1234.56
        assert normalize_amount(1000) == 1000.0
        assert normalize_amount(0) == 0.0
    
    def test_string_numeric(self):
        assert normalize_amount("1234.56") == 1234.56
        assert normalize_amount("1000") == 1000.0
    
    def test_currency_strings(self):
        assert normalize_amount("$1,234.56") == 1234.56
        assert normalize_amount("$1000") == 1000.0
        assert normalize_amount("1,000.00") == 1000.0
    
    def test_invalid_input(self):
        assert normalize_amount("invalid") == 0.0
        assert normalize_amount(None) == 0.0
        assert normalize_amount("") == 0.0
    
    def test_negative_amounts(self):
        assert normalize_amount(-100.50) == -100.50
        assert normalize_amount("$-100.50") == -100.50


class TestNormalizeDate:
    """Tests for normalize_date()"""
    
    def test_valid_date_format(self):
        assert normalize_date("2026-08-03") == "2026-08-03"
        assert normalize_date("2026-01-15") == "2026-01-15"
    
    def test_date_with_time(self):
        assert normalize_date("2026-08-03 15:30:45") == "2026-08-03"
    
    def test_invalid_dates(self):
        assert normalize_date("invalid") == ""
        assert normalize_date(None) == ""
        assert normalize_date("") == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
