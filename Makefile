SOURCES = $(wildcard src/*.v)
GL_NETLIST ?= test/gate_level_netlist.v
GL_RUNS_NETLIST = runs/wokwi/final/pnl/tt_um_fommil_polkadot_E4M3.pnl.v
GL_CELLS = $(PDK_ROOT)/ciel/sky130/versions/$(PDK_VERSION)/$(PDK)/libs.ref/sky130_fd_sc_hd/verilog

# provide your own PDK_ROOT if you already have one from another project
export PDK_ROOT ?= $(HOME)/.ciel
export PDK ?= sky130A

export PATH := $(CURDIR)/.venv/bin:$(PATH)

export PYTHONPATH := $(CURDIR)/test
export COCOTB_REDUCED_LOG_FMT=1
export NO_COLOR=1
COCOTB_LOG_LEVEL ?= INFO
GPI_LOG_LEVEL ?= WARNING
export COCOTB_LOG_LEVEL GPI_LOG_LEVEL
export PYGPI_PYTHON_BIN := $(shell PATH="$(PATH)" cocotb-config --python-bin)
export GPI_USERS := $(shell PATH="$(PATH)" cocotb-config --libpython);$(shell PATH="$(PATH)" cocotb-config --pygpi-entry-point)

# match the version of librelane to the shuttle...
# https://raw.githubusercontent.com/TinyTapeout/tt-gds-action/ttsky26d/action.yml
LIBRELANE_VERSION = 3.0.14

PDK_VERSION = 8afc8346a57fe1ab7934ba5a6056ea8b43078e71
LIB = $(PDK_ROOT)/ciel/sky130/versions/$(PDK_VERSION)/$(PDK)/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib

# PLUSARGS=+dump for a waveform that can be viewed in gtkwave
#
# SWEEP_LIMIT=n caps each pair sweep at about n transactions. The default sweeps
#               the 8 bit formats exhaustively and samples the wider ones.
#               SWEEP_LIMIT=500 is a fast smoke test, larger values buy
#               coverage; exhausting E5M10 would need 2^32 per sweep, i.e. days.

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

test: $(TESTS) test_project

compile: $(SOURCES)
	verilator --lint-only -Wall --top-module tt_um_fommil_polkadot_E4M3 $(SOURCES)
	iverilog -g2012 -s tt_um_fommil_polkadot_E4M3 $(SOURCES)

synth: $(SOURCES)
	yosys -p 'read_verilog -sv $(SOURCES); synth -top tt_um_fommil_polkadot_E4M3; stat'

.PRECIOUS: polkadot_%.vvp

# polkadot_EXP_MAN_FN_GUARD.vvp
polkadot_%.vvp: $(SOURCES) $(filter-out $(GL_NETLIST),$(wildcard test/*.v))
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

project.vvp: $(SOURCES) test/dump_project.v
	iverilog -o $@ -s tt_um_fommil_polkadot_E4M3 -s dump_project -g2012 $^

test_project: project.vvp test/test_project.py
	@rm -f results.xml
	COCOTB_TEST_MODULES=test_project $(VVP) $< $(PLUSARGS)
	python -m cocotb_tools.check_results results.xml

$(GL_NETLIST): $(wildcard $(GL_RUNS_NETLIST))
	@test -f $(GL_RUNS_NETLIST) || { echo "$(GL_RUNS_NETLIST) not found: run 'make harden' first" >&2; exit 1; }
	cp $(GL_RUNS_NETLIST) $@

project_gl.vvp: $(GL_NETLIST) test/dump_project.v
	iverilog -o $@ -s tt_um_fommil_polkadot_E4M3 -s dump_project -g2012 \
	  -DGL_TEST -DFUNCTIONAL -DUSE_POWER_PINS -DSIM -DUNIT_DELAY=\#1 \
	  $(GL_CELLS)/primitives.v $(GL_CELLS)/sky130_fd_sc_hd.v $^

test_gds: project_gl.vvp test/test_project.py
	@rm -f results.xml
	COCOTB_TEST_MODULES=test_project $(VVP) $< $(PLUSARGS)
	python -m cocotb_tools.check_results results.xml

harden: $(SOURCES) info.yaml src/config.json
	tt/tt_tool.py --create-user-config
	tt/tt_tool.py --harden
	cp $(GL_RUNS_NETLIST) $(GL_NETLIST)
	tt/tt_tool.py --print-warnings

stats:
	tt/tt_tool.py --print-cell-summary
	tt/tt_tool.py --print-cell-category
	tt/tt_tool.py --print-warnings
	tt/tt_tool.py --print-stats
	python ./fmax.py

# this produces a file that lets us pick a value for SYNTH_STRATEGY
explore:
	python -m librelane --dockerized --pdk-root "$(PDK_ROOT)" --pdk sky130A -f SynthesisExploration src/config_merged.json

# just for fun, note that the netlist uses the default values in polkadot.v not the
# values used by project.v
render:
	python tt/tt_tool.py --create-svg --create-png
	convert gds_render.png -resize 1000x render.jpg
	yowasp-yosys -p "read_verilog src/polkadot.v; prep -top polkadot; show -format svg -prefix docs/netlist"
	dot -Tsvg 'docs/netlist.dot' > 'docs/netlist.svg.new' && mv 'docs/netlist.svg.new' 'docs/netlist.svg'

# and a more complex rendering with the synthesized gate level
render_synth:
# these are just using basic abc settings...
#	yowasp-yosys -p "read_verilog src/polkadot.v; synth -flatten -top polkadot; opt_clean; show -format svg -prefix docs/netlist_synth"
#	dot -Tsvg 'docs/netlist_synth.dot' > 'docs/netlist_synth.svg.new' && mv 'docs/netlist_synth.svg.new' 'docs/netlist_toy_synth.svg'
	yowasp-yosys -p "read_liberty -lib $(LIB); read_verilog runs/wokwi/06-yosys-synthesis/tt_um_fommil_polkadot_E4M3.nl.v; hierarchy -top tt_um_fommil_polkadot_E4M3; show -format dot -prefix docs/netlist_synth tt_um_fommil_polkadot_E4M3"
	dot -Tsvg 'docs/netlist_synth.dot' > 'docs/netlist_synth.svg.new' && mv 'docs/netlist_synth.svg.new' 'docs/netlist_synth.svg'

deps:
	python3 -m venv .venv
	.venv/bin/pip install -r tt/requirements.txt cocotb pytest librelane==$(LIBRELANE_VERSION)
	.venv/bin/pip install --no-deps 'ml_dtypes>=0.5,<0.6' # otherwise we get 0.4

clean:
	rm -rf *.vcd *.vvp *.json results.xml sim_build test/__pycache__ .venv/

.PHONY: compile synth test test_project test_gds harden stats deps clean
