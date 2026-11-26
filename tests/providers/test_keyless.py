from typing import ClassVar

from daedalus.providers import base


class Keyless(base.OpenAIProvider):
  """A keyless provider for the base tests."""

  keyless: ClassVar[bool] = True


def test_keyed_base_still_requires_a_key() -> None:
  try:
    base.OpenAIProvider("custom", {"api_base": "https://custom.test/v1"})
  except base.ProviderError as error:
    assert "Missing api_key for custom" in str(error), error
  else:
    raise AssertionError("a keyed provider must not construct without a key")


def test_keyless_provider_constructs_without_a_key() -> None:
  assert base.OpenAIProvider.keyless is False
  assert Keyless.keyless is True
  provider = Keyless("stub", {"api_base": "https://stub.test/v1"})
  assert provider.key == ""
  assert provider.base == "https://stub.test/v1"


def test_keyless_headers_send_no_authorization() -> None:
  provider = Keyless("stub", {"api_base": "https://stub.test/v1"})
  headers = provider.headers()
  assert headers == {"content-type": "application/json"}, headers
  assert not any(key.lower() == "authorization" for key in headers)
