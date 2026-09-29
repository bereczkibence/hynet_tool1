"""Product labels at the reporting boundary; numerical results are untouched."""
from dataclasses import replace
from pathlib import Path
from zipfile import ZipFile
from io import BytesIO

def brand_text(text):
    return text.replace("# PF/OPF Benchmark Comparison", "# Tool1 (acdcopf) PF/OPF Report").replace("Native ACDCPF", "Tool5 (acdcpf)").replace("ACDCPF PF", "Tool5 (acdcpf) PF")

def present_bundle(bundle):
    def publish(path):
        if path is None:
            return None
        target = path
        if path.suffix == ".xlsx":
            buf = BytesIO()
            with ZipFile(path) as source, ZipFile(buf, "w") as output:
                for item in source.infolist():
                    content = source.read(item.filename)
                    if item.filename.endswith(".xml"):
                        content = brand_text(content.decode("utf-8")).encode("utf-8")
                    output.writestr(item, content)
            target.write_bytes(buf.getvalue())
        else:
            target.write_text(brand_text(path.read_text(encoding="utf-8-sig")), encoding="utf-8-sig" if path.suffix == ".csv" else "utf-8")
        if target != path:
            path.unlink()
        return target
    return replace(bundle, markdown=brand_text(bundle.markdown), markdown_path=publish(bundle.markdown_path),
        html_path=publish(bundle.html_path), csv_paths=tuple(map(publish,bundle.csv_paths)),
        xlsx_paths=tuple(map(publish,bundle.xlsx_paths)))
