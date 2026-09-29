"""Lanes: the cooldown and pacing key of a model for 1 client with its own provider key."""

LANE = "#"


def join(model: str, client: str) -> str:
  """The lane of a client on a model."""
  return f"{model}{LANE}{client}"


def split(lane: str) -> tuple[str, str]:
  """The model and the client of a lane. A model alone has an empty client."""
  model, _, client = lane.partition(LANE)
  return model, client
