"""Tests for Studio server lifecycle, /healthz, runtime metadata, CLI, and export chart polish."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from nicegui import app as nicegui_app
from pptx import Presentation

from pfetl_analytics_studio import __version__
from pfetl_analytics_studio.cli import main as cli_main
from pfetl_analytics_studio.reports import (
    RenderedChartBlock,
    RenderedProvenanceBlock,
    RenderedTextBlock,
    ReportContext,
    ReportExporter,
    ReportResult,
    check_static_export_capability,
)
from pfetl_analytics_studio.reports.exporters import (
    _prepare_pdf_chart_figure,  # pyright: ignore[reportPrivateUsage]
)
from pfetl_analytics_studio.semantic import PeriodContext, PeriodPreset
from pfetl_analytics_studio.server import (
    StudioServerConfig,
    is_runtime_metadata_live,
    read_runtime_metadata,
    register_health_endpoint,
    remove_runtime_metadata,
    resolve_loopback_port,
    validate_loopback_host,
    validate_startup_preflight,
    write_runtime_metadata,
)


def test_loopback_host_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loopback hosts are accepted; remote hosts are rejected unless dev override is set."""
    for host in ("127.0.0.1", "localhost", "::1"):
        validate_loopback_host(host)

    monkeypatch.delenv("PFETL_STUDIO_DEV_ALLOW_REMOTE", raising=False)
    with pytest.raises(ValueError, match="loopback"):
        validate_loopback_host("0.0.0.0")

    monkeypatch.setenv("PFETL_STUDIO_DEV_ALLOW_REMOTE", "1")
    validate_loopback_host("0.0.0.0")


