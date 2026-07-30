"""Small synthetic PDFs used by the history-import tests.

The repository intentionally ships no student documents.  These bytes are
generated at test time and contain only fictional data.
"""
from __future__ import annotations


def _pdf(lines: list[str], prefix: bytes = b"") -> bytes:
    """Build a minimal one-page PDF whose text is extractable by pdfplumber."""
    commands = ["BT", "/F1 10 Tf", "48 790 Td", "13 TL"]
    for line in lines:
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.extend((f"({safe}) Tj", "T*"))
    commands.append("ET")
    stream = "\n".join(commands).encode("ascii")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    result = bytearray(b"%PDF-1.4\n%synthetic\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode("ascii"))
        result.extend(obj)
        result.extend(b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    result.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    result.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return prefix + bytes(result)


def historico_pdf() -> bytes:
    return _pdf(
        [
            "Nome: Pessoa de Teste Matricula: 00000000",
            "Ano/semestre: 2024/1",
            "2024/1 Componente Curricular",
            "11100058 - Calculo 1 T1 8,0 APR",
            "2024/2 Componente Curricular",
            "11100059 - Calculo 2 T1 2,0 REPR",
            "22000297 - Algoritmos e Estruturas de Dados I T1 CURSANDO",
            "2025/1 Componente Curricular",
            "11100059 - Calculo 2 T1 8,0 APR",
        ],
        prefix=b"\n\n\n\n",
    )


def integralizacao_pdf() -> bytes:
    return _pdf(
        [
            "Nome Pessoa de Teste",
            "Matricula 00000000",
            "11100058 Calculo 1 DISCIPLINA 60 (2024/1) Aprovada",
            "11100110 Algebra Linear DISCIPLINA 90 11100005",
        ]
    )
