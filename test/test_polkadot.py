## tests the polkadot module, dynamic on EXP, MAN, FN, GUARD

import cocotb
import ml_dtypes
import numpy as np

import test_common
from test_common import *

# cocotb has no module level fixture, so the constants are derived from the
# dut's parameters on the first test to run, and published as globals.
def setup(dut):
    if "DECODE" in globals():
        return

    EXP, MAN = int(dut.EXP.value), int(dut.MAN.value)
    FN = int(dut.FN.value)

    # the IEEE 754 style dtype for this format, used as the rounding oracle.
    # ml_dtypes has no finite-only variant for every format, so FN is emulated
    # below.
    DTYPES = {
        (3, 4): ml_dtypes.float8_e3m4,
        (4, 3): ml_dtypes.float8_e4m3,
        (5, 2): ml_dtypes.float8_e5m2,
        (5, 10): np.float16,
        (8, 7): ml_dtypes.bfloat16,
    }
    assert (EXP, MAN) in DTYPES, (EXP, MAN)
    DTYPE = DTYPES[(EXP, MAN)]

    BIAS = (1 << (EXP - 1)) - 1

    # The oracle below rounds once, from a float64. A product of two codes is
    # exact in a float64: it needs 2*(MAN+1) significand bits and its exponent
    # stays inside the normal range. A sum of two codes is not exact for the
    # wider formats (bfloat16 would need 262 bits), but rounding it to a float64
    # first is innocuous so long as the float64 has 2*(MAN+1)+1 significand
    # bits, so the answer is still the correctly rounded one. See Figueroa,
    # "When is double rounding innocuous?" (1995) Theorem 1, extended to the
    # subnormal cases by Le Roux, Boldo & Melquiond (2014).
    assert 2 * (MAN + 1) + 1 <= 53, (EXP, MAN)
    assert 2 * (1 - BIAS - MAN) >= -1022, (EXP, MAN)  # no float64 subnormals
    assert 2 * (BIAS + 2) <= 1023, (EXP, MAN)         # no float64 overflow
    TOP = ((1 << EXP) - 1) << MAN  # the all ones exponent

    if FN:
        NAN = TOP | ((1 << MAN) - 1) # the only NaN, and there is no Inf
        MAX = NAN - 1                # largest finite
    else:
        NAN = TOP | (1 << (MAN - 1)) # canonical, see man_nan in polkadot.v
        MAX = TOP - 1                # largest finite, the exponent is reserved

    NEG = 1 << (EXP + MAN)       # negate any other value
    CODES = 1 << (EXP + MAN + 1)
    assert EXP + MAN + 1 <= 16, (EXP, MAN)  # DECODE is a dense table
    RAW = np.uint8 if EXP + MAN + 1 <= 8 else np.uint16  # a code's storage

    # DECODE[code] is the value with that bit pattern, as a float64
    DECODE = np.arange(CODES, dtype=RAW).view(DTYPE).astype(np.float64)

    if FN:
        # the all ones exponent is an ordinary one here, out of range for DTYPE,
        # but each of its values is exactly double the one an exponent below
        for sign in (0, NEG):
            row = sign + TOP + np.arange(1 << MAN)
            DECODE[row] = 2 * DECODE[row - (1 << MAN)]
        DECODE[NAN] = DECODE[NAN | NEG] = np.nan

    # the largest finite that DTYPE itself can hold, which is MAX unless FN
    IEEE_MAX = DECODE[TOP - 1]
    MAX_F = DECODE[MAX]

    # half an ulp above the largest finite is a tie, and the mantissa of MAX is
    # even in both encodings, so RNE only overflows strictly above this
    OVF_MAG = MAX_F + np.float64(2.0) ** (((1 << EXP) - 1) - BIAS - MAN - 1)

    def _round(exact):
        with np.errstate(over="ignore"):
            y = np.array([exact], dtype=np.float64).astype(DTYPE)
            return int(y.view(RAW)[0])

    # only does RNE, with the single rounding argued for above
    def _pack(exact, sat):
        if np.isnan(exact):
            return NAN
        if sat and np.isfinite(exact):
            # saturation is about the rounding: an Inf term still propagates
            exact = np.clip(exact, -MAX_F, MAX_F)
        elif FN and abs(exact) > OVF_MAG:
            return NAN  # not saturating, and no Inf to reach for
        if FN and abs(exact) > IEEE_MAX:
            # DTYPE has no room for the top binade, so round the one below it
            # and push the exponent field back up
            return _round(exact / 2) + (1 << MAN)
        return _round(exact)

    # the code an overflowing magnitude is delivered as, under RNE
    def ovf_ref(sat=0, neg=False):
        if sat:
            return MAX | (NEG if neg else 0)
        if FN:
            return NAN  # canonical, so the sign is lost
        return TOP | (NEG if neg else 0)

    def mult_ref(a, b, sat=0):
        with np.errstate(invalid="ignore"):
            return _pack(np.float64(DECODE[a]) * np.float64(DECODE[b]), sat)

    def add_ref(a, b, sat=0):
        with np.errstate(invalid="ignore"):
            return _pack(np.float64(DECODE[a]) + np.float64(DECODE[b]), sat)

    def isnan(a):
        return np.isnan(DECODE[a])

    # the code for 2**k, normal or subnormal, exact by construction
    def pow2(k):
        assert k >= 1 - BIAS - MAN, k
        if k >= 1 - BIAS:
            return (k + BIAS) << MAN
        return 1 << (k - (1 - BIAS - MAN))

    # MIN * EIGHTH is the sub-ULP term the accumulation tests use: SUBULP_N of
    # them sum to MIN and SUBULP_TIE of them stop exactly on the tie below it.
    # Unlike 1/MIN copies of MIN * MIN this is a constant amount of work, and it
    # is still an exact multiple of MIN * MIN (the accumulator's own ULP).
    assert BIAS + MAN >= 4, (BIAS, MAN)
    EIGHTH = pow2(-3)
    SUBULP_N = 8
    SUBULP_TIE = SUBULP_N // 2

    # the pair sweeps, sampled unless the format is small enough to be exhausted
    SWEEP = sample_codes(EXP, MAN)
    print(f"sweeping {len(SWEEP)} of {CODES} codes, {len(SWEEP) ** 2} pairs")

    globals().update(
        CODES = CODES,
        SWEEP = SWEEP,
        GUARD = int(dut.GUARD.value),
        FN = FN,
        MAN = MAN,
        TOP = TOP,

        EIGHTH = EIGHTH,         # 0.125
        SUBULP_N = SUBULP_N,
        SUBULP_TIE = SUBULP_TIE,

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
        ovf_ref = ovf_ref,
    )

    # FN has no Inf, so INF is deliberately left unbound in that mode
    if not FN:
        globals().update(INF = TOP)

