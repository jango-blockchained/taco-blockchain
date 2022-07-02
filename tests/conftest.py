# flake8: noqa E402 # See imports after multiprocessing.set_start_method
import multiprocessing
import os
from secrets import token_bytes

import pytest
import pytest_asyncio
import tempfile

from tests.setup_nodes import setup_node_and_wallet, setup_n_nodes, setup_two_nodes
from pathlib import Path
from typing import AsyncIterator, List, Tuple
from taco.server.start_service import Service

# Set spawn after stdlib imports, but before other imports
from taco.clvm.spend_sim import SimClient, SpendSim
from taco.protocols import full_node_protocol
from taco.simulator.simulator_protocol import FarmNewBlockProtocol
from taco.types.blockchain_format.sized_bytes import bytes32
from taco.types.peer_info import PeerInfo
from taco.util.ints import uint16
from tests.core.node_height import node_height_at_least
from tests.pools.test_pool_rpc import wallet_is_synced
from tests.setup_nodes import (
    setup_simulators_and_wallets,
    setup_node_and_wallet,
    setup_full_system,
    setup_daemon,
    setup_n_nodes,
    setup_introducer,
    setup_timelord,
    setup_two_nodes,
)
from tests.simulation.test_simulation import test_constants_modified
from tests.time_out_assert import time_out_assert
from tests.wallet_tools import WalletTool

multiprocessing.set_start_method("spawn")

from pathlib import Path
from taco.util.keyring_wrapper import KeyringWrapper
from tests.block_tools import BlockTools, test_constants, create_block_tools, create_block_tools_async
from tests.util.keyring import TempKeyring
from tests.setup_nodes import setup_farmer_multi_harvester


@pytest.fixture(scope="session")
def get_keychain():
    with TempKeyring() as keychain:
        yield keychain
        KeyringWrapper.cleanup_shared_instance()


@pytest.fixture(scope="session", name="bt")
def block_tools_fixture(get_keychain) -> BlockTools:
    # Note that this causes a lot of CPU and disk traffic - disk, DB, ports, process creation ...
    _shared_block_tools = create_block_tools(constants=test_constants, keychain=get_keychain)
    return _shared_block_tools


# if you have a system that has an unusual hostname for localhost and you want
# to run the tests, change the `self_hostname` fixture
@pytest_asyncio.fixture(scope="session")
def self_hostname():
    return "localhost"


# NOTE:
#       Instantiating the bt fixture results in an attempt to create the taco root directory
#       which the build scripts symlink to a sometimes-not-there directory.
#       When not there, Python complains since, well, the symlink is not a directory nor points to a directory.
#
#       Now that we have removed the global at tests.setup_nodes.bt, we can move the imports out of
#       the fixtures below. Just be aware of the filesystem modification during bt fixture creation


@pytest_asyncio.fixture(scope="function", params=[1, 2])
async def empty_blockchain(request):
    """
    Provides a list of 10 valid blocks, as well as a blockchain with 9 blocks added to it.
    """
    from tests.util.blockchain import create_blockchain
    from tests.setup_nodes import test_constants

    bc1, db_wrapper, db_path = await create_blockchain(test_constants, request.param)
    yield bc1

    await db_wrapper.close()
    bc1.shut_down()
    db_path.unlink()


@pytest.fixture(scope="function", params=[1, 2])
def db_version(request):
    return request.param


@pytest.fixture(scope="function", params=[1000000, 2300000])
def softfork_height(request):
    return request.param


saved_blocks_version = "rc5"


@pytest.fixture(scope="session")
def default_400_blocks(bt):
    from tests.util.blockchain import persistent_blocks

    return persistent_blocks(400, f"test_blocks_400_{saved_blocks_version}.db", bt, seed=b"400")


@pytest.fixture(scope="session")
def default_1000_blocks(bt):
    from tests.util.blockchain import persistent_blocks

    return persistent_blocks(1000, f"test_blocks_1000_{saved_blocks_version}.db", bt, seed=b"1000")


@pytest.fixture(scope="session")
def pre_genesis_empty_slots_1000_blocks(bt):
    from tests.util.blockchain import persistent_blocks

    return persistent_blocks(
        1000,
        f"pre_genesis_empty_slots_1000_blocks{saved_blocks_version}.db",
        bt,
        seed=b"empty_slots",
        empty_sub_slots=1,
    )


