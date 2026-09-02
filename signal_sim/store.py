"""SQLite storage for canonical events."""

from __future__ import annotations

import json
import sqlite3
from os import PathLike

from .events import Event


class EventStore:
    def __init__(self, database: sqlite3.Connection | str | PathLike[str] = ":memory:") -> None:
        self._owns_connection = not isinstance(database, sqlite3.Connection)
        self.connection = database if isinstance(database, sqlite3.Connection) else sqlite3.connect(database)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                kind TEXT NOT NULL,
                ticker TEXT NOT NULL,
                entities TEXT NOT NULL,
                headline TEXT NOT NULL,
                url TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                filed_at TEXT,
                observed_at TEXT NOT NULL,
                confidence REAL NOT NULL,
                raw_ref TEXT NOT NULL
            )
            """
        )

    def add(self, event: Event) -> None:
        values = event.to_dict()
        values["entities"] = json.dumps(values["entities"], separators=(",", ":"))
        columns = tuple(values)
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(f"{column}=excluded.{column}" for column in columns if column != "id")
        self.connection.execute(
            f"INSERT INTO events ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            tuple(values[column] for column in columns),
        )
        self.connection.commit()

    def add_many(self, events: list[Event]) -> None:
        for event in events:
            self.add(event)

    def all(self) -> list[Event]:
        cursor = self.connection.execute(
            """
            SELECT id, source, kind, ticker, entities, headline, url,
                   occurred_at, filed_at, observed_at, confidence, raw_ref
            FROM events
            ORDER BY observed_at, id
            """
        )
        columns = [description[0] for description in cursor.description]
        events = []
        for row in cursor:
            values = dict(zip(columns, row))
            values["entities"] = json.loads(values["entities"])
            events.append(Event.from_dict(values))
        return events

    def close(self) -> None:
        if self._owns_connection:
            self.connection.close()

    def __enter__(self) -> "EventStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
