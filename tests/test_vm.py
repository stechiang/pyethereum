import pytest
import json
import pyethereum.processblock as pb
import pyethereum.blocks as blocks
import pyethereum.transactions as transactions
import pyethereum.utils as u
import os
import sys

import logging
logging.basicConfig(level=logging.DEBUG, format='%(message)s')
logger = logging.getLogger()
pblogger = pb.pblogger

# customize VM log output to your needs
# hint: use 'py.test' with the '-s' option to dump logs to the console
pblogger.log_pre_state = True    # dump storage at account before execution
pblogger.log_post_state = True   # dump storage at account after execution
pblogger.log_block = False       # dump block after TX was applied
pblogger.log_memory = False      # dump memory before each op
pblogger.log_op = True           # log op, gas, stack before each op
pblogger.log_json = False        # generate machine readable output


def check_testdata(data_keys, expected_keys):
    assert set(data_keys) == set(expected_keys), \
        "test data changed, please adjust tests"


@pytest.fixture(scope="module")
def vm_tests_fixtures():
    """Read vm tests from fixtures"""
    # FIXME: assert that repo is uptodate
    # cd fixtures; git pull origin develop; cd ..;  git commit fixtures
    filenames = os.listdir(os.path.join('fixtures', 'vmtests'))
    files = [os.path.join('fixtures', 'vmtests', f) for f in filenames]
    vm_fixtures = {}
    try:
        for f, fn in zip(files, filenames):
            if f[-5:] == '.json':
                vm_fixtures[fn[:-5]] = json.load(open(f, 'r'))
    except IOError:
        raise IOError("Could not read vmtests.json from fixtures",
            "Make sure you did 'git submodule init'")
    #assert set(vm_fixture.keys()) == set(['boolean', 'suicide', 'random', 'arith', 'mktx']),\
    #    "Tests changed, try updating the fixtures submodule"
    return vm_fixtures

test_random = lambda: do_test_vm('random', 'random')
test_boolean = lambda: do_test_vm('vmtests', 'boolean')
test_boolean = lambda: do_test_vm('vmtests', 'suicide')
test_boolean = lambda: do_test_vm('vmtests', 'arith')
test_boolean = lambda: do_test_vm('vmtests', 'mktx')
test_add = lambda: do_test_vm('vmArithmeticTest')
test_bitwise = lambda: do_test_vm('vmBitwiseLogicOperationTest')
test_blockinfo = lambda: do_test_vm('vmBlockInfoTest')
test_envinfo = lambda: do_test_vm('vmEnvironmentalInfoTest')
test_pushdupswap = lambda: do_test_vm('vmPushDupSwapTest')
test_ioandflow = lambda: do_test_vm('vmIOandFlowOperationsTest')
test_sha3 = lambda: do_test_vm('vmSha3Test')
test_sysops = lambda: do_test_vm('vmSystemOperationsTest')

faulty = [
    # Put a list of strings of known faulty tests here
]


def do_test_vm(filename, testname=None, limit=99999999):
    if testname is None:
        for testname in vm_tests_fixtures()[filename].keys()[:limit]:
            do_test_vm(filename, testname)
        return
    if testname in faulty:
        logger.debug('skipping test:%r in %r', testname, filename)
        return
    logger.debug('running test:%r in %r', testname, filename)
    params = vm_tests_fixtures()[filename][testname]

    pre = params['pre']
    exek = params['exec']
    callcreates = params['callcreates']
    env = params['env']
    post = params['post']

    check_testdata(env.keys(), ['currentGasLimit', 'currentTimestamp',
                                'previousHash', 'currentCoinbase',
                                'currentDifficulty', 'currentNumber'])
    # setup env
    blk = blocks.Block(
        prevhash=env['previousHash'].decode('hex'),
        number=int(env['currentNumber']),
        coinbase=env['currentCoinbase'],
        difficulty=int(env['currentDifficulty']),
        gas_limit=int(env['currentGasLimit']),
        timestamp=int(env['currentTimestamp']))

    # code FIXME WHAT TO DO WITH THIS CODE???
    # if isinstance(env['code'], str):
    #     continue
    # else:
    #     addr = 0 # FIXME
    #     blk.set_code(addr, ''.join(map(chr, env['code'])))

    # setup state
    for address, h in pre.items():
        check_testdata(h.keys(), ['code', 'nonce', 'balance', 'storage'])
        blk.set_nonce(address, int(h['nonce']))
        blk.set_balance(address, int(h['balance']))
        blk.set_code(address, h['code'][2:].decode('hex'))
        for k, v in h['storage']:
            blk.set_storage_data(address,
                                 u.big_endian_to_int(k.decode('hex')),
                                 u.big_endian_to_int(v.decode('hex')))
        pblogger.log('PRE Balance', address=address, balance=h['balance'])

    # execute transactions
    sender = exek['caller']  # a party that originates a call
    recvaddr = exek['address']
    tx = transactions.Transaction(
        nonce=blk._get_acct_item(exek['caller'], 'nonce'),
        gasprice=int(exek['gasPrice']),
        startgas=int(exek['gas']),
        to=recvaddr,
        value=int(exek['value']),
        data=exek['data'][2:].decode('hex'))
    tx.sender = sender
    pblogger.log_apply_op = True
    pblogger.log_op = True
    pblogger.log('TX', tx=tx.hex_hash(), sender=sender, to=recvaddr, value=tx.value, startgas=tx.startgas, gasprice=tx.gasprice)

    # capture apply_message calls
    apply_message_calls = []
    orig_apply_msg = pb.apply_msg

    def apply_msg_wrapper(_block, _tx, msg, code, toplevel=False):
        apply_message_calls.append(dict(gasLimit=msg.gas, value=msg.value,
                                        destination=msg.to,
                                        data=msg.data.encode('hex')))
        if not toplevel:
            pb.apply_msg = orig_apply_msg
        result, gas_rem, data = orig_apply_msg(_block, _tx, msg, code)
        if not toplevel:
            pb.apply_msg = apply_msg_wrapper
        return result, gas_rem, data

    pb.apply_msg = apply_msg_wrapper

    msg = pb.Message(tx.sender, tx.to, tx.value, tx.startgas, tx.data)
    blk.delta_balance(exek['caller'], tx.value)
    blk.delta_balance(exek['address'], -tx.value)
    success, gas_remained, output = \
        pb.apply_msg(blk, tx, msg, exek['code'][2:].decode('hex'), toplevel=True)
    while len(blk.postqueue):
        msg2 = blk.postqueue.pop(0)
        pb.apply_msg(blk, tx, msg2, blk.get_code(msg2.to))
    pb.apply_msg = orig_apply_msg
    apply_message_calls.pop(0)
    blk.commit_state()

    assert len(callcreates) == len(apply_message_calls)

    # check against callcreates
    for i, callcreate in enumerate(callcreates):
        amc = apply_message_calls[i]
        assert callcreate['data'] == '0x'+amc['data']
        assert callcreate['gasLimit'] == str(amc['gasLimit'])
        assert callcreate['value'] == str(amc['value'])
        assert callcreate['destination'] == amc['destination']

    assert '0x'+''.join(map(chr, output)).encode('hex') == params['out']
    assert str(gas_remained) == params['gas']

    # check state
    for address, data in post.items():
        state = blk.account_to_dict(address, for_vmtest=True)
        state.pop('storage_root', None)  # attribute not present in vmtest fixtures
        assert data == state
