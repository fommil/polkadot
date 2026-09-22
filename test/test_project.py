## tests the TinyTapeout wrapper tt_um_fommil_polkadot_E4M3 through its pins

import os
from collections import namedtuple
from math import isqrt
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge
import ml_dtypes
import numpy as np

# opcodes, must match the localparams in src/project.v
OP_NOP        = 0b0000
OP_LOAD_A_CLR = 0b0001
OP_LOAD_A     = 0b0010
OP_MAC        = 0b0011
OP_ADD_CLR    = 0b0100
OP_ADD        = 0b0101
OP_CTRL       = 0b0110
OP_RESERVED   = range(0b0111, 0b10000)

# CTRL operand, rmode must match the localparams in src/polkadot.v
RNE = 0
RTZ = 1
SAT = 0b1000

# as in test_polkadot.py, the default exhausts all 2**16 pairs
SWEEP_LIMIT = int(os.environ.get("SWEEP_LIMIT") or 1 << 17)

# OCP OFP8 E4M3 (fn): BIAS 7, no Inf, S.1111.111 is the only NaN
CODES  = 256
NEG    = 0x80
ZERO   = 0x00
MIN    = 0x01  # smallest subnormal
EIGHTH = 0x20  # 2**-3
HALF   = 0x30
ONE    = 0x38
TWO    = 0x40
MAX    = 0x7E  # 448
NAN    = 0x7F  # canonical

# see test_polkadot.py for why SUBULP_N * MIN * EIGHTH is interesting
SUBULP_N   = 8
SUBULP_TIE = SUBULP_N // 2

with np.errstate(invalid="ignore"):
    DECODE = (np.arange(CODES, dtype=np.uint8)
              .view(ml_dtypes.float8_e4m3fn).astype(np.float64))
MAX_F = DECODE[MAX]
OVF_MAG = MAX_F + 16.0  # the RNE tie between MAX and the missing 480

def isnan(code):
    return bool(np.isnan(DECODE[int(code)]))

# codes compare exactly, except that any NaN will do for a NaN
def matches(got, expect):
    return int(got) == expect or (isnan(expect) and isnan(got))

# the oracle, exact is a float64 which is exact for one product or one sum.
# The overflow region is decided here so that ml_dtypes only ever rounds
# in-range values.
def round_ref(exact, rmode=RNE, sat=0):
    if np.isnan(exact):
        return NAN
    neg = NEG if np.signbit(exact) else 0
    mag = abs(exact)
    if mag > OVF_MAG and rmode == RNE and not sat:
        return NAN
    if mag >= MAX_F:
        return neg | MAX
    y = int(np.array([exact]).astype(ml_dtypes.float8_e4m3fn).view(np.uint8)[0])
    if rmode == RTZ and abs(DECODE[y]) > mag:
        y -= 1  # sign-magnitude, so this is one code toward zero
    return y

def product(a, b):
    with np.errstate(invalid="ignore"):
        return DECODE[a] * DECODE[b]

def total(a, b):
    with np.errstate(invalid="ignore"):
        return DECODE[a] + DECODE[b]

