from typing import Tuple
from pathlib import Path

import random

from taco.consensus.blockchain import Blockchain
from taco.consensus.constants import ConsensusConstants
from taco.full_node.block_store import BlockStore
from taco.full_node.coin_store import CoinStore
from taco.util.db_wrapper import DBWrapper2


async def create_ram_blockchain(consensus_constants: ConsensusConstants) -> Tuple[DBWrapper2, Blockchain]:
    uri = f"file:db_{random.randint(0, 99999999)}?mode=memory&cache=shared"
    db_wrapper = await DBWrapper2.create(database=uri, uri=True, reader_count=1)
    block_store = await BlockStore.create(db_wrapper)
    coin_store = await CoinStore.create(db_wrapper)
    blockchain = await Blockchain.create(coin_store, block_store, consensus_constants, Path("."), 2)
    return db_wrapper, blockchain
