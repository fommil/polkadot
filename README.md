The primary usecase for this project is to create hardware LLM accelerators (and other uses of large scale matrix multiplication).

For an overview of the process for making a microchip see [Designing Silicon from Scratch by Breaking Taps](https://youtu.be/q6ytRHaTEXI) and consider doing the [Zero to ASIC course](https://www.zerotoasiccourse.com/) (like I did).

An implementation of an Exact [Dot Product](https://en.wikipedia.org/wiki/Dot_product) in verilog. This is aimed at 8bit floating point formats to fit [tinytapeout](https://tinytapeout.com/) but is parameterisable for use in FPGAs or custom tapeouts at higher precision.

"Exact" in the sense that it uses fixed point accumulation and there is a single rounding at the end, such that calculation order doesn't matter, e.g. (simplified)

```
1000 + 0.0001 + 0.0001 - 1000
=>   0.0002
```

whereas rounding after each multiply into a register could yield an answer of `0.0`

There is no limit to the size of the input vectors, an element is added on every clock cycle and the accumulated result always available.

This single (stateful) instruction can be used to build `MULT`, `ADD`, `FMADD`, `SUB`, `FMSUB` instructions for hobby 8bit computers such as the [`@beneater`](https://www.youtube.com/@BenEater/playlists) 8-bit or 6502. And can also be used as the basis of efficient matrix multiplication for scientific computations and machine learning.

This component could be duplicated (many times) as part of a larger design in a systolic array to build a TPU for large scale matrix multiplication.

### Design

The [Handbook of Floating-Point Arithmetic](https://link.springer.com/book/10.1007/978-3-319-76526-6) (Muller et al) gives a description of a typical hardware floating point MULT and ADD. Bizarrely, a fixed point accumulating multiply is simpler than a rounded one, since we do not need to consider the case when the numbers are at a different scale. Instead, we simply expand every number into its exact representation (roughly 64 bits for 8 bit floating point inputs, and several kilobits for double precision floating point numbers) and perform integer ADD and MULT on an aggregator. Multi-layer cache solutions have been proposed [by Koenig](http://www2.eecs.berkeley.edu/Pubs/TechRpts/2018/EECS-2018-51.html) for the higher precision bits, but this implementation just keeps it simple by using internal registers, so it's quite big.

There is no attempt to optimise the upper bound on the clock frequency. It may be possible to redesign the phases of this computation such that answers are available several clock cycles after the inputs are provided, in order to consume more inputs within the same amount of time.

### Developers

The github actions are based on https://github.com/TinyTapeout/ttsky-verilog-template

To get setup you must have `python3`, `iverilog`, `verilator` and `docker` installed. To install local dependencies

```
git submodule update --init
make deps
```

To run the tests

```
make test
```

To do the lengthy verification steps and produce a GDS (this will do some big downloads on first run, and requires docker to be running)

```
make harden
```

Look in `runs/wokwi/` for errors/warnings. Diagnostics are available with

```
make stats
```

To render an image try

```
make render
```

![rendered image of the circuit](./render.jpg)

and also look under `docs/netlist.svg`. To see a gate level netlist, try (slow)

```
make render_synth
```

and look in `docs/netlist_synth.svg`.

The final output is in `runs/wokwi/final/` (GDS, LEF, netlists).

The CI builds a [3d visualisation of the design](https://gds-viewer.tinytapeout.com/?pdk=sky130A&model=https%3A%2F%2Ffommil.com%2Fpolkadot%2F%2Ftinytapeout.oas) !

### Timing and Optimisations

An estimate of maximum clock frequency is `1 / (CLOCK_PERIOD − WNS)`.

`src/config.json` sets `CLOCK_PERIOD: 20` (20 ns, 50 MHz)

`WNS` (worst negative slack) is obtained from looking at the output of `runs/wokwi/*-openroad-stapostpnr/summary.rpt`

The `fmax.py` script will automatically produce reports for each "corner" and at different temperatues. With an `AREA 3` synth strategy I am just over the limit for 1 tile, so on 1x2:

```
| Utilisation (%) | Wire length (um) |
|-------------|------------------|
| 46.766 % | 39625 |
python ./fmax.py
nom_tt_025C_1v80        8.5040 ns   86.99 MHz
nom_ss_100C_1v60        2.0258 ns   55.64 MHz
nom_ff_n40C_1v95       11.0136 ns  111.28 MHz
min_tt_025C_1v80        8.6315 ns   87.96 MHz
min_ss_100C_1v60        2.2470 ns   56.33 MHz
min_ff_n40C_1v95       11.0983 ns  112.34 MHz
max_tt_025C_1v80        8.3547 ns   85.87 MHz
max_ss_100C_1v60        1.7771 ns   54.88 MHz
max_ff_n40C_1v95       10.9086 ns  109.99 MHz
fmax range: 54.88 – 112.34 MHz
```

We can try to swap out the `SYNTH_ADDER_TYPE` in `src/config.json`. Here are some numbers with various adder implementations

- `FA` 47% utilisation, 54.88 – 112.34 MHz
- `RCA` violations, 49% utilisation, 17.62 – 52.45 MHz
- `CSA` 50% utilisation, 39.09 – 91.74 MHz

But looking at our `docs/netlist.svg` we see that the majority of the time is take up by muxing.

However, since this project is designed to run on a 1MHz beneater style 8-bit computer, and tinytapeout is already limited to 50MHz I/O, we choose to leave it as it is.

### Future Work

#### 16 bit

Experiments showed that this needed about 10x tinytapeout space to fit a bfloat16 build of the `polkadot` module. It would be interesting to actually do that, or at least simulate on an FPGA.

#### Art

I was too close to the limit to be able to include art in this design, encorporating some easter egg designs in the next one could be feasible https://tinytapeout.com/guides/creating-silicon-art/ / https://github.com/nicoca20/artistic

#### Clock Optimisation

I find it hard to understand what to optimise, besides eyeballing the netlist. There are detailed reports under `runs/wokwi/*-openroad-stapostpnr` that can be analysed to get timings. However, a tool that simply takes the theoretical gate level propagation times and overlays it onto the netlist, while accumulating the time to get there, would be very useful for finding what is best to pipeline.

For this particular design it seems that the sequential `$mux` step (taking the initial mult result and shifting it into the `msb` register) would benefit from either a rethink or pipelining.

#### FPGA

It would be very useful to be able to target an FPGA, not just for testing but to be able to make use of this design as an actual hardware accelerator allowing very parallel workloads.

#### PCB

It would be a lot of fun to synthesise to a PCB and have it made with 74 series popcorn ICs. That is feasible with the yosys `74xx-liberty` backend, e.g.

https://pepijndevos.nl/2019/07/18/vhdl-to-pcb.html

