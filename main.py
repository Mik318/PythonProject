import base64
import json
import os

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
QBO_BASE = os.getenv("QBO_BASE")
COMPANY_ID = os.getenv("COMPANY_ID")

TOKENS_FILE = "tokens.json"

def save_tokens(tokens):
    with open(TOKENS_FILE, "w") as f:
        json.dump(tokens, f)

def load_tokens():
    try:
        with open(TOKENS_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

class QuickBooksClient:
    def __init__(self, access_token, realm_id):
        self.access_token = access_token
        self.realm_id = realm_id
        self.base_url = "https://sandbox-quickbooks.api.intuit.com/v3/company"

    def headers(self, accept="application/json"):
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/text",
            "Accept": accept
        }

    def query(self, query_str):
        url = f"{self.base_url}/{self.realm_id}/query"
        response = requests.post(url, headers=self.headers(), data=query_str)
        return response

    def get_company_info(self):
        url = f"{self.base_url}/{self.realm_id}/companyinfo/{self.realm_id}"
        response = requests.get(url, headers=self.headers())
        return response

def get_qb_client():
    tokens = load_tokens()
    access_token = tokens.get("access_token")
    realm_id = tokens.get("realmId") or COMPANY_ID
    if not access_token or not realm_id:
        return None
    return QuickBooksClient(access_token, realm_id)

@app.get("/customer-info")
def customer_info():
    """
    Obtiene información básica de la empresa (equivalente a getCustomerData)
    """
    qb = get_qb_client()
    if not qb:
        return {"error": "No autenticado. Usa /connect y asegúrate de tener realmId"}
    response = qb.get_company_info()
    return {
        "status_code": response.status_code,
        "data": response.json() if response.status_code == 200 else response.text
    }

@app.get("/refresh")
def refresh_access_token():
    """
    Refresca el access_token usando el refresh_token guardado en .env
    """
    refresh_token = os.getenv("REFRESH_TOKEN")
    if not refresh_token:
        return {"error": "No hay refresh_token en el entorno"}

    token_url = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
    auth_header = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {auth_header}",
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    response = requests.post(token_url, headers=headers, data=data)
    if response.status_code != 200:
        return {"error": response.text}

    tokens = response.json()
    # Mantén el realmId si ya existe
    old_tokens = load_tokens()
    if "realmId" in old_tokens:
        tokens["realmId"] = old_tokens["realmId"]
    save_tokens(tokens)
    return {"success": True, "tokens": tokens}

@app.get("/customers")
def get_customers():
    """
    Obtiene todos los clientes (Customer) de QuickBooks, mostrando solo los campos principales.
    """
    qb = get_qb_client()
    if not qb:
        return {"error": "No autenticado. Usa /connect y asegúrate de tener realmId"}
    response = qb.query("SELECT * FROM Customer")
    if response.status_code != 200:
        return {"error": response.text}
    data = response.json()
    customers = data.get("QueryResponse", {}).get("Customer", [])
    def extract_customer_fields(cust):
        return {
            "Id": cust.get("Id"),
            "DisplayName": cust.get("DisplayName"),
            "PrimaryEmailAddr": cust.get("PrimaryEmailAddr"),
            "PrimaryPhone": cust.get("PrimaryPhone"),
            "CompanyName": cust.get("CompanyName"),
            "Balance": cust.get("Balance"),
        }
    filtered_customers = [extract_customer_fields(cust) for cust in customers]
    return {"customers": filtered_customers}

@app.get("/invoices")
def get_invoices():
    """
    Obtiene todas las facturas (Invoice) de QuickBooks, mostrando solo los campos principales.
    """
    qb = get_qb_client()
    if not qb:
        return {"error": "No autenticado. Usa /connect y asegúrate de tener realmId"}
    response = qb.query("SELECT * FROM Invoice")
    if response.status_code != 200:
        return {"error": response.text}
    data = response.json()
    invoices = data.get("QueryResponse", {}).get("Invoice", [])
    def extract_invoice_fields(inv):
        return {
            "Id": inv.get("Id"),
            "DocNumber": inv.get("DocNumber"),
            "CustomerRef": inv.get("CustomerRef"),
            "BillEmail": inv.get("BillEmail"),
            "TxnDate": inv.get("TxnDate"),
            "DueDate": inv.get("DueDate"),
            "Line": inv.get("Line"),
            "TxnTaxDetail": inv.get("TxnTaxDetail"),
            "TotalAmt": inv.get("TotalAmt"),
            "Balance": inv.get("Balance"),
        }
    filtered_invoices = [extract_invoice_fields(inv) for inv in invoices]
    return {"invoices": filtered_invoices}

