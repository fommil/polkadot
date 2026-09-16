## E4M3 IEEE

import cocotb
import ml_dtypes
import numpy as np

from test_common import *

EXP, MAN, OCP = 4, 3, 0
CODES = 1 << (EXP + MAN + 1)

ZERO = 0b0_0000_000 # +0.0
ONE  = 0b0_0111_000 # +1.0
MIN  = 0b0_0000_001 # +0.001953125
MAX  = 0b0_1110_111 # +240
INF  = 0b0_1111_000 # +Inf
NAN  = 0b0_1111_100 # the canonical NaN that we emit

NEG  = 0b1_0000_000 # negate any other value

# F8[code] is the E4M3 value with that bit pattern
F8 = np.arange(CODES, dtype=np.uint8).view(ml_dtypes.float8_e4m3)

# only does RNE
def mult_ref(a, b):
    with np.errstate(invalid="ignore"):
        y = F8[a:a + 1] * F8[b:b + 1]
    return int(y.view(np.uint8)[0])

def add_ref(a, b):
    with np.errstate(invalid="ignore"):
        y = F8[a:a + 1] + F8[b:b + 1]
    return int(y.view(np.uint8)[0])

def isnan(a):
    return np.isnan(F8[a])

# obligatory trivial test
@cocotb.test()
async def test_multiply(dut):
    await start(dut)

    await mult(dut, ONE, ONE)
    assert dut.y.value == ONE
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

@cocotb.test()
async def test_multiply_exhaustive(dut):
    await start(dut)

    total = 0
    total_comparisons = 0
    rtz_fallbacks = 0

    for a in range(0, CODES, STRIDE):
        for b in range(0, CODES, STRIDE):
            total += 1
            rne = mult_ref(a, b)
            await mult(dut, a, b)
            got = dut.y.value
            if isnan(rne) and isnan(got):
                continue
            assert got == rne

            # now test the anwer in RTZ mode, we don't need to resubmit inputs
            await change_rmode(dut, RTZ)
            got = dut.y.value
            debug = f"a={F8[a]}, b={F8[b]}, expect={F8[rne]}, got={F8[got]}"

            total_comparisons += 1
            if got == rne - 1:
                rtz_fallbacks += 1
                continue

            assert got == rne, debug
            # print(debug)

    # just to make sure we didn't have all nans or something
    assert total_comparisons > 0
    assert total_comparisons < total
    assert rtz_fallbacks > 0
    assert rtz_fallbacks < total_comparisons / 2

# we have no ground truth for arbitrary length accumulations,
# but we can test (exhaustively) a single add.
@cocotb.test()
async def test_add_exhaustive(dut):
    await start(dut)

    total = 0
    total_comparisons = 0
    rtz_fallbacks = 0

    for a in range(0, CODES, STRIDE):
        for b in range(0, CODES, STRIDE):
            total += 1
            rne = add_ref(a, b)
            await add(dut, ONE, a, b)
            got = dut.y.value
            if isnan(rne) and isnan(got):
                continue
            assert got == rne

            # now test the anwer in RTZ mode, we don't need to resubmit inputs
            await change_rmode(dut, RTZ)
            got = dut.y.value
            debug = f"a={F8[a]}, b={F8[b]}, expect={F8[rne]}, got={F8[got]}"

            total_comparisons += 1
            if got == rne - 1:
                rtz_fallbacks += 1
                continue

            assert got == rne, debug
            # print(debug)

    assert total_comparisons > 0
    assert total_comparisons < total
    assert rtz_fallbacks > 0
    assert rtz_fallbacks < total_comparisons / 2

# we can't test exhaustively because we can have infinite inputs, so we have to
# test predefined vignettes.
@cocotb.test()
async def test_dotproduct(dut):
    await start(dut)

    # if we sum up (1 << 9) times MIN * MIN we get back to MIN
    # (this would have been truncated to zero by a non-exact dot)
    for a in range(0, 1 << 9):
        await mult(dut, MIN, MIN, clear = a == 0)
    got = dut.y.value
    expect = MIN
    debug = f"expect={F8[expect]}, got={F8[got]}"
    assert got == expect, debug
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

    # similarly, for negatives
    for a in range(0, 1 << 9):
        await mult(dut, MIN | NEG, MIN, clear = a == 0)
    got = dut.y.value
    expect = (MIN | NEG)
    debug = f"expect={F8[expect]}, got={F8[got]}"
    assert got == expect, debug
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

    # lots of dynamic range
    # also testing with dot, so no intermediate valid=0 states
    terms = [(MAX, MAX)] + [(MIN, MIN)] * (1 << 9) + [(MAX | NEG, MAX)]
    await dot(dut, terms)
    assert dut.y.value == MIN, F8[dut.y.value]
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

