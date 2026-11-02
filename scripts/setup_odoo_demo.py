"""Prepare an EMPTY test database for the demo: company in Côte d'Ivoire, currency XOF.

Needs the Inventory, Sales, Purchase and Invoicing apps installed. Do not run it on a
real company: Odoo refuses to change the currency once entries exist, and it renames the company.

Usage: python scripts/setup_odoo_demo.py
"""

from griot.odoo import OdooClient


def main() -> None:
    odoo = OdooClient.from_env()
    installed = {m["name"] for m in odoo.call("ir.module.module", "search_read",
                                               [("state", "=", "installed")], fields=["name"])}
    missing = {"stock", "sale_management", "purchase", "account"} - installed
    if missing:
        raise SystemExit(f"Install these apps first: {', '.join(sorted(missing))}")
    xof = odoo.call("res.currency", "search", [("name", "=", "XOF"), ("active", "in", [True, False])])
    odoo.call("res.currency", "write", xof, {"active": True})
    ci = odoo.call("res.country", "search", [("code", "=", "CI")])
    company = odoo.call("res.users", "read", [odoo.uid], fields=["company_id"])[0]["company_id"][0]
    odoo.call("res.company", "write", [company], {"name": "Boutique Démo Griot", "currency_id": xof[0],
                                                  "country_id": ci[0]})
    print(odoo.call("res.company", "read", [company], fields=["name", "currency_id", "country_id"]))


if __name__ == "__main__":
    main()
