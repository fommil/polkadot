import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

# rounding modes, must match the localparams in src/polkadot.v
RNE = 0  # nearest, ties to even
RTZ = 1  # toward zero

# STRIDE=n thins out the exhaustive tests
STRIDE = int(os.environ.get("STRIDE", 1))

# accumulator headroom, must match the -Ppolkadot.GUARD given by the Makefile
GUARD = int(os.environ.get("GUARD", 0))

async def start(dut):
    """Start the clock, hold reset for two cycles, release it."""
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())

    dut.rst_n.value = 0
    dut.clear.value = 0
    dut.valid.value = 0
    dut.rmode.value = RNE
    dut.a.value = 0
    dut.b.value = 0
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

    # the environment must agree with the elaborated parameter
    assert dut.GUARD.value == GUARD, (dut.GUARD.value, GUARD)

async def mult(dut, a, b, clear=1, rmode=RNE):
    dut.valid.value = 1
    dut.clear.value = clear
    dut.rmode.value = rmode
    dut.a.value = a
    dut.b.value = b
    await RisingEdge(dut.clk)

    dut.clear.value = 0
    dut.valid.value = 0

    await RisingEdge(dut.clk)
    #await Timer(1, units="ns")

# NEEDS a definition of 1.0
async def add(dut, one, a, b, rmode=RNE):
    dut.valid.value = 1
    dut.clear.value = 1
    dut.rmode.value = rmode
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
async def dot(dut, terms, rmode=RNE):
    dut.rmode.value = rmode
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

def flags(dut):
    return (int(dut.inexact.value), int(dut.underflow.value),
            int(dut.overflow.value), int(dut.invalid.value))
