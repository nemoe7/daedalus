from daedalus import providers

CLOUDFLARE = providers.CloudflareProvider(
  "cloudflare", {"api_base": "https://cloudflare.test", "api_key": "k"}
)


def test_cloudflare() -> None:
  image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}}
  messages = [
    {"role": "system", "content": [{"type": "text", "text": "Be terse."}]},
    {"role": "user", "content": "hi"},
    {
      "role": "assistant",
      "content": None,
      "tool_calls": [
        {"id": "c", "type": "function", "function": {"name": "f", "arguments": "{}"}}
      ],
    },
    {"role": "tool", "tool_call_id": "c", "content": [{"type": "text", "text": "a"}]},
    {"role": "user", "content": [{"type": "text", "text": "x"}, image]},
  ]
  payload = {"model": "m", "messages": messages}
  sent = CLOUDFLARE.body("@cf/m", payload)["messages"]
  assert sent[0]["content"] == "Be terse.", "a text-part list becomes a string"
  assert sent[1]["content"] == "hi"
  assert sent[2]["content"] == "" and sent[2]["tool_calls"], (
    "null content becomes empty"
  )
  assert sent[3]["content"] == "a"
  assert sent[4]["content"][1] == image, "a list with an image stays a list"
  assert payload["messages"][0]["content"][0]["text"] == "Be terse.", (
    "no change in place"
  )
  text = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
  joined = CLOUDFLARE.body("@cf/m", {"messages": [{"role": "user", "content": text}]})
  assert joined["messages"][0]["content"] == "a\nb", "text parts join with a new line"
