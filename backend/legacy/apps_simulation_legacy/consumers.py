from channels.generic.websocket import AsyncJsonWebsocketConsumer


class SimulationConsumer(AsyncJsonWebsocketConsumer):
    group_name = "simulation"

    async def connect(self):
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_json(
            {
                "code": 200,
                "message": "Simulation socket connected.",
                "status": "OK",
                "data": None,
            }
        )

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def simulation_result(self, event):
        await self.send_json(event["payload"])
