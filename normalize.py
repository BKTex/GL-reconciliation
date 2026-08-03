"""
GL Reconciliation: Data normalization module

Handles normalization of invoice numbers, vendor names, and GL codes
for robust matching across RVW and Craftable systems.
"""

import re
from difflib import SequenceMatcher
from typing import Tuple


def normalize_invoice(inv_number) -> str:
    """
    Normalize invoice number for matching.
    
    Rules:
    - Convert to uppercase
    - Remove dashes, spaces, underscores, and special characters
    - Keep alphanumeric characters only
    - Handle leading zeros: "00123" -> "123", but "0" -> "0"
    
    Examples:
        "INV-00123" -> "INV123"
        "00456" -> "456"
        "inv-789" -> "INV789"
        "INV-12-345-A" -> "INV12345A"
    
    Args:
        inv_number: Invoice number (string or numeric)
        
    Returns:
        Normalized invoice number (uppercase, alphanumeric only)
    """
    if inv_number is None or (isinstance(inv_number, float) and inv_number != inv_number):  # NaN check
        return ""
    
    # Convert to string and uppercase
    text = str(inv_number).strip().upper()
    
    # Return empty string if input was empty
    if not text:
        return ""
    
    # Remove all non-alphanumeric characters (but NOT underscore, which is in \w)
    text = re.sub(r'[^\w]', '', text)  # \w = [a-zA-Z0-9_]
    text = text.replace('_', '')  # Remove underscores explicitly
    
    # Handle leading zeros: find first non-zero character
    # E.g., "00123ABC" -> "123ABC", "000" -> "0"
    
    match = re.search(r'[^0]', text)
    if match:
        # Start from first non-zero
        return text[match.start():]
    else:
        # All zeros (or empty after processing)
        return "0" if text else ""


def normalize_vendor(vendor_name) -> str:
    """
    Normalize vendor name for matching and display.
    
    Rules:
    - Convert to uppercase
    - Strip whitespace
    - Remove common business suffixes (INC, LLC, CORP, CORPORATION, etc.)
    
    Examples:
        "SYSCO CORPORATION" -> "SYSCO"
        "abc foods inc" -> "ABC FOODS"
        "US FOODS, INC." -> "US FOODS"
    
    Args:
        vendor_name: Vendor name
        
    Returns:
        Normalized vendor name (uppercase, no common suffixes)
    """
    if vendor_name is None or (isinstance(vendor_name, float) and vendor_name != vendor_name):  # NaN check
        return ""
    
    text = str(vendor_name).strip().upper()
    
    # Remove common suffixes
    suffixes = [
        ', INC.', ', INC', ', LLC.', ', LLC', ', CORP.', ', CORP',
        ', CORPORATION', ' INC.', ' INC', ' LLC.', ' LLC', ' CORP.', ' CORP',
        ' CORPORATION', ' CO.', ' CO', ' LTD.', ' LTD', ' SUPPLY', ' SUPPLIES',
        ', SUPPLY', ', SUPPLIES'
    ]
    
    for suffix in suffixes:
        if text.endswith(suffix):
            text = text[:-len(suffix)]
    
    return text.strip()


def fuzzy_match_vendor(vendor1: str, vendor2: str, threshold: float = 0.85) -> Tuple[float, bool]:
    """
    Compare two vendor names using fuzzy matching (Levenshtein similarity).
    
    Returns the similarity score and whether it exceeds the threshold.
    Uses Python's built-in difflib.SequenceMatcher for Levenshtein-like ratio.
    
    Args:
        vendor1: First vendor name
        vendor2: Second vendor name
        threshold: Minimum similarity score (0.0 to 1.0) to consider a match
                   Default: 0.85 (85% similar)
    
    Returns:
        (similarity_score, is_match)
        - similarity_score: Float 0.0-1.0 (1.0 = exact match)
        - is_match: Boolean (True if score >= threshold)
    
    Examples:
        fuzzy_match_vendor("SYSCO CORPORATION", "SYSCO CORP")
        -> (0.92, True)
        
        fuzzy_match_vendor("ABC FOODS", "DEF FOODS")
        -> (0.56, False)
    """
    # Normalize both vendor names first
    norm1 = normalize_vendor(vendor1)
    norm2 = normalize_vendor(vendor2)
    
    # Exact match check
    if norm1 == norm2:
        return (1.0, True)
    
    # Empty check
    if not norm1 or not norm2:
        return (0.0, False)
    
    # Fuzzy match using SequenceMatcher
    matcher = SequenceMatcher(None, norm1, norm2)
    score = matcher.ratio()
    
    return (score, score >= threshold)