@pytest.fixture(scope="session")
def default_1500_blocks(bt):
    from tests.util.blockchain import persistent_blocks

    return persistent_blocks(1500, f"test_blocks_1500_{saved_blocks_version}.db", bt, seed=b"1500")


@pytest.fixture(scope="session")
def default_10000_blocks(bt):
    from tests.util.blockchain import persistent_blocks

    return persistent_blocks(10000, f"test_blocks_10000_{saved_blocks_version}.db", bt, seed=b"10000")


@pytest.fixture(scope="session")
def default_20000_blocks(bt):
    from tests.util.blockchain import persistent_blocks

    return persistent_blocks(20000, f"test_blocks_20000_{saved_blocks_version}.db", bt, seed=b"20000")


@pytest.fixture(scope="session")
def test_long_reorg_blocks(bt, default_1500_blocks):
    from tests.util.blockchain import persistent_blocks

    return persistent_blocks(
        758,
        f"test_blocks_long_reorg_{saved_blocks_version}.db",
        bt,
        block_list_input=default_1500_blocks[:320],
        seed=b"reorg_blocks",
        time_per_block=8,
    )


@pytest.fixture(scope="session")
def default_2000_blocks_compact(bt):
    from tests.util.blockchain import persistent_blocks

    return persistent_blocks(
        2000,
        f"test_blocks_2000_compact_{saved_blocks_version}.db",
        bt,
        normalized_to_identity_cc_eos=True,
        normalized_to_identity_icc_eos=True,
        normalized_to_identity_cc_ip=True,
        normalized_to_identity_cc_sp=True,
        seed=b"2000_compact",
    )


@pytest.fixture(scope="session")
def default_10000_blocks_compact(bt):
    from tests.util.blockchain import persistent_blocks

    return persistent_blocks(
        10000,
        f"test_blocks_10000_compact_{saved_blocks_version}.db",
        bt,
        normalized_to_identity_cc_eos=True,
        normalized_to_identity_icc_eos=True,
        normalized_to_identity_cc_ip=True,
        normalized_to_identity_cc_sp=True,
        seed=b"1000_compact",
    )


@pytest.fixture(scope="function")
def tmp_dir():
    with tempfile.TemporaryDirectory() as folder:
        yield Path(folder)


# For the below see https://stackoverflow.com/a/62563106/15133773
if os.getenv("_PYTEST_RAISE", "0") != "0":

    @pytest.hookimpl(tryfirst=True)
    def pytest_exception_interact(call):
        raise call.excinfo.value

    @pytest.hookimpl(tryfirst=True)
    def pytest_internalerror(excinfo):
        raise excinfo.value


@pytest_asyncio.fixture(scope="function")
async def wallet_node(self_hostname, request):
    params = {}
    if request and request.param_index > 0:
        params = request.param
    async for _ in setup_node_and_wallet(test_constants, self_hostname, **params):
        yield _


@pytest_asyncio.fixture(scope="function")
async def node_with_params(request):
    params = {}
    if request:
        params = request.param
    async for (sims, wallets) in setup_simulators_and_wallets(1, 0, {}, **params):
        yield sims[0]


@pytest_asyncio.fixture(scope="function")
async def two_nodes(db_version, self_hostname):
    async for _ in setup_two_nodes(test_constants, db_version=db_version, self_hostname=self_hostname):
        yield _


@pytest_asyncio.fixture(scope="function")
async def setup_two_nodes_fixture(db_version):
    async for _ in setup_simulators_and_wallets(2, 0, {}, db_version=db_version):
        yield _


@pytest_asyncio.fixture(scope="function")
async def three_nodes(db_version, self_hostname):
    async for _ in setup_n_nodes(test_constants, 3, db_version=db_version, self_hostname=self_hostname):
        yield _


@pytest_asyncio.fixture(scope="function")
async def four_nodes(db_version, self_hostname):
    async for _ in setup_n_nodes(test_constants, 4, db_version=db_version, self_hostname=self_hostname):
        yield _


@pytest_asyncio.fixture(scope="function")
async def five_nodes(db_version, self_hostname):
    async for _ in setup_n_nodes(test_constants, 5, db_version=db_version, self_hostname=self_hostname):
        yield _


