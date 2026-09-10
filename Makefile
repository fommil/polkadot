SOURCES = $(wildcard src/*.v)

export PATH := $(CURDIR)/.venv/bin:$(PATH)

export PYTHONPATH := $(CURDIR)/test
export COCOTB_REDUCED_LOG_FMT=1
export LIBPYTHON_LOC=$(shell cocotb-config --libpython)

# based on https://github.com/mattvenn/rgb_mixer_2025
#
# install deps with:
#
# python3 -m venv .venv
# .venv/bin/pip install cocotb pytest ml_dtypes
#
# PLUSARGS=+dump for a waveform that can be viewed in gtkwave
# STRIDE=8 to sample the exhaustivity tests (fast sanity check)

all: compile synth test

test: test_e4m3_ieee

compile: $(SOURCES)
	iverilog -g2012 -s tt_um_fommil_polkadot_E4M3 $(SOURCES)

synth: $(SOURCES)
	yosys -p 'read_verilog -sv $(SOURCES); synth -top tt_um_fommil_polkadot_E4M3; stat'

# polkadot_EXP_MAN_OCP_GUARD.vvp
polkadot_%.vvp: $(SOURCES) $(wildcard test/*.v)
	iverilog -o $@ -s polkadot -s dump -g2012 \
	  -Ppolkadot.EXP=$(word 1,$(subst _, ,$*)) \
	  -Ppolkadot.MAN=$(word 2,$(subst _, ,$*)) \
	  -Ppolkadot.OCP=$(word 3,$(subst _, ,$*)) \
	  -Ppolkadot.GUARD=$(word 4,$(subst _, ,$*)) \
	  $^

test_e4m3_ieee: polkadot_4_3_0_0.vvp test/test_e4m3_ieee.py
	MODULE=$@ vvp -M $$(cocotb-config --prefix)/cocotb/libs -m libcocotbvpi_icarus $< $(PLUSARGS)
	! grep failure results.xml

clean:
	rm -rf *.vcd *.vvp *.json results.xml sim_build test/__pycache__

.PHONY: all compile synth test test_e4m3_ieee clean
