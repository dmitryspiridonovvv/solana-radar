"""SQLite persistence for per-chat subscriptions."""

import json
import sqlite3

from .alerts import Subscription


class SubscriptionStore:
    def __init__(self, path: str = "radar.db"):
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS subscriptions (
                chat_id INTEGER PRIMARY KEY,
                feeds TEXT NOT NULL,
                whale_usd REAL NOT NULL,
                watched TEXT NOT NULL,
                paused INTEGER NOT NULL DEFAULT 0
            )"""
        )
        self.conn.commit()

    def get(self, chat_id: int) -> Subscription | None:
        row = self.conn.execute("SELECT * FROM subscriptions WHERE chat_id = ?", (chat_id,)).fetchone()
        return self._from_row(row) if row else None

    def get_or_create(self, chat_id: int) -> Subscription:
        sub = self.get(chat_id)
        if sub is None:
            sub = Subscription(chat_id=chat_id)
            self.save(sub)
        return sub

    def save(self, sub: Subscription) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO subscriptions (chat_id, feeds, whale_usd, watched, paused) VALUES (?, ?, ?, ?, ?)",
            (sub.chat_id, json.dumps(sorted(sub.feeds)), sub.whale_usd, json.dumps(sorted(sub.watched)), int(sub.paused)),
        )
        self.conn.commit()

    def delete(self, chat_id: int) -> None:
        self.conn.execute("DELETE FROM subscriptions WHERE chat_id = ?", (chat_id,))
        self.conn.commit()

    def all(self) -> list[Subscription]:
        return [self._from_row(row) for row in self.conn.execute("SELECT * FROM subscriptions")]

    @staticmethod
    def _from_row(row) -> Subscription:
        chat_id, feeds, whale_usd, watched, paused = row
        return Subscription(chat_id=chat_id, feeds=set(json.loads(feeds)), whale_usd=whale_usd, watched=set(json.loads(watched)), paused=bool(paused))
