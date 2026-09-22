`timescale 1ns / 1ps

module dump_project();
   initial begin
      if ($test$plusargs("dump")) begin
         $dumpfile("project.vcd");
         $dumpvars(0, tt_um_fommil_polkadot_E4M3);
      end
      #1;
   end
endmodule
