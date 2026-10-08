"""The GLM-4.x flash rows reason through `thinking` on/off only: the effort maps to it."""

from daedalus.providers.zai import ZAiProvider

PROVIDER = ZAiProvider("z-ai", {"api_base": "https://z.ai.test/v1", "api_key": "k"})


def sent(slug: str, effort: object) -> dict:
  """The body that goes to Z.ai for one client effort."""
  payload: dict = {"messages": [{"role": "user", "content": "hi"}]}
  if effort is not None:
    payload["reasoning_effort"] = effort
  return PROVIDER.request(slug, payload)[1]


def test_flash_rows_map_the_effort_to_thinking() -> None:
  """A flash slug sends thinking on or off, and never the effort parameter."""
  assert sent("glm-4.5-flash", "max")["thinking"] == {"type": "enabled"}
  assert sent("glm-4.6v-flash", "none")["thinking"] == {"type": "disabled"}
  assert sent("glm-4.7-flash", "max")["thinking"] == {"type": "enabled"}
  for slug in ("glm-4.5-flash", "glm-4.6v-flash", "glm-4.7-flash"):
    assert "reasoning_effort" not in sent(slug, "max"), slug


def test_non_flash_rows_pass_the_effort_through() -> None:
  """A slug outside the 4.x flash keeps the reasoning_effort pass-through."""
  body = sent("glm-5.2", "high")
  assert body["reasoning_effort"] == "high"
  assert "thinking" not in body


def test_flash_columns_name_the_thinking_ladder() -> None:
  """The discovery columns of a flash row claim reasoning with the on/off ladder."""
  for slug in ("glm-4.5-flash", "glm-4.6v-flash", "glm-4.7-flash"):
    assert ZAiProvider.columns({"id": slug}) == {
      "supports_reasoning": True,
      "supported_efforts": ["none", "max"],
    }, slug
  assert ZAiProvider.columns({"id": "glm-4.7"}) == {}
  assert ZAiProvider.columns({"id": "glm-5.2"}) == {}
