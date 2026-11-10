# 将其他仿真器的报告接入主 index.html

目标：让 Toffee 的主报告在 **Line Coverage → Detail** 中显示新仿真器的
`HTML` 或 `HTML | UVD` 等入口。这里的接入方式是链接到原生报告，
点击后在新标签页打开；不会把原生 HTML 内容拼进主页面，也不是 iframe。

**仿真器适配器只负责两部分：解析报告得到覆盖率，提供详情入口信息。**
分别放在 `convert_session()` 和 `build_details()` 中。无需修改 HTML 模板，
也无需实现 `convert_case()`。本教程不启用 `--case-coverage`。

## 接入流程

```text
注册 provider，并提交覆盖率记录
    ↓
convert_session(records, output_dir)
    解析/生成原生报告，返回行覆盖率及产物路径
    ↓
build_details(result, output_dir)
    将产物路径转换为 HTML/应用入口描述
    ↓
公共层生成链接，渲染主 index.html
    Line Coverage: 80% ... Detail: HTML | Open UVD
```

| 接入者需要提供什么 | 主报告如何使用 |
|---|---|
| 实际生成的 HTML 及 CSS、JS、子页面 | 点击 HTML 后展示原生报告 |
| `metrics["line"]` | 展示已覆盖行数、总行数和百分比 |
| `HtmlDetail(path=...)` | 在 Detail 列生成 HTML 链接 |
| 可选 `ApplicationDetail(...)` | 根据 native/xpra 模式生成应用入口 |

`convert_session` 在主报告生成时调用。`records` 是本次运行提交的输入记录，
`output_dir` 是框架准备好的覆盖率输出目录，新增仿真器当前为 `reports/line_dat/`，VCS/Verilator 为 `reports/coverage_data/`。
不要写死目录名，使用传入的 `output_dir` 和 `html_report_detail()`。
仿真器工具的调用、输入合并和统计解析都放在这个方法中。

## 最小示例：接入已经生成的 HTML 报告

下面的适配器接收**一个已经汇总好的报告目录**。不演示厂商的生成命令：
请先用对应仿真器的工具生成真实报告，并把行统计整理成以下约定的
`line-summary.json`。这里的 80/100 仅用于说明输入格式。

```text
project/
├── conftest.py
├── test_example.py
└── simulator-output/
    ├── index.html
    ├── line-summary.json    # {"covered": 80, "total": 100}
    ├── assets/
    └── ...                 # 原生报告依赖的其他文件
```

在项目的 `conftest.py` 中注册适配器，并提交输入记录：

```python
import json
from pathlib import Path
import shutil

import pytest

from toffee_test.reporter import set_code_coverage
from toffee_test.utils.code_coverage import (
    CodeCoverageResult, CoverageProvider, CoverageRecord,
    HtmlDetail, MetricSummary, register_coverage_provider,
)
from toffee_test.utils.code_coverage.common import html_report_detail


class MySimulatorProvider(CoverageProvider):
    simulator_names = ("my_simulator",)

    def convert_session(self, records, output_dir, **options):
        """将预先生成的汇总报告接入 Toffee 主报告。"""
        sources = {Path(record.database).resolve() for record in records}
        if len(sources) != 1:
            raise ValueError("本示例只接收一个已汇总报告；多个输入需要先按仿真器规则合并")
        source = sources.pop()
        counts = json.loads((source / "line-summary.json").read_text())
        covered, total = counts["covered"], counts["total"]
        if not (0 <= covered <= total):
            raise ValueError("覆盖率行数无效")

        # 整个目录一起复制，保留 CSS、JS 和子页面的相对链接。
        destination = output_dir / "native" / "my_simulator"
        shutil.copytree(source, destination, dirs_exist_ok=True)

        # 自动计算相对于最终 reports/index.html 的 URL。
        href = html_report_detail(destination, output_dir)
        return CodeCoverageResult(
            simulator="my_simulator",
            metrics={
                "line": MetricSummary(
                    covered=covered,
                    total=total,
                    uncovered=total - covered,
                    rate=100.0 * covered / total if total else 0.0,
                    available=True,
                    details_available=True,
                ),
            },
            detail=href,
        )

    def build_details(self, result, output_dir):
        """第二部分：提供主报告的详情入口。"""
        return [HtmlDetail(path=result.detail, label="HTML")] if result.detail else []


def pytest_configure(config):
    register_coverage_provider(MySimulatorProvider())


@pytest.fixture(scope="module", autouse=True)
def attach_simulator_report(request):
    yield
    # 此时目录中的原生报告必须已经生成完毕。
    source = Path(request.config.rootpath) / "simulator-output"
    set_code_coverage(
        request,
        CoverageRecord(simulator="my_simulator", database=str(source)),
    )
```

