# receipt_ocr_server.py
import os
import json
import base64
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import google.generativeai as genai

#google sheets imports
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ======================================================
# MCP SERVER INIT
# ====================================================
load_dotenv()
mcp = FastMCP("receipt-ocr")

# Workspace setup
DEFAULT_HOME = os.path.expanduser("~")
DEFAULT_WORKSPACE = os.path.join(DEFAULT_HOME, "receipt-ocr", "workspace")
os.makedirs(DEFAULT_WORKSPACE, exist_ok=True)

# ==================================================
# GEMINI CONFIG
# =========================================================
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    raise RuntimeError("Missing GOOGLE_API_KEY in environment.")

genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# ============================================================
# CATEGORY STORAGE (local JSON)
# ============================================================
CATEGORIES_FILE = os.path.join(DEFAULT_WORKSPACE, "categories.json")

def load_categories():
    if not os.path.exists(CATEGORIES_FILE):
        # create default
        default = {
            "categories": [
                "Snacks", "Vegetables", "Meat", "Clothes",
                "Makeup/Skincare", "Condiments", "Dairy",
                "Beverages", "Bakery"
            ]
        }
        with open(CATEGORIES_FILE, "w") as f:
            json.dump(default, f, indent=2)
        return default["categories"]

    with open(CATEGORIES_FILE, "r") as f:
        data = json.load(f)
        return data.get("categories", [])

def save_categories(categories):
    with open(CATEGORIES_FILE, "w") as f:
        json.dump({"categories": categories}, f, indent=2)
# =================================================
# HELPERS
# ===========================================================
def load_image_as_base64(image_path: str) -> str:
    """
    Read an image file from disk and return its contents as a Base64-encoded string.
    Args:
        image_path (str): Absolute or relative path to the image file (e.g. .jpg, .png).
    Returns:
        str: Base64-encoded representation of the image, ready to be passed into Gemini OCR.
    """
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def parse_response(response) -> str:
    """
    Extract and concatenate all text outputs from a Gemini response object.
    Args:
        response: A response object returned by `model.generate_content(...)`.
    Returns:
        str: Plaintext string containing all extracted text parts, joined by newlines.
             If no text candidates are found, returns an empty string.
    Notes:
        - This function flattens multi-part responses into a single readable string.
        - Non-text parts (e.g. images, structured data) are ignored.
    """
    if not response.candidates:
        return ""
    text_parts = []
    for cand in response.candidates:
        if cand.content:
            for part in cand.content.parts:
                if getattr(part, "text", None):
                    text_parts.append(part.text)
    return "\n".join(text_parts)

# =========================================================
# GOOGLE SHEETS TOOLING
# ==================================================
SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SPREADSHEET_ID = os.environ.get("SHEETS_ID")  # put your target sheet ID in .env

class SheetsTool:
    def __init__(self):
        self.service = None

    def auth(self):
        creds = None
        try:
            creds = Credentials.from_authorized_user_file("token.json", SHEETS_SCOPES)
        except FileNotFoundError:
            pass

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    "credentials.json", SHEETS_SCOPES
                )
                creds = flow.run_local_server(port=8080, access_type="offline", prompt="consent")

            with open("token.json", "w") as token:
                token.write(creds.to_json())

        self.service = build("sheets", "v4", credentials=creds)

    def append_receipt(self, data: dict, sheet_name="Sheet1"):
        """
        Append receipt data into Google Sheet as rows.

        Args:
            data (dict): Receipt in structured form, e.g.
                {
                "vendor": "...",
                "date": "...",
                "line_items": [
                    {"item": "Milk", "price": 3.50, "category": "Dairy"},
                    {"item": "Discount", "price": -1.00, "category": "Dairy"},
                ]
                }
            sheet_name (str): Target Google Sheet tab name.

        Notes:
            - If a line item has a negative price, it will be subtracted
            from the previous item's price instead of being added as a new row.
            - If the first line is negative (no previous row), it will remain as-is.
        """

        if not self.service:
            self.auth()

        values = []
        vendor = data.get("vendor", "")
        date = data.get("date", "")
        for li in data.get("line_items", []):
            values.append([
                            date,
                            vendor,
                            li.get("item", ""),
                            li.get("price", ""),
                            li.get("category", "")
])

        body = {"values": values}

        self.service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{sheet_name}!A:D",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body,
        ).execute()

        return {"status": "success", "rows_added": len(values)}

