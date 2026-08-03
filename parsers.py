"""
GL Reconciliation: Excel file parsers

Parses RVW GL Detail Inquiry and Craftable (Foodager + Bevager) exports
and validates file structure before processing.
"""

import pandas as pd
from typing import Tuple, Optional
from normalize import (
    normalize_invoice,
    normalize_vendor,
    normalize_gl_code,
    normalize_date,
    normalize_amount,
    extract_gl_description
)
from gl_accounts import gl_mapping


class FileValidationError(Exception):
    """Raised when file structure is invalid or missing required columns."""
    pass


def parse_rvw(
    file_obj,
    header_row: int = 3,
    sheet_name: Optional[str] = None,
    expected_columns: Optional[dict] = None
) -> pd.DataFrame:
    """
    Parse RVW GL Detail Inquiry export.
    
    Default column mapping (can be overridden):
    - Reference → invoice_number
    - Tran Date → date
    - Description → vendor_name
    - Major → gl_account
    - Amount → amount
    
    Args:
        file_obj: File upload object or file path
        header_row: 0-indexed row containing column headers (default: 3)
        sheet_name: Sheet name to read (default: first/active sheet)
        expected_columns: Dict mapping standard names to actual column names
                         {
                           'invoice_number': 'Reference',
                           'date': 'Tran Date',
                           'vendor_name': 'Description',
                           'gl_account': 'Major',
                           'amount': 'Amount'
                         }
    
    Returns:
        DataFrame with normalized columns:
        ['invoice_number', 'date', 'vendor_name', 'gl_account', 'amount']
    
    Raises:
        FileValidationError: If file cannot be read or required columns missing
    """
    
    # Default column mapping
    if expected_columns is None:
        expected_columns = {
            'invoice_number': 'Reference',
            'date': 'Tran Date',
            'vendor_name': 'Description',
            'gl_account': 'Major',
            'amount': 'Amount'
        }
    
    try:
        # Read Excel file
        if sheet_name:
            df = pd.read_excel(file_obj, sheet_name=sheet_name, header=header_row)
        else:
            df = pd.read_excel(file_obj, header=header_row)
    except Exception as e:
        raise FileValidationError(f"Failed to read RVW file: {str(e)}")
    
    # Normalize column names (strip whitespace)
    df.columns = [col.strip() for col in df.columns]
    
    # Check for required columns
    missing_cols = [col for col in expected_columns.values() if col not in df.columns]
    if missing_cols:
        available = ", ".join(df.columns.tolist())
        raise FileValidationError(
            f"RVW file missing columns: {missing_cols}\nAvailable: {available}"
        )
    
    # Select and rename columns
    result = df[list(expected_columns.values())].copy()
    result.columns = list(expected_columns.keys())
    
    # Normalize data
    result['invoice_number'] = result['invoice_number'].apply(
        lambda x: normalize_invoice(x) if pd.notna(x) else ""
    )
    result['vendor_name'] = result['vendor_name'].apply(
        lambda x: normalize_vendor(x) if pd.notna(x) else ""
    )
    result['gl_account'] = result['gl_account'].apply(
        lambda x: normalize_gl_code(x) if pd.notna(x) else ""
    )
    result['date'] = result['date'].apply(
        lambda x: normalize_date(x) if pd.notna(x) else ""
    )
    result['amount'] = result['amount'].apply(normalize_amount)

    # RVW's GL column ("Major") is a bare code with no description, e.g. "40100".
    # Look up a human-readable description from the shared gl_accounts mapping so
    # results can display "40100 - Grocery" instead of a bare code.
    result['gl_description'] = result['gl_account'].apply(
        lambda code: gl_mapping.get(code, '').split('-', 1)[-1].strip() if code in gl_mapping else ""
    )

    # Remove rows with missing critical data
    result = result[
        (result['invoice_number'] != "") & 
        (result['gl_account'] != "")
    ].reset_index(drop=True)
    
    return result


def parse_craftable(
    food_file_obj,
    bev_file_obj,
    header_row: int = 5,
    sheet_name: str = "2. GL Distribution",
    expected_columns: Optional[dict] = None
) -> pd.DataFrame:
    """
    Parse Craftable Foodager + Bevager exports and concatenate.
    
    Default column mapping (can be overridden):
    - INVOICE NO → invoice_number
    - VENDOR → vendor_name
    - GL ACCOUNT → gl_account
    - GL AMOUNT → gl_amount (amount for this GL line)
    - TOTAL → total_amount (total invoice amount if split)
    - INVOICE DATE → date
    
    Args:
        food_file_obj: Foodager Excel file
        bev_file_obj: Bevager Excel file
        header_row: 0-indexed row containing column headers (default: 5)
        sheet_name: Sheet name to read (default: "2. GL Distribution")
        expected_columns: Dict mapping standard names to actual column names
    
    Returns:
        Concatenated DataFrame with normalized columns:
        ['invoice_number', 'vendor_name', 'gl_account', 'gl_amount', 'total_amount', 'date']
    
    Raises:
        FileValidationError: If files cannot be read or required columns missing
    """
    
    # Default column mapping
    if expected_columns is None:
        expected_columns = {
            'invoice_number': 'INVOICE NO',
            'vendor_name': 'VENDOR',
            'gl_account': 'GL ACCOUNT',
            'gl_amount': 'GL AMOUNT',
            'total_amount': 'TOTAL',
            'date': 'INVOICE DATE'
        }
    
    dfs = []
    
    for file_obj, file_label in [(food_file_obj, "Foodager"), (bev_file_obj, "Bevager")]:
        try:
            df = pd.read_excel(file_obj, sheet_name=sheet_name, header=header_row)
        except Exception as e:
            raise FileValidationError(
                f"Failed to read {file_label} file: {str(e)}"
            )
        
        # Normalize column names (strip whitespace, including trailing)
        df.columns = [col.strip() for col in df.columns]
        
        # Check for required columns
        missing_cols = [col for col in expected_columns.values() if col not in df.columns]
        if missing_cols:
            available = ", ".join(df.columns.tolist())
            raise FileValidationError(
                f"{file_label} file missing columns: {missing_cols}\nAvailable: {available}"
            )
        
        # Select and rename columns
        result = df[list(expected_columns.values())].copy()
        result.columns = list(expected_columns.keys())
        
        # Normalize data
        result['invoice_number'] = result['invoice_number'].apply(
            lambda x: normalize_invoice(x) if pd.notna(x) else ""
        )
        result['vendor_name'] = result['vendor_name'].apply(
            lambda x: normalize_vendor(x) if pd.notna(x) else ""
        )
        # Craftable's GL column is "CODE - Description" (e.g. "40100 - Grocery").
        # Extract the description before normalize_gl_code strips it down to the code.
        result['gl_description'] = result['gl_account'].apply(
            lambda x: extract_gl_description(x) if pd.notna(x) else ""
        )
        result['gl_account'] = result['gl_account'].apply(
            lambda x: normalize_gl_code(x) if pd.notna(x) else ""
        )
        result['date'] = result['date'].apply(
            lambda x: normalize_date(x) if pd.notna(x) else ""
        )
        result['gl_amount'] = result['gl_amount'].apply(normalize_amount)
        result['total_amount'] = result['total_amount'].apply(normalize_amount)
        
        # Remove rows with missing critical data
        result = result[
            (result['invoice_number'] != "") & 
            (result['gl_account'] != "")
        ].reset_index(drop=True)
        
        dfs.append(result)
    
    # Concatenate food and beverage data
    df_craftable = pd.concat(dfs, ignore_index=True)
    
    return df_craftable


def parse_files(
    rvw_file,
    food_file,
    bev_file,
    rvw_config: Optional[dict] = None,
    craftable_config: Optional[dict] = None
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Convenience function to parse all three files at once.
    
    Args:
        rvw_file: RVW GL Detail Inquiry file
        food_file: Foodager export file
        bev_file: Bevager export file
        rvw_config: Optional {header_row, sheet_name, expected_columns}
        craftable_config: Optional {header_row, sheet_name, expected_columns}
    
    Returns:
        (df_rvw, df_craftable) tuple
    
    Raises:
        FileValidationError: If any file cannot be parsed
    """
    
    rvw_config = rvw_config or {}
    craftable_config = craftable_config or {}
    
    df_rvw = parse_rvw(rvw_file, **rvw_config)
    df_craftable = parse_craftable(food_file, bev_file, **craftable_config)
    
    return df_rvw, df_craftable
