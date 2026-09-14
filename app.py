import os
import re
import base64
from datetime import datetime
from dateutil.relativedelta import relativedelta
import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup

# Google APIs
import gspread
from google.oauth2.service_account import Credentials as SACredentials
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build

st.set_page_config(page_title="USPTO Trademark Docketing Manager", layout="wide")

# -----------------------------------------------------------------------------
# 1. PARSING & EXTRACTION ENGINE
# -----------------------------------------------------------------------------
def clean_number(val):
    if not val or pd.isna(val): return ""
    s = re.sub(r'\.0$', '', str(val).strip())
    match = re.search(r'\d{7,8}', s)
    return match.group(0) if match else re.sub(r'\D', '', s)

def clean_wordmark(raw_mark):
    if not raw_mark or pd.isna(raw_mark): return ""
    s = re.sub(r'&nbsp;?', '', str(raw_mark)).strip()
    s = re.sub(r'\(.*?\)', '', s).strip()
    s = re.sub(r'https?://\S+', '', s).strip()
    if s.upper() in ["NONE", "NULL", "N/A", "SECTION", "MARK", "APPLICATION", "NAN"]: return ""
    return s if s else ""

def clean_owner_name(raw_owner):
    if not raw_owner or pd.isna(raw_owner): return ""
    s = re.sub(r'&nbsp;?', '', str(raw_owner)).strip()
    s = re.sub(r'\(.*?\)', '', s).strip()
    parts = re.split(r'[\(;]', s)
    s = parts[0].strip()
    if s.upper() in ["NONE", "NULL", "N/A", "NAN"]: return ""
    return s if s else ""

def extract_docket_no(text):
    if not text: return ""
    invalid_words = ["NO", "NUMBER", "SERIAL", "APPLICATION", "US", "SN", "NONE", "NULL", "CONTACTS", "INFORMATION", "ADDRESS", "NAME", "DETAILS"]
    
    # Strictly require No, Number, or #
    m1 = re.search(r'(?:Docket|Reference|Ref)\s*(?:/\s*(?:Reference|Ref|Docket))?\s*(?:No\.?|Number|\#)\s*:?\s*([A-Z0-9\-_]+)', text, re.IGNORECASE)
    if m1 and m1.group(1).upper() not in invalid_words and len(m1.group(1)) >= 3:
        return m1.group(1).strip()
    
    m2 = re.search(r'^\s*([A-Z0-9\-_]+)\s+(?:Serial\s*Number|SN|Application)', text, re.IGNORECASE)
    if m2 and m2.group(1).upper() not in invalid_words and len(m2.group(1)) >= 3:
        return m2.group(1).strip()
    
    m3 = re.search(r'\b(\d{1,3}-TM-\d{1,3}(?:-\d{1,2})?)\b', text, re.IGNORECASE)
    if m3: return m3.group(1).strip()
    
    m4 = re.search(r'\b(TM\d{3,4})\b', text, re.IGNORECASE)
    if m4: return m4.group(1).strip()
    return ""

