"""
Round-trip: convert a DXF file back to DWG using ZWCAD COM automation.
Mirror of dwg_to_dxf.py.  Uses the same ZWCAD Application COM interface.

Usage:
    python dxf_to_dwg.py <input.dxf> <output.dwg>
"""
import sys
import time
import comtypes
import comtypes.client


def dxf_to_dwg(dxf_path: str, dwg_path: str) -> None:
    """Open DXF in ZWCAD and save as DWG."""
    comtypes.CoInitialize()
    try:
        zwcad = comtypes.client.CreateObject("ZWCAD.Application")
        zwcad.Visible = False

        doc = zwcad.Documents.Open(dxf_path)
        time.sleep(1)

        # SaveAs with .dwg extension triggers DWG format write
        doc.SaveAs(dwg_path)

        doc.Close(False)
    finally:
        comtypes.CoUninitialize()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python dxf_to_dwg.py <input.dxf> <output.dwg>")
        sys.exit(1)
    dxf_to_dwg(sys.argv[1], sys.argv[2])
    print(f"Converted: {sys.argv[1]} -> {sys.argv[2]}")
