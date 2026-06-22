from channels.generic.websocket import AsyncJsonWebsocketConsumer

from . import state


class SimulationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        state.websocket_clients += 1
        await self.channel_layer.group_add(state.GROUP_NAME, self.channel_name)
        await self.accept()

        snapshot = state.engine.latest_snapshot()
        if snapshot:
            await self.send_json(snapshot)
        else:
            await self.send_json(state.status_payload())

    async def disconnect(self, close_code):
        state.websocket_clients = max(0, state.websocket_clients - 1)
        await self.channel_layer.group_discard(state.GROUP_NAME, self.channel_name)

    async def swmm_message(self, event):
        await self.send_json(event["payload"])
