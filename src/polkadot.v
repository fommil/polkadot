/*
 * Copyright (c) 2026 Sam Halliday
 * SPDX-License-Identifier: BSD-2-Clause
 */

`default_nettype none

// Exact fixed-point floating point dot-product accumulator.
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
// ASSUMPTIONS:
//   - Subnormal inputs supported, round to nearest ties to even on output.
//   - Exact cancellation gives +0.
//   - Accumulation is exact, the only rounding in the design is at the output.
module polkadot #(
    parameter integer EXP   = 4,  // exponent field bits
    parameter integer SIG   = 4,  // significand bits, implicit bit INCLUDED
    parameter integer GUARD = 8,  // headroom: 2**GUARD worst-case terms
    parameter integer OCP   = 0   // 0: IEEE-style Inf/NaN, 1: OCP FP8 style
) (
    input  wire                 clk,
    input  wire                 rst_n,  // active low, synchronous
    input  wire                 clear,  // zero the accumulator and sticky flags
    input  wire                 valid,  // accumulate a*b this cycle
    input  wire [EXP+SIG-1:0]   a,
    input  wire [EXP+SIG-1:0]   b,
    output wire [EXP+SIG-1:0]   y       // round(sum of all a*b so far)
);

  localparam integer MAN  = SIG - 1;          // stored mantissa bits
  localparam integer W    = EXP + SIG;        // 1 sign + EXP + MAN
  localparam integer BIAS = (1 << (EXP - 1)) - 1;

  // Fixed point accumulator: INT bits above the point, FRAC below, one sign
  // bit and GUARD bits of headroom. Products range over
  // 2**(2-2*BIAS-2*MAN) .. <2**(2*BIAS+2).
  localparam integer FRAC  = 2 * BIAS + 2 * MAN - 2;
  localparam integer INT   = 2 * BIAS + 2;
  localparam integer ACC_W = 1 + GUARD + INT + FRAC;
  localparam integer MSB_W = $clog2(ACC_W);

  // Right shift that lands the accumulator's subnormal LSB on the output's,
  // i.e. FRAC+1-BIAS-MAN. Always >= 1, so the round bit index never underflows.
  localparam integer SUB_SHIFT = BIAS + MAN - 1;
  // msb below this means the result is subnormal (or zero)
  localparam integer SUB_MSB = MAN + SUB_SHIFT;
  // biased exponent = msb - EOFF for normal results
  localparam integer EOFF = FRAC - BIAS;

  // ---------------------------------------------------------------- decode
  // Split the input words into their raw bit fields, and classify them. No
  // arithmetic interpretation yet.

  wire           sign_a = a[W-1];
  wire [EXP-1:0] exp_a = a[W-2:MAN];
  wire [MAN-1:0] man_a = a[MAN-1:0];
  wire           sign_b = b[W-1];
  wire [EXP-1:0] exp_b = b[W-2:MAN];
  wire [MAN-1:0] man_b = b[MAN-1:0];

  wire a_zero = (exp_a == 0) && (man_a == 0);
  wire b_zero = (exp_b == 0) && (man_b == 0);
  // all-ones exponent code, reserved in IEEE mode but ordinary in OCP mode
  wire a_top = (exp_a == {EXP{1'b1}});
  wire b_top = (exp_b == {EXP{1'b1}});
  wire a_nan = OCP ? (a_top && (man_a == {MAN{1'b1}})) : (a_top && (man_a != 0));
  wire b_nan = OCP ? (b_top && (man_b == {MAN{1'b1}})) : (b_top && (man_b != 0));
  wire a_inf = !OCP && a_top && (man_a == 0);
  wire b_inf = !OCP && b_top && (man_b == 0);
  // not a finite number, so it must bypass the accumulator
  wire a_spec = a_nan || a_inf;
  wire b_spec = b_nan || b_inf;

  // ------------------------------------------------------------ term input
  // Expand a*b into its exact fixed-point form. This is the heart of the
  // design: `mag` (unsigned) / `term` (signed) is the product decoded into the
  // accumulator's fixed-width representation, with no rounding whatsoever.
  //
  // SCALE CONVENTION: an accumulator integer N represents the real value
  // N * 2**-FRAC, i.e. the binary point sits FRAC bits up from the LSB.
  //
  //   a*b   = prod * 2**(ea + eb - 2*BIAS - 2*MAN)
  //   fixed = a*b * 2**FRAC                       (by the convention above)
  //         = prod * 2**(ea + eb - 2)             (since FRAC = 2*BIAS+2*MAN-2)
  //
  // so the entire exponent calculation collapses to a left shift by ea+eb-2,
  // which is 0 when both inputs are subnormal and never goes negative.

  // implicit bit is 0 for subnormals, whose exponent is then treated as 1
  wire [SIG-1:0] sig_a = {exp_a != 0, man_a};
  wire [SIG-1:0] sig_b = {exp_b != 0, man_b};
  wire [EXP:0] ea = (exp_a == 0) ? 1 : {1'b0, exp_a};
  wire [EXP:0] eb = (exp_b == 0) ? 1 : {1'b0, exp_b};

  // significand product, an integer of value prod * 2**(-2*MAN)
  wire [2*SIG-1:0] prod = sig_a * sig_b;
  // where prod lands on the fixed-point number line
  wire [EXP:0] shamt = ea + eb - 2;
  // |a*b| in fixed point, exactly, zero extended to the full accumulator width
  wire [ACC_W-1:0] mag = {{(ACC_W - 2 * SIG) {1'b0}}, prod} << shamt;
  wire term_sign = sign_a ^ sign_b;
  wire signed [ACC_W-1:0] term = term_sign ? -$signed(mag) : $signed(mag);

  // specials never enter the accumulator, they go to the sticky flags
  wire take = valid && !a_spec && !b_spec;

  // the running fixed-point sum, the only real state in the datapath
  reg signed [ACC_W-1:0] acc;
  // clear in the same cycle as valid zeroes the sum but still takes the term
  wire signed [ACC_W-1:0] base = clear ? {ACC_W{1'b0}} : acc;

  always @(posedge clk) begin
    if (!rst_n) acc <= {ACC_W{1'b0}};
    else acc <= base + (take ? term : {ACC_W{1'b0}});
  end

  // --------------------------------------------------------- sticky specials

  // Inf*0 is invalid, Inf*NaN is just NaN
  wire nan_in = valid && (a_nan || b_nan || (a_inf && b_zero) || (b_inf && a_zero));
  wire inf_in = valid && !nan_in && (a_inf || b_inf);

  reg nan_sticky, inf_pos_sticky, inf_neg_sticky;

  always @(posedge clk) begin
    if (!rst_n) begin
      nan_sticky <= 1'b0;
      inf_pos_sticky <= 1'b0;
      inf_neg_sticky <= 1'b0;
    end else begin
      nan_sticky <= (clear ? 1'b0 : nan_sticky) | nan_in;
      inf_pos_sticky <= (clear ? 1'b0 : inf_pos_sticky) | (inf_in && !term_sign);
      inf_neg_sticky <= (clear ? 1'b0 : inf_neg_sticky) | (inf_in && term_sign);
    end
  end

  // ------------------------------------------------------- normalise + round
  // Encode the fixed-point sum back into a float: find its magnitude's leading
  // one to get the exponent, shift the significand down into SIG bits, and
  // round to nearest, ties to even. This is the only rounding in the design.

  // sign/magnitude split, so the shifts below are unsigned
  wire acc_neg = acc[ACC_W-1];
  wire [ACC_W-1:0] amag = acc_neg ? -acc : acc;

  // leading one detect, 0 when amag is zero (which then rounds to +0)
  reg [MSB_W-1:0] msb;
  integer i;
  always @* begin
    msb = {MSB_W{1'b0}};
    for (i = 1; i < ACC_W; i = i + 1) if (amag[i]) msb = i[MSB_W-1:0];
  end

  wire subnorm = (msb < SUB_MSB);
  // how far to shift amag right so its top bit lands at bit MAN of sig_pre;
  // subnormal results instead use a fixed shift, so they lose precision
  wire [MSB_W-1:0] s = subnorm ? SUB_SHIFT[MSB_W-1:0] : (msb - MAN);

  // the candidate significand, before rounding
  wire [ACC_W-1:0] shifted = amag >> s;
  wire [SIG-1:0] sig_pre = shifted[SIG-1:0];

  // the discarded bits: amag[s-1] decides the tie, amag[s-2:0] breaks it

  wire [ACC_W-1:0] sticky_mask = ({{(ACC_W - 1) {1'b0}}, 1'b1} << (s - 1)) - 1'b1;
  wire round_bit = amag[s-1];
  wire sticky_bit = |(amag & sticky_mask);
  wire round_up = round_bit && (sticky_bit || sig_pre[0]);

  wire [SIG:0] sig_rnd = sig_pre + round_up;

  // carry out of the significand bumps the exponent, and mantissa is then zero
  wire [MSB_W:0] exp_out = subnorm ? {{MSB_W{1'b0}}, sig_rnd[MAN]}
                                  : ({1'b0, msb} - EOFF) + sig_rnd[SIG];
  // Overflow is anything the format cannot encode: past the top exponent, or
  // (OCP only) landing exactly on the single NaN code.
  wire nan_code = OCP && (exp_out == EXP_TOP) && (sig_rnd[MAN-1:0] == {MAN{1'b1}});
  wire ovf = (exp_out > EXP_TOP) || nan_code;

  // ------------------------------------------------------------------ output
  // Priority: NaN beats Inf beats overflow beats the rounded value. The sign
  // of an exactly zero result is forced positive.

  wire nan_out = nan_sticky || (inf_pos_sticky && inf_neg_sticky);
  wire inf_out = !nan_out && (inf_pos_sticky || inf_neg_sticky);

  // the canonical NaN, and what overflow produces
  wire [W-1:0] nan_val = OCP ? {1'b0, {EXP{1'b1}}, {MAN{1'b1}}}
                             : {1'b0, {EXP{1'b1}}, 1'b1, {(MAN - 1) {1'b0}}};
  // no Inf in OCP mode, so overflow saturates to the largest finite value
  wire [W-1:0] ovf_val = OCP ? {acc_neg, {EXP{1'b1}}, {(MAN - 1) {1'b1}}, 1'b0}
                             : {acc_neg, {EXP{1'b1}}, {MAN{1'b0}}};

  assign y = nan_out ? nan_val
           : inf_out ? {inf_neg_sticky, {EXP{1'b1}}, {MAN{1'b0}}}
           : ovf     ? ovf_val
           : {acc_neg && (|amag), exp_out[EXP-1:0], sig_rnd[MAN-1:0]};

endmodule