这里用 `CoverageRecord.database` 携带适配器输入目录；真实 provider 通常在此字段
接收仿真器实际的覆盖率数据库路径（例如 VCS 的 VDB），在 `convert_session()` 内调用厂商工具生成报告。
记录的 `simulator` 必须匹配 provider 的 `simulator_names`，否则框架无法找到适配器。
也可以继续使用显式注册方式 `register_coverage_provider("my_simulator", provider)`，
此时按传入的名字注册。

内置 UVS 的匹配名称直接在 `uvs.py` 中声明，无需修改中央映射：

```python
class UvsCoverageProvider(CoverageProvider):
    simulator_names = ("uvs", "uvs1")
    # convert_session / build_details ...
```

Picker DUT 的 `SIMULATOR` 或 `GetSimulator()` 返回 `uvs`、`uvs1` 时，
都使用这个 provider。如果只想匹配 `uvs1`，改成 `("uvs1",)` 即可。
名称匹配会去除首尾空格并转为小写。修改后重新启动 pytest，使注册表重新加载。
这是覆盖率 provider 的选择配置；应用的 `viewer="uvd"` 可以保持不变。

执行项目测试：

```bash
pytest --toffee-report --report-dir=reports --report-name=index.html
```

输出结构为：

```text
reports/
├── index.html                         # Toffee 主报告
└── line_dat/native/my_simulator/
    ├── index.html                     # 仿真器原生报告
    ├── assets/
    └── ...
```

主报告的 Detail 列会自动包含：

```html
<a href="line_dat/native/my_simulator/index.html" target="_blank">HTML</a>
```

不要把 `/work/project/.../index.html` 这种文件系统绝对路径作为 HTML 链接。
不需要在 `toffee.html` 中添加仿真器判断，也不需要手工修改生成后的主报告。

## 再增加一个应用入口

在 `build_details()` 中追加应用描述，例如：

```python
from toffee_test.utils.code_coverage import ApplicationDetail

def build_details(self, result, output_dir):
    details = []
    if result.detail:
        details.append(HtmlDetail(path=result.detail, label="HTML"))
    if result.databases:
        details.append(ApplicationDetail(
            viewer="uvd",
            label="UVD",
            artifacts={"database": result.databases[0]},
            scope="Full native database",
        ))
    return details
```

数据库路径必须指向真实产物。只有 HTML 就返回第一项；只有应用就返回第二项。
`viewer` 对应宿主配置的应用名称，`label` 是主报告中的链接文字。
provider 不需要填写 Xpra API URL，也不需要启动应用。

- 默认不传 `--report-custom`：只显示 HTML。
- `--report-custom=coverage-gui-native`：桌面协议处理器直接启动本地应用，新增查看器需配置本地命令。
- `--report-custom=coverage-gui-xpra`：显示 provider 提供的应用页面链接，或由 provider 生成启动页；UCAgent 只选择 GUI 模式。

[UVS provider](../toffee_test/utils/code_coverage/uvs.py) 也按同样的两个部分组织：

| 方法 | 需要实现的内容 | 返回值 |
|---|---|---|
| `convert_session()` | 调用 UVS 工具并解析真实统计，记录生成的 HTML 和覆盖率数据库路径 | `CodeCoverageResult(metrics=..., detail=..., databases=...)` |
| `build_details()` | 根据上述路径决定显示 HTML、UVD 或两者 | `list[HtmlDetail | ApplicationDetail]` |

例如，第一部分返回 `metrics["line"]` 的 80/100 后，主页面显示 80%；
第二部分返回 HTML 和 UVD 两个描述后，详情列按所选 GUI 模式生成相应入口。

UVS 不需要知道 Xpra API 地址，也不需要生成本地协议链接。
native 模式由本地处理器生成桌面链接；xpra 模式的 URL 和启动行为由 provider 实现。

UVS uses `uvcov -db <compile>.cdb <run1>.cdb ... -mdb <output>/merged.cdb`
to generate the report and save a merged database containing the selected runs.
The Xpra link launches `uvd -ucdb <output>/merged.cdb`, avoiding commands that
exceed the host API limit. UVD displays the full native hierarchy, including
generated wrappers; the main report's DUT filtering does not apply to the GUI.
Report generation only writes the launch page; clicking the link creates the
Xpra session. The default remains HTML-only. VCS still uses URG for its reports.

## Selecting a GUI mode

`--report-custom=OPTION` is a repeatable list option. Each occurrence adds one
item to `config.option.report_custom`; it is not a comma-separated string or a
JSON array. Currently supported values are `coverage-gui-native` and
`coverage-gui-xpra`. They are mutually exclusive; unknown values are errors.
Repeated identical values are allowed. With no options, reports show HTML only.
This replaces the previous dedicated `--coverage-gui` option.

