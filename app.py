import os
import re
from datetime import datetime
from dateutil.relativedelta import relativedelta
import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup

# Try importing modular dependencies if available
try:
    from src.gmail_client import fetch_uspto_emails
except ImportError:
    fetch_uspto_emails = None

try:
    from src.sheets_client import fetch_master_docket_df, push_records_to_google_sheet
except ImportError:
    fetch_master_docket_df = None
    push_records_to_google_sheet = None

# Page Configuration
st.set_page_config(page_title="USPTO Trademark Docketing Manager", layout="wide")

# -----------------------------------------------------------------------------
# HELPER PARSING FUNCTIONS
# -----------------------------------------------------------------------------

def clean_number(val):
    if not val or pd.isna(val):
        return ""
    s = re.sub(r'\.0$', '', str(val).strip())
    match = re.search(r'\d{7,8}', s)
    return match.group(0) if match else re.sub(r'\D', '', s)

def clean_wordmark(raw_mark):
    if not raw_mark or pd.isna(raw_mark):
        return "N/A"
    s = re.sub(r'&nbsp;?', '', str(raw_mark)).strip()
    s = re.sub(r'\(.*?\)', '', s).strip()
    s = re.sub(r'https?://\S+', '', s).strip()
    if s.upper() in ["NONE", "NULL", "N/A", "SECTION", "MARK", "APPLICATION", "NAN"]:
        return "N/A"
    return s if s else "N/A"

def clean_owner_name(raw_owner):
    if not raw_owner or pd.isna(raw_owner):
        return "N/A"
    s = re.sub(r'&nbsp;?', '', str(raw_owner)).strip()
    s = re.sub(r'\(.*?\)', '', s).strip()
    parts = re.split(r'[\(;]', s)
    s = parts[0].strip()
    if s.upper() in ["NONE", "NULL", "N/A", "NAN"]:
        return "N/A"
    return s if s else "N/A"

def extract_docket_no(text):
    if not text:
        return "N/A"
    invalid_words = ["NO", "NUMBER", "SERIAL", "APPLICATION", "US", "SN", "N/A", "NONE", "NULL", "CONTACTS", "INFORMATION", "ADDRESS", "NAME", "DETAILS"]
    
    # 1. Explicit Docket/Reference No. pattern
    m1 = re.search(r'(?:Docket|Reference|Ref)\s*(?:/\s*(?:Reference|Ref|No\.?|Number|\#))*\s*(?:No\.?|Number|\#)?\s*:?\s*([A-Z0-9\-_]+)', text, re.IGNORECASE)
    if m1 and m1.group(1).upper() not in invalid_words and len(m1.group(1)) >= 3:
        return m1.group(1).strip()

    # 2. Docket at start of subject line e.g. "212-TM-4 Serial Number..."
    m2 = re.search(r'^\s*([A-Z0-9\-_]+)\s+(?:Serial\s*Number|SN|Application)', text, re.IGNORECASE)
    if m2 and m2.group(1).upper() not in invalid_words and len(m2.group(1)) >= 3:
        return m2.group(1).strip()

    # 3. Standard firm docket format e.g. 302-TM-3, 212-TM-4, 143-TM-4-2, 125-TM-29-2
    m3 = re.search(r'\b(\d{1,3}-TM-\d{1,3}(?:-\d{1,2})?)\b', text, re.IGNORECASE)
    if m3:
        return m3.group(1).strip()

    # 4. Format TM0606, TM2103
    m4 = re.search(r'\b(TM\d{3,4})\b', text, re.IGNORECASE)
    if m4:
        return m4.group(1).strip()

    return "N/A"

