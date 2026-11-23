"""All tools of the helpdesk world. Importing this package registers them in `REGISTRY`."""

from ..registry import REGISTRY
from . import crm, helpdesk, lookups  # noqa: F401  (imported for their @tool registrations)

__all__ = ["REGISTRY"]
