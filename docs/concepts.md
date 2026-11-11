# Real-time concepts, one milestone at a time

## M0: Integer time, rounding once at the edge
1. Hardware sequencers count clock ticks; they have no notion of seconds or fractions of a tick.
2. Floats can't represent most decimal times exactly, and their errors add up as events are scheduled.
3. qtwin therefore keeps every timestamp as an integer number of 4 ns cycles (250 MHz).
4. Seconds are converted exactly once, at the API edge, with one stated rule: round half to even.
5. Every later comparison of times (late commands, skew, drift) is exact integer arithmetic, so results are reproducible.

## M1: Memory-mapped registers
1. The host controls the device only by reading and writing 32-bit words at fixed addresses; each address is a register with a stated access type (RO, RW, W1C, WO).
2. Status bits are sticky: the hardware sets them when something goes wrong, and they stay set until software clears them, so a brief error can't be missed between two reads.
3. W1C (write-1-to-clear) lets software clear exactly the bits it has handled; a bit that the hardware sets in the meantime survives, which a plain read-modify-write would erase.
4. A 64-bit counter takes two 32-bit reads, and it keeps counting in between. Reading LO latches HI into a shadow, so the pair is one snapshot even across a rollover.
5. The interrupt line is pending AND enabled (`IRQ_STATUS & IRQ_ENABLE`): masking hides an event from the CPU without losing it.

## M2: Timestamped commands and event-driven sequencing
1. A command carries the cycle at which it must happen, so WHEN software sends it is decoupled from WHEN the output changes: software only has to be early, not punctual.
2. The FIFO is strictly first-in first-out and the sequencer only looks at the head, so commands with the same timestamp always run in push order, with no tie-break rule needed.
3. The simulation is event-driven: the clock jumps straight to the next command's timestamp, so a long run with few commands costs a few steps, not one step per cycle.
4. A late command (timestamp already passed) is a missed deadline. The device runs it at once and sets a sticky LATE flag, so the miss is never silent.
5. The output trace depends only on the commands and their timestamps, so the same input always gives the identical trace.