# codes compare exactly, except that any NaN will do for a NaN
def matches(got, expect):
    return int(got) == expect or (isnan(expect) and isnan(got))

# shadows test_common.start, imported by the * above
async def start(dut):
    await test_common.start(dut)
    setup(dut)

# obligatory trivial test
@test()
async def test_multiply(dut):
    await start(dut)

    await mult(dut, ONE, ONE)
    assert dut.y.value == ONE
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

@test()
async def test_multiply_sweep(dut):
    await start(dut)

    total = 0
    total_comparisons = 0
    rtz_fallbacks = 0

    for a in SWEEP:
        for b in SWEEP:
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
    assert rtz_fallbacks > 0, rtz_fallbacks
    assert rtz_fallbacks < total_comparisons / 2

# we have no ground truth for arbitrary length accumulations,
# but we can test a single add over the sweep.
@test()
async def test_add_sweep(dut):
    await start(dut)

    total = 0
    total_comparisons = 0
    rtz_fallbacks = 0

    for a in SWEEP:
        for b in SWEEP:
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

# saturation only changes how an overflow is delivered, so we only pay for the
# pairs where it is observable
@test()
async def test_sat_sweep(dut):
    await start(dut)

    total = 0

    for a in SWEEP:
        for b in SWEEP:
            expect = mult_ref(a, b, sat=1)
            if expect == mult_ref(a, b):
                continue
            total += 1
            await mult(dut, a, b, sat=1)
            got = dut.y.value
            debug = f"a={DECODE[a]}, b={DECODE[b]}, expect={DECODE[expect]}, got={DECODE[got]}"
            assert matches(got, expect), debug

    assert total > 0

# we can't test exhaustively because we can have infinite inputs, so we have to
# test predefined vignettes.
@test()
async def test_dotproduct(dut):
    await start(dut)

    # if we sum up SUBULP_N times MIN * EIGHTH we get back to MIN
    # (this would have been truncated to zero by a non-exact dot)
    for a in range(0, SUBULP_N):
        await mult(dut, MIN, EIGHTH, clear = a == 0)
    got = dut.y.value
    expect = MIN
    debug = f"expect={DECODE[expect]}, got={DECODE[got]}"
    assert got == expect, debug
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

    # similarly, for negatives
    for a in range(0, SUBULP_N):
        await mult(dut, MIN | NEG, EIGHTH, clear = a == 0)
    got = dut.y.value
    expect = (MIN | NEG)
    debug = f"expect={DECODE[expect]}, got={DECODE[got]}"
    assert got == expect, debug
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

    # lots of dynamic range
    # also testing with dot, so no intermediate valid=0 states
    terms = [(MAX, MAX)] + [(MIN, EIGHTH)] * SUBULP_N + [(MAX | NEG, MAX)]
    await dot(dut, terms)
    assert dut.y.value == MIN, DECODE[dut.y.value]
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

