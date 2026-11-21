"""Taking a prospect list out of the app: CSV and plain phone numbers."""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence

from app.hunter.domain import Prospect

# Excel in pt-BR reads a semicolon file as columns and needs the BOM to
# recognise UTF-8. Without both, accents break and everything lands in
# column A.
CSV_DELIMITER = ";"
CSV_BOM = "﻿"

CSV_HEADERS: tuple[str, ...] = (
    "Nome",
    "Nota",
    "Por que",
    "Categoria",
    "Avaliações",
    "Nota média",
    "Telefone",
    "WhatsApp",
    "Site",
    "Tipo de site",
    "Pixel da Meta",
    "E-mails",
    "Bairro",
    "Cidade",
    "Endereço",
    "Situação",
    "Link do Maps",
)


def _pixel_label(prospect: Prospect) -> str:
    if prospect.website_type.value != "site":
        return ""
    if prospect.site_unreachable:
        return "site fora do ar"
    if prospect.has_meta_pixel is None:
        return "não verificado"
    return "sim" if prospect.has_meta_pixel else "não"


def _decimal_br(value: object) -> str:
    return str(value).replace(".", ",") if value is not None else ""


def prospects_to_csv(prospects: Sequence[Prospect]) -> str:
    """A spreadsheet of the current list, ready to open in Excel."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=CSV_DELIMITER, lineterminator="\r\n")
    writer.writerow(CSV_HEADERS)

    for prospect in prospects:
        writer.writerow(
            [
                prospect.name,
                prospect.score,
                prospect.score_explanation,
                prospect.category or "",
                prospect.review_count,
                _decimal_br(prospect.review_rating),
                prospect.phone or "",
                prospect.whatsapp or "",
                prospect.website or "",
                prospect.website_label,
                _pixel_label(prospect),
                ", ".join(prospect.emails),
                prospect.borough or "",
                prospect.city or "",
                prospect.address or "",
                prospect.status_label,
                prospect.maps_link,
            ]
        )

    return CSV_BOM + buffer.getvalue()


def prospects_to_phone_list(prospects: Sequence[Prospect]) -> list[str]:
    """Numbers to paste into a dialer or a broadcast list, no duplicates."""
    numbers: list[str] = []
    for prospect in prospects:
        number = prospect.whatsapp or prospect.phone
        if number and number not in numbers:
            numbers.append(number)
    return numbers
