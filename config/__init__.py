"""
config package — re-exporteer de settings-instantie zodat
`from config import settings` overal werkt zonder padverwarring.
"""

from config.settings import settings, get_settings, Settings

__all__ = ["settings", "get_settings", "Settings"]
