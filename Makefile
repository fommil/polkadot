SOURCES = src/polkadot.v src/project.v

export PATH := $(CURDIR)/.venv/bin:$(PATH)

export PYTHONPATH := $(CURDIR)/test
export COCOTB_REDUCED_LOG_FMT=1
export LIBPYTHON_LOC=$(shell cocotb-config --libpython)

# based on https://github.com/mattvenn/rgb_mixer_2025
#
# install deps with:
#
# python3 -m venv .venv
# .venv/bin/pip install 'cocotb<2'
#
# PLUSARGS=+dump for a waveform, NOASSERT=1 to run the stimulus without
# checking, STRIDE=n to thin out the exhaustive tests.

all: compile synth test

test: test_e4m3_ieee

compile: $(SOURCES)
	iverilog -g2012 -s tt_um_fommil_polkadot_E4M3 $(SOURCES)

synth: $(SOURCES)
	yosys -p 'read_verilog -sv $(SOURCES); synth -top tt_um_fommil_polkadot_E4M3; stat'

test_e4m3_ieee:
	rm -rf sim_build/; mkdir sim_build/
	iverilog -o sim_build/sim.vvp -s polkadot -s dump -g2012 \
	  -Ppolkadot.EXP=4 -Ppolkadot.MAN=3 -Ppolkadot.OCP=0 -Ppolkadot.GUARD=0 \
	  src/polkadot.v test/dump_polkadot.v
	MODULE=test_e4m3_ieee vvp -M $$(cocotb-config --prefix)/cocotb/libs -m libcocotbvpi_icarus sim_build/sim.vvp $(PLUSARGS)
	! grep failure results.xml

show_%: %.vcd %.gtkw
	gtkwave $^

clean:
	rm -rf *.vcd *.json results.xml sim_build test/__pycache__

.PHONY: all compile synth test test_e4m3_ieee clean
