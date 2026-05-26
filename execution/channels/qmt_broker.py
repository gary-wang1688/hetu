from core.interfaces import Broker
from core.types import ChannelResult, ComponentHealth, HealthStatus
class QMTBroker(Broker):
    name='qmt_broker';monitor_type='push'
    def __init__(self):pass
    async def submit_order(self,o):return ChannelResult(success=False,error='QMT未连接')
    async def cancel_order(self,id):return ChannelResult(success=False)
    async def get_balance(self):return{}
    async def get_positions(self):return[]
    async def get_trades(self):return[]
    async def query_order(self,id):return None
    async def health_check(self):return ComponentHealth(name=self.name,status=HealthStatus.UNHEALTHY,message='脚手架')