@test()
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

@test()
async def test_flags_underflow(dut):
    await start(dut)

    # a single MIN * MIN is below half of MIN, so it rounds away to zero
    await mult(dut, MIN, MIN)
    assert dut.y.value == ZERO
    assert flags(dut) == (1, 1, 0, 0), flags(dut)

    # half way to MIN is a tie, RNE keeps the even (zero) result
    for a in range(0, SUBULP_TIE):
        await mult(dut, MIN, EIGHTH, clear = a == 0)
    assert dut.y.value == ZERO
    assert flags(dut) == (1, 1, 0, 0), flags(dut)

@test()
async def test_flags_overflow(dut):
    await start(dut)

    # no hope of being represented
    await mult(dut, MAX, MAX)
    assert matches(dut.y.value, ovf_ref()), DECODE[dut.y.value]
    assert flags(dut) == (1, 0, 1, 0), flags(dut)

    # saturation is a runtime choice, so the same accumulation delivers both,
    # and it is an overflow either way
    await change_sat(dut, 1)
    assert dut.y.value == MAX, DECODE[dut.y.value]
    assert flags(dut) == (1, 0, 1, 0), flags(dut)
    await change_sat(dut, 0)
    assert matches(dut.y.value, ovf_ref()), DECODE[dut.y.value]

    # RTZ can never round away from zero, so it saturates instead
    await change_rmode(dut, RTZ)
    assert dut.y.value == MAX
    assert flags(dut) == (1, 0, 1, 0), flags(dut)

    # the sign survives, unless the answer is a canonical NaN
    await mult(dut, MAX | NEG, MAX)
    assert matches(dut.y.value, ovf_ref(neg=True)), DECODE[dut.y.value]
    assert flags(dut) == (1, 0, 1, 0), flags(dut)

@test()
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

@test()
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
        assert matches(dut.y.value, ovf_ref()), DECODE[dut.y.value]
        assert flags(dut) == (1, 0, 1, 0), flags(dut)

## REMAINING TESTS ARE WEIRD OR VERBOSE CORNER CASES

@test()
async def test_clear_needs_valid(dut):
    await start(dut)

    # stop half way to MIN, where any stray term or clear is observable
    for a in range(0, SUBULP_TIE):
        await mult(dut, MIN, EIGHTH, clear = a == 0)

    # clear is only accepted with valid, and inputs are ignored without it
    dut.clear.value = 1
    dut.a.value = MAX
    dut.b.value = MAX
    await RisingEdge(dut.clk)
    dut.clear.value = 0
    await RisingEdge(dut.clk)

    for a in range(0, SUBULP_TIE):
        await mult(dut, MIN, EIGHTH, clear = 0)
    assert dut.y.value == MIN, DECODE[dut.y.value]
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

@test()
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

@test()
async def test_nan_canonical(dut):
    await start(dut)

    # constructed, not found by scanning every code: a NaN is the all ones
    # exponent with a non-zero mantissa (and OCP has only the one)
    if "INF" in globals():
        payloads = (range(1, 1 << MAN) if MAN <= 8
                    else [1, 2, (1 << MAN) >> 1, ((1 << MAN) >> 1) | 1,
                          (1 << MAN) - 1])
        nans = [sign | TOP | p for sign in (0, NEG) for p in payloads]
    else:
        nans = [NAN, NAN | NEG]

    for nan in nans:
        assert isnan(nan), bin(nan)
        await mult(dut, nan, ONE)
        assert dut.y.value == NAN or dut.y.value == nan, bin(nan)
        assert flags(dut) == (0, 0, 0, 0), (bin(nan), flags(dut))

@test()
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

@test()
async def test_rmode_reread(dut):
    await start(dut)

    # rounding the same accumulation twice must not disturb it
    await mult(dut, MAX, MAX)
    assert matches(dut.y.value, ovf_ref())
    await change_rmode(dut, RTZ)
    assert dut.y.value == MAX
    await change_rmode(dut, RNE)
    assert matches(dut.y.value, ovf_ref())
    assert flags(dut) == (1, 0, 1, 0), flags(dut)

    # ... nor may it disturb an accumulation that is still in progress
    for a in range(0, SUBULP_TIE):
        await mult(dut, MIN, EIGHTH, clear = a == 0)
    await change_rmode(dut, RTZ)
    await change_rmode(dut, RNE)
    for a in range(0, SUBULP_TIE):
        await mult(dut, MIN, EIGHTH, clear = 0)
    assert dut.y.value == MIN, DECODE[dut.y.value]
    assert flags(dut) == (0, 0, 0, 0), flags(dut)

# Local Variables:
# compile-command: "cd .. ; SWEEP_LIMIT=500 make test"
# End:
