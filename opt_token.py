#!/usr/bin/env python3
"""
AWS Glue script to read email_date and token from Google Sheets
email_date is in the first column (A1) and token is in the second column (B1) of the first row
"""

import os
import sys
import json
from pathlib import Path
from typing import Optional

from utils import setup_logger, load_env_file

# Load environment variables from .env if present
load_env_file()

# Configuration from environment (with safe defaults where possible)
DEFAULT_SPREADSHEET_ID = "1GpVhtgI6sa7TqE2vueZy_xtNNMDQdE7UXejhDkDrBp4"
DEFAULT_SERVICE_ACCOUNT_FILE = "secrets.json"

SPREADSHEET_ID = os.getenv("GOOGLE_SPREADSHEET_ID", DEFAULT_SPREADSHEET_ID)
SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME") or None
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", DEFAULT_SERVICE_ACCOUNT_FILE)
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")



def _load_service_account_info() -> dict:
    """
    Load service account credentials from environment-provided JSON or file path.
    """
    if SERVICE_ACCOUNT_JSON:
        try:
            return json.loads(SERVICE_ACCOUNT_JSON)
        except json.JSONDecodeError as exc:
            raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON must contain valid JSON") from exc

    if SERVICE_ACCOUNT_FILE:
        sa_path = Path(SERVICE_ACCOUNT_FILE)
        if sa_path.exists():
            return json.loads(sa_path.read_text())
        raise FileNotFoundError(f"Service account file not found: {sa_path}")

    raise RuntimeError(
        "Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE in your environment or .env"
    )


def _get_spreadsheet_id() -> str:
    """
    Ensure a spreadsheet ID is available from environment or defaults.
    """
    if not SPREADSHEET_ID:
        raise RuntimeError("GOOGLE_SPREADSHEET_ID is required to read the sheet.")
    return SPREADSHEET_ID


def get_google_sheets_service():
    """
    Create Google Sheets API service using configured service account credentials.
    
    Returns:
        Google Sheets API service object
    """
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        raise ImportError(
            "Google API libraries not found. "
            "Add google-auth and google-api-python-client to dependencies.zip"
        )
    
    service_account_info = _load_service_account_info()
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    
    # Build the service
    service = build("sheets", "v4", credentials=credentials)
    return service


def read_data_from_sheets() -> dict:
    """
    Read email_date and token from Google Sheets (first row)
    email_date is in column 0 (A1), token is in column 1 (B1)
    Uses spreadsheet_id and credentials provided via environment/.env
    
    Returns:
        Dictionary with 'email_date' and 'token' keys, or None if not found
    """
    logger = setup_logger("dracma.login_inviu", "INFO")
    
    try:
        # Get Google Sheets service
        service = get_google_sheets_service()
        sheets = service.spreadsheets()
        
        spreadsheet_id = _get_spreadsheet_id()
        sheet_name = SHEET_NAME
        
        # If sheet_name not provided, get the first sheet
        if not sheet_name:
            spreadsheet = sheets.get(spreadsheetId=spreadsheet_id).execute()
            sheet_name = spreadsheet["sheets"][0]["properties"]["title"]
            logger.info("Using first sheet", extra={"sheet_name": sheet_name})
        
        # Read the first row, columns A and B (A1:B1)
        range_name = f"{sheet_name}!A1:B1"
        logger.info("Reading data from sheet", extra={
            "spreadsheet_id": spreadsheet_id,
            "range": range_name
        })
        
        result = sheets.values().get(
            spreadsheetId=spreadsheet_id,
            range=range_name
        ).execute()
        
        values = result.get("values", [])
        
        if not values or not values[0]:
            logger.warning("No data found in first row")
            return None
        
        row = values[0]
        
        # Extract email_date from column 0 (A1) and token from column 1 (B1)
        email_date = row[0] if len(row) > 0 else None
        token = row[1] if len(row) > 1 else None
        
        if not email_date and not token:
            logger.warning("Both email_date and token are empty")
            return None
        
        result_dict = {
            "email_date": email_date,
            "token": token
        }
        
        logger.info("Data retrieved successfully", extra={
            "email_date": email_date,
            "token_length": len(token) if token else 0,
            "token_preview": token[:10] + "..." if token and len(token) > 10 else token
        })
        
        return result_dict
        
    except Exception as e:
        logger.error("Error reading data from Google Sheets", exc_info=True)
        raise


def read_token_from_sheets() -> Optional[str]:
    """
    Read token from Google Sheets (backward compatibility)
    Returns only the token from column 1 (B1)
    """
    data = read_data_from_sheets()
    return data.get("token") if data else None


def main():
    """Main execution function"""
    logger = setup_logger("dracma.login_inviu", "INFO")
    
    
    try:
        data = read_data_from_sheets()
        
        if data:
            email_date = data.get("email_date")
            token = data.get("token")
            
            logger.info("Data retrieved successfully", extra={
                "email_date": email_date,
                "has_token": bool(token)
            })
            
            # Print as JSON to stdout (can be captured by calling process)
            output = json.dumps(data, ensure_ascii=False)
            print(output)
            return data
        else:
            logger.error("No data found in spreadsheet")
            sys.exit(1)
            
    except Exception as e:
        logger.error("Failed to read data from Google Sheets", exc_info=True)
        sys.exit(1)

def get_opt_token() -> Optional[str]:
    """Get the OPT token from the Google Sheets"""
    data = main()
    return data.get("token") if data else None

if __name__ == "__main__":
    token = get_opt_token()
    print("aca",token)