def test_port_resolution_explicit_occupied_and_auto() -> None:
    """Explicit occupied port fails clearly; auto-port resolves a free loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        busy_port = int(occupied.getsockname()[1])

        with pytest.raises(RuntimeError, match="already in use"):
            resolve_loopback_port("127.0.0.1", busy_port)

    auto_port = resolve_loopback_port("127.0.0.1", None)
    assert auto_port > 0
    explicit_ok = resolve_loopback_port("127.0.0.1", auto_port)
    assert explicit_ok == auto_port


def test_runtime_metadata_atomic_allowed_fields_and_stale_detection(tmp_path: Path) -> None:
    """Runtime metadata writes atomically, omits secrets, and detects stale PIDs."""
    rt_path = tmp_path / "studio_runtime.json"
    written = write_runtime_metadata(rt_path, pid=999999, host="127.0.0.1", port=59999)
    assert set(written.keys()) == {"pid", "host", "port", "version", "started_at", "health_url"}
    assert written["version"] == __version__
    assert written["health_url"] == "http://127.0.0.1:59999/healthz"

    loaded = read_runtime_metadata(rt_path)
    assert loaded == written
    assert not is_runtime_metadata_live(loaded or {})

    remove_runtime_metadata(rt_path)
    assert not rt_path.exists()


def test_healthz_endpoint_payload_and_zero_secrets() -> None:
    """`GET /healthz` returns readiness metadata without financial or profile paths."""
    register_health_endpoint("127.0.0.1", 8123)
    client = TestClient(nicegui_app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "status": "ready",
        "app": "pfetl-analytics-studio",
        "version": __version__,
        "host": "127.0.0.1",
        "port": 8123,
    }


def test_cli_requires_paired_settings_and_rules_and_rejects_remote_host() -> None:
    """CLI returns non-zero on unpaired profile paths or remote hosts."""
    assert cli_main(["--host", "0.0.0.0"]) == 1
    assert cli_main(["--settings", "only_settings.toml"]) == 1
    assert cli_main(["--rules", "only_rules.toml"]) == 1

    with pytest.raises(ValueError, match="together"):
        validate_startup_preflight(
            StudioServerConfig(host="127.0.0.1", port=None, settings_path=Path("a.toml"))
        )


def test_subprocess_lifecycle_spawn_healthz_and_terminate(tmp_path: Path) -> None:
    """Spawn Studio child process, poll `/healthz`, and terminate cleanly."""
    rt_file = tmp_path / "runtime.json"
    child_env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pfetl_analytics_studio.cli",
            "--no-browser",
            "--auto-port",
            "--runtime-file",
            str(rt_file),
        ],
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 20.0
        health_data: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            meta = read_runtime_metadata(rt_file)
            if meta and meta.get("health_url"):
                try:
                    with urllib.request.urlopen(str(meta["health_url"]), timeout=1.0) as resp:
                        if resp.status == 200:
                            health_data = json.loads(resp.read().decode("utf-8"))
                            break
                except Exception:  # noqa: BLE001
                    pass
            if proc.poll() is not None:
                break
            time.sleep(0.25)
        if health_data is None:
            out_b, err_b = proc.communicate(timeout=3) if proc.poll() is not None else (b"", b"")
            pytest.fail(
                f"Studio child process did not become ready on /healthz "
                f"(rc={proc.poll()}, stdout={out_b.decode(errors='ignore')!r}, "
                f"stderr={err_b.decode(errors='ignore')!r})"
            )
        assert health_data["status"] == "ready"
        assert health_data["app"] == "pfetl-analytics-studio"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_export_chart_labels_thinned_and_formatted_across_pptx_and_pdf(tmp_path: Path) -> None:
    """PPTX native charts & PDF/PNG figures format percentages cleanly and thin dense labels."""
    ok, msg = check_static_export_capability()
    assert isinstance(ok, bool)
    assert msg

    dates = [f"2025-{m:02d}-28" for m in range(1, 13)]
    cov = [0.2167254 + i * 0.005 for i in range(12)]
    prob = [0.9514854 - i * 0.001 for i in range(12)]
    fig = {
        "data": [
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "FI Coverage (%)",
                "x": dates,
                "y": cov,
            },
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "MC Success Probability (%)",
                "x": dates,
                "y": prob,
            },
        ],
        "layout": {"yaxis": {"ticksuffix": "%"}},
    }

    prepared = _prepare_pdf_chart_figure(fig, "FIRE Coverage")
    assert prepared["data"][0]["text"][0] == "21.7%"
    # Intermediate dense labels are thinned out so they don't collide
    assert prepared["data"][0]["text"][1] == ""
    assert prepared["data"][0]["text"][-1] != ""

    # PPTX native chart formatting & thinning
    from datetime import UTC, datetime

    ctx = ReportContext(
        profile_id="test",
        pfetl_run_id="run-1",
        period=PeriodContext(
            start_date=None,
            end_date=None,
            label="Rolling 12M",
            preset=PeriodPreset.ROLLING_12M,
        ),
        studio_version=__version__,
        generated_at=datetime.now(UTC),
    )
    res = ReportResult(
        definition_id="fire_test",
        definition_name="FIRE Test",
        context=ctx,
        blocks=(
            RenderedTextBlock(block_type="title", block_id="t1", heading="FIRE Test"),
            RenderedProvenanceBlock(
                block_id="p1",
                profile_id="test",
                pfetl_run_id="run-1",
                period_label="Rolling 12M",
                studio_version=__version__,
                generated_at=datetime.now(UTC),
            ),
            RenderedChartBlock(
                block_id="c1",
                source_id="fire.traj",
                title="FIRE Trajectory",
                figure_dict=fig,
            ),
        ),
    )
    pptx_out = ReportExporter.export_pptx(res, tmp_path / "fire_chart.pptx")
    prs = Presentation(str(pptx_out.path))
    chart_shapes: list[Any] = [s for slide in prs.slides for s in slide.shapes if s.has_chart]
    assert chart_shapes
    plot: Any = chart_shapes[0].chart.plots[0]
    assert plot.data_labels.number_format == "0.0%"
    # Verify middle point data label is hidden while endpoints remain enabled
    pts: list[Any] = list(plot.series[0].points)
    assert pts[1].data_label.has_text_frame
    assert pts[1].data_label.text_frame.text == ""
