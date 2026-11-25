from types import SimpleNamespace

from scripts import baseline


def test_disk_usage_snapshot_reports_available_volumes(monkeypatch):
    monkeypatch.setattr(
        baseline.psutil, "disk_partitions",
        lambda all=False: [SimpleNamespace(device="C:", mountpoint="C:\\")],
    )
    monkeypatch.setattr(
        baseline.psutil, "disk_usage",
        lambda _: SimpleNamespace(total=10 * 2**30, used=7 * 2**30, free=3 * 2**30, percent=70),
    )

    assert baseline.disk_usage_snapshot() == [{
        "device": "C:", "mountpoint": "C:\\", "total_gb": 10.0,
        "used_gb": 7.0, "free_gb": 3.0, "percent": 70,
    }]


def test_disk_io_snapshot_handles_unavailable_counters(monkeypatch):
    monkeypatch.setattr(baseline.psutil, "disk_io_counters", lambda: None)
    assert baseline.disk_io_snapshot() is None

    monkeypatch.setattr(
        baseline.psutil, "disk_io_counters",
        lambda: SimpleNamespace(read_bytes=1024, write_bytes=2048),
    )
    assert baseline.disk_io_snapshot() == (1024, 2048)
