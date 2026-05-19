import hashlib
import json
import math
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import pandas as pd

CATEGORY_RATES = {
    "A": Decimal("0.115"),
    "B": Decimal("0.173"),
    "C": Decimal("0.23"),
    "Z": Decimal("0.10"),
}

REQUIRED_FIELDS = {
    "ID Ciclo Dispensación": "id_ciclo_dispensacion",
    "EPS": "eps",
    "Número de Prescripción": "numero_prescripcion",
    "Fecha Máxima de Entrega": "fecha_maxima_entrega",
    "Tipo Doc. Paciente": "tipo_doc_paciente",
    "Identificación del Paciente": "identificacion_paciente",
    "Fecha Direccionamiento": "fecha_direccionamiento",
    "Código de Tecnología Direccionada": "codigo_tecnologia_direccionada",
    "Tecnología Direccionada": "tecnologia_direccionada",
    "Cantidad Total a Entregar": "cantidad_total_entregar",
    "Nombre": "nombre",
    "Municipio": "municipio",
    "Departamento": "departamento",
    "Domiciliario": "domiciliario",
    "Estado del Paciente": "estado_paciente",
    "Estado Final": "estado_final",
    "LABORATORIO": "laboratorio",
    "Valor Direccionado": "valor_direccionado",
}


def normalize_key(value: str) -> str:
    text = str(value).strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


EXPECTED_NORMALIZED = {normalize_key(k): v for k, v in REQUIRED_FIELDS.items()}

# Alias tolerantes por variaciones típicas del archivo.
ALIASES = {
    normalize_key("ID Ciclo Dispensacion"): "id_ciclo_dispensacion",
    normalize_key("ID Ciclo Dispensación"): "id_ciclo_dispensacion",
    normalize_key("Id Ciclo Dispensación"): "id_ciclo_dispensacion",
    normalize_key("Numero de Prescripcion"): "numero_prescripcion",
    normalize_key("Número de Prescripción"): "numero_prescripcion",
    normalize_key("Fecha Maxima Entrega"): "fecha_maxima_entrega",
    normalize_key("Fecha Máxima de Entrega"): "fecha_maxima_entrega",
    normalize_key("Tipo Doc Paciente"): "tipo_doc_paciente",
    normalize_key("Tipo Doc. Paciente"): "tipo_doc_paciente",
    normalize_key("Identificacion del Paciente"): "identificacion_paciente",
    normalize_key("Identificación del Paciente"): "identificacion_paciente",
    normalize_key("Fecha Direccionamiento"): "fecha_direccionamiento",
    normalize_key("Codigo de Tecnologia Direccionada"): "codigo_tecnologia_direccionada",
    normalize_key("Código de Tecnología Direccionada"): "codigo_tecnologia_direccionada",
    normalize_key("Tecnologia Direccionada"): "tecnologia_direccionada",
    normalize_key("Tecnología Direccionada"): "tecnologia_direccionada",
    normalize_key("Cantidad Total a Entregar"): "cantidad_total_entregar",
    normalize_key("Estado Paciente"): "estado_paciente",
    normalize_key("Estado del Paciente"): "estado_paciente",
    normalize_key("Estado Final"): "estado_final",
    normalize_key("Laboratorio"): "laboratorio",
    normalize_key("LABORATORIO"): "laboratorio",
    normalize_key("Valor Direccionado"): "valor_direccionado",
    **EXPECTED_NORMALIZED,
}


def map_dataframe_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    rename_map: dict[str, str] = {}
    found: set[str] = set()

    for col in df.columns:
        canonical = ALIASES.get(normalize_key(str(col)))
        if canonical:
            rename_map[col] = canonical
            found.add(canonical)

    required_canonical = set(REQUIRED_FIELDS.values())
    missing_canonical = sorted(required_canonical - found)
    missing_display = [k for k, v in REQUIRED_FIELDS.items() if v in missing_canonical]

    df = df.rename(columns=rename_map)
    return df, missing_display


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if pd.isna(value):
        return True
    return str(value).strip() == ""


def clean_text(value: Any, *, upper: bool = False) -> str | None:
    if is_blank(value):
        return None
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text.upper() if upper else text


def clean_document(value: Any) -> str | None:
    if is_blank(value):
        return None
    text = str(value).strip()
    # Conserva letras si algún documento extranjero las trae, pero elimina ruido común.
    text = re.sub(r"[^A-Za-z0-9]", "", text)
    return text.upper() or None


def parse_decimal(value: Any) -> Decimal | None:
    if is_blank(value):
        return None
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    text = str(value).strip()
    text = text.replace("$", "").replace("COP", "").replace(" ", "")
    text = re.sub(r"[^0-9,\.\-]", "", text)

    if not text or text in {"-", ",", "."}:
        return None

    if "," in text and "." in text:
        # Si la coma aparece después del punto, asume formato colombiano: 1.234.567,89
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        # Si hay una sola coma y tres dígitos al final, suele ser separador de miles.
        parts = text.split(",")
        if len(parts[-1]) == 3 and len(parts) == 2:
            text = text.replace(",", "")
        else:
            text = text.replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")

    try:
        return Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def parse_date(value: Any) -> date | None:
    if is_blank(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    return parsed.date()


def calculate_copago(valor_direccionado: Decimal | None, categoria: str | None) -> Decimal | None:
    if valor_direccionado is None or not categoria:
        return None
    rate = CATEGORY_RATES.get(categoria.upper())
    if rate is None:
        return None
    return (valor_direccionado * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def stable_json(data: dict[str, Any]) -> str:
    def default(obj):
        if isinstance(obj, (date, Decimal)):
            return str(obj)
        if pd.isna(obj):
            return None
        return str(obj)

    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=default)


def row_hash(data: dict[str, Any]) -> str:
    return hashlib.sha256(stable_json(data).encode("utf-8")).hexdigest()


def file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
