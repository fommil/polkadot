import os
from math import isqrt

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

# rounding modes, must match the localparams in src/polkadot.v
RNE = 0  # nearest, ties to even
RTZ = 1  # toward zero

# SWEEP_LIMIT is roughly the most transactions we will run in any one pair
# sweep: a format whose every code pair fits inside it is swept exhaustively,
# anything larger is sampled down to about sqrt(limit) codes.
# The default exhausts the 8 bit formats (2**16 pairs) and samples the 16 bit
# ones. Lower it (e.g. 500) for a smoke test, raise it to buy more coverage;
# exhausting 16 bits needs 2**32, which is days rather than minutes.
SWEEP_LIMIT = int(os.environ.get("SWEEP_LIMIT") or 1 << 17)

# the codes to sweep in the pair tests, in descending order of usefulness:
# mantissa 1 and all ones make products inexact at every scale (so the RTZ
# fallbacks are exercised however hard we prune), the midpoint and the midpoint
# plus one bracket the RNE tie, and 0 is the exact case.
def _mantissas(MAN):
    full = 1 << MAN
    return [1, full - 1, full >> 1, (full >> 1) | 1, 0]

def sample_codes(EXP, MAN):
    codes = 2 << (EXP + MAN)
    if codes * codes <= SWEEP_LIMIT:
        return list(range(codes))

    full = 1 << MAN
    want = max(2, isqrt(SWEEP_LIMIT))  # codes to sweep
    rows = 2 << EXP              # the (sign, exponent) combinations
    picks = _mantissas(MAN)

    if want >= rows:  # every (sign, exponent), several mantissas each
        per = want // rows
        mans = set(picks[:per])
        if per > len(picks):  # spread the remainder over the mantissa range
            step = (full // (per - len(picks))) | 1  # odd, so both parities
            mans |= set(range(0, full, step))
        return [row << MAN | man
                for row in range(rows)
                for man in sorted(mans)]

    # too tight even for one mantissa per exponent, so stride the exponents and
    # rotate the mantissa, keeping all of the interesting ones in the sweep
    step = -(-rows // want)
    return [row << MAN | picks[i % len(picks)]
            for i, row in enumerate(range(0, rows, step))]

# the format under test, e.g. E4M3fn-G1, appended to every test name
def _config():
    top = cocotb.top
    EXP, MAN = int(top.EXP.value), int(top.MAN.value)
    FN = int(top.FN.value)
    GUARD = int(top.GUARD.value)
    return "E{}M{}{}{}".format(EXP, MAN,
                               "fn" if FN else "",
                               f"-G{GUARD}" if GUARD else "")

CONFIG = _config()

# cocotb.test, but with CONFIG in the test's name (name= needs cocotb 2.0)
def test(**kwargs):
    def decorate(func):
        return cocotb.test(name=f"{func.__qualname__}[{CONFIG}]", **kwargs)(func)
    return decorate

test.__test__ = False  # this is a decorator, not a test, as far as pytest cares

async def start(dut):
    Clock(dut.clk, 10, unit="ns").start()

    dut.rst_n.value = 0
    dut.clear.value = 0
    dut.valid.value = 0
    dut.rmode.value = RNE
    dut.sat.value = 0
    dut.a.value = 0
    dut.b.value = 0
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

async def mult(dut, a, b, clear=1, rmode=RNE, sat=0):
    dut.valid.value = 1
    dut.clear.value = clear
    dut.rmode.value = rmode
    dut.sat.value = sat
    dut.a.value = a
    dut.b.value = b
    await RisingEdge(dut.clk)

    dut.clear.value = 0
    dut.valid.value = 0

    await RisingEdge(dut.clk)
    #await Timer(1, units="ns")

# NEEDS a definition of 1.0
async def add(dut, one, a, b, rmode=RNE, sat=0):
    dut.valid.value = 1
    dut.clear.value = 1
    dut.rmode.value = rmode
    dut.sat.value = sat
    dut.a.value = one
    dut.b.value = a
    await RisingEdge(dut.clk)

    dut.clear.value = 0
    dut.a.value = one
    dut.b.value = b
    await RisingEdge(dut.clk)

    dut.clear.value = 0
    dut.valid.value = 0

    await RisingEdge(dut.clk)
    #await Timer(1, units="ns")

# no idle gaps
async def dot(dut, terms, rmode=RNE, sat=0):
    dut.rmode.value = rmode
    dut.sat.value = sat
    dut.valid.value = 1
    for i, (a, b) in enumerate(terms):
        dut.clear.value = 1 if i == 0 else 0
        dut.a.value = a
        dut.b.value = b
        await RisingEdge(dut.clk)

    dut.clear.value = 0
    dut.valid.value = 0

    await RisingEdge(dut.clk)

async def change_rmode(dut, rmode):
    dut.rmode.value = rmode
    await RisingEdge(dut.clk)

async def change_sat(dut, sat):
    dut.sat.value = sat
    await RisingEdge(dut.clk)

def flags(dut):
    return (int(dut.inexact.value), int(dut.underflow.value),
            int(dut.overflow.value), int(dut.invalid.value))
