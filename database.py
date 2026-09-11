"""
Database-verbinding — legacy forwarder.
Gebruik bij voorkeur `from database import get_session, Base`.
"""

from database.connection import AsyncSessionLocal, Base, engine, get_session

__all__ = ["AsyncSessionLocal", "Base", "engine", "get_session"]

