import subprocess
import sys

COMMANDS = {
  "daedalus": [sys.executable, "-c", "from daedalus.api import run; run()"],
  "catalog": [sys.executable, "-m", "daedalus.catalog"],
}


def main() -> None:
  for name, command in COMMANDS.items():
    shown = subprocess.run(
      [*command, "--help"], capture_output=True, text=True, timeout=10, check=False
    )
    assert shown.returncode == 0, (name, shown.stderr)
    assert shown.stdout.startswith("usage:"), (name, shown.stdout)
    wrong = subprocess.run(
      [*command, "--wrong"], capture_output=True, text=True, timeout=10, check=False
    )
    assert wrong.returncode == 2, (name, wrong.stderr)
  print("ok: cli help")


if __name__ == "__main__":
  main()
