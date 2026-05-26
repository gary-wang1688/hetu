from core.interfaces import Broker
from core.types import ChannelResult
class PaperBroker(Broker):
    name='paper_broker'
    def __init__(self):self._orders={};self._pos=[]
    async def submit_order(self,o):return ChannelResult(success=True)
    async def cancel_order(self,id):return ChannelResult(success=True)
    async def get_balance(self):return{'total_assets':100000}
    async def get_positions(self):return self._pos
    async def get_trades(self):return[]
    async def query_order(self,id):return None
    @classmethod
    def from_config(cls,cfg):return cls()
