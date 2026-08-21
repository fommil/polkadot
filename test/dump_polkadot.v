`timescale 1ns / 1ps

module dump();
   initial begin
      if ($test$plusargs("dump")) begin
         $dumpfile("polkadot.vcd");
         $dumpvars(0, polkadot);
      end
      #1;
   end
endmodule
