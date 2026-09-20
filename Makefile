SOURCES = $(wildcard src/*.v)

export PATH := $(CURDIR)/.venv/bin:$(PATH)

export PYTHONPATH := $(CURDIR)/test
export COCOTB_REDUCED_LOG_FMT=1
export NO_COLOR=1
COCOTB_LOG_LEVEL ?= INFO
GPI_LOG_LEVEL ?= WARNING
export COCOTB_LOG_LEVEL GPI_LOG_LEVEL
export PYGPI_PYTHON_BIN := $(shell cocotb-config --python-bin)
export GPI_USERS := $(shell cocotb-config --libpython);$(shell cocotb-config --pygpi-entry-point)

# based on https://github.com/mattvenn/rgb_mixer_2025
#
# install deps with:
#
# python3 -m venv .venv
# .venv/bin/pip install cocotb pytest ml_dtypes
#
# PLUSARGS=+dump for a waveform that can be viewed in gtkwave
#
# SWEEP_LIMIT=n caps each pair sweep at about n transactions. The default sweeps
#               the 8 bit formats exhaustively and samples the wider ones.
#               SWEEP_LIMIT=500 is a fast smoke test, larger values buy
#               coverage; exhausting E5M10 would need 2^32 per sweep, i.e. days.

all: compile synth test

# polkadot_EXP_MAN_FN_GUARD configurations to test.
#
# FN=1 is the finite-only ("fn") encoding
ifndef CONFIGS
CONFIGS  = 4_3_0_0  # ml_dtypes.float8_e4m3 (IEEE style, has Inf)
CONFIGS += 4_3_0_1  # ... with guard bit
CONFIGS += 4_3_1_0  # OCP OFP8 E4M3 = ml_dtypes.float8_e4m3fn
CONFIGS += 4_3_1_1  # ... with guard bit
CONFIGS += 3_4_0_0  # ml_dtypes.float8_e3m4 (IEEE style, has Inf)
CONFIGS += 3_4_0_1  # ... with guard bit
CONFIGS += 3_4_1_0  # non-standard "e3m4fn"
CONFIGS += 5_2_0_0  # OCP OFP8 E5M2 = ml_dtypes.float8_e5m2
CONFIGS += 5_10_0_0 # IEEE 754 binary16 = numpy.float16
CONFIGS += 5_10_0_1 # ... with guard bit
CONFIGS += 8_7_0_0  # ml_dtypes.bfloat16 (de-facto standard, not IEEE)
endif
TESTS = $(CONFIGS:%=test_polkadot_%)

test: $(TESTS)

compile: $(SOURCES)
	iverilog -g2012 -s tt_um_fommil_polkadot_E4M3 $(SOURCES)

synth: $(SOURCES)
	yosys -p 'read_verilog -sv $(SOURCES); synth -top tt_um_fommil_polkadot_E4M3; stat'

.PRECIOUS: polkadot_%.vvp

# polkadot_EXP_MAN_FN_GUARD.vvp
polkadot_%.vvp: $(SOURCES) $(wildcard test/*.v)
	iverilog -o $@ -s polkadot -s dump -g2012 \
	  -Ppolkadot.EXP=$(word 1,$(subst _, ,$*)) \
	  -Ppolkadot.MAN=$(word 2,$(subst _, ,$*)) \
	  -Ppolkadot.FN=$(word 3,$(subst _, ,$*)) \
	  -Ppolkadot.GUARD=$(word 4,$(subst _, ,$*)) \
	  $^

VVP = vvp -m $$(cocotb-config --lib-entry vpi icarus)

test_polkadot_%: polkadot_%.vvp test/test_polkadot.py
	@rm -f results.xml
	COCOTB_TEST_MODULES=test_polkadot $(VVP) $< $(PLUSARGS)
	python -m cocotb_tools.check_results results.xml

clean:
	rm -rf *.vcd *.vvp *.json results.xml sim_build test/__pycache__

.PHONY: all compile synth test clean
