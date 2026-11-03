SIM ?= verilator

.PHONY: test_Adder test_vcs_Adder test_verilator_Adder clean_Adder

test_Adder:
	$(MAKE) -C examples/Adder test_Adder SIM=$(SIM)

test_vcs_Adder:
	$(MAKE) -C examples/Adder test_Adder SIM=vcs

test_verilator_Adder:
	$(MAKE) -C examples/Adder test_Adder SIM=verilator

clean_Adder:
	$(MAKE) -C examples/Adder clean SIM=vcs
	$(MAKE) -C examples/Adder clean SIM=verilator