| pytest option | Detail links | Click behavior |
|---|---|---|
| no `--report-custom` options (default) | HTML only | Open native HTML |
| `--report-custom=coverage-gui-native` | HTML and available application descriptors | Desktop protocol handler launches the configured executable |
| `--report-custom=coverage-gui-xpra` | HTML and provider-owned Xpra links | Host launches its Xpra session |

Labels remain provider-owned, such as `HTML | Verdi` or `HTML | Open UVD`, and
appear together in the existing Detail column.
An absent HTML or application artifact is not invented by selecting a mode.
Providers may supply a ready-to-open application `href` with `mode="xpra"`
(or `mode="native"` for a registered native link). Toffee validates and displays
it only in the matching GUI mode, without invoking any launcher.
Without `href`, `viewer` and `artifacts` support the native handler. Xpra
requires a provider-owned URL (or an explicitly configured custom resolver).
No host package is imported during report generation.
An explicit resolver remains an optional override for custom host protocols.

### Open Verdi directly from a local HTML file

Register the desktop handler once on the Linux machine where the application runs:

```bash
python -m toffee_test.cli register-native
```

Generate the report and open `reports/index.html` directly in the browser
(`file://` is supported):

```bash
pytest --toffee-report --report-dir=reports --report-name=index.html \
  --report-custom=coverage-gui-native
```

Clicking Verdi invokes the desktop protocol handler and directly starts:

```python
["verdi", "-cov", "-covdir", "/absolute/workspace/build/design.vdb"]
```

