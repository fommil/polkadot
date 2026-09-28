/*
 * Copyright (c) 2026 Sam Halliday
 * SPDX-License-Identifier: BSD-2-Clause
 */

`default_nettype none

// TinyTapeout wrapper: polkadot as an E4M3 (OCP FP8, fn) dot-product
// accumulator.
//
// Design note: TT has 8 inputs, 8 outputs and 8 fixed in/outs. polkadot (for
// e4m3) has 16 inputs, 6 control inputs, 16 outputs and 4 error outputs. So
// either we mess with the CLK and allow one-cycle computations using only
// defaults or we split the operation over two cycles. This design chooses the
// latter, since an 8 bit computer would need two cycles anyway to provide both
// input values.
//
// Pins
//   ui_in[7:0]    operand byte (E4M3 or CTRL)
//   uio_in[3:0]   opcode (input)
//   uio_out[7:4]  {strobe, invalid, overflow, inexact} (output)
//   uo_out[7:0]   y = round(sum of a*b so far)
//   uio_oe        8'b1111_0000
//
// Opcodes
//   0000 NOP         hold
//   0001 LOAD_A_CLR  reg_a <= ui_in, accumulator = +0.0 (pending)
//   0010 LOAD_A      reg_a <= ui_in
//   0011 MAC         accumulator += reg_a * ui_in
//   0100 ADD_CLR     accumulator = ui_in * 1.0
//   0101 ADD         accumulator += ui_in * 1.0
//   0110 CTRL        rmode <= ui_in[2:0], sat <= ui_in[3]
//   else reserved
//
// Only reg_a is stored: b comes straight from ui_in on the issuing cycle.
// rmode and sat persist until changed; clear is per-instruction.
//
// uo_out and the flags are one cycle behind the instruction that issued them,
// and strobe marks the cycles on which they may have changed.
//
//   LOAD_A_CLR a  =>  ?          (no strobe)
//          MAC b  =>  a*b
//       LOAD_A c  =>  a*b        (no strobe)
//          MAC d  =>  a*b + c*d
//          CTRL   =>  a*b + c*d
//          ADD e  =>  a*b + c*d + e
//
// rmode or sat may be provided retrospectively to change the rounding mode
// after an accumulation has happened and the CTRL state will persist for future
// accumulations. The default is RNE with no saturation 4'b0000.
//
// underflow is not pinned out, reconstruct it as
//
//   underflow == inexact && !overflow && (uo_out[EXP+MAN-1:MAN] == 0)
//
// Pending clear survives `NOP`, `LOAD_A` and `CTRL`; it is consumed by the next
// `MAC`/`ADD`. Implying `LOAD_A_CLR a; ADD e` behave like `ADD_CLR e` (`a` is
// discarded). Two `_CLR` in a row discards the first.
/* verilator lint_off DECLFILENAME */
module tt_um_fommil_polkadot_E4M3
  (
   input wire [7:0]  ui_in,   // Dedicated inputs
   output wire [7:0] uo_out,  // Dedicated outputs
   input wire [7:0]  uio_in,  // IOs: Input path
   output wire [7:0] uio_out, // IOs: Output path
   output wire [7:0] uio_oe,  // IOs: Enable path (active high: 0=input, 1=output)
   input wire        ena,     // always 1 when the design is powered, so you can ignore it
   input wire        clk,     // clock
   input wire        rst_n    // reset_n - low to reset
   );

   // Floor-utilisation experiment: bf16 core behind the E4M3 pins.
   localparam integer     EXP  = 8;
   localparam integer     MAN  = 7;
   localparam [EXP-1:0]   BIAS = (1 << (EXP - 1)) - 1;

   localparam [EXP+MAN:0] ONE  = {1'b0, BIAS, {MAN{1'b0}}};

   /* verilator lint_off UNUSEDPARAM */
   localparam [3:0]       OP_NOP        = 4'b0000;
   /* verilator lint_on UNUSEDPARAM */
   localparam [3:0]       OP_LOAD_A_CLR = 4'b0001;
   localparam [3:0]       OP_LOAD_A     = 4'b0010;
   localparam [3:0]       OP_MAC        = 4'b0011;
   localparam [3:0]       OP_ADD_CLR    = 4'b0100;
   localparam [3:0]       OP_ADD        = 4'b0101;
   localparam [3:0]       OP_CTRL       = 4'b0110;

   wire [3:0]             op = uio_in[3:0];

   wire                   valid = (op == OP_MAC) | (op == OP_ADD) | (op == OP_ADD_CLR);

   reg [7:0]              reg_a;
   reg                    clr_pending;
   reg [2:0]              rmode;
   reg                    sat;
   reg                    strobe;

   wire                   clear = clr_pending | (op == OP_ADD_CLR);

   // E4M3 -> bf16: exponent + 120 (subnormals flushed). The low mantissa is
   // filled with noise, not zeros, so synthesis can't shrink the multiplier.
   function [15:0] widen(input [7:0] f);
      widen = {f[7], |f[6:3] ? {f[6], {4{~f[6]}}, f[5:3]} : 8'b0, f[2:0], f[3:0]};
   endfunction

   wire [EXP+MAN:0]       a = ((op == OP_ADD) | (op == OP_ADD_CLR)) ? ONE : widen(reg_a);
   wire [EXP+MAN:0]       b = widen(ui_in);
   wire [EXP+MAN:0]       y;
   wire                   y_zero = ~|y[14:7];

   always @(posedge clk) begin
      if (!rst_n) begin
         reg_a       <= 8'b0000_0000;
         clr_pending <= 1'b0;
         rmode       <= 3'b000; // RNE
         sat         <= 1'b0;
         strobe      <= 1'b0;
      end else begin
         if ((op == OP_LOAD_A) | (op == OP_LOAD_A_CLR))
           reg_a <= ui_in;

         if (op == OP_LOAD_A_CLR)
           clr_pending <= 1'b1;
         else if (valid)
           clr_pending <= 1'b0;

         if (op == OP_CTRL) begin
            rmode <= ui_in[2:0];
            sat   <= ui_in[3];
         end

         strobe <= valid | (op == OP_CTRL);
      end
   end

   wire inexact, underflow, overflow, invalid;

   polkadot #(.EXP(EXP), .MAN(MAN), .GUARD(0), .FN(0)) dut
     (
      .clk(clk),
      .rst_n(rst_n),
      .clear(clear),
      .valid(valid),
      .rmode(rmode),
      .sat(sat),
      .a(a),
      .b(b),
      .y(y),
      .inexact(inexact),
      .underflow(underflow),
      .overflow(overflow),
      .invalid(invalid)
      );

   // bf16 -> E4M3: exponent - 120 is {~e8[3], e8[2:0]}.
   assign uo_out  = {y[15], y_zero ? 4'b0000 : {~y[10], y[9:7]}, y[6:4]};
   // Unused bf16 bits go to the disabled pins so the logic isn't optimised away.
   assign uio_out = {strobe, invalid, overflow, inexact, y[3:0] ^ y[14:11]};
   assign uio_oe  = 8'b1111_0000;

   wire _unused = &{ena, underflow, uio_in[7:4], 1'b0};

endmodule

`default_nettype wire

// Local Variables:
// compile-command: "cd .. && make compile"
// End:
