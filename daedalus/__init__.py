"""Local OpenAI-compatible proxy."""

import os

__all__ = ["__version__"]

VERSION_ENV = "DAEDALUS_VERSION"

# The image build sets it from the v* tag. A build from the source sets dev-COMMIT.
__version__ = os.environ.get(VERSION_ENV) or "dev"