sheets_tool = SheetsTool()
# ===================================================
# TOOLS
# ============================================================
@mcp.tool()
async def extract_receipt_text(image_path: str) -> str:
    """
    Extract raw text from a receipt image using Gemini OCR.

    Args:
        image_path (str): Local path to the receipt image (JPEG/PNG).

    Returns:
        str (JSON): {
            "raw_text": <plain text extracted from the receipt>
        }

    Notes:
        - OCR is performed by Gemini (`model.generate_content`).
        - Output is flattened into a single string, preserving line breaks.
        - If no text is detected, "raw_text" will be an empty string.
        - On error, returns {"error": "..."} with details.
    """
    try:
        image_b64 = load_image_as_base64(image_path)
        response = model.generate_content([
            {"mime_type": "image/jpeg", "data": image_b64},
            {"text": "Extract all text from this receipt."}
        ])
        return json.dumps({"raw_text": parse_response(response)}, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
async def structure_receipt_text(raw_text: str) -> str:
    """Please extract the following fields from this receipt text:

{raw_text}

Return valid JSON with exactly these keys:
- vendor (string)
- date (string, format DD/MM/YYYY if possible)
- total_amount (number)
- line_items: list of objects, each with:
   - item (string)
   - price (number)
   -category (string) - choose from predefined categories

Do not include any other fields.
    """
    try:
        categories = load_categories()
        response = model.generate_content(f"""
        Here is a receipt OCR text:

        {raw_text}

        I have the following spending categories:
        {categories}

        For each line item, classify it into the most appropriate category.
        Return JSON with:
        - vendor
        - date
        - total_amount
        - line_items: list of objects with 'item', 'price', 'category'
        """)
        return parse_response(response)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def append_to_sheet(structured_json: str) -> str:
    """
    Append structured receipt JSON into Google Sheets.
    Expected JSON:
    {
      "vendor": "...",
      "date": "...",
      "line_items": [
        {"item": "...", "price": ..., "category": "..."}
      ]
    }
    """
    try:
        # Step 1: Parse JSON
        data = json.loads(structured_json)

        # Step 2: Validate keys
        expected_keys = {"vendor", "date", "line_items"}
        if not all(k in data for k in expected_keys):
            return json.dumps(
                {"error": f"Invalid JSON: missing required keys {expected_keys}", "raw": data},
                indent=2
            )

        if not isinstance(data["line_items"], list):
            return json.dumps({"error": "line_items must be a list", "raw": data}, indent=2)

        for li in data["line_items"]:
            if not {"item", "price", "category"} <= li.keys():
                return json.dumps(
                    {"error": f"Invalid line_item format {li}. Must include item, price, and category.",
                     "raw": data},
                    indent=2
                )

        # Step 3: Append to sheet (with category column)
        result = sheets_tool.append_receipt(data)
        return json.dumps(result, indent=2)

    except json.JSONDecodeError:
        return json.dumps(
            {"error": "Invalid JSON received from LLM", "raw": structured_json},
            indent=2
        )
    except Exception as e:
        return json.dumps(
            {"error": str(e), "raw": structured_json},
            indent=2
        )


@mcp.tool()
async def add_category(new_category: str) -> str:
    """
    Add a new spending category to categories.json.

    Args:
        new_category (str): Name of the category to add.

    Returns:
        str (JSON): {
            "status": "added" | "exists" | "error",
            "category": <category_name>,
            "categories": [list of all categories]
        }
    Notes:
        - Category names are stored in a normalized form (trimmed, title-cased).
        - If the category already exists, no duplicate will be added.
    """
    try:
        categories = load_categories()
        if new_category not in categories:
            categories.append(new_category)
            save_categories(categories)
            return json.dumps({"status": "added", "categories": categories}, indent=2)
        else:
            return json.dumps({"status": "exists", "categories": categories}, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
async def remove_category(category: str) -> str:
    """
    Remove a spending category from categories.json.

    Args:
        category (str): Name of the category to remove.

    Returns:
        str (JSON): {
            "status": "removed" | "not_found" | "error",
            "category": <category_name>,
            "categories": [list of all categories]
        }
    Notes:
        - Matching is case-insensitive and ignores extra spaces.
    """
    try:
        categories = load_categories()
        if category in categories:
            categories.remove(category)
            save_categories(categories)
            return json.dumps({"status": "removed", "categories": categories}, indent=2)
        else:
            return json.dumps({"status": "not_found", "categories": categories}, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})
# =================================================
# MAIN
# ================================================
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--server_type", type=str, default="sse", choices=["sse", "stdio"])
    args = parser.parse_args()
    print("Starting Receipt OCR MCP server...")
    mcp.run(args.server_type)
