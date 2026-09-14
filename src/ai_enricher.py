import json
import google.generativeai as genai
from pydantic import BaseModel, Field
from typing import Optional


class USPTODocumentSchema(BaseModel):
    serialNumber: Optional[str] = Field(default="", description="8-digit application serial number")
    registrationNumber: Optional[str] = Field(default="", description="7-digit registration number")
    wordmark: Optional[str] = Field(default="", description="Literal mark element name")
    owner: Optional[str] = Field(default="", description="Owner / Client company name")
    docketNumber: Optional[str] = Field(default="", description="Internal docket or reference number")
    category: str = Field(default="OTHER", description="OA, SOU_EXT, MAINT, PUB, INTL, TTAB, ABANDONMENT, or OTHER")
    noticeType: str = Field(default="USPTO Notice", description="Exact title of document")
    isFilingReceipt: bool = Field(default=False, description="True if submission receipt/confirmation")
    issueDate: Optional[str] = Field(default="", description="YYYY-MM-DD or empty")
    publicationDate: Optional[str] = Field(default="", description="YYYY-MM-DD or empty")
    allowanceMailDate: Optional[str] = Field(default="", description="YYYY-MM-DD or empty")
    extensionNumber: Optional[int] = Field(default=None, description="Extension number 1, 2, 3, 4, or 5")


def enrich_parsing_with_gemini(api_key: str, email_subject: str, email_body: str, fallback_data: dict) -> dict:
    """Queries Gemini Flash API to extract structured USPTO metadata."""
    if not api_key:
        return fallback_data

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-1.5-flash")

        prompt = f"""
        You are an expert USPTO trademark docket paralegal.
        Extract data from this USPTO email and return a valid JSON object matching this schema:
        {USPTODocumentSchema.model_json_schema()}

        Subject: {email_subject}
        Body:
        {email_body}
        """

        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json", "temperature": 0.1}
        )

        if response and response.text:
            ai_data = json.loads(response.text)
            
            # Merge non-empty AI fields into fallback dictionary
            for key, val in ai_data.items():
                if val is not None and val != "" and val != "N/A":
                    fallback_data[key] = val

    except Exception as e:
        print(f"Gemini API enrichment skipped: {e}")

    return fallback_data