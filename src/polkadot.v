/*
 * Copyright (c) 2026 Sam Halliday
 * SPDX-License-Identifier: BSD-2-Clause
 */

`default_nettype none

// Exact dot-product accumulator, that uses fixed precision accumulation with a
// single rounding at the end. Multiple rounding modes may be computed on the
// same acculation (no need to provide the terms again) by setting rmode (even
// with an unset valid flag).
//
// Specials are selected by OCP:
//   OCP=0  IEEE 754 style. An all-ones exponent is reserved: mantissa zero is
//          Inf, anything else is NaN. Largest finite biased exponent is
//          2**EXP-2, and overflow gives +/-Inf.
//   OCP=1  OCP FP8 E4M3 style. Only S.1..1.1..1 is NaN and there are no
//          infinities, so the all-ones exponent is an ordinary one and the
//          largest finite value has mantissa all-ones-minus-one (448 for
//          E4M3). Overflow saturates to that value instead.
//
// A note on power consumption: it is recommended that callers hold a and b
// steady even when valid is not set high, avoiding unnecessary work being
// performed.
//
// The clear pin is only accepted with the valid input set, indicating that this
// input starts a new dot product.
//
// We always emit a canonical NaN with a zero sign and throw away the payload of
// any incoming NaN. This is allowed, since IEEE 754-2019 6.3 leaves the sign of
// a NaN unspecified, 6.2 only recommends keeping the payload.
module polkadot
  #(
    parameter integer EXP = 4,   // exponent field bits
    parameter integer MAN = 3,   // significand bits, not including implicit bit
    parameter integer GUARD = 0, // headroom: 2**GUARD worst-case terms
    parameter integer OCP = 0    // 0: IEEE-style Inf/NaN, 1: OCP FP8 style
    )
   (
    input wire              clk,
    input wire              rst_n,     // active low, synchronous
    input wire              clear,     // start a new accumulation (first cycle)
    input wire              valid,     // accumulate a*b this cycle
    input wire [2:0]        rmode,     // rounding mode, see RNE/RTZ below
    input wire [EXP+MAN:0]  a,         // width = EXP + MAN + 1 (for sign)
    input wire [EXP+MAN:0]  b,
    output wire [EXP+MAN:0] y,         // round(sum of all a*b so far)
    // debugging
    output wire             inexact,   // y differs from the exact sum
    output wire             underflow, // y is tiny and inexact
    output wire             overflow,  // exact sum is too large to represent
    output wire             invalid    // a NaN was introduced, not propagated
    );

   initial begin
      if (MAN < 1) $fatal(1, "polkadot: MAN must be >= 1, got %0d", MAN);
      if (EXP < 2) $fatal(1, "polkadot: EXP must be >= 2, got %0d", EXP);
      if (GUARD < 0) $fatal(1, "polkadot: GUARD must be >= 0, got %0d", GUARD);
   end

   // Rounding modes
   //
   // Giving this option is standard but it's quite costly to allow it to be
   // swapped at runtime. If a caller has need of a particular fixed rounding
   // mode, it can be best to bake it in at compile time, especially RTZ which
   // is the simplest of all the rounding modes.
   localparam RNE = 0; // nearest, ties to even
   localparam RTZ = 1; // toward zero, i.e. plain truncation
   // TODO RDN (010, toward -Inf)
   // TODO RUP (011, toward +Inf)
   // TODO RMM (100, nearest ties away from zero)

   // An exp value of 0 indicates a subnormal; the implicit leading bit is 0.
   // Its (unsigned) fixed point value is `mantissa * 2^(1-BIAS-MAN)`, with the
   // smallest value being 2^(1-BIAS-MAN).
   //
   // A normal float has a non-zero exp and has an implicit leading bit of 1.
   // sig here means the mantissa with the leading 1. The (unsigned) fixed point
   // value is then shifted by sig << exp-1, i.e. the significand shifted
   // towards the MSB by its exponent.
   localparam integer SIG  = MAN + 1; // significand width with implicit bit
   localparam integer BIAS = (1 << (EXP - 1)) - 1; // exp corresponding to unity 2^0

   // Multiplying two arbitrary precision numbers involves multiplying their
   // integer portion and summing their exponential parts. Dot Product involves
   // accumulating many such calculations.
   //
   // Floating point representations typically require branches to handle
   // mismatched scales when accumulation, see Chapter 7.3 of Muller, and more
   // advanced approaches use caching layers and/or MSB/LSB limits to reduce the
   // size of the accumulator for high precisions.
   //
   // Our simplifying design choice is to accumulate exact fixed point values,
   // and pay the full cost of the accumulator register and everything that
   // comes with it.
   //
   // The width of the integer part is 2 * SIG significand bits (multiplied
   // integer part), and the shift is a sum of each individual shift (i.e. 0 for
   // subnormal, and exp-1 for normal), i.e. 2^EXP - 2 per incoming value.
   // Summing gives 2 * (2^EXP - 2) = 2^(EXP+1) - 4. Note that IEEE reserves
   // an exp value for Inf so treating it this way adds two extra guard bits.
   //
   // We account for SIGN and GUARD bits, giving us a total width of:
   localparam integer ACC_W = 1 + GUARD + (2 * SIG) + ((1 << (EXP + 1)) - 4);
   // and if we want to index into that, we need this many bits
   localparam integer MSB_W = $clog2(ACC_W); // width of index into ACC_W

   // Subnormals have exponent field 0, which decodes to unbiased exponent
   // (1-BIAS). Every float is an integer multiple of the smallest subnormal
   // 2^(-MAN) * 2^(1-BIAS) = 2^(1-BIAS-MAN).
   //
   // POINT is the index of the bit, counting from the LSB, that when set gives
   // us 2^0 = 1.
   localparam integer POINT = BIAS + MAN - 1;
   // ACC_POINT is the same thing but for the accumulator, whose smallest
   // magnitude is the square of the smallest subnormal: 2^(1-BIAS-MAN)^2 =
   // 2^(2-2*BIAS-2*MAN). The bit width is double the width of POINT.
   //
   // Thus, a bit at index i has value 2^(i-ACC_POINT).
   //
   // Given a fixed point value with its msb at index i, we can compute the
   // corresponding floating point (biased) exp value by subtracting ACC_POINT -
   // BIAS.
   localparam integer ACC_POINT = 2 * POINT;

   // The smallest normal float is 2^(1-BIAS), which sits at this accumulator
   // bit index. Anything with its leading 1 below this is a subnormal (or zero)
   // result and uses a fixed slice of the accumulator instead of a shift.
   localparam integer NORM_LSB = ACC_POINT - BIAS + 1;

   // route the parts of the input floats and detect NaN/Inf.
   wire               sign_a = a[EXP+MAN];
   wire [EXP-1:0]     exp_a = a[EXP+MAN-1:MAN];
   wire [MAN-1:0]     man_a = a[MAN-1:0];
   wire               sign_b = b[EXP+MAN];
   wire [EXP-1:0]     exp_b = b[EXP+MAN-1:MAN];
   wire [MAN-1:0]     man_b = b[MAN-1:0];

   // OCP NaN is all ones exponent and all ones mantissa (no Inf).
   // IEEE uses all ones exponent with zero mantissa for Inf and non-zero for NaN.
   //
   // syntax reminder:
   //
   // ~|a = a is all 0
   //  |a = a is non-zero
   //  &a = a is all 1
   wire               a_zero = ~|exp_a && ~|man_a;
   wire               b_zero = ~|exp_b && ~|man_b;
   wire               a_nan = OCP ? (&exp_a && &man_a) : (&exp_a && |man_a);
   wire               b_nan = OCP ? (&exp_b && &man_b) : (&exp_b && |man_b);
   wire               a_inf = OCP ? 1'b0 : (&exp_a && ~|man_a);
   wire               b_inf = OCP ? 1'b0 : (&exp_b && ~|man_b);

   // Expand a*b into its exact fixed-point form.
   //
   // construct the scale shift, being careful not to overflow
   wire [EXP-1:0]     shift_a = ~|exp_a ? 1 : exp_a;
   wire [EXP-1:0]     shift_b = ~|exp_b ? 1 : exp_b;
   wire [EXP:0]       shift = shift_a + shift_b - 2; // single -2, instead of 2x -1
   // add the implicit bit to the significands, and multiply
   wire [MAN:0]       sig_a = {|exp_a, man_a};
   wire [MAN:0]       sig_b = {|exp_b, man_b};
   wire [2*SIG-1:0]   prod = sig_a * sig_b; // costly
   wire [ACC_W-1:0]   prod_acc = prod;   // widen before shifting
   wire [ACC_W-1:0]   mag = prod_acc << shift; // costly
   // set the sign
   wire               sign = sign_a ^ sign_b;
   // term and acc are defined signed so that the sum handles negatives
   wire signed [ACC_W-1:0] term = sign ? -mag : mag;

   // State
   //
   // the dot product accumulator
   reg signed [ACC_W-1:0]  acc;
   // Sticky bits handle special flag pollution. Note that we need to track if
   // everything was negative zero because -0 + -0 = -0.
   reg                     nan_sticky, inf_pos_sticky, inf_neg_sticky, nzero_sticky;
   // Set if a term introduced a NaN of its own, see invalid_in.
   reg                     invalid_sticky;
   // Set if the accumulator ever wrapped past the GUARD bits, see acc_ovf.
   reg                     acc_sticky;

   // Table 7.4 from Muller shows Inf*0 is NaN, anything*NaN is also NaN.
   // Everything else uses a standard sign rule, so -Inf*Inf=-Inf, -0*0=-0.
   //
   // Table 7.2 shows NaN+*=NaN, Inf-Inf=NaN, Inf+else=Inf
   wire                    invalid_in = (a_inf && b_zero) || (b_inf && a_zero);
   wire                    nan_in = a_nan || b_nan || invalid_in;
   wire                    inf_in = !nan_in && (a_inf || b_inf);
   wire                    nzero_in = (a_zero || b_zero) && sign;

   wire signed [ACC_W-1:0] acc_op = clear ? {ACC_W{1'b0}} : acc;
   wire signed [ACC_W-1:0] acc_sum = acc_op + term; // costly

   // detect possible overflows relative to the last state
   wire                    acc_ovf = (acc_op[ACC_W-1] == term[ACC_W-1]) &&
                           (acc_sum[ACC_W-1] != term[ACC_W-1]);

   // update the acc(umulator) and sticky flags on rising edges
   always @(posedge clk) begin
      if (!rst_n) begin
         acc <= 0;
         nan_sticky <= 0;
         inf_pos_sticky <= 0;
         inf_neg_sticky <= 0;
         nzero_sticky <= 0;
         acc_sticky <= 0;
         invalid_sticky <= 0;
      end
      else if (valid) begin
         acc <= acc_sum;
         acc_sticky <= (clear ? 0 : acc_sticky) || acc_ovf;
         nan_sticky <= (clear ? 0 : nan_sticky) || nan_in;
         inf_pos_sticky <= (clear ? 0 : inf_pos_sticky) || (inf_in && !sign);
         inf_neg_sticky <= (clear ? 0 : inf_neg_sticky) || (inf_in && sign);
         nzero_sticky <= (clear ? 1 : nzero_sticky) && nzero_in;
         invalid_sticky <= (clear ? 0 : invalid_sticky) || invalid_in;
      end
   end

   // Output

   wire                    acc_neg = acc[ACC_W-1];
   wire [ACC_W-1:0]        amag = acc_neg ? -acc : acc;

   // find the index of the leading 1.
   //
   // what we are really building here is a big switch/case encoder statement
   // that looks like
   //
   // 1xxxxx => 1
   // 01xxxx => 2
   // 001xxx => 3
   // ...
   //
   // which is then converted into gate logic and synthesized thanks to
   // McCluskey et al.
   reg [MSB_W-1:0]         msb;
   integer                 i;
   always @* begin
      msb = {MSB_W{1'b0}};
      for (i = 0; i < ACC_W; i = i + 1) if (amag[i]) msb = i[MSB_W-1:0];
   end

   // truncate the exact form into MAN width
   //
   // check if normal or subnormal. amag = 0 (i.e. zero) counts as subnormal
   wire                    is_norm = |amag[ACC_W-1:NORM_LSB];
   wire [MAN-1:0]          man_norm = amag[msb-1 -: MAN];
   wire [MAN-1:0]          man_sub = amag[NORM_LSB-1 -: MAN];
   wire [MAN-1:0]          man_y = is_norm ? man_norm : man_sub;

   // A bit at index i has value 2^(i-ACC_POINT), and a normal float with biased
   // exp e has value 2^(e-BIAS), so e = msb - ACC_POINT + BIAS.
   wire [MSB_W-1:0]        exp_norm = msb - (ACC_POINT - BIAS);
   wire [MSB_W-1:0]        exp_y = is_norm ? exp_norm : 0;

   // guard bit is the bit immediately below the truncated slice
   wire [MSB_W-1:0]        guard_idx = is_norm ? msb - MAN - 1 : NORM_LSB - MAN - 1;
   wire [ACC_W-1:0]        lo_mask = (1 << guard_idx) - 1;
   wire                    guard_bit = amag[guard_idx];
   // sticky bit is OR of everything strictly below the guard
   wire                    sticky_bit = |(amag & lo_mask);

   // rounding
   reg                     round_up;
   always @* begin
      case (rmode)
        RNE: round_up = guard_bit && (sticky_bit || man_y[0]);
        RTZ: round_up = 0;
        default: round_up = 0;
      endcase
   end

   // Incrementing the concatenated {exp, man} field, rather than the mantissa
   // alone, gets two carry cases for free: a normal mantissa overflow (1.1..1 +
   // 1ulp = 10.0..0, i.e. exp+1 with mantissa 0) and the subnormal to normal
   // transition (max subnormal + 1ulp = exp 1 with mantissa 0).
   //
   // The exponent is kept at full MSB_W width here (plus a carry bit) so that
   // the overflow test below sees the true value rather than a wrapped one.
   wire [MSB_W+MAN:0]      fields_full = {1'b0, exp_y, man_y} + round_up;
   wire [MSB_W:0]          exp_full = fields_full[MSB_W+MAN:MAN];
   wire [MAN-1:0]          man_full = fields_full[MAN-1:0];

   // overflow is reachable by rounding up from the largest finite value
   localparam integer      EXP_MAX = (1 << EXP) - 1; // all ones exponent field
   // largest finite biased exponent
   localparam integer      EXP_FIN = OCP ? EXP_MAX : EXP_MAX - 1;
   wire                    ovf = OCP
                           ? (exp_full > EXP_MAX || (exp_full == EXP_MAX && &man_full))
                           : (exp_full > EXP_FIN);

   // a wrapped accumulator has lost the true magnitude
   wire                    ovf_any = ovf || acc_sticky;

   // IEEE overflows to Inf, OCP saturates to the largest finite value.
   //
   // RTZ never rounds away from zero, so an overflowing magnitude must be
   // delivered as the largest finite value, not Inf.
   //
   // TODO RDN,RUP requires more overflow handling, RMM=RNE
   wire                    saturate = OCP || rmode == RTZ;
   wire [MAN-1:0]          man_max = OCP ? {MAN{1'b1}} - 1 : {MAN{1'b1}};
   wire [EXP+MAN-1:0]      fields_ovf = saturate ? {EXP_FIN[EXP-1:0], man_max}
                           : {{EXP{1'b1}}, {MAN{1'b0}}};
   wire [EXP+MAN-1:0]      fields_y = ovf_any ? fields_ovf
                           : fields_full[EXP+MAN-1:0];

   // override man_y/exp_y results if a sticky flag was set
   wire                    nan_out = nan_sticky || (inf_pos_sticky && inf_neg_sticky);
   wire                    inf_out = !nan_out && (inf_pos_sticky || inf_neg_sticky);
   wire [MAN-1:0]          man_nan = OCP ? {MAN{1'b1}} : {1'b1, {MAN-1{1'b0}}};

   // TODO exact cancellation should be -0 under RDN
   wire                    sign_y = nzero_sticky || acc_neg;

   // the only loss is the final rounding
   assign inexact = !nan_out && !inf_out && (guard_bit || sticky_bit || ovf_any);

   // only when the result is both tiny and inexact. ovf implies a non-zero
   // exp_full, but a wrapped accumulator can leave any value behind.
   assign underflow = inexact && ~|exp_full && !ovf_any;

   // the rounded exact sum exceeds the largest finite value
   assign overflow = !nan_out && !inf_out && ovf_any;

   // NaN was introduced, not just propagated
   assign invalid = invalid_sticky || (inf_pos_sticky && inf_neg_sticky);

   assign y = nan_out ? {1'b0, {EXP{1'b1}}, man_nan}
              : (inf_out ? {inf_neg_sticky, {EXP{1'b1}}, {MAN{1'b0}}}
                 : {sign_y, fields_y});

endmodule

`default_nettype wire