@cocotb.test()
async def test_flags_inexact(dut):
    await start(dut)

    # 1 + 2**-18 needs 19 bits of significand, so the exact sum is not
    # representable, but it is neither tiny nor huge
    await mult(dut, ONE, ONE)
    await mult(dut, MIN, MIN, clear = 0)
    assert dut.y.value == ONE
    assert flags(dut) == (1, 0, 0, 0), flags(dut)

    # the discarded part is below the tie, so RTZ agrees
    await change_rmode(dut, RTZ)
    assert dut.y.value == ONE
    assert flags(dut) == (1, 0, 0, 0), flags(dut)

@cocotb.test()
async def test_flags_underflow(dut):
    await start(dut)

    # a single MIN * MIN is 2**-18, which rounds away to zero
    await mult(dut, MIN, MIN)
    assert dut.y.value == ZERO
    assert flags(dut) == (1, 1, 0, 0), flags(dut)

    # half way to MIN is a tie, RNE keeps the even (zero) result
    for a in range(0, 1 << 8):
        await mult(dut, MIN, MIN, clear = a == 0)
    assert dut.y.value == ZERO
    assert flags(dut) == (1, 1, 0, 0), flags(dut)

@cocotb.test()
async def test_flags_overflow(dut):
    await start(dut)

    # no hope of being represented
    await mult(dut, MAX, MAX)
    assert dut.y.value == INF
    assert flags(dut) == (1, 0, 1, 0), flags(dut)

    # RTZ can never round away from zero, so it saturates instead
    await change_rmode(dut, RTZ)
    assert dut.y.value == MAX
    assert flags(dut) == (1, 0, 1, 0), flags(dut)

    # the sign survives
    await mult(dut, MAX | NEG, MAX)
    assert dut.y.value == (INF | NEG)
    assert flags(dut) == (1, 0, 1, 0), flags(dut)

@cocotb.test()
async def test_flags_invalid(dut):
    await start(dut)

    # an incoming NaN is propagated, not introduced
    await mult(dut, NAN, ONE)
    assert isnan(dut.y.value)
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

    # an Inf is not a loss of information either
    await mult(dut, INF, ONE)
    assert dut.y.value == INF
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

    # Inf * 0 introduces a NaN
    await mult(dut, INF, ZERO)
    assert isnan(dut.y.value)
    assert flags(dut) == (0, 0, 0, 1), flags(dut)

    # and so does Inf + -Inf
    await mult(dut, INF, ONE)
    await mult(dut, INF | NEG, ONE, clear = 0)
    assert isnan(dut.y.value)
    assert flags(dut) == (0, 0, 0, 1), flags(dut)

@cocotb.test()
async def test_guard(dut):
    await start(dut)

    # when the unit is built with (at least one) guard bit, we can temporarily
    # overflow and come back without triggering any flags. five MAX * MAX terms
    # wrap a GUARD=0 accumulator (which fits 4) but not a GUARD=1 one (9), so
    # this is the one test whose answer depends on the parameter
    await dot(dut, [(MAX, MAX)] * 5 + [(MAX, MAX | NEG)] * 5)

    if GUARD:
        assert dut.y.value == ZERO, F8[dut.y.value]
        assert flags(dut) == (0, 0, 0, 0), flags(dut)
    else:
        assert dut.y.value == INF, F8[dut.y.value]
        assert flags(dut) == (1, 0, 1, 0), flags(dut)

## REMAINING TESTS ARE WEIRD OR VERBOSE CORNER CASES

# the accumulator is ACC_W bits (1 sign + magnitude) and MAX * MAX is the
# largest possible term, so this many such terms fit before the sum wraps past
# the sign bit (4 for GUARD=0, 9 for GUARD=1)
ACC_W = 1 + GUARD + 2 * (MAN + 1) + ((1 << (EXP + 1)) - 4)
MAXSQ = ((1 << (MAN + 1)) - 1) ** 2 << (2 * ((1 << EXP) - 3))
ACC_FITS = ((1 << (ACC_W - 1)) - 1) // MAXSQ

@cocotb.test()
async def test_flags_acc_sticky(dut):
    await start(dut)

    # no wrap: the sum cancels exactly and nothing is flagged
    for a in range(0, ACC_FITS):
        await mult(dut, MAX, MAX, clear = a == 0)
    for a in range(0, ACC_FITS):
        await mult(dut, MAX | NEG, MAX, clear = 0)
    assert dut.y.value == ZERO
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

    # one term more and two's complement still brings the accumulator back to
    # zero, but the wrap is not forgiven: we must not claim an exact zero
    for a in range(0, ACC_FITS + 1):
        await mult(dut, MAX, MAX, clear = a == 0)
    for a in range(0, ACC_FITS + 1):
        await mult(dut, MAX | NEG, MAX, clear = 0)
    assert dut.y.value == INF
    assert flags(dut) == (1, 0, 1, 0), flags(dut)

