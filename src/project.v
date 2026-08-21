/*
 * Copyright (c) 2026 Sam Halliday
 * SPDX-License-Identifier: BSD-2-Clause
 */

`default_nettype none

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

   // FIXME
   // Every cycle takes 16 bits (two 8 bit floating point numbers), and outputs
   // 8 bits (floating point number). But I need a signal to indicate that I
   // want to reset the internal state. And I want the option in the future to
   // be able to specify a rounding mode. I'm struggling to see how to do that
   // in a single cycle.

   // If the caller can write to uio_oe I could use that for the reset and
   // rounding modes, and potentially even provide useful debugging feedback on
   // the uio_out in response.

   // All output pins must be assigned. If not used, assign to 0.
   assign uo_out  = ui_in + uio_in;  // Example: ou_out is the sum of ui_in and uio_in
   assign uio_out = 0;
   assign uio_oe  = 0;

   // List all unused inputs to prevent warnings
   wire _unused = &{ena, clk, rst_n, 1'b0};

endmodule

`default_nettype wire
