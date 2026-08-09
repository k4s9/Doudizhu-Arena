import asyncio

from arena.api.event_bus import MatchEventBus


def test_play_turn_event_contains_server_deadline():
    async def run():
        bus = MatchEventBus('match-1')
        await bus.emit_play_turn_started(
            table='A',
            hand_num=3,
            seat='N',
            timeout_ms=60_000,
            deadline_ms=1_720_000_060_000,
        )
        return await bus.get()

    event = asyncio.run(run())

    assert event == {
        'type': 'play_turn_started',
        'payload': {
            'table': 'A',
            'hand_num': 3,
            'seat': 'N',
            'timeout_ms': 60_000,
            'deadline_ms': 1_720_000_060_000,
        },
    }
