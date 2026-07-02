"""
Convert a DWG file to DXF using the locally installed ZWCAD via COM automation.
DXF is a plain-text/binary-documented format that ezdxf (pure Python) can read
and write directly, so this is the bridge between "real CAD file" and
"AI-editable file" without needing any paid/vendored wrapper library.

Usage:
    python dwg_to_dxf.py <input.dwg> <output.dxf>
"""
import sys
import time
import comtypes
import comtypes.client


def dwg_to_dxf(dwg_path: str, dxf_path: str, dxf_version: str = "ACAD2018") -> None:
    comtypes.CoInitialize()
    try:
        zwcad = comtypes.client.CreateObject("ZWCAD.Application")
        zwcad.Visible = False

        doc = zwcad.Documents.Open(dwg_path)
        # Give ZWCAD a moment to fully load the drawing before saving.
        time.sleep(1)

        # SaveAs with a DXF file extension triggers ZWCAD's DXF exporter.
        doc.SaveAs(dxf_path)

        doc.Close(False)
    finally:
        comtypes.CoUninitialize()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python dwg_to_dxf.py <input.dwg> <output.dxf>")
        sys.exit(1)
    dwg_to_dxf(sys.argv[1], sys.argv[2])
    print(f"Converted: {sys.argv[1]} -> {sys.argv[2]}")
