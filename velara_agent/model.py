"""Load the pack into clean, typed records while remembering what was dirty.

Cleaning (each is also reportable as a finding): country names are stripped so "United States "
groups with "United States" (raw value kept); date columns accept real dates or Excel serials
(serials converted, column remembered as number-stored); each monthly tab is kept separately so
month-on-month changes can be compared.
"""
import csv
import datetime as dt
import os
from collections import defaultdict

from .xlsx_reader import Workbook

COLUMNS = {
    "country": "Country", "status": "Launch Status", "sku": "SKU", "dose": "Dose",
    "planned_submission": "Planned Submission", "actual_submission": "Actual Submission Date",
    "target_approval": "Target Approval Date", "actual_approval": "Actual Approval Date",
    "expected_ship": "Expected Launch Shipment Date", "actual_ship": "Actual Launch Shipment Date",
    "demand_strategy": "Demand Strategy", "pathway": "Regulatory Pathway",
    "supply_route": "Supply Route", "site": "Packaging Site", "site_role": "Site Role",
    "plant_flag": "Product Code Set Up At Plant?", "plant_date": "Plant Setup Date",
    "wh_flag": "Product Code Set Up At Warehouse?", "wh_date": "Warehouse Setup Date",
    "order_flag": "Ordering Route Created?", "demand_flag": "Demand Loaded In Planning System?",
    "demand_date": "Demand Loaded Date", "change_log": "Shipment Date Change Log",
    "refresh": "RefreshDate",
}
DATE_KEYS = {"planned_submission", "actual_submission", "target_approval", "actual_approval",
             "expected_ship", "actual_ship", "plant_date", "wh_date", "demand_date", "refresh"}
FLAG_KEYS = ("plant_flag", "wh_flag", "order_flag", "demand_flag")


def norm(text):
    return " ".join(str(text).split()).casefold() if text is not None else ""


def to_date(value, date1904=False):
    """Return (date or None, stored_as_number)."""
    if value is None or value == "":
        return None, False
    if isinstance(value, dt.datetime):
        return value.date(), False
    if isinstance(value, dt.date):
        return value, False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        base = dt.date(1904, 1, 1) if date1904 else dt.date(1899, 12, 30)
        return base + dt.timedelta(days=int(value)), True
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
            try:
                return dt.datetime.strptime(value.strip(), fmt).date(), False
            except ValueError:
                pass
    return None, False


class Extract:
    def __init__(self, name, rows, numeric_date_columns):
        self.name = name
        self.rows = rows
        self.numeric_date_columns = numeric_date_columns
        refreshes = [r["refresh"] for r in rows if r["refresh"]]
        self.refresh = max(refreshes) if refreshes else None

    def by_country(self):
        out = defaultdict(list)
        for r in self.rows:
            out[r["country"]].append(r)
        return out


class Pack:
    def __init__(self, pack_dir):
        wb = Workbook(os.path.join(pack_dir, "VELARA_tracker.xlsx"))
        self.date1904 = wb.date1904
        self.extracts = self._load_extracts(wb)
        self.latest = self.extracts[-1]
        self.today = self.latest.refresh  # judge everything as of the latest RefreshDate
        self.countries = self._load_ref_countries(wb)
        self.skus = {(r.get("sku") or "").strip(): r.get("dose") for r in self._table(wb, "Ref_SKUs")}
        self.sites = self._load_ref_sites(wb)
        self.dashboard = self._load_dashboard(os.path.join(pack_dir, "dashboard_status.csv"))

    def _load_extracts(self, wb):
        extracts = []
        for name in wb.sheetnames:
            if not name.lower().startswith("tracker"):
                continue
            raw = wb.rows(name)
            if not raw:
                continue
            header = [norm(h) for h in raw[0]]
            col = {k: header.index(norm(lbl)) for k, lbl in COLUMNS.items() if norm(lbl) in header}
            numeric, records = set(), []
            for line_no, values in enumerate(raw[1:], start=2):
                if not any(v not in (None, "") for v in values):
                    continue
                rec = {"_sheet": name, "_line": line_no}
                for key in COLUMNS:
                    i = col.get(key)
                    val = values[i] if i is not None and i < len(values) else None
                    if key in DATE_KEYS:
                        d, as_num = to_date(val, self.date1904)
                        if as_num:
                            numeric.add(COLUMNS[key])
                        rec[key] = d
                    else:
                        rec[key] = val if (val is None or isinstance(val, str)) else str(val)
                rec["country_raw"] = rec["country"]
                rec["country"] = (rec["country"] or "").strip()
                for k in FLAG_KEYS:
                    v = rec[k]
                    rec[k] = v.strip() if isinstance(v, str) and v.strip() else None
                records.append(rec)
            extracts.append(Extract(name, records, sorted(numeric)))
        extracts.sort(key=lambda e: (e.refresh or dt.date.min, e.name))
        return extracts

    @staticmethod
    def _table(wb, name):
        if name not in wb.sheetnames:
            return []
        raw = wb.rows(name)
        header = [norm(h) for h in raw[0]]
        return [dict(zip(header, r + [None] * (len(header) - len(r)))) for r in raw[1:] if any(r)]

    def _load_ref_countries(self, wb):
        out = {}
        for r in self._table(wb, "Ref_Countries"):
            vk = next((k for k in r if k.startswith("forecast volume")), None)
            v = r.get(vk) if vk else None
            out[(r.get("country") or "").strip()] = {
                "volume": int(float(v)) if v not in (None, "") else 0,
                "site": (r.get("packaging site") or "").strip()}
        return out

    def _load_ref_sites(self, wb):
        out = {}
        for r in self._table(wb, "Ref_Sites"):
            lk = next((k for k in r if k.startswith("lead time")), None)
            ck = next((k for k in r if k.startswith("annual packaging capacity")), None)
            weeks = int(float(r[lk])) if lk and r[lk] is not None else 0
            out[(r.get("packaging site") or "").strip()] = {
                "lead_weeks": weeks, "lead_days": weeks * 7,
                "capacity": int(float(r[ck])) if ck and r[ck] is not None else 0}
        return out

    @staticmethod
    def _load_dashboard(path):
        out = {}
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8-sig") as fh:
                for row in csv.DictReader(fh):
                    out[(row.get("country") or "").strip()] = (row.get("status") or "").strip().upper()
        return out

    def all_countries(self):
        names = set(self.countries) | set(self.dashboard)
        for e in self.extracts:
            names |= {r["country"] for r in e.rows}
        return sorted(n for n in names if n)
