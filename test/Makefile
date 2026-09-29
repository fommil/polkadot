# Stub for Tiny Tapeout's gl_test action (tt-gds-action/gl_test), which runs
# `make clean` and `GATES=yes make` in this directory after copying the netlist
# to gate_level_netlist.v, then expects results.xml here. The real build is in
# ../Makefile; locally use `make test_gds` from the project root.
#
# deps and test_gds must be separate invocations: ../Makefile evaluates
# cocotb-config at parse time, before .venv exists.

all:
	$(MAKE) -C .. deps
	$(MAKE) -C .. test_gds $(if $(PDK_VERSION),PDK_VERSION=$(PDK_VERSION))
	cp ../results.xml .

clean:
	rm -f results.xml

.PHONY: all clean
