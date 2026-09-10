import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

# rounding modes, must match the localparams in src/polkadot.v
RNE = 0  # nearest, ties to even
RTZ = 1  # toward zero

# STRIDE=n thins out the exhaustive tests
STRIDE = int(os.environ.get("STRIDE", 1))

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

async def change_rmode(dut, rmode):
    dut.rmode.value = rmode
    await RisingEdge(dut.clk)
