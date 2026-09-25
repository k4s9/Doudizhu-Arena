"""Revoke a task's ability to persist work after its cancellation deadline."""
import asyncio


class ExecutionRevoked(asyncio.CancelledError):
    pass


class ExecutionScope:
    def __init__(self):
        self.active = True
        self.cancel_requested = False
        self.pending = set()
        self.match_runner = None

    def check(self):
        if not self.active:
            raise ExecutionRevoked('task execution has been revoked')

    def revoke(self):
        if not self.active:
            return
        try:
            # Persist unknown usage while the scope still has write access.
            for finalize in list(self.pending):
                finalize()
        finally:
            self.active = False
            if self.match_runner and self.match_runner.event_bus:
                self.match_runner.event_bus.close()

    def check_result(self):
        self.check()
        if self.cancel_requested:
            raise ExecutionRevoked('result arrived after cancellation')

    def guard(self, target):
        return GuardedAccess(self, target)


class GuardedAccess:
    def __init__(self, scope, target):
        self._scope = scope
        self._target = target

    def __getattr__(self, name):
        self._scope.check()
        value = getattr(self._target, name)
        if name == 'conn':
            return GuardedAccess(self._scope, value)
        if callable(value):
            def guarded(*args, **kwargs):
                self._scope.check()
                return value(*args, **kwargs)
            return guarded
        return value
