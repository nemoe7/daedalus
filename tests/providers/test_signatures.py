"""Gemini thought signatures: save, find, replace and expire."""

import time

from daedalus.providers import signatures

CALL = "call_1"
MODEL = "gemini-3-pro"
OTHER = "gemini-3-flash"


def backdate(used: float) -> None:
  database = signatures.connect()
  with database:
    database.execute(f"UPDATE {signatures.TABLE} SET used = ?", (used,))
  database.close()


def used() -> float:
  database = signatures.connect()
  row = database.execute(f"SELECT used FROM {signatures.TABLE}").fetchone()
  database.close()
  return row[0]


def test_save_then_find_gives_the_signature_back() -> None:
  """A saved signature comes back for its own call and model."""
  signatures.save(CALL, MODEL, "sig-a")
  assert signatures.find(CALL, MODEL) == "sig-a"


def test_find_gives_none_for_a_call_it_does_not_know() -> None:
  """An unknown call gives None, not an error."""
  signatures.save(CALL, MODEL, "sig-a")
  assert signatures.find("call_2", MODEL) is None


def test_find_gives_none_for_another_model() -> None:
  """A signature belongs to one model of one call."""
  signatures.save(CALL, MODEL, "sig-a")
  assert signatures.find(CALL, OTHER) is None


def test_save_replaces_the_signature_of_the_same_call() -> None:
  """A second save for the same call and model replaces the first one."""
  signatures.save(CALL, MODEL, "sig-a")
  signatures.save(CALL, MODEL, "sig-b")
  assert signatures.find(CALL, MODEL) == "sig-b"


def test_a_signature_that_nobody_used_for_an_hour_expires() -> None:
  """A signature older than the idle time is gone."""
  signatures.save(CALL, MODEL, "sig-a")
  backdate(time.time() - signatures.IDLE_SECONDS - 1)
  assert signatures.find(CALL, MODEL) is None


def test_find_moves_the_used_time_forward() -> None:
  """A hit keeps the signature alive for another idle span."""
  signatures.save(CALL, MODEL, "sig-a")
  backdate(time.time() - signatures.IDLE_SECONDS + 5)
  assert signatures.find(CALL, MODEL) == "sig-a"
  assert used() > time.time() - signatures.IDLE_SECONDS + 5


def test_save_drops_the_expired_signatures() -> None:
  """A save cleans up the signatures that expired."""
  signatures.save("call-old", MODEL, "sig-old")
  backdate(time.time() - signatures.IDLE_SECONDS - 1)
  signatures.save(CALL, MODEL, "sig-a")
  assert signatures.find("call-old", MODEL) is None
  assert signatures.find(CALL, MODEL) == "sig-a"
