import base64
import streamlit as st
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

def get_gmail_service():
    """Authenticates and returns the Gmail API service instance."""
    gmail_secrets = st.secrets["gmail"]
    creds = Credentials(
        token=None,
        refresh_token=gmail_secrets["refresh_token"],
        client_id=gmail_secrets["client_id"],
        client_secret=gmail_secrets["client_secret"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"]
    )
    return build('gmail', 'v1', credentials=creds)


def fetch_uspto_emails(max_results=20):
    """
    Searches Gmail for direct and forwarded USPTO notification emails,
    returning a list of dicts with subject, sender, and body content.
    """
    service = get_gmail_service()
    
    # Search query matching direct USPTO notices & forwarded emails
    query = (
        'from:uspto.gov OR from:teas@uspto.gov OR from:TMOfficialNotices@uspto.gov '
        'OR subject:"FW: Official USPTO" OR subject:"Fwd: Official USPTO" '
        'OR "Notice of Allowance" OR "Notice of Publication" OR "Office Action"'
    )
    
    results = service.users().messages().list(userId='me', q=query, maxResults=max_results).execute()
    messages = results.get('messages', [])
    
    fetched_emails = []
    
    for msg_meta in messages:
        msg = service.users().messages().get(userId='me', id=msg_meta['id'], format='full').execute()
        headers = msg.get('payload', {}).get('headers', [])
        
        subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), '')
        sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), '')
        
        # Extract body text/html
        body = _extract_email_body(msg.get('payload', {}))
        
        fetched_emails.append({
            "id": msg_meta['id'],
            "subject": subject,
            "sender": sender,
            "body": body
        })
        
    return fetched_emails


def _extract_email_body(payload):
    """Recursively extracts plain text or HTML body from Gmail message payload."""
    if 'data' in payload.get('body', {}):
        return base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='ignore')
    
    parts = payload.get('parts', [])
    for part in parts:
        if part.get('mimeType') in ['text/html', 'text/plain']:
            if 'data' in part.get('body', {}):
                return base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='ignore')
        elif 'parts' in part:
            body = _extract_email_body(part)
            if body:
                return body
    return ""