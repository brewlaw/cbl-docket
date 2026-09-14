import re
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import streamlit as st

# -----------------------------------------------------------------------------
# AUTHENTICATION & WORKSHEET LOOKUPS
# -----------------------------------------------------------------------------

def get_spreadsheet():
    """Authenticates via Streamlit Secrets and opens the Target Google Sheet."""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds_dict = dict(st.secrets["google_sheets"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(st.secrets["google_sheets"]["spreadsheet_id"])


def get_worksheet(ss, tab_name):
    """Retrieves a worksheet by name or handles flexible variations (SOUEXT / SOU/EXT)."""
    try:
        return ss.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        # Flexible fallback matching
        title_map = {
            "SOUEXT": ["SOUEXT", "SOU/EXT"],
            "SOU/EXT": ["SOUEXT", "SOU/EXT"],
            "Intl Priority": ["Intl Priority", "Int'l Priority"],
            "Int'l Priority": ["Intl Priority", "Int'l Priority"]
        }
        
        target_options = title_map.get(tab_name, [tab_name])
        for sheet in ss.worksheets():
            if sheet.title in target_options:
                return sheet
        
        # Create worksheet if missing
        return ss.add_worksheet(title=tab_name, rows="100", cols="20")

# -----------------------------------------------------------------------------
# READ & WRITE OPERATIONAL DATA
# -----------------------------------------------------------------------------

def fetch_master_docket_df(ss=None):
    """Loads Master Docket into a pandas DataFrame for memory-based relational lookups."""
    if ss is None:
        ss = get_spreadsheet()
    ws = get_worksheet(ss, "Master Docket")
    records = ws.get_all_records()
    df = pd.DataFrame(records)
    
    # Ensure SerialNumber is formatted cleanly as an 8-digit string
    if not df.empty and "SerialNumber" in df.columns:
        df["SerialNumber"] = df["SerialNumber"].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    return df


def backfill_master_docket(ss, serial_number, mark_name, owner_name):
    """Updates Master Docket if Wordmark or Owner fields are missing."""
    ws = get_worksheet(ss, "Master Docket")
    data = ws.get_all_values()
    
    for i, row in enumerate(data[1:], start=2):
        row_serial = re.sub(r'\D', '', str(row[0])) if len(row) > 0 else ""
        if row_serial == serial_number:
            # Col B (index 2) is Wordmark
            if len(row) > 1 and (not row[1] or row[1].upper() in ["N/A", "NONE", "NULL"]) and mark_name:
                ws.update_cell(i, 2, mark_name)
            # Col H (index 8) is Owner
            if len(row) > 7 and (not row[7] or row[7].upper() in ["N/A", "NONE", "NULL"]) and owner_name:
                ws.update_cell(i, 8, owner_name)
            break

# -----------------------------------------------------------------------------
# SUB-TAB ROUTING & DUP-CHECK LOGIC
# -----------------------------------------------------------------------------

def push_records_to_google_sheet(records, ss=None):
    """
    Pushes staged records from Streamlit to Google Sheets.
    Routes items to SOUEXT, OA, or Pub using exact LBL column structures.
    """
    if ss is None:
        ss = get_spreadsheet()

    grouped_records = {}
    for r in records:
        target_tab = r.get("tab")
        if not target_tab:
            continue
        grouped_records.setdefault(target_tab, []).append(r)

    for tab_name, tab_records in grouped_records.items():
        ws = get_worksheet(ss, tab_name)
        existing_data = ws.get_all_values()

        for rec in tab_records:
            client = rec.get("Client", "N/A")
            tm = rec.get("TM", "N/A")
            docket = rec.get("Docket #", "N/A")
            appl = rec.get("Appl. #", "N/A")

            if tab_name in ["SOUEXT", "SOU/EXT"]:
                deadline = rec.get("Deadline", "N/A")
                status = rec.get("Status", "N/A")
                _update_or_append_souext(ws, existing_data, client, tm, docket, appl, deadline, status)

            elif tab_name == "OA":
                issue_date = rec.get("OA Issue Date", "N/A")
                dl_3mo = rec.get("3-Month Response Deadline", "N/A")
                dl_6mo = rec.get("6-Month Extended Deadline", "N/A")
                status = rec.get("Status", "Pending Response")
                _update_or_append_oa(ws, existing_data, client, tm, docket, appl, issue_date, dl_3mo, dl_6mo, status)

            elif tab_name == "Pub":
                pub_date = rec.get("Scheduled Publication Date", "N/A")
                pub_comp = rec.get("Publication Complete Date (30 Days)", "N/A")
                status = rec.get("Status", "Published - 30-Day Opposition Period Open")
                _update_or_append_pub(ws, existing_data, client, tm, docket, appl, pub_date, pub_comp, status)


def _update_or_append_souext(ws, existing_data, client, tm, docket, appl, deadline, status):
    """Updates an active pending row on SOUEXT or appends a new row."""
    updated = False
    for i, row in enumerate(existing_data[1:], start=2):
        row_appl = re.sub(r'\D', '', str(row[3])) if len(row) > 3 else ""
        row_status = str(row[5]) if len(row) > 5 else ""

        if row_appl == appl and "36-Month" not in row_status and "Final Statutory" not in row_status:
            ws.update(f"A{i}:F{i}", [[client, tm, docket, appl, deadline, status]])
            updated = True
            break

    if not updated:
        ws.append_row([client, tm, docket, appl, deadline, status])


def _update_or_append_oa(ws, existing_data, client, tm, docket, appl, issue_date, dl_3mo, dl_6mo, status):
    """Updates a pending row on OA or appends a new row."""
    updated = False
    for i, row in enumerate(existing_data[1:], start=2):
        row_appl = re.sub(r'\D', '', str(row[3])) if len(row) > 3 else ""
        row_status = str(row[7]) if len(row) > 7 else ""

        if row_appl == appl and "Pending" in row_status:
            ws.update(f"A{i}:H{i}", [[client, tm, docket, appl, issue_date, dl_3mo, dl_6mo, status]])
            updated = True
            break

    if not updated:
        ws.append_row([client, tm, docket, appl, issue_date, dl_3mo, dl_6mo, status])


def _update_or_append_pub(ws, existing_data, client, tm, docket, appl, pub_date, pub_comp, status):
    """Updates or appends a publication notice row on Pub tab."""
    updated = False
    for i, row in enumerate(existing_data[1:], start=2):
        row_appl = re.sub(r'\D', '', str(row[3])) if len(row) > 3 else ""
        if row_appl == appl:
            ws.update(f"A{i}:G{i}", [[client, tm, docket, appl, pub_date, pub_comp, status]])
            updated = True
            break

    if not updated:
        ws.append_row([client, tm, docket, appl, pub_date, pub_comp, status])

# -----------------------------------------------------------------------------
# TAB INITIALIZATION
# -----------------------------------------------------------------------------

def ensure_tab_headers(ss=None):
    """Enforces LBL-style column headers across all sub-tabs."""
    if ss is None:
        ss = get_spreadsheet()

    tab_schemas = {
        "OA": ["Client", "TM", "Docket #", "Appl. #", "OA Issue Date", "3-Month Response Deadline", "6-Month Extended Deadline", "Status"],
        "SOUEXT": ["Client", "TM", "Docket #", "Appl. #", "Deadline", "Status"],
        "Pub": ["Client", "TM", "Docket #", "Appl. #", "Scheduled Publication Date", "Publication Complete Date (30 Days)", "Status"],
        "Maint": ["Reg / Serial Number", "Wordmark", "Reg / Issue Date", "Maintenance Type", "Regular Expiration Deadline", "6-Month Grace Period Deadline", "Status"],
        "Review Needed": ["Date Received", "Subject", "Serial / Reg Number", "Document Type", "Reason For Flag", "Email Thread Link"]
    }

    for tab_name, headers in tab_schemas.items():
        ws = get_worksheet(ss, tab_name)
        current_headers = ws.row_values(1)
        if current_headers != headers:
            ws.update("A1", [headers])
            ws.format("A1:Z1", {"textFormat": {"bold": True}})