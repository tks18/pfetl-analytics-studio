"""Tests for tray controller, resource resolution, /healthz liveness, and streaming exports."""

from __future__ import annotations

import csv
import http.server
import json
import os
import sqlite3
import threading
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pytest

from pfetl_analytics_studio import __version__
from pfetl_analytics_studio.analytics import (
    HouseholdAnalytics,
    HouseholdTransactionQuery,
    InvestmentAnalytics,
    LotFilter,
    OperationsAnalytics,
)
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.resources import resolve_resource_path
from pfetl_analytics_studio.server import (
    StudioServerConfig,
    append_startup_log,
    is_runtime_metadata_live,
    write_runtime_metadata,
)
from pfetl_analytics_studio.tray import StudioTrayController, load_tray_icon_image


def test_resource_resolution_and_tray_icon() -> None:
    """Bundled `logo.ico` and `logo.png` resolve cleanly and produce a 64x64 RGBA icon."""
    ico = resolve_resource_path("logo.ico")
    png = resolve_resource_path("logo.png")
    assert ico is not None and ico.exists()
    assert png is not None and png.exists()

    img = load_tray_icon_image()
    assert img.size == (64, 64)
    assert img.mode == "RGBA"


def test_is_runtime_metadata_live_verifies_healthz_identity(tmp_path: Path) -> None:
    """`is_runtime_metadata_live` rejects foreign HTTP servers and accepts Studio `/healthz`."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        mode = "foreign"

        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/healthz":
                self.send_response(404)
                self.end_headers()
                return
            payload = (
                {"status": "ready", "app": "pfetl-analytics-studio", "version": __version__}
                if _Handler.mode == "studio"
                else {"status": "ok", "app": "something-else"}
            )
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        meta = write_runtime_metadata(
            tmp_path / "runtime.json", pid=os.getpid(), host="127.0.0.1", port=port
        )
        assert not is_runtime_metadata_live(meta)

        _Handler.mode = "studio"
        assert is_runtime_metadata_live(meta)

        controller = StudioTrayController(
            config=StudioServerConfig(host="127.0.0.1", port=port),
            runtime_file=tmp_path / "runtime.json",
        )
        assert controller.adopt_existing_if_live()
        assert controller.studio_url == f"http://127.0.0.1:{port}"
        summary = controller.diagnostics_summary()
        assert __version__ in summary
        assert f"127.0.0.1:{port}" in summary
    finally:
        server.shutdown()
        server.server_close()


def test_startup_log_appends_timestamped_lines(tmp_path: Path) -> None:
    """`append_startup_log` writes timestamped diagnostic lines to the target log file."""
    log_path = tmp_path / "logs" / "studio_startup.log"
    written = append_startup_log("tray diagnostics check", log_file=log_path)
    assert written == log_path
    content = log_path.read_text(encoding="utf-8")
    assert "tray diagnostics check" in content


def test_chunked_household_transaction_csv_export_exceeds_page_cap(
    profile_with_full_atlas: PFETLProfile,
    tmp_path: Path,
) -> None:
    """`HouseholdAnalytics.export_transactions_csv_file` streams >200 rows without page cap."""
    with duckdb.connect(str(profile_with_full_atlas.duckdb_path)) as conn:
        for idx in range(250):
            conn.execute(
                """
                INSERT INTO silver.fact_expense_transactions
                    (UID, DATE, TIME, ASSET_ID, CURRENCY_ID, CATEGORY_NAME,
                     SUBCATEGORY_NAME, BASE_AMOUNT, AMOUNT_ACCOUNT, LOCAL_AMOUNT,
                     EXCH_RATE, IS_CORE, PAID, DESCRIPTION, TRANSFER_UID)
                VALUES (?, ?, '10:00:00', 'HDFC_SB', 'INR_INR', 'Food',
                        'Groceries', -100.0, -100.0, -100.0,
                        1.0, TRUE, 'Merchant', ?, NULL)
                """,
                [f"EXP_STREAM_{idx:04d}", date(2025, 4, 15), f"Streamed item {idx}"],
            )

    out_csv = tmp_path / "tx_export.csv"
    exported_path, count = HouseholdAnalytics.export_transactions_csv_file(
        profile_with_full_atlas,
        HouseholdTransactionQuery(transaction_kind="expense"),
        columns=["UID", "Date", "Description"],
        fields=["uid", "date", "desc"],
        row_formatter=lambda rows: [
            {"uid": r.uid, "date": r.tx_date.isoformat(), "desc": r.description} for r in rows
        ],
        target_path=out_csv,
        chunk_size=75,
    )
    assert exported_path == out_csv
    assert count >= 250
    with out_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = list(csv.reader(handle))
    assert reader[0] == ["UID", "Date", "Description"]
    assert len(reader) - 1 == count


def test_chunked_investment_lot_csv_export_streams_all_rows(
    profile_with_full_atlas: PFETLProfile,
    tmp_path: Path,
) -> None:
    """`InvestmentAnalytics.export_lots_csv_file` streams all lots without 100k slice load."""
    out_csv = tmp_path / "lots_export.csv"
    exported_path, count = InvestmentAnalytics.export_lots_csv_file(
        profile_with_full_atlas,
        LotFilter(),
        columns=["Lot ID", "ISIN", "Holding"],
        row_formatter=lambda rows, _cid: [(r.lot_id, r.isin, r.holding_type) for r in rows],
        target_path=out_csv,
        chunk_size=1,
    )
    assert exported_path == out_csv
    assert count >= 1
    with out_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = list(csv.reader(handle))
    assert reader[0] == ["Lot ID", "ISIN", "Holding"]
    assert len(reader) - 1 == count


def test_operations_blobopen_streams_large_payload_to_file(
    profile_with_full_atlas: PFETLProfile,
    tmp_path: Path,
) -> None:
    """`OperationsAnalytics.export_source_payload_to_file` streams >256 KiB BLOBs via blobopen."""
    payload_bytes = (b"PFETL-BLOB-CHUNK-" * 20_000)[:310_000]
    with sqlite3.connect(str(profile_with_full_atlas.control_plane_path)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cp_file_registry "
            "(file_id, file_name, file_category, file_type, sync_status, file_size_bytes) "
            "VALUES (?, ?, 'bank', 'csv', 'SYNCED', ?)",
            ("FILE_BIG_BLOB", "big_statement.csv", len(payload_bytes)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO cp_file_payloads (file_id, file_bytes) VALUES (?, ?)",
            ("FILE_BIG_BLOB", sqlite3.Binary(payload_bytes)),
        )
        conn.commit()

    out_file = tmp_path / "streamed_blob.csv"
    meta, written_path = OperationsAnalytics.export_source_payload_to_file(
        profile_with_full_atlas,
        "FILE_BIG_BLOB",
        target_path=out_file,
        chunk_size=65_536,
    )
    assert meta.available is True
    assert meta.file_name == "big_statement.csv"
    assert meta.byte_length == len(payload_bytes)
    assert meta.file_bytes is None
    assert written_path == out_file
    assert out_file.read_bytes() == payload_bytes


@pytest.mark.parametrize("flag", ["--cli", "--no-browser"])
def test_cli_parse_config_recognizes_tray_and_cli_flags(flag: str) -> None:
    """Smoke check that `--cli` and tray flags parse cleanly."""
    from pfetl_analytics_studio.cli import _parse_config  # pyright: ignore[reportPrivateUsage]

    cfg, is_cli = _parse_config([flag])
    assert is_cli is (flag == "--cli")
    assert cfg.host == "127.0.0.1"
