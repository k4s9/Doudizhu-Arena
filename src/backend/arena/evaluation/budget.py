"""Shared, persistent, conservative reservation ledger for every provider call."""
from decimal import Decimal
import time
import uuid


class BudgetExceeded(RuntimeError):
    stop_run = True


class EvidenceError(RuntimeError):
    stop_run = True


class BudgetLedger:
    def __init__(self, repo, run_id, limit, max_calls, input_limit, output_limit, pricing):
        self.repo, self.run_id = repo, run_id
        self.limit = Decimal(str(limit))
        self.max_calls, self.input_limit, self.output_limit = max_calls, input_limit, output_limit
        self.pricing = pricing
        self.stopped = False

    def cost(self, input_tokens, output_tokens):
        return (Decimal(input_tokens)*Decimal(str(self.pricing["input_per_million"])) +
                Decimal(output_tokens)*Decimal(str(self.pricing["output_per_million"]))) / 1_000_000

    def reserve(self, system, user):
        if self.stopped:
            raise BudgetExceeded("budget stopped")
        # Conservative UTF-8 byte bound for text tokenizers, plus message framing.
        # Full configured input allowance is reserved; no optimistic token estimate.
        if len((system+user).encode()) + 1024 > self.input_limit:
            raise BudgetExceeded("input allowance exceeded")
        with self.repo.atomic():
            rows = self.repo.conn.execute("SELECT reserved_usd,actual_usd FROM budget_ledger WHERE run_id=?", (self.run_id,)).fetchall()
            used = sum((Decimal(str(r[1] if r[1] is not None else r[0])) for r in rows), Decimal(0))
            amount = self.cost(self.input_limit, self.output_limit)
            if len(rows) >= self.max_calls or used + amount > self.limit:
                self.stopped = True
                raise BudgetExceeded("call count or reserved cost limit reached")
            reservation = uuid.uuid4().hex
            self.repo.conn.execute("INSERT INTO budget_ledger VALUES(?,?,NULL,?,NULL,'reserved',?)", (reservation, self.run_id, float(amount), time.time()))
        return reservation

    def settle(self, reservation, call_id, usage):
        cost = None if usage is None or usage.prompt_tokens is None or usage.completion_tokens is None else self.cost(usage.prompt_tokens, usage.completion_tokens)
        reserved = self.repo.conn.execute("SELECT reserved_usd FROM budget_ledger WHERE reservation_id=?", (reservation,)).fetchone()[0]
        self.repo.conn.execute("UPDATE budget_ledger SET call_id=?,actual_usd=?,status=? WHERE reservation_id=?", (call_id, float(cost) if cost is not None else None, "settled" if cost is not None else "unknown", reservation))
        self.repo.conn.commit()
        if cost is not None and cost > Decimal(str(reserved)):
            self.stopped = True
            raise BudgetExceeded("provider usage exceeded reserved allowance")
