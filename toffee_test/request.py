import os
from zlib import crc32

from .reporter import set_func_coverage
from .reporter import set_line_coverage


class ToffeeRequest:
    from pytest import FixtureRequest
    def __init__(self, request: FixtureRequest):
        self.dut = None
        self.args = None
        self.request = request
        self.cov_groups = []

        self.waveform_filename = None
        self.coverage_filename = None

    def __add_cov_sample(self, cov_groups):
        """
        Add the coverage sample to the DUT.
        """

        assert self.dut is not None, "The DUT has not been set."
        assert self.cov_groups is not None, "The coverage group has not been set."

        if not isinstance(cov_groups, list):
            cov_groups = [cov_groups]

        def sample_helper(cov_point):
            return lambda _: cov_point.sample()

        for g in cov_groups:
            self.dut.xclock.StepRis(sample_helper(g))

    def __need_report(self) -> bool:
        """
        Whether to generate the report
        """

        return self.request.config.getoption("--toffee-report")

    def __no_func(self) -> bool:
        """
        Whether to run test without functional coverage.
        """
        return self.request.config.getoption("--no-func-cov")

    def create_dut(
            self,
            dut_cls,
            clock_name=None,
            waveform_filename=None,
            coverage_filename=None,
            *dut_extra_args,
            **dut_extra_kwargs,
    ):
        """
        Create the DUT.

        Args:
            dut_cls: The DUT class.
            clock_name: The clock pin name.
            waveform_filename: The waveform filename. If not set, it will be auto-generated.
            coverage_filename: The coverage filename. If not set, it will be auto-generated.
            dut_extra_args: Extra positional arguments for the DUT. e.g., ("arg1", "args2")
            dut_extra_kwargs: Extra keyword arguments for the DUT. e.g., {"arg1": 1, "arg2": 2}
        Returns:
            The DUT instance.
        """

        dut_extra_kwargs["waveform_filename"] = ""
        dut_extra_kwargs["coverage_filename"] = ""

        # Create DUT
        self.dut = dut_cls(*dut_extra_args, **dut_extra_kwargs)

        # Set clock name
        if clock_name:
            self.dut.InitClock(clock_name)

        # Options for running without waveform/coverage generation from the DUT.
        wave_format: str = self.dut.GetWaveFormat()
        use_code_cov: bool = self.dut.GetCovMetrics() != 0

        # Set default export name when generate report
        if self.__need_report():
            # Default export file name
            report_dir = os.path.dirname(self.request.config.option.report[0])  # Get report dir
            request_name = self.request.node.name  # Get name of the case
            path_bytes = str(self.request.path).encode("utf-8")
            path_hash = format(crc32(path_bytes), 'x')  # Get the hash of the case file path
            # Default export name is '{DUT_Class}_{request_name}_{path_hash}'
            default_export_name = "_".join((dut_cls.__name__, request_name, path_hash))
            # Set default waveform name
            if wave_format:
                default_name = ".".join((default_export_name, wave_format))
                self.waveform_filename = "/".join((report_dir, default_name))
            # Set default coverage name
            if use_code_cov:
                default_name = ".".join((default_export_name, "dat"))
                self.coverage_filename = "/".join((report_dir, default_name))

        # Also export functional coverage
        if not self.__no_func() and self.cov_groups:
            self.__add_cov_sample(self.cov_groups)

        # Set waveform name
        if wave_format:
            if waveform_filename:
                self.waveform_filename = ".".join((waveform_filename, wave_format))
            self.dut.SetWaveform(self.waveform_filename)

        # Set coverage name
        if use_code_cov:
            if coverage_filename:
                self.coverage_filename = coverage_filename
            self.dut.SetCoverage(self.coverage_filename)

        return self.dut

    def add_cov_groups(self, cov_groups, periodic_sample=True):
        """
        Add the coverage groups to the list.

        Args:
            cov_groups: The coverage groups to be added.
            periodic_sample: Whether to sample the coverage periodically.
        """

        if not isinstance(cov_groups, list):
            cov_groups = [cov_groups]
        self.cov_groups.extend(cov_groups)

        if self.dut is not None and not self.__no_func() and periodic_sample:
            self.__add_cov_sample(cov_groups)

    def finish(self, request):
        """
        Finish the request.
        """

        if self.dut is not None:
            self.dut.Finish()

            use_code_cov: bool = self.dut.GetCovMetrics() != 0

            if self.__need_report():
                if not self.__no_func():
                    set_func_coverage(request, self.cov_groups)
                if use_code_cov:
                    set_line_coverage(request, self.coverage_filename)

        for g in self.cov_groups:
            g.clear()

        self.cov_groups.clear()


PreRequest = ToffeeRequest