@app.get("/customer-invoices")
def customer_invoices():
    """
    Devuelve por cada factura:
    - Cliente
    - Email del cliente
    - Fecha de la factura
    - Fecha de vencimiento
    - Ubicación de venta (si existe)
    - Número de factura
    - Información asociada al invoice: type, name, qty, rate, amount
    """
    qb = get_qb_client()
    if not qb:
        return {"error": "No autenticado. Usa /connect y asegúrate de tener realmId"}
    # Obtener todos los clientes
    cust_resp = qb.query("SELECT Id, DisplayName, PrimaryEmailAddr FROM Customer")
    if cust_resp.status_code != 200:
        return {"error": cust_resp.text}
    customers = cust_resp.json().get("QueryResponse", {}).get("Customer", [])
    customer_map = {c["Id"]: c for c in customers}

    # Obtener todas las facturas
    inv_resp = qb.query("SELECT * FROM Invoice")
    if inv_resp.status_code != 200:
        return {"error": inv_resp.text}
    invoices = inv_resp.json().get("QueryResponse", {}).get("Invoice", [])

    result = []
    for inv in invoices:
        cust_ref = inv.get("CustomerRef", {})
        cust_id = cust_ref.get("value")
        customer = customer_map.get(cust_id, {})
        # Ubicación de venta puede estar en SalesTermRef, ShipAddr, o LocationRef
        location = None
        if "ShipAddr" in inv and inv["ShipAddr"]:
            location = inv["ShipAddr"].get("City")
        elif "LocationRef" in inv and inv["LocationRef"]:
            location = inv["LocationRef"].get("name")
        elif "SalesTermRef" in inv and inv["SalesTermRef"]:
            location = inv["SalesTermRef"].get("name")

        invoice_lines = []
        for line in inv.get("Line", []):
            detail = line.get("SalesItemLineDetail", {})
            invoice_lines.append({
                "type": line.get("DetailType"),
                "name": detail.get("ItemRef", {}).get("name"),
                "qty": detail.get("Qty"),
                "rate": detail.get("UnitPrice"),
                "amount": line.get("Amount"),
            })

        result.append({
            "customer": customer.get("DisplayName"),
            "customer_email": customer.get("PrimaryEmailAddr", {}).get("Address"),
            "invoice_date": inv.get("TxnDate"),
            "invoice_due_date": inv.get("DueDate"),
            "location_of_sale": location,
            "invoice_no": inv.get("DocNumber"),
            "lines": invoice_lines
        })
    return {"customer_invoices": result}

class InvoiceLineDetail(BaseModel):
    Amount: float
    DetailType: str
    SalesItemLineDetail: Optional[Dict[str, Any]]

class InvoiceCreateModel(BaseModel):
    CustomerRef: Dict[str, Any]
    Line: List[InvoiceLineDetail]
    BillEmail: Optional[Dict[str, Any]] = None
    TxnDate: Optional[str] = None
    DueDate: Optional[str] = None
    # Puedes agregar más campos según lo necesites

@app.post("/create-invoice")
async def create_invoice(invoice_data: InvoiceCreateModel):
    """
    Crea una factura en QuickBooks.
    El body debe contener los datos necesarios para la factura.
    Ejemplo mínimo:
    {
      "CustomerRef": {"value": "1"},
      "Line": [
        {
          "Amount": 100.0,
          "DetailType": "SalesItemLineDetail",
          "SalesItemLineDetail": {
            "ItemRef": {"value": "3"}
          }
        }
      ]
    }
    """
    qb = get_qb_client()
    if not qb:
        return {"error": "No autenticado. Usa /connect y asegúrate de tener realmId"}

    url = f"https://sandbox-quickbooks.api.intuit.com/v3/company/{qb.realm_id}/invoice"
    headers = qb.headers()
    headers["Content-Type"] = "application/json"
    response = requests.post(url, headers=headers, json=invoice_data.dict(exclude_none=True))
    if response.status_code not in (200, 201):
        return {"error": response.text}
    return response.json()

@app.get("/inventory")
def get_inventory():
    """
    Obtiene todos los productos de inventario (Item) de QuickBooks.
    Útil para seleccionar productos al crear facturas.
    """
    qb = get_qb_client()
    if not qb:
        return {"error": "No autenticado. Usa /connect y asegúrate de tener realmId"}
    response = qb.query("SELECT * FROM Item WHERE Type = 'Inventory'")
    if response.status_code != 200:
        return {"error": response.text}
    data = response.json()
    items = data.get("QueryResponse", {}).get("Item", [])
    def extract_item_fields(item):
        return {
            "Id": item.get("Id"),
            "Name": item.get("Name"),
            "Sku": item.get("Sku"),
            "QtyOnHand": item.get("QtyOnHand"),
            "UnitPrice": item.get("UnitPrice"),
            "Type": item.get("Type"),
            "Active": item.get("Active"),
        }
    filtered_items = [extract_item_fields(item) for item in items]
    return {"inventory": filtered_items}
