"""An AuthorityClient that replays a recorded snapshot instead of hitting the network.

It overrides only ``_get`` - the transport - so every test exercises the real batching,
resolution, merge-proof and audit logic. Nothing about the decision-making is stubbed.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from goldentarget.authority import AuthorityClient  # noqa: E402

SNAPSHOT_PATH = os.path.join(HERE, "fixtures", "authority_snapshot.json")
MINI_PACK = os.path.join(HERE, "fixtures", "mini_pack")


class OfflineAuthorityClient(AuthorityClient):
    def __init__(self, snapshot_path=SNAPSHOT_PATH, **kwargs):
        super().__init__(**kwargs)
        with open(snapshot_path, encoding="utf-8") as handle:
            self.snapshot = json.load(handle)
        self.unserved = []

    def _get(self, params):
        self.calls += 1
        if "accession" in params:
            wanted = [a.strip().upper() for a in params["accession"].split(",")]
            return [
                self.snapshot["accession"][a]
                for a in wanted
                if a in self.snapshot["accession"]
            ]
        if "gene" in params:
            tax = params.get("taxid", "9606")
            key = "%s|%s" % (params["gene"].upper(), tax)
            if key in self.snapshot["gene"]:
                return self.snapshot["gene"][key]
            self.unserved.append(key)
            return []
        if "protein" in params:
            key = "%s|%s" % (params["protein"].lower(), params.get("taxid", "9606"))
            return self.snapshot["protein"].get(key, [])
        return []