def parse_uspto_email_html(html_or_text_content, subject=""):
    soup = BeautifulSoup(html_or_text_content, 'html.parser')
    clean_text = ' '.join(soup.get_text().split())
    full_text = subject + " " + clean_text

    # Extract Serial Number
    sn_match = re.search(r'(?:SN|Serial\s*Number|Application\s*serial\s*no\.?|Application\s*SN)\s*:?\s*(\d{8})', full_text, re.IGNORECASE) or re.search(r'\b(\d{8})\b', full_text)
    serial = sn_match.group(1) if sn_match else ""

    # Extract Docket Number
    docket = extract_docket_no(full_text)

    # Extract Mark
    mark = "N/A"
    m_subj1 = re.search(r'Serial\s*No\.?\s*\d{8}\s*-\s*([A-Za-z0-9\'"\s\-\.\?\&\/]+?)\s*-\s*[A-Z0-9\-_]+', subject, re.IGNORECASE)
    if m_subj1:
        mark = m_subj1.group(1)
    else:
        m_subj2 = re.search(r'U\.S\.\s*Trademark\s*SN\s*\d{8}\s+([A-Za-z0-9\'"\s\-\.\?\&\/]+?)(?:\s*null|\s*--|\r|\n|$)', subject, re.IGNORECASE)
        if m_subj2:
            mark = m_subj2.group(1)
        else:
            m_sec = re.search(r'MARK\s+SECTION\s+MARK\s+([A-Za-z0-9\'"\s\-\.\?\&\/]+?)(?=\s*\(see|\s*STANDARD|\s*SECTION|\r|\n|<|$)', clean_text, re.IGNORECASE)
            if m_sec:
                mark = m_sec.group(1)
            else:
                m_lit = re.search(r'LITERAL\s+ELEMENT\s+([A-Za-z0-9\'"\s\-\.\?\&\/]+?)\s+OWNER\s+SECTION', clean_text, re.IGNORECASE)
                if m_lit:
                    mark = m_lit.group(1)
                else:
                    m_noa = re.search(r'Mark\s*[:\.]\s*(?:&nbsp;)*\s*([A-Za-z0-9\'"\s\-\.\?\&\/]+?)(?=\s+Docket|\s+SECTION|\s+Owner|\s+STANDARD|\r|\n|<|$)', clean_text, re.IGNORECASE)
                    if m_noa and not m_noa.group(1).upper().startswith("APPLICATION"):
                        mark = m_noa.group(1)
    mark = clean_wordmark(mark)

    # Extract Owner
    owner = "N/A"
    o_sec = re.search(r'OWNER\s+SECTION\s+NAME\s+([A-Za-z0-9\'"\s\-\.\,\&\/]+?)\s+MAILING\s+ADDRESS', clean_text, re.IGNORECASE)
    if o_sec:
        owner = o_sec.group(1)
    else:
        o_noa = re.search(r'Owner\s*:\s*(?:&nbsp;)*\s*([A-Za-z0-9\'"\s\-\.\,\&\/]+?)(?=\s+\d{1,5}\s+[A-Za-z]|\s+Correspondence|\s+MAILING|\s+363|\r|\n|<|$)', clean_text, re.IGNORECASE)
        if o_noa:
            owner = o_noa.group(1)
    owner = clean_owner_name(owner)

    # Identify Document Type & Dates
    is_receipt = bool(re.search(r'received your|teas confirmation|filing receipt|confirmation number', full_text, re.IGNORECASE))
    
    # Dates
    pub_match = re.search(r'scheduled\s+to\s+publish\s+(?:in\s+the\s+Official\s+Gazette\s+)?on\s+([A-Za-z]+\s+\d{1,2},\s*\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})', full_text, re.IGNORECASE)
    pub_date_str = pub_match.group(1) if pub_match else ""

    oa_match = re.search(r'Office\s+Action.*?has\s+issued\s+on\s+([A-Za-z]+\s+\d{1,2},\s*\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})', full_text, re.IGNORECASE)
    noa_match = re.search(r'ISSUE\s*DATE\s*:?\s*([A-Za-z]+\s+\d{1,2},\s*\d{4}|\d{1,2}/\d{1,2}/\d{4})', full_text, re.IGNORECASE)
    issue_date_str = oa_match.group(1) if oa_match else (noa_match.group(1) if noa_match else "")

    ext_match = re.search(r'EXTENSION\s*NUMBER\s*:?\s*([1-5])', full_text, re.IGNORECASE)
    ext_num = int(ext_match.group(1)) if ext_match else None

    allow_match = re.search(r'ALLOWANCE\s*MAIL\s*DATE\s*:?\s*(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})', full_text, re.IGNORECASE)
    allowance_date_str = allow_match.group(1) if allow_match else ""

    # Categorize
    category = "OTHER"
    if re.search(r'NOTICE\s*OF\s*PUBLICATION|scheduled\s+to\s+publish', full_text, re.IGNORECASE):
        category = "PUB"
    elif re.search(r'office action', full_text, re.IGNORECASE):
        category = "OA"
    elif re.search(r'NOTICE\s*OF\s*ALLOWANCE|Extension\s*of\s*Time\s*to\s*File\s*a\s*Statement\s*of\s*Use|EXTENSION\s*NUMBER', full_text, re.IGNORECASE):
        category = "SOU_EXT"

    return {
        "serialNumber": serial,
        "wordmark": mark,
        "owner": owner,
        "docketNumber": docket,
        "category": category,
        "isFilingReceipt": is_receipt,
        "issueDate": issue_date_str,
        "publicationDate": pub_date_str,
        "allowanceMailDate": allowance_date_str,
        "extensionNumber": ext_num
    }

# -----------------------------------------------------------------------------
# DOCKET ENRICHMENT & ROUTING
# -----------------------------------------------------------------------------

