from flask import Flask, render_template, request
import pandas as pd
import re
from gl_accounts import gl_mapping

app = Flask(__name__)

# Define Major GL Accounts
MAJOR_GL_ACCOUNTS = {
    '40100', '40150', '40200', '40250', '40300',
    '40500', '40550', '40600', '40650', '40700',
    '60000', '60050', '60700', '60750', '60800',
    '60100', '60150', '60180', '60200', '60250',
    '60300', '60350'
}


# Mappings
mapping_rvw = {
    "invoice_number": "Reference",
    "vendor_name": "Description",
    "amount": "Amount",
    "date": "Tran Date",
    "gl_account": "Major"
}

mapping_craftable = {
    "invoice_number": "INVOICE NO      ",
    "vendor_name": "VENDOR      ",
    "amount": "GL AMOUNT      ",
    "total": "TOTAL      ",
    "date": "INVOICE DATE      ",
    "gl_account": "GL ACCOUNT      "
}

def clean_invoice(inv):
    """Remove non-digit characters from invoice numbers."""
    text = str(inv).strip().replace("–", "-").replace("—", "-")
    text = text.replace("-", "")
    return re.sub(r"[^\d]", "", text)

def build_lookup(df: pd.DataFrame, m: dict):
    """Return {invoice+gl_account: (vendor, amount, date, gl_description)}"""
    lut = {}
    for _, row in df.iterrows():
        inv = clean_invoice(row[m["invoice_number"]])
        vend = str(row[m["vendor_name"]]).strip()
        try:
            amt = float(row[m["amount"]])
        except Exception:
            amt = 0.0
        date_raw = row.get(m["date"], "")
        date = str(date_raw)[:10] if pd.notna(date_raw) else ""
        gl_raw = str(row.get(m["gl_account"], "")).strip().split(" ")[0]
        gl_desc = gl_mapping.get(gl_raw, f"{gl_raw} - Unknown")
        key = f"{inv}_{gl_raw}"
        lut[key] = (vend, amt, date, gl_desc)
    return lut

@app.route("/", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        rvw_file = request.files["rvw"]
        food_file = request.files["food"]
        bev_file = request.files["bev"]

        df_rvw = pd.read_excel(rvw_file, header=3)
        df_food = pd.read_excel(food_file, sheet_name="2. GL Distribution", header=5)
        df_bev  = pd.read_excel(bev_file,  sheet_name="2. GL Distribution", header=5)
        df_craftable = pd.concat([df_food, df_bev], ignore_index=True)

        rvw_lookup = build_lookup(df_rvw, mapping_rvw)
        craft_lookup = build_lookup(df_craftable, mapping_craftable)

        rows = []
        for key, (v_rvw, a_rvw, d_rvw, gl_rvw) in rvw_lookup.items():
            if key in craft_lookup:
                v_cr, a_cr, d_cr, gl_cr = craft_lookup[key]
                diff = a_rvw - a_cr
            else:
                v_cr, a_cr, d_cr, gl_cr = "", 0.0, "", ""
                diff = a_rvw
            rows.append((key, gl_rvw, d_rvw, v_rvw, a_rvw, a_cr, diff, v_cr, d_cr))

        for key, (v_cr, a_cr, d_cr, gl_cr) in craft_lookup.items():
            if key not in rvw_lookup:
                rows.append((key, gl_cr, "", "", 0.0, a_cr, -a_cr, v_cr, d_cr))

        # Sort rows by GL → Vendor → Amount descending
        rows.sort(key=lambda x: (x[1], x[3], -abs(x[4])))


        return render_template("results.html", rows=rows)

    return render_template("upload.html")

if __name__ == "__main__":
    app.run(debug=True)
