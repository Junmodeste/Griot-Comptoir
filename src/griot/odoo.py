"""Minimal Odoo external API client (XML-RPC), configured from the environment."""

from __future__ import annotations

import os
import xmlrpc.client
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class OdooConfig:
    url: str
    db: str
    user: str
    api_key: str  # an Odoo API key (Preferences > Account Security), or the password on a test database

    @classmethod
    def from_env(cls) -> "OdooConfig":
        load_dotenv()
        missing = [k for k in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY") if not os.environ.get(k)]
        if missing:
            raise RuntimeError(f"Missing Odoo settings: {', '.join(missing)} (see .env.example)")
        return cls(os.environ["ODOO_URL"].rstrip("/"), os.environ["ODOO_DB"], os.environ["ODOO_USER"],
                   os.environ["ODOO_API_KEY"])


class OdooClient:
    def __init__(self, config: OdooConfig):
        self.config = config
        common = xmlrpc.client.ServerProxy(f"{config.url}/xmlrpc/2/common")
        self.uid = common.authenticate(config.db, config.user, config.api_key, {})
        if not self.uid:
            raise PermissionError(f"Odoo login refused for {config.user} on {config.db}")
        self._object = xmlrpc.client.ServerProxy(f"{config.url}/xmlrpc/2/object", allow_none=True)

    @classmethod
    def from_env(cls) -> "OdooClient":
        return cls(OdooConfig.from_env())

    def call(self, model: str, method: str, *args, **kwargs):
        try:
            return self._object.execute_kw(self.config.db, self.uid, self.config.api_key, model, method,
                                           list(args), kwargs)
        except xmlrpc.client.Fault as fault:
            # Odoo ran the method and committed, but cannot send back a Python None over XML-RPC.
            if "cannot marshal None" in fault.faultString:
                return None
            raise
