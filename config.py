"""
Applicatieconfiguratie — legacy forwarder.
Gebruik bij voorkeur `from config import settings`.
"""

from config.settings import Settings, get_settings, settings

__all__ = ["Settings", "get_settings", "settings"]

