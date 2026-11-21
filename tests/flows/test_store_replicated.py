"""on the HA servers the flows tables are created on both replicas with replicated engines."""

from flows.store import SCHEMA, replicated


def test_every_table_and_view_is_created_on_the_cluster_with_a_replicated_engine():
    out = [replicated(s, "fabric") for s in SCHEMA]
    assert all(" ON CLUSTER fabric" in s.splitlines()[0] for s in out)
    assert "ENGINE = ReplicatedMergeTree PARTITION BY toDate(time)" in out[0]
    assert out[1].count("ENGINE = ReplicatedSummingMergeTree((bytes, packets, flows))") == 1
    assert out[2].startswith("CREATE MATERIALIZED VIEW IF NOT EXISTS flows_minute_mv ON CLUSTER fabric TO flows_minute")
    assert not any("ENGINE = MergeTree" in s or "ENGINE = SummingMergeTree" in s for s in out)
