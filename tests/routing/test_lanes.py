"""A client with its own provider key gets its own cooldowns and pacing counts."""

from daedalus import providers, store
from daedalus.routing import cooldowns, lanes, pacing, router

CONFIG = {
  "gemini": {"api_key": "main", "client_keys": {"kilo": "kilo-key", "empty": ""}},
  "cloudflare": {"api_key": "cf"},
}


def test_lane() -> None:
  """Only a client with a non-empty key under client_keys gets its own lane."""
  assert router.lane(CONFIG, "gemini/flash", "kilo") == "gemini/flash#kilo"
  assert router.lane(CONFIG, "gemini/flash", "owui") == "gemini/flash"
  assert router.lane(CONFIG, "gemini/flash", "empty") == "gemini/flash"
  assert router.lane(CONFIG, "gemini/flash", None) == "gemini/flash"
  assert router.lane(CONFIG, "cloudflare/@cf/meta/x", "kilo") == "cloudflare/@cf/meta/x"
  assert lanes.split("cloudflare/@cf/meta/x#kilo") == ("cloudflare/@cf/meta/x", "kilo")
  assert lanes.split("gemini/flash") == ("gemini/flash", "")


def test_provider_key() -> None:
  """The provider request uses the key of the client, else the api_key."""
  for client, key in (("kilo", "kilo-key"), ("owui", "main"), (None, "main")):
    provider, _ = providers.provider_for("gemini/flash", CONFIG, client)
    assert provider.key == key, (client, provider.key)


def test_cooldown_lanes() -> None:
  """A cooldown of 1 lane does not stop the other lanes of the model."""
  clock = {"now": 1000.0}
  cool = cooldowns.Cooldowns(lambda: store.MODELS_DB, lambda: clock["now"])
  cool.clear()
  cool.start("gemini/flash#kilo", {"retry-after": "30"}, b"")
  ends = cool.ends()
  assert cool.until("gemini/flash#kilo", ends) == 1030.0, ends
  assert cool.until("gemini/flash", ends) is None, ends
  assert cool.until("gemini/flash#owui", ends) is None, ends
  assert cooldowns.Cooldowns.clients("gemini/flash", ends) == {"kilo": 1030.0}, ends
  assert cooldowns.Cooldowns.clients("gemini/pro", ends) == {}, ends
  daily = b'{"errors": [{"code": 4006}]}'
  assert cooldowns.daily_end("cloudflare/@cf/x#kilo", cooldowns.parsed(daily), 0.0)[
    0
  ] == ("cloudflare/*#kilo")
  assert cooldowns.key_of("cloudflare/@cf/x#kilo") == [
    "cloudflare/@cf/x#kilo",
    "cloudflare/*#kilo",
  ]


def test_pacing_lanes() -> None:
  """Each lane counts its own requests against the rpm of the model."""
  paced = pacing.Pacing(lambda: 0.0)
  limits = {"gemini/flash": (1.0, None)}
  paced.record("gemini/flash#kilo")
  assert paced.full("gemini/flash#kilo", limits)
  assert not paced.full("gemini/flash", limits)
