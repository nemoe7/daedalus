"""The catalog_rebuilds table: one event per rebuild, with the diff it made."""

from daedalus import store


def test_rows_by_id_carries_the_values() -> None:
  store.migrate()
  store.write_store([{"id": "p/one", "mode": "chat", "tier": "TIER-A", "rpm": 10}])
  first = store.rows_by_id()
  assert set(first) == {"p/one"}
  assert first["p/one"]["tier"] == "TIER-A" and first["p/one"]["rpm"] == 10
  # The same row keeps its values.
  store.write_store([{"id": "p/one", "mode": "chat", "tier": "TIER-A", "rpm": 10}])
  assert store.rows_by_id() == first
  # A moved tier shows in the values.
  store.write_store([{"id": "p/one", "mode": "chat", "tier": "TIER-B", "rpm": 10}])
  assert store.rows_by_id()["p/one"]["tier"] == "TIER-B"


def test_record_and_read() -> None:
  store.migrate()
  store.record_rebuild(
    reason="scheduled",
    models=3,
    added=["p/a", "p/b"],
    removed=["p/c"],
    changed=["p/d"],
    failed=["kilo"],
  )
  events = store.recent_rebuilds()
  assert len(events) == 1
  event = events[0]
  assert event["reason"] == "scheduled"
  assert event["models"] == 3
  assert event["added"] == ["p/a", "p/b"]
  assert event["removed"] == ["p/c"]
  assert event["changed"] == ["p/d"]
  assert event["failed"] == ["kilo"]
  assert event["at"] > 0


def test_newest_first_and_pruned() -> None:
  store.migrate()
  for index in range(205):
    store.record_rebuild(
      reason=f"r{index}", models=index, added=[], removed=[], changed=[], failed=[]
    )
  events = store.recent_rebuilds(limit=1000)
  assert len(events) == 200, "the events older than the newest 200 go"
  assert [event["reason"] for event in events] == [
    f"r{index}" for index in range(204, 4, -1)
  ]
  assert len(store.recent_rebuilds(limit=3)) == 3