@pytest_asyncio.fixture(scope="function")
async def wallet_nodes(bt):
    async_gen = setup_simulators_and_wallets(2, 1, {"MEMPOOL_BLOCK_BUFFER": 1, "MAX_BLOCK_COST_CLVM": 400000000})
    nodes, wallets = await async_gen.__anext__()
    full_node_1 = nodes[0]
    full_node_2 = nodes[1]
    server_1 = full_node_1.full_node.server
    server_2 = full_node_2.full_node.server
    wallet_a = bt.get_pool_wallet_tool()
    wallet_receiver = WalletTool(full_node_1.full_node.constants)
    yield full_node_1, full_node_2, server_1, server_2, wallet_a, wallet_receiver

    async for _ in async_gen:
        yield _


@pytest_asyncio.fixture(scope="function")
async def setup_four_nodes(db_version):
    async for _ in setup_simulators_and_wallets(5, 0, {}, db_version=db_version):
        yield _


@pytest_asyncio.fixture(scope="function")
async def two_nodes_sim_and_wallets():
    async for _ in setup_simulators_and_wallets(2, 0, {}):
        yield _


@pytest_asyncio.fixture(scope="function")
async def wallet_node_sim_and_wallet():
    async for _ in setup_simulators_and_wallets(1, 1, {}):
        yield _


@pytest_asyncio.fixture(scope="function")
async def wallet_node_100_pk():
    async for _ in setup_simulators_and_wallets(1, 1, {}, initial_num_public_keys=100):
        yield _


@pytest_asyncio.fixture(scope="function")
async def two_wallet_nodes(request):
    params = {}
    if request and request.param_index > 0:
        params = request.param
    async for _ in setup_simulators_and_wallets(1, 2, {}, **params):
        yield _


@pytest_asyncio.fixture(scope="function")
async def three_sim_two_wallets():
    async for _ in setup_simulators_and_wallets(3, 2, {}):
        yield _


@pytest_asyncio.fixture(scope="function")
async def setup_two_nodes_and_wallet():
    async for _ in setup_simulators_and_wallets(2, 1, {}, db_version=2):
        yield _


@pytest_asyncio.fixture(scope="function")
async def setup_two_nodes_and_wallet_fast_retry():
    async for _ in setup_simulators_and_wallets(
        1, 1, {}, config_overrides={"wallet.tx_resend_timeout_secs": 1}, db_version=2
    ):
        yield _


@pytest_asyncio.fixture(scope="function")
async def three_wallet_nodes():
    async for _ in setup_simulators_and_wallets(1, 3, {}):
        yield _


@pytest_asyncio.fixture(scope="function")
async def two_wallet_nodes_five_freeze():
    async for _ in setup_simulators_and_wallets(1, 2, {}):
        yield _


@pytest_asyncio.fixture(scope="function")
async def wallet_node_simulator():
    async for _ in setup_simulators_and_wallets(1, 1, {}):
        yield _


@pytest_asyncio.fixture(scope="function")
async def wallet_two_node_simulator():
    async for _ in setup_simulators_and_wallets(2, 1, {}):
        yield _


@pytest_asyncio.fixture(scope="module")
async def wallet_nodes_mempool_perf(bt):
    key_seed = bt.farmer_master_sk_entropy
    async for _ in setup_simulators_and_wallets(2, 1, {}, key_seed=key_seed):
        yield _


@pytest_asyncio.fixture(scope="module")
async def wallet_nodes_perf(bt):
    async_gen = setup_simulators_and_wallets(1, 1, {"MEMPOOL_BLOCK_BUFFER": 1, "MAX_BLOCK_COST_CLVM": 11000000000})
    nodes, wallets = await async_gen.__anext__()
    full_node_1 = nodes[0]
    server_1 = full_node_1.full_node.server
    wallet_a = bt.get_pool_wallet_tool()
    wallet_receiver = WalletTool(full_node_1.full_node.constants)
    yield full_node_1, server_1, wallet_a, wallet_receiver

    async for _ in async_gen:
        yield _