There is no launch webpage, HTTP service or Xpra session. The native anchor uses
`toffee-coverage://open/<local-registration-id>`; it does not open a new browser
tab. The browser may ask permission to open the external application. Allow
“Toffee Coverage” to proceed. This desktop registration follows the
[freedesktop Desktop Entry specification](https://specifications.freedesktop.org/desktop-entry/latest-single/).

Verdi, its license and a graphical desktop must be available to the desktop
handler. Registration resolves known executable paths from the invoking terminal.
If the application needs extra environment setup, configure a local wrapper
executable which sets that environment and then invokes the application.

Native registrations belong to the user who generated the report on this
machine. Copying the report to another user or machine does not grant launch
access; regenerate it locally. Ordinary HTML remains portable with its assets.
The private launch receipt is stored under
`${XDG_STATE_HOME:-~/.local/state}/toffee-test/native/links/`; the report copy
of the manifest is only diagnostic data and cannot change the launch command.
No executable or arbitrary filesystem path is accepted in a browser URI.

The desktop handler starts the local application when clicked; generating or
opening the main HTML report does not start it. Application stdout/stderr goes
to a temporary `toffee-native-*.log`. Launch failures trigger a desktop
notification when available and are recorded in
`${XDG_STATE_HOME:-~/.local/state}/toffee-test/native/last-error.txt`.

Registration currently supports Linux desktops with `xdg-utils`. Re-run
`register-native` if the Python environment or source checkout moves.
The installed CLI also accepts `toffee-test register-native`.

### Configure local application commands

A local JSON file can override Verdi or add viewer IDs:

```json
{
  "verdi": ["/opt/eda/verdi/bin/verdi", "-cov", "-covdir", "{database}"]
}
```

```bash
python -m toffee_test.cli register-native --viewer-config=local-viewers.json
```

Configuration maps `ApplicationDetail.viewer` to a nonempty argv list.
Only whole arguments such as `{database}` are substituted from the descriptor's
artifact mapping. Commands are operator-configured, never supplied by the
report or browser. For UVD, add a `uvd` entry using the installed application's
real arguments; the skeleton does not assume a UVD command-line syntax.

## Xpra with one option

Generate reports using:

```bash
pytest --toffee-report --report-custom=coverage-gui-xpra
```

Providers can return existing viewer pages directly:

```python
ApplicationDetail(
    viewer="uvd", label="UVD", mode="xpra",
    href="https://viewer.example/api/xpra/sessions/123/view",
)
# A provider-generated page beside index.html is also supported:
ApplicationDetail(viewer="uvd", label="UVD", href="apps/uvd.html")
```

The provider owns the page and its lifetime. A direct link opens that page; it
must not point to a POST-only API endpoint. HTML-only mode hides application
links. A link for a different GUI mode is omitted.

To launch a session on click, a provider can write its own page or use the optional
helper used by VCS:

```python
from toffee_test.utils.code_coverage.xpra import write_xpra_link

href = write_xpra_link(
    output_dir,
    ["verdi", "-cov", "-covdir", "/work/demo/build/design.vdb"],
    label="Verdi",
)
return [ApplicationDetail(viewer="verdi", label="Verdi", href=href, mode="xpra")]
```

`output_dir` is the provider's coverage directory beside the final `index.html`.
The helper writes a page under that directory's `applications/` and returns its
report-relative URL. It does not launch a GUI or import UCAgent. Providers own
the command and database paths; use absolute artifact paths for nested reports.
The page uses UCAgent's existing authenticated generic Xpra API. It must be opened
through the workspace browser, with the viewer executable available in the host
environment. Reopening the page creates another session; no coverage-specific
session cache or configuration is maintained in UCAgent.

`RunUnityChipTest` only supplies `--report-custom=coverage-gui-xpra`. No resolver, workspace
metadata or entry-point registration is required, including source-tree `make`
launches. Provider-owned pages are responsible for session lifetime and errors.

### Optional custom launch protocol

Most providers need only links or application artifacts. A different host may
supply `--coverage-application-resolver=MODULE:CALLABLE`, metadata
`coverage_gui.resolver`, or a pytest plugin callback at
`config.toffee_coverage_application_resolver`. These optionally handle descriptors without `href`; provider URLs always take
precedence. Ordinary UCAgent integration does not use this extension.

The callback signature is
`resolve(detail: ApplicationDetail, context: CoverageReportContext) -> str | None`.
Return a relative/HTTP(S) URL, or `None` to omit an unavailable viewer. Exceptions
become warnings without erasing metrics or HTML links. Generating a report must
not launch a GUI. Direct callers of `to_report_dict()` can pass
`coverage_gui="xpra"` for ready-made URLs, or additionally pass
`application_detail_resolver=` and `context=` for artifact-based launches.

## Concrete return values and URLs

Assume the workspace is `/work/demo` and the final report is
`/work/demo/reports/index.html`. The following is an illustrative UVS result,
not a working UVS converter: only return it after producing and parsing the
real artifacts.
The built-in UVS provider uses compile/run `.cdb` directories and `uvd -ucdb`.
The generic descriptor example below does not generate a launch page.
`coverage_database` is a placeholder for a real coverage file or directory, not
a filename-extension requirement. USDB waveform files are not evidence of the
coverage database format.

```python
from toffee_test.utils.code_coverage.models import (
    ApplicationDetail, CodeCoverageResult, HtmlDetail, MetricSummary,
)

result = CodeCoverageResult(
    simulator="uvs",
    metrics={
        "line": MetricSummary(
            covered=80, total=100, uncovered=20, rate=80.0,
            available=True, details_available=True,
        ),
    },
    summary_file="/work/demo/reports/coverage_data/uvs-summary.json",
    databases=["/work/demo/build/coverage_database"],
    details=[
        HtmlDetail(
            path="coverage_data/native/uvs/index.html",
            label="HTML",
        ),
        ApplicationDetail(
            viewer="uvd",
            label="UVD",
            artifacts={"database": "/work/demo/build/coverage_database"},
            scope="Full native database",
        ),
    ],
)
# convert_session returns result.
# The inherited build_details returns result.details.
```

For HTML only, retain just the `HtmlDetail`. For application only, retain just
the `ApplicationDetail`; in the default `off` mode no application link is displayed.
For Verdi, use `viewer="verdi"`, `label="Verdi"`, and the actual VDB directory
as `artifacts["database"]`. VCS conversion still goes through URG.

### HTML link

The example HTML file must be at
`/work/demo/reports/coverage_data/native/uvs/index.html`. Its provider return
value is the URL relative to the final report:

```python
HtmlDetail(path="coverage_data/native/uvs/index.html", label="HTML")
```

When the report is served at
`https://ucagent.example/workspace/reports/index.html`, clicking HTML opens
`https://ucagent.example/workspace/reports/coverage_data/native/uvs/index.html`.
Do not use an absolute filesystem path as the browser link. URL-encode spaces
in URL components, for example `native/my%20report/index.html`.

### Application link and Xpra API (`--report-custom=coverage-gui-xpra`)

A provider can return an existing viewer URL, or generate a page with a request
like the following. This is the host's generic API, not a coverage-specific API:

```http
POST /api/xpra/sessions
Content-Type: application/json

{"command": "verdi -cov -covdir /work/demo/build/design.vdb", "mode": "gui", "name": "Verdi", "cwd": "."}
```

Build command strings with `shlex.join(argv)` to preserve paths with spaces and
avoid shell interpretation. The optional `write_xpra_link` helper does this for
providers. A successful response includes:

```json
{"id": "<session-id>", "viewer_path": "sessions/<session-id>/view"}
```

Resolve `viewer_path` against `/api/xpra/`, preserving any CMD/Master proxy prefix.
The helper derives that prefix from the report's `/workspace/` serving URL and
uses the existing browser authentication. It never places credentials in reports.
An anchor pointing directly to `/api/xpra/sessions` sends GET and cannot create a
session; link to your launch page or to an existing viewer page instead.

The built-in UVS provider supplies its parser and a `uvd -ucdb` launch page.
No UCAgent coverage-viewer registration is needed.
