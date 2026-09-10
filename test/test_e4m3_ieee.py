## E4M3 IEEE

import cocotb
import ml_dtypes
import numpy as np

from test_common import *

EXP, MAN, OCP = 4, 3, 0
CODES = 1 << (EXP + MAN + 1)

# F8[code] is the E4M3 value with that bit pattern
F8 = np.arange(CODES, dtype=np.uint8).view(ml_dtypes.float8_e4m3)

# only does RNE
def mult_ref(a, b):
    """The E4M3 encoding of a * b, rounded to nearest even."""
    with np.errstate(invalid="ignore"):
        y = F8[a:a + 1] * F8[b:b + 1]
    return int(y.view(np.uint8)[0])

@cocotb.test()
async def test_multiply(dut):
    await start(dut)

    a = 0b0_0111_000 # = 1.0
    b = 0b0_0111_000 # = 1.0
    y = 0b0_0111_000 # = 1.0

    await mult(dut, a, b)
    assert dut.y.value == y


@cocotb.test()
async def test_multiply_exhaustive(dut):
    await start(dut)

    for a in range(0, CODES, STRIDE):
        for b in range(0, CODES, STRIDE):
            rne = mult_ref(a, b)
            await mult(dut, a, b)
            got = dut.y.value
            if np.isnan(F8[rne]) and np.isnan(F8[got]):
                # TODO count how many of these there are and assert on that
                continue
            assert got == rne

            # now test the anwer in RTZ mode, we don't need to resubmit inputs
            await change_rmode(dut, RTZ)
            got = dut.y.value
            debug = f"a={F8[a]}, b={F8[b]}, expect={F8[rne]}, got={F8[got]}"

            # TODO count how many of these there are and assert on that
            assert got == rne or got == rne - 1, debug
            # print(debug)

# TODO accumulation
# TODO error flags
# TODO ODP and other 8 / 16 bit modes

# Local Variables:
# compile-command: "cd .. ; make test_e4m3_ieee"
# End:
