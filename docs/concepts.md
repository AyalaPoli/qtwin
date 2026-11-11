# Real-time concepts, one milestone at a time

## M0: Integer time, rounding once at the edge
1. Hardware sequencers count clock ticks; they have no notion of seconds or fractions of a tick.
2. Floats can't represent most decimal times exactly, and their errors add up as events are scheduled.
3. qtwin therefore keeps every timestamp as an integer number of 4 ns cycles (250 MHz).
4. Seconds are converted exactly once, at the API edge, with one stated rule: round half to even.
5. Every later comparison of times (late commands, skew, drift) is exact integer arithmetic, so results are reproducible.