def enrich_and_format_docket(parsed_info, df_master):
    sn = parsed_info["serialNumber"]
    
    client = parsed_info["owner"]
    tm = parsed_info["wordmark"]
    docket = parsed_info["docketNumber"]

    # Relational Lookup against Master Docket DataFrame
    if sn and df_master is not None and len(df_master) > 0:
        master_match = df_master[df_master['SerialNumber'].astype(str).str.contains(sn, na=False)]
        if len(master_match) > 0:
            m_row = master_match.iloc[0]
            if (tm == "N/A" or not tm) and 'Wordmark' in m_row and pd.notna(m_row['Wordmark']):
                tm = clean_wordmark(m_row['Wordmark'])
            if (client == "N/A" or not client) and 'OwnerFullText' in m_row and pd.notna(m_row['OwnerFullText']):
                client = clean_owner_name(m_row['OwnerFullText'])

    if not tm or tm == "N/A":
        tm = "[Design Mark]"

    records = []
    category = parsed_info["category"]

    # 1. SOU/EXT DEADLINE ROUTING
    if category == "SOU_EXT":
        issue_date = parsed_info["issueDate"] or datetime.now().strftime("%Y-%m-%d")
        base_date_str = parsed_info["allowanceMailDate"] or issue_date
        
        try:
            base_dt = pd.to_datetime(base_date_str).date()
        except:
            base_dt = datetime.now().date()

        ext_num = parsed_info["extensionNumber"]

        if not parsed_info["isFilingReceipt"] and not ext_num:
            # NOA
            deadline_6mo = (base_dt + relativedelta(months=6)).strftime("%Y-%m-%d")
            deadline_3yr = (base_dt + relativedelta(years=3)).strftime("%Y-%m-%d")

            records.append({
                "tab": "SOU/EXT",
                "Client": client, "TM": tm, "Docket #": docket, "Appl. #": sn,
                "Deadline": deadline_6mo, "Status": "1st SOU / Extension Deadline (0 Extensions Filed)"
            })
            records.append({
                "tab": "SOU/EXT",
                "Client": client, "TM": tm, "Docket #": docket, "Appl. #": sn,
                "Deadline": deadline_3yr, "Status": "36-Month Final Statutory SOU Limit"
            })

        elif ext_num:
            stat_3yr = (base_dt + relativedelta(years=3)).strftime("%Y-%m-%d")
            if 1 <= ext_num <= 4:
                next_ext = ext_num + 1
                next_dl = (base_dt + relativedelta(months=6 * next_ext)).strftime("%Y-%m-%d")
                suf = "2nd" if next_ext == 2 else ("3rd" if next_ext == 3 else f"{next_ext}th")
                records.append({
                    "tab": "SOU/EXT",
                    "Client": client, "TM": tm, "Docket #": docket, "Appl. #": sn,
                    "Deadline": next_dl, "Status": f"{suf} Extension or SOU Due (Ext {ext_num} Filed)"
                })
            elif ext_num == 5:
                records.append({
                    "tab": "SOU/EXT",
                    "Client": client, "TM": tm, "Docket #": docket, "Appl. #": sn,
                    "Deadline": stat_3yr, "Status": "Final Statutory SOU Deadline (Ext 5 Filed - Max Reached)"
                })

    # 2. OFFICE ACTION ROUTING
    elif category == "OA":
        issue_date_str = parsed_info["issueDate"] or datetime.now().strftime("%Y-%m-%d")
        try:
            dt = pd.to_datetime(issue_date_str).date()
        except:
            dt = datetime.now().date()

        dl_3mo = (dt + relativedelta(months=3)).strftime("%Y-%m-%d")
        dl_6mo = (dt + relativedelta(months=6)).strftime("%Y-%m-%d")

        records.append({
            "tab": "OA",
            "Client": client, "TM": tm, "Docket #": docket, "Appl. #": sn,
            "OA Issue Date": dt.strftime("%Y-%m-%d"),
            "3-Month Response Deadline": dl_3mo,
            "6-Month Extended Deadline": dl_6mo,
            "Status": "Pending Response"
        })

    # 3. NOTICE OF PUBLICATION ROUTING
    elif category == "PUB":
        pub_date_str = parsed_info["publicationDate"] or parsed_info["issueDate"] or datetime.now().strftime("%Y-%m-%d")
        try:
            dt = pd.to_datetime(pub_date_str).date()
        except:
            dt = datetime.now().date()

        pub_complete = (dt + relativedelta(days=30)).strftime("%Y-%m-%d")

        records.append({
            "tab": "Pub",
            "Client": client, "TM": tm, "Docket #": docket, "Appl. #": sn,
            "Scheduled Publication Date": dt.strftime("%Y-%m-%d"),
            "Publication Complete Date (30 Days)": pub_complete,
            "Status": "Published - 30-Day Opposition Period Open"
        })

    return records