def parse_uspto_email(html_content, subject=""):
    soup = BeautifulSoup(html_content, 'html.parser')
    clean_text = ' '.join(soup.get_text().split())
    full_text = subject + " " + clean_text

    # Skip generic junk emails
    category = ""
    if re.search(r'NOTIFICATION\s+OF\s+["\']?NOTICE\s+OF\s+PUBLICATION|scheduled\s+to\s+publish', full_text, re.IGNORECASE):
        category = "PUB"
    elif re.search(r'Office\s+Action.*?has\s+issued', full_text, re.IGNORECASE):
        category = "OA"
    elif re.search(r'NOTICE\s*OF\s*ALLOWANCE|Extension\s*of\s*Time\s*to\s*File\s*a\s*Statement\s*of\s*Use|EXTENSION\s*NUMBER', full_text, re.IGNORECASE):
        category = "SOU_EXT"

    if not category:
        return None # Skips rendering for generic inbox emails

    sn_match = re.search(r'(?:SN|Serial\s*Number|Application\s*serial\s*no\.?|Application\s*SN)\s*:?\s*(\d{8})', full_text, re.IGNORECASE) or re.search(r'\b(\d{8})\b', full_text)
    serial = sn_match.group(1) if sn_match else ""

    docket = extract_docket_no(full_text)

    mark = ""
    m_subj = re.search(r'Serial\s*No\.?\s*\d{8}\s*-\s*([A-Za-z0-9\'"\s\-\.\?\&\/]+?)\s*-\s*[A-Z0-9\-_]+', subject, re.IGNORECASE)
    if m_subj: mark = m_subj.group(1)
    else:
        m_sec = re.search(r'MARK\s+SECTION\s+MARK\s+([A-Za-z0-9\'"\s\-\.\?\&\/]+?)(?=\s*\(see|\s*STANDARD|\s*SECTION|\r|\n|<|$)', clean_text, re.IGNORECASE)
        if m_sec: mark = m_sec.group(1)
        else:
            m_noa = re.search(r'Mark\s*[:\.]\s*(?:&nbsp;)*\s*([A-Za-z0-9\'"\s\-\.\?\&\/]+?)(?=\s+Docket|\s+SECTION|\s+Owner|\s+STANDARD|\r|\n|<|$)', clean_text, re.IGNORECASE)
            if m_noa and not m_noa.group(1).upper().startswith("APPLICATION"): mark = m_noa.group(1)
    mark = clean_wordmark(mark)

    owner = ""
    o_sec = re.search(r'OWNER\s+SECTION\s+NAME\s+([A-Za-z0-9\'"\s\-\.\,\&\/]+?)\s+MAILING\s+ADDRESS', clean_text, re.IGNORECASE)
    if o_sec: owner = o_sec.group(1)
    owner = clean_owner_name(owner)

    is_receipt = bool(re.search(r'received your|teas confirmation|filing receipt', full_text, re.IGNORECASE))
    
    pub_m = re.search(r'scheduled\s+to\s+publish\s+(?:in\s+the\s+Official\s+Gazette\s+)?on\s+([A-Za-z]+\s+\d{1,2},\s*\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})', full_text, re.IGNORECASE)
    oa_m = re.search(r'Office\s+Action.*?has\s+issued\s+on\s+([A-Za-z]+\s+\d{1,2},\s*\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})', full_text, re.IGNORECASE)
    ext_m = re.search(r'EXTENSION\s*NUMBER\s*:?\s*([1-5])', full_text, re.IGNORECASE)
    allow_m = re.search(r'ALLOWANCE\s*MAIL\s*DATE\s*:?\s*(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})', full_text, re.IGNORECASE)

    return {
        "serialNumber": serial,
        "wordmark": mark,
        "owner": owner,
        "docketNumber": docket,
        "category": category,
        "isFilingReceipt": is_receipt,
        "issueDate": oa_m.group(1) if oa_m else "",
        "publicationDate": pub_m.group(1) if pub_m else "",
        "allowanceMailDate": allow_m.group(1) if allow_m else "",
        "extensionNumber": int(ext_m.group(1)) if ext_m else None
    }

def format_docket_records(parsed, df_master):
    sn = parsed["serialNumber"]
    client, tm, docket = parsed["owner"], parsed["wordmark"], parsed["docketNumber"]

    # Relational Lookup
    if sn and df_master is not None and not df_master.empty:
        match = df_master[df_master['SerialNumber'].astype(str).str.contains(sn, na=False)]
        if len(match) > 0:
            m_row = match.iloc[0]
            if not tm and 'Wordmark' in m_row and pd.notna(m_row['Wordmark']):
                tm = clean_wordmark(m_row['Wordmark'])
            if not client and 'OwnerFullText' in m_row and pd.notna(m_row['OwnerFullText']):
                client = clean_owner_name(m_row['OwnerFullText'])

    if not tm: tm = "[Design Mark]"
    records = []
    
    # Routing Rules
    if parsed["category"] == "SOU_EXT":
        base_dt_str = parsed["allowanceMailDate"] or parsed["issueDate"] or datetime.now().strftime("%Y-%m-%d")
        try: base_dt = pd.to_datetime(base_dt_str).date()
        except: base_dt = datetime.now().date()

        ext_num = parsed["extensionNumber"]
        if not parsed["isFilingReceipt"] and not ext_num:
            dl_6mo = (base_dt + relativedelta(months=6)).strftime("%Y-%m-%d")
            dl_3yr = (base_dt + relativedelta(years=3)).strftime("%Y-%m-%d")
            records.append({"tab": "SOUEXT", "Client": client, "TM": tm, "Docket #": docket, "Appl. #": sn, "Deadline": dl_6mo, "Status": "1st SOU / Extension Deadline (0 Extensions Filed)"})
            records.append({"tab": "SOUEXT", "Client": client, "TM": tm, "Docket #": docket, "Appl. #": sn, "Deadline": dl_3yr, "Status": "36-Month Final Statutory SOU Limit"})
        elif ext_num:
            stat_3yr = (base_dt + relativedelta(years=3)).strftime("%Y-%m-%d")
            if 1 <= ext_num <= 4:
                next_dl = (base_dt + relativedelta(months=6 * (ext_num + 1))).strftime("%Y-%m-%d")
                suf = "2nd" if ext_num+1 == 2 else ("3rd" if ext_num+1 == 3 else f"{ext_num+1}th")
                records.append({"tab": "SOUEXT", "Client": client, "TM": tm, "Docket #": docket, "Appl. #": sn, "Deadline": next_dl, "Status": f"{suf} Extension or SOU Due (Ext {ext_num} Filed)"})
            elif ext_num == 5:
                records.append({"tab": "SOUEXT", "Client": client, "TM": tm, "Docket #": docket, "Appl. #": sn, "Deadline": stat_3yr, "Status": "Final Statutory SOU Deadline (Ext 5 Filed - Max Reached)"})

    elif parsed["category"] == "OA":
        try: issue_dt = pd.to_datetime(parsed["issueDate"]).date()
        except: issue_dt = datetime.now().date()
        records.append({
            "tab": "OA", "Client": client, "TM": tm, "Docket #": docket, "Appl. #": sn,
            "OA Issue Date": issue_dt.strftime("%Y-%m-%d"),
            "3-Month Response Deadline": (issue_dt + relativedelta(months=3)).strftime("%Y-%m-%d"),
            "6-Month Extended Deadline": (issue_dt + relativedelta(months=6)).strftime("%Y-%m-%d"),
            "Status": "Pending Response"
        })

    elif parsed["category"] == "PUB":
        try: pub_dt = pd.to_datetime(parsed["publicationDate"]).date()
        except: pub_dt = datetime.now().date()
        records.append({
            "tab": "Pub", "Client": client, "TM": tm, "Docket #": docket, "Appl. #": sn,
            "Scheduled Publication Date": pub_dt.strftime("%Y-%m-%d"),
            "Publication Complete Date (30 Days)": (pub_dt + relativedelta(days=30)).strftime("%Y-%m-%d"),
            "Status": "Published - 30-Day Opposition Period Open"
        })

    return records