@pytest_asyncio.fixture(scope="function")
async def wallet_node_starting_height(self_hostname):
    async for _ in setup_node_and_wallet(test_constants, self_hostname, starting_height=100):
        yield _


@pytest_asyncio.fixture(scope="function")
async def wallet_nodes_mainnet(bt, db_version):
    async_gen = setup_simulators_and_wallets(2, 1, {"NETWORK_TYPE": 0}, db_version=db_version)
    nodes, wallets = await async_gen.__anext__()
    full_node_1 = nodes[0]
    full_node_2 = nodes[1]
    server_1 = full_node_1.full_node.server
    server_2 = full_node_2.full_node.server
    wallet_a = bt.get_pool_wallet_tool()
    wallet_receiver = WalletTool(full_node_1.full_node.constants)
    yield full_node_1, full_node_2, server_1, server_2, wallet_a, wallet_receiver

    async for _ in async_gen:
        yield _


@pytest_asyncio.fixture(scope="function")
async def three_nodes_two_wallets():
    async for _ in setup_simulators_and_wallets(3, 2, {}):
        yield _


@pytest_asyncio.fixture(scope="function")
async def wallet_and_node():
    async for _ in setup_simulators_and_wallets(1, 1, {}):
        yield _


@pytest_asyncio.fixture(scope="function")
async def one_node_one_block(bt, wallet_a):
    async_gen = setup_simulators_and_wallets(1, 0, {})
    nodes, _ = await async_gen.__anext__()
    full_node_1 = nodes[0]
    server_1 = full_node_1.full_node.server

    reward_ph = wallet_a.get_new_puzzlehash()
    blocks = bt.get_consecutive_blocks(
        1,
        guarantee_transaction_block=True,
        farmer_reward_puzzle_hash=reward_ph,
        pool_reward_puzzle_hash=reward_ph,
        genesis_timestamp=10000,
        time_per_block=10,
    )
    assert blocks[0].height == 0

    for block in blocks:
        await full_node_1.full_node.respond_block(full_node_protocol.RespondBlock(block))

    await time_out_assert(60, node_height_at_least, True, full_node_1, blocks[-1].height)

    yield full_node_1, server_1

    async for _ in async_gen:
        yield _


@pytest_asyncio.fixture(scope="function")
async def two_nodes_one_block(bt, wallet_a):
    async_gen = setup_simulators_and_wallets(2, 0, {})
    nodes, _ = await async_gen.__anext__()
    full_node_1 = nodes[0]
    full_node_2 = nodes[1]
    server_1 = full_node_1.full_node.server
    server_2 = full_node_2.full_node.server

    reward_ph = wallet_a.get_new_puzzlehash()
    blocks = bt.get_consecutive_blocks(
        1,
        guarantee_transaction_block=True,
        farmer_reward_puzzle_hash=reward_ph,
        pool_reward_puzzle_hash=reward_ph,
        genesis_timestamp=10000,
        time_per_block=10,
    )
    assert blocks[0].height == 0

    for block in blocks:
        await full_node_1.full_node.respond_block(full_node_protocol.RespondBlock(block))

    await time_out_assert(60, node_height_at_least, True, full_node_1, blocks[-1].height)

    yield full_node_1, full_node_2, server_1, server_2

    async for _ in async_gen:
        yield _


@pytest_asyncio.fixture(scope="function")
async def farmer_one_harvester(tmp_path: Path, bt: BlockTools) -> AsyncIterator[Tuple[List[Service], Service]]:
    async for _ in setup_farmer_multi_harvester(bt, 1, tmp_path, test_constants, start_services=True):
        yield _


@pytest_asyncio.fixture(scope="function")
async def farmer_one_harvester_not_started(
    tmp_path: Path, bt: BlockTools
) -> AsyncIterator[Tuple[List[Service], Service]]:
    async for _ in setup_farmer_multi_harvester(bt, 1, tmp_path, test_constants, start_services=False):
        yield _


@pytest_asyncio.fixture(scope="function")
async def farmer_two_harvester_not_started(
    tmp_path: Path, bt: BlockTools
) -> AsyncIterator[Tuple[List[Service], Service]]:
    async for _ in setup_farmer_multi_harvester(bt, 2, tmp_path, test_constants, start_services=False):
        yield _


