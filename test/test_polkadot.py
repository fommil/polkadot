## tests the polkadot module, dynamic on EXP, MAN, OCP, GUARD

import cocotb
import ml_dtypes
import numpy as np

import test_common
from test_common import *

# FIXME 16 bit modes

# cocotb has no module level fixture, so the constants are derived from the
# dut's parameters on the first test to run, and published as globals.
def setup(dut):
    if "DECODE" in globals():
        return

    EXP, MAN, OCP = int(dut.EXP.value), int(dut.MAN.value), int(dut.OCP.value)

    # the IEEE 754 style dtype for this format, used as the rounding oracle.
    # ml_dtypes has no no-inf variant for every format, so OCP is emulated below.
    DTYPES = {
        (3, 4): ml_dtypes.float8_e3m4,
        (4, 3): ml_dtypes.float8_e4m3,
    }
    assert (EXP, MAN) in DTYPES, (EXP, MAN, OCP)
    DTYPE = DTYPES[(EXP, MAN)]

    # an even stride only samples a subgroup of the mantissa field, and a
    # multiple of (1 << MAN) only samples powers of two, making every product
    # exact and the inexact / rtz coverage checks below vacuous
    assert STRIDE % 2 == 1, STRIDE

    BIAS = (1 << (EXP - 1)) - 1
    TOP = ((1 << EXP) - 1) << MAN  # the all ones exponent

    if OCP:
        NAN = TOP | ((1 << MAN) - 1) # the only NaN, and there is no Inf
        MAX = NAN - 1                # largest finite
    else:
        NAN = TOP | (1 << (MAN - 1)) # canonical, see man_nan in polkadot.v
        MAX = TOP - 1                # largest finite, the exponent is reserved

    NEG = 1 << (EXP + MAN)       # negate any other value
    CODES = 1 << (EXP + MAN + 1)
    RAW = np.uint8 if EXP + MAN + 1 <= 8 else np.uint16  # a code's storage

    # DECODE[code] is the value with that bit pattern, as a float64
    DECODE = np.arange(CODES, dtype=RAW).view(DTYPE).astype(np.float64)

    if OCP:
        # the all ones exponent is an ordinary one here, out of range for DTYPE,
        # but each of its values is exactly double the one an exponent below
        for sign in (0, NEG):
            row = sign + TOP + np.arange(1 << MAN)
            DECODE[row] = 2 * DECODE[row - (1 << MAN)]
        DECODE[NAN] = DECODE[NAN | NEG] = np.nan

    # the largest finite that DTYPE itself can hold, which is MAX unless OCP
    IEEE_MAX = DECODE[TOP - 1]

    # OCP saturates on overflow but ml_dtypes casts out of range values to NaN
    LIMIT = DECODE[MAX] if OCP else np.float64(np.inf)

    def _round(exact):
        with np.errstate(over="ignore"):
            y = np.array([exact], dtype=np.float64).astype(DTYPE)
            return int(y.view(RAW)[0])

    # only does RNE. An exact product or sum of two of these floats is
    # representable in float64, so there is only one rounding.
    def _pack(exact):
        if np.isnan(exact):
            return NAN
        y = np.clip(exact, -LIMIT, LIMIT)
        if OCP and abs(y) > IEEE_MAX:
            return _round(y / 2) + (1 << MAN)
        return _round(y)

    def mult_ref(a, b):
        with np.errstate(invalid="ignore"):
            return _pack(np.float64(DECODE[a]) * np.float64(DECODE[b]))

    def add_ref(a, b):
        with np.errstate(invalid="ignore"):
            return _pack(np.float64(DECODE[a]) + np.float64(DECODE[b]))

    def isnan(a):
        return np.isnan(DECODE[a])

    # MIN * MIN is the smallest term the accumulator can hold, so RECIP_MIN of
    # them sum to MIN, and HALF_MIN of them stop exactly on the tie below it
    RECIP_MIN = int(1 / np.float64(DECODE[1]))
    HALF_MIN = RECIP_MIN // 2

    globals().update(
        CODES = CODES,
        GUARD = int(dut.GUARD.value),

        RECIP_MIN = RECIP_MIN,
        HALF_MIN = HALF_MIN,

        ZERO = 0,                # +0.0
        MIN  = 1,                # smallest subnormal
        ONE  = BIAS << MAN,      # +1.0
        MAX  = MAX,              # largest finite
        NAN  = NAN,              # canonical

        NEG  = NEG,              # negate any other value

        DECODE = DECODE,
        mult_ref = mult_ref,
        add_ref = add_ref,
        isnan = isnan,
    )

    # OCP has no Inf, so INF is deliberately left unbound in that mode
    if not OCP:
        globals().update(INF = TOP)

# shadows test_common.start, imported by the * above
async def start(dut):
    await test_common.start(dut)
    setup(dut)

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
            debug = f"a={DECODE[a]}, b={DECODE[b]}, expect={DECODE[rne]}, got={DECODE[got]}"

            total_comparisons += 1
            if got == rne - 1:
                rtz_fallbacks += 1
                continue

            assert got == rne, debug
            # print(debug)

    # just to make sure we didn't have all nans or something
    assert total_comparisons > 0
    assert rtz_fallbacks > 0, rtz_fallbacks # might fail for some STRIDE values
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
            debug = f"a={DECODE[a]}, b={DECODE[b]}, expect={DECODE[rne]}, got={DECODE[got]}"

            total_comparisons += 1
            if got == rne - 1:
                rtz_fallbacks += 1
                continue

            assert got == rne, debug
            # print(debug)

    assert total_comparisons > 0
    assert rtz_fallbacks > 0
    assert rtz_fallbacks < total_comparisons / 2