# -----------------------------------------------------------------------------
# 2. GMAIL INBOX SYNC
# -----------------------------------------------------------------------------
def fetch_uspto_emails(max_results=20):
    gmail_secrets = st.secrets["gmail"]
    creds = OAuthCredentials(
        token=None, refresh_token=gmail_secrets["refresh_token"],
        client_id=gmail_secrets["client_id"], client_secret=gmail_secrets["client_secret"],
        token_uri="https://oauth2.googleapis.com/token"
    )
    service = build('gmail', 'v1', credentials=creds)
    query = 'from:uspto.gov OR from:teas@uspto.gov OR from:TMOfficialNotices@uspto.gov OR subject:"FW: Official USPTO" OR subject:"Fwd: Official USPTO"'
    
    results = service.users().messages().list(userId='me', q=query, maxResults=max_results).execute()
    messages = results.get('messages', [])
    fetched = []
    
    for m in messages:
        msg = service.users().messages().get(userId='me', id=m['id'], format='full').execute()
        headers = msg.get('payload', {}).get('headers', [])
        subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), '')
        
        # Extract Body
        payload = msg.get('payload', {})
        body = ""
        if 'data' in payload.get('body', {}):
            body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='ignore')
        else:
            for part in payload.get('parts', []):
                if part.get('mimeType') in ['text/html', 'text/plain'] and 'data' in part.get('body', {}):
                    body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='ignore')
                    break
        
        fetched.append({"subject": subject, "body": body})
    return fetched

# -----------------------------------------------------------------------------
# 3. STREAMLIT UI
# -----------------------------------------------------------------------------
st.title("🛡️ USPTO Trademark Docketing Manager")

# Sidebar Configuration
st.sidebar.header("📁 Google Sheets Data")
uploaded_excel = st.sidebar.file_uploader("Upload Master Docket (.xlsx)", type=["xlsx"])
df_master = None
if uploaded_excel:
    try:
        df_master = pd.read_excel(uploaded_excel, sheet_name="Master Docket")
        if "SerialNumber" in df_master.columns:
            df_master["SerialNumber"] = df_master["SerialNumber"].astype(str).str.replace(r'\.0$', '', regex=True)
        st.sidebar.success(f"Loaded Master Docket ({len(df_master)} records)")
    except Exception as e:
        st.sidebar.error("Error reading file. Ensure tab is named 'Master Docket'.")

st.sidebar.header("📥 Gmail Sync")
max_emails = st.sidebar.slider("Emails to Scan", 5, 50, 20)

if st.sidebar.button("Fetch USPTO Emails", type="primary"):
    if "gmail" not in st.secrets:
        st.sidebar.error("Missing [gmail] credentials in Streamlit Secrets.")
    else:
        with st.spinner("Connecting to Gmail..."):
            try:
                emails = fetch_uspto_emails(max_results=max_emails)
                staged = []
                for em in emails:
                    parsed = parse_uspto_email(em["body"], em["subject"])
                    if parsed:  # Skips junk emails
                        staged.extend(format_docket_records(parsed, df_master))

                if "st_records" not in st.session_state: st.session_state["st_records"] = []
                st.session_state["st_records"].extend(staged)
                st.sidebar.success(f"Scanned {len(emails)} emails. Staged {len(staged)} deadlines.")
            except Exception as e:
                st.sidebar.error(f"Gmail Error: Verify your Refresh Token. {e}")

# Review & Export Table
if "st_records" in st.session_state and st.session_state["st_records"]:
    st.subheader("📋 Staged Deadlines for Export")
    
    # Load into DataFrame and clean N/A texts to empty strings
    df_staged = pd.DataFrame(st.session_state["st_records"]).fillna("")
    if 'Appl. #' in df_staged.columns:
        df_staged['Appl. #'] = df_staged['Appl. #'].astype(str).str.replace(r'\.0$', '', regex=True)
    
    # Interactive Table
    edited_df = st.data_editor(df_staged, num_rows="dynamic", use_container_width=True)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("❌ Clear Queue"):
            st.session_state["st_records"] = []
            st.rerun()
    with col2:
        # Export CSV explicitly dropping the backend routing "tab" column
        csv_df = edited_df.drop(columns=["tab"], errors="ignore")
        csv_data = csv_df.to_csv(index=False).encode('utf-8')
        st.download_button("⬇️ Download Formatted Deadlines (.csv)", data=csv_data, file_name="Formatted_TM_Deadlines.csv", mime="text/csv")
else:
    st.info("No deadlines staged. Upload your Master Docket in the sidebar and click 'Fetch USPTO Emails'.")