def sample_codes():
    if CODES * CODES <= SWEEP_LIMIT:
        return list(range(CODES))
    want = max(2, isqrt(SWEEP_LIMIT))
    step = -(-CODES // want) | 1  # odd, so both mantissa parities
    specials = {ZERO, MIN, EIGHTH, ONE, MAX, NAN}
    return sorted(set(range(0, CODES, step)) | specials | {c | NEG for c in specials})

SWEEP = sample_codes()

Pins = namedtuple("Pins", "y strobe inexact overflow invalid")

# inputs change on the falling edge, outputs are sampled once the rising edge
# that consumed them has settled, i.e. the cycle after the instruction
async def issue(dut, op, byte=0):
    dut.uio_in.value = op
    dut.ui_in.value = byte
    await RisingEdge(dut.clk)
    await ReadOnly()
    y = int(dut.uo_out.value)
    out = int(dut.uio_out.value)
    await FallingEdge(dut.clk)
    return Pins(y, (out >> 7) & 1, (out >> 4) & 1, (out >> 5) & 1, (out >> 6) & 1)

def flags(pins):
    return (pins.inexact, pins.overflow, pins.invalid)

async def reset(dut):
    dut.rst_n.value = 0
    dut.ui_in.value = 0
    dut.uio_in.value = OP_NOP
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1

async def start(dut):
    Clock(dut.clk, 10, unit="ns").start()
    dut.ena.value = 1
    await reset(dut)

async def mac(dut, a, b):
    await issue(dut, OP_LOAD_A_CLR, a)
    return await issue(dut, OP_MAC, b)

async def add(dut, a, b):
    await issue(dut, OP_ADD_CLR, a)
    return await issue(dut, OP_ADD, b)

async def ctrl(dut, rmode=RNE, sat=0):
    return await issue(dut, OP_CTRL, rmode | (SAT if sat else 0))

def check(pins, exact, rmode, sat, debug):
    expect = round_ref(exact, rmode, sat)
    debug = f"{debug} rmode={rmode} sat={sat} exact={exact} expect={DECODE[expect]} got={DECODE[pins.y]}"
    assert matches(pins.y, expect), debug
    assert pins.strobe == 1, debug
    assert pins.invalid == 0, debug  # there is no Inf to introduce a NaN
    if not isnan(expect):
        assert pins.inexact == int(DECODE[pins.y] != exact), (debug, pins)
    return pins.inexact

## PROTOCOL

@cocotb.test()
async def test_reset(dut):
    await start(dut)

    pins = await issue(dut, OP_NOP)
    assert int(dut.uio_oe.value) == 0b1111_0000
    assert int(dut.uio_out.value) & 0b1111 == 0
    assert pins.strobe == 0

@cocotb.test()
async def test_multiply(dut):
    await start(dut)

    pins = await mac(dut, ONE, ONE)
    assert pins.y == ONE
    assert pins.strobe == 1
    assert flags(pins) == (0, 0, 0), pins

# the worked example in project.v, with a=1, b=2, c=0.5, d=3, e=1
@cocotb.test()
async def test_example(dut):
    await start(dut)
    THREE = 0x44

    pins = await issue(dut, OP_LOAD_A_CLR, ONE)
    assert pins.strobe == 0
    pins = await issue(dut, OP_MAC, TWO)
    assert (DECODE[pins.y], pins.strobe) == (2.0, 1), pins
    pins = await issue(dut, OP_LOAD_A, HALF)
    assert (DECODE[pins.y], pins.strobe) == (2.0, 0), pins
    pins = await issue(dut, OP_MAC, THREE)
    assert (DECODE[pins.y], pins.strobe) == (3.5, 1), pins
    pins = await ctrl(dut)
    assert (DECODE[pins.y], pins.strobe) == (3.5, 1), pins
    pins = await issue(dut, OP_ADD, ONE)
    assert (DECODE[pins.y], pins.strobe) == (4.5, 1), pins
    assert flags(pins) == (0, 0, 0), pins

@cocotb.test()
async def test_strobe(dut):
    await start(dut)

    expect = [(OP_LOAD_A_CLR, 0), (OP_MAC, 1), (OP_LOAD_A, 0), (OP_NOP, 0),
              (OP_CTRL, 1), (OP_ADD, 1), (OP_ADD_CLR, 1), (OP_NOP, 0)]
    expect += [(op, 0) for op in OP_RESERVED]
    for op, strobe in expect:
        pins = await issue(dut, op, ONE)
        assert pins.strobe == strobe, (op, pins)

# reserved opcodes must not touch reg_a, the pending clear, the accumulator or
# CTRL, even with a hostile operand
@cocotb.test()
async def test_reserved(dut):
    await start(dut)

    await mac(dut, ONE, ONE)
    await issue(dut, OP_LOAD_A_CLR, ONE)
    for op in OP_RESERVED:
        pins = await issue(dut, op, MAX)
        assert pins.y == ONE, (op, pins)
        assert pins.strobe == 0, (op, pins)
    pins = await issue(dut, OP_MAC, TWO)
    assert pins.y == TWO, DECODE[pins.y]

    # rmode and sat are still the reset defaults
    pins = await mac(dut, MAX, MAX)
    assert isnan(pins.y), DECODE[pins.y]

# only uio_in[3:0] is the opcode
@cocotb.test()
async def test_upper_uio_ignored(dut):
    await start(dut)

    await issue(dut, 0xF0 | OP_LOAD_A_CLR, TWO)
    pins = await issue(dut, 0xF0 | OP_MAC, TWO)
    assert DECODE[pins.y] == 4.0, DECODE[pins.y]
    assert pins.strobe == 1

    # and only ui_in[3:0] is the CTRL operand
    pins = await issue(dut, OP_CTRL, 0xF0 | RNE)
    await mac(dut, MAX, MAX)
    pins = await issue(dut, OP_NOP)
    assert isnan(pins.y), DECODE[pins.y]

@cocotb.test()
async def test_reg_a_persists(dut):
    await start(dut)

    await issue(dut, OP_LOAD_A_CLR, TWO)
    await issue(dut, OP_MAC, ONE)
    await issue(dut, OP_MAC, HALF)
    pins = await issue(dut, OP_MAC, HALF)
    assert DECODE[pins.y] == 4.0, DECODE[pins.y]

    # ADD doesn't clobber it
    await issue(dut, OP_ADD, ONE)
    pins = await issue(dut, OP_MAC, HALF)
    assert DECODE[pins.y] == 6.0, DECODE[pins.y]

# the pending clear survives NOP, LOAD_A and CTRL, and the last LOAD_A wins
@cocotb.test()
async def test_pending_clear(dut):
    await start(dut)

    await mac(dut, ONE, ONE)
    pins = await issue(dut, OP_LOAD_A_CLR, TWO)
    assert pins.y == ONE
    for op, byte in [(OP_NOP, 0), (OP_LOAD_A, HALF), (OP_CTRL, RNE), (OP_NOP, 0)]:
        pins = await issue(dut, op, byte)
        assert pins.y == ONE, (op, pins)
    pins = await issue(dut, OP_MAC, ONE)
    assert pins.y == HALF, DECODE[pins.y]

    # and is consumed by the MAC
    pins = await issue(dut, OP_MAC, ONE)
    assert pins.y == ONE, DECODE[pins.y]

# LOAD_A_CLR a; ADD e is ADD_CLR e, but a is still loaded
@cocotb.test()
async def test_load_a_clr_add(dut):
    await start(dut)

    await mac(dut, TWO, TWO)
    await issue(dut, OP_LOAD_A_CLR, TWO)
    pins = await issue(dut, OP_ADD, ONE)
    assert pins.y == ONE, DECODE[pins.y]
    pins = await issue(dut, OP_MAC, ONE)
    assert DECODE[pins.y] == 3.0, DECODE[pins.y]

# ADD_CLR uses 1.0, not reg_a, and clears whatever was pending
@cocotb.test()
async def test_add_clr(dut):
    await start(dut)

    await mac(dut, TWO, TWO)
    await issue(dut, OP_LOAD_A, MAX)
    pins = await issue(dut, OP_ADD_CLR, TWO)
    assert pins.y == TWO, DECODE[pins.y]

    await issue(dut, OP_LOAD_A_CLR, HALF)
    pins = await issue(dut, OP_ADD_CLR, TWO)
    assert pins.y == TWO, DECODE[pins.y]
    pins = await issue(dut, OP_ADD, ONE)
    assert DECODE[pins.y] == 3.0, DECODE[pins.y]

# two _CLR in a row discards the first
@cocotb.test()
async def test_double_clear(dut):
    await start(dut)

    await mac(dut, TWO, TWO)
    await issue(dut, OP_LOAD_A_CLR, TWO)
    await issue(dut, OP_LOAD_A_CLR, HALF)
    pins = await issue(dut, OP_MAC, ONE)
    assert pins.y == HALF, DECODE[pins.y]

    await issue(dut, OP_ADD_CLR, TWO)
    pins = await issue(dut, OP_ADD_CLR, HALF)
    assert pins.y == HALF, DECODE[pins.y]

# CTRL applies retrospectively and persists across accumulations
@cocotb.test()
async def test_ctrl_persists(dut):
    await start(dut)

    pins = await ctrl(dut, sat=1)
    pins = await mac(dut, MAX, MAX)
    assert pins.y == MAX, DECODE[pins.y]
    assert flags(pins) == (1, 1, 0), pins
    pins = await mac(dut, MAX | NEG, MAX)
    assert pins.y == MAX | NEG, DECODE[pins.y]

    pins = await ctrl(dut)
    assert isnan(pins.y), DECODE[pins.y]
    assert flags(pins) == (1, 1, 0), pins
    pins = await ctrl(dut, RTZ)
    assert pins.y == MAX | NEG, DECODE[pins.y]
    pins = await mac(dut, MAX, MAX)
    assert pins.y == MAX, DECODE[pins.y]

@cocotb.test()
async def test_reset_restores_defaults(dut):
    await start(dut)

    await ctrl(dut, RTZ, sat=1)
    await issue(dut, OP_LOAD_A, TWO)
    await reset(dut)

    pins = await mac(dut, MAX, MAX)
    assert isnan(pins.y), DECODE[pins.y]
    # reg_a was reset, and the accumulator was cleared by the LOAD_A_CLR
    await issue(dut, OP_LOAD_A_CLR, ZERO)
    await reset(dut)
    pins = await issue(dut, OP_ADD_CLR, ONE)
    pins = await issue(dut, OP_MAC, MAX)
    assert pins.y == ONE, DECODE[pins.y]

## ARITHMETIC

@cocotb.test()
async def test_dotproduct(dut):
    await start(dut)

    for sign in (0, NEG):
        await issue(dut, OP_LOAD_A_CLR, MIN | sign)
        for _ in range(SUBULP_N):
            pins = await issue(dut, OP_MAC, EIGHTH)
        assert pins.y == MIN | sign, DECODE[pins.y]
        assert flags(pins) == (0, 0, 0), pins

    # lots of dynamic range
    await issue(dut, OP_LOAD_A_CLR, MAX)
    await issue(dut, OP_MAC, MAX)
    await issue(dut, OP_LOAD_A, MIN)
    for _ in range(SUBULP_N):
        await issue(dut, OP_MAC, EIGHTH)
    await issue(dut, OP_LOAD_A, MAX | NEG)
    pins = await issue(dut, OP_MAC, MAX)
    assert pins.y == MIN, DECODE[pins.y]
    assert flags(pins) == (0, 0, 0), pins

@cocotb.test()
async def test_flags(dut):
    await start(dut)

    # inexact
    await mac(dut, ONE, ONE)
    await issue(dut, OP_LOAD_A, MIN)
    pins = await issue(dut, OP_MAC, MIN)
    assert pins.y == ONE
    assert flags(pins) == (1, 0, 0), pins

    # underflow, reconstructed as documented in project.v
    await issue(dut, OP_LOAD_A_CLR, MIN)
    for _ in range(SUBULP_TIE):
        pins = await issue(dut, OP_MAC, EIGHTH)
    assert pins.y == ZERO, DECODE[pins.y]
    assert flags(pins) == (1, 0, 0), pins
    assert (pins.y >> 3) & 0xF == 0

    # overflow
    pins = await mac(dut, MAX, MAX)
    assert isnan(pins.y), DECODE[pins.y]
    assert flags(pins) == (1, 1, 0), pins

    # a NaN is propagated, not introduced
    pins = await mac(dut, NAN, ONE)
    assert isnan(pins.y), DECODE[pins.y]
    assert flags(pins) == (0, 0, 0), pins

# rmode alternates, and is flipped retrospectively on every pair, so each pair
# is checked under both RNE and RTZ in 3 cycles
@cocotb.test()
async def test_mac_sweep(dut):
    await start(dut)

    rmode, inexact = RNE, 0
    for a in SWEEP:
        for b in SWEEP:
            debug = f"a={DECODE[a]} b={DECODE[b]}"
            exact = product(a, b)
            pins = await mac(dut, a, b)
            inexact += check(pins, exact, rmode, 0, debug)
            rmode ^= 1
            pins = await ctrl(dut, rmode)
            inexact += check(pins, exact, rmode, 0, debug)

    assert inexact > 0

@cocotb.test()
async def test_add_sweep(dut):
    await start(dut)

    rmode, inexact = RNE, 0
    for a in SWEEP:
        for b in SWEEP:
            debug = f"a={DECODE[a]} b={DECODE[b]}"
            exact = total(a, b)
            pins = await add(dut, a, b)
            inexact += check(pins, exact, rmode, 0, debug)
            rmode ^= 1
            pins = await ctrl(dut, rmode)
            inexact += check(pins, exact, rmode, 0, debug)

    assert inexact > 0

# only the pairs where saturation is observable
@cocotb.test()
async def test_sat_sweep(dut):
    await start(dut)

    await ctrl(dut, RNE, sat=1)
    count = 0
    for a in SWEEP:
        for b in SWEEP:
            exact = product(a, b)
            if round_ref(exact, RNE, 1) == round_ref(exact, RNE, 0):
                continue
            count += 1
            pins = await mac(dut, a, b)
            check(pins, exact, RNE, 1, f"a={DECODE[a]} b={DECODE[b]}")

    assert count > 0

# Local Variables:
# compile-command: "cd .. ; SWEEP_LIMIT=500 make test_project"
# End:
