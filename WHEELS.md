# Offline Wheel Installation (Corporate Network Workaround)

If `pip install` fails with `ConnectionResetError(10054)` when fetching from PyPI
(common in corporate networks with SSL inspection), use this PowerShell-based
workaround.

## The Problem

`pip install mcp` (or any package) fails with:

```
ConnectionResetError(10054): An existing connection was forcibly closed by the remote host
```

This affects `pip`'s HTTPS client specifically — direct `Invoke-WebRequest` to the
same PyPI URLs works fine. The issue is with pip's HTTP stack, not a firewall block.

## The Workaround

1. **Fetch package metadata** via PowerShell (which works):

```powershell
$pkg = "mcp"  # or any package name
$json = Invoke-RestMethod "https://pypi.org/pypi/$pkg/json" -TimeoutSec 30
$version = $json.info.version
```

2. **Find the correct wheel** for your platform:

```powershell
$releases = $json.releases.$version
foreach ($r in $releases) {
    if ($r.packagetype -eq "bdist_wheel" -and $r.filename -match "cp312-cp312-win_amd64") {
        $url = $r.url
        $file = $r.filename
        break
    }
}
```

3. **Download the wheel directly**:

```powershell
Invoke-WebRequest -Uri $url -OutFile "C:\wheels\$file" -UseBasicParsing
```

4. **Install offline**:

```bash
pip install --no-index --find-links C:\wheels C:\wheels\$file
```

## Full Dependency Tree for MCP SDK

The `mcp` package and its transitive dependencies for Python 3.12 on Windows x64:

```
mcp, httpx, httpx-sse, pydantic, pydantic-core, pydantic-settings,
annotated-types, typing-extensions, typing-inspection, starlette,
sse-starlette, uvicorn, anyio, sniffio, click, h11, httpcore,
certifi, idna, jsonschema, jsonschema-specifications, referencing,
rpds-py, colorama, python-dotenv, attrs, pyjwt, python-multipart,
pywin32, cryptography, cffi, pycparser, exceptiongroup, websockets
```

A comprehensive downloader script is available in `scripts/download_mcp_wheels.ps1`.

## Quick Install

An all-in-one PowerShell script that downloads and installs everything:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/download_mcp_wheels.ps1
```
