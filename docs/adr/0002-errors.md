# 2. Errors reroute to the next fallback

## Status

Accepted.

## Context

A provider can answer 429, a bad request, or any other error. A client that asked a pool
for an answer should not see that error while another model in the chain can still answer.

## Decision

On any upstream error, the proxy logs the error internally and reroutes the request to the
next fallback in the chain. The client sees an error only after the last fallback fails.

The proxy caps the wait for an answer at 60 seconds. A provider that is sending bytes is not
waiting, so a stream that is producing output sits outside the cap. It may be preparing an
answer.

## Consequences

- A client sees one answer or one error, never the failures in between.
- The internal log is the only record of the fallbacks it tried.
- A slow provider that keeps sending bytes can hold a request longer than 60 seconds.