# we can't test exhaustively because we can have infinite inputs, so we have to
# test predefined vignettes.
@cocotb.test()
async def test_dotproduct(dut):
    await start(dut)

    # if we sum up RECIP_MIN times MIN * MIN we get back to MIN
    # (this would have been truncated to zero by a non-exact dot)
    for a in range(0, RECIP_MIN):
        await mult(dut, MIN, MIN, clear = a == 0)
    got = dut.y.value
    expect = MIN
    debug = f"expect={DECODE[expect]}, got={DECODE[got]}"
    assert got == expect, debug
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

    # similarly, for negatives
    for a in range(0, RECIP_MIN):
        await mult(dut, MIN | NEG, MIN, clear = a == 0)
    got = dut.y.value
    expect = (MIN | NEG)
    debug = f"expect={DECODE[expect]}, got={DECODE[got]}"
    assert got == expect, debug
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

    # lots of dynamic range
    # also testing with dot, so no intermediate valid=0 states
    terms = [(MAX, MAX)] + [(MIN, MIN)] * RECIP_MIN + [(MAX | NEG, MAX)]
    await dot(dut, terms)
    assert dut.y.value == MIN, DECODE[dut.y.value]
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

@cocotb.test()
async def test_flags_inexact(dut):
    await start(dut)

    # 1 + MIN*MIN needs more significand bits than the format has, so the exact
    # sum is not representable, but it is neither tiny nor huge
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

    # a single MIN * MIN is below half of MIN, so it rounds away to zero
    await mult(dut, MIN, MIN)
    assert dut.y.value == ZERO
    assert flags(dut) == (1, 1, 0, 0), flags(dut)

    # half way to MIN is a tie, RNE keeps the even (zero) result
    for a in range(0, HALF_MIN):
        await mult(dut, MIN, MIN, clear = a == 0)
    assert dut.y.value == ZERO
    assert flags(dut) == (1, 1, 0, 0), flags(dut)

@cocotb.test()
async def test_flags_overflow(dut):
    await start(dut)

    # no hope of being represented
    await mult(dut, MAX, MAX)
    if "INF" in globals():
        assert dut.y.value == INF
    else:
        assert dut.y.value == MAX
    assert flags(dut) == (1, 0, 1, 0), flags(dut)

    # RTZ can never round away from zero, so it saturates instead
    await change_rmode(dut, RTZ)
    assert dut.y.value == MAX
    assert flags(dut) == (1, 0, 1, 0), flags(dut)

    # the sign survives
    await mult(dut, MAX | NEG, MAX)
    if "INF" in globals():
        assert dut.y.value == (INF | NEG)
    else:
        assert dut.y.value == (MAX | NEG)
    assert flags(dut) == (1, 0, 1, 0), flags(dut)

@cocotb.test()
async def test_flags_invalid(dut):
    await start(dut)

    # an incoming NaN is propagated, not introduced
    await mult(dut, NAN, ONE)
    assert isnan(dut.y.value)
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

    if "INF" in globals():
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
    # overflow and come back without triggering any flags. Two MAX * MAX terms
    # wrap a GUARD=0 accumulator but even 1 bit of headroom saves us.
    await dot(dut, [(MAX, MAX)] * 2 + [(MAX, MAX | NEG)] * 2)

    if GUARD > 0:
        assert dut.y.value == ZERO, DECODE[dut.y.value]
        assert flags(dut) == (0, 0, 0, 0), flags(dut)
    else:
        expect = INF if "INF" in globals() else MAX
        assert dut.y.value == expect, DECODE[dut.y.value]
        assert flags(dut) == (1, 0, 1, 0), flags(dut)

## REMAINING TESTS ARE WEIRD OR VERBOSE CORNER CASES

@cocotb.test()
async def test_clear_needs_valid(dut):
    await start(dut)

    # stop half way to MIN, where any stray term or clear is observable
    for a in range(0, HALF_MIN):
        await mult(dut, MIN, MIN, clear = a == 0)

    # clear is only accepted with valid, and inputs are ignored without it
    dut.clear.value = 1
    dut.a.value = MAX
    dut.b.value = MAX
    await RisingEdge(dut.clk)
    dut.clear.value = 0
    await RisingEdge(dut.clk)

    for a in range(0, HALF_MIN):
        await mult(dut, MIN, MIN, clear = 0)
    assert dut.y.value == MIN, DECODE[dut.y.value]
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

    for nan in [code for code in range(CODES) if isnan(code)]:
        await mult(dut, nan, ONE)
        assert dut.y.value == NAN or dut.y.value == nan, bin(nan)
        assert flags(dut) == (0, 0, 0, 0), (bin(nan), flags(dut))

@cocotb.test()
async def test_inf_sign(dut):
    await start(dut)

    if "INF" not in globals():
        return

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
    if "INF" in globals():
        assert dut.y.value == INF
    else:
        assert dut.y.value == MAX
    await change_rmode(dut, RTZ)
    assert dut.y.value == MAX
    await change_rmode(dut, RNE)
    if "INF" in globals():
        assert dut.y.value == INF
    else:
        assert dut.y.value == MAX
    assert flags(dut) == (1, 0, 1, 0), flags(dut)

    # ... nor may it disturb an accumulation that is still in progress
    for a in range(0, HALF_MIN):
        await mult(dut, MIN, MIN, clear = a == 0)
    await change_rmode(dut, RTZ)
    await change_rmode(dut, RNE)
    for a in range(0, HALF_MIN):
        await mult(dut, MIN, MIN, clear = 0)
    assert dut.y.value == MIN, DECODE[dut.y.value]
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

# Local Variables:
# compile-command: "cd .. ; STRIDE=7 make test"
# End:
