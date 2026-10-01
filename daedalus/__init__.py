"""Local OpenAI-compatible proxy."""

import os

__all__ = ["__version__"]

DAEDALUS_VERSION = "DAEDALUS_VERSION"

# The image build sets it from the v* tag. A build from the source sets dev-COMMIT.
__version__ = os.environ.get(DAEDALUS_VERSION) or "dev"
