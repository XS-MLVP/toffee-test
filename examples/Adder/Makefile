# Shared simulator entry point; generated artifacts live under outputs/.
SIM ?= verilator
EXAMPLE_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPO_ROOT := $(abspath $(EXAMPLE_DIR)/../..)
PICKER ?= $(REPO_ROOT)/../picker/build/bin/picker
PYTHON ?= python3
TOFFEE_ROOT ?= $(REPO_ROOT)/../toffee
JOBS ?= 4
VERDI_MODE ?= modern
OUTPUT_ROOT ?= $(REPO_ROOT)/outputs
OUTPUT_DIR := $(abspath $(OUTPUT_ROOT))/Adder/$(SIM)
PICKER_ARGS ?=
PYTEST_ARGS ?=

export PYTHONDONTWRITEBYTECODE := 1
export PYTHONPATH := $(OUTPUT_DIR)/build:$(REPO_ROOT):$(TOFFEE_ROOT)$(if $(PYTHONPATH),:$(PYTHONPATH))
ifeq ($(SIM),vcs)
WAVE := Adder.fsdb
SIM_ARGS := --verdi-mode $(VERDI_MODE) --vflag "-cm_line contassign"
export GLIBC_TUNABLES := glibc.rtld.optional_static_tls=262144$(if $(GLIBC_TUNABLES),:$(GLIBC_TUNABLES))
else
WAVE := Adder.fst
endif

.DEFAULT_GOAL := test_Adder
.PHONY: test_Adder dut run clean

test_Adder: dut
	$(MAKE) run

dut:
	rm -rf "$(OUTPUT_DIR)/build/Adder"
	mkdir -p "$(OUTPUT_DIR)/build"
	cd "$(OUTPUT_DIR)" && $(PICKER) export "$(EXAMPLE_DIR)/Adder.v" --autobuild true --sim $(SIM) \
		--sname Adder --tdir "$(OUTPUT_DIR)/build/Adder" --lang python \
		-w $(WAVE) --coverage -j $(JOBS) $(SIM_ARGS) $(PICKER_ARGS)

run:
	mkdir -p "$(OUTPUT_DIR)"
	cd "$(OUTPUT_DIR)" && $(PYTHON) -m pytest \
		-c "$(EXAMPLE_DIR)/pytest.ini" "$(EXAMPLE_DIR)/test_adder.py" \
		-o "cache_dir=$(OUTPUT_DIR)/.pytest_cache" \
		--toffee-report --case-coverage --report-dump-json \
		--report-dir reports --report-name index.html $(PYTEST_ARGS)

clean:
	rm -rf "$(OUTPUT_DIR)"
