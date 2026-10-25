# Walk Reference (`scripts/walk.py`)

`scripts/walk.py` (`nuu walk`) drives an app's demo mode in a real browser with fixed data and fixed clocks. Each `@step(device)` presses one control, asserts one effect, writes one screenshot, and fails if a new console error appears.

A second mode (`--views`) saves the named view PNGs that `nuu board` embeds, so a board shows the real page rather than a mock. Run the walk during `nuu` audits and before a release, never in CI, and keep its output out of Git.

```python
from walk import Demo, Walker, capture_views, step


@step("desktop")
def no_console_errors_on_load(w: Walker, demo: Demo) -> None:
  w.goto(1500)
  assert w.errors == []


def capture_views(walker: Walker, demo: Demo) -> dict[str, bytes]:
  """The example views of the design boards, PNG bytes by shot name."""
  shots: dict[str, bytes] = {}
  walker.goto(1500)
  shots["dashboard"] = walker.page.screenshot()
  demo.sign_in(False)
  demo.wait_until_checked(False)
  walker.goto(2500)
  shots["signed-out"] = walker.page.screenshot()
  return shots
```

```text
nuu walk -s scripts/walk.py -o /tmp/walk
nuu walk -V -s scripts/walk.py -o docs/walk/views
```