@pytest_asyncio.fixture(scope="function")
async def farmer_three_harvester_not_started(
    tmp_path: Path, bt: BlockTools
) -> AsyncIterator[Tuple[List[Service], Service]]:
    async for _ in setup_farmer_multi_harvester(bt, 3, tmp_path, test_constants, start_services=False):
        yield _


# TODO: Ideally, the db_version should be the (parameterized) db_version
# fixture, to test all versions of the database schema. This doesn't work
# because of a hack in shutting down the full node, which means you cannot run
# more than one simulations per process.
@pytest_asyncio.fixture(scope="function")
async def daemon_simulation(bt, get_b_tools, get_b_tools_1):
    async for _ in setup_full_system(
        test_constants_modified,
        bt,
        b_tools=get_b_tools,
        b_tools_1=get_b_tools_1,
        connect_to_daemon=True,
        db_version=1,
    ):
        yield _


@pytest_asyncio.fixture(scope="function")
async def get_daemon(bt):
    async for _ in setup_daemon(btools=bt):
        yield _


@pytest_asyncio.fixture(scope="function")
async def get_temp_keyring():
    with TempKeyring() as keychain:
        yield keychain


@pytest_asyncio.fixture(scope="function")
async def get_b_tools_1(get_temp_keyring):
    return await create_block_tools_async(constants=test_constants_modified, keychain=get_temp_keyring)


@pytest_asyncio.fixture(scope="function")
async def get_b_tools(get_temp_keyring):
    local_b_tools = await create_block_tools_async(constants=test_constants_modified, keychain=get_temp_keyring)
    new_config = local_b_tools._config
    local_b_tools.change_config(new_config)
    return local_b_tools


@pytest_asyncio.fixture(scope="function")
async def get_daemon_with_temp_keyring(get_b_tools):
    async for daemon in setup_daemon(btools=get_b_tools):
        yield get_b_tools, daemon


@pytest_asyncio.fixture(scope="function")
async def wallets_prefarm(two_wallet_nodes, self_hostname, trusted):
    """
    Sets up the node with 10 blocks, and returns a payer and payee wallet.
    """
    farm_blocks = 10
    buffer = 4
    full_nodes, wallets = two_wallet_nodes
    full_node_api = full_nodes[0]
    full_node_server = full_node_api.server
    wallet_node_0, wallet_server_0 = wallets[0]
    wallet_node_1, wallet_server_1 = wallets[1]
    wallet_0 = wallet_node_0.wallet_state_manager.main_wallet
    wallet_1 = wallet_node_1.wallet_state_manager.main_wallet

    ph0 = await wallet_0.get_new_puzzlehash()
    ph1 = await wallet_1.get_new_puzzlehash()

    if trusted:
        wallet_node_0.config["trusted_peers"] = {full_node_server.node_id.hex(): full_node_server.node_id.hex()}
        wallet_node_1.config["trusted_peers"] = {full_node_server.node_id.hex(): full_node_server.node_id.hex()}
    else:
        wallet_node_0.config["trusted_peers"] = {}
        wallet_node_1.config["trusted_peers"] = {}

    await wallet_server_0.start_client(PeerInfo(self_hostname, uint16(full_node_server._port)), None)
    await wallet_server_1.start_client(PeerInfo(self_hostname, uint16(full_node_server._port)), None)

    for i in range(0, farm_blocks):
        await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph0))

    for i in range(0, farm_blocks):
        await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph1))

    for i in range(0, buffer):
        await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(bytes32(token_bytes(nbytes=32))))

    await time_out_assert(10, wallet_is_synced, True, wallet_node_0, full_node_api)
    await time_out_assert(10, wallet_is_synced, True, wallet_node_1, full_node_api)

    return wallet_node_0, wallet_node_1, full_node_api


@pytest_asyncio.fixture(scope="function")
async def introducer(bt):
    async for _ in setup_introducer(bt, 0):
        yield _


@pytest_asyncio.fixture(scope="function")
async def timelord(bt):
    async for _ in setup_timelord(uint16(0), False, test_constants, bt):
        yield _


@pytest_asyncio.fixture(scope="function")
async def setup_sim():
    sim = await SpendSim.create()
    sim_client = SimClient(sim)
    await sim.farm_block()
    return sim, sim_client