# -----------------------------------------------------------------------------
# STREAMLIT UI
# -----------------------------------------------------------------------------

st.title("🛡️ Automated USPTO Trademark Docketing Manager")

st.sidebar.header("📁 Data Source & Configuration")

# 1. Master Docket Selection
uploaded_excel = st.sidebar.file_uploader("Upload Initial Docket Workbook (.xlsx)", type=["xlsx", "xls"])

df_master = None
if uploaded_excel:
    try:
        df_master = pd.read_excel(uploaded_excel, sheet_name="Master Docket")
        st.sidebar.success(f"Loaded Master Docket ({len(df_master)} records)")
    except Exception as e:
        st.sidebar.error(f"Error reading uploaded Master Docket: {e}")
elif fetch_master_docket_df is not None:
    try:
        df_master = fetch_master_docket_df()
        st.sidebar.info(f"Loaded Master Docket from Google Sheets ({len(df_master)} records)")
    except Exception as e:
        pass

# 2. Gmail Inbox Sync Section
st.sidebar.header("📥 Inbox Sync")
max_emails = st.sidebar.slider("Max Emails to Scan", min_value=5, max_value=50, value=20)

if st.sidebar.button("Fetch New USPTO Emails", type="primary"):
    if fetch_uspto_emails is None:
        st.sidebar.error("Gmail integration module (`src.gmail_client`) not found.")
    elif "gmail" not in st.secrets:
        st.sidebar.error("Gmail credentials missing from Streamlit secrets.")
    else:
        with st.spinner("Scanning Gmail inbox for USPTO notifications..."):
            try:
                emails = fetch_uspto_emails(max_results=max_emails)
                staged_records = []
                
                for em in emails:
                    parsed = parse_uspto_email_html(em["body"], em["subject"])
                    records = enrich_and_format_docket(parsed, df_master)
                    staged_records.extend(records)

                if "st_records" not in st.session_state:
                    st.session_state["st_records"] = []

                st.session_state["st_records"].extend(staged_records)
                st.sidebar.success(f"Fetched {len(emails)} emails -> Staged {len(staged_records)} deadlines!")
                st.rerun()
            except Exception as e:
                st.sidebar.error(f"Inbox Sync Error: {e}")

# Manual Email Staging Input (Fallback / Testing)
with st.expander("📝 Manual Email Ingestion (Testing / Override)"):
    email_subject = st.text_input("Email Subject Line", placeholder="e.g. Official USPTO Notification: U.S. Trademark Application SN 99622671 -- Docket/Reference No. 302-TM-3")
    email_body = st.text_area("Email Content / HTML Body", height=150, placeholder="Paste USPTO notification or forwarded email body here...")

    if st.button("Parse & Generate Manual Deadlines"):
        if not email_body and not email_subject:
            st.warning("Please enter an email subject or body to process.")
        else:
            parsed_info = parse_uspto_email_html(email_body, email_subject)
            docket_records = enrich_and_format_docket(parsed_info, df_master)

            if "st_records" not in st.session_state:
                st.session_state["st_records"] = []
            
            st.session_state["st_records"].extend(docket_records)
            st.success("Manual email successfully parsed and staged!")
            st.rerun()

# Staging Review Table
if "st_records" in st.session_state and st.session_state["st_records"]:
    st.subheader("📋 Interactive Review Queue (Editable Staging)")
    df_staged = pd.DataFrame(st.session_state["st_records"])
    
    # Display editable data editor
    edited_df = st.data_editor(df_staged, num_rows="dynamic", use_container_width=True)

    col1, col2, col3 = st.columns([1.5, 1.5, 3])
    
    with col1:
        if push_records_to_google_sheet is not None:
            if st.button("🚀 Commit to Google Sheets", type="primary"):
                with st.spinner("Writing records to Google Sheets..."):
                    try:
                        push_records_to_google_sheet(edited_df.to_dict("records"))
                        st.success("Successfully committed to Google Sheets!")
                        st.session_state["st_records"] = []
                        st.rerun()
                    except Exception as e:
                        st.error(f"Commit Error: {e}")

    with col2:
        if st.button("Clear Queue"):
            st.session_state["st_records"] = []
            st.rerun()
            
    with col3:
        # Export to CSV / Excel
        csv = edited_df.to_csv(index=False).encode('utf-8')
        st.download_button("Download Formatted Docket (.csv)", data=csv, file_name="Formatted_TM_Deadlines.csv", mime="text/csv")
else:
    st.info("No deadlines currently staged. Click 'Fetch New USPTO Emails' in the sidebar or use manual ingestion above.")