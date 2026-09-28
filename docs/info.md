## How it works

An exact dot-product accumulator for OCP FP8 E4M3 (fn) numbers. Every product `a*b` is expanded into its exact fixed-point form and added to a wide internal integer accumulator, so the only rounding happens once, when the output is produced. The order of the terms therefore does not change the result.

The chip has only 8 input pins, so each product takes two cycles: `LOAD_A` latches `a` into a register, then `MAC` takes `b` directly from `ui_in`. The opcode is set on `uio[3:0]`:

| Opcode | Name         | Effect                                             |
|--------|--------------|----------------------------------------------------|
| `0000` | `NOP`        | hold                                               |
| `0001` | `LOAD_A_CLR` | `a <= ui_in`, clear accumulator on next `MAC`/`ADD` |
| `0010` | `LOAD_A`     | `a <= ui_in`                                       |
| `0011` | `MAC`        | `acc += a * ui_in`                                 |
| `0100` | `ADD_CLR`    | `acc = ui_in`                                      |
| `0101` | `ADD`        | `acc += ui_in`                                     |
| `0110` | `CTRL`       | `rmode <= ui_in[2:0]`, `sat <= ui_in[3]`           |

The rounding modes are `0` RNE (default), `1` RTZ and `4` RMM. `sat` saturates to ±448 on overflow instead of returning NaN. `CTRL` settings persist and apply retroactively to the current accumulator.

`uo_out` holds the rounded sum, one cycle behind the instruction that caused it. `STROBE` goes high on the cycles where the output may have changed. `INEXACT`, `OVERFLOW` and `INVALID` (NaN) are the IEEE-style flags. Underflow is `INEXACT && !OVERFLOW && exponent == 0`.

## How to test

Reset the design, then from the RP2040 on the demo board (or by hand with a manual clock), drive `ui_in` and `uio[3:0]` and pulse the clock once per instruction. For example, to compute `2*3 + 0.5`:

| `uio[3:0]` | `ui_in`      | `uo_out` after the clock |
|------------|--------------|------------------------|
| `0001`     | `0x40` (2.0) | -                      |
| `0011`     | `0x44` (3.0) | `0x4C` (6.0)           |
| `0101`     | `0x30` (0.5) | `0x4D` (6.5)           |

`0x38` is 1.0 and `0x7F` is NaN.

## External hardware

None required. The design is meant to be driven as a coprocessor by a hobby 8-bit computer (e.g. Ben Eater's 8-bit or a 6502) at around 1 MHz.