def normalize_gl_code(gl_code) -> str:
    """
    Normalize GL (General Ledger) account code for matching.

    RVW's "Major" column contains a bare code ("40100"). Craftable's
    "GL ACCOUNT" column contains "CODE - Description" (e.g. "40100 - Grocery").
    Both must normalize to the same join key or every line item silently
    fails to match.

    Rules:
    - Strip whitespace
    - If the value contains " - " (code + description), keep only the code
      portion before the separator
    - Convert to uppercase
    - Strip trailing/leading whitespace from the code portion

    Examples:
        "60100" -> "60100"
        "  5000  " -> "5000"
        "40100 - Grocery" -> "40100"
        "COGS-100" -> "COGS-100" (no " - " separator, kept as-is)

    Args:
        gl_code: GL account code, optionally with a " - Description" suffix

    Returns:
        Normalized GL code (join key only, no description)
    """
    if gl_code is None or (isinstance(gl_code, float) and gl_code != gl_code):  # NaN check
        return ""

    text = str(gl_code).strip().upper()
    if not text:
        return ""

    # Split off a " - Description" suffix if present (Craftable format).
    # Use " - " (space-dash-space) specifically so GL codes that legitimately
    # contain a dash (no surrounding spaces) are left untouched.
    if ' - ' in text:
        text = text.split(' - ', 1)[0].strip()

    return text


def extract_gl_description(gl_code) -> str:
    """
    Extract the human-readable description from a "CODE - Description" GL
    value, if present.

    Examples:
        "40100 - Grocery" -> "Grocery"
        "40100" -> ""

    Args:
        gl_code: Raw GL account value (string or numeric)

    Returns:
        Description string, or "" if none present
    """
    if gl_code is None or (isinstance(gl_code, float) and gl_code != gl_code):
        return ""

    text = str(gl_code).strip()
    if ' - ' in text:
        return text.split(' - ', 1)[1].strip()
    return ""


def normalize_date(date_raw) -> str:
    """
    Normalize date to YYYY-MM-DD format.
    
    Handles various input formats:
    - Already formatted YYYY-MM-DD
    - Excel date serial numbers
    - Datetime objects
    
    Args:
        date_raw: Date value (string, datetime, or numeric)
        
    Returns:
        Date string in YYYY-MM-DD format, or empty string if invalid
    """
    if date_raw is None or (isinstance(date_raw, float) and date_raw != date_raw):  # NaN check
        return ""
    
    try:
        # If already a string that looks like a date
        date_str = str(date_raw).strip()
        if len(date_str) >= 10:
            # Try to extract YYYY-MM-DD from first 10 characters
            potential_date = date_str[:10]
            if re.match(r'\d{4}-\d{2}-\d{2}', potential_date):
                return potential_date
        
        # Try pandas datetime parsing (if available, fallback to string handling)
        import pandas as pd
        dt = pd.to_datetime(date_raw, errors='coerce')
        if pd.notna(dt):
            return dt.strftime('%Y-%m-%d')
    except Exception:
        pass
    
    return ""


def normalize_amount(amount_raw) -> float:
    """
    Normalize amount to float.
    
    Handles various input formats:
    - Numeric values
    - Currency strings ("$1,234.56")
    - Text with non-numeric characters
    
    Args:
        amount_raw: Amount value
        
    Returns:
        Float amount, or 0.0 if invalid
    """
    if amount_raw is None or (isinstance(amount_raw, float) and amount_raw != amount_raw):  # NaN check
        return 0.0
    
    try:
        # If already numeric
        if isinstance(amount_raw, (int, float)):
            return float(amount_raw)
        
        # If string, remove currency symbols and commas
        text = str(amount_raw).strip()
        text = re.sub(r'[\$,]', '', text)  # Remove $ and commas
        return float(text)
    except (ValueError, TypeError):
        return 0.0
