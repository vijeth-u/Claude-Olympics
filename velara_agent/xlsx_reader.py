"""Minimal, dependency-free .xlsx reader.

A numeric cell whose style has a date number-format comes back as ``datetime``; a numeric cell
WITHOUT a date format comes back as a plain int/float. That is exactly how a "date stored as a
number" defect shows up in a workbook, so the reader keeps the distinction.
"""
import datetime as _dt
import re
import zipfile
import xml.etree.ElementTree as ET

_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_M = "{%s}" % _NS["m"]
_BUILTIN_DATE_FMT_IDS = set(range(14, 23)) | set(range(27, 37)) | {45, 46, 47} | set(range(50, 59))


def _is_date_format_code(code):
    if not code:
        return False
    cleaned = re.sub(r'"[^"]*"', "", code)
    cleaned = re.sub(r"\[[^\]]*\]", "", cleaned)
    cleaned = re.sub(r"\\.", "", cleaned)
    return bool(re.search(r"[ydmhs]", cleaned, flags=re.IGNORECASE))


def _col_index(ref):
    idx = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1


def _text_of(node):
    return "".join(t.text or "" for t in node.iter(_M + "t"))


class Workbook:
    def __init__(self, path):
        self._zip = zipfile.ZipFile(path)
        self._names = set(self._zip.namelist())
        self._shared = self._load_shared()
        self._date_styles = self._load_date_styles()
        self.date1904 = False
        self._sheets = self._load_sheets()

    def _xml(self, part):
        return ET.fromstring(self._zip.read(part))

    def _load_shared(self):
        part = "xl/sharedStrings.xml"
        if part not in self._names:
            return []
        return [_text_of(si) for si in self._xml(part).findall("m:si", _NS)]

    def _load_date_styles(self):
        part = "xl/styles.xml"
        if part not in self._names:
            return set()
        root = self._xml(part)
        custom = {}
        nfs = root.find("m:numFmts", _NS)
        if nfs is not None:
            for nf in nfs.findall("m:numFmt", _NS):
                custom[int(nf.get("numFmtId"))] = nf.get("formatCode", "")
        out = set()
        xfs = root.find("m:cellXfs", _NS)
        if xfs is not None:
            for i, xf in enumerate(xfs.findall("m:xf", _NS)):
                fid = int(xf.get("numFmtId", "0"))
                if fid in _BUILTIN_DATE_FMT_IDS or (fid in custom and _is_date_format_code(custom[fid])):
                    out.add(i)
        return out

    def _load_sheets(self):
        wb = self._xml("xl/workbook.xml")
        pr = wb.find("m:workbookPr", _NS)
        if pr is not None and pr.get("date1904") in ("1", "true"):
            self.date1904 = True
        rels = self._xml("xl/_rels/workbook.xml.rels")
        targets = {r.get("Id"): r.get("Target") for r in rels.findall("pr:Relationship", _NS)}
        sheets = {}
        for s in wb.find("m:sheets", _NS).findall("m:sheet", _NS):
            t = targets[s.get("{%s}id" % _NS["r"])].lstrip("/")
            sheets[s.get("name")] = t if t.startswith("xl/") else "xl/" + t
        return sheets

    @property
    def sheetnames(self):
        return list(self._sheets)

    def rows(self, name):
        out = []
        for row in self._xml(self._sheets[name]).iter(_M + "row"):
            cells = {_col_index(c.get("r")): self._value(c) for c in row.findall("m:c", _NS)}
            out.append([cells.get(i) for i in range(max(cells) + 1)] if cells else [])
        return out

    def _value(self, c):
        t = c.get("t", "n")
        if t == "inlineStr":
            node = c.find("m:is", _NS)
            return _text_of(node) if node is not None else None
        v = c.find("m:v", _NS)
        if v is None or v.text is None:
            return None
        raw = v.text
        if t == "s":
            return self._shared[int(raw)]
        if t in ("str", "e"):
            return raw
        if t == "b":
            return raw == "1"
        num = float(raw)
        if int(c.get("s", "0")) in self._date_styles:
            base = _dt.datetime(1904, 1, 1) if self.date1904 else _dt.datetime(1899, 12, 30)
            return base + _dt.timedelta(days=num)
        return int(num) if num.is_integer() else num
