# 3. Errors reroute to the next fallback

## Status

Accepted.

## Context

A provider can answer 429, a bad request, or any other error. A client that asked a pool
for an answer should not see that error while another model in the chain can still answer.

## Decision

On any upstream error, daedalus logs the error internally and reroutes the request to the
next fallback in the chain. The client sees an error only after the last fallback fails.

daedalus does not send a request that is too large for a model. When the input tokens are
more than the input limit of a model, it skips that model and goes to the next
fallback. A model without a known limit gets the request.

A stream can fail after the client received text. daedalus then sends the received text to
the next fallback as an assistant prefix, and streams only the continuation. The client
receives one stream with one status.

A partial tool call cannot continue. When a stream fails inside a tool call, daedalus sends
one error chunk and ends the stream.

`limits.wait` caps the gap between provider data at 60 seconds. A provider that sends data
is not waiting, so a stream that produces output sits outside the cap. `limits.request`
caps the whole request at 600 seconds. Keep-alive bytes are not data: after 60 seconds of
only keep-alive bytes, the next model starts.

## Consequences

- A client sees one answer or one error, never the failures in between.
- The internal log is the only record of the fallbacks it tried.
- A slow provider that keeps sending data can hold a request longer than 60 seconds.
- A slow model that sends only keep-alive bytes while it reads a long input loses its turn.
- A continued answer can change style or facts at the join, because two models wrote it.
- A stream that fails inside a tool call ends with an error chunk.
