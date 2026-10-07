"""Read-only presentation of one explicitly selected local delivery workspace."""
from pathlib import Path
import re

from hardware.delivery import read, require, verify_input, verify_delivery
from autohls.experiments import file_hash
from scripts.audit_delivery_board import audit_directory


def status(base):
    active = base / "active.json"
    if not active.is_file():
        return {"available": False}
    version = read(active)["version"]
    require(isinstance(version, str) and re.fullmatch(r"v[1-9][0-9]*", version), "Invalid delivery version")
    directory = base / version
    launch = directory / "launch.json"
    if not launch.is_file():
        return {"available": False}
    provenance = verify_input(directory / "release")
    result = {"available": True, "mode": "read_only_delivery", "version": version,
              "state": "source_verified", "job": provenance["job"], "candidate": provenance["candidate"],
              "source_sha256": provenance["source_sha256"], "search_metrics": provenance["search_metrics"],
              "contract": provenance["contract"], "created_utc": provenance["created_utc"],
              "board_verified": False, "board": None, "receipt": None, "observation": None,
              "source": (directory / "release/inputs/kernel.cpp").read_text(encoding="utf-8")}
    observation = directory / "remote-observation.json"
    if observation.is_file():
        result["observation"] = read(observation)
        # A remote status snapshot is not an independently checked build receipt.
        result["state"] = "remote_snapshot_available"
    exported = directory / "exported"
    if exported.is_dir():
        result["receipt"] = verify_delivery(exported)
        result["state"] = "awaiting_board_confirmation"
        board = base / ('board-' + version)
        if (board / 'results').exists():
            receipt_sha256 = file_hash(exported / 'artifacts/delivery/bundle/delivery.json')
            result['board'] = audit_directory(board, receipt_sha256)
            result['receipt_sha256'] = receipt_sha256
            result['board_verified'] = True
            result['state'] = 'board_functional_verified'
    return result
