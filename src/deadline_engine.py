import re
from datetime import datetime
from dateutil.relativedelta import relativedelta
import pandas as pd
from src.parser import clean_wordmark, clean_owner_name


def _parse_date_obj(date_str: str) -> datetime.date:
    if not date_str or date_str == "N/A":
        return datetime.now().date()
    try:
        return pd.to_datetime(date_str).date()
    except Exception:
        return datetime.now().date()


def generate_docket_records(parsed_info: dict, df_master: pd.DataFrame = None) -> list[dict]:
    """Applies USPTO deadline rules and formats rows for SOUEXT, OA, and Pub tabs."""
    sn = parsed_info.get("serialNumber", "")
    client = parsed_info.get("owner", "N/A")
    tm = parsed_info.get("wordmark", "N/A")
    docket = parsed_info.get("docketNumber", "N/A")

    # Relational lookup against Master Docket
    if sn and df_master is not None and not df_master.empty:
        match = df_master[df_master['SerialNumber'].astype(str).str.contains(sn, na=False)]
        if len(match) > 0:
            m_row = match.iloc[0]
            if (tm == "N/A" or not tm) and 'Wordmark' in m_row and pd.notna(m_row['Wordmark']):
                tm = clean_wordmark(m_row['Wordmark'])
            if (client == "N/A" or not client) and 'OwnerFullText' in m_row and pd.notna(m_row['OwnerFullText']):
                client = clean_owner_name(m_row['OwnerFullText'])

    if not tm or tm == "N/A":
        tm = "[Design Mark]"

    records = []
    category = parsed_info.get("category", "OTHER")

    # 1. SOU / EXT TAB ROUTING
    if category == "SOU_EXT":
        base_date_str = parsed_info.get("allowanceMailDate") or parsed_info.get("issueDate")
        base_dt = _parse_date_obj(base_date_str)
        ext_num = parsed_info.get("extensionNumber")

        if not parsed_info.get("isFilingReceipt") and not ext_num:
            # Notice of Allowance
            dl_6mo = (base_dt + relativedelta(months=6)).strftime("%Y-%m-%d")
            dl_3yr = (base_dt + relativedelta(years=3)).strftime("%Y-%m-%d")

            records.append({
                "tab": "SOUEXT",
                "Client": client, "TM": tm, "Docket #": docket, "Appl. #": sn,
                "Deadline": dl_6mo, "Status": "1st SOU / Extension Deadline (0 Extensions Filed)"
            })
            records.append({
                "tab": "SOUEXT",
                "Client": client, "TM": tm, "Docket #": docket, "Appl. #": sn,
                "Deadline": dl_3yr, "Status": "36-Month Final Statutory SOU Limit"
            })

        elif ext_num:
            stat_3yr = (base_dt + relativedelta(years=3)).strftime("%Y-%m-%d")
            if 1 <= ext_num <= 4:
                next_ext = ext_num + 1
                next_dl = (base_dt + relativedelta(months=6 * next_ext)).strftime("%Y-%m-%d")
                suf = "2nd" if next_ext == 2 else ("3rd" if next_ext == 3 else f"{next_ext}th")
                records.append({
                    "tab": "SOUEXT",
                    "Client": client, "TM": tm, "Docket #": docket, "Appl. #": sn,
                    "Deadline": next_dl, "Status": f"{suf} Extension or SOU Due (Ext {ext_num} Filed)"
                })
            elif ext_num == 5:
                records.append({
                    "tab": "SOUEXT",
                    "Client": client, "TM": tm, "Docket #": docket, "Appl. #": sn,
                    "Deadline": stat_3yr, "Status": "Final Statutory SOU Deadline (Ext 5 Filed - Max Reached)"
                })

    # 2. OFFICE ACTION TAB ROUTING
    elif category == "OA":
        issue_dt = _parse_date_obj(parsed_info.get("issueDate"))
        dl_3mo = (issue_dt + relativedelta(months=3)).strftime("%Y-%m-%d")
        dl_6mo = (issue_dt + relativedelta(months=6)).strftime("%Y-%m-%d")

        records.append({
            "tab": "OA",
            "Client": client, "TM": tm, "Docket #": docket, "Appl. #": sn,
            "OA Issue Date": issue_dt.strftime("%Y-%m-%d"),
            "3-Month Response Deadline": dl_3mo,
            "6-Month Extended Deadline": dl_6mo,
            "Status": "Pending Response"
        })

    # 3. NOTICE OF PUBLICATION TAB ROUTING
    elif category == "PUB":
        pub_dt = _parse_date_obj(parsed_info.get("publicationDate") or parsed_info.get("issueDate"))
        pub_comp = (pub_dt + relativedelta(days=30)).strftime("%Y-%m-%d")

        records.append({
            "tab": "Pub",
            "Client": client, "TM": tm, "Docket #": docket, "Appl. #": sn,
            "Scheduled Publication Date": pub_dt.strftime("%Y-%m-%d"),
            "Publication Complete Date (30 Days)": pub_comp,
            "Status": "Published - 30-Day Opposition Period Open"
        })

    return records