"""The same six testcase groups run against either Picker simulator backend."""

import os
import random

import pytest
import toffee
import toffee_test
from Adder import DUTAdder
from toffee.funcov import CovGroup, Eq

WIDTH = 128
MASK = (1 << WIDTH) - 1
PARENT_PID = os.getpid()


@toffee_test.fixture
async def dut(toffee_request):
    # Referencing the generated class directly also enables automatic VCS fork.
    # The generated DPI wrapper is infrastructure, outside the Adder RTL.
    dut = toffee_request.create_dut(DUTAdder, ignore_patterns=["*Adder_top.sv"])
    assert (os.getpid() != PARENT_PID) == (DUTAdder.SIMULATOR == "vcs")
    group = CovGroup("Adder")
    group.add_cover_point(dut.cin, {"zero": Eq(0), "one": Eq(1)}, name="carry_in")
    group.add_cover_point(dut.cout, {"zero": Eq(0), "one": Eq(1)}, name="carry_out")
    toffee_request.add_cov_groups(group)
    toffee.start_clock(dut)
    return dut


def vectors(case):
    if case == "zero":
        return [(0, 0, cin) for cin in (0, 1)]
    if case == "small":
        return [(a, b, cin) for a in range(4) for b in range(4) for cin in (0, 1)]
    if case == "boundary":
        return [(a, b, cin) for a in (0, 1, MASK) for b in (0, 1, MASK) for cin in (0, 1)]
    if case == "carry_chain":
        return [((1 << bit) - 1, 0, 1) for bit in range(1, WIDTH + 1)]
    if case == "alternating":
        alternating = int("aa" * (WIDTH // 8), 16)
        return [(a, b, cin) for a in (alternating, MASK ^ alternating)
                for b in (alternating, MASK ^ alternating) for cin in (0, 1)]
    rng = random.Random(20261102)
    return [(rng.getrandbits(WIDTH), rng.getrandbits(WIDTH), rng.getrandbits(1))
            for _ in range(128)]


@pytest.mark.parametrize("case", ["zero", "small", "boundary", "carry_chain", "alternating", "random"])
@toffee_test.testcase
async def test_adder(dut, case):
    for a, b, cin in vectors(case):
        dut.a.value = a
        dut.b.value = b
        dut.cin.value = cin
        await toffee.ClockCycles(dut)
        expected = a + b + cin
        assert dut.sum.value == expected & MASK, (case, a, b, cin)
        assert dut.cout.value == expected >> WIDTH, (case, a, b, cin)
