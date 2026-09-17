SOURCES = $(wildcard src/*.v)

export PATH := $(CURDIR)/.venv/bin:$(PATH)

export PYTHONPATH := $(CURDIR)/test
export COCOTB_REDUCED_LOG_FMT=1
export NO_COLOR=1
export LIBPYTHON_LOC=$(shell cocotb-config --libpython)

# based on https://github.com/mattvenn/rgb_mixer_2025
#
# install deps with:
#
# python3 -m venv .venv
# .venv/bin/pip install cocotb pytest ml_dtypes
#
# PLUSARGS=+dump for a waveform that can be viewed in gtkwave
# STRIDE=7 to sample the exhaustivity tests (fast sanity check)
#          (note that same STRIDE values will fail the tests, e.g. 8)

all: compile synth test

# polkadot_EXP_MAN_OCP_GUARD configurations to test
CONFIGS ?= 4_3_0_0 4_3_0_1 4_3_1_0 3_4_0_0 3_4_0_1 3_4_1_0
TESTS = $(CONFIGS:%=test_polkadot_%)

test: $(TESTS)

compile: $(SOURCES)
	iverilog -g2012 -s tt_um_fommil_polkadot_E4M3 $(SOURCES)

synth: $(SOURCES)
	yosys -p 'read_verilog -sv $(SOURCES); synth -top tt_um_fommil_polkadot_E4M3; stat'

.PRECIOUS: polkadot_%.vvp

# polkadot_EXP_MAN_OCP_GUARD.vvp
polkadot_%.vvp: $(SOURCES) $(wildcard test/*.v)
	iverilog -o $@ -s polkadot -s dump -g2012 \
	  -Ppolkadot.EXP=$(word 1,$(subst _, ,$*)) \
	  -Ppolkadot.MAN=$(word 2,$(subst _, ,$*)) \
	  -Ppolkadot.OCP=$(word 3,$(subst _, ,$*)) \
	  -Ppolkadot.GUARD=$(word 4,$(subst _, ,$*)) \
	  $^

VVP = vvp -M $$(cocotb-config --prefix)/cocotb/libs -m libcocotbvpi_icarus

test_polkadot_%: polkadot_%.vvp test/test_polkadot.py
	MODULE=test_polkadot $(VVP) $< $(PLUSARGS)
	! grep failure results.xml

clean:
	rm -rf *.vcd *.vvp *.json results.xml sim_build test/__pycache__

.PHONY: all compile synth test clean