@cocotb.test()
async def test_sticky_clear(dut):
    await start(dut)

    # each of these poisons a different sticky bit, and a new dot product
    # (clear) must not inherit any of them
    for a, b in [(NAN, ONE), (INF, ONE), (INF | NEG, ONE), (INF, ZERO),
                 (ZERO | NEG, ONE), (MAX, MAX)]:
        await mult(dut, a, b)
        await mult(dut, ONE, ONE)
        assert dut.y.value == ONE, (bin(a), bin(b))
        assert flags(dut) == (0, 0, 0, 0), (bin(a), bin(b), flags(dut))

    # and the same for a wrapped accumulator
    for a in range(0, ACC_FITS + 1):
        await mult(dut, MAX, MAX, clear = a == 0)
    await mult(dut, ONE, ONE)
    assert dut.y.value == ONE
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

@cocotb.test()
async def test_clear_needs_valid(dut):
    await start(dut)

    # stop half way to MIN, where any stray term or clear is observable
    for a in range(0, 1 << 8):
        await mult(dut, MIN, MIN, clear = a == 0)

    # clear is only accepted with valid, and inputs are ignored without it
    dut.clear.value = 1
    dut.a.value = MAX
    dut.b.value = MAX
    await RisingEdge(dut.clk)
    dut.clear.value = 0
    await RisingEdge(dut.clk)

    for a in range(0, 1 << 8):
        await mult(dut, MIN, MIN, clear = 0)
    assert dut.y.value == MIN, F8[dut.y.value]
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

@cocotb.test()
async def test_signed_zero(dut):
    await start(dut)

    # -0 + -0 = -0, so an all negative zero accumulation keeps the sign
    await mult(dut, ZERO | NEG, ONE)
    assert dut.y.value == (ZERO | NEG)
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

    # but the sign rule makes -0 * -0 positive
    await mult(dut, ZERO | NEG, ZERO | NEG)
    assert dut.y.value == ZERO

    # and a single +0 term is enough to lose the sign
    await mult(dut, ZERO | NEG, ONE)
    await mult(dut, ZERO, ONE, clear = 0)
    assert dut.y.value == ZERO

    # exact cancellation of non-zero terms is +0 (RDN would want -0)
    await mult(dut, ONE, ONE)
    await mult(dut, ONE | NEG, ONE, clear = 0)
    assert dut.y.value == ZERO
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

@cocotb.test()
async def test_nan_canonical(dut):
    await start(dut)

    # the sign is cleared and the payload is discarded
    for nan in [NAN, NAN | NEG, 0b0_1111_011, 0b1_1111_111]:
        await mult(dut, nan, ONE)
        assert isnan(dut.y.value), bin(nan)
        assert flags(dut) == (0, 0, 0, 0), (bin(nan), flags(dut))

@cocotb.test()
async def test_inf_sign(dut):
    await start(dut)

    await mult(dut, INF | NEG, ONE | NEG)
    assert dut.y.value == INF

    await mult(dut, INF | NEG, ONE)
    assert dut.y.value == (INF | NEG)

    # an Inf swallows a finite overflow, and its flags with it
    await mult(dut, INF, ONE)
    await mult(dut, MAX, MAX, clear = 0)
    assert dut.y.value == INF
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

    # a propagated NaN dominates an Inf, and is still not invalid
    await mult(dut, NAN, ONE)
    await mult(dut, INF, ONE, clear = 0)
    assert isnan(dut.y.value)
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

    # Inf * 0 is invalid even when a NaN is already sticky
    await mult(dut, NAN, ONE)
    await mult(dut, INF, ZERO, clear = 0)
    assert isnan(dut.y.value)
    assert flags(dut) == (0, 0, 0, 1), flags(dut)

@cocotb.test()
async def test_rmode_reread(dut):
    await start(dut)

    # rounding the same accumulation twice must not disturb it
    await mult(dut, MAX, MAX)
    assert dut.y.value == INF
    await change_rmode(dut, RTZ)
    assert dut.y.value == MAX
    await change_rmode(dut, RNE)
    assert dut.y.value == INF
    assert flags(dut) == (1, 0, 1, 0), flags(dut)

    # ... nor may it disturb an accumulation that is still in progress
    for a in range(0, 1 << 8):
        await mult(dut, MIN, MIN, clear = a == 0)
    await change_rmode(dut, RTZ)
    await change_rmode(dut, RNE)
    for a in range(0, 1 << 8):
        await mult(dut, MIN, MIN, clear = 0)
    assert dut.y.value == MIN, F8[dut.y.value]
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

# TODO ODP and other 8 / 16 bit modes

# Local Variables:
# compile-command: "cd .. ; STRIDE=1 make test_e4m3_ieee"
# End:
