## IEEE style E4M3

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer

RNE = 0
RTZ = 1

@cocotb.test()
async def test_multiply(dut):
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

    # 1.0 * 1.0 in E4M3: sign 0, exp 0b0111 (bias 7), man 0b000
    dut.a.value = 0b0_0111_000
    dut.b.value = 0b0_0111_000
    dut.clear.value = 1
    dut.valid.value = 1
    await RisingEdge(dut.clk)
    dut.clear.value = 0
    dut.valid.value = 0

    # y is combinational from acc, settle before sampling
    await Timer(1, units="ns")

    dut._log.info(
        "a=%s b=%s y=%s inexact=%s underflow=%s overflow=%s invalid=%s",
        dut.a.value,
        dut.b.value,
        dut.y.value,
        dut.inexact.value,
        dut.underflow.value,
        dut.overflow.value,
        dut.invalid.value,
    )

# Local Variables:
# compile-command: "cd .. ; make test_e4m3_ieee"
# End:
