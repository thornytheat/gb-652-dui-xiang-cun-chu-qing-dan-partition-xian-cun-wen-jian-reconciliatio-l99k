from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

CSV = (
    "key,size,etag\n"
    "dt=2026-11-15/hour=00/a%20b.parquet,120,aaa\n"
    "dt=2026-11-15\\hour=01\\c.parquet,80,ccc\n"
    "dt=2026-11-15//hour=02//d.parquet,70,ddd\n"
    "dt=2026-11-15/hour=00/a b.parquet,120,aaa\n"
    "dt=2026-11-15/hour=03/_EMPTY,,\n"
)

OBJECTS = (
    "dt=2026-11-15/hour=00/a b.parquet\n"
    "dt=2026-11-15/hour=01/c.parquet\n"
)


@pytest.fixture
def store_client(tmp_path):
    import os
    os.environ["DB_PATH"] = str(tmp_path / "partitions.db")
    with TestClient(create_app()) as client:
        yield client


def _import(client, **overrides):
    payload = {
        "text": CSV,
        "source": "supplier",
        "objects": OBJECTS,
        "expected_hours": ["00", "01", "02", "03"],
    }
    payload.update(overrides)
    response = client.post("/api/partitions/import", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_supplier_source_unifies_escaped_keys(store_client):
    data = _import(store_client)
    passed = [row for row in data["rows"] if row["status"] == "passed"]
    targets = {row["target"] for row in passed}
    assert "dt=2026-11-15/hour=01/c.parquet" in targets
    assert "dt=2026-11-15/hour=02/d.parquet" in targets
    # 原键逐字保留
    raw = {row["raw_key"] for row in data["rows"]}
    assert "dt=2026-11-15\\hour=01\\c.parquet" in raw


def test_conflicting_mapping_quarantines_whole_group(store_client):
    data = _import(store_client)
    conflicted = [row for row in data["rows"] if row["reason"] == "CONFLICTING_MAPPING"]
    assert len(conflicted) == 2
    assert {row["raw_key"] for row in conflicted} == {
        "dt=2026-11-15/hour=00/a%20b.parquet",
        "dt=2026-11-15/hour=00/a b.parquet",
    }
    # 不挑选一条覆盖
    assert not any(
        row["target"] == "dt=2026-11-15/hour=00/a b.parquet" and row["status"] == "passed"
        for row in data["rows"]
    )


def test_native_source_keeps_literal_names(store_client):
    payload = {
        "source": "native",
        "manifest": [
            "dt=2026-11-15/hour=00/a%20b.parquet",
            "dt=2026-11-15/hour=00/a b.parquet",
            "dt=2026-11-15\\hour=01\\c.parquet",
            "dt=2026-11-15//hour=02//d.parquet",
        ],
        "expected_hours": ["00", "01", "02"],
        "objects": [
            "dt=2026-11-15/hour=00/a%20b.parquet",
            "dt=2026-11-15/hour=00/a b.parquet",
            "dt=2026-11-15\\hour=01\\c.parquet",
            "dt=2026-11-15//hour=02//d.parquet",
        ],
    }
    data = store_client.post("/api/partitions/import", json=payload).json()
    assert data["valid_count"] == 4
    assert data["quarantine_count"] == 0
    assert data["missing_objects"] == []


def test_source_mismatch_does_not_merge_objects(store_client):
    # 供货方清单解释出 a b，但现存对象只有转义名，两者不串
    payload = {
        "source": "supplier",
        "manifest": ["dt=2026-11-15/hour=00/a%20b.parquet"],
        "objects": ["dt=2026-11-15/hour=00/a%20b.parquet"],
        "expected_hours": ["00"],
    }
    data = store_client.post("/api/partitions/import", json=payload).json()
    assert data["missing_objects"] == ["dt=2026-11-15/hour=00/a b.parquet"]
    assert data["orphan_objects"] == ["dt=2026-11-15/hour=00/a%20b.parquet"]


def test_partition_counts_are_exclusive(store_client):
    data = _import(store_client)
    assert data["input_count"] == len(data["rows"])
    assert data["input_count"] == (
        data["valid_count"] + data["quarantine_count"] + data["empty_count"]
    )
    assert data["balanced"] is True
    labels = {part["partition"] for part in data["partitions"]}
    assert "dt=2026-11-15/hour=01" in labels
    assert "dt=2026-11-15/hour=02" in labels


def test_empty_marker_is_own_partition(store_client):
    data = _import(store_client)
    empty_parts = [part for part in data["partitions"] if part["empty"]]
    assert [part["partition"] for part in empty_parts] == ["dt=2026-11-15/hour=03"]


def test_idempotent_import_reuses_batch(store_client):
    first = _import(store_client)
    second = _import(store_client)
    assert first["batch_id"] == second["batch_id"]
    assert first["valid_count"] == second["valid_count"]


def test_replay_only_quarantined_rows(store_client):
    data = _import(store_client)
    passed_before = {row["row_id"]: row["target"] for row in data["rows"] if row["status"] == "passed"}
    conflicted = [row for row in data["quarantine"] if row["reason"] == "CONFLICTING_MAPPING"]
    fix_row = conflicted[0]

    response = store_client.post(
        f"/api/partitions/{data['batch_id']}/replay",
        json={"row_id": fix_row["row_id"], "target": "dt=2026-11-15/hour=04/fixed.parquet"},
    )
    assert response.status_code == 200
    replayed = response.json()

    for row_id, target in passed_before.items():
        row = next(item for item in replayed["rows"] if item["row_id"] == row_id)
        assert row["status"] == "passed" and row["target"] == target
        assert row["attempts"] == 0

    fixed = next(item for item in replayed["rows"] if item["row_id"] == fix_row["row_id"])
    assert fixed["status"] == "passed"
    assert fixed["attempts"] == 1
    assert fixed["raw_key"] == fix_row["raw_key"]

    other = next(item for item in replayed["rows"] if item["raw_key"] != fix_row["raw_key"]
                 and item["reason"] == "CONFLICTING_MAPPING")
    assert other["status"] == "quarantined"
    assert other["attempts"] == 0


def test_replay_failure_does_not_duplicate_count(store_client):
    data = _import(store_client)
    batch_id = data["batch_id"]
    first = store_client.post(
        f"/api/partitions/{batch_id}/replay",
        json={"overrides": {"*": {"target": "dt=2026-11-15/hour=03/_EMPTY"}}},
    ).json()
    second = store_client.post(
        f"/api/partitions/{batch_id}/replay", json={}
    ).json()
    # 无可重放行时计数不变
    assert second["valid_count"] == first["valid_count"]
    assert second["quarantine_count"] == first["quarantine_count"]
    assert second["input_count"] == first["input_count"]
    assert second["replays"] == first["replays"]


def test_replay_failure_keeps_original_evidence(store_client):
    response = store_client.post("/api/partitions/import", json={
        "source": "supplier",
        "text": (
            "key,size,etag\n"
            "dt=2026-11-15/hour=00/a%zz,1,x\n"
            "dt=2026-11-15/hour=00/b,1,y\n"
        ),
    })
    data = response.json()
    batch_id = data["batch_id"]
    bad = next(row for row in data["rows"] if row["reason"] == "INVALID_ESCAPE")
    result = store_client.post(
        f"/api/partitions/{batch_id}/replay",
        json={"row_id": bad["row_id"], "source": "mystery"},
    ).json()
    row = next(item for item in result["rows"] if item["row_id"] == bad["row_id"])
    assert row["reason"] == "INVALID_ESCAPE"
    assert row["last_error"] == "UNKNOWN_SOURCE"
    assert row["attempts"] == 1
    assert result["replay_notice"]["failed"] == 1

    retry = store_client.post(
        f"/api/partitions/{batch_id}/replay",
        json={"row_id": bad["row_id"], "source": "native"},
    ).json()
    row = next(item for item in retry["rows"] if item["row_id"] == bad["row_id"])
    assert row["status"] == "passed"
    assert row["last_error"] is None
    assert row["attempts"] == 2


def test_targeted_replay_leaves_peer_untouched(store_client):
    data = _import(store_client)
    batch_id = data["batch_id"]
    conflicted = [row for row in data["quarantine"] if row["reason"] == "CONFLICTING_MAPPING"]
    chosen, peer = conflicted
    result = store_client.post(
        f"/api/partitions/{batch_id}/replay",
        json={"row_id": chosen["row_id"], "target": "dt=2026-11-15/hour=07/z.parquet"},
    ).json()
    fixed = next(item for item in result["rows"] if item["row_id"] == chosen["row_id"])
    untouched = next(item for item in result["rows"] if item["row_id"] == peer["row_id"])
    assert fixed["status"] == "passed" and fixed["attempts"] == 1
    assert untouched["status"] == "quarantined"
    assert untouched["attempts"] == 0
    assert untouched["reason"] == "CONFLICTING_MAPPING"
    assert result["valid_count"] == data["valid_count"] + 1
    assert result["quarantine_count"] == data["quarantine_count"] - 1


def test_replay_does_not_duplicate_partition(store_client):
    data = _import(store_client)
    batch_id = data["batch_id"]
    before = {part["partition"]: part["count"] for part in data["partitions"]}
    conflicted = [row for row in data["quarantine"] if row["reason"] == "CONFLICTING_MAPPING"]
    target = "dt=2026-11-15/hour=01/extra.parquet"
    first = store_client.post(
        f"/api/partitions/{batch_id}/replay",
        json={"row_id": conflicted[0]["row_id"], "target": target},
    ).json()
    second = store_client.post(
        f"/api/partitions/{batch_id}/replay",
        json={"row_id": conflicted[0]["row_id"], "target": target},
    ).json()
    part = next(p for p in second["partitions"] if p["partition"] == "dt=2026-11-15/hour=01")
    assert part["count"] == before["dt=2026-11-15/hour=01"] + 1
    assert second["valid_count"] == first["valid_count"]
    assert second["quarantine_count"] == first["quarantine_count"]
    assert second["replay_notice"]["noop"] is True


def test_replay_conflict_stays_quarantined(store_client):
    data = _import(store_client)
    batch_id = data["batch_id"]
    conflicted = [row for row in data["quarantine"] if row["reason"] == "CONFLICTING_MAPPING"]
    assert len(conflicted) == 2
    # 第一条修正后通过；第二条再修正到同一目标，与已通过行冲突，仍隔离
    for row in conflicted:
        store_client.post(
            f"/api/partitions/{batch_id}/replay",
            json={"row_id": row["row_id"], "target": "dt=2026-11-15/hour=09/x.parquet"},
        )
    final = store_client.get(f"/api/partitions/{batch_id}").json()
    rows = [row for row in final["rows"] if row["target"] == "dt=2026-11-15/hour=09/x.parquet"]
    assert len(rows) == 2
    passed = [row for row in rows if row["status"] == "passed"]
    quarantined = [row for row in rows if row["status"] == "quarantined"]
    assert len(passed) == 1 and len(quarantined) == 1
    assert quarantined[0]["reason"] == "CONFLICTING_MAPPING"


def test_unknown_source_rejected(store_client):
    response = store_client.post(
        "/api/partitions/import", json={"manifest": ["a"], "source": "mystery"}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "UNKNOWN_SOURCE"


def test_ui_import_and_replay(store_client):
    page = store_client.get("/partitions")
    assert page.status_code == 200
    assert "导入清单" in page.text

    response = store_client.post(
        "/partitions/import",
        data={"text": CSV, "source": "supplier", "objects": OBJECTS,
              "expected_hours": "00 01 02 03"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    page = store_client.get(location)
    assert page.status_code == 200
    assert "分区归属" in page.text
    assert "CONFLICTING_MAPPING" in page.text


def test_ui_file_upload_csv(store_client):
    response = store_client.post(
        "/partitions/import",
        files={"manifest_file": ("manifest.csv", CSV.encode("utf-8"), "text/csv")},
        data={"source": "supplier", "objects": OBJECTS,
              "expected_hours": "00 01 02 03"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    data = store_client.get(response.headers["location"].replace(
        "/partitions?batch=", "/api/partitions/")).json()
    assert data["input_count"] == 5
    assert data["balanced"] is True


def test_ui_file_upload_jsonl(store_client):
    body = (
        '{"key":"dt=2026-11-15/hour=00/a%20b.parquet","size":1,"etag":"x"}\n'
        '{"key":"dt=2026-11-15/hour=01/b.parquet","size":1,"etag":"y"}\n'
    )
    response = store_client.post(
        "/partitions/import",
        files={"manifest_file": ("manifest.jsonl", body.encode("utf-8"),
                                 "application/jsonl")},
        data={"source": "supplier"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    data = store_client.get("/api/partitions").json()
    assert data["valid_count"] == 2
    assert data["manifest_state"] == "data"


def test_ui_import_rejects_unknown_source(store_client):
    response = store_client.post(
        "/partitions/import",
        data={"text": "key,size,etag\n", "source": "mystery"},
        follow_redirects=False,
    )
    assert response.status_code == 422
    assert "未登记" in response.text


def test_batch_survives_new_client(tmp_path):
    import os
    os.environ["DB_PATH"] = str(tmp_path / "persist.db")
    with TestClient(create_app()) as client:
        data = _import(client)
        batch_id = data["batch_id"]
    with TestClient(create_app()) as client:
        latest = client.get("/api/partitions").json()
        assert latest["batch_id"] == batch_id
        assert latest["valid_count"] == data["valid_count"]
